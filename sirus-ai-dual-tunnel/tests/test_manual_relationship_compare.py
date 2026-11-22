"""Synthetic tests for explicit-scope, source-rechecked manual comparisons."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gentoo"))
from manual_relationship_compare import compare
from manual_relationship_index import sync

A = "a" * 64
B = "b" * 64
C = "c" * 64


def declared(year=2026, model="Defender", revision="Rev A"):
    return {"manufacturer": "BRP", "model": model, "year": year,
            "engine": "HD10", "revision": revision,
            "application": "Primary clutch"}


def ref(sha, page, quote):
    return {"source_id": "pdf:" + sha, "page": page, "part": 1, "quote": quote}


class ManualComparisonTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "pdf.db"
        self.index = self.root / "relationship.db"
        with sqlite3.connect(self.source) as db:
            db.executescript("""
                CREATE TABLE documents(sha256 TEXT PRIMARY KEY,filename TEXT,
                  status TEXT,reviewed INTEGER,updated_at INTEGER,excerpts INTEGER);
                CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)", [
                (A, "2026 Defender Shop Manual", "approved", 1, 11, 1),
                (B, "2024 Commander Shop Manual", "approved", 1, 11, 1),
                (C, "2026 Defender Supplement", "approved", 1, 11, 1),
            ])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)", [
                (A, 2, 1, "Part No. 420234561\nClutch bolt torque: 40 Nm\nCooling System"),
                (B, 3, 1, "Part No. 420234561\nClutch bolt torque: 50 Nm\nCooling System"),
                (C, 4, 1, "Part No. 420234561\nClutch bolt torque: 40 Nm\nCooling System"),
            ])
        sync(source=self.source, index=self.index)

    def request(self, *, left=A, right=B, left_scope=None, right_scope=None,
                measurement=True):
        scope_a = left_scope or declared()
        scope_b = right_scope or declared(year=2024, model="Commander")
        request = {
            "sources": [
                {"source_id": "pdf:" + left, "scope": scope_a},
                {"source_id": "pdf:" + right, "scope": scope_b},
            ],
            "terms": [{"kind": "part", "term": "420234561"},
                      {"kind": "system_heading", "term": "cooling system"}],
            "measurements": [],
        }
        if measurement:
            pages = {A: 2, B: 3, C: 4}
            values = {A: 40, B: 50, C: 40}
            request["measurements"].append({
                "question": "Clutch bolt torque",
                "left": ref(left, pages[left],
                            "Clutch bolt torque: %s Nm" % values[left]),
                "right": ref(right, pages[right],
                             "Clutch bolt torque: %s Nm" % values[right]),
            })
        return request

    def run_compare(self, request):
        return compare(request, source=self.source, index=self.index)

    def test_different_year_and_model_are_not_contradictions(self):
        report = self.run_compare(self.request())
        self.assertEqual(report["status"], "source_checked")
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["shared_mentions"]), 2)
        self.assertTrue(all(x["status"] == "different_declared_scope"
                            for x in report["shared_mentions"]))
        self.assertEqual(report["measurement_reviews"][0]["status"],
                         "different_declared_scope")
        self.assertEqual(report["measurement_reviews"][0]["scope_differences"]["year"],
                         [2026, 2024])
        self.assertIn("operator_declared", report["scope_authority"])
        self.assertEqual(report["shared_mentions"][0]["citations"][0][0]["page"], 2)
        self.assertEqual(report["shared_mentions"][0]["citations"][1][0]["page"], 3)

    def test_equal_declared_scope_is_still_not_confirmed_fitment(self):
        report = self.run_compare(self.request(right=C,
            right_scope=declared()))
        self.assertTrue(report["complete"])
        self.assertEqual(report["shared_mentions"][0]["status"],
                         "shared_mention_requires_application_review")
        self.assertEqual(report["measurement_reviews"][0]["status"],
                         "numeric_agreement_only")
        self.assertIn("NOT part interchangeability",
                      report["shared_mentions"][0]["interpretation"])

    def test_difference_candidates_only_with_identical_declared_scope(self):
        report = self.run_compare(self.request(right_scope=declared()))
        review = report["measurement_reviews"][0]
        self.assertEqual(review["status"], "numeric_difference_candidate")
        self.assertEqual(review["normalized_values"], ["40", "50"])
        self.assertEqual(review["left"]["source_id"], "pdf:" + A)
        self.assertEqual(review["right"]["source_id"], "pdf:" + B)
        self.assertEqual(len(review["left"]["quote_sha256"]), 64)
        self.assertIn("not a verified contradiction", review["explanation"])

    def test_revision_only_difference_suppresses_conflict_candidate(self):
        report = self.run_compare(self.request(
            right_scope=declared(revision="Rev B")))
        self.assertEqual(report["measurement_reviews"][0]["status"],
                         "different_declared_scope")
        self.assertIn("revision",
                      report["measurement_reviews"][0]["scope_differences"])

    def test_unannounced_numeric_change_invalidates_quote(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=replace(text, '50 Nm', '51 Nm') "
                       "WHERE sha256=?", (B,))
        report = self.run_compare(self.request(right_scope=declared()))
        self.assertEqual(report["status"], "unresolved_measurements")
        self.assertFalse(report["complete"])
        self.assertEqual(report["measurement_reviews"][0]["status"],
                         "unavailable_evidence")
        self.assertIsNone(report["measurement_reviews"][0]["right"])

    def test_unannounced_mention_change_refuses_all_candidates(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=replace(text, '420234561', "
                       "'420234562') WHERE sha256=?", (B,))
        report = self.run_compare(self.request())
        self.assertEqual(report["status"], "stale_or_incomplete_index")
        self.assertFalse(report["complete"])
        self.assertEqual(report["shared_mentions"], [])
        self.assertEqual(report["measurement_reviews"], [])

    def test_revision_bump_or_revocation_fails_closed(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET updated_at=12 WHERE sha256=?", (B,))
        self.assertFalse(self.run_compare(self.request())["complete"])
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET updated_at=11, reviewed=0 WHERE sha256=?",
                       (B,))
        report = self.run_compare(self.request())
        self.assertFalse(report["complete"])
        self.assertEqual(report["shared_mentions"], [])

    def test_bounded_dense_index_never_claims_complete(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=? WHERE sha256=?", (
                "\n".join("Part No. 420234561 note %02d" % i for i in range(70)), A))
        sync(source=self.source, index=self.index, force=True)
        report = self.run_compare(self.request(measurement=False))
        self.assertEqual(report["status"], "bounded_index_query")
        self.assertFalse(report["complete"])
        self.assertEqual(report["shared_mentions"], [])

    def test_invalid_cross_document_selection_is_rejected(self):
        request = self.request()
        request["sources"][1] = request["sources"][0]
        with self.assertRaises(ValueError):
            self.run_compare(request)
        request = self.request()
        request["sources"][0]["scope"].pop("year")
        with self.assertRaises(ValueError):
            self.run_compare(request)
        request = self.request()
        request["measurements"][0]["right"]["source_id"] = "pdf:" + "f"*64
        with self.assertRaises(ValueError):
            self.run_compare(request)
        request = self.request()
        request["terms"] = []
        with self.assertRaises(ValueError):
            self.run_compare(request)
        request = self.request()
        request["sources"][1]["scope"]["year"] = True
        with self.assertRaises(ValueError):
            self.run_compare(request)

    def test_wrong_page_or_property_label_is_not_a_conflict(self):
        request = self.request(right_scope=declared())
        request["measurements"][0]["right"]["page"] = 18
        report = self.run_compare(request)
        self.assertEqual(report["measurement_reviews"][0]["status"],
                         "unavailable_evidence")
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE excerpts SET text=replace(text, 'Clutch bolt torque:', "
                       "'Clutch spring torque:') WHERE sha256=?", (B,))
        sync(source=self.source, index=self.index, force=True)
        request = self.request(right_scope=declared())
        request["measurements"][0]["right"]["quote"] = "Clutch spring torque: 50 Nm"
        report = self.run_compare(request)
        self.assertEqual(report["measurement_reviews"][0]["status"],
                         "different_property_labels")


if __name__ == "__main__":
    unittest.main()
