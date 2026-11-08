# SirusSofts engineering recovery checkpoint

Checkpoint date: 2026-11-08 UTC.
Authoritative repository: https://github.com/drkevorkian/SirusSofts
Development branch: feature/cms-vhost-distributed-nodes
Draft pull request: https://github.com/drkevorkian/SirusSofts/pull/5
Target base: main.
Latest verified implementation commit before this checkpoint:
c04ad0483e25e9f921cb04f3ae2a8b6cc43cf3f0.
The checkpoint commit itself changes HEAD. Future sessions MUST fetch branch HEAD live
and inspect recent commits and PR before proceeding.

## VERIFIED - source and tests committed

1. Existing custom PHP CMS public homepage, project catalog, page editing,
   session-based admin/settings and publication controls are present; inherited
   from earlier merged PRs. This branch preserves them.
2. CMS/app/storage_registry.php: per-project driver, node, connection reference,
   schema version and migration state registry outside public document root.
3. CMS/app/storage_adapters.php: SQLite PDO and MySQL/MariaDB PDO adapters.
   Remote MySQL requires configured CA certificate and server validation;
   passwords come from trusted external secret resolver, never Git.
4. CMS/app/storage_backup.php: SQLite VACUUM INTO consistent snapshot, including
   WAL data; restrictive umask on temporary plaintext; streaming sodium
   secretstream-xchacha20poly1305 encryption; plaintext and ciphertext SHA-256
   hashes in manifest, authenticated verification and validated non-destructive
   restore that will not overwrite original DB.
5. CMS/bin/storage-tool.php: independent offline PHP CLI commands backup,
   verify, restore, preflight, compare. Encryption key is read from a root-
   controlled private binary key file (mode 0400/0600), NOT a CLI parameter.
   CMS/tests/storage_cli_test.php executes real subprocess integration tests.
6. CMS/app/storage_migration.php: read-only SQLite integrity and foreign key
   preflight, schema/row fingerprints and source/target verification; persistent
   state transition journal. Cross-engine automatic migration is BLOCKED.
   Activation only records journal metadata and does not switch a live database.
7. CMS/app/node_registry.php and CMS/node-heartbeat.php: per-node HMAC-SHA256
   signatures, nonce replay rejection, timestamp freshness, node inventory,
   online/offline health and queued workload metadata. No arbitrary remote
   commands or job execution. Heartbeat endpoint remains disabled until
   private environment configuration is explicitly provided.
8. node/: standalone Python Gentoo/OpenRC outbound-only heartbeat agent
   (CPU, memory, GPU vendor discovery), HTTPS verification, retry on outages,
   manual idempotent installer, WireGuard peer example, unit tests. No tunnel
   or service enabled anywhere by this work.
9. CMS/app/core.php: both .org and .com canonical root/subdomain recognition;
   dedicated private CMS state env override. CMS/assets/cms.js: History API
   async content navigation, Back/Forward and full-page fallback.
   CMS/bin/setup.php: clean initialization no longer auto-publishes cPoint.
10. CMS/app/vhosts.php and CMS/app/admin.php: admin UI connects to limited
    AF_UNIX Nginx vhost service, preview/apply/rollback, operator text confirmation
    and both domain routes. Page-type CMS projects only; app-type intentionally
    blocked. Existing Plesk files remain in repo but must NOT be used.
11. deploy/nginx_vhost_agent.py: root-owned fixed-operation Nginx page-vhost
    agent, dual-domain certificate name+expiry checks, fixed Nginx test/reload,
    managed config integrity, rollback, and tests. It writes nginx site files
    into sites-enabled and registry metadata into a private directory.
12. deploy/install-nginx-vhost-agent.sh: separate opt-in installer for the
    restricted agent. It installs unit files but DOES NOT enable/start service.
13. deploy/install-framework-ubuntu.sh, nginx-sirussofts.conf and PHP-FPM pool:
    Ubuntu 24.04 custom-PHP-only website installer, idempotent release paths,
    private SQLite state, non-root PHP, no Docker/Plesk/Laravel. Manual DNS and
    HTTPS provisioning required. No live execution performed.
14. CMS/app/operations.php and CMS admin /admin/infrastructure:
    authenticated read-only storage/backend inventory and node heartbeat
    telemetry; connection references and HMAC keys excluded from HTML.
    Missing registries are reported as not configured.

15. CMS/app/github_projects.php and CMS admin /admin/github:
    read-only integration with the standalone Python GitHub catalog SQLite
    cache; automatic repository version-family grouping matches Python naming
    conventions (Gentoo Installer original/2/3, AI Bridge original/2), with
    independent per-version visibility/hosting metadata. PHP does not need
    GitHub API tokens and cannot publish, clone or deploy from this screen.
    CMS/tests/github_projects_test.php verifies grouping, private catalog
    access control, read-only data and admin rendering. Catalog sync still
    runs separately in Python and must be configured by the administrator.

## Tests genuinely executed

GitHub Actions workflow: .github/workflows/php-framework.yml
LATEST GREEN test run:
https://github.com/drkevorkian/SirusSofts/actions/runs/42502171831
Commit tested: c04ad0483e25e9f921cb04f3ae2a8b6cc43cf3f0
Results: SUCCESS.

