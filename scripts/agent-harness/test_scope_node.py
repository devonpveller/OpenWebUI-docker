"""A queue item is a depth-1 ScopeNode (U2).

The drift guard is the point of this file. A mapping written once and never checked is two
vocabularies again within a month — and this time it would LOOK unified, which is worse than
the honest disagreement U2 set out to fix. So the test reads agent-org's actual `models.py`
and asserts every column of the real `ScopeNode` is either produced by the projection or
explicitly declined with a reason.

    python -m pytest scripts/agent-harness/test_scope_node.py -q
"""

import json
import re
from pathlib import Path

import pytest

import scope_node

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MODELS = REPO / "agent-org" / "agent-bridge" / "app" / "models.py"


ITEM = {
    "id": "widget",
    "line": "ai-stack",
    "developer": "wt-widget",
    "state": "queued",
    "anchor": {
        "goal": "The drain converges without the planner churning. Second sentence ignored.",
        "artifact": "scripts/drain.ps1 and its test",
        "audience": "the operator",
        "acceptance": ["the drain finishes under 5 minutes", "no churn in the log"],
        "out_of_scope": ["the viewer", "the graph layer"],
    },
}


# ── the shape ────────────────────────────────────────────────────────────────
def test_a_queue_item_is_depth_ONE():
    # Depth 0 is the project. An item is one bounded tier below it, handed to a developer
    # who is deliberately unaware of the rest - which is what a worktree already is.
    n = scope_node.from_queue_item(ITEM)
    assert n["depth"] == 1
    assert n["parent_id"] is None


def test_scope_is_what_IS_and_what_IS_NOT():
    # ScopeNode.scope is documented as "what IS and ISN'T this tier's business". The anchor
    # already asks that question twice, under different names.
    n = scope_node.from_queue_item(ITEM)
    assert "IS: scripts/drain.ps1" in n["scope"]
    assert "IS NOT: the viewer; the graph layer" in n["scope"]


def test_the_contract_is_the_acceptance_and_NOTHING_ELSE():
    # §11: a boundary defined in prose is not encapsulation. The contract is the executable
    # check, so it comes from `acceptance` - never from the goal or the artifact text.
    n = scope_node.from_queue_item(ITEM)
    assert n["contract"] == "the drain finishes under 5 minutes\nno churn in the log"
    assert "drain converges" not in (n["contract"] or "")


def test_no_acceptance_means_NO_contract_not_a_prose_stand_in():
    item = {**ITEM, "anchor": {**ITEM["anchor"], "acceptance": []}}
    assert scope_node.from_queue_item(item)["contract"] is None


def test_the_title_is_one_sentence_and_fits_the_column():
    n = scope_node.from_queue_item(ITEM)
    assert n["title"] == "The drain converges without the planner churning"
    assert len(n["title"]) <= 200


def test_a_long_goal_is_truncated_to_the_column_width():
    item = {**ITEM, "anchor": {**ITEM["anchor"], "goal": "x" * 500}}
    assert len(scope_node.from_queue_item(item)["title"]) <= 200


def test_an_item_with_no_anchor_still_projects():
    # The queue holds pre-anchor rows. A projection that refused them would be unusable for
    # exactly the items a reviewer most wants to look at.
    n = scope_node.from_queue_item({"id": "bare", "state": "anchor-draft"})
    assert n["id"] == "bare"
    assert n["title"] == "bare"
    assert n["scope"] == ""
    assert n["contract"] is None


def test_a_malformed_anchor_does_not_raise():
    for bad in ("a string", 7, [], None):
        n = scope_node.from_queue_item({"id": "x", "anchor": bad})
        assert n["scope"] == ""


# ── status mapping ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("state,expected", [
    ("merged", "done"),
    ("rejected", "blocked"),
    ("failed", "blocked"),
    ("queued", "open"),
    ("in-test", "open"),
    ("anchor-draft", "open"),
    ("", "open"),
    ("something-invented-later", "open"),
])
def test_queue_state_maps_into_ScopeNode_status(state, expected):
    # Unknown states fall to 'open' on purpose: calling unfinished work 'done' is the error
    # that matters, and a state added later must not silently close a scope.
    assert scope_node.from_queue_item({"id": "x", "state": state})["status"] == expected


