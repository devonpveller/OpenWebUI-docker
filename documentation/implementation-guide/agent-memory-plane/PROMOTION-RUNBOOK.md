# Agent-memory schema — promotion runbook

> Status: LIVE · memory-plane Phase 1.1 · first executed 2026-08-29.
> Scope: the 8 `agent_memory*` sidecar tables, their trigger, and their two
> functions. Nothing here writes an agent-memory row.

## Why a runbook exists at all

OB1's `docker-entrypoint-initdb.d` scripts run **on a fresh volume ONLY**. The live
`openbrain-db-data` volume was initialised long ago, so every migration since has needed
two homes: the initdb mount (so a rebuilt database matches) and a manual `psql` apply (so
the running one does). Skip either and the two silently diverge — which is exactly what had
happened here before this landed.

**The 2026-08-29 finding:** the live database already had all 8 tables, applied by hand at
some earlier point, while `OB1/docker/init-agent-memory.sql` **did not exist and was not
mounted**. A fresh volume would therefore have produced a database *without* the
agent-memory plane. The half that was missing was the fresh-volume half, not the live one.

## Preconditions

- `openbrain-db` running.
- The roles `service_role`, `authenticated`, `anon` exist. They are created by
  `40-init-graph.sql`; the schema's `CREATE POLICY … TO service_role` fails without them.
  (Verified present on the live DB, 2026-08-29.)
- `public.thoughts` exists — the schema `RAISE EXCEPTION`s if not.

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -t -c `
  "SELECT rolname FROM pg_roles WHERE rolname IN ('service_role','authenticated','anon');"
```

## 1. Check whether it is already applied

Idempotent, so re-applying is safe — but knowing beforehand tells you what you are doing.

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -t -c `
  "SELECT count(*) FROM information_schema.tables
    WHERE table_schema='public' AND table_name LIKE 'agent_memor%';"
```

`8` = present. `0` = not applied. Anything between means a partial apply — stop and look
before continuing.

## 2. Apply

```powershell
Get-Content -Raw OB1\docker\init-agent-memory.sql |
  docker exec -i openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1
```

`ON_ERROR_STOP=1` matters: without it psql reports success after a failed statement, and the
whole point of this step is knowing whether it took.

Expect `NOTICE: … does not exist, skipping` lines from the `DROP … IF EXISTS` idempotency
guards. Those are normal. `ERROR` is not.

## 3. Verify BY QUERY — never by a clean exit code

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -t -c `
 "SELECT count(*) AS tables FROM information_schema.tables
    WHERE table_schema='public' AND table_name LIKE 'agent_memor%';
  SELECT count(*) AS trigger FROM pg_trigger
    WHERE tgname='trg_agent_memories_updated_at';
  SELECT count(*) AS functions FROM pg_proc
    WHERE proname IN ('agent_memories_set_updated_at','agent_memory_hash_text');"
```

**PASS: `8`, `1`, `2`.** Anything else is a partial apply.

The 8 tables are `agent_memories`, `agent_memory_source_refs`, `agent_memory_artifacts`,
`agent_memory_relations`, `agent_memory_review_actions`, `agent_memory_recall_traces`,
`agent_memory_recall_items`, `agent_memory_audit_events`.

## 4. Prove RLS is not silently blocking the application

The schema enables row-level security on all 8 tables and grants policies to `service_role`
— but `openbrain-mcp` connects as `postgres`. Tables that exist and cannot be written to are
worse than no tables, so prove the real caller can use them. The transaction is rolled back,
so this writes nothing:

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -t -c `
 "BEGIN;
  INSERT INTO agent_memories (workspace_id, memory_type, summary, content)
    VALUES ('rls-probe','lesson','probe summary','probe');
  SELECT count(*) FROM agent_memories WHERE workspace_id='rls-probe';
  SELECT agent_memory_hash_text('probe') IS NOT NULL;
  ROLLBACK;"
```

**PASS: `INSERT 0 1`, then `1`, then `t`.** A permission error here means RLS is biting and
the plane is unusable regardless of the tables existing.

Confirm nothing persisted: `SELECT count(*) FROM agent_memories;` → `0` (until Phase 2
starts writing).

