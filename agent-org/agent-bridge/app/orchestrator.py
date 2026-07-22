"""orchestrator — wires the SRP modules into the running bridge (PLAN §3.1.1/§5.1).

The orchestrator is thin glue: it owns no safety logic itself (that lives in the modules)
but it composes the flow — post CONCERNs to #mgmt, parse operator decisions, run the
sampled monitor, and route inbound chat events to wakes/decisions. Keeping it thin is the
"Thinnest Viable Platform" discipline: features must not accrete into the brake (§3.1.1).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import shlex
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
    read_default_branch_head,
    read_merge_base,
    sha_is_ancestor,
    classify_agent_branches,
    close_pull_request,
    delete_branch,
    read_added_lines,
    read_removal_summary,
    read_branch_delivery,
    read_broken_gitlinks,
    read_sibling_agent_prs,
    read_repo_state,
    merge_branch,
    ensure_branch,
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

from .models import (
    Concern as ConcernRow,
    Effort, EffortConstraint, Event, GlobalState, LensReport, Project, ScopeNode, ScopeTask,
    WorkerInstance,
)
from .modules.pending_store import PendingStore
from .schemas import (
    Concern, ConcernOption, Decision, Level, LifecyclePlan, LifecycleStep, MonitorVerdict, OperatorIntent, Plan, Trigger,
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
# P17 F14 — a STOP instruction that misses the strict grammar must never be silently swallowed.
# Live 2026-07-20: `POST /nl` with "Stop and abort effort-gym-015-todo-product. The gym diagnostic
# run is complete and I do not want any further rounds, dispatches or pushes on it." returned
# {"ok": true} and did NOTHING — `_CONTROL_RE` is anchored at `^` and the message opens with
# "Stop and", so it fell through to the PO model, which took no action and logged no event. The
# effort ran another full drain round seven minutes later. The terse "archive <id>" worked. The
# more explicit, more human phrasing lost, which is the wrong way round for a stop.
# Deliberately NOT auto-aborting on this looser shape: acting on a fuzzy stop is its own hazard
# ("don't stop effort-x"). It ASKS, which is the one thing the silent path never did.
# The id must not swallow sentence punctuation: `[\w.-]+` is greedy and captured the trailing
# period of "…abort effort-x." as part of the id, so the lookup missed and the guard silently
# did nothing — the very failure it exists to prevent. Ends on a word char or hyphen.
_STOP_INTENT_RE = re.compile(
    r"\b(stop|halt|abort|archive|cancel|shut\s+down|kill)\b[\s\S]{0,120}?"
    r"\b(effort-[\w.-]*[\w-])", re.I)
# P19 F14-refinement — a CLEAN stop COMMAND: the verb LEADS and an effort id follows, and that is
# the whole message (a trailing period is fine). `archive <id>` was a valid PO command before F14
# shipped (it stopped gym-015); F14 then diverted it to the "use abort" ask because it isn't in
# `_CONTROL_RE`. This shape is unambiguous — route it straight to the abort handler. Anything more
# elaborate ("Stop and abort effort-x. The run is complete …") does NOT match and still reaches the
# ASK below, which is where a fuzzy stop belongs. Negations ("do not stop effort-x") lead with a
# different word and never match. `abort <id>` itself is already handled by `_CONTROL_RE` upstream.
_STOP_COMMAND_RE = re.compile(
    r"^\s*(?:stop|halt|archive|cancel|shut\s+down)\s+(?P<eid>effort-[\w.-]*[\w-])\s*\.?\s*$", re.I)

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
# UNAMBIGUOUS re-run/reopen command verbs. When one of these applies to an explicitly NAMED effort
# ("re-run effort-X", "reopen effort-X and re-verify …") the intent is beyond doubt — the small
# model must not get a vote (live 2026-07-11: a verbose "re-run effort-…, it was closed …, reopen
# it and surface …" classified as `archive`/tidy — the cleanup-adjacent words "closed/reopen/
# surface" outweighed the command — and it dispatched the WRONG effort). Project-agnostic.
_RERUN_VERB_RE = re.compile(
    r"\b(?:re-?run|re-?engage|re-?dispatch|re-?verify|re-?open|reopen|resume|"
    r"run\s+(?:it|them|that)\s+again|try\s+(?:it|that)?\s*again|pick\s+(?:it|that)\s+back\s+up)\b",
    re.I)

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


def _now_iso() -> str:
    """UTC now, ISO — stamps `asked_at` on human-gate parks (P8 #2 waiting-on state)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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


# A check FAILURE that is the CHECK's own environment/setup breaking — NOT the delivered code.
# You can't burn these down by editing source; they mean the check or workspace is misconfigured
# (fix the check/env, or hand off), so the org must not read them as code errors (2026-07-10).
_INFRA_FAIL_RES = [
    re.compile(r"git-proxy:\s*DENIED", re.I),
    re.compile(r"MSB1009|project file does not exist", re.I),
    re.compile(r"could(?:\s+not|n'?t)\s+find\s+a\s+project\s+to\s+run", re.I),
    re.compile(r"clone\s+failed|fatal:\s+could\s+not\s+read|authentication\s+failed", re.I),
    re.compile(r"no\s+such\s+file\s+or\s+directory", re.I),
    re.compile(r"command\s+not\s+found|:\s*not\s+found\b|is\s+not\s+recognized", re.I),
    re.compile(r"permission\s+denied", re.I),
    re.compile(r"unable\s+to\s+access|could\s+not\s+resolve\s+host|network\s+is\s+unreachable", re.I),
    re.compile(r"no\s+space\s+left\s+on\s+device|disk\s+quota\s+exceeded", re.I),
    re.compile(r"submodule\b[^.\n]*\b(?:denied|failed|not\s+initialized)", re.I),
]


# A genuine SOURCE-code error carries a file+line locus (`Foo.cs(12,5): error CS1503:`); an
# MSB/tool-level error (MSB1009, "command not found") does not. That locus is how we tell "the
# code is broken" from "the check's environment is broken".
_SOURCE_ERROR_RE = re.compile(r"\S+\.[A-Za-z]{1,6}\(\d+(?:,\d+)?\):\s*(?:error|fatal)\b", re.I)

# MSBuild ENVIRONMENT errors — a missing referenced project / import / SDK / reference-assembly.
# These carry a `<buildfile>(line): error MSBxxxx` locus (e.g. `NuGet.targets(465,5): error MSB3202`)
# that LOOKS like a source-code locus but is the BUILD SYSTEM failing to SET UP — the build never
# reached (never compiled) the delivered code, so there is no code error to grind (live 2026-07-12:
# a composition build hit MSB3202 because a vendored NESTED submodule wasn't populated — a
# workspace/focus problem the worker can't fix, but it read as a code error and burned down rounds).
# Infra unconditionally, so this class is surfaced honestly, never sent to the code burn-down.
_MSBUILD_ENV_RE = re.compile(r"\berror\s+MSB(?:1009|3202|3644|4019|4025|4236)\b", re.I)


def _is_infra_failure(output: str) -> bool:
    """True when a red check is the CHECK's OWN infrastructure failing (proxy/clone/tool/path),
    not the delivered code. An infra signature must be present AND there must be no genuine
    SOURCE-code error (a `file.ext(line): error` locus) — a real build error means the code IS
    broken; treat it as code even if some infra-ish noise is also present."""
    if not output:
        return False
    # An MSBuild environment error (missing referenced project/import/SDK) is infra even though its
    # `NuGet.targets(line): error MSBxxxx` locus mimics a source-code locus — the build never reached
    # the delivered code, so there is no code error to burn down.
    if _MSBUILD_ENV_RE.search(output):
        return True
    if not any(rx.search(output) for rx in _INFRA_FAIL_RES):
        return False
    return not _SOURCE_ERROR_RE.search(output)


# A monitor/PM CONCERN whose subject is an ENVIRONMENT/WORKSPACE symptom — the org can self-heal it
# by re-cloning + retrying (operator-authorized autonomous recovery, 2026-07-13). Deliberately
# SPECIFIC (a missing `.git`, an unpopulated clone, an uninitialised submodule, "reset the repository
# setup") so it never fires on a real code/behaviour deviation, which must still reach the human.
_INFRA_CONCERN_RES = [re.compile(r, re.I) for r in (
    r"\bnot a git repo",
    r"\bno\b[^.\n]{0,15}\.git\b|\.git\b[^.\n]{0,15}\b(missing|absent|gone|no longer)",
    r"\bonly\b[^.\n]{0,25}\b(compiled|build)\b[^.\n]{0,15}\bartifact",       # "only compiled artifacts"
    r"\b(re-?set|reset|re-?clone|re-?establish|repair)\b[^.\n]{0,30}\brepositor",
    r"\bclone\b[^.\n]{0,25}\b(missing|failed|absent|incomplete|corrupt|empty)",
    r"\bsubmodul\w*\b[^.\n]{0,35}\b(not|un)[- ]?initiali",                    # submodule(s) not initialised
    r"\brepository setup\b",
    r"\bworkspace\b[^.\n]{0,45}\b(empty|void|corrupt|missing|no (repo|clone|source|git)|"
    r"only .{0,15}artifact|not .{0,10}clon|deviat|environment)",
)]

# Signs of a GENUINE work-deviation — if present, the concern is NOT auto-cleared even if some
# infra-ish words also appear (a real problem must never be masked by the autonomous recovery).
_REAL_DEVIATION_RE = re.compile(
    r"\b(remov|delet|dropp?|strip)\w*\b[^.\n]{0,30}\b(feature|functionality|code|test|method|class|logic)"
    r"|does ?n[o']?t match|revert(ed|ing)?\b|regress|wrong (output|behaviou?r|result|answer)"
    r"|violat\w* the (spec|intent|standing|charter)|gaming|faked?\b|hard-?cod",
    re.I,
)


def _is_infra_concern(text: str) -> bool:
    """True when a FROZEN effort's concern is an environment/workspace symptom the org can self-heal
    (re-clone + retry) rather than a real code/behaviour deviation that needs the Human Operator.
    Conservative: an infra signature must be present AND no genuine-deviation signature."""
    if not text:
        return False
    if _REAL_DEVIATION_RE.search(text):
        return False
    return any(rx.search(text) for rx in _INFRA_CONCERN_RES)


# Cross-effort DEBUG HANDOFF marker (operator 2026-07-14): a worker BLOCKED by a bug in code
# OUTSIDE its project reports `HANDOFF: <path or project> :: <one-line summary>` followed by the
# debug log, instead of working around it or editing foreign code. The protocol clause's own
# template line carries angle brackets, so an echoed instruction never parses as a real request.
_HANDOFF_LINE_RE = re.compile(r"^[ \t>*-]*HANDOFF:\s*(?P<rest>\S[^\n]*)$", re.M)


def _parse_handoff(output: str) -> dict | None:
    """The worker's handoff request as {'target', 'summary', 'log'}, or None. The log is
    everything from the marker line on (the worker pastes the error output right after it)."""
    if not output or "HANDOFF:" not in output:
        return None
    m = _HANDOFF_LINE_RE.search(output)
    if not m:
        return None
    target, _, summary = m.group("rest").strip().partition("::")
    target = target.strip().strip("`'\"")
    if not target or "<" in target or ">" in target:
        return None            # the template line from an echoed instruction, not a real request
    return {"target": target[:200], "summary": summary.strip()[:300],
            "log": output[m.start():][:2600]}


# The little-coder daemon's flail-guard answer marker: the turn was KILLED for reading without
# ever editing (operator 2026-07-14) — the bridge forks a fresh session and re-plans.
_FLAIL_MARKER = "FLAIL-GUARD:"

# P8 #3 — the worker's honest-stop marker when its checkout fails the base assertion below.
_STALE_MARKER = "WORKSPACE STALE:"

# P8 #3 — PROVENANCE clause injected into the FIRST coding step when the org knows the expected
# base (2026-07-16 gym: both workers ran days-old checkouts rooted on dead history — every branch
# they pushed had no common ancestor with the live main, so nothing could ever be delivered, and
# nobody noticed until a human diffed the audit). The worker cannot discover the live base itself
# (its git egress is proxied — `git fetch` can't reach the forge), so the org HANDS it the base
# and demands an assert-before-work: an ancestry check, not a HEAD equality, so a re-engaged
# workspace that already carries this effort's own commits still passes.
_PROVENANCE_CLAUSE = (
    "\n\nWORKSPACE PROVENANCE — run this check FIRST, before any other work: this task is planned "
    "against base commit `{sha}` (the current `{branch}` head; your clone should be rooted on "
    "it). Verify:\n"
    "  git rev-list -n 500 HEAD | grep -q ^{sha} && echo BASE-OK || echo BASE-MISMATCH\n"
    "BASE-OK → proceed with the task. BASE-MISMATCH → your workspace is STALE (cloned from dead "
    "history — anything built on it is undeliverable): STOP immediately, change NOTHING, and "
    "reply exactly `WORKSPACE STALE: HEAD not rooted on {sha12}` so I can re-clone you fresh."
)

# POST-DELIVERY QA report parsing (operator 2026-07-15). The QA agent replies with WORKS/DEFECTS/
# FOLLOWUPS/VERDICT sections; these pull one section's block and split it into list items.
_QA_HEADERS = ("WORKS", "DEFECTS", "FOLLOWUPS", "VERDICT")


def _qa_block(out: str, header: str) -> str:
    """The text following `HEADER:` up to the next known QA header (or end)."""
    m = re.search(
        rf"^\s*{header}\s*:\s*(.*?)(?=^\s*(?:{'|'.join(_QA_HEADERS)})\s*:|\Z)",
        out or "", re.I | re.M | re.S)
    return m.group(1).strip() if m else ""


def _qa_items(block: str) -> list[str]:
    """Split a QA section block into cleaned list items; empty for an explicit 'none'."""
    if not block or re.fullmatch(r"none\.?", block.strip(), re.I):
        return []
    items: list[str] = []
    for ln in block.splitlines():
        ln = re.sub(r"^[\-\*\d.)\s]+", "", ln.strip()).strip()
        if ln and not re.fullmatch(r"none\.?", ln, re.I):
            items.append(ln[:200])
    return items[:12]

# ── P10.1 THE THREE STANDING LENSES (ORCHESTRATION-DESIGN §6.5) ──────────────────────────────
# The operator's own PR-review prompts, VERBATIM — proven by hand on the gym deliveries the org
# had already declared clean. They replace the single graded QA instruction, which was producing
# FALSE GREENS: it literally said "Say `none` ONLY if…", and gym-008's functional lens duly
# returned "no defects" on a codebase where the code-review lens found real ones and an operator
# review of a comparable product found 5 bugs + 3 gaps. A prompt that sanctions "nothing" gets
# told "nothing".
#
# Two structural rules make these lenses OBJECTIVE, and both are acceptance criteria:
#
#   1. NO "NOTHING" AFFORDANCE and NO VERDICT FRAMING. No "say none if none", no "grade to a bar".
#      A lens OBSERVES and REPORTS; it never adjudicates. Whether the scope is done is decided by
#      COUNTING what the sweep propagates (P10.4), never by asking a model for a verdict.
#   2. THE GOAL IS WITHHELD from `goal_alignment`. This is the debias, not an oversight: a goal in
#      an observation prompt invites the model to reason *toward* it and declare it met. The report
#      is compared to the goal in P10.2 — a separate step, after the observation is already fixed.
#
# All three run FRESH every round and are never fed a previous report (see `LensReport`).
_LENS_GOAL_ALIGNMENT = (
    "test the codebase thoroughly treating as a final product, checking each function, find gaps "
    "in the solution for the problem the codebase is attempting to solve and write a short report. "
    "Do not edit files in this codebase, this is just evaluative."
)
_LENS_CLEAN_CODE = (
    "evaluate the codebase code cleanliness, is the code practicing SOLID, industry standard "
    "programming patterns, clear naming conventions and does the code support good documentation? "
    "How does or doesn't this codebase support documentation for its code?"
)
_LENS_PROJECT_DOCUMENTATION = (
    "evaluate the comments in the git repo through its history here. Are the titles and "
    "descriptions clear with intent focused and enough to grasp an evolving projects history? how "
    "does is the information helpful and how could the information be better written for you to be "
    "able to pick up the project where it left off?"
)
# The KEY of the only goal-aware lens. Named because it is load-bearing in two coupled places:
# `_gap_analysis` consumes this report and nothing else, and `_drain_round` refuses to call a round
# "swept" without it (P17 F3). A sweep that loses this lens has not compared the product to the
# goal, however many other lenses reported.
_LENS_GOAL_ALIGNMENT_KEY = "goal_alignment"
# P18 F4 — where a lens appends findings as it establishes them, so a turn that dies mid-sweep
# still yields what it had found. Outside the repo tree (`/tmp`, not `/workspace`) so it can never
# be committed by a later turn or show up in a diff.
_LENS_FINDINGS_PATH = "/tmp/lens-findings.txt"
# Order is the sweep order; `goal_alignment` runs first because P10.2 consumes its report.
_LENSES: tuple[tuple[str, str], ...] = (
    (_LENS_GOAL_ALIGNMENT_KEY, _LENS_GOAL_ALIGNMENT),
    ("clean_code", _LENS_CLEAN_CODE),
    ("project_documentation", _LENS_PROJECT_DOCUMENTATION),
)


# An explicit "nothing to do" in any of the shapes a small model actually writes it. This is the
# COUNTABLE ZERO the whole termination rule rests on, so it must not be defeated by punctuation or
# a trailing word ("none.", "None found", "- none").
# Structural words that appear in almost every scope title and so carry no ownership information.
_SCOPE_STOPWORDS = frozenset({
    "layer", "module", "component", "system", "service", "part", "area", "code", "scope",
    "feature", "support", "handling", "logic", "core", "main", "base", "misc",
})
# P11.5 — A VANISHED REPORT IS NOT AN EMPTY ONE. In gym-009 the `goal_alignment` lens produced no
# report in 3 of 3 rounds: its turn ended mid-flight and the last thing it had emitted was a
# narration line ("All 44 tests pass. Now let me do manual CLI testing to probe edge cases." — 72
# chars). That was stored as the round's report and fed to gap analysis, which correctly concluded
# a 72-char stub evidences none of a 5417-char goal and manufactured 12 tasks demanding features the
# product had already shipped. P10's `swept = bool(reports)` defends the ZERO and leaves the
# NEAR-zero wide open — so a body has to clear a substance floor and not read as narration.
_LENS_MIN_REPORT_CHARS = 400
_NARRATION_RE = re.compile(
    r"^\s*(?:ok|okay|good|great|alright)?[\s,.—-]*"
    r"(?:all \d+ tests? pass|tests? pass|the tests? pass)?[\s,.—-]*"
    r"(?:now )?(?:let me|let's|i'll|i will|i'm going to|next,? i)\b",
    re.I)


def _is_lens_report(body: str) -> bool:
    """Is this a REPORT, or a turn that ended before writing one? A lens that genuinely found
    nothing still writes prose saying so; a lens whose turn died mid-flight leaves its last
    narration line. Treating the second as the first is how an accident of a truncated reply
    becomes a confident, wrong work list."""
    body = (body or "").strip()
    if len(body) < _LENS_MIN_REPORT_CHARS:
        return False
    # A report that opens by ANNOUNCING work it never got to is a preamble, however long.
    return not _NARRATION_RE.match(body)


def _is_plan_reply(body: str) -> bool:
    """Is this a PLAN, or a turn that ended before writing one? (P13.3)

    The same discipline as `_is_lens_report`, but a plan is legitimately much shorter than a lens
    report, so the lens's length floor would re-ask perfectly good plans. A plan is identified by
    STRUCTURE first — the sections the gate asks for — and only falls back to length.

    gym-011: the worker had implemented, tested and committed its work; the turn's final output was
    `"Final test run and commit:"`. The gate adjudicated that fragment, called it "severely
    incomplete", and rejected finished work three times until the effort blocked."""
    body = (body or "").strip()
    if not body:
        return False
    if re.search(r"^\s*(?:UNDERSTANDING|PLAN|WON'?T DO|RISKS)\s*:", body, re.I | re.M):
        return True          # it has the requested structure — judge it on the merits
    if _NARRATION_RE.match(body):
        return False         # "now let me…", "final test run and commit:" — a preamble
    return len(body) >= 200  # unstructured, but substantial enough to be a real answer


_NOTHING_RE = re.compile(r"\s*(?:none|nothing|n/a)\b[\s.!]*(?:found|identified|required|needed)?[\s.!]*",
                         re.I)
# Prose that ASSERTS there is no work, rather than naming work. Such a line must never become a
# task: a task is content-addressed, so re-worded commentary would count as NEW every round and the
# propagation count could never reach zero.
_NO_WORK_RE = re.compile(
    r"\b(?:no|zero)\s+(?:\w+\s+){0,2}(?:issues?|problems?|defects?|gaps?|shortcomings?|changes?|"
    r"gap|work|gaps? (?:were|was) (?:found|identified))\b"
    r"|\b(?:nothing|no work)\s+(?:to\s+(?:do|change|fix)|(?:is\s+)?(?:required|needed|outstanding))\b"
    r"|\b(?:looks?|reads?|is)\s+(?:fine|clean|good|complete|solid)\b",
    re.I)


def _plain_tasks(text: str, *, limit: int = 12) -> list[str]:
    """Split a model's task list into PLAINLY-STATED task bodies.

    Rationale is stripped, not preserved: a task handed to a small model must be a legitimate,
    relevant, plainly stated unit of work, because a small model reasons worse than a frontier one
    and a "why" chain is an invitation to re-litigate the task instead of doing it. The reasoning
    that produced the task survives in the `LensReport` audit trail. An explicit "none" yields [],
    which is the honest zero the propagation count needs."""
    text = (text or "").strip()
    if not text or _NOTHING_RE.fullmatch(text):
        return []
    out: list[str] = []
    for ln in text.splitlines():
        # Strip a list marker — but ONLY a real one. A bare `[\d.)]+` prefix class eats the leading
        # digits of real text ("2FA login support" → "FA login support"), so a numbered marker must
        # be followed by its punctuation and a space.
        ln = re.sub(r"^\s*(?:[-*•]+\s*|\d+[.)]\s+)", "", ln.strip()).strip()
        if not ln or _NOTHING_RE.fullmatch(ln):
            continue
        # P18 F17 — a `REPRO:` line BELONGS TO the task above it; it is evidence, not work. Left
        # alone it becomes its own content-addressed task ("REPRO: python3 todo.py add ...") and
        # gets dispatched to a worker, which is worse than the fabricated task F17 exists to stop.
        # Folding it into the preceding body also keeps the reproduction where `_drop_false_defects`
        # looks for it.
        if ln.upper().startswith("REPRO:"):
            if out:
                out[-1] = f"{out[-1]}\n{ln}"
            continue
        # A model asked for a list still emits headers and commentary. Left unfiltered, a line like
        # "The codebase is fine, no issues were found." becomes a content-addressed TASK that is
        # NEW every time the wording drifts — which would block the propagation count from ever
        # reaching zero, i.e. break termination itself. Drop headers, and drop prose that ASSERTS
        # there is nothing to do rather than naming work.
        if ln.endswith(":") and len(ln.split()) <= 4:
            continue
        if _NO_WORK_RE.search(ln):
            continue
        # Drop a trailing rationale clause — the task is the WORK, not the argument for it.
        # Deliberately does NOT include "since": it is far more often temporal than causal in a
        # task body ("list todos since a given date"), and truncating a real task is a worse
        # failure than leaving one rationale clause attached.
        ln = re.split(r"\s+(?:because|so that|in order to|as this|which is why)\b", ln,
                      maxsplit=1, flags=re.I)[0].strip(" .;,–—-")
        if ln:
            out.append(ln[:300])
    return out[:limit]


# NOTE (operator 2026-07-14, after two live false-positive generations): "the plan inclusion
# needs REASONING, not determinism." A plan is PROSE about intent — a bare substring match
# rejected honest plans for naming the very term they were REMOVING, and a context-regex is just
# a longer keyword list. Plan judgment is therefore the PM LLM lens's job alone (fed the standing
# intent + forbidden terms as reasoning context); determinism stays where the surface IS
# deterministic — the delivery gates on actual diffs (added lines, file deletions, exit codes).


def _is_transient_focus_collision(detail: str) -> bool:
    """A verify-focus (privileged recursive re-clone) that TRANSIENTLY collided on a workspace the
    scheduler handed it while it still held a prior/parked clone — the `git clone` exit-128
    'destination path already exists and is not an empty directory', or a generic 'verification
    focus failed' clone error. Distinct from a persistent failure: worth ONE deterministic retry
    (a fresh focus re-wipes clean) before dropping to the LLM verifier. Generic string match, not
    tool-specific."""
    d = (detail or "").lower()
    return (("already exists" in d and ("not an empty" in d or "not empty" in d or "destination" in d))
            or ("verification focus failed" in d and "clone" in d))


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
        # runtime failure — no compiler-style lines; the tail says what actually happened
        tail = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
        return ("runtime failure — log tail: " + tail[-1][:140]) if tail             else "no machine-readable errors in the log"
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

# For a RUNTIME/BEHAVIORAL goal (a symptom you only see by RUNNING + using the program), a green
# build proves nothing (operator 2026-07-10: "90% of the time when I test what was claimed, the
# claim is false" — because the check compiles, it doesn't RUN the behavior). The trust fix: the
# fix must be PROVEN by an automated reproduction that FAILS on the broken code and PASSES on the
# fix, wired into the check so it can never silently regress. This is what makes "done" mean the
# GOAL is met, not "it compiles" — and it makes cheating fail (you can't hollow out the code and
# still pass a test that exercises the real path).
_REPRO_CLAUSE = (
    "\n\nTHIS IS A RUNTIME / BEHAVIORAL SYMPTOM — something seen only by RUNNING the program, not "
    "by compiling it. A build that COMPILES is NOT done, and a fix that merely 'looks right' proves "
    "nothing. Prove it the only trustworthy way:\n"
    "1. REPRODUCE the symptom as an AUTOMATED TEST that exercises the REAL code path that fails — "
    "the actual load/init/operation (e.g. load the resource the way the failing action does), NOT "
    "a simulated mouse-click. Run it against the CURRENT code and confirm it FAILS — that proves "
    "the test genuinely catches the symptom.\n"
    "2. FIX the code so the test PASSES — WITHOUT weakening, stubbing, or deleting the test or the "
    "behavior it checks. (Hollowing out code to pass a build is exactly the failure this catches.)\n"
    "3. WIRE the test into the project's check/build so it RUNS every time — a permanent regression "
    "guard. If there is no test harness, add the smallest runnable one.\n"
    "4. End your report with a block starting exactly `REPRO:` — then these lines:\n"
    "   `EXERCISES:` <what real path the test drives>\n"
    "   `BEFORE:` FAIL — <the failing run's evidence on the unfixed code>\n"
    "   `AFTER:` PASS — <the passing run's evidence after the fix>\n"
    "   `WIRED:` <exactly how the check now runs this test>\n"
    "If part of the symptom genuinely CANNOT be exercised without a human (pure visual correctness, "
    "real-GPU rendering, a physical display), add `UNAUTOMATABLE:` <exactly what needs a human eye> "
    "and automate everything around it — NEVER fake a passing test to look done."
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


# Minimal built-in fallback for the PM communication voice (used only if charters/pm-voice.md is
# missing on a bad deploy — the file is the source of truth + the operator-tunable surface).
_PM_VOICE_FALLBACK_SYS = (
    "You are the PM speaking directly to the human operator — a sharp engineer who would rather "
    "talk to you than read a dashboard. Make them UNDERSTAND what's happening, what it means, and "
    "what (if anything) they need to do. Communicate only what the GROUND-TRUTH FACTS support; "
    "never invent a branch, SHA, PR number, count, or state, and never soften an honest caveat "
    "into false confidence (a green build is not a verified runtime fix). Lead with what matters "
    "most; separate clearly what's handled from what needs the operator; explain the WHY; be "
    "honest about what you couldn't verify and offer the next step. Concise, direct, technical, "
    "no corporate padding. Markdown. A one-line question gets a one-line answer."
)

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
        # P8 #3 — PROVENANCE: the expected BASE per effort, read from the remote at dispatch
        # ({effort_id: {"branch": default_branch, "sha": head_sha}}). Handed to the worker in the
        # brief to ASSERT against (it can't discover it — proxied git), keys workspace reuse in the
        # router, and is stamped on effort_published / delivery_pr_opened so no claim exists
        # without the base it was made against (2026-07-16 gym: days-stale checkouts pushed
        # branches with no common ancestor to the live main → compare 404 → PR 422 → hollow done).
        self._expected_base: dict[str, dict] = {}
        # Evolved goals queued by a machine-detected failure (red check / unresolved verdicts) —
        # launched by delegate's finally the moment the current run closes (auto-iteration).
        self._iterate_after: dict[str, str] = {}
        # BURN-DOWN (operator 2026-07-07: "all 138 errors should have been worked through
        # autonomously and not elevated in the first place"): a RED org-run build doesn't stop at
        # a fixed retry count — it starts a progress-based loop that keeps dispatching fix rounds
        # while the error count still falls. Failing logs queued here by paths inside delegate
        # are launched by delegate's finally (single-flight-safe).
        self._burndown_after: dict[str, str] = {}
        # Burn-down RESEARCH-on-stall (operator 2026-07-12: "the workers and pm should be able to
        # research the error" — a stall that punts "answer the open question" with no question is
        # useless). `_burndown_researched`: efforts that already spent this burn-down's one research
        # round (bounded, no research spam). `_burndown_research_note`: the grounded findings, so a
        # still-stalled escalation carries them (actionable, not a vague question).
        self._burndown_researched: set[str] = set()
        self._burndown_research_note: dict[str, str] = {}
        # Efforts whose standalone run verifiably delivered NOTHING on a vendored+checked
        # project — delegate's finally re-runs them in the HOST context (2026-07-09).
        self._route_host_after: set[str] = set()
        # effort -> the CURRENT round's failing log (full, in-memory) — burn-down wakes read
        # the tail from here when the failure has no parseable error lines (runtime crashes).
        self._last_burn_log: dict[str, str] = {}
        # effort → branch head the ORG itself verified green (its own build run + log, not a
        # worker's word) — the finish path skips a duplicate composition check and the closure is
        # labelled "org-verified". Cleared on every fresh dispatch.
        self._org_verified: dict[str, str] = {}
        # RUNTIME-SYMPTOM RED→GREEN (dark-factory 2026-07-12, the atlas false-done): maps effort_id →
        # the head_sha for which the ORG ITSELF observed a symptom reproduction fail on the pre-fix
        # state and pass on the fix. This is the ONLY basis on which "verified via reproduction" may be
        # claimed for a runtime/interaction symptom — a green BUILD/SMOKE (which passes whether or not
        # the interaction bug is present) is NOT a reproduction. Set exclusively by the before/after
        # harness; absent ⇒ the org has not independently proven the symptom ⇒ honest needs-attention.
        # Cleared on every fresh dispatch alongside `_org_verified`.
        self._repro_red_green: dict[str, str] = {}
        # Monotonic counter for BUILD-VERIFICATION sessions (org build check / composition check):
        # these MUST run in a FRESH, isolated session — reusing the effort's work session made the
        # little-coder agent no-op (live 2026-07-08: the org build check ran 0 commands, returned
        # empty → verdict "unknown" → the burn-down never engaged). A stateless "clone, build,
        # report" task must not inherit the port work's conversational context.
        # Session-number namespace, seeded from the BOOT CLOCK so a restart can never RE-ISSUE a
        # session id that a previous boot already used. Live 2026-07-13: a restart reset this to 0,
        # the next dispatch re-issued `~host1`, the daemon re-focused THAT session's already-BLOATED
        # 646KB journal, the prompt overflowed the model's context window, qwen returned EMPTY
        # completions, and the agent no-op'd (0 commands, idle GPU). Monotonic within a boot;
        # disjoint across boots — so a stale, bloated session is never inherited again.
        self._verify_seq = int(time.time()) % 1_000_000
        # Advisory research jobs IN FLIGHT (transparency: PM work must be visible) — shown in
        # /status; updated by the state-driven poll's progress callback. {key: {question, state,
        # started}}. In-memory only (a restart orphans the job on the engine side, harmlessly).
        self._advisories: dict[str, dict] = {}
        # Cross-effort A→B DEBUG HANDOFFS in flight (operator 2026-07-14): fix-effort id → the
        # REPORTING effort waiting on it ({'from', 'target', 'escalated'}). In-memory; the durable
        # trail is the handoff_* audit events. A restart drops the link harmlessly: the reporter is
        # eventually re-engaged by the stall watchdog and re-raises the handoff if the bug still
        # bites — bounded by `handoff_cap`, which IS restart-safe (an event count).
        self._handoff_by_fix: dict[str, dict] = {}
        # Reporting efforts paused on a handed-off fix — the stall watchdog must not re-engage
        # them mid-wait (their resume comes from the fix effort's clean finish, or the operator).
        self._handoff_waiting: set[str] = set()
        # Efforts whose NEXT dispatch must plan first regardless of risk mode — set by the flail
        # guard (a killed read-without-edit turn re-enters through the plan gate) and consumed
        # one-shot by _worker_plan_required.
        self._force_plan: set[str] = set()
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
        self._stall_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._draining = False
        self._last_backpressure = 0.0   # monotonic ts of the last shed (source-guard window)
        # Efforts with a LIVE delegate task right now (actively being executed). This is the honest
        # "work is happening" signal — distinct from the gate state `active` (= merely not-frozen),
        # which persists forever and misleads the PM into reporting a phantom queue.
        self._delegating: set[str] = set()
        # WORKER LIVENESS (register #25): {base_url: (task_id, last_offset, last_progress_at)} — the
        # last per-agent-step event offset the stall sweep observed for each worker's running task, and
        # WHEN. The offset climbing = alive; frozen past `worker_silence_s` = hung. Empty after a
        # restart (we can't know pre-restart silence, so the clock starts fresh — the safe default).
        self._worker_progress: dict[str, tuple[str, int, datetime]] = {}

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

    # An effort whose LATEST event is one of these was dispatched but hasn't reached any resolution
    # (a PR opened / burn-down / escalation / a surfaced state). If it then goes SILENT, it's wedged —
    # a focus that failed, a delegate that died mid-flight — not something correctly awaiting you.
    # `effort_published` is included (live 2026-07-11): a delivery that PUBLISHED a branch but whose
    # verify→PR→closure then STALLED (silent, no worker running) is a wedge the org must recover —
    # publishing is not the finish line. The busy-check keeps this from disturbing an in-flight verify.
    _STALL_MIDDISPATCH_KINDS = frozenset({
        "worker_acquire", "worker_project_set", "worker_release", "goal_change",
        "readiness_gate", "effort_risk_set", "effort_reopened", "worker_resumed",
        "worker_waiting", "focus_failed", "effort_published",
        # The DRY-RUN / worker-plan mid-pipeline (2026-07-16 gym: an effort sat mid-pipeline with the
        # GPU idle because the watchdog treated these as "surfaced states awaiting the operator" and
        # never re-engaged). These states AUTO-ADVANCE to dispatch, so going quiet past the threshold
        # means genuinely stuck, not waiting on a human — the safety net must cover them.
        # DELIBERATELY EXCLUDED: `plan_drafted` / `lifecycle_plan_drafted` are the Stage-3 PLAN
        # APPROVAL gate (P3.9) — an effort parked there is CORRECTLY awaiting the operator's
        # `approve <effort>`, and auto-re-engaging it would bypass a human governance gate (§4.5).
        # A quiet plan gate is the system working, not a stall.
        "dry_run_started", "dry_run_recorded", "dry_run_auto_isolated", "worker_plan_approved",
        # LIVE GAP (gym-008, 2026-07-18): a worker turn that ends `abandoned` — or whose
        # post-turn handling dies — leaves `wake_done` as the effort's last event. Without it
        # here the kind-gate skipped the effort: it sat open+active, worker idle, GPU idle for
        # 31 min, and would have stranded FOREVER, silently. An OPEN effort still silent past
        # the threshold after a COMPLETED turn is a stall — nothing followed the turn.
        # Safe: the sweep only sees OPEN efforts, and human-gated / parked / actively-
        # delegating ones are skipped earlier, so unlike the `plan_drafted` mistake of
        # 2026-07-16 this can never bypass a human gate.
        "wake_done",
        # P21 F4 (gym-019, 2026-07-21): the gym-008 `wake_done` fix above was DEFEATED by ONE
        # trailing event. An abandon (`wake_done`, covered) was immediately followed by a verifier
        # `worker_acquire` + a `check_exec` probe (git status / a test run) whose verify→publish
        # coroutine then died silently — moving the effort's LAST event PAST the covered `wake_done`
        # to `check_exec`, which the kind-gate did not cover → the effort sat open+idle for 2 HOURS
        # until a human `re-run it`. `check_exec` is a BRIDGE-ISSUED verify command, never a human
        # gate (same category as `dry_run_*`: auto-advancing, so silent-past-threshold = stuck, not
        # awaiting you). The human-gate/frozen/refusal cases are still excluded earlier + by their
        # own kinds staying OUT of this set, so this cannot bypass a gate (§4.5 / paper-F3).
        "check_exec",
    })

    async def _worker_urls(self) -> list[dict]:
        """The live worker pool's daemon base_urls (non-retired) — for restart-safe ground-truth
        probes (`has_running_task`) the in-memory scheduler state can't give after a bridge restart."""
        async with self.db.session_factory() as s:
            rows = (await s.execute(
                select(WorkerInstance).where(WorkerInstance.retired.is_(False)))).scalars().all()
        return [{"id": r.id, "base_url": r.base_url, "effort_id": r.effort_id} for r in rows]

    async def _last_event(self, effort_id: str) -> tuple[str, str] | None:
        async with self.db.session_factory() as s:
            row = (await s.execute(
                select(Event.kind, Event.ts).where(Event.effort_id == effort_id)
                .order_by(Event.ts.desc()).limit(1))).first()
        return (row[0], row[1]) if row else None

    async def _event_count(self, effort_id: str, kind: str) -> int:
        async with self.db.session_factory() as s:
            return int((await s.execute(
                select(func.count()).select_from(Event).where(
                    Event.effort_id == effort_id, Event.kind == kind))).scalar_one())

    async def _last_event_payload(self, effort_id: str, kind: str) -> dict | None:
        """The most recent payload for one event kind on one effort. Used by the delivery-ancestry
        check (P17 F13) to recover the head this effort last published."""
        async with self.db.session_factory() as s:
            row = (await s.execute(
                select(Event.payload).where(
                    Event.effort_id == effort_id, Event.kind == kind)
                .order_by(Event.id.desc()).limit(1))).first()
        if not row or not row[0]:
            return None
        p = row[0]
        return p if isinstance(p, dict) else None

    # A turn CLAIMING a verification result: "31/31 tests pass", "all tests pass", "suite is
    # green", "tests passed". Deliberately narrow — it must match a claim about a RUN, not a
    # description of intent ("I will run the tests") or of the suite's existence.
    _CLAIMS_VERIFICATION_RE = re.compile(
        r"\b(\d+\s*/\s*\d+\s+tests?\s+pass|all\s+\d*\s*tests?\s+pass|"
        r"tests?\s+(?:all\s+)?pass(?:ed|ing)?\b|suite\s+(?:is\s+)?green|"
        r"acceptance\s+corpus\s+(?:check\s+)?pass)", re.I)
    # A command that actually EXERCISES the product: a test runner, or the acceptance check.
    # `git log` is not one, which is the whole point.
    _IS_VERIFICATION_CMD_RE = re.compile(
        r"\b(unittest|pytest|py\.test|tox|nose|npm\s+(?:run\s+)?test|yarn\s+test|"
        r"go\s+test|cargo\s+test|dotnet\s+test|make\s+(?:test|check)|--help)\b", re.I)
    # An honest carry-forward: the turn says the result is inherited rather than fresh.
    _CARRIES_FORWARD_RE = re.compile(
        r"\b(unchanged since|already (?:verified|committed|delivered|pushed)|"
        r"in the (?:previous|prior) turn|no additional changes)\b", re.I)

    async def _flag_unverified_claim(self, effort_id: str, result) -> bool:
        """P18 F18 — did this turn claim a verification result it did not produce?

        gym-016 produced three: a step reporting "All 31 tests pass, acceptance corpus passes"
        after running only `git log --oneline -5`; a drain no-op reporting "31/31 tests pass" after
        `git log --oneline -3`; and a plan turn reporting "the existing test suite (28 tests)
        passes" moments after a command printed 31. Each was true by luck — nothing had changed —
        and the org had no way to establish that. It is the same mechanism by which gym-015's
        delivery claim outlived the commit it named (P17 F2).

        Returns True when a claim was made with no verification command in the turn's record.

        FLAGS, never blocks. A carry-forward is legitimate and common — most no-op turns correctly
        report an unchanged result — so the honest move is to make the unevidenced claim visible
        and let the delivery-side gates (which run `check_cmd` themselves) remain the arbiters. A
        turn that says so explicitly ("unchanged since the previous turn") is not flagged at all:
        the problem is silence about provenance, not the carry-forward itself.

        Fails SILENT when the command record is empty: `_command_texts` returns [] both for "ran
        nothing" and for "shape we could not read", and flagging on the latter would cry wolf on
        every daemon whose activity schema drifts."""
        out = (getattr(result, "output", None) or "")
        if not out or not self._CLAIMS_VERIFICATION_RE.search(out):
            return False
        cmds = list(getattr(result, "commands", None) or [])
        if not cmds:
            return False                      # cannot tell — never a claim of wrongdoing
        if any(self._IS_VERIFICATION_CMD_RE.search(c) for c in cmds):
            return False                      # it ran one; the claim is earned
        if self._CARRIES_FORWARD_RE.search(out):
            return False                      # honest about inheriting the result
        await self.audit.log("unverified_claim", effort_id=effort_id,
                             payload={"claim": out[:300], "commands": cmds[:10]})
        await self.comms.post(
            Intent.worker_activity,
            "🔎 That turn reported a test result without running the tests in it. The result may "
            "well still hold — nothing may have changed — but say so explicitly (\"unchanged "
            "since <commit>\") rather than restating it as if freshly measured. I verify the "
            "suite myself before anything is delivered.",
            effort_id=effort_id,
        )
        return True

    _TESTDEF_RE = re.compile(r"^TESTDEFS\s+(\d+)\s*$", re.M)
    # P19 F13-redux — count test DEFINITIONS by AST, not by scraping the runner's stdout. A parse
    # per file (parse errors skipped, so a mid-edit file cannot abort the count), every `def
    # test_*` / `async def test_*` in the tree. Deterministic: the same tree always yields the same
    # number, which "Ran N tests" does not.
    _TESTDEF_CMD = (
        "cd /workspace && python3 -c \"import ast,glob\n"
        "def c(p):\n"
        " try: t=ast.parse(open(p,encoding='utf-8').read())\n"
        " except Exception: return 0\n"
        " return sum(isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and "
        "x.name.startswith('test_') for x in ast.walk(t))\n"
        "print('TESTDEFS',sum(c(p) for p in sorted(set("
        "glob.glob('**/test_*.py',recursive=True)+glob.glob('**/*_test.py',recursive=True)))))\"")

    async def _check_test_count_regression(self, effort_id: str) -> int | None:
        """P17 F13 (deferred) / P18 / P19 — a delivery whose test count DROPPED is a silent
        regression.

        Specified in P17 as "the cheapest possible detector" and then not built. gym-015 shows the
        cost: a stale workspace published a tree with 51 tests where the branch had 55, and the
        drop passed unremarked because nothing in the org remembers the previous count. gym-016
        showed the softer version — a worker reported "28 tests" moments after running the command
        that printed 31, and again nothing noticed.

        One integer, compared round over round. Strictly weaker than the ancestry check (a revert
        that happens to preserve the count defeats it) and completely independent of git, which is
        exactly why it is worth having alongside. Records the count and returns the previous one
        when a regression is detected, else None.

        P19 F13-redux — the count is now the number of test DEFINITIONS in the tree (AST), not a
        scrape of "Ran N tests". gym-017 fired this flag WRONGLY: the first publish scraped `55`
        from a flaky run of a suite that has a stable 44 `def test_`, so the next round's honest 44
        read as a regression. A definition count is stable across runs, which is the whole point of
        a round-over-round comparison.

        Never blocks: this RAISES A FLAG for the human. A test count can legitimately fall (a
        consolidated suite, a removed feature the operator asked for), so the honest move is to
        make the drop visible rather than to refuse the delivery on it."""
        proj = await self._effort_project(effort_id)
        check = ""
        try:
            p = await self.projects.get(proj) if proj else None
            check = ((p or {}).get("check_cmd") or "").strip()
        except Exception:  # noqa: BLE001
            return None
        if not check:
            return None                       # no configured suite — nothing to count
        try:
            _exit, out, _timed = await self.router.exec_check(
                effort_id, command=self._TESTDEF_CMD,
                session_id=f"{effort_id}~testcount", repo=None, repo_token=None, timeout=300)
        except Exception as exc:  # noqa: BLE001 — unmeasurable is not a regression
            log.debug("test count read failed for %s: %s", effort_id, exc)
            return None
        m = self._TESTDEF_RE.search(out or "")
        if not m:
            return None                       # the counter did not run; nothing to compare
        count = int(m.group(1))
        prev_payload = await self._last_event_payload(effort_id, "delivery_test_count")
        prev = (prev_payload or {}).get("count")
        await self.audit.log("delivery_test_count", effort_id=effort_id,
                             payload={"count": count, "previous": prev})
        if isinstance(prev, int) and count < prev:
            await self.audit.log("test_count_regressed", effort_id=effort_id,
                                 payload={"count": count, "previous": prev})
            msg = (f"⚠️ **{effort_id}** — the delivered test count **fell from {prev} to "
                   f"{count}**. That can be legitimate (a consolidated suite, a feature you asked "
                   f"to remove), but it is also what a silent revert or a dropped test file looks "
                   f"like, and the suite still passes either way. Worth a look before merging.")
            await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
            return prev
        return None

    async def _delivery_orphans_previous_head(
        self, effort_id: str, repo: str, delivery: BranchDelivery,
    ) -> str | None:
        """P17 F13/F2 — has this delivery THROWN AWAY the head it previously published?

        Returns the orphaned sha when the new head does not descend from the last one, else None.

        gym-015 round 5 dispatched to a worker whose workspace was four commits stale. It committed
        on that base, producing `1b04400` whose parent is `0f375e0` rather than the branch head
        `1ed9da6` — silently dropping the quoting fix, the TypedDict, `pyproject.toml`,
        `docs/architecture.md` and four tests (55 → 51). Nothing noticed: the suite passed at
        51/51, and the org's provenance check PASSED because it asks the wrong question —

            git rev-list -n 500 HEAD | grep -q ^<base_sha>     ← true on a 4-commits-stale HEAD

        Base ancestry is not head currency. A stale workspace answers "is the base in my history?"
        perfectly well. The only question that catches this is whether the PREVIOUS head is an
        ancestor of the NEW one, which is also exactly the check F2 needs (a reported hash that no
        longer exists fails it too). That round survived on merge luck — a later merge happened to
        reconcile both sides — which is not a mechanism.

        Fails OPEN: an unreadable remote or a first delivery is never an orphan claim."""
        new_head = (delivery.head_sha or "").strip()
        if not new_head or not delivery.verifiable:
            return None
        prev = await self._last_event_payload(effort_id, "effort_published")
        prev_head = ((prev or {}).get("head_sha") or "").strip()
        if not prev_head or prev_head == new_head:
            return None
        # No `github_app_enabled` guard: `sha_is_ancestor` already returns None on GitHubAppError,
        # and this must fail OPEN anyway — an unreadable remote is never an orphan claim.
        if self.github is None:
            return None
        try:
            ok = await sha_is_ancestor(
                self.github, repo, prev_head, new_head,
                api_base=self.s.github_api_base, transport=self._gh_transport)
        except Exception as exc:  # noqa: BLE001 — unverifiable is NOT an orphan
            log.debug("ancestry check failed for %s: %s", effort_id, exc)
            return None
        if ok is False:
            return prev_head
        return None

    # ── §4 TIERED SCOPE TREE — the operator's composition layer ──────────────
    async def add_scope_node(self, project_slug: str, title: str, scope: str, *,
                             parent_id: str | None = None, contract: str | None = None) -> str | None:
        """Add one TIER to a project's scope tree. Top-down decomposition: each node bounds what a
        worker at that tier may hold, so the long horizon lives in the TREE rather than in any single
        model's context (§4). `contract` is the node's executable boundary — the check that says this
        scope is satisfied. Returns the node id, or None if the project/parent is unknown."""
        title, scope = (title or "").strip(), (scope or "").strip()
        if not title or not scope:
            return None
        nid = "sn-" + hashlib.sha1(f"{project_slug}|{parent_id or ''}|{title}".encode()).hexdigest()[:12]
        try:
            async with self.db.session_factory() as s:
                if await s.get(Project, project_slug) is None:
                    return None
                depth = 0
                if parent_id:
                    par = await s.get(ScopeNode, parent_id)
                    if par is None:
                        return None
                    depth = par.depth + 1
                if await s.get(ScopeNode, nid) is None:
                    s.add(ScopeNode(id=nid, project_slug=project_slug, parent_id=parent_id,
                                    depth=depth, title=title[:200], scope=scope,
                                    contract=(contract or None)))
                    await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("scope node add failed for %s: %s", project_slug, exc)
            return None
        await self.audit.log("scope_node_added",
                             payload={"id": nid, "project": project_slug, "parent": parent_id,
                                      "depth": depth, "title": title[:120]})
        return nid

    async def _scope_node(self, node_id: str) -> dict | None:
        try:
            async with self.db.session_factory() as s:
                n = await s.get(ScopeNode, node_id)
                if n is None:
                    return None
                return {"id": n.id, "project_slug": n.project_slug, "parent_id": n.parent_id,
                        "depth": n.depth, "title": n.title, "scope": n.scope,
                        "contract": n.contract, "status": n.status, "effort_id": n.effort_id}
        except Exception as exc:  # noqa: BLE001
            log.debug("scope node read failed for %s: %s", node_id, exc)
            return None

    async def _scope_context(self, node_id: str) -> str:
        """The BOUNDED brief a worker at this tier gets: its own scope and its contract — and
        deliberately NOT the rest of the tree. Withholding the global picture is the mechanism, not an
        oversight: a small model fails at whole-project horizon and succeeds inside a bounded scope
        (§4). What lies outside is named only as 'not yours — escalate', so the worker knows the
        BORDER without carrying what's beyond it."""
        n = await self._scope_node(node_id)
        if not n:
            return ""
        contract = (f"\nDONE means this passes: `{n['contract']}`" if n.get("contract")
                    else "\n(No executable contract on this scope yet — say so rather than guessing "
                         "when you believe it is done.)")
        return (
            f"\n\nYOUR SCOPE — `{n['title']}` (tier {n['depth']}):\n{n['scope']}{contract}\n"
            f"This scope is the WHOLE of your responsibility. Anything outside it is NOT yours to "
            f"fix, refactor, or redesign — if your work is blocked by something beyond this border, "
            f"do NOT work around it: report it as `ESCALATE: <what you need and why>` and stop. "
            f"Someone owns the adjacent scope and will decide it.")

    # ── P10.6 THE TIER WALK — what makes the §4 tree LIVE ─────────────────────
    # Until now `ScopeNode` and its helpers existed with NO CALLER. The tree is what stops the loop
    # running out of work before the project is done: a scope completes when its own queue drains
    # and its sweep is silent, then its PARENT re-evaluates, and so on up to the project. Crucially
    # "complete" is a CURRENT STATE, not a terminal one — a neighbour's later sweep can reopen a
    # finished scope, which is the integration/seam check.
    async def _ensure_scope_node(self, effort_id: str) -> str | None:
        """The scope node this effort is working, creating the project's ROOT tier on first use.

        An effort that arrived before the tree existed still needs somewhere to hang its tasks, so
        the root is derived from the effort's own goal rather than demanding an up-front
        decomposition. Returns None when the effort has no project (nothing to scope)."""
        proj = await self._effort_project(effort_id)
        if not proj:
            return None
        try:
            async with self.db.session_factory() as s:
                row = (await s.execute(
                    select(ScopeNode).where(ScopeNode.effort_id == effort_id)
                    .order_by(ScopeNode.depth.desc()))).scalars().first()
                if row is not None:
                    return row.id
        except Exception as exc:  # noqa: BLE001
            log.debug("scope node lookup failed for %s: %s", effort_id, exc)
            return None
        try:
            _, goal, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal = ""
        # The root is PER EFFORT, not per project. `add_scope_node` content-addresses on
        # `(project, parent, title)`, so a title of just the project slug would hand every effort
        # on that project the SAME node — the newest effort would steal `effort_id` while the
        # node's `scope` stayed the first effort's goal, and gap analysis would then score effort B
        # against effort A's goal. The effort id in the title keeps the roots distinct; a genuine
        # shared tree is built by `decompose_scope` under a deliberate parent.
        nid = await self.add_scope_node(proj, f"{proj} · {effort_id}",
                                        (goal or f"the {proj} project").strip())
        if nid:
            await self._attach_effort_to_scope(nid, effort_id)
        return nid

    async def _attach_effort_to_scope(self, node_id: str, effort_id: str) -> None:
        try:
            async with self.db.session_factory() as s:
                n = await s.get(ScopeNode, node_id)
                if n is not None and n.effort_id != effort_id:
                    n.effort_id = effort_id
                    await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("scope attach failed for %s: %s", node_id, exc)

    async def _scope_children(self, node_id: str) -> list[dict]:
        try:
            async with self.db.session_factory() as s:
                rows = (await s.execute(
                    select(ScopeNode).where(ScopeNode.parent_id == node_id)
                    .order_by(ScopeNode.created_at))).scalars().all()
            return [{"id": r.id, "title": r.title, "scope": r.scope, "status": r.status,
                     "effort_id": r.effort_id, "depth": r.depth} for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.debug("scope children read failed for %s: %s", node_id, exc)
            return []

    async def decompose_scope(self, node_id: str, parts: list[tuple[str, str]]) -> list[str]:
        """Split one tier into child tiers — the top-down decomposition of §4. Each part is
        `(title, scope)`; the child inherits nothing but its border, because what a worker at a
        tier may hold is exactly what keeps a small model inside a scope it can actually reason
        about. Returns the created node ids."""
        n = await self._scope_node(node_id)
        if not n:
            return []
        out: list[str] = []
        for title, scope in parts:
            cid = await self.add_scope_node(n["project_slug"], title, scope, parent_id=node_id)
            if cid:
                out.append(cid)
        if out:
            await self.audit.log("scope_decomposed",
                                 payload={"parent": node_id, "children": len(out)})
        return out

    async def _maybe_decompose(self, node_id: str, evidence: list[str], *,
                               effort_id: str | None = None,
                               from_reports: bool = False) -> list[str]:
        """SPLIT A TIER THAT HAS OUTGROWN ITSELF — the production caller that makes the tree live.

        Decomposition is driven by evidence rather than guessed up front, and (P11.3) that evidence
        is now the LENS REPORTS rather than derived tasks. The order matters: splitting from the
        raw observation of what the codebase contains means the resulting scopes describe the
        PRODUCT's real parts, and gap analysis can then run against one of them. Splitting from
        derived tasks — as gym-009 did — happens after the analysis it was meant to scope, which is
        why that tree was never used for anything.

        No-ops when the node already has children, when the tier is deep enough (depth is a
        reliability tax — loss compounds per hop, §4), or when the evidence is too thin to justify
        a split. Returns the created child ids."""
        n = await self._scope_node(node_id)
        if not n or n["depth"] >= self.s.drain_max_tier_depth:
            return []
        if await self._scope_children(node_id):
            return []
        if from_reports:
            # A report is one blob per lens, so "how much work is here" can't be a line count.
            # Require real substance before carving a tree out of it.
            if sum(len(e) for e in evidence) < 1200:
                return []
        elif len(evidence) < max(2, self.s.drain_decompose_threshold):
            return []
        sys_p = (
            "You identify the distinct parts of a software project from a description of it.\n"
            "- Output one part per line, as `title :: what this part covers`.\n"
            "- Between 2 and 5 parts. Together they cover the whole project.\n"
            "- Titles are concrete parts of THIS project (e.g. `storage`, `argument parsing`), "
            "not generic words like `layer`, `module` or `core`.\n"
            "- No preamble, no numbering, no explanation."
        )
        listed = "\n\n".join(e[:2500] for e in evidence[:3])
        try:
            out = await self.models.complete(
                "pm", sys_p,
                f"PROJECT SCOPE:\n{n['scope'][:1500]}\n\n"
                + (f"OBSERVATIONS OF THE CODEBASE:\n{listed}" if from_reports
                   else f"TASKS:\n{listed}"))
        except Exception as exc:  # noqa: BLE001 — a flat tree is a valid tree; never block a round
            log.debug("scope decomposition failed for %s: %s", node_id, exc)
            return []
        parts: list[tuple[str, str]] = []
        for ln in (out or "").splitlines():
            ln = re.sub(r"^\s*(?:[-*•]+\s*|\d+[.)]\s+)", "", ln.strip()).strip()
            if "::" not in ln:
                continue
            title, _, scope = ln.partition("::")
            title, scope = title.strip()[:200], scope.strip()
            if title and scope and title.lower() not in _SCOPE_STOPWORDS:
                parts.append((title, scope))
        if len(parts) < 2:
            return []
        # P17 F9 — A CHILD MUST BE SMALLER THAN ITS PARENT. gym-015 decomposed
        # "Data persistence layer :: handles loading, saving, atomic file writes, and robust
        # parsing" into a child "data_layer :: handles loading, saving, atomic file writes,
        # database path configuration, and malformed data resilience" — a restatement, not a
        # narrowing. The tier walk descended a level, narrowed nothing, and spent a round doing it.
        # Nothing checked, because `decompose_scope` accepts whatever the model returns.
        # Cheap, high-precision test: near-total token overlap with the parent AND no new
        # constraint terms. A genuinely narrower child names something the parent did not.
        parent_words = {w for w in re.findall(r"[a-z]{4,}", (n["scope"] or "").lower())}
        narrowed: list[tuple[str, str]] = []
        for title, scope in parts[:5]:
            words = {w for w in re.findall(r"[a-z]{4,}", scope.lower())}
            novel = words - parent_words
            # A child that adds nothing (or almost nothing) to its parent's vocabulary is a
            # paraphrase. Two novel content words is a deliberately low bar — the aim is to catch
            # "data persistence layer" -> "data_layer", not to police wording.
            if words and len(novel) < 2 and len(words & parent_words) >= max(3, len(words) - 1):
                await self.audit.log("scope_child_rejected_not_narrower", effort_id=effort_id,
                                     payload={"parent": node_id, "title": title[:80],
                                              "scope": scope[:160]})
                continue
            narrowed.append((title, scope))
        if len(narrowed) < 2:
            # Everything the model returned restated the parent — treat this node as ATOMIC rather
            # than burning a tier on a copy of itself.
            await self.audit.log("scope_decompose_declined", effort_id=effort_id,
                                 payload={"parent": node_id, "returned": len(parts)})
            return []
        kids = await self.decompose_scope(node_id, narrowed[:5])
        if kids:
            await self.audit.log("scope_decomposed_live", effort_id=effort_id,
                                 payload={"parent": node_id, "children": len(kids),
                                          "source": "reports" if from_reports else "tasks",
                                          "evidence": len(evidence)})
        return kids

    async def _select_working_scope(self, effort_id: str, root_id: str) -> str | None:
        """P11.3 — WHICH TIER IS THIS ROUND ACTUALLY WORKING? The deepest open descendant that
        still holds work, else the shallowest open one, else the root.

        Without this the tree is decorative. gym-009 built three child scopes and then analysed
        against the root's goal every single round, because `_ensure_scope_node` can only return a
        node whose `effort_id` matches — and decomposition never set one. Selecting here (and
        stamping the choice, below) is what makes a bounded scope reach gap analysis.

        Returns the selected node id, or None to leave the caller's choice alone."""
        kids = await self._scope_children(root_id)
        if not kids:
            return None
        # A scope another effort OWNS is not ours to select — taking it would have this effort
        # working inside someone else's border, which is the encapsulation break the tree exists
        # to prevent (§4). Unowned children are fair game; that is what decomposition creates.
        def _mine(k: dict) -> bool:
            return k["status"] != "done" and k["effort_id"] in (None, "", effort_id)

        # Prefer a child that already carries open work — that is where this round's effort lives.
        for k in kids:
            if _mine(k) and await self.list_open_tasks(scope_node_id=k["id"]):
                await self._attach_effort_to_scope(k["id"], effort_id)
                return k["id"]
        for k in kids:
            if _mine(k):
                await self._attach_effort_to_scope(k["id"], effort_id)
                return k["id"]
        # Nothing selectable below: every child is done, or belongs to another owner. The parent is
        # the working scope — which is precisely when a seam check is meaningful.
        return None

    async def _seam_owner(self, node_id: str | None, task_body: str) -> str | None:
        """Which CHILD scope owns this finding, if any — the seam router.

        A sweep at a parent tier sees the whole assembled product, so it surfaces defects that live
        inside a child's territory. Those must be written into the CHILD (and reopen it), not fixed
        by the parent: a parent reaching into its children's insides is exactly the encapsulation
        break the tree exists to prevent. Matched on the child's title tokens appearing in the
        finding — deliberately conservative, because mis-routing work is worse than leaving it with
        the parent, which is a legitimate owner of a genuine seam."""
        if not node_id:
            return None
        kids = await self._scope_children(node_id)
        if not kids:
            return None
        body = (task_body or "").lower()
        matches: list[str] = []
        for k in kids:
            # Generic structural words ("layer", "module", "component") carry no ownership
            # information — a child titled "cli layer" reducing to ["layer"] would claim every task
            # mentioning any layer. Require the DISTINCTIVE tokens, matched on word boundaries so
            # "port" cannot match "reporting".
            toks = [t for t in re.split(r"[^a-z0-9]+", (k["title"] or "").lower())
                    if len(t) >= 4 and t not in _SCOPE_STOPWORDS]
            if toks and all(re.search(rf"\b{re.escape(t)}\b", body) for t in toks):
                matches.append(k["id"])
        # AMBIGUITY STAYS WITH THE PARENT. Two children both claiming a finding means we cannot
        # tell who owns it, and mis-routing work is worse than leaving it with the parent — which
        # is a legitimate owner of a genuine seam.
        return matches[0] if len(matches) == 1 else None

    async def _reopen_scope(self, node_id: str, *, reason: str = "",
                            effort_id: str | None = None) -> bool:
        """Flip a scope back to `open` because a neighbour's sweep found a defect it owns. This is
        why completion is a CURRENT STATE: a scope that drained honestly can still be reopened by
        work it could not see from the inside."""
        try:
            async with self.db.session_factory() as s:
                n = await s.get(ScopeNode, node_id)
                if n is None or n.status == "open":
                    return False
                n.status = "open"
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("scope reopen failed for %s: %s", node_id, exc)
            return False
        await self.audit.log("scope_reopened", effort_id=effort_id,
                             payload={"scope": node_id, "reason": (reason or "")[:200]})
        return True

    async def _complete_scope(self, node_id: str, effort_id: str | None = None) -> dict | None:
        """Mark a scope complete and BUBBLE UP: the parent is flagged for re-evaluation, because a
        child finishing changes what the parent's own sweep will see (its seams are now real). A
        parent whose children are ALL done and whose queue is empty is itself a completion
        candidate — that recursion is what walks the tree up to the project. Returns the parent
        node marked for re-evaluation, or None at the root (where a human governs)."""
        n = await self._scope_node(node_id)
        if not n:
            return None
        if await self.list_open_tasks(scope_node_id=node_id):
            return None    # the queue must DRAIN too — a silent sweep alone is not completion
        # NOR MAY A PARENT COMPLETE OVER AN UNFINISHED CHILD. A tier whose findings all seam-routed
        # down has an empty queue of its own while the work it discovered is still outstanding
        # below it — completing on that would report the whole subtree done on the strength of the
        # parent having handed its work away.
        #
        # But a child this effort's own decomposition created has NO owner of its own: nobody will
        # ever call `_complete_scope` on it, so a strict "all children done" rule would deadlock
        # the parent until the runaway cap. An UNOWNED child whose queue has drained, under a
        # parent whose sweep is silent, is complete by exactly the same rule that is completing the
        # parent — so close it here rather than let it block forever.
        for kid in await self._scope_children(node_id):
            if kid["status"] == "done":
                continue
            if kid["effort_id"] and kid["effort_id"] != effort_id:
                return None      # a real other owner — not ours to declare finished
            if await self.list_open_tasks(scope_node_id=kid["id"]):
                return None      # its queue still holds work
            await self._complete_scope(kid["id"], effort_id)
        if any(k["status"] != "done" for k in await self._scope_children(node_id)):
            return None
        try:
            async with self.db.session_factory() as s:
                row = await s.get(ScopeNode, node_id)
                if row is not None and row.status != "done":
                    row.status = "done"
                    await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("scope complete failed for %s: %s", node_id, exc)
            return None
        await self.audit.log("scope_completed", effort_id=effort_id,
                             payload={"scope": node_id, "title": n["title"], "depth": n["depth"]})
        parent = await self._escalation_target(node_id)
        if parent is None:
            return None
        siblings = await self._scope_children(parent["id"])
        pending = [k for k in siblings if k["status"] != "done"]
        await self.audit.log("scope_reevaluate", effort_id=effort_id,
                             payload={"scope": parent["id"], "child_done": node_id,
                                      "children_pending": len(pending)})
        # BUBBLE UP, RECURSIVELY. "child complete → parent complete → … → project" (§4) is a WALK,
        # not a single hop: a parent whose children are ALL done and whose own queue is empty is
        # itself complete, and its completion re-evaluates ITS parent. Without the recursion the
        # tree only ever completes one tier and the project is never reached.
        if not pending:
            await self._complete_scope(parent["id"], effort_id)
        return parent

    async def _escalation_target(self, node_id: str) -> dict | None:
        """Route an escalation UP: the tier that owns the ADJACENT scope (the parent) is the only
        place with the standing to decide a cross-scope issue — a worker inside a bounded scope
        structurally cannot (§4). None at the root, which is where a human governs."""
        n = await self._scope_node(node_id)
        if not n or not n.get("parent_id"):
            return None
        return await self._scope_node(n["parent_id"])

    # ── P10.3 THE SCOPED TASK QUEUE — the substrate the drain drains ──────────
    async def add_task(self, body: str, *, project_slug: str, scope_node_id: str | None = None,
                       effort_id: str | None = None, source_lens: str = "goal_alignment",
                       round_no: int = 1) -> tuple[str, bool] | None:
        """Queue one plainly-stated task for a scope. Returns `(task_id, is_new)`, or None if the
        body was empty. IDEMPOTENT by content address on `(scope_node_id, body)`: the same gap
        re-derived by next round's independent sweep collapses onto the existing row and reports
        `is_new=False`.

        That flag is the whole ballgame. Termination is "a full lens sweep propagated ZERO NEW
        tasks" — if a re-derived gap counted as new, every round would re-propagate its
        predecessors' findings and the count could never reach zero, so the loop would run to the
        runaway cap on a finished project. Mirrors `AcceptanceCheck` / `EffortConstraint`, which
        content-address for the same reason."""
        body = " ".join((body or "").split()).strip()
        if not body:
            return None
        # Content address. The OWNER is the scope node when there is one; without a tree, two
        # concurrent efforts on one project that derive the same body would otherwise collide —
        # the second would see `is_new=False` on a row carrying the FIRST effort's `effort_id`,
        # making it invisible to both `list_open_tasks(effort_id=…)` and `count_new_tasks`, and the
        # second effort would complete on a phantom zero. Falling back to the effort (not the
        # project) keeps idempotency per owner while keeping efforts genuinely separate.
        owner = scope_node_id or effort_id or project_slug or "unscoped"
        tid = "st-" + hashlib.sha1(f"{owner}|{body}".encode()).hexdigest()[:12]
        try:
            async with self.db.session_factory() as s:
                existing = await s.get(ScopeTask, tid)
                if existing is None:
                    s.add(ScopeTask(id=tid, project_slug=project_slug, scope_node_id=scope_node_id,
                                    effort_id=effort_id, body=body[:2000], source_lens=source_lens,
                                    round_no=round_no))
                    await s.commit()
                    is_new = True
                else:
                    is_new = False
                    # A CLOSED task re-derived by a later independent sweep is still-outstanding
                    # work: reopen it so the queue stays true. It is emphatically NOT new
                    # information, so it keeps its original `round_no` and does not count toward
                    # propagation — otherwise a task the implementer keeps failing would
                    # re-propagate forever and the loop could never terminate.
                    #
                    # `dispatched` reopens for exactly the same reason as `done` (P17 F12 split the
                    # two): both mean "closed, and the sweep can still see the gap". Only `dropped`
                    # stays shut — that is a deliberate decision that the item is not wanted, and
                    # re-deriving it must not silently overturn the operator's call.
                    if existing.status in ("done", "dispatched"):
                        existing.status = "open"
                        existing.closed_at = None
                        await s.commit()
        except Exception as exc:  # noqa: BLE001 — the queue must never block a delivery path
            log.debug("scope task add failed for %s: %s", project_slug, exc)
            return None
        if is_new:
            await self.audit.log("scope_task_added", effort_id=effort_id,
                                 payload={"id": tid, "scope": scope_node_id, "lens": source_lens,
                                          "round": round_no, "body": body[:200]})
        return tid, is_new

    async def list_open_tasks(self, *, project_slug: str | None = None,
                              scope_node_id: str | None = None,
                              effort_id: str | None = None) -> list[dict]:
        """This scope's OPEN tasks, oldest first (the order they were discovered)."""
        try:
            async with self.db.session_factory() as s:
                q = select(ScopeTask).where(ScopeTask.status == "open")
                if scope_node_id is not None:
                    q = q.where(ScopeTask.scope_node_id == scope_node_id)
                elif effort_id is not None:
                    q = q.where(ScopeTask.effort_id == effort_id)
                elif project_slug is not None:
                    q = q.where(ScopeTask.project_slug == project_slug)
                rows = (await s.execute(q.order_by(ScopeTask.created_at))).scalars().all()
            return [{"id": r.id, "body": r.body, "lens": r.source_lens, "round": r.round_no,
                     "scope_node_id": r.scope_node_id, "effort_id": r.effort_id} for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.debug("scope task list failed: %s", exc)
            return []

    async def close_task(self, task_id: str, *, status: str = "done") -> bool:
        """Close one task.

        `done` — evidenced as worked. `dropped` — no longer relevant. `dispatched` — handed to an
        implementer, outcome NOT yet known (P17 F12).

        The `dispatched` state exists because the drain closes its queue at HAND-OVER, before the
        implementer has run. Recording that as `done` made the org assert completion it had never
        observed: in gym-015 a worker explicitly declined two out-of-scope tasks ("Not touched:
        outside data_layer scope") and both were already marked `done` — one of them,
        `st-19ee694` (remove the broad `except Exception` in `cmd_repl`), is still not done on the
        delivered branch. All three states are closed for dispatch purposes, so nothing is
        re-dispatched forever; only the CLAIM differs, and the claim is what the audit reads."""
        try:
            async with self.db.session_factory() as s:
                t = await s.get(ScopeTask, task_id)
                if t is None or t.status != "open":
                    return False
                t.status = status if status in ("done", "dropped", "dispatched") else "done"
                t.closed_at = _now_iso()
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("scope task close failed for %s: %s", task_id, exc)
            return False
        return True

    async def count_new_tasks(self, round_no: int, *, scope_node_id: str | None = None,
                              effort_id: str | None = None) -> int:
        """How many tasks THIS ROUND propagated — the termination quantity (P10.4).

        Counts rows STAMPED with this round, which by content-addressing can only be tasks nobody
        had seen before: a re-derived gap kept its original `round_no` and is invisible here. Zero
        means an independent, un-primed sweep of the whole scope found nothing new to do, which is
        the only honest definition of "complete" the org has."""
        try:
            async with self.db.session_factory() as s:
                q = select(func.count()).select_from(ScopeTask).where(ScopeTask.round_no == round_no)
                # Both filters COMBINE when both are given. `round_no` is per-effort (it counts
                # THIS effort's drain rounds), so a scope filter alone would mix efforts' rounds on
                # a shared tree and report another effort's round-3 work as this one's.
                if scope_node_id is not None:
                    q = q.where(ScopeTask.scope_node_id == scope_node_id)
                if effort_id is not None:
                    q = q.where(ScopeTask.effort_id == effort_id)
                return int((await s.execute(q)).scalar_one())
        except Exception as exc:  # noqa: BLE001
            log.debug("new-task count failed: %s", exc)
            return 0

    async def _dispatchable_tasks(self, effort_id: str, node_id: str | None) -> list[dict]:
        """The open tasks THIS effort must actually work.

        "A parent never fixes its children's insides" (§4) is about ANOTHER OWNER's scope — it
        presumes somebody else is going to do that work. An UNOWNED child scope has no such
        somebody: nothing else is attached to it, and `_ensure_scope_node` always resolves an
        effort to its own root, so a task filed there can never be picked up by anyone.

        Filtering those out (as a naive "own node only" rule does) creates the worst failure this
        loop has: the round that decomposes a scope routes every task it just derived into the new
        children, the parent's list comes back EMPTY, and the effort closes reporting "a full,
        independent lens sweep found nothing further to do" while all of its work sits unreachable
        one tier down. So: exclude a descendant's tasks only when that descendant belongs to a
        DIFFERENT effort — a real other owner, which is the case the encapsulation rule is for."""
        mine = await self.list_open_tasks(effort_id=effort_id)
        if not node_id:
            return mine
        # P13.1 — THE BRIEF AND THE BORDER MUST DESCRIBE THE SAME WORK. Until now this returned
        # every task belonging to the EFFORT while `_drain_iterate` injected the context of the
        # SELECTED SCOPE, so the worker was told "work these 12 tasks" and "your scope is JSON data
        # persistence" in the same brief. gym-011 (2026-07-19) did exactly what `_scope_context`
        # prescribes — worked its 5 persistence tasks, wrote `ESCALATE: REPL scope worker needed`
        # for the rest — and the plan gate, which judges against the GOAL, rejected that three times
        # and BLOCKED the effort. Nothing misbehaved; the instructions were incoherent.
        #
        # Dispatch is now exactly the selected scope's tasks. A sibling scope's work stays queued
        # until the walk selects that scope, which is the whole point of the tier walk: bounded
        # work, one tier at a time.
        own = [t for t in mine if t["scope_node_id"] == node_id]
        if own:
            return own
        # No task filed against this node yet (e.g. the round that created the tree). Fall back to
        # the effort's unrouted tasks — never a sibling's, and never one another effort owns.
        kid_ids = {k["id"] for k in await self._scope_children(node_id)}
        for kid in list(kid_ids):
            kid_ids |= {g["id"] for g in await self._scope_children(kid)}
        return [t for t in mine if t["scope_node_id"] in (None, node_id)
                and t["scope_node_id"] not in kid_ids]

    async def _record_lens_report(self, effort_id: str, lens: str, body: str, *, round_no: int,
                                  scope_node_id: str | None = None) -> str | None:
        """Persist one lens's report FOR OUR HISTORY ONLY. Never read back into a prompt — see
        `LensReport`: a sweep that inherits the last sweep's text is not independent, and the
        propagation count would stop meaning anything."""
        body = (body or "").strip()
        if not body:
            return None
        rid = "lr-" + hashlib.sha1(f"{effort_id}|{lens}|{round_no}".encode()).hexdigest()[:12]
        try:
            async with self.db.session_factory() as s:
                if await s.get(LensReport, rid) is None:
                    s.add(LensReport(id=rid, effort_id=effort_id, scope_node_id=scope_node_id,
                                     lens=lens, round_no=round_no, body=body[:20000]))
                    await s.commit()
        except Exception as exc:  # noqa: BLE001 — history is never a dispatch blocker
            log.debug("lens report persist failed for %s: %s", effort_id, exc)
            return None
        return rid

    async def _record_constraint(self, effort_id: str, body: str, *, origin: str = "",
                                 kind: str = "failure") -> str | None:
        """CDCL clause learning (ORCHESTRATION-DESIGN §5–6): record a failure as a durable constraint
        on this effort so every later retry inherits it and the search narrows. Content-addressed →
        re-recording the same failure is a no-op (clause subsumption), which matters because several
        red paths funnel the same underlying failure here. Returns the id, or None if it was NOT
        recorded (empty, or an INFRA failure — a proxy/clone/tool breakage is not a fact about the
        code and must never steer the search). Never raises: learning is not a dispatch blocker."""
        body = (body or "").strip()
        if not body:
            return None
        if _is_infra_failure(body):
            return None
        sig = self._failure_sig(body)
        cid = "ec-" + hashlib.sha1(f"{effort_id}|{kind}|{sig}".encode()).hexdigest()[:12]
        try:
            async with self.db.session_factory() as s:
                if await s.get(EffortConstraint, cid) is None:
                    s.add(EffortConstraint(
                        id=cid, effort_id=effort_id, signature=sig, kind=kind,
                        body=body[:4000], origin_note=(origin or "")[:512]))
                    await s.commit()
                    fresh = True
                else:
                    fresh = False
        except Exception as exc:  # noqa: BLE001 — learning must never block a retry
            log.debug("constraint record failed for %s: %s", effort_id, exc)
            return None
        if fresh:
            await self.audit.log("constraint_learned", effort_id=effort_id,
                                 payload={"id": cid, "sig": sig, "origin": (origin or "")[:120]})
        return cid

    async def _list_constraints(self, effort_id: str) -> list[dict]:
        """This effort's learned constraints (oldest first — the order they were discovered)."""
        try:
            async with self.db.session_factory() as s:
                rows = (await s.execute(
                    select(EffortConstraint)
                    .where(EffortConstraint.effort_id == effort_id,
                           EffortConstraint.active.is_(True))
                    .order_by(EffortConstraint.created_at))).scalars().all()
            return [{"id": r.id, "sig": r.signature, "kind": r.kind, "body": r.body,
                     "origin_note": r.origin_note} for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.debug("constraint list failed for %s: %s", effort_id, exc)
            return []

    async def _constraints_context(self, effort_id: str, *, limit: int = 12) -> str:
        """The accumulated constraints as a retry preamble — the CDCL clause set the next attempt must
        not re-walk. Empty string when nothing has been learned yet."""
        cs = await self._list_constraints(effort_id)
        if not cs:
            return ""
        shown = cs[-limit:]
        listed = "\n".join(
            f"  {i}. {c['body'].strip()[:400]}" + (f"   ({c['origin_note']})" if c['origin_note'] else "")
            for i, c in enumerate(shown, 1))
        more = f"\n  …and {len(cs) - len(shown)} earlier constraint(s)." if len(cs) > len(shown) else ""
        return (
            f"\n\nLEARNED CONSTRAINTS ({len(cs)}) — failures already hit on THIS task. Each one is a "
            f"dead end that has been tried; do NOT repeat these approaches, and do not undo a fix that "
            f"resolved one:\n{listed}{more}\nTreat them as the narrowed search space: your next attempt "
            f"must satisfy all of them at once.")

    def _waiting_on(self, effort_id: str) -> dict | None:
        """P8 #2 — WAITING-ON-HUMAN as a first-class state. Whether this effort is parked at a
        HUMAN gate right now, and which: `{gate, asked_at, ask}` — derived straight from the hold
        dicts (the single source of truth, rehydrated across restarts by the PendingStore), so it
        can never drift out of sync with the actual parking. Born 2026-07-16: an effort correctly
        holding at the Stage-3 plan gate looked IDENTICAL to a wedge (idle GPU, silent thread), a
        session misread it as a stall, and the watchdog it 'fixed' auto-executed unapproved plans
        after 15 min. None = not waiting on a human."""
        if effort_id in self._pending:
            e = self._pending[effort_id]
            qs = e.get("questions") or []
            return {"gate": "clarification", "asked_at": e.get("asked_at", ""),
                    "ask": (qs[0] if isinstance(qs[0], str) else str(qs[0]))[:200] if qs
                           else "answer the clarifying questions"}
        if effort_id in self._pending_plan:
            e = self._pending_plan[effort_id]
            return {"gate": "plan_approval", "asked_at": e.get("asked_at", ""),
                    "ask": f"`approve {effort_id}` (or `abort {effort_id}`)"}
        m = self._pending_merge.get(f"merge-{effort_id}")
        if m is not None:
            return {"gate": "merge", "asked_at": m.get("asked_at", ""),
                    "ask": f"say “merge it” for PR #{m.get('pr_number', '?')}"}
        # capability/lifecycle holds are keyed by action/plan id — match on their payload's effort
        for aid, e in self._pending_capability.items():
            if e.get("effort_id") == effort_id:
                return {"gate": "capability", "asked_at": e.get("asked_at", ""),
                        "ask": f"`approve {aid}`"}
        for pid, e in self._pending_lifecycle.items():
            if e.get("effort_id") == effort_id:
                return {"gate": "plan_approval", "asked_at": e.get("asked_at", ""),
                        "ask": f"`approve {pid}`"}
        return None

    async def _stall_watchdog_loop(self) -> None:
        """Safety net so the org NEVER sits silent after a dispatch (operator 2026-07-10: "there
        hasn't been an update in 2 hours"). Each tick sweeps for efforts wedged mid-dispatch — a
        focus that failed, a delegate that died, a worker left bound — and AUTO-RE-ENGAGES them
        (bounded), escalating loudly past the cap. Distinct from the capacity drain (backpressure)."""
        while True:
            try:
                await asyncio.sleep(self.s.stall_watchdog_s)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_stalled_efforts()
            except Exception as exc:  # noqa: BLE001 - the watchdog must never die
                log.warning("stall watchdog tick failed: %s", exc)

    async def _sweep_stalled_efforts(self) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        parked = {t["effort_id"] for t in await self.parks.all()}
        mgmt = await self.mgmt_channel_id()
        # RESTART-SAFE ground truth (live 2026-07-11: a bridge redeploy mid-task wiped the in-memory
        # `_delegating` marker; the watchdog then re-engaged an effort whose worker was STILL RUNNING
        # — the daemon 409'd it, and the failed recovery burned a retry + queued a noise escalation).
        # The daemons' own task lists survive restarts: if ANY worker is actually running a task,
        # work IS happening — defer this sweep's recoveries to a later tick instead of guessing.
        #
        # BUT "running" is not "progressing" (P9 register #25, arm D 2026-07-17): a worker that HANGS
        # mid-turn holds status `running` forever, so a status-only busy-defer sat idle 20 min while the
        # GPU was at 0%. So we ask the daemon for each running worker's per-agent-step event OFFSET
        # (advances on generation/tool/edit): offset climbing = alive → defer to it; offset FROZEN past
        # `worker_silence_s` = hung → cancel the turn and recover the bound effort now. This never
        # interrupts legitimate long work (a working worker keeps bumping the offset).
        alive = False
        hung: list[tuple[str, str, str | None]] = []          # (base_url, task_id, effort_id)
        if self.s.worker_silence_s > 0:
            for w in await self._worker_urls():
                url = w["base_url"]
                prev = self._worker_progress.get(url)
                try:
                    prog = await self.harness.running_task_progress(
                        url, since_offset=(prev[1] if prev else 0))
                except Exception:  # noqa: BLE001 — a probe failure must not stall the sweep
                    prog = None
                if prog is None:                              # idle daemon (no running task)
                    self._worker_progress.pop(url, None)
                    continue
                task_id, offset = prog
                if prev is not None and prev[0] == task_id and offset <= prev[1]:
                    # same task, no new agent-loop events since we last looked
                    if (now - prev[2]).total_seconds() >= self.s.worker_silence_s:
                        hung.append((url, task_id, w.get("effort_id")))
                        continue
                    alive = True                              # silent but still within the grace window
                else:                                         # first sight, new task, or offset advanced
                    self._worker_progress[url] = (task_id, offset, now)
                    alive = True
            for url, task_id, eid in hung:                    # recover each genuinely-hung worker
                await self.harness.cancel_task(url, task_id)   # free the daemon (orphaned turn) …
                self._worker_progress.pop(url, None)
                if not eid or eid in parked or self._waiting_on(eid) is not None:
                    continue
                self._delegating.discard(eid)                 # … its delegate coroutine is dead now
                n = await self._event_count(eid, "stall_recovered")
                if n >= self.s.stall_max_recoveries:
                    await self.audit.log("stall_escalated", effort_id=eid,
                                         payload={"reason": "worker_silent", "recoveries": n})
                    body = (f"🧰 **{eid}**'s worker hung mid-turn and my {n} recoveries didn't take — "
                            f"stopping auto-retry to avoid a loop. Say **“re-run it”** when clear.")
                    await self.comms.post(Intent.escalation, body, effort_id=eid)
                    await self.comms.post(Intent.operator_reply, body,
                                          thread_id=self._mgmt_thread_of(eid))
                    await self.router.update_effort_card(eid, "needs-attention")
                    continue
                await self.audit.log("stall_recovered", effort_id=eid,
                                     payload={"reason": "worker_silent", "n": n + 1})
                # P16 — A RECOVERED TURN MUST START FROM THE LAST COMMITTED STATE. gym-014
                # (2026-07-20): a worker abandoned mid-edit leaving `tests/test_todo.py` modified
                # and the suite FAILING (2 errors). The recovery re-engaged onto that same tree, so
                # the next worker inherited a broken suite it had not caused, spent its whole turn
                # chasing it, and abandoned too — three turns lost to one partial edit. The org
                # then told the operator "something structural is blocking it… not your code",
                # which was wrong: the daemon, queue and GPU were all healthy.
                #
                # Uncommitted work from a turn that DIED is not work — it is wreckage the next
                # worker cannot distinguish from intent. Discard it. Anything committed survives,
                # which is the line that matters.
                discarded = await self._discard_uncommitted(url, eid)
                await self._reengage(
                    [eid], mgmt_channel=mgmt, mgmt_thread=self._mgmt_thread_of(eid),
                    reply_prefix=(f"🔧 **{eid}**'s worker went silent mid-turn (no progress for "
                                  f"~{int(self.s.worker_silence_s // 60)} min) — I stopped the hung "
                                  f"turn and re-engaged it. Committed work is intact"
                                  + ("; its half-finished uncommitted edits were discarded so the "
                                     "next turn starts from a clean, known state."
                                     if discarded else ".")))
        if alive:
            return                                            # a worker is genuinely progressing
        for e in await self.gate.snapshot(open_only=True):
            eid = e["id"]
            if eid.startswith("__") or eid in self._delegating or eid in parked:
                continue                                    # internal / running now / waiting on capacity
            if eid in self._handoff_waiting:
                continue        # paused on a handed-off fix — resumed by its finish (or by you)
            # P8 #2 — an effort WAITING ON A HUMAN gate is never a stall, however long it idles:
            # NO timeout may ever bypass a human gate (§4.5). Direct state check, not the fragile
            # rule-by-event-kind below (2026-07-16: `plan_drafted` was once added to the
            # mid-dispatch kinds and the watchdog auto-executed unapproved plans after 15 min).
            if self._waiting_on(eid) is not None:
                continue
            if e.get("state") == "frozen":
                # A freeze on an ENVIRONMENT/WORKSPACE symptom (not a real code deviation) is
                # something the org self-heals — re-clone + retry, bounded — instead of idling on the
                # operator (operator 2026-07-13: full infra-autonomy, notify-don't-ask). A genuine
                # work-deviation stays frozen for the human.
                await self._maybe_auto_recover_infra_freeze(eid, mgmt)
                continue                                    # (real concerns stay put — needs a decision)
            last = await self._last_event(eid)
            if last is None:
                continue
            kind, ts = last
            try:
                age = (now - datetime.fromisoformat(ts)).total_seconds()
            except Exception:  # noqa: BLE001
                continue
            if age < self.s.stall_threshold_s:
                continue
            # A blocked effort whose blocker the ORG can resolve itself (a host-context/workspace
            # limit) must not sit idle waiting on the operator — auto-route it (bounded). A blocker
            # that genuinely needs a human stays put. (operator 2026-07-11: the org does the work.)
            if kind == "effort_blocked_elevated":
                await self._try_auto_resolve_blocked(eid)
                continue
            if kind not in self._STALL_MIDDISPATCH_KINDS:
                continue    # last event is a resolution / surfaced state — correctly awaiting you
            mins = int(age // 60)
            n = await self._event_count(eid, "stall_recovered")
            if n >= self.s.stall_max_recoveries:
                await self.audit.log("stall_escalated", effort_id=eid,
                                     payload={"idle_min": mins, "recoveries": n})
                # P16 — DON'T NAME A CAUSE YOU HAVEN'T CHECKED. This used to assert "something
                # structural is blocking it (a repo, clone, or tool problem), not your code".
                # In gym-014 that was wrong on every count — the daemon answered 200, the queue had
                # free permits and zero held connections, and the GPU and containers were healthy.
                # The real cause was a half-finished edit left by an abandoned turn. Sending the
                # operator a confident wrong diagnosis is worse than sending none: it aims their
                # attention at the wrong layer. Same rule as P13.4.
                body = (f"🧰 **{eid}** stalled mid-dispatch and my {n} auto-recoveries didn't take — "
                        f"it's been silent ~{mins} min. I have NOT identified the cause. Common "
                        f"ones: a turn dying mid-edit, an unreachable worker, or a repo/clone "
                        f"problem. I've stopped auto-retrying to avoid a loop — say **“re-run it”** "
                        f"to try again, and its workspace is reset to the last committed state "
                        f"first.")
                await self.comms.post(Intent.escalation, body, effort_id=eid)
                await self.comms.post(Intent.operator_reply, body,
                                      thread_id=self._mgmt_thread_of(eid))
                await self.router.update_effort_card(eid, "needs-attention")
                continue
            await self.audit.log("stall_recovered", effort_id=eid,
                                 payload={"idle_min": mins, "n": n + 1, "last_kind": kind})
            # P16 — the SECOND recovery path, same rule: re-engaging onto a dead turn's
            # half-finished tree is what cost gym-014 three turns. No worker url is in scope here
            # (this path fires on idleness, not on a specific hung daemon), so the discard runs
            # against the effort's current workspace.
            discarded = await self._discard_uncommitted("", eid)
            await self._reengage(
                [eid], mgmt_channel=mgmt, mgmt_thread=self._mgmt_thread_of(eid),
                reply_prefix=(f"🔧 **{eid}** went quiet for ~{mins} min with no progress after a "
                              f"dispatch — a focus or step stalled without reporting. "
                              f"Auto-re-engaging it now. Committed work is intact"
                              + ("; half-finished uncommitted edits were discarded so it restarts "
                                 "from a clean, known state." if discarded else ".")))

    async def _maybe_auto_recover_infra_freeze(self, eid: str, mgmt: str | None) -> None:
        """A FROZEN effort whose concern is an ENVIRONMENT/WORKSPACE symptom (not a real code
        deviation) is self-healable — the org clears it, re-clones + retries, and just NOTIFIES,
        instead of idling on the operator (operator 2026-07-13: "fully autonomous ... send a message
        so i can see; i don't need approval"). Bounded by `infra_recovery_cap`; escalates HONESTLY
        if the re-clones don't take. A genuine work-deviation is never infra-classified, so it stays
        frozen for the human. Live origin: a corrupt leftover workspace froze the FNA→MonoGame port
        as a hard-gate 'deviation' and idled the floor ~5h waiting on a human tap it didn't need."""
        concerns = await self.gate.open_concerns(eid)
        if not concerns:
            return
        text = " ".join(
            f"{(c.payload or {}).get('what_surfaced', '')} {(c.payload or {}).get('intent_of_change', '')}"
            for c in concerns
        )
        if not _is_infra_concern(text):
            return                                      # a real deviation — stays frozen for the human
        cap = max(1, self.s.infra_recovery_cap)
        n = await self._event_count(eid, "infra_auto_recovered")
        if n >= cap:
            # Re-clones didn't take — escalate ONCE, honestly, then leave it for the human.
            if await self._event_count(eid, "infra_recovery_exhausted") == 0:
                await self.audit.log("infra_recovery_exhausted", effort_id=eid,
                                     payload={"recoveries": n})
                body = (f"🛠️ **{eid}** — I auto-re-cloned {n}× for an infra/workspace symptom and it "
                        f"still won't take, so this genuinely needs you: the environment problem looks "
                        f"structural (a repo/clone/tool issue), not the code. `approve {eid}` once it's "
                        f"sorted, or `abort {eid}`.")
                await self.comms.post(Intent.escalation, body, effort_id=eid)
                if mgmt:
                    await self.comms.post(Intent.operator_reply, body,
                                          thread_id=self._mgmt_thread_of(eid))
                await self.router.update_effort_card(eid, "needs-attention")
            return
        # AUTO-RECOVER: clear the infra hard-gate (sanctioned) → re-dispatch (the daemon re-clones a
        # void workspace fresh) → NOTIFY (visibility, not a request).
        try:
            await self.gate.clear(
                eid,
                Decision(decision="approve",
                         note="auto-recovery: infra/workspace symptom (operator-authorized 2026-07-13)"),
                actor_role="auto-recovery", infra_recovery=True,
            )
        except Exception as exc:  # noqa: BLE001 - a clear failure must not wedge the sweep
            log.warning("infra auto-recovery clear failed for %s: %s", eid, exc)
            return
        await self.audit.log("infra_auto_recovered", effort_id=eid,
                             payload={"attempt": n + 1, "cap": cap})
        await self.router.update_effort_card(eid, "active")
        note = (f"🔧 **Auto-recovering `{eid}`** — the freeze was an **infra/workspace symptom** (the "
                f"environment, not your code): re-cloning fresh + retrying (attempt {n + 1}/{cap}). "
                f"I'll only pull you in if it doesn't take.")
        await self.comms.post(Intent.worker_activity, note, effort_id=eid)
        if mgmt:
            await self.comms.post(Intent.operator_reply, note, thread_id=self._mgmt_thread_of(eid))
        await self._reengage(
            [eid], mgmt_channel=mgmt, mgmt_thread=self._mgmt_thread_of(eid),
            reply_prefix=f"↩️ Auto-resuming **{eid}** after infra recovery (fresh re-clone).")

    async def _branch_reaper_loop(self) -> None:
        """Keep the repos clean AUTOMATICALLY (operator 2026-07-11: "when a new branch replaces the
        last, just delete the last — it's abandoned; no human code is lost in an agent branch"). Each
        tick deletes SPENT/ABANDONED `agent/*` branches — merged into main, or SUPERSEDED (an older
        effort's branch with no open PR, when a newer effort's branch exists on the same repo). KEEPS
        branches with an OPEN PR (still in play — "continue if it makes sense"), the NEWEST effort's
        branch (the current work), and any in-flight effort's branch. `agent/*` only — never a human
        branch. This is the org doing its own hygiene; the operator never has to."""
        while True:
            try:
                await asyncio.sleep(self.s.branch_reaper_s)
            except asyncio.CancelledError:
                return
            try:
                await self._reap_abandoned_branches()
            except Exception as exc:  # noqa: BLE001 - the reaper must never die
                log.warning("branch reaper tick failed: %s", exc)

    async def _reap_abandoned_branches(self) -> int:
        """Delete spent/abandoned `agent/*` branches across all onboarded repos. Returns the count
        deleted. SAFE by construction: only `agent/*`, only branches with NO open PR, never the
        newest/in-flight effort's branch — and agent branches carry no human code."""
        if self.github is None or not self.s.github_app_enabled:
            return 0
        reaped = 0
        for p in await self.projects.list():
            repo = p["repo_url"]
            try:
                cls = await classify_agent_branches(
                    self.github, repo, api_base=self.s.github_api_base,
                    transport=self._gh_transport)
            except Exception as exc:  # noqa: BLE001 - one bad repo never stops the sweep
                log.debug("reaper classify %s failed: %s", repo, exc)
                continue
            dates = cls.get("dates", {})
            pr_num = cls.get("pr_num", {})
            all_agent = list(cls["merged"]) + [u["name"] for u in cls["unmerged"]] + list(cls["open_pr"])
            if not all_agent:
                continue
            # The CURRENT work = the agent branch with the MOST-RECENT commit (+ any in-flight one) —
            # keep it. Every OLDER branch is superseded ("the new replaces the last"); when its last
            # commit is also STALE it's abandoned → reap it AND close its PR, even a PR'd one (operator
            # 2026-07-12: "3 branches again, confusing; I don't know where to look" + earlier: "no
            # human code is lost in an agent branch"). A RECENT superseded branch is kept (parallel
            # live work). Merged branches are spent regardless.
            import datetime as _dt
            newest = max(all_agent, key=lambda b: dates.get(b, ""))
            merged_set = set(cls["merged"])
            stale_cutoff = (_dt.datetime.now(_dt.timezone.utc)
                            - _dt.timedelta(hours=self.s.branch_stale_hours)).strftime(
                                "%Y-%m-%dT%H:%M:%SZ")
            targets: list[tuple[str, str]] = []
            for b in all_agent:
                if b[len("agent/"):] in self._delegating:
                    continue                                   # running right now — keep
                if b in merged_set:
                    targets.append((b, "merged"))              # spent — every commit is in default
                elif b == newest:
                    continue                                   # the current work — keep
                elif dates.get(b, "") and dates[b] < stale_cutoff:
                    targets.append((b, "superseded"))          # older + abandoned → reap (+ close PR)
                # else: a RECENT superseded branch — keep (may be parallel live work)
            for b, reason in targets:
                # Close the branch's OPEN PR first (reversible — the branch is deleted next, both
                # reopenable) so no dangling superseded PR clutters "what do I check".
                if pr_num.get(b):
                    try:
                        await close_pull_request(self.github, repo, pr_num[b],
                                                 api_base=self.s.github_api_base,
                                                 transport=self._gh_transport)
                        await self.audit.log("pr_closed", payload={
                            "repo": repo, "pr": pr_num[b], "reason": f"reaper-{reason}"})
                    except Exception as exc:  # noqa: BLE001 — closing the PR never blocks the reap
                        log.debug("reaper close PR #%s failed: %s", pr_num[b], exc)
                try:
                    res = await delete_branch(self.github, repo, b,
                                              api_base=self.s.github_api_base,
                                              transport=self._gh_transport)
                except Exception as exc:  # noqa: BLE001
                    log.debug("reaper delete %s failed: %s", b, exc)
                    continue
                await self.audit.log("branch_deleted", payload={
                    "repo": repo, "branch": b, "ok": res.ok, "reason": f"reaper-{reason}"})
                reaped += 1 if res.ok else 0
                # CONSOLIDATE the EFFORT behind the reaped branch too (operator 2026-07-11: "the
                # latest change-containing branch is what we focus on; all others aren't progress").
                # A merged branch's effort is done; a superseded one's is dead — either way it should
                # drop out of /status so the board shows only the current work. Reversible (reopen).
                if res.ok:
                    await self._consolidate_effort(
                        b[len("agent/"):], "done" if reason == "merged" else "aborted")
        if reaped:
            log.info("branch reaper: deleted %d spent/abandoned agent branch(es)", reaped)
        return reaped

    async def _consolidate_effort(self, effort_id: str, lifecycle: str) -> None:
        """Drop a spent/superseded effort out of the active board (its branch was just reaped). Only
        touches an OPEN, non-frozen, non-in-flight effort — never the current work, never one paused
        on a concern. Best-effort; reversible via reopen."""
        if effort_id in self._delegating:
            return
        try:
            async with self.db.session_factory() as s:
                e = await s.get(Effort, effort_id)
                if e is None or e.lifecycle != "open" or e.state == "frozen":
                    return
            await self.gate.set_lifecycle(effort_id, lifecycle)
            await self.router.update_effort_card(
                effort_id, "done" if lifecycle == "done" else "aborted")
            await self.audit.log("effort_consolidated", effort_id=effort_id,
                                 payload={"lifecycle": lifecycle, "reason": "branch-reaped"})
        except Exception as exc:  # noqa: BLE001 - consolidation never breaks the reaper
            log.debug("consolidate effort %s failed: %s", effort_id, exc)

    async def _handle_clone_failure(self, effort_id: str, result) -> None:
        """A worker COULDN'T FOCUS its workspace (clone/setup failed) — NOT your code, NOT a worker
        bug, and NEVER something to swallow silently (operator 2026-07-10: an effort sat silent for
        2h after a focus failure only flipped its card to 'error'). Audit + tell you plainly; the
        stall watchdog re-engages it on a clean workspace shortly. Generic across projects."""
        tail = (getattr(result, "output", None) or getattr(result, "signal", None) or "")[-400:]
        await self.audit.log("focus_failed", effort_id=effort_id, payload={"detail": tail[:300]})
        body = (f"🔧 **{effort_id}** — the worker couldn't set up its workspace (clone/focus failed): "
                f"this is a workspace/clone problem, not your code or the worker, and **nothing ran**. "
                f"I'll re-engage it on a clean workspace automatically; if it keeps failing I'll raise "
                f"it loudly rather than sit quiet."
                + (f"\n```\n{tail[-280:]}\n```" if tail.strip() else ""))
        await self.comms.post(Intent.worker_activity, body, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, body,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "working")

    async def _handle_stale_workspace(self, effort_id: str, result) -> None:
        """P8 #3 — the worker verified its checkout is NOT rooted on the expected base and honestly
        STOPPED before building on dead history (the assert the org hands it in the brief — the
        worker's proxied git can't refresh a stale clone, so working on would only produce branches
        with no common ancestor to the live main: undeliverable). Drop the workspace's provenance
        claim so the next focus WIPES + re-clones off the live base; audited as `focus_failed`
        (a mid-dispatch kind) so the stall watchdog re-engages it bounded, like a clone failure."""
        self.router.invalidate_focus(effort_id)
        tail = (getattr(result, "output", None) or "")[-300:]
        exp = (self._expected_base.get(effort_id) or {}).get("sha", "")
        await self.audit.log("focus_failed", effort_id=effort_id,
                             payload={"reason": "workspace_stale",
                                      "expected_base": exp, "detail": tail})
        body = (f"🧭 **{effort_id}** — the worker checked its workspace against the expected base "
                f"(`{exp[:12] or 'unknown'}`) and found it rooted on DEAD history, so it honestly "
                f"stopped **before doing any work** (work built there could never be delivered). "
                f"I've invalidated that checkout — it re-clones fresh off the live base on the "
                f"next dispatch, which the watchdog triggers automatically. Nothing was lost.")
        await self.comms.post(Intent.worker_activity, body, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, body,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "working")

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
        self._load_pm_voice_charter()     # the operator-tunable "how the org talks to you" system prompt
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
        # Stall watchdog: the org must never sit silent after a dispatch (recovers mid-dispatch
        # wedges — a failed focus, a dead delegate). Live only (a fake harness never really stalls).
        if (self.s.chat_adapter != "fake" and getattr(self, "_stall_task", None) is None):
            self._stall_task = asyncio.create_task(self._stall_watchdog_loop())
        # Branch reaper: the org keeps its repos clean itself (deletes spent/abandoned agent/*
        # branches). Live only + when the GitHub App is present.
        if (self.s.chat_adapter != "fake" and self.s.branch_reaper_enabled
                and self.github is not None and getattr(self, "_reaper_task", None) is None):
            self._reaper_task = asyncio.create_task(self._branch_reaper_loop())

    async def aclose(self) -> None:
        """Stop the capacity drain loop (for a clean shutdown / test teardown)."""
        for attr in ("_capacity_task", "_stall_task", "_reaper_task"):
            task = getattr(self, attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                setattr(self, attr, None)

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
    async def raise_verifiable_concern(
        self, effort_id: str, trigger: Trigger, concern: Concern, *, verify_cmd: str,
        branch: str | None = None, constraint_id: str | None = None,
        actor: str = "pm", level: Level | None = None,
    ) -> Concern:
        """§11 — raise a concern that carries its own EXECUTABLE proof (the failing check), not just
        prose. The structured payload is what makes the escalation lossless end-to-end: the receiving
        tier gets the exact command that is red, and `apply_operator_decision` re-runs it before
        allowing a close, so 'resolved' is VERIFIED rather than asserted. Prose escalations are the
        paper's proven-lossy step; this is the antidote."""
        result = await self.raise_concern(effort_id, trigger, concern, actor=actor, level=level)
        try:
            async with self.db.session_factory() as s:
                rows = (await s.execute(
                    select(ConcernRow).where(ConcernRow.effort_id == effort_id,
                                             ConcernRow.status == "open"))).scalars().all()
                for c in rows:
                    p = dict(c.payload or {})
                    p.update({"verify_cmd": verify_cmd, "branch": branch,
                              "constraint_id": constraint_id})
                    c.payload = p
                await s.commit()
            await self.audit.log("escalation_verifiable", effort_id=effort_id,
                                 payload={"verify_cmd": verify_cmd[:200],
                                          "constraint_id": constraint_id})
        except Exception as exc:  # noqa: BLE001 — the freeze already happened; payload is enrichment
            log.debug("attaching verify_cmd to concern failed for %s: %s", effort_id, exc)
        return result

    async def _verifiable_concern_blocks_clear(self, effort_id: str, decision: Decision) -> str:
        """§11 FAITHFUL ESCALATION — a ticket may not close by ASSERTION. When a concern was raised
        from a failing executable check, its payload carries that check; clearing it as
        approve/modify RE-RUNS the check and refuses the clear while it is still red. Returns ''
        when the clear may proceed, else the refusal reason.

        This is the mechanism that makes escalation lossless (ORCHESTRATION-DESIGN §11): the paper's
        proven-lossy step is a concern raised and then NOT incorporated. Prose can be waved through;
        a failing test cannot. `abort` is always allowed (giving up is a legitimate decision), and an
        explicit `override` in the note is the human's escape hatch — logged, never silent."""
        if decision.decision != "approve":
            # `abort` (giving up) and `modify` (steer + RESUME work) must always pass. Blocking
            # `modify` would deadlock the ticket: the check can only go green if work resumes, and
            # work can only resume by clearing the freeze. Only `approve` — the claim that this is
            # RESOLVED — has to be earned with a passing check.
            return ""
        if "override" in (decision.note or "").lower():
            await self.audit.log("escalation_override", effort_id=effort_id,
                                 payload={"note": (decision.note or "")[:200]})
            return ""
        try:
            concerns = await self.gate.open_concerns(effort_id)
        except Exception as exc:  # noqa: BLE001 — never wedge a decision on a lookup
            log.debug("verifiable-concern lookup failed for %s: %s", effort_id, exc)
            return ""
        for c in concerns:
            cmd = ((c.payload or {}).get("verify_cmd") or "").strip() if isinstance(c.payload, dict) else ""
            if not cmd:
                continue
            # We do NOT run the check here: governance §3.0 forbids moving a frozen effort's agents
            # to computing, and acquiring a verifier slot would do exactly that. Instead we consult
            # the RECORD — a check_exec that PASSED for this command since the concern was raised.
            # The check runs during the fix round (while the effort is active); the clear consults
            # its result. Same guarantee ("verified, not asserted"), no invariant broken.
            passed = False
            try:
                async with self.db.session_factory() as s:
                    rows = (await s.execute(
                        select(Event).where(
                            Event.effort_id == effort_id, Event.kind == "check_exec",
                            Event.ts > c.created_at).order_by(Event.ts.desc()).limit(50)
                    )).scalars().all()
                for ev in rows:
                    p = ev.payload if isinstance(ev.payload, dict) else {}
                    if p.get("exit_code") == 0 and (p.get("command") or "")[:120] == cmd[:120]:
                        passed = True
                        break
            except Exception as exc:  # noqa: BLE001 — never wedge a decision on a lookup
                log.debug("verify-record lookup failed for %s: %s", effort_id, exc)
                return ""
            if not passed:
                return (f"its own check has not passed since this was raised (`{cmd}`). Re-run the "
                        f"work so the check goes green, and I'll close it on the proof")
        return ""

    async def apply_operator_decision(
        self, effort_id: str, decision: Decision, *, actor_role: str = "human"
    ) -> None:
        # §11: an escalation carrying an executable check cannot be closed while that check is red.
        blocked = await self._verifiable_concern_blocks_clear(effort_id, decision)
        if blocked:
            await self.audit.log("escalation_clear_refused", effort_id=effort_id,
                                 payload={"reason": blocked[:300]})
            await self.comms.post(
                Intent.operator_reply,
                f"⛔ I can't close **{effort_id}** yet — {blocked}\nFix it (or re-send your decision "
                f"with the word **override** to close it anyway, which I'll log).",
                effort_id=effort_id, thread_id=self._mgmt_thread_of(effort_id))
            return
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
        # CONTROL-SURFACE PARITY (live 2026-07-15, iteration-2's first gate): `approve <effort>`
        # sent through POST /nl reached the PO MODEL, which NARRATED "Approved. Dispatching…"
        # while the plan stayed `draft` — a false-ack at the operator API, because the privileged
        # command grammar was only checked on the CHAT path (handle_event). Every inlet gets the
        # same control surface: a decision/kill/slash message routes to _handle_command, exactly
        # as /nl's contract promises ("drives the org EXACTLY like a chat message"). Idempotent
        # for the chat path (handle_event already returns before nl_intake on a control message).
        _ctrl = _MENTION_RE.sub("", message).strip()
        if _CONTROL_RE.match(_ctrl):
            await self._handle_command(_ctrl, channel_id, thread_id,
                                       user_id=user_id or "operator-api")
            return
        # P19 F14-refinement — a clean stop COMMAND (`archive`/`stop`/`halt`/`cancel <id>`, the verb
        # leading and the id following, nothing else) is a real command, not off-grammar phrasing.
        # Route it straight to the abort handler — the same full stop the ask below would have told
        # the operator to type — instead of diverting a synonym of `abort` to "I didn't understand".
        _stop_cmd = _STOP_COMMAND_RE.match(_ctrl)
        if _stop_cmd:
            await self._handle_command(f"abort {_stop_cmd.group('eid')}", channel_id, thread_id,
                                       user_id=user_id or "operator-api")
            return
        # P17 F14 — stop-shaped but off-grammar. The strict path above needs the message to OPEN
        # with the verb; anything else reached a model that could silently do nothing. Answer with
        # the exact command instead of guessing, and leave an event so the drop is never invisible.
        _stop = _STOP_INTENT_RE.search(_ctrl)
        if _stop and not _DECISION_RE.match(_ctrl):
            eid = _stop.group(2)
            await self.audit.log("operator_intent_unmatched", effort_id=eid,
                                 payload={"kind": "stop", "message": _ctrl[:400]})
            await self.comms.post(
                Intent.operator_reply,
                f"🛑 That reads as a request to STOP **{eid}**, but it didn't match the command "
                f"grammar, so **I have not stopped anything**. Send exactly `abort {eid}` and I "
                f"will archive it (machine loops treat that as a full stop). If you meant "
                f"something else, say so and I'll act on it.",
                thread_id=thread_id,
            )
            return
        # PURE standing-intent statements are CONFIG, never work (live 2026-07-15, the gym
        # 'ouroboros': the model minted an effort_name from "in gym, set the standing intent: …",
        # its is_work heuristic went True, and the fall-through dispatched an effort whose
        # DELIVERABLE was the intent text — which then violated its own freshly-extracted
        # forbidden terms). A message that OPENS by setting/clearing the standing intent is
        # handled deterministically here; work requests that merely restate a rule mid-sentence
        # (the 2026-07-07 anti-eat case) don't match this shape and still dispatch.
        m_si = re.match(
            r"^\s*(?:in\s+(?P<proj>[A-Za-z0-9][\w.-]*)\s*[,:]?\s+)?(?P<verb>set|update|change|clear)"
            r"\s+(?:the\s+)?standing\s+intent\b\s*(?:for\s+(?P<proj2>[A-Za-z0-9][\w.-]*))?"
            r"\s*(?:to|:|=)?\s*(?P<text>.*)$",
            message.strip(), re.I | re.S)
        if m_si:
            named = m_si.group("proj") or m_si.group("proj2")
            p = await self.projects.resolve(named) if named else None
            if p is None:
                slug = await self.router.resolve_project_by_channel(channel_id)
                p = await self.projects.get(slug) if slug else None
            if p is not None:
                si = "" if m_si.group("verb").lower() == "clear" else m_si.group("text").strip()
                await self.projects.set_standing_intent(p["slug"], si)
                if si:
                    # ECHO THE EXTRACTED FORBIDDEN TERMS (live 2026-07-15: a backticked word
                    # after a negation silently became a diff-wide forbidden term — `harness` —
                    # and rejected deliveries that merely named it; the setter must show its
                    # blast radius so wording mistakes surface immediately).
                    forb = self._forbidden_terms(si)
                    forb_note = (
                        "\nForbidden term(s) I will reject in any diff: "
                        + ", ".join(f"`{t}`" for t in forb) + "."
                        if forb else
                        "\n(No diff-level forbidden terms — backtick a word after a negation to "
                        "block it in diffs.)")
                    body = (f"🧭 Standing intent set for **`{p['slug']}`** (config only — no "
                            f"work dispatched): _{si}_{forb_note}")
                else:
                    body = f"🧭 Cleared the standing intent on **`{p['slug']}`**."
                await self.chat.post(channel_id, body, thread_id=thread_id)
                return
            await self.chat.post(
                channel_id,
                "Which project is that standing intent for? Say `in <project>, set the standing "
                "intent: …`.",
                thread_id=thread_id)
            return
        # DURABLE ACCEPTANCE CHECK capture (ORCHESTRATION-DESIGN §10 — the finding→durable-check
        # pipeline). The operator, reviewing a delivery, turns a finding into a PERMANENT executable
        # check the org can't regress on. Deterministic + governor-issued (a governance action is not
        # left to the PO model's classification). Grammar: `accept check for <project>: <command>
        # [:: <note>]`. Config only — never dispatches work.
        m_ac = re.match(
            r"^\s*(?:add\s+(?:an?\s+)?)?accept(?:ance)?\s+check\s+(?:for|to|in|on)\s+"
            r"(?P<proj>[A-Za-z0-9][\w.-]*)\s*[:=]\s*(?P<rest>\S.*)$",
            message.strip(), re.I | re.S)
        if m_ac:
            p = await self.projects.resolve(m_ac.group("proj"))
            if p is None:
                slug = await self.router.resolve_project_by_channel(channel_id)
                p = await self.projects.get(slug) if slug else None
            if p is None:
                await self.chat.post(
                    channel_id, "Which project is that acceptance check for? Say `accept check for "
                    "<project>: <command> :: <note>`.", thread_id=thread_id)
                return
            body, _sep, note = m_ac.group("rest").strip().partition("::")
            body, note = body.strip(), (note.strip() or "operator review")
            cid = await self.projects.add_acceptance_check(
                p["slug"], note, body, created_by=user_id or "operator")
            if cid:
                await self.chat.post(
                    channel_id,
                    f"📐 Acceptance check captured for **`{p['slug']}`** (durable — every future "
                    f"delivery must pass it, or the merge is withheld):\n  `{body}`\n  _origin: {note}_",
                    thread_id=thread_id)
            else:
                await self.chat.post(
                    channel_id, "That acceptance check needs a command to run — `accept check for "
                    "<project>: <command> :: <note>`.", thread_id=thread_id)
            return
        # EXPLICIT NEW-EFFORT idiom — deterministic, immune to the board/hygiene classifiers
        # (gym finding ⑤, 2026-07-15: "start effort gym-003-…: <goal>" whose goal text mentioned
        # branches was captured WHOLE by branch hygiene and never dispatched; the anti-capture
        # guard only protected NAMED RE-RUNS). "start [a new] effort <name>: <goal>" is beyond
        # classification doubt — open it and run the normal governed intake directly. Bonus: the
        # effort id becomes exactly `effort-<name>` instead of a model-mangled slug.
        # Two accepted shapes, BOTH deterministic and BOTH immune to the hygiene classifiers:
        #   "[in <proj>,] start [a new] effort <name>: <goal>"                  (explicit separator)
        #   "start [a new] effort <name…> on [the] <proj> [project]. <goal>"    (natural prose)
        # The 2nd shape is how a human — and the gym runner — actually phrase it: a MULTI-WORD name,
        # the project named via "on the <proj> project", and NO separator before the goal (2026-07-16
        # gym finding: "start a new effort gym-004 todo-product on the gym project. …" fell through
        # the strict 1st shape, then its goal text "a clear-completed action" tripped _TIDY_RE and the
        # whole start-effort was swallowed as a board-tidy — silently dropped, never dispatched).
        # Separator between <name> and <goal> is a colon OR a SPACE-PADDED dash — never a bare dash,
        # so a hyphenated name ("gym-004", "gym-a-delete-command") is not split at its own hyphen
        # (that mis-split left proj empty → fell through to the model, mangling the slug — the other
        # half of the 2026-07-16 gym drop).
        m_ne = re.match(
            r"^\s*(?:in\s+(?P<proj>[A-Za-z0-9][\w.-]*)\s*[,:]?\s+)?start\s+(?:a\s+new\s+)?"
            r"effort\s+(?P<name>[A-Za-z0-9][\w-]{2,60})(?:\s*:\s*|\s+[-–—]\s+)(?P<goal>.+)$",
            message.strip(), re.I | re.S)
        if not m_ne:
            m_ne = re.match(
                r"^\s*start\s+(?:a\s+new\s+)?effort\s+(?P<name>[A-Za-z0-9][\w -]{1,80}?)"
                r"\s+on\s+(?:the\s+)?(?P<proj>[A-Za-z0-9][\w.-]*?)(?:\s+project)?\s*[.:]\s+"
                r"(?P<goal>\S.+)$",
                message.strip(), re.I | re.S)
        if m_ne:
            _p = None
            if m_ne.group("proj"):
                _p = await self.projects.resolve(m_ne.group("proj"))
            if _p is None:
                _slug = await self.router.resolve_project_by_channel(channel_id)
                _p = await self.projects.get(_slug) if _slug else None
            if _p is not None:
                _goal = m_ne.group("goal").strip()
                # slugify — the 2nd shape's name may be multiple words ("gym-004 todo-product")
                _ne_name = re.sub(r"[^A-Za-z0-9]+", "-",
                                  m_ne.group("name").strip()).strip("-").lower()
                eid, chan, root = await self.router.open_effort(
                    _ne_name, project=_p["slug"], goal=_goal)
                await self.chat.post(
                    channel_id,
                    f"On it — opened {self._effort_link(eid, root)} on `{_p['slug']}`; running "
                    f"readiness now.",
                    thread_id=thread_id)
                await self._intake_or_dispatch(
                    eid, chan, root, _goal, reply_prefix="",
                    mgmt_channel=channel_id, mgmt_thread=thread_id)
                return
            # no resolvable project — fall through to the model, which will ask
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
        # A re-run/reopen VERB + an EXPLICITLY NAMED effort is an unambiguous REENGAGE of that effort
        # (handled deterministically below) — it must NOT be swallowed by the board/branch HYGIENE
        # paths just because the operator's reset context mentions "clean"/"branch"/"lost"/"reset"
        # (live 2026-07-13: "re-run effort-X. CLEAN RESET, a commit was lost …" was captured by
        # branch-hygiene and never reengaged — nothing dispatched). Skip hygiene for a named re-run.
        _named_rerun = (bool(re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message))
                        and bool(_RERUN_VERB_RE.search(message)))
        # NL-FIRST "tidy up" — the umbrella board-cleanup: close COMPLETED efforts (idle + work
        # already merged into main) and delete their merged branches in one go; report-only on a
        # question. Before branch hygiene so "tidy up" (efforts + branches) isn't captured by the
        # branch-only path.
        if not _named_rerun and await self._nl_tidy_up(message, channel_id, thread_id):
            return
        # NL-FIRST branch HYGIENE — "clean up / which branches …" (NO branch named): understand the
        # repo's agent/* branches by merge state and report, or delete the already-merged ones on a
        # cleanup request (the org reasons "these were merged, they can go"). Before the named-delete
        # path since a general cleanup names no branch.
        if not _named_rerun and await self._nl_branch_hygiene(message, channel_id, thread_id):
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
        if re.search(r"\b(?:run|re-?run|retry|do)\b[^.\n]{0,90}?\b(?:in|with|using)\b"
                     r"[^.\n]{0,20}?\bhost\s+context\b", message, re.I):
            # gap widened 30→90 (live 2026-07-09: a 47-char effort id between "run" and "in"
            # made the command miss the handler and dispatch a normal standalone run)
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
        # Internal singletons (`__survey__`, `__capability__`) are org PLUMBING, not the operator's
        # work — never show them in status, never dispatch/archive them from a "get the workers
        # working" / "archive" (operator 2026-07-10: they leaked into the effort list as things to
        # dispatch). Filter here so every operator-facing path downstream (status, select, reengage,
        # archive, the model's context) sees only real efforts.
        efforts = [e for e in efforts if not (e.get("id") or "").startswith("__")]
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

        # DETERMINISTIC re-run guard: a re-run/reopen verb applied to an EXPLICITLY NAMED effort is
        # unambiguous — force reengage on that exact effort so the small model can't mis-route it
        # (live 2026-07-11: "re-run effort-fix-editor-atlas-runtime-error. It was closed … reopen it
        # …" classified as `archive` and dispatched a DIFFERENT effort; the operator's mandate is
        # reliability). The reengage handler reopens it if it's closed. Not a filter for the model's
        # own correct reengage — it only OVERRIDES a miss and pins the named effort id.
        _rerun_eff = re.search(r"\beffort-[A-Za-z0-9][\w-]*\b", message)
        if _rerun_eff and _RERUN_VERB_RE.search(message):
            _named = _rerun_eff.group(0)
            if intent.kind != "reengage" or intent.effort_id != _named:
                log.info("nl_intake: deterministic re-run override → reengage %s (model said %s)",
                         _named, intent.kind)
            intent.kind = "reengage"
            intent.effort_id = _named
            # PRESERVE THE OPERATOR'S FINDINGS (live 2026-07-12: the operator ran the editor and
            # reported "reopen effort-…, the atlas loads now but the cursor is still missing" — the
            # override reopened+re-ran but DISCARDED the verdict, re-running the stale goal so the
            # worker lost the human's runtime finding). A re-run message that carries substance
            # BEYOND the bare command is a runtime verdict / new direction — record it as steering so
            # the reengaged worker gets it on its next wake (build_context injects current_steering).
            # Strip the command verb + the effort id and keep it only if real content remains, so a
            # bare "re-run effort-X" adds no spurious steering. Best-effort — never blocks the reengage.
            _residue = re.sub(r"\beffort-[A-Za-z0-9][\w-]*\b", " ", _RERUN_VERB_RE.sub(" ", message))
            if len(re.sub(r"[\W_]", "", _residue)) >= 20:
                try:
                    await self.charters.set_steering(_named, message.strip(), actor="po")
                    log.info("nl_intake: captured operator re-run detail as steering for %s", _named)
                except Exception as exc:  # noqa: BLE001 — steering is a bonus; reengage must still fire
                    log.debug("override set_steering(%s) failed: %s", _named, exc)

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

        # PM VOICE (operator UX 2026-07-10): on a CONVERSATIONAL turn — where the reply IS the
        # deliverable — synthesize it from the full ground-truth facts in the PM's communication
        # voice (charters/pm-voice.md), rather than the small model's thin classification-byproduct
        # reply or a bare status template. This is DECOUPLED from intent classification so the model
        # does ONE job (communicate) instead of splitting attention across a ~15-kind taxonomy.
        # ACTION turns are untouched — their handlers already posted precise deterministic messages.
        # Fails SOFT: on any synthesis hiccup / GPU squeeze it keeps the existing deterministic reply.
        if intent.kind in ("status", "question") and (reply or efforts):
            facts = await self._comm_facts(efforts, status_map)
            synth = await self._pm_voice(message, facts, history)
            if synth:
                reply = synth

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
          waiting-on-you   — parked at a HUMAN gate (plan approval / clarification / merge); the
                             system working, not a wedge — an idle GPU here is CORRECT (P8 #2)
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
            elif self._waiting_on(eid) is not None:
                out[eid] = "waiting-on-you"
            elif eid in parked:
                out[eid] = "waiting-capacity"
            else:
                out[eid] = "idle"
        return out

    def _render_status(self, efforts: list[dict], status_map: dict[str, str]) -> str:
        """Honest per-effort status lines + a one-line reality check when nothing is running."""
        icon = {"running": "🟢", "paused": "⏸️", "waiting-on-you": "🙋", "waiting-capacity": "⏳",
                "idle": "⚪", "done": "✅", "aborted": "🗑️"}
        lines = []
        for e in efforts:
            st = status_map.get(e["id"], "idle")
            line = f"- `{e['id']}` — {icon.get(st, '·')} **{st}**"
            if st == "waiting-on-you":
                # P8 #2: "why is the GPU idle?" answerable in one look — name the gate + the ask.
                w = self._waiting_on(e["id"]) or {}
                line += f" ({w.get('gate', 'human gate')} — {w.get('ask', 'your decision')})"
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
        waiting = sum(1 for v in status_map.values() if v == "waiting-on-you")
        if waiting:
            body += (f"\n\n_🙋 **Waiting on you ({waiting})** — holding at a human gate, not "
                     f"stuck; an idle GPU here is the system working. Each ask is on its line "
                     f"above._")
        if running == 0 and idle and not self._advisories:
            body += (f"\n\n_⚠️ Nothing is running. {idle} effort(s) are **idle** — they will NOT "
                     f"start on their own. Say **“get the workers working”** (or name which) and I'll "
                     f"dispatch them; or **“archive”** the ones you're done with._")
        return body

    # ── PM communication voice (operator UX): synthesize, don't template ──────────
    def _load_pm_voice_charter(self) -> None:
        """Load the operator-tunable PM communication charter (how the org talks to the human).
        Editing charters/pm-voice.md changes the voice with NO code change. Falls back to a
        built-in minimal charter if the file is missing so synthesis never breaks on a bad deploy."""
        self._pm_voice_sys = _PM_VOICE_FALLBACK_SYS
        try:
            path = os.path.join(self.s.charters_dir, "pm-voice.md")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    txt = fh.read().strip()
                if txt:
                    self._pm_voice_sys = txt
        except Exception as exc:  # noqa: BLE001 - a bad file must never break boot
            log.warning("pm-voice charter load failed (using built-in fallback): %s", exc)

    async def _comm_facts(self, efforts: list[dict], status_map: dict[str, str]) -> str:
        """The GROUND-TRUTH facts block the PM voice synthesizes from — deterministic, honesty-
        preserving, and richer than the classifier sees: honest per-effort status (+ recent real
        commands), the PRs awaiting the operator's merge, anything else pending an operator
        decision, and the latest org-run build verdict per effort. The synthesis renders these
        faithfully; it never invents beyond them. Generic — no project specifics baked in."""
        parts: list[str] = [self._render_status(efforts, status_map)]
        # What NEEDS the operator — PRs held for their merge (D4) + other pending decisions.
        if self._pending_merge:
            merges = []
            for mid, e in self._pending_merge.items():
                pr = e.get("pr_number") or "?"
                repo = (e.get("repo") or "").split("github.com/")[-1] or "?"
                merges.append(f"  - `{mid}` → PR #{pr} on `{repo}` (say “merge it” to merge)")
            parts.append("PRs AWAITING YOUR MERGE (main only changes when you say so):\n"
                         + "\n".join(merges))
        pending = [*self._pending_lifecycle.keys(), *self._pending_capability.keys(),
                   *self._pending_plan.keys()]
        if pending:
            parts.append("AWAITING YOUR DECISION: " + ", ".join(f"`{p}`" for p in pending))
        # CURRENT WORK per project (operator 2026-07-11: "the latest change-containing branch is what
        # we focus on; all other branches aren't progress") — so "what do I check?" has ONE answer.
        by_proj: dict[str, dict] = {}
        for e in efforts:
            proj = e.get("project") or ""
            if not proj or e.get("lifecycle") == "aborted":
                continue
            cur = by_proj.get(proj)
            if cur is None or (e.get("updated_at") or "") > (cur.get("updated_at") or ""):
                by_proj[proj] = e
        if by_proj:
            lines = []
            for proj, e in sorted(by_proj.items()):
                eid = e["id"]
                branch = self._effort_branch(eid)
                pr = next((f"PR #{v.get('pr_number')}" for v in self._pending_merge.values()
                           if v.get("effort_id") == eid), None)
                where = (f"→ {pr} is open (say “merge it” to land it)" if pr
                         else "→ no PR yet (still in progress / not ready)")
                lines.append(f"  - `{proj}`: the CURRENT branch is `{branch}` "
                             f"({status_map.get(eid, 'idle')}) {where}")
            parts.append("CURRENT WORK — WHAT TO CHECK (the latest branch per project; older `agent/*` "
                         "branches are NOT progress and get auto-reaped):\n" + "\n".join(lines))
        # The org's OWN most-recent build verdict per open effort (the evidence layer — a green
        # build never proves a runtime symptom; the synthesis must carry that honestly).
        verdicts = await self._recent_build_verdicts([e["id"] for e in efforts])
        if verdicts:
            parts.append("LATEST ORG BUILD VERDICTS (the org's own build, not a worker claim):\n"
                         + verdicts)
        return "\n\n".join(p for p in parts if p and p.strip())

    async def _recent_build_verdicts(self, effort_ids: list[str], *, limit: int = 6) -> str:
        """Most-recent org_build_check verdict per effort (pass/fail + error count/mode), from the
        audit log — factual evidence for the voice. Empty when there's nothing to report."""
        if not effort_ids:
            return ""
        lines: list[str] = []
        try:
            async with self.db.session_factory() as s:
                for eid in effort_ids[:limit]:
                    row = (await s.execute(
                        select(Event).where(Event.kind == "org_build_check",
                                            Event.effort_id == eid)
                        .order_by(Event.ts.desc()).limit(1))).scalar_one_or_none()
                    if row is None:
                        continue
                    p = row.payload or {}
                    v, n = p.get("verdict", "?"), p.get("errors")
                    n_txt = f", {n} error(s)" if isinstance(n, int) else ""
                    lines.append(f"  - `{eid}`: **{v}**{n_txt}")
        except Exception as exc:  # noqa: BLE001 - evidence is a nicety; never break the reply
            log.debug("build-verdict lookup failed: %s", exc)
            return ""
        return "\n".join(lines)

    async def _pm_voice(self, operator_message: str, facts: str, history: str) -> str:
        """Synthesize the operator-facing reply in the PM's communication voice
        (charters/pm-voice.md) — clear, honest, leading with what matters and what needs the
        operator. DECOUPLED from intent classification: that call fills the action schema on a
        big taxonomy; THIS call does one job — communicate — so the small model isn't splitting
        attention. Faithful to the GROUND-TRUTH facts (never invents SHAs/states). Fails SOFT:
        returns "" so the caller falls back to its deterministic reply/template (a synthesis
        hiccup or GPU squeeze must never swallow the operator's turn)."""
        sys = getattr(self, "_pm_voice_sys", "") or _PM_VOICE_FALLBACK_SYS
        if not operator_message.strip():
            return ""
        user = (
            f"THE OPERATOR JUST SAID:\n{operator_message}\n\n"
            f"GROUND-TRUTH FACTS — communicate ONLY what these support; never invent beyond them:\n"
            f"{facts or '(no additional facts beyond the conversation)'}\n\n"
            f"CONVERSATION SO FAR (most recent last):\n{history or '(none)'}\n\n"
            f"Write your reply to the operator now — faithful to the facts, in your voice."
        )
        try:
            out = (await self.models.complete("pm-voice", sys, user)).strip()
        except Exception as exc:  # noqa: BLE001 - enhancement only; degrade to the deterministic reply
            log.info("pm-voice synthesis unavailable (%s) — using deterministic reply", exc)
            return ""
        if len(re.sub(r"[\W_]", "", out)) < 8:   # junk/empty → fall back
            return ""
        return out

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
                # RESTORE the gate so a capability that DIDN'T land (a transient GitHub error) can be
                # RETRIED, symmetric with the merge gate — popping it before the attempt otherwise
                # strands it ('approve <id>' → nothing pending). A terminal failure (the fork already
                # exists / auth) simply fails the retry again. Best-effort re-persist.
                self._pending_capability[action_id] = action
                try:
                    await self.pending.save(action_id, "capability",
                                            self._jsonify_pending(action))
                except Exception as exc:  # noqa: BLE001
                    log.debug("re-persist pending capability %s failed: %s", action_id, exc)
                await self.chat.post(channel_id, f"❌ {result.summary}"
                                     + (f"\n> {result.detail}" if result.detail else "")
                                     + f"\n_Kept it pending — say **“approve {action_id}”** to retry, "
                                       f"or **“abort {action_id}”** to drop it._",
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

    async def _acceptance_corpus_context(self, slug: str) -> str:
        """The project's DURABLE ACCEPTANCE CORPUS as a goal preamble — the corpus moved UPSTREAM
        (ORCHESTRATION-DESIGN §10 + alteration 1, 2026-07-17). Each entry is an executable check
        captured from an earlier human review that WILL run against this delivery and hard-gates the
        merge. Injected at PLAN time, not only enforced at delivery, because a delivery-only corpus
        makes the org build wrong, get caught, and burn a fix round: observed live in gym-007 — the
        plan omitted `reopen`, the gate caught it, and a SECOND worker turn was spent adding it. The
        checks up front turn build-wrong-then-fix into build-right-first-time (at project scale that
        is the difference between converging and thrashing). '' when the corpus is empty."""
        try:
            checks = await self.projects.list_acceptance_checks(slug)
        except Exception as exc:  # noqa: BLE001 — context enrichment must never block dispatch
            log.debug("acceptance-corpus context lookup failed for %s: %s", slug, exc)
            return ""
        if not checks:
            return ""
        listed = "\n".join(f"  - `{c['body']}`   ({c['origin_note']})" for c in checks[:20])
        more = (f"\n  …and {len(checks) - 20} more." if len(checks) > 20 else "")
        return (
            f"\n\nACCEPTANCE CORPUS for `{slug}` — {len(checks)} durable check(s) captured from "
            f"earlier human reviews of this project. Each one RUNS against your delivery and a "
            f"failure WITHHOLDS the merge, so satisfy them as part of this work EVEN IF the goal "
            f"text doesn't mention them:\n{listed}{more}\nThese are standards the org already "
            f"committed to — do not weaken or work around them; plan and build so they pass first "
            f"time.")

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
        failed-run-END escalations, derived from the audit log (no new state; deterministic across
        restarts). Gen 0 keeps the plain effort id, so healthy efforts keep workspace + session
        affinity exactly as before. Every signal here marks the END of a failed attempt (never a
        mid-run event), so the session rotates BETWEEN runs, never within one — the step→publish→
        verify wakes of a single run share a session, but a RE-RUN starts clean (live 2026-07-10:
        the atlas re-run ended in `burndown_stalled`/`check_infra_error`, which weren't counted, so
        the re-dispatch reused the rotted base session and the worker no-op'd 0 commands for 18
        min — the stateless-session law applies to re-runs too)."""
        try:
            async with self.db.session_factory() as s:
                n = int((await s.execute(
                    select(func.count()).select_from(Event).where(
                        Event.kind.in_(("effort_undelivered", "delivery_stale_head",
                                        "delivery_empty_diff", "burndown_stalled",
                                        "check_infra_error", "org_build_unverifiable",
                                        # a flail-guard kill forks a FRESH session (2026-07-14):
                                        # the flailing context is the poison — never re-enter it
                                        "flail_replanned",
                                        # an EMPTY plan reply / a plan-gate stop = a rotted or
                                        # overflowing session (live 2026-07-14: a 593KB base
                                        # session returned EMPTY on every plan turn) — the retry
                                        # and any operator re-run must start fresh
                                        "worker_plan_empty", "worker_plan_stopped",
                                        # P21 F1 — an ABANDONED turn (a per-turn deadline kill,
                                        # gym-019) rots its session the same way. Counting it here
                                        # is what makes a re-run/auto-recovery after an abandon start
                                        # in a FRESH session instead of resuming the bloated one
                                        # (which returned EMPTY twice). Same class as the atlas re-run
                                        # note below (uncounted END event → rotted-session reuse).
                                        "worker_turn_abandoned",
                                        # P10.5 PLAN/IMPLEMENT SPLIT: every drain round dispatches
                                        # a FRESH implementer. A worker asked to continue in the
                                        # session where it just declared the goal met has context
                                        # bias by construction (gym-008: it planned nothing and the
                                        # effort stranded). Counting the round here rotates the
                                        # session, so the implementer differs from the planner AND
                                        # from the previous round's implementer.
                                        "drain_round")),
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
            """A "signature" line: long enough to be distinctive AND genuinely shaped like TOOL
            OUTPUT.

            P12.2 — the old test accepted any long line containing `'`, `\\` or `/`, on the theory
            that "plain prose doesn't". English prose is full of both. On gym-010's goal that
            passed **8 of 23 lines**, among them `Beyond add/list/done,`,
            `levels (low/medium/high), due dates, …` and `edit a todo's text`. Those were then
            matched against other efforts' goals — and because the gym scenarios carry
            byte-identical goal text by design (so rounds stay comparable), every line matched and
            a first-ever run was told three prior attempts had failed at "this same error".

            A real compiler/runtime line carries structure, not just punctuation: a file:line, a
            stack frame, a tool code, or an error word next to a path/symbol."""
            s = ln.strip()
            if len(s) < 30:
                return False
            if re.search(r"[\w./\\-]+[(:]\d+[,:)]", s):        # foo.cs(42,17): / foo.py:42:
                return True
            if re.search(r"^\s*(?:at\s+\w[\w.<>]*\(|File\s+\"[^\"]+\",\s+line\s+\d+)", s):
                return True                                     # stack frame (CLR / Python)
            # Tool/compiler diagnostic codes. The bare form matters: real MSBuild output is
            # `MSB3202: The project file … was not found` with no preceding "error", and
            # `_ERROR_REPORT_RE` does not cover "was not found" — so requiring an error word here
            # would silently drop the exact class of failure this org hits most (the murder/atlas
            # gitlink breakages). 2-5 uppercase letters + 3-5 digits does not occur in prose.
            if re.search(r"\b(?:error|warning)\s+[A-Z]{1,4}\d{2,5}\b|\berror\s*:|"
                         r"\b[A-Z]{2,5}\d{3,5}\b", s):
                return True                                     # CS0246 / MSB3202 / error:
            if _ERROR_REPORT_RE.search(s) and re.search(
                    r"[\w-]+\.[A-Za-z]{1,5}\b|[\w-]+/[\w./-]+|\b\w+\.\w+\.\w+\b|'[^']+'", s):
                return True                                     # error word + path/symbol/quoted
            # A QUOTED SYMBOL INTRODUCING A DIAGNOSTIC — `'Game.OnExiting(object, EventArgs)': no
            # suitable method found to override`. Real C#/MSBuild output, and it carries none of
            # the error words in `_ERROR_REPORT_RE` ("no suitable method found" is not "cannot
            # find"). Requiring one would drop it, which is how the first cut of this filter broke
            # `test_error_report_goal_gets_verification_and_attempt_history`. Prose does not put a
            # dotted/parenthesised symbol in quotes and follow it with a colon.
            if re.search(r"'[^']*[.(][^']*'\s*:", s):
                return True
            return False

        lines = {ln.strip().lower() for ln in request.splitlines() if _sig(ln)}
        if not lines:
            return ""
        matches: list[dict] = []
        efforts = sorted(await self.gate.snapshot(open_only=False),
                         key=lambda e: e.get("updated_at") or "", reverse=True)
        for e in efforts:
            if e["id"] == effort_id or e["id"].startswith("__"):
                continue
            # P12.3 (b) — an ABORTED effort was WITHDRAWN, not attempted-and-failed. There is
            # nothing to "build on" and nothing was rejected, so offering it as a prior attempt is
            # noise. `done` stays eligible: a delivered-but-unmerged attempt is exactly the case
            # this block exists for. (gym-010 listed `effort-gym-004d-todo-product`, which is
            # aborted — this scan passes `open_only=False`, so lifecycle was never consulted.)
            if (e.get("lifecycle") or "").lower() == "aborted":
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
        # P12.3 (a) — AN UNREACHABLE BRANCH DISQUALIFIES AN EFFORT; it does not merely reword it.
        # Previously a missing branch just changed the text to "never reached", left the entry in
        # the list, and still emitted "First fetch and READ those branches". That inverts the
        # meaning of absence: there is nothing to read. It also makes an arena wipe SELF-ARMING —
        # deleting agent branches is exactly what makes every prior effort look like an
        # unpublished failed attempt (gym-010: all three listed branches had just been deleted, and
        # the worker duly fetched them, failed, and then adopted `agent/effort-gym-008-todo-product`
        # as its own working branch).
        entries: list[str] = []
        for e in matches:
            branch = self._effort_branch(e["id"])
            repo = await self._effort_repo(e["id"]) or ""
            if not (repo and self.github is not None and self.s.github_app_enabled):
                continue          # cannot verify reachability ⇒ cannot claim it is worth reading
            try:
                d = await read_branch_delivery(
                    self.github, repo, branch,
                    api_base=self.s.github_api_base, transport=self._gh_transport)
            except Exception:  # noqa: BLE001
                continue
            if not (d.verifiable and d.exists):
                continue          # deleted / never pushed / unverifiable ⇒ nothing to build on
            nf = d.files_changed if d.files_changed >= 0 else "?"
            entries.append(
                f"- `{e['id']}` (project `{e.get('project') or '?'}`): branch `{branch}` on "
                f"`{self._norm_repo(repo)}` — {d.ahead} commit(s), {nf} file(s) changed, UNMERGED")
        if not entries:
            return ""             # a header with an empty list is worse than no header
        out = ["\n\nPRIOR ATTEMPTS AT THIS SAME ERROR (the operator reports it AGAIN — nothing "
               "delivered so far resolved it):"]
        out += entries
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
        2026-07-07: a hallucinated no-op skipped the whole check stack). For a BEHAVIORAL goal (a
        runtime/interaction/visual symptom): only when the ORG ITSELF has observed the reproduction
        go RED on the pre-fix state and GREEN on the fix (`_repro_red_green`, the org-run harness)
        — 'no changes' on a LIVE symptom means the symptom is unaddressed, so nothing was fixed,
        and a worker's PROSE (`REPRO:` + `AFTER: PASS` markers) can never close it (P8 #4,
        2026-07-16 gym: exactly those markers closed gym-004b while the audit read
        `effort_reproduction_verified: 0`; earlier live 2026-07-11: a NO-CHANGES auto-iteration of
        an already-`delivery_runtime_unverified` atlas fix was falsely closed 'done — verified').
        General rule: NO WORKER SENTENCE MAY CAUSE A STATE CHANGE. When the org can't run the
        reproduction, the honest outcome is the "not verified — needs your runtime check" hold,
        which is a GOOD outcome, not a failure. This mirrors `_finish_effort`'s runtime-symptom
        gate so the bar is identical whether or not a branch landed. Generic across toolchains."""
        try:
            _, goal, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal = ""
        goal = goal or ""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        check_owner = host[0] if host else proj
        cp = await self.projects.get(check_owner) if check_owner else None
        behavioral = bool(self._runtime_symptom_phrase(goal))
        demands_proof = (
            "REQUIRED VERIFICATION" in goal or bool((cp or {}).get("check_cmd")) or behavioral)
        if not demands_proof:
            return True   # a real read-only task — NO CHANGES is the legitimate outcome
        # A behavioral-symptom goal: doing nothing never fixes a live symptom, so 'no changes' is a
        # done ONLY if the ORG has independently proven the symptom no longer reproduces — the
        # org-run RED→GREEN harness (`_org_reproduction_verified` sets `_repro_red_green` to the
        # org-verified head). The worker's own `REPRO:`/`AFTER: PASS` prose is NOT proof (P8 #4).
        if behavioral:
            _rg = self._repro_red_green.get(effort_id)
            return bool(_rg) and _rg == self._org_verified.get(effort_id)
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
        # Strip ONLY the leading `git …` segments and keep the remainder VERBATIM — a naive
        # split-and-rejoin corrupted quoted sub-shells (live 2026-07-09: the editor smoke's
        # `sh -c '…; …'` was split on the inner `;` and rejoined with `&&`, producing
        # "Unterminated quoted string" on every check round).
        parts = re.split(r"(\s*(?:&&|;)\s*)", check_cmd)
        i = 0
        while i < len(parts) and parts[i].strip().lower().startswith("git "):
            i += 2   # skip the segment and its trailing delimiter
        rest = "".join(parts[i:]).strip()
        return rest if rest else check_cmd

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
                                              "log": dout[-6000:]})
                return (f"\n🧪 **Composition check passed** on `{host_slug}` (`{check_cmd}`, "
                        f"org-run, exit 0) — the wiring branch builds.")
            if not timed_out and exit_code is not None and _is_infra_failure(dout):
                # the CHECK's own environment failed (proxy/clone/tool/path), not the code —
                # surface it, don't burn-down (2026-07-10).
                await self.audit.log("check_infra_error", effort_id=effort_id,
                                     payload={"owner": host_slug, "log": dout[-1500:]})
                await self._elevate_check_infra(effort_id, dout)
                return (f"\n🧰 **Composition check couldn't run** on `{host_slug}` — an environment "
                        f"problem (not your code); left open for attention, no burn-down.")
            if not timed_out and exit_code is not None:
                n = _error_count(dout)
                tail = "\n".join(_error_lines(dout)[:12]) or dout[-600:]
                self._comp_check_failed.add(effort_id)
                await self.audit.log("org_build_check", effort_id=effort_id,
                                     payload={"verdict": "fail", "errors": n, "owner": host_slug,
                                              "mode": "exec", "cmd": check_cmd[:200],
                                              "log": dout[-6000:]})
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
                                          "log": out[-6000:]})
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
        # The corpus UPSTREAM (alteration 1): the durable checks are visible while PLANNING, so the
        # work is built to pass them first time instead of being caught at the delivery gate.
        if proj_slug and "ACCEPTANCE CORPUS" not in request:
            request += await self._acceptance_corpus_context(proj_slug)
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
                    f"\n\nHow this gets checked: I'll build and run `{ccmd}` on `{check_owner}` "
                    f"myself to confirm it actually works before anything merges — so build and "
                    f"try it in your workspace as you go, to catch problems early. (The check is "
                    f"there to catch real breakage, not something to satisfy by any means — a "
                    f"correct fix that leaves a little to do beats a green one that dropped "
                    f"something.)"
                )
        # ERROR-REPORT convergence (live 2026-07-05: the same build error was re-reported after
        # every attempt): the goal must (a) require the worker to reproduce → fix → RE-VERIFY the
        # reported failure, and (b) carry what prior attempts already delivered, so the next
        # worker builds on them instead of re-deriving (or repeating a rejected approach).
        # BEHAVIORAL goal FIRST (operator 2026-07-10 trust ladder): a runtime/interaction symptom is
        # proven by a REPRODUCTION test (fails on the break, passes on the fix, wired into the check),
        # not by a build. This supersedes the build-oriented verify clause for these goals — a green
        # build never proves a runtime symptom fixed, and it's where 90% of the false "done"s came from.
        if self._runtime_symptom_phrase(request) and "REPRO:" not in request:
            request += _REPRO_CLAUSE
            if "PRIOR ATTEMPTS" not in request:
                request += await self._attempt_history(effort_id, request)
            if proj_slug:
                await self._derive_check_cmd(proj_slug, request, proj_channel, root)
        elif _ERROR_REPORT_RE.search(request) and request.count("\n") >= 2:
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
                "questions": questions, "asked_at": _now_iso(),
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

    async def _plan_auto_approvable(self, effort_id: str) -> bool:
        """P21 F2b — is a time-boxed AUTONOMOUS WINDOW active, and is THIS plan's risk one it may
        auto-clear? The window (`plan_auto_approve_until`) is a Human-Operator grant. FIREWALL, per
        the research alignment check (§1 / governance §3 / the paper's dropped-signal rule):
        - Only a `cascading_refactor` plan — a WIDE but REVERSIBLE dev change — is auto-approvable.
        - `irreversible` and `cross_effort` risk are NEVER auto-approved (they stay human).
        - This only clears the Stage-3 PLAN-PROCEED gate; §3 hard-gates (refusal / ethics / a real
          concern) freeze the effort on a DIFFERENT path this never reaches, and merge-to-main (D4)
          is a separate human gate this never touches.
        - Fails SAFE (→ human) on an empty/expired/unparseable window. Audited on use."""
        until = (self.s.plan_auto_approve_until or "").strip()
        if not until:
            return False
        from datetime import datetime, timezone
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(until):
                return False                                   # window expired → fail safe (human)
        except Exception:  # noqa: BLE001
            return False                                       # unparseable → fail safe (human)
        return (await self._effort_risk_str(effort_id)) == "cascading_refactor"

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
            "asked_at": _now_iso(),
        }
        await self.pending.save(effort_id, "effort_plan",
                                self._jsonify_pending(self._pending_plan[effort_id]))
        # P21 F2b — inside an active autonomous window, a DEV-scale (`cascading_refactor`) plan
        # auto-proceeds instead of idling for a human tap (gym-019 lost ~5h here). Firewalled: only
        # cascading_refactor (never irreversible/cross_effort/a hard-gate), and merge-to-main stays
        # human. Audited. Off by default — the window is a Human-Operator grant.
        if await self._plan_auto_approvable(effort_id):
            await self.audit.log("plan_auto_approved", effort_id=effort_id,
                                 payload={"window_until": self.s.plan_auto_approve_until,
                                          "risk": "cascading_refactor"})
            await self.chat.post(
                mgmt_channel,
                (f"{reply_prefix}\n\n📋 Plan for `{effort_id}` **auto-approved** under the active "
                 f"autonomous window (dev-scale `cascading_refactor` — reversible; `main` still only "
                 f"changes when you merge). `abort {effort_id}` to stop it.").strip(),
                thread_id=mgmt_thread)
            await self.approve_effort_plan(effort_id)
            return
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
                f"You're working in `{host_slug}` — the full project with all its submodules "
                f"present, so the build actually runs here. The work is on the vendored `{proj}` "
                f"at `{sub_path}`.\n\n"
                f"{goal}\n\n"
                f"THIS IS ONE ROUND OF A MULTI-ROUND BURN-DOWN. Progress is carried on the `{branch}` "
                f"branch of `{proj}`, so FIRST continue any prior rounds' work — inside the submodule:\n"
                f"  cd {sub_path} && (git fetch origin {branch} && git checkout {branch} || "
                f"git checkout -b {branch})\n"
                f"(checks out `{branch}` with prior progress if it exists; creates it otherwise.)\n\n"
                f"Then edit the files under `{sub_path}` and build/check with `{build_line}` from "
                f"/workspace.\n\n"
                f"NEVER DELETE A FEATURE TO GET PAST AN ERROR. Do not delete or gut a file, class or "
                f"method just because it doesn't compile — that is not a port, and I will REJECT the "
                f"delivery and make you redo it. (This has already happened once: a round 'went green' "
                f"by deleting the editor's CURSOR — `MouseCursor.Sdl.cs` — which broke a critical part "
                f"of a working editor.) PORT each FNA construct to its MonoGame equivalent instead. If "
                f"some API genuinely has NO MonoGame equivalent, SAY SO and stop — do not drop it.\n\n"
                f"You do NOT need a fully green build in this single round. Make as much CORRECT "
                f"progress as you can, then — ALWAYS, even if compile errors still remain — commit and "
                f"push it so the next round continues from it and the build can measure the remaining "
                f"errors. Never leave work uncommitted. Publish ONLY the `{proj}` change to ITS OWN "
                f"remote, from INSIDE the submodule directory:\n"
                f"  cd {sub_path} && git add -A && git commit -m \"{effort_id}: round progress\" && "
                f"git push origin {branch}\n"
                f"CRITICAL — do NOT commit or push at the `{host_slug}` (engine) ROOT, and NEVER "
                f"push to `main`/`master`. You are ONLY delivering the `{proj}` work on its `{branch}` "
                f"branch. The engine's submodule pointer is bumped by the org on an operator-approved "
                f"PR — not by you. (A push to the engine root or to main will be refused, by design.)\n"
                f"Then report the CURRENT error count (or the passing build) and the commit hash. "
                f"Don't fake a pass — say what's blocking you if you're stuck."
            )
            await self.comms.post(
                Intent.effort_dispatch,
                f"▶ Re-running **{effort_id}** in the **host context** (`{host_slug}`, recursive) "
                f"so the build can actually run — editing `{sub_path}` in place.",
                effort_id=effort_id)
            # FRESH session — a re-contexted workspace is a NEW task (live 2026-07-09: the
            # host wake inherited the rotted work session and no-op'd in 3 seconds; the
            # stateless-session law applies to every re-context, not just burn-down rounds).
            self._verify_seq += 1
            host_session = f"{effort_id}~host{self._verify_seq}"
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=host_session, instruction=instruction,
                repo=host_url, repo_token=host_token, recurse_submodules=True,
            )
        finally:
            self._delegating.discard(effort_id)
        if result is None:
            return
        blk = self._extract_blocker(result.output or "")
        if blk and not await self._route_escalation(effort_id, result.output or ""):
            await self._elevate_blocker(effort_id, blk)
            return
        # The worker pushed the fix to the VENDORED repo's own remote — verify + gate + finish.
        sub_repo = await self._effort_repo(effort_id)
        delivery = await self._verify_delivery(effort_id, sub_repo) if sub_repo else BranchDelivery()
        if not delivery.landed:
            # THE PM ITERATES AND HELPS — it does not idle on "reply to re-run" (operator 2026-07-14:
            # "the purpose of the orchestration is to iterate and continue … when the worker doesn't
            # know how to progress, the bot-pm will help with an alternative approach").
            # LIVE CAUSE: on a heavy port the worker burns its ENTIRE turn fixing errors and ends
            # (stopReason=stop) before it ever reaches the commit+push — 14 real FNA→MonoGame edits sat
            # stranded and uncommitted, so no delivery landed, the build check never ran, and the
            # burn-down loop never engaged. Re-running just repeats that. The PM's alternative approach
            # is to PUBLISH the worker's stranded progress ITSELF (deterministic exec, no model in the
            # loop, no re-clone), which lands the delivery and hands off to the burn-down.
            delivery = await self._publish_stranded_work(
                effort_id, sub_repo, sub_path, branch, host_session)
        if not delivery.landed:
            await self.comms.post(
                Intent.operator_reply,
                f"⚠️ **{effort_id}** ran in the host context but landed no branch on "
                f"`{self._norm_repo(sub_repo or proj)}`, and it left no work I could publish for it "
                f"— so the round genuinely produced nothing. Not done; say “re-run it” to try again.",
                thread_id=self._mgmt_thread_of(effort_id))
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        if not await self._gate_standing_intent(effort_id, channel_id, root, sub_repo, delivery):
            return
        await self._finish_effort(effort_id, result, delivery=delivery)

    async def _publish_stranded_work(
        self, effort_id: str, sub_repo: str | None, sub_path: str, branch: str, session_id: str,
    ) -> BranchDelivery:
        """PM ALTERNATIVE APPROACH — publish the worker's STRANDED progress for it.

        On a heavy port the worker spends its ENTIRE turn fixing compile errors and its turn ends
        (stopReason=stop) before it ever runs commit+push — so real work sits UNCOMMITTED, no branch
        lands, the org's build check never runs, and the burn-down loop never engages (live 2026-07-14:
        28 min of work, 14 modified FNA→MonoGame files, local branch created, never pushed). Simply
        re-running reproduces that exactly. So the PM finishes the step the worker couldn't reach.

        DETERMINISTIC by construction: no model in the loop (a bloated session can't no-op it), and
        `repo=None` means `exec_check` does NOT focus/clone — it runs in the worker's EXISTING
        workspace, so the stranded work survives. Affinity on `session_id` returns the very worker
        holding it. Best-effort: any failure just leaves the delivery unlanded and the round escalates
        honestly. Returns the RE-VERIFIED delivery."""
        if not sub_repo:
            return BranchDelivery()
        # NEVER PUBLISH DESTRUCTION. The PM finishing the worker's job must not also ship the worker's
        # shortcut: if the stranded work DELETES tracked files, refuse outright (live 2026-07-14: the
        # worker deleted the editor CURSOR to get past FNA errors and the PM published it — the exact
        # delete-to-pass the operator has now flagged twice). Deletions are checked BEFORE `git add`.
        cmd = (
            f"cd {sub_path} 2>/dev/null || exit 90; "
            f"DEL=$(git status --porcelain 2>/dev/null | grep -E '^( D|D |AD)' | cut -c4- | head -20); "
            f"if [ -n \"$DEL\" ]; then echo PM_PUBLISH_REFUSED_DELETIONS; echo \"$DEL\"; exit 0; fi; "
            f"git add -A 2>&1; "
            f"if git diff --cached --quiet 2>/dev/null; then echo PM_PUBLISH_NOTHING_TO_COMMIT; "
            f"else git commit -m \"{effort_id}: round progress (published by the PM — the worker's "
            f"turn ended before it could commit)\" 2>&1; fi; "
            # ONE bounded push retry: a transient 'unable to access' (live 2026-07-15: a single
            # GitHub hiccup) must not convert a committed round into "produced nothing" — the
            # manual re-push seconds later succeeded. The re-verify below stays the arbiter.
            f"git push origin {branch} 2>&1 || {{ echo PM_PUSH_RETRY; sleep 8; "
            f"git push origin {branch} 2>&1; }}; echo PM_PUBLISH_DONE"
        )
        try:
            exit_code, out, timed_out = await self.router.exec_check(
                effort_id, command=cmd, session_id=session_id, repo=None, timeout=300)
        except Exception as exc:  # noqa: BLE001 — salvage is best-effort; never wedge the round
            log.warning("PM publish-stranded-work failed for %s: %s", effort_id, exc)
            return BranchDelivery()
        if "PM_PUBLISH_REFUSED_DELETIONS" in (out or ""):
            deleted = [ln.strip() for ln in (out or "").splitlines()
                       if ln.strip() and not ln.strip().startswith("PM_PUBLISH")][:12]
            listed = ", ".join(f"`{d}`" for d in deleted) or "source file(s)"
            await self.audit.log("pm_publish_refused_removals", effort_id=effort_id,
                                 payload={"deleted": deleted})
            await self.comms.post(
                Intent.worker_activity,
                f"⛔ The worker's unfinished round DELETES {listed} — I will **not** publish that. "
                f"Deleting a feature to get past a compile error is not a port. Re-driving it to "
                f"RESTORE and PORT them instead.",
                effort_id=effort_id,
            )
            await self._auto_iterate(
                effort_id,
                f"the round DELETED {listed} instead of porting them",
                f"Your last round DELETED: {listed}\nThose are FEATURES (e.g. the editor CURSOR), "
                f"not errors — and I did NOT publish that work. RESTORE them and PORT them to their "
                f"MonoGame equivalents. If an API genuinely has NO MonoGame equivalent, SAY SO and "
                f"stop — never delete a feature to make the build green.",
            )
            return BranchDelivery()
        tail = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", (out or ""))[-400:]
        await self.audit.log(
            "pm_published_stranded_work", effort_id=effort_id,
            payload={"branch": branch, "exit_code": exit_code, "timed_out": timed_out,
                     "nothing_to_commit": "PM_PUBLISH_NOTHING_TO_COMMIT" in (out or ""),
                     "tail": tail[:300]},
        )
        delivery = await self._verify_delivery(effort_id, sub_repo)
        if delivery.landed:
            await self.comms.post(
                Intent.worker_activity,
                f"🧰 The worker ran out of turn mid-port with its work still uncommitted, so I "
                f"published its progress for it — `{branch}` is now on `{self._norm_repo(sub_repo)}`. "
                f"Picking the burn-down up from there; no action needed.",
                effort_id=effort_id,
            )
        return delivery

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

    async def _is_aborted(self, effort_id: str) -> bool:
        """The operator ARCHIVED this effort — machine loops must treat that as a full stop."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
        return bool(e is not None and e.lifecycle == "aborted")

    async def _reopen_if_closed(self, effort_id: str) -> None:
        # NEVER machine-resurrect an operator ABORT (live 2026-07-14: an archived effort's
        # queued flail-replan re-dispatched, its burn-down REOPENED it, and a zombie loop ground
        # rounds on a wrong branch for an hour). `done` may reopen (convergent re-reports);
        # `aborted` reopens only through the operator's own explicit re-run/re-report path.
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e is not None and e.lifecycle == "done":
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
        # ABORTED IS FINAL for machine loops (live 2026-07-14: a queued flail-replan re-dispatch
        # ran AFTER the operator archived the effort — a zombie worked a wrong branch for an
        # hour). Auto-iterate/flail/burn-down/park-drain re-entries all pass through here; only
        # the operator's own re-run (which reopens the effort first) may dispatch it again.
        if await self._is_aborted(effort_id):
            await self.audit.log("aborted_dispatch_suppressed", effort_id=effort_id)
            log.info("delegate: %s is archived (operator abort) — not dispatching", effort_id)
            return
        self._delegating.add(effort_id)   # honest "work is happening now" marker
        # A (re-)dispatch supersedes a handoff wait — the operator's manual re-run must never be
        # ignored because a stale wait marker says the effort is still paused on a fix.
        self._handoff_waiting.discard(effort_id)
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
            # P8 #3 — PROVENANCE: read the CURRENT default-branch head — the expected BASE this
            # run is planned against. Refreshed every dispatch (never carried stale); unreadable
            # ⇒ absent, and nothing downstream claims a base it can't prove.
            self._expected_base.pop(effort_id, None)
            if repo and self.github is not None and self.s.github_app_enabled:
                try:
                    bb = await read_default_branch_head(
                        self.github, repo, api_base=self.s.github_api_base,
                        transport=self._gh_transport)
                except Exception as exc:  # noqa: BLE001 — best-effort; never wedge a dispatch
                    log.debug("expected-base read failed for %s: %s", effort_id, exc)
                    bb = None
                if bb:
                    self._expected_base[effort_id] = {"branch": bb[0], "sha": bb[1]}
            # a fresh dispatch invalidates any prior org-verified verdict (new work, new head)
            self._org_verified.pop(effort_id, None)
            self._repro_red_green.pop(effort_id, None)
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
            # WORKER-SIDE PLAN GATE (operator 2026-07-14: "plan mode could be used to ensure
            # alignment to the task ... save wasted time working on the wrong thing"). Before any
            # code changes, the worker plans in a READ-ONLY turn (edit/write excluded — headless
            # plan mode) in its OWN session; the PM checks the plan against the goal. Misaligned →
            # one revision with the reason → still misaligned → honest stop BEFORE wasted work.
            # The approved plan stays in the session, so execution continues from it.
            if await self._worker_plan_required(effort_id):
                approved_plan = await self._worker_plan_gate(
                    effort_id, channel_id, root_post_id, goal, repo, repo_token,
                    upstream, upstream_token)
                if approved_plan is None:
                    return          # blocked/steered/escalated — the gate posted the state
                # P17 F16 — the plan is CARRIED, not remembered. It was written in its own `~plan`
                # session (so it was observed rather than recalled), which means this session has
                # never seen it. Quoting it here is what lets those two steps have different
                # sessions at all; "your plan (previous turn in this session)" silently degraded to
                # "whatever this session happens to recall" the moment they diverged.
                steps[0] += (
                    "\n\nYour plan below was REVIEWED and APPROVED — execute exactly that plan "
                    "now. It was written in a separate read-only turn, so re-read any file you "
                    "need rather than assuming what is in front of you.\n\n"
                    f"--- APPROVED PLAN ---\n{approved_plan.strip()[:4000]}\n--- END PLAN ---")
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
                # P17 F13/F2 — a delivery that ORPHANS the head we last published has thrown work
                # away. Never close on it; hand it to the human with the sha that went missing.
                await self._check_test_count_regression(effort_id)
                orphaned = await self._delivery_orphans_previous_head(effort_id, repo, delivery)
                if orphaned:
                    await self.audit.log(
                        "delivery_orphans_head", effort_id=effort_id,
                        payload={"previous_head": orphaned, "new_head": delivery.head_sha,
                                 "branch": delivery.branch})
                    msg = (
                        f"⛔ **{effort_id}** — the new delivery **does not descend from the work "
                        f"already published**. `{delivery.head_sha[:8]}` does not have "
                        f"`{orphaned[:8]}` in its history, so everything committed since that "
                        f"point is missing from this branch.\n\n"
                        f"This is what a stale worker workspace looks like: the base-sha check "
                        f"still passes (the base IS in history) while several commits of "
                        f"delivered work are silently dropped. **I have not closed this effort.** "
                        f"The published branch `{delivery.branch}` needs a human look before "
                        f"anything merges.")
                    await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
                    await self.comms.post(Intent.operator_reply, msg,
                                          thread_id=self._mgmt_thread_of(effort_id))
                    await self.router.update_effort_card(effort_id, "needs-attention")
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
            if effort_id in self._route_host_after:
                # workspace-sufficiency re-route: the standalone run delivered nothing on a
                # vendored+checked project — the work re-runs where it can actually build
                self._route_host_after.discard(effort_id)
                self._spawn(self._run_in_host_context(effort_id))
            elif burn is not None:
                # the burn-down supersedes a queued auto-iteration — it is the stronger loop
                # (progress-based, org-verified every round) and owns the same failing output
                self._spawn(self._burndown_loop(effort_id, burn))
            elif nxt:
                self._spawn(self.delegate(effort_id, channel_id, root_post_id, nxt))

    async def _run_step(self, effort_id, channel_id, root, step, i, n, repo, heavy, repo_token=None,
                        upstream=None, upstream_token=None):
        """Run one plan step = one checkpoint. Returns the WorkResult to continue, or None to STOP
        (the failure/flag/deviation handler has already posted + frozen where required)."""
        # P13.5 — ABORT MUST BITE MID-RUN, NOT ONLY AT ENTRY. `delegate` checks `_is_aborted` when
        # it starts, but an abort issued WHILE a delegate is running was never re-read, so the
        # in-flight call walked its remaining steps regardless. gym-010: aborted at ~14:11, then
        # `worker_acquire` at 14:15, 14:20, 14:24, 14:27… — roughly 19 minutes of worker turns on
        # an effort the operator had stopped, with no `aborted_dispatch_suppressed` until the drain
        # path happened to check. Every step is a dispatch; every dispatch re-reads the abort.
        if await self._is_aborted(effort_id):
            await self.audit.log("aborted_dispatch_suppressed", effort_id=effort_id,
                                 payload={"stage": "run_step", "step": i})
            return None
        header = f"▶ **step {i}/{n}**: {step[:180]}" if n > 1 else "⏳ worker dispatched. Working…"
        await self.comms.post(Intent.effort_dispatch, header, effort_id=effort_id)
        cp_id = f"{effort_id}:cp{i}"
        if heavy:  # P4.1: the enforced halt exists as a Checkpoint row, independent of plan markers
            await self.stop_gates.add_checkpoint(cp_id, effort_id, f"step {i}", i)
        # Cross-effort DEBUG HANDOFF protocol rides on every step wake (operator 2026-07-14): a
        # worker blocked by FOREIGN code must report it (marker + debug log), never work around it.
        # Injected per-wake — the goal itself stays clean — and only when the org actually has a
        # sibling project to hand off TO.
        instruction = step
        if self.s.handoff_enabled and "HANDOFF PROTOCOL" not in instruction:
            instruction += await self._handoff_protocol_context(effort_id)
        # ALTERATION 1 — the corpus UPSTREAM: the durable acceptance checks ride the FIRST coding
        # step so the worker builds to pass them, instead of being caught at the delivery gate and
        # burning a fix round (gym-007, live). Once per effort — later steps already have it in
        # session context.
        if i == 1 and "ACCEPTANCE CORPUS" not in instruction:
            _proj = await self._effort_project(effort_id)
            if _proj:
                instruction += await self._acceptance_corpus_context(_proj)
        # CDCL (§5–6): a re-dispatch after a failed round carries the clause set, so the retry starts
        # from the NARROWED search space instead of rediscovering the same dead ends.
        if i == 1 and "LEARNED CONSTRAINTS" not in instruction:
            instruction += await self._constraints_context(effort_id)
        # P8 #3 — PROVENANCE: the first coding step carries the expected base + the assert-before-
        # work demand (the worker can't discover the live base itself — proxied git); every focused
        # wake keys workspace reuse on that base, so a moved base re-clones instead of resuming
        # dead history.
        eb = self._expected_base.get(effort_id) or {}
        if (i == 1 and repo and eb.get("sha")
                and "WORKSPACE PROVENANCE" not in instruction):
            instruction += _PROVENANCE_CLAUSE.format(
                sha=eb["sha"], branch=eb.get("branch") or "main", sha12=eb["sha"][:12])
        # P8 #5 — ORIENTATION ARTIFACT: a wiped workspace must not mean a BLIND worker (a fresh
        # clone once burned 26 read-only calls re-discovering a tiny template and tripped the
        # flail guard). Hand the worker the org's cached survey of this codebase, keyed by the
        # base commit: same base ⇒ a map lookup shared across efforts; base moved ⇒ one
        # re-survey. Best-effort — no map, no harm.
        if i == 1 and repo and "PROJECT ORIENTATION" not in instruction:
            try:
                _proj = await self._effort_project(effort_id)
                omap = await self.project_context.ensure(
                    _proj or "", repo, base_sha=eb.get("sha", ""))
            except Exception as exc:  # noqa: BLE001 — orientation is a bonus, never a blocker
                log.debug("orientation map for %s failed: %s", effort_id, exc)
                omap = ""
            if omap:
                instruction += (
                    "\n\nPROJECT ORIENTATION (the org's cached survey of this codebase at your "
                    "base — orient from THIS instead of re-exploring from zero; verify only the "
                    "files you actually touch):\n" + omap)
        result = await self.router.wake(
            effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
            session_id=await self._session_for(effort_id), instruction=instruction, repo=repo, repo_token=repo_token,
            upstream=upstream, upstream_token=upstream_token,
            # arm the daemon's read-without-edit watchdog on CODING turns. Off only to MEASURE the
            # fork's effect on product quality (P9 Phase 0) — see `worker_flail_guard`.
            flail_guard=self.s.worker_flail_guard,
            expected_base=eb.get("sha") or None,
        )
        # FLAIL GUARD tripped (operator 2026-07-14: "too many thinking turns or time iterating on
        # read without editing anything is a good indicator to stop, fork from original user
        # prompt and re-ask in plan mode"). The daemon killed a turn that kept reading without a
        # single edit — the context itself is the poison, so don't retry INTO it: fork a fresh
        # session from the original goal and re-enter through the plan gate. Bounded to once.
        if result is not None and _FLAIL_MARKER in (result.output or ""):
            await self._flail_replan(effort_id, result)
            return None
        # P8 #3 — the worker asserted its checkout is NOT rooted on the expected base and honestly
        # stopped. Refuse to act on unprovenanced state: drop the workspace's provenance claim (the
        # next focus wipes + re-clones off the live base) and surface it; the stall watchdog
        # re-engages it bounded (focus_failed is a mid-dispatch kind), exactly like a clone failure.
        if result is not None and _STALE_MARKER in (result.output or ""):
            await self._handle_stale_workspace(effort_id, result)
            return None
        if result is None:
            await self._report_completion(effort_id, None)
            return None
        if not result.ok:
            # A CLONE failure (couldn't focus the worker) is NOT a worker failure — but it must be
            # surfaced LOUDLY (operator 2026-07-10: this path only flipped the card to 'error' and an
            # effort sat silent for 2h). Escalate to the operator's conversation + audit `focus_failed`
            # so the stall watchdog re-engages it on a clean workspace, never leaving it stranded.
            if result.status == "clone_failed":
                await self._handle_clone_failure(effort_id, result)
                return None
            # If the worker's OWN inference was shed by the saturated GPU, that's backpressure — PARK
            # + auto-resume (raise so delegate parks this step), NOT a worker failure to escalate.
            if is_backpressure_text(getattr(result, "output", None)):
                raise ModelBackpressureError(f"worker inference shed: {(result.output or '')[:160]}")
            await self._escalate_worker_failure(effort_id, result)
            return None
        # Cross-effort DEBUG HANDOFF (operator 2026-07-14): the worker is BLOCKED by a bug in
        # ANOTHER project's code — this turn is a bug report, not a deliverable. Route the debug
        # log to the owning project's worker and pause this effort until the fix lands.
        if self.s.handoff_enabled:
            ho = _parse_handoff(result.output or "")
            if ho:
                await self._open_handoff(effort_id, channel_id, root, ho, repo)
                return None
        # P18 F18 — a turn that CLAIMS a verification result must have run one.
        await self._flag_unverified_claim(effort_id, result)
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
        # A commit needs a SUBJECT and a BODY (operator 2026-07-15, a git-history evaluation: the
        # effort commits were "silent" — bare one-line subjects with no body, so a reader landing on
        # the branch couldn't tell what changed, why, or whether tests pass; the gold-standard
        # commits pair subject=WHAT with body=WHY + verification). Two `-m` flags = subject + body.
        instruction = (
            f"{lead} (the env prefix on the commit attributes it to you, `{name}`):\n"
            f"  git checkout -b {branch} 2>/dev/null || git checkout {branch}\n"
            f"  git add -A\n"
            f'  {ident} git commit \\\n'
            f'    -m "{effort_id}: <short imperative subject — WHAT changed>" \\\n'
            f'    -m "<body, 1-3 lines: what changed and WHY, and the verification result — '
            f"e.g. 'Extracted file I/O from todo.py into a TodoStore class. Tests: 6/6 pass, "
            f'behaviour unchanged.\'>"   # skip only if nothing to commit\n'
            f"  git push -u origin {branch}\n"
            f"The commit MUST have BOTH a clear subject AND a body (what + why + test result) — a "
            f"bare one-line commit is not acceptable; a reader landing on this branch cold should "
            f"understand the change from the message alone. Do NOT push to main/master. Do NOT "
            f"force-push or delete anything. {tail}"
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
            payload={"branch": branch, "self_reported_ok": bool(result and result.ok), "firm": firm,
                     # P8 #3 — every published claim states what it was built against ("" = the
                     # org couldn't read the base; the claim is then explicitly unprovenanced).
                     "base_sha": (self._expected_base.get(effort_id) or {}).get("sha", "")},
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
            if verdict == "infra":
                await self._elevate_check_infra(effort_id, out)
                return None
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
        if (pub is not None and "NO CHANGES:" in (pub.output or "")
                and await self._no_changes_acceptable(effort_id, pub.output or "")):
            # Same gate as the first-publish path: a bare no-op can't close a fix/build/behavioral
            # goal even after a firm re-engage. If it's NOT acceptable, fall through to _verify_
            # delivery (below) — a landed branch reaches _finish_effort's honest unverified closure.
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
        # WORKSPACE-SUFFICIENCY RE-ROUTE (live 2026-07-09: 8 plan steps ran in the STANDALONE
        # murder clone where the editor can't even build — each no-op'd in ~3 min, nothing
        # landed). A vendored project with a host build check that VERIFIABLY delivered nothing
        # from a standalone run gets its work re-run in the HOST context — where building and
        # running are physically possible — instead of a dead-end escalation. Once only per run
        # (the host path's own blocker elevation is the honest stop).
        proj = await self._effort_project(effort_id)
        if (proj and await self._vendored_host(proj)
                and await self._machine_verified_effort(effort_id)):
            await self.comms.post(
                Intent.worker_activity,
                "🧭 the standalone workspace produced no delivery — this project is VENDORED in "
                "a host composition, so I'm re-running the work in the **host context** "
                "(recursive clone) where its build can actually run.",
                effort_id=effort_id)
            await self.audit.log("host_context_reroute", effort_id=effort_id,
                                 payload={"from": "undelivered_standalone"})
            self._route_host_after.add(effort_id)   # delegate's finally launches it
            return None
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

    # A constraint that names a capability the SANDBOXED workers structurally lack but a human's
    # own machine typically has — a host-only/platform-specific tool, a GUI/interactive step, a
    # licensed or proprietary binary, physical hardware, or credentials the org must not hold.
    # Generic across projects; used to turn a blocker into a HUMAN-ACTION suggestion.
    _HUMAN_CAP_RE = re.compile(
        r"\bwine\b|\bgui\b|graphical|interactiv|manual(?:ly)?\b|by\s+hand\b|"
        r"windows(?:-only|\s+host|\s+machine)?\b|mac(?:os)?\b|native\s+(?:tool|compiler|binary)|"
        r"licen[cs]|proprietary|activation|hardware|gpu\s+driver|physical\s+device|"
        r"credential|secret|api\s+key|two-?factor|2fa|sign\s*in|log\s*in\s+to|"
        r"install\s+(?:it\s+)?(?:on|locally)|on\s+your\s+(?:host|machine|box)|"
        r"\bdisplay\b|\bscreen\b|window\s+manager|render(?:ing)?\s+context|click|"
        r"game\s+(?:assets?|content|project)|real\s+content|user\s+(?:input|interaction)|"
        r"can(?:not|'?t|\s+not)\s+(?:reproduce|repro|trigger|observe)\b|"
        r"only\s+(?:works|runs|available|happens|reproduces?)\s+(?:on|when|with)\b|"
        r"not\s+available\s+(?:in|on)\s+(?:this|the)\s+(?:worker|container|environment|sandbox)|"
        r"headless\b[^.\n]*\b(?:can(?:not|'?t)|no|without)\b", re.I)

    def _is_human_capability_blocker(self, blk: dict) -> bool:
        text = f"{blk.get('needs', '')} {blk.get('blocked', '')} {blk.get('feasible', '')}"
        return bool(self._HUMAN_CAP_RE.search(text))

    async def _elevate_check_infra(self, effort_id: str, log: str) -> None:
        """A red check that is the CHECK's OWN infrastructure failing (git-proxy denial, missing
        project/tool, clone/workspace error) — not the delivered code. Surface it honestly as a
        check/environment problem to fix (or hand off), never a code failure to burn down or a
        worker's fault. Keeps the effort open. Generic (2026-07-10)."""
        tail = "\n".join([ln for ln in (log or "").splitlines() if ln.strip()][-12:])[:700]
        await self.audit.log("check_infra_error", effort_id=effort_id,
                             payload={"log": (log or "")[-1500:]})
        body = (
            f"🧰 **{effort_id}** — the delivery may be fine, but my **check couldn't run** here: "
            f"it failed on its own environment (proxy/clone/tool/path), not on your code:\n"
            f"```\n{tail}\n```\n"
            f"This isn't a code error to fix by editing source, and it's **not** the worker's "
            f"fault — the CHECK or the workspace setup needs attention. I've left the effort open "
            f"and not marked it done. (I flag this instead of grinding the code, which would only "
            f"chase a problem that isn't in the code.)")
        await self.comms.post(Intent.escalation, body, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, body,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "needs-attention")

    # A blocker the ORG can resolve ITSELF: a vendored/composition project whose build or test can't
    # run standalone because its siblings live only in the host. The remedy is a host-context re-run,
    # so the org never sits idle on it (operator 2026-07-11: the org does the work, don't go idle).
    _WORKSPACE_BLOCKER_RE = re.compile(
        r"workspace|present|sibling|submodule|\bhost\b|context|standalone|vendored|"
        r"couldn'?t\s+(?:run|build|test)|full\s+(?:build|test)|dotnet\s+(?:build|test)|"
        r"needs?\s+the\s+(?:host|engine)", re.I)

    async def _last_blocker_text(self, effort_id: str) -> str:
        """The `blocked` text of the effort's most recent elevated blocker (for auto-resolution)."""
        async with self.db.session_factory() as s:
            row = (await s.execute(
                select(Event.payload).where(
                    Event.kind == "effort_blocked_elevated", Event.effort_id == effort_id)
                .order_by(Event.ts.desc()).limit(1))).scalar_one_or_none()
        return (row or {}).get("blocked", "") if isinstance(row, dict) else ""

    async def _try_auto_resolve_blocked(self, effort_id: str) -> bool:
        """A blocked effort whose blocker the ORG can resolve (a workspace/host-context limit) must
        not sit idle waiting on the operator — auto-route it to the host context (bounded to once).
        Returns True if it acted; False if the blocker genuinely needs a human (leave it)."""
        if effort_id in self._delegating:
            return False
        if await self._event_count(effort_id, "host_context_reroute") >= 1:
            return False                                  # already tried once → leave for the operator
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        if not host:
            return False
        blocked = await self._last_blocker_text(effort_id)
        if not (blocked and self._WORKSPACE_BLOCKER_RE.search(blocked)):
            return False                                  # not a workspace limit → needs a human
        await self.audit.log("host_context_reroute", effort_id=effort_id,
                             payload={"blocked": blocked[:200], "auto": True, "via": "watchdog"})
        note = (f"🔁 **{effort_id}** was blocked on a workspace-context limit and left idle — "
                f"auto-running it in the host context now so it builds + verifies for real (the org "
                f"resolves this itself; no action needed).")
        await self.comms.post(Intent.worker_activity, note, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, note,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "working")
        self._spawn(self._run_in_host_context(effort_id))
        return True

    _ESCALATE_RE = re.compile(r"^\s*ESCALATE\s*:\s*(.+)$", re.I | re.M)

    async def _discard_uncommitted(self, worker_url: str, effort_id: str) -> bool:
        """P16 — drop a dead turn's uncommitted edits so the next turn starts from a known state.

        Returns True when something was actually discarded. COMMITTED work is never touched: the
        commit is the worker's statement that a change is finished, and an abandoned turn made no
        such statement about its working tree. `checkout -- .` reverts tracked edits and `clean -fd`
        drops untracked files; both are scoped to the workspace and are proxy-legal (unlike
        `reset --hard`). Never raises — a failed cleanup must not block the recovery it serves.

        P17 F6 — when `worker_url` is known this MUST run against that worker. The original went
        through `router.exec_check`, which calls `scheduler.acquire(...)` and therefore cleans
        whichever worker happens to be free. gym-015: the hung worker was worker-2 (tree dirty with
        `M todo.py`, `M tests/test_todo.py`); the discard ran on worker-1, which was already clean;
        `stall_tree_discarded` never fired and the wreckage stayed exactly where it was. The
        recovery that round succeeded in spite of this, not because of it. `run_check` takes the
        base URL directly, so a known worker is cleaned deterministically; the idle path (no URL in
        scope) still falls back to the pool."""
        cmd = ("cd /workspace && git status --porcelain | head -20 && "
               "git checkout -- . 2>/dev/null; git clean -fd 2>/dev/null; "
               "echo DISCARD-DONE")
        try:
            if worker_url:
                # TARGETED: this is the worker that hung, so this is the tree that is dirty.
                _exit, out, _timed = await self.router.harness.run_check(
                    worker_url, cmd, timeout=120)
            else:
                # Idle-stall path — no specific daemon is implicated; clean the effort's workspace
                # via the pool. Still worth doing, just not attributable to one worker.
                _exit, out, _timed = await self.router.exec_check(
                    effort_id, command=cmd, session_id=f"{effort_id}~clean",
                    repo=None, repo_token=None, timeout=120,
                )
        except Exception as exc:  # noqa: BLE001 — cleanup is best-effort, never a recovery blocker
            log.debug("uncommitted discard failed for %s: %s", effort_id, exc)
            return False
        # `git status --porcelain` emits `XY<space><path>`, X/Y from a fixed status alphabet. Match
        # that shape exactly — a looser `^\s*[?MADRU]` also matches the DISCARD-DONE sentinel on
        # its leading `D` and reports a clean tree as dirty.
        dirty = bool(out and re.search(r"^[ ?MADRUC][ ?MADRUC]\s+\S", out, re.M))
        if dirty:
            await self.audit.log("stall_tree_discarded", effort_id=effort_id,
                                 payload={"worker": worker_url, "status": (out or "")[:400]})
        return dirty

    async def _route_escalation(self, effort_id: str, output: str) -> int:
        """P14.1 — A SIBLING-SCOPE HANDOFF IS ROUTING, NOT A HUMAN QUESTION.

        `_scope_context` tells a bounded worker to write `ESCALATE: <what you need and why>` when
        the work is outside its border, and `_escalation_target` was built in P10.6 to say who owns
        the adjacent scope. Nothing connected the two: an ESCALATE was treated as a generic blocker,
        so it froze the effort and asked the operator. gym-012 (2026-07-19) did everything right —
        completed its in-scope task, escalated a test-assertions task that had been mis-filed into
        the persistence scope — and the org froze on `ambiguous_scope` with a four-child tree
        sitting right there. With a bounded tree, that means the org can never work its own
        decomposition unaided.

        Re-file each escalated task into the sibling scope whose text best matches it (else the
        parent), leave it OPEN, and let the tier walk pick it up. Returns how many were routed; 0
        means nothing matched and the caller should elevate to a human as before — a genuine
        cross-project escalation still reaches the operator."""
        marks = [m.group(1).strip() for m in self._ESCALATE_RE.finditer(output or "")]
        if not marks:
            return 0
        node_id = await self._ensure_scope_node(effort_id) if self.s.drain_tier_walk else None
        if not node_id:
            return 0
        n = await self._scope_node(node_id)
        parent_id = (n or {}).get("parent_id")
        siblings = await self._scope_children(parent_id) if parent_id else []
        candidates = [k for k in siblings if k["id"] != node_id]
        if not candidates:
            candidates = await self._scope_children(node_id)   # a root escalating into its children
        if not candidates:
            return 0
        proj = await self._effort_project(effort_id) or ""
        routed = 0
        for mark in marks:
            target = await self._best_scope_for(mark, candidates)
            if not target:
                continue
            # The escalated work is usually ALREADY queued in the wrong scope (that is why the
            # worker met it at all) — move it rather than duplicating it.
            moved = await self._refile_task(effort_id, mark, target)
            if not moved:
                res = await self.add_task(mark[:2000], project_slug=proj, scope_node_id=target,
                                          effort_id=effort_id, source_lens="escalation",
                                          round_no=await self._drain_round_no(effort_id))
                moved = bool(res)
            if moved:
                routed += 1
                await self._reopen_scope(target, reason=mark[:200], effort_id=effort_id)
                await self.audit.log("escalation_routed", effort_id=effort_id,
                                     payload={"from": node_id, "to": target, "mark": mark[:200]})
        if routed:
            await self.comms.post(
                Intent.worker_activity,
                f"↔️ **Escalation routed** — {routed} item(s) handed to the adjacent scope that "
                f"owns them. No operator action needed; the tier walk works them in turn.",
                effort_id=effort_id)
        return routed

    async def _best_scope_for(self, text: str, candidates: list[dict]) -> str | None:
        """Which candidate scope owns this text. Lexical overlap on distinctive words — the same
        conservative posture as `_seam_owner`: no clear winner means no route, and the caller
        falls back to a human rather than guessing."""
        words = {w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
                 if len(w) >= 4 and w not in _SCOPE_STOPWORDS}
        if not words:
            return None
        best, best_score = None, 0
        for k in candidates:
            blob = f"{k.get('title','')} {k.get('scope','')}".lower()
            score = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", blob))
            if score > best_score:
                best, best_score = k["id"], score
        return best if best_score >= 2 else None

    async def _refile_task(self, effort_id: str, text: str, target_node: str) -> bool:
        """Move an already-queued task to the scope that owns it, keeping it OPEN. Matched on
        distinctive-word overlap with the task body — the escalation text paraphrases the task."""
        words = {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) >= 5}
        if not words:
            return False
        try:
            async with self.db.session_factory() as s:
                rows = (await s.execute(
                    select(ScopeTask).where(ScopeTask.effort_id == effort_id))).scalars().all()
                for r in rows:
                    body = (r.body or "").lower()
                    if sum(1 for w in words if w in body) < 2:
                        continue
                    r.scope_node_id = target_node
                    r.status = "open"          # escalated work is NOT done
                    r.closed_at = None
                    await s.commit()
                    return True
        except Exception as exc:  # noqa: BLE001 — routing must never break a dispatch
            log.debug("task refile failed for %s: %s", effort_id, exc)
        return False

    async def _elevate_blocker(self, effort_id: str, blk: dict) -> None:
        """The PM's job the mechanical monitor skipped: HEAR the worker's constraint and surface it
        to the operator with a synthesized read + an actionable next step, keeping the effort OPEN
        (needs-attention) — never a false done, never a blind re-dispatch. If the constraint is a
        workspace insufficiency on a composition effort, name the concrete remedy (run in the host
        context). Generic for any project/blocker."""
        proj = await self._effort_project(effort_id)
        host = await self._vendored_host(proj) if proj else None
        # A WORKSPACE-context limit on a COMPOSITION (the standalone project can't build/test because
        # its vendored siblings only exist inside the host) is something the ORG resolves ITSELF —
        # re-run in the host context (the engine, recursively cloned). Do it AUTOMATICALLY, bounded to
        # once, instead of offering it and waiting on the operator (operator 2026-07-10: the atlas fix
        # was verified as far as it could be standalone — "atlas files exist + parse" — then blocked
        # only on the full `dotnet test`, which the org can run in the host context on its own).
        workspace_blocker = bool(host and self._WORKSPACE_BLOCKER_RE.search(blk["blocked"]))
        if workspace_blocker and await self._event_count(effort_id, "host_context_reroute") < 1:
            await self.audit.log("host_context_reroute", effort_id=effort_id,
                                 payload={"blocked": blk["blocked"][:200], "auto": True})
            note = (f"🔁 **{effort_id}** — the worker hit a WORKSPACE-context limit: `{proj}` builds/"
                    f"tests only inside its host `{host[0]}`, where the vendored source + full test "
                    f"live. **Re-running it in the host context automatically** so it can build and "
                    f"verify for real — no action needed. The branch it already pushed is kept.")
            await self.comms.post(Intent.worker_activity, note, effort_id=effort_id)
            await self.comms.post(Intent.operator_reply, note,
                                  thread_id=self._mgmt_thread_of(effort_id))
            await self.router.update_effort_card(effort_id, "working")
            self._spawn(self._run_in_host_context(effort_id))
            return
        remedy = ""
        if workspace_blocker:   # already auto-routed once → offer the manual retry, don't loop
            remedy = (f"\n\n**Likely remedy:** this reads as a WORKSPACE-context limit — `{proj}` "
                      f"only builds inside its host `{host[0]}` (where `{host[1]}`'s siblings like "
                      f"the vendored dependency exist). I already re-ran it in the host context once; "
                      f"say _\"run it in the host context\"_ to try again, or tell me how to proceed.")
        # HUMAN-ACTION SUGGESTION (operator 2026-07-09: "if I'm building the shaders, the PM
        # should tell me to do so — when the issue is otherwise impossible for the workers, make
        # a suggestion with instruction for the human"). When the constraint is a capability the
        # WORKERS structurally lack but the operator's own machine has (a host-only tool, a GUI, a
        # licensed/platform binary, a manual step), don't just say "your move" — propose the human
        # do it, relaying whatever the worker said it NEEDS as concrete guidance. Generic.
        elif not remedy and self._is_human_capability_blocker(blk):
            need = (blk["needs"] or blk["blocked"]).strip()
            remedy = (
                f"\n\n**Suggested next step — this one likely needs YOU:** the workers can't do "
                f"this in their environment (it needs {need}). That kind of capability usually "
                f"lives on your own machine, not the sandboxed workers. If you can, do that step "
                f"on your host and commit/point me at the result, and I'll verify and carry on "
                f"from there — or tell me another way you'd like to handle it. (I'm flagging this "
                f"rather than guessing, because forcing it here would only produce a fake pass.)")
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

    async def _drain_iterate(self, effort_id: str, open_tasks: list[dict], round_no: int, *,
                             channel_id: str = "", root: str = "") -> bool:
        """P10.4 + P10.5 — dispatch one round's work. Returns True when work was queued.

        This REPLACES `_auto_iterate` on the drain path, and deliberately does not inherit either of
        its two constructions:

        * **No `n >= 2` cap.** The loop continues while the sweep keeps propagating new work and
          stops when it propagates none. A cap stops for a reason unrelated to whether the work is
          finished, which is precisely how the org kept declaring unfinished products done.
        * **No restating of the whole goal.** `_auto_iterate` re-sent the ENTIRE original goal plus
          one defect to the worker that had just satisfied that goal — asking it to plan work it
          had just done, against a goal it believed met. gym-008 answered with an EMPTY plan and the
          effort stranded as `abandoned`. The implementer here is handed a PLAN and a TASK LIST and
          nothing else.

        The PLAN/IMPLEMENT SPLIT (P10.5): a planner turn (fresh, read-only session) converts the
        open tasks into an ordered plan; a FRESH implementer session executes it. The implementer
        never plans its own completed work and is not defending prior output. This is only safe
        because the CDCL clause set (§5–6) now carries the learning across the rotation — before
        clauses existed, a fresh session forgot every dead end it had already walked."""
        if not open_tasks:
            return False
        # P20 ONE TASK AT A TIME (ORCHESTRATION-DESIGN §4: "single tasks, each delegated to a single
        # worker ... one task at a time and unaware of the bigger picture"; §5: "how ONE worker
        # executes ONE bounded task"). This is the architecture's load-bearing sidestep of the
        # small-model long-horizon failure — the worker never carries the horizon. Handing it the
        # whole scope queue is a multi-task turn, which "don't typically work reliably" (operator,
        # 2026-07-21): gym-018's worker went silent mid-turn trying to implement 6 tasks in one pass.
        # Dispatch the FIRST task ONLY; the rest stay queued and drain one per turn (`_finish_effort`
        # re-enters and works the next single one without re-sweeping).
        queued = len(open_tasks)
        open_tasks = open_tasks[:1]
        listed = f"- {open_tasks[0]['body']}"
        await self.audit.log("drain_dispatch", effort_id=effort_id,
                             payload={"round": round_no, "task": open_tasks[0]["body"][:200],
                                      "queued": queued})
        plan = ""
        if self.s.drain_plan_split:
            plan = await self._drain_plan(effort_id, listed)
        # The implementer's brief: the plan + the tasks, the scope border, and the learned
        # constraints — NOT the project goal (see the docstring).
        # THE SCOPE BORDER — but only from a genuine SUB-scope. `_scope_context` exists to withhold
        # the REST of the tree; at an undecomposed ROOT there is no rest, and the root's scope text
        # is the effort's own goal — so injecting it here would restate the whole goal to the
        # implementer, which is precisely the gym-008 construction this split was built to retire.
        # A root brief is therefore tasks + plan only.
        scope_ctx = ""
        node_id = await self._ensure_scope_node(effort_id) if self.s.drain_tier_walk else None
        if node_id:
            n = await self._scope_node(node_id)
            if n and (n.get("depth") or 0) > 0:
                scope_ctx = await self._scope_context(node_id)
        # P15.5 — THE COMMIT IS THE HANDOFF. The operator's review of gym-013's history scored
        # intent clarity 5/10 and context linking 2/10: the commits said WHAT changed and never
        # WHY, and never referenced the goal, the scenario or the acceptance check that motivated
        # them — all of which the org holds and simply never passed on. The requirement, in the
        # operator's words: "the commit history should be a traceable record of the agent's
        # reasoning — not just its output."
        commit_brief = (
            "\n\nWHEN YOU COMMIT: the message is how the next worker inherits your reasoning.\n"
            "Subject: imperative, naming the component you changed.\n"
            "Body: state WHY before what — the goal or check this serves, the decision you took "
            "and what you rejected, and anything a later reader would otherwise have to "
            "reverse-engineer from the diff. Name the file/function the change lands in. End with "
            "the verification you ran and its result."
        )
        goal = (
            f"Work the following SINGLE task on the existing branch — implement it, commit, and "
            f"push. It is one bounded unit; do only this, then stop. Other work for this scope is "
            f"tracked separately and will come to you as its own task.\n\nTASK:\n{listed}"
            + (f"\n\nPLAN:\n{plan}" if plan else "")
            + scope_ctx + commit_brief
        )
        # DISPATCH, DON'T STRAND. `_iterate_after` is drained by `delegate`'s finally — but
        # `_finish_effort` is ALSO reached from `_burndown_loop` and `_run_in_host_context`, where
        # that finally has already run. Queuing there would leave the effort with no dispatch, no
        # PR and no closure: silently dead, with its tasks already closed. So mirror
        # `_queue_burndown`: inside delegate's single-flight, queue for the finally; anywhere else,
        # launch it directly. (`_auto_iterate` had the same shape but its callers fell through to
        # a closure — this path RETURNS, which is what makes stranding terminal.)
        if effort_id in self._delegating or not (channel_id and root):
            self._iterate_after[effort_id] = goal
        else:
            self._spawn(self.delegate(effort_id, channel_id, root, goal))
        # Close what we just handed over, as `dispatched` — NOT `done`. The distinction is the
        # whole of P17 F12. This runs immediately after the dispatch above, so at this instant the
        # implementer has not executed a single step: any completion claim here is a guess.
        #
        # The original reasoning — "a task that isn't really finished is re-derived by the next
        # independent sweep, so closing cannot hide unfinished work" — did not survive gym-015 on
        # two counts. (1) Re-derivation is not idempotent: the sweep re-words the finding, so
        # `sha1(owner|body)` sees a NEW task rather than reopening the old one, and the queue
        # churns instead of correcting (`st-29afe75` "Implement a --version CLI flag" came back as
        # `st-1783517` "Expose the __version__ string via a --version CLI flag"). (2) It does not
        # always come back at all: `st-19ee694` (remove the broad `except Exception` in `cmd_repl`)
        # was closed `done`, was never re-derived as actionable, and is still undone on the
        # delivered branch. Meanwhile a worker that CORRECTLY declines out-of-scope work — the
        # tier walk behaving exactly as designed — has its refusal recorded as completion.
        #
        # `dispatched` keeps the anti-re-dispatch property (it is still a closed state) while
        # making the audit honest: the org now says "handed over", which is what it actually knows.
        for t in open_tasks:
            await self.close_task(t["id"], status="dispatched")
        await self.comms.post(
            Intent.worker_activity,
            f"🔁 **Drain round {round_no}** — dispatching a fresh implementer on **one** task"
            + (f" ({queued - 1} more queued for this scope)" if queued > 1 else "")
            + ". No operator action needed.",
            effort_id=effort_id,
        )
        return True

    async def _drain_plan(self, effort_id: str, listed_tasks: str) -> str:
        """The PLANNER half of P10.5 — a fresh, read-only turn that orders the open tasks against
        the actual codebase. Separate from the implementer so that no agent is ever asked to plan
        work it has just performed. Best-effort: an unplanned round still dispatches the tasks."""
        loc = await self.router.effort_thread(effort_id)
        if not loc:
            return ""
        channel_id, root = loc
        repo = await self._effort_repo(effort_id) or ""
        self._verify_seq += 1
        # P11.2 — PHRASED AS AN EVALUATION, NOT A CALL TO ACTION. The previous wording said "you
        # will CHANGE NOTHING" and then "turn the task list into an ordered implementation plan" —
        # a prohibition wrapped around an imperative, and in gym-009 the worker did the whole
        # implementation here (6 min, 5 commits) before the implementer was ever dispatched. The
        # three standing lenses stay read-only every round with NO enforcement at all, because they
        # ask for an assessment and a written report. This is modelled on them: the deliverable is
        # a report about the work, not the work. `plan_only` is the backstop (it gates the
        # edit/write tools but NOT `cat > file` or `git push`, so the phrasing carries the load).
        instr = (
            f"assess the codebase against the task list below and write a short report. For each "
            f"task, state which files it touches and what would need to change, and say whether "
            f"the codebase already satisfies it. Do not edit files in this codebase, this is just "
            f"evaluative.\n\n"
            f"TASKS:\n{listed_tasks}"
        )
        try:
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=f"{effort_id}~plan{self._verify_seq}", instruction=instr,
                repo=repo, repo_token=await self._project_token(effort_id),
                plan_only=True, withhold_goal=True,
            )
        except Exception as exc:  # noqa: BLE001 — planning is a quality step, never a blocker
            log.debug("drain planner wake failed for %s: %s", effort_id, exc)
            return ""
        return ((result.output or "") if result else "").strip()[:4000]

    def _queue_burndown(self, effort_id: str, failing_log: str, *, origin: str = "") -> None:
        """Start (or defer) the burn-down for a RED org build. Inside delegate's single-flight the
        loop must wait for the current run to close — delegate's finally launches it; anywhere
        else it starts immediately.

        CDCL (§5–6): this is the chokepoint EVERY red path funnels through (composition check, the
        post-check ladder, delegate deviation, D2, the acceptance corpus), so it is where a failure
        becomes a durable LEARNED CONSTRAINT. Recorded BEFORE the defer/spawn branch, so the clause
        is already on the effort when the loop reads it. Content-addressed + infra-filtered inside
        `_record_constraint`, so the several paths that report the same underlying failure collapse
        to one clause and a tool breakage never steers the search."""
        self._spawn(self._record_constraint(effort_id, failing_log, origin=origin or "burn-down"))
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
            token, checkout = await self._project_token(effort_id), ""
            # A plain project can still VENDOR submodules its build needs (an engine that vendors a
            # framework which itself vendors more) — when the project's OWN check declares recursive
            # submodules, the focus MUST populate the full NESTED tree or the build fails MSB3202
            # 'project file not found' (live 2026-07-12: the atlas effort is on the engine HOST; its
            # build of vendor/murder needs murder's OWN nested bang/gum, which a direct-only focus
            # misses). The check_cmd's `submodule … --recursive` is the operator's own declaration of
            # that need — honour it. Generic across toolchains.
            recurse = bool(re.search(
                r"\bsubmodule\b[^\n]*--recursive|--recursive[^\n]*\bsubmodule\b", check_cmd, re.I))
        # FAIL-FAST on an UNREACHABLE submodule gitlink (live 2026-07-13: a composition branch bumped a
        # submodule pointer to a commit that was never published to the submodule's remote; the check's
        # `git submodule update` then HUNG ~30 min on a fetch that can never resolve, timed out to an
        # 'unknown' verdict, and wedged the whole verify pipeline for that effort). Reachability is a
        # FAST GitHub API check (no git fetch) — do it BEFORE the build and return 'infra' cleanly: the
        # composition is unbuildable until the commit is published, which is a delivery/infra gap, not a
        # code failure to burn down. Generic across compositions; fail-open (a read error → run the
        # build as before, never block on the pre-check).
        if on_branch:
            check_repo = host_url if host else repo
            try:
                broken = await self._broken_gitlinks(effort_id, check_repo) if check_repo else []
            except Exception:  # noqa: BLE001 — a pre-check must never crash or block the verify path
                broken = []
            if broken:
                listing = ", ".join(
                    f"`{b['path']}`→`{b['sha'][:10]}` (missing from `{b['submodule_repo']}`)"
                    for b in broken)
                out = (f"unreachable submodule gitlink(s): {listing} — not on the submodule remote(s), "
                       f"so the build's `git submodule update` would hang on an unresolvable fetch. The "
                       f"composition can't build until the commit is published (a delivery/infra gap, "
                       f"not a code error).")
                await self.audit.log("org_build_check", effort_id=effort_id,
                                     payload={"verdict": "infra", "errors": None, "owner": check_owner,
                                              "mode": "gitlink-precheck", "cmd": check_cmd[:200],
                                              "log": out})
                return "infra", out, None
        # DETERMINISTIC FIRST (2026-07-08): "run a command, report its output" is a MACHINE step
        # — the daemon's `/check` returns the real exit code + full log with no model in the loop
        # (the LLM verifier burned its whole turn re-running builds and never wrote the verdict).
        command = f"{checkout}{build_only}" if checkout else f"cd /workspace && {build_only}"
        mode = "exec"
        verdict = "unknown"
        out = ""
        n: int | None = None
        # The verify-focus does a privileged recursive re-clone of the host; the scheduler may hand
        # the verifier a SUSPENDED task worker still holding the effort's large parked workspace, and
        # wiping+recloning that tree can TRANSIENTLY collide ("clone failed … destination '/workspace'
        # already exists and is not an empty directory") — an intermittent race, not a code fault
        # (live 2026-07-11: the atlas composition kept falling through to the NONDETERMINISTIC LLM
        # verifier, which then guessed — "pass" one round, "unknown" the next). RETRY the deterministic
        # check ONCE on such a transient collision (a fresh focus re-wipes clean) so we keep the
        # MACHINE verdict; only a persistent failure falls back to the LLM verifier.
        for attempt in (1, 2):
            fresh = attempt == 2   # the retry forces a FRESH recursive re-clone (re-wipe + re-populate)
            try:
                exit_code, out, timed_out = await self.router.exec_check(
                    effort_id, command=command, session_id=f"{effort_id}~chk",
                    repo=focus, repo_token=token, recurse_submodules=recurse, timeout=900,
                    fresh=fresh,
                )
                if timed_out or exit_code is None:
                    verdict, n = "unknown", None
                    out = f"(build timed out / no exit code)\n{out[-3000:]}"
                elif exit_code == 0:
                    verdict, n = "pass", 0
                elif _is_infra_failure(out):
                    # the CHECK ITSELF couldn't run — a git-proxy denial, a missing project/tool, a
                    # clone/workspace-setup error (live 2026-07-10: the check's submodule branch
                    # fetch was git-proxy-DENIED → MSB1009, and the burn-down spun on it as if it
                    # were a code error). This is NOT a code failure — you can't fix a broken check
                    # by editing the code. Distinct verdict so callers surface it instead of
                    # burning down. Generic across toolchains.
                    verdict, n = "infra", None
                else:
                    verdict, n = "fail", _error_count(out)
                # SELF-HEAL a partial composition workspace (dark-factory 2026-07-12): an MSBuild
                # missing-project/submodule error on a COMPOSITION almost always means the privileged
                # recursive focus TRANSIENTLY missed a vendored NESTED submodule (murder → bang/gum) —
                # the worker can't fix that (the git-proxy hard-blocks `git submodule`), so re-run the
                # check ONCE with a FRESH recursive focus to re-populate the tree before surfacing it.
                if (verdict == "infra" and attempt == 1 and recurse
                        and _MSBUILD_ENV_RE.search(out or "")
                        and not re.search(r"git-proxy|\bDENIED\b", out or "", re.I)):
                    # …but ONLY a genuinely missing referenced project (a nested submodule that
                    # didn't populate) — a git-proxy DENIAL or tool-not-found is NOT fixed by a
                    # re-focus, so don't waste one (it would still deny, and could burn the verdict).
                    log.info("composition check for %s hit a missing-project/submodule infra error — "
                             "re-running once with a FRESH recursive focus to self-recover", effort_id)
                    continue
                break
            except Exception as exc:  # noqa: BLE001 — old daemon without /check, or a focus failure
                if attempt == 1 and _is_transient_focus_collision(str(exc)):
                    log.info("verify-focus collided for %s (%s) — retrying the deterministic check "
                             "once before the LLM fallback", effort_id, exc)
                    continue
                log.info("deterministic check unavailable for %s (%s) — LLM verifier fallback",
                         effort_id, exc)
                mode = "llm"
                verdict, out, n = await self._llm_verify_fallback(
                    effort_id, channel_id, root, check_owner, checkout, build_only,
                    focus, token, recurse)
                break
        await self.audit.log("org_build_check", effort_id=effort_id,
                             payload={"verdict": verdict, "errors": n, "owner": check_owner,
                                      "mode": mode, "cmd": check_cmd[:200], "log": out[-6000:]})
        return verdict, out, n

    async def _org_reproduction_verified(self, effort_id: str, head_sha: str) -> bool:
        """HEADLESS RUNTIME SELF-VERIFICATION — the dark-factory keystone (2026-07-13). Independently
        prove a runtime-symptom fix is REAL, not a smoke test, by running the project's OWN check at
        the pre-fix BASE and at the fix and requiring RED→GREEN: the check must FAIL without the fix
        and PASS with it. base=GREEN ⇒ the check passes with OR without the fix ⇒ it does not exercise
        the symptom (a passive smoke launch — e.g. the atlas editor-launch that never opens a Game
        Profile) ⇒ NOT a reproduction ⇒ NOT verified. Only an ORG-OBSERVED red→green sets
        `_repro_red_green` (the honest basis for 'verified via reproduction'); everything else FAILS
        CLOSED (unresolvable base, timeout, unparseable result, an infra failure at base).

        The base is a merge-base ANCESTOR of head, so it is already in the worker's full-history clone —
        ONE deterministic exec_check checks out each commit in turn and runs the build; no extra clone,
        no model. `git submodule update` is proxy-denied to the worker, so between checkouts each
        vendored submodule is synced to ITS gitlink with a LOCAL `git -C <path> checkout` (proxy-safe) —
        so a SUBMODULE-fix reproduces too. Anything that can't be reverted (a submodule sha not in the
        local clone, a proxy-denied read) simply leaves that submodule put ⇒ fail-closed for that fix,
        never a false pass. Generic across host-level AND vendored-submodule runtime symptoms."""
        if not head_sha:
            return False
        proj = await self._effort_project(effort_id)
        branch = self._effort_branch(effort_id)
        host = await self._vendored_host(proj) if proj else None
        check_owner = host[0] if host else proj
        p = await self.projects.get(check_owner) if check_owner else None
        check_cmd = ((p or {}).get("check_cmd") or "").strip()
        if not check_cmd:
            return False
        check_repo = host[2] if host else (await self._effort_repo(effort_id) or "")
        if not check_repo or self.github is None:
            return False
        base_sha = await read_merge_base(
            self.github, check_repo, branch,
            api_base=self.s.github_api_base, transport=self._gh_transport)
        if not base_sha or base_sha[:12] == head_sha[:12]:
            return False   # no distinct pre-fix base ⇒ can't prove red→green ⇒ fail closed
        build_only = self._build_segment(check_cmd)
        recurse = bool(re.search(
            r"\bsubmodule\b[^\n]*--recursive|--recursive[^\n]*\bsubmodule\b", check_cmd, re.I))
        # Build at BASE first (expect RED). A GREEN base is DEFINITIVELY not a reproduction (the check
        # passes WITHOUT the fix ⇒ a smoke test), so skip the redundant HEAD build in that case — head
        # is already known green (org_build_check is the precondition for this harness). Only when base
        # is RED do we build HEAD to independently confirm GREEN. This halves the cost for the common
        # smoke-test case (a worker claims a repro but the check doesn't exercise the symptom — the
        # atlas editor-launch). `( … )` subshells the build so its `cd`s don't leak; markers are parsed.
        b, hh = shlex.quote(base_sha), shlex.quote(head_sha)
        # `git submodule update` is proxy-denied to the worker, so after `git checkout <commit>` the
        # superproject tree moves but each vendored submodule's WORKING DIR stays put. To make a
        # SUBMODULE-fix (e.g. the murder cursor fix) reproduce, sync each submodule to ITS gitlink at
        # that commit with a LOCAL checkout (proxy-safe, unlike `submodule update`): `git rev-parse
        # <commit>:<path>` reads the pinned sha, `git -C <path> checkout` moves the working dir. All
        # best-effort + `|| true`: if the proxy denies these reads, or a submodule sha isn't local, the
        # submodule simply stays put and the harness FAILS CLOSED for that fix (never a false pass).
        # NB: use a `(cd "$pth" && git checkout …)` SUBSHELL, never `git -C <path>` — the git-proxy
        # DENIES the `-C` global as a repo-escape (blocklist:global-override), which would make this a
        # silent no-op. `config --get-regexp` + `rev-parse` + bare-sha `checkout` are all whitelisted.
        csub = (
            "csub(){ for pth in $(git config -f .gitmodules --get-regexp path 2>/dev/null "
            "| awk '{print $2}'); do s=$(git rev-parse \"$1:$pth\" 2>/dev/null) "
            "&& ( cd \"$pth\" && git checkout -qf \"$s\" ) 2>/dev/null || true; done; }"
        )
        command = (
            f"{csub}; cd /workspace; "
            f"git checkout -f {b} >/dev/null 2>&1; csub {b}; "
            f"if ( {build_only} ); then REPRO_BASE=0; else REPRO_BASE=1; fi; "
            f"if [ \"$REPRO_BASE\" = 1 ]; then "
            f"git checkout -f {hh} >/dev/null 2>&1; csub {hh}; "
            f"if ( {build_only} ); then REPRO_HEAD=0; else REPRO_HEAD=1; fi; "
            f"else REPRO_HEAD=skip; fi; "     # green base ⇒ smoke test ⇒ head is moot, don't rebuild
            f"echo \"REPRO_BASE=$REPRO_BASE REPRO_HEAD=$REPRO_HEAD\""
        )
        try:
            _exit, out, timed_out = await self.router.exec_check(
                effort_id, command=command, session_id=f"{effort_id}~repro",
                repo=f"{check_repo}#{branch}", repo_token=await self._project_token(effort_id),
                recurse_submodules=recurse, timeout=1200, fresh=False)
        except Exception as exc:  # noqa: BLE001 — no /check route, or a focus failure ⇒ fail closed
            log.info("reproduction check unavailable for %s (%s) — fail closed", effort_id, exc)
            return False
        m = re.search(r"REPRO_BASE=(\d+)\s+REPRO_HEAD=(\d+|skip)", out or "")
        if timed_out or not m:
            verified = False
            base_exit = head_exit = None
        else:
            base_exit = int(m.group(1))
            head_raw = m.group(2)                       # "skip" when base was green (head not rebuilt)
            head_exit = None if head_raw == "skip" else int(head_raw)
            # RED→GREEN: base FAILED on a genuine CODE failure (not an infra/env error) AND head PASSED.
            # A green base (head skipped) is a smoke test ⇒ not a reproduction ⇒ not verified.
            base_red = base_exit != 0 and not _is_infra_failure(out or "")
            verified = base_red and head_exit == 0
        await self.audit.log("delivery_repro_red_green", effort_id=effort_id,
                             payload={"base": base_sha[:10], "head": head_sha[:10],
                                      "base_exit": base_exit, "head_exit": head_exit,
                                      "verified": verified, "log": (out or "")[-2000:]})
        if verified:
            self._repro_red_green[effort_id] = head_sha
        return verified

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
        # ABORTED IS FINAL — a burn-down must never start on (or resurrect) an archived effort.
        if await self._is_aborted(effort_id):
            await self.audit.log("aborted_dispatch_suppressed", effort_id=effort_id,
                                 payload={"loop": "burndown"})
            return
        self._delegating.add(effort_id)
        # Bound BEFORE the try: the finally drains a queued drain iteration and must not itself
        # raise NameError on the early-return paths below.
        channel_id, root = "", ""
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
            self._last_burn_log[effort_id] = failing_log
            last_sig = self._failure_sig(failing_log)
            # CDCL: every failure signature seen in THIS burn-down. Novelty against the whole set
            # (not just the previous round) is what makes the stop condition a fixed point and
            # catches A→B→A cycles that a one-step comparison scores as endless progress.
            seen_sigs: set[str] = {last_sig}
            branch_exists = (await self._verify_delivery(effort_id, repo)).landed
            stall = 0
            last_result = None
            # PROGRESS-GATED, generous cap: a STILL-PROGRESSING campaign runs to green rather than
            # elevating mid-progress (operator: "all 138 errors should have been worked through
            # autonomously, not elevated"). A stuck campaign elevates FAR sooner (2-no-progress stall
            # detector below). The cap is only a runaway guard for a campaign progressing every round.
            cap = max(1, self.s.burndown_round_cap)
            for rnd in range(1, cap + 1):
                if await self._is_aborted(effort_id):
                    # The operator archived it mid-campaign — stop silently and finally (the
                    # archive already told them; the branch keeps whatever landed).
                    await self.audit.log("aborted_dispatch_suppressed", effort_id=effort_id,
                                         payload={"loop": "burndown-round", "round": rnd})
                    return
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
                if verdict == "infra":
                    # the CHECK itself broke (proxy/clone/tool/path) — NOT the code. Burning
                    # down can't fix that. Stop and surface it as a check/environment problem.
                    await self._elevate_check_infra(effort_id, out)
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
                    # RUNTIME failures defeat counting (live 2026-07-09: a failing editor smoke
                    # is "0 errors" every round — 0 → 0 read as no-progress and insta-stalled).
                    # When counts can't move, a CHANGED failure signature (normalized log tail)
                    # is progress: the org moved PAST the previous failure into the next one.
                    sig = self._failure_sig(out)
                    # CDCL (§5–6): novelty is measured against EVERY signature seen this burn-down,
                    # not just the previous round. A→B→A is not progress — it's a cycle, and the old
                    # `last_sig` test scored both flips as progress and looped forever. A signature
                    # absent from the set is genuinely NEW INFORMATION: record it as a clause. This
                    # turns the loop's stop condition into a real fixed point — "a sweep that yields
                    # no new constraint" — instead of a bare counter.
                    novel_sig = sig not in seen_sigs
                    seen_sigs.add(sig)
                    if novel_sig:
                        await self._record_constraint(
                            effort_id, out, origin=f"burn-down round {rnd}")
                    sig_progress = (not improved and (n in (None, 0) or n == prev) and novel_sig
                                    and last_sig is not None)
                    last_sig = sig
                    await self.audit.log("burndown_round", effort_id=effort_id,
                                         payload={"round": rnd, "errors": n, "prev": prev,
                                                  "sig": sig, "sig_progress": sig_progress,
                                                  "novel": novel_sig, "seen": len(seen_sigs)})
                    if improved or sig_progress:
                        stall = 0
                        note = (f"**{prev if prev >= 0 else '?'} → {n}** errors" if improved
                                else "the failure CHANGED (moved past the previous one)")
                        await self.comms.post(
                            Intent.worker_activity,
                            f"🔥 round {rnd}: {note} — progress, continuing autonomously.",
                            effort_id=effort_id)
                    else:
                        stall += 1
                        await self.comms.post(
                            Intent.worker_activity,
                            f"⚠️ round {rnd}: no progress ({prev} → {n} errors, same failure) — "
                            f"{stall}/2 before I raise it with the full picture.",
                            effort_id=effort_id)
                    errors_log = out
                    self._last_burn_log[effort_id] = out
                if stall >= 2:
                    # RESEARCH-ON-STALL before punting to the human (operator 2026-07-12): the org
                    # grounds the failing error and retries ONE round with the findings; only if that
                    # doesn't move it does it elevate — now carrying the research (actionable).
                    if await self._research_burndown_stall(effort_id, errors_log):
                        stall = 0
                        continue
                    await self._burndown_elevate(effort_id, counts, errors_log,
                                                 "two consecutive rounds without progress")
                    return
            await self._burndown_elevate(effort_id, counts, errors_log,
                                         f"round cap ({cap}) reached — still not green")
        finally:
            self._delegating.discard(effort_id)
            self._burndown_researched.discard(effort_id)   # a fresh burn-down may research again
            # a red queued DURING this loop (e.g. the finish path's D2 disagreed) re-enters —
            # bounded: every loop's stall detector elevates after 2 rounds without progress
            queued = self._burndown_after.pop(effort_id, None)
            if queued is not None:
                self._spawn(self._burndown_loop(effort_id, queued))
            else:
                # DRAIN THE QUEUED ITERATION HERE TOO. This loop holds `_delegating` while it calls
                # `_finish_effort`, so a drain round firing on the burn-down-green path queues into
                # `_iterate_after` — which only `delegate`'s finally used to drain. The effort would
                # be left with no dispatch, no PR and no closure, its tasks already closed. Any
                # single-flight holder that reaches `_finish_effort` owes the same drain.
                nxt = self._iterate_after.pop(effort_id, None)
                if nxt and channel_id and root:
                    self._spawn(self.delegate(effort_id, channel_id, root, nxt))

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
        proj = await self._effort_project(effort_id) or "the project"
        # PER-ROUND part branches (live 2026-07-08 v7: folds 404'd on rounds whose part worker
        # never pushed — unique names also make any stale leftover branch harmless).
        push_branch = f"{branch}-pt{part}r{rnd}" if part else branch
        # SMALL SLICES (live 2026-07-08): 60 errors in one turn overwhelmed the 27B worker —
        # 30 min of thinking, zero commands, poll timeout, nothing pushed. A round is a BITE,
        # not the meal: ~16 errors per worker per round finishes inside the turn budget and
        # lands real commits; the loop's progress test does the rest. (Parallel parts share one
        # GPU, so their turns are slower — keep them smaller still.)
        cap = 12 if part else 16
        # RUNTIME failures carry no `error XX123:` lines (live 2026-07-09: the editor smoke
        # failed with an empty slice — the worker got "(see the failing log)" and nothing else).
        # No parseable errors ⇒ hand the worker the LOG TAIL, where the runtime failure lives.
        slice_txt = "\n".join(err_lines[:cap])
        if not slice_txt:
            tail = [ln for ln in (getattr(self, '_last_burn_log', {}).get(effort_id) or '')
                    .splitlines() if ln.strip()][-30:]
            slice_txt = ("THE CHECK'S FAILING OUTPUT (runtime failure — no compiler errors):\n"
                         + "\n".join(tail)) if tail else "(see the failing log in the thread above)"
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
            f"{workspace}\n\n"
            f"The build for `{proj}` is failing. Get it building and working correctly under the "
            f"new API — porting what each piece was doing, the way you'd handle any build fix. "
            f"Please don't remove features or delete code just to silence an error; if something "
            f"genuinely has no equivalent in the new API, say so rather than dropping it.\n\n"
            f"{scope}Current build errors:\n{slice_txt}\n\n"
            f"Work from /workspace; build and check yourself with `{build_line}`. Commit and push "
            f"your progress to `{push_branch}` as you go — unpushed work is lost when your turn "
            f"ends. Start from what's already delivered (`{checkout}`).\n\n"
            f"What this is for:\n{base_goal}"
        )
        # CDCL (§5–6): every round is a FRESH session, so the accumulated clause set is the ONLY
        # thing carrying what earlier rounds already learned. Without it each round re-walks the
        # same dead ends with only the latest error slice for guidance.
        instruction += await self._constraints_context(effort_id)
        # FRESH session per ROUND — parts AND single rounds (live 2026-07-08: every reused
        # session rotted the same way — part rounds 2/4/5 quit in ~90s with nothing pushed, and
        # once the count dropped below the partition threshold the SINGLE rounds did the exact
        # same thing on the main effort session, stalling at 19. A burn-down round is stateless:
        # the goal + error slice carry everything it needs; history only poisons it.)
        session = (f"{effort_id}~pt{part}r{rnd}" if part else f"{effort_id}~bd{rnd}")
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

    async def _research_burndown_stall(self, effort_id: str, errors_log: str) -> bool:
        """Operator 2026-07-12: "research the error MSB3202 for starters, which the workers and pm
        should be able to do" — a burn-down that stalls must try to answer its OWN question (grounded
        research on the failing error) before punting a vague "answer the open question" to the human.
        Ground the failure, inject the findings as steering, and signal ONE more round. Bounded ONCE
        per burn-down loop; best-effort (grounding off / unavailable / not grounded → return False and
        let the loop escalate — carrying whatever was found). Generic across toolchains."""
        if effort_id in self._burndown_researched:
            return False   # already spent this loop's research round — escalate honestly
        self._burndown_researched.add(effort_id)
        if not self.s.grounding_enabled or self._backpressure_recent():
            return False
        brief = _error_brief(errors_log)
        tail = "\n".join([ln for ln in (errors_log or "").splitlines() if ln.strip()][-12:])[:700]
        question = (
            "A build/check keeps failing the same way and mechanical fix rounds aren't clearing it. "
            f"The failing output:\n{tail}\n\nWhat is the ROOT CAUSE and the concrete, actionable fix? "
            "Be specific — exact commands, config, or project/build changes.")
        await self.comms.post(
            Intent.worker_activity,
            f"🔎 burn-down stalled on `{brief}` — researching the error before raising it with you "
            f"(the org answers its own question first).", effort_id=effort_id)
        try:
            res = await self.grounding.ground(question)
        except Exception as exc:  # noqa: BLE001 — research is best-effort, never blocks the loop
            log.info("burn-down research failed for %s: %s", effort_id, exc)
            return False
        if not (res.grounded and (res.claims or res.summary)):
            return False
        body = "# RESEARCHED FIX CONTEXT (openbrain-research — the stall's likely cause + fix)\n"
        if res.summary:
            body += res.summary.strip() + "\n"
        for c in res.claims[:15]:
            body += f"- {c}\n"
        await self.charters.set_steering(effort_id, body, actor="burndown-research")
        self._burndown_research_note[effort_id] = body[:1200]
        await self.audit.log("burndown_researched", effort_id=effort_id,
                             payload={"claims": len(res.claims), "brief": brief[:200]})
        await self.comms.post(
            Intent.worker_activity,
            f"🔎 researched it — injected {len(res.claims)} grounded claim(s) as fix context; giving "
            f"it one more round before I escalate.", effort_id=effort_id)
        return True

    async def _burndown_elevate(self, effort_id: str, counts: list[int], errors_log: str,
                                why: str) -> None:
        """Burn-down stopped short of green — the HONEST, evidence-carrying elevation: the full
        error trajectory (from the org's own builds, not worker claims), what still fails, and
        the real next moves. The branch keeps all progress; the effort stays open."""
        traj = " → ".join(str(c) if c >= 0 else "?" for c in counts)
        error_lines = _error_lines(errors_log)
        await self.audit.log("burndown_stalled", effort_id=effort_id,
                             payload={"why": why, "trajectory": counts})
        # If the org RESEARCHED this stall (operator 2026-07-12), carry the findings into the
        # escalation so it is ACTIONABLE — the org already tried to answer its own question, and the
        # human sees the grounded cause/fix, not a bare "answer the open question".
        researched = self._burndown_research_note.pop(effort_id, "")
        research_note = (f"\n\n_I researched this and gave it another round first — the grounded "
                         f"cause/fix I tried:_\n{researched[:800]}" if researched else "")
        # A RUNTIME stall (the check is RED but there are NO compiler errors to count — it built
        # and RAN, then failed/crashed the same way every round) is a DIFFERENT animal from a
        # compiler-error plateau. Grinding code-rounds can't clear a failure the count can't even
        # measure. Show the LOG TAIL (where the runtime failure lives, not an empty error list) and
        # frame it honestly — it usually needs a human to run/observe it, not an API judgment call.
        # Live 2026-07-09: an editor-headless-launch stalled [0,0,0] and the generic "needs a
        # judgment call on the API" escalation showed 'still failing: <nothing>' — confusing.
        runtime_stall = not error_lines and bool((errors_log or "").strip())
        if runtime_stall:
            tail = "\n".join([ln for ln in (errors_log or "").splitlines()
                              if ln.strip()][-14:])[:900]
            body = (
                f"🧱 **{effort_id}** — burn-down STALLED ({why}) on a **runtime failure**, not "
                f"compiler errors. It builds and **runs**, then fails the same way every round — "
                f"there are no `file.ext(line): error` lines to fix, so the org can't measure or "
                f"grind this down mechanically. The failing run's tail:\n"
                f"```\n{tail}\n```\n"
                f"The branch keeps every fix so far. A runtime failure like this usually needs "
                f"**you to run it and observe** (or a repro/different approach) — more code-rounds "
                f"chase a target the check can't see. Tell me how to proceed, paste what you "
                f"observe when you run it, or say **“keep going”** for more rounds anyway."
                + research_note)
        else:
            brief = _error_brief(errors_log)
            remaining = "\n".join(error_lines[:15])
            body = (
                f"🧱 **{effort_id}** — burn-down STALLED ({why}). The honest picture, from the org's "
                f"own build logs:\n"
                f"- error trajectory across rounds: **{traj}**\n"
                f"- still failing: {brief}\n"
                + (f"```\n{remaining[:900]}\n```\n" if remaining else "")
                + f"The branch keeps every fix so far — nothing is lost. What remains hasn't yielded "
                f"to mechanical rounds"
                + (". I researched it (below) and it still didn't clear — this likely needs a "
                   "judgment call or a change the workers can't make (e.g. a workspace/build-setup "
                   "issue, not code)." if research_note else
                   ", which usually means it needs a judgment call (an API choice, a design "
                   "decision, or missing context).")
                + f" Tell me how you'd like to proceed, or say **“keep going”** for more rounds."
                + research_note)
        await self.comms.post(Intent.escalation, body, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, body,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "needs-attention")

    @staticmethod
    def _failure_sig(log: str) -> str:
        """A stable signature of WHAT failed — the normalized log tail (digits/hashes masked).
        Runtime failures have no countable error lines, so 'the failure changed' is the
        progress signal a count can't give (2026-07-09)."""
        import hashlib
        tail = [ln.strip() for ln in (log or "").splitlines() if ln.strip()][-25:]
        norm = re.sub(r"[0-9a-f]{7,}|\d+", "#", " ".join(tail).lower())
        return hashlib.sha1(norm.encode()).hexdigest()[:16]

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
            if verdict == "infra":
                await self._elevate_check_infra(effort_id, out)
                return None
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
        if (result is not None and "NO CHANGES:" in (result.output or "")
                and await self._no_changes_acceptable(effort_id, result.output or "")):
            # Gate the bare no-op (same bar as the first-publish path): a behavioral/fix goal is
            # NEVER closed done because a re-engaged worker on a stale branch says "already published,
            # nothing to change" (live 2026-07-11: the reopened atlas effort's branch pre-existed at
            # delivery-1's commits, the worker reported NO CHANGES, and it FALSE-closed done). If not
            # acceptable, fall through to the honest re-verify + "delivered nothing new" escalation.
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
        if (result is not None and "NO CHANGES:" in (result.output or "")
                and await self._no_changes_acceptable(effort_id, result.output or "")):
            # Same gate: a bare no-op can't close a behavioral/fix goal even after the empty-delivery
            # re-engage; if unacceptable, fall through to the honest "zero net changes" escalation.
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
            "repo": repo, "pr_number": pr_number, "effort_id": effort_id, "branch": branch,
            "mgmt_thread": self._mgmt_thread_of(effort_id) or "",
            "asked_at": _now_iso(),
        }
        await self.pending.save(merge_id, "merge", self._pending_merge[merge_id])
        await self.audit.log("delivery_pr_opened", effort_id=effort_id,
                             payload={"repo": repo, "pr": pr_number, "merge_id": merge_id,
                                      "base_sha": (self._expected_base.get(effort_id)
                                                   or {}).get("sha", "")})
        return res.url

    async def _integrate_to_develop(
        self, effort_id: str, repo: str | None, delivery: BranchDelivery,
    ) -> str:
        """DEVELOP-BRANCH INTEGRATION (operator 2026-07-15: "the PRs should be separate as they are
        now, but they should be MERGED INTO DEVELOPMENT" — the org left N parallel PRs off main and
        never converged them into one product 'like an actual project'). Fold an ACCEPTED delivery
        (green + its own PR) into the project's `develop` branch so the product ACCUMULATES across
        efforts, and keep ONE standing `develop → main` PR as the whole-product gate. Merge-into-
        develop is autonomous; merge-to-main stays HUMAN (D4). A conflict is surfaced honestly, not
        forced. Best-effort — never blocks the closure. Returns a closure note ('' when off)."""
        if not (self.s.develop_integration and repo and delivery.landed
                and self.github is not None and self.s.github_app_enabled):
            return ""
        dev = self.s.develop_branch
        try:
            seed = await ensure_branch(
                self.github, repo, dev,
                api_base=self.s.github_api_base, transport=self._gh_transport)
            if not seed.ok:
                # Record the ATTEMPT (P8 #1: the closure invariant asserts integration was
                # attempted, not that it succeeded — an unrecorded failure is invisible).
                await self.audit.log(
                    "develop_integration", effort_id=effort_id,
                    payload={"branch": delivery.branch, "develop": dev, "ok": False,
                             "summary": f"couldn't prepare {dev}: {seed.summary}"[:160]})
                return f"\n🔀 _Couldn't prepare `{dev}` for integration ({seed.summary})._"
            # The merge commit records the ACCEPTANCE status (operator 2026-07-15, git-history
            # eval: a reader should see WHY a branch was folded in) — org-verified green + gated.
            sha = (delivery.head_sha or "")[:10]
            merged = await merge_branch(
                self.github, repo, dev, delivery.branch,
                message=(f"Integrate {effort_id} into {dev} (accepted)\n\n"
                         f"Accepted per-effort delivery: org-verified green build, D2 gate passed"
                         + (f", QA-evaluated" if self.s.qa_gate != "off" else "")
                         + f". Source branch {delivery.branch}"
                         + (f" @ {sha}" if sha else "") + "."),
                api_base=self.s.github_api_base, transport=self._gh_transport)
        except Exception as exc:  # noqa: BLE001 — integration is best-effort; never wedge closure
            log.warning("develop integration failed for %s: %s", effort_id, exc)
            # Same honesty on the error path: the attempt happened — leave its trace so the
            # closure invariant (P8 #1) sees "attempted and failed", not a silent void.
            try:
                await self.audit.log(
                    "develop_integration", effort_id=effort_id,
                    payload={"branch": delivery.branch, "develop": dev, "ok": False,
                             "summary": f"error: {exc}"[:160]})
            except Exception:  # noqa: BLE001
                pass
            return ""
        await self.audit.log(
            "develop_integration", effort_id=effort_id,
            payload={"branch": delivery.branch, "develop": dev, "ok": merged.ok,
                     "summary": merged.summary[:160]})
        if not merged.ok:
            # a conflict = this effort overlaps accumulated develop work — the operator decides;
            # its own PR stays open, nothing is force-merged.
            return (f"\n🔀 **Not integrated into `{dev}`** — {merged.summary}. The effort's own PR "
                    f"stays open; folding it into `{dev}` needs a manual call.")
        # Keep the standing whole-product PR: `develop` → default branch (merge stays human, D4).
        prod_pr = ""
        try:
            res = await open_pull_request(
                self.github, repo, dev,
                title="agent: develop → main (integrated product)",
                body=(f"The accumulated, integrated product — every accepted per-effort delivery "
                      f"merged into `{dev}`. Review the WHOLE product here; merge to the default "
                      f"branch is human-gated (D4)."),
                base_branch="", api_base=self.s.github_api_base, transport=self._gh_transport)
            prod_pr = res.url if res.ok else ""
        except Exception as exc:  # noqa: BLE001
            log.debug("develop→main PR ensure failed for %s: %s", effort_id, exc)
        note = f"\n🔀 **Integrated into `{dev}`** — {merged.summary}."
        if prod_pr:
            note += (f"\n📦 **Whole-product PR (`{dev}` → default, your gate):** {prod_pr} — "
                     f"review the complete product here.")
        return note

    async def _run_check(self, effort_id: str, check_cmd: str,
                         branch: str | None = None, repo: str | None = None,
                         ) -> tuple[str, str, str]:
        """Run the project's D2 check — ORG-RUN first. The deterministic `/check` exec (a
        verifier slot, real exit code, no model) runs `check_cmd` against the DELIVERED branch;
        only when that route is unavailable does it fall back to the old worker-reported wake.
        Gym finding ⑥ (2026-07-15): every gym delivery closed "D2 passed (worker-reported)"
        because this path only ever ASKED THE WORKER — the subject of verification was also its
        executor, on a project where the deterministic route worked fine. Verification is a
        machine step. Returns (status, tail, provenance): status 'pass'|'fail'|'unknown',
        provenance 'org-run'|'worker-reported'."""
        if branch and repo:
            self._verify_seq += 1
            det = (f"git fetch origin {branch} && git checkout -f FETCH_HEAD && {check_cmd}")
            try:
                repo_token = await self._project_token(effort_id)
                rc, out, timed_out = await self.router.exec_check(
                    effort_id, command=det, session_id=f"{effort_id}~d2{self._verify_seq}",
                    repo=repo, repo_token=repo_token, timeout=900)
                if timed_out:
                    return "unknown", "org-run check timed out", "org-run"
                if rc == 0:
                    return "pass", "", "org-run"
                if rc is not None:
                    return "fail", (out or "").strip()[-600:], "org-run"
            except Exception as exc:  # noqa: BLE001 — deterministic route missing → fall back
                log.debug("org-run D2 check unavailable for %s: %s", effort_id, exc)
        loc = await self.router.effort_thread(effort_id)
        if not loc:
            return "unknown", "no effort thread", "worker-reported"
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
            return "unknown", str(exc)[:160], "worker-reported"
        out = (result.output or "") if result else ""
        if "CHECK: PASS" in out:
            return "pass", "", "worker-reported"
        if "CHECK: FAIL" in out:
            return "fail", out.split("CHECK: FAIL", 1)[1].strip()[:600], "worker-reported"
        return "unknown", out.strip()[:200], "worker-reported"

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
        status, tail, prov = await self._run_check(
            effort_id, check_cmd, branch=delivery.branch, repo=repo)
        if status == "pass":
            # P15.1 — SAY WHERE IT WAS VERIFIED. gym-013 reported 48/48 green and the operator's
            # own run was 47/48: a `UnicodeEncodeError` on a high-priority item under CP1252, i.e.
            # a High-severity crash on the platform the product is actually used on. The org checks
            # inside a Linux container and had no way to see it. Until a second verification lane
            # exists, the honest thing is to name the platform rather than let "checks passed" read
            # as "works where you are".
            return (f"\n🧪 **D2 checks passed** (`{check_cmd}`, {prov})."
                    f"\n_Verified in the org's Linux worker container — **not** on your own "
                    f"platform. Encoding-, path- and shell-specific behaviour is unverified._"), delivery
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
            status2, tail2, prov2 = await self._run_check(
                effort_id, check_cmd, branch=delivery.branch, repo=repo)
            if status2 == "pass":
                return (f"\n🧪 **D2 checks passed after one fix round** (`{check_cmd}`, "
                        f"{prov2}).", delivery)
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

    async def _acceptance_corpus_gate(self, effort_id: str, repo: str, delivery: BranchDelivery,
                                      merge_id: str) -> tuple[str, BranchDelivery]:
        """DURABLE ACCEPTANCE CORPUS (ORCHESTRATION-DESIGN §10, the finding→durable-check pipeline).
        The project's PERMANENT checks — each an operator review finding made executable — run against
        every delivery. Distinct from D2 (the project's own test suite): the corpus OUTLIVES every
        round and encodes the human's standard, so the org cannot repeat a defect a human already found.
        Same hard red-gate as D2 (route back once → re-check → still red → withdraw the merge +
        burn-down): a broken promise never travels forward. Empty corpus → silent (no clutter for
        projects that have none yet). Returns (note-for-closure, possibly-updated delivery)."""
        proj = await self._effort_project(effort_id)
        checks = await self.projects.list_acceptance_checks(proj) if proj else []
        if not checks:
            return "", delivery

        async def _run_all(d: BranchDelivery) -> list[tuple[dict, str]]:
            out: list[tuple[dict, str]] = []
            for c in checks:
                status, tail, _prov = await self._run_check(
                    effort_id, c["body"], branch=d.branch, repo=repo)
                if status == "fail":          # 'unknown' can't run → don't block; 'pass' → ok
                    out.append((c, tail))
            return out

        def _fmt(fs: list[tuple[dict, str]]) -> str:
            return "\n".join(f"- [{c['id']}] {c['origin_note']}\n    cmd: `{c['body']}`\n    {t[:300]}"
                             for c, t in fs)

        fails = await _run_all(delivery)
        if not fails:
            await self.audit.log("acceptance_corpus_passed", effort_id=effort_id,
                                 payload={"total": len(checks)})
            return (f"\n📐 **Acceptance corpus passed** — {len(checks)} durable check(s) from prior "
                    f"reviews.", delivery)
        # RED — route back ONCE, naming the exact broken standards (executable, not prose).
        await self.comms.post(
            Intent.worker_activity,
            f"❌ **Acceptance corpus failed** ({len(fails)}/{len(checks)}) — durable checks captured "
            f"from earlier human reviews; these are non-negotiable. Routing back to fix:\n{_fmt(fails)}",
            effort_id=effort_id)
        loc = await self.router.effort_thread(effort_id)
        if loc:
            channel_id, root = loc
            fix_instruction = (
                f"THE PROJECT'S DURABLE ACCEPTANCE CHECKS FAILED on your delivered branch. Each encodes "
                f"a standard the org committed to from an earlier human review — they are NOT optional "
                f"and must not be worked around. Fix the CAUSE of each (stay in scope of your task), "
                f"then commit + push to the SAME branch ({delivery.branch}):\n{_fmt(fails)}\n"
                f"  git add -A && git commit -m \"fix: acceptance corpus\" && "
                f"git push origin {delivery.branch}\nThen reply with what you changed.")
            try:
                repo_token = await self._project_token(effort_id)
                await self.router.wake(
                    effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                    session_id=await self._session_for(effort_id), instruction=fix_instruction,
                    repo=repo, repo_token=repo_token)
            except (httpx.HTTPStatusError, httpx.TransportError, NoCapacityError) as exc:
                log.warning("acceptance-corpus fix wake failed for %s: %s", effort_id, exc)
            new_delivery = await self._verify_delivery(effort_id, repo)
            if new_delivery.landed:
                delivery = new_delivery
            fails = await _run_all(delivery)
            if not fails:
                await self.audit.log("acceptance_corpus_passed", effort_id=effort_id,
                                     payload={"total": len(checks), "after_fix": True})
                return (f"\n📐 **Acceptance corpus passed after one fix round** — {len(checks)} "
                        f"check(s).", delivery)
        # STILL red → withdraw the merge gate + burn-down (never ship a broken promise).
        self._pending_merge.pop(merge_id, None)
        await self.pending.delete(merge_id)
        self._queue_burndown(effort_id, "acceptance corpus failing:\n" + _fmt(fails))
        await self.audit.log("acceptance_corpus_failed", effort_id=effort_id,
                             payload={"failed": [c["id"] for c, _ in fails], "total": len(checks)})
        await self.comms.post(
            Intent.escalation,
            f"⛔ **Acceptance corpus still failing** ({len(fails)}/{len(checks)}) after a fix round. "
            f"The merge gate is withdrawn — the org will not ship a delivery that breaks a standard it "
            f"already committed to. Burn-down engaged.", effort_id=effort_id)
        return (f"\n⛔ **Acceptance corpus FAILED** ({len(fails)}/{len(checks)} durable check(s)) — "
                f"merge gate withdrawn; burn-down continues.", delivery)

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
        if not res.ok:
            # RESTORE the gate so a merge that DIDN'T land — a transient GitHub error, or a resolvable
            # not-mergeable state (a conflict to fix, a required check to wait on) — can be RETRIED.
            # Popping it before the attempt would otherwise STRAND the delivery: the operator says
            # "merge it" again and gets "nothing pending". Re-add + re-persist, with a plain handle. A
            # truly terminal failure (already merged / PR gone) simply fails the retry again, and
            # "abort" drops it. Best-effort persistence; the in-memory gate is what matters.
            self._pending_merge[merge_id] = entry
            try:
                await self.pending.save(merge_id, "merge", entry)
            except Exception as exc:  # noqa: BLE001
                log.debug("re-persist pending merge %s failed: %s", merge_id, exc)
            msg += (f"\n_The merge didn't go through, so I kept the gate open — fix it (or wait, if "
                    f"it's transient) and say **“merge it”** again, or **“abort {merge_id}”** to drop "
                    f"it._")
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
            # AUTO-HYGIENE (operator 2026-07-10 "so many branches, its a mess"): the merged head's
            # commits are now in `main`, so the branch is dead weight — delete it instead of letting
            # agent/* branches pile up (the root cause: hygiene only ever HINTED, never deleted).
            # Best-effort; a delete failure never taints the merge report. Only the just-merged
            # head, only agent/* (delete_branch refuses the default branch itself).
            head = entry.get("branch") or ""
            if head.startswith("agent/"):
                try:
                    dr = await delete_branch(self.github, entry.get("repo") or "", head,
                                             api_base=self.s.github_api_base,
                                             transport=self._gh_transport)
                    await self.audit.log("branch_deleted", effort_id=entry.get("effort_id"),
                                         payload={"repo": entry.get("repo"), "branch": head,
                                                  "ok": dr.ok, "reason": "auto-post-merge"})
                    if dr.ok:
                        msg += f"\n🧹 _Cleaned up the merged branch `{head}`._"
                except Exception as exc:  # noqa: BLE001 — cleanup never blocks the merge report
                    log.debug("post-merge branch delete failed: %s", exc)
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

    _TIDY_RE = re.compile(
        r"\btidy\b|"
        # The cleanup verb and its target must be SEPARATE whitespace-delimited words, so a
        # hyphenated FEATURE compound in a goal/steering message ("a clear-completed action") is NOT
        # read as a board-cleanup command (2026-07-16 gym: "clear-completed" swallowed BOTH a
        # start-effort fire AND a clarification answer as "Tidied up", dropping the work).
        r"\b(?:clean(?:\s*up)?|clear(?:\s+out)?|sweep|wrap\s+up)\b\s+(?:\w+\s+){0,4}?"
        r"(?:board|efforts?|finished|done|stale|completed?|old|mess|everything|up)\b", re.I)

    async def _nl_tidy_up(self, message: str, channel_id: str, thread_id: str | None) -> bool:
        """"Tidy up" the board the way a human would (operator 2026-07-10 "it would be good to have
        this available … so the pm knows what to do"): understand which OPEN efforts are actually
        COMPLETED (idle + their branch already merged into `main` → the work landed) vs still active
        vs idle-with-unmerged-work, and — on a cleanup ask — close the completed ones and delete
        their merged branches in one go. Internal `__` singletons are already hidden. Report-only on
        a question. Never touches active efforts or idle work whose branch ISN'T merged (that would
        lose it). Generic across projects."""
        if not self._TIDY_RE.search(message):
            return False
        # A start-effort directive is NEVER a board-tidy — even though its GOAL text may mention
        # clear/complete/done as FEATURE words ("a clear-completed action"). The deterministic
        # new-effort idiom handles these first; this is defense in depth (2026-07-16 gym finding).
        if re.match(r"^\s*(?:in\s+\S+[,:]?\s+)?(?:start|open|create|kick\s*off|launch)\s+"
                    r"(?:a\s+new\s+)?effort\b", message, re.I):
            return False
        # a branch-ONLY ask ("clean up the branches") belongs to _nl_branch_hygiene
        if re.search(r"\bbranch(?:es)?\b", message, re.I) and not re.search(
                r"\beffort|\bboard\b|\btidy\b", message, re.I):
            return False
        act = bool(self._BRANCH_HYGIENE_ACT_RE.search(message))

        async def say(m: str) -> None:
            await self.chat.post(channel_id, m, thread_id=thread_id)

        efforts = [e for e in await self.gate.snapshot(open_only=True)
                   if not (e.get("id") or "").startswith("__")]
        if not efforts:
            await say("The board's already clean — no open efforts (internal plumbing aside).")
            return True
        status_map = await self._effort_status_map(efforts)
        # classify each touched repo's agent/* branches ONCE (merged = work is in main)
        merged_by_repo: dict[str, set] = {}
        if self.github is not None and self.s.github_app_enabled:
            for repo in {r for e in efforts if (r := await self._effort_repo(e["id"]))}:
                try:
                    cls = await classify_agent_branches(
                        self.github, repo, api_base=self.s.github_api_base,
                        transport=self._gh_transport)
                    merged_by_repo[repo] = set(cls["merged"])
                except Exception as exc:  # noqa: BLE001
                    log.debug("tidy classify %s failed: %s", repo, exc)
        completed: list[tuple[str, str, str]] = []   # (eid, repo, branch) — done, work in main
        active: list[tuple[str, str]] = []
        idle_open: list[str] = []
        for e in efforts:
            eid = e["id"]
            st = status_map.get(eid, "idle")
            if st in ("running", "paused", "waiting-capacity"):
                active.append((eid, st)); continue
            repo = await self._effort_repo(eid)
            branch = self._effort_branch(eid)
            if repo and branch in merged_by_repo.get(repo, set()):
                completed.append((eid, repo, branch))
            else:
                idle_open.append(eid)
        if act:
            closed: list[str] = []
            for eid, repo, branch in completed:
                await self.gate.set_lifecycle(eid, "done")
                await self.router.update_effort_card(eid, "done")
                closed.append(eid)
                try:
                    res = await delete_branch(self.github, repo, branch,
                                              api_base=self.s.github_api_base,
                                              transport=self._gh_transport)
                    await self.audit.log("branch_deleted", effort_id=eid, payload={
                        "repo": repo, "branch": branch, "ok": res.ok, "reason": "tidy-merged"})
                except Exception as exc:  # noqa: BLE001
                    log.debug("tidy branch delete %s failed: %s", branch, exc)
            lines: list[str] = []
            if closed:
                lines.append(f"✅ Closed **{len(closed)}** completed effort(s) — their work is in "
                             f"`main` and I deleted the merged branch: "
                             + ", ".join(f"`{i}`" for i in closed))
            else:
                lines.append("Nothing to auto-close — no idle effort has its work merged into "
                             "`main` yet (I only close what's genuinely done).")
            if active:
                lines.append("**Kept — active:** "
                             + ", ".join(f"`{i}` ({s})" for i, s in active))
            if idle_open:
                lines.append("**Kept — idle but NOT merged** (name one to archive if you're done "
                             "with it): " + ", ".join(f"`{i}`" for i in idle_open))
            await say("**Tidied up.**\n" + "\n".join(lines))
        else:
            await say(
                "**Board** (internal plumbing hidden). Say **“tidy up”** and I'll close the "
                "completed ones + delete their merged branches:\n\n"
                f"- ✅ **Completed** (idle, work already in `main`): "
                + (", ".join(f"`{i}`" for i, _, _ in completed) or "none") + "\n"
                f"- 🟢 **Active:** "
                + (", ".join(f"`{i}` ({s})" for i, s in active) or "none") + "\n"
                f"- ⚪ **Idle, not yet merged:** "
                + (", ".join(f"`{i}`" for i in idle_open) or "none"))
        return True

    _BRANCH_HYGIENE_RE = re.compile(
        r"\b(?:clean(?:\s*up)?|tidy|prune|sweep|purge|clear\s+out|get\s+rid\s+of|"
        r"delete|remove|which|what|show|list|any|how\s+many|are\s+there)\b"
        r"[^.\n]*\bbranch(?:es)?\b", re.I)
    _BRANCH_HYGIENE_ACT_RE = re.compile(
        r"\b(?:clean(?:\s*up)?|tidy|prune|sweep|purge|clear\s+out|get\s+rid\s+of|"
        r"delete|remove|kill)\b", re.I)

    async def _nl_branch_hygiene(self, message: str, channel_id: str,
                                 thread_id: str | None) -> bool:
        """Branch HYGIENE the way a human reasons about it (operator 2026-07-10 "so many branches,
        its a mess … it should understand the branches were already merged and no longer need to
        be here"): understand a repo's `agent/*` branches by MERGE STATE and either REPORT them or,
        on a cleanup request, delete the ones already CONTAINED IN MAIN (zero loss). Distinct from
        `_nl_branch_delete` (which needs an explicitly-NAMED branch) — this fires on a general
        "clean up / which branches …" ask with NO branch named. Merged→safe to delete; unmerged and
        open-PR branches are NEVER auto-deleted (name them explicitly if you really mean it).
        Returns True when handled here."""
        if not self._BRANCH_HYGIENE_RE.search(message):
            return False
        if re.search(r"\bagent/[\w./-]+", message):
            return False   # an explicit branch name → that's _nl_branch_delete's job, defer
        act = bool(self._BRANCH_HYGIENE_ACT_RE.search(message))

        async def say(msg: str) -> None:
            await self.chat.post(channel_id, msg, thread_id=thread_id)

        if self.github is None or not self.s.github_app_enabled:
            await say("⚠️ I can't manage branches — the GitHub App isn't set up.")
            return True
        # Which repos? A named project scopes to it (+ any composition partner it's vendored into);
        # otherwise every onboarded project. Merged branches are safe to clean on any of them.
        projects = await self.projects.list()
        named = None
        for p in projects:
            if re.search(rf"\b{re.escape(p['slug'])}\b", message, re.I):
                named = p
                break
        if named:
            repos = {named["repo_url"]}
            host = await self._vendored_host(named["slug"])
            if host:
                hp = await self.projects.get(host[0])
                if hp:
                    repos.add(hp["repo_url"])
            # a project this one is the HOST of (its vendored children carry the same effort branch)
            for q in projects:
                qh = await self._vendored_host(q["slug"])
                if qh and qh[0] == named["slug"]:
                    repos.add(q["repo_url"])
        else:
            repos = {p["repo_url"] for p in projects}
        if not repos:
            await say("No onboarded repos to check for stale branches.")
            return True

        blocks: list[str] = []
        deleted_any = False
        for repo in sorted(repos):
            short = repo.split("github.com/")[-1].rstrip("/")
            try:
                cls = await classify_agent_branches(
                    self.github, repo, api_base=self.s.github_api_base,
                    transport=self._gh_transport)
            except Exception as exc:  # noqa: BLE001 — one bad repo never fails the whole sweep
                log.debug("branch classify for %s failed: %s", repo, exc)
                blocks.append(f"**{short}** — couldn't read branches ({str(exc)[:80]}).")
                continue
            merged, unmerged, open_pr = cls["merged"], cls["unmerged"], cls["open_pr"]
            if not (merged or unmerged or open_pr):
                blocks.append(f"**{short}** — clean (no `agent/*` branches).")
                continue
            lines = [f"**{short}**"]
            if act and merged:
                results = []
                for b in merged:
                    res = await delete_branch(self.github, repo, b,
                                              api_base=self.s.github_api_base,
                                              transport=self._gh_transport)
                    await self.audit.log("branch_deleted", payload={
                        "repo": repo, "branch": b, "ok": res.ok, "reason": "merged-hygiene",
                        "clearance": message[:160]})
                    results.append((b, res.ok))
                    deleted_any = deleted_any or res.ok
                ok = [b for b, k in results if k]
                if ok:
                    lines.append(f"- 🧹 deleted {len(ok)} already-merged: "
                                 + ", ".join(f"`{b}`" for b in ok))
                bad = [b for b, k in results if not k]
                if bad:
                    lines.append(f"- ⚠️ couldn't delete: " + ", ".join(f"`{b}`" for b in bad))
            elif merged:
                lines.append(f"- ✅ {len(merged)} **safe to delete** (already merged into "
                             f"`{cls['default']}`): " + ", ".join(f"`{b}`" for b in merged))
            if unmerged:
                lines.append("- ⚠️ **has unmerged commits** (kept — name it explicitly to delete): "
                             + ", ".join(f"`{u['name']}` (+{u['ahead']})" for u in unmerged))
            if open_pr:
                lines.append("- 📬 **live PR — kept**: " + ", ".join(f"`{b}`" for b in open_pr))
            blocks.append("\n".join(lines))
        header = ("**Branch cleanup** — deleted the already-merged `agent/*` branches (their commits "
                  "are all in `main`, nothing lost); kept anything unmerged or with a live PR:\n\n"
                  if act and deleted_any else
                  "**Branch cleanup** — nothing deleted (no already-merged branches, or you were "
                  "just asking). Say _\"clean up the merged branches\"_ to delete the safe ones:\n\n"
                  if act else
                  "**Branch inventory** (`agent/*`, by merge state). Say _\"clean up the merged "
                  "branches\"_ and I'll delete the safe ones:\n\n")
        await say(header + "\n\n".join(blocks))
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

    _REMOVAL_GOAL_RE = re.compile(
        r"\b(remov|delet|drop|prune|clean\s*up|cleanup|strip|deprecat|retire|get\s+rid|"
        r"tear\s+out|purge|excis)", re.I)

    # A goal that describes a RUNTIME / INTERACTION / VISUAL symptom — something you only
    # observe by RUNNING the program and using it, not by compiling it. A green build is
    # necessary but NOT sufficient to prove such a symptom is gone: our checks here are
    # headless and cannot click a UI, move a cursor, or watch a window. Generic for any
    # project (live 2026-07-10: the atlas effort — "the editor throws this at runtime when
    # clicked" — built green and closed 'done', but the actual interaction-triggered crash
    # was never reproduced/verified; a green build masqueraded as a fixed symptom).
    _RUNTIME_SYMPTOM_RE = re.compile(
        r"\bat\s+runtime\b|\bruntime\s+(?:error|exception|crash|failure)|"
        r"when\s+(?:i\s+|you\s+|the\s+user\s+)?(?:click|press|open|run|launch|select|drag|"
        r"hover|type|use|navigat|move|scroll|resize|load)\w*|"
        r"\bclick(?:ed|ing|s)?\b|\bcursor\b|\bmouse\b|\bkeyboard\b|"
        r"\b(?:crash(?:es|ed|ing)?|freezes?|frozen|hangs?|hung|stutter)\b|"
        r"\bunhandled\s+exception\b|throws?\s+(?:this\s+|it\s+)?(?:at|when|on|during)\b|"
        r"doesn'?t\s+(?:display|show|render|appear|draw|load|open|launch|respond|animate)|"
        r"\b(?:menu|button|dialog|toolbar|drop-?down|panel|widget|editor\s+ui|the\s+ui)\b|"
        r"on\s+(?:launch|startup|start-?up|exit|load|open|resize|shutdown)\b|"
        r"nothing\s+happens|black\s+screen|blank\s+screen|no\s+window|misrender|glitch",
        re.I)

    def _runtime_symptom_phrase(self, goal_text: str) -> str | None:
        """If the goal describes a runtime/interaction/visual symptom (not a build error),
        return a short human snippet around the match — else None. Used to caveat a
        build-only-verified delivery: a green build never proves a run-time symptom fixed."""
        if not goal_text:
            return None
        # P12.1 — A SYMPTOM MUST BE REPORTED, NOT FORBIDDEN. The regex below is keyword-only: it
        # carries no polarity and no subject, so "must never crash" reads exactly like "it
        # crashes", and "your turn will hang" reads as a product defect. gym-010 (2026-07-19) was a
        # greenfield feature build on an empty template, and it matched precisely two words —
        # `crashes` from "never corrupts or CRASHES on malformed data" (a quality REQUIREMENT) and
        # `hang` from "it will HANG your turn" (a warning about the WORKER'S OWN TURN, not the
        # product). That single match appended the whole reproduce-the-symptom protocol, which then
        # demanded `BEFORE: FAIL` evidence of a failure that did not exist — so the worker invented
        # one. Every match is now tested for both defects before it counts.
        for m in self._RUNTIME_SYMPTOM_RE.finditer(goal_text):
            before = goal_text[max(0, m.start() - 60):m.start()]
            sent_start = max((before.rfind(c) for c in ".!?\n"), default=-1)
            clause = before[sent_start + 1:] if sent_start >= 0 else before
            # (1) NEGATED / ASPIRATIONAL — a requirement that it must not happen, not a report
            #     that it did. Scoped to the clause so a distant "not" can't suppress a real report.
            if re.search(r"\b(?:never|not|n't|cannot|can't|avoid|prevent|without|no|"
                         r"instead\s+of|must\s+not|should\s+not|shouldn't)\b", clause, re.I):
                continue
            # (2) SECOND-PERSON PROCESS WARNING — describes the worker's own turn or environment,
            #     never the deliverable ("it will hang your turn", "you are a headless worker").
            after = goal_text[m.end():m.end() + 60]
            if re.search(r"\byour\s+(?:turn|workspace|session|run)\b", clause + after, re.I):
                continue
            start = max(0, m.start() - 32)
            end = min(len(goal_text), m.end() + 32)
            snippet = goal_text[start:end].strip().replace("\n", " ")
            return ("…" if start else "") + snippet + ("…" if end < len(goal_text) else "")
        return None

    async def _gate_removals(
        self, effort_id: str, repo: str, delivery: BranchDelivery
    ) -> bool:
        """ANTI-DELETE-TO-PASS gate — a green reached by DELETING features is NOT a fix.

        LIVE 2026-07-14: the FNA→MonoGame port reached `burndown_green` by DELETING
        `src/Murder.Editor/Core/Cursor/MouseCursor.Sdl.cs` (181 lines — the CURSOR, a critical
        component of a working editor) and gutting input/game code, and the org opened PRs off it.
        The removals WERE detected — but `_removal_disclosure` only DISCLOSES, and it ran AFTER the
        PR was already open, so delete-to-pass shipped. Operator (2nd time): "removing the cursor is
        a repeated issue… a critical component to a functioning piece of software."

        So on a goal that is NOT a removal/cleanup goal, a delivery that deletes source files, guts
        method bodies, or is heavily net-negative is REJECTED — no PR, no green. The PM then does what
        it's for: auto-iterate with steering that NAMES what was deleted and requires it be PORTED
        rather than dropped; if it keeps deleting, escalate HONESTLY (never a PR).
        True = clean (proceed). False = blocked. Fail-open on an unreadable diff."""
        if not (repo and self.github is not None and self.s.github_app_enabled):
            return True
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal_text = ""
        if self._REMOVAL_GOAL_RE.search(goal_text or ""):
            return True                      # the operator ASKED for removal — deleting IS the job
        try:
            rm = await read_removal_summary(
                self.github, repo, delivery.branch, api_base=self.s.github_api_base,
                transport=self._gh_transport)
        except Exception as exc:  # noqa: BLE001 — an unreadable diff must never block a good delivery
            log.debug("removal gate diff read failed for %s: %s", effort_id, exc)
            return True
        deleted = rm.get("deleted_files") or []
        gutted = rm.get("gutted_files") or []
        ins, dels = rm.get("insertions", 0), rm.get("deletions", 0)
        net_heavy = dels >= 60 and dels >= ins * 3
        if not (deleted or gutted or net_heavy):
            return True
        what: list[str] = []
        if deleted:
            what.append("DELETED " + ", ".join(f"`{f}`" for f in deleted[:8]))
        if gutted:
            what.append("GUTTED " + ", ".join(f"`{g['file']}`" for g in gutted[:6]))
        if not what and net_heavy:
            what.append(f"net-negative diff (−{dels}/+{ins} lines)")
        listed = "; ".join(what)
        await self.audit.log(
            "delivery_removal_blocked", effort_id=effort_id,
            payload={"branch": delivery.branch, "deleted": deleted[:20],
                     "gutted": [g["file"] for g in gutted][:20], "ins": ins, "dels": dels})
        await self.comms.post(
            Intent.worker_activity,
            f"⛔ **Green by REMOVING code — rejected.** `{delivery.branch}`: {listed}. Deleting a "
            f"feature to get past a compile error is not a port. **No PR.** Re-driving it: what was "
            f"removed has to be PORTED, not dropped.",
            effort_id=effort_id,
        )
        iterating = await self._auto_iterate(
            effort_id,
            f"the delivery reached green by REMOVING code ({listed}) — deleting features is not a fix",
            f"You made the build pass by REMOVING code: {listed}\n"
            f"That is NOT acceptable. Those are FEATURES (e.g. the editor CURSOR), not errors. "
            f"RESTORE everything you deleted or gutted, and PORT it to its MonoGame equivalent "
            f"instead. If some API genuinely has NO MonoGame equivalent, SAY SO and stop — never "
            f"delete a feature to make the build green.",
        )
        if iterating:
            return False                     # auto-iteration owns the retry; no PR now
        await self.router.update_effort_card(effort_id, "error")
        await self.comms.post(
            Intent.escalation,
            f"⚠️ **{effort_id}** keeps reaching green by DELETING code ({listed}) even after "
            f"auto-iterating. **No PR, not merged.** It cannot port these without dropping them — "
            f"that's a real constraint, not a quick fix. ↑ raised to you.",
            effort_id=effort_id,
        )
        await self.comms.post(
            Intent.operator_reply,
            f"⚠️ **{effort_id}** could only go green by REMOVING code ({listed}) — that's features, "
            f"not errors. I **refused the PR**. This needs a real porting decision from you.",
            thread_id=self._mgmt_thread_of(effort_id),
        )
        return False

    # ══════════════════════════════════════════════════════════════════════════
    # P10 — THE DRAIN LOOP (ORCHESTRATION-DESIGN §4, §5, §6.5)
    #
    # The org ran out of work before the project was done. It QA'd once or twice, the loop hit a
    # hard `n >= 2` cap, and it stopped — or a worker shrugged and the effort stranded. There was no
    # task queue, no notion of "next item / next module / next tier", and "nothing left to do" was
    # an ACCIDENT OF AN EMPTY MODEL REPLY rather than a computed fact.
    #
    # The replacement is a loop with a COUNTED termination:
    #
    #     round:  run 3 objective lenses (fresh)  →  gap analysis  →  tasks
    #             work the open tasks
    #             count NEW tasks propagated this round
    #             > 0   → another round
    #             == 0  → this scope is COMPLETE
    #
    # Every step of that is designed against one failure mode: a model being ASKED whether it is
    # done. The lenses observe without being told the goal; the goal enters exactly once, at the
    # comparison step; and the stopping rule is arithmetic on content-addressed rows. No model is
    # ever asked "is this finished?", because in gym-007/008 the answer was reliably yes.
    # ══════════════════════════════════════════════════════════════════════════
    async def _drain_round_no(self, effort_id: str) -> int:
        """This effort's NEXT drain round (1-based), derived from the audit log so the count
        survives a bridge restart — the same reason `_auto_iterate` counts its own events."""
        return await self._event_count(effort_id, "drain_round") + 1

    async def _lens_sweep(
        self, effort_id: str, channel_id: str, root: str, repo: str, delivery: BranchDelivery,
        *, round_no: int, scope_node_id: str | None = None,
    ) -> dict[str, str]:
        """Run all three standing lenses FRESH against the delivered branch and persist their
        reports. Returns `{lens: report_body}` (a lens that couldn't run is simply absent).

        Each lens gets its own fresh session and is read-only. The operator's prompt is passed
        THROUGH VERBATIM — the only thing wrapped around it is the mechanical checkout and the
        change-nothing rule. Nothing here tells a lens what the goal is, offers it a way to say
        "nothing", or asks it to grade: those three omissions ARE the debias (§6.5).

        Best-effort per lens — a hiccup on one lens costs that lens's observations for the round,
        never the delivery."""
        reports: dict[str, str] = {}
        token = await self._project_token(effort_id)
        for lens, prompt in _LENSES:
            self._verify_seq += 1
            # P18 F4 — start each lens with an empty findings file, so a salvage can only ever
            # recover THIS lens's observations (see `_clear_lens_findings`).
            await self._clear_lens_findings(effort_id, round_no=round_no)
            # P17 F8 — WHAT CHANGED IS THE LEAST-EXERCISED SURFACE. A lens sweeps the branch as a
            # flat artifact and gives a line written two commits ago no more scrutiny than one that
            # has survived several rounds. gym-015: round 1's drain replaced `rest.split()` with a
            # bare `shlex.split()`, which raises on an unbalanced quote and kills the REPL session
            # on a typo. Round 2's lens probed `_parse_repl_line` EIGHT times — twice with quotes,
            # both balanced — and filed "REPL parser handles quoted strings ... correctly" under
            # What Works Well. The loop certified its own regression, three rounds running.
            # This is PRIORITISATION, not narrowing: the whole scope is still in play, so F3's
            # completeness contract is untouched. A diff is also an objective artifact, so the
            # P10.1 debias survives — it reveals nothing about the goal.
            changed = ""
            if round_no > 1:
                changed = (
                    "\nRECENTLY CHANGED — the newest and least-exercised code on this branch:\n"
                    "  git diff --stat HEAD~1 HEAD && git diff HEAD~1 HEAD | head -200\n"
                    "Run that first. Code changed in the last round has been exercised least and "
                    "is where a regression is most likely; probe it HARDER than the rest, "
                    "especially with malformed or hostile input. Do NOT limit your assessment to "
                    "it — cover everything the questions below ask about.\n")
            instr = (
                f"EVALUATION — you did NOT build this, and you will CHANGE NOTHING (no edits, no "
                f"git writes; this is an evaluative turn). First check out the DELIVERED branch:\n"
                f"  git fetch origin {delivery.branch} && git checkout -f {delivery.branch}\n"
                f"{changed}\n"
                f"{prompt}\n\n"
                f"Answer every question above, in order, and assess against those criteria AS "
                f"WRITTEN. Do not decide a different standard for the project because it is small, "
                f"a script, or early — no 'for its scope', 'at this scale', or 'good enough for a "
                f"prototype'. Something that behaves wrongly is wrong at any size.\n"
                # P17 F4 — WRITE EACH FINDING AS YOU ESTABLISH IT. A report composed at the end is
                # lost entirely when the turn runs out of budget: gym-015's goal_alignment lens
                # spent 5m27s on genuinely good adversarial probing (hostile inputs, a directory
                # where the data file belongs, `--due 2026-02-30`) and emitted 70 characters of
                # preamble. That was CONTEXT exhaustion, not a timeout — the harness bound is 90
                # minutes — so no watchdog or timeout change can fix it, and a stopping rule would
                # have cut the turn before the probe that found the one real bug in the codebase.
                f"Write your report as plain prose. Report what you actually observe.\n"
                # P18 F4 — THE FINDINGS MUST LEAVE THE TURN AS AN ARTIFACT, NOT AS CONTEXT.
                # P17 asked for incremental emission in prose and it did not work: gym-016 round 2
                # ran ~30 probes over four minutes and emitted 44 characters
                # ("Now let me test malformed database handling:"), the same failure as gym-015's
                # 70-char truncation. A model's natural rhythm is probe-then-report, one
                # instruction line does not override it, and the SAME budget funds both — so a
                # lens that probes to exhaustion has nothing left to report with. It is not a
                # timeout either (the harness bound is 5400s; the turn died at ~4 minutes), so no
                # limit change touches it.
                # Writing to a file converts a claim held in context into something on disk that
                # the harness can read back however the turn ended. Same move as every other fix
                # in P17/P18: make the boundary an artifact rather than a promise.
                # P18 F17 — a finding that alleges a misbehaviour must carry the command that
                # shows it. gym-016's clean_code lens asserted "`strptime` still accepts invalid
                # dates like 2025-02-30" — false; it raises, and the CLI exits 1 with a clear
                # message — and that became an open task against correct code. A named
                # reproduction turns the claim into something the org can run before spending a
                # worker on it, which is the same executable-contract move as every other gate.
                f"If a finding says the program MISHANDLES some input, you must have run it. "
                f"State the exact command on its own line, prefixed `REPRO: `, immediately after "
                f"the finding. If you cannot demonstrate it with a command, say plainly that you "
                f"did not verify it.\n"
                f"AFTER EACH CHECK, before moving on, append your finding to a file:\n"
                f"  echo 'FINDING: <one self-contained sentence>' >> {_LENS_FINDINGS_PATH}\n"
                f"Write it as a complete sentence that stands alone — someone reading only that "
                f"line must understand the finding without the rest of your report. Do this as you "
                f"go, not at the end: if this turn runs out of room, the file is all that "
                f"survives. Then write your full report as normal."
            )
            try:
                result = await self.router.wake(
                    effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                    session_id=f"{effort_id}~lens{self._verify_seq}", instruction=instr,
                    repo=repo, repo_token=token,
                    # THE DEBIAS, enforced at the wake: withholding the goal from the instruction
                    # is worthless if the standing context preamble injects it anyway.
                    withhold_goal=True,
                )
            except NoCapacityError:
                # NEVER swallow this. A saturated worker pool means the sweep DIDN'T HAPPEN, and a
                # sweep that didn't happen must not read as a sweep that found nothing. Propagate
                # so the effort PARKS and resumes when capacity returns (the pre-existing
                # no-silent-idle contract) — trap 3: a round needs four worker slots.
                raise
            except Exception as exc:  # noqa: BLE001 — a lens hiccup never blocks the delivery
                log.debug("lens %s wake failed for %s: %s", lens, effort_id, exc)
                continue
            body = ((result.output or "") if result else "").strip()
            if not body:
                continue
            # P11.5: a truncated turn is a MISSING report, not a clean one. Persist it for the
            # audit trail (we want to see what the lens managed to say), but keep it out of
            # `reports` so it can neither satisfy `swept` nor reach gap analysis.
            if not _is_lens_report(body):
                # P18 F4 — the turn died, but it may have banked findings on disk as it went.
                # Recover them: a lens that probed for four minutes and established a dozen real
                # defects should not lose all of them because it never reached its summary.
                salvaged = await self._salvage_lens_findings(effort_id, lens, round_no=round_no)
                await self._record_lens_report(effort_id, f"{lens}:truncated", body,
                                               round_no=round_no, scope_node_id=scope_node_id)
                await self.audit.log("lens_report_truncated", effort_id=effort_id,
                                     payload={"lens": lens, "round": round_no,
                                              "chars": len(body), "body": body[:200],
                                              "salvaged_chars": len(salvaged)})
                if salvaged and _is_lens_report(salvaged):
                    # Recovered enough to be a real report. It counts — including for `swept`,
                    # because the observation genuinely happened; only the summary was lost.
                    log.info("lens %s truncated for %s but %d chars salvaged from findings file",
                             lens, effort_id, len(salvaged))
                    reports[lens] = salvaged
                    await self._record_lens_report(effort_id, f"{lens}:salvaged", salvaged,
                                                   round_no=round_no, scope_node_id=scope_node_id)
                    continue
                log.info("lens %s produced no report for %s (%d chars) — round not swept by it",
                         lens, effort_id, len(body))
                continue
            reports[lens] = body
            await self._record_lens_report(effort_id, lens, body, round_no=round_no,
                                           scope_node_id=scope_node_id)
        await self.audit.log("lens_sweep", effort_id=effort_id,
                             payload={"round": round_no, "lenses": sorted(reports),
                                      "scope": scope_node_id})
        return reports

    # Identifiers a task body can name concretely enough to CHECK: a backticked token, a long
    # flag, or a filename with an extension. Anything vaguer ("improve error handling") is a
    # judgement, not a checkable absence, and is never filtered.
    _NAMED_THING_RE = re.compile(
        r"`([^`\s]{2,60})`|(?<![\w-])(--[a-z][\w-]{1,30})|\b([\w.-]{2,40}\.(?:py|toml|md|json|ya?ml|cfg|ini|txt|ts|js))\b")
    # "add / implement / expose X" — the shapes that ASSERT X is absent. A task that says "fix" or
    # "rename" presupposes existence and must never be dropped for the thing existing.
    _ASSERTS_ABSENCE_RE = re.compile(
        r"\b(add|create|implement|introduce|expose|define|provide|include|write)\b", re.I)

    async def _drop_false_absences(
        self, effort_id: str, derived: list[tuple[str, str]], *, round_no: int,
    ) -> list[tuple[str, str]]:
        """P17 F11 — refuse to create work from an absence the repository refutes.

        P11.4 made the lens report authoritative for what EXISTS ("treat anything it describes as
        DONE"). Nothing was ever authoritative for what it says is MISSING, so an asserted absence
        became work with nothing checking it. gym-015 produced three, from two different agent
        roles: a lens claimed "no type annotations on function signatures" (17 of 17 defs were
        annotated, and it contradicted itself two sentences later); an assessment claimed "there is
        no `__version__` variable defined anywhere" (`todo.py:20`); the REVIEWER claimed
        "`os.makedirs` lacks explicit `exist_ok=True`" (it was there). The `__version__` one was
        dispatched — and the worker then burned its plan turn hex-dumping the file, because the
        task's premise contradicted what was in front of it.

        Deliberately conservative — this drops a task only when EVERY concrete thing it names is
        already present AND the body is phrased as "add/create/implement". Vague bodies, and
        anything phrased as fix/change/remove, pass through untouched: the goal is to stop
        fabricated work, not to second-guess judgement. Failure to check is never a drop."""
        if not derived:
            return derived
        candidates: dict[str, list[str]] = {}
        for _lens, body in derived:
            if not self._ASSERTS_ABSENCE_RE.search(body):
                continue
            names = {g for m in self._NAMED_THING_RE.finditer(body) for g in m.groups() if g}
            if names:
                candidates[body] = sorted(names)
        if not candidates:
            return derived
        # One grep per distinct identifier, in a single shell round-trip. `-F` so `--version` and
        # `todo.py` are literals, not patterns; `-r` over the workspace; quiet exit codes only.
        probes = sorted({n for names in candidates.values() for n in names})
        script = " ; ".join(
            f"grep -rqF -- {shlex.quote(n)} /workspace --exclude-dir=.git 2>/dev/null "
            f"&& echo 'PRESENT {n}' || echo 'ABSENT {n}'" for n in probes[:40])
        try:
            _exit, out, _timed = await self.router.exec_check(
                effort_id, command=script, session_id=f"{effort_id}~absence",
                repo=None, repo_token=None, timeout=120)
        except Exception as exc:  # noqa: BLE001 — unverifiable is NOT refuted; keep every task
            log.debug("false-absence check failed for %s: %s", effort_id, exc)
            return derived
        present = {ln.split(" ", 1)[1].strip() for ln in (out or "").splitlines()
                   if ln.startswith("PRESENT ") and " " in ln}
        if not present:
            return derived
        kept: list[tuple[str, str]] = []
        for lens, body in derived:
            names = candidates.get(body)
            if names and all(n in present for n in names):
                await self.audit.log("false_absence_rejected", effort_id=effort_id,
                                     payload={"round": round_no, "lens": lens,
                                              "body": body[:200], "present": names[:8]})
                continue
            kept.append((lens, body))
        return kept

    # A task body that alleges a specific input is mishandled: it names a quoted literal AND a
    # verb of rejection/acceptance. That shape is CHECKABLE — run the input, see what happens.
    _ALLEGES_MISBEHAVIOUR_RE = re.compile(
        r"\b(reject|accept|handle|validate|guard|prevent|allow)\w*\b", re.I)
    # The reproduction the LENS named, in the form the lens prompt asks for:
    #   REPRO: <command>
    # Nothing is synthesised — an orchestrator-level check cannot know how to drive an arbitrary
    # product, and guessing produced a gym-only probe in the first draft of this fix.
    _REPRO_CMD_RE = re.compile(r"REPRO:\s*(.+?)(?:\n|$)")

    async def _drop_false_defects(
        self, effort_id: str, derived: list[tuple[str, str]], *, round_no: int,
    ) -> list[tuple[str, str]]:
        """P18 F17 — refuse to create work from a MISBEHAVIOUR the code refutes.

        F11 checks asserted ABSENCES ("add X" where X exists) and deliberately never fires on
        fix/reject/remove verbs, because a filter that could delete real repair work is a worse
        failure than one that lets fabricated work through. The consequence is a blind spot
        exactly the size of that exclusion, and gym-016 walked straight into it: the `clean_code`
        lens reported

            "`strptime` still accepts invalid dates like 2025-02-30 — the regex checks shape but
             not semantic validity ... the function claims to validate but does not fully validate"

        which is false (`strptime` raises "day is out of range for month", and the CLI exits 1
        with a clear message). It became the open task "Reject semantically invalid dates in
        _parse_date" — fabricated work against correct code.

        The tractable check is the same executable-contract move as everywhere else in P17/P18: a
        claim of the form "input X is mishandled" is settled by RUNNING X. Where a task alleges a
        misbehaviour and names a concrete literal, feed that literal to the module and see whether
        anything actually goes wrong.

        Conservative by construction, in the direction that matters: a task is dropped ONLY when
        the named input is demonstrably handled correctly (a non-zero exit with a diagnostic and NO
        traceback — a clean rejection). Anything unparseable, unrunnable, ambiguous, or CRASHING is
        KEPT. Failure to reproduce a bug is not the same as proving there is none — but a command
        that exits 1 with "invalid date format" is positive evidence the alleged acceptance does not
        happen. P19 F17-redux draws the line between that and a traceback (a real crash), which the
        first cut could not: see the probe below."""
        if not derived:
            return derived
        # The reproduction must come FROM THE FINDING. An earlier draft of this synthesised a
        # probe (`python3 todo.py add --due <literal>`), which works on the gym's todo CLI and is
        # meaningless on any other project — an orchestrator-level check must not know what the
        # product is. If the lens did not name a way to demonstrate the misbehaviour, that is a
        # finding we cannot check, and an unchecked finding is KEPT.
        candidates: dict[str, str] = {}
        for _lens, body in derived:
            if not self._ALLEGES_MISBEHAVIOUR_RE.search(body):
                continue
            repro = self._REPRO_CMD_RE.search(body)
            if repro:
                candidates[body] = repro.group(1).strip()
        if not candidates:
            return derived
        idx = {i: (body, cmd) for i, (body, cmd) in enumerate(list(candidates.items())[:6])}
        parts = []
        for i, (_body, cmd) in idx.items():
            # `sh -c` so the lens's own command runs as written. A non-zero exit WITH output is
            # positive evidence the input is handled: the program noticed and complained —
            # EXCEPT when that output is a traceback.
            # P19 F17-redux — A TRACEBACK IS A CRASH, NOT A CLEAN REJECTION. gym-017's undo bug is
            # real and its repro exits non-zero with a `JSONDecodeError` traceback; the old rule
            # ("non-zero + output → HANDLED") could not tell that crash from an argparse exit-2, and
            # would have DROPPED a real critical bug as fabricated. So a `Traceback (most recent
            # call last)` in the output means the input is NOT handled — keep the task. Only a
            # non-zero exit WITHOUT a traceback (a validation message, argparse usage) is a clean
            # rejection worth dropping the fabricated task over.
            parts.append(
                f"out=$(cd /workspace && sh -c {shlex.quote(cmd)} 2>&1); rc=$?; "
                f"if printf '%s' \"$out\" | grep -qF 'Traceback (most recent call last)'; then "
                f"echo 'UNPROVEN {i}'; "
                f"elif [ $rc -ne 0 ] && [ -n \"$out\" ]; then echo 'HANDLED {i}'; "
                f"else echo 'UNPROVEN {i}'; fi")
        try:
            _exit, out, _timed = await self.router.exec_check(
                effort_id, command=" ; ".join(parts),
                session_id=f"{effort_id}~defect{round_no}",
                repo=None, repo_token=None, timeout=180)
        except Exception as exc:  # noqa: BLE001 — unverifiable is NOT refuted; keep every task
            log.debug("false-defect check failed for %s: %s", effort_id, exc)
            return derived
        handled_bodies = {idx[int(ln.split(" ", 1)[1])][0]
                          for ln in (out or "").splitlines()
                          if ln.startswith("HANDLED ") and ln.split(" ", 1)[1].strip().isdigit()
                          and int(ln.split(" ", 1)[1]) in idx}
        if not handled_bodies:
            return derived
        kept: list[tuple[str, str]] = []
        for lens, body in derived:
            if body in handled_bodies:
                await self.audit.log("false_defect_rejected", effort_id=effort_id,
                                     payload={"round": round_no, "lens": lens,
                                              "body": body[:200],
                                              "repro": candidates.get(body, "")[:160]})
                continue
            kept.append((lens, body))
        return kept

    async def _salvage_lens_findings(
        self, effort_id: str, lens: str, *, round_no: int,
    ) -> str:
        """P18 F4 — read back the findings a truncated lens banked to disk, then clear the file.

        gym-016 round 2: the `goal_alignment` lens ran ~30 probes over four minutes and returned
        44 characters of narration. Everything it had established was in its context and died with
        the turn. P17 tried to fix that by ASKING for incremental prose emission; that failed,
        because the same budget funds probing and reporting.

        The lens now appends each finding to a file as it goes, so recovery is a file read rather
        than a request for cooperation. Clearing afterwards matters as much as reading: the file
        lives in the container, so a later lens in the same round would otherwise inherit its
        predecessor's findings and report them as its own.

        Returns the salvaged text, or '' when there is nothing (which is the honest answer — a
        lens that banked nothing observed nothing worth keeping)."""
        cmd = (f"cat {_LENS_FINDINGS_PATH} 2>/dev/null; "
               f"rm -f {_LENS_FINDINGS_PATH} 2>/dev/null; echo SALVAGE-DONE")
        try:
            _exit, out, _timed = await self.router.exec_check(
                effort_id, command=cmd, session_id=f"{effort_id}~salvage{round_no}",
                repo=None, repo_token=None, timeout=120)
        except Exception as exc:  # noqa: BLE001 — nothing salvaged is the pre-P18 behaviour
            log.debug("lens findings salvage failed for %s: %s", effort_id, exc)
            return ""
        lines = [ln.strip() for ln in (out or "").splitlines()
                 if ln.strip().startswith("FINDING:")]
        if not lines:
            return ""
        await self.audit.log("lens_findings_salvaged", effort_id=effort_id,
                             payload={"lens": lens, "round": round_no, "findings": len(lines)})
        return (f"Findings recovered from the {lens} lens after its turn ended early. Each line "
                f"is one observation it had established and written down before stopping.\n\n"
                + "\n".join(lines))

    async def _clear_lens_findings(self, effort_id: str, *, round_no: int) -> None:
        """Drop any findings file left over before a lens runs. Without this, a lens that finishes
        cleanly leaves its file behind and the NEXT lens's salvage would pick it up — attributing
        one lens's observations to another, which is worse than losing them."""
        try:
            await self.router.exec_check(
                effort_id, command=f"rm -f {_LENS_FINDINGS_PATH} 2>/dev/null; echo CLEARED",
                session_id=f"{effort_id}~salvage{round_no}",
                repo=None, repo_token=None, timeout=120)
        except Exception as exc:  # noqa: BLE001
            log.debug("lens findings clear failed for %s: %s", effort_id, exc)

    # P18 F19's `_extraction_scopes` (per-scope fan-out of gap analysis) was removed in P19
    # F19-redux: fanning the same whole-branch report across N overlapping scope-goals N-plicated
    # every cross-scope finding and inflated the propagation count the loop terminates on. Gap
    # analysis now runs ONCE against the product goal in `_drain_round`, and per-task `_seam_owner`
    # routing places each result — one finding, one task, filed to its owner.

    async def _gap_analysis(self, effort_id: str, report: str, scope_goal: str) -> list[str]:
        """P10.2 — the ONLY place the goal is allowed to enter.

        Compare an OBJECTIVE observation of what exists against what the goal requires, and state
        the difference as work. Tasks are DISCOVERED here rather than invented: the input is a
        report written by an agent that did not know the goal, so a gap is a real absence of
        evidence, not a model's willingness to agree that something is missing.

        The goal passed in is the PRODUCT goal since P19 F19-redux — one pass over the whole-branch
        report, not one pass per scope. §4 scoped this to keep it tractable for a small model, but
        the P11.4 reframe below (the report is the authority on what is ALREADY BUILT) is what
        actually defends against gym-009's restate-the-goal degeneration, and scoping's cost —
        N-plicating cross-scope findings across the fan-out — broke the termination count it was
        meant to protect. Returns plainly-stated task bodies; empty when the report evidences every
        component of the goal (which is a legitimate, and countable, zero)."""
        report, scope_goal = (report or "").strip(), (scope_goal or "").strip()
        if not (report and scope_goal):
            return []
        # NOTE on the "output exactly: none" line below. The §6.5 ban on a "nothing" affordance
        # governs the OBSERVATION lenses, where it invites a model to skip looking. This is an
        # EXTRACTION step over a report that is already written and fixed: the affordance here does
        # not decide whether anything was observed, it just lets an empty extraction be stated
        # cleanly instead of as prose that `_plain_tasks` would have to guess at. The countable
        # zero has to be expressible somewhere; the point is that it is expressed AFTER the looking,
        # not offered as an alternative to it.
        # P11.4 — the question is "what REMAINS for this scope", asked of a report that has already
        # observed what EXISTS. gym-009's prompt framed it as "what the report does not evidence",
        # which against a thin report degenerates into restating the goal: it produced 12 tasks
        # demanding delete/search/priority/REPL on a codebase that had shipped all of them and had
        # 44 passing tests. The report is now stated as the authority on what is ALREADY BUILT, and
        # the model is told in terms that most of the goal may already be done.
        # P17 F10 — THE SCOPE IS A BOUNDARY, NOT A SPECIFICATION. The previous prompt labelled the
        # scope text "GOAL FOR THIS PART OF THE PROJECT", which invited the model to read a
        # DESCRIPTION as a checklist. gym-015 round 2 ran against the scope "handles loading,
        # saving, atomic file writes, database path configuration, and malformed data resilience"
        # and emitted "Implement database path configuration" — the scope's own wording reflected
        # back as work, for a `db_path()` that already existed and which the worker then correctly
        # reported as "already satisfied, no changes needed". The same round produced
        # `pyproject.toml`, a version string and mypy config: packaging concerns present in
        # neither the scope nor the project goal.
        #
        # A scope says WHERE to look. Only an ABSENCE the report evidences is work.
        sys_p = (
            "You are given a report describing what a codebase CURRENTLY DOES, and a description "
            "of ONE AREA of that project. The report was written by someone who did not know the "
            "area, so it is an unbiased account of what already exists.\n"
            "The area description tells you WHERE to look. It is NOT a specification and NOT a "
            "checklist: it describes what that part of the system deals with, and the things it "
            "names may already be built.\n"
            "MUCH OF THE PROJECT MAY ALREADY BE IMPLEMENTED. The report is your evidence for what "
            "is already there — treat anything it describes as DONE.\n"
            "List only work that the REPORT SHOWS is missing or broken inside this area.\n"
            "Rules:\n"
            "- One task per line. No numbering, no headers, no preamble.\n"
            "- State each task PLAINLY as work to do: what to build, fix or change.\n"
            "- Give NO rationale. Do not explain why. Do not reference the report or the area.\n"
            "- Do NOT list anything the report describes as existing or working.\n"
            "- Do NOT turn a phrase from the area description into a task. That the area MENTIONS "
            "something is not evidence the thing is missing — only the report is evidence.\n"
            "- Do NOT list general software hygiene the report gives no evidence is needed "
            "(packaging metadata, version strings, linter or type-checker configuration, CI, "
            "changelogs) unless this area is explicitly about that.\n"
            "- Do NOT list work outside this area.\n"
            # P19 F17-redux — carry a reproduction THROUGH extraction so the misbehaviour check has
            # something to run. The report, not this step, is the source of truth: copy a command
            # it actually shows, never invent one (an invented probe is meaningless off the one
            # product it was guessed for — the failure the first F17 was built to avoid).
            "- If the report shows a concrete command or input that DEMONSTRATES a defect, copy "
            "that command VERBATIM on the very next line as `REPRO: <command>`. Copy only a command "
            "the report actually shows; never invent one.\n"
            "- If the report shows nothing is missing in this area, output exactly: none"
        )
        user_p = (f"THE AREA TO EXAMINE (a boundary, not a checklist):\n{scope_goal[:2000]}\n\n"
                  f"REPORT — WHAT THE CODEBASE ALREADY DOES:\n{report[:6000]}")
        try:
            out = await self.models.complete("pm", sys_p, user_p)
        except Exception as exc:  # noqa: BLE001 — no gaps derived is honest; a crash is not
            log.debug("gap analysis failed for %s: %s", effort_id, exc)
            return []
        tasks = _plain_tasks(out)
        await self.audit.log("gap_analysis", effort_id=effort_id,
                             payload={"tasks": len(tasks), "goal_chars": len(scope_goal)})
        return tasks

    async def _tasks_from_lens(self, effort_id: str, lens: str, report: str) -> list[str]:
        """Lenses 2 and 3 need no goal comparison — code cleanliness and project history are
        judged against the standards named in the lens prompt itself, so their findings convert
        DIRECTLY into tasks. Same plain-statement rule as P10.2: work, not argument."""
        report = (report or "").strip()
        if not report:
            return []
        # P13.6 — A SEVERITY FLOOR, OR THE COUNT CANNOT REACH ZERO. `clean_code` and
        # `project_documentation` are asked "how could this be better?", and an aesthetic observer
        # asked that always answers: gym-009 round 3 called the history "above average" and still
        # emitted four more suggestions; gym-011 round 1 propagated 12 tasks of which 7 were
        # commit-message preferences ("rewrite subject lines to state intent", "restructure bodies
        # into bullet points"). With no fixed point on two of three lenses the propagation count
        # cannot converge, so termination-on-zero is unmeasurable however correct everything else
        # is. Preferences still reach the human in the persisted LensReport; only DEFECTS queue.
        # P15.2 — RECALIBRATED. The first grading treated "the program silently does the wrong
        # thing" as taste. Measured against the operator's own evaluation of gym-013 (2026-07-19),
        # it dropped: the REPL swallowing error output, `done`/`reopen` succeeding silently on
        # no-ops, empty text accepted, and `Dict[str, Any]` blocking any documentable data
        # contract — the operator's #1 recommendation. Round 1 dropped 11 of 12 findings and at
        # least four came back in the human report as real gaps.
        #
        # The floor still exists for a measured reason: without it two of three lenses have no
        # fixed point and the count cannot converge (gym-009: 21 -> 23 -> ascending; gym-011: 7 of
        # 12 tasks were commit-message preferences). So the fix is a SHARPER LINE plus a third
        # grade — GAP — that is queued and visible but NOT counted, so real work survives without
        # reintroducing a loop that never terminates.
        sys_p = (
            "You convert an evaluation report into a list of tasks, and you grade each one.\n"
            "- One task per line, prefixed with DEFECT:, GAP: or PREFERENCE:.\n"
            "\n"
            "DEFECT — the software is wrong. Includes:\n"
            "  * a crash, a traceback, or a failure on any input or platform\n"
            "  * a command that REPORTS SUCCESS while doing nothing\n"
            "  * an error that is swallowed, discarded or never shown to the user\n"
            "  * invalid input accepted (empty, malformed, out of range)\n"
            "  * an error path or branch with no test\n"
            "  * a documented or claimed behaviour that is not true\n"
            "  * a data contract that cannot be relied on or documented\n"
            "GAP — genuinely required by the goal but not yet done, and not a malfunction.\n"
            "PREFERENCE — it is correct and complete; someone might arrange it differently:\n"
            "  naming, formatting, file layout, wording, or a feature the goal never asked for.\n"
            "\n"
            "- Grade by what the software DOES, not by whether the project is small. Do not soften "
            "a DEFECT because the codebase is a script or the scope is modest.\n"
            "- State each task PLAINLY as work to do. Give NO rationale.\n"
            # P19 F17-redux — as in gap analysis: carry a reproduction the report shows, verbatim,
            # so a false DEFECT can be refuted by running it. Copy only; never invent.
            "- If the report shows a concrete command or input that DEMONSTRATES a DEFECT, copy "
            "that command VERBATIM on the next line as `REPRO: <command>`. Never invent one.\n"
            "- If the report identifies nothing at all, output exactly: none"
        )
        try:
            out = await self.models.complete("pm", sys_p, f"REPORT:\n{report[:6000]}")
        except Exception as exc:  # noqa: BLE001
            log.debug("lens task extraction failed for %s/%s: %s", effort_id, lens, exc)
            return []
        defects, gaps, prefs = [], [], 0
        for ln in (out or "").splitlines():
            s = ln.strip().lstrip("-*• ").strip()
            if re.match(r"^DEFECT\s*:", s, re.I):
                defects.append(re.sub(r"^DEFECT\s*:\s*", "", s, flags=re.I))
            elif re.match(r"^GAP\s*:", s, re.I):
                gaps.append(re.sub(r"^GAP\s*:\s*", "", s, flags=re.I))
            elif re.match(r"^PREFERENCE\s*:", s, re.I):
                prefs += 1
            elif s.upper().startswith("REPRO:") and defects:
                # P19 F17-redux — a repro belongs to the DEFECT above it. Keep it on the body so
                # `_plain_tasks` folds it and `_drop_false_defects` can run it. (A repro under a
                # GAP/PREFERENCE has no false-defect check to feed, so it is dropped with the line.)
                defects[-1] = f"{defects[-1]}\n{s}"
        if prefs or gaps:
            await self.audit.log("lens_preferences_dropped", effort_id=effort_id,
                                 payload={"lens": lens, "dropped": prefs, "kept": len(defects),
                                          "gaps": len(gaps)})
        # GAPs are queued but NOT counted — `_drain_round` stamps them with round 0 so they never
        # increment `new_tasks`. Real work stays visible; termination stays reachable.
        # ACCUMULATE across the round's lenses — assigning here would let a later lens with no
        # GAPs wipe an earlier lens's, which is exactly what a three-lens sweep does.
        self._pending_gaps = getattr(self, "_pending_gaps", {})
        if gaps:
            self._pending_gaps.setdefault(effort_id, []).extend(_plain_tasks("\n".join(gaps)))
        # An ungraded reply (older model, format drift) must not silently propagate everything as
        # defects — fall back to the plain parse ONLY when nothing was graded at all.
        if defects or gaps or prefs:
            return _plain_tasks("\n".join(defects))
        return _plain_tasks(out)

    async def _drain_round(
        self, effort_id: str, channel_id: str, root: str, repo: str, delivery: BranchDelivery,
    ) -> dict:
        """ONE round of the drain loop: sweep → gap analysis → queue → count.

        Returns `{round, new_tasks, open_tasks, note, scope_node_id, capped}`. `new_tasks` is the
        TERMINATION QUANTITY (P10.4): zero means an independent sweep of the whole scope found
        nothing new to do, which is the org's only honest definition of complete. `open_tasks` is
        what the next dispatch works — including tasks carried over from earlier rounds, because
        "complete" requires the queue drained AND the sweep silent, not just the sweep silent."""
        if not (repo and delivery.branch):
            return {"round": 0, "new_tasks": 0, "open_tasks": [], "note": "",
                    "scope_node_id": None, "capped": False}
        round_no = await self._drain_round_no(effort_id)
        proj = await self._effort_project(effort_id) or ""
        node_id = await self._ensure_scope_node(effort_id) if self.s.drain_tier_walk else None
        # RUNAWAY GUARD ONLY (never the termination condition — see `drain_round_cap`).
        if round_no > max(1, self.s.drain_round_cap):
            await self.audit.log("drain_round_capped", effort_id=effort_id,
                                 payload={"round": round_no, "cap": self.s.drain_round_cap})
            return {"round": round_no, "new_tasks": 0, "capped": True, "scope_node_id": node_id,
                    "open_tasks": await self.list_open_tasks(effort_id=effort_id),
                    "note": (f"\n\n⚠️ **Drain loop hit its runaway guard** after "
                             f"{self.s.drain_round_cap} rounds — still propagating new work. This "
                             f"is a safety net, not a completion: the scope is **not** finished.")}
        # Clear last round's GAP carry-over before this round's lenses accumulate into it.
        getattr(self, "_pending_gaps", {}).pop(effort_id, None)
        reports = await self._lens_sweep(effort_id, channel_id, root, repo, delivery,
                                         round_no=round_no, scope_node_id=node_id)
        # ── P11.3 THE SEQUENCE: decompose → SELECT the working scope → analyse against ITS goal ──
        # gym-009 ran this backwards and the tier walk was cosmetic as a result: `_maybe_decompose`
        # fired AFTER `_gap_analysis`, so the children could never inform the analysis that created
        # them, and every single round asked "does this report evidence the ENTIRE 5417-char
        # project goal?" A storage-layer round and a UX-layer round were handed the same question.
        # Decomposition now happens FIRST, from the raw observations rather than from derived
        # tasks, so the scope that gets analysed is the scope the work actually belongs to.
        if node_id and reports:
            await self._maybe_decompose(
                node_id, [b for b in reports.values() if b], effort_id=effort_id,
                from_reports=True)
            node_id = await self._select_working_scope(effort_id, node_id) or node_id
        # The SELECTED working scope — used for DISPATCH (which tasks run this round) and as the
        # default routing target below. A scope node's own `scope` text wins; the effort goal is
        # the fallback when the tier walk is off.
        scope_goal = ""
        if node_id:
            n = await self._scope_node(node_id)
            scope_goal = (n or {}).get("scope") or ""
        if not scope_goal:
            try:
                _, scope_goal, _ = await self.charters.current_goal(effort_id)
            except Exception:  # noqa: BLE001
                scope_goal = ""
        # P19 F19-redux — the goal gap analysis mines against is the PRODUCT (root) goal, not the
        # selected scope's. The whole effort goal covers every scope's findings in one pass; a
        # single scope's goal covers only its own, which is what P18 F19 fanned out to repair.
        try:
            _, product_goal, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            product_goal = ""
        product_goal = (product_goal or scope_goal or "").strip()
        await self.audit.log("drain_scope_selected", effort_id=effort_id,
                             payload={"round": round_no, "scope": node_id,
                                      "scope_goal_chars": len(scope_goal)})
        derived: list[tuple[str, str]] = []
        if reports.get(_LENS_GOAL_ALIGNMENT_KEY):
            # P19 F19-redux — EXTRACT ONCE AGAINST THE PRODUCT GOAL, THEN ROUTE. NO FAN-OUT.
            #
            # P18 F19 fanned gap analysis across every open scope so a sibling's finding could not
            # evaporate (gym-016: a precisely-diagnosed broken REPL flag parser belonged to `cli
            # and repl interface`, gap analysis ran against `json data storage`, and it vanished).
            # But mining the SAME whole-branch report against N overlapping scope-goals N-plicates
            # every finding that touches more than one scope. gym-017 round 3 turned ~8 distinct
            # findings into 24 tasks; the undo crash alone became five. The implementer de-duped
            # the WORK ("deduplicated the 20 items into 9 unique concerns") — but `new_tasks` is
            # the loop's termination signal (P10.4: stop on zero NEW), and a count inflated by
            # paraphrase can never descend to a trustworthy zero. The loop re-ascended instead of
            # converging, and the over-produced schema-tightening tasks drove a 44→5 delete-to-pass
            # (commit 03390ff) that shipped only because a non-ff push forced a reconciling merge.
            #
            # The report already describes the whole branch, so mine it ONCE against the product
            # goal — which covers every scope — and let the SAME per-task routing that already
            # files work to its owner (`_seam_owner` / `_best_scope_for`, below) place each result.
            # One finding in, one task out, routed to its owner: no fan-out, no paraphrase
            # duplication, and a count that means what P10.4 needs it to mean. DISPATCH still works
            # one scope at a time (`_dispatchable_tasks` below uses the selected `node_id`).
            derived += [(_LENS_GOAL_ALIGNMENT_KEY, b) for b in await self._gap_analysis(
                effort_id, reports[_LENS_GOAL_ALIGNMENT_KEY], product_goal)]
        for lens in ("clean_code", "project_documentation"):
            if reports.get(lens):
                derived += [(lens, b) for b in
                            await self._tasks_from_lens(effort_id, lens, reports[lens])]
        # P17 F11 — DROP TASKS BUILT ON AN ABSENCE THE TREE REFUTES. One batch check per round.
        derived = await self._drop_false_absences(effort_id, derived, round_no=round_no)
        # P18 F17 — and drop tasks built on a MISBEHAVIOUR the code refutes. F11's filter only
        # looks at asserted absences; this is its mirror.
        derived = await self._drop_false_defects(effort_id, derived, round_no=round_no)
        # The sibling scopes a task may belong to (P14.2). Read once per round, not per task.
        scope_children = await self._scope_children(node_id) if node_id else []
        new_bodies: list[str] = []
        for lens, body in derived:
            # P10.6 SEAM ROUTING: on a parent tier, a defect that belongs to a CHILD scope is
            # written into that child and flips it back to open — the integration check. A parent
            # never fixes its children's insides; that would dissolve the encapsulation the tree
            # exists to provide.
            # P14.2 — FILE WORK WHERE IT BELONGS, not where it was found. `_seam_owner` is lexical
            # and conservative, so anything it can't match lands in whichever scope happened to be
            # selected. gym-012 filed "add stdout assertions to filter tests" into the DATA STORAGE
            # scope with a four-child tree available; the worker then had to escalate it, and the
            # org froze. Correct filing removes most escalations before they happen.
            # P19 NOTE: F19-redux mines the whole report against the product goal in ONE pass (fixing
            # the propagation count the loop terminates on), but routing stays anchored to the
            # SELECTED scope. Distributing a decomposing round's derivations across sibling children
            # would strand them: the tier walk selects downward only, so a sibling's tasks would
            # never be picked up (the "worst failure mode" test). Sideways selection is deferred.
            owner = await self._seam_owner(node_id, body) if node_id else None
            if not owner and node_id and scope_children:
                owner = await self._best_scope_for(body, scope_children)
            target = owner or node_id
            res = await self.add_task(body, project_slug=proj, scope_node_id=target,
                                      effort_id=effort_id, source_lens=lens, round_no=round_no)
            if res and res[1]:
                new_bodies.append(body)
            # Reopen on ANY seam finding, new or re-derived. A defect the parent's sweep still sees
            # is still real — gating the reopen on novelty would leave a since-completed child
            # marked done while a known defect it owns sits unfixed.
            if owner and res:
                await self._reopen_scope(owner, reason=body, effort_id=effort_id)
        # P15.2 — GAPs: queued, visible, and NOT counted. Stamped round 0 so `count_new_tasks`
        # (which filters on the current round) can never see them, and so a lens that keeps
        # surfacing required-but-not-malfunctioning work cannot prevent termination. Filed AFTER
        # the counted tasks so the round's arithmetic is already fixed.
        for gap_body in (getattr(self, "_pending_gaps", {}) or {}).pop(effort_id, []):
            owner = await self._seam_owner(node_id, gap_body) if node_id else None
            if not owner and node_id and scope_children:
                owner = await self._best_scope_for(gap_body, scope_children)
            await self.add_task(gap_body, project_slug=proj, scope_node_id=owner or node_id,
                                effort_id=effort_id, source_lens="gap", round_no=0)
        # Counted from the DB, not from the loop above: the count must be a property of what was
        # PERSISTED (idempotently), so a restart mid-round can't double-count and a re-derived gap
        # can't inflate it. Seam-routed tasks still belong to this effort's round.
        new_tasks = await self.count_new_tasks(round_no, effort_id=effort_id)
        open_tasks = await self._dispatchable_tasks(effort_id, node_id)
        # ZERO MUST BE EVIDENCED. A sweep that did not run is not a sweep that found nothing: if no
        # lens produced a report (all three wakes failed, or the effort is frozen and every wake
        # returned None), `new_tasks` is zero for a reason that has nothing to do with the state of
        # the product. Treating that as completion would reinstate the exact false-green this loop
        # exists to eliminate — an absence of output read as an absence of work.
        #
        # P17 F3 — `bool(reports)` was too weak: it is true when ANY lens reported. gym-015 rounds 1
        # and 5 (2 of 5 rounds) recorded `swept: true` on a 2-of-3 sweep whose MISSING lens was
        # `goal_alignment` — the sole input to `_gap_analysis` (see the `reports.get(...)` guard
        # above). Those rounds never compared the deliverable to the goal at all, and emitted no
        # `gap_analysis` event; with an empty queue they would have declared the scope complete.
        # A sweep missing the only goal-aware lens is a sweep that did not happen, exactly as the
        # `NoCapacityError` path one screen up already asserts.
        swept = bool(reports) and bool(reports.get(_LENS_GOAL_ALIGNMENT_KEY))
        await self.audit.log("drain_round", effort_id=effort_id,
                             payload={"round": round_no, "new_tasks": new_tasks,
                                      "open": len(open_tasks), "lenses": sorted(reports),
                                      "swept": swept, "scope": node_id})
        lines = [f"## 🔁 Drain round {round_no}\n_Three objective lenses swept the product; gaps "
                 f"were derived against this scope's goal._"]
        if not swept:
            missing = ("no lens produced a report" if not reports else
                       f"the **{_LENS_GOAL_ALIGNMENT_KEY}** lens produced no report "
                       f"(only {', '.join(sorted(reports))} reported)")
            lines = [f"## ⚠️ Drain round {round_no} — **the sweep did not complete**\n_"
                     f"{missing.capitalize()}, so the product was never compared against the goal "
                     f"this round. This is **not** a clean sweep and says nothing about whether "
                     f"the work is finished._"]
        elif new_tasks:
            lines.append(f"**{new_tasks} new task(s) propagated this round:**\n"
                         + "\n".join(f"- {b}" for b in new_bodies[:10]))
        elif open_tasks:
            lines.append(f"**Zero NEW tasks propagated**, but {len(open_tasks)} task(s) are still "
                         f"open — the sweep re-derived work the last round did not land. Not "
                         f"complete: completion needs the queue drained AND the sweep silent.")
        else:
            lines.append("**Zero new tasks propagated** and the queue is empty — a full, "
                         "independent lens sweep found nothing further to do. This scope is "
                         "complete.")
        if open_tasks and (new_tasks or not swept):
            lines.append(f"_{len(open_tasks)} task(s) open in this scope's queue._")
        return {"round": round_no, "new_tasks": new_tasks, "open_tasks": open_tasks,
                "note": "\n\n".join(lines), "scope_node_id": node_id, "capped": False,
                "swept": swept}

    async def _qa_evaluation(
        self, effort_id: str, channel_id: str, root: str, repo: str, delivery: BranchDelivery,
    ) -> tuple[str, list[str]]:
        """POST-DELIVERY QA (operator 2026-07-15, reviewing gym PR#2: green tests, but frustrating
        to USE — no help systems, and a separate QA pass found a page of gaps "that could've been
        caught with a simple QA"). A DIFFERENTLY-GOALED agent that did NOT build the thing exercises
        the product as a skeptical end user — runs it, tries each function + malformed/edge inputs,
        checks for usage help and clear errors — and reports DEFECTS (in scope → fixable now) vs
        FOLLOWUPS (out of scope → the operator's call). It optimises to FIND fault, not bless
        (governance §4.4). CHANGE-NOTHING; best-effort — a QA hiccup never blocks the delivery.
        Returns (qa_note, defects): `qa_note` is a PR/closure markdown block ('' if QA couldn't
        run); `defects` are the in-scope fixables."""
        if self.s.qa_gate == "off" or not (repo and delivery.branch):
            return "", []
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal_text = ""
        goal_head = " ".join((goal_text or "").split())[:400]
        self._verify_seq += 1
        instr = (
            f"QA EVALUATION — you did NOT build this, and you will CHANGE NOTHING (no edits, no "
            f"git writes; this is a review turn). First check out the DELIVERED branch:\n"
            f"  git fetch origin {delivery.branch} && git checkout -f {delivery.branch}\n"
            f"This delivery's goal was: {goal_head}\n\n"
            f"EVALUATE THE WHOLE RUNNING PRODUCT AS A FINAL PRODUCT — not just this delivery's "
            f"diff, and not the happy path. Be ADVERSARIAL: ASSUME there ARE gaps and hunt for "
            f"them; passing its own tests is NOT enough. Do all of the following:\n"
            f"1. Take EACH function/command one at a time — run VALID inputs, THEN empty, "
            f"malformed, missing-field, duplicate, negative, non-ASCII and very-large inputs. Any "
            f"raw stack trace on ANY input is a crash bug — record every one.\n"
            f"2. DISCOVERABILITY — run the tool with no args and with `--help`, and EACH subcommand "
            f"with `--help`. Can a brand-new user learn the EXACT syntax of every command from the "
            f"tool itself? Missing/thin per-command help or usage is a DEFECT (the single most "
            f"repeated complaint — treat it as REQUIRED).\n"
            f"3. FAILURE PATHS & DATA SAFETY — corrupt/partial data, a stored record missing a "
            f"field, an un-writable/full disk, two runs at once. Clear message + non-zero exit, or "
            f"crash / silent corruption / lost data? Check for atomic writes + defensive field "
            f"access (item.get, not item['x']).\n"
            f"4. END-TO-END — every accepted input must actually SHOW in output (stored-but-never-"
            f"shown does not count).\n"
            f"Grade to a FINAL-PRODUCT bar. Then reply in EXACTLY this format:\n"
            f"WORKS: one line, what genuinely works.\n"
            f"DEFECTS: numbered list of QUALITY problems in the product to fix before it ships — "
            f"unhandled tracebacks/crashes on ANY input, missing or thin per-command help/usage, "
            f"unclear error messages, non-atomic writes / no error handling on save, missing "
            f"defensive or schema validation, a feature that does not work end-to-end. These are "
            f"QUALITY, not new features — list EVERY one you found. Say `none` ONLY if adversarial "
            f"testing genuinely surfaced zero.\n"
            f"FOLLOWUPS: numbered list of NEW FEATURES / scope additions the goal did not ask for — "
            f"suggestions for the operator, NOT to fix now. Say `none` if none.\n"
            f"VERDICT: a one-line final-product grade naming the single biggest weakness."
        )
        try:
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=f"{effort_id}~qa{self._verify_seq}", instruction=instr,
                repo=repo, repo_token=await self._project_token(effort_id),
            )
        except Exception as exc:  # noqa: BLE001 — QA is a bonus; never block the delivery
            log.debug("QA evaluation wake failed for %s: %s", effort_id, exc)
            return "", []
        out = (result.output or "") if result else ""
        if not out.strip():
            return "", []
        defects = _qa_items(_qa_block(out, "DEFECTS"))
        followups = _qa_items(_qa_block(out, "FOLLOWUPS"))
        verdict = " ".join(_qa_block(out, "VERDICT").split())[:300]
        await self.audit.log("qa_evaluation", effort_id=effort_id,
                             payload={"defects": len(defects), "followups": len(followups),
                                      "verdict": verdict[:120]})
        lines = ["## 🔎 QA evaluation\n_A differently-goaled agent exercised the running product "
                 "(not just the tests)._"]
        if verdict:
            lines.append(f"**Verdict:** {verdict}")
        if defects:
            lines.append("**Defects (in scope — worth fixing before merge):**\n"
                         + "\n".join(f"- {d}" for d in defects))
        if followups:
            lines.append("**Follow-ups (out of scope — your call):**\n"
                         + "\n".join(f"- {d}" for d in followups))
        if not defects and not followups:
            lines.append("_No defects or follow-ups surfaced — the product exercised cleanly._")
        # ── Lens 2 (governance §4.4): a distinct reviewer reads the SOURCE for craftsmanship &
        # documentation — the class of gaps the run-the-product lens above cannot see. Its defects
        # merge into the same iterate loop.
        if self.s.qa_code_review:
            cr = await self._qa_code_review_lens(
                effort_id, channel_id, root, repo, delivery, goal_head)
            if cr is not None:
                _cv, c_defects, _cf, c_block = cr
                defects = defects + c_defects
                lines.append(c_block)
        return "\n\n".join(lines), defects

    async def _qa_code_review_lens(
        self, effort_id: str, channel_id: str, root: str, repo: str,
        delivery: BranchDelivery, goal_head: str,
    ):
        """Second QA lens (operator 2026-07-15, evaluating the delivered tool a 4th way: "evaluate
        the code cleanliness — is it SOLID, industry-standard patterns, clear naming, does the code
        support documentation?"). The functional QA is BLACK-BOX (run it, feed it garbage) and by
        design cannot see missing docstrings, absent type hints, a data-layer `sys.exit` that should
        `raise`, or `sys.path` packaging hacks. This DIFFERENTLY-GOALED reviewer READS THE SOURCE
        for craftsmanship & documentation and optimises to REFUTE 'this is clean, maintainable
        code' (governance §4.4). Gated by AO_QA_CODE_REVIEW; CHANGE-NOTHING; best-effort — a lens
        hiccup never blocks the delivery. Returns (verdict, defects, followups, markdown_block) or
        None if it couldn't run."""
        self._verify_seq += 1
        instr = (
            f"CODE REVIEW — craftsmanship, maintainability & documentation. You did NOT build this "
            f"and you will CHANGE NOTHING (no edits, no git writes; this is a review turn). First "
            f"check out the DELIVERED branch:\n"
            f"  git fetch origin {delivery.branch} && git checkout -f {delivery.branch}\n"
            f"This delivery's goal was: {goal_head}\n\n"
            f"READ THE SOURCE as a senior engineer doing a merge-review. Your job is to REFUTE the "
            f"claim that this is clean, well-documented, maintainable code. Judge every point AT "
            f"THE SCALE OF THIS PROJECT (no enterprise ceremony on a small script) but hold the "
            f"fundamentals:\n"
            f"1. DOCUMENTATION-OF-CODE — a docstring on every public function/class saying what it "
            f"does; type hints on signatures; a module docstring and a README that match real "
            f"behaviour. Project-level docs do NOT excuse undocumented functions.\n"
            f"2. SOLID & SEPARATION — one responsibility per function; I/O separated from logic; "
            f"design smells like a data-layer/helper calling sys.exit() or print() instead of "
            f"raising/returning so callers can handle it; behaviour testable, not hard-wired to "
            f"globals.\n"
            f"3. NAMING & CONVENTIONS — clear, consistent, idiomatic names (PEP 8 for Python); no "
            f"cryptic abbreviations; tests named for what they assert.\n"
            f"4. PACKAGING & HYGIENE — proper layout (no sys.path.insert hacks / missing "
            f"__init__.py), no dead code, no copy-paste duplication, tidy imports.\n"
            f"5. ERROR-HANDLING SHAPE — typed exceptions with clear messages, not swallowed or "
            f"turned into process-killing exits deep in the call tree.\n"
            f"Then reply in EXACTLY this format:\n"
            f"WORKS: one line — what the code does well.\n"
            f"DEFECTS: numbered list of CODE-QUALITY problems to fix before merge — missing "
            f"docstrings/type hints, SOLID/separation violations, design smells (e.g. sys.exit from "
            f"a data layer), naming problems, packaging hacks, unhandled failure shapes. Scope to "
            f"THIS project's size — real gaps only, not gold-plating. Say `none` only if the source "
            f"genuinely meets a professional bar.\n"
            f"FOLLOWUPS: numbered list of larger refactors out of scope for now — the operator's "
            f"call. Say `none` if none.\n"
            f"VERDICT: a one-line maintainability grade naming the biggest code-quality weakness."
        )
        try:
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=f"{effort_id}~qa{self._verify_seq}", instruction=instr,
                repo=repo, repo_token=await self._project_token(effort_id),
            )
        except Exception as exc:  # noqa: BLE001 — a second-lens hiccup never blocks delivery
            log.debug("QA code-review lens wake failed for %s: %s", effort_id, exc)
            return None
        out = (result.output or "") if result else ""
        if not out.strip():
            return None
        defects = _qa_items(_qa_block(out, "DEFECTS"))
        followups = _qa_items(_qa_block(out, "FOLLOWUPS"))
        verdict = " ".join(_qa_block(out, "VERDICT").split())[:300]
        await self.audit.log("qa_evaluation", effort_id=effort_id,
                             payload={"lens": "code_review", "defects": len(defects),
                                      "followups": len(followups), "verdict": verdict[:120]})
        block = ["### 🧹 Code review — craftsmanship & documentation\n_A second, differently-goaled "
                 "reviewer audited the SOURCE (SOLID, naming, docstrings, type hints, packaging)._"]
        if verdict:
            block.append(f"**Verdict:** {verdict}")
        if defects:
            block.append("**Code-quality defects (worth fixing before merge):**\n"
                         + "\n".join(f"- {d}" for d in defects))
        if followups:
            block.append("**Refactor follow-ups (out of scope — your call):**\n"
                         + "\n".join(f"- {d}" for d in followups))
        if not defects and not followups:
            block.append("_Code reads clean — no craftsmanship gaps surfaced._")
        return verdict, defects, followups, "\n\n".join(block)

    async def _removal_disclosure(self, effort_id: str, branch: str,
                                  goal_text: str) -> tuple[str, bool]:
        """Surface what a delivery REMOVED (no silent removals). Returns (note, flag): `note` is
        appended to the closure; `flag`=True marks the effort needs-attention when the removals
        are NOT justified by a removal/cleanup goal — a fix/port/launch that deletes files or guts
        methods must be reviewed, never silently 'done'. Generic; fail-open (no note on an
        unreadable diff)."""
        repo = await self._effort_repo(effort_id)
        if not (repo and self.github is not None and self.s.github_app_enabled):
            return "", False
        try:
            rm = await read_removal_summary(
                self.github, repo, branch, api_base=self.s.github_api_base,
                transport=self._gh_transport)
        except Exception as exc:  # noqa: BLE001 — disclosure never blocks a finish
            log.debug("removal summary failed for %s: %s", effort_id, exc)
            return "", False
        deleted = rm.get("deleted_files") or []
        gutted = rm.get("gutted_files") or []
        syms = rm.get("removed_symbols") or []
        ins, dels = rm.get("insertions", 0), rm.get("deletions", 0)
        # "substantial removal" = a deleted file, a gutted method-body, or a net-negative diff
        # that removes a lot more than it adds (the delete-to-pass shape).
        net_heavy = dels >= 60 and dels >= ins * 3
        if not (deleted or gutted or net_heavy):
            return "", False
        goal_is_removal = bool(self._REMOVAL_GOAL_RE.search(goal_text or ""))
        parts: list[str] = []
        if deleted:
            parts.append("deleted file(s): " + ", ".join(f"`{f}`" for f in deleted[:8])
                         + (f" +{len(deleted) - 8} more" if len(deleted) > 8 else ""))
        if gutted:
            parts.append("gutted (body largely removed): "
                         + ", ".join(f"`{g['file']}` (−{g['removed']}/+{g['added']})"
                                     for g in gutted[:6]))
        if syms:
            parts.append("removed symbol(s): " + ", ".join(f"`{s}`" for s in syms[:10])
                         + (f" +{len(syms) - 10} more" if len(syms) > 10 else ""))
        if not parts and net_heavy:
            parts.append(f"net-negative diff (−{dels}/+{ins} lines)")
        listing = "; ".join(parts)
        await self.audit.log("delivery_removals", effort_id=effort_id,
                             payload={"deleted": deleted[:20], "gutted": [g["file"] for g in gutted],
                                      "symbols": syms[:20], "ins": ins, "dels": dels,
                                      "goal_removal": goal_is_removal})
        if goal_is_removal:
            # the operator ASKED to remove — disclose, but it's expected (not a flag)
            return (f"\n\n🗑️ **Removals (as intended by the goal):** {listing}.", False)
        # a fix/port/launch that removed things → REVIEW, don't silently pass. A green build does
        # not prove the removed functionality wasn't needed (live: a deleted cursor feature).
        return (
            f"\n\n⚠️ **Removal review — the goal wasn't to remove code, but this delivery {listing}.** "
            f"A passing build does NOT prove these were safe to drop. Confirm each removal was a "
            f"genuine port/dead-code (not a feature deleted to clear an error). If any was needed, "
            f"say so and I'll port it properly instead.", True)

    async def _closure_invariant_gaps(self, effort_id: str) -> list[str]:
        """P8 #1 — the delivery gates a LANDED delivery must have hit before "done", read from the
        effort's OWN audit (2026-07-16 gym: two complete, green products closed "done — read-only,
        nothing to publish" while their audits read `delivery_pr_opened: 0`, `qa_evaluation: 0`,
        `develop_integration: 0` — the report and the audit disagreed and nothing noticed). Each
        gate is asserted ONLY when its own preconditions say it should have run, so a stack without
        the GitHub App / with qa off is never held to gates it can't reach. Returns human-readable
        descriptions of the missing gates (empty = the audit backs the claim)."""
        gaps: list[str] = []
        repo = await self._effort_repo(effort_id)
        gh_live = self.github is not None and self.s.github_app_enabled
        if repo and self.s.auto_pr and gh_live:
            if await self._event_count(effort_id, "delivery_pr_opened") == 0:
                gaps.append("**no delivery PR** (`delivery_pr_opened: 0`) — the branch never "
                            "became visible for review")
        if repo and self.s.qa_gate != "off":
            # P10: the drain loop REPLACES the graded QA pass, so it satisfies this gate on its own
            # evidence — a `drain_round` event means three lenses swept the product and the round's
            # propagation was counted, which is strictly more than the old pass proved.
            if (await self._event_count(effort_id, "qa_evaluation") == 0
                    and await self._event_count(effort_id, "drain_round") == 0):
                gaps.append(f"**no QA evaluation** (`qa_evaluation: 0`, `drain_round: 0` with "
                            f"qa_gate={self.s.qa_gate}) — nobody exercised the product")
        # Integration is asserted only when the delivery was ACCEPTED (its merge gate opened) —
        # and "attempted" is the invariant, not success: an honestly-surfaced conflict counts
        # (every attempt path in _integrate_to_develop leaves a develop_integration event).
        if (repo and self.s.develop_integration and gh_live
                and f"merge-{effort_id}" in self._pending_merge):
            if await self._event_count(effort_id, "develop_integration") == 0:
                gaps.append(f"**no develop integration** (`develop_integration: 0`) — the accepted "
                            f"delivery was never folded into `{self.s.develop_branch}` (not even "
                            f"attempted)")
        return gaps

    async def _finish_effort(self, effort_id: str, result, *, delivery: BranchDelivery | None = None) -> None:
        """All steps cleared → closure DOWN into the effort thread + a summary UP to #mgmt (§2). When a
        repo was focused, `delivery` is the PM's VERIFIED verdict on the branch (§4.2): a verified
        `landed` states the branch + commit factually; an `unverifiable` one is labelled as the
        worker's self-report we couldn't independently check — never a bare, over-confident 'pushed'."""
        head = ((result.output or "").strip().splitlines()[0][:200]
                if result and result.output else "done")
        # The worker's self-report (its turn ended ok); the VERIFIED verdict overrides it as the truth.
        self_reported = self._published_branch.pop(effort_id, None)
        # A no_changes delivery means "the worker changed nothing THIS turn" — but a PRIOR turn may
        # already have published a real branch. Before a read-only close (which skips the whole
        # PR/QA/develop pipeline), RE-VERIFY the remote: if the branch is actually AHEAD of main there
        # IS a deliverable, so route it through the real delivery path instead of closing it hollow
        # (2026-07-16 gym: a complete 62-test product closed "done — read-only, nothing to publish"
        # with no PR, no QA, no develop, because a final no-changes turn masked the landed branch).
        if delivery is not None and delivery.no_changes:
            _rv_repo = await self._effort_repo(effort_id)
            if _rv_repo:
                _rv = await self._verify_delivery(effort_id, _rv_repo)
                # only a branch with REAL changes overrides the no-op close — an empty-diff branch
                # (a commit that touches nothing) is a legitimate NO CHANGES read-only completion.
                if _rv.landed and _rv.files_changed != 0:
                    log.info("no_changes re-verify: %s branch has real changes ahead of main — "
                             "delivering, not read-only closing", effort_id)
                    delivery = _rv
        branch = delivery.branch if (delivery and delivery.landed) else None
        if delivery is not None and delivery.no_changes:
            # BACKSTOP (single closure chokepoint — every no_changes delivery passes through here):
            # a BEHAVIORAL-symptom goal must NEVER reach a clean no-changes 'done' — doing nothing
            # can't fix a live runtime symptom. Each upstream no-changes path is already gated by
            # `_no_changes_acceptable`, but this is the last line of defense: if any path ever leaks
            # a no_changes delivery here for a behavioral goal the ORG has not independently proven
            # fixed (the org-run RED→GREEN harness), REFUSE the false done and surface honest
            # needs-attention. The worker's `REPRO:`/`AFTER: PASS` prose is NOT proof — no worker
            # sentence may cause a state change (P8 #4; 2026-07-16 gym: those markers closed
            # gym-004b with `effort_reproduction_verified: 0`). Makes the whole false-done class
            # impossible regardless of upstream path (live 2026-07-11: a false-done recurred via a
            # path that resisted tracing — a chokepoint guard is the durable fix). Project-agnostic;
            # keys off the GOAL wording + org-observed state only.
            try:
                _, _bgoal, _ = await self.charters.current_goal(effort_id)
            except Exception:  # noqa: BLE001
                _bgoal = ""
            _brg = self._repro_red_green.get(effort_id)
            _brepro = bool(_brg) and _brg == self._org_verified.get(effort_id)
            if self._runtime_symptom_phrase(_bgoal or "") and not _brepro:
                log.warning("no_changes-on-behavioral backstop tripped for %s — refusing false done",
                            effort_id)
                symptom = self._runtime_symptom_phrase(_bgoal or "") or "the reported symptom"
                await self.audit.log(
                    "delivery_runtime_unverified", effort_id=effort_id,
                    payload={"reason": "no_changes on behavioral goal without repro proof",
                             "symptom": symptom[:200]})
                msg = (f"⚠️ **{effort_id}** — the worker reported nothing to change, but the goal is a "
                       f"**runtime/interaction** symptom (“{symptom}”) I can't confirm by doing "
                       f"nothing. I did **not** mark it done. It needs a reproduction that exercises "
                       f"the symptom (or your runtime check). The branch is safe; tell me how you'd "
                       f"like to verify.")
                await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
                await self.comms.post(Intent.operator_reply, msg,
                                      thread_id=self._mgmt_thread_of(effort_id))
                await self.router.update_effort_card(effort_id, "needs-attention")
                return
            # Read-only/investigation completion: the worker's ANSWER (streamed above in the thread)
            # is the deliverable. No branch, no PR, no D2 — and no scope flag (nothing was meant to
            # change). Honest and DONE.
            self._effort_intent_scope.pop(effort_id, None)
            where = ("**no changes** — the worker confirmed this was a read-only task; its answer "
                     "above in the thread is the deliverable (nothing to publish)")
        elif delivery is not None and delivery.landed:
            sha = f" @ `{delivery.head_sha[:10]}`" if delivery.head_sha else ""
            # STALE RE-VERIFY (operator 2026-07-12: "the 18:24 activity looked like new work but the
            # branch hadn't moved in 6h — I couldn't tell what was real"). When the head is EXACTLY
            # where it was before this run dispatched, NOTHING new landed this round — the delivery
            # from an earlier round still stands. Frame it as a re-confirmation, NOT a fresh push/PR,
            # so a no-op re-verify never masquerades as new activity. Generic for any project.
            stale_reverify = self._is_stale_head(effort_id, delivery)
            where = (
                (f"re-verified branch **`{branch}`**{sha} — **no new commits this round**; the "
                 f"delivery from an earlier round still stands (`git fetch origin {branch}` to see it)")
                if stale_reverify else
                (f"pushed to branch **`{branch}`**{sha} (verified on the remote) — "
                 f"`git fetch origin {branch}` to see it"))
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
            # ANTI-DELETE-TO-PASS — a green reached by DELETING features is not a fix. This runs
            # BEFORE any PR: removals used to be merely DISCLOSED, and disclosure ran *after* the PR
            # was already open (live 2026-07-14: the port went green by deleting the CURSOR and the
            # org opened PRs off it). Blocked ⇒ no PR; the PM re-drives it to PORT what it deleted.
            if not await self._gate_removals(effort_id, eff_repo, delivery):
                return
            # POST-DELIVERY QA (operator 2026-07-15): exercise the PRODUCT before shipping — green
            # tests are not a usable product. A differently-goaled agent runs it and surfaces the
            # gaps a test suite misses (usability, missing help, unhandled inputs). In `iterate`
            # mode, in-scope defects auto-iterate ONCE first; in `report` mode the findings ride on
            # the PR + closure for the human to dispose. Best-effort — never blocks the delivery.
            # (_finish_effort has no channel_id/root in scope — resolve the effort's own thread.)
            qa_note, qa_defects = "", []
            # P10 — THE DRAIN LOOP. When on, post-delivery evaluation is not a graded QA pass but a
            # counted round: three objective lenses sweep, gaps are derived against THIS SCOPE's
            # goal, and the round's NEW-TASK COUNT decides what happens next. > 0 dispatches another
            # round; == 0 completes the scope and walks the tree up. The org stops because it has
            # computed that there is nothing left, not because a counter ran out or a model shrugged.
            if self.s.drain_loop and self.s.qa_gate != "off":
                _dr_loc = await self.router.effort_thread(effort_id)
                if _dr_loc:
                    # P20 ONE TASK AT A TIME — before sweeping again, drain the tasks the LAST sweep
                    # already produced, one implementer turn each. The lens sweep is a whole-product
                    # observation and belongs at the ROUND BOUNDARY (an empty queue); running it
                    # between every task would spend three lens turns per fix for no new information.
                    # So: if this scope still has queued tasks, dispatch the NEXT SINGLE one and
                    # re-enter — the sweep waits until the queue is empty. (§4/§5: the worker holds
                    # one bounded task and is unaware of the bigger picture.)
                    _drain_node = (await self._ensure_scope_node(effort_id)
                                   if self.s.drain_tier_walk else None)
                    _pending = await self._dispatchable_tasks(effort_id, _drain_node)
                    if _pending:
                        await self._drain_iterate(
                            effort_id, _pending,
                            max(1, await self._drain_round_no(effort_id) - 1),
                            channel_id=_dr_loc[0], root=_dr_loc[1])
                        return   # the delivery re-enters: next queued task, or the sweep when empty
                    dr = await self._drain_round(
                        effort_id, _dr_loc[0], _dr_loc[1], eff_repo, delivery)
                    qa_note = dr["note"]
                    # WORK REMAINS while the queue is non-empty, even on a zero-propagation round:
                    # a task the last implementer failed to land is re-derived by the next
                    # independent sweep and REOPENED without counting as new information (it isn't
                    # new). Dispatching only on `new_tasks` would close such an effort as complete
                    # with its own queue visibly non-empty — DoD 4 is "the queue drains AND the
                    # sweep propagates zero", not either alone.
                    if dr["swept"] and dr["open_tasks"] and not dr["capped"]:
                        if await self._drain_iterate(effort_id, dr["open_tasks"], dr["round"],
                                                     channel_id=_dr_loc[0], root=_dr_loc[1]):
                            await self.comms.post(
                                Intent.worker_activity,
                                f"🔎 Drain round {dr['round']} — {dr['new_tasks']} new task(s), "
                                f"{len(dr['open_tasks'])} open; working them before the PR:\n"
                                + "\n".join(f"- {t['body']}" for t in dr["open_tasks"][:6]),
                                effort_id=effort_id)
                            return   # the improved delivery re-enters here and opens the PR then
                    elif dr["swept"] and not dr["capped"] and dr["scope_node_id"]:
                        # EVIDENCED ZERO on both counts → this scope is complete; bubble up so the
                        # parent tier re-evaluates with its child's seams now real (P10.6).
                        await self._complete_scope(dr["scope_node_id"], effort_id)
            elif self.s.qa_gate != "off":
                _qa_loc = await self.router.effort_thread(effort_id)
                if _qa_loc:
                    qa_note, qa_defects = await self._qa_evaluation(
                        effort_id, _qa_loc[0], _qa_loc[1], eff_repo, delivery)
            if self.s.qa_gate == "iterate" and qa_defects:
                if await self._auto_iterate(
                        effort_id,
                        "QA found in-scope defects a user would hit: " + "; ".join(qa_defects[:6]),
                        "QA DEFECTS (fix these, stay in scope):\n- " + "\n- ".join(qa_defects)):
                    await self.comms.post(
                        Intent.worker_activity,
                        "🔎 QA exercised the product and found fixable gaps — auto-iterating before "
                        "the PR:\n" + "\n".join(f"- {d}" for d in qa_defects[:6]),
                        effort_id=effort_id)
                    return   # the improved delivery re-enters here and opens the PR then
            # D1: open the PR that makes this delivery VISIBLE; merge stays yours (D4).
            pr_url = await self._open_delivery_pr(
                effort_id, eff_repo, branch, verified_sha=delivery.head_sha, body_extra=qa_note)
            if pr_url:
                # D2: the autonomous test series red-gates the merge — run the project's check on
                # the delivered branch BEFORE inviting the merge; red routes back, never forward.
                d2_note, delivery = await self._d2_gate(effort_id, eff_repo, delivery,
                                                        f"merge-{effort_id}")
                # DURABLE ACCEPTANCE CORPUS (§10): the project's permanent operator-review findings,
                # run on this delivery AFTER D2. Same hard red-gate — a delivery that breaks a standard
                # the org already committed to withdraws the merge and burns down, never ships.
                corpus_note, delivery = await self._acceptance_corpus_gate(
                    effort_id, eff_repo, delivery, f"merge-{effort_id}")
                gate_open = f"merge-{effort_id}" in self._pending_merge
                invite = (f"\n_`main` only changes when you merge — say **“merge it”** and I'll "
                          f"merge, or merge on GitHub after review._" if gate_open else "")
                pr_lead = ("📬 **Existing PR still open** (no new commits this round)"
                           if stale_reverify else "📬 **PR opened for review:**")
                where += f"\n{pr_lead} {pr_url}{d2_note}{corpus_note}{invite}"
                if qa_note:      # surface the QA findings in-thread too, not only in the PR body
                    where += "\n\n" + qa_note
                if gate_open:    # D2 green/skipped ⇒ safe to accumulate this delivery into develop
                    where += await self._integrate_to_develop(effort_id, eff_repo, delivery)
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
        # NO SILENT REMOVALS (operator 2026-07-09: a burn-down round DELETED a whole feature file
        # to clear compile errors and the org green-passed it — the operator only found out via a
        # broken cursor). Every landed delivery discloses what it REMOVED; a removal that isn't
        # justified by a removal/cleanup GOAL is surfaced for review, not silently "done". Generic
        # for any project — a green build never proves functionality was preserved.
        removal_note, removal_flag = await self._removal_disclosure(
            effort_id, branch, goal_text) if branch else ("", False)
        scope_note += removal_note
        # RUNTIME-SYMPTOM TRUST LADDER (operator 2026-07-10: "90% of claims are false because the
        # check compiles, it doesn't RUN the behavior … claims MUST be honest"). For a goal that's a
        # runtime/interaction/visual symptom, a green BUILD proves nothing. "Done" requires a
        # REPRODUCTION test (fails on the break, passes on the fix, wired into the check) — and the
        # claim states EXACTLY what was proven, so the commit history stays honest/researchable:
        #   • repro present + org-confirmed GREEN  → VERIFIED via reproduction (trustworthy; still
        #     human-gated in this phase — auto-merge is earned once the track record proves out).
        #   • no repro (build-only)                → UNVERIFIED: cannot claim the symptom is fixed;
        #     demand the reproduction (auto-iterate), keep visible.
        #   • UNAUTOMATABLE bit declared           → verified what's automatable; name the exact
        #     slice that needs the operator's eyes, never a blanket "done".
        runtime_symptom = (self._runtime_symptom_phrase(goal_text)
                           if branch and not (partial or comp_failed) else None)
        runtime_verified = False
        if runtime_symptom:
            repro_ok = (bool(re.search(r"\bREPRO:", out_text, re.I))
                        and bool(re.search(r"\bAFTER:\s*PASS\b", out_text, re.I)))
            unautomatable = bool(re.search(r"\bUNAUTOMATABLE:", out_text, re.I))
            org_green = bool(self._org_verified.get(effort_id))
            # HONESTY FIX (operator 2026-07-12, the atlas false-done): "verified via reproduction"
            # used to fire on (worker's REPRO word) + (org BUILD green). But a green build — or even
            # a green SMOKE launch — is NOT a reproduction of an INTERACTION symptom: the monogame
            # check builds Murder.sln and runs the editor 30s, yet never OPENS a Game Profile, so it
            # passes whether or not the atlas bug is present. The org marked the atlas "verified via
            # reproduction" trusting the worker's word + a smoke test that can't fail on the bug —
            # exactly the "90% of claims are false" symptom. A reproduction is only trustworthy when
            # the ORG ITSELF watched it go RED on the pre-fix state and GREEN on the fix. That org-run
            # before/after sets `_repro_red_green`. FAIL CLOSED: no org-observed RED→GREEN ⇒ never
            # "verified", always honest needs-attention.
            # THE HARNESS (2026-07-13, dark-factory keystone): now that the org build is GREEN and the
            # worker declared a reproduction, INDEPENDENTLY establish RED→GREEN — run the check at the
            # pre-fix base and at the fix, requiring the base to FAIL (a smoke test that stays green at
            # base earns nothing). Sets `_repro_red_green` only on a clean red→green; fail-closed on an
            # unresolvable base, a submodule-fix base that can't revert, an infra failure, or a timeout.
            # Best-effort — a harness error never blocks the closure.
            if (repro_ok and org_green and not unautomatable
                    and self._repro_red_green.get(effort_id) != self._org_verified.get(effort_id)):
                try:
                    await self._org_reproduction_verified(
                        effort_id, delivery.head_sha if delivery is not None else "")
                except Exception as exc:  # noqa: BLE001 — verify is a bonus; never block the closure
                    log.info("reproduction harness failed for %s (%s) — fail closed", effort_id, exc)
            org_ran_repro = self._repro_red_green.get(effort_id) == self._org_verified.get(effort_id)
            if repro_ok and org_green and org_ran_repro and not unautomatable:
                runtime_verified = True
                scope_note += (
                    f"\n\n✅ **Verified via reproduction:** the worker reproduced your symptom "
                    f"(“{runtime_symptom}”) as an automated test, and **I ran that reproduction "
                    f"myself — RED on the code before the fix, GREEN on the fix** — so I've "
                    f"independently proven it, not taken the worker's word. It's wired into the check "
                    f"so it can't silently regress. Merge stays yours for now."
                )
                await self.audit.log("delivery_runtime_verified", effort_id=effort_id,
                                     payload={"branch": branch, "symptom": runtime_symptom[:200],
                                              "org_green": self._org_verified.get(effort_id, "")[:10]})
            else:
                # Precise, honest reason — and only burn worker cycles when the WORKER can still act.
                # When the worker already gave a reproduction but the ORG hasn't independently run it
                # (repro_ok + org_green, no RED→GREEN), the gap is on the org's side (harness pending),
                # not the worker's — don't auto-iterate; rest as the operator's to confirm.
                worker_can_improve = not unautomatable and not repro_ok
                if unautomatable:
                    why = "it declared part of the symptom UNAUTOMATABLE"
                elif not repro_ok:
                    why = "there's no reproduction test — only a build"
                elif not org_green:
                    why = "I couldn't confirm the check went green"
                else:
                    why = ("the worker reports a passing reproduction, but I have not run it myself "
                           "against the pre-fix state — a green build/launch isn't proof the "
                           "interaction is fixed")
                iterating = worker_can_improve and await self._auto_iterate(
                    effort_id, "a runtime symptom with no passing reproduction (build-only is not "
                    "proof)", out_text[-700:])
                scope_note += (
                    f"\n\n🕹️ **Runtime symptom — NOT independently verified:** your report is a "
                    f"**runtime/interaction** behavior (“{runtime_symptom}”), and {why}. I can only "
                    f"stand behind what I actually ran (it builds and launches); a green build/launch "
                    f"can't prove a symptom that only shows when you **exercise** it, so I won't claim "
                    f"it's fixed. "
                    + ("**This slice needs your eyes** — the rest is automated. "
                       if unautomatable else
                       "_Auto-iterating with a demand for a reproduction test (fails before, passes "
                       "after, wired into the check) — no action needed._" if iterating else
                       "**Please confirm it on your end** — and I'm building the headless before/after "
                       "self-check so I can prove this class of fix myself." if repro_ok and org_green
                       else "Say **“re-run it”** and I'll push for a reproduction test.")
                )
                await self.audit.log("delivery_runtime_unverified", effort_id=effort_id,
                                     payload={"branch": branch, "symptom": runtime_symptom[:200],
                                              "why": why})
        runtime_open = bool(runtime_symptom) and not runtime_verified
        unmet_or_partial = (bool(unmet) or partial or comp_failed or removal_flag or runtime_open)
        # P8 #1 — CLOSURE INVARIANT: the PM may not claim "done" without proof. Immediately before
        # a clean close on a LANDED delivery, assert the effort's OWN audit shows the gates that
        # should have run actually did (PR / QA / develop integration). A genuine read-only
        # no-changes completion has no delivery and no gates to assert (the branch above). If a
        # gate is missing: do NOT close done — audit it, name exactly what's missing, honest
        # needs-attention, effort stays open ("I could not deliver", never a hollow "done").
        if (self.s.closure_invariant and not unmet_or_partial and delivery is not None
                and delivery.landed and not delivery.no_changes):
            _gaps = await self._closure_invariant_gaps(effort_id)
            if _gaps:
                await self.audit.log(
                    "closure_invariant_failed", effort_id=effort_id,
                    payload={"missing": [g.split("**")[1] for g in _gaps if "**" in g],
                             "branch": branch or ""})
                _gap_lines = "\n".join(f"- {g}" for g in _gaps)
                msg = (f"🛑 **{effort_id}** — I could **not** deliver this, so I'm not claiming "
                       f"done. The work is real and safe on `{branch}`, but my own audit says the "
                       f"delivery pipeline didn't complete:\n{_gap_lines}\n"
                       f"Say **“re-run it”** to retry the delivery, or tell me how to proceed.")
                await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
                await self.comms.post(Intent.operator_reply, msg,
                                      thread_id=self._mgmt_thread_of(effort_id))
                await self.router.update_effort_card(effort_id, "needs-attention")
                return
        done_word = ("done — VERIFIED via reproduction" if runtime_verified and not unmet_or_partial else
                     "done" if not unmet_or_partial else
                     "partly done — see the scope check" if unmet else
                     "partly done — the composition check failed" if comp_failed else
                     "done — but review the removals" if removal_flag and not partial else
                     "not verified — needs a reproduction test" if runtime_open and not partial else
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
        # Cross-effort DEBUG HANDOFF: if THIS effort was a handed-off fix, close the loop — tell
        # the waiting reporter and re-engage it (operator 2026-07-14). No-op for normal efforts.
        await self._resolve_handoff_if_any(effort_id, delivery, result,
                                           clean=not unmet_or_partial)

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
        # Cross-effort DEBUG HANDOFF: a FAILED fix effort must not leave its reporter waiting
        # silently — say so once, and release the reporter to the stall watchdog (it re-engages
        # and, if the bug still bites, re-raises the handoff — bounded by the cap). The B→A link
        # stays, so a later successful re-run of the fix still resumes the reporter.
        info = self._handoff_by_fix.get(effort_id)
        if info is not None and not info.get("escalated"):
            info["escalated"] = True
            frm = info["from"]
            self._handoff_waiting.discard(frm)
            await self.comms.post(
                Intent.operator_reply,
                f"⏸️ **{frm}** is still waiting on that fix — `{effort_id}` failed before "
                f"delivering it. Re-run `{effort_id}` (or steer it), or say “re-run {frm}” to "
                f"continue without the fix.",
                thread_id=self._mgmt_thread_of(frm),
            )
            await self.router.update_effort_card(frm, "needs-attention")

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

    # ── cross-effort A→B DEBUG HANDOFF (operator 2026-07-14) ──────────────────
    async def _handoff_protocol_context(self, effort_id: str) -> str:
        """The HANDOFF PROTOCOL clause that teaches a worker how to report a FOREIGN blocking bug
        (operator 2026-07-14: cross-worker debug handoff). Injected per step wake, and only when
        the org actually has somewhere to hand off TO (≥1 other registered project) — a lone
        project keeps its prompts lean."""
        try:
            slug = await self._effort_project(effort_id)
            others = [p for p in await self.projects.list()
                      if p["slug"] not in (slug, self.s.default_project)]
        except Exception:  # noqa: BLE001 — a lookup hiccup must never block a dispatch
            return ""
        if not others:
            return ""
        return (
            "\n\nHANDOFF PROTOCOL (cross-project bugs): if you are BLOCKED by a bug in code "
            "OUTSIDE this project (a sibling submodule, the host repo, another team's repo — code "
            "that is not yours to change from here), do NOT work around it, do NOT edit the "
            "foreign code, and do NOT fake progress. Instead reply with one line in exactly this "
            "form:\nHANDOFF: <path or project where the bug lives> :: <one-line summary>\n"
            "followed by the exact error output / debug log that proves it. The org wakes that "
            "project's own worker to fix and push it, then resumes you once the fix lands. Only "
            "use this for genuinely FOREIGN code — errors in this project are yours to fix."
        )

    async def _resolve_handoff_owner(self, from_slug: str | None, target: str) -> str | None:
        """Which registered project OWNS the code a handoff points at. Explicit project name
        first, then a slug/repo-name match inside the path, then the composition layout (a sibling
        submodule's path in the host's .gitmodules). None ⇒ unresolvable — routes to the human."""
        t = (target or "").strip().strip("`'\"").replace("\\", "/")
        tl = t.lower()
        if not tl:
            return None
        try:
            p = await self.projects.resolve(t)
            if p and p["slug"] != from_slug:
                return p["slug"]
            projects = await self.projects.list()
        except Exception:  # noqa: BLE001 — resolution is best-effort; unresolved goes to the human
            return None
        for p in projects:
            slug = p["slug"]
            if slug in (from_slug, self.s.default_project):
                continue
            repo_name = self._norm_repo(p.get("repo_url") or "").split("/")[-1].lower()
            if (slug and slug in tl) or (repo_name and repo_name in tl):
                return slug
        if from_slug:
            host = await self._vendored_host(from_slug)
            if host:
                _host_slug, _mine, _url, siblings = host
                for pth, rep in re.findall(r"`([^`]+)` \(`([^`]+)`\)", siblings or ""):
                    if pth.lower().strip("/") in tl:
                        for p in projects:
                            if self._norm_repo(p.get("repo_url") or "").lower() == rep.lower():
                                return p["slug"]
        return None

    async def _open_handoff(
        self, effort_id: str, channel_id: str, root: str, ho: dict, repo: str | None,
    ) -> None:
        """Cross-effort A→B DEBUG HANDOFF — BRIDGE-MEDIATED, never peer-to-peer, so the floor's
        bus-only (#3) and escalate-up (#7) rules hold (the report goes UP to the org, which wakes
        the owning project's worker and resumes the reporter when the fix lands — operator
        2026-07-14). Flow: preserve A's progress (publish its branch) → open a fix effort B on the
        owning project with the debug log as its goal (B passes the SAME delivery gates as any
        effort — a handoff is never a side door) → pause A → B's clean finish re-engages A.
        Depth 1 (no chains) + capped per effort; anything unresolvable reaches the human."""
        target, summary = ho["target"], (ho["summary"] or "(no summary given)")
        mgmt_thread = self._mgmt_thread_of(effort_id)
        await self.audit.log("handoff_requested", effort_id=effort_id,
                             payload={"target": target, "summary": summary[:200]})
        # DEPTH 1 (anti-chain — the runaway-delegation guard, governance §5): a FIX effort blocked
        # by yet another foreign bug reaches the HUMAN, not a third worker.
        if effort_id.startswith("effort-hx-"):
            msg = (f"⛔ **{effort_id}** — this handed-off FIX effort is itself blocked by another "
                   f"foreign bug (`{target}`: {summary}). Handoffs don't chain (depth 1 by "
                   f"design) — this needs you.")
            await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
            await self.comms.post(Intent.operator_reply, msg, thread_id=mgmt_thread)
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        n = await self._event_count(effort_id, "handoff_opened")
        if n >= max(1, self.s.handoff_cap):
            msg = (f"⛔ **{effort_id}** hit yet another cross-project blocker (`{target}`: "
                   f"{summary}) — past the handoff cap ({n} already opened), so I'm not opening "
                   f"another fix loop. This pattern usually means the split of work is wrong — "
                   f"needs your steer.")
            await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
            await self.comms.post(Intent.operator_reply, msg, thread_id=mgmt_thread)
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        from_proj = await self._effort_project(effort_id)
        owner = await self._resolve_handoff_owner(from_proj, target)
        if not owner:
            msg = (f"↪️ **{effort_id}** reports a blocking bug OUTSIDE its scope (`{target}`: "
                   f"{summary}), but I can't map `{target}` to any registered project — routed to "
                   f"you. (Register the owning project, or steer this effort.)")
            await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
            await self.comms.post(Intent.operator_reply, msg, thread_id=mgmt_thread)
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        # Preserve A's progress FIRST (its resume may land on a fresh clone): push what it has to
        # its own branch. Best-effort — a read-only turn just answers NO CHANGES.
        if repo:
            try:
                await self._publish_effort(effort_id, channel_id, root, repo)
            except Exception as exc:  # noqa: BLE001 — preservation must never block the handoff
                log.debug("handoff progress publish for %s failed: %s", effort_id, exc)
        goal = (
            f"CROSS-PROJECT BUG HANDOFF — a worker on `{effort_id}` (project `{from_proj}`) is "
            f"BLOCKED by a bug in THIS project ({target}): {summary}\n\n"
            f"THEIR DEBUG LOG / ERROR OUTPUT:\n{ho['log'][:2400]}\n\n"
            f"First REPRODUCE this failure here, then fix the CAUSE in this project (not a "
            f"workaround in the caller), verify your reproduction passes, then commit and push "
            f"your branch. If you reproduce it and conclude the bug is NOT in this project (the "
            f"caller misuses it), reply exactly `NO CHANGES: <your analysis>` — never force a "
            f"fix that doesn't belong here."
        )
        try:
            goal += await self._standing_intent_context(owner)
            goal += await self._acceptance_corpus_context(owner)   # corpus upstream (alteration 1)
            goal += await self._composition_context(owner)
        except Exception:  # noqa: BLE001 — context is garnish, never a blocker
            pass
        short = effort_id.removeprefix("effort-")[:22]
        fix_eid, fix_chan, fix_root = await self.router.open_effort(
            f"hx-{short}-{n + 1}", project=owner, goal=goal)
        await self.charters.set_goal(fix_eid, goal, created_by="pm")
        if mgmt_thread:
            self._effort_mgmt_thread[fix_eid] = mgmt_thread
        self._handoff_by_fix[fix_eid] = {"from": effort_id, "target": target, "escalated": False}
        self._handoff_waiting.add(effort_id)
        await self.router.record_wake(effort_id, target=owner, kind="brake")  # storm-exempt (§5)
        await self.audit.log("handoff_opened", effort_id=effort_id,
                             payload={"fix_effort": fix_eid, "owner": owner, "target": target,
                                      "attempt": n + 1})
        await self.comms.post(
            Intent.escalation,
            f"↪️ **Debug handoff:** this effort is blocked by a bug in `{owner}` (`{target}`). "
            f"I've woken `{owner}`'s worker on {self._effort_link(fix_eid, fix_root)} with the "
            f"debug log — this effort is **paused** and resumes automatically when the fix lands.",
            effort_id=effort_id,
        )
        await self.comms.post(
            Intent.operator_reply,
            f"↪️ **{effort_id}** hit a bug in `{owner}` (`{target}`: {summary}) — handed the "
            f"debug log to `{owner}`'s worker as `{fix_eid}`. `{effort_id}` waits and "
            f"auto-resumes when the fix lands. No action needed.",
            thread_id=mgmt_thread,
        )
        await self.router.update_effort_card(effort_id, "working")
        self._spawn(self.delegate(fix_eid, fix_chan, fix_root, goal))

    async def _resolve_handoff_if_any(
        self, fix_eid: str, delivery, result, *, clean: bool,
    ) -> None:
        """When a finished effort was a HANDOFF FIX, close the loop: tell the waiting reporter
        the bug is fixed — with the exact branch/commit to build against, or the owner's
        no-bug-here analysis — and RE-ENGAGE it on its original goal (operator 2026-07-14:
        "Worker is told the bug was fixed and wakes again to continue its work"). A partial/
        flagged finish keeps the reporter paused and tells the operator honestly, once."""
        info = self._handoff_by_fix.get(fix_eid)
        if info is None:
            return
        frm, target = info["from"], info["target"]
        mgmt_thread = self._mgmt_thread_of(frm)
        if not clean:
            if not info.get("escalated"):
                info["escalated"] = True
                self._handoff_waiting.discard(frm)   # let the watchdog reclaim A eventually
                msg = (f"⏸️ **{frm}** stays paused — its handed-off fix `{fix_eid}` finished only "
                       f"partially (see its closure). Steer or re-run `{fix_eid}`, or say "
                       f"“re-run {frm}” to continue without the fix.")
                await self.comms.post(Intent.operator_reply, msg, thread_id=mgmt_thread)
                await self.router.update_effort_card(frm, "needs-attention")
            return
        self._handoff_by_fix.pop(fix_eid, None)
        self._handoff_waiting.discard(frm)
        out_tail = ((result.output or "").strip()[:600]) if result is not None else ""
        if delivery is not None and delivery.landed:
            repo = await self._effort_repo(fix_eid)
            sha = (delivery.head_sha or "")[:10]
            fix_note = (
                f"the owning worker fixed it on branch `{delivery.branch}` of "
                f"`{self._norm_repo(repo) if repo else 'its repo'}`"
                + (f" @ `{sha}`" if sha else "")
                + f" — a PR is open (merge to main stays with the operator). To build against "
                  f"the fix NOW, fetch that branch in the affected checkout (`{target}`): "
                  f"`git fetch origin {delivery.branch} && git checkout {delivery.branch}`."
            )
        elif delivery is not None and delivery.no_changes:
            fix_note = (f"the owning project's worker investigated and reports the bug is NOT on "
                        f"their side — their analysis:\n{out_tail}\nAdjust your approach "
                        f"accordingly.")
        else:
            fix_note = f"the owning project's worker reports it resolved: {out_tail}"
        await self.audit.log("handoff_resolved", effort_id=frm,
                             payload={"fix_effort": fix_eid, "target": target,
                                      "landed": bool(delivery is not None and delivery.landed)})
        await self.comms.post(
            Intent.worker_activity,
            f"✅ **Handoff resolved** — {fix_note}\n↩️ Resuming `{frm}` on its original goal now.",
            effort_id=frm,
        )
        await self.comms.post(
            Intent.operator_reply,
            f"✅ the bug `{frm}` was blocked on is resolved (`{fix_eid}`) — resuming `{frm}` "
            f"automatically.",
            thread_id=mgmt_thread,
        )
        loc = await self.router.effort_thread(frm)
        try:
            _v, goal, _s = await self.charters.current_goal(frm)
        except Exception:  # noqa: BLE001
            goal = ""
        if loc is None or not goal:
            await self.router.update_effort_card(frm, "needs-attention")
            return
        chan, res_root = loc
        resume_goal = (
            f"{goal}\n\nHANDOFF RESOLVED ({target}): {fix_note} Your own earlier progress (if "
            f"any) is on branch `agent/{frm}` — continue from it toward your ORIGINAL goal above."
        )
        await self.router.update_effort_card(frm, "active")
        self._spawn(self.delegate(frm, chan, res_root, resume_goal))

    async def _effort_risk_str(self, effort_id: str) -> str:
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
        return e.risk if e and e.risk else "routine"

    # ── worker-side plan gate (operator 2026-07-14 — headless plan mode) ──────
    async def _worker_plan_required(self, effort_id: str) -> bool:
        """Whether this dispatch plans first (AO_WORKER_PLAN_GATE): `all` = every effort,
        `risky` = high-blast-radius only (default), `off` = never."""
        # The flail guard forces the next dispatch through the plan gate regardless of mode —
        # the worker just proved it doesn't know how to proceed (one-shot; even mode=off).
        if effort_id in self._force_plan:
            self._force_plan.discard(effort_id)
            return True
        mode = self.s.worker_plan_gate
        if mode == "off":
            return False
        if mode == "all":
            return True
        return self.exec_gate.dry_run_required(await self._effort_risk_str(effort_id))

    async def _flail_replan(self, effort_id: str, result) -> None:
        """The daemon's FLAIL GUARD killed a coding turn that kept READING without one edit
        (operator 2026-07-14: the signal to "stop, fork from original user prompt and re-ask in
        plan mode"). Retrying INTO that context reproduces the spiral — the fix is a FORK: the
        `flail_replanned` event bumps the session generation (a fresh session, seeded only by the
        re-dispatch goal) and forces the next dispatch through the plan gate. Bounded to ONCE per
        effort; a second flail is a real can't-converge signal for the human."""
        head = (result.output or "").strip().splitlines()[0][:200]
        n = await self._event_count(effort_id, "flail_replanned")
        if n >= 1:
            msg = (f"⛔ **{effort_id}** — the worker flailed again after a fresh plan-first "
                   f"restart ({head}). It can't converge on this goal without help — steer it "
                   f"(what should the approach be?) or say “re-run it”.")
            await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
            await self.comms.post(Intent.operator_reply, msg,
                                  thread_id=self._mgmt_thread_of(effort_id))
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        # The event BOTH records the decision and rotates _session_for's generation — the fork.
        await self.audit.log("flail_replanned", effort_id=effort_id, payload={"detail": head})
        self._force_plan.add(effort_id)
        try:
            _, goal_text, _ = await self.charters.current_goal(effort_id)
        except Exception:  # noqa: BLE001
            goal_text = ""
        base_goal = (goal_text or "").split("\n\nITERATION ")[0].strip()
        if not base_goal:
            await self.router.update_effort_card(effort_id, "needs-attention")
            return
        note = (f"🌀 The worker was spinning — reading and thinking without a single edit "
                f"({head}). I stopped it, and I'm **forking a fresh session from the original "
                f"goal and re-asking in plan mode** so it commits to an approach before touching "
                f"code. No action needed.")
        await self.comms.post(Intent.worker_activity, note, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, note,
                              thread_id=self._mgmt_thread_of(effort_id))
        # Queued (not spawned) — delegate's finally launches it AFTER this run fully closes,
        # exactly like an auto-iteration, so the single-flight guard can't collide.
        self._iterate_after[effort_id] = base_goal

    async def _plan_misalignment(self, effort_id: str, goal: str, plan: str,
                                 *, scope_goal: str = "") -> str | None:
        """WHY the worker's plan is misaligned with the goal, or None when it's fine.
        Deterministic checks first (forbidden standing-intent terms; declared delete-to-pass on a
        non-removal goal), then the PM's LLM lens (off-goal judgment). The LLM lens FAILS OPEN on
        a model hiccup — the deterministic checks and the delivery-side gates still stand.

        `scope_goal` (P13.2): when a BOUNDED SCOPE is in force, that is the standard the plan is
        held to — not the whole goal. Declining out-of-scope work with `ESCALATE:` is the protocol
        `_scope_context` prescribes, so it must read as compliance. Skipping an IN-SCOPE task is
        still a deviation."""
        proj = await self._effort_project(effort_id)
        try:
            p = await self.projects.get(proj) if proj else None
        except Exception:  # noqa: BLE001
            p = None
        # REASONING, not determinism (operator 2026-07-14): the lens judges INTENT, with the
        # standing intent + forbidden terms supplied as context — and told explicitly that
        # naming a forbidden thing in order to REMOVE it is compliant (two honest removal
        # plans were rejected by keyword matching before this). Fails open on a model hiccup:
        # the delivery gates on the ACTUAL diff remain the deterministic backstop.
        si = ((p or {}).get("standing_intent") or "").strip()
        forb = self._forbidden_terms(si)
        try:
            verdict = await self.models.structured(
                "pm",
                "You are the PM. A worker proposed this PLAN before touching any code. Judge "
                "whether EXECUTING the plan would accomplish the GOAL without violating its "
                "constraints. Reason about INTENT, not keywords: a plan that mentions a "
                "forbidden thing in order to REMOVE or verify the absence of it is COMPLIANT; "
                "a plan that would add it back, keep depending on it, or delete working "
                "features to force a green build is a DEVIATION. Set deviates=true only for a "
                "real misalignment, with a specific rationale."
                # P17 F7 — THE GATE MUST FAIL IN BOTH DIRECTIONS. Within one gym-015 effort it
                # REJECTED a correct plan for "completely fail[ing] to address the core goal" when
                # every named feature was already implemented, committed and passing (10 commands,
                # 51 tests green) — then APPROVED, as "aligned with the goal", a plan to add
                # `pyproject.toml`, a version string and mypy config to a goal about a polished
                # todo CLI. Same effort, opposite errors: it was matching how much the plan's prose
                # resembled the goal's prose. The worker layer already gets this right ("already
                # satisfied, no changes needed"), so the discipline exists one tier down.
                + "\nOMISSION IS NOT AUTOMATICALLY A DEVIATION. The plan's ALREADY DONE section "
                  "reports what the worker OBSERVED to be working, with the command or file that "
                  "shows it. Work that is already done must NOT be re-planned: a plan that omits "
                  "it BECAUSE the observation shows it exists is CORRECT and must be approved. "
                  "Treat an omission as a deviation only when nothing in ALREADY DONE accounts "
                  "for it.\n"
                  "DRIFT IS ALSO A DEVIATION. Every step the plan proposes must be traceable to "
                  "the goal or the scope. A plan that proposes work nobody asked for — packaging "
                  "metadata, version strings, linter or CI configuration, refactors, extra "
                  "features — is a DEVIATION even though it sounds constructive. Judge what the "
                  "plan ADDS as strictly as what it leaves out."
                + ("\nA BOUNDED SCOPE is in force (see SCOPE below). Judge the plan against THAT "
                   "scope only. Declining work that falls outside it — especially with an "
                   "`ESCALATE:` marker naming the adjacent owner — is the REQUIRED protocol and is "
                   "COMPLIANT, never a refusal. Set deviates=true only if the plan skips work that "
                   "is INSIDE the scope, or would violate a constraint." if scope_goal else ""),
                (f"SCOPE IN FORCE (judge against this, not the whole goal):\n{scope_goal[:1200]}\n\n"
                 if scope_goal else "")
                + f"GOAL:\n{(goal or '')[:2000]}\n\n"
                f"STANDING INTENT (non-negotiable constraint):\n{si or '(none)'}\n\n"
                f"FORBIDDEN — must never be (re)introduced; removing/mentioning them is fine: "
                f"{', '.join(forb) if forb else '(none)'}\n\n"
                f"WORKER PLAN:\n{plan[:2500]}",
                MonitorVerdict,
            )
        except ModelBackpressureError:
            raise
        except Exception as exc:  # noqa: BLE001 — the lens is best-effort; delivery gates remain
            log.debug("plan alignment lens failed for %s: %s", effort_id, exc)
            return None
        if getattr(verdict, "deviates", False):
            return (getattr(verdict, "rationale", "") or "the PM judged it off-goal")[:300]
        return None

    async def _revert_plan_turn_writes(self, effort_id: str) -> bool:
        """P17 F1/F15 — undo anything a supposedly read-only plan turn wrote. Returns True when it
        had in fact written something.

        `plan_only` excludes the daemon's edit/write TOOLS; it does not restrict the shell. gym-015
        proved a deny-list cannot close that: round 3 wrote with `sed -i` and committed `33eae95`;
        round 5 used a `python3 -c` read-modify-write and committed `1b04400`. Any interpreter with
        file access is another route, so the durable fix is a read-only workspace for the duration
        of the turn (a worker-image change).

        This is the interim guard, and it targets the property that matters rather than the
        mechanism: after a plan turn the workspace must look exactly as it did before, so the gate
        judges a PLAN and not a fait accompli. It also removes the way `33eae95` was lost — a plan
        turn committed, reported the commit as delivered, and the post-gate re-clone destroyed it
        24 seconds later, leaving the org's record pointing at a commit that existed nowhere.

        Reverts uncommitted edits AND resets a commit the plan turn made (`reset --soft` is
        proxy-legal where `--hard` is not; the working tree is then cleaned separately). Never
        raises: a failed revert must not block the gate."""
        probe = ("cd /workspace && "
                 "echo BEFORE-HEAD=$(git rev-parse HEAD 2>/dev/null) && "
                 "git status --porcelain | head -20 && echo PROBE-DONE")
        try:
            _exit, out, _timed = await self.router.exec_check(
                effort_id, command=probe, session_id=f"{effort_id}~planclean",
                repo=None, repo_token=None, timeout=120)
        except Exception as exc:  # noqa: BLE001 — best-effort; never a gate blocker
            log.debug("plan-turn write probe failed for %s: %s", effort_id, exc)
            return False
        dirty = bool(out and re.search(r"^[ ?MADRUC][ ?MADRUC]\s+\S", out, re.M))
        if not dirty:
            return False
        await self.audit.log("plan_turn_wrote_files", effort_id=effort_id,
                             payload={"status": (out or "")[:400]})
        try:
            await self.router.exec_check(
                effort_id,
                command=("cd /workspace && git checkout -- . 2>/dev/null; "
                         "git clean -fd 2>/dev/null; echo REVERT-DONE"),
                session_id=f"{effort_id}~planclean", repo=None, repo_token=None, timeout=120)
        except Exception as exc:  # noqa: BLE001
            log.debug("plan-turn revert failed for %s: %s", effort_id, exc)
            return True
        await self.comms.post(
            Intent.worker_activity,
            "🧹 The plan turn modified files even though it was a read-only turn — reverted them "
            "so the plan is reviewed on its merits, not on work already done.",
            effort_id=effort_id,
        )
        return True

    async def _worker_plan_gate(
        self, effort_id: str, channel_id: str, root: str, goal: str, repo: str | None,
        repo_token: str | None, upstream: str | None, upstream_token: str | None,
    ) -> str | None:
        """PLAN-FIRST dispatch (operator 2026-07-14): the worker maps its approach in a READ-ONLY
        turn (edit/write tools excluded — the plan-mode guard, headless) and the PM checks the
        plan against the goal BEFORE any code changes. One steered revision; a second misaligned
        plan stops honestly — catching the wrong direction costs two model turns here instead of
        a wasted implementation + operator steering + a restart.

        Returns the APPROVED PLAN TEXT to execute, or None if stopped (this method posted the
        state).

        P17 F16 — the plan runs in its OWN session, and is returned as an ARTIFACT rather than
        left as implicit session memory. Both halves matter:

        * FRESH SESSION. This turn used to run in `_session_for(effort_id)` — the very session
          that had just built the thing. gym-015: a 7-minute build turn was followed, in the same
          session, by a 21-second "plan" that replied "The work is already complete — all features
          implemented, all 51 tests passing". That is RECALL, not observation: the worker was
          reporting its own previous turn, never looking at the repository. Zero-change plans and
          the plan gate's contradictory verdicts (P17 F7 — it was adjudicating between two
          recollections) both follow from that. ORCHESTRATION-DESIGN §11 already mandates the cure
          for the reviewer — "cleared-context adversarial review ... a fresh reviewer isn't
          carrying the builder's rationalizations" — and P10.1 applied it to the lenses. The plan
          step never got it. It does now.
        * EXPLICIT ARTIFACT. Execution used to be told "your plan (previous turn in this session)",
          which only works while the plan and the execution share a session — the coupling that
          forced the plan into the builder's session in the first place. Handing the text back
          makes the plan an executable-contract-shaped artifact (§11) and lets the two steps have
          whatever sessions they need."""
        await self.comms.post(
            Intent.effort_dispatch,
            "📐 **Plan first** — the worker maps its approach in a read-only turn, and I check "
            "it against the goal before any code changes.",
            effort_id=effort_id,
        )
        # P17 F16 — this turn runs in a FRESH session with NO memory of any previous turn, which is
        # the point: the plan must come from the repository, not from recollection. That cuts both
        # ways, so the instruction is explicit about it. Without the first line a context-free
        # worker plans to build things that already exist (the mirror of the recall failure); with
        # it, "already done" becomes a CITED observation instead of a remembered one.
        instr = (
            "PLAN FIRST — do NOT change any file this turn (your edit tools are disabled; "
            "explore only).\n"
            # P17 F1 — the tool block does not cover the shell, and gym-015's plan turns wrote via
            # `sed -i` and then via `python3 -c`. Name the routes explicitly: a model that has been
            # told "your edit tools are disabled" reasonably concludes the shell is the sanctioned
            # way to proceed. Anything written here is reverted before the gate runs anyway, so
            # writing is purely wasted turn budget.
            "This includes the shell: no `sed -i`, no redirection into a file, no `python3 -c` "
            "that writes, no `git add`/`commit`/`push`. Read-only commands only. Any change you "
            "make this turn will be REVERTED before your plan is reviewed — writing here costs "
            "you the turn and lands nothing.\n"
            "You have NO memory of earlier turns on this project. Before planning anything, LOOK "
            "at the current state of the workspace and establish what ALREADY EXISTS — read the "
            "relevant files, run the test suite, try the entry point. Much of the goal may "
            "already be implemented.\n"
            "Then reply with exactly:\n"
            "UNDERSTANDING: the goal in your own words (1-2 lines)\n"
            "ALREADY DONE: what you OBSERVED to be working, each with the command or file that "
            "shows it (write 'nothing' if the work has not been started)\n"
            "PLAN: numbered steps for what REMAINS — name the files you'll touch and what changes "
            "in each (write 'nothing remains' if your observations show the goal is already met)\n"
            "WON'T DO: what you will NOT change (out of scope for this goal)\n"
            "RISKS: what could go wrong / anything you're unsure of\n\n"
            f"The goal:\n{goal}"
        )
        reason: str | None = None
        misaligned = 0
        empties = 0
        stubs = 0
        # P13.2 — JUDGE THE PLAN AGAINST THE SCOPE IN FORCE. When the drain dispatches a bounded
        # scope, the goal text carries that scope's border, and declining out-of-scope work with
        # `ESCALATE:` is the PRESCRIBED protocol (`_scope_context`), not a refusal. gym-011 blocked
        # an effort over exactly that: the worker escalated 7 sibling-scope tasks and the gate,
        # comparing against the whole goal, called it "refus[ing] to implement multiple requested
        # tasks". P13.1 stops the lists diverging; this stops the gate punishing the protocol if
        # they ever do again.
        scope_goal = ""
        _m = re.search(r"YOUR SCOPE — `([^`]+)` \(tier \d+\):\n(.+?)\nThis scope is the WHOLE",
                       goal or "", re.S)
        if _m:
            scope_goal = _m.group(2).strip()
        while misaligned < 2:
            # P17 F16 — `~plan` keeps this OFF the builder's session so the plan is observed, not
            # recalled. Derived from `_session_for` (not a fixed id) so the `worker_plan_empty`
            # generation bump still rotates it: an overflowing session must stay self-healable.
            result = await self.router.wake(
                effort_id, role="worker-default", thread_id=root, channel_id=channel_id,
                session_id=f"{await self._session_for(effort_id)}~plan", instruction=instr,
                repo=repo, repo_token=repo_token, upstream=upstream,
                upstream_token=upstream_token, plan_only=True,
            )
            # P17 F1/F15 — A PLAN TURN MUST LEAVE NOTHING ON DISK. `plan_only` gates the daemon's
            # edit/write TOOLS, not the shell, and gym-015 broke it twice by different routes:
            # round 3 with `sed -i` + `git commit` (33eae95), round 5 with a `python3 -c`
            # read-modify-write + commit (1b04400). A command deny-list cannot close this — any
            # interpreter with file access defeats it — and the durable fix is a read-only
            # workspace for the turn, which needs a worker-image change.
            #
            # Until then, DETECT AND REVERT. This is the property that actually matters: the gate
            # must judge a plan, not a fait accompli, and the post-gate re-clone must not be able
            # to silently destroy work (which is how 33eae95 was lost — reported as delivered,
            # then wiped 24 seconds later). Reverting here makes the violation visible and the
            # workspace honest, whichever mechanism the worker used.
            if repo:
                await self._revert_plan_turn_writes(effort_id)
            if result is None:
                await self._report_completion(effort_id, None)
                return None
            out = result.output or ""
            if not result.ok:
                if result.status == "clone_failed":
                    await self._handle_clone_failure(effort_id, result)
                    return None
                if is_backpressure_text(out):
                    raise ModelBackpressureError(f"plan turn shed: {out[:160]}")
                await self._escalate_worker_failure(effort_id, result)
                return None
            if not out.strip():
                # An EMPTY plan reply is a rotted/overflowing SESSION symptom, not a worker
                # decision (live 2026-07-14: a 593KB base session made the model return EMPTY on
                # every plan turn — the gate stopped with "plan missing", honest but self-healable).
                # The `worker_plan_empty` event bumps _session_for's generation, so the retry runs
                # the SAME self-contained plan request in a FRESH session. One retry; a second
                # empty is a real can't-answer and stops below.
                empties += 1
                await self.audit.log("worker_plan_empty", effort_id=effort_id,
                                     payload={"retry": empties})
                if empties <= 1:
                    await self.comms.post(
                        Intent.worker_activity,
                        "🌀 The plan turn came back EMPTY — a rotted/overflowing session, not an "
                        "answer. Retrying the plan request in a FRESH session.",
                        effort_id=effort_id,
                    )
                    continue
                reason = "the worker replied EMPTY twice, even in a fresh session"
                break
            # A genuine blocker named at PLAN time is the cheapest possible catch — elevate it
            # before any work. EXPLICIT protocol markers only: the plan's RISKS section is
            # SUPPOSED to speculate ("could be blocked by…"), so the plain-language blocker
            # heuristic would false-positive here and bounce good plans to the operator.
            # P14.1 — try the TREE before waking a human. A sibling-scope handoff is routing the
            # decomposition already encodes; only an escalation with no plausible owner is a
            # genuine operator question.
            if await self._route_escalation(effort_id, out):
                await self.audit.log("worker_plan_approved", effort_id=effort_id,
                                     payload={"attempt": misaligned + 1, "escalation_routed": True})
                return out
            blk = self._extract_blocker(out) if re.search(r"\b(BLOCKED|NEEDS):", out) else None
            if blk:
                await self._elevate_blocker(effort_id, blk)
                return None
            # P13.3 — A NARRATION STUB IS A MISSING PLAN, NOT A BAD ONE. gym-011: the worker had
            # implemented, tested and COMMITTED the work; its turn's final output was the single
            # line "Final test run and commit:" — a preamble. The gate adjudicated that stub, found
            # it "severely incomplete", and rejected finished work. This is the THIRD instance of
            # one pattern (gym-009 and gym-010: a truncated lens narration read as findings →
            # phantom tasks). P11.5 fixed it for lenses with a substance floor; the same discipline
            # applies to every turn-output consumer. Re-ask ONCE rather than judge the fragment.
            if not _is_plan_reply(out) and stubs < 1:
                stubs += 1
                await self.audit.log("worker_plan_stub", effort_id=effort_id,
                                     payload={"chars": len(out.strip()), "body": out.strip()[:200]})
                await self.comms.post(
                    Intent.worker_activity,
                    "🌀 The plan turn ended on a narration line rather than a plan — that's a "
                    "TRUNCATED turn, not an answer. Asking for the plan itself.",
                    effort_id=effort_id,
                )
                instr = (
                    "Your previous turn ended before you wrote the plan (its last line was "
                    "narration). Write ONLY the plan now, in the format UNDERSTANDING / PLAN / "
                    "WON'T DO / RISKS. If the work is already complete, say so and list what was "
                    "done, with the commit. Do NOT change any file this turn."
                )
                continue
            reason = await self._plan_misalignment(effort_id, goal, out, scope_goal=scope_goal)
            if reason is None:
                await self.audit.log("worker_plan_approved", effort_id=effort_id,
                                     payload={"attempt": misaligned + 1})
                await self.comms.post(
                    Intent.worker_activity,
                    "✅ Plan reviewed — aligned with the goal. Executing it now.",
                    effort_id=effort_id,
                )
                return out
            misaligned += 1
            await self.audit.log("worker_plan_rejected", effort_id=effort_id,
                                 payload={"attempt": misaligned, "reason": reason[:300]})
            if misaligned == 1:
                # P13.4 — DON'T ASSERT WHAT WE HAVEN'T CHECKED. This used to claim "no code has
                # been touched" unconditionally. In gym-011 it said so at 18:48:39 while commit
                # `d3de299` sat in the worker's workspace, made 13 seconds earlier — the plan turn
                # is `plan_only`, but that gates the edit/write TOOLS, not `cat > file` or `git
                # commit`. An org that reports its own gate outcomes wrongly is the failure class
                # the closure invariant exists to prevent: the report and the reality must agree.
                await self.comms.post(
                    Intent.worker_activity,
                    f"↩️ Plan NOT aligned — {reason}. Asking for a revised plan.",
                    effort_id=effort_id,
                )
                instr = (
                    f"Your plan is NOT aligned with the goal: {reason}\n"
                    f"Write a REVISED plan in the same format (UNDERSTANDING / PLAN / WON'T DO / "
                    f"RISKS) that fixes this. Do NOT change any file this turn."
                )
        # The stop event ALSO rotates _session_for's generation, so the operator's "re-run it"
        # starts from a fresh session instead of re-entering the one that just failed here.
        await self.audit.log("worker_plan_stopped", effort_id=effort_id,
                             payload={"reason": (reason or "")[:250]})
        # Say WHAT the effort is for, not just its id (operator 2026-07-14, twice: the stop
        # "doesn't state at all what specific task it's planned for").
        goal_head = " ".join((goal or "").strip().split())[:160]
        msg = (f"⛔ **{effort_id}** — its task: “{goal_head}…”\n"
               f"The worker couldn't produce an aligned plan ({reason}). Stopped at the plan gate, "
               f"before any work was dispatched. Steer it (tell me what "
               f"to change) or say “re-run it” (a re-run starts from a fresh session).")
        await self.comms.post(Intent.escalation, msg, effort_id=effort_id)
        await self.comms.post(Intent.operator_reply, msg,
                              thread_id=self._mgmt_thread_of(effort_id))
        await self.router.update_effort_card(effort_id, "needs-attention")
        return None

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
                # ABORT FALLBACK (live 2026-07-15, the gym 'ouroboros'): `abort <effort>` on an
                # effort with NO open concern used to dead-end on "no open concern" — but the
                # operator's plain meaning is CANCEL THE EFFORT. Fall back to archiving it
                # (reversible; pushed branches kept), exactly like the NL archive path.
                if (cmd == "abort" and effort_id.startswith("effort-")
                        and "no open concern" in str(exc)):
                    await self._archive_efforts(
                        [effort_id], mgmt_channel=channel_id, mgmt_thread=thread_id,
                        reply_prefix=f"_(no open concern on `{effort_id}` — archiving it instead)_")
                    return
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
