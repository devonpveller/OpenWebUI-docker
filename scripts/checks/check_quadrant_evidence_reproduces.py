#!/usr/bin/env python3
"""DURABLE CHECK: a run record's verdict must be RE-DERIVABLE from the evidence it kept.

    python scripts/checks/check_quadrant_evidence_reproduces.py [--auto] [<results-dir> ...]

    --auto  discover every results set under the DISCOVERY_ROOTS defined below (a directory
            holding `*/record.json`), AND hold this checkout to the evidence it COMMITS.
            This is the form BANKED in the durable-check registry, so the check runs against
            whatever evidence a machine has without anyone editing a path. The roots are
            named in ONE place and every message about them is derived from it.

    exit 0  every admissible record's acceptance re-ran in its retained workspace and
            returned the exit code the record claims - and every record this repository
            COMMITS was on disk to be re-run (including the two genuinely vacuous cases
            below, each of which says WHICH one it is rather than implying coverage)
    exit 1  at least one record's verdict cannot be reproduced from what it kept, or the
            committed evidence this checkout is supposed to hold is missing from it
    exit 2  misuse (a named directory that does not exist, an unreadable record)

WHAT IS SKIPPED, and why that is not a loophole. A record that `record.admit` REFUSES is not
part of any comparison - the report renders it as REFUSED and it contributes to nothing - so
re-deriving its verdict would be re-deriving a number nobody may use. Skipped records are
COUNTED and PRINTED with the reason. On this machine that covers the pre-2026-08-30 results
set, whose records name no venue and are refused for that.

"NO EVIDENCE HERE" AND "THE COMMITTED EVIDENCE IS GONE" ARE DIFFERENT FACTS - added
2026-08-31, after a verifier found the hole in this file's own fix. Until then `--auto`
returned 0 whenever it discovered nothing, and it SAID so ("that is a vacuous pass"). The
disclosure was honest and still insufficient, because U4's evidence is COMMITTED at a known
path: `rm -rf documentation/evidence/dfu-u4/` - the precise loss this check exists to
prevent - made `--auto` find nothing and pass, while WALKTHROUGH.md credited it with
reaching that set. Only `cli.py report --results-dir <committed>` went red.

A checkout is not an arbitrary machine. It either holds the run records its own index tracks
or it has LOST them, and git is the authority on which. So `--auto` reads the expectation
from `git ls-files` - not from a path list in this file, which would drift out of step with
what the repository actually carries - and reds on any tracked `record.json` that is not on
disk, naming each one.

TWO CASES STAY GENUINELY VACUOUS, and each prints which one it is:
  * the tree is NOT a git checkout (an exported tarball, a copied directory) - nothing can
    be expected of it, so nothing is;
  * the tree IS a git checkout whose index tracks no run records under the discovery roots -
    a repository that banks no evidence, which this one was until 2026-08-31.
What this does NOT close: `git rm`-ing the evidence and committing that clears the
expectation too. No check reading the index can tell that from a legitimate removal - it is
a diff a reviewer reads. The boundary is drawn at the index and stated here rather than
overclaimed, because the failure that actually happened was a worktree thrown away, not a
commit.

A record this repository COMMITS is held to a second rule: it must be ADMISSIBLE. A
committed record that `record.admit` refuses enters no comparison, so committing it banks a
directory nobody may cite - that is red, not "skipped". The skip below stays for WORKING
evidence under `.quadrant/`, which is where the pre-venue records live.

WHERE THIS CHECK CAME FROM - a TESTER finding, in a prior round, on real work.

  round     dark-factory U4, verification round of 2026-08-30
  source    a verifier re-checking `work/u4close`'s four-quadrant comparison
  finding   "For the two `target:self` cells the preserved workspace holds only CHANGED
             files, so `guards.py unmodified` now fails there (`test_slugify.py` MISSING) -
             those cells' acceptance is NOT reproducible from the retained evidence, though
             it passed when run."

The finding did not block anything. The runs were real, the acceptance had genuinely passed,
and every gate the harness owned was green - `record.admit` checks that `evidence.workspace`
EXISTS, which it did. That is precisely the shape PLAN section 0's A5 says evaporates: a true
finding that costs nothing to ignore, on an item that is already passing.

THE GENERAL RULE IT BANKS, which is bigger than the bug that produced it:

    A record of a check is not a check. If the artifacts a run kept cannot re-produce the
    verdict the run claims, the verdict is a self-report with a directory next to it.

PLAN section C.7 makes the audit trail the deliverable's twin, because an unattended run is
audited afterwards from its records rather than from its diffs. An audit that cannot re-run
anything is a reading exercise. This check is the difference.

WHAT IT DOES, exactly. For every `record.json` under each results directory:
  * skip records that are not outcomes (`not_run`, `error`) - they claim no verdict;
  * locate the retained workspace - the `workspace/` BESIDE the record if there is one,
    otherwise the absolute path the record names;
  * re-run every `acceptance[*].check` command IN that workspace;
  * require the exit code to equal the one the record recorded.

WHY THE SIBLING WINS OVER THE RECORDED PATH - found by the U3 drill on its first run, which
is the drill earning its keep. `evidence.workspace` is an ABSOLUTE path written at run time.
Follow it blindly and this check re-runs against wherever that path points TODAY: the U3
drill copied a results set into a sandbox, seeded regressions into the COPY, and the check
read straight past them into the untouched original and reported everything reproducible.
Evidence that has been archived, copied or moved is exactly the evidence an audit reads, so
the check verifies the tree it was handed.

Nor is the recorded path a safe FALLBACK when the sibling is missing: the drill's third seed
deleted a retained workspace outright, the absolute path still resolved to the original it
had been copied from, and the deletion read as reproducible - the same defect wearing a
different hat. So when a record names a workspace inside its own run directory (which is how
every record this harness writes is shaped), the sibling is AUTHORITATIVE and its absence is
the finding. The recorded path is used only for records shaped some other way.

WHAT IT DOES NOT DO. It does not re-dispatch a runner, and it does not judge whether the
verdict was RIGHT - only whether the retained evidence still yields it. A check whose
acceptance command reaches the network or a live plane will be re-run by this, which is a
reason to keep acceptance commands hermetic, not a reason to weaken the check.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

OUTCOME_STATUSES = {"completed", "failed"}


def _records(results_dir: Path) -> List[Path]:
    return sorted(results_dir.glob("*/record.json"))


def _local_guards() -> str:
    """`{guards}` as THIS checkout can invoke it."""
    return '"{}" "{}"'.format(
        sys.executable,
        _repo_root() / "scripts" / "agent-harness" / "quadrant" / "guards.py")


def _runnable(entry: Dict[str, Any], item_id: str) -> Tuple[str, str]:
    """(command to re-run, how it was derived) for one acceptance entry.

    WHY THE RECORDED COMMAND IS NOT ALWAYS THE ONE TO RUN — measured 2026-08-31, on the U4
    evidence set this check exists to audit. `item.expand` writes the EXACT command that
    ran, which is right for "re-run it yourself" and is an absolute path to an interpreter
    and to `guards.py` inside the worktree that produced it. That worktree is gone the
    moment the branch merges. Re-running the recorded string in a fresh clone therefore
    fails on a missing file — the verdict reads NOT REPRODUCIBLE for a reason that is about
    the path, not about the evidence, which is the false red that teaches a reader to stop
    trusting the check.

    So a record may also carry `check_template`: the UNEXPANDED criterion from the item
    (`{guards} tests --item u4-baseline`). When it is present it is expanded against THIS
    checkout and run; `check` is left untouched in the record as the historical fact of what
    executed. When it is absent — every record written before 2026-08-31 — the recorded
    command is used and the derivation says so, because a check that silently guessed a
    substitution would be inventing evidence rather than re-deriving it.
    """
    tmpl = str(entry.get("check_template") or "").strip()
    if tmpl:
        return (tmpl.replace("{guards}", _local_guards()).replace("{item}", item_id),
                "template re-expanded in this checkout")
    return str(entry.get("check") or ""), "the command recorded at run time (machine-bound)"


def verify_record(path: Path) -> Tuple[bool, List[str]]:
    """(ok, problems) for ONE record. Problems are sentences, not codes."""
    try:
        rec: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, [f"{path}: unreadable record ({exc})"]

    status = str(rec.get("status") or "")
    if status not in OUTCOME_STATUSES:
        return True, []

    problems: List[str] = []
    ws = str((rec.get("evidence") or {}).get("workspace") or "")
    if not ws:
        return False, [f"{path}: status '{status}' with no evidence.workspace"]
    recorded = Path(ws)
    sibling = path.parent / recorded.name
    if recorded.parent.name == path.parent.name:
        # The record names a workspace inside ITS OWN run directory - which is how every
        # record this harness writes is shaped. The directory beside the record is therefore
        # authoritative and its ABSENCE is the finding. Falling back to the absolute path
        # here is what let a deleted workspace read as reproducible: the path still resolved,
        # to the original the copy was made from.
        wsp = sibling
        if not wsp.is_dir():
            return False, [f"{path}: the run directory kept no '{recorded.name}/' beside its "
                           f"record (it recorded {ws}). There is nothing to re-run the "
                           f"verdict in."]
    else:
        wsp = recorded
        if not wsp.is_dir():
            return False, [f"{path}: retained workspace is gone: {ws}"]

    acc = rec.get("acceptance") or []
    if not acc:
        return False, [f"{path}: status '{status}' with no acceptance entries to reproduce"]

    item_id = str(rec.get("item") or "")
    for i, a in enumerate(acc):
        cmd, how = _runnable(a or {}, item_id)
        claimed = (a or {}).get("exit_code")
        if not cmd or not isinstance(claimed, int):
            problems.append(f"{path}: acceptance[{i}] has no command or no exit code to re-derive")
            continue
        proc = subprocess.run(cmd, shell=True, cwd=str(wsp), capture_output=True,  # noqa: S602
                              encoding="utf-8", errors="replace")
        if proc.returncode != claimed:
            out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            tail = out[-3:] if out else ["(no output)"]
            problems.append(
                f"{path}: acceptance[{i}] recorded exit {claimed}, re-running it in the "
                f"retained workspace gives exit {proc.returncode}. The verdict is not "
                f"re-derivable from what the run kept.\n"
                f"    $ {cmd}   [{how}]\n" + "\n".join(f"    {ln}" for ln in tail))
    return (not problems), problems


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[2]


#: Where `--auto` looks for results sets. `.quadrant/` is the WORKING location (gitignored,
#: per-checkout, thrown away with the worktree that made it); `documentation/evidence/` is
#: the COMMITTED one. Both are searched, and the second is why the banked check still has
#: something to audit in a fresh clone — added 2026-08-31 after the four-quadrant comparison
#: that closed U4 was destroyed with its worktree, leaving a walkthrough row claiming 4/4
#: over a `report` that answered COMPARED 0/4.
DISCOVERY_ROOTS = (".quadrant", "documentation/evidence")


def _discover(root: Path) -> List[Path]:
    """Every results SET under a discovery root: a directory that holds `*/record.json`."""
    out: List[Path] = []
    for rel in DISCOVERY_ROOTS:
        base = root / rel
        if not base.is_dir():
            continue
        # One level of nesting under the root, then the set itself - `documentation/evidence`
        # groups sets by the item they belong to (`dfu-u4/quadrant`), `.quadrant` does not.
        for d in sorted(base.rglob("*")):
            if d.is_dir() and any(d.glob("*/record.json")):
                out.append(d)
    return out


def _roots_phrase() -> str:
    """The discovery roots as prose, DERIVED from DISCOVERY_ROOTS.

    Every message that names where this check looks is built from here. The stale sibling
    that made this necessary: the roots list gained `documentation/evidence` on 2026-08-31
    and both the docstring and the "nothing to check" line went on saying `.quadrant/`, so
    the sentence a future auditor reads named the wrong search root. A third root added
    later cannot desynchronise the text again, because there is no second copy of it.
    """
    names = [r.rstrip("/") + "/" for r in DISCOVERY_ROOTS]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " or " + names[-1]


def _committed_records(root: Path) -> Tuple[List[str], str]:
    """(repo-relative `*/record.json` paths this checkout TRACKS, why-there-is-no-expectation).

    The expectation is asked of git, so it is whatever the repository actually carries. A
    non-empty first element means "these files are supposed to be here"; an empty one with a
    reason means the tree cannot be held to anything, and that reason is PRINTED rather than
    quietly treated as coverage.

    The INDEX, not HEAD: `rm -rf documentation/evidence/` leaves the index untouched, and
    that is the loss that actually happened - so it must be caught the moment it happens,
    not only once someone commits it.

    THE ENCLOSING-REPOSITORY TRAP, found by this fix's own guard test on its first run and
    worth more than the test that found it. `git -C <dir> ls-files` does not fail outside a
    repository - it SEARCHES UPWARDS, and on this machine `C:/Users/yamao` is itself a git
    repo, so a temporary directory under the home tree answered "exit 0, no tracked records"
    and read as a repository that banks no evidence. Worse than the wrong message: paths
    from `--full-name` are relative to whatever toplevel git found, so an enclosing repo
    that DID track records would have had them resolved against the wrong root. The
    expectation is only meaningful when the tree being checked IS the repository, so that is
    asserted rather than assumed.
    """
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, encoding="utf-8", errors="replace")
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--full-name", "-z", "--", *DISCOVERY_ROOTS],
            capture_output=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"git is not runnable here ({exc}), so nothing can be expected of this tree"
    if top.returncode != 0 or proc.returncode != 0:
        detail = [ln for ln in ((top.stderr or "") + (proc.stderr or "")).strip().splitlines()
                  if ln.strip()]
        return [], ("this tree is not a git checkout, so nothing can be expected of it - "
                    + (detail[0] if detail else f"git rev-parse exited {top.returncode}"))
    found = (top.stdout or "").strip()
    try:
        same = bool(found) and Path(found).resolve() == root.resolve()
    except OSError:
        same = False
    if not same:
        return [], ("this tree is not a git checkout - git searched upwards and answered "
                    f"with the enclosing repository at {found or '(none)'}, whose index says "
                    "nothing about this directory")
    paths = sorted(x for x in (proc.stdout or "").split("\0") if x.endswith("/record.json"))
    if not paths:
        return [], ("this checkout's index tracks no run records under " + _roots_phrase()
                    + " - it is a repository that banks no evidence")
    return paths, ""


def _admissible(rec: Dict[str, Any]) -> Tuple[bool, str]:
    """Would this record enter a comparison at all? Uses the harness's OWN gate.

    Imported lazily so the check still runs on a tree where the harness package is absent -
    in which case every record is treated as admissible, which is the conservative direction.
    """
    try:
        sys.path.insert(0, str(_repo_root() / "scripts" / "agent-harness"))
        from quadrant import record as record_mod  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return True, ""
    problems = record_mod.admit(rec, item_digest=str(rec.get("item_digest") or ""),
                                venue=str((rec.get("venue") or {}).get("name") or ""))
    return (not problems), "; ".join(problems)


def main(argv: List[str]) -> int:
    dirs = [Path(a) for a in argv if not a.startswith("-")]
    auto = "--auto" in argv
    repo = _repo_root()
    committed: List[str] = []
    vacuous_because = ""
    if auto:
        # THE ASYMMETRY THIS CLOSES. "Discovered nothing" used to be indistinguishable from
        # "lost everything". Ask git what this checkout is SUPPOSED to hold first, and red
        # before the vacuous branch below can be reached.
        committed, vacuous_because = _committed_records(repo)
        missing = [c for c in committed if not (repo / c).is_file()]
        if missing:
            print(f"MISSING COMMITTED EVIDENCE  this checkout's index tracks "
                  f"{len(committed)} run record(s) under {_roots_phrase()}; "
                  f"{len(missing)} of them are not on disk:")
            for c in missing:
                print(f"    {c}")
            print(f"\nThat is not a machine with no evidence - it is a checkout that has "
                  f"LOST the evidence its own index says it carries, which is the exact "
                  f"failure this check exists to catch. Restore it "
                  f"(git checkout -- {DISCOVERY_ROOTS[-1]}), or remove it deliberately in a "
                  f"commit a reviewer reads.")
            return 1
        dirs += _discover(repo)
    if not dirs:
        print(f"no results set given and none discovered under {_roots_phrase()} - nothing "
              f"to check. That is a vacuous pass, and it is printed rather than implied.")
        if vacuous_because:
            print(f"    genuinely vacuous, and this is WHICH case: {vacuous_because}.")
        return 0

    checked = 0
    committed_audited = 0
    committed_abs = {(repo / c).resolve() for c in committed}
    skipped: List[str] = []
    all_problems: List[str] = []
    for d in dirs:
        if not d.is_dir():
            print(f"no such results directory: {d}")
            return 2
        recs = _records(d)
        if not recs:
            print(f"{d}: no records")
            continue
        for r in recs:
            try:
                rec = json.loads(r.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                all_problems.append(f"{r}: unreadable record ({exc})")
                continue
            if rec.get("status") not in OUTCOME_STATUSES:
                continue
            is_committed = r.resolve() in committed_abs
            ok_adm, why = _admissible(rec)
            if not ok_adm:
                if is_committed:
                    # Skipping this one would bank a directory nobody may cite. A COMMITTED
                    # record that enters no comparison proves nothing, and reporting it as
                    # "skipped" next to exit 0 is the same vacuity one layer down.
                    all_problems.append(
                        f"{r}: this record is COMMITTED to the repository and record.admit "
                        f"REFUSES it ({why[:160]}). It can enter no comparison, so what is "
                        f"committed here is a directory nobody may cite.")
                else:
                    skipped.append(f"{r.parent.name}: refused at admission ({why[:120]})")
                continue
            ok, problems = verify_record(r)
            if problems:
                all_problems += problems
            if ok:
                checked += 1
                if is_committed:
                    committed_audited += 1

    for sk in skipped:
        print(f"SKIPPED (not in any comparison)  {sk}")

    for p in all_problems:
        print(f"NOT REPRODUCIBLE  {p}")
    if all_problems:
        print(f"\n{len(all_problems)} verdict(s) cannot be re-derived from the retained evidence.")
        return 1
    print(f"{checked} outcome record(s) re-derived their verdict from the evidence they kept "
          f"(re-run in the workspace beside each record, not at whatever absolute path it "
          f"was written with); {len(skipped)} skipped as inadmissible")
    if auto and committed:
        print(f"the {len(committed)} run record(s) this checkout COMMITS are all on disk; "
              f"{committed_audited} of them claim a verdict and every one re-derived it")
    elif auto and vacuous_because:
        print(f"nothing was expected of this tree: {vacuous_because}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
