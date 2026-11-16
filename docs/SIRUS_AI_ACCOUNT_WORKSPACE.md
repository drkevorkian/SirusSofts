# SirusSofts AI account and document workspace

**Status:** draft implementation; not deployed. Review PR #58 before installing.

## Added

- Public AI frontend and links from CMS navigation. Admin links appear only for admin sessions.
- Browser requests go through the CMS-authenticated, rate-limited /ai/api/* routes. Raw /Gentoo/AI/api/* and internal routes are denied by the reviewed Nginx snippet.
- Guest chats require no separate password; 6 requests per 10 minutes per client IP, 1,200-byte prompt cap, 512 output tokens. No guest server-side saved conversations, memory or document uploads.
- Registered CMS viewer accounts have private persisted conversations, explicit saved memories and limited cross-conversation lexical recall. The **2 GiB per-account logical quota** applies only to messages, titles and memories, not documents.
- Category access is private by default. Administrators choose publicly accessible categories and give registered users explicit grants.
- Private PDFs are SHA-256 deduplicated and attached to the uploader's access list. A queued new PDF remains in a private random-name staging directory; the CLI extractor writes page text into SQLite, commits, then deletes the staged original. Exact duplicates reuse existing parsed or queued data.
- CMS-authorized inference uses only permitted knowledge excerpts. Legacy global Gentoo JSONL knowledge is bypassed to prevent unauthorized retrieval. All inference endpoints are loopback-only and external LLM fallback is prohibited.

## Not production-ready yet

1. Storage is currently in the private CMS SQLite database on Ubuntu. The **2 GiB** quota is logical, not physically reserved, and database, WAL and index overhead are not included. The existing 60 GB VPS cannot safely give unlimited accounts their full quota. Move bulk user and document storage onto a provisioned, backed-up Gentoo-managed volume/database before public scale.
2. CMS users still must be created by administrators. Self-service signup is not part of this change.
3. User sessions may need to sign in again because the cookie path changes to / for the shared AI API.
4. The PDF worker must be scheduled as the unprivileged site user; processing is not currently automatic. Before public PDF uploads, sandbox Poppler extraction and validate CPU, memory, malware/format handling, resume and disk quotas. Scanned PDFs require separately reviewed OCR.
5. Initial lexical recall inspects the latest 250 memory entries and 250 past user messages, not every record in a possible 2 GiB account. A disk-backed, owner-scoped full-text search index is still needed.
6. Queue status shows queued/processing/parsed/failed. Accurate per-page progress, orphan staging cleanup, parser-version reindex, and capacity checks are outstanding.
7. The chat frontend requires public Ubuntu. The separate Gentoo-local browser that keeps working without internet has not yet been implemented.
8. Unit tests do not substitute for real browser, PHP-FPM/Hestia Nginx, two-host tunnel, load, threat-model, backup and rollback tests.

## Deployment boundary

Do **not** rerun first-time installers and do not switch off HTTP Basic until the CMS guarded API and new UI have been staged and tested together. Upgrade both hosts at pinned commits with private database backups and verified rollback. All live services and secrets remain unchanged by this PR.
