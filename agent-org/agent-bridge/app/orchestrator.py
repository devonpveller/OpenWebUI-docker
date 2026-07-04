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

import httpx

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
from .modules.capabilities import (
    BranchDelivery,
    CapabilityResult,
    bump_submodule,
    fork_repo,
    merge_pull_request,
    open_pull_request,
    parse_owner_repo,
    read_branch_changes,
    read_branch_delivery,
    read_repo_state,
)
from .modules.github_app import GitHubApp, build_github_app
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
from .modules.pending_store import PendingStore
from .schemas import (
    Concern, Decision, Level, LifecyclePlan, LifecycleStep, MonitorVerdict, OperatorIntent, Plan, Trigger,
)
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
    "- advisory: the operator wants to DISCUSS or UNDERSTAND something — a design/architecture "
    "decision, a 'what's the best way to…', 'how should I…', 'what's the industry standard for…', "
    "'compare X vs Y', 'explain the tradeoffs of…' question that is NOT about the status of a specific "
    "effort and is NOT a coding task to dispatch. Set kind=advisory. This runs real research and "
    "replies with a grounded, cited answer — so DON'T try to answer it yourself in `reply`; instead "
    "put a brief 'let me research that' acknowledgement in `reply`.\n"
    "- capability: the operator wants to FORK a repo — 'fork X', 'fork X into my account' → set "
    "kind=capability, `capability`='fork', `repo_url`=the repo. NOT a coding task; I propose it and "
    "the operator approves.\n"
    "- plan: the operator describes a MULTI-STEP setup or ARCHITECTURE to build — 'set up an engine "
    "repo that vendors my forks as submodules', 'wire murder to build on the monogame source', "
    "'scaffold a project that…', anything needing several repo/code steps. Set kind=plan. I draft a "
    "concrete, reviewable plan (fork/submodule/worker steps) for the operator to approve — I do NOT "
    "guess a hardcoded recipe.\n"
    "- question / chitchat otherwise (a quick factual/conversational reply you can give directly).\n"
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

# The ungrounded FALLBACK advisor prompt — used ONLY when the research engine is unreachable, and the
# answer is always posted with a clear "unverified / no citations" label so it's never mistaken for a
# grounded one. Kept scoped to software architecture / engineering practice (the advisory domain).
_ADVISOR_FALLBACK_SYS = (
    "You are a senior software architect advising an engineer. Answer their design / architecture / "
    "best-practice question directly and concretely, with the industry-standard approach and its "
    "tradeoffs. Be specific and practical (name tools, patterns, commands where useful); prefer a "
    "clear recommendation over an exhaustive survey. You do NOT have live research access right now, "
    "so stick to well-established practice and do not fabricate citations, versions, or URLs."
)

