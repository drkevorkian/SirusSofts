# Offline-first SirusSofts AI knowledge and training — implementation contract

**Status: implementation specification only. Not deployed, not an operational training queue.**
**Requested behavior:** An administrator uploads about 20 mechanic PDFs (or a dictionary), selects a subject, starts local processing, watches verified progress, reviews content, and can immediately query approved material from the local Gentoo browser with **zero internet**. No paid API, external LLM, cloud embedding, or automatic outbound model call is allowed for this workflow.

## What exists in this repository today

- `CMS/app/ai_imports.php`: administrator-authenticated private PDF staging. Up to ten files per POST, up to 200 MiB *aggregate* and 200 MiB *per file*. It explicitly **does not** process or transfer uploads to Gentoo.
- `sirus-ai-dual-tunnel/gentoo/manual_ingest.py`: local PDF page extraction via Poppler, hash-tagged JSONL excerpts, marked unreviewed. Scanned pages, tables and diagrams require inspection.
- `sirus-ai-dual-tunnel/gentoo/knowledge.py`: BM25-like, local-only, reviewed-file retrieval injected into prompts; 16 MiB, 3,000-record **single-file limit**, which cannot be assumed to hold twenty manuals.
- `sirus-ai-dual-tunnel/gentoo/manual_summary.py`: optional summary through a *loopback* llama.cpp worker; summaries are drafts, not facts.
- `CMS/app/ai_training.php`: profiles, review workflow, JSONL export; **does not** dispatch training.
- `sirus-ai-dual-tunnel/training/qlora_train.py`: explicit local opt-in adapter-training CLI, requires original checkpoint, suitable NVIDIA training hardware and offline dependencies; current 6 GiB NVIDIA / 8 GiB AMD inference hardware is not a validated Qwen 7B QLoRA training setup.
- `sirus-ai-dual-tunnel/ubuntu/web/app.js`: HTTPS-only remote chat frontend. It is not a local offline browser endpoint; currently renders raw text, including LaTeX, as plain text.

## Product contract

### Admin controls under `/admin/ai`

- Library profiles: mechanics, coding, science, physics, time, relativity, finance, plus admin-created subjects with explicit allowlisted identifiers.
- Multiple file import: PDF, plain text, Markdown and structured dictionary formats (CSV/JSON); choose profile, collection, title and source/license metadata. **No URL fetch** during offline ingestion. Accept batches of at most ten PDFs per HTTP request today, allow subsequent uploads into the same collection, so 20 PDFs can be two or more batches.
- Separate actions: **Stage**, **Extract and index locally**, **Review**, **Make available to AI**, **Generate draft training examples**, **Train adapter** (disabled until capability check), **Evaluate**, **Activate validated adapter**, **Rollback**. Never label retrieval/indexing “weight training.”
- One job record per document and per stage: durable UUID, owner, content hash, total pages/bytes, processed pages/bytes, chunks, warnings, selected worker, created/updated timestamps, last committed checkpoint, revision, and human-readable error. Use a bounded private SQLite job ledger with migrations.
- Visible statuses: queued, transferring, validating, extracting, indexing, awaiting review, ready, failed, paused, canceled. Progress = completed units / known total * 100; if totals are unknown use indeterminate state (never invent percentages). Show overall collection completion and per-file status.
- Poll or subscribe to an authenticated progress endpoint. Closing the browser must not cancel a job; refreshing must restore current progress from SQLite. Admin-only cancel/retry/resume with audit history.
- Search preview should cite **document name and page/section** and show whether answer was drawn from an approved local document. Offer source comparison and explicit “not found in documents.”
- Subject-specific enable/disable and revision pinning; deleting a source triggers index deletion, supersession, and an audit log.
- UI settings may switch compute workers, pause/drain jobs, and request restart safely; these controls must not pretend to transfer undocumented hidden model state.

### Backend and offline authority

