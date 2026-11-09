# SirusSofts — Main website and CMS

**Live VPS:** Ubuntu 24.04 / HestiaCP hosts `sirussofts.com` as `sirusweb`.
The main CMS, GitHub importer and private source-browser daemon have been
installed and independently checked by the operator. The most recent v0.4 CMS
upgrade was **declined at its interactive prompt**, so the user-management UI
and newer node features are not yet deployed. Changes in this GitHub feature
branch must still be reviewed, tested and installed separately.

**New development architecture (v0.5.0-alpha, feature branch only):**
[`gentoo-node/`](gentoo-node/README.md) is the standalone outbound Python
Gentoo/OpenRC client; [`ubuntu-worker/`](ubuntu-worker/README.md) is the
independent restricted background executor. `CMS/node-work.php` is an
authenticated broker, never an executor. No SSH tunnel is required for normal
telemetry or task delegation. The first allowed Ubuntu jobs are
`health_snapshot` and `site_health`; no arbitrary commands are accepted.

**Start here in PuTTY on Windows:** [Step-by-step SSH installation and public GitHub clone](deploy/ubuntu24/PUTTY-SSH-QUICKSTART.md).
The detailed technical reference is [Clean Ubuntu 24.04 + HestiaCP deployment runbook](deploy/ubuntu24/README.md). Hestia owns Nginx,
Apache, PHP-FPM and firewall rules. Never run the older direct Nginx/Plesk
installers on this Hestia target. cPoint is NOT installed by the CMS setup.

The SirusSofts repository owns the public website and CMS. The cPoint Monitor application remains a separate project in `drkevorkian/cPoint-monitor`.

## Repository ownership

- **`CMS/`** — main SirusSofts website: `CMS/index.php` is the homepage/front controller; `CMS/app/admin.php` is the existing admin implementation; `CMS/assets/` contains site assets.
- **`deploy/`** — webhook and release-management infrastructure, NOT the website document root.
- **`internal/`** — read-only diagnostic agent.
- **`cPoint-monitor/deploy/web/`** (different repository) — cPoint dashboard and PHP API, NOT the SirusSofts homepage.

## Planned routing

- `sirussofts.com` and `sirussofts.org`: SirusSofts CMS home and project directory.
- `cpoint.sirussofts.com`: separate cPoint app deployment (independent code, runtime, and database).
- `sirussofts.com/cpoint`: optional reverse proxy or configured path alias to the SAME independent cPoint app, after API and session compatibility tests.

CMS visibility and the right to publish a project's source are separate decisions. The GitHub catalog must not automatically expose private repository contents as web files.

## No reinstallation on every update

The intended flow is a one-time server bootstrap, then signed GitHub webhook events that trigger a validated release and atomic document-root switch. PHP/static updates do not normally require a machine reboot. **An automated safe self-updater for the deployment agent itself is not yet implemented**; it requires release validation and restart of only its service when necessary.

## Historical pre-wipe transition plan (not for the new clean server)

1. Keep the live cPoint instance running; back up the live document root and take a consistent snapshot of the cPoint SQLite database (including WAL state).
2. Stage and test the cPoint application under its own independent document root and verify health, authentication, and Android client synchronization.
3. **Compatibility note:** `CMS/api.php` is currently a bridge to `cpoint/api.php`. Keep the root API route functioning during migration, then switch it to the dedicated app without breaking Android clients.
4. Point the main domain to the contents of `CMS/`, with correct PHP-FPM/nginx or Apache handling. Confirm `/admin`, homepage, settings, and project links.
5. Verify both subdomain and directory routes if both are enabled. Test rollback before taking the change live.
6. Only after cutover, remove the old `CMS/` directory from cPoint-monitor in a separate reviewed change. That repository must remain dedicated to cPoint.

On the wiped Ubuntu 24.04 server, neither cPoint nor the legacy Plesk-hosted CMS is assumed present. Follow the new Hestia runbook instead. The prior notes above describe history, not current server state.

**Integration note:** the protected CMS admin hosting editor and independent Nginx agent are staged, but that Nginx agent is NOT compatible with Hestia-owned virtual hosts. Hestia-native per-app proxy wiring remains an explicit future deployment gate; no project is automatically published.
