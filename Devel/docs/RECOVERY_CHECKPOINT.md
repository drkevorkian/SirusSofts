# SirusSofts development recovery checkpoint

## Current work: separate GentooNode app and Ubuntu Python work delegation (2026-11-08)

User clarified that Python is primarily for **persistent node communication,
job delegation and notifications**, NOT just maintaining an SSH tunnel.
`sirussofts.com` is the web authentication/job relay; a separate worker
executes approved jobs on Ubuntu. Gentoo must have its OWN application directory
in the main SirusSofts repo.

Implementation on `feature/cms-vhost-distributed-nodes`:
- `gentoo-node/`: standalone Python client with hardware telemetry,
  signed HTTPS heartbeat, `--delegate` and `--status`/`--wait`, OpenRC
  service, private key installer, README and tests.
- `ubuntu-worker/`: allowlisted Python worker, Hestia-compatible root
  installer, restricted sirusweb systemd service, README/tests.
- `CMS/app/work_broker.php` and `CMS/node-work.php`: HMAC-SHA256 signed,
  timestamp/nonce/replay-checked, per-node private SQLite mailbox.
  Idempotent requests; only health_snapshot/site_health jobs with no arguments.
  PHP never executes user-submitted programs or commands.
- `CMS/app/node_registry.php`, `CMS/app/operations.php`: signed CPU/GPU,
  memory/disk, temperature, uptime telemetry and admin work status display.
- `08-upgrade-cms-hestia.sh` can now add the new `node-work.php` entry
  safely to the existing CMS; preserves consistent SQLite backup/rollback.
- `sirus-source-browser.service` has corrected [Install] configuration
  and reviewed `10-install-source-browser.sh --fix-autostart` path.
- `CMS/app/core.php` version 0.5.0-alpha; CI includes PHP broker, Python
  worker/Gentoo client, and previous Hestia security tests.

**Operator's last verified state:** The previous `08 --apply` for v0.4 was
DECLINED because shell commands were pasted into the `UPGRADE CMS`
confirmation input. A subsequent `07 --live` passed for the existing site,
NOT for v0.4. The separate source browser service was started successfully
but had a static unit; the new fix addresses reboot persistence. Do not
assume that v0.4 or v0.5 website files are live. User must pull a TESTED new
commit, then type exactly `UPGRADE CMS` alone at the interactive prompt,
then run routing audit. Next install Ubuntu worker, securely enroll a unique
binary 32-byte key from Gentoo, then start the Gentoo OpenRC agent.

The legacy `node/` agent and optional SSH supervisor are not the
new dedicated app. Do not tell user to install the legacy node for this task.

---

## Live rollout update — .com is PRIMARY (2026-11-08)

Operator asked to use **sirussofts.com** now; defer `.org` completely.
The Hestia account `sirusweb` exists (WEB DOMAINS: 1), but prior attempts
to inspect `sirussofts.org` failed. We still MUST VERIFY with
`/usr/local/hestia/bin/v-list-web-domain sirusweb sirussofts.com`
before using the CMS installer. If missing, list the actual hosted domains
(`v-list-web-domains sirusweb`) and create .com without deleting unknown files.

The .com-first update is on `feature/cms-vhost-distributed-nodes`.
`06-deploy-cms-hestia.sh` now accepts either domain as the ONE primary
Hestia site; `07-audit-cms-routing.sh` tests the selected primary only,
with `--also-domain sirussofts.org` opt-in for a future configured
alias; the `CMS/app/core.php` default domain is .com. Documentation was
updated. GitHub Actions verification must pass on the LATEST commit
before the operator updates their pinned VPS checkout.

The actual VPS is still checked out in detached HEAD state at
`8b8bb1bb52ae76d415989b6e40b58437acc03a4d`. To get the fix AFTER
CI passes: `cd /root/SirusSofts; git status --short; git fetch origin feature/cms-vhost-distributed-nodes; git checkout NEW_PINNED_COMMIT`.
Then run the **read-only** CMS plan with `--domain sirussofts.com`.
Do not rerun the Hestia install, extension install, or firewall lockdown.
Do not deploy the CMS until plan is clean and .com TLS is ready or staged.

