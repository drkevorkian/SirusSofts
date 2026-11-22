"""Synthetic source-backed engineering feasibility regression tests."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gentoo"))
import feasibility_review as review

DONOR = "a" * 64
TARGET = "b" * 64


class FeasibilityReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "pdf.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""CREATE TABLE documents (
                sha256 TEXT PRIMARY KEY, status TEXT, reviewed INTEGER);
                CREATE TABLE excerpts (sha256 TEXT, page INTEGER, part INTEGER, text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?)",
                           [(DONOR, "approved", 1), (TARGET, "approved", 1)])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)", [
                (DONOR, 2, 1, "Engine width: 80 cm\nEngine voltage: 12 V"),
                (TARGET, 8, 1, "Available bay width: 750 mm\n")
            ])

    @staticmethod
    def ref(sha, page, quote):
        return {"source_id": "pdf:" + sha, "page": page, "part": 1, "quote": quote}

    def plan(self, *, actual=None, limit=None, extra=None):
        req = {"id": "width", "question": "Is donor width within available bay width?",
               "criterion": "at_most",
               "actual": actual if actual is not None else
                   self.ref(DONOR, 2, "Engine width: 80 cm"),
               "limit": limit if limit is not None else
                   self.ref(TARGET, 8, "Available bay width: 750 mm")}
        return {"project": "hypothetical donor engine to recipient chassis", 
                "checks": [req] + (extra or [])}

    def test_cross_unit_numeric_conflict_requires_redesign_not_claim_of_impossibility(self):
        result = review.assess(self.plan(), source=self.path)
        self.assertEqual(result["status"], "measured_constraints_violated")
        self.assertEqual(result["checks"][0]["normalized_actual"], "800")
        self.assertEqual(result["checks"][0]["normalized_limit"], "750")
        self.assertEqual(result["blocked_count"], 1)
        self.assertIn("No complete-fit", result["scope_warning"])
        self.assertEqual(result["checks"][0]["actual"]["page"], 2)

    def test_non_conflict_is_only_numeric_and_missing_mount_question_remains_open(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE excerpts SET text='Engine width: 70 cm' WHERE sha256=?",
                       (DONOR,))
        missing = {"id": "mounts", "question": "What mount geometry is documented?",
                   "criterion": "equal", "actual": None, "limit": None}
        result = review.assess(self.plan(
            actual=self.ref(DONOR, 2, "Engine width: 70 cm"),
            extra=[missing]), source=self.path)
        self.assertEqual(result["checks"][0]["outcome"], "measured_constraint_satisfied")
        self.assertEqual(result["status"], "insufficient_verified_evidence")
        self.assertEqual(result["questions_requiring_evidence"],
                         ["What mount geometry is documented?"])

    def test_wrong_page_tampering_and_unapproved_manual_do_not_become_evidence(self):
        wrong = self.ref(DONOR, 5, "Engine width: 80 cm")
        result = review.assess(self.plan(actual=wrong), source=self.path)
        self.assertEqual(result["status"], "insufficient_verified_evidence")
        self.assertIsNone(result["checks"][0]["actual"])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?", (DONOR,))
        result = review.assess(self.plan(), source=self.path)
        self.assertEqual(result["checks"][0]["outcome"], "missing_verified_measurement")

    def test_incompatible_dimensions_cannot_be_compared(self):
        result = review.assess(self.plan(
            actual=self.ref(DONOR, 2, "Engine voltage: 12 V")), source=self.path)
        self.assertEqual(result["checks"][0]["outcome"],
                         "incompatible_or_unsupported_units")
        self.assertEqual(result["status"], "insufficient_verified_evidence")

    def test_invalid_or_duplicate_checks_are_rejected(self):
        p = self.plan()
        p["checks"] = p["checks"] * 2
        with self.assertRaises(ValueError):
            review.assess(p, source=self.path)
        p = self.plan()
        p["checks"][0]["criterion"] = "execute_shell"
        with self.assertRaises(ValueError):
            review.assess(p, source=self.path)

    def test_changes_to_original_quote_invalidate_previous_input(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE excerpts SET text='Engine width: 90 cm' WHERE sha256=?",
                       (DONOR,))
        report = review.assess(self.plan(), source=self.path)
        self.assertEqual(report["checks"][0]["outcome"], "missing_verified_measurement")
        self.assertEqual(report["blocked_count"], 0)


if __name__ == "__main__":
    unittest.main()
