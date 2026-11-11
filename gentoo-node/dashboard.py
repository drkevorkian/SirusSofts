#!/usr/bin/env python3
"""Private Gentoo dashboard: local metrics and authenticated node-scoped CMS task history."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import socket
from urllib.error import HTTPError, URLError
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import client
import dashboard_live

HOST, PORT = "127.0.0.1", 18080
AUTH = "/etc/sirus-gentoo-node/dashboard.auth"
CONFIG = "/etc/sirus-gentoo-node/config.json"
DB = "/var/lib/sirusnode/dashboard.sqlite3"
ITERATIONS = 300000


def make_hash(password: str) -> str:
    if not 14 <= len(password) <= 256:
        raise ValueError("Dashboard password must be 14..256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return ":".join(("pbkdf2-sha256", str(ITERATIONS), salt.hex(), digest.hex()))


def verify(password: str, record: str) -> bool:
    try:
        algorithm, rounds, salt, stored = record.strip().split(":")
        if algorithm != "pbkdf2-sha256" or int(rounds) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                    bytes.fromhex(salt), ITERATIONS)
        return hmac.compare_digest(actual, bytes.fromhex(stored))
    except (ValueError, UnicodeError):
        return False


def metrics_db(path: str):
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS samples "
                 "(at INTEGER PRIMARY KEY,cpu REAL,memory REAL,disk REAL)")
    return conn


def sample_loop(path: str, stop: threading.Event):
    while not stop.is_set():
        try:
            m = client.performance()
            memory = client.memory_bytes()
            total = m.get("disk_total_bytes", 0)
            now = int(time.time())
            with metrics_db(path) as conn:
                conn.execute("INSERT OR REPLACE INTO samples VALUES (?,?,?,?)",
                             (now, m.get("cpu_pct"),
                              round(100*m["memory_used_bytes"]/max(1,memory),2),
                              round(100*m["disk_used_bytes"]/max(1,total),2)))
                conn.execute("DELETE FROM samples WHERE at < ?", (now-86400,))
        except (OSError,ValueError,sqlite3.Error,ZeroDivisionError) as error:
            print("Sampling error:",type(error).__name__,flush=True)
        stop.wait(60)


def chart(path: str) -> str:
    try:
        with metrics_db(path) as conn:
            values = [min(100,max(0,float(v))) for (v,) in conn.execute(
                "SELECT cpu FROM samples WHERE cpu IS NOT NULL ORDER BY at")]
    except (OSError,ValueError,sqlite3.Error):
        values=[]
    if len(values)<2:
        return '<p class="muted">Collecting CPU history; check back after two samples.</p>'
    line=" ".join(f"{i*600/(len(values)-1):.1f},{110-v:.1f}"
                  for i,v in enumerate(values))
    return ('<svg viewBox="0 0 600 120" preserveAspectRatio="none" '
            'role="img" aria-label="CPU use over 24 hours">'
            '<polyline stroke="#6db9ff" stroke-width="2" fill="none" points="'
            +line+'"/></svg>')


def chart_points(path: str) -> list[float]:
    """Bounded 24-hour CPU samples as numbers for the live chart."""
    try:
        with metrics_db(path) as conn:
            return [min(100, max(0, float(v))) for (v,) in conn.execute(
                "SELECT cpu FROM samples WHERE cpu IS NOT NULL ORDER BY at DESC LIMIT 1440")][::-1]
    except (OSError, ValueError, sqlite3.Error):
        return []


def live_loop(server, stop: threading.Event):
    """One metrics and signed-history producer for all connected browsers."""
    metrics, jobs, graph = {}, [], []
    hardware = {"node_id": server.config["node_id"],
                "ram_bytes": client.memory_bytes(),
                "cpu_threads": max(1, os.cpu_count() or 1)}
    sync = "Connecting to Ubuntu's signed work broker."
    next_metrics = next_jobs = next_graph = 0.0
    while not stop.is_set():
        now = time.monotonic()
        changed = False
        if now >= next_metrics:
            try:
                fresh = client.performance()
                if isinstance(fresh, dict):
                    metrics = fresh
                    changed = True
            except (OSError, ValueError, TypeError) as error:
                print("Live metrics failed:", type(error).__name__, flush=True)
            next_metrics = now + 10
        if now >= next_jobs:
            try:
                key = client.load_key(server.config["key_file"])
                response = client.work_request(server.config, key, "history")
                fresh = response.get("jobs", []) if isinstance(response, dict) else []
                if not isinstance(fresh, list):
                    raise ValueError("Invalid history response")
                jobs = [entry for entry in fresh[:40] if isinstance(entry, dict)]
                sync = "Live signed node-scoped jobs from Ubuntu."
            except Exception as error:
                sync = history_error_message(error) + (
                    " Displaying previously received jobs." if jobs else "")
                print("Live history unavailable:", type(error).__name__, flush=True)
            changed = True
            next_jobs = now + 4
        if now >= next_graph:
            graph = chart_points(server.db)
            changed = True
            next_graph = now + 60
        if changed:
            safe_jobs = []
            for entry in jobs[:40]:
                item = {k: entry.get(k) for k in
                        ("request_id", "kind", "state", "attempts", "updated_at", "error")}
                raw = entry.get("result", {})
                try:
                    value = json.dumps(raw, ensure_ascii=True)
                    item["result"] = raw if len(value) <= 8192 else {"notice": "Result exceeds display limit"}
                except (TypeError, ValueError):
                    item["result"] = {}
                safe_jobs.append(item)
            server.live_state.publish({
                "type": "snapshot", "metrics": metrics, "hardware": hardware,
                "jobs": safe_jobs, "graph": graph, "sync": sync
            })
        stop.wait(2)


def bytes_gib(value):
    return "N/A" if not isinstance(value,(int,float)) else f"{value/1073741824:.2f} GiB"


def date_utc(value):
    try:
        return datetime.fromtimestamp(int(value),timezone.utc).strftime("%b %d %H:%M UTC")
    except (ValueError,TypeError,OverflowError):
        return "N/A"


def history_error_message(error: Exception) -> str:
    """Explain broker failures after authentication; never expose URLs or credentials."""
    if isinstance(error, HTTPError):
        if error.code == 401:
            return ("Ubuntu rejected signed task history (HTTP 401). "
                    "Check the node HMAC key/enrollment and CMS work broker.")
        if error.code == 404:
            return ("Ubuntu task-history endpoint is missing (HTTP 404). "
                    "Check that the CMS node-work.php upgrade is deployed.")
        if error.code == 405:
            return ("Ubuntu rejected the task-history POST (HTTP 405). "
                    "Check HTTPS and the CMS work endpoint routing.")
        if error.code == 503:
            return ("Ubuntu work broker is not configured (HTTP 503). "
                    "Check its private nodes/keys directory and database permissions.")
        return f"Ubuntu work broker returned HTTP {error.code}; check server logs."
    if isinstance(error, (ssl.SSLError, ssl.CertificateError)):
        return "Could not verify the Ubuntu HTTPS certificate; check trust and system clock."
    if isinstance(error, (URLError, TimeoutError, socket.timeout, ConnectionError)):
        return "Could not reach the Ubuntu HTTPS work broker; check DNS, TLS and routing."
    if isinstance(error, (ValueError, KeyError, PermissionError)):
        return "Task history could not be validated; check node configuration and HMAC key."
    return "Coordinator history is temporarily unavailable; check Gentoo dashboard logs."


def gpu_telemetry_note(gpu: dict) -> str:
    """Display why an inventoried GPU may have no driver-backed readings."""
    if any(gpu.get(k) is not None for k in
           ("usage_pct", "temp_c", "vram_used_bytes", "vram_total_bytes")):
        return ""
    if gpu.get("vendor") == "0x10de":
        return ("Detected by PCI; NVIDIA telemetry requires a loaded compatible "
                "driver and readable NVML/nvidia-smi.")
    if gpu.get("vendor") == "0x1002":
        return ("Detected by PCI; AMD utilization/temperature require readable "
                "DRM or hwmon driver interfaces.")
    return "Detected by PCI, but performance counters are unavailable."


def page(m: dict, hardware: dict, jobs: list, graph: str, sync: str) -> bytes:
    e=lambda value:escape(str(value),quote=True)
    gpu_rows=""
    for gpu in m.get("gpus",[]):
        if not isinstance(gpu,dict):
            continue
        pct="N/A" if gpu.get("usage_pct") is None else f'{gpu["usage_pct"]}%'
        temperature="N/A" if gpu.get("temp_c") is None else f'{gpu["temp_c"]} °C'
        detail=gpu_telemetry_note(gpu)
        label=e(gpu.get("name","GPU"))+(("<small>"+e(detail)+"</small>") if detail else "")
        gpu_rows+=("<tr><td>"+label+"</td><td>"+e(pct)+
                   "</td><td>"+e(temperature)+"</td><td>"+
                   e(bytes_gib(gpu.get("vram_used_bytes")))+" / "+
                   e(bytes_gib(gpu.get("vram_total_bytes")))+"</td></tr>")
    rows=""
    for job in jobs[:40]:
        if not isinstance(job,dict):
            continue
        result=json.dumps(job.get("result",{}),indent=2,ensure_ascii=True)[:8192]
        rows+=("<tr><td><code>"+e(job.get("request_id",""))+"</code></td><td>"+
               e(job.get("kind",""))+"</td><td>"+e(job.get("state",""))+
               "</td><td>"+e(job.get("attempts",0))+"</td><td>"+
               e(date_utc(job.get("updated_at")))+"</td><td><details><summary>Details</summary>"+
               "<pre>"+e(result)+"</pre><small>"+e(job.get("error",""))+
               "</small></details></td></tr>")
    used=m.get("memory_used_bytes",0)
    ram=hardware["ram_bytes"]
    total=m.get("disk_total_bytes",0)
    disk=m.get("disk_used_bytes",0)
    cpu="N/A" if m.get("cpu_pct") is None else f'{m["cpu_pct"]:.1f}%'
    body=f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Gentoo · SirusSofts</title>
<style>
:root{{color-scheme:dark;--bg:#07121f;--panel:#132a40;--stroke:#31536c;
--text:#eaf4ff;--muted:#a5bcd0;--accent:#6db9ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 system-ui,Arial,sans-serif}}main{{max-width:1200px;margin:auto;padding:25px}}
h1{{font-size:clamp(29px,5vw,46px);margin:4px 0}}h2{{font-size:19px;margin:0 0 15px}}
a{{color:var(--accent)}}.muted,small{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
gap:15px;margin:22px 0}}.card,section{{background:var(--panel);
border:1px solid var(--stroke);border-radius:15px;padding:21px}}
.card strong{{display:block;font-size:26px}}section{{margin:20px 0}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:650px}}
th,td{{border-bottom:1px solid var(--stroke);padding:10px;vertical-align:top;text-align:left}}
th{{color:var(--muted);font-size:12px}}code,pre{{font-size:12px;overflow-wrap:anywhere}}
pre{{max-height:270px;overflow:auto;white-space:pre-wrap}}
svg{{display:block;width:100%;height:150px}}summary{{cursor:pointer}}
</style></head><body><main><small>SIRUSSOFTS · GENTOO COMPUTE NODE</small>
<h1>{e(hardware["node_id"])}</h1>
<p class="muted">Compute workloads run on Gentoo. Ubuntu provides HTTPS,
assigns jobs and stores the authoritative task history.
<a href="https://sirussofts.com/admin/infrastructure">CMS infrastructure</a></p>
<div class="cards">
<div class="card"><small>CPU · {hardware["cpu_threads"]} threads</small><strong>{e(cpu)}</strong></div>
<div class="card"><small>Memory</small><strong>{100*used/max(1,ram):.1f}%</strong></div>
<div class="card"><small>Root disk</small><strong>{100*disk/max(1,total):.1f}%</strong></div>
<div class="card"><small>Uptime</small><strong>{int(m.get("uptime_seconds",0))//3600}h</strong></div>
</div>
<section><h2>CPU utilization · last 24 hours</h2>{graph}</section>
<section><h2>Graphics hardware</h2><div class="wrap"><table>
<thead><tr><th>GPU</th><th>Usage</th><th>Temperature</th><th>VRAM</th></tr></thead>
<tbody>{gpu_rows or '<tr><td colspan="4">No GPUs found</td></tr>'}</tbody>
</table></div></section>
<section><h2>Previous tasks</h2><p class="muted">{e(sync)}</p>
<div class="wrap"><table><thead><tr><th>ID</th><th>Kind</th><th>Status</th>
<th>Attempts</th><th>Updated</th><th>Result</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6">No previous jobs</td></tr>'}</tbody></table></div>
</section><small>Read-only · Refreshes every 30 seconds</small></main></body></html>"""
    return body.encode("utf8")


