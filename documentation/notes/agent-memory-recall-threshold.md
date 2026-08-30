# Finding — recall has no similarity threshold, and cannot be calibrated yet (2026-08-30)

Memory-plane PLAN §3 warns:

> **Similarity threshold is NOT inherited**: upstream's 0.7 was tuned for
> text-embedding-3-small; bge-m3 has a different similarity distribution (related items
> often land 0.4–0.6). Calibrate against our corpus before enabling recall, or it returns
> nothing.

## What is actually true here

**We never inherited it.** The recall SQL has no cutoff at all —
`agent-memory.ts` orders by `t.embedding <=> $1::vector` and `LIMIT`s, with no `WHERE` on
similarity. So the failure the plan warns about (an inherited 0.7 making recall return
nothing against bge-m3) cannot happen.

**The inverse risk is the live one.** With no cutoff, recall returns the top-K by distance
whatever their relevance. On a small corpus that means a brief gets memories that have
nothing to do with the goal — and the brief block states them as evidence the worker should
weigh. Irrelevant evidence presented as relevant is worse than no evidence.

## Why it is not calibrated

Calibration needs a corpus to calibrate against. There are currently **2** agent memories
(`SELECT count(*) FROM agent_memories`), one of which is this phase's own acceptance probe.
A threshold picked against two rows is a number with a story attached, not a measurement.

## What was done instead

`AO_MEMORY_RECALL_ENABLED` stays **off**, and it is a separate flag from
`AO_MEMORY_WRITEBACK_ENABLED` precisely so the write path can run and build the corpus while
the read path stays shut. That ordering is also what makes calibration possible later:
writes first, then enough data to measure, then a threshold, then reads.

## To close this

1. Let the write paths run until the corpus is large enough to show a distribution — Phase 2
   writes one memory per finished effort, so this accumulates on its own.
2. Measure real query/memory pairs: for a sample of goals, record the cosine similarity of
   the memories a human judges relevant vs irrelevant. bge-m3 is expected to put related
   items at 0.4–0.6, so the threshold is likely to sit well below upstream's 0.7 — but
   "likely" is the reason to measure rather than to pick.
3. Add the cutoff to `buildRecallScopeFilter`'s SQL, not to the client, so every caller gets
   it and no door can opt out.
4. Then turn `AO_MEMORY_RECALL_ENABLED` on, and check §3's acceptance: a confirmed memory
   measurably appears in a worker brief, and a pending one never does.

The two-phase recency blend §3 also describes (`sim*(1-w) + exp(-age/half_life)*w`, index
scan then re-rank) is deferred with this, for the same reason: it is a re-ranking of scores
whose scale has not been measured.
