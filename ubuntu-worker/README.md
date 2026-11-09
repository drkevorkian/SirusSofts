# Ubuntu Worker — SirusSofts delegated work executor

The website is a broker. This independent Python systemd service runs on
Ubuntu, operating under the existing restricted Hestia website user
`sirusweb`. It reads an authenticated SQLite work queue from the domain's
private directory, not public_html.

## Task allowlist

| Task | Server-side action | Arguments |
|---|---|---|
| `health_snapshot` | Read Ubuntu CPU threads, load, RAM, root disk and uptime | None |
| `site_health` | Check fixed URL `https://sirussofts.com/` with verified TLS, no redirects | None |

No submitted shell command, code, path, hostname or GitHub token ever enters the
worker. All work is read-only except updating job status in private SQLite.
A failed/crashed job can be retried up to three times. Job IDs provide
idempotency, and a node can have at most 32 queued/running requests.

## Install on Ubuntu VPS (root PuTTY)

Use the **existing** Hestia Ubuntu installation; do not reinstall Hestia.
First deploy the new website's `CMS/node-work.php` using the reviewed
`deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan` and `--apply` path.
The upgrade still requires manually entering `UPGRADE CMS` as the
**only response to its prompt**.

```bash
cd /root/SirusSofts
bash ubuntu-worker/install.sh --check
bash ubuntu-worker/install.sh --plan
bash ubuntu-worker/install.sh --install
systemctl is-active sirus-ubuntu-worker.service
systemctl is-enabled sirus-ubuntu-worker.service
journalctl -u sirus-ubuntu-worker.service -n 30 --no-pager
```

Type `ENABLE UBUNTU WORKER`. The installer creates a root-owned
systemd unit and a private work directory:

`/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/`

The job database is `nodes/work.sqlite3`; the authorized node keys are
in `nodes/keys/*.key`. Use a different 32-byte key for each node.
`sirusweb` needs read access to keys and write access to work database.
Do not place the keys in `public_html`, GitHub, or ChatGPT.

## Enroll Gentoo

On Gentoo, use the standalone `gentoo-node/install-openrc.sh` installer to
generate a 32-byte key. Transfer the exact binary key to Ubuntu through
an authenticated encrypted channel, then install it as `gentoo-home.key`
with ownership `sirusweb:sirusweb` and mode 0600.

A practical example, if SSH/SCP to the Ubuntu root account is permitted:

```bash
# Gentoo root:
scp /etc/sirus-gentoo-node/heartbeat.key root@74.208.40.187:/root/gentoo-home.key
```

On Ubuntu root:

```bash
install -o sirusweb -g sirusweb -m 0600 /root/gentoo-home.key \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys/gentoo-home.key
rm -f /root/gentoo-home.key
```

If your root SSH policy blocks SCP, use an authorized existing operator
account and a protected staging file; never disable SSH security to move
the secret. Verify file size is 32 bytes with `wc -c` (not `cat`).

## Verify

On Gentoo, use `--once` to send a signed telemetry heartbeat, then
`--delegate health_snapshot --wait` to request actual Ubuntu work.
On Ubuntu, use `journalctl -u sirus-ubuntu-worker.service` and browse
`/admin/infrastructure`. Do not open any extra public ports: everything
uses outbound HTTPS from Gentoo to 443 on the VPS.
