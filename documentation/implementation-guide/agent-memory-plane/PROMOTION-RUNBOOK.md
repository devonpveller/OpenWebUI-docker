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

`OB1\docker
evert-agent-memory-corpus-failclosed.sql` -- re-opens the predicate, **then**
unstamps exactly the rows this migration stamped (matched on
`metadata->>'exposure_backfill' = 'dfu-c8-corpus-failclosed'`, so the 4 rows labelled `ops`
by the write path are left alone). Nothing is dropped and no row is deleted.

```powershell
docker cp OB1\docker
evert-agent-memory-corpus-failclosed.sql openbrain-db:/tmp/
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
NOTE (corrected 2026-08-31): the round-3 apply printed the NOTICE below. Round 4 DELETED
that sentence as a defect - it claimed a completeness the gate cannot have, and was printed
three times while three live oracles existed. The shipped file now prints a balanced census
naming what was checked AND what was not. Expect that instead; the line below is historical.
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



## `init-graph-plane-rls.sql` — ROUND 5: it reads the COLUMN, and one policy's role is put back

**Same file, same `200-` mount point, not a new migration. NOT APPLIED to the live volume —
the live volume is still on ROUND 1 (measured 2026-08-31: `ob_relation_governed` absent,
`agent_memory_audit_events` still carrying `USING (true)`), and promoting rounds 2–5 is the
`u5graph` item's deployment, not H3's.** Recorded here because the file changed and the
two-place invariant is about the FILE reaching both places, not about who applies it.

### Why

`195-init-agent-memory-exposure-column.sql` made `exposure` the source of truth. Four sites in
this file still read the jsonb mirror, and a mirror is not a trust decision:

* both `agent_memories` policies (§2c) — `ob_memory_on_ops_plane(metadata)` → `(exposure)`
* the tier-B containment sweep (§3) — `ob_corpus_on_ops_plane(t.metadata)` → `(t.exposure)`
* the entity-extraction write gate (§4) — `(NEW.metadata)` → `(NEW.exposure)`. The `COALESCE`
  around it STAYS and its comment is extended: a committed row can never reach that gate with
  a NULL, but a BEFORE INSERT trigger runs before the constraints do, so `NEW.exposure` is
  whatever the statement supplied — NULL for a writer that omitted it — and `NOT NULL` is NULL,
  which an `IF` does not take. One statement's worth of trigger is enough.

§0's precondition changes with them: it now requires the COLUMN to exist, to be `NOT NULL`, to
carry a CHECK restricting it to `ops`/`personal`, and the predicate to deny a plane it cannot
establish. Checking the jsonb key instead would be this file trusting the mirror H3 retired.

### And one thing that is not about H3 at all

`agent_memories_personal_plane` is restored **`TO ob_plane_personal`**. 180 created it that way;
the round that added the `thought_id` arm DROPped both policies and recreated **both**
`TO service_role`. Because `ob_plane_personal` is a member of `service_role` everything kept
working and nothing went red — while the policy had widened from "the personal plane, for its
own tenant" to "any `service_role` session, for any tenant it can name". `ob.user_id` is an
ordinary GUC with no privilege attached to setting it.

Reproduced on a throwaway with a RED, as `service_role` with `SET LOCAL ob.user_id` = the
fixture's tenant:

```
TO service_role      -> D_RED_personal_via_tenant=1   D_RED_summary_leaked=H3PROBE personal
TO ob_plane_personal -> E_restored_personal_via_tenant=0
```

`thoughts_personal_plane` was never touched by this file and still names `ob_plane_personal`,
which is why the same probe against `thoughts` returned nothing — one table drifted, its twin
did not, and that asymmetry is what made it findable. `prove-agent-memory-rls.ps1` now asserts
the property for **every** `*_personal_plane` policy in the schema, from `pg_policies`, so a
future round cannot widen one silently.

### §9 — the retired predicates are unreached

The jsonb-argument predicates are **kept** (`revert-graph-plane-rls.sql` recreates a policy that
calls one, so dropping them would break a revert path that already ships) and asserted to be
CALLED BY NOTHING — over `pg_depend` for declarative callers AND over every function body in
`public` for opaque ones, because a plpgsql body records no dependency at all. That is not a
detail: `queue_entity_extraction()` called this predicate for the whole of its life and
`DROP FUNCTION` would not have caught it. A positive control asserts both functions still
exist, so the sweep cannot pass vacuously.

### Apply / Verify / Rollback

Unchanged from round 4 — same file, same command, same verification block. Add to that block:

```powershell
# the policies read the COLUMN and no policy on either table reads metadata
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT tablename||'.'||policyname||' -> '||COALESCE(qual,'-')
     FROM pg_policies WHERE schemaname='public'
      AND tablename IN ('agent_memories','thoughts') ORDER BY 1;"
