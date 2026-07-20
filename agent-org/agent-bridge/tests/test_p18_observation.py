"""P18 — observation fidelity (evidence: gym-016, 2026-07-20).

Each test is one gym-016 failure. Full evidence in docs/P18-observation-fidelity.md.

The through-line, unchanged from P17 and from ORCHESTRATION-DESIGN §11: a claim that can be
settled by running something must be settled by running it. P18 extends that from "does this
symbol exist" (F11) to "does this misbehaviour happen" (F17), and from "trust the turn's summary"
to "read the findings it wrote down" (F4).

Fakes only.
"""

from __future__ import annotations

from test_lenses import (  # noqa: F401 — shared fixtures/helpers, same fake stack
    GOAL, REPO, _REPORT, _delivery, _effort, _orch, _round, _shutdown,
)


# ── F19 — a sweep's observations are mined for every open scope ───────────────
async def test_gap_extraction_fans_out_across_open_sibling_scopes(db_url):
    """gym-016 round 1: the goal_alignment lens found and precisely diagnosed a broken REPL flag
    parser. Gap analysis ran against the 68-char `json data storage` scope, the finding belonged
    to `cli and repl interface`, and it evaporated — still broken at the delivered head, shipped
    in the PR. The lens sweeps the whole branch; extraction must not throw away everything it saw
    about the siblings."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        root = await orch.add_scope_node("gym", "product", "the whole todo product")
        kids = await orch.decompose_scope(root, [
            ("json data storage", "loading, saving, atomic writes, repairing malformed entries"),
            ("cli and repl interface", "argument parsing, interactive loop, input validation"),
        ])
        assert len(kids) == 2
        goals = await orch._extraction_scopes(
            eid, kids[0], "loading, saving, atomic writes, repairing malformed entries")
        # the selected scope first, then the open sibling — the report is mined against both
        assert len(goals) == 2
        assert "atomic writes" in goals[0]
        assert any("interactive loop" in g for g in goals)
        assert await orch._event_count(eid, "gap_extraction_fanout") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_completed_scope_is_not_mined_for_more_work(db_url):
    """Fan-out covers OPEN scopes only. A scope that has completed is not owed further work, and
    re-deriving against it would reopen finished tiers every round."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        root = await orch.add_scope_node("gym", "product", "the whole todo product")
        kids = await orch.decompose_scope(root, [
            ("storage", "loading and saving the database file"),
            ("interface", "argument parsing and the interactive loop"),
        ])
        await orch._complete_scope(kids[1])
        goals = await orch._extraction_scopes(eid, kids[0], "loading and saving the database file")
        assert len(goals) == 1                      # only the selected, still-open scope
        assert "interactive loop" not in " ".join(goals)
    finally:
        await _shutdown(orch, db)


async def test_extraction_falls_back_to_the_selected_scope_without_a_tree(db_url):
    """No tree, or an unreadable one, must behave exactly as it did before P18 — one extraction
    against the scope in force. A new fan-out must never change the tree-less path."""
    orch, _chat, _harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, _c, _r = await _effort(orch)
        goals = await orch._extraction_scopes(eid, None, "the whole goal")
        assert goals == ["the whole goal"]
        assert await orch._event_count(eid, "gap_extraction_fanout") == 0
    finally:
        await _shutdown(orch, db)


# ── F4 — findings survive a truncated turn ────────────────────────────────────
async def test_findings_are_salvaged_from_a_truncated_lens(db_url):
    """P17 asked lenses to emit findings incrementally IN PROSE and it did not work: gym-016's
    goal_alignment lens ran ~30 probes over four minutes and returned 44 characters
    ("Now let me test malformed database handling:"). Same failure as gym-015's 70 chars. The
    same budget funds probing and reporting, so a lens that probes to exhaustion has nothing left
    to report with — and it is not a timeout (bound 5400s, died at ~4 min), so no limit change
    touches it. Findings now go to a file the harness reads back."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        banked = "\n".join(
            f"FINDING: probe {i} showed the parser mishandles input case {i}." for i in range(8))

        async def _exec(effort_id, *, command, session_id, **kw):
            if "lens-findings" in command and "cat" in command:
                return 0, banked + "\nSALVAGE-DONE", False
            return 0, "CLEARED", False

        orch.router.exec_check = _exec
        # all three lenses truncate to narration
        for _ in range(3):
            harness.output_queue.append("Now let me test malformed database handling:")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        orch.models._client.queue_text("none")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert await orch._event_count(eid, "lens_findings_salvaged") == 3
        # the observation genuinely happened — only the summary was lost — so the round IS swept
        assert r["swept"] is True
    finally:
        await _shutdown(orch, db)


async def test_a_truncated_lens_with_nothing_banked_still_does_not_sweep(db_url):
    """Salvage must not become a way to fake a sweep. A lens that banked nothing observed nothing
    worth keeping, and P17 F3's guard still has to hold."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _exec(effort_id, *, command, session_id, **kw):
            return 0, "SALVAGE-DONE", False     # empty findings file

        orch.router.exec_check = _exec
        for _ in range(3):
            harness.output_queue.append("Now let me test malformed database handling:")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["swept"] is False
        assert await orch._event_count(eid, "lens_findings_salvaged") == 0
        assert "not** a clean sweep" in r["note"]
    finally:
        await _shutdown(orch, db)


