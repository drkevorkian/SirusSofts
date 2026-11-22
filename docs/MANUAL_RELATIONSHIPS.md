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


## Explicit cross-manual comparison and numeric-difference candidates (source-only)

The offline `sirus-ai-dual-tunnel/gentoo/manual_relationship_compare.py`
accepts a **bounded operator-selected request** on standard input. It requires
2–8 exact approved PDF source IDs with full **operator-declared** manufacturer,
model, year, engine, revision and application fields, 1–4 exact indexed search
terms, and optionally up to 12 explicitly paired numeric source quotations.
It does **not** derive applicability from filenames, model predictions, matching
parts, or the knowledge index.

For each selected exact term, it searches the incremental index and rechecks
each original page/part citation. Shared part identifiers, procedure headings,
system headings and direct document-reference mentions produce
`shared_mention_requires_application_review` when declared scopes match,
or `different_declared_scope` when declared model, year, engine, revision
or another field differs. These are review candidates; **neither result
establishes actual compatibility or verified source scope**.

For numeric checks, the original approved excerpts are reparsed using the
existing conservative measurement parser and Decimal unit conversions.
A numeric disagreement becomes `numeric_difference_candidate` **only**
when both operator-declared scopes and parsed property labels match, units
are safely comparable, and values differ. Differing declared scopes,
property labels, unsupported units, absent/revoked quotes, and equal values
have separate outcomes. Even a numeric-difference candidate is **not** a
confirmed contradiction: identical labels may refer to distinct fasteners,
conditions, variants or revisions.

Searches fail closed when selected documents have stale/partial indexes,
revoked approval, missing original quotations or more than 64 matching
mentions for a requested term. A final read-only snapshot gate rechecks
all selected source metadata and returned citations before releasing
results. If a selected numeric quote is missing, the report is explicitly
incomplete. Output includes exact source/page/part/quotation hashes,
per-source citation counts and explicitly labeled truncated display
samples (up to three citations per side). No PDF, index, CMS or research
ledger is written; no network or LLM execution occurs.

Local CLI example (the SHA values below are placeholders, not actual PDFs):

```bash
python3 sirus-ai-dual-tunnel/gentoo/manual_relationship_compare.py \
  --source /path/to/private/pdf_ingest.sqlite3 \
  --index /path/to/private/manual_relationships.sqlite3 <<'JSON'
{
  "sources": [
    {"source_id": "pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
     "scope": {"manufacturer": "BRP", "model": "Defender",
               "year": 2026, "engine": "HD10", "revision": "Rev A",
               "application": "Primary clutch"}},
    {"source_id": "pdf:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
     "scope": {"manufacturer": "BRP", "model": "Commander",
               "year": 2024, "engine": "HD10", "revision": "Rev A",
               "application": "Primary clutch"}}
  ],
  "terms": [{"kind": "part", "term": "420234561"}],
  "measurements": []
}
JSON
```

The Gentoo **in-place upgrade manifest** now includes both
`manual_relationship_context.py` (required by the previous prompt
enrichment increment) and `manual_relationship_compare.py`, with a
regression test that enforces dependency coverage. This changes source
only; it does not run the upgrade. The comparison tool has no public
API or admin-panel route and is **not** automatically used to broaden
an autonomous research job beyond its selected manual. Authenticated,
explicit cross-document workflows, full semantic/procedural reasoning,
real PDF-corpus acceptance and live safety validation remain open.


## Original-PDF procedural dependency and order review (source-only)

The read-only `sirus-ai-dual-tunnel/gentoo/manual_procedure_dependencies.py`
now accepts 1–8 **explicit SHA-256 approved PDF selections**, each with one
**exact indexed procedure heading** and a complete **operator-declared**
manufacturer/model/year/engine/revision/application scope. It never discovers
or widens a vehicle application by filename, model output, or shared part number.

The analyzer revalidates each selected heading against the original approved
PDF catalog and the fresh private relationship index. It reads only the
**same page/part excerpt** containing that unique heading, stopping at the
next recognized unnumbered procedure/system heading. Limits are 160 lines,
12,000 characters, 32 strictly consecutive numbered steps, and 16 explicit
temporal relationships per selected excerpt. Ambiguous headings, missing or
revoked approvals, stale index revisions, numbering gaps and exceeded limits
**fail closed** without presenting partial comparisons.

It records source/page/part, full printed quote and SHA-256 of each detected
numbered step, along with narrowly formatted temporal clauses:

- `Before [dependent action], [prerequisite action].`
- `After [prerequisite action], [dependent action].`
- `Do not [dependent action] until [prerequisite action].`

These are **printed ordering candidates**, not independently established
safety prerequisites. The wording is not rewritten as instructions for
performing a repair. Other natural-language phrasing, diagrams, sidebars and
steps split across PDF excerpts are not interpreted.

