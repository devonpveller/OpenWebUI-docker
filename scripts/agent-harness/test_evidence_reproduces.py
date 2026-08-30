"""Guards for the durable check born from U3's tester finding.

`scripts/checks/check_quadrant_evidence_reproduces.py` is banked in the shared durable-check
registry, which means it runs against every future results set. The GYM DRILL
(`u3_evidence_regression_gym.py`) proves it catches real seeded regressions in the arena, but
that needs the arena and real run evidence. These are the fast guards: they run anywhere, in
under a second, and they are what stops the banked check from quietly becoming decorative.

Each test is one sentence of the check's contract, and two of them are the exact defects the
drill found in the check ITSELF on its first two runs - the absolute-path read-through, and
the fallback that made a deleted workspace look reproducible.

Run:  python -m pytest scripts/agent-harness/test_evidence_reproduces.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CHECK = REPO / "scripts" / "checks" / "check_quadrant_evidence_reproduces.py"


def run_check(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True,
                          encoding="utf-8", errors="replace")


def make_set(tmp_path: Path, *, status: str = "completed", venue: bool = True,
             marker: str = "kept.txt") -> Path:
    """A minimal results set whose acceptance command is re-runnable anywhere.

    The command asserts a file the run "kept" is still there, which is the shape of the real
    frozen-file guard without needing the item machinery.
    """
    results = tmp_path / "runs"
    run_dir = results / "20260830T000000Z-fixture-project"
    ws = run_dir / "workspace"
    ws.mkdir(parents=True)
    (ws / marker).write_text("the artifact\n", encoding="utf-8")
    (run_dir / "transcript.txt").write_text("ran\n", encoding="utf-8")

    rec = {
        "quadrant": "fixture::project", "runner": "fixture", "target": "project",
        "item": "u4-baseline", "item_digest": "d" * 64, "status": status,
        "started_utc": "2026-08-30T00:00:00Z", "ended_utc": "2026-08-30T00:01:00Z",
        "wall_seconds": 60.0,
        "evidence": {"workspace": str(ws), "transcript": str(run_dir / "transcript.txt")},
        "acceptance": [{
            "criterion": "the kept artifact is still there",
            # QUOTED. `sys.executable` lives under "D:\Open WebUI\..." on this machine and
            # the command is run through the shell: unquoted, cmd.exe answers "'D:\Open' is
            # not recognized" and the check reads that as a non-reproducing verdict. The
            # test would then have been measuring the quoting rather than the check.
            "check": f'"{sys.executable}" -c "import pathlib,sys; '
                     f'sys.exit(0 if pathlib.Path(\'{marker}\').is_file() else 1)"',
            "exit_code": 0, "passed": True,
        }],
    }
    if venue:
        rec["venue"] = {"name": "gym", "kind": "gym", "repo": "/arena", "ref": "main",
                        "source": "config"}
    (run_dir / "record.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return results


def test_a_reproducible_record_passes(tmp_path):
    res = run_check(str(make_set(tmp_path)))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "re-derived their verdict" in res.stdout


def test_a_record_whose_kept_evidence_no_longer_yields_its_verdict_is_caught(tmp_path):
    """THE FINDING, in miniature: the verdict passed when it ran and the workspace still
    exists, but what it kept no longer re-produces it."""
    results = make_set(tmp_path)
    (results / "20260830T000000Z-fixture-project" / "workspace" / "kept.txt").unlink()
    res = run_check(str(results))
    assert res.returncode == 1
    assert "NOT REPRODUCIBLE" in res.stdout
    assert "recorded exit 0" in res.stdout


def test_a_deleted_workspace_in_a_copy_is_caught_and_not_masked_by_the_recorded_path(tmp_path):
    """FOUND BY THE DRILL, second run, and reproduced here in its exact configuration.

    `evidence.workspace` is an absolute path. Delete the workspace in a COPY of a results set
    and that path still resolves - to the untouched original - so the pre-existing admission
    gate passes it (it only asks whether the path exists) and the old fallback made this
    check pass it too. A record naming a workspace inside its own run directory is checked
    THERE, and its absence is the finding.

    The copy is essential: delete the workspace in the ORIGINAL and admission refuses the
    record outright, which is a different and already-covered case."""
    import shutil
    original = make_set(tmp_path)
    copy = tmp_path / "archived"
    shutil.copytree(original, copy)
    shutil.rmtree(copy / "20260830T000000Z-fixture-project" / "workspace")

    res = run_check(str(copy))
    assert res.returncode == 1, "a results set whose workspace is gone read as reproducible"
    assert "kept no 'workspace/'" in res.stdout
    assert "SKIPPED" not in res.stdout, \
        "admission would have to be refusing this for the check never to look"


def test_the_check_reads_the_tree_it_was_handed_not_the_path_the_record_names(tmp_path):
    """FOUND BY THE DRILL, first run. A results set copied elsewhere - which is what an
    archive or an audit is - must be checked where it now lives."""
    original = make_set(tmp_path)
    import shutil
    copy = tmp_path / "archived"
    shutil.copytree(original, copy)
    (copy / "20260830T000000Z-fixture-project" / "workspace" / "kept.txt").unlink()
    res = run_check(str(copy))
    assert res.returncode == 1, "the check followed the recorded absolute path into the original"
    # and the untouched original still passes, so the failure above is about the copy
    assert run_check(str(original)).returncode == 0


def test_a_record_refused_at_admission_is_skipped_and_counted_not_silently_passed(tmp_path):
    """A record with no venue is refused by the harness's own gate, so it is in no
    comparison and re-deriving its verdict would re-derive a number nobody may use. That is
    a SKIP - printed, counted, and never a silent pass."""
    results = make_set(tmp_path, venue=False)
    (results / "20260830T000000Z-fixture-project" / "workspace" / "kept.txt").unlink()
    res = run_check(str(results))
    assert res.returncode == 0
    assert "SKIPPED (not in any comparison)" in res.stdout
    assert "1 skipped as inadmissible" in res.stdout


def test_a_non_outcome_record_claims_no_verdict_and_is_not_re_run(tmp_path):
    results = make_set(tmp_path, status="not_run")
    (results / "20260830T000000Z-fixture-project" / "workspace" / "kept.txt").unlink()
    assert run_check(str(results)).returncode == 0


def test_no_results_anywhere_is_a_vacuous_pass_that_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = run_check()
    assert res.returncode == 0
    assert "vacuous pass" in res.stdout


def test_a_named_directory_that_does_not_exist_is_misuse_not_a_pass(tmp_path):
    res = run_check(str(tmp_path / "nope"))
    assert res.returncode == 2


def test_the_check_is_banked_in_the_registry_with_the_form_that_runs_anywhere():
    """A durable check banked without its arguments exits 2 on every machine and teaches the
    line to ignore its own registry. The banked command must be the --auto form."""
    sys.path.insert(0, str(HERE))
    import durable_checks  # noqa: PLC0415
    rows = durable_checks.load(REPO)
    mine = [r for r in rows if "check_quadrant_evidence_reproduces" in r["check"]]
    assert mine, "the U3 check is not banked in the durable-check registry"
    assert all("--auto" in r["check"] for r in mine), \
        f"banked without --auto: {[r['check'] for r in mine]}"
