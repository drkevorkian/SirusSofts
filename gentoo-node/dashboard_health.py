#!/usr/bin/env python3
"""Wait for the *new* private Gentoo dashboard after OpenRC restarts it.

OpenRC's supervise-daemon reports "started" before Python binds 18080.
Only use anonymous requests to loopback. Never read or print credentials.
"""
from __future__ import annotations
import argparse
import http.client
import time


def probe(port: int = 18080) -> tuple[bool, str]:
    for path, expected in (("/healthz", 200), ("/", 401),
                           ("/live-token", 401), ("/dashboard_live.js", 401)):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            status = response.status
            body = response.read(64)
            if status != expected:
                return False, f"{path}: HTTP {status}, expected {expected}"
            if path == "/healthz" and body != b"OK\n":
                return False, "/healthz: unexpected response"
        except (OSError, TimeoutError, http.client.HTTPException):
            return False, f"{path}: dashboard socket not accepting requests"
        finally:
            connection.close()
    return True, "healthy: dashboard and live endpoints are available"


def wait_ready(port: int = 18080, timeout: float = 30.0, *,
               check=probe, clock=time.monotonic, sleep=time.sleep) -> tuple[bool, str]:
    deadline = clock() + timeout
    latest = "Dashboard did not start"
    while True:
        ok, latest = check(port)
        if ok:
            return True, latest
        remaining = deadline - clock()
        if remaining <= 0:
            return False, latest
        sleep(min(0.4, remaining))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--wait", type=float, default=30)
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.wait <= 60:
        parser.error("Port must be 1..65535 and wait must be 1..60 seconds")
    if args.check:
        print("PASS bounded Gentoo readiness probe checks dashboard and live endpoints")
        return 0
    success, result = wait_ready(args.port, args.wait)
    print(("PASS " if success else "FAIL ") + result, flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
