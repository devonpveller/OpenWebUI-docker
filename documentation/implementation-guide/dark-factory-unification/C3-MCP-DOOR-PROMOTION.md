# Clause 3 — the MCP door stops being a superuser: the promotion plan

> Status: **VALIDATED ON THROWAWAYS, NOT DEPLOYED.** Nothing here has been applied to
> `openbrain-db`, no compose file has been edited, no production credential has changed, no
> production service has been restarted. **Deployment is the orchestrator's.**
>
> Artefacts: `scripts/checks/sql/init-app-role-memory.sql`,
> `scripts/checks/sql/revert-app-role-memory.sql`,
> `scripts/checks/drill-mcp-door-not-superuser.ps1`.
>
> Validated at ai-stack `fba111d` + this branch, OB1 pinned at `b604d55`, 2026-09-01/02.

## 1. The defect, and why it is not a documentation problem

The live `dfu-done.ps1` run reported clause 3 open at two doors:

```
[fail] door-openbrain-mcp-door (exit 200)
   HTTP 200; the door RETURNED the personal fixture;
   the door connects as 'postgres' (rolsuper/rolbypassrls = t/t)
[fail] door-cloud-search-thoughts (exit 200)
   HTTP 200; the door RETURNED the personal fixture
```

No policy is missing. Measured on `openbrain-db`, read-only, 2026-09-01:

| fact | value |
|---|---|
| `thoughts` / `agent_memories` row security | `rls=true forced=true` — both |
| ops-plane policies | `thoughts_ops_plane`, `agent_memories_ops_plane`, both `TO service_role` |
| personal-plane policies | both `TO ob_plane_personal` |
| `openbrain-mcp`'s `DB_USER` | `postgres` (`rolsuper`, `rolbypassrls`) |

RLS binds no role with `BYPASSRLS`. Every predicate in the 180/190 chain is inert in front
of that one connection, so the boundary is decorative at exactly the doors clause 3 names.

## 2. Two failing doors, one connection — the change is smaller than the board suggests

`openbrain-gateway` holds **no database connection at all**. It is an HTTP proxy:
`OPENBRAIN_URL = http://openbrain-mcp:8000` (`openbrain-gateway/app.py:41`; confirmed live
with `docker exec openbrain-gateway printenv OPENBRAIN_URL`). `openbrain-ops-gateway`, which
fronts clause 3's third MCP door, is a second instance of the same image with the same
upstream.

So all three MCP doors traverse **one** database connection, and the minimal change is one
service's credentials:

```
openbrain-mcp.DB_USER : postgres -> ob_app_memory
```

H1's larger promotion — the eight `ob_app` clients, the PostgREST authenticator — is a
separate and much larger change. **Clause 3 does not require it.** This plan does not do it.

## 3. What was measured, not argued

`scripts/checks/drill-mcp-door-not-superuser.ps1` — **16 probes, 0 failures**, on a
throwaway `pgvector/pgvector:pg16` built from the compose-derived 29-file init chain, on its
own network (`wt-dfuc3-drill-net`), running the **real** `openbrain-mcp-server:local` and
`openbrain-gateway:local` images, synthetic fixtures only, self-cleaning, no `-Live` switch.

The headline is a matched pair through the real doors:

| | probe |
|---|---|
| **RED** | `openbrain-mcp` as `postgres`: `door-openbrain-mcp-door` returns the personal fixture (`http=200 personal=1 ops=1`) — the live failure, reproduced |
| **RED** | the same, through the real cloud gateway: `door-cloud-search-thoughts` returns it too |
| **GREEN** | `openbrain-mcp` as `ob_app_memory`, **no `SET ROLE` anywhere**: `http=200 personal=0 ops=1` |
| **GREEN** | the cloud gateway closes with it — one connection, two doors |
| **control** | the ops twin returns in every GREEN probe, so this is a boundary and not an outage |

And the parts that stop it being a blackout or a bypass:

- `ob_app_memory` is `rolsuper=false rolbypassrls=false`.
- It **cannot** `SET ROLE ob_plane_personal` (`permission denied to set role`).
- A personal write is refused **loudly** (`new row violates row-level security policy`), not
  silently dropped.
- An ops thought write still succeeds.

## 4. The role, and why it is H1's and not a second scheme

`scripts/checks/sql/init-app-role-memory.sql` creates exactly one role:

```sql
CREATE ROLE ob_app_memory LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT service_role TO ob_app_memory;
REVOKE ob_plane_personal FROM ob_app_memory;
```

`INHERIT` is load-bearing: the ops policies are written `TO service_role`, and PostgreSQL
applies a policy only through membership a role **inherits**. A `NOINHERIT` role here is
default-denied on every table — a door returning an empty corpus, which from the outside is
indistinguishable from a boundary working perfectly.

