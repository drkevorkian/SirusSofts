# SirusSofts AI — independent persistent input and output tunnels

**Status:** installable, locally tested package; **not deployed to either live server**. Nothing in this bundle changes your running CPU `sirus-ai` service unless an operator explicitly runs an installer. Neither your existing Gentoo web tunnel nor `/Gentoo/live` is replaced. No command-line text editing is required.

## Intended end-to-end topology

```text
 Browser: https://sirussofts.com/Gentoo/AI/
       |
       | HTTPS + independent HTTP Basic authentication
       v
 Ubuntu VPS (Hestia Nginx; existing domain and certificate)
       |  /Gentoo/AI/ → 127.0.0.1:18483 (static chat UI + SSE result reader)
       |
       |  /Gentoo/AI/api/in/* → 127.0.0.1:18485 (INPUT SSH -R listening on Ubuntu)
       |     [SSH connection 1: restricted user sirus-ai-in, keepalive 15s]
       |                       |
       |                       v
       |             Gentoo 127.0.0.1:18082: durable task queue
       |                       |      ↳ NVIDIA CUDA:127.0.0.1:18486 (optional)
       |                       |      ↳ AMD Vulkan: 127.0.0.1:18487 (optional)
       |                       |      ↳ CPU llama:  127.0.0.1:18482 (existing)
       |                       v
       |             Gentoo SQLite durable output queue
       |                       |
       |                       v
       |             Gentoo 127.0.0.1:18084 (OUTPUT SSH -L)
       |     [SSH connection 2: restricted user sirus-ai-out, keepalive 15s]
       |                       |
       v                       v
 Ubuntu 127.0.0.1:18483: authenticated private result collector
       |
       v
 Browser SSE over HTTPS: completed answer and GPU/CPU worker indicator
```

Both SSH connections **originate on Gentoo** because it is behind NAT/Starlink. The connections serve opposite **application directions**: requests flow *into* Gentoo over `-R`, responses are pushed *out* from Gentoo over `-L`. They are distinct SSH processes, credentials, ports, and OpenRC services. Both use the existing independently pinned Ubuntu SSH Ed25519 host key; **no `StrictHostKeyChecking=no`**.

Each tunnel has `ServerAliveInterval=15`, `ServerAliveCountMax=3`, `ExitOnForwardFailure=yes`, bounded reconnect backoff (up to 60s), and OpenRC supervision with `respawn_period=30`. A signed output heartbeat travels across the actual OUTPUT tunnel every 15 seconds and the frontend reports it offline if no heartbeat is received for 40 seconds. The Ubuntu output receiver itself is supervised by systemd. Gentoo persists each job and undelivered result in SQLite and retries delivery until the receiving gateway acknowledges it. **No tunnel can stay physically open during a power/network outage**; the goal is automatic recovery and no loss of completed results, not impossible zero-interruption guarantees.

### Important limitations

- **Not publicly accessible until installed and tested**. A created frontend is not a live production deployment.
- This first version uses an **independent Nginx Basic Auth** login named `sirusadmin` (you choose its password interactively), not the CMS admin session. Do not remove authentication.
- Conversations are saved **locally in the browser**. The SQLite output receiver retains response events; browser-side conversation history does not yet sync across computers.
- The model answer is delivered after each inference finishes, not token-by-token. The output event channel itself uses server-sent events and heartbeat comments.
- GPU workers are optional, require working Gentoo NVIDIA CUDA and AMD Vulkan drivers/tools, and must be validated before being enabled. With GPU workers online, the *task router* targets GPU for 90% of auto-selected jobs, not a guaranteed 90% GPU utilization or GPU FLOPs. GPU offload layers are adjustable in the generated worker services through script changes only after testing. Existing CPU service is the always-available fallback if the model is running.
- The same 7B model can run on CPU, NVIDIA and AMD independently if both GPU backends succeed. They do **not** combine VRAM. RTX/GTX cooling, GPU driver support, power supply and VRAM headroom require live checks.
- Operators should first perform deployment on a test/staging instance or maintenance window, review sshd/Hestia changes, have a separate working SSH session and IONOS rescue console available, and use the rollback copy if needed.

