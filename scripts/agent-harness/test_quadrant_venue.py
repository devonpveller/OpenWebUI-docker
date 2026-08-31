"""Executable proof for THE VENUE - the place a comparison was performed on.

Split from test_quadrant.py because it proves a different claim. That file proves a
comparison cannot lie about WHAT ran; this one proves it cannot lie about WHERE.

The defect these tests exist because of, stated plainly. dark-factory PLAN section 2 binds
the term four lines above the phase table - "'gym' means measured runs in
`ai-orchestration-gym`, never live planes or a real target" - and U4's column begins
"Gym:". A four-cell comparison was produced with every mechanism in this package satisfied:
real dispatches, real acceptance runs, a pinned declared matrix, exit 0. It ran against
ai-stack. `target: self` resolved to the repository the harness lives in, the config's own
restatement of the column had dropped the word "Gym:", and NOTHING in the record, the
report or the exit code could say so. A verifier had to notice by reading a path.

So the venue is now data (quadrant/venue.py, quadrant/schema.json's venue_kinds), and each
test below is one sentence of that verifier's report turned into an exit code.

Run:  python -m pytest scripts/agent-harness/test_quadrant_venue.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from quadrant import matrix as matrix_mod      # noqa: E402
from quadrant import proc as _proc             # noqa: E402
from quadrant import record as record_mod      # noqa: E402
from quadrant import report as report_mod      # noqa: E402
from quadrant import venue as venue_mod        # noqa: E402

import config as harness_config                # noqa: E402


SCHEMA = matrix_mod.schema()


# ---------------------------------------------------------------- helpers --

def git(repo: Path, *args: str):
    out = _proc.run(["git", "-C", str(repo), *args])
    assert out.returncode == 0, f"git {' '.join(args)} failed in {repo}: {out.stderr}"
    return out


def make_repo(path: Path, *, branch: str = "main", content: str = "hello\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _proc.run(["git", "init", "-q", "-b", branch, str(path)])
    (path / "README.md").write_text(content, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return path


def cfg(venue_name: str = "arena", **venue_over) -> dict:
    v = {"kind": "gym", "repo": "../ai-orchestration-gym", "ref": "main"}
    v.update(venue_over)
    return {
        "runners": {"fixture": {"kind": "fixture", "status": "self-test"}},
        "targets": {"self": {"kind": "self", "status": "proven"},
                    "project": {"kind": "project", "status": "unproven",
                                "scratch_root": ".quadrant/scratch"}},
        "quadrant": {"runners": ["fixture"], "targets": ["self"], "repeats": 1,
                     "venue": venue_name, "venues": {venue_name: v}},
    }


def resolved(tmp_path: Path, **venue_over) -> venue_mod.Venue:
    return venue_mod.resolve(cfg(**venue_over), SCHEMA, harness_repo=tmp_path)


# ------------------------------------------------- the config layer is loud --

def test_a_configuration_with_no_venue_fails_loudly():
    """A comparison that cannot say where it ran cannot be judged against a 'Gym:' column,
    so the absence is a CONFIG ERROR - the loud kind - and not a default."""
    c = cfg()
    del c["quadrant"]["venue"]
    with pytest.raises(matrix_mod.QuadrantConfigError) as exc:
        matrix_mod.build(c)
    assert "venue" in str(exc.value).lower()


def test_a_venue_the_config_never_defines_fails_loudly():
    c = cfg()
    c["quadrant"]["venue"] = "somewhere-else"
    with pytest.raises(matrix_mod.QuadrantConfigError) as exc:
        matrix_mod.build(c)
    assert "somewhere-else" in str(exc.value)


def test_a_venue_kind_the_harness_has_no_rules_for_fails_loudly():
    """Same rule as an unknown runner transport: a kind the harness cannot check is one it
    must not silently accept. Inventing `kind: "sandbox"` must not buy a free pass."""
    c = cfg(kind="sandbox")
    with pytest.raises(matrix_mod.QuadrantConfigError) as exc:
        matrix_mod.build(c)
    assert "sandbox" in str(exc.value)


def test_a_venue_missing_its_repo_fails_loudly():
    c = cfg()
    c["quadrant"]["venues"]["arena"].pop("repo")
    with pytest.raises(matrix_mod.QuadrantConfigError) as exc:
        matrix_mod.build(c)
    assert "repo" in str(exc.value)


# ---------------------------------------------------- THE check that matters --

def test_a_gym_venue_resolving_to_the_harness_repo_is_a_venue_violation(tmp_path):
    """THE ONE. This is the exact run that shipped: everything ready, subject = ai-stack."""
    repo = make_repo(tmp_path / "ai-stack")
    v = venue_mod.resolve(cfg(repo=str(repo)), SCHEMA, harness_repo=repo)
    res = venue_mod.probe(v, harness_repo=repo)
    assert not res.ready
    assert "VENUE VIOLATION" in res.reason
    assert "never live planes or a real target" in res.reason


def test_a_worktree_of_the_harness_repo_is_still_the_harness_repo(tmp_path):
    """A path comparison would pass this and be wrong. Harness sessions run in
    `.claude/worktrees/wt-<id>`, so 'a different directory' is not 'a different repo' - the
    comparison is over the GIT COMMON DIR, which every worktree of a repo shares."""
    repo = make_repo(tmp_path / "ai-stack")
    wt = tmp_path / "wt-x"
    git(repo, "worktree", "add", "--detach", str(wt), "HEAD")
    v = venue_mod.resolve(cfg(repo=str(wt)), SCHEMA, harness_repo=repo)
    res = venue_mod.probe(v, harness_repo=repo)
    assert not res.ready and "VENUE VIOLATION" in res.reason


def test_a_gym_venue_in_a_different_repository_is_ready(tmp_path):
    repo = make_repo(tmp_path / "ai-stack")
    arena = make_repo(tmp_path / "arena")
    v = venue_mod.resolve(cfg(repo=str(arena)), SCHEMA, harness_repo=repo)
    res = venue_mod.probe(v, harness_repo=repo)
    assert res.ready, res.reason
    assert res.detail["kind"] == "gym"


def test_a_workspace_venue_may_be_the_harness_repo_and_says_it_is_not_a_gym_run(tmp_path):
    """The escape hatch is legitimate and it is NOT silent: kind `workspace` is allowed to
    be this repository, and `satisfies_gym_column` is false, so choosing it cannot quietly
    produce evidence for a column that begins 'Gym:'."""
    repo = make_repo(tmp_path / "ai-stack")
    v = venue_mod.resolve(cfg(kind="workspace", repo=str(repo)), SCHEMA, harness_repo=repo)
    assert venue_mod.probe(v, harness_repo=repo).ready
    assert not v.satisfies_gym_column


def test_a_venue_ref_that_does_not_exist_is_blocked_rather_than_guessed(tmp_path):
    repo = make_repo(tmp_path / "ai-stack")
    arena = make_repo(tmp_path / "arena", branch="main")
    v = venue_mod.resolve(cfg(repo=str(arena), ref="no-such-branch"), SCHEMA,
                          harness_repo=repo)
    res = venue_mod.probe(v, harness_repo=repo)
    assert not res.ready and "no-such-branch" in res.reason


def test_a_venue_directory_that_is_not_a_repository_root_is_blocked(tmp_path):
    """Two ways this can be true and both must block. If nothing encloses the path it is
    'not a git repository'; if something does, it is 'NOT a repository root'. The second
    branch is the dangerous one and it is the one this machine actually took."""
    repo = make_repo(tmp_path / "ai-stack")
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    v = venue_mod.resolve(cfg(repo=str(plain)), SCHEMA, harness_repo=repo)
    res = venue_mod.probe(v, harness_repo=repo)
    assert not res.ready
    assert ("not a git repository" in res.reason
            or "NOT a repository root" in res.reason), res.reason


def test_a_venue_path_inside_a_repository_does_not_silently_adopt_that_repository(tmp_path):
    """THE HAZARD, deterministic. git discovers UPWARD, so a wrong arena path resolves to
    whatever repo encloses it rather than failing. Measured while writing these tests:
    C:/Users/<user> is itself a git repository on this machine, so every path under the
    user's home - the system temp directory included - answers `git rev-parse` with the
    HOME repo. A mistyped arena path would have made the operator's personal repository the
    subject of an experiment; PLAN C.2 class 4 forbids exactly that."""
    repo = make_repo(tmp_path / "ai-stack")
    arena = make_repo(tmp_path / "arena")
    inner = arena / "scenarios" / "not-the-root"
    inner.mkdir(parents=True)
    v = venue_mod.resolve(cfg(repo=str(inner)), SCHEMA, harness_repo=repo)
    res = venue_mod.probe(v, harness_repo=repo)
    assert not res.ready
    assert "NOT a repository root" in res.reason
    assert str(arena.resolve()).lower() in res.reason.lower(),         "the reason must name the repository that would have been adopted"


# ------------------------------------------------------------- resolution --

def test_a_relative_venue_path_resolves_from_the_main_checkout_not_the_worktree(tmp_path):
    """Sessions run in `.claude/worktrees/wt-<id>`. Resolving '../arena' against the
    worktree would name `.claude/worktrees/arena` - a directory that does not exist - and
    the resulting BLOCKED cell would have blamed the arena for a resolution bug."""
    repo = make_repo(tmp_path / "ai-stack")
    make_repo(tmp_path / "arena")
    wt = repo / ".claude" / "worktrees" / "wt-x"
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "--detach", str(wt), "HEAD")

    v = venue_mod.resolve(cfg(repo="../arena"), SCHEMA, harness_repo=wt)
    assert v.repo == (tmp_path / "arena").resolve(), \
        "a relative venue path must resolve from the main checkout"
    assert venue_mod.probe(v, harness_repo=wt).ready


def test_an_explicit_repo_override_beats_the_config_and_says_so(tmp_path):
    arena = make_repo(tmp_path / "arena")
    v = venue_mod.resolve(cfg(), SCHEMA, harness_repo=tmp_path,
                          override_repo=str(arena))
    assert v.repo == arena.resolve()
    assert v.source == "--repo", "the provenance of the path is part of the answer"


def test_the_env_var_a_venue_declares_beats_the_configured_path(tmp_path, monkeypatch):
    arena = make_repo(tmp_path / "arena")
    monkeypatch.setenv("QUADRANT_TEST_ARENA", str(arena))
    v = venue_mod.resolve(cfg(repo_env="QUADRANT_TEST_ARENA"), SCHEMA, harness_repo=tmp_path)
    assert v.repo == arena.resolve()
    assert v.source == "$QUADRANT_TEST_ARENA"


# ------------------------------------------------- the venue reaches a cell --

def test_a_venue_violation_blocks_every_cell_with_that_reason(tmp_path):
    """The runner and the target are both ready; only the PLACE is wrong. Before this, such
    a cell ran, completed and produced evidence for a column it did not satisfy."""
    repo = make_repo(tmp_path / "ai-stack")
    c = cfg(repo=str(repo))
    q = matrix_mod.build(c)[0]
    v = venue_mod.resolve(c, SCHEMA, harness_repo=repo)
    pf = matrix_mod.preflight(q, c, repo=v.repo, venue=v, harness_repo=repo,
                              scratch_root=str(tmp_path / "scratch"))
    assert not pf.ready
    assert "VENUE VIOLATION" in pf.reason


def test_target_self_is_a_worktree_of_the_venues_ref_not_the_callers_head(tmp_path):
    """`target: self` means 'the repository the org is working in'. Which repository that is
    is the VENUE's answer - the adapter used to hardcode HEAD, and the only repo a hardcoded
    HEAD can name is the caller's."""
    from quadrant import adapters
    make_repo(tmp_path / "ai-stack", content="ai-stack\n")
    arena = make_repo(tmp_path / "arena", branch="harness", content="gym harness branch\n")
    (arena / "ARENA.md").write_text("the training arena\n", encoding="utf-8")
    git(arena, "checkout", "-q", "-b", "main")
    git(arena, "add", "-A")
    git(arena, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "-m", "arena")
    git(arena, "checkout", "-q", "harness")

    c = cfg(repo=str(arena), ref="main")
    q = [x for x in matrix_mod.build(c) if x.target == "self"][0]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ws = adapters.prepare_target(q, c, run_dir=run_dir, repo=arena,
                                 scratch_root=tmp_path / "scratch", ref="main")
    try:
        assert (ws / "ARENA.md").is_file(), "the workspace is not the venue's ref"
        assert not (ws / "README.md").read_text(encoding="utf-8").startswith("ai-stack")
    finally:
        _proc.run(["git", "-C", str(arena), "worktree", "remove", "--force", str(ws)])


