#!/usr/bin/env python3
"""Fail-closed verifier for an explicitly chosen Hestia panel firewall rule.

Does not write firewall state, never executes a shell command. The caller may
change only the proven public ACCEPT TCP 8083 rule.
"""
from __future__ import annotations
import argparse
import ipaddress
import json
import sys


def verify_rule(payload: str, rule_id: str, cidr: str) -> str:
    if not rule_id.isdecimal() or int(rule_id) < 1 or int(rule_id) > 99999:
        raise ValueError("Invalid Hestia rule number")
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        raise ValueError("Invalid administrator IP or CIDR") from None
    if not isinstance(net, ipaddress.IPv4Network) or net.prefixlen < 24 or net.is_multicast:
        raise ValueError("Administrator allowlist must be an IPv4 /24-/32 subnet")
    try:
        rule = json.loads(payload)
    except (ValueError, TypeError):
        raise ValueError("Hestia rule response is not valid JSON") from None
    if not isinstance(rule, dict):
        raise ValueError("Unexpected Hestia rule structure")
    if rule_id in rule and isinstance(rule[rule_id], dict):
        rule = rule[rule_id]
    fields = {str(k).upper(): str(v).upper() for k, v in rule.items()}
    if (fields.get("ACTION") != "ACCEPT" or
            fields.get("IP") not in {"0.0.0.0/0", "0.0.0.0"} or
            fields.get("PORT") != "8083" or
            fields.get("PROTOCOL") != "TCP"):
        raise ValueError("Selected rule must be exactly a public ACCEPT TCP 8083")
    return str(net)


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--rule", required=True)
    args.add_argument("--cidr", required=True)
    parsed = args.parse_args()
    try:
        cidr = verify_rule(sys.stdin.read(), parsed.rule, parsed.cidr)
    except ValueError as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: public Hestia 8083 rule confirmed, requested destination CIDR {cidr}")


if __name__ == "__main__":
    main()
