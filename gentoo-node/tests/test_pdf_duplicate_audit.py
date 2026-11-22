"""Synthetic, offline read-only PDF duplicate inventory and safety checks."""
import hashlib
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'pdf_duplicate_audit.py'
spec = importlib.util.spec_from_file_location('pdf_duplicate_audit', MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class DuplicateAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'databases').mkdir()
        (self.root / 'manuals' / 'inbox').mkdir(parents=True)
        self.db = sqlite3.connect(self.root / 'databases' / 'pdf_ingest.sqlite3')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE documents(sha256 TEXT, filename TEXT, object_rel TEXT,
                                   bytes INTEGER, status TEXT);
            CREATE TABLE events(id INTEGER PRIMARY KEY, sha256 TEXT,
                                kind TEXT, detail TEXT);
        ''')
        self.data = b'%PDF-1.4\nidentical contents\n'
        self.digest = hashlib.sha256(self.data).hexdigest()

    def canonical(self, digest=None, filename='primary.pdf'):
        self.db.execute('INSERT INTO documents VALUES (?,?,?,?,?)',
                        (digest or self.digest, filename, 'manuals/objects/ab/object.pdf',
                         len(self.data), 'ready'))
        self.db.commit()

    def staged(self, name, data=None):
        path = self.root / 'manuals' / 'inbox' / name
        path.write_bytes(self.data if data is None else data)
        return path

    def test_historical_hash_duplicate_and_original_do_not_reappear_as_two_objects(self):
        self.canonical()
        self.db.execute("INSERT INTO events(sha256,kind,detail) VALUES (?, 'duplicate', ?)",
                        (self.digest, 'second.pdf'))
        self.db.commit()
        result = audit.audit(self.root)
        self.assertTrue(result['complete'])
        self.assertEqual(result['summary']['catalog_unique_pdf_objects'], 1)
        self.assertEqual(result['summary']['historical_duplicate_uploads'], 1)
        self.assertEqual(result['summary']['duplicate_groups'], 1)
        self.assertEqual(result['groups'][0]['historical_duplicate_filenames'], ['second.pdf'])
        self.assertEqual(result['groups'][0]['staged_inbox_paths'], [])

    def test_pending_identical_files_and_match_to_canonical(self):
        self.canonical()
        self.staged('alpha.pdf')
        self.staged('beta.PDF')
        result = audit.audit(self.root)
        self.assertTrue(result['complete'])
        self.assertEqual(result['groups'][0]['staged_inbox_paths'], ['alpha.pdf', 'beta.PDF'])
        self.assertEqual(result['summary']['staged_pdfs_encountered'], 2)
        self.assertTrue((self.root / 'manuals' / 'inbox' / 'alpha.pdf').exists())
        result = audit.audit(self.root, include_inbox=False)
        self.assertFalse(result['inbox_scanned'])
        self.assertEqual(result['groups'], [])

    def test_same_filename_different_content_is_not_a_duplicate(self):
        self.canonical()
        self.staged('primary.pdf', b'%PDF-different')
        result = audit.audit(self.root)
        self.assertEqual(result['summary']['duplicate_groups'], 0)

    def test_unreadable_symlink_marks_report_incomplete(self):
        self.canonical()
        (self.root / 'manuals' / 'inbox' / 'alias.pdf').symlink_to('/etc/passwd')
        result = audit.audit(self.root)
        self.assertFalse(result['complete'])
        self.assertEqual(result['summary']['unverified_or_skipped_entries'], 1)
        self.assertEqual(result['problems'][0]['type'], 'unreadable_or_unstable_pdf')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(['--root', str(self.root)]), 2)

    def test_orphan_event_rejected_as_incomplete_and_readonly(self):
        self.db.execute("INSERT INTO events(sha256,kind,detail) VALUES (?, 'duplicate', ?)",
                        (self.digest, 'orphan.pdf'))
        self.db.commit()
        result = audit.audit(self.root)
        self.assertFalse(result['complete'])
        self.assertEqual(result['summary']['historical_duplicate_uploads'], 0)
        self.assertEqual(result['problems'][0]['type'], 'unmatched_duplicate_event')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_rejects_symlink_storage_root_and_missing_catalog(self):
        link = self.root.parent / (self.root.name + '-link')
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            audit.audit(link)
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(ValueError):
                audit.audit(Path(other))


if __name__ == '__main__':
    unittest.main()
