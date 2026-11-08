#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run as root (sudo or root shell).' >&2; exit 1
fi
SRC=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
install -d -m 0700 /var/lib/sirus-audit /etc/sirus-audit
install -d -m 0755 /opt/sirus-audit
install -m 0644 "$SRC/agent.py" /opt/sirus-audit/agent.py
install -m 0644 "$SRC/sirus-audit.service" /etc/systemd/system/sirus-audit.service
install -m 0644 "$SRC/sirus-audit.timer" /etc/systemd/system/sirus-audit.timer
systemctl daemon-reload
printf '\nInstalled (NOT scheduled, NOT uploaded).\n'
printf 'To run once: systemctl start sirus-audit.service\n'
printf 'To view sanitized report as root: cat /var/lib/sirus-audit/latest.json\n'
printf 'Optional hourly reports: systemctl enable --now sirus-audit.timer\n'
