# SirusSofts controlled source deployments and rollback

This release adds a **fixed-task Ubuntu installation queue** to the existing
authenticated Release Planner at `/admin/updates`. It also includes a
guarded **local console executor** for Ubuntu and Gentoo.

Important: the website does not get a shell, sudo, or root credentials.
The privileged root-owned Ubuntu controller is a separate systemd service,
reads a bounded queue from the private CMS database, validates an already
password-confirmed deployment request, and independently enforces fixed
source-file destinations and SHA-256 hashes. A GitHub push or source update
check can **never** automatically install code.

## SQLite privilege boundary

The Hestia website owns the CMS SQLite database and its WAL/SHM files.
To preserve ownership, the root-owned source controller **never connects
to the CMS database with a writable SQLite handle**. Instead it calls the
fixed, root-owned `db_queue.py` helper with subprocess credentials dropped
to the existing `sirusweb` UID/GID. Queue claims, controller heartbeats,
completion records, and interrupted-request recovery occur as `sirusweb`.

Only the separate root controller replaces allowlisted installed Python
service files and manages root-private backups. This distinction prevents a
deployment controller from creating root-owned SQLite journal files.
The SQLite approval checker used by the release executor opens the
database in read-only mode.

Do not execute `db_queue.py` as root or change the installed helper's
root-owned file permissions to allow web-user writes.

## Website-controlled actions: current scope

| Host | Component | Website Install | Website Rollback |
| --- | --- | --- | --- |
| Ubuntu | `ubuntu-worker/worker.py` | Enabled **only after controller installation** | Yes |
| Ubuntu | `realtime/server.py` | Enabled **only after controller installation** | Yes |
| Ubuntu | CMS PHP and JS | No: guarded CMS release upgrade | No: existing CMS recovery |
| Gentoo | Node agent and dashboard | No: local root console only | Local console only |
| Gentoo | SSH tunnel | No: keep tunnel intact | No: manual tunnel recovery |

**Website installation of Gentoo and full CMS bundles is not complete.**
The UI hides Install/Rollback actions when the Ubuntu controller's
30-second heartbeat is stale. The PHP request additionally rejects them
before they enter the work queue. The admin must re-enter the password
and type `DEPLOY <12-char-commit>` or `ROLLBACK <12-char-commit>`.
Approvals and requests are written to the existing CMS audit log.

Only an approved, staged, immutable GitHub component with a matching
installed baseline can be installed. Ubuntu's controller handles one job
at a time; it never handles Gentoo jobs and never executes user-supplied
command strings. An interrupted in-progress job is marked
`interrupted` and **not automatically replayed**.

## Protection and rollback

The local `deploy/release/source_executor.py` requires:
- A fixed approved host, service and destination, exact plan ID,
  GitHub commit SHA and approved source-file SHA-256
- No symlink/hardlink target file, no baseline drift and valid Python syntax
- Root-owned private backup directory and at least **512 MiB** free
  on the destination filesystem
- A running target service before replacement
- A local process lock, exact operator confirmation, and a fresh
  approval (on Ubuntu)

The executor copies the live file and the approved staged source into
root-owned private storage, verifies both hashes and atomically replaces
the target. It restarts only the corresponding named service, checks
its status and automatically attempts rollback on failure.

**Recovery is independent of source artifact or approval expiration.**
If the controller is interrupted between backup and verification, the
original file remains in `/var/lib/sirussofts/source-releases/<plan-id>/`.
A root console can recover it based solely on the validated recovery
journal. The restore refuses unrelated file drift.

## Deployment onto Ubuntu, after review and merge

Do **not** run the commands until the PR is merged into
`feature/cms-vhost-distributed-nodes`. They operate on the existing
Hestia installation; never run the first-run CMS installer.

1. Review local working changes, then
   `git pull --ff-only origin feature/cms-vhost-distributed-nodes` from
   `/root/SirusSofts`.
2. Run the repo checks:
   `python3 -m unittest discover -s deploy/release/tests -p 'test_*.py' -v`,
   `php CMS/tests/source_deploy_test.php`,
   and `bash deploy/release/install-ubuntu.sh --check`.
3. Deploy the CMS using
   `bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com`,
   review the plan and existing local changes, then explicitly run the
   same script with `--apply`. The guarded CMS release creates a
   rollback backup and preserves SQLite/admin credentials.
4. Visit `https://sirussofts.com/admin/updates` once as the
   administrator to ensure the new queue schema exists.
5. Review/update the separate WebSocket Python installation using the
   existing guarded `realtime/install-ubuntu.sh --check|--plan|--install`
   workflow; the CMS PHP deployment does not replace its executable.
6. `bash deploy/release/install-ubuntu.sh --plan`, then interactive
   `bash deploy/release/install-ubuntu.sh --install`.
   Its installer refuses any previously queued deployment request.
7. Verify `systemctl is-active sirus-release-controller.service`,
   `journalctl -u sirus-release-controller.service -n 40 --no-pager`,
   and that the Update Center shows **controller connected**.
   Confirm the public website and Gentoo dashboard still respond.

Only after these gates should the administrator approve a specific
Ubuntu worker/WebSocket revision, wait for private staging, submit
**Install approved source**, and watch the WebSocket queue progress.
Do **not** test by applying an unreviewed older commit to production.

## Local recovery

From the reviewed checkout on Ubuntu, as root:

```sh
python3 deploy/release/source_executor.py --host ubuntu-primary --id PLAN_ID --rollback --confirm "ROLLBACK SHORT_COMMIT"
```

Gentoo can use the same CLI with `--host gentoo-home` only from a **local
root console** after the operator securely transfers the exact staged
artifact and manifest to `/var/lib/sirusnode/source_artifacts/`.
The existing sirus-tunnel user is deliberately restricted and must not
be changed into a general-purpose file/command tunnel.

Read-only local preflight for either host:

```sh
python3 deploy/release/source_executor.py --check
```

The website-controlled Gentoo source delivery, coherent whole-CMS
release manifests and production package installation are separate
workstreams, not enabled by this code.

## Failure handling

- Request `queued`: waiting for the fixed Ubuntu controller
- `running`: root controller claimed the exact request
- `done`: service restart and health check verified
- `failed`: no automatic retry, review root-owned backup and logs
- `interrupted`: controller stopped while applying; **operator review**
  of the journal is required before any other operation

Do not delete source artifacts, journals, or previous-file backups
during a deployment. Avoid manual file edits between approval and
installation. Never add unrestricted sudo rules to PHP or the
unprivileged Gentoo agent.
