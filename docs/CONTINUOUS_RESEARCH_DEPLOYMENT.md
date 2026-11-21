# Continuous Research — reviewed in-place deployment plan (NOT executed)

This document is a **future operator runbook**. A GitHub commit and green CI are NOT deployment or permission to restart live services.

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

## Stage 3: Rollback and gates

Disable the new controller before any rollback. Use private release backups to restore prior input router/CMS code; never drop any cr_ tables or erase legacy research, knowledge or PDF data as a rollback shortcut. An older router should ignore additive tables. Restore the consistent private SQLite snapshot only after reconciling newer job history and in-flight writes; avoid silently discarding data created since the backup.

Verify private Gentoo health on 127.0.0.1:18082, Ubuntu loopback proxy, existing interactive chat, one-off 1–100-pass research, retrieved/manual citations, CMS admin, outbox delivery and Hestia routing. Never expose Gentoo input routes, raw draft content or storage metadata as unauthenticated public URLs.

**Still required before broad rollout:** real-library coverage reconciliation, 100-pass process-kill recovery, human adjudication of source independence and technical context, chat latency SLAs, VRAM/process-specific admission and energy caps, archived retention/restore policy, reviewed-claim retrieval integration and an explicitly approved source-format expansion. Automatic claim promotion, automatic knowledge export and model training remain disabled.
