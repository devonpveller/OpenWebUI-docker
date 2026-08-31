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
  UNION ALL SELECT 'idea_revisions', count(*) FROM idea_revisions;"

# ...and separately, because it DRIFTS. The wiki compiler runs on a schedule, so wiki_pages
# moves on its own between two runs of this block. It is worth eyeballing for order of
# magnitude; it is NOT an identity check, and leaving it in the column above made a moving
# member weaken the eight that do not move.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
 "SET ROLE service_role; SELECT 'wiki_pages (DRIFTS)', count(*) FROM wiki_pages;"

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
source_entities `81273`, idea_revisions `37`. `wiki_pages` was in this column when round 1
was written, recorded at `47987`; it measured `47981` on 2026-08-31 with nothing having
touched it, because the compiler writes to it continuously. It has been moved out of the
identity check and is now read separately -- a member that moves on its own weakens every
member that does not. (3) `0` and `1` -- the fingerprint is refused while the control is
returned.

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

---

## `init-graph-plane-rls.sql` — ROUND 2: the gate tests its own closure, and the boundary reaches views

**This is the same file at the same 200- mount point, not a new migration.** A live volume
that already carries round 1 must be re-applied to pick up the three fixes below; the file is
idempotent and re-applying it is the supported path (three consecutive applies COMMIT).

### Why

Round 1 was refuted three times. Each refutation was reproduced RED on a throwaway before it
was fixed, and each fix carries an ops positive control.

1. **A closure member was waved through by a hardcoded list.** §0 presented its target set as
   derived, but only the *referenced-by* arm applied a predicate. The *closure* arm classified
   eight members by membership of the `v_governed_180` name array and tested none of them.
   `agent_memory_audit_events` is `rls=t force=t` **and** carried a policy whose `qual` is
   literally `true` — so FORCE was on and the policy still permitted everything. It is now
   governed here on parent visibility of both its foreign keys, and the predicate lives in one
   function, `ob_relation_governed()`, called by all three arms.
2. **Views.** A view without `security_invoker` runs as its superuser owner, so RLS on the base
   table does not apply. `public.ideas_owed_research` reads `idea_revisions` and leaked the
   **existence** of a governed revision (it projects `ideas.*`, so `summary`, `thought_id` and
   `content_hash` are not returned). §6b now derives every view in `public` lacking the flag
   and sets it, and refuses on a **materialized** view over a force-RLS relation.
3. **The FK existence oracle.** `POST /agent_memories` with a hidden `thought_id` returned
   success while a nonexistent one returned `23503`. Both `agent_memories` policies now name
   `thought_id` in `WITH CHECK` (only there — the `USING` halves are unchanged from 180), so
   both cases fail identically at `42501`.

### Preconditions

Round 1 already applied (this file at 200-), plus 180 and 190. §0 refuses otherwise.

### Apply

```powershell
docker cp OB1\docker\init-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/init-graph-plane-rls.sql
```

Expect `NOTICE: security_invoker set on 4 view(s): ideas_owed_research, research_run_metrics,
reusable_claims, ungrounded_claims`, then `NOTICE: post-conditions hold on 9 table(s)`, then
`COMMIT`. On a second run the view NOTICE is replaced by `all public views already run as
SECURITY INVOKER` — that is the idempotent path, not a failure.

### Verify **by query**, never by a clean exit code

Each block is written to a file and run with `-f`, so no shell quoting sits between the SQL
and the database.

