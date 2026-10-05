"""ef-worker-model (tracker H3): a worker turn runs on the model its profile names, and F3.

The worker side is the REAL `LittleCoderHarness`, talking HTTP to an in-process stub of the
little-coder daemon (httpx ASGITransport, no socket, no model, no GPU). Two stubs:
  - the NEW daemon: `model` is a field, checked against an allowlist (422 `model refused: ...`);
  - the OLD daemon: its trigger model has no `model` field and, like the real pre-H3 daemon's
    pydantic model, ignores the unknown key.
The bridge is the real Orchestrator/Router with the seed profiles (worker-default = local-small).
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Event
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker import harness as harness_mod
from app.worker.harness import LittleCoderHarness

ROOT = Path(__file__).resolve().parents[1]
W1 = "http://w1:8090"
REPO = "https://github.com/probeuser/widget.git"
PARENT = "https://github.com/acme/widget.git"


class _OldTrigger(BaseModel):     # the trigger model of a daemon older than ef-worker-model
    prompt: str
    channel: str = "cli"
    user_id: str = "cli"
    session_id: str | None = None
    plan_only: bool = False
    flail_guard: bool = False


class _NewTrigger(_OldTrigger):
    model: str | None = None


class StubDaemon:
    """Records every /tasks and /project body; answers /project with `project_reply`."""

    def __init__(self, *, knows_model: bool = True,
                 allowed: tuple[str, ...] = ("local-large", "local-small")) -> None:
        self.task_bodies: list[dict] = []
        self.project_bodies: list[dict] = []
        self.tasks: dict[str, dict] = {}
        self.ran: list[str] = []          # what each task ran (stub-internal truth)
        self.project_reply: dict = {"action": "clone", "focus": REPO}
        app = FastAPI()

        @app.post("/tasks")
        async def post_task(request: Request) -> dict:
            body = await request.json()
            self.task_bodies.append(body)
            req = (_NewTrigger if knows_model else _OldTrigger)(**body)
            if knows_model and req.model is not None and req.model not in allowed:
                raise HTTPException(422, f"model refused: model {req.model!r} is not allowed here")
            # Like the real daemon: the task view reports the pi id it ran (`llamacpp/<role>`),
            # never the request's key, so a test can tell a read-back from an echo (attempt 3).
            sent = getattr(req, "model", None)
            ran = f"llamacpp/{sent}" if sent else "llamacpp/agent-default"
            self.ran.append(ran)
            tid = f"t{len(self.tasks) + 1}"
            self.tasks[tid] = {"task_id": tid, "status": "done", "answer": "ok", "activity": []}
            if knows_model:   # the task view of a daemon older than ef-worker-model has no `model`
                self.tasks[tid]["model"] = ran
            return {"task_id": tid, "status": "queued"}

        @app.get("/tasks/{tid}")
        async def get_task(tid: str) -> dict:
            return self.tasks[tid]

        @app.post("/project")
        async def project(request: Request) -> dict:
            self.project_bodies.append(await request.json())
            return dict(self.project_reply)

        self.app = app


@pytest.fixture
def stub(monkeypatch) -> StubDaemon:
    d = StubDaemon()
    _route_harness_to(monkeypatch, d)
    return d


def _route_harness_to(monkeypatch, daemon: StubDaemon) -> None:
    real = httpx.AsyncClient
    transport = httpx.ASGITransport(app=daemon.app)

    def client(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr(harness_mod.httpx, "AsyncClient", client)


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=W1,
        max_concurrent_workers=1, database_url=db_url,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=LittleCoderHarness(poll_interval_s=0.0, poll_timeout_s=5.0))
    await orch.setup()
    return orch, orch.chat, db


async def _events(db, kind: str) -> list[dict]:
    async with db.session_factory() as s:
        rows = (await s.execute(select(Event).where(Event.kind == kind))).scalars().all()
    return [r.payload for r in rows]


async def _wake(orch, eid, chan, root, **kw):
    return await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                  session_id=eid, instruction="edit one file", **kw)


# ── part 2: the /tasks body carries the dispatching profile's model ─────────────────────────────

async def test_a_worker_wake_sends_the_worker_default_profile_model(db_url, stub):
    orch, chat, db = await _orch(db_url)
    try:
        assert orch.profiles.get("worker-default").model == "local-small"   # the seed (H2)
        eid, chan, root = await orch.router.open_effort("small-job")
        result = await _wake(orch, eid, chan, root)
        assert result.ok
        assert stub.task_bodies[-1]["model"] == "local-small"
        # A governed model change reaches the very next turn (no restart, no config write).
        await orch.profiles.set_model("worker-default", "local-large",
                                      registered={"local-large", "local-small"})
        await _wake(orch, eid, chan, root)
        assert stub.task_bodies[-1]["model"] == "local-large"
        done = await _events(db, "wake_done")
        assert [p["model_sent"] for p in done] == ["local-small", "local-large"]
        assert [p["model_ran"] for p in done] == ["llamacpp/local-small", "llamacpp/local-large"]
    finally:
        await db.dispose()


async def test_the_project_survey_sends_the_worker_default_profile_model(db_url, stub):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.router.survey_project(REPO)
        assert len(stub.task_bodies) == 1
        assert stub.task_bodies[0]["model"] == "local-small"
    finally:
        await db.dispose()


async def test_an_older_daemon_that_ignores_model_still_runs_the_turn(db_url, monkeypatch):
    old = StubDaemon(knows_model=False)
    _route_harness_to(monkeypatch, old)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("old-daemon")
        result = await _wake(orch, eid, chan, root)
        assert result.ok and result.status == "done"
        assert old.task_bodies[-1]["model"] == "local-small"      # sent, and ignored
        assert old.ran == ["llamacpp/agent-default"]              # the old daemon ran its own
    finally:
        await db.dispose()


async def test_a_model_the_daemon_refuses_is_reported_and_nothing_runs(db_url, monkeypatch):
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("refused")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        assert strict.tasks == {}                                  # no task was created
        assert any("refused model `local-small`" in p["message"] for p in chat.posted)
        (refusal,) = await _events(db, "worker_model_refused")
        assert refusal["model"] == "local-small" and refusal["role"] == "worker-default"
        # Not a worker fault: the worker was not quarantined.
        assert await _events(db, "worker_dispatch_failed") == []
    finally:
        await db.dispose()


async def test_a_cloud_lane_worker_profile_sends_no_model(db_url, stub):
    """Out of scope (cloud-lane workers): the bridge sends no model, so little-coder runs its own
    agent.model exactly as before H3, and the audit says why."""
    orch, chat, db = await _orch(db_url)
    try:
        await orch.profiles.set_lane("worker-default", "cloud")
        eid, chan, root = await orch.router.open_effort("cloudy")
        await _wake(orch, eid, chan, root)
        assert "model" not in stub.task_bodies[-1]
        (done,) = await _events(db, "wake_done")
        assert done["model_sent"] is None and "cloud lane" in done["model_from"]
        assert done["model_ran"] == "llamacpp/agent-default"   # the stub's report of its default
    finally:
        await db.dispose()


def test_an_unset_model_is_not_sent(stub):
    import asyncio

    asyncio.run(LittleCoderHarness(poll_interval_s=0.0).wake(W1, "s", "p"))
    assert "model" not in stub.task_bodies[-1]


def test_every_worker_dispatch_call_site_resolves_the_worker_default_profile():
    """The call sites: every `self.router.wake(...)` in orchestrator.py names its profile as a
    literal `role=` and it is `worker-default`; Router.wake turns `role` into the profile's model.
    The other harness.wake caller, Router.survey_project, asks for worker-default by name."""
    tree = ast.parse((ROOT / "app" / "orchestrator.py").read_text(encoding="utf-8"))
    roles = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "wake"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "router"):
            kw = {k.arg: k.value for k in node.keywords}
            assert "role" in kw, ast.unparse(node)[:120]
            roles.append(ast.literal_eval(kw["role"]))
    assert roles and set(roles) == {"worker-default"}, roles
    router_src = (ROOT / "app" / "modules" / "router.py").read_text(encoding="utf-8")
    assert router_src.count("self.harness.wake(") == 2
    assert 'self.worker_turn_model("worker-default")' in router_src
    assert "self.worker_turn_model(role)" in router_src


# ── part 4 (F3): the /project upstream keys are read correctly ─────────────────────────────────

@pytest.mark.parametrize("reply, expected", [
    # a NOOP whose remote was already there and nothing to re-store (no upstream_token, or an
    # older daemon): usable - this was the spurious warning
    ({"action": "noop", "focus": REPO}, (True, "", True)),
    ({"action": "noop", "focus": REPO, "upstream": PARENT, "upstream_reauthed": True},
     (True, "", True)),
    ({"action": "noop", "focus": REPO, "upstream": PARENT, "upstream_reauthed": True,
      "upstream_mismatch": True}, (True, "upstream_mismatch", True)),
    # real failures
    ({"action": "noop", "focus": REPO, "upstream": PARENT, "upstream_reauthed": False},
     (True, "", False)),
    ({"action": "noop", "focus": REPO, "upstream": PARENT, "upstream_ok": False}, (True, "", False)),
    ({"action": "clone", "focus": REPO, "upstream": PARENT, "upstream_ok": False}, (True, "", False)),
    ({"action": "clone", "focus": REPO}, (True, "", False)),
    # good bakes
    ({"action": "noop", "focus": REPO, "upstream": PARENT, "upstream_ok": True}, (True, "", True)),
    ({"action": "clone", "focus": REPO, "upstream": PARENT, "upstream_ok": True}, (True, "", True)),
])
def test_set_project_reads_the_upstream_keys(stub, reply, expected):
    import asyncio

    stub.project_reply = reply
    got = asyncio.run(LittleCoderHarness().set_project(W1, REPO, upstream=PARENT,
                                                        upstream_token="t"))
    assert got == expected


async def _fork_wake(orch, eid, chan, root):
    return await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                  session_id=eid, instruction="sync the fork", repo=REPO,
                                  upstream=PARENT, upstream_token="t")


def _bake_warnings(chat) -> list[str]:
    return [p["message"] for p in chat.posted if "couldn't set up its `upstream` remote" in p["message"]]


async def test_a_noop_reuse_of_a_fork_with_upstream_present_does_not_warn(db_url, stub):
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("fork-reuse")
        stub.project_reply = {"action": "noop", "focus": REPO}              # remote already there
        await _fork_wake(orch, eid, chan, root)
        stub.project_reply = {"action": "noop", "focus": REPO, "upstream": PARENT,
                              "upstream_reauthed": True}                     # re-stored
        await _fork_wake(orch, eid, chan, root)
        assert _bake_warnings(chat) == []
    finally:
        await db.dispose()


async def test_a_real_bake_failure_still_warns(db_url, stub):
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("fork-broken")
        stub.project_reply = {"action": "clone", "focus": REPO, "upstream": PARENT,
                              "upstream_ok": False}
        await _fork_wake(orch, eid, chan, root)
        stub.project_reply = {"action": "noop", "focus": REPO, "upstream": PARENT,
                              "upstream_reauthed": False}
        await _fork_wake(orch, eid, chan, root)
        assert len(_bake_warnings(chat)) == 2
    finally:
        await db.dispose()


async def test_a_mismatch_is_surfaced_once(db_url, stub):
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("fork-mismatch")
        stub.project_reply = {"action": "noop", "focus": REPO, "upstream": PARENT,
                              "upstream_reauthed": True, "upstream_mismatch": True}
        await _fork_wake(orch, eid, chan, root)
        await _fork_wake(orch, eid, chan, root)
        told = [p["message"] for p in chat.posted if "points somewhere other than" in p["message"]]
        assert len(told) == 1 and PARENT in told[0]
        assert _bake_warnings(chat) == []
        # every occurrence is still in the audit record
        sets = [p for p in await _events(db, "worker_project_set") if p.get("upstream")]
        assert [p["detail"] for p in sets] == ["upstream_mismatch", "upstream_mismatch"]
    finally:
        await db.dispose()


# ── part 3 (F2): AO_WORKER_MODEL / AO_JUDGE_MODEL are gone, not silently unread ────────────────

def test_the_dead_worker_and_judge_model_settings_are_removed(monkeypatch):
    """H2's F2: Settings read AO_WORKER_MODEL / AO_JUDGE_MODEL (env prefix AO_) but no running code
    used them; the profile is the one source of a role's model. Removed: setting the old env vars
    creates no attribute (extra="ignore"), the compose file no longer sets them, and the offline
    capability-floor eval defaults to the local large-tier role instead."""
    from app.config import Settings as S
    from app.evals import capability_floor
    from app.modules.profiles import tier_model

    monkeypatch.setenv("AO_WORKER_MODEL", "local-small")
    monkeypatch.setenv("AO_JUDGE_MODEL", "local-small")
    s = S(_env_file=None)
    assert "worker_model" not in S.model_fields and "judge_model" not in S.model_fields
    assert not hasattr(s, "worker_model") and not hasattr(s, "judge_model")
    compose = (ROOT.parent / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "AO_WORKER_MODEL:" not in compose and "AO_JUDGE_MODEL:" not in compose
    src = Path(capability_floor.__file__).read_text(encoding="utf-8")
    assert 'tier_model(s, "large", "local")' in src and "args.model or s.worker_model" not in src
    assert tier_model(s, "large", "local") == "local-large"


# ── attempt 2: TF1 (what RAN), TF2 (refusal awaits the operator), TF3 (survey refusal audited) ──

async def test_wake_done_records_the_model_that_ran_beside_the_model_sent(db_url, stub):
    """TF1: the audit's `model_ran` is the daemon's own report on GET /tasks/<id>."""
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("ran")
        result = await _wake(orch, eid, chan, root)
        assert result.model == "llamacpp/local-small"   # the daemon's report, not the sent key
        (done,) = await _events(db, "wake_done")
        assert done["model_sent"] == "local-small" and done["model_ran"] == "llamacpp/local-small"
    finally:
        await db.dispose()


async def test_an_old_daemon_turn_records_the_model_that_ran_as_unknown(db_url, monkeypatch):
    """TF1: an older daemon reports no model: `model_ran` is None (unknown), never the sent value."""
    old = StubDaemon(knows_model=False)
    _route_harness_to(monkeypatch, old)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("old-ran")
        result = await _wake(orch, eid, chan, root)
        assert "model" not in old.tasks["t1"]                     # the old task view
        assert result.ok and result.model is None
        (done,) = await _events(db, "wake_done")
        assert done["model_sent"] == "local-small" and done["model_ran"] is None
    finally:
        await db.dispose()


async def test_a_model_refusal_is_not_re_run_by_the_stall_watchdog(db_url, monkeypatch):
    """TF2: a refused model needs a human config change. The stall watchdog must treat the effort
    as awaiting the operator: no re-engage, no generic 'I have NOT identified the cause'
    escalation, and the refusal post stays the one message."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("refused-stall")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        async with db.session_factory() as s:            # everything happened 2 h ago
            await s.execute(update(Event).where(Event.effort_id == eid).values(ts=old))
            await s.commit()
        for _ in range(3):                                 # three sweeps: past the recovery cap
            await orch._sweep_stalled_efforts()
        assert await orch._event_count(eid, "stall_recovered") == 0
        assert await orch._event_count(eid, "stall_escalated") == 0
        assert len(strict.task_bodies) == 1                # never re-dispatched
        refusals = [p for p in chat.posted if "refused model" in p["message"]]
        assert len(refusals) == 1
        assert not any("NOT identified the cause" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_a_turn_that_runs_after_a_refusal_is_watched_again(db_url, monkeypatch):
    """TF2 boundary: once the operator fixes the config and a turn runs (wake_done after the
    refusal), the effort is no longer awaiting the operator: a later silence is a stall again."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("refused-then-fixed")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        assert await orch._model_refusal_unresolved(eid)
        await orch.profiles.set_model("worker-default", "local-large",
                                      registered={"local-large", "local-small"})
        assert (await _wake(orch, eid, chan, root)).ok
        assert not await orch._model_refusal_unresolved(eid)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        async with db.session_factory() as s:
            await s.execute(update(Event).where(Event.effort_id == eid).values(ts=old))
            await s.commit()
        await orch._sweep_stalled_efforts()
        assert await orch._event_count(eid, "stall_recovered") == 1
    finally:
        await db.dispose()


async def test_a_refused_project_survey_is_audited(db_url, monkeypatch):
    """TF3: the survey degrades to "" as before, but the refusal is in the audit record and the
    project_survey audit says which model was sent."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        assert await orch.router.survey_project(REPO) == ""
        (refusal,) = await _events(db, "worker_model_refused")
        assert refusal["model"] == "local-small" and refusal["survey"] is True
        (survey,) = await _events(db, "project_survey")
        assert survey["model_sent"] == "local-small" and survey["ok"] is False
    finally:
        await db.dispose()


async def test_a_good_project_survey_audits_the_model(db_url, stub):
    orch, chat, db = await _orch(db_url)
    try:
        await orch.router.survey_project(REPO)
        (survey,) = await _events(db, "project_survey")
        assert survey["model_sent"] == "local-small"
        assert survey["model_ran"] == "llamacpp/local-small"     # read back, not copied from sent
    finally:
        await db.dispose()


# ── attempt 3: D1 (a refusal never pins an effort that was re-run), D2 (one actionable message),
#    and the tester's pins. Daemon doubles adapted from the attempt-2 tester's probe_tf2.py. ─────

class _Down:
    """A daemon whose /tasks answers a NON-model error (default 500)."""

    def __init__(self, code: int = 500, detail: str = "boom") -> None:
        app = FastAPI()
        self.posts = 0

        @app.post("/tasks")
        async def post_task() -> dict:
            self.posts += 1
            raise HTTPException(code, detail)

        @app.post("/project")
        async def project() -> dict:
            return {"action": "clone", "focus": REPO}

        self.app = app


class _Hang:
    """A daemon that accepts the task and reports it running forever; optional repeating activity."""

    def __init__(self, *, activity: list | None = None, model="llamacpp/local-large") -> None:
        app = FastAPI()
        self.posts = 0

        @app.post("/tasks")
        async def post_task() -> dict:
            self.posts += 1
            return {"task_id": "h1", "status": "queued"}

        @app.get("/tasks/{tid}")
        async def get_task(tid: str) -> dict:
            v = {"task_id": tid, "status": "running", "activity": activity or []}
            if model is not None:
                v["model"] = model
            return v

        @app.post("/tasks/{tid}/cancel")
        async def cancel(tid: str) -> dict:
            return {"ok": True}

        self.app = app


class _CloneFails:
    """A daemon whose /project refuses the clone (the router's clone_failed early return)."""

    def __init__(self) -> None:
        app = FastAPI()
        self.tasks = 0

        @app.post("/project")
        async def project() -> dict:
            raise HTTPException(502, "clone failed (exit 128): fatal: repository not found")

        @app.post("/tasks")
        async def post_task() -> dict:
            self.tasks += 1
            return {"task_id": "never", "status": "queued"}

        self.app = app


class _Switch:
    """One ASGI app forwarding to whichever daemon is current (swap daemons mid-test)."""

    def __init__(self, d) -> None:
        self.cur = d

        async def app(scope, receive, send):
            await self.cur.app(scope, receive, send)
        self.app = app


async def _backdate(db, eid, hours=2):
    """Shift every event of the effort back by `hours`, preserving their order."""
    from datetime import datetime, timedelta

    from sqlalchemy import update
    async with db.session_factory() as s:
        rows = (await s.execute(select(Event.id, Event.ts).where(Event.effort_id == eid))).all()
        for rid, ts in rows:
            new = (datetime.fromisoformat(ts) - timedelta(hours=hours)).isoformat()
            await s.execute(update(Event).where(Event.id == rid).values(ts=new))
        await s.commit()


async def _flatten(db, eid, hours=2):
    """Give every event of the effort ONE identical timestamp (the equal-clock boundary)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with db.session_factory() as s:
        await s.execute(update(Event).where(Event.effort_id == eid).values(ts=old))
        await s.commit()


async def _refuse_then_fix(orch, monkeypatch, name):
    """A dispatch refused for its model, then the operator's fix (profile -> an allowed model)."""
    sw = _Switch(StubDaemon(allowed=("local-large",)))
    _route_harness_to(monkeypatch, sw)
    eid, chan, root = await orch.router.open_effort(name)
    with pytest.raises(httpx.HTTPStatusError):
        await _wake(orch, eid, chan, root)
    assert await orch._model_refusal_unresolved(eid)
    await orch.profiles.set_model("worker-default", "local-large",
                                  registered={"local-large", "local-small"})
    return sw, eid, chan, root


async def _swept_recoveries(orch, db, eid) -> int:
    await _backdate(db, eid)
    orch._delegating.discard(eid)
    await orch._sweep_stalled_efforts()
    return (await orch._event_count(eid, "stall_recovered")
            + await orch._event_count(eid, "stall_escalated"))


async def test_d1_a_re_run_that_gets_a_non_model_500_is_watched_again(db_url, monkeypatch):
    orch, chat, db = await _orch(db_url)
    try:
        sw, eid, chan, root = await _refuse_then_fix(orch, monkeypatch, "d1-500")
        down = sw.cur = _Down(500)
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        assert down.posts == 1                               # the re-run WAS dispatched
        assert not await orch._model_refusal_unresolved(eid)
        assert await _swept_recoveries(orch, db, eid) == 1
    finally:
        await db.dispose()


async def test_d1_a_re_run_lost_mid_turn_is_watched_again(db_url, monkeypatch):
    """A bridge restart / dead coroutine mid-turn: the dispatch started, no wake_done ever lands."""
    import asyncio

    orch, chat, db = await _orch(db_url)
    try:
        sw, eid, chan, root = await _refuse_then_fix(orch, monkeypatch, "d1-lost")
        hang = sw.cur = _Hang()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_wake(orch, eid, chan, root), timeout=0.3)
        assert hang.posts == 1 and await _events(db, "wake_done") == []
        assert not await orch._model_refusal_unresolved(eid)
        assert await _swept_recoveries(orch, db, eid) == 1
    finally:
        await db.dispose()


