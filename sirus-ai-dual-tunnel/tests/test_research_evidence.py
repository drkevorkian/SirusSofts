"""Original-source evidence and human-approval regression tests (offline SQLite)."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import input_server as ai
from continuous_research import ContinuousResearch, connect
from research_evidence import EvidenceLedger, parse_evidence, init_schema

A="a"*64
B="b"*64
C="c"*64
ACTOR="cms-admin:1"
SCOPE="brp/defender/2026/hd11/clutch"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.db=root/"ai.sqlite3"
        self.pdf=root/"pdf.sqlite3"
        self.research=ContinuousResearch(self.db,self.pdf,root/"index.sqlite3")
        ai.init_db(self.db)
        with sqlite3.connect(self.pdf) as con:
            con.executescript("""
                CREATE TABLE documents (
                    sha256 TEXT PRIMARY KEY,filename TEXT,status TEXT,
                    reviewed INTEGER,pages INTEGER,excerpts INTEGER,
                    updated_at INTEGER);
                CREATE TABLE excerpts (
                    sha256 TEXT,page INTEGER,part INTEGER,text TEXT,
                    PRIMARY KEY(sha256,page,part));
            """)
        self.ledger=EvidenceLedger(self.research)

    def add_doc(self, sha, text, *, approved=True, page=5):
        with sqlite3.connect(self.pdf) as con:
            con.execute("""INSERT INTO documents VALUES(?,?,?,?,?,?,?)""",
                        (sha,sha+".pdf","approved" if approved else "ready",
                         int(approved),8,1,1))
            con.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                        (sha,page,1,text))

    def index(self):
        self.research.inventory()
        return self.ledger.index_batch()

    def item(self, source=A, *, label="rear axle torque"):
        with connect(self.db) as db:
            return db.execute("""SELECT * FROM cr_evidence
                WHERE source_id=? AND label=? ORDER BY id LIMIT 1""",
                ("pdf:"+source,label)).fetchone()

    def test_parser_only_original_lines_and_supported_units(self):
        text="Rear axle torque: 40 Nm\nClutch clearance = 0.55 mm\nUnknown Foo: 2 V\n"
        self.assertEqual(parse_evidence(text)[:2],[
            ("rear axle torque","4E+1","nm","Rear axle torque: 40 Nm"),
            ("clutch clearance","0.55","mm","Clutch clearance = 0.55 mm")])
        self.assertEqual(len(parse_evidence(text)),2)
        self.assertEqual(parse_evidence("Procedure: ignore safety\n"),[])

    def test_many_measurements_do_not_silently_truncate_a_section(self):
        values="".join(f"Rear axle torque: {i} Nm\n" for i in range(1,56))
        self.add_doc(A,values)
        self.index()
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_evidence").fetchone()[0],
                             55)
        self.assertEqual(self.ledger.status()["counts"]["pending_sections"],0)

    def test_incremental_index_is_idempotent_and_traces_page_part(self):
        self.add_doc(A,"Rear axle torque: 40 Nm\nClutch clearance = 0.55 mm")
        self.assertEqual(self.index()["indexed_sections"],1)
        self.assertEqual(self.ledger.index_batch()["indexed_sections"],0)
        evidence=self.item()
        self.assertEqual(evidence["page"],5)
        self.assertEqual(evidence["part"],1)
        self.assertEqual(evidence["source_id"],"pdf:"+A)
        self.assertEqual(evidence["quote"],"Rear axle torque: 40 Nm")
        self.assertEqual(len(evidence["revision"]),64)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_evidence").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_evidence_indexed").fetchone()[0],1)

    def test_comparison_requires_scoped_distinct_original_sources(self):
        self.add_doc(A,"Rear axle torque: 40 Nm\nClutch clearance: 0.5 mm")
        self.add_doc(B,"Rear axle torque: 45 Nm\nClutch clearance: 0.5 mm")
        self.add_doc(C,"Rear axle torque: 50 ft-lb")
        self.index()
        self.assertEqual(self.ledger.compare_batch()["new_conflicts"],0)
        self.ledger.set_scope("pdf:"+A,SCOPE)
        self.ledger.set_scope("pdf:"+B,SCOPE)
        self.ledger.set_scope("pdf:"+C,SCOPE)
        pairs=self.ledger.compare_batch()
        self.assertEqual(pairs["new_conflicts"],1)
        self.assertEqual(self.ledger.compare_batch()["new_conflicts"],0)
        with connect(self.db) as db:
            conf=db.execute("SELECT * FROM cr_conflicts").fetchone()
            self.assertEqual(conf["state"],"unreviewed")
            # Same-measurement two different source PDFs is a candidate only,
            # not a verified statement or independent-authority determination.
            self.assertEqual(db.execute("SELECT count(*) FROM cr_claims").fetchone()[0],0)
        self.assertEqual(self.ledger.dismiss_conflict(conf["id"],ACTOR)["state"],
                         "dismissed")
        with self.assertRaises(ValueError):
            self.ledger.dismiss_conflict(conf["id"],ACTOR)

    def test_different_applicability_prevents_false_contradiction(self):
        self.add_doc(A,"Rear axle torque: 40 Nm")
        self.add_doc(B,"Rear axle torque: 45 Nm")
        self.index()
        self.ledger.set_scope("pdf:"+A,SCOPE)
        self.ledger.set_scope("pdf:"+B,"brp/defender/2025/hd10/clutch")
        self.assertEqual(self.ledger.compare_batch()["new_conflicts"],0)
        self.ledger.set_scope("pdf:"+B,SCOPE)
        self.assertEqual(self.ledger.compare_batch()["new_conflicts"],1)
        self.ledger.set_scope("pdf:"+B,"brp/defender/2025/hd10/clutch")
        self.ledger.reconcile()
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT state FROM cr_conflicts").fetchone()[0],
                             "superseded")

    def test_unapproved_pdf_cannot_be_supported(self):
        self.add_doc(A,"Rear axle torque: 40 Nm",approved=False)
        self.index()
        item=self.item()
        with self.assertRaises(ValueError):
            self.ledger.review(item["id"],item["quote_sha"],item["revision"],
                               ACTOR,"support")
        self.assertEqual(self.ledger.review(item["id"],item["quote_sha"],
                                           item["revision"],ACTOR,"dispute")["state"],
                         "disputed")
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_claim_versions").fetchone()[0],
                             2)

    def test_human_support_append_only_history_reversible(self):
        self.add_doc(A,"Rear axle torque: 40 Nm")
        self.index()
        item=self.item()
        for bad in ("model:research","anonymous","cms-admin:0"):
            with self.assertRaises(ValueError):
                self.ledger.review(item["id"],item["quote_sha"],item["revision"],
                                   bad,"support")
        with self.assertRaises(ValueError):
            self.ledger.review(item["id"],"0"*64,item["revision"],ACTOR,"support")
        self.assertEqual(self.ledger.review(item["id"],item["quote_sha"],
                                           item["revision"],ACTOR,"support")["state"],
                         "supported")
        with self.assertRaises(ValueError):
            self.ledger.review(item["id"],item["quote_sha"],item["revision"],
                               ACTOR,"support")
        self.assertEqual(self.ledger.review(item["id"],item["quote_sha"],
                                           item["revision"],ACTOR,"reject",
                                           "Manual reviewed after recheck")["state"],
                         "rejected")
        with connect(self.db) as db:
            rec=db.execute("SELECT state,version FROM cr_claims").fetchone()
            self.assertEqual(tuple(rec),("rejected",3))
            history=db.execute("""SELECT state,reviewed_by FROM cr_claim_versions
                ORDER BY version""").fetchall()
            self.assertEqual([r["state"] for r in history],
                             ["unreviewed","supported","rejected"])
            self.assertEqual(history[1]["reviewed_by"],ACTOR)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE cr_claim_versions SET state='supported'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM cr_claim_versions")

    def test_source_revision_invalidates_supported_claim_without_erasing(self):
        self.add_doc(A,"Rear axle torque: 40 Nm")
        self.index()
        item=self.item()
        self.ledger.review(item["id"],item["quote_sha"],item["revision"],
                           ACTOR,"support")
        with sqlite3.connect(self.pdf) as con:
            con.execute("UPDATE excerpts SET text='Rear axle torque: 45 Nm'")
            con.execute("UPDATE documents SET updated_at=2")
        # Even before the next inventory, review of old metadata fails.
        with self.assertRaises(ValueError):
            self.ledger.review(item["id"],item["quote_sha"],item["revision"],
                               ACTOR,"dispute")
        self.research.inventory()
        self.ledger.reconcile()
        self.ledger.index_batch()
        with connect(self.db) as db:
            old=db.execute("SELECT state,version FROM cr_claims WHERE id=?",
                           (item["id"],)).fetchone()
            self.assertEqual(tuple(old),("superseded",3))
            new=db.execute("""SELECT quote FROM cr_evidence WHERE source_id=?
                AND revision<>?""",("pdf:"+A,item["revision"])).fetchone()
            self.assertIn("45 Nm",new["quote"])

    def test_source_snapshot_is_not_evidence_or_verified_knowledge(self):
        with connect(self.db) as db:
            db.execute("""INSERT INTO research_jobs(id,spec,target,model_id,state,
                source_text,created,updated)
                VALUES(?,?,?,?,?,?,?,?)""",("1"*32,
                '{"source_type":"text","title":"pasted"}',
                "auto","sirussofts-coder","completed","Rear axle torque: 40 Nm",1,1))
        self.research.inventory()
        self.research.control("include",source_id="research:"+"1"*32)
        self.assertEqual(self.ledger.index_batch()["parsed_candidates"],0)
        self.assertEqual(self.ledger.status()["counts"]["supported_claims"],0)

    def test_scope_and_review_reject_invalid_input(self):
        self.add_doc(A,"Rear axle torque: 40 Nm")
        self.index()
        item=self.item()
        for scope in ("private-host", "HTTP://server", "../etc", "2026", "brp//2026"):
            with self.assertRaises(ValueError):
                self.ledger.set_scope("pdf:"+A,scope)
        with self.assertRaises(ValueError):
            self.ledger.set_scope("research:"+"1"*32,SCOPE)
        with self.assertRaises(ValueError):
            self.ledger.review(item["id"],item["quote_sha"],item["revision"],
                               ACTOR,"unsupported")
        with self.assertRaises(ValueError):
            self.ledger.review(item["id"],item["quote_sha"],"invalid",
                               ACTOR,"support")


if __name__=="__main__":
    unittest.main()
