#!/usr/bin/env python3
"""U3's GYM RUN: a seeded regression, caught by a check born from a TESTER finding.

    python scripts/agent-harness/u3_evidence_regression_gym.py [--keep] [--repo <arena>]

    exit 0  every seed behaved as the drill asserts, and the counterfactual was MEASURED
    exit 1  a seed the banked check must catch was missed, or the pristine copy went red
    exit 2  the venue refused, or the drill could not find real run evidence to seed from

dark-factory-unification PLAN section 2, U3:

    "Gym: a seeded regression must be caught by a check born from a *tester* finding in a
     prior round (gym-007's shape, new source); drills green in both systems"

and section 2's preamble, which binds the first word:

    "'gym' means measured runs in `ai-orchestration-gym`, never live planes or a real
     target."

DECISIONS.md (2026-08-30, "U3 - CORRECTION") records that U3's second half is met (harness
66/66, agent-org 9/9) and routes this first half to U4's quadrants, because it is
runner-level work. This file is where it discharges.

--------------------------------------------------------------------------------------
GYM-007'S SHAPE, AND WHAT IS NEW HERE

gym-007 (agent-org, ORCHESTRATION-DESIGN section 10): a recurring OPERATOR review finding was
captured once as a durable executable check, and the next round's org was forced to ship the
behaviour the goal never asked for. The recurrence broke.

THE NEW SOURCE U3 asks for is a TESTER finding. Testers produce findings the operator never
sees, and the ones that do NOT block a merge are exactly the ones that evaporate (PLAN
section 0, A5): the item passes, the finding is true, and nothing carries it forward.

THE FINDING, real, from a prior round, quoted rather than paraphrased:

  round     dark-factory U4, verification round of 2026-08-30
  source    a verifier re-checking `work/u4close`'s four-quadrant comparison
  said      "For the two `target:self` cells the preserved workspace holds only CHANGED
             files, so `guards.py unmodified` now fails there (`test_slugify.py` MISSING) -
             those cells' acceptance is NOT reproducible from the retained evidence, though
             it passed when run."

It blocked nothing. Every gate the harness owned was green: `record.admit` requires
`evidence.workspace` to EXIST, and it did. The check born from it is
`scripts/checks/check_quadrant_evidence_reproduces.py`, which banks the general rule -
*a record of a check is not a check; the artifacts a run kept must re-produce the verdict it
claims* - and it is banked in the shared durable-check registry, so it outlives this item.

--------------------------------------------------------------------------------------
WHAT IS REAL HERE AND WHAT IS NOT - stated plainly, because a claimed gym run that was a
local simulation is the over-claim section C.7 exists to prevent.

  REAL  the finding (a prior round's verifier report, quoted above)
  REAL  the check (runs against real evidence; it goes RED on the historical `.quadrant/runs`
        records and GREEN on the gym ones - both measured, both re-runnable)
  REAL  the bank (durable_checks.add -> the SHARED git-dir registry, content-addressed)
  REAL  the evidence being seeded: a COPY of an actual quadrant run from the gym venue,
        produced by a real dispatch, not a fixture
  REAL  the venue: the sandbox is created inside the ARENA checkout, and this drill REFUSES
        to run if `quadrant.venue` does not resolve to a gym-kind repository that is not
        this one (quadrant/venue.py)
  REAL  the counterfactual: section "the counterfactual" EXECUTES the pre-existing gate
        (record.admit) against every seed instead of asserting what it would say
  NOT   an agent-org / gym_runner.py scenario cycle. No worker built the regression and no
        PR was scored: the regression is seeded deterministically by this file so the loop
        is RE-RUNNABLE rather than a transcript. The gym's own runner drives the org through
        a scenario; this drives a CHECK through a regression, in the gym's arena.
  NOT   a claim about any runner. The seeds are edits to retained evidence.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config as harness_config              # noqa: E402
import durable_checks                        # noqa: E402
from quadrant import matrix as matrix_mod     # noqa: E402
from quadrant import record as record_mod     # noqa: E402
from quadrant import venue as venue_mod       # noqa: E402

REPO = HERE.parents[1]
CHECK_CMD = "python scripts/checks/check_quadrant_evidence_reproduces.py --auto"
FINDING = (
    "A record of a check is not a check: if the artifacts a run kept cannot re-produce the "
    "verdict it claims, the verdict is a self-report with a directory next to it. Born from "
    "a tester finding (dark-factory U4 verification round, 2026-08-30): the two target:self "
    "cells retained only CHANGED files, so their acceptance - which had genuinely passed - "
    "could no longer be re-run from the evidence ('test_slugify.py: MISSING'). Every gate "
    "was green, because admission checks that the workspace EXISTS."
)


def rmtree(path: Path) -> None:
    """shutil.rmtree that survives git's read-only object files on Windows.

    Measured on the first run of this drill: the copied `workspace/.git/objects/**` are
    read-only, and `os.unlink` raises WinError 5 - which aborted the drill mid-seed and left
    the sandbox behind. A cleanup that only works on a tree nobody wrote is not a cleanup.
    """
    def _force(func, target, _exc):
        import os
        import stat
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass
    shutil.rmtree(path, onexc=_force)


def sh(cmd: List[str] | str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), shell=isinstance(cmd, str),  # noqa: S602,S603
                          capture_output=True, encoding="utf-8", errors="replace")


def check_against(sandbox: Path) -> Tuple[int, str]:
    """The BANKED check, run against one seeded copy. Returns (exit code, output)."""
    proc = sh([sys.executable, str(REPO / "scripts" / "checks"
                                  / "check_quadrant_evidence_reproduces.py"), str(sandbox)],
              cwd=REPO)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def admit_against(sandbox: Path) -> Tuple[bool, str]:
    """THE PRE-EXISTING GATE, executed rather than assumed.

    The u3gym round's lesson, in its own words: the first version of that drill printed "the
    counterfactual: nothing that already existed catches either seed" and proved it by
    GREPPING. The grep was true and the conclusion was false. So this runs `record.admit` -
    the gate that was green while the finding was true - against every seed and reports what
    it actually says.
    """
    problems: List[str] = []
    schema = matrix_mod.schema()
    for rp in sorted(sandbox.glob("*/record.json")):
        rec = json.loads(rp.read_text(encoding="utf-8"))
        problems += [f"{rp.parent.name}: {p}"
                     for p in record_mod.admit(rec, item_digest=str(rec.get("item_digest") or ""),
                                               venue=str((rec.get("venue") or {}).get("name") or ""),
                                               schema=schema)]
    return (not problems), "; ".join(problems)


# --------------------------------------------------------------------------- seeds --

def seed_a_drop_frozen(sandbox: Path) -> str:
    """THE SHIPPED SHAPE, verbatim: the retained workspace loses the frozen file."""
    for f in sandbox.glob("*-self/workspace/quadrant-item/test_slugify.py"):
        f.unlink()
        return f"deleted {f.relative_to(sandbox)}"
    for f in sandbox.glob("*/workspace/quadrant-item/test_slugify.py"):
        f.unlink()
        return f"deleted {f.relative_to(sandbox)}"
    raise RuntimeError("no retained frozen file to delete - the sandbox holds no usable run")


def seed_b_break_artifact(sandbox: Path) -> str:
    """The artifact is edited after the fact, so the recorded PASS no longer reproduces."""
    for f in sorted(sandbox.glob("*/workspace/quadrant-item/slugify.py")):
        f.write_text("def slugify(text):\n    return 'REGRESSED'\n", encoding="utf-8")
        return f"overwrote {f.relative_to(sandbox)} with a stub"
    raise RuntimeError("no retained artifact to break")


def seed_c_workspace_gone(sandbox: Path) -> str:
    """The whole workspace is removed. Included because the counterfactual must be MEASURED:
    this is the seed the pre-existing gate is expected to catch, and the drill asserts that
    rather than granting the banked check credit for it."""
    for d in sorted(sandbox.glob("*/workspace")):
        rmtree(d)
        return f"removed {d.relative_to(sandbox)}"
    raise RuntimeError("no retained workspace to remove")


SEEDS = [
    ("A  frozen file dropped from the retained workspace", seed_a_drop_frozen),
    ("B  retained artifact edited after the verdict", seed_b_break_artifact),
    ("C  retained workspace removed entirely", seed_c_workspace_gone),
]


# ----------------------------------------------------------------------------- main --

def main(argv: List[str]) -> int:
    keep = "--keep" in argv
    override = argv[argv.index("--repo") + 1] if "--repo" in argv else ""

    # 1. THE VENUE. This drill refuses outside the arena, by the same mechanism the quadrant
    #    comparison uses - a "Gym:" column is a claim about a place.
    cfg = harness_config.load(fresh=True)
    try:
        v = venue_mod.resolve(cfg, matrix_mod.schema(), harness_repo=REPO,
                              override_repo=override)
    except venue_mod.VenueConfigError as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2
    vres = venue_mod.probe(v, harness_repo=REPO)
    if not vres.ready:
        print(f"VENUE REFUSED: {vres.reason}")
        return 2
    if not v.satisfies_gym_column:
        print(f"VENUE REFUSED: venue '{v.name}' is kind '{v.kind}', which does not satisfy a "
              f"\"Gym:\" column. U3's validation names the arena.")
        return 2
    print(f"venue      : {v.name} ({v.kind}) - {v.repo} @ {v.ref}")

    # 2. THE SOURCE EVIDENCE: a real run from that venue, not a fixture.
    src = REPO / ".quadrant" / "gym-runs"
    runs = [p.parent for p in sorted(src.glob("*/record.json"))]
    usable = []
    for d in runs:
        rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
        if rec.get("status") in ("completed", "failed") and \
                str((rec.get("venue") or {}).get("name") or "") == v.name:
            usable.append(d)
    if not usable:
        print(f"NO EVIDENCE: {src} holds no outcome record from venue '{v.name}'. Run the "
              f"quadrant comparison in the arena first - this drill seeds a copy of REAL "
              f"run evidence and will not fabricate one.")
        return 2
    print(f"source     : {len(usable)} outcome record(s) from {src}")

    # 3. THE BANK. Content-addressed, so re-running this drill does not grow the registry.
    row = durable_checks.add(REPO, command=CHECK_CMD, why=FINDING,
                             source_item="u4close/u3-evidence-reproduces",
                             source="tester-finding")
    print(f"banked     : [{row['id']}] {row['check']}")
    print(f"registry   : {durable_checks.registry_path(REPO)}")

    # 4. THE SANDBOX, inside the arena.
    stamp = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = v.repo / ".gym-sandbox" / f"u3-evidence-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    print(f"sandbox    : {root}")

    rows: List[Dict[str, Any]] = []
    ok = True
    try:
        # 4a. PRISTINE first. A drill whose "red" is the sandbox being broken on arrival
        #     measures nothing, so the control runs before any seed.
        pristine = root / "pristine"
        pristine.mkdir()
        for d in usable:
            shutil.copytree(d, pristine / d.name)
        rc, out = check_against(pristine)
        adm_ok, adm_why = admit_against(pristine)
        rows.append({"seed": "-  PRISTINE copy (the control)", "what": "nothing changed",
                     "check": rc, "pre_existing": adm_ok})
        if rc != 0:
            print(f"\nCONTROL FAILED: the banked check is red on an UNSEEDED copy, so no "
                  f"result below means anything.\n{out}")
            return 1

        for label, fn in SEEDS:
            sb = root / label.split()[0]
            sb.mkdir()
            for d in usable:
                shutil.copytree(d, sb / d.name)
            what = fn(sb)
            rc, out = check_against(sb)
            adm_ok, adm_why = admit_against(sb)
            rows.append({"seed": label, "what": what, "check": rc,
                         "pre_existing": adm_ok, "pre_why": adm_why, "out": out})
            if rc == 0:
                ok = False

        # 5. THE TABLE. What each seed did, what the banked check said, and what the gate
        #    that ALREADY existed said - measured for every row.
        print("\n  seed                                                  pre-existing   this check")
        for r in rows:
            pre = "CAUGHT" if not r["pre_existing"] else "missed"
            if r["seed"].startswith("-"):
                pre = "n/a" if r["pre_existing"] else "RED?!"
            mine = "caught" if r["check"] != 0 else ("green" if r["seed"].startswith("-") else "MISSED")
            print(f"  {r['seed']:<52}  {pre:<13}  {mine}")
            print(f"     {r['what']}")

        seeded = [r for r in rows if not r["seed"].startswith("-")]
        caught_by_both = [r for r in seeded if r["check"] != 0 and not r["pre_existing"]]
        only_this = [r for r in seeded if r["check"] != 0 and r["pre_existing"]]
        print(f"\n  {len(only_this)} of {len(seeded)} seeds are caught ONLY by the banked check; "
              f"{len(caught_by_both)} are also caught by the gate that already existed.")
        print("  The banked check's value is the former set. Stating the latter is the point "
              "of measuring a counterfactual rather than asserting one.")

        for r in seeded:
            if r["check"] == 0:
                print(f"\nMISSED: seed '{r['seed']}' did not go red.\n{r.get('out', '')}")
    finally:
        if ok and not keep:
            rmtree(root)
            # And the parent, if nothing else in the arena is using it. An empty
            # `.gym-sandbox/` left behind in someone else's repository is litter with a
            # tidy name - this drill is a guest in the gym's checkout.
            try:
                if root.parent.is_dir() and not any(root.parent.iterdir()):
                    root.parent.rmdir()
            except OSError:
                pass
            print(f"\nsandbox removed ({root.name}). Pass --keep to retain it.")
        else:
            print(f"\nsandbox KEPT for diagnosis: {root}")

    if not ok:
        return 1
    print("\nU3 GYM RUN: every seeded regression was caught by a durable check born from a "
          "tester finding in a prior round, in the arena, with the counterfactual measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
