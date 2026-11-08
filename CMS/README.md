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
