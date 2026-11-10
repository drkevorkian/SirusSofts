#!/usr/bin/env python3
"""Ubuntu worker for explicitly approved, read-only SirusSofts node jobs."""
import argparse
import datetime
import json
import os
import subprocess
from pathlib import Path
import signal
import sqlite3
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

DB = "/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/work.sqlite3"
RUN = True

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

def health_snapshot():
    with open("/proc/meminfo", encoding="ascii") as stream:
        mem = {}
        for line in stream:
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, val, *_ = line.split()
                mem[key.rstrip(":")] = int(val) * 1024
    uptime = int(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0]))
    disk = os.statvfs("/")
    return {
        "server": "ubuntu-primary",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cpu_threads": os.cpu_count() or 1,
        "load_1": round(os.getloadavg()[0], 2),
        "uptime_seconds": uptime,
        "memory_total_bytes": mem.get("MemTotal", 0),
        "memory_used_bytes": max(0, mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)),
        "disk_total_bytes": disk.f_blocks * disk.f_frsize,
        "disk_used_bytes": (disk.f_blocks - disk.f_bfree) * disk.f_frsize,
    }

def site_health():
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    req = Request("https://sirussofts.com/", headers={
        "User-Agent": "SirusSofts-WorkExecutor/0.1"
    }, method="GET")
    try:
        with opener.open(req, timeout=8) as response:
            code = response.status
    except HTTPError as error:
        code = error.code
    except (OSError, URLError, TimeoutError):
        return {"target": "sirussofts.com", "reachable": False, "http_status": 0}
    return {"target": "sirussofts.com", "reachable": code == 200, "http_status": code}

def package_update_check():
    """Read-only APT simulation, no locks or installation triggered."""
    try:
        run = subprocess.run(
            ["/usr/bin/apt-get", "-s", "upgrade"],
            capture_output=True, text=True, timeout=35, check=False,
            env={"PATH":"/usr/bin:/bin", "LC_ALL":"C", "DEBIAN_FRONTEND":"noninteractive"},
        )
    except (OSError, subprocess.SubprocessError):
        return {"system":"ubuntu", "available":False, "reason":"APT simulation unavailable"}
    if run.returncode:
        return {"system":"ubuntu", "available":False, "reason":"APT simulation failed",
                "exit_code":run.returncode}
    lines = run.stdout.splitlines()
    installs = [line for line in lines if line.startswith("Inst ")]
    kept = [line for line in lines if line.startswith("Conf ")]
    return {"system":"ubuntu", "available":True, "upgrade_count":len(installs),
            "package_preview":[line[5:180] for line in installs[:25]],
            "truncated":len(installs)>25, "simulation_only":True}


def execute(kind):
    if kind == "health_snapshot":
        return health_snapshot()
    if kind == "site_health":
        return site_health()
    if kind == "package_update_check":
        return package_update_check()
    raise ValueError("Unsupported work type")

def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS work_jobs (
        request_id TEXT PRIMARY KEY,
        node_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed')),
        result_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        claimed_at INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS work_pending ON work_jobs(state,created_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS work_nonces (
        node_id TEXT NOT NULL, nonce TEXT NOT NULL, seen_at INTEGER NOT NULL,
        PRIMARY KEY(node_id,nonce)
    )""")
    db.commit()

def claim(db, now):
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""UPDATE work_jobs SET state='queued',updated_at=?
                      WHERE node_id='ubuntu-primary' AND state='running' AND claimed_at<? AND attempts<3""",
                   (now, now-90))
        db.execute("""UPDATE work_jobs SET state='failed',
                       error='Worker timed out repeatedly',updated_at=?
                      WHERE node_id='ubuntu-primary' AND state='running' AND claimed_at<? AND attempts>=3""",
                   (now, now-90))
        item = db.execute("""SELECT request_id,node_id,kind FROM work_jobs
                             WHERE node_id='ubuntu-primary' AND state='queued' ORDER BY created_at,request_id LIMIT 1""").fetchone()
        if not item:
            db.commit()
            return None
        db.execute("""UPDATE work_jobs SET state='running',claimed_at=?,updated_at=?,attempts=attempts+1
                      WHERE request_id=? AND state='queued'""",
                   (now,now,item["request_id"]))
        db.commit()
        return dict(item)
    except Exception:
        db.rollback()
        raise

def process_one(db, now=None):
    now=int(time.time()) if now is None else now
    item=claim(db,now)
    if not item:return None
    state, result, error = "done", {}, ""
    try:
        result=execute(item["kind"])
        if not isinstance(result,dict) or len(json.dumps(result))>16384:
            raise RuntimeError("Invalid or oversized worker result")
    except Exception as problem:
        state="failed"
        error=type(problem).__name__[:60] + ": Worker job failed"
    db.execute("""UPDATE work_jobs SET state=?,result_json=?,error=?,updated_at=?
                  WHERE request_id=? AND state='running' AND claimed_at=?""",
               (state,json.dumps(result,separators=(",",":")),error,
                int(time.time()),item["request_id"],now))
    db.commit()
    print(f"job={item['request_id']} kind={item['kind']} state={state}",flush=True)
    return state

def stop(*args):
    global RUN
    RUN=False

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true",help="No DB/network access")
    parser.add_argument("--db",default=DB)
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--interval",type=int,default=4)
    args=parser.parse_args(argv)
    if args.check:
        print("PASS Ubuntu fixed-task executor available; no changes")
        return 0
    if args.db!=DB:raise ValueError("Unexpected private job database path")
    if not 1<=args.interval<=60:raise ValueError("Invalid work poll interval")
    if os.geteuid()==0:raise RuntimeError("Worker must not run as root")
    directory=Path(DB).parent
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError("Private node directory not initialized")
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    db=sqlite3.connect(DB,timeout=15,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA journal_mode=WAL")
    ensure_schema(db)
    try:
        while RUN:
            result=process_one(db)
            if args.once:break
            if result is None:
                for _ in range(args.interval):
                    if not RUN:break
                    time.sleep(1)
        return 0
    finally:
        db.close()

if __name__=="__main__":
    try:sys.exit(main())
    except (ValueError,OSError,RuntimeError,sqlite3.Error) as error:
        print("FAIL Ubuntu worker: "+str(error),file=sys.stderr)
        sys.exit(1)