# ── THE DRIFT GUARD ──────────────────────────────────────────────────────────
def _scope_node_columns() -> list:
    """Column names of agent-org's real ScopeNode, read from models.py.

    Parsed rather than imported: importing agent-org's models pulls SQLAlchemy and the whole
    app package into the harness's test run, and the harness must not depend on the bridge
    being installable. Parsing is the weaker tool and the right one - it needs only the file
    the two systems are supposed to agree about.
    """
    src = MODELS.read_text(encoding="utf-8")
    body = src[src.index("class ScopeNode(Base):"):]
    body = body[: body.index("\nclass ")] if "\nclass " in body else body
    return re.findall(r"^\s{4}(\w+):\s*Mapped\[", body, re.M)


@pytest.mark.skipif(not MODELS.exists(), reason="agent-org models.py not in this checkout")
def test_every_ScopeNode_column_is_produced_or_explicitly_declined():
    """The guard that keeps this a unification rather than a resemblance.

    A column added to ScopeNode upstream fails here until someone decides whether the harness
    can fill it. That decision is cheap now and invisible later.
    """
    produced = set(scope_node.from_queue_item(ITEM))
    unmapped = [c for c in _scope_node_columns()
                if c not in produced and c not in scope_node.DECLINED]
    assert not unmapped, (
        f"ScopeNode columns neither produced nor declined: {unmapped}. Add them to the "
        f"projection, or to scope_node.DECLINED with the reason they cannot be filled."
    )


@pytest.mark.skipif(not MODELS.exists(), reason="agent-org models.py not in this checkout")
def test_the_drift_guard_can_FAIL():
    """RED proof: the guard reads the file, so a new column really would trip it."""
    cols = _scope_node_columns()
    assert cols, "parsed no columns - the guard would pass vacuously"
    produced = set(scope_node.from_queue_item(ITEM))
    pretend = cols + ["a_column_nobody_mapped"]
    unmapped = [c for c in pretend
                if c not in produced and c not in scope_node.DECLINED]
    assert unmapped == ["a_column_nobody_mapped"]


@pytest.mark.skipif(not MODELS.exists(), reason="agent-org models.py not in this checkout")
def test_declined_columns_are_real_columns():
    # A stale DECLINED entry would silence a guard for a column that no longer exists, and
    # nothing else would notice.
    cols = set(_scope_node_columns())
    stale = [c for c in scope_node.DECLINED if c not in cols]
    assert not stale, f"DECLINED names columns ScopeNode no longer has: {stale}"


def test_the_projection_produces_no_field_ScopeNode_lacks():
    # The other direction. An invented field would be a third vocabulary rather than a
    # shared one, and it would read as unification.
    if not MODELS.exists():
        pytest.skip("agent-org models.py not in this checkout")
    cols = set(_scope_node_columns())
    extra = [k for k in scope_node.from_queue_item(ITEM) if k not in cols]
    assert not extra, f"projection invents fields ScopeNode does not have: {extra}"


# ── reading a real queue directory ───────────────────────────────────────────
def test_sidecar_files_are_not_work_items(tmp_path):
    # `<id>.anchor.json` sits beside `<id>.json`; queue.ps1 -List uses the same dot rule, and
    # the two have to agree about what an item is or they report different queues.
    (tmp_path / "real.json").write_text(json.dumps({"id": "real"}), encoding="utf-8")
    (tmp_path / "real.anchor.json").write_text(json.dumps({"goal": "g"}), encoding="utf-8")
    assert [i["id"] for i in scope_node.load_queue(tmp_path)] == ["real"]


def test_a_corrupt_row_does_not_hide_the_rest_of_the_queue(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps({"id": "good"}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert [i["id"] for i in scope_node.load_queue(tmp_path)] == ["good"]


def test_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert scope_node.load_queue(tmp_path / "nope") == []


def test_the_live_queue_projects_without_raising():
    """Against the real queue if one exists - the rows this will actually meet."""
    # Via --git-common-dir, not REPO/".git": inside a worktree `.git` is a FILE pointing at
    # the shared dir, so the naive path resolves to nothing and this test would skip exactly
    # where the harness actually runs - a skip that reads as coverage.
    import subprocess
    common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True)
    if common.returncode != 0:
        pytest.skip("not a git checkout")
    root = Path(common.stdout.strip())
    if not root.is_absolute():
        root = (REPO / root).resolve()
    live = root / "agent-worktrees" / "queue"
    if not live.is_dir():
        pytest.skip("no live queue in this checkout")
    nodes = scope_node.project_queue(live, project_slug="ai-stack")
    assert all(n["depth"] == 1 for n in nodes)
    assert all(n["status"] in ("open", "done", "blocked") for n in nodes)
