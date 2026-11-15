#!/usr/bin/env python3
"""Non-disruptive llama.cpp CPU/CUDA/Vulkan benchmark. Python standard library only.

Runs fixed requests against *already listening* loopback services and records
real response timing rather than claiming a theoretical GPU speed.
"""
import argparse
import datetime as dt
import json
import pathlib
import platform
import statistics
import sys
import time
import urllib.error
import urllib.request

WORKERS = {
    "cpu": "http://127.0.0.1:18482",
    "nvidia-cuda": "http://127.0.0.1:18486",
    "amd-vulkan": "http://127.0.0.1:18487",
    "nvidia-vulkan": "http://127.0.0.1:18488",
}
PROMPTS = {
    "coding": [
        {"role": "system", "content": "You are a programming assistant. Produce practical, valid Python code and concise explanations."},
        {"role": "user", "content": (
            "Implement a Python 3 function parse_duration(text: str) -> int which converts "
            "strings such as '1h 2m 3s' and '90s' into total seconds. Reject negative values, "
            "unknown units, and empty input with ValueError. Include at least five unittest "
            "test methods, tricky edge cases, and a short explanation of your parsing strategy."
        )},
    ],
    "reasoning": [
        {"role": "system", "content": "Answer clearly and show the steps that can be verified."},
        {"role": "user", "content": (
            "A scheduler has 8 independent inference workers. Three process 15 tasks/minute each; "
            "five process 7 tasks/minute each. Explain the combined ideal throughput, why real "
            "throughput can be lower, and show pseudocode for routing whole requests to healthy "
            "workers by their observed queue time rather than splitting tokens over a WAN."
        )},
    ],
}


def get_json(url, payload=None, timeout=10):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, json.load(response)


def healthy(base):
    try:
        code, body = get_json(base + "/health", timeout=3)
        return code == 200 and body.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def one_run(base, messages, max_tokens, timeout):
    payload = {
        "model": "sirussofts-coder",
        "messages": messages,
        "temperature": 0,
        "seed": 12345,
        "max_tokens": max_tokens,
        "stream": False,
    }
    start = time.perf_counter()
    code, response = get_json(base + "/v1/chat/completions", payload, timeout)
    wall = time.perf_counter() - start
    if code != 200 or not response.get("choices"):
        raise ValueError("No completion was returned")
    usage = response.get("usage") or {}
    timings = response.get("timings") or {}
    content = str((response["choices"][0].get("message") or {}).get("content") or "")
    if not content.strip():
        raise ValueError("Completion was empty")
    return {
        "wall_seconds": round(wall, 4),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens_per_second": timings.get("prompt_per_second"),
        "generation_tokens_per_second": timings.get("predicted_per_second"),
        "finish_reason": response["choices"][0].get("finish_reason"),
        "content_chars": len(content),
    }


def median_numeric(items, name):
    vals = [item[name] for item in items if isinstance(item.get(name), (int, float))]
    return round(statistics.median(vals), 3) if vals else None


def print_report(report):
    print("\nSirusSofts AI benchmark (median measured runs; warmups excluded)")
    print(f"Suite: {report['suite']} | max tokens: {report['max_tokens']} | runs: {report['repeats']}")
    print(f"{'Worker':<19} {'State':<9} {'Gen tok/s':>11} {'Prompt tok/s':>13} {'Wall sec':>10}")
    print("-" * 70)
    for item in report["results"]:
        summary = item.get("summary") or {}
        def fmt(value):
            return f"{value:.2f}" if isinstance(value, (float, int)) else "--"
        print(
            f"{item['worker']:<19} {item['state']:<9}"
            f" {fmt(summary.get('generation_tokens_per_second')):>11}"
            f" {fmt(summary.get('prompt_tokens_per_second')):>13}"
            f" {fmt(summary.get('wall_seconds')):>10}"
        )
        if item.get("error"):
            print("  " + item["error"])


def benchmark(args):
    if "nvidia-cuda" in args.workers and "nvidia-vulkan" in args.workers:
        raise ValueError("Benchmark NVIDIA CUDA and NVIDIA Vulkan in separate runs; they share the GTX 1660 VRAM.")
    if healthy(WORKERS["nvidia-cuda"]) and healthy(WORKERS["nvidia-vulkan"]):
        raise ValueError("Both NVIDIA backends are active. Stop one before measuring either backend.")
    report = {
        "schema": "sirus-benchmark-v1",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": platform.node(),
        "kernel": platform.release(),
        "suite": args.suite,
        "max_tokens": args.max_tokens,
        "repeats": args.repeats,
        "warmups": args.warmups,
        "results": [],
    }
    for worker in args.workers:
        base = WORKERS[worker]
        entry = {"worker": worker, "url": base, "state": "offline", "samples": []}
        report["results"].append(entry)
        if not healthy(base):
            entry["error"] = "Health endpoint unavailable (service not running or still loading)"
            continue
        entry["state"] = "error"
        try:
            for i in range(args.warmups):
                print(f"{worker}: warming up {i + 1}/{args.warmups}", flush=True)
                one_run(base, PROMPTS[args.suite], args.max_tokens, args.timeout)
            for i in range(args.repeats):
                print(f"{worker}: measuring {i + 1}/{args.repeats}", flush=True)
                entry["samples"].append(one_run(base, PROMPTS[args.suite], args.max_tokens, args.timeout))
            entry["summary"] = {
                key: median_numeric(entry["samples"], key) for key in (
                    "wall_seconds", "prompt_tokens", "completion_tokens",
                    "prompt_tokens_per_second", "generation_tokens_per_second"
                )
            }
            entry["state"] = "online"
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            entry["error"] = f"{type(error).__name__}: {error}"
    out = pathlib.Path(args.output).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"ai-benchmark-{args.suite}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print_report(report)
    print(f"\nSaved results: {path}")
    if not any(x["state"] == "online" for x in report["results"]):
        return 1
    return 0


def compare(files):
    entries = []
    suite = None
    for file in files:
        report = json.loads(pathlib.Path(file).expanduser().read_text(encoding="utf-8"))
        if report.get("schema") != "sirus-benchmark-v1":
            raise ValueError(f"{file} is not a SirusSofts benchmark JSON report")
        params = (report.get("suite"), report.get("max_tokens"))
        if suite is None:
            suite = params
        elif suite != params:
            print(f"WARNING: workload mismatch in {file}: {params} vs {suite}", file=sys.stderr)
        entries.extend(report.get("results", []))
    print_report({"suite": suite[0], "max_tokens": suite[1], "repeats": "varies", "results": entries})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", nargs="+", choices=WORKERS, default=["cpu", "amd-vulkan"])
    parser.add_argument("--suite", choices=PROMPTS, default="coding")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=192)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--output", default="~/sirus-ai-benchmarks")
    parser.add_argument("--compare", nargs="+", metavar="REPORT.json",
                        help="Compare saved JSON runs; don't invoke inference")
    args = parser.parse_args()
    if args.compare:
        compare(args.compare)
        return 0
    if args.repeats < 1 or args.warmups < 0 or not 16 <= args.max_tokens <= 1024 or args.timeout < 10:
        parser.error("Use repeats >=1, warmups >=0, 16 <= max-tokens <=1024, and timeout >=10")
    return benchmark(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(2)
