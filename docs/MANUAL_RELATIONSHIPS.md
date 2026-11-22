# Original-PDF cross-document relationships (source-only prototype)

`sirus-ai-dual-tunnel/gentoo/manual_relationships.py` implements a **read-only**
offline inspector over the existing approved/reviewed PDF ingest database.
It accepts 1–8 **explicit SHA-256 PDF IDs** (not titles or arbitrary paths) and
finds strictly formatted part-number mentions, procedure-style headings and
direct references to service/shop/repair/wiring/electrical/installation documents.

Results include exact original source hash, page, part, bounded quote and
quote digest. Exact repeated part identifiers produce **candidate relationships**
across different PDFs, not confirmed compatibility. A part number that appears
in both a Can-Am manual and a Polaris manual does not authorize interchange
or imply either source applies to the other model.

A bounded summary groups citations by document; it is not a complete manual
summary and does not interpret diagrams or infer service steps. An unmatched
reference to a 'wiring diagram' or 'service manual' produces a **possible
catalog-title gap**, never a claim that the document is missing: references
may point to internal sections, differently named files or unextracted pages.

CLI (illustrative source IDs, not actual manuals):

```bash
python3 sirus-ai-dual-tunnel/gentoo/manual_relationships.py \
  --source /path/to/private/pdf_ingest.sqlite3 \
  pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  pdf:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

The implementation reads original current PDF excerpts on each query,
honors `status='approved' AND reviewed=1`, and never reads generated
research drafts or performs arbitrary path traversal. It caps documents,
excerpts, lines and mentions, returning `complete=false` if limited.
There is no network, model weight change, automatic review promotion,
source rewriting or database mutation.

**Roadmap status:** Advances the three Section 6 requirements on repeated
parts/procedures, source-linked summaries and missing reference detection.
These remain unchecked: complete corpus indexing, inferred applicability,
diagram reading, semantic reasoning, admin UI, and live acceptance are pending.


## Incremental private search index (source-only)

`sirus-ai-dual-tunnel/gentoo/manual_relationship_index.py` maintains a
**separate** derived SQLite file. It scans only approved, reviewed PDF
excerpts and tracks the original ingestion `updated_at`, filename, excerpt
count and completeness for each document. Repeated runs index up to 16
new/changed documents per call; revoked or unapproved document rows and their
derived mentions are removed before adding work. A keyset cursor rotates
through pending documents. Individual document updates replace all mentions
atomically. Limited scans report incomplete coverage and are not called done.

Recognized original-source relationship categories are **part numbers**,
**procedure headings**, **system headings** (conservative literal labels such
as `Cooling System`, `Electrical System`) and **manual/document references**.
They are text mentions only, not a semantic graph of compatibility, causal
relationships or procedural safety.

The `search` subcommand requires a kind, exact term and explicit source
SHA-256 IDs (up to eight). It checks that each selected original is currently
approved, its revision matches the indexed document, and each returned
quote/page/part still exists in the private original catalog. A stale PDF or
quotation causes the query to **fail closed** rather than returning obsolete
evidence. Results are paginated and source-linked. Original source metadata
changes outside the indexed revision signal can be addressed by an explicit
`sync --force` refresh; it is not yet an unattended file watcher.

```bash
python3 sirus-ai-dual-tunnel/gentoo/manual_relationship_index.py \
  --source /path/to/pdf_ingest.sqlite3 \
  --index /path/to/private/relationships.sqlite3 sync --batch 16

python3 sirus-ai-dual-tunnel/gentoo/manual_relationship_index.py \
  --source /path/to/pdf_ingest.sqlite3 \
  --index /path/to/private/relationships.sqlite3 search \
  --kind system_heading --term 'cooling system' \
  --source-id pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

**Source-only limitations:** No website/API route, automatic scheduling or
live deployment is added. Documents that contain only scanned images,
procedural prose without recognized headings, novel part-number formats,
or implicit system relationships are not indexed by this lexical prototype.
The index can grow substantially on a large manual library and needs operator
storage budgeting and long-term retention. No auto-approval, model training,
part fitment or full semantic reasoning is claimed.


## Autonomous low-priority indexing (source-only)

When the Continuous Research controller is explicitly **running**, its
existing 30-second private inventory loop now refreshes at most four PDF
relationship-index documents per eligible iteration after the existing
numeric-evidence batch. If a chat job is queued/running, it performs **no**
relationship-index write. The index stays disabled when research is disabled
or paused. The derived file is `manual_relationships.sqlite3` in the
existing Gentoo private RAG directory, never in the public CMS database.
No arbitrary model requests, training, outbound fetches, new services,
automatic original-PDF editing or new web endpoints are introduced.

This is **admission-time deferral**, not preemption: a chat arriving during
one bounded indexing call may wait for that call's disk I/O to finish.
Worker CPU/GPU cost and actual responsiveness require hardware testing
before production acceptance. No Gentoo or Ubuntu deployment occurred.


## Source-scoped local research prompt integration

The autonomous research worker now calls `manual_relationship_context.append_for_section`
just before local inference for a PDF section. The helper recognizes at most
two literal part/heading/reference mentions from that section and performs
bounded lookup against **only the same exact PDF source ID**, rechecking
current approval, catalog revisions and original excerpt quotes. It appends
a clearly marked **untrusted, source-cited** quotation packet only when the
index and selected source are complete and current. If the index is absent,
partial, stale or revoked, ordinary research proceeds with no added packet.

`evidence_packet` additionally provides an explicit 1–8-SHA cross-manual
comparison interface for future administrator-approved source selection;
the autonomous worker **does not** invoke this broad mode. Matching a printed
identifier across documents does not prove interchangeability. No application
scope, engine, model year or safety claims are inferred, and generated text is
not eligible for automatic knowledge promotion or model training.

Source-only: the private index must be installed through a reviewed in-place
upgrade before this enrichment is possible on Gentoo. No live deployment or
corpus validation has occurred.
