#!/usr/bin/env python3
"""Two independently managed, pinned, persistent SSH connections.

INPUT is -R (public gateway to Gentoo). OUTPUT is -L (Gentoo pushes to
public gateway's private receiver). Both originate from Gentoo behind NAT.
"""
import argparse
import urllib.request
import json
from pathlib import Path
import signal
import subprocess
import threading
import time

STOP = threading.Event()


def ssh_command(config, channel):
    if channel not in ('input', 'output'):
        raise ValueError('Invalid tunnel channel')
    cfg = config
    host = cfg['host']
    known = cfg['known_hosts']
    identity = cfg[channel + '_identity']
    for file in (known, identity):
        p = Path(file)
        if not p.is_file() or not p.stat().st_mode & 0o077 == 0:
            raise RuntimeError('SSH identity/known_hosts must be private files: ' + file)
    common = ['/usr/bin/ssh', '-F', '/dev/null', '-N', '-T',
              '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
              '-o', 'StrictHostKeyChecking=yes',
              '-o', 'UserKnownHostsFile=' + known,
              '-o', 'GlobalKnownHostsFile=/dev/null',
              '-o', 'HostKeyAlgorithms=ssh-ed25519',
              '-o', 'ExitOnForwardFailure=yes',
              '-o', 'ServerAliveInterval=15',
              '-o', 'ServerAliveCountMax=3',
              '-o', 'TCPKeepAlive=yes',
              '-o', 'ConnectTimeout=10',
              '-o', 'ConnectionAttempts=1',
              '-o', 'ForwardAgent=no',
              '-o', 'ForwardX11=no',
              '-o', 'RequestTTY=no',
              '-o', 'ControlMaster=no',
              '-i', identity, '-p', str(cfg['ssh_port'])]
    if channel == 'input':
        common += ['-l', 'sirus-ai-in', '-R', '127.0.0.1:18485:127.0.0.1:18082']
    else:
        common += ['-l', 'sirus-ai-out', '-L', '127.0.0.1:18084:127.0.0.1:18483']
    return common + [host]


def output_heartbeat():
    # An application-level end-to-end heartbeat traverses the OUTPUT SSH -L
    # forward, so the browser never confuses a healthy Ubuntu daemon with
    # a working Gentoo->Ubuntu output connection.
    try:
        secret = Path('/etc/sirus-ai-dual/secret').read_text().strip()
    except OSError:
        print('OUTPUT heartbeat secret missing', flush=True)
        return
    while not STOP.is_set():
        req = urllib.request.Request('http://127.0.0.1:18084/internal/heartbeat',
                                     data=b'{}', method='POST', headers={'X-Sirus-AI-Token': secret})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status != 200:
                    print('OUTPUT heartbeat failed:', response.status, flush=True)
        except OSError:
            pass  # SSH keepalive + reconnect loop handles transport loss
        STOP.wait(15)


def loop(cfg, channel):
    backoff = 2
    if channel == 'output':
        threading.Thread(target=output_heartbeat, daemon=True).start()
    while not STOP.is_set():
        p = None
        started = time.monotonic()
        try:
            p = subprocess.Popen(ssh_command(cfg, channel), stdin=subprocess.DEVNULL)
            print(channel, 'tunnel active as SSH process', p.pid, flush=True)
            while p.poll() is None and not STOP.wait(2):
                pass
            if not STOP.is_set():
                print(channel, 'tunnel exited; reconnecting', flush=True)
        except OSError as exc:
            print(channel, 'SSH launch failed:', type(exc).__name__, flush=True)
        finally:
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        if STOP.is_set():
            break
        backoff = 2 if time.monotonic() - started >= 300 else min(60, backoff * 2)
        STOP.wait(backoff)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', required=True, choices=('input', 'output'))
    ap.add_argument('--config', default='/etc/sirus-ai-dual/tunnel.json')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_text())
    cmd = ssh_command(config, args.channel)
    if args.check:
        print('PASS independent', args.channel, 'tunnel, pinned SSH, 15s keepalive')
        return
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    loop(config, args.channel)


if __name__ == '__main__':
    main()
