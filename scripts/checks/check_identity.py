#!/usr/bin/env python3
"""check_identity.py - refuse personal identifiers in what this public repo tracks.

THE PROBLEM. This repository is public, and for most of its life it was written on one
host by one operator. Their LAN and tailnet addresses, their Windows user name, the
directory the checkout lives in, their tailnet's hostname, their domain and their email
got into config, code and prose as ordinary literals. Nothing flagged them, because a
value that is correct on the host that wrote it looks like any other value.

TWO LAYERS, and the second is the reason this file is shaped the way it is:

  GENERIC (tracked, in this file). Patterns that recognise personal data WITHOUT naming
  anyone: private IPv4 (RFC 1918), CGNAT/tailnet IPv4 (100.64.0.0/10) and Tailscale's
  IPv6 prefix, `*.ts.net` tailnet host names, Windows/macOS user-profile paths
  (C:\\Users\\<name>, /c/Users/<name>, /Users/<name>), drive-rooted paths into this
  stack's checkout directory, and email addresses. Each has a small set of forms that are
  generic on purpose (example.com, noreply, <name>, 10.0.0.0/8 ...), written below.

  OPERATOR (not tracked, anywhere). A domain, an email, a GitHub or git user name, a
  tailnet or machine name matches no generic shape - "acme-corp" is a word. The only way
  to recognise one is to know it, and writing it into a tracked denylist would publish
  the very list of values the gate exists to keep out. So the gate reads those literals
  from a LOCAL file that git never tracks (see DENYLIST below) and never prints one:
  a hit says "operator (denylist entry 3)", the file and the line, nothing of the value.

  Why not salted hashes in a tracked file instead? Because the entries are short and
  low-entropy (a user name, a domain): anyone holding the salt - which must be tracked
  for the gate to work - can confirm a guess in microseconds, so a hashed list is a
  published list with one extra step. And a hash can only test a WHOLE token, so the gate
  would have to guess token boundaries in every file. A local plaintext file has neither
  problem, and the one thing it costs - a fresh clone has no operator layer - is correct:
  a newcomer has no operator identity to protect, and CI runs the generic layer.

DENYLIST - first found wins:
  1. --denylist PATH, or the environment variable AI_STACK_IDENTITY_DENYLIST
  2. <worktree root>/.identity-denylist           (gitignored)
  3. <git common dir>/identity-denylist            (e.g. .git/identity-denylist)
The third is the recommended place: it lives INSIDE the git directory, so it can never be
staged, and every `git worktree` of the repository shares it (a gitignored file in the
main checkout is not copied into a new worktree). Format: `.identity-denylist.example`.

ALLOWLIST - scripts/checks/identity-allowlist.txt, tracked. One entry per line:
    <path glob> | <class>[~<context regex>] | <reason>
`**` in the glob crosses directories, `*` does not. A reason is required. With a context
regex, a finding is allowed only if it lies INSIDE a match of that regex on its line, so
an entry can allow "the owner segment of a github.com URL to this project's own repos"
without allowing the same value anywhere else on the line - and without spelling it. The
allowlist is itself a tracked file this gate scans, so an entry that spelled a guarded
value would be refused like any other line. Entries that match nothing are listed under
--all as STALE (a warning, not a failure: with no denylist, as in CI, an `operator` entry
cannot match anything).

MODES
  (default)   staged: only lines the index ADDS relative to HEAD are judged - an old line
              you did not touch never blocks your commit. Renames are read as additions
              (--no-renames), so moving a file into a non-allowlisted path re-judges it.
  --all       every tracked blob in the index (gitlinks - the OB1 submodule - excluded;
              run it inside OB1 with --no-allowlist to audit that repository).
  --summary   with --all: counts by class and by path instead of one line per finding.
  --report    exit 0 even with findings (for audits of a tree this gate does not guard).

Standard library only; Python 3.8+. Exit 0 = clean, 1 = findings (or git could not answer,
which is never read as "nothing to check"), 2 = a malformed allowlist/denylist or usage.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

ALLOWLIST_DEFAULT = 'scripts/checks/identity-allowlist.txt'
DENYLIST_ENV = 'AI_STACK_IDENTITY_DENYLIST'
MIN_LITERAL = 4          # a shorter denylist entry would match inside ordinary words
OPERATOR = 'operator'

# ---------------------------------------------------------------------------------------
# GENERIC LAYER
# ---------------------------------------------------------------------------------------
# Each class: a compiled regex plus a predicate that says whether a match is one of the
# deliberately generic forms. Group 'v' (if present) is the part judged and reported.

_IPV4 = re.compile(r'(?<![\w.])(?P<v>(?:\d{1,3}\.){3}\d{1,3})(?P<cidr>/\d{1,2})?(?![\w]|\.\d)')

# Addresses that appear in docs and config as ARCHITECTURE, not as anyone's host: the
# network address of a range, a CIDR block, and Docker's own default bridge/gateway.
_GENERIC_IPV4 = {'172.17.0.1', '172.18.0.1', '10.0.0.1', '192.168.0.1', '192.168.1.1',
                 '100.100.100.100'}      # Tailscale's MagicDNS resolver, the same everywhere
_CGNAT = ipaddress.ip_network('100.64.0.0/10')
_RFC1918 = (ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'),
            ipaddress.ip_network('192.168.0.0/16'))


def _ipv4_class(m: re.Match) -> str | None:
    """'lan-ip' / 'tailnet-ip' for a personal-looking address, else None."""
    try:
        ip = ipaddress.ip_address(m.group('v'))
    except ValueError:
        return None
    if m.group('cidr') or str(ip) in _GENERIC_IPV4 or str(ip).endswith('.0') or str(ip).endswith('.255'):
        return None
    if ip in _CGNAT:
        return 'tailnet-ip'
    if any(ip in n for n in _RFC1918):
        return 'lan-ip'
    return None


# Tailscale's IPv6 ULA prefix, fd7a:115c:a1e0::/48 - every node address in every tailnet.
_TS_IPV6 = re.compile(r'(?i)(?<![0-9a-f:])(?P<v>fd7a:115c:a1e0(?::[0-9a-f]{0,4}){1,5})(?![0-9a-f:])')


def _ts_ipv6_class(m: re.Match) -> str | None:
    v = m.group('v').lower().rstrip(':')
    # the bare prefix (docs describing the range) is generic; a node address is not
    return None if v in ('fd7a:115c:a1e0', 'fd7a:115c:a1e0:') else 'tailnet-ip'


_TSNET = re.compile(r'(?i)(?P<v>(?:[a-z0-9<>{}$_*-]+\.)+ts\.net)\b')
# placeholder tailnet labels a document may use to SHOW the shape of a name
_TSNET_PLACEHOLDER = re.compile(
    r'(?i)^(?:\*|<[^>]*>|\{[^}]*\}|\$\{?\w+\}?|example|tailnet|your-?tailnet|tailnet-?name|'
    r'tail[x0]{4,}|x{3,}|foo|bar|machine|host(?:name)?|node|device)$')


def _tsnet_class(m: re.Match) -> str | None:
    labels = m.group('v').lower().split('.')[:-2]          # drop 'ts', 'net'
    if not labels:
        return None
    # the TAILNET label is the one directly before ts.net; a machine label before that
    return None if _TSNET_PLACEHOLDER.match(labels[-1]) else 'tailnet-host'


# C:\Users\<name>, C:/Users/<name>, /c/Users/<name>, /mnt/c/Users/<name>, /Users/<name>
_USERPATH = re.compile(
    r'(?i)(?:\b[a-z]:[\\/]+|(?<![\w.~-])/mnt/[a-z]/|(?<![\w.~-])/[a-z]/|(?<![\w.~/-])/)'
    r'(?:users|documents and settings)[\\/]+(?P<v>[^\\/\s"\'`<>|:;,*?]+)')
_GENERIC_USERS = {
    'public', 'default', 'default user', 'all users', 'defaultapppool', 'shared',
    'user', 'username', 'user-name', 'user_name', 'yourname', 'your-name', 'your_name',
    'you', 'me', 'name', 'someone', 'somebody', 'example', 'runner', 'runneradmin',
    'alice', 'bob', 'jdoe', 'john', 'jane', 'operator', 'admin', 'administrator', 'x',
    '...', '..', '.', '*', 'foo', 'bar', 'dev', 'developer', 'me.example',
}


def _userpath_class(m: re.Match) -> str | None:
    name = m.group('v').strip().rstrip('.').lower()
    if not name or name in _GENERIC_USERS:
        return None
    # a variable standing in for the name: %USERNAME%, $env:USERNAME, ${USER}, $HOME, {user}
    if name[0] in '%${[(' or name.startswith('<'):
        return None
    return 'user-profile-path'


# A drive-rooted path into THIS stack's checkout directory. The checkout's location is the
# operator's filesystem layout, not the project's: a newcomer clones it anywhere. Written
# as the directory's name only, the pattern names no person.
_DRIVEPATH = re.compile(r'(?i)(?<![\w])(?P<v>[a-z]:[\\/]{1,2}open[ _-]?webui)(?=[\\/"\'`\s)\]]|$)')


def _drivepath_class(m: re.Match) -> str | None:
    return 'drive-host-path'


_EMAIL = re.compile(r'(?i)(?<![\w.%+-])(?P<v>[a-z0-9][a-z0-9._%+-]*@(?P<dom>[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}))\b')
_GENERIC_EMAIL_DOMAINS = re.compile(
    r'(?i)^(?:(?:[\w-]+\.)*(?:example\.(?:com|org|net)|example|invalid|test|localhost|local|'
    r'internal|lan|home\.arpa|localdomain)|(?:users\.)?noreply\.github\.com|'
    r'anthropic\.com|github\.com|gitlab\.com)$')
_GENERIC_EMAIL_LOCAL = re.compile(r'(?i)^(?:no-?reply|git|you|user|me|someone|example|admin|root|'
                                  r'postmaster|security|abuse|support|hello|info|noone|nobody)$')


def _email_class(m: re.Match) -> str | None:
    local, dom = m.group('v').rsplit('@', 1)
    if _GENERIC_EMAIL_DOMAINS.match(dom):
        return None
    # file names such as `foo@2x.png` and package pins such as `pkg@1.2.3` are not email:
    # their "domain" ends in a file extension or a version
    if re.search(r'(?i)\.(?:png|jpe?g|gif|svg|webp|js|mjs|ts|css|json|md|txt|py|sh)$', dom):
        return None
    if _GENERIC_EMAIL_LOCAL.match(local) and dom.lower().endswith(('.example', '.invalid')):
        return None
    return 'email'


GENERIC = (
    ('ipv4', _IPV4, _ipv4_class),
    ('tailnet-ipv6', _TS_IPV6, _ts_ipv6_class),
    ('tailnet-host', _TSNET, _tsnet_class),
    ('user-profile-path', _USERPATH, _userpath_class),
    ('drive-host-path', _DRIVEPATH, _drivepath_class),
    ('email', _EMAIL, _email_class),
)
CLASSES = ('lan-ip', 'tailnet-ip', 'tailnet-host', 'user-profile-path', 'drive-host-path',
           'email', OPERATOR)


# ---------------------------------------------------------------------------------------
# git plumbing - a failed query RAISES; it is never an empty answer
# ---------------------------------------------------------------------------------------
class GitFailed(Exception):
    pass


def _git(*args: str, cwd: str | None = None, inp: bytes | None = None) -> bytes:
    proc = subprocess.run(('git',) + args, cwd=cwd, input=inp, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise GitFailed(f"'git {' '.join(args[:4])}' exited {proc.returncode}"
                        + (f': {err[0]}' if err else ''))
    return proc.stdout


def _z(out: bytes) -> list[str]:
    return [p.decode('utf-8', 'surrogateescape') for p in out.split(b'\0') if p]


# ---------------------------------------------------------------------------------------
# allowlist and denylist
# ---------------------------------------------------------------------------------------
class ConfigError(Exception):
    pass


def glob_to_regex(glob: str) -> re.Pattern:
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith('**/', i):
            out.append('(?:.*/)?')
            i += 3
            continue
        if glob.startswith('**', i):
            out.append('.*')
            i += 2
            continue
        out.append('[^/]*' if c == '*' else '[^/]' if c == '?' else re.escape(c))
        i += 1
    return re.compile('^' + ''.join(out) + '$')


class AllowEntry:
    def __init__(self, lineno: int, glob: str, cls: str, rx: str | None, reason: str):
        self.lineno, self.glob, self.cls, self.reason = lineno, glob, cls, reason
        self.path_rx = glob_to_regex(glob)
        self.text_rx = re.compile(rx) if rx else None
        self.used = 0

    def covers(self, f: 'Finding') -> bool:
        if f.cls != self.cls or not self.path_rx.match(f.path):
            return False
        if self.text_rx is None:
            return True
        end = f.col - 1 + len(f.text)
        return any(m.start() <= f.col - 1 and end <= m.end() for m in self.text_rx.finditer(f.context))


def load_allowlist(text: str, source: str) -> list[AllowEntry]:
    entries = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(' | ')]
        if len(parts) != 3 or not all(parts):
            raise ConfigError(f'{source}:{n}: expected `<path glob> | <class>[~<regex>] | <reason>`')
        glob, clsrx, reason = parts
        cls, _, rx = clsrx.partition('~')
        cls = cls.strip()
        if cls not in CLASSES:
            raise ConfigError(f'{source}:{n}: unknown class {cls!r} (one of {", ".join(CLASSES)})')
        if len(reason) < 10:
            raise ConfigError(f'{source}:{n}: the reason must say why (10+ characters)')
        try:
            entries.append(AllowEntry(n, glob, cls, rx.strip() or None, reason))
        except re.error as e:
            raise ConfigError(f'{source}:{n}: bad regex: {e}')
    return entries


def load_denylist(text: str, source: str) -> list[tuple[int, str, str]]:
    """[(entry number, lowercased literal, label)]. Numbers count entries, not lines."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        lit, sep, label = line.partition(' #')
        lit = lit.strip()
        if len(lit) < MIN_LITERAL:
            # do not echo the entry: it may be the start of a real value
            raise ConfigError(f'{source}:{n}: an entry shorter than {MIN_LITERAL} characters would match '
                              f'inside ordinary words - lengthen it or remove it')
        out.append((len(out) + 1, lit.lower(), label.strip()))
    return out


