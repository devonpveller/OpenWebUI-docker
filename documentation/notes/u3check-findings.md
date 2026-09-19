# Findings — U3: `memory_type='check'` (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U3 · class 2 — `check` is a distinct memory_type, not a reused one
DECISION: Added `check` to the `agent_memories.memory_type` CHECK by additive
          migration (`init-agent-memory-check-type.sql`, chain slot 140, live
          volume applied). Not folded into `lesson` or `constraint`.
CITED:    §2's U3 row requires "harness findings write `memory_type='check'`".
          The type did not exist — the vendored schema permits eight values, and
          a writeback with 'check' is rejected by the CHECK at runtime and by
          NOTHING at test time when the pool is stubbed. U3 could not be
          implemented without it.
WHY DISTINCT: a check is not a thing learned and not a boundary on scope — it is
          an EXECUTABLE artifact that runs green or does not. A lesson is READ; a
          check is RUN. Recall, review and any later reflection pass want to treat
          them differently, and collapsing them makes that unrecoverable from the
          data.
REVERT:   Re-run the same ALTER with the original eight values. No row becomes
          invalid unless a 'check' memory has been written.

---

## F1 — the plan asked for a type the schema forbade

§2's U3 row is written as though `memory_type='check'` were available. It was not.
This is the second time a U-phase has required an additive migration the plan did
not name (the first was `promote_exposure` for §1.1's elevation path).

**Worth noting for U6/U7:** the plan's phase table describes intent, not the
schema's current vocabulary. A phase that names a value should be checked against
the CHECK constraints before it is estimated.

## F2 — both directions, on both volumes

The migration is proven four ways, because "the constraint accepts my new value"
and "the constraint still refuses everything else" are different claims and only
the pair means the CHECK was WIDENED rather than dropped:

- fresh volume, `check` accepted (offline harness, chain of 24)
- fresh volume, `not_a_real_type` still refused
- live volume, `check_allowed_after|1`
- live volume, `not_a_real_type` still refused

## F3 — nothing WRITES a check memory yet

The type exists and the plane accepts it. The harness does not yet write one: that
is the next U3 slice (tester finding → durable check → `memory_type='check'`
through the ops door). Recorded so nobody reads this as the pipeline being built.
