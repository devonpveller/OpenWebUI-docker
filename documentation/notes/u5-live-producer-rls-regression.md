# LIVE REGRESSION: U5's `thoughts` ops-plane policy refuses every direct-POST producer

**Found** 2026-08-31, during H3 round 2 adjudication. **Cause: work already MERGED (U5), not
the branch under review (`work/u8h3`).**

**Status 2026-08-31, round 3: FIXED IN THE TREE, NOT YET IN PRODUCTION.** Every direct
producer now states its plane, a derived pre-commit gate keeps the set honest, and the
producer table below has been corrected — it was wrong in both directions. Nothing was written
to the live database. See "Round 3" at the bottom, and the deploy note: the live outage does
not end until the deployment checkout's OB1 submodule moves **and** `openbrain-wiki:local` is
rebuilt.

## The symptom, measured

`openbrain-gmail-pull`, scheduled daily, run at `2026-08-31T04:59`:

```
Found 293 messages.  Processed: 1   Ingested: 0 email(s) / 0 chunk row(s)   Errors: 1
  reason: chunk 1/6: HTTP 401 {"code":"42501",
          "message":"new row violates row-level security policy for table \"thoughts\""}
```

Sweep of all 27 running `openbrain-*` containers over 40h: `openbrain-gmail-pull` is the only
one reporting `42501`. The other producers are **latent, not safe** — they simply have not run
since the policy landed.

## The mechanism

Live `thoughts` has `relrowsecurity=t`, `relforcerowsecurity=t`, and for `service_role`:

```
thoughts_ops_plane  *  USING/WITH CHECK: ob_corpus_on_ops_plane(metadata)
   ob_corpus_on_ops_plane(md jsonb) := SELECT md->>'exposure' = 'ops'
```

A POST that omits `exposure` yields `NULL = 'ops'` → NULL → not true → refused. There is **no
BEFORE-INSERT trigger that stamps `exposure`** (the three triggers on `thoughts` are
fingerprint, entity-extraction queue, and delete-touch). So the WITH CHECK can only be satisfied
by the producer sending the key.

## Why it looked fine until today

`gmail` rows carry `exposure='ops'` for every day 08-20 → 08-30, which reads like a producer
that states its plane. It is not: **`pull-gmail.ts` contains zero occurrences of `exposure`.**
Those values came from U5's **backfill of pre-existing rows**. The backfill stamped history, the
policy then bound the future, and the first scheduled run under the policy failed. The green
history is an artifact of the migration, not evidence about the producer.

## The producer set, swept properly

`grep -rln "rest/v1/thoughts" OB1 --include=*.ts --include=*.mjs --include=*.js --include=*.py`
→ **ten files, and `grep -c exposure` is 0 in all ten**:

| file | service |
|---|---|
| `recipes/email-history-import/pull-gmail.ts` | `openbrain-gmail-pull` — **failing daily now** |
| `recipes/email-history-import/prune-short-term.ts` | `openbrain-gmail-prune` — latent |
| `recipes/daily-digest/src/clients/postgrest.ts` | `openbrain-digest` — latent |
| `recipes/thought-enrichment/enrich-thoughts.mjs` | latent |
| `recipes/source-filtering/backfill-metadata.ts` | latent |
| `recipes/chatgpt-conversation-import/import-chatgpt.py` | one-shot |
| `recipes/obsidian-vault-import/import-obsidian.py` | one-shot |
| `recipes/perplexity-conversation-import/import-perplexity.py` | one-shot |
| `recipes/google-activity-import/import-google-activity.mjs` | one-shot |
| `recipes/local-ollama-embeddings/embed-local.py` | one-shot |

## What this falsifies

`init-agent-memory-exposure-column.sql` §7 claims *"Every caller of this rpc in the tree was
found and given an explicit `exposure: ops`."* True of the **ten RPC callers**; the sweep was
`grep -rn 'rpc("upsert_thought"' OB1`, so the **direct-table producer set was never searched
for**. The post-condition presents the RPC callers as the producer set. It is not.

The same alphabet error as A2's `.ts`-only scan root: **the search term defined the finding.**

## Data loss

None permanent. The failed email is not recorded as ingested (dedupe is by presence of the row),
so tomorrow's run retries it. The cost so far is one day of digest latency.

## OPEN DECISION FOR THE OPERATOR — do not let a fix settle this silently

Making these producers send `exposure: 'ops'` restores exactly the status quo of 08-30 (all 156
gmail rows are already `ops` via the backfill). **But it asserts that the mail corpus belongs on
the ops plane, where agents read it** — and the discovered labels include `brain/keep-short-term/PDNAS`,
`brain/keep-short-term/Y-12/travel` and `brain/keep-short-term/Cozy Kidz Academy`, which do not
read as ops material. The backfill already made this choice for 156 rows without it being stated
as a decision. Conforming to it is reversible and widens nothing **relative to yesterday**; it is
still a personal-plane classification call and it is §1.1's to make, not a fix's.

