"""Regression tests for persistent read-only WAL access on writer shutdown."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_wal_guard import WalGuard, grant_reader_sidecars, open_persistent_wal


class PersistentWalTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name) / "pdf_ingest.sqlite3"

    def sidecars(self):
        return [Path(str(self.path) + suffix) for suffix in ("-wal", "-shm")]

    def test_reader_without_directory_write_after_writer_closes(self):
        db = open_persistent_wal(self.path)
        db.execute("CREATE TABLE documents(sha TEXT)")
        db.execute("INSERT INTO documents VALUES('test-pdf')")
        db.close()
        self.assertTrue(all(p.is_file() for p in self.sidecars()))
        directory = self.path.parent
        directory.chmod(0o500)
        try:
            reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            try:
                self.assertEqual(
                    reader.execute("SELECT sha FROM documents").fetchone()[0],
                    "test-pdf")
            finally:
                reader.close()
            self.assertTrue(all(p.is_file() for p in self.sidecars()))
        finally:
            directory.chmod(0o700)

    def test_repeated_writes_retain_sidecars(self):
        db = open_persistent_wal(self.path)
        db.execute("CREATE TABLE t(n INTEGER)")
        db.close()
        for n in range(3):
            db = open_persistent_wal(self.path)
            db.execute("INSERT INTO t VALUES(?)", (n,))
            db.close()
            self.assertTrue(all(p.is_file() for p in self.sidecars()))
            reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            self.assertEqual(reader.execute("SELECT count(*) FROM t").fetchone()[0], n + 1)
            reader.close()

    def test_other_sqlite_writer_closes_before_guarded_writer(self):
        # PDF admin and ingestion may hold separate connections. Closing the
        # admin writer must not remove sidecars while ingestion is active.
        guarded = open_persistent_wal(self.path)
        try:
            guarded.execute("CREATE TABLE t(n INTEGER)")
            admin = sqlite3.connect(self.path)
            try:
                admin.execute("INSERT INTO t VALUES(42)")
                admin.commit()
            finally:
                admin.close()
            self.assertTrue(all(p.is_file() for p in self.sidecars()))
        finally:
            guarded.close()
        self.assertTrue(all(p.is_file() for p in self.sidecars()))
        reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(reader.execute("SELECT n FROM t").fetchone()[0], 42)
        finally:
            reader.close()

    def test_guard_does_not_silently_accept_non_wal_database(self):
        # Guarding an existing rollback-journal database is an explicit
        # migration; open_persistent_wal must enable WAL before returning.
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE t(n INTEGER)")
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        guarded = open_persistent_wal(self.path)
        try:
            self.assertEqual(guarded.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            guarded.close()
        self.assertTrue(all(p.is_file() for p in self.sidecars()))

    def test_open_rejects_symlink_before_sqlite_can_write_target(self):
        target = self.path.parent / "original.sqlite3"
        with sqlite3.connect(target) as db:
            db.execute("CREATE TABLE original(n INTEGER)")
            db.execute("INSERT INTO original VALUES(7)")
        original_bytes = target.read_bytes()
        self.path.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "not a symlink"):
            open_persistent_wal(self.path)
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertFalse(any(p.exists() for p in self.sidecars()))

    def test_open_rejects_directory_before_sqlite_connect(self):
        self.path.mkdir()
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            open_persistent_wal(self.path)

    def test_guard_refuses_missing_or_symlinked_database(self):
        with self.assertRaisesRegex(RuntimeError, "existing regular"):
            WalGuard(self.path)
        target = self.path.parent / "real.sqlite3"
        sqlite3.connect(target).close()
        self.path.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "existing regular"):
            WalGuard(self.path)

    def test_native_close_failure_retains_handle_for_retry(self):
        # SQLite can return SQLITE_BUSY without destroying its native handle.
        # The Python connection must retain that handle for a later retry.
        db = open_persistent_wal(self.path)
        db.execute("CREATE TABLE t(n INTEGER)")
        guard = db._wal_guard
        original_handle = guard._handle.value
        with patch.object(guard._lib, "sqlite3_close", return_value=5) as native_close:
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                db.close()
            native_close.assert_called_once()
        self.assertEqual(guard._handle.value, original_handle)
        self.assertIs(db._wal_guard, guard)
        db.close()
        self.assertFalse(guard._handle.value)
        self.assertIsNone(db._wal_guard)
        self.assertTrue(all(p.is_file() for p in self.sidecars()))

    def test_scoped_read_acl_never_grants_write(self):
        db = open_persistent_wal(self.path)
        db.execute("CREATE TABLE t(n)")
        db.close()
        with patch("pdf_wal_guard.pwd.getpwnam", return_value=object()), \
             patch("pdf_wal_guard.subprocess.run") as execute:
            self.assertTrue(grant_reader_sidecars(self.path))
        self.assertEqual(execute.call_count, 2)
        for call, sidecar in zip(execute.call_args_list, self.sidecars()):
            self.assertEqual(
                call.args[0],
                ("setfacl", "-m", "u:sirusai:r--", os.fspath(sidecar)))

    def test_pdf_ingest_works_before_ai_account_exists(self):
        with patch("pdf_wal_guard.pwd.getpwnam", side_effect=KeyError):
            self.assertFalse(grant_reader_sidecars(self.path))


if __name__ == "__main__":
    unittest.main()
