# `wiki_pages` carries no RLS — measured, NOT closed, and deliberately left parked

**2026-09-02.** Surfaced by clause 4's own probe during the DFU close-out. **Recorded as a
finding only. No migration was merged and nothing was deployed.**

## The measurement

`dfu-done.ps1` clause 4, live run at `4ca2e65` from a verified-complete clean clone:

```
[fail] service-rls-boundary (exit 1)
   1 of 13 stage table(s) are not relrowsecurity/relforcerowsecurity = t/t: wiki_pages/f/f
```

Confirmed independently, read-only, against production:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='wiki_pages';
-- wiki_pages | f | f
```

Every other table in clause 4's stage set reads `t/t`. `wiki_pages` is in that set because it
joins to the corpus — content derived from `thoughts` lands in a table with no row-level
boundary.

## Why it is NOT being closed

The operator ruled on 2026-09-01, in the class-1 regression brief:

> `wiki_pages` (extractLinks, since 08-28) is **PRE-EXISTING** and stays parked.

That ruling was written about the dead write path, and this is a different property of the same
table — but "the probe told me to" is not a grant, and the boundary the operator drew is around
**the table**, not around one of its defects. A round-3 agent built and validated an RLS
migration for it; **that work was blocked at verification by the safety classifier, correctly
citing this ruling, and it has not been merged.** The branch is bundled at
`D:\Open WebUI\_notes\parked-work\aistack-dfur3h-wikipages-rls.bundle`
(verified *"records a complete history"*), so nothing is lost if the park is lifted.

**Widening into a parked area because an adjacent check went red is scope creep with a
justification attached.** It is recorded here rather than done.

## What is and is not known about the exposure

**Known:** the table has no RLS, and it is derived from `thoughts`, which now carries 1,129
personal-exposure rows.

**NOT established, and it matters:** whether a personal-plane row can actually *reach*
`wiki_pages` today. Two facts cut against assuming a live leak —
1. `wiki_pages` writes have been **dead since 2026-08-28** (the parked `extractLinks`
   `ReferenceError`), so the current row set does not represent normal operation;
2. the derivation may filter by plane upstream of the write.

So this must not be reported as a live leak, and must not be dismissed as harmless. **The
question is about the WRITE PATH, not the current rows**, and it was not answered because
answering it fully meant building inside the park.

## What closing it would need, in order

1. The operator lifts the park on `wiki_pages` (or scopes the lift to the RLS question only).
2. Establish by construction whether a personal-derived row reaches the table — RED first.
3. Apply the migration in `init-graph-plane-rls.sql`'s shape (ENABLE + FORCE + a corpus-consistent
   predicate), honouring the two-place invariant: the initdb chain slot AND `PROMOTION-RUNBOOK.md`.
4. Prove the boundary is a boundary and not a blackout — an ops row must still be visible.

**Until then `service-rls-boundary` stays RED, and it is right to.** A probe that reports a
missing boundary as present would be the defect; a probe that reports it as missing, while the
operator has deliberately parked the table, is the system working.
