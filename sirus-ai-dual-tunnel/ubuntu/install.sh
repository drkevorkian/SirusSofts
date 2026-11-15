#!/usr/bin/env bash
# Ubuntu 24.04/Hestia ONLY. Requires manual review and typed confirmation.
# Adds dedicated in/out SSH identities and two narrow Nginx includes; never
# replaces an existing Gentoo dashboard, tunnel, domain, or public website.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="${1:---check}"
[[ $# -eq 1 && ( "$mode" == --check || "$mode" == --plan || "$mode" == --install ) ]] || fail 'Usage: bash ubuntu/install.sh --check|--plan|--install'
base="$(cd "$(dirname "$0")/.." && pwd)"
for f in ubuntu/gateway.py ubuntu/web/index.html ubuntu/web/app.js ubuntu/web/styles.css ubuntu/nginx.conf_sirusai ubuntu/nginx.ssl.conf_sirusai ubuntu/sirus-ai-gateway.service; do
  [[ -f "$base/$f" ]] || fail "Bundle file missing: $f"
done
python3 -m py_compile "$base/ubuntu/gateway.py"
[[ "$mode" == --check ]] && { echo 'PASS Ubuntu gateway components and Python syntax (no system changes)'; exit 0; }
[[ "$(id -u)" == 0 ]] || fail 'Ubuntu root required'
. /etc/os-release
[[ "$ID" == ubuntu ]] || fail 'Ubuntu required'
[[ -x /usr/sbin/sshd && -f /etc/ssh/sshd_config ]] || fail 'OpenSSH server missing'
[[ -x /usr/local/hestia/bin/v-list-web-domain ]] || fail 'HestiaCP required'
/usr/local/hestia/bin/v-list-web-domain sirusweb sirussofts.com >/dev/null || fail 'Existing Hestia domain sirussofts.com missing'
command -v htpasswd >/dev/null || fail 'Install apache2-utils (htpasswd) before proceeding'
nginx -t || fail 'Existing Nginx config invalid'
/usr/sbin/sshd -t || fail 'Existing SSH daemon config invalid'
ssl=/home/sirusweb/conf/web/sirussofts.com/nginx.ssl.conf_sirusai
plain=/home/sirusweb/conf/web/sirussofts.com/nginx.conf_sirusai
if [[ "$mode" == --plan ]]; then
  echo 'INPUT: restricted key-only SSH account sirus-ai-in, -R 127.0.0.1:18485 -> Gentoo 18082.'
  echo 'OUTPUT: separate restricted key-only SSH account sirus-ai-out, -L Gentoo 18084 -> Ubuntu 127.0.0.1:18483.'
  echo 'Create private Ubuntu output collector (loopback 18483), persistent event database and protected frontend.'
  echo "Add ONLY Hestia includes $ssl and $plain for /Gentoo/AI/; do not alter other routes."
  echo 'HTTP redirects to HTTPS; HTTPS Basic auth covers the entire AI app; internal/push is denied by Nginx.'
  echo 'No existing site files, certificate, firewall, original reverse tunnel, or Gentoo AI model are modified.'
  echo 'Provide /root/input_ed25519.pub, /root/output_ed25519.pub and /root/secret transferred securely from Gentoo.'
  echo 'This does not deploy by itself; use --install after review.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive SSH terminal required for credential setup and confirmation'
for f in /root/input_ed25519.pub /root/output_ed25519.pub /root/secret; do
  [[ -f "$f" && ! -L "$f" ]] || fail "Required transferred Gentoo file missing: $f"
done
for f in /etc/sirus-ai-dual /opt/sirus-ai-dual /etc/systemd/system/sirus-ai-gateway.service "$ssl" "$plain"; do
  [[ ! -e "$f" && ! -L "$f" ]] || fail "Existing installation or route would be overwritten: $f"
done
[[ ! -e /var/lib/sirus-ai-dual ]] || fail 'Existing AI data directory; refusing to overwrite'
[[ ! -e /var/lib/sirus-ai-in && ! -e /var/lib/sirus-ai-out ]] || fail 'AI SSH service accounts may already exist; review'
[[ ! -e /etc/ssh/sshd_config.d/90-sirus-ai.conf ]] || fail 'Unexpected prior SSH AI include'
for who in sirus-ai-in sirus-ai-out; do ! id "$who" >/dev/null 2>&1 || fail "User $who already exists"; done

read -r in_type in_key _ < /root/input_ed25519.pub
read -r out_type out_key _ < /root/output_ed25519.pub
[[ "$in_type" == ssh-ed25519 && "$out_type" == ssh-ed25519 ]] || fail 'Both SSH keys must be Ed25519'
[[ "$in_key" =~ ^[A-Za-z0-9+/=]{50,500}$ && "$out_key" =~ ^[A-Za-z0-9+/=]{50,500}$ ]] || fail 'SSH key encoding invalid'
[[ "$in_key" != "$out_key" ]] || fail 'Separate input and output SSH keys required'
ssh-keygen -lf /root/input_ed25519.pub >/dev/null || fail 'Invalid input key'
ssh-keygen -lf /root/output_ed25519.pub >/dev/null || fail 'Invalid output key'
[[ "$(wc -l </root/input_ed25519.pub)" -eq 1 && "$(wc -l </root/output_ed25519.pub)" -eq 1 ]] || fail 'Public key files must contain one key each'
[[ "$(wc -c </root/secret)" -ge 64 && "$(wc -c </root/secret)" -le 128 ]] || fail 'Invalid shared output secret length'
python3 - <<'PY'
import re
s=open('/root/secret').read().strip()
assert re.fullmatch('[0-9a-f]{64}',s),'Secret must be 32 bytes encoded in hex'
PY
read -r -p 'Type ENABLE SEPARATE AI TUNNELS to provision Ubuntu: ' confirm
[[ "$confirm" == 'ENABLE SEPARATE AI TUNNELS' ]] || fail 'Operator declined'

backup="$(mktemp /root/sirus-ai-sshd-backup-XXXXXXXX)"
cp -p /etc/ssh/sshd_config "$backup"
ssh_changed=0
rollback(){
  code=$?
  if (( code != 0 && ssh_changed == 1 )); then
    cp -p -- "$backup" /etc/ssh/sshd_config
    /usr/sbin/sshd -t && systemctl reload ssh || true
    echo "SSH configuration reverted from $backup" >&2
  fi
}
trap rollback EXIT
for who in sirus-ai-in sirus-ai-out; do
  useradd --system --home-dir "/var/lib/$who" --create-home --shell /bin/false "$who"
  passwd -l "$who" >/dev/null
  install -d -m 0700 -o "$who" -g "$who" "/var/lib/$who/.ssh"
done
printf 'restrict,port-forwarding,permitlisten="127.0.0.1:18485" ssh-ed25519 %s sirus-ai-input\n' "$in_key" > /var/lib/sirus-ai-in/.ssh/authorized_keys
printf 'restrict,port-forwarding,permitopen="127.0.0.1:18483" ssh-ed25519 %s sirus-ai-output\n' "$out_key" > /var/lib/sirus-ai-out/.ssh/authorized_keys
for who in sirus-ai-in sirus-ai-out; do
  chown "$who:$who" "/var/lib/$who/.ssh/authorized_keys"
  chmod 0600 "/var/lib/$who/.ssh/authorized_keys"
done
cat >> /etc/ssh/sshd_config <<'SSH'

# SIRUS-AI-IN-OUT — two separate least-privilege keepalive connections
Match User sirus-ai-in
    AllowTcpForwarding remote
    PermitOpen none
    PermitListen 127.0.0.1:18485
    GatewayPorts no
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PubkeyAuthentication yes
    X11Forwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTTY no
    ForceCommand /bin/false
Match User sirus-ai-out
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:18483
    PermitListen none
    GatewayPorts no
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PubkeyAuthentication yes
    X11Forwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTTY no
    ForceCommand /bin/false
SSH
ssh_changed=1
/usr/sbin/sshd -t || fail 'New restricted SSH config invalid'
in_check="$(/usr/sbin/sshd -T -C user=sirus-ai-in,host=localhost,addr=127.0.0.1)"
out_check="$(/usr/sbin/sshd -T -C user=sirus-ai-out,host=localhost,addr=127.0.0.1)"
grep -qx 'allowtcpforwarding remote' <<< "$in_check" || fail 'Input account must allow only remote forwarding'
grep -qx 'allowtcpforwarding local' <<< "$out_check" || fail 'Output account must allow only local forwarding'
grep -qx 'permitopen 127.0.0.1:18483' <<< "$out_check" || fail 'Output account permitopen restriction missing'
grep -qx 'gatewayports no' <<< "$in_check" || fail 'Input account might open public ports'
grep -qx 'passwordauthentication no' <<< "$out_check" || fail 'Output account password auth enabled'
original="$(/usr/sbin/sshd -T -C user=sirus-tunnel,host=localhost,addr=127.0.0.1)"
grep -qx 'allowtcpforwarding remote' <<< "$original" || fail 'Original tunnel SSH rules were affected; reverting'
systemctl reload ssh || fail 'SSH reload failed'
ssh_changed=0

useradd --system --home-dir /var/lib/sirus-ai-dual --shell /usr/sbin/nologin --no-create-home sirusaiout
install -d -m 0711 -o root -g root /etc/sirus-ai-dual
install -m 0600 -o sirusaiout -g sirusaiout /root/secret /etc/sirus-ai-dual/secret
install -d -m 0755 /opt/sirus-ai-dual /opt/sirus-ai-dual/ubuntu /opt/sirus-ai-dual/ubuntu/web
install -m 0644 "$base/ubuntu/gateway.py" /opt/sirus-ai-dual/ubuntu/gateway.py
for f in index.html app.js styles.css; do
  install -m 0644 "$base/ubuntu/web/$f" "/opt/sirus-ai-dual/ubuntu/web/$f"
done
install -d -m 0700 -o sirusaiout -g sirusaiout /var/lib/sirus-ai-dual
install -m 0644 "$base/ubuntu/sirus-ai-gateway.service" /etc/systemd/system/sirus-ai-gateway.service
htpasswd -c -B /etc/sirus-ai-dual/users.htpasswd sirusadmin
chown root:www-data /etc/sirus-ai-dual/users.htpasswd
chmod 0640 /etc/sirus-ai-dual/users.htpasswd
systemctl daemon-reload
systemctl enable --now sirus-ai-gateway
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18483/healthz | grep -q '"status": "ok"' || fail 'Ubuntu AI output receiver failed health check'
install -m 0644 -o root -g root "$base/ubuntu/nginx.ssl.conf_sirusai" "$ssl"
install -m 0644 -o root -g root "$base/ubuntu/nginx.conf_sirusai" "$plain"
if ! nginx -t; then
  rm -f "$ssl" "$plain"
  fail 'Nginx rejected AI route; route files rolled back'
fi
nginx_dump="$(nginx -T 2>&1)"
grep -Fq "# configuration file $ssl:" <<< "$nginx_dump" || fail 'Hestia HTTPS vhost did not include the AI route'
grep -Fq "# configuration file $plain:" <<< "$nginx_dump" || fail 'Hestia HTTP vhost did not include the AI redirect'
if ! systemctl reload nginx; then
  rm -f "$ssl" "$plain"
  nginx -t && systemctl reload nginx || true
  fail 'Nginx reload failed; AI route removed'
fi
echo 'PASS Ubuntu AI gateway provisioned. Original dashboard and tunnel untouched.'
echo 'Now activate input and output SSH tunnels on Gentoo using: sudo bash gentoo/install.sh --activate'
echo 'Then test https://sirussofts.com/Gentoo/AI/ (Basic auth username sirusadmin).'
