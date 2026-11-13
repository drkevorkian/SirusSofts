#!/usr/bin/env python3
"""SirusSofts internal, read-only diagnostic collector. Python 3 stdlib only.

Does not open network listeners, change settings, read secrets, or execute
remote commands. Optional outbound report delivery requires a PRIVATE GitHub repo.
"""
import argparse
import base64
import datetime
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
from urllib import error, request

ALLOWED_SSH_KEYS = {
    'permitrootlogin', 'passwordauthentication', 'pubkeyauthentication',
    'kbdinteractiveauthentication', 'x11forwarding', 'allowtcpforwarding',
    'permitemptypasswords', 'port', 'maxauthtries'
}
SERVICES = ('ssh', 'sshd', 'nginx', 'apache2', 'httpd', 'sw-cp-server',
            'sw-engine', 'psa', 'fail2ban', 'mariadb', 'mysql')
CHECK_PORTS = {22: 'SSH', 80: 'HTTP', 443: 'HTTPS', 8443: 'Plesk panel',
               8447: 'Plesk installer/updater', 3306: 'MySQL/MariaDB', 5432: 'PostgreSQL'}


def command(args, timeout=8):
    """Run a fixed, local, read-only command; never invoke a shell."""
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, check=False)
        return p.returncode, p.stdout.strip()
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return -1, ''


