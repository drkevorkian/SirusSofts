# Deploy SirusSofts on a CLEAN Ubuntu 24.04 server

**Using PuTTY to get code on the VPS? Start with the [copy/paste Windows PuTTY SSH quickstart](PUTTY-SSH-QUICKSTART.md).**

**Deployment track: HestiaCP (free hosting control panel), Nginx + Apache +
PHP-FPM/FastCGI, MariaDB, independent SQLite, Hestia-managed firewall and
Fail2Ban.**

This deployment path assumes an Ubuntu 24.04 LTS server with NO Plesk,
existing website or hosting panel. It was authored for a wiped VPS. Scripts
are staged and unit-tested in GitHub Actions but **have NOT been executed on
the real VPS**. Operator approval and an IONOS out-of-band console are required
before actual installation.

HestiaCP officially supports Ubuntu 24.04 and offers Nginx proxy, Apache2,
PHP-FPM, MariaDB, firewall, Fail2Ban and a web UI. These are distinct roles:
Nginx is the PUBLIC HTTPS entry point (80/443); Apache handles Hestia's
backend web traffic (normally 8080/8443); PHP-FPM runs PHP as isolated site
users over FastCGI. "All modules" is deliberately **not** enabled: unused
modules and mod_php expose unnecessary code and conflict with FPM.

Vendor references:
- https://hestiacp.com/docs/introduction/getting-started
- https://hestiacp.com/docs/server-administration/web-templates
- https://hestiacp.com/docs/server-administration/firewall
- https://hestiacp.com/docs/reference/cli

## Critical architecture boundary

**Hestia MUST be the only manager of webserver virtual hosts and its
iptables firewall.** DO NOT run any of these older standalone installers
after selecting this track:

- deploy/install-framework-ubuntu.sh (writes competing Nginx vhost/FPM pool)
- deploy/install-nginx-vhost-agent.sh (direct site writer)
- deploy/install-vhost-agent.sh or deploy/vhost_agent.py (Plesk legacy)
- deploy/install-ubuntu.sh (old webhook setup assumptions)
- Ubuntu UFW service alongside Hestia iptables
- a standalone certbot --nginx configuration rewrite alongside Hestia TLS

The prior Nginx agent/proxy app-routing code remains in GitHub for a separate,
UNINSTALLED direct-Nginx track. It was tested independently but does **not**
automatically integrate with Hestia. New Hestia-native per-app routing and
non-root deployment executors require separate staging validation. Do not
enable cPoint, WorldCom, Sentinel Trader or any other app automatically.

## Phase 0 — SSH and host requirements (operator)

Use the actual VPS root console, PuTTY or SSH. Confirm that IONOS exposes an
out-of-band rescue console BEFORE changing firewall rules. Keep one SSH
session open throughout installation.

- Ubuntu 24.04 LTS x86_64/arm64, clean base OS, no Plesk.
- 1 GiB RAM minimum per Hestia; 4 GiB recommended. A 2 GiB VPS may work
  with no mail/DNS/AV/FTP but has limited memory headroom.
- At least 6 GiB disk currently free; 40 GiB SSD recommended.
- SSH reachable with a working key. If using non-default SSH port, update
  Hestia's managed firewall rules and IONOS provider firewall before relying
  on the installer. Inbound 22 by default, 80 and 443 for web and ACME.
- Keep public TCP 8080/8443, 3306, 8787 and all node-backend ports CLOSED.
- Hestia admin TCP 8083: lock to localhost (SSH tunnel) or a trusted /32 IP.
- DNS: A records for both sirussofts.org and sirussofts.com (and selected
  aliases) must point to the new server before requesting HTTPS certificates.
  Review stale AAAA records; an incorrect AAAA breaks some ACME challenges.
- Supply a real admin email and a new hosting-panel hostname FQDN at run time.
  This repository does not guess credentials or live IP addresses.

If git/curl/CA certs are not on a minimal image, use:
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates curl git openssh-server

Clone/check out the SirusSofts feature branch using your approved GitHub
authentication (SSH deploy key for a private repo, NOT a token in a URL):
    git clone --branch feature/cms-vhost-distributed-nodes \
      git@github.com:drkevorkian/SirusSofts.git /root/SirusSofts
    cd /root/SirusSofts
    git rev-parse HEAD
    bash deploy/ubuntu24/01-preflight.sh --strict

Inspect every deployment script. The published feature branch includes the test-backed installation procedure.
A reviewed, CI-green commit is the installation source; verify its SHA before
executing the installer. The older main branch is not needed for this path.

