# SirusSofts Gentoo Node — the WORKER (Python + OpenRC)

**Direction matters:** Ubuntu runs **sirussofts.com** and its CMS. Gentoo
does the work. The website is the authenticated intermediary and persistent
queue; it does **not** execute delegated jobs on Ubuntu.

```text
ADMIN at https://sirussofts.com/admin/infrastructure
    | Queue an approved job for gentoo-home
    v
UBUNTU: Hestia/PHP website + private SQLite queue (NO execution)
    ^
    | HTTPS signed outbound claim + result, every ~4 seconds
    v
GENTOO: independent gentoo-node/client.py + OpenRC (EXECUTES work)
    |
    +--> per-minute signed hardware telemetry + CPU/GPU/disk stats
    +--> polls Ubuntu for assigned work
    +--> executes allowlisted local tasks
    +--> posts the result back to Ubuntu for CMS admin viewing
```

No incoming Gentoo port, reverse SSH tunnel or external push notification
service is needed. OpenRC keeps the Gentoo node process alive across reboots.
The daemon sends a heartbeat every ~60 seconds, checks the queue every
~4 seconds and reconnects with bounded backoff after network failure.
"Notification" is authenticated polling; new work is usually noticed on
the next successful poll. It is not a permanent TCP/WebSocket connection.

## Initial permitted tasks (actually run on Gentoo)

- `health_snapshot`: local Gentoo processor count, RAM, CPU, GPU,
  disk and uptime snapshot (read only).
- `compute_selftest`: bounded deterministic SHA-256 calculation on
  Gentoo with result digest and measured run time.

No arbitrary shell commands, Git commands, path/URL inputs, package
installation, or remotely supplied Python programs. Additional task
types require reviewed changes to BOTH the CMS allowlist and Gentoo
executor, plus tests.

## Ubuntu: install the WEBSITE coordinator only

1. Upgrade the existing Hestia CMS to a tested feature commit with the
   established `deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan`
   followed by `--apply`. When prompted, enter ONLY `UPGRADE CMS`
   and press Enter; do not paste subsequent shell commands into the prompt.
   This installs `node-work.php`, `app/work_broker.php`, the
   administrator queue UI and preserves the existing CMS database.
2. Run `07-audit-cms-routing.sh --live`.
3. Prepare the private node directory and its HMAC key location on
   Ubuntu as the website owner `sirusweb`, without installing the old
   `ubuntu-worker/` executable:

```bash
install -d -o sirusweb -g sirusweb -m 0700 \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys
```

The website will create `nodes/work.sqlite3` and
`nodes/registry.sqlite3` as the `sirusweb` user when the first
authenticated request arrives. The website is both the registry and
queue; it is NOT an Ubuntu executor. DO NOT install or enable
`sirus-ubuntu-worker.service` on the VPS. If that legacy service was
previously installed, disable it before upgrading the coordinator.

## Gentoo: standalone installation

On **Gentoo**, from its local checkout of the reviewed same commit:

```bash
bash gentoo-node/install-openrc.sh --check
bash gentoo-node/install-openrc.sh --plan
bash gentoo-node/install-openrc.sh --install
```

Type `INSTALL GENTOO NODE` at the interactive prompt. The installer
creates the non-login `sirusnode` account and the isolated application
under `/opt/sirus-gentoo-node/`. It makes a private random 32-byte key
at `/etc/sirus-gentoo-node/heartbeat.key`. The OpenRC service is
installed but NOT started/enabled until keys are enrolled.

The Gentoo client configuration is
`/etc/sirus-gentoo-node/config.json`, with default HTTPS endpoints
`https://sirussofts.com/node-heartbeat.php` and
`https://sirussofts.com/node-work.php`.

## Enroll Gentoo node key on Ubuntu

Transfer the **binary** 32-byte key over an authenticated encrypted
channel, not by pasting it into ChatGPT, the CMS or GitHub. If authorized
SCP access exists:

```bash
# Gentoo root. Only use this if your root/SSH policy allows it:
scp /etc/sirus-gentoo-node/heartbeat.key root@74.208.40.187:/root/gentoo-home.key
```

Then on Ubuntu root:

```bash
install -o sirusweb -g sirusweb -m 0600 /root/gentoo-home.key \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys/gentoo-home.key
rm -f /root/gentoo-home.key
```

Verify the resulting files are exactly 32 bytes and private using
`wc -c` and `stat -c '%U:%G %a'`; do not print the key.
Both servers must have synchronized clocks within two minutes.
HTTPS hostname/certificate verification is mandatory.

## Start the Gentoo worker and test end-to-end

On Gentoo, first run a one-time hardware/queue check:

```bash
su -s /bin/sh sirusnode -c \
  'python3 /opt/sirus-gentoo-node/client.py --once'
```

Then enable persistent operation:

```bash
rc-service sirus-gentoo-node start
rc-update add sirus-gentoo-node default
rc-service sirus-gentoo-node status
```

Sign in as CMS admin on Ubuntu:
`https://sirussofts.com/admin/infrastructure`.
After Gentoo has successfully connected and is **Online**, choose it as
the worker and queue `compute_selftest` or `health_snapshot`.
Gentoo will pick up and execute the job and send its result back to the
CMS page. The UI shows the queue state and escaped result JSON.

