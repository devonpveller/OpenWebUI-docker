"""F34 — a drain worker's `ESCALATE:` (out-of-scope handoff) is resolved AUTONOMOUSLY and never
mistaken for a delivery.

Before F34, an ESCALATE turn (no new commits, because the worker refused out-of-scope work) fell
through to `_recover_stale_delivery`, which read "no new commits + build passes" as "the requested
change was already in place" and archived the task though the work was never done (gym-037). F34
intercepts the ESCALATE on the drain result path and resolves it the dark-factory way — most
autonomous first:
  1. ROUTE to an existing adjacent scope (the org's decomposition already owns it somewhere), else
  2. DECOMPOSE — create the scope the work belongs to and re-file it for the tier walk, else
  3. LOG it richly (`escalation_unresolved`) + keep the task open. A temporary INSTRUMENTED backstop
     (the log is the data for removing even this), NOT a freeze, NOT "already in place", NOT a drop.
Fakes only.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import EscalationVerdict
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/acme/gym.git"


async def _orch(db_url, **over):
    kwargs = dict(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", drain_tier_walk=True,
    )
    kwargs.update(over)
    settings = Settings(**kwargs)
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=FakeHarness())
    await orch.setup()
    return orch, db


async def _effort(orch, goal="a todo cli that adds lists and completes todos"):
    await orch.projects.add("gym", REPO)
    eid, chan, root = await orch.router.open_effort("feat", project="gym")
    await orch.charters.set_goal(eid, goal, created_by="po")
    return eid, chan, root


# ── tier 1: route the handoff to an existing adjacent scope (no human) ─────────
async def test_escalation_routes_to_an_existing_scope(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        node = await orch._ensure_scope_node(eid)
        await orch.decompose_scope(node, [
            ("storage", "json data storage load and save the todo file"),
            ("cli parsing", "command line argument parsing and dispatch of subcommands")])
        out = "ESCALATE: decouple the command line argument parsing and dispatch layer"
        await orch._handle_drain_escalation(eid, chan, root, out)
        assert await orch._event_count(eid, "escalation_routed") >= 1
        assert await orch._event_count(eid, "escalation_unresolved") == 0
        # F34.2 — a routed escalation CONTINUES the drain (doesn't halt like a human-stop → gym-040 stall)
        assert await orch._event_count(eid, "escalation_drain_continued") == 1
    finally:
        await db.dispose()


# ── tier 2: no scope owns it → the org DECOMPOSES one autonomously (no human) ──
async def test_escalation_decomposes_a_new_scope_when_unrouted(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch)
        await orch._ensure_scope_node(eid)     # root exists but is NOT decomposed → nothing to route into
        out = "ESCALATE: add an interactive keyboard TUI rendering widget interface"
        await orch._handle_drain_escalation(eid, chan, root, out)
        assert await orch._event_count(eid, "escalation_decomposed") >= 1
        assert await orch._event_count(eid, "escalation_unresolved") == 0
        assert await orch._event_count(eid, "escalation_drain_continued") == 1   # F34.2 — keep draining
        # a fresh scope now owns the escalated work; a task was filed into it (open, for the tier walk)
        open_bodies = [t["body"] for t in await orch.list_open_tasks(effort_id=eid)]
        assert any("keyboard" in b.lower() or "tui" in b.lower() for b in open_bodies)
    finally:
        await db.dispose()


# ── tier 3: unplaceable → LOGGED (instrumented backstop), never frozen/closed ──
async def test_unplaceable_escalation_is_logged_not_frozen(db_url):
    orch, db = await _orch(db_url, drain_tier_walk=False)   # no scope tree → can't route or decompose
    try:
        eid, chan, root = await _effort(orch)
        out = "ESCALATE: a genuinely irreducible dependency the org cannot itself provide"
        await orch._handle_drain_escalation(eid, chan, root, out)
        assert await orch._event_count(eid, "escalation_unresolved") == 1   # the DATA to learn from
        assert await orch._event_count(eid, "escalation_routed") == 0
        assert await orch._event_count(eid, "escalation_decomposed") == 0
        # autonomy: a LOG + FYI, NOT a freeze — the effort is not blocked on a human decision
        assert await orch.gate.state_of(eid) != "frozen"
    finally:
        await db.dispose()


# ── the decompose guard: an escalation that only PARAPHRASES the scope is declined ─
async def test_paraphrase_escalation_is_declined_to_the_backstop(db_url):
    orch, db = await _orch(db_url)
    try:
        eid, chan, root = await _effort(orch, goal="add list and complete todos")
        await orch._ensure_scope_node(eid)
        # every content word is already in the parent scope → not a genuinely narrower scope (P17 F9)
        out = "ESCALATE: add list and complete todos"
        await orch._handle_drain_escalation(eid, chan, root, out)
        assert await orch._event_count(eid, "escalation_scope_declined") >= 1
        assert await orch._event_count(eid, "escalation_decomposed") == 0
        assert await orch._event_count(eid, "escalation_unresolved") == 1
    finally:
        await db.dispose()


# ── F34.1: the classifier LEDGER + human-gated-on-autonomous ──────────────────
async def test_autonomous_verdict_is_logged_recoverable_not_escalated_to_human(db_url):
    """A verdict the org can resolve itself → logged as an automation target (`escalation_recoverable`),
    NO human touch. This is how the human path shrinks: the classifier keeps the operator out of the
    loop for anything the org should own."""
    orch, db = await _orch(db_url, drain_tier_walk=False)   # route+decompose off → straight to tier 3
    try:
        eid, chan, root = await _effort(orch, goal="a polished todo cli")
        orch.models._client.queue_structured(EscalationVerdict(
            category="scope_handoff", requires="belongs to the CLI dispatch layer",
            suggested_action="re-file to the cli scope", autonomous=True))
        await orch._handle_drain_escalation(eid, chan, root, "ESCALATE: decouple command dispatch")
        assert await orch._event_count(eid, "escalation_classified") == 1     # the ledger
        assert await orch._event_count(eid, "escalation_recoverable") == 1    # automation target
        assert await orch._event_count(eid, "escalation_unresolved") == 0     # NO human touch
    finally:
        await db.dispose()


async def test_needs_human_verdict_reaches_the_operator_with_evidence(db_url):
    """Only a verdict the org genuinely CAN'T resolve reaches the operator — and it carries WHAT it
    needs. The classifier is context-isolated: it sees the North Star + the escalated work."""
    orch, db = await _orch(db_url, drain_tier_walk=False)
    try:
        eid, chan, root = await _effort(orch, goal="NORTHSTAR-TODO a polished cli")
        orch.models._client.queue_structured(EscalationVerdict(
            category="needs_human", requires="a GitHub deploy credential the org cannot obtain",
            autonomous=False))
        await orch._handle_drain_escalation(eid, chan, root, "ESCALATE: I need a deploy credential")
        assert await orch._event_count(eid, "escalation_classified") == 1
        assert await orch._event_count(eid, "escalation_unresolved") == 1
        assert await orch._event_count(eid, "escalation_recoverable") == 0
        # context-isolation: the classifier saw the North Star + the escalated work, nothing else
        user = orch.models._client.calls[-1]["user"]
        assert "NORTHSTAR-TODO" in user and "credential" in user
    finally:
        await db.dispose()
