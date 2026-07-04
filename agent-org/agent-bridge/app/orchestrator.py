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
import time

from .adapters.chat import ChatAdapter
from .config import Settings
from .db import Database
from .modules.audit_sink import AuditSink
from .modules.capacity_park import ParkStore
from .modules.charters import Charters
from .modules.comms_router import CommsRouter, Intent
from .modules.context_manager import ContextManager
from .modules.egress import EgressAllowlist
from .modules.event_gateway import EventGateway
from .modules.execution_gate import ExecutionGate
from .modules.floor_guard import FloorGuard
from .modules.governance_gate import GovernanceGate
from .modules.grounding import Grounding, build_grounding
from .modules.learning_loop import LearningLoop
from .modules.model_router import (
    ModelBackpressureError,
    ModelRouter,
    is_backpressure_text,
)
from .modules.planner import Planner
from .modules.profiles import ProfileRegistry
from .modules.project_context import ProjectContext
from .modules.projects import ProjectRegistry
from .modules.roles import RoleAuthority
from .modules.router import Router, slugify
from .modules.scheduler import NoCapacityError, Scheduler
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
    "helpfully and concisely in the first person (you own the 'intent thread').\n"
    "BE A THINKING PARTNER, NOT A TICKET-TAKER. When the operator describes what they want built, "
    "don't just say 'on it' — briefly reflect back what you understand the goal to be, propose HOW "
    "you'd approach it, and surface any GENUINE decision that changes the outcome (with your "
    "recommendation). That 'figuring out the options' is the value you add. But stay tight: no "
    "frivolous questions, no re-asking things you can resolve from the project or standard practice, "
    "and never pad. Classify the message:\n"
    "- new project: they want to START/onboard a NEW project, or they give a git URL "
    "(github.com/…, gitlab, an `git@…:…` link) → set `repo_url` to that URL (and `project` to a "
    "short name if they give one). This creates its `#proj-<name>` channel. If they want a new "
    "project but give NO git URL, ASK for the repo URL in your reply (don't guess one). If they say "
    "it's a **fork** (or mention an upstream/parent repo), set `repo_url` to THEIR fork and "
    "`upstream_url` to the PARENT repo — the org bakes a read-only `upstream` remote so workers can "
    "pull the parent's changes but push only to the fork. If they also describe work to do, ALSO "
    "set effort_name.\n"
    "- set upstream on an EXISTING project: they want to add/track a parent repo as **upstream** on a "
    "project that is ALREADY registered — 'set X as upstream for project Y', 'maintain X as upstream', "
    "'track the official repo as upstream', 'clone X into Y keeping upstream'. Set `project` to the "
    "existing project (match a KNOWN PROJECT) + `upstream_url` to the parent repo URL, and do NOT set "
    "`repo_url` (you are NOT onboarding a new project). I update the project + confirm.\n"
    "- request: they want NEW work done on an EXISTING project → set effort_name to a short "
    "kebab-case slug (it becomes a thread in the project channel). If they name a project to work "
    "on, set `project` to it (match a KNOWN PROJECT). In your `reply`, do the thinking-partner thing: "
    "(1) reflect the goal back in a line so they know you got it; (2) state your intended APPROACH at "
    "a high level; (3) if a real fork-in-the-road exists (a choice that changes the result), name the "
    "option(s) + your recommendation. Do NOT invent frivolous questions and do NOT claim you "
    "started/dispatched anything — a readiness check runs next and will pause ONLY if a genuine "
    "blocker remains (governance F5 — a question is cheaper than a misaligned worker).\n"
    "- clarification: the operator is ANSWERING a question or ADDING detail to an effort that is "
    "awaiting clarification or already in progress → set kind=clarification, effort_id to that "
    "effort (see AWAITING CLARIFICATION / CURRENT EFFORTS), and put their words in `steering`.\n"
    "- status: they're asking what's going on / what a worker is doing / why something is taking a "
    "while → set kind=status. Answer from CURRENT EFFORTS + RECENT WORKER ACTIVITY (real commands). "
    "CRITICAL — understand the states: `running` = a worker is executing it NOW; `idle` = open but "
    "NOTHING is running and it will NOT start on its own; `paused` = frozen on a concern (needs a "
    "decision); `waiting-capacity` = auto-resumes when the GPU frees. Efforts DO NOT queue and "
    "auto-run. So NEVER say things like 'they're queued and will proceed as resources become "
    "available' or 'I'll route workers' — that is FALSE. If nothing is `running`, say so plainly and "
    "OFFER to dispatch the idle ones (or archive the ones they're done with).\n"
    "- reengage: they want stalled/idle/failed work to actually START — 'get the workers working', "
    "'continue', 'start the work', 're-engage the X tasks', 'run it', 'kick it off', 'they're not "
    "working'. Set kind=reengage. This DISPATCHES workers for real. If they name a group/project "
    "(e.g. 'the monogame tasks'), set `target_filter` to a substring of those effort ids (e.g. "
    "'monogame'); a specific effort → `effort_id`; otherwise it re-engages all idle efforts. Do NOT "
    "promise to do it 'soon' — this action does it now.\n"
    "- archive: they want efforts CANCELLED/cleared/removed — 'abort the calculators', 'cancel these', "
    "'clear the queue', 'those are done, remove them', or a confirmed 'yes' to your offer to archive. "
    "Set kind=archive + `target_filter` (e.g. 'calculator') or `effort_id`. This actually cancels "
    "them (pushed branches are kept). Never just say you'll 'flag them for termination' — DO it.\n"
    "- reassign: 'move effort X to project Y', 'X belongs in project Y', 'that effort should be on Y' "
    "→ kind=reassign + effort_id=X + project=Y (fixes an effort stuck in the wrong/sandbox project).\n"
    "- steering: explicitly changing the direction/scope of an existing effort → set effort_id + steering.\n"
    "- decision: approve/modify a paused effort → set effort_id + decision (approve/modify still need "
    "the human's explicit command; abort is handled as archive above).\n"
    "- project_list: 'what projects do we have', 'list the projects' → kind=project_list.\n"
    "- project_remove: 'remove/forget/delete project X', 'stop tracking X' → kind=project_remove + "
    "`project`=X.\n"
    "- egress_allow: 'let the workers reach X', 'allow X for egress', 'whitelist host X' → "
    "kind=egress_allow + `host`=the host or repo URL.\n"
    "- kill: 'stop everything', 'freeze the fleet', 'emergency stop', 'kill switch' → kind=kill "
    "(freezes ALL work; reversible). unkill: 'resume', 'release', 'unfreeze', 'let them run' → "
    "kind=unkill.\n"
    "- question / chitchat otherwise.\n"
    "EVERY user-facing action has an NL path (this is the primary surface; slash commands are just a "
    "power-user fallback) — so map the operator's plain-language intent to the RIGHT kind and act, "
    "rather than telling them to run a command. Only set action fields when clearly warranted. Never "
    "claim to have taken an irreversible action. Keep replies short and human.\n\n"
    "NEVER MAKE EMPTY PROMISES. Do not say 'I'll route workers', 'I'll monitor and let you know', "
    "'they'll proceed as resources free up', or 'I'm flagging them for termination' — you have no "
    "background process that does those things. Either the action fires THIS turn (reengage/archive/"
    "request) or you state the honest current state and ask what to do. A promise you can't keep is "
    "worse than saying 'nothing is running right now — want me to dispatch them?'.\n\n"
    "USE THE CONVERSATION SO FAR — the operator is continuing one thread; don't treat each message "
    "as new or ask them to repeat context you were already given.\n"
    "DO NOT INVENT FACTS you don't actually have: URLs, ports, host addresses, file paths, where "
    "something runs, or whether a server is up. You do NOT know these unless they're in the "
    "conversation or effort/project context. If you don't know, say so plainly. NEVER promise to "
    "'check', 'look up', 'find', or 'investigate' something and then do nothing — if answering "
    "requires inspecting a workspace/codebase, open an effort (kind=request, a worker actually "
    "checks); otherwise just say you don't have that information and suggest how they can find it."
)

