#!/usr/bin/env bash
# Gentoo only. Adds services without changing sirus-ai, RAID, or the existing Gentoo tunnel.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="\${1:---check}"
[[ $# -eq 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install || "$mode" == --activate ) ]] || fail 'Usage: bash gentoo/install.sh --check|--plan|--install|--activate'
base="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$base/gentoo/input_server.py" && -f "$base/gentoo/tunnel.py" ]] || fail 'Missing bundle files'
python3 -m py_compile "$base/gentoo/input_server.py" "$base/gentoo/tunnel.py"
[[ "$mode" == --check ]] && { echo 'PASS Gentoo AI components compile (no system changes)'; exit 0; }
[[ "$(id -u)" == 0 ]] || fail 'Gentoo root required'
[[ -f /etc/gentoo-release ]] || fail 'Not a Gentoo host'
[[ -f /etc/sirus-gentoo-node/tunnel.json ]] || fail 'Existing Gentoo tunnel configuration not found'
[[ -f /etc/init.d/sirus-ai ]] || fail 'Existing working CPU sirus-ai service missing'
command -v ssh-keygen >/dev/null || fail 'OpenSSH client required'
command -v rc-service >/dev/null || fail 'OpenRC required'
command -v curl >/dev/null || fail 'curl required'
if [[ "$mode" == --plan ]]; then
  echo 'Keep original CPU service on Gentoo 127.0.0.1:18482 unchanged.'
  echo 'Create input API at Gentoo 127.0.0.1:18082; output forward on Gentoo 127.0.0.1:18084.'
  echo 'Two new independently supervised SSH connections to Ubuntu, both using existing PINNED SSH host key.'
  echo 'Input:  -R Ubuntu:127.0.0.1:18482 -> Gentoo:127.0.0.1:18082'
  echo 'Output: -L Gentoo:127.0.0.1:18084 -> Ubuntu:127.0.0.1:18483'
  echo 'No existing tunnel, CPU service, GPU driver, RAID or firewall is changed.'
  echo 'New private keys and output shared secret will be created; tunnel activation is a separate step.'
  exit 0
fi
app=/opt/sirus-ai-dual
conf=/etc/sirus-ai-dual
storage=/var/lib/sirus-ai-dual
if [[ "$mode" == --install ]]; then
  for file in "$conf/input_ed25519" "$conf/output_ed25519" "$conf/secret" /etc/init.d/sirus-ai-input /etc/init.d/sirus-ai-input-tunnel /etc/init.d/sirus-ai-output-tunnel; do
    [[ ! -e "$file" && ! -L "$file" ]] || fail "Existing installation file would be overwritten: $file"
  done
  [[ ! -e "$app" ]] || fail "Existing app directory $app; review before reinstallation"
  id sirusai >/dev/null || fail 'Existing sirusai user missing'
  install -d -m 0755 "$app" "$app/gentoo"
  install -m 0644 "$base/gentoo/input_server.py" "$app/gentoo/input_server.py"
  install -m 0644 "$base/gentoo/tunnel.py" "$app/gentoo/tunnel.py"
  install -d -o root -g root -m 0711 "$conf"
  install -d -o sirusai -g sirusai -m 0700 "$storage"
  ssh-keygen -q -t ed25519 -N '' -C sirus-ai-in -f "$conf/input_ed25519"
  ssh-keygen -q -t ed25519 -N '' -C sirus-ai-out -f "$conf/output_ed25519"
  python3 - "$conf/secret" <<'PY'
import secrets,sys
with open(sys.argv[1], 'x') as file: file.write(secrets.token_hex(32)+'\n')
PY
  chown sirusai:sirusai "$conf/secret"
  chmod 0600 "$conf/secret" "$conf/input_ed25519" "$conf/output_ed25519"
  python3 - "$conf/tunnel.json" <<'PY'
