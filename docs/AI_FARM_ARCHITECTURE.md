# SirusSofts AI farm — design baseline (phase 0)

## Goal

One public SirusSofts AI service, reached through the existing Ubuntu/Hestia
HTTPS vhost, backed by **many independent compute workers**: the existing
Gentoo home system, local servers, and optional machines in remote residences
or datacenters. Every participant can have CPU, AMD Vulkan, NVIDIA CUDA,
NVIDIA Vulkan, or other future inference backends.

A computer at a family residence in West Virginia, another in Redding,
California, and the existing Gentoo machines at home should behave as one
**logical service** from the website's perspective. They are *not* silently
one computer with pooled physical RAM/VRAM.

### Architecture

    Browser --TLS--> Ubuntu Hestia/vhost/gateway + scheduler
                           |  secured coordinator/registry/queue
                           |  idempotent jobs, worker leases, results
                           |
              +------------+-------------+
              |            |             |
         Site A node   Site B node    Site C node
          Gentoo        remote          remote
          home         West Virginia   Redding CA
              |            |             |
        CPU / AMD     CPU / NVIDIA    CPU / AMD ...
        / NVIDIA       / AMD           / NVIDIA

- Remote nodes initiate authenticated **outbound** connections to Ubuntu.
  No internet-facing llama.cpp API or inbound home router port forwarding.
- Each node has a unique identifier, public key, approved enrollment identity,
  and per-node authorization; never share one SSH account/private key among
  all nodes.
- Ubuntu delegates **whole inference requests** to a healthy node/worker.
  The website continues using one hostname and common model/API contract.
- HTTPS vhost is a gateway, not the model host; preserve single-core Ubuntu
  capacity by keeping model compute on workers.
- Local site concentrators may represent multiple LAN machines behind a
  single site connection but must report each actual worker separately.
- Only phase 1 home-node registration should be attempted before enabling
  independent remote sites. Existing input/output tunnels remain in place.

## Why whole-request scheduling comes first

Across a wide-area network, splitting each transformer layer or token step
among geographically distant GPUs can be slower than one good local GPU
because every dependent stage adds network latency and transfer overhead.
Therefore first aggregate **throughput** by dispatching separate jobs to
different workers, with failover, queueing and health checks.

Low-latency LAN tensor/pipeline parallel inference may be an advanced,
backend-specific future mode. It requires explicit model sharding, compatible
GPUs/links, data-transfer benchmarks and memory topology. A 64GB RAM Gentoo
node does not become a GPU with 64GB VRAM, nor do 16 PCs automatically become
a single 16-way inference engine.

## Coordinator / node contract (implementation phases)

Phase 0 — baseline (this commit):
- Repeatable CPU/AMD/NVIDIA benchmarks against loopback endpoints.
- Read-only per-node inventory JSON from farm/node-preflight.py.
- Document protocol, lifecycle, rollout and rollback.

Phase 1 — Gentoo home-site integration:
- Coordinator registry on Ubuntu: node_id, site_id, public key, allowed models,
  engine versions, backend name, GPU UUID, VRAM, queue depth and available slots.
- Signed/encrypted node enrollment with an admin-reviewed, single-use token;
  token expires and is never stored in a repository or exposed through a URL.
- Per-node outbound SSH or mTLS connection with pinned identities; no inbound
  LAN firewall openings. Heartbeat includes monotonic timestamp, queue depth,
  available RAM/VRAM, backend health, load and measured tokens/sec.
- Distinguish disconnected, unhealthy, draining, busy and available workers.
- Add/remove/suspend nodes from admin UI, with a visible audit log.

Phase 2 — multi-machine home LAN:
- Install a minimal OpenRC/systemd node agent that advertises its actual
  backends and automatically registers *only after administrator approval*.
- A local site gateway can multiplex independent workers behind one remote
  tunnel; an install on each home PC yields a distinct worker identity.
- Per-worker concurrency limits, memory admission checks, model warmup,
  matching model versions/hashes and weighted scheduling by completed
  benchmark data, not raw core count.
- A durable task ledger with state queued/leased/running/completed/error.
  Each job has a UUID, lease owner, attempt, deadline, output sequence and
  stable completion record to avoid duplicate replies after tunnel recovery.
