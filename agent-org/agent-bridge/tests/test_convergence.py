"""Error-report CONVERGENCE (live 2026-07-05: the operator pasted the same `Game.OnExiting` build
error after every attempt — four deliveries, zero resolution). Three generic mechanisms make the
series converge through the org instead of through the operator's patience:

1. REQUIRED VERIFICATION — an error-report goal demands reproduce → fix → re-run → confirm the
   pasted errors are GONE before publishing (nobody but the operator had ever run the build).
2. PRIOR ATTEMPTS — a re-reported error carries the earlier efforts' branches + verified outcomes
   into the goal, so the next worker builds on (or consciously diverges from) what exists.
3. AUTO-WIRING — an intake-born delivery on a VENDORED project bumps the host's gitlink to the
   verified commit + opens the paired wiring PR (planner-path parity): the fix cannot reach the
   host build otherwise. Plan-owned efforts are excluded (no double-bump)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import ReadinessVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

ERR = "'Game.OnExiting(object, EventArgs)': no suitable method found to override"


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
    return orch, orch.chat, orch.harness, db


def _stack_remote(bumped: dict):
    """murder (vendored) + Engine (host) remotes: healthy branches/compares, engine .gitmodules
    vendoring murder, and the Git Data API for the gitlink bump (recorded into `bumped`)."""
    gitmodules = base64.b64encode(
        b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n'
        b'\turl = https://github.com/devonpveller/murder\n').decode()

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/contents/.gitmodules"):
            return httpx.Response(200, json={"content": gitmodules})
        if p.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 2, "behind_by": 0,
                "commits": [{"commit": {"message": "fix override"}}],
                "files": [{"filename": "src/Murder/Game.cs", "additions": 3, "deletions": 3}]})
        if "/contents/src/Murder/Game.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            bumped["branch_reads"] = bumped.get("branch_reads", 0) + 1
            sha = ("prehead0000000000" if bumped["branch_reads"] == 1
                   else "abc123def456789000000000")   # head moves after the pre-dispatch read
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "eng_base"}})
        if p.endswith("/git/commits/eng_base"):
            return httpx.Response(200, json={"tree": {"sha": "eng_tree"}})
        if p.endswith("/git/trees") and request.method == "POST":
            bumped["tree"] = json.loads(request.content)
            return httpx.Response(201, json={"sha": "eng_newtree"})
        if p.endswith("/git/commits") and request.method == "POST":
            return httpx.Response(201, json={"sha": "eng_newcommit"})
        if p.endswith("/git/refs") and request.method == "POST":
            bumped["ref"] = json.loads(request.content)
            return httpx.Response(201, json={})
        if p.endswith("/pulls") and request.method == "POST":
            n = 7 if "/murder/" in p else 8
            return httpx.Response(201, json={"number": n, "html_url": f"https://x/pull/{n}"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_error_report_goal_gets_verification_and_attempt_history(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        # a PRIOR attempt at the same error, with a delivered (unmerged) branch on the remote
        prior, chan0, _r0 = await orch.router.open_effort("fix-murder-build-errors",
                                                          project="murder")
        await orch.charters.set_goal(prior, f"when building Murder.sln:\n{ERR}\nfix it",
                                     created_by="po")
        # the RE-REPORT: same pasted error, fresh effort
        eid, chan, root = await orch.router.open_effort("fix-onexiting-again", project="murder")
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        await orch._intake_or_dispatch(
            eid, chan, root, f"same errors again:\n{ERR}\nMetadata file 'x' could not be found",
            reply_prefix="", mgmt_channel=chan)
        _, goal, _ = await orch.charters.current_goal(eid)
        assert "REQUIRED VERIFICATION" in goal, "no repro→fix→re-verify contract in the goal"
        assert "PRIOR ATTEMPTS" in goal, "the re-report carries no attempt history"
        assert f"agent/{prior}" in goal and "UNMERGED" in goal, \
            "history must name the prior branch + its verified outcome"
        assert "BUILD ON IT" in goal
    finally:
        await db.dispose()


async def test_intake_delivery_on_vendored_project_auto_wires_host(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("fix-sig", project="murder")
        await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        assert bumped.get("tree"), "the host gitlink was never bumped"
        assert bumped["tree"]["tree"][0]["path"] == "vendor/murder"
        assert bumped["tree"]["tree"][0]["sha"] == "abc123def456789000000000"
        assert bumped["ref"]["ref"] == f"refs/heads/agent/{eid}"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Wiring half" in msgs and "Merge BOTH halves" in msgs
    finally:
        await db.dispose()


async def test_plan_owned_effort_is_not_double_wired(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("plan-owned", project="murder")
        orch._composition_managed.add(eid)          # a lifecycle plan owns the wiring
        try:
            await orch.delegate(eid, chan, root, "fix the override", plan_steps=["work"])
        finally:
            orch._composition_managed.discard(eid)
        assert not bumped.get("tree"), "intake auto-wiring must not double-bump a plan-owned effort"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Wiring half" not in msgs
    finally:
        await db.dispose()


# ── LIVE 2026-07-06: partial fix closed "done" + operator didn't know what to do with it ──
async def test_partial_error_verdicts_close_partly_done_and_stay_open(db_url, tmp_path):
    """A landed delivery on an error-report goal whose worker marks errors NOT RESOLVED must
    close as PARTIAL (needs-attention, lifecycle stays open) — never an unqualified done+merge."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("partial-fix", project="engine")
        goal = ("errors:\nerror CS0115 OnExiting no suitable method\n"
                "REQUIRED VERIFICATION: reproduce, fix, re-verify.")
        harness.output_queue = [
            "did the work. Summary follows.\nERROR VERDICTS:\n"
            "- StbImageSharp missing: RESOLVED (built DesktopGL)\n"
            "- CS0115 OnExiting: NOT RESOLVED (lives in vendor/murder)",
            "published-ack",
        ]
        await orch.delegate(eid, chan, root, goal, plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Partial fix" in msgs and "NOT RESOLVED" in msgs
        assert "partly done" in msgs
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done", "a partial fix silently closed the operator's report"
    finally:
        await db.dispose()


async def test_landed_closure_includes_local_apply_steps(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("engine", "dotnet build vendor/murder/Murder.sln")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("apply-steps", project="engine")
        await orch.delegate(eid, chan, root, "do the thing", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Try it locally before merging" in msgs
        assert f"git checkout agent/{eid}" in msgs
        assert "git submodule update --init --recursive" in msgs
        assert "dotnet build vendor/murder/Murder.sln" in msgs   # the project's own check
    finally:
        await db.dispose()


async def test_reopening_closed_effort_posts_channel_pointer(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("engine", "https://github.com/devonpveller/Engine")
        eid, chan, root = await orch.router.open_effort("old-thread", project="engine")
        await orch.gate.set_lifecycle(eid, "done")
        await orch.router.open_effort("old-thread", project="engine")   # the re-report reuse
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "reopened" in msgs and "original thread" in msgs
    finally:
        await db.dispose()


# ── LIVE 2026-07-06 iteration 7: an uncompiled fix shipped ambiguity errors — the machine
# must be the compiler in the loop, and a vendored project only compiles via its HOST build ──
async def test_composition_check_pass_reported_in_closure(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("sig-fix", project="murder")
        harness.output_queue = ["did the work", "published", "CHECK: PASS"]
        await orch.delegate(eid, chan, root, "fix the signature", plan_steps=["work"])
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Composition check passed" in msgs and "the wiring branch builds" in msgs
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        # the worker runs ONLY the build — the recursive submodule init rode the privileged focus,
        # not the proxy-blocked worker git.
        assert "dotnet build vendor/murder/Murder.sln" in prompts
        assert "BUILD VERIFIER" in prompts and "change NOTHING" in prompts
        # the verification build runs in a FRESH isolated session (never the work session — a
        # reused session made the agent no-op, live 2026-07-08)
        assert any("~vfy" in (w.get("session_id") or "") for w in harness.wakes)
        assert any(f.get("recurse_submodules") for f in harness.focus_calls), \
            "the composition-check focus must request a recursive submodule clone"
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"
    finally:
        await db.dispose()


async def test_composition_check_red_blocks_done_and_stays_open(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        bumped: dict = {}
        orch._gh_transport = _stack_remote(bumped)
        eid, chan, root = await orch.router.open_effort("bad-fix", project="murder")
        harness.output_queue = [
            "did the work", "published",
            "CHECK: FAIL\nerror CS0104: 'Point' is an ambiguous reference",
            # the burn-down's round-1 worker states a real constraint → clean elevation ends it
            "BLOCKED: the ambiguity fix needs an API decision\nNEEDS: guidance\nFEASIBLE: unknown",
        ]
        await orch.delegate(eid, chan, root, "fix the signature", plan_steps=["work"])
        await _drain_bg(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Composition check FAILED" in msgs
        assert "ambiguous reference" in msgs                     # the failing tail is shown
        # PR STAGING (operator 2026-07-07): a red build means NO PR and an explicit not-done —
        # the burn-down owns the follow-up, not the operator.
        assert "no PR opened" in msgs and "Burn-down" in msgs
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done", "a red composition check still closed the effort"
    finally:
        await db.dispose()


async def test_goal_carries_machine_check_forewarning(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        from app.schemas import ReadinessVerdict
        orch.models._client.queue_structured(
            ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
        eid, chan, root = await orch.router.open_effort("warned", project="murder")
        await orch._intake_or_dispatch(eid, chan, root, "fix the signature in Game.cs",
                                       reply_prefix="", mgmt_channel=chan)
        _, goal, _ = await orch.charters.current_goal(eid)
        # murder has no check of its own — the HOST's check is the one that applies
        assert "MACHINE CHECK" in goal and "dotnet build vendor/murder/Murder.sln" in goal
        assert "`monogame-engine`" in goal
    finally:
        await db.dispose()


# ── LIVE 2026-07-07 (operator): "having to say re-run it while the pm knows there's an
# issue — it should just re-run itself with a relevant evolutionary prompt" ──
def _iterating_remote(bumped: dict):
    """Like _stack_remote but the branch head MOVES on every read (each iteration's push),
    so neither the stale-head gate nor the mock trips a legitimate multi-round run."""
    import base64 as _b64
    gitmodules = _b64.b64encode(
        b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n'
        b'\turl = https://github.com/devonpveller/murder\n').decode()

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/contents/.gitmodules"):
            return httpx.Response(200, json={"content": gitmodules})
        if p.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "src/Murder/Game.cs", "additions": 1, "deletions": 1}]})
        if "/contents/src/Murder/Game.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            bumped["reads"] = bumped.get("reads", 0) + 1
            return httpx.Response(200, json={"commit": {"sha": f"head{bumped['reads']:02d}" + "0" * 18}})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "eng_base"}})
        if p.endswith("/git/commits/eng_base"):
            return httpx.Response(200, json={"tree": {"sha": "eng_tree"}})
        if p.endswith("/git/trees") and request.method == "POST":
            return httpx.Response(201, json={"sha": "eng_newtree"})
        if p.endswith("/git/commits") and request.method == "POST":
            return httpx.Response(201, json={"sha": "eng_newcommit"})
        if p.endswith("/git/refs") and request.method == "POST":
            return httpx.Response(201, json={})
        if "/git/refs/heads/" in p and request.method == "PATCH":
            return httpx.Response(200, json={})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://x/pull/9"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _drain_bg(orch):
    import asyncio
    for _ in range(20):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def test_red_composition_check_auto_iterates_to_green(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _iterating_remote({})
        eid, chan, root = await orch.router.open_effort("iterate-me", project="murder")
        await orch.charters.set_goal(eid, "fix the ambiguity errors", created_by="po")
        harness.output_queue = [
            "did the work", "published", "CHECK: FAIL\nerror CS0104: ambiguous 'Point'",
            "ERRORS AFTER: 0\nfixed the usings, pushed", "CHECK: PASS",
        ]
        await orch.delegate(eid, chan, root, "fix the ambiguity errors", plan_steps=["work"])
        await _drain_bg(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        # the red check hands off to the BURN-DOWN (progress-based, org-verified every round),
        # not a fixed retry count — the PM keeps working instead of asking the operator
        assert "Burn-down engaged" in msgs, "the PM asked the operator instead of iterating"
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "BURN-DOWN ROUND 1" in prompts
        assert "CS0104" in prompts                     # the round's prompt carries the real red
        assert "GREEN" in msgs                         # round 1 went green, org-verified
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"
    finally:
        await db.dispose()


async def test_auto_iteration_is_bounded(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _iterating_remote({})
        eid, chan, root = await orch.router.open_effort("hopeless", project="murder")
        await orch.charters.set_goal(eid, "fix it", created_by="po")
        red = "CHECK: FAIL\nsrc/A.cs(1,1): error CS0001: still broken"
        harness.output_queue = [
            "did work", "published", red,              # red → burn-down engaged
            "ERRORS AFTER: 1\nno luck", red,           # round 1: 1 → 1, no progress
            "ERRORS AFTER: 1\nstill stuck", red,       # round 2: no progress → honest stop
        ]
        await orch.delegate(eid, chan, root, "fix it", plan_steps=["work"])
        await _drain_bg(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        # bounded autonomy is now PROGRESS-based: two rounds without improvement → an honest,
        # trajectory-carrying hand-back (never an infinite loop, never a silent stop)
        assert "burn-down STALLED" in msgs
        assert "1 → 1 → 1" in msgs                     # the evidence: org-run builds each round
        assert len(harness.wakes) <= 8                 # bounded — no runaway dispatching
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"
    finally:
        await db.dispose()


def test_build_segment_strips_git_setup():
    from app.orchestrator import Orchestrator as O
    assert O._build_segment(
        "git submodule sync --recursive && git submodule update --init --recursive && "
        "dotnet build vendor/murder/Murder.sln") == "dotnet build vendor/murder/Murder.sln"
    # no git prefix → unchanged
    assert O._build_segment("dotnet build X.sln") == "dotnet build X.sln"
    # git AFTER the build is kept (only LEADING git-setup is dropped)
    assert O._build_segment("npm ci && npm test") == "npm ci && npm test"
    assert O._build_segment("git fetch && git checkout b && make") == "make"


# ── LIVE 2026-07-07: a fix request dodged the whole check stack via a FALSE "NO CHANGES" ──
async def test_no_changes_on_fix_request_without_build_proof_auto_iterates(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        eid, chan, root = await orch.router.open_effort("cop-out", project="murder")
        await orch.charters.set_goal(
            eid, "fix the build.\nREQUIRED VERIFICATION: reproduce, fix, re-verify.",
            created_by="po")
        # worker claims no-changes with NO build evidence → must be rejected + auto-iterated
        harness.output_queue = ["did work", "NO CHANGES: vendored ref already in place"]
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["work"])
        await _drain_bg(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Auto-iteration" in msgs, "a false no-op closed the fix request as done"
        assert "finished (**done**)" not in msgs
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"
    finally:
        await db.dispose()


async def test_no_changes_with_build_proof_is_accepted(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
        await orch.projects.set_check("monogame-engine", "dotnet build vendor/murder/Murder.sln")
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        eid, chan, root = await orch.router.open_effort("legit-noop", project="murder")
        await orch.charters.set_goal(
            eid, "fix the build.\nREQUIRED VERIFICATION: reproduce, fix, re-verify.",
            created_by="po")
        harness.output_queue = [
            "did work",
            "NO CHANGES: already fixed on main — ran `dotnet build`, Build succeeded, 0 error(s)"]
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["work"])
        await _drain_bg(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs        # build-proven no-op is legitimate
        assert "Auto-iteration" not in msgs
    finally:
        await db.dispose()


async def test_no_changes_on_pure_investigation_still_accepted(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("murder", "https://github.com/devonpveller/murder")
        orch._gh_transport = _stack_remote({})
        eid, chan, root = await orch.router.open_effort("investigate", project="murder")
        # a read-only goal (no REQUIRED VERIFICATION, no project check) → NO CHANGES is fine
        await orch.charters.set_goal(eid, "investigate the repo structure; read-only",
                                     created_by="po")
        harness.output_queue = ["looked around", "NO CHANGES: read-only investigation, here's what I found"]
        await orch.delegate(eid, chan, root, "investigate", plan_steps=["work"])
        await _drain_bg(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "finished (**done**)" in msgs and "Auto-iteration" not in msgs
    finally:
        await db.dispose()
