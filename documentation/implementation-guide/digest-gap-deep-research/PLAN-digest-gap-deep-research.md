# PLAN — Digest gap deep-research ("gap dives")

> Status: DEPLOYED + VERIFIED END-TO-END 2026-08-05 (operator-authorized deploy→test→commit).
> `openbrain-podcast` recreated with the `GAP_DIVE_*` env; a full real-window dry-run
> now shows the complete flow: **21 candidates collected → temp-0 triage kept 13
> relevant → ceiling 12 hit → 1 deferred to tomorrow → 12 submitted → 10 filled
> INLINE → 1 carried (pending) → 1 dropped (no grounded material, D0)**. Episode
> narration weaves the dives in, grounded + honest ("our overnight research filled
> in… / we couldn't find the exact dates, that remains an open question"); the email
> artifact carries merged dive key-points + `pendingFollowUps`. Committed after this.
>
> Two integration bugs found + fixed only by running the deployed pipeline (unit/
> isolated tests structurally couldn't catch them): (1) article-mode research leaves
> `result.gaps` **empty** — the `[GAP]` lines live in `result.synthesis`; fixed to
> parse the synthesis. (2) triage was borrowing the podcast script's chat at
> `temperature 0.5`, making it **non-deterministic** (same 18 gaps → [7,10,8,7], and
> it hit 0 on the failing run); fixed with a dedicated **temperature-0** triage chat
> (verified stable [6,6,6,6]).
>
> Build surface (as specified): NEW `src/enrich/gap-dive.ts`; edits to
> `src/enrich/research-client.ts`, `link-enrich.ts`, `src/podcast/script-renderer.ts`,
> `src/podcast/enrichment.ts`, `src/renderers/{html,markdown}.ts`, and the
> `openbrain-podcast` env in `OB1/docker/docker-compose.scheduled.yml`. No
> research-service / curator / schema / container changes.
> Sibling plans: `documentation/daily-digests-autonomous-podcasts/PLAN-digest-podcast-services.md` (S1–S4),
> `documentation/implementation-guide/research-engine-for-OB/PLAN-research-engine.md` (engine + grounding).

## Problem

The morning podcast correctly refuses to fabricate: when an email source mentions
news without context (e.g. a Claude headline with no elaboration), the item
surfaces as `[GAP]` lines, a thin synthesis, or an `email-only` fallback, and the
hosts say "the article leaves open…". That honesty is right — but the listener
wanted the missing context *researched*, not just flagged.

Today the only gap handling is the **bounded preliminary pass** inside article-mode
research (`PRELIM_GAP_LIMIT=3`, `PRELIM_MAX_FETCH=6` → at best a tentative
`[UNCERTAIN] "preliminary research suggests…"`). There is no path from "the email
source didn't have the context" to a **full research session**.

Goal: when missing/incomplete content occurs in the daily digest run, trigger
real deep-research sessions over it — filling the gaps for the podcast/email
**and** growing the research corpus (sessions, sources, grounded claims) with the
daily email as the standing trigger.

## Current behavior (code-grounded)

The nightly chain (all UTC, self-chained off the 05:00 cron): `openbrain-gmail-pull`
→ `openbrain-gmail-prune` → `openbrain-podcast` (`link-enrich.ts --commit --audio`)
→ `openbrain-digest` (email). The email deliberately **waits** for the podcast
(operator decision: no time-box; digest fires from the podcast's `finally`).

Per item, `link-enrich.ts` submits article-mode research
(`mode:"article", gapResearch:"preliminary"`, `origin:"notebook"`) to
`openbrain-research` and wait-gates to terminal `done` (`processLink`,
`link-enrich.ts:353`). The three "source lacked context" signals already exist as
structured data:

1. **Remaining `[GAP]` lines** — `result.gaps` on every done job
   (`research-client.ts:51`), i.e. what survived the preliminary pass.
2. **Thin synthesis** — `countTaggedClaimLines(synthesis)` (`link-enrich.ts:162`)
   of 0–1 = "headline mentioned, nothing grounded behind it".
3. **`emailOnly` fallback** — fetch/research failed (`processLink` fallback,
   `link-enrich.ts:358`); the item reaches the script with no synthesis at all.

Nothing consumes these signals beyond narration (`SCRIPT_SYS`,
`script-renderer.ts:84`) and the email's `followUps` list (capped 6,
`writeEnrichment`, `link-enrich.ts:464`).

Meanwhile the research service already supports exactly the session we want:
`POST /research` **without** `mode:"article"` runs full topic research —
iterative deepening (`MAX_ROUNDS=3`, `MAX_FETCH=40`), SearXNG+Tor gather, claim
reuse, grounded persist + thread resolution via the curator. `origin:"notebook"`
attributes it to the `ob-research` llm-queue lane (rank 3, 30-min acceptable
wait, max_concurrency 2) with 429-backoff up to 25 min. Jobs are durable rows in
`research_jobs`; the drain loop finishes them even after the submitter exits.

## Design

**One sentence:** at the end of the nightly research pass, triage the day's gap
signals into a capped set of full deep-research jobs ("gap dives"), wait a bounded
budget so tonight's podcast/email get the results, and carry anything unfinished
into tomorrow's episode as a resolved follow-up.

### D0 — Invariant: honest-by-default is preserved

A dive only ever *adds* grounded, tagged claim lines. It never fabricates, never
forces a filled answer, and never rewrites the existing no-fabrication behavior. A
dive that comes back empty, partial, or unfinished leaves the item narrated
exactly as today — `[GAP]` spoken as an open point, `email-only` spoken with the
"we only had the newsletter blurb" caveat. The current stance ("this was mentioned
but the source didn't have the context") is the correct floor and stays the floor;
dives raise it *when and only when* real grounded material is found. This is a hard
rule for the dive-aware `SCRIPT_SYS` addition (D-changes below): if there is no
dive synthesis, or the dive synthesis has zero tagged claim lines, narrate the item
as unfilled.

### D1 — Candidates (all three signals, digest-level)

Collect per finished item: its remaining `result.gaps`, a thin-synthesis marker
(`taggedLines < GAP_DIVE_MIN_TAGGED`, default 2 → the item's *topic* becomes the
candidate), and `email-only` items (candidate = link text + subject). Each
candidate carries the owning item's `label`, `gmail_id`, **source-email subject**
(the relevance lever), and curator `thread_id` when present.

### D2 — Triage: relevance is the throttle, not a number

*(Operator direction 2026-08-05: a fixed nightly cap is the wrong lever — it either
drops relevant gaps on a heavy day or lets them pile up to a stale, months-later
dive. Relevance decides the volume instead.)*

One cheap `:nothink` pass over ALL candidates at once, **grouped by their source
email**: dedupe cross-newsletter repeats, then for each gap judge whether it is
**relevant to what its source email is actually about** — keep every gap central
to the newsletter's topic (however many that is), and drop gaps tangential to the
email's point (an incidental aside, a minor unstated figure) even if independently
interesting. This relevance filter is what keeps the count sane, and it's exactly
why heavy days don't explode: most `[GAP]` lines are peripheral. Survivors are
rewritten into self-contained questions and ranked most-relevant-first. There is
**no fixed count** — `GAP_DIVE_CEILING` (default 12) is only a runaway safety
valve; when it's exceeded the overflow *defers* to the next night (D4/D5), it is
not dropped. Candidate text is untrusted: the triage prompt quotes it strictly as
data (`INJECTION_GUARD` posture), and the dives inherit the service's
`INJECTION_GUARD` + `screenSources`.

Measured (2026-08-05 live triage on real gaps): 21 raw candidates → **5**
relevance-vetted dives; ceiling not hit, 0 deferred. Heaviest recent day (Aug 4:
31 gap lines + 10 email-only) is well within a ceiling of 12 after relevance
filtering.

### D3 — Dive = full default-mode research job

`research.submit({ query, origin:"notebook", threadId, dryRun: !COMMIT })` — no
`mode`, no seeds, web search ON. `threadId` from the owning item's curator echo so
the dive compounds the same thread. This is deliberately a *separate top-level
job*, not a deeper in-article pass: it gets its own session/thread placement,
enables cross-newsletter dedupe, and keeps article-job latency unchanged.

### D4 — Same-night budget + durable carryover

Submit dives after the existing wait-gate, before script render. Wait up to
`GAP_DIVE_WAIT_MS` (default 2 h — same order as the prod `RESEARCH_WAIT_MS`).
This fits the operator's standing decision that the email waits for a healthy
process. Dives finishing in budget enrich tonight's script + email. On budget
expiry we do **not** cancel: jobs keep draining server-side; their ids +
questions + attempt counts go to a ledger (`/reports/gap-dives-pending.json`).
The dive stage is best-effort end-to-end — any failure degrades to today's
behavior, never blocks the digest chain.

### D5 — Next-morning resolution loop + freshness gate

At the start of the next run, resolve the ledger. Each entry sorts to: **done +
grounded** → a dedicated **`follow-ups` segment** ("yesterday we flagged X — here's
what we found") + email block; **still running** → keep (no penalty); **errored**
→ attempt++ then resubmit, dropped after `GAP_DIVE_MAX_ATTEMPTS=2`; **deferred**
(overflow not yet run) → resubmitted first this run. The **freshness gate**
(`GAP_DIVE_MAX_AGE_DAYS`, default 3) is the hard guarantee against the operator's
"months-later" worry: any carried entry older than the window — deferred or
retrying — is **dropped as stale, never researched late**. A done-with-material
entry is always narrated regardless of age (it finished). The triage pass sees all
in-flight/deferred questions so it never re-submits one already queued.

### D6 — The flywheel (why this grows the corpus)

Each dive is a normal engine run: staged `sources`, a `session`, grounded
`claims` + typed edges, thread attach/create, wiki recompile. Tomorrow's
article-mode reuse pass pulls those claims automatically, so repeat coverage of a
story gets cheaper and the email's `[GAP]` count should trend down. Watch
`research_run_metrics` (reuse ratio / gap_ratio) to confirm.

### Email changes

`followUps` splits three ways: **resolved** (dive done — rendered with key
points), **digging deeper overnight** (dive submitted, still running), and
**open** (not selected). The reader sees the system actively working the gaps
instead of a static wishlist.

## Changes by file (all in the bind-mounted digest recipe; no service rebuilds)

- **NEW `OB1/recipes/daily-digest/src/enrich/gap-dive.ts`** — candidate types,
  triage prompt + `planDives()`, ledger load/save/resolve, attempt accounting.
- **`src/enrich/research-client.ts`** — make `seedSources` optional in
  `SubmitArgs` (topic mode sends none). ~3 lines.
- **`link-enrich.ts`** — collect candidates in `recordDoneJob` + the `fallback`
  path; after `mapLimit(...)`: triage → submit → `waitForAll` with
  `GAP_DIVE_WAIT_MS` → merge finished dive syntheses into the owning
  `SegmentItem` (new `dive` field) → write ledger. At `main()` start: resolve
  yesterday's ledger → `follow-ups` segment. Extend `writeEnrichment` with the
  three-way follow-up split. Dry-run: dives inherit `dry_run`, ledger gets the
  `-dryrun` suffix (never clobbers production state).
- **`src/podcast/script-renderer.ts`** — `SegmentItem.dive?` + `SCRIPT_SYS`
  addition: narrate dive material as "we dug deeper…", follow-ups segment as
  "yesterday we flagged…". Grounding rules unchanged (dive synthesis is tagged
  claim lines like everything else). Audit refinements: (a) trim dive material
  to its top tagged lines before `renderSegmentForPrompt` (script chat caps
  output at 2 200 tokens — keep the prompt proportionate); (b) an `emailOnly`
  item WITH dive material keeps its marker so the hosts narrate "we only had
  the blurb, so we did our own digging overnight".
- **`src/podcast/enrichment.ts`** + `src/renderers/{html,markdown}.ts` —
  `EmailEnrichment` gains `resolvedFollowUps` / `pendingFollowUps`; render blocks.
- **`OB1/docker/docker-compose.scheduled.yml`** (`openbrain-podcast` env) — new
  knobs: `GAP_DIVE_ENABLED=1`, `GAP_DIVE_CEILING=12` (runaway safety, not the
  throttle), `GAP_DIVE_MIN_TAGGED=2`, `GAP_DIVE_WAIT_MS=7200000`,
  `GAP_DIVE_MAX_ATTEMPTS=2`, `GAP_DIVE_MAX_AGE_DAYS=3` (freshness gate).

**Not changed:** `openbrain-research` (topic mode, lane attribution, injection
defense all already live), the curator, the schema, the chain topology. No new
container → no emergency-recovery/stack-map 3-place change.

### Codebase audit (2026-08-05) — verified by direct reads

- `POST /research` accepts a seedless, modeless submit: `seed_sources` optional
  (`index.ts:600-607`), article mode only when explicitly `mode:"article"`
  (`:610`) → dives run the default full-research path with no service change.
- `origin:"notebook"` valid at the DB CHECK (`init-research-jobs.sql:30`) and
  the handler (`index.ts:620`); unknown origins coerce to `"owui"` (pre-existing
  quirk: agent-bridge's `"agent-org"` origin lands in the default lane — out of
  scope here).
- Recipe seams all additive as planned: `SubmitArgs`/`waitForAll`
  (`research-client.ts:23,146`), gap signals + wait-gate (`link-enrich.ts:162,
  335,358,394`), `SegmentItem` (`script-renderer.ts:22`), `EmailEnrichment`
  (`enrichment.ts:26`).

## Load & safety

- **GPU/queue — dives stay below OWUI chat by design:** dives submit as
  `origin:"notebook"`, which is the ONLY origin attributed to the `ob-research`
  llm-queue lane (rank 3, `acceptable_wait_s=1800`, `max_concurrency=2`). That
  lane sits far below the `owui-chat` rank-0 interactive class, so nightly dives
  can never starve OWUI chat — the operator's "prioritize OWUI chat inlets"
  intent holds. Dives are serialized by the service (`MAX_CONCURRENCY=1`) inside
  the overnight window; 429s are ridden out as backpressure (25-min budget). Est.
  +1–2 h wall inside the 2 h dive budget. (Note: OWUI's own *research* — the
  deep_research tool, `origin:"owui"` — is deliberately left on the default lane,
  above the ob-research batch lane, so operator-initiated research isn't stuck
  behind the nightly batch. Dives correctly do NOT take that lane.)
- **Email always arrives:** unchanged guarantee — dive stage is best-effort;
  podcast `finally` still fires the digest on any outcome.
- **Injection:** candidate text handled as quoted data at triage; dives run
  behind `INJECTION_GUARD` + `screenSources`. Promo/ad text is already filtered
  before it can become a candidate.
- **No thrash:** ledger attempt cap (2) + triage dedupe vs in-flight jobs; the
  engine's own reuse pass makes a re-asked answered question cheap.
- **Rollback:** `GAP_DIVE_ENABLED=0` + recreate `openbrain-podcast` restores
  today's behavior exactly (OB1 recipe files are not git-tracked here, so the
  flag — not git revert — is the rollback lever).

## Deploy + verification

1. `deno check` all touched files.
2. **Dry-run triage only** — new `--dump-dives` flag (prints candidates +
   KEEP/drop + rewritten questions, submits nothing), on today's window. The
   under-elaborated Claude item should appear as a KEEP.
3. **Dry-run full** — dives submitted with `dry_run:true`: previews in the run
   report, no canonical writes, `-dryrun` ledger.
4. Recreate `openbrain-podcast` with the new env
   (`docker compose -p open-brain --project-directory OB1/docker -f
   OB1/docker/docker-compose.yml up -d openbrain-podcast`); recipe code is
   bind-mounted, so the next run picks it up.
5. One manual COMMIT run **after a fresh pull** (stale-pull lesson: run the
   gmail-pull first or the data is old), then let the 05:00 cron own it.
6. Next morning: confirm the follow-ups segment + email split renders, and
   `research_jobs` shows the dives with sessions/claims landed on real threads.

## Out of scope / future

- **Raising the preliminary pass** (`PRELIM_*`) — kept as-is; dives supersede it
  for the items that matter without inflating every article job.
- **Idea Refinery-style standalone drain service** — not needed while dives ride
  inside the podcast run; revisit if dive volume grows past the nightly window.
- **User-steered dives** ("dig into X from today's email" via Mattermost/OWUI) —
  natural follow-on: same `planDives()` path, manual trigger.
- **Volatility-driven re-research** of aging claims (`revalidate_days`) — the
  engine has the fields; a future sweep could refresh stale high-value claims.
