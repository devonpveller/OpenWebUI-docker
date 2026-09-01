"""Guards for the durable check born from U3's tester finding.

`scripts/checks/check_quadrant_evidence_reproduces.py` is banked in the shared durable-check
registry, which means it runs against every future results set. The GYM DRILL
(`u3_evidence_regression_gym.py`) proves it catches real seeded regressions in the arena, but
that needs the arena and real run evidence. These are the fast guards: they run anywhere, in
under a second, and they are what stops the banked check from quietly becoming decorative.

Each test is one sentence of the check's contract, and two of them are the exact defects the
drill found in the check ITSELF on its first two runs - the absolute-path read-through, and
the fallback that made a deleted workspace look reproducible.

The last two guard the drill's COUNTERFACTUAL rather than the check: `copy_run` is what makes
a sandbox copy describe itself, and without it the pre-existing gate the drill executes was
reading the untouched originals and reporting every seed missed.

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
             marker: str = "kept.txt", rel: str = "runs") -> Path:
    """A minimal results set whose acceptance command is re-runnable anywhere.

    The command asserts a file the run "kept" is still there, which is the shape of the real
    frozen-file guard without needing the item machinery.
    """
    results = tmp_path.joinpath(*rel.split("/"))
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


def test_a_record_whose_recorded_command_died_with_its_worktree_re_derives_from_the_template(tmp_path):
    """The defect that destroyed U4's first evidence set, one layer down.

    `item.expand` records the EXACT command that ran, which embeds this machine's
    interpreter and the producing WORKTREE's `guards.py`. Both are gone once that worktree
    is removed, so an auditor in a fresh clone re-runs a path instead of the evidence and
    gets a red that is about the filesystem. A record that also carries `check_template`
    (the unexpanded criterion) is re-derivable anywhere.

    RED WITHOUT THE FIX: the recorded `check` names an executable that does not exist, so
    re-running it cannot return the recorded exit code and the set reads NOT REPRODUCIBLE.
    """
    results = make_set(tmp_path)
    rec_path = next(results.glob("*/record.json"))
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    entry = rec["acceptance"][0]
    entry["check_template"] = entry["check"]
    entry["check"] = '"C:/gone-with-the-worktree/python.exe" -c "pass"'
    rec_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")

    res = run_check(str(results))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "re-derived their verdict" in res.stdout


def test_the_guards_placeholder_is_re_expanded_against_the_checking_checkout():
    """`{guards}` must resolve to THIS checkout's guard runner, not the producing one."""
    sys.path.insert(0, str(REPO / "scripts" / "checks"))
    import check_quadrant_evidence_reproduces as chk  # noqa: PLC0415

    cmd, how = chk._runnable({"check_template": "{guards} tests --item {item}",
                              "check": "whatever ran on some other machine"}, "u4-baseline")
    guards = REPO / "scripts" / "agent-harness" / "quadrant" / "guards.py"
    assert str(guards) in cmd, cmd
    assert "u4-baseline" in cmd and "{item}" not in cmd, cmd
    assert "re-expanded" in how

    # No template: the recorded command is used, and the derivation SAYS it is machine-bound
    # rather than implying the same guarantee.
    cmd2, how2 = chk._runnable({"check": "the recorded one"}, "u4-baseline")
    assert cmd2 == "the recorded one" and "machine-bound" in how2


def test_auto_discovery_reaches_the_committed_evidence_root(tmp_path):
    """`--auto` must find the COMMITTED set, or the banked check audits nothing in a clone.

    RED WITHOUT THE FIX: `_discover` looked only under `.quadrant/`, which `.gitignore`
    excludes - so every set it could ever find was one a fresh clone does not have.
    """
    sys.path.insert(0, str(REPO / "scripts" / "checks"))
    import check_quadrant_evidence_reproduces as chk  # noqa: PLC0415

    committed = tmp_path / "documentation" / "evidence" / "dfu-u4" / "quadrant" / "run-1"
    committed.mkdir(parents=True)
    (committed / "record.json").write_text("{}", encoding="utf-8")
    working = tmp_path / ".quadrant" / "runs" / "run-1"
    working.mkdir(parents=True)
    (working / "record.json").write_text("{}", encoding="utf-8")

    found = {p.as_posix() for p in chk._discover(tmp_path)}
    assert (tmp_path / "documentation" / "evidence" / "dfu-u4" / "quadrant").as_posix() in found, found
    assert (tmp_path / ".quadrant" / "runs").as_posix() in found, found


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


# ---------------------------------------------------------------------------------------
# THE DRILL'S COUNTERFACTUAL. Round 7's finding: `record.admit` follows each record's
# ABSOLUTE `evidence.workspace`, so the drill's "pre-existing gate" column was measured
# against `.quadrant/gym-runs` - not against the sandbox the seeds were written into. Every
# seed came back "missed", including seed C, which the drill's own docstring says the gate
# is expected to catch. `copy_run` is the fix; these two tests are what stop it regressing.
# ---------------------------------------------------------------------------------------

