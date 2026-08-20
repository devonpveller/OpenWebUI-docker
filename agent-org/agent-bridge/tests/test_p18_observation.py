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


# ── F19-redux (P19) — mine the report ONCE against the product goal, then route ───────────────
#
# P18 F19 fanned gap analysis across every open scope so a sibling's finding could not evaporate
# (gym-016). But mining the same whole-branch report against N overlapping scope-goals N-plicates
# every cross-scope finding: gym-017 round 3 turned ~8 findings into 24 tasks and re-ascended the
# open queue 9 → 24 — the wrong direction for a propagation-count termination (P10.4). The fix is
# to extract ONCE against the product goal and let the existing per-task `_seam_owner` routing file
# each result to its owner: one finding, one task, no paraphrase duplication, a trustworthy count.
async def test_gap_analysis_is_run_once_against_the_product_goal_not_per_scope(db_url):
    """The convergence fix. With a decomposed tree, a round used to run gap analysis once PER open
    scope (the fan-out); it now runs exactly once, against the effort's own goal — so the derived
    count reflects distinct findings, not findings × scopes."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        seen = {"n": 0, "goal": None}

        async def _fake_gap(effort_id, report, goal):
            seen["n"] += 1
            seen["goal"] = goal
            return ["Add atomic writes so an interrupted save cannot truncate the storage file",
                    "Fix the interface so repl input flags are parsed, not swallowed into text"]

        async def _no_lens_tasks(effort_id, lens, report):
            return []

        orch._gap_analysis = _fake_gap
        orch._tasks_from_lens = _no_lens_tasks
        for _ in range(3):
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text(          # the ONE decomposition call
            "storage :: loading saving atomic writes json database file resilience\n"
            "interface :: argument parsing interactive repl loop flag input validation")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert seen["n"] == 1                      # ONCE — not once per open scope
        assert seen["goal"] == GOAL                # against the PRODUCT goal, not a scope's
        assert r["new_tasks"] == 2                 # one task per distinct finding, no fan-out
    finally:
        await _shutdown(orch, db)


async def test_a_decomposing_round_does_not_multiply_its_findings_by_the_scope_count(db_url):
    """The anti-duplication guarantee, end to end. gym-017 round 3 fanned ~8 findings across the
    open scopes into 24 tasks and re-ascended the queue; the implementer then de-duped them back to
    9 — the harm was to the COUNT (the termination signal), not the work. Under F19-redux the same
    two distinct findings, with two open sibling scopes present, yield exactly two tasks: the count
    equals the number of distinct findings, so it can descend to a trustworthy zero.

    Routing is deliberately UNCHANGED: the derivations land on the selected working scope and stay
    dispatchable this round. Distributing them to the siblings would strand them, because the tier
    walk selects downward only (`_dispatchable_tasks` / the "worst failure mode" test)."""
    orch, _chat, harness, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)

        async def _fake_gap(effort_id, report, goal):
            return ["Add atomic writes to the storage path so a crash cannot truncate the file",
                    "Fix the interface repl so flags in input are parsed, not swallowed as text"]

        async def _no_lens_tasks(effort_id, lens, report):
            return []

        orch._gap_analysis = _fake_gap
        orch._tasks_from_lens = _no_lens_tasks
        for _ in range(3):
            harness.output_queue.append(_REPORT)
        orch.models._client.queue_text(          # decompose into two OPEN sibling scopes
            "storage :: loading saving atomic writes json database file resilience\n"
            "interface :: argument parsing interactive repl loop flag input validation")
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert r["new_tasks"] == 2               # two findings -> two tasks, NOT two × two scopes
        assert len(r["open_tasks"]) == 2         # both dispatchable this round — nothing stranded
    finally:
        await _shutdown(orch, db)


async def test_without_a_tree_the_product_goal_is_still_mined_once(db_url):
    """Tree-less (tier walk off): node routing is unavailable, but the single-extraction contract
    still holds and the goal passed is the effort's own goal."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        seen = {"n": 0, "goal": None}

        async def _fake_gap(effort_id, report, goal):
            seen["n"] += 1
            seen["goal"] = goal
            return ["Add a delete command to the cli"]

        async def _no_lens_tasks(effort_id, lens, report):
            return []

        orch._gap_analysis = _fake_gap
        orch._tasks_from_lens = _no_lens_tasks
        for _ in range(3):
            harness.output_queue.append(_REPORT)
        r = await orch._drain_round(eid, chan, root, REPO, _delivery())
        assert seen["n"] == 1
        assert seen["goal"] == GOAL
        assert r["new_tasks"] == 1
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


