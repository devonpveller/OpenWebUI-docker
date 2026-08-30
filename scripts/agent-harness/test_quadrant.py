"""Executable proof for the runner x target quadrant comparison (dark-factory U4).

PLAN 2's U4 row is validated by "same anchored item run per quadrant (runner x target),
outcomes compared". This file is the half of that sentence nobody writes down: what stops
the comparison from LYING.

The failure mode is specific and it is not a wrong number. Two quadrants run, two do not,
the report shows the two that did, and it reads as a completed comparison. Every test below
exists because some shape of that was reachable:

  * a record that says "completed" with no evidence behind it
  * a record that says "completed" with no acceptance check ever executed
  * a "not_run" with no reason, indistinguishable from one nobody considered
  * a record from a DIFFERENT item silently compared against the others
  * a quadrant absent from the report because no record mentioned it
  * the fixture runner - scaffolding - appearing in a decision table

Run:  python -m pytest scripts/agent-harness/test_quadrant.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from quadrant import item as item_mod          # noqa: E402
from quadrant import matrix as matrix_mod      # noqa: E402
from quadrant import record as record_mod      # noqa: E402
from quadrant import report as report_mod      # noqa: E402

import config as harness_config                # noqa: E402


# ---------------------------------------------------------------- fixtures --

def cfg(**over):
    """A minimal, complete quadrant configuration. Tests mutate copies of it.

    Deliberately NOT the real harness.config.json: a test that can only fail when the
    operator's live config is wrong tests the config, not the code.
    """
    base = {
        "runners": {
            "claude-code": {"kind": "claude-code", "status": "proven", "default_model": "opus"},
            "little-coder": {"kind": "little-coder", "status": "unproven",
                             "transport": "docker-exec", "container": "little-coder",
                             "base_url": "http://localhost:8090",
                             "container_workspace": "/workspace",
                             "submit_path": "/tasks",
                             "default_model": "local-default"},
            "fixture": {"kind": "fixture", "status": "self-test"},
        },
        "targets": {
            "self": {"kind": "self", "status": "proven"},
            "project": {"kind": "project", "status": "unproven",
                        "scratch_root": ".claude/quadrant-scratch"},
        },
        "quadrant": {
            "runners": ["little-coder", "claude-code"],
            "targets": ["self", "project"],
            "repeats": 1,
            "results_dir": ".claude/quadrant-runs",
        },
    }
    for k, v in over.items():
        base[k] = v
    return base


@pytest.fixture()
def evidence(tmp_path):
    """Real files on disk, because admission checks the filesystem and not the JSON."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    tr = tmp_path / "transcript.txt"
    tr.write_text("dispatched; ran; done\n", encoding="utf-8")
    return {"workspace": str(ws), "transcript": str(tr)}


def good_record(ev, **over):
    rec = {
        "quadrant": "claude-code::self",
        "runner": "claude-code",
        "target": "self",
        "item": "u4-baseline",
        "item_digest": "d" * 64,
        "status": "completed",
        "started_utc": "2026-08-30T10:00:00Z",
        "ended_utc": "2026-08-30T10:04:00Z",
        "wall_seconds": 240.0,
        "evidence": dict(ev),
        "acceptance": [
            {"criterion": "the module imports", "check": "python -c pass", "exit_code": 0,
             "passed": True},
        ],
        "rounds": {"dispatch_attempts": 1, "test_cycles": 1, "operator_taps": 0},
        "scope": {"files_changed": 2, "out_of_scope_hits": []},
        "containment": {"class": "normative", "guard_events": []},
        "cost": {"wall_seconds": 240.0, "tokens": None, "usd": None, "gpu_seconds": None},
    }
    rec.update(over)
    return rec


# ------------------------------------------------------- the matrix is 2x2 --

def test_matrix_is_the_full_cross_product():
    qs = matrix_mod.build(cfg())
    assert len(qs) == 4
    assert {q.key for q in qs} == {
        "little-coder::self", "little-coder::project",
        "claude-code::self", "claude-code::project",
    }


