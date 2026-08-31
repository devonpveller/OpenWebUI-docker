# u5graph - the exposure boundary reaches the derived graph

Item `u5graph`, 2026-08-31. Branch `work/u5graph`, cut fresh from `refactor/ai-stack-cleanup`
(**not** stacked on `u5pplane`/`u5rls`, per section C.7b).

## The shas this was validated at (C.7b)

| what | sha |
|---|---|
| base of `work/u5graph` | `ea61627a0035304cc40acb7ae713dc07bd707ab2` |
| every measurement below taken at | `ea61627a0035304cc40acb7ae713dc07bd707ab2` |
| `refactor/ai-stack-cleanup` when the work finished | `8e82447a4c5584cea756c022e341d852924b694c` (1 commit ahead - `docs(u7)`, documentation only) |
| OB1 commit the gitlink now pins | `b81cd5a1bedc2936461440517e66c1846acd0d30`, pushed to `origin/work/u5graph-plane-rls` |
| OB1 commit the line pinned before | `adb7345cb90d90dc2f6ae580547d83214466cfcb` |

The work line moved by one documentation commit during the run. Under C.7b that makes the pass
STALE and the branch must be rebased and re-run before it lands; the diff is
`documentation/implementation-guide/dark-factory-unification/DECISIONS.md` only (41
insertions), and no measurement here reads it. **CORRECTED in round 2** - this table
originally named `PLAN.md`. The conclusion was right and the file was wrong, which is the
cheap half of "a claim wider than its evidence": `git diff --stat ea61627 8e82447` names
DECISIONS.md and nothing else. **That is a note for the reviewer, not a dispensation** - a clean
`git merge-tree` is not a revalidation.

Every suite was run in ONE checkout - `.claude/worktrees/wt-u5graph` - and the live plane was
held under an `open-brain` lease for the whole of the apply.

## What was actually leaking, stated as measured

**The proven disclosure.** `entity_extraction_queue.source_fingerprint` **is**
`encode(digest(thoughts.content,'sha256'),'hex')` - `queue_entity_extraction()` copies
`NEW.content_fingerprint` into it verbatim. Measured on the live database, in a rolled-back
transaction, 2026-08-31:

```
INSERT a thought with metadata.exposure='personal'   -> id 13633, fp 99f36c82...4472
SET ROLE service_role;
SELECT count(*) FROM thoughts WHERE content LIKE 'U5GRAPH-RED-PROBE%'   -> 0
SELECT thought_id, source_fingerprint FROM entity_extraction_queue ...  -> 13633 | 99f36c82...4472
SELECT encode(digest('U5GRAPH-RED-PROBE personal payload alpha','sha256'),'hex')
                                                                        -> 99f36c82...4472
```

`service_role` **is** `PGRST_DB_ANON_ROLE` on `openbrain-postgrest`, so that is every
unauthenticated caller on `open-brain_obnet`. The disclosure is: **the row exists, here is a
content hash of it, and you can confirm any guess by hashing it.**

**What is NOT claimed.** The content-shaped columns - `thought_entities.evidence` (jsonb),
`source_entities.evidence` (text), `consolidation_log.details` (jsonb),
`entity_extraction_queue.last_error` and `.metadata` - are **empty in current production
data**. Today's measured disclosure is the fingerprint, the row's existence, and its
timestamps. Those columns are content-shaped by design, which is why governance covers them;
they were not leaking verbatim content and this note does not say they were.

## The set is derived, and it found an eighth table nobody had

The operator's list named **six**. The schema said **seven** (`source_entities`). Deriving it
from `pg_constraint` says **eight**:

> **`idea_revisions`** - `thought_id BIGINT REFERENCES thoughts(id) ON DELETE SET NULL`, plus
> `summary TEXT NOT NULL` (real content, not a hash) and `content_hash TEXT` (the same
> fingerprint shape as the queue's). 37 rows live, **all 37 with `thought_id` set**. It was on
> no list - not the operator's, not the brief's, not the `init-graph.sql` neighbourhood,
> because it is created by `init-ideas.sql` one corpus over.

`init-graph-plane-rls.sql` section 0 computes its own closure at apply time and **RAISES** on a
member it cannot classify. Both adversarial cases were executed on the throwaway:

```
CREATE TABLE u5g_sneaky_notes (... thought_id BIGINT REFERENCES thoughts(id) ...);
  -> ERROR: 1 table(s) derive from the protected corpus and this migration does not
     classify them: u5g_sneaky_notes.

CREATE TABLE u5g_sneaky_parent (...);
ALTER TABLE thought_entities ADD COLUMN sneaky_id BIGINT REFERENCES u5g_sneaky_parent(id);
  -> ERROR: 1 table(s) are REFERENCED BY the derived closure, are outside it, and are
     neither governed nor registered as a separate corpus: u5g_sneaky_parent.
```

The second check exists because the closure walks child-to-parent only, and
`agent_memory_recall_traces` - which holds a recall's query text - is a **parent** of
`agent_memory_recall_items` and would never be found by a child closure. It happens to be
governed by `180-`. The next one might not be.

## The two tiers, and the honest half

**Tier A** (`thought_entities`, `entity_extraction_queue`, `thought_edges`, `idea_revisions`) -
each row NAMES a thought, so `ob_thought_visible(id)`, SECURITY INVOKER, which makes the
`thoughts` policy the single definition.

**Tier B** (`entities`, `edges`, `source_entities`, `consolidation_log`) - **no read-side
predicate, and the file says so.** These carry no thought id and no thought content. A
citation-based predicate collapses under RLS (an invoker cannot tell "no citation exists" from
"every citation is hidden"), a denormalised label is the "unlabelled defaults to fine" class
one table over, and an EXISTS-per-row is not viable at 69,785 entities / 92,865 edges / 81,273
source links read in batches by the wiki compiler. Tier B is contained at the **write** -
amendment A2's own reframe one layer down - and the containment claim is **measured at apply
time**, not asserted: the migration counts off-plane citations and entities cited only
off-plane and **fails** if either is non-zero. Both were 0 live and 0 on the throwaway.

Tier B still gets FORCE RLS, reduced grants, and a policy NAMED for what it is, with a
`COMMENT ON POLICY` in the database saying it is deliberately wide and why. **Renaming a wide
policy is not narrowing it**, and the file states that in the same breath.

## The four SECURITY DEFINER functions - all four accounted for

| function | what it did with governed content | disposition |
|---|---|---|
| `queue_entity_extraction` | wrote sha256(thought content) into an ungoverned table **as the definer** | **gated.** Off-plane produces no queue row; an ops-to-personal transition DELETEs the existing row. Stays DEFINER: it is a trigger on `thoughts` that must succeed for writers holding no grant on the queue. |
| `thought_edges_upsert` | `INSERT ... ON CONFLICT DO UPDATE ... RETURNING *` as the superuser owner - a caller could write an edge to an invisible thought AND learn from the returned row that one already existed, with its support_count, confidence, classifier_version and merged metadata | **flipped to INVOKER.** It needs no elevation; `service_role` already holds SELECT/INSERT/UPDATE/DELETE and is the PostgREST rpc caller (`recipes/typed-edge-classifier/classify-edges.mjs`). |
| `touch_entities_for_deleted_thought` | reads `thought_entities` as the definer; writes only `entities.updated_at` | **flipped to INVOKER.** Moves no content, needs no elevation, and as invoker each plane touches exactly what it cites. |
| `queue_source_extraction` | writes `md5(sources.content)` into `source_extraction_queue` - the identical shape one corpus over | **deliberately unchanged**, with the reason in the file: `sources` carries no exposure label, no agent-memory mirror, and no FK into `thoughts`/`agent_memories`, so it is not in the closure. See open item 2. |

## The one that nearly shipped: three-valued logic

`ob_corpus_on_ops_plane(md)` is `md->>'exposure' = 'ops'`. For an **unlabelled** row that
returns **NULL**, not FALSE. Written the obvious way -

```sql
IF NOT public.ob_corpus_on_ops_plane(NEW.metadata) THEN ...
```

- `NOT NULL` is NULL, an `IF` treats NULL as not-taken, and **every unlabelled thought would
have sailed through the gate and been fingerprinted** - while the fail-closed policy on
`thoughts` made that same row invisible to the ops plane. The exact leak this migration closes,
reintroduced by an operator precedence nobody looks at. Measured on the throwaway:

```
ob_corpus_on_ops_plane('{}') IS NULL          -> t
(NOT ob_corpus_on_ops_plane('{}')) IS NULL    -> t
COALESCE(ob_corpus_on_ops_plane('{}'), false) -> f
```

A **policy** coerces NULL to false for you. **plpgsql does not.** Fixed with `COALESCE(...,
false)` in the gate and in the tier-B measurement, and `IS TRUE` in the fail-open guard. The
gate probe `G2 UNLABELLED insert queued=0` is the test that would have caught it, and it is in
the probe permanently.

## The evidence

**RED before GREEN, on a throwaway built from the real init chain.** `pgvector/pgvector:pg16`,
own network `wt-u5graph-net`, container `wt-u5graph-db`, never attached to an `ai-stack_*`
anchor network. The chain was **derived from `docker-compose.yml`**, not hand-listed - and the
first derivation regex required end-of-line at `:ro` and silently staged **12 of 25** files
(trailing comments). A derived gate is only as wide as its alphabet; the staged count is now
asserted against the compose count.

| probe | RED (200 not applied) | GREEN (applied) |
|---|---|---|
| A1 thoughts | personal=0 ops=2 | personal=0 ops=2 |
| A2 thought_entities | **personal=1** ops=1 | personal=0 ops=1 |
| A3 thought_entities.evidence | **personal=1** ops=1 | personal=0 ops=1 |
| A4 queue fingerprint | **personal=1** ops=1 | personal=0 ops=1 |
| A5 queue existence by id | **personal=1** ops=1 | personal=0 ops=1 |
| A6 thought_edges | **personal=1** ops=1 | personal=0 ops=1 |
| A7 idea_revisions.summary | **personal=1** ops=1 | personal=0 ops=1 |
| A8 idea_revisions.content_hash | **personal=1** ops=1 | personal=0 ops=1 |
| G1 gate: personal insert queued | **1** | 0 |
| G2 gate: UNLABELLED insert queued | **1** | 0 |
| G3 gate: ops insert queued (CONTROL) | 1 | **1** |
| G4 gate: ops-to-personal transition queued | **1** | 0 |

Every negative carries a positive control written through the same statement at the same
moment. **Two control bugs were found by looking at the controls rather than the verdicts:**
A6's ops arm counted a personal-to-ops edge (no ops-to-ops edge existed, so the control could
never be positive), and the PostgREST door counter counted occurrences of the marker string in
the body - which `thought_edges` responses, being integer ids, do not contain, so it reported
`ops_rows=0` for a door that was working correctly. **A control that cannot be positive is not
a control**, and a counter that cannot see the control is the same failure one layer out.

**Idempotence.** The first version was NOT idempotent: a second apply died on
`policy "thought_entities_plane" already exists`, because only the OLD policy names were
dropped - leaving the tier-A tables half-migrated. Every policy the file creates is now dropped
by its own name too. Three consecutive applies COMMIT.

**Revert round trip**, on the throwaway: revert restores `force=f` and the original permissive
policy counts on all eight tables, restores all three functions to SECURITY DEFINER, the RED
column returns in full, and re-applying COMMITs.

**Live apply**, under the lease. Ops-path counts as `service_role`, before and after, and again
after the fixture teardown - **identical all three times**:

```
thoughts 13001 | entities 69785 | thought_entities 54063 | edges 92865
entity_extraction_queue 13001 | source_entities 81273 | consolidation_log 0
thought_edges 0 | idea_revisions 37 | wiki_pages 47987
```

**The ops WRITE path**, exercised as `service_role` in the entity-extraction worker's own shape
(it connects through PostgREST - `SUPABASE_URL=http://openbrain-rest` - so it **is** bound by
this migration), rolled back: it sees its queue row, UPDATEs it to `processing`, INSERTs an
entity, INSERTs `thought_entities`, INSERTs an edge, marks `done`, and calls
`thought_edges_upsert` - now INVOKER - successfully. All seven steps pass.

**Eight PostgREST doors**, attacked over the network from a container on `open-brain_obnet`
with a persisted live fixture, each with a live ops control: `thoughts`, `thought_entities`,
`entity_extraction_queue` by fingerprint, the same by id, `thought_edges`, `idea_revisions`,
the `thought_entities` embed of `thoughts(content)`, and the `v_thoughts` view. **All eight
`personal_rows=0 ops_rows>0`.**

**The full 28-file init chain on a genuinely fresh volume**: `test-quartz4-offline.ps1` gives
`ALL OFFLINE CHECKS PASSED`, including *initdb chain derived from compose (28 migrations)*,
*every init*.sql is mounted in the chain*, *preview compose carries the same chain*, and
*init chain ran without errors*.

**Teardown verified.** Every fixture row deleted; residual = 0 on thoughts, queue,
thought_entities, idea_revisions, entities and thought_edges. Production shows **0 personal
rows** in `thoughts` and **0** in `agent_memories`.

## The provenance debt this uncovered, and closed

The brief named one: `init-agent-memory-rls.sql` was applied to production from unmerged
`work/u5rls`. Deriving the chain found a **second and worse** one:

> **`init-agent-memory-corpus-failclosed.sql` was applied to the live database on 2026-08-31,
> is documented in `PROMOTION-RUNBOOK.md` on the work line, and existed in NO GIT REF
> ANYWHERE.** `git log --all --diff-filter=A` across every OB1 ref returns nothing. It survived
> only as an untracked file in `.claude/worktrees/wt-dfudone/OB1/docker/`, a worktree whose
> ai-stack branch had already been merged **without** the SQL. A fresh volume would have come
> up with the predicate still fail-OPEN. The `corpus-predicate-source-on-work-line` probe in
> `dfu-done.ps1` reports exactly this and is red today for exactly the right reason.

Both pairs, plus my own, now land on the work line and are mounted at 180 / 190 / 200, with
runbook sections carrying the apply command, the verify-by-query, the recorded result and the
rollback. A doc typo was fixed while there: the runbook's rollback path was missing a path
separator (a backslash-r eaten as an escape) in four places.

## Open items - named, not fixed here

