# u8h3 findings — DFU C.9 H3 (exposure becomes a typed column), and what re-running the boundary drills found

Item `u8h3`, branch `work/u8h3`, work line `refactor/ai-stack-cleanup`.
Written 2026-08-31. Everything below was measured, and each entry says how.

This is the findings sink for the H3 item. Findings about OTHER items live here rather than in
the deliverable, per the operator's 2026-08-28 rule — and most of what follows is about other
items, because the drills this item salvaged had never been run against the merged design.

---

## C.7b — the sha this was validated at

**SUPERSEDED BY §14 for the OB1 gitlink and every re-run.** Round 2 (an operator review of
this item) changed the migration, the drill and this file; the work line did not move, so no
second rebase was required, but the gitlink below is no longer the one under test. Read this
section for what round 1 established and §14 for what currently holds.

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
| `scripts/checks/drill-personal-plane-exclusion.ps1` | **105 passed, 0 failed, 18 named gaps**, exit 2 *(the figures AT `ef0c2b7`; round 3 grew the set to 25 - see §12)* |
| `scripts/checks/verify-dfu-done.ps1` | **GREEN - 201 assertions, 0 failed**, 8/8 clauses with a constructed failing case |
| `scripts/checks/dfu-done.ps1 -Only 3` | **UNMET**, 12 of 14 evaluated, the two `[fail]`s are the superuser doors (§3.4) |
| full 29-migration initdb chain on a throwaway | no init errors; 195's self-test and 200 §9's notice both printed |

### Round 5 re-validation (§C.7b), from CLEAN CHECKOUTS at the rebased sha

The branch was rebased onto the work line's `8e2eaf4` (U4 merged, ledger work landed); the
round-4 pass at `90c7cde` was stale under §C.7b and a clean `git merge-tree` is not a
revalidation. Round 5's changes are at **`5ff217b`**. Two throwaway `git clone`s of the branch
at that exact sha, submodule initialised to the recorded gitlink `debbbaa`, **one suite per
checkout**:

| checkout | suite | result |
|---|---|---|
| `cc1` @ `5ff217b` | `check-corpus-exposure-producers.ps1 -SelfTest` | **PASSED**, exit 0 - 6 assertions red/green, cases 4-6 record the blind spots |
| `cc1` @ `5ff217b` | `check-corpus-exposure-producers.ps1` (full scan) | **OK - all 13 recognised sites state their plane** (742 files), exit 0 |
| `cc2` @ `5ff217b` | `drill-personal-plane-exclusion.ps1 -SelfTestLedger` | **PASSED**, exit 0 - all **25** ids register on success; `AUDIT-INSPECT` closes at `closed=1 vanished=0 fails=0 EXIT=0` |
| `cc2` @ `5ff217b` | `drill-personal-plane-exclusion.ps1 -SelfTestVacuity` | **PASSED**, exit 0 - an empty universe cannot reach PASS |
| `cc2` @ `5ff217b` | `drill-personal-plane-exclusion.ps1 -AcceptDispositionedGaps` (FULL, throwaway plane) | **CONTAINMENT GREEN, 106 checks passed, 0 failed**; `GAP LEDGER - 25 fired, 25 dispositioned`, exit 0 |

The full drill built its own `pp-drill-49ce9d4a-*` plane on its own network and tore it down
(0 containers, 0 networks left). It never touched `openbrain-db`, and nothing in this round
wrote to the live database or deployed anything.

**Red-proofs re-run at `5ff217b`, each by reverting exactly one line:**

* evidence test back to `$evidence -match 'exposure'` -> self-test cases **3b and 3c FAIL**, exit 1.
* one `PassGap` back to `Pass` -> `BAD 1 dispositioned id(s) have NO success route ...
  AUDIT-RECALL-OVERRIDE`, ledger self-test **FAILS**, exit 1.

The `105 passed / 18 gaps` figures in the table above are the round-2 measurement and are kept
as a historical record. The current figures are **106 passed / 25 gaps**, and neither is
written into any check - both are printed by the run.

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

**`drill-personal-plane-exclusion.ps1` — 105 passes, 0 failures, 18 named gaps, exit 2** *(as it stood at the end of round 2; round 3 added seven gaps and changed the pass count - §12, §17, §18).*
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

---

# ROUND 2 - the review that found the cutover sound and the DOOR not

Added 2026-08-31 after an operator review of this item. The cutover, the constraint battery and
the salvaged drill were re-verified and left alone. Four things were wrong, all of them one
layer up from the schema, and each was RED-PROVEN on a throwaway built from the full
29-migration initdb chain before being fixed.

## 9. The door defaulted exactly what the column refuses (R1)

`upsert_thought` read `COALESCE(NULLIF(v_meta->>'exposure',''),'ops')`. Measured, before:

```
probe     | exposure     <- all three WROTE A ROW, on the WIDER plane
absent    | ops
empty     | ops
jsonnull  | ops
' '       | ERROR  upsert_thought: metadata.exposure =   is not a plane
'PERSONAL'| ERROR  ...
```

So the file's own header sentence - *"Anything else is REFUSED here rather than silently
coerced, so a typo is a loud failure at the door"* - was half false, and the false half was the
half H3 exists to remove. The same file's section 8(b) FAILS the migration if the COLUMN carries
a DEFAULT, on the grounds that *"a writer that omits the column would then succeed silently on a
plane it never stated"*. The COALESCE was that DEFAULT, moved one layer up where 8(b) could not
see it. C.9 H3 sides with 8(b).

**After:** absent and JSON null are `not_null_violation`; `''`, `' '`, `'ops '`, `' ops'`,
`'OPS'`, `'Ops'`, `'opsy'`, `'"ops"'` are `check_violation`; `'ops'` is accepted. Twelve cases,
asserted inside the migration (section 8c) so they run in BOTH places the migration reaches.

**And `'personal'` is refused too.** The door advertised "an explicit demotion to personal is
honoured, because narrowing is always allowed". Measured, it cannot deliver one:

```
as a BOUND role (NOSUPERUSER NOBYPASSRLS, member of service_role):
  SELECT upsert_thought('b-personal','{"metadata":{"exposure":"personal"}}')
  ERROR:  new row violates row-level security policy for table "thoughts"
```

`thoughts_personal_plane` is granted `TO ob_plane_personal` and requires
`user_id = ob_current_user_id()`; the ops door has neither role nor tenant. As a superuser the
insert succeeds only by bypassing the boundary, and writes a row with `user_id IS NULL` that no
personal-plane session can ever read. Refusing, with the reason, is the only outcome that is
neither an error nor an orphan.

**OPEN, and named rather than invented: there is no narrowing path for a `thoughts` row.**
`agent_memories` has one (`agent_memory_review`'s `restrict_scope`). The corpus does not, and
adding a policy that admits a narrowing write is a boundary change, not an H3 one. Recorded
here; not built.

## 10. The mirror desynced in BOTH directions, and the runbook's argument rested on it (R2)

Measured, before:

```
r2-personal (personal row, re-upserted with NO exposure key)  col=personal  mirror=ops
r2-ops      (ops row, "demoted" through the door)             col=ops       mirror=personal
                                     -> on_ops_by_column = t, on_ops_by_mirror = f
```

`PROMOTION-RUNBOOK.md`'s reason why `195-` does not conflict with the ROUND-1 `200-` still on
the live volume was *"the round-1 write gate reads the jsonb mirror, which every writer keeps in
step with the column"* - a premise about writer discipline, falsified by the door in the very
migration being promoted.

**This is not tidiness.** Read live off `openbrain-db` 2026-08-31, the round-1 gate is:

```sql
CREATE OR REPLACE FUNCTION public.queue_entity_extraction() ... SECURITY DEFINER ...
  IF NOT COALESCE(public.ob_corpus_on_ops_plane(NEW.metadata), false) THEN
    DELETE FROM public.entity_extraction_queue WHERE thought_id = NEW.id; RETURN NEW;
  END IF;
  INSERT INTO public.entity_extraction_queue (thought_id, ..., source_fingerprint, ...)
```

so a row with `column='personal'` and `mirror='ops'` gets `sha256(content)` written into the
graph queue - by a definer function, across the boundary that gate exists to hold.

**A third desync, on the only scheduled producer.** `recipes/entity-wiki/generate-wiki.mjs`'s
idempotent dossier path does `sb.patch("thoughts?id=eq.N", { content, metadata, embedding })`,
and that `metadata` object had no `exposure` key - so every wiki compile REPLACED the mirror
with an object that did not contain it, deleting it. Fixed by putting `exposure: "ops"` on the
shared metadata object, which is both the PATCH body and the rpc payload.

**THE DECISION, stated: the COLUMN is the source of truth, and NOTHING READS THE MIRROR.**
Both halves of the operator's either/or were taken, because each alone is fragile:

* the door keeps them in step (INSERT writes both from one value; UPDATE writes the mirror FROM
  the column, repairing a row that arrives disagreeing) - but a `PATCH` that replaces `metadata`
  wholesale will always be able to drop the mirror, and "every writer remembers" is the model
  self-restraint this workspace rejects;
* so `195-` section 7b also re-points the LAST READER at the column, and section 8(d) ASSERTS
  over `pg_policies` and over `pg_proc.prosrc` that no policy and no function body reads
  `metadata->>'exposure'` for a trust decision. A desync can no longer decide anything.

Measured on the live database before the change, that gate was the ONLY function body reading
the mirror (`pg_proc` scan over `prosrc`: one hit). The runbook's argument now cites the
assertion instead of the premise.

Note the scan's own trap, hit on its first run: `LIKE '%metadata%exposure%'` matched the
CORRECTED gate, whose body reads `metadata->>'generated_by'` on one line and `NEW.exposure`
twenty lines later. A check that fires on code that is right is a check nobody keeps, so the
scan is whitespace-normalised and anchored on the actual read forms.

## 10b. The transition trigger did not fire on the transition

`200-`'s own TRIGGER-DISPOSITION comment claims *"an ops-to-personal transition deletes the
existing one"*. `trg_queue_entity_extraction` is `AFTER INSERT OR UPDATE OF content, metadata` -
and once the plane lives in a COLUMN, the only way to make that transition is
`UPDATE thoughts SET exposure='personal'`, which touches neither.

```
RED  : queued after ops insert                     1
       queued after ops->personal (column only)    1   <- sha256 of now-personal content
GREEN: queued after ops->personal (column only)    0
```

`195-` section 7b widens the column list to `(content, metadata, exposure)`. This is a defect in
the `u5graph` item's file, found from here; it is fixed in `195-` because re-pointing the gate
at the column without it would have made this file's gate weaker than the one it replaced.

## 11. Three drill checks that could not fail, and one red that never ran (R3)

`drill-personal-plane-exclusion.ps1` ATTACK 1, 3 and 8 each ended:

```powershell
} else {
    Note "... Defence in depth, stated."
    Pass "ATTACK N is guarded in the APPLICATION as well as in the database - stated, not assumed"
}
```

Both branches Pass. All three fired in the verifier's run, so all three reds were vacuous - the
defect class this drill exists to catch, inside the drill.

**ATTACK 8's was worse than unfalsifiable: its claim was false.** The red passed
`reviewer = "drill-red"` while `REVIEW_SCHEMA` requires `actor: { label }`, so zod rejected the
call and it never reached the review path. The drill read "the memory is still personal" as "the
review door filters on the plane in SQL". It does not: `reviewMemory` selects
`FROM agent_memories WHERE id = $1 FOR UPDATE` with **no exposure predicate at all**
(`agent-memory-ops.ts`, read 2026-08-31). The only thing in front of ATTACK 8 is the row-level
policy. With `actor` passed, the red reproduces immediately: *"RED CONFIRMED (ATTACK 8) - as
postgres, promote_exposure MOVED the personal memory onto the ops plane"*.

**For ATTACKS 1 and 3 the guards are real**, so the red now removes BOTH layers. `Set-RedAnchor`
patches the server-side clauses out of a copy of the exported gitlink tree - anchored, with the
match count ASSERTED, so a guard that moves FAILS the drill instead of producing an unpatched
image whose non-leak reads as containment - builds a second image, and runs each attack twice:

| | ATTACK 1 (recall) | ATTACK 3 (inspect by id) |
|---|---|---|
| RED-A: patched + `postgres` | **leaks** - must, or the green measures something else | **leaks** |
| RED-B: patched + app role | **nothing** - the database, on its own | **nothing** |

**And the first version of that patch was itself vacuous.** Dropping the clause without a cast
left `$2` untyped; every patched query died with `could not determine data type of parameter $2`.
RED-A failed loudly and correctly - and **RED-B PASSED, on an error**. The cast
(`$n::text[] IS NOT NULL`) fixes the query, and each RED-B now fires the SAME call at the SAME
door for the OPS control first: if the control does not come back, RED-B FAILS rather than
reading a broken query as a boundary.

## 12. Exit 2 is red in CI, and H4 is about to wire it (R4)

The drill reports 0 failures, a set of named GAPs and EXITS 2. H4 wires it into CI on
`development`, where 2 is a failing build - so the first green tree hits a red gate for a reason
that is not a defect, and the first fix anyone reaches for is `|| true`.

> **CORRECTED, ROUND 5 (2026-08-31).** This section said "18 GAPs" in four places and kept
> saying it after round 3 grew the set to **25**. The count is not written down anywhere any
> more, here or in the drill: `$GAP_DISPOSITIONS` is the set, and the run's own `GAP LEDGER`
> line prints how many fired. A figure a human re-types is a figure that drifts from what it
> describes - the same failure as the hand-list of producers, one layer up, and it was being
> offered as evidence.

**None of them are H3's to close.** The 25 break down as 13 `AUDIT-*` (the audit-record gap -
`auditRefusal`'s existence probe is bound by the policy that hid the row, so closing it needs a
SECURITY DEFINER probe, an H1/H4 decision), 3 `EXT-*` (`openbrain-ext` connecting as `postgres`,
H1, measured), 2 `LIFT-*` (the lift's conjunction, which cannot close while the thirteen are
open), 6 `VACUOUS-*` (assertions whose universe is empty *because* the thirteen are open, and
which used to print PASS off it) and `RED-COVERAGE`.

