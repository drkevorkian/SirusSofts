# Private Can-Am manual import: staging, extraction and review

The CMS AI admin page accepts up to 10 PDFs per request (each maximum 200 MiB)
with a combined maximum of 200 MiB per multipart request, stored
in its private, access-controlled directory outside public_html. The
database records original filename, expertise, byte count and SHA-256 hash.
Uploaded files are never executed, parsed or sent to third parties by PHP.
Download of staged files requires CMS admin authentication and CSRF validation.

Check the production PHP upload_max_filesize/post_max_size and Hestia Nginx
client_max_body_size. The smallest limit wins. Do not change global limits
without a reviewed Hestia-specific maintenance plan. A large technical PDF
may exceed the new 200 MiB per-file limit. Keep original page numbering
intact; larger files require an additional reviewed transfer mechanism.

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


## Hestia sirussofts.com production upload limits

These are scoped site settings for uploading a **single 200 MiB PDF**;
the CMS enforces 200 MiB for the total body of uploaded PDF files. A
ten-manual batch cannot contain ten 200 MiB manuals. Submit large manuals
in separate batches to protect the 2 GiB Ubuntu VPS.

The application byte limit is 200 * 1024 * 1024 = 209715200 bytes.
The CMS enforces ten PDFs per request; PHP's default max_file_uploads of 20
is already sufficient and does not need modification.
PHP-FPM / Hestia HTTP limits must permit multipart overhead:

    upload_max_filesize = 200M
    post_max_size = 220M
    max_input_time = 600

For a domain-specific Hestia site, PHP-FPM generally honors these values
in public_html/.user.ini (unless the FPM pool enforces php_admin_value).
Back up a preexisting .user.ini; do not overwrite other site settings.
Hestia's protected Nginx guard already blocks public HTTP access to
/.user.ini. Verify the guard is active and that the site does not
expose dotfiles before creating it.

Check the *effective* Nginx request-size limit for BOTH HTTP and HTTPS
vhosts, plus Apache request limits and any upstream proxy/CDN. Nginx
client_max_body_size must be >= 220m if it otherwise blocks large
requests. Do not blindly append a second client_max_body_size in a server
block; duplicate directives can invalidate Nginx config. Prefer a
reviewed Hestia-compatible per-domain configuration or template if needed.
Verify with nginx -t and review nginx -T. Changing a global Nginx/PHP
limit would also affect unrelated websites; avoid doing so by default.

FPM reload may be needed to clear the .user.ini cache; the Hestia CMS
upgrade helper already reloads php8.3-fpm. There is no need to change
php memory_limit to 220M: the upload is streamed into temporary files
and hash_file reads the saved PDF incrementally.

Sufficient disk space is needed for the PHP upload temp file and
private staging copy (plus any backups); verify the available storage
before attempting a near-limit manual upload.


### Per-site PHP-FPM settings

Apply the following as **site-scoped** .user.ini settings in
/home/sirusweb/web/sirussofts.com/public_html/.user.ini:

    upload_max_filesize = 200M
    post_max_size = 220M
    max_input_time = 600
    max_file_uploads = 10

The reference template is deploy/ubuntu24/sirus-ai-upload.user.ini. Do not
blindly overwrite an existing .user.ini containing other application
settings: preserve those entries. Hestia's reviewed CMS Nginx security
guard denies direct requests to /.user.ini. Reload php8.3-fpm so settings
are picked up promptly, then verify the homepage remains HTTPS 200 and
the file itself yields HTTP 404.

The production HTTPS Nginx server block must allow >=220m request bodies.
Do not add a second client_max_body_size directive without inspecting
the effective Hestia-generated vhost, and do not change global PHP
values for unrelated applications by default.
