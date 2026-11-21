"""Regression tests for private continuous research inventory and scheduling."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import input_server as ai
from continuous_research import (ContinuousResearch, connect, digest,
                                 init_schema, admission_reasons,
                                 gpu_memory_snapshot, database_size_bytes)


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

    def test_upgrade_prior_config_preserves_state_and_adds_resource_columns(self):
        # Reproduce the previous phase-A table without new admission fields.
        legacy=self.base/"legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("""CREATE TABLE cr_config(
                id INTEGER PRIMARY KEY,mode TEXT,passes INTEGER,model_id TEXT,
                target TEXT,revisit_seconds INTEGER,max_cpu_load REAL,
                max_chat_jobs INTEGER,last_completed REAL,updated REAL,
                last_inventory REAL,scan_requested INTEGER,scan_error TEXT)""")
            db.execute("""INSERT INTO cr_config VALUES
                (1,'paused',42,'old-model','cpu',3600,3.0,0,8,9,10,0,'')""")
        init_schema(legacy)
        init_schema(legacy)
        with sqlite3.connect(legacy) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM cr_config WHERE id=1").fetchone()
            self.assertEqual(row["mode"],"paused")
            self.assertEqual(row["passes"],42)
            self.assertEqual(row["model_id"],"old-model")
            self.assertEqual(row["min_available_ram_mib"],0)
            self.assertEqual(row["quiet_start_utc"],-1)
            self.assertEqual(row["quiet_end_utc"],-1)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_config").fetchone()[0],1)

    def test_checkpointed_100_passes_resume_across_engine_recreation(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"passes":100})
        self.research.control("enable")
        def infer(messages,target,model_id,job_id):
            return "bounded unreviewed research result", "cpu"
        for _ in range(37):
            self.assertTrue(self.research.run_once(infer))
        # A new process opens the same durable database; no completed pass is lost.
        next_process=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")
        for _ in range(63):
            self.assertTrue(next_process.run_once(infer))
        with connect(self.db) as db:
            row=db.execute("SELECT id,state,pass_completed FROM cr_jobs").fetchone()
            self.assertEqual(row["state"],"completed")
            self.assertEqual(row["pass_completed"],100)
            self.assertEqual(db.execute("""SELECT count(DISTINCT pass_number)
                FROM cr_results WHERE job_id=?""",(row["id"],)).fetchone()[0],100)
            self.assertEqual(db.execute("""SELECT count(*) FROM cr_results
                WHERE job_id=?""",(row["id"],)).fetchone()[0],100)
        self.assertIsNone(next_process.claim())

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

    def test_pass_metrics_and_cycle_percentage_are_measured_not_guessed(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"passes":2})
        self.research.control("enable")
        self.assertEqual(self.research.status()["cycle_progress"]["planned_passes"],0)
        for number in (1,2):
            def infer(messages,target,model_id,job_id):
                time.sleep(0.005)
                return f"Unreviewed source draft {number}", "cpu"
            self.assertTrue(self.research.run_once(infer))
            status=self.research.status()
            progress=status["cycle_progress"]
            self.assertEqual(progress["planned_passes"],2)
            self.assertEqual(progress["finished_passes"],number)
            self.assertEqual(progress["pass_percent"],number*50.0)
            self.assertEqual(status["throughput"]["measured_passes"],number)
            self.assertGreater(status["throughput"]["average_inference_ms"],0)
            self.assertGreater(status["throughput"]["estimated_passes_per_hour"],0)
            self.assertGreaterEqual(status["throughput"]["passes_last_60_minutes"],number)
        self.assertEqual(status["cycle"]["state"],"completed")
        with connect(self.db) as db:
            for row in db.execute("SELECT duration_ms,output_chars FROM cr_results"):
                self.assertGreater(row["duration_ms"],0)
                self.assertGreater(row["output_chars"],0)

    def test_targeted_reanalysis_does_not_repeat_unchanged_sources_early(self):
        self.add_doc(SHA,text="Old unchanged source specification.")
        self.add_doc(FAILED,text="Other source unaffected.")
        self.research.inventory()
        self.research.control("configure",value={
            "passes":1,"revisit_seconds":3600})
        self.research.control("enable")
        for _ in range(2):
            lease=self.research.claim()
            self.assertIsNotNone(lease)
            self.assertTrue(self.research.finish(lease,"Unreviewed draft"))
        self.assertEqual(self.research.status()["cycle"]["state"],"completed")
        self.assertIsNone(self.research.claim())
        with sqlite3.connect(self.pdf) as db:
            db.execute("UPDATE excerpts SET text='Updated evidence.' WHERE sha256=?",
                       (SHA,))
            db.execute("UPDATE documents SET updated_at=2 WHERE sha256=?",(SHA,))
        self.research.inventory()
        updated=self.research.claim()
        self.assertIsNotNone(updated)
        self.assertEqual(updated["source_id"],"pdf:"+SHA)
        self.assertTrue(self.research.finish(updated,"Reanalysis from current source"))
        self.assertEqual(self.research.status()["cycle"]["state"],"completed")
        self.assertIsNone(self.research.claim())
        with connect(self.db) as db:
            count=db.execute("SELECT count(*) FROM cr_cycles").fetchone()[0]
            self.assertEqual(count,2)
            other=db.execute("""SELECT count(*) FROM cr_jobs j
                JOIN cr_sections s ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=?""",("pdf:"+FAILED,)).fetchone()[0]
            self.assertEqual(other,1)
        # Full revisit is still permitted once the configured interval elapses.
        after=self.research.claim(now=time.time()+3601)
        self.assertIsNotNone(after)

    def test_new_revision_precedes_unchanged_revisit_at_equal_priority(self):
        self.add_doc()
        second="b"*64
        self.add_doc(second,text="Other unchanged approved source")
        self.research.inventory()
        self.research.control("configure",value={"revisit_seconds":60})
        self.research.control("enable")
        for _ in range(2):
            lease=self.research.claim()
            self.assertIsNotNone(lease)
            self.assertTrue(self.research.finish(lease,"Historical draft"))
        self.assertIsNone(self.research.claim())
        with sqlite3.connect(self.pdf) as db:
            db.execute("""UPDATE excerpts SET text='Changed primary source.'
                WHERE sha256=?""",(SHA,))
            db.execute("UPDATE documents SET updated_at=2 WHERE sha256=?",(SHA,))
        self.research.inventory()
        # Both are due for a full cycle. The changed revision must win over
        # the previous completed unchanged revision at equal admin priority.
        lease=self.research.claim(now=time.time()+61)
        self.assertIsNotNone(lease)
        self.assertEqual(lease["source_id"],"pdf:"+SHA)
        self.assertTrue(self.research.finish(lease,"New source revision draft"))
        older=self.research.claim()
        self.assertIsNotNone(older)
        self.assertEqual(older["source_id"],"pdf:"+second)
        self.assertTrue(self.research.finish(older,"Unchanged review completed"))
        self.assertEqual(self.research.status()["cycle"]["state"],"completed")

    def test_explicit_admin_priority_still_overrides_novelty(self):
        self.add_doc()
        other="b"*64
        self.add_doc(other,text="Unchanged source with operator priority")
        self.research.inventory()
        self.research.control("configure",value={"revisit_seconds":60})
        self.research.control("enable")
        for _ in range(2):
            self.assertTrue(self.research.finish(self.research.claim(),
                                                 "Initial supported? No, draft."))
        self.research.control("priority",source_id="pdf:"+other,value=10)
        with sqlite3.connect(self.pdf) as db:
            db.execute("UPDATE excerpts SET text='Fresh revision.' WHERE sha256=?",
                       (SHA,))
            db.execute("UPDATE documents SET updated_at=2 WHERE sha256=?",(SHA,))
        self.research.inventory()
        lease=self.research.claim(now=time.time()+61)
        self.assertIsNotNone(lease)
        self.assertEqual(lease["source_id"],"pdf:"+other)
        self.assertTrue(self.research.finish(lease,"Operator priority honored"))

    def test_targeted_cycle_scope_survives_restart(self):
        # Three PDFs: after full coverage, revise two and leave one untouched.
        self.add_doc(SHA,text="Original source A.")
        self.add_doc(FAILED,text="Original source B.")
        third="c"*64
        self.add_doc(third,text="Unchanged source C.")
        self.research.inventory()
        self.research.control("configure",value={"passes":1,"revisit_seconds":3600})
        self.research.control("enable")
        for _ in range(3):
            lease=self.research.claim()
            self.assertTrue(self.research.finish(lease,"Private draft"))
        with sqlite3.connect(self.pdf) as db:
            db.execute("""UPDATE excerpts SET text='Revised source A.'
                WHERE sha256=?""",(SHA,))
            db.execute("""UPDATE excerpts SET text='Revised source B.'
                WHERE sha256=?""",(FAILED,))
            db.execute("""UPDATE documents SET updated_at=2
                WHERE sha256 IN (?,?)""",(SHA,FAILED))
        self.research.inventory()
        first=self.research.claim()
        self.assertIsNotNone(first)
        self.assertTrue(self.research.finish(first,"Revised draft"))
        status=self.research.status()
        self.assertEqual(status["cycle"]["scope"],"new_only")
        self.assertEqual(status["cycle"]["state"],"open")
        restored=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")
        other=restored.claim()
        self.assertIsNotNone(other)
        self.assertNotEqual(other["source_id"],first["source_id"])
        self.assertIn(other["source_id"],("pdf:"+SHA,"pdf:"+FAILED))
        self.assertTrue(restored.finish(other,"Other revised draft"))
        self.assertEqual(restored.status()["cycle"]["state"],"completed")
        with connect(self.db) as db:
            old=db.execute("""SELECT count(*) FROM cr_jobs j JOIN cr_sections s
                ON s.slot=j.slot AND s.revision=j.revision
                WHERE s.source_id=?""",("pdf:"+third,)).fetchone()[0]
            self.assertEqual(old,1)

    def test_legacy_cycle_schema_retains_completed_rows(self):
        legacy=self.base/"legacy-cycles.sqlite3"
        ai.init_db(legacy)
        with sqlite3.connect(legacy) as db:
            db.execute("DROP TABLE cr_cycles")
            db.execute("""CREATE TABLE cr_cycles(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started REAL NOT NULL,completed REAL,
                state TEXT NOT NULL CHECK(state IN ('open','completed')))""")
            db.execute("""INSERT INTO cr_cycles(started,completed,state)
                VALUES(100,110,'completed')""")
        init_schema(legacy)
        init_schema(legacy)
        with sqlite3.connect(legacy) as db:
            self.assertEqual(db.execute("""SELECT scope FROM cr_cycles
                WHERE id=1""").fetchone()[0],"full")
            self.assertEqual(db.execute("""SELECT state FROM cr_cycles
                WHERE id=1""").fetchone()[0],"completed")

    def test_source_history_is_complete_paginated_and_preserves_old_revisions(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"passes":12})
        self.research.control("enable")
        for i in range(12):
            self.assertTrue(self.research.run_once(
                lambda messages,target,model_id,job_id: (
                    "Private draft answer %02d" % i, "cpu")))
        sid="pdf:"+SHA
        first=self.research.history(sid)
        self.assertEqual(first["jobs_total"],1)
        self.assertEqual(first["pass_results_total"],12)
        self.assertEqual(len(first["passes"]),8)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["passes"][0]["pass_number"],12)
        self.assertEqual(first["passes"][0]["page"],2)
        self.assertEqual(first["passes"][0]["part"],1)
        self.assertEqual(first["passes"][0]["review_state"],"unreviewed")
        self.assertTrue(first["passes"][0]["notes"].startswith("Private draft answer"))
        last=self.research.history(sid,8)
        self.assertEqual(len(last["passes"]),4)
        self.assertFalse(last["has_more"])
        self.assertEqual(last["passes"][-1]["pass_number"],1)
        self.assertEqual(first["jobs"][0]["pass_completed"],12)
        self.assertTrue(first["draft_only"])
        self.assertFalse(first["weight_training"])
        with sqlite3.connect(self.pdf) as db:
            db.execute("UPDATE excerpts SET text='Changed source revision.'")
            db.execute("UPDATE documents SET updated_at=2")
        self.research.inventory()
        historical=self.research.history(sid)
        self.assertEqual(historical["pass_results_total"],12)
        self.assertFalse(historical["passes"][0]["current"])
        self.assertNotEqual(historical["passes"][0]["source_revision"],
                            historical["source"]["revision"])
        with sqlite3.connect(self.pdf) as db:
            db.execute("DELETE FROM documents")
            db.execute("DELETE FROM excerpts")
        self.research.inventory()
        self.assertEqual(self.research.history(sid)["source"]["present"],0)
        self.assertEqual(self.research.history(sid)["pass_results_total"],12)

    def test_source_history_fails_closed_for_unknown_and_invalid_identifiers(self):
        for invalid in ("", "file:///etc/passwd", "pdf:"+"a"*63,
                        "research:"+"z"*32, "../../secret", None):
            with self.assertRaises(ValueError):
                self.research.history(invalid)
        self.add_doc()
        self.research.inventory()
        with self.assertRaises(ValueError):
            self.research.history("pdf:"+FAILED)
        for invalid in (-1,1000001,"0",1.5,True):
            with self.assertRaises(ValueError):
                self.research.history("pdf:"+SHA,invalid)
        for invalid in (0,9,True):
            with self.assertRaises(ValueError):
                self.research.history("pdf:"+SHA,0,invalid)
        self.assertEqual(self.research.history("pdf:"+SHA)["pass_results_total"],0)

    def test_backward_compatible_research_result_timing_migration(self):
        legacy=self.base/"legacy-results.sqlite3"
        ai.init_db(legacy)
        with sqlite3.connect(legacy) as db:
            db.execute("DROP TABLE cr_results")
            db.execute("""CREATE TABLE cr_results(
                job_id TEXT NOT NULL,pass_number INTEGER NOT NULL,
                notes TEXT NOT NULL,sha256 TEXT NOT NULL,
                review_state TEXT NOT NULL DEFAULT 'unreviewed',
                created REAL NOT NULL,PRIMARY KEY(job_id,pass_number))""")
            db.execute("""INSERT INTO cr_results(job_id,pass_number,notes,
                sha256,created) VALUES('old',1,'historical draft','sha',123)""")
        init_schema(legacy)
        init_schema(legacy)
        with sqlite3.connect(legacy) as db:
            row=db.execute("""SELECT notes,review_state,duration_ms,
                output_chars FROM cr_results WHERE job_id='old'""").fetchone()
            self.assertEqual(row,("historical draft","unreviewed",0,0))
            self.assertEqual(db.execute("SELECT count(*) FROM cr_results").fetchone()[0],1)

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

    def test_cancel_one_leased_job_fences_output_and_persists_after_resume(self):
        self.add_doc(text="A controlled source.")
        other="b"*64
        self.add_doc(other,text="An independent source.")
        self.research.inventory()
        self.research.control("configure",value={"passes":3,
                                                  "revisit_seconds":60})
        self.research.control("enable")
        lease=self.research.claim()
        self.assertIsNotNone(lease)
        self.assertTrue(self.research.finish(lease,"First checkpointed pass"))
        next_pass=self.research.claim()
        self.assertEqual(next_pass["id"],lease["id"])
        self.assertEqual(next_pass["pass_completed"],1)
        cancelled=self.research.control("cancel",job_id=lease["id"],
                                        actor="cms-admin:12")
        self.assertEqual(cancelled["jobs"]["stopped"],1)
        self.assertFalse(self.research.heartbeat(next_pass["id"],next_pass["token"]))
        self.assertFalse(self.research.finish(next_pass,"Should not be stored"))
        self.assertFalse(self.research.fail(next_pass,RuntimeError("late error")))
        with connect(self.db) as db:
            old=db.execute("""SELECT pass_number,notes FROM cr_results
                WHERE job_id=?""",(lease["id"],)).fetchall()
            self.assertEqual([tuple(r) for r in old],
                             [(1,"First checkpointed pass")])
            c=db.execute("""SELECT actor,source_id,cycle,pass_completed
                FROM cr_job_cancellations WHERE job_id=?""",
                (lease["id"],)).fetchone()
            self.assertEqual(c["actor"],"cms-admin:12")
            self.assertEqual(c["pass_completed"],1)
            self.assertEqual(db.execute("""SELECT count(*) FROM cr_events
                WHERE kind='job-cancelled' AND subject=?""",
                (lease["id"],)).fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""DELETE FROM cr_job_cancellations
                    WHERE job_id=?""",(lease["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""UPDATE cr_job_cancellations SET actor='other'
                    WHERE job_id=?""",(lease["id"],))
        hist=self.research.history(next_pass["source_id"])
        row=next(j for j in hist["jobs"] if j["id"]==lease["id"])
        self.assertEqual(row["cancelled_by"],"cms-admin:12")
        self.research.control("pause")
        self.research.control("resume")
        with connect(self.db) as db:
            state=db.execute("SELECT state FROM cr_jobs WHERE id=?",
                             (lease["id"],)).fetchone()[0]
            self.assertEqual(state,"stopped")
        # Other queued work is unaffected by the targeted cancellation.
        other_lease=self.research.claim()
        self.assertIsNotNone(other_lease)
        self.assertNotEqual(other_lease["id"],lease["id"])
        self.assertEqual(other_lease["source_id"],"pdf:"+other
                         if next_pass["source_id"]=="pdf:"+SHA
                         else "pdf:"+SHA)
        for _ in range(3):
            self.assertTrue(self.research.finish(other_lease,"Other research"))
            other_lease=self.research.claim()
            if other_lease is None:
                break
        with connect(self.db) as db:
            self.assertEqual(db.execute("""SELECT count(*) FROM cr_cycles
                WHERE state='completed'""").fetchone()[0],1)
        restored=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")
        self.assertIsNone(restored.claim())
        newer=restored.claim(now=time.time()+61)
        self.assertIsNotNone(newer)
        self.assertNotEqual(newer["id"],lease["id"])

    def test_cancel_queued_job_does_not_cancel_other_pending_sections(self):
        self.add_doc()
        other="b"*64
        self.add_doc(other,text="Other source section")
        self.research.inventory()
        self.research.control("enable")
        first=self.research.claim()
        with connect(self.db) as db:
            queued=db.execute("""SELECT id FROM cr_jobs
                WHERE state='queued' LIMIT 1""").fetchone()[0]
        self.research.control("cancel",job_id=queued,actor="cms-admin:1")
        self.assertTrue(self.research.finish(first,"Original lease remains valid"))
        with connect(self.db) as db:
            self.assertEqual(db.execute("""SELECT state FROM cr_jobs
                WHERE id=?""",(queued,)).fetchone()[0],"stopped")
            self.assertEqual(db.execute("""SELECT state FROM cr_jobs
                WHERE id=?""",(first["id"],)).fetchone()[0],"completed")
        self.assertIsNone(self.research.claim())

    def test_cancellation_rejects_bad_actor_bad_id_and_completed_or_repeat(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        lease=self.research.claim()
        for job,actor in (("", "cms-admin:1"), ("../escape","cms-admin:1"),
                          ("A"*32,"cms-admin:1"),(lease["id"],"model:auto"),
                          (lease["id"],"cms-admin:0"),(lease["id"],""),
                          (lease["id"],None)):
            with self.assertRaises(ValueError):
                self.research.control("cancel",job_id=job,actor=actor)
        with self.assertRaises(ValueError):
            self.research.control("cancel",job_id="f"*32,actor="cms-admin:1")
        self.research.control("cancel",job_id=lease["id"],actor="cms-admin:1")
        with self.assertRaises(ValueError):
            self.research.control("cancel",job_id=lease["id"],actor="cms-admin:1")
        self.assertFalse(self.research.finish(lease,"stale"))
        self.assertEqual(self.research.status()["recent_jobs"][0]["cancelled_by"],
                         "cms-admin:1")

    def test_audit_is_append_only_and_source_scoped_with_pagination(self):
        self.add_doc()
        other="b"*64
        self.add_doc(other,text="Different original source.")
        self.research.inventory()
        self.research.control("configure",value={"passes":12})
        self.research.control("enable")
        lease=self.research.claim()
        sid=lease["source_id"]
        for pass_no in range(12):
            self.assertTrue(self.research.finish(lease,f"Draft #{pass_no}"))
            if pass_no<11:
                lease=self.research.claim()
        audit=self.research.history(sid,audit_offset=0)
        self.assertEqual(audit["audit_offset"],0)
        self.assertEqual(len(audit["audit_events"]),20)
        self.assertTrue(audit["audit_has_more"])
        self.assertGreater(audit["audit_total"],20)
        self.assertEqual(audit["audit_events"][0]["kind"],"job-complete")
        last=self.research.history(sid,audit_offset=20)
        self.assertGreater(len(last["audit_events"]),0)
        self.assertFalse(last["audit_has_more"])
        self.assertEqual(last["audit_total"],audit["audit_total"])
        from_db=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")
        self.assertEqual(from_db.history(sid)["audit_total"],audit["audit_total"])
        # An unrelated document must never inherit this job's audit records.
        other_sid="pdf:"+other if sid!="pdf:"+other else "pdf:"+SHA
        unrelated=from_db.history(other_sid)
        self.assertEqual(unrelated["pass_results_total"],0)
        self.assertFalse(any(e["subject"]==lease["id"]
                             for e in unrelated["audit_events"]))
        with connect(self.db) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM cr_events")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE cr_events SET kind='changed'")
        for offset in (-1,1000001,1.5,"0",True):
            with self.assertRaises(ValueError):
                from_db.history(sid,audit_offset=offset)

    def test_terminal_failure_and_recovery_have_audited_transitions(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        lease=self.research.claim()
        for count in range(5):
            self.assertTrue(self.research.fail(lease,RuntimeError("secret content")))
            if count<4:
                lease=self.research.claim(now=time.time()+4000)
                self.assertIsNotNone(lease)
        with connect(self.db) as db:
            types=[r["kind"] for r in db.execute("""SELECT kind
                FROM cr_events WHERE subject=? ORDER BY id""",(lease["id"],))]
            self.assertIn("job-failed",types)
            self.assertEqual(types.count("job-error"),5)
            self.assertEqual(types.count("job-leased"),5)
            self.assertEqual(db.execute("""SELECT state FROM cr_jobs
                WHERE id=?""",(lease["id"],)).fetchone()[0],"failed")
        events=self.research.history(lease["source_id"])
        self.assertEqual(events["audit_events"][0]["kind"],"job-failed")
        # Error messages are never persisted in events, only exception types.
        self.assertNotIn("secret content",str(events["audit_events"]))

    def test_legacy_schema_supports_additive_cancel_table(self):
        self.add_doc()
        self.research.inventory()
        with connect(self.db) as db:
            db.execute("INSERT INTO cr_events(happened,kind,subject) "
                       "VALUES(1,'legacy','preserve')")
        init_schema(self.db)
        init_schema(self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute("""SELECT count(*) FROM cr_events
                WHERE kind='legacy' AND subject='preserve'""").fetchone()[0],1)
            self.assertTrue(db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='cr_job_cancellations'""").fetchone())

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

    def test_read_only_gpu_vram_discovery_and_invalid_counters(self):
        from unittest.mock import Mock
        root=self.base/"drm"
        amd=root/"card2"/"device"
        amd.mkdir(parents=True)
        (amd/"vendor").write_text("0x1002\n")
        (amd/"mem_info_vram_total").write_text(str(8*1048576)+"\n")
        (amd/"mem_info_vram_used").write_text(str(3*1048576)+"\n")
        other=root/"card5"/"device"
        other.mkdir(parents=True)
        (other/"vendor").write_text("0x8086\n")
        command=self.base/"nvidia-smi"
        command.write_text("")  # mock subprocess; no real shell execution
        mock=Mock(returncode=0,stdout="0, 6144, 3072\n1, 4096, 1024\n")
        runner=Mock(return_value=mock)
        gpus=gpu_memory_snapshot(drm_root=root,nvidia_binary=str(command),
                                 runner=runner)
        self.assertEqual([(g["backend"],g["free_mib"]) for g in gpus],
                         [("amd",5),("nvidia",3072),("nvidia",1024)])
        self.assertEqual(runner.call_args.kwargs["timeout"],1)
        self.assertFalse(runner.call_args.kwargs["check"])
        self.assertTrue(runner.call_args.kwargs["capture_output"])
        self.assertNotIn("shell",runner.call_args.kwargs)
        (amd/"mem_info_vram_used").write_text(str(10*1048576))
        gpus=gpu_memory_snapshot(drm_root=root,
                                 nvidia_binary=str(self.base/"missing"),
                                 runner=runner)
        self.assertEqual(gpus[0]["backend"],"amd")
        self.assertIsNone(gpus[0]["free_mib"])
        self.assertEqual(gpus[0]["telemetry"],"unavailable")
        runner.reset_mock()
        self.assertEqual(len(gpus),1)
        runner.assert_not_called()

    def test_gpu_admission_is_disabled_by_default_and_fails_closed_on_unknown_vram(self):
        from unittest.mock import patch
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        default=self.research.status()["config"]
        self.assertEqual(default["min_gpu_free_mib"],0)
        self.assertNotIn("vram-telemetry-unavailable",
            admission_reasons(default,load=0,free_mib=999999,hour=12,
                              gpu_devices=[]))
        self.research.control("configure",value={
            "min_gpu_free_mib":2048,"target":"amd"})
        cfg=self.research.status()["config"]
        self.assertIn("vram-telemetry-unavailable",
            admission_reasons(cfg,load=0,free_mib=999999,hour=12,
                              gpu_devices=[]))
        self.assertIn("low-free-vram",
            admission_reasons(cfg,load=0,free_mib=999999,hour=12,
                              gpu_devices=[{"backend":"amd","free_mib":1024}]))
        self.assertNotIn("low-free-vram",
            admission_reasons(cfg,load=0,free_mib=999999,hour=12,
                              gpu_devices=[{"backend":"amd","free_mib":4096}]))
        with patch("continuous_research.gpu_memory_snapshot",return_value=[]):
            self.assertIsNone(self.research.claim())
        with patch("continuous_research.gpu_memory_snapshot",return_value=[
                    {"backend":"amd","free_mib":4096}]):
            lease=self.research.claim()
        self.assertIsNotNone(lease)
        self.assertEqual(lease["target"],"amd")
        self.assertTrue(self.research.finish(lease,"Approved? No: draft only."))
        self.research.control("configure",value={"target":"auto"})
        cfg=self.research.status()["config"]
        self.assertIn("gpu-target-required",
            admission_reasons(cfg,load=0,free_mib=999999,hour=12,
                              gpu_devices=[{"backend":"amd","free_mib":4096}]))
        for invalid in (-1,1048577,"1024",True):
            with self.assertRaises(ValueError):
                self.research.control("configure",value={"min_gpu_free_mib":invalid})
        self.research.control("configure",value={"min_gpu_free_mib":0})
        self.assertEqual(self.research.status()["config"]["min_gpu_free_mib"],0)

    def test_legacy_gpu_config_migration_keeps_existing_limits(self):
        legacy=self.base/"legacy-gpu.sqlite3"
        ai.init_db(legacy)
        with sqlite3.connect(legacy) as db:
            db.execute("ALTER TABLE cr_config RENAME TO old_config")
            db.execute("""CREATE TABLE cr_config(
                id INTEGER PRIMARY KEY,mode TEXT,passes INTEGER,model_id TEXT,
                target TEXT,revisit_seconds INTEGER,max_cpu_load REAL,
                max_chat_jobs INTEGER,last_completed REAL,updated REAL,
                last_inventory REAL,scan_requested INTEGER,scan_error TEXT,
                min_available_ram_mib INTEGER,quiet_start_utc INTEGER,
                quiet_end_utc INTEGER)""")
            db.execute("""INSERT INTO cr_config VALUES(
                1,'paused',3,'sirussofts-coder','nvidia',90,1.5,0,1,2,3,0,'',
                8000,-1,-1)""")
            db.execute("DROP TABLE old_config")
        init_schema(legacy)
        init_schema(legacy)
        with sqlite3.connect(legacy) as db:
            self.assertEqual(db.execute("""SELECT min_gpu_free_mib
                FROM cr_config""").fetchone()[0],0)
            self.assertEqual(db.execute("""SELECT min_available_ram_mib
                FROM cr_config""").fetchone()[0],8000)
            self.assertEqual(db.execute("SELECT mode FROM cr_config").fetchone()[0],
                             "paused")

    def test_database_size_includes_wal_and_shm_and_unknown_fails_closed(self):
        standalone=self.base/"physical-size.sqlite3"
        standalone.write_bytes(b"main" * 32)
        wal=Path(str(standalone)+"-wal")
        wal.write_bytes(b"wal" * 300)
        shm=Path(str(standalone)+"-shm")
        shm.write_bytes(b"shm" * 80)
        self.assertEqual(database_size_bytes(standalone),
                         standalone.stat().st_size+wal.stat().st_size+
                         shm.stat().st_size)
        wal.unlink()
        self.assertEqual(database_size_bytes(standalone),
                         standalone.stat().st_size+shm.stat().st_size)
        standalone.unlink()
        self.assertIsNone(database_size_bytes(standalone))

    def test_private_db_budget_stops_new_work_without_erasing_history(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        self.research.control("configure",value={"max_database_mib":10})
        with patch("continuous_research.database_size_bytes",
                   return_value=11*1048576):
            self.assertIsNone(self.research.claim())
            status=self.research.status()
            self.assertEqual(status["database_bytes"],11*1048576)
            self.assertIn("database-size-limit",status["admission_blocks"])
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_jobs").fetchone()[0],0)
        with patch("continuous_research.database_size_bytes",
                   return_value=9*1048576):
            lease=self.research.claim()
            self.assertIsNotNone(lease)
            self.assertTrue(self.research.finish(lease,"Private draft remains intact"))
        with patch("continuous_research.database_size_bytes",return_value=None):
            status=self.research.status()
            self.assertIn("database-size-unavailable",status["admission_blocks"])
            self.assertIsNone(self.research.claim())
        self.research.control("configure",value={"max_database_mib":0})
        with patch("continuous_research.process_resident_mib",return_value=49):
            self.assertEqual(self.research.status()["process_rss_mib"],49)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_results").fetchone()[0],1)
        for bad in (-1,1048577,"1024",True):
            with self.assertRaises(ValueError):
                self.research.control("configure",value={"max_database_mib":bad})

    def test_operational_telemetry_is_bounded_durable_and_not_evidence(self):
        t=int(time.time()//60)*60+600
        self.assertTrue(self.research.sample_resources(
            now=t-8*86400,resident=21,available=8000,size_bytes=1024))
        self.assertTrue(self.research.sample_resources(
            now=t-600,resident=25,available=7000,size_bytes=2048))
        self.assertFalse(self.research.sample_resources(
            now=t-600,resident=30,available=5000,size_bytes=4096))
        self.add_doc()
        self.add_doc("b"*64,text="Second source.")
        self.research.inventory()
        self.research.control("enable")
        lease=self.research.claim()
        self.assertIsNotNone(lease)
        self.assertTrue(self.research.sample_resources(
            now=t,resident=29,available=6000,size_bytes=8192))
        with connect(self.db) as db:
            samples=db.execute("""SELECT minute,queued,leased,process_rss_mib,
                database_bytes FROM cr_resource_samples ORDER BY minute""").fetchall()
            self.assertFalse(any(r["minute"]==t-8*86400 for r in samples))
            self.assertEqual(samples[-1]["queued"],1)
            self.assertEqual(samples[-1]["leased"],1)
            self.assertEqual(samples[-1]["process_rss_mib"],29)
            self.assertEqual(samples[-1]["database_bytes"],8192)
            # Diagnostic retention is allowed; original-source/audit history
            # is preserved even when a telemetry bucket expires.
            self.assertGreater(db.execute("SELECT count(*) FROM cr_events").
                               fetchone()[0],0)
        status=self.research.status()
        self.assertGreaterEqual(status["queued_growth_last_samples"],1)
        self.assertTrue(any(v["minute"]==t for v in status["resource_history"]))
        self.assertTrue(all(set(v)>={"minute","queued","process_rss_mib",
                                    "database_bytes"} for v in status["resource_history"]))
        restored=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")
        self.assertTrue(any(v["minute"]==t for v in
                            restored.status()["resource_history"]))
        for invalid in (-1,"now",True,1e20):
            with self.assertRaises(ValueError):
                self.research.sample_resources(now=invalid,
                                               resident=1,available=1,size_bytes=1)
        with self.assertRaises(ValueError):
            self.research.sample_resources(now=t+60,resident=-2,
                                           available=1,size_bytes=1)

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
