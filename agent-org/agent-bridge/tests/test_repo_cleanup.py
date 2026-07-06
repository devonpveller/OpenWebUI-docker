"""Repo cleanup inlets (live 2026-07-06: "remove all pull requests … delete branches X, Y, Z …
The remaining branch W is the most current" → the PM mapped it to `archive` and replied "Nothing
to archive" — an empty promise; neither bulk PR close nor branch deletion existed).

- Bulk PR close: "remove all pull requests" sweeps every open agent PR (reversible).
- Branch deletion: IRREVERSIBLE — fires only on explicitly NAMED `agent/*` branches inside a
  delete-verb sentence (the operator's words are the §3 clearance). Sentence scoping protects the
  keep-branch named one sentence later; unknown names get a closest-match suggestion (the live
  prompt contained two typos)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

LIVE_CLEANUP = (
    "remove all pull requests, none are ready to move forward. Please delete branches "
    "agent/effort-fix-murder-submodule-restore, agent/effort-fix-monogame-engine-errors, "
    "agent/effort-fix-murder-gameonexiting-error, none of these branches have had a fix. "
    "The remaining branch agent/effort-murer-errors is the most current."
)

ENGINE_BRANCHES = ["agent/effort-fix-murder-submodule-restore",
                   "agent/effort-fix-monogame-engine-errors",
                   "agent/effort-fix-murder-game-onexiting-error",
                   "agent/effort-fix-murder-errors"]


async def _orch(db_url, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, db


def _remote(state: dict):
    """Engine repo with 4 agent branches + 2 open agent PRs; murder repo with 1 open PR.
    Records deletions and closures into `state`."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/git/refs/heads/" in p and request.method == "DELETE":
            state.setdefault("deleted", []).append(p.split("/git/refs/heads/")[-1])
            return httpx.Response(204)
        if p.endswith("/branches") and request.method == "GET":
            names = ENGINE_BRANCHES if "/Engine/" in p or "MonoGame-Engine" in p else \
                ["agent/effort-fix-murder-errors", "main"]
            return httpx.Response(200, json=[{"name": n} for n in names])
        if p.endswith("/pulls") and request.method == "GET":
            if "murder" in p:
                return httpx.Response(200, json=[
                    {"number": 3, "head": {"ref": "agent/effort-fix-murder-errors"}, "title": "t"}])
            return httpx.Response(200, json=[
                {"number": 4, "head": {"ref": "agent/effort-fix-murder-game-onexiting-error"}, "title": "t"},
                {"number": 5, "head": {"ref": "agent/effort-fix-murder-errors"}, "title": "t"}])
        if "/pulls/" in p and "/files" in p:
            return httpx.Response(200, json=[])
        if "/pulls/" in p and request.method == "PATCH":
            state.setdefault("closed", []).append(p.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"state": "closed"})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_live_cleanup_prompt_closes_prs_and_deletes_named_branches(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        state: dict = {}
        orch._gh_transport = _remote(state)
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake(LIVE_CLEANUP, mgmt, thread_id="t")
        # ONE message does BOTH: all open agent PRs swept AND the named branches deleted
        assert sorted(state.get("closed", [])) == ["3", "4", "5"]
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "PR sweep" in msgs
        deleted = state.get("deleted", [])
        assert "agent/effort-fix-murder-submodule-restore" in deleted
        assert "agent/effort-fix-monogame-engine-errors" in deleted
        # the KEEP branch (typo'd in a non-delete sentence) was never touched
        assert not any("murer" in d or d == "agent/effort-fix-murder-errors" for d in deleted)
        msgs = " ".join(p["message"] for p in chat.posted)
        # the typo'd delete target got a closest-match suggestion, not a silent skip
        assert "did you mean `agent/effort-fix-murder-game-onexiting-error`" in msgs
    finally:
        await db.dispose()


async def test_delete_never_fires_without_explicit_agent_branch_names(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        state: dict = {}
        orch._gh_transport = _remote(state)
        mgmt = await orch.mgmt_channel_id()
        # vague hygiene talk → NOT the deterministic delete path (falls through to the model)
        handled = await orch._nl_branch_delete("please clean up the old branches", mgmt, "t")
        assert handled is False
        assert not state.get("deleted")
    finally:
        await db.dispose()


async def test_delete_refuses_non_agent_branches(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        state: dict = {}
        orch._gh_transport = _remote(state)
        mgmt = await orch.mgmt_channel_id()
        # `main` is not an agent/ ref — the extraction simply never matches it
        handled = await orch._nl_branch_delete("delete branch main", mgmt, "t")
        assert handled is False and not state.get("deleted")
    finally:
        await db.dispose()


# ── env-template egress + check_cmd derivation (operator principle: fixes are ABSTRACTS) ──
async def test_active_env_template_widens_egress_to_its_registries(db_url, tmp_path):
    """Activating a toolchain template (AO_OT*_IMAGE) IS the clearance: the bridge derives the
    template's package registries (envs.py) and widens the default-deny worker egress itself —
    no operator NL step."""
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        ot1_image="little-coder-open-terminal:dotnet8", ot2_image="",
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    try:
        allowlist = await orch.egress.render()
        # the render normalizes hosts to domain patterns — nuget.org coverage is the contract
        assert "nuget\.org" in allowlist, f"dotnet8's registry not derived into egress: {allowlist}"
    finally:
        await db.dispose()


async def test_error_report_repro_becomes_project_check_cmd(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/MonoGame-Engine")
        eid, chan, root = await orch.router.open_effort("fix-errs", project="monogame-engine")
        await orch._intake_or_dispatch(
            eid, chan, root,
            "when building the following errors occur:\n"
            "PS P:\_git\MonoGame-Engine> dotnet build vendor\murder\Murder.sln\n"
            "'Game.OnExiting(object, EventArgs)': no suitable method found to override",
            reply_prefix="", mgmt_channel=chan)
        p = await orch.projects.get("monogame-engine")
        assert p["check_cmd"] == "dotnet build vendor/murder/Murder.sln", \
            f"repro not adopted (got {p['check_cmd']!r})"
        msgs = " ".join(m["message"] for m in chat.posted)
        assert "Adopted your repro" in msgs and "red-gates" in msgs
        # a second error report NEVER overwrites an existing (possibly operator-set) check
        await orch.projects.set_check("monogame-engine", "dotnet test Custom.sln")
        eid2, chan2, root2 = await orch.router.open_effort("fix-more", project="monogame-engine")
        await orch._intake_or_dispatch(
            eid2, chan2, root2,
            "errors again:\nPS> dotnet build Something.Else.sln\nerror CS0000: nope",
            reply_prefix="", mgmt_channel=chan2)
        p = await orch.projects.get("monogame-engine")
        assert p["check_cmd"] == "dotnet test Custom.sln"
    finally:
        await db.dispose()
