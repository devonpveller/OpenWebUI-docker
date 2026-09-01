# Agent memory: an intent axis, not a role axis — proposal

**Status: PROPOSAL, not built. Parked deliberately** — the agent-memory plane is
under active work by another session (OB1 `8108e07` and the queue's `memplane1`
lineage). This is written to be picked up *after* that work lands, so it does not
collide with it. Nothing here has been implemented.

Operator framing (2026-08-30), which this plan takes as the north star:

> "Better access is project, topic, intent based rather than limited to a role
> that may not be the same later down the road. History is useful for project
> history, but a challenge overcome is useful from an intent perspective — an
> intent-focused semantic would return when looking for past solutions to a
> previously resolved problem. Intent is abstract and could widen possible
> overlapping solutions from adjacent domains. This is where expertise comes from
> and is important to nurture."

## What is already true (verified, 2026-08-30)

Good news first, because it narrows the work considerably:

- **Recall is already semantic.** `performRecall` embeds the query and orders by
  `1 - (t.embedding <=> $1::vector)` over the linked thought's embedding, blended
  with recency (`OB1/integrations/kubernetes-deployment/agent-memory.ts`).
- **Recall is already NOT role-bound.** The gate
  (`isRowRecallableBy` / `buildRecallScopeFilter`) discriminates on exactly:
  `workspace_id`, optional `project_id`, `visibility`, `exposure`,
  `lifecycle_status`, `review_status`. `created_by`, `runtime_name`, `task_id`
  and `flow_id` are recorded as provenance and **never consulted**. There is no
  "same agent to read it back" constraint to remove — it was never there.
- **The project axis exists and is populated** (`project_id = 'ai-stack'`).
- A channel axis exists (`channel_kind` / `channel_id` / `channel_thread_id`) and
  is unused.

## The three real gaps

### 1. `content` is the entire retrieval surface

The embedding is computed on **`row.content` only** (`agent-memory.ts:252`).
`summary` is not embedded and therefore not searchable.

This is the highest-leverage finding in the whole proposal: an intent-shaped
query can only match a memory whose `content` *states the problem*. A writeback
that records what was done, without saying in general terms what problem it
solved, is unreachable by "how have we handled X before" — however good the
solution was. That is a writeback-contract change, not a migration.

### 2. There is no topic or intent axis at all

`memory_type` is the nearest thing and it is a **form** taxonomy, not an intent
one:

    decision | output | lesson | constraint | open_question
    failure  | artifact_reference | work_log | check

`decision` says what shape the record is. It says nothing about *what it was for*.
And `memory_type` is not even filterable — `RecallScope` has no field for it, so
it is returned in results and never narrows them.

### 3. `project_id` scoping is the thing that blocks adjacency

A project-scoped recall (`am.project_id = $n`) is the correct default for
"what happened on this project". It is also exactly what prevents the operator's
stated goal: a solved problem in one project surfacing for an analogous problem in
another. Adjacency needs an axis that is *not* the project.

## Proposal

**A. Make the problem statement part of `content` (cheap, do first).**
Change the writeback contract so a memory carries an abstract statement of the
problem alongside the concrete solution — the shape the operator described:
"a challenge overcome". Because `content` is what gets embedded, this alone makes
intent-shaped recall work with **no schema change**. It is mechanically checkable
the same way the plane-agreement invariant is: a test that a writeback missing a
problem statement is refused, and a test that an intent-shaped query returns a
memory written from a differently-worded problem.

**B. Add an `intent` axis as a first-class, *narrowing* field.**
A short abstract phrase ("a check that passes while checking nothing",
"a fix that lived only in the deployed container"), stored in its own column and
embedded separately from `content`. Two properties matter:

- it must be **abstract** — naming the problem class, not the incident. This is
  what lets an adjacent-domain match happen at all.
- recall must be able to search intent **without** a project filter, while the
  default project-scoped recall stays the default. Adjacency is an opt-in widening,
  not a change to the safe default.

**C. Keep `memory_type` as form; do not overload it.**
The temptation will be to add `intent` as another `memory_type` value. That
conflates "what shape is this record" with "what was it for", and the CHECK
constraint would then be doing two jobs badly.

## Constraints this must not break

- **§1.1 access bounds writes.** An intent axis is metadata about the *problem*,
  not about the data. It must not become a way to widen `exposure` or
  `visibility`, and it must not be settable by the writer in a way that changes
  which plane the memory lands on.
- **The review door.** Everything an agent writes unprompted is `pending` and sits
  outside the default recall gate by design. Cross-project intent recall makes the
  review backlog matter *more*, not less — a memory nobody reviewed reaching a
  different project is a wider blast radius than it reaching its own.
- **Do not fix this by widening the default recall scope.** The failure mode the
  plan already documents twice (the `review_status` trap, then the `project_id`
  trap) is a write side and a read side that each look right alone. Any new axis
  needs the same both-sides-composed test.

## Known contamination to resolve alongside it

The entity extractor currently reads agent-memory `content` and mints entities
from it — on 2026-08-30 it turned the worktree id `wt-tester-3` into a `person`
with a wiki page. Four agent-memory thoughts produced eleven entities. Whatever
shape the intent axis takes, harness scaffolding must stop becoming people and
tools in the knowledge graph: either scope the agent-memory plane out of entity
extraction, or teach the extractor to skip harness identifiers.

## What is NOT proposed

- No change to `created_by` / `runtime_name` / `task_id` / `flow_id`. They are
  provenance and are already correctly excluded from the gate.
- No removal of `project_id` scoping. The default stays project-scoped.
- No mass backfill of existing memories. There are four.
