#!/usr/bin/env python3
"""Bounded, unauthenticated production HTTPS security smoke check.

Owner-authorized tests for the fixed SirusSofts domain. Does not log in,
fetch secrets, follow redirects, send modifying requests or change a host.
Use --check for no-network preflight, --run for public HTTPS verification.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import http.client
import json
from pathlib import Path
import ssl
import sys
import time

DOMAIN = "sirussofts.com"
REPORT = Path("security-results/external-audit.json")
TIMEOUT = 8.0
# Never request source files from /app etc with an authenticated session.
ROUTES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("homepage", "/", (200,)),
    ("admin-login", "/admin/login", (200,)),
    ("private-php-app", "/app/core.php", (403, 404)),
    ("private-php-bin", "/bin/setup.php", (403, 404)),
    ("private-test-suite", "/tests/smoke.php", (403, 404)),
    ("private-directory", "/private/", (403, 404)),
    ("private-database", "/db.sqlite3", (403, 404)),
    ("gentoo-protected-page", "/Gentoo/", (401,)),
    ("gentoo-token", "/Gentoo/live-token", (401,)),
    ("gentoo-javascript", "/Gentoo/dashboard_live.js", (401,)),
    ("gentoo-invalid-websocket", "/Gentoo/live", (403,)),
)


@dataclass(frozen=True)
class Result:
    route: str
    path: str
    status: int | None
    pass_check: bool
    reason: str


def evaluate(name: str, path: str, expected: tuple[int, ...],
             status: int | None, body: bytes = b"") -> Result:
    passed = status in expected
    reason = "expected status" if passed else "unexpected status / TLS or network error"
    if name == "gentoo-invalid-websocket" and passed:
        passed = b"WebSocket authentication rejected" in body
        reason = ("signed WebSocket authentication boundary reached" if passed
                  else "HTTP 403 did not originate from Gentoo signed-token rejection")
    return Result(name, path, status, passed, reason)


def get(path: str, *, invalid_websocket: bool = False) -> tuple[int, bytes]:
    headers = {
        "User-Agent": "SirusSofts-Public-Security-Audit/1.0",
        "Accept": "text/html,application/json,text/plain",
        "Connection": "close",
    }
    if invalid_websocket:
        headers.update({
            "Origin": "https://" + DOMAIN,
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Protocol": "sirus-live, sirus-auth.invalid",
        })
    # A hostname-verified TLS connection; no HTTP or 3xx redirect fallback.
    connection = http.client.HTTPSConnection(
        DOMAIN, 443, timeout=TIMEOUT, context=ssl.create_default_context()
    )
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(2048)
    finally:
        connection.close()


def run() -> list[Result]:
    results = []
    for name, path, expected in ROUTES:
        try:
            status, body = get(path, invalid_websocket=name == "gentoo-invalid-websocket")
            result = evaluate(name, path, expected, status, body)
        except (OSError, ssl.SSLError, TimeoutError,
                http.client.HTTPException):
            result = evaluate(name, path, expected, None)
        results.append(result)
        print(f"{'PASS' if result.pass_check else 'FAIL'} {name}: "
              f"HTTP {result.status if result.status is not None else 'unavailable'}; "
              f"{result.reason}", flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    opts = parser.add_mutually_exclusive_group(required=True)
    opts.add_argument("--check", action="store_true")
    opts.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert len(ROUTES) == len({path for _, path, _ in ROUTES})
        assert all(path.startswith("/") and not path.startswith("//")
                   and isinstance(codes, tuple) for _, path, codes in ROUTES)
        assert evaluate("homepage", "/", (200,), 200).pass_check
        assert not evaluate("private-php-app", "/app/core.php", (403, 404), 200).pass_check
        assert not evaluate("gentoo-invalid-websocket", "/Gentoo/live",
                            (403,), 403, b"wrong rejection").pass_check
        print("PASS bounded unauthenticated SirusSofts HTTPS security audit preflight")
        return 0
    started = int(time.time())
    results = run()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "domain": DOMAIN, "started_at_unix": started, "read_only": True,
        "checks": [asdict(item) for item in results],
        "all_passed": all(item.pass_check for item in results),
    }
    REPORT.write_text(json.dumps(document, indent=2, ensure_ascii=True) + "\n",
                      encoding="utf8")
    print(f"Summary: {sum(x.pass_check for x in results)}/{len(results)} passed")
    return 0 if document["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
