#!/usr/bin/env python3
"""Relative-link sweep over EVERY tracked Markdown file in the code repo.

    python scripts/checks/check-md-links.py [--root <repo>] [--store <plan-store>]
                                            [--require-store] [--require-submodules]
                                            [--quiet]

WHY. The journal (notes, evidence, archive, closed plans, test plans) moved out of this
repo into the private plan store, `documentation-plans-ai-stack`, on 2026-09-25
(adoption-closeout item ac-journal-move). Every inbound link to a moved file had to be
rewritten or removed, and "I rewrote them" is not evidence. This script is: it resolves
every relative link in every tracked `.md` and lists what does not exist.

It supersedes the one-off `href-sweep.py` from sl-gate4-carries (now in the store under
journal/evidence/sl-gate4-carries/), which swept documentation/runbooks/ only.

WHAT COUNTS AS A LINK. An inline link or image `[text](target)` / `![alt](target)`, a
reference definition `[label]: target`, and an HTML `href="..."` / `src="..."`. Skipped,
and COUNTED as skipped: any target with a URI scheme (`http:`, `https:`, `mailto:`, ...),
a bare `#anchor`, and anything inside a fenced code block or an inline code span - code
samples show syntax, they are not navigation.

HOW A TARGET RESOLVES. Against the directory of the file that carries it, after dropping
`#fragment` / `?query` and percent-decoding. Two outcomes are a pass:
  * the path exists inside this checkout, or
  * the path, taken relative to the repo root, is EXACTLY `../documentation-plans-ai-stack/
    <rest>` - a link into the sibling plan store at the right depth - and <rest> exists in
    the store. The depth test is what keeps a link one `../` short from being rescued by a
    suffix match. The store is found the way scripts/checks/plan-store.ps1 finds it: beside
    this checkout, else beside the MAIN checkout (a harness worktree lives three levels
    deeper, under .claude/worktrees/<id>/), else `--store`.
A store link with NO store on this machine (CI, a stranger's clone) cannot be checked: it
is counted and listed as UNVERIFIABLE, and it fails the run only under `--require-store`.
The same holds for a link into a git SUBMODULE (OB1) that is not initialised in this
checkout - a clone made without `--recurse-submodules` has an empty directory there. Such
links are counted and listed as UNVERIFIABLE (submodule not initialised) and fail the run
only under `--require-submodules`. An INITIALISED submodule's links are checked normally.

EXIT. 0 = every checkable link resolves; 1 = at least one does not, each listed with the
file, line and resolved path; 2 = misuse, or NOTHING WAS EXAMINED (zero tracked .md files,
or zero links across them) - a sweep that read nothing does not get to say "clean".
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from urllib.parse import unquote

STORE_NAME = "documentation-plans-ai-stack"

INLINE = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>]*>|[^()\s]*(?:\([^()]*\)[^()\s]*)*)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
HTML = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDRIVE = re.compile(r"^[A-Za-z]:[\\/]")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CODESPAN = re.compile(r"(`+)(?:.+?)\1")


def git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True,
                          encoding="utf-8", errors="replace")


def find_store(root: str, explicit: str | None) -> str | None:
    if explicit:
        return os.path.abspath(explicit) if os.path.isdir(explicit) else None
    cands = [os.path.join(os.path.dirname(root), STORE_NAME)]
    common = git(root, "rev-parse", "--git-common-dir")
    if common.returncode == 0 and common.stdout.strip():
        c = common.stdout.strip()
        if not os.path.isabs(c):
            c = os.path.join(root, c)
        main_root = os.path.dirname(os.path.normpath(c))
        cands.append(os.path.join(os.path.dirname(main_root), STORE_NAME))
    for c in cands:
        if os.path.isdir(os.path.join(c, ".git")) or os.path.isfile(os.path.join(c, ".git")):
            return os.path.normpath(c)
    return None


def uninitialised_submodules(root: str):
    """Absolute paths of gitlinks (mode 160000) whose checkout is absent or empty here."""
    out = []
    ls = git(root, "ls-files", "-s", "-z")
    if ls.returncode != 0:
        return out
    for rec in ls.stdout.split("\0"):
        if not rec.startswith("160000 "):
            continue
        rel = rec.split("\t", 1)[1]
        d = os.path.normpath(os.path.join(root, *rel.split("/")))
        if not os.path.exists(os.path.join(d, ".git")):
            out.append(d)
    return out


def targets(text: str):
    """(line_number, raw_target) for every link outside code."""
    in_fence = None
    for n, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            mark = m.group(1)[0]
            if in_fence is None:
                in_fence = mark
                continue
            if in_fence == mark:
                in_fence = None
                continue
        if in_fence is not None:
            continue
        scrubbed = CODESPAN.sub(lambda mm: " " * len(mm.group(0)), line)
        for rx in (INLINE, HTML):
            for mm in rx.finditer(scrubbed):
                yield n, mm.group(1)
        mm = REFDEF.match(scrubbed)
        if mm:
            yield n, mm.group(1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=None)
    ap.add_argument("--store", default=None)
    ap.add_argument("--require-store", action="store_true")
    ap.add_argument("--require-submodules", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    root = a.root
    if not root:
        top = git(os.getcwd(), "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            print("MISUSE: not inside a git checkout and no --root given")
            return 2
        root = top.stdout.strip()
    root = os.path.normpath(os.path.abspath(root))
    ls = git(root, "ls-files", "-z", "--", "*.md", "*.MD")
    if ls.returncode != 0:
        print("MISUSE: git ls-files failed in " + root + ": " + ls.stderr.strip())
        return 2
    files = sorted(p for p in ls.stdout.split("\0") if p)
    store = find_store(root, a.store)
    bare_subs = uninitialised_submodules(root)

    checked = in_repo = in_store = skipped = 0
    unresolved, unverifiable, in_bare_sub = [], [], []
    store_prefix = os.pardir + os.sep + STORE_NAME + os.sep
    for rel in files:
        path = os.path.join(root, *rel.split("/"))
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            unresolved.append((rel, 0, "(file unreadable)", str(exc)))
            continue
        base = os.path.dirname(path)
        for line, raw in targets(text):
            t = raw.strip()
            if t.startswith("<") and t.endswith(">"):
                t = t[1:-1].strip()
            if not t or t.startswith("#") or (SCHEME.match(t) and not WINDRIVE.match(t)):
                skipped += 1
                continue
            t = unquote(t.split("#", 1)[0].split("?", 1)[0])
            if not t:
                skipped += 1
                continue
            checked += 1
            if WINDRIVE.match(t) or t.startswith("/"):
                unresolved.append((rel, line, raw, "absolute path - resolves only on one machine"))
                continue
            resolved = os.path.normpath(os.path.join(base, t))
            if os.path.exists(resolved):
                in_repo += 1
                continue
            bare = next((b for b in bare_subs
                         if resolved == b or resolved.startswith(b + os.sep)), None)
            if bare is not None:
                in_bare_sub.append((rel, line, raw, os.path.relpath(bare, root)))
                continue
            from_root = os.path.relpath(resolved, root)
            if from_root.startswith(store_prefix):
                rest = from_root[len(store_prefix):]
                if store is None:
                    unverifiable.append((rel, line, raw, rest))
                    continue
                if os.path.exists(os.path.join(store, rest)):
                    in_store += 1
                    continue
                unresolved.append((rel, line, raw, os.path.join(store, rest)))
                continue
            unresolved.append((rel, line, raw, resolved))

    print("check-md-links: {0} tracked .md file(s) scanned under {1}".format(len(files), root))
    print("  links checked : {0} ({1} in this checkout, {2} in the plan store)".format(
        checked, in_repo, in_store))
    print("  skipped       : {0} (URI scheme, bare #anchor)".format(skipped))
    print("  plan store    : {0}".format(store or "NOT FOUND - store links are unverifiable here"))
    if unverifiable:
        print("  UNVERIFIABLE  : {0} link(s) into the plan store, which is not on this machine".format(
            len(unverifiable)))
        if not a.quiet:
            for rel, line, raw, rest in unverifiable:
                print("    {0}:{1}  {2}".format(rel, line, raw))
    if in_bare_sub:
        subs = sorted({x[3] for x in in_bare_sub})
        print("  UNVERIFIABLE  : {0} link(s) into submodule(s) not initialised here ({1}) - run "
              "`git submodule update --init` to check them".format(len(in_bare_sub), ", ".join(subs)))
        if not a.quiet:
            for rel, line, raw, _sub in in_bare_sub:
                print("    {0}:{1}  {2}".format(rel, line, raw))
    print("  unresolved    : {0}".format(len(unresolved)))
    for rel, line, raw, where in unresolved:
        print("    {0}:{1}  ({2})  ->  {3}".format(rel, line, raw, where))

    if not files or checked == 0:
        print("REFUSED: nothing was examined ({0} file(s), {1} link(s)). A sweep that read "
              "nothing is not a clean sweep.".format(len(files), checked))
        return 2
    if unresolved:
        return 1
    if in_bare_sub and a.require_submodules:
        print("FAIL: --require-submodules and {0} submodule link(s) could not be checked".format(
            len(in_bare_sub)))
        return 1
    if unverifiable and a.require_store:
        print("FAIL: --require-store and {0} store link(s) could not be checked".format(len(unverifiable)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
