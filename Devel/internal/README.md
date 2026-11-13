# SirusSofts internal diagnostic agent (0.1)

**Status:** Source only. No server access or installation has occurred. This complements the deploy/ webhook agent on the staging branch; it does not depend on Plesk.

## Purpose

An internal, read-only Ubuntu inventory for an authorized administrator: effective SSH security settings, TCP listening ports **without IP addresses**, Plesk version, systemd service state, and limited firewall status. This is an inventory, **not a completed penetration test**. In particular a wildcard TCP listener does **not** establish internet reachability.

### Safety and privacy

- No inbound port, listener, remote shell, code execution API, or server-side control channel.
- No access to application databases, credential files, SSH private keys, Plesk licenses, request logs, environment secrets, or source files.
- Never includes hostname, IP addresses, process arguments, or firewall rules in reports. No credentials are written to report JSON.
- Local reports default to /var/lib/sirus-audit/latest.json with mode 0600, directory 0700.
- **Does not publish any report to the public SirusSofts repository.** An optional upload is allowed ONLY when the target GitHub repository is confirmed private through the GitHub API.
- Does not change Plesk, UFW, nftables, SSH configuration, permissions on web content, or any web process; systemd install only creates the audit service files.
- Commands are fixed and use no shell interpolation. No arbitrary command interface.

## Install on Ubuntu

From a directory containing these files (for example internal/ in the feature branch):

    sudo sh install-ubuntu.sh
    sudo systemctl start sirus-audit.service
    sudo cat /var/lib/sirus-audit/latest.json

No reboot, cron, Plesk subscription, SSH credential sharing, or inbound firewall change required.

## Optional PRIVATE GitHub report relay

1. Create a PRIVATE GitHub repository exclusively for security reports. Do not use the public SirusSofts code repository.
2. Create a fine-grained GitHub token restricted to that repository with Contents: Read and write only. Keep it on the VPS; do not paste it into ChatGPT.
3. Create /etc/sirus-audit/github.env owned by root and mode 0600 with:

    SIRUS_AUDIT_REPORT_REPO=drkevorkian/YOUR_PRIVATE_SECURITY_REPO
    SIRUS_AUDIT_GITHUB_TOKEN=YOUR_FINE_GRAINED_TOKEN

4. Run sudo systemctl start sirus-audit.service. If the token is valid and the destination is verified private, the agent uploads security-reports/internal/latest.json. The report contains only the limited redacted inventory. GitHub versions every change. **No automatic uploads occur until both values are configured.**
5. Optionally enable hourly collection with sudo systemctl enable --now sirus-audit.timer. This is optional and is not enabled by installation.

To turn uploads off, remove /etc/sirus-audit/github.env or revoke the token. To stop hourly scheduling, run sudo systemctl disable --now sirus-audit.timer.

## Access limits

ChatGPT can inspect the report only after a private repository with access through its GitHub connector is set up. Nothing here grants ChatGPT inbound access to the VPS, deploys your existing CMS, or exposes the backend IP address. Use a separate external test to verify public network reachability.
