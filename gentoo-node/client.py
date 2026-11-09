#!/usr/bin/env python3
"""Persistent Gentoo worker: heartbeat, claim Ubuntu CMS jobs, execute locally, report results."""
from __future__ import annotations
import argparse
import subprocess
import shutil
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import urllib.parse
import urllib.request

VERSION = "0.4.0"
NODE_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    node_id = config.get("node_id")
    address = config.get("coordinator_url")
    key_file = config.get("key_file")
    period = config.get("period_seconds", 60)
    work_url = config.get("work_url", "")
    if not work_url and isinstance(address,str):
        work_url = address.replace("/node-heartbeat.php", "/node-work.php")
    if not isinstance(node_id, str) or not NODE_RE.fullmatch(node_id):
        raise ValueError("Invalid node_id")
    if not isinstance(address, str):
        raise ValueError("Missing coordinator_url")
    target = urllib.parse.urlsplit(address)
    if (target.scheme != "https" or not target.hostname or target.username or
            target.password or target.query or target.fragment or
            target.path != "/node-heartbeat.php"):
        raise ValueError("Coordinator requires verified HTTPS and /node-heartbeat.php")
    if not isinstance(key_file, str) or not os.path.isabs(key_file):
        raise ValueError("key_file must be absolute")
    if type(period) is not int or not 15 <= period <= 600:
        raise ValueError("period_seconds must be 15..600")
    if not isinstance(work_url,str):
        raise ValueError("Missing work_url")
    work_target = urllib.parse.urlsplit(work_url)
    if (work_target.scheme!="https" or not work_target.hostname or
        work_target.username or work_target.password or work_target.query or
        work_target.fragment or work_target.path!="/node-work.php"):
        raise ValueError("Delegation requires HTTPS and /node-work.php")
    return {"node_id": node_id, "coordinator_url": address,
            "work_url": work_url, "key_file": key_file, "period_seconds": period}


def load_key(path: str) -> bytes:
    meta = os.lstat(path)
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise PermissionError("Key must be a private regular file, mode 0400 or 0600")
    key = Path(path).read_bytes()
    if len(key) != 32:
        raise ValueError("Heartbeat HMAC key must be 32 bytes")
    return key


def memory_bytes() -> int:
    with open("/proc/meminfo", encoding="ascii") as stream:
        for line in stream:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("Cannot read MemTotal")


def gpu_vendors(directory: Path = Path("/sys/class/drm")) -> list[str]:
    found = set()
    for entry in directory.glob("card[0-9]*/device/vendor"):
        try:
            value = entry.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeError):
            continue
        if re.fullmatch(r"0x[0-9a-f]{4}", value):
            found.add(value)
    return sorted(found)[:16]


def init_system() -> str:
    if Path("/run/openrc").exists():
        return "openrc"
    if Path("/run/systemd/system").exists():
        return "systemd"
    return "other"


def distro() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("ID="):
                value = line[3:].strip('"').lower()
                return value if value in ("gentoo", "ubuntu") else "linux"
    except OSError:
        pass
    return "linux"



def meminfo() -> dict[str, int]:
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, val, *_ = line.split()
                values[key.rstrip(":")] = int(val) * 1024
    except (OSError, ValueError):
        return {}
    return values


def cpu_usage_percent() -> float | None:
    def sample():
        line = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0]
        if not line.startswith("cpu "):
            return None
        values = [int(v) for v in line.split()[1:9]]
        return (sum(values), values[3] + values[4])
    try:
        first = sample()
        time.sleep(0.15)
        second = sample()
        if first is None or second is None:
            return None
        total = second[0] - first[0]
        idle = second[1] - first[1]
        if total <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1)
    except (OSError, ValueError, IndexError):
        return None


