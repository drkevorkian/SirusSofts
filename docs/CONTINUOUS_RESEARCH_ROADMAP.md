# SirusSofts — Continuous Autonomous Research & Learning Roadmap

**Status:** Approved roadmap — source implementation underway; production deployment and acceptance pending  
**Priority:** High  
**Tracking baseline:** a70275e6ceb9 (source changes tested in GitHub; live acceptance pending, 2026-11-20)  
**Platform:** Gentoo local AI workers + Ubuntu public CMS gateway  
**Objective:** Enable the local LLM to continuously revisit, analyze, cross-reference, and improve its understanding of the entire available knowledge library without requiring a user to initiate each research session.

### Deployment state — operator-confirmed, 2026-11-22

- **Ubuntu public CMS/gateway: NOT UPDATED for this roadmap.** The installed system still runs the pre-roadmap/older code. Changes committed to `main` are not present on that host.
- **Gentoo AI/worker/research services: NOT UPDATED for this roadmap.** The installed system still runs older code; repository source changes and UI additions are not active there.
- **No production inference loop, GPU admission change, training integration, new administrative learning panel, archive tool or database migration is claimed to be installed.** Whether any unrelated older features are running is not established here.
- **GitHub checkboxes are source-development status only: 72/138 currently marked.** Production deployment is pending; Section 11 remains **0/12 live acceptance criteria verified**. This is not equivalent to 72 deployed features.
- Before any implementation is marked **deployed**, record actual installed revisions on both hosts using read-only commands, compare against a reviewed and green pinned commit, prepare independent and verified backups (CMS, Gentoo input, PDF ingest), perform the approved in-place upgrade, check the private bridge key, and verify real endpoints and hardware. **Do not run the old full installers or apply unapproved upgrades.**

### Checklist rules

- **[x] Complete in the GitHub source and covered by existing automated tests or a validated source boundary.** A checkmark **does not** mean the feature is installed on Gentoo/Ubuntu, accepted against the private PDF library, or safe for unrestricted production use.
- **[ ] Open or partially implemented.** Each partial item retains its unchecked state until its **whole** requirement is met; source-only progress is noted beside it.
- **Section 11 is real deployment/acceptance testing.** Synthetic unit tests are noted as partial evidence but do not close real-system acceptance tasks.
- **No source or checklist update deploys servers.** In particular, the 32-byte private signed administrative bridge key still needs secure provisioning in an approved maintenance window. Model-weight training remains disabled.
- **Git workflow:** The repository root README specifies `main` only; the original dedicated-development-branch requirement is superseded rather than performed. Keep history and rollbacks through normal reviewed commits.

**Overall source checklist: 72 / 138 checked (52.2%).** This is development progress, **not production readiness**.

| Section | Source-complete | Still open |
|---|---:|---:|
| 1. Core requirements | 6/10 | 4 |
| 2. Knowledge inventory | 6/10 | 4 |
| 3. Scheduler | 11/12 | 1 |
| 4. Multi-pass reasoning | 4/12 | 8 |
| 5. Verification and memory | 9/12 | 3 |
| 6. Cross-document intelligence | 1/9 | 8 |
| 7. Resource management | 5/11 | 6 |
| 8. Administrator dashboard | 15/15 | 0 |
| 9. Safety and reliability | 7/12 | 5 |
| 10. Optional model training | 1/9 | 8 |
| 11. Acceptance tests | 0/12 | 12 |
| 12. Implementation and deployment | 7/14 | 7 |

**Primary implementation:** [Gentoo scheduler](../sirus-ai-dual-tunnel/gentoo/continuous_research.py) · [Original-source evidence ledger](../sirus-ai-dual-tunnel/gentoo/research_evidence.py) · [Signed private bridge](../sirus-ai-dual-tunnel/gentoo/research_admin_auth.py) · [CMS controls](../CMS/app/ai_continuous.php) · [Deployment and acceptance procedure](CONTINUOUS_RESEARCH_DEPLOYMENT.md).

## 1. Core Operating Requirements

- [ ] Run a continuous autonomous inference service on the Gentoo compute node. **Partial (source):** Background worker is coded, but not installed on Gentoo.
- [x] Operate without a browser session or active conversation.
- [x] Automatically schedule another research cycle when work finishes.
- [ ] Resume automatically after unexpected crashes, system restarts, or temporary model unavailability. **Partial (source):** SQLite recovery, retry and 100-pass engine-recreation tests pass; real crash/restart and prolonged model outage testing remain.
- [ ] Process **all eligible indexed knowledge**, not merely recent documents. **Partial (source):** PDF ingestion records and saved one-off research snapshots are indexed; other knowledge formats remain.
- [x] Revisit previously analyzed material indefinitely.
- [x] Maintain persistent state across cycles and software updates.
- [x] Allow administrators to start, pause, resume, cancel, and reprioritize research. **Source implementation:** Admin controls include start/pause/resume/stop, source priorities and signed single-job cancellation. Cancellation fences leases, preserves completed passes, survives global resume and permits later full-cycle revisits. It does not forcibly stop in-flight model computation.
- [ ] Allow the system to run continuously while maintaining interactive chatbot availability. **Partial (source):** Interactive-queue admission exists; in-flight inference cannot yet be preempted.
- [x] Never silently modify model weights or promote generated claims into verified facts.

