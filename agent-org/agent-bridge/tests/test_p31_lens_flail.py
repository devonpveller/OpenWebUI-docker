"""P31 F31.4 — a BRIDGE-SIDE flail-guard for READ-ONLY lens turns.

gym-035 round 3: a lens turn wedged repeating ONE identical command for minutes. It evaded every
existing backstop — the daemon's flail-guard (keys on read-*without-edit*, but a lens never edits by
design), the offset-silence watchdog (the offset advances on each repeat), and lens truncation (the
repeats don't grow the findings file) — and would have looped to the turn deadline. The fix: the poll
loop stops a turn after N consecutive identical commands (`max_repeat`), and the lens sweep salvages
whatever findings streamed before the flail. Fakes only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.modules.capabilities import BranchDelivery
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import _LENSES, Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"
GOAL = "a todo CLI that adds, lists, completes and deletes todos"


# ── the harness-level repeat guard (deterministic unit) ───────────────────────
async def test_repeat_guard_stops_at_the_nth_identical_command():
    h = FakeHarness()
    h.stream_commands = ["scan a", "scan b", "probe x", "probe x", "probe x", "probe x", "later"]
    r = await h.wake("http://w1:8090", "s", "p", max_repeat=3)
    assert r.status == "flail"
    # stopped ON the 3rd consecutive "probe x" — nothing after it ran
    assert r.commands == ["scan a", "scan b", "probe x", "probe x", "probe x"]


async def test_guard_off_by_default_and_ignores_non_consecutive_repeats():
    # guard disabled (max_repeat=0) → a repeating stream runs to a normal terminal state
    h = FakeHarness(result_status="done")
    h.stream_commands = ["x", "x", "x", "x", "x"]
    assert (await h.wake("http://w1:8090", "s", "p", max_repeat=0)).status == "done"
    # armed, but repeats are NOT consecutive → no flail (a real, varied sweep is untouched)
    h2 = FakeHarness(result_status="done")
    h2.stream_commands = ["a", "x", "b", "x", "c", "x"]
    assert (await h2.wake("http://w1:8090", "s", "p", max_repeat=3)).status == "done"


# ── F31.4b — the SAME probe re-run on a fresh scratch file each time (gym-038) ──
async def test_repeat_guard_catches_a_probe_re_run_on_fresh_scratch_files():
    """gym-038's wedge: the goal lens looped one REPL probe, changing only the temp DB path
    (`/tmp/todo_eval_60.json`, `…_61.json`, …). Raw-string F31.4 never tripped; F31.4b collapses
    the temp path so the repeat is seen."""
    h = FakeHarness()
    h.stream_commands = ["ls -la"] + [
        f"cd /workspace && export TODO_DB=/tmp/todo_eval_{i}.json && "
        f"printf 'add test\\ndone 1\\nquit\\n' | python3 todo.py repl" for i in range(60, 68)]
    r = await h.wake("http://w1:8090", "s", "p", max_repeat=6)
    assert r.status == "flail"                       # caught despite the changing scratch-file path
    assert len(r.commands) == 1 + 6                  # stopped ON the 6th normalised-identical probe


async def test_varied_probes_with_temp_paths_are_not_flailed():
    """Fail-safe: only the TEMP PATH is normalised, so probes that genuinely differ (different
    subcommands) are NOT collapsed together — a real sweep touching /tmp scratch files is untouched."""
    h = FakeHarness(result_status="done")
    subcmds = ["add x", "list", "done 1", "reopen 1", "delete 1", "summary", "search x", "clear"]
    h.stream_commands = [
        f"export TODO_DB=/tmp/t{i}.json && python3 todo.py {c}" for i, c in enumerate(subcmds)]
    assert (await h.wake("http://w1:8090", "s", "p", max_repeat=3)).status == "done"


# ── the lens sweep: a flail is salvaged + audited, and the guard is armed ──────
async def _orch(db_url, **over):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", **over,
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, GOAL, created_by="po")
    return eid, chan, root


def _delivery():
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


async def test_lens_sweep_arms_the_guard_and_salvages_a_flail(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        # The lens streams two real FINDINGs, then wedges repeating one probe past the threshold.
        findings = [
            "echo 'FINDING: db_path('') returns an empty string instead of the default path' >> /tmp/lens-findings.txt",
            "echo 'FINDING: the REPL add command corrupts text containing --priority substrings' >> /tmp/lens-findings.txt",
        ]
        orch.harness.stream_commands = findings + ["python todo.py add 'test --priority high'"] * (
            orch.s.lens_flail_repeats + 3)
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        # every lens wake was armed with the bridge-side guard
        assert orch.harness.wakes and all(
            w["max_repeat"] == orch.s.lens_flail_repeats for w in orch.harness.wakes)
        # the wedged lens was stopped, not left to loop to the deadline
        assert await orch._event_count(eid, "lens_flail_stopped") >= 1
        # and its findings-so-far were recovered from the streamed commands
        assert await orch._event_count(eid, "lens_findings_salvaged") >= 1
    finally:
        await db.dispose()


async def test_a_non_flailing_lens_is_untouched(db_url):
    """Regression guard: a lens that runs a VARIED sweep (no long identical run) must not be stopped."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.stream_commands = [f"probe step {i}" for i in range(orch.s.lens_flail_repeats + 5)]
        await orch._lens_sweep(eid, chan, root, REPO, _delivery(), round_no=1)
        assert await orch._event_count(eid, "lens_flail_stopped") == 0
    finally:
        await db.dispose()
