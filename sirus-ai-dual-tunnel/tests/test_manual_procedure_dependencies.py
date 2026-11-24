"""Synthetic regression tests for read-only procedure ordering candidates."""
from pathlib import Path
import hashlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gentoo"))
from manual_procedure_dependencies import inspect
from manual_relationship_index import sync

A, B, C = "a" * 64, "b" * 64, "c" * 64

BASE = """Removal of primary clutch
1. Disconnect the negative battery cable.
2. Remove the primary clutch cover.
Before removing the primary clutch cover, disconnect the negative battery cable.
Installation of primary clutch
1. Reinstall the primary clutch cover.
"""
REVERSED = """Removal of primary clutch
1. Remove the primary clutch cover.
2. Disconnect the negative battery cable.
After removing the primary clutch cover, disconnect the negative battery cable.
Installation of primary clutch
1. Reinstall the primary clutch cover.
"""
NEUTRAL = """Removal of primary clutch
1. Disconnect the negative battery cable.
2. Remove the primary clutch cover.
Refer to the wiring diagram.
Installation of primary clutch
1. Reinstall the primary clutch cover.
"""


def scope(year=2026, model="Defender", revision="A"):
    return {"manufacturer": "BRP", "model": model, "year": year,
            "engine": "HD10", "revision": revision,
            "application": "Primary clutch"}


class ProcedureDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "pdf.db"
        self.index = self.root / "relationship.db"
        with sqlite3.connect(self.source) as db:
            db.executescript("""
                CREATE TABLE documents(sha256 TEXT PRIMARY KEY,filename TEXT,
                    status TEXT,reviewed INTEGER,updated_at INTEGER,excerpts INTEGER);
                CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)", [
                (A, "2026 Defender Service Manual", "approved", 1, 10, 1),
                (B, "2026 Defender Service Supplement", "approved", 1, 10, 1),
                (C, "2024 Commander Service Manual", "approved", 1, 10, 1),
            ])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)", [
                (A, 5, 1, BASE), (B, 8, 2, REVERSED), (C, 9, 1, NEUTRAL),
            ])
        sync(source=self.source, index=self.index)

    def request(self, b_scope=None, b=B, b_heading="removal of primary clutch"):
        return {"selections": [
            {"source_id": "pdf:" + A, "heading": "removal of primary clutch",
             "scope": scope()},
            {"source_id": "pdf:" + b, "heading": b_heading,
             "scope": b_scope or scope()},
        ]}

    def inspect(self, request=None):
        return inspect(self.request() if request is None else request,
                       source=self.source, index=self.index)

    def test_printed_sequence_and_explicit_temporal_order_cited(self):
        report = self.inspect()
        self.assertTrue(report["complete"])
        self.assertFalse(report["procedure_coverage_complete"])
        self.assertEqual(report["comparisons"][0]["status"],
                         "possible_order_disagreement")
        candidates = report["comparisons"][0]["candidates"]
        self.assertEqual({x["status"] for x in candidates},
                         {"possible_numbered_order_disagreement",
                          "possible_explicit_order_disagreement"})
        a = report["windows"][0]
        self.assertEqual([x["number"] for x in a["steps"]], [1, 2])
        self.assertEqual(a["explicit_relations"][0]["marker"], "before")
        self.assertEqual(a["explicit_relations"][0]["prerequisite"],
                         "disconnect the negative battery cable")
        self.assertEqual(a["steps"][0]["citation"]["page"], 5)
        self.assertEqual(a["steps"][0]["citation"]["part"], 1)
        quote = a["steps"][0]["citation"]["quote"]
        self.assertEqual(a["steps"][0]["citation"]["quote_sha256"],
                         hashlib.sha256(quote.encode()).hexdigest())
        self.assertNotIn("reinstall", " ".join(x["action"] for x in a["steps"]).lower())
        self.assertFalse(a["whole_procedure_verified"])

    def test_year_or_model_changes_not_flagged_as_conflicting(self):
        report = self.inspect(self.request(b_scope=scope(year=2024, model="Commander")))
        self.assertTrue(report["complete"])
        diff = report["comparisons"][0]
        self.assertEqual(diff["status"], "different_declared_scope")
        self.assertEqual(diff["candidates"], [])
        self.assertEqual(diff["scope_differences"]["year"], [2026, 2024])

    def test_revision_only_scope_change_suppresses_order_comparison(self):
        report = self.inspect(self.request(b_scope=scope(revision="B")))
        self.assertEqual(report["comparisons"][0]["status"],
                         "different_declared_scope")
        self.assertIn("revision", report["comparisons"][0]["scope_differences"])

    def test_revision_change_flags_review_without_guessing_age(self):
        report = self.inspect(self.request(b_scope=scope(revision="B")))
        record = report["comparisons"][0]
        candidate = record["revision_review"]
        self.assertEqual(record["status"], "different_declared_scope")
        self.assertEqual(record["candidates"], [])
        self.assertEqual(candidate["status"], "possible_outdated_procedure_review")
        self.assertIsNone(candidate["older_source_id"])
        self.assertFalse(candidate["supersession_verified"])
        self.assertEqual(candidate["left"]["heading"]["source_id"], "pdf:" + A)
        self.assertEqual(candidate["right"]["steps"][0]["page"], 8)
        self.assertNotIn("revision_review", self.inspect(
            self.request(b_scope=scope(year=2025, revision="B")))["comparisons"][0])
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=? WHERE sha256=?", (BASE, B))
        self.assertNotIn("revision_review", self.inspect(
            self.request(b_scope=scope(revision="B")))["comparisons"][0])

    def test_unapproved_and_stale_catalog_fails_closed(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?", (B,))
        report = self.inspect()
        self.assertFalse(report["complete"])
        self.assertEqual(report["windows"], [])
        self.assertEqual(report["comparisons"], [])
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET reviewed=1, updated_at=11 "
                       "WHERE sha256=?", (B,))
        self.assertFalse(self.inspect()["complete"])

    def test_changed_heading_fails_and_fresh_unannounced_body_is_read(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=replace(text, "
                       "'After removing the primary clutch cover, disconnect the negative battery cable.', "
                       "'Disconnect battery before servicing.') WHERE sha256=?", (B,))
        report = self.inspect()
        self.assertEqual(report["comparisons"][0]["status"],
                         "possible_order_disagreement")
        self.assertEqual(len(report["comparisons"][0]["candidates"]), 1)
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=replace(text, "
                       "'Removal of primary clutch', 'Diagnosis of primary clutch') "
                       "WHERE sha256=?", (B,))
        report = self.inspect()
        self.assertFalse(report["complete"])
        self.assertEqual(report["windows"], [])

    def test_ambiguous_duplicate_heading_refused(self):
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO excerpts VALUES(?,?,?,?)",
                       (B, 12, 1, REVERSED))
            db.execute("UPDATE documents SET excerpts=2,updated_at=12 WHERE sha256=?", (B,))
        sync(source=self.source, index=self.index)
        report = self.inspect()
        self.assertEqual(report["status"], "unavailable_or_ambiguous_heading")
        self.assertFalse(report["complete"])

    def test_unsupported_prose_does_not_infer_dependencies(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=? WHERE sha256=?", (NEUTRAL, B))
        report = self.inspect()
        self.assertTrue(report["complete"])
        self.assertEqual(report["windows"][1]["explicit_relations"], [])
        self.assertEqual(report["comparisons"][0]["candidates"], [])
        self.assertEqual(report["comparisons"][0]["status"],
                         "no_exact_order_disagreement_detected")

    def test_numbering_gaps_and_long_sections_refused(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=replace(text, "
                       "'2. Remove the primary clutch cover.', "
                       "'3. Remove the primary clutch cover.') WHERE sha256=?", (A,))
        self.assertEqual(self.inspect()["status"], "ambiguous_or_oversized_numbering")
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=? WHERE sha256=?",
                       ("Removal of primary clutch\n" + "\n".join(
                           "Non instructional filler text" for _ in range(165)), A))
        self.assertEqual(self.inspect()["status"], "procedure_window_exceeds_bounds")

    def test_invalid_scopes_source_ids_and_headings_rejected(self):
        requests = [self.request() for _ in range(4)]
        requests[0]["selections"][1]["source_id"] = "pdf:" + A
        requests[1]["selections"][0]["scope"]["year"] = "2026"
        requests[2]["selections"][1]["heading"] = "rm -rf /"
        requests[3]["selections"][0]["scope"].pop("model")
        for request in requests:
            with self.assertRaises(ValueError):
                self.inspect(request)

    def test_missing_and_symlink_indexes_refused_without_source_writes(self):
        self.index.unlink()
        with self.assertRaises(ValueError):
            self.inspect()
        fake = self.root / "outside.db"
        fake.touch()
        self.index.symlink_to(fake)
        with self.assertRaises(ValueError):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