Live firewall: Hestia rule 2 is restricted to 127.0.0.1/32 TCP 8083;
rule 9 allows 80/443, rule 10 SSH 22. The `04-audit.sh --live` PASSED.
The redundant `05-lockdown-panel.sh --apply --rule 2` outputs
`REFUSED` correctly because the rule is no longer public. Avoid repeating.

---

## 2026-11-08 follow-up: executable PuTTY SSH installation candidate

The operator wants this to be runnable on the next session, including EXACT
steps to get the code physically onto the wiped Ubuntu 24.04 server.

**Canonical 1st-stop file:**
[PuTTY SSH install / public GitHub clone](../deploy/ubuntu24/PUTTY-SSH-QUICKSTART.md)

The SirusSofts GitHub repo is PUBLIC. PuTTY terminal commands:
- apt-get update; apt-get install -y ca-certificates curl git openssh-server
- git clone --single-branch --branch feature/cms-vhost-distributed-nodes
  https://github.com/drkevorkian/SirusSofts.git /root/SirusSofts
- cd /root/SirusSofts; git rev-parse HEAD
- bash deploy/ubuntu24/01-preflight.sh --strict
Then the official Hestia release script is downloaded and SHA-256 reviewed,
executed only after interactive confirmation, with PHP8.3/8.4,
Nginx+Apache+FPM+MariaDB+Hestia firewall+Fail2Ban. Post-reboot command
sequence, PuTTY SSH tunnel, Windows hosts mapping for panel certificate,
Hestia site creation, .org/.com aliases, Let's Encrypt, CMS initialization
and both-domain HTTPS audits are documented as literal copy/paste blocks.

**Critical bugs found and FIXED this round:**
1. Hestia's default homepage actually reads "Coming Soon" rather than
   "Plesk" or the previously anticipated default-page wording. The
   installer now compares the ENTIRE original Hestia skeleton index.html
   after replacing the %domain% token; it only backs up a verified clean
   default page. No arbitrary homepage overwrite.
2. PuTTY quickstart Markdown code blocks previously escaped fence tokens.
   Fixed to render executable copy/paste commands normally, with a CI test.
3. The PHP CLI CMS setup root account fallback used to depend on a
   Plesk httpdocs directory. Updated to determine the nonroot owner of
   the actual site directory, with root-owned checkout fail-closed.
   The Hestia installer still runs the setup as the unprivileged Hestia
   website user.
4. Replaced the CMS/README.md's obsolete Plesk deployment instructions with
   the actual Hestia path and per-site secure storage layout.
5. CI now includes deploy/tests/test_hestia_bootstrap.py, which tests the
   PuTTY clone / command coverage and exact default homepage comparison.

**Verification:** GitHub Actions pass on implementation commit
6fb10aa1c59fa3aaca683cbea67735dbf1c2cd5d:
https://github.com/drkevorkian/SirusSofts/actions/runs/42509785773

This checkpoint update itself creates a newer commit; verify its exact
latest SHA and CI result before the next coding session.
The actual IONOS Ubuntu server is NOT connected to a remote execution tool
and no SSH command has been run against it here. This is ready for
operator-run preflight / installation, not verified production uptime.

No cPoint, WorldCom, Sentinel Trader deployment or database migration was
attempted. The native Hestia app subdomain/directory deploy service is still
separate engineering work; use Hestia-owned web templates only. The old
standalone Nginx service is incompatible with Hestia and stays disabled.


---

**Operator-facing PuTTY quickstart:** [deploy/ubuntu24/PUTTY-SSH-QUICKSTART.md](../deploy/ubuntu24/PUTTY-SSH-QUICKSTART.md). The repository is PUBLIC: clone via HTTPS in PuTTY; no GitHub credentials are required to read it. The existing feature branch remains the deployable source until reviewed code is moved to main. This is an executable installer path, not a generic architecture proposal. Actual IONOS execution remains unverified until the operator performs the live audit.\n
## NEWER DEPLOYMENT STATE — 2026-11-08 CLEAN UBUNTU 24.04 (SUPERSEDES OLD NGiNX/PLesK PLAN)

The operator confirmed **the IONOS vServer has been WIPED and is now fresh
Ubuntu 24.04 LTS with NO Plesk**. This invalidates the previous assumption
that an existing cPoint/Plesk installation needs preserving on this VPS.
All changes since this clarification are in the GitHub staging branch.
**No VPS, DNS, SSL, firewall, Gentoo or project files were changed remotely.**