def cpu_temperature() -> float | None:
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            driver = (hwmon / "name").read_text(encoding="ascii").strip()
            if driver not in ("k10temp", "coretemp", "zenpower"):
                continue
            for candidate in ("temp1_input", "temp2_input"):
                value = hwmon / candidate
                if value.exists():
                    temp = int(value.read_text().strip()) / 1000
                    if -20 <= temp <= 140:
                        return round(temp, 1)
        except (OSError, ValueError):
            continue
    return None


def gpu_devices() -> list[dict]:
    devices = []
    for vendor_file in sorted(Path("/sys/class/drm").glob("card[0-9]*/device/vendor")):
        try:
            vendor = vendor_file.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeError):
            continue
        if vendor not in ("0x1002", "0x10de", "0x8086"):
            continue
        device = vendor_file.parent
        gpu = {"vendor": vendor, "name": {"0x1002":"AMD GPU","0x10de":"NVIDIA GPU",
                                       "0x8086":"Intel GPU"}[vendor],
               "usage_pct": None, "temp_c": None, "vram_used_bytes": None,
               "vram_total_bytes": None}
        try:
            busy = device / "gpu_busy_percent"
            if busy.exists():
                value = float(busy.read_text().strip())
                if 0 <= value <= 100: gpu["usage_pct"] = round(value, 1)
            for hwmon in (device / "hwmon").glob("hwmon*"):
                file = hwmon / "temp1_input"
                if file.exists():
                    temp = int(file.read_text().strip()) / 1000
                    if -20 <= temp <= 140:gpu["temp_c"] = round(temp, 1)
                    break
            for name, field in (("mem_info_vram_used","vram_used_bytes"),
                                ("mem_info_vram_total","vram_total_bytes")):
                file = device / name
                if file.exists():gpu[field] = max(0,int(file.read_text().strip()))
        except (OSError, ValueError):
            pass
        devices.append(gpu)
    if shutil.which("nvidia-smi"):
        try:
            result=subprocess.run([
                "nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"
            ],capture_output=True,text=True,timeout=3,check=True)
            nvidia = [item for item in devices if item["vendor"]=="0x10de"]
            for position,line in enumerate(result.stdout.splitlines()[:8]):
                fields=[value.strip() for value in line.split(",")]
                if len(fields)!=5:continue
                if position<len(nvidia):
                    gpu=nvidia[position]
                else:
                    gpu={"vendor":"0x10de","name":"NVIDIA GPU","usage_pct":None,
                         "temp_c":None,"vram_used_bytes":None,"vram_total_bytes":None}
                    devices.append(gpu)
                gpu["name"]=fields[0][:80]
                for key,raw in (("usage_pct",fields[1]),("temp_c",fields[2])):
                    try:
                        value=float(raw)
                        if (0<=value<=100 if key=="usage_pct" else -20<=value<=140):
                            gpu[key]=round(value,1)
                    except ValueError:pass
                for key,raw in (("vram_used_bytes",fields[3]),("vram_total_bytes",fields[4])):
                    try:gpu[key]=max(0,int(float(raw)*1048576))
                    except ValueError:pass
        except (OSError,subprocess.SubprocessError):
            pass
    return devices[:16]


def performance() -> dict:
    memory=meminfo()
    total=memory.get("MemTotal",0)
    available=memory.get("MemAvailable",total)
    try:
        filesystem=os.statvfs("/")
        disk_total=filesystem.f_blocks*filesystem.f_frsize
        disk_used=(filesystem.f_blocks-filesystem.f_bfree)*filesystem.f_frsize
    except OSError:
        disk_total,disk_used=0,0
    try:
        uptime=int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError,ValueError,IndexError):
        uptime=0
    try:
        load=round(os.getloadavg()[0],2)
    except OSError:
        load=0.0
    return {
        "cpu_pct":cpu_usage_percent(),
        "cpu_temp_c":cpu_temperature(),
        "load_1":load,
        "memory_used_bytes":max(0,min(total,total-available)),
        "disk_used_bytes":max(0,disk_used),
        "disk_total_bytes":max(0,disk_total),
        "uptime_seconds":max(0,uptime),
        "gpus":gpu_devices()
    }

