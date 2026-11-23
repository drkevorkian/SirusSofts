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
from continuous_research import (LEASE_SECONDS, ContinuousResearch, connect, digest,
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

    def test_new_research_defaults_to_nvidia_without_overriding_admin_choice(self):
        init_schema(self.db)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT target FROM cr_config WHERE id=1").fetchone()[0], "nvidia")
        self.research.control("configure",value={"target":"amd"})
        init_schema(self.db)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT target FROM cr_config WHERE id=1").fetchone()[0], "amd")

    def test_admin_status_does_not_write_telemetry(self):
        # An admin refresh must not acquire a writer lock simply to sample
        # resources while the PDF inventory is using the same SQLite DB.
        with patch.object(self.research, "sample_resources",
                          side_effect=AssertionError("status wrote telemetry")):
            first=self.research.status()
            second=self.research.status()
        self.assertEqual(first["config"]["mode"], second["config"]["mode"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM cr_resource_samples").fetchone()[0], 0)

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
        self.assertIn(target,("auto","nvidia"))
        self.assertIn("[BEGIN UNTRUSTED SOURCE]",messages[1]["content"])
        return "Unverified analysis", "cpu"

    def test_production_inference_timeout_expires_before_lease(self):
        import input_server as gateway
        engine=type("TestEngine",(),{"gpu_percent":35})()
        messages=[{"role":"user","content":"test"}]
        with patch.object(gateway,"choose_worker",return_value=("cpu","http://127.0.0.1:18482")) as choose, \
             patch.object(gateway,"chat",return_value=("draft",{})) as request:
            self.assertEqual(gateway.Engine._continuous_infer(engine,messages,"auto","local-model","job"),
                             ("draft","cpu",{}))
        self.assertEqual(request.call_args.kwargs["timeout"],180)
        self.assertLess(request.call_args.kwargs["timeout"],LEASE_SECONDS)

    def test_configurable_background_lease_concurrency_and_default(self):
        self.add_doc()
        with sqlite3.connect(self.pdf) as db:
            db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                       (SHA,3,1,"Another source section for bounded workers."))
            db.execute("UPDATE documents SET excerpts=2 WHERE sha256=?",(SHA,))
        self.research.inventory()
        self.assertEqual(self.research.status()["config"]["background_concurrency"],1)
        self.research.control("enable")
        first=self.research.claim()
        self.assertIsNotNone(first)
        self.assertIsNone(self.research.claim())
        self.research.control("configure",value={"background_concurrency":2})
        second=self.research.claim()
        self.assertIsNotNone(second)
        self.assertNotEqual(first["id"],second["id"])
        self.assertIsNone(self.research.claim())
        self.research.control("configure",value={"background_concurrency":1})
        self.assertIsNone(self.research.claim())
        for bad in (0,5,True):
            with self.assertRaises(ValueError):
                self.research.control("configure",value={"background_concurrency":bad})

    def test_auto_pool_distinct_workers_and_atomic_leases(self):
        self.add_doc()
        with sqlite3.connect(self.pdf) as db:
            for page in (3,4,5):
                db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                           (SHA,page,1,"Independent source section "+str(page)))
            db.execute("UPDATE documents SET pages=5,excerpts=4 WHERE sha256=?",(SHA,))
        self.research.inventory()
        self.research.control("configure",value={"target":"auto","background_concurrency":3})
        self.research.control("enable")
        def claim(worker):
            return self.research.claim(worker=worker,model_id="sirussofts-coder")
        workers=("cpu","amd","nvidia")
        with ThreadPoolExecutor(max_workers=3) as pool:
            leases=list(pool.map(claim,workers))
        self.assertTrue(all(leases))
        self.assertEqual({x["target"] for x in leases},set(workers))
        self.assertEqual(len({x["id"] for x in leases}),3)
        for name in workers:
            self.assertIsNone(claim(name))
        with connect(self.db) as db:
            self.assertEqual({row[0] for row in db.execute(
                "SELECT worker FROM cr_jobs WHERE state='leased'")},set(workers))
        for lease in leases:
            self.assertTrue(self.research.finish(lease,"draft",lease["target"]))
        self.assertIsNotNone(claim("amd"))

    def test_four_distinct_research_leases_and_status(self):
        self.add_doc()
        with sqlite3.connect(self.pdf) as db:
            for page in (3,4,5):
                db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                           (SHA,page,1,"Independent source section "+str(page)))
            db.execute("UPDATE documents SET pages=5,excerpts=4 WHERE sha256=?",(SHA,))
        self.research.inventory()
        self.research.control("configure",value={"target":"auto","background_concurrency":4})
        self.research.control("enable")
        workers=("cpu","amd","nvidia","mac")
        with ThreadPoolExecutor(max_workers=4) as pool:
            leases=list(pool.map(lambda worker: self.research.claim(
                worker=worker,model_id="sirussofts-coder"),workers))
        self.assertTrue(all(leases))
        self.assertEqual(len({x["id"] for x in leases}),4)
        status=self.research.status()
        self.assertEqual({x["worker"] for x in status["active_jobs"]},set(workers))
        self.assertEqual({x["worker"] for x in status["worker_counts"]},set(workers))
        for lease in leases:
            self.assertTrue(self.research.finish(lease,"draft",lease["target"]))

    def test_mac_disabled_skips_probe_and_claim(self):
        engine=ai.Engine.__new__(ai.Engine)
        engine.db=self.db
        engine.continuous=self.research
        with patch.object(ai,"mac_research_enabled",return_value=False), \
             patch.object(ai,"worker_healthy",side_effect=AssertionError("probed disabled Mac")), \
             patch.object(self.research,"run_once",side_effect=AssertionError("claimed disabled Mac")):
            self.assertFalse(engine._continuous_step("mac"))

    def test_mac_enabled_private_dispatch_and_no_public_chat_target(self):
        engine=ai.Engine.__new__(ai.Engine)
        engine.db=self.db
        engine.continuous=self.research
        engine.gpu_percent=90
        self.research.control("configure",value={"target":"mac","background_concurrency":4})
        self.research.control("enable")
        with patch.object(ai,"mac_research_enabled",return_value=True), \
             patch.object(ai,"worker_healthy",return_value=True) as health, \
             patch.object(ai,"local_model_ids",return_value=["sirussofts-coder"]), \
             patch.object(self.research,"run_once",return_value=True) as run, \
             patch.object(ai,"chat",return_value=("draft",{})) as chat:
            self.assertTrue(engine._continuous_step("mac"))
            health.assert_called_once_with(ai.MAC_RESEARCH)
            self.assertEqual(run.call_args.kwargs,{"worker":"mac","model_id":"sirussofts-coder"})
            self.assertEqual(engine._continuous_infer([], "mac","sirussofts-coder","job"),
                             ("draft","mac",{}))
            self.assertEqual(chat.call_args.args[0],"http://127.0.0.1:18588")
        with self.assertRaises(ValueError):
            ai.choose_worker("job","mac")

    def test_bound_claim_honors_target_model_and_worker_limit(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        self.assertIsNone(self.research.claim(worker="cpu",model_id="sirussofts-coder"))
        self.assertIsNone(self.research.claim(worker="nvidia",model_id="wrong-model"))
        self.assertIsNotNone(self.research.claim(worker="nvidia",model_id="sirussofts-coder"))

    def test_dispatch_skips_offline_worker_without_claim(self):
        self.research.control("configure",value={"target":"auto","background_concurrency":3})
        self.research.control("enable")
        engine=ai.Engine.__new__(ai.Engine)
        engine.db=self.db
        engine.continuous=self.research
        with patch.object(ai,"worker_healthy",return_value=False), \
             patch.object(self.research,"run_once",side_effect=AssertionError("claimed offline work")):
            self.assertFalse(engine._continuous_step("cpu"))
        with patch.object(ai,"worker_healthy",return_value=True), \
             patch.object(ai,"local_model_ids",return_value=["sirussofts-coder"]), \
             patch.object(self.research,"run_once",return_value=True) as run:
            self.assertTrue(engine._continuous_step("amd"))
            self.assertEqual(run.call_args.kwargs,{"worker":"amd","model_id":"sirussofts-coder"})

    def test_inference_energy_uses_attempt_start_settings_and_measured_time(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"research_watts":250,"rate_microusd_kwh":180000})
        self.research.control("enable")
        def infer(*args):
            self.research.control("configure",value={"research_watts":500,"rate_microusd_kwh":0})
            return "draft","cpu"
        with patch("continuous_research.time.monotonic",side_effect=[100,3700]):
            self.assertTrue(self.research.run_once(infer))
        summary=self.research.status()["energy_7d"]
        self.assertEqual((summary["attempts"],summary["timed"],summary["priced_samples"]),(1,1,1))
        self.assertAlmostEqual(summary["kwh"],0.25)
        self.assertAlmostEqual(summary["usd"],0.045)

    def test_daily_power_admission_and_utc_rollover(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"passes":2,"research_watts":200,"daily_energy_wh":200})
        self.research.control("enable")
        with patch("continuous_research.time.monotonic",side_effect=[0,3600]):
            self.research.run_once(self.run_result)
        self.assertIsNone(self.research.claim())
        self.assertIn("daily-energy-budget",self.research.status()["admission_blocks"])
        self.assertIsNotNone(self.research.claim(now=(int(time.time())//86400+1)*86400))

    def test_unknown_usage_and_scoped_expiring_admin_override(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"daily_energy_wh":20,
            "quiet_start_utc":22,"quiet_end_utc":6})
        self.research.control("enable")
        self.assertIsNone(self.research.claim())
        for minutes,actor in ((15,"guest:1"),(True,"cms-admin:1"),(61,"cms-admin:1")):
            with self.assertRaises(ValueError):
                self.research.control("power_override",value=minutes,actor=actor)
        current=self.research.control("power_override",value=15,actor="cms-admin:2")
        expiry=current["config"]["power_override_until"]
        self.assertGreater(expiry,time.time())
        self.assertIsNone(self.research.claim(now=expiry))
        self.assertEqual(admission_reasons(current["config"],free_mib=999,load=0,
                                           hour=23,now=expiry-1),[])
        with connect(self.db) as con:
            con.execute("INSERT INTO jobs(id,messages,target,state,created) VALUES('chat','[]','auto','queued',?)",(time.time(),))
            self.assertIn("cms-admin:2",con.execute("SELECT detail FROM cr_events WHERE kind='power-override'").fetchone()[0])
        self.assertIsNone(self.research.claim())
        self.research.control("pause")
        self.assertEqual(self.research.status()["config"]["power_override_until"],0)
        with connect(self.db) as con:
            con.execute("UPDATE jobs SET state='completed' WHERE id='chat'")
        self.research.control("resume")
        self.research.control("power_override",value=30,actor="cms-admin:2")
        self.assertIsNotNone(self.research.claim())
        cleared=self.research.control("power_override",value=0,actor="cms-admin:2")
        self.assertIn("daily-energy-unknown",cleared["admission_blocks"])

    def test_racing_energy_commit_defers_admission(self):
        from continuous_research import daily_energy
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"daily_energy_wh":200,"research_watts":100})
        self.research.control("enable")
        def race(reader,now):
            before=daily_energy(reader,now)
            with connect(self.db) as writer:
                writer.execute("""INSERT INTO cr_energy_attempts(attempt_id,started,watts,rate_microusd)
                    VALUES('concurrent',?,100,-1)""",(now,))
            return before
        with patch("continuous_research.daily_energy",side_effect=race):
            self.assertIsNone(self.research.claim())
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_jobs WHERE state='leased'").fetchone()[0],0)

    def test_daily_budget_defers_when_attempt_coverage_is_incomplete(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"daily_energy_wh":200,"research_watts":100})
        self.research.control("enable")
        with connect(self.db) as db:
            db.execute("""INSERT INTO cr_energy_attempts(attempt_id,started,watts,rate_microusd)
                VALUES('interrupted',?,100,-1)""",(time.time(),))
        self.assertIsNone(self.research.claim())
        self.assertIn("daily-energy-unknown",self.research.status()["admission_blocks"])

    def test_failed_unknown_free_and_interrupted_energy_attempts(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        def offline(*args):
            raise RuntimeError("worker offline")
        self.research.run_once(offline)
        stats=self.research.status()["energy_7d"]
        self.assertEqual((stats["errors"],stats["timed"],stats["priced_samples"]),(1,1,0))
        self.assertIsNone(stats["kwh"])
        self.assertIsNone(stats["usd"])
        with connect(self.db) as db:
            db.execute("UPDATE cr_jobs SET retry_at=0")
        self.research.control("configure",value={"research_watts":100,"rate_microusd_kwh":0})
        lease=self.research.claim()
        self.assertTrue(self.research._start_energy_attempt(lease))
        self.assertFalse(self.research._start_energy_attempt(lease))
        # Reopening interrupted work preserves its unknown duration.
        restarted=ContinuousResearch(self.db,self.pdf,self.base/"index.sqlite3")
        stats=restarted.status()["energy_7d"]
        self.assertEqual((stats["attempts"],stats["timed"]),(2,1))
        self.assertIsNone(stats["usd"])
        restarted._end_energy_attempt(lease["token"],3600000,"error")
        restarted._end_energy_attempt(lease["token"],1,"returned")
        stats=restarted.status()["energy_7d"]
        self.assertEqual((stats["errors"],stats["priced_samples"],stats["usd"]),(2,1,0))
        self.assertAlmostEqual(stats["kwh"],0.1)

    def test_unpriced_energy_and_rolling_retention(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"research_watts":250})
        self.research.control("enable")
        self.research.run_once(self.run_result)
        summary=self.research.status()["energy_7d"]
        self.assertGreater(summary["kwh"],0)
        self.assertIsNone(summary["usd"])
        self.research.sample_resources(now=time.time()+8*86400)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_energy_attempts").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_results").fetchone()[0],1)
        for field, value in (("rate_microusd_kwh",-2),("rate_microusd_kwh",10000001),
                             ("research_watts",True),("research_watts",100001)):
            with self.assertRaises(ValueError):
                self.research.control("configure",value={field:value})

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

    def test_measured_token_counts_per_pass_job_and_unknown_legacy(self):
        self.add_doc()
        self.research.inventory()
        self.research.control("configure",value={"passes":2})
        self.research.control("enable")
        def with_counts(messages,target,model_id,job_id):
            return "Original quote is unverified", "cpu", {
                "prompt_tokens":110,"completion_tokens":25,"generation_tps":12.5}
        self.assertTrue(self.research.run_once(with_counts))
        self.assertTrue(self.research.run_once(self.run_result))
        status=self.research.status()["throughput"]
        self.assertEqual(status["prompt_tokens"],110)
        self.assertEqual(status["completion_tokens"],25)
        self.assertEqual(status["prompt_token_samples"],1)
        self.assertEqual(status["completion_token_samples"],1)
        self.assertGreater(status["observed_output_tps_inference_wall"],0)
        history=self.research.history("pdf:"+SHA)
        job=history["jobs"][0]
        self.assertEqual(job["prompt_tokens"],110)
        self.assertEqual(job["completion_tokens"],25)
        self.assertGreater(job["observed_output_tps_inference_wall"],0)
        rows=sorted(history["passes"],key=lambda x:x["pass_number"])
        self.assertEqual(rows[0]["completion_tokens"],25)
        self.assertEqual(rows[0]["generation_tps"],12.5)
        self.assertIsNone(rows[1]["prompt_tokens"])
        self.assertIsNone(rows[1]["generation_tps"])
        with connect(self.db) as db:
            db.execute("UPDATE cr_results SET prompt_tokens=NULL,completion_tokens=NULL, generation_tps=NULL")
        status=self.research.status()["throughput"]
        self.assertIsNone(status["observed_output_tps_inference_wall"])
        self.assertIsNone(status["prompt_tokens"])

    def test_token_metrics_reject_guesses_and_invalid_counts(self):
        self.assertEqual(ai.model_token_metrics({"choices":[]}),{
            "prompt_tokens":None,"completion_tokens":None,"generation_tps":None})
        values=ai.model_token_metrics({"usage":{"prompt_tokens":42,"completion_tokens":20},
             "timings":{"predicted_per_second":11.2}})
        self.assertEqual(values,{"prompt_tokens":42,"completion_tokens":20,
                                  "generation_tps":11.2})
        self.assertEqual(ai.model_token_metrics(
            {"usage":{"completion_tokens":True},
             "timings":{"predicted_per_second":float('nan')}}),
             {"prompt_tokens":None,"completion_tokens":None,"generation_tps":None})
        self.add_doc()
        self.research.inventory()
        self.research.control("enable")
        lease=self.research.claim()
        self.assertIsNotNone(lease)
        with self.assertRaises(ValueError):
            self.research.finish(lease,"draft",duration_ms=1,
                token_metrics={"completion_tokens":True})
        with self.assertRaises(ValueError):
            self.research.finish(lease,"draft",duration_ms=1,
                token_metrics={"generation_tps":float('inf')})
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cr_results").fetchone()[0],0)

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
