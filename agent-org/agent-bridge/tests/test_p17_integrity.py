"""P17 — plan-gate, sweep and delivery integrity (evidence: gym-015, 2026-07-20).

Each test is one observed gym-015 failure. The full evidence for every finding — event ids,
git output, reproduced commands — is in docs/P17-plan-gate-and-sweep-integrity.md; the
docstrings here carry the short form so a failure explains itself.

The organising principle behind the fixes (ORCHESTRATION-DESIGN §11, "every boundary in the
system is an executable contract"): a gate may not decide on another agent's prose when the
claim is mechanically checkable. Every component that failed in gym-015 was reading an
assertion; every component that held was executing something.

Fakes only.
"""

from __future__ import annotations

from app.models import ScopeTask
from app.orchestrator import _LENSES

from test_lenses import (  # noqa: F401 — shared fixtures/helpers, same fake stack
    GOAL, REPO, _REPORT, _delivery, _effort, _orch, _round, _shutdown,
)


# ── F3 — a partial sweep is not a sweep ───────────────────────────────────────
async def test_a_sweep_missing_goal_alignment_is_not_swept(db_url):
    """`swept = bool(reports)` was true when ANY lens reported. gym-015 rounds 1 and 5 (2 of 5)
    recorded `swept: true` on a 2-of-3 sweep whose missing lens was `goal_alignment` — the ONLY
    input to gap analysis. Those rounds never compared the product to the goal and emitted no
    `gap_analysis` event at all; with an empty queue they would have declared completion."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        # goal_alignment truncates to a narration stub; the other two produce real reports.
        harness.output_queue.append("All 51 tests pass. Now let me do thorough manual testing:")
        harness.output_queue.append(_REPORT)
        harness.output_queue.append(_REPORT)
        orch.models._client.queue_text("none")      # clean_code
        orch.models._client.queue_text("none")      # project_documentation
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["swept"] is False                  # 2 of 3 is NOT a sweep
        assert await orch._event_count(eid, "gap_analysis") == 0
        assert "goal_alignment" in r["note"]        # names what went missing
        assert "not** a clean sweep" in r["note"]
    finally:
        await _shutdown(orch, db)


async def test_a_sweep_keeps_working_when_a_style_lens_dies(db_url):
    """The converse — F3 must not over-correct into "all three or nothing". `goal_alignment` is
    load-bearing because gap analysis consumes it and nothing else; the style lenses are not."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        harness.output_queue.append(_REPORT)        # goal_alignment: real
        harness.output_queue.append("Looks fine.")  # clean_code: stub
        harness.output_queue.append(_REPORT)        # project_documentation: real
        orch.models._client.queue_text("none")      # gap analysis
        orch.models._client.queue_text("none")      # project_documentation
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["swept"] is True
        assert await orch._event_count(eid, "gap_analysis") == 1
    finally:
        await _shutdown(orch, db)


# ── F11 — an asserted absence the tree refutes is not work ────────────────────
async def test_a_task_asserting_an_absence_the_tree_refutes_is_dropped(db_url):
    """Three false absences in gym-015, across two agent roles: "no type annotations on function
    signatures" (17 of 17 defs were annotated, and it self-contradicted two sentences later);
    "there is no `__version__` variable defined anywhere" (todo.py:20); and the REVIEWER's
    "`os.makedirs` lacks explicit `exist_ok=True`" (it was there). The `__version__` one was
    dispatched, and the worker then burned its plan turn hex-dumping the file because the task's
    premise contradicted what was in front of it."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _grep(effort_id, *, command, session_id, **kw):
            return 0, "PRESENT __version__\nABSENT --archive", False

        orch.router.exec_check = _grep
        r = await _round(orch, harness, eid, chan, root,
                         "Add a `__version__` module constant\nAdd an `--archive` CLI flag")
        bodies = [t["body"] for t in r["open_tasks"]]
        assert not any("__version__" in b for b in bodies)   # refuted by the tree -> dropped
        assert any("--archive" in b for b in bodies)         # genuinely absent -> kept
        assert await orch._event_count(eid, "false_absence_rejected") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_fix_task_is_never_dropped_for_naming_something_that_exists(db_url):
    """The filter may only fire on an ASSERTED ABSENCE. "remove X" / "fix X" presuppose that X
    exists, so finding X present is confirmation, not refutation. Dropping those would delete real
    work — a worse failure than the one this fixes."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _grep(effort_id, *, command, session_id, **kw):
            return 0, "PRESENT cmd_repl", False

        orch.router.exec_check = _grep
        r = await _round(orch, harness, eid, chan, root,
                         "Remove the broad `cmd_repl` exception handler")
        assert len(r["open_tasks"]) == 1
        assert await orch._event_count(eid, "false_absence_rejected") == 0
    finally:
        await _shutdown(orch, db)


