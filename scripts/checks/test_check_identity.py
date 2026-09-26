#!/usr/bin/env python3
"""Tests for check_identity.py - stdlib only; run with pytest OR `python3 test_check_identity.py`.

Every planted identifier below is SYNTHETIC and is ASSEMBLED AT RUN TIME from fragments, so
this file itself carries no literal the gate would refuse: `check_identity.py --all` scans
it like any other tracked file, and it must stay clean without an allowlist entry. The
operator-layer cases use a made-up literal written into a throwaway denylist - never a real
one - and assert that the refusal does not print it.

Each case builds a scratch git repository, stages content and runs the real CLI in a
subprocess, the way .githooks/pre-commit runs it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, 'check_identity.py')
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import check_identity as ci  # noqa: E402

BS = chr(92)
# --- synthetic look-alikes, assembled so no whole literal sits in this file ------------
LAN_IP = '192.' + '168.' + '40.17'
LAN_IP_10 = '10.' + '44.' + '3.9'
TAILNET_IP = '100.' + '101.' + '7.8'
TS_V6 = 'fd7a:' + '115c:a1e0:' + 'ab12:3::9'
TSNET = 'laptop-q.' + 'tail9zz' + 'q01.ts' + '.net'
WIN_USER_PATH = 'C:' + BS + 'Users' + BS + 'zqx' + 'user' + BS + 'models'
GITBASH_USER_PATH = '/c/' + 'Users/' + 'zqx' + 'user/src'
DRIVE_PATH = 'E:' + BS + 'Open ' + 'WebUI' + BS + 'ai-stack'
EMAIL = 'zqx.' + 'person' + '@' + 'mailhost' + '.io'
OPERATOR_LITERAL = 'qz' + 'vorn' + 'ack'          # the made-up "operator" value
OPERATOR_IN_URL = 'https://github.com/' + 'Qz' + 'Vorn' + 'Ack' + '/thing.git'

GENERIC_OK = [
    'contact: you@example.com, noreply@github.com, ci@example.invalid',
    'trusted_proxies 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16',
    'docker gateway 172.17.0.1 and resolver 100.100.100.100',
    'path C:' + BS + 'Users' + BS + '<name>' + BS + '.wslconfig or %USERPROFILE%',
    'serve at https://openwebui.<tailnet>.ts.net and *.ts.net',
    'image logo@2x.png and pkg@1.2.3',
    'section 10.3.1 of the design',
    'subnets 10.44.0.0/16 and ' + '192.' + '168.40.0/24 (host bits zero)',
    'cloud metadata at 169.' + '254.169.254',
    'bot 41898282+github-actions[bot]@users.noreply.github.com',
    'a checkout at /d/<dir>/ai-stack or /mnt/d/<dir>',
]

# attempt-2 boundaries (tester evidence, attempt 1): each of these PASSED the first gate
BOUNDARY_REFUSED = [
    ('link-local-ip', 'LMSTUDIO_HOST=' + '169.' + '254.' + '83.9'),
    ('lan-ip', 'Address = ' + LAN_IP_10 + '/32'),
    ('lan-ip', 'host ' + LAN_IP + '/24'),
    ('lan-ip', 'backup_' + LAN_IP + '.log'),
    ('lan-ip', LAN_IP + '_snapshot'),
    ('drive-host-path', 'cd "/d/' + 'Open ' + 'WebUI/ai-stack"'),
    ('drive-host-path', 'cd /mnt/e/' + 'open-' + 'webui/ai-stack'),
    ('email', 'author 1234567+' + 'zqxperson' + '@users.noreply.github.com'),
    ('email', 'author ' + 'zqxperson' + '@users.noreply.github.com'),
]


def git(cwd, *args, check=True):
    return subprocess.run(('git',) + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=check)


class ScratchRepo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='idgate-')
        git(self.dir, 'init', '-q')
        git(self.dir, 'config', 'user.email', 't@example.invalid')
        git(self.dir, 'config', 'user.name', 't')
        git(self.dir, 'config', 'core.autocrlf', 'false')
        self.write('README.md', 'scratch\n')
        git(self.dir, 'add', '-A')
        git(self.dir, 'commit', '-q', '-m', 'init')
        self.env = {k: v for k, v in os.environ.items() if k != ci.DENYLIST_ENV}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rel, text, mode='w'):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode, **({} if 'b' in mode else {'encoding': 'utf-8', 'newline': ''})) as fh:
            fh.write(text)

    def stage(self, rel, text):
        self.write(rel, text)
        git(self.dir, 'add', '--', rel)

    def run_gate(self, *args, allowlist=None):
        extra = ['--allowlist', allowlist] if allowlist else ['--no-allowlist']
        p = subprocess.run([sys.executable, GATE, *extra, *args], cwd=self.dir, env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode('utf-8', 'replace')

    def allow(self, text):
        p = os.path.join(self.dir, '.allow-under-test')     # untracked: never scanned
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return p

    def denylist_in_git_dir(self, text):
        common = git(self.dir, 'rev-parse', '--git-common-dir').stdout.decode().strip()
        with open(os.path.join(self.dir, common, 'identity-denylist'), 'w', encoding='utf-8') as fh:
            fh.write(text)


class GenericLayerRefuses(ScratchRepo):
    CASES = [
        ('lan-ip', 'host = "' + LAN_IP + '"\n'),
        ('lan-ip', 'VPN_ADDR=' + LAN_IP_10 + '/32 peer ' + LAN_IP_10 + '\n'),
        ('tailnet-ip', 'ssh ' + TAILNET_IP + '\n'),
        ('tailnet-ip', 'addr ' + TS_V6 + '\n'),
        ('tailnet-host', 'url: https://' + TSNET + ':8443/\n'),
        ('user-profile-path', 'models: ' + WIN_USER_PATH + '\n'),
        ('user-profile-path', 'cd ' + GITBASH_USER_PATH + '\n'),
        ('drive-host-path', 'cd "' + DRIVE_PATH + '"\n'),
        ('email', 'owner: ' + EMAIL + '\n'),
    ]

    def test_each_class_is_refused_with_file_line_and_class(self):
        for cls, line in self.CASES:
            with self.subTest(cls=cls, line=line):
                self.stage('docs/note.md', 'intro\n\n' + line)
                rc, out = self.run_gate()
                self.assertEqual(rc, 1, out)
                self.assertIn('docs/note.md:3:', out)
                self.assertIn(': ' + cls + ' (', out)
                git(self.dir, 'reset', '-q', '--', 'docs/note.md')

    def test_a_generic_match_is_not_echoed(self):
        self.stage('a.txt', 'mail ' + EMAIL + ' at ' + LAN_IP + '\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertNotIn(EMAIL, out)
        self.assertNotIn(LAN_IP, out)

    def test_generic_forms_pass(self):
        self.stage('docs/ok.md', '\n'.join(GENERIC_OK) + '\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        self.assertIn('clean', out)

    def test_crlf_line_is_judged(self):
        self.stage('w.ps1', 'x\r\n$ip = "' + LAN_IP + '"\r\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 1, out)
        self.assertIn('w.ps1:2:', out)


class Attempt2Boundaries(ScratchRepo):
    def test_each_boundary_shape_is_refused(self):
        for cls, line in BOUNDARY_REFUSED:
            with self.subTest(cls=cls, line=line):
                self.stage('docs/b.md', 'x\n' + line + '\n')
                rc, out = self.run_gate()
                self.assertEqual(rc, 1, out)
                self.assertIn('docs/b.md:2:', out)
                self.assertIn(': ' + cls + ' (', out)
                git(self.dir, 'reset', '-q', '--', 'docs/b.md')

    def test_utf16_text_is_scanned_staged_and_all(self):
        body = ('first\r\n$h = "' + LAN_IP + '"\r\n').encode('utf-16')     # BOM + UTF-16LE
        self.write('ps/out.ps1', body, mode='wb')
        git(self.dir, 'add', 'ps/out.ps1')
        rc, out = self.run_gate()
        self.assertEqual(rc, 1, out)
        self.assertIn('ps/out.ps1:2:', out)
        self.assertIn('0 binary skipped', out)
        git(self.dir, 'commit', '-q', '--no-verify', '-m', 'utf16')
        rc, out = self.run_gate('--all')
        self.assertEqual(rc, 1, out)
        self.assertIn('ps/out.ps1:2:', out)

    def test_utf16_edit_judges_only_new_lines(self):
        old = ('keep ' + LAN_IP + '\r\n').encode('utf-16')
        self.write('ps/o.ps1', old, mode='wb')
        git(self.dir, 'add', 'ps/o.ps1')
        git(self.dir, 'commit', '-q', '--no-verify', '-m', 'legacy utf16')
        self.write('ps/o.ps1', ('keep ' + LAN_IP + '\r\nclean line\r\n').encode('utf-16'), mode='wb')
        git(self.dir, 'add', 'ps/o.ps1')
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)

    def test_a_nul_binary_is_still_skipped(self):
        self.write('b.dat', b'\x00\x00' + LAN_IP.encode(), mode='wb')
        git(self.dir, 'add', 'b.dat')
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        self.assertIn('1 binary skipped', out)


class StagedModeJudgesAdditionsOnly(ScratchRepo):
    def test_an_old_line_you_did_not_touch_does_not_block(self):
        self.stage('cfg.txt', 'a ' + LAN_IP + '\n')
        git(self.dir, 'commit', '-q', '--no-verify', '-m', 'legacy')
        self.stage('cfg.txt', 'a ' + LAN_IP + '\nb clean\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        rc, out = self.run_gate('--all')
        self.assertEqual(rc, 1, out)
        self.assertIn('cfg.txt:1:', out)

    def test_a_rename_is_read_as_an_addition(self):
        self.stage('keep/x.md', 'ip ' + LAN_IP + '\n')
        git(self.dir, 'commit', '-q', '--no-verify', '-m', 'allowed place')
        git(self.dir, 'mv', 'keep/x.md', 'moved.md')
        rc, out = self.run_gate(allowlist=self.allow('keep/** | lan-ip | test: allowed only under keep/\n'))
        self.assertEqual(rc, 1, out)
        self.assertIn('moved.md:1:', out)

    def test_a_binary_is_skipped_and_counted(self):
        self.write('b.bin', b'\x00\x01' + LAN_IP.encode() + b'\x00', mode='wb')
        git(self.dir, 'add', 'b.bin')
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        self.assertIn('1 binary skipped', out)

    def test_nothing_staged_is_clean_and_says_what_it_scanned(self):
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        self.assertIn('0 staged file(s)', out)


class OperatorLayer(ScratchRepo):
    def test_no_denylist_means_generic_only(self):
        self.stage('n.md', 'by ' + OPERATOR_LITERAL + '\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        self.assertIn('generic only', out)

    def test_denylisted_literal_refused_in_any_case_and_never_printed(self):
        self.denylist_in_git_dir('# local\n' + OPERATOR_LITERAL + '   # github user\n')
        self.stage('n.md', 'clone ' + OPERATOR_IN_URL + '\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 1, out)
        self.assertIn('n.md:1:', out)
        self.assertIn('operator (denylist entry 1 [github user])', out)
        self.assertNotIn(OPERATOR_LITERAL.lower(), out.lower())

    def test_denylist_found_at_worktree_root_and_via_environment(self):
        self.write('.identity-denylist', OPERATOR_LITERAL + '\n')     # untracked, as if gitignored
        self.stage('n.md', OPERATOR_LITERAL + '\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 1, out)
        os.remove(os.path.join(self.dir, '.identity-denylist'))
        side = os.path.join(self.dir, '..', os.path.basename(self.dir) + '-deny')
        with open(side, 'w', encoding='utf-8') as fh:
            fh.write(OPERATOR_LITERAL + '\n')
        try:
            self.env[ci.DENYLIST_ENV] = side
            rc, out = self.run_gate()
            self.assertEqual(rc, 1, out)
            self.assertNotIn(OPERATOR_LITERAL, out)
        finally:
            os.remove(side)

    def test_a_short_entry_is_refused_without_echo(self):
        self.denylist_in_git_dir('zq9\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 2, out)
        self.assertNotIn('zq9', out)

    def test_no_denylist_flag_turns_the_layer_off(self):
        self.denylist_in_git_dir(OPERATOR_LITERAL + '\n')
        self.stage('n.md', OPERATOR_LITERAL + '\n')
        rc, _out = self.run_gate('--no-denylist')
        self.assertEqual(rc, 0)


class Allowlist(ScratchRepo):
    def test_a_path_entry_accepts_and_counts(self):
        self.stage('evidence/run1/t.txt', 'at ' + DRIVE_PATH + '\n')
        rc, out = self.run_gate(allowlist=self.allow('evidence/** | drive-host-path | test: committed evidence\n'))
        self.assertEqual(rc, 0, out)
        self.assertIn('1 allowlisted', out)

    def test_an_entry_for_another_class_or_path_does_not_accept(self):
        self.stage('evidence/t.txt', 'at ' + DRIVE_PATH + '\n')
        a = self.allow('evidence/** | email | test: wrong class\nother/** | drive-host-path | test: wrong path\n')
        rc, out = self.run_gate(allowlist=a)
        self.assertEqual(rc, 1, out)

    def test_single_star_does_not_cross_directories(self):
        self.stage('evidence/deep/t.txt', 'at ' + DRIVE_PATH + '\n')
        rc, _ = self.run_gate(allowlist=self.allow('evidence/* | drive-host-path | test: one level only\n'))
        self.assertEqual(rc, 1)

    def test_context_regex_allows_only_inside_its_match(self):
        denied = OPERATOR_LITERAL
        self.denylist_in_git_dir(denied + '\n')
        a = self.allow(r'README.md | operator~https://github\.com/[^/\s]+/thing\.git | test: own clone URL' + '\n')
        self.stage('README.md', 'git clone ' + OPERATOR_IN_URL + '\n')
        rc, out = self.run_gate(allowlist=a)
        self.assertEqual(rc, 0, out)
        # the same value elsewhere on the SAME line is still refused
        self.stage('README.md', 'git clone ' + OPERATOR_IN_URL + '  # ask ' + denied + '\n')
        rc, out = self.run_gate(allowlist=a)
        self.assertEqual(rc, 1, out)
        self.assertIn('README.md:1:', out)

    def test_an_allow_group_must_equal_the_finding(self):
        self.denylist_in_git_dir(OPERATOR_LITERAL + '\n' + 'zzdomainq' + 'ux' + '\n')
        a = self.allow(r'README.md | operator~https://github\.com/(?P<allow>[A-Za-z0-9][A-Za-z0-9-]{0,38})/thing\.git'
                       ' | test: one owner token' + '\n')
        self.stage('README.md', 'git clone ' + OPERATOR_IN_URL + '\n')
        rc, out = self.run_gate(allowlist=a)
        self.assertEqual(rc, 0, out)
        glued = 'https://github.com/' + OPERATOR_LITERAL + '-' + 'zzdomainq' + 'ux' + '/thing.git'
        self.stage('README.md', 'git clone ' + glued + '\n')
        rc, out = self.run_gate(allowlist=a)
        self.assertEqual(rc, 1, out)
        self.assertNotIn(OPERATOR_LITERAL, out.lower())

    def test_a_literal_inside_a_longer_literal_is_one_finding(self):
        short = OPERATOR_LITERAL[:6]
        self.denylist_in_git_dir(OPERATOR_LITERAL + '\n' + short + '\n')
        self.stage('n.md', 'by ' + OPERATOR_LITERAL + ' and ' + short + 'x\n')
        rc, out = self.run_gate()
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count('n.md:1:'), 2, out)      # the long one, and the short one alone

    def test_malformed_entries_are_config_errors(self):
        for bad in ('x.md | lan-ip\n', 'x.md | nosuchclass | a long enough reason\n', 'x.md | lan-ip | short\n',
                    'x.md | lan-ip~( | a long enough reason\n'):
            with self.subTest(bad=bad):
                rc, out = self.run_gate(allowlist=self.allow(bad))
                self.assertEqual(rc, 2, out)

    def test_stale_entries_are_reported_by_all(self):
        rc, out = self.run_gate('--all', allowlist=self.allow('nowhere/** | email | test: matches nothing at all\n'))
        self.assertEqual(rc, 0, out)
        self.assertIn('STALE allowlist entry line 1', out)


class FailsClosed(unittest.TestCase):
    def test_outside_a_repository_is_a_failure_not_a_pass(self):
        d = tempfile.mkdtemp(prefix='idgate-norepo-')
        try:
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(d))
            p = subprocess.run([sys.executable, GATE], cwd=d, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn(b'FAIL', p.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ThisRepository(unittest.TestCase):
    """The real tree and the real wiring - what CI relies on."""

    def test_the_tracked_tree_is_clean_on_the_generic_layer(self):
        p = subprocess.run([sys.executable, GATE, '--all', '--no-denylist'], cwd=REPO,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(p.returncode, 0, p.stdout.decode('utf-8', 'replace'))

    def test_the_allowlist_parses(self):
        with open(os.path.join(REPO, ci.ALLOWLIST_DEFAULT), encoding='utf-8') as fh:
            self.assertTrue(ci.load_allowlist(fh.read(), ci.ALLOWLIST_DEFAULT))

    def test_pre_commit_runs_the_gate(self):
        with open(os.path.join(REPO, '.githooks', 'pre-commit'), encoding='utf-8') as fh:
            hook = fh.read()
        self.assertIn('scripts/checks/check_identity.py', hook)
        self.assertIn('GATES_RAN="$GATES_RAN identity"', hook)

    def test_the_denylist_name_is_gitignored(self):
        p = subprocess.run(['git', 'check-ignore', '-q', '.identity-denylist'], cwd=REPO)
        self.assertEqual(p.returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
