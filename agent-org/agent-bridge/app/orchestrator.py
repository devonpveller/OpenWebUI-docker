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
from .modules.envs import hosts_for_images
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
    close_pull_request,
    delete_branch,
    read_added_lines,
    read_branch_delivery,
    read_broken_gitlinks,
    read_sibling_agent_prs,
    read_repo_state,
    merge_branch,
    read_open_pr_numbers,
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
from sqlalchemy import func, select

from .models import Effort, Event, GlobalState
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

# Deterministic cues that a message is a WORK request (junk-intent repair, live 2026-07-05 miss:
# a pasted build-error list junk-misfired the classifier twice and the fix request was dropped).
_WORK_CUE_RE = re.compile(
    r"\b(fix|repair|resolve|debug|patch|implement|build|add|update|upgrade|wire|create|refactor|"
    r"remove|rename|move|migrate|integrate|install)\w*\b", re.I)
_ERROR_REPORT_RE = re.compile(
    r"\berrors?\b|\bexception\b|\btraceback\b|stack trace|could not be found|unable to find|"
    r"\bcannot find\b|\bfail(s|ed|ure|ing)?\b", re.I)
# "continue its PREVIOUS/LAST task" (singular) — the operator means ONE interrupted effort, not a
# fleet-wide fan-out (live 2026-07-05: an unscoped re-engage dispatched 5 stale efforts at once,
# double-booking both workers). Deliberately does NOT match the plural ("the tasks").
_SINGULAR_TASK_RE = re.compile(r"\b(?:previous|last|its)\s+(?:\w+\s+)?task\b", re.I)

# A worker signalling it is BLOCKED / the workspace is INSUFFICIENT / the task isn't feasible as
# scoped — either via the explicit protocol or in its own words. The PM must ELEVATE this, not
# steamroll it with a mechanical "commit + push" (live 2026-07-07: the worker said plainly "the
# standalone build fails because ../MonoGame isn't present in this workspace" and the PM ignored
# it entirely). Kept broad on purpose — a real constraint stated any reasonable way must be heard.
_BLOCKER_RE = re.compile(
    r"^\s*BLOCKED:|(?:is|are|it'?s)\s+not\s+present\s+in\s+this\s+workspace|"
    r"not\s+(?:present|available|found)\s+in\s+(?:this|the)\s+workspace|"
    r"workspace\s+(?:lacks|doesn'?t\s+have|is\s+missing|does\s+not\s+(?:have|contain))|"
    r"(?:isn'?t|is\s+not|aren'?t|are\s+not)\s+present\s+in\s+(?:this|the|its)\b|"
    r"standalone\s+build\s+fails\s+because|can(?:not|'?t)\s+(?:verify|build|compile|run)\b[^.\n]*"
    r"\b(?:because|since|as|without)\b|"
    r"(?:sibling|nested)\s+submodule[^.\n]*(?:isn'?t|is\s+not|not)\s+(?:present|available|there)|"
    r"\bnot\s+(?:feasible|possible)\s+(?:in|as|here|with)\b|missing\s+(?:dependency|dependencies)|"
    r"insufficient\s+context|need(?:s)?\s+(?:the\s+)?(?:host|engine|parent)\s+(?:repo|context)",
    re.I | re.M)

# ── ORG-READ BUILD LOGS (operator 2026-07-07: "the PM should have access to logs") ──────────
# The org runs builds ITSELF and reasons over the real output — error counts, categories and
# per-file clusters come from the log, never from a worker's self-report (live: the first true
# delivery in 10+ rounds was reported "delivered NOTHING NEW" because no one ever built it;
# 138 real errors surfaced only in the operator's own IDE). Generic across toolchains.
_ERR_AFTER_RE = re.compile(r"^\s*ERRORS\s+AFTER\s*:\s*(\d+)", re.I | re.M)
_ERR_PROTO_RE = re.compile(r"^\s*ERRORS(?:\s+TOTAL)?\s*:\s*(\d+)", re.I | re.M)
_ERR_SUMMARY_RES = [
    re.compile(r"(\d+)\s+Error\(s\)", re.I),                          # MSBuild
    re.compile(r"Found\s+(\d+)\s+errors?", re.I),                     # tsc
    re.compile(r"(\d+)\s+errors?\s+generated", re.I),                 # clang
    re.compile(r"aborting due to\s+(\d+)\s+previous errors", re.I),   # rustc
    re.compile(r"[=\s](\d+)\s+failed", re.I),                         # pytest/jest
]
_ERR_LINE_RE = re.compile(r"\berror\b\s*(?:[A-Z]{1,5}\d{2,5})?\s*:", re.I)
_ERR_FILE_RE = re.compile(r"^\s*(\S+?\.[A-Za-z0-9]{1,6})\s*[\(:]")


def _error_lines(output: str) -> list[str]:
    """Distinct error lines from a build log (MSBuild repeats each error in the final summary
    with a `[project]` suffix — normalize that away so counts aren't doubled)."""
    seen: set[str] = set()
    out: list[str] = []
    for ln in (output or "").splitlines():
        if not _ERR_LINE_RE.search(ln):
            continue
        key = re.sub(r"\s*\[[^\]]*\]\s*$", "", ln.strip())
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _error_count(output: str) -> int | None:
    """Total errors in a build log / check report: the org's own `ERRORS:` protocol first, then a
    toolchain summary line, then the distinct-error-line count. None = no error evidence at all."""
    m = _ERR_AFTER_RE.search(output or "") or _ERR_PROTO_RE.search(output or "")
    if m:
        return int(m.group(1))
    best: int | None = None
    for rx in _ERR_SUMMARY_RES:
        for sm in rx.finditer(output or ""):
            n = int(sm.group(1))
            best = n if best is None or n > best else best
    if best is not None:
        return best
    lines = _error_lines(output)
    return len(lines) if lines else None


def _error_files(output: str) -> dict[str, list[str]]:
    """Error lines clustered by the file they name (lines with no recognizable file → '')."""
    clusters: dict[str, list[str]] = {}
    for ln in _error_lines(output):
        m = _ERR_FILE_RE.match(ln)
        clusters.setdefault(m.group(1) if m else "", []).append(ln)
    return clusters


def _error_brief(output: str) -> str:
    """One honest line of SCOPE for the operator — count, file spread, top categories — straight
    from the log (the PM 'expresses what effort the workers are about to undergo')."""
    from collections import Counter
    lines = _error_lines(output)
    n = _error_count(output) or len(lines)
    if not n:
        return "no machine-readable errors in the log"
    files = {f for f in _error_files(output) if f}
    cats = Counter()
    for ln in lines:
        m = _ERR_LINE_RE.search(ln)
        cats[ln[m.end():].strip()[:70] or ln[:70]] += 1
    top = "; ".join(f"“{msg}” ×{c}" for msg, c in cats.most_common(3))
    return (f"{n} error(s)" + (f" across {len(files)} file(s)" if files else "")
            + (f" — top: {top}" if top else ""))

# An ERROR-REPORT effort closes when the reported failure is GONE, not when a plausible edit
# exists — the worker must reproduce, fix, and re-verify (live 2026-07-05: four attempts shipped
# without anyone but the operator ever running the failing build).
_VERIFY_CLAUSE = (
    "\n\nREQUIRED VERIFICATION BEFORE PUBLISHING: reproduce the reported failure in your "
    "workspace (run the same build/command that produced these errors, or the closest equivalent "
    "available here), apply your fix, re-run it, and CONFIRM every reported error is gone. "
    "Include the command and the tail of its passing output in your final report. If the repro "
    "cannot run in this environment, say exactly which part you could not verify and why — a fix "
    "that merely looks right does not close an error report.\n"
    "End your final report with a block starting exactly `ERROR VERDICTS:` listing EVERY error "
    "line the operator reported, each marked `RESOLVED` (with how you verified) or "
    "`NOT RESOLVED` (with what is still needed). A missing or partial verdict block means the "
    "report is treated as a PARTIAL fix, not a resolution.\n"
    "IF THE TASK CANNOT BE COMPLETED OR VERIFIED IN THIS WORKSPACE — a needed dependency, file, "
    "or sibling repo is ABSENT; the requirement is ambiguous or self-contradictory; or you lack "
    "the context to judge feasibility — do NOT force a fix, do NOT claim NO CHANGES, and do NOT "
    "stop silently. Report it, in exactly this shape, and it is a SUCCESSFUL outcome:\n"
    "`BLOCKED:` <the specific obstacle — name the missing thing / exact error / ambiguity>\n"
    "`NEEDS:` <what would unblock you — a dependency at a path, the host/parent repo, a clarified "
    "requirement>\n"
    "`FEASIBLE:` <yes-with-that | no | unknown-because-X>\n"
    "A clear BLOCKED report is how the org learns the task's real constraints and fixes them; "
    "guessing or faking progress is the only failure.\n"
    "MINIMAL DIFF: change ONLY what the reported errors require. Do not vendor, restructure or "
    "add components the errors don't demand — if a structurally bigger change seems necessary, "
    "SAY SO in your report (with why) instead of doing it (operator: an error fix is not an "
    "invitation to redesign)."
)


def _compact_paste(text: str, max_lines: int = 40, max_chars: int = 2500) -> str:
    """Compact a pasted wall (build errors, logs) for a SMALL-model prompt: collapse repeated
    lines (annotated `repeated ×N` so no information is lost) and cap the total. Used ONLY for
    the classification/readiness calls — the FULL text always remains the effort goal, so the
    worker sees everything. (Live miss: 10 near-identical csproj errors pushed the constrained
    small model into junk output twice.)"""
    nonblank = [ln.strip() for ln in text.splitlines() if ln.strip()]
    has_dupes = len(set(nonblank)) < len(nonblank)   # repetition harms even a SHORT paste
    if len(text) <= max_chars and text.count("\n") < max_lines and not has_dupes:
        return text
    counts: dict[str, int] = {}
    order: list[str] = []
    for raw in text.splitlines():
        key = raw.strip()
        if not key:                      # keep paragraph breaks; never dedupe blanks
            order.append(raw)
            continue
        if key in counts:
            counts[key] += 1
            continue
        counts[key] = 1
        order.append(raw)
    out = [f"{raw}  (repeated ×{counts[raw.strip()]})"
           if raw.strip() and counts[raw.strip()] > 1 else raw
           for raw in order]
    omitted = 0
    if len(out) > max_lines:
        omitted = len(out) - max_lines
        out = out[:max_lines]
    compact = "\n".join(out)
    if len(compact) > max_chars:
        compact = compact[:max_chars]
        omitted += 1                     # signal char-cap truncation too
    if omitted:
        compact += (f"\n… (+{omitted} more line(s) omitted here for brevity — the FULL text is "
                    f"kept in the task goal)")
    return compact

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
    "- remove an upstream from an EXISTING project: 'remove/clear the upstream on X', 'X isn't a "
    "fork — drop its upstream', 'stop treating X as a fork' → set `project`=X + "
    "`remove_upstream`=true (no upstream_url, no repo_url). I clear it + confirm.\n"
    "- set a project CHECK (the build/test that must pass before any merge is offered): they "
    "describe it in plain words — 'before merging engine changes make sure Murder.sln builds', "
    "'the check for X is <cmd>', 'every delivery on X must pass <cmd>' → set `project`=X + "
    "`check_cmd`=the exact shell command (verbatim when they give one; when they DESCRIBE it, "
    "write the command yourself from the paths they've used, e.g. `dotnet build "
    "vendor/murder/Murder.sln`). 'remove/clear the check on X' → check_cmd='' + project. I "
    "confirm + run it on every delivered branch (red routes back to the worker).\n"
    "- set a project's STANDING INTENT (a durable architectural rule the org must never trade "
    "away): 'X must always <invariant>', 'the rule for X is <invariant>', 'X must build from "
    "<A>, never <B>', 'don't ever let X do <thing>' → set `project`=X + `standing_intent`=the "
    "invariant in plain words, and BACKTICK any term that is FORBIDDEN so I can enforce it at the "
    "diff level (e.g. standing_intent='murder builds from the vendored MonoGame source; never use "
    "the `Murder.FNA` NuGet package or a `PackageReference` to it'). 'clear the standing intent "
    "on X' → standing_intent='' + project. I inject it into every effort on X and REJECT any "
    "delivery that reintroduces a forbidden term.\n"
    "- request: they want NEW work done on an EXISTING project → set effort_name to a short "
    "kebab-case slug (it becomes a thread in the project channel). If they name a project to work "
    "on, set `project` to it (match a KNOWN PROJECT). In your `reply`, do the thinking-partner thing: "
    "(1) reflect the goal back in a line so they know you got it; (2) state your intended APPROACH at "
    "a high level; (3) if a real fork-in-the-road exists (a choice that changes the result), name the "
    "option(s) + your recommendation. Do NOT invent frivolous questions and do NOT claim you "
    "started/dispatched anything — a readiness check runs next and will pause ONLY if a genuine "
    "blocker remains (governance F5 — a question is cheaper than a misaligned worker). "
    "A BUG or BUILD-FAILURE report is a request too: pasted compiler/build errors, stack traces "
    "or failing logs about an existing project mean 'diagnose and fix these' — set kind=request + "
    "`project` + an effort_name like fix-<area>-build-errors, even when the message ALSO asks what "
    "the operator must run in their LOCAL dev environment (the worker fixes what's repo-side and "
    "its answer reports any local steps — do NOT route this to advisory, and do NOT try to solve "
    "the errors yourself in `reply`).\n"
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
    "guess a hardcoded recipe. CHALLENGE STRUCTURAL ASSUMPTIONS: when the ask embeds a structure "
    "decision (e.g. 'add X as a submodule', 'vendor Y'), your `reply` must say what GOAL that "
    "structure serves, whether it is actually feasible/buildable as asked, and name the simpler "
    "alternative if one exists — never silently execute a structure the goal doesn't need "
    "(operator: 'the PM should have called out that it's not possible, or proposed a solution, "
    "rather than blindly creating a submodule').\n"
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

