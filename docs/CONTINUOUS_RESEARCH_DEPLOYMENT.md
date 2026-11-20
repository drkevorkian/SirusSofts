# Continuous Research — reviewed in-place deployment plan (NOT executed)

This document is a **future operator runbook**. A GitHub commit and green CI are NOT deployment or permission to restart live services.

## Stage 0: Pin and verify

1. Record a reviewed main commit SHA from GitHub. Confirm both PHP framework checks and SirusSofts AI dual-tunnel validation are green for **that SHA**.
2. Review the additive private SQLite schema and tests, CMS/app/ai_continuous.php, sirus-ai-dual-tunnel/gentoo/continuous_research.py, sirus-ai-dual-tunnel/gentoo/research_evidence.py and all in-place upgrade changes. Confirm the claims and evidence tables are backed up with input.sqlite3. The root README's main-only policy supersedes old feature-branch notes.
3. On Gentoo, inspect the dedicated /srv/sirussofts/storage SSD mount, private pdf_ingest.sqlite3 (including WAL), the sirusai account's read permission and running local llama.cpp models. Do not fall back to a different disk without operator approval.
4. Run Python tests including test_research_evidence.py, php CMS/tests/ai_continuous_test.php, php CMS/tests/ai_evidence_test.php, full CMS tests, PHP lint, Python compileall, shellcheck and the two Actions workflows.
5. Verify capacity for growth of unreviewed drafts before enabling more than one small PDF.

## Stage 1: Backups and read-only plans

Take and verify **consistent SQLite backups** of Gentoo /var/lib/sirus-ai-dual/input.sqlite3, the PDF ingestion database (with WAL state), Gentoo AI router files/service arguments, and Ubuntu's private CMS SQLite and site files. Use the scripts' existing private release backups; keep backup checksums, owner/modes and rollback instructions off the public website. Never put source PDFs, credentials or backups in GitHub.

From the pinned checkout, inspect the two existing plan modes:
- bash sirus-ai-dual-tunnel/gentoo/upgrade-in-place.sh --plan
- bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com

Check actual usage and read each plan. Do **not** use the original Gentoo/Ubuntu installers or Plesk/direct-Nginx scripts.

## Stage 2: Controlled apply after separate operator approval

1. Drain manually requested research and verify all prior jobs/checkpoints were backed up. Keep PDF ingestion and local models unchanged.
2. Use the Gentoo **in-place** upgrade's reviewed apply flow. It backs up and installs the input router and continuous_research.py plus research_evidence.py, and restarts only sirus-ai-input. Recheck local input health and ensure controller mode remains disabled. The source evidence/claim schemas are additive; no existing records may be replaced.
3. Use the Hestia-owned **in-place CMS** upgrade flow to install the admin UI and helpers, preserving the site's private database, permissions and TLS. Do not modify SSH keys, firewall, models or source objects.
4. In /admin/ai, authenticate as admin and inspect the controller. Request Scan inventory. Compare eligible, excluded, failed/pending, missing-text, historical/current and removed source counts to the private ingestion catalog.
5. Exclude all but one **non-sensitive** three-page PDF, select one pass and a long revisit interval, configure conservative free-RAM and UTC quiet hours, then explicitly enable for a controlled test. Confirm original source labels, private draft-only storage, one lease, no unintended network/model actions, and normal chat availability.
6. In the administrator evidence panel, index a small original PDF, set two deliberately matching applicability scopes, review a known measurement discrepancy, and verify that a human-supported claim requires an approved original and explicit confirmation. Revise one original and confirm claim supersession while preserving history. Do not sync supported claims into existing CMS RAG or training exports.
7. Only during the planned test, simulate a worker stop/restart and verify safe checkpoint resume and lease fencing. Then run a 100-pass test with interruption and repeat a full coverage cycle under monitored CPU/GPU/RAM/disk load.

## Stage 3: Rollback and gates

Disable the new controller before any rollback. Use private release backups to restore prior input router/CMS code; never drop any cr_ tables or erase legacy research, knowledge or PDF data as a rollback shortcut. An older router should ignore additive tables. Restore the consistent private SQLite snapshot only after reconciling newer job history and in-flight writes; avoid silently discarding data created since the backup.

Verify private Gentoo health on 127.0.0.1:18082, Ubuntu loopback proxy, existing interactive chat, one-off 1–100-pass research, retrieved/manual citations, CMS admin, outbox delivery and Hestia routing. Never expose Gentoo input routes, raw draft content or storage metadata as unauthenticated public URLs.

**Still required before broad rollout:** real-library coverage reconciliation, 100-pass process-kill recovery, human adjudication of source independence and technical context, chat latency SLAs, VRAM/process-specific admission and energy caps, archived retention/restore policy, reviewed-claim retrieval integration and an explicitly approved source-format expansion. Automatic claim promotion, automatic knowledge export and model training remain disabled.
