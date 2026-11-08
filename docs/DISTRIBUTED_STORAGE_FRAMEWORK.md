# SirusSofts distributed storage framework — implementation specification

Status: design checkpoint, not deployed. Custom PHP CMS; Ubuntu VPS (Plesk optional); Gentoo OpenRC compute node. WorldCom and Sentinel Trader are excluded from automatic deployment.

## Per-project storage registry
Store project_id, driver (sqlite|mariadb|mysql), connection reference (not password), schema_version, primary_node, backup_policy, retention, and migration state. Keep credentials outside webroot in root-readable files or a secrets store. Do not place live databases or secrets in Git.

## Portable backup manifest
Every archive includes manifest.json with format_version, project_id, source_driver, schema_version, created_utc, snapshot_consistency_method, files with SHA-256 hashes, encryption metadata, and application commit SHA. Database data is backed up via SQLite backup API or database-native consistent dump. Encrypt locally before off-site upload; verify checksums and perform periodic restore tests. GitHub private repositories may hold small encrypted archives and manifests; use object storage for larger/frequent backups. Never rely on GitHub as the only copy.

## Migration workflow
1. Discover source and target drivers; run compatibility preflight (SQL dialect, types, collations, indexes, foreign keys, triggers).
2. Create verified consistent backup and provision target.
3. Convert schema and transfer records in batches; log counts and checksums.
4. Pause writes or use a verified change-capture strategy for final cutover.
5. Verify application-level reads/writes and schema; explicitly approve activation.
6. Switch connection reference atomically; retain original database for rollback. Do not automatically delete original.
No raw live SQLite file copying between servers.

## Distributed node agent
Registry fields: node_id, OS, init (OpenRC|systemd), CPU threads, RAM, GPU inventory, supported runtimes, health, last_seen, tunnel endpoint, and workload capabilities. Colorado Gentoo establishes outbound encrypted connectivity to public Ubuntu VPS behind Starlink CGNAT. WireGuard preferred; SSH reverse tunnel fallback. Use authenticated heartbeats, signed jobs, per-node quotas and idempotent job IDs. Public VPS handles HTTPS and routing; compute jobs queue for Gentoo. A tunnel alone is not replication or failover.

## Deployment and administration
Single CMS admin screen: project, subdomain, path, assigned node, database engine, backup destination, dry-run, apply and rollback. Plesk adapter and plain Nginx adapter must not overwrite each other's config. A restricted root-owned deployer validates requested changes; PHP web process never runs unrestricted shell commands. Navigation uses History API and asynchronous content loading, with working direct URLs and browser back/forward.

## Recovery
Fresh Ubuntu install -> fetch pinned trusted installer -> verify release checksum/signature -> install minimal runtime -> authenticate to backup destination -> decrypt -> validate manifest -> restore CMS and projects -> test routes and DB -> enable traffic. Never commit decryption keys or credentials to GitHub.

## Next implementation slices
1. Storage registry schema and PHP driver interfaces with tests.
2. Snapshot/export and manifest/checksum tests.
3. Restore and migration preflight with rollback tests.
4. Gentoo OpenRC node service and Ubuntu systemd coordinator.
5. Admin UI and safe vhost deployer, then end-to-end staging tests.

Current state: specification only. No live server changes; do not mark these features complete until tested.