```powershell
# 1. THE PREDICATE APPLIED TO THE 180 LIST. Expect every row governed=t. This is the check
#    whose absence WAS the defect: before round 2, agent_memory_audit_events returned f here
#    and the migration exited 0 anyway.
@'
SELECT relname, public.ob_relation_governed(relname) AS governed
  FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r'
   AND relname IN ('agent_memories','agent_memory_source_refs','agent_memory_artifacts',
       'agent_memory_relations','agent_memory_review_actions','agent_memory_recall_traces',
       'agent_memory_recall_items','agent_memory_audit_events','thought_entities',
       'entity_extraction_queue','thought_edges','idea_revisions') ORDER BY 1;

-- 2. NO VIEW RUNS AS ITS OWNER. Expect zero rows.
SELECT relname FROM pg_class c WHERE relkind='v' AND relnamespace='public'::regnamespace
   AND COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
                  WHERE option_name='security_invoker'),'false') <> 'true';

-- 3. THE OPS PATH IS UNBROKEN. Take BEFORE and compare AFTER; they must be identical.
SELECT (SELECT count(*) FROM agent_memories) AS memories,
       (SELECT count(*) FROM agent_memory_audit_events) AS audit_events,
       (SELECT count(*) FROM idea_revisions) AS revisions,
       (SELECT count(*) FROM ideas_owed_research) AS owed;
'@ | Set-Content -Encoding utf8 verify-a.sql
docker cp verify-a.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -f /tmp/verify-a.sql
```

```powershell
# 4. THE ONES THAT MATTER — both leaks attacked with a live positive control, inside a
#    transaction that is ROLLED BACK so nothing persists. Expect personal=0 ops=1, and both
#    SQLSTATEs 42501 (a 23503 on either line means the FK oracle is open again).
@'
BEGIN;
INSERT INTO thoughts (id, content, metadata)
VALUES (990001,''RB-OPS'',''{"exposure":"ops"}''::jsonb),
       (990002,''RB-PERSONAL'',''{"exposure":"personal"}''::jsonb);
INSERT INTO agent_memories (id, thought_id, workspace_id, memory_type, summary, content, metadata)
VALUES (''cccccccc-0000-0000-0000-000000000001'',990001,''ai-stack'',''lesson'',''RB-OPS'',''x'',''{"exposure":"ops"}''::jsonb),
       (''cccccccc-0000-0000-0000-000000000002'',990002,''ai-stack'',''lesson'',''RB-PERSONAL'',''x'',''{"exposure":"personal"}''::jsonb);
INSERT INTO agent_memory_audit_events (event_type, workspace_id, memory_id, actor_kind)
VALUES (''memory_written'',''ai-stack'',''cccccccc-0000-0000-0000-000000000001'',''agent''),
       (''memory_written'',''ai-stack'',''cccccccc-0000-0000-0000-000000000002'',''agent'');
SET ROLE service_role;
SELECT count(*) FILTER (WHERE memory_id=''cccccccc-0000-0000-0000-000000000002'') AS personal,
       count(*) FILTER (WHERE memory_id=''cccccccc-0000-0000-0000-000000000001'') AS ops
  FROM agent_memory_audit_events;
DO $x$ BEGIN
  INSERT INTO agent_memories (thought_id, workspace_id, memory_type, summary, content, metadata)
  VALUES (990002,''ai-stack'',''lesson'',''P'',''x'',''{"exposure":"ops"}''::jsonb);
EXCEPTION WHEN others THEN RAISE NOTICE ''hidden      SQLSTATE=%'', SQLSTATE; END $x$;
DO $x$ BEGIN
  INSERT INTO agent_memories (thought_id, workspace_id, memory_type, summary, content, metadata)
  VALUES (999999,''ai-stack'',''lesson'',''P'',''x'',''{"exposure":"ops"}''::jsonb);
EXCEPTION WHEN others THEN RAISE NOTICE ''nonexistent SQLSTATE=%'', SQLSTATE; END $x$;
RESET ROLE;
ROLLBACK;
'@ | Set-Content -Encoding utf8 verify-b.sql
docker cp verify-b.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -f /tmp/verify-b.sql
```

Check 1 returning all `t`, and check 4, are the necessary ones. The counts in check 3 look
identical whether the policies bind or not — only an attack with a control can tell a bound
door from a broken query.

