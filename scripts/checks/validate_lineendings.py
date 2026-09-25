#!/usr/bin/env python3
"""validate_lineendings.py - tracked shell scripts must be LF, for a host with no PowerShell.

TWIN OF validate-lineendings.ps1. .githooks/pre-commit runs the .ps1 wherever a
PowerShell host exists and this file only where none does. Keep the two in step.

What it mirrors: every file `git ls-files '*.sh'` lists (the pathspec matches at any
depth), read from the WORKING TREE - not the index - and refused if it contains a CRLF
pair. A lone CR is not flagged, by either twin. A path listed but absent on disk is
skipped, as the .ps1's Test-Path skip does. A tracked script that exists but cannot be read, or a `git ls-files` that fails,
REFUSES (both twins) - a failed query is not "no tracked scripts". A UTF-16 file is decoded by its BOM first,
as Get-Content does, so its CRLF is still seen.

One stated difference: git is asked with -z, so a non-ASCII file name is checked. The
.ps1 receives git's C-quoted rendering of such a name, cannot find it on disk, and
skips it.

Standard library only. Exit 0 = clean, 1 = a tracked *.sh has CRLF line endings.
"""
from __future__ import annotations

import os
import subprocess
import sys


def has_crlf(raw: bytes) -> bool:
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        try:
            return '\r\n' in raw.decode('utf-16')
        except UnicodeDecodeError:
            pass
    return b'\r\n' in raw


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print("Checking line endings (git-tracked *.sh)...")
    proc = subprocess.run(['git', 'ls-files', '-z', '*.sh'], cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)
    # FAIL CLOSED (ac-hooks-portable2): a git that cannot read the repository returns
    # nothing; that is not "no tracked shell scripts".
    if proc.returncode != 0:
        err = proc.stderr.decode('utf-8', 'replace').strip().splitlines()
        print(f"FAILED: 'git ls-files' exited {proc.returncode} - cannot tell which shell scripts"
              f" are tracked{': ' + err[0] if err else ''}")
        return 1
    out = proc.stdout
    tracked = [p.decode('utf-8', 'surrogateescape') for p in out.split(b'\0') if p]
    if not tracked:
        print("SUCCESS: No tracked shell scripts to check")
        return 0

    bad = False
    for rel in tracked:
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            continue
        try:
            with open(full, 'rb') as fh:
                raw = fh.read()
        except OSError as e:
            # A tracked script that exists but cannot be read was not checked: refuse.
            print(f"FAILED: cannot read {rel} ({e})")
            return 1
        if raw and has_crlf(raw):
            print(f"ERROR: Windows line endings found in: {rel}")
            bad = True

    if not bad:
        print("SUCCESS: All tracked shell scripts have Unix line endings")
        return 0
    print("FAILED: Line ending validation failed")
    return 1


if __name__ == '__main__':
    sys.exit(main())