## 2. Complete Knowledge Inventory

- [x] Inventory existing PDF files and extracted PDF sections on Gentoo.
- [ ] Inventory available website snapshots, text documents, and supported research records. **Partial (source):** Stored website/text research snapshots are indexed; general text-file import is not.
- [ ] Inventory existing extracted knowledge, source citations, and research history. **Partial (source):** PDF extracts and existing one-off snapshots are indexed; separately approved CMS knowledge is not.
- [ ] Identify duplicate files through content hashes. **Partial (source):** SHA-256 source identities exist, but a complete duplicate-file report/deduplication policy does not.
- [x] Maintain source IDs, page numbers, document versions, timestamps, and extraction status.
- [x] Detect additions, deletions, and changed source content.
- [ ] Track inaccessible or incompletely extracted files. **Partial (source):** Failed/zero-text ingestion records are counted; files absent from the catalog are not enumerated.
- [x] Queue previously unprocessed material.
- [x] Record the last successful analysis of every eligible section.
- [x] Provide administrator controls for excluding sources or selecting research priorities.

**Completion criterion:** Every eligible source can be accounted for, including sources that failed processing.

## 3. Continuous Research Scheduler

- [x] Build a durable, database-backed autonomous work queue.
- [x] Divide large documents into independently checkpointed sections.
- [x] Assign work according to priority, age, source changes, and coverage.
- [x] Process each section through a configurable number of inference passes.
- [x] Support repeated analysis across multiple research cycles.
- [x] Ensure completed work is checkpointed before proceeding.
- [x] Recover interrupted jobs without losing valid completed results.
- [ ] Prevent duplicate execution of the same job. **Partial (source):** Fencing prevents duplicate commits, but an expired lease can briefly overlap physical inference.
- [x] Detect stalled workers and recover abandoned jobs safely.
- [x] Implement exponential backoff for repeated failures.
- [x] Automatically begin another coverage cycle when eligible work has been completed.
- [x] Preserve a complete audit trail of completed and failed jobs. **Source implementation:** Newly created jobs have append-only source-scoped creation, lease, pass checkpoint, retry/failure, stop/resume, supersession and cancellation events, with admin attribution. Histories can be paged and downloaded as private reports. Older records retain existing events but cannot retroactively acquire omitted lifecycle events; production acceptance remains open.

**Important:** A complete cycle does not mean a source is permanently finished. It becomes eligible for analysis again.

## 4. Multi-Pass Reasoning Engine

For each selected document or document section:

- [ ] Extract factual claims and technical relationships. **Partial (source):** Only narrow numeric measurement lines are extracted directly from PDFs; general relationships are not.
- [ ] Identify assumptions, ambiguities, and missing explanations.
- [ ] Compare the section against related sources. **Partial (source):** Cross-source numeric candidates are compared, not arbitrary related prose.
- [ ] Detect contradictions between documents. **Partial (source):** Narrow equal-scope/equal-unit measurement conflicts are flagged; procedural/semantic contradictions remain.
- [ ] Evaluate calculations, measurements, and units where possible. **Partial (source):** Values and literal units are parsed; conversions and independent calculations are not.
- [ ] Identify procedures that may have become outdated.
- [ ] Generate questions requiring additional evidence.
- [ ] Re-examine earlier conclusions using fresh source context.
- [x] Store each pass separately for later inspection.
- [x] Distinguish verified evidence from model-generated hypotheses.
- [x] Avoid treating repeated agreement between the model's own outputs as independent confirmation.
- [x] Support configurable inference passes, preserving the existing 1–100-pass research functionality.

## 5. Knowledge Verification and Memory

- [x] Create a versioned knowledge-claim database linked to original sources.
- [x] Store exact supporting citations for every promoted factual claim.
- [x] Track contradictory evidence rather than automatically discarding it.
- [x] Separate proposed findings from verified findings.
- [x] Assign verification states: unreviewed, supported, disputed, rejected, and superseded.
- [x] Require source-backed evidence before promoting new factual knowledge.
- [x] Maintain historical versions and reversal capability.
- [ ] Preserve human review controls for safety-critical technical procedures. **Partial (source):** Admin-only original-source confirmation now binds the selected application scope and invalidates human approvals when that scope changes; procedure-specific safety rules and second-reviewer policy are still missing.
- [ ] Make verified knowledge available through the existing retrieval system.
- [x] Avoid overwriting original documents or extracted source material.
- [ ] Detect circular references and unsupported generated claims. **Partial (source):** Model-generated drafts cannot become evidence; cross-source circularity and independent-authorship detection remain.
- [x] Recheck conclusions when source documents change.

