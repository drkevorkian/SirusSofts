"""Run progressive prompt with automatic NEXT and intra-cycle CONTINUE (3–25 cycles).

In accumulate mode, the target is the SUM of actual tokens from multiple
requests; each fits the live server context and rolling checkpoint.
Single mode preserves a one-completion-per-cycle measurement. Time budgets
prevent unbounded runs on current hardware and can be disabled by the user.
No inference services, drivers, or OpenRC state are modified.
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


def continuation_instruction(cycle, next_segment, cumulative, target):
    """Differentiate continuing a response from NEXT (which starts another cycle)."""
    return (
        f"CONTINUE RESPONSE {cycle}, PART {next_segment}. Stay in the SAME response "
        f"cycle. You have generated {cumulative} of the {target} output tokens "
        "targeted for this response across its earlier parts. "
        "Continue the substantive technical discussion from the last sentence. "
        "Introduce new specific analysis, derivations, and examples. Do not repeat "
        "the checkpoint or earlier text, do not declare the response finished, "
        "and do not start the next numbered RESPONSE until you receive NEXT. "
        "Produce as much new meaningful content as your current request allows."
    )


def make_segment_messages(protocol, history, segments, mode, checkpoint_chars,
                          cycle, target, generated, segment_count=None):
    """Retain full history if requested; otherwise only the latest text tail.

    The original protocol stays in the prompt. NEXT is sent only between
    logical cycles; CONTINUE requests further segments of the *same* cycle.
    """
    messages = make_messages(protocol, history, mode, checkpoint_chars)
    if segments:
        if mode == "full":
            past = 0
            for index, item in enumerate(segments, 1):
                past += item["tokens"]
                messages.append({"role": "assistant", "content": item["body"]})
                messages.append({"role": "user", "content": continuation_instruction(
                    cycle, index + 1, past, target)})
        else:
            last = segments[-1]["body"]
            messages.append({"role": "assistant", "content": last[-checkpoint_chars:]})
            messages.append({"role": "user", "content": continuation_instruction(
                cycle, (segment_count if segment_count is not None else len(segments)) + 1,
                generated, target)})
    return messages


def accumulation_stall_reason(last_hashes, short_streak, stall_limit):
    """Detect model repetition or too-short responses, never fake progress."""
    if len(last_hashes) >= stall_limit and len(set(last_hashes[-stall_limit:])) == 1:
        return "repeated-output"
    if short_streak >= stall_limit:
        return "low-progress"
    return None


def run_worker(worker, args, protocol):
    base = baseline.WORKERS[worker]
    if not baseline.healthy(base):
        raise RuntimeError(f"{worker} is offline or loading")
    context, context_source = server_context(base, args.context_override)
    report = {
        "schema": "sirus-progressive-benchmark-v2",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": platform.node(),
        "worker": worker,
        "url": base,
        "model": "sirussofts-coder",
        "mode": args.mode,
        "cycles_requested": args.cycles,
        "context_tokens": context,
        "context_source": context_source,
        "history_mode": args.history,
        "subject": args.subject,
        "growth": "ceil(1500 * (7/4)**(cycle-1))",
        "user_output_cap_per_segment": args.max_output_tokens,
        "max_cycle_seconds": args.max_cycle_seconds,
        "max_worker_seconds": args.max_worker_seconds,
        "max_segments_per_cycle": args.max_segments_per_cycle,
        "cycles": [],
        "state": "running",
    }
    path = report_path(args.output, worker)
    history = []
    worker_start = time.monotonic()
    interrupted = False
    print(f"\n{worker}: context {context} tokens ({context_source}); "
          f"{args.cycles} cycles; mode={args.mode}", flush=True)
    try:
        for cycle in range(1, args.cycles + 1):
            target = cycle_target(cycle)
            result = {
                "cycle": cycle,
                "mathematical_target_tokens": target,
                "segments": [],
                "actual_completion_tokens": 0,
                "wall_seconds": 0.0,
                "status": "running",
            }
            report["cycles"].append(result)
            cycle_start = time.monotonic()
            # Only keep the latest fragment in checkpoint mode. Full mode
            # intentionally retains complete content and can exhaust context.
            parts = []
            recent_hashes = []
            short_streak = 0
            print(f"  cycle {cycle}/{args.cycles}: target={target:,}", flush=True)
            write_report(path, report)

            while result["actual_completion_tokens"] < target:
                cycle_elapsed = time.monotonic() - cycle_start
                worker_elapsed = time.monotonic() - worker_start
                if args.max_worker_seconds and worker_elapsed >= args.max_worker_seconds:
                    result["status"] = "worker-time-budget"
                    break
                if args.max_cycle_seconds and cycle_elapsed >= args.max_cycle_seconds:
                    result["status"] = "cycle-time-budget"
                    break
                if (args.max_segments_per_cycle and
                        len(result["segments"]) >= args.max_segments_per_cycle):
                    result["status"] = "segment-budget"
                    break

                generated = result["actual_completion_tokens"]
                messages = make_segment_messages(
                    protocol, history, parts, args.history, args.checkpoint_chars,
                    cycle, target, generated, len(result["segments"]))
                try:
                    prompt_tokens, margin = approx_prompt_tokens(base, messages)
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    result["status"] = "error"
                    result["error"] = f"Prompt tokenization failed: {type(exc).__name__}: {exc}"
                    break
                remaining = target - generated
                requested, limiting = cap_budget(
                    remaining, context, prompt_tokens, margin, args.max_output_tokens)
                if requested < 1:
                    result["status"] = "context-exhausted"
                    break

                elapsed_budget = [
                    seconds - elapsed for seconds, elapsed in (
                        (args.max_cycle_seconds, cycle_elapsed),
                        (args.max_worker_seconds, worker_elapsed)
                    ) if seconds
                ]
                request_timeout = min([float(args.timeout)] + elapsed_budget)
                if request_timeout <= 0:
                    result["status"] = "cycle-time-budget"
                    break

                segment_number = len(result["segments"]) + 1
                record = {
                    "segment": segment_number,
                    "remaining_before": remaining,
                    "estimated_prompt_tokens": prompt_tokens,
                    "template_margin_tokens": margin,
                    "effective_requested_tokens": requested,
                    "limit_source": limiting,
                }
                print(f"    segment {segment_number}: request={requested:,} "
                      f"remaining={remaining:,} prompt≈{prompt_tokens:,} "
                      f"({limiting})", flush=True)
                started = time.monotonic()
                try:
                    body, metrics = request_cycle(
                        base, messages, requested, request_timeout)
                except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
                    record["status"] = "error"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["elapsed_before_error_seconds"] = round(
                        time.monotonic() - started, 3)
                    result["segments"].append(record)
                    # If the deadline expired, report the time-budget cause,
                    # not an unsupported claim of a model context failure.
                    if (args.max_cycle_seconds and
                            time.monotonic() - cycle_start >= args.max_cycle_seconds):
                        result["status"] = "cycle-time-budget"
                    elif (args.max_worker_seconds and
                          time.monotonic() - worker_start >= args.max_worker_seconds):
                        result["status"] = "worker-time-budget"
                    else:
                        result["status"] = "error"
                    break

                record.update(metrics)
                actual = metrics["completion_tokens"]
                record["status"] = "ok"
                result["segments"].append(record)
                result["actual_completion_tokens"] += actual
                result["wall_seconds"] = round(
                    result["wall_seconds"] + metrics["wall_seconds"], 4)
                record["target_achievement_ratio"] = round(
                    min(1.0, result["actual_completion_tokens"] / target), 6)
                if args.save_transcripts:
                    record["response_text"] = body
                else:
                    record["checkpoint_excerpt"] = body[-args.checkpoint_chars:]
                # Even for mode=full, only retain the full answer in memory
                # when selected; default checkpoint has constant memory use.
                if args.history == "checkpoint":
                    parts = [{"body": body[-args.checkpoint_chars:], "tokens": actual}]
                else:
                    parts.append({"body": body, "tokens": actual})

                digest = hashlib.sha256(body.strip().encode("utf-8")).hexdigest()
                recent_hashes.append(digest)
                recent_hashes = recent_hashes[-args.stall_limit:]
                short_streak = short_streak + 1 if actual < args.min_progress_tokens else 0
                print(f"      +{actual:,} tokens -> "
                      f"{result['actual_completion_tokens']:,}/{target:,}; "
                      f"{metrics['wall_seconds']:.2f}s; "
                      f"{metrics['finish_reason']}", flush=True)
                write_report(path, report)

                if result["actual_completion_tokens"] >= target:
                    result["status"] = "target-reached"
                    break
                if args.mode == "single":
                    # Preserves the old measurement: one completion per cycle.
                    result["status"] = (
                        "context-limited" if limiting == "context"
                        else "user-limited" if limiting == "user-limit"
                        else "early-stop" if metrics["finish_reason"] != "length"
                        else "output-limited")
                    break
                stall = accumulation_stall_reason(
                    recent_hashes, short_streak, args.stall_limit)
                if stall:
                    result["status"] = stall
                    break

            if result["status"] == "running":
                result["status"] = "target-reached" if (
                    result["actual_completion_tokens"] >= target) else "stopped"
            result["target_achievement_ratio"] = round(
                min(1.0, result["actual_completion_tokens"] / target), 6)
            result["generation_tokens_per_wall_second"] = round(
                result["actual_completion_tokens"] / result["wall_seconds"], 3
            ) if result["wall_seconds"] > 0 else None
            # Preserve a finite tail across cycles. In full mode the entire
            # cycle output remains available; high memory cost is opt-in.
            if parts:
                if args.history == "checkpoint":
                    history = [parts[-1]["body"]]
                else:
                    history.append("".join(part["body"] for part in parts))
            print(f"    cycle result: {result['status']}; "
                  f"{result['actual_completion_tokens']:,}/{target:,} tokens; "
                  f"{len(result['segments'])} segment(s)", flush=True)
            write_report(path, report)
            if result["status"] in ("worker-time-budget", "error"):
                break
        if report["state"] == "running":
            finished_all = len(report["cycles"]) == args.cycles
            met_all = finished_all and all(
                cycle["status"] == "target-reached" for cycle in report["cycles"])
            errored = any(cycle["status"] == "error" for cycle in report["cycles"])
            report["state"] = (
                "error" if errored else
                "completed" if met_all else
                "completed-with-limits" if finished_all else "budget-exhausted")
    except KeyboardInterrupt:
        interrupted = True
        report["state"] = "interrupted"
        if report["cycles"] and report["cycles"][-1]["status"] == "running":
            report["cycles"][-1]["status"] = "interrupted"
        print("\nInterrupted; stopping all remaining workers and saving metrics.",
              flush=True)
    finally:
        write_report(path, report)
        print(f"  saved: {path}", flush=True)
    if interrupted:
        return 130
    return 1 if report["state"] in ("budget-exhausted",) or any(
        c["status"] == "error" for c in report["cycles"]) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", nargs="+", choices=baseline.WORKERS,
                        default=["amd-vulkan"], help="Worker(s), benchmarked separately")
    parser.add_argument("--mode", choices=("accumulate", "single"), default="accumulate",
                        help="Accumulate CONTINUE segments per cycle (default) or test one response")
    parser.add_argument("--max-cycle-seconds", type=float, default=900,
                        help="Time budget per logical cycle; 0 disables (default: 900s)")
    parser.add_argument("--max-worker-seconds", type=float, default=3600,
                        help="Total time budget per worker; 0 disables (default: 3600s)")
    parser.add_argument("--max-segments-per-cycle", type=int, default=0,
                        help="Optional segment count limit per cycle; 0 disables")
    parser.add_argument("--min-progress-tokens", type=int, default=32,
                        help="Below this count a segment is considered low-progress")
    parser.add_argument("--stall-limit", type=int, default=3,
                        help="Stop a cycle after N identical or low-progress segments")
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
    if (args.max_cycle_seconds < 0 or args.max_worker_seconds < 0 or
            args.max_segments_per_cycle < 0 or args.min_progress_tokens < 1 or
            args.stall_limit < 2):
        parser.error("Time/segment limits must be nonnegative, min-progress >=1, stall-limit >=2")
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
            status = run_worker(worker, args, protocol)
            if status == 130:
                return 130  # Ctrl+C aborts *all* remaining workers
            failures += status
        except KeyboardInterrupt:
            return 130
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
