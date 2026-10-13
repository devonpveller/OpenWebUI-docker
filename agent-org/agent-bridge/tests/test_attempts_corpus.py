"""ao-attempt2 B1 - the plan grammar against the REAL model's plans.

ao-attempt's plan grammar was written to the author's own fixtures and refused 9 of the 13 plans
the real local model (Qwen via llama.cpp) wrote in the agent bake-off C7, C7a and C7b runs; a
second refusal stopped the effort. tests/fixtures/c7_plan_corpus.json holds EVERY one of those
plan replies verbatim (the final assistant text of each plan turn; the corpus has no VERDICT
reply - the real model was never asked for one yet). These tests replay them:

* through `attempts.parse_plan` - every reply yields exactly the steps it numbered, in order, with
  its continuation lines attached to their step (the expected step numbers in the fixture were
  derived WITHOUT the bridge's parser, from the column-0 numbered lines of each PLAN section);
* with anchor tags appended in the shapes a model writes (multi-id, parenthesised, colon-less, on
  a continuation line, bold, bare) - each step gets the id it named first, no problem reported;
* through the REAL plan gate (`Orchestrator._worker_plan_gate`): the model ignores the tag ask
  twice -> asked once, then its steps are MAPPED to the effort's anchor items and the plan is
  approved; never stopped.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Event
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((Path(__file__).parent / "fixtures" / "c7_plan_corpus.json")
                    .read_text(encoding="utf-8"))["replies"]
IDS = [r["id"] for r in CORPUS]


def _a():
    from app.modules import attempts
    return attempts


def test_b1_the_corpus_is_the_whole_real_set():
    """13 plan replies: C7 (7), C7a (5), C7b (1) - every plan turn that ended with a reply."""
    assert len(CORPUS) == 13
    assert sorted({r["id"].split("-")[0] for r in CORPUS}) == ["C7", "C7a", "C7b"]
    assert all("UNDERSTANDING" in r["text"] and r["expected_steps"] for r in CORPUS)
    assert not any(re.search(r"^\W*VERDICT", r["text"], re.M | re.I) for r in CORPUS)


# ── parse_plan on the raw replies ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rep", CORPUS, ids=IDS)
def test_b1_every_real_plan_yields_the_steps_it_numbered(rep):
    att = _a()
    d = att.parse_plan(rep["text"], {"A1": "the goal"}, fixed=True)
    assert [s["n"] for s in d.steps] == rep["expected_steps"], (rep["plan_header"], d.problems)
    assert not d.nothing_remains
    # untagged (the base prompt never asked for tags): every step is reported as untagged,
    # nothing else is wrong with the plan
    assert all(s["anchor"] == "" for s in d.steps)
    assert len(d.problems) == len(d.steps), d.problems


# continuation lines belong to their step (tags and paths written there must count)
CONTINUATION = [
    ("C7-ao-wd-offset-r1", 2, "Cache capped at 64 entries"),
    ("C7-ao-wd-offset-r1", 3, "FakeHarness.__init__"),
    ("C7a-ao-queue-r1", 1, "reap_stale_running"),
    ("C7b-ao-queue-r1", 2, "alert_at = max(1, ceil(cap*alert_ratio))"),
    ("C7a-ao-wd-offset-r2", 2, "Remove the now-unused"),
]


@pytest.mark.parametrize("rid,n,needle", CONTINUATION, ids=[f"{c[0]}-s{c[1]}" for c in CONTINUATION])
def test_b1_continuation_lines_attach_to_their_step(rid, n, needle):
    att = _a()
    rep = next(r for r in CORPUS if r["id"] == rid)
    d = att.parse_plan(rep["text"], {"A1": "x"}, fixed=True)
    step = next(s for s in d.steps if s["n"] == n)
    assert needle in step["text"], step["text"][:400]
    nxt = next((s for s in d.steps if s["n"] == n + 1), None)
    assert nxt is None or needle not in nxt["text"]


# ── tagged variants: the shapes a model writes ───────────────────────────────────────────────
SHAPES = [
    "{line} [anchor: {id}]",
    "{line} [anchor: {id}, A3]",          # several ids: the first is taken
    "{line} (anchor: {id})",
    "{line} [anchor {id}]",
    "{line} **[anchor: {id}]**",
    "CONT",                                # the tag on the step's last continuation line
    "{line} - anchor {id}",
    "{line} [{id}]",
    "{line} (anchor item {id})",
    "{line} [Anchor: {id}]",
]


def _tagged(text: str) -> tuple[str, list[str]]:
    """Append a tag to every top-level step of the reply's PLAN section, rotating the shapes;
    returns the text and the id each step names first."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if "PLAN" in ln
                 and not ln.lstrip(" *").startswith("UNDERSTANDING"))
    end = next(i for i in range(start + 1, len(lines)) if re.search(r"WON.?T DO", lines[i]))
    steps = [i for i in range(start + 1, end) if re.match(r"^(?:\*\*)?\d+[.)]", lines[i])]
    want = []
    for k, i in enumerate(steps):
        aid = f"A{k % 2 + 1}"
        shape = SHAPES[k % len(SHAPES)]
        if shape == "CONT":
            nxt = steps[k + 1] if k + 1 < len(steps) else end
            last = max(j for j in range(i, nxt) if lines[j].strip())
            lines[last] = lines[last] + f" [anchor: {aid}]"
        else:
            lines[i] = shape.format(line=lines[i], id=aid)
        want.append(aid)
    return "\n".join(lines), want


