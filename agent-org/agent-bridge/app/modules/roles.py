"""roles — two-tier spawn authority (governance §4.1, P5.2/P5.3).

  - The PM may instantiate MORE workers within an already-approved role/domain freely
    (just parallelism).
  - Introducing a NEW role/domain type is an org-structure change -> Human-Operator-gated
    (PO proposes): it changes the decomposition surface + incentive mix, so it goes through
    the §3 escalation gate for Human-Operator sign-off, with the PO's proposed charter + scope.

Every new role inherits the charter + hard rules unchanged; same aligned baseline always
(F6); new role = new handoff seam = new constraint contract (F5).
"""

from __future__ import annotations

import logging

from ..schemas import Concern, Level, Trigger
from .governance_gate import GovernanceGate
from .scope_ledger import ScopeLedger

log = logging.getLogger("agent_bridge.roles")


class RoleAuthorityError(Exception):
    pass


class RoleAuthority:
    def __init__(self, gate: GovernanceGate, scope: ScopeLedger) -> None:
        self.gate = gate
        self.scope = scope

    async def spawn_instance(self, role: str, *, by: str = "pm") -> None:
        """PM instantiates another instance of an APPROVED role. Denied if the role type
        is not pre-cleared — that path must go through introduce_role_type()."""
        if by != "pm":
            raise RoleAuthorityError("only the PM instantiates approved-role instances (§4.1)")
        if not await self.scope.is_role_approved(role):
            raise RoleAuthorityError(
                f"role type {role!r} is not approved — introducing a new role TYPE is "
                f"Human-Operator-gated (PO proposes); use introduce_role_type() (§4.1)"
            )
        log.info("PM spawned another instance of approved role %s", role)

    async def introduce_role_type(
        self, role: str, charter_ref: str, effort_id: str, *, proposed_by: str = "po"
    ) -> Concern:
        """A NEW role type: freeze the effort + raise a hard-gate CONCERN for Human-Operator
        sign-off (the PO proposes the charter + scope). The role is NOT usable until the
        human clears it via the gate; on clear the orchestrator calls approve_role_type()."""
        concern = Concern(
            intent_thread=f"introduce new role type {role}",
            what_surfaced=f"a new role/domain type '{role}' is proposed (org-structure change)",
            intent_of_change=(
                "adding a new kind of agent changes the decomposition surface and incentive "
                "mix (governance F1/F6) — it must be Human-Operator-approved, not PM-authored"
            ),
            options=[],
            pm_recommendation=f"approve role '{role}' with charter {charter_ref}",
            blocked_efforts=[effort_id],
        )
        # A new role type is org-structure — treat as a hard-gate trigger (human clears).
        await self.gate.freeze(
            effort_id, Trigger.deviation, concern, actor=proposed_by, level=Level.hard_gate
        )
        return concern

    async def approve_role_type(self, role: str, charter_ref: str, *, actor: str = "human") -> None:
        """Called after the human clears the introduce-role-type CONCERN. Adds the role to
        the approved catalog so the PM may now spawn instances of it."""
        if actor != "human":
            raise RoleAuthorityError("only the Human Operator approves a new role type (§4.1)")
        await self.scope.catalog_add(role, charter_ref, approved=True)
        log.info("Human approved new role type %s", role)
