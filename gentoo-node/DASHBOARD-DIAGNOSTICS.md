# Gentoo dashboard: task history and GPU counters

This runbook is for the already working public `/Gentoo/` dashboard. The public
reverse SSH tunnel and the node-to-Ubuntu work broker are **different paths**:
the dashboard HTML is sent from Gentoo through the tunnel, while the signed
history query originates on Gentoo and calls the Ubuntu CMS HTTPS endpoint.

## Task history: read-only diagnosis first

On Ubuntu, check the **live deployed CMS**, not just the Git checkout:

```bash
test -f /home/sirusweb/web/sirussofts.com/public_html/node-work.php && echo 'Work endpoint installed' || echo 'Work endpoint missing'
test -d /home/sirusweb/web/sirussofts.com/private/sirus-cms/nodes/keys && echo 'Node key directory exists' || echo 'Node key directory missing'
curl -sS --max-time 10 -o /dev/null -w 'Work endpoint GET: %{http_code}\n' https://sirussofts.com/node-work.php
```

A correctly installed `node-work.php` rejects an unauthenticated GET with
HTTP 405. A 404 suggests the upgrade or routing is missing. A 503 in response
to a **signed** request suggests the private broker directory is absent or
not writable by the PHP-FPM website user.

On Gentoo, run this read-only signed history test as the installed service
account (do **not** print the HMAC key or dump `config.json`):

```bash
sudo runuser -u sirusnode -- python3 - <<'PY'
import json, sys
from urllib.error import HTTPError, URLError
sys.path.insert(0, '/opt/sirus-gentoo-node')
import client
try:
    cfg = client.validate_config(json.load(open('/etc/sirus-gentoo-node/config.json')))
    result = client.work_request(cfg, client.load_key(cfg['key_file']), 'history')
    print('PASS: signed coordinator history; jobs:', len(result['jobs']))
except HTTPError as exc:
    print('FAIL: coordinator HTTP', exc.code)
except URLError:
    print('FAIL: coordinator DNS, TLS or connection error')
except Exception as exc:
    print('FAIL:', type(exc).__name__, '(review configuration and deployment)')
PY
```

To upgrade a missing CMS work endpoint, first review and run the *existing*
guarded deploy helper from the reviewed checkout:

```bash
cd /root/SirusSofts
bash deploy/ubuntu24/08-upgrade-cms-hestia.sh --plan --user sirusweb --domain sirussofts.com
```

Only run `--apply` after inspecting its plan and confirming it will not
overwrite local CMS code changes. It backs up the existing code and SQLite.
Do not regenerate the 32-byte node key; verify that Ubuntu's registered key
matches the existing Gentoo node identity without publishing either value.

## GPU diagnostics (no driver changes)

The PCI inventory can identify a GTX 1660 or RX 590 even when the service
account cannot read NVIDIA NVML or the AMD DRM/hwmon interfaces. Missing
utilization or temperature is **N/A**, not zero.

On Gentoo, inspect these read-only interfaces:

```bash
lspci -nnk | grep -A 4 -Ei 'VGA compatible|3D controller|Display controller'
command -v nvidia-smi && nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits
ls -l /sys/class/drm/card*/device/gpu_busy_percent /sys/class/drm/card*/device/hwmon/hwmon*/temp1_input 2>/dev/null
```

Repeat the NVIDIA utility as `sirusnode` when available; the dashboard
must be able to read the telemetry under its actual service identity.
The updated collector additionally checks `/sys/class/hwmon` sensors only
when each sensor resolves to the same PCI device.

Do not install drivers, change GPU assignments, adjust disk partitions or
clear caches merely to force readings. Investigate missing hardware counters
separately if the new dashboard still shows N/A.

After code deployment to Gentoo, restart only the dashboard and compute
agent when their Python files have been updated, then open the existing
`https://sirussofts.com/Gentoo/` route. No DNS/Hestia changes are needed.
