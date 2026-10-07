"""A role profile's model moves only through the governed operator intent (model-roles, mr-consumers;
operator decision 2026-09-30: every operator inlet is NL -> OperatorIntent -> a governed handler).

The DB owns a profile's live values; the shipped profiles/*.json only seed a MISSING profile. So an
existing install moves a profile from `qwen36-27b` to `local-large` by saying so, and the handler
writes one audited version with only `model` changed. Fakes only (FakeModelClient answers the
gateway's model list)."""

from __future__ import annotations

import asyncio
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


async def _orch(db_url, profiles_dir: Path, **extra):
    settings = Settings(
        _env_file=None, chat_adapter="fake", **extra,
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


class _CommitBarrier:
    """Wraps the registry's Database so every profile COMMIT waits until `n` writers reach it -
    i.e. each has already read the active row. Makes the P1 race deterministic instead of
    depending on how the event loop happens to interleave two requests."""

    def __init__(self, db, n: int = 2):
        self._db, self._barrier = db, asyncio.Barrier(n)

    def __getattr__(self, k):
        return getattr(self._db, k)

    def session_factory(self):
        cm, barrier = self._db.session_factory(), self._barrier

        class _Cm:
            async def __aenter__(self_inner):
                sess = await cm.__aenter__()
                real = sess.commit

                async def commit():
                    await asyncio.wait_for(barrier.wait(), 10)
                    return await real()

                sess.commit = commit
                return sess

            async def __aexit__(self_inner, *a):
                return await cm.__aexit__(*a)

        return _Cm()


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
        # an allowed chat role the gateway does not list (e.g. a gateway still before mr-gateway)
        orch.models._client.registered_models = ["qwen36-27b", "qwen36-27b:nothink"]
        await orch.nl_intake("set profile pm model local-small", mgmt, thread_id="t1")
        orch.models._client.registered_models = ConnectionError("gateway down")
        await orch.nl_intake("set profile pm model local-large", mgmt, thread_id="t1")
        assert {n: [(r.version, r.model) for r in await _rows(db, n)]
                for n in orch.profiles.all()} == snapshot       # nothing written
        assert not await _events(db, "profile_model_set")
        refused = await _events(db, "profile_model_refused")
        assert [e.payload["profile"] for e in refused] == ["nobody", "pm", "pm", "pm"]
        msgs = [p["message"] for p in chat.posted]
        assert any("no profile called `nobody`" in m for m in msgs)
        assert any("`local-huge` is not a chat model" in m for m in msgs)
        assert any("`local-small` is not registered at the gateway" in m for m in msgs)
        assert any("cannot be verified" in m for m in msgs)
    finally:
        await db.dispose()


async def test_the_po_model_path_is_only_ever_a_dry_run_with_the_exact_command(db_url, tmp_path):
    """P3: a model-CLASSIFIED change never writes, even when the model did not mark it a dry run
    (a misread question must not apply); the reply is the handler's outcome plus the exact
    command. P2: the model's own reply is not posted."""
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        orch.models._client.queue_structured(OperatorIntent(
            kind="profile_model", profile_name="reviewer-scope", profile_model="local-large",
            profile_dry_run=False, reply="Done - reviewer-scope now uses it."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("what would happen if the scope reviewer used local-large?", mgmt,
                             thread_id="t1", user_id="u-operator")
        assert orch.profiles.get("reviewer-scope").model == "qwen36-27b"          # nothing written
        assert [r.version for r in await _rows(db, "reviewer-scope")] == [1]
        assert not await _events(db, "profile_model_set")
        msgs = [p["message"] for p in chat.posted]
        assert any("Dry run" in m and "send exactly: `set profile reviewer-scope model local-large`" in m
                   for m in msgs)
        assert not any("Done - reviewer-scope now uses it." in m for m in msgs)    # P2
        # the exact command it offered is what applies
        await orch.nl_intake("set profile reviewer-scope model local-large", mgmt, thread_id="t1",
                             user_id="u-operator")
        assert orch.profiles.get("reviewer-scope").model == "local-large"
        ev = await _events(db, "profile_model_set")
        assert len(ev) == 1 and ev[0].actor == "u-operator"
    finally:
        await db.dispose()


async def test_a_po_path_refusal_posts_only_the_refusal(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        orch.models._client.queue_structured(OperatorIntent(
            kind="profile_model", profile_name="pm", profile_model="local-embed",
            reply="Done - pm now uses it."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("give pm the embed model", mgmt, thread_id="t1")
        msgs = [p["message"] for p in chat.posted]
        assert any(m.startswith("⚠️ Profile model NOT changed") for m in msgs)
        assert not any("Done - pm now uses it." in m for m in msgs)
    finally:
        await db.dispose()


async def test_only_chat_models_for_the_lane_are_accepted(db_url, tmp_path):
    """P4: 'registered at the gateway' is not enough - local-embed is registered, and the cloud
    group is listed by the local gateway once an OpenRouter key is set. Only the lane's chat set."""
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        orch.models._client.registered_models = orch.models._client.registered_models + [
            "cloud-large", "cloud-small"]
        mgmt = await orch.mgmt_channel_id()
        for bad in ("local-embed", "bge-m3", "cloud-large", "cloud-small"):
            await orch.nl_intake(f"set profile pm model {bad}", mgmt, thread_id="t1")
        assert [r.version for r in await _rows(db, "pm")] == [1]
        refused = await _events(db, "profile_model_refused")
        assert [e.payload["model"] for e in refused] == ["local-embed", "bge-m3", "cloud-large", "cloud-small"]
        assert all("not a chat model for a profile on the local lane" in e.payload["reason"] for e in refused)
        for ok in ("local-small", "local-large:nothink", "qwen36-27b:nothink", "local-large"):
            await orch.nl_intake(f"set profile pm model {ok}", mgmt, thread_id="t1")
        assert [r.model for r in await _rows(db, "pm")] == [
            "qwen36-27b", "local-small", "local-large:nothink", "qwen36-27b:nothink", "local-large"]
    finally:
        await db.dispose()


async def test_the_cloud_group_only_for_a_profile_on_an_enabled_cloud_lane(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"), cloud_enabled=True)
    try:
        orch.models._client.registered_models = ["cloud-large", "cloud-small"]
        await orch.profiles.set_lane("pm", "cloud")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("set profile pm model local-large", mgmt, thread_id="t1")   # not a cloud chat model
        await orch.nl_intake("set profile pm model cloud-large", mgmt, thread_id="t1")
        await orch.nl_intake("set profile po model cloud-large", mgmt, thread_id="t1")   # po is local
        assert orch.profiles.get("pm").model == "cloud-large" and orch.profiles.get("pm").lane == "cloud"
        assert orch.profiles.get("po").model == "qwen36-27b"
        assert [e.payload["model"] for e in await _events(db, "profile_model_refused")] == [
            "local-large", "cloud-large"]
        # the cloud list is asked of the CLOUD endpoint
        lm = [c for c in orch.models._client.calls if c["kind"] == "list_models"]
        assert lm and lm[-1]["api_base"] == orch.s.cloud_api_base
    finally:
        await db.dispose()


async def test_profile_names_are_case_folded(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("SET PROFILE PM MODEL local-large", mgmt, thread_id="t1")
        assert orch.profiles.get("pm").model == "local-large"
        await orch.nl_intake("set profile po model LOCAL-LARGE", mgmt, thread_id="t1")   # models are not
        assert orch.profiles.get("po").model == "qwen36-27b"
    finally:
        await db.dispose()


async def test_two_concurrent_intents_on_one_profile_one_wins_one_is_refused(db_url, tmp_path):
    """P1: the (name, version) unique constraint lets exactly one write; the other is caught,
    replied to, audited as a conflict - never an unhandled IntegrityError."""
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        orch.profiles.db = _CommitBarrier(orch.profiles.db)
        res = await asyncio.gather(
            orch.nl_intake("set profile pm model local-large", mgmt, thread_id="t1"),
            orch.nl_intake("set profile pm model local-small", mgmt, thread_id="t1"))
        outcomes = sorted(r["outcome"] for r in res)
        assert outcomes == ["applied", "conflict"], outcomes
        rows = await _rows(db, "pm")
        assert [r.version for r in rows] == [1, 2] and [r.active for r in rows] == [False, True]
        winner = next(r for r in res if r["outcome"] == "applied")
        assert rows[-1].model == winner["after"]
        conflict = [e for e in await _events(db, "profile_model_refused") if e.payload["outcome"] == "conflict"]
        assert len(conflict) == 1
        assert any("changed concurrently" in p["message"] and "retry" in p["message"] for p in chat.posted)
        assert orch.profiles.get("pm").model == winner["after"]
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
        # mt-policy: every shipped profile seeds its TIER's model role - local-large for the
        # planning / decomposition / review roles, local-small for the file-scoped worker
        # (test_profile_tiers.py holds the rule itself).
        assert {n: p.model for n, p in orch.profiles.all().items() if p.model != "local-large"} == {
            "worker-default": "local-small"}
    finally:
        await db.dispose()


async def test_the_landing_path_through_the_http_inlet(db_url, tmp_path):
    """What the landing runs: POST /nl with a named actor for each profile, dry run first, then
    GET /profiles to read the result back."""
    import httpx

    from app.main import create_app
    from authtok import OP_HEADERS, OP_TOKEN

    d = _profiles_on(tmp_path, "qwen36-27b")
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url, profiles_dir=str(d),
        charters_dir=str(ROOT / "charters"), floor_dir=str(ROOT / "floor"),
        worker_instance_urls="http://w1:8090", operator_token=OP_TOKEN)
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    app = create_app(orch)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                     headers=OP_HEADERS) as c:
            names = sorted((await c.get("/profiles")).json()["profiles"])
            assert len(names) == 9
            for n in names:
                r = await c.post("/nl", json={"message": f"set profile {n} model local-large (dry run)",
                                              "actor": "landing-mr-consumers"})
                assert r.status_code == 200 and r.json()["outcome"] == "dry_run"
            assert {p["model"] for p in (await c.get("/profiles")).json()["profiles"].values()} == {"qwen36-27b"}
            for n in names:
                r = await c.post("/nl", json={"message": f"set profile {n} model local-large",
                                              "actor": "landing-mr-consumers"})
                assert r.status_code == 200 and r.json()["outcome"] == "applied"
            got = (await c.get("/profiles")).json()["profiles"]
            assert {p["model"] for p in got.values()} == {"local-large"}
            # P1 through HTTP: two concurrent writes to one profile -> one 200, one 409
            orch.profiles.db = _CommitBarrier(orch.profiles.db)
            rs = await asyncio.gather(*(c.post("/nl", json={"message": f"set profile pm model {m}",
                                                           "actor": "race"})
                                        for m in ("local-small", "local-large:nothink")))
            assert sorted(r.status_code for r in rs) == [200, 409]
            loser = next(r for r in rs if r.status_code == 409).json()
            assert loser["outcome"] == "conflict" and "retry" in loser["reason"]
    ev = await _events(orch.db, "profile_model_set")
    landing = [e for e in ev if e.actor == "operator-api:landing-mr-consumers"]
    assert len(landing) == 9
    await orch.db.dispose()


async def test_a_write_landing_while_the_gateway_is_asked_is_a_conflict(db_url, tmp_path):
    """N1: the handler validates against the row it READ, then awaits the gateway list. A lane
    flip (or another model change) committed in that window must not be written over: conflict,
    nothing written by this request, the other write intact."""
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        client = orch.models._client
        real = client.list_models
        mgmt = await orch.mgmt_channel_id()

        async def flip_lane_meanwhile(**kw):
            await orch.profiles.set_lane("pm", "cloud")         # commits during the wait
            return await real(**kw)

        client.list_models = flip_lane_meanwhile
        res = await orch.nl_intake("set profile pm model local-large", mgmt, thread_id="t1")
        assert res["outcome"] == "conflict"
        pm = orch.profiles.get("pm")
        assert (pm.lane, pm.model) == ("cloud", "qwen36-27b")   # the flip stands; no model write
        assert [(r.version, r.lane, r.model) for r in await _rows(db, "pm")][-1] == (2, "cloud", "qwen36-27b")

        async def change_model_meanwhile(**kw):
            await orch.profiles.set_model("po", "local-small", registered={"local-small"})
            return await real(**kw)

        client.list_models = change_model_meanwhile
        res = await orch.nl_intake("set profile po model local-large", mgmt, thread_id="t1")
        assert res["outcome"] == "conflict"
        assert orch.profiles.get("po").model == "local-small"
        assert [r.version for r in await _rows(db, "po")] == [1, 2]
        assert not await _events(db, "profile_model_set")
        assert len([e for e in await _events(db, "profile_model_refused")
                    if e.payload["outcome"] == "conflict"]) == 2
        assert not any("unchanged)." in p["message"] and "now asks for" in p["message"] for p in chat.posted)
    finally:
        await db.dispose()


async def test_a_po_classification_with_a_project_or_repo_goes_only_to_the_handler(db_url, tmp_path):
    """N2: a profile_model classification that ALSO carries an unknown project or a repo URL is
    routed to the handler before any project / onboarding branch: no model reply, no onboarding."""
    orch, chat, db = await _orch(db_url, _profiles_on(tmp_path, "qwen36-27b"))
    try:
        mgmt = await orch.mgmt_channel_id()
        for extra in ({"project": "no-such-project"},
                      {"repo_url": "https://github.com/acme/new-thing.git"}):
            chat.posted.clear()
            orch.models._client.queue_structured(OperatorIntent(
                kind="profile_model", profile_name="pm", profile_model="local-small",
                reply="Done - pm now uses local-small.", **extra))
            res = await orch.nl_intake("move pm to the small model", mgmt, thread_id="t1")
            assert res["outcome"] == "dry_run", extra
            msgs = [p["message"] for p in chat.posted]
            assert len(msgs) == 1 and "send exactly: `set profile pm model local-small`" in msgs[0], msgs
            assert not any("Done - pm" in m for m in msgs)
        assert await orch.projects.list() == []                     # nothing onboarded
        assert orch.profiles.get("pm").model == "qwen36-27b"
    finally:
        await db.dispose()
