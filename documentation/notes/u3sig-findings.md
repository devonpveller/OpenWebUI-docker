# Findings — U3: failure-signature write-through (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U3 · class 2 — the write-through claims LESS than §2.3's promotion
DECISION: `_record_constraint` writes the learned failure SIGNATURE through to the
          plane at LEARN time as `memory_type='failure'`, keyed
          `failure-sig-<signature>`. It does NOT write it as a `constraint`.
CITED:    §2's U3 row — "failure signatures→clauses write-through to the plane".
WHY IT DOES NOT UNDO §2.3: 2.3 promotes clauses only at GREEN CLOSE, because a
          clause from an effort that never converged "may just be what this
          attempt got wrong". Writing at learn time as a *constraint* would
          silently reverse that. This says "this failure was SEEN", which is
          exactly what a novelty test needs and asserts nothing about the clause
          being right.
WHY KEYED ON THE SIGNATURE: `EffortConstraint.signature` exists so "have we seen
          this failure before?" is a cheap set-membership test, and that set is
          currently PER EFFORT. Keying on the effort would produce one row per
          effort saying the same thing and make the test useless at the scale it
          starts mattering.
REVERT:   Remove the `_write_failure_signature` call from `_record_constraint`.
          The clause recording itself is untouched.

---

## F1 — only on `fresh`

`_record_constraint` is content-addressed and several red paths funnel the same
failure into it; a re-recorded clause is subsumed. The write-through sits inside
the `if fresh:` branch so a subsumed clause does not produce a second write
either. Without that it would be idempotent at the plane and still burn a request
per red path.

## F2 — the cross-effort test is NOT built

The signature now reaches the plane, so the data exists for "has any effort hit
this wall before?". Nothing ASKS that question yet — `_constraints_context` still
reads only the current effort's clauses.

**Not claimed as delivered.** Recorded so "signatures write through" is not read
as "novelty is cross-effort". Wiring the recall side is the follow-on, and it
needs the recall path enabled, which is blocked on the threshold calibration in
`documentation/notes/agent-memory-recall-threshold.md`.

## F3 — U3's Validated-by column is only partly satisfiable here

§2's column is a GYM run: "a seeded regression must be caught by a check born from
a *tester* finding in a prior round (gym-007's shape, new source); drills green in
both systems".

- **drills green in both systems** — satisfied: harness 66/66, agent-org 9/9.
- **the seeded-regression gym run** — NOT satisfied. It needs a gym cycle with a
  tester round, which is a runner-level activity belonging to U4's quadrants.

The mechanism is complete and proven at the unit and live-write level; the gym
demonstration is the part still owed, and it is owed to U4's infrastructure rather
than to anything missing here.
