import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/"worker.py"
spec=importlib.util.spec_from_file_location("sirus_ubuntu_worker",FILE)
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class WorkTests(unittest.TestCase):
    def test_claim_run_and_status_in_shared_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(str(Path(d)/"jobs.sqlite3"),isolation_level=None)
            db.row_factory=sqlite3.Row
            worker.ensure_schema(db)
            db.execute("""INSERT INTO work_jobs(request_id,node_id,kind,state,created_at,updated_at)
                      VALUES(?,?,?,'queued',?,?)""",("a"*32,"ubuntu-primary","health_snapshot",100,100))
            with patch.object(worker,"health_snapshot",return_value={"load_1":0.4}):
                self.assertEqual(worker.process_one(db,100),"done")
            got=db.execute("SELECT * FROM work_jobs").fetchone()
            self.assertEqual(got["state"],"done")
            self.assertIn('"load_1":0.4',got["result_json"])
            self.assertEqual(got["attempts"],1)
            self.assertIsNone(worker.process_one(db,101))
            db.close()

    def test_unknown_task_fails_not_executes(self):
        with self.assertRaises(ValueError):
            worker.execute("run_shell")
        with self.assertRaises(ValueError):
            worker.execute("install_packages")

    def test_failed_job_does_not_crash_runner(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(str(Path(d)/"jobs.sqlite3"),isolation_level=None)
            db.row_factory=sqlite3.Row
            worker.ensure_schema(db)
            db.execute("""INSERT INTO work_jobs(request_id,node_id,kind,state,created_at,updated_at)
                      VALUES(?,?,?,'queued',?,?)""",("b"*32,"ubuntu-primary","site_health",100,100))
            with patch.object(worker,"site_health",side_effect=OSError("Network down")):
                self.assertEqual(worker.process_one(db,100),"failed")
            got=db.execute("SELECT state,error FROM work_jobs").fetchone()
            self.assertEqual(got["state"],"failed")
            self.assertNotIn("Network down",got["error"])

    def test_ubuntu_never_claims_gentoo_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(str(Path(d)/"jobs.sqlite3"),isolation_level=None)
            db.row_factory=sqlite3.Row
            worker.ensure_schema(db)
            db.execute("""INSERT INTO work_jobs(request_id,node_id,kind,state,created_at,updated_at)
                      VALUES(?,?,?,'queued',?,?)""", ("a"*32,"gentoo-home","package_update_check",100,100))
            with patch.object(worker,"execute",side_effect=AssertionError("Wrong host")):
                self.assertIsNone(worker.process_one(db,100))
            self.assertEqual(db.execute("SELECT state FROM work_jobs").fetchone()[0],"queued")
            db.close()

    def test_apt_preview_is_only_simulated(self):
        from types import SimpleNamespace
        with patch.object(worker.subprocess,"run",return_value=SimpleNamespace(
                returncode=0, stdout="Inst abc [1] (2 Ubuntu)\\nConf abc (2 Ubuntu)\\n",
                stderr="")) as command:
            info=worker.package_update_check()
        self.assertTrue(info["simulation_only"])
        self.assertEqual(info["upgrade_count"],1)
        self.assertEqual(command.call_args.args[0][:2],["/usr/bin/apt-get","-s"])

    def test_no_redirect_to_other_hostname(self):
        self.assertIsNone(worker.NoRedirect().redirect_request(None,None,302,"redirect",{},"http://bad/"))

if __name__=="__main__":unittest.main()