So each gap now carries a stable ID and `$GAP_DISPOSITIONS` names it with its owning item. The
bare exit code is UNCHANGED (2 with any gap open). `-AcceptDispositionedGaps` - what CI runs -
exits 0 only when every gap that fired is in the ledger, exits 2 naming any gap that is NOT, and
FAILS when a gap in the ledger stops firing. A count-based budget would have passed
"17 old + 1 new"; a ledger by name does not. Written up in `PROMOTION-RUNBOOK.md` under
"The drill's exit code, and what CI reads (C.9 H4)" so H4 does not discover it.

## 13. Out of scope, stated so it is not re-found as new

* **`dfu-done.ps1 -Only 3` is UNMET with 12 of 14 evaluated**, and both `[fail]`s - the RAW
  `openbrain-mcp` door and the CLOUD door - are because they connect as `postgres`
  (`rolsuper`/`bypassrls`). **That is H1, not H3.** Not touched here; section 7 above is the
  same finding from the previous round.
* **`recipes/instagram-import/import-instagram.mjs` sets `sensitivity_tier: "personal"`** and now
  also `exposure: "ops"`. Those are DIFFERENT AXES - `sensitivity_tier` is a handling label on
  the row, `exposure` is the access plane - but a reader will trip over the pair. The recipe is
  mounted and never started; no row it would write exists. Stating `'ops'` reproduces 190's
  ruling for the general corpus rather than inventing a plane, and whether an Instagram import
  belongs on the personal plane is an operator decision, not a migration's.
* **`recipes/vercel-neon-telegram/src/lib/db.ts`** inserts into a `thoughts` table with a
  `source` column this schema does not have. It targets a separate Neon deployment, not
  `openbrain-db`, and was left alone.
