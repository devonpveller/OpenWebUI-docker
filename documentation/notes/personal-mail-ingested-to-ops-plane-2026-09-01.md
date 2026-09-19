# INCIDENT: I ingested the operator's SENT/INBOX mail onto the ops plane

**2026-09-01, caused by the orchestrator. Contained, NOT reversed — the reversal is the
operator's call.** This is the §C.2 class-4 surface (the personal data plane), so the record is
written before any further action on it.

## What happened

Restoring the gmail outage, I triggered `openbrain-gmail-pull` three times over its HTTP `/run`
door with body overrides `{"window": …, "limit": …, "chain": …}`.

**`--labels` defaults to `SENT`.** The label targeting that selects the newsletter corpus is
`--labels-prefix=brain/`, and the daily cron passes it explicitly —
`OB1/docker/cron/crontab` sends `{"labelsPrefix":"brain/","window":"90d","limit":500}`.
My overrides omitted `labelsPrefix`, so all three runs fell back to the default and pulled the
operator's own sent and inbox mail instead of the `brain/*` newsletters.

The runs reported success, correctly — they did what they were told. The defect was in the
instruction, and nothing in the loop was positioned to notice that the *targeting* was wrong
rather than the *writing*.

## Scope, measured

| | |
|---|---|
| rows written | **1,129** chunk rows from **127 distinct emails** |
| window | `2026-09-01 10:25:13Z` → `11:07:41Z` |
| labels present | `SENT`, `INBOX`, `IMPORTANT`, `daily digest` |
| labels absent | **zero** rows carry any `brain/*` label |
| plane | `exposure='ops'` — the plane agents read |
| propagation | 1,129 rows queued for entity extraction; **632 `thought_entities` rows already derived** |

## Containment taken

`docker stop openbrain-entity-worker` (exited 143) to stop further derivation. Reversible with
`docker start`. The extraction queue is holding at 14,110 and **this leaves a production worker
down** — that is a cost of the containment, not a neutral state, and it should not sit
indefinitely.

## The reversal, NOT run

```sql
DELETE FROM thoughts
 WHERE metadata->>'source' = 'gmail'
   AND created_at > '2026-09-01 10:24:45+00'
   AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(metadata->'gmail_labels') l
                   WHERE l LIKE 'brain/%');
```

Scoped by creation time so it cannot reach pre-existing rows; the `brain/%` guard is
belt-and-braces since none of them carry one. **Before running it, the FK behaviour of
`thought_entities` and `entity_extraction_queue` must be READ, not assumed** — the 632 derived
rows either cascade or need sweeping in the same transaction, and "probably cascades" is not a
plan. The source mail is intact in Gmail, so the reversal restores the prior state exactly.

## Why this is written down rather than quietly fixed

Deleting 1,129 production rows on the personal-data surface is exactly the action §C.2 reserves
for the operator, and I created the situation — which is a reason for more caution, not less.

## The lesson, which is not "be careful"

**A door with a permissive default is a door that fails open.** `pull-gmail.ts` defaults
`--labels` to `SENT` — a *narrower-sounding* flag that is in fact the operator's private
correspondence — and the safe behaviour lives only in the caller. Every scheduled invocation
passes `labelsPrefix`; every ad-hoc one must remember to. The same class as this effort's other
findings: the guarantee rested on the caller remembering, and the moment a caller was written by
someone reasoning about a different problem (a backfill window), it was gone.

The narrow fix would be to make the recipe refuse an ingest that specifies no label prefix.
That is NOT built here — U8's scope is frozen and this is a note, not an item.
