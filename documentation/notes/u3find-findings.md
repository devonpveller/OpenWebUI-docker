# Findings — U3: tester finding → durable check (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U3 · class 2 — the harness's durable checks mirror agent-org's, not a new shape
DECISION: `scripts/agent-harness/durable_checks.py` — a content-addressed,
          project-scoped registry in the SHARED git dir, with `add` / `list` /
          `run`, and a fail-soft mirror that writes `memory_type='check'` through
          the ops door.
CITED:    §0 A5 — agent-org "BUILT and PROVED finding→durable-check (gym-007)";
          the harness "banked every lesson from 9 cycles as prose in
          MERGE-PROTOCOL — the exact evaporation §2.3 names" and is "currently
          the violator". §2's U3 row requires harness findings to write
          `memory_type='check'`.
WHY MIRROR: `projects.add_acceptance_check` is content-addressed, carries an
          origin note, and runs against every future delivery. U3 is
          UNIFICATION, so the harness gets those semantics rather than a second
          dialect of them.
REVERT:   Delete the module and its registry file. Nothing else reads it yet.

### 2026-08-30 · U3 · class 2 — one vocabulary, two files, now tested against each other
DECISION: `memoryTypeArg`'s zod enum is asserted equal to the SQL CHECK's value
          list, read from `init-agent-memory*.sql` at test time.
CITED:    Found by failing: the live mirror returned `MCP error -32602` because
          the enum still listed eight values after the migration widened the
          database to nine. The tool refused a value the schema permitted, before
          the database was ever consulted. Neither file was wrong on its own.
REVERT:   Drop the test. The enum and the CHECK go back to being two independent
          copies of one vocabulary.

---

## F1 — the drift was invisible until something ran end to end

Both halves passed their own tests. The migration proved the database accepts
`check`; the tool suite proved the schemas are non-empty and described. Only an
actual write through the door found that the zod enum rejects the value first.

**This is the second time in this phase** that a "check that checks nothing" was
caught by executing the real path rather than by adding a test — and both times
the guard that closes it compares two files that are supposed to agree. That
pattern is now used three times in this repo (harness.config.json's two readers,
ScopeNode's columns, this enum).

## F2 — a corrupt registry RAISES rather than reading as empty

Reading it as empty would report "0 checks, all green" for a line that has banked
dozens — a green that means the opposite of what it says. Tested.

## F3 — the mirror is fail-soft and the registry is the durable artifact

`mirror_to_plane` returns False on every failure, including "the door is not
there". A memory write that could block banking a check would make the
unification cost you the thing being unified.

Proven live: `MIRRORED TO PLANE: True`, and the row reads
`check | pending | ops | tester-finding` in `agent_memories`.

## F4 — nothing calls this from the pipeline yet

`queue.ps1 -Fail` does not bank the tester's finding automatically. The
capability, the registry and the mirror all work by hand. Wiring the queue's
failure path into it is the next U3 slice — recorded so this does not read as the
pipeline being closed.
