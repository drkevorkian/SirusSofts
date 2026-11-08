#!/usr/bin/env python3
"""SirusSofts outbound Gentoo/OpenRC heartbeat. No remote command executor."""
from __future__ import annotations
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import urllib.parse
import urllib.request

VERSION = "0.1.0"
NODE_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    node_id = config.get("node_id")
    address = config.get("coordinator_url")
    key_file = config.get("key_file")
    period = config.get("period_seconds", 60)
    if not isinstance(node_id, str) or not NODE_RE.fullmatch(node_id):
        raise ValueError("Invalid node_id")
    if not isinstance(address, str):
        raise ValueError("Missing coordinator_url")
    target = urllib.parse.urlsplit(address)
    if (target.scheme != "https" or not target.hostname or target.username or
            target.password or target.query or target.fragment or
            target.path != "/node-heartbeat.php"):
        raise ValueError("Coordinator requires verified HTTPS and /node-heartbeat.php")
    if not isinstance(key_file, str) or not os.path.isabs(key_file):
        raise ValueError("key_file must be absolute")
    if type(period) is not int or not 15 <= period <= 600:
        raise ValueError("period_seconds must be 15..600")
    return {"node_id": node_id, "coordinator_url": address,
            "key_file": key_file, "period_seconds": period}


def load_key(path: str) -> bytes:
    meta = os.lstat(path)
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise PermissionError("Key must be a private regular file, mode 0400 or 0600")
    key = Path(path).read_bytes()
    if len(key) != 32:
        raise ValueError("Heartbeat HMAC key must be 32 bytes")
    return key


def memory_bytes() -> int:
    with open("/proc/meminfo", encoding="ascii") as stream:
        for line in stream:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("Cannot read MemTotal")


def gpu_vendors(directory: Path = Path("/sys/class/drm")) -> list[str]:
    found = set()
    for entry in directory.glob("card[0-9]*/device/vendor"):
        try:
            value = entry.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeError):
            continue
        if re.fullmatch(r"0x[0-9a-f]{4}", value):
            found.add(value)
    return sorted(found)[:16]


def init_system() -> str:
    if Path("/run/openrc").exists():
        return "openrc"
    if Path("/run/systemd/system").exists():
        return "systemd"
    return "other"


def distro() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("ID="):
                value = line[3:].strip('"').lower()
                return value if value in ("gentoo", "ubuntu") else "linux"
    except OSError:
        pass
    return "linux"


def telemetry(node_id: str, when: int | None = None) -> dict:
    return {"node_id": node_id, "sent_at": int(time.time()) if when is None else when,
            "nonce": secrets.token_hex(16), "os": distro(), "init": init_system(),
            "agent_version": VERSION, "cpu_threads": max(1, os.cpu_count() or 1),
            "ram_bytes": memory_bytes(), "gpu_vendors": gpu_vendors(),
            "capabilities": ["compute", "index"]}


def signed_request(config: dict, key: bytes, when: int | None = None):
    data = json.dumps(telemetry(config["node_id"], when), separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key, data, hashlib.sha256).hexdigest()
    return urllib.request.Request(config["coordinator_url"], data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Sirus-Node-Signature": signature,
                 "User-Agent": "SirusNode/" + VERSION})


def send_once(config: dict, key: bytes) -> dict:
    # Never bypass certificate or hostname verification.
    with urllib.request.urlopen(signed_request(config, key), timeout=12) as response:
        if response.status != 200:
            raise RuntimeError("Coordinator returned HTTP " + str(response.status))
        result = json.loads(response.read(4096))
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Coordinator rejected heartbeat")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/sirus-node/config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--probe", action="store_true", help="Hardware report without network")
    args = parser.parse_args(argv)
    if args.probe:
        print(json.dumps(telemetry("probe-node"), indent=2))
        return 0
    config = validate_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
    key = load_key(config["key_file"])
    delay = config["period_seconds"]
    while True:
        try:
            result = send_once(config, key)
            print("Heartbeat accepted:", result.get("node_id"), flush=True)
            delay = config["period_seconds"]
        except Exception as error:
            print("Heartbeat error:", type(error).__name__, str(error), file=sys.stderr, flush=True)
            if args.once:
                return 1
            delay = min(max(delay * 2, 30), 300)
        if args.once:
            return 0
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
