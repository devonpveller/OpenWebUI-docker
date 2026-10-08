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
from littlecoder.daemon import LittleCoderDaemon, build_app, count_tool_calls, workspace_marker
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


# ── round 2: edits made through bash are visible as a workspace fingerprint change ───────────


def _ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "todo.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (ws / "tests" / "test_todo.py").write_text("import todo\n", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return ws


def _bump(p):
    """A file rewrite the way `sed -i` does it: new content, newer mtime."""
    import os
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_marker_changes_on_sed_heredoc_and_new_files_only(tmp_path):
    ws = _ws(tmp_path)
    m0 = workspace_marker(str(ws), {}, ttl=0)
    assert m0 and m0 == workspace_marker(str(ws), {}, ttl=0)        # stable when nothing changed
    # sed -i: same path, same size possible - the mtime moves
    p = ws / "todo.py"
    p.write_text("def main():\n    return 1\n", encoding="utf-8")
    _bump(p)
    m1 = workspace_marker(str(ws), {}, ttl=0)
    assert m1 != m0
    # heredoc `cat > todo_filter.py <<EOF`: a new file
    (ws / "todo_filter.py").write_text("def keep(i, c):\n    return True\n", encoding="utf-8")
    m2 = workspace_marker(str(ws), {}, ttl=0)
    assert m2 != m1
    # NOT edits: git internals, bytecode caches
    (ws / ".git" / "FETCH_HEAD").write_text("abc\n", encoding="utf-8")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "todo.cpython-312.pyc").write_bytes(b"\x00")
    (ws / "tests" / "x.pyc").write_bytes(b"\x00")
    assert workspace_marker(str(ws), {}, ttl=0) == m2


def test_marker_is_bounded_and_cached(tmp_path):
    ws = _ws(tmp_path)
    assert workspace_marker(str(ws), {}, max_files=1, ttl=0) is None     # over the cap: unknown
    assert workspace_marker(str(tmp_path / "missing"), {}, ttl=0) is None
    cache: dict = {}
    m0 = workspace_marker(str(ws), cache, now=100.0, ttl=2.0)
    (ws / "new.py").write_text("x = 1\n", encoding="utf-8")
    assert workspace_marker(str(ws), cache, now=101.0, ttl=2.0) == m0   # within the ttl: reused
    assert workspace_marker(str(ws), cache, now=103.0, ttl=2.0) != m0   # after it: rescanned


def test_running_task_view_reports_the_workspace_marker(tmp_path):
    ws = _ws(tmp_path)
    cfg = Config()
    cfg.journals.dir = str(tmp_path / "journals")
    cfg.workspace.path = str(ws)
    d = LittleCoderDaemon(cfg)
    d.workspace = SimpleNamespace(is_focused=lambda: True)
    ot = tmp_path / "ot.jsonl"
    _append(ot, json.dumps({"command": "sed -i s/0/1/ todo.py", "exit_code": 0}) + "\n")
    st = TaskState(task_id="t1", session_id="s", channel="batch", user_id="u", prompt="p")
    st.status = TaskStatus.RUNNING
    st.event_stream_path = str(ot)
    d.tasks["t1"] = st
    v = TestClient(build_app(d)).get("/tasks/t1").json()
    assert v["workspace_marker"] == workspace_marker(str(ws), {}, ttl=0)


# ── round 3: one bad entry does not null the whole marker; the cap still does ─────────────────
def test_unreadable_subdir_keeps_a_marker(tmp_path, monkeypatch):
    import os
    from littlecoder import daemon as dm
    ws = _ws(tmp_path)
    (ws / "locked").mkdir()
    real = os.scandir

    def scandir(p):
        if str(p).endswith("locked"):
            raise PermissionError(13, "denied", str(p))
        return real(p)

    monkeypatch.setattr(dm.os, "scandir", scandir)
    m0 = workspace_marker(str(ws), {}, ttl=0)
    assert m0 is not None
    p = ws / "todo.py"
    p.write_text("def main():\n    return 2\n", encoding="utf-8")
    _bump(p)
    assert workspace_marker(str(ws), {}, ttl=0) not in (None, m0)    # edits elsewhere still seen


def test_file_vanishing_mid_scan_keeps_a_marker(tmp_path, monkeypatch):
    import os
    from littlecoder import daemon as dm
    ws = _ws(tmp_path)
    (ws / "sedXYZ").write_text("tmp", encoding="utf-8")
    real = os.scandir

    class _Gone:
        """A listed entry whose file no longer exists (DirEntry may cache stat on Windows, so the
        vanish is simulated at the entry)."""
        def __init__(self, e):
            self.name, self.path = e.name, e.path

        def is_dir(self, follow_symlinks=True):
            return False

        def stat(self, follow_symlinks=True):
            raise FileNotFoundError(2, "gone", self.path)

    class _It:
        def __init__(self, p):
            self._it = real(p)
            self._l = list(self._it)

        def __enter__(self):
            out = []
            for e in self._l:
                if e.name == "sedXYZ":      # sed -i's temp file, gone between listing and stat
                    out.append(_Gone(e))
                else:
                    out.append(e)
            return iter(out)

        def __exit__(self, *a):
            self._it.close()

    monkeypatch.setattr(dm.os, "scandir", lambda p: _It(p))
    m = workspace_marker(str(ws), {}, ttl=0)
    monkeypatch.undo()
    (ws / "sedXYZ").unlink()
    assert m is not None and m == workspace_marker(str(ws), {}, ttl=0)   # the vanished file is not in it


def test_cap_and_missing_root_still_give_no_marker(tmp_path):
    ws = _ws(tmp_path)
    assert workspace_marker(str(ws), {}, max_files=1, ttl=0) is None
    assert workspace_marker(str(tmp_path / "nope"), {}, ttl=0) is None