* **`recipes/adaptive-capture-classification/capture-with-gating.ts`** is an EXAMPLE ("replace
  with your MCP tool call"), but it is an example of writing THIS schema, so it was given the
  column - a copied example that produces a `not_null_violation` teaches the wrong thing.

## 14. ROUND 2 - the sha this was re-validated at, and every re-run

The work line did NOT move during round 2: `refactor/ai-stack-cleanup` is still `1a6b0b8`, and
it is this branch's merge-base, so C.7b required no second rebase. The OB1 gitlink DID move.

```
work line base : 1a6b0b813e241cfb4b74659cbb2c11c8f86616aa  (refactor/ai-stack-cleanup, unmoved)
OB1 gitlink    : 4e2239305150c01e79ba860d0226eeb6ea9480a1
              -> ac4e3450bb916369b8f587909e69649a724536ca  (the four fixes)
              -> 8ebe19780ce93613689fd8241d787c1a77749454  (comment only: 195's final
                 row-count assertion renamed 8b -> 8e, because "section 8(b)" - the
                 NO-DEFAULT post-condition the door fix is argued from - was a DIFFERENT
                 thing with the same name. `git diff ac4e345..8ebe197` is five comment
                 lines in one file.)
```

Both were pushed to `origin/feat/agent-memory-exposure-column` BEFORE the gitlink was bumped,
and each descends from the pin the line carried. Every number below is from a run against
**8ebe197**, after the last edit to any file it builds from.

Every run below is against the tree that gitlink pins, one checkout, each throwaway on its own
docker network, nothing attached to an `ai-stack_*` network and no `:local` tag written.

| check | result |
|---|---|
| `deno check index.ts agent-memory.ts` + `deno test` (kubernetes-deployment) | clean / **133 passed, 0 failed** |
| full 29-migration initdb chain on a throwaway | no init errors; 195's four notices printed (backfill, table self-test, **door self-test - 12 payloads**, mirror-reader scan - whose notice said "zero readers" and now states its own scope, §17.4) |
| 195 round trip on that throwaway | revert(200) -> revert(195) -> re-apply(195) -> re-apply(200) -> re-apply both AGAIN: clean, idempotent, row count preserved |
| `scripts/checks/prove-agent-memory-rls.ps1` | **PASSED - 68 checks**, every green with a red beside it |
| `scripts/checks/drill-personal-plane-exclusion.ps1` (bare) | **109 passed, 0 failed, 18 named gaps**, EXIT 2 - the bare contract is unchanged |
| the same, `-AcceptDispositionedGaps` (what CI runs) | **109 passed, 0 failed, 18 gaps ALL DISPOSITIONED**, EXIT 0 |
| (both drill rows are the ROUND-2 numbers; round 3 changed them - see §17 and §18) | |
| production `openbrain-db`, after everything | `thoughts=13001 personal=0 memories=21 personal=0` - **row state** unchanged. **"and never touched" was FALSE; see below.** |

### The R1/R2 fixes, red then green, in one place

| | RED (before) | GREEN (after) |
|---|---|---|
| `upsert_thought('{}')` | wrote a row, `exposure=ops` | `not_null_violation` at the door |
| `{"exposure":""}` / `{"exposure":null}` | wrote a row, `exposure=ops` | `check_violation` / `not_null_violation` |
| `{"exposure":"personal"}` | accepted (superuser) / 42501 (bound) | refused at the door, with the reason |
| personal row re-upserted as `ops` | `col=personal mirror=ops` | `col=personal mirror=personal` |
| ops row "demoted" through the door | `col=ops mirror=personal` | refused; row untouched, `col=ops mirror=ops` |
| ops->personal by column UPDATE | queue row SURVIVES with `sha256(content)` | queue row DELETED |

### The R3/R4 fixes, proven by breaking what they guard

The three unfalsifiable `Pass`es and ATTACK 8's schema-rejected red are gone, and the
replacements were watched failing before they were watched passing:

* **run 1** (cast omitted from the red patch): `FAIL RED-A (ATTACK 1) did NOT reproduce`,
  `FAIL RED-A (ATTACK 3) did NOT reproduce ... could not determine data type of parameter $2`.
  The drill exited **1**. Two checks that previously could not fail, failing.
* **run 2** (cast added, live ops controls added to both RED-Bs): all four RED-A/RED-B
  branches pass on the real reason, `RED CONFIRMED (ATTACK 8) - as postgres, promote_exposure
  MOVED the personal memory onto the ops plane`.
* **runs 3-5** (after section 15's assertion fixes, at gitlink 8ebe197): the same four
  branches, plus `and its jsonb mirror agrees with the column (personal)` and
  `fixture restored to the personal plane for the phases below (column and mirror)`.

The gap ledger was broken deliberately, on a throwaway copy of the script, to prove both of its
detectors fire:

* ledger key `AUDIT-INSPECT` renamed to `AUDIT-INSPECT-TYPO` and a never-emitted
  `LEDGER-SELFTEST-NEVER-FIRES` added, run WITH `-AcceptDispositionedGaps`:
  `AUDIT-INSPECT  *** UNDISPOSITIONED - nobody has named this one ***`, then
  `FAIL 2 dispositioned gap(s) did NOT fire: LEDGER-SELFTEST-NEVER-FIRES, AUDIT-INSPECT-TYPO`,
  **exit 1**.
* the rename alone, `-AcceptDispositionedGaps -SkipRed`. `-SkipRed` exempts the stale check,
  so the renamed key does not also trip the exit-1 branch and MASK this one; all 18 gaps of the
  round-2 set still fired, `AUDIT-INSPECT` among them, and the undispositioned detector alone
  decided the exit -
  **exit 2**, `1 UNDISPOSITIONED GAP(S) - AUDIT-INSPECT`.

So CI cannot go green on a NEW gap, and cannot stay green on a ledger that has rotted. The
throwaway copy was deleted.

## 15. Seven drill assertions still read the mirror, and one RESTORE only wrote it

Found while checking the review brief's own claim that in the salvaged drill "every assertion
reads the COLUMN". Seven did not:

```
935/938  the fixture and control verification        SELECT metadata->>'exposure' ...
1573     ATTACK 8's GREEN                             SELECT metadata->>'exposure' ...
1600     ATTACK 8's RESTORE                           SET metadata = ... || {'exposure':'personal'}
2143     the LIFT's "WRITTEN" clause                  AND metadata->>'exposure' = 'personal'
2182     the LIFT's "REMOVED" (memories)              COALESCE(metadata->>'exposure','personal')
2183     the LIFT's "REMOVED" (thoughts)              WHERE metadata->>'exposure' = 'personal'
```

Every one of them is green today for a reason that is not the reason it is testing. The
policies read the COLUMN; these read a value nothing enforces. They agree only because every
fixture in this file writes both halves.

**The restore is the one that could have bitten.** ATTACK 8's green ends by restoring the
fixture "unconditionally: if the attack DID succeed, the phases below (and the red phase in
particular) need the fixture back on the personal plane or they test nothing" - and it set only
the MIRROR. So in the world where the attack succeeded, the restore would have left
`column='ops'` with `mirror='personal'`, and every phase after it - the entire red phase, and
the LIFT's "0 personal rows again" - would have been attacking an OPS-plane row while its own
verification, reading the mirror, agreed it was personal. The drill would have reported
containment over a row the boundary was correctly letting through. The red phase's own restore
already wrote both; the green's did not.

Fixed: every one now reads (and the restore writes) the COLUMN. The two fixture checks read
BOTH and assert they agree - the mirror is asserted there, not trusted, because a desynced
fixture would make every later assertion in the file ambiguous. The LIFT's `COALESCE(...,
'personal')` is dropped: it was the conservative pre-H3 reading that counted an UNLABELLED row
as personal, and under NOT NULL + CHECK there is no unlabelled row to be conservative about.

This is the same defect as R3, one layer further in: not a check that cannot fail, but a check
that can only fail for the wrong reason.

## 16. What the reviewer should check first (ROUND 2's map - two items below are now FALSE)

Not a finding - a map, because round 2 changed four files and the interesting parts are small.
**Kept as written, with the two corrections marked inline: item 3's reasoning was wrong and
round 3 measured it wrong in production. Round 3's own map is section 17.**

* `OB1/docker/init-agent-memory-exposure-column.sql` **section 7** (the door: what it refuses
  and why 'personal' is one of them), **7b** (the last mirror reader and the trigger column
  list), **8c** (the door attacks itself: 12 payloads + both desyncs), **8d** (zero mirror
  readers, asserted over pg_policies and pg_proc.prosrc). The cutover - sections 0 to 6 - is
  UNCHANGED and was not re-litigated.
* `OB1/docker/revert-agent-memory-exposure-column.sql` **1a** and **3b**.
* `scripts/checks/drill-personal-plane-exclusion.ps1`: `Set-RedAnchor`, the RED-A/RED-B block,
  ATTACK 8's red, `$GAP_DISPOSITIONS`, and the exit block.
* the seven assertions moved off the mirror (section 15) - in particular ATTACK 8's restore.

The claims most worth trying to break, in the order I would try:

1. *"the door cannot leave the column and the mirror disagreeing"* - the UPDATE branch writes
   the mirror from `v_row.exposure`, but a caller that reaches `thoughts` any other way still
   can, and section 8(d) is what makes that not matter. Break 8(d) and the claim narrows.
2. *"nothing reads the mirror"* - the scan is anchored on `metadata->>'exposure'`,
   `metadata->'exposure'` and three `on_ops_plane(...)` call forms, whitespace-normalised. A
   body that used `metadata @> '{"exposure":"ops"}'` would pass it. Nothing in the tree does
   today; that is a bound on the assertion, and it is stated here rather than in the file's
   NOTICE.
3. *"every producer states its plane"* - found by grepping the literal `upsert_thought` over
   `.ts`/`.mjs`/`.js`/`.py` in OB1 and `.ts`/`.mjs`/`.js`/`.py`/`.ps1`/`.sql` in ai-stack
   (which has ZERO callers): ten `rpc("upsert_thought", ...)` sites, plus the direct-insert
   paths that bypass the door (generate-wiki's dossier fallback and backfill-gmail-wikis'
   plain insert, both of which now carry the COLUMN, and the four
   `INSERT INTO thoughts (..., exposure)` statements in the MCP server that already did).
   A producer that builds the rpc name dynamically, or writes through PostgREST from outside
   these two repos, is NOT covered by that grep - and would be refused at the door, loudly,
   rather than silently mislabelled. That is the failure direction H3 chose, and it is the
   reason the grep not being exhaustive is survivable.

   > **CORRECTED, ROUND 3.** Both halves of that paragraph are false.
   > **(a)** The grep was over `upsert_thought`, so it could only ever return callers of the
   > door. **Twelve producers write `thoughts` without going through it** - `POST
   > /rest/v1/thoughts` and `supabase.from("thoughts").insert()` - and this paragraph names
   > only the three of them that happened to be adjacent to an rpc call site.
   > **(b) "Refused at the door, loudly" is not what happens.** `openbrain-gmail-pull` catches
   > its own error and reports `Ingested: 0 email(s)`; `wiki-service.mjs`'s note ingest catches
   > per file and continues. Both are scheduled. The refusal ran daily for a day before anyone
   > looked, and would have kept running. **Fail-closed is not the same as fail-visibly**, and
   > the survivability argument rested on confusing them. See section 17.3 and
   > `documentation/notes/u5-live-producer-rls-regression.md`.
4. *the gap ledger* - 18 ids, each mapped to H1 or H4. If any one of them is really H3's, the
   ledger is hiding work rather than dispositioning it.

   > **UPDATED, ROUND 3: 25 ids.** Seven were added, none of them a new shortfall - six name
   > assertions that were reporting PASS off an empty set and now report VACUOUS, and one
   > (`RED-COVERAGE`) names the seven ATTACK sections that have greens and no red. Section 17.1.
   >
   > **UPDATED, ROUND 4: still 25, and every one now carries its COST.** Making a vacuity
   > visible was the right first move and was not enough: under `-AcceptDispositionedGaps`,
   > six assertions that measure nothing sat *inside* an exit-0 green, so CI asserted less
   > than it had before while looking identical. Each entry now ends with **GREEN DOES NOT
   > COVER:** - the specific thing a passing run fails to rule out. And closing a gap no
   > longer fails the build: a dispositioned gap that stops firing is CLOSED (its assertion
   > ran, zero failures) or VANISHED (nothing measured it, still a FAIL). Section 19.3-19.4.

## 17. ROUND 3 - a PASS printed off an empty set, and a producer set that was never swept for

Two send-backs, both of the class this whole effort keeps landing in: **a check that passes
while checking nothing**, and **a sweep whose search term defined its finding.**

### 17.1 Eleven assertions could not fail, and one mechanism now stops the twelfth

Five green-phase assertions had the shape *"of the rows in S, none has property P"* written as
a count of the VIOLATIONS only:

```powershell
$n = Db "SELECT count(*) FROM ... WHERE <violating>"
if ($n -eq "0") { Pass "..." } else { Fail "..." }
```

With `S` empty the violating count is also `0`, so the branch prints **PASS**. **Four of the
five printed on the line immediately after the GAP that had just measured `S` as empty** - the
drill reported "0 refusal row(s) exist to outlive anything" and then congratulated itself, five
times, that none of those zero rows was wrong.

**Fixed structurally, not five times.** `Assert-NoneOf` takes BOTH counts - the universe and
the violating subset - and can only reach `Pass` when the universe is non-empty. An empty
universe is reported `VACUOUS`, names *which* universe was empty, counts as a gap, and must be
dispositioned by id like every other open property. It has a fourth outcome too: a count that
does not PARSE is a `Fail`, not a silent zero, because a broken query returning `""` would
otherwise read as "no violations".

**Eleven assertions are routed through it** - the five named in the review, plus six siblings
found by reading for the shape rather than for the line numbers:

| where | universe | outcome on this tree |
|---|---|---|
| the ops door's refused mint | memories in the drill workspace | **PASS** (1) |
| the `share` label on the mirror | mirrored thoughts carrying the marker | **PASS** (2) |
| the ALLOWED inspect wrote no refusal row | `access_refused` rows so far | **VACUOUS** (0) |
| the ghost id writes no refusal row | `access_refused` rows so far | **VACUOUS** (0) |
| the trace refusal names no memory id | `off-plane-trace` refusal rows | **VACUOUS** (0) |
| the refused review filed no paperwork | review-action rows | **PASS** (1) |
| the writeback refusal names no memory | writeback `access_refused` rows | **VACUOUS** (0) |
| no usage row for the off-plane memory | `memory_used`/`memory_ignored` rows | **PASS** (1) |
| `wiki_pages` holds none of the personal text | `wiki_pages` rows this compile wrote | **VACUOUS** (0) |
| the ENUMERATING doors filed nothing | tools that filed a refusal row | **VACUOUS** (0) |
| no audit row carries the content it refused | audit rows | **PASS** (5) |

The mix matters: **five of the eleven still PASS**, so the helper is not a blanket "everything
is vacuous" - it discriminates, which is exactly what the assertions it replaced could not do.

`VACUOUS-WIKIPAGES` is a **new finding**, not a restatement: the compiler wrote **zero**
`wiki_pages` rows in the throwaway, so ATTACK 14's table-side assertion has been about a
compile that published nothing to that table. ATTACK 14's file-side assertions (no leaf page,
no marker in the emitted text) are measured against output that does exist and still pass.

**Proving it, both directions.** `-SelfTestVacuity` forces the helper through all four
outcomes with no docker, no database and no gitlink:

```
PASS     universe=7 violating=0 -> pass
FAIL     universe=7 violating=2 -> fail
VACUOUS  universe=0 violating=0 -> vacuous   <- the case that used to print PASS
FAIL     universe=x violating=0 -> unparsable
VACUITY GUARD SELF-TEST PASSED - an empty universe cannot reach PASS.
```

And on the real run: the six lines that printed `PASS` in round 2 print `VACUOUS` in round 3,
against the same tree and the same fixtures. Nothing about the boundary changed; the reporting
did.

### 17.2 The patch script that "worked" and dropped three of its own edits

**Drill run 1 is why there is a second commit in this round.** The script that routed the first
four assertions asserted on its fourth replacement and never wrote the file - so replacements
1-3 were **lost**, while the script's earlier output read as success. The run then printed the
OLD, uncounted text for all three, *including `:1187`, the assertion the review named first*.

What caught it was the **count in the PASS line**: `Assert-NoneOf` appends
`(0 violations out of N ...)`, and three PASSes came back without it. Had the helper printed the
same sentence as before, the gap would have been invisible in a 230-line log. **A fix that
changes the output is auditable; a fix that preserves it is not.**

### 17.3 The producer set, derived

`195-` section 7's post-condition said *"Every caller of this rpc in the tree was found and
given an explicit `exposure: 'ops'`"*. The sweep behind it was
`grep -rn 'rpc("upsert_thought"' OB1` - RPC callers were the only thing it could return, and the
post-condition then presented them as the producer set.

`openbrain-gmail-pull` runs daily and had been refused `42501` by U5's already-live ops-plane
policy since it landed. Measured from the container's own log:

```
2026-08-30T05:00:28Z   Ingested: 1 email(s) / 11 chunk row(s)
2026-08-31T05:00:19Z   -> PARTIAL/ERROR (0/6 chunks): HTTP 401 {"code":"42501", ...}
2026-08-31T05:00:20Z   Ingested: 0 email(s) / 0 chunk row(s)
```

Twelve direct producers, `'ops'` at each call site (column **and** mirror), and a gate that
derives its sites from the tree *within the shapes it recognises* -
`scripts/checks/check-corpus-exposure-producers.ps1`, pre-commit check 3b. Full account,
including the two producers the regression note's own ten-file table missed and the four it
listed that are not producers at all:
`documentation/notes/u5-live-producer-rls-regression.md`.

> **CORRECTED, ROUND 6 (2026-08-31).** This paragraph ended *"so producer thirteen breaks the
> build rather than production"*. **That is false** — the same sentence, in the same shape, that
> §21 and the gate's own header were written to retract, still standing here in the round-3
> record. The enforcement is `195-`: `exposure` NOT NULL + CHECKed and `upsert_thought`
> refusing a payload that omits it. Producer thirteen, in a shape the gate does not recognise,
> breaks **production** — quietly, per §16. The gate is authoring-time convenience. The clause
> is deleted rather than re-typed; the derivation claim it was attached to is true only within
> the recognised shapes, and now says so.

**The gate's first version was itself vacuous.** It inherited `'*\.claude\*'` from
`check-llm-gateway-routing.ps1`'s allow-list; a session worktree lives under
`.claude\worktrees\<id>\`, so every file matched, every file was allowed, and it printed
`OK - every direct corpus insert states its plane` over a scan of **nothing**. Found by planting
a violating producer in the real tree and watching it pass. The glob is gone, and the gate now
**FAILS** when it examined zero insert sites - the same discipline as 17.1, applied to the fix
for 17.1.

### 17.4 Corrections to the record

| claim | where | corrected to |
|---|---|---|
| "The write contract reaches the RECIPES" | `PROMOTION-RUNBOOK.md` | it reached the RPC callers. Twelve direct producers are outside that set; **"no rebuild, no restart" is FALSE for `wiki-service.mjs`**, which is `COPY`d into `openbrain-wiki:local` |
| "production `openbrain-db` unchanged, and never touched" | this note, §14 | **FALSE.** Verified read-only 2026-08-31: `thoughts.exposure` and `agent_memories.exposure` BOTH EXIST live as **nullable** columns. Reversible - `revert-agent-memory-exposure-column.sql` deliberately does not drop a column ("dropping a column is not reversible, and PLAN class 4 forbids it") - but not untouched. The note also did not observe the live 42501 |
| "the mirror has zero readers" | `195-` §8(d) NOTICE, `PROMOTION-RUNBOOK.md` | the scan's anchors are the literal `metadata->>'exposure'` / `on_ops_plane(metadata)`, which cannot match the two RETAINED jsonb overloads whose bodies **are** `md->>'exposure'`. Nothing CALLS them - asserted by `200-` §9 over `pg_depend` and every function body - so the substance holds; the notice now states its own scope instead of borrowing §9's conclusion |
| "`prove-agent-memory-rls.ps1` 68/68" | `PROMOTION-RUNBOOK.md` | true **only when it does not overlap `dfu-done.ps1 -Only 3`**, which plants a personal-exposure fixture in the LIVE database (`$DbContainer = "openbrain-db"`). Overlapped, `prove-rls` exits **1** with `production is not clean`. **C.9 H4 wires BOTH into CI, so CI must serialise them** |
| "(109 checks passed)" vs 110 PASS lines | the drill's summary | the gitlink PASS was a raw `Write-Host` and bypassed `$passes`. It is a `Pass` now, and run 3's summary matches its own output exactly: **106 printed, 106 counted** |
| "a red for every family of green above ... a green whose red is missing is visible as an absence" | the drill, red phase | **false twice.** ATTACKS 2, 4, 5, 5b, 6, 9, 10 have greens and no red, and nothing said so. Each red now REGISTERS the attack it backs at the point it RUNS; the attack universe is DERIVED from this file's own `Section "ATTACK ..."` headings; the difference is reported as GAP `RED-COVERAGE` - `8 of 15 ATTACK section(s) have a red that RAN` |

## 18. ROUND 3 - the sha this was validated at, and every run

The work line did not move: `refactor/ai-stack-cleanup` is still `1a6b0b8` and it is this
branch's merge-base, so C.7b required no rebase. The OB1 gitlink moved once.

```
work line base : 1a6b0b813e241cfb4b74659cbb2c11c8f86616aa  (refactor/ai-stack-cleanup, unmoved)
VALIDATED AT   : 0c0c3d551f3956d3934b3e68c8528a43abf2047c  (work/u8h3)
OB1 gitlink    : 8ebe19780ce93613689fd8241d787c1a77749454
              -> e9be2cdb0eb340662df0edadb1ff4b90b0493775  (the twelve producers + 195 s7/s8d)
