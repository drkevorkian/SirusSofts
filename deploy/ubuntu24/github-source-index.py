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
    now = int(time.time()) if now is None else int(now)
    # Resolve SHA first, then request each file pinned to that immutable SHA.
    commit = reader("/commits/" + quote(BRANCH, safe=""), token)
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("GitHub branch did not return a valid commit")
    entries = []
    for platform, paths in SOURCES.items():
        for path in paths:
            result = reader("/contents/" + quote(path, safe="/") + "?ref=" + sha, token)
            if not isinstance(result, dict) or result.get("type") != "file" or result.get("encoding") != "base64":
                raise RuntimeError("Missing or unsupported source artifact")
            raw = base64.b64decode(result["content"], validate=False)
            if len(raw) > 2_000_000:
                raise RuntimeError("Source file exceeds fixed limit")
            entries.append({"platform": platform, "path": path,
                            "sha256": hashlib.sha256(raw).hexdigest()})
    return {"repository": REPO, "branch": BRANCH,
            "commit": sha, "checked_at": now, "files": entries}


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
    if not requested and time.time() - last < 21600:
        print("Source manifest current; no GitHub request required")
        return
    token = Path(args.token_file).read_text(encoding="utf8").strip()
    if len(token) < 20 or "\n" in token:
        raise RuntimeError("GitHub credential missing")
    index = refresh(token)
    write_index(index)
    if requested and REQUEST.is_file() and not REQUEST.is_symlink():
        REQUEST.unlink()
    print("PASS GitHub source fingerprint check; commit " + index["commit"][:12])


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print("FAIL source indexer: " + type(error).__name__ + ": " + str(error))
        raise SystemExit(1)
