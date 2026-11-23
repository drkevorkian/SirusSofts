# Gentoo: two shared physical CPU cores for GUI, web and tunnels

**State:** Source-only, opt-in. No installed node or live affinity is changed by a GitHub commit. This policy supplements, but does not replace, the asynchronous Ubuntu CMS AI admin fix.

## Intended scheduling

On the Gentoo Ryzen 7 5700X, **two whole physical cores**, including both simultaneous-multithreading (SMT) siblings on each core, form **one common GUI/web/tunnel group**. The other six physical cores run PDF processing, local llama.cpp CPU support, indexing, continuous research, and other recognized AI compute workers.

The physical-core groups are **discovered** from Linux `/sys/devices/system/cpu/cpu*/topology`. Never assume Linux CPU 0 and CPU 1 are two different physical cores or that SMT siblings are numbered consecutively. The `--plan` output lists the exact logical CPUs chosen on the actual Gentoo machine.

The web group includes known Nginx, Apache, PHP-FPM and SSH server processes, graphical display managers/compositors, Gentoo dashboard/node/SSH tunnel services, and **the web-facing threads** of `sirus-ai-input`. Separately tagged `sirus-learn` threads of that mixed HTTP/research process run on compute cores. The PDF service, its subprocesses, and local llama.cpp workers also run on compute cores. Unknown or old `sirus-ai-input` code is deliberately *not pinned* until it exposes tagged learning threads; pinning it wholesale to the GUI cores would defeat the purpose.

**Boundaries:** This policy sets verified per-thread Linux scheduling affinity for listed processes. It does **not** exclude all unrelated user processes, kernel threads, interrupt handlers, or timers from web cores. For truly exclusive host-wide CPU reservations, first audit the running host and implement/test cgroup v2 cpuset partitions, interrupt affinities and system housekeeping as a separate, reversible maintenance change. Do not claim hard isolation from taskset affinity alone. The Ubuntu VPS is physically separate and does not gain any extra CPU capacity when Gentoo reserves cores.

## Install safely (Gentoo only)

```sh
cd /root/SirusSofts
bash gentoo-node/install-cpu-roles.sh --plan
# Inspect actual topology, process matches, and any warnings before touching processes.
python3 gentoo-node/cpu_roles.py --plan
# Optional installer: copies source files; does not activate affinity.
bash gentoo-node/install-cpu-roles.sh --apply
python3 /opt/sirus-gentoo-node/cpu_roles.py --plan
# After verifying tagged sirus-ai-input research threads and the correct core topology:
rc-service sirus-cpu-roles start
# Confirm live process TID affinity and UI/AI responsiveness before enabling boot persistence.
rc-update add sirus-cpu-roles default
```

Do not run the installer on the Ubuntu VPS. Require a known-good rollback path and a separate Gentoo terminal before starting the service. The installer refuses to replace an existing service/policy, and normal `--plan` never changes affinity.

To revert the tracked process/thread masks and disable the policy:

```sh
rc-update del sirus-cpu-roles default
rc-service sirus-cpu-roles stop
python3 /opt/sirus-gentoo-node/cpu_roles.py --rollback
```

The state lives in `/run/sirus-cpu-roles-affinities.json` (root-only, ephemeral) and includes each process's start time to guard against PID reuse. Reboot clears any remaining runtime affinity modifications. Validate workload routing on Gentoo with `ps -eLo pid,tid,comm,psr` and `taskset -pc <tid>`; also monitor both GPU memory and load before accepting the change.

## CPU learning versus GPU learning

Setting continuous-research `target=nvidia` prevents fallback to CPU-only *model inference* when the GTX 1660 is unavailable; it does not stop CPU use for PDF extraction, database transactions, memory retrieval, networking, or NVIDIA/AMD GPU host threads. Those activities still need the compute-core group. Do not disable all CPU processing: that would stop learning entirely.

The default for new research configurations is NVIDIA; existing `cr_config.target` values remain preserved until an authorized administrator changes them. The default for new chat routing must be validated with the installed RX 590 Vulkan model before any live cutover.

## Acceptance

1. Confirm at least three discoverable physical cores with complete online/allowed SMT siblings; for the 5700X, expect eight complete groups on a nonrestricted host.
2. Confirm mixed `sirus-ai-input` research workers have `sirus-learn` thread names; old code must fail safe without forced pinning.
3. Observe dashboard, Apache/Nginx/PHP services, SSH reverse tunnels, and GUI compositor using the same web logical-CPU group.
4. Observe PDF extraction, llama.cpp, indexer and research threads outside that group.
5. Run chat, PDFs, admin panel refreshes, restart tests and rollback; check actual performance before activating the OpenRC boot service.
6. Confirm `/admin/ai` returns its local shell rapidly even if Gentoo is unreachable; private panels should fail independently. This requires the separate updated Ubuntu CMS deployment.
