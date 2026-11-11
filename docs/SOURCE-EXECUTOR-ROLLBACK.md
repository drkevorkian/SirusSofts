# Guarded SirusSofts source deployment and rollback

This is **stage 2 of the Release Planner**. It introduces an independent,
root-console release executor at `deploy/release/source_executor.py`.

**No automatic web-triggered installation is enabled**. A browser request,
GitHub push, or release approval alone cannot invoke this executor. It does
not use GitHub credentials, write to the CMS database, process shell text,
grant sudo to PHP, or install operating-system packages.

## Bounded targets

| Host | Exact supported source | Fixed installed target | Controlled restart |
| --- | --- | --- | --- |
| Ubuntu | `ubuntu-worker/worker.py` | `/usr/local/libexec/sirus-ubuntu-worker.py` | `systemctl restart sirus-ubuntu-worker.service` |
| Ubuntu | `realtime/server.py` | `/usr/local/libexec/sirus-live-socket.py` | `systemctl restart sirus-live-socket.service` |
| Gentoo | `gentoo-node/client.py` | `/opt/sirus-gentoo-node/client.py` | `rc-service sirus-gentoo-node restart` |
| Gentoo | `gentoo-node/dashboard.py` | `/opt/sirus-gentoo-node/dashboard.py` | `rc-service sirus-gentoo-dashboard restart` |

The full CMS/website must still use the existing backed-up
`deploy/ubuntu24/08-upgrade-cms-hestia.sh` deployment. Changing one
`CMS/app/*.php` file at a time is **not a coherent CMS release**.
The Gentoo SSH tunnel also remains out of scope to prevent losing
the private compute node while deploying.

## Required inputs

An administrator must first select a revision, create a plan and
explicitly approve it in `/admin/updates`. The GitHub source indexer
downloads the approved *immutable* commit's source file and verifies
its SHA-256 before staging it outside the website root.

For Ubuntu, the CLI independently verifies the local CMS SQLite
`cms_source_release_plans` row is still approved. The release
must be recent, the manifest must be allowlisted, and the live
installed file must still match the approved baseline digest.

For Gentoo, the existing HMAC heartbeat reports installed code
digests, but **remote signed transfer and web-triggered application are
not built**. To use the CLI, the administrator must securely transfer
the approved file and matching manifest into
`/var/lib/sirusnode/source_artifacts/` *outside the web root*.
Only a local root console can run the executor on Gentoo. The CLI
does not claim to validate a remote Ubuntu approval on its own.

## Console commands (only after code review, PR merge, and staging)

Read-only syntax preflight, from the reviewed source checkout on either host:

```sh
python3 deploy/release/source_executor.py --check
```

Ubuntu plan and apply, with the precise 32-character plan ID and commit
short hash supplied by the authenticated Release Planner:

```sh
sudo python3 deploy/release/source_executor.py --host ubuntu-primary --id PLAN_ID --plan
sudo python3 deploy/release/source_executor.py --host ubuntu-primary --id PLAN_ID --apply --confirm "DEPLOY SHORT_COMMIT"
```

Gentoo manual console usage, after explicit operator transfer of the
staged artifact and manifest:

```sh
sudo python3 deploy/release/source_executor.py --host gentoo-home --id PLAN_ID --plan
sudo python3 deploy/release/source_executor.py --host gentoo-home --id PLAN_ID --apply --confirm "DEPLOY SHORT_COMMIT"
```

Rollback is independently available even if the approved plan expires
or the original staged artifact is gone. Root-owned backups are stored
under `/var/lib/sirussofts/source-releases/<plan-id>/`:

```sh
sudo python3 deploy/release/source_executor.py --host ubuntu-primary --id PLAN_ID --rollback --confirm "ROLLBACK SHORT_COMMIT"
```

Use `--host gentoo-home` on the Gentoo machine for Gentoo rollback.
Never run the Ubuntu host mode on Gentoo or vice versa.

## Safety and recovery behavior

1. The executable checks the host OS, exact component allowlist, source
   and installed SHA-256, staging age, installed service health,
   file types and 512 MiB of free destination disk space.
2. It takes a non-blocking process lock; only one local deployment may
   run at once.
3. It creates a root-owned 0700 recovery directory with a 0600
   backup and a **second root-owned copy of the approved staged source**.
   This prevents a PHP-account race after the replacement begins.
4. Python syntax is compiled before any live file changes. The source
   file is atomically replaced on the same filesystem, preserving
   the original owner, group and mode.
5. Only the known service is restarted. A failing service health check
   triggers rollback of the source file and an attempted service
   restart. The private journal retains deployment/rollback state.
6. A manual rollback refuses to overwrite a file that has drifted
   since deployment. Escalate to operator recovery in this case.

Do not erase recovery directories or manually modify a running file
after approval and before application. Do not point a sudo rule or an
HTTP route at the generic Python interpreter or this CLI.

## Current deployment readiness

The executor and its tests can be reviewed and staged, but **neither
machine has been modified by repository commits**. Production requires
an administrator to deploy and validate the updated code, ensure
approved artifacts have been staged, and execute the guarded CLI.
The next control-plane milestone is a separate audited, host-specific,
root-owned queue service that consumes constrained apply requests,
publishes progress over WebSockets and arranges authenticated Gentoo
artifact transfer. Those endpoints are intentionally not enabled yet.
