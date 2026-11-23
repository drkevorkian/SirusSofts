#!/usr/bin/env bash
# Install-only. Never starts or enables the CPU policy without operator action.
set -euo pipefail
base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:---plan}" in
  --plan)
    echo 'PLAN: Reserve two verified physical cores (including SMT siblings) for'
    echo 'Gentoo GUI, web servers, dashboard, signed web tunnels and AI HTTP dispatch.'
    echo 'Plan computes all core numbers on the actual host; no CPU IDs are assumed.'
    echo 'Research threads, PDF parsing and llama.cpp inference use remaining cores.'
    echo 'Unmatched OS processes and IRQs are NOT isolated by this policy.'
    echo 'The installer copies files but does not start, enable or restart services.'
    echo 'Then, on Gentoo, run: python3 /opt/sirus-gentoo-node/cpu_roles.py --plan'
    ;;
  --apply)
    [[ "$(id -u)" == 0 ]] || { echo 'Run as root on Gentoo.' >&2; exit 1; }
    [[ -f /etc/gentoo-release ]] || { echo 'Gentoo only.' >&2; exit 1; }
    [[ ! -e /etc/init.d/sirus-cpu-roles && ! -L /etc/init.d/sirus-cpu-roles ]] ||
      { echo 'An existing CPU policy service requires manual upgrade review.' >&2; exit 1; }
    [[ -d /opt/sirus-gentoo-node && ! -L /opt/sirus-gentoo-node ]] ||
      { echo 'Expected installed Gentoo node directory missing or unsafe.' >&2; exit 1; }
    [[ ! -e /opt/sirus-gentoo-node/cpu_roles.py && ! -L /opt/sirus-gentoo-node/cpu_roles.py ]] ||
      { echo 'CPU policy already installed; do not silently overwrite.' >&2; exit 1; }
    python3 -m py_compile "$base/cpu_roles.py"
    install -m 0755 "$base/cpu_roles.py" /opt/sirus-gentoo-node/cpu_roles.py
    install -m 0755 "$base/openrc/sirus-cpu-roles" /etc/init.d/sirus-cpu-roles
    echo 'Installed but INACTIVE. Run the read-only CPU role plan and review actual'
    echo 'thread sibling mapping before: rc-service sirus-cpu-roles start'
    echo 'To persist after validation: rc-update add sirus-cpu-roles default'
    echo 'To restore tracked masks: rc-service sirus-cpu-roles stop;'
    echo '  python3 /opt/sirus-gentoo-node/cpu_roles.py --rollback'
    ;;
  *) echo 'Usage: install-cpu-roles.sh --plan | --apply' >&2; exit 2 ;;
esac
