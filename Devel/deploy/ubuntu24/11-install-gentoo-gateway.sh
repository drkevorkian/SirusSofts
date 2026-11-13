#!/usr/bin/env bash
# Ubuntu coordinator: restricted SSH reverse forwards and Hestia custom templates.
# Does not alter the existing root website, create DNS records, or activate certificates.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"
if (( $# )); then mode="$1"; shift; fi
pubkey=""
while (( $# )); do
  case "$1" in
    --pubkey-file) (( $# >= 2 )) || fail '--pubkey-file requires a filename'
      pubkey="$2"; shift 2;;
    *) fail "Unexpected argument: $1";;
  esac
done
[[ "$mode" == --check || "$mode" == --plan || "$mode" == --install ]] ||
  fail "Use --check|--plan|--install [--pubkey-file /root/gentoo-web.pub]"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ "$mode" == --check ]]; then
  for tpl in sirus-gentoo-{dashboard,app}.{tpl,stpl}; do
    [[ -f "$HERE/$tpl" ]] || fail "Missing Hestia template $tpl"
  done
  echo 'PASS Hestia Gentoo gateway scripts and templates present.'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Run as Ubuntu root'
[[ -x /usr/local/hestia/bin/v-list-web-domain ]] || fail 'Requires HestiaCP'
[[ -x /usr/sbin/sshd ]] || fail 'OpenSSH server missing'
[[ -f /etc/ssh/sshd_config && ! -L /etc/ssh/sshd_config ]] || fail 'Unsafe sshd configuration'
[[ -d /usr/local/hestia/data/templates/web/nginx ]] || fail 'Hestia Nginx proxy template directory missing'
[[ -x /usr/local/hestia/bin/v-change-web-domain-proxy-tpl ]] || fail 'Hestia template activation command missing'
if [[ "$mode" == --plan ]]; then
  echo 'Configure dedicated SSH user sirus-tunnel for outbound Gentoo -> Ubuntu SSH only.'
  echo 'Two reverse ports: 127.0.0.1:18480 dashboard and 127.0.0.1:18481 applications.'
  echo 'Install Hestia domain templates; no existing domains, DNS, SSL, or website changed.'
  echo 'The Gentoo private SSH key stays on Gentoo. This installer requires its PUBLIC key.'
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail 'Interactive PuTTY terminal required'
[[ "$pubkey" == /* && -f "$pubkey" && ! -L "$pubkey" ]] ||
  fail 'Provide --pubkey-file /root/gentoo-web.pub'
[[ "$(wc -l < "$pubkey")" -eq 1 ]] || fail 'Public key must be one line'
read -r keytype encoded comment < "$pubkey"
[[ "$keytype" == ssh-ed25519 && "$encoded" =~ ^[A-Za-z0-9+/=]{50,500}$ ]] ||
  fail 'Only a well-formed Ed25519 public key is accepted'
ssh-keygen -lf "$pubkey" >/dev/null || fail 'Invalid SSH public key'
/usr/sbin/sshd -t || fail 'Current SSH server config invalid'
read -r -p 'Type CONFIGURE GENTOO GATEWAY to continue: ' answer
[[ "$answer" == 'CONFIGURE GENTOO GATEWAY' ]] || fail 'Declined'
user=sirus-tunnel
home=/var/lib/sirus-tunnel
config=/etc/ssh/sshd_config
backup="$(mktemp /root/sirus-sshd-backup-XXXXXXXX)"
cp -p "$config" "$backup"
changed=0
restore(){
  rc=$?
  if (( rc != 0 && changed == 1 )); then
    cp -p -- "$backup" "$config"
    /usr/sbin/sshd -t || echo 'Warning: review restored sshd config' >&2
    systemctl reload ssh >/dev/null 2>&1 || true
    echo "Restored SSH config from $backup" >&2
  fi
}
trap restore EXIT
if ! id "$user" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$home" --shell /bin/false "$user"
  passwd -l "$user" >/dev/null
fi
[[ "$(getent passwd "$user" | cut -d: -f6)" == "$home" ]] ||
  fail 'Restricted SSH account has an unexpected home directory'
install -d -m 0700 -o "$user" -g "$user" "$home/.ssh"
printf 'restrict,port-forwarding,permitlisten="127.0.0.1:18480",permitlisten="127.0.0.1:18481" ssh-ed25519 %s sirus-gentoo-web\n' "$encoded" > "$home/.ssh/authorized_keys"
chown "$user:$user" "$home/.ssh/authorized_keys"
chmod 0600 "$home/.ssh/authorized_keys"
marker='# SIRUS-GENTOO-WEB-FORWARDING'
if ! grep -Fq "$marker" "$config"; then
  changed=1
  cat >> "$config" <<'SSH'
# SIRUS-GENTOO-WEB-FORWARDING
Match User sirus-tunnel
    AllowTcpForwarding remote
    PermitOpen none
    PermitListen 127.0.0.1:18480 127.0.0.1:18481
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
fi
/usr/sbin/sshd -t || fail 'Restricted SSH configuration is invalid'
settings="$(/usr/sbin/sshd -T -C user=sirus-tunnel,host=localhost,addr=127.0.0.1)"
grep -qx 'allowtcpforwarding remote' <<< "$settings" || fail 'Remote-only SSH forwarding was not enforced'
grep -qx 'permitopen none' <<< "$settings" || fail 'Local SSH forwarding was not disabled'
grep -qx 'gatewayports no' <<< "$settings" || fail 'Remote ports might be publicly reachable'
grep -qx 'passwordauthentication no' <<< "$settings" || fail 'Password authentication not blocked'
for tpl in sirus-gentoo-{dashboard,app}.{tpl,stpl}; do
  target="/usr/local/hestia/data/templates/web/nginx/$tpl"
  [[ ! -L "$target" ]] || fail "Unsafe template symlink $target"
  if [[ -e "$target" ]] && ! cmp -s "$HERE/$tpl" "$target"; then
    fail "Existing Hestia template differs: $target"
  fi
  install -m 0644 -o root -g root "$HERE/$tpl" "$target"
done
systemctl reload ssh || fail 'Could not reload SSH; restoring previous config'
changed=0
echo 'PASS restricted SSH tunnel identity and Hestia proxy templates are ready.'
echo "Previous SSH configuration backed up at: $backup"
echo 'No domain was automatically created or modified.'
echo 'Once DNS points here and Hestia SSL is issued, activate:'
echo ' /usr/local/hestia/bin/v-change-web-domain-proxy-tpl sirusweb gentoo.sirussofts.com sirus-gentoo-dashboard'
echo 'WorldCom can later use its own Hestia domain with sirus-gentoo-app.'
