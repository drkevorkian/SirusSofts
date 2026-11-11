import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
import sys

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location("ubuntu_controller",BASE/"ubuntu_controller.py")
controller=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=controller
spec.loader.exec_module(controller)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=controller.connect(self.make_db())

    def make_db(self):
        db_path=Path(self.temp.name)/"cms.sqlite3"
        with sqlite3.connect(db_path) as db:
            db.execute("""CREATE TABLE cms_source_deploy_requests(
                id TEXT,plan_id TEXT,host TEXT,action TEXT,state TEXT,
                created_at INTEGER,updated_at INTEGER,error TEXT,result_json TEXT)""")
        return db_path

    def insert(self,ident,host="ubuntu-primary",action="apply",state="queued"):
        self.db.execute("""INSERT INTO cms_source_deploy_requests VALUES
               (?,?,?,?,?,1,1,'','{}')""",(ident,ident*2,host,action,state))

    def test_claim_is_host_specific_and_claim_is_atomic(self):
        self.insert("a",host="gentoo-home")
        self.insert("b")
        item=controller.claim(self.db,now=10)
        self.assertEqual(item["id"],"b")
        self.assertEqual(self.db.execute(
            "SELECT state FROM cms_source_deploy_requests WHERE id='a'").fetchone()[0],"queued")
        self.assertIsNone(controller.claim(self.db,now=11))

    def test_success_is_persisted_once(self):
        self.insert("c")
        calls=[]
        def fake_loader(host,ident):
            calls.append((host,ident))
            return "release"
        result=controller.process_one(self.db,loader=fake_loader,
           installer=lambda release:{"state":"verified","path":"worker"},
           now=10)
        self.assertEqual(result,"done")
        self.assertEqual(calls,[("ubuntu-primary","cc")])
        row=self.db.execute("SELECT state,result_json FROM cms_source_deploy_requests").fetchone()
        self.assertEqual(row[0],"done")
        self.assertIn("verified",row[1])
        self.assertIsNone(controller.process_one(self.db,loader=fake_loader,now=11))

    def test_failed_release_has_safe_error(self):
        self.insert("d")
        def failed(*_):
            raise RuntimeError("private token unexpectedly included in an error")
        result=controller.process_one(self.db,loader=failed,now=20)
        self.assertEqual(result,"failed")
        row=self.db.execute("SELECT state,error FROM cms_source_deploy_requests").fetchone()
        self.assertEqual(row[0],"failed")
        self.assertNotIn("private token",row[1])

    def test_rollback_calls_verified_backup_recovery(self):
        self.insert("e",action="rollback")
        calls=[]
        result=controller.process_one(self.db,
            restorer=lambda host,ident:(host,ident),
            undo=lambda release:{"state":"rolled_back","previous":release[1]},
            now=40)
        self.assertEqual(result,"done")
        self.assertEqual(self.db.execute("SELECT state FROM cms_source_deploy_requests").fetchone()[0],"done")

    def test_interrupted_release_is_not_automatically_retried(self):
        self.insert("f",state="running")
        controller.recover_interrupted(self.db,now=55)
        row=self.db.execute("SELECT state,error FROM cms_source_deploy_requests").fetchone()
        self.assertEqual(row[0],"interrupted")
        self.assertIn("operator review",row[1])
        self.assertIsNone(controller.claim(self.db,now=60))


if __name__=="__main__":
    unittest.main()
