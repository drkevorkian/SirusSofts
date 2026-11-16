#!/usr/bin/env bash
# Explicit, reversible upgrade of ONLY the existing Gentoo dashboard PDF API.
# No public ports, no tunnel changes, no PDF moves, no worker restart.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode=--plan
if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
[[ $# == 0 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --apply ) ]] ||
  fail 'Usage: gentoo-node/install-pdf-admin-bridge.sh --check|--plan|--apply'
here="$(cd "$(dirname "$0")" && pwd)"
if [[ "$mode" == --check ]]; then
  python3 -B "$here/dashboard.py" --check
  python3 -m unittest discover -s "$here/tests" -p test_pdf_admin.py
  echo 'PASS: PDF admin bridge offline checks. No running services changed.'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Run as root on Gentoo'
[[ -f /etc/gentoo-release ]] || fail 'Not Gentoo Linux'
for tool in rc-service mountpoint setfacl getfacl runuser python3 stat findmnt blkid; do
  command -v "$tool" >/dev/null || fail "Missing prerequisite: $tool"
done
[[ -f /opt/sirus-gentoo-node/dashboard.py &&
   -f /opt/sirus-gentoo-node/client.py &&
   -f /etc/init.d/sirus-gentoo-dashboard ]] ||
  fail 'Existing Gentoo dashboard is not installed'
[[ -f /opt/sirus-pdf/pdf_ingest.py ]] || fail 'Gentoo PDF worker is not installed'
[[ -f /etc/sirus-gentoo-node/config.json ]] || fail 'Gentoo node config missing'
id sirusnode >/dev/null || fail 'Existing Gentoo dashboard service account missing'
id siruspdf >/dev/null || fail 'Existing Gentoo PDF worker account missing'
root=/srv/sirussofts/storage
db="$root/databases/pdf_ingest.sqlite3"
mountpoint -q "$root" || fail 'Dedicated SSD mount missing'
[[ "$(findmnt -M "$root" -n -o FSTYPE)" == ext4 ]] || fail 'Expected ext4 SSD'
[[ "$(blkid -s UUID -o value /dev/sdc1)" == "2531d241-3f0d-4fe8-af82-8059328ad3b3" ]] ||
  fail 'Unexpected SSD UUID; stop'
[[ -f "$db" && ! -L "$db" ]] || fail 'Existing Gentoo PDF queue missing or linked'
[[ -d "$root/databases" && ! -L "$root/databases" ]] ||
  fail 'Unsafe database directory'
[[ ! -L /opt/sirus-gentoo-node/dashboard.py &&
   ! -L /opt/sirus-gentoo-node ]] || fail 'Linked Gentoo dashboard installation'
# Do not perform a network request in --plan.
echo 'PLAN: Preserve a private backup of installed Gentoo dashboard.py.'
echo 'PLAN: Install updated dashboard.py and offline signed pdf_admin.py.'
echo 'PLAN: Grant sirusnode ACL access to ONLY the PDF queue database directory/files.'
echo 'PLAN: Restart sirus-gentoo-dashboard briefly, then validate signed status API.'
echo 'PLAN: Existing reverse SSH 18480->18080 remains unchanged; PDF worker is not restarted.'
echo 'PLAN: PDF data, approvals, CMS database and current Ubuntu site are preserved.'
if [[ "$mode" == --plan ]]; then
  echo 'PASS: Plan checks complete; no changes.'
  exit 0
fi
rc-service sirus-gentoo-dashboard status >/dev/null 2>&1 ||
  fail 'Gentoo dashboard is not running; check service health before upgrading.'
python3 -B "$here/dashboard.py" --check ||
  fail 'New dashboard source did not pass its self-check'
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -r -p 'Type ENABLE GENTOO PDF ADMIN to upgrade dashboard bridge: ' confirm
[[ "$confirm" == 'ENABLE GENTOO PDF ADMIN' ]] || fail 'Not approved'
backup="$(mktemp -d /root/sirus-pdf-admin-bridge-XXXXXXXX)"
chmod 0700 "$backup"
cp -p /opt/sirus-gentoo-node/dashboard.py "$backup/dashboard.py"
[[ ! -e /opt/sirus-gentoo-node/pdf_admin.py ]] || \
  cp -p /opt/sirus-gentoo-node/pdf_admin.py "$backup/pdf_admin.py"
getfacl -p "$root" "$root/databases" "$db" > "$backup/previous-acls.txt"
for suffix in '-wal' '-shm'; do
  if [[ -f "$db$suffix" ]]; then
    getfacl -p "$db$suffix" >> "$backup/previous-acls.txt"
  fi
done
# Permission changes are scoped to the DB; no access granted to original PDFs.
setfacl -m u:sirusnode:--x "$root"
setfacl -m u:sirusnode:rwx,u:siruspdf:rwx "$root/databases"
setfacl -d -m u:sirusnode:rwx,u:siruspdf:rwx "$root/databases"
setfacl -m u:sirusnode:rw-,u:siruspdf:rw-,m::rw- "$db"
for suffix in '-wal' '-shm'; do
  [[ ! -f "$db$suffix" ]] || setfacl -m u:sirusnode:rw-,u:siruspdf:rw-,m::rw- "$db$suffix"
done
runuser -u sirusnode -- test -w "$db" || fail 'sirusnode cannot write SQLite queue'
runuser -u siruspdf -- test -w "$db" || fail 'siruspdf lost write permissions'
install -o root -g root -m 0644 "$here/pdf_admin.py" /opt/sirus-gentoo-node/pdf_admin.py
install -o root -g root -m 0755 "$here/dashboard.py" /opt/sirus-gentoo-node/dashboard.py
install -o root -g root -m 0755 "$here/pdf_admin_probe.py" /opt/sirus-gentoo-node/pdf_admin_probe.py
python3 -B /opt/sirus-gentoo-node/dashboard.py --check || fail 'New dashboard self-check failed'
rc-service sirus-gentoo-dashboard restart || {
  cp -p "$backup/dashboard.py" /opt/sirus-gentoo-node/dashboard.py
  [[ ! -f "$backup/pdf_admin.py" ]] || cp -p "$backup/pdf_admin.py" /opt/sirus-gentoo-node/pdf_admin.py
  rc-service sirus-gentoo-dashboard restart || true
  fail "New dashboard start failed; attempted rollback. Inspect $backup"
}
runuser -u sirusnode -- python3 -B /opt/sirus-gentoo-node/pdf_admin_probe.py \
  --config /etc/sirus-gentoo-node/config.json || \
  fail "Signed local PDF bridge probe failed. Keep backup: $backup"
echo 'PASS: Gentoo signed PDF admin bridge is live on the existing private dashboard tunnel.'
echo "Rollback backup: $backup"
echo 'NEXT: after verifying this local bridge, deploy reviewed CMS update on Ubuntu.'