**Completion criterion:** The system becomes better at finding and applying verified information without contaminating its evidence base.

## 6. Cross-Document Intelligence

- [ ] Connect related facts across different manuals and knowledge sources. **Partial (source):** Only restricted same-property numeric PDF evidence is currently joined.
- [ ] Identify repeated components, specifications, part numbers, and procedures. **Partial (source):** Numeric measurement labels only; no general part-number/procedure relationship index.
- [ ] Compare manuals for conflicting torque specifications, electrical values, tolerances, and service procedures. **Partial (source):** Torque and other simple numeric lines are compared; service-procedure contradictions are not.
- [x] Preserve model, year, engine, revision, and application boundaries.
- [ ] Detect when apparently contradictory specifications actually apply to different equipment. **Partial (source):** Explicitly mismatched scope prevents false comparisons; the software does not infer equipment applicability.
- [ ] Generate source-linked technical summaries.
- [ ] Identify missing reference documents.
- [ ] Maintain a searchable relationship index for parts, systems, and procedures.
- [ ] Automatically revisit unresolved contradictions as new source material becomes available. **Partial (source):** Source revisions invalidate old candidates; complete automated re-adjudication of all open conflicts is not yet validated.

## 7. Resource and Performance Management

- [x] Detect available CPU, RAM, GPU, and VRAM resources. **Source implementation:** Linux logical CPU count, host MemAvailable and CPU load, AMD amdgpu sysfs and local NVIDIA nvidia-smi VRAM snapshots are available; missing/unsupported GPU counters are reported as unknown. Live GPU-driver acceptance is pending.
- [x] Respect the existing Gentoo inference worker configuration.
- [ ] Reserve resources for interactive chat and existing system services. **Partial (source):** New background work defers to chat and can require a minimum free VRAM threshold at admission for explicit AMD/NVIDIA targets. This is not a GPU allocation/reservation; it cannot preempt or isolate in-flight inference.
- [ ] Allow configurable background inference concurrency.
- [x] Throttle research when resource utilization exceeds configured limits.
- [x] Monitor memory consumption, inference latency, and queue growth. **Source implementation:** Measures the Gentoo input-service process RSS and host RAM; checkpointed pass inference latency; and durable per-minute queued/leased/failed counts with seven-day bounded telemetry and 60-sample admin history. Input-service RSS is not separate llama.cpp process memory or GPU allocation; real hardware acceptance remains in section 11.
- [ ] Track tokens processed per second and per research job.
- [ ] Prevent uncontrolled database growth through configurable retention and archival. **Partial (source):** A manual WAL-consistent private snapshot/restore utility and a verified read-only cold-history catalog, source/job locator and bounded provenance browser are implemented and tested. The private SQLite admission ceiling blocks new background work near a configured footprint threshold; operational telemetry expires after seven days. A metadata-only offline retention preview **does not authorize any pruning**. Selective offload/deletion with recoverable primary indexes, archive capacity limits, independent PDF-ingest backups and live restore acceptance are still outstanding.
- [x] Avoid continuously reprocessing unchanged material at unnecessarily high priority. **Source implementation:** Targeted cycles already exclude unchanged completed revisions until configured revisit; when a full cycle is due, previously completed unchanged sections rank behind unseen/revised sections of equal admin priority, while an operator's explicitly higher source priority wins. Full revisits remain indefinite.
- [ ] Support quiet hours, power-management policies, and operator overrides. **Partial (source):** UTC quiet hours, CPU/RAM/VRAM admission settings and admin overrides exist; power-management policy and energy budgets are missing.
- [ ] Track resource consumption and estimated energy costs.

## 8. Administrator Dashboard

Add a **Continuous Research** section to the existing SirusSofts AI administration interface.

- [x] Master enable/disable control.
- [x] Start, pause, resume, and stop controls.
- [x] Active research job and currently selected source.
- [x] Current document, page, section, and inference pass.
- [x] Coverage cycle number and progress. **Source implementation:** Cycle ID, scoped plan, completed/total passes, failure count and pass percentage are displayed; live acceptance remains in section 11.
- [x] Total indexed documents and sections.
- [x] Processed, pending, failed, and excluded counts.
- [x] Estimated processing throughput. **Source implementation:** Measured inference durations and completed passes feed a labeled inference-only estimate; queue delays/GPU contention are excluded.
- [x] Resource utilization and active worker status. **Source implementation:** Admin status shows the active worker, CPU load, host RAM headroom and supported AMD/NVIDIA VRAM total/used/free snapshots, with missing counters clearly unknown. Hardware acceptance remains outstanding.
- [x] Recently verified findings. **Source implementation:** A separate admin feed displays only current human-supported original-PDF quotations, with original source/page/part, applicability and reviewer. It excludes disputed/rejected/superseded claims. A verified quotation is not necessarily a safe repair procedure; live acceptance remains open.
- [x] Outstanding contradictions and review requests.
- [x] Source-specific research histories. **Source implementation:** Admin-only signed source history lists recent jobs and pages through all stored pass results, including superseded revisions and failures.
- [x] Research priorities and scheduling configuration.
- [x] Downloadable research reports. **Source implementation:** Explicit admin-only JSON download of the selected eight-pass history page, with citations and unreviewed draft labels. Full-source bulk export is not included.
- [x] Visible explanations when jobs are blocked or repeatedly failing.