# ── P22 F22.1 — a truncated lens is salvaged from its ACTIONS (the command stream) ────────────
def test_findings_in_commands_extracts_and_cleans():
    """Pure: pull `FINDING:` out of the echo commands a lens streamed, stripping the shell
    redirection and quotes; drop non-findings and too-short stubs."""
    from app.orchestrator import Orchestrator
    got = Orchestrator._findings_in_commands([
        "echo 'FINDING: the --due flag accepts an unparseable date' >> /tmp/lens-findings.txt",
        'echo "FINDING: reopen on a missing id exits 0 silently"',
        "python3 todo.py list",     # not a finding
        "echo 'FINDING: x'",        # too short -> dropped
    ])
    assert got == ["FINDING: the --due flag accepts an unparseable date",
                   "FINDING: reopen on a missing id exits 0 silently"]


async def test_findings_are_salvaged_from_the_command_stream_when_the_file_is_empty(db_url):
    """gym-020: the goal_alignment lens NARRATED instead of writing the findings file, so a
    file-only salvage recovered NOTHING and the round was never swept. Salvage now also parses
    FINDING: from the turn's streamed commands (its actions), so a lens that echoed findings is
    recovered even with an empty file — the design's 'the environment remembers' / 'verify
    self-report against actions'."""
    orch, _chat, _harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, _c, _r = await _effort(orch)

        async def _exec(effort_id, *, command, session_id, **kw):
            return 0, "SALVAGE-DONE", False          # the findings FILE is empty

        orch.router.exec_check = _exec
        cmds = [
            "cd /workspace && python3 todo.py add x --due nonsense",
            "echo 'FINDING: the --due flag accepts an unparseable date without any error' "
            ">> /tmp/lens-findings.txt",
            'echo "FINDING: reopen on a missing id prints nothing and exits 0"',
        ]
        salvaged = await orch._salvage_lens_findings(
            eid, "goal_alignment", round_no=1, commands=cmds)
        assert "unparseable date" in salvaged and "reopen on a missing id" in salvaged
        assert await orch._event_count(eid, "lens_findings_salvaged") == 1
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


# ── F17-redux (P19) — carry a shown repro through, and tell a crash from a clean rejection ────
async def test_the_false_defect_probe_treats_a_traceback_as_unhandled(db_url):
    """gym-017's undo bug is REAL and its repro exits non-zero with a `JSONDecodeError` traceback.
    The first F17 rule ("non-zero exit + output → HANDLED → drop") could not tell that crash from
    an argparse exit-2, and — had a repro reached it — would have DROPPED a real critical bug as
    fabricated. The probe must check for a traceback FIRST and route it to keep."""
    orch, _chat, harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, chan, root = await _effort(orch)
        captured = {}

        async def _exec(effort_id, *, command, session_id, **kw):
            captured["cmd"] = command
            return 0, "UNPROVEN 0", False        # a crash routes to UNPROVEN in the real shell

        orch.router.exec_check = _exec
        r = await _round(
            orch, harness, eid, chan, root,
            "Reject the undo command crashing on an empty database\n"
            "REPRO: python3 todo.py undo")
        # the traceback test is present AND is checked BEFORE the non-zero+output HANDLED branch
        assert "grep -qF 'Traceback (most recent call last)'" in captured["cmd"]
        assert captured["cmd"].index("Traceback") < captured["cmd"].index("HANDLED")
        assert len(r["open_tasks"]) == 1                       # UNPROVEN -> kept
        assert await orch._event_count(eid, "false_defect_rejected") == 0
    finally:
        await _shutdown(orch, db)


async def test_gap_analysis_carries_a_shown_repro_into_the_task_body(db_url):
    """Part 1 of the pair: F17 was inert because gap analysis rewrote lens findings into plain
    bodies and stripped the reproduction — `_drop_false_defects` never had a command to run. The
    extraction now copies a repro the report shows onto the task, and the prompt forbids inventing
    one (an invented probe is meaningless off the single product it was guessed for)."""
    orch, _chat, _harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, _c, _r = await _effort(orch)
        orch.models._client.queue_text(
            "Reject invalid dates in _parse_date\nREPRO: python3 todo.py add x --due 2025-02-30")
        tasks = await orch._gap_analysis(eid, _REPORT, GOAL)
        assert len(tasks) == 1
        assert tasks[0].startswith("Reject invalid dates in _parse_date")
        assert "REPRO: python3 todo.py add x --due 2025-02-30" in tasks[0]
        sys_p = orch.models._client.calls[-1]["system"]
        assert "REPRO:" in sys_p and "never invent" in sys_p.lower()
    finally:
        await _shutdown(orch, db)


