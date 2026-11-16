#!/usr/bin/env bash
# Run queued PDF parsing as existing Hestia user, never as root.
# Schedule this wrapper no more frequently than once per minute after reviewing
# the Poppler parser sandbox and private storage capacity.
set -Eeuo pipefail
[[ "$(id -un)" == sirusweb ]] || { echo "FAIL: run as sirusweb, never root" >&2; exit 1; }
base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
private="/home/sirusweb/web/sirussofts.com/private/sirus-cms"
[[ -f "$base/ai-document-worker.php" && -d "$private/data" ]] || {
  echo "FAIL: reviewed CMS worker/database absent" >&2; exit 1;
}
[[ ! -L "$private/data" ]] || { echo "FAIL: linked storage refused" >&2; exit 1; }
exec 9>"$private/data/ai-document-queue.lock"
/usr/bin/flock -n 9 || exit 0
# Run one queued file at a time; the database keeps per-page progress.
# A timeout/error of one input must never create overlapping parsers.
/usr/bin/nice -n 15 /usr/bin/timeout 220s /usr/bin/php "$base/ai-document-worker.php"
