# SirusSofts Gentoo Node — the WORKER (Python + OpenRC)

**Direction matters:** Ubuntu runs **sirussofts.com** and its CMS. Gentoo
does the work. The website is the authenticated intermediary and persistent
queue; it does **not** execute delegated jobs on Ubuntu.

\`\`\`text
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
\`\`\`

No incoming Gentoo port, reverse SSH tunnel or external push notification
service is needed. OpenRC keeps the Gentoo node process alive across reboots.
The daemon sends a heartbeat every ~60 seconds, checks the queue every
~4 seconds and reconnects with bounded backoff after network failure.
"Notification" is authenticated polling; new work is usually noticed on
the next successful poll. It is not a permanent TCP/WebSocket connection.

## Initial permitted tasks (actually run on Gentoo)

- \`health_snapshot\`: local Gentoo processor count, RAM, CPU, GPU,
  disk and uptime snapshot (read only).
- \`compute_selftest\`: bounded deterministic SHA-256 calculation on
  Gentoo with result digest and measured run time.

No arbitrary shell commands, Git commands, path/URL inputs, package
installation, or remotely supplied Python programs. Additional task
types require reviewed changes to BOTH the CMS allowlist and Gentoo
executor, plus tests.

## Ubuntu: install the WEBSITE coordinator only

1. Upgrade the existing Hestia CMS to a tested feature commit with the
   established \`deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan\`
   followed by \`--apply\`. When prompted, enter ONLY \`UPGRADE CMS\`
   and press Enter; do not paste subsequent shell commands into the prompt.
   This installs \`node-work.php\`, \`app/work_broker.php\`, the
   administrator queue UI and preserves the existing CMS database.
2. Run \`07-audit-cms-routing.sh --live\`.
3. Prepare the private node directory and its HMAC key location on
   Ubuntu as the website owner \`sirusweb\`, without installing the old
   \`ubuntu-worker/\` executable:

\`\`\`bash
install -d -o sirusweb -g sirusweb -m 0700 \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys
\`\`\`

The website will create \`nodes/work.sqlite3\` and
\`nodes/registry.sqlite3\` as the \`sirusweb\` user when the first
authenticated request arrives. The website is both the registry and
queue; it is NOT an Ubuntu executor. DO NOT install or enable
\`sirus-ubuntu-worker.service\` on the VPS. If that legacy service was
previously installed, disable it before upgrading the coordinator.

## Gentoo: standalone installation

On **Gentoo**, from its local checkout of the reviewed same commit:

\`\`\`bash
bash gentoo-node/install-openrc.sh --check
bash gentoo-node/install-openrc.sh --plan
bash gentoo-node/install-openrc.sh --install
\`\`\`

Type \`INSTALL GENTOO NODE\` at the interactive prompt. The installer
creates the non-login \`sirusnode\` account and the isolated application
under \`/opt/sirus-gentoo-node/\`. It makes a private random 32-byte key
at \`/etc/sirus-gentoo-node/heartbeat.key\`. The OpenRC service is
installed but NOT started/enabled until keys are enrolled.

The Gentoo client configuration is
\`/etc/sirus-gentoo-node/config.json\`, with default HTTPS endpoints
\`https://sirussofts.com/node-heartbeat.php\` and
\`https://sirussofts.com/node-work.php\`.

## Enroll Gentoo node key on Ubuntu

Transfer the **binary** 32-byte key over an authenticated encrypted
channel, not by pasting it into ChatGPT, the CMS or GitHub. If authorized
SCP access exists:

\`\`\`bash
# Gentoo root. Only use this if your root/SSH policy allows it:
scp /etc/sirus-gentoo-node/heartbeat.key root@74.208.40.187:/root/gentoo-home.key
\`\`\`

Then on Ubuntu root:

\`\`\`bash
install -o sirusweb -g sirusweb -m 0600 /root/gentoo-home.key \
  /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys/gentoo-home.key
rm -f /root/gentoo-home.key
\`\`\`

Verify the resulting files are exactly 32 bytes and private using
\`wc -c\` and \`stat -c '%U:%G %a'\`; do not print the key.
Both servers must have synchronized clocks within two minutes.
HTTPS hostname/certificate verification is mandatory.

## Start the Gentoo worker and test end-to-end

On Gentoo, first run a one-time hardware/queue check:

\`\`\`bash
su -s /bin/sh sirusnode -c \
  'python3 /opt/sirus-gentoo-node/client.py --once'
\`\`\`

Then enable persistent operation:

\`\`\`bash
rc-service sirus-gentoo-node start
rc-update add sirus-gentoo-node default
rc-service sirus-gentoo-node status
\`\`\`

Sign in as CMS admin on Ubuntu:
\`https://sirussofts.com/admin/infrastructure\`.
After Gentoo has successfully connected and is **Online**, choose it as
the worker and queue \`compute_selftest\` or \`health_snapshot\`.
Gentoo will pick up and execute the job and send its result back to the
CMS page. The UI shows the queue state and escaped result JSON.

Individual job status from Gentoo:

\`\`\`bash
su -s /bin/sh sirusnode -c \
  'python3 /opt/sirus-gentoo-node/client.py --status YOUR_32_HEX_REQUEST_ID'
\`\`\`

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