# The PLANNER (P-APL.2). Turns a natural-language architectural intent into a CONCRETE, reviewable
# sequence of executable steps — the general mechanism (works for ANY project/architecture), NOT a
# hardcoded recipe. The intelligence is here (the model reasoning), the steps map to governed
# primitives + worker tasks, and the operator approves the whole plan before anything runs.
_PLANNER_SYS = (
    "You are a software architect PLANNER for a governed agent org. The operator describes an "
    "architecture or a multi-step repo/code setup; you produce a CONCRETE, MINIMAL, ORDERED plan of "
    "executable steps for the operator to review and approve. Use ONLY these step kinds:\n"
    "- fork: fork an EXTERNAL repo into the operator's account. `source`='owner/repo'.\n"
    "- add_submodule: add a repo as a git submodule. `source`=the repo/registered-project to add, "
    "`target`=the repo/project to add it INTO, `path`=the mount path (a short dir name).\n"
    "- worker_task: a CODING task a worker performs (edit/build/wire). `target`=the project slug, "
    "`task`=a clear instruction.\n"
    "RULES: order by dependency (fork before you submodule it; submodule before you wire it). "
    "Do NOT re-fork or re-create things already in the REGISTERED PROJECTS list — reference them by "
    "slug. Do NOT invent repos the operator didn't mention. Keep it minimal — only the steps needed. "
    "Each step's `summary` is one plain line. Put assumptions/caveats in `notes`. If you truly can't "
    "form a plan, return an empty steps list.\n"
    "ANCHOR TO THE CURRENT STATE (this is the most important rule — you are given the ACTUAL contents "
    "of each repo): plan the DELTA to reach the desired end-state, like a careful maintainer, NOT a "
    "blind list of adds. Specifically: (1) if a submodule/file the intent wants ALREADY EXISTS at the "
    "right path, do NOT add it again — skip it. (2) if it exists at a DIFFERENT path than the intent "
    "wants (e.g. `murder` at root but the intent wants `vendor/murder`), plan a worker_task to MOVE/"
    "rename it, NOT a duplicate add. (3) if the desired end-state ALREADY HOLDS, return an EMPTY steps "
    "list and say so in `notes`. Keep the repo CLEAN — never leave or create duplicates. "
    "Set `estimate` to a rough time/effort guess for the whole plan (e.g. '~20 min, 1 worker task')."
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
    "- `approve|modify|abort <effort_id> [note]` — approve a drafted **plan**, or decide an open CONCERN "
    "(id optional for `approve`/`abort` when exactly one thing is pending)\n"
    "- `/risk <effort_id> <routine|irreversible|cross_effort|cascading_refactor>` — set blast radius "
    "(risky ⇒ a dry-run is required before real-code execution)\n"
    "- `/dry-run <effort_id> <pass|fail>` — record the isolated dry-run outcome\n"
    "- `/kill` / `/unkill` — global kill switch (freeze/release the whole fleet)\n"
    "Each effort is a **thread** in its `#proj-<project>` channel — reply in the thread to wake "
    "its worker; watch the work stream there. Escalations come to **#mgmt** and their resolution "
    "is echoed back into the effort thread so you get closure.\n"
    "\n**How delivery works:** every effort's work lands on its own branch **`agent/<effort-id>`** "
    "(I verify it on the remote before saying done) and I open a **GitHub PR** so you can review the "
    "diff. **`main` never changes until you merge** — say **“merge it”** and I'll merge the pending "
    "PR, or merge on GitHub yourself. Nothing deploys or merges without you."
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
        github_app: GitHubApp | None = None,
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
        # Capability plane root of trust (autonomous-project-lifecycle P-APL.0). None until the
        # GitHub App is registered — the capability inlets (P-APL.1) refuse with a clear "not set up
        # yet" message while it's None, so the bridge runs normally before the one-time App setup.
        self.github: GitHubApp | None = github_app or build_github_app(settings)
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
        self.events = EventGateway(db, chat, self.handle_event,
                                   max_attempts=settings.event_max_attempts)
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
        # D4 — PRs awaiting the operator's human-gated merge: {merge-<id>: {repo, pr_number,
        # effort_id, mgmt_thread}}. Registered when D1 opens a delivery PR; consumed by
        # `approve merge-<id>` / a plain "merge it". Persisted via PendingStore (kind="merge").
        self._pending_merge: dict[str, dict] = {}
        # INTENT-ANCHORED completion (DELIVERY-PIPELINE §1: the PM judges completeness against the
        # operator-intent thread, not one mechanical effort). Per effort: the registered projects the
        # operator NAMED in the intent that this effort did NOT target — so a `done` on a sub-repo
        # can't hide that the operator's stated target went untouched (the murder-vs-monogame-engine
        # scope miss). Checked at completion → a scope-mismatch flag instead of a false "done".
        self._effort_intent_scope: dict[str, list[str]] = {}
        # Efforts opened but HELD at the readiness gate awaiting operator clarification (P3.8);
        # the operator's next answer resolves them → dispatch. {effort_id: {proj_channel, root, request}}
        self._pending: dict[str, dict] = {}
        # Efforts HELD at the Stage-3 plan-approval gate (P3.9) awaiting operator approval;
        # `approve <effort>` dispatches with the plan's steps. {effort_id: {proj_channel, root, request, plan}}
        self._pending_plan: dict[str, dict] = {}
        # Capability actions (fork/create/…) PROPOSED and awaiting the operator's hard-gate approval
        # before they execute (irreversible/outward → §3). {action_id: {kind, args, description}}.
        self._pending_capability: dict[str, dict] = {}
        # Lifecycle PLANS drafted by the planner (P-APL.2) awaiting operator approval before the
        # executor runs them. {plan_id: {plan: LifecyclePlan, channel_id, thread_id, intent}}.
        self._pending_lifecycle: dict[str, dict] = {}
        # Optional httpx transport for the GitHub-API capability calls — injected in tests
        # (httpx.MockTransport) so the governed flow is exercised without touching real GitHub.
        self._gh_transport = None
        self._bg_tasks: set[asyncio.Task] = set()  # in-flight delegations
        # Capacity park-and-resume (machine B `suspended`, reason=inference_backpressure): an effort
        # whose step is shed by the saturated GPU is PARKED here (DB-backed) instead of failed, and
        # auto-resumed when capacity returns. The resume driver drains one-at-a-time, clocked by a
        # successful model call (self._signal_capacity, fired from the ModelRouter) + a timer tick.
        self.parks = ParkStore(db, self.audit)
        # Durable mirror of the three pending-approval dicts above — a proposed hard gate the operator
        # hasn't decided yet must survive a bridge restart (else a rebuild silently drops it, §3).
        # Rehydrated into the dicts in setup(); rows removed the instant a decision is made.
        self.pending = PendingStore(db, self.audit)
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
        await self._rehydrate_pending()   # restore proposals a prior run held (survives a rebuild, §3)
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
        # Capability plane readiness (P-APL.0): if the GitHub App is configured, verify it reachable +
        # installed at boot so the operator gets a clear confirmation (or a clear failure) in the log.
        if self.github is not None and self.s.chat_adapter != "fake" and self.s.github_app_enabled:
            try:
                info = await self.github.verify()
                log.info("github app VERIFIED — capability plane online (slug=%s owner=%s installation=%s)",
                         info.get("app_slug"), info.get("owner"), info.get("installation_id"))
            except Exception as exc:  # noqa: BLE001 - configured but unreachable → plane stays offline
                log.warning("github app configured but VERIFY FAILED — capability plane offline: %s", exc)
        elif self.s.chat_adapter != "fake":
            log.info("github app not configured — capability plane offline (P-APL.0 setup pending)")
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

    def _effort_of_mgmt_thread(self, thread_id: str | None) -> str | None:
        """Reverse of `_effort_mgmt_thread`: the effort whose #mgmt conversation this thread IS.
        Lets a reply in that conversation inherit the effort's CONTEXT (its project) instead of
        falling to the sandbox — the live 'PR request in the monogame thread landed in proj-sandbox'
        miss. First match wins (a thread maps to one conversation)."""
        if not thread_id:
            return None
        for eid, tid in self._effort_mgmt_thread.items():
            if tid == thread_id:
                return eid
        return None

    async def _resolve_project_slug(
        self, named: str | None, channel_id: str | None = None, effort_name: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """Resolve which project a request belongs to: an explicitly named/onboarded project wins;
        else the originating #proj-<slug> channel's project; else the project of the effort whose
        #mgmt conversation thread this is (a reply in that thread inherits its context); else — the
        fix for 'init-monogame-engine' landing in the sandbox — an UNAMBIGUOUS match of a known
        project's slug inside the effort name; else the fallback (default/sandbox)."""
        if named:
            p = await self.projects.resolve(named)
            if p:
                return p["slug"]
        if channel_id:
            slug = await self.router.resolve_project_by_channel(channel_id)
            if slug:
                return slug
        ctx_eid = self._effort_of_mgmt_thread(thread_id)
        if ctx_eid:
            proj = await self._effort_project(ctx_eid)
            if proj and proj != self.s.default_project:
                return proj
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

    async def _intent_named_projects(self, intent_text: str, exclude_slug: str) -> list[str]:
        """Registered projects the operator NAMED in the intent, minus `exclude_slug` (the effort's own
        target). Longest-slug-first so a shorter slug (`monogame`) can't match inside a longer one
        (`monogame-engine`) — each match is consumed. Grounds the intent-anchored completion check:
        if the operator named a project the effort didn't touch, the PM flags it (DELIVERY-PIPELINE §1
        / governance §3.7 — deliverable-vs-intent), instead of a false 'done'."""
        text = (intent_text or "").lower()
        named: list[str] = []
        for p in sorted(await self.projects.list(), key=lambda x: -len(x["slug"])):
            slug = p["slug"]
            name = (p.get("name") or "").lower()
            token = slug if slug in text else (name if name and name in text else None)
            if not token:
                continue
            text = text.replace(token, " ")   # consume so a shorter slug can't re-match the same span
            if slug != exclude_slug:
                named.append(slug)
        return named

    async def _effort_project(self, effort_id: str) -> str | None:
        """The project slug this effort targets (its registry project), or None."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            return e.project if e else None

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
        # 3) GitHub App installation token — for a repo under the App's account, so workers can
        #    clone/push PRIVATE repos the App manages WITHOUT a per-project PAT (P-APL.1c). Short-
        #    lived + minted per dispatch; the durable retirement of the at-rest deploy token.
        if self.github is not None and self.s.github_app_enabled:
            try:
                owner, _repo = parse_owner_repo(p.get("repo_url", ""))
                if owner.lower() == (self.github.owner or "").lower():
                    return await self.github.installation_token()
            except Exception as exc:  # noqa: BLE001 - fall through to the pool token
                log.debug("App-token fallback skipped for %s: %s", proj, exc)
        # 4) pool default (LC_DEPLOY_TOKEN, on the worker pool).
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
        # NL-FIRST merge (D4): a plain "merge it" / "merge the PR" resolves the pending merge
        # DETERMINISTICALLY (never via the small model — this is an irreversible action; the phrase
        # is the operator's explicit clearance). One pending → merge it (echo which); several →
        # disambiguate; none → fall through to the model (it may be about something else).
        if re.fullmatch(r"(?:please\s+)?merge(?:\s+(?:it|that|the\s+prs?|both))?\s*[.!]*",
                        message.strip(), re.IGNORECASE):
            merges = list(self._pending_merge.keys())
            if len(merges) == 1:
                await self.chat.post(channel_id, f"_(merging the one open PR: `{merges[0]}`)_",
                                     thread_id=thread_id)
                async def _r(msg: str) -> None:
                    await self.chat.post(channel_id, msg, thread_id=thread_id)
                await self._execute_merge(merges[0], _r)
                return
            if "both" in message.lower() and len(merges) > 1:
                for mid in merges:
                    async def _r(msg: str, _mid=mid) -> None:
                        await self.chat.post(channel_id, f"`{_mid}`: {msg}", thread_id=thread_id)
                    await self._execute_merge(mid, _r)
                return
            if merges:
                listing = "\n".join(f"- `{m}` — PR #{self._pending_merge[m].get('pr_number', '?')} on "
                                    f"`{(self._pending_merge[m].get('repo') or '').split('github.com/')[-1]}`"
                                    for m in merges)
                await self.chat.post(
                    channel_id, f"{len(merges)} PRs are awaiting your merge — which one?\n{listing}\n"
                    f"Say `approve <id>`, or **merge both**.", thread_id=thread_id)
                return
            # NOTHING pending — answer deterministically (the model would just get confused): the
            # likeliest reality is the previous PR(s) were already merged.
            await self.chat.post(
                channel_id,
                "Nothing is awaiting a merge right now — the previous PR(s) were already merged or "
                "closed. If you want a PR for a branch, say \"create a PR for `agent/…`\".",
                thread_id=thread_id)
            return
        # NL-FIRST PR request (D1/D4): "create a PR for agent/… [merge if clean]" is an operator-
        # plane capability the bridge does itself — deterministically, never via a worker.
        if await self._nl_pr_request(message, channel_id, thread_id):
            return
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
        # EXCEPT for a capability intent, whose `repo_url` is the fork TARGET, not a repo to onboard.
        if intent.repo_url and intent.kind != "capability":
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
                    intent.project, channel_id, effort_name=intent.effort_name, thread_id=thread_id
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
        elif intent.kind == "advisory" and self.s.advisory_enabled:
            # A design/architecture/best-practice question the operator wants DISCUSSED. Route to the
            # research-grounded advisor (Tier 2): ack now, research in the background, post a grounded
            # + cited answer in-thread. The operator's whole message is the research query.
            await self._advise(message, channel_id, thread_id, reply_prefix=reply)
            return
        elif intent.kind == "capability":
            # A governed structure action (fork/create/…). PROPOSE + hard-gate — never fires from NL
            # directly; the operator clears it with `approve <id>` (P-APL.1, governance §3).
            await self._propose_capability(intent, message, channel_id, thread_id, reply_prefix=reply)
            return
        elif intent.kind == "plan":
            # A multi-step setup/architecture. The PLANNER (P-APL.2) drafts a concrete, reviewable
            # plan from the operator's words; nothing runs until they `approve <plan_id>`. Suppress the
            # classification `reply` — the model's "I'll draft a plan, sound good?" is redundant with
            # (and can contradict) the plan presentation the planner posts itself.
            await self._propose_lifecycle_plan(message, channel_id, thread_id, reply_prefix="")
            return
        elif intent.kind == "reengage":
            # "get the workers working" / "continue" / "re-engage the monogame tasks" — actually
            # RE-DISPATCH idle efforts. This is additive (running work the operator already asked
            # for), so it fires directly from NL — no phantom "they'll proceed as resources free up".
            targets = self._select_efforts(intent, efforts)
            scope = (intent.project or intent.target_filter or "").strip()
            if not targets and scope:
                # A scoped re-engage that matched no IDLE effort. Do NOT grab unrelated efforts — the
                # operator named a project/group, and re-dispatch means EXISTING work. Tell them
                # plainly and offer to START new work (the likely intent when the last one finished).
                await self.chat.post(
                    channel_id,
                    (reply + f"\n\n_There's no idle effort in **{scope}** to re-dispatch — its last "
                     f"effort already finished (or there isn't one yet). I did **not** start "
                     f"anything unrelated. Want me to open a NEW effort on it? Just tell me the task "
                     f"(e.g. “on {scope}, fetch the upstream and integrate it”) and I'll run it._"
                     ).strip(),
                    thread_id=thread_id,
                )
                return
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
        """Resolve which efforts an action targets. Scoping is by an explicit effort_id, a
        name/substring filter, or a named project (matched against BOTH the effort id and its
        project). CRITICAL: a scoped request that matches nothing returns [] — it must NEVER silently
        widen to ALL efforts (that dispatched stale calculator efforts against the monogame workspace
        when 'get the workers working on monogame-engine' had no idle monogame effort to re-dispatch).
        Only a completely UNSCOPED request ('get the workers working', no group named) targets all."""
        ids = {e["id"] for e in open_efforts}
        if intent.effort_id and intent.effort_id in ids:
            return [intent.effort_id]
        filt = (getattr(intent, "target_filter", None) or "").strip().lower()
        proj = (getattr(intent, "project", None) or "").strip().lower()
        scoped = bool(filt or proj or intent.effort_id)
        if scoped:
            def _match(e: dict) -> bool:
                eproj = (e.get("project") or "").lower()
                if filt and (filt in e["id"].lower() or filt in eproj):
                    return True
                if proj and proj == eproj:
                    return True
                return False
            return [e["id"] for e in open_efforts if _match(e)]   # may be [] — never widen to all
        return [e["id"] for e in open_efforts]   # unscoped 'continue' → all idle efforts

    @staticmethod
    def _extract_fork_target(intent, message: str) -> tuple[str, str] | None:
        """Find the (owner, repo) to fork, robust to the small model mis-filling fields: try the
        structured fields, then scan the raw message for a `owner/repo` or github URL. So 'fork
        isadorasophia/murder into my account' works even if the model didn't set repo_url."""
        for cand in (intent.repo_url, intent.project, intent.capability):
            if cand:
                try:
                    return parse_owner_repo(cand)
                except ValueError:
                    pass
        m = re.search(r"(?:https?://github\.com/|git@github\.com:)?"
                      r"([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)", message or "")
        if m:
            try:
                return parse_owner_repo(m.group(1))
            except ValueError:
                pass
        return None

    async def _propose_capability(
        self, intent, message: str, channel_id: str, thread_id: str | None, *, reply_prefix: str = "",
    ) -> None:
        """PROPOSE a capability-plane structure action (P-APL.1) and HARD-GATE it on the operator.
        Deterministic + irreversible/outward → it never fires from fuzzy NL; the operator clears it
        with `approve <id>` (governance §3). Refuses cleanly if the GitHub App isn't set up yet."""
        prefix = (reply_prefix or "").strip()
        if self.github is None or not self.s.github_app_enabled:
            await self.chat.post(
                channel_id,
                (prefix + "\n\n" if prefix else "") +
                "⚠️ The capability plane isn't set up yet — register the GitHub App (see "
                "`SETUP-github-app.md`) and I'll be able to fork repos.",
                thread_id=thread_id,
            )
            return
        # Detect the action from the capability field OR the raw message (the model is unreliable at
        # filling `capability` exactly — don't hinge on it). Multi-repo COMPOSITION is NOT a hardcoded
        # verb here — it's a PLAN the planner produces from natural-language intent (P-APL.2), so the
        # intelligence generalises to any project/architecture instead of being baked into a recipe.
        cap = (intent.capability or "").strip().lower()
        msg_l = (message or "").lower()
        wants_fork = "fork" in cap or "fork" in msg_l
        wants_create = any(w in cap or w in msg_l for w in ("create repo", "new repo"))
        if wants_fork:
            target = self._extract_fork_target(intent, message)
            if target is None:
                await self.chat.post(
                    channel_id, (prefix + "\n\n" if prefix else "") +
                    "Which repo should I fork? Give me its URL or `owner/repo`.",
                    thread_id=thread_id)
                return
            owner, repo = target
            action_id = f"cap-fork-{repo.lower()}"
            self._pending_capability[action_id] = {
                "kind": "fork", "parent": f"{owner}/{repo}", "repo": repo,
                "channel_id": channel_id, "thread_id": thread_id,
            }
            await self.pending.save(action_id, "capability",
                                    self._jsonify_pending(self._pending_capability[action_id]))
            await self.chat.post(
                channel_id,
                (prefix + "\n\n" if prefix else "") +
                f"⛔ **Approval needed** (this creates a repo under `{self.github.owner}`):\n"
                f"> Fork **`{owner}/{repo}`** → **`{self.github.owner}/{repo}`**, tracking "
                f"`{owner}/{repo}` as its read-only upstream.\n\n"
                f"Reply **`approve {action_id}`** to do it, or **`abort {action_id}`** to cancel.",
                thread_id=thread_id,
            )
            await self.audit.log("capability_proposed", payload={"action": action_id, "kind": "fork",
                                                                 "parent": f"{owner}/{repo}"})
            return
        if wants_create:
            await self.chat.post(
                channel_id, (prefix + "\n\n" if prefix else "") +
                "I can't **create** a fresh empty repo — GitHub only allows that for organizations "
                "via an App, not personal accounts. Create the empty repo on GitHub (once), then I "
                "can **compose** it with your forks as submodules.",
                thread_id=thread_id)
            return
        await self.chat.post(
            channel_id, (prefix + "\n\n" if prefix else "") +
            "I can **fork** a repo into your account (_“fork isadorasophia/murder”_). For multi-repo "
            "setups (submodules, composition), just describe the architecture you want and I'll draft "
            "a plan you can approve.",
            thread_id=thread_id)

    async def _execute_capability(self, action_id: str) -> None:
        """Execute a PREVIOUSLY-APPROVED capability action and report the result in its thread. Called
        only from the `approve <id>` control path — never directly from NL (the hard-gate is the fence)."""
        action = self._pending_capability.pop(action_id, None)
        if action is None:
            return
        await self.pending.delete(action_id)   # decided → drop the durable mirror
        channel_id = action["channel_id"]
        thread_id = action.get("thread_id")
        if action["kind"] == "fork":
            parent = action["parent"]
            result: CapabilityResult = await fork_repo(
                self.github, parent, api_base=self.s.github_api_base, transport=self._gh_transport
            )
            await self.audit.log("capability_executed", payload={"action": action_id, "kind": "fork",
                                                                 "parent": parent, "ok": result.ok})
            if not result.ok:
                await self.chat.post(channel_id, f"❌ {result.summary}"
                                     + (f"\n> {result.detail}" if result.detail else ""),
                                     thread_id=thread_id)
                return
            # Register the fork as a project with the parent as its read-only upstream, so a worker can
            # `git fetch upstream` it and the #proj channel exists — the fork is immediately usable.
            slug = action["repo"].lower()
            fork_url = result.url or f"https://github.com/{self.github.owner}/{action['repo']}"
            parent_url = f"https://github.com/{parent}"
            registered = ""
            try:
                await self.projects.add(slug, fork_url, upstream_url=parent_url, created_by="capability")
                await self.egress.sync()
                registered = (f"\n\n_Registered as project **{slug}** (upstream `{parent}`). "
                              f"Say “get the workers working on {slug}” to build in it._")
            except Exception as exc:  # noqa: BLE001 - the fork succeeded; registration is best-effort
                log.warning("fork ok but project register failed for %s: %s", slug, exc)
                registered = (f"\n\n_(Forked, but I couldn't auto-register the project: {exc} — "
                              f"you can register it by NL.)_")
            await self.chat.post(channel_id, f"✅ {result.summary}"
                                 + (f"\n{result.url}" if result.url else "") + registered,
                                 thread_id=thread_id)

    # ── the planner (P-APL.2) + executor (P-APL.3) ─────────────────────────────
    async def _resolve_repo_ref(self, ref: str) -> str | None:
        """A step's `source`/`target` → a git URL: a registered project slug resolves to its repo,
        else an `owner/repo`/URL parses to a github URL. None if unresolvable."""
        ref = (ref or "").strip()
        if not ref:
            return None
        p = await self.projects.resolve(ref)
        if p:
            return p["repo_url"]
        try:
            owner, repo = parse_owner_repo(ref)
            return f"https://github.com/{owner}/{repo}"
        except ValueError:
            return None

    @staticmethod
    def _render_lifecycle_step(i: int, s) -> str:
        if s.kind == "fork":
            return f"{i}. 🍴 **fork** `{s.source}` → your account"
        if s.kind == "add_submodule":
            return f"{i}. 🧩 **submodule** `{s.source}` → `{s.target}` at `{s.path or s.source}`"
        if s.kind == "worker_task":
            return f"{i}. 🔧 **worker task** in `{s.target}`: {s.task.split(chr(10))[0]}"
        if s.kind == "submodule_bump":
            what = s.source or (s.path or "submodule").split("/")[-1]
            return (f"{i}. 🔗 **wire back** — bump `{s.target}`'s `{s.path}` to the new `{what}` "
                    f"commit + commit the engine")
        return f"{i}. {s.summary}"

    async def _augment_composition(self, intent_text: str, steps: list, states: dict) -> tuple[list, str]:
        """DETERMINISTIC composition-awareness (the session pattern — put critical structure in CODE,
        not the small model). A task like "in <engine>, wire <submodule> against <sibling>" is inherently
        multi-repo: edit the submodule's repo, THEN bump the parent/engine's submodule pointer so the
        engine reflects it. The planner under-plans this (a single sub-repo effort). Here, when the intent
        NAMES an engine that vendors the submodule the worker_task targets, we (a) inject the ENGINE
        LAYOUT into the worker task (so relative paths resolve in the vendored tree, not standalone), and
        (b) ensure a `submodule_bump` step exists. Returns (steps, operator-facing note)."""
        import posixpath

        text = (intent_text or "").lower()
        projects = await self.projects.list()

        def _norm(url: str) -> str:
            try:
                o, r = parse_owner_repo(url)
                return f"{o.lower()}/{r.lower()}"
            except ValueError:
                return ""

        repo_to_slug = {_norm(p["repo_url"]): p["slug"] for p in projects if _norm(p["repo_url"])}
        added: list[str] = []
        for eng_slug, st in states.items():
            if not getattr(st, "readable", False) or not getattr(st, "submodule_paths", []):
                continue
            if eng_slug not in text:                    # the engine must be NAMED in the intent
                continue
            sub_path_by_slug: dict[str, str] = {}       # registered submodule slug -> its path in the engine
            for path, url in zip(st.submodule_paths, st.submodule_urls):
                sslug = repo_to_slug.get(_norm(url))
                if sslug:
                    sub_path_by_slug[sslug] = path
            if not sub_path_by_slug:
                continue
            for s in steps:
                if s.kind != "worker_task":
                    continue
                wt_slug = slugify(s.target)
                if wt_slug not in sub_path_by_slug:     # worker_task must target a submodule OF this engine
                    continue
                path = sub_path_by_slug[wt_slug]
                # the sibling submodule the task builds AGAINST = another engine submodule named in intent
                sib = next(((sl, p) for sl, p in sub_path_by_slug.items() if sl != wt_slug and sl in text), None)
                if sib and "COMPOSITION CONTEXT" not in s.task:
                    rel = posixpath.relpath(sib[1], path)   # e.g. vendor/MonoGame from vendor/murder -> ../MonoGame
                    s.task += (
                        f"\n\nCOMPOSITION CONTEXT: `{wt_slug}` is used as a git submodule inside `{eng_slug}` "
                        f"at `{path}`, alongside `{sib[0]}` (the sibling you must build against) at `{rel}` "
                        f"relative to `{wt_slug}`'s repo ROOT. Write any project/path reference to `{sib[0]}` "
                        f"relative to THAT vendored layout (from your edited file's directory: up to the repo "
                        f"root, then follow `{rel}`). Do NOT assume `{wt_slug}` is standalone.")
                elif "COMPOSITION CONTEXT" not in s.task:
                    s.task += (
                        f"\n\nCOMPOSITION CONTEXT: `{wt_slug}` is vendored inside `{eng_slug}` at `{path}`; "
                        f"keep any relative paths valid for that vendored layout, not a standalone checkout.")
                # REPAIR a model-authored bump for this engine (the small model emits the step but
                # fills fields sloppily — e.g. source="" — which would fail executor pairing): fix
                # its path/source so it pairs with THIS worker_task deterministically.
                for x in steps:
                    if x.kind != "submodule_bump" or slugify(x.target) != eng_slug:
                        continue
                    if not (x.path or "").strip():
                        x.path = path
                    if (x.path or "") == path and slugify(x.source) != wt_slug:
                        x.source = wt_slug
                has_bump = any(x.kind == "submodule_bump" and slugify(x.target) == eng_slug
                               and (x.path or "") == path for x in steps)
                if not has_bump:
                    steps.append(LifecycleStep(
                        kind="submodule_bump", target=eng_slug, path=path, source=wt_slug,
                        summary=f"bump {eng_slug}'s {path} to the wired {wt_slug} commit"))
                added.append(f"`{eng_slug}` will be wired back (bump `{path}`) so the engine reflects it")
        note = "  ".join(added)
        return steps, note

    async def _propose_lifecycle_plan(
        self, intent_text: str, channel_id: str, thread_id: str | None, *, reply_prefix: str = "",
    ) -> None:
        """P-APL.2: draft a concrete plan from the operator's NL architectural intent, present it for
        review, and HOLD for approval. Nothing runs until `approve <plan_id>` — the whole plan is the
        gate. The plan is the MODEL's reasoning over the current project state, not a hardcoded recipe."""
        prefix = (reply_prefix or "").strip()
        projects = await self.projects.list()
        projects_ctx = "\n".join(
            f"- {p['slug']} — {p['repo_url']} — upstream: {p.get('upstream_url') or 'none'}"
            for p in projects) or "none"
        # ANCHOR to workspace reality (UX-FLOW Stage 1): read each project's ACTUAL current state
        # (submodules + tree) so the planner reconciles desired-vs-actual instead of blindly adding /
        # duplicating. Best-effort + bounded; a repo the App can't read just contributes nothing.
        states: dict[str, object] = {}                 # slug -> RepoState (structured, for the filter)
        state_lines: list[str] = []
        for p in projects[:8]:
            if self.github is not None and self.s.github_app_enabled:
                st = await read_repo_state(self.github, p["repo_url"],
                                           api_base=self.s.github_api_base, transport=self._gh_transport)
                states[p["slug"]] = st
                if st.readable and st.summary:
                    state_lines.append(f"- {p['slug']}: {st.summary}")
        state_ctx = "\n".join(state_lines) or "(no readable repo state — plan from the intent)"
        try:
            plan: LifecyclePlan = await self.models.structured(
                "po", _PLANNER_SYS,
                f"OPERATOR INTENT:\n{intent_text}\n\nREGISTERED PROJECTS (slug — repo — upstream):\n"
                f"{projects_ctx}\n\nCURRENT STATE OF THE REPOS (the ACTUAL contents — ANCHOR to this; "
                f"do NOT re-add what already exists):\n{state_ctx}\n\nProduce the plan.",
                LifecyclePlan,
            )
        except ModelBackpressureError:
            self._remember(channel_id, thread_id, "operator", intent_text)
            await self.chat.post(channel_id, "⏳ The model's saturated right now — re-send that and "
                                 "I'll draft the plan.", thread_id=thread_id)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("planner failed: %s", exc)
            await self.chat.post(channel_id, "I couldn't turn that into a plan just now — try "
                                 "rephrasing the setup you want.", thread_id=thread_id)
            return
        steps = [s for s in (plan.steps or [])
                 if s.kind in ("fork", "add_submodule", "worker_task", "submodule_bump")]
        if not steps:
            # The model proposed no steps. That often means "nothing to change" (the model saw the
            # state already satisfies the intent but didn't say so) — so SHOW the current state rather
            # than a bare "couldn't plan", and let the operator confirm or add detail.
            note = f" — {plan.notes}" if plan.notes else ""
            if state_lines:
                await self.chat.post(
                    channel_id, (prefix + "\n\n" if prefix else "") +
                    f"I didn't find concrete changes to make for that{note} — it may already be set up. "
                    f"Current state:\n" + "\n".join(f"> {ln}" for ln in state_lines) +
                    "\n\nIf you want me to change/add something specific, or I've misread the intent, "
                    "tell me more and I'll draft it.", thread_id=thread_id)
            else:
                await self.chat.post(
                    channel_id, (prefix + "\n\n" if prefix else "") +
                    "I couldn't break that into concrete steps yet — tell me a bit more about the repos "
                    "and how they should fit together.", thread_id=thread_id)
            return
        # DETERMINISTIC reconciliation (the model doesn't reliably subtract against the anchor): drop
        # add_submodule steps whose target already HAS that submodule path. So the plan PRESENTED is
        # already reconciled — no duplicate adds — regardless of the model's reconciliation quality.
        already: list[str] = []
        reconciled: list = []
        for s in steps:
            if s.kind == "add_submodule":
                st = states.get(slugify(s.target))
                path = (s.path or "").strip()
                if st is not None and getattr(st, "readable", False) and path in getattr(st, "submodule_paths", []):
                    already.append(f"`{s.target}` already has `{path}`")
                    continue
            reconciled.append(s)
        already_note = ("\n\n_Already in place (skipped): " + "; ".join(already) + "._") if already else ""
        if not reconciled:                             # every step was already satisfied
            await self.chat.post(
                channel_id, (prefix + "\n\n" if prefix else "") +
                f"✅ **{plan.goal or intent_text}** — the desired state already holds; nothing to do."
                + already_note, thread_id=thread_id)
            return
        steps = reconciled
        # DETERMINISTIC composition-awareness: if the intent targets an ENGINE that vendors a submodule
        # the task wires, give the worker the engine LAYOUT (correct relative paths) + ensure the
        # wiring-back (submodule_bump) so the ENGINE reflects the change — not just the submodule.
        steps, comp_note = await self._augment_composition(intent_text, steps, states)
        if comp_note:
            already_note += f"\n\n_🧩 {comp_note}_"
        plan.steps = steps
        base = re.sub(r"[^a-z0-9]+", "-", (plan.goal or intent_text).lower()).strip("-")[:24] or "setup"
        plan_id = f"plan-{base}"
        n = 1
        while plan_id in self._pending_lifecycle:
            n += 1; plan_id = f"plan-{base}-{n}"
        self._pending_lifecycle[plan_id] = {
            "plan": plan, "channel_id": channel_id, "thread_id": thread_id, "intent": intent_text}
        await self.pending.save(plan_id, "lifecycle",
                                self._jsonify_pending(self._pending_lifecycle[plan_id]))
        body = "\n".join(f"> {self._render_lifecycle_step(i, s)}" for i, s in enumerate(steps, 1))
        note = f"\n\n_{plan.notes}_" if plan.notes else ""
        est = f"\n_Estimate: {plan.estimate}_" if plan.estimate else ""
        await self.chat.post(
            channel_id,
            (prefix + "\n\n" if prefix else "") +
            f"📋 **Plan** — {plan.goal or intent_text}\n{body}{note}{est}{already_note}\n\n"
            f"Reply **`approve {plan_id}`** to run it, **`abort {plan_id}`** to drop it, or tell me "
            f"what to change and I'll redraft.",
            thread_id=thread_id,
        )
        await self.audit.log("lifecycle_plan_drafted", payload={
            "plan": plan_id, "steps": [s.kind for s in steps]})

    async def _execute_lifecycle_plan(self, plan_id: str) -> None:
        """P-APL.3: run an APPROVED plan step-by-step, dispatching each to its governed primitive
        (fork/add_submodule) or a worker task. Reports a per-step result. Called only from the
        `approve <id>` path — the approval IS the gate."""
        entry = self._pending_lifecycle.pop(plan_id, None)
        if entry is None:
            return
        await self.pending.delete(plan_id)     # decided → drop the durable mirror
        plan: LifecyclePlan = entry["plan"]
        channel_id = entry["channel_id"]
        thread_id = entry.get("thread_id")
        steps = plan.steps
        results: list[str] = []
        # 1) forks
        for s in [s for s in steps if s.kind == "fork"]:
            res: CapabilityResult = await fork_repo(
                self.github, s.source, api_base=self.s.github_api_base, transport=self._gh_transport)
            if res.ok:
                try:
                    owner, repo = parse_owner_repo(s.source)
                    await self.projects.add(repo.lower(), res.url or f"https://github.com/{self.github.owner}/{repo}",
                                            upstream_url=f"https://github.com/{owner}/{repo}", created_by="planner")
                    await self.egress.sync()
                except Exception:  # noqa: BLE001
                    pass
            results.append(("✅" if res.ok else "❌") + f" {res.summary}")
        # 2) submodules, grouped by target repo (one focus+push per target)
        by_target: dict[str, list[tuple[str, str]]] = {}
        for s in [s for s in steps if s.kind == "add_submodule"]:
            turl = await self._resolve_repo_ref(s.target)
            surl = await self._resolve_repo_ref(s.source)
            if not turl or not surl:
                results.append(f"❌ submodule `{s.source}`→`{s.target}`: couldn't resolve a repo")
                continue
            by_target.setdefault(turl, []).append((surl, s.path or parse_owner_repo(surl)[1]))
        for turl, subs in by_target.items():
            try:
                token = await self.github.installation_token()
            except Exception as exc:  # noqa: BLE001
                results.append(f"❌ submodules into {turl}: no token ({exc})"); continue
            ok, detail, added = await self.router.compose_submodules(turl, subs, token=token)
            results.append(("✅" if ok else "❌") +
                           (f" submodules {', '.join('`'+p+'`' for p in added)} → {turl.split('github.com/')[-1]}"
                            if ok else f" submodules → {turl.split('github.com/')[-1]}: {detail}"))
        # 3) worker tasks — some are the coding half of a COMPOSITION (paired with a submodule_bump on
        #    the engine); those run as a coordinated sequence (edit submodule → verify → bump the engine)
        #    so the ENGINE reflects the change, not just the submodule.
        all_bumps = [b for b in steps if b.kind == "submodule_bump"]
        bumps_by_source = {slugify(b.source): b for b in all_bumps if (b.source or "").strip()}
        worker_steps = [s for s in steps if s.kind == "worker_task"]
        paired: set[int] = set()
        for s in worker_steps:
            proj = await self.projects.resolve(s.target)
            if not proj:
                results.append(f"❌ worker task: unknown project `{s.target}`"); continue
            # Carry the INTENT THREAD into the worker's goal (UX-FLOW §0/Stage 5 — "the intent thread
            # rides along as each worker's grounded goal") + an explicit reconcile-don't-duplicate
            # directive, so the worker maintains the repo cleanly instead of working context-free.
            goal = (f"{s.task}\n\n_Context — this is part of the effort: {plan.goal}. First orient in "
                    f"the repo's CURRENT state, then reconcile toward that goal: build on / move what "
                    f"already exists rather than duplicating it, and keep the repo clean._")
            bump = bumps_by_source.get(proj["slug"])
            if bump is None and len(all_bumps) == 1 and len(worker_steps) == 1:
                # Unambiguous fallback: one worker task + one wire-back → pair them even if the model
                # left the bump's `source` blank/mismatched (belt-and-braces under the augmenter repair).
                bump = all_bumps[0]
            if bump is not None:
                paired.add(id(bump))
            try:
                eid, chan, root = await self.router.open_effort(
                    slugify(s.task)[:24] or "task", project=proj["slug"], goal=goal)
                await self.charters.set_goal(eid, goal, created_by="planner")
                if thread_id:
                    self._effort_mgmt_thread[eid] = thread_id
                if bump is not None:
                    # COMPOSITION: edit the submodule, then bump the engine's pointer. The coordinator
                    # reports both branches; DON'T set the intent-scope flag (the engine IS updated by
                    # the bump, so it's not an untouched stated target).
                    self._spawn(self._run_composition(eid, chan, root, goal, s, bump, plan, thread_id))
                    results.append(f"▶ composition on `{proj['slug']}` → wire back into "
                                   f"`{slugify(bump.target)}`: {s.task.splitlines()[0][:50]}")
                else:
                    # Intent-anchored completion: record any project the operator NAMED that this effort
                    # is NOT targeting, so a `done` on `proj` can't hide an untouched stated target.
                    others = await self._intent_named_projects(
                        entry.get("intent", "") or plan.goal, proj["slug"])
                    if others:
                        self._effort_intent_scope[eid] = others
                    self._spawn(self.delegate(eid, chan, root, goal))
                    results.append(f"▶ dispatched worker on `{proj['slug']}`: {s.task.splitlines()[0][:50]}")
            except Exception as exc:  # noqa: BLE001
                results.append(f"❌ worker task on `{proj['slug']}`: {exc}")
        # A wire-back step that couldn't be paired with any worker task must be SAID, not silently
        # dropped — otherwise the plan shows a step that never runs (a phantom promise).
        for b in all_bumps:
            if id(b) not in paired:
                results.append(f"⚠️ wire-back `{b.target}`/`{b.path}` had no matching worker task — skipped")
        await self.audit.log("lifecycle_plan_executed", payload={"plan": plan_id, "results": len(results)})
        await self.chat.post(
            channel_id, "**Plan run:**\n" + "\n".join(f"- {r}" for r in results),
            thread_id=thread_id)

    async def _run_composition(self, eid, chan, root, goal, worker_step, bump_step, plan, mgmt_thread) -> None:
        """Coordinated COMPOSITION (Phase 2 / autonomous-project-lifecycle §11d): run the submodule edit
        on the WORKER plane, verify it landed on the remote, then bump the ENGINE's submodule pointer on
        the OPERATOR plane (App Git Data API — no checkout) so the ENGINE reflects the change. Reports
        BOTH branches. Everything additive; merge to the engine's `main` stays human-gated (D4)."""
        engine_slug = slugify(bump_step.target)
        mgmt = mgmt_thread or self._mgmt_thread_of(eid)
        # 1) the submodule code edit (worker plane) — awaited (this coroutine is already backgrounded).
        #    Its own completion posts the submodule branch; we then wire it back into the engine.
        await self.delegate(eid, chan, root, goal)
        # 2) verify the submodule branch landed → its exact commit (the bump target). No commit = no bump.
        s_repo = await self._effort_repo(eid)
        delivery = await self._verify_delivery(eid, s_repo) if s_repo else BranchDelivery(branch="")
        if not (delivery.landed and delivery.head_sha):
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{eid}** — composition halted: the `{worker_step.target}` edit didn't land a "
                f"verified commit, so `{engine_slug}`'s `{bump_step.path}` was **not** bumped. Fix the "
                f"edit (see the effort thread) and re-run.",
                thread_id=mgmt,
            )
            return
        # 3) bump the engine's submodule → an engine branch (same name as the submodule's, so they pair)
        engine_url = await self.projects.repo_for(engine_slug) or await self._resolve_repo_ref(bump_step.target)
        branch = self._effort_branch(eid)
        if self.github is None or not engine_url:
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{eid}** — the `{worker_step.target}` edit landed, but I can't wire it into "
                f"`{engine_slug}` (no engine repo / GitHub App). The engine wasn't updated.",
                thread_id=mgmt,
            )
            return
        res = await bump_submodule(
            self.github, engine_url, bump_step.path, delivery.head_sha,
            branch=branch, api_base=self.s.github_api_base, transport=self._gh_transport,
        )
        await self.audit.log(
            "composition_wired", effort_id=eid,
            payload={"engine": engine_slug, "path": bump_step.path, "ok": res.ok,
                     "commit": delivery.head_sha, "branch": branch},
        )
        if res.ok:
            short = delivery.head_sha[:10]
            # D1: PRs make BOTH halves visible — the code change (submodule repo) + the wiring
            # (engine repo, gitlink bump). Each PR is separately mergeable; merges stay yours (D4).
            code_pr = await self._open_delivery_pr(
                eid, s_repo, delivery.branch, verified_sha=delivery.head_sha,
                body_extra=f"This is the CODE half of a composition — `{engine_slug}` vendors it at "
                           f"`{bump_step.path}` (see the engine's paired PR).\n")
            engine_pr = await self._open_delivery_pr(
                eid, engine_url, branch, merge_id=f"merge-{eid}-engine",
                body_extra=f"This is the WIRING half of a composition: bumps `{bump_step.path}` to the "
                           f"updated `{worker_step.target}` commit `{short}`"
                           + (f" (code PR: {code_pr})" if code_pr else "") + ".\n")
            prs = ""
            if engine_pr or code_pr:
                prs = ("\n📬 **PRs opened for review:**"
                       + (f"\n- engine wiring: {engine_pr}" if engine_pr else "")
                       + (f"\n- code change: {code_pr}" if code_pr else "")
                       + "\n_`main` only changes when you merge — say **“merge it”** (I'll ask which "
                         "if both are pending), or merge on GitHub after review._")
            await self.comms.post(
                Intent.closure,
                f"🔗 **Composition wired** — `{worker_step.target}` branch **`{delivery.branch}`** (the "
                f"code change) + `{engine_slug}` branch **`{branch}`** (its `{bump_step.path}` bumped to "
                f"`{short}`). `git fetch origin {branch}` in `{engine_slug}` for the wired engine. Merge "
                f"to `main` stays human-gated.{prs}",
                effort_id=eid,
            )
            await self.comms.post(
                Intent.operator_reply,
                f"🔗 **{eid}** wired the composition: `{engine_slug}` branch **`{branch}`** now vendors "
                f"the updated `{worker_step.target}` (`{bump_step.path}` → `{short}`). Fetch it to test; "
                f"merge is human-gated.{prs}",
                thread_id=mgmt,
            )
        else:
            await self.router.update_effort_card(eid, "needs-attention")
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{eid}**: the `{worker_step.target}` edit landed (branch `{delivery.branch}`), but "
                f"bumping `{engine_slug}`'s `{bump_step.path}` failed — {res.summary}. The engine was "
                f"**not** updated.",
                thread_id=mgmt,
            )

    async def _advise(
        self, question: str, channel_id: str, thread_id: str | None, *, reply_prefix: str = "",
    ) -> None:
        """Tier-2 advisor: answer a design/architecture question with a research-grounded, CITED
        answer. Acks immediately (a research job takes minutes), runs it in the background, and posts
        the grounded answer + sources in-thread. If research is unavailable, posts a clearly-labelled
        UNGROUNDED local-model take (honest — never a silent, uncited guess)."""
        ack = (reply_prefix or "").strip()
        ack = (ack + "\n\n" if ack else "") + (
            "🔎 _Researching that against current sources — I'll post a grounded, cited answer here "
            "in a moment (a full research pass can take a minute or two)._"
        )
        await self.chat.post(channel_id, ack.strip(), thread_id=thread_id)
        self._spawn(self._run_advisory(question, channel_id, thread_id))

    async def _run_advisory(
        self, question: str, channel_id: str, thread_id: str | None
    ) -> None:
        """Background half of `_advise`: run the research job, then post the grounded answer (or the
        labelled local fallback). Isolated in its own task so the operator's turn returns immediately
        and a slow/failed research job never wedges the intake loop."""
        ans = None
        try:
            ans = await self.grounding.advise(question)
        except Exception as exc:  # noqa: BLE001 - degrade to the local fallback below
            log.warning("advisory research raised: %s", exc)
        if ans is not None and ans.grounded and (ans.answer or "").strip():
            body = ans.answer.strip()
            if ans.sources:
                srcs = "\n".join(f"- {s}" for s in ans.sources[:12])
                body += f"\n\n**Sources**\n{srcs}"
            await self.chat.post(channel_id, body, thread_id=thread_id)
            return
        # Research unavailable/timed out → an honest, clearly-labelled ungrounded local answer.
        local = ""
        try:
            local = (await self.models.complete("po", _ADVISOR_FALLBACK_SYS, question)).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("advisory local fallback failed: %s", exc)
        if local:
            await self.chat.post(
                channel_id,
                "⚠️ _I couldn't reach the research engine to ground this, so here's my best take from "
                f"general knowledge — **unverified, no citations**:_\n\n{local}",
                thread_id=thread_id,
            )
        else:
            await self.chat.post(
                channel_id,
                "⚠️ I couldn't reach the research engine to answer that just now — please try again "
                "shortly.",
                thread_id=thread_id,
            )

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
             f"it live. Its work will land on branch `agent/{effort_id}` (+ a PR for your review) — "
             f"`main` only changes when you merge. If you'd tackle it differently, just say so and "
             f"I'll steer it. I'll summarize back here when done._").strip(),
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
        await self.pending.save(effort_id, "effort_plan",
                                self._jsonify_pending(self._pending_plan[effort_id]))
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
        await self.pending.delete(effort_id)   # decided → drop the durable mirror
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
                # PM-as-monitor (governance §4.2 / F8): publish, then INDEPENDENTLY VERIFY the branch
                # landed. A worker's turn ending `done` is not delivery; the PM checks the remote and,
                # on non-delivery, re-engages once then escalates — it does NOT rubber-stamp "done".
                delivery = await self._publish_and_verify(effort_id, channel_id, root_post_id, repo)
                if delivery is None:   # verified-undelivered after a re-engage → escalated, NOT done
                    return
                await self._finish_effort(effort_id, last, delivery=delivery)
            else:
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

    async def _publish_effort(
        self, effort_id: str, channel_id: str, root: str, repo: str, *, firm: bool = False
    ) -> None:
        """Commit + push the effort's work to its feature branch so it's DURABLE (survives a
        /project wipe), VISIBLE to the team, and fetchable for A→B hand-off. Additive push to a
        feature branch is routine (floor); push-to-main/deploy stay human-gated. Deterministic
        finalize wake — not a reviewable deliverable, so it skips the review gate. Commits carry the
        AGENT's identity (via GIT_AUTHOR/COMMITTER env — git-proxy-safe, since `-c` is blocked).
        `firm=True` is the PM's RE-ENGAGE after verification found no landed branch: it states plainly
        the task is not complete until pushed, and asks the worker to explicitly report if there were
        genuinely no changes (so 'forgot to push' is distinguishable from 'nothing to do')."""
        branch = self._effort_branch(effort_id)
        name, email = self._agent_identity("worker-default")
        ident = (
            f'GIT_AUTHOR_NAME="{name}" GIT_AUTHOR_EMAIL="{email}" '
            f'GIT_COMMITTER_NAME="{name}" GIT_COMMITTER_EMAIL="{email}"'
        )
        lead = (
            "YOUR CHANGES ARE NOT PUBLISHED — I checked the remote and there is no `"
            f"{branch}` branch with your commit. The task is NOT complete until it is pushed. "
            "Run these git steps EXACTLY now"
            if firm else
            "PUBLISH YOUR WORK so the team can see it (additive, allowed). Run these git steps EXACTLY"
        )
        tail = (
            "If you genuinely made NO file changes, do NOT invent any — instead reply exactly "
            "`NO CHANGES: <why>` so I can report that. Otherwise reply with the branch name and the "
            "pushed commit hash."
            if firm else
            "Then reply with the branch name and the pushed commit hash."
        )
        instruction = (
            f"{lead} (the env prefix on the commit attributes it to you, `{name}`):\n"
            f"  git checkout -b {branch} 2>/dev/null || git checkout {branch}\n"
            f"  git add -A\n"
            f'  {ident} git commit -m "{effort_id}: <one-line summary of your changes>"   # skip only if nothing to commit\n'
            f"  git push -u origin {branch}\n"
            f"Do NOT push to main/master. Do NOT force-push or delete anything. {tail}"
        )
        try:
            # Pass repo + a CURRENT token: the worker is already focused, so the daemon NOOPs (work
            # preserved) but RE-BAKES origin's auth — the token embedded at clone time is short-lived
            # (App token, 1h) and a long task / NOOP re-focus outlives it, killing the push with a
            # dead credential (the live "expired token in origin" failure).
            repo_token = await self._project_token(effort_id)
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=effort_id, instruction=instruction, repo=repo, repo_token=repo_token,
            )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            # The worker daemon rejected the dispatch (409 busy) or was unreachable — router.wake has
            # already quarantined it. Don't let a transient publish hiccup crash the finalize path —
            # record it as a failed self-report and let VERIFICATION be the arbiter (it re-engages if
            # nothing landed). NoCapacityError still propagates so delegate parks + auto-resumes.
            log.warning("publish wake dispatch failed for %s: %s", effort_id, exc)
            result = None
        # NOTE: result.ok is only the worker's turn-ended signal — NOT proof anything pushed. The
        # branch is CONFIRMED by _verify_delivery against the remote, never by this self-report.
        self._published_branch[effort_id] = branch if (result and result.ok) else ""
        await self.audit.log(
            "effort_published", effort_id=effort_id,
            payload={"branch": branch, "self_reported_ok": bool(result and result.ok), "firm": firm},
        )

    async def _verify_delivery(self, effort_id: str, repo: str) -> BranchDelivery:
        """PM's checkable acceptance signal (§4.2): independently read the remote to see if the effort's
        branch landed with a real commit. Own-account only via the App; any error/other-owner ⇒
        `verifiable=False` (the PM then falls back to the self-report, honestly labelled unverified)."""
        branch = self._effort_branch(effort_id)
        if self.github is None or not self.s.github_app_enabled:
            return BranchDelivery(branch=branch, detail="GitHub App not enabled")
        try:
            return await read_branch_delivery(
                self.github, repo, branch,
                api_base=self.s.github_api_base, transport=self._gh_transport,
            )
        except Exception as exc:  # noqa: BLE001 — verification must never crash the finalize path
            log.debug("delivery verification failed for %s: %s", effort_id, exc)
            return BranchDelivery(branch=branch, detail=str(exc)[:120])

    async def _publish_and_verify(
        self, effort_id: str, channel_id: str, root: str, repo: str
    ) -> BranchDelivery | None:
        """The PM's monitor→verify→re-engage→escalate loop for the deliverable (governance §4.2/F8;
        UX-FLOW Stage 5→6). Publishes, then VERIFIES the branch landed on the remote. If it didn't and
        we can verify, re-engages the worker ONCE with a firm publish instruction and re-checks; if it
        STILL hasn't landed, ESCALATES to the operator and returns None (the effort is NOT marked done —
        it stays visible in /status). Returns the BranchDelivery to hand to _finish_effort otherwise
        (a verified `landed`, or an `unverifiable` verdict the closure labels honestly)."""
        await self._publish_effort(effort_id, channel_id, root, repo)
        delivery = await self._verify_delivery(effort_id, repo)
        if delivery.landed or not delivery.verifiable:
            return delivery   # verified-landed, or we couldn't check (finish labels it unverified)

        # Verified NON-delivery — the worker's turn ended but nothing landed. This is the exact
        # deviation the PM must catch (a `done` that didn't deliver). Re-engage ONCE, firmly.
        gap = ("created the branch but committed nothing" if delivery.exists
               else "pushed no branch")
        await self.comms.post(
            Intent.worker_activity,
            f"🔍 the worker reported done, but I checked the remote and it {gap} — the change hasn't "
            f"landed. Re-dispatching with an explicit commit + push instruction (PM monitor, §4.2).",
            effort_id=effort_id,
        )
        await self._publish_effort(effort_id, channel_id, root, repo, firm=True)
        delivery = await self._verify_delivery(effort_id, repo)
        if delivery.landed:
            return delivery

        # Still undelivered after a re-engage → climb the ladder (§3). Do NOT mark done; the operator
        # decides (retry / investigate / accept as no-op). Intent-framed so the WHY reaches them.
        await self._escalate_undelivered(effort_id, delivery)
        return None

    async def _escalate_undelivered(self, effort_id: str, delivery: BranchDelivery) -> None:
        """Undelivered-after-re-engage escalation (§3 ladder). The change did not land even after the
        PM re-dispatched; surface it honestly UP to the operator (never a false 'done') and mark the
        card so it doesn't read as complete."""
        empty = delivery.verifiable and delivery.exists  # branch exists but 0 commits over base
        why = ("its branch has no new commits over the base — either the task needed no code change, "
               "or the work wasn't done" if empty else
               "no branch with the work reached the remote")
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{effort_id}** finished but the change **did not land** — {why}. I re-dispatched the "
            f"publish once and it still didn't. ↑ raised to you: re-run it, or confirm this is expected.",
            effort_id=effort_id,
        )
        await self.router.update_effort_card(effort_id, "error")
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}** ran but I could **not verify the change landed** — {why}. It is **not** "
            f"marked done. Reply to re-run it, or say it's expected and I'll close it.",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        await self.audit.log(
            "effort_undelivered", effort_id=effort_id,
            payload={"exists": delivery.exists, "ahead": delivery.ahead, "branch": delivery.branch},
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

    async def _open_delivery_pr(
        self, effort_id: str, repo: str | None, branch: str, *,
        merge_id: str | None = None, verified_sha: str = "", body_extra: str = "",
    ) -> str:
        """D1 — open the PR that makes a delivered branch VISIBLE (the corpus's 'promotion artifact':
        a branch push is easy to miss; a PR shows in GitHub's UI/notifications with the diff). The PR
        body carries the intent + branch + verification; merge stays HUMAN-GATED (D4) — the message
        invites a plain "merge it". Best-effort: a PR failure never blocks the closure. Returns the
        PR url ('' if none)."""
        if not (repo and self.s.auto_pr and self.github is not None and self.s.github_app_enabled):
            return ""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            name = (e.name if e else "") or effort_id
        goal = ""
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
            goal = (goal_text or "").strip().splitlines()[0][:300] if goal_text else ""
        except Exception:  # noqa: BLE001 — goal text is garnish; never block the PR
            pass
        merge_id = merge_id or f"merge-{effort_id}"
        # The PR body is DESCRIPTIVE of the delivery (corpus D1: intent + changes + verification) —
        # chat instructions ("say merge it") live in Mattermost, not here.
        base_br, commits, files = await read_branch_changes(
            self.github, repo, branch, api_base=self.s.github_api_base, transport=self._gh_transport)
        parts: list[str] = []
        if goal:
            parts.append(f"## What this delivers\n{goal}")
        if body_extra:
            parts.append(body_extra.strip())
        if commits:
            parts.append("## Changes\n" + "\n".join(f"- {c}" for c in commits))
        if files:
            parts.append(f"## Files touched ({len(files)})\n" + "\n".join(f"- {f}" for f in files))
        parts.append(f"Branch `{branch}`"
                     + (f" verified on the remote @ `{verified_sha[:10]}`" if verified_sha else "")
                     + (f", against `{base_br}`" if base_br else "") + ".")
        parts.append("---\n_Opened by agent-org (DELIVERY-PIPELINE D1); merge is human-gated (D4)._")
        body = "\n\n".join(parts)
        res = await open_pull_request(
            self.github, repo, branch, title=f"agent: {name}", body=body,
            api_base=self.s.github_api_base, transport=self._gh_transport,
        )
        if not res.ok:
            log.warning("delivery PR for %s failed: %s", effort_id, res.summary)
            return ""
        try:
            pr_number = int(res.detail or "0")
        except ValueError:
            pr_number = 0
        self._pending_merge[merge_id] = {
            "repo": repo, "pr_number": pr_number, "effort_id": effort_id,
            "mgmt_thread": self._mgmt_thread_of(effort_id) or "",
        }
        await self.pending.save(merge_id, "merge", self._pending_merge[merge_id])
        await self.audit.log("delivery_pr_opened", effort_id=effort_id,
                             payload={"repo": repo, "pr": pr_number, "merge_id": merge_id})
        return res.url

    async def _execute_merge(self, merge_id: str, reply=None) -> None:
        """D4 — perform the operator-approved merge (the approve IS the §3 clearance for this
        irreversible action). Merge commit via the host API (--no-ff equivalent); the result is
        posted UP (operator thread) and echoed DOWN into the effort thread (bring-back-down)."""
        entry = self._pending_merge.pop(merge_id, None)
        if entry is None:
            return
        await self.pending.delete(merge_id)
        res = await merge_pull_request(
            self.github, entry["repo"], int(entry.get("pr_number") or 0),
            api_base=self.s.github_api_base, transport=self._gh_transport,
        )
        await self.audit.log("pr_merge_decided", effort_id=entry.get("effort_id"),
                             payload={"merge_id": merge_id, "ok": res.ok, "pr": entry.get("pr_number")})
        icon = "✅" if res.ok else "⚠️"
        if reply is not None:
            await reply(f"{icon} {res.summary}" + (f" — {res.url}" if res.ok and res.url else ""))
        eid = entry.get("effort_id")
        if eid:   # bring the audience back down (CM.4)
            await self.comms.post(Intent.closure, f"{icon} {res.summary} (operator-approved merge).",
                                  effort_id=eid)

    async def _nl_pr_request(self, message: str, channel_id: str, thread_id: str | None) -> bool:
        """Operator-plane catch (D1/D4): 'create/open a PR for <branch> [merge if clean]' is a
        CAPABILITY the bridge performs via the App — NEVER a worker task (a worker has no host-API
        access; the live miss dispatched one to do nothing in the sandbox). Deterministic, like
        "merge it" — PR/merge are governed actions, not fuzzy-NL material. A composition branch can
        exist on SEVERAL onboarded repos (code + engine): PRs open for each. An explicit merge
        instruction in the operator's words ("proceed with merge", "merge if clean") is the §3
        clearance — logged verbatim — and each PR is merged if GitHub reports it mergeable.
        Returns True when the message was handled here."""
        if not re.search(r"\b(?:create|open|raise|make)\b[^.\n]{0,40}?\b(?:pr|pull\s+request)s?\b",
                         message, re.IGNORECASE):
            return False

        async def say(msg: str) -> None:
            await self.chat.post(channel_id, msg, thread_id=thread_id)

        if self.github is None or not self.s.github_app_enabled:
            await say("⚠️ I can't open PRs — the GitHub App isn't set up (see SETUP-github-app.md).")
            return True
        mb = re.search(r"\b(agent/[\w./-]+)", message)
        if not mb:
            await say("Which branch should the PR be for? (e.g. `agent/effort-…` — say "
                      "\"create a PR for <branch>\")")
            return True
        branch = mb.group(1).rstrip(".")
        # Every onboarded repo where this branch actually exists (a composition delivery lands on 2).
        hits: list[tuple[str, str]] = []
        for p in (await self.projects.list())[:8]:
            d = await read_branch_delivery(self.github, p["repo_url"], branch,
                                           api_base=self.s.github_api_base, transport=self._gh_transport)
            if d.verifiable and d.exists:
                hits.append((p["slug"], p["repo_url"]))
        if not hits:
            await say(f"I couldn't find `{branch}` on any onboarded repo — check the branch name?")
            return True
        merge_wanted = re.search(r"\bmerge\b", message, re.IGNORECASE) is not None
        if merge_wanted:
            await self.audit.log("operator_premerge_clearance",
                                 payload={"branch": branch, "phrase": message[:300]})
        ctx_eid = self._effort_of_mgmt_thread(thread_id) or ""
        lines: list[str] = []
        for slug, repo_url in hits:
            merge_id = f"merge-{slugify(branch.split('/')[-1])[:20]}-{slug}"[:64]
            url = await self._open_delivery_pr(ctx_eid or branch, repo_url, branch, merge_id=merge_id)
            if not url:
                lines.append(f"⚠️ `{slug}`: couldn't open a PR for `{branch}` — see the logs.")
                continue
            if merge_wanted:
                entry = self._pending_merge.get(merge_id) or {}
                res = await merge_pull_request(
                    self.github, repo_url, int(entry.get("pr_number") or 0),
                    api_base=self.s.github_api_base, transport=self._gh_transport)
                self._pending_merge.pop(merge_id, None)
                await self.pending.delete(merge_id)
                await self.audit.log("pr_merge_decided", effort_id=ctx_eid or None,
                                     payload={"merge_id": merge_id, "ok": res.ok, "pre_authorized": True})
                lines.append((f"✅ `{slug}`: PR opened + **merged** (you pre-cleared it) — {url}"
                              if res.ok else
                              f"⚠️ `{slug}`: PR opened ({url}) but the merge didn't go through — "
                              f"{res.summary} It stays open for you."))
            else:
                lines.append(f"📬 `{slug}`: PR opened — {url} — say **“merge it”** and I'll merge.")
        await say("\n".join(lines))
        return True

    async def _finish_effort(self, effort_id: str, result, *, delivery: BranchDelivery | None = None) -> None:
        """All steps cleared → closure DOWN into the effort thread + a summary UP to #mgmt (§2). When a
        repo was focused, `delivery` is the PM's VERIFIED verdict on the branch (§4.2): a verified
        `landed` states the branch + commit factually; an `unverifiable` one is labelled as the
        worker's self-report we couldn't independently check — never a bare, over-confident 'pushed'."""
        head = ((result.output or "").strip().splitlines()[0][:200]
                if result and result.output else "done")
        # The worker's self-report (its turn ended ok); the VERIFIED verdict overrides it as the truth.
        self_reported = self._published_branch.pop(effort_id, None)
        branch = delivery.branch if (delivery and delivery.landed) else None
        if delivery is not None and delivery.landed:
            sha = f" @ `{delivery.head_sha[:10]}`" if delivery.head_sha else ""
            where = (f"pushed to branch **`{branch}`**{sha} (verified on the remote) — "
                     f"`git fetch origin {branch}` to see it")
            # D1: open the PR that makes this delivery VISIBLE; merge stays yours (D4).
            pr_url = await self._open_delivery_pr(
                effort_id, await self._effort_repo(effort_id), branch,
                verified_sha=delivery.head_sha)
            if pr_url:
                where += (f"\n📬 **PR opened for review:** {pr_url}\n_`main` only changes when you "
                          f"merge — say **“merge it”** and I'll merge, or merge on GitHub after review._")
        elif delivery is not None and not delivery.verifiable and self_reported:
            # We couldn't independently check (App can't read this repo) — report the worker's word,
            # labelled honestly as unverified rather than asserting it as fact (§4.2 unverified).
            where = (f"the worker reports it pushed **`{self_reported}`**, which I could **not "
                     f"independently verify** (this repo isn't on the App's account)")
        elif await self._effort_repo(effort_id):
            # The project HAS a repo but no branch landed — say so honestly, don't imply "no repo".
            where = ("the worker pushed **no branch** — nothing was committed/published. If it should "
                     "have, re-run it and tell it to commit + push its changes")
        else:
            where = "changes are in the worker's workspace (no repo focused to publish to)"
        # INTENT-ANCHORED completion (DELIVERY-PIPELINE §1 / §3.7): the effort did its mechanical work,
        # but if the operator NAMED a target this effort didn't touch, the OPERATOR'S goal isn't
        # necessarily met — surface that as a deviation instead of a clean "done", so a sub-repo change
        # can't masquerade as the whole intent (the murder-branch-but-monogame-engine-untouched miss).
        unmet = self._effort_intent_scope.pop(effort_id, [])
        scope_note = ""
        if unmet:
            listed = ", ".join(f"`{s}`" for s in unmet)
            scope_note = (
                f"\n\n⚠️ **Scope check:** your request also named {listed}, which this effort did "
                f"**not** change (it worked on `{await self._effort_project(effort_id) or 'its repo'}`). "
                f"If your goal needs {listed} updated too — e.g. a composition where the parent repo's "
                f"submodule must be bumped — that part is **not done**. Say the word and I'll plan it."
            )
        done_word = "done" if not unmet else "partly done — see the scope check"
        await self.comms.post(
            Intent.closure,
            f"✅ worker finished (**{done_word}**) — {where}. Merge to `main`/deploy stay "
            f"human-gated.{scope_note}",
            effort_id=effort_id,
        )
        # A scope-unmet effort did its piece but the INTENT is incomplete → mark the card
        # 'needs-attention' and keep it visible in /status (don't silently close the operator's goal).
        await self.router.update_effort_card(effort_id, "needs-attention" if unmet else "done")
        if not unmet:
            await self.gate.set_lifecycle(effort_id, "done")  # drops out of the default /status view
        await self.comms.post(
            Intent.operator_reply,
            f"{'✅' if not unmet else '⚠️'} **{effort_id}** finished (**{done_word}**): {head}\n"
            f"_{where[0].upper() + where[1:]}._{scope_note}",
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

    @staticmethod
    def _jsonify_pending(entry: dict) -> dict:
        """A JSON-safe copy of a pending-store entry for persistence: any pydantic plan under `plan`
        is `model_dump`'d; everything else is already str/None. The in-memory dict keeps the live
        object — only the persisted mirror is flattened."""
        out = dict(entry)
        plan = out.get("plan")
        if hasattr(plan, "model_dump"):
            out["plan"] = plan.model_dump(mode="json")
        return out

    async def _rehydrate_pending(self) -> None:
        """Boot: restore the three in-memory pending dicts from the durable store so a proposal held
        across a restart is still resolvable (a bare/keyed `approve` finds it). A payload that no
        longer deserializes (schema drift) is dropped, not fatal — boot must never wedge on it."""
        for row in await self.pending.all():
            pid, kind, payload = row["id"], row["kind"], dict(row["payload"])
            try:
                if kind == "lifecycle":
                    payload["plan"] = LifecyclePlan(**payload["plan"])
                    self._pending_lifecycle[pid] = payload
                elif kind == "capability":
                    self._pending_capability[pid] = payload
                elif kind == "effort_plan":
                    payload["plan"] = Plan(**payload["plan"])
                    self._pending_plan[pid] = payload
                elif kind == "merge":
                    self._pending_merge[pid] = payload
                else:
                    continue
            except Exception as exc:  # noqa: BLE001 — a drifted row must not crash boot; drop it
                log.warning("dropping unrehydratable pending %s (%s): %s", pid, kind, exc)
                await self.pending.delete(pid)
        n = (len(self._pending_lifecycle) + len(self._pending_capability)
             + len(self._pending_plan) + len(self._pending_merge))
        if n:
            log.info("rehydrated %d pending approval(s) held across a restart", n)

    async def _pending_decisions(self) -> list[str]:
        """Every item currently awaiting an explicit operator decision — drafted lifecycle plans
        (P-APL.3), proposed capability actions (P-APL.1), held Stage-3 effort plans (P3.9), and
        efforts frozen on a concern (§3). De-duped, insertion order. Used so a bare `approve`/`abort`
        (no id) can resolve THE single pending item unambiguously instead of erroring with a usage
        string — the operator typed the decision verb explicitly; we only fill an unambiguous
        target."""
        ids: list[str] = [
            *self._pending_lifecycle.keys(),
            *self._pending_capability.keys(),
            *self._pending_plan.keys(),
            *self._pending_merge.keys(),
        ]
        try:
            efforts = await self.gate.snapshot(open_only=True)
            smap = await self._effort_status_map(efforts)
            ids += [e["id"] for e in efforts if smap.get(e["id"]) == "paused"]
        except Exception as exc:  # noqa: BLE001 — status enumeration must never break the command
            log.debug("_pending_decisions status sweep failed: %s", exc)
        seen: set[str] = set()
        return [i for i in ids if not (i in seen or seen.add(i))]

    def _render_pending(self, only: str | None = None) -> str:
        """The queue of proposals awaiting an `approve <id>` — drafted plans, proposed forks, held
        effort plans — rendered for `/status` so a restart-restored (or scrolled-past) hard gate is
        VISIBLE without re-asking. `only` limits it to a single id (targeted `/status <id>`). Empty
        string when nothing (matching) is pending."""
        items: list[tuple[str, str]] = []
        for pid, e in self._pending_lifecycle.items():
            plan = e.get("plan")
            goal = (getattr(plan, "goal", None) or e.get("intent") or "plan").strip()
            n = len(getattr(plan, "steps", []) or [])
            items.append((pid, f"📋 plan: {goal} ({n} step{'' if n == 1 else 's'})"))
        for aid, e in self._pending_capability.items():
            items.append((aid, f"🛠️ fork `{e.get('parent', '?')}`"))
        for mid, e in self._pending_merge.items():
            items.append((mid, f"🔀 merge PR #{e.get('pr_number', '?')} on "
                               f"`{(e.get('repo') or '').split('github.com/')[-1]}` — say “merge it”"))
        for eid, e in self._pending_plan.items():
            plan = e.get("plan")
            feat = (getattr(plan, "feature_overview", None) or e.get("request") or "").strip()
            items.append((eid, f"📋 effort plan: {feat[:80]}"))
        if only is not None:
            items = [(i, d) for (i, d) in items if i == only]
        if not items:
            return ""
        lines = "\n".join(f"- `{i}` — {d}" for i, d in items)
        hint = ("_Reply `approve` or `abort` — it's the only thing pending._" if len(items) == 1
                else "_Reply `approve <id>` or `abort <id>`._")
        return "**⛔ Awaiting your approval:**\n" + lines + "\n" + hint

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
            # Proposals awaiting `approve <id>` — always shown, and the ONLY thing to show after a
            # restart when nothing's running (why this got surfaced). Targeted view filters to that id.
            pending_block = self._render_pending(only=target)
            if snap:
                status_map = await self._effort_status_map(snap)
                header = "**Efforts (open):**" if not (want_all or target) else "**Efforts:**"
                out = header + "\n" + self._render_status(snap, status_map)
            elif target:
                out = pending_block or f"no effort `{target}`."
                pending_block = ""                        # folded into `out` already
            elif want_all:
                out = "no efforts yet — create one with `/effort <name>`"
            else:
                out = "no open efforts — everything's done/aborted. `/status all` shows the history."
            if pending_block:
                out += "\n\n" + pending_block
            await reply(out)
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
            if args:
                effort_id, note = args[0], " ".join(args[1:])
            elif cmd == "modify":
                # `modify` conveys a change — it needs both the target and the note.
                await reply(f"usage: `{cmd} <effort_id> [note]`")
                return
            else:
                # NL-first: a bare `approve`/`abort` resolves THE single pending decision when
                # there's exactly one (the operator gave the verb; we fill the unambiguous target
                # and echo it — decisions stay crisp + auditable, §3). Otherwise disambiguate.
                cands = await self._pending_decisions()
                if len(cands) == 1:
                    effort_id, note = cands[0], ""
                    await reply(f"_(no id given — resolving the only item awaiting you: `{effort_id}`)_")
                elif not cands:
                    await reply("nothing's awaiting your approval right now.")
                    return
                else:
                    listing = " · ".join(f"`{c}`" for c in cands)
                    await reply(f"{len(cands)} items await you — which? `{cmd} <id>`\n{listing}")
                    return
            # Lifecycle-plan approval (P-APL.3) — the operator approves the WHOLE plan, then it runs.
            if effort_id in self._pending_lifecycle:
                if cmd == "approve":
                    await reply(f"▶ Running plan `{effort_id}`…")
                    await self._execute_lifecycle_plan(effort_id)
                else:
                    self._pending_lifecycle.pop(effort_id, None)
                    await self.pending.delete(effort_id)
                    await reply(f"⛔ Plan `{effort_id}` dropped — nothing ran.")
                return
            # Capability approval (fork/create/…) — the hard-gate on a proposed structure action.
            if effort_id in self._pending_capability:
                if cmd == "approve":
                    await reply(f"▶ Executing `{effort_id}`…")
                    await self._execute_capability(effort_id)
                else:
                    self._pending_capability.pop(effort_id, None)
                    await self.pending.delete(effort_id)
                    await self.audit.log("capability_aborted", payload={"action": effort_id})
                    await reply(f"⛔ `{effort_id}` cancelled — nothing was created.")
                return
            # Stage-3 plan approval takes precedence over a CONCERN clear when a plan is pending.
            if effort_id in self._pending_plan:
                if cmd == "approve":
                    await self.approve_effort_plan(effort_id)
                    await reply(f"✅ plan approved for `{effort_id}` — dispatching a worker.")
                else:
                    self._pending_plan.pop(effort_id, None)
                    await self.pending.delete(effort_id)
                    await reply(
                        f"⛔ plan {cmd} for `{effort_id}` — not dispatched. "
                        f"Re-send the request with your changes to adjust it."
                    )
                return
            # D4 — the human-gated merge: the operator's approve IS the §3 clearance; the bridge
            # merges via the host API (merge commit = --no-ff). Abort leaves the PR open on GitHub.
            if effort_id in self._pending_merge:
                if cmd == "approve":
                    await self._execute_merge(effort_id, reply)
                else:
                    self._pending_merge.pop(effort_id, None)
                    await self.pending.delete(effort_id)
                    await reply(f"👍 not merging `{effort_id}` — the PR stays open on GitHub for "
                                f"review; merge it there whenever you're ready.")
                return
            # A plan/capability id that reached here isn't pending — it already ran, was dropped, or
            # expired (a rebuild clears un-approved proposals). It is NOT a CONCERN to resolve, so say
            # that plainly instead of the confusing "no open concern for effort <plan-id>" fallthrough.
            if effort_id.startswith(("plan-", "cap-", "merge-")):
                await reply(
                    f"`{effort_id}` isn't awaiting approval — it already ran, was dropped, or expired "
                    f"(a rebuild clears un-approved proposals). Re-send the request to draft a fresh one."
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