**Canonical clean-server deployment path:**
[deploy/ubuntu24/README.md](../deploy/ubuntu24/README.md)

Hosting panel selection: free, supported **HestiaCP** rather than paid cPanel.
Nginx is the public port 80/443 reverse proxy; Apache is managed backend
8080/8443; per-site PHP-FPM/FastCGI handles PHP 8.3/8.4; MariaDB and
private PDO_SQLITE coexist. Hestia owns Nginx virtual hosts, TLS and
iptables/Fail2Ban; the old independent Nginx vhost agent and Plesk installers
MUST NOT RUN alongside Hestia. UFW MUST NOT be enabled concurrently.
Do not enable "all" PHP/Apache modules; deploy the curated, tested set only.

Staged fresh-server scripts (all support read-only --check):
1. deploy/ubuntu24/01-preflight.sh: Ubuntu 24.04, arch, free/total disk
   headroom, RAM, no existing panel/servers, no UFW, free ports, SSH port22.
   Strict mode intended for real root console; CI only read-only --check.
2. deploy/ubuntu24/02-install-hestia.sh: two-phase REVIEWED official Hestia
   release installer download + printed SHA256, explicit --install with hash,
   interactive confirmation. Nginx+Apache+PHP-FPM 8.3,8.4+MariaDB+iptables+
   Fail2Ban, disables mail/DNS/FTP/webterminal/API.
   CRITICAL: installation is deliberately NOT run in CI or on VPS.
3. deploy/ubuntu24/03-install-extensions.sh: selected PHP and Apache modules,
   tests PHP extension existence and Nginx/Apache syntax after installing.
4. deploy/ubuntu24/04-audit.sh: nonmutating Hestia services and firewall
   readiness, additional rejection of ANY public ACCEPT TCP/8083 rule;
   see deploy/ubuntu24/firewall_rules.py and Python tests.
5. deploy/ubuntu24/05-lockdown-panel.sh: Hestia-native interactive firewall
   restriction of a verified public TCP/8083 ACCEPT rule to 127.0.0.1/32
   (SSH tunnel) or an explicitly narrow trusted CIDR; fails closed.
   Out-of-band IONOS console and a working SSH connection are required.
6. deploy/ubuntu24/06-deploy-cms-hestia.sh: guarded, opt-in CMS code deploy
   into the Hestia owned /home/sirusweb/web/sirussofts.org/public_html, with
   root-owned **BOTH** HTTP nginx.conf_siruscms AND HTTPS
   nginx.ssl.conf_siruscms security include fragments, preventing exposure of
   app/, bin/, tests/ and hidden storage. Blocks existing CMS/other homepage
   overwrites; initializes private SQLite/admin credentials as NON-root user.
   Hestia TLS is not issued by script; after SSL activation HTTPS guard
   inclusion must be independently verified.
7. deploy/ubuntu24/07-audit-cms-routing.sh: nonmutating REQUIRED launch gate:
   verify both custom includes actually loaded by Hestia Nginx, and make
   verified real HTTPS requests to BOTH sirussofts.org and sirussofts.com.
   Expected homepage/admin = HTTP200; private source = HTTP403/404.
   Cannot pass until public DNS, Hestia aliases and certificates are ready.
8. deploy/ubuntu24/sirus-cms-protect.nginx.conf: exact matches and ^~
   prefix locations that outrank Hestia's Nginx nested static file regexes;
   the prior single regex protection was weaker.
9. Root README.md/CMS/index.php comments updated to prefer this clean
   Ubuntu 24.04/Hestia track, not Plesk.

Verification:
- GitHub Actions full PHP/Python/Bash suite, including new script bash -n +
  read-only --check, pure firewall parsing/audit tests, and **disposable
  Nginx HTTP AND verified HTTPS requests** against independently loaded
  Nginx guards; tests cover nested CSS/JS static regex attempts to bypass
  server-level private-prefix protection.
- Latest VERIFIED IMPLEMENTATION commit before this doc update is
  3103218399a928499cea51cf5f708cbe00cc20ca; verify current CI run
  and branch HEAD because this checkpoint edit itself creates another commit.