## 5. Confirm the existing system is unharmed

This is an additive sidecar; it must behave like one.

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -t -c "SELECT count(*) FROM thoughts;"
```

Compare with the count you took before applying. It must be unchanged (12,976 at the
2026-08-29 apply).

## Rollback

Don't drop the tables. OB1's posture is archive-don't-destroy, and these are inert until
Phase 1.2 ships the server code — an unused table costs nothing. To disable the feature,
revert the OB1 gitlink and rebuild `openbrain-mcp`; the tables stay behind harmlessly.

If they genuinely must go, that is a deliberate destructive operation needing a backup of
`openbrain-db` first — not part of this runbook.

## Keeping the two places in step

`OB1/docker/init-agent-memory.sql` is a **verbatim copy** of
`OB1/schemas/agent-memory/schema.sql`. If either changes, copy it across and re-run this
runbook. They should hash the same:

```powershell
docker exec openbrain-db sh -c "echo"   # (any shell) — or locally:
sha256sum OB1/schemas/agent-memory/schema.sql OB1/docker/init-agent-memory.sql
```

At the 2026-08-29 apply both were
`46a74f05d4992a552e584389ff2adad2835c5dae7d24b414840ac7af289dcd3e`.

## The initdb prefix — CORRECTED

This section used to say the mount was `99a-init-agent-memory.sql`, and reasoned that
"`99a` sorts after `99-` (`-` < `a`)". **That is ASCII order, which is what `sh` uses.**
The Postgres entrypoint globs through bash under a UTF-8 locale, whose collation ignores
punctuation for primary weight — so `99a-` sorts *before* `99-`, and the migration would
have run ahead of the tables it depends on, exactly the failure the prefix was chosen to
avoid.

The chain is now **fixed-width, in tens**: `010-` … `130-`. No suffix tricks, no collation
dependency, and room to insert between any two. `scripts/checks/test-quartz4-offline.ps1`
derives the chain from compose and asserts both directions (every mount names a real file;
every `init*.sql` is mounted), so a file that reaches only one place is caught.

## Later migrations, and how to apply them

Each is additive and each must reach **both** places — the initdb mount for fresh volumes
and a psql apply for the live one. There is no migration runner.

| File | Adds | Apply |
|---|---|---|
| `init-agent-memory-idempotency.sql` | per-workspace idempotency index (replaces the globally-unique one) | as below |
| `init-agent-memory-promote-exposure.sql` | `promote_exposure` to the `agent_memory_review_actions` CHECK | as below |
| `init-agent-memory-check-type.sql` | `check` to the `agent_memories.memory_type` CHECK (U3's finding→durable-check) | as below |
| `init-agent-memory-corpus-failclosed.sql` | **the corpus predicate stops defaulting to visible** (DFU PLAN.md C.8 clause 3) - see the dedicated section below | **read that section first** |
| `init-agent-memory-rls.sql` | **the exposure boundary moves into the database** - FORCE RLS + narrow policies on `agent_memories`, `thoughts` and the 8 sidecars (DFU PLAN.md A2) | **read its section below** |
| `init-graph-plane-rls.sql` | **the boundary reaches the DERIVED graph** - 8 tables that derive from `thoughts`, plus the write gate on `queue_entity_extraction()` | **read its section below** |

```powershell
Get-Content OB1\docker\init-agent-memory-promote-exposure.sql |
  docker exec -i openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1
```

Verify **by query**, never by a clean exit code:

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -tA -c @"
SELECT 'promote_exposure_allowed', count(*)
  FROM pg_constraint
 WHERE conname = 'agent_memory_review_actions_action_check'
   AND pg_get_constraintdef(oid) LIKE '%promote_exposure%';
"@
```

Expect `promote_exposure_allowed|1`. **Executed against the live volume 2026-08-30** —
before: the CHECK listed the original nine; after: `promote_exposure_allowed|1`, and a
bogus action (`delete_everything`) is still refused, so the constraint was widened rather
than loosened. Rollback is the same `ALTER` with the original
nine-value list; no row becomes invalid unless a `promote_exposure` action has been written.

### `check` memory_type — executed against the live volume 2026-08-30

