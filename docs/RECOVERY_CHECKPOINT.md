# SirusSofts development recovery checkpoint

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
