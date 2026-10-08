"""ao-dauth: agent-bridge sends LC_DAEMON_TOKEN to the workers' little-coder daemons.

The daemon (little-coder/src/littlecoder/daemon_auth.py) now refuses every route but GET /health
without `Authorization: Bearer <LC_DAEMON_TOKEN>`. These tests prove the bridge side:
  - Settings reads LC_DAEMON_TOKEN (no AO_ prefix - the same name the daemons read);
  - the production Orchestrator hands it to LittleCoderHarness;
  - every LittleCoderHarness call carries the bearer, against a fake daemon that enforces it the
    way the real one does - wake (task -> poll -> done) end to end, the progress probe
    (/tasks + /tasks/{id}/events), cancel, project focus, submodule, check, focus;
  - no token -> no header -> the daemon's refusal surfaces as a failure, never as success;
  - when little-coder's source is beside this checkout, the same calls against the REAL daemon
    app (in-process, no socket).
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import LittleCoderHarness

ROOT = Path(__file__).resolve().parents[1]
LC_SRC = ROOT.parents[1] / "little-coder" / "src"

# Fake token built from parts - authenticates only against the fakes below.
LC_TOKEN = "-".join(["fake", "lc", "daemon", "d4em0n", "9a8b7c"])
BASE = "http://w1:8090"


class FakeDaemon:
    """Enforces the bearer like daemon_auth (GET /health public, everything else 401 without it)
    and serves just enough of the API for every harness call."""

    def __init__(self, token: str | None = LC_TOKEN):
        self.token = token
        self.seen: list[tuple[str, str, str | None]] = []
        self.polls = 0
        self.cancelled: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization")
        self.seen.append((req.method, req.url.path, auth))
        path = req.url.path
        if not (req.method == "GET" and path == "/health"):
            if self.token is None:
                return httpx.Response(503, json={"detail": "not configured"}, headers={"x-lc-auth": "refused"})
            if auth != f"Bearer {self.token}":
                return httpx.Response(401, json={"detail": "daemon token required"},
                                      headers={"x-lc-auth": "refused"})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "focus": "https://github.com/a/b"})
        if req.method == "POST" and path == "/tasks":
            return httpx.Response(200, json={"task_id": "T1", "status": "queued"})
        if req.method == "GET" and path == "/tasks/T1":
            self.polls += 1
            if self.polls < 2:
                return httpx.Response(200, json={"status": "running", "activity": [{"command": "ls"}]})
            return httpx.Response(200, json={"status": "done", "answer": "ok",
                                             "activity": [{"command": "ls"}]})
        if req.method == "GET" and path == "/tasks":
            return httpx.Response(200, json={"tasks": [{"task_id": "T1", "status": "running"}]})
        if req.method == "GET" and path == "/tasks/T1/events":
            return httpx.Response(200, json={"events": [], "next_offset": 7})
        if req.method == "POST" and path == "/tasks/T1/cancel":
            self.cancelled.append("T1")
            return httpx.Response(200, json={"task_id": "T1", "status": "abandoned"})
        if req.method == "POST" and path in ("/project", "/project/submodule"):
            return httpx.Response(200, json={"focus": "x"})
        if req.method == "POST" and path == "/check":
            return httpx.Response(200, json={"exit_code": 0, "output": "fine", "timed_out": False})
        return httpx.Response(404, json={"detail": "no such route"})


def _harness(daemon: FakeDaemon, token: str = LC_TOKEN) -> LittleCoderHarness:
    return LittleCoderHarness(0.0, 5.0, daemon_token=token, transport=httpx.MockTransport(daemon))


def test_settings_read_lc_daemon_token(monkeypatch):
    monkeypatch.setenv("LC_DAEMON_TOKEN", LC_TOKEN)
    assert Settings(_env_file=None).lc_daemon_token.get_secret_value() == LC_TOKEN
    monkeypatch.delenv("LC_DAEMON_TOKEN")
    monkeypatch.setenv("AO_LC_DAEMON_TOKEN", LC_TOKEN)
    assert Settings(_env_file=None).lc_daemon_token.get_secret_value() == ""
    assert LC_TOKEN not in repr(Settings(_env_file=None, LC_DAEMON_TOKEN=LC_TOKEN))


async def test_production_orchestrator_gives_the_harness_the_token(db_url, monkeypatch):
    monkeypatch.setenv("LC_DAEMON_TOKEN", LC_TOKEN)
    s = Settings(_env_file=None, chat_adapter="mattermost", database_url=db_url,
                 profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
                 floor_dir=str(ROOT / "floor"), worker_instance_urls=BASE, egress_allowlist_file="")
    orch = Orchestrator(s, Database(db_url), FakeChatAdapter(), model_client=FakeModelClient())
    assert isinstance(orch.harness, LittleCoderHarness)
    assert orch.harness._headers == {"Authorization": f"Bearer {LC_TOKEN}"}


async def test_every_harness_call_sends_the_bearer():
    d = FakeDaemon()
    h = _harness(d)
    updates: list = []

    async def on_update(kind, payload):
        updates.append(kind)

    res = await h.wake(BASE, "s1", "do it", on_update=on_update)
    assert res.status == "done" and res.output == "ok"
    assert "command" in updates and "answer" in updates
    assert await h.has_running_task(BASE) is True
    assert await h.running_task_progress(BASE, 0, "T1") == ("T1", 7)
    assert await h.cancel_task(BASE, "T1") is True and d.cancelled == ["T1"]
    assert (await h.set_project(BASE, "https://github.com/a/b"))[0] is True
    assert (await h.add_submodule(BASE, "https://github.com/a/c", "c"))[0] is True
    assert await h.run_check(BASE, "true") == (0, "fine", False)
    assert await h.current_focus(BASE) == "https://github.com/a/b"
    paths = {(m, p) for m, p, _ in d.seen}
    for want in [("POST", "/tasks"), ("GET", "/tasks/T1"), ("GET", "/tasks"),
                 ("GET", "/tasks/T1/events"), ("POST", "/tasks/T1/cancel"), ("POST", "/project"),
                 ("POST", "/project/submodule"), ("POST", "/check"), ("GET", "/health")]:
        assert want in paths, want
    assert all(a == f"Bearer {LC_TOKEN}" for _m, _p, a in d.seen), d.seen


async def test_no_token_is_refused_and_reported_as_failure():
    d = FakeDaemon()
    h = _harness(d, token="  ")
    assert h._headers == {}
    with pytest.raises(httpx.HTTPStatusError):
        await h.wake(BASE, "s1", "do it")
    assert await h.has_running_task(BASE) is False
    assert await h.running_task_progress(BASE) is None
    assert await h.cancel_task(BASE, "T1") is False and d.cancelled == []
    ok, detail, _ = await h.set_project(BASE, "https://github.com/a/b")
    assert ok is False and "token" in detail
    assert (await h.add_submodule(BASE, "u", "p"))[0] is False
    with pytest.raises(httpx.HTTPStatusError):
        await h.run_check(BASE, "true")
    assert all(a is None for _m, _p, a in d.seen)


# --- against the REAL daemon app, in-process (needs little-coder's source beside this tree) ---

def _real_daemon_app(tmp_path, token):
    if not LC_SRC.is_dir():
        pytest.skip("little-coder source not beside this checkout")
    sys.path.insert(0, str(LC_SRC))
    try:
        from types import SimpleNamespace

        from littlecoder.config import AgentConfig, Config
        from littlecoder.daemon import LittleCoderDaemon, build_app
        from littlecoder.urlnorm import normalize_repo_url
    except ImportError as exc:  # its deps (e.g. prometheus_client) are not in this env
        pytest.skip(f"little-coder not importable here: {exc}")
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = Config()
    cfg.agent = AgentConfig(command=[sys.executable, "-c", "pass"], model="m", prompt_mode="arg",
                            extra_args=[], use_session=False)
    cfg.journals.dir = str(tmp_path / "journals")
    cfg.workspace.path = str(ws)
    cfg.workspace.open_terminal_url = "http://127.0.0.1:1"
    d = LittleCoderDaemon(cfg)
    d.current_focus = normalize_repo_url("https://github.com/acme/widget")
    d.workspace = SimpleNamespace(is_focused=lambda: True)
    return d, build_app(d, token=token)


async def test_against_the_real_daemon_app(tmp_path):
    d, app = _real_daemon_app(tmp_path, LC_TOKEN)
    asgi = httpx.ASGITransport(app=app)
    good = LittleCoderHarness(0.0, 5.0, daemon_token=LC_TOKEN, transport=asgi)
    bad = LittleCoderHarness(0.0, 5.0, daemon_token="", transport=asgi)
    wrong = LittleCoderHarness(0.0, 5.0, daemon_token=LC_TOKEN + "x", transport=asgi)
    # a queued task the daemon reports (no worker loop runs: lifespan not started)
    from littlecoder.daemon import TriggerRequest
    st = d.enqueue(TriggerRequest(prompt="x", channel="cli", user_id="u"))
    from littlecoder.tasks import TaskStatus
    st.status = TaskStatus.RUNNING
    assert await good.has_running_task(BASE) is True
    assert await good.running_task_progress(BASE, 0, st.task_id) == (st.task_id, 0)
    assert await good.current_focus(BASE) == "https://github.com/acme/widget"   # /health: public
    for h in (bad, wrong):
        assert await h.has_running_task(BASE) is False
        assert await h.running_task_progress(BASE) is None
        assert await h.cancel_task(BASE, st.task_id) is False
        assert st.status == TaskStatus.RUNNING
    assert await good.cancel_task(BASE, st.task_id) is True
    assert st.status == TaskStatus.ABANDONED
