"""Durable operator-scoped procedure contradiction-review queue tests."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
from manual_relationship_index import sync
from procedure_review_queue import (register,tick,list_reviews,history,
                                    request_recheck,RECHECK_SECS,BLOCK_RETRY_SECS)
from manual_procedure_dependencies import inspect
from relationship_scheduler import recheck_if_idle

A,B="a"*64,"b"*64
BASE="""Removal of primary clutch
1. Disconnect battery cable.
2. Remove primary clutch cover.
Before removing primary clutch cover, disconnect battery cable.
Installation of primary clutch
1. Install primary clutch cover.
"""
REVERSE="""Removal of primary clutch
1. Remove primary clutch cover.
2. Disconnect battery cable.
After removing primary clutch cover, disconnect battery cable.
Installation of primary clutch
1. Install primary clutch cover.
"""


def scope(year=2026):
    return {"manufacturer":"BRP","model":"Defender","year":year,
            "engine":"HD10","revision":"A","application":"Primary clutch"}


def request(year_b=2026):
    return {"selections":[
        {"source_id":"pdf:"+A,"heading":"removal of primary clutch","scope":scope()},
        {"source_id":"pdf:"+B,"heading":"removal of primary clutch","scope":scope(year_b)},
    ]}


class ProcedureQueueTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.src=self.root/"manuals.sqlite3"
        self.idx=self.root/"relationships.sqlite3"
        self.q=self.root/"private_reviews.sqlite3"
        with sqlite3.connect(self.src) as db:
            db.executescript("""
                CREATE TABLE documents(sha256 TEXT PRIMARY KEY,filename TEXT,
                   status TEXT,reviewed INTEGER,updated_at INTEGER,excerpts INTEGER);
                CREATE TABLE excerpts(sha256 TEXT,page INTEGER,part INTEGER,text TEXT);
            """)
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)",[
                (A,"Defender 2026 A","approved",1,10,1),
                (B,"Defender 2026 B","approved",1,10,1)])
            db.executemany("INSERT INTO excerpts VALUES(?,?,?,?)",[
                (A,2,1,BASE),(B,3,1,REVERSE)])
        sync(source=self.src,index=self.idx)

    def reg(self,req=None,now=1):
        return register(queue=self.q,request=request() if req is None else req,now=now)

    def check_once(self,now=10,**kw):
        return tick(queue=self.q,source=self.src,index=self.idx,now=now,**kw)

    def test_explicit_registration_dedup_initial_candidates_and_history(self):
        self.assertEqual(self.check_once()["reason"],"no_registered_reviews")
        item=self.reg()
        self.assertTrue(item["registered"])
        self.assertFalse(self.reg()["registered"])
        self.assertEqual(self.q.stat().st_mode & 0o077,0)
        result=self.check_once()
        self.assertEqual(result["status"],"monitoring")
        self.assertTrue(result["new_observation"])
        self.assertEqual(result["version"],1)
        self.assertEqual(result["current_candidate_count"],2)
        self.assertFalse(result["verified_conflict"])
        view=list_reviews(queue=self.q)["reviews"][0]
        self.assertEqual(view["current_candidates"],2)
        past=history(queue=self.q,target=item["id"],include_evidence=True)
        self.assertEqual(len(past["observations"]),1)
        candidates=past["observations"][0]["historical_unverified_evidence"]
        self.assertEqual({x["type"] for x in candidates},
                         {"possible_numbered_order_disagreement",
                          "possible_explicit_order_disagreement"})
        self.assertEqual(candidates[0]["evidence"]["left"][0]["source_id"],"pdf:"+A)
        self.assertIn("Historical",past["warning"])
        self.assertEqual(len(past["observations"][0]["candidate_changes"]["new"]),2)
        self.assertTrue(self.check_once(now=11)["skipped"])

    def test_stable_no_change_does_not_append_duplicate_history(self):
        target=self.reg()["id"]
        first=self.check_once()
        second=self.check_once(now=10+RECHECK_SECS+1)
        self.assertFalse(second["new_observation"])
        self.assertEqual(second["version"],first["version"])
        self.assertEqual(len(history(queue=self.q,target=target)["observations"]),1)

    def test_changed_unannounced_excerpts_reopen_review_and_preserve_old(self):
        target=self.reg()["id"]
        self.check_once()
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE excerpts SET text=? WHERE sha256=?", (BASE,B))
        request_recheck(queue=self.q,target=target)
        new=self.check_once(now=20)
        self.assertEqual(new["status"],"monitoring")
        self.assertEqual(new["current_candidate_count"],0)
        self.assertEqual(new["version"],2)
        past=history(queue=self.q,target=target,include_evidence=True)["observations"]
        self.assertEqual([x["count"] for x in past],[0,2])
        self.assertEqual(len(past[0]["historical_unverified_evidence"]),0)
        self.assertEqual(len(past[1]["historical_unverified_evidence"]),2)
        self.assertEqual(past[0]["candidate_changes"]["new"], [])
        self.assertEqual(len(past[0]["candidate_changes"]["no_longer_observed"]),2)

    def test_revocation_blocks_and_does_not_publish_stale_candidates(self):
        target=self.reg()["id"]
        self.check_once()
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?",(B,))
        request_recheck(queue=self.q,target=target)
        blocked=self.check_once(now=30)
        self.assertEqual(blocked["status"],"blocked")
        self.assertEqual(blocked["current_candidate_count"],0)
        self.assertIn("unavailable",blocked["reason"])
        self.assertTrue(self.check_once(now=31)["skipped"])
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE documents SET reviewed=1,updated_at=11 WHERE sha256=?",(B,))
        request_recheck(queue=self.q,target=target)
        self.assertEqual(self.check_once(now=40)["status"],"blocked")
        sync(source=self.src,index=self.idx)
        request_recheck(queue=self.q,target=target)
        after=self.check_once(now=50)
        self.assertEqual(after["status"],"monitoring")
        self.assertEqual(after["current_candidate_count"],2)
        self.assertEqual(len(history(queue=self.q,target=target)["observations"]),4)

    def test_revision_candidate_is_durable_and_revocation_blocks(self):
        req=request()
        req["selections"][1]["scope"]["revision"]="B"
        target=self.reg(req)["id"]
        result=self.check_once()
        self.assertEqual(result["current_candidate_count"],1)
        self.assertFalse(result["verified_conflict"])
        rows=history(queue=self.q,target=target,include_evidence=True)["observations"]
        candidate=rows[0]["historical_unverified_evidence"][0]
        self.assertEqual(candidate["type"],"possible_outdated_procedure_review")
        self.assertIsNone(candidate["evidence"]["older_source_id"])
        self.assertFalse(candidate["evidence"]["supersession_verified"])
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE documents SET reviewed=0 WHERE sha256=?",(B,))
        request_recheck(queue=self.q,target=target)
        self.assertEqual(self.check_once(now=20)["status"],"blocked")
        rows=history(queue=self.q,target=target,include_evidence=True)["observations"]
        self.assertEqual([row["count"] for row in rows],[0,1])

    def test_different_declared_year_never_enqueues_conflict(self):
        item=self.reg(request(2024))
        result=self.check_once()
        self.assertEqual(result["current_candidate_count"],0)
        self.assertTrue(result["new_observation"])
        self.assertEqual(history(queue=self.q,target=item["id"])["observations"][0]["count"],0)

    def test_recheck_is_disabled_during_chat_or_controller_off(self):
        self.reg()
        for enabled,chat in ((False,False),(False,True),(True,True)):
            out=recheck_if_idle(enabled=enabled,interactive=chat,queue=self.q,
                                source=self.src,index=self.idx)
            self.assertTrue(out["skipped"])
        self.assertEqual(list_reviews(queue=self.q)["reviews"][0]["status"],"pending")
        # No time injection needed: next_due=0 is already due.
        outcome=recheck_if_idle(enabled=True,interactive=False,
                                queue=self.q,source=self.src,index=self.idx)
        self.assertEqual(outcome["status"],"monitoring")

    def test_lease_fences_reentrant_worker_and_recovers_expired_claim(self):
        self.reg()
        def observe_overlap(req,**kwargs):
            competing=self.check_once(now=11)
            self.assertTrue(competing["skipped"])
            return inspect(req,**kwargs)
        self.assertEqual(self.check_once(now=10,evaluator=observe_overlap)["status"],"monitoring")
        target=self.reg()["id"]
        request_recheck(queue=self.q,target=target)
        with sqlite3.connect(self.q) as db:
            db.execute("""UPDATE procedure_review_targets SET
                status='leased',lease_token='orphan',lease_until=10,next_due=0""")
        self.assertEqual(self.check_once(now=200)["status"],"monitoring")

    def test_transient_error_retries_no_path_leaks(self):
        target=self.reg()["id"]
        def offline(req,**kwargs):
            raise RuntimeError("/private/secret/manual.pdf")
        blocked=self.check_once(evaluator=offline)
        self.assertEqual(blocked["status"],"blocked")
        self.assertEqual(blocked["reason"],"source_recheck_unavailable")
        self.assertTrue(self.check_once(now=10+BLOCK_RETRY_SECS-1)["skipped"])
        resumed=self.check_once(now=10+BLOCK_RETRY_SECS+1)
        self.assertEqual(resumed["status"],"monitoring")
        self.assertEqual(len(history(queue=self.q,target=target)["observations"]),2)

    def test_invalid_input_and_append_only_observations(self):
        invalid=request()
        invalid["selections"][0]["scope"].pop("engine")
        with self.assertRaises(ValueError):
            self.reg(invalid)
        target=self.reg()["id"]
        self.check_once()
        with sqlite3.connect(self.q) as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM procedure_review_observations")
        with self.assertRaises(ValueError):
            request_recheck(queue=self.q,target="../secret")
        with self.assertRaises(ValueError):
            history(queue=self.q,target=target,limit=0)
        self.assertFalse(request_recheck(queue=self.q,target="f"*64)["scheduled"])

    def test_symlink_queue_rejected(self):
        real=self.root/"outside"
        real.write_text("")
        link=self.root/"link.db"
        link.symlink_to(real)
        with self.assertRaises(ValueError):
            register(queue=link,request=request())

    def test_invalidated_index_or_duplicate_heading_disables_candidates(self):
        self.reg()
        with sqlite3.connect(self.src) as db:
            db.execute("INSERT INTO excerpts VALUES(?,?,?,?)", (B,4,1,REVERSE))
            db.execute("UPDATE documents SET updated_at=12,excerpts=2 "
                       "WHERE sha256=?",(B,))
        sync(source=self.src,index=self.idx)
        result=self.check_once()
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["current_candidate_count"],0)


if __name__=="__main__":
    unittest.main()