class Handler(BaseHTTPRequestHandler):
    server_version="SirusGentoo/0.1"
    def log_message(self, fmt, *args):
        print("Gentoo dashboard:",fmt % args,flush=True)

    def send(self,status:int,data:bytes,ctype:str,authenticate=False):
        self.send_response(status)
        self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none';style-src 'unsafe-inline';"
                         "frame-ancestors 'none';form-action 'none'")
        if authenticate:
            self.send_header("WWW-Authenticate",'Basic realm="SirusSofts Gentoo"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        uri=urlsplit(self.path).path
        if uri=="/healthz":
            return self.send(200,b"OK\n","text/plain; charset=utf-8")
        if uri not in ("/","/dashboard"):
            return self.send(404,b"Not found","text/plain; charset=utf-8")
        auth=self.headers.get("Authorization","")
        try:
            if not auth.startswith("Basic ") or len(auth)>512:
                raise ValueError("No auth")
            username,password=base64.b64decode(auth[6:],validate=True).decode().split(":",1)
            if not (hmac.compare_digest(username,"admin") and
                    verify(password,self.server.secret)):
                raise ValueError("Invalid")
        except (ValueError,UnicodeError):
            return self.send(401,b"Authentication required","text/plain; charset=utf-8",True)
        config=self.server.config
        m=client.performance()
        hardware={"node_id":config["node_id"],"ram_bytes":client.memory_bytes(),
                  "cpu_threads":max(1,os.cpu_count() or 1)}
        jobs,sync=[],"Coordinator history is temporarily unavailable."
        try:
            key=client.load_key(config["key_file"])
            jobs=client.work_request(config,key,"history").get("jobs",[])
            sync="Latest 40 signed, node-scoped jobs from Ubuntu."
        except Exception as error:
            sync=history_error_message(error)
            print("Task history request failed:",type(error).__name__,
                  getattr(error,"code",""),flush=True)
        self.send(200,page(m,hardware,jobs,chart(self.server.db),sync),
                  "text/html; charset=utf-8")


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--config",default="/etc/sirus-gentoo-node/config.json")
    parser.add_argument("--auth",default=AUTH)
    parser.add_argument("--db",default=DB)
    args=parser.parse_args(argv)
    if args.check:
        assert verify("correct-password-1234",make_hash("correct-password-1234"))
        assert not verify("wrong-password-1234",make_hash("correct-password-1234"))
        print("PASS private dashboard password and syntax checks")
        return 0
    config=client.validate_config(json.loads(Path(args.config).read_text()))
    auth_path=Path(args.auth)
    if not auth_path.is_file() or auth_path.lstat().st_mode & 0o077:
        raise PermissionError("Private dashboard credential file required")
    secret=auth_path.read_text().strip()
    db_path=Path(args.db)
    if db_path.is_symlink() or not db_path.parent.is_dir():
        raise ValueError("Unsafe monitoring database directory")
    stop=threading.Event()
    threading.Thread(target=sample_loop,args=(str(db_path),stop),daemon=True).start()
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    server.config,server.secret,server.db=config,secret,str(db_path)
    print("Gentoo dashboard started on loopback-only port",PORT,flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0

if __name__=="__main__":
    raise SystemExit(main())
