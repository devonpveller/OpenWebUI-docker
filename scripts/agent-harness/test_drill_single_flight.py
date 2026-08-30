#!/usr/bin/env python3
"""REPRODUCTION + REGRESSION GUARD for the two defects that made verify-merge-protocol.ps1
non-deterministic. Run it directly (no pytest): python scripts/agent-harness/test_drill_single_flight.py

THE INCIDENT (2026-08-30). The merge-protocol drill was cited as U3 evidence at 66/66.
Launched eight times in a row on a machine where other harness agents were live, seven
completed before the burst's wall-clock cap and produced 66, 66, 63, 59, 39, 34, 40 of 66 -
two clean of seven. A drill that fails half the time cannot stand between an item and an
unread merge, so the flakiness had to be a defect with a name rather than "the machine was
busy". It was two:

 1. NO MUTUAL EXCLUSION. The drill creates and force-deletes FIXED global names -
    `drill/verify-d`, `work/drilla`, `work/drillb`, and three worktree paths - inside the
    OPERATOR'S checkout, and its preamble deletes them unconditionally so a crashed run
    cannot wedge the next one. That preamble is also, verbatim, a second copy destroying the
    first: run 3 failed with `fatal: Needed a single revision` on `drill/verify-d` moments
    after run 3 had itself created it. A concurrent `verify-merge-protocol.ps1` (pid 137560,
    a different session's) was caught in Get-CimInstance mid-burst, so this is observed and
    not inferred.

 2. `git -C` ASCENDS. When provisioning half-fails, `$wtA`/`$wtB` are plain directories
    inside the repo rather than worktree roots. git does not error on that: it walks up and
    finds the enclosing repository, which for a drill that has done `Set-Location $repo` is
    the MAIN CHECKOUT. So `git -C $wtB rebase drill/verify-d` rebased the operator's tree.
    It was found afterwards holding .git/rebase-merge with
    head-name refs/heads/refactor/ai-stack-cleanup - a stray rebase in the one checkout the
    whole toolkit exists to keep out of.

WHAT THIS FILE ASSERTS, and why in this shape. The guards are proven by RUNNING the real
script with -LockProbe, which takes the single-flight decision and exits before touching
anything. Refs and worktrees are snapshotted around the refused call, so "it refused" is not
taken on the script's word - the test checks that nothing moved. The ascent hazard is
reproduced against a real directory, then the guard is asked about the same directory.

This is deliberately not a grep for `lease.ps1` in the drill's source: a source string proves
the line was written, never that it runs before the destructive preamble. The refused run
plus an unchanged ref list proves the ordering.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parents[1]
DRILL = HARNESS / "verify-merge-protocol.ps1"
LEASE = HARNESS / "lease.ps1"
LEASE_NAME = "merge-protocol-drill"

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


def ps(args, env=None):
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] + args
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def git(*args):
    p = subprocess.run(["git"] + list(args), cwd=str(REPO), capture_output=True, text=True)
    return (p.stdout or "").strip()


# The FIXED names verify-merge-protocol.ps1 creates and force-deletes. Scoping the snapshot
# to exactly these is not convenience, it is correctness: the first version of this test
# compared ALL of refs/heads and every worktree, and failed 1 run in 6 because other agents
# were committing on their own branches at the time. A guard that goes red when a neighbour
# works is a guard nobody keeps - the same lesson this file exists to record, arriving one
# layer up. Miss nothing the drill touches; include nothing it does not.
DRILL_REFS = ("refs/heads/drill/verify-d", "refs/heads/work/drilla", "refs/heads/work/drillb")
DRILL_WORKTREES = ("wt-drilla", "wt-drillb", "merge-line")


def topology():
    """Only what a colliding drill run would disturb: its own branch refs and worktree paths."""
    refs = [ln for ln in git("for-each-ref", "--format=%(refname) %(objectname)",
                             "refs/heads").splitlines()
            if ln.split(" ")[0] in DRILL_REFS]
    trees = [ln for ln in git("worktree", "list", "--porcelain").splitlines()
             if ln.startswith("worktree ") and any(w in ln for w in DRILL_WORKTREES)]
    return (sorted(refs), sorted(trees))


def main():
    print("=== SINGLE-FLIGHT + WORKTREE-ROOT GUARDS (verify-merge-protocol.ps1) ===\n")
    if shutil.which("powershell") is None:
        print("  [FAIL] PowerShell not found - these guards cannot be measured on this host")
        return 1

    lockdir = Path(tempfile.mkdtemp(prefix="drill-lease-"))
    env = dict(os.environ)
    env["AI_STACK_LEASE_DIR"] = str(lockdir)   # hermetic: never touches the real lock namespace
    try:
        print("--- 1. the lease name is a KNOWN name, not an ad-hoc one")
        rc, out = ps([str(LEASE), "-Acquire", "-Name", LEASE_NAME, "-Owner", "probe-owner",
                      "-TtlMin", "5"], env)
        check("lease.ps1 accepts '" + LEASE_NAME + "' without -AdHoc (it is in lease-names.conf)",
              rc == 0, "exit " + str(rc))
        ps([str(LEASE), "-Release", "-Name", LEASE_NAME, "-Owner", "probe-owner"], env)

        print("\n--- 2. with the lease FREE, the drill would run")
        rc, out = ps([str(DRILL), "-LockProbe"], env)
        check("-LockProbe exits 0 and says it acquired", rc == 0 and "LOCK PROBE" in out,
              "exit " + str(rc))
        check("-LockProbe left the lease released, not held",
              not (lockdir / (LEASE_NAME + ".json")).exists())

        print("\n--- 3. with another copy holding it, the drill REFUSES - and touches nothing")
        rc, _ = ps([str(LEASE), "-Acquire", "-Name", LEASE_NAME, "-Owner", "other-agent",
                    "-TtlMin", "5"], env)
        check("a second agent holds the lease", rc == 0, "exit " + str(rc))

        before = topology()
        rc, out = ps([str(DRILL), "-LockProbe"], env)
        after = topology()
        check("the drill exits 3 (blocked - WAIT), not 0", rc == 3, "exit " + str(rc))
        check("it says WHY, naming the lease", "REFUSED" in out and LEASE_NAME in out)
        # THE POINT, and it is about ORDER. Exit 3 would also be produced by a script that
        # refused AFTER its preamble had already force-deleted the other run's branches -
        # which is the failure mode being fixed, so exit 3 alone proves nothing. The
        # preamble's last act is to print "development before: <sha> | operator checkout
        # on: <branch>"; a full run prints it as its very first line. Its ABSENCE from a
        # blocked run is the observable that the refusal came first.
        check("the refusal precedes the destructive preamble (none of its output appears)",
              "development before:" not in out and "two developers" not in out)
        check("the drill's own branch refs are untouched across the refused run",
              before[0] == after[0], "tracked: " + ", ".join(DRILL_REFS))
        check("the drill's own worktree paths are untouched across the refused run",
              before[1] == after[1], "tracked: " + ", ".join(DRILL_WORKTREES))
        check("the holder still holds it - a refused run does not steal the lease",
              (lockdir / (LEASE_NAME + ".json")).exists())

        print("\n--- 4. releasing it lets the next run through (the guard is not a one-way latch)")
        ps([str(LEASE), "-Release", "-Name", LEASE_NAME, "-Owner", "other-agent"], env)
        rc, out = ps([str(DRILL), "-LockProbe"], env)
        check("-LockProbe exits 0 again once the lease is free", rc == 0, "exit " + str(rc))

        print("\n--- 5. the ascent hazard: a stale directory at a worktree path IS a live repo")
        stale = REPO / ".claude" / "worktrees" / "stale-probe-dir"
        stale.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(["git", "-C", str(stale), "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True)
            top = (p.stdout or "").strip()
            check("git -C <stale plain dir> succeeds and ASCENDS to the enclosing checkout - "
                  "this is why a failed provision redirects the drill at the operator's tree",
                  p.returncode == 0 and top != "", "-> " + top)
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-Command",
                                ". ./scripts/agent-harness/common.ps1; "
                                "if (Test-IsWorktreeRoot '" + str(stale) + "') { 'ROOT' } "
                                "else { 'NOT-ROOT' }"],
                               cwd=str(REPO), capture_output=True, text=True)
            check("Test-IsWorktreeRoot says NOT-ROOT for it, so the drill can refuse to proceed",
                  "NOT-ROOT" in (r.stdout or ""), (r.stdout or "").strip())
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-Command",
                                ". ./scripts/agent-harness/common.ps1; "
                                "if (Test-IsWorktreeRoot '" + str(REPO) + "') { 'ROOT' } "
                                "else { 'NOT-ROOT' }"],
                               cwd=str(REPO), capture_output=True, text=True)
            check("and ROOT for a real worktree root, so the guard is not simply always false",
                  "NOT-ROOT" not in (r.stdout or "") and "ROOT" in (r.stdout or ""),
                  (r.stdout or "").strip())
        finally:
            try:
                stale.rmdir()
            except OSError:
                pass
    finally:
        shutil.rmtree(lockdir, ignore_errors=True)

    print("\n" + str(_passed) + "/" + str(_passed + _failed) + " single-flight checks passed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
