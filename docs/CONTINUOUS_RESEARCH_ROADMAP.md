# SirusSofts — Continuous Autonomous Research & Learning Roadmap

**Status:** Approved development roadmap — implementation pending  
**Priority:** High  
**Target:** Future development, beginning with the next available deployment window  
**Platform:** Gentoo local AI workers + Ubuntu public CMS gateway  
**Objective:** Enable the local LLM to continuously revisit, analyze, cross-reference, and improve its understanding of the entire available knowledge library without requiring a user to initiate each research session.

## 1. Core Operating Requirements

- [ ] Run a continuous autonomous inference service on the Gentoo compute node.
- [ ] Operate without a browser session or active conversation.
- [ ] Automatically schedule another research cycle when work finishes.
- [ ] Resume automatically after unexpected crashes, system restarts, or temporary model unavailability.
- [ ] Process **all eligible indexed knowledge**, not merely recent documents.
- [ ] Revisit previously analyzed material indefinitely.
- [ ] Maintain persistent state across cycles and software updates.
- [ ] Allow administrators to start, pause, resume, cancel, and reprioritize research.
- [ ] Allow the system to run continuously while maintaining interactive chatbot availability.
- [ ] Never silently modify model weights or promote generated claims into verified facts.

## 2. Complete Knowledge Inventory

- [ ] Inventory existing PDF files and extracted PDF sections on Gentoo.
- [ ] Inventory available website snapshots, text documents, and supported research records.
- [ ] Inventory existing extracted knowledge, source citations, and research history.
- [ ] Identify duplicate files through content hashes.
- [ ] Maintain source IDs, page numbers, document versions, timestamps, and extraction status.
- [ ] Detect additions, deletions, and changed source content.
- [ ] Track inaccessible or incompletely extracted files.
- [ ] Queue previously unprocessed material.
- [ ] Record the last successful analysis of every eligible section.
- [ ] Provide administrator controls for excluding sources or selecting research priorities.

**Completion criterion:** Every eligible source can be accounted for, including sources that failed processing.

## 3. Continuous Research Scheduler

- [ ] Build a durable, database-backed autonomous work queue.
- [ ] Divide large documents into independently checkpointed sections.
- [ ] Assign work according to priority, age, source changes, and coverage.
- [ ] Process each section through a configurable number of inference passes.
- [ ] Support repeated analysis across multiple research cycles.
- [ ] Ensure completed work is checkpointed before proceeding.
- [ ] Recover interrupted jobs without losing valid completed results.
- [ ] Prevent duplicate execution of the same job.
- [ ] Detect stalled workers and recover abandoned jobs safely.
- [ ] Implement exponential backoff for repeated failures.
- [ ] Automatically begin another coverage cycle when eligible work has been completed.
- [ ] Preserve a complete audit trail of completed and failed jobs.

**Important:** A complete cycle does not mean a source is permanently finished. It becomes eligible for analysis again.

## 4. Multi-Pass Reasoning Engine

For each selected document or document section:

- [ ] Extract factual claims and technical relationships.
- [ ] Identify assumptions, ambiguities, and missing explanations.
- [ ] Compare the section against related sources.
- [ ] Detect contradictions between documents.
- [ ] Evaluate calculations, measurements, and units where possible.
- [ ] Identify procedures that may have become outdated.
- [ ] Generate questions requiring additional evidence.
- [ ] Re-examine earlier conclusions using fresh source context.
- [ ] Store each pass separately for later inspection.
- [ ] Distinguish verified evidence from model-generated hypotheses.
- [ ] Avoid treating repeated agreement between the model's own outputs as independent confirmation.
- [ ] Support configurable inference passes, preserving the existing 1–100-pass research functionality.

## 5. Knowledge Verification and Memory

- [ ] Create a versioned knowledge-claim database linked to original sources.
- [ ] Store exact supporting citations for every promoted factual claim.
- [ ] Track contradictory evidence rather than automatically discarding it.
- [ ] Separate proposed findings from verified findings.
- [ ] Assign verification states: unreviewed, supported, disputed, rejected, and superseded.
- [ ] Require source-backed evidence before promoting new factual knowledge.
- [ ] Maintain historical versions and reversal capability.
- [ ] Preserve human review controls for safety-critical technical procedures.
- [ ] Make verified knowledge available through the existing retrieval system.
- [ ] Avoid overwriting original documents or extracted source material.
- [ ] Detect circular references and unsupported generated claims.
- [ ] Recheck conclusions when source documents change.

**Completion criterion:** The system becomes better at finding and applying verified information without contaminating its evidence base.

## 6. Cross-Document Intelligence

- [ ] Connect related facts across different manuals and knowledge sources.
- [ ] Identify repeated components, specifications, part numbers, and procedures.
- [ ] Compare manuals for conflicting torque specifications, electrical values, tolerances, and service procedures.
- [ ] Preserve model, year, engine, revision, and application boundaries.
- [ ] Detect when apparently contradictory specifications actually apply to different equipment.
- [ ] Generate source-linked technical summaries.
- [ ] Identify missing reference documents.
- [ ] Maintain a searchable relationship index for parts, systems, and procedures.
- [ ] Automatically revisit unresolved contradictions as new source material becomes available.

