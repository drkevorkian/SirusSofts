# Gentoo offline PDF intake (separate from Ubuntu CMS pipeline)

**Status**: Python ingestion implementation, standalone Gentoo OpenRC installer.
The Ubuntu CMS review bridge and admin-side remote controls are **not** part of
this release. Until those are connected, local jobs in "ready" mean **unreviewed
on Gentoo** and are *not* published to users, RAG, training, or the Ubuntu CMS.

## Confirmed storage

- 569 PDFs, approx 7.3 GiB, currently in
  /srv/sirussofts/storage/manuals/inbox
- Dedicated SPCC ext4 SSD, UUID 2531d241-3f0d-4fe8-af82-8059328ad3b3
- /srv/sirussofts/storage is an exact mountpoint, **not RAID0**.
- Old source on RAID0 is still preserved as a rollback directory.

## Operation

The scanner waits for a PDF to be unchanged on two separate scans with at
least 5 seconds between observations and with a last-modified age of >=5 seconds.
The recommended upload convention is filename.pdf.part while transferring,
then atomic rename to filename.pdf after completion. Symlinks are ignored.
It verifies PDF header and SHA256 while copying to a temporary file, commits a
single content-addressed object to manuals/objects/XX/(sha256).pdf, and only then
removes a matching stable inbox original. Duplicate content uses the same object.

Jobs in databases/pdf_ingest.sqlite3 are claimed atomically under SQLite WAL.
Each worker calls local Poppler pdfinfo and pdftotext; extracted page chunks are
held for later human review. No model or network is contacted. Three workers
start by default; up to 64 can be selected on the host for later integration
with the CMS administration UI. Worker launches are staggered by 300 ms.
The queue and excerpts survive restart. No approvals are ever made by the
service. Failed and scanned-only manuals require inspection.

The 1 GiB PDF limit and max 96 MiB extracted text per document are deliberate
initial processing safety limits. No OCR or image understanding is implemented.
Tables/diagrams and drawings require separate source review.

## Install (Gentoo only)

From a pinned, reviewed SirusSofts checkout:

    sudo bash gentoo-node/install-pdf-ingest.sh --plan
    sudo bash gentoo-node/install-pdf-ingest.sh --install
    sudo -u siruspdf python3 /opt/sirus-pdf/pdf_ingest.py --status

Install **does not start/enable** the service. Inspect permissions and readiness,
then start with sudo rc-service sirus-pdf-ingest start. Observe worker logs via
OpenRC / service supervisor. Enable on boot only after a successful trial:
sudo rc-update add sirus-pdf-ingest default.

Only the root administrator can change local worker capacity while the service
is installed; the running process observes changes on its next loop:

    sudo python3 /opt/sirus-pdf/pdf_ingest.py --set-workers 6
    sudo python3 /opt/sirus-pdf/pdf_ingest.py --pause
    sudo python3 /opt/sirus-pdf/pdf_ingest.py --resume
    sudo python3 /opt/sirus-pdf/pdf_ingest.py --retry-failed

Future CMS-to-Gentoo signed configuration (not implemented yet) will control the
same number from Admin > AI without exposing a raw shell execution surface.

**Never** manually set reviewed=1 in this library: explicit approval
requires a completed, authenticated, audited CMS-to-Gentoo review workflow.

## Boundaries and safety

This code does not migrate the Ubuntu CMS SQLite database, does not move or
read existing Ubuntu PDF uploads, and does not make the 569 manuals visible
in the current CMS approval list. Approval and website uploads will require
a follow-on secure bridge without duplicating the entire PDF library onto
the Ubuntu VPS.

SSD readiness is checked before all ingestion/processing, ensuring a missing
SSD never silently writes to RAID0. Keep the intact RAID0 preserved copy until
verified independent backups have been tested.

For large public user-upload workloads, add per-account quotas, malware/parser
sandboxing, granular profile ACLs and audit logging before connecting this
ingestion service to an unauthenticated or user-facing upload route.
