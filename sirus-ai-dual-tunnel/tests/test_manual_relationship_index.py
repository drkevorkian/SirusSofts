"""Persistent original-source relationship index and freshness tests."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import manual_relationship_index as rel
A="a"*64;B="b"*64;C="c"*64

class RelationshipIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/"pdf.db"
        self.index=self.root/"rel.db"
        with sqlite3.connect(self.source) as db:
            db.executescript("""CREATE TABLE documents(
                 sha256 TEXT PRIMARY KEY,filename TEXT,status TEXT,
                 reviewed INTEGER,updated_at INTEGER,excerpts INTEGER);
                 CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)",[
                (A,"2026 Defender Shop Manual","approved",1,10,1),
                (B,"2026 Commander Service Manual","approved",1,10,1),
                (C,"2026 Polaris Manual","ready",0,10,1)])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)",[
                (A,5,1,"Part Number: 420234561\nRemoval of primary clutch\nCooling System"),
                (B,9,2,"Part No. 420234561\nConsult the wiring diagram\nElectrical System"),
                (C,1,1,"Part No. 420234561")])
    def sync(self,**kw):
        return rel.sync(source=self.source,index=self.index,**kw)
    def search(self,**kw):
        return rel.search(source=self.source,index=self.index,kind="part",
                          term="420234561",source_ids=["pdf:"+A,"pdf:"+B],**kw)
    def test_incremental_reload_search_and_revoke(self):
        first=self.sync()
        self.assertEqual(first["refreshed"],2)
        self.assertEqual(first["cached_documents"],2)
        found=self.search()
        self.assertEqual([r["page"] for r in found["matches"]],[5,9])
        self.assertEqual(found["status"],"source_checked")
        self.assertFalse(found["has_more"])
        self.assertEqual(self.sync()["refreshed"],0)
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET status='ready' WHERE sha256=?",(B,))
        changed=self.sync()
        self.assertEqual(changed["revoked"],1)
        self.assertEqual(self.search()["status"],"stale_or_unindexed")
        with sqlite3.connect(self.index) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rel_mentions WHERE sha=?",
                                        (B,)).fetchone()[0],0)
    def test_updated_revision_replaces_old_quotes(self):
        self.sync()
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text='Part No. 420234562' WHERE sha256=?",(B,))
            db.execute("UPDATE documents SET updated_at=11 WHERE sha256=?",(B,))
        self.assertEqual(self.search()["status"],"stale_or_unindexed")
        report=self.sync()
        self.assertEqual(report["refreshed"],1)
        self.assertEqual(len(self.search()["matches"]),1)
    def test_unannounced_excerpt_change_fails_original_quote_check(self):
        self.sync()
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text='Part No. 420234562' WHERE sha256=?",(B,))
        self.assertEqual(self.search()["status"],"stale_original_excerpts")
        self.sync(force=True)
        self.assertEqual(len(self.search()["matches"]),1)
    def test_explicit_scope_and_paging(self):
        self.sync()
        self.assertEqual(len(self.search(limit=1)["matches"]),1)
        self.assertTrue(self.search(limit=1)["has_more"])
        self.assertEqual(self.search(limit=1,offset=1)["matches"][0]["page"],9)
        for ids in ([],["pdf:"+A]*2,["pdf:../../danger"]):
            with self.assertRaises(ValueError):
                rel.search(source=self.source,index=self.index,kind="part",
                           term="420234561",source_ids=ids)
        with self.assertRaises(ValueError):
            rel.search(source=self.source,index=self.index,kind="unknown",
                       term="420234561",source_ids=["pdf:"+A])
    def test_batches_and_source_cap_are_visible(self):
        out=self.sync(batch=1)
        self.assertEqual(out["refreshed"],1)
        self.assertEqual(out["remaining"],1)
        self.assertFalse(out["complete"])
        out=self.sync(batch=1)
        self.assertEqual(out["refreshed"],1)
        self.assertTrue(out["complete"])
        old=rel.MAX_EXCERPTS_PER_DOC
        try:
            rel.MAX_EXCERPTS_PER_DOC=1
            with sqlite3.connect(self.source) as db:
                db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                           (A,6,1,"Part Number: 420234561"))
                db.execute("UPDATE documents SET excerpts=2,updated_at=12 WHERE sha256=?",(A,))
            out=self.sync()
            self.assertEqual(out["partial_refreshed"],1)
            self.assertFalse(out["complete"])
            found=self.search()
            self.assertFalse(found["complete"])
        finally:
            rel.MAX_EXCERPTS_PER_DOC=old
    def test_systems_and_procedure_index_are_source_cited(self):
        self.sync()
        for kind,term,page in (
            ("system_heading","cooling system",5),
            ("system_heading","electrical system",9),
            ("procedure_heading","removal of primary clutch",5)):
            out=rel.search(source=self.source,index=self.index,kind=kind,
                term=term,source_ids=["pdf:"+A,"pdf:"+B])
            self.assertEqual(out["status"],"source_checked")
            self.assertEqual(out["matches"][0]["page"],page)
            self.assertEqual(len(out["matches"][0]["quote_sha256"]),64)
        self.assertEqual(rel.search(source=self.source,index=self.index,
            kind="system_heading",term="cooling system",
            source_ids=["pdf:"+B])["matches"],[])

    def test_symlink_index_denied(self):
        target=self.root/"target.db"
        target.touch()
        linked=self.root/"linked.db"
        linked.symlink_to(target)
        with self.assertRaises(rel.IndexErrorSafe):
            rel.sync(source=self.source,index=linked)

if __name__=="__main__":
    unittest.main()
