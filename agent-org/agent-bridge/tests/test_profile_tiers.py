"""Tiered model routing for the org's roles (mt-policy, tracker H2; operator 2026-09-30: "planning
the work requires long horizon and plan considerations while the work to one file or another
requires only their specific narrow scope of work").

The routing path is the EXISTING one - a task kind runs under a profile, ModelRouter sends that
profile's `model` - so the rule lives on the profile: `tier` in profiles/*.json, and the tier's
model role per lane in Settings.profile_tier_models_local / _cloud. A live install's profile moves
ONLY through the governed `set profile <name> model <role>` intent; `tier_drift()` names the
command and writes nothing. Fakes only (FakeModelClient, FakeHarness); no network, no docker."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.modules.profiles import TIERS, parse_tier_models, tier_model

from test_profile_model_intent import _events, _orch, _rows
from authtok import OP_HEADERS, OP_TOKEN

ROOT = Path(__file__).resolve().parents[1]

#: Task kind -> the profile it already runs under (the call sites in app/), and the tier the
#: operator's rule gives that kind. Planning / decomposition / review / monitoring are long-horizon
#: judgement -> large. The coding worker was small (a file-scoped edit is narrow) until ao-thinkon
#: (2026-10-11, principle 4 "thinking is part of the work"): on the local lane small and large are
#: the same 27B and small differs only in having thinking OFF, so the worker moved to large.
TASK_KINDS = {
    "planning (plan generation, readiness)": ("planner", "large"),
    "decomposition (lifecycle plan, intent)": ("po", "large"),
    "monitoring (deviation, alignment, plan gate)": ("pm", "large"),
    "review: correctness lens": ("reviewer-correctness", "large"),
    "review: ethics lens": ("reviewer-ethics", "large"),
    "review: scope lens": ("reviewer-scope", "large"),
    "review: security lens": ("reviewer-security", "large"),
    "coding turn (domain worker; thinking on since ao-thinkon)": ("worker-default", "large"),
}


def _pre_ao_think(tmp_path: Path) -> Path:
    """The shipped profiles as an install seeded BEFORE ao-thinkon has them in its database: every
    judgement role on local-large, the worker on local-small (mt-policy's seed). The tier is read
    from the seed file (large now); the model is what the database kept."""
    d = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles", d)
    f = d / "worker-default.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["model"] = "local-small"
    f.write_text(json.dumps(data), encoding="utf-8")
    return d


def _small_worker(tmp_path: Path) -> Path:
    """A profile set whose worker is on the SMALL tier (the rollback shape: `"tier": "small"`)."""
    d = _pre_ao_think(tmp_path)
    f = d / "worker-default.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["tier"] = "small"
    f.write_text(json.dumps(data), encoding="utf-8")
    return d


def _seeds() -> dict[str, dict]:
    return {json.loads(f.read_text(encoding="utf-8"))["profile"]: json.loads(f.read_text(encoding="utf-8"))
            for f in sorted((ROOT / "profiles").glob("*.json"))}


def test_every_shipped_profile_names_a_tier():
    for name, d in _seeds().items():
        assert d.get("tier") in TIERS, name


@pytest.mark.parametrize("kind", sorted(TASK_KINDS))
def test_task_kind_rule(kind):
    profile, tier = TASK_KINDS[kind]
    assert _seeds()[profile]["tier"] == tier, kind


def test_pm_voice_stays_large():
    """Operator-facing synthesis is truth-critical (charters/pm-voice.md: 'a confident wrong answer
    is the worst thing you can produce') - not a mechanical step, so not demoted."""
    assert _seeds()["pm-voice"]["tier"] == "large"


def test_every_seed_asks_for_its_tiers_model_role():
    s = Settings(_env_file=None)
    for name, d in _seeds().items():
        assert d["model"] == tier_model(s, d["tier"], d["lane"]), name


def test_the_tier_models_are_ones_the_governed_intent_accepts():
    """The landing moves a live profile with `set profile <n> model <role>`; that handler refuses a
    model outside the lane's chat set, so a tier model outside it could never be landed."""
    s = Settings(_env_file=None)
    local = {m.strip() for m in s.profile_chat_models_local.split(",")}
    cloud = {m.strip() for m in s.profile_chat_models_cloud.split(",")}
    assert set(parse_tier_models(s.profile_tier_models_local).values()) <= local
    assert set(parse_tier_models(s.profile_tier_models_cloud).values()) <= cloud
    assert parse_tier_models(s.profile_tier_models_local) == {"large": "local-large", "small": "local-small"}


@pytest.mark.parametrize("spec", ["large", "large=", "medium=local-small", "large=local-large,huge=x"])
def test_a_malformed_tier_model_spec_is_loud(spec):
    with pytest.raises(ValueError):
        parse_tier_models(spec)


async def test_every_shipped_role_routes_to_local_large_the_worker_included(db_url):
    """End to end through the EXISTING path: profile -> ModelRouter -> the model the gateway is
    asked for (FakeModelClient records it). ao-thinkon: the worker asks for the thinking role too."""
    orch, chat, db = await _orch(db_url, ROOT / "profiles")
    try:
        client = orch.models._get_client()
        await orch.models.complete("planner", "sys", "plan this")
        await orch.models.complete("reviewer-correctness", "sys", "review this")
        await orch.models.complete("worker-default", "sys", "edit one file")
        asked = [c["model"] for c in client.calls if c["kind"] == "complete"][-3:]
        assert asked == ["local-large", "local-large", "local-large"]
        assert orch.profiles.tier_drift(orch.s) == []
    finally:
        await db.dispose()


async def test_a_small_tier_profile_still_routes_to_local_small(db_url, tmp_path):
    """The small tier stays defined (rollback: the worker's seed back to `"tier": "small"`)."""
    orch, chat, db = await _orch(db_url, _small_worker(tmp_path))
    try:
        client = orch.models._get_client()
        await orch.models.complete("worker-default", "sys", "edit one file")
        assert [c["model"] for c in client.calls if c["kind"] == "complete"][-1:] == ["local-small"]
        assert orch.profiles.tier_drift(orch.s) == []
    finally:
        await db.dispose()


async def test_an_existing_install_moves_only_through_the_governed_intent(db_url, tmp_path):
    """The ao-thinkon landing: an install seeded before it (worker on local-small) is NOT moved by
    the new seed file; tier_drift names the one command, and sending it clears it."""
    orch, chat, db = await _orch(db_url, _pre_ao_think(tmp_path))
    try:
        assert orch.profiles.get("worker-default").tier == "large"      # the tier is read...
        assert orch.profiles.get("worker-default").model == "local-small"  # ...the model is not moved
        drift = orch.profiles.tier_drift(orch.s)
        assert drift == [{"profile": "worker-default", "tier": "large", "lane": "local",
                          "model": "local-small", "expected": "local-large",
                          "command": "set profile worker-default model local-large"}]
        assert [r.version for r in await _rows(db, "worker-default")] == [1]   # read-only
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(drift[0]["command"], mgmt, thread_id="t1",
                             actor="operator-api:landing-ao-thinkon")
        assert orch.profiles.get("worker-default").model == "local-large"
        assert orch.profiles.get("worker-default").tier == "large"      # survives the new version
        assert orch.profiles.tier_drift(orch.s) == []
        ev = await _events(db, "profile_model_set")
        assert [(e.payload["profile"], e.payload["before"], e.payload["after"]) for e in ev] == [
            ("worker-default", "local-small", "local-large")]
    finally:
        await db.dispose()


async def test_a_cloud_profile_with_the_cloud_lane_off_is_judged_on_the_local_map(db_url):
    orch, chat, db = await _orch(db_url, ROOT / "profiles")
    try:
        await orch.profiles.set_lane("pm", "cloud")
        assert orch.profiles.get("pm").tier == "large"
        assert orch.profiles.tier_drift(orch.s) == []                   # cloud off -> local-large ok
        on = orch.s.model_copy(update={"cloud_enabled": True})
        assert orch.profiles.tier_drift(on) == [{
            "profile": "pm", "tier": "large", "lane": "cloud", "model": "local-large",
            "expected": "cloud-large", "command": "set profile pm model cloud-large"}]
    finally:
        await db.dispose()


async def test_get_profiles_reports_tier_and_drift(db_url, tmp_path):
    import httpx

    from app.db import Database
    from app.main import create_app
    from app.adapters.chat import FakeChatAdapter
    from app.modules.model_router import FakeModelClient
    from app.orchestrator import Orchestrator
    from app.worker.harness import FakeHarness

    d = _pre_ao_think(tmp_path)
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
            body = (await c.get("/profiles")).json()
    assert body["profiles"]["planner"]["tier"] == "large"
    assert body["profiles"]["worker-default"]["tier"] == "large"
    assert [x["command"] for x in body["tier_drift"]] == ["set profile worker-default model local-large"]


# ── attempt 2 (tester F4): a malformed tier-model spec must not 500 the profile listing ──
async def _get_profiles(db_url, tmp_path, **extra) -> tuple[int, dict]:
    import httpx

    from app.adapters.chat import FakeChatAdapter
    from app.db import Database
    from app.main import create_app
    from app.modules.model_router import FakeModelClient
    from app.orchestrator import Orchestrator
    from app.worker.harness import FakeHarness

    d = _pre_ao_think(tmp_path)
    settings = Settings(
        _env_file=None, chat_adapter="fake", database_url=db_url, profiles_dir=str(d),
        charters_dir=str(ROOT / "charters"), floor_dir=str(ROOT / "floor"),
        worker_instance_urls="http://w1:8090", operator_token=OP_TOKEN, **extra)
    orch = Orchestrator(settings, Database(db_url), FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    app = create_app(orch)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                     headers=OP_HEADERS) as c:
            r = await c.get("/profiles")
    return r.status_code, r.json()


@pytest.mark.parametrize("field,spec,env,needle", [
    ("profile_tier_models_local", "large=local-large", "AO_PROFILE_TIER_MODELS_LOCAL", "small"),
    ("profile_tier_models_local", "LARGE=local-large,small=local-small", "AO_PROFILE_TIER_MODELS_LOCAL", "LARGE"),
    ("profile_tier_models_local", "garbage", "AO_PROFILE_TIER_MODELS_LOCAL", "garbage"),
    ("profile_tier_models_local", "", "AO_PROFILE_TIER_MODELS_LOCAL", "large, small"),
    ("profile_tier_models_cloud", "small=cloud-small", "AO_PROFILE_TIER_MODELS_CLOUD", "large"),
])
async def test_a_malformed_tier_model_spec_reports_drift_unavailable_not_500(
        db_url, tmp_path, caplog, field, spec, env, needle):
    import logging
    caplog.set_level(logging.WARNING)
    status, body = await _get_profiles(db_url, tmp_path, **{field: spec})
    assert status == 200
    assert len(body["profiles"]) == 9                       # the listing survives
    assert body["tier_drift"] is None
    assert env in body["tier_drift_error"] and needle in body["tier_drift_error"]
    assert any("tier_drift unavailable" in r.getMessage() and env in r.getMessage()
               for r in caplog.records)


async def test_a_well_formed_spec_has_no_error_key(db_url, tmp_path):
    status, body = await _get_profiles(db_url, tmp_path)
    assert status == 200 and "tier_drift_error" not in body
    assert [x["profile"] for x in body["tier_drift"]] == ["worker-default"]
