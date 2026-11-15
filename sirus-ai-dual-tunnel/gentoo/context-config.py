#!/usr/bin/env python3
"""Inspect, plan and explicitly change llama.cpp per-worker context on Gentoo/OpenRC.

No running service is restarted in --show or --plan. --apply requires root,
backs up the existing init script, and restores it if the worker fails health.
Context 0 means llama.cpp model-native (not infinite; may exhaust GPU memory).
"""
import argparse
import datetime as dt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

WORKERS = {
    "cpu": ("sirus-ai", 18482),
    "amd-vulkan": ("sirus-ai-amd", 18487),
    "nvidia-cuda": ("sirus-ai-nvidia", 18486),
}
CTX = re.compile(r"(?<!\S)--ctx-size\s+(?:\d+|\$\{[^}]+\})")
CMD = re.compile(r'^(command_args=")([^"]*)(")\s*$', flags=re.MULTILINE)


def parse_size(value):
    if value.lower() in ("model", "native", "auto"):
        return 0
    try:
        size = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Use model (native) or a non-negative integer")
    if size < 0:
        raise argparse.ArgumentTypeError("Context cannot be negative")
    return size


def read_service(worker, init_dir=Path("/etc/init.d")):
    name, port = WORKERS[worker]
    path = init_dir / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing missing/symlink service: {path}")
    original = path.read_text(encoding="utf-8")
    if original.count(f"--port {port}") != 1 or "llama-server" not in original:
        raise ValueError(f"Service {path} does not match expected llama.cpp worker/port")
    found = list(CMD.finditer(original))
    if len(found) != 1:
        raise ValueError(f"Expected one command_args assignment in {path}")
    return path, name, port, original, found[0]


def rewrite_context(original, match, size):
    args = match.group(2)
    occurrences = list(CTX.finditer(args))
    if len(occurrences) > 1:
        raise ValueError("Multiple --ctx-size options; refusing ambiguous edit")
    new_arg = f"--ctx-size {size}"
    if occurrences:
        changed = CTX.sub(new_arg, args, count=1)
    else:
        changed = args.rstrip() + " " + new_arg
    return original[:match.start(2)] + changed + original[match.end(2):]


def current_context(match):
    found = CTX.search(match.group(2))
    return found.group(0).split(maxsplit=1)[1] if found else "llama.cpp default"


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def service_command(name, verb):
    return subprocess.run(["rc-service", name, verb], check=False).returncode == 0


def apply(worker, size, init_dir=Path("/etc/init.d"), backup_dir=Path("/root")):
    if os.geteuid() != 0:
        raise ValueError("--apply must be run as root")
    path, name, port, previous, match = read_service(worker, init_dir)
    updated = rewrite_context(previous, match, size)
    if previous == updated:
        print("No change necessary.")
        return
    running = service_command(name, "status")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"{name}.context.{stamp}.bak"
    if backup.exists():
        raise ValueError(f"Backup already exists: {backup}")
    shutil.copy2(path, backup)
    print("Original service backed up to:", backup, flush=True)
    if running and not service_command(name, "stop"):
        raise ValueError("Could not stop service cleanly. Original file remains unchanged.")
    # Atomic replacement preserves the script's executable mode.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".context-", delete=False) as out:
            temp_path = Path(out.name)
            out.write(updated)
        os.chmod(temp_path, path.stat().st_mode & 0o777)
        os.replace(temp_path, path)
        temp_path = None
        if running:
            if not service_command(name, "start"):
                raise ValueError("OpenRC rejected the new worker configuration")
            for _ in range(90):
                if healthy(port):
                    break
                time.sleep(2)
            else:
                raise ValueError("Worker did not reach /health within 180 seconds")
    except Exception:
        shutil.copy2(backup, path)
        if running:
            service_command(name, "stop")
            if not service_command(name, "start"):
                print("URGENT: Previous service restored but failed to restart. Inspect OpenRC.", file=sys.stderr)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print(f"PASS {worker}: context set to {size} ({'native model context' if size == 0 else 'tokens'})")
    if not running:
        print("Service was stopped and remains stopped; the new setting takes effect on next start.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="Show current worker context settings")
    parser.add_argument("--plan", nargs=2, metavar=("WORKER", "CONTEXT"),
                        help="Show a proposed configuration with no changes")
    parser.add_argument("--apply", nargs=2, metavar=("WORKER", "CONTEXT"),
                        help="Explicitly save and activate a context size (root required)")
    args = parser.parse_args()
    if sum(bool(x) for x in (args.show, args.plan, args.apply)) != 1:
        parser.error("Choose exactly one of --show, --plan, --apply")
    if args.show:
        for worker in WORKERS:
            try:
                _, _, _, _, match = read_service(worker)
                print(f"{worker:<14} {current_context(match)}")
            except ValueError as error:
                print(f"{worker:<14} unavailable: {error}")
        return 0
    worker, raw = args.plan or args.apply
    if worker not in WORKERS:
        parser.error("Unknown worker. Choose: " + ", ".join(WORKERS))
    try:
        size = parse_size(raw)
        path, _, _, current, match = read_service(worker)
        edited = rewrite_context(current, match, size)
        print("Worker:", worker)
        print("Init script:", path)
        print("Current:", current_context(match))
        print("Requested:", "model-native (--ctx-size 0)" if size == 0 else size)
        print("Actual context is always finite; larger contexts require more RAM/VRAM.")
        print("Change preview:", CMD.search(edited).group(2))
        if args.plan:
            print("PLAN ONLY: no services or files modified.")
            return 0
        apply(worker, size)
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(2)
