#!/usr/bin/env bash
# Install ONLY Gentoo's offline PDF queue; no CMS, AI/tunnel/RAID modifications.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode=--plan
if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
[[ $# -eq 0 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: bash gentoo-node/install-pdf-ingest.sh --check|--plan|--install'
base="$(cd "$(dirname "$0")" && pwd)"
python3 -m py_compile "$base/pdf_ingest.py" || fail 'PDF module has invalid Python syntax'
if [[ "$mode" == --check ]]; then
  echo 'PASS: offline PDF module and installer syntax; no changes made'
  exit 0
fi
[[ "$(id -u)" == 0 ]] || fail 'Gentoo root required for plan/install'
[[ -f /etc/gentoo-release ]] || fail 'Not a Gentoo host'
for cmd in rc-service rc-update mountpoint setfacl find install useradd findmnt blkid; do
  command -v "$cmd" >/dev/null || fail "Missing prerequisite: $cmd"
done
for cmd in pdfinfo pdftotext; do
  command -v "$cmd" >/dev/null || fail "Missing Poppler tool: $cmd"
done
root=/srv/sirussofts/storage
[[ -d "$root" && ! -L "$root" ]] || fail 'Expected storage directory missing'
mountpoint -q "$root" || fail 'SSD is not mounted; no RAID0 fallback'
[[ "$(findmnt -M "$root" -n -o FSTYPE)" == 'ext4' ]] ||
  fail 'Storage must be the reviewed ext4 SSD'
[[ "$(blkid -s UUID -o value /dev/sdc1)" == '2531d241-3f0d-4fe8-af82-8059328ad3b3' ]] ||
  fail 'Unexpected SSD UUID or /dev/sdc1 has changed'
[[ -d "$root/manuals/inbox" && ! -L "$root/manuals/inbox" ]] ||
  fail 'PDF inbox missing/linked'
[[ ! -e /etc/init.d/sirus-pdf-ingest && ! -L /etc/init.d/sirus-pdf-ingest ]] ||
  fail 'Service already exists; manual upgrade review required'
[[ ! -e /opt/sirus-pdf && ! -L /opt/sirus-pdf ]] ||
  fail 'Application directory already exists; manual upgrade review required'
echo 'PLAN: Install separate /opt/sirus-pdf Python offline watcher, 3 workers default.'
echo 'PLAN: Siruspdf no-login account and private SQLite data on the dedicated SSD.'
echo 'PLAN: Add ACL permissions for siruspdf to read and ingest existing inbox PDFs.'
echo 'PLAN: Create an OpenRC service, but DO NOT start or enable it.'
echo 'PLAN: No change to existing Ubuntu worker, CMS, tunnel, or Gentoo LLM.'
if [[ "$mode" == --plan ]]; then
  echo 'PASS: No changes made.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive console required'
read -r -p 'Type INSTALL PDF WATCHER to install (no service start): ' response
[[ "$response" == 'INSTALL PDF WATCHER' ]] || fail 'Operator declined'
if ! id siruspdf >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/siruspdf --shell /sbin/nologin siruspdf
fi
install -d -o root -g root -m 0755 /opt/sirus-pdf
install -o root -g root -m 0755 "$base/pdf_ingest.py" /opt/sirus-pdf/pdf_ingest.py
install -d -m 0750 -o siruspdf -g siruspdf "$root/manuals/objects" "$root/databases"
# The watcher needs directory deletion rights to atomically move completed PDFs.
# Existing files are preserved; no chown, rewrite, or bulk manual copying.
setfacl -R -m u:siruspdf:rwX "$root/manuals/inbox"
find "$root/manuals/inbox" -type d -exec setfacl -m u:siruspdf:rwx {} +
find "$root/manuals/inbox" -type d -exec setfacl -d -m u:siruspdf:rwx {} +
install -o root -g root -m 0755 "$base/openrc/sirus-pdf-ingest" /etc/init.d/sirus-pdf-ingest
echo 'PASS: Gentoo PDF watcher installed but NOT STARTED and NOT ENABLED.'
echo 'Preflight as service user:'
echo '  sudo -u siruspdf /usr/bin/python3 /opt/sirus-pdf/pdf_ingest.py --status'
echo 'Activate after reviewing: rc-service sirus-pdf-ingest start'
echo 'Enable after testing: rc-update add sirus-pdf-ingest default'
echo 'IMPORTANT: CMS approval UI and remote admin control are NOT connected yet.'
