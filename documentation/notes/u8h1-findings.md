# u8h1 findings — H1, no application connects as a superuser

Findings sink for DFU §C.9 H1, worktree `work/u8h1`, 2026-08-31. Everything below was
measured; each entry names the command or the probe. Nothing was applied to `openbrain-db`
and no compose file was edited.

---

## 1. The census reproduces, and it is 22 of 22

`scripts/checks/census-db-connection-roles.ps1` against `openbrain-db`, read-only,
2026-08-31T21:27-04:00: **22 client backends, 22 of them `postgres` (`rolsuper`,
`bypassrls`)** — 17 `deno_postgres`, 3 PostgREST, 1 asyncpg (`open_notebook`, no
`application_name`), 1 operator `psql`. Same number as the plan's day-old measurement, same
composition.

## 2. A live census is not the client inventory — two clients were invisible

`openbrain-ext` and `openbrain-suggestion-worker` hold **lazy pools**. Neither was connected in
any run of the census, and both are configured to connect as `postgres` exactly like the eight
that showed. A census-only answer would have left them out of scope entirely.

Worse, `openbrain-idea-refinery` sets **no `DB_USER` at all** — it reaches `postgres` through
`Deno.env.get("DB_USER") || "postgres"` (`OB1/integrations/openbrain-idea-refinery/index.ts:39`),
and every other direct client has the same fallback. A compose sweep keyed on explicit
`DB_USER` lines reports it as not-a-client while it holds a live superuser connection.

The census now reads the compose files as well and reports **12 services configured** as
`postgres`, marking the implicit ones. Both halves are in the script because either half alone
is wrong.

## 3. Four views ran with the superuser's row-security context

`ideas_owed_research`, `reusable_claims`, `ungrounded_claims`, `research_run_metrics` carried
no `security_invoker` and are owned by `postgres`. Any non-superuser `SELECT` on them therefore
executed as `postgres` — RLS bypassed. `ideas_owed_research` JOINs `idea_revisions`, whose
policy is the plane predicate, so this was a live route around 180/190/200 for exactly the
roles H1 exists to create.

Proved RED→GREEN inside one drill run (probes G9/R3/G10): with `security_invoker` reset, the
same query as `ob_app` returns the personal-linked idea; set, it returns nothing.

`v_agent_memories` / `v_thoughts` already had it — these four predate the idea.

## 4. `openbrain-ext` fails SILENTLY under a non-superuser role, not loudly

15 extension/CRM tables (19 policies) are governed **only** by
`TO public USING (auth.uid() = user_id)`. `auth.uid()` in this database is a stub:
`CREATE FUNCTION auth.uid() RETURNS uuid ... AS $$ SELECT NULL::uuid $$`. There is no JWT
anywhere in this stack.

**I predicted a loud failure and was wrong.** `ob_app` has no `USAGE` on schema `auth`
(`has_schema_privilege('ob_app','auth','USAGE')` = `false`), and a direct `SELECT auth.uid()`
as `ob_app` **is** refused with `permission denied for schema auth` — but the RLS policy that
calls the same function on `ob_app`'s behalf runs anyway, yields `NULL`, and the read returns
an **empty set with no error**. Measured with a seeded fixture (probes R4/G17/G20/G21): the
superuser sees the row, `ob_app` sees zero, nothing is raised.

Without the fixture this probe would have read `0` from an empty table and proved nothing —
all 18 extension tables hold **0 rows** in production. That is the shape of check that passes
while checking nothing, and it was one edit away from being in here.

Consequence: `openbrain-ext` moved to a non-superuser role reports an **empty CRM**, not a
broken one. It stays on `postgres`. Granting `USAGE ON SCHEMA auth` would only make the silence
permanent, so the migration deliberately does not.

## 5. `init-graph-plane-rls.sql` (200-) has already closed the write door the app roles need

Section 6a of 200 executes
`REVOKE INSERT, UPDATE, DELETE ON <derived agent-memory corpus>, idea_revisions FROM
service_role, authenticated`.

Measured, fresh chain-complete volume vs production:

| table | fresh | production |
|---|---|---|
| `agent_memories` + all 7 sidecars | `SELECT` | `DELETE,INSERT,SELECT,UPDATE` |
| `idea_revisions` | `SELECT` | `DELETE,INSERT,SELECT,UPDATE` |
| `thoughts`, `sources`, `threads`, `claims`, `ideas` | identical | identical |

**They differ because 200 is not applied to production**, not because anything drifted:
`ob_relation_governed` does not exist on `openbrain-db` and 200 has written **0** of its
`ORACLE-DISPOSITION` comments there. (Round 4's withdrawal of the earlier "grant drift"
finding compared `thoughts` and `thought_entities`, which agree. The eight `agent_memory*`
tables were not in that comparison and do not.)

