# LATENT: personal-plane content has a second home in `thoughts`, and nothing reads its label

Found 2026-08-30 by a U5 adversarial verifier; the production half confirmed by the
orchestrator with its own queries. **Not exploitable today. Exploitable the moment the personal
plane is used.**

## The operational constraint, first, because it is the actionable part

> **Do not write a personal-exposure memory until this is closed.**

Today `agent_memories` holds **0** personal rows, which is the only reason nothing is exposed.
The containment work proved on the read tools is real, and it does not cover this path.

## What the leak is

`performWriteback` (OB1 `agent-memory.ts:281-296`) mirrors a memory's **full content** into the
general-purpose `thoughts` table, stamped with `metadata.exposure`.

**Nothing reads that label.** `index.ts` is 2084 lines with 6 `FROM thoughts` statements and 36
`queryObject` calls, and the string `exposure` occurs in it exactly **once** — in a comment on
line 2061.

A verifier ran the whole path on a throwaway pgvector DB built from the real 25-file init chain,
with the branch server booted unmodified — one session, one server, one brain key:

```
writeback                   -> memory exposure=personal, thought exposure=personal
agent_memory_inspect(id)    -> "Refused (not_found)" + access_refused audit row   [guard holds]
list_thoughts{limit:5}      -> "SYNTHETIC-U5 SECRET personal-plane payload"
search_thoughts{query}      -> "100.0% match ... SYNTHETIC-U5 SECRET personal-plane payload"
audit rows after both reads -> NONE. Silent.
```

The door that was hardened refuses the id, and a general-purpose door beside it hands back the
content with no check and no record that anyone asked.

## The production half, which I verified myself

```
SELECT COALESCE(metadata->>'exposure','(none)'), count(*) FROM thoughts GROUP BY 1;
  (none) | 12989
  ops    |     4

SELECT count(*) FROM agent_memories
 WHERE COALESCE(metadata->>'exposure','personal') = 'personal';
  0
```

**The mirror is deployed and working in production** — the 4 ops memories have 4
exposure-labelled thoughts. The mechanism is live; only the sensitive input is absent.

Which door is exposed matters: the drill verifies the ops gateway and the cloud gateway, but
allocates a port for the **raw openbrain-mcp door and never calls a tool on it** — and that raw
door is what `openbrain-mcpo` (OWUI's Open Brain bridge, obnet + llm-net, raw `MCP_ACCESS_KEY`
per `OB1/docker/mcpo.config.json`) is wired to. The gateway's own docstring says local clients
bypass it *by design*.

## The comment that made it invisible

`agent-memory.ts:255-258` states the exposure label is mirrored onto the thought "so the generic
search_thoughts lane enforces the same boundary as the agent-memory recall."

That sentence is the load-bearing justification for the mirror being safe, and it is false in
this tree. Writing a label is not enforcing a boundary. Nobody re-read the readers, because the
comment said they were covered.

## Why three rounds of guarding did not find it

Each round guarded **readers of `agent_memories`**, one at a time — `inspect`, then
`list_review_queue` and `recall_trace`, then `performReview`. Round 3 built a chokepoint and a
completeness test, and both were scoped to that same one table in six hand-named files. Content
lives in at least four places (`agent_memories`, `thoughts`, `agent_memory_recall_traces`,
`agent_memory_recall_items`), and `index.ts` alone has 36 query sites.

The completeness test was itself vacuous, in the exact way it was briefed not to be: a verifier
added an unguarded by-id resolver of `id, summary, content, metadata` in a NEW file, imported it
into a scanned file, and the suite stayed **154 passed / 0 failed**. Renaming that file to
`agent-memory-lookup.ts` turned it red. The "scan" was a hand-written 6-entry list cross-checked
against a filename prefix in one directory — a list with a spell-checker. `Dockerfile:19` is
`COPY *.ts ./`, so the unguarded file would ship.

Two further defeats of the same guard, both executed:

- **SQL precedence.** The query builder concatenates caller fragments unparenthesised, so one
  unparenthesised `OR` turns `plane AND a OR b` into `(plane AND a) OR b`. Against real Postgres
  with the unmodified module, the ops door returned both personal fixtures *with content* and
  wrote zero audit rows — against a test comment asserting no arrangement could remove the
  predicate.
- **Plane widening.** Forging a door plane by object literal is correctly blocked (`TS2352`), but
  the accessor returns an **unfrozen** array, so pushing onto it passes `deno check` and re-binds
  every guarded statement to both planes.

## The reframe now briefed for round 4

Guarding every reader of every store is the enumerate-and-patch loop one level up, and it has now
lost four times. **Control the write.** If personal-exposure content never enters a
general-purpose store, the readers of that store need no guard: the mirror exists for unified
search, and content that must not appear in unified search does not belong there. The
alternative — making all 36 query sites plane-aware — is the option that keeps failing.

## DECISIONS entries to append

- **2026-08-30, U5 (LATENT SECURITY):** personal-plane memory content is mirrored verbatim into
  `thoughts` by `performWriteback`, and no reader of `thoughts` consults the exposure label —
  proven live: `agent_memory_inspect` refuses an id while `search_thoughts` returns the same
  content, with no audit row. The mirror is DEPLOYED (production `thoughts`: 4 rows labelled
  `ops`); the leak is unexploitable only because `agent_memories` holds 0 personal rows.
  **Constraint until closed: do not write a personal-exposure memory.** Round 4 is briefed to
  contain at the WRITE rather than guard six readers.
  Revert path: nothing to revert — the finding is on unmerged branches and production is
  unchanged.
- **2026-08-30, method:** a completeness test whose enumeration is a hand-written file list is a
  list with a spell-checker. It passed while an unguarded by-id resolver shipped in the image,
  and went red only when the new file was renamed into the guarded naming family. If a gate
  claims no unguarded site can be added, its enumeration must be derived from the code — and the
  proof is adding one yourself, in a file named nothing like the others.
- **2026-08-30, method:** a comment asserting that a mechanism enforces a boundary is why nobody
  re-read the readers. Writing a label is not enforcing it. Comments that claim enforcement need
  a test, or they are load-bearing fiction.
