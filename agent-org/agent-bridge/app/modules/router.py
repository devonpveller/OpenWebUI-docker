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
}


def slugify(name: str) -> str:
    """kebab-case slug for a project/effort name (channel-safe, matches the research's
    `[category]/[project]` convention)."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "untitled"

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
    ) -> WorkResult | None:
        """Wake a worker on an effort and post its reply in-thread. Returns None if the
        effort is frozen (the composition rule refuses to dispatch) or no capacity. If `repo`
        is given, the worker is focused on it first (clone via /project); if omitted, the
        worker is assumed already focused (pre-seeded/throwaway)."""
        session_id = session_id or thread_id
        try:
            inst = await self.scheduler.acquire(effort_id, role, session_id)
        except FrozenEffortError:
            await self.audit.log(
                "wake_refused_frozen", effort_id=effort_id, payload={"role": role}
            )
            return None
        except NoCapacityError:
            await self.audit.log(
                "wake_queued", effort_id=effort_id, payload={"role": role}
            )
            return None

        try:
            if repo:
                ok = await self.harness.set_project(inst.base_url, repo)
                await self.audit.log(
                    "worker_project_set", effort_id=effort_id, actor=inst.id,
                    payload={"repo": repo, "ok": ok},
                )
                if not ok:
                    await self.chat.post(
                        channel_id, f"⚠️ couldn't focus the worker on `{repo}`.",
                        thread_id=thread_id,
                    )
                    return WorkResult("error", task_id="", output="set_project failed")
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
                    if not ok:  # failure/denial — flush the batch, then surface this with context
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
                        await self.chat.post(
                            channel_id, f"💬 **{role}@{inst.id}:** {ans[:1500]}",
                            thread_id=thread_id,
                        )

            context = await self.build_context(effort_id, role)
            prompt = f"{context}\n\n{instruction}".strip()
            # `channel` here is little-coder's trigger-surface enum, NOT the Mattermost
            # channel — the harness defaults it to "batch" (automated trigger).
            result = await self.harness.wake(
                inst.base_url, session_id, prompt, on_update=_stream
            )
            await _flush()  # defensive: surface any tail commands if no answer callback fired
            await self.audit.log(
                "wake_done",
                effort_id=effort_id,
                actor=inst.id,
                payload={"status": result.status, "role": role},
            )
            # A finished effort wakes its dependency waiters (idle-wait DAG).
            await self.scheduler.wake_finished(effort_id)
            return result
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
