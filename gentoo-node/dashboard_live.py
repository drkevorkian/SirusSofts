"""Authenticated, bounded WebSocket feed for the private Gentoo dashboard.

Uses an HTTPS-authenticated one-minute token issued by dashboard.py after
HTTP Basic authentication. Token is sent as the first masked WebSocket
message, never in URL or browser logs. No commands are accepted.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import secrets
import select
import struct
import threading
import time

ALLOWED_HOSTS = ("sirussofts.com", "gentoo.sirussofts.com")
MAX_CONNECTIONS = 12
MAX_FRAME = 2048


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def new_token(secret: str, *, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    payload = json.dumps({"exp": now + 90, "nonce": secrets.token_hex(16)},
                         separators=(",", ":")).encode()
    return b64(payload) + "." + b64(hmac.new(secret.encode(), payload, hashlib.sha256).digest())


def verify_token(secret: str, token: str, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    if not isinstance(token, str) or len(token) > 256 or token.count(".") != 1:
        return False
    try:
        raw, sig = token.split(".")
        payload = unb64(raw)
        digest = unb64(sig)
        if len(digest) != 32 or not hmac.compare_digest(
            hmac.new(secret.encode(), payload, hashlib.sha256).digest(), digest
        ):
            return False
        claims = json.loads(payload)
        return (type(claims) is dict and type(claims.get("exp")) is int
                and now - 2 <= claims["exp"] <= now + 92
                and isinstance(claims.get("nonce"), str)
                and len(claims["nonce"]) == 32)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return False


class LiveState:
    """One producer shared by every viewer, not one coordinator poll per socket."""
    def __init__(self, initial: dict | None = None):
        self.condition = threading.Condition()
        self.version = 0
        self.snapshot = initial or {
            "type": "snapshot", "metrics": {}, "hardware": {},
            "jobs": [], "graph": [], "sync": "Collecting live data."
        }
        self.last_serialized = None

    def publish(self, value: dict) -> bool:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(serialized) > 250000:
            return False
        with self.condition:
            if serialized == self.last_serialized:
                return False
            self.last_serialized = serialized
            self.snapshot = value
            self.version += 1
            self.condition.notify_all()
        return True

    def wait(self, last: int, timeout: float = 15) -> tuple[int, dict]:
        with self.condition:
            if self.version == last:
                self.condition.wait(timeout)
            return self.version, self.snapshot


def masked_frame(reader):
    header = reader.read(2)
    if len(header) != 2 or header[0] & 0x70 or not header[1] & 0x80:
        raise ValueError("Invalid WebSocket client frame")
    opcode = header[0] & 15
    length = header[1] & 127
    if length == 126:
        data = reader.read(2)
        if len(data) != 2:
            raise ValueError("Truncated frame")
        length = struct.unpack("!H", data)[0]
    elif length == 127:
        raise ValueError("Oversized client frame")
    if length > MAX_FRAME:
        raise ValueError("Client frame exceeds limit")
    mask = reader.read(4)
    if len(mask) != 4:
        raise ValueError("Truncated mask")
    payload = reader.read(length)
    if len(payload) != length:
        raise ValueError("Truncated payload")
    return opcode, bytes(char ^ mask[i % 4] for i, char in enumerate(payload))


def frame(payload: bytes, opcode: int = 1) -> bytes:
    length = len(payload)
    prefix = bytes((0x80 | opcode,))
    if length < 126:
        return prefix + bytes((length,)) + payload
    if length <= 65535:
        return prefix + bytes((126,)) + struct.pack("!H", length) + payload
    return prefix + bytes((127,)) + struct.pack("!Q", length) + payload


def _authenticated_subprotocol(headers, secret: str) -> bool:
    """Authenticate before HTTP 101; do not rely on a fragile first frame.

    Browsers cannot add custom Authorization headers to WebSocket requests.
    The short-lived HMAC token is therefore carried in one offered subprotocol,
    while the agreed response subprotocol is always the fixed 'sirus-live'.
    Neither a token nor a secret is written to URLs or application logs.
    """
    offered = headers.get("Sec-WebSocket-Protocol", "")
    if not isinstance(offered, str) or not 0 < len(offered) <= 512:
        return False
    parts = [item.strip() for item in offered.split(",")]
    if len(parts) != 2 or parts[0] != "sirus-live":
        return False
    signed = parts[1]
    if not signed.startswith("sirus-auth."):
        return False
    return verify_token(secret, signed[len("sirus-auth."):])


def stream(handler) -> None:
    """Perform an authenticated HTTP/1.1 WebSocket upgrade, then send snapshots."""
    slots = handler.server.live_slots
    if not slots.acquire(blocking=False):
        handler.send(503, b"Live dashboard connection limit reached",
                     "text/plain; charset=utf-8")
        return
    upgraded = False
    try:
        headers = handler.headers
        host = headers.get("Host", "").lower()
        origin = headers.get("Origin", "")
        key = headers.get("Sec-WebSocket-Key", "")
        try:
            raw_key = base64.b64decode(key, validate=True)
        except (ValueError, TypeError):
            raw_key = b""
        if (host not in ALLOWED_HOSTS
                or origin != "https://" + host
                or handler.request_version != "HTTP/1.1"
                or headers.get("Upgrade", "").lower() != "websocket"
                or "upgrade" not in headers.get("Connection", "").lower()
                or headers.get("Sec-WebSocket-Version") != "13"
                or len(raw_key) != 16):
            handler.send(403, b"WebSocket handshake rejected",
                         "text/plain; charset=utf-8")
            return
        if not _authenticated_subprotocol(headers, handler.server.secret):
            handler.send(403, b"WebSocket authentication rejected",
                         "text/plain; charset=utf-8")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept)
        handler.send_header("Sec-WebSocket-Protocol", "sirus-live")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        upgraded = True
        handler.connection.settimeout(25)
        handler.wfile.write(frame(b'{"type":"ready"}'))
        handler.wfile.flush()
        current = -1
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            version, state = handler.server.live_state.wait(current, 12)
            if version != current:
                outgoing = json.dumps(state, separators=(",", ":"), ensure_ascii=True).encode()
                handler.wfile.write(frame(outgoing))
                handler.wfile.flush()
                current = version
            else:
                handler.wfile.write(frame(b"", opcode=9))
                handler.wfile.flush()
            # Browsers respond to PING with masked PONG. Read control frames,
            # detect closes, and reject client data (this is a read-only feed).
            if select.select([handler.connection], [], [], 0)[0]:
                opcode, _ = masked_frame(handler.rfile)
                if opcode == 8:
                    break
                if opcode not in (9, 10):
                    break
        try:
            handler.wfile.write(frame(b"", opcode=8))
            handler.wfile.flush()
        except OSError:
            pass
    except (OSError, ValueError, TimeoutError, BrokenPipeError,
            ConnectionError, json.JSONDecodeError):
        # No signed tokens or user-controlled headers in server logs.
        pass
    finally:
        try:
            handler.connection.settimeout(None)
        except OSError:
            pass
        slots.release()
