#!/usr/bin/env python3
"""Outbound, persistent Gentoo -> Ubuntu reverse tunnel watchdog.

Python supervises the native OpenSSH client. It NEVER implements SSH crypto,
handles passwords, opens an inbound public port, or accepts remote commands.
Ubuntu must separately verify its loopback-only endpoint before reporting READY.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
STATUS_DIR = Path("/run/sirus-node")
SHUTDOWN = threading.Event()


def port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("SSH ports must be integers in 1..65535")
    return value


def validate_config(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Tunnel settings must be a JSON object")
    if set(data) - {
        "remote_host", "remote_user", "ssh_port", "reverse_port",
        "key_file", "known_hosts_file", "local_target_port",
        "alive_interval", "alive_count"
    }:
        raise ValueError("Unrecognized tunnel configuration setting")
    host, user = data.get("remote_host"), data.get("remote_user")
    if not isinstance(host, str) or not HOST.fullmatch(host) or ".." in host:
        raise ValueError("remote_host must be a reviewed DNS hostname or IPv4 address")
    if not isinstance(user, str) or not USER.fullmatch(user):
        raise ValueError("Invalid restricted SSH username")
    identity, known = data.get("key_file"), data.get("known_hosts_file")
    if not isinstance(identity, str) or not isinstance(known, str):
        raise ValueError("key_file and known_hosts_file are required")
    for path in (identity, known):
        if not path.startswith("/") or "\x00" in path or path in ("/", "/dev/null"):
            raise ValueError("SSH key and host pinning require absolute private files")
    period, attempts = data.get("alive_interval", 15), data.get("alive_count", 3)
    if type(period) is not int or not 5 <= period <= 60:
        raise ValueError("Keepalive period must be 5..60 seconds")
    if type(attempts) is not int or not 1 <= attempts <= 6:
        raise ValueError("Keepalive retry count must be 1..6")
    return {
        "remote_host": host, "remote_user": user,
        "ssh_port": port(data.get("ssh_port", 22)),
        "reverse_port": port(data.get("reverse_port", 22122)),
        "local_target_port": port(data.get("local_target_port", 22)),
        "key_file": identity, "known_hosts_file": known,
        "alive_interval": period, "alive_count": attempts
    }


def validate_private_files(config: dict) -> None:
    for key in ("key_file", "known_hosts_file"):
        filename = config[key]
        metadata = os.lstat(filename)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or (
            metadata.st_mode & 0o077
        ):
            raise PermissionError(key + " must be a private regular file owned by the tunnel user")
        if not Path(filename).read_bytes():
            raise ValueError(key + " is empty")


def ssh_command(cfg: dict) -> list[str]:
    # Fixed bind address prevents a reverse port from becoming publicly reachable.
    remote = "127.0.0.1:" + str(cfg["reverse_port"])
    local = "127.0.0.1:" + str(cfg["local_target_port"])
    return [
        "/usr/bin/ssh", "-F", "/dev/null", "-N", "-T",
        "-o", "BatchMode=yes",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + cfg["known_hosts_file"],
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=" + str(cfg["alive_interval"]),
        "-o", "ServerAliveCountMax=" + str(cfg["alive_count"]),
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
        "-o", "ForwardAgent=no",
        "-o", "ForwardX11=no",
        "-o", "RequestTTY=no",
        "-o", "ControlMaster=no",
        "-i", cfg["key_file"],
        "-p", str(cfg["ssh_port"]), "-l", cfg["remote_user"],
        "-R", remote + ":" + local,
        cfg["remote_host"]
    ]


def status(state: str, reconnects: int, child: subprocess.Popen | None) -> None:
    if not STATUS_DIR.is_dir():
        return  # OpenRC normally creates the runtime directory.
    data = {
        "state": state, "at": int(time.time()), "reconnects": reconnects,
        "ssh_process_alive": child is not None and child.poll() is None,
        "note": "SSH process status only. Verify actual remote port on Ubuntu."
    }
    with tempfile.NamedTemporaryFile("w", dir=STATUS_DIR, encoding="utf8",
                                     prefix=".tunnel-", delete=False) as stream:
        tmp = Path(stream.name)
        os.chmod(tmp, 0o600)
        json.dump(data, stream)
        stream.write("\n")
    os.replace(tmp, STATUS_DIR / "tunnel-status.json")


def terminate(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)


def supervise(cfg: dict) -> int:
    reconnects, delay = 0, 3
    while not SHUTDOWN.is_set():
        start = time.monotonic()
        child = None
        try:
            child = subprocess.Popen(
                ssh_command(cfg), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, start_new_session=True
            )
            print("SSH tunnel process started", flush=True)
            while child.poll() is None and not SHUTDOWN.wait(10):
                status("transport-running", reconnects, child)
            if SHUTDOWN.is_set():
                terminate(child)
                break
            print("SSH tunnel exited; reconnect scheduled", file=sys.stderr, flush=True)
        except (OSError, subprocess.SubprocessError) as error:
            print("SSH tunnel process failed: " + type(error).__name__,
                  file=sys.stderr, flush=True)
        finally:
            if child is not None:
                terminate(child)
            status("disconnected", reconnects, None)
        reconnects += 1
        stable = time.monotonic() - start >= 600
        delay = 3 if stable else min(delay * 2, 60)
        SHUTDOWN.wait(delay)
    status("stopped", reconnects, None)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/sirus-node/tunnel.json")
    parser.add_argument("--check", action="store_true",
                        help="Check script without files, credentials or network")
    parser.add_argument("--validate", action="store_true",
                        help="Validate local tunnel config/key/host pinning only")
    args = parser.parse_args(argv)
    if args.check:
        print("PASS native OpenSSH reverse tunnel supervisor available; no changes")
        return 0
    cfg = validate_config(json.loads(Path(args.config).read_text(encoding="utf8")))
    validate_private_files(cfg)
    if not Path("/usr/bin/ssh").is_file():
        raise RuntimeError("OpenSSH client required at /usr/bin/ssh")
    if args.validate:
        print("PASS private SSH identity, host key pinning and localhost-only reverse port")
        return 0
    signal.signal(signal.SIGINT, lambda *_: SHUTDOWN.set())
    signal.signal(signal.SIGTERM, lambda *_: SHUTDOWN.set())
    return supervise(cfg)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print("FAIL SSH tunnel supervisor: " + str(error), file=sys.stderr)
        sys.exit(1)
