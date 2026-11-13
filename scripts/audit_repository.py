#!/usr/bin/env python3
"""Whole-repository, read-only source checks for the single production main tree.

No imports of application modules, no installers, no network and no service
connections. Unit and integration tests run separately in GitHub Actions.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED_BRANCH_DOCUMENT = "docs/RECOVERY_CHECKPOINT.md"
OBSOLETE_BRANCH = "feature/cms-vhost-distributed-nodes"
SCANNED_EXTENSIONS = {".py", ".php", ".sh", ".js", ".json"}
TOOL = {".php": ["php", "-l"], ".sh": ["bash", "-n"], ".js": ["node", "--check"]}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    return [
        ROOT / name.decode("utf8", "surrogateescape")
        for name in result.stdout.split(b"\x00") if name
    ]


def audit() -> tuple[dict[str, int], list[str]]:
    counts: Counter[str] = Counter()
    errors: list[str] = []
    files = tracked_files()
    names = {str(path.relative_to(ROOT)) for path in files}
    if any(name == "Devel" or name.startswith("Devel/") for name in names):
        errors.append("Unexpected development mirror in the production main tree")
    if ".github/workflows/promote-devel.yml" in names:
        errors.append("Old branch-promotion workflow must not be present in main")
    if not {"CMS/index.php", "gentoo-node/dashboard.py",
            "deploy/ubuntu24/github-source-index.py"} <= names:
        errors.append("Required SirusSofts production entrypoints are missing")

    for file in files:
        rel = str(file.relative_to(ROOT))
        suffix = file.suffix.lower()
        counts["tracked"] += 1
        if file.is_symlink():
            errors.append(f"{rel}: tracked symlink not allowed by source audit")
            continue
        if not file.is_file():
            errors.append(f"{rel}: tracked file not present in checkout")
            continue
        if file.stat().st_size > 2_000_000:
            errors.append(f"{rel}: unexpected tracked file larger than 2 MB")
            continue
        if suffix not in SCANNED_EXTENSIONS and suffix != ".md":
            continue
        try:
            raw = file.read_bytes()
            source = raw.decode("utf8")
        except (OSError, UnicodeDecodeError):
            errors.append(f"{rel}: source is not valid UTF-8 text")
            continue
        # Old branch mentions are retained in the historical recovery record,
        # but operational docs, code, and deployment scripts must use main.
        if OBSOLETE_BRANCH in source and rel != IGNORED_BRANCH_DOCUMENT:
            errors.append(f"{rel}: references retired deployment branch")
        if suffix == ".md":
            counts["docs"] += 1
            continue
        counts[suffix] += 1
        try:
            if suffix == ".py":
                compile(source, rel, "exec")
            elif suffix == ".json":
                json.loads(source)
            else:
                result = subprocess.run(
                    [*TOOL[suffix], str(file)], cwd=ROOT,
                    capture_output=True, text=True, timeout=12
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout).splitlines()
                    errors.append(f"{rel}: syntax check failed: " +
                                  (detail[-1][:200] if detail else "tool failed"))
        except (SyntaxError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{rel}: {type(exc).__name__}: {str(exc)[:160]}")
        except OSError as exc:
            errors.append(f"{rel}: required language tool unavailable: {exc.filename}")

    return dict(counts), errors


def main() -> int:
    try:
        counts, errors = audit()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL repository checkout unavailable: {type(exc).__name__}")
        return 1
    print("Repository source syntax coverage:")
    for category in ("tracked", ".py", ".php", ".sh", ".js", ".json", "docs"):
        print(f"  {category}: {counts.get(category, 0)}")
    for message in errors:
        print("FAIL", message)
    print("PASS clean tracked production tree" if not errors
          else f"FAIL {len(errors)} repository source check(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
