# SirusSofts PHP CMS — Ubuntu 24.04 / HestiaCP

The CMS homepage is `CMS/index.php`, and the administrator dashboard is
`/admin/login`. This is the **main SirusSofts website**, not the cPoint
application.

## Deploy to a new Ubuntu 24.04 VPS

**Start with [the Windows PuTTY SSH instructions](../deploy/ubuntu24/PUTTY-SSH-QUICKSTART.md).**
They give the exact public GitHub clone command and the ordered SSH commands
for firewall preflight, HestiaCP, Nginx, Apache, PHP-FPM, MariaDB, the CMS
first-run setup and live HTTPS security verification.

The more detailed architecture and troubleshooting runbook is
[deploy/ubuntu24/README.md](../deploy/ubuntu24/README.md).

HestiaCP owns web server vhosts, Apache/PHP-FPM handler setup, TLS, site
permissions and iptables firewall rules. **Do not run the legacy Plesk
subdomain helper or standalone Nginx vhost agent on Hestia.**

## Installed layout

When run through the guarded Hestia installer under the separate `sirusweb`
website account:

```text
/home/sirusweb/web/sirussofts.com/
├── public_html/
│   ├── index.php            # Homepage and project routes
│   ├── api.php              # Legacy cPoint bridge, inactive until separately deployed
│   ├── node-heartbeat.php   # HMAC-authenticated node endpoint
│   ├── assets/              # Public CSS and JavaScript
│   ├── app/                 # Internal PHP code: HTTP and HTTPS blocked
│   ├── bin/                 # CLI tools: HTTP and HTTPS blocked
│   └── .htaccess            # Apache defence-in-depth and front routing
└── private/
    └── sirus-cms/
        └── data/cms.sqlite3 # NON-public SQLite including WAL state
```

Hestia's custom HTTP and HTTPS Nginx security fragments are installed
separately. The final launch audit verifies both are actively included.

## First login and security

On first deployment, the installer initializes SQLite and requests a new
CMS administrator username and 14+-character password in the SSH terminal.
The CMS administrator is not a Linux root account and is not Hestia's own
control-panel administrator.

Existing CMS databases are never silently overwritten or reset. Every
project starts unpublished until it is intentionally added and published.

Direct access to `/app/`, `/bin/`, `/tests/` and other private paths must
return 403/404 from HTTPS on `sirussofts.com`, and on `.org` later if
configured as a Hestia alias.
Run `deploy/ubuntu24/07-audit-cms-routing.sh --live --user sirusweb
--domain sirussofts.com` from the original repository checkout once Hestia
domain aliases, DNS and TLS are configured.

## Capabilities and current boundaries

The CMS supports a public landing page, draft/published project listings,
accessible project reordering (drag or keyboard arrows in /admin/projects),
admin settings/pages, a read-only GitHub project-family catalog, node and
database inventory, encrypted SQLite backup/restore CLI, and planned
infrastructure settings.

The CMS **does not automatically deploy or expose** private GitHub
repositories or independently running applications. cPoint, WorldCom and
Sentinel Trader are independent, protected projects, not part of the
first-run CMS installation. Application subdomain and directory routing on
Hestia must use its own reviewed templates, not the older standalone
Nginx agent. SQLite and MySQL/MariaDB migration and cluster job execution
remain separate engineering projects.

## Development checks

GitHub Actions checks PHP syntax, SQLite-backed administration and backup
tests, authenticated node heartbeats, Hestia scripts' no-write `--check`
mode, separate HTTP/HTTPS Nginx private-path denial, and firewall parsing.
A successful CI run is not equivalent to a real IONOS VPS installation;
the live launch audit must pass before announcing the website is online.

## Update an already deployed Hestia CMS

**Never rerun the first-install script** `06-deploy-cms-hestia.sh --deploy`
on an existing site. It intentionally refuses overwrites.

After reviewing a newer feature-branch commit and fetching it into the VPS,
use the dedicated upgrade workflow:

```bash
cd /root/SirusSofts
git status --short
git fetch origin feature/cms-vhost-distributed-nodes
git checkout VERIFIED_REVIEWED_COMMIT
php8.3 CMS/tests/smoke.php
node --check CMS/assets/cms.js
bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com
bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --apply --user sirusweb --domain sirussofts.com
bash deploy/ubuntu24/07-audit-cms-routing.sh --live --user sirusweb --domain sirussofts.com
```

