"""router/waker — channel<->effort<->session map + wake execution + provenance (PLAN §3.1.1).

Coordination lives HERE, in the deterministic bridge, not in agent-to-agent negotiation
(§3.5 — weak models botch direct A2A). The router:
  - owns the channel/thread <-> effort <-> little-coder --session map (P1.1),
  - executes a wake: acquire a pool slot (scheduler) -> inject the worker's grounded
    context -> run the worker (harness) -> post the reply back on the bus (P1.2/P1.3),
  - resolves the A->B hand-off target via last-owner provenance (git-blame v1, P5.4),
  - accounts wake-storm rate caps on WORK chatter only — the brake channel is sacred
    and exempt (P5.6/§5).

Bus-only comms (P1.4/§5) are structural: the worker's only transport is the bridge; it
posts back through `chat.post`, never a side-channel.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select

from ..config import Settings
from ..db import Database
from ..models import Effort, SessionMap, WakeLog
from ..schemas import Trigger
from .audit_sink import AuditSink
from .governance_gate import GovernanceGate
from .scheduler import FrozenEffortError, NoCapacityError, Scheduler
from ..worker.harness import WorkerHarness, WorkResult

log = logging.getLogger("agent_bridge.router")

# Effort-card status glyphs (the root post reflects the live gate/lifecycle state, CM.4/CM.6).
_STATUS_ICON = {
    "active": "🟢",
    "frozen": "🟡",
    "done": "✅",
    "aborted": "⛔",
    "error": "⚠️",
    "needs-attention": "🟠",   # did its piece but the operator's INTENT isn't complete (scope miss)
}


def slugify(name: str) -> str:
    """kebab-case slug for a project/effort name (channel-safe, matches the research's
    `[category]/[project]` convention)."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "untitled"


# Reserved effort id for the read-only project survey (UX-FLOW Stage 1 anchor). It exists only so
# the survey respects the scheduler's concurrency semaphore (surveys yield to real work).
SURVEY_EFFORT = "__survey__"
CAPABILITY_EFFORT = "__capability__"  # reserved effort for operator-plane git ops (compose/submodule)


