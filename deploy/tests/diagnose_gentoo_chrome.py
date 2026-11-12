#!/usr/bin/env python3
"""One-off Chromium fixture for production Gentoo dashboard JS and Nginx proxy."""
import base64
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gentoo-node"))
import dashboard
import dashboard_live

PWD = "test-password-for-fixture-12345"


def main():
    backend = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    backend.secret = dashboard.make_hash(PWD)
    backend.config = {"node_id": "gentoo-home"}
    backend.live_state = dashboard_live.LiveState()
    backend.live_slots = threading.BoundedSemaphore(8)
    backend.live_state.publish({
        "type": "snapshot",
        "metrics": {"cpu_pct": 27.4, "memory_used_bytes": 1000000000,
                    "disk_used_bytes": 1024, "disk_total_bytes": 8192,
                    "uptime_seconds": 86400, "gpus": []},
        "hardware": {"node_id": "gentoo-home", "ram_bytes": 8000000000,
                     "cpu_threads": 16},
        "jobs": [], "graph": [], "sync": "test"
    })
    t = threading.Thread(target=backend.serve_forever, daemon=True)
    t.start()
    try:
        with tempfile.TemporaryDirectory(prefix="gentoo-chrome-") as temporary:
            root = Path(temporary)
            backend.db = str(root / "fixture.sqlite")
            key, pem = root / "key.pem", root / "cert.pem"
            subprocess.run([
                "openssl", "req", "-new", "-newkey", "rsa:2048",
                "-nodes", "-x509", "-days", "1",
                "-subj", "/CN=sirussofts.com",
                "-keyout", str(key), "-out", str(pem)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            fragment = (ROOT / "deploy/ubuntu24/nginx.ssl.conf_sirusgentoolive").read_text()
            fragment = fragment.replace(
                "127.0.0.1:18480", f"127.0.0.1:{backend.server_port}"
            )
            conf = root / "nginx.conf"
            conf.write_text(
                "worker_processes 1;\n"
                f"pid {root}/nginx.pid;\n"
                f"error_log {root}/error.log info;\n"
                "events { worker_connections 512; }\n"
                "http { access_log off; server {\n"
                "listen 127.0.0.1:443 ssl; server_name sirussofts.com;\n"
                f"ssl_certificate {pem}; ssl_certificate_key {key};\n"
                "location /Gentoo/ {\n"
                f" proxy_pass http://127.0.0.1:{backend.server_port}/;\n"
                " proxy_http_version 1.1; proxy_set_header Host $host;\n"
                " proxy_set_header X-Forwarded-Proto https;\n"
                "}\n"
                + fragment + "\n} }\n", encoding="utf8"
            )
            verify = subprocess.run(["nginx", "-p", str(root)+"/", "-c", str(conf), "-t"],
                                    capture_output=True, text=True)
            if verify.returncode:
                raise RuntimeError("Nginx test configuration invalid: " + verify.stderr)
            proc = subprocess.Popen(["sudo", "nginx", "-p", str(root)+"/",
                                     "-c", str(conf), "-g", "daemon off;"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(50):
                    try:
                        with socket.create_connection(("127.0.0.1", 443), timeout=.1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("Test-only TLS Nginx listener did not bind")
                browser = ROOT / "deploy/tests/diagnose_gentoo_chrome.cjs"
                print("Launching real Chrome against temporary production Nginx route...", flush=True)
                result = subprocess.run(["node", str(browser)], timeout=40)
                if result.returncode:
                    print("Nginx errors (last 1000 bytes):",
                          (root / "error.log").read_text()[-1000:], flush=True)
                return result.returncode
            finally:
                proc.terminate()
                try:
                    proc.communicate(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=3)
    finally:
        backend.shutdown()
        backend.server_close()
        t.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