1. **The eight superuser connections.** `openbrain-mcp`, `-ext`, `-workbench`,
   `-suggestion-worker`, `-research`, `-curator`, `-grounding-backfiller` and `-chunk-worker`
   all connect as `postgres` (rolsuper/rolbypassrls = t/t) and are exempt from every policy in
   this file, FORCE included. What IS bound is the whole PostgREST surface, because PostgREST
   issues `SET ROLE service_role` - and that is where the disclosure was measured. Closing this
   is a `DB_USER` change, or a `SET ROLE` at each connection chokepoint. Clause 3's
   `door-openbrain-mcp-door` and `door-cloud-search-thoughts` are red for this reason, and this
   migration does not change them.
2. **`sources` has RLS OFF ENTIRELY** (relrowsecurity = f), as do `source_chunks`,
   `source_revisions` and `wiki_pages`. `queue_source_extraction()` writes
   `md5(sources.content)` into `source_extraction_queue` under a `USING(true)` policy - **the
   identical shape as the leak this item fixed**, one corpus over. It is not exploitable in the
   same way today because `sources` carries no exposure label and no agent-memory mirror. If
   the research corpus ever carries plane-labelled content, this is the same defect and must be
   fixed with it.
3. **`wiki_pages`** (47,987 rows, `body TEXT`, RLS off) is compiled from `thoughts` via
   `entities`. Its containment is at the WRITE, in `recipes/_shared/corpus-plane.mjs` imported
   by `generate-wiki.mjs` and `wiki-service.mjs` - which lives on `work/u5rls`, **not on this
   work line**.
   **UNDERSTATED, corrected in round 2 in this item's favour.** "The published wiki has no
   plane filter in the deliverable" was too pessimistic. `openbrain-wiki` reads the corpus
   through `OPEN_BRAIN_URL=http://openbrain-rest` (verified on the live container), i.e.
   PostgREST as `service_role` - so the `thought_entities` policy THIS migration installs
   already binds the compiler, whether or not the `corpus-plane.mjs` filter has landed. The
   compiler's read path is bound at the database; measured on the throwaway,
   `thought_entities` returns personal=0 / ops=1 to `service_role`. `wiki_pages` itself is
   still RLS-off (item 2) and is contained at the WRITE by that binding.
   `dfu-done` carries it as the named manual check `wiki-compiler-personal-exclusion`.
4. **An entity extracted while its thought was ops, whose thought later becomes personal**,
   keeps its `entities` row and its now-invisible `thought_entities` row. The join is bound;
   the entity's existence is not. Nothing is dropped, per class 4. Under the write gate this
   cannot arise for new content.
5. **`init-agent-memory-embedding.sql` and `init-agent-memory-corpus-plane.sql`** reached the
   mount on `work/u5rls` but were **never applied to the live volume** (measured:
   `agent_memories.embedding` does not exist, and the live `match_thoughts` carries no exposure
   filter). That is the two-place invariant failing in the other direction, and it belongs to
   `u5rls`.

---

# Round 2 - three refutations, and a recall that CONFIRMED a class already in hand

> **Heading corrected in round 3.** It read *"the recall that named the class before I wrote
> a line"*, which is wider than the evidence: the class was named in the send-back
> refutation and typed verbatim into the query, so the plane confirmed a class already in
> hand rather than naming it first. What it genuinely contributed is below - the constraint
> to sweep the SHAPE rather than fix the instance. Clause 8 rests on exactly this
> distinction.

## Step zero: what the memory plane said, and what I did differently because of it

`scripts/checks/recall-sibling-class.ps1` (on `work/c8plane`), run **before any SQL was
written**, against the ops door at `127.0.0.1:8062`.