# ------------------------------------------------------- records and report --

def _rec(**over):
    rec = {
        "venue": {"name": "arena", "kind": "gym", "repo": "/arena", "ref": "main",
                  "source": "config"},
        "quadrant": "fixture::self", "runner": "fixture", "target": "self",
        "item": "u4-baseline", "item_digest": "d" * 64, "status": "not_run",
        "not_run_reason": "blocked for a reason", "started_utc": "2026-08-30T10:00:00Z",
        "ended_utc": "2026-08-30T10:00:01Z",
    }
    rec.update(over)
    return rec


def test_a_record_that_names_no_venue_is_refused():
    """Records produced before 2026-08-30 carry no venue by construction. They were real
    runs in the wrong place, and the honest rendering of that is a refusal with a reason -
    not a silent inclusion, and not a deletion of the evidence."""
    rec = _rec()
    rec.pop("venue")
    problems = record_mod.admit(rec, item_digest="d" * 64, venue="arena")
    assert any("VENUE" in p for p in problems)


def test_a_record_from_another_venue_is_refused():
    rec = _rec(venue={"name": "somewhere-else", "kind": "gym", "repo": "/x", "ref": "main"})
    problems = record_mod.admit(rec, item_digest="d" * 64, venue="arena")
    assert any("venue mismatch" in p for p in problems)


