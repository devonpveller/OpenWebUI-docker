"""agent-org's side of the shared anchor (U2).

The module reads a bind-mounted schema that is NOT present in a test checkout, so these
tests cover both states on purpose — the degraded path is the one that runs on every dev
machine, and a module that guesses the anchor shape when it cannot read the schema would be
the third definition the cross-reader test exists to prevent.
"""
import pytest

from app.modules import anchors


@pytest.fixture(autouse=True)
def _reset_reader_cache(monkeypatch):
    # The reader is cached after the first attempt; tests must not inherit each other's.
    monkeypatch.setattr(anchors, "_reader", None, raising=False)
    monkeypatch.setattr(anchors, "_tried", False, raising=False)
    yield


# ── the pure builder ─────────────────────────────────────────────────────────
def test_the_anchor_is_mode_A():
    # B is a specified deliverable; A is "a theme walked toward, not a spec exhausted".
    # An agent-org effort is the second thing - which is why §6.6 keeps the original prompt.
    a = anchors.build_effort_anchor(north_star="ship the thing", audience="the operator")
    assert a["mode"] == "A"


def test_the_north_star_is_carried_verbatim():
    # Not a summary and not the scope goal: a re-derived goal drifts, and an anchor built
    # from a drifted goal certifies the drift instead of catching it.
    prompt = "Make the drain converge without the planner churning, and say how you know."
    a = anchors.build_effort_anchor(north_star=prompt, audience="the operator")
    assert a["north_star"] == prompt


def test_blank_constraints_are_dropped_rather_than_stored_empty():
    a = anchors.build_effort_anchor(
        north_star="x", audience="y", constraints=["real", "  ", None, ""])
    assert a["constraints"] == ["real"]


def test_no_constraints_means_the_key_is_absent():
    # An empty list would read as "the ledger is empty", which is a claim; absence is not.
    a = anchors.build_effort_anchor(north_star="x", audience="y")
    assert "constraints" not in a


# ── rendering ────────────────────────────────────────────────────────────────
def test_render_carries_the_guard_substring():
    # Every injection site in the orchestrator guards on its block's own header.
    out = anchors.render(anchors.build_effort_anchor(north_star="x", audience="y"))
    assert "EFFORT ANCHOR" in out


def test_render_states_what_realignment_is_against():
    out = anchors.render(anchors.build_effort_anchor(north_star="ship it", audience="ops"))
    assert "ship it" in out and "ops" in out
    assert "not against a re-derived scope goal" in out


def test_render_of_nothing_is_nothing():
    # Not an empty header. A brief that announces an anchor and shows none is a claim that
    # the work has no north star, which is different from silence.
    assert anchors.render({}) == ""
    assert anchors.render({"audience": "someone"}) == ""


# ── the degraded path: no mounted schema ─────────────────────────────────────
def test_available_is_False_without_the_mount(monkeypatch):
    monkeypatch.setattr(anchors, "ANCHOR_DIR", "/nonexistent/anchor")
    assert anchors.available() is False


def test_problems_reports_NOTHING_when_it_cannot_read_the_schema(monkeypatch):
    # Inventing problems from a module that cannot read the schema is worse than reporting
    # none: a caller would act on a verdict nothing produced.
    monkeypatch.setattr(anchors, "ANCHOR_DIR", "/nonexistent/anchor")
    assert anchors.problems({"anything": True}) == []


def test_is_usable_is_FALSE_when_it_cannot_read_the_schema(monkeypatch):
    """The deliberate asymmetry with problems().

    A caller asking "is this usable?" is about to rely on the anchor, and an unreadable
    schema is not permission. A caller asking "what is wrong?" is reporting, and inventing
    findings is the worse failure there.
    """
    monkeypatch.setattr(anchors, "ANCHOR_DIR", "/nonexistent/anchor")
    assert anchors.is_usable({"mode": "A", "north_star": "x", "audience": "y"}) is False


def test_a_broken_reader_never_raises_into_a_caller(monkeypatch):
    class _Boom:
        def load(self):
            raise RuntimeError("schema is corrupt")

        def problems(self, a):
            raise RuntimeError("boom")

        def is_usable(self, a):
            raise RuntimeError("boom")

    monkeypatch.setattr(anchors, "_reader", _Boom(), raising=False)
    monkeypatch.setattr(anchors, "_tried", True, raising=False)
    assert anchors.problems({}) == []
    assert anchors.is_usable({}) is False


# ── with the real schema, when it IS readable ────────────────────────────────
def _real_reader():
    """The actual harness reader, imported from the repo rather than the mount."""
    import sys
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "scripts" / "agent-harness"
        if (cand / "anchor_schema.py").exists():
            sys.path.insert(0, str(cand))
            import anchor_schema  # type: ignore

            return anchor_schema
    return None


@pytest.mark.skipif(_real_reader() is None, reason="harness schema not in this checkout")
def test_a_built_anchor_SATISFIES_the_shared_schema(monkeypatch):
    """The seam that matters: what agent-org builds must be what the harness accepts.

    Two systems agreeing on a field NAME while disagreeing on what makes it valid is the
    whole failure U2 exists to close, and it is invisible until something checks.
    """
    reader = _real_reader()
    monkeypatch.setattr(anchors, "_reader", reader, raising=False)
    monkeypatch.setattr(anchors, "_tried", True, raising=False)
    a = anchors.build_effort_anchor(
        north_star="Make the drain converge without the planner churning.",
        audience="the operator running this stack",
    )
    assert anchors.problems(a) == []
    assert anchors.is_usable(a) is True


@pytest.mark.skipif(_real_reader() is None, reason="harness schema not in this checkout")
def test_an_anchor_MISSING_its_north_star_is_refused(monkeypatch):
    # RED proof that the check above is load-bearing rather than tautological.
    reader = _real_reader()
    monkeypatch.setattr(anchors, "_reader", reader, raising=False)
    monkeypatch.setattr(anchors, "_tried", True, raising=False)
    bad = anchors.build_effort_anchor(north_star="", audience="someone")
    assert anchors.problems(bad) != []
    assert anchors.is_usable(bad) is False