async def test_d1_a_re_run_whose_clone_fails_is_watched_again(db_url, monkeypatch):
    orch, chat, db = await _orch(db_url)
    try:
        sw, eid, chan, root = await _refuse_then_fix(orch, monkeypatch, "d1-clone")
        cf = sw.cur = _CloneFails()
        r = await orch.router.wake(eid, role="worker-default", thread_id=root, channel_id=chan,
                                   session_id=eid, instruction="x", repo=REPO)
        assert r.status == "clone_failed" and cf.tasks == 0   # the early return: no /tasks at all
        assert not await orch._model_refusal_unresolved(eid)
        assert await _swept_recoveries(orch, db, eid) == 1
    finally:
        await db.dispose()


async def test_d1_a_frozen_effort_after_a_refusal_still_gets_its_freeze_recovery(db_url, monkeypatch):
    """The guard sits after the frozen branch: a freeze's own auto-recovery is never pre-empted."""
    from sqlalchemy import update

    from app.models import Effort

    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("d1-frozen")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        async with db.session_factory() as s:
            await s.execute(update(Effort).where(Effort.id == eid).values(state="frozen"))
            await s.commit()
        seen = []

        async def _record(e, mgmt):
            seen.append(e)
        monkeypatch.setattr(orch, "_maybe_auto_recover_infra_freeze", _record)
        await _backdate(db, eid)
        await orch._sweep_stalled_efforts()
        assert seen == [eid]
    finally:
        await db.dispose()