**Recorded result (throwaway built from the compose-derived 28-file chain, 2026-08-31).**
RED before the migration: personal audit rows visible `2`, personal idea visible through
`ideas_owed_research` `1`, hidden `thought_id` insert **succeeded** while a nonexistent one
returned `23503`. GREEN after: `0`, `0`, and both arms `42501`. The ops positive control was
`1` on every probe in both arms, four `service_role` ops writes succeeded, and the
`access_refused` audit for a hidden memory still writes as the superuser the real writer uses
(`openbrain-mcp` runs `DB_USER=postgres`, verified on the live container).

Adversarial cases, each RED for the right reason: a 180 table regressed to `USING(true)` — §0
raises naming it; a brand-new owner-rights view over a governed table — §6b closes it
(personal `1` -> `0`, ops control `1`); a **materialized** view over a force-RLS relation —
refused outright, because `security_invoker` cannot fix one.

### Rollback

`OB1\docker\revert-graph-plane-rls.sql` — extended in round 2 to also restore the wide
`agent_memory_audit_events` policy, the `agent_memories` policies without their `thought_id`
arm, and to clear `security_invoker` on the four views that lacked it. **It re-opens all three
disclosures above in addition to the round 1 fingerprint leak.** The round trip was executed
on the throwaway: GREEN -> revert -> RED returns -> re-apply -> GREEN.

```powershell
docker cp OB1\docker\revert-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/revert-graph-plane-rls.sql
```

---

## `init-graph-plane-rls.sql` — ROUND 3: absence must DENY, and the sweep moves to mechanisms

**Still the same file at the same 200- mount point.** A live volume carrying round 1 or round 2
must be re-applied to pick up the fixes below. The file is idempotent (three consecutive applies
COMMIT) and re-applying it is the supported path.

**This round CHANGES PRIVILEGES as well as policies.** §6a withdraws `INSERT`, `UPDATE` and
`DELETE` from `service_role` on the agent-memory corpus and on `idea_revisions`. `SELECT` is
untouched everywhere. Read the "what could break" note before applying.

### Why

Round 2 was refuted three times, and the three were one defect: **a policy arm that PERMITS
when the row's plane cannot be established.**

1. **`idea_revisions` was an unauthenticated existence oracle.** `WITH CHECK` was
   `(thought_id IS NULL OR ob_thought_visible(thought_id))`. Omit the column, the NULL arm
   passes, RLS never refuses — and `idea_revisions_pkey (idea_id, revision)`, which no policy
   binds, answers instead. `ideas` is ungoverned by design, so `GET /ideas` hands over the ids.
   Measured as `service_role`, with an ops control on **both** arms: revision 1 of a personal
   idea `23505`, revision 99 `success`; ops idea identical. Both halves of both policies now
   read `thought_id IS NOT NULL AND ob_thought_visible(thought_id)`.
2. **`agent_memory_audit_events` was the same shape, armed by `ON DELETE SET NULL`.** Round 2's
   parent-visibility policy held only while the parent lived: delete the memory and the audit
   row orphans to `(NULL, NULL)` and becomes readable, carrying its `payload` free text.
   Round 2's own evidence for that arm was wrong — **21** of the live rows have a NULL
   `memory_id`, not 12, and every one is an orphan of a `memory_written` / `memory_used` /
   `memory_confirmed` event, which are events that name a memory by definition. The table is
   now **CLOSED** to the `service_role` door, read and write. Nothing deployed reads it there:
   `authenticated` holds no grant on it, the only PostgREST reader in the repo
   (`integrations/agent-memory-api`) is in no compose file, and both readers of the
   `access_refused` drill evidence (`smoke-agent-memory.ps1`, `dfu-done.ps1`) use
   `docker exec … psql` as `postgres`.
3. **The dependency walk was one edge deep.** §6b joined `pg_rewrite` to `pg_depend` once, so
   it saw a matview sitting directly on a table and nothing else. With one ordinary view in
   between (`matview -> ideas_owed_research -> idea_revisions`) the migration COMMITted while
   the matview returned the hidden row. The walk is now recursive.