**One deliberate departure from H1**, stated so it can be objected to: H1 provisioned
`ob_app_memory` with a switchable `ob_plane_personal` membership for a future per-request
chokepoint in `openbrain-mcp`. That chokepoint does not exist — `openbrain-mcp` issues no
`SET ROLE` anywhere. Granting a path no code takes only widens what a leaked password
reaches, so this file leaves it ungranted and asserts the absence. The grant the chokepoint
will want is written in a comment beside it (`WITH INHERIT FALSE, SET TRUE`), to be added by
the change that makes it used.

The migration **refuses to apply** to a database where the ops policies or forced row
security are missing, and asserts the role's shape in the transaction that builds it.

### H1's own migration is not in this checkout

`OB1/docker/init-app-role.sql`, `init-app-role-passwords.sh` and `revert-app-role.sql` — the
artefacts `H1-APP-ROLE-PROMOTION.md` and `u8h1-findings.md` both cite — **exist on no
reachable commit of either repository.** `git -C OB1 log --all --diff-filter=A -- docker/init-app-role*`
returns nothing across all 963 commits, and `drill-app-role-not-superuser.ps1` aborts with
exit 2 for exactly this reason (its own comment predicts it). H1's design survived only in
prose; the runnable half was lost with its worktree.

That is why the SQL here lives in **ai-stack** rather than in the OB1 submodule: a clean
clone of this repository at this branch has it. Mounting it into OB1's initdb chain for
fresh-volume parity is step 4 below, and it requires an OB1 push plus a gitlink bump — the
step whose omission is what lost H1's files.

## 5. Prerequisites

1. **The 180/190 chain is applied.** Verified live 2026-09-01: policies present, `forced=true`.
   The migration checks this itself and refuses otherwise.
2. **`OB_APP_MEMORY_PASSWORD` chosen** and placed in `OB1/docker/.env`. Never in git.
3. **Read `200` below.** It is the one ordering constraint and it is not optional reading.

## 6. THE ORDERING HAZARD — `init-graph-plane-rls.sql` (200-)

`init-graph-plane-rls.sql` §6a `REVOKE`s `INSERT, UPDATE, DELETE` on the agent-memory corpus
from `service_role` — which is where `ob_app_memory`'s write privilege comes from.

**200 is not applied to production.** Measured 2026-09-01: `ob_relation_governed` = 0 rows in
`pg_proc`; `service_role` holds `DELETE,INSERT,SELECT,UPDATE` on `agent_memories`. It **is**
in the compose init chain, so any fresh volume has it.

Both shapes are measured by the drill rather than predicted:

| world | agent-memory write as `ob_app_memory` | read boundary |
|---|---|---|
| 200 applied (fresh volume) | **DENIED** — `permission denied for table agent_memories` | holds |
| 200 not applied (production today) | **succeeds** | holds |

