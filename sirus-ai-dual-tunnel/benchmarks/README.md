# SirusSofts AI: GPU and CPU inference benchmark

The benchmark uses the same SirusSofts Qwen coder model alias, prompts, seed,
temperature, and token cap against already running **loopback-only**
llama.cpp HTTP workers. No remote services or package installs.

## Worker endpoints

| Label | Port | Engine | Notes |
| --- | --- | --- | --- |
| cpu | 18482 | Existing CPU | Do not change original OpenRC service |
| amd-vulkan | 18487 | RX 590 RADV | Existing sirus-ai-amd OpenRC service |
| nvidia-cuda | 18486 | GTX 1660 CUDA | Built separately; optional OpenRC service |
| nvidia-vulkan | 18488 | GTX 1660 NVIDIA Vulkan ICD | Foreground test only, not a production service |

**WARNING:** The two NVIDIA backends share one physical 6 GB GPU. Never run
both NVIDIA inference servers simultaneously. Compare their results from
separate sessions, rather than trying to load two 7B model copies into VRAM.
Neither NVIDIA mode requires changing or disabling the AMD OpenRC service.

## First benchmark — existing CPU vs RX 590

Run on Gentoo, from the repository root:

    python3 sirus-ai-dual-tunnel/benchmarks/benchmark.py \
      --workers cpu amd-vulkan --suite coding --repeats 3 --warmups 1

This writes versioned JSON to ~/sirus-ai-benchmarks; it does not restart
services. A second workload is available via --suite reasoning.

## NVIDIA Vulkan benchmark

The RX 590 Vulkan binary is a generic Vulkan llama-server. In a **second
terminal**, with the NVIDIA CUDA service **stopped**, run:

    sudo bash sirus-ai-dual-tunnel/gentoo/gpu-workers.sh --run-nvidia-vulkan

The script isolates the NVIDIA Vulkan ICD, enumerates the GTX 1660 as
sirusai, and launches it as a non-root, foreground process at 127.0.0.1:18488.
No OpenRC service or autostart entry is created. GPU offloading starts at
16 layers to fit the GTX 1660 6GB; adjust only after measuring.

In your benchmark terminal:

    python3 sirus-ai-dual-tunnel/benchmarks/benchmark.py \
      --workers nvidia-vulkan --suite coding --repeats 3 --warmups 1

Stop the foreground Vulkan server with Ctrl+C afterward.

## NVIDIA CUDA preparation and benchmark

Current host has NVIDIA driver 595.71.05 but no nvcc; Portage proposed
dev-util/nvidia-cuda-toolkit-12.9.2, alongside sys-devel/gcc:14.
GCC 15 remains the default system compiler. **Do not change system-wide GCC
or rebuild Mesa or LLVM for this work.** Review Portage's current preview
before installation:

    sudo emerge --pretend --verbose sys-devel/gcc:14 dev-util/nvidia-cuda-toolkit

When satisfied with the dependencies, install those packages with emerge --ask.
The CUDA build command checks for nvcc, a functioning NVIDIA driver, and a
GCC 14 C++ compiler, selecting Turing architecture 75:

    sudo bash sirus-ai-dual-tunnel/gentoo/gpu-workers.sh --build-nvidia

Then, only after the NVIDIA Vulkan foreground process has exited:

    sudo bash sirus-ai-dual-tunnel/gentoo/gpu-workers.sh --enable-nvidia

The initial OpenRC CUDA worker uses port 18486 and 16 GPU layers; successful
health checks enable it at boot. Monitor VRAM before increasing layer counts.
If it fails, inspect startup logs rather than rerunning the create-only installer.

    python3 sirus-ai-dual-tunnel/benchmarks/benchmark.py \
      --workers nvidia-cuda --suite coding --repeats 3 --warmups 1

## Compare previous runs

    python3 sirus-ai-dual-tunnel/benchmarks/benchmark.py \
      --compare ~/sirus-ai-benchmarks/ai-benchmark-coding-*.json

Only compare identical suites, model quantizations, token caps, context sizes,
and temperatures. Benchmarks show both end-to-end request latency and
llama.cpp's internal measured prompt/generation token speeds; service
availability is not inferred from throughput alone. Warm-up runs are excluded
from medians.

These comparisons measure **single-model inference per GPU**, not aggregate
server throughput. To benchmark a future multi-node farm, additionally measure
concurrent requests, scheduler queue time, p50/p95 latency, task failures,
network RTT, and total requests/minute.

## Future test requirements

