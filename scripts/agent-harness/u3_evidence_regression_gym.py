#!/usr/bin/env python3
"""U3's GYM RUN: a seeded regression, caught by a check born from a TESTER finding.

    python scripts/agent-harness/u3_evidence_regression_gym.py [--keep] [--repo <arena>]

    exit 0  every seed was caught by at least one gate, and WHICH gate was MEASURED
    exit 1  a seed no gate caught, or the pristine copy went red
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
  REAL  the counterfactual: `admit_against` EXECUTES the pre-existing gate (record.admit)
        against every seed instead of asserting what it would say - AND against the seeded
        COPIES, which is a correction. See "the counterfactual measured the wrong tree".
  NOT   an agent-org / gym_runner.py scenario cycle. No worker built the regression and no
        PR was scored: the regression is seeded deterministically by this file so the loop
        is RE-RUNNABLE rather than a transcript. The gym's own runner drives the org through
        a scenario; this drives a CHECK through a regression, in the gym's arena.
  NOT   a claim about any runner. The seeds are edits to retained evidence.

--------------------------------------------------------------------------------------
THE COUNTERFACTUAL MEASURED THE WRONG TREE (found by a verifier, 2026-08-30, round 7)

`record.admit` resolves `evidence.workspace` as the ABSOLUTE path the record was written
with. The sandbox is a COPY, so every seed edited the copy while admission walked back to
the untouched originals under `.quadrant/gym-runs` and found them all intact. The drill
therefore printed "0 are also caught by the gate that already existed" for every seed
INCLUDING seed C - the one this file's own docstring says the pre-existing gate is expected
to catch. A gate pointed at a different directory measures nothing about this one.

That is the SAME CLASS as this drill's first counterfactual, which "proved" that nothing
pre-existing caught the seeds by GREPPING; the fix then was to execute the gate, and the
fix now is to execute it against the tree the seeds are in.

`copy_run` is the correction: a run directory is copied AND its record's `evidence.*` paths
are rewritten to point inside the copy, so the sandbox is self-consistent - an evidence set
as an auditor actually receives one, describing the tree in hand rather than a machine it
was produced on. The originals are never touched.

WHAT THAT COSTS, said rather than glossed: with the paths rewritten, this drill no longer
demonstrates the banked check's robustness to a STALE absolute path (the check prefers the
`workspace/` beside the record; the two now agree). That property has its own named
regression tests in `test_evidence_reproduces.py`. The drill's job is the counterfactual,
and a counterfactual that inspects a directory it does not seed is not one.

AND THE NUMBER GOT SMALLER, which is the point. Seed C is CAUGHT by the pre-existing gate
(`record.admit` refuses a record whose `evidence.workspace` is not on disk), so the banked
check's unique contribution is 2 of 3 seeds, not 3 of 3. The banked check SKIPS seed C, and
that is correct rather than a miss: a record admission refuses is in no comparison, so
re-deriving its verdict would be re-deriving a number nobody may use - the check says so in
its own docstring and prints the skip with its reason.

So the two gates are complementary, and the drill's pass condition says which: admission
catches "the evidence is gone", the banked check catches "the evidence is there and no
longer yields the verdict". The second is the shape of the tester finding this all came
from, and it is the shape nothing caught before.
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

#: WHERE THIS DRILL LOOKS FOR THE REAL RUN EVIDENCE IT SEEDS. `.quadrant/gym-runs` is the
#: WORKING location - gitignored, per-checkout, thrown away with the worktree that produced
#: it; `documentation/evidence/` is the COMMITTED one. Only the first was searched until
#: 2026-09-02, and that is why this drill answered `NO EVIDENCE`, exit 2, in every checkout
#: but the one that ran the arena dispatch: its gym records had been deleted with their
#: worktree - byte for byte the loss that destroyed U4's quadrant comparison, where
#: .gitignore's "run artifacts, not source" rule took the audit trail with it. This is U4's
#: own fix applied to the drill that first earned the rule; see
#: check_quadrant_evidence_reproduces.py, whose DISCOVERY_ROOTS gained
#: `documentation/evidence` on 2026-08-31 for exactly this reason.
#:
#: WIDENING THE SEARCH DOES NOT WIDEN WHAT IS ADMISSIBLE, which is the whole reason it is
#: safe: every candidate is still filtered on `record.venue.name == <the configured venue>`,
#: and the venue gate above is untouched - this drill still REFUSES to run outside the arena.
SOURCE_ROOTS = (".quadrant/gym-runs", "documentation/evidence")


def _source_roots_phrase() -> str:
    """The source roots as prose, DERIVED from SOURCE_ROOTS so no second copy can drift."""
    names = [r.rstrip("/") + "/" for r in SOURCE_ROOTS]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " or " + names[-1]

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


def copy_run(src: Path, dest: Path) -> None:
    """Copy ONE run directory into the sandbox and make the copy describe ITSELF.

    THE DEFECT THIS FIXES. `record.admit` reads `evidence.workspace` as written - an
    absolute path into `.quadrant/gym-runs`. A plain `copytree` therefore produced a
    sandbox whose records still pointed at the originals, so the counterfactual measured a
    gate aimed at an untouched directory: every seed came back "missed", including the one
    seed the gate is expected to catch.

    Only paths that live INSIDE the source run directory are rewritten, and only in the
    copy. A record that names a workspace somewhere else is left exactly as it is: that
    record's shape is a fact about how it was produced, and rewriting it would be inventing
    evidence rather than relocating it.
    """
    shutil.copytree(src, dest)
    rp = dest / "record.json"
    if not rp.is_file():
        return
    rec = json.loads(rp.read_text(encoding="utf-8"))
    ev = rec.get("evidence")
    if not isinstance(ev, dict):
        return
    src_res = src.resolve()
    changed = False
    for key, val in list(ev.items()):
        if not isinstance(val, str) or not val:
            continue
        try:
            rel = Path(val).resolve().relative_to(src_res)
        except (ValueError, OSError):
            continue
        ev[key] = str(dest / rel)
        changed = True
    if changed:
        rec["evidence"] = ev
        rec.setdefault("notes", []).append(
            "sandbox copy: evidence paths rewritten to this directory by "
            "u3_evidence_regression_gym.copy_run, so the gate under test reads the tree it "
            "was handed. The originals are unchanged.")
        rp.write_text(json.dumps(rec, indent=2), encoding="utf-8")


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
    """THE PRE-EXISTING GATE, executed rather than assumed, AGAINST THE TREE IT IS HANDED.

    Two rounds of the same lesson are baked into this one function.

    The u3gym round's, in its own words: the first version of that drill printed "the
    counterfactual: nothing that already existed catches either seed" and proved it by
    GREPPING. The grep was true and the conclusion was false. So this runs `record.admit` -
    the gate that was green while the finding was true - against every seed.

    Round 7's, found by a verifier: running the real gate is not enough if it is pointed
    somewhere else. `record.admit` follows each record's ABSOLUTE `evidence.workspace`, so
    against a plain copy it walked back to the untouched originals and reported every seed
    missed - including seed C, which this file's own docstring says the gate is expected to
    catch. `copy_run` rewrites the copy's evidence paths into the copy; this function is
    then measuring the seeds.
    """
    problems: List[str] = []
    schema = matrix_mod.schema()
    for rp in sorted(sandbox.glob("*/record.json")):
        rec = json.loads(rp.read_text(encoding="utf-8"))
        problems += [f"{rp.parent.name}: {p}"
                     for p in record_mod.admit(rec, item_digest=str(rec.get("item_digest") or ""),
                                               venue=str((rec.get("venue") or {}).get("name") or ""),
                                               schema=schema, record_dir=rp.parent)]
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
    rather than granting the banked check credit for it.

    It did grant it credit, for one round: while the sandbox copies still carried the
    originals' absolute paths, `record.admit` found the ORIGINAL workspace present and
    reported this seed missed too. Fixed by `copy_run`; the honest count is now 2 of 3."""
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
    usable, seen = [], set()
    for root in SOURCE_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for rp in sorted(base.rglob("record.json")):
            d = rp.parent
            try:
                rec = json.loads(rp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("status") not in ("completed", "failed"):
                continue
            # THE VENUE FILTER IS WHAT MAKES A WIDER SEARCH SAFE, and it is unchanged: a
            # record is usable only if IT SAYS it came from this venue. Widening WHERE the
            # drill looks therefore cannot widen WHAT it seeds from - a `workspace`-venue
            # run, or a pre-venue one, is refused here however many roots are searched.
            if str((rec.get("venue") or {}).get("name") or "") != v.name:
                continue
            # Two roots can hold runs with the same directory name; the sandbox copy needs a
            # unique destination or copytree raises on the second one.
            name, n = d.name, 1
            while name in seen:
                n += 1
                name = "{0}-{1}".format(d.name, n)
            seen.add(name)
            usable.append((name, d, root))
    if not usable:
        print("NO EVIDENCE: none of {0} under {1} holds an outcome record from venue "
              "'{2}'. Run the quadrant comparison in the arena first - this drill seeds a "
              "copy of REAL run evidence and will not fabricate one.".format(
                  _source_roots_phrase(), REPO, v.name))
        return 2
    from collections import Counter
    tally = Counter(root for _, _, root in usable)
    print("source     : {0} outcome record(s) from {1}".format(
        len(usable), ", ".join("{0} in {1}/".format(c, r) for r, c in sorted(tally.items()))))

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
        for name, d, _root in usable:
            copy_run(d, pristine / name)
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
            for name, d, _root in usable:
                copy_run(d, sb / name)
            what = fn(sb)
            rc, out = check_against(sb)
            adm_ok, adm_why = admit_against(sb)
            rows.append({"seed": label, "what": what, "check": rc,
                         "pre_existing": adm_ok, "pre_why": adm_why, "out": out})
            # A seed is a FAILURE only when NO gate caught it. Requiring the banked check to
            # catch every seed was the assertion that hid the measurement: it can only hold
            # while the counterfactual is pointed at a directory the seeds are not in.
            if rc == 0 and adm_ok:
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
        only_this = [r for r in seeded if r["check"] != 0 and r["pre_existing"]]
        pre_caught = [r for r in seeded if not r["pre_existing"]]
        neither = [r for r in seeded if r["check"] == 0 and r["pre_existing"]]
        print(f"\n  {len(only_this)} of {len(seeded)} seeds are caught ONLY by the banked "
              f"check - that set is its value.")
        print(f"  {len(pre_caught)} of {len(seeded)} are caught by the gate that ALREADY "
              f"existed (record.admit), which the banked check then skips as inadmissible - "
              f"correctly: a record admission refuses is in no comparison.")
        print(f"  {len(neither)} of {len(seeded)} are caught by NEITHER.")
        print("  Stating the second number is the point of measuring a counterfactual "
              "rather than asserting one. It read 0 until the seeds were measured in the "
              "tree they were seeded into - see copy_run.")

        for r in neither:
            print(f"\nMISSED BY EVERY GATE: seed '{r['seed']}'.\n{r.get('out', '')}")
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
    print("\nU3 GYM RUN: every seeded regression was caught, in the arena, with the "
          "counterfactual measured on the seeded copies - and the report says which gate "
          "caught which. The seeds nothing already caught are the durable check's, and it "
          "was born from a tester finding in a prior round.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
