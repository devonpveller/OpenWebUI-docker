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
  thought** so `search_thoughts` enforces the same boundary.
- `stampExposure()` is **demotion-only** — no argument widens. Taint (reported by the
  calling runtime) and detected PII both demote; **PII never rejects**.
- Stamped **at doors**: the caller's value is dropped, not merged. The internal lane is
  wired to `ops` per §1.1's door table; reads are forced by the door too.
- Proven: 87 unit tests; the filter executed against the real schema (including that a
  pre-exposure row reads as `personal`, not NULL); and end to end through the doors.

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

## Phase 3 — governed recall into briefs, VALIDATED 2026-08-30 (U6, `work/u6recall`)

Reconciled against canonical PLAN.md §Phase 3. The helper and the four injection points
landed in `dbbffc8`; this pass verified them adversarially and fixed what the verification
found. Full write-up + the DECISIONS entries owed: `documentation/notes/u6recall-findings.md`.

| Gate (canonical §3) | State | Evidence in this repo |
|---|---|---|
| `_agent_memory_context` modelled on `_acceptance_corpus_context`: guard substring, `try/except → log.debug → ""`, never raises | **MET** | `orchestrator.py:5480`; fail-soft proven at the SEAM (`test_recall_seams.py::test_a_dead_plane_never_blocks_the_seam`), not only in the module |
| Self-bounded: limit 8, ~300 chars/item, ≤4000 total | **MET, after a correction** | The budget bounded the ITEM LINES only, so a full block measured **4312** chars against a stated 4000 (header + omitted-line marker sat outside it), and the cap test asserted `RECALL_BLOCK_MAX + 500` — a bound no document states. `RECALL_BODY_MAX` now subtracts both; `test_the_block_is_self_bounded` asserts the documented bound, and `test_every_item_line_is_bounded_including_its_policy_markers` asserts the per-item one (334 = clip + markers; a real line measured 329, and nothing asserted it before). |
| Injection at ALL FOUR seams, intake before `set_goal` | **MET, after two seams were found DEAD on the real path** | `orchestrator.py` 6082 / 7001 / 8754 / 12811, plus a fifth at `_open_handoff:12682`. Seams 3 and 4 both guarded on `"RELEVANT MEMORIES" not in <assembled text>`, and on the real path that text is the VERSIONED goal, which carries the block seam 1 deliberately put there — so both guards were always false and neither seam ever fired after a real intake. Both now re-query against their own new information and REPLACE the inherited block. Every seam has a fixture-backed test AND a recall-off control; deleting any seam turns its test red (`scripts/checks/recall-falsifiability-drill.py`). |
| Conservative recall only; `include_unconfirmed` never exposed | **MET** | `test_recall_NEVER_asks_for_unconfirmed_memories` (asserts the wire body, not the server default) |
| `agent_memory_report_usage` after injection | **MET, and corrected** | Usage now carries the `trace_id` that returned the memory, is `used=True` only for memories the brief actually showed, and is bounded — serial reporting added a measured 24s to a dispatch |
| Brief rendering ports Hermes `_format_recall_context` | **MET, and hardened** | Whitespace is collapsed before clipping: a multi-line summary previously forged a column-0 `STANDING INTENT:` line into the brief |
| Two-phase ranking (raw-distance scan, then blend re-rank), NOT upstream's execution shape | **MET, and now guarded** | OB1 `agent-memory-ranking.ts` + `performRecall`; 20 tests; the SELECT also executes against the real schema in `scripts/checks/test-quartz4-offline.ps1`. `RECALL_OVERFETCH = 1` collapses the two phases into one and left all 17 original tests green (each computed its expectation from that constant); two new tests fail at 1. The shape claim is **index-SERVABLE**, not "index scan": measured live, the statement plans as Nested Loop + Sort on a 4-row corpus and as `Index Scan using idx_thoughts_embedding ... Order By: (embedding <=> $1)` when the planner is forced off the sort. |
| Similarity threshold NOT inherited; calibrate before enabling | **PARKED, deliberately** | `AGENT_MEMORY_RECALL_MIN_SIMILARITY` / `_RECENCY_WEIGHT` are named, wired through compose, documented, and **unset** — the corpus is **4** ops memories, all pending. `documentation/notes/agent-memory-recall-threshold.md` holds the procedure. `AO_MEMORY_RECALL_ENABLED` stays off. |
| Acceptance: live smoke — a confirmed memory appears in a worker brief, a pending one never does | **MET 2026-08-30** | `scripts/checks/smoke-agent-memory-live.ps1` + `live_recall_probe.py`: `openbrain-mcp` rebuilt from OB1 `adb7345` and redeployed, two SYNTHETIC `ops` memories written through the LIVE writeback door, one moved `pending -> confirmed` through the live review tool, then ONE REAL EFFORT through `_intake_or_dispatch` with **no transport override**. The confirmed memory reached the worker brief and the versioned goal; the pending one reached neither. `agent_memory_recall_traces` went from **zero rows, ever** to a trace recording `{"examined": 1, "returned": 1}` at `candidates: 32`. Fixtures deleted; corpus back at 4; zero personal-plane rows before and after. |

**Note on the Phase 1 rows above: they are STALE.** They were written 2026-08-29 and record
five MCP tools and the third REST twin as missing. `agent-memory-tools.ts`,
`agent-memory-review.ts` and `agent-memory-ops.ts` now implement `report_usage`, `review`,
`list_review_queue`, `inspect` and `recall_trace`, `POST /agent-memory/usage` exists
(`agent-memory.ts:661`), and the review path writes ten actions including
`promote_exposure`. Those rows are not corrected here because this pass verified Phase 3,
not Phase 1 — a row rewritten from a grep is exactly the kind of unearned claim this file
exists to warn about.
