# Daily podcast delivery — findings (2026-08-29)

Incidental discoveries from the `podcast-delivery-key` work. Everything here was
checked against the code path or the live system on the date given, not inferred
from a comment.

## Verified, and left alone deliberately

### `LlmClient`'s `"no-key"` default is a latent J.1 trap — but is NOT firing

`recipes/daily-digest/src/clients/llm.ts` documents its bearer as *"Non-secret
bearer placeholder — llama-cpp ignores when no API key set"*. That comment has
been **false since the J.1 virtual-key flip (2026-08-21)**: the gateway rejects
any bearer that does not start with `sk-`.

`send-digest.ts` constructs it as `bearer: env("LOCAL_LLM_BEARER", "no-key")`,
so the placeholder is only reached if the env var is unset.

Checked in the running container (2026-08-29):

- `openbrain-digest` has `LOCAL_LLM_BEARER=sk-wbqNBAtp…` set.
- A request with that value returns **200**.
- A request with the literal `"no-key"` default returns **401**
  (`LiteLLM Virtual Key expected`).
- `CHAT_API_KEY` is **not** set in `openbrain-digest` (it is set in
  `openbrain-podcast`), so it is not available as a fallback there.

The digest's LLM polish is therefore healthy, and the code was **not** changed —
it is working code, and editing it on suspicion would have been the wrong move.
The residual risk is that the default silently degrades to a 401 if
`LOCAL_LLM_BEARER` is ever dropped from the compose env, with `if (!res.ok)
return null` swallowing it exactly as the podcast path did. Changing that default
to a hard failure (or deleting it) is worth its own item.

### `graph.ts` is stored as a binary file

`OB1/docker/workbench/src/routes/graph.ts` contains one literal NUL byte, used
deliberately as a composite-key delimiter:

    const key = `${r.slug}<NUL>${target}`;

The intent is sound. Writing it as a raw byte rather than a `\0` escape makes
git classify the file as binary, so it cannot be diffed, reviewed line-by-line,
or merged. It runs correctly; the loss is reviewability.

**This note demonstrated its own finding (2026-09-04).** The paragraph above
contained a RAW NUL at byte 1891 — inside the sentence describing the hazard —
so THIS FILE was itself a git binary blob: `git diff --numstat` reported `- -`,
the stat line read `Bin 0 -> 3504 bytes`, and `grep` needed `-a` to search it.
An earlier re-encoding pass fixed mojibake only and left the NUL, and the
"UTF-8 clean" claim in the test plan was therefore wrong. The byte is now
removed and the escape written as text. Check with
`git diff --numstat -- <file>`: a real line count, never `- -`.

## Verified as NOT a problem

Container files appear to differ from their committed blobs when compared with
`sha256sum`, because images are built by copying a Windows worktree (CRLF) while
git stores LF. `diff --strip-trailing-cr` shows them identical. A naive hash
comparison against this stack produces false "drift" alarms — compare with CR
stripped, or compare the container against the *worktree* file rather than the
blob.

## Cause of the 2026-08-29 outage, for the record

One origin, five links:

1. `makeScriptChat` defaulted its key to `"not-needed"` and `link-enrich.ts`
   passed none, so every call sent `Bearer not-needed` and took a **401** from
   2026-08-21 onward.
2. `if (!res.ok) return null` — silent, no retry, no log.
3. `renderEpisode` fell back to dumping raw grounded material: 47,296 chars for
   episode 083.
4. Open Notebook received a 21,822-token transcript prompt and hit
   `podcast_creator`'s `max_tokens: 5000`.
5. The venv was unpatched (the overlay was baked into `Dockerfile`, not the
   `Dockerfile.single` the deployed image is actually built from), so one failed
   segment aborted the whole episode.

Link 5 was fixed by the Open Notebook image rebuild; links 1–2 by this item.
Evidence: LiteLLM `LiteLLM_SpendLogs` holds three `status=failure` rows with
`prompt_tokens=0` at 05:29:38 on 2026-08-29, three seconds before ON's first
transcript call.

## The unit suite proved a policy nothing called (2026-09-04, attempt 2)

`retryUntil` was extracted, exported and covered by four passing tests — and
`link-enrich.ts` called it without importing it. `deno run` does not
type-check, so this was a runtime `ReferenceError`, not a build failure, and
`generateAudio` sits after the report write inside a `try/finally` with **no
catch**: every real run would have written the report and then aborted before
the email, `closeLoop` and `writeEnrichment`. Strictly worse than the bug the
item set out to fix.

The green tests are what hid it. They exercised the helper directly; nothing
asserted that the *caller* binds to it. **A unit suite proves a unit; only
loading the artifact proves the artifact.** One `deno check link-enrich.ts`
catches this in under a second, and no step in the test plan asked for it —
that gap is now T0 in the plan.