def find_denylist(explicit: str | None, top: str, common: str) -> str | None:
    cands = []
    if explicit:
        return explicit
    if os.environ.get(DENYLIST_ENV):
        return os.environ[DENYLIST_ENV]
    cands.append(os.path.join(top, '.identity-denylist'))
    cands.append(os.path.join(common, 'identity-denylist'))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


# ---------------------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------------------
class Finding:
    __slots__ = ('path', 'line', 'col', 'cls', 'text', 'detail', 'context')

    def __init__(self, path, line, col, cls, text, detail='', context=''):
        self.path, self.line, self.col, self.cls, self.text = path, line, col, cls, text
        self.detail, self.context = detail, context


def scan_line(path: str, lineno: int, line: str, deny) -> list[Finding]:
    found = []
    for _name, rx, judge in GENERIC:
        for m in rx.finditer(line):
            cls = judge(m)
            if cls:
                v = m.group('v')
                found.append(Finding(path, lineno, m.start('v') + 1, cls, v, context=line))
    if deny:
        low = line.lower()
        for num, lit, label in deny:
            start = low.find(lit)
            while start != -1:
                found.append(Finding(path, lineno, start + 1, OPERATOR, line[start:start + len(lit)],
                                     f'denylist entry {num}' + (f' [{label}]' if label else ''), line))
                start = low.find(lit, start + 1)
    return found


