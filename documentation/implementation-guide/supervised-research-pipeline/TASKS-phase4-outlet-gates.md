# TASKS — Phase 4: Gate consequential outlets (not research)

> Status: BUILD SPEC 2026-08-05 — not started. Design-of-record:
> `PLAN-supervised-research-pipeline.md` §"Phase 4". This phase is mostly
> agent-org-side wiring of **existing** gate primitives; it adds little new code.
> One operator policy decision is flagged below.

## Principle (non-negotiable)

The engine stays autonomous. Research is read-only in effect (it produces claims +
briefs); gating the loop kills the flywheel. The human gate belongs at
**consequential use** — where research output *steers an action* — matching the
stack's own Idea Refinery stance ("the worth call stays with the human").

## Reuse — build no new gate machinery

All in `agent-org/agent-bridge/app/modules/`:
- **approvals MCP** (`scripts/claude-sessions-bridge/approval_server.py`) —
  in-thread Mattermost approve/deny, **fail-closed**, JSONL audit, `follow_thread`
  auto-wake.
- **`pending_store.py`** — durable pending-approval mirror (survives bridge restart).
- **`governance_gate.py`** — freeze/escalation FSM (no timeout auto-resume,
  authority separation, default-deny).
- **`execution_gate.py`** — mandatory dry-run for risky actions (already gates code
  execution).

## The two places a gate fires

**Trigger A — research output about to drive a consequential downstream action.**
Not every outlet is consequential; map them explicitly:

| Outlet | Consequential? | Gate |
|---|---|---|
| Advisory answer posted in-thread (`grounding.py::advise`) | No — informational | none (as today) |
| Daily digest / podcast brief | No — informational | none (as today) |
| Research grounding a plan's assumptions feeding code exec (`grounding.py::ground` → `execution_gate`) | Yes | **already gated** by the existing dry-run `execution_gate` — no new gate |
| Idea Refinery **PROMOTE** → agent-org `/nl` (spawns an effort) | Yes | **formalize** through `pending_store` (today it's user-chosen but not a durable, audited approval) |

**Trigger B — a chained research job requesting scope outside its contract**
(Phase 1 tie-in). When an auto-chained dive/refinery job's `resolveContract` would
need to **widen** its parent's contract (a domain outside the parent allow-list, a
bigger budget, dropping a red line), raise a `pending_store` approval + post via
approvals MCP. **Fail-closed:** no approval ⇒ run under the narrower parent
contract, never auto-widen.

## Tasks

- **T1 — outlet policy (confirm the table above).** Encode "consequential outlet"
  as a small explicit list, not a heuristic. *(Operator decision — see below.)*
- **T2 — formalize Idea Refinery PROMOTE through `pending_store`.** The PROMOTE →
  `/nl` step becomes a durable `PendingApproval` (survives restart, audited),
  resolved by the operator's in-thread `approve <id>`. Reuses the existing store;
  no new mechanism.
- **T3 — scope-expansion gate (Trigger B).** In the chaining path (gap-dive /
  refinery drain), when a child contract would widen the parent, call
  `pending_store.propose` + approvals-MCP post; on deny/timeout run the narrower
  parent contract. Depends on **Phase 1** (contracts) existing.
- **T4 — confirm no double-gating.** `ground()` feeding `execution_gate` is already
  gated; assert Phase 4 does **not** add a second gate there (the dry-run gate is
  the gate). Test that an advisory-only answer is never gated.
- **T5 — tests.** PROMOTE raises a durable pending that survives a simulated
  restart; scope-widen chained job blocks fail-closed and falls back to the parent
  contract; advisory/digest outlets post ungated; deny path is audited.

## Operator decision (the one real choice here)

**Which outlets are "consequential"?** The T1 table is my recommended default:
gate only (a) Idea Refinery PROMOTE and (b) contract-scope widening; leave
advisory answers, digests, and podcasts ungated (informational), and rely on the
*existing* `execution_gate` for research-fed code execution. Confirm or adjust
before T2/T3 — this is a policy call, not a technical one, and it sets how much
friction the human sees.

## Deploy ladder (agent-bridge — Python, not the research image)

Changes land in `agent-org/agent-bridge` (recreate the bridge), not
`openbrain-research`. No schema change (reuses `pending_store`'s table). No
stack-map change. Verify: a PROMOTE surfaces a pending in-thread and survives a
bridge bounce; a chained dive requesting a denied domain runs narrow.

## Dependencies

Trigger A (T2) is independent. Trigger B (T3) needs **Phase 1** contracts. Nothing
here depends on Phase 2 or 3.