def test_unknown_runner_fails_loudly():
    c = cfg()
    c["quadrant"]["runners"] = ["little-coder", "no-such-runner"]
    with pytest.raises(matrix_mod.QuadrantConfigError) as e:
        matrix_mod.build(c)
    assert "no-such-runner" in str(e.value)


def test_unknown_target_fails_loudly():
    c = cfg()
    c["quadrant"]["targets"] = ["self", "no-such-target"]
    with pytest.raises(matrix_mod.QuadrantConfigError) as e:
        matrix_mod.build(c)
    assert "no-such-target" in str(e.value)


def test_runner_missing_a_field_its_kind_needs_fails_loudly():
    """A little-coder runner that does not say HOW it is reached is a config error.

    The distinction is the point: NOT RUN is a fact about the world (the daemon is
    unreachable); a missing transport is a fact about the operator's file, and reporting it
    as NOT RUN would hide a typo behind a legitimate-looking result.
    """
    c = cfg()
    del c["runners"]["little-coder"]["transport"]
    with pytest.raises(matrix_mod.QuadrantConfigError) as e:
        matrix_mod.build(c)
    assert "transport" in str(e.value)


def test_a_docker_exec_runner_missing_its_container_fails_loudly():
    """The same rule one level down: the fields depend on the transport, and are required.

    Written 2026-08-30 with the merge of the quadrant harness and the dispatch layer. The
    schema used to demand `endpoint` of EVERY little-coder runner - a host door the running
    container does not publish - so the requirement moved onto the transport. This test is
    what stops that move from having quietly deleted the requirement: a docker-exec runner
    with no container, or no workspace inside it, is still refused before anything runs.
    """
    for field in ("container", "base_url", "container_workspace"):
        c = cfg()
        del c["runners"]["little-coder"][field]
        with pytest.raises(matrix_mod.QuadrantConfigError) as e:
            matrix_mod.build(c)
        assert field in str(e.value), field
        assert "docker-exec" in str(e.value), field


def test_an_unknown_transport_is_refused_rather_than_attempted():
    """A transport the harness cannot check is one it must not silently attempt.

    Same rule `preflight` applies to a runner KIND with no probe. Without this, a typo in
    `transport` would fall through every requirement list (no requirements are declared for
    an unknown name) and the cell would fail at dispatch time as if the world were at fault.
    """
    c = cfg()
    c["runners"]["little-coder"]["transport"] = "carrier-pigeon"
    with pytest.raises(matrix_mod.QuadrantConfigError) as e:
        matrix_mod.build(c)
    assert "carrier-pigeon" in str(e.value)


def test_the_probe_reports_an_unfocused_daemon_as_blocked_with_that_reason():
    """A healthy daemon with no focused project cannot take a task: POST /tasks is 409.

    That is a fact about the plane, so it is a BLOCKED preflight carrying the reason - not
    an adapter exception two minutes into a run, and not a silent READY that becomes an
    `error` record about the harness.
    """
    body = {"status": "ok", "version": "0.1.0", "focus": ""}
    pf = matrix_mod._lc_health_verdict(body, {"transport": "docker-exec"})
    assert not pf.ready
    assert "no focused project" in pf.reason and "409" in pf.reason

    pf_ok = matrix_mod._lc_health_verdict(
        {"status": "ok", "focus": "https://github.com/o/r"}, {"transport": "docker-exec"})
    assert pf_ok.ready and pf_ok.detail["focus"] == "https://github.com/o/r"

    pf_drain = matrix_mod._lc_health_verdict(
        {"status": "draining", "focus": "https://github.com/o/r"}, {})
    assert not pf_drain.ready and "draining" in pf_drain.reason


def test_target_missing_a_field_its_kind_needs_fails_loudly():
    c = cfg()
    del c["targets"]["project"]["scratch_root"]
    with pytest.raises(matrix_mod.QuadrantConfigError) as e:
        matrix_mod.build(c)
    assert "scratch_root" in str(e.value)


def test_repeats_below_one_fails_loudly():
    c = cfg()
    c["quadrant"]["repeats"] = 0
    with pytest.raises(matrix_mod.QuadrantConfigError):
        matrix_mod.build(c)


