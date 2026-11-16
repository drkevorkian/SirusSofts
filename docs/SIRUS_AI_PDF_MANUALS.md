# SirusSofts automatic PDF manual extraction and approval

## The normal workflow

1. Download the manuals from BRP.
2. Upload once in **Admin → AI Knowledge & Training → Upload service manuals (PDF)**.
3. The site's sandboxed background worker extracts page-attributed text from the
   original private Ubuntu file. Existing **Staged** uploads enter the queue
   automatically at CMS schema migration; no re-upload is needed.
4. In the same PDF table, preview the extracted snippets and click
   **Approve text** after checking their correctness. Approved excerpts can
   participate in the CMS's authorized RAG retrieval and reviewed exports.

**No download from SirusSofts, no Gentoo copy, no JSONL re-upload.**
The standalone Gentoo JSONL importer remains only as a collapsed legacy option.

## Limits

- **1 GiB per PDF** (1,073,741,824 bytes), **1 GiB combined file bytes per
  upload request**, at most 10 PDFs per request.
- PHP upload_max_filesize=1024M, post_max_size=1100M (multipart overhead),
  max_input_time=1200 seconds; Nginx HTTPS client_max_body_size=1100m.
- Site-scoped settings only; never change PHP/Nginx globals. The separate
  activation helper merges the .user.ini settings with existing site overrides.
- Private manual capacity is temporarily 20 GiB and disk free space must
  remain at least 5 GiB after adding the uploaded PDF. The deployment activation
  preflight requires 6 GiB free.
- One parser job per minute; one document per invocation; pdf-to-text limited
  to 240 seconds, 64 MiB of resulting text and 20,000 text excerpts.
- The systemd worker is sirusweb-only, network isolated, filesystem restricted,
  with 900 MiB memory limit and 90% CPU quota.

Large technical PDFs, especially scanned images, may contain no selectable text.
Those appear as **Failed — retry available** and remain in private storage for
manual/OCR follow-up. The pipeline never claims to understand diagrams, torque
tables, scanned figures or safety-critical values. Approval is explicit, not
automatic.

## Install on the existing Ubuntu Hestia VPS

1. Update GitHub checkout to the approved main commit.
2. Run `bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb
   --domain sirussofts.com`, then `--apply` after reviewing, then the existing
   `07-audit-cms-routing.sh --live` audit.
3. Run `bash deploy/ubuntu24/14-enable-ai-pdf-pipeline.sh --plan`.
   Resolve any failing storage, package, or Nginx checks.
4. Run `bash deploy/ubuntu24/14-enable-ai-pdf-pipeline.sh --apply` and confirm
   with **ENABLE PRIVATE PDF PIPELINE**.
5. Verify `systemctl status sirus-ai-pdf-worker.timer`,
   `journalctl -u sirus-ai-pdf-worker.service`, private staging rows and the
   `/.user.ini` HTTP 404 guard. Confirm an actual 350 MiB HTTPS upload works.

The activation script never runs a first-time installer, changes Gentoo,
touches SSH tunnels, or replaces the CMS database.

The Hestia Nginx HTTPS snippet is installed only if Nginx accepts it. If Hestia
already sets a conflicting server-level client_max_body_size, activation
**stops** and removes the candidate snippet. Resolve the conflict in the
site's Hestia-managed vhost/template, not by modifying global Nginx settings.

## Security and rollback

PDF binaries are parsed only in a separate unprivileged systemd service under
PrivateNetwork, ProtectSystem, ProtectHome and memory/CPU limits. PHP-FPM
uploads/stages and validates PDF signatures but never parses them in the request.
No unreviewed text reaches model context or export. Existing original PDFs
remain in private storage after processing and may be downloaded by administrators
for quality review. The CMS upgrade backs up its code and SQLite snapshot,
and the activation backs up pre-existing per-site .user.ini.

The admin table shows queued, extracting, ready for review, failed, and approved
states. Initial deployments should be smoke-tested with representative manuals,
including a scanned PDF, before inviting third-party uploads.
