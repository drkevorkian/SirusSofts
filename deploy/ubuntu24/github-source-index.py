#!/usr/bin/env python3
"""Fetch approved SirusSofts GitHub file fingerprints into private local metadata.

Uses the existing restricted GitHub importer credential via systemd LoadCredential.
Never downloads executable code to a deployment path or changes packages.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO = "drkevorkian/SirusSofts"
BRANCH = "feature/cms-vhost-distributed-nodes"
API = "https://api.github.com/repos/" + REPO
ROOT = Path("/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes")
TARGET = ROOT / "source_index.json"
REQUEST = ROOT / "source_refresh.request"
SOURCES = {
    "ubuntu": (
        "CMS/app/admin.php", "CMS/app/operations.php", "CMS/assets/live.js",
        "ubuntu-worker/worker.py", "realtime/server.py",
    ),
    "gentoo": (
        "gentoo-node/client.py", "gentoo-node/dashboard.py",
        "gentoo-node/tunnel.py",
    ),
}


def api_json(path, token):
    request = Request(API + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "SirusSofts-Source-Update-Indexer/1",
    })
    try:
        with urlopen(request, timeout=12) as result:
            data = result.read(2000000)
            if len(data) >= 2000000:
                raise ValueError("GitHub response too large")
    except HTTPError as error:
        raise RuntimeError("GitHub HTTP " + str(error.code)) from None
    except (OSError, URLError):
        raise RuntimeError("GitHub unavailable") from None
    return json.loads(data)


def refresh(token, reader=api_json, *, now=None):
    """Index up to three pinned release candidates without installing code.

    Older commits lacking required framework components are excluded rather
    than publishing an incomplete installable manifest.
    """
    now = int(time.time()) if now is None else int(now)
    commit = reader("/commits/" + quote(BRANCH, safe=""), token)
    head = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError("GitHub branch did not return a valid commit")
    raw_recent = reader("/commits?sha=" + quote(BRANCH, safe="") + "&per_page=3", token)
    recent = raw_recent if isinstance(raw_recent, list) else []
    revisions = [(head, "Approved branch HEAD")]
    for item in recent[:3]:
        sha = item.get("sha") if isinstance(item, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        if any(previous == sha for previous, _ in revisions):
            continue
        message = item.get("commit", {}).get("message", "") if isinstance(item.get("commit"), dict) else ""
        message = message.splitlines()[0][:160] if isinstance(message, str) and message.strip() else "Previous revision"
        revisions.append((sha, message))
        if len(revisions) >= 3:
            break
    candidates = []
    for sha, message in revisions:
        entries = []
        try:
            for platform, paths in SOURCES.items():
                for path in paths:
                    result = reader("/contents/" + quote(path, safe="/") + "?ref=" + sha, token)
                    if (not isinstance(result, dict) or result.get("type") != "file"
                            or result.get("encoding") != "base64"):
                        raise RuntimeError("Missing or unsupported source artifact")
                    raw = base64.b64decode(result["content"], validate=False)
                    if len(raw) > 2_000_000:
                        raise RuntimeError("Source file exceeds fixed limit")
                    entries.append({"platform": platform, "path": path,
                                    "sha256": hashlib.sha256(raw).hexdigest()})
        except (RuntimeError, ValueError, KeyError, TypeError):
            if sha == head:
                raise
            # Older framework revisions may not contain the live socket yet.
            continue
        candidates.append({"commit": sha, "message": message, "files": entries})
    if not candidates or candidates[0]["commit"] != head:
        raise RuntimeError("Approved branch HEAD could not be indexed")
    return {"repository": REPO, "branch": BRANCH,
            "commit": head, "checked_at": now, "files": candidates[0]["files"],
            "candidates": candidates}


def write_index(index, path=TARGET):
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("Unsafe source index destination")
    fd, name = tempfile.mkstemp(prefix=".source-index-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as output:
            json.dump(index, output, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stage_approved(token, reader=api_json, *, root=ROOT,
                   database=None, index=None, now=None):
    """Stage approved immutable source bytes privately without executing them."""
    import sqlite3
    import shutil
    now = int(time.time()) if now is None else int(now)
    database = database or root.parent / "data" / "cms.sqlite3"
    index = index or root / "source_index.json"
    if (not database.is_file() or database.is_symlink()
            or not index.is_file() or index.is_symlink()):
        return 0
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        try:
            plans = [dict(row) for row in db.execute("""
                SELECT id,host,source_path,commit_sha,target_sha256,baseline_sha256,
                       approved_at FROM cms_source_release_plans
                WHERE state='approved' ORDER BY approved_at DESC LIMIT 15""")]
        except sqlite3.OperationalError:
            return 0
    if not plans:
        return 0
    if shutil.disk_usage(root).free < 256 * 1024 * 1024:
        raise RuntimeError("Insufficient free disk space for private source staging")
    document = json.loads(index.read_text(encoding="utf8"))
    candidates = document.get("candidates", [])
    revisions = {}
    for candidate in candidates[:3]:
        if isinstance(candidate, dict) and isinstance(candidate.get("files"), list):
            revisions[candidate.get("commit")] = {
                (part.get("platform"), part.get("path")): part.get("sha256")
                for part in candidate["files"] if isinstance(part, dict)
            }
    directory = root / "source_artifacts"
    if directory.is_symlink():
        raise RuntimeError("Unsafe source artifact directory")
    completed = 0
    for plan in plans:
        ident, host, path = plan["id"], plan["host"], plan["source_path"]
        sha, expected, baseline = plan["commit_sha"], plan["target_sha256"], plan["baseline_sha256"]
        platform = "gentoo" if host == "gentoo-home" else "ubuntu" if host == "ubuntu-primary" else ""
        if (not re.fullmatch(r"[a-f0-9]{32}", ident)
                or not re.fullmatch(r"[a-f0-9]{40}", sha)
                or not re.fullmatch(r"[a-f0-9]{64}", expected)
                or not re.fullmatch(r"[a-f0-9]{64}", baseline)
                or path not in SOURCES.get(platform, ())
                or revisions.get(sha, {}).get((platform, path)) != expected
                or not 0 <= now - int(plan["approved_at"]) <= 86400):
            continue
        artifact, manifest = directory / (ident + ".source"), directory / (ident + ".json")
        if artifact.is_symlink() or manifest.is_symlink():
            raise RuntimeError("Unsafe source artifact path")
        if artifact.is_file() and manifest.is_file():
            if hashlib.sha256(artifact.read_bytes()).hexdigest() == expected:
                continue
        result = reader("/contents/" + quote(path, safe="/") + "?ref=" + sha, token)
        if (not isinstance(result, dict) or result.get("type") != "file"
                or result.get("encoding") != "base64"):
            raise RuntimeError("GitHub returned an invalid source artifact")
        raw = base64.b64decode(result["content"], validate=False)
        if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError("Staged source does not match the approved SHA-256")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".source-", dir=str(directory))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, artifact)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        data = {"id": ident, "host": host, "path": path, "commit": sha,
                "target_sha256": expected, "baseline_sha256": baseline,
                "staged_at": now, "ready_for_manual_review": True}
        fd, temporary = tempfile.mkstemp(prefix=".source-meta-", dir=str(directory))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf8") as output:
                json.dump(data, output, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, manifest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        completed += 1
    return completed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--token-file")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.check:
        print("PASS fixed GitHub source manifest validator")
        return
    if not args.once or not args.token_file or os.geteuid() == 0:
        raise RuntimeError("Run as unprivileged scheduled service with token credential")
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise RuntimeError("Private CMS node directory missing")
    requested = REQUEST.is_file()
    last = 0
    if TARGET.is_file() and not TARGET.is_symlink():
        try:
            last = int(json.loads(TARGET.read_text(encoding="utf8")).get("checked_at", 0))
        except (ValueError, OSError):
            last = 0
    token = Path(args.token_file).read_text(encoding="utf8").strip()
    if len(token) < 20 or "\n" in token:
        raise RuntimeError("GitHub credential missing")
    if requested or time.time() - last >= 21600:
        index = refresh(token)
        write_index(index)
        if requested and REQUEST.is_file() and not REQUEST.is_symlink():
            REQUEST.unlink()
        print("PASS GitHub source fingerprint check; commit " + index["commit"][:12])
    count = stage_approved(token)
    if count:
        print("PASS privately staged " + str(count) + " approved artifacts; not installed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print("FAIL source indexer: " + type(error).__name__ + ": " + str(error))
        raise SystemExit(1)
