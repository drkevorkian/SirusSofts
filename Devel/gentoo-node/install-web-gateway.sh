#!/usr/bin/env bash
# Install a local dashboard and separately supervised outbound SSH web tunnel.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"; if (( $# )); then mode="$1"; shift; fi
port=22 fingerprint=""
while (( $# )); do
  case "$1" in
    --ssh-port) (( $# >= 2 )) || fail 'Need SSH port'; port="$2"; shift 2;;
    --fingerprint) (( $# >= 2 )) || fail 'Need SHA256 fingerprint'; fingerprint="$2"; shift 2;;
    *) fail "Unknown argument: $1";;
  esac
done
[[ "$mode" == --check || "$mode" == --plan || "$mode" == --install ||
   "$mode" == --pin-host || "$mode" == --enable ]] || fail 'Invalid mode'
here="$(cd "$(dirname "$0")" && pwd)"
if [[ "$mode" == --check ]]; then
  python3 -m py_compile "$here/client.py" "$here/dashboard.py" "$here/tunnel.py"
  python3 "$here/dashboard.py" --check
  python3 "$here/tunnel.py" --check
  echo 'PASS Gentoo gateway syntax and unit self-tests'
  exit 0
fi
[[ "$(id -u)" -eq 0 && -f /etc/gentoo-release ]] || fail 'Requires Gentoo root'
command -v rc-service >/dev/null || fail 'OpenRC missing'
command -v ssh-keygen >/dev/null || fail 'OpenSSH tools missing'
base=/etc/sirus-gentoo-node
app=/opt/sirus-gentoo-node
[[ -f "$base/config.json" && ! -L "$base/config.json" &&
   -f "$base/heartbeat.key" && ! -L "$base/heartbeat.key" ]] ||
  fail 'Existing node configuration/key unavailable; refuse to reinitialize'
id sirusnode >/dev/null || fail 'Existing sirusnode account missing'
[[ "$port" =~ ^[0-9]+$ ]] && (( port>=1 && port<=65535 )) || fail 'Invalid SSH port'
if [[ "$mode" == --plan ]]; then
  echo 'Preserve current Gentoo identity and HMAC key; upgrade worker for task history.'
  echo 'Dashboard is loopback-only 127.0.0.1:18080; future apps use 18081.'
  echo 'Two OpenRC services: authenticated dashboard and reconnecting reverse SSH.'
  echo 'Separate SSH key/verified Ubuntu host fingerprint, no public Gentoo ports.'
  exit 0
fi
if [[ "$mode" == --install ]]; then
  [[ -t 0 && -t 1 ]] || fail 'Interactive console required'
  read -r -p 'Type INSTALL GENTOO GATEWAY: ' answer
  [[ "$answer" == 'INSTALL GENTOO GATEWAY' ]] || fail 'Declined'
  install -d -o root -g root -m 0755 "$app"
  install -d -o sirusnode -g sirusnode -m 0700 /var/lib/sirusnode
  [[ -f "$app/client.py" && ! -L "$app/client.py" ]] || fail 'Existing client.py missing'
  cp -p "$app/client.py" "$app/client.py.pre-gateway-$(date +%s)"
  for p in client.py dashboard.py tunnel.py; do
    [[ -f "$here/$p" && ! -L "$here/$p" ]] || fail "Source missing: $p"
    install -m 0755 -o root -g root "$here/$p" "$app/$p"
  done
  python3 -m py_compile "$app/client.py" "$app/dashboard.py" "$app/tunnel.py"
  for name in sirus-gentoo-dashboard sirus-gentoo-tunnel; do
    dest="/etc/init.d/$name"
    [[ ! -L "$dest" ]] || fail "Unsafe service path: $dest"
    if [[ -f "$dest" ]] && ! cmp -s "$here/openrc/$name" "$dest"; then
      fail "Existing service differs: $name"
    fi
    install -m 0755 -o root -g root "$here/openrc/$name" "$dest"
  done
  if [[ ! -f "$base/dashboard.auth" ]]; then
    read -r -s -p 'Private dashboard password (14+ characters): ' pass; echo
    read -r -s -p 'Repeat private dashboard password: ' repeat; echo
    [[ "$pass" == "$repeat" ]] || fail 'Passwords do not match'
    (( $(printf '%s' "$pass" | wc -c) >= 14 )) || fail 'Password too short'
    umask 077
    printf '%s' "$pass" | python3 -c '
import sys
sys.path.insert(0,"/opt/sirus-gentoo-node")
import dashboard
print(dashboard.make_hash(sys.stdin.read()))
' > "$base/dashboard.auth"
    unset pass repeat
    chown sirusnode:sirusnode "$base/dashboard.auth"
    chmod 0600 "$base/dashboard.auth"
  fi
  if [[ ! -e "$base/tunnel_ed25519" ]]; then
    [[ ! -e "$base/tunnel_ed25519.pub" ]] || fail 'Unexpected incomplete SSH key pair'
    runuser -u sirusnode -- ssh-keygen -q -t ed25519 -N '' \
      -C sirus-gentoo-web -f "$base/tunnel_ed25519"
  fi
  if [[ ! -e "$base/tunnel.json" ]]; then
    python3 - "$here/tunnel.example.json" "$base/tunnel.json" "$port" <<'PY'
import json,sys
with open(sys.argv[1],encoding="utf8") as source:
    data=json.load(source)
data["ssh_port"]=int(sys.argv[3])
with open(sys.argv[2],"x",encoding="utf8") as target:
    json.dump(data,target,indent=2)
    target.write("\n")
PY
    chown sirusnode:sirusnode "$base/tunnel.json"
    chmod 0600 "$base/tunnel.json"
  fi
  if rc-service sirus-gentoo-node status >/dev/null 2>&1; then
    rc-service sirus-gentoo-node restart ||
      fail 'Worker restart failed; restore its pre-gateway backup'
  fi
  if ! rc-service sirus-gentoo-dashboard status >/dev/null 2>&1; then
    rc-service sirus-gentoo-dashboard start || fail 'Dashboard start failed'
  fi
  rc-update add sirus-gentoo-dashboard default
  echo 'PASS password-protected Gentoo dashboard listening only on 127.0.0.1:18080'
  echo 'Transfer this PUBLIC key (never the private key) to Ubuntu:'
  cat "$base/tunnel_ed25519.pub"
  echo 'Get Ubuntu trusted fingerprint: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'
  echo 'After Ubuntu setup run --pin-host --fingerprint SHA256:... then --enable.'
  exit 0
fi
if [[ "$mode" == --pin-host ]]; then
  [[ "$fingerprint" =~ ^SHA256:[A-Za-z0-9+/]{20,70}$ ]] ||
    fail 'Pass trusted Ubuntu SSH fingerprint as --fingerprint SHA256:...'
  command -v ssh-keyscan >/dev/null || fail 'ssh-keyscan missing'
  temp="$(mktemp)"
  trap 'rm -f "$temp"' EXIT
  ssh-keyscan -T 6 -t ed25519 -p "$port" 74.208.40.187 2>/dev/null > "$temp" ||
    fail 'Unable to retrieve Ubuntu Ed25519 host key'
  [[ -s "$temp" ]] || fail 'No Ubuntu Ed25519 SSH key returned'
  actual="$(ssh-keygen -lf "$temp" | awk 'NR==1{print $2}')"
  [[ "$actual" == "$fingerprint" ]] || fail 'SSH HOST KEY MISMATCH - nothing installed'
  [[ ! -L "$base/tunnel_known_hosts" ]] || fail 'Unsafe known_hosts path'
  install -m 0600 -o sirusnode -g sirusnode "$temp" "$base/tunnel_known_hosts"
  echo 'PASS Ubuntu SSH host authenticity verified and pinned.'
  exit 0
fi
if [[ "$mode" == --enable ]]; then
  runuser -u sirusnode -- python3 "$app/tunnel.py" --validate
  rc-service sirus-gentoo-dashboard status >/dev/null 2>&1 ||
    rc-service sirus-gentoo-dashboard start
  rc-service sirus-gentoo-tunnel start
  rc-update add sirus-gentoo-tunnel default
  echo 'PASS outbound SSH tunnel supervisor enabled; CPU-heavy sites may bind 127.0.0.1:18081.'
  echo 'Verify on Ubuntu: curl --max-time 5 http://127.0.0.1:18480/healthz'
fi