def mask(f: Finding) -> str:
    """What a finding prints of the matched text. NOTHING of an operator literal; for a
    generic match only its shape, because a generic email may be the operator's own."""
    if f.cls == OPERATOR:
        return f.detail
    return f'{len(f.text)} chars'


def scan_text(path: str, text: str, deny, only_lines: set[int] | None = None) -> list[Finding]:
    found = []
    for i, line in enumerate(text.split('\n'), 1):
        if only_lines is not None and i not in only_lines:
            continue
        found.extend(scan_line(path, i, line.rstrip('\r'), deny))
    return found


_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def added_lines(diff: str) -> list[tuple[int, str]] | None:
    """[(new line number, text)] of the '+' lines of a -U0 diff; None for a binary."""
    out, n, in_hunk = [], 0, False
    for line in diff.split('\n'):
        if not in_hunk:
            if line.startswith('Binary files ') or line.startswith('GIT binary patch'):
                return None
        m = _HUNK.match(line)
        if m:
            in_hunk, n = True, int(m.group(1))
            continue
        if not in_hunk:
            continue
        if line.startswith('+'):
            out.append((n, line[1:].rstrip('\r')))
            n += 1
        elif line.startswith(' '):
            n += 1
    return out


def staged_findings(deny) -> tuple[list[Finding], int, int]:
    gitlinks = {p for rec in _z(_git('ls-files', '--stage', '-z'))
                for meta, _, p in [rec.partition('\t')] if meta.startswith('160000 ')}
    files = [p for p in _z(_git('diff', '--cached', '--name-only', '-z', '--no-renames', '--diff-filter=ACMRT'))
             if p not in gitlinks]
    found, skipped = [], 0
    for f in files:
        diff = _git('-c', 'core.quotePath=false', 'diff', '--cached', '-U0', '--no-color', '--no-ext-diff',
                    '--no-textconv', '--no-renames', '--', f).decode('utf-8', 'surrogateescape')
        added = added_lines(diff)
        if added is None:
            skipped += 1
            continue
        for n, text in added:
            found.extend(scan_line(f, n, text, deny))
    return found, len(files), skipped