- **Outcome:** `RECALL_OUTCOME=INFORMED returned=5`
- **Trace:** `65f57a92-74a5-4711-b2ba-27c5c87d40f4`
- **Memories returned:** `e0af6087-9513-4966-8c36-a81407c85977` (class 16, derived data
  escaping a row boundary), `73a38752-ba72-4366-a6bc-1ca018c5677b` (class 5, a claim wider
  than its evidence), `fced2ede-82cf-4fa7-b453-1a42cb013ada` (class 4, a derived gate whose
  alphabet is too narrow), `b6af0900-8fb9-43e9-a315-9572949e155a` (the "fixed one, left the
  sibling" constraint), `bdb7f947-9a38-4b6d-952e-4a3a6be25a2d` (class 14, re-implementing
  another system's rules).

**Two of the five were the refutations, before I had read them in the schema.**

`fced2ede` - *"the iteration is genuinely derived from disk, and the PREDICATE that selects
what counts is hard-coded. Completeness is a property of scan x PREDICATE, not of the scan.
What is this gate's ALPHABET, as distinct from its iteration? Name it out loud."* That is R1
exactly: section 0 iterated a genuinely derived closure and then selected what counted with
`v_governed_180`, a hardcoded name array.

`b6af0900` - *"a defect is not fixed when its instance is fixed; it is fixed when the artifact
has been SWEPT for its shape and every instance is dispositioned... put it in the drill, a
test that FAILS when a new one does not route through the shared normaliser. A discipline that
depends on remembering is the normative governance already recorded as FALSIFIED."* Its fifth
recorded instance is *"a normaliser written for ONE reader, three readers left raw IN THE SAME
FILE"*.

**What I did differently, concretely - three things I would not otherwise have done:**

1. **I did not fix `agent_memory_audit_events` and move on.** `b6af0900` says the instance is
   not the defect. So the wide policy is the smaller half of R1's fix; the larger half is that
   the predicate is now **one function**, `ob_relation_governed()`, with **three** callers -
   the closure arm, the referenced-by arm (which had it written out inline, which is precisely
   how the two arms drifted apart) and the section 7 post-condition. A fourth reader that
   re-implements it is the next instance, and it now has one obvious place to call instead.
2. **I swept the view class instead of fixing the one leaking view.** `ideas_owed_research` is
   the only view touching this closure today. `b6af0900` is why section 6b **derives** every
   view in `public` lacking `security_invoker` and fixes all four, and why the adversarial case
   creates a *brand-new* leaking view and re-applies to prove the sweep is a mechanism rather
   than a one-off (personal 1 -> 0, ops control 1).
3. **I named the alphabet out loud, per `fced2ede` - and it caught a scoping error of my
   own.** Asking "what is this predicate's alphabet?" is what made section 7(e) explicit about
   applying to tier A plus the classified 180 tables and *not* to `v_all`; tier B is
   deliberately wide on the read side and contained at the write. The first run of my own new
   assertion went RED on `consolidation_log, edges, entities, source_entities` and told me so.

`73a38752` (a claim wider than its evidence) is why the R1 disclosure below is bounded to what
the probe actually returned rather than to what the refutation asserted - see "two claims I did
not simply relay".

This is a recall consumed by a **later** effort: the memories were written by earlier rounds
and are cited here in a session that did not seed them.

## R1 - a closure member waved through by a hardcoded list

`agent_memory_audit_events` is in the closure (`memory_id REFERENCES agent_memories(id)`), was
`rls=t force=t`, **and** carried `agent_memory_audit_events_service_role_all` with `qual =
true`. FORCE was on and the policy still permitted everything - which is why reading
`relforcerowsecurity` alone did not catch it. The string `agent_memory_audit_events` appeared
0 times in the round 1 migration.

Applying the referenced-by arm's own predicate to the closure arm's own list, live:

| relname | rls | force | wide permissive |
|---|---|---|---|
| agent_memories | t | t | 0 |
| agent_memory_artifacts | t | t | 0 |
| **agent_memory_audit_events** | t | t | **1** |
| agent_memory_recall_items | t | t | 0 |
| agent_memory_recall_traces | t | t | 0 |
| agent_memory_relations | t | t | 0 |
| agent_memory_review_actions | t | t | 0 |
| agent_memory_source_refs | t | t | 0 |

Seven pass, one fails - the list was right about seven tables and wrong about the one that
mattered, which is why a name in a list is not a measurement.

**Fixed:** `v_governed_180` shrinks to the seven, each now **tested** with
`ob_relation_governed()`; `agent_memory_audit_events` becomes tier A2, governed here on parent
visibility of **both** FKs (`memory_id` via `ob_memory_visible`, `trace_id` via an
invoker-bound `EXISTS` against the traces table, because a trace carries the caller's query
text). Both NULL arms are a foreign key being absent, not a label being absent - both FKs are
`ON DELETE SET NULL`, and 12 of the 67 live rows never had a `memory_id`.

> **REFUTED AND REPLACED IN ROUND 3, and the sentence above is wrong twice.** The NULL count is
> **21**, not 12, and none of those rows "never had" a `memory_id`: their event types are
> `memory_written` (12), `memory_confirmed` (5) and `memory_used` (4), every one of which names
> a memory by definition, so all 21 are ORPHANS of deleted memories. `ON DELETE SET NULL` is not
> what makes the arm safe - it is what ARMS it. Delete the parent and the audit row, payload
> free text and all, becomes readable. Round 3 closes the table to the unauthenticated door
> instead; see below.

## R2 - views, the same boundary one relkind across

`public.ideas_owed_research` is owned by `postgres` with `security_invoker` NOT SET and JOINs
`idea_revisions`. A non-invoker view executes with its owner's privileges, so RLS on the base
table does not apply.

**Derived, not hand-listed** - exactly four views in `public` lacked the flag
(`ideas_owed_research`, `research_run_metrics`, `reusable_claims`, `ungrounded_claims`), and
the two that have it (`v_agent_memories`, `v_thoughts`) are the two
`init-agent-memory-rls.sql` created - the file documenting this exact hazard in capitals at
line 343 ("without it this view would bypass the exposure boundary"). The class was known one
file over.

**Bounded honestly.** The view projects `i.*` only, so `idea_revisions.summary`, `.thought_id`
and `.content_hash` are **not** returned. The disclosure is the **existence** of a governed
revision - an idea appears in the "owed research" list because of a revision the caller cannot
see - not content and not a fingerprint.

Round 1's section 7 could not have caught this: every assertion in it reads `pg_class` and
`pg_policies`, which describe **tables**. A view has neither `relrowsecurity` nor policies, so
the post-condition titled "measured, not assumed" was structurally blind to it.

**Fixed:** section 6b derives every view in `public` lacking the flag and sets it; section 7(f)
asserts none remain. All four are fixed, not just the leaking one - setting it on the other
three is **measured** inert today (both roles hold SELECT on `claims`/`research_jobs` and
EXECUTE on `claim_min_depth()`, and both base tables carry permissive `USING(true)` for both
roles), and it means governing `claims` tomorrow is not silently bypassed by a view written
years earlier. A **materialized** view over a force-RLS relation is refused outright rather
than skipped: it has no `security_invoker` option and its rows were computed and stored by its
owner, so RLS can never reach them.

## R3 - the referential-integrity existence oracle

A FK is enforced by an internal trigger RLS does not bind, and it fires **after** `WITH CHECK`.
The four tier A tables are immune because their `WITH CHECK` names `thought_id` and therefore
fires first; `agent_memories` was not, because its `WITH CHECK` looked only at `metadata` and
`user_id`.

**Fixed** on **both** policies - permissive policies are OR-ed, so an arm added to one and not
the other closes nothing, and two policies on one table are two readers of one rule, which is
`b6af0900`'s shape again. `WITH CHECK` only: the `USING` halves are unchanged from 180, so a
caller's ability to READ a memory it owns never depends on the plane of the thought behind it.

**Ops impact measured before the change, not after:** all 21 live `agent_memories` rows
carrying a `thought_id` point at ops-plane thoughts, so nothing that works today is narrowed.

## The evidence

RED before GREEN on a throwaway (`pgvector/pgvector:pg16`, container `wt-u5g2-db`, own network
`wt-u5g2-net`, never attached to an `ai-stack_*` anchor network), built from the initdb chain
**derived from `OB1/docker/docker-compose.yml`** with the staged count asserted against the
compose count: **28 mentions, 28 derived pairs, 28 files staged**. Synthetic fixtures only.

| probe | RED (200 round 1) | GREEN (200 round 2) |
|---|---|---|
| P0 `thoughts` (control from 180) | personal 0 / ops 1 | personal 0 / ops 1 |
| P1 `agent_memory_audit_events` | **personal 2** / ops 1 | personal **0** / ops 1 |
| P1b leaked `payload` note text | **3 notes incl. the refusal** | 1 (the ops note) |
| P2 view `ideas_owed_research` | **personal 1** / ops 1 | personal **0** / ops 1 |
| P2b base `idea_revisions` | personal 1 / ops 1 | personal **0** / ops 1 |
| P3a insert w/ hidden `thought_id` | **succeeded** | `42501` |
| P3b insert w/ nonexistent id | `23503` | `42501` |
| P3c insert w/ visible id (control) | succeeded | succeeded |
| P4 views lacking `security_invoker` | **4** | 0 |

P3a and P3b return not merely the same class but the **same SQLSTATE and the same message**,
verified explicitly: `hidden SQLSTATE=42501`, `nonexistent SQLSTATE=42501`. That is the fix -
the two answers become one answer.

**Adversarial, each RED for the right reason.**

1. A 180 table regressed to `USING(true)` (`agent_memory_recall_items`) -> section 0 raises and
   names it. This is the check whose absence *was* the defect.
2. A **brand-new** owner-rights view over `idea_revisions`, created after the migration -> on
   re-apply, section 6b finds and closes it: personal 1 -> 0, ops control 1. The sweep is a
   mechanism, not a one-time edit.
3. A **materialized** view over a force-RLS relation -> refused, naming it.

**Idempotence:** three consecutive applies COMMIT. **Revert round trip:** GREEN -> revert ->
RED returns on both leaks and all four view flags -> re-apply -> GREEN.

**Ops is unbroken.** Four `service_role` ops writes succeed (audit event with a visible parent,
audit event with a NULL parent, `agent_memories` with a visible `thought_id`, `agent_memories`
with none). The `access_refused` audit for a **hidden** memory still writes, because the real
writer is a superuser - `openbrain-mcp` runs `DB_USER=postgres`, verified on the live
container, and RLS binds no superuser with or without FORCE. Final state as `service_role`:
`thoughts=0 idea_revisions=0 ideas_owed_research=0 audit_personal=0 agent_memories=0` personal
rows readable through any table or view.

**Nothing was applied to the live database in round 2.** The two-place invariant is carried by
the new `PROMOTION-RUNBOOK.md` section (apply, verify-by-query, recorded result, rollback);
production still runs round 1 and therefore still carries all three findings until an operator
applies it.

## Two claims I did not simply relay

The refutations were adjudicated individually before being acted on, per `73a38752` (adjacency
to a verified claim is not evidence).

1. **The R1 disclosure is narrower than stated.** The refutation says the table leaks "a hidden
   personal memory's id, verbatim summary, content excerpt and timestamp". The table has no
   `summary` and no `content` column, and its `payload` keys across all 67 live rows are
   `action, from, note, superseded_by, to, via`. The probe's LEFT JOIN back to `agent_memories`
   returned **NULL** summary and content for the personal row while returning both for the ops
   control. What leaks is **id, event history, timestamps and payload free text** - real, and
   worth closing, but not the memory's text. The leak is fixed either way; the record should be
   accurate about which.
2. **The four views are four.** Derived from `pg_class.reloptions` rather than taken on trust;
   it agreed.

## Corrections to my own round 1 note

- **My section 4 `access_refused` claim was FALSE, and I checked it live.** I wrote that
  `access_refused` is not in the `agent_memory_audit_events` CHECK constraint, and therefore
  that every refusal audit fails silently and U5's refusal-record argument is unsound. The live
  constraint is `CHECK (event_type = ANY (ARRAY['recall_requested', 'memory_returned',
  'memory_used', 'memory_ignored', 'memory_written', 'memory_confirmed', 'memory_edited',
  'memory_rejected', 'memory_superseded', 'memory_disputed', 'access_refused']))`.
  `OB1/docker/init-agent-memory-access-refused.sql` adds it, it is mounted in the chain at
  `150-` (confirmed by the compose-derived staging: it is one of the 28), and it sits in the
  same directory as the file I did read. I hedged it as unverified and then stated it in a way
  that undermined a sound result - this effort's own "stopped the read early then generalised"
  class, aimed at a correct conclusion. The check I should have run took one command. Round 2
  additionally verified the *behaviour*: a refusal audit for a hidden memory writes
  successfully.
- **The C.7b staleness table named the wrong file.** `ea61627..8e82447` is `DECISIONS.md` only,
  41 insertions - not `PLAN.md`. Corrected above; the conclusion (documentation only, no
  measurement reads it) was unaffected.
- **The wiki item was understated, in its own favour.** Corrected in open item 3 above:
  `openbrain-wiki` reads through PostgREST as `service_role`, so this migration's
  `thought_entities` policy already binds the compiler.

## Open items - round 1's 1-5 stand, plus one

6. **`ob_relation_governed()` is conservative about `qual IS NULL`**, which also counts a
   `FOR INSERT` policy (whose `qual` is always NULL) as wide. No such policy exists on these
   tables today, and failing closed is the right direction for a boundary gate - but a future
   legitimate INSERT-only policy will need this predicate taught the difference, not the gate
   deleted.

## C.7b discipline for round 2, and the arbiter's verdict

**Rebased first, re-run after, sha recorded.** The branch was rebased onto the work line
`refactor/ai-stack-cleanup` at `8e82447a4c5584cea756c022e341d852924b694c` **before** the
validating run, not after it.

| what | sha |
|---|---|
| work line rebased onto | `8e82447a4c5584cea756c022e341d852924b694c` |
| `work/u5graph` after rebase, **the sha the suite ran at** | `ee66e82493bae1f7fe190b00e5e22da97de4d5e9` |
| OB1 commit the gitlink pins | `53f0880748555b2bdb970b7d5365f1d86c9d077c` (pushed to `origin/work/u5graph-plane-rls` BEFORE the bump) |
| OB1 commit the line pinned before | `b81cd5a1bedc2936461440517e66c1846acd0d30` |

The whole suite was re-run on a **second, genuinely fresh** volume built from the chain
re-derived at the rebased sha (28 mentions = 28 pairs = 28 staged again), and every RED and
GREEN figure in the table above reproduced identically. One checkout
(`.claude/worktrees/wt-u5graph`), one suite.

**A race the ops control caught, worth recording.** On the first attempt at the rebased sha
every probe returned 0 — including the ops controls. `pg_isready` had answered the *temporary*
server postgres runs during initdb, so the fixtures never landed. All-zeros reads exactly like
a perfect GREEN if you only look at the personal column; it is the **ops control at 0** that
says the measurement is void. The wait now keys on `PostgreSQL init process complete`.

### `dfu-done.ps1 -Only 3` — the arbiter, run at `ee66e82`

**Verdict: clause 3 UNMET, board FAILED.** Reported, not worked around. The four subjects that
hold it open are:

| subject | verdict | why |
|---|---|---|
| `door-openbrain-mcp-door` | **fail** | returns the personal fixture; connects as `postgres` (rolsuper/rolbypassrls = t/t) |
| `door-cloud-search-thoughts` | **fail** | returns the personal fixture |
| `door-wiki-compiler-output` | indeterminate | named manual check, no recorded result in `dfu-done-manual.json` |
| `door-mcp-read-tools` | indeterminate | returned neither fixture nor ops twin — with no positive control it refuses to call itself a pass |

**None of these is caused by round 2, and round 2 does not claim to close them.** The two
failures are round 1's open item 1 verbatim: RLS does not bind a superuser, and those doors
connect as `postgres`. Closing them is a `DB_USER` change or a `SET ROLE` at each connection
chokepoint — a different item.

**The decisive point about this run: clause 3 measures the LIVE database, and the live database
still runs round 1.** Round 2 was deliberately not applied to production, so clause 3 *cannot*
reflect it either way. Verified after the run: production still shows
`audit_events wide policies=1` and `views lacking security_invoker=4` — the two findings this
round fixes are still open in production, exactly as the runbook says. What clause 3 does
confirm is that round 2 broke nothing: all nine automated door and predicate probes still pass
with live positive controls, `postgrest-surface-sweep` passes across 56 tables / 242 text
columns / 212 jsonb keys, and `fixture-cleaned-up` reports production at **0 personal rows in
either corpus** — independently re-checked here as `thoughts_personal=0`,
`memories_personal=0` against 13,001 thoughts and 21 memories.

**Class 4 end state:** synthetic fixtures only, additive migration only, nothing dropped, both
throwaways and their networks removed (`0` leftover containers, `0` leftover networks), and
zero personal rows anywhere in production.

---

# Round 3 - three refutations, one defect, and the sweep moves from relkinds to mechanisms

## Step zero: what the memory plane said, and when - stated precisely

Two consultations, and they are not the same thing.

**Before any SQL was written**, the plane was asked in my own words, through the Open Brain
search tool: *"RLS policy NULL column arm permits, default deny, Postgres row level security
leak"* and *"enumerating what to protect trails the mechanisms; fail closed default deny design
principle"*. The first returned **nothing**. The second returned one 2026-06-08 memory about
agent-org model risk, unrelated. **The plane, asked at the start, had nothing to say about this
class.** That is a real outcome and it is recorded as one.

**After the SQL was written and validated**, `scripts/checks/recall-sibling-class.ps1` (on
`work/c8plane`) was run against the ops door with the defect stated as the query:

- **Outcome:** `RECALL_OUTCOME=INFORMED returned=5`
- **Trace:** `60666580-4c2e-4814-bae4-154820114f13`
- **Returned:** `1be66284` (class 2, a guard deciding by exception), `9bdae627` (class 12, an
  exemption resting on a check the guard declines to make), `ded7af3f` (class 1, a check green
  while checking nothing), `e0af6087` (class 16, derived data escaping a row boundary),
  `05cccbfe` (class 13, a checker deriving its population from the document under test).

**What it changed, and what it did not - reported to the plane, both ways.**

`05cccbfe` (class 13) **changed the code**, and this is the one clean instance. Section 7's
mechanism sweeps had a **hardcoded** `v_scope` array of thirteen table names. That is the class
one step removed: govern a new table in 180 tomorrow and the absence, uniqueness and
foreign-key sweeps would report clean having never looked at it, with coverage still reading
N of N. `v_scope` is now **derived** - every FORCE-RLS table in `public`, with tier B subtracted
by name and with its reason - and the memory's own remedy is applied: a **floor** taken from
what this file and 180 are specified to govern is checked back against the derived population,
so a subject leaving the population is a failure rather than a smaller N. Adversarial case F
below is the proof: a brand-new force-RLS table with a wide policy and **no** foreign key into
the corpus - invisible to section 0's FK closure - is now caught.

`ded7af3f` (class 1) **changed the runbook**. The by-hand section-7(h) block printed
`arms that permit on absence: ` from a `COALESCE` over `array_to_string`, which returns the
**empty string** for a clean run - indistinguishable from a query that matched nothing. It now
prints an explicit `NONE` **and** the number of policies actually examined. It is also why the
closed audit door's control is stated rather than assumed: both arms read 0 through that door
**by design**, so the control that the door is alive is the `thoughts` probe in the same
session, and the control that the rows exist is the superuser count.

`1be66284` (class 2 - "an unhandled input falls through to a PASS, so 'could not check' is
recorded as 'fine'") is **the same class as this round's defect in different vocabulary**: a
NULL plane column falling through to VISIBLE. It was reported **IGNORED**, not used, and the
distinction matters more than the credit: the class had already been named in the send-back
refutation that started this round, and the SQL was already written when the recall ran.

**And a correction to round 2's heading.** Round 2's section was titled *"the recall that named
the class before I wrote a line"*. That is wider than its evidence. The class was named in the
send-back refutation and typed verbatim into the query; the plane **confirmed a class already in
hand**, and its contribution was to generalise it (the "sweep the shape, do not fix the
instance" constraint) rather than to name it first. The heading is corrected in place. Clause 8
rests on exactly this distinction, and overstating it here would corrupt the only evidence the
clause has.

## R1 - `idea_revisions`: an unauthenticated existence oracle over a governed row

`WITH CHECK` was `(thought_id IS NULL OR ob_thought_visible(thought_id))`. **Omit the column and
the NULL arm passes**, so RLS never refuses - and `idea_revisions_pkey (idea_id, revision)`,
which no policy binds, answers instead. No guessing is needed: `ideas` is ungoverned by design
(section 0 registers it as a separate corpus), so `GET /ideas` hands over the ids.

Measured as `service_role` on a throwaway built from the compose-derived 28-file chain, with an
ops positive control on **both** arms:

```
INSERT (personal idea, revision 1)  -> 23505 duplicate key on idea_revisions_pkey
INSERT (personal idea, revision 99) -> OK
INSERT (ops idea,      revision 1)  -> 23505 duplicate key            [CONTROL]
INSERT (ops idea,      revision 98) -> OK                             [CONTROL]
read idea_revisions                 -> 0 personal / 1 ops
```

The last line is the point: **the read policy was working and the unique index was undoing it.**

Round 1's defence of that arm - *"the NULL arm is a FOREIGN KEY being absent, not a LABEL being
absent; a revision with no thought_id is not derived from the corpus"* - also fails on its own
terms. The foreign key is `ON DELETE SET NULL`, so a revision that **was** derived from a thought
orphans to `thought_id IS NULL` when the thought is deleted, and its `summary` - real content,
not a hash - becomes readable. Absence never meant "never had one". It meant "cannot be
established".

**Fixed** as the property: `thought_id IS NOT NULL AND ob_thought_visible(thought_id)` in both
halves of both policies, so `WITH CHECK` refuses at `42501` **before** the primary key is
consulted. Ops impact measured first: all 37 live rows carry a `thought_id`, so nothing readable
today becomes unreadable, and the writer (`openbrain-idea-refinery`) connects as `postgres`.

## R2 - `agent_memory_audit_events`: the same shape, armed by `ON DELETE SET NULL`

Round 2's policy was `(memory_id IS NULL OR ob_memory_visible(memory_id)) AND (trace_id IS NULL
OR EXISTS(...))`. It held **only while the parent lived**:

```
PHASE1 live parent   personal 0 / ops 1     <- the round 2 fix working
  RESET ROLE; DELETE FROM agent_memories WHERE summary = 'U5G3-MEM-PERSONAL';
PHASE2 orphaned      personal 1 / ops 1     <- LEAK
  memory_id=NULL trace_id=NULL payload={"note": "U5G3 personal note text"}
```

**And round 2's evidence for that arm was mis-measured, which I checked rather than relayed.**
The file said "12 of the 67 live audit rows have never had a `memory_id` at all". At the same
snapshot the total is right and the NULL count is **21**, not 12 - and "never had" is false:

| event_type of the NULL-`memory_id` rows | count |
|---|---|
| `memory_written` | 12 |
| `memory_confirmed` | 5 |
| `memory_used` | 4 |

Every one of those events names a memory by definition, so all 21 are **orphans** of deleted
memories - precisely the case the arm permitted. The companion claim, that "a row whose parent
has been deleted has nothing left to protect", was asserted rather than measured and is false:
one live orphan reads `{"note": "synthetic fixture, confirmed to prove the review gate"}`.

**Fixed by closing the table to the unauthenticated door**, read and write, rather than by
patching one arm - and the closure is argued from measurement, not preference:

- `authenticated` holds **no grant at all** on it;
- the only PostgREST reader/writer in either repository,
  `OB1/integrations/agent-memory-api/index.ts`, appears in **no compose file** - it is a Supabase
  Edge Function, not deployed here;
- 180 left it wide to keep the `access_refused` rows readable as drill evidence, and **both**
  readers of that evidence - `scripts/checks/smoke-agent-memory.ps1` and
  `scripts/checks/dfu-done.ps1` - reach it with `docker exec ... psql` as `postgres`. The reason
  180 gave is not a reason that involves this door.

## R3 - transitivity: the walk was one edge deep

Section 6b joined `pg_rewrite` to `pg_depend` **once**, so it saw a matview sitting directly on a
table and nothing else. One ordinary view in between and the claim in the file - "a materialized
view over a force-RLS relation is refused outright rather than skipped" - was true only of
adjacency. Measured at the round 2 file:

```
CREATE MATERIALIZED VIEW u5g3_transitive_mv AS SELECT * FROM ideas_owed_research;
   (ideas_owed_research JOINs idea_revisions, which round 1 governs and forces)
re-apply the round 2 migration       -> COMMIT, "post-conditions hold on 9 table(s)"
SET ROLE service_role;
  SELECT ... FROM u5g3_transitive_mv  -> 1 personal / 1 ops     <- LEAK
  SELECT ... FROM ideas_owed_research -> 0 personal / 1 ops     <- the view is bound
```

**Fixed** with a recursive `pg_depend`/`pg_rewrite` walk, in section 6b and again as section
7(l), which also covers an owner-rights view in **any** schema, not only `public`.

## R4 - the one the assertion found, not the schema read

`agent_memory_recall_traces` carried `USING (ob_trace_on_ops_plane(request_payload))
WITH CHECK (true)` from 180, with the comment *"WITH CHECK stays open so that writing a trace
never fails"*. An unconditional `WITH CHECK` is an arm that permits on absence written shorter,
on a table that holds the caller's recall **query text**, next to a unique `request_id` no policy
binds. Nobody had read it as an absence arm because it does not look like one; section 7(h)
found it on its first run. Narrowed to the `USING` predicate -
`ob_trace_on_ops_plane(NULL)` is FALSE, so absence denies with no extra arm.

## The fix is one change, and section 7 now asserts properties

**A row whose plane cannot be established is not visible and is not writable, and nothing
reaches a governed relation around the boundary.**

Round 1 swept tables. Round 2 swept relkinds. The leaks came from **mechanisms**: unique
indexes, foreign-key triggers, `ON DELETE SET NULL` and transitive dependency. So section 7
stopped re-deriving a list of the state and now asserts, over a **derived** population:

| | property | how it is derived |
|---|---|---|
| (h) | no policy arm permits a row whose every column is NULL | each `pg_policies.qual` / `.with_check` is EXECUTED against `(NULL::public.t).*` |
| (i) | no unique constraint on a governed relation is an existence oracle | safe only if its columns contain the plane columns (from `pg_depend`'s policy-to-column edges), **or** no door role holds INSERT/UPDATE, **or** every column is a uuid defaulted to `gen_random_uuid()`; anything else must carry an `ORACLE-DISPOSITION` comment |
| (j) | no FK into a governed parent is unguarded | every referencing column must appear in the `WITH CHECK` of every permissive write policy on the child |
| (k) | the `SECURITY DEFINER` set is exactly the classified one | `pg_proc.prosecdef` in `public` minus the two section 4 accounts for |
| (l) | nothing reaches a FORCE-RLS table around the boundary | recursive `pg_rewrite`/`pg_depend` walk, matviews and non-invoker views, any schema |

And the write door closes on what nothing writes through it (section 6a): `INSERT`, `UPDATE`
and `DELETE` withdrawn from `service_role` on the **derived** agent-memory corpus
(`agent_memories`, its FK children, and their governed parents) and on `idea_revisions`.
`SELECT` is untouched everywhere. A caller that cannot write cannot provoke a unique index or a
foreign-key trigger, which is the same defence applied at the privilege layer instead of one
predicate at a time.

**The two oracles this file does NOT close, written down rather than left silent.**
`thoughts_pkey` and `thought_edges_pkey` are surrogate keys no policy mentions on tables the
door must be able to write, so `23505`-versus-success is an existence oracle over an id. The fix
is to withdraw the caller's ability to NAME the column - `REVOKE INSERT` then
`GRANT INSERT (every other column)`, because a table-level grant subsumes column grants - and
that breaks, silently and at runtime, every `service_role` writer of any column added later.
Columns **are** added here (`init-agent-memory-embedding.sql` adds one), so this is a live
hazard rather than a hypothetical. Both carry an `ORACLE-DISPOSITION` COMMENT in the database
saying so; deleting those two statements from section 6a2 turns section 7(i) RED, measured
(adversarial case G).

## A defect this round created and caught, worth more than the ones it fixed

The first version of section 6a derived the agent-memory corpus as "agent_memories, its FK
children, and their governed parents" with no exclusion. While testing adversarial case D, a
probe added `u5g3_mem uuid REFERENCES agent_memories(id)` to `thought_entities`. That made
`thought_entities` a child, and the parent arm then reached `thoughts` - so the next apply
**stripped INSERT/UPDATE/DELETE from `thought_entities` and `thoughts`**, the entity worker and
the ingestion path, silently, with a COMMIT.

It surfaced only because adversarial case D **failed to fire** and I went looking for why - the
gate had already revoked the privilege its own predicate reads. Two changes came out of it:
children that carry a foreign key into `thoughts` are excluded from the corpus (they belong to
the corpus sections 2 and 3 govern, which has a live write path), and section 6a now carries a
**gate on the gate** - if the derivation ever reaches a relation whose write path this file
deliberately preserves, it RAISES instead of revoking. Adversarial case E fires it.

## A live-versus-fresh drift the mechanism gates made visible

The uniqueness and FK gates read **grants**, so they behave differently in the two places the
two-place invariant covers. A fresh volume built from the 28-file chain gives `service_role`
only `SELECT` on `thoughts` and `thought_entities`; **production gives it the full
DELETE/INSERT/SELECT/UPDATE set.** Nothing in the chain grants those, so they were granted to
the live database by something outside it.

A GREEN measured on a fresh volume would therefore not have transferred: on the throwaway,
`thoughts_pkey` escapes gate (i) because the door cannot write the table, while on production it
does not. **The validating run replicates production's grants onto the throwaway before it
starts**, and the run prints them so the substitution is visible rather than assumed. The drift
itself is open item 7 below.

## The evidence

RED before GREEN on a genuinely fresh throwaway (`pgvector/pgvector:pg16`, container
`wt-u5g3f-db`, own network `wt-u5g3-net`, never attached to an `ai-stack_*` anchor network),
built from the initdb chain **derived from `OB1/docker/docker-compose.yml`** with the staged
count asserted against the compose count (**28 mentions = 28 pairs = 28 staged**), synthetic
fixtures only. The RED baseline is the round 2 file **verbatim from git** (`OB1` `53f0880`,
byte-compared before use).

| probe | RED (round 2 file) | GREEN (round 3) |
|---|---|---|
| R1a `idea_revisions` insert, existing personal revision | **23505** | `42501` |
| R1b same, absent revision | **OK** | `42501` |
| R1c ops CONTROL, existing revision | 23505 | `42501` |
| R1d ops CONTROL, absent revision | OK | `42501` |
| R1e `idea_revisions` read | 0 personal / 1 ops | 0 personal / 1 ops |
| P1f orphan revisions (`thought_id` NULL) visible | **4** | **0** |
| R2 audit rows, live parent | 0 personal / 1 ops | 0 personal / 0 ops |
| R2 audit rows, **orphaned** parent | **1 personal** / 1 ops | **0** personal / 0 ops |
| R3 matview one view from `idea_revisions` | **1 personal** / 1 ops | migration REFUSES |
| P2b audit write through the door | (open) | `42501` |
| P4a recall-trace write through the door | (open) | `42501` |
| P5a `thoughts` / P5b `ideas_owed_research` | 0 personal / 1 ops | 0 personal / 1 ops |

**The controls are positive and they are named.** `C1` (superuser sees 1 personal / 1 ops audit
rows) proves the fixtures exist; `C2` the same for `idea_revisions`; `C3` that the real writer
still writes. For the **closed** audit door both arms read 0 by design, so the control that the
door itself is alive is `P5a` in the same session on the same connection - it returns the ops
thought. A door that returned nothing anywhere would be indeterminate, not a pass.

**The policy closes the oracle on its own**, independently of the withdrawn grant - measured by
re-granting `INSERT` inside a transaction that is rolled back:

```
Q1a omit thought_id, existing personal revision  -> 42501 row-level security
Q1b omit thought_id, absent revision             -> 42501 row-level security
Q1c a HIDDEN thought_id named                    -> 42501 row-level security
Q1d a VISIBLE ops thought_id CONTROL             -> OK
Q2a trace write with no enforced_exposure        -> 42501 row-level security
Q2b trace write, enforced_exposure ops CONTROL   -> OK
```

**Ops is unbroken**, exercised as `service_role` in the entity-extraction worker's own shape and
rolled back: the queue row is visible, claimed, an entity written, a `thought_entities` row
written, an `edges` row written, the queue marked done, a second ops thought written, and
`thought_edges_upsert` - now INVOKER - called successfully. `W9` proves the same rpc still
REFUSES an edge with a hidden endpoint (`42501`).

**Nine adversarial cases, each RED for its own reason:**

| | case | caught by |
|---|---|---|
| A | a NULL-permitting arm re-introduced on a 180 policy | 7(h) |
| B | a new unique constraint with no disposition | 7(i) |
| C | a new `SECURITY DEFINER` function | 7(k) |
| D | an unguarded FK into a governed parent | 7(j) |
| E | the corpus derivation reaching a table whose write path is preserved | 6a's gate on the gate |
| F | a new FORCE-RLS table with a wide policy and **no** FK into the corpus | 7(h) over the derived population |
| G | the two `ORACLE-DISPOSITION` statements deleted from the file | 7(i) |
| H | a matview one view away from a governed table | 6b, transitively |
| I | a brand-new owner-rights view over `idea_revisions` | 6b's sweep: 1 personal -> 0, ops control 1 |

**Idempotence:** three consecutive applies COMMIT. **Revert round trip:** GREEN -> revert ->
every RED returns (the oracle answers `23505`, the orphan audit row reappears, the four orphan
revisions become visible again, the trace write succeeds) -> re-apply -> GREEN. **Full chain on
a genuinely fresh volume:** 28 files, no errors, all three NOTICEs. **`test-quartz4-offline.ps1`:
ALL OFFLINE CHECKS PASSED**, including the compose-derived chain and the agent-memory review
suite that writes four audit events (as `postgres`, which is why the write withdrawal does not
touch it).

**Nothing was applied to the live database.** Production still runs round 1 and therefore still
carries every finding round 2 and round 3 fix. The two-place invariant is carried by the new
`PROMOTION-RUNBOOK.md` section, whose verify-by-query blocks were **executed** against the
throwaway rather than written.

## C.7b discipline, and the arbiter's verdict

**Rebased first, re-run after, sha recorded.** The work line moved by one documentation commit
(`b618591`, C.8 clause 8) during the round; the branch was rebased onto it **before** the
validating run.

| what | sha |
|---|---|
| work line rebased onto | `b6185915980462dcb65e189b96770db214f26751` |
| `work/u5graph` after rebase, **the sha the suite ran at** | `6531459c766af21be78d04dec427afc520547add` |
| OB1 commit the gitlink pins | `49d2e3d12f36f327b40fd191b1ed67cf3e646a8e` (pushed to `origin/work/u5graph-plane-rls` BEFORE the bump) |
| OB1 commit the line pinned before | `53f0880748555b2bdb970b7d5365f1d86c9d077c` |
| round 2's RED baseline, byte-compared from git | `53f0880:docker/init-graph-plane-rls.sql` |

One checkout (`.claude/worktrees/wt-u5graph`), one suite. The live plane was held under an
`open-brain` lease for the `dfu-done` run and released after it.

### `dfu-done.ps1 -Only 3` - the arbiter, run at `6531459`

**Verdict: clause 3 UNMET, board FAILED.** Reported, not worked around. The four subjects that
hold it open are the **same four as round 2**, and none is caused by this round:

| subject | verdict | why |
|---|---|---|
| `door-openbrain-mcp-door` | **fail** | returns the personal fixture; connects as `postgres` (rolsuper/rolbypassrls = t/t) |
| `door-cloud-search-thoughts` | **fail** | returns the personal fixture |
| `door-wiki-compiler-output` | indeterminate | named manual check, no recorded result |
| `door-mcp-read-tools` | indeterminate | returned neither the fixture nor its ops twin - with no positive control it refuses to call itself a pass |

Both failures are round 1's open item 1 verbatim: RLS binds no superuser. What the run **does**
confirm is that round 3 broke nothing - every automated door and predicate probe still passes
with a live positive control, and `corpus-predicate-source-on-work-line` passes citing the new
pin `49d2e3d`. And the decisive point is unchanged from round 2: **clause 3 measures the LIVE
database, and the live database still runs round 1**, so it cannot reflect this round either way.

**Class 4 end state**, verified after the run: production shows `thoughts_personal=0`,
`memories_personal=0` against 13,001 thoughts and 21 memories; no `DFU-DONE-` or `U5G3` fixture
row remains in either corpus; both throwaways and their network removed (`0` leftover
containers, `0` leftover networks); the migration drops no table, column, view or row.

## Open items - rounds 1 and 2's stand, plus three

7. **The live database has grants the initdb chain does not give.** `service_role` holds
   `INSERT/UPDATE/DELETE` on `thoughts` and `thought_entities` in production and only `SELECT`
   on a fresh volume built from the same 28 files. Nothing in the chain grants them. That is the
   two-place invariant failing in the **privilege** dimension, and it is exactly the dimension
   this round's gates read, so it changes what those gates conclude. Not fixed here: deciding
   which of the two is correct is a separate call (production's grants may be load-bearing for
   the ingestion path, in which case the chain is what is wrong).
8. **`agent_memory_recall_items` still discloses cross-plane existence on the read side.** Its
   policy is `ob_memory_visible(memory_id)` with no arm for `trace_id`, so an item naming an
   **ops** memory is visible even when the trace that returned it is personal - "this ops memory
   was returned in a recall you cannot see". Measured live: 0 of 75 traces are on the ops plane,
   so all 25 items would become invisible if the arm were added, and no consumer of that
   visibility could be found. Left alone deliberately rather than narrowed blind; it is 180's
   table and the change wants its own measurement of who reads it. The **write**-side oracle on
   the same table is closed by section 6a.
9. **`agent_memory_report_usage` writes an audit event and never sets
   `agent_memory_recall_items.used`.** Not this item's defect - `recall-sibling-class.ps1` prints
   the same warning - but it is the reason clause 8's evidence has to be read out of two places.
10. **`wiki_pages` drifts and was in a column of identity checks.** Round 1 recorded `47987`; it
   measured `47981` on 2026-08-31 and `47982` an hour later, with nothing having touched it,
   because the compiler writes continuously. It has been moved out of the ops-path identity
   block in the runbook and is now read separately and labelled - a member that moves on its own
   weakens every member that does not.
