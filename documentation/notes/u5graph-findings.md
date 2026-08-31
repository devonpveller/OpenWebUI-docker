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
`documentation/implementation-guide/dark-factory-unification/PLAN.md` only, and no measurement
here reads it. **That is a note for the reviewer, not a dispensation** - a clean
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
   work line**. Until that lands, the published wiki has no plane filter in the deliverable.
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
