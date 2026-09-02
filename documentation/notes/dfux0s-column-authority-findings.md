# Findings — item `dfux0s`, "make the boundary read the column H3 made authoritative"

2026-09-02. Things this item turned up that are TRUE but belong to something else, plus the two
claims in its own brief that the measurement did not support. Written here rather than into the
deliverable, per CLAUDE.md's findings rule.

Every line below was checked by reading the file or running the command named. Nothing was
applied to the live database and no image was rebuilt; the only live access was read-only
catalogue and census queries.

---

## 1. The brief's part (a) was already done in code. What is owed is a REBUILD, not a commit.

The brief says the MCP write path must be changed to stamp both halves, and that the change must
be pushed to OB1 and the gitlink bumped. **It is already stamped at the pinned SHA `b604d55`**,
by OB1 commit `921d866` ("agent-memory: exposure becomes a typed column, and the predicates read
it"). Read, at the pinned tree:

| Site | Writes |
|---|---|
| `integrations/kubernetes-deployment/index.ts:884` (`capture_thought`) | `JSON.stringify({ ...meta, exposure: CORPUS_EXPOSURE })` **and** `CORPUS_EXPOSURE` into the column |
| `…/index.ts:982` (`capture_idea`) | both |
| `…/index.ts:1052` (`update_idea`) | both |
| `…/agent-memory.ts:267` (writeback thought) | column `row.exposure`, mirror `{…, exposure: row.exposure}` |
| `…/agent-memory.ts:292` (writeback memory) | column `row.exposure`; the mirror comes from `buildWritebackRow`'s `metadata: { ...(input.metadata ?? {}), exposure }`, written from the STAMPED value after the spread, so a caller cannot smuggle one in |
| `…/agent-memory-ops.ts:165` (the **UPDATE** path, `promote_exposure`) | both, in ONE statement off ONE parameter — the two `sets` entries are pushed before the single `args.push`, so they share an index and cannot desync |

There is no other `INSERT`/`UPDATE` against `thoughts` or `agent_memories` in the MCP (swept
`grep -n "INSERT INTO\|UPDATE \|DELETE FROM" *.ts` across the whole `kubernetes-deployment`
directory). So **no OB1 code change was made and no gitlink bump was needed.**

## 2. The orchestrator's measured defect is right in its conclusion and wrong in its detail

The brief states "`openbrain-mcp` writes the **column** and not the mirror". The deployed image
writes **neither**:

```
$ docker image inspect openbrain-mcp-server:local --format '{{.Created}}'
2026-08-30T16:43:37Z
$ docker exec openbrain-mcp grep -n 'INSERT INTO thoughts' -A3 /app/index.ts
869:  `INSERT INTO thoughts (content, embedding, metadata)
870-   VALUES ($1, $2::vector, $3::jsonb)`,
871-  [content, embStr, JSON.stringify(meta)]      # meta has no exposure key
```

The conclusion is unaffected — under a bound role that write is refused by the mirror-reading
`WITH CHECK`, which is what was observed. But the difference matters for the fix: the door does
not need a code change, it needs its **image rebuilt**, and it will *also* fail the new NOT NULL
constraint, not just the RLS check. Diagnosing from the source tree instead of the container
would have produced the opposite verdict ("nothing to do").

**The general shape, which is why this is a finding and not a nit:** the registry records what a
producer's SOURCE does. Bind-mounted producers (`openbrain-gmail-pull`, `openbrain-wiki`'s
`/recipes`, the import recipes) track the tree; baked ones (`openbrain-mcp-server:local`,
`openbrain-wiki:local`) do not, and nothing distinguishes them in a source read. Added as
registry hardening rule 10.

## 3. A LIVE containment hole: `upsert_thought`'s UPDATE branch republishes personal rows

Not hypothetical, not introduced by this work, open on production right now. The live body
(read from `pg_proc.prosrc`) is:

```sql
UPDATE public.thoughts SET metadata = metadata || v_meta, updated_at = now() WHERE id = v_row.id
```

It merges the caller's metadata — including an `exposure` key — and never touches the column.
With the mirror authoritative, re-upserting an existing **personal** thought with
`{"metadata":{"exposure":"ops"}}` moves it onto the ops plane. Reproduced on a throwaway built to
the live shape:

```
after_upsert col=personal mirror=ops
personal_row_now_visible_to_ops_plane=1
```

`recipes/entity-wiki/generate-wiki.mjs` is one of its callers, over PostgREST rpc. The 1,129
personal rows are email content with `note_path`-free metadata, so the wiki compiler is not
currently a path to it — but the door is open to anything that can call the rpc as
`service_role`, which is `PGRST_DB_ANON_ROLE`, i.e. every unauthenticated caller on
`open-brain_obnet`.

The 205 migration closes it from both sides (authority moves to the column; the door writes the
mirror FROM the column). **Until it is applied, this is live.** It is recorded here as well as in
the migration because it is a reason to promote, not merely a consequence of promoting.

## 4. `openbrain-chunk-worker`'s registry row was wrong