## 7. Resource and Performance Management

- [ ] Detect available CPU, RAM, GPU, and VRAM resources.
- [ ] Respect the existing Gentoo inference worker configuration.
- [ ] Reserve resources for interactive chat and existing system services.
- [ ] Allow configurable background inference concurrency.
- [ ] Throttle research when resource utilization exceeds configured limits.
- [ ] Monitor memory consumption, inference latency, and queue growth.
- [ ] Track tokens processed per second and per research job.
- [ ] Prevent uncontrolled database growth through configurable retention and archival.
- [ ] Avoid continuously reprocessing unchanged material at unnecessarily high priority.
- [ ] Support quiet hours, power-management policies, and operator overrides.
- [ ] Track resource consumption and estimated energy costs.

## 8. Administrator Dashboard

Add a **Continuous Research** section to the existing SirusSofts AI administration interface.

- [ ] Master enable/disable control.
- [ ] Start, pause, resume, and stop controls.
- [ ] Active research job and currently selected source.
- [ ] Current document, page, section, and inference pass.
- [ ] Coverage cycle number and progress.
- [ ] Total indexed documents and sections.
- [ ] Processed, pending, failed, and excluded counts.
- [ ] Estimated processing throughput.
- [ ] Resource utilization and active worker status.
- [ ] Recently verified findings.
- [ ] Outstanding contradictions and review requests.
- [ ] Source-specific research histories.
- [ ] Research priorities and scheduling configuration.
- [ ] Downloadable research reports.
- [ ] Visible explanations when jobs are blocked or repeatedly failing.

## 9. Safety, Access, and Reliability

- [ ] Keep model inference and internal research databases on private infrastructure.
- [ ] Continue using the existing secured Gentoo-to-Ubuntu communication architecture.
- [ ] Preserve administrator-only job management.
- [ ] Enforce authentication, authorization, and CSRF protections.
- [ ] Treat documents and websites as untrusted input, not instructions to the research controller.
- [ ] Prevent unauthorized network requests and local file access.
- [ ] Set bounded job limits, source sizes, and timeouts.
- [ ] Protect credentials and private source data from generated reports.
- [ ] Maintain database backup and restore procedures.
- [ ] Provide safe shutdown and restart behavior.
- [ ] Test job interruption, database corruption recovery, and queue resumption.
- [ ] Maintain detailed operational logging without exposing secrets.

## 10. Optional Future Model Training

This phase is separate from continuous inference.

- [ ] Build curated training examples from verified, source-backed findings.
- [ ] Remove duplicated, contradictory, or unsupported training material.
- [ ] Establish evaluation datasets and regression benchmarks.
- [ ] Determine which locally supported models and hardware can handle adapter fine-tuning.
- [ ] Evaluate LoRA/QLoRA or other practical fine-tuning methods.
- [ ] Train offline on controlled, versioned snapshots—not continuously on raw generated output.
- [ ] Test candidate adapters against the existing production model.
- [ ] Preserve the ability to reject or roll back degraded models.
- [ ] Keep fine-tuning disabled until separately approved.

**Guiding principle:** Continuous inference and knowledge-base refinement come first. Model-weight training is a later, independently validated capability.

## 11. Testing and Acceptance Criteria

- [ ] Verify complete coverage of a selected multipage PDF.
- [ ] Verify a 100-pass job resumes after interruption.
- [ ] Verify continuous cycles restart without manual interaction.
- [ ] Verify no sections are permanently skipped.
- [ ] Verify duplicate jobs do not execute concurrently.
- [ ] Verify citations always map to the correct source and page.
- [ ] Verify unsupported conclusions cannot enter the verified knowledge store.
- [ ] Verify changed documents trigger targeted re-analysis.
- [ ] Verify interactive AI chat remains available during background work.
- [ ] Verify pause, cancellation, and restoration behavior.
- [ ] Verify that an administrator can inspect and reverse promoted findings.
- [ ] Test database migration, backup, restore, and rollback.

## 12. Safe Implementation and Deployment

- [ ] Create a dedicated GitHub development branch.
- [ ] Review existing research-loop services and reuse working components.
- [ ] Implement changes in small, independently testable increments.
- [ ] Add or update automated unit and integration tests.
- [ ] Run GitHub CI and confirm relevant tests pass.
- [ ] Review security and source-isolation boundaries.
- [ ] Prepare a pinned-commit deployment plan.
- [ ] Run read-only deployment checks before changing installed services.
- [ ] Use reviewed in-place upgrades rather than full server installers.
- [ ] Preserve the existing CMS, PDF library, AI databases, and model files.
- [ ] Retain automatic rollback and database backups.
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
