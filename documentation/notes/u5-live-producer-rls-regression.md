# LIVE REGRESSION: U5's `thoughts` ops-plane policy refuses every direct-POST producer

**Found** 2026-08-31, during H3 round 2 adjudication. **Status: live, ongoing, not yet fixed.**
**Cause: work already MERGED (U5), not the branch under review (`work/u8h3`).**

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
