import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "db_queue.py"
spec = importlib.util.spec_from_file_location("sirus_db_queue", MODULE)
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)


class PrivilegeSeparatedQueueTests(unittest.TestCase):
    def test_unprivileged_queue_actions_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "cms.sqlite3"
            with sqlite3.connect(file) as db:
                db.execute("""CREATE TABLE cms_source_controller_health(
                    host TEXT PRIMARY KEY,last_seen INTEGER,state TEXT)""")
                db.execute("""CREATE TABLE cms_source_deploy_requests(
                    id TEXT PRIMARY KEY,plan_id TEXT,host TEXT,action TEXT,
                    state TEXT,created_at INTEGER,updated_at INTEGER,
                    error TEXT,result_json TEXT)""")
                db.execute("""INSERT INTO cms_source_deploy_requests
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("a"*32,"b"*32,"ubuntu-primary","apply","running",1,1,"","{}"))
            db = helper.connect(file)
            try:
                helper.do_action(db,"recover")
                self.assertEqual(db.execute(
                    "SELECT state FROM cms_source_deploy_requests").fetchone()[0],
                    "interrupted")
                helper.heartbeat(db, now=123)
                self.assertEqual(db.execute(
                    "SELECT last_seen FROM cms_source_controller_health").fetchone()[0],123)
                db.execute("INSERT INTO cms_source_deploy_requests VALUES(?,?,?,?,?,?,?,?,?)",
                           ("c"*32,"d"*32,"ubuntu-primary","apply","queued",2,2,"","{}"))
                request=helper.do_action(db,"next")["request"]
                self.assertEqual(request["id"],"c"*32)
                self.assertEqual(db.execute(
                    "SELECT state FROM cms_source_deploy_requests WHERE id=?",
                    ("c"*32,)).fetchone()[0],"running")
                helper.do_action(db,"finish",request_id=request["id"],state="done",
                                 result={"state":"verified"})
                state=db.execute("SELECT state,result_json FROM cms_source_deploy_requests WHERE id=?",
                                 ("c"*32,)).fetchone()
                self.assertEqual(state[0],"done")
                self.assertIn("verified",state[1])
                with self.assertRaises(ValueError):
                    helper.do_action(db,"finish",request_id="../../etc/passwd",
                                     state="done",result={})
                with self.assertRaises(ValueError):
                    helper.do_action(db,"execute arbitrary command")
            finally:
                db.close()


if __name__=="__main__":
    unittest.main()
