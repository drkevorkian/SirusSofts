#!/usr/bin/env python3
"""SirusSofts macOS agent. No root requirement, remote shell or arbitrary commands."""
from __future__ import annotations
import argparse
import hashlib
import hmac
import json
import os
import platform
import plistlib
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

VERSION = "0.1.0"
KIND = {"health_snapshot", "compute_selftest"}
HEX_ID = re.compile(r"^[a-f0-9]{32}$")
STOP = False


def run(argv, timeout=5):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def sysctl(key):
    return run(["/usr/sbin/sysctl", "-n", key])


def hardware():
    arch = platform.machine()
    model = sysctl("hw.model")[:96] or "Unknown Mac"
    ram = int(sysctl("hw.memsize") or 0)
    cpus = int(sysctl("hw.logicalcpu") or 1)
    if ram < 1048576 or not 1 <= cpus <= 1024:
        raise RuntimeError("Unable to identify Mac hardware")
    return {"arch": arch, "model": model, "ram": ram, "cpus": cpus}


def gpu_inventory():
    raw = run(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-xml"], timeout=12)
    if not raw:
        return []
    try:
        documents = plistlib.loads(raw.encode())
    except (ValueError, plistlib.InvalidFileException):
        return []
    devices = []
    for record in documents[:8]:
        for item in record.get("_items", [])[:16]:
            name = str(item.get("sppci_model", "Unknown GPU"))[:80]
            vendor = str(item.get("spdisplays_vendor", "")).lower()
            code = "0x106b" if "apple" in vendor or "apple" in name.lower() else (
                "0x8086" if "intel" in vendor or "intel" in name.lower() else (
                "0x1002" if "amd" in vendor or "ati" in vendor else (
                "0x10de" if "nvidia" in vendor else "0x106b")))
            devices.append({"vendor": code, "name": name, "usage_pct": None,
                            "temp_c": None, "vram_used_bytes": None,
                            "vram_total_bytes": None})
    return devices[:16]


def power_info():
    raw = run(["/usr/bin/pmset", "-g", "batt"])
    ac = "AC Power" in raw
    match = re.search(r"(\d{1,3})%", raw)
    percentage = int(match.group(1)) if match else None
    return ("AC" if ac else "Battery" if "Battery Power" in raw else "Unknown",
            percentage if percentage is None or percentage <= 100 else None)


def memory_info():
    stats = run(["/usr/bin/memory_pressure", "-Q"])
    match = re.search(r"System-wide free memory percentage:\s*(\d+)", stats)
    free = int(match.group(1)) if match else None
    pressure = "unknown" if free is None else (
        "high" if free < 10 else "moderate" if free < 25 else "normal")
    vm = sysctl("vm.page_free_count")
    page_size = sysctl("hw.pagesize")
    if not vm or not page_size:
        return None, pressure
    return max(0, int(vm) * int(page_size)), pressure


def uptime():
    data = run(["/usr/sbin/sysctl", "-n", "kern.boottime"])
    match = re.search(r"sec\s*=\s*(\d+)", data)
    return max(0, int(time.time()) - int(match.group(1))) if match else 0


def cpu_percent():
    # 'top' reports whole-system CPU percentages; absent samples stay null.
    raw = run(["/usr/bin/top", "-l", "1", "-n", "0", "-stats", "cpu"], timeout=8)
    match = re.search(r"CPU usage:.*?(\d+(?:\.\d+)?)% user.*?(\d+(?:\.\d+)?)% sys", raw)
    if not match:
        return None
    return round(min(100.0, float(match.group(1)) + float(match.group(2))), 1)


def snapshot(config, sleep_active=False):
    hw = hardware()
    usage = shutil.disk_usage("/")
    available, pressure = memory_info()
    source, battery = power_info()
    gpus = gpu_inventory()
    mac = {"model": hw["model"], "arch": hw["arch"],
           "os_version": platform.mac_ver()[0][:96] or "Unknown",
           "power_source": source, "memory_pressure": pressure,
           "sleep_assertion": bool(sleep_active),
           "worker_cpu_target": float(config.get("cpu_target_percent", 90))}
    if battery is not None:
        mac["battery_pct"] = battery
    metrics = {"cpu_pct": cpu_percent(), "cpu_temp_c": None,
               "load_1": round(os.getloadavg()[0], 2),
               "memory_used_bytes": max(0, hw["ram"] - available) if available is not None else 0,
               "disk_used_bytes": usage.used, "disk_total_bytes": usage.total,
               "uptime_seconds": uptime(), "gpus": gpus,
               "services": ["sirus-mac-node"], "mac": mac}
    return {"node_id": config["node_id"], "sent_at": int(time.time()),
            "nonce": secrets.token_hex(16), "os": "macos", "init": "launchd",
            "agent_version": VERSION, "cpu_threads": hw["cpus"],
            "ram_bytes": hw["ram"], "gpu_vendors": list(dict.fromkeys(g["vendor"] for g in gpus)),
            "capabilities": ["compute", "index"], "metrics": metrics}


def post(url, config, key, payload):
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > 12000:
        raise ValueError("Signed request too large")
    request = urllib.request.Request(url, data=data, method="POST",
                                    headers={"Content-Type": "application/json",
                                             "X-Sirus-Node-Signature": hmac.new(key, data, hashlib.sha256).hexdigest(),
                                             "User-Agent": "SirusMacNode/" + VERSION})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("Unexpected coordinator status")
        raw = response.read(16385)
    if len(raw) > 16384:
        raise RuntimeError("Oversized coordinator response")
    reply = json.loads(raw)
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise RuntimeError("Coordinator rejected request")
    return reply


def envelope(config, op, **kwargs):
    return {"node_id": config["node_id"], "sent_at": int(time.time()),
            "nonce": secrets.token_hex(16), "op": op, **kwargs}


def execute(kind, config, sleep_active):
    if kind == "health_snapshot":
        data = snapshot(config, sleep_active)
        return {"node": "macos", "cpu_threads": data["cpu_threads"],
                "ram_bytes": data["ram_bytes"], "metrics": data["metrics"]}
    if kind == "compute_selftest":
        start = time.monotonic()
        digest = b"SirusSofts-Mac-Compute-Selftest-v1"
        for _ in range(50000):
            digest = hashlib.sha256(digest).digest()
        return {"iterations": 50000, "sha256": digest.hex(),
                "elapsed_ms": round((time.monotonic() - start)*1000, 1), "executor": "macos"}
    raise ValueError("Unsupported task")


def poll(config, key, sleep_active):
    reply = post(config["work_url"], config, key, envelope(config, "claim"))
    if reply.get("state") == "idle":
        return
    request_id, lease, kind = reply.get("request_id"), reply.get("lease_token"), reply.get("kind")
    if reply.get("state") != "running" or not isinstance(request_id, str) or not HEX_ID.fullmatch(request_id) or not isinstance(lease, str) or not HEX_ID.fullmatch(lease):
        raise RuntimeError("Invalid job lease")
    if kind not in KIND:
        result, state, error = {}, "failed", "Unsupported Mac task"
    else:
        try:
            result, state, error = execute(kind, config, sleep_active), "done", ""
        except Exception:
            result, state, error = {}, "failed", "Mac task failed"
    for attempt in range(3):
        try:
            post(config["work_url"], config, key,
                 envelope(config, "complete", request_id=request_id, lease_token=lease,
                          state=state, result=result, error=error))
            return
        except (OSError, RuntimeError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2*(attempt+1))


def keep_awake():
    """Idle-sleep assertion only. Lid-close sleep is NOT bypassed."""
    binary = "/usr/bin/caffeinate"
    if not os.path.isfile(binary):
        return None
    return subprocess.Popen([binary, "-i"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_signal(_signum, _frame):
    global STOP
    STOP = True


def load_config(path):
    config = json.loads(Path(path).read_text())
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", config["node_id"]):
        raise ValueError("Invalid node ID")
    for k in ("coordinator_url", "work_url"):
        if not isinstance(config[k], str) or not config[k].startswith("https://") or any(c in config[k] for c in "\r\n"):
            raise ValueError("TLS coordinator URL required")
    target = float(config.get("cpu_target_percent", 90))
    if not 10 <= target <= 90:
        raise ValueError("CPU target must be 10..90")
    config["cpu_target_percent"] = target
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path.home() / ".config/sirus-mac-node/config.json"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("This agent is for macOS only")
    config = load_config(args.config)
    if args.probe:
        print(json.dumps(snapshot(config), indent=2))
        return 0
    key = Path(config["key_file"]).read_bytes()
    if len(key) != 32:
        raise ValueError("HMAC key must be exactly 32 bytes")
    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    assertion = keep_awake() if config.get("prevent_idle_sleep", True) else None
    try:
        next_heartbeat = 0
        retry = 4
        while not STOP:
            try:
                if time.monotonic() >= next_heartbeat:
                    post(config["coordinator_url"], config, key,
                         snapshot(config, assertion is not None and assertion.poll() is None))
                    next_heartbeat = time.monotonic() + 60
                poll(config, key, assertion is not None and assertion.poll() is None)
                retry = 4
            except (OSError, ValueError, TimeoutError, RuntimeError) as exc:
                print("Mac worker: " + type(exc).__name__, file=sys.stderr, flush=True)
                if args.once:
                    return 1
                time.sleep(retry)
                retry = min(retry*2, 60)
            if args.once:
                return 0
            time.sleep(4)
    finally:
        if assertion is not None and assertion.poll() is None:
            assertion.terminate()
            try:
                assertion.wait(timeout=3)
            except subprocess.TimeoutExpired:
                assertion.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