# -------------------------------------------------- evidence-gated admission --

def test_a_good_record_is_admitted(evidence):
    assert record_mod.admit(good_record(evidence), item_digest="d" * 64) == []


def test_fabricated_completion_without_evidence_is_refused(evidence):
    """THE central guard. 'completed' is a claim; evidence is what makes it a measurement."""
    rec = good_record(evidence, evidence={})
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert problems
    assert any("workspace" in p for p in problems)
    assert any("transcript" in p for p in problems)


def test_completion_whose_evidence_paths_do_not_exist_is_refused(evidence, tmp_path):
    rec = good_record(evidence, evidence={"workspace": str(tmp_path / "nope"),
                                          "transcript": str(tmp_path / "gone.txt")})
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("does not exist" in p for p in problems)


def test_completion_with_no_acceptance_results_is_refused(evidence):
    """A run nobody checked is not a result. C.7: only an executable check counts."""
    rec = good_record(evidence, acceptance=[])
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("acceptance" in p for p in problems)


def test_acceptance_entry_without_an_exit_code_is_refused(evidence):
    rec = good_record(evidence, acceptance=[{"criterion": "it works", "check": "run it",
                                             "passed": True}])
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("exit_code" in p for p in problems)


def test_acceptance_verdict_contradicting_its_exit_code_is_refused(evidence):
    """passed=True beside exit 1 is the shape of a self-report overriding a measurement."""
    rec = good_record(evidence, acceptance=[{"criterion": "it works", "check": "run it",
                                             "exit_code": 1, "passed": True}])
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("exit_code" in p and "passed" in p for p in problems)


def test_zero_wall_time_is_refused(evidence):
    rec = good_record(evidence, wall_seconds=0)
    assert any("wall_seconds" in p for p in record_mod.admit(rec, item_digest="d" * 64))


# ------------------------------------------------------ not_run must be honest --

def test_not_run_without_a_reason_is_refused(evidence):
    rec = good_record(evidence, status="not_run", acceptance=[], evidence={})
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("not_run_reason" in p for p in problems)


def test_not_run_carrying_acceptance_results_is_refused(evidence):
    """A quadrant that did not run cannot have checked anything. This catches the
    half-run record that would otherwise carry a score into the table."""
    rec = good_record(evidence, status="not_run", not_run_reason="daemon unreachable")
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("acceptance" in p for p in problems)


def test_not_run_with_a_reason_is_admitted_but_not_comparable(evidence):
    rec = good_record(evidence, status="not_run", not_run_reason="no dispatch implementation",
                      acceptance=[], evidence={}, wall_seconds=0)
    assert record_mod.admit(rec, item_digest="d" * 64) == []
    assert record_mod.is_comparable(rec) is False


def test_unknown_status_is_refused(evidence):
    rec = good_record(evidence, status="fine")
    assert any("status" in p for p in record_mod.admit(rec, item_digest="d" * 64))


# ------------------------------------------------------------- one item only --

def test_a_record_from_a_different_item_is_refused(evidence):
    """'Same anchored item' is the experiment's only control, so it is mechanized."""
    rec = good_record(evidence, item_digest="a" * 64)
    problems = record_mod.admit(rec, item_digest="d" * 64)
    assert any("digest" in p for p in problems)


def test_item_digest_is_stable_and_content_addressed(tmp_path):
    spec = {"id": "x", "task": "do the thing", "anchor": {"goal": "g"}}
    a = tmp_path / "a.json"
    a.write_text(json.dumps(spec), encoding="utf-8")
    b = tmp_path / "b.json"
    b.write_text(json.dumps(spec, indent=4), encoding="utf-8")   # same content, other bytes
    assert item_mod.digest(json.loads(a.read_text())) == item_mod.digest(json.loads(b.read_text()))
    spec2 = dict(spec, task="do a different thing")
    assert item_mod.digest(spec2) != item_mod.digest(spec)


# -------------------------------------------------- the report cannot omit --

