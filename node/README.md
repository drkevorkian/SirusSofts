# SirusSofts legacy Gentoo compute-node prototype (staging)

**Superseded for new installations:** use the standalone [`gentoo-node/`](../gentoo-node/README.md) Python app and the independent [`ubuntu-worker/`](../ubuntu-worker/README.md) service. The normal delegation/heartbeat path is outbound signed HTTPS; an SSH tunnel is optional maintenance only.

This agent discovers CPU, RAM, GPU vendor IDs, OS/init and capabilities, signs a
heartbeat with per-node HMAC-SHA256 and sends it **outbound** over verified HTTPS.
Each heartbeat includes a timestamp and unique nonce. Network interruptions
cause bounded exponential retry. No arbitrary commands, remote shell, job
execution or database synchronization are provided yet.

## Manual Gentoo setup — not performed on any live host

1. Review and run node/install-openrc.sh as root on a **trusted checkout**.
   The installer creates the nonlogin sirusnode user, copies the Python script,
   installs an OpenRC unit and generates a random binary 32-byte heartbeat key.
   It does NOT start/enable the agent or configure networking.
2. Create a WireGuard tunnel *initiated from Gentoo to Ubuntu*; review
   wireguard/gentoo-wg0.conf.example. Configure authorized peers, firewall and
   private routes manually. WireGuard works outbound through Starlink CGNAT.
   SSH reverse tunneling may be configured separately as a fallback.
3. Point the agent's HTTPS hostname at the WireGuard VPS address via trusted
   DNS (or hosts) while maintaining a valid matching TLS certificate. The
   sample public URL uses HTTPS but is not a WireGuard tunnel by itself.
   Never disable certificate validation.
4. Securely copy the same binary key to the VPS outside the document root at
   /etc/sirussofts/node-keys/gentoo-home.key. Do NOT put keys in GitHub or chat.
   The PHP-FPM service needs read-only key access.
5. Provision a private writable /etc/sirussofts/state directory for PHP-FPM;
   set pool environment variables SIRUS_NODE_KEY_DIR and
   SIRUS_NODE_REGISTRY_FILE for CMS/node-heartbeat.php. The endpoint is disabled
   until both are set. Avoid exposing directories under the website root.
6. Configure /etc/sirus-node/config.json; run:
   python3 /opt/sirus-node/agent.py --probe
   python3 /opt/sirus-node/agent.py --once
   When verified, manually run: rc-service sirus-node start
   To enable on reboot separately: rc-update add sirus-node default

Workloads in the coordinator are metadata-only queued assignments.
There is no workload executor or automatic server deployment.
