#!/usr/bin/env python3
"""Run the exact progressive prompt and automatically send NEXT for 3-25 cycles.

No artificial fixed output cap: each cycle requests the 1500*1.75**(n-1)
target up to the *live* llama.cpp context capacity reported by /props.
The context length is finite and configured on each server, not in this
benchmark. Reports clearly distinguish target, request cap, and actual usage.
Runs only against existing 127.0.0.1 inference services. No restarts/installs.
"""
import argparse
import datetime as dt
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import urllib.error

import benchmark as baseline

HERE = Path(__file__).resolve().parent
DEFAULT_PROMPT = HERE / "progressive_prompt.txt"
DEFAULT_SUBJECT = "Heterogeneous distributed AI inference: GPUs, memory, scheduling, resilience, and network architecture"


def cycle_target(cycle):
    """Exact ceiling of 1500 * 1.75 ** (cycle - 1), including 25-cycle runs."""
    if cycle < 1:
        raise ValueError("Cycle must be positive")
    ratio = Fraction(7, 4) ** (cycle - 1)
    value = 1500 * ratio
    return (value.numerator + value.denominator - 1) // value.denominator


def server_context(base, override=None):
    """Use reported per-slot context, or require an explicit user override."""
    if override is not None:
        if override < 1:
            raise ValueError("Context override must be positive")
        return override, "user-override"
    status, props = baseline.get_json(base + "/props", timeout=10)
    if status != 200 or not isinstance(props, dict):
        raise ValueError("llama.cpp GET /props did not report server context")
    settings = props.get("default_generation_settings") or {}
    ctx = settings.get("n_ctx")
    if not isinstance(ctx, int) or ctx < 1:
        raise ValueError("Server /props has no valid default_generation_settings.n_ctx; use --context-override only if verified")
    return ctx, "server-/props"


def approx_prompt_tokens(base, messages):
    """Use llama.cpp's own chat-template renderer and tokenizer when available."""
    try:
        status, applied = baseline.get_json(
            base + "/apply-template", {"messages": messages}, timeout=30)
        if status != 200 or not isinstance(applied.get("prompt"), str):
            raise ValueError("Invalid /apply-template response")
        rendered_prompt = applied["prompt"]
        template_margin = 8  # small safety allowance for server/template variance
    except (OSError, ValueError, KeyError, urllib.error.HTTPError):
        # Older llama.cpp servers may not implement /apply-template.
        # This fallback is approximate, so reserve role/template overhead.
        rendered_prompt = "\n".join(
            f"[{message['role']}]\n{message['content']}\n" for message in messages)
        template_margin = 16 * len(messages) + 16
    status, result = baseline.get_json(
        base + "/tokenize", {"content": rendered_prompt}, timeout=30)
    if status != 200 or not isinstance(result, dict) or not isinstance(result.get("tokens"), list):
        raise ValueError("Unable to tokenize prompt; cannot calculate safe context budget")
    return len(result["tokens"]), template_margin


def request_cycle(base, messages, requested_tokens, timeout):
    payload = {
        "model": "sirussofts-coder",
        "messages": messages,
        "temperature": 0,
        "seed": 12345,
        "max_tokens": requested_tokens,
        "stream": False,
    }
    started = time.perf_counter()
    status, response = baseline.get_json(base + "/v1/chat/completions", payload, timeout)
    wall = time.perf_counter() - started
    if status != 200 or not response.get("choices"):
        raise ValueError("No completion returned")
    choice = response["choices"][0]
    body = str((choice.get("message") or {}).get("content") or "")
    if not body.strip():
        raise ValueError("Completion was empty")
    usage = response.get("usage") or {}
    timings = response.get("timings") or {}
    actual = usage.get("completion_tokens")
    if not isinstance(actual, int):
        actual = timings.get("predicted_n")
    if not isinstance(actual, int):
        raise ValueError("Model response omitted token usage; cannot measure saturation")
    return body, {
        "completion_tokens": actual,
        "prompt_tokens": usage.get("prompt_tokens"),
        "wall_seconds": round(wall, 4),
        "generation_tokens_per_second": timings.get("predicted_per_second"),
        "prompt_tokens_per_second": timings.get("prompt_per_second"),
        "finish_reason": choice.get("finish_reason"),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "characters": len(body),
    }


def prompt_for_subject(path, subject):
    prompt = path.read_text(encoding="utf-8")
    placeholder = "**[INSERT SUBJECT HERE — OR SELECT A COMPLEX MULTIDISCIPLINARY SUBJECT AUTONOMOUSLY]**"
    if placeholder in prompt:
        prompt = prompt.replace(placeholder, f"**{subject}**")
    return prompt


def make_messages(protocol, history, mode, checkpoint_chars):
    messages = [{"role": "user", "content": protocol}]
    if not history:
        return messages
    if mode == "full":
        for reply in history:
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": "NEXT"})
    else:
        # Preserve the most recent checkpoint rather than attempting to keep
        # hundreds of thousands of previous tokens in a single finite context.
        last = history[-1]
        tail = last[-checkpoint_chars:]
        messages.append({"role": "assistant", "content": tail})
        messages.append({"role": "user", "content": "NEXT"})
    return messages


def cap_budget(target, context, prompt_tokens, template_margin, user_cap=None):
    room = max(0, context - prompt_tokens - template_margin)
    effective = min(target, room)
    cause = "target"
    if effective < target:
        cause = "context"
    if user_cap is not None and effective > user_cap:
        effective = user_cap
        cause = "user-limit"
    return effective, cause


def report_path(output, worker):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"ai-progressive-{worker}-{stamp}.json"


