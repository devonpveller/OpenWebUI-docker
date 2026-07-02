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
from .modules.egress import EgressAllowlist
from .modules.event_gateway import EventGateway
from .modules.execution_gate import ExecutionGate
from .modules.floor_guard import FloorGuard
from .modules.governance_gate import GovernanceGate
from .modules.grounding import Grounding, build_grounding
from .modules.learning_loop import LearningLoop
from .modules.model_router import ModelRouter
from .modules.planner import Planner
from .modules.profiles import ProfileRegistry
from .modules.project_context import ProjectContext
from .modules.projects import ProjectRegistry
from .modules.roles import RoleAuthority
from .modules.router import Router, slugify
from .modules.scheduler import Scheduler
from .modules.scope_ledger import ScopeLedger
from .modules.stop_gates import StopGates
from .models import Effort, GlobalState
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
    "- request: they want NEW work done → set effort_name to a short kebab-case slug (it becomes "
    "a thread in the project channel). If they name a project/repo to work on, set `project` to it "
    "(match a KNOWN PROJECT if listed). Write a brief friendly acknowledgement, but do NOT ask "
    "clarifying questions yourself and do NOT claim you started/dispatched anything — a readiness "
    "check runs next and will pause to ask the operator if the request is under-specified "
    "(governance F5 — a question is cheaper than a misaligned worker).\n"
    "- clarification: the operator is ANSWERING a question or ADDING detail to an effort that is "
    "awaiting clarification or already in progress → set kind=clarification, effort_id to that "
    "effort (see AWAITING CLARIFICATION / CURRENT EFFORTS), and put their words in `steering`.\n"
    "- status: they're asking what's going on.\n"
    "- steering: explicitly changing the direction/scope of an existing effort → set effort_id + steering.\n"
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
    "- `/project add <name> <repo-url>` — onboard a repo the org can work on (creates "
    "`#proj-<name>` + allows its git host); `/project list` · `/project remove <name>`\n"
    "- `/egress allow <host|repo-url>` — widen the worker git-egress allowlist; `/egress list`\n"
    "- `/effort <name>` — open a work effort as a **thread** in its project channel\n"
    "- `/status [effort_id]` — gate state of all efforts (or one)\n"
    "- `approve|modify|abort <effort_id> [note]` — decide an open CONCERN\n"
    "- `/risk <effort_id> <routine|irreversible|cross_effort|cascading_refactor>` — set blast radius "
    "(risky ⇒ a dry-run is required before real-code execution)\n"
    "- `/dry-run <effort_id> <pass|fail>` — record the isolated dry-run outcome\n"
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
        grounding: Grounding | None = None,
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
        self.exec_gate = ExecutionGate(db, self.audit)          # P4.0 risk-gated dry-run gate
        self.grounding: Grounding = grounding or build_grounding(settings)
        self.learning = LearningLoop(db, self.audit)
        self.roles = RoleAuthority(self.gate, self.scope)
        # Multi-project registry (COMMS-MODEL §4: channel = project = repo) + the worker git-egress
        # allowlist it drives (remotely managed via /project + /egress in Mattermost).
        self.projects = ProjectRegistry(db, self.audit)
        self.egress = EgressAllowlist(db, self.audit, self.projects, settings.egress_allowlist_file)

        self.harness: WorkerHarness = harness or (
            FakeHarness()
            if settings.chat_adapter == "fake"
            else LittleCoderHarness(settings.worker_poll_interval_s, settings.worker_poll_timeout_s)
        )
        self.router = Router(
            db, settings, self.gate, self.scheduler, self.harness, chat, self.audit,
            context_builder=self.charters.build_context,
        )
        # Stage-1 anchor: a cached read-only repo survey feeds the readiness gate (P3.8) so it
        # reasons from the real codebase instead of guessing. Only surveys when a repo is focused.
        self.project_context = ProjectContext(
            self.router.survey_project, enabled=settings.project_survey_enabled
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
        # Efforts opened but HELD at the readiness gate awaiting operator clarification (P3.8);
        # the operator's next answer resolves them → dispatch. {effort_id: {proj_channel, root, request}}
        self._pending: dict[str, dict] = {}
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
        # Fallback repo (AO_DEFAULT_REPO) → auto-register as a project so it's in the registry +
        # gets a #proj channel; then render the egress allowlist file from all registered hosts.
        if self.s.default_repo:
            try:
                await self.projects.add(self._project_for(), self.s.default_repo, created_by="boot")
            except Exception as exc:  # noqa: BLE001
                log.warning("could not auto-register default repo: %s", exc)
        try:
            await self.egress.sync()  # seed + project hosts → the mounted tinyproxy filter
        except Exception as exc:  # noqa: BLE001
            log.warning("egress allowlist sync failed at boot: %s", exc)
        if mgmt and self.s.chat_adapter != "fake":
            # A one-line boot ack so the operator can see the bridge is live (best-effort).
            try:
                await self.chat.post(mgmt, "✅ agent-bridge online — try `/help`")
            except Exception:  # noqa: BLE001
                pass

    def _project_for(self, repo: str | None = None) -> str:
        """The FALLBACK project slug for a request that names no project: the AO_DEFAULT_REPO slug
        if set, else the sandbox. Named/onboarded projects are resolved via the registry
        (`_resolve_project_slug`); this is only the default."""
        target = repo or (self.s.default_repo or "")
        return slugify(target) if target else self.s.default_project

    async def _resolve_project_slug(self, named: str | None, channel_id: str | None = None) -> str:
        """Resolve which project a request belongs to: an explicitly named/onboarded project wins;
        else the originating #proj-<slug> channel's project; else the fallback (default/sandbox)."""
        if named:
            p = await self.projects.resolve(named)
            if p:
                return p["slug"]
        if channel_id:
            slug = await self.router.resolve_project_by_channel(channel_id)
            if slug:
                return slug
        return self._project_for()

    async def _effort_repo(self, effort_id: str) -> str | None:
        """The repo a worker should be focused on for this effort = its project's repo (registry),
        falling back to AO_DEFAULT_REPO. None ⇒ pre-focused/throwaway pool (no /project clone)."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            proj = e.project if e else None
        if proj:
            repo = await self.projects.repo_for(proj)
            if repo:
                return repo
        return self.s.default_repo or None

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
        pending_ctx = ", ".join(self._pending.keys()) or "none"
        projects = await self.projects.list()
        projects_ctx = ", ".join(f"{p['slug']} ({p['repo_url']})" for p in projects) or "none"
        try:
            intent = await self.models.structured(
                "po", _PO_NL_SYS,
                f"OPERATOR MESSAGE:\n{message}\n\nCURRENT EFFORTS: {ctx}\n"
                f"AWAITING CLARIFICATION: {pending_ctx}\nKNOWN PROJECTS: {projects_ctx}",
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
                # Resolve WHICH project this works on: a named/onboarded project, else the fallback.
                # An effort is a THREAD in its project channel (COMMS-MODEL §4).
                project = await self._resolve_project_slug(intent.project, channel_id)
                eid, chan, root = await self.router.open_effort(
                    intent.effort_name, project=project, goal=message
                )
                self.events.track_channel(chan)
                # Add the requester to the PROJECT channel ONCE (not per effort).
                if user_id:
                    await self.chat.add_member(chan, user_id)
                # Stage 2 readiness gate (P3.8): DON'T guess — if under-specified, ask + HOLD;
                # only dispatch when the request is clear (F5). This replies itself + returns.
                await self._intake_or_dispatch(
                    eid, chan, root, message, reply_prefix=reply, mgmt_channel=channel_id
                )
            except Exception as exc:  # noqa: BLE001
                await self.chat.post(
                    channel_id, (reply + f"\n\n_(couldn't open that effort: {exc})_").strip()
                )
            return
        elif intent.effort_id and intent.kind in ("clarification", "steering"):
            # The operator answered a held question OR added scope to an existing effort. Merge it
            # into the effort's goal and re-run the readiness gate → dispatch when clear. This is
            # the fix for "clarification only updated steering but never did the work".
            eid = intent.effort_id
            loc = await self.router.effort_thread(eid)
            if loc is None:
                await self.chat.post(
                    channel_id, (reply + f"\n\n_(couldn't find effort `{eid}` to update)_").strip()
                )
                return
            proj_channel, root = loc
            addition = (intent.steering or message).strip()
            try:  # record the direction change as a versioned steering edit (audit, §4.2)
                await self.charters.set_steering(eid, addition, actor="po")
            except Exception as exc:  # noqa: BLE001
                log.debug("set_steering(%s) failed: %s", eid, exc)
            # Fold the answer (+ held recommendations) into the goal and dispatch — no second
            # readiness pass (the operator has spoken; don't re-interrogate).
            await self._resume_after_clarification(
                eid, proj_channel, root, addition, reply_prefix=reply, mgmt_channel=channel_id
            )
            return
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

    @staticmethod
    def _risk_from_blast(blast_radius: str) -> str:
        """Map the readiness gate's blast_radius (UX-FLOW Stage 2) to the P4.0 dry-run risk class.
        cross_effort / cascading_refactor ⇒ a dry-run is required; routine ⇒ none."""
        return blast_radius if blast_radius in ("cross_effort", "cascading_refactor") else "routine"

    @staticmethod
    def _render_questions(questions) -> str:
        """A NUMBERED list the operator can address item-by-item; security/ethics questions are
        flagged with their specific concern, others carry the recommended default (operator can
        accept it wholesale). Tolerant of a bare-string question (degraded model output)."""
        lines: list[str] = []
        for i, q in enumerate(questions[:6], 1):
            text = getattr(q, "question", None) or str(q)
            rec = getattr(q, "recommendation", "") or ""
            cat = getattr(q, "category", "feature_intent")
            if cat in ("security", "ethics"):
                lines.append(f"{i}. ⚠️ **[{cat}]** {text}" + (f"\n   _Concern: {rec}_" if rec else ""))
            else:
                lines.append(f"{i}. {text}" + (f"\n   _Recommended: {rec}_" if rec else ""))
        return "\n".join(lines)

    async def _resume_after_clarification(
        self, effort_id: str, proj_channel: str, root: str, answer: str,
        *, reply_prefix: str, mgmt_channel: str,
    ) -> None:
        """The operator answered a held question (or added scope). Fold their answer + the held
        questions' recommended defaults into the goal and DISPATCH — no second readiness pass, so
        we don't re-interrogate once the operator has spoken (respects 'don't over-ask')."""
        pend = self._pending.pop(effort_id, None)
        base = pend["request"] if pend else ((await self.charters.current_goal(effort_id))[1] or "")
        parts = [base] if base else []
        parts.append(f"Operator clarification: {answer}")
        if pend and pend.get("questions"):
            recs = [
                f"- {getattr(q, 'question', str(q))} → {getattr(q, 'recommendation', '')}"
                for q in pend["questions"] if getattr(q, "recommendation", "")
            ]
            if recs:
                parts.append(
                    "Apply these recommended defaults for anything the operator did not override:\n"
                    + "\n".join(recs)
                )
        combined = "\n\n".join(parts).strip()
        await self.charters.set_goal(effort_id, combined, created_by="po")
        self._spawn(self.delegate(effort_id, proj_channel, root, combined))
        await self.chat.post(
            mgmt_channel,
            (f"{reply_prefix}\n\n_Got it — dispatching a worker on **{effort_id}** with your "
             f"clarification. I'll summarize back here when done._").strip(),
        )

    async def _intake_or_dispatch(
        self, effort_id: str, proj_channel: str, root: str, request: str,
        *, reply_prefix: str, mgmt_channel: str,
    ) -> None:
        """Stage 2→4 for a request: run the readiness gate (P3.8), auto-classify blast radius →
        dry-run risk (P4.0), then either HOLD for operator clarification (F5 — don't guess) or
        dispatch a worker. Owns its own #mgmt reply so the caller just returns."""
        await self.charters.set_goal(effort_id, request, created_by="po")
        # Anchor the readiness gate to the existing project (UX-FLOW Stage 1) so it resolves
        # conventions/placement/language itself instead of asking about them. When a real repo is
        # focused, inject a cached read-only survey of the actual codebase; else conventions-only.
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            project = (e.project if e and e.project else None) or self._project_for()
        repo = await self._effort_repo(effort_id) or ""
        workspace_ctx = f"existing project: #{project}" + (f" (repo: {repo})" if repo else "")
        if repo:
            summary = await self.project_context.ensure(project, repo)
            if summary:
                workspace_ctx += f"\n\nPROJECT SUMMARY (survey of the actual codebase):\n{summary}"
        verdict = None
        try:
            verdict = await self.planner.readiness_gate(effort_id, request, workspace_ctx)
        except Exception as exc:  # noqa: BLE001 - a model hiccup must not wedge intake
            log.warning("readiness gate failed for %s (proceeding to dispatch): %s", effort_id, exc)
        blast = getattr(verdict, "blast_radius", "routine") or "routine"
        await self.exec_gate.set_risk(effort_id, self._risk_from_blast(blast))

        # Fail toward dispatch if readiness is unavailable/partial (don't wedge the operator on a
        # model glitch); HOLD only on an explicit not-clear verdict WITH genuine blockers to ask.
        clear = getattr(verdict, "clear_and_safe", True)
        questions = getattr(verdict, "clarifying_questions", None) or []
        if verdict is not None and clear is False and questions:
            # HOLD at the readiness gate — surface ONLY genuine blockers (F5), each with a
            # recommended default; do NOT dispatch until answered (UX-FLOW Stage 2).
            self._pending[effort_id] = {
                "proj_channel": proj_channel, "root": root, "request": request,
                "questions": questions,
            }
            numbered = self._render_questions(questions)
            n = len(questions)
            footer = (
                f"\n\n**All {n} question{'s' if n != 1 else ''} need an answer before I start** — "
                f"reply with your answers, or say _“use your recommendations”_ and I'll apply the "
                f"suggested defaults."
            )
            await self.comms.post(
                Intent.effort_dispatch,
                f"⏸️ Awaiting operator clarification before dispatch:\n{numbered}",
                effort_id=effort_id,
            )
            await self.chat.post(
                mgmt_channel,
                (f"{reply_prefix}\n\n**Before I start `{effort_id}` I need to clarify:**\n"
                 f"{numbered}{footer}").strip(),
            )
            return

        # Clear (or readiness unavailable) → dispatch. Clear any prior hold.
        self._pending.pop(effort_id, None)
        self._spawn(self.delegate(effort_id, proj_channel, root, request))
        await self.chat.post(
            mgmt_channel,
            (f"{reply_prefix}\n\n_Readiness ✓ — dispatched a worker on **{effort_id}**; watch it "
             f"live in its project thread. I'll summarize back here when done._").strip(),
        )

    # ── ground + dry-run prep (UX-FLOW Stage 4, P4.0) ─────────────────────────
    async def prepare_execution(
        self, effort_id: str, request: str, *, risk: str = "routine"
    ) -> dict:
        """Classify the effort's blast radius (sets the dry-run requirement, P4.0b) and — if
        grounding is enabled and the effort is risky — ground its assumptions via
        openbrain-research and inject the grounded claims as steering (P4.0a). Grounding is
        best-effort/advisory; it never blocks. Returns the execution-gate status."""
        await self.exec_gate.set_risk(effort_id, risk)
        if self.s.grounding_enabled and self.exec_gate.dry_run_required(risk):
            res = await self.grounding.ground(request)
            if res.grounded and (res.claims or res.summary):
                body = "# GROUNDED CONTEXT (openbrain-research — verify before relying)\n"
                if res.summary:
                    body += res.summary.strip() + "\n"
                for c in res.claims[:20]:
                    body += f"- {c}\n"
                await self.charters.set_steering(effort_id, body, actor="grounding")
                await self.comms.post(
                    Intent.worker_activity,
                    f"🔎 grounded {len(res.claims)} claim(s) into the effort context (P4.0).",
                    effort_id=effort_id,
                )
        return await self.exec_gate.status(effort_id)

    async def delegate(
        self, effort_id: str, channel_id: str, root_post_id: str, goal: str,
        *, repo: str | None = None,
    ) -> None:
        """Set the effort's goal (constraints inline, §4.3) and dispatch a worker; all activity
        streams into the effort THREAD (`root_post_id`). Runs as a background task. `repo` focuses
        the worker first; omit to resolve it from the effort's project (registry → AO_DEFAULT_REPO)."""
        repo = repo or await self._effort_repo(effort_id)
        try:
            await self.charters.set_goal(effort_id, goal, created_by="po")
            # P4.0 gate: a high-blast-radius effort may not reach REAL-code execution until its
            # isolated dry-run is recorded complete. Routine efforts pass immediately.
            ok, reason = await self.exec_gate.may_execute(effort_id)
            if not ok:
                await self.comms.post(
                    Intent.escalation,
                    f"⛔ execution held — {reason}. Complete the isolated dry-run first, then "
                    f"record it (`/dry-run {effort_id} pass`).",
                    effort_id=effort_id,
                )
                await self.comms.post(
                    Intent.operator_reply, f"⛔ **{effort_id}** held before execution — {reason}."
                )
                return
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
        elif cmd == "risk":
            if len(args) < 2:
                await reply(
                    "usage: `/risk <effort_id> <routine|irreversible|cross_effort|cascading_refactor>`"
                )
                return
            eff, risk = args[0], args[1]
            try:
                st = await self.exec_gate.set_risk(eff, risk)
                await reply(f"✅ `{eff}` risk=`{risk}` → dry_run_status=`{st}`")
            except Exception as exc:  # noqa: BLE001
                await reply(f"⚠️ could not set risk for `{eff}`: {exc}")
        elif cmd == "dry-run":
            if not args:
                await reply("usage: `/dry-run <effort_id> <pass|fail>`")
                return
            eff = args[0]
            passed = len(args) < 2 or args[1].lower() in ("pass", "passed", "ok", "true")
            try:
                await self.exec_gate.record_dry_run(eff, passed=passed)
                ok, reason = await self.exec_gate.may_execute(eff)
                await reply(
                    f"✅ `{eff}` dry-run {'passed' if passed else 'failed'} — may_execute={ok}"
                    + (f" ({reason})" if reason else "")
                )
            except Exception as exc:  # noqa: BLE001
                await reply(f"⚠️ could not record dry-run for `{eff}`: {exc}")
        elif cmd == "project":
            sub = args[0].lower() if args else ""
            if sub == "add" and len(args) >= 3:
                name, repo = args[1], args[2]
                try:
                    proj = await self.projects.add(name, repo, created_by="operator")
                    chan = await self.router.ensure_project_channel(proj["slug"])
                    await self.projects.set_channel(proj["slug"], chan)
                    self.events.track_channel(chan)
                    if user_id:
                        await self.chat.add_member(chan, user_id)
                    note = ""
                    if proj["git_host"]:  # widen the worker egress scope to this repo's host
                        await self.egress.allow(proj["git_host"], added_by="operator", source="project")
                        await self.egress.sync()
                        note = f" · egress host `{proj['git_host']}` allowed"
                    await reply(
                        f"✅ project `{proj['slug']}` → `{repo}` (post in `#proj-{proj['slug']}` "
                        f"to work on it, or say _\"in {proj['slug']}, …\"_ here){note}"
                    )
                except Exception as exc:  # noqa: BLE001
                    await reply(f"⚠️ could not add project: {exc}")
            elif sub == "list":
                ps = await self.projects.list()
                await reply(
                    "**Projects:**\n" + "\n".join(f"- `{p['slug']}` → {p['repo_url']}" for p in ps)
                    if ps else "no projects yet — `/project add <name> <repo-url>`"
                )
            elif sub == "remove" and len(args) >= 2:
                ok = await self.projects.remove(args[1], actor="operator")
                await self.egress.sync()
                await reply(f"{'✅ removed' if ok else '⚠️ no such'} project `{args[1]}`")
            else:
                await reply(
                    "usage: `/project add <name> <repo-url>` · `/project list` · `/project remove <name>`"
                )
        elif cmd == "egress":
            sub = args[0].lower() if args else ""
            if sub == "allow" and len(args) >= 2:
                try:
                    h = await self.egress.allow(args[1], added_by="operator", source="manual")
                    content = await self.egress.sync()
                    await reply(f"✅ egress host `{h}` allowed ({content.count('^')} hosts live)")
                except Exception as exc:  # noqa: BLE001
                    await reply(f"⚠️ could not allow host: {exc}")
            elif sub in ("remove", "deny") and len(args) >= 2:
                h = await self.egress.remove(args[1], actor="operator")
                await self.egress.sync()
                await reply(f"✅ egress host `{h}` removed")
            elif sub == "list":
                hosts = await self.egress.hosts()
                await reply("**Egress allowlist:**\n" + "\n".join(f"- `{h}`" for h in hosts))
            else:
                await reply(
                    "usage: `/egress allow <host|repo-url>` · `/egress remove <host>` · `/egress list`"
                )
        else:
            await reply(f"unknown command `/{cmd}` — try `/help`")
