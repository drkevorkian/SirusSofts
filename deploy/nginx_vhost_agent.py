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
import socket
import stat
import socketserver
import subprocess
import tempfile
import threading

ROOT = Path("/srv/sirussofts/current/CMS")
MANAGED = Path("/etc/nginx/sirussofts-managed")
CERT = Path("/etc/sirussofts/tls/fullchain.pem")
KEY = Path("/etc/sirussofts/tls/privkey.pem")
SOCKET = Path("/run/sirus-nginx/agent.sock")
SITES = Path("/etc/nginx/sites-enabled")
APP_ROUTES = Path("/etc/sirussofts/app-routes.json")
FRAGMENTS = Path("/etc/nginx/sirussofts-managed/routes")
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
    def __init__(self, root=ROOT, managed=MANAGED, cert=CERT, key=KEY, sites=SITES,
                 runner=run_command, tls_checker=None, app_routes=APP_ROUTES,
                 fragments=FRAGMENTS, upstream_checker=None):
        self.root = Path(root)
        self.managed = Path(managed)
        self.sites = Path(sites)
        self.app_routes = Path(app_routes)
        self.fragments = Path(fragments)
        self.upstream_checker = upstream_checker
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
        return self.sites / ("sirussofts-project-" + slug + ".conf")

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


    def _app_backend(self, slug):
        """Backend destination is root-controlled data; never supplied over PHP IPC."""
        path = self.app_routes
        if path.is_symlink() or not path.is_file():
            raise VhostError("No approved application backend registry")
        mode = path.stat()
        if mode.st_mode & 0o022 or (os.geteuid() == 0 and mode.st_uid != 0):
            raise VhostError("Application backend registry requires root ownership and no group/world write")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, OSError):
            raise VhostError("Invalid application registry JSON") from None
        if not isinstance(data, dict) or set(data) != {"schema_version", "applications"} or \
                data["schema_version"] != 1 or not isinstance(data["applications"], dict):
            raise VhostError("Unsupported application registry")
        used = set()
        for name, spec in data["applications"].items():
            validate(name, "app")
            if not isinstance(spec, dict) or set(spec) != {"enabled", "host", "port"} or \
                    type(spec["enabled"]) is not bool or spec["host"] != "127.0.0.1" or \
                    type(spec["port"]) is not int or not 1024 <= spec["port"] <= 65535:
                raise VhostError("Invalid application backend entry")
            if spec["enabled"]:
                if spec["port"] in used:
                    raise VhostError("Duplicate enabled application backend port")
                used.add(spec["port"])
        record = data["applications"].get(slug)
        if record is None or not record["enabled"]:
            raise VhostError("Application is not explicitly approved for routing")
        return record["port"]

    def _upstream_ready(self, slug, port):
        if self.upstream_checker is not None:
            return bool(self.upstream_checker(slug, port))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.6):
                return True
        except OSError:
            return False

    def _route_file(self, slug):
        return self.fragments / ("sirussofts-app-" + slug + ".conf")

    @staticmethod
    def _proxy_options(prefix):
        return (f"        proxy_set_header Host $host;\n"
                f"        proxy_set_header X-Real-IP $remote_addr;\n"
                f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
                f'        proxy_set_header X-Forwarded-Prefix "{prefix}";\n'
                f"        proxy_connect_timeout 3s;\n"
                f"        proxy_read_timeout 60s;\n"
                f"        proxy_redirect off;\n")

    def _app_config(self, slug, port):
        hosts = slug + ".sirussofts.org " + slug + ".sirussofts.com"
        # The upstream must be a separately deployed HTTP application, bound
        # only to loopback, with its own runtime and database permissions.
        return (f"# Managed SirusSofts app upstream; PHP cannot alter templates.\n"
                f"server {{\n    listen 80;\n    server_name {hosts};\n"
                f"    return 308 https://$host$request_uri;\n}}\n"
                f"server {{\n    listen 443 ssl;\n    server_name {hosts};\n"
                f"    ssl_certificate {self.cert};\n"
                f"    ssl_certificate_key {self.key};\n"
                f"    location / {{\n"
                f"        proxy_pass http://127.0.0.1:{port};\n"
                + self._proxy_options("") +
                f"    }}\n}}\n")

    def _app_directory_route(self, slug, port):
        # Prefix is removed before forwarding to the app, which must generate
        # prefix-aware asset URLs and redirects using X-Forwarded-Prefix.
        return (f"# Managed directory route for {slug} on BOTH root domains.\n"
                f"location = /{slug} {{ return 308 /{slug}/; }}\n"
                f"location ^~ /{slug}/ {{\n"
                f"        proxy_pass http://127.0.0.1:{port}/;\n"
                + self._proxy_options("/" + slug) +
                f"}}\n")

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
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.is_symlink():
            raise VhostError("Unsafe managed directory")
        fd, tmp = tempfile.mkstemp(prefix=".sirus-", dir=path.parent)
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

    def _result(self, slug, status, ready, note, kind="page"):
        return {"status": status, "kind": kind, "hostname": slug + ".sirussofts.org",
                "subdomain_url": "https://" + slug + ".sirussofts.org",
                "secondary_subdomain_url": "https://" + slug + ".sirussofts.com",
                "directory_url": "https://sirussofts.org/" + slug,
                "secondary_directory_url": "https://sirussofts.com/" + slug,
                "www_root": str(self.root), "ready": ready, "note": note}

    def _plans(self, slug, kind):
        if kind == "page":
            return self._config(slug), None
        port = self._app_backend(slug)
        return self._app_config(slug, port), self._app_directory_route(slug, port)

    @staticmethod
    def _sha(content):
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _expected_state(self, kind, site, directory):
        return {"kind": kind, "site_sha256": self._sha(site),
                "directory_sha256": self._sha(directory) if directory is not None else None}

    def _owned_files(self, slug, kind, site, directory):
        if site.is_symlink() or not site.is_file() or \
                self._sha(site.read_text(encoding="utf-8")) != kind["site_sha256"]:
            raise VhostError("Managed site differs from recorded digest")
        if kind["directory_sha256"] is None:
            if directory.exists() or directory.is_symlink():
                raise VhostError("Unexpected unmanaged directory route")
        elif directory.is_symlink() or not directory.is_file() or \
                self._sha(directory.read_text(encoding="utf-8")) != kind["directory_sha256"]:
            raise VhostError("Managed directory route differs from recorded digest")

    def preview(self, slug, kind):
        validate(slug, kind)
        self._root_valid()
        try:
            site, route = self._plans(slug, kind)
        except VhostError as err:
            return self._result(slug, "blocked", False, str(err), kind)
        tls = self._tls_valid(slug)
        upstream = True
        if kind == "app":
            upstream = self._upstream_ready(slug, self._app_backend(slug))
        return self._result(slug, "preview", bool(tls and upstream),
            ("TLS and upstream validated" if tls and upstream else
             "Apply blocked: valid TLS and a running approved backend required"), kind)

    def apply(self, slug, kind):
        validate(slug, kind)
        self._root_valid()
        site, directory = self._plans(slug, kind)
        if not self._tls_valid(slug):
            raise VhostError("Valid TLS coverage of both project hostnames is required")
        if kind == "app" and not self._upstream_ready(slug, self._app_backend(slug)):
            raise VhostError("Approved loopback application backend is unavailable")
        state = self._expected_state(kind, site, directory)
        path = self._file(slug)
        fragment = self._route_file(slug)
        with self.lock:
            ledger = self._state()
            if slug in ledger or path.exists() or path.is_symlink() or \
                    fragment.exists() or fragment.is_symlink():
                if ledger.get(slug) != state:
                    raise VhostError("Existing route is unmanaged or has a different deployment configuration")
                self._owned_files(slug, state, path, fragment)
                return self._result(slug, "already-managed", True, "No changes required", kind)
            written = []
            try:
                self._atomic(path, site)
                written.append(path)
                if directory is not None:
                    self._atomic(fragment, directory)
                    written.append(fragment)
                self.runner(["nginx", "-t"])
                updated = dict(ledger)
                updated[slug] = state
                self._save(updated)
                self.runner(["systemctl", "reload", "nginx"])
            except Exception:
                for item in written:
                    item.unlink(missing_ok=True)
                self._save(ledger)
                try:
                    self.runner(["nginx", "-t"])
                    self.runner(["systemctl", "reload", "nginx"])
                except Exception:
                    pass
                raise
        return self._result(slug, "applied", True, "Managed Nginx routes activated", kind)

    def rollback(self, slug, kind):
        validate(slug, kind)
        site = self._file(slug)
        fragment = self._route_file(slug)
        with self.lock:
            ledger = self._state()
            state = ledger.get(slug)
            if not isinstance(state, dict) or state.get("kind") != kind:
                raise VhostError("No matching managed vhost of this type")
            self._owned_files(slug, state, site, fragment)
            old_site = site.read_text(encoding="utf-8")
            old_route = fragment.read_text(encoding="utf-8") if state["directory_sha256"] else None
            site.unlink()
            if old_route is not None:
                fragment.unlink()
            updated = dict(ledger)
            del updated[slug]
            try:
                self.runner(["nginx", "-t"])
                self._save(updated)
                self.runner(["systemctl", "reload", "nginx"])
            except Exception:
                self._atomic(site, old_site)
                if old_route is not None:
                    self._atomic(fragment, old_route)
                self._save(ledger)
                try:
                    self.runner(["nginx", "-t"])
                    self.runner(["systemctl", "reload", "nginx"])
                except Exception:
                    pass
                raise
        return self._result(slug, "rolled-back", False, "Managed routes removed, app data preserved", kind)


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
