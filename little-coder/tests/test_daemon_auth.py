"""ao-dauth - the control daemon requires LC_DAEMON_TOKEN on every route except GET /health.

Found by the K14 tester (T-3): a worker's shell commands run in ao-ot-N, which can open a TCP
connection to its worker's daemon on :8090 - and the daemon answered everything, including
/tasks/{id}/confirm (journalled as an operator action), /admin/approve and /admin/shutdown.

These tests drive the REAL app (build_app over a real LittleCoderDaemon, lifespan not started)
through the real HTTP stack. The token is configured the way production configures it - the
LC_DAEMON_TOKEN environment variable - so the same tests run at the development tip (RED: every
route answers without a token) and on this branch (GREEN).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from littlecoder import daemon as daemon_mod
from littlecoder.config import AgentConfig, Config
from littlecoder.daemon import LittleCoderDaemon, build_app
from littlecoder.urlnorm import normalize_repo_url

# Fake tokens built from parts (never a real secret in a test file).
_TOKEN = "-".join(["lcd", "test", "token", "4f2a"])
_WRONG = "-".join(["lcd", "test", "token", "0000"])

# One request per protected route, shaped so that, ONCE PAST AUTH, the endpoint answers without
# side effects (unknown ids -> 404, empty bodies -> 422, observer off -> skeleton).
_PROTECTED = [
    ("POST", "/tasks", {}),
    ("GET", "/tasks", None),
    ("GET", "/tasks/01NOPE", None),
    ("GET", "/tasks/01NOPE/events", None),
    ("POST", "/tasks/01NOPE/confirm", {"outcome": "done", "actor": "ot"}),
    ("POST", "/tasks/01NOPE/cancel", None),
    ("GET", "/focus", None),
    ("POST", "/project", {}),
    ("POST", "/project/submodule", {}),
    ("POST", "/check", {}),
    ("GET", "/admin/observe", None),
    ("GET", "/admin/pending", None),
    ("POST", "/admin/approve/nope", None),
    ("POST", "/admin/reject/nope", None),
    ("POST", "/admin/bootstrap-agents", {"mode": "no-such-mode"}),
    ("POST", "/admin/upstream/pull", {}),
    ("POST", "/admin/shutdown", {}),
]


def _daemon(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    cfg = Config()
    cfg.agent = AgentConfig(command=[sys.executable, "-c", "pass"], model="m",
                            prompt_mode="arg", extra_args=[], use_session=False)
    cfg.journals.dir = str(tmp_path / "journals")
    cfg.workspace.path = str(ws)
    cfg.workspace.open_terminal_url = "http://127.0.0.1:1"
    d = LittleCoderDaemon(cfg)
    d.current_focus = normalize_repo_url("https://github.com/acme/widget")
    d.workspace = SimpleNamespace(is_focused=lambda: True)
    d._ensure_git_credentials = lambda: None
    return d


@pytest.fixture
def no_kill(monkeypatch):
    """/admin/shutdown SIGTERMs its own process - record instead."""
    killed: list = []
    monkeypatch.setattr(daemon_mod.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    return killed


def _client(tmp_path, monkeypatch, token: str | None):
    if token is None:
        monkeypatch.delenv("LC_DAEMON_TOKEN", raising=False)
    else:
        monkeypatch.setenv("LC_DAEMON_TOKEN", token)
    d = _daemon(tmp_path)
    return d, TestClient(build_app(d))   # no `with`: the lifespan (workers) never starts


def _send(c, method, path, body, headers=None):
    return c.request(method, path, json=body, headers=headers or {})


def _is_auth_refusal(r) -> bool:
    return r.status_code in (401, 403, 503) and r.headers.get("x-lc-auth") == "refused"


@pytest.mark.parametrize("method,path,body", _PROTECTED)
def test_route_refuses_without_token(tmp_path, monkeypatch, no_kill, method, path, body):
    _d, c = _client(tmp_path, monkeypatch, _TOKEN)
    r = _send(c, method, path, body)
    assert r.status_code == 401, (method, path, r.status_code, r.text[:200])
    assert _is_auth_refusal(r)
    assert no_kill == []


@pytest.mark.parametrize("method,path,body", _PROTECTED)
def test_route_refuses_wrong_token(tmp_path, monkeypatch, no_kill, method, path, body):
    _d, c = _client(tmp_path, monkeypatch, _TOKEN)
    for hdr in ({"Authorization": f"Bearer {_WRONG}"}, {"Authorization": _TOKEN},
                {"Authorization": "Basic " + _TOKEN}, {"X-Token": _TOKEN}):
        r = _send(c, method, path, body, hdr)
        assert r.status_code == 401, (method, path, hdr.keys(), r.status_code)
        assert _TOKEN not in r.text and _WRONG not in r.text
    assert no_kill == []


@pytest.mark.parametrize("method,path,body", _PROTECTED)
def test_route_works_with_token(tmp_path, monkeypatch, no_kill, method, path, body):
    _d, c = _client(tmp_path, monkeypatch, _TOKEN)
    r = _send(c, method, path, body, {"Authorization": f"Bearer {_TOKEN}"})
    assert not _is_auth_refusal(r), (method, path, r.status_code, r.text[:200])
    assert _TOKEN not in r.text


def test_shutdown_with_token_reaches_the_handler(tmp_path, monkeypatch, no_kill):
    _d, c = _client(tmp_path, monkeypatch, _TOKEN)
    assert c.post("/admin/shutdown", json={}).status_code == 401
    assert no_kill == []
    r = c.post("/admin/shutdown", json={}, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200 and len(no_kill) == 1


def test_confirm_without_token_writes_no_operator_audit(tmp_path, monkeypatch):
    """The T-3 case: a sandbox 'confirming' a task must not reach the operator audit journal."""
    d, c = _client(tmp_path, monkeypatch, _TOKEN)
    st = d.enqueue(daemon_mod.TriggerRequest(prompt="x", channel="cli", user_id="u"))
    st.status = daemon_mod.TaskStatus.DONE
    st.ended_ts = st.created_ts
    r = c.post(f"/tasks/{st.task_id}/confirm", json={"outcome": "fail", "actor": "operator"})
    assert r.status_code == 401
    assert st.outcome != "fail"
    r = c.post(f"/tasks/{st.task_id}/confirm", json={"outcome": "fail", "actor": "operator"},
               headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200 and st.outcome == "fail"


def test_health_is_public(tmp_path, monkeypatch):
    _d, c = _client(tmp_path, monkeypatch, _TOKEN)
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("method,path,body", _PROTECTED)
def test_unset_token_fails_closed(tmp_path, monkeypatch, no_kill, method, path, body):
    for unset in (None, "", "   "):
        _d, c = (_client(tmp_path / f"u{len(unset or '')}", monkeypatch, unset) if unset is not None
                 else _client(tmp_path / "none", monkeypatch, None))
        for hdr in ({}, {"Authorization": "Bearer "}, {"Authorization": f"Bearer {_TOKEN}"}):
            r = _send(c, method, path, body, hdr)
            assert r.status_code == 503, (method, path, repr(unset), r.status_code)
            assert _is_auth_refusal(r)
    assert no_kill == []


def test_unset_token_health_still_answers(tmp_path, monkeypatch):
    _d, c = _client(tmp_path, monkeypatch, None)
    assert c.get("/health").status_code == 200