def all_findings(deny) -> tuple[list[Finding], int, int]:
    recs = []
    for rec in _z(_git('ls-files', '--stage', '-z')):
        meta, _, p = rec.partition('\t')
        mode, sha, _stage = meta.split(' ')
        if mode == '160000':
            continue
        recs.append((p, sha))
    if not recs:
        return [], 0, 0
    out = _git('cat-file', '--batch', inp=''.join(sha + '\n' for _, sha in recs).encode())
    found, skipped, pos = [], 0, 0
    for path, _sha in recs:
        nl = out.index(b'\n', pos)
        header = out[pos:nl].split()
        if len(header) < 3 or header[1] != b'blob':
            raise GitFailed(f'cat-file could not read the blob of {path}')
        size = int(header[2])
        blob = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
        if b'\0' in blob:
            skipped += 1
            continue
        found.extend(scan_text(path, blob.decode('utf-8', 'surrogateescape'), deny))
    return found, len(recs), skipped


# ---------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--all', action='store_true', help='audit every tracked file, not the staged additions')
    ap.add_argument('--summary', action='store_true', help='counts by class and path (with --all)')
    ap.add_argument('--report', action='store_true', help='exit 0 even with findings (audit only)')
    ap.add_argument('--denylist', help=f'operator literal file (else ${DENYLIST_ENV}, then the defaults)')
    ap.add_argument('--no-denylist', action='store_true', help='generic layer only, even if a denylist exists')
    ap.add_argument('--allowlist', help=f'allowlist file (default: <repo>/{ALLOWLIST_DEFAULT})')
    ap.add_argument('--no-allowlist', action='store_true', help='judge everything (e.g. inside OB1)')
    a = ap.parse_args(argv)
    try:
        return _main(a)
    except GitFailed as e:
        print(f'  [identity] FAIL - {e}, so this gate cannot tell what it would check.')
        print("  A failed query is not 'nothing to check'. Fix git's access to this repository, then retry.")
        return 1
    except ConfigError as e:
        print(f'  [identity] CONFIG ERROR - {e}')
        return 2


