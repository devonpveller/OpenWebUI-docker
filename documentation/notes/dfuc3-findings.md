# dfuc3 findings — DFU C.8 clause 3, the personal-plane boundary at the MCP doors

Findings sink for item C (worktree `work/dfuc3`), 2026-09-01/02. Everything below was
measured; each entry names the command or the probe. Production was read-only throughout: no
row was written, no credential changed, no service restarted, and the 1,129 personal rows
were not touched.

Validated against ai-stack `fba111d` + this branch, OB1 pinned `b604d55`.

---

## 1. Two failing doors are one connection

`openbrain-gateway` holds no database connection. It is an HTTP proxy
(`openbrain-gateway/app.py:41`, `OPENBRAIN_URL = os.environ["OPENBRAIN_URL"]`), and live it
is `http://openbrain-mcp:8000`. `openbrain-ops-gateway` is a second instance of the same
image with the same upstream.

So `door-openbrain-mcp-door`, `door-cloud-search-thoughts` and `door-mcp-read-tools` all
traverse **one** database connection, and `openbrain-mcp`'s `DB_USER` is the whole of the
minimal fix. The board reads as two independent failures; it is one.

Measured and reproduced end to end in `scripts/checks/drill-mcp-door-not-superuser.ps1`
(16 probes, 0 failures), which runs the **real** `openbrain-mcp-server:local` behind the
**real** `openbrain-gateway:local` against a throwaway built from the compose init chain.

## 2. H1's migration does not exist

`OB1/docker/init-app-role.sql`, `init-app-role-passwords.sh` and `revert-app-role.sql` are
cited by `H1-APP-ROLE-PROMOTION.md` (its Artefacts header), by `u8h1-findings.md` §10, and by
`drill-app-role-not-superuser.ps1:203-204`. They are on **no reachable commit of either
repository**:

```
git -C OB1 log --all --oneline --diff-filter=A -- 'docker/init-app-role*' 'docker/revert-app-role*'   # empty, 963 commits
find "D:/Open WebUI" -maxdepth 6 -name "init-app-role*.sql"                                            # nothing
```