Before: `check_allowed|0`. After: `check_allowed_after|1`, and an invented type
(`not_a_real_type`) is STILL refused — the constraint was widened, not removed, which is
the direction a migration like this fails in silently.

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -tA -c @"
SELECT 'check_allowed', count(*) FROM pg_constraint
 WHERE conname = 'agent_memories_memory_type_check'
   AND pg_get_constraintdef(oid) LIKE '%''check''%';
"@
```


## `init-agent-memory-corpus-failclosed.sql` -- the corpus predicate stops defaulting to visible

**Executed against the live volume 2026-08-31** (item `dfudone`, under an `open-brain` plane lease).

### Why

`ob_corpus_on_ops_plane` shipped as `md->>'exposure' IS NULL OR md->>'exposure' = 'ops'`.
An **unlabelled** thought was therefore VISIBLE to the agent plane -- the
"unlabelled defaults to fine" class from this effort's own class list, in SQL. Measured
before the migration: **12,989 of 12,993** thoughts carried no exposure label at all, so
almost the entire corpus was on the ops plane by *default* rather than by decision, and
any future row whose write path forgot the label would join it silently.

### What it does, and why the order matters

1. **Label first** -- every unlabelled thought is stamped `exposure='ops'`, which is what
   the predicate already treated it as, so **no row changes visibility**.
2. **Then close** -- the predicate drops its `IS NULL` arm.

The reverse order would hide 12,989 rows between the two statements.

### Apply

```powershell
docker cp OB1\docker\init-agent-memory-corpus-failclosed.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 `
    -f /tmp/init-agent-memory-corpus-failclosed.sql
```

### Verify **by query**, never by a clean exit code

The migration's whole claim is that the ops corpus is unchanged and the *default* is not.
Both halves have to be measured, because the first is invisible in a row count alone.

```powershell
# 1. The ops corpus still reads normally: expect 12993 before AND after.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SET ROLE service_role; SELECT count(*) FROM thoughts;"

# 2. The label census: expect unlabelled 0 | ops 12993 | personal 0 | stamped 12989.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT count(*) FILTER (WHERE metadata->>'exposure' IS NULL)  AS unlabelled,
          count(*) FILTER (WHERE metadata->>'exposure'='ops')     AS ops,
          count(*) FILTER (WHERE metadata->>'exposure'='personal') AS personal,
          count(*) FILTER (WHERE metadata->>'exposure_backfill'='dfu-c8-corpus-failclosed') AS stamped
     FROM thoughts;"

# 3. THE ONE THAT MATTERS -- an unlabelled row must now be INVISIBLE. Inside a
#    transaction that is ROLLED BACK, so nothing persists. Expect 0.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN;
   INSERT INTO thoughts (content, metadata) VALUES ('UNLABELLED-CANARY','{}'::jsonb);
   SET ROLE service_role;
   SELECT 'agent-plane sees unlabelled canary: '||count(*) FROM thoughts
    WHERE content='UNLABELLED-CANARY';
   ROLLBACK;"
```

**Recorded results, 2026-08-31:** (1) `12993` before, `12993` after -- no difference.
(2) `0|12993|0|12989|12993`. (3) `agent-plane sees unlabelled canary: 0` -- fail-closed.
Check 3 is the necessary one: with the corpus already fully labelled by step 1, checks 1
and 2 look identical whether the predicate was flipped or not.

### Rollback

`OB1\dockerevert-agent-memory-corpus-failclosed.sql` -- re-opens the predicate, **then**
unstamps exactly the rows this migration stamped (matched on
`metadata->>'exposure_backfill' = 'dfu-c8-corpus-failclosed'`, so the 4 rows labelled `ops`
by the write path are left alone). Nothing is dropped and no row is deleted.

```powershell
docker cp OB1\dockerevert-agent-memory-corpus-failclosed.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 `
    -f /tmp/revert-agent-memory-corpus-failclosed.sql
