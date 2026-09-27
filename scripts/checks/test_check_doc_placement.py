#!/usr/bin/env python3
"""Tests for check_doc_placement.py, the Python twin of check-doc-placement.ps1.

stdlib only; run with pytest OR `python3 test_check_doc_placement.py`.

Each case builds a scratch git repository (HEAD holds one legacy feature directory under
documentation/implementation-guide/, the index README and multi-agent-concurrency/), stages
something and runs the gate in a subprocess, the way .githooks/pre-commit runs it. Every
case runs through the Python twin; where a PowerShell host exists (powershell.exe on
Windows, pwsh elsewhere) the SAME case also runs through the .ps1 and the two exit codes
must agree - that is what keeps the twins in step.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_GATE = os.path.join(HERE, 'check_doc_placement.py')
PS_GATE = os.path.join(HERE, 'check-doc-placement.ps1')


def _ps_host():
    if os.name == 'nt' and shutil.which('powershell'):
        return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File']
    if shutil.which('pwsh'):
        return ['pwsh', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File']
    return None


PS_HOST = _ps_host()


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# (name, paths to stage as NEW files, paths to stage as RENAMES from a tracked file,
#  expected exit)
CASES = [
    ('notes file', ['documentation/notes/x.md'], [], 1),
    ('evidence file, any extension', ['documentation/evidence/run.log'], [], 1),
    ('archive file', ['documentation/archive/old.md'], [], 1),
    ('journal dir, other case', ['Documentation/Notes/x.md'], [], 1),
    ('MERGE-PROTOCOL named file in notes is not exempt', ['documentation/notes/MERGE-PROTOCOL.md'], [], 1),
    ('rename into notes', [], [('keep/doc.md', 'documentation/notes/doc.md')], 1),
    ('root PLAN.md', ['PLAN.md'], [], 1),
    ('root PLANNING.md', ['PLANNING.md'], [], 1),
    ('root TEST-PLAN-foo.txt', ['TEST-PLAN-foo.txt'], [], 1),
    ('root extensionless TEST_PLAN', ['TEST_PLAN'], [], 1),
    ('root x-FINDINGS.md', ['x-FINDINGS.md'], [], 1),
    ('root TASKS.md', ['TASKS.md'], [], 1),
    ('root roadmap', ['roadmap'], [], 1),
    ('root PLANNER.py (code extension)', ['PLANNER.py'], [], 0),
    ('root planets.txt (no word boundary)', ['planets.txt'], [], 0),
    ('root plan directory is not refused', ['planner/__init__.py'], [], 0),
    ('new feature dir under implementation-guide', ['documentation/implementation-guide/newfeat/a.md'], [], 1),
    ('plan-shaped file in a legacy feature dir', ['documentation/implementation-guide/legacy/PLAN-2.md'], [], 1),
    ('numbered file in a legacy feature dir', ['documentation/implementation-guide/legacy/03-step.md'], [], 1),
    ('plain file in a legacy feature dir', ['documentation/implementation-guide/legacy/notes-on-it.md'], [], 0),
    ('multi-agent-concurrency is exempt', ['documentation/implementation-guide/multi-agent-concurrency/PLAN.md'], [], 0),
    ('a runbook', ['documentation/runbooks/new-runbook.md'], [], 0),
    ('a plane README edit-sized addition', ['frontend/NOTES-ON-THIS.md'], [], 0),
    ('nothing staged', [], [], 0),
]


class Twin(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix='docplace-')
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'config', 'user.name', 'test')
        git(self.repo, 'config', 'core.autocrlf', 'false')
        for rel in ('documentation/implementation-guide/README.md',
                    'documentation/implementation-guide/legacy/PLAN.md',
                    'documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md',
                    'keep/doc.md', 'README.md'):
            self.write(rel, 'seed ' + rel + '\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'seed')

    def tearDown(self):
        def unlock(func, path, _exc):  # git writes its objects read-only; Windows refuses those
            os.chmod(path, 0o700)
            func(path)
        shutil.rmtree(self.repo, onerror=unlock)

    def write(self, rel, text):
        full = os.path.join(self.repo, *rel.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)

    def run_gate(self, cmd, env_extra=None):
        env = dict(os.environ)
        env.pop('AI_STACK_PLAN_IN_CODE_REPO', None)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(cmd, cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=env, check=False)
        return proc.returncode, proc.stdout.decode('utf-8', 'replace')

    def both(self, env_extra=None):
        py = self.run_gate([sys.executable, PY_GATE], env_extra)
        ps = self.run_gate(PS_HOST + [PS_GATE], env_extra) if PS_HOST else None
        return py, ps

    def stage(self, new, renames):
        for rel in new:
            self.write(rel, 'content of ' + rel + '\n')
            git(self.repo, 'add', '--', rel)
        for src, dst in renames:
            os.makedirs(os.path.dirname(os.path.join(self.repo, *dst.split('/'))), exist_ok=True)
            git(self.repo, 'mv', src, dst)

    def test_cases(self):
        for name, new, renames, want in CASES:
            with self.subTest(case=name):
                git(self.repo, 'reset', '-q', '--hard', 'HEAD')
                git(self.repo, 'clean', '-qfdx')
                self.stage(new, renames)
                (py_rc, py_out), ps = self.both()
                self.assertEqual(py_rc, want, f'python twin, {name}:\n{py_out}')
                if ps is not None:
                    self.assertEqual(ps[0], py_rc, f'the .ps1 and the twin disagree on {name}:\n{ps[1]}')

    def test_refusal_names_the_store_path(self):
        self.stage(['documentation/notes/x.md'], [])
        (rc, out), _ = self.both()
        self.assertEqual(rc, 1)
        self.assertIn('documentation/notes/x.md', out)
        self.assertIn('../documentation-plans-ai-stack/journal/notes/x.md', out)
        self.assertIn('1 of 1 staged path(s)', out)

    def test_escape_hatch_warns_and_passes(self):
        self.stage(['documentation/notes/x.md'], [])
        (rc, out), ps = self.both({'AI_STACK_PLAN_IN_CODE_REPO': '1'})
        self.assertEqual(rc, 0)
        self.assertIn('WARNING', out)
        if ps is not None:
            self.assertEqual(ps[0], 0)

    def test_success_says_what_it_examined(self):
        self.stage(['documentation/runbooks/a.md', 'b.md'], [])
        (rc, out), _ = self.both()
        self.assertEqual(rc, 0)
        self.assertIn('2 staged addition(s)/rename(s) examined', out)

    def test_a_failing_git_refuses(self):
        # git pointed at no repository: the twin must refuse, not read "nothing staged".
        rc, out = self.run_gate([sys.executable, PY_GATE],
                                {'GIT_DIR': os.path.join(self.repo, 'no-such-git-dir')})
        self.assertEqual(rc, 1, out)
        self.assertIn('FAILED', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
