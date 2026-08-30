#!/usr/bin/env python3
"""The projects map in scripts/lib/stack-services.json must point at files that EXIST.

WHERE THIS CAME FROM (this is a durable check, so its origin is part of the artifact).
A TESTER wrote it, not an operator and not the developer whose work it was reviewing.
`watchdog-fix` attempt 1, tester wt-tester-3, 2026-08-28, evidence section C item 4
("IS THE 'MACHINE-VERIFIED' JUSTIFICATION TRUE?"), verbatim:

    RESIDUAL RISK FOR THE REVIEWER: the NEW data this patch adds (the projects map's
    file and env_file fields) is NOT covered by that verifier, and the verifier only
    runs when a .yml is staged. A plane compose-file RENAME would silently stale the
    file field.

The tester PASSED the item. The finding was true, out of scope, and cost nothing to
ignore - which is precisely the finding class that evaporates into prose (dark-factory
PLAN §0 A5). This file is that finding made executable and owned by the line.

WHAT WOULD HAVE CAUGHT IT OTHERWISE - MEASURED, not asserted (corrected 2026-08-30).
This docstring used to say "nothing", and that was FALSE. A verifier disproved it by
running the pre-existing check-watchdog-repair-targets.ps1 against a seeded sandbox. The
honest matrix, reproduced on every run of scripts/agent-harness/seeded_regression_drill.py
(sections 3-6, four seeds x two checks, exit codes asserted):

    seed                                              pre-existing   this check
    A  rename, inventory untouched                     CAUGHT         caught
    B  half-fixed rename (file updated, -f not)        missed         caught
    C  rename in a project no managed container
       points at (agent-org)                           missed         caught
    D  env_file points at a file that is not there     missed         caught

So this check is NOT the only thing standing between the repo and the tester's rename;
for seed A it is the second line, not the first. Its genuine remainder is B, C and D.
Why each pre-existing reader stops where it does:
  * check-project-configs.ps1 (the pre-commit hook's drift verifier, lines 60-102) builds
    `$known[container] = project` from `planes[]` and compares it to rendered
    `container_name` values. It reads the CONTAINER rows: it never opens
    `projects[*].file` or `projects[*].env_file`, and it only runs when a .yml is staged.
    It catches none of the four seeds.
  * check-watchdog-repair-targets.ps1 does `Test-Path` on `projects[*].file` - but it
    reaches a project only by resolving a container the watchdog claims to self-heal, so a
    project with no managed row (agent-org) is invisible to it; and it never reads
    `env_file`, nor the `-f` argument inside the `compose` command, so B and D pass it.
    It is also not wired into pre-commit (its own header says so) - it is an on-demand
    script, so even for seed A nothing catches the commit that makes the mistake.
stack-watchdog.ps1 CONSUMES both fields (line 139-140, Invoke-PlaneCompose) and would
fail at repair time, on the host, at 3am.

WHAT IT CHECKS, and why each rule is here rather than being a nicety:
  1. every non-null projects[*].file exists            - the rename the tester named
  2. the -f argument inside `compose` equals `file`     - a half-fixed rename is the
     realistic failure: someone updates one field. Two fields that must agree and are
     never compared are two chances to be wrong.
  3. the --env-file argument equals `env_file`, and null means the flag is ABSENT -
     both open-brain and agent-org deliberately carry env_file: null with a note saying
     passing the root .env would override their own. A drift that adds --env-file .env
     there is a silent misconfiguration, not a typo.
  4. a non-null env_file resolves to <path> OR <path>.example - .env is gitignored, so
     requiring the real file would make this check red in a fresh clone and a check that
     is red for everyone is a check nobody keeps.

Exit 0 clean, 1 with one line per problem. No docker, no network, no repo mutation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

INVENTORY = "scripts/lib/stack-services.json"


def _submodule_paths(root: Path) -> list[str]:
    """Paths declared in .gitmodules. A file inside an UNINITIALIZED submodule is absent
    for a reason that is not drift, and calling that red would train people to ignore
    this check on every fresh clone."""
    gm = root / ".gitmodules"
    if not gm.is_file():
        return []
    return re.findall(r"^\s*path\s*=\s*(.+?)\s*$", gm.read_text(encoding="utf-8", errors="replace"),
                      re.MULTILINE)


def _uninitialized_submodule(root: Path, rel: str, subs: list[str]) -> str:
    for s in subs:
        if rel == s or rel.startswith(s.rstrip("/") + "/"):
            d = root / s
            if not d.is_dir() or not any(d.iterdir()):
                return s
    return ""


def _flag(cmd: str, flag: str) -> str | None:
    """The argument to `flag` in a compose command string, or None if the flag is absent."""
    parts = cmd.split()
    for i, p in enumerate(parts):
        if p == flag:
            return parts[i + 1] if i + 1 < len(parts) else ""
    return None


def check(root: Path) -> tuple[list[str], list[str]]:
    """Returns (problems, notes). Pure apart from reading the tree."""
    problems: list[str] = []
    notes: list[str] = []
    inv_path = root / INVENTORY
    if not inv_path.is_file():
        return ([f"{INVENTORY} is missing (looked in {root})"], notes)
    try:
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return ([f"{INVENTORY} is not valid JSON: {exc}"], notes)

    projects = inv.get("projects")
    if not isinstance(projects, dict) or not projects:
        return ([f"{INVENTORY} has no 'projects' map"], notes)

    subs = _submodule_paths(root)
    for name in sorted(projects):
        row = projects[name] or {}
        cmd = (row.get("compose") or "").strip()
        f = row.get("file")
        ef = row.get("env_file")
        if not cmd:
            problems.append(f"projects.{name}: no 'compose' command")
            continue

        # 1 + 2 - the file exists, and the command agrees with the field
        cmd_f = _flag(cmd, "-f")
        if f:
            rel = str(f).replace("\\", "/")
            skip = _uninitialized_submodule(root, rel, subs)
            if skip:
                notes.append(f"projects.{name}: {rel} is inside uninitialized submodule '{skip}' - existence not checked")
            elif not (root / rel).is_file():
                problems.append(f"projects.{name}.file points at a file that does not exist: {rel}")
            if cmd_f is None:
                problems.append(f"projects.{name}: file='{rel}' but 'compose' carries no -f")
            elif cmd_f.replace("\\", "/") != rel:
                problems.append(f"projects.{name}: 'compose' -f is '{cmd_f}' but file='{rel}'")
        else:
            if cmd_f is not None:
                problems.append(f"projects.{name}: file=null but 'compose' carries -f {cmd_f}")

        # 3 + 4 - env_file agrees with the command, and resolves to a real or example file
        cmd_ef = _flag(cmd, "--env-file")
        if ef:
            rel = str(ef).replace("\\", "/")
            if cmd_ef is None:
                problems.append(f"projects.{name}: env_file='{rel}' but 'compose' carries no --env-file")
            elif cmd_ef.replace("\\", "/") != rel:
                problems.append(f"projects.{name}: 'compose' --env-file is '{cmd_ef}' but env_file='{rel}'")
            if not ((root / rel).is_file() or (root / (rel + ".example")).is_file()):
                problems.append(f"projects.{name}.env_file resolves to neither {rel} nor {rel}.example")
        else:
            if cmd_ef is not None:
                problems.append(f"projects.{name}: env_file=null but 'compose' carries --env-file {cmd_ef}")

    return problems, notes


def main(argv: list[str]) -> int:
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parents[2]
    problems, notes = check(root)
    for n in notes:
        print(f"  [paths] note: {n}")
    if problems:
        for p in problems:
            print(f"  [paths] STALE INVENTORY: {p}")
        print(f"  [paths] {len(problems)} problem(s) in {INVENTORY} - a plane's compose path or env-file "
              f"reference no longer matches the tree (stack-watchdog.ps1 reads these to repair planes)")
        return 1
    print(f"  [paths] {INVENTORY}: every projects[*] file/env_file reference resolves and matches its compose command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
