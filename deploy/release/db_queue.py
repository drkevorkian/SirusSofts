#!/usr/bin/env python3
"""Unprivileged SQLite transport for the SirusSofts Ubuntu source controller.

Run only as the Hestia site owner (sirusweb). This helper does not execute
source code, restart services, load root-owned backups, or require root.
"""
import argparse
import json
import os
import pwd
import sys

from ubuntu_controller import connect, heartbeat, recover_interrupted, claim, finish

ALLOWED_STATES = ("done", "failed")


def do_action(db, action, *, request_id="", state="", result=None, error=""):
    if action == "recover":
        recover_interrupted(db)
        return {"ok": True}
    if action == "next":
        heartbeat(db)
        return {"ok": True, "request": claim(db)}
    if action == "finish":
        if (not isinstance(request_id, str) or len(request_id) != 32
                or any(ch not in "0123456789abcdef" for ch in request_id)
                or state not in ALLOWED_STATES
                or not isinstance(result, dict)
                or not isinstance(error, str)):
            raise ValueError("Invalid source deployment completion request")
        finish(db, {"id": request_id}, state, result=result, error=error)
        return {"ok": True}
    raise ValueError("Unknown database helper action")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("recover", "next", "finish"))
    parser.add_argument("--request-id", default="")
    parser.add_argument("--state", default="")
    args = parser.parse_args()
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "sirusweb":
        raise PermissionError("Deployment database helper must run as sirusweb")
    payload = {}
    if args.action == "finish":
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError("Deployment result exceeds 8 KiB")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) - {"result", "error"}:
            raise ValueError("Invalid completion payload")
    db = connect()
    try:
        output = do_action(db, args.action,
                           request_id=args.request_id, state=args.state,
                           result=payload.get("result", {}),
                           error=payload.get("error", ""))
        print(json.dumps(output, separators=(",", ":")))
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, PermissionError) as exc:
        print("FAIL source queue helper: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
