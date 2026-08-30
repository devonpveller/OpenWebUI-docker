# Agent-memory plane — implementation plan

> Status: **RECONSTRUCTED 2026-08-29.** Phases 0–1.3 are DONE; 1.4 onward are open.
>
> **This document is not the original.** The dark-factory-unification plan's U1 row
> validates against "the memory-plane plan's own per-phase gates (already written,
> file/line-grounded)". That document was never committed — it does not appear anywhere in
> this repository's history, and it is not on disk. Phases 1.1, 1.2 and 1.3 were executed
> against a working memory of it, which means U1's stated validation source did not exist
> while U1 was being validated.
>
> This file replaces it, built from evidence that DOES exist: the merged commits, the
> promotion runbook beside it, the findings sinks, and the schema itself. Where a completed
> phase's gate is recorded below, it is **what was actually validated**, cited to the
> artifact that proves it — not a guess at what the original asked for. Where a future
> phase's gate is stated, it is being set now, before implementation, per design constraint
> A.4.
>
> Anything the original required that is not here is lost. That is a real gap and it is
> named rather than papered over.

Companion: [PROMOTION-RUNBOOK.md](PROMOTION-RUNBOOK.md) — how the schema is applied and
verified against a live database.

---

## The invariant the whole plane exists to hold

**A memory's write exposure never exceeds its writer's read plane**, and **nothing an agent
writes unprompted is instruction-grade**. Everything below is in service of those two.

The failure mode they guard is silent: the write side and the read side each look correct
alone and disagree in combination, so a memory is written, nothing errors, and the default
recall never returns it. The plane reports healthy and holds nothing retrievable. Two
instances were found locally and reproduced against a real database — `review_status`
defaulting to `pending` against a gate that admits only `confirmed`/`evidence_only`, and
`visibility: 'project'` with a NULL `project_id` against a project-scoped recall.

---

## Phase 0 — provenance: the org's audit events actually reach Open Brain — **DONE**

Prerequisite, not part of the schema. The memory plane is worthless if the events that
feed it are silently dropped.

| | |
|---|---|
| **Gate** | An `Event.mirrored` flag flips only on genuine success — HTTP 200 **and** no JSON-RPC error **and** no `result.isError`. Covered by tests naming the exact wire lines. |
| **Evidence** | `agent-org/agent-bridge/app/modules/openbrain_client.py` (owns the wire protocol); `audit_sink._mirror` delegates to it. DECISIONS.md, four entries under "U1 Phase 0". |
| **Found here** | The tool argument is `metadata_extra`, not `metadata`. `x-brain-key` was correct and the anchor was wrong about it. The mirror had been **off in production** while I asserted from code defaults that it was on — 26 events lost provenance before the operator authorised the credential fix and the backfill. |

## Phase 1.1 — the schema reaches a fresh volume — **DONE**

| | |
|---|---|
| **Gate** | On a throwaway pgvector volume running the **real** initdb chain: 8 sidecar tables, 1 trigger, 2 functions present — asserted **by query**, never by a clean exit code. |
| **Evidence** | `OB1/docker/init-agent-memory.sql`; merge `a9febd8`; `PROMOTION-RUNBOOK.md`; `scripts/checks/test-quartz4-offline.ps1` prints `agent_memory_tables(8)\|8`. |
| **Found here** | The harness was testing a **hardcoded** 13-file chain while compose mounted 20 — it had been proving "fresh apply works" for a chain that was not the real one. The chain is now derived from compose (`scripts/checks/lib/ob-initdb.ps1`), integrity checked both directions, and `docker-compose.preview.yml` is checked for drift after it fell eight migrations behind. |

## Phase 1.2 — the server code: policy, writeback, recall — **DONE**

| | |
|---|---|
| **Gate** | The plane-agreement invariant is a **test**, not a comment: a default writeback is admitted by the default recall, composed over every discriminating column — and the test is proven able to FAIL (forcing the column default must break it). Recall is parameterised, pins `lifecycle_status='active'`, and excludes `personal` by default. |
| **Evidence** | `agent-memory-policy.ts`, `agent-memory.ts` and their suites (42 tests); merges `578a81c`, `920e8a2`, `9267f42`, `9f56cfb`, `22cd9a0`, `062a3d7`. |
| **Found here** | Three live defects an adversarial reviewer caught: an insert into a `detail` column that **does not exist** (schema has `payload`) — every writeback would have failed *after* committing two rows; the three writes not being one transaction; and idempotency keyed **globally** rather than per workspace, so a second tenant was handed the first tenant's memory and told `duplicate: true` while its own was never written. |

