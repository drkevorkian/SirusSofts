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


def audit_public_panel(payload: str) -> list[str]:
    """Find Hestia ACCEPT rules that expose TCP/8083 publicly."""
    try:
        records = json.loads(payload)
    except (TypeError, ValueError):
        raise ValueError("Cannot parse Hestia firewall state") from None
    if isinstance(records, list):
        pairs = [(str(i + 1), x) for i, x in enumerate(records)]
    elif isinstance(records, dict):
        pairs = records.items()
    else:
        raise ValueError("Unsupported firewall inventory format")
    exposed = []
    inspected = 0
    for key, record in pairs:
        if not isinstance(record, dict):
            continue
        fields = {str(k).upper(): str(v).upper() for k, v in record.items()}
        if not {"ACTION", "PORT", "IP"}.issubset(fields):
            continue
        inspected += 1
        if (fields["ACTION"] != "ACCEPT" or
                fields["IP"] not in {"0.0.0.0/0", "0.0.0.0", "::/0", "::"} or
                fields.get("PROTOCOL", "TCP") not in {"TCP", "TCP/UDP"}):
            continue
        ports = fields["PORT"].replace(" ", "").split(",")
        for port in ports:
            if port in {"0", "*", "8083"}:
                exposed.append(str(key))
                break
            separator = ":" if ":" in port else "-" if "-" in port else None
            if separator:
                low, _, high = port.partition(separator)
                if low.isdecimal() and high.isdecimal() and int(low) <= 8083 <= int(high):
                    exposed.append(str(key))
                    break
    if inspected == 0:
        raise ValueError("Hestia returned no recognizable firewall entries")
    return exposed


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--audit", action="store_true")
    args.add_argument("--rule")
    args.add_argument("--cidr")
    parsed = args.parse_args()
    try:
        if parsed.audit:
            exposed = audit_public_panel(sys.stdin.read())
            if exposed:
                raise ValueError("Public TCP/8083 ACCEPT rule(s): " + ", ".join(exposed))
            print("PASS: no public TCP/8083 ACCEPT rule detected")
            return
        if not parsed.rule or not parsed.cidr:
            raise ValueError("--rule and --cidr are required")
        cidr = verify_rule(sys.stdin.read(), parsed.rule, parsed.cidr)
    except ValueError as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: public Hestia 8083 rule confirmed, requested destination CIDR {cidr}")


if __name__ == "__main__":
    main()