# expect: ob_memory_on_ops_plane(exposure) / ob_corpus_on_ops_plane(exposure), and both
# *_personal_plane rows granted TO {ob_plane_personal}
```

Applied to a throwaway built from the full 29-migration chain, twice: no init errors, §9's
notice printed, `prove-agent-memory-rls.ps1` 68/68 and the personal-plane drill green on
containment against it.

**`prove-agent-memory-rls.ps1` 68/68 is true only when it does not overlap `dfu-done.ps1
-Only 3`, and C.9 H4 wires BOTH into CI.** `prove-agent-memory-rls.ps1` ends with a
*production* assertion — `personal_memories=0` / `personal_thoughts=0` on the live
`openbrain-db`, by the mirror and by the column — and `dfu-done.ps1` clause 3 **plants a
personal-exposure fixture in that same live database** (`$DbContainer = "openbrain-db"`) for the
duration of its door probes. Run them concurrently, or run `prove-rls` while a `dfu-done` run
is mid-clause, and `prove-rls` exits **1** with `production is not clean, or the column and the
mirror disagree` — a true reading of the database at that instant, and not a defect in either
script. **CI must serialise them** (or give `dfu-done` its own database); a retry loop or an
`|| true` on `prove-rls` would delete the only check that watches the live plane. **Quote the drill's result in full, including the gaps and the exit
code** — see "The drill's exit code, and what CI reads" below. An earlier version of this line
said "105 passes / 0 failures" and stopped there, which reads as a pass; the run also reports
18 named gaps and EXITS 2.

## `init-agent-memory-exposure-column.sql` — the exposure label becomes a TYPED COLUMN (DFU C.9 H3)

**Mounted at `195-`, between `190-` and `200-`. APPLIED to the live volume 2026-08-31 under an
`open-brain` plane lease, VERIFIED, and then REVERTED in the same window — see "Why it is not
live yet" below. It is not a rollback of a failure: the migration did exactly what it claims.**

### Why

Operator decision, PLAN.md §C.9 (2026-08-31), option **A**: exposure is a security
discriminator and must not live at a weaker strength than tenancy, which is already a typed
column (`user_id`). A jsonb key cannot be constrained, so *unlabelled* and *misspelled* were
both reachable states resolved at READ time by a fail-closed predicate. Fail-closed is the
right error direction and it is not a write contract — a producer that forgot the label got a
row that silently vanished from the plane it meant to write to.

After this file `agent_memories.exposure` and `thoughts.exposure` are
`TEXT NOT NULL CHECK (exposure IN ('ops','personal'))`, **the predicates read the COLUMN**, and
`metadata->>'exposure'` is a non-authoritative mirror.

**No DEFAULT, deliberately — and no default at the DOOR either.** A default would make the
NOT NULL unreachable from a writer that omits the column, which is the failure H3 exists to
remove. The first version of this migration then implemented that exact semantics one layer up:
`upsert_thought` COALESCEd an absent, empty or null `metadata.exposure` to `'ops'`, so `{}`,
`{"exposure":""}` and `{"exposure":null}` all wrote a row on the WIDER plane (measured on a
throwaway from the full chain, 2026-08-31: all three succeeded). §C.9 H3 says "a writer that
does not supply the column is rejected by the CHECK, which is the point", and the operator's
ruling is "forcing every producer to state exposure explicitly is the intent, not a side
effect" — so the door now REFUSES all of them, loudly, with the reason.

A *door* may still stamp its own forced value — `stampExposure()` does, and its unstated value
is `'personal'`, the NARROW end, and it can only ever demote. That is not what `upsert_thought`
was doing: filling in the WIDE plane for a caller that said nothing is not stamping, it is
guessing. **Every one of the ten `upsert_thought` callers in the tree now states `exposure:
'ops'` at its own call site**, so the choice is visible in the producer's code rather than
hidden in a COALESCE in the migration.

### What it does, in the only order that is safe

Every step is inside ONE transaction, so no other session observes any intermediate state.

1. **Assert** 180 and 190 are applied and the jsonb corpus predicate is already fail-closed.
2. **ADD COLUMN**, nullable, no default. No predicate reads it, so no row's visibility changes.
3. **BACKFILL** from the jsonb key. Still a write to a column nothing reads.
4. **VERIFY zero NULL**, and RAISE otherwise. This is the gate that makes step 5 safe.
5. **NOT NULL, then CHECK.** Both: `CHECK (exposure IN (...))` is NULL-permissive on its own.
6. **Define** the column-reading predicates (TEXT overloads).
7. **Swap the policies.** The only step that changes what a predicate reads — and its
   transient state, between `DROP POLICY` and `CREATE POLICY`, is *RLS enabled with no
   permissive policy*, i.e. default DENY. There is no ordering here that permits a row.
8. **Re-point `upsert_thought`** — the shared rpc door for wiki synthesis, entity-wiki and
   every import recipe — at the column, and make it REQUIRE the plane. Absent and JSON null are
   a `not_null_violation`; `''`, `' '`, `'ops '`, `' ops'`, `'OPS'`, `'Ops'`, `'opsy'` and
   `'"ops"'` are a `check_violation`; `'ops'` is the only accepted value.

   **`'personal'` is refused too, and that is a correction, not a narrowing for its own sake.**
   The door used to "honour an explicit demotion". Measured, it cannot deliver one:
   `thoughts_personal_plane` is granted `TO ob_plane_personal` and requires
   `user_id = ob_current_user_id()`, and this door has neither — so a BOUND connection's
   personal insert through it is refused `42501` by the WITH CHECK, and a SUPERUSER's succeeds
   only by bypassing the boundary and writes a row with `user_id IS NULL` that no personal-plane
   session can ever read. Both are worse than a refusal that names the real path.

   The UPDATE branch still does not re-decide an existing row's plane, and it now writes
   `metadata.exposure` **from the row's COLUMN** in the same statement — so the two cannot
   disagree, and a row that arrives disagreeing is repaired. See "the mirror" below.
8b. **Re-point the LAST READER of the mirror** — `queue_entity_extraction()` — at the column,
   with `200-`'s body verbatim, and **widen `trg_queue_entity_extraction` to fire on
   `UPDATE OF content, metadata, exposure`**. Neither changes what any caller can SEE.
9. **Self-test, before COMMIT:** an absent write and a malformed write are attempted on BOTH
   tables and must be refused by the database; the DOOR is attacked with all twelve non-plane
   payloads and must refuse each; `'ops'` must be accepted and must leave column and mirror
   equal; both mirror-desync cases are red-proven closed; the database is scanned (over
   `pg_policies` AND `pg_proc.prosrc`) for any remaining reader of the mirror; and a final block
   proves no probe row survived.

**What an absent key backfills to: `ops`. A malformed one: the migration REFUSES.** Absent was
already ruled on and executed by `190-` (which stamped ~13,000 rows `ops` so the corpus stayed
readable); reproducing that decision is the only choice that does not silently move rows.
Malformed is different — a producer stated a plane and stated a non-plane, no prior decision
covers it, and this file will not invent one. Measured live before the run: **0 malformed**.

### Apply

`195-` must come **after** `190-` and **before** `200-`.

```powershell
docker cp OB1\docker\init-agent-memory-exposure-column.sql openbrain-db:/tmp/195.sql
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/195.sql
```

### Verify **by query**, never by a clean exit code

```powershell
# 1. NO ROW MOVED. Take these BEFORE and compare AFTER; they must be identical.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT (SELECT count(*) FROM thoughts) || '/' ||
          (SELECT count(*) FROM agent_memories) || '/' ||
          (SELECT count(*) FROM entities) || '/' ||
          (SELECT count(*) FROM thought_entities) || '/' ||
          (SELECT count(*) FROM entity_extraction_queue);"