**And a fourth, found by the new assertion rather than by reading the schema:**
`agent_memory_recall_traces` carried `WITH CHECK (true)` from 180 — an arm that permits
unconditionally, absence included, on a table holding recall query text. It is narrowed to the
`USING` predicate.

### What actually changed, in one list

| § | change |
|---|---|
| 2 | `idea_revisions`: both policies deny when `thought_id` is NULL |
| 2b | `agent_memory_audit_events`: `USING (false) WITH CHECK (false)`, with a `COMMENT ON POLICY` saying why |
| 2c | `agent_memory_recall_traces`: `WITH CHECK` narrowed from `true` to the ops-plane predicate |
| 6a | `INSERT/UPDATE/DELETE` withdrawn from `service_role` on the **derived** agent-memory corpus (`agent_memories` + its FK children + their governed parents) and on `idea_revisions` |
| 6a2 | two `ORACLE-DISPOSITION` comments on `thoughts_pkey` and `thought_edges_pkey` — the two surrogate-key oracles this file does **not** close, with the trade written down |
| 6b | the matview guard walks `pg_depend` transitively |
| 7 | the post-condition asserts PROPERTIES over a DERIVED population: (h) no policy arm permits an all-NULL row, (i) no unique constraint is an existence oracle, (j) no FK into a governed parent is unguarded by `WITH CHECK`, (k) the `SECURITY DEFINER` set is exactly the classified one, (l) nothing reaches a FORCE-RLS table around the boundary |

### What could break, and why it should not

The write withdrawal is the only behaviour change an operator needs to think about. Measured
before it was written:

* the agent-memory corpus is written by `openbrain-mcp`, which runs `DB_USER=postgres` — a
  superuser, which no policy and no grant here binds;
* `idea_revisions` is written by `openbrain-idea-refinery`, whose container sets **no**
  `DB_USER`, so its code default `postgres` applies;
* the tables that genuinely need a `service_role` write — `thought_entities`,
  `entity_extraction_queue`, `thought_edges`, `entities` — keep every privilege they had, and
  the entity-worker path plus the `thought_edges_upsert` rpc were exercised end to end as
  `service_role` after the change.

§6a also carries a **gate on the gate**: if the corpus derivation ever reaches a relation whose
write path this file deliberately preserves, it RAISES instead of revoking. That check exists
because during testing a probe added a FK from `thought_entities` to `agent_memories`, and the
first version of the derivation silently stripped writes from `thought_entities` **and**
`thoughts` — the ingestion path.

### Preconditions

180, 190 and this file at 200- already applied (round 1 or round 2 state). §0 refuses otherwise.

### Apply

```powershell
docker cp OB1\docker\init-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/init-graph-plane-rls.sql
```

Expect, in order:

```
NOTICE:  init-graph-plane-rls: write door closed on 9 relation(s): agent_memories, ... , idea_revisions
NOTICE:  init-graph-plane-rls: mechanism sweeps run over 13 derived relation(s), floor of 13 satisfied
NOTICE:  init-graph-plane-rls: post-conditions hold on 9 table(s), and the four mechanism sweeps
         (absence, uniqueness, foreign keys, reachability) are clean
COMMIT
```

A `write door closed on` line naming anything outside the agent-memory corpus and
`idea_revisions` is a **stop**: re-read §6a before continuing.

### Verify **by query**, never by a clean exit code

Write each block to a file and run it with `-f`, so no shell quoting sits between the SQL and
the database.

