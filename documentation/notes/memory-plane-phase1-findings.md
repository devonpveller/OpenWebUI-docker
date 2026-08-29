# Findings — memory-plane Phase 1.1, 2026-08-29

The anchor's `findings_sink`. Checked against `work/memplane1` at `734e4fd`,
OB1 at `3a47606`.

---

## F1 — a SECOND unmounted migration, same divergence, different plane

`OB1/docker/init-wiki-pages-links.sql` exists on disk, is **live** on the running database,
and is **not mounted** in the initdb chain. It is the only such file:

```
ls init*.sql            -> 21 files
mounted in compose      -> 20
difference              -> init-wiki-pages-links.sql
```

It creates one thing:

```sql
CREATE INDEX IF NOT EXISTS idx_wiki_pages_links_gin
  ON wiki_pages USING gin (links jsonb_path_ops);
```

`idx_wiki_pages_links_gin` is present on the live DB, so it was applied by hand and never
added to the chain — exactly the half-done two-place mechanism this item fixed for
agent-memory.

**Why it matters more than an index usually would.** Its own header says the wiki graph
needs backlinks via `links @> '["slug"]'`, and "without an index [that is] a sequential scan
over ~41k rows on every graph render". So a rebuilt database would not be missing a
*feature* — it would come up with a silent performance cliff in the wiki viewer, of the
same kind that was diagnosed and fixed at real cost in August.

The fix is one mount line beside the one this item added:

```yaml
- ./init-wiki-pages-links.sql:/docker-entrypoint-initdb.d/99b-init-wiki-pages-links.sql:ro
```

**Deliberately not done here.** It is the wiki plane, my anchor scopes this item to
agent-memory, and folding an unrelated fix into that OB1 commit is precisely the "and also
fix what you find" the anchor refuses. It is one line and wants its own item — ideally with
a check that makes the whole class impossible (see F2).

---

## F2 — nothing detects an unmounted migration, and that is the real gap

Both F1 and this item's own subject are instances of one failure: a `.sql` file that is
applied to the live database but absent from the initdb chain. Nothing in the repo notices.
`test-quartz4-offline.ps1` builds a throwaway DB from the chain, so it verifies what IS
mounted works — it cannot see what is missing.

A cheap check would have caught both, and is worth building:

> every `OB1/docker/init*.sql` appears in `docker-compose.yml`'s initdb mounts, and every
> mount points at a file that exists.

That is a five-line comparison, it runs offline with no database, and it turns a silent
divergence into a failing check. The reverse direction matters too: a mount naming a
missing file would make `docker compose up` fail on a fresh volume only — the worst time
to find out.

**Not built here** (this item is the schema, not a new check), but this is the highest-value
follow-up from the phase.

---

## F3 — `agent_memories.summary` is NOT NULL, and Phase 1.2 will hit it

Found by probing RLS: an insert with `workspace_id`, `memory_type` and `content` but no
`summary` fails with

```
null value in column "summary" of relation "agent_memories" violates not-null constraint
```

The writeback path in Phase 1.2 must always produce a summary — it cannot be an optional
enrichment applied later. Worth knowing before the zod schema is written, because the
obvious shape (content required, summary optional) will fail at the database.

---

## F4 — the live schema was already applied; only the fresh path was missing

Recorded because the plan assumed otherwise, and the next reader should not repeat the
check. Phase 1.1 as written reads "schema lands", implying deployment. In fact the live
database already had all 8 tables, the trigger and both functions, and a column-by-column
comparison against a clean build from `schema.sql` was **identical** — 101 columns, exact
match.

So the promotion half was already done by hand at some earlier point, and the fresh-volume
half was the missing one. The plan's Phase 1.1 acceptance ("promotion runbook executed +
verify query green on live DB") was satisfiable only by *verifying*, not by applying.

This does not weaken the phase — it re-points it. But a session that had assumed the plan
was right about the starting state would have "applied" a schema that was already there,
seen a clean exit, and reported a deployment that never happened.