```

Every number below is from a run against **`0c0c3d5`**. The commits that follow it on this
branch touch `documentation/notes/u8h3-findings.md` and nothing else - no script, no migration,
no compose file - so no run above them is stale. `git diff 0c0c3d5..HEAD --name-only` is the
check for that claim, and it is one line long.

Both suites were run from this checkout with a **clean working tree at `0c0c3d5`**, one at a
time - never concurrently, which is the isolation "one suite per checkout" exists to buy (see
§17.4 on `prove-rls` vs `dfu-done -Only 3`). Every throwaway ran on its own docker network;
nothing was attached to an `ai-stack_*` network, no `:local` tag was written, and **nothing was
written to the live database**. An `open-brain` lease was held for the window.

| check | result |
|---|---|
| `drill-personal-plane-exclusion.ps1 -SelfTestVacuity` | **4/4 outcomes correct**, EXIT 0 - and the empty-universe case reports VACUOUS, not PASS |
| `drill-personal-plane-exclusion.ps1` (bare) | **106 passed, 0 failed, 25 named gaps** (19 GAP + 6 VACUOUS), EXIT **2** - bare contract unchanged |
| the same, `-AcceptDispositionedGaps` (what CI runs) | **106 passed, 0 failed, 25 gaps ALL DISPOSITIONED**, EXIT **0** |
| PASS lines printed vs counted, run 3 | **106 / 106** - the gitlink PASS no longer bypasses the counter |
| `prove-agent-memory-rls.ps1` | **PASSED - 68 checks**, 0 failed, EXIT 0; live assertion reads `source=column personal_memories=0 personal_thoughts=0 personal_memories_col=0 personal_thoughts_col=0` |
| `check-corpus-exposure-producers.ps1` | **OK - all 13 direct corpus insert site(s) state their plane** (659 files scanned), EXIT 0 |
| the same, `-SelfTest` | **red on a planted producer, green when the plane is stated**, EXIT 0 |
| the same, `-Root <empty dir>` | **FAIL - the scan examined 0 file(s) and found ZERO corpus insert sites**, EXIT 1 (the anti-vacuity assertion) |
| pre-commit, twice | all six checks green, including 3b |
| `node --test` (wiki-service build gate) | **13 passed, 0 failed** |
| `deno check` on the changed TS | `pull-gmail.ts` clean; `schema-aware-routing/index.ts` reports a **pre-existing** missing `@supabase/supabase-js` dependency (confirmed identical at `git stash`) |

**The count moved from 109 to 106 and that is the fix working, not a regression.** Seven
assertions that printed PASS in round 2 now report VACUOUS; one (the ghost id) was split into a
response half that still passes and a row half that does not; and the gitlink PASS joined the
count. Nothing that was proved before is unproved now - what changed is that six things that
were never proved have stopped claiming to be.

### Not closed

* ~~**The OB1 commit `e9be2cd` is NOT on `origin`.**~~ **CLOSED 2026-08-31.** The operator
  pushed `e9be2cd`; round 4's `debbbaa` was pushed from this session. Both are reachable on
  `origin/feat/agent-memory-exposure-column`, and the gitlink names `debbbaa`.
* **`RED-COVERAGE` is visible, not closed.** Seven ATTACK sections still have greens and no
  red. Writing them is its own item.
* **`VACUOUS-WIKIPAGES` needs a fixture**, not a boundary change: the drill's compile writes no
  `wiki_pages` rows, so that assertion cannot discriminate until it does.

  > **CORRECTED, ROUND 4. "Needs a fixture" was a guess wearing the clothes of a diagnosis** -
  > it named a remedy without having measured the cause, and it was half wrong. The fixture
  > half is real and is done (`WIKI_GIT_DIR=/out`). The other half is a **live OB1 bug**:
  > `wiki-pages.mjs` re-exports `extractLinks` without importing it, so `parseWikiPage` throws
  > and `queueWikiPage`'s bare `catch {}` swallows it - **no compiler has written a
  > `wiki_pages` row since 2026-08-28**. Section 19.5 and
  > `documentation/notes/wiki-pages-extractlinks-scope-bug.md`.
* **The live outage is fixed in the tree, not in production.** Ending it needs the deployment
  checkout's submodule moved AND `openbrain-wiki:local` rebuilt, in a promotion window under a
  lease - a gated deploy, which this session did not take.

## 19. ROUND 4 - the gate was a hand-list in a costume, and it was green on the one site that mattered

Four send-backs. Two of them are the same defect at two altitudes: **a check that reports
coverage it does not have.** One is about what a green is allowed to contain. One is a survivor
of round 3's own sweep.

### 19.1 The producer gate is not the enforcement, and it must stop saying it is

The gate's header claimed:

> An eleventh producer written next year is in the universe the moment it is written, and it
> breaks this gate instead of breaking production.

**That sentence is FALSE.** Two verifiers independently planted producers in a temp root; the
gate did not flag them, did not warn about them, and **did not even count them as insert sites**:

| planted shape | why it was invisible |
|---|---|
| `const TABLE = "thoughts"` … `` fetch(`${REST}/${TABLE}`, {method:"POST"}) `` | the gate resolves no values |
| `fetch(REST_BASE + "/" + "thoughts", …)` | ditto, on a concatenated path |
| `insertRows("thoughts", rows)` / `obPost("thoughts", …)` | `$postVerb` knew `.post(` and `http_post(`, not an arbitrary helper |
| byte-identical copies named `.mts` and `.tsx` (OB1 ships **57** `.tsx`, counted) | `$exts` was ts/mjs/js/cjs/py |
| `curl -X POST "$SUPABASE_URL/rest/v1/thoughts"` in a `.sh` | no `.sh` in `$exts`, and `-X POST` was not a verb |
| supabase-py `.table("thoughts").insert(rows)` | `$siteOrm` knew `.from(` only |

This is **the same alphabet error as A2's `.ts`-only scan root** — the error §17.3 of this note
cites as the reason the gate exists. The gate written to cure a bad sweep was itself a bad sweep.

**The answer was NOT six more patterns.** That is the enumerate-the-readers method A2 abandoned,
and the seventh evasion still wins. Four changes, at three different altitudes:

**(a) Name the real enforcement.** `195-` makes `thoughts.exposure` and `agent_memories.exposure`
`NOT NULL` with no default and `CHECK (exposure IN ('ops','personal'))`, and makes
`upsert_thought` refuse a payload that omits them. **That** refuses an unlabelled write
unconditionally — in every shape, from every language, through every client, forever, including
shapes nobody has thought of. The gate adds nothing to it. It is authoring-time convenience: it
moves *some* of those refusals from 05:00-in-a-cron to `git commit`, which is worth having and is
not a guarantee.

**(b) Correct the claim wherever it appears.** A producer this gate cannot see breaks
**PRODUCTION** — and, per §16, it breaks it **quietly**, because both producers that were
actually failing catch the 42501 and carry on (`Ingested: 0 email(s)`, exit 0). *Fail-closed is
not fail-visibly.* Corrected in four places: the gate header, `195-` §7,
`u5-live-producer-rls-regression.md` (whose section heading *was* the false claim), and
`PROMOTION-RUNBOOK.md`.

**(c) Make the gate declare its own blind spots, in its own output, every run** — pass or fail,
and alone under `-ShowShapes`. A reader now learns its scope from the run instead of from its
author's confidence.

**And the disclosure is MEASURED, not asserted.** The first draft of the blind-spot list said
these shapes are "INVISIBLE here", and that was itself an overclaim — I planted them and two of
the two supposedly-invisible ones were flagged. Not by design: by accident of layout, because an
unrelated literal happened to fall inside the 2-line ARG window. So the list now says what is
true, and the run says how it was established:

```
const TABLE = "thoughts";                       <- literal ADJACENT to the fetch
  ... fetch(`${REST}/${TABLE}`, {method:"POST"})
  => FAIL - 1 of 1 ... var-table.mjs:2

const TABLE = "thoughts";                       <- same producer, 40 filler lines inserted
  ... 40 lines ...
  ... fetch(`${REST}/${TABLE}`, {method:"POST"})
  => OK - all 1 RECOGNISED corpus insert site(s) state their plane
```

**Same defect, same file, opposite verdict, decided by whitespace.** That is what "it resolves no
values" means in practice, and it is now the sentence the gate prints.

**(d) Widen the alphabet anyway** — not as the fix, but because a cheap catch is worth having
once the claim is honest. `.mts .cts .tsx .jsx .sh .bash`; supabase-py `.table()`; `curl -X POST`
/ `--request POST`; and any identifier *containing* `post` or `insert` called as a function.
Measured against the planted set: **the four evasions that still write the table name as a
literal beside a verb are now caught; the two that hold it in a value are not, and say so.**

The alphabet widening cost 659 → 742 files scanned and, with the new fence pre-pass, took the
pre-commit hook to **75 seconds**. A 75-second pre-commit check is a deleted pre-commit check, so
there is an early-out: a file naming neither table ordinally cannot match any pattern (they are
all lowercase literals), and is skipped. **16.5s.**

### 19.2 The gate was green for the wrong reason on the one site that mattered

`OB1/integrations/agent-memory-api/index.ts:491` is the **only `agent_memories` INSERT in the
tree**. It carried neither `exposure` nor `metadata.exposure` — and it **passed**, cleared by the
`exposure: "ops"` key at `:471`, which belongs to the `upsert_thought` RPC payload **for a
different table**, twenty lines up and inside the ±30-line evidence window. A verifier proved it
by renaming that unrelated key and watching the gate go red on a line it had never examined.

**The gate's entire `agent_memories` coverage was a false positive** — and a green off a
neighbour's key is worth *less* than no check at all, because it reads as coverage.

Two changes:

* **an ORM site is read over its own STATEMENT** — from the builder to the terminating `;`,
  however far. `:491`'s insert body is 33 lines, so even without the neighbour the 30-line window
  would have cut off the very key it was looking for.
* **no site's evidence may cross a corpus site naming a DIFFERENT table.** The fence is
  *table-aware*, and the first version was not — fencing at every corpus site turned
  `pull-gmail.ts:856` red, which is the **retry leg** re-POSTing the same, correctly labelled
  `row` built above the first POST. Two POSTs of one row into one table are one producer; a
  `thoughts` key clearing an `agent_memories` insert is the defect. That false red was worth
  having: it is what forced the rule to be stated precisely instead of broadly.

Red then green, on the real tree:

```
FAIL - 2 of 13 ...   (blind fence)      index.ts:491  AND  pull-gmail.ts:856
FAIL - 1 of 13 ...   (table-aware)      index.ts:491                    <- the real defect, alone
OK   - all 13 RECOGNISED ... state their plane (742 files)              <- after the site is fixed
```

**The site itself is fixed** (OB1 `debbbaa`): `exposure: "ops"` as the column and in the
`metadata` mirror. It **is** dead code — no compose service and no Dockerfile references
`integrations/agent-memory-api` anywhere in the tree (verified by grep, not by trusting `195-`
§7's caller table) — but a dead-code exemption **has to be written down to be one**, and this one
was an accident of line distance. The reason it produced no live 42501 like
`openbrain-gmail-pull`'s is only that nothing runs it; the column is `NOT NULL` with no default,
so the **first** deploy of that file would have been refused on its first write.

`-SelfTest` now has a third case that is exactly this shape — an insert cleared by a neighbouring
statement's key. **Cases 1 and 2 passed for the entire time the `:491` false green existed**,
which is the argument for case 3 existing.

### 19.3 CI green contained six assertions that measure nothing — each now carries its price

Round 3 made the vacuity **visible**, which was right. But under `-AcceptDispositionedGaps` — the
form C.9 H4 wires into CI — a dispositioned vacuity sits **inside an exit-0 green**. Six
assertions that measure nothing were formally part of "CI passed", so **CI asserted less than it
had before while looking identical**. A disposition that explains only the *cause* lets that
happen quietly.

Every `VACUOUS-*` and `RED-COVERAGE` entry now ends with **GREEN DOES NOT COVER:** — the specific
thing a passing run fails to rule out. Two examples, since the point is that they are specific
rather than boilerplate:

* `VACUOUS-REFUSAL-DISCRIMINATES` — *"…that a refusal is distinguishable from an allow in any
  durable record. **A door that filed a refusal row for EVERY call, allowed ones included, would
  pass this run unchanged.**"*
* `VACUOUS-GHOST-NO-ROW` — *"…that the audit log cannot be used to CONFIRM a guessed memory id.
  **A door that filed a row naming the ghost id — which is an existence oracle — would pass this
  run unchanged**; only the response half would catch it."*

A reader can now price the green from the ledger without reading the drill.

### 19.4 And closing a gap must not be punished — with the round's best evidence that it works

A verifier noted that closing a vacuity trips the stale-gap FAIL (exit 1) until the pin is
pulled. That rule is right for the case it was written for — a check that quietly stopped
*running* leaves the same silence as a check whose property got *fixed*, and the flattering
reading is the wrong one — but it makes the **good** outcome expensive, and **a gate that turns
red when you fix something teaches people to stop fixing things.**

The fix is not to weaken the rule but to **tell the two silences apart**. A dispositioned gap
that stops firing is now:

* **CLOSED** — an assertion carrying that id RAN and reached a verdict. Good news. Printed
  loudly, with `PULL THESE PINS`, and worth **zero** failures.
* **VANISHED** — nothing with that id was measured at all. Still a **FAIL**, and now the *only*
  case that rule fires on.

`Split-StaleGaps` is a pure function and `-SelfTestLedger` forces it through five
classifications, no docker and no database, including the one that matters:

```
OK   stale=[CLOSED-ONE]           -> closed=1 vanished=0 fails=0   (fixing something does not turn the build red)
OK   stale=[GONE-ONE]             -> closed=0 vanished=1 fails=1   (the original rule, intact)
OK   stale=[CLOSED-ONE,GONE-ONE]  -> closed=1 vanished=1 fails=1   (one good outcome does not launder the bad one)
OK   stale=[]                     -> closed=0 vanished=0 fails=0
OK   stale=[CLOSED-ONE] map={}    -> closed=0 vanished=1 fails=1   (the escape hatch cannot be the default)
LEDGER SELF-TEST PASSED
```

**The cost is stated rather than hidden:** a CLOSED pin is a nag, not a gate, so a pin for a
genuinely closed property can sit in the ledger indefinitely. That is the conservative error —
the ledger then *over*-reports an open gap — and every run prints it.

### 19.5 I tried to close `VACUOUS-WIKIPAGES`, pulled its pin too early, and the ledger caught me

Item 3 offered a choice: close a vacuity, or state its cost. `VACUOUS-WIKIPAGES` looked
closeable, and the diagnosis was cheap and correct as far as it went:
`recipes/_shared/wiki-pages.mjs`'s `vaultRel()` drops any path outside `WIKI_GIT_DIR`
(default `/wiki`) — deliberately, so a scratch `--out-dir` cannot write junk slugs into the table
— and this drill compiles into `/out`. So **every page was outside the vault and nothing was ever
queued.** The assertion had never been measuring the boundary; it was measuring that guard.
`Invoke-WikiCompile` now sets `WIKI_GIT_DIR=/out`. I pulled the pin.

**The next run reported `VACUOUS-WIKIPAGES` as UNDISPOSITIONED and exited 2.** The fixture was
only half the cause, and the other half is a **live production bug in OB1**, found by this
failure:

```
$ WIKI_GIT_DIR=/out node -e '...'
vaultRel(/out/content/concept/x.md) = content/concept/x.md   <- path handling FINE
queued = 0                                                   <- and nothing queues
$ node --test OB1/recipes/_shared/wiki-pages.test.mjs
# pass 5  # fail 5      all five: 'extractLinks is not defined'
```

`wiki-pages.mjs` **re-exports** `extractLinks` without **importing** it, so the name is unbound
in the module's own scope, `parseWikiPage` throws `ReferenceError`, and `queueWikiPage`'s bare
`catch {}` swallows it. **Since OB1 `dfc6228` (2026-08-28), no compiler has written a `wiki_pages`
row, and the backfill that exists to rebuild the table calls the same broken function.** The
viewer's search / nav / graph read that table. Full write-up, blast radius and one-line fix:
`documentation/notes/wiki-pages-extractlinks-scope-bug.md`.

**Not fixed here, and deliberately.** It is a live wiki-index defect belonging to the wiki work
line; folding an OB1 wiki fix into an agent-memory-plane branch puts a change nobody asked for
into a merge nobody would review for it. What is done instead:

* the drill's own half (`WIKI_GIT_DIR=/out`) is **kept** — correct, necessary, and it means the
  gap closes *by itself* and reports CLOSED once the OB1 bug is fixed;
* the pin is **back**, with the real cause and its cost: *a green run does not cover the
  `wiki_pages` surface at all — a compile that published the personal row into that table would
  pass unchanged. Only ATTACK 14's file-side half is actually measured, and that half passes
  against real output;*
* **the vacuity now diagnoses itself.** When the universe is empty the drill prints both
  preconditions in order, and the one command that decides which is biting
  (`node --test OB1/recipes/_shared/wiki-pages.test.mjs`). Round 3 left this as "needs a
  fixture"; round 4 spent a whole drill run learning that the fixture was half the story. Nobody
  should have to spend a third.

Two things worth keeping from this: **the ledger's undispositioned-gap rule did exactly its
job** — I made an over-claim and the machine, not a reviewer, refused it. And *"needs a fixture"*
was a guess wearing the clothes of a diagnosis. It named a remedy without having measured the
cause, and it was half wrong.

### 19.6 One unrouted vacuity survivor, and the adjudication of the rest — verified, not assumed

`drill:2070` — the **read**-tool COVERAGE gate — was `if ($missed.Count -eq 0) { Pass "all
$($opsReadTools.Count) derived read tool(s) were attacked" }`, which prints **"all 0 … were
attacked"** on an empty list.

The cause is upstream and is an asymmetry between twins written at different times:

```powershell
if ($opsEnv.ContainsKey("GATEWAY_READ_TOOLS") -and …)   # TRUE for `GATEWAY_READ_TOOLS=` (empty!)
…
if ($opsWriteTools.Count -gt 0)                          # its twin, 12 lines below, guards the LIST
```

Both guard the parsed list now, and **both** COVERAGE gates route through `Assert-NoneOf`. Belt
and braces: the derivation refuses an empty set, so the vacuous branch is unreachable; if it is
ever reached, the id is undispositioned and the run exits 2 — which §19.5 just demonstrated is
not a theoretical consequence.

**The other candidates were re-checked one at a time rather than taken on trust**, since an
agent's report is not evidence until the part you act on has been verified. An independent
enumeration of every `.Count -eq 0`-gated `Pass` in the file returns exactly five sites, and:

| site | verdict | why |
|---|---|---|
| `:2071` read coverage | **the survivor** | unguarded, fixed above |
| `:2084` write coverage | legitimate | guarded upstream by the `Count -gt 0` throw at `:1026` |
| `:2387` / `:2389` RED COVERAGE | legitimate | `if ($attackIds.Count -eq 0) { Fail … }` guards emptiness *before* either Pass branch |
| `:2447` `LIFT-REFUSED-AND-RECORDED` | legitimate | the universe is a **hardcoded** 7-element `$expected` list; `$missing` empty means all seven present |
| `:886` "database is EMPTY before the drill plants anything" | legitimate | exact-equality on `"0/0/0/0"`, not a "none-of-S" shape — emptiness is the claim |
| `:1181` app role reads the ops mirror | legitimate | requires `$corpusSeen -eq "1"`, a positive count, so it cannot pass off an empty universe |
| `:2481-2482` the LIFT "REMOVED" clauses | legitimate | the documented exclusion: paired with a prior assertion that the set was non-empty, so "0 personal rows" is a **change**, not a vacuum |

**The adjudication holds.** One survivor, seven correct dispositions.

## 20. ROUND 4 - the sha this was validated at, and every run

The work line did not move: `refactor/ai-stack-cleanup` is still `1a6b0b8` and it is this
branch's merge-base, so C.7b required no rebase. The OB1 gitlink moved once, and the commit it
names is on the remote.

```
work line base : 1a6b0b813e241cfb4b74659cbb2c11c8f86616aa  (refactor/ai-stack-cleanup, unmoved)
VALIDATED AT   : 3440ba6  the gate + the OB1 fix (every gate run below)
                 f4ae9f6  the drill + the ledger  (every drill run below)
OB1 gitlink    : e9be2cdb0eb340662df0edadb1ff4b90b0493775
              -> debbbaa10bb9c004a9a9ac104dbe6e5b9c31293e  (the :491 fix + 195 s7's correction)
                 REACHABLE: `git -C OB1 branch -r --contains debbbaa` ->
                 origin/feat/agent-memory-exposure-column. Round 3's blocker is closed.
```

**Why two shas, and why no run below is stale.** `git diff 3440ba6..HEAD --name-only` is five
lines: four documents and `drill-personal-plane-exclusion.ps1`. The drill's runs (2 and 3) were
executed against exactly the script content `f4ae9f6` records — the pin restore and the
self-diagnosis were in the working tree before either run started, and **nothing script-touching
was edited afterwards**; only the notes below were written. The gate's runs were executed against
`3440ba6`, which no later commit modifies. Check both claims with that one-line diff.

Both suites ran from this checkout, **one at a time, never concurrently** — the isolation
"one suite per checkout" exists to buy, and the specific thing §17.4 records about
`prove-rls` overlapping `dfu-done -Only 3`. Every throwaway ran on its own docker network;
nothing was attached to an `ai-stack_*` network, no `:local` tag was written, and **nothing was
written to the live database**. `dfu-done.ps1` was **not** run at all — its clause 3 plants
personal-exposure fixture rows in `openbrain-db`, the live database, and this round needed
nothing from it.

**On the lease, precisely rather than flatteringly.** `lease.ps1 -Status` showed every plane
free at the start; `open-brain` was acquired for `wt-u8h3` (30m TTL) and refreshed once,
before the CI drill run. At release it reported **`lease 'open-brain' already free`** — so the
TTL had EXPIRED somewhere in the tail of the window, and **I cannot claim continuous coverage
for the whole window, only for the parts inside a live TTL.** Nothing depended on the gap: no
other agent held or requested the plane, the only live-touching run was
`prove-agent-memory-rls.ps1`, which is read-only, and every drill container was per-run on its
own network (`docker ps -a` shows none left). The honest statement is that the lease
discipline was followed and the TTL was not watched — and the lesson is mechanical: a run
longer than the TTL needs a refresh loop, not a single refresh.

| check | result |
|---|---|
| `check-corpus-exposure-producers.ps1 -SelfTest` | **4/4** — red on a planted producer, green on a fix, **red on a neighbouring statement's key**, green when the plane is in the statement. EXIT 0 |
| the same, on the tree, **blind fence** | `FAIL - 2 of 13` — `index.ts:491` **and** `pull-gmail.ts:856`. The second is the false positive that forced the fence to be table-aware |
| the same, **table-aware fence**, before the site fix | `FAIL - 1 of 13`, naming `index.ts:491` alone — **the round-4 red** |
| the same, after the site fix | **`OK - all 13 RECOGNISED corpus insert site(s) state their plane`** (742 files), EXIT 0 |
| the same, `-Root <empty dir>` | `FAIL - the scan examined 0 file(s) and found ZERO corpus insert sites`, EXIT **1** (anti-vacuity) |
| the same, `-ShowShapes` | prints the recognised/blind shape lists and the scanned extensions, EXIT 0 |
| the gate's blind-spot claim, **measured** | same variable-table producer: `FAIL - 1 of 1` adjacent, **`OK - all 1 …`** with 40 lines between. Opposite verdicts, same defect |
| the widened alphabet, against the planted evasion set | `FAIL - 8 of 8` across `.mts .cts .tsx .jsx`, a `curl -X POST` `.sh`, supabase-py `.table().insert()`, and two helper-wrapper calls |
| gate runtime (pre-commit budget) | 75s with the fence pre-pass → **16.5s** with the ordinal early-out |
| `drill -SelfTestVacuity` | **4/4 outcomes correct**, EXIT 0 |
| `drill -SelfTestLedger` | **5/5 classifications correct**, EXIT 0 — including "an empty closed-map cannot classify anything as closed" |
| `drill` (bare), **pin pulled too early** | `1 UNDISPOSITIONED GAP(S) - VACUOUS-WIKIPAGES`, EXIT **2**. §19.5 — the ledger refusing my over-claim |
| `drill` (bare), pin restored | **106 passed, 0 failed, 25 named gaps** (19 GAP + 6 VACUOUS), EXIT **2** — bare contract unchanged |
| the same, `-AcceptDispositionedGaps` (what CI runs) | **106 passed, 0 failed, 25 gaps ALL DISPOSITIONED**, EXIT **0** |
| PASS lines printed vs counted, run 2 | **106 / 106** |
| both COVERAGE gates, now counted | `every read tool … was attacked … (0 violations out of 4 derived read tool(s))`; the write twin, `out of 3` |
| `prove-agent-memory-rls.ps1` | **PASSED — 68 checks**, 0 failed, EXIT 0. Live assertion read-only: `source=column personal_memories=0 personal_thoughts=0 personal_memories_col=0 personal_thoughts_col=0` |
| pre-commit, on the real commit | all six checks green, including 3b with its disclosure block |
| `node --test` (wiki-service build gate) | **13 passed, 0 failed** |
| `node --test OB1/recipes/_shared/wiki-pages.test.mjs` | **5 passed, 5 FAILED** — pre-existing, not caused here. §19.5 and `wiki-pages-extractlinks-scope-bug.md` |

**The count did not move: 106 both rounds.** Round 4 changed no assertion's verdict on this
tree. What changed is what a green is allowed to *claim* — the gate's, and CI's.

### Not closed

* **`wiki_pages` has been unwritable since 2026-08-28** (OB1 `dfc6228`), by any compiler and by
  the backfill that exists to repair it. **Found here, deliberately not fixed here** — it belongs
  to the wiki work line, and folding an OB1 wiki fix into an agent-memory-plane branch puts a
  change nobody asked for into a merge nobody would review for it. One-line fix, existing red
  test, blast radius: `documentation/notes/wiki-pages-extractlinks-scope-bug.md`.
  **Cost of leaving it:** `VACUOUS-WIKIPAGES` stays open, and a green drill run does not cover
  the `wiki_pages` surface at all.
* **`RED-COVERAGE` is visible and priced, not closed.** Seven ATTACK sections still have greens
  and no red. **Cost:** for each of those seven, deleting the mechanism actually doing the work
  would look exactly like the mechanism working. Writing seven reds is its own item.
* **The five audit-record vacuities stay open**, and each now states what a green run does not
  cover. They close with C.9 H1's `SECURITY DEFINER` existence probe.

  > **CORRECTED, ROUND 5.** "on that day they will report **CLOSED** rather than failing the
  > build" was TRUE of these five `VACUOUS-*` and **FALSE of the 13 `AUDIT-*` that close on the
  > same day, from the same fix.** `Resolve-Gap` had four call sites - two in `Assert-NoneOf`,
  > one on `RED-COVERAGE`, one in `LiftGap` - so only 9 of the 25 dispositioned ids could ever
  > reach `CLOSED`. The other 16 had a plain `Pass` on their success branch and registered
  > nothing, so they would have been classified **VANISHED**: `FAIL ... did NOT fire AND nothing
  > with that id reached a verdict`, **exit 1** - "the check stopped RUNNING" printed about a
  > check that ran and PASSED. **H1 is being built now; on the day it lands CI would have gone
  > red with 13 failures for having fixed the thing the ledger asks for.** §19.4's own words:
  > a gate that turns red when you fix something teaches people to stop fixing things.
  >
  > FIXED: every dispositioned id's success branch routes through `Resolve-Gap` (`PassGap`), and
  > `EXT-CRM-COPY` - which had *no* success branch at all, only `if { Gap }` - has one. Proved
  > with no database by `-SelfTestLedger`, which now (a) derives the id list from
  > `$GAP_DISPOSITIONS` and fails if any id has no route, and (b) simulates `AUDIT-INSPECT`
  > closing through the REAL reconciliation and the REAL exit rule: `closed=1 vanished=0
  > fails=0 EXIT=0`. Red-proved by reverting one `PassGap` to `Pass`: `BAD 1 dispositioned
  > id(s) have NO success route`.
* **The gate's blind spots are real and stated — and there are more than two.**

  > **CORRECTED, ROUND 5.** "two remaining" was wrong. Verifiers planted three more that the
  > gate reported OK, exit 0, on: a **backtick-quoted** table argument (`$siteArg` accepts `'`
  > and `"` only, and a backtick is a quote in JS), a producer under a directory matching the
  > **`*-data` prune glob** (which the run's `DIRECTORIES PRUNED` line did not print), and a
  > producer under an **allow-listed path** (`*\docs\*`, `*\documentation\*`, also unprinted).
  > A fourth is the donor-above case in §21.1. All are now in `$SHAPES_BLIND`, the prune globs
  > and the allow-list are printed on every run, and `-SelfTest` cases 4–6 PLANT three of them
  > and record the verdict — so a blind spot that closes is noticed instead of asserted about.
  > None is closable by pattern-matching source text, and none needs to be — **the `NOT NULL`
  > column is what refuses those rows.**
* **The live outage is fixed in the tree, not in production.** Ending it still needs the
  deployment checkout's submodule moved AND `openbrain-wiki:local` rebuilt, in a promotion
  window under a lease — a gated deploy, which this session did not take.

---

## 21. ROUND 5 - every sentence made true, and the ledger stopped punishing a fix

Round 5 is a CLOSING round under the operator's convergence bound. H3's core - the typed column,
the door, the mirror, the cutover, the constraint battery - survived six verifiers across rounds
2-4 and is ACCEPTED; nothing here touches it. **No coverage was widened.** Five things were
wrong, and wrong in the same direction every time: a check reporting more than it checks.

### 21.1 The gate's own comment was falsified by running the gate

`check-corpus-exposure-producers.ps1` said the table-aware fence stops "an `exposure` key
belonging to one table's statement clearing another table's statement below it (index.ts:491,
refuted)". **It does not.** The fence clips a site's evidence window at the *donor's SITE LINE*,
and a donor's `exposure` key is not on its site line - it is in the BODY, one to three lines
below, INSIDE the clip.

Measured 2026-08-31 on CRLF fixtures: a `thoughts` POST stating `exposure: "ops"` above an
`agent_memories` POST stating nothing, at separations of **0, 3, 10 and 25 lines**:

```
[check-corpus-exposure-producers] OK - all 8 RECOGNISED corpus insert site(s) state their plane (4 file(s) scanned).
```

**What IS fixed is the ORM victim**, and not by the fence - by the statement-scoped evidence,
which replaces the window with the builder-to-`;` slice and cannot read a neighbour's body at
any distance. `index.ts:491` was an ORM site, which is why that site went red. `-SelfTest`
case 3 cannot catch the URL/ARG case because **its victim is an ORM site too**.

FIXED: the sentence, in the header and at the fence, now says exactly what is fixed and what is
not; the URL/ARG-with-a-donor-above case is in `$SHAPES_BLIND`; and `-SelfTest` **case 4** plants
it and RECORDS the known-bad verdict rather than pretending. The fence was NOT rebuilt.

### 21.2 Two false greens on COUNTED sites - and a third that was live in this tree

The evidence test was a **bare text match** on the word `exposure`. So a producer with no plane
anywhere was reported as *stating its plane*:

| planted | result before |
|---|---|
| `// exposure is applied downstream by the ingest worker` above a bare POST | `OK - all 1 RECOGNISED corpus insert site(s) state their plane` |
| `const exposureMetricsCounter = 0;` above the same POST | identical |

These are **false greens on counted sites**, not misses - the run reports the site as examined.
Same class as `index.ts:491` one layer down: green off text that is not this statement's plane
declaration.

FIXED: `Test-StatesPlane` requires a key or an assignment - the whole word `exposure`, optionally
quoted or backslash-escaped, followed by `:` or `=` - and whole-line comments are dropped from
the evidence. Red-proved as `-SelfTest` cases **3b** and **3c**, which are hard assertions.

**AND THE TIGHTENING FOUND A LIVE ONE.** With the substring test gone,
`OB1/recipes/schema-aware-routing/index.ts:298` turned RED - a producer that *does* state its
plane, correctly, at line 308. The cause: the ORM statement slice ended at the first `;`, and the
eight-line comment between the builder and the key contains "...widens nothing; where this
corpus belongs...". The key was never inside the evidence. **That site had been passing off the
word `exposure` in that comment.** Proof, on a copy with BOTH real plane keys deleted and only
the comment left:

```
NEW gate: FAIL - 1 of 1 recognised corpus insert site(s) do not state a plane:  index.ts:298
OLD gate: OK   - all 1 RECOGNISED corpus insert site(s) state their plane
```

FIXED: `Get-Statement` does not take a terminator from a comment line. A `;` inside a *string*
literal still ends the slice early - declared, and it truncates toward RED, which is the safe
direction now that the evidence test is a key test.

### 21.3 The line splitter required a carriage return - on Windows, not on Linux

Not on the review's list; found while reproducing 21.1. The source of
`check-corpus-exposure-producers.ps1` carried a **bare CR** in its split pattern.

> **CORRECTED IN THE SAME ROUND.** The first draft of this section, and of the two comments in
> the gate, had the direction **backwards** - it said an LF checkout was the broken one and that
> "this worktree is CRLF, so the runs here were unaffected". The opposite is true, and the
> conclusion happened to survive for a different reason. Re-measured before commit.

The repo blob is `22 0d 3f 0a 22` - `"<CR>?<LF>"`, i.e. optional-CR then LF, which is **correct**.
But `core.autocrlf=true` on this machine rewrites that trailing LF on checkout, so the **Windows**
working copy is `22 0d 3f 0d 0a 22` - `"<CR>?<CR><LF>"`, which **requires** a CR. So the false
green needs both a Windows checkout of the script *and* an LF-only scanned file. All four
combinations measured on one fixture (a labelled `thoughts` POST 40 lines below an unlabelled
`agent_memories` POST):

| script checkout | scanned file | old gate |
|---|---|---|
| CRLF (Windows, `autocrlf=true`) | LF | `all 1 RECOGNISED corpus insert site(s) state their plane` - **FALSE GREEN** |
| CRLF (Windows) | CRLF | `FAIL - 1 of 2` (correct) |
| LF (Linux CI) | LF | `FAIL - 1 of 2` (correct) |
| LF (Linux CI) | CRLF | `FAIL - 1 of 2` (correct) |

**Windows is the affected side, and Windows is what every run in this branch used.** Those runs
were nonetheless unaffected, for a reason that had to be measured rather than assumed: of the
**44 LF-majority files** in the scan set, **zero name a corpus table**. Had one appeared, the
vacuity guard would not have caught it - one site is not zero sites.

FIXED: the split is now an explicit `[regex]::Split($text, "\r\n|\n|\r")` over the three
newline forms, correct in all four combinations (re-measured after the fix).

> **CORRECTED, ROUND 6 (2026-08-31).** The sentence above previously carried the pattern as
> **literal control characters** - a real CR LF, a real LF and a real CR - instead of the text
> `\r\n|\n|\r`. The tool layer ate the backslashes when this fix was written up, so the one
> sentence in the note that describes the newline fix was itself broken by newlines: it rendered
> as three fragments across four lines and told the reader nothing. Rewritten via `chr(92)`,
> which is the standing workaround for that trap. The gate itself was never affected - only this
> description of it.

### 21.4 Blind spots that were real and silent

Verifiers planted these; the gate reported OK, exit 0, with unlabelled producers present.

| planted | why it is invisible | now |
|---|---|---|
| a BACKTICK-quoted table argument beside a POST | `$siteArg` accepts `'` and `"` only, and a backtick IS a quote in JS. **0 sites counted** | in `$SHAPES_BLIND`; the ARG line of `SHAPES_SEEN` now says "SINGLE- or DOUBLE-quoted"; `-SelfTest` case 5 records it |
| a producer under `scratch-data/` | `Get-ScanFiles` prunes any `*-data` directory (and reparse points); the run's `DIRECTORIES PRUNED` line printed `$pruneDirNames` only | the glob list is hoisted to `$pruneDirGlobs` and PRINTED, with the reparse-point rule |
| a producer under `docs/` | `$allowPathLike` excludes the `docs` and `documentation` trees; never printed | the allow-list is PRINTED on every run; `-SelfTest` case 6 records it |

**A blind spot that is stated is a scope; a blind spot that is silent is a false green** - and one
stated only in a comment is a claim nobody re-measures. Cases 4-6 cannot fail the self-test (the
miss *is* the documented behaviour), but if one is ever FLAGGED the run says so and asks for the
entry to be struck. Closing a blind spot must not turn a check red.

### 21.5 The ledger punished the fix, and the fix is the named next step

See the corrected bullet in the round-4 close-out above. `Resolve-Gap` had four call sites;
**16 of the 25** dispositioned ids (13 `AUDIT-*`, 3 `EXT-*`) had a plain `Pass` on their success
branch and so could only ever be classified **VANISHED** - `exit 1`, "the check stopped RUNNING",
printed about a check that ran and PASSED. C.9 H1's `SECURITY DEFINER` probe closes the whole
`AUDIT-*` family in one move, and **it is being built now.** `EXT-CRM-COPY` was worse: a bare
`if { Gap }` with no success branch at all.

FIXED: `PassGap` routes the success branch of every dispositioned id through `Resolve-Gap`, and
`EXT-CRM-COPY` has a success branch. `Get-LedgerExit` extracts the exit decision so the rule CI
obeys and the rule the self-test proves are the same seven lines. `-SelfTestLedger` now also
(a) DERIVES the id list from `$GAP_DISPOSITIONS` and fails if any id has no route, and
(b) simulates the scheduled closure:

```
OK   all 25 dispositioned id(s) register on success (PassGap / Resolve-Gap / LiftGap / Assert-NoneOf -Id)
OK   AUDIT-INSPECT closes (assertion RAN and passed): closed=1 vanished=0 fails=0 EXIT=0
OK   AUDIT-INSPECT stops firing with NOTHING registered: closed=0 vanished=1 fails=1 EXIT=1
```

Red-proved by reverting one `PassGap` to `Pass`:
`BAD 1 dispositioned id(s) have NO success route ... AUDIT-INSPECT`, and the self-test FAILS.

### 21.6 Stale counts, and one claim that was wrong on its face

Four comments in the drill and three lines in the runbook said **18** gaps; the dispositioned set
has been **25** since round 3. The drill's ledger comment additionally claimed "all 18 fire under
`-SkipRed` too" - false on its face: `RED-COVERAGE` is raised inside the red phase's own `else`
branch and **cannot** fire under `-SkipRed`, so the exemption is load-bearing today rather than a
provision for the next one.

FIXED by deleting the literals rather than re-typing a third one. The set is `$GAP_DISPOSITIONS`,
its size is printed by the run's own `GAP LEDGER` line, and under `-SkipRed` the run now PRINTS
which dispositioned gaps did not fire - so a reader sees the set instead of a claim about it.
Historical run records (the round-2 tables) keep their figures and are now labelled with the
round they were measured in.

### 21.7 What was deliberately NOT done

* **The fence was not rebuilt.** Making a URL or ARG victim immune needs statement scoping for
  those two shapes, which is a rewrite of the detection core in a closing round. It is DECLARED
  and self-tested instead.
* **No shape and no extension was added** - not even the backtick, which is a one-character
  change. The mandate was to make the sentences true, not to widen the gate; a wider gate with a
  wrong disclosure is the failure being closed here.
* **`OB1/recipes/_shared/wiki-pages.mjs` and `links.mjs` were not touched** - another agent owns
  them this round. `VACUOUS-WIKIPAGES` stays open and stays dispositioned.
* **Nothing was written to the live database**, and no deploy was made.

---

## 22. ROUND 6 - the paragraph that motivated a fix and was never re-read after it

Round 6 is the closing round. It changed **one line of code** and the rest is text. H3's core,
the gate's detection, the drill's `Split-StaleGaps` / `Get-LedgerExit`, the 25-id ledger, the
fence sentence and the E1/E2 fix are **untouched** - all of them survived round 5's verifier and
none of them was in scope here. **No coverage was added: no shape, no extension, no self-test
case.** The self-test still asserts the same six cases and records the same three blind spots.

### 22.1 The gate's header described a world two rounds old, and it shipped that way

`check-corpus-exposure-producers.ps1` said of six planted shapes that *"this gate did not flag
them, did not warn about them, and did not even COUNT them as sites"*. That paragraph was
written to **motivate** widening the alphabet and the ARG/ORM/VERB patterns - and the widening
landed **in the same commit**, `c192041`. So it described a gate that had ceased to exist by the
time it was committed, and then stood through rounds 5 and 6 as a live claim. It also
contradicted two things a few lines away in its own file: `$SHAPES_SEEN`, which lists
`insertRows` as RECOGNISED, and the `$exts` comment, which says `.tsx`/`.sh` were added
*because* those planted copies passed.

**Re-measured 2026-08-31 at `5c81f97`**, one unlabelled fixture per shape, with the historical
blobs (`git show 819b5fe:...`, `git show c192041:...`) run against the *same* fixtures:

| planted shape | `819b5fe` | `c192041` | `5c81f97` |
|---|---|---|---|
| `insertRows("thoughts", rows)` | `ZERO SITES` | **FAIL 1 of 1** | **FAIL 1 of 1** |
| `obPost("thoughts", rows)` | `ZERO SITES` | **FAIL 1 of 1** | **FAIL 1 of 1** |
| byte-identical `.mts` copy | 0 files scanned | **FAIL 1 of 1** | **FAIL 1 of 1** |
| byte-identical `.tsx` copy | 0 files scanned | **FAIL 1 of 1** | **FAIL 1 of 1** |
| `curl -X POST ".../thoughts"` in a `.sh` | 0 files scanned | **FAIL 1 of 1** | **FAIL 1 of 1** |
| supabase-py `.table("thoughts").insert(rows)` | `ZERO SITES` | **FAIL 1 of 1** | **FAIL 1 of 1** |
| `fetch(BASE + "/" + "thoughts", ...)` | **FAIL 1 of 1** | **FAIL 1 of 1** | **FAIL 1 of 1** |
| `const TABLE = "thoughts"` + a template URL | `ZERO SITES` | `ZERO SITES` | `ZERO SITES` |

**Six of the eight are flagged and counted now; one always was; one is still missed.**

Two honesty notes on that table. First, **the original claim was over-broad at its own sha**:
the concatenated path was FLAGGED at `819b5fe` too, so *"did not flag them"* was true of five
of the six bullets, not six. Second, **the fixtures are reconstructions from those bullets**,
not the verifiers' original files, which were not kept - each is the smallest producer that
spells the bullet, and all three blobs were run against the *same* file, so the columns are
comparable to each other even where they are not byte-identical to what a verifier wrote.

And the last two rows are **one case, not two**, and neither is coverage: the gate resolves no values,
so it sees either only when the literal `"thoughts"` lands within `$argWindow` (2) lines of a
post/insert verb.

| same producer, two layouts | verdict at `5c81f97` |
|---|---|
| concatenated path, verb on the **next** line | **FAIL 1 of 1** |
| concatenated path, verb **five** lines away | `FAIL - ... ZERO corpus insert sites` |
| variable-held name, verb **adjacent** | **FAIL 1 of 1** |
| variable-held name, verb **three** lines away | `FAIL - ... ZERO corpus insert sites` |

Same file, same producer, opposite verdict, decided by whitespace.

**FIXED:** the paragraph is kept - it is why the alphabet was widened, and that history is worth
keeping - but it is now marked plainly as *what WAS true, at `819b5fe`*, with the re-measurement
and the two commits named. The u5 note's own round-4 tally, *"the four that still write the
table name as a literal beside a verb are now caught; the two that hold it in a value are not"*,
**was wrong in both halves** and is corrected: six are caught, not four, and only **one**
evasion holds the table name in a value - the concatenated path writes `"thoughts"` as a
literal, which is why it was caught even at `819b5fe`.

### 22.2 A blind-spot list cannot be complete by enumeration - so it stopped being a list

A verifier found six more shapes that clear a **counted** site. Each was planted as an
unlabelled POST plus the clearing text, and each reported
`OK - all 1 RECOGNISED corpus insert site(s) state their plane`, exit 0, at `5c81f97`:

| planted | measured before |
|---|---|
| a TypeScript type annotation: `interface CorpusRow { exposure: "ops" \| "personal" }` | `OK - all 1 ...` |
| a sibling object: `const audit = { actor: "cron", exposure: "ops" };` | `OK - all 1 ...` |
| a plain string literal: `const ERR = "row rejected: exposure: label missing";` | `OK - all 1 ...` |
| SQL text: `"select id from thoughts where exposure = 'ops'"` | `OK - all 1 ...` |
| a **mis-cased key**: `Exposure: "ops"` | `OK - all 1 ...` |
| a block-comment continuation line with no marker, inside a `/* ... */` | `OK - all 1 ...` |

This is the **fourth consecutive round** in which a verifier produced a fresh set of these and
the round closed them one shape at a time - a comment, a look-alike identifier, a semicolon in a
comment, and now a case fold. That is the ENUMERATE-AND-PATCH method A2 abandoned, and the
seventh shape wins exactly as the sixth did. Listing these six would buy one afternoon.

**FIXED, and not by listing them.** The declaration is now the **property that generates the
list**, stated in the header and printed on every run above the examples:

> THE EVIDENCE TEST IS A TEXT MATCH WITHIN A WINDOW AROUND THE SITE. ANY OCCURRENCE OF THE KEY
> THAT IS NOT THIS STATEMENT'S OWN PLANE DECLARATION CAN CLEAR IT. Type annotations, sibling
> objects, string literals, SQL text and comment continuations are KNOWN INSTANCES. THE LIST IS
> ILLUSTRATIVE, NOT EXHAUSTIVE. A green from this gate is never evidence that a row carries a
> plane; only the NOT NULL + CHECK in the database is that.

The six instances live underneath it as `$EVIDENCE_CLEARS`, as examples. The two narrowings that
*are* in place are stated with it, and neither narrows the test to a declaration - only to
something that **looks** like one: the token must be a key or an assignment rather than a bare
substring, and a **whole-line** comment is not evidence.

A statement that stays true as new shapes appear is worth more than a list that is complete for
one afternoon. **Five of the six stay open** and are declared, not closed.

### 22.3 The mis-cased key was a DEFECT, not a declaration gap - the round's only code change

`Test-StatesPlane` ended in `$code -match $statesPlane`, and **PowerShell's `-match` is
case-insensitive**. So `Exposure: "ops"` cleared a bare POST. PostgREST **refuses** that key -
the column is `exposure` - so the gate was green on a producer **the database rejects**. That is
the worst direction a false green can point: not a miss, not a harmless pass, but a green over a
row that will never be written at all.

Red-proved at `5c81f97`, same fixture, one character of difference in the gate:

```
-match  (before) : OK   - all 1 RECOGNISED corpus insert site(s) state their plane    EXIT 0
-cmatch (after)  : FAIL - 1 of 1 recognised corpus insert site(s) do not state a plane   EXIT 1
```

Safe in this tree, checked rather than assumed: **every `exposure` key in the scanned set is
lowercase**; the only capitalised `Exposure` is the TypeScript **type** name
(`OB1/integrations/kubernetes-deployment/agent-memory-policy.ts:50` and its users), and a type
name is not a key. The full scan is unchanged after the fix - `OK - all 13 RECOGNISED corpus
insert site(s) state their plane`, 742 files, exit 0 - and `-SelfTest` still passes its six
assertions and records the same three blind spots.

### 22.4 Every sentence about the gate, swept

The mandate was not the two paragraphs named above: **every sentence in the branch that
describes what this gate catches must match what it was measured to catch.** Six surfaces:

| surface | what it said | what it says now |
|---|---|---|
| `check-corpus-exposure-producers.ps1` header | six shapes "not flagged, not warned about, not counted" | kept as history, **dated to `819b5fe`**, re-measured at `5c81f97`, and the layout dependence stated |
| the same file, the false-green paragraph | an enumeration of shapes that clear a counted site | a **category statement**, with the instances as examples, printed every run |
| `.githooks/pre-commit` check 3b | *"The gate DERIVES the producer set from the tree, so producer thirteen breaks the build instead of breaking production"* | the derivation is **within the recognised shapes**; the enforcement is `195-`; producer thirteen breaks **production**, quietly |
| `195-` section 7 | six shapes, "none of them was flagged or even counted" | dated to `819b5fe`, re-measured, conclusion unchanged and stated as unchanged |
| `PROMOTION-RUNBOOK.md` | "producers it cannot see ... it neither flagged nor counted them" | dated, re-measured, plus the counted-site category |
| `u5-live-producer-rls-regression.md` | *"the evidence for `exposure` is scoped to the statement"* | **only in the ORM shape** - URL and ARG are still a 30-line window clipped at fences |

The pre-commit sentence was the load-bearing one: it is the hook's own justification and the
first thing a future reader believes. Two verifiers refuted it in its earlier form and it was
still there.

Two further defects found while sweeping, neither on the list:

* **21.3's own sentence was broken by the thing it described.** The line quoting the newline fix
  carried **literal control characters** - a real CR LF, a real LF and a real CR - instead of the
  text `\r\n|\n|\r`. The tool layer ate the backslashes, so the one sentence in the note that
  describes the newline fix rendered as three fragments and said nothing. Rewritten via
  `chr(92)`. The gate was never affected - only the description of it.
* **The round-3 record still carried the retracted claim.** 17.3 ended *"so producer thirteen
  breaks the build rather than production"* - the same sentence 21 and the gate header were
  written to retract, still standing four sections earlier. Corrected in place.

### 22.5 Round 6 re-validation (C.7b), from CLEAN CHECKOUTS, one suite per checkout

Measurements in 22.1-22.3 were taken against the working tree at **`5c81f97`** (the sha this
round started from) and against the historical blobs `819b5fe` and `c192041`, extracted with
`git show`. The round's changes are committed at **`7197903`**; OB1 gitlink `debbbaa` ->
**`b604d55`** (two comment-only commits), pushed to
`origin/feat/agent-memory-exposure-column` before each bump, so the pinned sha is reachable
from a fresh `--recurse-submodules` clone.

**A seventh surface turned up in the closing adversarial grep**, after `cc1`/`cc2`/`cc3` had
run: `195-` also said *"every producer states its plane at its own call site"* - a bare
completeness claim about a set assembled by a grep (ten RPC callers) and by a check that only
sees the shapes it recognises (twelve direct producers). Qualified to *"every producer THAT WAS
FOUND"*, with both methods named and the point restated: the completeness that matters is
section 7's refusal, which does not depend on anybody having found the caller. It is a **SQL
comment** - no DDL, no function body, nothing the suites below execute - so the runs recorded
here are unaffected.

**Every suite was re-run at the branch tip after the seventh surface landed**, rather than
arguing that a SQL comment could not matter: `cc4` and `cc5` are fresh clean checkouts at
`292f2aa` with `OB1` at `b604d55`, one suite each, both green. The `7197903` rows below are
kept because they are what the sweep was measured against, not because they are the current
evidence.

**And this statement is true of itself, which is where the regress stops.** Every commit after
`292f2aa` on this branch touches `documentation/notes/u8h3-findings.md` and nothing else -
`git diff 292f2aa..HEAD --name-only` is one line long. That path is on the gate's own
allow-list (`*\documentation\*`), it is read by no suite, and it is not the `OB1` gitlink. So
no run recorded here can be staled by the commits that record it.

Throwaway `git clone`s of this repo, each checked out at the exact sha and with `OB1`
initialised to the recorded gitlink, working tree clean (`git status --porcelain` empty),
**one suite per checkout**:

| checkout | sha | suite | result |
|---|---|---|---|
| `cc1` | `7197903` | `check-corpus-exposure-producers.ps1 -SelfTest` | **PASSED**, exit 0 - the same 6 assertions red/green, cases 4-6 record the same 3 blind spots |
| `cc2` | `7197903` | `check-corpus-exposure-producers.ps1` (full scan) | **OK - all 13 RECOGNISED corpus insert site(s) state their plane** (742 files), exit 0 |
| `cc2` | `7197903` | the same, `-Root <empty dir>` | **FAIL - the scan examined 0 file(s) and found ZERO corpus insert sites**, exit 1 (anti-vacuity) |
| `cc3` | `f6b64ef` | both of the above, on `7197903` **rebased onto the moved work line** | full scan **exit 0**, `-SelfTest` **PASSED** exit 0 |
| `cc4` | `292f2aa` (tip) | `check-corpus-exposure-producers.ps1` (full scan) | **OK - all 13 RECOGNISED corpus insert site(s) state their plane** (742 files), exit 0 |
| `cc5` | `292f2aa` (tip) | `check-corpus-exposure-producers.ps1 -SelfTest` | **PASSED**, exit 0 - same 6 assertions, same 3 blind-spot records |

**The work line moved, and the pass was made rebase-proof rather than asserted to be.** The
merge-base is `8e2eaf4`; `refactor/ai-stack-cleanup` is now `b4311d2`, **2 commits ahead**
(`aa91eac`, `b4311d2`). Both add **new** note files
(`wiki-pages-extractlinks-outage-2026-08-31.md`, `parked-non-dfu-work-2026-08-31.md`) and
`comm -12` over the two changed-file sets returns **empty** - zero overlap with anything this
branch touches. Rather than rely on that, `cc3` performed the rebase in a throwaway and re-ran
both suites:

```
git diff --stat 7197903 f6b64ef -- scripts/ .githooks/ OB1   ->   (empty)
```

The rebase changes **nothing that was tested**, so the pass is not stale under C.7b. The
rebase itself was NOT applied to `work/u8h3` - that is the reviewer's step, and this branch was
not merged.

**Round 5's other suites are untouched by this commit.** `7197903` changes six files -
`scripts/checks/check-corpus-exposure-producers.ps1`, `.githooks/pre-commit`, the `OB1`
gitlink, and three markdown files. `drill-personal-plane-exclusion.ps1`, `dfu-done.ps1` and
`prove-agent-memory-rls.ps1` are byte-identical to their round-5 state, so the round-5 passes
recorded in 21 stand on their own evidence and were not re-run here.

**Isolation:** every run in this round was a static text scan over throwaway fixtures under
`$env:TEMP` and over the clean checkouts above. **Nothing was written to the live database**, no
container was started, no image was built or tagged, nothing was attached to an `ai-stack_*`
network, and no plane lease was required or taken. `ai-stack` was **not pushed** and **not
merged**.

### 22.6 What was deliberately NOT done

* **No coverage was added.** No shape, no extension, no self-test case, no pattern. The
  self-test is the same six assertions and three blind-spot records. Five of the six
  newly-found evidence shapes are **declared, not closed** - closing them by enumeration is the
  failure 22.2 exists to stop.
* **Nothing verified in round 5 was touched** - `Split-StaleGaps`, `Get-LedgerExit`, the 25-id
  ledger and its routes, the fence sentence, the E1/E2 fix. All held under a verifier and none
  was in scope.
* **The fence was still not rebuilt**, and the URL/ARG-donor case stays in `$SHAPES_BLIND` with
  `-SelfTest` case 4 recording it.
* **Nothing was written to the live database**, no deploy was made, and no plane lease was
  needed: every run in this round was a static text scan against throwaway fixtures under
  `$env:TEMP`.
