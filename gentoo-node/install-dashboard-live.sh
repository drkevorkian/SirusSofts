#!/usr/bin/env bash
# Upgrade only the existing Gentoo dashboard, preserving node + SSH tunnel.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: install-dashboard-live.sh --check|--plan|--install'
here="$(cd "$(dirname "$0")" && pwd)"
for file in dashboard.py dashboard_live.py dashboard_live.js; do
  [[ -f "$here/$file" && ! -L "$here/$file" ]] || fail "Source missing: $file"
done
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$here" <<'PY'
import ast,pathlib,sys
for name in ("dashboard.py","dashboard_live.py"):
    ast.parse((pathlib.Path(sys.argv[1])/name).read_text(encoding="utf8"))
print("PASS dashboard and live socket Python syntax")
PY
if command -v node >/dev/null 2>&1; then
  node --check "$here/dashboard_live.js" || fail 'Dashboard JavaScript failed syntax check'
fi
if [[ "$mode" == --check ]]; then exit 0; fi
[[ "$(id -u)" == 0 && -f /etc/gentoo-release ]] ||
  fail 'Gentoo root required for dashboard deployment'
id sirusnode >/dev/null || fail 'Existing Gentoo node account missing'
command -v rc-service >/dev/null || fail 'OpenRC required'
app=/opt/sirus-gentoo-node
[[ -d "$app" && ! -L "$app" ]] || fail 'Existing dashboard directory missing'
[[ -f "$app/dashboard.py" && ! -L "$app/dashboard.py" ]] ||
  fail 'Existing dashboard unavailable'
[[ -f "$app/client.py" && ! -L "$app/client.py" ]] ||
  fail 'Existing Gentoo node client unavailable'
[[ -f /etc/sirus-gentoo-node/config.json && ! -L /etc/sirus-gentoo-node/config.json &&
   -f /etc/sirus-gentoo-node/dashboard.auth &&
   ! -L /etc/sirus-gentoo-node/dashboard.auth ]] ||
  fail 'Existing dashboard configuration or authentication unavailable'
for name in dashboard.py dashboard_live.py dashboard_live.js; do
  [[ ! -L "$app/$name" ]] || fail "Refusing linked installed asset: $name"
done
[[ "$(df -Pk "$app" | awk 'NR==2 {print $4}')" -ge 102400 ]] ||
  fail 'Less than 100 MiB free on Gentoo dashboard filesystem'
if [[ "$mode" == --plan ]]; then
  echo "Dashboard files: $app/dashboard.py, dashboard_live.py, dashboard_live.js"
  echo 'Back up existing dashboard source before replacing any executable.'
  echo 'Restart ONLY sirus-gentoo-dashboard; preserve sirus-gentoo-node/tunnel.'
  echo 'No new public ports, SSH keys, credentials, or firewall rules.'
  echo 'Ubuntu must separately proxy /Gentoo/live with WebSocket Upgrade.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive Gentoo console required'
read -r -p 'Type UPDATE GENTOO DASHBOARD: ' answer
[[ "$answer" == 'UPDATE GENTOO DASHBOARD' ]] || fail 'Operator declined'
recovery="$app/recovery/dashboard-live-$(date +%Y%m%d-%H%M%S)-$$"
install -d -m 0700 -o root -g root "$app/recovery"
install -d -m 0700 -o root -g root "$recovery"
for name in dashboard.py dashboard_live.py dashboard_live.js; do
  if [[ -f "$app/$name" ]]; then
    cp -p -- "$app/$name" "$recovery/$name"
  fi
done
restore(){
  local status=$?
  if (( status != 0 )); then
    echo 'Dashboard upgrade failed; restoring only backed-up dashboard files.' >&2
    for name in dashboard.py dashboard_live.py dashboard_live.js; do
      if [[ -f "$recovery/$name" ]]; then
        cp -p -- "$recovery/$name" "$app/$name"
      else
        rm -f -- "$app/$name"
      fi
    done
    rc-service sirus-gentoo-dashboard restart >&2 || true
    echo "Recovery directory retained at: $recovery" >&2
  fi
}
trap restore EXIT
for name in dashboard.py dashboard_live.py dashboard_live.js; do
  mode=0644
  [[ "$name" == dashboard.py ]] && mode=0755
  install -o root -g root -m "$mode" "$here/$name" "$app/$name"
done
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$app" <<'PY'
import ast,pathlib,sys
for name in ("dashboard.py","dashboard_live.py"):
    ast.parse((pathlib.Path(sys.argv[1])/name).read_text(encoding="utf8"))
PY
rc-service sirus-gentoo-dashboard restart ||
  fail 'Dashboard service restart failed'
health="$(curl --noproxy '*' --silent --show-error --max-time 5   --output /dev/null --write-out '%{http_code}' http://127.0.0.1:18080/healthz || true)"
[[ "$health" == 200 ]] || fail "Dashboard /healthz expected HTTP 200, got $health"
private="$(curl --noproxy '*' --silent --max-time 5   --output /dev/null --write-out '%{http_code}' http://127.0.0.1:18080/ || true)"
[[ "$private" == 401 ]] || fail "Dashboard without credentials expected HTTP 401, got $private"
trap - EXIT
echo 'PASS Gentoo live dashboard upgraded; only dashboard OpenRC service restarted.'
echo "Private source recovery: $recovery"
echo 'Ubuntu still requires the guarded /Gentoo/live WebSocket reverse-proxy include.'