# 2. THE OPS PATH IS UNBROKEN - as the agent plane, not as the superuser.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; SET LOCAL ROLE service_role;
   SELECT 'thoughts='||count(*) FROM thoughts;
   SELECT 'memories='||count(*) FROM agent_memories; COMMIT;"

# 3. THE COLUMN IS TOTAL, AND HAS NO DEFAULT. Expect NO/none twice.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT table_name||' '||is_nullable||'/'||COALESCE(column_default,'none')
     FROM information_schema.columns
    WHERE table_schema='public' AND column_name='exposure' ORDER BY table_name;"

# 4. THE ONE THAT MATTERS - the DATABASE refuses an absent and a malformed write, with a
#    live positive control beside each. Rolled back; nothing persists.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; INSERT INTO thoughts (content, metadata)
     VALUES ('H3-ABSENT', jsonb_build_object('exposure','ops')); ROLLBACK;"
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; INSERT INTO thoughts (content, exposure) VALUES ('H3-BAD','opsy'); ROLLBACK;"
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; INSERT INTO thoughts (content, exposure) VALUES ('H3-OK','ops');
   SELECT 'control accepted'; ROLLBACK;"

# 5. THE DOOR REFUSES WHAT THE COLUMN REFUSES - the check that would have caught the
#    COALESCE. Expect three ERRORs and then 'ops/ops'. Rolled back; nothing persists.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; SELECT upsert_thought('H3-DOOR-ABSENT','{}'::jsonb); ROLLBACK;"
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; SELECT upsert_thought('H3-DOOR-EMPTY','{\"metadata\":{\"exposure\":\"\"}}'::jsonb); ROLLBACK;"
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; SELECT upsert_thought('H3-DOOR-NULL','{\"metadata\":{\"exposure\":null}}'::jsonb); ROLLBACK;"
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "BEGIN; SELECT r.exposure||'/'||(r.metadata->>'exposure')
     FROM upsert_thought('H3-DOOR-OK','{\"metadata\":{\"exposure\":\"ops\"}}'::jsonb) r; ROLLBACK;"

