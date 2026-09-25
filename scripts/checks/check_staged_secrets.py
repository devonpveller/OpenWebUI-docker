#!/usr/bin/env python3
"""check_staged_secrets.py - the pre-commit secret guard, for a host with no PowerShell.

TWIN OF check-staged-secrets.ps1, AND IT MUST STAY ONE. .githooks/pre-commit runs the
.ps1 wherever a PowerShell host exists (powershell.exe, else pwsh) and this file only
where none does - a Linux or macOS clone with Python and no pwsh. The secret guard is
the one gate that may never be skipped, so on such a host it is this file or a refused
commit. Change a pattern or a filename rule in one twin and you MUST change it in the
other, then re-run the side-by-side parity cases (item ac-hooks-portable's test plan)
against both.

What it mirrors, rule for rule (see the .ps1 for WHY each rule exists):
  * the file set is `git diff --cached --diff-filter=ACMRT`, minus submodule gitlinks
    (mode 160000). RENAMED paths are in it (since 2026-09-25; before that a
    `git mv x frontend/.env` passed both twins): `--name-only` gives a rename's
    DESTINATION, which is the name tested and the blob read. Copies were always in.
    TYPE CHANGES (T) too, since the same day: a tracked file turned into a symlink. A
    symlink's staged blob is its target text - what the commit publishes - so that text
    is scanned and the link's own name is tested.
  * rule 1, filenames: the LEAF is tested, case-insensitively (PowerShell -eq/-like/
    -contains are case-insensitive), allowlist first.
  * rule 2, content: the STAGED blob, one violation per (pattern, file) at its first
    match, never printing the secret; an empty blob or one containing NUL is skipped.

One deliberate difference, stated rather than hidden: paths come from git with -z, so
a non-ASCII file name is read as itself. The .ps1 reads git's C-quoted rendering of
such a name; under Windows PowerShell 5.1 that makes `git show` fail and the script
exit 1 (the commit is refused by accident), under pwsh 7 the .ps1 now asks git for
unquoted names too. This twin scans the file properly instead.

Standard library only. Exit 0 = clean, 1 = blocked.
"""
from __future__ import annotations

import re
import subprocess
import sys

# Rule 1 allowlist - templates and non-secret config that are intentionally tracked.
ALLOW_NAMES = ('.env.example', '.healthcheck.env')