# ── F17 — a false DEFECT is refuted by running the named reproduction ─────────
async def test_a_task_alleging_a_misbehaviour_the_code_refutes_is_dropped(db_url):
    """gym-016's clean_code lens reported "`strptime` still accepts invalid dates like
    2025-02-30 ... the function claims to validate but does not fully validate". It is false —
    strptime raises "day is out of range for month" and the CLI exits 1 with a clear message —
    and it became the open task "Reject semantically invalid dates in _parse_date": fabricated
    work against correct code. P17's F11 filter cannot catch this: it only checks asserted
    ABSENCES, and deliberately never fires on reject/fix verbs so it can never delete real repair
    work. This is its mirror."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _exec(effort_id, *, command, session_id, **kw):
            if "HANDLED" in command:
                return 0, "HANDLED 0", False     # the repro exits non-zero WITH a diagnostic
            return 0, "", False

        orch.router.exec_check = _exec
        r = await _round(
            orch, harness, eid, chan, root,
            "Reject semantically invalid dates in _parse_date\nREPRO: python3 todo.py add x "
            "--due 2025-02-30")
        assert r["open_tasks"] == []
        assert await orch._event_count(eid, "false_defect_rejected") == 1
    finally:
        await _shutdown(orch, db)


async def test_a_misbehaviour_that_reproduces_is_kept(db_url):
    """The converse, and the one that matters most: a REAL defect whose reproduction genuinely
    misbehaves must survive. A filter that drops real bugs is far worse than one that lets
    fabricated work through."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _exec(effort_id, *, command, session_id, **kw):
            if "HANDLED" in command:
                return 0, "UNPROVEN 0", False    # the input was NOT handled — bug is real
            return 0, "", False

        orch.router.exec_check = _exec
        r = await _round(
            orch, harness, eid, chan, root,
            "Reject flags in REPL input instead of swallowing them into the text\n"
            "REPRO: printf 'add x --priority high\\nquit\\n' | python3 todo.py repl")
        assert len(r["open_tasks"]) == 1
        assert await orch._event_count(eid, "false_defect_rejected") == 0
    finally:
        await _shutdown(orch, db)


async def test_a_finding_with_no_named_reproduction_is_kept(db_url):
    """An orchestrator-level check cannot know how to drive an arbitrary product — an earlier
    draft synthesised `python3 todo.py add --due <literal>`, which works on the gym's todo CLI and
    is meaningless anywhere else. If the lens named no reproduction, the finding is unchecked, and
    an unchecked finding is kept."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)

        async def _exec(effort_id, *, command, session_id, **kw):
            raise AssertionError("nothing to run — no REPRO was named")

        orch.router.exec_check = _exec
        r = await _round(orch, harness, eid, chan, root,
                         "Reject semantically invalid dates in _parse_date")
        assert len(r["open_tasks"]) == 1
        assert await orch._event_count(eid, "false_defect_rejected") == 0
    finally:
        await _shutdown(orch, db)


# ── F13 — a delivered test count must not silently fall ───────────────────────
async def test_a_falling_test_count_raises_a_flag(db_url):
    """gym-015: a stale workspace published a tree with 51 tests where the branch had 55, and the
    drop passed unremarked because nothing remembers the previous count. Specified in P17 as "the
    cheapest possible detector" and then not built."""
    orch, chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.projects.set_check("gym", "python3 -m unittest discover -s tests")
        calls = {"n": 0}

        async def _exec(effort_id, *, command, session_id, **kw):
            calls["n"] += 1
            ran = 55 if calls["n"] == 1 else 51
            return 0, f"Ran {ran} tests in 0.2s\n\nOK", False

        orch.router.exec_check = _exec
        assert await orch._check_test_count_regression(eid) is None      # first: records 55
        assert await orch._check_test_count_regression(eid) == 55        # second: 51 < 55
        assert await orch._event_count(eid, "test_count_regressed") == 1
        posts = " ".join(str(p.get("message", "")) for p in chat.posted)
        assert "fell from 55 to 51" in posts
    finally:
        await _shutdown(orch, db)


