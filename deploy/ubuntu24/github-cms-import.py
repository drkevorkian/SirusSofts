#!/usr/bin/env python3
"""Import every GitHub repository visible to a credential into private CMS drafts.

No git clone, code deployment, public publication, source-file fetch or shell
execution. Runs as sirusweb with a systemd LoadCredential token.
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.github.com/user/repos"
PAGE_SIZE = 100
MAX_PAGES = 100
INTERVAL_SECONDS = 6 * 60 * 60
FAILURE_RETRY_SECONDS = 15 * 60
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
RESERVED = {
    "www", "admin", "api", "assets", "projects", "pages", "settings",
    "about", "login", "logout", "private", "install", "setup", "health",
    "favicon", "robots", "system", "feed", "cms", "static", "media",
    "app", "lib", "dashboard", "cpoint", "worldcom", "sentinel-trader",
    "sentinal-trader"
}

def get_page(token, page):
    params = urlencode({
        "per_page": PAGE_SIZE, "page": page,
        "affiliation": "owner,collaborator,organization_member"
    })
    request = Request(API + "?" + params, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "SirusSofts-CMS-Repository-Import/0.3"
    })
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(3 * 1024 * 1024 + 1)
            if len(raw) > 3 * 1024 * 1024:
                raise RuntimeError("GitHub repository page exceeds size limit")
            return json.loads(raw)
    except HTTPError as error:
        raise RuntimeError(f"GitHub API HTTP {error.code}; check token access, scope and API limits") from None
    except (URLError, TimeoutError):
        raise RuntimeError("GitHub API connection failure") from None

def normalize(item):
    if not isinstance(item, dict):
        raise ValueError("Malformed GitHub repository entry")
    rid, name = item.get("id"), item.get("full_name")
    if not isinstance(rid, int) or isinstance(rid, bool) or rid < 1:
        raise ValueError("Invalid GitHub repository ID")
    if not isinstance(name, str) or not REPO.fullmatch(name):
        raise ValueError("Invalid GitHub full repository name")
    owner, slug = name.split("/", 1)
    if owner in (".", "..") or slug in (".", "..") or slug.startswith("-"):
        raise ValueError("Invalid GitHub repository name")
    if not isinstance(item.get("private"), bool):
        raise ValueError("Missing GitHub repository visibility")
    branch = item.get("default_branch") or ""
    if not isinstance(branch, str) or len(branch) > 120:
        raise ValueError("Invalid default branch")
    description = item.get("description") or ""
    if not isinstance(description, str):
        raise ValueError("Invalid repository description")
    return {
        "github_id": rid, "full_name": name,
        "github_private": int(item["private"]),
        "description": description[:1000],
        "default_branch": branch,
        "archived": int(bool(item.get("archived", False))),
        "fork": int(bool(item.get("fork", False))),
        "pushed_at": str(item.get("pushed_at") or "")[:40],
        "html_url": "https://github.com/" + name
    }

def discover(token, reader=get_page):
    result = {}
    for page in range(1, MAX_PAGES + 1):
        response = reader(token, page)
        if not isinstance(response, list) or len(response) > PAGE_SIZE:
            raise RuntimeError("Unexpected GitHub API pagination response")
        for raw in response:
            item = normalize(raw)
            rid = item["github_id"]
            if rid in result and result[rid]["full_name"] != item["full_name"]:
                raise RuntimeError("Conflicting repository IDs in GitHub response")
            result[rid] = item
        if len(response) < PAGE_SIZE:
            if not result:
                raise RuntimeError("No repositories are visible; verify GitHub token grants repository access")
            return list(result.values())
    raise RuntimeError("Exceeded 10,000 accessible repositories; import not applied")

def candidate_slug(name):
    short = name.split("/", 1)[1].lower()
    short = re.sub(r"[^a-z0-9]+", "-", short).strip("-")
    if not short or not short[0].isalpha():
        short = "repo-" + short
    if short in RESERVED or len(short) < 2:
        short = "github-" + short
    short = short[:40].rstrip("-")
    return short if SLUG.fullmatch(short) and short not in RESERVED else "github-repo"

def choose_slug(db, item):
    base = candidate_slug(item["full_name"])
    options = [base, (base[:max(1, 40-len(str(item["github_id"]))-4)] +
                      "-gh-" + str(item["github_id"])).rstrip("-")]
    for slug in options:
        found = db.execute("SELECT 1 FROM cms_projects WHERE slug=?", (slug,)).fetchone()
        if not found and slug not in RESERVED and SLUG.fullmatch(slug):
            return slug
    raise RuntimeError("Cannot reserve an unambiguous CMS route for " + item["full_name"])

def prepare_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS cms_github_repositories (
        github_id INTEGER PRIMARY KEY,
        full_name TEXT NOT NULL UNIQUE,
        project_id INTEGER REFERENCES cms_projects(id) ON DELETE SET NULL,
        github_private INTEGER NOT NULL CHECK(github_private IN (0,1)),
        description TEXT NOT NULL DEFAULT '',
        default_branch TEXT NOT NULL DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0,
        fork INTEGER NOT NULL DEFAULT 0,
        html_url TEXT NOT NULL,
        pushed_at TEXT NOT NULL DEFAULT '',
        seen_at TEXT NOT NULL,
        last_seen_run INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS cms_github_sync_state (
        id INTEGER PRIMARY KEY CHECK(id=1),
        requested_at INTEGER NOT NULL DEFAULT 0,
        attempted_at INTEGER NOT NULL DEFAULT 0,
        success_at INTEGER NOT NULL DEFAULT 0,
        discovered INTEGER NOT NULL DEFAULT 0,
        imported INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("INSERT OR IGNORE INTO cms_github_sync_state(id) VALUES (1)")
    db.execute("CREATE INDEX IF NOT EXISTS cms_github_project_link ON cms_github_repositories(project_id)")

def apply(db, repositories, when):
    now = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute("BEGIN IMMEDIATE")
    try:
        prepare_schema(db)
        old = {int(row[0]) for row in db.execute("SELECT github_id FROM cms_github_repositories")}
        created = 0
        for item in repositories:
            rid = item["github_id"]
            linked = None
            if rid not in old:
                slug = choose_slug(db, item)
                short = item["full_name"].split("/", 1)[1]
                title = short[:100]
                neutral = "Imported GitHub repository. Draft entry: review before publishing."
                cursor = db.execute("""INSERT INTO cms_projects
                    (slug,title,summary,body,category,icon,kind,state,sort_order,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (slug, title, neutral, "", "GitHub", "⌘", "page",
                     "draft", 1000, now, now))
                linked = cursor.lastrowid
                created += 1
                branch = item["default_branch"]
                if (re.fullmatch(r"[A-Za-z0-9._/-]+", branch) and ".." not in branch and
                        not branch.startswith(("-", "/", ".")) and not branch.endswith(".lock")):
                    db.execute("""INSERT INTO cms_hosting_preferences
                        (slug,source_repository,source_ref,release_policy,updated_at)
                        VALUES (?,?,?,'hold',?)""",
                        (slug, item["full_name"], branch, now))
            db.execute("""INSERT INTO cms_github_repositories (
                github_id, full_name, project_id, github_private, description,
                default_branch, archived, fork, html_url, pushed_at, seen_at, last_seen_run
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(github_id) DO UPDATE SET
                full_name=excluded.full_name,
                github_private=excluded.github_private,
                description=excluded.description,
                default_branch=excluded.default_branch,
                archived=excluded.archived,
                fork=excluded.fork,
                html_url=excluded.html_url,
                pushed_at=excluded.pushed_at,
                seen_at=excluded.seen_at,
                last_seen_run=excluded.last_seen_run""",
                (rid, item["full_name"], linked, item["github_private"], item["description"],
                 item["default_branch"], item["archived"], item["fork"], item["html_url"],
                 item["pushed_at"], now, when))
        db.execute("""UPDATE cms_github_sync_state
            SET attempted_at=?,success_at=?,discovered=?,imported=?,last_error=''
            WHERE id=1""", (when, when, len(repositories), created))
        db.commit()
        return created
    except Exception:
        db.rollback()
        raise

