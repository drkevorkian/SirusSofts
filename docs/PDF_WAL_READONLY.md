# Gentoo PDF WAL access: source fix pending deployment

The operator confirmed 4,700 PDF records and 309,285 extracted sections.
When sirus-pdf-ingest stopped, the last SQLite connection removed WAL and SHM
sidecars. The separate AI reader sirusai, correctly denied directory write,
could not run even a SELECT and the inventory recorded OperationalError.
Starting ingestion and granting exact read-only sidecar ACLs restored the
full inventory and cleared the stored error.

The new source helper gentoo-node/pdf_wal_guard.py uses the documented
SQLITE_FCNTL_PERSIST_WAL SQLite file-control flag on a separate final
trusted writer handle. This retains sidecars after normal shutdown.
The ingestion service also grants read-only ACLs to the two sidecar
files, not the source database or the containing directory.

Offline tests cover writer shutdown without directory write rights,
repeat opens, missing/symlinked source refusal, exact read-only ACL
arguments and independent installation without the AI account.

This code is NOT deployed. Do not rerun the PDF first-time installer
on the existing host. A reviewed in-place PDF-worker upgrade with
consistent backups and recovery validation is still needed. The
other independent writer in the PDF admin bridge also needs its last
close audited; it could still remove sidecars while ingestion is stopped.
No model weights, live server services or PDF sources were changed.

SQLite references:
https://www.sqlite.org/wal.html
https://www.sqlite.org/walformat.html