async def test_tasks_from_lens_keeps_a_repro_on_its_defect(db_url):
    """The clean_code / project_documentation path carries a repro the same way — the shown command
    stays on the DEFECT it demonstrates (and a repro under a non-DEFECT line, which has no
    false-defect check to feed, is dropped with it)."""
    orch, _chat, _harness, db = await _orch(db_url, tier_walk=False)
    try:
        eid, _c, _r = await _effort(orch)
        orch.models._client.queue_text(
            "DEFECT: the undo command crashes on an empty database\n"
            "REPRO: python3 todo.py undo\n"
            "PREFERENCE: rename todos.json to db.json")
        tasks = await orch._tasks_from_lens(eid, "clean_code", _REPORT)
        assert len(tasks) == 1
        assert tasks[0].startswith("the undo command crashes on an empty database")
        assert "REPRO: python3 todo.py undo" in tasks[0]
    finally:
        await _shutdown(orch, db)


# ── F13 (P19 F13-redux) — count test DEFINITIONS by AST, not by scraping "Ran N tests" ────────
async def test_a_falling_test_count_raises_a_flag(db_url):
    """gym-015: a stale workspace published a tree with fewer tests than the branch, and the drop
    passed unremarked because nothing remembers the previous count. The count is now a stable
    definition count (`TESTDEFS`), so a genuine drop still flags."""
    orch, chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.projects.set_check("gym", "python3 -m unittest discover -s tests")
        calls = {"n": 0}

        async def _exec(effort_id, *, command, session_id, **kw):
            calls["n"] += 1
            n = 55 if calls["n"] == 1 else 51
            return 0, f"TESTDEFS {n}\n", False

        orch.router.exec_check = _exec
        assert await orch._check_test_count_regression(eid) is None      # first: records 55
        assert await orch._check_test_count_regression(eid) == 55        # second: 51 < 55
        assert await orch._event_count(eid, "test_count_regressed") == 1
        posts = " ".join(str(p.get("message", "")) for p in chat.posted)
        assert "fell from 55 to 51" in posts
    finally:
        await _shutdown(orch, db)


async def test_a_flaky_runner_count_does_not_masquerade_as_a_regression(db_url):
    """THE gym-017 FALSE POSITIVE. The first publish scraped `55` from a flaky `Ran N` line, the
    branch has a stable 44 `def test_`, and the honest 44 next round read as a regression. Counting
    definitions makes the number stable: the same tree yields 44 both times even when the runner's
    stdout also carries a spurious `Ran 55 tests`, so no phantom regression fires."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.projects.set_check("gym", "python3 -m unittest discover -s tests")

        async def _exec(effort_id, *, command, session_id, **kw):
            # The AST counter only ever emits TESTDEFS; a flaky "Ran 55" cannot reach the parser.
            assert "TESTDEFS" in command and "ast" in command
            return 0, "Ran 55 tests in 0.2s\nTESTDEFS 44\n", False

        orch.router.exec_check = _exec
        for _ in range(3):
            assert await orch._check_test_count_regression(eid) is None
        assert await orch._event_count(eid, "test_count_regressed") == 0
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
            return 0, f"TESTDEFS {next(counts)}\n", False

        orch.router.exec_check = _exec
        for _ in range(3):
            assert await orch._check_test_count_regression(eid) is None
        assert await orch._event_count(eid, "test_count_regressed") == 0
    finally:
        await _shutdown(orch, db)


async def test_an_unmeasurable_suite_is_not_a_regression(db_url):
    """A tree the counter could not measure (no python, a crash before the marker) yields no count.
    Unmeasurable is not a drop — the check must stay silent rather than guess."""
    orch, _chat, _harness, db = await _orch(db_url)
    try:
        eid, _c, _r = await _effort(orch)
        await orch.projects.set_check("gym", "pytest -q")

        async def _exec(effort_id, *, command, session_id, **kw):
            return 0, "python3: command not found", False      # no TESTDEFS marker

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
