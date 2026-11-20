# SirusSofts — Continuous Autonomous Research & Learning Roadmap

**Source implementation status (November 19, 2026):** Phase A foundation, initial Phase B original-source comparison and a separately review-gated Phase C claim history coded on main. Not deployed, not accepted against the production corpus. The root README now defines **main as the only working branch**; the former dedicated-development-branch instruction is retired.

**Hard boundary:** Gentoo holds private source data, checkpoint state, and local inference. Ubuntu CMS acts as an authenticated administrator facade. No code push installs either server, promotes LLM outputs, or trains model weights.

## Phases and dependencies

| Phase | Status | Remaining gate |
|---|---|---|
| A — Inventory, section revisions, scheduling, recovery | **Implemented in source; live validation pending** | Reconcile the actual mounted SSD catalog, restart test with 100 passes, exercise backup and restore, measure queue scaling |
| B — Cross-document comparisons and contradictions | **Partial** | Deterministic, source-linked numeric measurement candidates and manually scoped contradiction tracking implemented; semantic entity linking, conversion and independent-authorship review pending |
| C — Verified, versioned knowledge | **Partial** | Private append-only claim revisions, explicit human approval/reversal and source-change supersession implemented. No automatic RAG export/retrieval; full claim semantics and independent source verification pending |
| D — Autonomous service and resource protection | **Partial** | Chat queue, CPU load, minimum host RAM and UTC quiet hours implemented; GPU/VRAM constraints, preemption, multi-node coordination, energy limits, retention and chat load testing pending |
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

Controls POST through the **existing** CMS admin session, CSRF check, audit logger and rate limiter, then a fixed-schema Gentoo loopback request. New research control, evidence status and claim review endpoints additionally require an HMAC-signed, single-use nonce and timestamp from a separately provisioned 32-byte secret. Direct unsigned localhost calls receive HTTP 403, including private status reads. The public /ai/api routes do not expose these controls. No arbitrary shell command, path, model endpoint or external fetch is accepted. The existing public Nginx /Gentoo/AI/api/ deny must remain installed.

**Acceptance:** Test unauthenticated/non-admin and CSRF-invalid mutations, invalid source IDs, unknown action, malformed settings, forged HMAC, duplicate nonce, expired timestamp, bad key permissions, and unsigned private reads. The admin page must fail closed and display unavailable when Gentoo is offline. The test suite covers payload validation and unauthorized mutation; external penetration and permission checks remain pending.

## Evidence boundary — enforced

Continuous research results reside only in private cr_results with review_state constrained to **unreviewed** by SQLite. Results are draft model outputs, not independent evidence; a second pass is not a second corroborating source. The existing source-grounded, untrusted-input prompt builder is reused. The CMS reviewed-knowledge and training export path is not linked to autonomous drafts. There are no automatic writes to model files, adapters or approved knowledge.

**Acceptance:** Database rejects any attempt to mark a draft supported/verified. A result must maintain source/page/part/section hashes and never become a verified citation through repeated inference. Synthetic checks pass; actual approval and retrieval integration belong to Phase C.

## Phase B: First original-source comparison index — partial

