"""Signed CMS-to-Gentoo administrator bridge: strict keys, TTL and replay."""
import os
from pathlib import Path
import sqlite3
from threading import Thread
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.request
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import input_server as ai
from research_admin_auth import (ADMIN_ROUTES, signature, load_secret,
                                 verify, init_schema)


class ResearchBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.db=root/"research.sqlite3"
        ai.init_db(self.db)
        init_schema(self.db)
        self.key=root/"research-admin.key"
        self.secret=b"z"*32
        self.key.write_bytes(self.secret)
        self.key.chmod(0o600)
        self.path="/api/in/research/evidence/control"
        self.now=1760000000
        self.nonce="a"*32
        self.raw=b'{"local_only":true,"action":"review","reviewer":"cms-admin:1"}'

    def headers(self, *, nonce=None, ts=None, path=None, body=None):
        timestamp=str(self.now if ts is None else ts)
        nonce=self.nonce if nonce is None else nonce
        path=self.path if path is None else path
        body=self.raw if body is None else body
        return {
            "X-Sirus-Admin-Time":timestamp,
            "X-Sirus-Admin-Nonce":nonce,
            "X-Sirus-Admin-Signature":signature(self.secret,timestamp,nonce,
                                               path,body)
        }

    def test_valid_request_consumes_nonce_and_replay_is_rejected(self):
        hdr=self.headers()
        self.assertTrue(verify(hdr,self.path,self.raw,self.db,
                               key_path=self.key,now=self.now))
        self.assertFalse(verify(hdr,self.path,self.raw,self.db,
                                key_path=self.key,now=self.now))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_admin_nonces").fetchone()[0],1)
        # Another legitimate distinct signed nonce succeeds on same route.
        hdr2=self.headers(nonce="b"*32)
        self.assertTrue(verify(hdr2,self.path,self.raw,self.db,
                               key_path=self.key,now=self.now))
        self.assertEqual(len(ADMIN_ROUTES),4)

    def test_tampered_payload_wrong_path_and_missing_auth_rejected(self):
        hdr=self.headers()
        self.assertFalse(verify(hdr,self.path,b"tampered",self.db,
                                key_path=self.key,now=self.now))
        self.assertFalse(verify(hdr,"/api/in/research/continuous/control",self.raw,
                                self.db,key_path=self.key,now=self.now))
        self.assertFalse(verify({},self.path,self.raw,self.db,
                                key_path=self.key,now=self.now))
        hdr["X-Sirus-Admin-Signature"]="0"*64
        self.assertFalse(verify(hdr,self.path,self.raw,self.db,
                                key_path=self.key,now=self.now))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_admin_nonces").fetchone()[0],0)

    def test_expired_clock_future_and_invalid_nonce_rejected(self):
        self.assertFalse(verify(self.headers(ts=self.now-121),self.path,self.raw,
                                self.db,key_path=self.key,now=self.now))
        self.assertFalse(verify(self.headers(ts=self.now+121),self.path,self.raw,
                                self.db,key_path=self.key,now=self.now))
        data=self.headers()
        data["X-Sirus-Admin-Nonce"]="../unsafe"
        self.assertFalse(verify(data,self.path,self.raw,self.db,
                                key_path=self.key,now=self.now))

    def test_restrict_key_permissions_link_and_size(self):
        self.assertEqual(load_secret(self.key),self.secret)
        self.key.chmod(0o644)
        self.assertIsNone(load_secret(self.key))
        self.assertFalse(verify(self.headers(),self.path,self.raw,self.db,
                                key_path=self.key,now=self.now))
        self.key.chmod(0o600)
        link=self.key.parent/"linked.key"
        link.symlink_to(self.key)
        self.assertIsNone(load_secret(link))
        self.key.write_bytes(b"wrong-size")
        self.assertIsNone(load_secret(self.key))
        self.assertIsNone(load_secret(self.key.parent/"missing"))

    def test_migration_is_idempotent_and_preserves_existing_jobs(self):
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO cr_admin_nonces(nonce,issued_at)
                VALUES(?,?)""",("a"*32,self.now))
        init_schema(self.db)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cr_admin_nonces").fetchone()[0],1)
            self.assertTrue(db.execute("""SELECT 1 FROM sqlite_master
                WHERE name='research_jobs'""").fetchone())

    def test_private_status_get_requires_signed_empty_body(self):
        path="/api/in/research/evidence"
        hdr=self.headers(path=path,body=b"",nonce="f"*32)
        self.assertFalse(verify({},path,b"",self.db,
                                key_path=self.key,now=self.now))
        self.assertTrue(verify(hdr,path,b"",self.db,
                               key_path=self.key,now=self.now))
        self.assertFalse(verify(hdr,path,b"",self.db,
                                key_path=self.key,now=self.now))

    def test_loopback_handler_denies_unsigned_private_reads_and_mutations(self):
        # The real HTTP handler must reject requests before any worker action.
        server=ThreadingHTTPServer(("127.0.0.1",0),ai.Handler)
        server.engine=type("PrivateEngine",(),{"db":self.db})()
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base=f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for route in ("/api/in/research/evidence",
                          "/api/in/research/continuous"):
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    urllib.request.urlopen(base+route,timeout=3)
                self.assertEqual(exc.exception.code,403)
            for route in ("/api/in/research/evidence/control",
                          "/api/in/research/continuous/control"):
                req=urllib.request.Request(base+route,method="POST",
                    data=b'{"local_only":true,"action":"stop"}',
                    headers={"Content-Type":"application/json"})
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    urllib.request.urlopen(req,timeout=3)
                self.assertEqual(exc.exception.code,403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_unsigned_admin_payload_cannot_bypass_route_authentication(self):
        # The JSON actor string is not authorization. A valid HMAC is mandatory.
        self.assertFalse(verify({},self.path,self.raw,self.db,
                                key_path=self.key,now=self.now))


if __name__=="__main__":
    unittest.main()
