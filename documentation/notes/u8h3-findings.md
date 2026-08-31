# u8h3 findings — DFU C.9 H3 (exposure becomes a typed column), and what re-running the boundary drills found

Item `u8h3`, branch `work/u8h3`, work line `refactor/ai-stack-cleanup`.
Written 2026-08-31. Everything below was measured, and each entry says how.

This is the findings sink for the H3 item. Findings about OTHER items live here rather than in
the deliverable, per the operator's 2026-08-28 rule — and most of what follows is about other
items, because the drills this item salvaged had never been run against the merged design.

---

## C.7b — the sha this was validated at

The work line moved while this item was in flight (`a9f4bb4` -> `1a6b0b8`, PLAN.md §2.1
amendment A3 only). Per §C.7b the branch was REBASED onto the line FIRST and every column
re-run afterwards; a pass taken against `a9f4bb4` would describe a tree the line has left.

```
work line base : 1a6b0b813e241cfb4b74659cbb2c11c8f86616aa  (refactor/ai-stack-cleanup)
validated at   : ef0c2b74493fd3acbdf5e0ad2a07c535c4d77f95  (work/u8h3)
OB1 gitlink    : 4e2239305150c01e79ba860d0226eeb6ea9480a1  (pushed to origin BEFORE the bump,
                 on feat/agent-memory-exposure-column; descends from the line's 4fdc21c)
```

The only commit after `ef0c2b7` is the one that adds this record. It touches this file and
nothing any check reads, which is stated rather than left to be inferred: a validation sha
that is not the branch tip is a stale pass unless the difference is named.

Re-run at that sha, in one checkout, each throwaway on its own network:

| check | result |
|---|---|
| `deno check *.ts` (13 files) + `deno test` | clean / **133 passed, 0 failed** |
| `scripts/checks/prove-agent-memory-rls.ps1` | **PASSED - 68 checks**, every green with a red beside it |
| `scripts/checks/drill-personal-plane-exclusion.ps1` | **105 passed, 0 failed, 18 named gaps**, exit 2 |
| `scripts/checks/verify-dfu-done.ps1` | **GREEN - 201 assertions, 0 failed**, 8/8 clauses with a constructed failing case |
| `scripts/checks/dfu-done.ps1 -Only 3` | **UNMET**, 12 of 14 evaluated, the two `[fail]`s are the superuser doors (§3.4) |
| full 29-migration initdb chain on a throwaway | no init errors; 195's self-test and 200 §9's notice both printed |

---

## 0. What H3 itself did (the short version)

`exposure` is a `TEXT NOT NULL CHECK (exposure IN ('ops','personal'))` column on
`agent_memories` and `thoughts`; `ob_memory_on_ops_plane` and `ob_corpus_on_ops_plane` read the
COLUMN; `metadata->>'exposure'` is a non-authoritative mirror. No default, deliberately.
Every write path states it. Live counts before and after are identical (13,001 thoughts, all
ops; 21 memories, all ops), and the absent-key backfill branch touched **0 rows**, because
`190-` had already labelled every row `ops` — so H3 changed no row's visibility, which the
brief required.

Applied to the live volume, verified, and **reverted in the same window**; see §6.

---

## 1. `agent_memories_personal_plane` had silently widened to `service_role` — CONFIRMED, with a RED

`180-init-agent-memory-rls.sql` created it `TO ob_plane_personal`. The round of
`200-init-graph-plane-rls.sql` that added the `thought_id` arm (§2c, closing the FK existence
oracle) DROPped both `agent_memories` policies and recreated **both** `TO service_role`.

Nothing went red, because `ob_plane_personal` is a MEMBER of `service_role`, so every existing
assertion — all of which were about a caller that was ALLOWED to see the row — kept passing.
What changed is that the policy went from "the personal plane, for its own tenant" to "ANY
`service_role` session, for any tenant it can name". `ob.user_id` is an ordinary GUC and any
role may `SET` it.

Measured on a throwaway built from the full initdb chain, one personal fixture, one ops control:

```
TO service_role      D_RED_personal_via_tenant=1   D_RED_summary_leaked=H3PROBE personal
TO ob_plane_personal E_restored_personal_via_tenant=0
```

