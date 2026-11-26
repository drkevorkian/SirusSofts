#!/usr/bin/env bash
# Replacement HOME Ubuntu compute node, NOT the public Hestia VPS.
set -Eeuo pipefail
umask 077
fail(){ echo "FAIL: $*" >&2; exit 1; }
repo="$(cd "$(dirname "$0")/.." && pwd)"
mode="${1:---plan}"; if [[ $# -gt 0 ]]; then shift; fi
private=/root/sirus-compute-private
bundle=/root/sirus-compute-enrollment
node=/etc/sirus-gentoo-node
dual=/etc/sirus-ai-dual
store=/srv/sirussofts/storage
units=/etc/systemd/system
host=74.208.40.187
usage(){ echo 'Usage: install.sh --check|--plan|--prepare|--install LLAMA_SERVER MODEL RX590_INDEX R9390_INDEX|--activate|--enable-amd2|--pdf|--status'; }
if [[ "$mode" == --check || "$mode" == --plan ]]; then
  bash -n "$0"
  python3 - "$repo" <<'PY'
import ast,pathlib,sys
root=pathlib.Path(sys.argv[1])
for folder in ('gentoo-node','sirus-ai-dual-tunnel/gentoo','ubuntu-compute'):
    for path in (root/folder).glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
PY
  echo 'PASS Python source syntax; no system changes.'
  echo 'Local Ubuntu systemd replacement: existing CMS node ID gentoo-home; CPU + RX590 + R9390 model services; original dashboard, queue and three pinned SSH tunnels.'
  echo 'PDF storage is separate; no disk formatting, GPU firmware, clock, firewall, public VPS installation or driver changes.'
  echo 'Prepare fresh keys, enroll via existing trusted VPS admin connection, stage, then explicitly activate.'
  exit 0
fi
[[ $(id -u) == 0 ]] || fail 'Run as root'
. /etc/os-release
[[ "$ID" == ubuntu && -d /run/systemd/system ]] || fail 'Requires Ubuntu with systemd'
[[ ! -e /usr/local/hestia/bin/v-list-web-domain ]] || fail 'This is the public Hestia VPS! Use only the HOME compute host.'
for cmd in python3 ssh ssh-keygen systemctl runuser curl install git; do command -v "$cmd" >/dev/null || fail "Missing $cmd"; done
if [[ "$mode" == --prepare ]]; then
  [[ $# == 0 && ! -e "$private" && ! -e "$bundle" ]] || fail 'Enrollment already exists; preserve and inspect it.'
  mkdir -m 0700 "$private" "$bundle"
  for key in web input output; do
    ssh-keygen -q -t ed25519 -N '' -C "sirus-compute-$key" -f "$private/$key"
    install -m 0600 "$private/$key.pub" "$bundle/$key.pub"
  done
  python3 - "$bundle/heartbeat.key" <<'PY'
import os,sys
with open(sys.argv[1],"xb") as f: f.write(os.urandom(32))
PY
  echo "Transfer ONLY $bundle to the existing VPS using verified administrative SSH."
  echo "Never transfer $private. Follow ubuntu-compute/README.md."
  exit 0
fi
service(){
  local name="$1" user="$2" command="$3" extra="$4"
  [[ ! -e "$units/$name.service" && ! -L "$units/$name.service" ]] || fail "Existing unit: $name"
  cat > "$units/$name.service" <<EOF
[Unit]
Description=SirusSofts $name (Ubuntu compute node)
After=network-online.target
Wants=network-online.target
$extra

[Service]
Type=simple
User=$user
ExecStart=$command
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$units/$name.service"
}
if [[ "$mode" == --install ]]; then
  [[ $# == 4 ]] || { usage; exit 1; }
  server="$(realpath "$1")"; model="$(realpath "$2")"; rx="$3"; r9="$4"
  [[ -x "$server" && -f "$model" && "$rx" =~ ^[0-9]+$ && "$r9" =~ ^[0-9]+$ && "$rx" != "$r9" ]] || fail 'Invalid server, model or distinct Vulkan indices'
  [[ -t 0 ]] || fail 'Interactive terminal required'
  for p in "$node" "$dual" /opt/sirus-gentoo-node /opt/sirus-ai-dual /opt/sirus-llama /var/lib/sirus-ai /var/lib/sirus-ai-dual; do
    [[ ! -e "$p" && ! -L "$p" ]] || fail "Existing path: $p; do not overwrite prior installation"
  done
  for name in sirus-gentoo-node sirus-gentoo-dashboard sirus-gentoo-tunnel sirus-ai sirus-ai-amd sirus-ai-amd2 sirus-ai-input sirus-ai-input-tunnel sirus-ai-output-tunnel; do
    [[ ! -e "$units/$name.service" && ! -L "$units/$name.service" ]] || fail "Existing unit: $name"
  done
  for f in "$bundle/heartbeat.key" "$bundle/secret" "$bundle/host_ed25519.pub" "$private/web" "$private/input" "$private/output"; do
    [[ -f "$f" && ! -L "$f" ]] || fail "Missing enrollment file: $f"
  done
  python3 - "$bundle" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])
assert len((p/'heartbeat.key').read_bytes())==32
assert re.fullmatch('[0-9a-f]{64}',(p/'secret').read_text().strip())
assert (p/'host_ed25519.pub').read_text().startswith('ssh-ed25519 ')
if (p/'research-admin.key').exists(): assert len((p/'research-admin.key').read_bytes())==32
PY
  ssh-keygen -lf "$bundle/host_ed25519.pub"
  read -rp 'Type INSTALL HOME COMPUTE to stage systemd services: ' answer
  [[ "$answer" == 'INSTALL HOME COMPUTE' ]] || fail 'Declined'
  for user in sirusnode sirusai; do
    id "$user" >/dev/null 2>&1 || useradd --system --user-group --create-home --home-dir "/var/lib/$user" --shell /usr/sbin/nologin "$user"
    for group in render video; do getent group "$group" >/dev/null && usermod -aG "$group" "$user" || true; done
  done
  install -d -m 0700 -o sirusnode -g sirusnode "$node" /var/lib/sirusnode
  install -d -m 0711 "$dual"
  install -d -m 0700 -o sirusai -g sirusai /var/lib/sirus-ai-dual
  install -d -m 0755 /opt/sirus-gentoo-node /opt/sirus-ai-dual/gentoo /opt/sirus-llama/bin /var/lib/sirus-ai/models
  install -m 0644 "$repo"/gentoo-node/*.py /opt/sirus-gentoo-node/
  install -m 0644 "$repo"/gentoo-node/*.js /opt/sirus-gentoo-node/
  install -m 0644 "$repo"/sirus-ai-dual-tunnel/gentoo/*.py /opt/sirus-ai-dual/gentoo/
  install -m 0755 "$server" /opt/sirus-llama/bin/llama-server
  shopt -s nullglob
  for lib in "$(dirname "$server")"/*.so*; do
    [[ -f "$lib" ]] && install -m 0755 "$lib" "/opt/sirus-llama/bin/$(basename "$lib")"
  done
  shopt -u nullglob
  install -m 0640 -o root -g sirusai "$model" /var/lib/sirus-ai/models/local.gguf
  install -m 0600 -o sirusnode -g sirusnode "$bundle/heartbeat.key" "$node/heartbeat.key"
  install -m 0600 -o sirusnode -g sirusnode "$private/web" "$node/tunnel_ed25519"
  install -m 0600 "$private/input" "$dual/input_ed25519"
  install -m 0600 "$private/output" "$dual/output_ed25519"
  install -m 0600 -o sirusai -g sirusai "$bundle/secret" "$dual/secret"
  if [[ -f "$bundle/research-admin.key" ]]; then
    install -m 0600 -o sirusai -g sirusai "$bundle/research-admin.key" "$dual/research-admin.key"
  fi
  python3 - "$host" "$bundle/host_ed25519.pub" <<'PY'
import json,sys,pathlib
host=sys.argv[1]
node=pathlib.Path('/etc/sirus-gentoo-node')
dual=pathlib.Path('/etc/sirus-ai-dual')
def write(p,d): p.write_text(json.dumps(d,indent=2)+'\n')
write(node/'config.json',dict(node_id='gentoo-home',coordinator_url='https://sirussofts.com/node-heartbeat.php',work_url='https://sirussofts.com/node-work.php',key_file=str(node/'heartbeat.key'),period_seconds=60))
key=pathlib.Path(sys.argv[2]).read_text().split()
assert len(key)>=2 and key[0]=='ssh-ed25519'
(node/'tunnel_known_hosts').write_text(host+' '+' '.join(key[:2])+'\n')
write(node/'tunnel.json',dict(host=host,ssh_port=22,user='sirus-tunnel',identity=str(node/'tunnel_ed25519'),known_hosts=str(node/'tunnel_known_hosts')))
write(dual/'tunnel.json',dict(host=host,ssh_port=22,known_hosts=str(node/'tunnel_known_hosts'),input_identity=str(dual/'input_ed25519'),output_identity=str(dual/'output_ed25519')))
PY
  chown sirusnode:sirusnode "$node/config.json" "$node/tunnel.json" "$node/tunnel_known_hosts"
  python3 - "$node/dashboard.auth" <<'PY'
import getpass,sys
sys.path.insert(0,'/opt/sirus-gentoo-node')
import dashboard
a=getpass.getpass('Dashboard password (14+ characters): ')
b=getpass.getpass('Repeat dashboard password: ')
assert a==b,'Passwords differ'
with open(sys.argv[1],'x') as f: f.write(dashboard.make_hash(a)+'\n')
PY
  chown sirusnode:sirusnode "$node/dashboard.auth"
  runuser -u sirusnode -- python3 /opt/sirus-gentoo-node/tunnel.py --validate
  python3 /opt/sirus-ai-dual/gentoo/tunnel.py --channel input --check
  python3 /opt/sirus-ai-dual/gentoo/tunnel.py --channel output --check
  runuser -u sirusai -- env LD_LIBRARY_PATH=/opt/sirus-llama/bin /opt/sirus-llama/bin/llama-server --version
  for idx in "$rx" "$r9"; do
    runuser -u sirusai -- env LD_LIBRARY_PATH=/opt/sirus-llama/bin GGML_VK_VISIBLE_DEVICES="$idx" /opt/sirus-llama/bin/llama-server --list-devices
  done
  common='/opt/sirus-llama/bin/llama-server -m /var/lib/sirus-ai/models/local.gguf --alias sirussofts-coder --host 127.0.0.1 -c 4096'
  service sirus-ai sirusai "/usr/bin/env LD_LIBRARY_PATH=/opt/sirus-llama/bin $common --port 18482 -ngl 0" ''
  service sirus-ai-amd sirusai "/usr/bin/env LD_LIBRARY_PATH=/opt/sirus-llama/bin GGML_VK_VISIBLE_DEVICES=$rx $common --port 18487 -ngl 99" ''
  service sirus-ai-amd2 sirusai "/usr/bin/env LD_LIBRARY_PATH=/opt/sirus-llama/bin GGML_VK_VISIBLE_DEVICES=$r9 $common --port 18488 -ngl 99" ''
  service sirus-gentoo-node sirusnode '/usr/bin/python3 /opt/sirus-gentoo-node/client.py --config /etc/sirus-gentoo-node/config.json' ''
  service sirus-gentoo-dashboard sirusnode '/usr/bin/python3 /opt/sirus-gentoo-node/dashboard.py' ''
  service sirus-gentoo-tunnel sirusnode '/usr/bin/python3 /opt/sirus-gentoo-node/tunnel.py' 'After=sirus-gentoo-dashboard.service'
  service sirus-ai-input sirusai '/usr/bin/python3 /opt/sirus-ai-dual/gentoo/input_server.py --gpu-percent 90 --workers 2' 'After=sirus-ai.service sirus-ai-amd.service'
  service sirus-ai-input-tunnel root '/usr/bin/python3 /opt/sirus-ai-dual/gentoo/tunnel.py --channel input' 'After=sirus-ai-input.service'
  service sirus-ai-output-tunnel root '/usr/bin/python3 /opt/sirus-ai-dual/gentoo/tunnel.py --channel output' ''
  systemctl daemon-reload
  echo 'Staged only. Verify device names above (RX590 first, R9390 second); run --activate after restoring private databases if available.'
  echo 'R9 390 endpoint staged but not started; use --enable-amd2 after thermal validation (continuous research auto target).'
  exit 0
fi
if [[ "$mode" == --activate ]]; then
  [[ $# == 0 && -f "$units/sirus-ai-input.service" ]] || fail 'Stage first'
  runuser -u sirusnode -- python3 /opt/sirus-gentoo-node/client.py --once
  systemctl start sirus-gentoo-dashboard sirus-ai sirus-ai-amd
  ready(){
    local port="$1"
    for _ in $(seq 1 120); do
      if curl -fsS --max-time 2 "http://127.0.0.1:$port/health" >/dev/null; then return 0; fi
      sleep 2
    done
    return 1
  }
  ready 18482 && ready 18487 || fail 'Model server failed health; inspect journalctl; nothing enabled'
  systemctl start sirus-ai-input sirus-gentoo-node
  curl -fsS --retry 10 --retry-connrefused --max-time 3 http://127.0.0.1:18082/api/in/health | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["local_only"] and d["workers"]["cpu"] and d["workers"]["amd"]; print(d)'
  systemctl start sirus-gentoo-tunnel sirus-ai-input-tunnel sirus-ai-output-tunnel
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 http://127.0.0.1:18084/api/out/health | grep -q '"status": *"ok"'; then
      systemctl enable sirus-gentoo-node sirus-gentoo-dashboard sirus-gentoo-tunnel sirus-ai sirus-ai-amd sirus-ai-input sirus-ai-input-tunnel sirus-ai-output-tunnel
      echo 'Local CPU/AMD and output transport healthy. Verify public dashboard and actual chat from VPS/browser before calling migration complete.'
      exit 0
    fi
    sleep 2
  done
  fail 'VPS output health unavailable; inspect tunnels; not enabled automatically'
fi
if [[ "$mode" == --enable-amd2 ]]; then
  [[ $# == 0 && -f "$units/sirus-ai-amd2.service" ]] || fail 'Stage core first'
  [[ -t 0 ]] || fail 'Interactive terminal required'
  read -rp 'After successful sustained thermal testing, type ENABLE R9 390 RESEARCH: ' answer
  [[ "$answer" == 'ENABLE R9 390 RESEARCH' ]] || fail 'Declined'
  systemctl start sirus-ai-amd2
  healthy=0
  for _ in $(seq 1 120); do
    if curl -fsS --max-time 2 http://127.0.0.1:18488/v1/models | python3 -c 'import json,sys; assert any(x.get("id")=="sirussofts-coder" for x in json.load(sys.stdin)["data"])' 2>/dev/null; then
      healthy=1; break
    fi
    sleep 2
  done
  if [[ "$healthy" != 1 ]]; then
    systemctl stop sirus-ai-amd2
    fail 'Second AMD model did not become ready'
  fi
  install -m 0644 -o root -g root /dev/null "$dual/amd2-research.enabled"
  # Stop/restart only the local input queue, retaining durable SQLite.
  if ! systemctl restart sirus-ai-input; then
    rm -f "$dual/amd2-research.enabled"
    systemctl stop sirus-ai-amd2
    systemctl restart sirus-ai-input || true
    fail 'Router restart failed; second AMD opt-in removed'
  fi
  systemctl enable sirus-ai-amd2
  echo 'R9 390 enabled as amd2, research-only. Select Auto with concurrency 3 in CMS; explicit AMD chat stays on RX 590.'
  exit 0
fi
if [[ "$mode" == --pdf ]]; then
  [[ $# == 0 && -f "$units/sirus-ai-input.service" ]] || fail 'Stage core first'
  mountpoint -q "$store" && [[ "$(findmnt -M "$store" -n -o FSTYPE)" == ext4 ]] || fail 'Mount reviewed dedicated ext4 PDF SSD at /srv/sirussofts/storage; no disk writes to root fallback'
  for p in manuals manuals/inbox manuals/objects databases rag; do
    [[ ! -L "$store/$p" ]] || fail "Unsafe linked path $p"
  done
  for c in setfacl pdfinfo pdftotext pdftoppm; do command -v "$c" >/dev/null || fail "Missing $c"; done
  [[ ! -e /opt/sirus-pdf && ! -e "$units/sirus-pdf-ingest.service" ]] || fail 'Existing PDF install'
  id siruspdf >/dev/null 2>&1 || useradd --system --user-group --create-home --home-dir /var/lib/siruspdf --shell /usr/sbin/nologin siruspdf
  install -d -m 0755 /opt/sirus-pdf
  install -m 0644 "$repo/gentoo-node/pdf_ingest.py" "$repo/gentoo-node/pdf_wal_guard.py" /opt/sirus-pdf/
  for p in manuals/inbox manuals/objects databases rag; do mkdir -p "$store/$p"; done
  setfacl -m u:siruspdf:--x,u:sirusnode:--x,u:sirusai:--x "$store" "$store/manuals"
  setfacl -R -m u:siruspdf:rwX "$store/manuals/inbox" "$store/manuals/objects" "$store/databases"
  setfacl -R -m u:sirusnode:rX "$store/manuals/objects"
  find "$store/manuals/objects" -type d -exec setfacl -d -m u:sirusnode:rX,u:siruspdf:rwx {} +
  find "$store/manuals/inbox" -type d -exec setfacl -d -m u:siruspdf:rwx {} +
  setfacl -m u:sirusnode:rx,u:sirusai:rx "$store/databases"
  setfacl -d -m u:sirusai:r--,u:sirusnode:r--,u:siruspdf:rwx "$store/databases"
  for f in "$store/databases/pdf_ingest.sqlite3"{,-wal,-shm}; do
    [[ ! -e "$f" ]] || setfacl -m u:sirusai:r--,u:sirusnode:r-- "$f"
  done
  setfacl -R -m u:sirusai:rwX "$store/rag"
  service sirus-pdf-ingest siruspdf '/usr/bin/python3 /opt/sirus-pdf/pdf_ingest.py --serve' 'RequiresMountsFor=/srv/sirussofts/storage
ConditionPathIsMountPoint=/srv/sirussofts/storage'
  systemctl daemon-reload
  echo 'PDF worker staged, not started. Review preserved database and pause/resume settings before systemctl start sirus-pdf-ingest.'
  exit 0
fi
if [[ "$mode" == --status ]]; then
  systemctl --no-pager status 'sirus-ai*' 'sirus-gentoo*' 'sirus-pdf*' || true
  curl -fsS --max-time 3 http://127.0.0.1:18082/api/in/health || true
  exit 0
fi
usage; exit 1