## Phase 1.3 — the doors are called, not just the logic — **DONE**

| | |
|---|---|
| **Gate** | The real server, started in a container against a real database, answers over HTTP: bad key → 401; good key → the writeback contract (**the success is the reachability proof** — the MCP catch-all answers 401 too, so only a successful call distinguishes the two route orderings); the row and its audit event present in one transaction; idempotent retry; the cross-tenant case; 422 + named reason with **nothing written**; 400 on malformed JSON; and the plane-agreement invariant end to end through two doors and a database. |
| **Evidence** | `scripts/checks/smoke-agent-memory.ps1` — ALL CHECKS PASSED. Closes writeback-findings F2 and F3. |
| **Found here** | On PS 5.1 an HTTP error body lives in `$_.ErrorDetails.Message`, not in the response stream — every refusal assertion would have compared against `$null` and **failed a correct server**. |

---

## Phase 1.4 — the review door — **OPEN, next**

**The gap, verified before this was written:** there is no `UPDATE agent_memories` and no
`SET review_status` anywhere in the codebase. Every memory is written `evidence_only` and
stays there for ever. `pending` can never become `confirmed`; nothing can be `rejected`,
`superseded` or `merged`. The schema defines the whole lifecycle — and reserves a
`memory_confirmed` audit event type (`init-agent-memory.sql:232`) — that nothing can emit.

So the review gate the recall path enforces is currently a gate onto a room with no other
door. That is safe (the conservative direction) but it means the plane can never accumulate
anything a human has actually vouched for.

**Why a separate door, on loopback.** Promotion is the one operation that changes what a
memory is allowed to be used for. It must not live on the surface agents already speak to:
an agent that can reach the promote route can vouch for its own memory, and the only thing
standing between it and instruction-grade material would be a key check on a server it is
already authenticated to. A distinct process bound to `127.0.0.1` is not reachable from the
agent plane at all — a routing mistake cannot expose it, which is a stronger property than
a correct authorisation check.

| | |
|---|---|
| **Gate** (set before implementation, A.4) | 1. Promote/reject/supersede each write an audit event naming the actor, in the same transaction as the state change. 2. The door is **not reachable from the agent plane** — proven by attempting it from a container on the agent network and failing to connect, not by reading a bind address. 3. A promoted memory becomes visible to a default recall, and a rejected one is returned by **no** recall path, both proven through the doors. 4. Instruction-grade remains unmintable from the agent side: promotion may raise `review_status`, and the schema CHECK still refuses `can_use_as_instruction` for anything not user-confirmed or imported. 5. The full `documentation/runbooks/SERVICE-LIFECYCLE.md` checklist if this ships as a new container. |
| **Depends on** | 1.3 (the smoke harness is the pattern its tests extend). |

## Phase 2 — write paths — **OPEN**

Agents actually writing memories at the seams, rather than the door existing unused.

| | |
|---|---|
| **Gate** (to be refined when 1.4 lands) | A real run produces memories through the door with correct provenance, and the count is asserted against what the run should have produced — not merely "greater than zero". A run that should produce none must produce none. |
| **Depends on** | 1.4 |

## Phase 3 — recall-informed briefs at the four seams — **OPEN**

Named by the unification plan's U6 row.

| | |
|---|---|
| **Gate** (to be refined) | At each seam, a brief demonstrably contains recalled material that changed it — shown by a differential run (same seam, recall disabled vs enabled), not by the presence of a recall call. |
| **Depends on** | Phase 2 |

---

## Standing rules for this plane

- **Never assert a memory is retrievable because a write returned success.** Every gate
  above that could be satisfied by a stub is instead satisfied by a query or an HTTP call.
- **The findings sinks are part of this plan**, not a graveyard:
  `documentation/notes/agent-memory-policy-findings.md` and
  `documentation/notes/agent-memory-writeback-findings.md`. An entry is removed when it
  lands, and closed entries say where.
- **Test images tag `:smoke` or `:wt-<id>`, never `:local`** — that is the production tag,
  and this plane has already had one accident there (writeback-findings F1).