Structural note: the 5b pre-commit gate globs `*.test.mjs` and this recipe is
Deno, so nothing in the hook chain type-checks or runs these files. The gate's
green says nothing about daily-digest. Worth a follow-up item — a `deno check`
over the recipe would have caught this at commit time.

## Escalation that is worse than not escalating (2026-09-04, attempt 2)

The truncation fix doubled `max_tokens` but left `timeoutMs` fixed at 200s.
Measured throughput from `LiteLLM_SpendLogs` is 28.9–60.4 tok/s, so a 12k-token
request overran in every sample — and a timeout is a *throw*, which returned
`null` **after** usable truncated text had already been discarded. A control
run made it explicit: with escalation, `null`; without escalation, usable text.

Two lessons, both general:
- **A retry that changes the request must re-check every budget the request is
  measured against.** Doubling the work while holding the clock fixed is not a
  retry, it is a guaranteed timeout.
- **Never discard a worse-but-usable result while reaching for a better one.**
  The fix keeps the best truncated text and returns it rather than nothing.

## Three rounds, three defects the unit suite could not see (2026-09-04)

This item failed test three times. Each failure was real, none was caught by a
green suite, and the pattern across them is the useful part:

| # | Defect | Why the suite missed it |
|---|--------|-------------------------|
| 1 | Script truncated at `max_tokens` exactly | The tests asserted a script was *produced*; nothing asserted it was *finished*. The 401 masked it entirely — you cannot truncate a script you never generate. |
| 2 | `retryUntil` called but never imported | The tests exercised the helper DIRECTLY. Nothing asserted the caller binds to it. `deno run` does not type-check. |
| 3 | `budget`/`bestTruncated` at closure scope, leaking across calls | Every test built a fresh ChatFn and called it ONCE. Production reuses one ChatFn per email, up to `MAX_EMAILS` (1000). |

**The common shape: each test proved the unit and not the usage.** A unit test
constructs the object the way the *test* finds convenient; the defect lives in
the way *production* constructs and reuses it. Three concrete rules earned here:

- **Assert the property the goal names, not the symptom you last saw.** "No
  fallback string" passed while shipping a script cut off mid-sentence. The goal
  said *complete*; the test said *not-obviously-broken*.
- **Load the artifact.** One `deno check` catches an unbound identifier that any
  number of green unit tests will happily talk past.
- **Exercise the object's LIFETIME.** If production reuses a returned closure,
  a single-call test can never see state that leaks between calls. Defect 3's
  own code comment claimed "per-call state" while the declarations sat one scope
  too high — prose asserting an invariant is not the invariant.

Structural note carried forward: the 5b pre-commit gate globs `*.test.mjs` and
this recipe is Deno, so **nothing in the hook chain type-checks or runs
daily-digest at all**. All three defects were reachable at commit time; none of
them could have been caught there. A `deno check` over this recipe is the
cheapest gate this workspace is currently missing.

## The fourth instance: one factory, two consumer kinds (2026-09-04, review)

`makeScriptChat` serves the episode SCRIPT (prose) and four CLASSIFIERS, and
they want **opposite** things from a truncated reply:

- **Prose**: partial text beats nothing — `null` degrades to dumping raw
  grounded material into TTS.
- **Classifier**: `null` is a *deliberate safe default the caller already
  reasoned about* (`src/enrich/promo-filter.ts:147`, "conservative: keep on
  model failure"), while a cut-off reply is **worse than none, because it still
  parses**.

Every fix in this series was reasoned from the prose consumer and applied to
all four ChatFns. Measured against the real `isPromoBody`, one truncated
think-model reply, same input: pre-fix `KEEP` (safe) → post-fix `DROP`, because
the sentence trim cut back past the `VERDICT: KEEP` line and promo-filter fell
through to its bare DROP-substring check. That email then contributes **no
source at all** — directly against this item's own goal.

**The general rule earned here:** when one factory serves consumers with
different contracts, a change reasoned from one consumer is a change to all of
them. Name the consumer kind in the type (`salvageTruncated`, default to the
SAFE reading) rather than assuming the caller you had in mind.

## A hole in the 5b gate, found while landing this (worth its own item)

Pinning the podcast OB1 branch alone would have **silently reverted the wikilink
fix** — the two are siblings off `5224928` — and **the 5b gate would have gone
GREEN doing it**. Two reasons, both structural:

1. The catching test landed in the *same commit* as the fix, so reverting both
   leaves nothing to fail.
2. The shrink floor counts test **files** (8 either way), not assertions or
   test cases.

So the floor detects a deleted test *file* and is blind to a reverted fix that
takes its test with it. A stronger floor would compare the total test COUNT
(48 here) between the old pin's tree and the staged tree, not just the file
count. Recorded, not fixed — this item is not the place.

Mitigation used instead: the gitlink was pointed at an OB1 **merge** of both
branches (`48c0363`), verified byte-identical to each source branch on every
changed file, with both parent SHAs confirmed as ancestors.