# 6. NOTHING READS THE MIRROR. Expect two empty results.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT tablename||'.'||policyname FROM pg_policies WHERE schemaname='public'
     AND replace(COALESCE(qual,'')||COALESCE(with_check,''),' ','')
         LIKE ANY (ARRAY['%metadata->>''exposure''%','%on_ops_plane(metadata)%']);"
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prokind='f' AND p.proname<>'upsert_thought'
      AND replace(COALESCE(p.prosrc,''),' ','')
          LIKE ANY (ARRAY['%metadata->>''exposure''%','%on_ops_plane(NEW.metadata)%']);"

# 7. THE TRANSITION TRIGGER FIRES ON THE COLUMN. Expect the definition to name `exposure`.
docker exec openbrain-db psql -U postgres -d openbrain -tAc `
  "SELECT pg_get_triggerdef(oid) FROM pg_trigger
    WHERE tgname='trg_queue_entity_extraction' AND tgrelid='public.thoughts'::regclass;"
```

**Recorded results, live volume, 2026-08-31 20:30 UTC (lease `open-brain`, owner `u8h3`):**

| | before | after |
|---|---|---|
| thoughts / agent_memories / entities / thought_entities / queue | 13001 / 21 / 70122 / 54063 / 13001 | **identical** |
| as `service_role`: thoughts / memories | 13001 / 21 | **identical** |
| thoughts on the ops plane | 13001 (mirror) | 13001 (**column**) |
| personal rows, either table | 0 | **0** |
| rows the absent-key branch had to backfill | — | **0** (190 had already labelled every row) |

(3) `agent_memories NO/none`, `thoughts NO/none`.
(4) `null value in column "exposure" ... violates not-null constraint` /
`violates check constraint "thoughts_exposure_check"` / `control accepted`.
The migration's own pre-COMMIT self-test printed
`self-test passed - the DATABASE rejects an absent exposure (not_null_violation) and a
malformed one (check_violation) on BOTH tables`.

### Why it is not live yet — and this is the important row in this section

**The schema half and the code half MUST land together, and the code half is a gated deploy.**
`openbrain-mcp` runs `openbrain-mcp-server:local`, and the image deployed on 2026-08-31 still
contains three `INSERT INTO thoughts (content, embedding, metadata)` statements with no
`exposure`. Under this migration those are a `not_null_violation` — measured on the live volume
while it was applied:

```
ERROR:  null value in column "exposure" of relation "thoughts" violates not-null constraint
DETAIL: Failing row contains (13744, H3-LIVE-DEPLOYCHECK, ...)
```

