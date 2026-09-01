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

Round 2 found that the script did not honour that sentence: a compose file it could not read
was degraded to a printed note and the run continued to a green verdict over whatever was
left. Section 13 has the reproduction and the fix.

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
| the census's own failing cases, re-runnable | `scripts/checks/redprove-census-cannot-measure.ps1` |
| the migration | `OB1/docker/init-app-role.sql`, `OB1/docker/init-app-role-passwords.sh` |
| the revert | `OB1/docker/revert-app-role.sql` |
| the plan | `documentation/implementation-guide/dark-factory-unification/H1-APP-ROLE-PROMOTION.md` |

Throwaway hygiene, verified after every run: container `wt-u8h1-h1db` and network
`wt-u8h1-h1net` removed, no `ai-stack_*` network ever attached, no `:local` tag produced,
`openbrain-db` never written to.

## 11. A harness gap: worktree submodules get CRLF, the main checkout does not

`OB1/docker/init-app-role-passwords.sh` is bind-mounted into
`docker-entrypoint-initdb.d`, where the postgres entrypoint runs it with `sh`. A CR in it
fails on the first line citing a command nobody wrote.

Measured 2026-08-31, same commit, two checkouts:

| | `core.autocrlf` | `docker/backup/openbrain-db-backup.sh` on disk |
|---|---|---|
| main checkout `D:\Open WebUI\ai-stack\OB1` | `false` (set by hand) | 0 CRs |
| harness worktree `.claude/worktrees/wt-u8h1/OB1` | **`true`** | **75 CRs** |

The index is clean — **0** of the tracked `.sh`/`.sql` files hold a CR — so the difference is
entirely which git config the checkout landed in. `new-worktree.ps1` clones the OB1 submodule
without pinning `core.autocrlf`, and its CRLF check reads only ai-stack's tracked `*.sh`, not
the submodule's. The existing backup sidecars have been mounted from CRLF copies in every
worktree since worktrees existed; they are only ever *run* from the main checkout, which is
why nobody has been bitten.

Re-measured 2026-08-31 (round 2) with a byte count rather than `grep`, because `grep -c $'\r'`
under this Git-Bash matches *every* line and silently reports "all lines have a CR" — one of
this session's own measurements was wrong for exactly that reason before it was rechecked, and
briefly made the 75 above look like a mistake. It is not. **Reproduced exactly** in a clean
clone of the parent at `69b860f` whose submodule was initialised the ordinary way and then
moved onto `b12d2fb`: `openbrain-db-backup.sh` **75**, `open-notebook-backup.sh` 105,
`openbrain-wiki-backup.sh` 74, every blob holding 0.

Also observed, and NOT a live defect: `docker/wiki-viewer/entrypoint.sh` is `i/lf w/crlf` in
the *main* checkout (16,367 bytes on disk against a 16,069-byte LF blob) while `git status`
reports the tree clean. It is `COPY`d into an image rather than bind-mounted, and that
Dockerfile already carries `RUN sed -i 's/\r$//' /entrypoint.sh` — someone met this before.
Recorded because a stale-CRLF working copy that git calls clean is a trap for the next person,
not because anything is broken today.

Closed for OB1 by adding `OB1/.gitattributes` with `*.sh text eol=lf` / `*.sql text eol=lf`
(scoped to two extensions on purpose — `* text=auto` would renormalise the repository, and
the index needed no renormalising). Verified: deleting and re-checking-out
`openbrain-db-backup.sh` in the worktree afterwards yields 0 CRs.

**The fix works, but only for checkouts made after it exists — and that is a narrower claim
than "closed".** Measured three ways at `b12d2fb`, `core.autocrlf=true` throughout:

| checkout | `init-app-role-passwords.sh` | the three backup `.sh` |
|---|---|---|
| cloned **directly at** `b12d2fb` (`clone -b work/u8h1-app-role`) | 0 CRs | **0 CRs** |
| initialised at `4fdc21c`, then `checkout b12d2fb` | 0 CRs | **75 / 105 / 74 CRs** |
| this worktree, files deleted and re-checked-out | 0 CRs | 0 CRs where forced |

Git renormalises on checkout, not retroactively: a file whose content did not change between
the two commits is never rewritten, so it keeps the CRLF it was first written with. The file
the drill mounts is safe in every case because it is *created* by `b12d2fb`. The backup
sidecars are not — **every checkout that already exists, and every one that reaches this commit
by moving onto it rather than cloning at it, still holds CRLF copies** until someone runs
`git add --renormalize .` or deletes and re-checks them out. And none of this reaches anyone
at all while the gitlink still points at `4fdc21c`, which has no `.gitattributes`.