For explicitly selected pairs with **identical declared equipment scope**
and **identical heading text**, the tool flags `possible_numbered_order_disagreement`
only when **two uniquely repeated exact step-action sentences** are printed
in opposite numbered order, or `possible_explicit_order_disagreement`
when the **same two exact phrases** appear in opposite temporal directions.
Each candidate carries quotations from both sources. Any year, engine, model,
revision or application difference results in `different_declared_scope`
rather than a contradiction. Differing headings are not compared. At most
48 candidates per selected pair are allowed; exceeding the cap fails closed.

The response always declares `procedure_coverage_complete=false`: a
source-checked excerpt is **not** the whole manual or a validated procedure.
Matching instructions do not prove actual fitment, correct torque or
service safety; no candidate does not mean the manuals agree. Scanned figures,
unindexed procedures and application-specific conditions still require human
inspection. The module is **offline only**: no authenticated website route,
automatic cross-document expansion in the LLM, database writes, training,
or production installation. It is listed in the Gentoo reviewed in-place
upgrade manifest but the upgrade is **not run**.

Example request (replace SHA placeholders with real approved private IDs):

```bash
python3 sirus-ai-dual-tunnel/gentoo/manual_procedure_dependencies.py \
  --source /path/to/private/pdf_ingest.sqlite3 \
  --index /path/to/private/manual_relationships.sqlite3 <<'JSON'
{
  "selections": [
    {
      "source_id": "pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "heading": "removal of primary clutch",
      "scope": {"manufacturer":"BRP","model":"Defender","year":2026,
                "engine":"HD10","revision":"A","application":"Primary clutch"}
    },
    {
      "source_id": "pdf:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "heading": "removal of primary clutch",
      "scope": {"manufacturer":"BRP","model":"Defender","year":2026,
                "engine":"HD10","revision":"A","application":"Primary clutch"}
    }
  ]
}
JSON
```

Synthetic regression coverage lives in
`sirus-ai-dual-tunnel/tests/test_manual_procedure_dependencies.py` and
the dependency-manifest test. Full procedural semantics, verified application
scope, lifecycle tracking and automatic revisiting of unresolved dependency
candidates remain **unchecked roadmap requirements**.


## Persistent operator-scoped procedure review queue (source-only)

`sirus-ai-dual-tunnel/gentoo/procedure_review_queue.py` stores **only explicitly
registered** 1–8-source selections, each with exact procedure headings and
full operator-declared manufacturer/model/year/engine/revision/application.
Registration does not search for additional documents or assert applicability.
The separate private SQLite file is
`/srv/sirussofts/storage/rag/procedure_reviews.sqlite3` (owner-only file inside
the existing private RAG directory). The existing original ingest database,
relationship index, research result ledger and model weights are unchanged.

A `tick` claims **at most one due registered review** with a bounded durable
120-second lease. The original source PDF procedure reviewer rechecks the
selected heading, current approval, original excerpt and index. Successful
observations are rescheduled after **one hour**; incomplete/stale/revoked
results are marked `blocked` and retried after **five minutes**. An
expired lease can be recovered on another tick; lease fencing rejects an
older worker after ownership changes. No new inference request is scheduled.

A change creates an **append-only, versioned observation** with a bounded
source-cited snapshot and identifiers of new, no-longer-observed and
evidence-changed candidates. Identical rechecks update the last-checked
timestamp but **do not duplicate history**. A revoked source, incomplete
index, changed quotation, ambiguous procedure heading, or exceeded bound
clears the **current** candidate count; older snapshots remain explicitly
historical and unverified, never promoted into active or approved evidence.
There is no automatic dismissal, confirmation, training export, or claim
promotion. A candidate disappearing is **not proof that a conflict was
resolved**. All displayed counts describe previous observations, not
instantaneous current source truth.

When Continuous Research is **running** and interactive chat is idle, the
existing Gentoo 30-second inventory worker first refreshes the derived index
and then admits **one due procedural recheck**. Disabled/paused research or
queued/running interactive chat means no recheck. This is admission-time
deferral; a newly arriving chat cannot interrupt an already in-flight
bounded SQLite/source check. Long queues may take longer than one hour to
cycle. No service, model, credential, API endpoint or public page is added.

Operator CLI examples, after a separately reviewed and approved in-place
upgrade (replace placeholders with real private paths and approved IDs):

```bash
# Register a JSON request accepted by manual_procedure_dependencies.py
python3 sirus-ai-dual-tunnel/gentoo/procedure_review_queue.py \
  --queue /srv/sirussofts/storage/rag/procedure_reviews.sqlite3 \
  --source /srv/sirussofts/storage/databases/pdf_ingest.sqlite3 \
  --index /srv/sirussofts/storage/rag/manual_relationships.sqlite3 \
  register < /private/operator_selection.json

# Inspect metadata and history; historical citations are deliberately opt-in
python3 sirus-ai-dual-tunnel/gentoo/procedure_review_queue.py \
  --queue /srv/sirussofts/storage/rag/procedure_reviews.sqlite3 list

python3 sirus-ai-dual-tunnel/gentoo/procedure_review_queue.py \
  --queue /srv/sirussofts/storage/rag/procedure_reviews.sqlite3 \
  history --id <64-character-review-id> --include-evidence

# Force the next eligible idle cycle to re-examine one known selection
python3 sirus-ai-dual-tunnel/gentoo/procedure_review_queue.py \
  --queue /srv/sirussofts/storage/rag/procedure_reviews.sqlite3 \
  recheck --id <64-character-review-id>
```

