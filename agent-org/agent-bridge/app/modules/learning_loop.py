"""learning-loop — suggestion pool + pattern surfacing + propose-not-dispose (§6, P6.3-P6.5).

The temporal dimension: the org learns from its own alignment hits/misses so recurring
patterns drive systemic change. All communication outcomes are signal; ONE incident is
noise, a PATTERN is a mandate.

The critical boundary (P6.5): the loop PROPOSES changes; it NEVER auto-applies them. A
self-modifying ruleset is the slow-motion version of F3. So: pattern detection + suggestion
pool -> PM synthesizes a *proposed* change -> the Human Operator approves -> it lands via the
versioned floor/steering update (charters.set_floor, which itself requires human approval).

This extends the existing little-coder `meta` propose-not-dispose loop rather than running a
parallel one (TOOLING §2) — the bridge records/surfaces; hardening lands through charters.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select

from ..db import Database, now_iso
from ..models import Pattern, Suggestion
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.learning")


def _signature(text: str) -> str:
    """Coarse grouping key so repeats across efforts collapse to one pattern."""
    norm = " ".join(text.lower().split())[:120]
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


class LearningLoop:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    # ── suggestion pool (P6.3) ───────────────────────────────────────────────
    async def add_suggestion(self, worker: str, text: str, effort_id: str | None = None) -> str:
        sig = _signature(text)
        async with self.db.session_factory() as s:
            s.add(
                Suggestion(worker=worker, text=text, effort_id=effort_id, signature=sig)
            )
            await s.commit()
        await self.audit.log(
            "suggestion", effort_id=effort_id, actor=worker, payload={"signature": sig}
        )
        return sig

    async def pool(self) -> list[dict]:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(Suggestion).where(Suggestion.status == "open"))
            ).scalars().all()
        return [{"id": r.id, "worker": r.worker, "text": r.text, "signature": r.signature} for r in rows]

    # ── pattern surfacing (P6.4) ─────────────────────────────────────────────
    async def observe(self, signature: str, effort_id: str, text: str) -> Pattern | None:
        """Record an occurrence; surface a candidate when it recurs across >=2 efforts."""
        async with self.db.session_factory() as s:
            pat = (
                await s.execute(select(Pattern).where(Pattern.signature == signature))
            ).scalar_one_or_none()
            if pat is None:
                pat = Pattern(signature=signature, count=1, effort_ids=[effort_id])
                s.add(pat)
            else:
                pat.count += 1
                if effort_id not in (pat.effort_ids or []):
                    pat.effort_ids = list(pat.effort_ids or []) + [effort_id]
                pat.last_seen = now_iso()
            await s.commit()
            await s.refresh(pat)
            surfaced = len(pat.effort_ids or []) >= 2 and pat.status == "observed"
            pat_id = pat.id
        if surfaced:
            await self.audit.log(
                "pattern_surfaced",
                payload={"signature": signature, "efforts": (pat.effort_ids or [])},
            )
            return pat
        return None

    # ── propose-not-dispose (P6.5) ────────────────────────────────────────────
    async def propose(self, signature: str, proposal: str, *, by: str = "pm") -> None:
        """PM synthesizes a PROPOSED change. It does NOT auto-apply — the Human Operator
        must approve, and hardening lands only via charters.set_floor (human-gated)."""
        async with self.db.session_factory() as s:
            pat = (
                await s.execute(select(Pattern).where(Pattern.signature == signature))
            ).scalar_one_or_none()
            if pat is None:
                raise KeyError(signature)
            pat.proposal = proposal
            pat.status = "proposed"
            await s.commit()
        await self.audit.log(
            "pattern_proposed", actor=by, payload={"signature": signature, "proposal": proposal}
        )

    async def dispose(self, signature: str, *, approve: bool, actor: str = "human") -> bool:
        """Human Operator disposes. Approval here only records the decision; the actual
        floor/steering change must go through charters (human-gated version bump). There
        is NO auto-apply path from a pattern to the floor."""
        if actor != "human":
            raise PermissionError("only the Human Operator disposes (§6 propose-not-dispose)")
        async with self.db.session_factory() as s:
            pat = (
                await s.execute(select(Pattern).where(Pattern.signature == signature))
            ).scalar_one_or_none()
            if pat is None:
                raise KeyError(signature)
            pat.status = "approved" if approve else "rejected"
            await s.commit()
        await self.audit.log(
            "pattern_disposed", actor=actor, payload={"signature": signature, "approved": approve}
        )
        return approve
