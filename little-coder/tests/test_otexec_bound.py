"""ao-exechang: ot-exec's bounded run against a scripted open-terminal (httpx.MockTransport).

What the real open-terminal does is exercised end to end by tests/node/e2e_exechang (a real
open-terminal container, the real ot-exec, the real pi + extension). These tests pin the client
side: the order of calls (kill before report), every wait bounded, how each ending is labelled,
and what text the model is given.
"""

from __future__ import annotations

import json
import signal
import time

import httpx
import pytest

from littlecoder import otexec
from littlecoder.openterminal import OpenTerminalClient, parse_exec_output


@pytest.fixture(autouse=True)
def _keep_sigterm():
    """ot-exec installs / ignores / resets a SIGTERM handler; give the test process its own back."""
    saved = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, saved)


class FakeOT:
    """A scripted open-terminal. `script` decides, per status request, whether the command is
    still running; `rc_after` makes the wrapper's status file appear after N status requests."""

    def __init__(self, *, done_after=None, rc_after=None, rc="0", output="out\n",
                 reap="LC_REAP killed=0 survivors=0 group=77", fail_on=None, fail_reaper=False,
                 stop_on=None):
        self.done_after = done_after   # status calls until the command is done (None: never)
        self.rc_after = rc_after       # status calls until the rc file exists (None: never)
        self.rc = rc
        self.out = output
        self.reap = reap
        self.fail_on = fail_on         # (method, path-prefix) that answers 500
        self.fail_reaper = fail_reaper
        self.stop_on = stop_on         # (method, path-prefix) that raises the SIGTERM exception
        self.calls: list[tuple[str, str, dict]] = []
        self.status_calls = 0
        self.killed = False
        self.bodies: list[dict] = []

    def _running(self):
        return not self.killed and (self.done_after is None or self.status_calls < self.done_after)

    def _proc(self, pid, params):
        running = self._running()
        data = [{"type": "output", "data": self.out}]
        if params.get("tail"):
            data = data[-int(params["tail"]):]
        return {"id": pid, "status": "running" if running else "done",
                "exit_code": None if running else 0, "output": data}

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        params = dict(request.url.params)
        self.calls.append((method, path, params))
        if self.stop_on and method == self.stop_on[0] and path.startswith(self.stop_on[1]):
            raise otexec._Stopped()
        if self.fail_on and method == self.fail_on[0] and path.startswith(self.fail_on[1]):
            return httpx.Response(500, json={"detail": "boom"})
        if method == "POST" and path == "/execute":
            body = json.loads(request.content)
            self.bodies.append(body)
            if "LC_REAP" in body["command"]:
                if self.fail_reaper:
                    return httpx.Response(503, json={})
                return httpx.Response(200, json={"id": "reaper", "status": "done", "exit_code": 0,
                                                 "output": [{"type": "output", "data": self.reap + "\r\n"}]})
            if self.done_after == 0:
                return httpx.Response(200, json={"id": "p1", "status": "done", "exit_code": 0,
                                                 "output": [{"type": "output", "data": self.out}]})
            return httpx.Response(200, json={"id": "p1", "status": "running", "exit_code": None,
                                             "output": [{"type": "output", "data": self.out}]})
        if method == "GET" and path == "/execute/p1/status":
            if float(params.get("wait", 0)) > 0:
                self.status_calls += 1
                time.sleep(min(float(params["wait"]), 0.02))
            return httpx.Response(200, json=self._proc("p1", params))
        if method == "GET" and path == "/files/read":
            if self.rc_after is not None and self.status_calls >= self.rc_after:
                return httpx.Response(200, json={"content": self.rc + "\n"})
            return httpx.Response(404, json={"detail": "File not found"})
        if method == "DELETE" and path == "/execute/p1":
            self.killed = True
            return httpx.Response(200, json={"status": "killed"})
        if method == "DELETE" and path == "/files/delete":
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    def client(self) -> OpenTerminalClient:
        return OpenTerminalClient("http://ot:8000", "k", transport=httpx.MockTransport(self.handler))

    def seq(self):
        return [(m, p) for m, p, _ in self.calls]


def _run(ot: FakeOT, timeout=0.3, command="pytest -q tests"):
    return otexec.run_command(ot.client(), command, timeout, exec_id="e1", slice_s=0.05)


# --- the wrapper ---------------------------------------------------------------------------------