def telemetry(node_id: str, when: int | None = None) -> dict:
    return {"node_id": node_id, "sent_at": int(time.time()) if when is None else when,
            "nonce": secrets.token_hex(16), "os": distro(), "init": init_system(),
            "agent_version": VERSION, "cpu_threads": max(1, os.cpu_count() or 1),
            "ram_bytes": memory_bytes(), "gpu_vendors": gpu_vendors(),
            "capabilities": ["compute", "index"], "metrics": performance()}


def signed_request(config: dict, key: bytes, when: int | None = None):
    data = json.dumps(telemetry(config["node_id"], when), separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key, data, hashlib.sha256).hexdigest()
    return urllib.request.Request(config["coordinator_url"], data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Sirus-Node-Signature": signature,
                 "User-Agent": "SirusNode/" + VERSION})


def send_once(config: dict, key: bytes) -> dict:
    # Never bypass certificate or hostname verification.
    with urllib.request.urlopen(signed_request(config, key), timeout=12) as response:
        if response.status != 200:
            raise RuntimeError("Coordinator returned HTTP " + str(response.status))
        result = json.loads(response.read(4096))
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Coordinator rejected heartbeat")
    return result



REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
JOB_KINDS = ("health_snapshot", "compute_selftest")
POLL_SECONDS = 4

def signed_job_request(config: dict, key: bytes, operation: str,
                       request_id: str | None = None, *,
                       lease_token: str | None = None, state: str | None = None,
                       result: dict | None = None, error: str = ""):
    if operation not in ("claim", "complete", "status"):
        raise ValueError("Unknown Gentoo worker operation")
    if operation != "claim" and (
        not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)):
        raise ValueError("Job ID must be 32 lowercase hexadecimal characters")
    message = {
        "node_id": config["node_id"], "sent_at": int(time.time()),
        "nonce": secrets.token_hex(16), "op": operation
    }
    if operation != "claim":
        message["request_id"] = request_id
    if operation == "complete":
        if not isinstance(lease_token, str) or not REQUEST_ID.fullmatch(lease_token):
            raise ValueError("Missing secure job lease")
        if state not in ("done", "failed") or not isinstance(result, dict):
            raise ValueError("Invalid work result state")
        if len(error.encode("utf8")) > 200 or (state == "done" and error):
            raise ValueError("Invalid work error")
        message.update({"lease_token": lease_token, "state": state,
                        "result": result, "error": error})
    encoded = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf8")
    if len(encoded) > 12000:
        raise ValueError("Result message exceeds broker request limit")
    signature = hmac.new(key, encoded, hashlib.sha256).hexdigest()
    return urllib.request.Request(
        config["work_url"], data=encoded, method="POST",
        headers={"Content-Type": "application/json", "X-Sirus-Node-Signature": signature,
                 "User-Agent": "SirusGentooWorker/" + VERSION})


def work_request(config: dict, key: bytes, operation: str,
                 request_id: str | None = None, **kwargs) -> dict:
    request = signed_job_request(config, key, operation, request_id, **kwargs)
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("Coordinator returned HTTP " + str(response.status))
        raw = response.read(16385)
        if len(raw) > 16384:
            raise RuntimeError("Oversized coordinator response")
    reply = json.loads(raw)
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise RuntimeError("Coordinator rejected signed worker request")
    if operation != "claim" and reply.get("request_id") != request_id:
        raise RuntimeError("Coordinator returned incorrect task ID")
    if operation == "claim" and reply.get("state") not in ("idle", "running"):
        raise RuntimeError("Unexpected worker queue response")
    return reply


