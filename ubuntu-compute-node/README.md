# Ubuntu local compute node migration and GPU acceptance plan

Status: design only; installer, service port, and live acceptance are not yet implemented. The existing public Ubuntu/Hestia VPS is out of scope. All inference must remain local.

## 1. Before replacing Gentoo

- Record exact OS disk identities (serial/WWN), RAID layout, PDF SSD filesystem UUID, mounts, GPU PCI addresses, installed software, service definitions, listening ports, users/permissions, current model paths and checksums.
- Stop intake and research/PDF workers gracefully; drain or persist queued work. Make consistent SQLite backups using the SQLite backup API. Back up model files, approved knowledge, job state, configuration, credentials and tunnel keys to independent protected storage. Verify restoration before wiping. Never commit credentials, private manuals, or local hardware identifiers.
- Physically disconnect the dedicated PDF SSD before OS installation. Create Linux software RAID1 on the two explicitly identified OS disks using the Ubuntu installer; configure and verify bootability from either disk, RAID monitoring and alerting. RAID1 is not a backup. No application installer may partition, format, or modify RAID membership.
- Install Ubuntu 24.04 LTS with a supported kernel and optional KDE desktop. Preserve hardware configuration; leave custom affinity, overdrive, LACT, and automatic inference disabled.

## 2. Proposed Git-controlled deployment

The existing Hestia installer is for the public VPS, not this worker. Implement a separate local-node package in this directory, on main under the established repository workflow.

- `pull-install.sh`: verify operator-approved full Git SHA and clean checkout, then invoke installer; no unreviewed remote root execution.
- `install.sh --check|--plan|--apply`: Ubuntu-specific prerequisites, explicit configuration, idempotent installation, backup and rollback, and separate activation. The plan is read-only. Do not enable jobs before acceptance.
- Use Ubuntu packages for AMD firmware, Mesa RADV, Vulkan development/runtime tools, build tools, Python, SQLite, SSH and monitoring. Build the approved local llama.cpp revision with Vulkan and record compiler, driver and model versions. Do not require CUDA or ROCm.
- Detect GPU identity using PCI BDF and map it to the actual Vulkan device reported by the pinned build, rejecting ambiguous matches. Verify both cards use amdgpu and that Vulkan enumerates both. On older CIK/Hawaii hardware such as R9 390, the installed kernel may need `radeon.cik_support=0 amdgpu.cik_support=1`; only propose a reviewed, reversible boot change if detection shows it is required. A Vulkan device listing is not sufficient proof of successful inference.
- Port existing local AI router, CPU fallback, per-GPU workers, research and PDF processing, node telemetry, and separate input/output tunnels to systemd. Preserve existing API contracts, local ports, authentication, database schemas and node identity where practical. Restore private keys/configuration from the backup, not Git; retain strict SSH host verification and local-only inference. Keep the VPS and its Hestia configuration unchanged.
- Reconnect PDF SSD only after OS install; verify expected filesystem identity, initially mount read-only for inspection, and enable writes only after backup verification. Use UUID-based mount plus service dependency/mount guards; missing or wrong SSD must stop PDF processing rather than write to the root filesystem. Restore existing access controls and validate SQLite integrity. No automatic formatting or deletion.
- Provide health, upgrade, rollback and benchmark commands. Make service enablement staged and explicit; failure leaves GPU/PDF processing disabled and records the reason.

## 3. GPU benchmark (mandatory before GPU production activation)

Initial reference target: AMD RX 590 and Radeon R9 390. No overclock, custom fan curves, or raised power limits. First verify safe hardware power and cooling arrangements; record idle temperatures and driver errors. Use vendor-appropriate conservative thermal limits, bounded runs and a watchdog; stop on a threshold breach, lost telemetry, GPU reset, or instability. Do not auto-resume after a fault.

Run the same pinned llama.cpp Vulkan executable and identical local GGUF (start with the existing 7B Q4 model, verified by hash). Fix model, context/KV settings, threads, batch/ubatch, flash-attention setting, and GPU offload layers. Confirm actual GPU offload and sufficient VRAM from logs; a silent CPU fallback invalidates a GPU comparison. Verify supported CLI options with that build's `llama-bench --help` and `--list-devices`.

| Phase | Configuration | Goal |
| --- | --- | --- |
| A | CPU only | Known-good inference and benchmark baseline |
| B | RX 590 only | Independent throughput and stability |
| C | R9 390 only | Independent throughput and stability |
| D | Both, independent workers | Aggregate tokens/sec, contention and thermals |
| E | Both, one layer-split model, if supported | Model fit and throughput; no assumed pooled VRAM |

Use warm-up then five measured repeats per case; start with prompt lengths 512 and 2048 tokens and generation of 128 and 256 tokens. Add a production-like long-context test only after short runs pass. Compare prompt processing (pp) and token generation (tg) separately; include both short-run and sustained performance. Perform progressively longer bounded stability checks only after earlier stages pass.

Log raw benchmark results and metadata locally (JSON/CSV): timestamp, CPU/GPU PCI identity, driver/kernel/Mesa versions, llama.cpp SHA, model hash, offload/quantization/context settings, pp/tg tokens per second, repeat variability, VRAM, temperatures, clocks, available power telemetry, kernel errors and failure status. Mark unavailable sensors unknown, not zero. Board sensor power is not whole-system wall power. For simultaneous independent workers measure aggregate throughput over a shared interval, plus per-worker performance; do not simply add isolated benchmarks.

Calculate R9 390 / RX 590 ratios separately for prompt processing and generation under identical settings. Hypothesis: the R9 390's wider memory interface may help generation, but driver, kernel, compute, PCIe, and memory efficiency can change the result. No estimated speed is a measured result. Publish a local summary; scheduler changes require reviewed measurements and successful stability tests.

## 4. Release gates

- [ ] Independent backups restored and verified; RAID1 and redundant boot verified.
- [ ] Clean Ubuntu baseline stable, stock settings, both GPUs correctly detected.
- [ ] CPU inference and local-only behavior validated.
- [ ] Each GPU passes independent correctness, performance and stability tests.
- [ ] Both GPUs pass simultaneous workload checks before concurrent scheduling.
- [ ] Optional layer-split benchmark passes before multi-GPU model mode is enabled.
- [ ] Mount guards, PDF/knowledge integrity, and controlled ingestion pass.
- [ ] Authenticated tunnels, telemetry, durable queues, restarts and rollback pass.
- [ ] Operator explicitly activates production workloads; no VPS reinstall.

The benchmark protocol is not a completed test and does not establish that either card is faster. Existing repository roadmap items must not be marked complete based on this design.

## References

- llama.cpp benchmark: https://github.com/ggml-org/llama.cpp/tree/master/tools/llama-bench
- Mesa RADV driver: https://docs.mesa3d.org/drivers/radv.html
- llama.cpp multi-GPU guide: https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md