def test_report_renders_a_row_for_every_matrix_quadrant_even_with_no_records():
    qs = matrix_mod.build(cfg())
    md = report_mod.render(qs, [], item={"id": "u4-baseline", "digest": "d" * 64})
    for q in qs:
        assert q.label in md
    assert md.count("NOT RUN") >= 4
    assert "COMPARED 0/4" in md


def test_report_headline_counts_only_admitted_comparable_records(evidence):
    qs = matrix_mod.build(cfg())
    ran = good_record(evidence, quadrant="claude-code::self")
    blocked = good_record(evidence, quadrant="little-coder::self", status="not_run",
                          not_run_reason="daemon unreachable from the host",
                          acceptance=[], evidence={}, wall_seconds=0)
    md = report_mod.render(qs, [ran, blocked], item={"id": "u4-baseline", "digest": "d" * 64})
    assert "COMPARED 1/4" in md
    assert "daemon unreachable from the host" in md


def test_a_later_real_run_outranks_an_earlier_blocked_record_for_the_same_cell(evidence):
    """A cell that was BLOCKED and then RAN is COMPARED, on the run.

    Records accumulate and `_load_records` hands them over oldest-first, so a quadrant that
    could not run in one session and ran in the next has both on disk. Until 2026-08-30 the
    row was built from the FIRST admitted record: the stale `not_run` spoke for the cell, so
    it read "did not produce an outcome" beside a completed run in the same directory and
    `compared` stayed false. That made an incomplete comparison impossible to COMPLETE by
    fixing whatever had blocked it - the only way out was deleting the blocked record, i.e.
    deleting evidence, which is the one move this module refuses everywhere else.

    Found by integration, not by review: the docker-exec transport made the two little-coder
    cells runnable for the first time, and their earlier NOT RUN records were sitting in the
    same results set.
    """
    qs = matrix_mod.build(cfg())
    blocked = good_record(evidence, quadrant="little-coder::self", runner="little-coder",
                          target="self", runner_status="unproven", status="not_run",
                          not_run_reason="daemon unreachable from the host",
                          acceptance=[], evidence={}, wall_seconds=0)
    ran = good_record(evidence, quadrant="little-coder::self", runner="little-coder",
                      target="self", runner_status="unproven")
    md = report_mod.render(qs, [blocked, ran], item={"id": "u4-baseline", "digest": "d" * 64})
    summary = report_mod.summarize(qs, [blocked, ran],
                                   item={"id": "u4-baseline", "digest": "d" * 64})
    row = [r for r in summary["rows"] if r["key"] == "little-coder::self"][0]
    assert row["compared"] is True, row
    assert row["status"] == "completed", row
    assert "COMPARED 1/4" in md


def test_the_most_recent_blocking_reason_is_the_one_reported(evidence):
    """Two blocked records, no run: the row carries the LATER reason, not the first one.

    The same ordering change, on the branch where nothing ever ran. A cell blocked for one
    reason in the morning and a different one in the afternoon must report the afternoon's -
    reporting the stale reason is how someone fixes a door that is no longer the problem.
    """
    qs = matrix_mod.build(cfg())
    old = good_record(evidence, quadrant="little-coder::self", runner="little-coder",
                      target="self", status="not_run", not_run_reason="no route from the host",
                      acceptance=[], evidence={}, wall_seconds=0)
    new = good_record(evidence, quadrant="little-coder::self", runner="little-coder",
                      target="self", status="not_run",
                      not_run_reason="the daemon has no focused project",
                      acceptance=[], evidence={}, wall_seconds=0)
    summary = report_mod.summarize(qs, [old, new], item={"id": "u4-baseline", "digest": "d" * 64})
    row = [r for r in summary["rows"] if r["key"] == "little-coder::self"][0]
    assert row["why_not"] == "the daemon has no focused project", row


