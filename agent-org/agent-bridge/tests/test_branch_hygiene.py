"""Branch hygiene (operator 2026-07-10: "there are so many branches. its a mess … it should
understand the branches were already merged previously and no longer need to be here"). Two fixes:
(1) the PM can REASON about a repo's agent/* branches by merge state and clean up the already-merged
ones on request (never the unmerged / live-PR ones) — a supporting action, not a me-action; (2) the
root cause — a merge only ever HINTED at leftovers, never deleted the merged head — is fixed: a merge
now auto-deletes its own branch. Fakes-only; a MockTransport stands in for the GitHub API."""

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


def _repo_transport(state: dict, *, branches: dict, open_heads: list,
                    dates: dict | None = None, pr_nums: dict | None = None):
    """`branches`: {name: ahead_by}. `open_heads`: branch names with an open PR. `dates`: {name: iso}
    last-commit date per branch (default a RECENT date). `pr_nums`: {name: pr#}. Records DELETE calls
    in state['deleted'] and closed PRs in state['closed_prs']."""
    state.setdefault("deleted", [])
    state.setdefault("closed_prs", [])
    dates = dates or {}
    pr_nums = pr_nums or {}
    _RECENT = "2026-07-12T00:00:00Z"

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        m = request.method
        if m == "DELETE" and "/git/refs/heads/" in p:
            state["deleted"].append(p.split("/git/refs/heads/", 1)[1])
            return httpx.Response(204)
        if m == "PATCH" and "/pulls/" in p:                     # close PR
            state["closed_prs"].append(int(p.rsplit("/", 1)[1]))
            return httpx.Response(200, json={"state": "closed"})
        if "/compare/" in p:
            head = p.split("...", 1)[1] if "..." in p else ""
            return httpx.Response(200, json={"ahead_by": branches.get(head, 0), "behind_by": 0})
        if "/branches/" in p and m == "GET":                    # single branch → carries commit date
            nm = p.split("/branches/", 1)[1]
            return httpx.Response(200, json={"name": nm, "commit": {
                "sha": "s", "commit": {"committer": {"date": dates.get(nm, _RECENT)}}}})
        if p.endswith("/branches"):
            return httpx.Response(200, json=[{"name": n} for n in branches])
        if p.endswith("/pulls"):
            return httpx.Response(200, json=[{"head": {"ref": h}, "number": pr_nums.get(h, 0)}
                                             for h in open_heads])
        if p.count("/") == 3:                                   # GET /repos/owner/name
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


_BRANCHES = {
    "agent/merged-a": 0, "agent/merged-b": 0,        # fully in main → safe
    "agent/unmerged": 2,                             # 2 commits not in main → keep
    "agent/live": 3,                                 # has an open PR → keep
}


