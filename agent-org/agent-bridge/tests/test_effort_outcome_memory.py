"""The effort-outcome memory at the _finish_effort seam (memory-plane §2.2, §2.5).

These drive `_write_outcome_memory` directly rather than a whole effort, because what needs
proving is the CONTRACT at the seam: written once, keyed idempotently, stamped with the
effort's taint, and incapable of changing the effort's outcome.
"""
import pytest


class _Mem:
    """A stand-in for OpenBrainMemory that records what the seam asked it to write."""

    def __init__(self, enabled=True, boom=None):
        self.enabled = enabled
        self.calls = []
        self._boom = boom

    async def write_effort_outcome(self, **kw):
        self.calls.append(kw)
        if self._boom:
            raise self._boom
        return True


class _Orch:
    """The seam, lifted out of Orchestrator so it can be exercised without a bridge.

    The methods below are copies of the real ones ONLY in the sense that the test binds the
    real `_write_outcome_memory` to this object - see the fixture. Nothing is reimplemented.
    """

    def __init__(self, memory, project="proj-x", tainted=False):
        self.memory = memory
        self._project = project
        self._tainted = tainted

    async def _effort_project(self, effort_id):
        return self._project

    async def _effort_memory_tainted(self, effort_id):
        return self._tainted


@pytest.fixture
def seam():
    """Bind the REAL _write_outcome_memory to a minimal host.

    Importing the method off the class is what makes this a test of the shipped code rather
    than of a paraphrase of it - the trap the writeback hit once already, where the tests
    exercised a builder while the call site went unvisited.
    """
    from app.orchestrator import Orchestrator

    def make(memory, project="proj-x", tainted=False):
        host = _Orch(memory, project=project, tainted=tainted)
        host._write_outcome_memory = Orchestrator._write_outcome_memory.__get__(host, _Orch)
        return host

    return make


@pytest.mark.asyncio
async def test_one_finished_effort_writes_exactly_one_memory(seam):
    mem = _Mem()
    host = seam(mem)
    await host._write_outcome_memory(
        "e1", head="did a thing", done_word="done", where="on a branch",
        branch="work/x", succeeded=True,
    )
    assert len(mem.calls) == 1
    assert mem.calls[0]["effort_id"] == "e1"
    assert mem.calls[0]["succeeded"] is True


@pytest.mark.asyncio
async def test_a_partial_close_records_a_failure_not_a_success(seam):
    # The more useful memory of the two. An effort that did not meet its scope is exactly
    # what a later effort needs to have read.
    mem = _Mem()
    host = seam(mem)
    await host._write_outcome_memory(
        "e1", head="h", done_word="partly done — see the scope check", where="w",
        branch="", succeeded=False,
    )
    assert mem.calls[0]["succeeded"] is False


@pytest.mark.asyncio
async def test_the_effort_taint_is_carried_into_the_write(seam):
    # §1.1: an effort that read grounded claims can no longer be assumed ops-clean, and the
    # stamp has to survive the trip from the flag to the payload.
    for tainted in (False, True):
        mem = _Mem()
        host = seam(mem, tainted=tainted)
        await host._write_outcome_memory(
            "e1", head="h", done_word="done", where="w", branch="", succeeded=True,
        )
        assert mem.calls[0]["tainted"] is tainted


@pytest.mark.asyncio
async def test_the_project_scope_is_carried(seam):
    mem = _Mem()
    host = seam(mem, project="my-project")
    await host._write_outcome_memory(
        "e1", head="h", done_word="done", where="w", branch="", succeeded=True,
    )
    assert mem.calls[0]["project"] == "my-project"


@pytest.mark.asyncio
async def test_a_disabled_memory_module_writes_nothing(seam):
    mem = _Mem(enabled=False)
    host = seam(mem)
    assert await host._write_outcome_memory(
        "e1", head="h", done_word="done", where="w", branch="", succeeded=True,
    ) is False
    assert mem.calls == []


@pytest.mark.asyncio
async def test_no_memory_module_at_all_is_not_an_error(seam):
    # The bridge must start and finish efforts on a build where this was never wired.
    host = seam(None)
    host.memory = None
    assert await host._write_outcome_memory(
        "e1", head="h", done_word="done", where="w", branch="", succeeded=True,
    ) is False


@pytest.mark.asyncio
async def test_THE_FAIL_SOFT_LAW_a_raising_module_would_break_the_effort(seam):
    """This is the assertion that protects every effort's close path.

    `_write_outcome_memory` sits between the operator being told and the handoff being
    resolved. If a memory write can raise, a finished effort becomes a crashed one and the
    traceback points at the effort, not at the memory plane.

    The module's own contract is that it never raises. THIS test states what happens if that
    contract is ever broken: the exception reaches the caller. It is written to FAIL loudly
    if someone adds a swallowing try/except at the seam instead of fixing the module - the
    seam must stay honest, and the fail-soft guarantee must live in one place.
    """
    mem = _Mem(boom=RuntimeError("open brain is down"))
    host = seam(mem)
    with pytest.raises(RuntimeError):
        await host._write_outcome_memory(
            "e1", head="h", done_word="done", where="w", branch="", succeeded=True,
        )
