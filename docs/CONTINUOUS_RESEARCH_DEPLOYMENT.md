# Continuous Research — reviewed in-place deployment plan (NOT executed)

This document is a **future operator runbook**. A GitHub commit and green CI are NOT deployment or permission to restart live services.

**Current installed-state warning (user-confirmed, 2026-11-22):** Both Ubuntu and Gentoo still run older code from before this continuous-research development effort. None of the roadmap commits, including the newer CMS learning-readiness view and subject-profile updates, are known to be deployed. No host revision was independently queried. Do not attempt new UI/API features against the existing live installations and do not treat source checklist completion as live acceptance.

**Before Stage 0 leads to an apply:** first identify the installed CMS and Gentoo worker revisions read-only, review the upgrade path from those exact versions, establish and test independent backups of the Ubuntu CMS, Gentoo input SQLite, and PDF ingest database, verify storage and the private HMAC bridge key, then pin a final commit with passing CI. Plan/inspection alone does not authorize upgrades. All deploys and service restarts require separate operator approval.

## Stage 0: Pin and verify

1. Record a reviewed main commit SHA from GitHub. Confirm both PHP framework checks and SirusSofts AI dual-tunnel validation are green for **that SHA**.
2. Review the additive private SQLite schema and tests, CMS/app/ai_continuous.php, sirus-ai-dual-tunnel/gentoo/continuous_research.py, sirus-ai-dual-tunnel/gentoo/research_evidence.py and all in-place upgrade changes. Confirm the claims and evidence tables are backed up with input.sqlite3. The root README's main-only policy supersedes old feature-branch notes.
3. On Gentoo, inspect the dedicated /srv/sirussofts/storage SSD mount, private pdf_ingest.sqlite3 (including WAL), the sirusai account's read permission and running local llama.cpp models. Do not fall back to a different disk without operator approval.
4. Run Python tests including test_research_evidence.py, php CMS/tests/ai_continuous_test.php, php CMS/tests/ai_evidence_test.php, full CMS tests, PHP lint, Python compileall, shellcheck and the two Actions workflows.
5. Verify capacity for growth of unreviewed drafts before enabling more than one small PDF. On Gentoo, inspect whether the actual GPU drivers expose `/sys/class/drm/card*/device/mem_info_vram_total` and `mem_info_vram_used` (AMD) or `/usr/bin/nvidia-smi` (NVIDIA). Missing readings are not free VRAM. Enable minimum free GPU VRAM only with a pinned AMD/NVIDIA research target and verified worker-device mapping; the threshold does not reserve memory.
6. Confirm application-scope changes append immutable audit records and immediately supersede supported claims. A stale review form must be rejected, not silently approved. Back up `cr_scope_versions` and the additive claim applicability column with the private Gentoo SQLite database.
7. Stage the **separate private administrative bridge key** described below before relying on any controller panel or claim review. Without it, private research GET/POST endpoints intentionally reject requests while existing chat and one-off research remain available.

## Source-only checks added for targeted cancellation and audit

Before enabling an unattended workload, select a noncritical small test PDF on Gentoo, configure more than one pass, and start two jobs. Cancel one job from the authenticated CMS using its exact job ID and explicit confirmation, then verify: its already checkpointed passes remain available; stale in-flight results are rejected; another queued job continues; and global Resume does not revive the canceled job. A later full revisit cycle may schedule a fresh job for that section. Cancellation **does not forcibly interrupt a running llama.cpp inference call**, so test GPU/CPU contention independently.

For a selected source, page through both the signed pass history (eight passes per page) and the independent private lifecycle event history (20 events per page); ensure unrelated source events are never mixed in. Verify that JSON report downloads include only the selected pages, preserve source identity, and remain marked private and unreviewed. Previous pre-upgrade events remain, but unlogged historical transitions cannot be retroactively reconstructed.

SQLite cr_events and cr_job_cancellations now have append-only triggers. Do **not** run destructive cleanup or drop these tables during rollback. Ensure the operator's consistent input SQLite backup captures both tables. Continuously running research will grow these tables and draft storage; a reviewed archival/retention/restore plan is still required before broad rollout. Confirm actual service interruption and restart behavior separately; unit tests alone do not establish production acceptance.

The evidence dashboard includes a separate recent human-supported original-PDF quotation feed. Test that supported source/page/part metadata is displayed, while disputed, rejected, superseded and changed-applicability claims are excluded. A verified quote is not blanket approval of a repair procedure.

## Privileged CMS-to-Gentoo bridge: mandatory key provisioning