async def test_an_unverifiable_absence_is_kept_not_dropped(db_url):
    """Failure to CHECK is not refutation. If the probe cannot run, every task survives: the
    filter may only remove work it has positive evidence against."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _boom(effort_id, **kw):
            raise RuntimeError("worker unreachable")

        orch.router.exec_check = _boom
        r = await _round(orch, harness, eid, chan, root, "Add a `__version__` module constant")
        assert len(r["open_tasks"]) == 1
        assert await orch._event_count(eid, "false_absence_rejected") == 0
    finally:
        await _shutdown(orch, db)


# ── F12 — handed over is not done ─────────────────────────────────────────────
async def test_dispatch_closes_tasks_as_dispatched_not_done(db_url):
    """The drain closes its queue at HAND-OVER, before the implementer has run a single step, and
    recorded that as `done`. gym-015: a worker explicitly declined two out-of-scope tasks ("Not
    touched: outside data_layer scope") and both were already marked `done`; one of them (remove
    the broad `except Exception` in `cmd_repl`) is still not done on the delivered branch."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        harness.output_queue.append("plan")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        async with db.session_factory() as s:
            row = await s.get(ScopeTask, r1["open_tasks"][0]["id"])
            assert row.status == "dispatched"   # what the org actually knows
    finally:
        await _shutdown(orch, db)


async def test_a_dispatched_task_still_reopens_when_re_derived(db_url):
    """`dispatched` must keep every property `done` had for the loop's honesty: re-derivation by a
    later independent sweep reopens it, so unfinished work cannot hide behind a closed row."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        harness.output_queue.append("plan")
        await orch._drain_iterate(eid, r1["open_tasks"], r1["round"])
        assert await orch.list_open_tasks(effort_id=eid) == []
        r2 = await _round(orch, harness, eid, chan, root, "add a delete command")
        assert r2["new_tasks"] == 0             # not new information
        assert len(r2["open_tasks"]) == 1       # but still outstanding
    finally:
        await _shutdown(orch, db)


async def test_a_dropped_task_is_not_resurrected_by_re_derivation(db_url):
    """`dropped` is a deliberate decision that an item is not wanted. Re-deriving it must not
    silently overturn that — only `done`/`dispatched` reopen."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        r1 = await _round(orch, harness, eid, chan, root, "add a delete command")
        await orch.close_task(r1["open_tasks"][0]["id"], status="dropped")
        r2 = await _round(orch, harness, eid, chan, root, "add a delete command")
        assert r2["open_tasks"] == []
    finally:
        await _shutdown(orch, db)


# ── F16 — the plan must be observed, not recalled ─────────────────────────────
async def test_the_plan_turn_runs_in_its_own_session_not_the_builders(db_url):
    """The plan turn used `_session_for(effort_id)`: the very session that had just built the
    thing. gym-015: a 7-minute build turn was followed IN THE SAME SESSION by a 21-second "plan"
    replying "The work is already complete — all features implemented, all 51 tests passing".
    That is recall, not observation. ORCHESTRATION-DESIGN §11 mandates cleared context for exactly
    this reason ("a fresh reviewer isn't carrying the builder's rationalizations"), and P10.1
    applied it to the lenses; the plan step never got it."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        seen: list[str] = []
        orig = orch.router.wake

        async def _spy(effort_id, **kw):
            seen.append(kw.get("session_id") or "")
            return await orig(effort_id, **kw)

        orch.router.wake = _spy
        harness.output_queue.append(
            "UNDERSTANDING: build the CLI\n"
            "ALREADY DONE: nothing — todo.py does not exist (ls showed no such file)\n"
            "PLAN: 1. write todo.py with add/list/done\n"
            "WON'T DO: no new dependencies\nRISKS: none")
        orch.models._client.queue_text("ALIGNED")
        plan = await orch._worker_plan_gate(eid, chan, root, GOAL, REPO, None, None, None)
        base = await orch._session_for(eid)
        assert plan is not None                 # returns the PLAN TEXT, not a bool
        assert seen and seen[0] != base         # NOT the builder's session
        assert seen[0].endswith("~plan")
    finally:
        await _shutdown(orch, db)


async def test_the_approved_plan_is_carried_as_an_artifact(db_url):
    """F16's other half: execution used to be told "your plan (previous turn in this session)",
    which is precisely what forced the plan into the builder's session. Returning the text makes
    the plan an artifact, so the two steps can have different sessions at all."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        harness.output_queue.append(
            "UNDERSTANDING: add delete\n"
            "ALREADY DONE: add and list work (ran `python3 todo.py --help`)\n"
            "PLAN: 1. add cmd_delete to todo.py\n"
            "WON'T DO: no schema change\nRISKS: none")
        orch.models._client.queue_text("ALIGNED")
        plan = await orch._worker_plan_gate(eid, chan, root, GOAL, REPO, None, None, None)
        assert "cmd_delete" in plan             # the executor is handed the text itself
    finally:
        await _shutdown(orch, db)