---

## Round 3 — what the fix found, and where this note was wrong

### The producer table above is wrong in BOTH directions

It was derived from `grep -rln "rest/v1/thoughts"`, and **that literal is itself a search term
that defines its finding** — the same error one layer down from the one the note is about. Two
kinds of mistake came out of it:

**Four of the ten are not producers at all.** They only read, patch or delete, so the NOT NULL
column and the ops-plane `WITH CHECK` never touch them:

| listed as latent | what it actually does |
|---|---|
| `recipes/daily-digest/src/clients/postgrest.ts` | `GET /rest/v1/thoughts?…` only; its POSTs go to `rpc/…` |
| `recipes/email-history-import/prune-short-term.ts` | `GET` with `?select=`, `DELETE` with `?id=in.` |
| `recipes/source-filtering/backfill-metadata.ts` | `GET` with `?select=`, `PATCH` with `?id=eq.` |
| `recipes/thought-enrichment/enrich-thoughts.mjs` | `GET`, and `PATCH` with `?id=eq.` |

**And two real producers were missing**, because neither writes the literal the grep looked
for:

| missed producer | why the grep could not see it | why it matters |
|---|---|---|
| `docker/wiki-service/wiki-service.mjs:473` | builds the path as `obFetch("POST", "thoughts", …)` — no `rest/v1` in the string | **`openbrain-wiki`, the only other scheduled corpus producer.** Note ingest, caught per-file, so a refusal degrades silently rather than failing the run |
| `recipes/schema-aware-routing/index.ts:298` | `supabase.from("thoughts").insert()` — not a URL at all | no REST-path sweep of any spelling would ever have found it |

So the corrected producer set is **twelve**, of which one (`backfill-gmail-wikis.mjs`) and one
(`adaptive-capture-classification/capture-with-gating.ts`) already stated their plane from
round 2, and **eight** were fixed in round 3. Full list and per-site status:
`powershell -File scripts/checks/check-corpus-exposure-producers.ps1`.

### The fix

Each producer states the plane **at its own call site**, both halves:

```
{ content, embedding, metadata: { ...metadata, exposure: "ops" }, exposure: "ops" }
```

The **column** is what the H3 policies read once `195-` is applied; the **`metadata` mirror** is
what the *currently deployed* U5 policy reads (`ob_corpus_on_ops_plane(metadata)`), and it is the
half that ends the live 42501. `wiki-service.mjs` carries `exposure` inside its `meta` object
rather than only at the POST, because that same object is the body of the idempotent PATCH, which
replaces `metadata` wholesale — without it, every edited note would silently lose its mirror.

### `'ops'` conforms; it does not resolve the open decision

Measured on the live database, 2026-08-31, read-only:

```
SELECT metadata->>'exposure', count(*) FROM thoughts GROUP BY 1;   ->  ops | 13001
SELECT exposure,              count(*) FROM thoughts GROUP BY 1;   ->  ops | 13001
```

**Every** row of the live corpus is already on `ops`, in both the column and the mirror — not
just the 156 gmail rows. So conforming restores 08-30's status quo exactly and widens nothing.
**The open §1.1 decision above stands unchanged**: where the mail corpus belongs is the
operator's call, and none of these code changes make it. Nothing was written to the live
database in this round.

### The gate, which is authoring-time convenience and NOT the enforcement

