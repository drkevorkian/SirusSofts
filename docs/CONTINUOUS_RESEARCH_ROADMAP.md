# SirusSofts — Continuous Autonomous Research & Learning Roadmap

**Source implementation status (November 19, 2026):** Phase A foundation coded on main. Not deployed, not accepted against the production corpus. The root README now defines **main as the only working branch**; the former dedicated-development-branch instruction is retired.

**Hard boundary:** Gentoo holds private source data, checkpoint state, and local inference. Ubuntu CMS acts as an authenticated administrator facade. No code push installs either server, promotes LLM outputs, or trains model weights.

## Phases and dependencies

| Phase | Status | Remaining gate |
|---|---|---|
| A — Inventory, section revisions, scheduling, recovery | **Implemented in source; live validation pending** | Reconcile the actual mounted SSD catalog, restart test with 100 passes, exercise backup and restore, measure queue scaling |
| B — Cross-document comparisons and contradictions | **Pending** | Independent-source evidence indexing, entity matching and per-application constraints |
| C — Verified, versioned knowledge | **Pending** | Dedicated claim/citation/reversal ledger and explicit human approval; retain existing approved CMS knowledge |
| D — Autonomous service and resource protection | **Partial** | Real GPU/VRAM/RAM admission, multi-node scheduling, quiet hours, energy limits, retention and load-tested chat latency |
| E — Optional LoRA/fine-tuning | **Disabled** | Separate operator approval, curated reviewed datasets, held-out evaluation and offline rollback |

## Phase A1: Complete eligible-source visibility — coded

The additive private scheduler lives in sirus-ai-dual-tunnel/gentoo/continuous_research.py. It inventories **all** PDF ingestion document records, including ready, approved, failed, queued, processing and zero-text sources. It reports source SHA-256, extraction status, page count, part/chunk count, missing-text pages, revisions, inclusion/exclusion and removed records. Missing-text pages are a coverage gap, **not proof of extraction failure**.

Pages/parts are chunked at at most 7,000 characters, with stable source/page/part/offset slot identities and per-content SHA-256 revisions. Old versions remain for audit; altered sections are not silently current. It also inventories existing one-off website/text research snapshots from private research_jobs, excluded from new autonomous work until an administrator includes them. It never re-fetches web pages.

**Acceptance:** Every PDF ingestion document must be represented, even if failed. Total per-page excerpt characters must equal the combined chunk lengths; revisions must distinguish old/new and removed sources. Compare counts on a small manual, scanned PDF and the full live corpus. Reconcile source removals and backed-up historical research records. Not yet completed on live data.

**Remaining formats:** Unstored web pages, ordinary text files outside research snapshots, and separately held CMS-reviewed knowledge need an approved private import/manifest before inclusion. Never crawl or copy those automatically. No new OCR pipeline is introduced.

## Phase A2: Durable scheduler and repeat coverage — coded

The existing private input SQLite database gains only additive cr_config, cr_sources, cr_sections, cr_cycles, cr_jobs, cr_results, and cr_events tables. Existing chat, outbox, one-off research passes/sections, source ingestion, knowledge profiles and administrator accounts remain unchanged.

Jobs are unique per cycle, section slot and content revision. A SQLite BEGIN IMMEDIATE transaction atomically claims one job and issues a random fencing token. Leases are renewed while inference runs; expired claims are retried with 15–3,600-second exponential backoff. Five failed claims for the same pass leave a **visible failed job**. Late/stale workers cannot commit results after their token is revoked or the content revision is superseded. Each pass commits an unreviewed result and checkpoint together. The next pass resumes without discarding earlier pass results.

When every queued/leased section in a cycle is finished or terminal, the cycle closes. After a configurable revisit interval (60–604,800 seconds), eligible sections are scheduled again; earlier cycle results remain. Processing is opt-in and **disabled by default**. A separate inventory thread refreshes source status periodically when enabled, or after an administrator requests a scan. No browser session is necessary.

**Acceptance:** Simulate two simultaneous claimers, a stalled lease, restarted engine, changed excerpt, paused worker and a 100-pass job interruption. Verify one fenced commit per pass and an auditable second cycle. Automated synthetic tests are present; live restart and endurance tests remain pending. A lease prevents duplicate **commits**, but physical inference can still briefly overlap following an abrupt timeout; validate model timeout and backend concurrency before production operation.

## Phase A3: Protected administrator controls — coded

