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

    # ── the map (P1.1) ───────────────────────────────────────────────────────
    async def ensure_effort_channel(self, name: str) -> tuple[str, str]:
        """Create-or-get an #effort-<name> channel + its Effort (P5.5). Returns
        (effort_id, channel_id)."""
        channel_id = await self.chat.ensure_channel(f"effort-{name}")
        effort_id = f"effort-{name}"
        await self.gate.ensure_effort(effort_id, name, channel_id=channel_id)
        return effort_id, channel_id

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

    async def resolve_effort_by_channel(self, channel_id: str) -> str | None:
        async with self.db.session_factory() as s:
            e = (
                await s.execute(select(Effort).where(Effort.channel_id == channel_id))
            ).scalar_one_or_none()
            return e.id if e else None

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
            # Stream the worker's activity to the bus as it happens (observability = safety,
            # governance §5/§7) — the user + PM can watch the work + see failures live.
            async def _stream(kind: str, item: dict) -> None:
                if kind == "command":
                    cmd = (item.get("command") or "").strip()
                    if not cmd:
                        return
                    icon = "🚫" if item.get("denied") else ("✅" if item.get("ok") else "❌")
                    line = f"{icon} `$ {cmd[:200]}`"
                    tail = (item.get("stderr_tail") or "").strip()
                    if not item.get("ok") and tail:
                        line += f"\n> {tail[:300]}"
                    await self.chat.post(channel_id, line, thread_id=thread_id)
                elif kind == "answer":
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
