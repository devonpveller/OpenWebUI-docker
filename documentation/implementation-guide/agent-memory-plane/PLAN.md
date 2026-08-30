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

## §1.1 exposure — IMPLEMENTED 2026-08-29

Was absent entirely when this file was written; now enforced and proven at three layers.

- `exposure` (`ops` / `personal`) in `agent_memories.metadata`, **mirrored onto the linked
  thought** so the generic thoughts lane can enforce the same boundary. Precisely: the
  mirror is the *label*, and the enforcement is the door's forced `metadata_filter`
  (`search_thoughts` applies `metadata @> $::jsonb`; it has no exposure logic of its own,
  `index.ts:497`). Corrected 2026-08-30 — this line previously read as if the tool enforced
  it, and the U5 drill's red phase showed otherwise by allowing the tool and observing that
  the forced filter, not the tool, is what held.
- `stampExposure()` is **demotion-only** — no argument widens. Taint (reported by the
  calling runtime) and detected PII both demote; **PII never rejects**.
- Stamped **at doors**: the caller's value is dropped, not merged. The internal lane is
  wired to `ops` per §1.1's door table; reads are forced by the door too.
- Proven: 87 unit tests; the filter executed against the real schema (including that a
  pre-exposure row reads as `personal`, not NULL); and end to end through the doors.

## U5 — personal-plane exclusion, drilled adversarially (2026-08-30)

`scripts/checks/drill-personal-plane-exclusion.ps1` is the executable form of the
dark-factory-unification U5 gate ("mechanically stopped **and** the attempt is visible in an
audit record"). It plants a **synthetic** personal-plane record on a throwaway database,
attacks it, then removes each guard in turn and requires the fixture to leak, so no green
result is taken on trust.

**What it attacks, stated exactly** — an earlier version of this paragraph said "all three
positions an agent actually occupies", which was wider than the evidence and a verifier
refuted it. The lanes are now named rather than characterised, with who actually calls each:

| Lane | Built from | Configured callers today |
|---|---|---|
| internal REST (`/agent-memory/*`, `x-brain-key`) | this tree | OB1 containers, agent-bridge |
| **ops door** — env DERIVED from compose's `openbrain-ops-gateway` | this tree | `scripts/claude-sessions-bridge/memory_writer.py` (imported at `bridge.py:1770`; both its feature flags default OFF) and `scripts/agent-harness/durable_checks.py`. **Both WRITE only** — no configured caller reads through this door. |
| **cloud door** — env DERIVED from compose's `openbrain-gateway` | this tree | `.mcp.json` → `127.0.0.1:8061`: every Claude Code / cloud agent in this workspace |

Two corrections are folded into that table, and both are the same lesson. The verifier's
"no client anywhere is configured to use the ops door" is FALSE — `git cat-file -e
70230c9:scripts/claude-sessions-bridge/memory_writer.py` succeeds, so that caller was on the
branch when it was refuted. Its *substance* was right anyway, and is what drove the work: the
door whose READ tools were leaking has no reader, while the door every agent demonstrably
holds open had no coverage at all. And `.mcp.json` is **gitignored**, so it is absent from
every worktree — a grep run inside one finds nothing and concludes nothing. That is the
CLAUDE.md "verify against gitignored evidence" rule collecting its toll in both directions.

All four read tools the ops door's `GATEWAY_READ_TOOLS` names are attacked, and the drill
FAILS if compose ever lists one it has no attack for — deriving the allow-list and then
exercising one of it read as coverage while providing none, which is how
`agent_memory_inspect` and `agent_memory_list_review_queue` shipped leaking. The cloud
door's exclusion of agent-memory content (no `share:'cloud'` label on the mirrored thought)
was asserted only in a code comment; it is now an executable check with a red phase that
adds the label and requires the fixture to come back.

NOT attacked, and not claimed: the RUNNING containers. The drill builds `:drill-<runid>`
images and never touches `:local`, `openbrain-db` or an `ai-stack_*` network. Whether
production runs this tree is the deploy gate's question, not this drill's.

The visibility half was the gap it closed: a refused recall now writes
`agent_memory_audit_events(event_type='recall_requested')` carrying `requested_exposure` /
`enforced_exposure` / `exposure_override_denied`; a tool denied at a door writes a
`tool_denied` audit line instead of vanishing into a `-32601`; and a targeted read that is
refused — `agent_memory_inspect` by id, or an off-plane item withheld from
`agent_memory_recall_trace` — writes an `access_refused` row naming the tool.

Also corrected here: §1 locks `review_status='pending'` and this repo had shipped
`evidence_only`, which removed the review gate. See DECISIONS.md.

## Still not implemented

**`promote_exposure`** — §1.1 makes human review the only elevation path, as a review
action beside `restrict_scope`. The schema's `agent_memory_review_actions` CHECK lists nine
actions and `promote_exposure` is not among them, so adding it is an additive migration
through the standing two-place mechanism. Until it exists, a demoted memory can never be
elevated — the conservative direction, but not the designed one.

**The rest of Phase 1.2** — five of the seven MCP tools, the third REST twin, zod
validation, and review coverage of all nine actions writing `agent_memory_review_actions`.

**Phase 1.4** — the ops door as a second `openbrain-gateway` instance.