Replace the placeholder with the **specific reviewed SHA**, after checking CI
and inspecting the commit diff. Do not discard local modifications. The update
script runs an interactive gate, verifies Hestia Nginx HTTP+HTTPS protection,
snapshots the live SQLite database consistently (including WAL), and saves the
previous CMS code inside the Hestia domain's private directory. It replaces
only managed CMS code/assets, never overwrites SQLite or resets admin credentials.
Any custom edits inside these managed code directories will be replaced; review
and back them up first. No cPoint project is deployed by the updater.

## Automated GitHub repository import (0.3.0-alpha)

The administrator page `/admin/github` includes **Import all repositories**,
a paginated list of imported repos (including private ones), repository visibility,
and a link to review each CMS project. Pressing the button **queues** work.
It cannot run shell commands, access token files or fetch GitHub data in PHP.

The separate scheduled importer uses GitHub's authenticated `/user/repos`
API, paginates through up to 10,000 repositories, and imports ALL repos visible
to the configured token (owned, collaborator and permitted organizations).
Private repos stay private on GitHub. New CMS entries are **drafts**, and
each repository only becomes a listing when an administrator explicitly
publishes its CMS project. No source is cloned to the webserver, no
applications are deployed, and nothing changes GitHub repository permissions.
Name collision handling generates unique stable slugs; repeat scans preserve
existing CMS titles, descriptions, publication state and manual deletion.
A repository renaming on GitHub updates the private metadata but does not
silently rename existing public routes.

### One-time configuration on the VPS (root PuTTY)

1. Create a GitHub credential whose repository visibility covers everything
   intended for import. A fine-grained PAT may cover all repos owned by ONE
   resource owner; private organization/collaborator access may require
   additional permissions, organization approval/SSO, a classic `repo` scope
   token, or separately configured GitHub App installations. The importer
   can only enumerate repositories visible to the chosen credential.
   Avoid granting write permissions unless genuinely needed. Never
   paste the token into the CMS, ChatGPT, a command argument or GitHub repo.

2. Securely enter the credential in PuTTY without shell-history exposure:

```bash
install -d -o root -g root -m 0700 /etc/sirussofts
read -r -s -p 'Paste GitHub access token (input hidden): ' GH_TOKEN
echo
if [ "${#GH_TOKEN}" -lt 20 ]; then
  unset GH_TOKEN
  echo 'Token too short; no credential saved'
else
  (umask 077; printf '%s\n' "$GH_TOKEN" > /etc/sirussofts/github.token)
  unset GH_TOKEN
  chown root:root /etc/sirussofts/github.token
  chmod 0600 /etc/sirussofts/github.token
fi
```

3. From the reviewed, pinned SirusSofts Git checkout after installing
   CMS 0.3.0-alpha with the standard `08-upgrade-cms-hestia.sh` workflow:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/09-install-github-import.sh --plan
bash deploy/ubuntu24/09-install-github-import.sh --install
systemctl start sirus-github-import.service
journalctl -u sirus-github-import.service -n 30 --no-pager
systemctl list-timers --all | grep sirus-github-import
```

Type `ENABLE GITHUB IMPORT` when prompted. The installer copies
root-owned code to `/usr/local/libexec` and enables a systemd timer.
The worker runs as `sirusweb` with a short-lived systemd
`LoadCredential` mount for the root-owned token. The CMS website itself
does not receive the token. The timer polls every ~2 minutes and actually
calls GitHub only when an import is queued or the 6-hour refresh is due.
The initial `systemctl start` performs the first sync immediately.

4. Sign in to `https://sirussofts.com/admin/github`, inspect the
   discovered count and draft entries, and optionally click **Import all
   repositories** to request an on-demand re-scan.

### Limits and recovery

- "All" means **all API-visible repositories** for the configured credential,
  not every local Git directory or repositories from unconnected Git hosts.
- Import only registers repository metadata and private draft landing pages.
  It does not copy Git source, deploy services or automatically provision
  subdomains. Those will be separate reviewed workflows.
- API failures are recorded under the private sync status without resetting
  the CMS project directory. A failed page stops the import, rather than
  silently pretending discovery was complete.
- Revoking the token in GitHub stops future synchronization; existing
  draft records remain until you explicitly delete them.
- Backup the live SQLite database before CMS or importer upgrades. The
  importer must never run as root because SQLite WAL/shm files belong to
  the unprivileged website account.
- Stop automatic syncing with `systemctl disable --now
  sirus-github-import.timer`; this does not delete imported projects.
