#!/usr/bin/env python3
"""check_llm_gateway_routing.py - gateway-only LLM routing, for a host with no PowerShell.

TWIN OF check-llm-gateway-routing.ps1. .githooks/pre-commit runs the .ps1 wherever a
PowerShell host exists and this file only where none does. The policy, the reasons for
every allow and prune entry, and the history of each are in the .ps1; this file copies
the rules, not the prose. CHANGE BOTH OR NEITHER.

What it mirrors:
  * the WORKING TREE under the repo root is walked (not the index), pruning directories
    by exact name (case-insensitive), `*-data`, and symlinks/junctions;
  * files are kept by leaf pattern (case-insensitive) and dropped by the allow globs,
    which are matched against `\\<path relative to the root>` with `/` turned into `\\`,
    exactly as the .ps1's Test-Allowed does - so they mean the same on either OS;
  * lines are split on CR, LF or CRLF (.NET ReadAllLines), comment lines (`#`, `//`
    after leading whitespace) are skipped, the llm-queue forward-target variables are
    sanctioned, and every other line matching the bypass pattern is a violation;
  * zero files scanned is a FAIL, not a green.

Standard library only. Exit 0 = clean, 1 = bypass(es) found or nothing scanned.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import sys

BAD = re.compile(r'(?i)(_HOST|_BASE|API_BASE|BASE_URL|api_base|CHAT_API_BASE|OPENAI[A-Z_]*BASE'
                 r'|EMBED[A-Z_]*BASE|LLM_BASE)\b[^\r\n]*[:=][^\r\n]*'
                 r'(llama-cpp-upstream|llama-cpp-embed-upstream)')
QUEUE_UPSTREAM_ALLOW = re.compile(r'(?i)LLM_QUEUE(_EMBED)?_UPSTREAM_BASE_URL')

ALLOW_PATH_LIKE = (
    '*\\inference\\config\\litellm.config.yaml',
    '*\\scripts\\recovery\\emergency-recovery.ps1',
    '*\\scripts\\checks\\stack-watchdog.ps1',
    '*\\scripts\\checks\\check-backup-coverage.ps1',
    '*\\scripts\\checks\\check-llm-gateway-routing.ps1',
    '*\\modules\\system-health\\*',
    '*\\modules\\gpu-status\\*',
    '*\\documentation\\*',
    '*\\node_modules\\*',
    '*\\.git\\*',
    '*\\.next\\*',
    '*\\data\\*',
    '*\\notebook_data\\*',
    '*-data\\*',
    '*\\backups\\*',
    '*\\tiktoken-cache\\*',
)
EXTS = ('*.yml', '*.yaml', '*.env', '.env', '*.ts', '*.js', '*.py', '*.sh', '*.toml', '*.json', '*.conf')
PRUNE_DIR_NAMES = ('.git', '.claude', '.venv', '.testvenv', 'node_modules', '.next',
                   'backups', 'tiktoken-cache', 'notebook_data', 'data')

# .NET String.TrimStart() strips Char.IsWhiteSpace; Python's str.lstrip() strips a
# slightly larger set (it includes U+001C-U+001F). Use the .NET set so a line that
# starts with one of those four is judged the same way by both twins.
_NET_WS = ''.join(chr(c) for c in list(range(0x09, 0x0E)) + [0x20, 0x85, 0xA0, 0x1680]
                  + list(range(0x2000, 0x200B)) + [0x2028, 0x2029, 0x202F, 0x205F, 0x3000])


def _like_rx(pattern: str) -> re.Pattern:
    """PowerShell -like (`*` and `?` only; these patterns use no `[`), case-insensitive."""
    out = []
    for ch in pattern:
        out.append('.*' if ch == '*' else '.' if ch == '?' else re.escape(ch))
    return re.compile(''.join(out), re.IGNORECASE | re.DOTALL)


ALLOW_RX = tuple(_like_rx(g) for g in ALLOW_PATH_LIKE)
EXT_RX = tuple(_like_rx(e) for e in EXTS)
DATA_DIR_RX = _like_rx('*-data')
PRUNE_LOWER = {n.lower() for n in PRUNE_DIR_NAMES}


def rel_for_allow(root_prefix: str, path: str) -> str:
    if path.lower().startswith(root_prefix.lower()):
        rel = path[len(root_prefix):]
    else:
        rel = path
    return '\\' + rel.replace('/', '\\').lstrip('\\')


def allowed(root_prefix: str, path: str) -> bool:
    rel = rel_for_allow(root_prefix, path)
    return any(rx.fullmatch(rel) for rx in ALLOW_RX)


def _is_link(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    # Windows junctions / other reparse points (.NET FileAttributes.ReparsePoint).
    attrs = getattr(st, 'st_file_attributes', 0)
    return bool(attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def scan_files(root: str) -> list[str]:
    results = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                is_dir = e.is_dir()          # follows links, like EnumerateDirectories
            except OSError:
                continue
            if not is_dir:
                continue
            if e.name.lower() in PRUNE_LOWER or DATA_DIR_RX.fullmatch(e.name):
                continue
            if _is_link(e.path):
                continue
            stack.append(e.path)
        for e in entries:
            try:
                if not e.is_file():
                    continue
            except OSError:
                continue
            if any(rx.fullmatch(e.name) for rx in EXT_RX):
                results.append(e.path)
    return results


def read_lines(path: str) -> list[str] | None:
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError:
        return None
    # ReadAllLines: BOM-detected, else UTF-8 with replacement; split on CR, LF, CRLF.
    if raw.startswith(b'\xef\xbb\xbf'):
        text = raw[3:].decode('utf-8', 'replace')
    elif raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        text = raw.decode('utf-16', 'replace')
    else:
        text = raw.decode('utf-8', 'replace')
    lines = re.split(r'\r\n|\r|\n', text)
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=None)
    args = ap.parse_args()
    root = args.root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    root_prefix = root.rstrip('\\/')

    files = [f for f in scan_files(root) if not allowed(root_prefix, f)]
    scanned = len(files)
    tag = '[check-llm-gateway-routing]'
    if scanned == 0:
        print(f'{tag} FAIL - scanned 0 files under {root}.')
        print('  Nothing was examined, so this run proves nothing about the tree. Check the')
        print('  scan root and the prune/allow lists before reading any green here as cover.')
        return 1

    violations = []
    for f in files:
        lines = read_lines(f)
        if lines is None:
            continue
        for n, line in enumerate(lines, 1):
            trimmed = line.lstrip(_NET_WS)
            if trimmed.startswith('#') or trimmed.startswith('//'):
                continue
            if QUEUE_UPSTREAM_ALLOW.search(line):
                continue
            if BAD.search(line):
                rel = f[len(root):].lstrip('\\/')
                violations.append((rel, n, line.strip(_NET_WS)))

    if not violations:
        print(f'{tag} OK - no LLM gateway bypasses found. {scanned} file(s) scanned under {root}.')
        return 0

    print(f'{tag} FAIL - {len(violations)} gateway bypass(es) found in {scanned} file(s) scanned under {root}:')
    print('  An inference/serve endpoint points at a *-upstream real server instead of the')
    print('  gateway alias (llama-cpp / llama-cpp-embed). Route it through llm-gateway.\n')
    for rel, n, text in violations:
        print(f'  {rel}:{n}')
        print(f'      {text}')
    print('\n  If a hit is a legitimate health/recovery probe, add its path to $allowPathLike'
          ' in the .ps1 AND ALLOW_PATH_LIKE here.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
