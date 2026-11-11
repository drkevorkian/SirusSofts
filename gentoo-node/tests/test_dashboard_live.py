import base64
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dashboard
import dashboard_live


class LiveDashboardTests(unittest.TestCase):
    def test_page_contains_no_forced_refresh(self):
        html = dashboard.page(
            {"cpu_pct":21.5,"memory_used_bytes":123,"disk_used_bytes":45,
             "disk_total_bytes":1000,"uptime_seconds":10,"gpus":[]},
            {"node_id":"gentoo-home","ram_bytes":1024,"cpu_threads":4},
            [],"","History available").decode()
        self.assertNotIn('http-equiv="refresh"',html)
        self.assertIn('src="./dashboard_live.js"',html)
        self.assertIn('id="live-job-rows"',html)
        self.assertIn('id="live-gpu-rows"',html)
        self.assertIn('id="live-cpu"',html)
        script = (ROOT/"dashboard_live.js").read_text()
        self.assertNotIn("window.location.reload",script)
        self.assertNotIn("location.reload(",script)
        self.assertIn('new WebSocket(endpoint.href)',script)

    def test_short_lived_socket_tokens(self):
        secret="salted stored dashboard credential"
        token=dashboard_live.new_token(secret,now=100)
        self.assertTrue(dashboard_live.verify_token(secret,token,now=110))
        self.assertFalse(dashboard_live.verify_token(secret,token,now=195))
        self.assertFalse(dashboard_live.verify_token("wrong",token,now=110))
        self.assertFalse(dashboard_live.verify_token(secret,token+"x",now=110))
        self.assertFalse(dashboard_live.verify_token(secret,"../unsafe",now=110))

    def test_shared_producer_deduplicates_snapshots(self):
        store=dashboard_live.LiveState()
        data={"type":"snapshot","metrics":{"cpu_pct":11},"jobs":[]}
        self.assertTrue(store.publish(data))
        version, snapshot=store.wait(0,0)
        self.assertEqual(version,1)
        self.assertEqual(snapshot["metrics"]["cpu_pct"],11)
        self.assertFalse(store.publish(data))
        self.assertEqual(store.version,1)
        self.assertTrue(store.publish({**data,"metrics":{"cpu_pct":12}}))
        self.assertEqual(store.version,2)

    def test_authenticated_token_endpoint_and_websocket_stream(self):
        server=dashboard.ThreadingHTTPServer(("127.0.0.1",0),dashboard.Handler)
        server.secret=dashboard.make_hash("example-test-password")
        server.config={"node_id":"gentoo-home"}
        server.db="/not-used-here"
        server.live_state=dashboard_live.LiveState()
        server.live_slots=threading.BoundedSemaphore(4)
        server.live_state.publish({"type":"snapshot","metrics":{"cpu_pct":22},
                "hardware":{"node_id":"gentoo-home"},
                "jobs":[{"request_id":"a"*32,"state":"queued"}],
                "graph":[],"sync":"Signed history ready"})
        t=threading.Thread(target=server.serve_forever,daemon=True)
        t.start()
        try:
            port=server.server_port
            http=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
            http.request("GET","/live-token",headers={"Host":"sirussofts.com"})
            denied=http.getresponse()
            self.assertEqual(denied.status,401)
            denied.read()
            auth=base64.b64encode(b"admin:example-test-password").decode()
            http.request("GET","/live-token",headers={
                "Host":"sirussofts.com","Authorization":"Basic "+auth})
            response=http.getresponse()
            self.assertEqual(response.status,200)
            token=json.loads(response.read())["token"]
            http.close()
            with socket.create_connection(("127.0.0.1",port),timeout=5) as conn:
                conn.settimeout(5)
                key=base64.b64encode(os.urandom(16)).decode()
                conn.sendall(("GET /live HTTP/1.1\r\n"
                    "Host: sirussofts.com\r\nOrigin: https://sirussofts.com\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: "+key+
                    "\r\n\r\n").encode())
                reader=conn.makefile("rb")
                status=reader.readline()
                self.assertIn(b"101",status)
                while reader.readline()!=b"\r\n": pass
                raw=json.dumps({"token":token}).encode()
                mask=b"abcd"
                payload=bytes(byte^mask[i%4] for i,byte in enumerate(raw))
                self.assertLess(len(raw),126)
                conn.sendall(bytes([0x81,0x80|len(raw)])+mask+payload)
                self.assertIn(b'"type":"ready"',self.read_frame(reader))
                content=self.read_frame(reader)
                self.assertIn(b'"cpu_pct":22',content)
                self.assertIn(b'"queued"',content)
                reader.close()
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=5)

    @staticmethod
    def read_frame(reader):
        h=reader.read(2)
        if len(h)!=2:
            raise AssertionError("Missing websocket frame")
        size=h[1]&127
        if size==126:
            size=int.from_bytes(reader.read(2),"big")
        elif size==127:
            size=int.from_bytes(reader.read(8),"big")
        return reader.read(size)


if __name__=="__main__":
    unittest.main()
