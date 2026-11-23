#!/usr/bin/env python3
"""Local read-only RSS inventory plus allowlisted user-session graceful quit.

Never kills by PID, never sends signals to system daemons, never runs via web,
and never calls sudo. Root escalation is reserved for a future reviewed helper.
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def inventory():
    output = subprocess.run(["/bin/ps", "-axo", "pid=,uid=,rss=,comm="],
                            capture_output=True, text=True, timeout=5, check=True)
    rows = []
    for line in output.stdout.splitlines()[:4096]:
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s*$", line)
        if not match:
            continue
        pid, uid, rss, command = match.groups()
        rows.append({"pid": int(pid), "uid": int(uid), "rss_bytes": int(rss)*1024,
                     "executable": Path(command).name[:96],
                     "actionable": False})
    return sorted(rows, key=lambda row: row["rss_bytes"], reverse=True)[:100]


def graceful_quit(bundle_id, allowed_ids, interactive=False):
    """Apps only: AppleScript quit permits macOS to ask about unsaved changes."""
    if not interactive or not isinstance(bundle_id, str) or \
       not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{2,127}", bundle_id) or \
       bundle_id not in allowed_ids:
        raise PermissionError("Application not explicitly authorized")
    # AppleScript receives a validated bundle ID, not a shell command.
    script = 'tell application id "' + bundle_id + '" to quit'
    return subprocess.run(["/usr/bin/osascript", "-e", script],
                          capture_output=True, text=True, timeout=20, check=False).returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--quit-bundle-id")
    parser.add_argument("--config", default=str(Path.home()/".config/sirus-mac-node/config.json"))
    args = parser.parse_args()
    if args.list:
        print(json.dumps(inventory(), indent=2))
        return
    if args.quit_bundle_id:
        config = json.loads(Path(args.config).read_text())
        allowed = config.get("allowlisted_quit_bundle_ids", [])
        if not isinstance(allowed, list) or len(allowed) > 16 or \
           not all(isinstance(x, str) for x in allowed):
            raise ValueError("Invalid administrator allowlist")
        if os.isatty(0):
            answer = input("Request graceful quit of " + args.quit_bundle_id + "? [yes/NO] ")
        else:
            answer = ""
        if answer != "yes":
            raise PermissionError("Interactive approval required")
        raise SystemExit(graceful_quit(args.quit_bundle_id, allowed, interactive=True))
    parser.error("Choose --list or --quit-bundle-id")


if __name__ == "__main__":
    main()
