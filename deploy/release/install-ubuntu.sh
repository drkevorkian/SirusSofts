#!/usr/bin/env bash
# Install a root-owned, allowlisted release controller. Does not deploy any code.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
    fail 'Usage: deploy/release/install-ubuntu.sh --check|--plan|--install'
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/source_executor.py" --check
python3 "$here/ubuntu_controller.py" --check
python3 - "$here/source_executor.py" "$here/ubuntu_controller.py" <<'PY'
import ast,sys
for name in sys.argv[1:]:
    ast.parse(open(name,encoding='utf8').read())
print("PASS release controller and executor syntax")
PY
if [[ "$mode" == --check ]]; then exit 0; fi
[[ $(id -u) == 0 ]] || fail 'Interactive Ubuntu root required'
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail 'Ubuntu only'
id sirusweb >/dev/null || fail 'Hestia website user missing'
db=/home/sirusweb/web/sirussofts.com/private/sirus-cms/data/cms.sqlite3
nodes=/home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes
[[ -f "$db" && ! -L "$db" && -d "$nodes" && ! -L "$nodes" ]] ||
    fail 'Existing CMS database/nodes directory not available'
python3 - "$db" <<'PY'
import sqlite3,sys
with sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True) as db:
    for table in ("cms_source_release_plans","cms_source_deploy_requests"):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          (table,)).fetchone():
            raise SystemExit("FAIL: CMS Release Planner schema is not deployed: "+table)
    pending=db.execute("""SELECT count(*) FROM cms_source_deploy_requests
                          WHERE host='ubuntu-primary' AND state='queued'""").fetchone()[0]
    if pending:
        raise SystemExit("FAIL: existing queued deployments require review before enabling")
print("PASS CMS deployment queue schema and no unattended pending operations")
PY
target=/usr/local/libexec/sirus-release
unit=/etc/systemd/system/sirus-release-controller.service
[[ ! -L "$target" && ! -L "$unit" ]] || fail 'Unsafe target or service symlink'
if [[ -f "$unit" ]] && ! cmp -s "$unit" "$here/sirus-release-controller.service"; then
    fail 'Installed systemd unit differs from reviewed template'
fi
for script in source_executor.py ubuntu_controller.py db_queue.py; do
    if [[ -e "$target/$script" || -L "$target/$script" ]]; then
        [[ -f "$target/$script" && ! -L "$target/$script" &&
           "$(stat -c %U:%G "$target/$script")" == root:root ]] ||
            fail "Existing executable ownership/link differs: $script"
    fi
done
if [[ "$mode" == --plan ]]; then
    echo 'Reviewed Ubuntu root controller will only run fixed-source releases.'
    echo "Controller installation: $target"
    echo "Systemd unit: $unit"
    echo 'Backups: /var/lib/sirussofts/source-releases (0700 root)'
    echo 'No PHP, Node, Hestia, nginx, package manager or SSH settings will change.'
    echo 'No staged release will be applied by the installer itself.'
    echo 'CMS SQLite queue writes run only as the existing unprivileged sirusweb account.'
    exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive terminal required'
read -r -p 'Type ENABLE CONTROLLED SOURCE DEPLOYS: ' approved
[[ "$approved" == 'ENABLE CONTROLLED SOURCE DEPLOYS' ]] ||
    fail 'Operator did not approve installation'
install -d -m 0755 -o root -g root /usr/local/libexec
install -d -m 0755 -o root -g root "$target"
install -d -m 0700 -o root -g root /var/lib/sirussofts
install -d -m 0700 -o root -g root /var/lib/sirussofts/source-releases
for script in source_executor.py ubuntu_controller.py; do
    file="$target/$script"
    if [[ -f "$file" ]]; then
        cp -p "$file" "$file.before-controller-$(date +%s)"
    fi
    # Only the DB helper runs as sirusweb. Root owns every deployed script.
    mode=0700
    [[ "$script" == db_queue.py ]] && mode=0755
    install -m "$mode" -o root -g root "$here/$script" "$file"
done
if [[ ! -f "$unit" ]]; then
    install -m 0644 -o root -g root "$here/sirus-release-controller.service" "$unit"
fi
systemctl daemon-reload
systemctl enable --now sirus-release-controller.service
systemctl restart sirus-release-controller.service
systemctl is-active --quiet sirus-release-controller.service ||
    fail 'Controller failed startup; see journalctl -u sirus-release-controller'
echo 'PASS restricted release controller started (no automatic site update triggered).'
