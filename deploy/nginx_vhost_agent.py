#!/usr/bin/env python3
"""Root-owned, strictly limited Nginx CMS page-vhost agent.

No arbitrary commands, paths, raw nginx snippets or application deployments.
Subdomain activation is blocked unless a reviewed wildcard certificate covers
both .org and .com. Application vhosts require a later route-safe adapter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socketserver
import subprocess
import tempfile
import threading

ROOT = Path("/srv/sirussofts/current/CMS")
MANAGED = Path("/etc/nginx/sirussofts-managed")
CERT = Path("/etc/sirussofts/tls/fullchain.pem")
KEY = Path("/etc/sirussofts/tls/privkey.pem")
SOCKET = Path("/run/sirus-nginx/agent.sock")
RESERVED = {"admin", "api", "app", "assets", "bin", "cms", "deploy", "health",
            "internal", "lib", "login", "pages", "private", "root", "settings",
            "static", "system", "tests", "www", "worldcom", "sentinel-trader",
            "sentinal-trader", "cpoint"}
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


class VhostError(ValueError):
    pass


def run_command(argv):
    result = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise VhostError("Fixed Nginx or TLS validation command failed")
    return result.stdout


def validate(slug, kind):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or slug in RESERVED:
        raise VhostError("Invalid or protected project slug")
    if kind not in ("page", "app"):
        raise VhostError("Invalid project type")


class NginxVhosts:
    def __init__(self, root=ROOT, managed=MANAGED, cert=CERT, key=KEY,
                 runner=run_command, tls_checker=None):
        self.root = Path(root)
        self.managed = Path(managed)
        self.cert = Path(cert)
        self.key = Path(key)
        self.runner = runner
        self.tls_checker = tls_checker
        self.lock = threading.Lock()

    def _root_valid(self):
        if not (self.root / "index.php").is_file() or not (self.root / "app/core.php").is_file():
            raise VhostError("CMS document root is not installed")
        if self.root.is_symlink():
            raise VhostError("Unexpected CMS document root symlink")

    def _tls_valid(self, slug):
        if self.tls_checker is not None:
            return bool(self.tls_checker(slug))
        if not self.cert.is_file() or not self.key.is_file() or self.key.is_symlink():
            return False
        try:
            self.runner(["openssl", "x509", "-in", str(self.cert), "-noout", "-checkend", "0"])
            for domain in ("sirussofts.org", "sirussofts.com"):
                self.runner(["openssl", "x509", "-in", str(self.cert), "-noout",
                             "-checkhost", slug + "." + domain])
            return True
        except VhostError:
            return False

    def _file(self, slug):
        return self.managed / ("site-" + slug + ".conf")

    def _config(self, slug):
        hosts = slug + ".sirussofts.org " + slug + ".sirussofts.com"
        # Only CMS content projects: their /slug directory route already
        # resolves via the main CMS. Separate app runtimes are NOT mapped here.
        return f"""# Managed by SirusSofts Nginx vhost agent. Do not edit in place.