**Implemented:** \`sirus-ai-dual-tunnel/gentoo/research_evidence.py\` indexes strictly formatted measurement lines (for example, \`Rear axle torque: 40 Nm\`) directly from original PDF extracted sections. It records page, part, stable section ID, source content revision, exact bounded quote, SHA-256 hash, numeric value and original unit. Ineligible/removed sections are ignored. It never treats LLM notes or prior model passes as source material. Source text remains on Gentoo.

An administrator can assign an explicit applicability key such as \`brp/defender/2026/hd11/clutch\`; unscoped documents are **never automatically compared**. Only equal normalized property labels, equal literal units, equal applicability keys and different source document identities produce \`cr_conflicts\` candidates. A durable paginated comparison cursor covers pairs rather than truncating to a hard per-document peer limit. Differences stay \`unreviewed\` until the administrator inspects them; dismissed or superseded candidates keep their history.

**Not implemented:** General text/diagram and procedural extraction, unit conversion, reliable part-number/entity alignment, source-authorship independence determination, applicability inference, automatic model/year mapping and semantic contradiction classification. Explicitly different source SHA-256 values **do not** establish independent authorship. Contradictions are *review requests*, not claims that either manual is wrong.

**Measurable acceptance:** For a curated three-document suite containing equal and unequal specifications, identical source IDs cannot form a cross-document conflict; different model/year scopes prevent false comparisons; mismatched units do not compare silently; source changes invalidate prior candidates. Complete comparison pagination and the original PDF citations must be checked on a private test corpus before broader scope expansion.

## Phase C: Private versioned review ledger — partial

**Implemented:** \`cr_evidence\`, \`cr_claims\`, \`cr_claim_versions\` and comparison records are additive, private Gentoo SQLite tables. The first proposal remains unreviewed until an authenticated CMS administrator submits a CSRF-checked, rate-limited review action from \`/admin/ai\`. To record \`supported\`, the administrator must type \`I VERIFIED ORIGINAL\`, and Gentoo rechecks the exact original PDF quote, content revision, ingestion status, and existing independent PDF approval. Support, dispute, rejection and supersession append immutable claim revisions with the reviewer identity. Database triggers reject update/delete of prior history. Changed or excluded source revisions automatically supersede previously supported claims while retaining older history. Model inference cannot call the approval path.

**Critical evidence semantics:** A supported record means **the human checked that the quoted statement exists in an approved original source**. It is not proof that a service procedure is safe/correct in every application. Repeated LLM analysis is never independent evidence.

**Not implemented:** New approved claims are **not** written into existing \`cms_ai_documents\`, exported as verified RAG knowledge, or added to training datasets. That integration requires explicit transfer, licensing, source scope checks, human approval policy, and retrieval rollback. Cross-document human corroboration and richer claim semantic models remain pending.

**Measurable acceptance:** The new PHP/Python tests enforce administrator-only actions, explicit review confirmation, exact SHA/page/part provenance, rejection of stale or unapproved originals, immutable review history, reversible human decisions and automatic source-revision supersession. A controlled source update and restoration test is required against the real private library.

## Phase D: Unattended resource management — partial

A persistent, opt-in Gentoo background thread executes one research pass at a time and defers new work if active/queued chat or manually requested research is present. Optional one-minute CPU load cap, minimum available RAM and UTC quiet-hours admission limits apply. Bounded deterministic evidence indexing/comparison runs in smaller batches when no chat is active. Queue, cycle, lease, errors, worker, CPU load and RAM headroom are observable.

**Remaining:** Explicit GPU/VRAM telemetry and process-specific admission, power limits, source retention/archival, multi-node coordination, chat preemption and real benchmark-backed latency SLAs. Minimum free RAM uses the Linux MemAvailable host metric and is not equivalent to reserving a model's VRAM. Quiet hours are in UTC, not inferred local time. The current gate protects **admission of new work**, not necessarily a chat request that arrives during an already-running inference.

**Measurable gate:** Two long cycles and peak interactive load without OOM, data loss, unacceptable latency, or unbounded storage growth.

## Phase E: Training — intentionally separate

No autonomous LoRA or model-weight changes. Future training requires curated **human-reviewed** references, duplicate filtering, independent evaluation, explicit model checkpoint control, an offline run and rollback. Disabled until separately approved.

## Validation and safe deployment

- Python: python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_*.py' -v (including test_continuous_research.py and test_research_evidence.py)
- PHP: php CMS/tests/ai_continuous_test.php, php CMS/tests/ai_evidence_test.php, php CMS/tests/ai_research_test.php and the full existing CMS suite
- CI: .github/workflows/sirus-ai-dual-tunnel.yml and .github/workflows/php-framework.yml, including Python syntax, PHP lint, serious undefined-name and shell analysis
- Source tests cover inventory completeness, long-section coverage, additive migration preservation, content changes, duplicate prevention, leases/fencing, retries, cycle eligibility, pauses, source exclusion, chat admission, RAM and UTC quiet-hours gates, deterministic evidence indexing, scope-limited contradiction candidates, stale original rejection, append-only human review/reversal, HMAC/nonce validation and real unsigned-localhost route rejection.

**Safe installation plan:** docs/CONTINUOUS_RESEARCH_DEPLOYMENT.md. Pin and review a green main SHA; use existing backup-backed in-place upgrade scripts, not the old full installers. No Ubuntu/Gentoo apply, service restart, model change, or destructive storage operation has been done during this implementation.

**Full-roadmap definition of done (NOT met):** End-to-end multi-format inventory, validated repeat cycles after outages, deeper independent cross-document reasoning, reviewed-claim retrieval integration and rollback, measured interactive responsiveness, monitoring and archival, plus explicit operator acceptance on both servers. All optional LoRA training remains disconnected and disabled.