_HELP = (
    "**agent-bridge** — governed multi-agent orchestration. You can talk to me in **plain "
    "language** (I'm your PO — tell me what you want built and I'll scope it), or use commands:\n"
    "- `/help` — this message\n"
    "- `/project add <name> <repo-url> [--upstream <parent-url>]` — onboard a repo the org can work "
    "on (creates `#proj-<name>` + allows its git host); `--upstream` makes it a **fork** (bakes a "
    "read-only `upstream` remote so workers fetch the parent but push only to the fork); "
    "`/project list` · `/project remove <name>`\n"
    "- `/egress allow <host|repo-url>` — widen the worker git-egress allowlist; `/egress list`\n"
    "- `/effort <name>` — open a work effort as a **thread** in its project channel\n"
    "- `/status [effort_id|all]` — open efforts + HONEST status (running/idle/paused/waiting) + "
    "recent worker activity (`all` includes done/aborted; an id targets one)\n"
    "- `/retry [filter]` — DISPATCH idle efforts now (idle efforts don't auto-start); e.g. "
    "`/retry monogame` or bare `/retry` for all idle\n"
    "- `/archive <effort_id|filter>` — cancel efforts you're done with (e.g. `/archive calculator`); "
    "pushed branches are kept\n"
    "- `approve|modify|abort <effort_id> [note]` — approve a drafted **plan**, or decide an open CONCERN\n"
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
        # Hierarchical, bounded, relevance-selected conversation memory (thread = immediate,
        # channel = higher-level background) so the PO stays coherent without overflowing the window.
        self.context = ContextManager(
            thread_chars=settings.context_thread_chars,
            channel_chars=settings.context_channel_chars,
            max_thread_turns=settings.context_max_thread_turns,
        )
        # The #mgmt thread each effort was requested in, so completion summaries + CONCERNs thread
        # back under that conversation instead of scattering as new top-level posts.
        self._effort_mgmt_thread: dict[str, str] = {}
        # Feature branch each effort's work was published to (commit + push on done).
        self._published_branch: dict[str, str] = {}
        # Efforts opened but HELD at the readiness gate awaiting operator clarification (P3.8);
        # the operator's next answer resolves them → dispatch. {effort_id: {proj_channel, root, request}}
        self._pending: dict[str, dict] = {}
        # Efforts HELD at the Stage-3 plan-approval gate (P3.9) awaiting operator approval;
        # `approve <effort>` dispatches with the plan's steps. {effort_id: {proj_channel, root, request, plan}}
        self._pending_plan: dict[str, dict] = {}
        self._bg_tasks: set[asyncio.Task] = set()  # in-flight delegations
        # Capacity park-and-resume (machine B `suspended`, reason=inference_backpressure): an effort
        # whose step is shed by the saturated GPU is PARKED here (DB-backed) instead of failed, and
        # auto-resumed when capacity returns. The resume driver drains one-at-a-time, clocked by a
        # successful model call (self._signal_capacity, fired from the ModelRouter) + a timer tick.
        self.parks = ParkStore(db, self.audit)
        self.models.on_capacity_signal = self._signal_capacity
        self.scheduler.on_release = self._signal_capacity   # worker frees → drain slot-parked efforts
        self._capacity_event = asyncio.Event()
        self._capacity_task: asyncio.Task | None = None
        self._draining = False
        self._last_backpressure = 0.0   # monotonic ts of the last shed (source-guard window)
        # Efforts with a LIVE delegate task right now (actively being executed). This is the honest
        # "work is happening" signal — distinct from the gate state `active` (= merely not-frozen),
        # which persists forever and misleads the PM into reporting a phantom queue.
        self._delegating: set[str] = set()

    def _spawn(self, coro) -> None:
        """Run a coroutine in the background, keeping a reference so it isn't GC'd."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ── capacity park-and-resume (inference backpressure, machine B) ───────────
    def _signal_capacity(self) -> None:
        """The capacity-recovered event: a successful model call proves the GPU has capacity, so
        wake the drain loop. Sync + cheap (idempotent Event.set) — safe to call on every success."""
        try:
            self._capacity_event.set()
        except Exception:  # noqa: BLE001 - never let a signal hiccup touch the call path
            pass

    def _note_backpressure(self) -> None:
        self._last_backpressure = time.monotonic()

    def _backpressure_recent(self) -> bool:
        """True if a shed happened within the source-guard window — used to skip firing our OWN
        research/grounding fan-out on top of an already-saturated GPU (anti-self-DoS)."""
        return (time.monotonic() - self._last_backpressure) < self.s.capacity_source_guard_s

    async def _park_effort(
        self, effort_id: str, *, stage: str, channel_id: str | None, root: str | None,
        request: str, plan_steps: list[str] | None, from_step: int, mgmt_thread: str | None,
        reason: str = "inference_backpressure",
    ) -> None:
        """Park an effort that can't run right now (don't fail it) — either the GPU is saturated
        (inference_backpressure) or every worker slot is busy (no_worker_slot). Records the resume
        token, reflects a waiting card, posts an honest note, and auto-resumes when capacity frees."""
        if reason == "inference_backpressure":
            self._note_backpressure()
            note = ("⏸️ Paused — the inference queue is saturated (the shared GPU is busy). I'll "
                    "resume this automatically as soon as capacity frees up; no work is lost.")
        else:  # no_worker_slot
            note = ("⏳ Waiting for a free worker — all worker slots are busy. I'll start this "
                    "automatically the moment one frees up; nothing is lost.")
        await self.parks.park(
            effort_id, stage=stage, channel_id=channel_id, root_post_id=root, request=request,
            plan_steps=plan_steps, from_step=from_step, mgmt_thread=mgmt_thread, reason=reason,
        )
        await self.router.update_effort_card(effort_id, "waiting")
        await self.comms.post(Intent.effort_dispatch, note, effort_id=effort_id)

    async def _capacity_drain_loop(self) -> None:
        """Drain parked-on-backpressure efforts ONE AT A TIME. Wakes on the capacity signal (a
        successful call) OR a timer tick (fallback), then resumes a single effort — staggered
        resumes clocked by real successes avoid re-saturating the queue (the thundering-herd trap)."""
        while True:
            try:
                await asyncio.wait_for(self._capacity_event.wait(), timeout=self.s.capacity_timer_s)
            except asyncio.TimeoutError:
                pass  # fallback tick — re-check even if no success signal fired
            except asyncio.CancelledError:
                return
            self._capacity_event.clear()
            try:
                await self._drain_parked_once()
            except Exception as exc:  # noqa: BLE001 - the loop must never die
                log.warning("capacity drain tick failed: %s", exc)

    async def _drain_parked_once(self) -> None:
        """Resume the oldest DISPATCHABLE parked effort (FIFO). Bumps its attempt count; escalates +
        stops retrying once starved. Re-entrancy-guarded so concurrent signals don't double-resume."""
        if self._draining:
            return
        self._draining = True
        try:
            token = None
            for t in await self.parks.all():
                if await self.gate.can_dispatch(t["effort_id"]):  # skip frozen/killed (stay parked)
                    token = t
                    break
            if token is None:
                return
            eid = token["effort_id"]
            # GPU saturation is a SYSTEMIC fault → escalate after the attempt cap. Worker-slot
            # contention is NORMAL and self-resolving (workers finish) → wait patiently, never
            # escalate on count (the drain only fires on a release or the timer, so no tight loop).
            if token.get("reason", "inference_backpressure") == "inference_backpressure":
                attempts = await self.parks.bump_attempts(eid)
                if attempts > self.s.capacity_max_attempts:
                    await self._escalate_starved(token)
                    await self.parks.unpark(eid)
                    self._signal_capacity()  # move on to the next parked effort
                    return
            log.info("resuming parked effort %s (stage=%s, reason=%s)",
                     eid, token["stage"], token.get("reason"))
            await self._resume_parked(token)
        finally:
            self._draining = False

    async def _resume_parked(self, token: dict) -> None:
        """Re-run the shed stage from its resume token. Runs in the background (a full effort can take
        minutes); its first successful model call fires _signal_capacity → the next drain. A re-shed
        re-parks it via the same park points; real progress unparks it (delegate/intake unpark)."""
        eid = token["effort_id"]
        if token["stage"] == "intake":
            mgmt = await self.mgmt_channel_id()
            self._spawn(self._intake_or_dispatch(
                eid, token["channel_id"], token["root_post_id"], token["request"],
                reply_prefix="↩️ Capacity's back — resuming this now.",
                mgmt_channel=mgmt or token["channel_id"], mgmt_thread=token["mgmt_thread"],
            ))
        else:  # "delegate"
            self._spawn(self.delegate(
                eid, token["channel_id"], token["root_post_id"], token["request"],
                plan_steps=token["plan_steps"], start_step=token["from_step"],
            ))

    async def _escalate_starved(self, token: dict) -> None:
        """A parked effort couldn't get capacity after the attempt cap — the queue's been saturated
        too long. Surface it to #mgmt (not a governance freeze — it's a capacity problem, not a
        safety one) and stop auto-retrying; the operator can re-request once the GPU frees up."""
        eid = token["effort_id"]
        await self.router.update_effort_card(eid, "error")
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{eid}** has been waiting on GPU capacity for {token['attempts']} attempts — the "
            f"inference queue has stayed saturated. I've stopped auto-retrying. Check what's using "
            f"the GPU (e.g. a research/ingestion batch); re-send the request to try again.",
            effort_id=eid,
        )
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{eid}** gave up waiting on inference capacity (queue saturated too long). "
            f"Re-send the request once the GPU frees up.",
            thread_id=token.get("mgmt_thread"),
        )
        await self.audit.log(
            "effort_capacity_starved", effort_id=eid, payload={"attempts": token["attempts"]}
        )

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
        # Capacity park-and-resume: start the drain loop and kick a boot resume so efforts parked
        # before a restart (DB-backed) get picked up as soon as capacity is available. Gated to the
        # live adapter — the deterministic test harness (fake) drives the drain directly, so no
        # background loop leaks across the many per-test orchestrators.
        if (self.s.capacity_resume_enabled and self.s.chat_adapter != "fake"
                and self._capacity_task is None):
            self._capacity_task = asyncio.create_task(self._capacity_drain_loop())
            parked = await self.parks.count()
            if parked:
                log.info("resuming %d parked effort(s) from a prior run", parked)
                self._signal_capacity()

    async def aclose(self) -> None:
        """Stop the capacity drain loop (for a clean shutdown / test teardown)."""
        if self._capacity_task is not None:
            self._capacity_task.cancel()
            try:
                await self._capacity_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._capacity_task = None

    def _project_for(self, repo: str | None = None) -> str:
        """The FALLBACK project slug for a request that names no project: the AO_DEFAULT_REPO slug
        if set, else the sandbox. Named/onboarded projects are resolved via the registry
        (`_resolve_project_slug`); this is only the default."""
        target = repo or (self.s.default_repo or "")
        return slugify(target) if target else self.s.default_project

    @staticmethod
    def _project_name_from_repo(repo_url: str) -> str:
        """Derive a project name from a git URL (its repo segment) when the operator gives none."""
        seg = (repo_url or "").rstrip("/").split("/")[-1]
        if seg.endswith(".git"):
            seg = seg[:-4]
        return seg or "project"

    async def _onboard_project(
        self, name: str, repo_url: str, *, user_id: str | None = None,
        upstream_url: str | None = None,
    ) -> dict:
        """Register a project from a git URL: create its `#proj-<slug>` channel, add the operator,
        and allow its git host on the worker egress (so clones work). `upstream_url` makes it a fork
        (parent baked as a read-only `upstream` remote + its host allowed). Returns the project row."""
        proj = await self.projects.add(
            name, repo_url, created_by="operator", upstream_url=upstream_url
        )
        chan = await self.router.ensure_project_channel(proj["slug"])
        await self.projects.set_channel(proj["slug"], chan)
        self.events.track_channel(chan)
        if user_id:
            await self.chat.add_member(chan, user_id)
        from .modules.projects import host_of
        for host in (proj["git_host"], host_of(upstream_url or "")):
            if host:
                try:
                    await self.egress.allow(host, added_by="operator", source="project")
                except Exception as exc:  # noqa: BLE001
                    log.debug("egress allow for %s: %s", proj["slug"], exc)
        try:
            await self.egress.sync()
        except Exception as exc:  # noqa: BLE001
            log.debug("egress sync for %s: %s", proj["slug"], exc)
        return proj

    async def _set_project_upstream(
        self, slug: str, upstream_url: str, channel_id: str, thread_id: str | None, reply_prefix: str
    ) -> None:
        """Set the fork parent on an EXISTING project (NL 'maintain X as upstream'). Widens egress to
        the parent host too, so the next focus can `git fetch upstream`. Idempotent + best-effort."""
        await self.projects.set_upstream(slug, upstream_url)
        from .modules.projects import host_of
        uh = host_of(upstream_url)
        note = ""
        if uh:
            try:
                await self.egress.allow(uh, added_by="operator", source="project")
                await self.egress.sync()
                note = f" · upstream host `{uh}` allowed"
            except Exception as exc:  # noqa: BLE001
                log.debug("egress allow for upstream %s: %s", uh, exc)
        await self.chat.post(
            channel_id,
            (reply_prefix + f"\n\n✅ Set **`{upstream_url}`** as the read-only **upstream** for "
             f"`{slug}`{note}. On the next worker focus it's baked as the `upstream` remote — workers "
             f"`git fetch upstream` the parent but push only to the fork. Say _\"get the workers "
             f"working on {slug}\"_ to (re)dispatch with it now.").strip(),
            thread_id=thread_id,
        )

    async def _handle_new_project(
        self, intent, message: str, channel_id: str, thread_id: str | None,
        user_id: str | None, reply: str,
    ) -> None:
        """Onboard a project from `intent.repo_url` (create its channel), then — if the operator also
        described work — open the first effort in it; else confirm the channel is ready."""
        name = intent.project or self._project_name_from_repo(intent.repo_url)
        try:
            proj = await self._onboard_project(
                name, intent.repo_url, user_id=user_id,
                upstream_url=getattr(intent, "upstream_url", None),
            )
        except Exception as exc:  # noqa: BLE001
            await self.chat.post(
                channel_id, (reply + f"\n\n_(couldn't set up that project: {exc})_").strip(),
                thread_id=thread_id,
            )
            return
        created = (
            f"✅ Project **#proj-{proj['slug']}** → `{proj['repo_url']}` "
            f"(token {self._project_token_label(proj)})"
            + (f" · ⑂ fork of `{proj['upstream_url']}` (read-only `upstream` remote)"
               if proj.get("upstream_url") else "")
        )
        if intent.effort_name:  # onboard + start the first effort in the new project
            eid, chan, root = await self.router.open_effort(
                intent.effort_name, project=proj["slug"], goal=message
            )
            self.events.track_channel(chan)
            if thread_id:
                self._effort_mgmt_thread[eid] = thread_id
            await self._intake_or_dispatch(
                eid, chan, root, message,
                reply_prefix=f"{reply}\n\n{created} — starting your first effort there.",
                mgmt_channel=channel_id, mgmt_thread=thread_id,
            )
        else:
            await self.chat.post(
                channel_id,
                (f"{reply}\n\n{created}. Post in that channel — or say _\"in {proj['slug']}, …\"_ "
                 f"here — to start work.").strip(),
                thread_id=thread_id,
            )

    async def _resolve_project_slug(
        self, named: str | None, channel_id: str | None = None, effort_name: str | None = None,
    ) -> str:
        """Resolve which project a request belongs to: an explicitly named/onboarded project wins;
        else the originating #proj-<slug> channel's project; else — the fix for
        'init-monogame-engine' landing in the sandbox — an UNAMBIGUOUS match of a known project's
        slug inside the effort name; else the fallback (default/sandbox)."""
        if named:
            p = await self.projects.resolve(named)
            if p:
                return p["slug"]
        if channel_id:
            slug = await self.router.resolve_project_by_channel(channel_id)
            if slug:
                return slug
        if effort_name:
            guess = await self._project_from_name(effort_name)
            if guess:
                return guess
        return self._project_for()

    async def _project_from_name(self, effort_name: str) -> str | None:
        """The one known project whose slug appears in the effort name (e.g. `monogame-engine` in
        `init-monogame-engine`). Only when EXACTLY one matches — never guess ambiguously."""
        name_slug = slugify(effort_name)
        hits = [p["slug"] for p in await self.projects.list()
                if p["slug"] != self.s.default_project and p["slug"] in name_slug]
        return hits[0] if len(hits) == 1 else None

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

    def _project_token_label(self, p: dict) -> str:
        """Human-readable label for which deploy token a project resolves to (for `/project list`)."""
        import os

        from .modules.projects import owner_token_env

        if p.get("token_env"):
            return f"`${p['token_env']}`" + ("" if os.environ.get(p["token_env"]) else " ⚠️ **unset**")
        cand = owner_token_env(p.get("repo_url", ""))
        if cand and os.environ.get(cand):
            return f"`${cand}` (by org)"
        return "`$LC_DEPLOY_TOKEN` (default)"

    async def _project_token(self, effort_id: str) -> str | None:
        """The deploy token for this effort's clone/push (multi-PAT). Resolution, in order:
          1. the project's EXPLICIT `token_env` (from `/project add … TOKEN_ENV`), if set;
          2. the per-OWNER convention `LC_<OWNER>_TOKEN` (e.g. PolyshDesign → LC_POLYSHDESIGN_TOKEN),
             used only if that env var is actually set — so any repo under an org auto-picks its PAT;
          3. else None ⇒ the pool's ambient `LC_DEPLOY_TOKEN` (little-coder's fallback = your own repos).
        Secrets live in env only; the DB stores the var NAME, never the token."""
        import os

        from .modules.projects import owner_token_env

        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            proj = e.project if e else None
        if not proj:
            return None
        p = await self.projects.get(proj)
        if not p:
            return None
        # 1) explicit override — warn if it's named but unset (a misconfiguration).
        env_name = p.get("token_env")
        if env_name:
            tok = os.environ.get(env_name)
            if not tok:
                log.warning("project %s token env %r is unset — falling back to the pool token", proj, env_name)
            return tok or None
        # 2) per-owner convention — used only if the env var is set (else silent fall-through).
        cand = owner_token_env(p.get("repo_url", ""))
        if cand and os.environ.get(cand):
            return os.environ[cand]
        # 3) pool default (LC_DEPLOY_TOKEN, on the worker pool).
        return None

    async def _effort_upstream(self, effort_id: str) -> str | None:
        """The fork PARENT URL for this effort's project (D0.f), or None if it isn't a fork. The
        bridge re-bakes it as the read-only `upstream` remote on every focus — the persistent source
        of truth is the Project record, so it survives workspace wipes + container rebuilds."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            proj = e.project if e else None
        return await self.projects.upstream_for(proj) if proj else None

    async def _project_upstream_token(self, effort_id: str) -> str | None:
        """A READ-scoped token for a PRIVATE fork parent, by the per-owner convention
        `LC_<PARENT_OWNER>_TOKEN` (used only if that env var is set). A public parent needs none.
        Distinct from the origin/push token — the parent is a different owner than the fork."""
        import os

        from .modules.projects import owner_token_env

        upstream = await self._effort_upstream(effort_id)
        if not upstream:
            return None
        cand = owner_token_env(upstream)
        return os.environ.get(cand) if cand else None

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
        posted = await self.comms.post(
            Intent.concern, body, effort_id=effort_id, thread_id=self._mgmt_thread_of(effort_id)
        )
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
        if getattr(verdict, "deviates", False) and getattr(verdict, "trigger", None):
            concern = Concern(
                intent_thread=f"effort {effort_id}",
                what_surfaced=getattr(verdict, "rationale", "") or "monitor detected a deviation",
                intent_of_change="a monitored deviation from intent/spec (governance §3)",
                pm_recommendation="review + re-ground",
                blocked_efforts=[effort_id],
            )
            await self.raise_concern(
                effort_id, verdict.trigger, concern, actor="pm-monitor", level=verdict.level
            )
        return verdict

    # ── conversation memory (hierarchical thread+channel, bounded, relevant) ──
    def _remember(self, channel_id: str, thread_id: str | None, role: str, text: str) -> None:
        """Log a turn (role ∈ {operator, po}) under its thread so the PO can build thread-immediate
        + channel-background context on the next query."""
        self.context.remember(channel_id, thread_id, role, text)

    async def _mgmt_remember(self, effort_id: str, text: str) -> None:
        """Record a bridge→#mgmt line (e.g. a completion summary) into the PO's memory, under the
        effort's originating thread, so follow-ups about that work have its context."""
        mgmt = await self.mgmt_channel_id()
        if mgmt:
            self._remember(mgmt, self._mgmt_thread_of(effort_id), "po", text)

    # ── natural-language intake (the conversational PO surface) ───────────────
    async def nl_intake(
        self, message: str, channel_id: str, *, user_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Route a natural-language operator message to the PO agent, which interprets intent
        and replies conversationally. Non-destructive actions (open an effort, apply steering,
        report status) are executed; safety decisions are NOT auto-run from fuzzy NL — the PO
        asks for the explicit, auditable command (governance §3). Runs on the PO profile's lane
        (local qwen36-27b by default; cloud if P0.5 mandated)."""
        efforts = await self.gate.snapshot(open_only=True)  # PO reasons over what's still in play
        # HONEST status (running/idle/paused/waiting-capacity) — NOT the gate `active` flag, which
        # persists forever and made the PM invent a phantom "queued, waiting for resources".
        status_map = await self._effort_status_map(efforts)
        ctx = "; ".join(f"{e['id']}={status_map.get(e['id'], 'idle')}" for e in efforts) or "none"
        n_running = sum(1 for v in status_map.values() if v == "running")
        n_idle = sum(1 for v in status_map.values() if v == "idle")
        ctx += f"  (running={n_running}, idle={n_idle}; idle efforts do NOT auto-start — dispatch them)"
        pending_ctx = ", ".join(self._pending.keys()) or "none"
        projects = await self.projects.list()
        projects_ctx = ", ".join(f"{p['slug']} ({p['repo_url']})" for p in projects) or "none"
        # Fix 1 (PO progress visibility): real, recent per-worker command activity so the PO can
        # answer "what's going on?" from FACTS instead of admitting it has no real-time visibility.
        activity_ctx = self._worker_activity_ctx(efforts)
        # Hierarchical + bounded + relevance-selected: this thread (immediate) + relevant channel
        # background, filtered to the query so it never overwhelms the model window.
        history = self.context.build(channel_id, thread_id, query=message)
        try:
            intent = await self.models.structured(
                "po", _PO_NL_SYS,
                f"CONVERSATION SO FAR (most recent last):\n{history}\n\n"
                f"LATEST OPERATOR MESSAGE:\n{message}\n\nCURRENT EFFORTS: {ctx}\n"
                f"AWAITING CLARIFICATION: {pending_ctx}\nKNOWN PROJECTS: {projects_ctx}\n"
                f"RECENT WORKER ACTIVITY (newest last):\n{activity_ctx}",
                OperatorIntent,
            )
        except ModelBackpressureError:
            # The shared GPU is saturated (a research/ingestion batch shed the request) — this is
            # NOT a parse failure. Say so honestly + keep the operator's message in memory so a
            # retry keeps context; never make a transient GPU squeeze look like a broken PM.
            log.info("nl_intake shed by inference backpressure — advising operator to retry")
            self._remember(channel_id, thread_id, "operator", message)
            await self.chat.post(
                channel_id,
                "⏳ The local model is saturated right now (a background job is using the GPU). "
                "I didn't lose your message — give me a moment and send it again, and I'll pick it up.",
                thread_id=thread_id,
            )
            return
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash the loop
            log.warning("nl_intake model call failed: %s", exc)
            self._remember(channel_id, thread_id, "operator", message)
            await self.chat.post(
                channel_id,
                "I couldn't parse that just now — you can also use `/help` for commands.",
                thread_id=thread_id,
            )
            return

        reply = (intent.reply or "").strip()
        # Remember this turn (under its thread) so the next message keeps context.
        self._remember(channel_id, thread_id, "operator", message)
        self._remember(channel_id, thread_id, "po", reply)
        # NEW PROJECT: a git URL onboards a new project (+ its #proj channel) — do this BEFORE
        # treating the message as work, else it would default to #proj-sandbox (the reported bug).
        if intent.repo_url:
            await self._handle_new_project(intent, message, channel_id, thread_id, user_id, reply)
            return
        # They named a project we don't know + gave no URL → ask for the repo (don't silently
        # fall back to the sandbox).
        if intent.project and not await self.projects.resolve(intent.project):
            await self.chat.post(
                channel_id,
                (reply + f"\n\n_I don't have a project called **{intent.project}** yet — share its "
                 f"git URL and I'll set it up (or `/project add {intent.project} <repo-url>`)._").strip(),
                thread_id=thread_id,
            )
            return
        # Set/track an UPSTREAM on an EXISTING project (no new repo_url) — "maintain X as upstream on
        # project Y" / "track X upstream". The fork parent can be added after onboarding, all in NL.
        if intent.upstream_url and intent.project and not intent.repo_url:
            p = await self.projects.resolve(intent.project)
            if p:
                await self._set_project_upstream(
                    p["slug"], intent.upstream_url, channel_id, thread_id, reply
                )
                return
        if intent.kind == "request" and intent.effort_name:
            try:
                # Resolve WHICH project this works on: a named/onboarded project, else the fallback.
                # An effort is a THREAD in its project channel (COMMS-MODEL §4).
                project = await self._resolve_project_slug(
                    intent.project, channel_id, effort_name=intent.effort_name
                )
                eid, chan, root = await self.router.open_effort(
                    intent.effort_name, project=project, goal=message
                )
                self.events.track_channel(chan)
                # Remember the #mgmt thread this effort was requested in, so its summaries + CONCERNs
                # thread back under this conversation instead of scattering as new top-level posts.
                if thread_id:
                    self._effort_mgmt_thread[eid] = thread_id
                # Add the requester to the PROJECT channel ONCE (not per effort).
                if user_id:
                    await self.chat.add_member(chan, user_id)
                # Stage 2 readiness gate (P3.8): DON'T guess — if under-specified, ask + HOLD;
                # only dispatch when the request is clear (F5). This replies itself + returns.
                await self._intake_or_dispatch(
                    eid, chan, root, message, reply_prefix=reply, mgmt_channel=channel_id,
                    mgmt_thread=thread_id,
                )
            except Exception as exc:  # noqa: BLE001
                await self.chat.post(
                    channel_id, (reply + f"\n\n_(couldn't open that effort: {exc})_").strip(),
                    thread_id=thread_id,
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
                    channel_id, (reply + f"\n\n_(couldn't find effort `{eid}` to update)_").strip(),
                    thread_id=thread_id,
                )
                return
            if thread_id:
                self._effort_mgmt_thread[eid] = thread_id   # keep summaries in this conversation
            proj_channel, root = loc
            addition = (intent.steering or message).strip()
            try:  # record the direction change as a versioned steering edit (audit, §4.2)
                await self.charters.set_steering(eid, addition, actor="po")
            except Exception as exc:  # noqa: BLE001
                log.debug("set_steering(%s) failed: %s", eid, exc)
            # Fold the answer (+ held recommendations) into the goal and dispatch — no second
            # readiness pass (the operator has spoken; don't re-interrogate).
            await self._resume_after_clarification(
                eid, proj_channel, root, addition, reply_prefix=reply, mgmt_channel=channel_id,
                mgmt_thread=thread_id,
            )
            return
        elif intent.kind == "reengage":
            # "get the workers working" / "continue" / "re-engage the monogame tasks" — actually
            # RE-DISPATCH idle efforts. This is additive (running work the operator already asked
            # for), so it fires directly from NL — no phantom "they'll proceed as resources free up".
            targets = self._select_efforts(intent, efforts)
            await self._reengage(targets, mgmt_channel=channel_id, mgmt_thread=thread_id,
                                 reply_prefix=reply)
            return
        elif intent.kind == "archive" or (intent.kind == "decision" and intent.decision == "abort"):
            # "abort/cancel/archive/clear these" — actually FIRES for open efforts (cancellation, not
            # a safety-gate clear; pushed branches persist). Require a target so we never wipe all.
            has_target = bool(intent.effort_id) or bool((intent.target_filter or "").strip())
            if not has_target:
                reply += ("\n\n_Which should I archive? Name one (`effort-…`) or a group "
                          "(e.g. “the calculator efforts”)._")
            else:
                targets = self._select_efforts(intent, efforts)
                await self._archive_efforts(targets, mgmt_channel=channel_id, mgmt_thread=thread_id,
                                            reply_prefix=reply)
                return
        elif intent.kind == "reassign" and intent.effort_id and intent.project:
            p = await self.projects.resolve(intent.project)
            if not p:
                reply += (f"\n\n_I don't have a project called **{intent.project}** — onboard it "
                          f"first (share its git URL)._")
            elif await self._reassign_effort(intent.effort_id, p["slug"]):
                reply += (f"\n\n✅ Moved `{intent.effort_id}` to project **`{p['slug']}`** — say "
                          f"_\"get the workers working on {p['slug']}\"_ to run it against that repo.")
            else:
                reply += f"\n\n_No effort called `{intent.effort_id}`._"
        elif intent.kind == "decision" and intent.effort_id and intent.decision:
            # approve/modify a PAUSED effort still needs the explicit command (safety gate, §3).
            reply += (
                f"\n\n_To **{intent.decision}** `{intent.effort_id}`, confirm with:_ "
                f"`{intent.decision} {intent.effort_id}`"
            )
        elif intent.kind == "project_list":
            ps = await self.projects.list()
            if ps:
                reply += "\n\n**Projects:**\n" + "\n".join(
                    f"- `{p['slug']}` → {p['repo_url']}"
                    + (f" · ⑂ upstream `{p['upstream_url']}`" if p.get("upstream_url") else "")
                    for p in ps
                )
            else:
                reply += "\n\n_No projects yet — give me a git URL and I'll onboard one._"
        elif intent.kind == "project_remove" and intent.project:
            ok = await self.projects.remove(intent.project, actor="operator")
            try:
                await self.egress.sync()
            except Exception as exc:  # noqa: BLE001
                log.debug("egress sync after remove: %s", exc)
            reply += (f"\n\n✅ Removed project `{intent.project}` (its channel stays; efforts keep "
                      f"their history)." if ok else f"\n\n_No project called `{intent.project}`._")
        elif intent.kind == "egress_allow" and intent.host:
            try:
                h = await self.egress.allow(intent.host, added_by="operator", source="manual")
                await self.egress.sync()
                reply += f"\n\n✅ Workers can now reach **`{h}`** (git-egress widened)."
            except Exception as exc:  # noqa: BLE001
                reply += f"\n\n_(couldn't allow that host: {exc})_"
        elif intent.kind == "kill":
            await self.gate.kill_switch(on=True, actor="human")
            reply += ("\n\n🛑 **Kill switch ENGAGED** — the whole fleet is frozen; no worker will run "
                      "until you lift it. Say _“release”_ / _“resume”_ (or `/unkill`) to run again.")
        elif intent.kind == "unkill":
            await self.gate.kill_switch(on=False, actor="human")
            reply += "\n\n✅ Kill switch **released** — the fleet can run again."
        elif intent.kind == "status":
            if efforts:
                status_map = await self._effort_status_map(efforts)
                reply += "\n\n" + self._render_status(efforts, status_map)
            else:
                reply += "\n\n_No open efforts — tell me what you'd like built._"

        await self.chat.post(channel_id, reply or "…", thread_id=thread_id)

    def _worker_activity_ctx(self, efforts: list[dict]) -> str:
        """Compact per-effort recent worker command activity for the PO's context (Fix 1).
        Only efforts with recorded activity appear; keeps the block small and factual."""
        blocks: list[str] = []
        for e in efforts:
            act = self.router.recent_activity(e["id"], n=6)
            if act:
                blocks.append(f"{e['id']}:\n  " + "\n  ".join(act))
        return "\n".join(blocks) if blocks else "none yet (no worker has run a command)"

    # ── honest execution status + re-engage + archive (the PM can ACT) ─────────
    async def _effort_status_map(self, efforts: list[dict]) -> dict[str, str]:
        """The TRUTH about whether work is happening — NOT the gate state (`active` = merely
        not-frozen, which persists forever and misleads the PM into reporting a phantom queue).
          running          — a delegate task is executing it right now (or a worker is computing it)
          paused           — frozen on a concern / kill switch (needs an operator decision)
          waiting-capacity — parked on GPU backpressure (auto-resumes when capacity returns)
          idle             — open but NOTHING is running; it will NOT start on its own (needs dispatch)
        Efforts do NOT queue and auto-run: an `idle` effort stays idle until re-engaged."""
        sched = await self.scheduler.snapshot()
        computing = {i["effort_id"] for i in sched
                     if i.get("state") == "computing" and i.get("effort_id")}
        parked = {t["effort_id"] for t in await self.parks.all()}
        out: dict[str, str] = {}
        for e in efforts:
            eid = e["id"]
            lc = e.get("lifecycle", "open")
            if lc in ("done", "aborted"):        # terminal lifecycle wins (shown in /status all|<id>)
                out[eid] = lc
            elif eid in self._delegating or eid in computing:
                out[eid] = "running"
            elif e.get("state") == "frozen":
                out[eid] = "paused"
            elif eid in parked:
                out[eid] = "waiting-capacity"
            else:
                out[eid] = "idle"
        return out

    def _render_status(self, efforts: list[dict], status_map: dict[str, str]) -> str:
        """Honest per-effort status lines + a one-line reality check when nothing is running."""
        icon = {"running": "🟢", "paused": "⏸️", "waiting-capacity": "⏳", "idle": "⚪",
                "done": "✅", "aborted": "🗑️"}
        lines = []
        for e in efforts:
            st = status_map.get(e["id"], "idle")
            line = f"- `{e['id']}` — {icon.get(st, '·')} **{st}**"
            act = self.router.recent_activity(e["id"], n=2)
            if act:
                line += "\n  " + "\n  ".join(f"· {a}" for a in act)
            lines.append(line)
        body = "\n".join(lines)
        running = sum(1 for v in status_map.values() if v == "running")
        idle = sum(1 for v in status_map.values() if v == "idle")
        if running == 0 and idle:
            body += (f"\n\n_⚠️ Nothing is running. {idle} effort(s) are **idle** — they will NOT "
                     f"start on their own. Say **“get the workers working”** (or name which) and I'll "
                     f"dispatch them; or **“archive”** the ones you're done with._")
        return body

    def _select_efforts(self, intent, open_efforts: list[dict]) -> list[str]:
        """Resolve which efforts an action targets: an explicit effort_id, a name/substring filter
        (e.g. 'calculator', 'monogame'), else ALL open efforts."""
        ids = {e["id"] for e in open_efforts}
        if intent.effort_id and intent.effort_id in ids:
            return [intent.effort_id]
        filt = (getattr(intent, "target_filter", None) or "").strip().lower()
        if filt:
            sel = [e["id"] for e in open_efforts if filt in e["id"].lower()]
            if sel:
                return sel
        return [e["id"] for e in open_efforts]

    async def _reengage(
        self, effort_ids: list[str], *, mgmt_channel: str, mgmt_thread: str | None = None,
        reply_prefix: str = "",
    ) -> list[str]:
        """Actually RE-DISPATCH idle efforts — the 'get the workers working' / 'continue' action.
        Skips efforts already running (no double-dispatch) or paused on a concern (needs a decision)."""
        status_map = await self._effort_status_map(await self.gate.snapshot(open_only=True))
        started: list[str] = []
        roots: dict[str, str] = {}       # eid -> effort-thread root post id (for clickable permalinks)
        skipped: list[tuple[str, str]] = []
        for eid in effort_ids:
            st = status_map.get(eid)
            if st == "running":
                skipped.append((eid, "already running")); continue
            if st == "paused":
                skipped.append((eid, f"paused on a concern — `approve {eid}` / `abort {eid}`")); continue
            if st == "waiting-capacity":
                skipped.append((eid, "waiting on GPU capacity — auto-resumes")); continue
            loc = await self.router.effort_thread(eid)
            if loc is None:
                skipped.append((eid, "no thread")); continue
            _v, goal, _s = await self.charters.current_goal(eid)
            if not goal:
                skipped.append((eid, "no goal recorded")); continue
            proj_channel, root = loc
            # Thread this effort's summaries/errors back to the operator's CURRENT conversation, so a
            # failure surfaces where they're looking — not buried in the project thread.
            if mgmt_thread:
                self._effort_mgmt_thread[eid] = mgmt_thread
            await self.router.update_effort_card(eid, "active")
            self._spawn(self.delegate(eid, proj_channel, root, goal))
            started.append(eid)
            roots[eid] = root
        parts: list[str] = []
        if started:
            # Link each effort to its live thread so the operator clicks straight to the command
            # stream — the work lives in the effort THREAD (#proj-<slug>), not #mgmt, so a plain id
            # left them hunting (Bug: "the pm says see the project thread, but there's nothing there").
            parts.append("▶ **Dispatching workers now** on: "
                         + ", ".join(self._effort_link(e, roots.get(e)) for e in started)
                         + " — click through to watch each live command stream.")
        if skipped:
            parts.append("Skipped: " + "; ".join(f"`{e}` ({why})" for e, why in skipped))
        if not parts:
            parts.append("Nothing to re-engage.")
        await self.chat.post(mgmt_channel, (reply_prefix + "\n\n" + "\n".join(parts)).strip(),
                             thread_id=mgmt_thread)
        return started

    async def _archive_efforts(
        self, effort_ids: list[str], *, mgmt_channel: str, mgmt_thread: str | None = None,
        reply_prefix: str = "",
    ) -> list[str]:
        """Cancel/ARCHIVE open efforts (lifecycle=aborted) — actually fires on 'yes, abort'. Any
        pushed work persists on its branch (reversible). A FROZEN effort (open concern) is a SAFETY
        gate — left for the explicit `abort <id>` command, not archived from fuzzy NL."""
        archived: list[str] = []
        skipped: list[tuple[str, str]] = []
        for eid in effort_ids:
            if await self.gate.state_of(eid) == "frozen":
                skipped.append((eid, f"paused on a concern — use `abort {eid}`")); continue
            await self.gate.set_lifecycle(eid, "aborted")
            self._delegating.discard(eid)
            await self.parks.unpark(eid)
            await self.router.update_effort_card(eid, "aborted")
            archived.append(eid)
        parts: list[str] = []
        if archived:
            parts.append("🗑️ **Archived** (cancelled; any pushed branch is kept): "
                         + ", ".join(f"`{e}`" for e in archived))
        if skipped:
            parts.append("Skipped: " + "; ".join(f"`{e}` ({why})" for e, why in skipped))
        if not parts:
            parts.append("Nothing to archive.")
        await self.chat.post(mgmt_channel, (reply_prefix + "\n\n" + "\n".join(parts)).strip(),
                             thread_id=mgmt_thread)
        return archived

    async def _reassign_effort(self, effort_id: str, project_slug: str) -> bool:
        """Move an effort to a different project (fixes a mis-resolution, e.g. one stuck in the
        sandbox). Updates the effort's project so its next focus clones the RIGHT repo. True if found."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is None:
                return False
            e.project = project_slug
            await s.commit()
        return True

    def _mgmt_thread_of(self, effort_id: str | None) -> str | None:
        """The #mgmt thread an effort was requested in (for threading its summaries/CONCERNs)."""
        return self._effort_mgmt_thread.get(effort_id) if effort_id else None

    def _effort_link(self, effort_id: str, root_post_id: str | None) -> str:
        """Render an effort id as a clickable markdown link to its live thread when the adapter can
        build a permalink, else a plain `code` id. Keeps dispatch messages navigable without
        assuming permalinks are available (fake adapter / unresolved team / no site URL)."""
        link = None
        if root_post_id:
            try:
                link = self.chat.permalink(root_post_id)
            except Exception:  # noqa: BLE001 - a link is a nicety; never break the dispatch message
                link = None
        return f"[`{effort_id}`]({link})" if link else f"`{effort_id}`"

    @staticmethod
    def _friendly_dispatch_error(exc: Exception) -> str:
        """Turn a raw worker/HTTP error into a readable, actionable line (not a stack-trace dump)."""
        s = str(exc)
        low = s.lower()
        if "409" in s and "no project focused" in low:
            return ("the worker had no repo focused (409) — the effort isn't tied to a project with a "
                    "repo; reassign it to a project or archive it")
        if "409" in s:
            return "the worker was busy (409 — a task was already in flight); re-engage it to retry"
        if "connect" in low or "timeout" in low or "connecterror" in low:
            return "the worker was unreachable (connection/timeout) — it may be restarting; retry it"
        return f"delegation error — {s[:160]}"

    @staticmethod
    def _risk_from_blast(blast_radius: str) -> str:
        """Map the readiness gate's blast_radius (UX-FLOW Stage 2) to the P4.0 dry-run risk class.
        cross_effort / cascading_refactor ⇒ a dry-run is required; routine ⇒ none."""
        return blast_radius if blast_radius in ("cross_effort", "cascading_refactor") else "routine"

    @staticmethod
    def _extract_flag(tokens: list[str], flag: str) -> tuple[str | None, list[str]]:
        """Pull `--flag <value>` (or `--flag=<value>`) out of a token list; return
        (value or None, remaining positional tokens). Only the first occurrence is taken."""
        value: str | None = None
        rest: list[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if value is None and t == flag and i + 1 < len(tokens):
                value = tokens[i + 1]
                i += 2
                continue
            if value is None and t.startswith(flag + "="):
                value = t[len(flag) + 1:]
                i += 1
                continue
            rest.append(t)
            i += 1
        return (value or None), rest

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
        *, reply_prefix: str, mgmt_channel: str, mgmt_thread: str | None = None,
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
            (f"{reply_prefix}\n\n_Got it — dispatching a worker on {self._effort_link(effort_id, root)} "
             f"with your clarification. Click through to watch it; I'll summarize back here when "
             f"done._").strip(),
            thread_id=mgmt_thread,
        )

    async def _intake_or_dispatch(
        self, effort_id: str, proj_channel: str, root: str, request: str,
        *, reply_prefix: str, mgmt_channel: str, mgmt_thread: str | None = None,
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
            try:
                summary = await self.project_context.ensure(project, repo)
            except ModelBackpressureError:
                summary = None  # the survey is optional context — skip under load, don't park on it
            if summary:
                workspace_ctx += f"\n\nPROJECT SUMMARY (survey of the actual codebase):\n{summary}"
        verdict = None
        try:
            verdict = await self.planner.readiness_gate(effort_id, request, workspace_ctx)
        except ModelBackpressureError:
            # The readiness gate was shed by the saturated GPU — PARK intake + auto-resume; do NOT
            # fail-open to dispatch (that would skip the gate) and do NOT error the effort.
            await self._park_effort(
                effort_id, stage="intake", channel_id=proj_channel, root=root, request=request,
                plan_steps=None, from_step=1, mgmt_thread=mgmt_thread,
            )
            return
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
                thread_id=mgmt_thread,
            )
            return

        # Readiness passed — clear any prior hold.
        self._pending.pop(effort_id, None)
        # Stage 3 (P3.9): plan-approval gate. Risk-gated — present a plan + HOLD for operator
        # approval before ANY execution; routine efforts (or plan_approval=off) proceed directly.
        if await self._plan_required(effort_id):
            await self._present_plan(
                effort_id, proj_channel, root, request, reply_prefix, mgmt_channel, workspace_ctx,
                mgmt_thread=mgmt_thread,
            )
            return
        self._spawn(self.delegate(effort_id, proj_channel, root, request))
        await self.chat.post(
            mgmt_channel,
            (f"{reply_prefix}\n\n_Readiness ✓ — I'm dispatching a worker on "
             f"{self._effort_link(effort_id, root)} with the approach above; click through to watch "
             f"it live. If you'd tackle it differently, just say so and I'll steer it. I'll summarize "
             f"back here when done._").strip(),
            thread_id=mgmt_thread,
        )

    async def _plan_required(self, effort_id: str) -> bool:
        """Whether the Stage-3 plan-approval gate applies (AO_PLAN_APPROVAL): `always` = every
        effort, `risky` = high-blast-radius only (default), `off` = never."""
        mode = self.s.plan_approval
        if mode == "off":
            return False
        if mode == "always":
            return True
        return self.exec_gate.dry_run_required(await self._effort_risk_str(effort_id))

    async def _present_plan(
        self, effort_id: str, proj_channel: str, root: str, request: str,
        reply_prefix: str, mgmt_channel: str, workspace_ctx: str, *, mgmt_thread: str | None = None,
    ) -> None:
        """UX-FLOW Stage 3: draft a plan, present it to #mgmt as the top-level stop-gate, and HOLD
        until the operator approves (`approve <effort>`). No execution happens before approval."""
        try:
            plan = await self.planner.draft_plan(
                effort_id, intent_thread=request, request=request, workspace_ctx=workspace_ctx
            )
        except ModelBackpressureError:
            # Planner shed by the saturated GPU — PARK at intake (re-runs readiness+plan on resume)
            # rather than skip the approval gate by dispatching without a plan.
            await self._park_effort(
                effort_id, stage="intake", channel_id=proj_channel, root=root, request=request,
                plan_steps=None, from_step=1, mgmt_thread=mgmt_thread,
            )
            return
        except Exception as exc:  # noqa: BLE001 - a planner hiccup shouldn't wedge the operator
            log.warning("draft_plan failed for %s (dispatching without plan gate): %s", effort_id, exc)
            self._spawn(self.delegate(effort_id, proj_channel, root, request))
            await self.chat.post(
                mgmt_channel, f"{reply_prefix}\n\n_(couldn't draft a plan — dispatched directly.)_",
                thread_id=mgmt_thread,
            )
            return
        self._pending_plan[effort_id] = {
            "proj_channel": proj_channel, "root": root, "request": request, "plan": plan,
        }
        steps_list = getattr(plan, "implementation_steps", None) or []
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(steps_list, 1)) or "_(no steps drafted)_"
        body = (
            f"{reply_prefix}\n\n📋 **Plan for `{effort_id}`** — the approval gate before any "
            f"execution (UX-FLOW Stage 3).\n"
            f"**Feature:** {getattr(plan, 'feature_overview', '') or request}\n"
            f"**Steps:**\n{steps}\n"
            f"**Estimate:** {getattr(plan, 'estimate', 'unknown')}\n\n"
            f"_Reply `approve {effort_id}` to execute, or `abort {effort_id}` to cancel._"
        )
        await self.chat.post(mgmt_channel, body.strip(), thread_id=mgmt_thread)
        await self.comms.post(
            Intent.effort_dispatch,
            "📋 Plan drafted — awaiting operator approval before execution (Stage 3).",
            effort_id=effort_id,
        )

    async def approve_effort_plan(self, effort_id: str) -> bool:
        """Operator approved a held plan (Stage 3 → Stage 4/5): record approval + dispatch with the
        plan's steps (each becomes a checkpoint). Returns False if no plan was pending."""
        pend = self._pending_plan.pop(effort_id, None)
        if not pend:
            return False
        try:
            await self.planner.approve_plan(effort_id, actor_role="human")
        except Exception as exc:  # noqa: BLE001
            log.debug("approve_plan(%s): %s", effort_id, exc)
        plan = pend.get("plan")
        steps = getattr(plan, "implementation_steps", None) if plan else None
        self._spawn(
            self.delegate(effort_id, pend["proj_channel"], pend["root"], pend["request"], plan_steps=steps)
        )
        return True

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
            # Source guard (anti-self-DoS): grounding fires an openbrain-research job; don't stack a
            # fan-out on top of an already-saturated GPU. If a shed happened recently, skip it —
            # grounding is advisory (best-effort) so skipping only forgoes optional context.
            if self._backpressure_recent():
                log.info("skipping grounding for %s — recent inference backpressure (source guard)", effort_id)
                await self.comms.post(
                    Intent.worker_activity,
                    "🔎 skipped grounding this time — the inference queue is saturated (avoiding "
                    "piling a research fan-out onto a busy GPU). Proceeding without it.",
                    effort_id=effort_id,
                )
                return await self.exec_gate.status(effort_id)
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
        *, repo: str | None = None, plan_steps: list[str] | None = None, start_step: int = 1,
    ) -> None:
        """Execute an effort (UX-FLOW Stage 5) as a governed loop: each plan step is a **checkpoint**
        (P4.1/4.2) — the worker runs it, then (on risky efforts) a sampled **monitor** (P3.7) + a
        differently-goaled **review** (P4.4-4.7) gate it before proceeding; a flag/deviation freezes +
        escalates (P4.6/§3). Routine efforts take the light path (wake → done). Runs in the background.
        `repo` focuses the worker; omit to resolve from the effort's project (registry → fallback).
        `start_step` resumes from a given step after a backpressure park (the earlier steps are done)."""
        # SINGLE-FLIGHT: never dispatch an effort that's already executing. Without this, an explicit
        # re-engage racing the capacity/slot drain (both spawn delegate for the SAME effort) → a
        # second wake hits a busy worker → 409 Conflict. The check-then-add is atomic (no await
        # between), so it's a correct guard under asyncio. (The park row is left intact — the drain
        # that picks it will find the effort in-flight and skip via this same guard.)
        if effort_id in self._delegating:
            log.info("delegate: %s already executing — skipping duplicate dispatch", effort_id)
            return
        self._delegating.add(effort_id)   # honest "work is happening now" marker
        steps = [s for s in (plan_steps or []) if s.strip()] or [goal]
        cur_step = start_step
        try:
            repo = repo or await self._effort_repo(effort_id)
            repo_token = await self._project_token(effort_id) if repo else None
            upstream = await self._effort_upstream(effort_id) if repo else None
            upstream_token = await self._project_upstream_token(effort_id) if upstream else None
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
                    Intent.operator_reply, f"⛔ **{effort_id}** held before execution — {reason}.",
                    thread_id=self._mgmt_thread_of(effort_id),
                )
                return
            # P5.1/5.2: grant the worker its (non-irreversible) scope + confirm its role is approved.
            await self._authorize_worker(effort_id)
            heavy = await self._effort_heavy(effort_id)   # risk-gated stop-gates+review+monitor
            last = None
            for i, step in enumerate(steps, 1):
                if i < start_step:   # resuming after a park — earlier steps already ran
                    continue
                cur_step = i
                last = await self._run_step(
                    effort_id, channel_id, root_post_id, step, i, len(steps), repo, heavy,
                    repo_token, upstream, upstream_token,
                )
                if last is None:   # stopped (failure / flagged / frozen) — handlers already posted
                    return
                await self.parks.unpark(effort_id)  # progressed past any prior shed point
            if repo:  # commit + push the effort's branch so the work is durable + shared
                await self._publish_effort(effort_id, channel_id, root_post_id, repo)
            await self._finish_effort(effort_id, last)
        except ModelBackpressureError:
            # A step was shed by the saturated GPU — PARK (machine B suspended), don't fail. The
            # resume driver re-runs delegate from `cur_step` when capacity returns; work isn't lost.
            await self._park_effort(
                effort_id, stage="delegate", channel_id=channel_id, root=root_post_id,
                request=goal, plan_steps=steps, from_step=cur_step,
                mgmt_thread=self._mgmt_thread_of(effort_id),
            )
        except NoCapacityError:
            # Every worker slot is busy — PARK (reason=no_worker_slot) instead of dead-ending on
            # "couldn't dispatch". The scheduler's on_release drain auto-runs it when a worker frees.
            await self._park_effort(
                effort_id, stage="delegate", channel_id=channel_id, root=root_post_id,
                request=goal, plan_steps=steps, from_step=cur_step,
                mgmt_thread=self._mgmt_thread_of(effort_id), reason="no_worker_slot",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("delegate failed for %s: %s", effort_id, exc)
            friendly = self._friendly_dispatch_error(exc)
            await self.comms.post(
                Intent.worker_activity, f"⚠️ {friendly}", effort_id=effort_id,
            )
            # Surface UP to the operator's conversation too — a worker failure must NEVER hide only
            # in the effort thread while the operator waits (the 'error never reached me' bug).
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}** couldn't run — {friendly} (see its project thread).",
                thread_id=self._mgmt_thread_of(effort_id),
            )
            await self.router.update_effort_card(effort_id, "error")
        finally:
            self._delegating.discard(effort_id)   # no longer actively executing

    async def _run_step(self, effort_id, channel_id, root, step, i, n, repo, heavy, repo_token=None,
                        upstream=None, upstream_token=None):
        """Run one plan step = one checkpoint. Returns the WorkResult to continue, or None to STOP
        (the failure/flag/deviation handler has already posted + frozen where required)."""
        header = f"▶ **step {i}/{n}**: {step[:180]}" if n > 1 else "⏳ worker dispatched. Working…"
        await self.comms.post(Intent.effort_dispatch, header, effort_id=effort_id)
        cp_id = f"{effort_id}:cp{i}"
        if heavy:  # P4.1: the enforced halt exists as a Checkpoint row, independent of plan markers
            await self.stop_gates.add_checkpoint(cp_id, effort_id, f"step {i}", i)
        result = await self.router.wake(
            effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
            session_id=effort_id, instruction=step, repo=repo, repo_token=repo_token,
            upstream=upstream, upstream_token=upstream_token,
        )
        if result is None:
            await self._report_completion(effort_id, None)
            return None
        if not result.ok:
            # A CLONE failure (couldn't focus the worker) is NOT a worker failure — router.wake
            # already posted a clear, actionable message. Just mark the card + stop; don't reframe
            # it as "worker ended error" (that's the confusing phantom-worker symptom).
            if result.status == "clone_failed":
                await self.router.update_effort_card(effort_id, "error")
                return None
            # If the worker's OWN inference was shed by the saturated GPU, that's backpressure — PARK
            # + auto-resume (raise so delegate parks this step), NOT a worker failure to escalate.
            if is_backpressure_text(getattr(result, "output", None)):
                raise ModelBackpressureError(f"worker inference shed: {(result.output or '')[:160]}")
            await self._escalate_worker_failure(effort_id, result)
            return None
        if heavy and not await self._gate_deliverable(effort_id, result, cp_id):
            return None
        return result

    @staticmethod
    def _effort_branch(effort_id: str) -> str:
        """The feature branch an effort's work is published to (never main/master)."""
        return f"agent/{effort_id}"

    def _agent_identity(self, role: str = "worker-default") -> tuple[str, str]:
        """(name, email) the agent commits as — its ROLE, not the baked 'little-coder', so blame +
        hand-off provenance identify who did what (P5.4). When named per-domain roles land (P5.2),
        each role commits under its own identity automatically."""
        return role, f"{role}@{self.s.agent_email_domain}"

    async def _publish_effort(self, effort_id: str, channel_id: str, root: str, repo: str) -> None:
        """Commit + push the effort's work to its feature branch so it's DURABLE (survives a
        /project wipe), VISIBLE to the team, and fetchable for A→B hand-off. Additive push to a
        feature branch is routine (floor); push-to-main/deploy stay human-gated. Deterministic
        finalize wake — not a reviewable deliverable, so it skips the review gate. Commits carry the
        AGENT's identity (via GIT_AUTHOR/COMMITTER env — git-proxy-safe, since `-c` is blocked)."""
        branch = self._effort_branch(effort_id)
        name, email = self._agent_identity("worker-default")
        ident = (
            f'GIT_AUTHOR_NAME="{name}" GIT_AUTHOR_EMAIL="{email}" '
            f'GIT_COMMITTER_NAME="{name}" GIT_COMMITTER_EMAIL="{email}"'
        )
        instruction = (
            f"PUBLISH YOUR WORK so the team can see it (additive, allowed). Run these git steps "
            f"EXACTLY (the env prefix on the commit attributes it to you, `{name}`):\n"
            f"  git checkout -b {branch} 2>/dev/null || git checkout {branch}\n"
            f"  git add -A\n"
            f'  {ident} git commit -m "{effort_id}: <one-line summary of your changes>"   # skip if nothing to commit\n'
            f"  git push -u origin {branch}\n"
            f"Do NOT push to main/master. Do NOT force-push or delete anything. "
            f"Then reply with the branch name and the pushed commit hash."
        )
        result = await self.router.wake(
            effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
            session_id=effort_id, instruction=instruction, repo=None,  # already focused; no re-clone
        )
        self._published_branch[effort_id] = branch if (result and result.ok) else ""
        await self.audit.log(
            "effort_published", effort_id=effort_id,
            payload={"branch": branch, "ok": bool(result and result.ok)},
        )

    async def _gate_deliverable(self, effort_id: str, result, cp_id: str) -> bool:
        """Stage-5 gates on a step's deliverable (risky efforts): sampled monitor (P3.7) + a
        differently-goaled review (P4.4-4.7). Returns True to proceed, False to STOP (frozen)."""
        deliverable = (result.output or "").strip()
        # P3.7 — the LLM monitor, forced on risky efforts (never per-token, never a health-probe).
        verdict = await self.monitor_sampled(effort_id, deliverable, force=True)
        if verdict is not None and getattr(verdict, "deviates", False):
            # monitor_sampled already froze + raised the CONCERN; record the pattern (P6.4) + stop.
            await self._observe_pattern(effort_id, verdict.rationale or "monitored deviation")
            return False
        # P4.4-4.7 — differently-goaled review, depth risk-gated; verdicts route to the PM.
        risk = await self._effort_risk_str(effort_id)
        verdicts = await self.stop_gates.review(
            effort_id, "worker-default", deliverable, risk=risk, checkpoint_id=cp_id
        )
        if not await self.stop_gates.clear_checkpoint(cp_id, verdicts):   # P4.2/4.6
            await self._on_review_flag(effort_id, verdicts)
            return False
        return True

    async def _report_completion(self, effort_id: str, result) -> None:
        """The undeliverable case (§2): the effort is FROZEN (a concern / kill switch). A busy pool
        is NO LONGER handled here — NoCapacity now parks + auto-resumes (no_worker_slot)."""
        if result is None:
            await self.comms.post(
                Intent.worker_activity,
                "⚠️ can't dispatch — this effort is frozen (a concern or the kill switch). "
                "Clear it (decide the concern / `unkill`) to run.",
                effort_id=effort_id,
            )
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}** is frozen (a concern or the kill switch) — decide it / `unkill` "
                f"to run.",
                thread_id=self._mgmt_thread_of(effort_id),
            )

    async def _finish_effort(self, effort_id: str, result) -> None:
        """All steps cleared → closure DOWN into the effort thread + a summary UP to #mgmt (§2)."""
        head = ((result.output or "").strip().splitlines()[0][:200]
                if result and result.output else "done")
        branch = self._published_branch.pop(effort_id, None)
        where = (
            f"pushed to branch **`{branch}`** — `git fetch origin {branch}` to see it"
            if branch else
            "changes are in the worker's workspace (no repo to publish to)"
        )
        await self.comms.post(
            Intent.closure,
            f"✅ worker finished (**done**) — {where}. Merge to `main`/deploy stay human-gated.",
            effort_id=effort_id,
        )
        await self.router.update_effort_card(effort_id, "done")
        await self.gate.set_lifecycle(effort_id, "done")  # drops out of the default /status view
        await self.comms.post(
            Intent.operator_reply,
            f"✅ **{effort_id}** finished (**done**): {head}\n_{where[0].upper() + where[1:]}._",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        await self._mgmt_remember(
            effort_id, f"[effort {effort_id} finished] {head}" + (f" (branch {branch})" if branch else "")
        )

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
            thread_id=self._mgmt_thread_of(effort_id),
        )

    # ── Stage-5 governance helpers (scope / risk-gating / review-flag / learning) ─
    async def _authorize_worker(self, effort_id: str) -> None:
        """P5.1/5.2: ensure the worker role is APPROVED (catalog) + grant its non-irreversible
        scope for this effort. Irreversible scope (push/deploy/delete) stays human-only, so the
        worker can read/write its workspace but not push — matching the container floor."""
        role = "worker-default"
        try:
            if not await self.scope.is_role_approved(role):
                await self.scope.catalog_add(role, "charters/worker-default.md", approved=True)
            for res in ("read", "write"):
                if not await self.scope.authorized(role, res):
                    try:
                        await self.scope.grant(role, res, granted_by="pm", effort_id=effort_id)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("scope grant %s/%s: %s", role, res, exc)
        except Exception as exc:  # noqa: BLE001 - authorization is best-effort scaffolding
            log.debug("authorize_worker(%s): %s", effort_id, exc)

    # ── lateral concern (P4.8) + A→B hand-off (P5.4) ──────────────────────────
    async def raise_lateral_concern(self, effort_id: str, from_role: str, text: str) -> None:
        """P4.8: a worker raises a cross-domain concern to a peer/reviewer. It surfaces on the BUS
        and routes to the PM (never resolved privately, never peer merge-authority), and is EXEMPT
        from the wake-storm rate cap (the brake channel is sacred, §5)."""
        await self.router.record_wake(effort_id, target="pm", kind="brake")  # exempt from the cap
        await self.comms.post(
            Intent.escalation,
            f"🛑 **lateral concern** from `{from_role}`: {text[:400]}\n↑ routed to the PM "
            f"(not resolved peer-to-peer, §4.4).",
            effort_id=effort_id,
        )
        await self.comms.post(
            Intent.operator_reply, f"🛑 lateral concern on **{effort_id}** ({from_role}): {text[:200]}"
        )
        await self.audit.log(
            "lateral_concern", effort_id=effort_id, actor=from_role, payload={"text": text[:500]}
        )

    async def hand_off(self, effort_id: str, path: str, *, workspace: str = "/workspace") -> str | None:
        """P5.4: an out-of-scope error hands off to the **last owner** of `path` (git-blame
        provenance, OD-4). Surfaces on the bus (observable); returns the owner, or None if
        unresolved (then it routes to the PM). The wake is brake-kind (storm-exempt)."""
        owner = await self.router.last_owner(path, workspace)
        if owner:
            await self.comms.post(
                Intent.escalation,
                f"↪️ **hand-off**: `{path}` is outside this effort's scope — last owner **{owner}** "
                f"(git-blame). Routing the fix to them.",
                effort_id=effort_id,
            )
        else:
            await self.comms.post(
                Intent.escalation,
                f"↪️ **hand-off**: `{path}` is outside scope but its last owner couldn't be "
                f"resolved — surfacing to the PM.",
                effort_id=effort_id,
            )
        await self.router.record_wake(effort_id, target=owner or "pm", kind="brake")
        await self.audit.log("handoff", effort_id=effort_id, payload={"path": path, "owner": owner})
        return owner

    async def _effort_risk_str(self, effort_id: str) -> str:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
        return e.risk if e and e.risk else "routine"

    async def _effort_heavy(self, effort_id: str) -> bool:
        """Whether to run the Stage-5 stop-gates + monitor + review (AO_REVIEW_MODE): `all` =
        always, `risky` = only high-blast-radius efforts (default), `off` = never."""
        mode = self.s.review_mode
        if mode == "off":
            return False
        if mode == "all":
            return True
        return self.exec_gate.dry_run_required(await self._effort_risk_str(effort_id))

    async def _observe_pattern(self, effort_id: str, text: str) -> str:
        """P6.4/6.5: record a signal in the learning loop; a pattern recurring across ≥2 efforts is
        surfaced to #suggestions + a PROPOSED hardening (never auto-applied — the human disposes)."""
        import hashlib

        sig = hashlib.sha1(" ".join((text or "").lower().split())[:120].encode()).hexdigest()[:16]
        try:
            pat = await self.learning.observe(sig, effort_id, text or "")
            if pat is not None:  # surfaced across ≥2 efforts
                await self.comms.post(
                    Intent.suggestion,
                    f"📈 **pattern** surfaced across {len(pat.effort_ids or [])} efforts "
                    f"(`{sig}`): {(text or '')[:200]}\n_PM should propose a hardening "
                    f"(propose-not-dispose — the human approves)._",
                )
                try:
                    await self.learning.propose(sig, f"recurring: {(text or '')[:160]}", by="pm")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.debug("observe_pattern(%s): %s", effort_id, exc)
        return sig

    async def _on_review_flag(self, effort_id: str, verdicts: list) -> None:
        """A review flagged the deliverable (P4.6/§4.4): record the pattern (P6.4), then freeze +
        escalate to the operator (pause-until-cleared) — the checkpoint stays blocking until a
        decision (approve = accept / modify = re-ground / abort)."""
        flagged = [v for v in verdicts if getattr(v, "verdict", "pass") == "flag"]
        detail = "; ".join(
            f"[{getattr(v, 'lens', '?')}] "
            + "; ".join(getattr(v, "findings", None) or [getattr(v, "reasoning", "")])
            for v in flagged
        )[:300] or "review flagged the deliverable"
        await self._observe_pattern(effort_id, detail)
        concern = Concern(
            intent_thread=f"effort {effort_id}",
            what_surfaced=f"review flagged the deliverable: {detail}",
            intent_of_change="a review flag means the deliverable may trade safety/scope for the metric (§4.4)",
            pm_recommendation="re-ground + refactor, or abort",
            blocked_efforts=[effort_id],
        )
        await self.raise_concern(effort_id, Trigger.deviation, concern, actor="reviewer")

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
        # Reply IN the operator's thread (root_id for a threaded message, else the message's own id
        # so a top-level message starts a coherent thread). Keeps the #mgmt conversation together.
        reply_thread = thread_id or post_id

        # Control surface — privileged (only the human posts; bot posts are filtered upstream).
        if _CONTROL_RE.match(stripped):
            await self._track_operator(user_id)
            await self._handle_command(stripped, channel_id, reply_thread, user_id=user_id)
            return

        mgmt = await self.mgmt_channel_id()
        if channel_id == mgmt:
            await self._track_operator(user_id)
            if stripped:
                # talk to the PO in plain language; user_id lets it add the requester to projects
                await self.nl_intake(stripped, channel_id, user_id=user_id, thread_id=reply_thread)
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
            # Reply IN the operator's thread so the #mgmt conversation stays coherent (the operator
            # uses threads; a top-level reply to a threaded command scatters the exchange).
            if channel_id:
                await self.chat.post(channel_id, msg, thread_id=thread_id)

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
            # Default view = efforts still in play. `/status all` includes done/aborted;
            # `/status <effort_id>` targets one (regardless of lifecycle).
            want_all = bool(args) and args[0].lower() == "all"
            target = args[0] if args and not want_all else None
            snap = await self.gate.snapshot(open_only=not (want_all or target))
            if target:
                snap = [e for e in snap if e["id"] == target]
            if not snap:
                if target:
                    await reply(f"no effort `{target}`.")
                elif want_all:
                    await reply("no efforts yet — create one with `/effort <name>`")
                else:
                    await reply(
                        "no open efforts — everything's done/aborted. `/status all` shows the history."
                    )
            else:
                status_map = await self._effort_status_map(snap)
                header = "**Efforts (open):**" if not (want_all or target) else "**Efforts:**"
                await reply(header + "\n" + self._render_status(snap, status_map))
        elif cmd in ("retry", "reengage"):
            # Re-dispatch idle efforts now: `/retry [filter]` (no filter = all idle).
            efforts = await self.gate.snapshot(open_only=True)
            filt = args[0] if args else None
            targets = [e["id"] for e in efforts if (not filt) or filt.lower() in e["id"].lower()]
            if not targets:
                await reply(f"no open efforts{f' matching `{filt}`' if filt else ''} to re-engage.")
            elif channel_id:
                await self._reengage(targets, mgmt_channel=channel_id, mgmt_thread=thread_id)
        elif cmd == "archive":
            if not args:
                await reply("usage: `/archive <effort_id|filter>` (e.g. `/archive calculator`)")
            else:
                efforts = await self.gate.snapshot(open_only=True)
                targets = [e["id"] for e in efforts if args[0].lower() in e["id"].lower()]
                if not targets:
                    await reply(f"no open efforts matching `{args[0]}`.")
                elif channel_id:
                    await self._archive_efforts(targets, mgmt_channel=channel_id, mgmt_thread=thread_id)
        elif cmd in ("kill", "unkill"):
            on = cmd == "kill"
            await self.gate.kill_switch(on=on, actor="human")
            await reply(f"✅ kill switch {'engaged — fleet frozen' if on else 'released'}")
        elif cmd in ("approve", "modify", "abort"):
            if not args:
                await reply(f"usage: `{cmd} <effort_id> [note]`")
                return
            effort_id, note = args[0], " ".join(args[1:])
            # Stage-3 plan approval takes precedence over a CONCERN clear when a plan is pending.
            if effort_id in self._pending_plan:
                if cmd == "approve":
                    await self.approve_effort_plan(effort_id)
                    await reply(f"✅ plan approved for `{effort_id}` — dispatching a worker.")
                else:
                    self._pending_plan.pop(effort_id, None)
                    await reply(
                        f"⛔ plan {cmd} for `{effort_id}` — not dispatched. "
                        f"Re-send the request with your changes to adjust it."
                    )
                return
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
                # Pull out `--upstream <url>` (fork parent) wherever it appears; the rest is
                # positional: name, repo, [TOKEN_ENV].
                upstream_url, positional = self._extract_flag(args[1:], "--upstream")
                if len(positional) < 2:
                    await reply("usage: `/project add <name> <repo-url> [--upstream <parent-url>] [TOKEN_ENV]`")
                    return
                name, repo = positional[0], positional[1]
                token_env = positional[2] if len(positional) >= 3 else None
                try:
                    proj = await self.projects.add(
                        name, repo, created_by="operator", token_env=token_env,
                        upstream_url=upstream_url,
                    )
                    chan = await self.router.ensure_project_channel(proj["slug"])
                    await self.projects.set_channel(proj["slug"], chan)
                    self.events.track_channel(chan)
                    if user_id:
                        await self.chat.add_member(chan, user_id)
                    note = ""
                    if proj["git_host"]:  # widen the worker egress scope to this repo's host
                        await self.egress.allow(proj["git_host"], added_by="operator", source="project")
                        note = f" · egress host `{proj['git_host']}` allowed"
                    up = ""
                    if upstream_url:  # a fork — allow the PARENT host too so `git fetch upstream` works
                        from .modules.projects import host_of
                        uh = host_of(upstream_url)
                        if uh:
                            await self.egress.allow(uh, added_by="operator", source="project")
                            note += f" · upstream host `{uh}` allowed"
                        up = f" · fork of `{upstream_url}` (read-only `upstream` remote, re-baked each focus)"
                    await self.egress.sync()
                    tok = f" · deploy token from env `{token_env}`" if token_env else ""
                    await reply(
                        f"✅ project `{proj['slug']}` → `{repo}` (post in `#proj-{proj['slug']}` "
                        f"to work on it, or say _\"in {proj['slug']}, …\"_ here){up}{note}{tok}"
                    )
                except Exception as exc:  # noqa: BLE001
                    await reply(f"⚠️ could not add project: {exc}")
            elif sub == "list":
                ps = await self.projects.list()
                await reply(
                    "**Projects:**\n" + "\n".join(
                        f"- `{p['slug']}` → {p['repo_url']} · token {self._project_token_label(p)}"
                        + (f" · ⑂ upstream `{p['upstream_url']}`" if p.get("upstream_url") else "")
                        for p in ps
                    )
                    if ps else "no projects yet — `/project add <name> <repo-url>`"
                )
            elif sub == "remove" and len(args) >= 2:
                ok = await self.projects.remove(args[1], actor="operator")
                await self.egress.sync()
                await reply(f"{'✅ removed' if ok else '⚠️ no such'} project `{args[1]}`")
            else:
                await reply(
                    "usage: `/project add <name> <repo-url> [--upstream <parent-url>] [TOKEN_ENV]` · "
                    "`/project list` · `/project remove <name>`  _(`--upstream` = the fork PARENT, "
                    "baked read-only so the worker can fetch it but push only to the fork; TOKEN_ENV "
                    "= the env var holding this repo's PAT, omit to use the pool's default token)_"
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
