"""Signed administrator procedure-review facade: source recheck, decisions, audit."""
import sqlite3
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"gentoo"))
sys.path.insert(0,str(ROOT/"tests"))
from test_procedure_discovery import DiscoveryTests
from procedure_discovery import accept, scan_once, suggestions
from procedure_review_admin import overview,detail,decide,control
from procedure_review_queue import list_reviews

class AdminProcedureTests(unittest.TestCase):
    def setUp(self):
        # Reuse the complete synthetic original-PDF and CMS-scope fixtures
        # without inheriting their test methods into this test class.
        self.fixture=DiscoveryTests(methodName="test_seed_discovers_only_matching_complete_admin_scopes")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.args=self.fixture.args()
        self.fixture.seed()
        report=scan_once(**self.args,now=10)
        self.assertEqual(report["suggestions_observed"],1)
        self.suggestion=overview(**self.args)["suggestions"][0]["id"]

    def current(self):
        return detail(**self.args,suggestion_id=self.suggestion)

    def test_detail_contains_original_quote_citations_and_scope(self):
        item=self.current()
        self.assertTrue(item["reviewable"])
        self.assertEqual(item["status"],"unreviewed_suggestion")
        self.assertEqual(len(item["selections"]),2)
        self.assertEqual(len(item["candidates"]),2)
        self.assertEqual(item["selections"][0]["scope"]["year"],2026)
        self.assertEqual(item["windows"][0]["steps"][0]["citation"]["page"],2)
        self.assertEqual(len(item["windows"][0]["steps"][0]["citation"]["quote_sha256"]),64)
        self.assertIn("not_independently_verified",item["scope_authority"])

    def test_revision_suggestion_is_reviewable_but_not_a_conflict_packet(self):
        from procedure_review_admin import _procedure_packet
        with sqlite3.connect(self.args["research_db"]) as db:
            scope="brp/defender/2026/hd10/clutch/rev-b"
            db.execute("UPDATE cr_evidence_scopes SET applicability=? WHERE source_id=?",
                       (scope,"pdf:"+"b"*64))
            db.execute("INSERT INTO cr_scope_versions VALUES(?,2,?,'cms-admin:1')",
                       ("pdf:"+"b"*64,scope))
        self.assertFalse(self.current()["reviewable"])
        scan_once(**self.args,now=10000)
        seen=self.current()
        self.assertTrue(seen["reviewable"])
        self.assertEqual(seen["candidates"][0]["status"],"possible_outdated_procedure_review")
        self.assertFalse(seen["candidates"][0]["supersession_verified"])
        with sqlite3.connect(self.args["queue"]) as db:
            db.row_factory=sqlite3.Row
            with self.assertRaisesRegex(ValueError,"not order conflicts"):
                _procedure_packet(db,self.suggestion,"a"*64,"b"*64,
                                  self.args["source"],self.args["index"],self.args["research_db"])
        outcome=decide(**self.args,suggestion_id=self.suggestion,action="accept",
                       actor="cms-admin:42",expected_hash=seen["evidence_hash"],
                       confirmation="ACCEPT FOR REVIEW",reason="Review revision changes")
        self.assertFalse(outcome["verified_repair"])

    def test_explicit_accept_is_atomic_and_audited_without_knowledge_approval(self):
        seen=self.current()
        result=decide(**self.args,suggestion_id=self.suggestion,
                      action="accept",actor="cms-admin:42",
                      expected_hash=seen["evidence_hash"],
                      confirmation="ACCEPT FOR REVIEW",reason="Checked selected original pages",
                      now=30)
        self.assertTrue(result["recorded"])
        self.assertFalse(result["verified_repair"])
        self.assertFalse(result["model_training"])
        self.assertEqual(result["review_id"],list_reviews(queue=self.args["queue"])["reviews"][0]["id"])
        view=overview(**self.args)
        self.assertEqual(view["suggestions"],[])
        self.assertEqual(view["decisions"][0]["actor"],"cms-admin:42")
        self.assertEqual(view["decisions"][0]["action"],"accepted")
        with sqlite3.connect(self.args["queue"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM procedure_review_targets").fetchone()[0],1)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE procedure_discovery_decisions SET action='rejected'")
        with self.assertRaises(ValueError):
            decide(**self.args,suggestion_id=self.suggestion,
                   action="accept",actor="cms-admin:42",expected_hash=seen["evidence_hash"],
                   confirmation="ACCEPT FOR REVIEW",reason="Duplicate attempt")

    def test_rejection_is_permanent_history_not_claim_resolution(self):
        seen=self.current()
        result=decide(**self.args,suggestion_id=self.suggestion,
                      action="reject",actor="cms-admin:4",
                      expected_hash=seen["evidence_hash"],
                      confirmation="REJECT SUGGESTION",reason="Different service conditions",
                      now=30)
        self.assertTrue(result["recorded"])
        self.assertEqual(result["review_id"],"")
        status=overview(**self.args)
        self.assertEqual(status["suggestions"],[])
        self.assertEqual(suggestions(**self.args)["suggestions"], [])
        with self.assertRaises(ValueError):
            accept(**self.args, suggestion_id=self.suggestion)
        self.assertEqual(status["decisions"][0]["reason"],"Different service conditions")
        with sqlite3.connect(self.args["queue"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM procedure_review_targets").fetchone()[0],0)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM procedure_discovery_decisions")
        with self.assertRaises(ValueError):
            self.current()

    def test_wrong_confirmation_or_digest_never_changes_state(self):
        current=self.current()
        for options in (
            {"actor":"model:1"},
            {"expected_hash":"0"*64},
            {"confirmation":"ACCEPT REPAIR"},
            {"reason":"x"},
        ):
            kw={"action":"accept","actor":"cms-admin:2",
                "expected_hash":current["evidence_hash"],
                "confirmation":"ACCEPT FOR REVIEW","reason":"Human original review"}
            kw.update(options)
            with self.assertRaises(ValueError):
                decide(**self.args,suggestion_id=self.suggestion,**kw)
        self.assertEqual(overview(**self.args)["decisions"],[])
        self.assertEqual(list_reviews(queue=self.args["queue"])["reviews"],[])

    def test_source_revocation_scope_revision_and_tamper_fail_closed(self):
        seen=self.current()
        with sqlite3.connect(self.args["source"]) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?",
                       ("b"*64,))
        stale=self.current()
        self.assertFalse(stale["reviewable"])
        with self.assertRaises(ValueError):
            decide(**self.args,suggestion_id=self.suggestion,action="reject",
                   actor="cms-admin:3",expected_hash=seen["evidence_hash"],
                   confirmation="REJECT SUGGESTION",reason="Old PDF source")
        self.assertEqual(overview(**self.args)["decisions"],[])

    def test_control_strict_schema_and_actor(self):
        req={"local_only":True,"action":"detail","id":self.suggestion}
        self.assertTrue(control(req,**self.args)["reviewable"])
        invalid=[
            {"local_only":False,"action":"detail","id":self.suggestion},
            {"local_only":True,"action":"detail","id":self.suggestion,"path":"/etc/passwd"},
            {"local_only":True,"action":"seed","source_id":"pdf:"+"a"*64,
             "heading":"Removal of primary clutch","actor":"user:1"},
            {"local_only":True,"action":"accept","id":self.suggestion,
             "expected_hash":self.current()["evidence_hash"],
             "confirmation":"ACCEPT FOR REVIEW","reason":"yes",
             "actor":"model:1"},
        ]
        for record in invalid:
            with self.assertRaises(ValueError):
                control(record,**self.args)
        self.assertEqual(overview(**self.args)["decisions"],[])

    def test_signed_auto_discovery_control_requires_exact_confirmation(self):
        for actor,confirmation in (("model:1","ENABLE AUTO DISCOVERY"),
                                   ("cms-admin:1","YES")):
            with self.assertRaises(ValueError):
                control({"local_only":True,"action":"auto_enable","actor":actor,
                         "confirmation":confirmation},**self.args)
        self.assertFalse(overview(**self.args)["automatic_discovery"]["enabled"])
        result=control({"local_only":True,"action":"auto_enable","actor":"cms-admin:1",
                        "confirmation":"ENABLE AUTO DISCOVERY"},**self.args)
        self.assertTrue(result["enabled"])
        self.assertTrue(overview(**self.args)["automatic_discovery"]["enabled"])
        control({"local_only":True,"action":"auto_pause","actor":"cms-admin:1",
                 "confirmation":"PAUSE AUTO DISCOVERY"},**self.args)
        self.assertFalse(overview(**self.args)["automatic_discovery"]["enabled"])

    def test_initial_overview_does_not_create_queue(self):
        self.args["queue"].unlink()
        result=overview(**self.args)
        self.assertFalse(result["configured"])
        self.assertFalse(self.args["queue"].exists())


if __name__=="__main__":
    unittest.main()
