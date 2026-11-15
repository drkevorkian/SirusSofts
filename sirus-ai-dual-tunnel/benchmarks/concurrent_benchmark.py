#!/usr/bin/env python3
"""Benchmark simultaneous requests to distinct, already-running local llama.cpp workers.

Standard-library only. Read-only for services and firewall; stores timings in
a local JSON file. Never combines NVIDIA CUDA and Vulkan on the same GTX 1660.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
import statistics
import sys
import threading
import time

import benchmark as baseline


def median_numeric(values):
    return round(statistics.median(values), 3) if values else None


def measure_round(workers, messages, max_tokens, timeout):
    """Send one request to each worker at nearly the same time."""
    gate = threading.Barrier(len(workers))
    def measure(worker):
        gate.wait(timeout=15)
        start = time.perf_counter()
        try:
            sample = baseline.one_run(baseline.WORKERS[worker], messages, max_tokens, timeout)
            return {"worker": worker, "ok": True, "start": start,
                    "end": time.perf_counter(), "metrics": sample}
        except Exception as exc:
            return {"worker": worker, "ok": False, "start": start,
                    "end": time.perf_counter(), "error": f"{type(exc).__name__}: {exc}"}
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        futures = [pool.submit(measure, worker) for worker in workers]
        samples = [future.result() for future in futures]
    first = min(sample["start"] for sample in samples)
    last = max(sample["end"] for sample in samples)
    span = last - first
    tokens = sum(int(sample["metrics"].get("completion_tokens") or 0)
                 for sample in samples if sample["ok"])
    return {
        "wall_seconds": round(span, 4),
        "generation_tokens": tokens,
        "aggregate_generation_tokens_per_wall_second": round(tokens / span, 3) if span > 0 else None,
        "all_succeeded": all(sample["ok"] for sample in samples),
        "workers": [
            {key: value for key, value in sample.items() if key not in ("start", "end")}
            for sample in samples
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", nargs="+", choices=baseline.WORKERS,
                        default=["cpu", "amd-vulkan", "nvidia-cuda"])
    parser.add_argument("--suite", choices=baseline.PROMPTS, default="coding")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=192)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--output", default="~/sirus-ai-benchmarks")
    args = parser.parse_args()
    if len(set(args.workers)) != len(args.workers):
        parser.error("Specify each worker once")
    if "nvidia-cuda" in args.workers and "nvidia-vulkan" in args.workers:
        parser.error("NVIDIA CUDA and NVIDIA Vulkan share the same GTX 1660: test separately")
    if not 1 <= args.repeats <= 20 or not 0 <= args.warmups <= 3:
        parser.error("Use 1-20 measured rounds and 0-3 warmups")
    if not 16 <= args.max_tokens <= 1024 or args.timeout < 10:
        parser.error("Use 16-1024 max tokens and timeout >=10 sec")
    unavailable = [worker for worker in args.workers
                   if not baseline.healthy(baseline.WORKERS[worker])]
    if unavailable:
        parser.error("Not ready: " + ", ".join(unavailable))
    if baseline.healthy(baseline.WORKERS["nvidia-cuda"]) and baseline.healthy(
            baseline.WORKERS["nvidia-vulkan"]):
        parser.error("Both NVIDIA backends online: stop one before continuing")

    report = {
        "schema": "sirus-concurrent-benchmark-v1",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workers": args.workers,
        "suite": args.suite,
        "max_tokens": args.max_tokens,
        "warmups": args.warmups,
        "repeats": args.repeats,
        "rounds": [],
    }
    print("SirusSofts AI simultaneous worker benchmark")
    print("Workers: " + ", ".join(args.workers), flush=True)
    for index in range(args.warmups + args.repeats):
        warming = index < args.warmups
        label = f"Warmup {index + 1}/{args.warmups}" if warming else (
            f"Measured round {index - args.warmups + 1}/{args.repeats}")
        print(label + " ...", flush=True)
        record = measure_round(args.workers, baseline.PROMPTS[args.suite],
                               args.max_tokens, args.timeout)
        if not warming:
            report["rounds"].append(record)
        print(f"  {record['generation_tokens']} generated tokens, "
              f"{record['wall_seconds']:.2f}s wall, "
              f"{record['aggregate_generation_tokens_per_wall_second']:.2f} tok/s aggregate, "
              f"{'PASS' if record['all_succeeded'] else 'FAIL'}", flush=True)
        for sample in record["workers"]:
            if not sample["ok"]:
                print(f"  {sample['worker']}: {sample['error']}", file=sys.stderr)
        if not record["all_succeeded"]:
            break

    measured = report["rounds"]
    successful = [record for record in measured if record["all_succeeded"]]
    report["summary"] = {
        "median_wall_seconds": median_numeric([r["wall_seconds"] for r in successful]),
        "median_aggregate_generation_tokens_per_second": median_numeric(
            [r["aggregate_generation_tokens_per_wall_second"] for r in successful]),
        "successful_rounds": len(successful),
        "measured_rounds": len(measured),
    }
    output = Path(args.output).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = output / f"ai-concurrent-{args.suite}-{stamp}.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nMedian concurrent generation throughput:",
          report["summary"]["median_aggregate_generation_tokens_per_second"], "tok/s")
    print("Note: aggregate tokens divided by concurrent wall time includes prefill and waiting.")
    print("Saved:", target)
    return 0 if len(successful) == args.repeats else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(2)
