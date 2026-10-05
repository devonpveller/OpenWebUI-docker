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
            ran = getattr(req, "model", None) or "agent.model"
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
        assert [p["model_ran"] for p in done] == ["local-small", "local-large"]
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
        assert old.ran == ["agent.model"]                         # the old daemon ran its own
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
        assert done["model_ran"] == "agent.model"       # the stub's report of its own default
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
        assert result.model == "local-small"          # the stub reports what it ran
        (done,) = await _events(db, "wake_done")
        assert done["model_sent"] == "local-small" and done["model_ran"] == "local-small"
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
        assert survey["model_sent"] == "local-small" and survey["model_ran"] == "local-small"
    finally:
        await db.dispose()
