#!/usr/bin/env bash
# Hestia-native port-8083 rule restriction. Fails closed on unexpected rule data.
set -Eeuo pipefail
fail(){ echo "FAIL: $*" >&2; exit 1; }
mode="--check"; if [[ $# -gt 0 ]]; then mode="$1"; shift; fi
rule=""; cidr="127.0.0.1/32"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rule) [[ $# -ge 2 ]] || fail '--rule requires an ID'; rule="$2"; shift 2;;
    --admin-cidr) [[ $# -ge 2 ]] || fail '--admin-cidr requires an IPv4 CIDR'; cidr="$2"; shift 2;;
    *) fail "Unknown parameter: $1";;
  esac
done
[[ "$mode" == --check || "$mode" == --plan || "$mode" == --apply ]] ||
  fail 'Usage: 05-lockdown-panel.sh --check|--plan|--apply --rule ID [--admin-cidr IPv4/32]'
if [[ "$mode" == --check ]]; then
  echo 'PASS firewall rule helper parsed. --apply requires exact confirmed Hestia public 8083 rule ID.'
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail 'Root is required for firewall review'
HESTIA=/usr/local/hestia/bin
[[ -x "$HESTIA/v-list-firewall" && -x "$HESTIA/v-list-firewall-rule" &&
   -x "$HESTIA/v-change-firewall-rule" ]] || fail 'Missing installed Hestia firewall commands'
if [[ "$mode" == --plan ]]; then
  echo 'Inspect existing Hestia-managed firewall rules:'
  "$HESTIA/v-list-firewall"
  echo 'The public ACCEPT TCP 8083 entry must be narrowed, not merely preceded by a new allow rule.'
  echo 'Apply with the exact public rule number after verifying it via:'
  echo '  05-lockdown-panel.sh --apply --rule RULE_NUMBER'
  echo 'Default allows 8083 only from loopback; use an SSH local port-forward.'
  echo 'SSH tunnel: ssh -L 8083:127.0.0.1:8083 root@YOUR_HOST'
  echo 'After tunnel, open https://localhost:8083 and verify the panel certificate.'
  exit 0
fi
[[ "$rule" =~ ^[1-9][0-9]{0,4}$ ]] || fail 'Rule number must be a positive integer'
command -v python3 >/dev/null || fail 'python3 required to inspect Hestia JSON'
python3 - "$cidr" <<'PY'
import ipaddress, sys
try:
    ip = ipaddress.ip_network(sys.argv[1], strict=False)
    if ip.version != 4 or ip.prefixlen < 24 or ip.is_multicast or ip.is_unspecified:
        raise ValueError
except ValueError:
    sys.exit("Invalid admin IPv4 CIDR; use loopback /32 or a small trusted /24-/32 subnet")
PY
json="$("$HESTIA/v-list-firewall-rule" "$rule" json)" || fail 'Could not read Hestia firewall rule'
# Reject unknown fields/shapes and the wrong rule rather than risking SSH or web access.
RULE_JSON="$json" python3 - "$rule" <<'PY'
import json,os,sys
# Hestia CLI may return an ID-keyed object or an unwrapped rule.
raw = os.environ.get('RULE_JSON', '')
try:
    data = json.loads(raw)
except ValueError:
    sys.exit("Firewall rule JSON is invalid")
num = sys.argv[1]
if not isinstance(data,dict):
    sys.exit("Unexpected Hestia firewall JSON response")
if num in data and isinstance(data[num],dict):
    data = data[num]
fields = {str(k).upper():str(v).upper() for k,v in data.items()}
if fields.get('ACTION') != 'ACCEPT' or fields.get('IP') not in ('0.0.0.0/0','0.0.0.0') or fields.get('PORT') != '8083' or fields.get('PROTOCOL') != 'TCP':
    sys.exit("Rule is not a public ACCEPT TCP 8083 entry; refusing firewall change")
PY
echo "Proposed narrowing: Hestia rule $rule ACCEPT from $cidr to TCP 8083 only."
echo 'Keep existing SSH connection and IONOS provider console available.'
[[ -t 0 && -t 1 ]] || fail 'Interactive confirmation required'
read -r -p 'Type RESTRICT PANEL to apply: ' approved
[[ "$approved" == "RESTRICT PANEL" ]] || fail 'Not approved'
"$HESTIA/v-change-firewall-rule" "$rule" ACCEPT "$cidr" 8083 TCP SIRUS_PANEL
echo 'Updated Hestia-managed firewall. Review all rules for any OTHER public 8083 allow.'
"$HESTIA/v-list-firewall"
echo 'Test SSH tunnel access and blocked public 8083 from a remote host.'
