# Agent-memory plane — THE PLAN IS NOT HERE

> **Canonical plan:**
> `d:\Open WebUI\documentation-plans-ai-stack\implementation-guide\agent-memory-plane\PLAN.md`
> — 493 lines, in the sibling private plans repo, outside this repo root.
>
> That document is authoritative for phases, gates, and the operator-decided
> invariants. **This file is not a plan and must never become one** — two plans for one
> effort diverge, and this file already caused that once.

## Why this file exists at all, and what it got wrong

An earlier version of this file was a *reconstruction*, written on the finding that the
memory-plane plan "does not exist — nowhere in this repository's history and not on disk".
The first half was right and the second half was wrong. I searched this repo and two
guessed paths (`D:/Open WebUI/agent-memory-plane`, `D:/agent-memory-plane`); I did not
search the sibling plans repo, where it has been the whole time.

So the reconstruction invented gates for phases whose real gates were written down, and
declared "anything the original required that is not here is lost" — about a document that
was never lost. The honest flag was right; the search behind it was not. That is the same
error class as asserting a commit SHA from memory: a claim about what exists, made without
looking hard enough to earn it.

The reconstruction is retired. What follows is the only thing this file should carry: a
pointer, and the evidence trail for what was actually validated **in this repo**.

## What was validated here, against the canonical gates

Reconciled 2026-08-29 against the canonical PLAN.md §Phase 1 (L186–L277). Divergences are
stated in `../dark-factory-unification/DECISIONS.md`; the short version:

| Gate | State | Evidence in this repo |
|---|---|---|
| **1.1** schema lands via the two-place mechanism + promotion runbook | **MET**, one deliberate deviation | `OB1/docker/init-agent-memory.sql`; [PROMOTION-RUNBOOK.md](PROMOTION-RUNBOOK.md); `scripts/checks/test-quartz4-offline.ps1` proves fresh-apply (8 tables, trigger, 2 functions). Deviation: the chain is fixed-width `010`–`120`, not `99-init-agent-memory.sql` — a `99a`-style suffix sorts *before* `99-` under bash+UTF-8 collation, so the plan's "next free prefix" would not have run where it read as running. |
| **1.2** server code: 7 MCP tools, 3 REST twins, zod, 9 review actions | **PARTIAL — 2 of 7 tools** | Built: `agent_memory_writeback`, `agent_memory_recall` + their REST twins. Missing: `report_usage`, `review`, `list_review_queue`, `inspect`, `recall_trace`, and `POST /agent-memory/usage`. No zod validation. Review logic covers **4** of the schema's **9** actions and writes only `agent_memory_audit_events` — never `agent_memory_review_actions`, the table that exists for it. |
| **1.3** offline harness + smoke script + plane-agreement invariant | **PARTIAL** | Done, and done first as the plan required: the plane-agreement invariant test. Harness and smoke script green. Missing: conservative-recall-returns-nothing-pending, `include_unconfirmed` returning it *and creating a trace*, usage report, `evidence_only` review action, and the **cloud-gateway negative test** (`agent_memory_*` denied via :8061; cloud `search_thoughts` must not surface agent-memory thoughts). |
| **1.4** the ops door | **NOT MET — built the wrong thing, reverted** | See DECISIONS.md. The canonical door is a second `openbrain-gateway` instance on `obnet` with its own `OPS_GATEWAY_KEY`, enforcing the `exposure` model. What was built was a bespoke Deno server on its own network with **no authentication at all**. Reverted unbuilt. |

## The invariant this repo has not implemented at all

Canonical PLAN.md §1.1, **DECIDED by the operator 2026-08-25**: *a record's maximum
exposure equals the access plane of the context that wrote it.* Every agent memory carries
`exposure` (`ops` / `personal`) in `agent_memories.metadata` with a mirrored label on the
linked thought; lanes are stamped **at doors, not by writers**; taint propagates; PII
heuristics *demote, never bless*; human review is the only elevation path.

None of that exists in the code here. The writeback path has no `exposure` field, no PII
heuristic, and no taint input. Recorded so the gap is visible from inside this repo, where
someone reading only the merged code would have no way to know it was ever required.
