# /GPT — isolated, temporary-access web gateway

**State (2026-11-22): Stage 1 source-only.** `CMS/index.php` dispatches
the exact uppercase `/GPT` route to `CMS/app/gpt_portal.php` **before**
initializing the CMS database. Ordinary GET requests receive a public holding
page. Requests to `/GPT/api/*` are denied (HTTP 403). There are no active
remote-edit, credential-issuance, version-bridge, FTP, SSH or filesystem tools.
**Neither the live Ubuntu nor Gentoo installation has been upgraded.**

This is a **separate feature**. Do not add its items to the 138-item
Continuous Research checklist or treat source implementation as live acceptance.

## Operator intent

- Canonical public URL: `https://sirussofts.com/GPT` (**case-sensitive**).
- Ubuntu CMS hosts the page and will eventually display its own installed
  version plus a Gentoo-reported installed version **obtained through the
  existing loopback SSH reverse tunnel**; distinguish a service's version
  from the installed checkout/release revision.
- Normal visitors see a holding page; no visible editing controls or details.
- An authorized remote client may make **small, specifically granted UI-file
  modifications only**, with file read, write and status capabilities.
- Never permit remote root, sudo, OS shell, SSH command execution, arbitrary
  URL fetch, CMS/Gentoo database queries, or access to database files,
  credentials, configuration, executable server code, models, or private
  documents. This is a *file-scoped HTTPS capability API*, not SSH or FTP.
- The existing authenticated Ubuntu CMS and Gentoo services remain separate.
  A query such as `?source=chatGPT`, user-agent or referer is never evidence
  of identity and must not trigger a privileged handshake by itself.

## Stage 2 design — not yet implemented or enabled

1. **Owner-issued one-session credential.** An administrator generates a fresh,
   cryptographically random 256-bit capability for **each work session** and
   uploads/provisions it directly to a protected server endpoint using HTTPS.
   If the credential is separately encrypted for upload, its private decryption
   key stays exclusively on the server. Do not upload tokens or decryption keys
   to ChatGPT conversations, GitHub, public directories or URL parameters.
   A suitable authenticated connector/client would be required before an
   assistant in this chat could actually use the API.
2. **Least privilege.** Provision the gateway under a dedicated, unprivileged
   Ubuntu service identity with access only to an **explicit administrator-
   maintained file allowlist**. The Gentoo side, if later granted UI access,
   must use a separately scoped identity and signed tunnel method. Different
   host credentials are safer than reusing one password for both. No DB
   permissions, no root, no sudo, no generic shell/FTP/SFTP.
3. **Authentication.** A `/GPT/api/challenge` endpoint may issue a short-lived
   nonce to enrolled clients. Every privileged request must carry a session
   identifier and keyed proof covering method, canonical path, body hash,
   timestamp and a unique nonce; enforce TLS, time skew, one-time nonce use,
   constant-time proof verification and per-session rate limits. A client
   label like `source=chatGPT` has no authorization effect.
4. **Read/write scope.** Allow only `status`, `list`, `read`, `write`,
   `diff`, and `end-session` operations. Restrict to a small named UI
   directory/file allowlist, reject traversal and symlinks, disallow PHP,
   Python, shell, secrets, DB and executable configuration; cap input and
   output sizes. Require the previous content hash on writes to prevent
   overwriting concurrent edits; validate formats, journal audit metadata
   without credential contents, keep a rollback copy, and preferably require
   operator approval for live-facing UI changes.
5. **Server-enforced expiration and revocation.** Store only a non-reversible
   verifier for the session credential outside the public document root.
   The server revokes the capability **immediately** on a successful
   `end-session` operation and deletes any active verifier/authorization
   records. Every request checks the absolute expiry (proposed: maximum
   15 minutes) so an interrupted session becomes unusable **without relying
   on the client to clean up**. Expired verifier files may be swept later;
   no replay is accepted merely because files remain. Issuing a new session
   always requires a new credential; the old one cannot be renewed/reused.
6. **Version status.** After authentication, the Ubuntu controller will query
   a fixed, authenticated read-only Gentoo version endpoint through its
   existing local reverse tunnel. Do not trust arbitrary upstream hosts or
   allow the client to choose a URL. When unreachable, report **unavailable**
   rather than guess a version.
7. **Explicit approval and testing.** Never use an externally accessible
   file editor to install itself, elevate its permissions, or enable new file
   scopes. Test path traversal, symlinks, replay, wrong user, expired session,
   revoked session, body tampering, concurrent writes, rollback, and a
   missing tunnel; review the precise deployment diff and preserve backups
   before separately authorized upgrades.

## Completed source work

- `CMS/app/gpt_portal.php`: static public holding page, no database or
  privileged operations, deny-all API boundary.
- `CMS/index.php`: exact `/GPT` dispatch **before `cms_db()`**.
- `CMS/tests/gpt_portal_test.php`: route casing, public holding page and
  database-independent dispatch checks.

## Deployment boundary

**Live Ubuntu and Gentoo both still use older installed code.**
No installation, credential provisioning, web token upload, reverse-tunnel
change, server account creation or production write was performed for /GPT.
Stage 2 requires source implementation, reviewed isolated credentials, tested
access controls, operator-approved deployment and verified expiration/revocation.
