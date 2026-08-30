# Findings — U3: the agent-org org drill (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U3 · class 1 — the drill is pytest AND standalone
DECISION: `agent-org/agent-bridge/tests/test_org_drill.py` runs both under pytest
          and as `python tests/test_org_drill.py`, printing named checks and a
          count like `verify-merge-protocol.ps1`.
CITED:    §2's U3 row — "port the harness's drill PATTERN to agent-org as an
          executable org drill". The pattern is what ports; the language does not.
          A drill a person RUNS is different from a suite CI runs: the operator
          reads the check names when they want to know whether the org's laws
          still hold, and a pytest summary line does not tell them which law.
REVERT:   Delete the file. Nothing imports it.

---

## F1 — the drill found my own wrong assumptions before it found anything else

Written against an API I had assumed rather than read, it failed 9/9 on the first
run: `ensure_effort` needs a `name`, `freeze` takes `Trigger` and `Concern`
objects rather than a `reason=` string, `clear` takes a `Decision` and an
`actor_role`, and the kill switch is `kill_switch(bool)` rather than
`freeze(None)`.

That is a small illustration of the reason the drill exists. Every one of those
calls is individually documented; nothing was mysterious. What surfaced them was
composing them in sequence and running it.

## F2 — the laws asserted are cited, not invented

Each check names where it comes from:

- **ABORT WINS EVERY RACE** — `set_lifecycle`'s own docstring, from the live
  2026-07-15 "ouroboros" incident where an effort resurrected twice.
- **the operator can still reopen** — the exception that same docstring names. A
  law with no release is a trap.
- **the observer never breaks a transition, and does not fire on a suppressed
  one** — memory-plane §2.2. Firing on a suppressed done-after-abort would write
  a 'done' memory for an effort the operator aborted, contradicting the law above
  it.
- **the kill switch covers efforts created after it was thrown** — one that
  missed them would be worse than none, because the operator would believe the
  org was stopped.

## F3 — the standalone runner needed an explicit dispose

On Windows an open sqlite handle makes `TemporaryDirectory` cleanup raise
`PermissionError`, so the drill exited non-zero AFTER every check passed — a red
that says nothing about the org. `await d.dispose()` before the directory is
removed. Exit 0, 9/9.

## F4 — what this drill does NOT cover

It drives the GOVERNANCE gate only. The harness drill also covers separation of
duties across roles (a developer cannot test or review their own work) and the
stale-pass rule. agent-org enforces those in the orchestrator rather than the
gate, and driving them needs a running bridge rather than a database.

**Not claimed as covered.** The next slice for this is an orchestrator-level
drill; recorded so "agent-org has a drill" is not read as "agent-org's
choreography is drilled".