**Never write the key into this repository, a command argument, HTTP query string, server log, web root or chat.** On the authorized maintenance window, generate exactly **32 raw random bytes** once with the operating system CSPRNG and transfer the same binary key over the preexisting trusted, host-key-pinned administrator channel.

- Ubuntu: place the binary bytes at `cms_private_directory()/research-admin.key`, outside the public document root, mode `0600`, owned/readable only by the CMS PHP-FPM service account (`sirusweb`). Resolve the actual private root using the deployed CMS, not a guessed path.
- Gentoo: place the same 32 raw bytes at `/etc/sirus-ai-dual/research-admin.key`, a regular non-symlink file mode `0600`, owned/readable only by the `sirusai` input-service account. Do not change existing tunnel keys or the outbox authentication secret.
- Independently compare secure SHA-256 fingerprints without exposing the key contents. Synchronize both system clocks to within **120 seconds**; stale timestamps are rejected. Reuse of a nonce is rejected and recorded in private SQLite.
- Rotate this key with the controller disabled and both copies updated as one reviewed operation; old signatures are rejected after rotation. Back up the key through the existing approved **private** secrets recovery process, not the public CMS release archive.

Both `/api/in/research/continuous` and `/api/in/research/evidence` reads, plus their `/control` mutations, now require timestamp/nonce/HMAC proof. The CMS still enforces its normal session authorization, CSRF and rate limiting. A missing key fails closed and the CMS panels show unavailable, rather than opening an unauthenticated fallback. The Gentoo model and legacy chat routes do not depend on the new key.

## Stage 1: Backups and read-only plans

Take and verify **consistent SQLite backups** of Gentoo /var/lib/sirus-ai-dual/input.sqlite3, the PDF ingestion database (with WAL state), Gentoo AI router files/service arguments, and Ubuntu's private CMS SQLite and site files. Use the scripts' existing private release backups; keep backup checksums, owner/modes and rollback instructions off the public website. Never put source PDFs, credentials or backups in GitHub.

From the pinned checkout, inspect the two existing plan modes:
- bash sirus-ai-dual-tunnel/gentoo/upgrade-in-place.sh --plan
- bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com

Check actual usage and read each plan. Do **not** use the original Gentoo/Ubuntu installers or Plesk/direct-Nginx scripts.

## Stage 2: Controlled apply after separate operator approval

1. Drain manually requested research and verify all prior jobs/checkpoints were backed up. Keep PDF ingestion and local models unchanged.
2. Use the Gentoo **in-place** upgrade's reviewed apply flow. It backs up and installs the input router and continuous_research.py plus research_evidence.py, and restarts only sirus-ai-input. Recheck local input health and ensure controller mode remains disabled. The source evidence/claim schemas are additive; no existing records may be replaced.
3. Use the Hestia-owned **in-place CMS** upgrade flow to install the admin UI and helpers, preserving the site's private database, permissions and TLS. Verify the private bridge key ownership before the first authenticated dashboard request. Do not modify SSH keys, firewall, models or source objects.
4. In /admin/ai, authenticate as admin and inspect the controller. Verify an unsigned direct request to the internal evidence/status routes is refused with HTTP 403, then verify the signed CMS panel works. Request Scan inventory. Compare eligible, excluded, failed/pending, missing-text, historical/current and removed source counts to the private ingestion catalog.
5. Exclude all but one **non-sensitive** three-page PDF, select one pass and a long revisit interval, configure conservative free-RAM and UTC quiet hours, then explicitly enable for a controlled test. Confirm original source labels, private draft-only storage, one lease, no unintended network/model actions, and normal chat availability.
6. In the Continuous Research panel, confirm cycle completed/total passes and inference-only throughput; select a source, page through unreviewed history and explicitly download one eight-pass JSON report. Verify the source/page/revision annotations and admin-only, no-store behavior; handle private model notes as sensitive. Stop if history or report exceeds bounded size or becomes available without signed access.
7. In the administrator evidence panel, index a small original PDF, set two deliberately matching applicability scopes, review a known measurement discrepancy, and verify that a human-supported claim requires an approved original and explicit confirmation. Revise one original and confirm claim supersession while preserving history. Do not sync supported claims into existing CMS RAG or training exports.
8. Only during the planned test, simulate a worker stop/restart and verify safe checkpoint resume and lease fencing. Then run a 100-pass test with interruption and repeat a full coverage cycle under monitored CPU/GPU/RAM/disk load.

## Private database growth and non-destructive recovery (NOT deployed)

