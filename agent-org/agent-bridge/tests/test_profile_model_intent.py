"""A role profile's model moves only through the governed operator intent (model-roles, mr-consumers;
operator decision 2026-09-30: every operator inlet is NL -> OperatorIntent -> a governed handler).

The DB owns a profile's live values; the shipped profiles/*.json only seed a MISSING profile. So an
existing install moves a profile from `qwen36-27b` to `local-large` by saying so, and the handler
writes one audited version with only `model` changed. Fakes only (FakeModelClient answers the
gateway's model list)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Event, Profile
from app.modules.model_router import FakeModelClient
from app.orchestrator import _PROFILE_MODEL_RE, Orchestrator
from app.schemas import OperatorIntent
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]


def _profiles_on(tmp_path: Path, model: str) -> Path:
    """The shipped profiles, as an install seeded BEFORE the move had them (model = `model`)."""
    d = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", d)
    for f in d.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        data["model"] = model
        f.write_text(json.dumps(data), encoding="utf-8")
    return d


async def _orch(db_url, profiles_dir: Path):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(profiles_dir), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _rows(db, name: str) -> list[Profile]:
    async with db.session_factory() as s:
        return list((await s.execute(
            select(Profile).where(Profile.name == name).order_by(Profile.version))).scalars().all())


async def _events(db, kind: str) -> list[Event]:
    async with db.session_factory() as s:
        return list((await s.execute(select(Event).where(Event.kind == kind))).scalars().all())


def _fields(p: Profile) -> tuple:
    return (p.lane, p.system_prompt_ref, p.temperature, list(p.tool_access or []), p.caller_key)


def test_grammar():
    ok = {
        "set profile pm model local-large": ("pm", "local-large", False),
        "Set the profile reviewer-ethics model to local-large.": ("reviewer-ethics", "local-large", False),
        "set profile po model qwen36-27b (dry run)": ("po", "qwen36-27b", True),
        "set profile planner model local-large:nothink --dry-run": ("planner", "local-large:nothink", True),
    }
    for msg, want in ok.items():
        m = _PROFILE_MODEL_RE.match(msg)
        assert m, msg
        assert (m.group("name"), m.group("model"), bool(m.group("dry"))) == want, msg
    for msg in ("set profile pm model", "please set profile pm model local-large and restart",
                "set profile pm lane cloud", "don't set profile pm model local-large"):
        assert not _PROFILE_MODEL_RE.match(msg), msg


async def test_nl_moves_one_profile_one_version_audited_nothing_else(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        await orch.profiles.set_lane("pm", "cloud")           # an operator flip, persisted as v2
        before = await _rows(db, "pm")
        others = {n: [(r.version, r.model) for r in await _rows(db, n)]
                  for n in orch.profiles.all() if n != "pm"}
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("set profile pm model local-large", mgmt, thread_id="t1",
                             actor="operator-api:landing-mr-consumers")
        rows = await _rows(db, "pm")
        assert len(rows) == len(before) + 1                   # exactly one new version
        new, old = rows[-1], before[-1]
        assert (new.version, new.active, new.model) == (3, True, "local-large")
        assert _fields(new) == _fields(old)                   # lane (cloud) and every other field
        assert not any(r.active for r in rows[:-1])
        assert orch.profiles.get("pm").model == "local-large"
        assert {n: [(r.version, r.model) for r in await _rows(db, n)]
                for n in orch.profiles.all() if n != "pm"} == others
        ev = await _events(db, "profile_model_set")
        assert len(ev) == 1 and ev[0].actor == "operator-api:landing-mr-consumers"
        assert ev[0].payload == {"profile": "pm", "before": "qwen36-27b", "after": "local-large",
                                 "version": 3, "lane": "cloud"}
        assert any("now asks for **`local-large`**" in p["message"] for p in chat.posted)
        # deterministic: no model call was needed to understand it
        assert not any(c["kind"] == "structured" for c in orch.models._client.calls)
    finally:
        await db.dispose()


async def test_same_model_twice_writes_nothing(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("set profile po model local-large", mgmt, thread_id="t1")
        await orch.nl_intake("set profile po model local-large", mgmt, thread_id="t1")
        assert [(r.version, r.model) for r in await _rows(db, "po")] == [
            (1, "qwen36-27b"), (2, "local-large")]
        assert len(await _events(db, "profile_model_set")) == 1
        assert any("already asks for `local-large`" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_dry_run_reports_and_writes_nothing(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("set profile planner model local-large (dry run)", mgmt, thread_id="t1")
        assert [(r.version, r.model) for r in await _rows(db, "planner")] == [(1, "qwen36-27b")]
        assert not await _events(db, "profile_model_set")
        assert any("Dry run" in p["message"] and "would move `qwen36-27b` → `local-large`"
                   in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_refuses_unknown_profile_unregistered_model_and_unreachable_gateway(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        snapshot = {n: [(r.version, r.model) for r in await _rows(db, n)] for n in orch.profiles.all()}
        await orch.nl_intake("set profile nobody model local-large", mgmt, thread_id="t1")
        await orch.nl_intake("set profile pm model local-huge", mgmt, thread_id="t1")
        orch.models._client.registered_models = ConnectionError("gateway down")
        await orch.nl_intake("set profile pm model local-large", mgmt, thread_id="t1")
        assert {n: [(r.version, r.model) for r in await _rows(db, n)]
                for n in orch.profiles.all()} == snapshot       # nothing written
        assert not await _events(db, "profile_model_set")
        refused = await _events(db, "profile_model_refused")
        assert [e.payload["profile"] for e in refused] == ["nobody", "pm", "pm"]
        msgs = [p["message"] for p in chat.posted]
        assert any("no profile called `nobody`" in m for m in msgs)
        assert any("`local-huge`" in m and "not registered" in m for m in msgs)
        assert any("cannot be verified" in m for m in msgs)
    finally:
        await db.dispose()


async def test_the_po_model_path_reaches_the_same_handler(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        orch.models._client.queue_structured(OperatorIntent(
            kind="profile_model", profile_name="reviewer-scope", profile_model="local-large",
            reply="Sure -"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("could you point the scope reviewer at local-large please", mgmt,
                             thread_id="t1", user_id="u-operator")
        assert orch.profiles.get("reviewer-scope").model == "local-large"
        ev = await _events(db, "profile_model_set")
        assert len(ev) == 1 and ev[0].actor == "u-operator"
    finally:
        await db.dispose()


async def test_rollback_through_the_same_intent_writes_a_new_version(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("set profile worker-default model local-large", mgmt, thread_id="t1")
        await orch.nl_intake("set profile worker-default model qwen36-27b", mgmt, thread_id="t1")
        rows = await _rows(db, "worker-default")
        assert [(r.version, r.active, r.model) for r in rows] == [
            (1, False, "qwen36-27b"), (2, False, "local-large"), (3, True, "qwen36-27b")]
        assert _fields(rows[2]) == _fields(rows[0])
        assert [e.payload["after"] for e in await _events(db, "profile_model_set")] == [
            "local-large", "qwen36-27b"]
    finally:
        await db.dispose()


async def test_seed_files_do_not_move_an_existing_install(db_url, tmp_path):
    """The DB owns the live value: restarting on the shipped (role) files leaves a row seeded on the
    old id alone - which is why the landing issues the intent."""
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    await db.dispose()
    orch2, _chat, db2 = await _orch(db_url, ROOT / "profiles")
    try:
        assert {p.model for p in orch2.profiles.all().values()} == {"qwen36-27b"}
    finally:
        await db2.dispose()


async def test_every_shipped_profile_seeds_a_role(db_url):
    orch, chat, db = await _orch(db_url, ROOT / "profiles")
    try:
        assert len(orch.profiles.all()) == 9
        assert {p.model for p in orch.profiles.all().values()} == {"local-large"}
    finally:
        await db.dispose()


async def test_the_landing_path_through_the_http_inlet(db_url, tmp_path):
    """What the landing runs: POST /nl with a named actor for each profile, dry run first, then
    GET /profiles to read the result back."""
    import httpx

    from app.main import create_app

    d = _profiles_on(tmp_path, "qwen36-27b")
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url, profiles_dir=str(d),
        charters_dir=str(ROOT / "charters"), floor_dir=str(ROOT / "floor"),
        worker_instance_urls="http://w1:8090")
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    app = create_app(orch)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            names = sorted((await c.get("/profiles")).json()["profiles"])
            assert len(names) == 9
            for n in names:
                r = await c.post("/nl", json={"message": f"set profile {n} model local-large (dry run)",
                                              "actor": "landing-mr-consumers"})
                assert r.status_code == 200
            assert {p["model"] for p in (await c.get("/profiles")).json()["profiles"].values()} == {"qwen36-27b"}
            for n in names:
                await c.post("/nl", json={"message": f"set profile {n} model local-large",
                                          "actor": "landing-mr-consumers"})
            got = (await c.get("/profiles")).json()["profiles"]
            assert {p["model"] for p in got.values()} == {"local-large"}
    ev = await _events(orch.db, "profile_model_set")
    assert len(ev) == 9 and {e.actor for e in ev} == {"operator-api:landing-mr-consumers"}
    await orch.db.dispose()
