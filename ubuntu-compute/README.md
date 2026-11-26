# Home Ubuntu compute node (Gentoo replacement)

**Do not run install.sh on the public Hestia VPS.** The existing VPS continues to host sirussofts.com, TLS, CMS and output gateway. This is a staged systemd port of the existing Gentoo applications, not a new public installation. It retains the legacy node identity, service names, dashboard hostname and fixed tunnel ports for compatibility.

## 1. Benchmark before enabling continuous inference

On the home computer, use the same llama-bench executable and GGUF model as your existing benchmarkgpu.sh. Stop other inference workloads first. Find their paths in that script, then run (replace the two paths):

```bash
python3 ubuntu-compute/benchmark-dual-vulkan.py \
  --bench /absolute/path/to/llama-bench \
  --model /absolute/path/to/model.gguf \
  --devices 0 1 --minutes 30 --max-temp 80
```

Each completed pass is a pp512/tg128 benchmark. Two processes run concurrently; repeated load/unload means this is not an uninterrupted single model generation. The output directory contains raw per-device logs and one-second sysfs thermal/power/clock/VRAM samples. Blank readings mean unavailable. The script requires both AMD PCI display adapters to report temperatures and stops both on a sensor fault or temperature cutoff. It does not alter firmware, drivers, fan settings or clocks. Verify each log shows the correct distinct device before trusting the results. No hardware test has been executed by the repository author.

## 2. Prepare home Ubuntu

Use a clean, reviewed checkout of this repository. Do not use the old Gentoo OpenRC installers or the public Hestia installer here. Prerequisites:

```bash
sudo apt update
sudo apt install -y git python3 openssh-client curl pciutils vulkan-tools mesa-vulkan-drivers acl poppler-utils sqlite3
bash ubuntu-compute/install.sh --check
bash ubuntu-compute/install.sh --plan
sudo bash ubuntu-compute/install.sh --prepare
```

A working Vulkan-enabled llama-server executable built with the Vulkan backend (with its shared libraries alongside it), the existing 7B GGUF, and functioning AMD graphics drivers are required. No automatic model downloads or kernel/graphics driver changes. Use the model/server from your existing local llama.cpp installation. Check Vulkan indices after BIOS changes/reboots; do not assume 0 is always the 590.

## 3. Re-enroll the existing VPS, without reinstalling it

Stop the old Gentoo node and all three old SSH tunnels first. The old machine must not remain connected using the same node ID or remote ports. Transfer ONLY /root/sirus-compute-enrollment (three public keys and a private heartbeat HMAC key) to the VPS via your existing verified administrative SSH connection. Do not transfer /root/sirus-compute-private or any private SSH key. Example, ONLY if root login and host-key trust are already configured:

```bash
# Home computer. Use the already verified known_hosts for your admin connection.
sudo scp -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -r /root/sirus-compute-enrollment root@74.208.40.187:/root/
```

On the VPS, use a separate clean clone or reviewed checkout (do not switch the deployed CMS revision). Run:

```bash
sudo bash ubuntu-compute/enroll-vps.sh --plan
sudo bash ubuntu-compute/enroll-vps.sh --apply
```

The helper verifies the existing restricted accounts and gateway, refuses if old reverse listeners are still present, and replaces only their authorized_keys and the existing gentoo-home HMAC key. It saves a root-only backup of old keys. It does not modify sshd configuration, Hestia, Nginx, the CMS DB or the gateway service. Existing gateway token and optional research-admin key are copied into the private enrollment bundle, together with the VPS public Ed25519 host key. If the actual private CMS path differs from the documented path, the helper fails closed; review the deployed configuration instead of inventing a new path.

Return the same directory via the authenticated SSH connection:

```bash
# Home computer
sudo scp -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -r root@74.208.40.187:/root/sirus-compute-enrollment /root/
```

If the root SSH connection is not allowed, use an authorized administrator and secure root-only file transfer. Do not relax SSH host-key checks. Compare the VPS public key fingerprint via your existing trusted VPS session.

## 4. Stage local systemd services

```bash
# Replace paths and indices with your verified RX590 and R9390 indices.
sudo bash ubuntu-compute/install.sh --install /absolute/path/to/llama-server /absolute/path/to/model.gguf 0 1
```

