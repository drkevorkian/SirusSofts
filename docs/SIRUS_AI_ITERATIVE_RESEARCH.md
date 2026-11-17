# SirusSofts AI — manual inference and repeatable source research

**Status:** Source changes requiring code review, tests and explicit staged deployment. Not proof of a live installation.

## Operator workflow

An authenticated CMS administrator visits `/Gentoo/AI/` → **Research loops**.

1. Choose **Public HTTPS website**, **Pasted text**, or **Existing Gentoo PDF (extracted)**.
2. For a PDF, search the private Gentoo extraction catalog by a filename keyword, select a SHA-256-identified manual, and optionally enter start/end pages. End = 0 means through the last extracted page.
3. Choose a loaded local llama.cpp model, CPU/NVIDIA/AMD/auto target, research focus, and **1–100 analysis passes** (10 by default).
4. Start research, return later to see persistent progress, cancel between inference sections, or rerun the same source/settings from the beginning in a new job.
5. Inspect and copy source-tagged **unreviewed draft** notes. For sustained assistant retrieval, independently verify and add relevant excerpts or corrected summaries through the existing `/admin/ai` review/approval workflow.

Repeated inference **does not modify base weights, fine-tune an adapter, or automatically change what the LLM remembers**. Every run keeps its pass/section drafts in a private Gentoo SQLite ledger. These drafts remain separate from approved knowledge and training exports unless an administrator explicitly reviews and imports them.

## Existing PDF support

The PDF picker uses the **existing Gentoo** `/srv/sirussofts/storage/databases/pdf_ingest.sqlite3` database through the local AI input service. It does not upload, duplicate, re-download, or rename any PDF file. It only reads extracted, page-numbered `excerpts` records, via read-only SQLite. Supported records are `ready` and `approved` documents with extractable text. Pending/failed/unparsed PDFs must go through the existing PDF extraction process first. Ready but unreviewed PDFs remain unreviewed after analysis.

Each requested **pass** goes over **every extractable section within the selected page range**. Sections are bounded (~7.5k source characters per LLM input), include exact PDF page/part labels, and are processed in order. A large PDF can involve many local inference calls per pass; 10–100 passes over a multi-thousand-page manual can take substantial compute time. Start with a narrow page range for validation. A 100-pass job over an entire manual is a deliberate request, not a quick chat response.

The worker saves each section result, SHA-256 digest, page list, pass number, and an updated checkpoint in its private SQLite `research_sections` table; it also saves completed pass digests in `research_passes`. The website's **Browse saved section drafts** control pages through all results (12 per page), shows each pass and original PDF page numbers, and allows copying individual unreviewed drafts. After a Gentoo service restart, a running job resumes from the last committed section. The PDF's ingestion revision must not have changed during the job; otherwise it fails safely and the operator can start a new run.

PDF scanning/diagrams are not OCR'd by this feature. Page figures, tables and unreadable scans may need the original page viewer or existing extraction improvements. Unreviewed AI summaries are not evidence or verified repairs.

## Website and model source protections

- Only signed-in **CMS admins**, with existing CSRF and rate limiting, can schedule, cancel, rerun and read research job results through `/ai/api/research` routes. The CMS enforces job ownership on read/modify calls.
- The Gentoo research service is loopback-only via the existing restricted SSH tunnel; no direct public inference or PDF endpoint is created. All model calls target already running, **local** llama.cpp workers.
- Public URLs are HTTPS-only, resolved to checked globally routable IPv4 addresses, then pinned for TLS; no redirects, proxies, HTTP downgrade or private-network fetches. HTML is parsed to inert text. Logins, JavaScript-only pages, and PDFs fetched as web pages are outside this path.
- Source content and previous LLM drafts are bracketed as **untrusted**. Model responses remain drafts with explicit provenance and caveats. No remote text is executed or auto-approved.
- The model processes up to 9,000 text characters per webpage or pasted source. PDF extracts are processed section-by-section without silently truncating the chosen page range; no raw PDF bytes enter prompts.
- The Gentoo job queue permits a single research job at a time to prevent uncontrolled concurrent inference. Cancel requests stop before the next section (not mid-request). Browser closure does not stop processing.
- All research data is stored privately on Gentoo. Ubuntu stores only owner-scoped job metadata and the validated initial request. No database is overwritten by deployment.

## Deployment boundary

Changes are split across the Ubuntu CMS, Ubuntu web UI and the Gentoo AI input router and its Python dependencies. Deploying a new UI **without the Gentoo research router** causes research actions to fail. No initial/full installers should be run on established servers.

After reviewing the pinned commit and passing tests, use the existing CMS backup/upgrade process; the existing Ubuntu AI in-place updater for frontend assets; and the Gentoo `upgrade-in-place.sh` plan/apply procedure which backs up router and private SQLite, installs the local research modules, checks the approved-manual SSD, and restarts only `sirus-ai-input`. Review all plans before applies. Keep Gentoo model/GPU services, Hestia, SSH keys, TLS and reverse tunnels intact.

## Validation

- `python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_research_loop.py' -v`
- `python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_web_source_summary.py' -v`
- `php CMS/tests/ai_conversation_titles_test.php`
- `php CMS/tests/ai_workspace_test.php`
- `php CMS/tests/ai_research_test.php`
- `node --check sirus-ai-dual-tunnel/ubuntu/web/app.js`
- Run both GitHub Actions workflows and verify against a real 3-page extracted PDF before enabling a large multi-pass job.
