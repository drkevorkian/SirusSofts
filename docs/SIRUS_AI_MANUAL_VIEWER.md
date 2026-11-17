# SirusSofts AI — Image-only cited manual reader

**Release status:** source-code implementation; **not deployed until the pinned
Gentoo dashboard bridge, CMS and Ubuntu AI frontend upgraders pass**.

## User-facing behavior

- Signed-in administrators who ask SirusSofts AI about approved Gentoo manuals
  receive "Retrieved PDF pages" cards under answers, even when the local model
  cannot produce a complete source-grounded answer.
- Each card contains the actual PDF filename, reported PDF page number,
  a **server-rendered JPEG preview**, and links for "View cited page" and
  "View entire manual".
- The dedicated full-manual viewer at
  \`/Gentoo/AI/manual.html?sha=<sha256>&page=<page>\`
  displays one rendered JPEG at a time with next/previous/page selection.
  There are **no raw PDF downloads, PDF.js binary URL, browser PDF plugin,
  print buttons or file download buttons** in this implementation.
- Image viewing is not copy-proof: screenshots and client-side image saving
  cannot be prevented by a website. Do not represent view-only as DRM.

## Existing production architecture

- Full PDF originals remain on the dedicated Gentoo SSD in
  \`/srv/sirussofts/storage/manuals/objects/XX/<sha256>.pdf\`.
- Existing HMAC-signed Gentoo dashboard bridge remains at Gentoo loopback
  \`/internal/pdf-admin\` (Ubuntu loopback via reverse SSH 18480).
- **Two read-only operations** added: \`manual_meta\` and \`manual_page\`.
  They require existing node-key HMAC signature, timestamp, unique nonce and
  node identity just like existing PDF Admin commands. No new open port.
- Both operations require \`documents.status='approved' AND reviewed=1\`
  and a SHA-matched immutable content-addressed PDF path. A revoked document
  becomes unavailable on the next request, even if previous chat history still
  has citations. Only approved documents with verified path/header/size can
  render; supplied filesystem paths are never accepted.
- \`pdftoppm\` is run with an explicit page number and a bounded scale, timeout,
  and image size. It outputs a private temporary JPEG; the original PDF is
  **never sent back**. At most 2.4 MB of JPEG image data enters the signed JSON
  response, and at most 3.4 MB of JSON may traverse the tunnel.
- The dashboard's \`sirusnode\` account gains only read/traverse ACLs on the
  existing PDF objects and default ACLs for future objects. The ingestion
  database, ingestion worker, and model services are not restarted for viewing.
- The CMS's \`/ai/api/manuals/meta/<sha>\` JSON route and
  \`/ai/api/manuals/page/<sha>/<page>?size=preview|full\` JPEG route
  enforce a real **administrator session** and existing rate limiting.
  They use the signed private tunnel request, validate the received SHA,
  page, MIME, and image bytes, and apply no-store/noindex security headers.
  Guest and regular user sessions are denied.
- No public raw filesystem path or PDF route is available.

## Source cards and reliable attribution

- The Gentoo input router includes a deterministic list of retrieved manual
  references in responses (between \`[[SIRUS_MANUAL_SOURCES_START]]\` and
  \`[[SIRUS_MANUAL_SOURCES_END]]\` markers). The manifest carries full SHA256
  and page, derived from retrieved/approved manual search results, **not**
  from the LLM's own guesses. It lists retrieved sources, not a guarantee
  every one was actually used by the model.
- The chat frontend parses and hides the manifest from text and builds cards
  using DOM \`textContent\` and fixed same-origin URLs. Previously stored
  fallback responses with 12-character SHA prefixes can also resolve via
  the existing protected bridge, but only when the prefix matches **exactly
  one approved** source.
- Pages render lazily below answers. Links open the protected standalone
  image-only reader in a new tab; image rendering never leaves the private
  Gentoo/Ubuntu infrastructure other than the image served to the logged-in
  browser.
- This does not change how RAG chooses excerpts or solve a model that refuses
  to produce a requested procedure. It provides **visual verification**
  of retrieved sources in either case.

## Deployment order and rollback

1. Review the pinned commit and green GitHub tests. The original PDF ingestion
   and Gentoo AI input services must already be healthy.
2. **Gentoo first**: update source checkout, run
   \`bash gentoo-node/install-pdf-admin-bridge.sh --plan\`, verify SSD UUID,
   Poppler renderer, source/installed paths, then \`--apply\`.
   Confirm \`ENABLE GENTOO PDF ADMIN\` in a terminal. It backs up
   \`dashboard.py\`, \`pdf_admin.py\`, and \`manual_view.py\` where installed,
   plus manual-object ACL snapshots, then briefly restarts **only**
   \`sirus-gentoo-dashboard\` and verifies signed status.
3. **Gentoo second**: run
   \`SIRUS_EXPECTED_COMMIT=<PIN> bash sirus-ai-dual-tunnel/gentoo/upgrade-in-place.sh --plan\`
   then \`--apply\` after review. The existing AI router upgrade script
   preserves its SQLite backup, updates only AI router/RAG sources, and
   restarts **only** \`sirus-ai-input\`. Index rebuild is incremental.
4. **Ubuntu third**: run CMS code upgrader
   \`bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com\`,
   then \`--apply\` after reviewing, confirming \`UPGRADE CMS\`.
   Consistent SQLite backup and code rollback are saved privately.
5. **Ubuntu fourth**: run
   \`SIRUS_EXPECTED_COMMIT=<PIN> bash sirus-ai-dual-tunnel/ubuntu/upgrade-in-place.sh --plan\`,
   then \`--apply\` and confirm \`UPGRADE UBUNTU AI\`. Its existing
   rollback flow backs up the AI gateway and frontend, including newly
   added \`manual.html\`, \`manual.js\`, and \`manual_sources.js\`.
6. Verify administrator and guest access to metadata/page routes; a guest
   must receive 403, admin should receive valid JPEG and metadata. Ask a new
   question with manual retrieval enabled and inspect image source cards.

**Never run the first-time installers.** Existing release backups and manual
SSDs must remain intact.

## Optional live rendering acceptance test

After the Gentoo dashboard bridge upgrade succeeds, run as root:

```bash
runuser -u sirusnode -- python3 -B /opt/sirus-gentoo-node/pdf_admin_probe.py \
  --config /etc/sirus-gentoo-node/config.json --view-test