```

Expect afterwards: unlabelled `12989`, ops `4`, stamped `0`.


## `init-agent-memory-rls.sql` -- THE EXPOSURE BOUNDARY, MOVED INTO THE DATABASE

**Executed against the live volume 2026-08-30** (item `u5rls`). **This section was written
2026-08-31 by item `u5graph`, which found the migration applied to production and its source
on no branch of this repository** -- a boundary running live from code no fresh clone could
reproduce, and no runbook row saying it had been applied. It is now mounted at `180-` and
recorded here. The file itself is unchanged from what was applied.

### Why

`agent_memories` had RLS enabled with `relforcerowsecurity = f` and one policy,
`USING (true)`; `thoughts` had RLS off entirely. Four rounds of guarding readers ran against
a table whose own access policy said *allow everything*. See DFU `PLAN.md` amendment A2.

### Apply

```powershell
docker cp OB1\docker\init-agent-memory-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 `
    -f /tmp/init-agent-memory-rls.sql
```

### Verify **by query**

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
 "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
         (SELECT count(*) FROM pg_policies p
           WHERE p.schemaname='public' AND p.tablename=c.relname
             AND p.permissive='PERMISSIVE' AND p.qual='true') AS wide
    FROM pg_class c
   WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'
     AND (c.relname LIKE 'agent_memor%' OR c.relname='thoughts')
   ORDER BY 1;"
```

**PASS:** every row `t|t` and `wide` = 0, except `agent_memory_audit_events`, whose policy is
deliberately left wide (its payload is `{tool, reason}` plus an id -- narrowing it would hide
the `access_refused` rows that are the drill's evidence). Recorded 2026-08-31: 9 tables,
all `t|t`, `wide` = 0 on 8 and 1 on `agent_memory_audit_events`.

### Rollback

`OB1\docker\revert-agent-memory-rls.sql` -- restores the `USING (true)` policies, clears
FORCE, disables RLS on `thoughts` and re-grants TRUNCATE. The added columns, indexes,
functions, views and role are left in place deliberately (dropping a column is not
reversible) and are inert once the policies are wide again.

## `init-graph-plane-rls.sql` -- the boundary reaches the DERIVED GRAPH

**Executed against the live volume 2026-08-31** (item `u5graph`, under an `open-brain` plane
lease). Mounted at `200-`, after `190-init-agent-memory-corpus-failclosed.sql`.

### Why

`180-` governed `agent_memories`, `thoughts` and the eight `agent_memory_*` sidecars. Eight
tables that DERIVE from `thoughts` were still `USING (true)` with FORCE off. The proven
disclosure: `entity_extraction_queue.source_fingerprint` **is** `sha256(thoughts.content)`,
so `service_role` -- which is `PGRST_DB_ANON_ROLE`, i.e. every unauthenticated caller on
`open-brain_obnet` -- could read the existence and a content hash of a thought it could not
see, and confirm any guess by hashing it.

The target set is **derived from `pg_constraint` at apply time**, not listed. The operator's
list named six tables; the schema said seven; the derivation says **eight** --
`idea_revisions` carries `thought_id REFERENCES thoughts(id)`, a `summary TEXT` and a
`content_hash TEXT`. A table that derives from the corpus and is not classified makes the
migration **RAISE**.

### Preconditions

`180-` and `190-` must be applied first. The file checks both and refuses otherwise: it calls
`ob_corpus_on_ops_plane`, and its write gate is only a gate while that predicate is
fail-CLOSED.

### Apply

```powershell
docker cp OB1\docker\init-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 `
    -f /tmp/init-graph-plane-rls.sql
```

Idempotent -- re-applying is a no-op. (It was not, at first: the second apply died on
`policy "thought_entities_plane" already exists` because only the OLD policy names were
dropped. Every policy the file creates is now dropped by its own name too. Measured on the
throwaway, three consecutive applies.)

### Verify **by query**, never by a clean exit code

Three things have to be true, and a row count alone shows none of them.

