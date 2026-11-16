# SirusSofts AI: reviewed in-place update plan

This is a code deployment plan, NOT a report of installation. No first-time installer, tunnel/key replacement, OS upgrade or new VPS is required.

## Upgrade order

1. Pin both hosts' clean Git checkout to the same reviewed 40-hex commit and ensure the databases have private off-host backups.
2. Gentoo: run the new sirus-ai-dual-tunnel/gentoo/upgrade-in-place.sh --plan, with environment variable SIRUS_EXPECTED_COMMIT set to that full commit SHA. After approving the plan, run --apply interactively. Only sirus-ai-input is restarted, not the running CPU/GPU model workers, the original SSH tunnel or the Gentoo infrastructure agent. It backs up router code and input SQLite using the online backup API.
3. Ubuntu CMS: run the existing deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com, then --apply interactively if verified. Do not delete or replace the CMS database, credentials or existing Hestia configuration. Run the 07-audit-cms-routing.sh --live check afterward.
4. Ubuntu AI: run sirus-ai-dual-tunnel/ubuntu/upgrade-in-place.sh --plan with SIRUS_EXPECTED_COMMIT pinned, then --apply after the guarded CMS API is live. The gateway is backed up and restarted; Nginx HTTPS AI location is updated last. Smoke tests require anonymous UI to return 200, raw API to return 404 and /ai/api/state to report guest with local_only true.
5. Perform genuine two-host tests: anonymous guest (limited), separate user accounts and memory isolation, authorized categories, owner-only private PDF access, admin links, SSH tunnel outage/recovery, 2 GiB quota cap and local model-only routing.
6. Schedule CMS/bin/ai-document-queue.sh under the sirusweb account only AFTER Poppler sandbox and private storage checks pass. The wrapper prevents overlapping jobs. Verify resume and deletion of the temporary original only after extracted database pages commit.

## Pending production gate checks

- Physical document storage currently uses a bounded, private CMS SQLite on the 60 GiB Ubuntu VPS, not the future Gentoo-backed high-capacity corpus. Uploads are limited by a separate shared cap plus a 5 GiB minimum free-space guard. Chat/memory account quotas are logical, not physically reserved.
- The parser is currently time-bounded, but untrusted publicly uploaded PDFs need stronger OS isolation before worker scheduling.
- Hestia rewrite and reverse-proxy client-IP accounting, Nginx Basic auth removal, real user sessions, and multi-user replay must pass live staging checks.
- Independent Gentoo-local browser access without internet, unrestricted document size, automated OCR, and all-custom admin GPU controls remain separate work.
- The previously merged Gentoo infrastructure telemetry collector is a different OpenRC agent. Its actual installation/NVML permissions are not altered by these AI-router upgrade scripts.
- Installation is not authorized by green source tests alone. Do not run any first-time installer on an already configured system.

All scripts' --plan mode is read-only. Never claim a code commit is proof that a live host has been upgraded.