The CLI is a **private local operator tool** without a web-admin route or
new authentication system. Keep the JSON inputs, candidate citations and
SQLite database off web roots and public GitHub. No operator registration
has been performed against the real library, nor has the queue been deployed
to Gentoo/Ubuntu. Human review, approved application-scope evidence,
complete semantic procedure understanding, real source-corpus acceptance,
cross-document discovery, and operational load testing remain open.


## Opt-in discovery of related original-PDF procedure evidence (source-only)

`sirus-ai-dual-tunnel/gentoo/procedure_discovery.py` extends the existing
persistent procedure-review queue with **private candidate discovery**, not
automatic verification or accepted reviews. An authorized local operator must
first choose an **exact approved PDF SHA-256 and exact indexed procedure
heading** and opt in by registering a discovery seed. Without an explicit seed,
the idle worker **does not create discovery tables or scan the corpus**.
Seeds can be paused and resumed; pausing fences an in-flight worker.

A seed is eligible only if the existing CMS-admin evidence scope ledger
contains a **current, full, six-part** `make/model/year/engine/assembly/revision`
application declaration for that PDF, with matching latest append-only
scope-history revision made by a `cms-admin:...` actor and a current eligible,
reviewed source inventory record. These declarations are **administrative
assertions, not independent proof of vehicle fitment or the manual's real
application**. Short/unknown scopes are not broadened or guessed.

On each eligible idle inventory cycle, the worker finds at most **four
other approved-PDF source IDs** with the **same exact indexed procedure
heading**, moving through the candidate SHA keys with a durable cursor.
It considers peers only if their full administratively declared scopes
are an exact match. It rechecks approved original PDF heading citations
and runs the bounded original-PDF procedure comparer on the selected pair.
Only a **source-cited possible ordering disagreement** produces an
unreviewed suggestion. A lexical heading match alone is **not** compatibility,
service correctness, or a contradiction.

Suggestions live in the separate owner-only private SQLite review database,
are capped at **512 stored suggestions**, and never enter a model prompt,
training export, approved knowledge table or review target automatically.
A private local `suggestions` command revalidates selected original
source citations, admin scopes and the current comparison fingerprint
before displaying an eligible proposal. Revoked approvals, changed excerpts,
incomplete index, revised scopes and invalid heading uniqueness render a
proposal stale/unavailable. A separate **explicit local operator `accept`**
command performs the same checks and atomically registers one candidate
for the existing versioned procedure-review queue. This is not a CMS
administrator UI, role-authenticated remote action or approved repair.

Examples (illustrative identifiers; use actual private file paths and SHA
values on a reviewed installation; no action is executed by GitHub commits):

```bash
# Operator opt-in: one chosen approved document and exact heading
printf '%s\n' '{"source_id":"pdf:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","heading":"Removal of primary clutch"}' |
  python3 sirus-ai-dual-tunnel/gentoo/procedure_discovery.py \
    --queue /srv/sirussofts/storage/rag/procedure_reviews.sqlite3 \
    --source /srv/sirussofts/storage/databases/pdf_ingest.sqlite3 \
    --index /srv/sirussofts/storage/rag/manual_relationships.sqlite3 \
    --research-db /var/lib/sirus-ai-dual/input.sqlite3 seed

# Show candidate IDs after opt-in idle scans (no automatic acceptance)
python3 sirus-ai-dual-tunnel/gentoo/procedure_discovery.py \
    --queue /srv/sirussofts/storage/rag/procedure_reviews.sqlite3 \
    --source /srv/sirussofts/storage/databases/pdf_ingest.sqlite3 \
    --index /srv/sirussofts/storage/rag/manual_relationships.sqlite3 \
    --research-db /var/lib/sirus-ai-dual/input.sqlite3 suggestions

# After an operator reviews the original manuals and their scope:
# use 'accept --id <suggestion-id>', or 'pause-seed --id <seed-id>'.
```

The existing inventory worker runs this scanner only when continuous research
is enabled, interactive chat is idle, and an explicit discovery seed has
been registered. It admits one seed and up to four candidate peers per cycle,
with a 30-second continuation for larger peer sets, a 15-minute revisit
after a completed sweep, and a short retry on unavailable source state.
No additional service, network interface, web route or model call is added.

**Remaining boundaries:** This discovers only literal heading matches among
operator-scoped PDFs. It does not automatically discover every procedure,
determine what a manual actually applies to, semantically equate different
instructions, approve a contradiction, or schedule production repair work.
Real Gentoo PDF-corpus acceptance, administrator UI, cross-source scope
attestation and operational load tests remain open.