**Not closed:** the harness itself. `new-worktree.ps1` should set `core.autocrlf=false` on the
submodule clone and extend its CRLF check to it. Left alone deliberately — two other agents
are live on the harness's checks right now and this is not H1's file.

## 12. Validation record (§C.7b)

| what | where | result |
|---|---|---|
| ai-stack | `work/u8h1` @ **`abb8c7f`** | committed locally, not pushed |
| OB1 submodule | `work/u8h1-app-role` @ **`b12d2fb`** (parent `9ab9031`) | committed locally, **not pushed, gitlink NOT bumped** |
| clean checkout | `git clone -b work/u8h1` → `D:\tmp-u8h1-clean` @ `abb8c7f`, submodule fetched from the worktree's OB1 at `b12d2fb` | — |
| `drill-app-role-not-superuser.ps1` | run there, container `wt-u8h1c-h1db`, network `wt-u8h1c-h1net` | **31 probes, 0 failures** |
| `census-db-connection-roles.ps1` | run there against live `openbrain-db`, read-only | 22 of 22 superuser; **9 unexplained**; exit 1 (correct — that is today's state) |
| apply / re-apply / re-apply / revert | throwaway `wt-u8h1-h1db` | three consecutive applies exit 0 (NOTICEs only); revert exit 0, 0 roles left, 4 views back to owner-context |
| revert guard | one held `ob_app` connection | refused: `H1 revert: 1 connection(s) still authenticated as an application role` |
| throwaway hygiene | after every run | 0 leftover containers, 0 leftover networks, no `ai-stack_*` attachment, no `:local` tag |

### Round 2 (2026-08-31), rebased onto `b4311d2`

Three clean `git clone`s of the parent at **`69b860f`**, **one suite per checkout**, none of
them the worktree this was written in.

| checkout | OB1 | suite | result |
|---|---|---|---|
| `D:\tmp-u8h1r2-a` | **uninitialised** (the plain git-clone state) | `redprove-census-cannot-measure.ps1` | **5 cases, 0 disagreements**, exit 0 |
| `D:\tmp-u8h1r2-b` | `b12d2fb` | `drill-app-role-not-superuser.ps1` | **31 probes, 0 failures**, exit 0 |
| `D:\tmp-u8h1r2-c` | `4fdc21c` (recorded gitlink) | `census-db-connection-roles.ps1` vs live `openbrain-db`, read-only | 21 of 21 superuser; **12 of 13 recognised clients across 41 service blocks**; **9 unexplained**; exit 1 — correct, that is today's state |

Discrimination, run against the pre-fix scripts rather than only the fixed ones:

| mutation | pre-fix | now |
|---|---|---|
| red-proof pointed at the pre-fix census | 3 of 5 cases green that should not be | fails 3 of 5, exit 1 |
| one probe deleted from the drill | `PASSED - 30 probes, 0 failures`, exit 0 | `CANNOT MEASURE - ran 30, expected 31`, exit 2 |
| census query returning no client backends | exit 0 | exit 2 |
| drill run at the recorded gitlink | exit 2 via a raw `Copy-Item` crash | exit 2 by check, naming the cause |

Throwaway hygiene after round 2: every container and network created here removed
(`wt-h1r2a-rpdb`, `wt-h1r2b-h1db`, and the red-proof's own, plus their networks), all three
temporary checkouts deleted, nothing attached to an `ai-stack_*` network, no `:local` tag
built, `openbrain-db` read-only throughout.

**The gitlink is deliberately NOT bumped.** The migration lives in the OB1 submodule at an
unpushed commit, so `abb8c7f` alone does not carry it: a fresh
`clone --recurse-submodules` of `work/u8h1` gets the *old* OB1 and the drill aborts with
`ABORT: 28 mounted but 28 staged` → `init-app-role.sql` missing. Bumping the gitlink to a
commit that is not on OB1's remote is the one thing CLAUDE.md says never to do, and pushing
was out of scope for this item. Whoever lands this pushes `work/u8h1-app-role` to OB1 FIRST,
then bumps.

### Round 3 (2026-09-01), rebased onto `75ae94d`

Code at **`5841994`**. Three clean `git clone`s, **one suite per checkout**, none of them the
worktree this was written in. Every clone was cloned with `-c core.longpaths=true` and asserted
COMPLETE before anything ran - `git status --porcelain` empty, 1,082 tracked files - per
`documentation/notes/clean-clone-maxpath-validation-trap.md`, because on this repo a plain
clone into a deep path drops 1,108 files and exits 0.

| checkout | OB1 | suite | result |
|---|---|---|---|
| `D:3a` | **uninitialised** (the plain git-clone state) | `redprove-census-cannot-measure.ps1` | **9 cases, 0 disagreements**, exit 0 |
| `D:3b` | `b604d55` (the gitlink this branch records) | `census-db-connection-roles.ps1` vs live `openbrain-db`, read-only | **22 of 22** superuser; **12 of 13** recognised clients across **36** parsed services (30 + 6); **9 unexplained**; exit 1 - correct, that is today's state |
| `D:3c` | `b12d2fb` (the H1 migration, still unpushed) | `drill-app-role-not-superuser.ps1` | **31 probes, 0 failures**, exit 0 |

Discrimination, measured rather than asserted: the round-3 red-proof pointed at the **pre-fix**
census fails **4 of 9** and exits 1 - `no-services` 0, `reindent` 0, `unresolved` 1, `live-ghost`
0 against wanted 2/1/2/2. Every figure in the `pre-fix` column of that table is an exit code
that was observed, not a claim about the past.

`drill-app-role-not-superuser.ps1` is **byte-identical** to the version four verifiers accepted
(`git diff 0ae8da1 HEAD -- scripts/checks/drill-app-role-not-superuser.ps1` is empty); it was
re-run anyway because the branch was rebased onto a new base.

**The gitlink moved on its own.** The rebase onto `75ae94d` brought the work line's OB1 pointer
with it, so this branch now records **`b604d55`** (H3's exposure column) where round 2 recorded
`4fdc21c`. Neither contains `init-app-role.sql`, so §12's warning is unchanged and if anything
sharper: a fresh `clone --recurse-submodules` of `work/u8h1` still gets an OB1 without the
migration, and the drill still aborts by check. Whoever lands this pushes `work/u8h1-app-role`
to OB1 FIRST, then bumps.

Throwaway hygiene after round 3: `wt-h1v3a-*`, `wt-h1v3c-*` and the red-proof's own containers
and networks all removed (`docker ps -a` and `docker network ls` show no `wt-*` at all), all
three checkouts deleted, nothing attached to an `ai-stack_*` network, no `:local` tag built,
`openbrain-db` read-only throughout.

## 13. Round 2: the census passed on an empty denominator

Both verifiers reproduced the same defect independently. `census-db-connection-roles.ps1`
degraded a missing compose file to a printed note and a `continue`, then exited 0 whenever the
candidate set came out empty. Copied into a repo root with no `OB1/` — **exactly what `git
clone` without `--recurse-submodules` leaves, which is the state U4 exists for** — and pointed
at a throwaway database, it produced:

```
(missing: ...docker-compose.yml)   (missing: ...docker-compose.scheduled.yml)
-> 0 service(s) configured to connect as postgres
VERDICT: zero unexplained superuser application clients.     EXIT 0
```

A green whose entire configured-client denominator was unavailable — contradicting both the
script's own header (*"Exit 2 = could not measure … which is NOT a pass"*) and section 2 above.

**The fix separates "measured, and the answer is zero" from "could not measure, so there is no
answer."** Compose files are now read in a preflight that runs *before* any docker call, since
a checkout that cannot be measured has no business opening a connection to production to find
that out. Missing or unreadable is exit 2, naming each file and the reason. An empty
*recognised-client* set is exit 2 — `$configuredSuper` reaching zero is the goal of the
promotion and stays a pass, but `$configured` reaching zero means the parse matched nothing.
Zero live client backends is exit 2, because psql is itself a client backend. And the exit 0
now prints what it measured, so an honest zero is legible as one.

`scripts/checks/redprove-census-cannot-measure.ps1` is the reproduction, re-runnable on a
throwaway `postgres:16-alpine` on its own network. Five fixture repo roots, before → after:

| case | pre-fix | now | |
|---|---|---|---|
| compose file missing (no `OB1/`) | **0** | **2** | the reported defect |
| compose file present but permission-denied | **0** | **2** | readable half was clean, so it went green on half a denominator |
| both files read, no database client recognised | **0** | **2** | empty candidate set |
| real clients, all on non-superuser roles | 0 | 0 | a measured zero, still a pass, now stating its counts |
| a client configured as `postgres`, not allow-listed | 1 | 1 | still a finding |

The red-proof discriminates: run against the pre-fix census it fails 3 of 5 and exits 1.
Separately, a mutation that empties the live census exited 0 before and exits 2 now.

### The same shape, swept through this item's other artifact

`drill-app-role-not-superuser.ps1` carried it three times. Only one was reachable as a false
green, and that one is the worst of the three:

1. **The verdict never asserted that any probe ran.** `$script:fail -eq 0` is satisfied by
   zero probes as happily as by 31 green ones. **Reproduced:** delete one probe from the
   pre-fix drill and it reports `H1 DRILL PASSED - 30 probes, 0 failures` and exits 0. It now
   exits 2 (`ran 30 probe(s), expected 31`). The count is a contract, which is also what makes
   the "31 probes" figure quoted in three documents a machine-checked number rather than prose.
2. **An empty init chain passed the staged-vs-mounted check**, because `0 -ne 0` is false.
   Latent — `Copy-ObInitChain`'s `[Parameter(Mandatory)][array]` refuses an empty array, so the
   run died on parameter binding instead. Checked now rather than incidentally survived.
3. **The migration being absent from the checkout was a non-terminating `Copy-Item` error.**
   Measured at the recorded gitlink `4fdc21c`: the run reached that line, `Copy-Item` failed
   silently under `$ErrorActionPreference = "Continue"`, and the run only stopped because the
   *next* line's `ReadAllBytes` threw into the trap — exit 2 by luck, diagnosed by a raw
   Copy-Item error. Reorder those two lines and the drill instead builds a database with no
   `ob_app` in it and reports "a probe disagreed", which is the wrong answer to a question it
   could not ask. It now aborts by check, naming the unpushed-OB1-commit situation.

Note the pattern in 2 and 3: **both were held shut by an accident of the runtime, not by a
check.** That is the same class as the census defect, one step luckier.

## 14. Round 3: the same defect, one level down - a denominator nobody asserted

Round 2 made a *missing* compose file a cannot-measure. A verifier then measured the same
class surviving inside a file that is present. Reproduced on the REAL files: copy
`OB1/docker/docker-compose.yml` and `docker-compose.scheduled.yml`, re-indent **only the
first** by two spaces - still valid YAML, `docker compose config --services` still lists every
service - and the census went from **12 of 13 recognised clients across 41 service blocks** to
**1 of 1 across 9**, and issued a verdict anyway. 92% of the denominator gone, no abort.

The guard added in round 2 was `$configured.Count -eq 0`, summed across BOTH files, so one
file collapsing to zero while the other still matched a single client never tripped it.
`$servicesSeen` was incremented and printed and **never asserted** - a printed number with
nothing to compare it against is decoration. A file that is present, readable, valid YAML and
accepted by docker but whose lines do not match `^  ([A-Za-z0-9_.-]+):\s*$` contributed zero
to both counters, silently; so did `services: {}` and a 0-byte file.

**The fix changes the layer rather than the regex.** Enumerating the indentations a pattern
must survive is the method this effort has abandoned four times, and the fifth would have won
too.

1. **`pg_stat_activity` is the denominator.** C.9's H1 asks for a census of *live connections*
   by role; live connections are ground truth and need no YAML parsing. The compose half is
   now explicitly secondary - it exists for the one thing a census cannot show, a client
   CONFIGURED to connect that happens to be idle (`openbrain-ext`,
   `openbrain-suggestion-worker`, both lazy pools).
2. **Compose is parsed by the real parser, per file.** `docker compose config
   --no-interpolate --format json`, which is already on this machine and is what docker itself
   uses. Service names, container names and the environment come from the resolved project;
   no line regex remains.
   Each file must contribute a measured service count **or the run aborts naming that file** -
   per file, not summed.
3. **`--profile *`.** A profile-gated service is invisible to a default `config`.
   `openbrain-idea-refinery` is exactly that, and it holds a live superuser connection today -
   without the flag it would drop out of the configured half while showing in the live one.
4. **The two halves are tied together on the way to green.** Every live client backend must be
   identifiable and present in the parsed compose set, or exit 2: a client the database can
   see and compose cannot is proof the configured half is short. It runs AFTER the unexplained
   set is computed, so a real superuser finding still reports as exit 1 rather than being
   downgraded to a shrug.
5. **The honest zero is still reachable.** `$configuredSuper` reaching zero is the goal of the
   promotion and remains exit 0; the `honest-zero` case asserts it.

Same live result as before the change, from a different mechanism: **12 of 13 recognised
clients, 9 unexplained, exit 1** - now over 30 + 6 = 36 services counted by docker, not 41
regex matches that included `volumes:` and `networks:` keys.

`redprove-census-cannot-measure.ps1` grows from 5 cases to 9, and the four new ones are the
reproduction:

| case | pre-fix | now | |
|---|---|---|---|
| one file `services: {}`, the other with a real client | **0** | **2** | the aggregate guard never fires; per-file does |
| one file re-indented two spaces, rogue client inside it | **0** | **1** | the reported defect, in fixture form |
| a live client backend no compose file contains | **0** | **2** | the live half sees what the compose half cannot |
| a client whose `DB_USER` is still `${...}` | 1 | **2** | right exit code, wrong reason: pre-fix it substituted to empty and fell through to the implicit-postgres rule |

Measured, not assumed: the new red-proof run against the **pre-fix** census fails **4 of 9**
and exits 1; against the fixed one it passes 9 of 9. Each new fixture is *established*
before they are trusted, the way the DENY ACE already was - the harness refuses to run if
docker does not accept the re-indented file as a 2-service project, or if the ghost client
never appears in `pg_stat_activity`, because either would let the case pass for the wrong
reason.

### Two things the clean-clone run found that the worktree could not

Both were found by running the fix from a clean clone before claiming it, not by reading it.

- **`docker compose config` fails outright in a clean clone.** OB1's compose has
  `${OPS_GATEWAY_KEY:?openbrain-ops-gateway needs its OWN key...}`, a *required* variable, and
  a clean clone has no `OB1/docker/.env` (it is gitignored). With substitution on, the parse
  exits 1 and the census correctly aborts - which would have made the census unrunnable on
  exactly the checkout C.7b validates from. Fixed with **`--no-interpolate`, unconditionally**
  rather than as a fallback, so the script behaves identically in the operator's checkout and
  in a clean clone instead of taking a branch in one that was never exercised in the other.
  It also means no secret is ever resolved into this script's memory. The cost is that a value
  which IS a variable stays one, so a *consulted* user or host still containing `${` - or a
  list entry with no value at all, which is inherited from the host environment - is a named
  cannot-measure rather than a value quietly read as "not postgres". Nothing in the fleet hits
  it today; the `unresolved` red-proof case does.
- **`--no-interpolate` stops compose normalising the list form.** With substitution on,
  `environment: [- OB1_DB_USER=postgres]` comes back as a JSON object; with it off it stays an
  array. The first draft of this fix read only the object form - and **aborted** on
  `open-notebook-backup` rather than reading it as not-a-client, which is the behaviour this
  whole item is about, even though it cost a round. Both forms are now flattened to
  key/value/was-a-value-given, and the `honest-zero` / `live-ghost` fixture feeds the list form
  on purpose so a one-form fixture cannot hide it again.

Final red-proof: **9 cases, 0 disagreements** against the fixed census; **4 of 9 disagree**
against the pre-fix one (`no-services`, `reindent`, `unresolved`, `live-ghost`), and every
`pre-fix` figure in that table is a measured exit code, not an assertion about the past.

### Filed, not fixed (C.10 - siblings of a recorded class)

- **2026-08-31 - four drill probes pass vacuously on an empty schema.** Verified by reading
  `scripts/checks/drill-app-role-not-superuser.ps1`: `:408` (G11, no view runs as its owner),
  `:431` (G14, no relation granted to PUBLIC) and `:440` (G15, no app role holds a `pg_*`
  role) each expect the literal `"none"`, which is exactly what
  `COALESCE(string_agg(...), 'none')` returns over ZERO rows; `:449` (G16, `ob_app` has USAGE
  on every public sequence) expects `"0"`, which is `count(*)` over zero sequences. Real, and
  the same class as everything else in this note: a check that is satisfied by the absence of
  the thing it checks. Not fixed here - the drill is accepted at 31 probes under four
  mutations and C.10 sends siblings of a recorded class to notes rather than to round N+1.
  Whoever picks it up: the fix is a companion assertion that the population is non-empty
  (a view/relation/sequence count > 0), not a change to the four predicates.
- **2026-09-01 - `env_file` values are not read under `--no-interpolate`.** A service whose
  database user arrived only via `env_file:` would be invisible to the configured half. Not a
  live gap: every `DB_USER` / `OB1_DB_USER` / `POSTGRES_USER` in both compose files is an
  inline literal (checked 2026-09-01), and `env_file` supplies only `POSTGRES_PASSWORD` there.
  Recorded because the same reasoning that justifies `--no-interpolate` has this as its edge.
- **2026-08-31 - the census now requires `docker compose` on PATH.** It always required
  `docker` (network inspect, exec); `config` is client-side parsing and adds no daemon
  dependency, but a checkout with the docker CLI and no compose plugin would abort at exit 2
  rather than degrade. That is the intended direction and is recorded here only so the
  dependency is not a surprise.
