#!/usr/bin/env python3
"""validate_lineendings.py - tracked shell scripts must be LF, for a host with no PowerShell.

TWIN OF validate-lineendings.ps1. .githooks/pre-commit runs the .ps1 wherever a
PowerShell host exists and this file only where none does. Keep the two in step.

What it mirrors: every file `git ls-files '*.sh'` lists (the pathspec matches at any
depth), read from the WORKING TREE - not the index - and refused if it contains a CRLF
pair. A lone CR is not flagged, by either twin. A path listed but absent on disk is
skipped, as the .ps1's Test-Path skip does. A UTF-16 file is decoded by its BOM first,
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
    out = subprocess.run(['git', 'ls-files', '-z', '*.sh'], cwd=root, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, check=False).stdout
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
        except OSError:
            continue
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
