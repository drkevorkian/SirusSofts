"""Synthetic opt-in discovery of exact source-cited procedural disagreements."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
from manual_relationship_index import sync
from procedure_discovery import (register_seed,scan_once,suggestions,accept,
                                 configure_seed,SCAN_INTERVAL,SWEEP_INTERVAL)
from relationship_scheduler import discover_if_idle
from procedure_review_queue import tick

A,B,C,D="a"*64,"b"*64,"c"*64,"d"*64
HEAD="Removal of primary clutch"
BASE="""Removal of primary clutch
1. Disconnect battery cable.
2. Remove primary clutch cover.
Before removing primary clutch cover, disconnect battery cable.
Installation of primary clutch
1. Reinstall primary clutch cover.
"""
REVERSE="""Removal of primary clutch
1. Remove primary clutch cover.
2. Disconnect battery cable.
After removing primary clutch cover, disconnect battery cable.
Installation of primary clutch
1. Reinstall primary clutch cover.
"""


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.src=self.root/"manual.sqlite3"
        self.idx=self.root/"index.sqlite3"
        self.research=self.root/"research.sqlite3"
        self.queue=self.root/"private.sqlite3"
        with sqlite3.connect(self.src) as db:
            db.executescript("""
            CREATE TABLE documents(sha256 TEXT PRIMARY KEY,filename TEXT,
                status TEXT,reviewed INTEGER,updated_at INTEGER,excerpts INTEGER);
            CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)", [
                (A,"2026 Defender A","approved",1,10,1),
                (B,"2026 Defender B","approved",1,10,1),
                (C,"2024 Commander C","approved",1,10,1),
                (D,"2026 Defender D","approved",1,10,1),
            ])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)",[
                (A,2,1,BASE),(B,3,1,REVERSE),
                (C,4,1,REVERSE),(D,5,1,REVERSE)])
        sync(source=self.src,index=self.idx)
        with sqlite3.connect(self.research) as db:
            db.executescript("""
            CREATE TABLE cr_sources(id TEXT PRIMARY KEY,kind TEXT,
                present INTEGER,eligible INTEGER,reviewed INTEGER);
            CREATE TABLE cr_evidence_scopes(source_id TEXT PRIMARY KEY,
                applicability TEXT);
            CREATE TABLE cr_scope_versions(source_id TEXT,version INTEGER,
                new_scope TEXT, reviewed_by TEXT);
            """)
            for sha,scope in ((A,"brp/defender/2026/hd10/clutch/rev-a"),
                              (B,"brp/defender/2026/hd10/clutch/rev-a"),
                              (C,"brp/commander/2024/hd10/clutch/rev-a"),
                              (D,"brp/defender/2026")):
                sid="pdf:"+sha
                db.execute("INSERT INTO cr_sources VALUES(?,'pdf',1,1,1)",(sid,))
                db.execute("INSERT INTO cr_evidence_scopes VALUES(?,?)",(sid,scope))
                db.execute("INSERT INTO cr_scope_versions VALUES(?,1,?,'cms-admin:1')",
                           (sid,scope))

    def args(self):
        return {"queue":self.queue,"source":self.src,"index":self.idx,
                "research_db":self.research}

    def seed(self, **kw):
        return register_seed(**self.args(),source_id="pdf:"+A,
                             heading=HEAD,now=1,**kw)

    def test_seed_discovers_only_matching_complete_admin_scopes(self):
        self.assertFalse(self.queue.exists())
        self.assertEqual(scan_once(**self.args())["reason"],"no_opt_in_discovery")
        seed=self.seed()
        self.assertTrue(seed["new"])
        self.assertFalse(self.seed()["new"])
        self.assertEqual(self.queue.stat().st_mode & 0o077,0)
        found=scan_once(**self.args(),now=10)
        self.assertEqual(found["peers_scanned"],3)
        self.assertEqual(found["suggestions_observed"],1)
        self.assertEqual(found["review_targets_registered"],0)
        with sqlite3.connect(self.queue) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM procedure_review_targets")
                             .fetchone()[0],0)
        visible=suggestions(**self.args())["suggestions"]
        self.assertEqual(len(visible),1)
        self.assertEqual(visible[0]["status"],"unreviewed_suggestion")
        self.assertEqual(visible[0]["source_ids"],["pdf:"+A,"pdf:"+B])
        self.assertNotIn("pdf:"+C,str(visible))
        self.assertNotIn("pdf:"+D,str(visible))

        enrolled=accept(**self.args(),suggestion_id=visible[0]["id"])
        self.assertEqual(enrolled["status"],"accepted_for_unverified_review")
        self.assertFalse(enrolled["automatically_verified"])
        with sqlite3.connect(self.queue) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM procedure_review_targets")
                             .fetchone()[0],1)
        result=tick(queue=self.queue,source=self.src,index=self.idx,now=20)
        self.assertEqual(result["status"],"monitoring")
        self.assertEqual(result["current_candidate_count"],2)

    def test_cross_revision_discovery_and_review_preserve_scope_boundaries(self):
        with sqlite3.connect(self.research) as db:
            scope="brp/defender/2026/hd10/clutch/rev-b"
            db.execute("UPDATE cr_evidence_scopes SET applicability=? WHERE source_id=?",
                       (scope,"pdf:"+B))
            db.execute("INSERT INTO cr_scope_versions VALUES(?,2,?,'cms-admin:1')",
                       ("pdf:"+B,scope))
        self.seed()
        self.assertEqual(scan_once(**self.args(),now=10)["suggestions_observed"],1)
        visible=suggestions(**self.args())["suggestions"]
        self.assertEqual(visible[0]["source_ids"],["pdf:"+A,"pdf:"+B])
        self.assertEqual(visible[0]["status"],"unreviewed_suggestion")
        self.assertFalse(accept(**self.args(),suggestion_id=visible[0]["id"])["automatically_verified"])
        result=tick(queue=self.queue,source=self.src,index=self.idx,now=20)
        self.assertEqual(result["current_candidate_count"],1)
        self.assertFalse(result["verified_conflict"])

    def test_revocation_blocks_stale_suggestion_and_manual_acceptance(self):
        self.seed()
        scan_once(**self.args(),now=10)
        sid=suggestions(**self.args())["suggestions"][0]["id"]
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?",(B,))
        view=suggestions(**self.args())["suggestions"][0]
        self.assertEqual(view["status"],"stale_or_unavailable")
        self.assertEqual(view["source_ids"],[])
        with self.assertRaises(ValueError):
            accept(**self.args(),suggestion_id=sid)
        with sqlite3.connect(self.queue) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM procedure_review_targets")
                             .fetchone()[0],0)

    def test_admin_scope_revision_invalidates_existing_suggestion(self):
        self.seed()
        scan_once(**self.args(),now=10)
        sid=suggestions(**self.args())["suggestions"][0]["id"]
        with sqlite3.connect(self.research) as db:
            db.execute("UPDATE cr_evidence_scopes SET applicability=? WHERE source_id=?",
                       ("brp/defender/2025/hd10/clutch/rev-a","pdf:"+B))
            db.execute("INSERT INTO cr_scope_versions VALUES(?,2,?,'cms-admin:1')",
                       ("pdf:"+B,"brp/defender/2025/hd10/clutch/rev-a"))
        self.assertEqual(suggestions(**self.args())["suggestions"][0]["status"],
                         "stale_or_unavailable")
        with self.assertRaises(ValueError):
            accept(**self.args(),suggestion_id=sid)

    def test_original_pdf_edit_invalidate_acceptance_even_without_revision_bump(self):
        self.seed()
        scan_once(**self.args(),now=10)
        sid=suggestions(**self.args())["suggestions"][0]["id"]
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE excerpts SET text=replace(text,"
                "'Remove primary clutch cover.', 'Inspect primary clutch cover.')"
                " WHERE sha256=?",(B,))
        with self.assertRaises(ValueError):
            accept(**self.args(),suggestion_id=sid)
        self.assertEqual(suggestions(**self.args())["suggestions"][0]["status"],
                         "stale_or_unavailable")

    def test_short_or_untrusted_scope_never_enters_discovery(self):
        with sqlite3.connect(self.research) as db:
            db.execute("UPDATE cr_evidence_scopes SET applicability=? WHERE source_id=?",
                       ("brp/defender/2026","pdf:"+A))
            db.execute("INSERT INTO cr_scope_versions VALUES(?,2,?,'cms-admin:1')",
                       ("pdf:"+A,"brp/defender/2026"))
        with self.assertRaises(ValueError):
            self.seed()
        with sqlite3.connect(self.research) as db:
            db.execute("UPDATE cr_evidence_scopes SET applicability=? WHERE source_id=?",
                       ("brp/defender/2026/hd10/clutch/rev-a","pdf:"+A))
            db.execute("INSERT INTO cr_scope_versions VALUES(?,3,?,'model:inferred')",
                       ("pdf:"+A,"brp/defender/2026/hd10/clutch/rev-a"))
        with self.assertRaises(ValueError):
            self.seed()

    def test_duplicate_heading_unindexed_and_symlink_refused(self):
        with sqlite3.connect(self.src) as db:
            db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",(A,8,1,BASE))
            db.execute("UPDATE documents SET excerpts=2,updated_at=11 WHERE sha256=?",(A,))
        sync(source=self.src,index=self.idx)
        with self.assertRaises(ValueError):
            self.seed()
        self.idx.unlink()
        self.idx.symlink_to(self.src)
        with self.assertRaises(ValueError):
            self.seed()

    def test_keyset_pagination_and_pause_resume_controls(self):
        with sqlite3.connect(self.src) as db, sqlite3.connect(self.research) as metadata:
            for ch in "01234":
                sha="f"+ch*63
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                           (sha,sha,"approved",1,10,1))
                db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",(sha,1,1,REVERSE))
                sid="pdf:"+sha
                metadata.execute("INSERT INTO cr_sources VALUES(?,'pdf',1,1,1)",(sid,))
                key="brp/defender/2026/hd10/clutch/rev-a"
                metadata.execute("INSERT INTO cr_evidence_scopes VALUES(?,?)",(sid,key))
                metadata.execute("INSERT INTO cr_scope_versions VALUES(?,1,?,'cms-admin:1')",
                                 (sid,key))
        sync(source=self.src,index=self.idx)
        seed=self.seed()["seed_id"]
        first=scan_once(**self.args(),now=10)
        self.assertEqual(first["peers_scanned"],4)
        self.assertFalse(first["complete_sweep"])
        self.assertTrue(scan_once(**self.args(),now=11)["skipped"])
        self.assertTrue(configure_seed(queue=self.queue,seed_id=seed,enabled=False)["found"])
        self.assertTrue(scan_once(**self.args(),now=40)["skipped"])
        configure_seed(queue=self.queue,seed_id=seed,enabled=True)
        second=scan_once(**self.args(),now=41)
        self.assertEqual(second["peers_scanned"],4)
        self.assertTrue(second["complete_sweep"])
        self.assertGreaterEqual(len(suggestions(**self.args(),limit=20)["suggestions"]),6)
        self.assertTrue(scan_once(**self.args(),now=42)["skipped"])
        self.assertFalse(configure_seed(queue=self.queue,seed_id="f"*64,
                                        enabled=False)["found"])

    def test_idle_admission_gates_without_side_effects(self):
        runner=Mock(return_value={"suggestions_observed":1})
        for enabled,interactive in ((False,False),(False,True),(True,True)):
            out=discover_if_idle(enabled=enabled,interactive=interactive,
                run=runner,**self.args())
            self.assertTrue(out["skipped"])
        runner.assert_not_called()
        out=discover_if_idle(enabled=True,interactive=False,
            run=runner,**self.args())
        self.assertEqual(out["suggestions_observed"],1)
        runner.assert_called_once_with(**self.args())

    def test_empty_or_broken_source_does_not_mark_complete_review(self):
        self.seed()
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE documents SET updated_at=50 WHERE sha256=?",(A,))
        result=scan_once(**self.args(),now=10)
        self.assertEqual(result["suggestions_observed"],0)
        self.assertNotEqual(result["reason"],"")
        with sqlite3.connect(self.queue) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM procedure_review_targets")
                             .fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
