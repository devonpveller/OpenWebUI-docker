"""ef-lane-500 (F16): POST /profiles/lane never answers 500 for a foreseeable case.

Unknown profile -> 404 naming it; a lost concurrent write (two lane flips, or a lane flip racing a
profile-model intent: the uq_profile_name_ver IntegrityError) -> 409 with nothing half-written
(one active row, cache == DB); both audited as profile_lane_refused; a lane outside local|cloud ->
422 from request validation. Disposable DB (conftest db_url), fakes only."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import Event, Profile
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness
from test_profile_model_intent import ROOT, _CommitBarrier


async def _app(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url,
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090")
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    return orch, create_app(orch)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _rows(orch, name):
    async with orch.db.session_factory() as s:
        return list((await s.execute(
            select(Profile).where(Profile.name == name).order_by(Profile.version))).scalars().all())


async def _refused(orch):
    async with orch.db.session_factory() as s:
        return list((await s.execute(
            select(Event).where(Event.kind == "profile_lane_refused"))).scalars().all())


def _assert_one_active_and_cache_matches(orch, rows, name):
    active = [r for r in rows if r.active]
    assert len(active) == 1 and active[0].version == rows[-1].version
    assert orch.profiles.get(name).lane == active[0].lane
    assert orch.profiles.get(name).model == active[0].model


async def test_unknown_profile_is_404_naming_it_and_audited(db_url):
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        async with _client(app) as c:
            r = await c.post("/profiles/lane", json={"name": "nobody", "lane": "cloud"})
            assert r.status_code == 404 and "nobody" in r.json()["detail"]
            ev = await _refused(orch)
            assert len(ev) == 1 and ev[0].payload["profile"] == "nobody"
            assert ev[0].payload["outcome"] == "refused"
    await orch.db.dispose()


async def test_lane_outside_local_cloud_is_422_and_writes_nothing(db_url):
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        async with _client(app) as c:
            r = await c.post("/profiles/lane", json={"name": "pm", "lane": "moon"})
            assert r.status_code == 422
            assert [x.version for x in await _rows(orch, "pm")] == [1]
    await orch.db.dispose()


async def test_a_good_flip_still_works(db_url):
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        async with _client(app) as c:
            r = await c.post("/profiles/lane", json={"name": "pm", "lane": "cloud"})
            assert r.status_code == 200 and r.json()["profile"]["lane"] == "cloud"
            rows = await _rows(orch, "pm")
            assert [(x.version, x.active, x.lane) for x in rows] == [(1, False, "local"), (2, True, "cloud")]
            assert not await _refused(orch)
    await orch.db.dispose()


async def test_two_concurrent_lane_flips_one_wins_one_409(db_url):
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        orch.profiles.db = _CommitBarrier(orch.profiles.db)
        async with _client(app) as c:
            rs = await asyncio.gather(
                c.post("/profiles/lane", json={"name": "pm", "lane": "cloud"}),
                c.post("/profiles/lane", json={"name": "pm", "lane": "local"}))
        assert sorted(r.status_code for r in rs) == [200, 409], [r.status_code for r in rs]
        loser = next(r for r in rs if r.status_code == 409)
        assert "pm" in loser.json()["detail"] and "retry" in loser.json()["detail"]
        rows = await _rows(orch, "pm")
        assert [x.version for x in rows] == [1, 2]
        _assert_one_active_and_cache_matches(orch, rows, "pm")
        ev = await _refused(orch)
        assert len(ev) == 1 and ev[0].payload["outcome"] == "conflict"
    await orch.db.dispose()


async def test_a_lane_flip_racing_set_model_one_wins_the_lane_side_never_500s(db_url):
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        orch.profiles.db = _CommitBarrier(orch.profiles.db)
        async with _client(app) as c:
            lane_call = c.post("/profiles/lane", json={"name": "pm", "lane": "cloud"})
            model_call = orch.profiles.set_model("pm", "local-small", registered={"local-small"})
            lane_res, model_res = await asyncio.gather(lane_call, model_call, return_exceptions=True)
        assert not isinstance(lane_res, BaseException)
        assert lane_res.status_code in (200, 409)
        rows = await _rows(orch, "pm")
        assert [x.version for x in rows] == [1, 2]
        _assert_one_active_and_cache_matches(orch, rows, "pm")
        if lane_res.status_code == 409:
            assert not isinstance(model_res, BaseException)   # the model write won
            assert rows[-1].model == "local-small" and rows[-1].lane == "local"
            ev = await _refused(orch)
            assert len(ev) == 1 and ev[0].payload["outcome"] == "conflict"
        else:
            assert isinstance(model_res, Exception)            # model side refused, lane won
            assert rows[-1].lane == "cloud"
    await orch.db.dispose()
