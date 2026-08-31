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
