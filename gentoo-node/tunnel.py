#!/usr/bin/env python3
"""Persistent outbound Gentoo -> Ubuntu web reverse SSH with strict host-key pinning."""
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
import threading
import time

STOP = threading.Event()
CONFIG = "/etc/sirus-gentoo-node/tunnel.json"
REMOTE_PORTS = (18480, 18481)
LOCAL_PORTS = (18080, 18081)


def validate(raw: dict) -> dict:
    if not isinstance(raw,dict) or set(raw) != {
        "host","ssh_port","user","identity","known_hosts"
    }:
        raise ValueError("Unexpected tunnel configuration fields")
    host=raw["host"]
    if not isinstance(host,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}",host):
        raise ValueError("Invalid Ubuntu host")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}",str(raw["user"])):
        raise ValueError("Invalid restricted SSH user")
    if type(raw["ssh_port"]) is not int or not 1 <= raw["ssh_port"] <= 65535:
        raise ValueError("Invalid SSH port")
    for field in ("identity","known_hosts"):
        file=raw[field]
        if not isinstance(file,str) or not file.startswith("/") or "\0" in file:
            raise ValueError(field+" must be an absolute path")
    return raw


def private_files(cfg:dict) -> None:
    for field in ("identity","known_hosts"):
        path=Path(cfg[field])
        metadata=path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or
            metadata.st_mode & 0o077 or
            metadata.st_uid != os.geteuid() or
            not path.read_bytes()):
            raise PermissionError(field+" must be a private regular file owned by the service")


def ssh_command(cfg:dict) -> list[str]:
    return ["/usr/bin/ssh","-F","/dev/null","-N","-T",
       "-o","BatchMode=yes","-o","NumberOfPasswordPrompts=0",
       "-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=yes",
       "-o","HostKeyAlgorithms=ssh-ed25519",
       "-o","UserKnownHostsFile="+cfg["known_hosts"],
       "-o","GlobalKnownHostsFile=/dev/null",
       "-o","ExitOnForwardFailure=yes",
       "-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3",
       "-o","TCPKeepAlive=yes","-o","ConnectTimeout=10",
       "-o","ConnectionAttempts=1","-o","ForwardAgent=no",
       "-o","ForwardX11=no","-o","RequestTTY=no",
       "-o","ControlMaster=no",
       "-i",cfg["identity"],"-p",str(cfg["ssh_port"]),
       "-l",cfg["user"],
       "-R","127.0.0.1:18480:127.0.0.1:18080",
       "-R","127.0.0.1:18481:127.0.0.1:18081",
       cfg["host"]]


def run(cfg:dict) -> int:
    delay=2
    while not STOP.is_set():
        process=None
        started=time.monotonic()
        try:
            process=subprocess.Popen(ssh_command(cfg),stdin=subprocess.DEVNULL,
                                     start_new_session=True)
            print("Gentoo web reverse tunnel started, PID",process.pid,flush=True)
            while process.poll() is None and not STOP.wait(3):
                pass
            if process.poll() is not None and not STOP.is_set():
                print("Reverse tunnel closed; reconnecting",flush=True)
        except OSError as error:
            print("SSH transport error:",type(error).__name__,flush=True)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if STOP.is_set():
            break
        delay=2 if time.monotonic()-started>300 else min(delay*2,60)
        STOP.wait(delay)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=CONFIG)
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--validate",action="store_true")
    args=parser.parse_args(argv)
    if args.check:
        cfg={"host":"74.208.40.187","ssh_port":22,"user":"sirus-tunnel",
             "identity":"/etc/sirus-gentoo-node/tunnel_ed25519",
             "known_hosts":"/etc/sirus-gentoo-node/tunnel_known_hosts"}
        command=ssh_command(validate(cfg))
        assert command.count("-R")==2 and "127.0.0.1:18480:127.0.0.1:18080" in command
        assert "StrictHostKeyChecking=yes" in command
        print("PASS two fixed loopback reverse tunnels and strict SSH host-key pinning")
        return 0
    cfg=validate(json.loads(Path(args.config).read_text()))
    private_files(cfg)
    if not Path("/usr/bin/ssh").is_file():
        raise RuntimeError("OpenSSH client is required")
    if args.validate:
        print("PASS tunnel key, pinned host key, and SSH configuration")
        return 0
    signal.signal(signal.SIGTERM,lambda *_: STOP.set())
    signal.signal(signal.SIGINT,lambda *_: STOP.set())
    return run(cfg)


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError,RuntimeError,ValueError,PermissionError) as error:
        print("FAIL Gentoo tunnel:",str(error),file=sys.stderr)
        raise SystemExit(1)