**Fixed** in this item (200 §2c restored to `TO ob_plane_personal`) because clause 3's door
attack has to be green against the new predicates and this policy is one of them.
`thoughts_personal_plane` was never touched by that file and still named the right role, which
is the asymmetry that made it findable.

**Exploitability today:** low. PostgREST does not let a request set an arbitrary GUC, so the
practical caller would have to be something that can already run SQL. It is still a policy
wider than its design, on the table the boundary exists for.

**Standing guard:** `prove-agent-memory-rls.ps1` now asserts, from `pg_policies`, that EVERY
`*_personal_plane` policy in the schema is granted to `ob_plane_personal` and nothing wider.

---

## 2. The recall lane was broken for any non-superuser — CONFIRMED, and FIXED

`agent_memory_recall_traces` is governed by `ob_trace_on_ops_plane(request_payload)`, which
reads `request_payload->'enforced_exposure'`. `180-`'s comment describes that field and `200-`
§1b hardened its empty-array case. **`performRecall` never wrote the key.**

So the trace of every recall claimed no plane, and for a non-superuser connection the INSERT
failed `42501` — **on the `RETURNING id` clause specifically**, because RETURNING makes
PostgreSQL apply the SELECT policy to the new row as well as the WITH CHECK. The whole recall
lane was unavailable to any door the boundary can actually bind. Nothing had noticed because
every writer in this stack connects as `postgres`.

```
PostgresError: new row violates row-level security policy for table "agent_memory_recall_traces"
    at async performRecall (file:///app/agent-memory.ts:456:19)
```

**Fixed** here (OB1 `4e22393`): the payload now carries the ENFORCED list — not the requested
one, because a trace that recorded the request would claim a plane the recall did not run on.
Two regression tests assert it on the PARAMETER, not on the SQL text: the statement reads
`$4::jsonb` either way, so a text assertion would have passed throughout the defect.

Fixed rather than reported because without it the drill's own recall attacks are vacuous and
H1 cannot be attempted at all.

---

## 3. C.9 H1 is bigger than "change DB_USER" — four blockers, measured

Running the drill's doors as a non-superuser (which is what H1 will make production do) turned
up four things that stop working. None of them is caused by H3; all of them are H1's.

1. **`agent_memory_audit_events` is closed to `service_role` for WRITES too.** `200-` §2b sets
   `USING (false) WITH CHECK (false)` on the explicit reasoning that "THE WRITER IS A SUPERUSER
   TOO: openbrain-mcp runs DB_USER=postgres". A non-superuser writer therefore cannot write its
   own audit row, and `agent_memory_writeback` — thought, memory and audit event in ONE
   transaction — fails entirely.
2. **`200-` §6a withdrew INSERT/UPDATE/DELETE from `service_role` on 8 agent-memory tables**
   (measured by the drill: `8`). Same reasoning, same consequence: writeback, recall tracing
   and the review door all stop.
3. **The extensions-server CRM surface goes dark.** `professional_contacts` is governed by
   `auth.uid() = user_id`, and `auth.uid()` in this database is a stub returning `NULL`
   (`CREATE FUNCTION auth.uid() RETURNS uuid ... SELECT NULL::uuid`). So the policy is
   `NULL = user_id` for every non-superuser and `openbrain-ext` answers nothing to anybody.
4. **And openbrain-ext, as it runs today, LEAKS.** Same image, connected as `postgres`:
   `link_thought_to_contact` returns a personal-labelled thought's content verbatim and copies
   it into `professional_contacts.notes` — a third home with no exposure label. RLS binds no
   superuser, FORCE included. Production holds 0 personal rows, which is exactly the property
   C.8 clause 3 says is not containment.

H1 needs a decision on each: which writes a non-superuser app role legitimately makes, and
whether `auth.uid()` gets a real implementation or those tables get a different predicate.

---

## 4. U5's "AND the attempt is visible in an audit record" is NOT met — by either configuration

U5's *Validated by* column asks for "mechanically stopped **and** the attempt is visible in an
audit record". Under the database boundary you get one or the other:

