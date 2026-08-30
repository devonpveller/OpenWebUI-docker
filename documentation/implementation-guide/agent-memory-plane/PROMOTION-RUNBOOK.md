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

Expect `promote_exposure_allowed|1`. Rollback is the same `ALTER` with the original
nine-value list; no row becomes invalid unless a `promote_exposure` action has been written.