It says the worker writes `thoughts (chunks + embeddings)`. Its only INSERT is
`integrations/chunk-embedding-worker/index.ts:210` → `public.source_chunks`. Corrected in the
registry. Consequence: it is **not** a corpus producer and is unaffected by the exposure
contract — but `source_chunks` derives from `sources` and carries content, so it is squarely
inside hardening rule 6 the day `sources` is governed.

## 5. `openbrain-wiki`'s baked note-ingest is already failing the CURRENT contract, silently

`docker exec openbrain-wiki grep -n thoughts /app/wiki-service.mjs`:

```
471:  await obFetch("PATCH", `thoughts?id=eq.${id}`, { content, metadata: meta });   # meta has no exposure
473:  await obFetch("POST",  "thoughts", { content, metadata: meta });               # neither half
```

Under U5's live mirror-reading `WITH CHECK` this POST is a 42501 today, and the PATCH deletes
the mirror on an existing row. It has not fired: `notes ingested: 0 upserted, 0 deleted` on
every cycle for the last 72h of logs, because no vault note changed. So it is **latent, not
firing** — the first note edit is the first failure, and its `catch` logs and continues. The
pinned tree fixes both lines; the image needs rebuilding regardless of this migration.

## 6. `scripts/checks/test-quartz4-offline.ps1` has been red for six checks since 195 landed

Baselined both ways, same 6 failures with and without this item's change:

```
worktree (30 migrations): 6 CHECK(S) FAILED   exit 6
main checkout (29)      : 6 CHECK(S) FAILED   exit 6
```

Cause: the script's own agent-memory probes `INSERT INTO agent_memories (...)` without stating
`exposure`, and `195-` makes that column NOT NULL on a fresh volume. Every failure message is
`null value in column "exposure" of relation "agent_memories" violates not-null constraint`.

So the check is not detecting a defect; it is failing its own precondition, and it has been
doing so since 195 was mounted. Three of the six are *inverted* (`an invented memory_type was
ACCEPTED - the constraint is gone, not widened`), i.e. the report is actively misleading about
what is wrong. The chain-derivation and preview-parity checks in the same script still pass and
do cover this item's compose change (`30 migrations`, `preview compose carries the same initdb
chain as production`).

**Not fixed here** — `scripts/checks/` is outside this item's ownership, and the fix is to add
`exposure` to the script's probe INSERTs, which is somebody's five-minute change with a real
green/red to show for it.

## 7. The runbook's prose about 195's mirror scan understates the code

`PROMOTION-RUNBOOK.md` says 195 §8(d)'s "anchors are the literal `metadata->>'exposure'` and
`on_ops_plane(metadata)`". The code has **five** anchors including `on_ops_plane(NEW.metadata)`
and `on_ops_plane(OLD.metadata)`. This matters because the live gate's form is
`ob_corpus_on_ops_plane(NEW.metadata)` — a reader who trusted the prose would conclude the scan
could not see the one function it was written for, and would be wrong. Doc-only; the two-anchor
list is reproduced verbatim in the 205 file's comment as the *wrong* version to warn against, so
the corrected fact is now recorded in code as well.

## 8. Slot 210 was already spoken for

The brief says "a new migration under `OB1/docker/`, slot ABOVE 200". 210 and 215 are reserved
by H1's staged `init-app-role.sql` / `init-app-role-passwords.sh` (documented in
`H1-APP-ROLE-PROMOTION.md`, and staged at those numbers by
`scripts/checks/drill-app-role-not-superuser.ps1` and `drill-mcp-door-not-superuser.ps1`).
`drill-rls-boot-assertion.ps1` also stages synthetic fixtures at 210 and 220. **205** was taken
instead: above 200, sorts unambiguously (equal width, digits only), free, and it encodes the
dependency — the boundary must read the column BEFORE the door stops being a `bypassrls`
superuser, which is precisely the order the 2026-09-02 promotion got wrong.

## 9. Open, and deliberately not touched

- **`wiki_pages`** — operator-parked. Not read, not migrated. It has no exposure column and the
  205 migration does not name it.
- **The 1,129 personal rows** — not deleted, not reclassified, not counted differently. Verified
  present and self-consistent (`col_personal=1129 mirror_personal=1129`) before and after every
  throwaway run; the live database was only ever read.
- **`entity_extraction_queue` rows for thoughts demoted before the trigger was widened** — the
  205 migration widens `trg_queue_entity_extraction` so a future column-only demotion evicts the
  fingerprint, but it does NOT sweep fingerprints of rows demoted while the narrow trigger was
  in force. **Measured live, read-only, 2026-09-02: 0.**
  `SELECT count(*) FROM entity_extraction_queue q JOIN thoughts t ON t.id = q.thought_id
   WHERE t.exposure IS DISTINCT FROM 'ops';` -> `0` (queue total 13,011, orphan rows 0, i.e.
  exactly the 13,011 ops thoughts and none of the 1,129 personal ones). So there is nothing to
  sweep today - but the absence is a fact about the current data, not a property, and it is
  the widened trigger in section 6 of the migration that keeps it true.
- **`claims` / `claim_sources`** — still `USING(true)`. Governing them repeats the gmail outage
  unless the research producers stamp first (registry rule 1/2). Unchanged by this item.