def _is_worker_unavailable(exc: Exception) -> bool:
    """A dispatch failure that means THE WORKER is wedged/unreachable (not a task or repo problem):
    409 (daemon already busy — the state-mismatch that trapped efforts), 502/503, or any transport
    error (connection refused/timeout = the daemon is down). These quarantine the worker + retry
    elsewhere; anything else (e.g. a clone auth error) is NOT a worker fault and propagates normally."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (409, 502, 503)
    return isinstance(exc, httpx.TransportError)
_SURVEY_PROMPT = (
    "PROJECT SURVEY (READ-ONLY — do not modify, create, or delete any files; do not run tests that "
    "mutate state). In 8–12 terse lines, give a FACTUAL summary of THIS repository so future work "
    "can anchor to it: primary language(s) + framework; top-level structure (key directories); the "
    "build/deps manifest; the test framework + how tests are run; the naming + code conventions you "
    "actually observe; and where a new small feature or utility would conventionally live. Facts "
    "only — no recommendations, no changes."
)

# A context builder returns the full prompt injected into a worker on wake
# (goal + floor + steering + plan). Wired to charters.build_context by the
# orchestrator; a trivial default keeps the router testable in isolation.
ContextBuilder = Callable[[str, str], Awaitable[str]]


async def _default_context(effort_id: str, role: str) -> str:
    return f"[effort={effort_id} role={role}]"


class Router:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        gate: GovernanceGate,
        scheduler: Scheduler,
        harness: WorkerHarness,
        chat,  # ChatAdapter
        audit: AuditSink,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.db = db
        self.s = settings
        self.gate = gate
        self.scheduler = scheduler
        self.harness = harness
        self.chat = chat
        self.audit = audit
        self.build_context = context_builder or _default_context
        # Recent worker activity per effort (the streamed commands) so the PO can answer
        # "what's going on?" with real visibility instead of "I don't have visibility."
        self._activity: dict[str, list[str]] = {}
        # Optional async hook `(repo, upstream) -> str | None`, tried when a focus reports the
        # upstream bake FAILED. A truthy return means the caller RECOVERED (e.g. the orchestrator
        # verified the registry upstream is wrong via the forge API and corrected it) and is the
        # message to post instead of the generic private-or-unreachable warning. Same wiring style
        # as scheduler.on_release.
        self.on_upstream_fail = None

    def _record_activity(self, effort_id: str, line: str) -> None:
        buf = self._activity.setdefault(effort_id, [])
        buf.append(line[:160])
        del buf[:-30]  # keep the last ~30 activity lines

    def recent_activity(self, effort_id: str, n: int = 8) -> list[str]:
        """The last `n` streamed worker actions for an effort (for the PO's progress answers)."""
        return self._activity.get(effort_id, [])[-n:]

    # ── project channel + effort thread (COMMS-MODEL §4 / CM.1) ──────────────
    def _effort_card(self, name: str, goal: str = "", status: str = "active") -> str:
        """The effort-card ROOT post. Its post id becomes the effort's thread; all effort
        activity (dispatch, worker stream, review, closure) posts as replies under it."""
        icon = _STATUS_ICON.get(status, "🟢")
        head = f"🧵 **Effort: {name}** — {icon} `{status}`"
        return f"{head}\n> {goal}" if goal else head

    async def ensure_project_channel(self, project: str) -> str:
        """Create-or-get the stable `#proj-<slug>` channel for a project (repo/product).
        Many efforts share it — the sidebar never grows with task volume (CM.1)."""
        return await self.chat.ensure_channel(f"proj-{slugify(project)}")

    async def open_effort(
        self, name: str, *, project: str | None = None, goal: str = "",
    ) -> tuple[str, str, str]:
        """Open (or reuse) an effort as a THREAD in its project channel. Posts the effort-card
        root post; its id is the effort's thread. Returns (effort_id, project_channel_id,
        root_post_id). Idempotent: re-opening the same effort reuses its existing thread."""
        project = project or self.s.default_project
        effort_id = f"effort-{slugify(name)}"
        channel_id = await self.ensure_project_channel(project)
        # Reuse the existing thread if this effort was already opened.
        async with self.db.session_factory() as s:
            existing = await s.get(Effort, effort_id)
            if existing is not None and existing.root_post_id:
                if existing.lifecycle != "open":
                    # Re-opening a closed effort with NEW work (a re-reported error reuses the
                    # slug): flip it back to `open`, else it stays invisible to re-engage/status
                    # forever (live 2026-07-06: "re-run <effort>" → "Nothing to re-engage"
                    # because the effort's lifecycle was still `done` from an earlier delivery).
                    existing.lifecycle = "open"
                    await s.commit()
                    await self.audit.log("effort_reopened", effort_id=effort_id,
                                         payload={"was": "done-or-aborted"})
                    # RE-SURFACE the thread: activity resumes inside a card posted hours/days ago,
                    # which the operator can't find in the channel view (live 2026-07-06: "I don't
                    # see the effort in the project channel"). A fresh top-level pointer fixes it.
                    try:
                        link = ""
                        if hasattr(self.chat, "permalink"):
                            link = self.chat.permalink(existing.root_post_id) or ""
                        await self.chat.post(
                            existing.channel_id or channel_id,
                            f"🔁 **Effort `{name}` reopened** — new work continues in its "
                            f"original thread" + (f": {link}" if link else
                                                  " (its effort card, earlier in this channel)."),
                        )
                    except Exception as exc:  # noqa: BLE001 — visibility is garnish, never a blocker
                        log.debug("reopen pointer post failed for %s: %s", effort_id, exc)
                return effort_id, existing.channel_id or channel_id, existing.root_post_id
        card = await self.chat.post(channel_id, self._effort_card(name, goal, "active"))
        root_post_id = card["id"]
        await self.gate.ensure_effort(
            effort_id, name, channel_id=channel_id, project=project, root_post_id=root_post_id
        )
        await self.map_thread(root_post_id, channel_id, effort_id, session_id=effort_id)
        return effort_id, channel_id, root_post_id

    async def ensure_effort_channel(self, name: str) -> tuple[str, str]:
        """Back-compat shim (HTTP `/effort`, `/effort` command): open an effort thread in the
        default project and return (effort_id, project_channel_id). New callers use
        `open_effort` to also get the thread root."""
        effort_id, channel_id, _root = await self.open_effort(name)
        return effort_id, channel_id

    async def effort_thread(self, effort_id: str) -> tuple[str, str] | None:
        """(project_channel_id, root_post_id) for an effort, or None."""
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            if e and e.channel_id and e.root_post_id:
                return e.channel_id, e.root_post_id
        return None

    async def update_effort_card(self, effort_id: str, status: str) -> None:
        """Edit the effort-card root post to reflect the current status (CM.4/CM.6). Best-effort:
        if the platform can't edit posts, this is a no-op (the thread replies still carry state)."""
        loc = await self.effort_thread(effort_id)
        if not loc:
            return
        channel_id, root_post_id = loc
        async with self.db.session_factory() as s:
            e = await s.get(Effort, effort_id)
            name = e.name if e else effort_id
            goal = ""
        update = getattr(self.chat, "update_post", None)
        if update is None:
            return
        try:
            await update(root_post_id, self._effort_card(name, goal, status))
        except Exception as exc:  # noqa: BLE001 - card is cosmetic; never break the flow
            log.debug("update_effort_card(%s,%s) failed: %s", effort_id, status, exc)

    async def map_thread(
        self, thread_id: str, channel_id: str, effort_id: str, session_id: str | None = None
    ) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(SessionMap, thread_id)
            if row is None:
                s.add(
                    SessionMap(
                        thread_id=thread_id,
                        channel_id=channel_id,
                        effort_id=effort_id,
                        session_id=session_id or thread_id,  # session id == thread id
                    )
                )
                await s.commit()

    async def resolve_effort_by_thread(self, thread_id: str) -> str | None:
        """Resolve an effort from a thread root (a reply in an effort thread carries
        root_id = the effort-card post id). This replaces channel-keyed lookup now that a
        channel is a project (many efforts), not a single effort (CM.1)."""
        if not thread_id:
            return None
        async with self.db.session_factory() as s:
            e = (
                await s.execute(select(Effort).where(Effort.root_post_id == thread_id))
            ).scalar_one_or_none()
            if e is not None:
                return e.id
            row = await s.get(SessionMap, thread_id)
            return row.effort_id if row else None

    async def resolve_project_by_channel(self, channel_id: str) -> str | None:
        """The project slug for a `#proj-<slug>` channel (for a top-level new-effort post)."""
        async with self.db.session_factory() as s:
            e = (
                await s.execute(
                    select(Effort).where(Effort.channel_id == channel_id).limit(1)
                )
            ).scalar_one_or_none()
            return e.project if e else None

    async def resolve_session(self, thread_id: str) -> tuple[str, str] | None:
        async with self.db.session_factory() as s:
            row = await s.get(SessionMap, thread_id)
            return (row.effort_id, row.session_id) if row else None

    # ── wake execution (P1.2/P1.3) ───────────────────────────────────────────
    async def wake(
        self,
        effort_id: str,
        role: str,
        thread_id: str,
        channel_id: str,
        *,
        session_id: str | None = None,
        instruction: str = "",
        repo: str | None = None,
        repo_token: str | None = None,
        upstream: str | None = None,
        upstream_token: str | None = None,
        recurse_submodules: bool = False,
    ) -> WorkResult | None:
        """Wake a worker on an effort and post its reply in-thread. Returns None if the
        effort is frozen (the composition rule refuses to dispatch) or no capacity. If `repo`
        is given, the worker is focused on it first (clone via /project, using `repo_token` if the
        project has a per-project deploy token); if omitted, the worker is assumed already focused.
        `upstream` (a fork's parent) is re-baked as the read-only `upstream` remote on this focus.
        `recurse_submodules`: the focus clones the FULL nested submodule tree — a composition build
        needs it (the worker can't init submodules itself; the proxy denies it)."""
        session_id = session_id or thread_id
        # RELIABILITY: dispatch inside a bounded retry loop. If the acquired worker is wedged (409
        # busy) or unreachable, QUARANTINE it (so it stops being picked) and RE-DISPATCH on another
        # worker — a stuck daemon no longer traps the effort in an infinite 409-retry (the idle-GPU
        # bug). Re-dispatch requires a `repo` to re-clone on the fresh worker; a repo-less follow-up
        # (publish) can't be moved, so it quarantines + raises (the caller's verify/re-engage handles
        # it). When every worker is quarantined, acquire raises NoCapacity → the effort PARKS.
        attempts = 0
        while True:
            attempts += 1
            try:
                inst = await self.scheduler.acquire(effort_id, role, session_id)
            except FrozenEffortError:
                await self.audit.log(
                    "wake_refused_frozen", effort_id=effort_id, payload={"role": role}
                )
                return None
            except NoCapacityError:
                # No free worker slot — DON'T dead-end ("couldn't dispatch"). Propagate so the
                # orchestrator PARKS the effort and auto-runs it when a worker frees (no silent idle).
                await self.audit.log(
                    "wake_queued", effort_id=effort_id, payload={"role": role}
                )
                raise

            quarantined = False
            try:
                if repo:
                    ok, detail, upstream_ok = await self.harness.set_project(
                        inst.base_url, repo, token=repo_token,
                        upstream=upstream, upstream_token=upstream_token,
                        recurse_submodules=recurse_submodules,
                    )
                    await self.audit.log(
                        "worker_project_set", effort_id=effort_id, actor=inst.id,
                        payload={"repo": repo, "ok": ok, "upstream": upstream,
                                 "upstream_ok": upstream_ok, "detail": detail},
                    )
                    if ok and upstream and upstream_ok is False:
                        # Clone worked but the fork's read-only `upstream` remote didn't bake.
                        # FIRST let the recovery hook try to prove the CONFIG is wrong (repo isn't
                        # actually a fork of that upstream) and heal the registry — else fall back
                        # to the honest warning (a genuinely private/unreachable parent). NON-FATAL
                        # either way (origin work still runs); surfaced in-thread rather than let
                        # the effort discover it mid-task (observability = safety).
                        healed = None
                        if self.on_upstream_fail is not None:
                            try:
                                healed = await self.on_upstream_fail(repo, upstream)
                            except Exception as exc:  # noqa: BLE001 — recovery must never break dispatch
                                log.debug("upstream-fail hook errored for %s: %s", repo, exc)
                        await self.chat.post(
                            channel_id,
                            healed or (
                                f"⚠️ Cloned `{repo}`, but I couldn't set up its `upstream` remote "
                                f"(`{upstream}`) — the parent looks **private or unreachable**. Fork "
                                f"sync (`git fetch upstream`) won't work until that's fixed (a "
                                f"read-scoped token for the parent, or a correct URL). Proceeding on "
                                f"`origin` only."
                            ),
                            thread_id=thread_id,
                        )
                    if not ok:
                        # A CLONE failure — not a worker failure. Name the real problem + how to fix
                        # it, so it never reads as a phantom "worker responded". exit 128 =
                        # private/missing repo the deploy token can't reach — EXCEPT a workspace
                        # collision ("destination path … already exists"), which is a dispatch race
                        # (another effort holds this worker's checkout), not repo/auth at all (live
                        # 2026-07-05: it was reported as "private or missing", pointing the operator
                        # at tokens instead of the scheduler).
                        is_collision = "already exists" in (detail or "").lower()
                        is_auth = not is_collision and (
                            ("128" in detail) or ("authentication" in detail.lower()) or not detail
                        )
                        hint = (
                            "the worker's workspace is **busy with another effort's checkout** (a "
                            "dispatch collision — not a repo or token problem); say _\"get the "
                            "workers working\"_ again in a bit and it will land on a free worker"
                            if is_collision else
                            "that repo looks **private or missing** and the deploy token can't reach "
                            "it — check the URL, or set a token with `/project add <name> <repo> "
                            "<TOKEN_ENV>`" if is_auth else "see the error above"
                        )
                        shown = f"`{detail}` — " if detail else ""
                        await self.chat.post(
                            channel_id,
                            f"⚠️ I couldn't clone `{repo}` — {shown}{hint}. No worker was dispatched "
                            f"(nothing ran; this wasn't a worker failure).",
                            thread_id=thread_id,
                        )
                        return WorkResult("clone_failed", task_id="", output=f"clone failed: {detail}")
                # Stream the worker's activity to the effort THREAD as it happens (observability =
                # safety, governance §5/§7). Notification discipline (CM.6): coalesce rapid *successful*
                # commands into one post; failures/denials always post immediately + in context so a
                # problem is never buried under a batch.
                batch_n = max(1, self.s.activity_batch)
                buf: list[str] = []

                async def _flush() -> None:
                    if buf:
                        await self.chat.post(channel_id, "\n".join(buf), thread_id=thread_id)
                        buf.clear()

                async def _stream(kind: str, item: dict) -> None:
                    if kind == "command":
                        cmd = (item.get("command") or "").strip()
                        if not cmd:
                            return
                        ok = bool(item.get("ok"))
                        icon = "🚫" if item.get("denied") else ("✅" if ok else "❌")
                        line = f"{icon} `$ {cmd[:200]}`"
                        self._record_activity(effort_id, f"{icon} {cmd[:120]}")  # PO visibility
                        if not ok:  # failure/denial — flush the batch, then surface with context
                            tail = (item.get("stderr_tail") or "").strip()
                            if tail:
                                line += f"\n> {tail[:300]}"
                            await _flush()
                            await self.chat.post(channel_id, line, thread_id=thread_id)
                            return
                        buf.append(line)
                        if len(buf) >= batch_n:
                            await _flush()
                    elif kind == "answer":
                        await _flush()
                        ans = (item.get("answer") or "").strip()
                        if ans:
                            self._record_activity(effort_id, f"💬 {ans[:120]}")
                            # The answer IS the deliverable for investigation tasks — never chop it
                            # at an arbitrary cap (live: a structure diagram was cut mid-tree).
                            # Chunk long answers into thread replies (Mattermost caps ~16k/post);
                            # only truly enormous output is truncated, and SAYS so.
                            chunks = [ans[i:i + 3500] for i in range(0, min(len(ans), 14000), 3500)]
                            for ci, chunk in enumerate(chunks):
                                head = (f"💬 **{role}@{inst.id}:** " if ci == 0
                                        else f"_(…continued {ci + 1}/{len(chunks)})_\n")
                                tail_note = ("\n\n_(answer truncated at 14k chars)_"
                                             if ci == len(chunks) - 1 and len(ans) > 14000 else "")
                                await self.chat.post(
                                    channel_id, f"{head}{chunk}{tail_note}", thread_id=thread_id,
                                )

                context = await self.build_context(effort_id, role)
                prompt = f"{context}\n\n{instruction}".strip()
                # `channel` here is little-coder's trigger-surface enum, NOT the Mattermost
                # channel — the harness defaults it to "batch" (automated trigger).
                result = await self.harness.wake(
                    inst.base_url, session_id, prompt, on_update=_stream
                )
                await _flush()  # defensive: surface any tail commands if no answer callback fired
                # POLL TIMEOUT = the bridge stopped waiting, but the daemon is STILL running the
                # turn — cancel it, or the orphaned task keeps the worker busy and the next
                # dispatch 409s (live 2026-07-08: both burn-down part turns outlived the window
                # and would have zombie-blocked round 2). Best-effort; the turn is abandoned.
                if (result.status == "error" and "poll timeout" in (result.output or "")
                        and getattr(result, "task_id", None)):
                    try:
                        await self.harness.cancel_task(inst.base_url, result.task_id)
                        await self.audit.log("task_cancelled_timeout", effort_id=effort_id,
                                             actor=inst.id, payload={"task": result.task_id})
                    except Exception as exc:  # noqa: BLE001 — cancel is best-effort
                        log.debug("timeout-cancel failed for %s: %s", result.task_id, exc)
                await self.audit.log(
                    "wake_done",
                    effort_id=effort_id,
                    actor=inst.id,
                    payload={"status": result.status, "role": role},
                )
                # A finished effort wakes its dependency waiters (idle-wait DAG).
                await self.scheduler.wake_finished(effort_id)
                return result
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if not _is_worker_unavailable(exc):
                    raise  # a real error (repo/task/other) — not a worker-health problem
                await self.scheduler.quarantine(
                    inst.id, seconds=self.s.worker_quarantine_seconds,
                    reason=f"dispatch failed: {exc}",
                )
                quarantined = True   # quarantine already reset its slot — don't also release
                can_retry = bool(repo) and attempts < self.s.worker_dispatch_max_attempts
                reason = (f"HTTP {exc.response.status_code}"
                          if isinstance(exc, httpx.HTTPStatusError) else "unreachable")
                await self.audit.log(
                    "worker_dispatch_failed", effort_id=effort_id, actor=inst.id,
                    payload={"attempt": attempts, "will_retry": can_retry,
                             "reason": reason, "error": str(exc)[:160]},
                )
                await self.chat.post(
                    channel_id,
                    f"⚠️ worker `{inst.id}` was unresponsive/stuck ({reason}) — "
                    + ("handing this to another worker (re-cloning there)…"
                       if can_retry else
                       "couldn't hand it off automatically (no repo focus to re-clone); raising it."),
                    thread_id=thread_id,
                )
                if can_retry:
                    continue   # re-acquire — the quarantined worker is now excluded
                raise
            finally:
                if not quarantined:
                    await self.scheduler.release(inst.id)

    # ── project survey — read-only Stage-1 anchor for the readiness gate (P3.8) ─
    async def survey_project(self, repo: str) -> str:
        """Run a ONE-TIME read-only survey of `repo` on a pooled worker and return its factual
        summary (languages/structure/conventions). Best-effort: returns "" if no repo, no free
        slot, or the survey fails — the caller degrades to conventions-only anchoring. Respects the
        concurrency semaphore via a reserved survey effort so it yields to real work."""
        if not repo:
            return ""
        await self.gate.ensure_effort(SURVEY_EFFORT, "project survey")
        try:
            inst = await self.scheduler.acquire(SURVEY_EFFORT, "worker-default", SURVEY_EFFORT)
        except (FrozenEffortError, NoCapacityError):
            return ""
        try:
            ok, _detail, _upstream_ok = await self.harness.set_project(inst.base_url, repo)
            if not ok:  # a non-empty tuple is always truthy — unpack ok explicitly (was a latent bug)
                return ""
            result = await self.harness.wake(
                inst.base_url, f"survey-{slugify(repo)}", _SURVEY_PROMPT
            )
            summary = (result.output or "").strip() if result else ""
            await self.audit.log(
                "project_survey", actor=inst.id,
                payload={"repo": repo, "ok": bool(result and result.ok), "len": len(summary)},
            )
            return summary if (result and result.ok) else ""
        except Exception as exc:  # noqa: BLE001 - survey is advisory; never block intake
            log.warning("project survey failed for %s: %s", repo, exc)
            return ""
        finally:
            await self.scheduler.release(inst.id)

    # ── operator-plane composition (autonomous-project-lifecycle P-APL.1b) ──────
    async def compose_submodules(
        self, engine_url: str, submodules: list[tuple[str, str]], *, token: str | None = None,
    ) -> tuple[bool, str, list[str]]:
        """Focus a pooled worker on the composition repo `engine_url` (cloned with the short-lived App
        `token` in origin for the push) and add each `(url, path)` in `submodules` as a git submodule
        via the daemon's operator-plane git (P-APL.1b). Returns (ok, detail, added_paths). This is a
        GOVERNED capability call — it only runs after the operator cleared the hard-gate."""
        await self.gate.ensure_effort(CAPABILITY_EFFORT, "capability: compose")
        try:
            inst = await self.scheduler.acquire(CAPABILITY_EFFORT, "capability", CAPABILITY_EFFORT)
        except (FrozenEffortError, NoCapacityError):
            return False, "no free worker to run the composition — try again shortly", []
        added: list[str] = []
        try:
            # fresh=True: force a clean re-clone of the TRUE remote — a cached workspace can be stale
            # (repo recreated at the same URL) and would push an unrelated history (exit 128).
            ok, detail, _ = await self.harness.set_project(
                inst.base_url, engine_url, token=token, fresh=True)
            if not ok:
                return False, f"couldn't focus the composition repo: {detail}", []
            for url, path in submodules:
                sub_ok, sub_detail = await self.harness.add_submodule(inst.base_url, url, path)
                await self.audit.log(
                    "compose_submodule", actor=inst.id,
                    payload={"engine": engine_url, "path": path, "ok": sub_ok},
                )
                if not sub_ok:
                    return False, f"added {added or 'none'}, then `{path}` failed: {sub_detail}", added
                added.append(path)
            return True, "", added
        except Exception as exc:  # noqa: BLE001 - surface a clear failure, never a silent stall
            log.warning("compose_submodules failed for %s: %s", engine_url, exc)
            return False, f"composition error: {exc}", added
        finally:
            await self.scheduler.release(inst.id)

    # ── deterministic verification exec (2026-07-08) ─────────────────────────
    async def exec_check(
        self, effort_id: str, *, command: str, session_id: str, repo: str | None = None,
        repo_token: str | None = None, recurse_submodules: bool = False, timeout: int = 900,
    ) -> tuple[int | None, str, bool]:
        """Run ONE verification command on a pooled worker DETERMINISTICALLY (the daemon's
        `/check` — real exit code + output, no model in the loop). Build verification is a
        machine step: the LLM 'verifier' burned its turn re-running builds and never reported
        (live 2026-07-08). Acquires a slot, optionally focuses `repo` (privileged recursive
        clone for compositions), runs, releases. Exceptions propagate — the orchestrator falls
        back to the LLM verifier (covers an old daemon image without `/check`)."""
        inst = await self.scheduler.acquire(effort_id, "verifier", session_id)
        try:
            if repo:
                ok, detail, _ = await self.harness.set_project(
                    inst.base_url, repo, token=repo_token,
                    recurse_submodules=recurse_submodules)
                await self.audit.log(
                    "worker_project_set", effort_id=effort_id, actor=inst.id,
                    payload={"repo": repo, "ok": ok, "detail": detail, "verify": True})
                if not ok:
                    raise RuntimeError(f"verification focus failed: {detail[:200]}")
            exit_code, output, timed_out = await self.harness.run_check(
                inst.base_url, command, timeout=timeout)
            await self.audit.log(
                "check_exec", effort_id=effort_id, actor=inst.id,
                payload={"command": command[:200], "exit_code": exit_code,
                         "timed_out": timed_out})
            return exit_code, output, timed_out
        finally:
            await self.scheduler.release(inst.id)

    # ── A->B hand-off provenance (P5.4) ──────────────────────────────────────
    async def last_owner(self, path: str, workspace: str = "/workspace") -> str | None:
        """Resolve who last touched a path — git-blame/last-commit (v1, OD-4).
        Returns a committer identity string, or None if unresolved. Best-effort:
        runs git in the workspace; the ownership-ledger upgrade is v1.5."""
        import asyncio

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", workspace, "log", "-1", "--format=%an", "--", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            owner = out.decode().strip()
            return owner or None
        except Exception as exc:  # noqa: BLE001
            log.warning("last_owner(%s) failed: %s", path, exc)
            return None

    # ── wake-storm rate cap (P5.6/§5) — brake channel is EXEMPT ───────────────
    async def record_wake(
        self, effort_id: str, target: str | None, kind: str = "work"
    ) -> None:
        async with self.db.session_factory() as s:
            s.add(WakeLog(effort_id=effort_id, target=target, kind=kind, delivered=True))
            await s.commit()

    async def wake_storm_tripped(self, effort_id: str) -> bool:
        """True if WORK chatter exceeded the cap in the window. Brake wakes are NEVER
        counted (sacred/exempt — minimizing the brake channel IS the paper's F3)."""
        since = (
            datetime.now(timezone.utc) - timedelta(seconds=self.s.wake_storm_window_s)
        ).isoformat()
        async with self.db.session_factory() as s:
            n = int(
                (
                    await s.execute(
                        select(func.count()).where(
                            WakeLog.effort_id == effort_id,
                            WakeLog.kind == "work",
                            WakeLog.ts >= since,
                        )
                    )
                ).scalar_one()
            )
        return n > self.s.wake_storm_max

    async def note_undeliverable(self, effort_id: str, target: str | None) -> Trigger:
        """A wake that can't be delivered past the bound is a §3 trigger, not a silent
        stall (PLAN §3.1.1). Returns the trigger for the caller to freeze on."""
        async with self.db.session_factory() as s:
            s.add(
                WakeLog(effort_id=effort_id, target=target, kind="work", delivered=False)
            )
            await s.commit()
        await self.audit.log(
            "wake_undeliverable", effort_id=effort_id, payload={"target": target}
        )
        return Trigger.undeliverable_wake