## Phase 1 — Install the free panel and full LAMP/LEMP hybrid stack

The download step retrieves the official Hestia RELEASE installer, stores
its SHA-256 and does NOT execute it. Substitute real values for examples.

    bash deploy/ubuntu24/02-install-hestia.sh --check
    bash deploy/ubuntu24/02-install-hestia.sh --download \
      --hostname YOUR-PANEL-HOSTNAME.example.com --email YOU@example.com
    sha256sum /root/sirussofts-bootstrap/hst-install.sh
    less /root/sirussofts-bootstrap/hst-install.sh

Only AFTER reviewing the vendor script and keeping SSH/rescue access:

    bash deploy/ubuntu24/02-install-hestia.sh --install \
      --hostname YOUR-PANEL-HOSTNAME.example.com --email YOU@example.com \
      --sha256 COPY-THE-EXACT-64-CHAR-SHA256

Type INSTALL HESTIA to confirm. Hestia prompts interactively; do not put
passwords in bash history, CI, web forms or GitHub. If the installer reboots,
reconnect. This installs Nginx, Apache, PHP-FPM with PHP8.3/8.4 option,
MariaDB, Hestia-managed iptables and Fail2Ban, while disabling unneeded
mail, BIND DNS, FTP, AV, webterminal and remote API installation options.
External DNS remains at the DNS provider; this does NOT install authoritative
DNS or production SMTP.

After Hestia finishes:

    bash deploy/ubuntu24/03-install-extensions.sh --install
    bash deploy/ubuntu24/04-audit.sh --live
    systemctl status nginx apache2 mariadb fail2ban --no-pager
    apache2ctl -M
    php8.3 -m
    php8.4 -m

The extensions script adds the PHP modules needed for the CMS and typical
projects: PDO, PDO_SQLITE, PDO_MYSQL, sodium, OpenSSL, cURL, XML, mbstring,
intl, GD, zip, bcmath, SOAP and select supported APCu/Redis/Imagick.
It enables Apache proxy, proxy_fcgi, proxy_http, rewrite, headers,
deflate, ssl, expires and related security modules without activating
mod_php. For any special future project, install only its audited extension.

## Phase 2 — Lock down the hosting panel and firewall

Hestia's initial firewall may allow public port 8083. Don't assume Fail2Ban
alone closes it. Use Hestia's own firewall CLI or panel UI — never UFW on top.

    bash deploy/ubuntu24/05-lockdown-panel.sh --plan
    /usr/local/hestia/bin/v-list-firewall

Locate the rule number that accepts public IPv4 0.0.0.0/0 TCP 8083.
Review it with:
    /usr/local/hestia/bin/v-list-firewall-rule RULE_NUMBER json

With verified SSH + IONOS console access, narrow ONLY that exact rule:

    bash deploy/ubuntu24/05-lockdown-panel.sh --apply --rule RULE_NUMBER

It validates the rule before modifying it and requires typed confirmation.
Default result is **127.0.0.1/32 only**, with no world-facing admin port.
An alternative fixed trusted administrator source can be specified using
--admin-cidr REAL.PUBLIC.IP/32; never use 0.0.0.0/0. Inspect the rules
AGAIN afterward for another public allow on 8083.

To reach the panel via an SSH tunnel from your laptop/desktop:

    ssh -L 8083:127.0.0.1:8083 root@VPS_PUBLIC_HOST

After obtaining a valid certificate for YOUR-PANEL-HOSTNAME, access the panel
through the hostname mapped to local port 8083 (a hosts entry or local DNS
may be required); localhost itself won't normally match the certificate.
Set up Hestia administrator 2FA and store recovery codes OFF-server.

Inspect the IONOS provider firewall and IPv6 controls separately. Confirm
externally that only the approved SSH port, 80 and 443 are reachable.
Verify that 8083 is not publicly accessible, and 3306/8080/8443 are blocked.
Re-run the audit after hardening.

## Phase 3 — Create the CMS website in Hestia (operator UI)

Log into Hestia over the restricted admin connection. Create a dedicated
hosting user, for example **sirusweb**, with non-root PHP permissions.
Then create the primary web domain **sirussofts.com** under that user. Add .org later as an alias, not a separate website.

Do NOT upload a different website into its public_html directory.