Included:
- PHP syntax for entire CMS tree.
- Original registry persistence and CMS routing/privacy smoke tests.
- SQLite adapter factory/validation and integration tests.
- WAL-consistent encrypted snapshot, restore, checksum, wrong-key, tamper and
  non-overwrite tests.
- SQLite preflight, fingerprint, target verification and migration journal.
- Signed node registry replay rejection, offline detection, queued jobs and
  redacted telemetry listing.
- PHP storage-tool CLI end-to-end subprocess tests (backup, verify, restore,
  preflight, compare, insecure key refusal).
- Protected CMS operations dashboard rendering tests, unauthorized access
  rejection and sensitive-data output checks.
- PHP GitHub repository family/version grouping and private cached catalog
  rendering tests, including denial of public catalog access.
- Python Gentoo agent unit tests.
- Python Nginx agent preview/apply/rollback/tamper/TLS refusal tests.
- JavaScript syntax, Bash syntax, Ubuntu and Nginx agent installers' read-only
  --check preflight.

Earlier RED runs were investigated and fixed:
- PDO journal transaction mismatch fixed in 4c7d29eb.
- Duplicate operations test include fixed in ff03a007.
- Incorrect test assertion assuming literal domain in HTML fixed in b8d6df7.
A GitHub catalog test initially expected AI instead of Python-compatible Ai
capitalization; the corrected assertion passed in c04ad048.
The local container lacks pdo_sqlite, so its original registry test EXITED 2
there; the fully supported Ubuntu 24.04 GitHub Actions runner DID execute it.

## Known missing features and real blockers

- Nothing was deployed to live IONOS VPS, Gentoo, DNS, TLS or cPoint.
- This is an interim framework, not a production-ready CMS/distributed cluster.
- Existing deploy/vhost_agent.py and install-vhost-agent.sh are Plesk-specific
  obsolete staging sources; do not install. Nginx replacement only serves CMS
  PAGE subdomains; independent executable apps are NOT routed.
- Nginx vhost agent was only unit-tested with mocked command execution.
  There is no disposable Ubuntu VM Nginx/TLS end-to-end test.
- No actual DNS/TLS provisioning in bootstrap; admin requires HTTPS.
  Test wildcard certificate name/expiry handling, chain/key matching,
  socket ownership and systemd sandbox before deployment.
- CMS host editing currently includes project slug/kind/visibility/preview but
  NOT one unified editable assigned node, database backend, deploy config UI.
  Infrastructure page is read-only, not a full management screen.
- PHP admin now reads and groups the existing Python GitHub catalog in a
  read-only view. Python sync must still be configured and run; no automatic
  private discovery or deployment runs on the CMS itself.
- Storage CLI supports SQLite-native portable encrypted snapshots, not
  cross-database logical export/import. MySQL and MariaDB live integration
  tests, schema conversion, verified DB cutover, restore drills and
  automated source/target reconciliation are still unimplemented.
- No private GitHub backup upload, encrypted retention policy, large backup
  storage connector or one-command full disaster-recovery replay.
- Node workloads are metadata only: no signed job protocol, quotas,
  idempotent execution, reconnect resume, encrypted WireGuard setup service,
  failover or actual remote compute dispatch.
- Unreviewed third-party projects WorldCom and Sentinel Trader are explicitly
  excluded from auto deployment. cPoint Monitor is independent, untouched.
- PHP web process must NEVER gain unrestricted root shell or password access.

## Exact continuation instructions

1. Fetch current repo HEAD, branch/PR #5 status, latest commit contents, and CI
   to avoid overwriting concurrent work. Keep PR #5 DRAFT.
2. Run existing complete CI suite locally where PDO_SQLITE is available or
   use the minimal PR GitHub Actions workflow. Add disposable Ubuntu 24.04 VM
   Nginx vhost integration tests; validate all supported domains, TLS
   certificate hostname/key/chain, AF_UNIX permissions, apply and rollback.
3. Implement safe separately-deployed app route support for both subdomain
   and root directory URL (NO automatic cPoint/WorldCom/Sentinel Trader deploy).
   Extend CMS admin hosting editor to include assigned node, database backend
   and deployment configuration, with security/CSRF guards.
4. Extend the integrated read-only PHP version catalog to include an explicitly
   selected deployment version per project, without publishing automatically.
   Connect the separate Python catalog sync service only after a staging
   permissions and token review.
5. Add MariaDB and MySQL real integration and portable import/export,
   database migration cutover gate and non-destructive rollback tests.
6. Implement encrypted private GitHub backup destination with local verification
   and strict backup size/retention constraints; no keys in Git.
7. Complete signed node workloads and reconnect semantics, then operator
   staged WireGuard/Gentoo-to-Ubuntu test.
8. After each real implementation slice: execute tests, inspect red CI logs,
   fix failures, commit on feature/cms-vhost-distributed-nodes, update
   this checkpoint with latest verified head, tests and next step. Do not
   deploy or merge before explicit operator approval and security review.

Security invariants: no secrets or plaintext private database contents in Git,
no live SQLite-file copy as replication, no source DB deletion after migration,
no arbitrary root execution from the public PHP CMS.
