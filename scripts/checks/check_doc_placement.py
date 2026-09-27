#!/usr/bin/env python3
"""check_doc_placement.py - new plan or journal material stays out of the code repo, for a
host with no PowerShell.

TWIN OF check-doc-placement.ps1 (its STAGED mode; `-All` stays PowerShell-only - it is an
audit run by hand and by scripts/checks/plan-store.ps1, never by the hook).
.githooks/pre-commit runs the .ps1 wherever a PowerShell host exists and this file only
where none does. Keep the two in step: test_check_doc_placement.py runs the same cases
through both where PowerShell is present, and through this file alone where it is not.

WHY IT EXISTS (ac-linux-rehearsal, 2026-09-26). The rehearsal's criterion 5 is "an agent
that reads only CLAUDE.md is told to write notes to the plan store, and the pre-commit gate
refuses them in the code repo". On a Linux clone with git and python3 only - the adopter the
README describes - the gate printed `SKIPPED doc-placement: needs PowerShell` and
`documentation/notes/x.md` COMMITTED. The rule was enforced only on hosts that have
PowerShell.

What it mirrors, rule for rule (the .ps1's header has the history of each rule):
  1. a staged addition, rename or copy (-M, ACR) under documentation/notes/,
     documentation/evidence/ or documentation/archive/ (case-insensitive prefix) is refused,
     with the store path to use; no exemption applies;
  2. a staged addition/rename/copy of a ROOT-LEVEL file whose name is plan or journal
     shaped (Test-RootJournal: TASKS/ROADMAP exactly; the PLAN / TEST-PLAN / CLEANUP-PLAN /
     BUILD-LOG / FINDINGS stems as a prefix for .md .markdown .rst .adoc and extensionless
     names, at a word boundary for .txt, never for any other extension) is refused;
  3. a staged ADDITION (A only) under documentation/implementation-guide/ that is not
     exempt (the index README and multi-agent-concurrency/) is refused when it opens a
     feature directory HEAD does not have, or when its path is plan-shaped;
  AI_STACK_PLAN_IN_CODE_REPO=1 (or `true`) warns and passes; the verdict names how many
  staged paths it examined.

One stated difference: a failing `git diff --cached` REFUSES here (exit 1, naming the
failure). The .ps1 reads a failed query as an empty list and passes. A failed query is not
"nothing staged".

Standard library only. Exit 0 = clean, 1 = material in the wrong repo (or git failed).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

GUIDE_PREFIX = 'documentation/implementation-guide/'
STORE_REL = '../documentation-plans-ai-stack'

PLAN_SHAPED = re.compile(
    r'(^|/)(\d{2}-[^/]+\.md|[^/]*PLAN[^/]*\.md|BUILD-LOG\.md|TASKS?\.md|ROADMAP\.md|PHASE[^/]*\.md)$',
    re.IGNORECASE)

JOURNAL_DIRS = (
    ('documentation/notes/', 'journal/notes/'),
    ('documentation/evidence/', 'journal/evidence/'),
    ('documentation/archive/', 'journal/archive/'),
)

ROOT_DOC_EXT = ('md', 'markdown', 'rst', 'adoc')
ROOT_STEMS = r'(TEST[-_]?PLAN|CLEANUP-PLAN|BUILD-LOG|PLAN|FINDINGS)'
ROOT_PREFIX = re.compile(r'^(' + ROOT_STEMS + r'|.*[-_]FINDINGS)', re.IGNORECASE)
ROOT_WORD = re.compile(r'^(' + ROOT_STEMS + r'|.*[-_]FINDINGS)([-_.].*)?$', re.IGNORECASE)
ROOT_EXACT_DOC = re.compile(r'^(TASKS?|ROADMAP)(\.md)?$', re.IGNORECASE)

EXEMPT = (
    re.compile(r'^documentation/implementation-guide/README\.md$', re.IGNORECASE),
    re.compile(r'^documentation/implementation-guide/multi-agent-concurrency/', re.IGNORECASE),
    re.compile(r'^documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL\.md$',
               re.IGNORECASE),
)


def is_root_journal(p: str) -> bool:
    if '/' in p:  # root FILES only
        return False
    if ROOT_EXACT_DOC.search(p):
        return True
    dot = p.rfind('.')
    if dot <= 0:
        return bool(ROOT_PREFIX.search(p))
    ext = p[dot + 1:].lower()
    base = p[:dot]
    if ext in ROOT_DOC_EXT:
        return bool(ROOT_PREFIX.search(base))
    if ext == 'txt':
        return bool(ROOT_WORD.search(base))
    return False


def is_exempt(p: str) -> bool:
    return any(rx.search(p) for rx in EXEMPT)


def journal_target(p: str) -> str | None:
    low = p.lower()
    for prefix, dest in JOURNAL_DIRS:
        if low.startswith(prefix):
            return STORE_REL + '/' + dest + p[len(prefix):]
    if is_root_journal(p):
        if re.match(r'TEST[-_]?PLAN', p, re.IGNORECASE):
            return (STORE_REL + '/implementation-guide/<feature>/test-plans/' + p
                    + '  (or journal/test-plans/' + p + ')')
        if re.search(r'(^|[-_])FINDINGS', p, re.IGNORECASE):
            return (STORE_REL + '/implementation-guide/<feature>/findings/' + p
                    + '  (or journal/notes/' + p + ')')
        return STORE_REL + '/implementation-guide/<feature>/' + p
    return None


def git(root: str, *args: str) -> tuple[int, list[str], str]:
    proc = subprocess.run(['git', *args], cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)
    items = [x.decode('utf-8', 'surrogateescape') for x in proc.stdout.split(b'\0') if x]
    return proc.returncode, items, proc.stderr.decode('utf-8', 'replace').strip()


def main() -> int:
    top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, check=False)
    root = top.stdout.decode('utf-8', 'replace').strip() or os.getcwd()

    # Feature directories HEAD already has (an empty repo has none: every one is new).
    known = set()
    proc = subprocess.run(['git', 'ls-tree', '--name-only', '-z', 'HEAD', GUIDE_PREFIX], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if proc.returncode == 0:
        for entry in proc.stdout.split(b'\0'):
            leaf = entry.decode('utf-8', 'surrogateescape')
            if leaf.lower().startswith(GUIDE_PREFIX):
                leaf = leaf[len(GUIDE_PREFIX):]
            leaf = leaf.rstrip('/')
            if leaf:
                known.add(leaf.lower())

    rc_a, staged_a, err_a = git(root, 'diff', '--cached', '--name-only', '-z', '--diff-filter=A')
    rc_acr, staged_acr, err_acr = git(root, 'diff', '--cached', '--name-only', '-z', '-M',
                                      '--diff-filter=ACR')
    if rc_a != 0 or rc_acr != 0:
        err = (err_a or err_acr).splitlines()
        print(f"FAILED: 'git diff --cached' exited {rc_a or rc_acr} - cannot tell what is staged"
              f"{': ' + err[0] if err else ''}")
        return 1

    violations = []
    for p in staged_acr:  # NO exemption is consulted here
        target = journal_target(p)
        if target:
            violations.append((p, 'operator journal (notes / findings / evidence / test plans'
                                  ' / archive / closed plans)', target))
    for p in staged_a:
        if is_exempt(p):
            continue
        if not p.lower().startswith(GUIDE_PREFIX):
            continue
        rest = p[len(GUIDE_PREFIX):]
        feature = rest.split('/')[0]
        if '/' in rest and feature.lower() not in known:
            violations.append((p, f"new feature directory '{feature}' in the code repo",
                               STORE_REL + '/implementation-guide/' + rest))
            continue
        if PLAN_SHAPED.search(p):
            violations.append((p, 'planning material (plan / build log / task list / numbered'
                                  ' plan set)',
                               STORE_REL + '/implementation-guide/<feature>/' + p.rsplit('/', 1)[-1]))

    examined = len(set(staged_acr) | set(staged_a))
    if not violations:
        print(f"SUCCESS: no new planning or journal material staged into the code repo"
              f" ({examined} staged addition(s)/rename(s) examined)")
        return 0

    if os.environ.get('AI_STACK_PLAN_IN_CODE_REPO') in ('1', 'true'):
        print("WARNING: planning or journal material staged into the code repo, allowed by"
              " AI_STACK_PLAN_IN_CODE_REPO:")
        for path, why, _ in violations:
            print(f"  {path}  ({why})")
        print("State the reason in the commit message.")
        return 0

    print("")
    print(f"FAIL: {len(violations)} of {examined} staged path(s) belong in"
          " documentation-plans-ai-stack, not here.")
    for path, why, use in violations:
        print(f"  {path}")
        print(f"      {why}")
        print(f"      write it at: {use}")
    print("")
    print("The plan store is the sibling checkout <workspace>/documentation-plans-ai-stack/:")
    print("  implementation-guide/<feature>/            plans, build logs, task lists")
    print("  implementation-guide/<feature>/findings/   a work item's findings sink")
    print("  implementation-guide/<feature>/test-plans/ a work item's test plan")
    print("  journal/notes|evidence|archive|test-plans/ anything with no feature")
    print("Commit AND push it there (git pull --rebase first), then add or update the feature's")
    print("status row here in documentation/implementation-guide/README.md.")
    print("Deliberate exception: AI_STACK_PLAN_IN_CODE_REPO=1, with the reason in the commit message.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
