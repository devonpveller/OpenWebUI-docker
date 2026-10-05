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
from app.modules.profiles import ConcurrentProfileChange, ProfileRegistry
from test_profile_model_intent import ROOT, _CommitBarrier


async def _app(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url,
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090")
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    return orch, create_app(orch)


class _HookDb:
    """Wraps a Database so the FIRST profile commit first awaits `hook()` (a competing write)."""

    def __init__(self, db, hook):
        self._db, self._hook, self._done = db, hook, False

    def __getattr__(self, k):
        return getattr(self._db, k)

    def session_factory(self):
        cm, outer = self._db.session_factory(), self

        class _Cm:
            async def __aenter__(inner):
                sess = await cm.__aenter__()
                real = sess.commit

                async def commit():
                    if not outer._done:
                        outer._done = True
                        await outer._hook()
                    return await real()

                sess.commit = commit
                return sess

            async def __aexit__(inner, *a):
                return await cm.__aexit__(*a)

        return _Cm()


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


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


async def test_a_lane_flip_losing_to_set_model_is_409_the_model_write_stands(db_url):
    """Deterministic: set_model commits while the lane flip is between its read and its commit."""
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        real_db = orch.profiles.db

        class _Db:
            def __getattr__(self, k):
                return getattr(real_db, k)

            def session_factory(self):
                cm = real_db.session_factory()

                class _Cm:
                    async def __aenter__(inner):
                        sess = await cm.__aenter__()
                        real = sess.commit

                        async def commit():
                            if not getattr(_Db, "done", False):
                                _Db.done = True           # the first commit is the lane flip's
                                orch.profiles.db = real_db
                                await orch.profiles.set_model(
                                    "pm", "local-small", registered={"local-small"})
                            return await real()

                        sess.commit = commit
                        return sess

                    async def __aexit__(inner, *a):
                        return await cm.__aexit__(*a)

                return _Cm()

        orch.profiles.db = _Db()
        async with _client(app) as c:
            r = await c.post("/profiles/lane", json={"name": "pm", "lane": "cloud"})
        assert r.status_code == 409, r.status_code
        rows = await _rows(orch, "pm")
        assert [(x.version, x.active, x.lane, x.model) for x in rows] == [
            (1, False, "local", rows[0].model), (2, True, "local", "local-small")]
        _assert_one_active_and_cache_matches(orch, rows, "pm")
        ev = await _refused(orch)
        assert len(ev) == 1 and ev[0].payload["outcome"] == "conflict"
    await orch.db.dispose()


async def test_set_model_losing_to_a_lane_flip_is_still_a_clean_conflict(db_url):
    """Deterministic (same at base and tip): a lane flip commits while set_model is between its
    read and its commit, so set_model - not the lane side - is the loser."""
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        real_db = orch.profiles.db
        other = ProfileRegistry(real_db, str(orch.profiles.dir))
        orch.profiles.db = _HookDb(real_db, lambda: other.set_lane("pm", "cloud"))
        try:
            await orch.profiles.set_model("pm", "local-small", registered={"local-small"})
            raise AssertionError("set_model should have lost")
        except ConcurrentProfileChange:
            pass
        orch.profiles.db = real_db
        rows = await _rows(orch, "pm")
        assert [(x.version, x.active, x.lane) for x in rows] == [(1, False, "local"), (2, True, "cloud")]
        await orch.profiles.refresh()
        _assert_one_active_and_cache_matches(orch, rows, "pm")
    await orch.db.dispose()


async def test_loser_refreshes_cache_when_the_winner_is_another_registry(db_url):
    """Cross-process winner: only the loser's own refresh can bring its cache in line with the DB."""
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        real_db = orch.profiles.db
        other = ProfileRegistry(real_db, str(orch.profiles.dir))
        orch.profiles.db = _HookDb(
            real_db, lambda: other.set_model("pm", "local-small", registered={"local-small"}))
        async with _client(app) as c:
            r = await c.post("/profiles/lane", json={"name": "pm", "lane": "cloud"})
        orch.profiles.db = real_db
        assert r.status_code == 409
        rows = await _rows(orch, "pm")
        _assert_one_active_and_cache_matches(orch, rows, "pm")
        assert orch.profiles.get("pm").model == "local-small"
    await orch.db.dispose()


async def test_direct_set_lane_with_a_bad_lane_raises_valueerror_and_writes_nothing(db_url):
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        for bad in ("moon", "", "Cloud"):
            try:
                await orch.profiles.set_lane("pm", bad)
                raise AssertionError("no ValueError")
            except ValueError:
                pass
        assert [x.version for x in await _rows(orch, "pm")] == [1]
    await orch.db.dispose()


async def test_a_keyerror_after_the_commit_is_not_a_404(db_url):
    """The write succeeded (v2 exists); a KeyError from the later refresh must not read as 'unknown
    profile'. Only UnknownProfile maps to 404."""
    orch, app = await _app(db_url)
    async with app.router.lifespan_context(app):
        async def boom():
            raise KeyError("stray")

        orch.profiles.refresh = boom
        async with _client(app) as c:
            r = await c.post("/profiles/lane", json={"name": "pm", "lane": "cloud"})
        assert r.status_code != 404
        rows = await _rows(orch, "pm")
        assert [(x.version, x.active, x.lane) for x in rows] == [(1, False, "local"), (2, True, "cloud")]
        assert not await _refused(orch)
    await orch.db.dispose()
