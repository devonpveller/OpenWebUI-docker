"""The daily sweep's SELECTION (dark-factory-unification U2).

`sweep_targets` is pure, which is the point: it holds the only judgement in the sweep — what
needs a plan — and it can be tested without GitHub, without a headless model run, and
without writing a plan file. The loop around it has no judgement in it.

    python -m pytest scripts/issue-ops/test_sweep.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import issue_ops  # noqa: E402


def _issue(n: int, updated: str = "2026-01-01T00:00:00Z") -> dict:
    return {"number": n, "title": f"issue {n}", "updated_at": updated, "labels": []}


@pytest.fixture
def fake(monkeypatch):
    """Drive selection off in-memory plans, so no file or network is involved."""
    plans: dict = {}
    freshness: dict = {}

    monkeypatch.setattr(issue_ops, "read_plan", lambda n: plans.get(n))
    monkeypatch.setattr(issue_ops, "plan_freshness",
                        lambda meta, issue, branch: freshness.get(issue["number"], "fresh"))
    return plans, freshness


def test_an_unplanned_issue_is_a_target(fake):
    plans, _ = fake
    assert issue_ops.sweep_targets([_issue(1)], "main") == [(1, "unplanned")]


def test_a_FRESH_plan_is_never_regenerated(fake):
    """The sweep runs unattended against a headless model.

    Regenerating fresh plans would burn a model run per issue per day, and worse, churn the
    plan text under a human part-way through reviewing it.
    """
    plans, _ = fake
    plans[1] = {"status": "planned"}
    assert issue_ops.sweep_targets([_issue(1)], "main") == []


def test_a_stale_plan_is_a_target_and_the_REASON_is_carried(fake):
    # The reason is what a human reads in the sweep's output, and it is what decides whether
    # the regenerate is a refresh or a first write.
    plans, freshness = fake
    plans[1] = {"status": "planned"}
    freshness[1] = "stale-code (+9 commits past base)"
    assert issue_ops.sweep_targets([_issue(1)], "main") == [
        (1, "stale-code (+9 commits past base)")]


def test_a_stale_issue_is_a_target_too(fake):
    plans, freshness = fake
    plans[1] = {"status": "planned"}
    freshness[1] = "stale-issue (issue edited after planning)"
    assert issue_ops.sweep_targets([_issue(1)], "main")[0][1].startswith("stale-issue")


def test_selection_is_per_issue_not_all_or_nothing(fake):
    plans, freshness = fake
    plans[2] = {"status": "planned"}          # fresh -> skipped
    plans[3] = {"status": "planned"}
    freshness[3] = "stale-code (+5 commits past base)"
    got = issue_ops.sweep_targets([_issue(1), _issue(2), _issue(3)], "main")
    assert [n for n, _ in got] == [1, 3]


def test_no_open_issues_is_no_targets(fake):
    assert issue_ops.sweep_targets([], "main") == []


def test_selection_does_not_write_or_call_anything(fake, monkeypatch):
    """Selection must stay pure: the sweep decides, then acts, in that order.

    If selection could generate a plan, `--dry-run` would silently do the thing it exists to
    avoid — and a dry run that is not dry is worse than no dry run.
    """
    def boom(*a, **k):
        raise AssertionError("selection must not generate plans")

    monkeypatch.setattr(issue_ops, "cmd_plan", boom)
    plans, freshness = fake
    freshness[1] = "stale-code"
    plans[1] = {"status": "planned"}
    issue_ops.sweep_targets([_issue(1)], "main")


# ── the sweep loop ───────────────────────────────────────────────────────────
@pytest.fixture
def swept(monkeypatch):
    """Run cmd_sweep against fake selection + a recording cmd_plan."""
    calls: list = []

    monkeypatch.setattr(issue_ops, "target_branch", lambda: ("main", True))
    monkeypatch.setattr(issue_ops, "open_issues", lambda: [_issue(1), _issue(2), _issue(3)])
    monkeypatch.setattr(issue_ops, "sweep_targets",
                        lambda issues, branch: [(1, "unplanned"), (2, "stale-code"), (3, "unplanned")])
    monkeypatch.setattr(issue_ops, "cmd_plan",
                        lambda n, refresh=False: calls.append((n, refresh)) or 0)
    return calls


def test_dry_run_generates_nothing(swept, capsys):
    assert issue_ops.cmd_sweep(dry_run=True) == 0
    assert swept == []
    assert "3 needing a plan" in capsys.readouterr().out


def test_a_stale_target_is_planned_with_refresh(swept):
    issue_ops.cmd_sweep()
    assert (2, True) in swept        # stale -> refresh
    assert (1, False) in swept       # unplanned -> first write


def test_the_limit_is_reported_not_silent(swept, capsys):
    """An unattended job that quietly does 3 of 40 looks identical to one with 3 to do."""
    issue_ops.cmd_sweep(limit=1)
    out = capsys.readouterr().out
    assert "TRUNCATED: 2" in out
    assert len(swept) == 1


def test_one_failing_issue_does_not_stop_the_sweep(monkeypatch, swept):
    def flaky(n, refresh=False):
        if n == 1:
            raise RuntimeError("model run failed")
        swept.append((n, refresh))
        return 0

    monkeypatch.setattr(issue_ops, "cmd_plan", flaky)
    # Partial success is success: the next issue may be the important one, and a job that
    # dies on the first error plans nothing all day.
    assert issue_ops.cmd_sweep() == 0
    assert [n for n, _ in swept] == [2, 3]


def test_a_TOTAL_failure_is_reported_as_failure(monkeypatch, swept):
    monkeypatch.setattr(issue_ops, "cmd_plan",
                        lambda n, refresh=False: (_ for _ in ()).throw(RuntimeError("down")))
    assert issue_ops.cmd_sweep() == 1


def test_nothing_to_do_is_success(monkeypatch):
    monkeypatch.setattr(issue_ops, "target_branch", lambda: ("main", True))
    monkeypatch.setattr(issue_ops, "open_issues", lambda: [])
    monkeypatch.setattr(issue_ops, "sweep_targets", lambda issues, branch: [])
    assert issue_ops.cmd_sweep() == 0


def test_the_sweep_never_approves_or_executes(monkeypatch, swept):
    """The door produces anchor-DRAFTS. Selection happens at the weekly verdict thread.

    §C.3 decision 5: "the daily sweep takes everything; selection happens at the weekly
    verdict thread". A sweep that could approve would be a door with no human in it.
    """
    for forbidden in ("cmd_execute", "cmd_gate", "cmd_gate_plan"):
        monkeypatch.setattr(issue_ops, forbidden,
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError(f"sweep must not call {forbidden}")))
    issue_ops.cmd_sweep()


def test_a_dry_run_does_not_claim_it_planned_anything(swept, capsys):
    """A report that overstates itself is worse than no report.

    The first version printed "3 planned, 0 failed" for a run that generated nothing, and
    the next person reads that log as evidence the plans exist.
    """
    issue_ops.cmd_sweep(dry_run=True)
    out = capsys.readouterr().out
    assert "would plan 3" in out
    assert "generated nothing" in out
    assert "3 planned," not in out
