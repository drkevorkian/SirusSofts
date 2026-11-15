#!/usr/bin/env python3
"""Read-only SirusSofts AI farm node inventory; no enrollment, secrets, or uploads."""
import argparse
import datetime as dt
import json
import os
import platform
import re
import subprocess
import urllib.request


def call(*argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=7, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def mem_total_mib():
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", default=None, help="Proposed node name; not enrolled or persisted")
    parser.add_argument("--json", action="store_true", help="Show JSON-only machine inventory")
    args = parser.parse_args()
    if args.node_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.node_id):
        parser.error("node-id must be lowercase ASCII letters, numbers, and dashes")
    pci = call("lspci", "-nn")
    gpus = [
        line for line in pci.splitlines()
        if re.search(r"\b(VGA compatible controller|3D controller|Display controller)\b", line, re.I)
    ]
    record = {
        "schema": "sirus-farm-preflight-v1",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "proposed_node_id": args.node_id,
        "hostname": platform.node(),
        "platform": platform.system(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_logical": os.cpu_count(),
        "memory_mib": mem_total_mib(),
        "gpu_pci": gpus,
        "services": {
            "cpu_18482": health(18482),
            "nvidia_cuda_18486": health(18486),
            "amd_vulkan_18487": health(18487),
            "nvidia_vulkan_18488": health(18488),
        },
        "tunnels_or_remote_access_changed": False,
    }
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(f"SirusSofts node preflight: {record['proposed_node_id'] or record['hostname']}")
        print(f"Linux: {record['platform']} {record['kernel']} ({record['architecture']})")
        print(f"CPU: {record['cpu_logical']} logical threads; RAM: {record['memory_mib']} MiB")
        for gpu in gpus:
            print(f"GPU: {gpu}")
        for name, online in record["services"].items():
            print(f"{name}: {'ONLINE' if online else 'offline'}")
        print("Read-only: no keys, tunnels, firewall rules, or service files created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
