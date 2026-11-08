#!/usr/bin/env python3
"""Signed GitHub webhook receiver + queued per-repository atomic deployment.

Runs on 127.0.0.1, behind the existing HTTPS web server.
Only an explicit allowlist can deploy; disabled by default.
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("sirus-deploy")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_BODY = 1024 * 1024
BLOCKED = {".git", ".github", ".env", ".ssh", "credentials.json", "id_rsa", "id_ed25519"}

def when():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def authentic(payload, signature, secret):
    computed = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(computed, signature)

def safe_source(raw):
    if (not isinstance(raw, str) or not raw or raw.startswith("/") or
            "\\" in raw or any(p in ("", "..") for p in raw.split("/"))):
        raise ValueError("unsafe source_subdir")
    return PurePosixPath(raw)

def load_projects(path):
    cfg = json.loads(Path(path).read_text(encoding="utf8"))
    if not isinstance(cfg.get("projects"), dict):
        raise ValueError("projects must be a mapping")
    slugs = set()
    for name, item in cfg["projects"].items():
        if not REPO.fullmatch(name) or not isinstance(item, dict):
            raise ValueError("invalid repository configuration")
        if not isinstance(item.get("enabled", False), bool):
            raise ValueError("enabled must be boolean")
        if item.get("transport", "https") not in ("https", "ssh"):
            raise ValueError("invalid transport")
        slug = item.get("slug", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", slug) or slug in slugs:
            raise ValueError("missing, duplicate, or invalid project slug")
        slugs.add(slug)
        branch = item.get("branch", "main")
        if (not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or
                branch.startswith(("-", "/")) or ".." in branch or "//" in branch):
            raise ValueError("invalid Git branch")
        safe_source(item.get("source_subdir", "public"))
        if item.get("transport") == "ssh":
            key = item.get("ssh_key", "")
            if not key or not Path(key).is_absolute():
                raise ValueError("SSH requires an absolute ssh_key path")
    return cfg

class Queue:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript("""
              CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, delivery TEXT NOT NULL UNIQUE,
                repo TEXT NOT NULL, sha TEXT NOT NULL,
                state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                updated TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS pending_jobs ON jobs(state,id);
            """)
            c.execute("UPDATE jobs SET state='pending' WHERE state='running'")

    def db(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def put(self, delivery, repo, sha):
        with self.db() as c:
            result = c.execute(
                "INSERT OR IGNORE INTO jobs(delivery,repo,sha,state,updated) "
                "VALUES(?,?,?,'pending',?)", (delivery, repo, sha, when()))
            return result.rowcount > 0

    def pop(self):
        with self.db() as c:
            row = c.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY id LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (when(), row["id"]))
            return dict(row) if row else None

    def finish(self, job, state, info):
        with self.db() as c:
            c.execute("UPDATE jobs SET state=?,message=?,updated=? WHERE id=?",
                      (state, str(info)[:2500], when(), job["id"]))

    def status(self):
        with self.db() as c:
            return [dict(r) for r in c.execute(
                "SELECT repo,sha,state,message,updated FROM jobs ORDER BY id DESC LIMIT 10")]

def run(argv, env=None, timeout=240):
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, env=env, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError(argv[0] + " failed: " + p.stderr[-1200:])
    return p.stdout.strip()

def inspect_publish(root):
    for base, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            item = Path(base) / name
            if item.is_symlink():
                raise ValueError("publishing symlinks is prohibited: " + str(item))
            low = name.lower()
            if (low in BLOCKED or low.startswith(".env.") or
                    low.endswith((".key", ".pem", ".p12", ".pfx"))):
                raise ValueError("sensitive file in published tree: " + str(item))

def deploy(repo, sha, project, sites_root, tmp_root):
    slug = project["slug"]
    if not SHA.fullmatch(sha):
        raise ValueError("bad commit SHA")
    site = sites_root / slug
    releases = site / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / sha

    with tempfile.TemporaryDirectory(dir=tmp_root, prefix=slug + "-") as scratch:
        checkout = Path(scratch) / "checkout"
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if project.get("transport", "https") == "ssh":
            import shlex
            env["GIT_SSH_COMMAND"] = ("ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "
                                      "-o IdentitiesOnly=yes -i " + shlex.quote(project["ssh_key"]))
            url = "git@github.com:" + repo + ".git"
        else:
            url = "https://github.com/" + repo + ".git"
        run(["git", "clone", "--quiet", "--depth", "1", "--single-branch",
             "--branch", project.get("branch", "main"), url, str(checkout)], env=env)
        tip = run(["git", "-C", str(checkout), "rev-parse", "HEAD"])
        if tip != sha:
            return "skipped", "branch has advanced to " + tip
        if not release.exists():
            src = checkout / str(safe_source(project.get("source_subdir", "public")))
            if not src.is_dir() or not src.resolve().is_relative_to(checkout.resolve()):
                raise ValueError("source_subdir does not exist or escapes repository")
            inspect_publish(src)
            staged = Path(scratch) / "release"
            shutil.copytree(src, staged)
            for f in staged.rglob("*.php"):
                if not shutil.which("php"):
                    raise RuntimeError("php CLI unavailable; refusing unvalidated PHP")
                run(["php", "-l", str(f)], timeout=25)
            shutil.move(str(staged), str(release))
    current = site / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("current is not a symlink; refusing to overwrite")
    next_link = site / (".next-" + sha[:12] + "-" + str(os.getpid()))
    if next_link.exists() or next_link.is_symlink():
        next_link.unlink()
    next_link.symlink_to(release)
    os.replace(next_link, current)
    return "deployed", str(release)

class Service:
    def __init__(self, config, secret_path, data_dir):
        self.cfg = load_projects(config)
        self.secret = Path(secret_path).read_bytes().strip()
        if len(self.secret) < 32:
            raise ValueError("webhook secret must be at least 32 bytes")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sites_root = Path(self.cfg.get("sites_root", "/srv/sirussofts/sites"))
        self.sites_root.mkdir(parents=True, exist_ok=True)
        self.queue = Queue(self.data_dir / "queue.sqlite3")
        self.event = threading.Event()
        self.event.set()

    def accept(self, event_name, delivery_id, body):
        if event_name == "ping":
            return 200, {"status": "ok"}
        if event_name != "push":
            return 202, {"status": "ignored", "reason": "event type"}
        try:
            p = json.loads(body)
            repo, ref, sha = p["repository"]["full_name"], p["ref"], p["after"]
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "malformed GitHub push payload"}
        project = self.cfg["projects"].get(repo)
        if not project or not project.get("enabled", False):
            return 202, {"status": "ignored", "reason": "project disabled"}
        if ref != "refs/heads/" + project.get("branch", "main"):
            return 202, {"status": "ignored", "reason": "branch not configured"}
        if not isinstance(sha, str) or not SHA.fullmatch(sha) or sha == "0" * 40 or p.get("deleted"):
            return 202, {"status": "ignored", "reason": "deleted or invalid commit"}
        if not re.fullmatch(r"[A-Za-z0-9-]{36}", delivery_id):
            return 400, {"error": "invalid delivery ID"}
        new = self.queue.put(delivery_id, repo, sha)
        if new:
            self.event.set()
        return 202, {"status": "queued" if new else "duplicate", "repo": repo, "sha": sha}

    def worker(self):
        while True:
            self.event.wait()
            while True:
                job = self.queue.pop()
                if not job:
                    self.event.clear()
                    break
                try:
                    config = self.cfg["projects"][job["repo"]]
                    if not config.get("enabled"):
                        raise ValueError("project disabled")
                    state, result = deploy(job["repo"], job["sha"], config,
                                           self.sites_root, self.data_dir)
                    self.queue.finish(job, state, result)
                    LOG.info("%s: %s %s", state, job["repo"], result)
                except Exception as ex:
                    self.queue.finish(job, "failed", ex)
                    LOG.exception("deployment failed: %s", job["repo"])

def handler(service):
    class WebhookHandler(BaseHTTPRequestHandler):
        def respond(self, code, obj):
            raw = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/healthz":
                return self.respond(200, {"ready": True})
            return self.respond(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/github":
                return self.respond(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                return self.respond(400, {"error": "bad length"})
            if length < 0 or length > MAX_BODY:
                return self.respond(413, {"error": "payload too large"})
            body = self.rfile.read(length)
            if not authentic(body, self.headers.get("X-Hub-Signature-256", ""), service.secret):
                return self.respond(401, {"error": "invalid signature"})
            code, result = service.accept(
                self.headers.get("X-GitHub-Event", ""),
                self.headers.get("X-GitHub-Delivery", ""), body)
            self.respond(code, result)

        def log_message(self, fmt, *values):
            LOG.info("%s " + fmt, self.client_address[0], *values)
    return WebhookHandler

def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["serve", "check", "status"])
    p.add_argument("--config", default="/etc/sirusdeploy/projects.json")
    p.add_argument("--secret", default="/etc/sirusdeploy/webhook-secret")
    p.add_argument("--data", default="/var/lib/sirusdeploy")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.action == "check":
        c = load_projects(args.config)
        print("OK:", len(c["projects"]), "projects configured")
        return
    service = Service(args.config, args.secret, args.data)
    if args.action == "status":
        print(json.dumps(service.queue.status(), indent=2))
        return
    threading.Thread(target=service.worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 8787), handler(service))
    LOG.info("listening on 127.0.0.1:8787; expose through HTTPS reverse proxy only")
    server.serve_forever()

if __name__ == "__main__":
    main()
