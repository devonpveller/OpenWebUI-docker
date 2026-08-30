"""ONE runner registry, and a dispatch that actually consults it (DFU U4, bidirectional clause).

The claim under test is PLAN section 2's U4 row: "agent-org workers as harness runners and
vice versa - one profile mechanism governs both". What this file pins is the half of that
claim which survived the audit (see documentation/notes/u4bidir-findings.md):

  * the unifiable object is the RUNNER REGISTRY (what substrates exist, of what kind, at
    what address), not the profile - the two systems' profile tables answer different
    questions, and collapsing them would force each to carry fields it has no use for;
  * the thing that BLOCKED "vice versa" was never the WorkerHarness protocol - that has
    been pluggable since it was written (LittleCoderHarness + FakeHarness) - but the
    SELECTION: the orchestrator chose one harness implementation ONCE, globally, from
    `settings.chat_adapter`. A pool could therefore never be heterogeneous.

So these tests drive the real dispatch path (`Router.wake`) and assert that the
implementation reached depends on the registry's answer for that worker's address. Change
the registry, and a different implementation runs: that is the difference between a
resolver both sides import and a resolver the execution path consults.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.modules.runners import (
    RunnerDispatch,
    RunnerNotProvisionedError,
    RunnerRegistry,
)
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness, WorkResult

ROOT = Path(__file__).resolve().parents[1]
SHARED_REGISTRY = ROOT.parents[1] / "scripts" / "agent-harness" / "harness.config.json"


class _MarkedHarness(FakeHarness):
    """A FakeHarness that stamps its own name on every result, so a test can tell WHICH
    implementation a dispatch reached rather than merely that one did."""

    def __init__(self, mark: str) -> None:
        super().__init__()
        self.mark = mark
        self.woke: list[str] = []

    async def wake(self, base_url, session_id, prompt, **kw):  # type: ignore[override]
        self.woke.append(base_url)
        return WorkResult("done", "task-" + self.mark, output="[" + self.mark + "] ok")


def _registry_file(tmp_path: Path, runners: dict) -> str:
    p = tmp_path / "runner-registry.json"
    p.write_text(json.dumps({"version": 1, "runners": runners}), encoding="utf-8")
    return str(p)


# -- the registry itself ------------------------------------------------------
def test_registry_reads_the_shared_harness_config():
    """The SHIPPED harness.config.json is a valid runner registry for this bridge too -
    that is what "one declaration, both systems" means. If this fails, the two drifted."""
    reg = RunnerRegistry.load(registry_file=str(SHARED_REGISTRY), fallback_urls="")
    assert "claude-code" in reg.specs and "little-coder" in reg.specs
    assert reg.specs["claude-code"].kind == "claude-code"
    # agent-org's own worker pool is declared THERE, not only in this plane's env file.
    pool = reg.pool()
    assert [u for _i, u, _k in pool] == [
        "http://ao-worker-1:8090",
        "http://ao-worker-2:8090",
    ]
    assert {k for _i, _u, k in pool} == {"little-coder"}


def test_env_csv_still_wins_and_can_carry_a_kind(tmp_path):
    """Precedence is the house one (file < environment), so an operator's existing
    AO_WORKER_INSTANCE_URLS keeps working unchanged - and can now name a kind per entry."""
    f = _registry_file(
        tmp_path,
        {"little-coder": {"kind": "little-coder", "pooled": True,
                          "instances": {"w1": "http://from-file:8090"}}},
    )
    reg = RunnerRegistry.load(
        registry_file=f,
        fallback_urls="http://w1:8090,claude-code=http://cc1:9099",
    )
    assert reg.pool() == [
        ("worker-1", "http://w1:8090", "little-coder"),
        ("worker-2", "http://cc1:9099", "claude-code"),
    ]
    assert reg.kind_for("http://cc1:9099") == "claude-code"
    assert reg.kind_for("http://never-registered:1") == "little-coder"  # documented default


def test_a_missing_registry_file_degrades_to_the_env_pool(tmp_path):
    """A bind-mount that did not land must not take the org down - it degrades to exactly
    the pre-U4 behaviour (the env CSV), the same posture as the harness readers."""
    reg = RunnerRegistry.load(
        registry_file=str(tmp_path / "nope.json"),
        fallback_urls="http://w1:8090",
    )
    assert reg.pool() == [("worker-1", "http://w1:8090", "little-coder")]


# -- the dispatch actually consults it ----------------------------------------
def test_dispatch_routes_by_the_registry_answer(tmp_path):
    lc, cc = _MarkedHarness("lc"), _MarkedHarness("cc")
    reg = RunnerRegistry.load(
        registry_file=_registry_file(tmp_path, {}),
        fallback_urls="http://w1:8090,claude-code=http://cc1:9099",
    )
    d = RunnerDispatch({"little-coder": lc, "claude-code": cc}, reg)
    assert d.impl_for("http://w1:8090") is lc
    assert d.impl_for("http://cc1:9099") is cc


async def test_an_unprovisioned_kind_fails_loudly_not_silently(tmp_path):
    """The "vice versa" direction is PARKED, not pretended: there is no claude-code task
    endpoint in this stack, so routing to that kind must raise a message naming what is
    missing. A wrapper that swallowed this would pass every test and change nothing."""
    reg = RunnerRegistry.load(
        registry_file=_registry_file(tmp_path, {}),
        fallback_urls="claude-code=http://cc1:9099",
    )
    d = RunnerDispatch.default(reg)
    with pytest.raises(RunnerNotProvisionedError) as e:
        await d.wake("http://cc1:9099", "s", "p")
    assert "claude-code" in str(e.value)


# -- end to end through the real wake path ------------------------------------
async def _orch(db_url, tmp_path, urls: str):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"),
        worker_instance_urls=urls,
        runner_registry_file=_registry_file(tmp_path, {}),
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient())
    await orch.setup()
    return orch, db


async def _wake_with(orch, lc, cc):
    """Swap in two DISTINGUISHABLE implementations behind the real dispatcher, then drive
    the real wake path. Nothing else about the call differs between the two tests below."""
    orch.harness = RunnerDispatch({"little-coder": lc, "claude-code": cc}, orch.runners)
    orch.router.harness = orch.harness
    await orch.projects.add("mono", "https://example.invalid/me/mono.git")
    eid, chan, root = await orch.router.open_effort("one", project="mono")
    return await orch.router.wake(eid, "worker-default", root, chan)


async def test_wake_worker_reaches_the_implementation_the_registry_names(db_url, tmp_path):
    """THE test. Two runs of the same public dispatch path over the same code, differing
    only in what the registry says about the worker's address - and a different
    implementation runs. RED before app/modules/runners.py existed: the orchestrator bound
    ONE harness for the whole pool at construction time."""
    lc, cc = _MarkedHarness("lc"), _MarkedHarness("cc")
    orch, db = await _orch(db_url, tmp_path, "http://w1:8090")
    try:
        res = await _wake_with(orch, lc, cc)
        assert res is not None and res.output == "[lc] ok"
        assert lc.woke == ["http://w1:8090"] and cc.woke == []
    finally:
        await db.dispose()


async def test_the_same_path_reaches_the_other_runner_when_the_registry_changes(
    db_url, tmp_path
):
    lc, cc = _MarkedHarness("lc"), _MarkedHarness("cc")
    # SAME code, SAME call - only the registry's answer for this address changed.
    orch, db = await _orch(db_url, tmp_path, "claude-code=http://cc1:9099")
    try:
        res = await _wake_with(orch, lc, cc)
        assert res is not None and res.output == "[cc] ok"
        assert cc.woke == ["http://cc1:9099"] and lc.woke == []
    finally:
        await db.dispose()

# -- the shared file governs the LIVE pool, not just a test fixture -----------
def test_a_bare_env_url_takes_its_substrate_from_the_shared_file(tmp_path):
    """The precedence that decides whether the shared registry is load-bearing or decorative.

    Every operator's `AO_WORKER_INSTANCE_URLS` is a list of BARE urls, and it stays
    authoritative about WHICH daemons are in the pool. But a bare url states an address and
    nothing else, so WHAT each one is comes from the file both systems read. If the env had
    silently won that question too, the registry would be a file nobody's dispatch consults -
    exactly the vacuity this phase was told to avoid.
    """
    f = _registry_file(
        tmp_path,
        {"experimental": {"kind": "claude-code", "pooled": True,
                          "instances": {"x1": "http://w9:8090"}}},
    )
    reg = RunnerRegistry.load(registry_file=f, fallback_urls="http://w9:8090")
    assert reg.pool() == [("worker-1", "http://w9:8090", "claude-code")]
    # ... and an entry that DOES state its kind still overrides the file.
    reg2 = RunnerRegistry.load(registry_file=f, fallback_urls="little-coder=http://w9:8090")
    assert reg2.kind_for("http://w9:8090") == "little-coder"


async def test_changing_only_the_shared_file_changes_which_implementation_runs(
    db_url, tmp_path
):
    """The end-to-end version of the test above, and the proof this phase is not a wrapper.

    Two runs. Same code, same settings, same `AO_WORKER_INSTANCE_URLS`, same public call.
    ONE json file differs - and a different implementation executes the wake.
    """
    lc, cc = _MarkedHarness("lc"), _MarkedHarness("cc")
    env_pool = "http://w1:8090"

    async def _run(runners: dict, mark_pair):
        settings = Settings(
            _env_file=None, chat_adapter="fake",
            profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
            floor_dir=str(ROOT / "floor"),
            worker_instance_urls=env_pool,
            runner_registry_file=_registry_file(tmp_path, runners),
            max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
            review_mode="off", plan_approval="off",
        )
        db = Database(db_url)
        orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient())
        await orch.setup()
        try:
            return await _wake_with(orch, *mark_pair)
        finally:
            await db.dispose()

    said_little_coder = await _run(
        {"a": {"kind": "little-coder", "instances": {"w1": env_pool}}}, (lc, cc)
    )
    assert said_little_coder.output == "[lc] ok"

    # Same everything, one word changed in the file.
    said_claude_code = await _run(
        {"a": {"kind": "claude-code", "instances": {"w1": env_pool}}}, (lc, cc)
    )
    assert said_claude_code.output == "[cc] ok"
    assert lc.woke == [env_pool] and cc.woke == [env_pool]


# -- the third reader agrees with the harness's own two ----------------------
def test_this_reader_agrees_with_the_harness_python_reader():
    """The anti-drift pin, from this side. `scripts/agent-harness/test_harness_config.py`
    pins its PowerShell and Python readers to each other; this pins THIS reader to those.
    Three readers of one file is a standing invitation to drift, and drift here is not
    cosmetic - it is two orchestrators believing in different worker pools."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_harness_config", SHARED_REGISTRY.parent / "config.py")
    harness_config = importlib.util.module_from_spec(spec)
    sys.modules["_harness_config"] = harness_config
    spec.loader.exec_module(harness_config)
    try:
        harness_config.load(fresh=True)
        theirs = [(a["url"], a["kind"]) for a in harness_config.runner_pool()]
        reg = RunnerRegistry.load(registry_file=str(SHARED_REGISTRY), fallback_urls="")
        ours = [(u, k) for _i, u, k in reg.pool()]
        assert ours == theirs
        assert set(harness_config.runner_names()) == set(reg.specs)
    finally:
        sys.modules.pop("_harness_config", None)

