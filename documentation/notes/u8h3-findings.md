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

The drill reports 0 failures, 18 GAPs and EXITS 2. H4 wires it into CI on `development`, where 2
is a failing build - so the first green tree hits a red gate for a reason that is not a defect,
and the first fix anyone reaches for is `|| true`.

**None of the 18 are H3's to close**: thirteen are the audit-record gap (`auditRefusal`'s
existence probe is bound by the policy that hid the row - closing it needs a SECURITY DEFINER
probe, an H1/H4 decision), three are `openbrain-ext` connecting as `postgres` (H1, measured),
two are the lift's conjunction, which cannot close while the thirteen are open.

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
| full 29-migration initdb chain on a throwaway | no init errors; 195's four notices printed (backfill, table self-test, **door self-test - 12 payloads**, mirror-reader scan - whose notice said "zero readers" and now states its own scope, §16) |
| 195 round trip on that throwaway | revert(200) -> revert(195) -> re-apply(195) -> re-apply(200) -> re-apply both AGAIN: clean, idempotent, row count preserved |
| `scripts/checks/prove-agent-memory-rls.ps1` | **PASSED - 68 checks**, every green with a red beside it |
| `scripts/checks/drill-personal-plane-exclusion.ps1` (bare) | **109 passed, 0 failed, 18 named gaps**, EXIT 2 - the bare contract is unchanged |
| the same, `-AcceptDispositionedGaps` (what CI runs) | **109 passed, 0 failed, 18 gaps ALL DISPOSITIONED**, EXIT 0 |
| (both drill rows are the ROUND-2 numbers; round 3 changed them - see §16 and §17) | |
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
  so the renamed key does not also trip the exit-1 branch and MASK this one; 18 gaps still
  fired, `AUDIT-INSPECT` among them, and the undispositioned detector alone decided the exit -
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

## 16. What the reviewer should check first

Not a finding - a map, because round 2 changed four files and the interesting parts are small:

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
4. *the gap ledger* - 18 ids, each mapped to H1 or H4. If any one of them is really H3's, the
   ledger is hiding work rather than dispositioning it.
