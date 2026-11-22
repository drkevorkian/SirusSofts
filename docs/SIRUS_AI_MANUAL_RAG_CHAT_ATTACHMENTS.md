# Approved Gentoo manual RAG + conversation file attachments

**Status:** source code feature, requires reviewed upgrade on Gentoo and Ubuntu
before the browser controls work. No fresh server install, no SSH tunnel changes,
no model weights changed and no external inference or embedding API.

## Architecture

SirusSofts AI chat lives at \`https://sirussofts.com/Gentoo/AI/\` and calls
the authenticated PHP CMS at \`/ai/api\`, which forwards authorized model
jobs over existing reverse SSH input tunnel 18485 to Gentoo 18082.

**Approved manual retrieval:**
- Gentoo source-of-truth: dedicated ext4 SSD at
  \`/srv/sirussofts/storage/databases/pdf_ingest.sqlite3\`.
- Search index: \`/srv/sirussofts/storage/rag/manual_fts.sqlite3\`,
  derived with offline SQLite FTS5. Only \`status='approved' AND reviewed=1\`
  records appear. Page and filename are preserved.
- \`sirusai\` has read-only access to ingestion DB/WAL; it owns only the
  isolated RAG index. No PDFs moved or duplicated.
- Initial index is built by the reviewed Gentoo AI in-place upgrader.
  The input router refreshes up to 20 newly approved/changed manuals each
  minute. Revoke takes effect immediately for queries: every candidate is
  verified against the current source DB before entering a prompt.
- The \`use_manuals\` flag is sent **only for logged-in administrator chat**.
  Public/guest/ordinary user chats cannot query the private manual corpus.
- Retrieval is local, lexical FTS5. On each question the Gentoo input router
  adds up to five query-matched excerpts to the local model request, labeled
  \`[M1]\` through \`[M5]\` with filename, source PDF page and SHA prefix.
  The source text is explicitly untrusted data, not commands.
- No match means no manual-based answer should be fabricated. The model is
  instructed to distinguish source content from inference and identify
  incompatible year/model variants. LLM-generated citation claims should
  still be checked against the cited source.
- This is **RAG**, not fine-tuning/LoRA or changing the model's weights.

**Chat attachments:**
- A signed-in account may attach up to 8 files per conversation.
- Formats: PDF (up to 40 MiB) and UTF-8 text, code, Markdown, CSV or JSON
  (up to 2 MiB each). Text attachments are parsed immediately and stored
  only as private, conversation-scoped chunks in the CMS SQLite database.
- PDF uploads reuse the existing /ai/api/documents staging and local
  PHP/Poppler extraction worker on Ubuntu. An uploaded PDF must reach
  \`parsed\` before asking about it; the UI shows queued/processing/parsed/
  failed states and polls pending PDFs.
- Attachments can be removed from the conversation. Uploaded documents do not
  enter the global Gentoo manual library and do not get auto-approved or
  used by other users.
- Relevance-scored attachment excerpts, labeled \`[A1]\` etc., are included in
  the existing bounded CMS prompt context. Raw files never go to the LLM,
  and file text is not treated as instructions.
- Sign-in required for attachment storage. Guest-only chat continues without
  file upload or manual corpus access. Image, audio, DOCX and scanned PDFs
  lacking selectable text are not yet supported.

## Deployment sequence

1. Commit/pin review, GitHub CI, and the Gentoo PDF Admin bulk-approval
   feature must already be installed. Batch approve eligible extracted PDFs
   in \`/admin/ai\` before building the manual index. Never bulk-approve
   PDFs without acknowledging that this is a corpus-level trust decision.

2. On **Gentoo**, pull/review the pinned commit, confirm clean checkout and set
   the \`SIRUS_EXPECTED_COMMIT\` environment variable to its exact SHA.
   Run \`sudo -E bash sirus-ai-dual-tunnel/gentoo/upgrade-in-place.sh --plan\`.
   Only after inspecting the plan, run \`--apply\` and type
   \`UPGRADE GENTOO AI\`. The installer backs up the router and its database,
   installs \`manual_rag.py\`, builds the derived FTS on SSD as service user
   \`sirusai\`, restarts only \`sirus-ai-input\`, and verifies local health.
   **It will not restart the actual llama.cpp model services.**

3. On **Ubuntu VPS**, pull/review the same pinned commit and set
   `SIRUS_EXPECTED_COMMIT` to its full SHA. Run CMS in-place upgrade
   \`deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan\` then
   \`--apply --user sirusweb --domain sirussofts.com\` with explicit operator
   confirmation and consistent rollback backups.

4. On **Ubuntu VPS**, run
   \`SIRUS_EXPECTED_COMMIT=<PIN> sudo -E bash sirus-ai-dual-tunnel/ubuntu/upgrade-in-place.sh --plan\`
   then \`--apply\` and type \`UPGRADE UBUNTU AI\`. This installs updated
   chat HTML/JS/CSS while preserving the events SQLite DB.
   The Ubuntu PDF extraction worker must already be enabled for attached
   PDFs. Text/code uploads require no PDF worker.

5. Verify
   - Gentoo: \`sudo -u sirusai python3 -B /opt/sirus-ai-dual/gentoo/manual_rag.py --query "HD11 clutch"\`
   - Gentoo: \`sudo rc-service sirus-ai-input status\`
   - Ubuntu: \`curl --noproxy '*' -fsS https://sirussofts.com/ai/api/state\`
   - Browser: signed-in Admin chat shows "Search approved manuals";
     attach a text file, ask about its contents; attach a PDF and wait for
     the status to become "parsed"; ask with source citations.

## Recovery notes

Gentoo in-place upgrade backs up the router, SQLite snapshot and previous
manual RAG module under \`/root/sirus-ai-upgrade-backups\`. The FTS index is
derived and can be regenerated by \`--sync\`; NEVER delete the original
\`pdf_ingest.sqlite3\`, PDFs, or the saved preserved RAID volume.
CMS in-place upgrade preserves the consistent CMS database and source code.
AI frontend upgrade snapshots gateway and events DB before swapping.

Known constraints: search ranking is lexical, not embeddings; first indexing
may be slow for thousands of long manuals; newly approved entries refresh
in batches; cited model-generated claims must still be cross-checked; current
chat attachments provide bounded excerpts, not an assurance that all pages of
an arbitrary long file have been examined.

## 2027 Defender HD11 retrieval reliability update

- Diagnostic evidence from an actual 2027 HD11 administrator chat confirmed
  four retrieved excerpts were included, but they favored shop-manual
  introductory pages and wiring diagrams, and the local model falsely claimed
  the manual was inaccessible.
- The revised retrieval scopes explicit year, model, and manual type **before**
  full-text search. An explicitly requested shop manual excludes wiring
  diagrams. Missing year/model combinations fail closed rather than substituting
  another model's mechanical specifications.
- Search prioritizes matching terms in **page body** and actual procedural
  language over filename/contents hits. Query-centered excerpt windows retain
  relevant text even when it is deep in a long page extraction.
- A local response guard detects false generic claims of no manual access when
  verified [M#] passages were supplied and replaces that denial with a factual
  source list. It does **not** invent steps or assert that all pages are visible.
- No database migration and no rebuild of previously indexed manuals are
  necessary; update the Gentoo router and RAG module with the existing pinned
  in-place upgrader.
- First real-data acceptance check must compare the 2027 HD11 source/page list
  against the expected manual, then ask the local LLM to cite those pages.
  Automated synthetic tests cannot establish the exact removal procedure.

## Negated document-type filter correction

Production read-only SQLite diagnostics found that requests including
"Do not substitute wiring diagrams or another model year" were being
misinterpreted as simultaneously *requesting* a shop manual and wiring diagram.
No filename can satisfy both filters, so the router sent a "no approved matching"
context even though the manual was approved, indexed, and directly searchable.

The corrected filter treats a document type mentioned inside a local exclusion
phrase ("do not substitute", "exclude", "avoid", "rather than", "without") as
**excluded**, not required. Positive references still select the requested
type; an explicit comparison can include both types. Excluded words are omitted
from the ranking query. The exact browser question is covered by synthetic
regression tests with same-year wiring/manual and wrong-year/model decoys.

This is a Gentoo-only, in-place AI router update; no Ubuntu frontend change,
no PDF reprocessing and no rebuild of the already populated FTS index.