def import_due(requested_at, attempted_at, success_at, now, force=False):
    """An authenticated admin request or --force bypasses the timer.
    A failed attempt (attempted > success) gets a short retry, not 6 hours.
    """
    if force or requested_at > attempted_at:
        return True
    if attempted_at == 0:
        return True
    if attempted_at > success_at:
        return now - attempted_at >= FAILURE_RETRY_SECONDS
    return now - success_at >= INTERVAL_SECONDS


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    parser.add_argument("--token-file")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--test-auth", action="store_true",
                        help="Validate credential with GitHub; do not touch CMS SQLite")
    parser.add_argument("--force", action="store_true",
                        help="Bypass schedule and retry a requested import immediately")
    args = parser.parse_args(argv)
    if args.check and (args.test_auth or args.force):
        parser.error("--check cannot be combined with other modes")
    if args.test_auth and args.force:
        parser.error("--test-auth cannot be combined with --force")
    if args.check:
        print("PASS GitHub CMS importer available; no network or database changes")
        return 0
    if not args.token_file or (not args.test_auth and not args.db):
        parser.error("--token-file is required; --db is also required for importing")
    with open(args.token_file, "r", encoding="utf8") as stream:
        token = stream.read().strip()
    if len(token) < 20 or "\n" in token:
        raise RuntimeError("GitHub token is absent or invalid")
    if args.test_auth:
        response=get_page(token,1)
        if not isinstance(response,list):
            raise RuntimeError("GitHub returned an unexpected authentication response")
        print(f"PASS GitHub accepted the credential; {len(response)} repositories on the first visible page.")
        if not response:
            print("WARNING No repositories visible to this credential; review token resource-owner and repository access.")
        return 0
    if not args.db.startswith("/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/"):
        raise RuntimeError("Database must live under the reviewed private CMS path")
    db = sqlite3.connect("file:" + args.db + "?mode=rw", uri=True, timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    prepare_schema(db)
    db.commit()
    row = db.execute("""SELECT requested_at, attempted_at, success_at
                        FROM cms_github_sync_state WHERE id=1""").fetchone()
    current = int(time.time())
    if not import_due(*map(int,row),current,args.force):
        print("PASS GitHub import not due; last success or short error retry interval still applies")
        db.close()
        return 0
    try:
        repositories = discover(token)
        created = apply(db, repositories, current)
    except Exception as error:
        # Never output token or API response body; errors contain generic types/statuses.
        db.execute("""UPDATE cms_github_sync_state SET attempted_at=?,last_error=?
                      WHERE id=1""", (current, str(error)[:180]))
        db.commit()
        raise
    finally:
        db.close()
    print(f"PASS GitHub repository sync: discovered={len(repositories)} new_drafts={created}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print("FAIL GitHub import: " + str(error), file=sys.stderr)
        sys.exit(1)
