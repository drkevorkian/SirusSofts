import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import types
import unittest

EXECUTOR=Path(__file__).resolve().parents[1]/"source_executor.py"
spec=importlib.util.spec_from_file_location("sirus_executor",EXECUTOR)
service=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=service
spec.loader.exec_module(service)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeCommand:
    def __init__(self, bad_at=None):
        self.calls=[]
        self.bad_at=bad_at

    def __call__(self, command, **kwargs):
        self.calls.append(tuple(command))
        fail=self.bad_at is not None and len(self.calls)==self.bad_at
        return types.SimpleNamespace(returncode=1 if fail else 0)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.src=self.root/"running.py"
        self.stage=self.root/"staged"
        self.stage.mkdir()
        self.backups=self.root/"backups"
        self.previous=b"value = 1\n"
        self.updated=b"value = 2\n"
        self.src.write_bytes(self.previous)
        self.ident="a"*32
        self.commit="b"*40
        self.path="gentoo-node/client.py"
        self.baseline=sha(self.previous)
        self.target=sha(self.updated)
        self.db=self.root/"cms.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("""CREATE TABLE cms_source_release_plans(
                 id TEXT,host TEXT,source_path TEXT,commit_sha TEXT,
                 target_sha256 TEXT,baseline_sha256 TEXT,state TEXT,approved_at INTEGER)""")
            db.execute("INSERT INTO cms_source_release_plans VALUES(?,?,?,?,?,?,?,?)",
                       (self.ident,"gentoo-home",self.path,self.commit,
                        self.target,self.baseline,"approved",123))
        self.metadata={"id":self.ident,"host":"gentoo-home","path":self.path,
                       "commit":self.commit,"target_sha256":self.target,
                       "baseline_sha256":self.baseline,"staged_at":123,
                       "ready_for_manual_review":True}
        (self.stage/(self.ident+".json")).write_text(json.dumps(self.metadata))
        (self.stage/(self.ident+".source")).write_bytes(self.updated)
        self.saved=service.ALLOWED["gentoo-home"].copy()
        service.ALLOWED["gentoo-home"][self.path]=(
            str(self.src),("rc-service","test-node","restart"),
            ("rc-service","test-node","status"))
        self.addCleanup(lambda: service.ALLOWED.__setitem__("gentoo-home", self.saved))

    def load(self):
        return service.load_release("gentoo-home",self.ident,staged_root=self.stage,
                                    confirm_approval=False,now=124)

    def test_preflight_reads_only_and_never_restores_or_executes(self):
        rel=self.load()
        check=FakeCommand()
        value=service.preflight(rel,backup_root=self.backups,run=check,
                                free_bytes=1024**3)
        self.assertTrue(value["eligible"])
        self.assertEqual(self.src.read_bytes(),self.previous)
        self.assertEqual(check.calls,[("rc-service","test-node","status")])
        self.assertFalse(self.backups.exists())

    def test_rejects_baseline_drift_and_bad_stage(self):
        rel=self.load()
        self.src.write_text("value = 9\n")
        with self.assertRaisesRegex(service.ReleaseError,"drifted"):
            service.preflight(rel,backup_root=self.backups,run=FakeCommand(),
                              free_bytes=1024**3)
        self.src.write_bytes(self.previous)
        (self.stage/(self.ident+".source")).write_text("value = 888\n")
        with self.assertRaisesRegex(service.ReleaseError,"hash"):
            self.load()

    def test_rejects_unknown_component_and_symlink(self):
        self.metadata["path"]="../client.py"
        (self.stage/(self.ident+".json")).write_text(json.dumps(self.metadata))
        with self.assertRaises(service.ReleaseError):
            self.load()
        self.metadata["path"]=self.path
        (self.stage/(self.ident+".json")).write_text(json.dumps(self.metadata))
        (self.stage/(self.ident+".source")).unlink()
        (self.stage/(self.ident+".source")).symlink_to(self.src)
        with self.assertRaises(service.ReleaseError):
            self.load()

    def test_ubuntu_must_match_approved_db(self):
        # Check the exact CMS signature, not just a staged local manifest.
        fields=dict(self.metadata)
        fields["host"]="gentoo-home"
        service.check_approval(self.db,fields,now=124)
        fields["target_sha256"]="f"*64
        with self.assertRaisesRegex(service.ReleaseError,"does not match"):
            service.check_approval(self.db,fields,now=124)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE cms_source_release_plans SET state='canceled'")
        fields["target_sha256"]=self.target
        with self.assertRaisesRegex(service.ReleaseError,"not currently approved"):
            service.check_approval(self.db,fields,now=124)

    def test_successful_apply_and_rollback_without_staged_manifest(self):
        rel=self.load()
        commands=FakeCommand()
        result=service.apply(rel,backup_root=self.backups,run=commands)
        self.assertEqual(result["state"],"verified")
        self.assertEqual(self.src.read_bytes(),self.updated)
        self.assertEqual((self.backups/self.ident/"previous.source").read_bytes(),self.previous)
        # Deleting the original staging proves recovery uses the root-owned journal.
        for path in self.stage.iterdir(): path.unlink()
        undo=service.release_from_backup("gentoo-home",self.ident,
                                         backup_root=self.backups)
        result=service.rollback(undo,backup_root=self.backups,run=FakeCommand())
        self.assertEqual(result["state"],"rolled_back")
        self.assertEqual(self.src.read_bytes(),self.previous)

    def test_interrupted_release_can_be_restored_without_original_staging(self):
        rel=self.load()
        service.apply(rel,backup_root=self.backups,run=FakeCommand())
        journal=self.backups/self.ident/"journal.json"
        data=json.loads(journal.read_text())
        data["state"]="installed_unverified"
        journal.write_text(json.dumps(data))
        for item in self.stage.iterdir(): item.unlink()
        recovered=service.release_from_backup("gentoo-home",self.ident,
                                               backup_root=self.backups)
        result=service.rollback(recovered,backup_root=self.backups,run=FakeCommand())
        self.assertEqual(result["state"],"rolled_back")
        self.assertEqual(self.src.read_bytes(),self.previous)

    def test_failed_restart_automatically_rolls_back(self):
        rel=self.load()
        commands=FakeCommand(bad_at=2)
        with self.assertRaisesRegex(service.ReleaseError,"rollback"):
            service.apply(rel,backup_root=self.backups,run=commands)
        self.assertEqual(self.src.read_bytes(),self.previous)
        record=json.loads((self.backups/self.ident/"journal.json").read_text())
        self.assertEqual(record["state"],"rolled_back")

    def test_refuses_unavailable_space_and_invalid_python(self):
        rel=self.load()
        with self.assertRaisesRegex(service.ReleaseError,"512 MiB"):
            service.preflight(rel,backup_root=self.backups,run=FakeCommand(),
                              free_bytes=10)
        self.updated=b"invalid syntax ???\n"
        self.target=sha(self.updated)
        (self.stage/(self.ident+".source")).write_bytes(self.updated)
        self.metadata["target_sha256"]=self.target
        (self.stage/(self.ident+".json")).write_text(json.dumps(self.metadata))
        rel=self.load()
        with self.assertRaisesRegex(service.ReleaseError,"syntax"):
            service.preflight(rel,backup_root=self.backups,run=FakeCommand(),
                              free_bytes=1024**3)


if __name__=="__main__":
    unittest.main()
