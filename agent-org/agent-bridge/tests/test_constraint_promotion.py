"""Constraint promotion, green-close only (memory-plane §2.3, §2.5).

A constraint is a dead end the work actually walked into. Effort-scoped it dies with the
effort, and the next effort re-walks it — that is the gap this closes.

The governance assertion is the one that matters: this promotes into REVIEWABLE MEMORIES and
never into `acceptance_checks`, which are executable merge gates.
"""
import pytest


class _Mem:
    def __init__(self, enabled=True, ok=True):
        self.enabled = enabled
        self.calls = []
        self._ok = ok

    async def write_constraint(self, **kw):
        self.calls.append(kw)
        return self._ok

    async def write_effort_outcome(self, **kw):
        return True


class _Host:
    def __init__(self, memory, constraints, project="proj-x", tainted=False):
        self.memory = memory
        self._constraints = constraints
        self._project = project
        self._tainted = tainted
        self.acceptance_checks_added = []

    async def _list_constraints(self, effort_id):
        return list(self._constraints)

    async def _effort_project(self, effort_id):
        return self._project

    async def _effort_memory_tainted(self, effort_id):
        return self._tainted


@pytest.fixture
def promote():
    """Bind the REAL method, so this tests shipped code rather than a paraphrase."""
    from app.orchestrator import Orchestrator

    def make(constraints, memory=None, **kw):
        host = _Host(memory or _Mem(), constraints, **kw)
        host._promote_constraints_to_memory = (
            Orchestrator._promote_constraints_to_memory.__get__(host, _Host)
        )
        return host

    return make


FAILURE = {"id": "c1", "sig": "s1", "kind": "failure", "body": "the drain stalls when the planner churns"}
OFF_THEME = {"id": "c2", "sig": "s2", "kind": "off_theme", "body": "do not add a GUI"}
DEFECT = {"id": "c3", "sig": "s3", "kind": "defect", "body": "worker skipped the plan gate"}


@pytest.mark.asyncio
async def test_failure_clauses_are_promoted(promote):
    host = promote([FAILURE])
    assert await host._promote_constraints_to_memory("e1") == 1
    assert host.memory.calls[0]["constraint_id"] == "c1"
    assert host.memory.calls[0]["text"] == FAILURE["body"]


@pytest.mark.asyncio
async def test_only_failure_clauses_are_promoted(promote):
    # off_theme narrows GENERATION rather than recording a dead end; a defect is about
    # conduct, not about the code. Neither is a fact about the project worth carrying.
    host = promote([FAILURE, OFF_THEME, DEFECT])
    assert await host._promote_constraints_to_memory("e1") == 1
    assert [c["constraint_id"] for c in host.memory.calls] == ["c1"]


@pytest.mark.asyncio
async def test_promotion_NEVER_writes_an_acceptance_check(promote):
    """§2.3's explicit rejection, as a test.

    acceptance_checks are EXECUTABLE MERGE GATES. Auto-promoting prose into one would make
    the org start blocking merges on text no human approved - the propose-not-dispose posture
    inverted. A reviewer can still elevate any of these by hand, which is the whole design.
    """
    host = promote([FAILURE])
    await host._promote_constraints_to_memory("e1")
    assert host.acceptance_checks_added == []


@pytest.mark.asyncio
async def test_the_memory_is_keyed_on_the_constraint_so_a_reclose_is_a_noop(promote):
    # _finish_effort is reachable more than once. Two promotions must be one memory, and the
    # key is what makes that true server-side.
    host = promote([FAILURE])
    await host._promote_constraints_to_memory("e1")
    await host._promote_constraints_to_memory("e1")
    keys = {c["constraint_id"] for c in host.memory.calls}
    assert keys == {"c1"}, "both rounds must address the same memory"


@pytest.mark.asyncio
async def test_taint_is_carried_into_promoted_constraints(promote):
    host = promote([FAILURE], tainted=True)
    await host._promote_constraints_to_memory("e1")
    assert host.memory.calls[0]["tainted"] is True


@pytest.mark.asyncio
async def test_an_empty_constraint_body_is_skipped_not_written_blank(promote):
    host = promote([{"id": "c9", "kind": "failure", "body": "   "}])
    assert await host._promote_constraints_to_memory("e1") == 0
    assert host.memory.calls == []


@pytest.mark.asyncio
async def test_nothing_learned_means_nothing_written(promote):
    host = promote([])
    assert await host._promote_constraints_to_memory("e1") == 0


@pytest.mark.asyncio
async def test_a_disabled_module_promotes_nothing(promote):
    host = promote([FAILURE], memory=_Mem(enabled=False))
    assert await host._promote_constraints_to_memory("e1") == 0
    assert host.memory.calls == []


@pytest.mark.asyncio
async def test_a_refused_write_is_counted_honestly(promote):
    # The count is what a caller would log. Reporting successes it did not have would make
    # a silent outage look like a quiet project.
    host = promote([FAILURE], memory=_Mem(ok=False))
    assert await host._promote_constraints_to_memory("e1") == 0
    assert len(host.memory.calls) == 1