**Consequence: this promotion is safe today for a reason that expires.** The moment 200 is
applied to production, `openbrain-mcp` loses every agent-memory write — loudly (`42501`), but
completely. Whoever applies 200 must re-grant the corpus DML to `ob_app_memory` in the same
change (H1's §2b), or apply 200 and this promotion together with that grant included.

This is a deliberate interaction with another item's security decision. **The owner of
`init-graph-plane-rls.sql` should confirm it before either lands.**

## 7. The other behaviour change, and it is the one to watch

`stampExposure()` (`agent-memory-policy.ts:104`) returns `'personal'` when the content is
**tainted or PII-detected**, even though the live door hardcodes `doorExposure: "ops"`
(`index.ts:2083`). After this promotion, `openbrain-mcp` cannot write the personal plane at
all — so the PII-demotion path stops demoting and starts **erroring** (`42501`).

Loud is better than silent, and a refused write is better than a personal row written by a
door that should not reach the personal plane. But it is a behaviour change, it will surface
as agent-memory writeback failures on PII-bearing content, and it stays that way until the
per-request `SET LOCAL ROLE ob_plane_personal` chokepoint lands. That chokepoint is a code
change and a separate item.

Normal writebacks are unaffected: the door stamps `ops`, and ops writes succeed (drill G7).

## 8. Blast radius, measured

| | value (2026-09-01) |
|---|---|
| thoughts, total | 14,140 |
| `exposure='ops'` | 13,011 — unaffected, still visible to the door |
| `exposure='personal'` | **1,129 — become invisible to the agent plane. That is the point of the change.** |
| rows where the column and the jsonb mirror disagree | **0** — so either predicate spelling hides the same rows |
| agent_memories | 21, all `ops` — unaffected |

The 1,129 personal thoughts are the operator's deliberate resolution of the 2026-09-01
incident. Nothing in this plan deletes, reclassifies or touches them; the change makes them
stop being readable by the agent plane, which is what clause 3 asks for.

> H1's plan justified its step 3 with "production holds 0 personal rows today, so nothing is
> lost now." **That sentence is stale** — it was measured on 2026-08-31, before the incident.
> The justification is no longer "nothing is hidden"; it is "1,129 rows become hidden, and
> that is the intent."

## 9. The promotion, step by step

Each step is separately revertible. Nothing below has been run.

### Step 1 — create the role (inert on its own)

```powershell
docker cp scripts\checks\sql\init-app-role-memory.sql openbrain-db:/tmp/
docker exec openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/init-app-role-memory.sql
docker exec openbrain-db psql -U postgres -d openbrain -c "ALTER ROLE ob_app_memory PASSWORD '<OB_APP_MEMORY_PASSWORD>';"
```

Nothing connects as the role yet. **This step changes no behaviour and is safe at any time.**
Verify:

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -Atc `
  "SELECT rolname||'|'||rolsuper::text||'|'||rolbypassrls::text FROM pg_roles WHERE rolname='ob_app_memory';"
# expect: ob_app_memory|false|false
```

### Step 2 — point the door at it

`OB1/docker/.env`:

```
OB_APP_MEMORY_PASSWORD=<the password from step 1>
```

`OB1/docker/docker-compose.yml`, service `openbrain-mcp` — two lines change:

```yaml
      DB_USER: ob_app_memory
      DB_PASSWORD: ${OB_APP_MEMORY_PASSWORD:?set OB_APP_MEMORY_PASSWORD in OB1/docker/.env}
```

The `:?` is deliberate: a container that comes up with an empty password fails at connect in
a way that reads like a database outage. `docker compose config` failing is louder.

```powershell
docker compose -f OB1/docker/docker-compose.yml up -d --force-recreate openbrain-mcp
docker logs --tail 40 openbrain-mcp
```

A wrong password fails at connect with `FATAL: password authentication failed` — loud, and
the revert is step 2 backwards.

### Step 3 — verify at the door, not at the database

```powershell
scripts\checks\dfu-done.ps1 -Only 3
```

`door-openbrain-mcp-door` and `door-cloud-search-thoughts` should stop returning the personal
fixture while still returning the ops control.

### Step 4 — fresh-volume parity (separate, and gated on an OB1 push)

Production is now correct but a **rebuilt volume would not be**: the role is applied by hand
and is not in the init chain. To close that, `init-app-role-memory.sql` must be mounted into
`openbrain-db`'s `/docker-entrypoint-initdb.d/` at `210-`, which means committing it inside
the OB1 submodule, **pushing that commit to OB1's remote**, and bumping the parent gitlink.

Do not skip the push. That omission is precisely what left H1's migration unreachable
(section 4).

## 10. What breaks if this is applied while something else is mid-flight

| concurrent activity | effect |
|---|---|
| **an agent mid-turn on an MCP tool call** | step 2 recreates `openbrain-mcp`; in-flight JSON-RPC calls fail and the agent sees a dropped tool call. Recreate during a quiet window. |
| **an agent-memory writeback carrying PII/tainted content** | refused with `42501` from the moment step 2 lands (section 7). Not a crash — an error the caller sees. |
| **`init-graph-plane-rls.sql` (200-) being applied by another item** | **the dangerous one.** If 200 lands after step 2 without H1's §2b grant, every agent-memory write starts failing. Coordinate, or land the grant with 200. (Section 6.) |
| **a fresh `openbrain-db` volume being created** | the role will not exist; `openbrain-mcp` fails at connect until step 1 is re-run. Step 4 is the durable fix. |
| **`openbrain-db-backup` running** | unaffected — it stays `postgres` on purpose. A `pg_dump` without `BYPASSRLS` silently omits every row its policies hide, producing a partial dump that exits 0. |
| **anything else on the plane** | unaffected. Only `openbrain-mcp`'s credentials change; the other clients keep connecting as `postgres`. |

## 11. Revert

| step | revert |
|---|---|
| 2 | put `DB_USER: postgres` / `DB_PASSWORD: ${POSTGRES_PASSWORD}` back and recreate `openbrain-mcp`. **This alone restores the previous behaviour** and is the emergency step — it needs nothing from the database. |
| 1 | optional, and only after step 2 is reverted: `docker cp scripts\checks\sql\revert-app-role-memory.sql openbrain-db:/tmp/` then `psql -v ON_ERROR_STOP=1 -f /tmp/revert-app-role-memory.sql`. It **refuses to run** while any backend is still connected as `ob_app_memory`, so reverting in the wrong order is an error rather than an outage. |

Leaving the role in place while the door connects as `postgres` costs nothing — it is inert
without a client pointed at it. Revert step 1 to return the database to its pre-promotion
shape, not as an emergency measure.

Round-tripped on the throwaway: apply -> 16 probes green -> revert -> role gone (drill V1).

## 12. What this does NOT close

- **The other eight direct clients** still connect as `postgres` (H1's step 2). Clause 3 does
  not need them; the boundary in front of `openbrain-postgrest`, `openbrain-workbench`,
  `openbrain-curator` and the rest is still inert.
- **`openbrain-mcp`'s personal-plane chokepoint.** The role is provisioned to have the switch
  added; the code has no `SET ROLE`.
- **`door-wiki-compiler-output`** and **`door-mcp-read-tools`** remain indeterminate for
  reasons unrelated to the DB role — see `documentation/notes/dfuc3-findings.md`.
- **Fresh-volume parity** until step 4.
