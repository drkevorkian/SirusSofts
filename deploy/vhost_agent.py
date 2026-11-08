#!/usr/bin/env python3
"""SirusSofts restricted Plesk vhost bridge. Unix socket only, no arbitrary commands.

IMPORTANT: root-side service, installed separately. No server changes on preview.
Only the project slug is accepted from a CMS request; all physical paths are
computed from the fixed SirusSofts subscription root.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socketserver
import subprocess
import tempfile
import threading

DOMAIN = "sirussofts.com"
HTTPDOCS = Path("/var/www/vhosts/sirussofts.com/httpdocs")
SOCKET = Path("/run/sirus-vhost/agent.sock")
STATE = Path("/var/lib/sirus-vhost/managed.json")
RESERVED = {"admin", "api", "app", "assets", "bin", "cms", "deploy", "health",
            "internal", "lib", "login", "pages", "private", "root", "settings",
            "static", "system", "tests", "www"}
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
MAX_LINE = 2048


class VhostError(ValueError):
    pass


def validate(slug, kind, root=HTTPDOCS):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or slug in RESERVED:
        raise VhostError("Invalid or reserved project slug")
    if kind not in ("page", "app"):
        raise VhostError("Invalid project type")
    if not root.is_dir() or root.is_symlink() or not (root / "index.php").is_file():
        raise VhostError("Main website document root is not installed or is unsafe")
    if not (root / "app" / "core.php").is_file():
        raise VhostError("SirusSofts CMS must be active before vhosts can be provisioned")
    # The main PHP CMS handles page-type subdomains. An app gets its own PHP docroot.
    target = root if kind == "page" else root / slug
    if target.is_symlink() or not target.is_dir():
        raise VhostError("Project folder missing or is a symlink")
    if kind == "app" and not any((target / p).is_file() for p in ("index.php", "index.html")):
        raise VhostError("Project app has no index.php or index.html")
    resolved = target.resolve(strict=True)
    parent = root.resolve(strict=True)
    if resolved != parent and not resolved.is_relative_to(parent):
        raise VhostError("Project docroot escapes SirusSofts subscription")
    if kind == "app" and resolved != target:
        raise VhostError("Symlinked project path is forbidden")
    return {"slug": slug, "kind": kind, "hostname": f"{slug}.{DOMAIN}",
            "directory_url": f"https://{DOMAIN}/{slug}",
            "subdomain_url": f"https://{slug}.{DOMAIN}",
            "www_root": "/httpdocs" + ("/" + slug if kind == "app" else ""),
            "requires_dns_tls": True}


def read_state(path=STATE):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise VhostError("Invalid saved vhost state")
    return data


def write_state(data, path=STATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="managed.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
    return result.returncode, (result.stdout + "\n" + result.stderr).strip()


def apply_vhost(plan, state_path=STATE, runner=command):
    if not shutil.which("plesk"):
        raise VhostError("Plesk CLI unavailable; no server changes made")
    slug, host = plan["slug"], plan["hostname"]
    state = read_state(state_path)
    owned = state.get(host)
    exists, _ = runner(["plesk", "bin", "subdomain", "--info", host])
    if exists == 0 and not owned:
        raise VhostError("Existing vhost is not managed by SirusSofts; refusing overwrite")
    if exists != 0 and owned:
        raise VhostError("Managed vhost missing in Plesk; manual reconciliation required")
    if owned and owned.get("www_root") == plan["www_root"]:
        return {"status": "unchanged", **plan}
    args = ["plesk", "bin", "subdomain", "--update" if owned else "--create",
            slug, "-domain", DOMAIN, "-www-root", plan["www_root"], "-php", "true"]
    code, _ = runner(args)
    if code:
        raise VhostError("Plesk rejected vhost change; inspect server journal")
    test = shutil.which("nginx")
    if test:
        status, _ = runner([test, "-t"])
        if status:
            if owned:
                runner(["plesk", "bin", "subdomain", "--update", slug,
                        "-domain", DOMAIN, "-www-root", owned["www_root"], "-php", "true"])
            # Newly created subdomains are NOT automatically deleted: Plesk
            # might delete the target's files during subdomain removal.
            raise VhostError("nginx validation failed; previous managed target restored if applicable. Review new vhost manually")
    state[host] = {"slug": slug, "kind": plan["kind"], "www_root": plan["www_root"]}
    write_state(state, state_path)
    return {"status": "applied", **plan,
            "note": "Configure DNS and TLS for this hostname separately; no server reboot"}


class Bridge(socketserver.UnixStreamServer):
    allow_reuse_address = True

    def __init__(self, path, root, state):
        self.root, self.state = root, state
        self.guard = threading.Lock()
        super().__init__(str(path), Handler)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(MAX_LINE + 1)
        try:
            if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise VhostError("Invalid request length")
            message = json.loads(line)
            if not isinstance(message, dict) or set(message) != {"action", "slug", "kind"}:
                raise VhostError("Only action, slug, kind are accepted")
            if message["action"] not in ("preview", "apply", "status"):
                raise VhostError("Unsupported action")
            with self.server.guard:
                plan = validate(message["slug"], message["kind"], self.server.root)
                if message["action"] == "preview":
                    result = {"status": "preview", **plan}
                elif message["action"] == "status":
                    entry = read_state(self.server.state).get(plan["hostname"])
                    result = {"status": "managed" if entry else "unmanaged", **plan}
                else:
                    result = apply_vhost(plan, state_path=self.server.state)
            reply = {"ok": True, "result": result}
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as err:
            reply = {"ok": False, "error": str(err)[:240]}
        except Exception:
            reply = {"ok": False, "error": "Provisioner failure; inspect service journal"}
        self.wfile.write((json.dumps(reply, separators=(",", ":")) + "\n").encode())


def serve(sock=SOCKET, root=HTTPDOCS, state=STATE):
    if os.geteuid() != 0:
        raise RuntimeError("Plesk helper must be started by root under systemd")
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Invalid Plesk document root")
    owner = pwd.getpwuid(root.stat().st_uid)
    if owner.pw_uid == 0:
        raise RuntimeError("Site root must have a non-root Plesk owner")
    sock.parent.mkdir(parents=True, exist_ok=True)
    if sock.exists() or sock.is_symlink():
        raise RuntimeError("Socket exists: remove only after confirming no other instance runs")
    with Bridge(sock, root, state) as server:
        os.chown(sock, 0, owner.pw_gid)
        os.chmod(sock.parent, 0o751)
        os.chmod(sock, 0o660)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            sock.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("serve", "check"))
    args = p.parse_args()
    if args.action == "check":
        print("SirusSofts vhost helper: Python syntax OK. No changes made.")
        return
    serve()


if __name__ == "__main__":
    main()
