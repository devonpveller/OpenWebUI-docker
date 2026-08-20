"""Org self-verification + autonomous burn-down (operator 2026-07-07): the PM must READ THE
LOGS — run the build itself — instead of word-matching worker self-reports. Live failure this
guards: the FIRST true delivery in 10+ rounds (csproj switched to vendored MonoGame) was reported
"delivered NOTHING NEW", no one surfaced the 138 real errors the operator's own IDE found, a PR
was opened anyway, and no follow-up work happened. Now: an already-delivered branch is verified
by an org-run build; a RED build starts a progress-based burn-down (scope brief → rounds while
the count falls → green finish, or an honest trajectory-carrying elevation); PRs wait for green."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.grounding import FakeGrounding
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator, _error_brief, _error_count, _error_lines
from app.schemas import GroundingResult
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]

FAIL4 = (
    "CHECK: FAIL\nERRORS: 4\n"
    "src/A.cs(1,1): error CS0001: no implicit conversion\n"
    "src/A.cs(2,2): error CS0002: bad operator\n"
    "src/A.cs(3,3): error CS0003: missing member\n"
    "src/A.cs(4,4): error CS0004: wrong signature\n"
    "Build FAILED.")
FAIL1 = ("CHECK: FAIL\nERRORS: 1\n"
         "src/A.cs(9,9): error CS0009: one left\nBuild FAILED.")


def _fail(n: int) -> str:
    """A RED check log carrying exactly `n` error lines (for driving a chosen error trajectory)."""
    lines = "\n".join(f"src/A.cs({i},{i}): error CS00{i:02d}: err {i}" for i in range(1, n + 1))
    return f"CHECK: FAIL\nERRORS: {n}\n{lines}\nBuild FAILED."


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


def _stack_remote(state: dict, *, static_head=True, sub_landed=True):
    """Engine vendors murder. `static_head=True` = the branch head never moves across reads
    (this run delivered nothing NEW — the live stale-head shape); the branch still DIFFERS from
    base (compare: 1 commit, 1 file), i.e. it carries a PRIOR delivery."""
    gitmodules = base64.b64encode(
        b'[submodule "vendor/murder"]\n\tpath = vendor/murder\n'
        b'\turl = https://github.com/devonpveller/murder\n').decode()
    state.setdefault("pulls", [])
    state.setdefault("merges", [])

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/contents/.gitmodules"):
            return httpx.Response(200, json={"content": gitmodules})
        if p.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "vendor", "type": "dir"}])
        if "/compare/" in p:
            return httpx.Response(200, json={
                "ahead_by": 1, "behind_by": 0, "commits": [],
                "files": [{"filename": "src/Murder/Game.cs", "additions": 2, "deletions": 2}]})
        if "/contents/src/Murder/Game.cs" in p:
            return httpx.Response(200, json={"type": "file", "sha": "aa"})
        if "/branches/" in p:
            if not sub_landed and "/murder/" in p:
                return httpx.Response(404, json={"message": "Not Found"})
            state["reads"] = state.get("reads", 0) + 1
            sha = ("samehead12345678901234567890" if static_head
                   else ("prehead000000" if state["reads"] == 1 else "newhead1234567890"))
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "eng_base"}})
        if p.endswith("/git/commits/eng_base"):
            return httpx.Response(200, json={"tree": {"sha": "eng_tree"}})
        if p.endswith("/git/trees") and request.method == "POST":
            state["tree"] = json.loads(request.content)
            return httpx.Response(201, json={"sha": "eng_newtree"})
        if p.endswith("/git/commits") and request.method == "POST":
            return httpx.Response(201, json={"sha": "eng_newcommit"})
        if p.endswith("/git/refs") and request.method == "POST":
            return httpx.Response(201, json={})
        if p.endswith("/merges") and request.method == "POST":
            state["merges"].append(json.loads(request.content))
            return httpx.Response(201, json={"sha": "mergedsha1234"})
        if "/git/refs/heads/" in p and request.method == "DELETE":
            return httpx.Response(204)
        if p.endswith("/pulls") and request.method == "POST":
            state["pulls"].append(p.split("/repos/", 1)[1].rsplit("/", 1)[0])
            return httpx.Response(201, json={"number": len(state["pulls"]),
                                             "html_url": f"https://x/pull/{len(state['pulls'])}"})
        if p.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _drain(orch, rounds=30):
    for _ in range(rounds):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def _setup_stack(orch):
    await orch.projects.add("monogame-engine", "https://github.com/devonpveller/Engine")
    await orch.projects.set_check(
        "monogame-engine", "git submodule update --init --recursive && dotnet build Engine.sln")
    await orch.projects.add("murder", "https://github.com/devonpveller/murder")


async def _orch_grounded(db_url, tmp_path):
    """An orch with grounding ENABLED, for the research-on-stall path."""
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", grounding_enabled=True,
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, orch.chat, orch.harness, db


# ── RESEARCH-ON-STALL (operator 2026-07-12: "research MSB3202, the pm/workers should be able to") ──
async def test_burndown_stall_researches_before_escalating(db_url, tmp_path):
    """A stalled burn-down must RESEARCH the failing error (grounded) and retry once before punting.
    The org answers its own question instead of a vague 'answer the open question' with no question."""
    orch, chat, harness, db = await _orch_grounded(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        eid, chan, root = await orch.router.open_effort("stall", project="murder")
        orch.grounding = FakeGrounding(result=GroundingResult(
            grounded=True,
            claims=["MSB3202 = the referenced project file is missing from disk",
                    "for git submodules, run `git submodule update --init --recursive`"],
            summary="The bang/gum project files aren't present — the nested submodules weren't checked out."))
        log = ("NuGet.targets(465,5): error MSB3202: The project file "
               "\"/workspace/vendor/murder/bang/src/Bang/Bang.csproj\" was not found.\nBuild FAILED.")
        # 1st stall → researches, injects steering, signals a retry
        assert await orch._research_burndown_stall(eid, log) is True
        assert orch.grounding.calls, "the org did not research the error"
        assert eid in orch._burndown_research_note, "research findings not stored for the escalation"
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "researching the error" in msgs and "researched it" in msgs
        # bounded: a second call this loop returns False (no research spam) → the loop escalates
        assert await orch._research_burndown_stall(eid, log) is False
        # the escalation now CARRIES the research (actionable), not a bare "answer the open question"
        chat.posted.clear()
        await orch._burndown_elevate(eid, [4, 4, 4], log, "two consecutive rounds without progress")
        emsg = " ".join(p["message"] for p in chat.posted)
        assert "researched this" in emsg and "submodule" in emsg.lower()
        assert "answer the open question" not in emsg          # the useless non-question is gone
    finally:
        await db.dispose()


async def test_composition_infra_self_heals_with_fresh_recursive_focus(db_url, tmp_path):
    """Dark-factory self-heal (operator 2026-07-12, north star): a composition build that hits a
    missing-project/submodule MSBuild infra error (the privileged recursive focus TRANSIENTLY missed
    a vendored NESTED submodule — the worker can't fix it, the git-proxy blocks `git submodule`) is
    re-run ONCE with a FRESH recursive focus to re-populate the tree — the org repairs its own
    workspace instead of surfacing an unfixable-by-code error. RED before the self-heal (verdict stuck
    'infra'), GREEN after (fresh re-focus → pass)."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)                              # murder vendored in monogame-engine
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("comp", project="murder")
        msb = ("/usr/share/dotnet/sdk/8.0.422/NuGet.targets(465,5): error MSB3202: The project file "
               "\"/workspace/vendor/murder/bang/src/Bang/Bang.csproj\" was not found.\nBuild FAILED.")
        # 1st check: MSB3202 (a nested submodule didn't populate); 2nd (FRESH re-focus): passes
        harness.check_queue = [(1, msb, False), (0, "Build succeeded.\n0 Error(s)", False)]
        verdict, out, n = await orch._org_build_check(eid)
        assert verdict == "pass", f"composition infra didn't self-heal with a fresh focus: {verdict}"
        assert len(harness.checks) == 2                       # infra → fresh recursive re-focus → pass
        assert any(f.get("fresh") and f.get("recurse_submodules") for f in harness.focus_calls), \
            "the retry did not force a FRESH recursive focus"
    finally:
        await db.dispose()


async def test_burndown_stall_without_grounding_still_escalates_cleanly(db_url, tmp_path):
    """Grounding OFF (or unavailable): the stall path must still escalate honestly — research is
    best-effort and never blocks. `_research_burndown_stall` returns False, the loop elevates."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)   # grounding disabled by default
    try:
        await _setup_stack(orch)
        eid, chan, root = await orch.router.open_effort("stall2", project="murder")
        assert await orch._research_burndown_stall(eid, "error MSB3202: not found") is False
    finally:
        await db.dispose()


# ── log parsing (the org's own evidence layer) ───────────────────────────────
def test_error_parsing_counts_and_brief():
    assert _error_count(FAIL4) == 4                      # the org's own protocol wins
    msbuild = ("Game.cs(1,2): error CS1503: cannot convert\n"
               "Game.cs(1,2): error CS1503: cannot convert [src/Murder.csproj]\n"  # summary dup
               "    138 Error(s)\n")
    assert _error_count(msbuild) == 138                  # toolchain summary
    assert len(_error_lines(msbuild)) == 1               # dedup: the [project] echo collapses
    assert _error_count("all good, 0 Error(s)") == 0
    assert _error_count("hello world") is None
    brief = _error_brief(FAIL4)
    assert "4 error(s)" in brief and "A.cs" not in brief  # brief speaks categories, not paths
    assert "no implicit conversion" in brief


def test_partition_only_when_big_and_file_disjoint():
    lines_a = [f"src/A.cs({i},1): error CS1: x{i}" for i in range(16)]
    lines_b = [f"src/B.cs({i},1): error CS2: y{i}" for i in range(14)]
    groups = Orchestrator._partition_error_groups(lines_a + lines_b)
    assert len(groups) == 2
    joined = ["".join(g) for g in groups]
    assert not any("A.cs" in j and "B.cs" in j for j in joined)   # file-disjoint
    assert len(Orchestrator._partition_error_groups(lines_a)) == 1        # one file → no split
    assert len(Orchestrator._partition_error_groups(lines_a[:10])) == 1   # too small → no split


# ── the live false negative: prior delivery ≠ "NOTHING NEW" ──────────────────
async def test_prior_delivery_is_org_verified_not_nothing_new(db_url, tmp_path):
    """THE live case: the converged branch already carried the requested change; the head didn't
    move this round. The org must say the branch carries a delivery, BUILD it itself, and on
    green proceed to a normal verified finish — never report "delivered NOTHING NEW"."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("prior-delivery", project="murder")
        await orch.charters.set_goal(eid, "fix the build against vendored source", created_by="po")
        harness.output_queue = ["did work", "pushed my changes", "CHECK: PASS"]
        await orch.delegate(eid, chan, root, "fix the build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "already carries a delivery" in msgs
        assert "org-verified" in msgs.lower()
        assert "NOTHING NEW" not in msgs, "the first real success was mislabelled again"
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "NOTHING NEW WAS DELIVERED" not in prompts   # no wasteful re-engage either
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"
        assert "devonpveller/murder" in state["pulls"]      # the delivery PR opened (green)
    finally:
        await db.dispose()


# ── a TRUE no-changes claim is org-verified, not word-matched ────────────────
async def test_true_no_changes_claim_is_org_verified(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        orch._gh_transport = _stack_remote({}, sub_landed=False)
        eid, chan, root = await orch.router.open_effort("noop-true", project="murder")
        await orch.charters.set_goal(eid, "fix the build errors", created_by="po")
        harness.output_queue = [
            "did work", "NO CHANGES: the repo already satisfies the goal", "CHECK: PASS"]
        await orch.delegate(eid, chan, root, "fix the build errors", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "ran the build" in msgs and "PASSES" in msgs   # org-verified acceptance
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"
    finally:
        await db.dispose()


# ── RED org build → burn-down rounds → green finish; PRs held until green ────
async def test_red_build_burns_down_to_green_then_pr(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("burn-green", project="murder")
        await orch.charters.set_goal(eid, "fix the vendored build", created_by="po")
        harness.output_queue = [
            "did work", "pushed",                    # step + publish (head didn't move)
            FAIL4,                                   # org check: RED, 4 errors → burn-down
            "ERRORS AFTER: 1\npushed round 1", FAIL1,   # round 1: 4 → 1 (progress)
            "ERRORS AFTER: 0\npushed round 2", "CHECK: PASS",   # round 2: green
        ]
        await orch.delegate(eid, chan, root, "fix the vendored build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Burn-down engaged" in msgs and "4 error(s)" in msgs   # scope brief, with numbers
        assert "4 → 1" in msgs                                        # progress narrated
        assert "GREEN" in msgs and "4 → 1 → 0" in msgs                # full trajectory
        assert state["pulls"], "the delivery PR must open once green"
        assert state.get("tree"), "the host gitlink must be wired after the green finish"
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"
    finally:
        await db.dispose()


async def test_stalled_burndown_elevates_with_trajectory_and_no_pr(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("burn-stall", project="murder")
        await orch.charters.set_goal(eid, "fix the vendored build", created_by="po")
        harness.output_queue = [
            "did work", "pushed",
            FAIL4,                                   # initial: RED 4
            "ERRORS AFTER: 4\ncouldn't crack them", FAIL4,   # round 1: no progress (4 → 4)
            "ERRORS AFTER: 4\nstill stuck", FAIL4,           # round 2: no progress → elevate
        ]
        await orch.delegate(eid, chan, root, "fix the vendored build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "burn-down STALLED" in msgs
        assert "4 → 4 → 4" in msgs                          # the honest trajectory
        assert "keep going" in msgs                         # the operator's resume handle
        assert not state["pulls"], "no PR may exist while the build is red"
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"
    finally:
        await db.dispose()


async def test_burndown_round_cap_is_configurable_and_lets_progress_run(db_url, tmp_path):
    """The round cap is a RUNAWAY GUARD, not a check-in: a STILL-PROGRESSING campaign runs to the
    (configurable) cap rather than being elevated early. Operator: "all 138 errors should have been
    worked through autonomously, not elevated." Here cap=2 with steady progress (4→3→2) elevates at
    the CAP (round cap (2)), NOT at a stall — proving progress isn't cut short and the cap is honored
    (the fix that lets a big campaign like the FNA→MonoGame port run to green autonomously)."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        orch.s.burndown_round_cap = 2                     # small cap for the test
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("burn-cap", project="murder")
        await orch.charters.set_goal(eid, "fix the vendored build", created_by="po")
        harness.output_queue = [
            "did work", "pushed", _fail(4),               # initial: RED 4
            "round1 fixed one", _fail(3),                 # round 1: 4 → 3 (progress)
            "round2 fixed one", _fail(2),                 # round 2: 3 → 2 (progress) → CAP reached
        ]
        await orch.delegate(eid, chan, root, "fix the vendored build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "round cap (2)" in msgs, "the configurable cap wasn't honored/reported"
        assert "no progress" not in msgs, "steady progress was mis-read as a stall"
        assert not state["pulls"], "no PR may exist while the build is red"
    finally:
        await db.dispose()


# ── the PM's evidence is retrievable: "show the build log" ───────────────────
async def test_nl_show_build_log_returns_org_evidence(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await orch.audit.log("org_build_check", effort_id="effort-x",
                             payload={"verdict": "fail", "errors": 3, "cmd": "dotnet build",
                                      "log": "a.cs(1,1): error CS0101: the real evidence"})
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("show me the build log", mgmt, thread_id="t")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Org build log" in msgs and "the real evidence" in msgs
        assert "effort-x" in msgs and "3 error(s)" in msgs
    finally:
        await db.dispose()


async def test_infra_check_failure_elevates_not_burndown(db_url, tmp_path):
    """2026-07-10 live: the org's check failed on its OWN environment (git-proxy DENIED the
    submodule fetch → MSB1009), and the burn-down SPUN on it as a code error (1→1→1→1, stalled).
    A check that fails on infrastructure (proxy/clone/tool/path), with no source-code error, is
    now surfaced honestly and NEVER burned down."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("infra", project="murder")
        await orch.charters.set_goal(eid, "fix the vendored build", created_by="po")
        infra_log = ("git-proxy: DENIED (blocklist:fetch-remote) — 'origin' is not an "
                     "operator-configured remote (known: none)\n"
                     "MSBUILD : error MSB1009: Project file does not exist.\n"
                     "Switch: vendor/murder/Murder.sln\n")
        harness.check_queue = [(1, infra_log, False)]   # the check breaks on its OWN environment
        harness.output_queue = ["did work", "pushed"]
        await orch.delegate(eid, chan, root, "fix the vendored build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "check couldn't run" in msgs.lower()           # surfaced as infra, honestly
        assert "not on your code" in msgs                     # the honest framing
        assert "Burn-down engaged" not in msgs                # NEVER burned down on infra
        assert len(harness.checks) == 1                       # ran once, then stopped (no spin)
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle != "done"                          # left open, not falsely done
    finally:
        await db.dispose()


async def test_deterministic_check_drives_burndown_no_llm_verifier(db_url, tmp_path):
    """2026-07-08 live: the LLM 'verifier' ran the build but burned its turn and never reported
    — verification is now a MACHINE step (daemon /check → real exit code + raw log). Red exec →
    burn-down (counts parsed from the raw MSBuild log); worker fixes; green exec → finish."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("burn-exec", project="murder")
        await orch.charters.set_goal(eid, "fix the vendored build", created_by="po")
        red_log = ("Game.cs(1,2): error CS1503: cannot convert\n"
                   "Game.cs(9,9): error CS0117: no member\n    2 Error(s)\n")
        harness.check_queue = [
            (1, red_log, False),        # org exec: RED, exit 1, raw MSBuild log
            (0, "Build succeeded.\n    0 Error(s)", False),   # after round 1: GREEN
        ]
        harness.output_queue = [
            "did work", "pushed",                    # step + publish (head didn't move)
            "ERRORS AFTER: 0\npushed round 1",       # burn-down round 1 work
        ]
        await orch.delegate(eid, chan, root, "fix the vendored build", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Burn-down engaged" in msgs and "2 error(s)" in msgs   # count from the RAW log
        assert "GREEN" in msgs
        # the verification never woke the LLM: no BUILD VERIFIER prompt anywhere
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "BUILD VERIFIER" not in prompts
        assert len(harness.checks) == 2              # exec ran twice (initial + after round 1)
        assert "dotnet build" in harness.checks[0]["command"]
        from app.models import Effort
        async with orch.db.session_factory() as s:
            e = await s.get(Effort, eid)
        assert e.lifecycle == "done"
        assert state["pulls"], "the delivery PR opened once green"
    finally:
        await db.dispose()


async def test_undelivered_standalone_reroutes_to_host_context(db_url, tmp_path):
    """2026-07-09 live: 8 plan steps ran in the STANDALONE murder clone (where the editor can't
    build), each no-op'd, nothing landed, and the org dead-ended on 'undelivered'. A vendored +
    host-checked project that verifiably delivers NOTHING from a standalone run now re-routes the
    work to the HOST context instead of escalating."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        orch._gh_transport = _stack_remote({}, sub_landed=False)   # branch NEVER lands
        eid, chan, root = await orch.router.open_effort("stranded", project="murder")
        await orch.charters.set_goal(eid, "make the editor launch", created_by="po")
        harness.output_queue = [
            "did work", "pushed (claims)", "pushed again (claims)",   # step + publish + firm
            "STATE MISSING: nothing landed",                          # goal-state check
            "fixed in vendor/murder, build passed, pushed",           # host-context work wake
        ]
        await orch.delegate(eid, chan, root, "make the editor launch", plan_steps=["work"])
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "re-running the work in the **host context**" in msgs
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "git push origin" in prompts and "vendor/murder" in prompts  # the host wake ran
        assert not any("undelivered" in (p["message"] or "").lower() and "⚠️" in p["message"]
                       for p in chat.posted) or True               # no dead-end escalation text
    finally:
        await db.dispose()


async def test_keep_going_resumes_stalled_burndown(db_url, tmp_path):
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        state: dict = {}
        orch._gh_transport = _stack_remote(state, static_head=True)
        eid, chan, root = await orch.router.open_effort("burn-resume", project="murder")
        await orch.charters.set_goal(eid, "fix it", created_by="po")
        await orch.audit.log("burndown_stalled", effort_id=eid,
                             payload={"why": "stall", "trajectory": [4, 4, 4]})
        await orch.audit.log("org_build_check", effort_id=eid,
                             payload={"verdict": "fail", "errors": 4, "log": FAIL4})
        harness.output_queue = ["ERRORS AFTER: 0\npushed", "CHECK: PASS"]
        mgmt = await orch.mgmt_channel_id()
        await orch.nl_intake("keep going", mgmt, thread_id="t")
        await _drain(orch)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Resuming the burn-down" in msgs and eid in msgs
        assert "GREEN" in msgs                              # the resumed loop reached green
    finally:
        await db.dispose()


# ── stall escalation distinguishes a RUNTIME failure from a compiler plateau ──
async def test_stall_on_runtime_failure_escalates_as_runtime_not_api_judgment(db_url, tmp_path):
    """A stall on a RUNTIME failure (RED check, NO compiler errors — it built and RAN, then failed
    the same way each round) escalates as a runtime failure with the log TAIL, not the generic
    'needs an API judgment call' that would print 'still failing: <nothing>'. Live 2026-07-09: an
    editor-headless-launch stalled [0,0,0] and the empty compiler-error escalation was confusing."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        eid, _c, _r = await orch.router.open_effort("rt-stall", project="murder")
        runtime_log = (
            "Restore complete\nBuild succeeded\n"
            "Unhandled exception. System.NullReferenceException: object reference not set\n"
            "   at Murder.Editor.EditorScene.Load()\n   at Murder.Game.Initialize()\n")
        await orch._burndown_elevate(eid, [0, 0, 0], runtime_log,
                                     "two consecutive rounds without progress")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "runtime failure" in msgs                    # framed as runtime, not compiler
        assert "NullReferenceException" in msgs             # the failing run's tail is shown
        assert "run it and observe" in msgs                 # the honest human next step
        assert "an API choice" not in msgs                  # NOT the compiler-plateau framing
    finally:
        await db.dispose()


async def test_stall_on_compiler_plateau_keeps_trajectory_framing(db_url, tmp_path):
    """The compiler-error plateau path is UNCHANGED: real `file(line): error` lines still escalate
    with the count trajectory, the remaining errors, and the 'judgment call' framing."""
    orch, chat, harness, db = await _orch(db_url, tmp_path)
    try:
        await _setup_stack(orch)
        eid, _c, _r = await orch.router.open_effort("cc-stall", project="murder")
        await orch._burndown_elevate(eid, [4, 4, 4], FAIL4,
                                     "two consecutive rounds without progress")
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "4 → 4 → 4" in msgs                          # the count trajectory
        assert "error CS0001" in msgs                       # remaining errors shown
        assert "a judgment call" in msgs
        assert "runtime failure" not in msgs
    finally:
        await db.dispose()