# Rule 2 - high-confidence provider token formats. SAME ORDER AND TEXT as the .ps1.
# Deliberately NOT a generic KEY=<long string> rule (see the .ps1 header).
PATTERNS = (
    ('GitHub PAT (classic)', r'ghp_[A-Za-z0-9]{36}'),
    ('GitHub PAT (fine-grain)', r'github_pat_[A-Za-z0-9_]{50,}'),
    ('GitHub OAuth/refresh', r'gh[osru]_[A-Za-z0-9]{36}'),
    ('OpenAI key', r'sk-[A-Za-z0-9]{32,}'),
    ('OpenAI project key', r'sk-proj-[A-Za-z0-9_\-]{20,}'),
    ('Anthropic key', r'sk-ant-[A-Za-z0-9_\-]{20,}'),
    ('Google API key', r'AIza[0-9A-Za-z_\-]{35}'),
    ('Slack token', r'xox[baprs]-[A-Za-z0-9\-]{10,}'),
    ('AWS access key id', r'AKIA[0-9A-Z]{16}'),
    ('Telegram bot token', r'[0-9]{8,10}:AA[A-Za-z0-9_\-]{33}'),
    ('Private key block', r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    # This repo's own gateway-key format (mnemory/openbrain privacy gateways).
    ('ai-stack gateway key', r'gw-[A-Za-z0-9_\-]{30,}'),
)
COMPILED = tuple((name, re.compile(rx)) for name, rx in PATTERNS)


class GitFailed(Exception):
    pass


def _git(*args: str) -> bytes:
    """Run git; a non-zero exit RAISES. A failed query is never an empty answer
    (ac-hooks-portable2: under WSL a git that refused the repo made this guard
    print "nothing staged - skip")."""
    proc = subprocess.run(('git',) + args, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise GitFailed(f"'git {' '.join(args)}' exited {proc.returncode}"
                        + (f": {err[0]}" if err else ''))
    return proc.stdout


def _z(out: bytes) -> list[str]:
    return [p.decode('utf-8', 'surrogateescape') for p in out.split(b'\0') if p]


def _like(name: str, pattern: str) -> bool:
    """PowerShell -like for the only wildcard these rules use (`*`), case-insensitive."""
    rx = '.*'.join(re.escape(part) for part in pattern.lower().split('*'))
    return re.fullmatch(rx, name.lower(), re.DOTALL) is not None


def env_file_violation(path: str) -> bool:
    leaf = path.rsplit('/', 1)[-1]
    if leaf.lower() in ALLOW_NAMES:
        return False
    if _like(leaf, '*.env.example'):
        return False
    return (leaf.lower() == '.env' or _like(leaf, '.env.*') or _like(leaf, '.env-*')
            or _like(leaf, '*.env'))


def content_violations(path: str, blob: bytes) -> list[str]:
    # latin-1 is byte<->char and lossless, so every pattern (all ASCII) matches exactly
    # the bytes it would match in any other single-byte or UTF-8 decoding.
    text = blob.decode('latin-1')
    # The .ps1 reads `git show` as LINES and re-joins them with LF, so any CR/LF run is
    # one line break there. Normalise the same way so reported line numbers agree.
    text = re.sub(r'\r\n|\r|\n', '\n', text)
    if text.endswith('\n'):
        text = text[:-1]
    if not text:
        return []            # .ps1: `-not $content` -> skip
    if '\0' in text:
        return []            # .ps1: "Skip obvious binaries."
    found = []
    for name, rx in COMPILED:
        m = rx.search(text)
        if m:
            line = text.count('\n', 0, m.start()) + 1
            found.append(f'{name} in {path}:{line}')   # never print the secret itself
    return found


def main() -> int:
    try:
        return _main()
    except GitFailed as e:
        print(f'  [secrets] FAIL - {e}, so this guard cannot tell what is staged.')
        print("  A failed query is not 'nothing staged'. Fix git's access to this repository")
        print('  (safe.directory, GIT_DIR, the index), then commit again.')
        return 1


def _main() -> int:
    gitlinks = set()
    for rec in _z(_git('ls-files', '--stage', '-z')):
        meta, _, p = rec.partition('\t')
        if meta.startswith('160000 '):
            gitlinks.add(p)
    staged = [p for p in _z(_git('diff', '--cached', '--name-only', '-z', '--diff-filter=ACMRT'))
              if p.strip() and p not in gitlinks]

    if not staged:
        print('  [secrets] nothing staged - skip')
        return 0

    violations = []
    for f in staged:
        if env_file_violation(f):
            violations.append(f'ENV FILE STAGED: {f}  (env files hold live credentials - never commit)')
    for f in staged:
        # Read the STAGED blob, not the working file - they can differ.
        blob = _git('show', ':' + f)   # a staged blob that cannot be read was not scanned: raise
        if not blob:
            continue
        violations.extend(content_violations(f, blob))

    if violations:
        print('')
        print('=========================================================')
        print(' COMMIT BLOCKED - secret material detected in staged files')
        print('=========================================================')
        for v in violations:
            print(f'  - {v}')
        print('')
        print(' Fix:')
        print('   git restore --staged <file>     # unstage it')
        print('   ...then add it to .gitignore so it cannot come back.')
        print('')
        print(' If a credential really was staged, treat it as COMPROMISED and')
        print(' rotate it - do not just unstage and move on.')
        print('')
        return 1

    print(f'  [secrets] staged files clean ({len(staged)} scanned)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