async def test_the_plan_turn_is_told_it_has_no_memory(db_url):
    """A fresh session cuts both ways: a context-free worker will happily plan to build things
    that already exist (the mirror of the recall failure). The instruction must therefore make
    observation the first step and require "already done" to be a CITED observation."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        instr: dict[str, str] = {}
        orig = orch.router.wake

        async def _spy(effort_id, **kw):
            instr.setdefault("text", kw.get("instruction") or "")
            return await orig(effort_id, **kw)

        orch.router.wake = _spy
        harness.output_queue.append(
            "UNDERSTANDING: x\nALREADY DONE: nothing\nPLAN: 1. do x\nWON'T DO: y\nRISKS: none")
        orch.models._client.queue_text("ALIGNED")
        await orch._worker_plan_gate(eid, chan, root, GOAL, REPO, None, None, None)
        text = instr["text"]
        assert "NO memory of earlier turns" in text
        assert "ALREADY DONE:" in text
        assert "already be implemented" in text
    finally:
        await _shutdown(orch, db)


# ── F14 — a stop instruction is never silently dropped ────────────────────────
async def test_a_stop_instruction_that_misses_the_grammar_is_answered(db_url):
    """`POST /nl` with "Stop and abort effort-X. The gym diagnostic run is complete..." returned
    {"ok": true} and did NOTHING: `_CONTROL_RE` is anchored at `^` and the message opens with
    "Stop and", so it fell through to a model that took no action and logged no event. The effort
    ran another full drain round seven minutes later. The terse "archive <id>" worked — the more
    explicit, more human phrasing lost, which is the wrong way round for a stop."""
    orch, chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        chan = await orch.mgmt_channel_id() or "chan"
        await orch.nl_intake(
            f"Stop and abort {eid}. The diagnostic run is complete and I do not want any further "
            f"rounds, dispatches or pushes on it.",
            chan, user_id="operator-api")
        assert await orch._event_count(eid, "operator_intent_unmatched") == 1
        posts = " ".join(str(p.get("message", "")) for p in chat.posted)
        assert "have not stopped anything" in posts   # never a silent no-op
        assert f"abort {eid}" in posts                # hands over the exact command
    finally:
        await _shutdown(orch, db)


async def test_a_wellformed_abort_still_goes_straight_through(db_url):
    """The F14 guard must not intercept the grammar that already works — `abort <id>` is the
    documented command and stays deterministic."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        chan = await orch.mgmt_channel_id() or "chan"
        await orch.nl_intake(f"abort {eid}", chan, user_id="operator-api")
        # handled by the control grammar, so the "I didn't understand" path never fires
        assert await orch._event_count(eid, "operator_intent_unmatched") == 0
    finally:
        await _shutdown(orch, db)


