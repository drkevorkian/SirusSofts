# Gentoo Node — outbound SirusSofts delegated-work client

This is the **standalone Gentoo application**. It is independent of the
legacy `node/` staging proof of concept and does not require SSH tunnels.
Run under the non-login `sirusnode` account with OpenRC. It signs every
outbound HTTPS message using a per-node 32-byte key.

## Architecture

```text
Gentoo (Python client, OpenRC)
  |  signed HTTPS POST /node-heartbeat.php   (CPU/GPU/RAM/disk metrics)
  |  signed HTTPS POST /node-work.php        (submit / check job)
  v
sirussofts.com (Hestia PHP CMS)              (authenticate, queue, relay ONLY)
  |
  v
Ubuntu (persistent Python systemd worker)    (run allowlisted jobs)
  |
  v
sirussofts.com (job result) -> Gentoo (status polling/notification)
```

The PHP website **never executes submitted code**. It accepts only
`health_snapshot` and `site_health` work kinds in the initial release.
Each request has an HMAC-SHA256 signature, timestamp, unique nonce and
idempotent 32-character job ID. The worker does not grant interactive access
to Ubuntu or execute shell commands. There are no arbitrary URLs or arguments.

## Gentoo initial installation (root terminal on Gentoo)

```bash
cd /path/to/SirusSofts
bash gentoo-node/install-openrc.sh --check
bash gentoo-node/install-openrc.sh --plan
bash gentoo-node/install-openrc.sh --install
```

Type `INSTALL GENTOO NODE` at the prompt. This installs the client to
`/opt/sirus-gentoo-node/client.py`, its private configuration to
`/etc/sirus-gentoo-node/config.json`, and a random 32-byte key at
`/etc/sirus-gentoo-node/heartbeat.key`. It does **not** start or enable
the OpenRC service. Protect that key like a password: never paste its
contents into ChatGPT or commit it to GitHub.

The key must then be securely copied as the exact binary contents to
the Ubuntu VPS at:
`/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys/gentoo-home.key`.
Both copies must contain **identical 32 bytes** and be readable only by their
respective service accounts (0600). Before copying, the Ubuntu worker install
creates the private nodes/keys directory. Only the holder of this key can
authenticate as `gentoo-home` through the website broker.

The example config targets `https://sirussofts.com`, and the server's TLS
certificate is verified. The Gentoo clock must be synchronized within
two minutes of Ubuntu. Do not disable TLS verification.

## Probe, enroll, and start

After Ubuntu has the key:

```bash
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --config /etc/sirus-gentoo-node/config.json --probe'
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --config /etc/sirus-gentoo-node/config.json --once'
rc-service sirus-gentoo-node start
rc-update add sirus-gentoo-node default
rc-service sirus-gentoo-node status
```

The daemon sends a signed heartbeat approximately every 60 seconds.
Ubuntu shows current CPU/GPU, memory, disk, temperature and uptime data
at `https://sirussofts.com/admin/infrastructure`. An offline node is marked
stale after three minutes without a valid heartbeat.

## Delegate Ubuntu work from Gentoo

```bash
# Submit a job and receive its ID immediately:
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --delegate health_snapshot'

# Submit and wait for the result/status notification:
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --delegate site_health --wait'

# Check a previously returned job ID:
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --status YOUR_32_HEX_JOB_ID'

# Keep polling that job until it finishes (up to 120 seconds):
su -s /bin/sh sirusnode -c 'python3 /opt/sirus-gentoo-node/client.py --status YOUR_32_HEX_JOB_ID --wait'
```

`--delegate` returns a job ID, a state (`queued`, `running`, `done`, or
`failed`) and optional result. `--wait` polls every 4 seconds and prints
state changes. A failed network call does not mean a job did not reach the
broker; use the original request ID when retrying through a future app
integration to avoid duplicate jobs. This revision intentionally does not
provide a general-purpose remote command executor.

The existing `node/tunnel_supervisor.py` remains an **optional** OpenSSH
maintenance channel, not a dependency for node communication or job handling.
