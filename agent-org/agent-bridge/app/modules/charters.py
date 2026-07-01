"""charters — rules-as-skills, floor/steering split, goal grounding (governance §4.2/§4.3).

Two-layer rule model (the key safety split, §4.2):
  - Floor: immutable at runtime (the hard rules). A floor change is a deliberate Human
    Operator act with a version bump + audit entry (P3.4). An in-flight steering update
    CANNOT weaken it.
  - Steering: mutable in flight (active constraints, scope, focus, direction). The PO/PM
    edit these; the change reaches the worker on its next turn / wake.

Goals carry their constraints INLINE (§4.3) — the single most important design principle:
a constraint inside the objective gets optimized; a constraint beside it gets dropped.

`build_context()` is what the router injects on wake: floor + current steering + the
effort's current goal (constraints inline) + the role charter. It also stamps which rule
version the worker is running (§4.2 propagation).
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import desc, select

from ..config import Settings
from ..db import Database
from ..models import GoalVersion, RuleVersion
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.charters")

FLOOR = "floor"
STEERING = "steering"


class FloorChangeDenied(Exception):
    """A floor edit without Human-Operator approval (P3.4 — the F3-erosion guard)."""


class Charters:
    def __init__(self, db: Database, settings: Settings, audit: AuditSink) -> None:
        self.db = db
        self.s = settings
        self.audit = audit

    # ── seed floor + charter skills from disk (P3.1) ─────────────────────────
    async def seed_floor_from_disk(self) -> None:
        """Load the non-overridable floor (hard-rules.md) as the initial floor rule."""
        floor_file = Path(self.s.floor_dir) / "hard-rules.md"
        if not floor_file.exists():
            log.warning("floor file %s missing — no floor seeded", floor_file)
            return
        content = floor_file.read_text(encoding="utf-8")
        async with self.db.session_factory() as s:
            existing = (
                await s.execute(
                    select(RuleVersion)
                    .where(RuleVersion.layer == FLOOR, RuleVersion.name == "hard-rules")
                    .order_by(desc(RuleVersion.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(
                    RuleVersion(
                        layer=FLOOR,
                        name="hard-rules",
                        version=1,
                        content=content,
                        approved_by="seed",
                    )
                )
                await s.commit()

    def charter_text(self, role: str) -> str:
        """Read a role charter file (system_prompt_ref target) — the role's charter."""
        f = Path(self.s.charters_dir) / f"{role}.md"
        if f.exists():
            return f.read_text(encoding="utf-8")
        return ""

    # ── floor (immutable at runtime — human-gated) ───────────────────────────
    async def current_floor(self) -> tuple[int, str]:
        async with self.db.session_factory() as s:
            row = (
                await s.execute(
                    select(RuleVersion)
                    .where(RuleVersion.layer == FLOOR, RuleVersion.name == "hard-rules")
                    .order_by(desc(RuleVersion.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
        return (row.version, row.content) if row else (0, "")

    async def set_floor(self, content: str, *, approved_by: str) -> int:
        """Change the floor — requires Human-Operator approval + a version bump (P3.4).
        This is the structural guard against the floor eroding over time (slow F3)."""
        if approved_by != "human":
            await self.audit.log(
                "floor_change_denied",
                actor=approved_by,
                payload={"reason": "requires_human_approval"},
            )
            raise FloorChangeDenied(
                "a floor change requires Human-Operator approval (governance §4.2 / P3.4)"
            )
        ver, _ = await self.current_floor()
        new_ver = ver + 1
        async with self.db.session_factory() as s:
            s.add(
                RuleVersion(
                    layer=FLOOR,
                    name="hard-rules",
                    version=new_ver,
                    content=content,
                    approved_by=approved_by,
                )
            )
            await s.commit()
        await self.audit.log(
            "floor_change", actor=approved_by, rule_version=new_ver, payload={"version": new_ver}
        )
        return new_ver

    # ── steering (mutable in flight) ─────────────────────────────────────────
    async def set_steering(
        self, effort_id: str, content: str, *, actor: str = "pm"
    ) -> int:
        async with self.db.session_factory() as s:
            last = (
                await s.execute(
                    select(RuleVersion)
                    .where(
                        RuleVersion.layer == STEERING,
                        RuleVersion.scope_effort_id == effort_id,
                    )
                    .order_by(desc(RuleVersion.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
            ver = (last.version if last else 0) + 1
            s.add(
                RuleVersion(
                    layer=STEERING,
                    name=f"steering:{effort_id}",
                    version=ver,
                    content=content,
                    scope_effort_id=effort_id,
                    approved_by=actor,
                )
            )
            await s.commit()
        await self.audit.log(
            "steering_change", effort_id=effort_id, actor=actor, payload={"version": ver}
        )
        return ver

    async def current_steering(self, effort_id: str) -> str:
        async with self.db.session_factory() as s:
            row = (
                await s.execute(
                    select(RuleVersion)
                    .where(
                        RuleVersion.layer == STEERING,
                        RuleVersion.scope_effort_id == effort_id,
                    )
                    .order_by(desc(RuleVersion.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
        return row.content if row else ""

    # ── goals (constraints inline — §4.3) ────────────────────────────────────
    async def set_goal(
        self,
        effort_id: str,
        objective: str,
        *,
        scope_slice: str | None = None,
        created_by: str = "pm",
        invalidates_in_progress: bool = False,
    ) -> int:
        """Set/adjust an effort's goal with constraints INLINE. Returns the new version.
        If it invalidates in-progress work, the caller must §3 freeze-and-surface (§4.3);
        adjusting the CANONICAL objective is a Human-Operator act (created_by='human')."""
        async with self.db.session_factory() as s:
            last = (
                await s.execute(
                    select(GoalVersion)
                    .where(GoalVersion.effort_id == effort_id)
                    .order_by(desc(GoalVersion.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
            ver = (last.version if last else 0) + 1
            s.add(
                GoalVersion(
                    effort_id=effort_id,
                    version=ver,
                    objective=objective,
                    scope_slice=scope_slice,
                    created_by=created_by,
                    invalidates_in_progress=invalidates_in_progress,
                )
            )
            await s.commit()
        await self.audit.log(
            "goal_change",
            effort_id=effort_id,
            actor=created_by,
            goal_version=ver,
            payload={"invalidates_in_progress": invalidates_in_progress},
        )
        return ver

    async def current_goal(self, effort_id: str) -> tuple[int, str, str]:
        async with self.db.session_factory() as s:
            row = (
                await s.execute(
                    select(GoalVersion)
                    .where(GoalVersion.effort_id == effort_id)
                    .order_by(desc(GoalVersion.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
        if not row:
            return (0, "", "")
        return (row.version, row.objective, row.scope_slice or "")

    # ── the wake context (§4.2/§4.3 delivery surface) ────────────────────────
    async def build_context(self, effort_id: str, role: str) -> str:
        """What the router injects on wake: floor + steering + goal (constraints inline)
        + role charter. Short, sharp, repeated (small models drift from long prompts)."""
        floor_ver, floor = await self.current_floor()
        steering = await self.current_steering(effort_id)
        goal_ver, objective, scope_slice = await self.current_goal(effort_id)
        charter = self.charter_text(role)
        parts = [
            f"# FLOOR (non-overridable — rule v{floor_ver})\n{floor}".rstrip(),
            f"# YOUR CHARTER ({role})\n{charter}".rstrip() if charter else "",
            f"# GOAL (v{goal_ver}) — constraints are part of the goal, optimize them\n{objective}".rstrip()
            if objective
            else "",
            f"## YOUR SCOPE SLICE\n{scope_slice}".rstrip() if scope_slice else "",
            f"# STEERING (current direction)\n{steering}".rstrip() if steering else "",
        ]
        return "\n\n".join(p for p in parts if p)
