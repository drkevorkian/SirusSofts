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
