"""Metadata-only GitHub import tests: pagination, privacy, idempotence, collisions."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "ubuntu24" / "github-cms-import.py"
spec = importlib.util.spec_from_file_location("sirus_github_cms_import", SCRIPT)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

def record(i, name, private=True):
    return {
        "id": i, "full_name": name, "private": private, "description": "internal secret",
        "default_branch": "main", "archived": False, "fork": False, "pushed_at": "2026-11-08T00:00:00Z"
    }

def database():
    db=sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("""CREATE TABLE cms_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,summary TEXT NOT NULL,body TEXT NOT NULL,
        category TEXT NOT NULL,icon TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('page','app')),
        state TEXT NOT NULL CHECK(state IN ('draft','published')),
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE cms_hosting_preferences (
        slug TEXT PRIMARY KEY REFERENCES cms_projects(slug) ON DELETE CASCADE,
        assigned_node TEXT NOT NULL DEFAULT 'vps-primary',
        database_backend TEXT NOT NULL DEFAULT 'sqlite',
        source_repository TEXT NOT NULL DEFAULT '',
        source_ref TEXT NOT NULL DEFAULT '',
        release_policy TEXT NOT NULL DEFAULT 'hold',
        updated_at TEXT NOT NULL
    )""")
    db.commit()
    return db

class ImportTests(unittest.TestCase):
    def test_all_pages_and_private_discovery(self):
        pages={1: [record(i, f"owner/repository-{i}") for i in range(1,101)],
               2: [record(101,"organization/Private-Tool")]}
        seen=[]
        def reader(token,page):
            self.assertEqual(token, "token")
            seen.append(page)
            return pages.get(page, [])
        rows=worker.discover("token",reader)
        self.assertEqual(len(rows),101)
        self.assertEqual(seen,[1,2])
        self.assertTrue(any(r["full_name"]=="organization/Private-Tool" and r["github_private"] for r in rows))

    def test_api_failure_cannot_partially_import(self):
        def reader(_,page):
            if page==1:
                return [record(i,f"owner/p-{i}") for i in range(1,101)]
            raise RuntimeError("HTTP 403")
        with self.assertRaisesRegex(RuntimeError,"HTTP 403"):
            worker.discover("token",reader)

    def test_import_creates_private_drafts_and_preserves_manual_edits(self):
        db=database()
        rows=[worker.normalize(record(11,"owner/Repo-One")),
              worker.normalize(record(12,"owner/Repo-Two",False))]
        count=worker.apply(db,rows,1794168000)
        self.assertEqual(count,2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM cms_projects").fetchone()[0],2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM cms_github_repositories").fetchone()[0],2)
        row=db.execute("SELECT slug,state,body,summary FROM cms_projects WHERE slug='repo-one'").fetchone()
        self.assertEqual(row[1],"draft")
        self.assertEqual(row[2],"")
        self.assertNotIn("internal secret",row[3])
        db.execute("UPDATE cms_projects SET title='Curated by operator',state='published' WHERE slug='repo-one'")
        db.commit()
        renamed=[worker.normalize(record(11,"owner/New-Repo-One")),
                 worker.normalize(record(12,"owner/Repo-Two",False))]
        self.assertEqual(worker.apply(db,renamed,1794168100),0)
        row=db.execute("SELECT title,state FROM cms_projects WHERE slug='repo-one'").fetchone()
        self.assertEqual(row,("Curated by operator","published"))
        self.assertEqual(db.execute("SELECT full_name FROM cms_github_repositories WHERE github_id=11").fetchone()[0],
                         "owner/New-Repo-One")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM cms_projects").fetchone()[0],2)

    def test_collision_and_protected_project_names(self):
        db=database()
        rows=[worker.normalize(record(1,"owner/cpoint")),
              worker.normalize(record(2,"other/cpoint")),
              worker.normalize(record(3,"third/7")),
              worker.normalize(record(4,"owner/Repo-One")),
              worker.normalize(record(5,"other/Repo-One"))]
        self.assertEqual(worker.apply(db,rows,1794168000),5)
        slugs=[s[0] for s in db.execute("SELECT slug FROM cms_projects")]
        self.assertEqual(len(slugs),len(set(slugs)))
        self.assertNotIn("cpoint",slugs)
        self.assertTrue(all(worker.SLUG.fullmatch(s) for s in slugs))
        self.assertTrue(all(s[0]=="draft" for s in db.execute("SELECT state FROM cms_projects")))

    def test_deleted_project_is_not_resurrected(self):
        db=database()
        rows=[worker.normalize(record(7,"owner/ProjectX"))]
        self.assertEqual(worker.apply(db,rows,1794168000),1)
        db.execute("DELETE FROM cms_projects")
        db.commit()
        self.assertEqual(worker.apply(db,rows,1794168300),0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM cms_projects").fetchone()[0],0)
        self.assertIsNone(db.execute("SELECT project_id FROM cms_github_repositories").fetchone()[0])

    def test_bad_repo_response_rejected(self):
        for item in [record(1,"a/../b"),dict(record(1,"owner/okay"),private=None),
                     dict(record(1,"owner/okay"),id=-1)]:
            with self.assertRaises(ValueError):
                worker.normalize(item)

if __name__=="__main__":
    unittest.main()
