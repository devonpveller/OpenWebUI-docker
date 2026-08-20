# TASKS — Phase 3: Job-scoped spend attribution

> Status: BUILD SPEC 2026-08-05 — not started. Design-of-record:
> `PLAN-supervised-research-pipeline.md` §"Phase 3". Anchors verified against
> `OB1/integrations/research-service/index.ts` + `init-research-jobs.sql` on
> 2026-08-05.

## Goal

A real per-run token-cost line in each trace, **and** fleet-level spend
attributable to a specific research job — without regressing llm-queue lane
behavior, and fixing the `agent-org` origin drop.

## Two complementary mechanisms (both needed; they answer different questions)

1. **In-process usage accumulation → `result.tokens`** (the per-run trace cost the
   plan asked for). Every `chat()` OpenAI response carries a `usage` block; today
   `chat()` (`index.ts:184-195`) returns only the content string
   (`harness.ts` `.trim()`s it). Accumulate prompt+completion tokens per job and
   write `result.tokens` / `metrics.tokens`. **Self-contained — no cross-DB join.**
   (`research_jobs` is in openbrain-db; `LiteLLM_SpendLogs` is in the *separate*
   llm-gateway-db, so an in-process counter is the clean source for the trace.)
2. **`user`-field stamping → LiteLLM `end_user` attribution** (fleet analytics +
   llm-queue lane). Stamp the OpenAI `user` body field (`index.ts:193` already
   forwards it) with a job-scoped value so the llm-gateway-ui dashboard can
   attribute spend to a job, and the llm-queue lane classifier keeps working.

## The lane-preservation trick (this is the whole subtlety)

llm-queue classifies by **substring** of the `user` value against class patterns
(`llm-queue/src/llm_queue/policy.py::_DEFAULT_CLASSES`). Today only
`origin:"notebook"` sets `user="ob-research"` (`index.ts:48-49`); all other origins
send `user=""` and fall to the default lane — deliberately, so interactive OWUI
research is not shoved into the 30-min batch lane.

Preserve that exactly by choosing job-user **prefixes that only collide with the
intended lane**:

| origin | job user value | matches lane pattern? |
|---|---|---|
| notebook | `ob-research:job-<id>` | **yes** → `ob-research` rank-3 batch (intended) |
| owui | `owui-research:job-<id>` | no (`owui-chat` ≠ `owui-research`) → default lane (as today) |
| agent / manual | `agent-research:job-<id>` / `manual-research:job-<id>` | no → default lane (as today) |
| agent-org-advisory | `agent-research:job-<id>` | no → default lane |

Net: only `ob-research` intentionally hits a slow lane; everything else keeps
today's default-lane treatment, **and** every origin now carries a `:job-<id>`
suffix for spend attribution. (Verify against the live `policy.py` pattern list at
build time — a new pattern could change a collision.)

## Tasks

- **T1 — `laneUserFor(origin, jobId)` (`index.ts`).** `${LANE_CLASS[origin] ??
  "research"}:job-${jobId}` with the `LANE_CLASS` map above. Replaces the
  `QUEUE_USER_BY_ORIGIN` lookup (`index.ts:48-49,400`).
- **T2 — wrap `chat` with the job user (`index.ts:401-403`).** `jobDeps.chat =
  (s,u,o) => chat(s,u,o, laneUserFor(origin, jobId))` — now **all** origins get a
  user value (attribution) with lane behavior preserved by prefix.
- **T3 — usage accumulation.** Extend `chat()` to read `data.usage` and invoke an
  optional `onUsage(promptTok, completionTok)` passed via `opts`; accumulate per
  job in `executeJob` scope; write `result.tokens = {prompt, completion, total}`
  (`index.ts:417-423`) and `metrics.tokens`.
- **T4 — origin-coercion fix (the `agent-org` drop).** The DB CHECK allows only
  `owui/agent/notebook/manual` (`init-research-jobs.sql:30-31`) and the handler
  coerces unknown → `owui` (`index.ts:620`), so `agent-org-advisory` collapses to
  `owui`. Fix = **additive migration** extending the CHECK (drop + re-add
  constraint — allowed; no DROP TABLE/TRUNCATE) to include `agent-org-advisory`,
  `agent-org-grounding`; extend the handler allowed-list; add `LANE_CLASS`
  entries. Operator-applied like other `init-*.sql`.
- **T5 — join documentation.** Document the logical join: llm-gateway-db
  `LiteLLM_SpendLogs.end_user LIKE '%:job-<id>'` ⇄ openbrain-db
  `research_jobs.id`. Optionally a small reconciliation query for the server-status
  LiteLLM panel (cross-DB, so a query pair, not a SQL JOIN).
- **T6 — tests.** `laneUserFor` prefixes; `ob-research:job-x` still substring-hits
  the batch lane while `owui-research:job-x` does not; CHECK accepts the new
  origins; usage accumulation sums across multiple `chat()` calls; a `notebook` job
  lands in `ob-research` exactly as before.

## Deploy ladder (built image + ONE additive migration)

Unlike Phases 1–2, T4 needs a schema migration:
1. `deno check`/`lint`/`test`.
2. Apply the additive CHECK migration to **openbrain-db** (operator, like other
   `init-*.sql`) — must land **before** the new-origin code path submits.
3. `docker compose build openbrain-research` → `up -d openbrain-research`.
4. Verify: submit an `agent-org-advisory` job → row persists with that origin (not
   coerced) → `result.tokens` populated → llm-gateway-ui shows the `:job-<id>`
   end_user. Confirm an interactive `owui` job did **not** change lanes.
No stack-map/emergency-recovery change (no container/network/port change).

## Relation to the parked keys plan (compose, do not wait)

- Per-service **keys** plan = caller *identity* ("who") — when it unparks, the
  self-asserted `user` becomes trustworthy; this phase's `:job-<id>` suffix rides
  underneath unchanged.
- This phase is **local-only**. Cloud routing (OpenRouter) is deferred out of this
  effort; if a route is ever added, the `:job-<id>` attribution extends to it with
  no rework because LiteLLM logs every backend. Do THIS phase now regardless.
