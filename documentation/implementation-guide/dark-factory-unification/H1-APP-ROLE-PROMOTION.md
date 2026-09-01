# H1 — no application connects as a superuser: the promotion plan

> Status: **VALIDATED ON THROWAWAYS, NOT DEPLOYED.** Nothing here has been applied to
> `openbrain-db`, no compose file has been edited, no production credential has changed.
> Phase U8, DFU PLAN §C.9.
>
> Artefacts: `OB1/docker/init-app-role.sql`, `OB1/docker/init-app-role-passwords.sh`,
> `OB1/docker/revert-app-role.sql`, `scripts/checks/drill-app-role-not-superuser.ps1`,
> `scripts/checks/census-db-connection-roles.ps1`.

## 1. What was measured, not assumed

`scripts/checks/census-db-connection-roles.ps1`, run read-only against `openbrain-db`
on **2026-08-31T21:27-04:00**:

```
superuser / bypassrls backends: 22 of 22
```

| container | role | application_name | n |
|---|---|---|---|
| openbrain-curator | postgres | deno_postgres | 8 |
| openbrain-postgrest | postgres | PostgREST 12.2.3 | 3 |
| openbrain-workbench | postgres | deno_postgres | 3 |
| openbrain-research | postgres | deno_postgres | 2 |
| openbrain-chunk-worker | postgres | deno_postgres | 1 |
| openbrain-grounding-backfiller | postgres | deno_postgres | 1 |
| openbrain-idea-refinery | postgres | deno_postgres | 1 |
| openbrain-mcp | postgres | deno_postgres | 1 |
| open_notebook | postgres | *(asyncpg, no app name)* | 1 |
| (local) | postgres | psql | 1 |

Every one is `rolsuper = t`, `rolbypassrls = t`. This reproduces the 22/22 the plan records.

**A census alone is not the denominator.** `openbrain-ext` and `openbrain-suggestion-worker`
hold lazy pools and were connected in neither run, while being configured to connect as
`postgres` exactly like the rest. The census therefore also reads the compose files and
reports **12 services configured** to connect as `postgres` — including
`openbrain-idea-refinery`, which sets **no `DB_USER` at all** and reaches `postgres` through
`Deno.env.get("DB_USER") || "postgres"`. A sweep that only read explicit `DB_USER` lines would
have called it not-a-client.

## 2. The classification — derived from each client's queries

Every client below was classified by reading its runtime SQL (tests excluded). A scan for
`CREATE`/`ALTER`/`DROP`/`NOTIFY`/`LISTEN`/`GRANT`/`information_schema`/`pg_catalog` across all
nine direct-client source trees returns **nothing outside test files and the initdb chain** —
so no direct client needs DDL, extensions or NOTIFY. They are data-plane, all of them.

| client | source read | verdict | role |
|---|---|---|---|
| `openbrain-mcp` | `OB1/integrations/kubernetes-deployment/*.ts` | data-plane; writes `agent_memories` + 4 sidecars + `idea_revisions`; writeback default `exposure='personal'` | **`ob_app_memory`** |
| `openbrain-workbench` | `OB1/docker/workbench/src/**` | data-plane (sources, threads, chunks, wiki_pages) | `ob_app` |
| `openbrain-curator` | `OB1/integrations/research-curator/*.ts` | data-plane (claims, threads, sources) | `ob_app` |
| `openbrain-research` | `OB1/integrations/research-service/*.ts` | data-plane (claims, sessions, research_jobs) | `ob_app` |
| `openbrain-chunk-worker` | `OB1/integrations/chunk-embedding-worker/*.ts` | data-plane (sources, source_chunks) | `ob_app` |
| `openbrain-grounding-backfiller` | `OB1/integrations/grounding-backfiller/*.ts` | data-plane (claims, sources) | `ob_app` |
| `openbrain-suggestion-worker` | `OB1/integrations/suggestion-worker/*.ts` | data-plane (sources, threads, thread_sources) | `ob_app` |
| `openbrain-idea-refinery` | `OB1/integrations/openbrain-idea-refinery/*.ts` | data-plane; `UPDATE ideas` + `UPDATE idea_revisions` | `ob_app` |
| `open_notebook` | `d:\Open WebUI\open-notebook\open_notebook\database\ob1_repository.py` | data-plane; one `to_regclass()` catalogue read, no DDL | `ob_app` |

