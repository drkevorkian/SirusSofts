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
