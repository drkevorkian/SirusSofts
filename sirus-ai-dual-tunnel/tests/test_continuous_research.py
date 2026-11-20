"""Regression tests for private continuous research inventory and scheduling."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import input_server as ai
from continuous_research import (ContinuousResearch, connect, digest,
                                 init_schema, admission_reasons)


SHA="a"*64
FAILED="b"*64


class ContinuousResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.db=self.base/"input.sqlite3"
        self.pdf=self.base/"pdf_ingest.sqlite3"
        ai.init_db(self.db)
        with sqlite3.connect(self.pdf) as con:
            con.executescript("""
            CREATE TABLE documents (
              sha256 TEXT PRIMARY KEY, filename TEXT, status TEXT,
              reviewed INTEGER, pages INTEGER, excerpts INTEGER,
              updated_at INTEGER);
            CREATE TABLE excerpts (
              sha256 TEXT NOT NULL, page INTEGER NOT NULL,part INTEGER NOT NULL,
              text TEXT NOT NULL,PRIMARY KEY(sha256,page,part));
            """)
        self.research=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")

    def add_doc(self, sha=SHA, status="ready", text="Verified source sentence.",
                pages=3, updated=1):
        with sqlite3.connect(self.pdf) as con:
            con.execute("""INSERT INTO documents
                (sha256,filename,status,reviewed,pages,excerpts,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (sha,"manual.pdf",status,0,pages,1 if text else 0,updated))
            if text:
                con.execute("INSERT INTO excerpts VALUES(?,?,?,?)",(sha,2,1,text))

    def run_result(self, messages, target, model_id, job_id):
        self.assertEqual(target,"auto")
        self.assertIn("[BEGIN UNTRUSTED SOURCE]",messages[1]["content"])
        return "Unverified analysis", "cpu"

    def test_idempotent_migration_preserves_old_research(self):
        with connect(self.db) as con:
            con.execute("""INSERT INTO research_jobs(id,spec,target,model_id,state,
                created,updated) VALUES(?,?,?,?,?,?,?)""",
                ("1"*32,'{"source_type":"pdf","title":"legacy"}',
                 "auto","sirussofts-coder","completed",1,1))
        init_schema(self.db)
        init_schema(self.db)
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM research_jobs").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM cr_config").fetchone()[0],1)
        self.assertEqual(self.research.status()["config"]["mode"],"disabled")

    def test_inventory_includes_failed_missing_pages_and_no_text(self):
        self.add_doc()
        self.add_doc(sha=FAILED,status="failed",text="",pages=4)
        result=self.research.inventory()
        self.assertEqual(result,{"sources":2,"sections":1,"unextracted_pages":6})
        status=self.research.status()
        self.assertEqual(status["inventory"]["incomplete_sources"],1)
        self.assertEqual(status["inventory"]["eligible_sources"],1)
        self.assertEqual(status["sections"]["current"],1)
        self.assertEqual(len(status["sources"]),2)
        with connect(self.db) as con:
            failed=con.execute("SELECT * FROM cr_sources WHERE id=?",
                               ("pdf:"+FAILED,)).fetchone()
            self.assertEqual(failed["missing_pages"],4)
            self.assertEqual(failed["extraction_status"],"failed")
        self.research.inventory()
        self.assertEqual(self.research.status()["sections"]["total"],1)

    def test_long_excerpts_split_without_loss_and_stable_identity(self):
        body="abcd"*4200
        self.add_doc(text=body)
        self.research.inventory()
        with connect(self.db) as con:
            before=con.execute("""SELECT slot,revision,byte_offset,chars
                FROM cr_sections WHERE current=1 ORDER BY byte_offset""").fetchall()
            self.assertEqual(sum(r["chars"] for r in before),len(body))
        self.research.inventory()
        with connect(self.db) as con:
            after=con.execute("""SELECT slot,revision,byte_offset,chars
                FROM cr_sections WHERE current=1 ORDER BY byte_offset""").fetchall()
            self.assertEqual([tuple(x) for x in before],[tuple(x) for x in after])

    def test_atomic_claim_and_no_duplicate_results(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims=list(pool.map(lambda _:self.research.claim(),range(6)))
        claimed=[c for c in claims if c]
        self.assertEqual(len(claimed),1)
        self.assertEqual(self.research.status()["jobs"].get("leased"),1)
        self.assertTrue(self.research.finish(claimed[0],"unreviewed","cpu"))
        self.assertFalse(self.research.finish(claimed[0],"different","cpu"))
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM cr_results").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT review_state FROM cr_results").fetchone()[0],
                             "unreviewed")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("""UPDATE cr_results SET review_state='supported'""")

    def test_recovery_retry_backoff_and_fencing(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        first=self.research.claim()
        with connect(self.db) as con:
            con.execute("UPDATE cr_jobs SET lease_until=0 WHERE id=?",(first["id"],))
        self.assertIsNone(self.research.claim())
        recovered=self.research.claim(now=time.time()+50)
        self.assertEqual(recovered["id"],first["id"])
        self.assertNotEqual(first["token"],recovered["token"])
        self.assertFalse(self.research.finish(first,"stale writer"))
        self.assertTrue(self.research.fail(recovered,RuntimeError("private text must not leak")))
        with connect(self.db) as con:
            row=con.execute("SELECT state,error,retry_at FROM cr_jobs").fetchone()
            self.assertEqual(row["state"],"queued")
            self.assertEqual(row["error"],"RuntimeError")
            self.assertGreater(row["retry_at"],time.time())
        next_claim=self.research.claim(now=time.time()+180)
        self.assertTrue(next_claim)
        self.assertTrue(self.research.finish(next_claim,"new draft"))

    def test_expired_lease_must_not_be_revived_by_old_heartbeat(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        lease=self.research.claim()
        with connect(self.db) as con:
            con.execute("UPDATE cr_jobs SET lease_until=0 WHERE id=?",(lease["id"],))
        self.assertFalse(self.research.heartbeat(lease["id"],lease["token"]))
        self.assertFalse(self.research.finish(lease,"too late"))
        self.assertIsNone(self.research.claim())
        replacement=self.research.claim(now=time.time()+60)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement["token"],lease["token"])

    def test_cycle_reeligibility_preserves_all_passes(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"passes":2,"revisit_seconds":60})
        self.research.control("enable")
        first=self.research.claim()
        self.assertTrue(self.research.finish(first,"pass one"))
        second=self.research.claim()
        self.assertEqual(second["id"],first["id"])
        self.assertEqual(second["pass_completed"],1)
        self.assertTrue(self.research.finish(second,"pass two"))
        self.assertIsNone(self.research.claim())  # complete first coverage cycle
        newer=self.research.claim(now=time.time()+61)
        self.assertIsNotNone(newer)
        self.assertNotEqual(newer["id"],first["id"])
        self.assertTrue(self.research.finish(newer,"new cycle draft"))
        with connect(self.db) as con:
            old=con.execute("""SELECT notes FROM cr_results WHERE job_id=?
                ORDER BY pass_number""",(first["id"],)).fetchall()
            self.assertEqual([r[0] for r in old],["pass one","pass two"])
            self.assertEqual(con.execute("SELECT count(*) FROM cr_cycles").fetchone()[0],2)

    def test_changed_removed_and_newly_eligible_are_not_silently_current(self):
        self.add_doc()
        self.research.inventory()
        with connect(self.db) as con:
            prior=con.execute("SELECT slot,revision FROM cr_sections").fetchone()
        with sqlite3.connect(self.pdf) as con:
            con.execute("UPDATE excerpts SET text='Revised sentence.' WHERE sha256=?",(SHA,))
            con.execute("UPDATE documents SET updated_at=2 WHERE sha256=?",(SHA,))
        self.research.inventory()
        with connect(self.db) as con:
            rows=con.execute("""SELECT revision,current FROM cr_sections
                WHERE slot=?""",(prior["slot"],)).fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual(sorted(x["current"] for x in rows),[0,1])
            self.assertNotEqual([x["revision"] for x in rows if x["current"]][0],
                                prior["revision"])
            self.assertGreater(con.execute("""SELECT count(*) FROM cr_events
                WHERE kind='changed'""").fetchone()[0],0)
        with sqlite3.connect(self.pdf) as con:
            con.execute("UPDATE documents SET status='failed',updated_at=3 WHERE sha256=?",
                        (SHA,))
        self.research.inventory()
        self.assertEqual(self.research.status()["inventory"]["eligible_sources"],0)
        with sqlite3.connect(self.pdf) as con:
            con.execute("UPDATE documents SET status='ready',updated_at=4 WHERE sha256=?",
                        (SHA,))
        self.research.inventory()
        with connect(self.db) as con:
            self.assertGreater(con.execute("""SELECT count(*) FROM cr_events
                WHERE kind='eligible'""").fetchone()[0],0)
        with sqlite3.connect(self.pdf) as con:
            con.execute("DELETE FROM documents WHERE sha256=?",(SHA,))
            con.execute("DELETE FROM excerpts WHERE sha256=?",(SHA,))
        self.research.inventory()
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT present FROM cr_sources").fetchone()[0],0)
            self.assertGreater(con.execute("""SELECT count(*) FROM cr_events
                WHERE kind='removed'""").fetchone()[0],0)

    def test_reincluded_section_in_open_cycle_requeues_same_revision(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        lease=self.research.claim()
        self.research.control("exclude",source_id="pdf:"+SHA)
        self.assertFalse(self.research.finish(lease,"excluded"))
        self.research.control("include",source_id="pdf:"+SHA)
        again=self.research.claim()
        self.assertIsNotNone(again)
        self.assertEqual(again["id"],lease["id"])
        self.assertNotEqual(again["token"],lease["token"])
        self.assertTrue(self.research.finish(again,"only current source"))

    def test_nontext_source_revision_requeues_even_if_chunk_unchanged(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        initial=self.research.claim()
        with sqlite3.connect(self.pdf) as con:
            con.execute("UPDATE documents SET updated_at=2 WHERE sha256=?",(SHA,))
        self.research.inventory()
        self.assertFalse(self.research.finish(initial,"outdated original revision"))
        replacement=self.research.claim()
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement["revision"],initial["revision"])
        self.assertTrue(self.research.finish(replacement,"current revision"))
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM cr_results").fetchone()[0],1)

    def test_pause_resume_stop_exclusion_and_priority(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        self.research.control("pause")
        self.assertIsNone(self.research.claim())
        self.research.control("priority",source_id="pdf:"+SHA,value=9)
        self.research.control("exclude",source_id="pdf:"+SHA)
        self.research.control("resume")
        self.assertIsNone(self.research.claim())
        self.research.control("include",source_id="pdf:"+SHA)
        lease=self.research.claim()
        self.assertIsNotNone(lease)
        self.research.control("stop")
        self.assertFalse(self.research.finish(lease,"must be fenced"))
        self.research.control("resume")
        self.assertIsNotNone(self.research.claim())

    def test_snapshot_is_private_and_opt_in_without_refetch(self):
        self.add_doc()
        spec='{"source_type":"url","title":"Saved https page"}'
        with connect(self.db) as con:
            con.execute("""INSERT INTO research_jobs(id,spec,target,model_id,state,
                source_text,source_sha256,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                ("2"*32,spec,"auto","sirussofts-coder","completed",
                 "Snapshot text",digest("Snapshot text"),1,1))
        self.research.inventory()
        key="research:"+"2"*32
        with connect(self.db) as con:
            row=con.execute("SELECT eligible,excluded FROM cr_sources WHERE id=?",
                            (key,)).fetchone()
            self.assertEqual(tuple(row),(1,1))
        self.research.control("enable")
        lease=self.research.claim()
        self.assertEqual(lease["source_id"],"pdf:"+SHA)
        self.research.finish(lease,"manual complete")
        self.research.control("include",source_id=key)
        new=self.research.claim()
        self.assertEqual(new["source_id"],key)
        self.assertIn("Snapshot text",self.research._section_text(new))
        self.research.finish(new,"snapshot draft")

    def test_disabled_admission_and_draft_boundary(self):
        self.add_doc()
        self.research.inventory()
        self.assertIsNone(self.research.claim())
        self.research.control("enable")
        with connect(self.db) as con:
            con.execute("""INSERT INTO jobs(id,messages,target,state,created)
                VALUES('chat','[]','auto','queued',?)""",(time.time(),))
        self.assertIsNone(self.research.claim())
        with connect(self.db) as con:
            con.execute("UPDATE jobs SET state='completed' WHERE id='chat'")
        self.assertTrue(self.research.run_once(self.run_result))
        self.assertEqual(self.research.status()["jobs"]["completed"],1)
        self.assertTrue(self.research.status()["draft_only"])
        self.assertFalse(self.research.status()["weight_training"])

    def test_resource_policy_wraparound_and_conservative_memory_gate(self):
        self.add_doc()
        self.research.inventory()
        cfg=self.research.status()["config"]
        self.assertEqual(admission_reasons(cfg,free_mib=0,load=100,hour=0),[])
        self.research.control("configure",value={
            "min_available_ram_mib":4096,"quiet_start_utc":22,
            "quiet_end_utc":6,"max_cpu_load":2.0})
        cfg=self.research.status()["config"]
        self.assertIn("low-free-ram",admission_reasons(cfg,free_mib=1024,load=0,hour=12))
        from unittest.mock import patch
        with patch("continuous_research.free_memory_mib",return_value=None):
            self.assertIn("ram-unavailable",admission_reasons(
                cfg,free_mib=None,load=0,hour=12))
        self.assertIn("quiet-hours-utc",admission_reasons(cfg,free_mib=8192,
                                                       load=0,hour=23))
        self.assertIn("quiet-hours-utc",admission_reasons(cfg,free_mib=8192,
                                                       load=0,hour=2))
        self.assertNotIn("quiet-hours-utc",admission_reasons(cfg,free_mib=8192,
                                                          load=0,hour=12))
        self.assertIn("cpu-load",admission_reasons(cfg,free_mib=8192,load=3,hour=12))
        self.research.control("enable")
        # A deliberately unachievable limit must deny new leases.
        self.research.control("configure",value={"min_available_ram_mib":1048576})
        self.assertIsNone(self.research.claim())
        self.assertEqual(self.research.status()["config"]["min_available_ram_mib"],
                         1048576)
        self.research.control("configure",value={"quiet_start_utc":-1,
                                                 "quiet_end_utc":-1,
                                                 "min_available_ram_mib":0})
        for invalid in ({"quiet_start_utc":5},
                        {"quiet_start_utc":6,"quiet_end_utc":6},
                        {"min_available_ram_mib":-1}):
            with self.assertRaises(ValueError):
                self.research.control("configure",value=invalid)

    def test_bad_configuration_rejected(self):
        self.add_doc()
        self.research.inventory()
        for value in (0,101,1.5,"100"):
            with self.assertRaises(ValueError):
                self.research.control("configure",value={"passes":value})
        with self.assertRaises(ValueError):
            self.research.control("priority",source_id="pdf:"+SHA,value=999)
        with self.assertRaises(ValueError):
            self.research.control("exclude",source_id="../secret")
        with self.assertRaises(ValueError):
            self.research.control("configure",value={"arbitrary_sql":"DROP TABLE"})
        with self.assertRaises(ValueError):
            self.research.control("configure",value={"model_id":"http://private-endpoint"})


if __name__=="__main__":
    unittest.main()
