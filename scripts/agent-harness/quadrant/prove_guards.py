"""Mutation drill: prove the comparison's guards BITE, not merely that they are green.

The recurring failure in this workspace is not a missing test - it is a check that passes
while checking nothing (eight found in a single day; CLAUDE.md records the pattern). A
suite of 33 greens is evidence that nothing currently breaks. It is NOT evidence that any
particular guard would notice if it did.

So each entry below breaks ONE guard on purpose, runs the ONE test that is supposed to
catch it, and requires that test to go RED. A mutation that leaves its test green is
reported as a HOLE: the guard is decorative and the drill fails.

    python -m quadrant.prove_guards          # from scripts/agent-harness

Every mutation is applied to a file that is restored in a `finally`, and the drill refuses
to start if the working tree of the module directory is dirty - a crash mid-run must never
be able to leave a weakened guard on disk looking like source.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent

# (module file, snippet to break, replacement, test that must go RED, what that proves)
MUTATIONS: List[Tuple[str, str, str, str, str]] = [
    ("record.py",
     "        if not Path(val).exists():",
     "        if False:",
     "test_completion_whose_evidence_paths_do_not_exist_is_refused",
     "evidence is checked against the FILESYSTEM, not against the presence of a string"),

    ("record.py",
     "    if not isinstance(acc, list) or not acc:",
     "    if False:",
     "test_completion_with_no_acceptance_results_is_refused",
     "a 'completed' run with nothing executed is refused - C.7's only-an-executable-check"),

    ("record.py",
     "        if got != item_digest:",
     "        if False:",
     "test_a_record_from_a_different_item_is_refused",
     "the same-item control is mechanical: a record from another item cannot slip in"),

    ("record.py",
     "            if bool(a[\"passed\"]) != (a[\"exit_code\"] == 0):",
     "            if False:",
     "test_acceptance_verdict_contradicting_its_exit_code_is_refused",
     "a self-reported 'passed' cannot override the exit code that measured it"),

    ("record.py",
     "        if rec.get(\"acceptance\"):",
     "        if False:",
     "test_not_run_carrying_acceptance_results_is_refused",
     "a quadrant that did not run cannot carry a score"),

    ("matrix.py",
     "        if not ready and not (reason or \"\").strip():",
     "        if False:",
     "test_preflight_reason_is_required_when_not_ready",
     "a blocked quadrant always says why - the 'nobody considered it' shape is unrepresentable"),

    ("matrix.py",
     "        if not isinstance(r, dict):",
     "        if False and not isinstance(r, dict):",
     "test_unknown_runner_fails_loudly",
     "a misconfigured quadrant fails LOUDLY instead of being reported as not-run"),

    ("report.py",
     "        if not recs:",
     "        if False:",
     "test_report_renders_a_row_for_every_matrix_quadrant_even_with_no_records",
     "THE central one: the report is built from the MATRIX, so a quadrant that produced "
     "nothing still gets a row and cannot vanish from the comparison"),

    ("report.py",
     "    compared = [r for r in rows if r[\"compared\"]]",
     "    compared = list(rows)",
     "test_report_headline_counts_only_admitted_comparable_records",
     "the COMPARED n/4 headline counts only quadrants that actually produced an outcome"),

    ("report.py",
     "        if not admitted:",
     "        if False:",
     "test_a_refused_record_is_reported_as_not_compared_never_dropped",
     "a record refused at admission is reported as REFUSED, never quietly counted"),
]


def _dirty() -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--", str(HERE)],
                         capture_output=True, text=True, cwd=str(HARNESS))
    return any(ln for ln in out.stdout.splitlines()
               if ln.strip() and "prove_guards" not in ln)


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _dirty() and "--allow-dirty" not in argv:
        print("REFUSED: the quadrant module has uncommitted changes.\n"
              "  This drill edits those files in place and restores them from memory. If it "
              "is interrupted while the tree is already dirty, telling the weakened guard "
              "from your own edit is guesswork.\n"
              "  Commit first, or pass --allow-dirty if you accept that.")
        return 2

    holes, errors = [], []
    for i, (fname, old, new, test, proves) in enumerate(MUTATIONS, 1):
        path = HERE / fname
        original = path.read_text(encoding="utf-8")
        if old not in original:
            errors.append(f"{fname}: mutation {i} no longer matches the source "
                          f"({old.strip()[:60]}...). The guard was refactored and this "
                          f"drill stopped testing it - fix the drill, do not delete it.")
            continue
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "test_quadrant.py", "-q", "-k", test,
                 "-p", "no:cacheprovider"],
                cwd=str(HARNESS), capture_output=True, text=True)
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")

        if proc.returncode == 0:
            holes.append(f"HOLE  [{i}] {fname}: broke the guard and {test} STAYED GREEN.\n"
                         f"      It was supposed to prove: {proves}")
            print(holes[-1])
        else:
            print(f"BITES [{i}] {fname} -> {test} went red")
            print(f"      proves: {proves}")

    print()
    for e in errors:
        print(f"STALE {e}")
    total = len(MUTATIONS)
    print(f"{total - len(holes) - len(errors)}/{total} guards proven to bite")
    return 1 if (holes or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