So applying `195-` without rebuilding `openbrain-mcp-server:local` breaks `capture_thought`,
`capture_idea`, `update_idea` and the whole agent-memory writeback. The migration was therefore
**reverted in the same window** and the live volume returned to the state it was found in
(counts above, verified identical; the deployed write path re-tested and working). Rebuilding a
`:local` tag and recreating a production container is a gated deploy, not a test
(`scripts/agent-harness/README.md`), and that gate was not taken here.

**To promote, in ONE window, under an `open-brain` lease:**

1. `docker build -t openbrain-mcp-server:local OB1/integrations/kubernetes-deployment`
   (from the tree the parent's OB1 gitlink pins — that is what a merge ships).
2. Apply `195-` as above.
3. Recreate `openbrain-mcp` (and `openbrain-ext`, which shares the image context's base but
   changes nothing here).
4. Re-run verification 1–4, then `scripts/checks/prove-agent-memory-rls.ps1`.

The order inside the window is not free: build first so the recreate is instant, and apply the
migration immediately before the recreate, because the interval between them is the interval in
which the deployed code cannot write the corpus. It fails CLOSED — writes are refused, nothing
is disclosed — but it is an outage, so keep it to seconds.

**`200-init-graph-plane-rls.sql` on the live volume is still ROUND 1** (measured 2026-08-31:
`ob_relation_governed` is absent and `agent_memory_audit_events` still carries a
`USING (true)` policy). `195-` does not depend on it and does not conflict with it — but **the
argument for that changed, because the one it used to rest on was false.**

It used to read: *"the round-1 write gate reads the jsonb mirror, which every writer keeps in
step with the column."* That is a premise about writer discipline, and the door this very
migration was shipping falsified it in both directions. `upsert_thought`'s UPDATE branch merged
the caller's `exposure` into `metadata` while deliberately not touching the column, so
re-upserting a personal row with no exposure key produced `column='personal'` /
`mirror='ops'` — and the round-1 gate is `ob_corpus_on_ops_plane(NEW.metadata)`, `SECURITY
DEFINER`, so it would have QUEUED that personal thought's content fingerprint into
`entity_extraction_queue`. Not tidiness: a carry across the boundary, through the gate that
exists to stop one. (`generate-wiki.mjs`'s idempotent dossier PATCH replaced `metadata`
wholesale with an object that had no `exposure` key, deleting the mirror on every compile —
same class, on the only scheduled producer.)

**So the premise was replaced with a property.** `195-` §7b re-points
`queue_entity_extraction()` at the COLUMN in the same transaction that creates it, and §8(d)
asserts — over `pg_policies` for declarative readers and over `pg_proc.prosrc` for opaque plpgsql
bodies — that **nothing in the database reads `metadata->>'exposure'` for a trust decision.**
Measured on the live volume before the change: that gate was the *only* function body reading
it. After `195-`, **no policy and no function body other than the two retired jsonb predicates
themselves** reads the mirror — those two (`ob_memory_on_ops_plane(md jsonb)`,
`ob_corpus_on_ops_plane(md jsonb)`) read it *by construction*, are kept because the 190/200
revert paths recreate policies that call them, and are invisible to §8(d)'s scan, whose anchors
are the literal `metadata->>'exposure'` and `on_ops_plane(metadata)` — neither of which matches
`md->>'exposure'`. That **nothing calls them** is the property that matters and it is asserted
by `200-` §9, over `pg_depend` and over every function body, with a positive control. So a
desync cannot decide anything — and the door can no longer produce one anyway. (§8(d)'s notice
used to say "the mirror has zero readers", which was section 9's conclusion borrowed by a scan
that could not reach it; corrected 2026-08-31.)

`195-` §7b also widens `trg_queue_entity_extraction` to `UPDATE OF content, metadata, exposure`.
`200-`'s own TRIGGER-DISPOSITION comment claims "an ops-to-personal transition deletes the
existing one"; with the plane in a COLUMN, the only way to make that transition is
`UPDATE thoughts SET exposure='personal'`, which touches neither `content` nor `metadata` and
did not fire the trigger at all. RED on a throwaway: 1 queue row before, 1 queue row after the
demotion, carrying `sha256` of now-personal content. GREEN after: 0.

Promoting the work line's `200-` is the `u5graph` item's deployment, not this one's, and it must
go **after** `195-` because its policies, sweeps and write gate read the column.

### Rollback

`OB1\docker\revert-agent-memory-exposure-column.sql`. In order: it refuses if `200-` is still
applied or the jsonb predicates are gone; then (§1a, **new**) **repairs the mirror from the
column and REFUSES if any row still disagrees**; then re-points the policies at the jsonb
predicate; then de-constrains the column; then restores `upsert_thought`; then (§3b, **new**)
puts `queue_entity_extraction` back on the mirror and narrows the trigger's column list again;
then unstamps exactly the rows this migration labelled
(`metadata->>'exposure_backfill' = 'dfu-h3-exposure-column'`).

**§1a exists because the step after it used to justify itself with "the mirror is complete at
this moment — the doors keep it in step", and that was the same false premise the no-conflict
argument rested on.** Re-pointing the policies at a mirror that disagrees with the column is a
**silent widening** of every row whose column says `personal` and whose mirror says `ops`. The
column is the source of truth, so the revert copies it into the mirror before trusting the
mirror — which changes no visibility, because at that moment the policies still read the
column — and then asserts zero disagreement.
**The column is NOT dropped** — dropping a column is not reversible and PLAN class 4 forbids it;
it is left in place, populated and inert, and a re-apply is idempotent.

It **refuses** to run while `200-`'s `thought_id` arm is present on `agent_memories_ops_plane`:
reverting in that order would silently re-open the foreign-key existence oracle. Revert `200-`
first in that case.

```powershell
docker cp OB1\docker\revert-agent-memory-exposure-column.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 `
    -f /tmp/revert-agent-memory-exposure-column.sql
```

Round trip executed twice on a throwaway built from the full 29-migration chain (apply →
revert → re-apply → re-apply again, all clean and idempotent) and once on the live volume
(apply → verify → revert → verify against the before-snapshot, identical).

### The drill's exit code, and what CI reads (C.9 H4)

`scripts/checks/drill-personal-plane-exclusion.ps1` against the tree this gitlink pins reports
**0 failures and 18 GAPs, and EXITS 2.** That is deliberate — U5's column asks for
"mechanically stopped **and** the attempt is visible in an audit record", and the recording half
is not met — but **H4 wires this drill into CI on `development`, where 2 is a failing build.**
Left alone, the first green tree to hit that gate goes red for a reason that is not a defect,
and the first fix anyone reaches for is `|| true`, which deletes the gate.

**None of the 18 are H3's to close.** Thirteen are the audit-record gap in its various doors:
`auditRefusal` fires only after a bare `SELECT 1 FROM agent_memories WHERE id=$1` confirms the
row exists, and that probe is bound by the same policy that hid it — so a non-superuser door
writes no record, and a superuser door records but stops nothing. Closing it needs an elevated
existence probe (`SECURITY DEFINER`, answers "exists" without returning the row), which is an
H1/H4 decision. Three are the `openbrain-ext` container: it connects as `postgres`, leaks the
personal row verbatim and copies it into `professional_contacts.notes` — H1, measured. Two are
the lift's own conjunction, which cannot close while the other thirteen are open.

So the drill now carries a **gap ledger**: every gap has a stable id, and `$GAP_DISPOSITIONS`
names each one with the item that owns it. The exit contract is:

| invocation | outcome |
|---|---|
| bare run | **unchanged** — exit 2 with any gap open |
| `-AcceptDispositionedGaps` | exit **0** iff every gap that fired is in the ledger |
| `-AcceptDispositionedGaps`, a gap fired that is **not** in the ledger | exit **2**, naming it |
| a gap in the ledger did **not** fire (and not `-SkipRed`) | exit **1** — the ledger has rotted |

**CI runs it with `-AcceptDispositionedGaps`.** A count-based budget would have let "17 old + 1
new" pass; the ledger is by name, so a new gap is red, and a gap that closes forces the ledger
(and this section) to be updated rather than quietly drifting.

> **CORRECTED, ROUND 4 (2026-08-31).** This paragraph used to end: *"When H1 closes the
> audit-record gap, the run goes RED until the corresponding ids are deleted from
> `$GAP_DISPOSITIONS` — that is the intended behaviour, not a bug to route around."* **It is no
> longer the behaviour, and it should not have been.** A gate that turns red when you fix
> something teaches people to stop fixing things — and the first VACUOUS gap actually closed
> (`VACUOUS-WIKIPAGES`) would have cost an exit-1 build for the privilege.
>
> The rule the red was protecting is still there; it just tells the two silences apart now. A
> dispositioned gap that stops firing is either **CLOSED** — its assertion RAN and reached a
> verdict, which is good news, printed loudly with "PULL THESE PINS", and worth **zero**
> failures — or **VANISHED**, meaning nothing with that id was measured at all, which is still a
> FAIL and is the case the rule was written for. `Split-StaleGaps` is a pure function and
> `-SelfTestLedger` forces it through five classifications, including the one that matters: an
> empty closed-map cannot classify anything as closed.
>
> **The cost is stated rather than hidden:** a CLOSED pin is a nag, not a gate, so a pin for a
> genuinely closed property can sit in the ledger indefinitely. That is the conservative error —
> the ledger then over-reports an open gap — and every run prints it.

**AND EVERY DISPOSITIONED VACUITY NOW CARRIES ITS COST.** Under `-AcceptDispositionedGaps`,
six assertions that measure nothing were formally inside an exit-0 green: CI was asserting less
than it had before, while looking identical. Each `VACUOUS-*` entry now ends with **GREEN DOES
NOT COVER:** — the specific thing a passing CI run fails to rule out because that assertion is
empty. A reader can price the green from the ledger without reading the drill.

### The write contract reaches the RECIPES *and the images*, and it reaches more than the RPC callers

`195-` changes what a producer must send, and the producers are not all in the image — nor are
they all RPC callers. **This section used to say "the write contract reaches the RECIPES" and
then describe only the ten `upsert_thought` call sites. That was the sweep talking, not the
tree:** the sweep behind it was `grep -rn 'rpc("upsert_thought"' OB1`, so RPC callers were the
only thing it could return. There are **twelve DIRECT-table producers** as well — they POST at
`/rest/v1/thoughts` or call `supabase.from("thoughts").insert()` — and this door is in front of
none of them. See `documentation/notes/u5-live-producer-rls-regression.md`; the set is now
derived on every commit by `scripts/checks/check-corpus-exposure-producers.ps1`.

> **AND THAT CHECK IS NOT THE ENFORCEMENT (round 4).** It is authoring-time convenience. **The
> enforcement is `195-` itself** — `exposure` is NOT NULL with no default and CHECKed on both
> tables, and `upsert_thought` refuses a payload that omits it, which rejects an unlabelled
> write in every shape and language, forever. The check only moves *some* of those refusals to
> commit time, for producers written in the shapes it recognises. Two verifiers planted
> producers it cannot see (a table name in a variable, a concatenated path, a helper wrapper, a
> `.tsx` copy, `curl -X POST` in a `.sh`, supabase-py `.table().insert()`) and it neither
> flagged nor counted them. **A producer it cannot see breaks production, not the build** — and
> quietly, because both failing producers catch the 42501 and carry on. The check prints its own
> blind spots on every run (`-ShowShapes`); read those before treating a green as coverage.

**Two producers whose rejection is an UNHANDLED OUTAGE, not a caught contract change:**

| producer | how it ships | what a refusal does |
|---|---|---|
| `recipes/entity-wiki/generate-wiki.mjs` (`openbrain-wiki`) | **bind mount** `../recipes:/recipes:ro` | the dossier upsert throws; the compile fails per entity |
| `docker/wiki-service/wiki-service.mjs` (`openbrain-wiki`) | **`COPY`d into `openbrain-wiki:local` at build time** (`docker/wiki-service/Dockerfile`) | note ingest fails per file; the run continues, silently ingesting nothing |
| `recipes/email-history-import/pull-gmail.ts` (`openbrain-gmail-pull`) | bind mount | the daily pull reports `Ingested: 0` and an error; **this is live today** |

So **"no rebuild, no restart" is FALSE for `wiki-service.mjs`.** It is the same container as
the bind-mounted recipe, which is exactly why the sentence read as covering it: moving the
deployment checkout's submodule fixes `generate-wiki.mjs` and does nothing at all for
`wiki-service.mjs`, whose copy is baked into `openbrain-wiki:local`. That one needs
`docker build -t openbrain-wiki:local OB1/docker/wiki-service` and a recreate — a gated
deploy, in the promotion window, not a checkout move.

For the bind-mounted producers the fix still lands when the deployment checkout's OB1
submodule is moved to this gitlink — no rebuild, no restart, but also **no protection from a
stale checkout**: an operator who applies `195-` without moving the submodule gets a wiki
compile that fails at the door on every dossier. Move the submodule first, apply second, and
rebuild `openbrain-wiki:local` in the same window.

