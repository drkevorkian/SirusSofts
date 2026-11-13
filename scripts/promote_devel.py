#!/usr/bin/env python3
"""Promote only tested Devel/ changes to main without changing the site checkout.

main is production. The devel branch contains a literal copy of main at Devel/.
This helper applies ONLY Devel/ changes relative to the previous published
Devel snapshot. Unrelated main changes are preserved; conflicts fail closed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(".github/devel-promotion-state.json")
EXCLUDED = {str(STATE)}


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True,
        check=False,
    )
    if check and proc.returncode:
        raise RuntimeError(f"Git command failed: {args[0]}")
    return proc.stdout.decode("utf8", errors="replace").strip()


def tree_files(tree_sha: str) -> dict[str, tuple[str, str]]:
    """Return {path: (mode, blob_sha)} using NUL delimiters for safety."""
    raw = subprocess.run(
        ["git", "ls-tree", "-rz", tree_sha],
        cwd=ROOT, capture_output=True, check=True
    ).stdout
    result: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, filename = record.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = filename.decode("utf8", "surrogateescape")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Unsupported Git object type or file mode: {path}")
        result[path] = (mode, sha)
    return result


def changes(published: dict, candidate: dict, production: dict):
    """Raise on concurrent main edits; otherwise give the smallest safe delta."""
    changeset = {}
    for path in sorted(set(published) | set(candidate)):
        if path in EXCLUDED or path == "Devel" or path.startswith("Devel/"):
            if published.get(path) != candidate.get(path):
                raise ValueError(f"Development attempted to change reserved path {path}")
            continue
        before = published.get(path)
        after = candidate.get(path)
        if before == after:
            continue
        current = production.get(path)
        if current != before and current != after:
            raise ValueError(
                f"main changed independently at {path}; do not overwrite production"
            )
        if current != after:
            changeset[path] = after
    return changeset


def run(*, publish: bool) -> int:
    if git("branch", "--show-current") != "main":
        raise ValueError("Promotion must run from the production main checkout")
    if git("status", "--porcelain"):
        raise ValueError("Working tree must be clean before promotion")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise ValueError("Local main is not the latest origin/main")
    state = json.loads((ROOT / STATE).read_text(encoding="utf8"))
    last_source = state["last_published_devel_sha"]
    source_sha = git("rev-parse", "origin/devel")
    old_tree = git("rev-parse", f"{last_source}:Devel")
    new_tree = git("rev-parse", f"{source_sha}:Devel")
    main_tree = git("rev-parse", "HEAD^{tree}")
    plan = changes(
        tree_files(old_tree), tree_files(new_tree), tree_files(main_tree)
    )
    print(f"Latest tested devel SHA: {source_sha[:12]}")
    print(f"Changed production files: {len(plan)}")
    for path in plan:
        print(f"  {'DELETE' if plan[path] is None else 'UPDATE'} {path}")
    if not publish:
        print("DRY RUN: no files or refs changed")
        return 0

    for path, entry in plan.items():
        if entry is None:
            git("update-index", "--force-remove", "--", path)
        else:
            mode, sha = entry
            git("update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}")
    (ROOT / STATE).write_text(
        json.dumps({"last_published_devel_sha": source_sha}, indent=2) + "\n",
        encoding="utf8",
    )
    git("add", "--", str(STATE))
    git("diff", "--cached", "--check")
    if not git("diff", "--cached", "--name-only"):
        print("PASS main already contains all tested Devel changes")
        return 0
    git("commit", "-m", f"Promote verified Devel snapshot {source_sha[:12]} to main")
    # Fast-forward only: never force-push, never replace concurrent main work.
    git("push", "origin", "HEAD:refs/heads/main")
    current = git("rev-parse", "HEAD")
    print(f"PASS main updated to {current}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    exclusive = parser.add_mutually_exclusive_group(required=True)
    exclusive.add_argument("--check", action="store_true")
    exclusive.add_argument("--dry-run", action="store_true")
    exclusive.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.check:
        sample = {"CMS/index.php": ("100644", "a")}
        assert changes(sample, {"CMS/index.php": ("100644", "b")}, sample) == {
            "CMS/index.php": ("100644", "b")
        }
        try:
            changes(sample, {"CMS/index.php": ("100644", "b")}, {
                "CMS/index.php": ("100644", "c")
            })
        except ValueError:
            pass
        else:
            raise AssertionError("Production conflict was not rejected")
        print("PASS main promotion planner detects changes and refuses drift")
        return 0
    try:
        return run(publish=args.publish)
    except (ValueError, KeyError, OSError, RuntimeError,
            subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"FAIL promotion stopped safely: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
