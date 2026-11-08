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
HERE="$(cd "$(dirname "$0")" && pwd)"
json="$("$HESTIA/v-list-firewall-rule" "$rule" json)" ||
  fail 'Cannot inspect requested Hestia rule'
printf '%s' "$json" | python3 "$HERE/firewall_rules.py" --rule "$rule" --cidr "$cidr" ||
  fail 'Refusing to change unexpected firewall rule'
echo "Proposed narrowing: Hestia rule $rule ACCEPT from $cidr to TCP 8083 only."
echo 'Keep existing SSH connection and IONOS provider console available.'
[[ -t 0 && -t 1 ]] || fail 'Interactive confirmation required'
read -r -p 'Type RESTRICT PANEL to apply: ' approved
[[ "$approved" == "RESTRICT PANEL" ]] || fail 'Not approved'
"$HESTIA/v-change-firewall-rule" "$rule" ACCEPT "$cidr" 8083 TCP SIRUS_PANEL
echo 'Updated Hestia-managed firewall. Review all rules for any OTHER public 8083 allow.'
"$HESTIA/v-list-firewall"
echo 'Test SSH tunnel access and blocked public 8083 from a remote host.'
