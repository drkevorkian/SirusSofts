#!/usr/bin/env python3
"""Unprivileged SQLite transport for the SirusSofts Ubuntu source controller.

Run only as the Hestia site owner (sirusweb). This helper does not execute
source code, restart services, load root-owned backups, or require root.
"""
import argparse
import json
import os
import pwd
import sqlite3
from pathlib import Path
import sys
import time

DB = Path("/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3")


def connect(path=DB):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("CMS release queue database unavailable")
    db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    return db


def heartbeat(db, now=None):
    now = int(time.time()) if now is None else int(now)
    db.execute("""INSERT INTO cms_source_controller_health(host,last_seen,state)
        VALUES('ubuntu-primary',?,'ready')
        ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen,state='ready'""",
        (now,))


def recover_interrupted(db, now=None):
    now = int(time.time()) if now is None else int(now)
    db.execute("""UPDATE cms_source_deploy_requests
        SET state='interrupted',updated_at=?,
            error='Service stopped during deployment; operator review required'
        WHERE host='ubuntu-primary' AND state='running'""", (now,))


def claim(db, now=None):
    now = int(time.time()) if now is None else int(now)
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("""SELECT id,plan_id,host,action FROM cms_source_deploy_requests
                    WHERE host='ubuntu-primary' AND state='queued'
                    ORDER BY created_at,id LIMIT 1""").fetchone()
        if row:
            updated = db.execute("""UPDATE cms_source_deploy_requests
                    SET state='running',updated_at=? WHERE id=? AND state='queued'""",
                                 (now,row["id"]))
            if updated.rowcount != 1:
                row = None
        db.commit()
        return dict(row) if row else None
    except BaseException:
        db.rollback()
        raise


def finish(db, request_id, state, *, result=None, error="", now=None):
    if state not in ("done","failed"):
        raise ValueError("Invalid terminal deployment state")
    now = int(time.time()) if now is None else int(now)
    db.execute("""UPDATE cms_source_deploy_requests
               SET state=?,updated_at=?,result_json=?,error=?
               WHERE id=? AND state='running'""",
               (state,now,json.dumps(result or {},separators=(",",":")),
                error[:190],request_id))


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
        finish(db, request_id, state, result=result, error=error)
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
