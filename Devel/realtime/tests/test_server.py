import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

FILE = Path(__file__).resolve().parents[1] / "server.py"
spec = importlib.util.spec_from_file_location("sirus_live", FILE)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class LiveSocketTests(unittest.TestCase):
    def token(self, secret=b"K"*32, exp=None, role="admin"):
        data=json.dumps({"role":role,"uid":5,"exp":exp or int(time.time())+45},
                        separators=(",",":")).encode()
        b64=lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return b64(data)+"."+b64(hmac.new(secret,data,hashlib.sha256).digest())

    def test_tokens_are_hmac_signed_and_expire(self):
        secret=b"K"*32
        self.assertTrue(live.verify_token(self.token(secret),secret))
        self.assertFalse(live.verify_token(self.token(secret),b"X"*32))
        self.assertFalse(live.verify_token(self.token(secret,exp=100),secret,now=400))
        self.assertFalse(live.verify_token(self.token(secret,role="viewer"),secret))
        self.assertFalse(live.verify_token("../../etc/passwd",secret))

    def test_snapshot_is_bounded_and_ro(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with sqlite3.connect(root/"work.sqlite3") as db:
                db.execute("""CREATE TABLE work_jobs(request_id TEXT, node_id TEXT,
                    kind TEXT,state TEXT,attempts INTEGER,updated_at INTEGER,
                    error TEXT,result_json TEXT,created_at INTEGER)""")
                for n in range(50):
                    db.execute("INSERT INTO work_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                      (str(n), "gentoo-home", "compute_selftest", "done", 1,
                       123,"", '{"safe":true}', n))
            with sqlite3.connect(root/"registry.sqlite3") as db:
                db.execute("CREATE TABLE node_status(node_id TEXT,os TEXT,agent_version TEXT,last_seen INTEGER,metrics_json TEXT)")
                db.execute("INSERT INTO node_status VALUES(?,?,?,?,?)",
                           ("gentoo-home","gentoo","0.5",int(time.time()),
                            json.dumps({"cpu_pct":12.0,"services":["sshd","nginx"],
                                        "applications":[{"name":"dashboard","healthy":True}]})))
            with sqlite3.connect(root/"live_events.sqlite3") as db:
                db.execute("CREATE TABLE live_events(id INTEGER,topic TEXT,payload_json TEXT,created_at INTEGER)")
                db.execute('INSERT INTO live_events VALUES(1,?,?,?)',
                           ("mail.received", '{"unread":2}', 123))
            result=live.snapshot(root)
            self.assertEqual(len(result["jobs"]),40)
            self.assertEqual(result["events"][0]["topic"],"mail.received")
            self.assertEqual(result["events"][0]["payload"],{"unread":2})
            self.assertEqual(result["jobs"][0]["result"],{"safe":True})
            self.assertTrue(result["nodes"][0]["online"])
            self.assertEqual(result["nodes"][0]["metrics"]["services"],["sshd","nginx"])
            with sqlite3.connect(root/"work.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM work_jobs").fetchone()[0],50)


if __name__=="__main__":
    unittest.main()