def test_every_subprocess_in_the_package_goes_through_the_utf8_chokepoint():
    """No `subprocess.run(` anywhere in the package except `proc.py`.

    THE COMPLETENESS TEST IS A SCAN, not a list of files. `subprocess.run(..., text=True)`
    decodes with the LOCALE codec - cp1252 on this host - and everything this package reads
    is UTF-8: a local model's answer, a container's JSON, a guard's output. A byte cp1252
    cannot map (0x9d, live 2026-08-30) killed the reader thread inside subprocess, left
    `stdout` as None, and the cell recorded `AttributeError: 'NoneType' object has no
    attribute 'rpartition'` - an error record about the harness, in a table about runners.

    DECISIONS.md 2026-08-30 "ENUMERATE-AND-PATCH LOSES", and its corollary: a completeness
    test whose enumeration is a hand-written file list is a list with a spell-checker. This
    one reads the directory, so a NEW file with a raw `subprocess.run` fails it on the day it
    is added, whatever it is called.
    """
    pkg = Path(matrix_mod.__file__).resolve().parent
    offenders = []
    for py in sorted(pkg.rglob("*.py")):
        if py.name == "proc.py" or "items" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if "subprocess.run(" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{py.relative_to(pkg)}:{i}: {line.strip()}")
    assert not offenders, (
        "these call sites bypass quadrant/proc.py and will decode with the locale codec:\n"
        + "\n".join(offenders))


def test_the_chokepoint_survives_a_byte_the_locale_codec_cannot_decode():
    """0x9d is unmapped in cp1252. The exact byte that produced the live failure."""
    from quadrant import proc
    prog = "import sys; sys.stdout.buffer.write(bytes([0x41, 0x9d, 0x42]))"
    out = proc.run([sys.executable, "-c", prog])
    assert out.returncode == 0
    assert isinstance(out.stdout, str) and out.stdout.startswith("A") and out.stdout.endswith("B")


def test_the_chokepoint_refuses_to_be_talked_out_of_utf8():
    """A caller passing text=True must not silently get the locale codec back."""
    from quadrant import proc
    prog = "import sys; sys.stdout.buffer.write('\u00fcber'.encode('utf-8'))"
    out = proc.run([sys.executable, "-c", prog], text=True)
    assert out.stdout == "\u00fcber"


def test_a_refused_record_is_reported_as_not_compared_never_dropped(evidence):
    """A record that fails admission must be LOUDER than one that never existed."""
    qs = matrix_mod.build(cfg())
    liar = good_record(evidence, quadrant="claude-code::self", evidence={})
    md = report_mod.render(qs, [liar], item={"id": "u4-baseline", "digest": "d" * 64})
    assert "COMPARED 0/4" in md
    assert "REFUSED" in md
    assert "workspace" in md


def test_a_record_off_the_matrix_is_absorbed_into_the_table_never_dropped(evidence):
    """The record names a cell the configuration does not. It must still be VISIBLE.

    This test replaced one that asserted `render` RAISED here. The raise was the pressure
    that produced the real defect: the only caller silenced it by filtering those records
    out before rendering, and filtering is what let a narrowed matrix launder an incomplete
    comparison. A row nobody can drop is the stronger guard than an exception someone can
    catch.
    """
    qs = matrix_mod.build(cfg())
    stray = good_record(evidence, quadrant="gpt-5::mars")
    md = report_mod.render(qs, [stray], item={"id": "u4-baseline", "digest": "d" * 64})
    assert "gpt-5 x mars" in md
    assert "OFF MATRIX" in md
    assert "COMPARED 0/5" in md, "the off-matrix cell must count against the denominator"


def test_a_record_naming_no_quadrant_at_all_is_unrenderable(evidence):
    """The one disagreement that cannot be absorbed: there is no cell to put it in."""
    qs = matrix_mod.build(cfg())
    nameless = good_record(evidence, quadrant="")
    with pytest.raises(report_mod.QuadrantReportError):
        report_mod.render(qs, [nameless], item={"id": "u4-baseline", "digest": "d" * 64})


