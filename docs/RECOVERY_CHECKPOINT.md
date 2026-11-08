# SirusSofts engineering recovery checkpoint

Updated: 2026-11-08 UTC. Re-read GitHub live before relying on this snapshot.

## Repository and deployment state
- Repo: https://github.com/drkevorkian/SirusSofts
- Development branch: feature/cms-vhost-distributed-nodes
- Draft PR #5: https://github.com/drkevorkian/SirusSofts/pull/5, base main.
- At checkpoint creation the preceding branch head was 68dd711fd0d1d9b79e6a835a1e1e15f3ef03e70e. The current head is this checkpoint commit or a later commit; always fetch branch live.
- NOT deployed. No changes were made to live IONOS, Gentoo, DNS, TLS, cPoint, WorldCom or Sentinel Trader.

## Work delivered so far
1. CMS/app/storage_registry.php — initial per-project SQLite registry.
2. CMS/app/storage_adapters.php — SQLite plus MySQL and MariaDB adapters, using external secret references and remote TLS validation.
3. CMS/app/storage_backup.php — WAL-consistent SQLite VACUUM INTO snapshots; streaming sodium secretstream encryption; SHA-256 manifests; verified non-destructive restore.
4. CMS/app/storage_migration.php — read-only SQLite preflight/schema/data fingerprints, migration verification and durable state journal. Cross-driver automatic migration is still blocked.
5. CMS/app/node_registry.php and CMS/node-heartbeat.php — authenticated per-node HTTPS heartbeats, anti-replay nonces, node hardware/health records and manually queued workload metadata.
6. node/ — Python outbound Gentoo agent, hardware discovery, OpenRC definition, repeatable manual installer and WireGuard example. No job execution.
7. CMS/app/core.php + CMS/assets/cms.js — primary .org and secondary .com host support, History API asynchronous navigation with full-page fallback, stable private storage location override.
8. CMS/bin/setup.php — no automatically published cPoint project on a clean CMS install.
9. deploy/install-framework-ubuntu.sh, nginx-sirussofts.conf and php-sirussofts.pool.conf — an Ubuntu 24.04-only Nginx/PHP-FPM/SQLite installer with --check mode, private data and explicit TLS follow-up. Not actually installed on VPS.

## Tests executed, not merely written
- Local container: PHP syntax checks of original registry and test passed. Original registry test exited 2 because this local PHP lacks pdo_sqlite.
- GitHub Actions: https://github.com/drkevorkian/SirusSofts/actions/runs/42500271191 — SUCCESS.
- The green run covers PHP lint, existing registry smoke, CMS smoke, new SQLite storage adapter integration, WAL backup/restore/tamper, migration and state journal, signed node registry/replay tests, Python Gentoo agent tests, JS syntax, bash syntax and Ubuntu installer --check.
- Earlier red run exposed a PDO transaction mismatch in migration journal, corrected by commit 4c7d29eb165607952d6e75d997246419a2e2e032, followed by successful CI.
- Not tested: full browser navigation, MySQL live connections, nginx runtime provisioning, DNS/TLS, real WireGuard networking, Ubuntu installer actual execution or deployment.

## Open engineering issues
- CMS/app/vhosts.php and deploy/vhost_agent.py + deploy/install-vhost-agent.sh still assume Plesk. DO NOT run Plesk services on the target; implement restricted Nginx vhost preview/apply/rollback, full admin screen and dual-domain certificate validation.
- Ubuntu installer produces an HTTP vhost; valid HTTPS and DNS remain an operator-controlled task.
- Cross-engine migration and cutover, portable import/export, backup retention, GitHub private encrypted archive uploads, and one-command disaster recovery are incomplete.
- Migration journal is independent metadata; it does not automatically switch project DB references. Original DB must never be deleted.
- Node workload assignment is data only. Signed dispatch/execution, quotas, heartbeat dashboard, tunnel provisioning and failover are outstanding.
- PHP CMS GitHub catalog version grouping integration is outstanding; standalone Python catalog already groups family versions.
- CMS/api.php has a cPoint compatibility shim, but cPoint remains independent and out of scope.
- WorldCom and Sentinel Trader must never be automatically deployed.

## Exact next continuation task
1. Inspect live GitHub head/PR/CI and verify the repository tree, rather than assuming this checkpoint is current.
2. Replace Plesk vhost pathways with a restricted, testable Nginx vhost agent. It must support both domain roots, preview, safe apply and rollback and reject missing valid certificates before activating project subdomains. Do not touch production.
3. Implement portable SQLite export/import and migration cutover verification with non-destructive rollback tests.
4. Expose storage/node health in authorized CMS admin screens, and add real browser and disposable-VM deployment tests.
5. Commit working changes to feature/cms-vhost-distributed-nodes, keep PR #5 draft, re-run tests, update this file with actual new commit SHA and results.

Never commit keys, credentials, plaintext database contents, or private production data.