- Historic Nginx private-path test initially failed due to a Python string
  literal typo and was fixed; do not rely on the earlier red test runs.
- **No actual live Hestia install/upgrade, in-place domain binding, public
  SSL ACME test or VPS firewall change has been performed.**

**Important deployment blockers**:
- Hestia must FIRST be installed and reviewed by operator (destructive
  service control, SSH/firewall risks). Initial guided CMS launch is
  materially closer to ready, but full Hestia integration is not proven
  until staging/real clean-server smoke and --live security gates pass.
- The previous **direct-Nginx** subdomain/path app routing is NOT a
  compatible Hestia-native app provisioning system. Build separate Hestia
  proxy templates and isolated HTTP app runtimes before announcing complete
  project subdomain/path hosting. No cPoint, WorldCom or Sentinel Trader
  installation or auto-publication.
- User must provide real host FQDN, admin email, SSH access, provider firewall
  settings and DNS from their own VPS environment. Do not store secrets
  or passwords in repo or installer CLI arguments.
- Encrypted backup and cross-database migration limitations from below
  remain. Main branch/PR #5 staging rule remains in force.

**Next step** after verifying CI and the current repository SHA:
(1) follow deploy/ubuntu24/README.md on fresh VPS through strict preflight,
reviewed Hestia installation and external firewall tests; (2) create
Hestia-managed sirussofts.org web domain and .com alias with valid TLS;
(3) run guarded CMS --plan and --deploy; (4) run
07-audit-cms-routing.sh --live and 04-audit.sh --live;
(5) keep unapproved independent apps disabled and begin Hestia-native
per-app routing integration design.

---

Updated: 2026-11-08 UTC.
Repository: https://github.com/drkevorkian/SirusSofts
Working branch: feature/cms-vhost-distributed-nodes
Open draft pull request: https://github.com/drkevorkian/SirusSofts/pull/5 (against main).
Latest successfully tested IMPLEMENTATION commit prior to this documentation update:
7737f83c8ff8c7b84ae5ef431bb5dfdef7e52344.
Latest green CI: https://github.com/drkevorkian/SirusSofts/actions/runs/42504411730

IMPORTANT: This checkpoint update creates a NEW branch commit. The actual
current HEAD is that documentation commit or later. Always fetch GitHub's
branch ref and PR/CI before making further changes. Do not trust a cached SHA.

## Authoritative status

The SirusSofts framework is a custom PHP CMS, not Laravel. GitHub contains
staged WORKING SOURCE with verified tests. **No code from this branch was
installed on the live IONOS VPS, Gentoo compute node or cPoint.** No production
websites, DNS, certs, databases or private GitHub backup repositories were
modified. PR #5 is DRAFT and not merged.

The existing CMS has a public homepage, admin authentication/settings, page
and project catalog, project draft/published status, dual .org/.com hostname
support, and History API asynchronous navigation with ordinary URL fallback.

### Completed source and tests (implementation, not deployment)

1. CMS/app/storage_registry.php: project storage driver, reference, schema
   version and primary node stored in private SQLite; never stores passwords.
2. CMS/app/storage_adapters.php: SQLite, MariaDB and MySQL PDO adapters, external
   secret resolution and remote MySQL TLS server-verification requirement.
   MySQL/MariaDB have not yet been live integration tested.
3. CMS/app/storage_backup.php: consistent SQLite VACUUM INTO snapshot (WAL
   inclusive), restrictive plaintext temp permissions, sodium secretstream
   XChaCha20-Poly1305 encryption, SHA-256 manifest and integrity-checked
   non-overwriting restore.
4. CMS/bin/storage-tool.php: operator-only backup, verify, restore, migration
   preflight, compare, publish-github and fetch-github CLI commands. Keys and
   tokens come from private files, never CLI arguments or the public PHP app.
5. CMS/app/storage_migration.php: SQLite preflight checks, schema/record
   fingerprints, source-target verification and durable journal. Automatic
   cross-engine conversion/activation is BLOCKED, source DB never deleted.
6. CMS/app/storage_github.php: **small encrypted snapshots only** (<=5 MiB)
   uploaded to a verified PRIVATE GitHub repository using the Contents API
   with locally authenticated backup validation. Manifest is uploaded LAST as
   completion marker. Encrypted archive and manifest can also be fetched into
   NEW local files, verified by sodium/authenticated hashes before exposure,
   without overwriting or activating a database. All GitHub API tests are
   simulated via injected transports; no real upload/download occurred.
   The actual CLI requires php8.3-curl (included in Ubuntu installer).
   Large backup storage and full disaster recovery remain outstanding.
