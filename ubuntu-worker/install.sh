#!/usr/bin/env bash
# Install or update the fixed-task, unprivileged Ubuntu Update Center worker.
# This is NOT a privileged OS package updater and never changes Hestia/Nginx.
set -Eeuo pipefail
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
    fail 'Usage: ubuntu-worker/install.sh --check|--plan|--install'
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/worker.py" --check
[[ -f "$here/sirus-ubuntu-worker.service" ]] || fail 'Service template missing'
if [[ "$mode" == --check ]]; then
    python3 -m py_compile "$here/worker.py"
    echo 'PASS fixed-task Ubuntu worker source and syntax checks'
    exit 0
fi
[[ $(id -u) -eq 0 ]] || fail 'Ubuntu root required'
[[ -f /etc/os-release ]] || fail 'OS information missing'
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail 'This installer is Ubuntu-only'
id sirusweb >/dev/null || fail 'Hestia website account sirusweb missing'
base=/home/sirusweb/web/sirussofts.com/private/sirus-cms
nodes="$base/nodes"
[[ -d "$base" && ! -L "$base" && -f "$base/data/cms.sqlite3" ]] ||
    fail 'Existing private CMS installation not found'
[[ -d "$nodes" && ! -L "$nodes" && -d "$nodes/keys" && ! -L "$nodes/keys" ]] ||
    fail 'Existing CMS node broker/keys not configured'
[[ -f "$nodes/work.sqlite3" && ! -L "$nodes/work.sqlite3" ]] ||
    fail 'Existing broker DB is missing'
[[ -f /usr/bin/apt-get ]] || fail 'Ubuntu apt-get missing'
unit=/etc/systemd/system/sirus-ubuntu-worker.service
exe=/usr/local/libexec/sirus-ubuntu-worker.py
[[ ! -L "$unit" && ! -L "$exe" ]] || fail 'Unsafe service/executable symlink'
if [[ -e "$unit" ]] && ! cmp -s "$unit" "$here/sirus-ubuntu-worker.service"; then
    fail 'Installed systemd unit differs from reviewed template; inspect manually'
fi
if [[ "$mode" == --plan ]]; then
    echo "Ubuntu CMS task broker: $nodes/work.sqlite3"
    echo 'Install/update unprivileged Ubuntu worker; fixed APT simulation only.'
    echo 'No OS packages, CMS content, node keys, Hestia domains or SSH settings modified.'
    echo "Executable: $exe"
    echo "Service: $unit"
    exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -r -p 'Type ENABLE UBUNTU UPDATE CHECKS: ' answer
[[ "$answer" == 'ENABLE UBUNTU UPDATE CHECKS' ]] || fail 'Operator declined'
install -d -m 0755 -o root -g root /usr/local/libexec
backup=''
if [[ -f "$exe" ]]; then
    backup="$exe.before-update-center-$(date +%s)"
    cp -p -- "$exe" "$backup"
fi
tmp="$(mktemp /usr/local/libexec/.sirus-ubuntu-worker-XXXXXXXX)"
trap 'rm -f "$tmp"' EXIT
install -m 0755 -o root -g root "$here/worker.py" "$tmp"
python3 -m py_compile "$tmp" || fail 'Staged Python syntax check failed'
mv -f -- "$tmp" "$exe"
if [[ ! -f "$unit" ]]; then
    install -m 0644 -o root -g root "$here/sirus-ubuntu-worker.service" "$unit"
fi
systemctl daemon-reload
if ! systemctl enable --now sirus-ubuntu-worker.service ||
   ! systemctl restart sirus-ubuntu-worker.service ||
   ! systemctl is-active --quiet sirus-ubuntu-worker.service; then
    if [[ -n "$backup" ]]; then
        cp -p -- "$backup" "$exe"
        systemctl restart sirus-ubuntu-worker.service || true
        echo "Restored previous executable from $backup" >&2
    else
        systemctl disable --now sirus-ubuntu-worker.service >/dev/null 2>&1 || true
        rm -f -- "$unit" "$exe"
        systemctl daemon-reload
    fi
    fail 'Ubuntu worker did not start; inspect journalctl -u sirus-ubuntu-worker'
fi
echo 'PASS Ubuntu Update Center worker running, restricted to ubuntu-primary work.'
[[ -z "$backup" ]] || echo "Previous worker backed up: $backup"
