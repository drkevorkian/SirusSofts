#!/usr/bin/env python3
"""Read-only signed WebSocket diagnostic: Gentoo loopback vs public HTTPS.

Run on Gentoo as root from the reviewed SirusSofts checkout:
    sudo python3 -B gentoo-node/diagnose-websocket.py

Uses the existing private dashboard credential hash only to mint a short-lived
HMAC token in memory. No password, token, private file or server configuration
is printed, changed or transmitted beyond its regular signed WS subprotocol.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import sys

import dashboard_live

AUTH = Path("/etc/sirus-gentoo-node/dashboard.auth")
MAX_FRAME = 300000


def load_auth(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("Dashboard credential file must not be a symlink")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024:
        raise ValueError("Private dashboard credential file has unsafe permissions/size")
    record = path.read_text(encoding="utf8").strip()
    if not record.startswith("pbkdf2-sha256:"):
        raise ValueError("Unrecognized dashboard authentication record")
    return record


def read_frame(reader):
    head = reader.read(2)
    if len(head) != 2 or head[0] & 15 != 1 or head[1] & 128:
        raise ValueError("No valid text frame from the upgraded server")
    length = head[1] & 127
    if length == 126:
        raw = reader.read(2)
        if len(raw) != 2:
            raise ValueError("Truncated frame length")
        length = int.from_bytes(raw, "big")
    elif length == 127:
        raw = reader.read(8)
        if len(raw) != 8:
            raise ValueError("Truncated frame length")
        length = int.from_bytes(raw, "big")
    if length > MAX_FRAME:
        raise ValueError("Oversized incoming frame")
    payload = reader.read(length)
    if len(payload) != length:
        raise ValueError("Truncated server message")
    message = json.loads(payload)
    if type(message) is not dict:
        raise ValueError("Invalid server message")
    return message


def probe(host: str, port: int, route: str, token: str, *,
          encrypted: bool) -> tuple[bool, str]:
    """Use the actual Chrome-equivalent handshake; never log the token."""
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode("ascii")
    with socket.create_connection((host, port), timeout=7) as connection:
        connection.settimeout(7)
        if encrypted:
            conn = ssl.create_default_context().wrap_socket(
                connection, server_hostname="sirussofts.com"
            )
        else:
            conn = connection
        with conn:
            request = (
                f"GET {route} HTTP/1.1\r\n"
                "Host: sirussofts.com\r\n"
                "Origin: https://sirussofts.com\r\n"
                "Connection: Upgrade\r\n"
                "Upgrade: websocket\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Protocol: sirus-live, sirus-auth.{token}\r\n"
                "\r\n"
            )
            conn.sendall(request.encode("ascii"))
            reader = conn.makefile("rb")
            try:
                status = reader.readline(128).decode("ascii", errors="replace").strip()
                if not status.startswith("HTTP/1."):
                    return False, "No HTTP response to WebSocket Upgrade"
                headers = {}
                for _ in range(40):
                    line = reader.readline(4096)
                    if line == b"\r\n":
                        break
                    if not line or len(line) >= 4096 or b":" not in line:
                        return False, "Invalid HTTP response headers"
                    name, value = line.split(b":", 1)
                    headers[name.decode("ascii", errors="replace").strip().lower()] = (
                        value.decode("ascii", errors="replace").strip()
                    )
                else:
                    return False, "Response headers were too large"
                if status != "HTTP/1.1 101 Switching Protocols":
                    length = min(int(headers.get("content-length", "0") or "0"), 120)
                    description = reader.read(length).decode("utf8", errors="replace")
                    if "WebSocket authentication rejected" in description:
                        return False, "HTTP 403: Gentoo rejected the signed handshake token (check forwarded subprotocol header)"
                    if "WebSocket handshake rejected" in description:
                        return False, "HTTP 403: Gentoo rejected a Host/Origin/Upgrade header"
                    return False, f"{status}: WebSocket upgrade rejected before connecting"
                if headers.get("sec-websocket-protocol") != "sirus-live":
                    return False, "HTTP 101 but server did not select the required sirus-live protocol"
                if headers.get("sec-websocket-accept") != expected_accept:
                    return False, "HTTP 101 but Sec-WebSocket-Accept did not match the challenge"
                if read_frame(reader).get("type") != "ready":
                    return False, "Upgraded connection did not receive the ready frame"
                if read_frame(reader).get("type") != "snapshot":
                    return False, "Upgraded connection did not receive a metrics snapshot"
                return True, "HTTP 101, valid signed handshake, ready + snapshot received"
            finally:
                reader.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="syntax/import validation only")
    parser.add_argument("--auth-file", type=Path, default=AUTH,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check:
        print("PASS public WebSocket diagnostic imports and CLI")
        return 0
    try:
        record = load_auth(args.auth_file)
    except (OSError, ValueError, UnicodeError):
        print("FAIL Cannot securely read the existing Gentoo dashboard credential")
        return 2
    # Reuse a local HMAC record. Never print this token; do not put it in a URL.
    token = dashboard_live.new_token(record)
    results = []
    for name, target in (
        ("Gentoo loopback", ("127.0.0.1", 18080, "/live", False)),
        ("Public HTTPS", ("sirussofts.com", 443, "/Gentoo/live", True)),
    ):
        host, port, route, tls = target
        try:
            ok, explanation = probe(host, port, route, token, encrypted=tls)
        except (OSError, ssl.SSLError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            ok, explanation = False, f"Connection or protocol error: {type(exc).__name__}"
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {explanation}", flush=True)
    if results == [True, True]:
        print("PASS End-to-end signed WebSocket works from Gentoo through public HTTPS.")
    elif results[0] and not results[1]:
        print("DIAGNOSIS: Gentoo works; public HTTPS / Nginx forwarding needs correction.")
    else:
        print("DIAGNOSIS: local Gentoo WebSocket needs investigation before public routing.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
