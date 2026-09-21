"""Unbounded relative-href sweep over documentation/runbooks/ (sl-gate4-carries).

Why this exists: the sl-recovery-backups sweep reported "1 unresolved left" and
was wrong - it matched only the `](../scripts/...)` spelling and missed two
`../` vs `../../` errors in backup-conventions.md. This one has no allowlist and
no spelling assumption: EVERY `](target)` in every .md under documentation/
runbooks/ that is not http(s):, mailto: or a bare #anchor is resolved against
the directory of the file that carries it, and anything that does not exist on
disk is printed.

Usage (from anywhere):
    python documentation/evidence/sl-gate4-carries/href-sweep.py <repo-root>

<repo-root> defaults to the current directory. Exit 0 = every relative href
resolves; exit 1 = at least one does not, and each is listed with the absolute
path it resolved to.

DEPTH MATTERS. `UPDATE-MANAGEMENT.md` links the sibling plan store as
`../../../documentation-plans-ai-stack/...`, which is correct from the main
checkout (D:\\Open WebUI\\ai-stack) and unresolvable from a worktree under
.claude/worktrees/<id>/, which sits three directories deeper. Run it against the
main checkout before calling a plan-store link broken.
"""

import os
import re
import sys

LINK = re.compile(r"\]\(([^()\s]*(?:\([^()]*\)[^()\s]*)*)\)")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "#")


def sweep(root):
    subdir = os.path.join(root, "documentation", "runbooks")
    unresolved = []
    checked = 0
    skipped = 0
    for dirpath, _dirnames, filenames in os.walk(subdir):
        for name in sorted(filenames):
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    for match in LINK.finditer(line):
                        target = match.group(1).strip()
                        if not target:
                            continue
                        if target.lower().startswith(SKIP_SCHEMES):
                            skipped += 1
                            continue
                        checked += 1
                        # drop a link title and any #fragment
                        bare = target.split(' "')[0].split(" '")[0].split("#")[0]
                        if not bare:
                            continue
                        resolved = os.path.normpath(os.path.join(dirpath, bare))
                        if not os.path.exists(resolved):
                            rel = os.path.relpath(path, root).replace("\\", "/")
                            unresolved.append((rel, lineno, target, resolved))
    return checked, skipped, unresolved


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    checked, skipped, unresolved = sweep(root)
    print(
        "relative hrefs checked: %d (http/mailto/anchor skipped: %d)"
        % (checked, skipped)
    )
    print("unresolved: %d" % len(unresolved))
    for rel, lineno, target, resolved in unresolved:
        print("  %s:%d -> %s   (resolves to %s)" % (rel, lineno, target, resolved))
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
