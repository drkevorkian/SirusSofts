#!/usr/bin/env python3
"""Offline Gentoo PDF ingestion regression suite (no Poppler or SSD required)."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "pdf_ingest.py"
spec = importlib.util.spec_from_file_location("pdf_ingest", MODULE)
ing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ing)


class GentooPdfIngestTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        ing.require_storage(self.root, strict=False)
        self.db = ing.connect(self.root)
        self.addCleanup(self.db.close)
        ing.schema(self.db)
        self.mount = mock.patch.object(ing, "require_storage", side_effect=self._check)
        self.mount.start()
        self.addCleanup(self.mount.stop)

    def _check(self, path, strict=True):
        if path != self.root:
            raise RuntimeError("Wrong test storage directory")
        return None

    def pdf(self, name="one.pdf", content=None):
        content = content or (b"%PDF-1.4\n" + b"sample simulated pages\n" * 4)
        file = self.root / "manuals/inbox" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        os.utime(file, (time.time() - 120, time.time() - 120))
        return file

    def stable_scan(self):
        first = ing.scan(self.root, self.db)
        self.assertEqual(first["imported_or_deduplicated"], 0)
        self.db.execute("UPDATE seen SET first_seen=?",
                        (time.time() - ing.STABLE_SECONDS - 1,))
        return ing.scan(self.root, self.db)

    def test_no_partial_processing_and_deduplicate_identical_files(self):
        first = self.pdf()
        self.assertEqual(self.stable_scan()["imported_or_deduplicated"], 1)
        self.assertFalse(first.exists())
        row = self.db.execute("SELECT * FROM documents").fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertFalse(row["reviewed"])
        self.assertTrue((self.root / row["object_rel"]).is_file())
        self.pdf("second.pdf")
        self.assertEqual(self.stable_scan()["imported_or_deduplicated"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        self.assertEqual(len(list(self.root.glob("manuals/objects/*/*.pdf"))), 1)

    def test_source_change_keeps_original_unmodified(self):
        file = self.pdf()
        ing.scan(self.root, self.db)
        file.write_bytes(file.read_bytes() + b"later")
        self.assertEqual(ing.scan(self.root, self.db)["imported_or_deduplicated"], 0)
        self.assertTrue(file.exists())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_symlinks_ignored(self):
        destination = self.pdf()
        (self.root / "manuals/inbox/link.pdf").symlink_to(destination)
        found = ing.scan(self.root, self.db)
        self.assertEqual(found["pdfs_found"], 1)
        self.assertTrue((self.root / "manuals/inbox/link.pdf").is_symlink())

    def test_claim_is_exclusive_and_restart_recovery(self):
        self.pdf()
        self.stable_scan()
        claimed = ing.claim(self.db)
        self.assertEqual(len(claimed), 64)
        self.assertIsNone(ing.claim(self.db))
        self.assertEqual(self.db.execute("SELECT status FROM documents").fetchone()[0],
                         "processing")
        self.db.execute("UPDATE documents SET status='queued' WHERE status='processing'")
        self.assertEqual(ing.claim(self.db), claimed)
        self.assertEqual(self.db.execute("SELECT attempts FROM documents").fetchone()[0], 2)

    def test_review_gate_remains_closed_after_extraction(self):
        self.pdf()
        self.stable_scan()
        digest = ing.claim(self.db)
        def fake_run(args, **kwargs):
            if args[0] == "pdfinfo":
                return mock.Mock(stdout=b"Pages: 2\n")
            self.assertEqual(args[0], "pdftotext")
            Path(args[-1]).write_text("Torque: 30 Nm\fInspection interval: 50h\f")
            return mock.Mock()
        with mock.patch.object(ing.subprocess, "run", side_effect=fake_run):
            ing.process_one(self.root, digest)
        row = self.db.execute("SELECT * FROM documents").fetchone()
        self.assertEqual((row["status"], row["reviewed"]), ("ready", 0))
        chunks = self.db.execute("SELECT page,text FROM excerpts ORDER BY page").fetchall()
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["page"], 1)
        self.assertEqual(chunks[1]["page"], 2)
        self.assertEqual(row["pages"], 2)

    def test_bad_pdf_goes_failed_and_does_not_approve(self):
        self.pdf()
        self.stable_scan()
        digest = ing.claim(self.db)
        with mock.patch.object(ing.subprocess, "run", side_effect=RuntimeError("broken PDF")):
            ing.process_one(self.root, digest)
        row = self.db.execute("SELECT status,reviewed,error FROM documents").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["reviewed"], 0)
        self.assertIn("broken PDF", row["error"])

    def test_settings_range(self):
        self.assertEqual(ing.worker_count(self.db), 3)
        self.db.execute("UPDATE settings SET value='6' WHERE key='workers'")
        self.assertEqual(ing.worker_count(self.db), 6)
        for invalid in ("0", "2", "65"):
            self.db.execute("UPDATE settings SET value=? WHERE key='workers'", (invalid,))
            with self.assertRaises(RuntimeError):
                ing.worker_count(self.db)

    def test_no_root_fallback(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            root = Path(elsewhere)
            with self.assertRaisesRegex(RuntimeError, "NOT mounted"):
                original = self.mount
                original.stop()
                try:
                    ing.require_storage(root)
                finally:
                    original.start()

    def test_split_page_preserves_text(self):
        text = ("a long maintenance paragraph\n" * 250).strip()
        pieces = list(ing.split_page(text))
        self.assertGreater(len(pieces), 1)
        self.assertLessEqual(max(map(len, pieces)), 4500)


if __name__ == "__main__":
    unittest.main()