async def test_d1_repeated_refusals_with_no_later_dispatch_stay_with_the_operator(db_url, monkeypatch):
    """TF2 kept: every dispatch refused, nothing else started - never re-run, never escalated."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("d1-multi")
        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await _wake(orch, eid, chan, root)
        await _backdate(db, eid)
        for _ in range(3):
            await orch._sweep_stalled_efforts()
        assert await orch._event_count(eid, "stall_recovered") == 0
        assert await orch._event_count(eid, "stall_escalated") == 0
        assert len(strict.task_bodies) == 3
    finally:
        await db.dispose()


async def test_d1_equal_timestamps_are_ordered_by_write_order(db_url, monkeypatch):
    """Boundary, decided: order is the append-only event id, so an identical clock reading cannot
    flip the answer either way. Refusal last -> unresolved; a later dispatch -> resolved."""
    strict = StubDaemon(allowed=("local-large",))
    sw = _Switch(strict)
    _route_harness_to(monkeypatch, sw)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("d1-eq")
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        await _flatten(db, eid)
        assert await orch._model_refusal_unresolved(eid)          # refused, nothing after
        await orch.profiles.set_model("worker-default", "local-large",
                                      registered={"local-large", "local-small"})
        sw.cur = _Down(500)
        with pytest.raises(httpx.HTTPStatusError):
            await _wake(orch, eid, chan, root)
        await _flatten(db, eid)
        assert not await orch._model_refusal_unresolved(eid)      # a later dispatch started
    finally:
        await db.dispose()


async def test_d2_the_delegate_path_gives_one_actionable_message(db_url, monkeypatch):
    """D2: the effort thread gets the router's actionable refusal only (no generic HTTP error
    post), and the operator's conversation gets the same advice, not a raw 422."""
    strict = StubDaemon(allowed=("local-large",))
    _route_harness_to(monkeypatch, strict)
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("d2")
        await orch.delegate(eid, chan, root, "edit one file")
        msgs = [p["message"] for p in chat.posted]
        assert not any("delegation error" in m or "Client error" in m for m in msgs), msgs
        in_thread = [p["message"] for p in chat.posted
                     if p.get("thread_id") == root and "refused model" in p["message"]]
        assert len(in_thread) == 1
        up = [m for m in msgs if "couldn't run" in m]
        assert len(up) == 1
        assert "refused model `local-small`" in up[0] and "set profile worker-default model" in up[0]
        assert "agent.allowed_models" in up[0]
    finally:
        await db.dispose()


