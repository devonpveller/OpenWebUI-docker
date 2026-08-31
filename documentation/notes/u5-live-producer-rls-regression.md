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

### The gate, so producer thirteen breaks the build instead of production

`scripts/checks/check-corpus-exposure-producers.ps1`, wired into `.githooks/pre-commit` as check
3b. It **derives** the insert sites rather than carrying a list, in three shapes, each with its
own proximity rule:

| shape | example | rule |
|---|---|---|
| REST URL | `` `${SUPABASE_URL}/rest/v1/thoughts` ``, `` `${REST_BASE}/thoughts` `` | anchored on `/rest/v1/` or a base variable's `}/`; a `?`-filtered path is a read, not an insert |
| POST argument | `obFetch("POST", "thoughts", body)`, `sb.post(\n "thoughts", …)` | table literal within 2 lines of a POST verb; `{"thoughts": []}` (a JSON key) is excluded |
| supabase-js | `.from("thoughts").insert({…})` | `.insert(` must be in the SAME statement — the slice runs forward from `.from(` and stops at the first `;` |

Three things it was watched doing before it was trusted:

* **red on a planted producer, green when the plane is stated** — `-SelfTest`, and again by
  planting `OB1/recipes/zz-red-proof/eleventh-producer.mjs` in the real tree (flagged at
  `:5`), then removing it;
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

* **The OB1 commit carrying these producer fixes could not be pushed** from this session (the
  push was denied by the environment's command classifier). The parent's gitlink was bumped to
  it so the drill could run against what a merge would ship, but **the pin is not reachable on
  `origin` yet** — pushing `feat/agent-memory-exposure-column` is a prerequisite for merging,
  per CLAUDE.md's gitlink rule.
* **Ordering hazard, not introduced here but now wider.** A direct insert that names `exposure`
  is a 400 on a database where `195-` has not been applied (the column does not exist). That was
  already true of `generate-wiki.mjs`'s fallback insert and `backfill-gmail-wikis.mjs` from
  round 2; it is now true of eight more files. The runbook's "move the submodule first, apply
  second" therefore has a window in which the direct producers fail — closed by doing both in
  one promotion window, which is what the runbook already instructs.