def test_the_command_travels_in_the_environment_never_in_the_shell_text():
    ot = FakeOT(done_after=0)
    cmd = "echo 'a' \"$HOME\" $(id) `x`; exit 3 # )"
    _run(ot, command=cmd)
    body = ot.bodies[0]
    assert body["env"] == {"LC_EXEC_CMD": cmd}
    assert cmd not in body["command"]
    assert body["command"] == otexec._wrapper("e1")


def test_wrapper_detaches_stdin_marks_the_tree_and_records_the_exit_status():
    w = otexec._wrapper("e1")
    assert 'LC_EXEC_ID=e1 sh -c "$c" </dev/null' in w
    assert "unset LC_EXEC_CMD" in w
    assert 'echo "$rc" > /tmp/lc-exec-e1.rc' in w
    assert w.startswith("umask 000\necho $$ > /tmp/lc-exec-e1.pg\n")   # group/session id first
    assert w.index("rm -f /tmp/lc-exec-e1.pg") < w.index('echo "$rc" > /tmp/lc-exec-e1.rc')


def test_reaper_kills_group_session_and_tag_and_wakes_stopped_processes():
    r = otexec._reaper("e1")
    assert "pg=$(cat /tmp/lc-exec-e1.pg" in r
    assert '[ "$3" = "$pg" ] || [ "$4" = "$pg" ]' in r      # process group, session
    assert 'grep -lzx "LC_EXEC_ID=$i"' in r                  # the tag
    assert r.index("kill -STOP") < r.index("kill -TERM") < r.index("kill -KILL")
    assert "pp[x] in m" in r          # descendants of members are members (round 2)
    assert "rm -f /tmp/lc-exec-e1.rc /tmp/lc-exec-e1.pg" in r


# --- endings -------------------------------------------------------------------------------------

def test_a_fast_command_is_untouched_no_kill_no_reaper():
    ot = FakeOT(done_after=0, output="ok\r\n")
    out = _run(ot)
    assert (out.status, out.exit_code, out.stdout) == ("done", 0, "ok\r\n")
    assert ("DELETE", "/execute/p1") not in ot.seq()
    assert not any("LC_REAP" in b["command"] for b in ot.bodies)


def test_a_long_command_that_finishes_gets_its_full_output_not_the_tail():
    ot = FakeOT(done_after=3)
    out = _run(ot, timeout=5)
    assert out.status == "done" and out.exit_code == 0
    last = ot.calls[-2]   # the full fetch before the rc-file cleanup
    assert last[1] == "/execute/p1/status" and "tail" not in last[2]
    assert not ot.killed


def test_background_job_holding_the_pty_returns_with_the_commands_own_status():
    ot = FakeOT(rc_after=2, rc="0")
    t0 = time.monotonic()
    out = _run(ot, timeout=30)
    assert time.monotonic() - t0 < 5, "returned when the status file appeared, not at the timeout"
    assert (out.status, out.exit_code) == ("background", 0)
    assert not ot.killed, "a background job the command started on purpose is NOT killed"
    assert ("DELETE", "/files/delete") in ot.seq()


def test_status_file_just_before_the_pty_closes_is_a_plain_finish_not_background():
    # the wrapper writes the file and exits; open-terminal sees the PTY close a moment later
    ot = FakeOT(rc_after=1, done_after=2)
    out = _run(ot, timeout=30)
    assert (out.status, out.exit_code) == ("done", 0)


def test_background_returns_the_commands_nonzero_status():
    out = _run(FakeOT(rc_after=1, rc="3"), timeout=30)
    assert (out.status, out.exit_code) == ("background", 3)


def test_timeout_reaps_before_delete_and_both_before_the_report():
    ot = FakeOT(reap="LC_REAP killed=2 survivors=0 group=77")
    t0 = time.monotonic()
    out = _run(ot, timeout=0.3)
    assert time.monotonic() - t0 < 3
    assert (out.status, out.exit_code, out.timed_out) == ("timed_out", 124, True)
    assert (out.killed, out.survivors) == (2, 0)
    seq = ot.seq()
    i_out = max(i for i, c in enumerate(ot.calls) if c[1] == "/execute/p1/status" and "tail" not in c[2])
    i_kill = seq.index(("DELETE", "/execute/p1"))
    i_reap = max(i for i, c in enumerate(seq) if c == ("POST", "/execute"))
    # output taken, then the reaper kills (not waiting on DELETE, which hangs on a stopped leader),
    # then DELETE clears open-terminal's record - all before run_command returns
    assert i_out < i_reap < i_kill, seq
    assert out.stdout == "out\n"