def test_a_record_from_the_pinned_venue_is_admitted():
    assert record_mod.admit(_rec(), item_digest="d" * 64, venue="arena") == []


def test_the_report_states_the_venue_and_whether_it_satisfies_the_gym_column(tmp_path):
    repo = make_repo(tmp_path / "ai-stack")
    arena = make_repo(tmp_path / "arena")
    c = cfg(repo=str(arena))
    qs = matrix_mod.build(c)
    v = venue_mod.resolve(c, SCHEMA, harness_repo=repo)
    md = report_mod.render(qs, [], item={"id": "u4-baseline", "digest": "d" * 64}, venue=v)
    assert "Venue:" in md and "arena" in md
    assert "SATISFIES" in md

    wv = venue_mod.resolve(cfg(kind="workspace", repo=str(repo)), SCHEMA, harness_repo=repo)
    md2 = report_mod.render(qs, [], item={"id": "u4-baseline", "digest": "d" * 64}, venue=wv)
    assert "does NOT satisfy" in md2


def test_a_report_with_no_venue_says_UNSTATED_rather_than_nothing():
    qs = matrix_mod.build(cfg())
    md = report_mod.render(qs, [], item={"id": "u4-baseline", "digest": "d" * 64})
    assert "Venue: UNSTATED" in md