7. CMS/app/node_registry.php + CMS/node-heartbeat.php: authenticated HMAC-SHA256
   hardware heartbeats, replay detection, offline health, stored assignments as
   metadata. Nothing executes jobs remotely.
8. node/: Gentoo Python outbound-only heartbeat agent, CPU/RAM/GPU vendor
   discovery, secure per-node key file, retry, OpenRC service and staged install,
   example WireGuard config. No real Gentoo server/tunnel test or installation.
9. CMS/app/github_projects.php and CMS admin /admin/github: read-only grouping
   of cached GitHub repo families/versions, independent publication metadata.
   Existing Python catalog remains the sync engine. No automatic publication.
10. CMS/app/operations.php and /admin/infrastructure: authenticated read-only
    compute node/status and database registry inventory; secrets omitted.
11. CMS/app/hosting_config.php + /admin/hosting: per-project planned server,
    database backend, GitHub repository/ref and manual approval/on-hold release
    policy in a private CMS table. All inputs validated and under admin/CSRF.
    Records are metadata only: saving does NOT migrate data, deploy, start a
    server, or publish a project. cPoint, WorldCom and Sentinel Trader remain
    protected from updates. CMS/bin/migrate-schema.php upgrades existing
    private CMS schema idempotently (included in installer).
12. deploy/nginx_vhost_agent.py: root-owned limited AF_UNIX command bridge
    with only preview/apply/rollback; no raw shell, templates, arbitrary paths,
    hostnames or upstream URLs from PHP. Supports BOTH .org/.com and /project/
    Nginx routes for independently running HTTP apps on explicitly approved
    healthy **127.0.0.1** ports listed in private root-managed
    /etc/sirussofts/app-routes.json. Directory requests strip /project/ prefix;
    app must honor X-Forwarded-Prefix for assets/cookies/redirects.
    The same agent manages CMS page subdomains and rollback.
    Refuses cPoint, WorldCom, Sentinel Trader and reserved names.
    Certificate coverage, expiry, OS-trusted chain, matching private key,
    private-key mode, Nginx syntax and managed-file digests are checked.
    No application is enabled in the repository. Separate app service setup is
    not yet implemented; the agent does NOT deploy app code.
13. deploy/nginx-sirussofts.conf: root .org/.com HTTP vhost with a managed
    directory route include. Root-domain HTTPS is not automatically provisioned.
14. deploy/install-framework-ubuntu.sh: opt-in repeatable Ubuntu 24.04
    Nginx/PHP-FPM/SQLite installer for the CMS, dedicated non-root PHP account,
    private CMS state, CLI schema migration and manual TLS operator step.
    deploy/install-nginx-vhost-agent.sh installs the root-owned agent but
    does NOT start/enable it. No installer ran on production.
15. docs/NGINX_VHOST_AGENT.md (under deploy/) and other implementation
    documentation detail the staging model.

### Tests actually executed (not merely written)

The most recent successful GitHub Actions run:
https://github.com/drkevorkian/SirusSofts/actions/runs/42504411730
Verified implementation revision:
7737f83c8ff8c7b84ae5ef431bb5dfdef7e52344