def test_a_declared_cell_survives_a_narrowed_configuration(evidence):
    """Shrinking the axes must not shrink the comparison.

    The report is handed the DECLARED matrix of the results set. A cell that is declared
    but no longer configured renders OFF MATRIX and counts as not compared, so the
    completeness verdict cannot improve by deleting a runner from the config.
    """
    narrow = cfg()
    narrow["quadrant"] = dict(narrow["quadrant"], runners=["claude-code"])
    qs = matrix_mod.build(narrow)
    assert len(qs) == 2
    full = ["little-coder::self", "little-coder::project",
            "claude-code::self", "claude-code::project"]
    ran = [good_record(evidence, quadrant="claude-code::self"),
           good_record(evidence, quadrant="claude-code::project", target="project")]
    summary = report_mod.summarize(qs, ran, item={"id": "u4-baseline", "digest": "d" * 64},
                                   declared=full)
    assert summary["quadrants_total"] == 4
    assert summary["compared"] == 2
    assert summary["complete"] is False
    assert sorted(summary["off_matrix"]) == sorted(full[:2])


# ------------------------------------ the CLI cannot shrink the comparison either --

def _write_record(runs, name, rec):
    d = runs / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text(json.dumps(rec), encoding="utf-8")
    return d


def _four_records(runs, evidence, digest):
    """Two real-shaped completions and two honest not-runs - the comparison as it stands."""
    for target in ("self", "project"):
        _write_record(runs, f"2026-claude-code-{target}",
                      good_record(evidence, quadrant=f"claude-code::{target}",
                                  target=target, item_digest=digest))
        _write_record(runs, f"2026-little-coder-{target}",
                      good_record(evidence, quadrant=f"little-coder::{target}",
                                  runner="little-coder", target=target, item_digest=digest,
                                  status="not_run", acceptance=[], evidence={},
                                  wall_seconds=0,
                                  not_run_reason="no route from the host to the daemon"))


def _config_at(path, runners):
    c = cfg()
    c["quadrant"] = dict(c["quadrant"], runners=runners)
    path.write_text(json.dumps(c), encoding="utf-8")


def test_narrowing_the_axes_cannot_launder_a_partial_comparison_through_the_cli(
        tmp_path, monkeypatch):
    """THE REGRESSION, end to end through the shipped CLI.

    Reproduced by a verifier 2026-08-30: `cli._emit_report` filtered the records down to
    the currently configured matrix, so narrowing `quadrant.runners` to one entry - a
    one-line config edit - made the two never-run cells leave the table. The identical
    evidence then rendered COMPARED 2/2, complete, exit 0.

    The comparison must stay 2/4 and exit 1 across that edit.
    """
    from quadrant import cli
    runs = tmp_path / "runs"
    runs.mkdir()
    it = item_mod.load("u4-baseline")
    ws = tmp_path / "ws"
    ws.mkdir()
    tr = tmp_path / "transcript.txt"
    tr.write_text("ran\n", encoding="utf-8")
    _four_records(runs, {"workspace": str(ws), "transcript": str(tr)}, it["digest"])

    cfg_path = tmp_path / "harness.config.json"
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(cfg_path))

    _config_at(cfg_path, ["little-coder", "claude-code"])
    assert cli.main(["report", "--results-dir", str(runs)]) == 1
    before = json.loads((runs / "comparison.json").read_text(encoding="utf-8"))
    assert (before["quadrants_total"], before["compared"], before["complete"]) == (4, 2, False)

    _config_at(cfg_path, ["claude-code"])
    rc = cli.main(["report", "--results-dir", str(runs)])
    after = json.loads((runs / "comparison.json").read_text(encoding="utf-8"))
    md = (runs / "COMPARISON.md").read_text(encoding="utf-8")

    assert rc == 1, "a narrowed matrix must not turn an incomplete comparison into exit 0"
    assert after["complete"] is False
    assert after["quadrants_total"] == 4, "the two dropped cells must still be rows"
    assert "COMPARED 2/4" in md
    assert "MATRIX NARROWED" in md
    assert "little-coder x self" in md and "little-coder x project" in md
    assert "no route from the host to the daemon" in md, \
        "the reason those cells did not run must survive the narrowing too"