def _drill():
    sys.path.insert(0, str(HERE))
    import u3_evidence_regression_gym as drill  # noqa: PLC0415
    return drill


def test_copy_run_makes_the_copy_describe_itself_so_the_gate_reads_the_seeded_tree(tmp_path):
    """RED before this existed: admission walked the absolute path back to the original."""
    sys.path.insert(0, str(HERE))
    from quadrant import record as record_mod   # noqa: PLC0415

    results = make_set(tmp_path)
    src = next(results.glob("*/record.json")).parent
    dest = tmp_path / "sandbox" / src.name
    dest.parent.mkdir(parents=True)
    _drill().copy_run(src, dest)

    rec = json.loads((dest / "record.json").read_text(encoding="utf-8"))
    assert Path(rec["evidence"]["workspace"]).resolve() == (dest / "workspace").resolve(), \
        "the copy still points at the original, so anything reading it measures that tree"
    assert any("sandbox copy" in n for n in rec.get("notes") or []), \
        "a rewritten path must be disclosed in the record it was rewritten into"

    # ...and now the pre-existing gate SEES a seed made in the copy.
    drill_mod = _drill()
    drill_mod.rmtree(dest / "workspace")
    problems = record_mod.admit(rec, item_digest=str(rec.get("item_digest") or ""),
                                venue=str((rec.get("venue") or {}).get("name") or ""))
    assert any("workspace" in p for p in problems), \
        "the seeded deletion is invisible to the gate the drill calls its counterfactual"
    assert (src / "workspace").is_dir(), "the ORIGINAL evidence must never be touched"


def test_copy_run_leaves_a_workspace_recorded_outside_the_run_dir_alone(tmp_path):
    """Only paths INSIDE the source run directory are relocated. A record naming a
    workspace somewhere else is describing how it was produced, and rewriting that would be
    inventing evidence rather than moving it."""
    results = make_set(tmp_path)
    src = next(results.glob("*/record.json")).parent
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rp = src / "record.json"
    rec = json.loads(rp.read_text(encoding="utf-8"))
    rec["evidence"]["workspace"] = str(elsewhere)
    rp.write_text(json.dumps(rec, indent=2), encoding="utf-8")

    dest = tmp_path / "sandbox2" / src.name
    dest.parent.mkdir(parents=True)
    _drill().copy_run(src, dest)
    out = json.loads((dest / "record.json").read_text(encoding="utf-8"))
    assert out["evidence"]["workspace"] == str(elsewhere)


# ---------------------------------------------------------------------------
# "no evidence here" vs "the committed evidence is gone" - the asymmetry that made the
# banked check unable to detect the defect it was built for. Added 2026-08-31 from a
# verifier finding: with `documentation/evidence/dfu-u4/` deleted, `--auto` printed a
# vacuous pass and returned 0, while WALKTHROUGH.md credited it with reaching that set.
# ---------------------------------------------------------------------------

def chk_module():
    sys.path.insert(0, str(REPO / "scripts" / "checks"))
    import check_quadrant_evidence_reproduces as chk  # noqa: PLC0415
    return chk


def git_repo_with_committed_set(tmp_path: Path, **kw) -> Path:
    """A tiny git checkout whose INDEX tracks one results set under the committed root.

    `git add` is enough: the expectation is read from the index, because `rm -rf` on the
    evidence leaves the index untouched and that is the loss this must catch immediately.
    """
    results = make_set(tmp_path, rel="documentation/evidence/dfu-u4/quadrant", **kw)
    for args in (["init", "-q"], ["add", "-A", "--", "documentation"]):
        proc = subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True,
                              encoding="utf-8", errors="replace")
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return results


def test_auto_reds_when_the_committed_evidence_is_missing_from_the_checkout(tmp_path, capsys,
                                                                           monkeypatch):
    """THE DEFECT. A checkout is not an arbitrary machine: it holds the records its index
    tracks, or it has LOST them, and those are different facts that must get different exit
    codes.

    RED WITHOUT THE FIX: `--auto` discovered nothing, printed "that is a vacuous pass", and
    returned 0 - the same 0 as a healthy tree.
    """
    import shutil  # noqa: PLC0415
    chk = chk_module()
    results = git_repo_with_committed_set(tmp_path)
    monkeypatch.setattr(chk, "_repo_root", lambda: tmp_path)

    assert chk.main(["--auto"]) == 0, capsys.readouterr().out

    shutil.rmtree(tmp_path / "documentation" / "evidence" / "dfu-u4")
    assert not results.exists()
    rc = chk.main(["--auto"])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "MISSING COMMITTED EVIDENCE" in out, out
    # It must NAME what is missing - a red that does not say what is gone sends the reader
    # back to guessing, which is what the vacuous pass did.
    assert "documentation/evidence/dfu-u4/quadrant" in out, out
    assert "vacuous" not in out, out