async def test_report_lists_branches_by_merge_state_without_deleting(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        state: dict = {}
        orch._gh_transport = _repo_transport(state, branches=_BRANCHES, open_heads=["agent/live"])
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("which branches can be cleaned up?", mgmt, thread_id="t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "safe to delete" in msgs
        assert "agent/merged-a" in msgs and "agent/merged-b" in msgs
        assert "unmerged" in msgs and "agent/unmerged" in msgs      # flagged, not deleted
        assert "agent/live" in msgs                                 # live PR shown, kept
        assert state["deleted"] == []                              # a QUESTION deletes nothing
    finally:
        await db.dispose()


async def test_cleanup_deletes_only_the_merged_branches(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        state: dict = {}
        orch._gh_transport = _repo_transport(state, branches=_BRANCHES, open_heads=["agent/live"])
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("clean up the merged branches", mgmt, thread_id="t")
        assert set(state["deleted"]) == {"agent/merged-a", "agent/merged-b"}   # only the safe ones
        assert "agent/unmerged" not in state["deleted"]            # unmerged kept
        assert "agent/live" not in state["deleted"]                # live PR kept
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "deleted" in msgs.lower() and "kept" in msgs.lower()
    finally:
        await db.dispose()


async def test_explicit_branch_name_defers_to_named_delete(db_url, tmp_path):
    """A message that NAMES a branch is the explicit-delete path, not the general hygiene sweep."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        assert await orch._nl_branch_hygiene(
            "delete branch agent/foo", await orch.mgmt_channel_id(), "t") is False
    finally:
        await db.dispose()


async def test_non_branch_message_is_ignored(db_url, tmp_path):
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        assert await orch._nl_branch_hygiene(
            "how's the build going?", await orch.mgmt_channel_id(), "t") is False
    finally:
        await db.dispose()


async def test_merge_auto_deletes_its_own_branch(db_url, tmp_path):
    """Root-cause fix: a merge now deletes the merged head (its commits are in main) instead of
    letting agent/* branches pile up. Best-effort — never blocks the merge report."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        state: dict = {"deleted": []}

        def handler(request: httpx.Request) -> httpx.Response:
            p, m = request.url.path, request.method
            if m == "DELETE" and "/git/refs/heads/" in p:
                state["deleted"].append(p.split("/git/refs/heads/", 1)[1])
                return httpx.Response(204)
            if m == "PUT" and p.endswith("/merge"):
                return httpx.Response(200, json={"merged": True, "sha": "mergedsha",
                                                 "message": "Pull Request successfully merged"})
            if p.endswith("/pulls"):
                return httpx.Response(200, json=[])
            if p.count("/") == 3:
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(404)

        orch._gh_transport = httpx.MockTransport(handler)
        orch._pending_merge["merge-x"] = {
            "repo": "https://github.com/devonpveller/murder", "pr_number": 7,
            "effort_id": "effort-x", "branch": "agent/effort-x"}
        posted: list[str] = []

        async def _reply(m: str) -> None:
            posted.append(m)

        await orch._execute_merge("merge-x", _reply)
        assert "agent/effort-x" in state["deleted"]                # the merged branch is gone
        assert any("Cleaned up the merged branch" in m for m in posted)
    finally:
        await db.dispose()


# ── tidy up: close COMPLETED efforts (work merged) + clean their branches ─────
async def test_tidy_up_closes_completed_efforts_and_cleans_their_branches(db_url, tmp_path):
    """"tidy up" closes idle efforts whose branch is already merged into main (work landed → done)
    and deletes that branch — but keeps efforts whose work ISN'T merged yet (never loses work)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("app", "https://github.com/devonpveller/app")
        state: dict = {}
        # effort-done's branch is merged; effort-wip's branch is 2 commits ahead (not merged)
        orch._gh_transport = _repo_transport(
            state, branches={"agent/effort-done": 0, "agent/effort-wip": 2}, open_heads=[])
        d, _c, _r = await orch.router.open_effort("done", project="app")
        w, _c2, _r2 = await orch.router.open_effort("wip", project="app")
        await orch.charters.set_goal(d, "finished work", created_by="po")
        await orch.charters.set_goal(w, "unfinished work", created_by="po")
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("tidy up the board", mgmt, thread_id="t")
        from app.models import Effort
        async with orch.db.session_factory() as s:
            ed = await s.get(Effort, d)
            ew = await s.get(Effort, w)
        assert ed.lifecycle == "done"                              # completed → closed
        assert ew.lifecycle != "done"                             # unmerged work → kept
        assert "agent/effort-done" in state["deleted"]            # its merged branch cleaned
        assert "agent/effort-wip" not in state["deleted"]         # unmerged branch untouched
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Closed" in msgs and "effort-done" in msgs
    finally:
        await db.dispose()


async def test_branch_reaper_reaps_stale_superseded_and_closes_prs_keeps_current(db_url, tmp_path):
    """The org keeps its repos clean itself (operator 2026-07-12: "3 branches again, confusing; I
    don't know where to look"). The reaper deletes MERGED branches + STALE SUPERSEDED ones (older than
    the newest AND not touched recently) and CLOSES their open PRs — even a PR'd branch, since no human
    code lives on an agent branch. It KEEPS the newest (the current work) and any RECENTLY-touched
    branch (parallel live work)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)

        def _iso(**kw):
            return (now - _dt.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")

        state: dict = {}
        orch._gh_transport = _repo_transport(
            state,
            branches={
                "agent/current": 2,        # newest, unmerged, no PR → current work → keep
                "agent/old": 2,            # unmerged, stale, no PR → superseded → reap
                "agent/merged": 0,         # merged into main → reap
                "agent/pr-stale": 2,       # unmerged, stale, OPEN PR → reap + close PR
                "agent/pr-recent": 2,      # unmerged, RECENT, OPEN PR → keep (parallel live work)
            },
            open_heads=["agent/pr-stale", "agent/pr-recent"],
            pr_nums={"agent/pr-stale": 21, "agent/pr-recent": 22},
            dates={
                "agent/current": _iso(hours=1),
                "agent/old": _iso(days=10),
                "agent/merged": _iso(days=8),
                "agent/pr-stale": _iso(days=9),
                "agent/pr-recent": _iso(hours=3),
            })
        n = await orch._reap_abandoned_branches()
        assert set(state["deleted"]) == {"agent/merged", "agent/old", "agent/pr-stale"}
        assert "agent/current" not in state["deleted"]         # newest → current work → kept
        assert "agent/pr-recent" not in state["deleted"]       # recent → kept (parallel live work)
        assert state["closed_prs"] == [21]                     # the stale PR'd branch's PR was closed
        assert n == 3
    finally:
        await db.dispose()


async def test_internal_singletons_are_hidden_from_the_operator(db_url, tmp_path):
    """`__survey__` / `__capability__` are org plumbing — never shown as dispatchable/archivable
    efforts (operator 2026-07-10: they leaked into the effort list)."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        from app.models import Effort
        async with orch.db.session_factory() as s:
            s.add(Effort(id="__survey__", name="survey", channel_id="c"))
            s.add(Effort(id="effort-real", name="real", channel_id="c"))
            await s.commit()
        orch.models._client.queue_structured(
            __import__("app.schemas", fromlist=["OperatorIntent"]).OperatorIntent(
                kind="status", reply="Here's the board."))
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("what's running?", mgmt, thread_id="t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "__survey__" not in msgs and "__capability__" not in msgs
    finally:
        await db.dispose()


async def test_reaper_consolidates_efforts_behind_reaped_branches(db_url, tmp_path):
    """Reaping a branch also drops its effort out of the active board (operator 2026-07-11: "the
    latest change-containing branch is what we focus on; all others aren't progress"). A merged
    branch's effort → done; a superseded one's → aborted; the newest (kept) stays open."""
    orch, chat, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        from app.models import Effort
        async with orch.db.session_factory() as s:
            s.add(Effort(id="effort-old", name="old", channel_id="c",
                         created_at="2026-07-01T00:00:00+00:00", lifecycle="open"))
            s.add(Effort(id="effort-new", name="new", channel_id="c",
                         created_at="2026-07-10T00:00:00+00:00", lifecycle="open"))
            s.add(Effort(id="effort-merged", name="m", channel_id="c",
                         created_at="2026-07-05T00:00:00+00:00", lifecycle="open"))
            await s.commit()
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)

        def _iso(**kw):
            return (now - _dt.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")

        state: dict = {}
        orch._gh_transport = _repo_transport(state, branches={
            "agent/effort-old": 2, "agent/effort-new": 2, "agent/effort-merged": 0,
        }, open_heads=[], dates={
            "agent/effort-new": _iso(hours=1),      # newest → current work → kept
            "agent/effort-old": _iso(days=10),      # stale superseded → reaped → aborted
            "agent/effort-merged": _iso(days=8),    # merged → reaped → done
        })
        await orch._reap_abandoned_branches()
        async with orch.db.session_factory() as s:
            assert (await s.get(Effort, "effort-merged")).lifecycle == "done"     # merged → done
            assert (await s.get(Effort, "effort-old")).lifecycle == "aborted"     # superseded → aborted
            assert (await s.get(Effort, "effort-new")).lifecycle == "open"        # current work → kept
    finally:
        await db.dispose()