## 1 — Stage the reviewed bundle on Gentoo

Download the ZIP and extract it normally in your preferred graphical file manager. Copy the folder onto Gentoo, or use the SirusSofts feature branch when this bundle has been incorporated into the repository. On Gentoo, in the extracted folder:

```bash
bash gentoo/install.sh --check
sudo bash gentoo/install.sh --plan
sudo bash gentoo/install.sh --install
```

The installer requires the currently running `sirus-ai` and `/etc/sirus-gentoo-node/tunnel.json` from the existing deployment. It creates two new keypairs, a 32-byte random secret, a durable input queue and three OpenRC services. It starts the input service, but **does not open either new tunnel or change existing services yet**. The Gentoo model stays at `127.0.0.1:18482`.

The new keys and token are in `/etc/sirus-ai-dual/` (mode-protected). **Transfer only the two `.pub` files and the `secret`, never either private SSH key.** For example, using already-authorized administrator SSH from Gentoo and the already pinned host key:

```bash
sudo scp -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/sirus-gentoo-node/tunnel_known_hosts \
  /etc/sirus-ai-dual/input_ed25519.pub \
  /etc/sirus-ai-dual/output_ed25519.pub \
  /etc/sirus-ai-dual/secret \
  root@74.208.40.187:/root/
```

If root SSH login is disallowed, transfer to an authorized administrative account using its approved host-key verification and then move the three files to `/root/` on Ubuntu using `sudo`. Do not weaken SSH validation or paste private keys into chat.

## 2 — Stage the same reviewed bundle on Ubuntu (Hestia VPS)

Place the same extracted folder (or the reviewed pinned Git revision) on Ubuntu. In that folder:

```bash
bash ubuntu/install.sh --check
sudo bash ubuntu/install.sh --plan
sudo bash ubuntu/install.sh --install
```

The Ubuntu installer checks for Hestia ownership of `sirussofts.com`, validates the two public keys, checks the shared output token, creates independent restricted `sirus-ai-in` and `sirus-ai-out` SSH users, and creates a **private backup** of `/etc/ssh/sshd_config` before appending dedicated `Match User` restrictions. It validates SSH before reloading it. It also creates a service for the Ubuntu output receiver, protected `htpasswd` credentials (entered without echoing), and *only* two domain-owned Nginx route fragments:

- `nginx.ssl.conf_sirusai` maps `/Gentoo/AI/` to the local frontend/output receiver and `/Gentoo/AI/api/in/` to the separate **input** tunnel.
- `nginx.conf_sirusai` redirects HTTP requests for the AI path to HTTPS. The private ingest path under `/Gentoo/AI/internal/` is explicitly denied from the web.

The existing `/Gentoo/`, `/Gentoo/live`, CMS, TLS certificates, original `sirus-tunnel` account, Hestia firewall, and web domains are not intentionally modified. Hestia must actively include the custom fragments; the installer checks that with `nginx -T` before reloading.

**Authentication username:** `sirusadmin`. The password is set during the Ubuntu install. Do not put it in command history or GitHub.

## 3 — Start both persistent SSH tunnels on Gentoo

After Ubuntu's installer reports PASS:

```bash
sudo bash gentoo/install.sh --activate
sudo rc-service sirus-ai-input status
sudo rc-service sirus-ai-input-tunnel status
sudo rc-service sirus-ai-output-tunnel status
```

Activation verifies the locally pinned SSH key configuration, starts two independent keepalive connections, checks connectivity to the Ubuntu **output receiver** through the actual `-L` path, and registers both OpenRC services for boot. If the output path cannot be reached, autostart is not enabled until it is fixed.

Verification:

On **Gentoo**:

```bash
curl -fsS http://127.0.0.1:18082/api/in/health
curl -fsS http://127.0.0.1:18084/api/out/health
```

On **Ubuntu**:

```bash
curl -fsS http://127.0.0.1:18485/api/in/health
curl -fsS http://127.0.0.1:18483/api/out/health
curl -I https://sirussofts.com/Gentoo/AI/
```