def test_a_tree_that_is_not_a_git_checkout_stays_vacuous_and_says_which_case(tmp_path, capsys,
                                                                            monkeypatch):
    """One of the two cases that are STILL genuinely vacuous - and it must identify itself,
    because "nothing to check" without a reason is the sentence that hid the defect."""
    chk = chk_module()
    monkeypatch.setattr(chk, "_repo_root", lambda: tmp_path)
    rc = chk.main(["--auto"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "vacuous pass" in out and "not a git checkout" in out, out


def test_a_git_checkout_that_banks_no_evidence_stays_vacuous_and_says_which_case(tmp_path,
                                                                                capsys,
                                                                                monkeypatch):
    """The second genuinely vacuous case: a real checkout whose index tracks no records."""
    chk = chk_module()
    proc = subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], capture_output=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    monkeypatch.setattr(chk, "_repo_root", lambda: tmp_path)
    rc = chk.main(["--auto"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "vacuous pass" in out and "banks no evidence" in out, out


def test_a_committed_record_refused_at_admission_is_red_not_skipped(tmp_path, capsys,
                                                                   monkeypatch):
    """The same vacuity one layer down. A record the repository COMMITS but `record.admit`
    refuses enters no comparison, so counting it "skipped" beside exit 0 banks a directory
    nobody may cite. Working evidence under `.quadrant/` keeps the skip - that is where the
    pre-venue records live."""
    chk = chk_module()
    git_repo_with_committed_set(tmp_path)
    monkeypatch.setattr(chk, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(chk, "_admissible", lambda rec: (False, "record names no venue"))

    rc = chk.main(["--auto"])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "COMMITTED to the repository" in out and "REFUSES it" in out, out
    assert "SKIPPED" not in out, out


def test_a_refused_record_that_is_only_WORKING_evidence_is_still_skipped(tmp_path, capsys,
                                                                        monkeypatch):
    """The other half of the same rule, so the change above cannot be read as "admission
    skips are gone". An untracked set under `.quadrant/` is skipped and counted, exit 0."""
    chk = chk_module()
    make_set(tmp_path, rel=".quadrant/runs")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], capture_output=True,
                   encoding="utf-8", errors="replace")
    monkeypatch.setattr(chk, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(chk, "_admissible", lambda rec: (False, "record names no venue"))

    rc = chk.main(["--auto"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "SKIPPED (not in any comparison)" in out, out


def test_every_message_naming_the_search_roots_is_derived_from_the_roots(tmp_path, capsys,
                                                                        monkeypatch):
    """DEFECT 2's guard. `DISCOVERY_ROOTS` gained a second entry and two hand-written
    sentences went on naming only `.quadrant/`, so the message a future auditor reads named
    the wrong search root. A third root must not be able to desynchronise the text again.

    RED WITHOUT THE FIX: the "nothing to check" line was a literal string ending
    "under .quadrant/".
    """
    chk = chk_module()
    monkeypatch.setattr(chk, "DISCOVERY_ROOTS", (".quadrant", "documentation/evidence",
                                                 "somewhere/else"))
    phrase = chk._roots_phrase()
    for root in (".quadrant/", "documentation/evidence/", "somewhere/else/"):
        assert root in phrase, phrase

    monkeypatch.setattr(chk, "_repo_root", lambda: tmp_path)
    assert chk.main(["--auto"]) == 0
    assert phrase in capsys.readouterr().out


def test_the_docstring_describes_auto_by_the_roots_definition_not_by_a_literal_root():
    """The stale sibling was PROSE, and prose is what an auditor reads.

    RED WITHOUT THE FIX: the `--auto` paragraph said "discover every results set under
    `.quadrant/`" while `DISCOVERY_ROOTS` had held two entries since the round before. The
    rule is narrow on purpose - the docstring may name `.quadrant/` when saying where
    WORKING evidence lives; what it may not do is describe the SEARCH SCOPE with a literal
    path, because that is the copy that goes stale.
    """
    chk = chk_module()
    doc = chk.__doc__ or ""
    # the DESCRIPTION paragraph, not the usage line that also carries the flag: it
    # starts at "    --auto  " and runs to the blank line before the exit codes
    marker = "\n    --auto  "
    assert marker in doc, "the docstring no longer documents --auto"
    body = doc.split(marker, 1)[1].split("\n\n", 1)[0]
    assert "DISCOVERY_ROOTS" in body, body
    for root in chk.DISCOVERY_ROOTS:
        assert root not in body, (
            f"the --auto description hard-codes the root {root!r}; it must point at "
            f"DISCOVERY_ROOTS, which is the one place they are defined")

