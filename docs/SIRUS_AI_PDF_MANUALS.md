# Private Can-Am manual import: staging, extraction and review

The CMS AI admin page accepts up to 10 PDFs per request (each maximum 25 MiB)
into its private, access-controlled directory outside public_html. The
database records original filename, expertise, byte count and SHA-256 hash.
Uploaded files are never executed, parsed or sent to third parties by PHP.
Download of staged files requires CMS admin authentication and CSRF validation.

Check the production PHP upload_max_filesize/post_max_size and Hestia Nginx
client_max_body_size. The smallest limit wins. Do not change global limits
without a reviewed Hestia-specific maintenance plan. A large technical PDF
may exceed the first version's 25 MiB per-file limit; break into separate
files only if it preserves intended pagination, or expand limits after review.

A separate *operator-run* Gentoo parser extracts page-attributed text using
Poppler pdfinfo and pdftotext, without OCR or network access. Page ranges
support large manuals. Source file never becomes a model prompt automatically.

On Gentoo, install Poppler tools using the approved system package manager,
then use the current source checkout (not the live worker install path):

    python3 sirus-ai-dual-tunnel/gentoo/manual_ingest.py --check
    python3 sirus-ai-dual-tunnel/gentoo/manual_ingest.py \
      --pdf /absolute/private/BRP-HD11.pdf \
      --profile mechanics \
      --start-page 1 --end-page 200 \
      --output /absolute/private/hd11-pages-1-200.draft.jsonl

The output intentionally sets reviewed=false and is not accepted by the
production RAG loader. It holds per-page snippets and SHA-256 metadata,
with page numbers in the titles for citation. A second, separately reviewed
admin importer and/or signed document approval step must be completed
before any of these excerpts become live knowledge.

Text extraction is NOT equivalent to full understanding of a technical
manual: torque tables, wiring diagrams, schematics, photos, foldouts,
scanned images and warnings can be missing. Treat page labels as pointers
to the original PDF, and verify safety-critical figures directly.

You can upload manuals from the website and download them securely, but
transfer from the Ubuntu private staging store to Gentoo is still manual.
Automatic private transfer, extraction-job status, searchable excerpts and
local AI summaries are next integration milestones, not live features.
