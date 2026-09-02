"""Mutation drill: prove the comparison's guards BITE, not merely that they are green.

The recurring failure in this workspace is not a missing test - it is a check that passes
while checking nothing (eight found in a single day; CLAUDE.md records the pattern). A
suite of greens is evidence that nothing currently breaks. It is NOT evidence that any
particular guard would notice if it did. (Round 1 of this module wrote "a suite of 33
greens" here and the suite printed 39 by round 2 - a count in a docstring is a claim with
a shelf life, so this one names no number.)

So each entry below breaks ONE guard on purpose, runs the ONE test that is supposed to
catch it, and requires that test to go RED. A mutation that leaves its test green is
reported as a HOLE: the guard is decorative and the drill fails.

    python -m quadrant.prove_guards          # from scripts/agent-harness

Every mutation is applied to a file that is restored in a `finally`, and the drill refuses
to start if the working tree of the module directory is dirty - a crash mid-run must never
be able to leave a weakened guard on disk looking like source.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple
from quadrant import proc as _proc  # noqa: E402

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent

# (module file, snippet to break, replacement, test that must go RED, what that proves)
MUTATIONS: List[Tuple[str, str, str, str, str]] = [
    ("record.py",
     "        if not _evidence_present(val, record_dir):",
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

    # --- added 2026-08-30 after a verifier reproduced the module's own failure mode
    # --- through its shipped CLI. Mechanism 1 was only as strong as what "the matrix" is.

    ("report.py",
     "    keys = declared_keys(quadrants, records, declared)",
     "    keys = [q.key for q in quadrants]",
     "test_a_declared_cell_survives_a_narrowed_configuration",
     "the row set is the DECLARED matrix, not today's configuration - narrowing the axes "
     "cannot shrink the comparison"),

    ("cli.py",
     "    records = _load_records(results_dir)",
     "    records = [r for r in _load_records(results_dir)\n"
     "               if r.get(\"quadrant\") in {q.key for q in quadrants}]",
     "test_narrowing_the_axes_cannot_launder_a_partial_comparison_through_the_cli",
     "THE SHIPPED DEFECT, restored verbatim: filtering records to the configured matrix "
     "let a one-line config edit turn a 2/4 comparison into a complete 2/2 one at exit 0"),

    ("cli.py",
     "    declared, pinned, pin_block = _declared_matrix(results_dir, quadrants, records, v)",
     "    declared, pinned, pin_block = None, \"\", {}",
     "test_the_matrix_lock_holds_a_cell_that_lost_both_its_config_and_its_record",
     "the results set's pinned matrix.json is load-bearing on its own - it remembers a "
     "cell that lost both its configuration and its record"),

    ("report.py",
     "    if said:",
     "    if False:",
     "test_narrowing_the_axes_cannot_launder_a_partial_comparison_through_the_cli",
     "an off-matrix cell carries the reason its records gave - a cell that leaves the "
     "configuration must not also lose the sentence explaining why it never ran"),

    # --- added 2026-08-30 with the VENUE. The comparison could not lie about WHAT ran and
    # --- had no way to state WHERE, so a four-cell gym-column comparison ran on ai-stack.

    ("venue.py",
     "    if v.rules.get(\"must_differ_from_harness_repo\"):",
     "    if False:",
     "test_a_gym_venue_resolving_to_the_harness_repo_is_a_venue_violation",
     "THE VENUE GUARD: a 'gym' venue that resolves to the harness's own repository is "
     "refused, so a run whose SUBJECT is the repo under test cannot be evidence for a "
     "column that begins 'Gym:'"),

    ("venue.py",
     "    if top and top != here:",
     "    if False:",
     "test_a_venue_path_inside_a_repository_does_not_silently_adopt_that_repository",
     "a venue path must be a repository ROOT: git discovers upward, so a wrong path "
     "otherwise adopts whatever repo encloses it - on this machine the user's HOME"),

    ("record.py",
     "    if not got:",
     "    if False:",
     "test_a_record_that_names_no_venue_is_refused",
     "a record that cannot say where it ran is refused - the pre-venue records are real "
     "runs in the wrong place and are rendered as refusals, not as results"),

    # --- added 2026-08-30 round 7. The venue check compared a LABEL: four records
    # --- re-pointed at another repository, still named `gym`, were admitted at exit 0.

    ("record.py",
     "        if pin_id != rec_id:",
     "        if False:",
     "test_a_record_re_pointed_at_another_repository_is_refused_though_its_name_matches",
     "THE SHIPPED DEFECT: the venue is identified by the REPOSITORY's root commit, so a "
     "record whose venue.repo was edited to another repo cannot ride in on the name"),

    ("record.py",
     "    if pin_id and not rec_id:",
     "    if False:",
     "test_a_record_with_no_identity_cannot_enter_an_identity_pinned_comparison",
     "a pin that carries an identity REFUSES a record without one - otherwise deleting "
     "one field from a forged record buys the weaker label comparison"),

    ("record.py",
     "    if pin.get(\"repo\") and not _same_repo_path(rv.get(\"repo\"), pin.get(\"repo\")):",
     "    if False:",
     "test_a_legacy_set_without_identity_still_compares_every_label_not_just_the_name",
     "a results set predating identity compares the repository PATH too, so the "
     "name-only comparison cannot survive as a fallback"),

    ("cli.py",
     "    if existing != body:",
     "    if declared != prior or venue_block != prior_venue:",
     "test_the_lock_refreshes_its_why_while_the_venue_pin_stays_put",
     "the lock is rewritten whenever it would DIFFER, so a false sentence in its `_why` "
     "cannot outlive the fix - it said admission refuses another venue while admission "
     "compared a name"),

    ("cli.py",
     "    rv = pin_block or None",
     "    rv = v",
     "test_the_report_renders_the_pinned_venue_not_todays_configuration",
     "THE SHIPPED DEFECT: the report rendered TODAY's venue object, so `report --repo "
     "<ai-stack>` printed ai-stack under 'SATISFIES a Gym: column' over arena records"),

    ("report.py",
     "        \"\" if summary.get(\"satisfies_gym_column\") else \" NOT\", v.get(\"kind\")),",
     "        \"\", v.get(\"kind\")),",
     "test_a_workspace_venue_report_says_it_does_not_satisfy_the_gym_column",
     "a venue kind the schema declares as NOT satisfying the column must render that "
     "way - the negation is the half a mislabel needs"),

    ("adapters.py",
     "    for rel in dict.fromkeys(changed + planted):",
     "    for rel in changed:",
     "test_target_self_retains_enough_to_re_run_the_acceptance_checks",
     "a target-self cell retains what its acceptance NEEDS, not only what changed - the "
     "frozen file is unchanged by definition and is exactly what the guard reads"),

    ("matrix.py",
     "    v = kw.get(\"venue\")",
     "    v = None",
     "test_a_venue_violation_blocks_every_cell_with_that_reason",
     "the venue is checked BEFORE the runner and the target, so a cell whose subject is "
     "the wrong repository is blocked rather than run to completion"),
]


def _dirty() -> bool:
    out = _proc.run(["git", "status", "--porcelain", "--", str(HERE)],
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
            proc = _proc.run(
                [sys.executable, "-m", "pytest", "test_quadrant.py",
                 "test_quadrant_venue.py", "-q", "-k", test,
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