```

This makes a read-only signed status request, selects the newest approved
document, resolves its metadata, and renders PDF page 1 through the actual
installed loopback dashboard. It verifies JPEG bytes/approval without copying
the original PDF over the tunnel or printing any credentials. If the selected
manual is damaged or unusually resource-intensive, it may fail and must be
investigated before enabling public image cards.


## Broad shorthand discovery (2026 HD10 wire diagram)

- Accept document-type synonyms such as "wire diagram", "wiring diagrams",
  "electrical schematic", "electrical drawing" and "wiring prints".
- A year plus HD engine family without a named platform means **browse all
  approved same-year/same-engine platforms**, not randomly choose one vehicle.
- For broad wiring searches, query approved document *titles* even when
  \`pdftotext\` cannot read wiring images and FTS returns zero circuit terms.
  Prefer dedicated electrical/wiring documents; if none exist, show only
  same-year/engine shop manuals as potential references.
- Search result source cards open the image-only manual viewer. If no page text
  identifies a drawing, the reference is a **catalog-only match** and its
  displayed page is merely the starting page, not a claim that the wiring
  schematic is on that page.
- If page text explicitly identifies a wiring or circuit diagram, that page
  may be preferred, but extracted text alone does not validate what visual
  circuit diagram appears in the image.
- **No cross-year or cross-engine substitution, no automatic approval,
  no scanned-content hallucination, no new model or PDF downloads.**
- Broad document discovery is different from answering an exact pinout or
  connector-color question; specific technical questions must still use
  retrieved, page-attributed evidence. Missing page evidence must be stated.
- New code requires only the **Gentoo AI input router** upgrade. The existing
  Ubuntu CMS, AI gateway, viewer UI, signed Gentoo PDF bridge, PDFs, and
  document index format are unchanged. The incremental FTS sync should show
  \`new_or_changed: 0\` if no manual approvals have changed.


## Engine-unlabeled BRP wiring documents (live corpus regression)

The live 2026 Gentoo approved catalog includes wiring documents titled
\`SSV 2026 Commander- Wiring Diagram_219101142_WD91Y026_en.pdf\`,
\`SSV 2026 DEFENDER - Wiring Diagram_219101142_WD91Y026_en.pdf\`,
and \`SSV 2026 MAV Trail_Sport - Wir Diagram_219101142_WD91Y026_en.pdf\`.
None says HD10 in its filename. The same catalog includes an explicitly named
\`SSV 2026 DEFENDER_TRAXTER HD11- Wiring Diagram...\`, which **must not**
be mistaken for HD10.

For a broad discovery query such as \`2026 HD10 wire diagram\`, the RAG
search may now present *approved, same-year SSV documents with an unspecified
HD engine* as **candidate wiring documents**. Exact engine matches rank
first, and filenames explicitly stating other HD engines or Maverick X3 are
excluded. The \`Wir Diagram\` title abbreviation is recognized.

These candidate titles do not certify HD10 applicability or identify a
diagram page. The response must carry an explicit **ENGINE APPLICABILITY
UNVERIFIED** warning and direct the administrator to check coverage inside
the image-only viewer before relying on a drawing. For targeted connector,
pinout or color questions, the engine must still match explicitly and the
answer must have page-attributed evidence. No approval or data changes occur.

This is a **Gentoo AI router-only** source patch: existing approved PDFs,
Gentoo PDF Admin/viewer bridge, Ubuntu CMS frontend, SSH tunnels, and
databases remain intact. Use the pinned upgrade-in-place script, not a
first-run installer.