## 9. Safety, Access, and Reliability

- [x] Keep model inference and internal research databases on private infrastructure.
- [x] Continue using the existing secured Gentoo-to-Ubuntu communication architecture.
- [x] Preserve administrator-only job management.
- [x] Enforce authentication, authorization, and CSRF protections.
- [x] Treat documents and websites as untrusted input, not instructions to the research controller.
- [x] Prevent unauthorized network requests and local file access.
- [ ] Set bounded job limits, source sizes, and timeouts. **Partial (source):** Section sizes, pass counts, retries and API request limits are bounded; hard limits on all model calls require validation.
- [ ] Protect credentials and private source data from generated reports. **Partial (source):** Reports are explicitly downloaded only by authorized administrators, are not stored in a public directory, and carry unreviewed/possibly private notes. Automatic secret scanning/redaction and report-classification policy remain.
- [ ] Maintain database backup and restore procedures. **Partial (source):** Backup-backed upgrades and a private consistent snapshot/hash/integrity verifier/new-file restore tool now have synthetic SQLite WAL recovery tests. Separate PDF-ingest and Ubuntu CMS database backups and full live restore acceptance are still outstanding.
- [x] Provide safe shutdown and restart behavior.
- [ ] Test job interruption, database corruption recovery, and queue resumption. **Partial (source):** Synthetic restart tests pass, but database-corruption recovery has not been tested.
- [ ] Maintain detailed operational logging without exposing secrets. **Partial (source):** Private job and event tables exist; operational audit/retention and log coverage are incomplete.

## 10. Optional Future Model Training

This phase is separate from continuous inference.

- [ ] Build curated training examples from verified, source-backed findings. **Partial (source, 2026-11-22):** The offline mechanics candidate builder selects only current human-supported original PDF quotations, rechecks them against the independent private PDF ingest catalog, preserves citation and applicability, and exports private source-grouped JSONL candidates. It does not approve individual training examples, cover other disciplines, or train weights.
- [ ] Remove duplicated, contradictory, or unsupported training material. **Partial (source, 2026-11-22):** Numeric mechanics candidates skip duplicated same-scope properties, open conflicts, unreviewed drafts, mismatched original quotations, superseded claims and scope changes. Full-corpus near-duplicate filtering and semantic contradiction detection remain.
- [ ] Establish evaluation datasets and regression benchmarks. **Partial (source, 2026-11-22):** Offline mechanics-only evaluation now generates separated private answer keys and held-out question sets from checksum-verified reviewed PDF candidates, and scores exact decimal values/units, application boundaries, original citations, abstentions and baseline-versus-candidate changes. Synthetic tests cover leakage, tampering, invalid values and unsafe applicability. No real model run, multi-domain benchmark, held-out programming/science reasoning, or live accuracy result is established.
- [ ] Determine which locally supported models and hardware can handle adapter fine-tuning.
- [ ] Evaluate LoRA/QLoRA or other practical fine-tuning methods. **Partial (source):** An offline opt-in QLoRA runner exists outside this autonomous service; no production training acceptance.
- [ ] Train offline on controlled, versioned snapshots—not continuously on raw generated output.
- [ ] Test candidate adapters against the existing production model.
- [ ] Preserve the ability to reject or roll back degraded models. **Partial (source):** Separate controlled training validation/rollback is future work.
- [x] Keep fine-tuning disabled until separately approved.

**New source-only increment:** [Reviewed training candidate utility and documented safety limits](REVIEWED_TRAINING_CANDIDATES.md). Default is read-only preview; explicit private output never changes the live database, training models or Ubuntu/Gentoo installations. A source-approved quotation is not yet a training-approved example and does not prove competency. No Phase E checkbox is newly closed.

