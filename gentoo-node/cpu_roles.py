#!/usr/bin/env python3
"""Opt-in, reversible Gentoo CPU affinity roles for SirusSofts.

No default mutation: --plan is read-only. --apply or --serve require root.
Two complete PHYSICAL cores (including all their SMT siblings) are allocated
to GUI/web/tunnels; named AI/PDF compute processes and named research threads
are allocated to the remaining physical cores. Other OS/third-party processes
and kernel IRQs are not controlled: this is not hard whole-host CPU isolation.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time

SYS = Path('/sys/devices/system/cpu')
PROC = Path('/proc')
STATE = Path('/run/sirus-cpu-roles-affinities.json')
WEB_COMM = frozenset({
    'nginx', 'apache2', 'httpd', 'php-fpm', 'php-fpm8.3', 'php-fpm8.4',
    'sshd', 'sshd-session', 'Xorg', 'Xwayland', 'gnome-shell',
    'kwin_x11', 'kwin_wayland', 'plasmashell', 'sway', 'Hyprland',
    'wayfire', 'weston', 'sddm', 'lightdm', 'gdm'
})
WEB_SCRIPTS = frozenset({
    '/opt/sirus-gentoo-node/dashboard.py',
    '/opt/sirus-gentoo-node/dashboard_live.py',
    '/opt/sirus-gentoo-node/client.py',
    '/opt/sirus-gentoo-node/tunnel.py',
    '/opt/sirus-ai-dual/gentoo/tunnel.py'
})
LEARN_SCRIPTS = frozenset({
    '/opt/sirus-pdf/pdf_ingest.py',
    '/opt/sirus-ai-dual/gentoo/continuous_research.py',
    '/opt/sirus-ai-dual/gentoo/manual_ingest.py',
    '/opt/sirus-ai-dual/gentoo/research_loop.py'
})
MIXED_SCRIPT = '/opt/sirus-ai-dual/gentoo/input_server.py'


def topology(sys_root=SYS, allowed=None, reserve=2):
    if allowed is None:
        allowed = os.sched_getaffinity(0)
    allowed = set(allowed)
    cores = {}
    if not allowed:
        raise RuntimeError('No allowed logical CPUs')
    for cpu in sorted(allowed):
        root = sys_root / ('cpu' + str(cpu)) / 'topology'
        try:
            key = (int((root / 'physical_package_id').read_text().strip()),
                   int((root / 'core_id').read_text().strip()))
        except (OSError, ValueError) as error:
            raise RuntimeError('Missing physical CPU topology') from error
        cores.setdefault(key, []).append(cpu)
    groups = sorted((tuple(sorted(group)) for group in cores.values()),
                    key=lambda group: group[0])
    if len(groups) <= reserve:
        raise RuntimeError('Too few physical cores for reserved web and compute roles')
    web = tuple(sorted(cpu for group in groups[:reserve] for cpu in group))
    compute = tuple(sorted(cpu for group in groups[reserve:] for cpu in group))
    return web, compute, groups


def read_process(proc_root, pid):
    base = proc_root / str(pid)
    try:
        # /proc/<pid>/stat has a parenthesized command which may contain spaces.
        suffix = (base / 'stat').read_text().rsplit(') ', 1)[1].split()
        ppid = int(suffix[1])
        start = int(suffix[19])
        comm = (base / 'comm').read_text().strip()
        argv = tuple(item.decode('utf-8', 'replace')
                     for item in (base / 'cmdline').read_bytes().split(b'\0') if item)
        tasks = {int(task.name): (task / 'comm').read_text().strip()
                 for task in (base / 'task').iterdir() if task.name.isdigit()}
        return dict(pid=pid, ppid=ppid, start=start, comm=comm,
                    argv=argv, tasks=tasks)
    except (OSError, ValueError, IndexError):
        return None


def direct_role(proc):
    argv = proc['argv']
    if MIXED_SCRIPT in argv:
        return 'mixed'
    if any(arg in WEB_SCRIPTS for arg in argv) or proc['comm'] in WEB_COMM:
        return 'web'
    if any(arg in LEARN_SCRIPTS for arg in argv):
        return 'compute'
    # Local llama.cpp workers belong on compute cores even for GPU inference;
    # their supporting CPU threads must not contend with the web and GUI.
    if any(arg.startswith('/opt/sirus-ai/llama.cpp/') and
           arg.endswith('/llama-server') for arg in argv):
        return 'compute'
    return None


def assignments(proc_root, web, compute, own_pid=None):
    if own_pid is None:
        own_pid = os.getpid()
    records = {}
    for child in proc_root.iterdir():
        if child.name.isdigit() and int(child.name) not in (1, own_pid):
            rec = read_process(proc_root, int(child.name))
            if rec:
                records[rec['pid']] = rec
    roles = {pid: direct_role(record) for pid, record in records.items()}
    # Tunnel and PDF extraction subprocesses inherit their approved role.
    for _ in range(8):
        changed = False
        for pid, rec in records.items():
            if roles[pid] is None and roles.get(rec['ppid']) in ('web', 'compute'):
                roles[pid] = roles[rec['ppid']]
                changed = True
        if not changed:
            break
    targets = []
    warnings = []
    for pid, rec in records.items():
        role = roles[pid]
        if role is None:
            continue
        if role == 'mixed' and 'sirus-learn' not in rec['tasks'].values():
            warnings.append('input_server ' + str(pid) +
                ' has no tagged learning threads; skipping the mixed process')
            continue
        for tid, thread in rec['tasks'].items():
            actual_role = ('compute' if thread == 'sirus-learn' else 'web') if role == 'mixed' else role
            mask = compute if actual_role == 'compute' else web
            targets.append(dict(pid=pid, tid=tid, start=rec['start'],
                                role=actual_role, cpus=list(mask)))
    return targets, warnings


def load_state(state_path):
    if state_path.is_symlink():
        raise RuntimeError('Refusing symlink affinity state')
    if not state_path.exists():
        return {'version': 1, 'tasks': {}}
    saved = json.loads(state_path.read_text())
    if saved.get('version') != 1 or not isinstance(saved.get('tasks'), dict):
        raise RuntimeError('Unexpected affinity state format')
    return saved


def store_state(data, state_path=STATE):
    if state_path.is_symlink():
        raise RuntimeError('Refusing symlink affinity state')
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + '.' + str(os.getpid()) + '.tmp')
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, state_path)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply_targets(targets, proc_root=PROC, state_path=STATE):
    state = load_state(state_path)
    old = state['tasks']
    # Save baselines BEFORE changing any affinity. Never overwrite a baseline.
    desired = []
    for item in targets:
        pid, tid, start = item['pid'], item['tid'], item['start']
        rec = read_process(proc_root, pid)
        if not rec or rec['start'] != start or tid not in rec['tasks']:
            continue
        try:
            original = sorted(os.sched_getaffinity(tid))
        except (OSError, ProcessLookupError):
            continue
        key = str(pid) + ':' + str(tid) + ':' + str(start)
        if key not in old:
            old[key] = original
        desired.append((tid, item['cpus']))
    store_state(state, state_path)
    success = failures = 0
    for tid, cpus in desired:
        try:
            os.sched_setaffinity(tid, cpus)
            success += 1
        except (OSError, ProcessLookupError):
            failures += 1
    return success, failures


def rollback(proc_root=PROC, state_path=STATE):
    saved = load_state(state_path)
    success = skipped = 0
    for key, cpus in saved['tasks'].items():
        try:
            pid, tid, start = (int(v) for v in key.split(':'))
            rec = read_process(proc_root, pid)
            if not rec or rec['start'] != start or tid not in rec['tasks']:
                skipped += 1
                continue
            os.sched_setaffinity(tid, cpus)
            success += 1
        except (ValueError, OSError):
            skipped += 1
    if state_path.exists():
        state_path.unlink()
    return success, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument('--plan', action='store_true', help='Read-only by default')
    group.add_argument('--apply', action='store_true', help='Apply current PID/thread affinities')
    group.add_argument('--serve', action='store_true', help='Reconcile newly started services every 5s')
    group.add_argument('--rollback', action='store_true', help='Restore tracked original masks')
    ap.add_argument('--reserve-cores', type=int, default=2,
                    help='Physical cores reserved for shared GUI/web/tunnel service role')
    args = ap.parse_args()
    if args.rollback:
        if os.geteuid() != 0: ap.error('Rollback requires root')
        changed, skipped = rollback()
        print(json.dumps(dict(restored=changed, stale_or_unavailable=skipped)))
        return
    if args.reserve_cores != 2:
        ap.error('This policy requires exactly two reserved physical cores')
    if (args.apply or args.serve) and os.geteuid() != 0:
        ap.error('Applying CPU roles requires root')
    web, compute, groups = topology(reserve=args.reserve_cores)
    if args.plan or not (args.apply or args.serve):
        targets, warnings = assignments(PROC, web, compute)
        print(json.dumps(dict(web_logical_cpus=web, compute_logical_cpus=compute,
            physical_core_groups=groups,
            assignments=targets, warnings=warnings,
            note='Affinity limits only the listed services/threads; unrelated system tasks and IRQs are not isolated.'), indent=2))
        return
    stop = False
    def halt(_signum, _frame):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    while not stop:
        targets, warnings = assignments(PROC, web, compute)
        success, failures = apply_targets(targets)
        if failures or warnings:
            print('Sirus CPU roles: pinned=' + str(success) +
                  ' failed=' + str(failures) +
                  ' warnings=' + '; '.join(warnings), file=sys.stderr, flush=True)
        if not args.serve:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