### Stays superuser, with the reason

- **`openbrain-db-backup`** — `pg_dump`. A dumper without `BYPASSRLS` silently omits every row
  its policies hide; the backup would become partial **without failing**. Superuser is the
  correct privilege for this client, not a concession.
- **`(local)` operator `psql`** — DDL, health probes, restores, and this promotion itself. Not
  an application.

### Blocked, not exempt

- **`openbrain-ext`** — 15 extension/CRM tables (19 policies) are governed **only** by
  `TO public USING (auth.uid() = user_id)`, and `auth.uid()` here is a stub returning `NULL`;
  there is no JWT anywhere in this stack. Under any non-superuser role those tables return
  **zero rows, with no error** (measured: drill probes R4/G17/G20/G21 — the superuser sees the
  seeded fixture, `ob_app` sees none, nothing is raised, and this is true *despite* `ob_app`
  having no `USAGE` on schema `auth`, because a direct `SELECT auth.uid()` as `ob_app` **is**
  refused while the policy that calls it on `ob_app`'s behalf is not). Moving `openbrain-ext`
  today produces an empty CRM that looks like an empty CRM. It stays on `postgres` until the
  extension tables get a policy model that does not depend on a JWT. Blast radius today: all
  18 extension tables hold **0 rows** in production.
- **`openbrain-postgrest`** — bound in practice (it switches to `PGRST_DB_ANON_ROLE =
  service_role` per request) but its authenticator is still the superuser. Step 5 below.

## 3. What the drill proves

`scripts/checks/drill-app-role-not-superuser.ps1` — **31 probes, 0 failures**, on a throwaway
`pgvector/pgvector:pg16` built from the compose-derived 28-file chain plus the two H1 files, on
its own network (`wt-u8h1-h1net`), synthetic fixtures only, self-cleaning. It has no `-Live`
switch and will not get one.

The headline, literally §C.9's sentence:

| | probe |
|---|---|
| **RED** | as `postgres` (`rolsuper|rolbypassrls` = `true|true`), the seeded personal thought **and** memory are visible (`1|1`) |
| **GREEN** | as `ob_app`, **no `SET ROLE` anywhere**, the same query returns `0|0` |
| **control** | the ops twins of both rows return `1|1` to `ob_app`, and an ops write succeeds — the blackout is not the whole table |

And the parts that make it a boundary rather than a blackout:

- `ob_app` **cannot** `SET ROLE ob_plane_personal` (`permission denied to set role`).
- `ob_app_memory` sees `0|0` without `SET ROLE`, `1|1` after `SET LOCAL ROLE
  ob_plane_personal` + `SET LOCAL ob.user_id`, and `0|0` for a different tenant.
- a personal write **without** `SET ROLE` is refused with `new row violates row-level security
  policy`, not silently dropped.
- the `SECURITY DEFINER` trigger still queues an ops write made by `ob_app`.

## 4. The grant set — where least privilege actually died

Four findings, each with a probe:

1. **Four views ran as their superuser owner.** `ideas_owed_research`, `reusable_claims`,
   `ungrounded_claims`, `research_run_metrics` had no `security_invoker`, so any non-superuser
   `SELECT` on them executed with `postgres`'s row-security context. `ideas_owed_research`
   JOINs `idea_revisions`, whose policy is the plane predicate — a hole straight through
   180/190/200. RED/GREEN in one run: reset the option and the same query as `ob_app` returns
   the personal-linked idea (`1`); set it and it returns `0`. `init-app-role.sql` §3 sets it on
   all four and asserts that no view in `public` is left running as its owner.
2. **`SECURITY DEFINER` functions.** Only two, both trigger functions owned by `postgres`.
   Tested rather than reasoned about: a direct call as `ob_app` is refused (`trigger functions
   can only be called as triggers`), so neither is a laundering path.
3. **`PUBLIC` grants.** None on any relation in `public`. No app role holds a `pg_*` predefined
   role (`pg_read_all_data` would undo everything in one line).
4. **Sequences.** `ob_app` holds `USAGE` on all of them via `service_role`, which is what keeps
   the ops write path working.

## 5. THE BLOCKER THIS EFFORT DID NOT EXPECT — 200 closed the write door

`init-graph-plane-rls.sql` §6a (mounted `200-`) executes
`REVOKE INSERT, UPDATE, DELETE ON <agent-memory corpus>, idea_revisions FROM service_role,
authenticated`. Measured on a fresh volume against production, 2026-08-31:

| table | fresh chain | production |
|---|---|---|
| `agent_memories` *(and all 7 sidecars)* | `SELECT` | `DELETE,INSERT,SELECT,UPDATE` |
| `idea_revisions` | `SELECT` | `DELETE,INSERT,SELECT,UPDATE` |
| `thoughts`, `sources`, `threads`, `claims`, `ideas` | identical | identical |

**They differ because 200 is not applied to production** — `ob_relation_governed` is absent
there and it has written none of its `ORACLE-DISPOSITION` comments — not because anything
drifted. (Round 4's withdrawal of the earlier "grant drift" finding checked `thoughts` and
`thought_entities`, which do agree. The `agent_memory*` tables were not in that comparison.)

This costs nothing while every writer is a superuser. It costs everything the moment they are
not: without a grant, `openbrain-mcp` loses **every** memory write and `openbrain-idea-refinery`
loses `UPDATE idea_revisions` — loudly (`42501`), but completely.

`init-app-role.sql` §2b therefore reopens that door for the **two named roles derived to need
it**, and for nobody else. `service_role` — what PostgREST and every anonymous reader run as —
stays read-only there, asserted by the migration and proved by probe G8d. **This is a
deliberate interaction with another item's security decision and the owner of
`init-graph-plane-rls.sql` should confirm it before promotion.**

**Ordering consequence:** `init-app-role.sql` asserts that `service_role` cannot write the
corpus, so **it will refuse to apply to production until 200 has been applied there.** That is
a prerequisite, not a bug — H1 lands after 200 or not at all.

## 6. The promotion, step by step

Each step is separately revertible. Nothing below has been run.

### Step 0 — prerequisites

1. `init-graph-plane-rls.sql` (200-) applied to production; verify
   `SELECT count(*) FROM pg_proc WHERE proname='ob_relation_governed'` = `1`.
2. `docker compose -f OB1/docker/docker-compose.yml config` parses with the new env var set.
3. **Open Notebook's runtime override is empty.** `ob1_repository._cfg()` prefers a
   SurrealDB-persisted `open_notebook:ob1_settings` record over the environment, so a record
   saved from the Settings page would keep `open_notebook` on `postgres` while compose said
   otherwise. Checked read-only 2026-08-31: the record does not exist (`result: []`).
   ```
   docker exec open_notebook sh -c "curl -s -u '$SURREAL_USER:$SURREAL_PASSWORD' \
     -H 'surreal-ns: open_notebook' -H 'surreal-db: open_notebook' \
     -X POST --data 'SELECT * FROM open_notebook:ob1_settings;' http://surrealdb:8000/sql"
   ```

### Step 1 — the migration, in both homes

Two-place invariant. **Both, or neither.**

*(a)* `OB1/docker/docker-compose.yml`, `openbrain-db` volumes — add after the `200-` line:

```yaml
      - ./init-app-role.sql:/docker-entrypoint-initdb.d/210-init-app-role.sql:ro
      - ./init-app-role-passwords.sh:/docker-entrypoint-initdb.d/215-init-app-role-passwords.sh:ro
```

and in the same service's `environment:`

```yaml
      OB_APP_PASSWORD: ${OB_APP_PASSWORD:?set OB_APP_PASSWORD in OB1/docker/.env}
      OB_APP_MEMORY_PASSWORD: ${OB_APP_MEMORY_PASSWORD:?set OB_APP_MEMORY_PASSWORD in OB1/docker/.env}
```

The `:?` is deliberate. A fresh volume that comes up with roles nobody can authenticate as is
a fleet that silently falls back; `docker compose config` failing is loud.

*(b)* apply by hand to the live volume:

```powershell
Get-Content -Raw OB1\docker\init-app-role.sql |
  docker exec -i openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1
docker exec openbrain-db psql -U postgres -d openbrain -c `
  "ALTER ROLE ob_app PASSWORD '<OB_APP_PASSWORD>'; ALTER ROLE ob_app_memory PASSWORD '<OB_APP_MEMORY_PASSWORD>';"
```

Nothing connects as either role yet, so this step is inert by itself.

### Step 2 — the eight `ob_app` clients

`OB1/docker/.env`: `OB_APP_PASSWORD=…`, `OB_APP_MEMORY_PASSWORD=…` (never in git).

For `openbrain-workbench`, `openbrain-curator`, `openbrain-research`,
`openbrain-chunk-worker`, `openbrain-grounding-backfiller`, `openbrain-suggestion-worker`:

```yaml
      DB_USER: ob_app
      DB_PASSWORD: ${OB_APP_PASSWORD}
```

For `openbrain-idea-refinery` (`docker-compose.scheduled.yml`) the `DB_USER` line **does not
exist today** — it must be **added**, not edited, or the client keeps its `"postgres"` default.

For `open_notebook`: `OB1_DB_USER=ob_app` and add `OB1_DB_PASSWORD=${OB_APP_PASSWORD}` (it
currently falls back to `POSTGRES_PASSWORD` from the `env_file`, which is the superuser's).

**No code change is required for any of them** — every client already reads `DB_USER` from the
environment.

Recreate one service at a time and watch its log. A wrong password fails at connect with
`FATAL: password authentication failed`, which is loud.

### Step 3 — `openbrain-mcp`

```yaml
      DB_USER: ob_app_memory
      DB_PASSWORD: ${OB_APP_MEMORY_PASSWORD}
```

**Known behaviour change, and it is the one to watch.** `openbrain-mcp` issues no `SET ROLE`.
After this step it operates on the ops plane only: personal memories become invisible to it and
personal writes are refused with `42501`. Production holds **0 personal rows today** (all
13,001 thoughts and 21 memories are `exposure='ops'`, measured 2026-08-31), so nothing is lost
now — but the review queue's `exposure='personal'` path stops working until `openbrain-mcp`
acquires a per-request `SET LOCAL ROLE ob_plane_personal` + `SET LOCAL ob.user_id` chokepoint.
That is a code change and it is a **separate item**; the role is already provisioned for it.

### Step 4 — re-measure

```powershell
scripts\checks\census-db-connection-roles.ps1     # expect: only postgrest / db-backup / ext / (local)
scripts\checks\drill-app-role-not-superuser.ps1   # unchanged, throwaway only
```

### Step 5 — later, separately

- `openbrain-postgrest`: a dedicated `authenticator` login role, `NOINHERIT`, with
  `GRANT service_role TO authenticator`. Its own change: `PGRST_DB_URI` is a URI, not a
  `DB_USER`, and PostgREST's schema-cache `LISTEN` runs as the authenticator.
- `openbrain-ext`: blocked on the extension tables' policy model (§2).
- `openbrain-mcp`'s plane-switch chokepoint (§ step 3).

## 7. Revert

| step | revert |
|---|---|
| 3, 2 | put `DB_USER`/`OB1_DB_USER` back to `postgres` (and delete the line added to idea-refinery), restore `DB_PASSWORD: ${POSTGRES_PASSWORD}`, recreate. Everything reconnects as before. |
| 1 | **compose env first, roles second.** `docker cp OB1\docker\revert-app-role.sql openbrain-db:/tmp/` then `psql -v ON_ERROR_STOP=1 -f /tmp/revert-app-role.sql`. It refuses to run while any `ob_app*` backend is still connected, and it also resets the four views. |

Round-tripped on the throwaway: apply → 31 probes green → revert → roles gone, views back to
owner-context, assertions green.

## 8. What this does NOT close

- **`openbrain-ext`** and the 15 `auth.uid()` tables. Silent-empty, not loud-broken.
- **`openbrain-postgrest`'s** authenticator.
- **`openbrain-mcp`'s** personal-plane switch — the role exists, the code does not use it.
- **`openbrain-db-backup`** stays superuser on purpose; a backup that quietly omits rows is
  worse than an app that quietly reads them.
- Nothing here is deployed. Until §6 runs, the boundary in front of `openbrain-db` is still
  22 superuser connections and the policies in front of them are still inert.