def test_every_wait_is_bounded_by_the_time_left():
    ot = FakeOT()
    _run(ot, timeout=0.3)
    waits = [float(p["wait"]) for m, path, p in ot.calls if path == "/execute/p1/status" and "wait" in p]
    assert waits and max(waits) <= 0.3
    post = ot.calls[0][2]
    assert float(post["wait"]) <= 0.3


def test_timeout_message_names_the_command_the_bound_and_the_way_forward():
    ot = FakeOT(reap="LC_REAP killed=0 survivors=0 group=77")
    out = _run(ot, timeout=0.3, command="cd /w && python -m pytest -q tests")
    msg = otexec.timeout_message("cd /w && python -m pytest -q tests", 3, out)
    assert msg.startswith("[ot-exec] timed out after 3 s, killed: `cd /w && python -m pytest -q tests`\n")
    assert "Killed its process group and session, every process carrying its tag, and their descendants" in msg
    assert "cannot be tracked and may still run" in msg
    assert "every process it started" not in msg      # round 2, D3: no blanket claim
    assert "narrower, targeted command" in msg
    assert "nohup CMD > /tmp/x.log 2>&1 &" in msg and "tail -n 20 /tmp/x.log" in msg


def test_timeout_with_survivors_says_so():
    out = _run(FakeOT(reap="LC_REAP killed=3 survivors=1 group=77"))
    msg = otexec.timeout_message("x", 3, out)
    assert "WARNING: 1 of its processes could not be killed" in msg and "none of them" not in msg


def test_timeout_with_a_dead_reaper_does_not_claim_the_kill():
    out = _run(FakeOT(fail_reaper=True))
    assert out.status == "timed_out" and out.survivors is None
    msg = otexec.timeout_message("x", 3, out)
    assert "could not be confirmed" in msg and "Killed" not in msg


def test_unknown_group_narrows_the_claim():
    out = _run(FakeOT(reap="LC_REAP killed=0 survivors=0 group=none"))
    msg = otexec.timeout_message("x", 3, out)
    assert "its process group was not recorded" in msg and "group and session" not in msg


def test_the_kill_stays_within_its_budget_when_open_terminal_stalls():
    """D2: a DELETE that never returns (a stopped leader) or a slow reaper cannot stretch the call
    past bound + KILL_BUDGET_S (<= 15 s); every kill-path request carries a short timeout."""
    assert otexec.KILL_BUDGET_S <= 15
    ot = FakeOT()
    timeouts = []
    real = ot.handler

    def handler(req):
        timeouts.append((req.method, req.url.path, req.extensions.get("timeout", {}).get("read")))
        return real(req)
    ot.handler = handler
    _run(ot, timeout=0.3)
    kill_path = [t for m, p, t in timeouts if (m, p) in (("DELETE", "/execute/p1"),)]
    reap = [t for m, p, t in timeouts if (m, p) == ("POST", "/execute")][-1]
    assert kill_path and max(kill_path) <= 2 and reap <= otexec._REAP_WAIT_S + 1


def test_timeout_is_still_a_timeout_when_the_output_fetch_fails():
    ot = FakeOT()
    real = ot.handler

    def handler(req):
        if req.url.path == "/execute/p1/status" and "tail" not in dict(req.url.params) and \
                float(dict(req.url.params).get("wait", 0)) == 0:
            return httpx.Response(500, json={})
        return real(req)
    ot.handler = handler
    out = _run(ot)
    assert (out.status, out.exit_code) == ("timed_out", 124)
    assert ot.killed


def test_open_terminal_failing_mid_command_still_kills_it():
    ot = FakeOT(fail_on=("GET", "/files/read"))
    out = _run(ot, timeout=30)
    assert (out.status, out.exit_code) == ("unreachable", 125)
    assert ot.killed and any("LC_REAP" in b["command"] for b in ot.bodies)


def test_sigterm_mid_command_kills_it_in_open_terminal():
    ot = FakeOT(stop_on=("GET", "/files/read"))
    out = _run(ot, timeout=30)
    assert (out.status, out.exit_code) == ("stopped", 143)
    assert ot.killed and any("LC_REAP" in b["command"] for b in ot.bodies)


def test_a_failed_start_still_runs_the_reaper_by_exec_id():
    ot = FakeOT(fail_on=("POST", "/execute"))
    out = _run(ot)
    assert out.status == "unreachable"
    assert ("DELETE", "/execute/p1") not in ot.seq()   # no process id to signal
    assert sum(1 for m, p in ot.seq() if (m, p) == ("POST", "/execute")) == 2  # start + reaper try


