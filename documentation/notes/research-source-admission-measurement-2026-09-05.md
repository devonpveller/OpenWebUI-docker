# Source-admission measurement — the wall problem is refuted (2026-09-05)

**What was checked:** the "measure first" step of
`documentation/implementation-guide/research-source-admission/PLAN-source-admission.md`
(shelved 2026-08-20). That plan's own decision rule: if walls/captchas/soft-404s
are <5% of pages charged to `MAX_FETCH`, stage 1 (content-validity gate + budget
refund) is not worth building; if ~40%, it explains the low coverage outright.

**Method:** retrospective, read-only. Every research-staged page persists with
full content (`sessions` → `session_sources` → `sources`), and `stageSource`
runs AFTER the 08-22 gates — so pre-08-22 sessions hold everything that charged
the budget, post-08-22 sessions hold only gate survivors. SQL classifier
(title/head regex per wall type + length buckets) over 18,079 stage events
across 1,959 sessions (2026-06-07 → 09-05); random samples eyeballed to verify
the classifier. Script: session scratchpad `wall-audit.sql` (rerunnable; the
classifier CASE is in this note's history via the SQL if ever needed —
deterministic, no LLM).

## Numbers

| Cohort | Stage events | Pattern walls | Stubs (<400ch) | Substantive |
|---|---|---|---|---|
| Pre-gate (before 08-22) | 16,545 | 109 (0.66%) | 1,197 (7.2%) | 92.1% |
| Post-gate | 1,534 | 9 (0.6%) | 43 (2.8%) | 96.6% |

- Wall/stub sources that ever reached grounded-claim citation: 22 distinct
  (3 captcha, 1 js_wall, 18 stubs).
- Backstops since the 08-20 ceiling raise (`MAX_FETCH` 80, `MAX_WALL_MS` 15min):
  **115 done jobs, 0 × max_fetch, 0 × wall_time — all complete.**
- Coverage (reuse_ratio) avg: 55.7% pre-cohort vs 37.3% post — NOT comparable
  (different job mixes; pre includes heavy-reuse digest lanes). The operational
  fact is the backstop row: low coverage now means honestly-open gaps, not
  budget starvation.
- `source_revisions` is empty and `stageSource` overwrites content per URL, so
  a wall later re-fetched clean is invisible — the wall numbers are a floor.
  The magnitude gap (0.7% vs the 5% bar) is too wide for this to change the
  verdict.

## Eyeball findings (the part the numbers alone would have gotten wrong)

- Uncited stubs ARE mostly junk: YouTube chrome-only shells, "Loading…" JS
  shells, redirect interstitials, login pages, 3-char MSN shells.
- **Cited stubs are mostly thin-but-TRUE**: "Your recycling day is every
  Wednesday" (38 chars), a Fidelity TDF definition, Reddit answer snippets.
  A hard length floor would discard exactly the long-tail evidence this stack
  exists to find. Do not add one.
- Genuine absurdity found: `filtering.ts isRelevant()` auto-passes content
  under 20 chars ("nothing to judge") — which is how a 4-character source
  ("Qwen", `qwen.ai/blog?id=qwen3.6-27b`) reached grounded citation. Shells
  this small have no judgeable content and no citable content either.

## Verdict

1. **Stage 1 fails its own build bar** (<1% pattern walls vs 5% threshold).
   The `rejected`-outcome budget refund is moot — ceilings no longer bind at
   all. The 08-22 `filtering.ts` work (credibility ranking + relevance gate)
   already took the worthwhile part of the shelved plan.
2. **One one-liner is worth taking:** flip the `<20 chars` auto-RELEVANT in
   `isRelevant()` to auto-reject (a page whose extracted text is under ~20
   chars cannot ground anything; the thin-but-true snippets above are all
   well over it). Closes the "Qwen"-class citation leak with near-zero
   false-positive surface.
