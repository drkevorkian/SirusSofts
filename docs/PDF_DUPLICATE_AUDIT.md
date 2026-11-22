# Gentoo PDF duplicate audit (source-only, read-only)

**Status:** implemented and locally tested in GitHub; **not installed** on Gentoo or Ubuntu. This is a private operator-side audit, not a public CMS/API endpoint. It does **not** train or query a model.

## What is and is not a duplicate

The existing Gentoo PDF ingest stores a single canonical content-addressed object per SHA-256 value. When a subsequent PDF upload has exactly the same bytes, ingestion verifies the existing object, removes the *staged copy*, and records a private \`events.kind='duplicate'\` event. The original canonical object and extracted citations are retained.

\`pdf_duplicate_audit.py\` reads the SQLite catalog and those recorded events without modifying either one. By default it also hashes the still-pending PDFs under \`manuals/inbox\`, detecting identical staged files and staged files matching known canonical hashes. Identical filenames alone are **not** duplicates; matching **SHA-256 bytes** are. No fuzzy/semantic duplicate claim is made.

A historical duplicate event represents an upload **already deduplicated**, not a second stored physical file. Reports distinguish these from present staged files and label canonical-object byte re-verification as **not performed**. Missing events from older or externally imported sources cannot be reconstructed from current canonical objects. This audit is scoped to the **Gentoo PDF ingest catalog and inbox**; other formats and separately stored CMS files require the outstanding inventory work.

## Private operator usage (after a separately approved deployment)

Run locally with the Gentoo storage mounted. This command is illustrative; **do not perform any installation or service restart as part of this audit**:

\`\`\`bash
umask 077
python3 gentoo-node/pdf_duplicate_audit.py --root /srv/sirussofts/storage > /path/to/private/pdf-duplicates.json
\`\`\`

Use \`--catalog-only\` to omit pending-inbox hashing, but that is a deliberately narrower report. Do not call it a complete inventory of staged PDFs.

The JSON contains the canonical stored SHA-256, original filename, object path, state, historical duplicate upload count and filenames, and any currently staged matching paths. It includes a summary with scanned-file and skipped/problem counts. The tool only reads source databases and PDFs and writes **nothing** itself; shell redirection is an explicit operator action. **Keep the report private** because filenames and paths can reveal confidential manuals. Do not publish it from the Ubuntu web root, put its content in GitHub, or give it to a model as training material.

Exit code \`0\` means the chosen audit scope was scanned without reported problems; \`2\` means a JSON report was produced but includes unresolved entries (symlinks, inaccessible/unstable PDF files, unmatched historical duplicate events); \`1\` means the audit could not be completed. These states are not proof of actual PDF validity. No data is ever deleted by this tool.

## Deduplication and review policy

- Preserve the existing **one canonical object per exact SHA-256** policy. Reuse extracted content rather than re-extracting identical byte copies, but retain review status and original page citation integrity.
- Never delete canonical objects, archive history, or unreviewed staged files from this report. Existing ingestion alone removes a verified duplicate staging copy according to its own integrity checks.
- Do not merge near-duplicates or same-name different-hash files automatically: revisions, model-year boundaries, annotations, and publishers may differ.
- If a problem is reported or the catalog/object hash is inconsistent, stop and investigate against original files and independent backups. Do not infer a storage saving or attempt destructive deduplication.
- Before marking **live acceptance**, compare the audit with a representative real manual, a staged duplicate, a missing/corrupt source, and the full installed corpus. That has **not** been performed.

## Regression coverage

\`python3 -m unittest discover -s gentoo-node/tests -p 'test_pdf_duplicate_audit.py' -v\`

Six synthetic offline cases cover historical duplicate accounting, pending files, byte identity vs filenames, symlink handling, orphan events, read-only behavior and missing catalog/storage-root safety. These tests do **not** establish real Gentoo storage acceptance.
