"""ao-abandoncarry F5 - a cancelled task stays ABANDONED with the cancel's detail.

`cancel()` marks a running task ABANDONED ("cancelled by the operator") and kills the agent; the
agent thread then returns with an exception (or the per-task timeout fires). `_run_task` used to
overwrite that with DONE/unverified ("daemon error: ...") - so a cancelled turn reached the bridge
as `done` and was published - or with the timeout's detail, which the bridge reads as a task
timeout and re-dispatches. Drives the real daemon (`_run_task`, `cancel`, GET /tasks/<id>) with
the agent's `run_task` replaced; no model, no container."""

from __future__ import annotations

import pytest

from littlecoder.agent import TaskTimeout
from test_per_task_model import _daemon, _run_queued


def _queue_one(client) -> None:
    r = client.post("/tasks", json={"prompt": "edit one file", "channel": "batch"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("after_cancel", [RuntimeError("agent killed: broken pipe"),
                                          TaskTimeout("late timeout")],
                         ids=["exception", "timeout"])
def test_a_task_cancelled_mid_run_stays_abandoned(tmp_path, monkeypatch, after_cancel):
    d, client, _log = _daemon(tmp_path, monkeypatch)
    _queue_one(client)

    def run_task(ctx, timeout):
        d.cancel(ctx.state.task_id)        # the operator / bridge cancels while the agent runs
        raise after_cancel             # ... and the killed agent's thread then fails
    monkeypatch.setattr(d.agent, "run_task", run_task)
    tid = _run_queued(d)
    got = client.get(f"/tasks/{tid}").json()
    assert got["status"] == "abandoned", got
    assert got["detail"] == "cancelled by the operator", got


def test_an_exception_without_a_cancel_is_still_a_daemon_error(tmp_path, monkeypatch):
    d, client, _log = _daemon(tmp_path, monkeypatch)
    _queue_one(client)

    def run_task(ctx, timeout):
        raise RuntimeError("boom")
    monkeypatch.setattr(d.agent, "run_task", run_task)
    tid = _run_queued(d)
    got = client.get(f"/tasks/{tid}").json()
    assert got["status"] == "done" and got["detail"].startswith("daemon error: boom"), got


def test_a_timeout_without_a_cancel_is_still_the_timeout(tmp_path, monkeypatch):
    d, client, _log = _daemon(tmp_path, monkeypatch)
    _queue_one(client)

    def run_task(ctx, timeout):
        raise TaskTimeout("over")
    monkeypatch.setattr(d.agent, "run_task", run_task)
    tid = _run_queued(d)
    got = client.get(f"/tasks/{tid}").json()
    assert got["status"] == "abandoned" and got["detail"].startswith("abandoned: exceeded"), got