- Health checked retries occur only on idempotent requests; streaming requests
  need a documented resume or restart policy to avoid duplicated tokens.

Phase 3 — remote multi-site:
- Install via one reviewed --plan / --check / --install command per host.
- Node-local keypair generated on first install. Copy only the public key;
  approved controller sends per-node scoped connection settings.
- Node agent connects outbound over internet to Ubuntu. Verify TLS host,
  SSH host key pins, reconnect backoff, keepalive, revocation and audit.
- Site ID and allowed model policy; admin may mark remote nodes untrusted
  and disallow private prompts. Encrypted prompts are necessarily decrypted
  on the node performing inference.
- Regional availability/latency measurements and job policy: local only,
  approved remote, fastest queue, least cost, or GPU-only.
- No automatic LAN filesystem shares, SSH agent forwarding, or open storage
  mounts to remote participants.

Phase 4 — secure self-service scale:
- Versioned portable installer for Gentoo/OpenRC, Ubuntu/systemd and other
  Linux; explicit prerequisites and dependency preview; no hidden driver
  upgrades or system-wide compiler changes.
- Transactional config staging, atomic service installation, idempotent
  repair/update, backup and rollback; first run --check/--plan must be
  read-only. No destructive "clean install" on a working production node.
- Controlled update channels, signing, metrics, cost caps, task priority,
  per-site quotas, queue starvation prevention and backpressure.
- Log safe performance statistics centrally; do not publish IP addresses,
  user credentials, inference prompts or system diagnostics publicly.

## API outline (planned, NOT YET IMPLEMENTED)

Node registration (admin-approved; authenticated and protected):
- POST /admin/ai/nodes/enroll-request — one-time proof and node public key.
- POST /internal/ai/nodes/heartbeat — signed identity, capabilities, metrics.
- POST /internal/ai/jobs/lease — assigned job ID, bounded expiry.
- POST /internal/ai/jobs/result — ordered output chunks + final ack.
- POST /admin/ai/nodes/drain — stop allocating new requests, finish in-flight.

Browser uses existing /Gentoo/AI/ endpoints; it never talks directly to a
remote node. The coordinator owns retry/failover and authorization.

## Acceptance tests before each phase advances

1. 2 workers on Gentoo: CPU + AMD; prove failure isolation and healthy routing.
2. NVIDIA CUDA + Vulkan run **separately** on GTX 1660; benchmark same model.
3. 2 distinct machines on home LAN, no public ports, unique node IDs.
4. Kill either tunnel: retain jobs and avoid duplicate user-visible completion.
5. Reboot/upgrade a node: new tasks avoid it; controller recovers lease state.
6. Site A offline: valid alternate site executes non-private eligible jobs.
7. Remote WAN test: measure RTT, throughput, error rate, queue, cost and security.
8. Add third-party remote nodes only after review of identity, data handling,
   patch levels, key revocation, physical access and permitted workloads.

## Hardware baseline and RAM expansion

Current home Gentoo host: Ryzen 7 5700X, ASRock B550M Pro SE, 32 GB G.Skill
Trident Z RGB DDR4-3600 CL18 (2x16GB), part F4-3600C18D-32GTZR, NVIDIA GTX
1660 6GB, Sapphire RX 590 8GB. A second 2x16GB matching kit targets 64GB.
Four DIMMs may need a lower speed or adjusted BIOS memory timings; stress
test before declaring a stable 24/7 server. Main-system RAM helps scheduling,
CPU inference and model caching; GPU VRAM remains 6GB and 8GB respectively.

## Preserved deployments

Do not change or replace existing sirus-ai CPU worker (18482), AMD RADV (18487),
AI input / output services and SSH tunnels (Ubuntu 18485/18483), existing Gentoo
dashboard/application tunnels (Ubuntu 18480/18481), or Ubuntu live socket
(18482). New node channels must have dedicated ports or be multiplexed by
a deliberate gateway protocol, not reuse existing bindings.

Current phase is a **design + benchmark baseline**, not a working distributed
coordinator. Do not present this document as deployment-ready farm software.