def execute_job(kind: str) -> dict:
    """Explicit fixed, bounded Gentoo work types. No arbitrary shell/code inputs."""
    if kind == "health_snapshot":
        return {
            "node": "gentoo", "cpu_threads": max(1, os.cpu_count() or 1),
            "ram_bytes": memory_bytes(), "metrics": performance()
        }
    if kind == "compute_selftest":
        start = time.monotonic()
        digest = b"SirusSofts-Gentoo-Compute-Selftest-v1"
        for _ in range(50000):
            digest = hashlib.sha256(digest).digest()
        elapsed = round(max(0, (time.monotonic() - start) * 1000), 1)
        return {"iterations": 50000, "sha256": digest.hex(),
                "elapsed_ms": elapsed, "executor": "gentoo"}
    raise ValueError("Unsupported Gentoo job type")


def poll_once(config: dict, key: bytes) -> dict:
    """Claim at most one job, execute on Gentoo, return result to Ubuntu CMS."""
    task = work_request(config, key, "claim")
    if task["state"] == "idle":
        return task
    request_id = task.get("request_id")
    lease_token = task.get("lease_token")
    kind = task.get("kind")
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise RuntimeError("Coordinator returned invalid job ID")
    if not isinstance(lease_token, str) or not REQUEST_ID.fullmatch(lease_token):
        raise RuntimeError("Coordinator returned invalid lease")
    if kind not in JOB_KINDS:
        # Fail safely; do not attempt any code or unrecognized task.
        result, state, error = {}, "failed", "Unsupported Gentoo task"
    else:
        try:
            result, state, error = execute_job(kind), "done", ""
        except Exception as problem:
            print("Gentoo worker failed job " + request_id + ": " +
                  type(problem).__name__, file=sys.stderr, flush=True)
            result, state, error = {}, "failed", "Gentoo task failed"
    # A lost completion response should NOT cause the task to be re-executed
    # within this process. Retry only the same result/lease using fresh nonces.
    for attempt in range(3):
        try:
            return work_request(config, key, "complete", request_id,
                                lease_token=lease_token, state=state,
                                result=result, error=error)
        except (OSError, TimeoutError, RuntimeError) as problem:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("Unable to return Gentoo work result")


def run_service(config: dict, key: bytes, *, once: bool = False) -> int:
    """Persistent outbound heartbeat + 4-second queue polling with reconnect."""
    backoff = 4
    next_heartbeat = 0.0
    while True:
        now = time.monotonic()
        try:
            if now >= next_heartbeat:
                heartbeat = send_once(config, key)
                print("Gentoo heartbeat accepted:", heartbeat.get("node_id"), flush=True)
                next_heartbeat = time.monotonic() + config["period_seconds"]
            task = poll_once(config, key)
            if task["state"] != "idle":
                print("Gentoo job:", task.get("request_id"),
                      task.get("state"), flush=True)
            backoff = 4
        except (OSError, TimeoutError, RuntimeError, ValueError) as problem:
            print("Gentoo connection/job error: " + type(problem).__name__,
                  file=sys.stderr, flush=True)
            if once:
                return 1
            # Keep trying when the network drops; do not lose the HMAC key.
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        if once:
            return 0
        time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/sirus-gentoo-node/config.json")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--once", action="store_true",
                          help="Send heartbeat and check one delegated Gentoo job")
    commands.add_argument("--probe", action="store_true",
                          help="Display Gentoo hardware without network")
    commands.add_argument("--status", metavar="REQUEST_ID",
                          help="Check assigned Gentoo job ID")
    args = parser.parse_args(argv)
    if args.probe:
        print(json.dumps(telemetry("probe-node"), indent=2))
        return 0
    config = validate_config(json.loads(Path(args.config).read_text(encoding="utf8")))
    key = load_key(config["key_file"])
    if args.status:
        print(json.dumps(work_request(config,key,"status",args.status),indent=2))
        return 0
    return run_service(config,key,once=args.once)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError,RuntimeError,ValueError,TimeoutError) as problem:
        print("FAIL Gentoo node: " + str(problem),file=sys.stderr)
        raise SystemExit(1)