This is invisible while every writer is a superuser and total the moment they are not.
`init-app-role.sql` §2b reopens the door for the two named roles derived to need it —
`ob_app_memory` (openbrain-mcp: `agent_memories` + 4 sidecars + `idea_revisions`) and `ob_app`
(`UPDATE idea_revisions`, openbrain-idea-refinery) — and `service_role` stays read-only there,
asserted by the migration and proved by probe G8d.

**This is a deliberate interaction with another item's security decision.** The owner of
`init-graph-plane-rls.sql` should confirm it. Consequence recorded in the migration: it
**refuses to apply to a database where `service_role` can still write the corpus**, i.e. H1
lands after 200 or not at all.

## 6. Open Notebook's DB user can be overridden from the UI, so compose is not authoritative

`ob1_repository._cfg()` (`d:\Open WebUI\open-notebook\open_notebook\database\ob1_repository.py:60`)
prefers a SurrealDB-persisted `open_notebook:ob1_settings` record over `OB1_DB_USER`. A record
saved from the Settings page would keep `open_notebook` on `postgres` while compose said
otherwise, and nothing would report it.

Checked read-only 2026-08-31: the record does not exist (`SELECT * FROM
open_notebook:ob1_settings` → `result: []`), so the env wins today. Recorded as a pre-flight
step in the promotion plan rather than left to be rediscovered.

## 7. `openbrain-mcp` cannot move on env alone — and it fails loudly, which is the good case

`WRITEBACK_DEFAULTS.exposure` is `"personal"`
(`OB1/integrations/kubernetes-deployment/agent-memory-policy.ts:82`) and the review path stamps
`exposure: "personal"` (`agent-memory-review.ts:98`). `openbrain-mcp` issues no `SET ROLE`
anywhere, so as `ob_app_memory` it would run on the ops plane only.

Measured (probe G8c): a personal write without `SET ROLE` is refused with `new row violates
row-level security policy for table "agent_memories"`. It does **not** silently vanish. Reads
of personal rows return nothing (G6) but are restored by an explicit `SET LOCAL ROLE
ob_plane_personal` + `SET LOCAL ob.user_id` (G7), and only for the matching tenant (G8).

Production holds **0 personal rows today** (13,001 thoughts and 21 memories, all
`exposure='ops'`, measured 2026-08-31), so step 3 of the promotion loses nothing now. The
per-request plane-switch chokepoint in `openbrain-mcp` is a code change and a separate item.

## 8. `openbrain-db-backup` must stay superuser, and that is a finding not an exception

`pg_dump` run by a role without `BYPASSRLS` silently omits every row its policies hide. Moving
the backup sidecar to `ob_app` would produce a **partial dump that exits 0**. Named in the
census allow-list with that reason so it cannot be "cleaned up" later.

## 9. What could not be closed, with its cost

- **`openbrain-ext`** — needs a policy model for the 15 extension tables that does not depend
  on a JWT this stack has never had. Cost: a design decision (who is the tenant, if there is no
  auth layer?) plus a migration over 15 tables. Not attempted here; it is not H1's question.
- **`openbrain-postgrest`'s authenticator** — a `NOINHERIT` login role with
  `GRANT service_role TO authenticator`. Cost: small, but it is a `PGRST_DB_URI` edit rather
  than a `DB_USER` edit, PostgREST's schema-cache `LISTEN` runs as the authenticator, and it
  wants its own drill. Deferred deliberately: PostgREST is the one client that is already
  mechanically bound.
- **Deployment** — gated promotion, out of scope by instruction. The migration is committed but
  **not mounted in compose**, which makes it a staged artefact and NOT a live one. The drill
  says so on every run rather than hiding it.
- **`init-app-role.sql`'s §5 assertion will fail on production today** because 200 is not
  applied there. Intended, documented, and the reason step 0 of the promotion exists.

## 10. Evidence

| what | where |
|---|---|
| 31-probe drill, throwaway only, RED before GREEN | `scripts/checks/drill-app-role-not-superuser.ps1` |
| read-only live census + configured-client sweep | `scripts/checks/census-db-connection-roles.ps1` |
| the migration | `OB1/docker/init-app-role.sql`, `OB1/docker/init-app-role-passwords.sh` |
| the revert | `OB1/docker/revert-app-role.sql` |
| the plan | `documentation/implementation-guide/dark-factory-unification/H1-APP-ROLE-PROMOTION.md` |

Throwaway hygiene, verified after every run: container `wt-u8h1-h1db` and network
`wt-u8h1-h1net` removed, no `ai-stack_*` network ever attached, no `:local` tag produced,
`openbrain-db` never written to.
