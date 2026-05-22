"""Task context + per-task journal envelope (design §4.1, §4.2)."""

from littlecoder.tasks import TaskContext, TaskState, digest


def _state(**kw):
    base = dict(
        task_id="01J0000000000000000000000A",
        session_id="sess-1",
        channel="cli",
        user_id="cli",
        prompt="add a retry to the http client",
        repo="https://github.com/acme/widget",
        lang="python",
    )
    base.update(kw)
    return TaskState(**base)


def test_digest_is_stable_and_short():
    assert digest("hello") == digest("hello")
    assert len(digest("hello")) == 16
    assert digest("a") != digest("b")


def test_seq_increments_monotonically_across_record_types():
    ctx = TaskContext(_state())
    started = ctx.started()
    tc = ctx.tool_call("bash")
    err = ctx.error("test_failure", "boom")
    ended = ctx.ended("unverified")
    assert [started.seq, tc.seq, err.seq, ended.seq] == [0, 1, 2, 3]


def test_records_carry_the_envelope_from_state():
    ctx = TaskContext(_state(lang="rust"))
    rec = ctx.tool_call("bash")
    assert rec.task_id == "01J0000000000000000000000A"
    assert rec.session_id == "sess-1"
    assert rec.channel == "cli"
    assert rec.repo == "https://github.com/acme/widget"
    assert rec.lang == "rust"


def test_started_carries_a_prompt_digest_not_the_prompt():
    state = _state()
    rec = TaskContext(state).started()
    assert rec.trigger_digest == digest(state.prompt)
    assert state.prompt not in rec.model_dump_json()


def test_amendment_continues_the_sequence():
    ctx = TaskContext(_state())
    ctx.started()
    ctx.ended("unverified")
    amended = ctx.amended("pass", prior_outcome="unverified", amended_by="cli")
    assert amended.seq == 2
    assert amended.prior_outcome == "unverified"
    assert amended.outcome == "pass"


def test_public_view_previews_the_prompt():
    state = _state(prompt="x" * 500)
    pub = state.public()
    assert len(pub["prompt_preview"]) == 120
    assert "prompt" not in pub  # full prompt not dumped in the API view