The optional private Gentoo database growth ceiling covers the active `input.sqlite3` main file, its WAL and shared-memory sidecar bytes. The administrator can set `max_database_mib=0` to disable the gate, or a measured limit suitable for the actual storage volume. At the limit, no new autonomous research job is scheduled. Existing interactive chat and already-running requests are not forcibly terminated, and writes can still grow the files. This is **not** a replacement for disk free-space alarms or proper space reservations.

Operational samples record input-service RSS, host RAM, queued/leased/failed counts and private database footprint every minute while the controller is enabled and on a private status read. The diagnostic sample table alone is bounded to seven days. **Do not prune** the immutable `cr_events`, `cr_job_cancellations`, verified human review history, original citations or private research drafts as an automatic retention shortcut.

### Explicit private archive utility

With operator-approved private storage (never a web root or public download directory), create a new mode-0700 directory on the intended volume. Run the following from a pinned reviewed SirusSofts repository checkout using an account that can read the actual Gentoo input SQLite database and write the private archive destination. Replace the placeholder with a verified absolute private path; do not paste private keys into commands.

```sh
python3 -B sirus-ai-dual-tunnel/gentoo/research_archive.py create --source /var/lib/sirus-ai-dual/input.sqlite3 --directory /reviewed/private/archive
python3 -B sirus-ai-dual-tunnel/gentoo/research_archive.py verify --archive /reviewed/private/archive/research-YYYYMMDDHHMMSS-XXXXXXXXXXXXXXXX.sqlite3
python3 -B sirus-ai-dual-tunnel/gentoo/research_archive.py restore-copy --archive /reviewed/private/archive/research-YYYYMMDDHHMMSS-XXXXXXXXXXXXXXXX.sqlite3 --destination /reviewed/private/recovery/restored-input.sqlite3
```

The archive writes a consistent SQLite snapshot through Python's `sqlite3.backup()` (including already committed WAL contents), verifies `PRAGMA integrity_check`, hashes the file and publishes an accompanying private JSON manifest with table row counts. Restore copies only to **new** files under an existing mode-0700 private directory; the utility refuses to overwrite the live input database. It never restarts a service, deletes history, prunes retained source material, trains a model, downloads files or sends data over the network.

Archive contents include the **entire** Gentoo input SQLite database: chat queue contents, source metadata, model drafts, immutable audits, human-reviewed quote history and private research records. Source PDF bytes and the separate `pdf_ingest.sqlite3`, Ubuntu CMS database, and private signing keys are **not** included. Secure and back up those separately through their existing procedures. Snapshots are mode-0600 but **not encrypted**; keep archive directories access-controlled and protected through the approved backup/encryption process. Never put archives in the Git repository, CMS web root, or user-visible reports.

Before promoting any restored copy, stop affected services in a planned window, validate the source database and backup/WAL state, verify hashes and table counts, check the separately backed-up PDF-ingest/Ubuntu sources, and test the recovery copy in isolation. **Do not** overwrite the running database based on an untested copy. Perform a rehearsal restore with a throwaway dataset before production approval.

**Retention remains incomplete.** Snapshots preserve data and therefore increase total disk usage. Automated provenance-aware selective archival, bounded primary+archive storage, retrieval of historic references across archives and rollback of pruned histories still require separate design and tests; leave the original roadmap retention item unchecked.

### Scheduler fairness and resource trends

Previously completed unchanged source revisions enter later full coverage cycles at a lower priority than new/revised sections with the *same* admin-assigned source priority. An explicitly higher administrator priority still wins, and normal full-cycle repeat coverage is preserved. The admin dashboard exposes a bounded latest-60 resource sample history and sampled queued-count delta, plus measured per-pass inference latency. This does not measure the resident memory of external llama.cpp processes or ensure GPU availability; continue live benchmarking and resource monitoring.
### Offline cold-history catalog, source recovery and retention preview (NOT deployed)

The Gentoo approved in-place upgrade will copy both local, trusted-operator programs: `research_archive.py` (snapshot creation, integrity verification and restore-copy) and `research_cold_history.py` (offline inspection). No public CMS/Gentoo HTTP route is added, and no standalone installer or live OpenRC service is started by these changes.

Run these examples only from a **pinned, reviewed source checkout** against an already created private archive directory, replacing paths, exact source IDs and job IDs with verified private values. The CLI prints JSON only to the operator's terminal. A PDF source ID has the form `pdf:` followed by 64 lowercase hexadecimal characters; a prior research source ID uses `research:` followed by 32 lowercase hex characters; a research job uses a 32-lowercase-hex ID.

