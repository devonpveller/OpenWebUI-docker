"""HEADLESS RUNTIME SELF-VERIFICATION — the before/after reproduction harness (dark-factory keystone,
2026-07-13). The org proves a runtime-symptom fix is REAL, not a smoke test, by running the project's
own check at the pre-fix BASE and at the fix and requiring RED→GREEN: base must FAIL, head must PASS.
A check that's green at BOTH (a passive smoke launch — the atlas editor that never opens a Game
Profile) earns nothing. Only an org-observed red→green sets `_repro_red_green`, the honest basis for
'verified via reproduction'. Everything else fails closed. Fakes + mocked GitHub."""

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

BASE_SHA = "ba5e0000000000000000000000000000000000aa"
HEAD_SHA = "fee10000000000000000000000000000000000bb"


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
    return orch, orch.harness, db


def _remote(*, merge_base=BASE_SHA):
    """A remote that resolves the default branch + the merge-base of the effort branch."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/compare/" in p:
            body = {"ahead_by": 1, "files": [{"filename": "src/x.cs"}]}
            if merge_base:
                body["merge_base_commit"] = {"sha": merge_base}
            return httpx.Response(200, json=body)
        if "/branches/" in p:
            return httpx.Response(200, json={"commit": {"sha": HEAD_SHA}})
        if p.count("/") == 3:      # /repos/{owner}/{repo}
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _effort(orch, check_cmd="dotnet build App.sln"):
    await orch.projects.add("game", "https://github.com/devonpveller/Docker-Game")
    assert await orch.projects.set_check("game", check_cmd)
    return await orch.router.open_effort("wire", project="game")


async def test_harness_verifies_on_red_green(db_url, tmp_path):
    """base FAILS + head PASSES ⇒ RED→GREEN ⇒ _repro_red_green set to the head."""
    orch, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, _c, _r = await _effort(orch)
        orch._gh_transport = _remote()
        harness.check_queue = [(0, "…build logs…\nREPRO_BASE=1 REPRO_HEAD=0\n", False)]
        ok = await orch._org_reproduction_verified(eid, HEAD_SHA)
        assert ok is True
        assert orch._repro_red_green.get(eid) == HEAD_SHA
        ev = [e for e in await orch.audit.replay(eid) if e["kind"] == "delivery_repro_red_green"]
        assert ev and ev[-1]["payload"]["verified"] is True
    finally:
        await db.dispose()


async def test_harness_rejects_green_base_smoke_test(db_url, tmp_path):
    """A check GREEN at base (a smoke test that doesn't exercise the symptom — the atlas editor-launch)
    is NOT a reproduction ⇒ not verified, fail closed. THE atlas false-done, killed. The harness
    SHORT-CIRCUITS on a green base (head build skipped, `REPRO_HEAD=skip`) since head is moot."""
    orch, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, _c, _r = await _effort(orch)
        orch._gh_transport = _remote()
        harness.check_queue = [(0, "REPRO_BASE=0 REPRO_HEAD=skip\n", False)]   # green base ⇒ head skipped
        ok = await orch._org_reproduction_verified(eid, HEAD_SHA)
        assert ok is False
        assert orch._repro_red_green.get(eid) is None
    finally:
        await db.dispose()


async def test_harness_fails_closed_without_a_resolvable_base(db_url, tmp_path):
    """No merge-base (compare carries none) ⇒ can't prove red→green ⇒ fail closed, no build run."""
    orch, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, _c, _r = await _effort(orch)
        orch._gh_transport = _remote(merge_base="")
        harness.check_queue = [(0, "REPRO_BASE=1 REPRO_HEAD=0\n", False)]     # would verify IF run
        ok = await orch._org_reproduction_verified(eid, HEAD_SHA)
        assert ok is False
        assert orch._repro_red_green.get(eid) is None
        assert len(harness.checks) == 0, "no base ⇒ must not even run the build"
    finally:
        await db.dispose()


async def test_harness_fails_closed_on_infra_failure_at_base(db_url, tmp_path):
    """A base that fails for an INFRA reason (a broken check env, not the symptom) is not a genuine
    RED — never certify red→green off an environmental failure."""
    orch, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, _c, _r = await _effort(orch)
        orch._gh_transport = _remote()
        harness.check_queue = [
            (0, "error MSB1009: Project file does not exist.\nREPRO_BASE=1 REPRO_HEAD=0\n", False)]
        ok = await orch._org_reproduction_verified(eid, HEAD_SHA)
        assert ok is False       # base 'red' was infra, not the symptom
        assert orch._repro_red_green.get(eid) is None
    finally:
        await db.dispose()


async def test_harness_command_syncs_submodules_at_base_and_head(db_url, tmp_path):
    """The check runs at base and head, and between checkouts syncs each vendored submodule to ITS
    gitlink with a LOCAL checkout (since `git submodule update` is proxy-denied) — so a submodule-fix
    reproduces too, not only host-level fixes. Best-effort/fail-closed if a sync can't happen."""
    orch, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, _c, _r = await _effort(orch)
        orch._gh_transport = _remote()
        harness.check_queue = [(1, "REPRO_BASE=1 REPRO_HEAD=0", False)]
        await orch._org_reproduction_verified(eid, HEAD_SHA)
        cmd = harness.checks[-1]["command"]
        assert "csub" in cmd                                              # the submodule-sync helper
        assert f"git checkout -f {BASE_SHA}" in cmd and f"git checkout -f {HEAD_SHA}" in cmd
        assert f"csub {BASE_SHA}" in cmd and f"csub {HEAD_SHA}" in cmd    # synced at BOTH commits
        assert "REPRO_BASE=" in cmd and "REPRO_HEAD=" in cmd
        # MUST use a `cd` subshell, NOT `git -C <path>` — the git-proxy denies the `-C` global
        # (blocklist:global-override), which would silently no-op the submodule sync.
        assert "git -C " not in cmd
        assert "cd \"$pth\"" in cmd
        # base-first short-circuit: head is only rebuilt when base was RED (else skipped)
        assert '[ "$REPRO_BASE" = 1 ]' in cmd and "REPRO_HEAD=skip" in cmd
    finally:
        await db.dispose()


async def test_harness_fails_closed_when_base_equals_head(db_url, tmp_path):
    """Degenerate: merge-base == head (nothing forked) ⇒ no distinct pre-fix state ⇒ fail closed."""
    orch, harness, db = await _orch(db_url, tmp_path)
    try:
        eid, _c, _r = await _effort(orch)
        orch._gh_transport = _remote(merge_base=HEAD_SHA)
        harness.check_queue = [(0, "REPRO_BASE=1 REPRO_HEAD=0\n", False)]
        ok = await orch._org_reproduction_verified(eid, HEAD_SHA)
        assert ok is False
        assert len(harness.checks) == 0
    finally:
        await db.dispose()
