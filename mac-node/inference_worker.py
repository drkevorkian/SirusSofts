#!/usr/bin/env python3
"""Opt-in Mac CPU inference behind a pinned, loopback-only SSH reverse forward.

No public listener, no remote commands, no automatic model download.
This independent user service does not change the existing heartbeat agent.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

PORT = 18588
LABEL = "com.sirussofts.mac-inference"
DEFAULT_CONFIG = Path.home() / ".config/sirus-mac-node/inference.json"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def absolute_file(value, *, private=False, executable=False):
    if not isinstance(value, str):
        raise ValueError("Expected absolute local file path")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unsafe local file: " + path.name)
    if private and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError("Identity must be owned by this user and private")
    if executable and not os.access(path, os.X_OK):
        raise ValueError("llama-server must be executable")
    return str(path.resolve())


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    if not isinstance(cfg, dict):
        raise ValueError("Expected inference configuration")
    host = cfg.get("gentoo_host")
    if not isinstance(host, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ValueError("Use an explicit Gentoo DNS name or IPv4 address")
    if cfg.get("ssh_user") != "sirus-mac-inference":
        raise ValueError("Use the dedicated restricted SSH account")
    port = cfg.get("ssh_port", 22)
    threads = cfg.get("threads", 2)
    context = cfg.get("context", 4096)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid SSH port")
    if type(threads) is not int or not 1 <= threads <= 4:
        raise ValueError("CPU threads must be 1..4")
    if type(context) is not int or not 2048 <= context <= 8192:
        raise ValueError("Context must be 2048..8192")
    digest = cfg.get("model_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Set the approved GGUF SHA-256 from Gentoo")
    model_id = cfg.get("model_id", "sirussofts-coder")
    if not isinstance(model_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", model_id):
        raise ValueError("Invalid model alias")
    return dict(host=host, user=cfg["ssh_user"], port=port,
                threads=threads, context=context, model_id=model_id,
                digest=digest,
                binary=absolute_file(cfg.get("llama_server"), executable=True),
                model=absolute_file(cfg.get("model_file")),
                identity=absolute_file(cfg.get("identity_file"), private=True),
                known_hosts=absolute_file(cfg.get("known_hosts_file")))


def verify_model(cfg):
    h = hashlib.sha256()
    with open(cfg["model"], "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    if h.hexdigest() != cfg["digest"]:
        raise ValueError("GGUF hash does not match the approved model")


def commands(cfg):
    llama = [cfg["binary"], "--model", cfg["model"], "--alias", cfg["model_id"],
             "--host", "127.0.0.1", "--port", str(PORT),
             "--ctx-size", str(cfg["context"]), "--threads", str(cfg["threads"]),
             "--n-gpu-layers", "0", "--parallel", "1"]
    ssh = ["/usr/bin/ssh", "-F", "/dev/null", "-N", "-T",
           "-p", str(cfg["port"]), "-i", cfg["identity"],
           "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "UserKnownHostsFile=" + cfg["known_hosts"],
           "-o", "GlobalKnownHostsFile=/dev/null",
           "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
           "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=10",
           "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
           "-R", f"127.0.0.1:{PORT}:127.0.0.1:{PORT}",
           cfg["user"] + "@" + cfg["host"]]
    return llama, ssh


def ready(cfg):
    try:
        with OPENER.open(f"http://127.0.0.1:{PORT}/health", timeout=2) as response:
            if response.status != 200:
                return False
        with OPENER.open(f"http://127.0.0.1:{PORT}/v1/models", timeout=2) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                return False
            rows = json.loads(raw).get("data", [])
        return any(isinstance(row, dict) and row.get("id") == cfg["model_id"] for row in rows)
    except (OSError, ValueError, AttributeError):
        return False


def supervise(cfg):
    verify_model(cfg)
    llama, ssh = commands(cfg)
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # Never adopt a pre-existing, possibly unrelated model listener.
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", PORT))
        model = subprocess.Popen(llama, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(model)
        until = time.monotonic() + 180
        while not stopping and model.poll() is None and time.monotonic() < until:
            if ready(cfg):
                break
            time.sleep(2)
        else:
            return 0 if stopping else 1
        if stopping or model.poll() is not None:
            return 0 if stopping else 1
        tunnel = subprocess.Popen(ssh, stdin=subprocess.DEVNULL)
        children.append(tunnel)
        print("Mac model ready; starting pinned private SSH forward", flush=True)
        while not stopping and all(p.poll() is None for p in children):
            time.sleep(2)
        return 0 if stopping else 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def install(config):
    if os.getuid() == 0:
        raise ValueError("Install as the regular Mac user, not root")
    app = Path.home() / "Library/Application Support/SirusMacNode"
    agents = Path.home() / "Library/LaunchAgents"
    app.mkdir(parents=True, exist_ok=True)
    agents.mkdir(parents=True, exist_ok=True)
    dest = app / "inference_worker.py"
    if dest.resolve() != Path(__file__).resolve():
        shutil.copyfile(__file__, dest)
    os.chmod(dest, 0o600)
    plist = agents / (LABEL + ".plist")
    with plist.open("wb") as f:
        plistlib.dump({"Label": LABEL,
                       "ProgramArguments": [sys.executable, str(dest), "--run",
                                            "--config", str(Path(config).resolve())],
                       "RunAtLoad": True,
                       "KeepAlive": {"SuccessfulExit": False},
                       "ThrottleInterval": 30,
                       "WorkingDirectory": str(app),
                       "StandardOutPath": str(app / "inference.stdout.log"),
                       "StandardErrorPath": str(app / "inference.stderr.log")}, f)
    os.chmod(plist, 0o600)
    print("Installed independent Mac inference LaunchAgent:", plist)
    print("Start using launchctl bootstrap gui/$(id -u) with that plist path.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ("plan", "verify-model", "install", "run"):
        actions.add_argument("--" + action, action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.plan:
        print("PASS: configuration; CPU-only model, loopback port", PORT)
        print("Target: restricted Gentoo SSH account; pinned host key required")
        print("No files, services or model downloads changed.")
    elif args.verify_model:
        verify_model(cfg)
        print("PASS: local GGUF matches the configured approved SHA-256")
    elif args.install:
        verify_model(cfg)
        install(args.config)
    else:
        return supervise(cfg)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print("Mac inference setup failed:", str(error), file=sys.stderr)
        raise SystemExit(1)