# --- what reaches the model (ot-exec's stdout/stderr) --------------------------------------------

def _main(monkeypatch, capsys, ot: FakeOT, command: str, timeout="1"):
    monkeypatch.setenv("LC_EXEC_TIMEOUT", timeout)
    monkeypatch.setenv("LC_OPEN_TERMINAL_KEY", "sekrit-key-value")
    monkeypatch.delenv("LC_EVENT_STREAM", raising=False)
    monkeypatch.setattr(otexec, "SLICE_S", 0.05)
    real = otexec.OpenTerminalClient

    def client(**kw):
        return real(transport=httpx.MockTransport(ot.handler), **kw)
    monkeypatch.setattr(otexec, "OpenTerminalClient", client)
    rc = otexec.main(["-c", command])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_main_timeout_exit_124_output_then_message_without_secrets(monkeypatch, capsys):
    ot = FakeOT(output="collected 3 items\r\n")
    rc, out, err = _main(monkeypatch, capsys, ot,
                         "git push https://user:tok3n@github.com/x/y.git && sleep 1000")
    assert rc == 124
    assert out == "collected 3 items\r\n"
    assert err.startswith("[ot-exec] timed out after 1 s, killed: `git push https://github.com/x/y.git && sleep 1000`")
    assert "tok3n" not in err and "sekrit-key-value" not in err + out


def test_main_background_returns_status_and_the_note(monkeypatch, capsys):
    rc, out, err = _main(monkeypatch, capsys, FakeOT(rc_after=1, rc="0", output="started\r\n"),
                         "( sleep 1000 ) & echo started", timeout="30")
    assert rc == 0 and out == "started\r\n"
    assert "background process(es) it started are still running" in err


def test_main_plain_command_has_no_note(monkeypatch, capsys):
    rc, out, err = _main(monkeypatch, capsys, FakeOT(done_after=0, output="hi\r\n"), "echo hi")
    assert (rc, out, err) == (0, "hi\r\n", "")


def test_event_fields_keep_their_meaning(monkeypatch, tmp_path):
    """stderr_tail / git_proxy_denied read the stderr-tagged entries, exactly as before ao-exechang
    (on the PTY path open-terminal tags everything as one stream, so both stay empty there)."""
    ev = tmp_path / "ev.jsonl"
    monkeypatch.setenv("LC_EVENT_STREAM", str(ev))
    otexec._emit_event("git push", otexec.RunOutcome("pty text git-proxy: DENIED", "", 1, "done"))
    otexec._emit_event("git push", otexec.RunOutcome("", "git-proxy: DENIED push --force", 1, "done"))
    a, b = (json.loads(x) for x in ev.read_text(encoding="utf-8").splitlines())
    assert (a["git_proxy_denied"], a["stderr_tail"]) == (False, "")
    assert (b["git_proxy_denied"], b["stderr_tail"]) == (True, "git-proxy: DENIED push --force")


def test_event_stream_records_the_timeout(monkeypatch, capsys, tmp_path):
    ev = tmp_path / "ev.jsonl"
    monkeypatch.setenv("LC_EVENT_STREAM", str(ev))
    monkeypatch.setenv("LC_EXEC_TIMEOUT", "1")
    monkeypatch.setattr(otexec, "SLICE_S", 0.05)
    ot = FakeOT()
    real = otexec.OpenTerminalClient
    monkeypatch.setattr(otexec, "OpenTerminalClient",
                        lambda **kw: real(transport=httpx.MockTransport(ot.handler), **kw))
    assert otexec.main(["-c", "sleep 1000"]) == 124
    rec = json.loads(ev.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["timed_out"] is True and rec["exit_code"] == 124 and rec["status"] == "killed"


# --- output joining ------------------------------------------------------------------------------

def test_raw_chunks_are_concatenated_without_inventing_line_breaks():
    chunk = "a" * 4096
    out, _ = parse_exec_output([{"type": "output", "data": chunk}, {"type": "output", "data": "b\r\n"}])
    assert out == chunk + "b\r\n"


@pytest.mark.parametrize("entries,expected", [
    (["l1", "l2"], "l1\nl2"),
    ([{"type": "stdout", "data": "x\n"}, {"type": "stdout", "data": "y\n"}], "x\ny\n"),
])
def test_join_rules(entries, expected):
    assert parse_exec_output(entries)[0] == expected
