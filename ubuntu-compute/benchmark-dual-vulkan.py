#!/usr/bin/env python3
"""Concurrent, thermally guarded llama.cpp Vulkan benchmark; no BIOS changes."""
import argparse
import csv
import datetime
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""

def sensors():
    rows = []
    for dev in sorted(Path("/sys/bus/pci/devices").glob("*")):
        if read(dev / "vendor") != "0x1002" or not read(dev / "class").startswith("0x03"):
            continue
        for hw in sorted((dev / "hwmon").glob("hwmon*")):
            for p in sorted(hw.glob("temp[0-9]*_input")):
                raw = read(p)
                if not raw:
                    continue
                label = read(hw / p.name.replace("_input", "_label")) or p.name
                rows.append((dev.name, label, float(raw) / 1000, read(hw / "power1_average") or read(hw / "power1_input"),
                             read(hw / "freq1_input"), read(hw / "freq2_input"),
                             read(dev / "gpu_busy_percent"), read(dev / "mem_info_vram_used")))
    return rows

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench", required=True, type=Path)
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--devices", nargs=2, default=["0", "1"])
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--max-temp", type=float, default=80)
    ap.add_argument("--output", type=Path, default=Path("gpu-bench-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    a = ap.parse_args()
    if not (0 < a.minutes <= 180 and 40 <= a.max_temp <= 85):
        ap.error("duration must be 0..180 minutes; thermal cutoff 40..85 C")
    if len(set(a.devices)) != 2 or any(not re.fullmatch(r"\d+", x) for x in a.devices):
        ap.error("supply two distinct Vulkan device indices")
    bench, model = a.bench.resolve(), a.model.resolve()
    if not bench.is_file() or not os.access(bench, os.X_OK) or not model.is_file():
        ap.error("missing executable llama-bench or model")
    a.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    children = {}
    handles = {}
    iterations = dict.fromkeys(a.devices, 0)
    expected = set()
    deadline = time.monotonic() + a.minutes * 60
    next_launch = dict.fromkeys(a.devices, 0.0)
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        with (a.output / "telemetry.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["utc", "pci", "sensor", "celsius", "power_uW", "core_Hz", "memory_Hz", "busy_pct", "vram_bytes"])
            while time.monotonic() < deadline:
                rows = sensors()
                seen = {r[0] for r in rows}
                if not expected:
                    expected = seen
                if len(expected) != 2 or seen != expected:
                    raise RuntimeError("Two AMD cards with readable thermal sensors are required; refusing blind stress test")
                for r in rows:
                    w.writerow([datetime.datetime.now(datetime.timezone.utc).isoformat(), *r])
                    if not 0 < r[2] < a.max_temp:
                        raise RuntimeError("Thermal cutoff or invalid sensor: " + str(r[:3]))
                f.flush()
                for dev in a.devices:
                    p = children.get(dev)
                    if p is not None and p.poll() is not None:
                        handles.pop(dev).close()
                        del children[dev]
                        if p.returncode:
                            raise RuntimeError("Vulkan device " + dev + " benchmark failed; see log")
                        log = (a.output / ("device-" + dev + "-" + str(iterations[dev]) + ".log")).read_text(errors="replace")
                        if "Found 1 Vulkan devices" not in log or "tg128" not in log or "pp512" not in log:
                            raise RuntimeError("Device isolation or expected benchmark output not verified: " + dev)
                        print("device", dev, "iteration", iterations[dev], "complete", flush=True)
                        next_launch[dev] = time.monotonic()
                    if dev not in children and time.monotonic() >= next_launch[dev]:
                        iterations[dev] += 1
                        out = (a.output / ("device-" + dev + "-" + str(iterations[dev]) + ".log")).open("w")
                        handles[dev] = out
                        env = dict(os.environ, GGML_VK_VISIBLE_DEVICES=dev)
                        children[dev] = subprocess.Popen([str(bench), "-m", str(model), "-ngl", "99", "-p", "512", "-n", "128", "-r", "1"], env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                time.sleep(1)
    finally:
        for p in children.values():
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        for p in children.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
        for f in handles.values():
            f.close()
        print("Logs:", a.output, flush=True)
    print("Duration completed. Final interrupted iterations are not complete measurements.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, RuntimeError, OSError, ValueError) as exc:
        print("STOP:", exc or "interrupted", file=sys.stderr)
        sys.exit(1)