The existing /admin/ai page gains Continuous Research status, cycle/queue/source coverage, current pass and worker, chat queue, scan error, source priorities and exclusions. Controls include enable, pause, resume, stop, disable, asynchronous inventory scan, 1–100 passes, local model ID, worker target, revisit interval, chat admission ceiling and optional CPU-load limit.

Controls POST through the **existing** CMS admin session, CSRF check, audit logger and rate limiter, then a fixed-schema Gentoo loopback request. The public /ai/api routes do not expose these controls. No arbitrary shell command, path, model endpoint or external fetch is accepted. The existing public Nginx /Gentoo/AI/api/ deny must remain installed.

**Acceptance:** Test unauthenticated/non-admin and CSRF-invalid mutations, invalid source IDs, unknown action and malformed settings. The admin page must fail closed and display unavailable when Gentoo is offline. The test suite covers payload validation and unauthorized mutation; external penetration and permission checks remain pending.

## Evidence boundary — enforced

Continuous research results reside only in private cr_results with review_state constrained to **unreviewed** by SQLite. Results are draft model outputs, not independent evidence; a second pass is not a second corroborating source. The existing source-grounded, untrusted-input prompt builder is reused. The CMS reviewed-knowledge and training export path is not linked to autonomous drafts. There are no automatic writes to model files, adapters or approved knowledge.

**Acceptance:** Database rejects any attempt to mark a draft supported/verified. A result must maintain source/page/part/section hashes and never become a verified citation through repeated inference. Synthetic checks pass; actual approval and retrieval integration belong to Phase C.

## Phase B: Multi-document intelligence — pending

Build a revisioned entity/part-number/specification index. Preserve model, year, engine, assembly, operating condition, units and procedure revision. Only flag contradictions supported by **two independently authored source records**; do not use multiple passes over one document as corroboration. Persist contradictions and re-evaluate when referenced originals change.

**Measurable gate:** For a curated conflicting-spec corpus, every candidate must cite two correct original page/part revisions and preserve application boundaries. No candidate may be published as a verified fact automatically.

## Phase C: Versioned verified knowledge — pending

Create a separate claim ledger with unreviewed/supported/disputed/rejected/superseded states, exact original source citations, history, reversal and source-change invalidation. Promotion must be a rate-limited, CSRF-protected authorized **human action**. Existing CMS approvals remain authoritative until verified replacement integration.

**Measurable gate:** Human approval/reversal tests; original-document changes flag previously promoted claims; self-generated or circular references cannot pass citation requirements.

## Phase D: Unattended resource management — partial

A persistent, opt-in Gentoo background thread executes one research pass at a time and yields before new work if active/queued chat or manually requested research is present. Optional one-minute CPU load cap and bounded draft output apply. Queue, cycle, lease, errors and worker are observable.

**Remaining:** Explicit GPU/VRAM/RAM telemetry and admission, quiet hours, power limits, source retention/archival, multi-node coordination, chat preemption and real benchmark-backed latency SLAs. The current gate protects **admission of new work**, not necessarily a chat request that arrives during an already-running inference.

**Measurable gate:** Two long cycles and peak interactive load without OOM, data loss, unacceptable latency, or unbounded storage growth.

## Phase E: Training — intentionally separate

No autonomous LoRA or model-weight changes. Future training requires curated **human-reviewed** references, duplicate filtering, independent evaluation, explicit model checkpoint control, an offline run and rollback. Disabled until separately approved.

## Validation and safe deployment

- Python: python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_*.py' -v
- PHP: php CMS/tests/ai_continuous_test.php, php CMS/tests/ai_research_test.php and the full existing CMS suite
- CI: .github/workflows/sirus-ai-dual-tunnel.yml and .github/workflows/php-framework.yml, including Python syntax, PHP lint, serious undefined-name and shell analysis
- Source tests cover inventory completeness, long-section coverage, migration preservation, content changes, duplicate prevention, leases/fencing, retries, cycle eligibility, pauses, source exclusion, chat admission, and draft-only evidence.

**Safe installation plan:** docs/CONTINUOUS_RESEARCH_DEPLOYMENT.md. Pin and review a green main SHA; use existing backup-backed in-place upgrade scripts, not the old full installers. No Ubuntu/Gentoo apply, service restart, model change, or destructive storage operation has been done during this implementation.

**Full-roadmap definition of done (NOT met):** End-to-end multi-format inventory, validated repeat cycles after outages, independent cross-document reasoning, versioned human-approved claims and rollback, measured interactive responsiveness, monitoring and archival, plus explicit operator acceptance on both servers.
