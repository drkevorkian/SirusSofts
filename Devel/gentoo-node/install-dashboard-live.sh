#!/usr/bin/env bash
# Upgrade only the existing Gentoo dashboard, preserving node + SSH tunnel.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -le 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] ||
  fail 'Usage: install-dashboard-live.sh --check|--plan|--install'
here="$(cd "$(dirname "$0")" && pwd)"
# All deployment stages use the SAME manifest: checking, backups,
# installation, verification, and rollback must never diverge.
dashboard_assets=(dashboard.py dashboard_live.py dashboard_live.js dashboard_health.py)
for file in "${dashboard_assets[@]}"; do
  [[ -f "$here/$file" && ! -L "$here/$file" ]] || fail "Source missing: $file"
done
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$here" <<'PY'
import ast,pathlib,sys
for name in ("dashboard.py","dashboard_live.py","dashboard_health.py"):
    ast.parse((pathlib.Path(sys.argv[1])/name).read_text(encoding="utf8"))
print("PASS dashboard and live socket Python syntax")
PY
python3 -B "$here/dashboard_health.py" --check
if command -v node >/dev/null 2>&1; then
  node --check "$here/dashboard_live.js" || fail 'Dashboard JavaScript failed syntax check'
fi
if [[ "$mode" == --check ]]; then exit 0; fi
[[ "$(id -u)" == 0 && -f /etc/gentoo-release ]] ||
  fail 'Gentoo root required for dashboard deployment'
id sirusnode >/dev/null || fail 'Existing Gentoo node account missing'
command -v rc-service >/dev/null || fail 'OpenRC required'
command -v runuser >/dev/null || fail 'runuser required to validate service identity'
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
for name in "${dashboard_assets[@]}"; do
  [[ ! -L "$app/$name" ]] || fail "Refusing linked installed asset: $name"
done
[[ "$(df -Pk "$app" | awk 'NR==2 {print $4}')" -ge 102400 ]] ||
  fail 'Less than 100 MiB free on Gentoo dashboard filesystem'
if [[ "$mode" == --plan ]]; then
  printf 'Dashboard files to install and protect:\\n'
  printf '  %s\\n' "${dashboard_assets[@]}"
  echo 'Back up existing dashboard source before replacing any executable.'
  echo 'Restart ONLY sirus-gentoo-dashboard; preserve sirus-gentoo-node/tunnel.'
  echo 'No new public ports, SSH keys, credentials, or firewall rules.'
  echo 'OpenRC supervisor may report success before Python binds; verify for up to 30 seconds.'
  echo 'Ubuntu must separately proxy /Gentoo/live with WebSocket Upgrade.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive Gentoo console required'
read -r -p 'Type UPDATE GENTOO DASHBOARD: ' answer
[[ "$answer" == 'UPDATE GENTOO DASHBOARD' ]] || fail 'Operator declined'
recovery="$app/recovery/dashboard-live-$(date +%Y%m%d-%H%M%S)-$$"
install -d -m 0700 -o root -g root "$app/recovery"
install -d -m 0700 -o root -g root "$recovery"
for name in "${dashboard_assets[@]}"; do
  if [[ -f "$app/$name" ]]; then
    cp -p -- "$app/$name" "$recovery/$name"
  fi
done
restore(){
  local status=$?
  if (( status != 0 )); then
    echo 'Dashboard upgrade failed; restoring only backed-up dashboard files.' >&2
    for name in "${dashboard_assets[@]}"; do
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
for name in "${dashboard_assets[@]}"; do
  mode=0644
  [[ "$name" == dashboard.py ]] && mode=0755
  install -o root -g root -m "$mode" "$here/$name" "$app/$name"
  cmp -s -- "$here/$name" "$app/$name" ||
    fail "Installed dashboard asset differs from reviewed source: $name"
done
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$app" <<'PY'
import ast,pathlib,sys
for name in ("dashboard.py","dashboard_live.py","dashboard_health.py"):
    ast.parse((pathlib.Path(sys.argv[1])/name).read_text(encoding="utf8"))
PY
# Syntax alone cannot catch missing modules or unreadable installed files.
# Confirm the target service identity can import exactly what will be run.
PYTHONDONTWRITEBYTECODE=1 runuser -u sirusnode -- \
  python3 -B "$app/dashboard.py" --check ||
  fail 'New dashboard failed import/self-check as sirusnode'
rc-service sirus-gentoo-dashboard restart ||
  fail 'Dashboard service restart failed'
# supervise-daemon can report [ ok ] before Python has imported its
# modules, loaded its private config and bound the loopback socket.
# Wait for the *new* dashboard (including its live-only routes) to respond.
if ! python3 -B "$here/dashboard_health.py" --port 18080 --wait 30; then
  echo 'Dashboard did not become ready. Checking the OpenRC process and listener:' >&2
  rc-service sirus-gentoo-dashboard status >&2 || true
  if command -v ss >/dev/null 2>&1; then
    ss -ltn '( sport = :18080 )' >&2 || true
  fi
  echo 'Dashboard service troubleshooting (no private credentials printed):' >&2
  echo '  rc-service sirus-gentoo-dashboard status' >&2
  echo '  ps -eo user,pid,stat,args | grep "[d]ashboard.py"' >&2
  fail 'Live dashboard startup verification failed; restoring previous files'
fi
# An HTTP 200 healthz does not establish that Chrome can authenticate over
# WebSocket. Test the running new dashboard's real signed HTTP/1.1 upgrade,
# ready acknowledgment, and live snapshot before declaring success.
if ! PYTHONDONTWRITEBYTECODE=1 runuser -u sirusnode -- \
     python3 -B "$app/dashboard_health.py" --socket-check; then
  fail 'Signed live WebSocket self-test failed; restoring previous dashboard files'
fi
trap - EXIT
echo 'PASS Gentoo live dashboard upgraded; only dashboard OpenRC service restarted.'
echo "Private source recovery: $recovery"
echo 'Ubuntu still requires the guarded /Gentoo/live WebSocket reverse-proxy include.'