async def test_a_rising_or_equal_test_count_is_silent(db_url):
    """Only a DROP is a signal. Normal rounds add tests, and a flag on every delivery would be
    noise the operator learns to ignore."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.projects.set_check("gym", "python3 -m unittest discover -s tests")
        counts = iter([31, 31, 55])

        async def _exec(effort_id, *, command, session_id, **kw):
            return 0, f"Ran {next(counts)} tests in 0.2s\n\nOK", False

        orch.router.exec_check = _exec
        for _ in range(3):
            assert await orch._check_test_count_regression(eid) is None
        assert await orch._event_count(eid, "test_count_regressed") == 0
    finally:
        await _shutdown(orch, db)


async def test_an_unmeasurable_suite_is_not_a_regression(db_url):
    """A runner whose output is not unittest-shaped yields no count. Unmeasurable is not a drop —
    the check must stay silent rather than guess."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.projects.set_check("gym", "pytest -q")

        async def _exec(effort_id, *, command, session_id, **kw):
            return 0, "5 passed in 0.10s", False      # pytest-shaped, no "Ran N tests"

        orch.router.exec_check = _exec
        assert await orch._check_test_count_regression(eid) is None
        assert await orch._event_count(eid, "delivery_test_count") == 0
    finally:
        await _shutdown(orch, db)


# ── F18 — a verification claim must carry a verification ──────────────────────
async def test_a_claim_without_a_test_run_is_flagged(db_url):
    """gym-016 produced three of these: a step reporting "All 31 tests pass, acceptance corpus
    passes" after running only `git log --oneline -5`; a drain no-op reporting "31/31 tests pass"
    after `git log --oneline -3`; and a plan turn reporting "the existing test suite (28 tests)
    passes" moments after a command printed 31. Each was true by luck, and the org had no way to
    know that — the same mechanism by which gym-015's delivery claim outlived the commit it
    named."""
    orch, chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        class _R:
            output = "All 31 tests pass, acceptance corpus check passes."
            commands = ["git log --oneline -5", "git status -sb"]

        assert await orch._flag_unverified_claim(eid, _R()) is True
        assert await orch._event_count(eid, "unverified_claim") == 1
        posts = " ".join(str(p.get("message", "")) for p in chat.posted)
        assert "without running the tests" in posts
    finally:
        await _shutdown(orch, db)


async def test_a_claim_backed_by_a_real_test_run_is_not_flagged(db_url):
    """The earned case, which must stay silent — otherwise the flag is noise on every good turn."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        class _R:
            output = "31/31 tests pass, acceptance corpus passes."
            commands = ["cd /workspace && python3 -m unittest discover -s tests -v"]

        assert await orch._flag_unverified_claim(eid, _R()) is False
        assert await orch._event_count(eid, "unverified_claim") == 0
    finally:
        await _shutdown(orch, db)


async def test_an_honest_carry_forward_is_not_flagged(db_url):
    """Carrying a result forward is legitimate and common — most no-op turns correctly report an
    unchanged result. The defect is silence about provenance, not the carry-forward itself, so a
    turn that says where the result came from passes."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        class _R:
            output = ("All work was already delivered in the previous turn (commit 3c3dcd5). "
                      "31/31 tests pass. No additional changes needed.")
            commands = ["git log --oneline -3"]

        assert await orch._flag_unverified_claim(eid, _R()) is False
        assert await orch._event_count(eid, "unverified_claim") == 0
    finally:
        await _shutdown(orch, db)


async def test_an_unreadable_command_record_is_never_flagged(db_url):
    """`_command_texts` returns [] both for "ran nothing" and for "a shape we could not read".
    Flagging on the latter would cry wolf on every daemon whose activity schema drifts, so an
    empty record is always treated as "cannot tell"."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        class _R:
            output = "All 31 tests pass."
            commands: list = []

        assert await orch._flag_unverified_claim(eid, _R()) is False
        assert await orch._event_count(eid, "unverified_claim") == 0
    finally:
        await _shutdown(orch, db)


async def test_a_turn_that_makes_no_verification_claim_is_ignored(db_url):
    """Most turns say nothing about tests. The matcher must not fire on intent ("I will run the
    tests next") or on a mere mention of the suite."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)

        class _R:
            output = "Added cmd_delete to todo.py. Next I will run the test suite."
            commands = ["git log --oneline -3"]

        assert await orch._flag_unverified_claim(eid, _R()) is False
        assert await orch._event_count(eid, "unverified_claim") == 0
    finally:
        await _shutdown(orch, db)


async def test_the_harness_records_what_a_turn_actually_ran(db_url):
    """F18 needs the command record to exist at all. The daemon already reports `activity` and the
    harness already streams it for observability; this keeps it on the result so a gate can check
    a claim against it."""
    from app.worker.harness import _command_texts
    assert _command_texts([{"command": "python3 -m unittest", "ok": True},
                           {"command": "git log", "ok": True}]) == [
        "python3 -m unittest", "git log"]
    assert _command_texts([]) == []
    assert _command_texts([{"no_command_key": 1}, "not a dict", None]) == []
