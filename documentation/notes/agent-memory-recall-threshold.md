# Finding — the recall similarity threshold exists now, and is still uncalibrated (2026-08-30)

Memory-plane PLAN §3 warns:

> **Similarity threshold is NOT inherited**: upstream's 0.7 was tuned for
> text-embedding-3-small; bge-m3 has a different similarity distribution (related items
> often land 0.4–0.6). Calibrate against our corpus before enabling recall, or it returns
> nothing.

**Updated 2026-08-30 (U6 / memory-plane P3, branch `work/u6recall`).** The first version of
this note recorded that there was no threshold *at all* and that the two-phase recency blend
was deferred with it. Both have since been built. What has NOT changed is the reason the
number is blank, so the note stays open — the mechanism moved, the measurement did not.

## What is true now

**The floor exists, is named, and is unset.**
`AGENT_MEMORY_RECALL_MIN_SIMILARITY` (plus `AGENT_MEMORY_RECALL_RECENCY_WEIGHT` and
`AGENT_MEMORY_RECALL_HALF_LIFE_DAYS`) are read per call by `performRecall`
(`OB1/integrations/kubernetes-deployment/agent-memory-ranking.ts`), wired through
`OB1/docker/docker-compose.yml` on `openbrain-mcp`, and documented blank in
`OB1/docker/.env.example`. Blank means **no floor and pure similarity** — byte-for-byte the
ordering that shipped before the mechanism existed. Calibration is therefore a config change,
not a code change.

**It lives in the SQL, not in any client.** The floor is a predicate on the raw cosine in the
outer filter of the recall query, so every door gets it and no caller can opt out. A
per-caller floor would be a policy decision made in the wrong place — a second door could
simply not send it.

**Recall is two-phase.** Phase 1 is an index scan ordered by the raw distance operator only
(the ordering HNSW can serve), taking `limit x 4` candidates; phase 2 re-ranks that bounded
set in memory by `sim*(1-w) + exp(-age/half_life)*w` and slices to `limit`. Upstream's
`recency-boosted-match-thoughts` puts the blend in its `ORDER BY`, which seq-scans — we took
the formula and not the shape, as PLAN §6 says to.

**Nothing about live behaviour changed**, because both knobs default off and
`AO_MEMORY_RECALL_ENABLED` is still off. Checked in the live env 2026-08-30:
`agent-org/docker/.env` has `AO_MEMORY_WRITEBACK_ENABLED=true` and
`AO_MEMORY_RECALL_ENABLED=false` — which is exactly the ordering this note asks for. The
corpus is accruing right now; the read path is shut until there is enough of it to measure.

## Why it is still not calibrated

Calibration needs a corpus to calibrate against. There are currently **4** agent memories,
all on the `ops` plane and all `pending` (measured directly, 2026-08-30 16:5x UTC, after the
U6 live recall smoke had planted and removed its two synthetic fixtures). The count has moved
three times in a day - 2, then 3, then 4 (the fourth landed at 14:46) - which is itself the
argument: a distribution nobody can quote a stable size for is not one to fit a threshold to.

```sql
SELECT COALESCE(metadata->>'exposure','personal') AS exposure, review_status, count(*)
  FROM agent_memories GROUP BY 1,2;
-- ops | pending | 4
```

A threshold picked against four rows is a number with a story attached, not a measurement.
Copying upstream's 0.7 would make recall return nothing against bge-m3 — the failure mode
that looks exactly like success. Picking a low number because it makes a demo look good is
the same mistake facing the other way. So both stay blank, and the *risk while blank* is the
inverse of the plan's warning: with no floor, recall returns the top-K by distance whatever
their relevance, and the brief presents them to a worker as evidence.

## To close this

1. Let the write paths run until the corpus shows a distribution — Phase 2 writes one memory
   per finished effort and one per learned failure signature, so this accumulates on its own.
   `AO_MEMORY_WRITEBACK_ENABLED` is a separate flag from the recall one for exactly this:
   writes first, then enough data to measure, then a threshold, then reads.
2. Measure real query/memory pairs: for a sample of goals, record the cosine similarity of
   the memories a human judges relevant vs irrelevant. bge-m3 is expected to put related
   items at 0.4–0.6, so the floor is likely to sit well below upstream's 0.7 — but "likely"
   is the reason to measure rather than to pick. The recall trace already records the tuning
   each recall ran under, so the before/after of a change is answerable from the plane.
3. Set `AGENT_MEMORY_RECALL_MIN_SIMILARITY` in `OB1/docker/.env` and recreate
   `openbrain-mcp`. Consider the recency weight separately and later: it is a second
   uncalibrated number, and changing two at once makes neither measurable.
4. Then turn `AO_MEMORY_RECALL_ENABLED` on. §3's acceptance itself no longer waits on this:
   `scripts/checks/smoke-agent-memory-live.ps1` proved it on 2026-08-30 against the live plane
   with the floor UNSET - a confirmed synthetic memory reached a real worker brief and the
   pending one never did, with the fixtures removed afterwards. What calibration is still
   owed is the SEPARATION question the acceptance does not ask: with a real corpus, does the
   floor keep out what a human would call irrelevant? Four rows cannot answer that.

## Related, and easy to conflate

**A badly-chosen recall QUERY is not repaired by any threshold** — it is repaired by asking a
better question. Fixed in the same branch: every seam now embeds its own cleanest text — the
request as asked (intake), the step, the goal plus THIS round's error slice (burn-down), the
handoff note plus the goal (resume), and the bug plus its debug log (a handoff FIX effort,
which never passes through intake and was embedding its own template) — instead of the
assembled brief, which had grown the org's standing preamble blocks: text identical on every
effort, and so the one text guaranteed not to discriminate between goals. A query also never
contains a block recall itself rendered, or it re-ranks its own previous answer. See
`documentation/notes/u6recall-findings.md`.