# De-biasing rewrite for SHORT-CHECK answers (research-engine-for-OB GROUNDING-MODEL principle,
# operator-specified): in shallow context the operator's framing dominates the context pool, and
# models favor the user's implied goal (sycophancy). The source→claim system avoids this by asking
# the objective question WITHOUT the goal in context; an ungrounded fallback can't claim-check, so
# it approximates the same discipline by NEUTRALIZING the question first. Deliberately given NO
# conversation history, NO goals — just the question.
_NEUTRALIZE_SYS = (
    "Rewrite the user's question as a single NEUTRAL, NON-LEADING question. Remove presuppositions, "
    "implied preferences, expected answers, and any framing that suggests what the asker hopes is "
    "true — keep only the factual subject matter to investigate. Do not answer it. Output ONLY the "
    "rewritten question, nothing else."
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
    "diff. If the project has a check command (`/project check <name> \"<cmd>\"`), I run it on the "
    "branch first and a failure routes back to the worker — red never reaches you. **`main` never "
    "changes until you merge** — say **“merge it”** and I'll merge the pending PR, or merge on GitHub "
    "yourself. After a merge I hand you the human-testing step; if it's broken, just say what's wrong "
    "and I'll open a fix effort. Nothing deploys or merges without you."
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
        # slug → (ts, host-tuple|None) cache for _vendored_host (repo-state reads are API calls).
        self._vendor_host_cache: dict[str, tuple[float, tuple | None]] = {}
        # Efforts whose composition wiring is owned by a lifecycle PLAN (_run_composition) — the
        # intake-delivery auto-wiring must not double-bump those.
        self._composition_managed: set[str] = set()
        # Efforts whose COMPOSITION CHECK (host build of the wiring branch) failed — the closure
        # must land "partly done" and the effort stays open; a red never travels forward.
        self._comp_check_failed: set[str] = set()
        # Efforts whose dispatch was refused by the KILL SWITCH — released automatically the
        # moment the operator lifts it (no re-asking; "say resume and I'll re-dispatch").
        self._kill_blocked: set[str] = set()
        # Branch head BEFORE this run dispatched (per effort) — a "landed" verdict whose head
        # equals this is a PRE-EXISTING branch resurrected, not a delivery (live 2026-07-07: an
        # empty-workspace run "delivered" yesterday's stale branch and wired an OLD commit).
        self._pre_dispatch_head: dict[str, str] = {}
        # Evolved goals queued by a machine-detected failure (red check / unresolved verdicts) —
        # launched by delegate's finally the moment the current run closes (auto-iteration).
        self._iterate_after: dict[str, str] = {}
        # BURN-DOWN (operator 2026-07-07: "all 138 errors should have been worked through
        # autonomously and not elevated in the first place"): a RED org-run build doesn't stop at
        # a fixed retry count — it starts a progress-based loop that keeps dispatching fix rounds
        # while the error count still falls. Failing logs queued here by paths inside delegate
        # are launched by delegate's finally (single-flight-safe).
        self._burndown_after: dict[str, str] = {}
        # effort → branch head the ORG itself verified green (its own build run + log, not a
        # worker's word) — the finish path skips a duplicate composition check and the closure is
        # labelled "org-verified". Cleared on every fresh dispatch.
        self._org_verified: dict[str, str] = {}
        # Monotonic counter for BUILD-VERIFICATION sessions (org build check / composition check):
        # these MUST run in a FRESH, isolated session — reusing the effort's work session made the
        # little-coder agent no-op (live 2026-07-08: the org build check ran 0 commands, returned
        # empty → verdict "unknown" → the burn-down never engaged). A stateless "clone, build,
        # report" task must not inherit the port work's conversational context.
        self._verify_seq = 0
        # Advisory research jobs IN FLIGHT (transparency: PM work must be visible) — shown in
        # /status; updated by the state-driven poll's progress callback. {key: {question, state,
        # started}}. In-memory only (a restart orphans the job on the engine side, harmlessly).
        self._advisories: dict[str, dict] = {}
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
        self._research_transport = None   # test hook for the repo-sync trigger's httpx calls
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
        # upstream bake failed → verify the registry against the forge's ACTUAL fork parent and
        # self-heal a wrong config (D0.f), instead of warning about tokens forever.
        self.router.on_upstream_fail = self._heal_project_upstream
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
            # ENV-TEMPLATE egress (operator principle: fixes are orchestration abstractions):
            # the ACTIVE sidecar toolchain images declare their package registries (envs.py);
            # activating a template (AO_OT*_IMAGE — the operator's own act) IS the clearance, so
            # the default-deny worker egress widens to exactly those registries automatically.
            for h in sorted(hosts_for_images(self.s.ot1_image, self.s.ot2_image)):
                await self.egress.allow(h, added_by="env-template", source="env")
            await self.egress.sync()  # seed + project + env hosts → the mounted tinyproxy filter
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

    @staticmethod
    def _norm_repo(url: str) -> str:
        """owner/name in lowercase for equality checks across URL spellings (.git, trailing /)."""
        u = (url or "").strip().rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        return u.split("github.com/")[-1].lower()

    async def _heal_project_upstream(self, repo: str, upstream: str) -> str | None:
        """A worker focus reported the upstream bake FAILED. Recover autonomously when the forge
        can PROVE the registry is wrong: read the repo's ACTUAL fork parent via the App —
        - not a fork at all  → the configured upstream is a registry mistake (an NL mishap):
          CLEAR it and say so;
        - a fork of a DIFFERENT parent → CORRECT the registry to the real parent;
        - the parent matches → the config is right and the parent is genuinely private or
          unreachable → return None (the caller keeps the honest token/URL warning).
        Generic for any registered project; own-account repos only (that's what the App can
        verify); fails open — an unverifiable state never mutates the registry."""
        if self.github is None or not self.s.github_app_enabled:
            return None
        slug = None
        for p in await self.projects.list():
            if self._norm_repo(p.get("repo_url") or "") == self._norm_repo(repo):
                slug = p["slug"]
                break
        if not slug:
            return None
        try:
            owner, name = parse_owner_repo(repo)
            if owner.lower() != (self.github.owner or "").lower():
                return None
            token = await self.github.installation_token()
            base = self.s.github_api_base.rstrip("/")
            async with httpx.AsyncClient(timeout=15.0, transport=self._gh_transport) as c:
                r = await c.get(
                    f"{base}/repos/{owner}/{name}",
                    headers={"Authorization": f"token {token}",
                             "Accept": "application/vnd.github+json"},
                )
            if r.status_code != 200:
                return None
            parent = ((r.json().get("parent") or {}).get("full_name") or "")
        except Exception as exc:  # noqa: BLE001 — an unverifiable state never mutates the registry
            log.debug("upstream heal check failed for %s: %s", repo, exc)
            return None
        if parent and parent.lower() == self._norm_repo(upstream):
            return None   # config is RIGHT — genuine reachability/auth problem; keep the warning
        if parent:
            fixed = f"https://github.com/{parent}"
            await self.projects.set_upstream(slug, fixed)
            await self.audit.log("project_upstream_healed",
                                 payload={"slug": slug, "was": upstream, "now": fixed})
            return (f"🩹 `{slug}`'s configured upstream (`{upstream}`) doesn't match its ACTUAL "
                    f"fork parent on GitHub — corrected the registry to `{fixed}` (verified via "
                    f"the App). It bakes as the `upstream` remote on the next focus.")
        await self.projects.set_upstream(slug, None)
        await self.audit.log("project_upstream_healed",
                             payload={"slug": slug, "was": upstream, "now": None})
        return (f"🩹 `{slug}` is **not a fork** (verified via the GitHub App), so its configured "
                f"upstream (`{upstream}`) was a registry mistake — cleared it; this warning stops "
                f"on the next focus. Re-add any time: _\"set <url> as upstream on {slug}\"_.")

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
        # RS.2: onboarding → the repo's docs become knowledge immediately (background, announced).
        self._spawn(self._repo_sync(proj["slug"], announce_channel=channel_id,
                                    announce_thread=thread_id))
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

    async def _project_scoped_in(self, message: str) -> tuple[dict | None, str]:
        """Deterministically resolve WHICH registered project a message is about — the junk-intent
        repair's grounding. Three tiers, most explicit first:
          1. 'prefix' — the documented `in <project>, …` idiom at the start;
          2. 'phrase' — an explicit `project <name>` anywhere ("from project `MonoGame-Engine`");
          3. 'named'  — a registered slug/display-name in the text (longest-first so `monogame`
             can't shadow `monogame-engine`; earliest mention wins; sandbox never matches).
        Returns (project_row | None, tier)."""
        m = re.match(r"^\s*(?:@\S+\s+)?in\s+([A-Za-z0-9][\w-]*)\s*[,:]", message, re.I)
        if m:
            p = await self.projects.resolve(m.group(1))
            if p:
                return p, "prefix"
        m = re.search(r"\bproject\s+[`'\"]?([A-Za-z0-9][\w.-]*)", message, re.I)
        if m:
            p = await self.projects.resolve(m.group(1))
            if p:
                return p, "phrase"
        text = message.lower()
        best: tuple[int, dict] | None = None
        for p in sorted(await self.projects.list(), key=lambda x: -len(x["slug"])):
            if p["slug"] == self.s.default_project:
                continue
            for token in (p["slug"], (p.get("name") or "").lower()):
                if not token:
                    continue
                mm = re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text)
                if mm:
                    if best is None or mm.start() < best[0]:
                        best = (mm.start(), p)
                    text = text[:mm.start()] + " " * len(token) + text[mm.end():]  # consume span
                    break
        return (best[1], "named") if best else (None, "")

    async def _post_unactionable(self, channel_id: str, thread_id: str | None,
                                 message: str) -> None:
        """The honest 'couldn't act on that' fallback. If the message DID name registered
        projects, say which — so the operator's rephrase costs one line, not a guessing game."""
        named = await self._intent_named_projects(message, exclude_slug="")
        hint = ""
        if named:
            hint = (f" I did recognize {', '.join(f'`{s}`' for s in named)} — for work on it, try "
                    f"_“in {named[0]}, <task>”_.")
        await self.chat.post(
            channel_id,
            "I couldn't turn that into something actionable — could you rephrase? "
            "(e.g. _“in <project>, <task>”_ for work, or ask a design question for the advisor.)"
            + hint,
            thread_id=thread_id)

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
        # AUTO-RESUME (operator 2026-07-07: "get things done" — approving a concern MEANS
        # continue; the operator shouldn't have to ALSO say "re-run it"). A cleared, non-aborted
        # effort re-dispatches its own work automatically (single-flight-guarded, so a still-
        # running one is skipped). Abort obviously does not resume.
        if not aborted:
            mgmt = await self.mgmt_channel_id()
            if mgmt:
                self._spawn(self._reengage(
                    [effort_id], mgmt_channel=mgmt,
                    mgmt_thread=self._mgmt_thread_of(effort_id),
                    reply_prefix=f"↩️ Resuming **{effort_id}** where it paused."))

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
        # NL-FIRST repo hygiene — these COMPOSE (the live cleanup prompt asked for both in ONE
        # message): "close PR 3"/"remove all pull requests" (reversible sweep) and branch deletion
        # (IRREVERSIBLE — fires only on explicitly NAMED agent/* branches in a delete-verb
        # sentence; the operator's words are the §3 clearance, like "merge it").
        handled_close = await self._nl_pr_close(message, channel_id, thread_id)
        handled_delete = await self._nl_branch_delete(message, channel_id, thread_id)
        if handled_close or handled_delete:
            return
        # NL-FIRST "run it in the host context" — the remedy the blocker elevation offers for a
        # workspace-insufficiency: re-run a composition effort where the build can actually run.
        if re.search(r"\b(?:run|re-?run|retry|do)\b[^.\n]{0,30}?\b(?:in|with|using)\b"
                     r"[^.\n]{0,20}?\bhost\s+context\b", message, re.I):
            eid = None
            m_eid = re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message)
            if m_eid:
                eid = m_eid.group(0)
            else:
                efforts = sorted(await self.gate.snapshot(open_only=True),
                                 key=lambda e: e.get("updated_at") or "", reverse=True)
                for e in efforts:
                    if not e["id"].startswith("__") and await self._vendored_host(
                            e.get("project") or ""):
                        eid = e["id"]
                        break
            if eid:
                await self.chat.post(channel_id, f"▶ Re-running `{eid}` in the host context now.",
                                     thread_id=thread_id)
                self._spawn(self._run_in_host_context(eid))
            else:
                await self.chat.post(
                    channel_id, "I couldn't find a composition effort to run in the host context — "
                    "name it (`effort-…`) and I'll run it there.", thread_id=thread_id)
            return
        # NL-FIRST "proceed" — the operator's explicit go-ahead IS the §3 clearance to release a
        # dry-run execution hold (P4.0) and dispatch (only claims the turn when something is
        # actually held, so it never steals a normal intake).
        if await self._nl_proceed_execution(message, channel_id, thread_id):
            return
        # NL-FIRST build-log access (operator 2026-07-07: "the PM should have access to logs") —
        # every build the org ran itself is kept as evidence; hand over the same log the PM used.
        if await self._nl_show_log(message, channel_id, thread_id):
            return
        # NL-FIRST burn-down resume: "keep going" after a stalled burn-down buys more rounds.
        if await self._nl_burndown_resume(message, channel_id, thread_id):
            return
        # NL-FIRST knowledge sync (RS.2): "sync <project> docs/knowledge/sources" — a governed
        # operator-plane trigger, resolved deterministically like merge/PR above.
        msync = re.match(
            r"^\s*(?:please\s+)?(?:re-?)?sync\s+(?:the\s+)?([A-Za-z0-9][\w-]*)(?:'s)?\s+"
            r"(?:docs|documentation|knowledge|sources)\s*[.!]*$",
            message.strip(), re.IGNORECASE)
        if msync:
            p = await self.projects.resolve(msync.group(1))
            if p:
                await self.chat.post(
                    channel_id, f"🧠 syncing `{p['slug']}`'s docs into Open Brain sources — "
                    f"results follow.", thread_id=thread_id)
                self._spawn(self._repo_sync(p["slug"], announce_channel=channel_id,
                                            announce_thread=thread_id))
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
        # Classification sees a COMPACT view of pasted walls (deduped error lines, capped) — the
        # small model junk-misfires on degenerate repetition. The FULL message stays the goal.
        user_prompt = (
            f"CONVERSATION SO FAR (most recent last):\n{history}\n\n"
            f"LATEST OPERATOR MESSAGE:\n{_compact_paste(message)}\n\nCURRENT EFFORTS: {ctx}\n"
            f"AWAITING CLARIFICATION: {pending_ctx}\nKNOWN PROJECTS: {projects_ctx}\n"
            f"RECENT WORKER ACTIVITY (newest last):\n{activity_ctx}"
        )
        try:
            intent = await self.models.structured("po", _PO_NL_SYS, user_prompt, OperatorIntent)
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

        # JUNK-INTENT GUARD (live miss: the model returned kind=chitchat + reply="…" for a clear
        # scoped work request → the bridge posted the ellipsis and SILENTLY DROPPED the work — the
        # worst outcome). Contentless reply ⇒ the model misfired; repair deterministically:
        def _junk(r: str) -> bool:
            return len(re.sub(r"[\W_]", "", r)) < 3          # "…", "...", "-", "" …

        def _actionless(it) -> bool:
            """True when this intent, AS FILLED, will run no handler — it acts only via its reply
            (or its branch requires fields the model didn't provide, so it falls through). This is
            what made the SECOND live '…': kind=request with effort_name=None skips the request
            branch entirely."""
            return (
                it.kind in ("chitchat", "question")
                or (it.kind == "request" and not it.effort_name)
                or (it.kind in ("clarification", "steering") and not it.effort_id)
                or (it.kind == "decision" and not (it.effort_id and it.decision))
                or (it.kind == "reassign" and not (it.effort_id and it.project))
            )

        if _junk(reply):
            proj, how = await self._project_scoped_in(message)
            # The documented `in <project>,` prefix is a work idiom on its own; a project named
            # ANYWHERE else needs a work cue (verb / error paste) so a mere mention — "how's murder
            # doing?" — can't be forced into a phantom effort (live 2026-07-05: "when building
            # `murder` from project `MonoGame-Engine` … Fix the entire list" + junk reply ×2 fell
            # into the rephrase fallback and the fix request was DROPPED).
            forceable = proj is not None and (
                how == "prefix"
                or _WORK_CUE_RE.search(message)
                or _ERROR_REPORT_RE.search(message)
            )
            if _actionless(intent) and forceable:
                # a scoped work request the model fumbled — force it (don't drop work).
                intent.kind = "request"
                intent.project = proj["slug"]
                if not intent.effort_name:
                    if how == "prefix":
                        task_part = re.sub(r"^\s*(?:@\S+\s+)?in\s+\S+\s*[,:]\s*", "", message)
                        intent.effort_name = slugify(task_part[:40]) or "task"
                    elif _ERROR_REPORT_RE.search(message):
                        intent.effort_name = f"fix-{proj['slug']}-errors"
                    else:
                        first_line = message.strip().splitlines()[0]
                        intent.effort_name = slugify(first_line[:40]) or "task"
                reply = f"On it — running that as work on `{proj['slug']}`."
            elif _actionless(intent):
                # One retry (the model is stochastic); still junk → an HONEST ask, never "…".
                try:
                    intent = await self.models.structured(
                        "po", _PO_NL_SYS,
                        user_prompt + "\n\nNOTE: your previous attempt returned an empty/unusable "
                        "reply. Classify DECISIVELY and write a substantive reply.",
                        OperatorIntent)
                    reply = (intent.reply or "").strip()
                except Exception:  # noqa: BLE001
                    reply = ""
                if _junk(reply) and _actionless(intent):
                    self._remember(channel_id, thread_id, "operator", message)
                    await self._post_unactionable(channel_id, thread_id, message)
                    return
            else:
                reply = ""   # actionable kind, junk ack → drop the junk; the handler posts its own

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
        # PROJECT CHECK in plain language (operator preference: no slash commands) — the D2
        # build/test gate is registry state, so setting/clearing it is an NL operation. Like the
        # standing intent, set it as a side effect but don't EAT a work request that names a build.
        if getattr(intent, "check_cmd", None) is not None and intent.project and not (
                intent.kind in ("request", "reengage") and (intent.effort_name or intent.effort_id)):
            p = await self.projects.resolve(intent.project)
            if p:
                cmd = self._extract_check_cmd(intent.check_cmd)
                await self.projects.set_check(p["slug"], cmd)
                await self.chat.post(
                    channel_id,
                    (reply + ("\n\n🧪 Every delivery on **`{s}`** now red-gates on `{c}` before a "
                              "merge is offered — a failing check routes straight back to the "
                              "worker.".format(s=p["slug"], c=cmd) if cmd else
                              f"\n\n🧪 Cleared the check on **`{p['slug']}`** — deliveries are no "
                              f"longer machine-verified (I'll say so on each closure).")).strip(),
                    thread_id=thread_id,
                )
                return
        # STANDING INTENT in plain language (anti-drift, ANY project) — a durable architectural
        # invariant, injected into every effort goal + enforced at delivery. Set it as a SIDE
        # EFFECT whenever present, but only STOP here when the message was PURELY about the rule —
        # a WORK request that merely restates the constraint must still dispatch (live 2026-07-07:
        # "in murder, … (never NuGet Murder.FNA); fix …" populated standing_intent and the handler
        # ate the whole request, so no work ran).
        if getattr(intent, "standing_intent", None) is not None and intent.project:
            p = await self.projects.resolve(intent.project)
            if p:
                si = intent.standing_intent.strip()
                await self.projects.set_standing_intent(p["slug"], si)
                is_work = intent.kind in ("request", "reengage") and (
                    intent.effort_name or intent.effort_id)
                if not is_work:
                    if si:
                        forb = self._forbidden_terms(si)
                        forb_note = (" Forbidden term(s) I'll reject in any diff: "
                                     + ", ".join(f"`{t}`" for t in forb) + "." if forb else
                                     " (no explicitly-forbidden `terms` — backtick any you want "
                                     "me to block at the diff level.)")
                        body = (reply + f"\n\n🧭 Standing intent set for **`{p['slug']}`**: _{si}_"
                                f"\n\nEvery effort on it now carries this rule, and I reject any "
                                f"delivery that violates it.{forb_note}")
                    else:
                        body = reply + f"\n\n🧭 Cleared the standing intent on **`{p['slug']}`**."
                    await self.chat.post(channel_id, body.strip(), thread_id=thread_id)
                    return
                # a work request that also stated the rule: rule is now recorded; fall through so
                # the work actually dispatches (its goal will carry the rule via injection).
        # REMOVE a wrong/stale upstream from an EXISTING project — the registry is bridge-owned
        # state, so correcting it is an NL operation (D0.f), never operator SQL.
        if getattr(intent, "remove_upstream", False) and intent.project and not intent.upstream_url:
            p = await self.projects.resolve(intent.project)
            if p:
                await self.projects.set_upstream(p["slug"], None)
                await self.chat.post(
                    channel_id,
                    (reply + f"\n\n✅ Cleared the upstream on **`{p['slug']}`** — it's no longer "
                     f"treated as a fork, so workers stop baking an `upstream` remote on their next "
                     f"focus. Re-add any time: _\"set <url> as upstream on {p['slug']}\"_.").strip(),
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
                # CONVERGENCE (anti-sprawl): a re-report of the SAME problem must reuse the
                # existing open effort — its branch + PR — not mint a new slug/branch/PR every
                # time (live 2026-07-07: 9+ branches/PRs from repeated build-error prompts). Match
                # by shared error/goal signature on the same project; reuse when found.
                conv = await self._find_convergent_effort(project, message)
                if conv:
                    eid, chan, root = conv
                    await self.chat.post(
                        channel_id,
                        (reply + f"\n\n♻️ _Continuing the existing effort `{eid}` for this — same "
                         f"branch + PR, so we converge instead of opening yet another. (Say "
                         f"“new effort” if you truly want a separate one.)_").strip(),
                        thread_id=thread_id)
                else:
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
            # An effort id in the operator's own words is DETERMINISTIC — never depend on the
            # small model to copy it into the intent (live 2026-07-06: "re-run effort-fix-…"
            # classified as reengage but effort_id came back empty → "Nothing to re-engage").
            if not intent.effort_id:
                m_eid = re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message)
                if m_eid:
                    intent.effort_id = m_eid.group(0)
            # A re-run that NAMES a closed effort reopens it — "re-run effort-X" is a legitimate
            # ask for lifecycle=done/aborted work (live 2026-07-06: it answered "Nothing to
            # re-engage" because a prior delivery had closed the effort).
            if intent.effort_id and intent.effort_id not in {e["id"] for e in efforts}:
                async with self.db.session_factory() as s:
                    e_row = await s.get(Effort, intent.effort_id)
                    if e_row is not None:
                        e_row.lifecycle = "open"
                        await s.commit()
                        await self.audit.log("effort_reopened", effort_id=intent.effort_id,
                                             payload={"via": "named-rerun"})
                        efforts = await self.gate.snapshot(open_only=True)
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
            # SINGULAR re-engage resolution (unscoped, several idle candidates). Two grounded
            # narrowings — never a blind fan-out for a one-thing ask:
            #   1. THREAD context: "re-run it" typed in an effort's #mgmt conversation means THAT
            #      effort — the undelivered escalation invites exactly this reply (live 2026-07-06:
            #      the bare reply re-fanned the stale backlog instead of the escalated effort).
            #   2. RECENCY: bare "re-run it" / "continue its previous task" outside a mapped
            #      thread means the most recently touched effort.
            if len(targets) > 1 and not scope and not intent.effort_id:
                ctx_eid = self._effort_of_mgmt_thread(thread_id) if thread_id else None
                singular = (_SINGULAR_TASK_RE.search(message)
                            or re.fullmatch(r"(?:please\s+)?re-?run\s+(?:it|that)\s*[.!]*",
                                            message.strip(), re.I))
                if ctx_eid and ctx_eid in targets:
                    targets = [ctx_eid]
                    reply = (reply + f"\n\n_(this conversation is about `{ctx_eid}` — resuming "
                             f"just that one; say “get the workers working” for everything idle.)_"
                             ).strip()
                elif singular:
                    by_recency = {e["id"]: (e.get("updated_at") or "") for e in efforts}
                    latest = max(targets, key=lambda t: by_recency.get(t, ""))
                    targets = [latest]
                    reply = (reply + f"\n\n_(resuming only the most recent effort `{latest}` — "
                             f"say “get the workers working” to re-dispatch everything idle.)_"
                             ).strip()
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
            # Anything refused WHILE frozen resumes now, automatically (the refusal message
            # promised exactly this — the operator never re-asks).
            blocked = sorted(self._kill_blocked)
            self._kill_blocked.clear()
            if blocked:
                reply += (f"\n▶ Re-dispatching the {len(blocked)} effort(s) the freeze blocked: "
                          + ", ".join(f"`{e}`" for e in blocked))
                self._spawn(self._reengage(blocked, mgmt_channel=channel_id,
                                           mgmt_thread=thread_id, reply_prefix=""))
        elif intent.kind == "status":
            if efforts:
                status_map = await self._effort_status_map(efforts)
                reply += "\n\n" + self._render_status(efforts, status_map)
            else:
                reply += "\n\n_No open efforts — tell me what you'd like built._"

        # NEVER post bare punctuation (the live "…" came from this exact default: a junk reply was
        # cleared upstream, every branch fell through, and this tail re-manufactured the ellipsis).
        # An empty reply here means nothing acted — say so honestly instead.
        if not reply:
            await self._post_unactionable(channel_id, thread_id, message)
            return
        await self.chat.post(channel_id, reply, thread_id=thread_id)

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
        # Transparency: PM work beyond coding efforts is visible too — research jobs in flight.
        for a in self._advisories.values():
            mins = int((time.time() - a.get("started", time.time())) // 60)
            body += (f"\n- 🔬 research — **{a.get('state', 'running')}** ({mins} min): "
                     f"_{a.get('question', '')}_")
        running = sum(1 for v in status_map.values() if v == "running")
        idle = sum(1 for v in status_map.values() if v == "idle")
        if running == 0 and idle and not self._advisories:
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
        #    Mark the effort plan-owned so the intake auto-wiring doesn't ALSO bump the gitlink.
        self._composition_managed.add(eid)
        try:
            await self.delegate(eid, chan, root, goal)
        finally:
            self._composition_managed.discard(eid)
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
            "when it lands (a full research pass usually takes **several minutes**; it runs in the "
            "background, so feel free to keep working)._"
        )
        await self.chat.post(channel_id, ack.strip(), thread_id=thread_id)
        self._spawn(self._run_advisory(question, channel_id, thread_id))

    async def _run_advisory(
        self, question: str, channel_id: str, thread_id: str | None
    ) -> None:
        """Background half of `_advise`: run the research job STATE-DRIVEN (the loop reads the job's
        own status and decides — wait / fall back / give up — never an arbitrary time gate), posting
        progress TRANSPARENTLY in-thread as the state changes + a heartbeat, then the grounded answer
        (or a labelled fallback whose wording matches the REAL failure reason). Registered in
        `_advisories` so /status shows research-in-flight like any other work."""
        akey = f"research-{int(time.time())}"
        started = time.time()
        self._advisories[akey] = {"question": question[:120], "state": "submitting",
                                  "started": started}

        async def _progress(state: dict) -> None:
            # advise() already gates these to MEANINGFUL milestones (status/phase transitions +
            # a 10-min heartbeat). Here: always keep /status fresh, but only POST after the first
            # 2 min (the ack just said it's running — echoing that seconds later is noise).
            status = state.get("status") or "running"
            qp = state.get("queue_position")
            phase = state.get("phase") or ""
            self._advisories[akey]["state"] = (
                status + (f" · {phase}" if phase else "") + (f" (queue #{qp})" if qp else ""))
            if time.time() - started < 120.0:
                return
            mins = int(state.get("waited_s", 0)) // 60
            await self.chat.post(
                channel_id,
                f"🔎 _research update: **{status}**"
                + (f" — phase **{phase}**" if phase else "")
                + (f", queue position {qp}" if qp else "")
                + (f" ({mins} min in)" if mins else "")
                + "; I'll post the answer here when it lands._",
                thread_id=thread_id,
            )

        ans = None
        try:
            ans = await self.grounding.advise(question, on_progress=_progress)
        except TypeError:   # a Grounding impl without on_progress (fakes) — still state-driven inside
            ans = await self.grounding.advise(question)
        except Exception as exc:  # noqa: BLE001 - degrade to the local fallback below
            log.warning("advisory research raised: %s", exc)
        finally:
            self._advisories.pop(akey, None)
        if ans is not None and ans.grounded and (ans.answer or "").strip():
            body = ans.answer.strip()
            if ans.sources:
                srcs = "\n".join(f"- {s}" for s in ans.sources[:12])
                body += f"\n\n**Sources**\n{srcs}"
            await self.chat.post(channel_id, body, thread_id=thread_id)
            return
        # No grounded answer → an honest, clearly-labelled ungrounded local answer, with the REAL
        # reason (state-aware): a failed job ≠ an unreachable engine ≠ a wedged job.
        reason = getattr(ans, "reason", "") if ans is not None else "unreachable"
        why = {
            "failed": "the research job **failed on the engine**",
            "empty": "research finished but returned **no synthesis**",
            "backstop": "the research job has been running for **hours — likely wedged** "
                        "(check `openbrain-research`)",
        }.get(reason, "I **couldn't reach the research engine**")
        # De-bias the short check (research-engine-for-OB GROUNDING-MODEL discipline, operator-
        # specified): in shallow context the operator's framing dominates and the model favors the
        # implied goal. An ungrounded fallback can't claim-check, so it approximates the objective
        # stance by answering a NEUTRALIZED form of the question. Sanity-guarded (junk/blown-up
        # rewrite → original); TRANSPARENT (the neutral form is shown with the answer).
        neutral = ""
        try:
            cand = (await self.models.complete("po", _NEUTRALIZE_SYS, question)).strip().strip('"')
            if 10 <= len(cand) <= max(3 * len(question), 200) and "\n" not in cand:
                neutral = cand
        except Exception as exc:  # noqa: BLE001 — de-biasing is best-effort, never blocks
            log.debug("question neutralization failed: %s", exc)
        local = ""
        try:
            local = (await self.models.complete(
                "po", _ADVISOR_FALLBACK_SYS, neutral or question)).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("advisory local fallback failed: %s", exc)
        if local:
            neutral_note = (f"\n\n_(To reduce framing bias, I answered the neutralized form: "
                            f"“{neutral}”)_" if neutral and neutral != question.strip() else "")
            await self.chat.post(
                channel_id,
                f"⚠️ _{why}, so here's my best take from general knowledge — **unverified, no "
                f"citations**:_\n\n{local}{neutral_note}",
                thread_id=thread_id,
            )
        else:
            await self.chat.post(
                channel_id,
                f"⚠️ {why} — and my local fallback failed too. Please try again shortly.",
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

    async def _vendored_host(self, slug: str) -> tuple[str, str, str, str] | None:
        """(host_slug, path, host_repo_url, siblings_desc) when `slug`'s repo is a git submodule
        inside another registered project — read from the host's ACTUAL `.gitmodules` (generic,
        nothing hardcoded). None when standalone/unreadable (fail-open). Cached ~10 min."""
        if self.github is None or not self.s.github_app_enabled:
            return None
        cached = self._vendor_host_cache.get(slug)
        if cached and (time.time() - cached[0]) < 600:
            return cached[1]
        found: tuple | None = None
        try:
            me = await self.projects.get(slug)
            if me:
                my_repo = self._norm_repo(me["repo_url"])
                for host in await self.projects.list():
                    if host["slug"] == slug:
                        continue
                    st = await read_repo_state(
                        self.github, host["repo_url"],
                        api_base=self.s.github_api_base, transport=self._gh_transport)
                    if not st.readable:
                        continue
                    pairs = list(zip(st.submodule_paths, st.submodule_urls))
                    mine = next((p for p, u in pairs if self._norm_repo(u) == my_repo), None)
                    if not mine:
                        continue
                    siblings = ", ".join(
                        f"`{p}` (`{self._norm_repo(u)}`)" for p, u in pairs if p != mine) or "none"
                    found = (host["slug"], mine, host["repo_url"], siblings)
                    break
        except Exception as exc:  # noqa: BLE001 — a lookup hiccup must never block dispatch
            log.debug("vendored-host lookup for %s failed: %s", slug, exc)
        self._vendor_host_cache[slug] = (time.time(), found)
        return found

    @staticmethod
    def _forbidden_terms(standing_intent: str) -> list[str]:
        """Terms the operator marked FORBIDDEN in the standing intent: a `backticked` token that
        follows a negation (never / no / not / don't / avoid / forbidden / instead of) within a
        short window. These are checked against every delivery's added lines. Generic — the org
        blocks exactly what the operator said to block, in ANY project's own vocabulary."""
        out: list[str] = []
        for m in re.finditer(r"`([^`]{2,60})`", standing_intent or ""):
            prefix = standing_intent[max(0, m.start() - 45):m.start()].lower()
            if re.search(r"\b(never|no|not|avoid|forbid|forbidden|instead of|rather than)\b"
                         r"|n['’]t\b", prefix):
                term = m.group(1).strip()
                if term and term not in out:
                    out.append(term)
        return out

    async def _standing_intent_context(self, slug: str) -> str:
        """The project's standing intent as a goal preamble (injected into EVERY effort on the
        project) — the durable architectural rule the worker must honor. '' when none set."""
        p = await self.projects.get(slug)
        si = ((p or {}).get("standing_intent") or "").strip()
        if not si:
            return ""
        forb = self._forbidden_terms(si)
        forb_line = (f" I will REJECT any delivery whose diff introduces: "
                     + ", ".join(f"`{t}`" for t in forb) + "." if forb else "")
        return (f"\n\nSTANDING INTENT (a durable architectural rule for `{slug}` — NON-NEGOTIABLE, "
                f"overrides any expedient shortcut): {si}\nDo NOT trade this away for a green "
                f"build — if honoring it makes the task hard or impossible, SAY SO and stop; do "
                f"NOT revert or work around the architecture to force a pass.{forb_line}")

    async def _composition_context(self, slug: str) -> str:
        """The COMPOSITION CONTEXT block the planner path injects, for DIRECT-intake dispatches —
        a worker given a standalone clone sees cross-submodule references as plainly broken and
        'fixes' them by reverting the composition (live 2026-07-05: a standalone murder worker
        reverted the vendored-MonoGame wiring back to NuGet to green the build)."""
        host = await self._vendored_host(slug)
        if not host:
            return ""
        host_slug, mine, _url, siblings = host
        return (
            f"\n\nCOMPOSITION CONTEXT: `{slug}` is used as a git submodule inside "
            f"`{host_slug}` at `{mine}` — sibling submodules there: {siblings}. "
            f"Cross-submodule project/path references are written for THAT vendored "
            f"layout and are EXPECTED to look broken in a standalone checkout — do "
            f"NOT 'fix' them by reverting to package references or rewriting paths "
            f"for standalone use; keep the vendored wiring intact and make your "
            f"change compatible with it. Do NOT assume `{slug}` is standalone."
        )

    @staticmethod
    def _extract_check_cmd(rest: str) -> str:
        """The check COMMAND from operator text: the quoted/backticked span when present, else
        the first line — never a pasted wall that happens to follow it."""
        rest = (rest or "").strip()
        if rest[:1] in ("\"", "'", "`"):
            end = rest.find(rest[0], 1)
            if end > 0:
                return rest[1:end].strip()[:250]
        return (rest.splitlines() or [""])[0].strip().strip('"').strip("'").strip("`")[:250]

    async def _derive_check_cmd(self, slug: str, request: str, channel_id: str,
                                thread_id: str | None) -> None:
        """If the operator's error report contains an explicit REPRO COMMAND (a pasted shell line
        like `PS P:\\…> dotnet build vendor\\murder\\Murder.sln`), adopt it as the project's D2
        check when none is set — announced transparently, overridable any time
        (`/project check <slug> "<cmd>"`). Generic across toolchains; never overwrites an
        operator-set check; stricter-only (it can only ADD a red-gate, never remove one)."""
        try:
            p = await self.projects.get(slug)
        except Exception:  # noqa: BLE001
            return
        if not p or (p.get("check_cmd") or "").strip():
            return
        m = re.search(
            r"(?:^|\n)\s*(?:PS [^>\n]*>\s*|\$\s*|>\s*)?"
            r"((?:dotnet|msbuild)\s+(?:build|test|msbuild)?[^\n]*"
            r"|npm\s+(?:test|run\s+\S+)[^\n]*|(?:yarn|pnpm)\s+(?:test|build)[^\n]*"
            r"|cargo\s+(?:build|test|check)[^\n]*|go\s+(?:build|test)[^\n]*"
            r"|make\s+\S+[^\n]*|pytest[^\n]*)",
            request, re.IGNORECASE)
        if not m:
            return
        cmd = m.group(1).strip().rstrip(".").replace("\\", "/")
        if len(cmd) < 8 or len(cmd) > 200:
            return
        await self.projects.set_check(slug, cmd)
        await self.chat.post(
            channel_id,
            f"🧪 Adopted your repro as `{slug}`'s check command: `{cmd}` — every delivery now "
            f"red-gates on it before a merge is offered (D2). Change or clear it any time: "
            f"`/project check {slug} \"<cmd>\"`.",
            thread_id=thread_id,
        )

    async def _session_for(self, effort_id: str) -> str:
        """The worker session for an effort — ROTATED to a fresh session after each undelivered
        escalation (live 2026-07-06: session-id = effort-id accumulated FIVE runs of history in
        one 775KB pi session; every retry loaded the rot, narrated one orientation line and quit —
        no goal wording can repair a degenerate session). Generation = the number of prior
        `effort_undelivered` escalations, derived from the audit log (no new state; deterministic
        across restarts). Gen 0 keeps the plain effort id, so healthy efforts keep workspace +
        session affinity exactly as before."""
        try:
            async with self.db.session_factory() as s:
                n = int((await s.execute(
                    select(func.count()).select_from(Event).where(
                        Event.kind.in_(("effort_undelivered", "delivery_stale_head",
                                        "delivery_empty_diff")),
                        Event.effort_id == effort_id)
                )).scalar_one())
        except Exception as exc:  # noqa: BLE001 — affinity fallback, never a dispatch blocker
            log.debug("session generation lookup failed for %s: %s", effort_id, exc)
            n = 0
        return effort_id if n == 0 else f"{effort_id}~r{n}"

    async def _find_convergent_effort(self, project: str, request: str):
        """The OPEN effort on `project` this request is a continuation of — matched by shared
        error/goal signature lines — so a re-report reuses ONE branch + PR instead of spawning a
        new one each time (anti-sprawl; live 2026-07-07: 9+ branches/PRs from repeated prompts).
        Returns (effort_id, channel_id, root_post_id) or None. Never converges an unrelated
        request (requires a real signature-line overlap); explicit 'new effort' bypasses upstream."""
        if re.search(r"\bnew\s+effort\b|\bseparate\s+effort\b|\bfresh\s+effort\b", request, re.I):
            return None
        sig = {ln.strip().lower() for ln in request.splitlines()
               if len(ln.strip()) >= 30 and ("'" in ln or "\\" in ln or "/" in ln
                                             or _ERROR_REPORT_RE.search(ln))}
        if not sig:
            return None
        efforts = sorted(await self.gate.snapshot(open_only=True),
                         key=lambda e: e.get("updated_at") or "", reverse=True)
        for e in efforts:
            if (e.get("project") or "") != project or e["id"].startswith("__"):
                continue
            try:
                _, goal, _ = await self.charters.current_goal(e["id"])
            except Exception:  # noqa: BLE001
                goal = ""
            g = (goal or "").lower()
            if g and sum(1 for s in sig if s in g) >= max(1, len(sig) // 2):
                loc = await self.router.effort_thread(e["id"])
                if loc:
                    return e["id"], loc[0], loc[1]
        return None

    async def _attempt_history(self, effort_id: str, request: str) -> str:
        """PRIOR ATTEMPTS block for a RE-REPORTED error: recent efforts whose goal contains the
        same error line(s), each with its delivered branch's VERIFIED outcome. A re-report means
        nothing delivered so far resolved it — the next worker must read what exists and build on
        it (or consciously diverge), never re-derive blind (live 2026-07-05: attempt 1 stranded
        the right fix in its container, attempt 2 reverted the architecture, attempt 3 would have
        known about neither). Generic: matches on the operator's own pasted error text."""
        def _sig(ln: str) -> bool:
            # a "signature" line: long enough to be distinctive AND shaped like tool output
            # (compiler/build lines carry quotes, paths or error keywords — plain prose doesn't)
            s = ln.strip()
            return (len(s) >= 30
                    and bool(_ERROR_REPORT_RE.search(s) or "'" in s or "\\" in s or "/" in s))

        lines = {ln.strip().lower() for ln in request.splitlines() if _sig(ln)}
        if not lines:
            return ""
        matches: list[dict] = []
        efforts = sorted(await self.gate.snapshot(open_only=False),
                         key=lambda e: e.get("updated_at") or "", reverse=True)
        for e in efforts:
            if e["id"] == effort_id or e["id"].startswith("__"):
                continue
            try:
                _, goal, _ = await self.charters.current_goal(e["id"])
            except Exception:  # noqa: BLE001
                goal = ""
            g = (goal or "").lower()
            if g and any(ln in g for ln in lines):
                matches.append(e)
            if len(matches) >= 3:
                break
        if not matches:
            return ""
        out = ["\n\nPRIOR ATTEMPTS AT THIS SAME ERROR (the operator reports it AGAIN — nothing "
               "delivered so far resolved it):"]
        for e in matches:
            branch = self._effort_branch(e["id"])
            repo = await self._effort_repo(e["id"]) or ""
            fact = "no verifiable delivery"
            if repo and self.github is not None and self.s.github_app_enabled:
                try:
                    d = await read_branch_delivery(
                        self.github, repo, branch,
                        api_base=self.s.github_api_base, transport=self._gh_transport)
                    if d.verifiable and d.exists:
                        nf = d.files_changed if d.files_changed >= 0 else "?"
                        fact = (f"branch `{branch}` on `{self._norm_repo(repo)}` — "
                                f"{d.ahead} commit(s), {nf} file(s) changed, UNMERGED")
                    elif d.verifiable:
                        fact = f"branch `{branch}` never reached `{self._norm_repo(repo)}`"
                except Exception:  # noqa: BLE001
                    pass
            out.append(f"- `{e['id']}` (project `{e.get('project') or '?'}`): {fact}")
        out.append(
            "First fetch and READ those branches, then — IN THIS SAME TURN — implement, verify "
            "and publish the fix. Orientation is not completion: do NOT end your turn after "
            "reading the prior work (a previous attempt died exactly that way). Do NOT re-deliver "
            "an approach that already sits there unmerged — it was not accepted. If a prior "
            "branch contains the right fix that never got merged or wired through, BUILD ON IT "
            "and say so in your report.")
        return "\n".join(out)

    async def _wire_vendored_delivery(self, effort_id: str, delivery: BranchDelivery) -> str:
        """The WIRING half for an INTAKE-born delivery on a vendored project (planner-path parity,
        §11d): a code fix on the vendored repo doesn't reach the host's build until the host's
        gitlink bumps — so bump it to the verified commit on the same-named branch (Git Data API,
        no checkout) and open the paired PR. Returns closure text; '' when the project isn't
        vendored. Efforts owned by a composition PLAN are excluded by the caller (no double-bump)."""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        if not (host and delivery.head_sha and self.github is not None):
            return ""
        host_slug, path, host_url, _sib = host
        branch = self._effort_branch(effort_id)
        res = await bump_submodule(
            self.github, host_url, path, delivery.head_sha, branch=branch,
            api_base=self.s.github_api_base, transport=self._gh_transport)
        await self.audit.log("composition_wired", effort_id=effort_id,
                             payload={"engine": host_slug, "path": path, "ok": res.ok,
                                      "commit": delivery.head_sha, "branch": branch,
                                      "source": "intake"})
        if not res.ok:
            return (f"\n⚠️ _The code landed, but wiring it into `{host_slug}` failed: "
                    f"{res.summary} — `{host_slug}` still points at the old `{path}` commit._")
        # PR STAGING (operator 2026-07-07: "a PR was still created even though there's a lot
        # more work to be done"): the build check runs FIRST; a wiring PR only exists on green.
        check_note = await self._composition_check(effort_id, host_slug, host_url, branch)
        if effort_id in self._comp_check_failed:
            return (f"\n🔗 gitlink bumped to `{delivery.head_sha[:10]}` on `{host_slug}`/"
                    f"`{branch}`, but the host build is RED — **no wiring PR opened** (PRs wait "
                    f"for green).{check_note}")
        wiring_pr = await self._open_delivery_pr(
            effort_id, host_url, branch, merge_id=f"merge-{effort_id}-engine",
            body_extra=f"WIRING half of a composition: bumps `{path}` to `{delivery.head_sha[:10]}` "
                       f"(see the paired code PR on the vendored repo).\n")
        return (f"\n🔗 **Wiring half:** `{host_slug}` vendors this at `{path}` — gitlink bumped to "
                f"`{delivery.head_sha[:10]}` on `{branch}`"
                + (f", paired PR: {wiring_pr}" if wiring_pr else "")
                + "\n_Merge BOTH halves (code + wiring) for the fix to reach the host build._"
                + check_note)

    async def _no_changes_acceptable(self, effort_id: str, output: str) -> bool:
        """Whether a NO CHANGES claim is legitimate. For a genuine read-only/investigation goal:
        always (the answer is the deliverable). For a FIX/BUILD goal (REQUIRED VERIFICATION, or the
        project/host has a check_cmd): only when the report carries explicit BUILD-PASS evidence —
        a fix request cannot be closed 'nothing to change' on the worker's word alone (live
        2026-07-07: a hallucinated no-op skipped the whole check stack). Generic across toolchains."""
        try:
            _, goal, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal = ""
        goal = goal or ""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        check_owner = host[0] if host else proj
        cp = await self.projects.get(check_owner) if check_owner else None
        demands_proof = "REQUIRED VERIFICATION" in goal or bool((cp or {}).get("check_cmd"))
        if not demands_proof:
            return True   # a real read-only task — NO CHANGES is the legitimate outcome
        # a fix/build request: require concrete build-pass evidence in the worker's report
        return bool(re.search(
            r"CHECK:\s*PASS|build succeeded|BUILD SUCCEEDED|0\s+error\b|0\s+Error\(s\)|"
            r"\bexit(?:ed)?\s+0\b|\bpassed\b", output, re.I))

    @staticmethod
    def _build_segment(check_cmd: str) -> str:
        """The BUILD/TEST portion of a check command — drop leading `git …` setup segments
        (submodule/fetch/checkout/sync/pull) that the privileged recursive focus already did (and
        that the worker's proxied git can't run anyway). E.g. `git submodule update --init
        --recursive && dotnet build X.sln` → `dotnet build X.sln`. Keeps everything from the first
        non-git segment on."""
        segs = [s.strip() for s in re.split(r"&&|;", check_cmd) if s.strip()]
        kept = [s for i, s in enumerate(segs)
                if not (s.lower().startswith("git ") and all(
                    p.lower().startswith("git ") for p in segs[:i + 1]))]
        return " && ".join(kept) if kept else check_cmd

    async def _composition_check(self, effort_id: str, host_slug: str, host_url: str,
                                 branch: str) -> str:
        """D2 for COMPOSITIONS: a vendored project's code cannot compile in its standalone clone
        (the sibling submodules don't exist there), so 'verification' without the host build is
        inspection, not compilation (live 2026-07-06, iteration 7: a signature fix that never
        compiled shipped ambiguity errors only the operator's own build caught). Run the HOST's
        check_cmd on the host's WIRING branch — but the WORKSPACE PREP (checkout + recursive
        submodule init) rides the PRIVILEGED focus (a recursive clone of `{host}#{branch}`), NOT
        the worker: the git-proxy hard-denies `submodule`, so a worker-run `git submodule update`
        can never populate the nested tree (live 2026-07-07: the build failed only because bang/gum
        couldn't init — an environmental failure the org kept reading as a code failure). So we
        strip the git-setup segments from check_cmd and run ONLY the build. Red ⇒ blocks the merge
        invite and the effort stays open."""
        # ORG-VERIFIED short-circuit: the org already ran this build itself (burn-down green /
        # a verified already-in-place delivery) — don't pay for a duplicate check of the same head.
        if self._org_verified.get(effort_id):
            return (f"\n🧪 **Org-verified green** — I ran this build myself on the delivered head "
                    f"(`{self._org_verified[effort_id][:10]}`; log on file). No duplicate check.")
        p = await self.projects.get(host_slug)
        check_cmd = ((p or {}).get("check_cmd") or "").strip()
        if not check_cmd:
            return (f"\n🧪 _No machine check on `{host_slug}` — this wiring branch is UNVERIFIED "
                    f"by any build (set one: `/project check {host_slug} \"<cmd>\"` and I'll run "
                    f"it on every wiring branch before inviting a merge)._")
        loc = await self.router.effort_thread(effort_id)
        if not loc:
            return ""
        channel_id, root = loc
        # Strip the workspace-PREP segments (git submodule / fetch / checkout / sync) — the
        # privileged recursive focus already did all of that; keep the actual build/test.
        build_only = self._build_segment(check_cmd)
        # Focus on the wiring BRANCH with a full recursive submodule clone (privileged path).
        focus_repo = f"{host_url}#{branch}"
        # DETERMINISTIC FIRST (2026-07-08): the machine runs the build and reads the real exit
        # code + log — no model in the verification loop. LLM verifier only as fallback (an old
        # daemon image without `/check`).
        host_token = await self._project_token_for_slug(host_slug)
        try:
            exit_code, dout, timed_out = await self.router.exec_check(
                effort_id, command=f"cd /workspace && {build_only}",
                session_id=f"{effort_id}~chk", repo=focus_repo, repo_token=host_token,
                recurse_submodules=True, timeout=900,
            )
            if not timed_out and exit_code == 0:
                await self.audit.log("org_build_check", effort_id=effort_id,
                                     payload={"verdict": "pass", "errors": 0, "owner": host_slug,
                                              "mode": "exec", "cmd": check_cmd[:200],
                                              "log": dout[:6000]})
                return (f"\n🧪 **Composition check passed** on `{host_slug}` (`{check_cmd}`, "
                        f"org-run, exit 0) — the wiring branch builds.")
            if not timed_out and exit_code is not None:
                n = _error_count(dout)
                tail = "\n".join(_error_lines(dout)[:12]) or dout[-600:]
                self._comp_check_failed.add(effort_id)
                await self.audit.log("org_build_check", effort_id=effort_id,
                                     payload={"verdict": "fail", "errors": n, "owner": host_slug,
                                              "mode": "exec", "cmd": check_cmd[:200],
                                              "log": dout[:6000]})
                self._queue_burndown(effort_id, dout)
                return (f"\n❌ **Composition check FAILED** on `{host_slug}` — "
                        f"{_error_brief(dout)}:\n```\n{tail[:600]}\n```\n"
                        f"_Burn-down engaged: I'll drive fix rounds autonomously (re-building "
                        f"after each) and hold every PR/merge invite until it's green. No "
                        f"action needed._")
            # timed out / no exit code → fall through to the LLM verifier below
            log.info("deterministic composition check inconclusive for %s (timed_out=%s)",
                     effort_id, timed_out)
        except Exception as exc:  # noqa: BLE001 — old daemon without /check, or a focus failure
            log.info("deterministic composition check unavailable for %s (%s) — LLM fallback",
                     effort_id, exc)
        instruction = (
            f"You are a BUILD VERIFIER in a FRESH workspace, already checked out on `{branch}` "
            f"with all submodules present. Your ONLY job is to RUN one build command and report "
            f"its real result — you MUST execute it in the terminal; do NOT answer without "
            f"running it, and change NOTHING (no fixes, no git writes). Run exactly:\n"
            f"  cd /workspace && {build_only}\n"
            f"Then, based on the ACTUAL exit status: if it exited 0 reply exactly `CHECK: PASS`; "
            f"if it failed reply `CHECK: FAIL`, then `ERRORS: <total error count>`, then EVERY "
            f"DISTINCT error line (deduplicated, keep file paths, up to 120 lines), then the "
            f"final summary line; if you truly cannot run it reply `CHECK: BLOCKED` + why. "
            f"Reporting the real failing output is a SUCCESSFUL result — never fake a pass or "
            f"stay silent."
        )
        self._verify_seq += 1
        try:
            repo_token = await self._project_token(effort_id)
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=f"{effort_id}~vfy{self._verify_seq}", instruction=instruction,
                repo=focus_repo, repo_token=repo_token, recurse_submodules=True,
            )
        except Exception as exc:  # noqa: BLE001 — an unrunnable check is reported, never fatal
            return (f"\n🧪 _Composition check couldn't run ({str(exc)[:120]}) — verify the build "
                    f"yourself before merging._")
        out = (result.output or "") if result is not None else ""
        if "CHECK: PASS" in out:
            return (f"\n🧪 **Composition check passed** on `{host_slug}` (`{check_cmd}`, "
                    f"worker-reported) — the wiring branch builds.")
        if "CHECK: FAIL" in out:
            tail = out.split("CHECK: FAIL", 1)[1].strip()[:600]
            self._comp_check_failed.add(effort_id)
            await self.audit.log("org_build_check", effort_id=effort_id,
                                 payload={"verdict": "fail", "errors": _error_count(out),
                                          "owner": host_slug, "cmd": check_cmd[:200],
                                          "log": out[:6000]})
            # A red build with a work list is not a dead end — burn it down (progress-based,
            # autonomous), don't stop at a fixed retry count (operator 2026-07-07).
            self._queue_burndown(effort_id, out)
            return (f"\n❌ **Composition check FAILED** on `{host_slug}` — "
                    f"{_error_brief(out)}:\n```\n{tail}\n```\n"
                    f"_Burn-down engaged: I'll drive fix rounds autonomously (re-building after "
                    f"each) and hold every PR/merge invite until it's green. No action needed._")
        return (f"\n🧪 _Composition check returned no verdict — verify the build yourself before "
                f"merging._")

    async def _intake_or_dispatch(
        self, effort_id: str, proj_channel: str, root: str, request: str,
        *, reply_prefix: str, mgmt_channel: str, mgmt_thread: str | None = None,
    ) -> None:
        """Stage 2→4 for a request: run the readiness gate (P3.8), auto-classify blast radius →
        dry-run risk (P4.0), then either HOLD for operator clarification (F5 — don't guess) or
        dispatch a worker. Owns its own #mgmt reply so the caller just returns."""
        # Composition awareness on DIRECT intake (the planner path already injects this): a
        # vendored project's goal carries the layout facts, so a standalone-cloned worker can't
        # mistake intentional cross-submodule wiring for breakage.
        proj_slug = await self._effort_project(effort_id)
        # STANDING INTENT (anti-drift): the project's durable architectural rule rides on EVERY
        # effort, so no worker can quietly revert the architecture to manufacture a pass.
        if proj_slug and "STANDING INTENT" not in request:
            request += await self._standing_intent_context(proj_slug)
        if proj_slug and "COMPOSITION CONTEXT" not in request:
            request += await self._composition_context(proj_slug)
        # MACHINE-CHECK forewarning: when the project (or the host that vendors it) has a check
        # command, the worker knows its delivery gets BUILT before any merge — so it runs the
        # equivalent itself instead of shipping code it never compiled (live 2026-07-06,
        # iteration 7: an uncompiled signature fix introduced ambiguity errors).
        if proj_slug and "MACHINE CHECK" not in request:
            host = await self._vendored_host(proj_slug)
            check_owner = host[0] if host else proj_slug
            cp = await self.projects.get(check_owner)
            ccmd = ((cp or {}).get("check_cmd") or "").strip()
            if ccmd:
                request += (
                    f"\n\nMACHINE CHECK: this delivery will be verified with `{ccmd}` on "
                    f"`{check_owner}` before any merge is offered — run the equivalent in your "
                    f"workspace first; a red check routes straight back to you."
                )
        # ERROR-REPORT convergence (live 2026-07-05: the same build error was re-reported after
        # every attempt): the goal must (a) require the worker to reproduce → fix → RE-VERIFY the
        # reported failure, and (b) carry what prior attempts already delivered, so the next
        # worker builds on them instead of re-deriving (or repeating a rejected approach).
        if _ERROR_REPORT_RE.search(request) and request.count("\n") >= 2:
            if "REQUIRED VERIFICATION" not in request:
                request += _VERIFY_CLAUSE
            if "PRIOR ATTEMPTS" not in request:
                request += await self._attempt_history(effort_id, request)
            # The operator's own pasted repro becomes the project's D2 check (stricter-only
            # autonomy: ADDING a merge gate is always safety-positive) — deliveries then red-gate
            # on the REAL build instead of the operator being the only build executor.
            if proj_slug:
                await self._derive_check_cmd(proj_slug, request, proj_channel, root)
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
            # The gate is the same small model — give it the compacted view of pasted walls
            # (the FULL request is already the goal, set above, and is what the worker gets).
            verdict = await self.planner.readiness_gate(
                effort_id, _compact_paste(request), workspace_ctx)
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

    async def _run_in_host_context(self, effort_id: str) -> None:
        """WORKSPACE-SUFFICIENCY fix (operator 2026-07-07): a vendored project whose build needs
        its host (e.g. murder can only compile where its sibling `vendor/MonoGame` exists) was
        being worked in a STANDALONE clone where the build physically cannot run — so the worker
        flailed and the org gate-stacked around it. This dispatches the WORK in the HOST context
        (engine, recursively cloned so every sibling is present, submodule origins re-authed for
        push), where the worker edits the vendored subdir IN PLACE, builds for real, and publishes
        the fix to the vendored repo's own remote. Then the normal verify → wire → composition
        check → finish path runs. Generic for any host/submodule composition."""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        loc = await self.router.effort_thread(effort_id)
        if not (host and loc):
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}** isn't a vendored-in-a-host composition, so there's no host "
                f"context to run it in. Tell me the task and I'll run it normally.",
                thread_id=self._mgmt_thread_of(effort_id))
            return
        host_slug, sub_path, host_url, _sib = host
        channel_id, root = loc
        branch = self._effort_branch(effort_id)
        if effort_id in self._delegating:
            return
        self._delegating.add(effort_id)
        try:
            await self._reopen_if_closed(effort_id)
            try:
                _, goal, _ = await self.charters.current_goal(effort_id)
            except Exception:  # noqa: BLE001
                goal = ""
            hp = await self.projects.get(host_slug)
            check_cmd = ((hp or {}).get("check_cmd") or "").strip()
            build_line = (self._build_segment(check_cmd) if check_cmd
                          else "(build the solution as usual)")
            host_token = await self._project_token_for_slug(host_slug)
            instruction = (
                f"WORK IN HOST CONTEXT — your workspace is `{host_slug}` with ALL submodules "
                f"present, so the build can actually run here (this is why prior standalone "
                f"attempts couldn't compile). Do the task by editing files INSIDE `{sub_path}` "
                f"(the vendored `{proj}`):\n"
                f"1. Make the fix in `{sub_path}`.\n"
                f"2. BUILD + verify from the host: `{build_line}`. Iterate until it PASSES; keep "
                f"the passing output.\n"
                f"3. Publish the `{proj}` change to ITS OWN remote (from inside the submodule):\n"
                f"   cd {sub_path} && git checkout -b {branch} && git add -A && "
                f"git commit -m \"{effort_id}: <summary>\" && git push origin {branch}\n"
                f"4. Report the pushed commit hash + the passing build tail. If even HERE you "
                f"cannot build, do NOT fake it — report `BLOCKED:` / `NEEDS:` / `FEASIBLE:`.\n\n"
                f"THE TASK:\n{goal}"
            )
            await self.comms.post(
                Intent.effort_dispatch,
                f"▶ Re-running **{effort_id}** in the **host context** (`{host_slug}`, recursive) "
                f"so the build can actually run — editing `{sub_path}` in place.",
                effort_id=effort_id)
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=await self._session_for(effort_id), instruction=instruction,
                repo=host_url, repo_token=host_token, recurse_submodules=True,
            )
        finally:
            self._delegating.discard(effort_id)
        if result is None:
            return
        blk = self._extract_blocker(result.output or "")
        if blk:
            await self._elevate_blocker(effort_id, blk)
            return
        # The worker pushed the fix to the VENDORED repo's own remote — verify + gate + finish.
        sub_repo = await self._effort_repo(effort_id)
        delivery = await self._verify_delivery(effort_id, sub_repo) if sub_repo else BranchDelivery()
        if not delivery.landed:
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}** ran in the host context but I don't see its branch on "
                f"`{self._norm_repo(sub_repo or proj)}` — the submodule push may have failed. Not "
                f"done; reply to re-run.",
                thread_id=self._mgmt_thread_of(effort_id))
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        if not await self._gate_standing_intent(effort_id, channel_id, root, sub_repo, delivery):
            return
        await self._finish_effort(effort_id, result, delivery=delivery)

    async def _nl_proceed_execution(self, message: str, channel_id: str,
                                    thread_id: str | None) -> bool:
        """The operator's explicit go-ahead releases a dry-run execution HOLD (P4.0) and
        dispatches — their word is the §3 clearance (like "merge it"). Only claims the turn when
        an effort is genuinely held on a dry-run, so a bare "proceed" elsewhere still flows to
        normal intake. Optionally scoped to a named effort."""
        if not re.search(r"^\s*(?:proceed|go\s+ahead|go\s+for\s+it|you'?re\s+clear|authoriz|"
                         r"i\s+approve|clear\s+to\s+(?:go|proceed))\b", message.strip(), re.I):
            return False
        m_eid = re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message)
        held: list[str] = []
        for e in await self.gate.snapshot(open_only=True):
            if e["id"].startswith("__"):
                continue
            if m_eid and e["id"] != m_eid.group(0):
                continue
            st = await self.exec_gate.status(e["id"])
            if st.get("dry_run_status") in {"required", "failed", "running"}:
                held.append(e["id"])
        if not held:
            return False   # nothing held → let normal intake have the message
        for eid in held:
            await self.exec_gate.record_dry_run(eid, passed=True)
            await self.audit.log("dry_run_operator_cleared", effort_id=eid)
        await self._reengage(
            held, mgmt_channel=channel_id, mgmt_thread=thread_id,
            reply_prefix="✅ Go-ahead received — releasing the execution hold (your word is the "
                         "clearance; `main` still only changes on your merge).")
        return True

    async def _nl_show_log(self, message: str, channel_id: str, thread_id: str | None) -> bool:
        """NL-FIRST log access (operator 2026-07-07: "the PM should have access to logs"): the
        org records every build it ran itself (`org_build_check` events, full output) — "show
        the build log [for effort-x]" returns the newest one, so the operator reads the SAME
        evidence the PM reasoned over instead of going to the worker for it."""
        if not re.search(r"\b(?:show|see|view|get|share|paste)\b[^.\n]{0,40}?"
                         r"\b(?:build\s+|check\s+|error\s+)?logs?\b", message, re.I):
            return False
        m_eid = re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message)
        async with self.db.session_factory() as s:
            q = select(Event).where(Event.kind == "org_build_check")
            if m_eid:
                q = q.where(Event.effort_id == m_eid.group(0))
            ev = (await s.execute(q.order_by(Event.id.desc()).limit(1))).scalars().first()
        if ev is None:
            await self.chat.post(
                channel_id,
                "I have no org-run build logs yet"
                + (f" for `{m_eid.group(0)}`" if m_eid else "")
                + " — one appears every time I run a project's check myself (burn-down rounds, "
                  "composition checks, no-changes verification).",
                thread_id=thread_id)
            return True
        p = ev.payload or {}
        logtxt = (p.get("log") or "").strip() or "(empty log)"
        await self.chat.post(
            channel_id,
            f"🧾 **Org build log** — `{ev.effort_id}`, {ev.ts[:16]}Z, verdict "
            f"**{p.get('verdict', '?')}**"
            + (f", {p.get('errors')} error(s)" if p.get("errors") is not None else "")
            + f", `{p.get('cmd', '')[:120]}`:\n```\n{logtxt[:3400]}\n```",
            thread_id=thread_id)
        return True

    async def _nl_burndown_resume(self, message: str, channel_id: str,
                                  thread_id: str | None) -> bool:
        """“keep going” after a stalled burn-down = the operator buying more rounds — resume the
        loop from the last org-run failing log. Only claims the phrase when a stalled burn-down
        actually exists (otherwise the generic intake keeps it)."""
        if not re.search(r"^\s*keep\s+going\b|\bcontinue\s+(?:the\s+)?burn-?down\b|"
                         r"\bmore\s+rounds?\b", message.strip(), re.I):
            return False
        m_eid = re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message)
        async with self.db.session_factory() as s:
            q = select(Event).where(Event.kind == "burndown_stalled")
            if m_eid:
                q = q.where(Event.effort_id == m_eid.group(0))
            ev = (await s.execute(q.order_by(Event.id.desc()).limit(1))).scalars().first()
            eid = ev.effort_id if ev is not None else None
            log_ev = None
            if eid:
                log_ev = (await s.execute(
                    select(Event).where(Event.kind == "org_build_check",
                                        Event.effort_id == eid)
                    .order_by(Event.id.desc()).limit(1))).scalars().first()
        if not eid:
            return False   # no stalled burn-down — not ours to answer
        failing = ((log_ev.payload or {}).get("log") if log_ev is not None else "") or ""
        await self.chat.post(
            channel_id,
            f"▶ Resuming the burn-down on `{eid}` — more rounds, same rules (progress every "
            f"round or I raise it with the trajectory).", thread_id=thread_id)
        self._queue_burndown(eid, failing)
        return True

    async def _project_token_for_slug(self, slug: str) -> str | None:
        """The deploy token for a project by SLUG (the host repo, for the recursive clone/push
        re-auth) — same resolution as `_project_token` but keyed on the project directly:
        explicit token_env → per-owner LC_<OWNER>_TOKEN → GitHub App token → pool fallback."""
        import os

        from .modules.projects import owner_token_env
        p = await self.projects.get(slug)
        if not p:
            return None
        env_name = p.get("token_env")
        if env_name:
            return os.environ.get(env_name) or None
        cand = owner_token_env(p.get("repo_url", ""))
        if cand and os.environ.get(cand):
            return os.environ[cand]
        if self.github is not None and self.s.github_app_enabled:
            try:
                owner, _repo = parse_owner_repo(p.get("repo_url", ""))
                if owner.lower() == (self.github.owner or "").lower():
                    return await self.github.installation_token()
            except Exception as exc:  # noqa: BLE001
                log.debug("App-token for host %s skipped: %s", slug, exc)
        return None

    async def _reopen_if_closed(self, effort_id: str) -> None:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is not None and e.lifecycle != "open":
                e.lifecycle = "open"
                await s.commit()

    async def _auto_dry_run_via_isolation(self, effort_id: str, repo: str | None) -> bool:
        """P4.0 reconciliation (operator 2026-07-07: a "port the whole engine" prompt was
        classified `cross_effort` → dry-run REQUIRED → dead-ended waiting for a `/dry-run pass`
        command nobody runs, so the PM "got nothing done"). The gate's OWN intent (see
        execution_gate docstring) is: *"the dry-run execution itself is a worker step — an
        isolated branch that never merges"*. For a BRANCH-ISOLATED code effort with a runnable
        build, the org can PERFORM that rehearsal itself: the work lands on an agent branch that
        cannot reach `main` without the D4 human merge gate, and the org's own build of that
        branch (D2 / composition check / burn-down) IS the isolated rehearsal + verdict. So we
        auto-satisfy the dry-run and proceed — governance intact (nothing merges un-rehearsed or
        un-approved), no operator command required. Scoped to BREADTH risk (`cross_effort` /
        `cascading_refactor`); a genuinely `irreversible` act still waits for a human. Returns
        True when it auto-satisfied (execution may proceed). Generic for any project."""
        if not repo:
            return False   # no isolated branch to rehearse on → keep the human gate
        st = await self.exec_gate.status(effort_id)
        if st.get("risk") not in {"cross_effort", "cascading_refactor"}:
            return False   # 'irreversible' or unknown → a human should look first
        # a runnable build is what makes the isolated branch a real REHEARSAL (else there's
        # nothing to catch a cascading break with — fall back to the human dry-run gate)
        if not await self._machine_verified_effort(effort_id):
            return False
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        check_owner = host[0] if host else proj
        await self.exec_gate.start_dry_run(effort_id)
        await self.exec_gate.record_dry_run(effort_id, passed=True)
        await self.audit.log("dry_run_auto_isolated", effort_id=effort_id,
                             payload={"risk": st.get("risk"), "check_owner": check_owner})
        await self.comms.post(
            Intent.worker_activity,
            f"🧪 **High blast radius** ({st.get('risk')}) — but this is branch-isolated code work. "
            f"I'll rehearse it the safe way: the change lands on an **isolated agent branch** that "
            f"never reaches `main` without your merge, and I **build it myself** (`{check_owner}`) "
            f"as the rehearsal before any merge invite. Proceeding autonomously — no dry-run "
            f"command needed.",
            effort_id=effort_id)
        return True

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
            # Record the branch head BEFORE any work: a later "landed" whose head still equals
            # this is a pre-existing branch resurrected, not this run's delivery.
            if repo and self.github is not None and self.s.github_app_enabled:
                pre = await self._verify_delivery(effort_id, repo)
                self._pre_dispatch_head[effort_id] = pre.head_sha or ""
            # a fresh dispatch invalidates any prior org-verified verdict (new work, new head)
            self._org_verified.pop(effort_id, None)
            # P4.0 gate: a high-blast-radius effort may not reach REAL-code execution until its
            # isolated dry-run is recorded complete. Routine efforts pass immediately.
            ok, reason = await self.exec_gate.may_execute(effort_id)
            if not ok and not await self._auto_dry_run_via_isolation(effort_id, repo):
                await self.comms.post(
                    Intent.escalation,
                    f"⛔ execution held — {reason}. This effort isn't branch-isolated or has no "
                    f"build to rehearse with, so a human should look first: say **“proceed”** "
                    f"(your go-ahead is the clearance) or record a dry-run.",
                    effort_id=effort_id,
                )
                await self.comms.post(
                    Intent.operator_reply,
                    f"⛔ **{effort_id}** held before execution — {reason}. Say **“proceed”** and "
                    f"I'll run it (nothing reaches `main` without your merge either way).",
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
            # AUTO-ITERATION (operator 2026-07-07: "having to say re-run it while the pm knows
            # there's an issue — it should just re-run itself with an evolutionary prompt"): a
            # machine-detected failure queued an evolved goal; launch it now that this run has
            # fully closed (the single-flight guard is released above).
            nxt = self._iterate_after.pop(effort_id, None)
            burn = self._burndown_after.pop(effort_id, None)
            if burn is not None:
                # the burn-down supersedes a queued auto-iteration — it is the stronger loop
                # (progress-based, org-verified every round) and owns the same failing output
                self._spawn(self._burndown_loop(effort_id, burn))
            elif nxt:
                self._spawn(self.delegate(effort_id, channel_id, root_post_id, nxt))

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
            session_id=await self._session_for(effort_id), instruction=step, repo=repo, repo_token=repo_token,
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
    ):
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
            "If this task was READ-ONLY / you made NO file changes, SKIP the git steps entirely and "
            "reply exactly `NO CHANGES: <why>`. Otherwise reply with the branch name and the pushed "
            "commit hash."
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
                session_id=await self._session_for(effort_id), instruction=instruction, repo=repo, repo_token=repo_token,
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
        return result

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
        pub = await self._publish_effort(effort_id, channel_id, root, repo)
        # BLOCKER / feasibility FIRST — before any mechanical re-engage or NO-CHANGES logic: if the
        # worker named a real constraint (a missing dependency, an insufficient workspace, an
        # ambiguous/infeasible requirement), the PM's job is to HEAR it and ELEVATE it, not bark
        # "commit + push" at it (live 2026-07-07: the worker said "the standalone build fails
        # because ../MonoGame isn't present in this workspace" and the monitor ignored it entirely).
        blk = self._extract_blocker(pub.output or "") if pub is not None else None
        if blk:
            await self._elevate_blocker(effort_id, blk)
            return None
        # NO CHANGES protocol (read-only/investigation tasks): the worker explicitly reports it
        # changed nothing — a LEGITIMATE completion whose deliverable is its ANSWER, not a branch.
        # Honor it (the live miss ignored the worker's correct report and escalated 'undelivered').
        # BUT a FIX/BUILD request (goal demands verification) can't dodge the whole check stack by
        # falsely claiming "nothing to change" (live 2026-07-07: worker hallucinated "no Murder.FNA,
        # vendored ref in place, no changes" on a main that HAS Murder.FNA + the unfixed signature,
        # and never built) — such a claim must carry BUILD-PASS evidence, else it auto-iterates.
        if pub is not None and "NO CHANGES:" in (pub.output or ""):
            if await self._no_changes_acceptable(effort_id, pub.output or ""):
                return BranchDelivery(no_changes=True, branch=self._effort_branch(effort_id))
            # The claim carries no proof — so the ORG builds it and READS THE LOG ITSELF instead
            # of word-matching the worker (operator 2026-07-07: one NO-CHANGES claim was false,
            # the next was TRUE, and word-matching mis-called both — only a real build tells
            # them apart, and its failure output is the burn-down's work list, not a dead end).
            d0 = await self._verify_delivery(effort_id, repo)
            verdict, out, _n = await self._org_build_check(effort_id, on_branch=d0.landed)
            if verdict == "pass":
                await self.comms.post(
                    Intent.worker_activity,
                    "✅ the worker claims nothing needed changing — I ran the build **myself** "
                    "and it PASSES (org-verified; log on file). Accepting the no-op as done.",
                    effort_id=effort_id)
                if d0.landed:
                    self._org_verified[effort_id] = d0.head_sha or ""
                    return d0
                return BranchDelivery(no_changes=True, branch=self._effort_branch(effort_id))
            if verdict == "fail":
                self._queue_burndown(effort_id, out)
                return None
            # the org couldn't run a build here — the old proof-or-iterate ladder stands
            if await self._auto_iterate(
                    effort_id, "the worker claimed NO CHANGES on a fix/build request without "
                    "showing the build passes (and the org has no runnable check here)",
                    pub.output or ""):
                return None
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}** claimed nothing needed changing, but the goal was to FIX a "
                f"build and it showed no passing build — I couldn't accept that as done and "
                f"auto-iteration is spent. Reply to re-run, or tell me how to proceed.",
                thread_id=self._mgmt_thread_of(effort_id))
            await self.router.update_effort_card(effort_id, "error")
            return None
        delivery = await self._verify_delivery(effort_id, repo)
        if delivery.landed or not delivery.verifiable:
            # A "landed" branch whose head predates THIS run is a stale branch resurrected —
            # nothing was delivered now (live 2026-07-07: an empty-workspace run "delivered"
            # yesterday's branch and wired an old commit into the host).
            if delivery.landed and self._is_stale_head(effort_id, delivery):
                return await self._recover_stale_delivery(
                    effort_id, channel_id, root, repo, delivery)
            # A landed branch is NOT a delivery if it references submodule commits nobody can
            # fetch — gate on gitlink reachability before treating it as done.
            if delivery.landed and not await self._gate_gitlinks(effort_id, channel_id, root, repo):
                return None
            # …nor if it VIOLATES the project's standing intent (reintroduces a forbidden term) —
            # a green-but-wrong delivery (the NuGet-revert trap) must never merge.
            if delivery.landed and not await self._gate_standing_intent(
                    effort_id, channel_id, root, repo, delivery):
                return None
            # …nor if its commits net to ZERO file changes (the fix is stranded elsewhere,
            # usually an unpushed vendored-submodule commit) — recover before a false done.
            if delivery.landed and delivery.files_changed == 0:
                return await self._recover_empty_delivery(
                    effort_id, channel_id, root, repo, delivery)
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
        pub = await self._publish_effort(effort_id, channel_id, root, repo, firm=True)
        if pub is not None and "NO CHANGES:" in (pub.output or ""):
            return BranchDelivery(no_changes=True, branch=self._effort_branch(effort_id))
        delivery = await self._verify_delivery(effort_id, repo)
        if delivery.landed:
            if self._is_stale_head(effort_id, delivery):
                return await self._recover_stale_delivery(
                    effort_id, channel_id, root, repo, delivery)
            if not await self._gate_gitlinks(effort_id, channel_id, root, repo):
                return None
            if not await self._gate_standing_intent(effort_id, channel_id, root, repo, delivery):
                return None
            if delivery.files_changed == 0:
                return await self._recover_empty_delivery(
                    effort_id, channel_id, root, repo, delivery)
            return delivery

        # Still undelivered after a re-engage. Before climbing the ladder, RECOVER the benign case
        # ourselves: "no branch" is EITHER unpushed work (a real failure) OR a goal that ALREADY
        # HOLDS in the repo (a stale effort / work landed earlier) — and the org can tell these
        # apart with a read-only state check against the effort's own goal. Only a verified
        # already-holds closes as done; anything else still escalates (§3 — never a false `done`).
        state = await self._verify_goal_state(effort_id, channel_id, root, repo)
        if state is not None:
            return state
        await self._escalate_undelivered(effort_id, delivery)
        return None

    @staticmethod
    def _extract_blocker(output: str) -> dict | None:
        """Parse a worker's report for a BLOCKER / feasibility constraint — the explicit protocol
        (`BLOCKED:`/`NEEDS:`/`FEASIBLE:`) OR the same thing stated in plain language. Returns
        {blocked, needs, feasible, raw} or None. The PM elevates this instead of steamrolling with
        a mechanical re-engage (live 2026-07-07)."""
        if not output or not _BLOCKER_RE.search(output):
            return None
        def _field(label: str) -> str:
            m = re.search(rf"`?{label}`?:\s*(.+)", output, re.I)
            return (m.group(1).strip()[:400] if m else "")
        blocked = _field("BLOCKED")
        if not blocked:
            # NL blocker: quote the sentence that tripped the detector
            m = _BLOCKER_RE.search(output)
            start = output.rfind("\n", 0, m.start()) + 1
            end = output.find("\n", m.end())
            blocked = output[start:(end if end > 0 else len(output))].strip()[:400]
        return {"blocked": blocked, "needs": _field("NEEDS"),
                "feasible": _field("FEASIBLE"), "raw": output[:1500]}

    async def _elevate_blocker(self, effort_id: str, blk: dict) -> None:
        """The PM's job the mechanical monitor skipped: HEAR the worker's constraint and surface it
        to the operator with a synthesized read + an actionable next step, keeping the effort OPEN
        (needs-attention) — never a false done, never a blind re-dispatch. If the constraint is a
        workspace insufficiency on a composition effort, name the concrete remedy (run in the host
        context). Generic for any project/blocker."""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        remedy = ""
        if host and re.search(r"workspace|present|sibling|submodule|host|context|standalone",
                              blk["blocked"], re.I):
            remedy = (f"\n\n**Likely remedy:** this reads as a WORKSPACE-context limit — `{proj}` "
                      f"only builds inside its host `{host[0]}` (where `{host[1]}`'s siblings like "
                      f"the vendored dependency exist). I can re-run this **in the host context** "
                      f"(the engine, recursively cloned) so the worker can actually build and "
                      f"verify. Say _\"run it in the host context\"_ and I'll do that.")
        needs = f"\n- **What it needs:** {blk['needs']}" if blk["needs"] else ""
        feas = f"\n- **Feasible as scoped:** {blk['feasible']}" if blk["feasible"] else ""
        body = (
            f"🚧 **{effort_id}** — the worker raised a real CONSTRAINT (this is the org learning "
            f"the task's limits, **not** a worker failure or a done):\n"
            f"- **What's blocking:** {blk['blocked']}{needs}{feas}\n"
            f"It is **not** marked done. Tell me how to proceed — provide what it needs, re-scope, "
            f"or ask me to dig further.{remedy}")
        await self.comms.post(Intent.escalation, body, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, body,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "needs-attention")
        await self.audit.log("effort_blocked_elevated", effort_id=effort_id,
                             payload={"blocked": blk["blocked"][:200], "needs": blk["needs"][:200],
                                      "feasible": blk["feasible"][:100]})

    async def _auto_iterate(self, effort_id: str, reason: str, failing_tail: str) -> bool:
        """The PM already KNOWS this delivery failed its own bar — re-run automatically with an
        EVOLVED goal (the failure folded in) instead of asking the operator to say "re-run it"
        (operator 2026-07-07). Bounded: max 2 auto-iterations per effort (audit-counted, so the
        limit survives restarts); past the limit the honest escalation stands. Returns True when
        an iteration is queued — the caller words its closure as 'iterating', not 'your move'."""
        try:
            async with self.db.session_factory() as s:
                n = int((await s.execute(
                    select(func.count()).select_from(Event).where(
                        Event.kind == "auto_iteration", Event.effort_id == effort_id)
                )).scalar_one())
        except Exception:  # noqa: BLE001
            n = 0
        if n >= 2:
            return False
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal_text = ""
        base_goal = (goal_text or "").split("\n\nITERATION ")[0].strip()
        if not base_goal:
            return False
        await self.audit.log("auto_iteration", effort_id=effort_id,
                             payload={"round": n + 1, "reason": reason[:150]})
        evolved = (
            f"{base_goal}\n\nITERATION {n + 1}/2 (automatic — no operator involved): the previous "
            f"delivery FAILED the machine bar: {reason}.\nFAILING OUTPUT:\n```\n"
            f"{(failing_tail or '(none captured)')[:800]}\n```\n"
            f"Continue from the existing branch: fix the CAUSE of that failure, push NEW commits, "
            f"and re-verify before publishing."
        )
        self._iterate_after[effort_id] = evolved
        await self.comms.post(
            Intent.worker_activity,
            f"🔁 **Auto-iteration {n + 1}/2** — {reason}. Re-dispatching with the failing output "
            f"folded into the goal; no operator action needed.",
            effort_id=effort_id,
        )
        return True

    def _queue_burndown(self, effort_id: str, failing_log: str) -> None:
        """Start (or defer) the burn-down for a RED org build. Inside delegate's single-flight the
        loop must wait for the current run to close — delegate's finally launches it; anywhere
        else it starts immediately."""
        if effort_id in self._delegating:
            self._burndown_after[effort_id] = failing_log
        else:
            self._spawn(self._burndown_loop(effort_id, failing_log))

    async def _org_build_check(self, effort_id: str, *, on_branch: bool = True
                               ) -> tuple[str, str, int | None]:
        """THE PM READS THE LOGS (operator 2026-07-07): the org runs the project's build ITSELF
        and reasons over the full output — a worker's self-report is never the evidence. Resolves
        the sufficient workspace automatically: a vendored project builds inside its HOST
        (recursive clone, the effort branch checked out at the vendored path); a plain project
        builds on its own branch. Returns (pass|fail|unknown, log, error_count) and records the
        log as an `org_build_check` event so the PM's claims — and the operator, via "show the
        build log" — always trace to real evidence. `on_branch=False` checks the repo state as-is
        (a NO-CHANGES claim, where no branch may exist)."""
        proj = await self._effort_project(effort_id)
        branch = self._effort_branch(effort_id)
        host = await self._vendored_host(proj) if proj else None
        check_owner = host[0] if host else proj
        p = await self.projects.get(check_owner) if check_owner else None
        check_cmd = ((p or {}).get("check_cmd") or "").strip()
        loc = await self.router.effort_thread(effort_id)
        if not check_cmd:
            return "unknown", f"no check command configured for `{check_owner}`", None
        if not loc:
            return "unknown", "no effort thread", None
        channel_id, root = loc
        build_only = self._build_segment(check_cmd)
        if host:
            host_slug, sub_path, host_url, _sib = host
            focus, token, recurse = host_url, await self._project_token_for_slug(host_slug), True
            # HERMETIC checkout (live 2026-07-08: the verifier reused a worker whose workspace
            # still held a timed-out agent's UNCOMMITTED edits — the count measured branch +
            # leftovers, "progress" that wasn't on the remote). `checkout -f` discards tracked
            # edits + `clean -fd` drops untracked ⇒ the build measures EXACTLY the pushed
            # branch. (Proxy-legal: `reset --hard` is tag-only through the git-proxy.)
            checkout = (f"cd /workspace/{sub_path} && git fetch origin {branch} && "
                        f"git checkout -f -B {branch} FETCH_HEAD && git clean -fd && "
                        f"cd /workspace && "
                        if on_branch else "")
        else:
            repo = await self._effort_repo(effort_id) or ""
            if not repo:
                return "unknown", "no repo focused", None
            focus = f"{repo}#{branch}" if on_branch else repo
            token, recurse, checkout = await self._project_token(effort_id), False, ""
        # DETERMINISTIC FIRST (2026-07-08): "run a command, report its output" is a MACHINE step
        # — the daemon's `/check` returns the real exit code + full log with no model in the loop
        # (the LLM verifier burned its whole turn re-running builds and never wrote the verdict).
        command = f"{checkout}{build_only}" if checkout else f"cd /workspace && {build_only}"
        mode = "exec"
        try:
            exit_code, out, timed_out = await self.router.exec_check(
                effort_id, command=command, session_id=f"{effort_id}~chk",
                repo=focus, repo_token=token, recurse_submodules=recurse, timeout=900,
            )
            if timed_out or exit_code is None:
                verdict, n = "unknown", None
                out = f"(build timed out / no exit code)\n{out[-3000:]}"
            elif exit_code == 0:
                verdict, n = "pass", 0
            else:
                verdict, n = "fail", _error_count(out)
        except Exception as exc:  # noqa: BLE001 — old daemon without /check, or a focus failure
            log.info("deterministic check unavailable for %s (%s) — LLM verifier fallback",
                     effort_id, exc)
            mode = "llm"
            verdict, out, n = await self._llm_verify_fallback(
                effort_id, channel_id, root, check_owner, checkout, build_only,
                focus, token, recurse)
        await self.audit.log("org_build_check", effort_id=effort_id,
                             payload={"verdict": verdict, "errors": n, "owner": check_owner,
                                      "mode": mode, "cmd": check_cmd[:200], "log": out[:6000]})
        return verdict, out, n

    async def _llm_verify_fallback(
        self, effort_id: str, channel_id: str, root: str, check_owner: str | None,
        checkout: str, build_only: str, focus: str, token: str | None, recurse: bool,
    ) -> tuple[str, str, int | None]:
        """The LLM build-verifier — FALLBACK only, for a worker daemon that predates the
        deterministic `/check` route. Fresh isolated session (a reused work session made the
        agent no-op, live 2026-07-08)."""
        instruction = (
            f"You are a BUILD VERIFIER working in a FRESH workspace (repo `{check_owner}`, all "
            f"submodules already present). Your ONLY job is to RUN one build command and report "
            f"its real result — you MUST execute it in the terminal; do NOT answer without "
            f"running it, and do NOT change any files or run any other git writes.\n"
            f"Run EXACTLY this (it is already set up for you):\n  {checkout}{build_only}\n"
            f"Then report, based on the ACTUAL exit status and output:\n"
            f"- if it exited 0: reply exactly `CHECK: PASS`\n"
            f"- if it failed: reply `CHECK: FAIL`, then on the next line `ERRORS: <total error "
            f"count>`, then EVERY DISTINCT error line from the output (deduplicated, keep file "
            f"paths, up to 120 lines), then the final summary line.\n"
            f"- if you genuinely cannot run the build (a tool/path is missing), reply "
            f"`CHECK: BLOCKED` and name exactly what stopped you.\n"
            f"Reporting the real failing output is a SUCCESSFUL result — do not fake a pass and "
            f"do not stay silent."
        )
        self._verify_seq += 1
        try:
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=f"{effort_id}~vfy{self._verify_seq}", instruction=instruction,
                repo=focus, repo_token=token, recurse_submodules=recurse,
            )
        except Exception as exc:  # noqa: BLE001 — an unrunnable check is a verdict, never fatal
            return "unknown", f"check couldn't run: {str(exc)[:200]}", None
        out = (result.output or "") if result is not None else ""
        if "CHECK: PASS" in out:
            return "pass", out, 0
        if "CHECK: FAIL" in out:
            return "fail", out, _error_count(out)
        return "unknown", out, None

    async def _burndown_loop(self, effort_id: str, failing_log: str) -> None:
        """AUTONOMOUS ERROR BURN-DOWN (operator 2026-07-07: "all 138 errors should have been
        worked through autonomously and not elevated in the first place" + "multiple workers
        where there is natural segregation of tasks"): a RED org build starts a PROGRESS-based
        loop, not a fixed retry count. Each round: dispatch fix work (fanned across workers when
        the errors split cleanly by file), then the org re-builds ITSELF and reads the log. Keep
        going while the error count falls; 0 → the normal green finish (PR + merge gate, held
        until now); two rounds without progress (or the round cap) → an honest elevation carrying
        the full trajectory. Generic for any project/toolchain with a check command."""
        if effort_id in self._delegating:
            self._burndown_after[effort_id] = failing_log
            return
        self._delegating.add(effort_id)
        try:
            loc = await self.router.effort_thread(effort_id)
            repo = await self._effort_repo(effort_id) or ""
            if not loc or not repo:
                return
            channel_id, root = loc
            await self._reopen_if_closed(effort_id)
            await self.router.update_effort_card(effort_id, "working")
            n0 = _error_count(failing_log)
            counts: list[int] = [n0 if n0 is not None else -1]
            brief = _error_brief(failing_log)
            # SCOPE BRIEF — the PM says what work is about to be undertaken, to CONFIRM but not
            # WAIT (operator 2026-07-07): the operator can stop it; silence means proceed.
            msg = (f"🔥 **Burn-down engaged** on **{effort_id}** — the org's own build is RED: "
                   f"{brief}.\nPlan: I'll drive fix rounds **autonomously** (multiple workers in "
                   f"parallel when the errors split cleanly by file), re-run the build myself "
                   f"after every round, and report the error trajectory as I go. PRs and merge "
                   f"invites are HELD until it builds green.\n_No action needed — say “stop” to "
                   f"halt me, “show the build log” for the raw evidence._")
            await self.comms.post(Intent.worker_activity, msg, effort_id=effort_id)
            await self.comms.post(Intent.operator_reply, msg,
                                  thread_id=self._mgmt_thread_of(effort_id))
            await self.audit.log("burndown_started", effort_id=effort_id,
                                 payload={"errors": n0, "brief": brief[:300]})
            errors_log = failing_log
            branch_exists = (await self._verify_delivery(effort_id, repo)).landed
            stall = 0
            last_result = None
            for rnd in range(1, 13):   # progress-gated; small per-round slices need the headroom
                if await self.gate.is_killed() or await self.gate.is_frozen(effort_id):
                    await self.comms.post(
                        Intent.worker_activity,
                        f"⏸ burn-down halted at round {rnd} — the effort is frozen or the kill "
                        f"switch is engaged. The branch keeps all progress; say “resume” to "
                        f"continue.", effort_id=effort_id)
                    return
                results = await self._burndown_work(
                    effort_id, channel_id, root, rnd, errors_log, branch_exists=branch_exists)
                last_result = next((r for r in reversed(results) if r is not None), last_result)
                for r in results:   # a stated constraint is HEARD mid-loop too, never steamrolled
                    blk = self._extract_blocker((r.output or "") if r else "")
                    if blk:
                        await self._elevate_blocker(effort_id, blk)
                        return
                if not branch_exists:
                    branch_exists = (await self._verify_delivery(effort_id, repo)).landed
                verdict, out, n = await self._org_build_check(effort_id, on_branch=branch_exists)
                if verdict == "pass":
                    counts.append(0)
                    traj = " → ".join(str(c) if c >= 0 else "?" for c in counts)
                    await self.comms.post(
                        Intent.worker_activity,
                        f"✅ **round {rnd}: GREEN** — error trajectory **{traj}**. Org-verified "
                        f"(I ran the build myself; log on file). Proceeding to PR + merge gate.",
                        effort_id=effort_id)
                    await self.audit.log("burndown_green", effort_id=effort_id,
                                         payload={"rounds": rnd, "trajectory": counts})
                    delivery = await self._verify_delivery(effort_id, repo)
                    if not delivery.landed:
                        await self.comms.post(
                            Intent.operator_reply,
                            f"⚠️ **{effort_id}**: the build is green but no branch landed on the "
                            f"remote — the last push may have failed. Not done; reply to re-run "
                            f"the publish.", thread_id=self._mgmt_thread_of(effort_id))
                        await self.router.update_effort_card(effort_id, "needs-attention")
                        return
                    self._org_verified[effort_id] = delivery.head_sha or ""
                    if not await self._gate_standing_intent(
                            effort_id, channel_id, root, repo, delivery):
                        return
                    from types import SimpleNamespace
                    res = last_result or SimpleNamespace(
                        status="done", output=f"burn-down green after {rnd} round(s)")
                    await self._finish_effort(effort_id, res, delivery=delivery)
                    return
                if verdict == "unknown":
                    stall += 1
                    await self.comms.post(
                        Intent.worker_activity,
                        f"⚠️ round {rnd}: the org build check returned no verdict "
                        f"({out[:120]}) — {stall}/2 before I raise it.", effort_id=effort_id)
                else:
                    counts.append(n if n is not None else counts[-1])
                    prev = counts[-2]
                    improved = prev < 0 or (n is not None and n < prev)
                    await self.audit.log("burndown_round", effort_id=effort_id,
                                         payload={"round": rnd, "errors": n, "prev": prev})
                    if improved:
                        stall = 0
                        await self.comms.post(
                            Intent.worker_activity,
                            f"🔥 round {rnd}: **{prev if prev >= 0 else '?'} → {n}** errors — "
                            f"progress, continuing autonomously.", effort_id=effort_id)
                    else:
                        stall += 1
                        await self.comms.post(
                            Intent.worker_activity,
                            f"⚠️ round {rnd}: no progress ({prev} → {n} errors) — {stall}/2 "
                            f"before I raise it with the full picture.", effort_id=effort_id)
                    errors_log = out
                if stall >= 2:
                    await self._burndown_elevate(effort_id, counts, errors_log,
                                                 "two consecutive rounds without progress")
                    return
            await self._burndown_elevate(effort_id, counts, errors_log, "round cap (12) reached")
        finally:
            self._delegating.discard(effort_id)
            # a red queued DURING this loop (e.g. the finish path's D2 disagreed) re-enters —
            # bounded: every loop's stall detector elevates after 2 rounds without progress
            queued = self._burndown_after.pop(effort_id, None)
            if queued is not None:
                self._spawn(self._burndown_loop(effort_id, queued))

    async def _burndown_work(self, effort_id: str, channel_id: str, root: str, rnd: int,
                             errors_log: str, *, branch_exists: bool) -> list:
        """One burn-down round's WORK dispatch. When the errors cluster into two file-disjoint
        groups (and the branch exists to base parts on), fan out across workers on part branches
        and fold them back via the API — the operator's "multiple workers where the work
        naturally segregates". Otherwise a single evolved wake on the effort branch."""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        branch = self._effort_branch(effort_id)
        repo = await self._effort_repo(effort_id) or ""
        groups = self._partition_error_groups(_error_lines(errors_log))
        can_fan = (len(groups) == 2 and branch_exists and self.github is not None
                   and self.s.github_app_enabled)
        if not can_fan:
            flat = [ln for g in groups for ln in g]
            res = await self._burndown_wake(effort_id, channel_id, root, rnd, flat, part=None,
                                            host=host, branch=branch, repo=repo,
                                            branch_exists=branch_exists)
            return [res]
        await self.comms.post(
            Intent.worker_activity,
            f"🔀 round {rnd}: the errors split into {len(groups)} file-disjoint groups "
            f"({len(groups[0])} + {len(groups[1])} lines) — fanning out across workers in "
            f"parallel on part branches; I'll fold them back automatically.",
            effort_id=effort_id)

        async def one(i: int, g: list[str]):
            try:
                return await self._burndown_wake(effort_id, channel_id, root, rnd, g, part=i,
                                                 host=host, branch=branch, repo=repo,
                                                 branch_exists=True)
            except NoCapacityError:
                return None   # only one slot free — the other group re-lists next round
            except Exception as exc:  # noqa: BLE001 — one part failing must not kill the round
                log.warning("burn-down part %s failed for %s: %s", i, effort_id, exc)
                return None
        results = list(await asyncio.gather(*(one(i, g) for i, g in enumerate(groups, 1))))
        folded = []
        for i in range(1, len(groups) + 1):
            part_branch = f"{branch}-pt{i}r{rnd}"   # per-round names (match _burndown_wake)
            r = await merge_branch(
                self.github, repo, branch, part_branch,
                message=f"{effort_id}: fold burn-down part {i} (round {rnd})",
                api_base=self.s.github_api_base, transport=self._gh_transport)
            folded.append(f"pt{i}: {r.summary}")
            if r.ok:  # the part is folded in — delete the transient part branch (hygiene: the
                # operator asked for no branch sprawl; its commits now live on the effort branch)
                await delete_branch(self.github, repo, part_branch,
                                    api_base=self.s.github_api_base,
                                    transport=self._gh_transport)
        await self.audit.log("burndown_parts", effort_id=effort_id,
                             payload={"round": rnd, "parts": len(groups),
                                      "fold": [f[:120] for f in folded]})
        await self.comms.post(Intent.worker_activity,
                              "🔀 parts folded back into the effort branch — "
                              + "; ".join(folded), effort_id=effort_id)
        return results

    async def _burndown_wake(self, effort_id: str, channel_id: str, root: str, rnd: int,
                             err_lines: list[str], *, part: int | None, host, branch: str,
                             repo: str, branch_exists: bool):
        """One worker wake of a burn-down round: continue from the delivery branch (host context
        for a vendored project — the only place its build can run), fix the listed errors,
        re-build, push, and report `ERRORS AFTER:` so progress is machine-readable."""
        try:
            _, goal, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal = ""
        base_goal = (goal or "").split("\n\nITERATION ")[0].strip()[:2500]
        # PER-ROUND part branches (live 2026-07-08 v7: folds 404'd on rounds whose part worker
        # never pushed — unique names also make any stale leftover branch harmless).
        push_branch = f"{branch}-pt{part}r{rnd}" if part else branch
        # SMALL SLICES (live 2026-07-08): 60 errors in one turn overwhelmed the 27B worker —
        # 30 min of thinking, zero commands, poll timeout, nothing pushed. A round is a BITE,
        # not the meal: ~16 errors per worker per round finishes inside the turn budget and
        # lands real commits; the loop's progress test does the rest. (Parallel parts share one
        # GPU, so their turns are slower — keep them smaller still.)
        cap = 12 if part else 16
        slice_txt = "\n".join(err_lines[:cap]) or "(see the failing log in the thread above)"
        if len(err_lines) > cap:
            slice_txt += (f"\n… plus {len(err_lines) - cap} more (later rounds — do NOT attempt "
                          f"them this turn; finish and PUSH your slice)")
        files = sorted({m.group(1) for m in (
            _ERR_FILE_RE.match(ln) for ln in err_lines[:cap]) if m})
        scope = (f"ONLY touch these files — a sibling worker owns the rest in parallel: "
                 + ", ".join(files[:20]) + "\n   ") if part and files else ""
        if host:
            host_slug, sub_path, host_url, _sib = host
            hp = await self.projects.get(host_slug)
            build_line = self._build_segment(((hp or {}).get("check_cmd") or "").strip()) \
                or "(build the solution as usual)"
            workspace = (f"Your workspace is `{host_slug}` with ALL submodules present (the only "
                         f"place this build can run); the work happens INSIDE `{sub_path}`.")
            checkout = (f"cd /workspace/{sub_path} && (git fetch origin {branch} && "
                        f"git checkout -B {push_branch} FETCH_HEAD || git checkout -b {push_branch})")
            publish = (f"cd /workspace/{sub_path} && git add -A && git commit -m "
                       f"\"{effort_id}: burn-down round {rnd}\" && git push origin {push_branch}")
            focus, recurse = host_url, True
            token = await self._project_token_for_slug(host_slug)
        else:
            pp = await self.projects.get(await self._effort_project(effort_id) or "")
            build_line = self._build_segment(((pp or {}).get("check_cmd") or "").strip()) \
                or "(build/test as usual)"
            workspace = f"Your workspace is the project repo, on `{branch}`."
            checkout = f"git checkout -B {push_branch}"
            publish = (f"git add -A && git commit -m \"{effort_id}: burn-down round {rnd}\" && "
                       f"git push origin {push_branch}")
            focus = f"{repo}#{branch}" if branch_exists else repo
            recurse, token = False, await self._project_token(effort_id)
        instruction = (
            f"BURN-DOWN ROUND {rnd} (automatic — the org re-builds after every round and keeps "
            f"going while the error count falls). {workspace} The org's own build currently "
            f"FAILS; your ONLY job this round is to clear as many of the errors below as you can "
            f"and PUSH real commits.\n"
            f"1. Continue from the delivered work: {checkout}\n"
            f"2. {scope}Fix these errors (mechanical, category by category; prefer small, "
            f"uniform changes):\n{slice_txt}\n"
            f"3. Re-build from /workspace: {build_line} — iterate until your slice is clean (or "
            f"what remains is outside your file scope).\n"
            f"   COMMIT + PUSH INCREMENTALLY — after EVERY few files fixed, not only at the end "
            f"(your turn has a hard time budget; pushed progress survives it, unpushed work "
            f"does not — the org re-builds and continues from whatever landed):\n   {publish}\n"
            f"4. Final publish: {publish}\n"
            f"5. Report — FIRST line exactly `ERRORS AFTER: <count from your final build>`, then "
            f"any remaining error lines. If genuinely blocked, report `BLOCKED:` / `NEEDS:` / "
            f"`FEASIBLE:` instead of guessing.\n\n"
            f"CONTEXT — the goal this serves:\n{base_goal}"
        )
        # FRESH session per part per ROUND (live 2026-07-08 v7: reused ~pt sessions rotted —
        # rounds 2/4/5 quit in ~90s with nothing pushed; round 1 on fresh sessions fixed 48).
        session = f"{effort_id}~pt{part}r{rnd}" if part else await self._session_for(effort_id)
        return await self.router.wake(
            effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
            session_id=session, instruction=instruction,
            repo=focus, repo_token=token, recurse_submodules=recurse,
        )

    @staticmethod
    def _partition_error_groups(lines: list[str]) -> list[list[str]]:
        """Split an error list into (at most two) FILE-DISJOINT groups for parallel workers —
        only when the work is big enough to pay for a second recursive workspace (≥24 distinct
        errors) and both halves are substantial (≥8 each). File-disjoint means the parts merge
        cleanly; anything less returns one group (sequential is always safe)."""
        if len(lines) < 24:
            return [lines]
        clusters: dict[str, list[str]] = {}
        loose: list[str] = []
        for ln in lines:
            m = _ERR_FILE_RE.match(ln)
            if m:
                clusters.setdefault(m.group(1), []).append(ln)
            else:
                loose.append(ln)
        if len(clusters) < 2:
            return [lines]
        buckets: list[list[str]] = [[], []]
        sizes = [0, 0]
        for f in sorted(clusters, key=lambda k: -len(clusters[k])):
            i = 0 if sizes[0] <= sizes[1] else 1
            buckets[i].extend(clusters[f])
            sizes[i] += len(clusters[f])
        buckets[0].extend(loose)   # un-attributable lines ride with group 1 (its worker may fix them)
        if min(sizes) < 8:
            return [lines]
        return buckets

    async def _burndown_elevate(self, effort_id: str, counts: list[int], errors_log: str,
                                why: str) -> None:
        """Burn-down stopped short of green — the HONEST, evidence-carrying elevation: the full
        error trajectory (from the org's own builds, not worker claims), what still fails, and
        the real next moves. The branch keeps all progress; the effort stays open."""
        traj = " → ".join(str(c) if c >= 0 else "?" for c in counts)
        brief = _error_brief(errors_log)
        remaining = "\n".join(_error_lines(errors_log)[:15])
        await self.audit.log("burndown_stalled", effort_id=effort_id,
                             payload={"why": why, "trajectory": counts})
        body = (
            f"🧱 **{effort_id}** — burn-down STALLED ({why}). The honest picture, from the org's "
            f"own build logs:\n"
            f"- error trajectory across rounds: **{traj}**\n"
            f"- still failing: {brief}\n"
            + (f"```\n{remaining[:900]}\n```\n" if remaining else "")
            + f"The branch keeps every fix so far — nothing is lost. What remains hasn't yielded "
            f"to mechanical rounds, which usually means it needs a judgment call (an API choice, "
            f"a design decision, missing context). Tell me how to proceed — answer the open "
            f"question, re-scope, or say **“keep going”** for more rounds.")
        await self.comms.post(Intent.escalation, body, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, body,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "needs-attention")

    def _is_stale_head(self, effort_id: str, delivery: BranchDelivery) -> bool:
        """True when the 'landed' branch head is EXACTLY where it was before this run dispatched —
        i.e. the branch pre-existed and this run contributed nothing (no pre-head recorded, or a
        changed head, is never stale — fail-open)."""
        pre = self._pre_dispatch_head.get(effort_id, "")
        return bool(pre) and bool(delivery.head_sha) and delivery.head_sha == pre

    async def _recover_stale_delivery(
        self, effort_id: str, channel_id: str, root: str, repo: str,
        delivery: BranchDelivery,
    ) -> BranchDelivery | None:
        """The branch verified 'landed' but its head predates this run — a resurrected stale
        branch, not a delivery. Re-engage once with the plain truth; a NEW head → proceed through
        the normal gates; still stale → state-check, then honest escalation. Never opens a PR or
        wires a host to a stale commit."""
        branch = delivery.branch
        # HONESTY FIRST (live 2026-07-07: the FIRST real success in 10+ rounds — the converged
        # branch already carried the requested change — was reported "delivered NOTHING NEW"):
        # a stale head whose branch ALREADY DIFFERS from base is PRIOR DELIVERED WORK, not an
        # empty run. Say that, then let the ORG verify it by building, instead of re-engaging
        # the worker to re-do finished work (or worse, mislabelling success as failure).
        if (delivery.files_changed and delivery.files_changed > 0
                and await self._machine_verified_effort(effort_id)):
            await self.comms.post(
                Intent.worker_activity,
                f"ℹ️ no NEW commits this round — but `{branch}` **already carries a delivery** "
                f"({delivery.ahead} commit(s), {delivery.files_changed} file(s) changed vs base) "
                f"from an earlier round. That is not “nothing new” — verifying it by running "
                f"the build myself…",
                effort_id=effort_id,
            )
            verdict, out, _n = await self._org_build_check(effort_id)
            if verdict == "pass":
                self._org_verified[effort_id] = delivery.head_sha or ""
                await self.comms.post(
                    Intent.worker_activity,
                    "✅ org-verified: the build PASSES with the branch as delivered — the "
                    "requested change was already in place. Proceeding through the normal gates.",
                    effort_id=effort_id)
                if not await self._gate_gitlinks(effort_id, channel_id, root, repo):
                    return None
                if not await self._gate_standing_intent(
                        effort_id, channel_id, root, repo, delivery):
                    return None
                return delivery
            if verdict == "fail":
                await self.comms.post(
                    Intent.worker_activity,
                    f"🔍 the branch carries real changes, but my own build of it is RED — "
                    f"{_error_brief(out)}. This is unfinished work, not a failed delivery.",
                    effort_id=effort_id)
                self._queue_burndown(effort_id, out)
                return None
            # unknown → the org couldn't get a build verdict. This branch DOES carry a delivery,
            # so it is NOT "nothing new" — never re-dispatch the worker to redo finished work and
            # never mislabel it. Say so honestly and keep it open (needs-attention), naming why
            # the build couldn't run so the operator can unblock verification.
            await self.comms.post(
                Intent.operator_reply,
                f"ℹ️ **{effort_id}**: `{branch}` carries a real delivery ({delivery.ahead} "
                f"commit(s), {delivery.files_changed} file(s)), but I couldn't run the build to "
                f"verify it — {out[:180] or 'the build check returned no verdict'}. **Not** "
                f"marked done and no PR opened; the work is safe on the branch. Say **“re-run "
                f"it”** and I'll retry the build, or tell me how you'd like to verify.",
                thread_id=self._mgmt_thread_of(effort_id))
            await self.router.update_effort_card(effort_id, "needs-attention")
            await self.audit.log("org_build_unverifiable", effort_id=effort_id,
                                 payload={"branch": branch, "reason": out[:200]})
            return None
        await self.comms.post(
            Intent.worker_activity,
            f"🔍 `{branch}` exists on the remote but its head (`{delivery.head_sha[:10]}`) is "
            f"UNCHANGED from before this run — nothing new was delivered (a stale branch from an "
            f"earlier attempt). Re-dispatching with that context (PM monitor, §4.2).",
            effort_id=effort_id,
        )
        instruction = (
            f"NOTHING NEW WAS DELIVERED: `{branch}` already existed on the remote and its head "
            f"(`{delivery.head_sha[:10]}`) is unchanged from before this run started — your turn "
            f"produced no pushed commits. Do the task now: implement the goal in the workspace, "
            f"commit, and push NEW commits to `{branch}`. If the workspace looks empty or wrong, "
            f"SAY SO explicitly. If there is genuinely nothing to change, reply exactly "
            f"`NO CHANGES: <why>`."
        )
        try:
            repo_token = await self._project_token(effort_id)
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=await self._session_for(effort_id), instruction=instruction,
                repo=repo, repo_token=repo_token,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("stale-delivery re-engage failed for %s: %s", effort_id, exc)
            result = None
        if result is not None and "NO CHANGES:" in (result.output or ""):
            return BranchDelivery(no_changes=True, branch=branch)
        delivery = await self._verify_delivery(effort_id, repo)
        if delivery.landed and not self._is_stale_head(effort_id, delivery):
            if not await self._gate_gitlinks(effort_id, channel_id, root, repo):
                return None
            if delivery.files_changed == 0:
                return await self._recover_empty_delivery(
                    effort_id, channel_id, root, repo, delivery)
            return delivery
        state = await self._verify_goal_state(effort_id, channel_id, root, repo)
        if state is not None:
            return state
        await self.audit.log("delivery_stale_head", effort_id=effort_id,
                             payload={"branch": branch, "head": delivery.head_sha})
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{effort_id}** ran but delivered NOTHING NEW — `{branch}` still sits at its "
            f"pre-run head `{delivery.head_sha[:10]}` after a re-engage. ↑ raised to you: it is "
            f"**not** done and no PR was opened for the stale content.",
            effort_id=effort_id,
        )
        await self.router.update_effort_card(effort_id, "error")
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}** produced no new commits (its branch pre-existed, unchanged) — "
            f"**not** marked done, no PR opened. Reply to re-run it (a fresh session will be "
            f"used), or say it's expected and I'll close it.",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        return None

    async def _gate_standing_intent(
        self, effort_id: str, channel_id: str, root: str, repo: str,
        delivery: BranchDelivery,
    ) -> bool:
        """ANTI-DRIFT gate: reject a delivery whose diff RE-INTRODUCES a term the project's
        standing intent forbids (live 2026-07-07: the org reverted to the `Murder.FNA` NuGet
        package to force a green build — the exact thing the operator said never to do). Returns
        True when clean/no-intent (proceed); False when it violated (auto-iterate if rounds
        remain, else escalate — the caller returns None, never a merge). Generic: enforces
        whatever the operator forbade, in any project. Fail-open — an unreadable diff never blocks."""
        proj = await self._effort_project(effort_id)
        p = await self.projects.get(proj) if proj else None
        si = ((p or {}).get("standing_intent") or "").strip()
        forbidden = self._forbidden_terms(si)
        if not (si and forbidden):
            return True
        try:
            added = await read_added_lines(
                self.github, repo, delivery.branch,
                api_base=self.s.github_api_base, transport=self._gh_transport)
        except Exception as exc:  # noqa: BLE001 — the gate never blocks on an infra hiccup
            log.debug("standing-intent diff read failed for %s: %s", effort_id, exc)
            return True
        blob = "\n".join(added).lower()
        hit = [t for t in forbidden if t.lower() in blob]
        if not hit:
            return True
        listed = ", ".join(f"`{t}`" for t in hit)
        reason = (f"the delivery VIOLATES `{proj}`'s standing intent — it reintroduces "
                  f"forbidden term(s) {listed} (intent: {si[:200]})")
        await self.comms.post(
            Intent.worker_activity,
            f"⛔ **Standing-intent violation** on `{delivery.branch}`: the diff adds {listed}, "
            f"which `{proj}`'s architecture rule forbids. This will NOT merge.",
            effort_id=effort_id,
        )
        iterating = await self._auto_iterate(
            effort_id, reason,
            "Forbidden term(s) added to the diff: " + listed
            + f"\nStanding intent: {si}\nImplement the task the REQUIRED way; do not revert or "
            f"work around the architecture.")
        if iterating:
            return False  # auto-iteration owns the retry; the caller returns None (no PR now)
        # limit reached → honest escalation, never a merge
        await self.router.update_effort_card(effort_id, "error")
        await self.audit.log("delivery_intent_violation", effort_id=effort_id,
                             payload={"branch": delivery.branch, "terms": hit, "intent": si[:300]})
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{effort_id}** keeps violating `{proj}`'s standing intent ({listed}) after "
            f"auto-iterating — it can't reach the goal without breaking the architecture rule. "
            f"↑ raised to you: this may be a genuine constraint conflict (the required approach "
            f"may need real work, not a quick fix). No PR opened.",
            effort_id=effort_id,
        )
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}** can't satisfy your build without breaking `{proj}`'s standing "
            f"intent (it kept reintroducing {listed}). **Not** merged, no PR. This usually means "
            f"the required approach is real work — tell me how you'd like to proceed.",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        return False   # not done; nothing to hand forward

    async def _recover_empty_delivery(
        self, effort_id: str, channel_id: str, root: str, repo: str,
        delivery: BranchDelivery,
    ) -> BranchDelivery | None:
        """A branch LANDED but its commits net to ZERO file changes — the 'delivery' changes
        nothing for consumers. The classic cause (live 2026-07-05, PR #4): the worker made its fix
        INSIDE a vendored submodule checkout, then re-pointed the gitlink back to a published
        commit — the fix stayed in its container while the superproject branch carries only
        cancelled-out commits. Re-engage the AFFINE worker once with the exact remedy; re-verify;
        a real diff → proceed, an explicit NO CHANGES → the no-op verdict, else state-check →
        escalate. Never opens a PR for an empty delivery."""
        branch = self._effort_branch(effort_id)
        await self.comms.post(
            Intent.worker_activity,
            f"🔍 `{branch}` landed with {delivery.ahead} commit(s) but **zero net file changes** "
            f"vs base — the delivery changes nothing for consumers. If the real fix lives inside "
            f"a vendored submodule checkout, it was never published. Re-dispatching with the "
            f"remedy (PM monitor, §4.2).",
            effort_id=effort_id,
        )
        instruction = (
            f"YOUR PUBLISHED BRANCH IS EMPTY: `{branch}` is ahead of base but its commits net to "
            f"ZERO file changes — as delivered, it fixes nothing. The usual cause: you made the "
            f"actual change inside a vendored SUBMODULE checkout and then re-pointed the gitlink "
            f"back, so the fix only exists in this workspace.\n"
            f"Recover it now:\n"
            f"(a) if your change is inside a submodule at <path>: publish it —\n"
            f"    git -C /workspace/<path> push origin HEAD:refs/heads/{branch}\n"
            f"    then commit the updated gitlink in the superproject and push `{branch}` again;\n"
            f"(b) if the change belongs in the SUPERPROJECT: re-apply it there, commit, push;\n"
            f"(c) if there is GENUINELY nothing to change, reply exactly `NO CHANGES: <why>`.\n"
            f"Do NOT push to main/master. Do NOT force-push. Reply with what you did."
        )
        try:
            repo_token = await self._project_token(effort_id)
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=await self._session_for(effort_id), instruction=instruction, repo=repo, repo_token=repo_token,
            )
        except Exception as exc:  # noqa: BLE001 — fall through to state-check/escalation
            log.warning("empty-delivery re-engage failed for %s: %s", effort_id, exc)
            result = None
        if result is not None and "NO CHANGES:" in (result.output or ""):
            return BranchDelivery(no_changes=True, branch=branch)
        delivery = await self._verify_delivery(effort_id, repo)
        if delivery.landed and delivery.files_changed != 0:
            if not await self._gate_gitlinks(effort_id, channel_id, root, repo):
                return None
            return delivery
        state = await self._verify_goal_state(effort_id, channel_id, root, repo)
        if state is not None:
            return state
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{effort_id}** published `{branch}` but it still changes **no files** after a "
            f"re-engage — the actual fix may be stranded in the worker's workspace (an unpushed "
            f"submodule commit). ↑ raised to you: it is **not** done and no PR was opened.",
            effort_id=effort_id,
        )
        await self.router.update_effort_card(effort_id, "error")
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}**'s branch has commits but **zero net file changes** — as "
            f"delivered it fixes nothing, so it is **not** marked done and I opened no PR. I "
            f"re-engaged the worker once; reply to re-run it, or say it's expected and I'll "
            f"close it.",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        await self.audit.log("delivery_empty_diff", effort_id=effort_id,
                             payload={"branch": branch, "ahead": delivery.ahead})
        return None

    async def _verify_goal_state(
        self, effort_id: str, channel_id: str, root: str, repo: str
    ) -> BranchDelivery | None:
        """Undelivered-recovery (DELIVERY-PIPELINE §4.2 extension): wake the AFFINE worker for a
        READ-ONLY check of the effort's goal against the repo's CURRENT state. `STATE HOLDS` +
        evidence ⇒ a legitimate no-op completion (returned as the NO-CHANGES verdict, closure
        carries the evidence); anything else ⇒ None (the caller escalates). Goal-anchored and
        task-agnostic — no repo- or project-specific logic."""
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal_text = ""
        goal = (goal_text or "").strip()
        if not goal:
            return None
        instruction = (
            "STATE CHECK (read-only verification — change NOTHING, no git writes). Your branch "
            "never landed on the remote, so I need ground truth about the repo's CURRENT state.\n"
            f"THE GOAL WAS:\n{_compact_paste(goal)}\n"
            "Inspect the checkout and decide: does the desired end-state ALREADY HOLD (an earlier "
            "effort delivered it, or nothing was ever needed)? Reply with EXACTLY one first line:\n"
            "`STATE HOLDS: <one line of concrete evidence — the files/config you verified>`\n"
            "`STATE MISSING: <one line on what is absent>`"
        )
        try:
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=await self._session_for(effort_id), instruction=instruction, repo=repo,
                repo_token=await self._project_token(effort_id),
            )
        except Exception as exc:  # noqa: BLE001 — recovery is best-effort; escalation still runs
            log.debug("state check failed for %s: %s", effort_id, exc)
            return None
        out = (result.output or "") if result is not None else ""
        m = re.search(r"STATE HOLDS:\s*(.*)", out)
        if not m:
            return None
        evidence = (m.group(1) or "").strip().splitlines()[0][:300]
        await self.comms.post(
            Intent.worker_activity,
            "✅ nothing to deliver — a read-only state check confirms the goal **already holds**"
            + (f": {evidence}" if evidence else "") + ". Closing as done (no changes needed).",
            effort_id=effort_id,
        )
        await self.audit.log("effort_state_holds", effort_id=effort_id,
                             payload={"evidence": evidence})
        return BranchDelivery(no_changes=True, branch=self._effort_branch(effort_id))

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

    async def _broken_gitlinks(self, effort_id: str, repo: str) -> list[dict]:
        """Changed-but-unreachable submodule pointers on the effort's branch ([] = clean or
        uncheckable — the read fails open; only a positive 'commit not found' blocks)."""
        if self.github is None or not self.s.github_app_enabled:
            return []
        try:
            return await read_broken_gitlinks(
                self.github, repo, self._effort_branch(effort_id),
                api_base=self.s.github_api_base, transport=self._gh_transport)
        except Exception as exc:  # noqa: BLE001 — the gate must never crash the finalize path
            log.debug("gitlink check failed for %s: %s", effort_id, exc)
            return []

    async def _gate_gitlinks(
        self, effort_id: str, channel_id: str, root: str, repo: str
    ) -> bool:
        """DELIVERY-PIPELINE gitlink-reachability gate (live 2026-07-05): the engine branch landed
        but pointed `vendor/MonoGame` at a commit the worker made ONLY inside its container — the
        operator's `git submodule update --init --recursive` failed with "not our ref", i.e. the
        PM verified 'landed' and invited a merge of a branch no one else could build. For every
        gitlink the branch CHANGED: verify the commit exists on the submodule remote; if not,
        re-engage the AFFINE worker once (its workspace still holds the unpushed commit) with the
        exact per-path remedy, re-check, and on a still-broken branch escalate honestly. Returns
        True when clean/uncheckable, False after escalation (the effort is NOT done)."""
        broken = await self._broken_gitlinks(effort_id, repo)
        if not broken:
            return True
        branch = self._effort_branch(effort_id)

        def _listing(items: list[dict]) -> str:
            return "\n".join(f"- `{b['path']}` → `{b['sha'][:10]}` (missing from "
                             f"`{b['submodule_repo']}`)" for b in items)

        await self.comms.post(
            Intent.worker_activity,
            f"🔍 the branch landed, but it references submodule commit(s) that do NOT exist on "
            f"the submodule remote(s) — a fresh clone fails `git submodule update` with "
            f"\"not our ref\":\n{_listing(broken)}\nRe-dispatching the worker to publish or "
            f"re-point them (PM monitor, §4.2).",
            effort_id=effort_id,
        )
        pushes = "\n".join(f"  git -C /workspace/{b['path']} push origin HEAD:refs/heads/{branch}"
                           for b in broken)
        instruction = (
            f"YOUR PUBLISHED BRANCH IS BROKEN for anyone who clones it: it references submodule "
            f"commit(s) that do NOT exist on the submodule remote(s), so `git submodule update "
            f"--init --recursive` fails with \"not our ref\".\n{_listing(broken)}\n"
            f"For EACH path above do ONE of:\n"
            f"(a) PUBLISH the submodule commit you made (if you changed the submodule on purpose):\n"
            f"{pushes}\n"
            f"(b) or RE-POINT the gitlink to a commit that already exists on the submodule remote "
            f"(`git -C <path> fetch origin && git -C <path> checkout origin/HEAD`), then commit the "
            f"pointer change in the superproject and push `{branch}` again.\n"
            f"Do NOT push to main/master. Do NOT force-push. Reply with what you did per path."
        )
        try:
            repo_token = await self._project_token(effort_id)
            await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=await self._session_for(effort_id), instruction=instruction, repo=repo, repo_token=repo_token,
            )
        except Exception as exc:  # noqa: BLE001 — a wedged re-engage falls through to escalation
            log.warning("gitlink re-engage failed for %s: %s", effort_id, exc)
        broken = await self._broken_gitlinks(effort_id, repo)
        if not broken:
            await self.comms.post(
                Intent.worker_activity,
                "✅ submodule pointer(s) now resolve on their remote(s) — the branch is buildable "
                "from a fresh clone.",
                effort_id=effort_id,
            )
            return True
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{effort_id}**'s branch landed but is BROKEN for consumers — after a re-engage "
            f"it still references submodule commit(s) missing from their remote(s):\n"
            f"{_listing(broken)}\n↑ raised to you: a fresh clone of `{branch}` cannot build "
            f"(\"not our ref\" on submodule update).",
            effort_id=effort_id,
        )
        await self.router.update_effort_card(effort_id, "error")
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}** pushed `{branch}`, but the branch references submodule commit(s) "
            f"that don't exist on their remote(s) — it will NOT build from a fresh clone. I "
            f"re-engaged the worker once and it's still broken, so it is **not** marked done:\n"
            f"{_listing(broken)}",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        await self.audit.log("delivery_broken_gitlink", effort_id=effort_id,
                             payload={"branch": branch, "broken": broken})
        return False

    async def _machine_verified_effort(self, effort_id: str) -> bool:
        """True when this effort's correctness is verified by the ORG'S OWN BUILD — its project,
        or the host that vendors it, has a `check_cmd` — AND delivery is branch-isolated (a repo
        is focused → agent branch → D4 human-gated merge). For such efforts the org build +
        burn-down + D4 are the real, un-gameable gates, so the subjective per-checkpoint review
        runs ADVISORY rather than as a hard freeze, and a breadth-risk dry-run is rehearsed by
        the org itself (operator 2026-07-07). Generic across projects."""
        repo = await self._effort_repo(effort_id)
        if not repo:
            return False
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        check_owner = host[0] if host else proj
        cp = await self.projects.get(check_owner) if check_owner else None
        return bool(((cp or {}).get("check_cmd") or "").strip())

    async def _gate_deliverable(self, effort_id: str, result, cp_id: str) -> bool:
        """Stage-5 gates on a step's deliverable (risky efforts): sampled monitor (P3.7) + a
        differently-goaled review (P4.4-4.7). Returns True to proceed, False to STOP (frozen)."""
        deliverable = (result.output or "").strip()
        # P3.7 — the LLM monitor, forced on risky efforts (never per-token, never a health-probe).
        # This stays a HARD freeze even for build-verified efforts: it catches the worker going
        # OFF-TASK / off-scope (a safety concern), which a build check can't.
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
            # ADVISORY for machine-verified efforts (D3): correctness here is proven by the org's
            # OWN build (composition/burn-down/D2) and the merge is D4 human-gated, so a
            # subjective quality flag is SURFACED, not a freeze that stalls the port (operator
            # 2026-07-07: a 138-error port froze on a review of a mid-work status message).
            if await self._machine_verified_effort(effort_id):
                await self._advisory_review_note(effort_id, verdicts)
                await self.stop_gates.force_clear(
                    cp_id, reason="machine-verified effort — review is advisory (D3)")
                return True
            await self._on_review_flag(effort_id, verdicts)
            return False
        return True

    async def _advisory_review_note(self, effort_id: str, verdicts: list) -> None:
        """Surface a review flag as ADVISORY input (not a freeze) — still learned from (P6.4)."""
        flagged = [v for v in verdicts if getattr(v, "verdict", "pass") == "flag"]
        detail = "; ".join(
            f"[{getattr(v, 'lens', '?')}] "
            + "; ".join(getattr(v, "findings", None) or [getattr(v, "reasoning", "")])
            for v in flagged
        )[:400] or "review flagged the deliverable"
        await self._observe_pattern(effort_id, detail)
        await self.comms.post(
            Intent.worker_activity,
            f"📝 _Review input (advisory) — this effort's build is machine-verified by the org "
            f"and the merge is human-gated, so I'm noting this and letting the build be the "
            f"arbiter rather than freezing:_ {detail}",
            effort_id=effort_id)

    async def _report_completion(self, effort_id: str, result) -> None:
        """The undeliverable case (§2): the effort is FROZEN. Say WHICH freeze and what one word
        releases it — never 'a concern or the kill switch' (live 2026-07-06: the operator had no
        context to act: 'i can't answer the question i don't have the context')."""
        if result is None:
            killed = await self.gate.is_killed()
            if killed:
                # Remember it — releasing the switch re-dispatches automatically (no re-asking).
                self._kill_blocked.add(effort_id)
                why = (
                    f"🛑 **{effort_id}** is queued behind the fleet-wide **kill switch** (engaged "
                    f"by your earlier “stop”). Nothing is wrong with the effort itself. Say "
                    f"**resume** and I'll release the fleet and re-dispatch this effort "
                    f"automatically — nothing else needed."
                )
            else:
                try:
                    concerns = await self.gate.open_concerns(effort_id)
                except Exception:  # noqa: BLE001
                    concerns = []
                if concerns:
                    listed = "\n".join(
                        f"- {getattr(c, 'what_surfaced', '')[:200]}" for c in concerns[:3])
                    why = (
                        f"⚠️ **{effort_id}** is frozen on an open **concern** that needs your "
                        f"decision:\n{listed}\nDecide it with `approve {effort_id}` / "
                        f"`modify {effort_id}` / `abort {effort_id}` — it dispatches once cleared."
                    )
                else:
                    why = (
                        f"⚠️ **{effort_id}** is frozen (gate state `frozen`, no open concern on "
                        f"record — likely from an earlier session). `approve {effort_id}` clears "
                        f"it, or `abort {effort_id}` cancels it."
                    )
            await self.comms.post(Intent.worker_activity, why, effort_id=effort_id)
            await self.comms.post(
                Intent.operator_reply, why, thread_id=self._mgmt_thread_of(effort_id),
            )

    async def _apply_note(self, effort_id: str, repo: str | None, branch: str) -> str:
        """Exact operator-side steps to try a landed delivery locally (live 2026-07-06: 'as the
        operator i don't know what to do with this fix') — repo named plainly, fetch/checkout,
        the submodule re-sync every vendored layout needs, and the project's own check command
        when one is set. Generic for any repo/branch."""
        if not (repo and branch):
            return ""
        check = ""
        slug = await self._effort_project(effort_id)
        if slug:
            p = await self.projects.get(slug)
            check = ((p or {}).get("check_cmd") or "").strip()
        return (
            f"\n🧭 **Try it locally before merging** (this branch is on `{self._norm_repo(repo)}`):\n"
            f"```\ngit fetch origin {branch}\ngit checkout {branch}\n"
            f"git submodule sync --recursive\ngit submodule update --init --recursive\n"
            f"{check or '# then rebuild your solution as usual'}\n```\n"
            f"_If you merge instead: `git checkout main && git pull`, then the same two "
            f"submodule lines, then rebuild._"
        )

    async def _sibling_pr_note(self, repo: str | None, branch: str) -> str:
        """A closure footnote mapping the OTHER open agent PRs on this repo (+ any files they
        overlap with this branch). One effort = one branch = one PR is the D1 design, so when
        several effort-PRs are open in parallel the relationship must be self-explaining — the
        live miss was the operator reading successive deliveries on PR #3 then PR #2 as 'the
        worker keeps switching branches'. '' when there are no siblings; best-effort."""
        if not (repo and self.github is not None and self.s.github_app_enabled):
            return ""
        try:
            siblings = await read_sibling_agent_prs(
                self.github, repo, branch,
                api_base=self.s.github_api_base, transport=self._gh_transport)
            if not siblings:
                return ""
            _, _, own_files = await read_branch_changes(
                self.github, repo, branch,
                api_base=self.s.github_api_base, transport=self._gh_transport)
            own = {f.split("`")[1] for f in own_files if "`" in f}
            lines = []
            for sib in siblings:
                overlap = sorted(own.intersection(sib["files"]))
                lines.append(
                    f"- PR #{sib['number']} (`{sib['head']}`)"
                    + ((" — ⚠️ **overlaps this one on** "
                        + ", ".join(f"`{f}`" for f in overlap[:5])) if overlap
                       else " — no file overlap")
                )
            return ("\n🔀 _Other agent PRs are open on this repo (each effort delivers on its own "
                    "branch + PR):_\n" + "\n".join(lines)
                    + "\n_Review overlapping PRs together — merge one and tell me to close the "
                      "other(s), or say which should win._")
        except Exception as exc:  # noqa: BLE001 — a visibility footnote must never block closure
            log.debug("sibling PR note failed for %s: %s", repo, exc)
            return ""

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

    async def _run_check(self, effort_id: str, check_cmd: str) -> tuple[str, str]:
        """Run the project's D2 check command on the AFFINE worker (its workspace is already on the
        delivered branch after publish). Returns (status, tail): 'pass' | 'fail' | 'unknown'.
        Worker-reported (the worker executes + reports the exit) — labelled as such upstream."""
        loc = await self.router.effort_thread(effort_id)
        if not loc:
            return "unknown", "no effort thread"
        channel_id, root = loc
        instruction = (
            f"RUN THE PROJECT CHECK (a verification step — change NOTHING). Execute exactly:\n"
            f"  cd /workspace && {check_cmd}\n"
            f"If it exits 0, reply exactly `CHECK: PASS`. If it fails, reply `CHECK: FAIL` followed "
            f"by the last ~15 lines of the failing output. Do not attempt fixes in this step."
        )
        try:
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=await self._session_for(effort_id), instruction=instruction, repo=None,
            )
        except (httpx.HTTPStatusError, httpx.TransportError, NoCapacityError) as exc:
            return "unknown", str(exc)[:160]
        out = (result.output or "") if result else ""
        if "CHECK: PASS" in out:
            return "pass", ""
        if "CHECK: FAIL" in out:
            return "fail", out.split("CHECK: FAIL", 1)[1].strip()[:600]
        return "unknown", out.strip()[:200]

    async def _d2_gate(self, effort_id: str, repo: str, delivery: BranchDelivery,
                       merge_id: str) -> tuple[str, BranchDelivery]:
        """DELIVERY-PIPELINE D2 — the autonomous test series, red-gating the merge: run the project's
        check on the delivered branch BEFORE the merge gate is presented. Red → route back to the
        owning effort ONCE (re-ground → fix → re-push → re-check, the corpus's loop); still red →
        the merge gate is WITHDRAWN (PR stays open; escalated) — a red never travels forward. No
        check command configured → skipped with an honest note. Returns (note-for-closure, possibly
        updated delivery — a fix pushes a new commit)."""
        proj = await self._effort_project(effort_id)
        p = await self.projects.get(proj) if proj else None
        check_cmd = (p or {}).get("check_cmd") or ""
        if not check_cmd:
            return (f"\n🧪 _D2 checks skipped — no check command configured for `{proj}` "
                    f"(set one: `/project check {proj} \"<cmd>\"`)._", delivery)
        status, tail = await self._run_check(effort_id, check_cmd)
        if status == "pass":
            return f"\n🧪 **D2 checks passed** (`{check_cmd}`, worker-reported).", delivery
        if status == "unknown":
            return (f"\n🧪 _D2 check couldn't run ({tail or 'no verdict'}) — presenting the merge "
                    f"gate WITHOUT a green check; verify before merging._", delivery)
        # RED — route back to the owning effort once (fix on the SAME branch), then re-check.
        await self.comms.post(
            Intent.worker_activity,
            f"❌ **D2 check failed** (`{check_cmd}`) — routing back to the effort to fix + re-push "
            f"(re-ground → fix → re-test, never forward):\n```\n{tail[:500]}\n```",
            effort_id=effort_id,
        )
        loc = await self.router.effort_thread(effort_id)
        if loc:
            channel_id, root = loc
            fix_instruction = (
                f"THE PROJECT CHECK FAILED on your delivered branch. Failing output:\n```\n{tail}\n```\n"
                f"Fix the CAUSE (stay in scope of your original task), then commit and push to the "
                f"SAME branch ({delivery.branch}):\n  git add -A && git commit -m \"fix: check "
                f"failure\" && git push origin {delivery.branch}\nThen reply with what you changed."
            )
            try:
                repo_token = await self._project_token(effort_id)
                await self.router.wake(
                    effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                    session_id=await self._session_for(effort_id), instruction=fix_instruction, repo=repo,
                    repo_token=repo_token,
                )
            except (httpx.HTTPStatusError, httpx.TransportError, NoCapacityError) as exc:
                log.warning("D2 fix wake failed for %s: %s", effort_id, exc)
            new_delivery = await self._verify_delivery(effort_id, repo)
            if new_delivery.landed:
                delivery = new_delivery
            status2, tail2 = await self._run_check(effort_id, check_cmd)
            if status2 == "pass":
                return (f"\n🧪 **D2 checks passed after one fix round** (`{check_cmd}`, "
                        f"worker-reported).", delivery)
        # STILL red → withdraw the merge gate (the PR stays open for human inspection) and hand
        # the failing log to the BURN-DOWN — a red with a work list keeps being worked, not
        # parked on the operator (operator 2026-07-07). Never forward on red.
        self._pending_merge.pop(merge_id, None)
        await self.pending.delete(merge_id)
        self._queue_burndown(effort_id, tail)
        await self.comms.post(
            Intent.escalation,
            f"⛔ **D2 checks still failing** after a fix round (`{check_cmd}`). The merge gate is "
            f"withdrawn — nothing moves forward on red. Burn-down engaged: I'll keep driving fix "
            f"rounds autonomously and re-invite the merge when it's green.",
            effort_id=effort_id,
        )
        return (f"\n⛔ **D2 checks FAILED** (`{check_cmd}`) — merge gate withdrawn; burn-down "
                f"continues autonomously. See the effort thread for the failing output.", delivery)

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
        msg = f"{icon} {res.summary}" + (f" — {res.url}" if res.ok and res.url else "")
        if res.ok:   # D6 — the human-testing handoff: merged code is now YOURS to verify (UX-FLOW D6)
            msg += await self._d6_handoff(entry.get("effort_id"), entry.get("repo") or "")
            # RS.2: main moved → refresh the repo's docs in Open Brain (background, announced in
            # the project channel). Only merged main is after-action truth (§5 triggers).
            self._spawn(self._repo_sync_for_repo_url(entry.get("repo") or ""))
            # Repo hygiene: a merge often SUPERSEDES parallel effort-PRs — surface the leftovers
            # with the exact close command instead of letting them accumulate (operator: "keep
            # the repo clean"). Closing stays operator-worded (reversible, but their call).
            try:
                leftovers = await read_sibling_agent_prs(
                    self.github, entry.get("repo") or "", own_branch="",
                    api_base=self.s.github_api_base, transport=self._gh_transport)
                if leftovers:
                    listing = "\n".join(f"- PR #{p['number']} (`{p['head']}`)" for p in leftovers)
                    msg += (f"\n🧹 _Still open on this repo:_\n{listing}\n_If this merge supersedes "
                            f"one, say **\"close PR <n>\"** and I'll retire it (branch kept)._")
            except Exception as exc:  # noqa: BLE001 — hygiene hint must never block the merge report
                log.debug("post-merge hygiene scan failed: %s", exc)
        if reply is not None:
            await reply(msg)
        eid = entry.get("effort_id")
        if eid:   # bring the audience back down (CM.4)
            await self.comms.post(Intent.closure, f"{icon} {res.summary} (operator-approved merge).",
                                  effort_id=eid)

    async def _repo_sync(self, slug: str, *, ref: str = "", announce_channel: str | None = None,
                         announce_thread: str | None = None) -> None:
        """RS.2 thin trigger (REPO-SOURCES-WIRING §5): ask openbrain-research to ingest this
        project's docs + structural manifests as PRIMARY sources — the ENGINE enumerates, fetches
        at a pinned sha, injection-screens and stages; the bridge only says WHEN (onboard / D4
        merge / operator ask). Syncs the repo AND its registered upstream (the docs usually live
        in the parent). Best-effort + transparent: results are posted, failures never block."""
        if not self.s.repo_sync_enabled:
            return
        p = await self.projects.get(slug)
        if not p:
            return
        targets = [p["repo_url"]] + ([p["upstream_url"]] if p.get("upstream_url") else [])
        headers = {"content-type": "application/json"}
        if self.s.research_key:
            headers["x-brain-key"] = self.s.research_key
        lines: list[str] = []
        for target in targets:
            short = target.split("github.com/")[-1]
            try:
                async with httpx.AsyncClient(timeout=240.0,
                                             transport=self._research_transport) as c:
                    r = await c.post(
                        f"{self.s.research_url.rstrip('/')}/sources/repo-sync", headers=headers,
                        json={"repo_url": target, **({"ref": ref} if ref else {})})
                    d = r.json()
                if r.status_code == 200 and d.get("ok"):
                    nq = len(d.get("quarantined") or [])
                    nsk = len(d.get("skipped") or [])
                    lines.append(f"📚 `{short}`: **{d.get('synced', 0)}** doc/manifest source(s) "
                                 f"ingested @ `{str(d.get('sha', ''))[:10]}`"
                                 + (f" · ⚠️ {nq} quarantined (injection screen)" if nq else "")
                                 + (f" · {nsk} skipped (caps/binaries)" if nsk else ""))
                else:
                    lines.append(f"⚠️ `{short}`: sync failed — "
                                 f"{str(d.get('error') or r.status_code)[:120]}")
            except Exception as exc:  # noqa: BLE001 — knowledge sync must never break a flow
                lines.append(f"⚠️ `{short}`: research engine unreachable — {str(exc)[:80]}")
        await self.audit.log("repo_sources_synced", payload={"slug": slug, "results": lines[:6]})
        if announce_channel:
            await self.chat.post(
                announce_channel,
                "🧠 **Knowledge sync** (repo docs → Open Brain sources):\n"
                + "\n".join(f"- {ln}" for ln in lines)
                + "\n_Repo questions now get research-grounded, cited answers from these._",
                thread_id=announce_thread,
            )

    async def _repo_sync_for_repo_url(self, repo_url: str, *, ref: str = "") -> None:
        """Merge-triggered sync (D4 → knowledge refresh): resolve the project owning `repo_url`
        and sync it, announcing into its project channel."""
        for p in await self.projects.list():
            if p["repo_url"].rstrip("/") == (repo_url or "").rstrip("/"):
                chan = await self.router.ensure_project_channel(p["slug"])
                await self._repo_sync(p["slug"], ref=ref, announce_channel=chan)
                return

    async def _d6_handoff(self, effort_id: str | None, repo: str) -> str:
        """D6 — the human-testing handoff appended to every successful merge: what to do next, and
        how the loop closes (pass → done; fail → a NEW effort back through intake — just say what's
        broken). Includes the project's check command as the suggested local verification."""
        proj = await self._effort_project(effort_id) if effort_id else None
        if not proj and repo:
            for p in await self.projects.list():
                if p["repo_url"].rstrip("/") == repo.rstrip("/"):
                    proj = p["slug"]
                    break
        check = ((await self.projects.get(proj)) or {}).get("check_cmd") if proj else None
        verify = (f" run `{check}`," if check else "")
        return (f"\n🧪 **Human testing (D6):** pull `main` (with submodules if any),{verify} and try "
                f"it. Works → nothing more to do. Broken → just tell me what's wrong and I'll open a "
                f"fix effort (the loop closes back through intake).")

    async def _nl_pr_close(self, message: str, channel_id: str, thread_id: str | None) -> bool:
        """Operator-plane repo hygiene (interim until the repo-maintainer role, P5.3): a plain
        "close PR 3" / "close pull request #3 on <project>" closes a superseded agent PR via the
        App — deterministic (never the small model), REVERSIBLE (branch kept, reopenable). Scans
        the onboarded repos for an OPEN PR with that number; several matches → disambiguate, never
        guess. Returns True when the message was handled here."""
        bulk = re.search(r"\b(?:close|remove|clear|drop)\b[^.\n]{0,40}?\ball\b[^.\n]{0,40}?"
                         r"\b(?:open\s+)?(?:pr|pull\s+request)s\b", message, re.IGNORECASE)
        m = re.search(r"\bclose\b[^.\n]{0,30}?\b(?:pr|pull\s+request)s?\s*#?(\d+)\b", message,
                      re.IGNORECASE)
        if not (bulk or m):
            return False

        async def say(msg: str) -> None:
            await self.chat.post(channel_id, msg, thread_id=thread_id)

        if self.github is None or not self.s.github_app_enabled:
            await say("⚠️ I can't manage PRs — the GitHub App isn't set up (see SETUP-github-app.md).")
            return True
        if bulk:
            # "remove ALL pull requests" — sweep every open AGENT PR across the onboarded repos
            # (reversible: closing keeps branches; each PR can be reopened).
            lines: list[str] = []
            for p in (await self.projects.list())[:8]:
                prs = await read_sibling_agent_prs(
                    self.github, p["repo_url"], own_branch="",
                    api_base=self.s.github_api_base, transport=self._gh_transport)
                for pr in prs:
                    res = await close_pull_request(
                        self.github, p["repo_url"], pr["number"],
                        api_base=self.s.github_api_base, transport=self._gh_transport)
                    await self.audit.log("pr_closed", payload={
                        "repo": p["repo_url"], "pr": pr["number"], "ok": res.ok,
                        "by": "operator-nl-bulk"})
                    lines.append(("✅ " if res.ok else "⚠️ ")
                                 + f"`{p['slug']}` PR #{pr['number']} (`{pr['head']}`): "
                                 + ("closed" if res.ok else res.summary))
            await say("**PR sweep:**\n" + "\n".join(lines) if lines else
                      "No open agent PRs found on any onboarded repo — nothing to close.")
            return True
        number = int(m.group(1))
        projects = await self.projects.list()
        named = await self._intent_named_projects(message, exclude_slug="")
        if named:   # "… on <project>" scopes the search
            projects = [p for p in projects if p["slug"] in named]
        hits: list[tuple[str, str]] = []   # (slug, repo_url) whose OPEN PR #number exists
        for p in projects[:8]:
            prs = await read_sibling_agent_prs(
                self.github, p["repo_url"], own_branch="",
                api_base=self.s.github_api_base, transport=self._gh_transport)
            if any(pr["number"] == number for pr in prs):
                hits.append((p["slug"], p["repo_url"]))
        if not hits:
            await say(f"I couldn't find an OPEN agent PR **#{number}**"
                      + (f" on **{', '.join(named)}**" if named else " on any onboarded repo")
                      + " — it may already be closed/merged, or isn't an agent branch.")
            return True
        if len(hits) > 1:
            listing = ", ".join(f"`{s}`" for s, _ in hits)
            await say(f"PR **#{number}** is open on several projects ({listing}) — which one? "
                      f"Say _\"close PR {number} on <project>\"_.")
            return True
        slug, repo_url = hits[0]
        res = await close_pull_request(self.github, repo_url, number,
                                       api_base=self.s.github_api_base,
                                       transport=self._gh_transport)
        await self.audit.log("pr_closed", payload={"repo": repo_url, "pr": number, "ok": res.ok,
                                                   "by": "operator-nl"})
        await say(("✅ " if res.ok else "⚠️ ") + res.summary
                  + (f" — {res.url}" if res.ok and res.url else ""))
        return True

    async def _nl_branch_delete(self, message: str, channel_id: str, thread_id: str | None) -> bool:
        """Operator-plane branch deletion — IRREVERSIBLE, so it fires ONLY on branches the
        operator explicitly NAMES in a sentence with a delete verb (their words are the §3
        clearance, like "merge it"); `agent/*` branches only (the floor: never a human branch).
        Sentence-scoped extraction so "delete X, Y. The remaining branch Z is current." can never
        touch Z (live 2026-07-05: the PM mapped this ask to `archive` → "Nothing to archive" — an
        empty promise; and the keep-branch was named one sentence later). Unknown names get a
        closest-match suggestion instead of a silent skip. Returns True when handled here."""
        targets: list[str] = []
        for sentence in re.split(r"[.!?\n]", message):
            if re.search(r"\b(?:delete|remove|drop)\b", sentence, re.I) \
                    and re.search(r"\bbranch(?:es)?\b|agent/", sentence, re.I):
                targets += re.findall(r"\bagent/[\w./-]+\b", sentence)
        if not targets:
            return False
        targets = list(dict.fromkeys(t.rstrip("./") for t in targets))

        async def say(msg: str) -> None:
            await self.chat.post(channel_id, msg, thread_id=thread_id)

        if self.github is None or not self.s.github_app_enabled:
            await say("⚠️ I can't manage branches — the GitHub App isn't set up.")
            return True
        # Inventory the actual agent/* branches per onboarded repo (existence + typo suggestions).
        inventory: dict[str, list[str]] = {}
        for p in (await self.projects.list())[:8]:
            try:
                owner, name = parse_owner_repo(p["repo_url"])
                token = await self.github.installation_token()
                async with httpx.AsyncClient(timeout=15.0, transport=self._gh_transport) as c:
                    r = await c.get(f"{self.s.github_api_base.rstrip('/')}/repos/{owner}/{name}/branches",
                                    headers={"Authorization": f"token {token}",
                                             "Accept": "application/vnd.github+json"},
                                    params={"per_page": 100})
                if r.status_code == 200:
                    inventory[p["repo_url"]] = [b["name"] for b in r.json()
                                                if (b.get("name") or "").startswith("agent/")]
            except Exception as exc:  # noqa: BLE001
                log.debug("branch inventory for %s failed: %s", p["slug"], exc)
        lines: list[str] = []
        all_names = sorted({b for bs in inventory.values() for b in bs})
        for t in targets:
            hit_repos = [repo for repo, bs in inventory.items() if t in bs]
            if not hit_repos:
                import difflib
                close = difflib.get_close_matches(t, all_names, n=1, cutoff=0.75)
                lines.append(f"⚠️ `{t}` — not found on any onboarded repo"
                             + (f"; did you mean `{close[0]}`? (say _\"delete branch {close[0]}\"_)"
                                if close else "") + ".")
                continue
            for repo in hit_repos:   # a composition branch exists on BOTH halves — delete each
                res = await delete_branch(self.github, repo, t,
                                          api_base=self.s.github_api_base,
                                          transport=self._gh_transport)
                await self.audit.log("branch_deleted", payload={
                    "repo": repo, "branch": t, "ok": res.ok,
                    "clearance": message[:200]})
                lines.append(("✅ " if res.ok else "⚠️ ") + res.summary)
        await say("**Branch cleanup** (operator-cleared, `agent/*` only):\n"
                  + "\n".join(lines))
        return True

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
                if res.ok:
                    handoff = await self._d6_handoff(ctx_eid or None, repo_url)
                    lines.append(f"✅ `{slug}`: PR opened + **merged** (you pre-cleared it) — {url}"
                                 + handoff)
                    # RS.2: main moved → refresh this repo's docs in Open Brain (background).
                    self._spawn(self._repo_sync_for_repo_url(repo_url))
                else:
                    lines.append(f"⚠️ `{slug}`: PR opened ({url}) but the merge didn't go through — "
                                 f"{res.summary} It stays open for you.")
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
        if delivery is not None and delivery.no_changes:
            # Read-only/investigation completion: the worker's ANSWER (streamed above in the thread)
            # is the deliverable. No branch, no PR, no D2 — and no scope flag (nothing was meant to
            # change). Honest and DONE.
            self._effort_intent_scope.pop(effort_id, None)
            where = ("**no changes** — the worker confirmed this was a read-only task; its answer "
                     "above in the thread is the deliverable (nothing to publish)")
        elif delivery is not None and delivery.landed:
            sha = f" @ `{delivery.head_sha[:10]}`" if delivery.head_sha else ""
            where = (f"pushed to branch **`{branch}`**{sha} (verified on the remote) — "
                     f"`git fetch origin {branch}` to see it")
            eff_repo = await self._effort_repo(effort_id)
            # ORG BUILD BEFORE ANY PR (operator 2026-07-07: "a PR was still created even though
            # there's a lot more work to be done"): on a composition, the wiring bump + the
            # HOST BUILD run FIRST — a red build opens NO PRs and hands off to the burn-down
            # (which re-enters here on green, org-verified, and the PRs open then).
            wire_note = ""
            if effort_id not in self._composition_managed:
                wire_note = await self._wire_vendored_delivery(effort_id, delivery)
            if effort_id in self._comp_check_failed:
                self._comp_check_failed.discard(effort_id)
                hold = (f"⏳ **{effort_id}**: code landed on `{branch}`{sha}, but the org's own "
                        f"build of the composition is **RED** — **not done, no PR opened**. "
                        f"Burn-down continues autonomously; the PR + merge invite come when it "
                        f"builds green. No action needed.{wire_note}")
                await self.comms.post(Intent.closure, hold, effort_id=effort_id)
                await self.comms.post(Intent.operator_reply, hold,
                                      thread_id=self._mgmt_thread_of(effort_id))
                await self.router.update_effort_card(effort_id, "working")
                return
            # D1: open the PR that makes this delivery VISIBLE; merge stays yours (D4).
            pr_url = await self._open_delivery_pr(
                effort_id, eff_repo, branch, verified_sha=delivery.head_sha)
            if pr_url:
                # D2: the autonomous test series red-gates the merge — run the project's check on
                # the delivered branch BEFORE inviting the merge; red routes back, never forward.
                d2_note, delivery = await self._d2_gate(effort_id, eff_repo, delivery,
                                                        f"merge-{effort_id}")
                gate_open = f"merge-{effort_id}" in self._pending_merge
                invite = (f"\n_`main` only changes when you merge — say **“merge it”** and I'll "
                          f"merge, or merge on GitHub after review._" if gate_open else "")
                where += f"\n📬 **PR opened for review:** {pr_url}{d2_note}{invite}"
                where += await self._sibling_pr_note(eff_repo, branch)
                where += wire_note
                # The operator must never be left asking "what do I DO with this fix?" (live
                # 2026-07-06) — every landed delivery carries exact local apply/verify steps.
                where += await self._apply_note(effort_id, eff_repo, branch)
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
        # PER-ERROR honesty (live 2026-07-06: a delivery fixed ONE leg of a 4-error report and the
        # closure invited a merge as if the issue were resolved — the operator rebuilt and hit the
        # same errors). An error-report effort is DONE only when the worker's ERROR VERDICTS block
        # marks every reported error RESOLVED; explicit NOT RESOLVED (or a missing block on a
        # landed delivery) closes as PARTIAL and the effort stays visible.
        out_text = (result.output or "") if result is not None else ""
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal_text = ""
        partial = False
        if branch and goal_text and "REQUIRED VERIFICATION" in goal_text:
            if re.search(r"\bNOT RESOLVED\b", out_text):
                partial = True
                tail = out_text[out_text.find("ERROR VERDICTS"):][:700] or out_text[-700:]
                iterating = await self._auto_iterate(
                    effort_id, "the worker marked some reported errors NOT RESOLVED", tail)
                scope_note += (
                    "\n\n⚠️ **Partial fix:** some of your reported errors are explicitly "
                    "**NOT RESOLVED** (see the ERROR VERDICTS above). "
                    + ("_Auto-iterating on the rest — no action needed._" if iterating else
                       "_Auto-iteration limit reached — say “re-run it” to continue._")
                )
            elif "ERROR VERDICTS:" not in out_text:
                partial = True
                iterating = await self._auto_iterate(
                    effort_id, "the delivery carried no per-error verdicts (unverified against "
                    "the reported errors)", out_text[-700:])
                scope_note += (
                    "\n\n⚠️ **Unverified against your report:** the worker gave no per-error "
                    "verdicts, so I can't claim your reported errors are resolved. "
                    + ("_Auto-iterating with a verification demand — no action needed._"
                       if iterating else
                       "_Auto-iteration limit reached — say “re-run it” to continue._")
                )
        comp_failed = effort_id in self._comp_check_failed
        self._comp_check_failed.discard(effort_id)
        if comp_failed:
            scope_note += (
                "\n\n❌ **The composition check failed** — the wiring branch does not build, so "
                "this is NOT done and no merge should happen. Say _\"re-run it\"_ and the worker "
                "continues from the failing output above."
            )
        unmet_or_partial = bool(unmet) or partial or comp_failed
        done_word = ("done" if not unmet_or_partial else
                     "partly done — see the scope check" if unmet else
                     "partly done — the composition check failed" if comp_failed else
                     "partly done — not all reported errors verified resolved")
        await self.comms.post(
            Intent.closure,
            f"✅ worker finished (**{done_word}**) — {where}. Merge to `main`/deploy stay "
            f"human-gated.{scope_note}",
            effort_id=effort_id,
        )
        # A scope-unmet or partial effort did its piece but the INTENT is incomplete → mark the
        # card 'needs-attention' and keep it visible (don't silently close the operator's goal).
        await self.router.update_effort_card(
            effort_id, "needs-attention" if unmet_or_partial else "done")
        if not unmet_or_partial:
            await self.gate.set_lifecycle(effort_id, "done")  # drops out of the default /status view
        await self.comms.post(
            Intent.operator_reply,
            f"{'✅' if not unmet_or_partial else '⚠️'} **{effort_id}** finished (**{done_word}**): {head}\n"
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

    async def _reconcile_merge_gates(self) -> None:
        """Drop merge gates whose PR is no longer OPEN on the remote (merged / closed / repo
        cleaned up) — 11 stale gates once buried the ONE real decision behind a wall of dead
        options (operator 2026-07-08: bare `approve` listed 14 items). Batched per repo;
        fail-open — an unreadable remote never drops a gate."""
        if not self._pending_merge or self.github is None or not self.s.github_app_enabled:
            return
        by_repo: dict[str, list[str]] = {}
        for mid, e in list(self._pending_merge.items()):
            by_repo.setdefault((e.get("repo") or "").strip(), []).append(mid)
        for repo, mids in by_repo.items():
            if not repo:
                continue
            nums = await read_open_pr_numbers(
                self.github, repo, api_base=self.s.github_api_base,
                transport=self._gh_transport)
            if nums is None:
                continue   # unreadable → keep everything (fail-open)
            for mid in mids:
                pr = int((self._pending_merge.get(mid) or {}).get("pr_number") or 0)
                if pr and pr not in nums:
                    self._pending_merge.pop(mid, None)
                    await self.pending.delete(mid)
                    await self.audit.log(
                        "merge_gate_pruned",
                        effort_id=(mid[len("merge-"):] if mid.startswith("merge-") else None),
                        payload={"merge_id": mid, "pr": pr, "repo": repo})

    async def _pending_decisions(self) -> list[str]:
        """Every item currently awaiting an explicit operator decision — drafted lifecycle plans
        (P-APL.3), proposed capability actions (P-APL.1), held Stage-3 effort plans (P3.9), and
        efforts frozen on a concern (§3). De-duped, insertion order. Used so a bare `approve`/`abort`
        (no id) can resolve THE single pending item unambiguously instead of erroring with a usage
        string — the operator typed the decision verb explicitly; we only fill an unambiguous
        target. Merge gates are RECONCILED against the remote first (stale ones pruned)."""
        await self._reconcile_merge_gates()
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
            note = ""
            if not on and self._kill_blocked:   # auto-resume what the freeze refused (promised)
                blocked = sorted(self._kill_blocked)
                self._kill_blocked.clear()
                note = " — re-dispatching " + ", ".join(f"`{e}`" for e in blocked)
                if channel_id:
                    self._spawn(self._reengage(blocked, mgmt_channel=channel_id,
                                               mgmt_thread=thread_id, reply_prefix=""))
            await reply(f"✅ kill switch {'engaged — fleet frozen' if on else 'released' + note}")
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
                # BLOCKING decisions (a frozen effort, a held plan) outrank OPTIONAL merge
                # invites: a bare verb means "unblock the work" — never bury the one decision
                # that stalls progress under a wall of "you could also merge X" (operator
                # 2026-07-08: 14 listed items hid the single frozen effort at the end).
                blocking = [c for c in cands if not c.startswith("merge-")]
                if len(cands) == 1:
                    effort_id, note = cands[0], ""
                    await reply(f"_(no id given — resolving the only item awaiting you: `{effort_id}`)_")
                elif len(blocking) == 1:
                    effort_id, note = blocking[0], ""
                    await reply(f"_(no id given — resolving the decision that blocks work: "
                                f"`{effort_id}` — {len(cands) - 1} merge invite(s) still open; "
                                f"say “merge it” when ready)_")
                elif not cands:
                    await reply("nothing's awaiting your approval right now.")
                    return
                elif blocking:
                    listing = " · ".join(f"`{c}`" for c in blocking)
                    await reply(f"{len(blocking)} decisions block work — which? `{cmd} <id>`\n"
                                f"{listing}\n_({len(cands) - len(blocking)} optional merge "
                                f"invite(s) not listed — say “merge it” for those.)_")
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
                    # RS.2: onboarding → the repo's docs become knowledge immediately (background).
                    self._spawn(self._repo_sync(proj["slug"], announce_channel=channel_id,
                                                announce_thread=thread_id))
                except Exception as exc:  # noqa: BLE001
                    await reply(f"⚠️ could not add project: {exc}")
            elif sub == "list":
                ps = await self.projects.list()
                await reply(
                    "**Projects:**\n" + "\n".join(
                        f"- `{p['slug']}` → {p['repo_url']} · token {self._project_token_label(p)}"
                        + (f" · ⑂ upstream `{p['upstream_url']}`" if p.get("upstream_url") else "")
                        + (f" · 🧪 `{p['check_cmd']}`" if p.get("check_cmd") else "")
                        for p in ps
                    )
                    if ps else "no projects yet — `/project add <name> <repo-url>`"
                )
            elif sub == "remove" and len(args) >= 2:
                ok = await self.projects.remove(args[1], actor="operator")
                await self.egress.sync()
                await reply(f"{'✅ removed' if ok else '⚠️ no such'} project `{args[1]}`")
            elif sub == "check" and len(args) >= 2:
                # D2: the project's check/test command — run on every delivered PR branch BEFORE the
                # merge gate; red routes back to the effort. Empty command clears it. Take ONLY the
                # quoted span (or the first line): a pasted error wall after the command must not
                # become part of it (live 2026-07-06: it overflowed the column + crash-looped).
                slug = slugify(args[1])
                cmd_str = self._extract_check_cmd(" ".join(args[2:]))
                ok = await self.projects.set_check(slug, cmd_str)
                if not ok:
                    await reply(f"⚠️ no such project `{slug}`")
                elif cmd_str:
                    await reply(f"🧪 `{slug}` check command set: `{cmd_str}` — I'll run it on every "
                                f"delivered PR branch before inviting a merge (D2); red routes back "
                                f"to the worker.")
                else:
                    await reply(f"🧪 `{slug}` check command cleared — D2 will be skipped (with a note).")
            elif sub == "sync" and len(args) >= 2:
                # RS.2 manual trigger: re-ingest the project's docs into Open Brain sources.
                slug = slugify(args[1])
                if not await self.projects.get(slug):
                    await reply(f"⚠️ no such project `{slug}`")
                else:
                    await reply(f"🧠 syncing `{slug}`'s docs into Open Brain sources — results follow.")
                    self._spawn(self._repo_sync(slug, announce_channel=channel_id,
                                                announce_thread=thread_id))
            else:
                await reply(
                    "usage: `/project add <name> <repo-url> [--upstream <parent-url>] [TOKEN_ENV]` · "
                    "`/project list` · `/project remove <name>` · `/project check <name> \"<cmd>\"` · "
                    "`/project sync <name>` "
                    "_(`--upstream` = the fork PARENT, baked read-only so the worker can fetch it but "
                    "push only to the fork; TOKEN_ENV = the env var holding this repo's PAT; `check` "
                    "= the D2 test command run on PR branches before the merge gate; `sync` = ingest "
                    "the repo's docs into Open Brain sources — or just say “sync <name> docs”)_"
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