```powershell
# 1. THE BOUNDARY. Expect t|t on all eight, and wide=0 on the four tier-A tables.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
 "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
         (SELECT count(*) FROM pg_policies p
           WHERE p.schemaname='public' AND p.tablename=c.relname
             AND p.permissive='PERMISSIVE' AND p.qual='true') AS wide
    FROM pg_class c
   WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'
     AND c.relname IN ('thought_entities','entity_extraction_queue','thought_edges',
                       'idea_revisions','entities','edges','source_entities',
                       'consolidation_log')
   ORDER BY 1;"

# 2. THE OPS PATH IS UNBROKEN. Take these BEFORE and compare AFTER; they must be identical.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
 "SET ROLE service_role;
  SELECT 'thoughts', count(*) FROM thoughts
  UNION ALL SELECT 'entities', count(*) FROM entities
  UNION ALL SELECT 'thought_entities', count(*) FROM thought_entities
  UNION ALL SELECT 'edges', count(*) FROM edges
  UNION ALL SELECT 'entity_extraction_queue', count(*) FROM entity_extraction_queue
  UNION ALL SELECT 'source_entities', count(*) FROM source_entities
  UNION ALL SELECT 'idea_revisions', count(*) FROM idea_revisions
  UNION ALL SELECT 'wiki_pages', count(*) FROM wiki_pages;"

# 3. THE ONE THAT MATTERS -- the leak, attacked, with a positive control. Rolled back.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
 "BEGIN;
  INSERT INTO thoughts (content, metadata)
    VALUES ('U5G-CANARY-PERSONAL', jsonb_build_object('exposure','personal'));
  INSERT INTO thoughts (content, metadata)
    VALUES ('U5G-CANARY-OPS', jsonb_build_object('exposure','ops'));
  INSERT INTO entity_extraction_queue (thought_id, status, source_fingerprint)
    SELECT id,'blocked','U5G-P-FP' FROM thoughts WHERE content='U5G-CANARY-PERSONAL'
    ON CONFLICT (thought_id) DO UPDATE SET source_fingerprint='U5G-P-FP';
  SET ROLE service_role;
  SELECT 'agent plane sees the personal fingerprint: '||count(*)
    FROM entity_extraction_queue WHERE source_fingerprint='U5G-P-FP';
  SELECT 'CONTROL - agent plane sees the ops canary: '||count(*)
    FROM thoughts WHERE content='U5G-CANARY-OPS';
  ROLLBACK;"
```

**Recorded results, 2026-08-31.** (1) all eight `t|t`; `wide` = 0 on `thought_entities`,
`entity_extraction_queue`, `thought_edges`, `idea_revisions` and 2 on the four tier-B tables,
which are DELIBERATELY wide -- see section 3 of the migration, and the `COMMENT ON POLICY`
that says so in the database. (2) identical before and after: thoughts `13001`, entities
`69785`, thought_entities `54063`, edges `92865`, entity_extraction_queue `13001`,
source_entities `81273`, idea_revisions `37`, wiki_pages `47987`. (3) `0` and `1` -- the
fingerprint is refused while the control is returned.

Check 3 is the necessary one. Checks 1 and 2 look identical whether the policies bind or not;
only the attack-with-a-control can tell a bound door from a broken query.

Eight PostgREST doors were also attacked over the network from `open-brain_obnet`, each with
a live ops control (`thoughts`, `thought_entities`, `entity_extraction_queue` by fingerprint
and by id, `thought_edges`, `idea_revisions`, the `thought_entities -> thoughts(content)`
embed, and the `v_thoughts` view): all eight `personal_rows=0 ops_rows>0`.

### The write gate, which is the other half

`queue_entity_extraction()` now refuses to fingerprint an off-plane thought AND deletes the
queue row when an ops thought becomes personal. Measured live, rolled back: personal insert
-> `0` queued, unlabelled insert -> `0` queued, **ops insert -> `1` queued** (the control
that proves the gate is not simply broken), ops -> personal transition -> `0` queued.

### Rollback

`OB1\docker\revert-graph-plane-rls.sql`. **It re-opens the measured disclosure** -- after it
runs, an unauthenticated caller on `open-brain_obnet` can read
`entity_extraction_queue.source_fingerprint` for a thought it cannot see. The round trip was
executed on the throwaway: revert restores `force=f` and the original policy counts on all
eight, restores all three functions to SECURITY DEFINER, the RED probe returns, and
re-applying the migration COMMITs.

```powershell
docker cp OB1\docker\revert-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 `
    -f /tmp/revert-graph-plane-rls.sql
```