def read_os_release(path='/etc/os-release'):
    values = {}
    try:
        for line in Path(path).read_text(encoding='utf8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                if key in ('ID', 'VERSION_ID', 'PRETTY_NAME'):
                    values[key] = value.strip().strip('"')[:120]
    except OSError:
        pass
    return values


def scope_of(address):
    address = address.strip('[]').lower()
    if address in ('*', '0.0.0.0', '::'):
        return 'wildcard'
    if address == '::1' or address.startswith('127.'):
        return 'loopback'
    return 'specific-interface'


def parse_listeners(output):
    """Intentionally discard addresses and process names from ss output."""
    results = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        if ':' not in local:
            continue
        address, port = local.rsplit(':', 1)
        if port.isdigit() and 0 < int(port) < 65536:
            results.add((int(port), scope_of(address)))
    return [{'port': port, 'bind': bind, 'service_hint': CHECK_PORTS.get(port, 'other')}
            for port, bind in sorted(results)]


def parse_ssh_config(output):
    config = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ALLOWED_SSH_KEYS:
            val = parts[1].lower().strip()
            if re.fullmatch(r'[a-z0-9-]{1,32}', val):
                config[parts[0].lower()] = val
    return config


def plesk_version(output):
    for line in output.splitlines():
        m = re.search(r'Product version\s*:\s*([^\r\n]{1,80})', line, re.I)
        if m:
            version = re.search(r'\d+(?:\.\d+){1,4}', m.group(1))
            return version.group(0) if version else 'detected'
    return 'detected' if output else 'unavailable'


def collect():
    report = {
        'schema': 'sirussofts.internal.v1',
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'host_identity': 'redacted',
        'os': read_os_release(),
        'kernel_release': platform.release()[:80],
        'root_privileges': hasattr(os, 'geteuid') and os.geteuid() == 0,
        'services': {},
        'listeners': [],
        'ssh_effective': {},
        'firewall': {},
        'plesk': {},
        'observations': []
    }
    for service in SERVICES:
        rc, result = command(['systemctl', 'is-active', service], timeout=3)
        if rc == 0:
            report['services'][service] = 'active'
        elif result in ('inactive', 'failed'):
            report['services'][service] = result

    rc, output = command(['ss', '-H', '-lnt'])
    report['listeners'] = parse_listeners(output) if rc == 0 else []
    if rc != 0:
        report['observations'].append('TCP listener inventory unavailable')

    rc, output = command(['sshd', '-T'])
    if rc != 0:
        rc, output = command(['/usr/sbin/sshd', '-T'])
    if rc == 0:
        report['ssh_effective'] = parse_ssh_config(output)
    else:
        report['observations'].append('Effective SSH configuration unavailable')

    rc, output = command(['ufw', 'status'])
    if rc == 0:
        m = re.search(r'^Status:\s*(active|inactive)', output, re.M | re.I)
        report['firewall']['ufw'] = m.group(1).lower() if m else 'unknown'
    else:
        report['firewall']['ufw'] = 'unavailable'
    rc, output = command(['nft', 'list', 'tables'])
    report['firewall']['nft_tables_present'] = bool(rc == 0 and re.search(r'^table ', output, re.M)) if rc == 0 else None

    rc, output = command(['plesk', 'version'])
    report['plesk']['installed'] = rc == 0
    report['plesk']['version'] = plesk_version(output) if rc == 0 else 'unavailable'

    wild = {item['port'] for item in report['listeners'] if item['bind'] == 'wildcard'}
    if 8443 in wild:
        report['observations'].append('Plesk panel listens on a wildcard address; external reachability is NOT established')
    if 8447 in wild:
        report['observations'].append('Plesk installer listens on a wildcard address; review access controls')
    if 3306 in wild or 5432 in wild:
        report['observations'].append('Database port listens on a wildcard address; review firewall restrictions')
    if report['ssh_effective'].get('permitrootlogin') == 'yes':
        report['observations'].append('SSH PermitRootLogin=yes; review necessity')
    if report['ssh_effective'].get('passwordauthentication') == 'yes':
        report['observations'].append('SSH password authentication enabled; review necessity')
    if report['firewall']['ufw'] == 'inactive':
        report['observations'].append('UFW inactive; this does NOT establish that the host lacks other firewall controls')
    report['observations'].append('Local listeners and firewall checks cannot prove internet reachability')
    return report


def write_private_json(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('refusing symlink output target')
    if target.parent.stat().st_mode & 0o077:
        raise ValueError('report directory must not be accessible by group or others')
    fd, tmp = tempfile.mkstemp(prefix='.audit-', dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf8') as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def github_api(method, endpoint, token, body=None):
    data = None if body is None else json.dumps(body).encode('utf8')
    req = request.Request('https://api.github.com' + endpoint, data=data, method=method,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28',
                                   'Content-Type': 'application/json',
                                   'User-Agent': 'SirusSofts-internal-audit/0.1'})
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except error.HTTPError as exc:
        if exc.code == 404:
            return 404, {}
        raise RuntimeError('GitHub API status ' + str(exc.code)) from None


def upload_private_report(report, repository, token):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or any(part in ('.', '..') for part in repository.split('/')):
        raise ValueError('invalid report repository')
    if not token:
        raise ValueError('missing GitHub token')
    repo_url = '/repos/' + repository
    status, meta = github_api('GET', repo_url, token)
    if status != 200 or meta.get('private') is not True:
        raise ValueError('report destination MUST be a private GitHub repository')
    path = '/security-reports/internal/latest.json'
    status, old = github_api('GET', repo_url + '/contents' + path, token)
    payload = {
        'message': 'chore(security): update internal redacted audit',
        'content': base64.b64encode((json.dumps(report, indent=2, sort_keys=True) + '\n').encode('utf8')).decode('ascii')
    }
    if status == 200:
        payload['sha'] = old['sha']
    elif status != 404:
        raise RuntimeError('unable to confirm existing report state')
    status, _ = github_api('PUT', repo_url + '/contents' + path, token, payload)
    if status not in (200, 201):
        raise RuntimeError('GitHub did not confirm report delivery')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='/var/lib/sirus-audit/latest.json')
    p.add_argument('--upload-if-configured', action='store_true',
                   help='upload only when a private repo and token are configured')
    args = p.parse_args(argv)
    os.umask(0o077)
    report = collect()
    write_private_json(args.output, report)
    print('Saved read-only redacted internal diagnostic report:', args.output)
    print('Services active:', ', '.join(k for k, v in report['services'].items() if v == 'active') or 'none detected')
    print('TCP listening port/bind combinations:', len(report['listeners']))
    print('Review observations:', len(report['observations']))
    repo = os.environ.get('SIRUS_AUDIT_REPORT_REPO', '')
    token = os.environ.get('SIRUS_AUDIT_GITHUB_TOKEN', '')
    if args.upload_if_configured and (repo or token):
        if not repo or not token:
            raise RuntimeError('both SIRUS_AUDIT_REPORT_REPO and SIRUS_AUDIT_GITHUB_TOKEN are required')
        upload_private_report(report, repo, token)
        print('Delivered redacted report to private GitHub repository:', repo)
    else:
        print('GitHub upload skipped (not configured or not requested).')


if __name__ == '__main__':
    main()