```sql
-- 1. THE PRIVILEGES. Expect exactly `SELECT` for service_role on all nine, and the tier A
--    graph tables still carrying their writes.
SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privs
  FROM information_schema.role_table_grants
 WHERE table_schema = 'public' AND grantee IN ('service_role','authenticated')
   AND table_name IN ('agent_memories','agent_memory_audit_events','agent_memory_recall_traces',
                      'agent_memory_recall_items','agent_memory_relations',
                      'agent_memory_review_actions','agent_memory_source_refs',
                      'agent_memory_artifacts','idea_revisions',
                      'thoughts','thought_entities','entity_extraction_queue','thought_edges')
 GROUP BY 1,2 ORDER BY 1,2;

-- 2. THE POLICIES. Expect `arms that permit on absence: NONE` AND a non-zero policy count -
--    a count of 0 means the population query matched nothing and the result is void. This is
--    section 7(h) run by hand.
DO $$
DECLARE r RECORD; permits BOOLEAN; bad TEXT[] := ARRAY[]::TEXT[];
BEGIN
  FOR r IN SELECT p.tablename, p.policyname, p.qual, p.with_check
             FROM pg_policies p
            WHERE p.schemaname = 'public' AND p.permissive = 'PERMISSIVE'
              AND p.tablename IN (SELECT c.relname FROM pg_class c
                                   WHERE c.relnamespace = 'public'::regnamespace
                                     AND c.relkind = 'r'
                                     AND c.relrowsecurity AND c.relforcerowsecurity
                                     AND c.relname NOT IN ('entities','edges','source_entities',
                                                           'consolidation_log'))
  LOOP
    IF r.qual IS NOT NULL THEN
      EXECUTE format('SELECT COALESCE((%s), false) FROM (SELECT (NULL::public.%I).*) AS %I',
                     r.qual, r.tablename, r.tablename) INTO permits;
      IF permits THEN bad := bad || (r.tablename||'.'||r.policyname||' USING'); END IF;
    END IF;
    IF r.with_check IS NOT NULL THEN
      EXECUTE format('SELECT COALESCE((%s), false) FROM (SELECT (NULL::public.%I).*) AS %I',
                     r.with_check, r.tablename, r.tablename) INTO permits;
      IF permits THEN bad := bad || (r.tablename||'.'||r.policyname||' WITH CHECK'); END IF;
    END IF;
  END LOOP;
  -- array_to_string of an EMPTY array is the empty string, not NULL, so a COALESCE here
  -- would print a blank line for a clean run and for a query that matched nothing alike.
  RAISE NOTICE 'policies checked: %, arms that permit on absence: %',
               (SELECT count(*) FROM pg_policies p
                 WHERE p.schemaname = 'public' AND p.permissive = 'PERMISSIVE'
                   AND p.tablename IN (SELECT c.relname FROM pg_class c
                                        WHERE c.relnamespace = 'public'::regnamespace
                                          AND c.relkind = 'r'
                                          AND c.relrowsecurity AND c.relforcerowsecurity
                                          AND c.relname NOT IN ('entities','edges',
                                                'source_entities','consolidation_log'))),
               CASE WHEN array_length(bad, 1) IS NULL THEN 'NONE'
                    ELSE array_to_string(bad, ', ') END;
END $$;

-- 3. THE OPS PATH IS UNBROKEN. Take these BEFORE and compare AFTER; they must be identical.
--    wiki_pages is DELIBERATELY NOT HERE: the wiki compiler runs on a schedule and its row
--    count moves on its own, so it cannot serve as an identity check.
SELECT 'thoughts' AS t, count(*) FROM thoughts
UNION ALL SELECT 'entities', count(*) FROM entities
UNION ALL SELECT 'thought_entities', count(*) FROM thought_entities
UNION ALL SELECT 'edges', count(*) FROM edges
UNION ALL SELECT 'entity_extraction_queue', count(*) FROM entity_extraction_queue
UNION ALL SELECT 'source_entities', count(*) FROM source_entities
UNION ALL SELECT 'idea_revisions', count(*) FROM idea_revisions
UNION ALL SELECT 'agent_memories', count(*) FROM agent_memories
UNION ALL SELECT 'agent_memory_audit_events', count(*) FROM agent_memory_audit_events;
```