Individual job status from Gentoo:

```bash
su -s /bin/sh sirusnode -c \
  'python3 /opt/sirus-gentoo-node/client.py --status YOUR_32_HEX_REQUEST_ID'
```

## Security and reliability guarantees

Per-node HMAC-SHA256 protects the signed HTTPS messages; freshness
timestamps and per-message nonces prevent replay. Only the CMS
administrator may queue work. Workers cannot submit new tasks.
Each job is assigned to one node and leased to its authenticated
identity. A job lease expires after 120 seconds if no result arrives;
the CMS can reissue it, up to three attempts. A retried completion
with the same lease/result is safe after a lost HTTPS acknowledgment.

There are no raw shell, Git or arbitrary-script task payloads.
The read-only jobs are designed to be repeatable if a network failure
causes a retry. For state-changing work in the future, use stronger
idempotency/reconciliation rules before enabling it.

## NVIDIA GPU information (worker 0.4.1)

This revision improves NVIDIA identification and performance telemetry
without requiring Python packages or Git. It first tries `nvidia-smi`,
then loads the native NVIDIA **NVML** driver library directly through
Python `ctypes`; it also uses `lspci` to identify card models if
the driver cannot expose a name. AMD and Intel GPU sysfs monitoring
continues unchanged. For multiple NVIDIA cards, NVML enumerates all
accessible devices.

If the dashboard recognizes PCI vendor `0x10de` but lacks utilization,
temperature or VRAM readings even after updating, run on Gentoo:

```bash
command -v nvidia-smi || true
nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits
lspci -nn | grep -Ei 'VGA|3D|NVIDIA'
ls -l /dev/nvidiactl /dev/nvidia0 2>/dev/null || true
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --probe'
```

Do not install NVIDIA proprietary drivers blindly. Native NVML metrics
are available only when the correct supported NVIDIA driver/library is
loaded and accessible to the unprivileged `sirusnode` account. Legacy
cards using Nouveau may be enumerable by PCI but lack the monitoring
interfaces used by NVIDIA's proprietary driver.

For Gentoo installations originally set up using the standalone
ChatGPT-provided shell installer (no Git checkout), use the updated
self-contained `SirusSofts-Gentoo-GPU-Upgrade.sh --upgrade` attachment.
It backups the old Python worker, preserves its existing key and
configuration, replaces only the worker code, and restarts OpenRC.
The installer is not required on Ubuntu; upgrade the CMS separately
using the reviewed Hestia code updater.

## Gentoo PCI inventory fix (worker 0.4.2)

On a mixed NVIDIA GTX 1660 / AMD Radeon RX 590 Gentoo machine, the
KDE Info Center listed both display cards but the original SirusSofts
worker showed only the GTX 1660. The earlier worker started discovery
at `/sys/class/drm/card*/device/vendor`, so GPUs without a corresponding
DRM device were silently omitted.

**0.4.2** inventories all actual PCI display controllers from
`/sys/bus/pci/devices/*/{vendor,class}` (PCI class `0x03`), then
reads metrics from the matching driver/sysfs paths if available.
It merges only DRM-only devices not already discovered by PCI and
excludes GPU audio functions (PCI class `0x04`). The NVIDIA `nvidia-smi`
and NVML telemetry fallback remains enabled.

`lspci -mm -s <PCI slot>` may name a card's GPU family (for example,
Radeon RX 470/480/570/580/590); it cannot always identify the exact
retail model, memory size, or Sapphire board branding. In the absence
of `lspci`, the worker reports vendor and PCI device ID rather than
inventing a model. GPU utilization, temperature and VRAM remain `N/A`
unless the corresponding *running driver* actually exposes those
values; seeing the card in KDE is not proof of driver telemetry.

For a node already installed from the self-contained ChatGPT starter,
use the standalone attachment
`SirusSofts-Gentoo-PCI-GPU-Fix.sh`:

```bash
chmod +x SirusSofts-Gentoo-PCI-GPU-Fix.sh
./SirusSofts-Gentoo-PCI-GPU-Fix.sh --check
su -c './SirusSofts-Gentoo-PCI-GPU-Fix.sh --upgrade'
```

The patcher only updates `/opt/sirus-gentoo-node/client.py`; it
creates a private backup, checks syntax and restarts the OpenRC
service if it was running. The existing HMAC key, config and node
identity stay unchanged. Run the 0.4.2 worker's `--probe` to confirm
that *two* GPU objects appear in the JSON results.

For missing GPU metrics, inspect Gentoo's active drivers as the
non-root node account and via system PCI facilities:

```bash
lspci -nnk | grep -Ei -A4 'VGA|3D controller|Display controller'
su -s /bin/sh sirusnode -c 'command -v nvidia-smi && nvidia-smi'
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --probe'
```

Don't install or change GPU drivers just to obtain dashboard statistics
without separately reviewing their effect on the working KDE/Wayland
desktop. The CMS continues to accept and display both GPU records
without an Ubuntu-side schema change.
