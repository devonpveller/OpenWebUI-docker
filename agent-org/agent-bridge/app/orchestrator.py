"""orchestrator — wires the SRP modules into the running bridge (PLAN §3.1.1/§5.1).

The orchestrator is thin glue: it owns no safety logic itself (that lives in the modules)
but it composes the flow — post CONCERNs to #mgmt, parse operator decisions, run the
sampled monitor, and route inbound chat events to wakes/decisions. Keeping it thin is the
"Thinnest Viable Platform" discipline: features must not accrete into the brake (§3.1.1).
"""

from __future__ import annotations

import logging
import random
import re

from .adapters.chat import ChatAdapter
from .config import Settings
from .db import Database
from .modules.audit_sink import AuditSink
from .modules.charters import Charters
from .modules.event_gateway import EventGateway
from .modules.floor_guard import FloorGuard
from .modules.governance_gate import GovernanceGate
from .modules.learning_loop import LearningLoop
from .modules.model_router import ModelRouter
from .modules.planner import Planner
from .modules.profiles import ProfileRegistry
from .modules.roles import RoleAuthority
from .modules.router import Router
from .modules.scheduler import Scheduler
from .modules.scope_ledger import ScopeLedger
from .modules.stop_gates import StopGates
from .models import GlobalState
from .schemas import Concern, Decision, Level, MonitorVerdict, Trigger
from .worker.harness import FakeHarness, LittleCoderHarness, WorkerHarness

log = logging.getLogger("agent_bridge.orchestrator")

