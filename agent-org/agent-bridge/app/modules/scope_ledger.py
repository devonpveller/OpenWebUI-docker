"""scope-ledger — who-may-touch-what (governance §5, §4.1; P5.1/P5.2/P5.8).

Hard-rule #2: no self-granted scope. New scope/spawn comes ONLY from the PM, and
irreversible scope comes only from the Human Operator (PO proposes). Grants are logged;
scope is revoked on retirement (§4.1 lifecycle). A worker that requests its own scope is
DENIED and the attempt is logged.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import Database
from ..models import RoleCatalog, ScopeGrant
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.scope")

# Roles allowed to grant scope (hard-rule #2). A worker (subject) granting to itself
# is a self-grant => denied.
_GRANTORS = {"pm", "po", "human"}
# Only the human may grant irreversible/external scope.
_IRREVERSIBLE_RESOURCES = {"deploy", "push", "delete", "spend", "send-outside"}


class ScopeDenied(Exception):
    pass


class ScopeLedger:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    async def grant(
        self, subject: str, resource: str, *, granted_by: str, effort_id: str | None = None
    ) -> None:
        # Deny self-grant (hard-rule #2).
        if granted_by == subject or granted_by not in _GRANTORS:
            await self.audit.log(
                "scope_denied",
                effort_id=effort_id,
                actor=subject,
                payload={"resource": resource, "granted_by": granted_by, "reason": "self_or_unauthorized"},
            )
            raise ScopeDenied(
                f"{subject} cannot be granted {resource!r} by {granted_by!r} "
                f"(no self-granted scope — hard-rule #2)"
            )
        # Irreversible scope is human-only.
        if resource in _IRREVERSIBLE_RESOURCES and granted_by != "human":
            await self.audit.log(
                "scope_denied",
                effort_id=effort_id,
                actor=subject,
                payload={"resource": resource, "granted_by": granted_by, "reason": "irreversible_needs_human"},
            )
            raise ScopeDenied(
                f"{resource!r} is irreversible — only the Human Operator may grant it (PO proposes)"
            )
        async with self.db.session_factory() as s:
            s.add(
                ScopeGrant(
                    subject=subject,
                    resource=resource,
                    effort_id=effort_id,
                    granted_by=granted_by,
                    active=True,
                )
            )
            await s.commit()
        await self.audit.log(
            "scope_granted",
            effort_id=effort_id,
            actor=granted_by,
            payload={"subject": subject, "resource": resource},
        )

    async def authorized(self, subject: str, resource: str) -> bool:
        async with self.db.session_factory() as s:
            grants = (
                await s.execute(
                    select(ScopeGrant).where(
                        ScopeGrant.subject == subject, ScopeGrant.active.is_(True)
                    )
                )
            ).scalars().all()
        return any(
            g.resource == resource or fnmatch.fnmatch(resource, g.resource) for g in grants
        )

    async def revoke_subject(self, subject: str, *, actor: str = "pm") -> int:
        """Retirement (§4.1/P5.8): revoke ALL scope for a subject — no zombie authority."""
        now = datetime.now(timezone.utc).isoformat()
        async with self.db.session_factory() as s:
            grants = (
                await s.execute(
                    select(ScopeGrant).where(
                        ScopeGrant.subject == subject, ScopeGrant.active.is_(True)
                    )
                )
            ).scalars().all()
            n = 0
            for g in grants:
                g.active = False
                g.revoked_at = now
                g.revoked_by = actor
                n += 1
            await s.commit()
        await self.audit.log(
            "scope_revoked", actor=actor, payload={"subject": subject, "count": n}
        )
        return n

    # ── approved-role catalog (§4.1, P5.3) ──────────────────────────────────
    async def catalog_add(self, name: str, charter_ref: str, *, approved: bool) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(RoleCatalog, name)
            if row is None:
                s.add(RoleCatalog(name=name, charter_ref=charter_ref, approved=approved))
            else:
                row.approved = approved
                row.charter_ref = charter_ref
            await s.commit()

    async def is_role_approved(self, name: str) -> bool:
        async with self.db.session_factory() as s:
            row = await s.get(RoleCatalog, name)
            return bool(row and row.approved and not row.retired)

    async def retire_role(self, name: str, *, actor: str = "human") -> None:
        async with self.db.session_factory() as s:
            row = await s.get(RoleCatalog, name)
            if row is not None:
                row.retired = True
                await s.commit()
        await self.audit.log("role_retired", actor=actor, payload={"role": name})
