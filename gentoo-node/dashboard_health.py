#!/usr/bin/env python3
"""Wait for the *new* private Gentoo dashboard after OpenRC restarts it.

OpenRC's supervise-daemon reports "started" before Python binds 18080.
Only use anonymous requests to loopback. Never read or print credentials.
"""
from __future__ import annotations
import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import time

import dashboard_live


def probe(port: int = 18080) -> tuple[bool, str]:
    for path, expected in (("/healthz", 200), ("/", 401),
                           ("/live-token", 401), ("/dashboard_live.js", 401)):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            status = response.status
            body = response.read(64)
            if status != expected:
                return False, f"{path}: HTTP {status}, expected {expected}"
            if path == "/healthz" and body != b"OK\n":
                return False, "/healthz: unexpected response"
        except (OSError, TimeoutError, http.client.HTTPException):
            return False, f"{path}: dashboard socket not accepting requests"
        finally:
            connection.close()
    return True, "healthy: dashboard and live endpoints are available"


def wait_ready(port: int = 18080, timeout: float = 30.0, *,
               check=probe, clock=time.monotonic, sleep=time.sleep) -> tuple[bool, str]:
    deadline = clock() + timeout
    latest = "Dashboard did not start"
    while True:
        ok, latest = check(port)
        if ok:
            return True, latest
        remaining = deadline - clock()
        if remaining <= 0:
            return False, latest
        sleep(min(0.4, remaining))



def websocket_selftest(port: int = 18080, *,
                       auth_file: Path = Path("/etc/sirus-gentoo-node/dashboard.auth")
                       ) -> tuple[bool, str]:
    """Test the running dashboard's REAL signed WebSocket authentication.

    Called as sirusnode, which already owns the private dashboard auth file.
    No credentials are logged or supplied as command-line arguments.
    """
    try:
        if auth_file.is_symlink():
            return False, "Dashboard credential path is a symlink"
        info = auth_file.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                or info.st_size > 1024):
            return False, "Dashboard credential file permissions/size invalid"
        secret = auth_file.read_text(encoding="utf8").strip()
        token = dashboard_live.new_token(secret)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        with socket.create_connection(("127.0.0.1", port), timeout=4) as client:
            client.settimeout(4)
            request = (
                "GET /live HTTP/1.1\\r\\n"
                "Host: sirussofts.com\\r\\n"
                "Origin: https://sirussofts.com\\r\\n"
                "Upgrade: websocket\\r\\n"
                "Connection: Upgrade\\r\\n"
                "Sec-WebSocket-Version: 13\\r\\n"
                f"Sec-WebSocket-Key: {key}\\r\\n"
                f"Sec-WebSocket-Protocol: sirus-live, sirus-auth.{token}\\r\\n"
                "\\r\\n"
            )
            client.sendall(request.encode())
            reader = client.makefile("rb")
            try:
                status = reader.readline(128)
                if status != b"HTTP/1.1 101 Switching Protocols\\r\\n":
                    return False, "Signed WebSocket handshake did not return HTTP/1.1 101"
                response_headers = []
                for _ in range(32):
                    line = reader.readline(2048)
                    if line == b"\\r\\n":
                        break
                    if not line or len(line) >= 2048:
                        return False, "Invalid WebSocket response headers"
                    response_headers.append(line.lower())
                else:
                    return False, "WebSocket response headers too large"
                if b"sec-websocket-protocol: sirus-live\\r\\n" not in response_headers:
                    return False, "Signed WebSocket subprotocol not accepted"

                def read_frame() -> dict:
                    header = reader.read(2)
                    if len(header) != 2 or header[0] & 15 != 1 or header[1] & 128:
                        raise ValueError("Unexpected WebSocket server frame")
                    length = header[1] & 127
                    if length == 126:
                        data = reader.read(2)
                        if len(data) != 2:
                            raise ValueError("Truncated WebSocket length")
                        length = int.from_bytes(data, "big")
                    elif length == 127:
                        data = reader.read(8)
                        if len(data) != 8:
                            raise ValueError("Truncated WebSocket length")
                        length = int.from_bytes(data, "big")
                    if length > 300000:
                        raise ValueError("Oversized WebSocket server frame")
                    payload = reader.read(length)
                    if len(payload) != length:
                        raise ValueError("Truncated WebSocket message")
                    result = json.loads(payload)
                    if not isinstance(result, dict):
                        raise ValueError("Invalid WebSocket snapshot")
                    return result

                if read_frame().get("type") != "ready":
                    return False, "No WebSocket ready acknowledgment"
                snapshot = read_frame()
                if snapshot.get("type") != "snapshot":
                    return False, "No WebSocket live data snapshot"
            finally:
                reader.close()
        return True, "signed WebSocket handshake, ready frame and live snapshot verified"
    except (OSError, ValueError, TimeoutError, UnicodeError, json.JSONDecodeError):
        return False, "Signed WebSocket self-test failed"



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--socket-check", action="store_true")
    parser.add_argument("--wait", type=float, default=30)
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.wait <= 60:
        parser.error("Port must be 1..65535 and wait must be 1..60 seconds")
    if args.check:
        print("PASS bounded Gentoo readiness probe checks dashboard and live endpoints")
        return 0
    if args.socket_check:
        success, result = websocket_selftest(args.port)
        print(("PASS " if success else "FAIL ") + result, flush=True)
        return 0 if success else 1
    success, result = wait_ready(args.port, args.wait)
    print(("PASS " if success else "FAIL ") + result, flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