* **as a non-superuser** — the read is stopped, and `auditRefusal` never fires. It fires only
  after a bare `SELECT 1 FROM agent_memories WHERE id = $1` confirms the row exists, and that
  probe is bound by the same policy that hid it. Measured: 1 of 7 targeted doors left a
  refusal row.
* **as a superuser** (production) — the probe succeeds and the record IS written, and nothing
  was stopped.

Neither satisfies the column. Closing it needs an **elevated existence probe** — a
`SECURITY DEFINER` helper that answers "exists" without returning the row — which is an H1/H4
design decision, not an H3 one.

Related, same family: **`recall_requested` / `exposure_override_denied` / `requested_exposure`
have no writer left in the tree at all.** Grep the whole of
`integrations/kubernetes-deployment`: those strings appear in exactly one place, a list of
event-type names in a test. The writer lived in `agent-memory-plane.ts`, which amendment A2
removed along with the reader guards — and the audit half of U5's column went with it,
silently, because nothing on the work line ran the drill afterwards.

The drill reports all of this as **NAMED GAPS** and exits **2**. The assertions were not
deleted: a deleted assertion is a requirement that leaves no trace.

---

## 5. What the salvaged drills were, and what changed in them

Both existed ONLY on `work/u5rls` (`drill-personal-plane-exclusion.ps1` also on `work/u5pplane`),
which the operator was about to abandon. The work line had **zero** files under `scripts/`
covering the boundary — H4 has to wire "the boundary drill" into CI and there was nothing to
wire.

**`prove-agent-memory-rls.ps1` — 68 checks, all green.**
* Its RED was "the chain MINUS `init-agent-memory-rls.sql`". That had stopped being a red:
  `195-` on its own installs a working boundary on both tables, so the "red" database would
  have had one. The red is now the chain minus ALL FOUR boundary migrations, derived from one
  list.
* New §3b: the write contract, at the database, as the SUPERUSER (because NOT NULL and CHECK
  bind a superuser where RLS does not) — absent, malformed and wrong-case refused on both
  tables, three legal writes accepted as controls, and the same absent write ACCEPTED on the
  red schema.