def _main(a) -> int:
    top = _git('rev-parse', '--show-toplevel').decode().strip()
    common = _git('rev-parse', '--git-common-dir').decode().strip()
    common = os.path.abspath(common)
    os.chdir(top)

    allow = []
    if not a.no_allowlist:
        apath = a.allowlist or os.path.join(top, ALLOWLIST_DEFAULT)
        if os.path.isfile(apath):
            with open(apath, encoding='utf-8') as fh:
                allow = load_allowlist(fh.read(), os.path.relpath(apath, top).replace(os.sep, '/'))
        elif a.allowlist:
            raise ConfigError(f'allowlist {a.allowlist} not found')

    deny, deny_src = [], None
    if not a.no_denylist:
        deny_src = find_denylist(a.denylist, top, common)
        if deny_src:
            try:
                with open(deny_src, encoding='utf-8') as fh:
                    deny = load_denylist(fh.read(), deny_src)
            except OSError as e:
                raise ConfigError(f'denylist {deny_src} unreadable: {e.strerror}')
    layer = (f'generic + operator ({len(deny)} denylist entries from {deny_src})' if deny
             else 'generic only (no operator denylist on this machine - see .identity-denylist.example)')

    if a.all:
        found, scanned, binary = all_findings(deny)
        what = f'{scanned} tracked file(s), {binary} binary skipped'
    else:
        found, scanned, binary = staged_findings(deny)
        what = f'the added lines of {scanned} staged file(s), {binary} binary skipped'

    kept = []
    for f in found:
        entry = next((e for e in allow if e.covers(f)), None)
        if entry:
            entry.used += 1
        else:
            kept.append(f)

    allowed = len(found) - len(kept)
    if a.summary:
        by_cls = Counter(f.cls for f in kept)
        by_path = defaultdict(Counter)
        for f in kept:
            by_path[f.path][f.cls] += 1
        print(f'  [identity] {len(kept)} finding(s) outside the allowlist in {what}; layer: {layer}')
        for c in CLASSES:
            if by_cls[c]:
                print(f'    {c:18} {by_cls[c]}')
        for p in sorted(by_path):
            print(f'    {p}: ' + ', '.join(f'{c} {n}' for c, n in sorted(by_path[p].items())))
    elif kept:
        print('')
        print('=========================================================')
        print(' COMMIT BLOCKED - personal identifier in ' + ('the tracked tree' if a.all else 'staged additions'))
        print('=========================================================')
        for f in sorted(kept, key=lambda f: (f.path, f.line, f.col)):
            print(f'  {f.path}:{f.line}:{f.col}: {f.cls} ({mask(f)})')
        print('')
        print(' Fix: parameterise it (a variable in the owning plane\'s .env, a generic value in its')
        print(' .env.example), write the prose generically (<user>, example.com, 192.0.2.10), or - if')
        print(' it must stay - add `<path glob> | <class> | <reason>` to ' + ALLOWLIST_DEFAULT + '.')
        print('')
    if a.all:
        stale = [e for e in allow if not e.used and not (e.cls == OPERATOR and not deny)]
        for e in stale:
            print(f'  [identity] STALE allowlist entry line {e.lineno}: {e.glob} | {e.cls} - matched nothing')
    verdict = 'clean' if not kept else f'{len(kept)} finding(s)'
    print(f'  [identity] {verdict} - scanned {what}; {allowed} allowlisted; layer: {layer}')
    if kept and not a.report:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
