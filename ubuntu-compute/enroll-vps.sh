#!/usr/bin/env bash
# Existing public VPS only: rotate dedicated compute-node keys, retain gateway and website.
set -Eeuo pipefail
umask 077
fail(){ echo "FAIL: $*" >&2; exit 1; }
bundle=/root/sirus-compute-enrollment
cms=/home/sirusweb/web/sirussofts.com/private/sirus-cms
[[ $# == 1 && ( "$1" == --plan || "$1" == --apply ) ]] || fail 'Usage: enroll-vps.sh --plan|--apply'
[[ $(id -u) == 0 && -x /usr/local/hestia/bin/v-list-web-domain ]] || fail 'Run only on existing public Hestia VPS'
/usr/local/hestia/bin/v-list-web-domain sirusweb sirussofts.com >/dev/null
[[ -d "$bundle" && ! -L "$bundle" ]] || fail "Missing private transfer directory $bundle"
chmod 0700 "$bundle"
[[ -f "$cms/nodes/keys/gentoo-home.key" && ! -L "$cms/nodes/keys/gentoo-home.key" ]] || fail 'Existing CMS node key not found; inspect actual deployed private path'
[[ -f /etc/sirus-ai-dual/secret && ! -L /etc/sirus-ai-dual/secret ]] || fail 'Existing gateway secret missing'
[[ -f /etc/ssh/ssh_host_ed25519_key.pub ]] || fail 'Missing existing server host public key'
systemctl is-active --quiet sirus-ai-gateway || fail 'Existing gateway inactive'
/usr/sbin/sshd -t
declare -A homes keys restrictions
keys[sirus-tunnel]=web
keys[sirus-ai-in]=input
keys[sirus-ai-out]=output
restrictions[sirus-tunnel]='restrict,port-forwarding,permitlisten="127.0.0.1:18480",permitlisten="127.0.0.1:18481"'
restrictions[sirus-ai-in]='restrict,port-forwarding,permitlisten="127.0.0.1:18485"'
restrictions[sirus-ai-out]='restrict,port-forwarding,permitopen="127.0.0.1:18483"'
for who in sirus-tunnel sirus-ai-in sirus-ai-out; do
  homes[$who]="$(getent passwd "$who" | cut -d: -f6)"
  path="$bundle/${keys[$who]}.pub"
  [[ -f "$path" && ! -L "$path" ]] || fail "Missing $path"
  [[ $(wc -l < "$path") == 1 ]] || fail 'Expected one public key'
  ssh-keygen -lf "$path" >/dev/null
  [[ -f "${homes[$who]}/.ssh/authorized_keys" && ! -L "${homes[$who]}/.ssh/authorized_keys" && ! -L "${homes[$who]}/.ssh" ]] || fail "Existing restricted key file missing for $who"
  effective="$(/usr/sbin/sshd -T -C "user=$who,host=localhost,addr=127.0.0.1")"
  grep -qx 'gatewayports no' <<< "$effective" || fail "$who public forward risk"
  grep -qx 'passwordauthentication no' <<< "$effective" || fail "$who password enabled"
  if [[ "$who" == sirus-ai-out ]]; then
    grep -qx 'allowtcpforwarding local' <<< "$effective" || fail "$who forwarding mismatch"
    grep -qx 'permitopen 127.0.0.1:18483' <<< "$effective" || fail "$who permitopen mismatch"
  else
    grep -qx 'allowtcpforwarding remote' <<< "$effective" || fail "$who forwarding mismatch"
  fi
done
python3 - "$bundle" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])
keys=[]
for f in ('web.pub','input.pub','output.pub'):
    row=(p/f).read_text().split()
    assert len(row)>=2 and row[0]=='ssh-ed25519' and re.fullmatch('[A-Za-z0-9+/=]+',row[1])
    keys.append(row[1])
assert len(set(keys))==3
assert not (p/'heartbeat.key').is_symlink() and len((p/'heartbeat.key').read_bytes())==32
assert re.fullmatch('[0-9a-f]{64}',pathlib.Path('/etc/sirus-ai-dual/secret').read_text().strip())
PY
echo 'Will replace the three dedicated compute SSH authorized_keys files and gentoo-home HMAC key; existing keys will be backed up.'
echo 'Will securely export existing gateway secret, optional research key and VPS host public key for the replacement computer.'
echo 'No Hestia, Nginx, sshd configuration, gateway service, CMS DB or firewall changes.'
if [[ "$1" == --plan ]]; then exit 0; fi
[[ -t 0 ]] || fail 'Interactive administrator terminal required'
read -rp 'Stop the old Gentoo node/tunnels first. Type REPLACE GENTOO NODE: ' answer
[[ "$answer" == 'REPLACE GENTOO NODE' ]] || fail 'Declined'
if ss -H -lnt | awk '{print $4}' | grep -Eq ':18480$|:18481$|:18485$'; then
  fail 'Old reverse tunnel listener still present; stop old node first'
fi
backup="$(mktemp -d /root/sirus-compute-key-backup.XXXXXXXX)"
for who in sirus-tunnel sirus-ai-in sirus-ai-out; do
  cp -p "${homes[$who]}/.ssh/authorized_keys" "$backup/$who.authorized_keys"
done
cp -p "$cms/nodes/keys/gentoo-home.key" "$backup/gentoo-home.key"
rollback(){
  local status=$?
  if (( status != 0 )); then
    for who in sirus-tunnel sirus-ai-in sirus-ai-out; do
      cp -p "$backup/$who.authorized_keys" "${homes[$who]}/.ssh/authorized_keys" || true
    done
    cp -p "$backup/gentoo-home.key" "$cms/nodes/keys/gentoo-home.key" || true
    echo "Reverted enrollment keys; private backup: $backup" >&2
  fi
}
trap rollback EXIT
for who in sirus-tunnel sirus-ai-in sirus-ai-out; do
  read -r typ pub _ < "$bundle/${keys[$who]}.pub"
  target="${homes[$who]}/.ssh/authorized_keys"
  tmp="$(mktemp "${homes[$who]}/.ssh/.sirus-key.XXXXXXXX")"
  printf '%s %s %s sirus-compute\n' "${restrictions[$who]}" "$typ" "$pub" > "$tmp"
  chown --reference="$target" "$tmp"
  chmod 0600 "$tmp"
  mv -f "$tmp" "$target"
done
tmp="$(mktemp "$cms/nodes/keys/.gentoo-home.XXXXXXXX")"
install -o sirusweb -g sirusweb -m 0600 "$bundle/heartbeat.key" "$tmp"
mv -f "$tmp" "$cms/nodes/keys/gentoo-home.key"
install -m 0600 /etc/sirus-ai-dual/secret "$bundle/secret"
install -m 0600 /etc/ssh/ssh_host_ed25519_key.pub "$bundle/host_ed25519.pub"
if [[ -f "$cms/research-admin.key" && ! -L "$cms/research-admin.key" ]]; then
  [[ $(wc -c < "$cms/research-admin.key") == 32 ]] || fail 'Unexpected research key length'
  install -m 0600 "$cms/research-admin.key" "$bundle/research-admin.key"
fi
echo "Enrollment complete. Private rollback copies: $backup"
echo "Securely return $bundle to the home Ubuntu node via verified administrative SSH."
echo 'Old dedicated key authorizations have been replaced; no SSH daemon reload required.'