def test_d2_a_raw_model_refusal_reads_as_actionable():
    req = httpx.Request("POST", "http://w1:8090/tasks")
    resp = httpx.Response(422, json={"detail": "model refused: model 'x' is not allowed here"},
                          request=req)
    exc = httpx.HTTPStatusError("422", request=req, response=resp)
    text = Orchestrator._friendly_dispatch_error(exc)
    assert text.startswith("the worker refused the task's model: model refused")
    assert "agent.allowed_models" in text and "set profile" in text
    other = httpx.HTTPStatusError("422", request=req, response=httpx.Response(
        422, json={"detail": "empty prompt"}, request=req))
    assert Orchestrator._friendly_dispatch_error(other).startswith("delegation error")


# ── the attempt-2 tester's pins ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("view_model, expect", [
    (None, None), ("", None), (5, None), (["x"], None), ({"a": 1}, None),
    ("llamacpp/local-large", "llamacpp/local-large"),
])
async def test_pin_model_ran_is_only_a_non_empty_string_from_the_daemon_view(
        db_url, monkeypatch, view_model, expect):
    app = FastAPI()

    @app.post("/tasks")
    async def post_task() -> dict:
        return {"task_id": "x1", "status": "queued"}

    @app.get("/tasks/{tid}")
    async def get_task(tid: str) -> dict:
        v = {"task_id": tid, "status": "done", "answer": "ok", "activity": []}
        if view_model is not None:
            v["model"] = view_model
        return v

    _route_harness_to(monkeypatch, type("D", (), {"app": app})())
    orch, chat, db = await _orch(db_url)
    try:
        eid, chan, root = await orch.router.open_effort("pin-ran")
        r = await _wake(orch, eid, chan, root)
        (done,) = await _events(db, "wake_done")
        assert done["model_sent"] == "local-small"
        assert r.model == expect and done["model_ran"] == expect
    finally:
        await db.dispose()


