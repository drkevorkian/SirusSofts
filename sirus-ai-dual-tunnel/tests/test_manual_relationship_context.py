"""Regression tests for source-scoped citations entering research prompts."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gentoo"))
from manual_relationship_index import sync
from manual_relationship_context import evidence_packet, append_for_section, section_requests

A = "a" * 64
B = "b" * 64


class ContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.source = root / "original.db"
        self.index = root / "rel.db"
        with sqlite3.connect(self.source) as db:
            db.executescript("""
                CREATE TABLE documents(sha256 TEXT PRIMARY KEY,filename TEXT,
                    status TEXT,reviewed INTEGER,updated_at INTEGER,excerpts INTEGER);
                CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)", [
                (A, "2026 Defender Manual", "approved", 1, 1, 1),
                (B, "2024 Commander Manual", "approved", 1, 1, 1),
            ])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)", [
                (A, 7, 1, "Part No. 420234561\nCooling System"),
                (B, 9, 2, "Part No. 420234561\nElectrical System"),
            ])
        sync(source=self.source, index=self.index)

    def packet(self, ids):
        return evidence_packet(source=self.source, index=self.index,
            source_ids=ids, requests=[{"kind": "part", "term": "420234561"}])

    def test_explicit_cross_source_scope_does_not_claim_fitment(self):
        single = self.packet(["pdf:" + A])
        self.assertTrue(single["complete"])
        self.assertEqual([c["source_id"] for c in single["citations"]], ["pdf:" + A])
        both = self.packet(["pdf:" + A, "pdf:" + B])
        self.assertEqual(len(both["citations"]), 2)
        self.assertIn("no model/year", both["interpretation"])

    def test_prompt_injection_is_quoted_as_untrusted_data(self):
        messages = [{"role": "user", "content": "Original section"}]
        added = append_for_section(messages, source=self.source,
            index=self.index, source_id="pdf:" + A,
            section="Part No. 420234561\nCooling System")
        self.assertTrue(added)
        self.assertIn("pdf:" + A, messages[0]["content"])
        self.assertNotIn("pdf:" + B, messages[0]["content"])
        self.assertIn("UNTRUSTED", messages[0]["content"])

    def test_missing_or_revoked_index_adds_no_context(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?", (A,))
        messages = [{"role": "user", "content": "unchanged"}]
        added = append_for_section(messages, source=self.source,
            index=self.index, source_id="pdf:" + A,
            section="Part No. 420234561")
        self.assertFalse(added)
        self.assertEqual(messages[0]["content"], "unchanged")
        self.assertFalse(self.packet(["pdf:" + A])["complete"])

    def test_changed_excerpts_fail_closed_even_without_revision_bump(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text='Part No. 420234562' WHERE sha256=?", (A,))
        result = self.packet(["pdf:" + A])
        self.assertFalse(result["complete"])
        self.assertEqual(result["citations"], [])

    def test_validation_and_missing_index(self):
        with self.assertRaises(ValueError):
            self.packet(["pdf:" + A, "pdf:" + A])
        with self.assertRaises(ValueError):
            evidence_packet(source=self.source, index=self.index,
                source_ids=["pdf:" + A], requests=[{"kind": "invalid", "term": "x"}])
        self.assertEqual(section_requests("pdf:../../", "Part No. 420234561"), [])
        msg = [{"role": "user", "content": "before"}]
        self.assertFalse(append_for_section(msg, source=self.source,
            index=self.index.with_name("missing.db"), source_id="pdf:" + A,
            section="Part No. 420234561"))
        self.assertEqual(msg[0]["content"], "before")


if __name__ == "__main__":
    unittest.main()
