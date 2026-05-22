"""open-terminal client — output parsing and result flags (design §3.4)."""

from littlecoder.openterminal import ExecResult, parse_exec_output


def test_parse_plain_string_entries():
    out, err = parse_exec_output(["line one", "line two"])
    assert out == "line one\nline two"
    assert err == ""


def test_parse_stream_tagged_entries():
    entries = [
        {"stream": "stdout", "text": "building"},
        {"stream": "stderr", "text": "warning: unused"},
        {"type": "stderr", "line": "error: boom"},
    ]
    out, err = parse_exec_output(entries)
    assert out == "building"
    assert err == "warning: unused\nerror: boom"


def test_parse_handles_missing_and_odd_entries():
    out, err = parse_exec_output([{"data": "x"}, 42, None])
    assert "x" in out
    assert "42" in out


def test_parse_empty():
    assert parse_exec_output(None) == ("", "")
    assert parse_exec_output([]) == ("", "")


def test_exec_result_ok():
    r = ExecResult("pytest", 0, "ok", "", "done", "p1")
    assert r.ok
    assert not ExecResult("pytest", 1, "", "fail", "done", "p1").ok
    assert not ExecResult("pytest", None, "", "", "running", "p1").ok


def test_git_proxy_denial_detected():
    blocked = ExecResult(
        "git push --force",
        128,
        "",
        "git-proxy: DENIED (blocklist:push-force) — force-push is blocked",
        "done",
        "p1",
    )
    assert blocked.git_proxy_denied
    assert not ExecResult("ls", 0, "a\nb", "", "done", "p1").git_proxy_denied
