#!/usr/bin/env python3
"""SEEDED-REGRESSION DRILL - dark-factory-unification U3's Validated-by column.

    "a seeded regression must be caught by a check born from a *tester* finding in a
     prior round (gym-007's shape, new source)"

gym-007's shape (ORCHESTRATION-DESIGN section 10, agent-org): a recurring OPERATOR review
finding was captured once as a durable executable check, and the next round's org was
forced to ship the behaviour the goal never asked for. The recurrence broke. THE NEW
SOURCE this drill supplies is a TESTER finding - the extension U3 makes. Testers produce
findings the operator never sees, and the ones that do NOT block a merge are the ones
that evaporate (PLAN section 0, A5): the item passes, the finding is true, and nothing
carries it forward.

THE FINDING (real, prior round, not invented for this drill):
  item      watchdog-fix, attempt 1
  tester    wt-tester-3, 2026-08-28   verdict PASS
  evidence  .git/agent-worktrees/queue/watchdog-fix.attempt1.evidence.md, section C item 4
  said      "the projects map's file and env_file fields [are] NOT covered by that
             verifier ... A plane compose-file RENAME would silently stale the file field"

WHAT IS REAL HERE AND WHAT IS NOT - stated because a claimed gym run that was a local
simulation is exactly the over-claim section C.7 exists to prevent:
  REAL  the finding (a prior round's tester evidence file, quoted above)
  REAL  the check (scripts/checks/check_stack_services_paths.py, runs against this tree)
  REAL  the bank (scripts/agent-harness/durable_checks.py -> the SHARED git-dir registry)
  REAL  the regression (a file rename, performed on disk) and the red/green (exit codes)
  REAL  the counterfactual - sections 3-6 EXECUTE the pre-existing checks against every
        seed rather than asserting what they would do (see below)
  NOT   an agent-org / ai-orchestration-gym org cycle. No worker built the regression and
        no PR was scored. The regression is seeded deterministically by this file, in a
        disposable sandbox, so the loop is RE-RUNNABLE rather than a transcript.

WHY THE COUNTERFACTUAL RUNS INSTEAD OF ASSERTING (2026-08-30, corrected). This drill's
first version printed "the counterfactual: nothing that already existed catches either
seed" and proved it by GREPPING check-project-configs.ps1 for `$inv.projects`. The grep is
true; the conclusion drawn from it was FALSE. A different pre-existing script,
check-watchdog-repair-targets.ps1, resolves every watchdog-managed container through
projects[*].file and Test-Paths it, so it catches SEED A outright - a verifier disproved
the claim by running that script against a seeded sandbox, which is section 0 A6 landing on
this file's own author. The counterfactual is now MEASURED: every seed is run against the
pre-existing readers of the inventory and the drill asserts the resulting matrix, so the
claim cannot rot the way a sentence in a docstring can.

    seed                                       pre-existing        this check
    A  rename, inventory untouched              CAUGHT              caught
    B  half-fixed rename (field yes, -f no)     missed              caught
    C  rename in a project with no managed
       container (agent-org)                    missed              caught
    D  env_file points at nothing               missed              caught

The value of the banked check is therefore B, C and D - not A. That is narrower than the
original claim and it is what a command actually shows.

Runs anywhere python and PowerShell are available (the pre-existing checks are .ps1, so a
host without PowerShell cannot measure the counterfactual - the drill FAILS that check
rather than skipping it silently). Prints named checks and a count, like
verify-merge-protocol.ps1 and agent-org's tests/test_org_drill.py.
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
BACKSLASH = chr(92)

CHECK_CMD = "python scripts/checks/check_stack_services_paths.py"
CHECK_WHY = (
    "stack-watchdog.ps1 builds every plane repair command from projects[*].file and "
    "env_file in scripts/lib/stack-services.json. check-project-configs.ps1 (the pre-commit "
    "verifier) reads the planes[] container rows only, and only when a .yml is staged; "
    "check-watchdog-repair-targets.ps1 does Test-Path projects[*].file, but only for the "
    "containers the watchdog claims to self-heal, and it never reads env_file nor the -f "
    "argument inside the compose command. So a half-fixed rename, a rename in a project no "
    "managed container points at, and any env_file drift all pass everything that exists. "
    "Found by wt-tester-3 on watchdog-fix attempt 1 (2026-08-28, evidence section C item 4) "
    "- a PASSING review, so nothing else carried it."
)
SOURCE_ITEM = "watchdog-fix attempt 1 / wt-tester-3 evidence section C item 4"

SEED_TARGET = "search/docker-compose.yml"      # the plane whose compose file gets renamed
SEED_RENAMED = "search/compose.yml"
# A project with NO watchdog-managed container. agent-org has zero rows in the inventory's
# planes[] map, so nothing resolves to it - which is exactly why the pre-existing
# repair-target check cannot see a rename here.
SEED_C_TARGET = "agent-org/docker/docker-compose.yml"
SEED_C_RENAMED = "agent-org/docker/compose.yml"

# The pre-existing script that reads projects[*].file. Copied into the sandbox and RUN
# there; nothing below is a claim about what it would do.
PREEXISTING = ("powershell -NoProfile -ExecutionPolicy Bypass -File "
               "scripts/checks/check-watchdog-repair-targets.ps1 -SkipDocker")

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


def have_powershell():
    return shutil.which("powershell") is not None or shutil.which("pwsh") is not None


def build_sandbox(dest):
    """A disposable tree carrying exactly what the checks read. The compose-file list is
    DERIVED from the inventory, so the sandbox cannot drift away from what it stands in for.

    The real .env is NEVER copied. It used to be (env_file values were followed literally),
    which put live secret values under %TEMP% for the length of every run for no benefit:
    rule 4 of the check accepts <path>.example, so the example file exercises it."""
    inv = json.loads((REPO / "scripts/lib/stack-services.json").read_text(encoding="utf-8"))
    wanted = ["scripts/lib/stack-services.json",
              "scripts/checks/check_stack_services_paths.py",
              # the pre-existing reader, so the counterfactual can be RUN not asserted
              "scripts/checks/check-watchdog-repair-targets.ps1",
              "scripts/checks/stack-watchdog.ps1",
              ".gitmodules", ".env.example"]
    for row in inv.get("projects", {}).values():
        f = (row or {}).get("file")
        if f:
            wanted.append(str(f).replace(BACKSLASH, "/"))
        ef = (row or {}).get("env_file")
        if ef:
            wanted.append(str(ef).replace(BACKSLASH, "/") + ".example")  # never the real dotenv
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
        check("the sandbox carries NO real dotenv, only the .example - secrets never leave the repo",
              not (sandbox / ".env").exists() and (sandbox / ".env.example").is_file())
        check("the sandbox carries the PRE-EXISTING check, so the counterfactual is measurable",
              (sandbox / "scripts/checks/check-watchdog-repair-targets.ps1").is_file()
              and (sandbox / "scripts/checks/stack-watchdog.ps1").is_file())

        sb_green = durable_checks.run(sandbox, [from_registry])
        check("the banked check is GREEN on the untouched sandbox", sb_green["failed"] == 0)

        ps = have_powershell()
        check("PowerShell is present, so the pre-existing check is RUN and not assumed", ps)
        base_prc, base_pout = run_capture(PREEXISTING, sandbox)
        check("the pre-existing check is GREEN on the untouched sandbox too, so any later RED "
              "is the seed and not the sandbox", base_prc == 0, "exit " + str(base_prc))

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

        # THE CORRECTION. Seed A is the one seed something pre-existing DOES catch, and this
        # drill claimed otherwise until 2026-08-30.
        prc, pout = run_capture(PREEXISTING, sandbox)
        check("SEED A is ALSO caught by the pre-existing check-watchdog-repair-targets.ps1 - "
              "the earlier 'nothing else catches this' claim was FALSE for this seed",
              prc == 1 and "does not exist on disk" in pout,
              "exit " + str(prc) + ", " + str(pout.count("[FAIL]")) + " FAIL lines")

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
        prc2, pout2 = run_capture(PREEXISTING, sandbox)
        check("SEED B is MISSED by the pre-existing check - projects[*].file now resolves and "
              "nothing compares it to the -f inside the compose command",
              prc2 == 0, "exit " + str(prc2))

        inv_path.write_text(original, encoding="utf-8")
        (sandbox / SEED_RENAMED).rename(sandbox / SEED_TARGET)
        check("reverting seed B returns the check to GREEN",
              durable_checks.run(sandbox, [from_registry])["failed"] == 0)

        print("\n--- 5. SEED C: a rename in a project NO watchdog-managed container points at")
        (sandbox / SEED_C_TARGET).rename(sandbox / SEED_C_RENAMED)
        rc3, out3 = run_capture(from_registry["check"], sandbox)
        check("the banked check catches it - it walks EVERY projects[*] row",
              rc3 == 1 and "projects.agent-org.file" in out3, first_line(out3))
        prc3, pout3 = run_capture(PREEXISTING, sandbox)
        check("SEED C is MISSED by the pre-existing check - it reaches a project only through "
              "the containers the watchdog manages, and agent-org has none",
              prc3 == 0, "exit " + str(prc3))
        (sandbox / SEED_C_RENAMED).rename(sandbox / SEED_C_TARGET)

        print("\n--- 6. SEED D: env_file drift (the OTHER field the tester named)")
        doc = json.loads(original)
        doc["projects"]["search"]["env_file"] = ".env.plane"
        doc["projects"]["search"]["compose"] = \
            "docker compose -f search/docker-compose.yml --env-file .env.plane"
        inv_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        rc4, out4 = run_capture(from_registry["check"], sandbox)
        check("the banked check catches env_file drift",
              rc4 == 1 and "env_file resolves to neither" in out4, first_line(out4))
        prc4, pout4 = run_capture(PREEXISTING, sandbox)
        check("SEED D is MISSED by the pre-existing check - it never reads env_file at all",
              prc4 == 0, "exit " + str(prc4))
        inv_path.write_text(original, encoding="utf-8")
        check("reverting seed D returns the check to GREEN",
              durable_checks.run(sandbox, [from_registry])["failed"] == 0)

        print("\n--- 7. why a stale field is not merely untidy: it is consumed at repair time")
        watchdog = (REPO / "scripts/checks/stack-watchdog.ps1").read_text(encoding="utf-8")
        check("the field IS consumed at repair time - a stale value fails on the host, not at commit",
              "$Proj.file" in watchdog and "$Proj.env_file" in watchdog)
        cfg = (REPO / "scripts/checks/check-project-configs.ps1").read_text(encoding="utf-8")
        check("the PRE-COMMIT verifier reads planes[] rows only, so none of the four seeds is "
              "caught at commit time by the hook",
              "$inv.planes" in cfg and "$inv.projects" not in cfg and "env_file" not in cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + str(_passed) + "/" + str(_passed + _failed) + " seeded-regression checks passed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