* New: the `*_personal_plane` role check (§1 above), behavioural and catalogue.
* TRAP 4 had turned red on a boundary that got STRICTER — `200-` §6a withdrew the door's INSERT
  entirely, so the old probe failed with `permission denied` before any policy was consulted.
  It is now two claims: the grant is closed (production's state), and the WITH CHECK underneath
  it still refuses a personal write when the grant is temporarily restored inside a rolled-back
  transaction.
* Its live census read a column that may not exist, counting `to_jsonb(row)->>'exposure' IS
  DISTINCT FROM 'ops'` — which is NULL for every row on a database without the column, so it
  reported the ENTIRE live plane as personal. A probe that reads a missing column does not
  measure nothing; it measures everything, in the alarming direction.

**`drill-personal-plane-exclusion.ps1` — 105 passes, 0 failures, 18 named gaps, exit 2.**
* Its RED phase patched `agent-memory-plane.ts` (3 anchors) and `_shared/corpus-plane.mjs`.
  **Neither file exists.** It would have died at `Set-RedAnchor` with "matched 0 times". The red
  is now the doors' database ROLE: GREEN as a per-run non-superuser, RED as `postgres`.
* Its mirror assertions inverted. "the personal fixture put NOTHING in the shared corpus" was
  true when containment meant refusing to mirror; `thoughts` is now RLS-governed, so the mirror
  is written for both planes and the claim becomes "written, and BOUND".
* The ops door can no longer MINT a personal memory, so the refused writeback became an
  assertion — access bounds writes, end to end over HTTP.
* ATTACK 14's red is now the policy itself, widened to `USING (true)` in the throwaway and
  restored (asserted both ways).
* The red phase also STATES which greens do not rest on the database: the recall filter, the
  inspect tool and the review door still carry their own plane clause in SQL and hold for a
  superuser too. Real defence in depth, printed rather than assumed.

---

## 6. The live promotion was applied, verified, and reverted — deliberately

`195-` was applied to `openbrain-db` under an `open-brain` lease, verified (counts identical,
ops path unbroken, write contract enforced), and then reverted, because **the schema half and
the code half must land together**: the deployed `openbrain-mcp-server:local` image still
contains three exposure-less `INSERT INTO thoughts`, and under H3 those are a
`not_null_violation`. Measured on the live volume while applied:

```
ERROR:  null value in column "exposure" of relation "thoughts" violates not-null constraint
DETAIL: Failing row contains (13744, H3-LIVE-DEPLOYCHECK, ...)
```

Rebuilding a `:local` tag and recreating a production container is a gated deploy, not a test
(`scripts/agent-harness/README.md`), and that gate was not taken. The promotion procedure —
build, apply, recreate, in one window, in that order — is written into
`PROMOTION-RUNBOOK.md`. The live volume was returned to the state it was found in and the
deployed write path re-tested and working.

**Residue:** the `exposure` column remains on both live tables, populated, nullable and read by
nothing (class 4 forbids dropping a column). Re-applying `195-` is idempotent.

**Also measured:** the live volume runs `200-` **round 1**, not the work line's round 5
(`ob_relation_governed` absent; `agent_memory_audit_events` still `USING (true)`). That is the
`u5graph` item's deployment gap, not this one's, and it must be promoted AFTER `195-`.

---

## 7. `dfu-done.ps1` clause 3, verbatim, live, 2026-08-31

```
CLAUSE 3 [UNMET] The personal-plane constraint is lifted by VALIDATION, never by emptiness
   coverage: evaluated 12 of 14 ...
   [fail] door-openbrain-mcp-door (exit 200)
        HTTP 200; the door RETURNED the personal fixture; the door connects as 'postgres'
        (rolsuper/rolbypassrls = t/t)
   [fail] door-cloud-search-thoughts (exit 200)
        HTTP 200; the door RETURNED the personal fixture
```

Both failures are §3.4's finding — superuser doors — i.e. C.9 H1. Every non-superuser door in
the same run passed with a live ops positive control: PostgREST on `thoughts`, on
`agent_memories`, on the `thought_entities` join and on `entity_extraction_queue`'s
fingerprint, plus a 56-table / 244-text-column / 199-jsonb-key surface sweep. The run's own
cleanup confirms production shows **0 personal rows** in either corpus.

Clause 3's `corpus-predicate-fail-closed` probe now branches on the schema, read from
`information_schema` at run time: on a database where the column is enforced it asserts the
STRONGER property (the unlabelled write is REFUSED), otherwise the original one (the unlabelled
row is INVISIBLE). Without the branch it would have gone INDETERMINATE against H3 — the INSERT
raises, psql exits non-zero — and the clause would have silently stopped being decided, which
is dfu-done's own rule 2 failing through a schema change rather than a code change. Both arms
were exercised: the old one live (above), the new one on the throwaway
(`DFU-U:1` with the ops control `O:1`).

`verify-dfu-done.ps1` — the drill that proves the arbiter can fail — is GREEN after these
changes: 201 assertions, 0 failed, 8 of 8 clauses with a constructed failing case.

---

## 8. Smaller things, recorded rather than fixed

* **`init-agent-memory-rls.sql` cites a file that does not exist.** Its comments point at
  `OB1/integrations/kubernetes-deployment/agent-memory-plane.ts` as the home of
  `planePredicate` / `corpusPlanePredicate` / `mirrorsToUnifiedSearch` / `auditRefusal`. A2
  deleted that module. The SQL is correct; its provenance comments are stale.
* **`agent_memories` has no `embedding` column** on this schema, though the drill asserted the
  personal memory "carries its OWN embedding". That assertion belonged to the era when a
  personal memory had no mirrored thought to rank against; it is gone with the mirror change.
* **The gateway records nothing when it denies a tool by allow-list.** Different cause from §4:
  the gateway refuses before any database session exists, so there is nothing to record from.
  Closing it is a gateway change.
* **`thought_stats` counted the on-plane subset using `metadata->>'exposure' IS NULL OR = 'ops'`**
  in the drill — the pre-190 fail-open rule, still passing because the fixtures' mirrors agreed
  with their columns. Now reads the column with no `IS NULL` arm.