import json,sys
src=json.load(open('/etc/sirus-gentoo-node/tunnel.json'))
for key in ('host','ssh_port','known_hosts'):
    if key not in src: raise SystemExit('Missing existing tunnel field '+key)
config={'host':src['host'], 'ssh_port':src['ssh_port'], 'known_hosts':src['known_hosts'],
        'input_identity':'/etc/sirus-ai-dual/input_ed25519',
        'output_identity':'/etc/sirus-ai-dual/output_ed25519'}
with open(sys.argv[1],'x') as f: json.dump(config,f,indent=2)
PY
  chmod 0600 "$conf/tunnel.json"
  cat > /etc/init.d/sirus-ai-input <<'RC'
#!/sbin/openrc-run
name="SirusSofts AI durable input queue"
supervisor=supervise-daemon
command="/usr/bin/python3"
command_args="/opt/sirus-ai-dual/gentoo/input_server.py --db /var/lib/sirus-ai-dual/input.sqlite3 --secret /etc/sirus-ai-dual/secret --gpu-percent 90 --workers 2"
command_user="sirusai:sirusai"
respawn_delay=5
respawn_period=30
respawn_max=0
depend() { need net; after sirus-ai; }
RC
  cat > /etc/init.d/sirus-ai-input-tunnel <<'RC'
#!/sbin/openrc-run
name="SirusSofts AI persistent INPUT SSH tunnel"
supervisor=supervise-daemon
command="/usr/bin/python3"
command_args="/opt/sirus-ai-dual/gentoo/tunnel.py --channel input"
respawn_delay=5
respawn_period=30
respawn_max=0
depend() { need net; }
RC
  cat > /etc/init.d/sirus-ai-output-tunnel <<'RC'
#!/sbin/openrc-run
name="SirusSofts AI persistent OUTPUT SSH tunnel"
supervisor=supervise-daemon
command="/usr/bin/python3"
command_args="/opt/sirus-ai-dual/gentoo/tunnel.py --channel output"
respawn_delay=5
respawn_period=30
respawn_max=0
depend() { need net; }
RC
  chmod 0755 /etc/init.d/sirus-ai-{input,input-tunnel,output-tunnel}
  rc-service sirus-ai-input start
  rc-update add sirus-ai-input default
  echo 'PASS Gentoo input queue staged (existing CPU AI is untouched).'
  echo 'NEW public SSH keys (safe to share with Ubuntu administrator):'
  cat "$conf/input_ed25519.pub" "$conf/output_ed25519.pub"
  echo 'Securely copy TWO .pub files and /etc/sirus-ai-dual/secret to Ubuntu /root/.'
  echo 'Then run the Ubuntu installer BEFORE activating the new tunnels.'
  exit 0
fi
[[ "$mode" == --activate ]] || fail 'Unexpected mode'
for f in "$conf/tunnel.json" "$conf/secret" /etc/init.d/sirus-ai-input-tunnel /etc/init.d/sirus-ai-output-tunnel; do
  [[ -f "$f" ]] || fail "Required file missing: $f"
done
python3 "$app/gentoo/tunnel.py" --channel input --check
python3 "$app/gentoo/tunnel.py" --channel output --check
rc-service sirus-ai-input-tunnel start || fail 'Input tunnel failed to start'
rc-service sirus-ai-output-tunnel start || fail 'Output tunnel failed to start'
healthy=0
for i in 1 2 3 4 5; do
  if curl --silent --fail --max-time 4 http://127.0.0.1:18084/api/out/health | grep -q '"status": "ok"'; then
    healthy=1; break
  fi
  sleep 2
done
[[ "$healthy" == 1 ]] || fail 'Output tunnel did not reach Ubuntu gateway; inspect service logs before enabling autostart'
rc-update add sirus-ai-input-tunnel default
rc-update add sirus-ai-output-tunnel default
echo 'PASS independent persistent INPUT and OUTPUT tunnels enabled with keepalives.'
