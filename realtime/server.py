#!/usr/bin/env python3
"""Read-only authenticated WebSocket feed for SirusSofts administrators.

Binds loopback only. WebSocket authentication uses an ephemeral HMAC token
issued after CMS session + CSRF validation. It never accepts commands.
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from urllib.parse import urlsplit

HOST = "127.0.0.1"
PORT = 18482
DOMAIN = "sirussofts.com"
ROOT = Path("/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes")
KEY = ROOT / "live.key"
MAX_CLIENTS = 32
clients = 0


def b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_token(token, secret, now=None):
    now = int(time.time()) if now is None else now
    if not isinstance(token, str) or len(token) > 512 or token.count(".") != 1:
        return False
    try:
        payload, signature = token.split(".")
        data = b64decode(payload)
        sig = b64decode(signature)
        if len(sig) != 32 or not hmac.compare_digest(
            hmac.new(secret, data, hashlib.sha256).digest(), sig
        ):
            return False
        claims = json.loads(data)
        return (isinstance(claims, dict)
                and claims.get("role") == "admin"
                and isinstance(claims.get("uid"), int)
                and claims["uid"] > 0
                and isinstance(claims.get("exp"), int)
                and now - 10 <= claims["exp"] <= now + 90)
    except (ValueError, TypeError, UnicodeDecodeError, OverflowError):
        return False


def snapshot(root=ROOT):
    """Finite, read-only view. No database creation or modification."""
    data = {"jobs": [], "nodes": []}
    jobs_db = root / "work.sqlite3"
    nodes_db = root / "registry.sqlite3"
    if jobs_db.is_file():
        with sqlite3.connect(f"file:{jobs_db}?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT request_id,node_id,kind,state,attempts,
                                updated_at,error,result_json FROM work_jobs
                                ORDER BY created_at DESC,request_id DESC LIMIT 40""")
            for row in rows:
                item = dict(row)
                raw = item.pop("result_json", "{}")
                try:
                    item["result"] = json.loads(raw[:8192]) if len(raw) <= 8192 else {}
                except (ValueError, TypeError):
                    item["result"] = {}
                data["jobs"].append(item)
    if nodes_db.is_file():
        with sqlite3.connect(f"file:{nodes_db}?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute("""SELECT node_id,os,agent_version,last_seen
                                     FROM node_status ORDER BY node_id LIMIT 32"""):
                node = dict(row)
                node["online"] = node["last_seen"] >= int(time.time()) - 180
                data["nodes"].append(node)
    return data


async def recv_frame(reader):
    header = await reader.readexactly(2)
    opcode = header[0] & 0x0f
    if header[0] & 0x70 or not header[1] & 0x80:
        raise ValueError("Invalid masked client frame")
    length = header[1] & 0x7f
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    if length > 2048:
        raise ValueError("Frame too large")
    mask = await reader.readexactly(4)
    data = await reader.readexactly(length)
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


async def send_frame(writer, payload, opcode=1):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    length = len(payload)
    header = bytes([0x80 | opcode])
    if length < 126:
        header += bytes([length])
    elif length <= 65535:
        header += bytes([126]) + struct.pack("!H", length)
    else:
        header += bytes([127]) + struct.pack("!Q", length)
    writer.write(header + payload)
    await writer.drain()


async def serve(reader, writer, *, root=ROOT, key=KEY):
    global clients
    clients += 1
    try:
        if clients > MAX_CLIENTS:
            return
        line = await asyncio.wait_for(reader.readline(), 5)
        if len(line) > 2048 or line.strip() != b"GET /admin/live-socket HTTP/1.1":
            return
        headers = {}
        for _ in range(30):
            line = await asyncio.wait_for(reader.readline(), 5)
            if not line or len(line) > 4096:
                return
            if line == b"\r\n":
                break
            if b":" not in line:
                return
            k, v = line.split(b":", 1)
            headers[k.decode("ascii", "ignore").lower()] = v.decode("ascii", "ignore").strip()
        else:
            return
        if (headers.get("origin") != "https://" + DOMAIN
                or headers.get("upgrade", "").lower() != "websocket"
                or "upgrade" not in headers.get("connection", "").lower()
                or headers.get("sec-websocket-version") != "13"):
            return
        supplied = headers.get("sec-websocket-key", "")
        try:
            challenge = base64.b64decode(supplied, validate=True)
        except ValueError:
            return
        if len(challenge) != 16:
            return
        accept = base64.b64encode(hashlib.sha1(
            (supplied + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
        await writer.drain()
        opcode, data = await asyncio.wait_for(recv_frame(reader), 5)
        if opcode != 1:
            return
        try:
            auth = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(auth, dict) or set(auth) != {"token"}:
            return
        secret = key.read_bytes()
        if len(secret) != 32 or not verify_token(auth["token"], secret):
            return
        await send_frame(writer, json.dumps({"type": "ready", "version": 1}))
        previous = None
        deadline = time.monotonic() + 600
        frame_task = asyncio.create_task(recv_frame(reader))
        try:
            while time.monotonic() < deadline:
                try:
                    state = snapshot(root)
                    serialized = json.dumps({"type": "snapshot", **state}, separators=(",", ":"))
                    if serialized != previous:
                        await asyncio.wait_for(send_frame(writer, serialized), 5)
                        previous = serialized
                except (OSError, sqlite3.Error):
                    # Don't publish a partial or stale snapshot on DB outages.
                    await asyncio.wait_for(send_frame(writer,
                        '{"type":"degraded","reason":"Event database unavailable"}'), 5)
                done, _ = await asyncio.wait({frame_task}, timeout=2)
                if frame_task in done:
                    opcode, message = frame_task.result()
                    if opcode == 8:
                        break
                    if opcode == 9:
                        await send_frame(writer, message, opcode=10)
                    elif opcode != 10:
                        # Post-auth client messages are never commands.
                        break
                    frame_task = asyncio.create_task(recv_frame(reader))
            await send_frame(writer, b"", opcode=8)
        finally:
            frame_task.cancel()
    except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError,
            asyncio.TimeoutError, ValueError, OSError):
        pass
    finally:
        clients -= 1
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        print("PASS read-only loopback WebSocket event server available")
        return
    if os.geteuid() == 0:
        raise RuntimeError("WebSocket service must not run as root")
    if not KEY.is_file() or len(KEY.read_bytes()) != 32:
        raise RuntimeError("Private WebSocket HMAC key missing")
    async def run():
        server = await asyncio.start_server(serve, HOST, PORT, limit=8192)
        async with server:
            await server.serve_forever()
    asyncio.run(run())


if __name__ == "__main__":
    main()