> **CORRECTED, ROUND 4 (2026-08-31).** This section was headed *"so producer thirteen breaks the
> build instead of production"*. **That is FALSE**, and false in the same shape as the sweep it
> was written to correct: it presented what a search could see as what exists. Two verifiers
> independently planted producers in a temp root and the gate did not flag them, did not warn
> about them, and **did not even count them as sites** — a table name held in a variable, a
> concatenated path, a helper wrapper (`insertRows("thoughts", rows)`), byte-identical copies
> named `.mts` and `.tsx`, `curl -X POST "$SUPABASE_URL/rest/v1/thoughts"` in a `.sh`, and
> supabase-py `.table().insert()`. **Those verdicts were measured at `819b5fe`** — see the
> round-6 dating at the end of this block, which re-measures every one of them.
>
> **THE ENFORCEMENT IS THE DATABASE.** `195-` makes `thoughts.exposure` and
> `agent_memories.exposure` NOT NULL with no default and CHECKed, and makes `upsert_thought`
> refuse a payload that omits them. That refuses an unlabelled write in every shape, from every
> language, forever. This gate moves *some* of those refusals to commit time and nothing more.
>
> **Producer thirteen, written in a shape the gate cannot see, breaks PRODUCTION** — and per
> §16 of `u8h3-findings.md` it breaks it **quietly**, because both producers that were failing
> catch the 42501 and carry on. `openbrain-gmail-pull` logged `Ingested: 0 email(s)` and exited
> 0 for a day. Fail-closed is not fail-visibly.
>
> The fix was **not** six more patterns — that is the enumerate-the-readers method A2 abandoned,
> and the seventh evasion still wins. The gate now **states its own blind spots in its own
> output on every run** (`-ShowShapes` prints them alone), so a reader learns its scope from the
> run rather than from its author's confidence. The alphabet widened too — `.mts .cts .tsx .jsx
> .sh .bash`, supabase-py `.table()`, `curl -X POST`, and any identifier containing
> `post`/`insert` called as a function — not as the fix, but because a cheap catch is worth
> having once the claim is honest.
>
> **DATED AND RE-MEASURED, ROUND 6 (2026-08-31).** Everything above describes the gate at
> `819b5fe`; the widening it announces landed in `c192041`, in the *same commit* as the
> paragraph, so the paragraph was already history when it shipped and then stood for two rounds
> as a live claim. Re-measured at `5c81f97`, one unlabelled fixture per shape, with the
> historical blobs run against the same fixtures:
>
> | planted evasion | `819b5fe` | `5c81f97` |
> |---|---|---|
> | `insertRows("thoughts", rows)` | 0 sites | **FLAGGED**, 1 of 1 |
> | `obPost("thoughts", rows)` | 0 sites | **FLAGGED**, 1 of 1 |
> | byte-identical `.mts` copy | 0 files scanned | **FLAGGED**, 1 of 1 |
> | byte-identical `.tsx` copy | 0 files scanned | **FLAGGED**, 1 of 1 |
> | `curl -X POST ".../thoughts"` in a `.sh` | 0 files scanned | **FLAGGED**, 1 of 1 |
> | supabase-py `.table("thoughts").insert(rows)` | 0 sites | **FLAGGED**, 1 of 1 |
> | `fetch(BASE + "/" + "thoughts", …)` | **FLAGGED** | **FLAGGED** — layout, see below |
> | `const TABLE = "thoughts"` + `` `${BASE}/${TABLE}` `` | 0 sites | 0 sites — layout, see below |
>
> **This round's earlier tally — "the four that write the table name as a literal beside a verb
> are now caught; the two that hold it in a value are not" — was itself wrong**, in both halves.
> Six are caught, not four. And only **one** evasion holds the table name in a value: the
> concatenated path writes `"thoughts"` as a literal, so it was caught even at `819b5fe`.
>
> **The last two rows are ONE case and neither is coverage.** The gate resolves no values, so it
> sees either only when the literal happens to land within `$argWindow` (2) lines of a
> post/insert verb. Measured both ways at `5c81f97`: the concatenated path is **FLAGGED** with
> the verb on the next line and reports **`ZERO SITES`** with five lines between them; the
> variable-held name is **FLAGGED** adjacent and **`ZERO SITES`** at three lines. Same producer,
> same file, opposite verdict, decided by whitespace.
>
> **None of which changes the heading above it.** A wider alphabet moves the boundary; it does
> not remove one. Producer thirteen in an unrecognised shape still breaks production.

`scripts/checks/check-corpus-exposure-producers.ps1`, wired into `.githooks/pre-commit` as check
3b. Within the shapes it recognises it **derives** the insert sites rather than carrying a list;
each shape has its own proximity rule:

| shape | example | rule |
|---|---|---|
| REST URL | `` `${SUPABASE_URL}/rest/v1/thoughts` ``, `` `${REST_BASE}/thoughts` `` | anchored on `/rest/v1/` or a base variable's `}/`; a `?`-filtered path is a read, not an insert |
| POST argument | `obFetch("POST", "thoughts", body)`, `sb.post(\n "thoughts", …)` | table literal within 2 lines of a POST verb; `{"thoughts": []}` (a JSON key) is excluded |
| supabase-js / -py | `.from("thoughts").insert({…})`, `.table("thoughts").insert(…)` | `.insert(` must be in the SAME statement — the slice runs forward from the builder and stops at the first `;` |

And the **evidence** for `exposure` is scoped to the statement **in the ORM shape** — see the
next subsection for why that sentence had to be written, and read the qualifier as load-bearing.
**URL and ARG sites are still read over a ±30-line window**, clipped at neighbouring
different-table sites; only the ORM shape replaces the window with the builder-to-`;` slice.
Round 5 measured the difference: a `thoughts` POST stating its plane above an `agent_memories`
POST stating none still **clears** it at 0, 3, 10 and 25 lines of separation, because the fence
clips at the donor's *site line* and the donor's key sits in its body below that line. An
earlier version of this sentence said "scoped to the statement" without the qualifier and was
read as covering all three shapes.

**And whatever the scope, the test inside it is a TEXT MATCH.** Any occurrence of the key that
is not that statement's own plane declaration can clear a counted site — type annotations,
sibling objects, string literals, SQL text and comment continuations are measured instances, and
the list is illustrative rather than exhaustive. The gate prints that category on every run.

#### The gate was green for the wrong reason on the one `agent_memories` site

`OB1/integrations/agent-memory-api/index.ts:491` is the **only `agent_memories` INSERT in the
tree**. It carried neither `exposure` nor `metadata.exposure` — and it **passed**, cleared by the
`exposure: "ops"` key at `:471`, which belongs to the `upsert_thought` RPC payload for a
*different table*, twenty lines up and inside the ±30-line window. A verifier proved it by
renaming that unrelated key and watching the gate go red on a line it had never examined. **The
gate's entire `agent_memories` coverage was a false positive**, and a green off a neighbour's
key is worth less than no check at all, because it reads as coverage.

Two changes, both in round 4:

* an **ORM site is now read over its own statement** — from the builder to the terminating `;`,
  however long. `:491`'s insert body is 33 lines, so the old 30-line window would have cut off
  the very key it was looking for even without the neighbour.
* **no site's evidence window may cross a corpus site naming a different table.** The fence is
  *table-aware*, and the first version was not: fencing at every corpus site turned
  `pull-gmail.ts:856` red, which is the retry leg re-POSTing the same labelled `row`.

Red proof: the gate reports `FAIL - 1 of 13` and names `:491`. The site itself is fixed in OB1
`debbbaa`. It is **dead code** — `195-` §7's caller table records it as "not built by any compose
service" — but the exemption is now *written down at the site* rather than being an accident of
line distance, because the column is NOT NULL and the first deploy of that file would have been
refused on its first write.

Three things it was watched doing before it was trusted:

* **red on a planted producer, green when the plane is stated** — `-SelfTest`, and again by
  planting `OB1/recipes/zz-red-proof/eleventh-producer.mjs` in the real tree (flagged at
  `:5`), then removing it;
* **red on a neighbouring statement's key** — `-SelfTest` case 3, added in round 4. Cases 1 and
  2 both passed for the whole time the `:491` false green existed, which is exactly why case 3
  is now in the file;
* **its blind-spot list is measured, not asserted.** The same variable-table producer is flagged
  when `const TABLE = "thoughts"` sits next to the `fetch`, and reported `OK - all 1 RECOGNISED
  corpus insert site(s) state their plane` when 40 filler lines are inserted between them. Same
  defect, same file, opposite verdict, decided by whitespace — which is what "it resolves no
  values" means in practice;
* **it found a producer nobody had listed** — `recipes/schema-aware-routing/index.ts:298`, which
  is why the corrected count is twelve and not eleven;
* **it refuses to be vacuous.** Its first version inherited `'*\.claude\*'` from
  `check-llm-gateway-routing.ps1`'s allow-list. A session worktree lives at
  `<repo>\.claude\worktrees\<id>\`, so **every file matched, every file was allowed, and the
  gate reported "OK — every direct corpus insert states its plane" over a scan of nothing** —
  measured, by planting the violating producer and watching it pass. The glob is gone and the
  script now FAILS if it examined zero insert sites. Same defect class as the drill's vacuous
  passes, found in the fix for them.

### Deploying this does NOT end the outage on its own

| producer | ships via | to fix production |
|---|---|---|
| `pull-gmail.ts`, `generate-wiki.mjs`, the import recipes | bind mount `../recipes:/recipes:ro` | move the deployment checkout's OB1 submodule to this gitlink |
| **`docker/wiki-service/wiki-service.mjs`** | **`COPY wiki-service.mjs ./` in `docker/wiki-service/Dockerfile`** | **`docker build -t openbrain-wiki:local OB1/docker/wiki-service` + recreate `openbrain-wiki`** |

`PROMOTION-RUNBOOK.md`'s "no rebuild, no restart" was written about the bind-mounted recipe and
is **false for `wiki-service.mjs`**, which lives in the same container. Corrected there.

### Not closed

* ~~**The OB1 commit carrying these producer fixes could not be pushed**~~ — **CLOSED
  2026-08-31.** `e9be2cd` was pushed by the operator, and round 4's `debbbaa` (the `:491` fix +
  this correction) was pushed from this session. Both are reachable on
  `origin/feat/agent-memory-exposure-column`; the gitlink names `debbbaa`.
* **Ordering hazard, not introduced here but now wider.** A direct insert that names `exposure`
  is a 400 on a database where `195-` has not been applied (the column does not exist). That was
  already true of `generate-wiki.mjs`'s fallback insert and `backfill-gmail-wikis.mjs` from
  round 2; it is now true of eight more files. The runbook's "move the submodule first, apply
  second" therefore has a window in which the direct producers fail — closed by doing both in
  one promotion window, which is what the runbook already instructs.
