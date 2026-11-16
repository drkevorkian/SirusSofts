# Gentoo Infrastructure observability and safe user-application memory controls

**Scope:** diagnostics and future design. The read-only collector/UI fixes in this
pull request do **not** create a kill/restart API or change running services.

## Current status and corrected reporting

- Gentoo's signed heartbeat contains GPU details, OpenRC started-service names,
  and two fixed loopback health probes (dashboard :18080; application-router :18081).
  This is not a complete inventory of all applications on the machine.
- The live CMS UI previously rebuilt GPU cards showing only name, usage and
  temperature, dropping VRAM even when the collector reported it. That is fixed.
- Missing service/app telemetry is now labeled **not reported**, not **none**.
  Offline node metrics are explicitly historical.
- OpenRC started-service marker symlinks are now listed by name without following
  links. This is a read-only inventory fix, not proof every OpenRC service is healthy.
- If nvidia-smi provides only partial readings, NVML supplies missing fields.
  No temperature, VRAM or GPU load is fabricated if both driver paths are unavailable.

## Remaining driver / freshness checks before a live declaration

On Gentoo, read-only preflight performed by the actual unprivileged node account:
`nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits`
and verify NVML loads as the same account. Check device nodes and group permissions,
the installed agent version and heartbeat delivery age. Diagnose each failed probe
without sending host secrets, full command lines, or credentials to Ubuntu.
The website's live refresh is not a substitute for a fresh authenticated heartbeat.
Verify AMD RX 590 is using its **normal, non-mining BIOS** before benchmarking.

## Planned admin process/memory management feature — not implemented

Provide a **read-only process inventory** first, locally collected from /proc
with bounded RSS, CPU%, executable identity, start time and owner. Classify
each process as:
- **Protected / never actionable**: all Gentoo/OpenRC/system components,
  display/session, SSH/tunnels, CMS and telemetry agents, network/storage,
  and **all llama.cpp inference workers, model loaders, input/output queues,
  knowledge ingestion and training in progress**.
- **Managed optional application**: a nonessential app explicitly enrolled by
  the administrator with a known service unit/process supervisor and reviewed
  stop/restart hook. Initially no entries are actionable until configured.
- **Observed but unmanaged**: everything else. Read-only; no actions, even if
  using significant RAM.

Display measured RSS and memory pressure, estimate the reclaimable app RSS
without double-counting shared memory, and distinguish Linux page cache from
real memory pressure. Linux releases cache automatically; never run
`drop_caches` or indiscriminately kill processes as a cleanup shortcut.

For a **managed optional application** only:
1. Admin chooses an allowlisted application ID, not an arbitrary PID or command,
   and sees dependencies, owner, RAM, active user sessions and reason it is safe.
2. Request an authenticated, auditable job, with confirmation and short expiry.
   Recheck PID start time and executable, supervisor identity, dependency graph,
   recent workload leases, and protected policy immediately before action.
3. Gracefully drain or stop **that app only**; report freed resident memory only
   after verified exit. Offer restart via approved supervisor with readiness
   checks. Escalation to SIGKILL requires a separate explicit action after a
   failed graceful stop, but must never apply to protected or unmanaged processes.
4. If any identity or dependency changed, fail closed. Never expose an arbitrary
   shell, root sudo, service name, process ID, or signal operation from PHP.
5. Keep functionality local when internet is off; the existing public CMS is a
   remote dashboard over authenticated node-to-Ubuntu messages only.

## Validation

- Existing and added Python tests for NVIDIA partial readings, PCI-only
  identification, and OpenRC marker symlinks.
- Node registry must continue to reject unsupported telemetry fields; do not
  change the signed heartbeat schema without paired PHP validation/tests.
- Confirm admin no longer loses VRAM when live WebSocket redraws the GPU card.
- Simulate agent version without metrics, live vs stale heartbeat, partial
  NVIDIA fields, and no telemetry at all.
- Later test the process management feature with fake applications first,
  verifying zero ability to stop Gentoo or any LLM process under adversarial
  IDs, stale PIDs, restarts or network failures.

No installer, database migration, reboot, service restart or production action
is included in this revision.
