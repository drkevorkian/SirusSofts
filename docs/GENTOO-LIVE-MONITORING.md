# Gentoo live monitoring scope

The signed Gentoo heartbeat reports the resource metrics already collected
(CPU load and temperature, memory, root filesystem, GPU gauges) and now adds:

- Up to 48 **running OpenRC service names**, read directly from
  `/run/openrc/started` as the unprivileged `sirusnode` user.
- Health of the two fixed localhost applications:
  `127.0.0.1:18080/healthz` (dashboard) and
  `127.0.0.1:18081/healthz` (future application router).
  An absent application is reported as unhealthy; it is not installed.
- The existing signed job lifecycle and result history.

Ubuntu's CMS checks the payload against strict caps, stores it in the private
node registry and broadcasts it through the authenticated administrator
WebSocket. The Infrastructure screen updates in place when new heartbeats
arrive, **without refreshing or opening a second tunnel**.

This is service and application monitoring, not an unrestricted process
explorer, all-process listing, log streaming or remote shell. Such features
need separate data budgets, scopes and authorization. The current node
heartbeat cadence (typically 60 seconds) determines resource update
freshness; the WebSocket detects stored changes about every two seconds.
No sub-second metric rate is implied.

Deploy CMS, socket server and Gentoo agent from the reviewed same branch.
Preserve the node HMAC key, reverse SSH tunnel and existing public routes.
Restart the Gentoo node worker and dashboard only when their installed files
have been safely upgraded. Nothing here grants privileged control or installs
operating-system packages.