server {{
    listen 80;
    server_name {hosts};
    return 308 https://$host$request_uri;
}}
server {{
    listen 443 ssl;
    server_name {hosts};
    root /srv/sirussofts/current/CMS;
    index index.php;
    ssl_certificate {self.cert};
    ssl_certificate_key {self.key};
    location ~ ^/(?:app|bin|tests|data|private|internal|deploy)(?:/|$) {{ return 404; }}
    location ~ /\\.(?!well-known) {{ return 404; }}
    location / {{ try_files $uri $uri/ /index.php?$args; }}
    location = /index.php {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/sirussofts.sock;
    }}
    location ~ \\.php$ {{ return 404; }}
}}
"""

    def _state_path(self):
        return self.managed / "managed.json"

    def _state(self):
        f = self._state_path()
        if not f.exists():
            return {}
        if f.is_symlink():
            raise VhostError("Invalid managed registry symlink")
        value = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise VhostError("Invalid managed vhost registry")
        return value

    def _atomic(self, path, content):
        self.managed.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.managed.is_symlink():
            raise VhostError("Unsafe managed directory")
        fd, tmp = tempfile.mkstemp(prefix=".sirus-", dir=self.managed)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600 if path.suffix == ".json" else 0o644)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _save(self, data):
        self._atomic(self._state_path(), json.dumps(data, sort_keys=True, indent=2) + "\n")

    def _result(self, slug, status, ready, note):
        return {"status": status, "kind": "page", "hostname": slug + ".sirussofts.org",
                "subdomain_url": "https://" + slug + ".sirussofts.org",
                "secondary_subdomain_url": "https://" + slug + ".sirussofts.com",
                "directory_url": "https://sirussofts.org/" + slug,
                "secondary_directory_url": "https://sirussofts.com/" + slug,
                "www_root": str(self.root), "ready": ready, "note": note}

    def preview(self, slug, kind):
        validate(slug, kind)
        self._root_valid()
        if kind != "page":
            return self._result(slug, "blocked-app", False,
                "Executable app vhosts are not supported by this page-only adapter. No changes made.")
        tls = self._tls_valid(slug)
        return self._result(slug, "preview", tls,
            "Wildcard TLS certificate validated" if tls else
            "Apply blocked until a reviewed certificate covers both project hostnames")

    def apply(self, slug, kind):
        validate(slug, kind)
        self._root_valid()
        if kind != "page":
            raise VhostError("Application vhosts are not supported; no configuration written")
        if not self._tls_valid(slug):
            raise VhostError("Valid wildcard TLS coverage is required before apply")
        config = self._config(slug)
        digest = hashlib.sha256(config.encode()).hexdigest()
        with self.lock:
            records = self._state()
            existing = self._file(slug)
            if existing.exists():
                if existing.is_symlink() or records.get(slug) != digest or \
                        hashlib.sha256(existing.read_bytes()).hexdigest() != digest:
                    raise VhostError("Existing unmanaged or modified vhost; refusing overwrite")
                return self._result(slug, "already-managed", True, "No changes required")
            self._atomic(existing, config)
            try:
                self.runner(["nginx", "-t"])
                updated = dict(records)
                updated[slug] = digest
                self._save(updated)
                self.runner(["systemctl", "reload", "nginx"])
            except Exception:
                existing.unlink(missing_ok=True)
                self._save(records)
                try:
                    self.runner(["nginx", "-t"])
                    self.runner(["systemctl", "reload", "nginx"])
                except Exception:
                    pass
                raise
        return self._result(slug, "applied", True, "Managed configuration written and Nginx reloaded")

    def rollback(self, slug, kind):
        validate(slug, kind)
        with self.lock:
            records = self._state()
            existing = self._file(slug)
            expected = records.get(slug)
            if not expected or not existing.is_file() or existing.is_symlink() or \
                    hashlib.sha256(existing.read_bytes()).hexdigest() != expected:
                raise VhostError("No matching managed vhost to roll back")
            old = existing.read_text(encoding="utf-8")
            existing.unlink()
            updated = dict(records)
            del updated[slug]
            try:
                self.runner(["nginx", "-t"])
                self._save(updated)
                self.runner(["systemctl", "reload", "nginx"])
            except Exception:
                self._atomic(existing, old)
                self._save(records)
                try:
                    self.runner(["nginx", "-t"])
                    self.runner(["systemctl", "reload", "nginx"])
                except Exception:
                    pass
                raise
        return self._result(slug, "rolled-back", False, "Managed vhost removed; no project data deleted")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(2049)
            if len(line) > 2048 or not line.endswith(b"\n"):
                raise VhostError("Request exceeds size limit")
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) != {"action", "slug", "kind"}:
                raise VhostError("Unsupported request fields")
            action = request["action"]
            if action not in ("preview", "apply", "rollback"):
                raise VhostError("Unsupported operation")
            result = getattr(self.server.manager, action)(request["slug"], request["kind"])
            response = {"ok": True, "result": result}
        except (ValueError, OSError, RuntimeError, TypeError) as error:
            response = {"ok": False, "error": str(error)[:200]}
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manager = NginxVhosts()
    if args.check:
        manager._root_valid()
        print("PASS: CMS directory verified; no Nginx changes made")
        return
    if os.geteuid() != 0:
        raise SystemExit("Root is required only for the restricted service")
    SOCKET.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if SOCKET.exists():
        SOCKET.unlink()
    os.umask(0o077)
    with Server(str(SOCKET), Handler) as server:
        server.manager = manager
        os.chown(SOCKET, 0, pwd.getpwnam("sirusweb").pw_gid)
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