3. **Coverage is no longer a budget problem.** If mid-30s coverage matters,
   the levers are search yield (`SEARCH_K` × `MAX_ROUNDS` caps candidates at
   ~24/round), gap pursuit (`gap_research`), or the source→proposal→claim
   gap-closing lifecycle from the shelved plan — a design effort, not a
   filter.
4. PLAN v3 (`source-admission-adversarial-gate`, sibling docs repo) keeps its
   own justification — it is a safety/injection gate across ALL lanes, not a
   usefulness filter; nothing here weakens it. Its `/v1/fetch` chokepoint
   remains the right home for wall detection IF it is ever wanted — as
   telemetry, not as budget protection.

**Status:** measurement complete; shelved plan can be closed as
MEASURED–REFUTED except for item 2 (the one-liner) and item 3 (a separate,
larger design decision).


---

# Closure — anchor srcadm landed (2026-09-05, merge ac67de4)

Item 2 (the one-liner) shipped, grown into its honest size: OB1 `a07103b` flips
`isRelevant()`'s sub-20-char auto-pass to auto-reject (no LLM spend), labels
rejections with reasons, teaches both fail-safe floors that emptiness is not a
verdict, and makes a configured-but-unbuildable fetch proxy refuse at startup
(EX_CONFIG) instead of silently going direct. Tester: PASS attempt 1,
-PlanInadequate; reviewer: FITS-CODEBASE. Deploy of openbrain-research off the
new pin is still pending (operator-gated).

Correction to the record: this note was described mid-effort as "on the branch";
it was actually untracked in the main checkout until this commit.

## Findings carried out of the srcadm test/review (not acted on)

- **Prelim log misattribution** — read-from-source (reviewer + tester,
  independently): `harness.ts:534-536` logs an all-shell preliminary batch as
  "irrelevant" with undifferentiated counters; the main pool got the
  irrelevant/shells_dropped split at :461-465 and a dedicated all-shells
  message. Behavior correct (shells are dropped); the progress record
  misattributes model verdicts. Candidate for the next research-service touch.
- **Tor-era proxy default** — read-from-source (reviewer): `index.ts:121` still
  defaults `FETCH_PROXY_URL` to retired `socks5h://tor:9050`, and Dockerfile:29
  explains the flag in Tor terms (Tor retired 2026-08-21; compose overrides to
  vpn:8888). A deploy that lost its compose env would BUILD a client to a dead
  host and fail every fetch — fail-closed, but the default should follow the
  fleet. Cheap fix next touch: default to http://vpn:8888 or to "" (refuse).
- **Seeds gated in article/sources-only/no-web-search modes** — read-from-source
  (tester): `protectedCount` is only set on the default path (`harness.ts:339`
  inside `if (!articleMode && !skipSearch)`), so in those modes caller seeds
  face the relevance gate as if web-gathered — pre-existing; NEW under srcadm is
  that a sub-20-char seed is now dropped rather than auto-passed. Judged
  in-bounds (a contentless seed grounds nothing); flagging because the gate's
  own comment claims seeds are protected, which is only true on the default
  path.
- **KB recalls never shell-checked** — read-from-source (tester): protected
  pages (`harness.ts:306` recall staging) bypass the gate entirely, so a short
  KB source can still reach staging by that route. Pre-existing, untouched.
- **Test-shape nits** — read-from-source (reviewer): `filtering.test.ts:80-84`
  asserts via a prompt-regex that goes vacuous if RELEVANCE_SYS's user-message
  shape changes (property still held non-degradably by the chatCalls counter);
  `let judgedUrls` should be const. `deno lint` was already red pre-change on
  two no-import-prefix errors.
- **Plan-authoring lesson** — observed-live (tester): plan case T5b keyed on
  `--unstable-net` gating `createHttpClient`, true on the deployed deno 2.3.3
  and false on the tester's 2.8.1, where the case would have passed while
  exercising nothing AND could have started a live server. Write repro cases
  against the DEPLOYED runtime's behavior or version-independent inputs (the
  tester's substitute: an unbuildable URL scheme).
