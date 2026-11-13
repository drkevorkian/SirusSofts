# SirusSofts source-code update checks

The Update Center must distinguish **operating-system packages** from
**SirusSofts application code**. The new "Check GitHub source revisions"
action is an authenticated CMS POST (CSRF protected) that creates a private
refresh request marker. A restricted systemd timer on Ubuntu processes the
request using the **existing** GitHub importer credential, stored as
`/etc/sirussofts/github.token` and passed with `LoadCredential`. Neither
PHP, Gentoo, the WebSocket, nor browser clients receive the token.

The checker reads the approved deployment branch
`drkevorkian/SirusSofts:main`.
It first resolves an immutable Git commit and retrieves selected files pinned
to that SHA. For each file, it records a SHA-256 digest in
`/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/source_index.json`.
No source file is installed, executed, or moved into the live website.

The private WebSocket server compares:

- **Ubuntu:** selected installed CMS PHP/JS files, the unprivileged Ubuntu
  worker and the local socket server
- **Gentoo:** signed installed agent, dashboard and tunnel file hashes
  reported in the existing authenticated heartbeat

The Update Center shows **Matches approved GitHub**, **Differs from approved
GitHub — review**, or **Not installed / not reported**. Differences can mean
a newer GitHub revision, a locally patched installation, or a different
branch; they are **not** proof that installing the newer file is safe.
The latest approved *commit* and check timestamp are displayed. The
periodic index refresh is six-hourly, and an administrator can request
an earlier refresh; the timer processes requests within approximately one
minute. The browser WebSocket updates the display automatically.

## Deployment (review, then apply separately)

Merge the GitHub source checks branch only after its WebSocket and Gentoo
monitoring prerequisites land in the branch tracked by Ubuntu. Review all
local changes and preserve any SSH/Hestia overrides.

1. Upgrade CMS using the existing audited
   `deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan` then reviewed
   `--apply` flow. It preserves private SQLite and administrator credentials.
2. Upgrade the **separate** WebSocket service executable using the reviewed
   realtime installer. The CMS upgrade does not replace its Python executable.
3. On Ubuntu run:
   `bash deploy/ubuntu24/12-install-github-source-index.sh --check`
   and `--plan`, then the interactive `--install`.
   It requires the already configured GitHub importer credential. Run
   `systemctl status sirus-github-source-index.service --no-pager`
   and inspect its logs without printing the token.
4. Deploy the Gentoo node's updated `client.py` after backing up the installed
   file, retaining `/etc/sirus-gentoo-node/config.json`, the HMAC identity,
   and the reverse SSH tunnel. Restart **only** `sirus-gentoo-node`.
5. Visit `https://sirussofts.com/admin/updates`, request "Check GitHub
   source revisions", and watch the live source comparison update.

**This feature is a read-only source version checker.** Applying source
releases still needs a privileged host-specific deployment helper with
manifest pinning, backups, migration strategy, health checks, rollback and
approval. A user's request to check for new commits must never automatically
fetch arbitrary GitHub code into the web root or Gentoo service directory.

Future additions: registered applications beyond fixed framework components,
tag/release provenance, build artifacts, signed release manifests, migration
requirements, release notes, dependency checks, approvals and safe rollback.
