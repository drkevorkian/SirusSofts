# PuTTY SSH quickstart — clean Ubuntu 24.04 VPS

**Exact procedure to get SirusSofts from GitHub onto an EMPTY Ubuntu 24.04
server and install the website hosting stack.** No Plesk. No VPS credentials
are needed in the public GitHub clone command.

**Important:** Use the tested `main` branch for all SirusSofts installations.
This GitHub checkout does NOT automatically change the live server.

Full explanation: [README.md](README.md).

## 0 — Connect to Ubuntu using PuTTY on Windows

1. Open PuTTY. Enter the IONOS VPS **public IPv4 address** in **Host Name**,
   set **Port 22**, select **SSH**, press **Open**.
   If you configured a different SSH port, use that instead, but note that
   `01-preflight.sh --strict` currently deliberately requires port 22 to
   avoid a firewall lockout during the first Hestia installation.
2. Verify the first-connect SSH host key fingerprint **through your IONOS
   provider console** before accepting it. A changed key is expected after a
   complete reinstall, but always confirm it independently.
3. At `login as:`, type `root` and enter the password, or use your configured
   PuTTY private key. The password will not echo; this is normal.
4. PuTTY pastes clipboard text by **right-clicking** the terminal. Commands
   below are for the Ubuntu terminal, NOT Windows Command Prompt.
   Use **Ctrl+C** to interrupt a mistaken command, never Ctrl+V as a paste
   assumption. Keep provider rescue/KVM console access available.

## 1 — Copy SirusSofts from GitHub onto the server

Enter **each line** in PuTTY. These are safe to paste into a fresh Ubuntu
shell after you are signed in as root:

```bash
whoami
lsb_release -a
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git openssh-server
cd /root
git clone --single-branch --branch main https://github.com/drkevorkian/SirusSofts.git /root/SirusSofts
cd /root/SirusSofts
git status --short --branch
git rev-parse HEAD
bash deploy/ubuntu24/01-preflight.sh --strict
```

The exact code is now physically at `/root/SirusSofts` on the VPS.

**If Git says the folder already exists, DO NOT delete it or reclone over
it.** Inspect `ls -la /root/SirusSofts` and `git -C /root/SirusSofts
status --short --branch`. Never use `git reset --hard` over unknown
changes. The project is public, so no username, token, personal access
token or GitHub SSH key is needed for this initial `git clone`.

The output of `git rev-parse HEAD` records the exact deployment revision.
You can compare it with the green GitHub Actions run linked in PR #5.
If the release branch moved after the reviewed revision, pin the SHA from
the latest successful run with `git checkout EXACT_40_CHARACTER_SHA` and
`git rev-parse HEAD` before executing installation.

## 2 — Install HestiaCP, Nginx, Apache, FastCGI PHP and MariaDB

Choose your real control-panel hostname, usually `panel.sirussofts.org`,
and a real administrator email address. Enter these interactively without
placing credentials in the Git checkout. Substitute actual values below.
A valid panel hostname should have an A record pointed to the VPS public IP.

First, only download the official Hestia installer and display its checksum:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/02-install-hestia.sh --check
bash deploy/ubuntu24/02-install-hestia.sh --download \
  --hostname panel.sirussofts.org --email REPLACE_WITH_YOUR_REAL_EMAIL
sha256sum /root/sirussofts-bootstrap/hst-install.sh
less /root/sirussofts-bootstrap/hst-install.sh
```

Press **q** to leave `less`. Hestia's current RELEASE script supports
Ubuntu 24.04 and `--apache yes`, `--phpfpm yes`,
`--multiphp '8.3,8.4'`, MariaDB, firewall and Fail2Ban options.
The download/review step makes **no** package or network configuration changes.

Review the installer. Copy the displayed exact **64-character SHA-256**.
Then run, substituting only your actual email and the approved hash:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/02-install-hestia.sh --install \
  --hostname panel.sirussofts.org --email REPLACE_WITH_YOUR_REAL_EMAIL \
  --sha256 PASTE_EXACT_64_CHARACTER_SHA256_HERE
```

Type `INSTALL HESTIA` at its explicit confirmation. Hestia prompts you
for administrative credentials and may reboot. **Never paste a password
into a shell argument.** Do not interrupt active apt operations.
Do not install UFW separately — Hestia owns the firewall.

After the VPS reboots, start a **new PuTTY SSH connection** to the same
public IP, log back in as root and run:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/03-install-extensions.sh --check
bash deploy/ubuntu24/03-install-extensions.sh --install
nginx -t
apache2ctl configtest
php8.3 -m
php8.4 -m
systemctl status nginx apache2 mariadb fail2ban --no-pager
```

The extra extension step includes PDO_SQLITE, PDO_MYSQL, sodium, curl, XML,
GD, mbstring, intl, zip, bcmath, SOAP and supported Apache modules.
Do not enable every conceivable PHP or Apache module.

## 3 — Restrict the Hestia control panel before public use

Hestia's UI normally listens on **8083**. Keep your active PuTTY session
and IONOS rescue console available before editing any firewall rules:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/05-lockdown-panel.sh --plan
/usr/local/hestia/bin/v-list-firewall
```

Find the **public ACCEPT TCP 8083** rule number and check it, substituting
the actual number, for example `5`:

```bash
/usr/local/hestia/bin/v-list-firewall-rule 5 json
bash deploy/ubuntu24/05-lockdown-panel.sh --apply --rule 5
```

The script refuses if the selected rule does not match a public TCP 8083
allow. It prompts `RESTRICT PANEL`, and narrows that one rule to
`127.0.0.1/32` by default. The panel then requires a PuTTY tunnel.
Alternatively an explicitly reviewed single public IPv4 source can be
specified as `--admin-cidr X.X.X.X/32`.

