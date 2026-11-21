"""Consistent, offline-only, no-delete private research backup tests."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import input_server as ai
from research_archive import archive_snapshot, verify_snapshot, restore_into_new_file


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.db=self.base/"input.sqlite3"
        self.archive=self.base/"archives"
        ai.init_db(self.db)
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO cr_events(happened,kind,subject,detail)
                VALUES(123,'job-complete','test-job','')""")
            db.execute("""INSERT INTO cr_results(job_id,pass_number,notes,sha256,
                review_state,created,duration_ms,output_chars)
                VALUES('job1',1,'private model draft','hash','unreviewed',123,15,19)""")
        self.original_size=self.db.stat().st_size

    def test_archive_consistent_wal_with_immutable_events_and_recovery_copy(self):
        # WAL contents that have not been checkpointed are included in the
        # sqlite3 backup; the original DB remains untouched and available.
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""INSERT INTO cr_events(happened,kind,subject,detail)
                VALUES(124,'job-error','failed','RuntimeError')""")
            db.commit()
            created=archive_snapshot(self.db,self.archive)
            self.assertTrue(created["verified"])
            self.assertEqual(created["tables"]["cr_events"],2)
            self.assertEqual(created["tables"]["cr_results"],1)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_events").fetchone()[0],2)
            file=self.archive/created["archive"]
            self.assertEqual(file.stat().st_mode & 0o777,0o600)
            self.assertEqual(self.archive.stat().st_mode & 0o777,0o700)
            self.assertEqual((self.archive/(file.name+".json")).stat().st_mode&0o777,
                             0o600)
            self.assertEqual(verify_snapshot(file)["sha256"],created["sha256"])
            output=self.base/"recovery"
            output.mkdir(mode=0o700)
            recovered=restore_into_new_file(file,output/"new.sqlite3")
            self.assertTrue(recovered["verified"])
            self.assertFalse(recovered["replaced_existing"])
            with sqlite3.connect(output/"new.sqlite3") as restored:
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0],
                                 "ok")
                self.assertEqual(restored.execute(
                    "SELECT count(*) FROM cr_events").fetchone()[0],2)
                self.assertEqual(restored.execute(
                    "SELECT notes FROM cr_results").fetchone()[0],
                    "private model draft")
                with self.assertRaises(sqlite3.IntegrityError):
                    restored.execute("DELETE FROM cr_events")
            self.assertEqual(self.db.stat().st_size,self.original_size)
            with self.assertRaises(ValueError):
                restore_into_new_file(file,output/"new.sqlite3")

    def test_verify_rejects_corruption_wrong_manifest_and_missing_file(self):
        archived=archive_snapshot(self.db,self.archive)
        file=self.archive/archived["archive"]
        manifest=self.archive/(file.name+".json")
        original=manifest.read_text()
        descriptor=json.loads(original)
        descriptor["tables"]["cr_events"]+=1
        manifest.write_text(json.dumps(descriptor))
        with self.assertRaisesRegex(ValueError,"row counts"):
            verify_snapshot(file)
        manifest.write_text(original)
        self.assertTrue(verify_snapshot(file)["verified"])
        with file.open("ab") as out:
            out.write(b"damaged")
        with self.assertRaisesRegex(ValueError,"hash or size"):
            verify_snapshot(file)
        with self.assertRaises(ValueError):
            verify_snapshot(self.archive/"does-not-exist.sqlite3")

    def test_private_permissions_and_symlinks_fail_closed(self):
        self.archive.mkdir(mode=0o700)
        alias=self.base/"source-link.sqlite3"
        alias.symlink_to(self.db)
        with self.assertRaises(ValueError):
            archive_snapshot(alias,self.archive)
        self.archive.chmod(0o755)
        with self.assertRaises(ValueError):
            archive_snapshot(self.db,self.archive)
        self.archive.chmod(0o700)
        archived=archive_snapshot(self.db,self.archive)
        file=self.archive/archived["archive"]
        file.chmod(0o644)
        with self.assertRaisesRegex(ValueError,"mode 0600"):
            verify_snapshot(file)
        file.chmod(0o600)
        private_link=self.archive/"research-20260101000000-0000000000000000.sqlite3"
        private_link.symlink_to(file)
        with self.assertRaises(ValueError):
            verify_snapshot(private_link)

    def test_two_archives_are_distinct_no_prune_no_overwrite(self):
        first=archive_snapshot(self.db,self.archive,now=1234567890)
        second=archive_snapshot(self.db,self.archive,now=1234567890)
        self.assertNotEqual(first["archive"],second["archive"])
        self.assertEqual(len(list(self.archive.glob("*.sqlite3"))),2)
        self.assertTrue(all(verify_snapshot(f)["verified"] for f in
                            self.archive.glob("*.sqlite3")))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_events").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM cr_results").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