# Plain-post operator command grammar (OD-5 — structured plain posts before a plugin).
_DECISION_RE = re.compile(r"^\s*(approve|modify|abort)\s+(\S+)\s*(.*)$", re.I)
_KILL_RE = re.compile(r"^\s*(kill|unkill)\s*$", re.I)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        chat: ChatAdapter,
        *,
        model_client=None,
        harness: WorkerHarness | None = None,
    ) -> None:
        self.s = settings
        self.db = db
        self.chat = chat

        self.audit = AuditSink(db, settings)
        self.gate = GovernanceGate(db, self.audit)
        self.scope = ScopeLedger(db, self.audit)
        self.scheduler = Scheduler(db, self.gate, self.audit, settings.max_concurrent_workers)
        self.profiles = ProfileRegistry(db, settings.profiles_dir)
        self.models = ModelRouter(settings, self.profiles, client=model_client)
        self.charters = Charters(db, settings, self.audit)
        self.floor_guard = FloorGuard(self.scope)
        self.planner = Planner(db, self.models, self.audit)
        self.stop_gates = StopGates(db, self.models, self.audit)
        self.learning = LearningLoop(db, self.audit)
        self.roles = RoleAuthority(self.gate, self.scope)

        self.harness: WorkerHarness = harness or (
            FakeHarness()
            if settings.chat_adapter == "fake"
            else LittleCoderHarness(settings.worker_poll_interval_s, settings.worker_poll_timeout_s)
        )
        self.router = Router(
            db, settings, self.gate, self.scheduler, self.harness, chat, self.audit,
            context_builder=self.charters.build_context,
        )
        self.events = EventGateway(db, chat, self.handle_event)
        self._mgmt_channel_id: str | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def setup(self) -> None:
        await self.db.create_all()
        async with self.db.session_factory() as s:
            if await s.get(GlobalState, 1) is None:
                s.add(GlobalState(id=1, kill_switch=False))
                await s.commit()
        await self.profiles.load_from_disk()
        await self.charters.seed_floor_from_disk()
        await self.scheduler.register_from_urls(self.s.worker_instance_urls)
        # #mgmt is where the Human Operator <-> PO <-> PM converse (§7); track it for events.
        try:
            self._mgmt_channel_id = await self.chat.ensure_channel(self.s.mgmt_channel)
            self.events.track_channel(self._mgmt_channel_id)
        except Exception as exc:  # noqa: BLE001 - chat may be down at boot; retried on demand
            log.warning("mgmt channel not ready at setup: %s", exc)

    async def mgmt_channel_id(self) -> str:
        if self._mgmt_channel_id is None:
            self._mgmt_channel_id = await self.chat.ensure_channel(self.s.mgmt_channel)
            self.events.track_channel(self._mgmt_channel_id)
        return self._mgmt_channel_id

    # ── CONCERN + freeze (§3) ─────────────────────────────────────────────────
    async def raise_concern(
        self,
        effort_id: str,
        trigger: Trigger,
        concern: Concern,
        *,
        actor: str = "pm",
        level: Level | None = None,
    ) -> Concern:
        """Freeze the effort (machine A), force its agents out of computing (machine B),
        and post the intent-framed CONCERN to #mgmt."""
        result = await self.gate.freeze(effort_id, trigger, concern, actor=actor, level=level)
        await self.scheduler.enforce_freeze(effort_id)
        await self._post_concern(effort_id, trigger, result)
        return result

    async def _post_concern(self, effort_id: str, trigger: Trigger, concern: Concern) -> None:
        mgmt = await self.mgmt_channel_id()
        lvl = await self.gate.state_of(effort_id)
        body = (
            f"🚩 **CONCERN** — effort `{effort_id}` FROZEN ({trigger.value})\n"
            f"**Intent:** {concern.intent_thread}\n"
            f"**Surfaced:** {concern.what_surfaced}\n"
            f"**Why it matters:** {concern.intent_of_change}\n"
            + "".join(
                f"\n- **Option:** {o.action} → _{o.effect_on_outcome}_ (risk: {o.risk})"
                for o in concern.options
            )
            + f"\n**PM recommends:** {concern.pm_recommendation}\n"
            f"**Blocked:** {', '.join(concern.blocked_efforts)}\n"
            f"_Reply `approve|modify|abort {effort_id} [note]` to decide (state={lvl})._"
        )
        await self.chat.post(mgmt, body)

    # ── operator decision (§3) ────────────────────────────────────────────────
    async def apply_operator_decision(
        self, effort_id: str, decision: Decision, *, actor_role: str = "human"
    ) -> None:
        await self.gate.clear(effort_id, decision, actor_role=actor_role)
        # On resume, wake any dependency-waiters of this effort (idle-wait DAG).
        await self.scheduler.wake_finished(effort_id)

    # ── cost-tiered supervision (P3.7) — the LLM monitor, SAMPLED ─────────────
    async def monitor_sampled(
        self, effort_id: str, subject_text: str, *, force: bool = False
    ) -> MonitorVerdict | None:
        """Expensive-continuous supervision: run the LLM monitor sampled/triggered (never
        per-token, never via a health-probe — C5). On a detected deviation, freeze."""
        if not force and random.random() > self.s.monitor_sample_rate:
            return None
        verdict = await self.models.structured(
            "pm",
            "You are the PM MONITOR. Judge whether this deliverable/action deviates from "
            "the effort's intent or agreed spec. If it does, name the trigger + level.",
            subject_text,
            MonitorVerdict,
        )
        if verdict.deviates and verdict.trigger:
            concern = Concern(
                intent_thread=f"effort {effort_id}",
                what_surfaced=verdict.rationale or "monitor detected a deviation",
                intent_of_change="a monitored deviation from intent/spec (governance §3)",
                pm_recommendation="review + re-ground",
                blocked_efforts=[effort_id],
            )
            await self.raise_concern(
                effort_id, verdict.trigger, concern, actor="pm-monitor", level=verdict.level
            )
        return verdict

    # ── inbound event routing (P1/P2) ─────────────────────────────────────────
    async def handle_event(self, event: dict) -> None:
        """Route an inbound (non-bot) chat event. In #mgmt, parse operator commands
        (decisions / kill switch). In an effort channel, an @mention is a wake/handoff."""
        channel_id = event.get("channel_id")
        message = event.get("message", "")
        mgmt = await self.mgmt_channel_id()

        if channel_id == mgmt:
            km = _KILL_RE.match(message)
            if km:
                await self.gate.kill_switch(on=km.group(1).lower() == "kill", actor="human")
                await self.chat.post(mgmt, f"✅ kill switch {'engaged' if km.group(1).lower()=='kill' else 'released'}")
                return
            dm = _DECISION_RE.match(message)
            if dm:
                verb, effort_id, note = dm.group(1).lower(), dm.group(2), dm.group(3).strip()
                try:
                    await self.apply_operator_decision(
                        effort_id, Decision(decision=verb, note=note), actor_role="human"
                    )
                    await self.chat.post(mgmt, f"✅ `{effort_id}` {verb} applied.", thread_id=event.get("thread_id"))
                except Exception as exc:  # noqa: BLE001
                    await self.chat.post(mgmt, f"⚠️ could not {verb} `{effort_id}`: {exc}")
                return
            return

        # Effort channel: resolve the effort + wake a worker on the referenced thread.
        effort_id = await self.router.resolve_effort_by_channel(channel_id) if channel_id else None
        if not effort_id:
            return
        thread_id = event.get("thread_id") or event.get("id")
        # Wake-storm guard on WORK chatter (brake channel is exempt).
        await self.router.record_wake(effort_id, target="worker", kind="work")
        if await self.router.wake_storm_tripped(effort_id):
            concern = Concern(
                intent_thread=f"effort {effort_id}",
                what_surfaced="wake-storm rate cap exceeded on work chatter",
                intent_of_change="a runaway hand-off loop threatens the org's stability (§5)",
                pm_recommendation="pause and inspect the loop",
                blocked_efforts=[effort_id],
            )
            await self.raise_concern(effort_id, Trigger.wake_storm, concern, actor="bridge")
            return
        await self.router.wake(
            effort_id, role="worker-default", thread_id=thread_id, channel_id=channel_id,
            instruction=message,
        )
