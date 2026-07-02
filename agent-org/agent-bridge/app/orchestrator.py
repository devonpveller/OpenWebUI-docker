"""orchestrator — wires the SRP modules into the running bridge (PLAN §3.1.1/§5.1).

The orchestrator is thin glue: it owns no safety logic itself (that lives in the modules)
but it composes the flow — post CONCERNs to #mgmt, parse operator decisions, run the
sampled monitor, and route inbound chat events to wakes/decisions. Keeping it thin is the
"Thinnest Viable Platform" discipline: features must not accrete into the brake (§3.1.1).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re

from .adapters.chat import ChatAdapter
from .config import Settings
from .db import Database
from .modules.audit_sink import AuditSink
from .modules.charters import Charters
from .modules.comms_router import CommsRouter, Intent
from .modules.event_gateway import EventGateway
from .modules.floor_guard import FloorGuard
from .modules.governance_gate import GovernanceGate
from .modules.learning_loop import LearningLoop
from .modules.model_router import ModelRouter
from .modules.planner import Planner
from .modules.profiles import ProfileRegistry
from .modules.roles import RoleAuthority
from .modules.router import Router, slugify
from .modules.scheduler import Scheduler
from .modules.scope_ledger import ScopeLedger
from .modules.stop_gates import StopGates
from .models import GlobalState
from .schemas import Concern, Decision, Level, MonitorVerdict, OperatorIntent, Trigger
from .worker.harness import FakeHarness, LittleCoderHarness, WorkerHarness

log = logging.getLogger("agent_bridge.orchestrator")

# Plain-post operator command grammar (OD-5 — structured plain posts before a plugin).
_DECISION_RE = re.compile(r"^\s*(approve|modify|abort)\s+(\S+)\s*(.*)$", re.I)
_KILL_RE = re.compile(r"^\s*(kill|unkill)\s*$", re.I)
# A leading @mention token to strip so "@bot-pm /effort x" parses as "/effort x".
_MENTION_RE = re.compile(r"^\s*@[\w.\-]+\s*")
# A message is a "control" message (privileged, always answered) if it's a slash command
# or one of the bare decision/kill verbs.
_CONTROL_RE = re.compile(r"^(/|approve\b|modify\b|abort\b|kill\b|unkill\b)", re.I)

_PO_NL_SYS = (
    "You are the PO (Project Overseer) — the human operator's conversational counterpart in a "
    "governed multi-agent coding org. Read the operator's natural-language message and reply "
    "helpfully and concisely in the first person (you own the 'intent thread'). Classify it:\n"
    "- request: they want new work done → set effort_name to a short kebab-case slug (it becomes "
    "a thread in the project channel). If the "
    "request is vague or high-blast-radius, ASK a clarifying question in your reply instead of "
    "guessing (governance F5 — surfacing a question is cheaper than a misaligned worker).\n"
    "- status: they're asking what's going on.\n"
    "- steering: adjusting the direction of an existing effort → set effort_id + steering.\n"
    "- decision: approve/modify/abort a paused effort → set effort_id + decision (you interpret "
    "it; the human still confirms with an explicit command — never claim you executed it).\n"
    "- question / chitchat otherwise.\n"
    "Only set action fields when clearly warranted. Never claim to have taken an irreversible "
    "action. Keep replies short and human."
)

_HELP = (
    "**agent-bridge** — governed multi-agent orchestration. You can talk to me in **plain "
    "language** (I'm your PO — tell me what you want built and I'll scope it), or use commands:\n"
    "- `/help` — this message\n"
    "- `/effort <name>` — open a work effort as a **thread** in its project channel\n"
    "- `/status [effort_id]` — gate state of all efforts (or one)\n"
    "- `approve|modify|abort <effort_id> [note]` — decide an open CONCERN\n"
    "- `/kill` / `/unkill` — global kill switch (freeze/release the whole fleet)\n"
    "Each effort is a **thread** in its `#proj-<project>` channel — reply in the thread to wake "
    "its worker; watch the work stream there. Escalations come to **#mgmt** and their resolution "
    "is echoed back into the effort thread so you get closure."
)


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
        # Deterministic intent -> destination routing (COMMS-MODEL §2). Every bridge-emitted
        # message goes through here so no module picks a channel inline (governance §3.5).
        self.comms = CommsRouter(
            chat, settings,
            mgmt_resolver=self.mgmt_channel_id,
            effort_thread_resolver=self.router.effort_thread,
            on_channel=self.events.track_channel,
        )
        self._mgmt_channel_id: str | None = None
        self._bot_name: str | None = None
        self._mgmt_warned = False
        self._operator_ids: set[str] = set()  # operators seen in #mgmt (for channel invites)
        self._bg_tasks: set[asyncio.Task] = set()  # in-flight delegations

    def _spawn(self, coro) -> None:
        """Run a coroutine in the background, keeping a reference so it isn't GC'd."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

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
        stale = await self.scheduler.reset_stale()  # clear any wedged 'computing' from a crash
        if stale:
            log.info("reset %d stale worker(s) to idle on startup", stale)
        self._bot_name = getattr(self.chat, "username", None)
        # #mgmt is where the Human Operator <-> PO <-> PM converse (§7); track it for events.
        mgmt = await self.mgmt_channel_id()
        # The permanent function channels (#incidents, #suggestions) exist for the org's lifetime
        # (COMMS-MODEL §4 / CM.5). Create-or-get them at boot; comms tracks them for catch-up.
        try:
            await self.comms.ensure_function_channels()
        except Exception as exc:  # noqa: BLE001 - platform may not be ready yet; retried lazily
            log.warning("function channels not ready at boot (will retry): %s", exc)
        if mgmt and self.s.chat_adapter != "fake":
            # A one-line boot ack so the operator can see the bridge is live (best-effort).
            try:
                await self.chat.post(mgmt, "✅ agent-bridge online — try `/help`")
            except Exception:  # noqa: BLE001
                pass

    def _project_for(self, repo: str | None = None) -> str:
        """The project slug an effort belongs to = its repo (COMMS-MODEL §4 project resolution).
        Falls back to AO_DEFAULT_REPO, then to the sandbox project for throwaway work."""
        target = repo or (self.s.default_repo or "")
        return slugify(target) if target else self.s.default_project

    async def _track_operator(self, user_id: str | None) -> None:
        """Remember an operator seen in #mgmt and pull them into the function channels so those
        appear in their sidebar (public channels you haven't joined are hidden). Best-effort."""
        if not user_id or user_id in self._operator_ids:
            return
        self._operator_ids.add(user_id)
        for name in (self.s.incidents_channel, self.s.suggestions_channel):
            try:
                cid = await self.chat.ensure_channel(name)
                await self.chat.add_member(cid, user_id)
            except Exception as exc:  # noqa: BLE001
                log.debug("add operator %s to %s failed: %s", user_id, name, exc)

    async def mgmt_channel_id(self) -> str | None:
        """Resolve #mgmt lazily. Returns None (not raising) if the chat platform isn't ready
        yet — e.g. the bot isn't on a team — so a transient state can't crash the event loop.
        Self-heals: once resolvable (operator adds the bot to the team), it caches + tracks."""
        if self._mgmt_channel_id is None:
            try:
                self._mgmt_channel_id = await self.chat.ensure_channel(self.s.mgmt_channel)
                self.events.track_channel(self._mgmt_channel_id)
                self._mgmt_warned = False
            except Exception as exc:  # noqa: BLE001
                if not self._mgmt_warned:
                    log.warning("mgmt channel not ready (will retry): %s", exc)
                    self._mgmt_warned = True
                return None
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
        """Freeze the effort (machine A), force its agents out of computing (machine B), post
        the intent-framed CONCERN to #mgmt (decision surface), and raise the up-signal into the
        effort thread (escalation ladder, COMMS-MODEL §3 rule 1)."""
        result = await self.gate.freeze(effort_id, trigger, concern, actor=actor, level=level)
        await self.scheduler.enforce_freeze(effort_id)
        await self.router.update_effort_card(effort_id, "frozen")  # CM.6 live card status
        await self._post_concern(effort_id, trigger, result)
        # Escalation ladder (CM.3): the RECORD is decided in #mgmt, but the effort thread's
        # followers get the pointer that it was escalated — the "decide-private/record-public"
        # split (§3 rule 2). The resolution comes back down here on clear (CM.4).
        await self.comms.post(
            Intent.escalation,
            f"🚩 **Escalated** — this effort is **frozen** pending an operator decision "
            f"(`{trigger.value}`). ↑ routed up the ladder to **#mgmt**; the decision will be "
            f"posted back here when it's made.",
            effort_id=effort_id,
        )
        return result

    async def _post_concern(self, effort_id: str, trigger: Trigger, concern: Concern) -> None:
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
            f"_Context: this effort's live thread is in its project channel; the resolution is "
            f"echoed back there on decision._\n"
            f"_Reply `approve|modify|abort {effort_id} [note]` to decide (state={lvl})._"
        )
        posted = await self.comms.post(Intent.concern, body, effort_id=effort_id)
        if posted is None:
            # The freeze already happened + is audited; we just can't surface it to chat yet
            # (#mgmt unresolved — e.g. bot not on a team). The effort stays frozen (fail-safe).
            log.warning("effort %s frozen but #mgmt unresolved — CONCERN not posted (logged only)", effort_id)

    # ── operator decision (§3) + bring-the-audience-back-down closure (CM.4) ──
    async def apply_operator_decision(
        self, effort_id: str, decision: Decision, *, actor_role: str = "human"
    ) -> None:
        await self.gate.clear(effort_id, decision, actor_role=actor_role)
        # On resume, wake any dependency-waiters of this effort (idle-wait DAG).
        await self.scheduler.wake_finished(effort_id)
        # ⭐ "Always bring the audience back down" (COMMS-MODEL §3 rule 3): echo the resolution
        # into the ORIGINATING effort thread so anyone following the work gets closure without
        # opening #mgmt. The decision RECORD already lives in #mgmt + the audit trail (§3 rule 2).
        aborted = decision.decision == "abort"
        note = f" — _{decision.note}_" if decision.note else ""
        closure = (
            f"⛔ **Aborted** by the operator{note}. This effort will not resume."
            if aborted else
            f"✅ **Operator {decision.decision}d** — resuming{note}."
        )
        await self.comms.post(Intent.closure, closure, effort_id=effort_id)
        await self.router.update_effort_card(effort_id, "aborted" if aborted else "active")

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

    # ── natural-language intake (the conversational PO surface) ───────────────
    async def nl_intake(self, message: str, channel_id: str, *, user_id: str | None = None) -> None:
        """Route a natural-language operator message to the PO agent, which interprets intent
        and replies conversationally. Non-destructive actions (open an effort, apply steering,
        report status) are executed; safety decisions are NOT auto-run from fuzzy NL — the PO
        asks for the explicit, auditable command (governance §3). Runs on the PO profile's lane
        (local qwen36-27b by default; cloud if P0.5 mandated)."""
        efforts = await self.gate.snapshot()
        ctx = "; ".join(f"{e['id']}={e['state']}" for e in efforts) or "none"
        try:
            intent = await self.models.structured(
                "po", _PO_NL_SYS, f"OPERATOR MESSAGE:\n{message}\n\nCURRENT EFFORTS: {ctx}",
                OperatorIntent,
            )
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the loop
            log.warning("nl_intake model call failed: %s", exc)
            await self.chat.post(
                channel_id,
                "I couldn't parse that just now — you can also use `/help` for commands.",
            )
            return

        reply = (intent.reply or "").strip()
        if intent.kind == "request" and intent.effort_name:
            try:
                project = self._project_for()
                # An effort is a THREAD in its project channel (COMMS-MODEL §4) — no per-effort
                # channel, so the sidebar never grows with task volume.
                eid, chan, root = await self.router.open_effort(
                    intent.effort_name, project=project, goal=message
                )
                self.events.track_channel(chan)
                # Add the requester to the PROJECT channel ONCE (not per effort) so it appears in
                # their sidebar and they can follow the threads they care about.
                if user_id:
                    await self.chat.add_member(chan, user_id)
                # Dispatch a worker in the BACKGROUND so the PO replies immediately; the worker's
                # activity streams into the effort THREAD; completion reports back to #mgmt.
                self._spawn(self.delegate(eid, chan, root, message))
                reply += (
                    f"\n\n_Opened **{eid}** as a thread in **#proj-{project}** (added to your "
                    f"channels) and dispatched a worker — watch it live in that thread; I'll "
                    f"summarize back here when done._"
                )
            except Exception as exc:  # noqa: BLE001
                reply += f"\n\n_(couldn't open that effort: {exc})_"
        elif intent.kind == "steering" and intent.effort_id and intent.steering:
            try:
                await self.charters.set_steering(intent.effort_id, intent.steering, actor="po")
                reply += f"\n\n_Steering updated for `{intent.effort_id}`._"
            except Exception as exc:  # noqa: BLE001
                reply += f"\n\n_(couldn't update steering: {exc})_"
        elif intent.kind == "decision" and intent.effort_id and intent.decision:
            # SAFETY: never auto-clear a gate from fuzzy NL — require the explicit command (§3).
            reply += (
                f"\n\n_To **{intent.decision}** `{intent.effort_id}`, confirm with:_ "
                f"`{intent.decision} {intent.effort_id}`"
            )
        elif intent.kind == "status":
            reply += "\n\n" + (
                "\n".join(f"- `{e['id']}` — **{e['state']}**" for e in efforts)
                if efforts else "_No efforts yet — tell me what you'd like built._"
            )

        await self.chat.post(channel_id, reply or "…")

    async def delegate(
        self, effort_id: str, channel_id: str, root_post_id: str, goal: str,
        *, repo: str | None = None,
    ) -> None:
        """Set the effort's goal (constraints inline, §4.3) and dispatch a worker; all activity
        streams into the effort THREAD (`root_post_id`). Runs as a background task. `repo` focuses
        the worker first (real projects); omit for a pre-focused/throwaway pool (`AO_DEFAULT_REPO`)."""
        repo = repo or (self.s.default_repo or None)
        try:
            await self.charters.set_goal(effort_id, goal, created_by="po")
            await self.comms.post(
                Intent.effort_dispatch, f"⏳ **{effort_id}** — worker dispatched. Working…",
                effort_id=effort_id,
            )
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root_post_id, channel_id=channel_id,
                session_id=effort_id, instruction=goal, repo=repo,
            )
            await self._report_completion(effort_id, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("delegate failed for %s: %s", effort_id, exc)
            await self.comms.post(
                Intent.worker_activity, f"⚠️ delegation error on `{effort_id}`: {exc}",
                effort_id=effort_id,
            )
            await self.router.update_effort_card(effort_id, "error")

    async def _report_completion(self, effort_id: str, result) -> None:
        """Route a finished effort per the §2 closure row: closure DOWN into the effort thread
        (bring the audience back down) + a summary UP to #mgmt. A non-`done` end escalates."""
        if result is None:
            await self.comms.post(
                Intent.worker_activity,
                "⚠️ couldn't dispatch a worker (effort frozen or no free slot).",
                effort_id=effort_id,
            )
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}**: couldn't dispatch a worker (frozen or no free slot).",
            )
            return
        head = (result.output or "").strip().splitlines()[0][:200] if result.output else result.status
        if result.ok:
            await self.comms.post(
                Intent.closure,
                "✅ worker finished (**done**). Review the changes in the worker's workspace; "
                "nothing is pushed (the floor blocks irreversible actions).",
                effort_id=effort_id,
            )
            await self.router.update_effort_card(effort_id, "done")
            await self.comms.post(
                Intent.operator_reply,
                f"✅ **{effort_id}** finished (**done**): {head}\n"
                f"_Full trace in its project-channel thread._",
            )
        else:
            await self._escalate_worker_failure(effort_id, result)

    async def _escalate_worker_failure(self, effort_id: str, result) -> None:
        """A worker that ended non-`done` climbs the escalation ladder (CM.3). A refusal/rejection
        is a hard-gate trigger reaching the human (F3 — never routed around); other non-`done`
        ends are raised up the ladder but don't hard-freeze (no gate thrash on ordinary failure)."""
        head = (result.output or "").strip()[:200] or result.status
        if result.status == "rejected":
            concern = Concern(
                intent_thread=f"effort {effort_id}",
                what_surfaced=f"worker refused/rejected the task: {head}",
                intent_of_change="a refusal must block and reach the human, never be routed around (F3)",
                pm_recommendation="review the refusal with the operator",
                blocked_efforts=[effort_id],
            )
            # raise_concern posts the in-thread escalation + #mgmt CONCERN + freezes + sets card.
            await self.raise_concern(effort_id, Trigger.refusal, concern, actor="bridge")
            return
        await self.comms.post(
            Intent.escalation,
            f"❌ worker ended **{result.status}** — {head}\n↑ raised to the PM/operator.",
            effort_id=effort_id,
        )
        await self.router.update_effort_card(effort_id, "error")
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}** ended **{result.status}** — see its project-channel thread. {head}",
        )

    async def record_suggestion(
        self, worker: str, text: str, effort_id: str | None = None
    ) -> str:
        """Record a worker suggestion (learning loop, §6) AND surface it in #suggestions (CM.5).
        The learning loop stays chat-agnostic; the surfacing is the router's job."""
        sig = await self.learning.add_suggestion(worker, text, effort_id)
        tag = f" (effort `{effort_id}`)" if effort_id else ""
        await self.comms.post(
            Intent.suggestion, f"💡 **suggestion** from `{worker}`{tag}:\n> {text[:800]}"
        )
        return sig

    def _effort_name_from(self, text: str) -> str:
        """Derive a short effort slug from a free-text request (for a top-level project post)."""
        parts = slugify(text).split("-")
        return "-".join(parts[:4]) or "task"

    # ── inbound event routing (P1/P2 + COMMS-MODEL §4 taxonomy) ───────────────
    async def handle_event(self, event: dict) -> None:
        """Route an inbound (non-bot) chat event under the comms model (channel = project,
        effort = thread):

        - **System posts** (joins/adds/etc.) are ignored.
        - A **control message** (slash command / bare decision/kill verb) is handled and answered
          wherever the operator sends it — the deterministic, auditable surface.
        - In **#mgmt**, any other natural-language message goes to the **PO agent** (`nl_intake`).
        - A **reply inside a known effort thread** wakes that effort's worker (continuation).
        - A **top-level @mention in a `#proj-<slug>` channel** opens a NEW effort in that project.
        - A mention anywhere else gets a help reply.
        """
        if str(event.get("type") or "").startswith("system"):
            return  # channel joins/leaves/etc. — not a message to act on
        channel_id = event.get("channel_id")
        raw = event.get("message", "")
        thread_id = event.get("thread_id")
        post_id = event.get("id")
        user_id = event.get("user_id")
        stripped = _MENTION_RE.sub("", raw).strip()
        mentioned = bool(self._bot_name and f"@{self._bot_name}" in raw)

        # Control surface — privileged (only the human posts; bot posts are filtered upstream).
        if _CONTROL_RE.match(stripped):
            await self._track_operator(user_id)
            await self._handle_command(stripped, channel_id, thread_id, user_id=user_id)
            return

        mgmt = await self.mgmt_channel_id()
        if channel_id == mgmt:
            await self._track_operator(user_id)
            if stripped:
                # talk to the PO in plain language; user_id lets it add the requester to projects
                await self.nl_intake(stripped, channel_id, user_id=user_id)
            return

        # A reply inside a known effort thread wakes that effort's worker (continuation/steering).
        is_reply = bool(thread_id) and thread_id != post_id
        effort_id = await self.router.resolve_effort_by_thread(thread_id) if is_reply else None
        if effort_id:
            loc = await self.router.effort_thread(effort_id)
            if not loc:
                return
            proj_channel, root = loc
            # Wake-storm guard on WORK chatter (brake channel is exempt).
            await self.router.record_wake(effort_id, target="worker", kind="work")
            if await self.router.wake_storm_tripped(effort_id):
                # Operational event -> #incidents (CM.5), AND freeze + surface the CONCERN (§3).
                await self.comms.post(
                    Intent.incident,
                    f"🌩️ **wake-storm** on `{effort_id}` — work-chatter rate cap exceeded; "
                    f"freezing the effort to inspect the loop.",
                )
                concern = Concern(
                    intent_thread=f"effort {effort_id}",
                    what_surfaced="wake-storm rate cap exceeded on work chatter",
                    intent_of_change="a runaway hand-off loop threatens the org's stability (§5)",
                    pm_recommendation="pause and inspect the loop",
                    blocked_efforts=[effort_id],
                )
                await self.raise_concern(effort_id, Trigger.wake_storm, concern, actor="bridge")
                return
            # Keep --session continuity: the effort thread's session id is stable (== effort id),
            # not the individual reply's post id.
            sess = await self.router.resolve_session(root)
            session_id = sess[1] if sess else effort_id
            await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=proj_channel,
                session_id=session_id, instruction=stripped,
            )
            return

        # A top-level @mention in a project channel opens a NEW effort in that project.
        project = await self.router.resolve_project_by_channel(channel_id) if channel_id else None
        if mentioned and project and stripped:
            try:
                eid, chan, root = await self.router.open_effort(
                    self._effort_name_from(stripped), project=project, goal=stripped
                )
                self.events.track_channel(chan)
                if user_id:
                    await self.chat.add_member(chan, user_id)
                self._spawn(self.delegate(eid, chan, root, stripped))
            except Exception as exc:  # noqa: BLE001
                await self.chat.post(channel_id, f"⚠️ couldn't open an effort here: {exc}")
            return
        if mentioned:
            await self.chat.post(channel_id, _HELP)  # top-level, visible inline

    async def _handle_command(
        self, text: str, channel_id: str | None, thread_id: str | None, *, user_id: str | None = None
    ) -> None:
        """Parse + execute an operator command; ALWAYS replies to the originating channel so the
        operator gets feedback (even usage errors). `text` has the @mention prefix stripped."""
        async def reply(msg: str) -> None:
            # Top-level channel post (NOT a thread reply): operator command responses must be
            # visible inline in #mgmt. Threading is reserved for effort work hand-offs (§7);
            # a threaded reply is hidden under Collapsed Reply Threads (needs a click/refresh).
            if channel_id:
                await self.chat.post(channel_id, msg)

        body = text[1:] if text.startswith("/") else text
        parts = body.split()
        cmd = parts[0].lower() if parts else ""
        args = parts[1:]

        if cmd in ("", "help"):
            await reply(_HELP)
        elif cmd == "effort":
            if not args:
                await reply("usage: `/effort <name>`")
                return
            name = args[0]
            try:
                project = self._project_for()
                effort_id, chan, _root = await self.router.open_effort(name, project=project)
                self.events.track_channel(chan)
                if user_id:
                    await self.chat.add_member(chan, user_id)
                await reply(
                    f"✅ opened effort `{effort_id}` as a thread in `#proj-{project}` "
                    f"(added to your channels) — reply in that thread to wake its worker"
                )
            except Exception as exc:  # noqa: BLE001
                await reply(f"⚠️ could not create effort `{name}`: {exc}")
        elif cmd == "status":
            snap = await self.gate.snapshot()
            if args:
                snap = [e for e in snap if e["id"] == args[0]]
            if not snap:
                await reply("no efforts yet — create one with `/effort <name>`")
            else:
                lines = [
                    f"- `{e['id']}` — **{e['state']}**" + (f" ({e['reason']})" if e["reason"] else "")
                    for e in snap
                ]
                await reply("**Efforts:**\n" + "\n".join(lines))
        elif cmd in ("kill", "unkill"):
            on = cmd == "kill"
            await self.gate.kill_switch(on=on, actor="human")
            await reply(f"✅ kill switch {'engaged — fleet frozen' if on else 'released'}")
        elif cmd in ("approve", "modify", "abort"):
            if not args:
                await reply(f"usage: `{cmd} <effort_id> [note]`")
                return
            effort_id, note = args[0], " ".join(args[1:])
            try:
                await self.apply_operator_decision(
                    effort_id, Decision(decision=cmd, note=note), actor_role="human"
                )
                await reply(f"✅ `{effort_id}` {cmd} applied — state now `{await self.gate.state_of(effort_id)}`")
            except Exception as exc:  # noqa: BLE001
                await reply(f"⚠️ could not {cmd} `{effort_id}`: {exc}")
        else:
            await reply(f"unknown command `/{cmd}` — try `/help`")