Commands/checks include:
- PHP lint on all CMS/*.php files, original CMS/registry smoke.
- SQLite adapters, WAL encrypted backup/restore/tamper, migration integrity and
  journal transitions, CLI subprocess backup/verify/restore/compare.
- Authenticated node registry and admin infrastructure page tests.
- GitHub family/version grouping and read-only admin rendering tests.
- Hosting metadata tests for authorization, input validation, persistence,
  draft privacy and protected slugs.
- GitHub encrypted upload/download simulated private API tests: refusal of
  public repo, incorrect key, insecure token, existing targets; archive-first
  manifest-last upload, authenticated staged fetch. No external GitHub API call.
- Gentoo Python unit tests, restricted Nginx vhost preview/apply/rollback tests,
  malformed app backend config/tamper refusal, TLS chain/key validation units.
- **Real Nginx -t** on disposable generated main site, CMS page and app vhosts.
- **Actual disposable Nginx HTTP and TLS request tests**. A separate loopback
  test backend received requests via both main root domains /test-project/
  and both secure project subdomains. Prefix stripping and forwarded headers
  and TLS hostname validation were asserted. Does NOT test production TLS/DNS.
- JavaScript syntax, Bash syntax, Ubuntu/Nginx agent installer --check. No
  full Ubuntu install run; no browser E2E for History API.

Historical failed CI runs were diagnosed and corrected:
- PDO journal transaction tracking mismatch.
- Test double-include / too-specific layout assertion.
- GitHub family capitalization assertion.
- Nginx parser test runner lacked permissions for default logs/ports.
Latest run is GREEN. Local PHP here lacks pdo_sqlite; GitHub Ubuntu CI has it
and actually executed storage integration tests.

## Open engineering tasks / known limitations

- **No production deployment**: Main GitHub branch, IONOS VPS, Gentoo,
  certificates, DNS and cPoint remain unchanged.
- The legacy deploy/vhost_agent.py and deploy/install-vhost-agent.sh are
  Plesk-only OLD staging code: DO NOT RUN on the Nginx Ubuntu target.
- App upstreams are separately deployed HTTP runtimes. No managed creation of
  those service units/ports/isolated credentials yet. No automatic cPoint
  conversion. Do not enable a route until upstream base-path safety and
  authorization/cookies have been reviewed on staging.
- Admin hosting settings are intended metadata only; the root service reads
  a separate root-owned allowlist. No safe publish/deploy coupling yet.
- Existing default root Nginx configuration is HTTP-only. Provision DNS and
  production root/wildcard TLS separately; test cert renewal, firewall and
  private key access without exposing source or data.
- Nginx agent has real disposable HTTP/TLS route tests but has NOT been
  installed under its actual systemd sandbox/socket permissions.
- MySQL and MariaDB real connections, logical cross-engine import/export,
  preflight/cutover, rollback and restore-drill integration unfinished.
- Github backups allow small encrypted snapshots only and have mocked API
  tests. No external private backup repo operations, scheduled retention,
  large-object store, or one-command whole-site disaster recovery.
- No authenticated remote workload executor, idempotent job acknowledgements,
  node quotas or WireGuard/SSH tunnel installation.
- GitHub version family selection is read-only; no approved release-selection
  and deploy wiring. Do not automatically publish versions.
- Existing CMS/index.php and setup comments contain historical Plesk wording;
  retire obsolete documentation in the next cleanup.
- Browser History API behavior has JS syntax and PHP routing tests, but no
  real automated browser Back/Forward E2E tests yet.

## Exact next implementation step

1. Inspect actual GitHub HEAD, PR #5 (keep DRAFT), latest CI job and current
   files. Verify no concurrent changes before writing commits.
2. Build a **restricted per-app loopback HTTP runtime installer** supporting
   independent service identities, source/version allowlisting, PHP-FPM or
   isolated HTTP listeners, port ownership and prefix-aware redirects.
   Never deploy cPoint, WorldCom or Sentinel Trader automatically.
3. Extend CMS hosting management to show a preview of the root-owned backend
   allowlist vs planned metadata; add permission/authorization tests. Never let
   the PHP user edit root-owned Nginx files or arbitrary service commands.
4. Add real browser navigation tests and disposable Ubuntu 24.04 installer
   provision/run/rollback tests before production review.
5. Add MySQL/MariaDB live tests, logical export/import and cutover consistency
   gates. Verify source remains intact and rollback is demonstrably safe.
6. Add a staged PRIVATE GitHub backup API integration with an intentionally
   designated test repo only after operator approval; test restore drill.
7. Add WireGuard test and authenticated job dispatch when resource isolation
   and replay/acknowledgement semantics are defined.
8. Commit each tested implementation on feature/cms-vhost-distributed-nodes,
   update this checkpoint and PR #5. Keep both in staging until explicitly
   authorized for deployment or merging.

Invariants: no Laravel, no arbitrary root PHP execution, no tokens/keys/
plaintext databases in Git, no active SQLite file copying as replication,
no deletion of original databases after migration, no automatic deployment of
cPoint, WorldCom or Sentinel Trader, no live VPS changes without approval.