- **Gentoo owns the authoritative local inference, extraction/indexing, documents, job ledger, and localhost UI**. Ubuntu is the authenticated public CMS/gateway and optional remote admin mirror. Gentoo must remain usable if Ubuntu, DNS, Starlink and all external internet go away.
- Provide a standalone local UI on Gentoo loopback (`127.0.0.1`, a separately assigned non-conflicting port), accessible by a browser running on the Gentoo machine. LAN access, if ever enabled, requires explicit network binding, TLS and authentication. Loopback is local to the machine running the browser.
- All processing uses local executable binaries and local model weights already present on disk; all fonts, math/Markdown rendering assets and JavaScript are served locally. No CDN, analytics, third-party scripts or remote inference fallback.
- Website-to-Gentoo ingestion must use an authenticated, bounded and resumable transfer mechanism with exact content-hash verification. Never expose raw llama.cpp or the file-processing agent on a public socket. Keep the existing SSH tunnel/service ports unchanged.
- Local-worker processes run as unprivileged users with read-only input, private output, resource/time quotas and *egress denied except necessary loopback*. Default profile is offline. Existing optional web-research settings are distinct and explicitly opt-in; they must never activate automatically for an offline knowledge query.
- SQLite FTS5 / BM25 may be used for an initial **disk-backed** index without embeddings or internet. Optional embedding and rerank models must be installed as local files and chosen explicitly. Remove the existing single-JSONL 16 MiB/3,000-record bottleneck when implementing the multi-manual library.
- The model sees only reviewed, attributable excerpts plus the user's prompt. Keep source excerpt boundaries, prompt-injection treatment, page numbers and confidence/coverage warnings. Do not silently treat unreviewed AI-generated summaries as trusted.
- Recover from interruption using atomic checkpoints for each completed document/page. Hash and deduplicate repeats, resume from last committed page, avoid duplicate visible chunks and never mark partial ingestion “complete.”

### Separate actual fine-tuning workflow

- Indexing/RAG should work immediately on existing CPU/GPU hardware, provided local GGUF inference is healthy. It does **not** alter Qwen weights.
- Adapter training uses an isolated offline training environment, locally downloaded/approved *prior* checkpoint and packages, manually reviewed examples, held-out evaluation, resource preflight, checkpoints, and post-training verification.
- Hardware shortage is a reported blocker, not a reason to send training to an external model or buy hardware automatically. Do not interrupt the running chat worker to attempt an unsupported QLoRA run.
- Only activate new adapters after evaluation and a user-approved safe worker swap; preserve the prior adapter and settings for rollback.

### Acceptance tests before production deployment

1. Block all external traffic; disconnect WAN/DNS; from a browser **on Gentoo**, open localhost UI, query locally approved mechanics sources and receive a useful citation-backed answer from local llama.cpp.
2. Stage 20 synthetic PDFs in several authenticated batches; verify each file's original content hash, per-file progress, batch progress and persistence after browser refresh.
3. Terminate the importer mid-PDF and restart it: resume without duplicate indexed chunks or false 100% success.
4. Import a local dictionary and verify exact term lookup, citations and offline search in its assigned profile.
5. Reject oversized, invalid, symlink, encrypted/unsupported or maliciously formed PDFs safely; mark errors per file while other queue entries continue.
6. Detect pages containing scanned-only text and diagram/table gaps; flag them, never hallucinate missing torque settings or procedures.
7. Verify unapproved material never appears in retrieval and no data is sent to external hosts during import, indexing, summarization or chat.
8. Bring Ubuntu/Starlink down, verify local UI still works; bring them back up, safely reconcile job status and hashes without losing progress.
9. Ensure no unexpected PHP/Hestia/Nginx/SSH/service/port changes, no overwriting existing CMS SQLite or administrator credentials, and no changes to production workers until explicit reviewed deployment.
10. Test worker draining, failure recovery, rollback, and model-version match before allowing a live adapter switch.

## Delivery stages

1. Local Gentoo durable library and offline browser; create validated extraction/indexing API and unit tests. Preserve current CPU/AMD/NVIDIA services.
2. Private, authenticated CMS integration for staging/sync, progress/approval, status and revisioned profiles. Keep current uploads and records.
3. Frontend Markdown/LaTeX fix with self-hosted renderer, no remote CDN and tests for escaped HTML/math/code.
4. Optional isolated local adapter training, evaluation, activation and rollback workflow with honest hardware preflight.
5. Only if measured demand requires it: Ubuntu resource scaling or secondary gateway. **No VPS purchase is required to begin.**

No automated installer or production service changes should accompany an unreviewed design revision.