The unauthenticated HTTPS `curl -I` should receive a `401` Basic-auth challenge; the browser prompts for a username and password. Open the intended UI at **https://sirussofts.com/Gentoo/AI/**. Both input/output connection indicators should become healthy; CPU availability is separate. Never expose `127.0.0.1:18482` from Gentoo or `127.0.0.1:18485` and `127.0.0.1:18483` from Ubuntu as public listeners.

## 4 — Optional independent NVIDIA and AMD inference workers

On Gentoo, after the dual-tunnel base system works:

```bash
sudo bash gentoo/gpu-workers.sh --check
sudo bash gentoo/gpu-workers.sh --plan
sudo bash gentoo/gpu-workers.sh --build-nvidia
sudo bash gentoo/gpu-workers.sh --enable-nvidia
sudo bash gentoo/gpu-workers.sh --build-amd
sudo bash gentoo/gpu-workers.sh --enable-amd
sudo bash gentoo/gpu-workers.sh --status
```

Each backend is built into its **own CMake build directory**, not the running CPU build. Missing NVIDIA `nvcc`/`nvidia-smi` or Vulkan ICD/tooling fails before proceeding; the script **does not** install or alter drivers on its own. CUDA selects `CUDA0`; Vulkan enumerates devices and selects a `VulkanN` whose description is AMD/Radeon to avoid accidentally using the NVIDIA GPU through Vulkan. NVIDIA starts conservatively at 16 offloaded layers because the GTX 1660 has 6 GB VRAM; AMD requests 99 offloaded layers but may need a smaller setting if the RX 590 runs out of memory. Check the log and device list before increasing offload.

The model can be present in three inference processes on separate ports, and RAM/VRAM/power must be monitored. If neither GPU is online, the frontend continues to use the existing CPU model. **The 90% setting applies to the dispatch of jobs when a GPU worker is available**, rather than a hard CPU/GPU load limiter.

## Local validation performed before packaging

```bash
python -m unittest discover -s tests -v
for f in gentoo/install.sh gentoo/gpu-workers.sh ubuntu/install.sh; do bash -n "$f"; done
python -m py_compile gentoo/input_server.py gentoo/tunnel.py ubuntu/gateway.py
```

The tests exercise the router, SSH tunnel construction with separate identities/forward directions and keepalives, and an end-to-end simulated inference/outage/recovery path in which Gentoo stores a completed answer while output is disconnected and replays the queued result after the receiver returns. This is not a substitute for live GPU-driver, OpenRC, Hestia or actual network testing.

## Cleanup/rollback planning

No existing service is overwritten. If you want to roll back the *new* integration, disable and stop the three new Gentoo services (`sirus-ai-input`, `sirus-ai-input-tunnel`, `sirus-ai-output-tunnel`), stop the two optional GPU workers, and disable the new Ubuntu `sirus-ai-gateway` service. Ubuntu's installer prints a private sshd backup path; restore it only after reviewing it and validating SSH access. Remove **only** the newly added `nginx.conf_sirusai` and `nginx.ssl.conf_sirusai` Hestia includes, run `nginx -t`, then reload Nginx. Do not delete the existing Gentoo dashboard, Live WebSocket, original SSH tunnel or CPU model. Keep SQLite state backups if you want task recovery.

## Existing Ubuntu installations with port 18482 collision

If Ubuntu already has `sirus-live-socket.service` bound to `127.0.0.1:18482`, DO NOT restart or remove that service. The new AI INPUT tunnel uses Ubuntu `127.0.0.1:18485` instead; Gentoo's existing CPU inference server remains on its OWN loopback `127.0.0.1:18482`. On an Ubuntu installation that stopped after provisioning SSH/accounts because of the earlier collision, use the reviewed `ubuntu/repair-input-port-18485.sh --check` and `--apply` script after fetching this commit. It changes only the AI SSH input user's forwarding restrictions and the AI HTTPS Nginx include. Then on Gentoo, install the updated `gentoo/tunnel.py` into `/opt/sirus-ai-dual/gentoo/tunnel.py` and restart ONLY `sirus-ai-input-tunnel`. Do not rerun the guarded first-time `--install` scripts.