```sh
python3 -B sirus-ai-dual-tunnel/gentoo/research_cold_history.py catalog --directory /reviewed/private/archive
python3 -B sirus-ai-dual-tunnel/gentoo/research_cold_history.py locate-source --directory /reviewed/private/archive --source-id 'pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
python3 -B sirus-ai-dual-tunnel/gentoo/research_cold_history.py locate-job --directory /reviewed/private/archive --job-id '11111111111111111111111111111111'
python3 -B sirus-ai-dual-tunnel/gentoo/research_cold_history.py source-history --archive /reviewed/private/archive/research-YYYYMMDDHHMMSS-XXXXXXXXXXXXXXXX.sqlite3 --source-id 'pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' --offset 0 --audit-offset 0
python3 -B sirus-ai-dual-tunnel/gentoo/research_cold_history.py job-provenance --archive /reviewed/private/archive/research-YYYYMMDDHHMMSS-XXXXXXXXXXXXXXXX.sqlite3 --job-id '11111111111111111111111111111111'
python3 -B sirus-ai-dual-tunnel/gentoo/research_cold_history.py retention-preview --archive /reviewed/private/archive/research-YYYYMMDDHHMMSS-XXXXXXXXXXXXXXXX.sqlite3 --before-unix 1760000000
```

The filenames and source/job IDs above are **examples, not known live files or real identifiers**. `source-history` returns at most eight pass records and 20 audit events per call with separate pagination; review-version metadata includes original citation hashes, reviewer identity and applicability. Draft notes are omitted by default. The optional `--include-private-drafts` flag for `source-history` or `job-provenance` prints **full unredacted private model output** into the local terminal; use it only with explicitly approved private terminal/log handling. It does not promote anything into verified knowledge, RAG or model weights.

The catalog scans up to 64 snapshots and refuses to claim completeness if any scanned snapshot is invalid or a matching manifest is orphaned. Each query verifies the corresponding SQLite snapshot's SHA-256, manifest row counts and integrity first. These checks catch accidental corruption, but **a mutable SHA-256 manifest is not an independent cryptographic signature**; protect both archive bytes and manifests through operator-controlled private encrypted backup. Identical passes appearing in multiple full snapshots are duplicates of history, not multiple independent supporting sources.

The retention preview counts historical old-revision terminal jobs in closed cycles. It always reports `pruning_permitted=false` and **never deletes, VACUUMs or marks jobs archived**. Do not interpret the count as safe reclaimable space. Immutable event/claim provenance, active leases, current source references and source-PDF backup/restore still need a reviewed selective migration design. Live deployment, restoration and controlled pruning are outside this increment.

## Stage 3: Rollback and gates

Disable the new controller before any rollback. Use private release backups to restore prior input router/CMS code; never drop any cr_ tables or erase legacy research, knowledge or PDF data as a rollback shortcut. An older router should ignore additive tables. Restore the consistent private SQLite snapshot only after reconciling newer job history and in-flight writes; avoid silently discarding data created since the backup.

Verify private Gentoo health on 127.0.0.1:18082, Ubuntu loopback proxy, existing interactive chat, one-off 1–100-pass research, retrieved/manual citations, CMS admin, outbox delivery and Hestia routing. Never expose Gentoo input routes, raw draft content or storage metadata as unauthenticated public URLs.

**Still required before broad rollout:** real-library coverage reconciliation, 100-pass process-kill recovery, human adjudication of source independence and technical context, chat latency SLAs, VRAM/process-specific admission and energy caps, archived retention/restore policy, reviewed-claim retrieval integration and an explicitly approved source-format expansion. Automatic claim promotion, automatic knowledge export and model training remain disabled.


## Offline procedural-review queue integration (not deployed)

The approved Gentoo in-place source manifest now includes
`procedure_review_queue.py`. Its SQLite database
`/srv/sirussofts/storage/rag/procedure_reviews.sqlite3` is created **only
when an operator explicitly registers** a reviewed comparison. It resides
outside the web root and is not a replacement for the original PDF ingest,
manual relationship index, or continuous research database.

After an approved pinned-source in-place upgrade and private backup, validate
that the local AI input worker can load the queue module and that no review
targets are registered unexpectedly. Enabling Continuous Research admits one
due recheck per idle inventory iteration, after derived index synchronization.
No additional public route, network listener, worker service or model weight
operation is introduced. Run the documented unit tests and a small
administrator-selected reviewed-manual trial before considering wider use;
never claim the source-only result is installed merely because CI is green.
