"""Independent scope review through the signed procedure control dispatcher."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import test_procedure_discovery as fixtures
from procedure_review_admin import control, overview


class EquipmentReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.DiscoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.args = fixture.args()
        fixture.seed()  # Existing pre-attestation queue and source declarations.
        self.quote = "BRP Defender 2026 HD10 clutch Rev-A"
        self.scope = "brp/defender/2026/hd10/clutch/rev-a"
        self.src = "pdf:"+fixtures.A
        with sqlite3.connect(self.args["source"]) as db:
            db.execute("UPDATE excerpts SET text=text||? WHERE sha256=?",
                       ("\n"+self.quote, fixtures.A))
        self.proposal = dict(source_id=self.src, page=2, part=1, quote=self.quote,
                             expected_scope=self.scope, actor="cms-admin:2")

    def dispatch(self, action, **data):
        return control(dict(local_only=True,action=action,**data), **self.args)

    def propose(self):
        return self.dispatch("propose_scope", **self.proposal)["id"]

    def vote(self, key, actor="cms-admin:3", decision="support"):
        return self.dispatch("review_scope", id=key, decision=decision, actor=actor,
                             confirmation=decision.upper()+" SCOPE", reason="Checked original passage")

    def test_independent_review_and_immutable_history(self):
        key = self.propose()
        for actor in ("cms-admin:1","cms-admin:2","model:3"):
            with self.assertRaises(ValueError):
                self.vote(key, actor)
        self.vote(key)
        item = self.dispatch("scope_detail", id=key)
        self.assertEqual(item["state"], "support")
        self.assertEqual(item["evidence"]["quote"], self.quote)
        self.assertEqual(item["evidence"]["scope"]["application"], "clutch")
        self.assertFalse(item["service_use_approved"])
        with self.assertRaises(ValueError):
            self.vote(key, decision="reject")
        with sqlite3.connect(self.args["queue"]) as db:
            for table in ("equipment_attestations","equipment_attestation_reviews"):
                for sql in ("DELETE FROM "+table, "UPDATE "+table+" SET id=id"):
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(sql)
            self.assertEqual(db.execute("SELECT count(*) FROM procedure_discovery_seeds").fetchone()[0],1)
        self.assertEqual(overview(**self.args)["attestations"][0]["id"], key)

    def test_concurrent_review_and_replay(self):
        key = self.propose()
        def try_vote(decision):
            try:
                self.vote(key, actor="cms-admin:"+("3" if decision=="support" else "4"),
                          decision=decision)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(try_vote, ("support","reject")), [True,False])
        with self.assertRaises(ValueError):
            self.propose()

    def test_scope_version_change_blocks_even_after_return_to_same_value(self):
        key = self.propose()
        self.vote(key)
        with sqlite3.connect(self.args["research_db"]) as db:
            for version, scope in ((2,self.scope.replace("2026","2025")),(3,self.scope)):
                db.execute("INSERT INTO cr_scope_versions VALUES(?,?,?,'cms-admin:1')",
                           (self.src,version,scope))
        item=self.dispatch("scope_detail",id=key)
        self.assertEqual(item["state"],"blocked")
        self.assertIsNone(item["evidence"])
        self.assertEqual(item["historical_decision"],"support")
        self.assertNotEqual(key,self.propose())

    def test_original_context_edit_and_revocation_block_review(self):
        key = self.propose()
        with sqlite3.connect(self.args["source"]) as db:
            db.execute("UPDATE excerpts SET text=text||' exception' WHERE sha256=?", (fixtures.A,))
        with self.assertRaises(ValueError):
            self.vote(key)
        key=self.propose()
        self.vote(key,decision="reject")
        with sqlite3.connect(self.args["source"]) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?", (fixtures.A,))
        item=self.dispatch("scope_detail",id=key)
        self.assertEqual(item["historical_decision"],"reject")
        self.assertEqual(item["state"],"blocked")
        self.assertIsNone(item["evidence"])

    def test_invalid_scope_and_untraceable_citation(self):
        for changes in ({"quote":self.quote.replace("2026","2025")},
                        {"expected_scope":self.scope.replace("2026","2025")},
                        {"quote":"Removal of primary clutch"}, {"source_id":"draft:one"},
                        {"page":True},{"part":-1},{"expected_scope":"brp/defender/2026"},
                        {"source_id":"pdf:"+fixtures.B},{"actor":"guest:1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.dispatch("propose_scope", **(self.proposal|changes))
        self.assertEqual(overview(**self.args)["attestations"],[])

    def test_failed_transaction_recovers_without_partial_decision(self):
        key=self.propose()
        with sqlite3.connect(self.args["queue"]) as db:
            db.execute("""CREATE TRIGGER simulated_failure AFTER INSERT ON equipment_attestation_reviews
                BEGIN SELECT RAISE(ABORT,'write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.vote(key)
        with sqlite3.connect(self.args["queue"]) as db:
            db.execute("DROP TRIGGER simulated_failure")
        self.assertEqual(self.dispatch("scope_detail",id=key)["state"],"pending")
        self.vote(key)
