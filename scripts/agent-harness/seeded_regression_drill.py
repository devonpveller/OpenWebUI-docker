#!/usr/bin/env python3
"""SEEDED-REGRESSION DRILL - dark-factory-unification U3's Validated-by column.

    "a seeded regression must be caught by a check born from a *tester* finding in a
     prior round (gym-007's shape, new source)"

gym-007's shape (ORCHESTRATION-DESIGN §10, agent-org): a recurring OPERATOR review
finding was captured once as a durable executable check, and the next round's org was
forced to ship the behaviour the goal never asked for. The recurrence broke. THE NEW
SOURCE this drill supplies is a TESTER finding - the extension U3 makes. Testers produce
findings the operator never sees, and the ones that do NOT block a merge are the ones
that evaporate (PLAN §0 A5): the item passes, the finding is true, and nothing carries it
forward.

THE FINDING (real, prior round, not invented for this drill):
  item      watchdog-fix, attempt 1
  tester    wt-tester-3, 2026-08-28   verdict PASS
  evidence  .git/agent-worktrees/queue/watchdog-fix.attempt1.evidence.md, section C item 4
  said      "the projects map's file and env_file fields [are] NOT covered by that
             verifier ... A plane compose-file RENAME would silently stale the file field"

WHAT IS REAL HERE AND WHAT IS NOT - stated because a claimed gym run that was a local
simulation is exactly the over-claim §C.7 exists to prevent:
  REAL  the finding (a prior round's tester evidence file, quoted above)
  REAL  the check (scripts/checks/check_stack_services_paths.py, runs against this tree)
  REAL  the bank (scripts/agent-harness/durable_checks.py -> the SHARED git-dir registry)
  REAL  the regression (a file rename, performed on disk) and the red/green (exit codes)
  NOT   an agent-org / ai-orchestration-gym org cycle. No worker built the regression and
        no PR was scored. The regression is seeded deterministically by this file, in a
        disposable sandbox, so the loop is RE-RUNNABLE rather than a transcript.

Runs anywhere, needs nothing but python and a checkout. Prints named checks and a count,
like verify-merge-protocol.ps1 and agent-org's tests/test_org_drill.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import durable_checks  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

CHECK_CMD = "python scripts/checks/check_stack_services_paths.py"
CHECK_WHY = (
    "stack-watchdog.ps1 builds every plane repair command from projects[*].file and "
    "env_file in scripts/lib/stack-services.json, and NOTHING verified those two fields: "
    "check-project-configs.ps1's drift verifier reads the planes[] container rows only, and "
    "only when a .yml is staged. A plane compose-file rename stales the field silently and "
    "the watchdog discovers it mid-incident. Found by wt-tester-3 on watchdog-fix attempt 1 "
    "(2026-08-28, evidence section C item 4) - a PASSING review, so nothing else carried it."
)
SOURCE_ITEM = "watchdog-fix attempt 1 / wt-tester-3 evidence section C item 4"

SEED_TARGET = "search/docker-compose.yml"      # the plane whose compose file gets renamed
SEED_RENAMED = "search/compose.yml"

_passed = 0
_failed = 0


def check(name, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print("  [PASS] " + name + (("  (" + detail + ")") if detail else ""))
    else:
        _failed += 1
        print("  [FAIL] " + name + (("  (" + detail + ")") if detail else ""))


def run_capture(cmd, cwd):
    p = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def first_line(text):
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[0] if lines else ""


def build_sandbox(dest):
    """A disposable tree carrying exactly what the check reads. The file list is DERIVED
    from the inventory, so the sandbox cannot drift away from what it stands in for."""
    inv = json.loads((REPO / "scripts/lib/stack-services.json").read_text(encoding="utf-8"))
    wanted = ["scripts/lib/stack-services.json",
              "scripts/checks/check_stack_services_paths.py",
              ".gitmodules", ".env.example"]
    for row in inv.get("projects", {}).values():
        for key in ("file", "env_file"):
            v = (row or {}).get(key)
            if v:
                wanted.append(str(v).replace("\\", "/"))
    copied = []
    for rel in dict.fromkeys(wanted):
        src = REPO / rel
        if not src.is_file():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    return copied


def main():
    print("=== U3 SEEDED-REGRESSION DRILL - a tester finding, banked, then caught ===\n")
    print("--- 1. the finding is banked as a durable check (content-addressed, shared registry)")

    row = durable_checks.add(REPO, command=CHECK_CMD, why=CHECK_WHY, source_item=SOURCE_ITEM)
    check("the tester finding is banked", bool(row.get("id")), "id " + str(row.get("id")))
    check("the bank names its human origin",
          SOURCE_ITEM in (row.get("source_item") or "") and row.get("source") == "tester-finding")

    again = durable_checks.add(REPO, command=CHECK_CMD, why="a second tester hits the same wall",
                               source_item="a later round")
    banked = [c for c in durable_checks.load(REPO) if c["id"] == row["id"]]
    check("re-banking the same finding is idempotent, not a duplicate row",
          again["id"] == row["id"] and len(banked) == 1)

    registry = durable_checks.registry_path(REPO)
    check("the registry lives in the SHARED git dir, so every worktree sees one bank",
          registry.is_file() and ".git" in str(registry), str(registry))

    # THE CHECK THE DRILL RUNS IS THE BANKED ONE, read back out of the registry - not a
    # literal in this file. A drill that runs its own copy proves nothing about the bank.
    from_registry = [c for c in durable_checks.load(REPO) if c["id"] == row["id"]][0]
    check("the drill runs the check it read back from the registry",
          from_registry["check"] == CHECK_CMD)

    print("\n--- 2. baseline: green on the real tree, and on the sandbox that stands in for it")
    base = durable_checks.run(REPO, [from_registry])
    check("the banked check is GREEN on this checkout", base["failed"] == 0)

    tmp = Path(tempfile.mkdtemp(prefix="u3gym-sandbox-"))
    try:
        sandbox = tmp / "tree"
        copied = build_sandbox(sandbox)
        check("disposable sandbox built from the inventory's own file list",
              (sandbox / "scripts/lib/stack-services.json").is_file() and len(copied) >= 8,
              str(len(copied)) + " files")
        check("the sandbox carries the plane the regression will hit",
              (sandbox / SEED_TARGET).is_file())

        sb_green = durable_checks.run(sandbox, [from_registry])
        check("the banked check is GREEN on the untouched sandbox", sb_green["failed"] == 0)

        print("\n--- 3. SEED A: the rename the tester predicted (compose file moves, map does not)")
        (sandbox / SEED_TARGET).rename(sandbox / SEED_RENAMED)
        check("regression seeded: the compose file is renamed, the inventory untouched",
              (sandbox / SEED_RENAMED).is_file() and not (sandbox / SEED_TARGET).exists())

        red = durable_checks.run(sandbox, [from_registry])
        check("the banked check goes RED on the seeded tree", red["failed"] == 1,
              red["results"][0]["detail"])

        rc, out = run_capture(from_registry["check"], sandbox)
        check("RED for the RIGHT reason, not an incidental non-zero exit",
              rc == 1 and SEED_TARGET in out and "projects.search.file" in out,
              first_line(out))

        (sandbox / SEED_RENAMED).rename(sandbox / SEED_TARGET)
        rev = durable_checks.run(sandbox, [from_registry])
        check("reverting the seed returns the check to GREEN", rev["failed"] == 0)

        print("\n--- 4. SEED B: the half-fixed rename (the field is updated, the command is not)")
        inv_path = sandbox / "scripts/lib/stack-services.json"
        original = inv_path.read_text(encoding="utf-8")
        doc = json.loads(original)
        (sandbox / SEED_TARGET).rename(sandbox / SEED_RENAMED)
        doc["projects"]["search"]["file"] = SEED_RENAMED          # field fixed ...
        inv_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")  # ... command not
        red2 = durable_checks.run(sandbox, [from_registry])
        rc2, out2 = run_capture(from_registry["check"], sandbox)
        check("a HALF-fixed rename is still RED - the two fields must agree",
              red2["failed"] == 1 and rc2 == 1 and "'compose' -f is" in out2,
              first_line(out2))

        inv_path.write_text(original, encoding="utf-8")
        (sandbox / SEED_RENAMED).rename(sandbox / SEED_TARGET)
        check("reverting seed B returns the check to GREEN",
              durable_checks.run(sandbox, [from_registry])["failed"] == 0)

        print("\n--- 5. the counterfactual: nothing that already existed catches either seed")
        cfg = (REPO / "scripts/checks/check-project-configs.ps1").read_text(encoding="utf-8")
        inv_block = cfg.split("$invPath")[-1] if "$invPath" in cfg else ""
        check("the pre-commit inventory verifier reads planes[] rows, never projects[*].file",
              "$inv.planes" in inv_block and "$inv.projects" not in cfg and "env_file" not in cfg)
        watchdog = (REPO / "scripts/checks/stack-watchdog.ps1").read_text(encoding="utf-8")
        check("the field IS consumed at repair time - a stale value fails on the host, not at commit",
              "$Proj.file" in watchdog and "$Proj.env_file" in watchdog)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + str(_passed) + "/" + str(_passed + _failed) + " seeded-regression checks passed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
