#!/usr/bin/env python3
"""Read-only Ubuntu Nginx audit for /Gentoo/live (no IPs or secrets printed).

Run on the Ubuntu VPS:
    sudo python3 -B deploy/ubuntu24/audit-gentoo-websocket.py

WebSocket upgrades return 101. A 401/403/404/502 indicates rejection before
upgrade. The audit never prints request headers, tokens, cookies, or IPs.
"""
from __future__ import annotations
import argparse
from collections import Counter, deque
from pathlib import Path
import re

# Strictly match the exact public WebSocket path, not token or JS endpoints.
LINE = re.compile(
    r'\[([^\]]+)\]\s+"(?:GET)\s+/Gentoo/live\s+HTTP/[0-9.]+"\s+(\d{3})\b'
)
POSSIBLE_LOGS = (
    Path("/var/log/nginx/domains/sirussofts.com.log"),
    Path("/var/log/nginx/domains/sirussofts.com.ssl.log"),
    Path("/var/log/nginx/access.log"),
)
LIVE_ROUTE = Path("/home/sirusweb/conf/web/sirussofts.com/nginx.ssl.conf_sirusgentoolive")


def scan_log(path: Path, *, rows: int = 15) -> list[tuple[str, str]]:
    if path.is_symlink() or not path.is_file():
        return []
    recent: deque[tuple[str, str]] = deque(maxlen=rows)
    try:
        with path.open(encoding="utf8", errors="replace") as src:
            for line in src:
                match = LINE.search(line)
                if match:
                    recent.append((match.group(1), match.group(2)))
    except (PermissionError, OSError):
        return []
    return list(recent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        sample = ('127.0.0.1 - - [12/Nov/2026:06:14:20 -0700] '
                  '"GET /Gentoo/live HTTP/1.1" 403 22 "-" "-"')
        assert LINE.search(sample).group(2) == "403"
        assert not LINE.search(sample.replace("/Gentoo/live ", "/Gentoo/live-token "))
        print("PASS sanitized Gentoo Nginx WebSocket log audit")
        return 0

    if LIVE_ROUTE.is_file() and not LIVE_ROUTE.is_symlink():
        route = LIVE_ROUTE.read_text(encoding="utf8", errors="replace")
        installed = ("location = /Gentoo/live {" in route and
                     "proxy_pass http://127.0.0.1:18480/live;" in route)
        print("Installed Hestia WebSocket proxy fragment:", "present" if installed else "unexpected")
    else:
        print("Installed Hestia WebSocket proxy fragment: missing/unreadable")

    any_entries = False
    for path in POSSIBLE_LOGS:
        entries = scan_log(path)
        if not entries:
            continue
        any_entries = True
        print("Access log:", path)
        print("Recent /Gentoo/live HTTP status counts:", dict(Counter(status for _,status in entries)))
        for date, status in entries:
            print(" ", date, "status", status)
    if not any_entries:
        print("No matching /Gentoo/live entries found in the standard Nginx access logs.")
        print("This may mean a different Hestia log path or logging is disabled.")
        return 1
    print("Interpretation: 101 = upgraded; 403/401 = rejected; 502/504 = upstream error.")
    print("This audit reads logs only; it makes no server, firewall, or SSH changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