def write_report(path, report):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_worker(worker, args, protocol):
    base = baseline.WORKERS[worker]
    if not baseline.healthy(base):
        raise RuntimeError(f"{worker} is offline or loading")
    context, context_source = server_context(base, args.context_override)
    report = {
        "schema": "sirus-progressive-benchmark-v1",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": platform.node(),
        "worker": worker,
        "url": base,
        "model": "sirussofts-coder",
        "cycles_requested": args.cycles,
        "context_tokens": context,
        "context_source": context_source,
        "history_mode": args.history,
        "subject": args.subject,
        "growth": "ceil(1500 * (7/4)**(cycle-1))",
        "user_output_cap": args.max_output_tokens,
        "cycles": [],
        "state": "running",
    }
    path = report_path(args.output, worker)
    history = []
    print(f"\n{worker}: context {context} tokens ({context_source}); {args.cycles} cycles",
          flush=True)
    try:
        for cycle in range(1, args.cycles + 1):
            target = cycle_target(cycle)
            messages = make_messages(protocol, history, args.history, args.checkpoint_chars)
            prompt_tokens, margin = approx_prompt_tokens(base, messages)
            request_tokens, limit = cap_budget(
                target, context, prompt_tokens, margin, args.max_output_tokens)
            result = {
                "cycle": cycle,
                "mathematical_target_tokens": target,
                "estimated_prompt_tokens": prompt_tokens,
                "template_margin_tokens": margin,
                "effective_requested_tokens": request_tokens,
                "limit_source": limit,
            }
            report["cycles"].append(result)
            if request_tokens < 1:
                result["status"] = "context-exhausted"
                report["state"] = "context-exhausted"
                print(f"  cycle {cycle}: no context remaining for generation; stop", flush=True)
                break
            print(f"  cycle {cycle}/{args.cycles}: target={target:,}; request={request_tokens:,}"
                  f" ({limit}); prompt≈{prompt_tokens:,}", flush=True)
            started = time.perf_counter()
            try:
                body, metrics = request_cycle(base, messages, request_tokens, args.timeout)
            except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
                result["status"] = "error"
                result["error"] = f"{type(exc).__name__}: {exc}"
                result["elapsed_before_error_seconds"] = round(time.perf_counter() - started, 3)
                report["state"] = "error"
                print(f"    ERROR {result['error']}", flush=True)
                break
            result.update(metrics)
            actual = metrics["completion_tokens"]
            if limit == "context":
                result["status"] = "context-limited"
            elif limit == "user-limit":
                result["status"] = "user-limited"
            elif actual < target and metrics["finish_reason"] != "length":
                result["status"] = "early-stop"
            elif actual < target:
                result["status"] = "output-limited"
            else:
                result["status"] = "target-reached"
            result["target_achievement_ratio"] = round(actual / target, 6)
            if args.history == "checkpoint":
                history = [body]  # avoid retaining all previous outputs in RAM
            else:
                history.append(body)
            print(f"    actual={actual:,} tok; {metrics['wall_seconds']:.2f}s;"
                  f" {metrics['generation_tokens_per_second']} tok/s;"
                  f" {metrics['finish_reason']}; {result['status']}", flush=True)
            if args.save_transcripts:
                result["response_text"] = body
            else:
                result["checkpoint_excerpt"] = body[-min(args.checkpoint_chars, len(body)):]
            write_report(path, report)
        else:
            report["state"] = "completed"
    except KeyboardInterrupt:
        report["state"] = "interrupted"
        print("\nInterrupted; saving completed cycle metrics.", flush=True)
    finally:
        write_report(path, report)
        print(f"  saved: {path}", flush=True)
    return 0 if report["state"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", nargs="+", choices=baseline.WORKERS,
                        default=["amd-vulkan"], help="Worker(s), benchmarked separately")
    parser.add_argument("--cycles", type=int, choices=range(3, 26), metavar="3..25",
                        default=5, help="Automatic responses per worker (default: 5)")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--history", choices=("checkpoint", "full"), default="checkpoint")
    parser.add_argument("--checkpoint-chars", type=int, default=2400,
                        help="Previous reply tail retained when using checkpoint history")
    parser.add_argument("--max-output-tokens", type=int, default=None,
                        help="Optional operator-imposed safety cap; default is none")
    parser.add_argument("--context-override", type=int, default=None,
                        help="Only for a server without /props; must match actual context")
    parser.add_argument("--timeout", type=int, default=1800, help="Per response timeout in seconds")
    parser.add_argument("--output", default="~/sirus-ai-benchmarks")
    parser.add_argument("--save-transcripts", action="store_true",
                        help="Save full model outputs in report (can create huge files)")
    args = parser.parse_args()
    if args.checkpoint_chars < 100 or args.timeout < 10:
        parser.error("--checkpoint-chars must be >=100, --timeout >=10")
    if args.max_output_tokens is not None and args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")
    if args.context_override is not None and args.context_override < 1:
        parser.error("--context-override must be positive")
    if len(set(args.workers)) != len(args.workers):
        parser.error("Specify each worker only once")
    if "nvidia-cuda" in args.workers and "nvidia-vulkan" in args.workers:
        parser.error("The NVIDIA backends share physical VRAM; test separately")
    if baseline.healthy(baseline.WORKERS["nvidia-cuda"]) and baseline.healthy(
            baseline.WORKERS["nvidia-vulkan"]):
        parser.error("Both NVIDIA backends are running; stop one")
    protocol = prompt_for_subject(args.prompt_file, args.subject)
    failures = 0
    for worker in args.workers:
        try:
            failures += run_worker(worker, args, protocol)
        except (OSError, RuntimeError, ValueError, urllib.error.HTTPError) as error:
            print(f"{worker}: FAIL {type(error).__name__}: {error}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(2)
