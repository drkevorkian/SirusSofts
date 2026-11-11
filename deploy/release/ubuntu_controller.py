#!/usr/bin/env python3
"""Ubuntu-only root-owned worker for fixed, previously approved source releases.

Receives no HTTP, SSH, WebSocket, or command-line job arguments from visitors.
The queue in CMS SQLite is a REQUEST, never arbitrary commands. Independently
verifies source hashes, approved plan, host OS and exact destination allowlist.
"""
import argparse
import json
import os
import pwd
import subprocess
from pathlib import Path
import signal
import sqlite3
import sys
import time

from source_executor import (
    CMS_DB, ReleaseError, apply, check_host, load_release,
    release_from_backup, rollback,
)

RUNNING = True


def stop(*_):
    global RUNNING
    RUNNING = False


def connect(path=CMS_DB):
    if not path.is_file() or path.is_symlink():
        raise ReleaseError("CMS release queue database is unavailable")
    db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    return db


def heartbeat(db, now=None):
    now = int(time.time()) if now is None else int(now)
    try:
        db.execute("""INSERT INTO cms_source_controller_health(host,last_seen,state)
            VALUES('ubuntu-primary',?,'ready')
            ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen,state='ready'""",
            (now,))
    except sqlite3.OperationalError:
        # Schema is installed by CMS. Do not create DB tables as root.
        pass


def recover_interrupted(db, now=None):
    now = int(time.time()) if now is None else int(now)
    try:
        db.execute("""UPDATE cms_source_deploy_requests
            SET state='interrupted',updated_at=?,
                error='Service stopped during deployment; operator review required'
            WHERE host='ubuntu-primary' AND state='running'""", (now,))
    except sqlite3.OperationalError:
        # The CMS creates this table on first visit to /admin/updates.
        pass


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


def finish(db, request, state, *, result=None, error="", now=None):
    now = int(time.time()) if now is None else int(now)
    if state not in ("done","failed"):
        raise ValueError("Invalid terminal deployment state")
    db.execute("""UPDATE cms_source_deploy_requests
                SET state=?,updated_at=?,result_json=?,error=?
                WHERE id=? AND state='running'""",
               (state,now,json.dumps(result or {},separators=(",",":")),
                error[:190],request["id"]))


def process_one(db, *, loader=load_release, restorer=release_from_backup,
                installer=apply, undo=rollback, now=None):
    try:
        request = claim(db,now=now)
    except sqlite3.OperationalError:
        return None
    if request is None:
        return None
    result = None
    failure = ""
    state = "done"
    try:
        ident = request["plan_id"]
        if request["action"] == "apply":
            release = loader("ubuntu-primary", ident)
            result = installer(release)
        elif request["action"] == "rollback":
            release = restorer("ubuntu-primary", ident)
            result = undo(release)
        else:
            raise ReleaseError("Unsupported deployment action")
        if not isinstance(result,dict) or result.get("state") not in ("verified","rolled_back"):
            raise ReleaseError("Executor returned an unverified state")
    except Exception as exc:
        state = "failed"
        # Never echo arbitrary exception text from a privileged process.
        failure = type(exc).__name__ + ": refer to root-owned release journal"
    finish(db,request,state,result=result,error=failure,now=now)
    print(f"release={request['plan_id']} request={request['id']} "
          f"action={request['action']} state={state}",flush=True)
    return state


def database_helper(action, *, request_id="", state="", result=None, error="",
                    run=subprocess.run):
    """Delegate ALL writeable CMS SQLite operations to the Hestia site owner."""
    if action not in ("recover", "next", "finish"):
        raise ReleaseError("Unsupported SQLite bridge operation")
    account = pwd.getpwnam("sirusweb")
    executable = Path(__file__).with_name("db_queue.py")
    if executable.is_symlink() or not executable.is_file():
        raise ReleaseError("Trusted CMS queue helper is missing")
    args = ["/usr/bin/python3", "-B", str(executable), action]
    payload = None
    if action == "finish":
        if (len(request_id) != 32 or any(ch not in "0123456789abcdef" for ch in request_id)
                or state not in ("done", "failed")):
            raise ReleaseError("Invalid queue completion identifier")
        args.extend(["--request-id", request_id, "--state", state])
        payload = json.dumps({"result": result or {}, "error": error[:190]})

    def drop_privileges():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    completed = run(args, input=payload, capture_output=True, text=True,
                    timeout=15, check=False, preexec_fn=drop_privileges)
    if completed.returncode != 0 or len(completed.stdout) > 8192:
        raise ReleaseError("Unprivileged CMS queue helper failed")
    try:
        response = json.loads(completed.stdout)
    except ValueError:
        raise ReleaseError("CMS queue helper returned invalid data") from None
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise ReleaseError("CMS queue helper rejected request")
    return response


def process_privileged(*, queue=database_helper, loader=load_release,
                       restorer=release_from_backup, installer=apply,
                       undo=rollback):
    """One root-managed deployment with unprivileged persistence only."""
    response = queue("next")
    request = response.get("request")
    if request is None:
        return None
    if (not isinstance(request, dict)
            or request.get("host") != "ubuntu-primary"
            or request.get("action") not in ("apply", "rollback")
            or not isinstance(request.get("plan_id"), str)
            or not isinstance(request.get("id"), str)):
        raise ReleaseError("Invalid request returned from unprivileged queue")
    state = "done"
    result = {}
    error = ""
    try:
        if request["action"] == "apply":
            release = loader("ubuntu-primary", request["plan_id"])
            result = installer(release)
        else:
            release = restorer("ubuntu-primary", request["plan_id"])
            result = undo(release)
        if not isinstance(result, dict) or result.get("state") not in ("verified", "rolled_back"):
            raise ReleaseError("Executor returned an unverified deployment state")
    except Exception as exc:
        state = "failed"
        error = type(exc).__name__ + ": inspect root-owned release journal"
        result = {}
    queue("finish", request_id=request["id"], state=state,
          result=result, error=error)
    print(f"release={request['plan_id']} request={request['id']} "
          f"action={request['action']} state={state}", flush=True)
    return state


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--loop",action="store_true")
    parser.add_argument("--interval",type=int,default=4)
    args=parser.parse_args()
    if args.check:
        print("PASS fixed Ubuntu source-release controller available")
        return
    if not (args.once or args.loop) or args.interval not in range(2,61):
        parser.error("Choose --once or --loop and interval 2..60 seconds")
    if os.geteuid() != 0:
        raise ReleaseError("Ubuntu release controller requires dedicated root service")
    check_host("ubuntu-primary")
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    # The privileged worker MUST NOT open writable CMS SQLite connections:
    # doing so could create root-owned WAL/SHM files and break the website.
    database_helper("recover")
    while RUNNING:
        process_privileged()
        if args.once:
            break
        time.sleep(args.interval)


if __name__=="__main__":
    try:
        main()
    except (ReleaseError,sqlite3.Error,OSError) as error:
        print("FAIL Ubuntu release controller: "+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
