"""Offline coverage for Gentoo's opt-in two-physical-core CPU role policy."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'cpu_roles.py'
spec = importlib.util.spec_from_file_location('cpu_roles', MODULE)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def process(root, pid, ppid, argv, comm='python3', threads=None, start=222):
    base = root / str(pid)
    base.mkdir()
    # Fields after the parenthesized comm start at process state (field 3).
    (base / 'stat').write_text(
        str(pid) + ' (' + comm + ') ' +
        ' '.join(['S', str(ppid)] + ['0'] * 17 + [str(start)]))
    (base / 'comm').write_text(comm)
    (base / 'cmdline').write_bytes(b'\0'.join(x.encode() for x in argv) + b'\0')
    for tid, name in (threads or {pid: comm}).items():
        task = base / 'task' / str(tid)
        task.mkdir(parents=True)
        (task / 'comm').write_text(name)


class CpuRoleTests(unittest.TestCase):
    def test_two_entire_physical_cores_with_smt_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysroot = Path(tmp)
            for cpu, core in [(0, 0), (8, 0), (1, 1), (9, 1),
                              (2, 2), (10, 2), (3, 3), (11, 3)]:
                d = sysroot / ('cpu' + str(cpu)) / 'topology'
                d.mkdir(parents=True)
                (d / 'physical_package_id').write_text('0')
                (d / 'core_id').write_text(str(core))
            web, compute, groups = policy.topology(
                sysroot, set([0, 1, 2, 3, 8, 9, 10, 11]))
            self.assertEqual(web, (0, 1, 8, 9))
            self.assertEqual(compute, (2, 3, 10, 11))
            self.assertEqual(len(groups), 4)

    def test_web_compute_mixed_threads_and_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            process(proc, 100, 1, ['/usr/bin/python3',
                '/opt/sirus-gentoo-node/dashboard.py'])
            process(proc, 200, 1, ['/usr/bin/python3',
                '/opt/sirus-pdf/pdf_ingest.py'])
            process(proc, 201, 200, ['/usr/bin/pdftotext', 'manual.pdf'],
                    comm='pdftotext')
            process(proc, 300, 1, ['/usr/bin/python3',
                '/opt/sirus-ai-dual/gentoo/input_server.py'],
                threads={300:'python3', 301:'sirus-web', 302:'sirus-learn'})
            process(proc, 400, 1, ['/bin/sh'], comm='sh')
            targets, warnings = policy.assignments(proc, (0, 8), (1, 9),
                                                    own_pid=99999)
            result = {t['tid']: (t['role'], t['cpus']) for t in targets}
            self.assertEqual(result[100], ('web', [0, 8]))
            self.assertEqual(result[200], ('compute', [1, 9]))
            self.assertEqual(result[201], ('compute', [1, 9]))
            self.assertEqual(result[300], ('web', [0, 8]))
            self.assertEqual(result[301], ('web', [0, 8]))
            self.assertEqual(result[302], ('compute', [1, 9]))
            self.assertNotIn(400, result)
            self.assertFalse(warnings)

    def test_unmarked_mixed_service_is_skipped_not_starved(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            process(proc, 300, 1, ['/usr/bin/python3',
                '/opt/sirus-ai-dual/gentoo/input_server.py'])
            targets, warnings = policy.assignments(proc, (0, 8), (1, 9),
                                                    own_pid=99999)
            self.assertEqual(targets, [])
            self.assertEqual(len(warnings), 1)

    def test_refuse_too_few_physical_cores(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysroot = Path(tmp)
            for cpu in range(2):
                d = sysroot / ('cpu' + str(cpu)) / 'topology'
                d.mkdir(parents=True)
                (d / 'physical_package_id').write_text('0')
                (d / 'core_id').write_text(str(cpu))
            with self.assertRaises(RuntimeError):
                policy.topology(sysroot, {0, 1}, reserve=2)


if __name__ == '__main__':
    unittest.main()