For the primary domain:
- PHP backend: 8.3 (matching the CLI initializer below).
- Hestia default Nginx proxy template, default Apache/PHP-FPM backend.
- No FastCGI cache on the authenticated CMS during initial rollout.
- Avoid setting a separate nonstandard docroot.
- Initially keep only the .com domain and its normal www.sirussofts.com alias. Later add sirussofts.org and www.sirussofts.org as Hestia aliases on the same site and expand its TLS certificate. Never create a second CMS database.
- Hestia owns the root-domain TLS certificate and its renewal. Issue a valid
  certificate covering all configured aliases after DNS and port 80 work.
  Don't run another standalone certbot/NGINX rewrite.

Verify the domain exists:
    /usr/local/hestia/bin/v-list-web-domain sirusweb sirussofts.com

## Phase 4 — Deploy the SirusSofts CMS files and create private SQLite

From the same reviewed feature-branch checkout:

    bash deploy/ubuntu24/06-deploy-cms-hestia.sh --plan \
      --user sirusweb --domain sirussofts.com

Read the output. It refuses to overwrite an existing CMS, project or database.
The installer verifies a fresh Hestia Coming Soon page by comparing its entire
contents with Hestia's installed skeleton template (after domain substitution).
If you changed the default page in any way, the deployment intentionally stops
to prevent overwriting your content.

When the Hestia domain and account are ready, type DEPLOY CMS:

    bash deploy/ubuntu24/06-deploy-cms-hestia.sh --deploy \
      --user sirusweb --domain sirussofts.com

The script installs TWO root-owned Nginx includes, because default Hestia
templates load HTTP via `nginx.conf_*` and HTTPS via `nginx.ssl.conf_*`.
Both block /app, /bin, /tests, /private, /deploy and sensitive extensions.
The HTTP include must be active before copying any PHP source. If SSL already
exists, its HTTPS include must also be active or deployment stops. Otherwise
the HTTPS guard is staged in advance for Hestia's later SSL activation.
After enabling HTTPS, the dual-protocol routing audit (07-audit-cms-routing.sh) MUST pass. It stages
only CMS files, backs up a recognized Hestia default index page privately,
and initializes SQLite/admin credentials as the NON-root website user.
CMS user data is stored in:
    /home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3

The private folder is OUTSIDE public_html. No scripts are deployed for
cPoint, WorldCom, Sentinel Trader, and no project is auto-published.

This command does not issue HTTPS certificates, create DNS, replace the
existing GitHub webhook agent or activate private projects. Re-running
--deploy after an installation intentionally refuses to overwrite files.

## Phase 5 — Prove the website is safe before DNS/public cutover

From an EXTERNAL machine, after .com DNS and Hestia TLS:
    curl --fail --head https://sirussofts.com/
    curl --head https://sirussofts.com/app/core.php
    curl --head https://sirussofts.com/bin/setup.php
    curl --head https://sirussofts.com/private/
    curl --head https://sirussofts.com/admin/login

Expected: public .com root and admin login return success, private paths
return 403 or 404 (never 200 or raw PHP). The .org hostname is NOT
required until it is explicitly added as an alias with its own valid TLS.
Do not bypass certificate errors.

Run the local HTTPS guard check for .com after TLS is active. If .org is later configured as an alias, append `--also-domain sirussofts.org` to the command:
    bash deploy/ubuntu24/07-audit-cms-routing.sh --live \
      --user sirusweb --domain sirussofts.com

Run:
    bash deploy/ubuntu24/04-audit.sh --live
    nginx -t && apache2ctl configtest
    journalctl -u nginx -u apache2 --since '15 minutes ago' --no-pager
    /usr/local/hestia/bin/v-list-firewall

Verify Hestia remains the exclusive owner of vhosts and firewall. Back up
the new CMS's consistent SQLite snapshot using the restricted CLI before
changing versions. Store encryption keys and tokens outside GitHub and the
web root. Do NOT copy a live SQLite file without taking a WAL-consistent
snapshot and verifying recovery.

## Production change gate and scope

A green Actions workflow tests code but is NOT a real Hestia install, DNS
challenge or the production firewall. This first round prepares the CMS on
a wiped Hestia-managed VPS, not the entire autonomous app platform.

Before enabling cPoint or any project under subdomains/directories, develop
and test separate Hestia-managed proxy templates and app service isolation,
base path, authentication and shared-cookie behavior. The previous independent
Nginx app agent must stay DISABLED under Hestia. Any production rollback
must preserve private SQLite and Hestia configuration. No destructive
reinstall over existing data.

Security note: Hestia's official default Nginx proxy .tpl and .stpl templates
include the separate HTTP `nginx.conf_*` and HTTPS `nginx.ssl.conf_*`
fragments. Never rely on the HTTP include to protect HTTPS requests.
