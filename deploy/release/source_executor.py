#!/usr/bin/env python3
"""Guarded local SirusSofts source-code deployment and rollback executor.

A separate *root console* program. It intentionally exposes no HTTP endpoint,
does not process untrusted commands from the CMS and has no GitHub credentials.
The existing CMS/Ubuntu/Gentoo services remain non-root.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

ALLOWED = {
    "ubuntu-primary": {
        "ubuntu-worker/worker.py": (
            "/usr/local/libexec/sirus-ubuntu-worker.py",
            ("systemctl", "restart", "sirus-ubuntu-worker.service"),
            ("systemctl", "is-active", "--quiet", "sirus-ubuntu-worker.service")),
        "realtime/server.py": (
            "/usr/local/libexec/sirus-live-socket.py",
            ("systemctl", "restart", "sirus-live-socket.service"),
            ("systemctl", "is-active", "--quiet", "sirus-live-socket.service")),
    },
    "gentoo-home": {
        "gentoo-node/client.py": (
            "/opt/sirus-gentoo-node/client.py",
            ("rc-service", "sirus-gentoo-node", "restart"),
            ("rc-service", "sirus-gentoo-node", "status")),
        "gentoo-node/dashboard.py": (
            "/opt/sirus-gentoo-node/dashboard.py",
            ("rc-service", "sirus-gentoo-dashboard", "restart"),
            ("rc-service", "sirus-gentoo-dashboard", "status")),
    },
}
STAGING = {
    "ubuntu-primary": Path("/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/source_artifacts"),
    "gentoo-home": Path("/var/lib/sirusnode/source_artifacts"),
}
CMS_DB = Path("/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3")
BACKUPS = Path("/var/lib/sirussofts/source-releases")
LOCK = Path("/run/lock/sirus-source-release.lock")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def ensure_regular(path: Path, *, max_bytes=2_000_000) -> None:
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("Source/target must be an existing regular file (not a symlink)")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= max_bytes:
        raise ReleaseError("Unsafe file type, hard link, empty file, or oversized file")


def check_host(host: str, *, etc=Path("/etc")) -> None:
    if host == "ubuntu-primary":
        info = (etc / "os-release").read_text(encoding="utf8")
        if not re.search(r'(?m)^ID="?ubuntu"?$', info):
            raise ReleaseError("This is not the approved Ubuntu host OS")
    elif host == "gentoo-home":
        if not (etc / "gentoo-release").is_file():
            raise ReleaseError("This is not the approved Gentoo host OS")
    else:
        raise ReleaseError("Host is not allowlisted")


def check_approval(db_path: Path, release: dict, *, now=None) -> None:
    """Ubuntu uses the CMS approval DB; Gentoo transport approval is not built."""
    now = int(time.time()) if now is None else int(now)
    if not db_path.is_file() or db_path.is_symlink():
        raise ReleaseError("Private CMS approval database is missing")
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT id,host,source_path,commit_sha,
                target_sha256,baseline_sha256,state,approved_at
                FROM cms_source_release_plans WHERE id=?""", (release["id"],)).fetchone()
    except sqlite3.Error:
        raise ReleaseError("Release approval database unavailable") from None
    if row is None or any(row[col] != release[key] for col, key in (
        ("host", "host"), ("source_path", "path"),
        ("commit_sha", "commit"), ("target_sha256", "target_sha256"),
        ("baseline_sha256", "baseline_sha256"))):
        raise ReleaseError("Approval database does not match staged artifact")
    if row["state"] != "approved" or not 0 <= now - int(row["approved_at"]) <= 86400:
        raise ReleaseError("Release is not currently approved")


@dataclass(frozen=True)
class Release:
    ident: str
    host: str
    path: str
    commit: str
    target_sha: str
    baseline_sha: str
    staged: Path
    destination: Path
    restart: tuple
    health: tuple