@pytest.mark.parametrize("rep", CORPUS, ids=IDS)
def test_b1_tagged_real_plans_declare_the_first_id_of_each_step(rep):
    att = _a()
    text, want = _tagged(rep["text"])
    d = att.parse_plan(text, {"A1": "x", "A2": "y", "A3": "z"}, fixed=True)
    assert d.problems == []
    assert [s["n"] for s in d.steps] == rep["expected_steps"]
    assert [s["anchor"] for s in d.steps] == want
    # the tag is metadata: taken out of the step's text
    assert not any(re.search(r"\banchor\b|\[A\d\]", s["text"], re.I) for s in d.steps)


# ── through the REAL plan gate ───────────────────────────────────────────────────────────────
async def _orch(db_url):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="all",
        attempts_enabled=True,   # ao-attempt is OFF by default (experimental): opt in
    )
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


async def _kinds(orch, eid) -> list[tuple[str, dict]]:
    async with orch.db.session_factory() as s:
        rows = (await s.execute(select(Event).where(Event.effort_id == eid)
                                .order_by(Event.id))).scalars().all()
        return [(e.kind, e.payload or {}) for e in rows]


GOALS = {
    # one sentence -> one anchor item: an untagged step can only serve A1
    "one-item": ("Fix the queue connection leak.", "A1"),
    # a list -> several items: an untagged step serves the effort's items as a whole (GOAL)
    "two-items": ("- fix the queue connection leak\n- make inference shedding visible", "GOAL"),
}


@pytest.mark.parametrize("goal_kind", sorted(GOALS))
@pytest.mark.parametrize("rep", CORPUS, ids=IDS)
async def test_b1_the_real_gate_maps_untagged_real_plans_and_never_stops(db_url, rep, goal_kind):
    """The model ignores the tag ask twice: one refusal, then the steps are MAPPED to the
    effort's anchor items, the plan is stored with them and approved; nothing stops."""
    goal, mapped = GOALS[goal_kind]
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        await orch.charters.set_goal(eid, goal, created_by="po")
        orch.harness.output_queue.extend([rep["text"], rep["text"]])
        out = await orch._worker_plan_gate(eid, chan, root, goal, None, None, None, None)
        ev = await _kinds(orch, eid)
        kinds = [k for k, _ in ev]
        assert out is not None, [k for k in kinds if "plan" in k]
        assert kinds.count("worker_plan_stopped") == 0
        # both replies are audited as undeclared (ask 1, ask 2); only the first is re-asked
        assert [p["ask"] for k, p in ev if k == "plan_steps_undeclared"] == [1, 2]
        mapped_ev = [p for k, p in ev if k == "plan_steps_mapped"]
        assert len(mapped_ev) == 1 and mapped_ev[0]["to"] == mapped
        assert mapped_ev[0]["steps"] == rep["expected_steps"]
        assert kinds.count("worker_plan_approved") == 1
        stored = [p for k, p in ev if k == "plan_steps_stored"][-1]
        assert stored["n_steps"] == len(rep["expected_steps"])
        assert stored["anchors"] == [mapped] * len(rep["expected_steps"])
        plan = await orch._effort_plan_row(eid)
        assert [s["n"] for s in plan["steps"]] == rep["expected_steps"]
        assert all(s["anchor"] == mapped for s in plan["steps"])
        assert len(orch.harness.wakes) == 2 and all(w["plan_only"] for w in orch.harness.wakes)
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("rep", CORPUS, ids=IDS)
async def test_b1_the_real_gate_approves_tagged_real_plans_first_time(db_url, rep):
    orch, db = await _orch(db_url)
    try:
        await orch.projects.add("gym", "https://github.com/acme/gym.git")
        eid, chan, root = await orch.router.open_effort("feat", project="gym")
        goal = "- fix the queue connection leak\n- make inference shedding visible"
        await orch.charters.set_goal(eid, goal, created_by="po")
        text, want = _tagged(rep["text"])
        orch.harness.output_queue.append(text)
        assert await orch._worker_plan_gate(eid, chan, root, goal, None, None, None, None) is not None
        ev = await _kinds(orch, eid)
        assert [k for k, _ in ev].count("plan_steps_undeclared") == 0
        stored = [p for k, p in ev if k == "plan_steps_stored"][-1]
        assert stored["anchors"] == want
        assert len(orch.harness.wakes) == 1
    finally:
        await _shutdown(orch, db)
