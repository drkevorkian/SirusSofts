#!/usr/bin/env python3
"""Real Nginx → Gentoo WebSocket test using the production route fragment.

This is the path missing from earlier direct-socket tests. It runs only on
ephemeral 127.0.0.1 ports; no real Hestia config, keys, SSH tunnel or site.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gentoo-node"))
import dashboard
import dashboard_live


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def websocket_frame(reader):
    head = reader.read(2)
    if len(head) != 2:
        raise AssertionError("Missing WebSocket frame")
    length = head[1] & 127
    if length == 126:
        length = int.from_bytes(reader.read(2), "big")
    elif length == 127:
        length = int.from_bytes(reader.read(8), "big")
    data = reader.read(length)
    if len(data) != length:
        raise AssertionError("Truncated WebSocket frame")
    return json.loads(data)


class GentooWebsocketNginxEndToEnd(unittest.TestCase):
    @unittest.skipUnless(shutil.which("nginx"), "Nginx is needed to exercise the real proxy")
    def test_signed_dashboard_websocket_through_production_nginx_location(self):
        backend = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        backend.secret = dashboard.make_hash("safe-ci-test-password")
        backend.config = {"node_id": "gentoo-home"}
        backend.db = "/not-used-here"
        backend.live_state = dashboard_live.LiveState()
        backend.live_slots = threading.BoundedSemaphore(5)
        backend.live_state.publish({
            "type": "snapshot", "metrics": {"cpu_pct": 22},
            "hardware": {"node_id": "gentoo-home"},
            "jobs": [{"request_id": "a" * 32, "state": "queued"}],
            "graph": [], "sync": "signed history test"
        })
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        nginx_proc = None
        try:
            with tempfile.TemporaryDirectory(prefix="sirus-nginx-ws-") as directory:
                root = Path(directory)
                port = free_port()
                original = (ROOT / "deploy/ubuntu24/nginx.ssl.conf_sirusgentoolive").read_text()
                # Substitute the *single fixed remote-tunnel backend* with
                # an ephemeral local dashboard. Leave all other Nginx
                # headers and location rules precisely as reviewed.
                fragment = original.replace(
                    "127.0.0.1:18480", f"127.0.0.1:{backend.server_port}"
                )
                self.assertIn("proxy_set_header Upgrade $http_upgrade;", fragment)
                self.assertIn('proxy_set_header Connection "upgrade";', fragment)
                config = root / "nginx.conf"
                config.write_text(
                    "worker_processes 1;\n"
                    f"pid {root}/nginx.pid;\n"
                    f"error_log {root}/nginx-error.log info;\n"
                    "events { worker_connections 128; }\n"
                    "http { access_log off; server {\n"
                    f"  listen 127.0.0.1:{port}; server_name sirussofts.com;\n"
                    "  location /Gentoo/ {"
                    f" proxy_pass http://127.0.0.1:{backend.server_port}/;"
                    " proxy_set_header Host $host; }\n"
                    + fragment + "\n} }\n",
                    encoding="utf8"
                )
                check = subprocess.run(["nginx", "-t", "-p", str(root)+"/",
                                        "-c", str(config)],
                                       capture_output=True, text=True, timeout=6)
                self.assertEqual(check.returncode, 0, check.stderr)
                nginx_proc = subprocess.Popen(
                    ["nginx", "-p", str(root)+"/", "-c", str(config),
                     "-g", "daemon off;"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                for _ in range(40):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=.2):
                            break
                    except OSError:
                        time.sleep(.05)
                else:
                    self.fail("Nginx did not open loopback HTTP port")

                token = dashboard_live.new_token(backend.secret)
                with socket.create_connection(("127.0.0.1", port), timeout=5) as ws:
                    ws.settimeout(5)
                    key = base64.b64encode(os.urandom(16)).decode()
                    ws.sendall((
                        "GET /Gentoo/live HTTP/1.1\r\n"
                        "Host: sirussofts.com\r\n"
                        "Origin: https://sirussofts.com\r\n"
                        "Connection: Upgrade\r\n"
                        "Upgrade: websocket\r\n"
                        "Sec-WebSocket-Version: 13\r\n"
                        f"Sec-WebSocket-Key: {key}\r\n"
                        f"Sec-WebSocket-Protocol: sirus-live, sirus-auth.{token}\r\n"
                        "\r\n"
                    ).encode())
                    reader = ws.makefile("rb")
                    self.assertEqual(
                        reader.readline(), b"HTTP/1.1 101 Switching Protocols\r\n"
                    )
                    response_headers = []
                    while True:
                        line = reader.readline()
                        if line == b"\r\n":
                            break
                        response_headers.append(line.decode().lower())
                    self.assertIn("sec-websocket-protocol: sirus-live\r\n",
                                  response_headers)
                    self.assertEqual(websocket_frame(reader)["type"], "ready")
                    snap = websocket_frame(reader)
                    self.assertEqual(snap["metrics"]["cpu_pct"], 22)
                    self.assertEqual(snap["jobs"][0]["state"], "queued")
                    backend.live_state.publish({
                        **snap,
                        "metrics": {"cpu_pct": 41},
                        "jobs": [{"request_id": "a" * 32, "state": "done"}]
                    })
                    updated = websocket_frame(reader)
                    self.assertEqual(updated["metrics"]["cpu_pct"], 41)
                    self.assertEqual(updated["jobs"][0]["state"], "done")
                    reader.close()

                # Invalid signed credentials must fail BEFORE HTTP 101.
                with socket.create_connection(("127.0.0.1", port), timeout=5) as ws:
                    ws.settimeout(5)
                    ws.sendall((
                        "GET /Gentoo/live HTTP/1.1\r\n"
                        "Host: sirussofts.com\r\n"
                        "Origin: https://sirussofts.com\r\n"
                        "Connection: Upgrade\r\n"
                        "Upgrade: websocket\r\n"
                        "Sec-WebSocket-Version: 13\r\n"
                        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                        "Sec-WebSocket-Protocol: sirus-live, sirus-auth.invalid\r\n"
                        "\r\n"
                    ).encode())
                    self.assertEqual(ws.makefile("rb").readline(),
                                     b"HTTP/1.1 403 Forbidden\r\n")
        finally:
            if nginx_proc is not None:
                nginx_proc.terminate()
                try:
                    nginx_proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    nginx_proc.kill()
                    nginx_proc.communicate(timeout=3)
            backend.shutdown()
            backend.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