def load_release(host: str, ident: str, *, staged_root: Path | None = None,
                 db_path: Path = CMS_DB, confirm_approval=True,
                 now=None) -> Release:
    if not HEX32.fullmatch(ident) or host not in ALLOWED:
        raise ReleaseError("Invalid host or release ID")
    root = staged_root or STAGING[host]
    if root.is_symlink() or not root.is_dir():
        raise ReleaseError("Private release staging directory is not available")
    manifest = root / (ident + ".json")
    file = root / (ident + ".source")
    ensure_regular(manifest, max_bytes=2048)
    ensure_regular(file)
    try:
        values = json.loads(manifest.read_text(encoding="utf8"))
    except (UnicodeError, ValueError):
        raise ReleaseError("Release manifest is invalid") from None
    if not isinstance(values, dict) or values.get("id") != ident or values.get("host") != host:
        raise ReleaseError("Release manifest has incorrect host or ID")
    path = values.get("path")
    commit = values.get("commit")
    target = values.get("target_sha256")
    baseline = values.get("baseline_sha256")
    timestamp = values.get("staged_at")
    if (not isinstance(path, str) or path not in ALLOWED[host]
            or not isinstance(commit, str) or not HEX40.fullmatch(commit)
            or not isinstance(target, str) or not HEX64.fullmatch(target)
            or not isinstance(baseline, str) or not HEX64.fullmatch(baseline)
            or not isinstance(timestamp, int) or not isinstance(values.get("ready_for_manual_review"), bool)
            or values["ready_for_manual_review"] is not True):
        raise ReleaseError("Release manifest fields failed validation")
    now = int(time.time()) if now is None else int(now)
    if not 0 <= now - timestamp <= 86400:
        raise ReleaseError("Staged source expired; approve and stage a new revision")
    if digest(file) != target:
        raise ReleaseError("Staged artifact hash does not match approved source")
    destination, restart, health = ALLOWED[host][path]
    dest = Path(destination)
    if dest.parent.is_symlink():
        raise ReleaseError("Destination parent cannot be a symlink")
    ensure_regular(dest)
    if confirm_approval:
        if host != "ubuntu-primary":
            raise ReleaseError("Gentoo signed deployment authorization not implemented yet")
        check_approval(db_path, values, now=now)
    return Release(ident, host, path, commit, target, baseline, file, dest, restart, health)


def release_from_backup(host: str, ident: str, *,
                        backup_root: Path = BACKUPS) -> Release:
    """Recover from a root-owned journal, even after approval/staging expiry."""
    if host not in ALLOWED or not HEX32.fullmatch(ident):
        raise ReleaseError("Invalid rollback host or ID")
    directory = backup_root / ident
    if directory.is_symlink() or not directory.is_dir():
        raise ReleaseError("Root-owned release backup directory missing")
    record_file = directory / "journal.json"
    backup = directory / "previous.source"
    ensure_regular(record_file, max_bytes=4096)
    ensure_regular(backup)
    try:
        record = json.loads(record_file.read_text(encoding="utf8"))
    except (UnicodeError, ValueError):
        raise ReleaseError("Release journal is invalid") from None
    path = record.get("path")
    commit = record.get("commit")
    baseline = record.get("baseline_sha256")
    target = record.get("target_sha256")
    if (record.get("id") != ident or record.get("host") != host
            or not isinstance(path, str) or path not in ALLOWED[host]
            or not isinstance(commit, str) or not HEX40.fullmatch(commit)
            or not isinstance(baseline, str) or not HEX64.fullmatch(baseline)
            or not isinstance(target, str) or not HEX64.fullmatch(target)
            or record.get("state") not in ("verified", "rollback_required")
            or digest(backup) != baseline):
        raise ReleaseError("Root-owned recovery record failed validation")
    destination, restart, health = ALLOWED[host][path]
    dest = Path(destination)
    ensure_regular(dest)
    return Release(ident, host, path, commit, target, baseline, backup,
                   dest, restart, health)


def preflight(release: Release, *, backup_root: Path = BACKUPS,
              run=subprocess.run, free_bytes=None) -> dict:
    if digest(release.destination) != release.baseline_sha:
        raise ReleaseError("Installed file drifted from the approved baseline")
    if free_bytes is None:
        free_bytes = shutil.disk_usage(release.destination.parent).free
    if free_bytes < 512 * 1024 * 1024:
        raise ReleaseError("Under 512 MiB available: refuse deployment")
    if backup_root.is_symlink():
        raise ReleaseError("Backup root cannot be a symlink")
    # A complete application release is needed for framework PHP and SSH tunnel.
    # This executor limits itself to four reviewed Python services.
    try:
        compile(release.staged.read_bytes(), str(release.staged), "exec")
    except (SyntaxError, ValueError):
        raise ReleaseError("Staged Python file failed syntax verification") from None
    health = run(release.health, capture_output=True, text=True, timeout=12, check=False)
    if health.returncode != 0:
        raise ReleaseError("Target service is not running; manual recovery required")
    return {"release": release.ident, "host": release.host,
            "component": release.path, "commit": release.commit,
            "baseline_sha256": release.baseline_sha,
            "target_sha256": release.target_sha,
            "mode": "preflight_only", "eligible": True}