**Configure the PuTTY tunnel BEFORE you shut the public panel port.**
In PuTTY: Connection → SSH → Tunnels → Source port `8083`,
Destination `127.0.0.1:8083`, click **Add**, then save the session
and connect. PuTTY must remain open for the tunnel to work.

For a correct TLS hostname on Windows, after issuing a valid Hestia host
certificate, add `127.0.0.1 panel.sirussofts.org` to the Windows
`C:\Windows\System32\drivers\etc\hosts` file using an elevated
administrator editor. Then browse **https://panel.sirussofts.org:8083**
while the tunnel is open. The hosts entry is local to that Windows PC;
remove it when you no longer need the tunnel. If the host certificate is
not yet issued, the panel may use a self-signed certificate; verify the
certificate/SSH host fingerprint through your trusted IONOS console
rather than blindly overriding browser warnings.

To request Hestia's host certificate after panel DNS resolves to this VPS,
use Hestia's own tool (not standalone certbot):

```bash
/usr/local/hestia/bin/v-add-letsencrypt-host
```

Finish firewall audit. Do not proceed if it flags another public 8083
ACCEPT rule:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/04-audit.sh --live
```

Keep ports 3306, 8080, 8443 and app backend ports blocked at the IONOS
provider firewall. Expose only the actual SSH port and web 80/443.
Test port reachability from another machine; the local audit cannot prove
what the provider firewall exposes.

## 4 — Create the hosting account and both website domains

Inside **HestiaCP** via your PuTTY tunnel:

1. Create a separate regular website user named **sirusweb** (not the
   control-panel admin account). Use a new strong password stored off-server.
2. Under that user, create the web domain **sirussofts.com**. Use the
   normal Nginx proxy + Apache + PHP-FPM web template; choose PHP **8.3**.
   No separate app source or alternative document root.
3. For now, keep only the default `www.sirussofts.com` alias.
   Later add `sirussofts.org` and `www.sirussofts.org` as aliases of
   this same Hestia website. Never create a second CMS database.
4. At your DNS provider, set the appropriate A records to the **new VPS IP**
   and remove obsolete AAAA records if the VPS has no working IPv6.
   Then enable **SSL + Let's Encrypt** for the main domain and aliases
   using Hestia's own web-domain UI.

If you prefer the Hestia CLI to add an alias after domain creation:

```bash
/usr/local/hestia/bin/v-add-web-domain-alias sirusweb sirussofts.com sirussofts.org
/usr/local/hestia/bin/v-add-web-domain-alias sirusweb sirussofts.com www.sirussofts.org
```

Do not run these if the aliases were already created in the UI.
The `www.sirussofts.com` alias is typically added automatically. Defer
optional .org aliases until .org DNS and TLS are ready.

## After first deployment — upgrading code safely

Once the first install is working, do not rerun `06 --deploy`. Review and pin
a newer GitHub commit, run `php8.3 CMS/tests/smoke.php` and
`node --check CMS/assets/cms.js`, then run:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com
bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --apply --user sirusweb --domain sirussofts.com
bash deploy/ubuntu24/07-audit-cms-routing.sh --live --user sirusweb --domain sirussofts.com
```

The upgrade preserves the current CMS database and saves a consistent SQLite
snapshot with the old code under the domain's protected `private/sirus-cms/releases`
directory. The apply mode requires an explicit typed confirmation. Do not
proceed if the `--plan` command fails. Review the contents of managed code
and asset directories before applying, since these are replaced.

## 5 — Copy and initialize the SirusSofts PHP CMS

Back in PuTTY:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/06-deploy-cms-hestia.sh --plan \
  --user sirusweb --domain sirussofts.com
```

The plan checks that the existing homepage is exactly the unmodified Hestia
Coming Soon template; it refuses a changed homepage. When it reports a clean
dedicated Hestia site:

```bash
bash deploy/ubuntu24/06-deploy-cms-hestia.sh --deploy \
  --user sirusweb --domain sirussofts.com
```

Type `DEPLOY CMS`. You'll be prompted for the SirusSofts CMS admin
username and password interactively. This is separate from Hestia admin.

The script copies website files from the Git checkout into:
`/home/sirusweb/web/sirussofts.com/public_html`

Its private SQLite database is created in:
`/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3`

It installs matching HTTP and HTTPS web-server security guards first and
never overwrites an existing database. No cPoint/WorldCom/Sentinel Trader
source code or GitHub webhook agent is installed by this step.

## 6 — Verify HTTPS, privacy and firewall before calling it live

After .com DNS and Hestia-issued TLS are working (.org is optional):

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/07-audit-cms-routing.sh --check
bash deploy/ubuntu24/07-audit-cms-routing.sh --live \
  --user sirusweb --domain sirussofts.com
bash deploy/ubuntu24/04-audit.sh --live
```

Visit `https://sirussofts.com/` in Chrome or Firefox and sign in at
`/admin/login`. Private files under `/app/`, `/bin/`, `/tests/`,
`/private/` must return 403 or 404 via **HTTPS**. Test .org later with
`--also-domain sirussofts.org` once that alias and TLS certificate exist.

**Do not publish other applications yet.** This first installation deploys
the base CMS and hosting stack. Independently runnable apps require a
Hestia-compatible per-app service/proxy installer, not the old standalone
Nginx vhost agent. The server remains yours to operate through PuTTY and
HestiaCP after initial setup.

## Reconnect and check installed files

After any PuTTY disconnect:

```bash
cd /root/SirusSofts
git rev-parse HEAD
ls -l deploy/ubuntu24
nginx -t
apache2ctl configtest
```

If any step fails, DO NOT delete the site or rerun Hestia over an existing
installation. Diagnose the exact failed step. Keep the existing Git
checkout, web-root files and private database unchanged.
