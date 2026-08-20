"""EXACT reproduction of the live 2026-07-05 02:33 miss: the operator pasted a build-error list
("when building `murder` from project `MonoGame-Engine` the following errors occur. Fix the entire
list …") and the PO classifier returned junk TWICE → the bridge posted the generic "couldn't turn
that into something actionable" fallback and DROPPED the work. The fixes under test:
  1. junk-intent repair understands a registered project named ANYWHERE in the message (not just
     the `in <project>,` prefix) when a work verb / error paste marks it as a work request;
  2. pasted error walls are compacted (deduped + capped) for the SMALL-model classification call,
     while the FULL text stays in the effort goal for the worker;
  3. the honest fallback names the projects it did recognize, so the operator's rephrase is cheap.
Run RED against the pre-fix code as proof, GREEN after."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import OperatorIntent, ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

_ERR_STB = ("The type or namespace name 'StbImageSharp' could not be found (are you missing a "
            "using directive or an assembly reference?)")
_ERR_BANG_GEN = (r"Unable to find project 'P:\_git\MonoGame-Engine\vendor\murder\bang\src"
                 r"\Bang.Generator\Bang.Generator.csproj'. Check that the project reference is "
                 r"valid and that the project file exists.")
_ERR_BANG = (r"Unable to find project 'P:\_git\MonoGame-Engine\vendor\murder\bang\src\Bang"
             r"\Bang.csproj'. Check that the project reference is valid and that the project "
             r"file exists.")
_ERR_GUM = (r"Unable to find project 'P:\_git\MonoGame-Engine\vendor\murder\gum\src\Gum"
            r"\Gum.csproj'. Check that the project reference is valid and that the project file "
            r"exists.")

LIVE_MESSAGE = (
    "when building `murder` from project `MonoGame-Engine` the following errors occur. Fix the "
    "entire list and provide any additional steps i'd otherwise need to perform in my development "
    "environment if that is what is otherwise required. \n\nerror list:\n"
    + _ERR_STB + "\n"
    + _ERR_STB.replace("StbImageSharp", "StbImageWriteSharp") + "\n"
    + "\n".join([_ERR_BANG_GEN] * 4) + "\n"
    + "\n".join([_ERR_BANG] * 4) + "\n"
    + _ERR_GUM
)


async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


async def _add_live_projects(orch):
    await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
    await orch.projects.add("murder", "https://github.com/devonpveller/murder")
    await orch.projects.add("monogame", "https://github.com/devonpveller/MonoGame")


async def _drain(orch):
    for _ in range(12):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_live_repro_build_error_paste_junk_intent_still_dispatches(db_url):
    """The exact live prompt + the exact live model failure (junk intents) must open + dispatch a
    request on `monogame-engine` (the stated "from project …" build context) — never the generic
    rephrase fallback that dropped the work."""
    orch, chat, db = await _orch(db_url)
    try:
        await _add_live_projects(orch)
        # the live failure shape: two junk classifications in a row, then whatever the gate needs
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="..."))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(LIVE_MESSAGE, mgmt, thread_id="t")
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "couldn't turn that into something actionable" not in msgs, \
            "the work request fell into the rephrase fallback (the live bug)"
        assert len(orch.harness.wakes) >= 1, "the build-error fix was never dispatched"
        from app.models import Effort
        async with orch.db.session_factory() as s:
            rows = (await s.execute(__import__("sqlalchemy").select(Effort))).scalars().all()
        projects = {e.project for e in rows if e.id.startswith("effort-")}
        assert "monogame-engine" in projects, \
            f"expected the effort scoped to monogame-engine (the 'from project' build context), got {projects}"
    finally:
        await db.dispose()


async def test_full_error_list_reaches_the_goal_despite_compaction(db_url):
    """Compaction is CLASSIFICATION-ONLY: the effort goal (what the worker gets) must keep every
    distinct error verbatim, including the operator's dev-environment ask."""
    orch, chat, db = await _orch(db_url)
    try:
        await _add_live_projects(orch)
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(LIVE_MESSAGE, mgmt, thread_id="t")
        await _drain(orch)
        from app.models import GoalVersion
        async with orch.db.session_factory() as s:
            goals = (await s.execute(__import__("sqlalchemy").select(GoalVersion))).scalars().all()
        objective = " ".join(g.objective for g in goals)
        for must in ("StbImageSharp", "StbImageWriteSharp", "Bang.Generator.csproj", "Bang.csproj",
                     "Gum.csproj", "development environment"):
            assert must in objective, f"goal lost {must!r} — the worker wouldn't see the real task"
    finally:
        await db.dispose()


async def test_error_paste_is_compacted_for_the_classifier_call(db_url):
    """The PO classification prompt must NOT carry the repeated error lines 4× — degenerate
    repetition is what made the small model junk-misfire live."""
    orch, chat, db = await _orch(db_url)
    try:
        await _add_live_projects(orch)
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(LIVE_MESSAGE, mgmt, thread_id="t")
        await _drain(orch)
        po_calls = [c for c in orch.models._client.calls if c.get("kind") == "structured"]
        assert po_calls, "no classification call recorded"
        user = po_calls[0]["user"]
        assert user.count("Bang.Generator.csproj") == 1, \
            "repeated error lines were not deduped for the classifier"
        assert "repeated ×4" in user, "the dedupe must SAY the line repeated (information-preserving)"
    finally:
        await db.dispose()


async def test_project_mention_without_work_cue_is_not_forced(db_url):
    """Guard the guard: a junk-misfired message that merely MENTIONS a project (no work verb, no
    error paste) must still get the honest fallback — now with a hint naming what was recognized —
    and must NOT dispatch a phantom effort."""
    orch, chat, db = await _orch(db_url)
    try:
        await _add_live_projects(orch)
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="…"))
        orch.models._client.queue_structured(OperatorIntent(kind="chitchat", reply="..."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("so about murder generally speaking hmm", mgmt, thread_id="t")
        await _drain(orch)
        assert len(orch.harness.wakes) == 0, "a phantom effort was dispatched from a non-work message"
        msgs = [p["message"] for p in chat.posted]
        fallback = [m for m in msgs if "couldn't turn that into something actionable" in m]
        assert fallback, "expected the honest rephrase fallback"
        assert any("`murder`" in m for m in fallback), \
            "the fallback should name the project it recognized (cheap rephrase for the operator)"
    finally:
        await db.dispose()


def test_compact_paste_dedupes_and_caps():
    from app.orchestrator import _compact_paste
    text = "fix these:\n" + "\n".join(["Unable to find project 'X.csproj'."] * 6) + "\nend"
    out = _compact_paste(text, max_lines=40, max_chars=2500)
    assert out.count("Unable to find project") == 1
    assert "repeated ×6" in out
    assert out.startswith("fix these:") and out.rstrip().endswith("end")

    # short text passes through untouched
    short = "in murder, fix the build"
    assert _compact_paste(short) == short

    # hard cap: unique lines beyond the limit are dropped but ACCOUNTED for
    many = "\n".join(f"line-{i}" for i in range(120))
    out = _compact_paste(many, max_lines=40, max_chars=2500)
    assert out.count("\n") <= 41
    assert "more line" in out