def run_health(release: Release, *, run=subprocess.run) -> None:
    result = run(release.health, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        raise ReleaseError("Updated service failed its health check")


def _atomic_json(path: Path, data: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".journal-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as output:
            json.dump(data, output, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _atomic_replace(source: Path, target: Path) -> None:
    old = target.stat()
    fd, name = tempfile.mkstemp(prefix=".sirus-release-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, stat.S_IMODE(old.st_mode))
        os.chown(name, old.st_uid, old.st_gid)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply(release: Release, *, backup_root: Path = BACKUPS,
          run=subprocess.run) -> dict:
    preflight(release, backup_root=backup_root, run=run)
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup_root.is_symlink() or stat.S_IMODE(backup_root.stat().st_mode) != 0o700:
        raise ReleaseError("Backup root must be a private 0700 directory")
    directory = backup_root / release.ident
    if directory.exists() or directory.is_symlink():
        raise ReleaseError("Release ID already executed or recovery folder exists")
    directory.mkdir(mode=0o700)
    backup = directory / "previous.source"
    staged_copy = directory / "approved.source"
    journal = directory / "journal.json"
    shutil.copy2(release.destination, backup, follow_symlinks=False)
    os.chmod(backup, 0o600)
    if digest(backup) != release.baseline_sha:
        raise ReleaseError("Backup hash mismatch; deployment aborted")
    # Stage an immutable root-owned copy before modifying the live service.
    # The browser-owned PHP account cannot race a replacement afterward.
    shutil.copyfile(release.staged, staged_copy)
    os.chmod(staged_copy, 0o600)
    if digest(staged_copy) != release.target_sha:
        raise ReleaseError("Approved source changed during staging; deployment aborted")
    record = {
        "id": release.ident, "host": release.host, "path": release.path,
        "commit": release.commit, "baseline_sha256": release.baseline_sha,
        "target_sha256": release.target_sha, "created_at": int(time.time()),
        "state": "backup_complete"
    }
    _atomic_json(journal, record)
    try:
        # Recheck after completing backup, before replacing the running code.
        if digest(release.destination) != release.baseline_sha:
            raise ReleaseError("Installed source changed during backup")
        _atomic_replace(staged_copy, release.destination)
        record["state"] = "installed_unverified"
        _atomic_json(journal, record)
        if digest(release.destination) != release.target_sha:
            raise ReleaseError("Installed file hash verification failed")
        restarted = run(release.restart, capture_output=True, text=True, timeout=30, check=False)
        if restarted.returncode != 0:
            raise ReleaseError("Service restart failed")
        run_health(release, run=run)
        record["state"] = "verified"
        _atomic_json(journal, record)
    except (ReleaseError, OSError, subprocess.SubprocessError) as exc:
        record["state"] = "rollback_required"
        _atomic_json(journal, record)
        _atomic_replace(backup, release.destination)
        run(release.restart, capture_output=True, text=True, timeout=30, check=False)
        if digest(release.destination) == release.baseline_sha:
            record["state"] = "rolled_back"
            _atomic_json(journal, record)
        raise ReleaseError("Deployment failed and rollback was attempted: " +
                           type(exc).__name__) from None
    return record


def rollback(release: Release, *, backup_root: Path = BACKUPS,
             run=subprocess.run) -> dict:
    directory = backup_root / release.ident
    journal, backup = directory / "journal.json", directory / "previous.source"
    if directory.is_symlink() or journal.is_symlink() or backup.is_symlink():
        raise ReleaseError("Unsafe rollback path")
    ensure_regular(journal, max_bytes=4096)
    ensure_regular(backup)
    record = json.loads(journal.read_text(encoding="utf8"))
    if (record.get("id") != release.ident or record.get("host") != release.host
            or record.get("path") != release.path or record.get("commit") != release.commit
            or record.get("baseline_sha256") != release.baseline_sha
            or record.get("target_sha256") != release.target_sha
            or record.get("state") not in ("verified", "rollback_required")):
        raise ReleaseError("No matching verified release to roll back")
    if digest(backup) != release.baseline_sha:
        raise ReleaseError("Rollback backup corrupted")
    if digest(release.destination) != release.target_sha:
        raise ReleaseError("Installed version drifted; manual investigation required")
    _atomic_replace(backup, release.destination)
    result = run(release.restart, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        record["state"] = "rollback_service_failed"
        _atomic_json(journal, record)
        raise ReleaseError("Source restored but old service failed to restart")
    record["state"] = "rolled_back"
    _atomic_json(journal, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--host", choices=tuple(ALLOWED))
    parser.add_argument("--id")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--rollback", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.check:
        print("PASS fixed-component source executor available; no files changed")
        return
    if not args.host or not args.id or not (args.plan or args.apply or args.rollback):
        parser.error("Host, release ID and exactly one mode are required")
    if os.geteuid() != 0:
        raise ReleaseError("Privileged operations require a local root console")
    check_host(args.host)
    LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with LOCK.open("w") as locked:
        try:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError("Another release operation is running") from None
        if args.rollback:
            release = release_from_backup(args.host, args.id)
            if args.confirm != "ROLLBACK " + release.commit[:12]:
                raise ReleaseError("Exact ROLLBACK commit confirmation required")
            result = rollback(release)
        else:
            # The local operator must have physically transferred the approved
            # artifact to Gentoo. It is never fetched from an arbitrary URL.
            release = load_release(args.host, args.id,
                                   confirm_approval=args.host == "ubuntu-primary")
            if args.plan:
                result = preflight(release)
            else:
                if args.confirm != "DEPLOY " + release.commit[:12]:
                    raise ReleaseError("Exact DEPLOY commit confirmation required")
                result = apply(release)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ReleaseError, OSError, subprocess.SubprocessError) as error:
        print("FAIL release executor: " + str(error), file=sys.stderr)
        raise SystemExit(1)
