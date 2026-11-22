# Gentoo PDF Admin Integration — reviewed two-host rollout

Feature status: code implementation staged in GitHub. NOT automatically deployed.
The 1200-ish already-processed Gentoo PDF records remain authoritative; no
re-ingestion and no transfer of original PDFs to Ubuntu is required.

## Architecture

The logged-in Ubuntu CMS administrator opens /admin/ai on sirussofts.com.
PHP fetches signed status and bounded previews from
http://127.0.0.1:18480/internal/pdf-admin (EXISTING reverse SSH tunnel).
The Gentoo dashboard listening at 127.0.0.1:18080 verifies each message using
the existing Gentoo node's 32-byte HMAC key, with a PDF-specific domain prefix,
node identity, nonce replay protection and a 120-second timestamp window.
Only explicit status, preview, pause, resume, workers, retry, approve and
unapprove operations are allowed. Browser never receives a signing key.
Server-side CMS session, admin authorization and CSRF protect commands.

Gentoo sirusnode receives narrowly scoped ACL access to the local
databases/pdf_ingest.sqlite3 SQLite database. Only this single database is
modified by worker-control and approval commands. The Ubuntu CMS database,
the Gentoo PDF object library, SSH tunnel and existing worker remain intact.

Approved is a status stored in Gentoo SQLite and audited in pdf_admin_audit,
but **existing Ubuntu RAG/LoRA exports do not yet consume approved Gentoo
excerpts**. Approvals do not directly train model weights. Preview text is
truncated to eight <=1400-character chunks per request; source PDFs should
be reviewed separately before approving. Original PDFs never cross tunnel.

## Gentoo rollout

Review the pinned commit in the repository first. Run on Gentoo:

    sudo bash gentoo-node/install-pdf-admin-bridge.sh --plan

This should verify the existing SSD, queue and dashboard. Then, on operator
approval, after backing up the running dashboard:

    sudo bash gentoo-node/install-pdf-admin-bridge.sh --apply

The installer does not touch PDFs or restart the PDF extraction service.
It DOES briefly restart only sirus-gentoo-dashboard; existing signed live
WebSocket sessions will reconnect. Existing SSH tunnel port 18480 is reused,
with no new public listener. It preserves old dashboard code in a private
root-owned rollback directory and grants sirusnode ACLs to the PDF DB.

Verify signed Gentoo API locally, with the configured node key (no key output):

    sudo -u sirusnode python3 -B gentoo-node/pdf_admin_probe.py

### Ubuntu rollout — after Gentoo bridge is confirmed

The existing reviewed CMS upgrade helper requires `SIRUS_EXPECTED_COMMIT` to
match the clean, pinned checkout before either plan or apply:
`SIRUS_EXPECTED_COMMIT=<reviewed-40-hex-SHA> bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com`.
preflights the in-place upgrade and preserves a consistent SQLite snapshot
and rollback copy when applying. Do not run any fresh system installers. The
CMS update supplies CMS/app/ai_gentoo.php, modified admin and ai_training,
and assets/gentoo-pdf-admin.js. Use a reviewed pinned checkout, not a
blind download of latest main.

The default node id is gentoo-home; environment variable
SIRUS_GENTOO_PDF_NODE_ID may set a different pre-enrolled node name. The
matching existing private raw 32-byte HMAC node key must be readable by the
CMS PHP user at <cms-private>/nodes/keys/<node-id>.key. Never expose this key
in a web page, log, shell paste, or GitHub repository.

### Failure behavior

If tunnel, key or Gentoo database is unavailable, the Gentoo admin panel
shows an error; the rest of the CMS and Ubuntu PDF uploads continue to work.
No auto retry or fallback into the live Ubuntu CMS SQLite database.
Read-only status poll (12 seconds) shows counts and existing visible rows;
refresh the page for changed actions or new page contents.
The CMS audit trail records administrator-issued control actions.
The Gentoo database records every accepted control action.

### Scope

- Admin counts, SSD capacity, pause/resume, workers 3–64, status search/filter
- Pagination (30/page), bounded preview, retries, per-document approval
- One confirmed bulk approval of **all** currently ready manuals with nonblank
  stored extracted text, across all pages and independent of filters. SQL
  transaction is atomic, repeat-safe, and logged with approved row count.
  Failed/processing/queued/textless/previously approved manuals are excluded.
  The form requires an authorization checkbox and browser confirmation.
  Bulk approval is the administrator's corpus-level trust decision; it does
  NOT represent individual text verification or train models automatically.
- Python replay/HMAC tests and PHP wiring tests
- No automatic model training or cross-node search/RAG export
- No user-facing PDF upload transfer or source-PDF download over web yet
- No public Gentoo SSH or network listener; existing port 18480 only