## Offline specialist knowledge (optional)

The new `gentoo/knowledge.py` module reads a local, approved CMS JSONL knowledge export and injects source-cited references at each request boundary. It is OFF unless the input server is given `--knowledge-file /absolute/private/path.jsonl` (or `SIRUS_AI_KNOWLEDGE_FILE`). The web composer now allows Auto, Coding, Physics or Time specialization independent of CPU/GPU selection. No source file is transmitted to external sites. See `../docs/SIRUS_AI_KNOWLEDGE_TRAINING.md`. Existing installations require a separately reviewed, backed-up upgrade; the original installer does not overwrite deployed services.

## Continuous autonomous research (source-only, operator opt-in)

The Gentoo input router now contains `continuous_research.py`, `research_evidence.py`, and `research_admin_auth.py`. They extend — do not replace — the existing local-only manual research loop, chat queue and private SQLite database. Default research state is disabled; source inventory, section revisions, leases, review histories and cycle checkpoints survive process restart. Evidence comparisons use exact source-extracted measurements and explicit administrator-defined applicability keys, not repeated model outputs.

New private controller and evidence GET/POST endpoints require a request-specific HMAC, timestamp and replay-protected nonce, separate from the existing SSH tunnel/output key. The Gentoo file is `/etc/sirus-ai-dual/research-admin.key` (exactly 32 binary bytes, regular file, no symlink, 0600, service-account readable). CMS has a matching private file outside the website root; it signs only allowlisted routes. The key is not generated, exported or committed by source updates. Without it, new administrator operations fail closed.

The reviewed **in-place** Gentoo upgrade now bundles all three modules. Review the pinned SHA, consistent private SQLite snapshot, SSD mount and [deployment plan](../docs/CONTINUOUS_RESEARCH_DEPLOYMENT.md) before a separately authorized apply. This code change does not restart local GPU/model services, train adapters, publish source text, or install anything automatically.


### Research progress, targeted cycles, and private history (source-only)

The private `cr_results` ledger now stores measured per-inference durations and output character counts. Source-cycle completion is committed immediately after its last successful pass. Recently revised or newly eligible sections join a durable `new_only` targeted cycle without reprocessing unchanged work ahead of the configured full revisit interval. Existing cycles and drafts survive additive SQLite migrations. The reported throughput estimate excludes source reads, queueing, retries, GPU contention, and worker allocation; it is not a measured tokens/second rate.

The Gentoo input worker accepts an additional **HMAC-authenticated** local-only history route, `POST /api/in/research/continuous/history`. It takes a validated original source ID and bounded pass offset; returns paginated private unreviewed draft history and job state metadata. The CMS `/admin/ai` displays the page history and offers an explicitly requested JSON report download of the selected eight passes. No report is written to a public directory or automatically approved, published, or used to train model weights. Because reports include raw draft notes, operators must handle them as private and possibly sensitive.

Before deployment, follow [the reviewed in-place deployment and acceptance procedure](../docs/CONTINUOUS_RESEARCH_DEPLOYMENT.md). The new signed route depends on the previously specified private 32-byte key on both hosts. No server changes were performed by these commits.


### Conservative local GPU memory admission (source-only)

Gentoo continuous research now reports logical CPU count, CPU load and host MemAvailable plus available AMD VRAM usage from Linux amdgpu DRM sysfs and NVIDIA memory usage from the fixed local `/usr/bin/nvidia-smi` binary (a one-second subprocess timeout, no shell/network). Unsupported or missing driver counters remain **unknown**, never guessed to be free. The administrative VRAM threshold is opt-in and disabled by default (`min_gpu_free_mib=0`). When nonzero, research must select a specific `amd` or `nvidia` worker, and all observed GPUs for that backend must report sufficient free VRAM. Missing metrics, low VRAM, `auto` or CPU target deny new background leases. The metric is a point-in-time estimate and is **not** a VRAM reservation, inference-preemption control, or interactive-latency guarantee. Probe work occurs outside the private SQLite claim write transaction; ordinary chat and existing worker routing remain unchanged.