def test_the_matrix_lock_holds_a_cell_that_lost_both_its_config_and_its_record(
        tmp_path, monkeypatch):
    """The lock is load-bearing on its own, not just a restatement of the records.

    Here the little-coder cells have NO records at all - config + records would declare a
    two-cell matrix and report it complete. Only `matrix.json`, pinned when the comparison
    was declared, remembers that this results set is a comparison of four.
    """
    from quadrant import cli
    runs = tmp_path / "runs"
    runs.mkdir()
    it = item_mod.load("u4-baseline")
    ws = tmp_path / "ws"
    ws.mkdir()
    tr = tmp_path / "transcript.txt"
    tr.write_text("ran\n", encoding="utf-8")
    ev = {"workspace": str(ws), "transcript": str(tr)}
    for target in ("self", "project"):
        _write_record(runs, f"2026-claude-code-{target}",
                      good_record(ev, quadrant=f"claude-code::{target}", target=target,
                                  item_digest=it["digest"]))
    (runs / "matrix.json").write_text(json.dumps({"version": 1, "declared": [
        "little-coder::self", "little-coder::project",
        "claude-code::self", "claude-code::project"]}), encoding="utf-8")

    cfg_path = tmp_path / "harness.config.json"
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(cfg_path))
    _config_at(cfg_path, ["claude-code"])

    rc = cli.main(["report", "--results-dir", str(runs)])
    summary = json.loads((runs / "comparison.json").read_text(encoding="utf-8"))
    assert rc == 1
    assert summary["complete"] is False
    assert summary["quadrants_total"] == 4
    assert sorted(summary["off_matrix"]) == ["little-coder::project", "little-coder::self"]


def test_the_declared_matrix_is_append_only(tmp_path, monkeypatch):
    """A cell added to the axes joins the lock; one removed from them does not leave it."""
    from quadrant import cli
    runs = tmp_path / "runs"
    runs.mkdir()
    cfg_path = tmp_path / "harness.config.json"
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(cfg_path))

    _config_at(cfg_path, ["claude-code"])
    cli.main(["report", "--results-dir", str(runs)])
    first = json.loads((runs / "matrix.json").read_text(encoding="utf-8"))["declared"]
    assert sorted(first) == ["claude-code::project", "claude-code::self"]

    _config_at(cfg_path, ["little-coder"])
    cli.main(["report", "--results-dir", str(runs)])
    second = json.loads((runs / "matrix.json").read_text(encoding="utf-8"))["declared"]
    assert set(first).issubset(set(second)), "the lock must never shrink"
    assert len(second) == 4


def test_report_states_the_confidence_limit_when_n_is_one(evidence):
    qs = matrix_mod.build(cfg())
    ran = good_record(evidence, quadrant="claude-code::self")
    md = report_mod.render(qs, [ran], item={"id": "u4-baseline", "digest": "d" * 64})
    assert "n=1" in md


def test_summary_json_is_machine_readable_and_agrees_with_the_markdown(evidence):
    qs = matrix_mod.build(cfg())
    ran = good_record(evidence, quadrant="claude-code::self")
    s = report_mod.summarize(qs, [ran], item={"id": "u4-baseline", "digest": "d" * 64})
    assert s["compared"] == 1
    assert s["quadrants_total"] == 4
    assert len(s["rows"]) == 4
    assert {r["status"] for r in s["rows"]} == {"completed", "not_run"}


# ----------------------------------------- scaffolding stays out of the table --

def test_a_self_test_runner_is_never_comparable():
    c = cfg()
    c["quadrant"]["runners"] = ["fixture", "claude-code"]
    qs = matrix_mod.build(c)
    fixtures = [q for q in qs if q.runner == "fixture"]
    assert fixtures and all(q.comparable is False for q in fixtures)


def test_a_self_test_runner_record_does_not_count_as_compared(evidence):
    c = cfg()
    c["quadrant"]["runners"] = ["fixture", "claude-code"]
    qs = matrix_mod.build(c)
    rec = good_record(evidence, quadrant="fixture::self", runner="fixture")
    md = report_mod.render(qs, [rec], item={"id": "u4-baseline", "digest": "d" * 64})
    assert "COMPARED 0/4" in md
    assert "self-test" in md