The installer copies the model and executable/libraries to private local service locations, retains all original endpoint paths, makes a new private dashboard password, and stages units without starting them. If any step fails, inspect the partial files rather than overwriting them blindly. Keep an adequate amount of free storage for the model and research data. It will not overwrite an existing application installation. No destructive disk operations are included.

### Preserve old data

This installer does not recover Gentoo data erased by OS installation. If you saved the old input.sqlite3, use a **consistent SQLite backup** (not a raw live DB copy without WAL) and restore it before activation at /var/lib/sirus-ai-dual/input.sqlite3 (owner sirusai:sirusai, mode 0600). Preserve existing approved knowledge JSONL under /var/lib/sirus-ai-dual/knowledge if backed up. To enable knowledge injection, add a systemd environment override SIRUS_AI_KNOWLEDGE_FILE pointing at that private file, then daemon-reload. Do not overwrite the live database. Resume research only after validating the restored state. Old chat data and manuals remain unavailable unless recovered from backup/storage.

### Optional PDF storage

Mount the preserved dedicated ext4 SSD by its verified UUID at /srv/sirussofts/storage. Do not format it. Confirm with lsblk -f and findmnt before installing PDF services. Keep a verified backup of the PDF database and objects before any ACL change. Then:

```bash
sudo bash ubuntu-compute/install.sh --pdf
sudo systemctl start sirus-pdf-ingest
sudo -u siruspdf python3 /opt/sirus-pdf/pdf_ingest.py --status
```

Only enable on boot after verifying existing approval/extraction state:
`sudo systemctl enable sirus-pdf-ingest`.
The PDF watcher is separate from research inference. Its systemd unit requires a real mounted storage directory; no root disk fallback. Existing dashboard read access is staged, but privileged Gentoo/OpenRC-specific PDF admin actions, CPU-affinity scripts and any sudo bridges are **not ported** by this installer. Manage the local PDF watcher via systemctl and the local CLI. Do not assume old process controls work on Ubuntu.

## 5. Activate and verify

```bash
sudo bash ubuntu-compute/install.sh --activate
sudo bash ubuntu-compute/install.sh --status
```

This starts CPU and RX590 model servers, the queue, dashboard, signed heartbeat and three tunnels. It checks local model health and output tunnel health before enabling persistent operation. On the VPS, verify:

```bash
curl -fsS http://127.0.0.1:18480/healthz
curl -fsS http://127.0.0.1:18485/api/in/health
ss -lnt | grep -E '127.0.0.1:1848[015]'
```

Verify the CMS infrastructure page, existing dashboard and an actual local-only AI chat. The legacy dashboard and inference UI may retain Gentoo labels. This source assumes the previously deployed gateway and CMS routes exist; it does not upgrade the public website.

## 6. Enable the R9 390 for independent continuous research

After passing the sustained dual-GPU benchmark, drain active chat/research requests before restarting the router:

```bash
sudo bash ubuntu-compute/install.sh --enable-amd2
```

This starts the R9 390 on private port 18488, verifies its model, installs a root-owned opt-in marker and restarts only the input queue. In CMS select **Auto** continuous research target and concurrency 3 for CPU + RX590 + R9390. A newly created research database may default to NVIDIA; change that before resuming. A configured minimum-GPU-free gate may reject auto as before. The second AMD endpoint is research-only and is attributed as amd2 in the ledger, not NVIDIA or Mac. Existing explicit AMD chat stays on RX590. The old Gentoo setup has no amd2 dispatcher unless explicitly opted in. Existing CMS UI may not display the second worker as a separate selector; this is not a new public chat target.

For thermal maintenance stop research before BIOS changes. Remove the opt-in and stop the second server if necessary:

```bash
sudo rm -f /etc/sirus-ai-dual/amd2-research.enabled
sudo systemctl disable --now sirus-ai-amd2
sudo systemctl restart sirus-ai-input
```

## Status and rollback

Source staging only; neither host has been accessed or changed. Live installation, sustained thermal stability, driver compatibility, exact restored database contents, web routing, privileged dashboard bridge parity and CI acceptance remain to be tested. If migration fails, stop the newly started local services before restoring the old dedicated authorized_keys and gentoo-home.key from the private VPS backup with their original ownership/modes. Do not delete any private backup or database. Securely remove the temporary enrollment copies after successful validation and retain the private rollback backup offline. Never commit or paste keys, model data, databases or manuals into GitHub.
