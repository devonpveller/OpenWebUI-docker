"""P30 Slice 1 — Mode B: adversarial hardening turns a REPRODUCED defect into a durable corpus check.

A contrarian lens tries to break the delivered product; each finding must carry a REPRO — a check that
EXITS NON-ZERO on the current code (a real break, not an opinion — §6 hygiene). A reproduced finding
becomes a §10 acceptance-corpus check (org-generated; red-gates every future delivery). Un-reproduced
findings are dropped. Fakes only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.capabilities import BranchDelivery
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"


async def _orch(db_url, **overrides):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", mode_b=True,
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _shutdown(orch, db):
    if orch._bg_tasks:
        await asyncio.gather(*list(orch._bg_tasks))
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


async def _effort(orch):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    return eid, chan, root


def _delivery():
    return BranchDelivery(branch="agent/feat", exists=True, ahead=1, head_sha="abc1234567")


# ── the finding/repro parser ──────────────────────────────────────────────────
def test_mode_b_pairs_binds_repro_to_the_finding_above_it():
    p = Orchestrator._mode_b_pairs(
        "narration\n"
        "echo 'FINDING: db_path empty string returns cwd' >> /tmp/lens-findings.txt\n"
        "echo 'REPRO: python3 -c \"import todo; assert todo.db_path()\"' >> /tmp/lens-findings.txt\n")
    assert p == [("db_path empty string returns cwd", 'python3 -c "import todo; assert todo.db_path()"')]
    assert Orchestrator._mode_b_pairs("REPRO: orphan with no finding") == []   # no preceding FINDING


# ── the reproducibility gate + corpus check ──────────────────────────────────
async def test_a_reproduced_defect_becomes_a_durable_corpus_check(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.output_queue.append(
            "FINDING: db_path('') returns an empty string instead of the default path\n"
            "REPRO: python3 -c \"import todo; assert todo.db_path('')\"")
        orch.harness.check_queue = [
            (0, "CLEARED", False),          # _clear_lens_findings
            (0, "SALVAGE-DONE", False),     # _salvage_lens_findings (cat)
            (1, "AssertionError", False),   # the REPRO FAILS on the current code → a real, reproduced defect
        ]
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["reproduced"] == 1 and r["checks_added"] == 1
        assert await orch._event_count(eid, "mode_b_check_added") == 1
        checks = await orch.projects.list_acceptance_checks("gym")
        assert len(checks) == 1 and "db_path" in checks[0]["origin_note"]      # banked, durable, org-generated
    finally:
        await _shutdown(orch, db)


async def test_an_unreproduced_finding_is_dropped_not_banked(db_url):
    """A finding whose REPRO PASSES on the current code is an opinion, not a defect — drop it, bank nothing."""
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        orch.harness.output_queue.append(
            "FINDING: a stylistic nit that does not actually break anything\n"
            "REPRO: python3 -c \"pass\"")
        orch.harness.check_queue = [
            (0, "CLEARED", False),
            (0, "SALVAGE-DONE", False),
            (0, "", False),                 # the REPRO PASSES → not a real break → dropped
        ]
        r = await orch._mode_b_phase(eid, chan, root, REPO, _delivery())
        assert r["reproduced"] == 0 and r["checks_added"] == 0
        assert await orch._event_count(eid, "mode_b_finding_unreproduced") == 1
        assert await orch.projects.list_acceptance_checks("gym") == []
    finally:
        await _shutdown(orch, db)