# -- the dispatcher must cover the WHOLE protocol ----------------------------
async def test_dispatch_forwards_every_method_the_protocol_declares(tmp_path):
    """A dispatcher that covers only the methods someone remembered is worse than none.

    `RunnerDispatch` stands between every call site and every implementation, so a method
    added to `WorkerHarness` and not forwarded here would not fail at import or at review -
    it would AttributeError in production, on the one dispatch that used it. This walks the
    Protocol itself, so the guard cannot fall behind the thing it guards.
    """
    from app.worker import harness as harness_mod

    protocol_methods = sorted(
        n for n in vars(harness_mod.WorkerHarness)
        if not n.startswith("_") and callable(vars(harness_mod.WorkerHarness)[n])
    )
    assert protocol_methods, "the WorkerHarness protocol declares no methods - check this test"

    calls: list[str] = []

    class _Recorder:
        def __getattr__(self, name):
            async def _call(*a, **kw):
                calls.append(name)
                return None
            return _call

    reg = RunnerRegistry.load(registry_file=_registry_file(tmp_path, {}),
                              fallback_urls="http://w1:8090")
    d = RunnerDispatch({"little-coder": _Recorder()}, reg)

    # One positional argument per method beyond base_url, taken from the protocol's own
    # signature so this does not encode a second, drifting copy of the surface.
    import inspect

    for name in protocol_methods:
        assert hasattr(d, name), f"RunnerDispatch does not forward {name}()"
        sig = inspect.signature(getattr(harness_mod.WorkerHarness, name))
        params = [
            p for p in sig.parameters.values()
            if p.name not in ("self",)
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and p.default is p.empty
        ]
        args = ["http://w1:8090"] + ["x"] * (len(params) - 1)
        await getattr(d, name)(*args)

    assert calls == protocol_methods, (
        "every protocol method must reach the implementation the registry names; "
        f"forwarded {calls}, expected {protocol_methods}"
    )