- 5+ repeats with longer outputs, memory consumption and utilization samples.
- A CUDA vs Vulkan comparison on the exact **same** GTX 1660 before choosing
  the production backend for that GPU.
- RX 590 vs GTX 1660 comparison on identical prompts and quantization.
- Re-run after DDR4 RAM expansion; system RAM does not increase GPU VRAM.
- Keep sirus-ai, sirus-ai-input, and both SSH tunnels operational.

## Simultaneous multi-worker baseline (CPU + AMD + NVIDIA CUDA)

Run this **only when** the CPU worker (18482), AMD Vulkan worker (18487)
and NVIDIA CUDA worker (18486) are online. Keep the temporary NVIDIA Vulkan
server (18488) **stopped** so that both NVIDIA backends do not contend for
the same GTX 1660.

    python3 sirus-ai-dual-tunnel/benchmarks/concurrent_benchmark.py \
      --workers cpu amd-vulkan nvidia-cuda \
      --suite coding --repeats 3 --warmups 1

The runner starts one request on each selected backend per round, measures
from the first submission until the last completion, and saves a JSON report
containing each worker's completion status and token counts. Its combined
generation rate is:

    sum(all workers' generated tokens) / concurrent round wall time

This is actual completed-request throughput, not a sum of independent
single-worker benchmarks. It includes request overhead and prompt processing.
CPU work from partially offloaded CUDA inference can compete with the
standalone CPU worker, so aggregate throughput is not expected to equal
the sum of the previously reported rates.

The runner **does not** change service settings, create remote connections,
or configure farm nodes. For repeatable comparison, run the same workload
with the same settings; benchmark concurrency and failover separately.


## Progressive Token Saturation Protocol (automatic NEXT)

This suite runs the **user-specified full prompt**, starting with RESPONSE 1 and
automatically sending NEXT for the chosen number of responses. Unlike the
original fixed-length speed benchmark, it does not impose a 192-token response
limit. CLI --cycles accepts 3 through 25; default is 5.

    python3 sirus-ai-dual-tunnel/benchmarks/progressive_benchmark.py \
      --workers amd-vulkan --cycles 5

Select a different cycle count, subject or workers without changing source:

    python3 sirus-ai-dual-tunnel/benchmarks/progressive_benchmark.py \
      --workers cpu amd-vulkan nvidia-cuda \
      --cycles 10 \
      --subject "Robust distributed AI inference systems"

Workers run **separately**, each with its own independent NEXT history. A
50-cycle or simultaneous run is not supported by this suite; the separate
concurrency benchmark measures simultaneous requests.

Each response targets ceil(1500 × 1.75^(cycle−1)): 1500, 2625, 4594, 8040,
14069, and so on. The target grows exponentially: 25 cycles requests far
more than any present-day model's context window could hold. Thus every
cycle reports the *mathematical target*, server's context size from GET /props,
approximate prompt tokens from POST /tokenize, *effective max_tokens requested*
and actual output tokens, output rate, finish reason and target achievement.
A context ceiling is not falsely reported as success. By default responses
are retained in memory only during the test; JSON stores a short checkpoint,
output hash, and metrics. --save-transcripts stores full response text.

History mode defaults to checkpoint: the latest response tail is included
with the instruction NEXT and the full protocol is retained. This is a
**lossy continuation**; for complete history use --history full but expect
context exhaustion sooner. Use --checkpoint-chars N to adjust retained text.

The benchmark **does not change the llama.cpp model/server context**. If
a server advertises 2048 total context tokens, the full protocol prompt may
leave fewer than 1500 tokens for its first answer. To change this, use the
explicit per-worker configuration procedure below.

### Remove the old hardcoded 2048 context for new and existing workers

Future GPU installer services no longer inject --ctx-size 2048. They leave
context selection to llama.cpp unless the operator sets
SIRUS_AI_CTX_SIZE=N. Setting 0 requests model-native context, which may
require significantly more host RAM or GPU VRAM. This can fail on current
6GB/8GB cards; nothing is automatically restarted or upgraded.

To inspect the existing Gentoo CPU, AMD and NVIDIA OpenRC services:

    python3 sirus-ai-dual-tunnel/gentoo/context-config.py --show

Preview replacing the AMD context with 8192 tokens (no modifications):

    python3 sirus-ai-dual-tunnel/gentoo/context-config.py --plan amd-vulkan 8192

Preview model-native context (0) on CPU:

    python3 sirus-ai-dual-tunnel/gentoo/context-config.py --plan cpu model

**Apply only when ready for a short inference service restart**:

    sudo python3 sirus-ai-dual-tunnel/gentoo/context-config.py --apply amd-vulkan 8192

The tool backs up the previous init file, atomically updates its context,
restarts an already-running worker and checks /health. On startup failure it
restores the old file and attempts to bring the original service back. A
stopped worker stays stopped. Existing CPU, GPU and SSH services are not
reconfigured merely by checking out this repository update.

When launching a fresh worker through the GPU installer, the explicit
environment option is:

    sudo env SIRUS_AI_CTX_SIZE=8192 bash \
      sirus-ai-dual-tunnel/gentoo/gpu-workers.sh --enable-amd

For actual model-native context on future larger-memory machines:

    sudo env SIRUS_AI_CTX_SIZE=0 bash \
      sirus-ai-dual-tunnel/gentoo/gpu-workers.sh --enable-amd

Do **not** rerun a create-only --enable-amd against an existing OpenRC
service: it intentionally refuses overwriting one. Use context-config.py
to change an existing service. The CPU service is also supported by the
context configuration helper.

Important: **model context length is finite**, even with unlimited system RAM.
More RAM lets larger context/cache and more models run, but doesn't change
a model's architecture or guarantee useful attention at arbitrarily long
positions. Long contexts can be slow, can exhaust VRAM/host RAM and should
be tested for reliability. --ctx-size 0 means "use model's configured
native context", not "infinite tokens". The benchmark has no arbitrary
maximum output-token cap; it asks for the minimum of each cycle's target
and the detected usable context remaining after the prompt.


## Progressive accumulation: CONTINUE within a logical cycle (v2)

The progressive test now defaults to **accumulate** mode. This differs from
the earlier one-completion-per-cycle test:

- The initial request uses the user's Progressive Token Saturation Protocol.
  Between logical cycles, **NEXT** begins the new numbered response.
- When the model ends its reply early (including finish_reason=stop),
  the benchmark sends **CONTINUE RESPONSE n, PART k** in the same cycle.
- Real completion-token counts are summed across segments until the target
  is reached or a measured condition stops further generation. Each segment
  is bounded by the remaining live worker context, not 2048 or 768 tokens.
- The default checkpoint history retains the full protocol plus the last
  generated tail across segments. This rolling checkpoint is *lossy* and
  measures accumulated output capacity, not a giant single context.
  Use --history full to measure complete history; that exhausts context sooner.
- Identical repeated segments or very short segments eventually stop a cycle
  with a labeled repeated-output / low-progress status (configurable).
- Version 2 JSON reports preserve segment request budgets, actual tokens,
  wall times, finish reasons, output fingerprints, cycle totals, progress
  ratios and stop reasons, saved atomically after each segment. The optional
  --save-transcripts flag embeds full text and can generate enormous JSON files.

Five AMD cycles with automatic continuation:

    python3 sirus-ai-dual-tunnel/benchmarks/progressive_benchmark.py \
      --workers amd-vulkan --cycles 5 --mode accumulate

Old single-response behavior for comparison:

    python3 sirus-ai-dual-tunnel/benchmarks/progressive_benchmark.py \
      --workers amd-vulkan --cycles 5 --mode single

Ten cycles can request hundreds of thousands of output tokens. Default run
budgets avoid unintentional multi-day generation: 900 seconds per logical
cycle and 3600 seconds per worker (workers are benchmarked sequentially).
These are **time budgets**, not model output-token caps. To disable:

    python3 sirus-ai-dual-tunnel/benchmarks/progressive_benchmark.py \
      --workers amd-vulkan --cycles 10 --mode accumulate \
      --max-cycle-seconds 0 --max-worker-seconds 0

Run that uncapped-time example only when actively monitoring the machine.
Other operator options include --max-segments-per-cycle (0 means unlimited),
--max-output-tokens (optional per-request cap), --stall-limit (default 3),
--min-progress-tokens (default 32), and --checkpoint-chars (default 2400).

A cycle can finish as target-reached, cycle-time-budget, context-exhausted,
repeated-output, or low-progress, among other truthful statuses. Partial
cycles do not count as achieving the requested target. The runner may
continue to the next cycle for data unless the whole worker budget expires
or a request fails.

**Ctrl+C now aborts all remaining workers** after saving completed segments,
rather than moving from an interrupted CPU run to the GPU workers.
This update only changes benchmark scripts; it does not restart any worker,
change GPU fans or drivers, or alter SSH tunnels.
