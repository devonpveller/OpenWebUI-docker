"""ao-loopguard (agent-org gym-002, 2026-10-07) - the live task view reports how many edit/write tool
calls the turn has made.

The agent-org bridge's loop guard sees only the bash `activity` stream. Edits (the edit/write tools)
never appear there, so an edit-then-test turn (edit, run tests, edit, run tests) looks like the same
test command over and over - indistinguishable from a loop. GET /tasks/<id> now carries `edits` (and
`tool_calls`) for a running task, counted from the pi events journal in O(new bytes).
"""

from __future__ import annotations

import builtins
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from littlecoder.config import Config
from littlecoder.daemon import LittleCoderDaemon, build_app, count_tool_calls
from littlecoder.tasks import TaskState, TaskStatus


def _ev(tool: str) -> str:
    return json.dumps({"type": "tool_execution_start", "toolName": tool, "args": {}}) + "\n"


def _append(p, *lines: str) -> None:
    with open(p, "ab") as fh:                                 # bytes: no CRLF translation
        fh.writelines(ln.encode() for ln in lines)


def test_counts_tool_calls_and_edits_incrementally(tmp_path):
    p = str(tmp_path / "pi.jsonl")
    _append(p, _ev("bash"), _ev("read"), json.dumps({"type": "message_update"}) + "\n", _ev("edit"))
    cache: dict = {}
    assert count_tool_calls(p, cache) == (3, 1)
    _append(p, _ev("bash"), _ev("write"), _ev("Edit"))
    assert count_tool_calls(p, cache) == (6, 3)
    assert count_tool_calls(p, cache) == (6, 3)              # nothing new: unchanged


def test_partial_trailing_line_is_left_for_the_next_probe(tmp_path):
    p = str(tmp_path / "pi.jsonl")
    _append(p, _ev("edit"))
    _append(p, _ev("write")[:-10])                            # the writer is mid-line
    cache: dict = {}
    assert count_tool_calls(p, cache) == (1, 1)
    _append(p, _ev("write")[-10:])
    assert count_tool_calls(p, cache) == (2, 2)


def test_probe_reads_only_new_bytes(tmp_path, monkeypatch):
    p = str(tmp_path / "pi.jsonl")
    _append(p, *[_ev("bash")] * 5000)
    cache: dict = {}
    count_tool_calls(p, cache)
    _append(p, _ev("edit"))
    total = 0
    real_open = builtins.open

    class Spy:
        def __init__(self, fh): self.fh = fh
        def __enter__(self): self.fh.__enter__(); return self
        def __exit__(self, *a): return self.fh.__exit__(*a)
        def fileno(self): return self.fh.fileno()
        def seek(self, *a): return self.fh.seek(*a)
        def readlines(self):
            nonlocal total
            out = self.fh.readlines(); total += sum(len(x) for x in out); return out

    monkeypatch.setattr("littlecoder.daemon.open", lambda *a, **k: Spy(real_open(*a, **k)),
                        raising=False)
    assert count_tool_calls(p, cache) == (5001, 1)
    assert total == len(_ev("edit").encode())


def test_running_task_view_reports_edits(tmp_path):
    cfg = Config()
    cfg.journals.dir = str(tmp_path / "journals")
    cfg.workspace.path = str(tmp_path)
    d = LittleCoderDaemon(cfg)
    d.workspace = SimpleNamespace(is_focused=lambda: True)
    pi = tmp_path / "pi.jsonl"
    ot = tmp_path / "ot.jsonl"
    _append(pi, _ev("bash"), _ev("edit"), _ev("bash"))
    _append(ot, json.dumps({"command": "python -m unittest", "exit_code": 0}) + "\n")
    st = TaskState(task_id="t1", session_id="s", channel="batch", user_id="u", prompt="p")
    st.status = TaskStatus.RUNNING
    st.events_path, st.event_stream_path = str(pi), str(ot)
    d.tasks["t1"] = st
    client = TestClient(build_app(d))      # no `with`: the lifespan (workers) never starts
    v = client.get("/tasks/t1").json()
    assert v["edits"] == 1 and v["tool_calls"] == 3 and v["commands"] == 1
    _append(pi, _ev("write"))
    assert client.get("/tasks/t1").json()["edits"] == 2
    # a finished task's view is unchanged (no live counters)
    st.status = TaskStatus.DONE
    assert "edits" not in client.get("/tasks/t1").json()