```sql
-- 4. THE ONE THAT MATTERS — the oracle, attacked, with a live positive control, inside a
--    transaction that is ROLLED BACK so nothing persists. All four inserts must return the
--    SAME SQLSTATE. A 23505 on any line means the oracle is open again.
BEGIN;
CREATE OR REPLACE FUNCTION pg_temp.try(p_sql TEXT) RETURNS TEXT LANGUAGE plpgsql AS $$
BEGIN EXECUTE p_sql; RETURN 'OK'; EXCEPTION WHEN OTHERS THEN RETURN SQLSTATE; END $$;
SET LOCAL ROLE service_role;
SELECT 'existing revision' AS arm,
       pg_temp.try(format($f$INSERT INTO public.idea_revisions (idea_id, revision, summary)
                             VALUES (%L, %s, 'probe')$f$,
                          (SELECT idea_id FROM public.idea_revisions LIMIT 1),
                          (SELECT revision FROM public.idea_revisions LIMIT 1))) AS sqlstate
UNION ALL
SELECT 'absent revision',
       pg_temp.try(format($f$INSERT INTO public.idea_revisions (idea_id, revision, summary)
                             VALUES (%L, 99999, 'probe')$f$,
                          (SELECT idea_id FROM public.idea_revisions LIMIT 1)));
SELECT 'audit rows visible to the door' AS arm, count(*) FROM public.agent_memory_audit_events;
RESET ROLE;
SELECT 'audit rows visible to postgres (CONTROL)' AS arm, count(*)
  FROM public.agent_memory_audit_events;
ROLLBACK;
```

Expect: both `idea_revisions` arms the same SQLSTATE (`42501`); `audit rows visible to the
door` = **0**; `audit rows visible to postgres (CONTROL)` = the real row count (72 at the time
of writing). A control of 0 means the probe measured nothing and the result is void.

### Recorded result

**Not yet applied to the live database.** Round 3 was validated on a throwaway built from the
compose-derived 28-file chain, with production's grants replicated onto it. Production still
runs round 1 and therefore still carries every finding this round and round 2 fix.

> **CORRECTED IN ROUND 4.** This paragraph originally justified the grant replication by a
> measured live-vs-fresh drift — "a fresh volume gives `service_role` only `SELECT` on
> `thoughts` and `thought_entities`". **That measurement does not reproduce.** On a genuinely
> fresh volume built from the same 28 files, `service_role` holds `DELETE, INSERT, SELECT,
> UPDATE` on both, identical to production, granted by `init-grants.sql:13` and reduced by
> `init-agent-memory-rls.sql:328`. The replication step was harmless and unnecessary; round 3's
> open item 7 is withdrawn. See the round 4 section below.

### Rollback

`OB1\docker\revert-graph-plane-rls.sql` — extended in round 3 to re-grant the withdrawn write
privileges (to `service_role` only; `authenticated` never held them), restore the wide
`agent_memory_audit_events` policy, restore `agent_memory_recall_traces`' open `WITH CHECK`,
and clear the two `ORACLE-DISPOSITION` comments. **It re-opens the `idea_revisions` oracle and
the audit-orphan leak in addition to everything the earlier rounds' revert re-opens.** The
round trip was executed on the throwaway with production-like grants: GREEN -> revert -> RED
returns on every probe -> re-apply -> GREEN.

```powershell
docker cp OB1\docker\revert-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/revert-graph-plane-rls.sql
```

---

## `init-graph-plane-rls.sql` — ROUND 4: three more catalogue proxies, and a verdict that says what it did not check

**Status: NOT applied to the live database.** Production still runs round 1.

### Why

Rounds 1, 2 and 3 each closed a real leak and each printed the same closing line —
*"post-conditions hold on 9 table(s), and the four mechanism sweeps (absence, uniqueness,
foreign keys, reachability) are clean"*. On 2026-08-31 that line was **reproduced on a
COMMITted apply over a database carrying three live leaks at once**, each measured through the
`service_role` door with an ops positive control in the same session:

| the leak | the proxy that hid it | measured |
|---|---|---|
| `EXCLUDE USING btree (entity_id WITH =, mention_role WITH =)` on `thought_entities` | §7(i) selected on `pg_index.indisunique`, which is **false** for an exclusion constraint's index | collide with the hidden row → `23P01`; free slot, same statement → `INSERT 0 1`; read control → 0 personal / 1 ops |
| a FORCE-RLS **partitioned** relation with `USING (true)` | the sweep population was `relkind = 'r'`; a partitioned table is `'p'` | 1 personal / 1 ops readable through the door |
| an **INVOKER** trigger on `thoughts` copying content + `sha256` into a door-readable table | §7(k) asserted `prosecdef`, and `prosecdef` was **false** | content and fingerprint both returned to `service_role`; flipping `prosecdef` to true turns the same file red |

Plus one absence hole the sweeps could not express: `ob_trace_on_ops_plane` COALESCEs an
**absent** `enforced_exposure` to `["personal"]` (denies) but `[] <@ '["ops"]'` is **TRUE**, so a
recall trace that enforced **nothing** was readable through the door with its query text.

### What actually changed, in one list

1. **§1b — `ob_trace_on_ops_plane` hardened.** Absent, non-array and **empty array** all deny.
   Ops impact measured on the live database first: all 78 live traces carry no
   `enforced_exposure` key, 0 readable before, 0 readable after.
2. **§7 population covers `relkind IN ('r','p')`**, and §7(l)'s reachability base with it.
3. **§7(n) — partition leaves.** A query naming a leaf partition is bound by the leaf, not the
   parent; every leaf of a governed partitioned relation must be RLS-enabled and FORCED or hold
   no door grant.
4. **§7(i) rebuilt off `pg_constraint`** — every `p`, `u` **and `x`** on an in-scope relation,
   UNION every bare unique index no constraint owns. The "columns contain the plane columns"
   escape applies to an exclusion constraint only when every operator is `=`.
5. **§4b + §7(m) — a trigger census.** Every non-internal trigger on a governed relation must
   carry a `TRIGGER-DISPOSITION:` COMMENT naming what it MOVES, whatever its function's
   `prosecdef`, timing or events. §4b writes the five that exist.
6. **§7(h2) + two `NULL-ARM-DISPOSITION` comments.** §7(h)'s claim that "a policy that denies
   the all-absent row cannot have an arm that permits on absence" was **false** — a hole in a
   disjunct beside a false conjunct survives it — and `agent_memories`' surviving
   `thought_id IS NULL OR visible(thought_id)` is exactly that. The arm stays, with the real
   argument (its plane is established by `metadata`/`user_id` in the same conjunction, and
   hidden-vs-nonexistent both answer `42501`) written into the database where the gate reads it.
7. **The closing NOTICE is replaced by a BALANCED CENSUS.** Relations, constraints, triggers and
   functions each land in exactly one bucket — examined by a named sweep, or NOT examined with
   the reason — and the buckets must sum to the catalogue's own totals or the migration fails.
   It ends with *"ABSENCE OF A FINDING ABOVE IS NOT A PROOF OF THE PROPERTY"*. A foreign table
   appearing in `public` makes the census not balance and stops the apply.

### Preconditions

Same as round 3. This file is idempotent and supersedes rounds 1–3 in place; it does **not**
require them to have been applied in order.

### Apply

```powershell
docker cp OB1\docker\init-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/init-graph-plane-rls.sql
```

Expect `COMMIT`, and read the VERDICT block it prints — the *NOT checked* half is the part that
matters.

### Verify **by query**, never by a clean exit code