# ── F9 — a child scope must be narrower than its parent ───────────────────────
async def test_a_child_scope_that_restates_its_parent_is_rejected(db_url):
    """gym-015 decomposed "Data persistence layer :: handles loading, saving, atomic file writes,
    and robust parsing" into a child "data_layer :: handles loading, saving, atomic file writes,
    database path configuration, and malformed data resilience" — a restatement, not a narrowing.
    The tier walk descended a level, narrowed nothing, and spent a round doing it."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        parent = await orch.add_scope_node(
            "gym", "Data persistence layer",
            "handles loading, saving, atomic file writes, and robust parsing of the database")
        # the model returns one paraphrase and one genuinely narrower part
        orch.models._client.queue_text(
            "data_layer :: handles loading saving atomic file writes and robust parsing of the "
            "database\n"
            "date filtering :: parses and compares due dates for the overdue summary")
        # two reports: `_maybe_decompose` needs >1200 chars of evidence before it will split
        kids = await orch._maybe_decompose(parent, [_REPORT, _REPORT], effort_id=eid,
                                           from_reports=True)
        # only one survivor -> below the 2-part floor -> the node stays atomic
        assert kids == []
        assert await orch._event_count(eid, "scope_child_rejected_not_narrower") == 1
        assert await orch._event_count(eid, "scope_decompose_declined") == 1
    finally:
        await _shutdown(orch, db)


# ── F13/F2 — a delivery must not orphan the head it already published ─────────
async def test_a_delivery_that_orphans_the_previous_head_is_caught(db_url):
    """gym-015 round 5 dispatched to a worker four commits stale. It committed on that base,
    producing `1b04400` whose parent is `0f375e0` rather than the head `1ed9da6` — dropping the
    quoting fix, the TypedDict, pyproject.toml, docs/architecture.md and four tests (55 -> 51).
    The suite passed at 51/51 and the base-sha provenance check PASSED, because base ancestry is
    not head currency."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.audit.log("effort_published", effort_id=eid,
                             payload={"branch": "agent/feat", "head_sha": "1ed9da6" + "0" * 33})
        calls = {}

        async def _not_ancestor(github, repo, anc, desc, **kw):
            calls["pair"] = (anc, desc)
            return False                      # diverged — the new head orphans the old

        import app.orchestrator as orch_mod
        orch_mod.sha_is_ancestor = _not_ancestor
        orch.github = object()                # any truthy app; the call is stubbed
        d = _delivery()
        d.verifiable = True          # the check is a no-op on an unverifiable remote, by design
        d.head_sha = "1b04400" + "0" * 33
        orphan = await orch._delivery_orphans_previous_head(eid, REPO, d)
        assert orphan == "1ed9da6" + "0" * 33
        assert calls["pair"][0].startswith("1ed9da6")
    finally:
        await _shutdown(orch, db)


async def test_an_unreadable_remote_is_never_called_an_orphan(db_url):
    """Fails OPEN. An unreachable API or a first delivery must never be reported as thrown-away
    work — the check may only ever fire on positive evidence of divergence."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.audit.log("effort_published", effort_id=eid,
                             payload={"branch": "agent/feat", "head_sha": "aaa" + "0" * 37})

        async def _unknown(github, repo, anc, desc, **kw):
            return None                       # couldn't tell

        import app.orchestrator as orch_mod
        orch_mod.sha_is_ancestor = _unknown
        orch.github = object()
        d = _delivery()
        d.verifiable = True
        d.head_sha = "bbb" + "0" * 37
        assert await orch._delivery_orphans_previous_head(eid, REPO, d) is None
    finally:
        await _shutdown(orch, db)


async def test_a_first_delivery_has_no_previous_head_to_orphan(db_url):
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        d = _delivery()
        d.verifiable = True
        d.head_sha = "ccc" + "0" * 37
        assert await orch._delivery_orphans_previous_head(eid, REPO, d) is None
    finally:
        await _shutdown(orch, db)


# ── F1/F15 — a plan turn leaves nothing on disk ───────────────────────────────
async def test_a_plan_turn_that_wrote_files_is_reverted(db_url):
    """`plan_only` gates the daemon's edit/write TOOLS, not the shell. gym-015 broke it twice by
    different routes: `sed -i` + `git commit` (33eae95), then a `python3 -c` read-modify-write +
    commit (1b04400). A command deny-list cannot close that — any interpreter is another route —
    so the workspace is checked and reverted before the gate judges the plan."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cmds: list[str] = []

        async def _exec(effort_id, *, command, session_id, **kw):
            cmds.append(command)
            if "PROBE-DONE" in command:
                return 0, "BEFORE-HEAD=abc123\n M todo.py\n?? scratch.py\nPROBE-DONE", False
            return 0, "REVERT-DONE", False

        orch.router.exec_check = _exec
        assert await orch._revert_plan_turn_writes(eid) is True
        assert await orch._event_count(eid, "plan_turn_wrote_files") == 1
        assert any("git checkout -- ." in c for c in cmds)
        assert any("git clean -fd" in c for c in cmds)
        assert not any("reset --hard" in c for c in cmds)   # proxy-illegal
    finally:
        await _shutdown(orch, db)


async def test_a_clean_plan_turn_is_left_alone(db_url):
    """The overwhelmingly common case: the plan turn behaved. No revert, no event, no noise."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        cmds: list[str] = []

        async def _exec(effort_id, *, command, session_id, **kw):
            cmds.append(command)
            return 0, "BEFORE-HEAD=abc123\nPROBE-DONE", False

        orch.router.exec_check = _exec
        assert await orch._revert_plan_turn_writes(eid) is False
        assert await orch._event_count(eid, "plan_turn_wrote_files") == 0
        assert not any("git clean" in c for c in cmds)      # nothing to clean, nothing run
    finally:
        await _shutdown(orch, db)