`drill-app-role-not-superuser.ps1` therefore **aborts with exit 2 on every run** — its own
comment predicts exactly this ("both files live in the OB1 submodule at a commit that is NOT
on OB1's remote"). H1 was recorded as VALIDATED with a 31-probe drill; the drill cannot run,
and the migration it validated is gone. What survived is the prose.

This is the same failure mode the parent repo's CLAUDE.md warns about for gitlinks — an OB1
commit that was never pushed — arriving through a deleted worktree instead of a bad bump.
**Consequence for this item:** the SQL was re-materialised from H1's specification and lives
in **ai-stack** (`scripts/checks/sql/`), not in the OB1 submodule, so a clean clone of this
branch has it.

## 3. `fixture-cleaned-up` failed on the operator's data, and missed its own

The probe counted `metadata->>'exposure'='personal'` across all of `thoughts` and
`agent_memories` and required zero, reporting `0/0/0/1129/0 - the plane was left dirty` while
its own fixtures were `0/0/0` and the cleanup had worked perfectly.

Two defects, opposite directions:

- **It asserted on rows that are not its business.** "The plane is empty" and "my probe
  cleaned up after itself" were never the same claim. A checker that demands the absence of
  1,129 legitimate personal rows is demanding that personal data be deleted to make a check
  go green.
- **It missed rows that are.** `agent_memory_recall_traces` holds **72 rows** with
  `workspace_id='dfu-done-fixture'` in production — litter from every past run of
  `door-mcp-read-tools`. The cleanup never deleted them (it deletes
  `agent_memory_recall_items`, which cascade from `agent_memories`; a trace is the *parent*
  of its items and survived both) and the verification never counted them.

Fixed: four fixture counters including recall-traces, a delete for the traces, and the
production personal count reported but not asserted on. `thought_entities`,
`entity_extraction_queue` and `agent_memory_recall_items` are deliberately not counted —
all three are `ON DELETE CASCADE` from a table that is counted (measured), so counting them
would add three comparisons that cannot fail.

Red-proven both ways by `scripts/checks/redprove-fixture-cleanup.ps1`, which runs the **real**
`dfu-done.ps1` against a throwaway (`-DbContainer`): case A, 7 legitimate personal rows
present and nothing stranded, must **pass** (it did — under the old code it failed); case B,
a fixture thought and a recall trace stranded behind a delete-suppressing trigger, must
**fail** and must blame the fixture counters (it did: `2/0/0/1`).

## 4. Clause 3's H3 arm has never executed — a PowerShell dollar-quote

`dfu-done.ps1`'s corpus-predicate probe built its SQL as
`"DO $x$ BEGIN " + ... + "END $x$; "` — PowerShell double-quoted strings, so `$x` was
**expanded**, and under the file's own `Set-StrictMode -Version Latest` an undefined variable
is a terminating error. Clause 3 returned a single probe, `clause-3-threw`, verdict
indeterminate: *no door verdicts at all*.

It has been invisible because the arm is unreachable on the current production schema. It
runs only when `thoughts.exposure` is `NOT NULL`:

| database | `exposure` nullability | arm taken |
|---|---|---|
| production `openbrain-db` | `1/0` (nullable) | else-branch — no `$x$`, no crash |
| any volume built from the compose init chain | `1/1` (NOT NULL) | **the H3 arm — crash** |

So every live run took the safe branch, and the H3 arm would have started failing the moment
H3 landed in production — in the worst shape: not a wrong answer but no answer. Escaped
(`` `$x`$ ``) and the clause now evaluates on a chain-built database. Found because that is
the database this item's red-prove runs against.

## 5. `door-mcp-read-tools` — why it is indeterminate, and why "fixing" it would be worse

Two independent reasons, both verified in source:

1. **The fixture is the wrong shape for the tool.** `performRecall`
   (`agent-memory.ts:437-445`) is `FROM agent_memories am JOIN thoughts t ON t.id =
   am.thought_id` and orders by `t.embedding`. `dfu-done.ps1`'s fixture inserts
   `agent_memories` with no `thought_id` and no embedded thought, so **both** twins are
   dropped by the JOIN. No positive control comes back, and the probe correctly refuses.
2. **Even repaired, it would not test the boundary.** The door forces
   `exposure: ['ops']` into the SQL filter (`DEFAULT_RECALL_EXPOSURES = ["ops"]`,
   `agent-memory-policy.ts:86`; `agent-memory.ts:395`; `agent-memory-ops.ts:281-282`).
   `agent_memory_recall` therefore **cannot return a personal row whatever DB role it runs
   as**. A repaired probe would go green while proving the application-layer filter, not the
   RLS boundary the door is listed under.

**Recommendation, not applied:** either re-point this door at a tool that does not force
exposure (`list_thoughts`, which is what the other two doors use, and which does discriminate
by role — proven in the drill), or keep it and rename it so it claims the app-layer filter it
actually tests. Turning it green as it stands would be a check passing for a reason unrelated
to its name — the failure class this project keeps finding. Left indeterminate deliberately.

## 6. `door-wiki-compiler-output` — the premise for making it manual has expired

It was made a named manual check because a machine probe "could not fail": the compiler runs
on its own schedule, so querying `wiki_pages` for a seconds-old fixture returns nothing
whatever the boundary does. That reasoning was sound **when production held 0 personal rows**.

It now holds 1,129, and 47,835 wiki pages are published, so the question is decidable over
the real corpus without waiting for anything — and it is cheap (GIN-indexed, ~3s):

```sql
SELECT count(DISTINCT w.slug)
  FROM thoughts t JOIN wiki_pages w
    ON w.search_tsv @@ plainto_tsquery('english', left(t.content,120))
 WHERE t.exposure='personal'
   AND position(substring(t.content from 21 for 80) in w.body) > 0;
```

**Not wired up, and this is the reason.** Run today it returns **330 pages** carrying an
exact 80-character substring of one of 135 personal thoughts. That would turn clause 3 red on
a false attribution, because the compiler did not put them there:

- every personal row was **created** 2026-09-01 10:25–11:07 (`created_at` min/max; all 1,129
  have `updated_at IS NULL`);
- the newest `wiki_pages.updated_at` in the entire table is **2026-08-31 14:30**.

Every published page predates every personal row by at least ~20 hours. A page written before
a row existed cannot have been written from it. The text is published because it also exists
elsewhere in the ops corpus — 174 of 1,127 personal rows have their text in an `ops` thought,
and 47 of the 83 distinct matching fragments are in `source_chunks` — which is where the
compiler got it.

**Residual, for the operator, not for clause 3:** material that is now labelled `personal` is
nonetheless present verbatim in 330 published wiki pages, arriving from the ops/sources
corpora before the reclassification. The exposure boundary does not cover already-published
output, and nothing re-examines the wiki when a thought's plane changes. That belongs to
whoever owns wiki publication; it is recorded here rather than acted on (C.10).

## 7. Production/chain drift found on the way

Both databases are supposed to be the same chain. On the properties clause 3 depends on they
are not:

| property | production | fresh chain |
|---|---|---|
| ops-plane predicate | `ob_corpus_on_ops_plane(metadata)` | `ob_corpus_on_ops_plane(exposure)` |
| `thoughts.exposure` | nullable | `NOT NULL` |
| `init-graph-plane-rls.sql` (200-) | **not applied** (`ob_relation_governed` = 0) | applied |
| `service_role` on `agent_memories` | `DELETE,INSERT,SELECT,UPDATE` | `SELECT` only |

Benign for the fix *today*: the column and the jsonb mirror agree on all 14,140 rows
(`col=personal meta=personal n=1129`, `col=ops meta=ops n=13011`), so either predicate hides
the same rows. Not benign for the promotion's ordering — see §6 of
`C3-MCP-DOOR-PROMOTION.md`, and probes H1/H2 of the drill, which measure both grant shapes.

## 8. H1's justification for its step 3 is stale

`H1-APP-ROLE-PROMOTION.md` §6 step 3 reads "Production holds **0 personal rows today** (all
13,001 thoughts and 21 memories are `exposure='ops'`, measured 2026-08-31), so nothing is
lost now." Measured 2026-09-01: **1,129** personal thoughts of 14,140.

The promotion is still right, but not for that reason. The honest statement is "1,129 rows
become invisible to the agent plane, and that is the intent" — not "nothing is hidden".

## 9. Evidence

| what | where |
|---|---|
| RED/GREEN through the real doors, throwaway only, 16 probes | `scripts/checks/drill-mcp-door-not-superuser.ps1` |
| the migration and its revert | `scripts/checks/sql/init-app-role-memory.sql`, `revert-app-role-memory.sql` |
| the corrected check's failing cases, re-runnable | `scripts/checks/redprove-fixture-cleanup.ps1` |
| the promotion plan and its revert path | `documentation/implementation-guide/dark-factory-unification/C3-MCP-DOOR-PROMOTION.md` |

Throwaway hygiene, verified after every run: containers `wt-dfuc3-drill-{db,mcp,gw}` and
`wt-dfuc3-rp-db`, networks `wt-dfuc3-drill-net` and `wt-dfuc3-rp-net` all removed; no
`ai-stack_*` network ever attached; no `:local` image built or retagged; `openbrain-db` never
written to (re-verified after the runs: 1,129 personal rows intact, 0 rows matching the drill
marker, `ob_app_memory` absent from production).