async def test_pin_a_poll_timeout_keeps_the_model_the_daemon_reported(db_url, monkeypatch):
    _route_harness_to(monkeypatch, _Hang(model="llamacpp/local-small"))
    orch, chat, db = await _orch(db_url)
    orch.harness.poll_timeout, orch.harness.poll_interval = 0.05, 0.01
    try:
        eid, chan, root = await orch.router.open_effort("pin-timeout")
        r = await _wake(orch, eid, chan, root)
        assert r.status == "error" and r.model == "llamacpp/local-small"
        (done,) = await _events(db, "wake_done")
        assert done["model_ran"] == "llamacpp/local-small"
    finally:
        await db.dispose()


def test_pin_the_flail_path_keeps_the_model_the_daemon_reported(monkeypatch):
    import asyncio

    act = [{"command": "cat a", "ok": True}] * 4
    _route_harness_to(monkeypatch, _Hang(activity=act, model="llamacpp/local-small"))
    r = asyncio.run(LittleCoderHarness(poll_interval_s=0.0, poll_timeout_s=5.0).wake(
        W1, "s", "p", max_repeat=3, model="local-small"))
    assert r.status == "flail" and r.model == "llamacpp/local-small"


@pytest.mark.parametrize("code, detail", [(422, "empty prompt"), (500, "boom"),
                                          (422, "model is fine")])
async def test_pin_a_non_model_survey_error_is_not_a_refusal(db_url, monkeypatch, code, detail):
    _route_harness_to(monkeypatch, _Down(code, detail))
    orch, chat, db = await _orch(db_url)
    try:
        assert await orch.router.survey_project(REPO) == ""
        assert await _events(db, "worker_model_refused") == []
    finally:
        await db.dispose()