def test_the_results_set_pins_its_venue_and_a_later_run_cannot_move_the_pin(tmp_path):
    """A results set is a comparison over one PLACE, the same way it is over one item and
    one set of cells. Re-pointing --repo and re-running into the same directory must not
    mix two experiments into one table."""
    from quadrant import cli
    repo = make_repo(tmp_path / "ai-stack")
    arena = make_repo(tmp_path / "arena")
    other = make_repo(tmp_path / "other-arena")
    out = tmp_path / "runs"
    out.mkdir()

    c = cfg(repo=str(arena))
    qs = matrix_mod.build(c)
    v = venue_mod.resolve(c, SCHEMA, harness_repo=repo)
    declared, pinned = cli._declared_matrix(out, qs, [], v)
    assert pinned == "arena"
    lock = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    assert lock["venue"]["repo"] == str(arena.resolve())

    v2 = venue_mod.resolve(cfg("elsewhere", repo=str(other)), SCHEMA, harness_repo=repo)
    declared2, pinned2 = cli._declared_matrix(out, qs, [], v2)
    assert pinned2 == "arena", "the pin moved - a results set would then mix two venues"
    lock2 = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    assert lock2["venue"]["repo"] == str(arena.resolve())


# ------------------------------------------------------ live config sanity --

def test_a_results_set_that_predates_the_venue_is_not_stamped_with_todays_one(tmp_path):
    """PINNING IS NOT LABELLING. A pre-venue results set has no pin, and taking one from the
    configuration would put "Venue: gym - SATISFIES a 'Gym:' column" at the top of a report
    whose every record ran somewhere else - the exact mislabel the mechanism exists to stop.
    Found by re-rendering the historical results set after the mechanism landed."""
    from quadrant import cli
    repo = make_repo(tmp_path / "ai-stack")
    arena = make_repo(tmp_path / "arena")
    out = tmp_path / "runs"
    out.mkdir()

    c = cfg(repo=str(arena))
    qs = matrix_mod.build(c)
    v = venue_mod.resolve(c, SCHEMA, harness_repo=repo)
    old_record = {"quadrant": "fixture::self", "runner": "fixture", "target": "self",
                  "item": "u4-baseline", "item_digest": "d" * 64, "status": "not_run",
                  "not_run_reason": "from before the venue existed"}

    declared, pinned = cli._declared_matrix(out, qs, [old_record], v)
    assert pinned == "", "a set whose records name no venue must not be stamped with one"
    lock = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    assert not lock.get("venue")

    md = report_mod.render(qs, [old_record], item={"id": "u4-baseline", "digest": "d" * 64},
                           declared=declared, venue=None)
    assert "Venue: UNSTATED" in md

    # ...while a set with nothing to contradict the venue DOES take the pin.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert cli._declared_matrix(fresh, qs, [], v)[1] == "arena"


def test_the_live_config_selects_a_gym_venue_that_satisfies_the_column():
    c = harness_config.load(fresh=True)
    v = venue_mod.resolve(c, SCHEMA, harness_repo=HERE)
    assert v.kind == "gym"
    assert v.satisfies_gym_column
    assert v.repo.name == "ai-orchestration-gym", \
        f"the configured arena is {v.repo}, which is not the gym"


def test_the_live_config_restates_the_gym_clause_of_the_u4_column():
    """The clause that was DROPPED. harness.config.json quoted U4's column starting at
    'same anchored item', so the one clause naming the venue was the one clause missing
    from the config's own copy of the requirement."""
    text = (HERE / "harness.config.json").read_text(encoding="utf-8")
    comment = json.loads(text)["quadrant"]["_comment"]
    joined = " ".join(comment)
    assert "Gym:" in joined, "the config's restatement of U4's column dropped 'Gym:' again"
    assert "ai-orchestration-gym" in joined
