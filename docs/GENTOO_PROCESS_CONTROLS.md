# Gentoo GPU / process console (source-only; deployment pending)

This upgrade lives on the **authenticated Gentoo dashboard**, reached through
Ubuntu's existing HTTPS reverse tunnel at `https://gentoo.sirussofts.com/`.
It does **not** add a public REST API to the Ubuntu CMS, copy PID lists into
Ubuntu's heartbeat database, or expose a root shell. The Ubuntu infrastructure
page links to this distinct private console after an approved deployment.

## Read-only diagnostics

The Gentoo dashboard refreshes every 10 seconds and now displays up to 48
processes ranked by **driver-attributed NVIDIA compute VRAM**, then resident
RAM. Each row shows process ID, process name, a conservative importance
classification, attributed VRAM (or **unknown**), host RSS, and number of
allowed logical CPUs. It records PID start ticks internally to reject stale
actions. `/proc` sampling is bounded to 4096 PIDs. The dashboard reads
NVIDIA's local `nvidia-smi --query-compute-apps=pid,used_gpu_memory
--format=csv,noheader,nounits` without a shell and with a two-second timeout.

**Caveat:** NVIDIA's compute-process listing may omit graphics and other
allocation categories; driver-wide VRAM usage is *not* the sum of these rows.
Standard AMD amdgpu sysfs supplies global VRAM totals, not reliable
per-process VRAM. An AMD process can therefore show **unknown**, even while
the RX 590 holds gigabytes. Do not infer that VRAM is empty when a row is
missing or the GPU is at 0% utilization. Use `nvtop`/driver diagnostics as
cross-checks when the installed driver supports them, without presenting
unverified figures as per-process allocations.

Importance classes are **protected**, **workload**, and **unknown**; this
is not a dependency analysis or proof that a process can be safely killed.

## Website controls, carefully restricted

The separate HTTPS Gentoo dashboard uses its existing Basic authentication.
To change a process, it also requires an allowed same-origin POST, a short
lived, domain-separated, one-use control token issued only to that
authenticated viewer, and explicit confirmation. The process action accepts
only two fixed operations:

* **Set cores:** narrow the selected process's CPU affinity to N of its
  currently permitted logical CPUs (1..256). This does **not** reduce the
  application's thread count, guarantee a CPU percentage, or reserve cores.
* **Terminate:** request SIGTERM for one selected process. Linux pidfd is
  used for signal delivery so a reused numeric PID is not accidentally
  signaled. SIGTERM does not guarantee a process exits or frees VRAM.

Both actions are restricted to the non-root dashboard **service account's own
processes** and refuse root and known system services, stale PID start times,
the monitoring process itself, and the dashboard's fixed infrastructure
scripts. Workloads require stronger confirmation. The browser can display
controls only when ownership/importance initially appear eligible, but the
server revalidates everything. The control endpoint contains no shell
interpreter, arbitrary command execution, package installation or GPU reset.

**If llama.cpp is running as a separate account such as `sirusai`, these
buttons will correctly refuse to control it.** That is intentional:
cross-user lifecycle actions need a separate, highly constrained, audited
maintenance broker with a fixed list of explicitly approved OpenRC services,
not a general-purpose website-root bridge. Such a broker is not yet installed.

## Clearing VRAM and resetting GPUs

VRAM is normally released by its owning process ending, unloading its model,
or closing GPU contexts. There is no universal safe **clear VRAM** operation.

A driver/GPU reset remains **disabled and not implemented** in this version.
NVIDIA's `nvidia-smi --gpu-reset` requires compatible hardware/permissions
and no active GPU clients, including graphics/display clients; not every GPU
supports it. AMD PCI reset support and impact vary by card/driver. Resets
can interrupt the desktop, SSH access dependencies, AI inference, and saved
work. The RX 590/GTX 1660 should not be reset from a generic authenticated
HTTP request. For future implementation require a separately installed local
maintenance helper, independently verified PCI identity, all clients stopped,
a second explicit confirmation, a cooldown/audit record, service readiness
checks and a tested recovery path. Never grant the web dashboard root.

## Optional read-only diagnostics on Gentoo

The dashboard uses Python's standard library and existing GPU drivers;
no new Gentoo packages are required for the process table. An operator can
**consider** installing monitoring utilities *after reviewing the actual
Portage package names and dependencies*:

- `htop` / `ps` for CPU/RAM process inspection.
- `lsof` to inspect `/dev/dri` and `/dev/nvidia*` open handles.
- `nvtop` where compatible with the AMD/NVIDIA driver stack.
- `pciutils` and `lm-sensors` for PCI devices and temperatures.

Do not emerge or update GPU drivers solely to enable this feature without
checking kernel/driver compatibility and preserving the normal RX 590 BIOS.

## Deployment boundary

The code is in GitHub only. The existing
`gentoo-node/install-dashboard-live.sh --check` and `--plan` preview a
*dashboard-only* upgrade that installs the monitoring/control modules and
backs up prior dashboard source. Do **not** rerun the old full installation.
Deployment remains a separately approved maintenance action on the actual
Gentoo and Ubuntu machines; neither has been modified in this work.

After deployment, verify Basic auth, Origin/CSRF and replay rejections,
same-UID and cross-UID action denial, PID reuse protection, service health
after a benign CPU-affinity trial, driver telemetry limitations, and Ubuntu
CMS routing. Never use a real system service as the first terminate test.


## GPU-device handle inspection

A bounded read-only scan of up to 128 file descriptors for up to 128
candidate processes supplements vendor-reported NVIDIA VRAM. The table
lists known open `/dev/dri/card*`, `/dev/dri/renderD*` and `/dev/nvidiaN`
handles. This can identify potential clients of AMD and NVIDIA drivers,
including processes whose VRAM is otherwise unknown, but **an open
handle does not establish GPU memory consumption**, and access to
other users' `/proc/PID/fd` is often denied. The scanner prioritizes
the top 96 processes by RSS and driver-reported NVIDIA owners. It does
not claim exhaustive GPU-handle coverage of all processes.


## Read-only VRAM accounting and reset-readiness view

The private Gentoo dashboard now shows driver-wide VRAM used/total alongside
NVIDIA **compute-process** allocations, any remaining non-attributed bytes
when exactly one NVIDIA card is present, and the known fixed OpenRC worker
service (`sirus-ai-amd` or `sirus-ai-nvidia`). These are not exhaustive
owners: graphics, driver reservations, inaccessible processes and the bounded
process table can account for the unexplained difference. On multi-NVIDIA
systems no per-card attribution is inferred; AMD per-process bytes remain
unknown. An inconsistent estimate is reported as such and never negative.

It also lists hardware/operational blockers and an operator-only next step.
**The reset action always remains disabled.** This is an eligibility
assessment, not a claim that a GPU can be reset or VRAM flushed. It
issues no OpenRC command, GPU reset, PCI mutation or process signal.