**Latest evaluation increment:** [Offline held-out mechanics benchmark](REVIEWED_TRAINING_CANDIDATES.md#held-out-mechanics-benchmark-source-only) and [synthetic scoring tests](../sirus-ai-dual-tunnel/tests/test_learning_eval.py) added in GitHub source only. Training remains disabled; questions and private answer keys are separated to prevent answer leakage, and no model has been scored. The evaluator currently measures reviewed numeric mechanics facts, not general comprehension.

**Dataset admission guard:** The optional offline QLoRA loader now refuses rows explicitly marked `candidate_only`, with regression coverage. Review/approval must occur through a separate operator workflow before a research candidate can become a permitted training example. This input check is not tamper-proof authorization, and no autonomous training or promotion exists.

**Guiding principle:** Continuous inference and knowledge-base refinement come first. Model-weight training is a later, independently validated capability.

## 11. Testing and Acceptance Criteria

- [ ] Verify complete coverage of a selected multipage PDF.
- [ ] Verify a 100-pass job resumes after interruption. **Partial (source):** Automated 100-pass restart-by-engine-recreation test passed; real process-kill recovery test remains.
- [ ] Verify continuous cycles restart without manual interaction. **Partial (source):** Synthetic recurring-cycle tests pass; real unattended endurance test remains.
- [ ] Verify no sections are permanently skipped. **Partial (source):** Chunk coverage checks and >32-measurement no-truncation tests pass; live full-library proof remains.
- [ ] Verify duplicate jobs do not execute concurrently. **Partial (source):** Atomic-claim/fencing tests pass; physical inference overlap after lease expiry needs testing.
- [ ] Verify citations always map to the correct source and page. **Partial (source):** Tests check page/part source metadata; representative real manuals still need audit.
- [ ] Verify unsupported conclusions cannot enter the verified knowledge store. **Partial (source):** Database rejects promoting drafts and checks human-backed originals; live acceptance remains.
- [ ] Verify changed documents trigger targeted re-analysis. **Partial (source):** Synthetic tests confirm prompt targeted cycles for revised sections, exclusion of unchanged revisions, durable cycle scope and later full revisit; real corpus acceptance remains.
- [ ] Verify interactive AI chat remains available during background work. **Partial (source):** Chat priority admission is implemented; measured peak-load latency is missing.
- [ ] Verify pause, cancellation, and restoration behavior. **Partial (source):** Synthetic unit tests now cover single-job cancellation, fencing, audit immutability and resume/restart persistence; actual service interruption and live recovery acceptance remain.
- [ ] Verify that an administrator can inspect and reverse promoted findings. **Partial (source):** Unit tests cover admin review/reversal; real CMS/Gentoo acceptance remains.
- [ ] Test database migration, backup, restore, and rollback. **Partial (source):** Additive migrations tested; consistent backup/restore and rollback not yet performed.

## 12. Safe Implementation and Deployment

- [x] **Superseded by repository policy:** ~~Create a dedicated GitHub development branch.~~ Work directly on `main` (root `README.md`).
- [x] Review existing research-loop services and reuse working components.
- [x] Implement changes in small, independently testable increments.
- [x] Add or update automated unit and integration tests.
- [x] Run GitHub CI and confirm relevant tests pass.
- [x] Review security and source-isolation boundaries.
- [x] Prepare a pinned-commit deployment plan.
- [ ] Run read-only deployment checks before changing installed services. **Partial (source):** A reviewed in-place deployment runbook exists; the operator must select and pin a green SHA for a maintenance window.
- [ ] Use reviewed in-place upgrades rather than full server installers. **Partial (source):** Read-only --plan workflows exist; they have not been run on the live hosts.
- [ ] Preserve the existing CMS, PDF library, AI databases, and model files. **Partial (source):** In-place upgrade scripts are prepared; no live upgrade has occurred.
- [ ] Retain automatic rollback and database backups. **Partial (source):** Source changes leave originals untouched; deployed-state preservation needs real validation.
- [ ] Validate worker and gateway health after each deployment.
- [ ] Begin with a controlled small-library trial.
- [ ] Expand gradually to full-library continuous operation after successful validation.

---

## Implementation Priority

**Phase A — Foundation:** Inventory, coverage tracking, scheduling, and recovery.

**Phase B — Intelligence:** Repeated analysis, cross-document comparisons, and contradiction detection.

**Phase C — Verified Knowledge:** Provenance, approval rules, versioned knowledge, and retrieval integration.

**Phase D — Autonomous Operation:** Resource-aware indefinite research, monitoring, and administrator controls.

**Phase E — Advanced Learning:** Optional curated model training, benchmarking, and controlled model upgrades.

## Definition of Done

The system can operate without an active user session, repeatedly work through the entire eligible knowledge library, preserve its progress, investigate conflicting evidence, maintain a verified and versioned knowledge base, and continue functioning after service interruptions.

The research service must not interfere with normal website operations, expose private data, or silently modify the underlying model.

**Target outcome:** An autonomous, continuously researching SirusSofts knowledge system that improves its source-grounded understanding while retaining administrator oversight and operational control.

---

### Continued implementation: private visibility, reports and targeted cycles (source only)

The scheduler now records inference duration and output character counts for each newly completed pass, retaining old results with unknown duration 0 through an additive SQLite migration. The CMS shows measured completed passes in the last hour, average inference duration, and a clearly labeled **estimated uninterrupted inference-only** pass rate. No token/sec performance claim is inferred from output character counts.

A completed cycle closes in the final checkpoint transaction. After a cycle closes, newly discovered/changed/uncovered source revisions enter an immediately eligible **targeted cycle**; unchanged source revisions wait for the normal revisit interval. The cycle mode (`full` or `new_only`) persists in SQLite across restarts. Historic pass results and source revisions are never erased. Regression tests verify two changed sources and one unchanged source through a new engine instance and check upgrades from the older cycle and results tables.

The existing administrator-only `/admin/ai` now has source-specific histories: selected original-source identifier, recent job states/retry error types, and **all recorded pass results through pagination (eight at a time)**, including old/superseded revisions. The new internal history route requires the same signed, timestamped, replay-resistant CMS bridge as the controls. PDF text is not copied to Ubuntu. The operator may explicitly download a private JSON research report for the current eight-pass page, retaining source hashes, page/part labels, notes, measured duration, and unequivocal *unreviewed* status. This download is **not** an approved knowledge export, does not enter training, and can contain private or sensitive model-generated notes; it requires careful local handling. No secret-redaction guarantee is claimed.

**Still open:** Full-source one-file report bundles; role-specific source-approval policies; export redaction/scanning; reliable token/sec metrics; process/GPU/VRAM telemetry; actual service-restart and full-library load testing. These remain unchecked above.

## Implementation evidence and unresolved engineering gates

The complete 12-section checklist above is the **single source of progress tracking**. The following engineering notes are retained from the previously published source roadmap so that prior implementation detail and acceptance limitations are not lost. Their descriptions indicate source-code status, not live approval.

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

**Implemented:** `sirus-ai-dual-tunnel/gentoo/research_evidence.py` indexes strictly formatted measurement lines (for example, `Rear axle torque: 40 Nm`) directly from original PDF extracted sections. It records page, part, stable section ID, source content revision, exact bounded quote, SHA-256 hash, numeric value and original unit. Ineligible/removed sections are ignored. It never treats LLM notes or prior model passes as source material. Source text remains on Gentoo.

An administrator can assign an explicit applicability key such as `brp/defender/2026/hd11/clutch`; unscoped documents are **never automatically compared**. Only equal normalized property labels, equal literal units, equal applicability keys and different source document identities produce `cr_conflicts` candidates. A durable paginated comparison cursor covers pairs rather than truncating to a hard per-document peer limit. Differences stay `unreviewed` until the administrator inspects them; dismissed or superseded candidates keep their history.

**Not implemented:** General text/diagram and procedural extraction, unit conversion, reliable part-number/entity alignment, source-authorship independence determination, applicability inference, automatic model/year mapping and semantic contradiction classification. Explicitly different source SHA-256 values **do not** establish independent authorship. Contradictions are *review requests*, not claims that either manual is wrong.

**Measurable acceptance:** For a curated three-document suite containing equal and unequal specifications, identical source IDs cannot form a cross-document conflict; different model/year scopes prevent false comparisons; mismatched units do not compare silently; source changes invalidate prior candidates. Complete comparison pagination and the original PDF citations must be checked on a private test corpus before broader scope expansion.

## Phase C: Private versioned review ledger — partial

**Implemented:** `cr_evidence`, `cr_claims`, `cr_claim_versions` and comparison records are additive, private Gentoo SQLite tables. The first proposal remains unreviewed until an authenticated CMS administrator submits a CSRF-checked, rate-limited review action from `/admin/ai`. To record `supported`, the administrator must type `I VERIFIED ORIGINAL`, and Gentoo rechecks the exact original PDF quote, content revision, ingestion status, and existing independent PDF approval. Support, dispute, rejection and supersession append immutable claim revisions with the reviewer identity. Database triggers reject update/delete of prior history. Changed or excluded source revisions automatically supersede previously supported claims while retaining older history. Model inference cannot call the approval path.

**Critical evidence semantics:** A supported record means **the human checked that the quoted statement exists in an approved original source**. It is not proof that a service procedure is safe/correct in every application. Repeated LLM analysis is never independent evidence.

**Not implemented:** New approved claims are **not** written into existing `cms_ai_documents`, exported as verified RAG knowledge, or added to training datasets. That integration requires explicit transfer, licensing, source scope checks, human approval policy, and retrieval rollback. Cross-document human corroboration and richer claim semantic models remain pending.

**Measurable acceptance:** The new PHP/Python tests enforce administrator-only actions, explicit review confirmation, exact SHA/page/part provenance, rejection of stale or unapproved originals, immutable review history, reversible human decisions and automatic source-revision supersession. A controlled source update and restoration test is required against the real private library.

## Subsequent integrity guard: application-scope reapproval (source only)

A manual's model/year/engine/application scope is part of the provenance of a human-supported statement. Changing its scope now requires an authenticated CMS administrator identity and creates a new immutable `cr_scope_versions` entry with old/new scope and actor. Before changing any indexed measurement applicability, the operation atomically moves existing reviewed claims to **superseded** with a `system:scope-change` attribution, retaining all prior claim versions and their original applicability. A repeated same-scope request is a no-op.

Review forms now bind an explicit `expected_scope` alongside the source excerpt hash/revision; saving a stale browser form after another administrator changes scope is rejected. The private CMS evidence panel displays the latest scope change history. Legacy claim history gains an additive applicability field with unknown/blank historical defaults, and further old/new scope comparisons conservatively invalidate mismatches.

**Still open:** Independent-authorship/cross-manual corroboration, procedure-specific dual control, live privacy/security validation, and approval-to-RAG transfer. Supported continues to mean that a human located the literal statement in an already approved original PDF, **not** that it is safe to use in every application.

## Phase D: Unattended resource management — partial

A persistent, opt-in Gentoo background thread executes one research pass at a time and defers new work if active/queued chat or manually requested research is present. Optional one-minute CPU load cap, minimum available RAM, explicit GPU VRAM threshold and UTC quiet-hours admission limits apply. Bounded deterministic evidence indexing/comparison runs in smaller batches when no chat is active. Queue, cycle, lease, errors, worker, CPU load and RAM headroom are observable.

**Remaining:** Process-specific GPU/VRAM allocation or reservation, power limits, source retention/archival, multi-node coordination, chat preemption and real benchmark-backed latency SLAs. Minimum free RAM uses the Linux MemAvailable host metric and is not equivalent to reserving a model's VRAM. The VRAM guard is disabled by default; when enabled it requires an explicit AMD or NVIDIA worker, denies auto/CPU targeting, and fails closed for unavailable or low GPU counters. It reads local AMD sysfs metrics or a timeout-bounded, fixed-path NVIDIA command, but cannot reserve VRAM or ensure the worker stays on the same GPU. Quiet hours are in UTC, not inferred local time. The current gate protects **admission of new work**, not necessarily a chat request that arrives during an already-running inference.

**Measurable gate:** Two long cycles and peak interactive load without OOM, data loss, unacceptable latency, or unbounded storage growth.

## Continued implementation: targeted cancellation, lifecycle audit, verified quotation display (source only)

**Single-job cancel:** CMS administrators explicitly confirm cancellation for one fixed-format private job ID, with identity derived from their session. Gentoo validates job and actor through the existing signed control bridge, records an immutable cancellation marker, fences the lease, and retains all previously checkpointed drafts. Global Resume does not revive an intentionally cancelled job, and targeted cycles do not immediately recreate it. A later full revisit cycle may issue a new job for that source. Cancellation does **not** forcibly interrupt a running inference request: any in-flight model call may finish computing, but its old lease cannot store additional results.

**Durable audit:** New jobs now record creation, lease, pass checkpoints, failures, lease expiration, cancellations, stops/resumes and source supersession in append-only private SQLite audit events. HMAC-protected source history has independent bounded pagination for passes (eight) and events (20), including export of the current private page. Historic records from earlier code retain existing events but cannot reconstruct missing transitions. Source text and model drafts are not inserted into audit event details, though downloaded pass reports still contain private unreviewed model text.

**Current human-supported findings:** The evidence dashboard now has a dedicated feed of supported quotes drawn from current approved original PDFs, with source identity, page, part, applicability, hash and human reviewer. Disputed, rejected, superseded or changed-scope entries do not appear. A human-supported quote means the literal source statement was checked, **not** that a procedure is correct in all model/year/equipment contexts.

**Unfinished:** Live Gentoo/Ubuntu installation, real process-kill and service restoration tests, in-flight inference preemption, corpus-level source-author independence, reviewed-claim retrieval integration, sanitized report policy and approved retention/archival for growing immutable logs.

## Continued implementation: bounded telemetry, database growth and offline archive

**Operational resource telemetry:** Gentoo stores one bounded sample per UTC minute while continuous research runs and on private admin status reads: queued/leased/failed counts, Python input-service RSS, host MemAvailable, and private input SQLite file + WAL + SHM size. Samples are retained for seven days and **are not evidence**; pruning their history never modifies immutable claims, job audit events, source records or research drafts. The dashboard shows up to 60 samples and queue-count delta. The RSS metric does not cover independent llama.cpp processes. Pass inference latency excludes queue delays and GPU contention.

**Source freshness:** Within an administrator priority tier, new or revised sections are preferred over already completed unchanged revisions. Operator-assigned higher priority still takes precedence; full coverage cycles continue revisiting previously analyzed original sources after the configured interval.

**Optional private input database ceiling:** Administrators may set `max_database_mib` (0 disables). Before new background scheduling, the controller measures private input SQLite, WAL and SHM bytes. At the threshold, or if size is unavailable, it refuses **new autonomous research leases** without deleting history or blocking regular chat. This is an admission gate, not guaranteed disk quota: active inference or other chat writes can still grow storage.

**Verified offline archive utility:** `sirus-ai-dual-tunnel/gentoo/research_archive.py` creates a complete SQLite backup using the SQLite backup API into an explicit private mode-0700 directory and publishes mode-0600 archives plus SHA-256, integrity check and row-count manifests. It can verify or restore to a **new private file**, never overwrite a live database. This is a manual local operator CLI, not a public CMS API. Snapshots include the full private input SQLite database, including chats and unreviewed draft outputs, but **do not** include the separate PDF-ingest database or Ubuntu CMS. The source is not pruned. Synthetic tests include WAL consistency, hash tampering, symlink/permission denial and no-overwrite restoration.

**Still open:** Complete archival retention requires selective provenance-preserving offload, restore/rollback, reliable query across old archives, bounded total archive storage, separately secured source PDF backups and real server acceptance. Snapshots are copies, not compaction.
## Continued implementation: verified cold-history retrieval and non-destructive retention planning

Source-only, no deployment: a separate local operator CLI, `sirus-ai-dual-tunnel/gentoo/research_cold_history.py`, can scan a private snapshot directory containing at most 64 full SQLite archives, independently verify every manifest/hash/row count/integrity check, and **fail closed** on corrupt, missing or mismatched snapshots. It can locate an exact 64-hex PDF or 32-hex previous-research source ID, or an exact 32-hex job ID, across all verified snapshots. Repeated snapshots containing identical historical results are **not independent evidence**.

Once an individual private snapshot is selected, the operator can page through original document provenance, source revision, original page/part, per-pass SHA-256 hashes and review_state (8 results/page), plus immutable audit events independently (20/page). Claim-version details retain original quote hash, reviewer, applicability, and original source revision, including historical reversed reviews. All access is local read-only (`mode=ro&immutable=1`); verification itself no longer creates SQLite WAL sidecars. Private unreviewed generated notes are omitted unless an explicit `--include-private-drafts` option is chosen, and even those notes remain **unreviewed**, not approved knowledge.

The offline `retention-preview --before-unix` command reports how many closed-cycle, old-revision terminal jobs and their checkpointed passes exist in that one snapshot. Its output explicitly sets `pruning_permitted=false`: it is **not a deletion plan** and never modifies the live input DB, immutable events, original PDFs or model weights. It does not estimate trustworthy reclaimable bytes and does not establish that any supported original claim may be removed. Cold snapshots remain separate complete database copies, not a compacted/archived primary store.

The reviewed Gentoo in-place upgrade source now includes both the existing private backup CLI and cold-history inspector in its allowlisted module list, subject to the existing pinned-commit checks and operator confirmation; no installer or service was executed here. A complete plan for selective archival, retained query indexes, authenticated storage, bounded total archive growth, and live recovery remains required before checking off the roadmap retention requirement.

## Phase E: Training — intentionally separate

No autonomous LoRA or model-weight changes. Future training requires curated **human-reviewed** references, duplicate filtering, independent evaluation, explicit model checkpoint control, an offline run and rollback. Disabled until separately approved.

## Validation and safe deployment

- Python: python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_*.py' -v (including test_continuous_research.py and test_research_evidence.py)
- PHP: php CMS/tests/ai_continuous_test.php, php CMS/tests/ai_evidence_test.php, php CMS/tests/ai_research_test.php and the full existing CMS suite
- CI: .github/workflows/sirus-ai-dual-tunnel.yml and .github/workflows/php-framework.yml, including Python syntax, PHP lint, serious undefined-name and shell analysis
- Source tests also cover application-scope change auditing, human-approval supersession, stale review-form rejection, old claim-history migration, AMD/NVIDIA telemetry parsing, unknown/invalid counters, fail-closed admission, and legacy GPU config migration.
- Source tests now additionally cover scoped single-job cancellation and actor attribution, future-cycle eligibility, immutable cancellation/audit rows, retries and terminal failure audit, source-isolated history pagination, and current human-supported quote filtering.
- Source tests additionally cover WAL-consistent private archives and new-file recovery, tamper/permission failures, fail-closed SQLite growth admission, bounded queue/RSS telemetry, and new-revision priority with administrator override.
- Cold-history tests verify source/job provenance across immutable snapshots, independent pass/audit pagination, human review-version citations, default redaction of generated drafts, explicit opt-in private access, corrupted snapshot rejection, cross-snapshot source discovery, and non-destructive retention preview.
- Source tests cover inventory completeness, long-section coverage, additive migration preservation, content changes, duplicate prevention, leases/fencing, retries, cycle eligibility, pauses, source exclusion, chat admission, RAM and UTC quiet-hours gates, deterministic evidence indexing, scope-limited contradiction candidates, stale original rejection, append-only human review/reversal, HMAC/nonce validation and real unsigned-localhost route rejection.

**Safe installation plan:** docs/CONTINUOUS_RESEARCH_DEPLOYMENT.md. Pin and review a green main SHA; use existing backup-backed in-place upgrade scripts, not the old full installers. No Ubuntu/Gentoo apply, service restart, model change, or destructive storage operation has been done during this implementation.

**Full-roadmap definition of done (NOT met):** End-to-end multi-format inventory, validated repeat cycles after outages, deeper independent cross-document reasoning, reviewed-claim retrieval integration and rollback, measured interactive responsiveness, monitoring and archival, plus explicit operator acceptance on both servers. All optional LoRA training remains disconnected and disabled.