def test_no_shipped_profile_assigns_a_role_to_a_self_test_runner():
    """The live config, not a fixture: a self-test runner reachable from a profile would
    put scaffolding on the real line."""
    live = harness_config.load(fresh=True)
    runners = live.get("runners", {})
    selftest = {n for n, r in runners.items() if r.get("status") == "self-test"}
    for pname, prof in (live.get("profiles") or {}).items():
        if pname.startswith("_"):
            continue
        for role, spec in prof.items():
            if isinstance(spec, dict):
                assert spec.get("runner") not in selftest, (
                    f"profile '{pname}' assigns {role} to self-test runner {spec.get('runner')}")


# ------------------------------------------- preflight blocks, never explodes --

def test_a_blocked_quadrant_becomes_a_not_run_record_not_an_exception():
    q = matrix_mod.build(cfg())[0]
    pf = matrix_mod.PreflightResult(ready=False, reason="little-coder API port is not published")
    rec = record_mod.not_run(q, item={"id": "u4-baseline", "digest": "d" * 64}, preflight=pf)
    assert rec["status"] == "not_run"
    assert "not published" in rec["not_run_reason"]
    assert record_mod.admit(rec, item_digest="d" * 64) == []


def test_preflight_reason_is_required_when_not_ready():
    with pytest.raises(ValueError):
        matrix_mod.PreflightResult(ready=False, reason="")


# ------------------------------------------------------ live config sanity --

def test_the_live_config_defines_a_buildable_quadrant_matrix():
    """The shipped harness.config.json must itself produce a valid 2x2."""
    qs = matrix_mod.build(harness_config.load(fresh=True))
    assert len(qs) == 4


# ------------------------------------------------------------ end to end --

def test_fixture_runner_completes_the_real_item_end_to_end(tmp_path):
    """Proves the machinery - plant, dispatch, run the acceptance checks, record, admit -
    without an LLM and without spending anything. The fixture runner is scaffolding by
    construction (test_a_self_test_runner_record_does_not_count_as_compared), so a green
    here is a claim about the HARNESS and never about a quadrant."""
    from quadrant import cli
    out = tmp_path / "runs"
    rc = cli.main(["run", "--runner", "fixture", "--target", "project",
                   "--item", "u4-baseline", "--results-dir", str(out),
                   "--scratch-root", str(tmp_path / "scratch")])
    assert rc == 0, "the fixture quadrant must complete"
    recs = list(out.glob("*/record.json"))
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    assert rec["status"] == "completed"
    assert rec["acceptance"] and all(a["exit_code"] == 0 for a in rec["acceptance"])
    it = item_mod.load("u4-baseline")
    assert record_mod.admit(rec, item_digest=it["digest"]) == []


def test_cli_report_exits_nonzero_when_the_comparison_is_incomplete(tmp_path):
    """A partial comparison must not exit 0. A script that consumes this harness has to be
    able to tell 'four quadrants compared' from 'one did and three did not'."""
    from quadrant import cli
    out = tmp_path / "runs"
    out.mkdir()
    rc = cli.main(["report", "--results-dir", str(out), "--item", "u4-baseline"])
    assert rc != 0


# ------------------------------------- the item's control cannot drift by accident --

def test_build_artifacts_do_not_enter_the_item(tmp_path):
    """MEASURED, not anticipated: pytest imported an item fixture once and left a
    __pycache__ in files/. It was swept into the planted set, which changed the item DIGEST
    (invalidating every record produced before that moment) and copied the .pyc into every
    workspace. An experiment whose control moves when someone runs the suite is not one."""
    src = Path(item_mod.ITEMS_DIR) / "u4-baseline"
    dst = tmp_path / "items" / "u4-baseline"
    shutil.copytree(src, dst)
    clean = item_mod.load("u4-baseline", tmp_path / "items")

    cache = dst / "files" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "slugify.cpython-313.pyc").write_bytes(b"\x00compiled\x00")
    (dst / "files" / ".DS_Store").write_bytes(b"junk")
    polluted = item_mod.load("u4-baseline", tmp_path / "items")

    assert polluted["digest"] == clean["digest"], "build artifacts changed the item digest"
    assert not any("__pycache__" in k or ".DS_Store" in k for k in polluted["planted"]), \
        "build artifacts would be planted into every run workspace"