```sql
-- 1. THE EMPTY-SET TRACE. Expect the ops control back and NOTHING else.
--    A control of zero rows means the probe measured nothing and the result is void.
BEGIN;
INSERT INTO public.agent_memory_recall_traces (workspace_id, query, schema_version, request_payload)
VALUES ('rb-r4','RB-R4 empty enforcement','1','{"enforced_exposure":[]}'::jsonb),
       ('rb-r4','RB-R4 ops control',      '1','{"enforced_exposure":["ops"]}'::jsonb);
SET LOCAL ROLE service_role;
SELECT request_payload->>'enforced_exposure' AS enf, query
  FROM public.agent_memory_recall_traces WHERE workspace_id='rb-r4';
ROLLBACK;

-- 2. THE DISPOSITIONS THE GATE READS. Expect 5 and 2.
SELECT count(*) AS trigger_dispositions
  FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 WHERE NOT t.tgisinternal AND c.relnamespace='public'::regnamespace
   AND COALESCE(obj_description(t.oid,'pg_trigger'),'') LIKE 'TRIGGER-DISPOSITION:%';
SELECT count(*) AS nullarm_dispositions FROM pg_policy pol
 WHERE COALESCE(obj_description(pol.oid,'pg_policy'),'') LIKE 'NULL-ARM-DISPOSITION:%';

-- 3. THE SWEEP POPULATION IS NOT relkind='r'. Expect the same count the VERDICT printed.
SELECT count(*) AS governed FROM pg_class
 WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p')
   AND relrowsecurity AND relforcerowsecurity
   AND relname NOT IN ('entities','edges','source_entities','consolidation_log');

-- 4. THE OPS PATH IS UNBROKEN. Rolled back; expect every step to succeed.
BEGIN;
SET LOCAL ROLE service_role;
INSERT INTO public.thoughts (content, metadata)
  VALUES ('RB-R4 ops thought','{"exposure":"ops"}'::jsonb);
SELECT count(*) AS queue_row_visible FROM public.entity_extraction_queue
 WHERE thought_id = (SELECT max(id) FROM public.thoughts);
ROLLBACK;
```

Expect: probe 1 returns **only** the `["ops"]` row; probe 2 returns **5** and **2**; probe 3
matches the VERDICT's *governed* count; probe 4's `queue_row_visible` = **1**.

### Recorded result

**Not yet applied to the live database.** Round 4 was validated on two throwaways built from the
compose-derived 28-file chain (28 mentions = 28 pairs = 28 staged), on their own network, never
attached to an `ai-stack_*` anchor network. RED before GREEN on all four leaks, each with a live
ops positive control; sixteen adversarial cases, each red for its own reason; three consecutive
applies COMMIT; revert round trip GREEN → RED → GREEN; the full chain runs clean on a genuinely
fresh volume; `test-quartz4-offline.ps1` reports ALL OFFLINE CHECKS PASSED.

**A correction to round 3's recorded result, which was a measurement error.** Round 3 recorded a
*live-versus-fresh grant drift* — "a fresh volume gives `service_role` only `SELECT` on
`thoughts` and `thought_entities`" — and replicated production's grants onto its throwaway
because of it. **Not reproducible.** Measured 2026-08-31 on a genuinely fresh volume built from
the same 28 files, and on production, with the same query:

```
                    fresh volume                        live openbrain-db
thoughts         service_role DELETE,INSERT,SELECT,UPDATE   DELETE,INSERT,SELECT,UPDATE
thought_entities service_role DELETE,INSERT,SELECT,UPDATE   DELETE,INSERT,SELECT,UPDATE
thought_entities authenticated SELECT                       SELECT
```

They are **identical**. The grant comes from `init-grants.sql:13`
(`GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role`, mounted at `050-`, after `thoughts`
is created at `010-` and `thought_entities` at `040-`), reduced to four privileges by
`init-agent-memory-rls.sql:328`'s `REVOKE TRUNCATE, REFERENCES, TRIGGER`. There is no drift, the
grant-replication step was compensating for nothing, and round 3's open item 7 is **withdrawn**.

### Rollback

`OB1\docker\revert-graph-plane-rls.sql` — extended in round 4 with §8 (restore 180's
`ob_trace_on_ops_plane`, which **re-opens the empty-set trace disclosure**) and §9 (clear the
`NULL-ARM-DISPOSITION` and `TRIGGER-DISPOSITION` comments, so a re-apply proves it wrote them
rather than inheriting them). Round trip executed on the throwaway: GREEN → revert → the
empty-set trace is readable again and all seven dispositions are gone → re-apply → GREEN.

```powershell
docker cp OB1\docker\revert-graph-plane-rls.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/revert-graph-plane-rls.sql
```
