# SirusSofts AI — Private conversation titles

## User experience

- Signed-in users may provide an exact title in the **+ New conversation**
  dialog before typing their first prompt. The optional title is saved as
  private CMS metadata and protected against automatic overwrites. Leaving it
  blank creates the temporary name `New conversation`. The first
  **substantive user message** then creates a short, topic-based title
  immediately after that message is accepted by the CMS, without waiting for
  model inference and without sending a separate naming request to an outside LLM. Example:
  `2026 HD10 wire diagram` → `2026 HD10 Wire Diagram`.
- A user may specify an exact title at the beginning of any message:
  `Name this conversation: 2026 HD10 Wiring`. They can add their
  actual question on the next line. This sets a locked user title, rather
  than treating the instruction as a mere suggestion.
- A `Rename` control in the current-conversation heading and a pencil
  button beside each conversation in the sidebar open the same accessible
  save/cancel dialog. This works on mobile via the heading even when the
  sidebar is collapsed. Manual names remain unchanged when other questions
  are sent. In-chat explicit rename instructions also work at any time.
- Old conversations that are still named `New conversation` are renamed
  during the reviewed CMS upgrade from their first informative user turn.
  Greeting-only and entirely empty conversations are left as placeholders.
  Existing custom names are not modified. Chronological chat ordering is
  retained for backfilled titles.

## Persistence and security

- The existing `cms_ai_conversations.title` column is reused, with one
  additive `title_locked` flag that differentiates user-chosen names from
  automatic names. No conversation records, documents, original messages,
  uploads, or LLM model files are replaced.
- `POST /ai/api/conversations/<32-character-id>/title` accepts
  `{"title": "..."}` and requires the active CMS owner session, CSRF token,
  HTTPS and normal API rate limiting. Titles are limited to one line of
  1–160 UTF-8 bytes and stored via parameterized queries.
- Title changes are performed in SQLite transactions. The old-title-to-
  new-title byte delta is accounted within each account's 2 GiB text quota.
  Attempts to rename another account's conversations fail without revealing
  their content.
- Only a direct user message can issue an in-chat title instruction.
  Retrieved manual pages, document attachments, model outputs and saved
  memories cannot rename a conversation.
- The automatic title builder uses the first informative words in the
  user's message. It is a deterministic *local CMS feature*, not an extra
  billed model request and not a claim to summarize the entire conversation.

## Safe deployment

1. Update **Ubuntu** `/root/SirusSofts` to the fully reviewed and
   GitHub-tested pinned commit. Do not run the main first-time installers.
2. Run
   `bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com`,
   then `--apply` only after reviewing the plan. It makes a consistent
   private SQLite/code backup, migrates the additive title flag and performs
   one-time backfill.
3. Run
   `SIRUS_EXPECTED_COMMIT=<PIN> bash sirus-ai-dual-tunnel/ubuntu/upgrade-in-place.sh --plan`,
   then `--apply` after CMS success to update the frontend HTML/JS/CSS.
4. Verify live CMS routing
   `bash deploy/ubuntu24/07-audit-cms-routing.sh --live --user sirusweb --domain sirussofts.com`.
   In an authenticated browser, confirm existing chats have topic names,
   manual renaming works and persists after reload, first-line explicit
   instructions persist, the optional title at conversation creation is saved
   and locked, and a blank new-chat title auto-names after a useful prompt.
   Check canceling the dialog, guest behavior and UTF-8 byte limits.
5. Existing backups and rollback contracts remain unchanged. No Gentoo
   deployment, model restart, SSH tunnel change, or PDF reindex is required.

## Regression tests

`php CMS/tests/ai_conversation_titles_test.php` checks normal and explicit
titles, lock preservation, owner-scoped edits, UTF-8 length/control
validation, quota deltas, schema migration and legacy backfill. PHP CI
also checks the original workspace and other CMS tests; JavaScript syntax
checks cover the updated frontend.
