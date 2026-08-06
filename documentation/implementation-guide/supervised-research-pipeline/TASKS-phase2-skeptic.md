# TASKS — Phase 2: Skeptic stage

> Status: **JUDGE-ONLY TIER BUILT + validated 2026-08-05** (not committed, not
> deployed). Design-of-record: `PLAN-supervised-research-pipeline.md` §"Phase 2".
>
> BUILT: `OB1/integrations/research-service/skeptic.ts` (pure: `SKEPTIC_SYS`,
> `parseSkepticResult` fail-open, `applyDowngrades` index-safe tag rewrite,
> `deriveRequeries`) + `skeptic.test.ts` (11 pure tests); wired into `harness.ts`
> (judge stage before `buildCitedAndRenumber`, `SKEPTIC_ENABLED` env default off,
> `RunResult.skeptic`) + `index.ts` (echo `result.skeptic`). `deno check`/`lint`
> clean; `deno test` = 43 passed (skeptic+contract+lib). OFF path (default) is
> additive-only (`result.skeptic: null`). Deploy = `docker compose build
> openbrain-research` + recreate (operator, on-site).
>
> DEFERRED to on-site validation (need the live GPU/DB stack, so cannot be
> runtime-validated headless): the **drop-and-replace re-gather tier** (pool
> mutation + RE-synthesis — index alignment must be verified against a real run;
> `SKEPTIC_REGATHER_MAX` reserved), the **T5 metrics-view** extension (DB change),
> and **Phase 2b** (curator-side durable audit; DB migration). The judge-only tier
> stands alone as a complete defensive gate: downgrade weak/refuted claims below
> the reuse floor + per-run audit; the re-gather adds "get more sources" on top.

## Architectural insight (why the downgrade is free)

The synthesizer emits tagged lines `[SOURCED]/[INFERRED]/[UNCERTAIN]/[GAP]`
(`harness.ts:90-103`) and the curator parses those tags into weighted grounding
edges — `states`/`corroborates` weight 1.0, `inferred_from` 1.0, `[UNCERTAIN]`
`inferred_from` weight 0.5 (`research-curator/claims.ts:78-91`) — which the SQL
confidence function consumes. **So the Skeptic downgrades a claim simply by
rewriting its tag** (`[SOURCED]`→`[UNCERTAIN]`) in the synthesis string **before**
`buildCitedAndRenumber` (`harness.ts:523`) and the curator delegation
(`harness.ts:547-564`). A downgrade flows to lower confidence through the pipeline
that already exists — **no curator or schema change** for the core path.

## Insertion point

New stage in `runResearch`, **between** `rawSynthesis` (`harness.ts:514-517`) and
`buildCitedAndRenumber` (`harness.ts:523`). It reads `rawSynthesis` + the same
`sourceList`/`pool` already built at `harness.ts:511-513`.

## Output shape (structured + quantified — the Track B forecasting hook)

```ts
export interface SkepticChallenge {
  target: string;   // the claim text or overall thesis under challenge
  type: "source_dependence" | "currency" | "severity_inflation"
      | "non_sequitur" | "disconfirming_evidence";
  evidence: string; // why — cite [Source N] or the disconfirming find
  confidenceDelta: number;      // negative; drives forecast probability later
}
export interface SkepticResult {
  challenges: SkepticChallenge[];
  downgrades: Array<{ from: string; to: string }>;       // tag rewrites applied to synthesis
  refuted: string[];                                     // claims judged unsupported → not invested
  droppedSources: Array<{ url: string; reason: string }>;// source-level rejections (defense + audit)
  regatherRounds: number;                                // drop-and-replace iterations run (≤ SKEPTIC_REGATHER_MAX)
}
```

**Not a boolean.** The quantified `confidenceDelta` + structured challenges are
exactly what the Track-B prediction record consumes unchanged (PLAN Phase 2 note);
a pass/fail verdict would force a later rework.

## Outcome — a defensive investment-gate (AUTONOMOUS, no human in the loop)

The Skeptic's purpose is **defense**: keep low-quality sources and ungrounded/fake
claims from being *invested* into the durable Open Brain KB, while recording an
**audit trail** of what was rejected and why. It runs **fully autonomously** —
sources are abundant, so the answer to a bad source is *drop it and gather more*,
and the answer to an ungroundable claim is *don't invest it, degrade honestly*.
**No human is ever in the research loop** (the Phase 4 human gate is a separate,
downstream concern at consequential *outlets* — never a step here).

Two levels + one bounded loop:

- **Source-level filter.** A source the Skeptic judges untrustworthy/low-quality is
  **dropped**, and the run **gathers more** to replace it (reuses
  `deps.searchWeb`/`deps.fetchPage` + `screenSources`).
- **Claim-level investment-gate.** A claim is invested (promoted to the KB) **only
  if it survives grounded** in the sources that remain. One that cannot be grounded
  after bounded re-gathering is **not invested** — it degrades to honest
  `[UNCERTAIN]`/`[GAP]` (tag rewrite → prose renderer `harness.ts:529-536`, so the
  reader hears the caveat too), never fabricated.
- **The loop (bounded, terminating).** flag bad source / ungrounded claim → drop →
  gather more (corroborating **and** negated queries) → re-synthesize → re-Skeptic →
  converge or exhaust budget. On exhaustion: invest survivors, keep the rest **out**
  with an audit record. Charged against the existing `backstopDecision`
  (`MAX_FETCH`/wall); capped like the gap-dive drain (attempt-cap / no-retry-storm)
  so it cannot thrash the single GPU or hang. Never calls a human, never fabricates.

Secondary effects (fall out of the above): reuse — a non-invested/downgraded claim
sits below the `CONFIDENCE_FLOOR` (0.50) governing `decideReuse` /
`reusable_claims`, so it is re-researched rather than compounded; quality sentinel
— `metrics.skeptic_challenges` trends refutation rate (T5).

## Audit history ("no traces, no trust", applied to rejections)

- **Per-run (core, free):** `result.skeptic` records every dropped source + refuted
  claim + reason → auditable in the job row immediately.
- **Durable / queryable (Phase 2b, recommended target):** a KB-level rejection
  record — an explicit `refuted`/quarantine claim state + a "screened-out" source
  marker — so **future runs consult the history**: skip re-fetching a known-bad
  source, and see what was considered-and-rejected instead of re-litigating it.
  Needs the curator to consume a Skeptic verdict (`claims.ts` + `init-claims.sql`
  additive change), which is why it is out of the no-migration core. The
  operator's "audit history for future use" intent argues for scheduling this
  soon after core, not treating it as optional.

## The self-heal loop (the core defensive mechanism)

Drop-and-replace is not an opt-in tier — it **is** the Skeptic's defensive job. The
judge runs on the **local** model; its verdict drives a bounded, terminating loop:

1. **Judge pass.** `deps.chat(INJECTION_GUARD + SKEPTIC_SYS, synthesis + sourceList,
   {json:true, nothink:true})` via the existing `jsonChat` pattern
   (`harness.ts:202`). Sources are untrusted data (INJECTION_GUARD prepended, same
   posture as synth `harness.ts:515`). Emits `SkepticResult` (challenges,
   downgrades, refuted, `droppedSources`).
2. **Source-level drop.** Remove `droppedSources` from the working pool.
3. **Re-gather (if warranted + budget remains).** If sources were dropped **or**
   claims remain ungrounded, **and** `regatherRounds < SKEPTIC_REGATHER_MAX`,
   **and** `backstopDecision` still says continue → gather more (reuse the existing
   gather machinery: `deps.searchWeb` with corroborating **and** negated queries →
   `deps.fetchPage` → `screenSources` → `stageSource`) → **re-synthesize**
   (`SYNTH_SYS`, `harness.ts:514`) over the refreshed pool → back to step 1.
4. **Terminate** when converged (no drops, nothing ungrounded), the cap is hit, or
   the backstop stops (`wall_time`/`max_fetch`). Then: apply final tag downgrades,
   **invest survivors**, keep non-invested claims **out** (honest `[UNCERTAIN]`/
   `[GAP]`, never fabricated), record the audit (`droppedSources` + `refuted`).

Bounding: the loop is charged against the **existing** `backstopDecision`
(`MAX_FETCH`/wall) so it can never thrash the single GPU or hang, and additionally
capped by `SKEPTIC_REGATHER_MAX` (per-run iteration cap). `SKEPTIC_REGATHER_MAX` is
a **tunable env, default TBD (decided later by the operator)**: `0` = judge-only
(downgrade + keep-out, no re-gather — the cheapest rollout); `N>0` = self-heal with
up to N drop-and-replace rounds.

The whole loop is **local**. A cloud/model-diversity refutation variant is deferred
with Track B (no cloud model in the stack today); the structured output leaves that
seam open but nothing here builds toward it. Phase 1's contract, **when present**,
can further constrain the re-gather's sources/budget — but the loop does **not**
depend on Phase 1 (it stands on the existing gather + backstop).

## Tasks

- **T1 — `SKEPTIC_SYS` prompt (`harness.ts`, beside the other `*_SYS`).**
  Adversarial/defensive charter: *refute, do not write*; judge **sources** for
  trustworthiness/quality (drop the bad ones) **and** **claims** for grounding in
  the sources that remain (source independence, currency, severity inflation,
  does-the-conclusion-follow, what would disconfirm). Emit the `SkepticResult` JSON
  incl. `droppedSources`. Model the tone on the agent-org reviewer charter.
- **T2 — judge + apply-downgrades stage in `runResearch`.** After `harness.ts:517`:
  run the judge pass; apply `downgrades` to `rawSynthesis` (tag rewrite, preserve
  `[Source N]`). Guards: skip if `!rawSynthesis.trim()`; run in `dryRun` (read-only)
  but annotate-only; skip if the wall budget is already spent (`elapsedMs >= maxMs`).
- **T3 — self-heal loop (the core defensive behavior).** Source-level drop of
  `droppedSources` + the bounded re-gather → re-synthesize → re-judge loop above.
  Reuse the existing gather machinery (`searchWeb`/`fetchPage`/`screenSources`/
  `stageSource`) and `SYNTH_SYS`. Bounded by `SKEPTIC_REGATHER_MAX` **and** the
  existing `backstopDecision`. On terminate: invest survivors, keep non-grounded
  claims OUT (honest `[GAP]`/`[UNCERTAIN]`), never fabricate.
- **T4 — thread through `RunResult` + echo the audit.** Add `skeptic?: SkepticResult`
  to `RunResult` (`harness.ts:183`); return it (`harness.ts:566-580`); echo into the
  jobs row `result.skeptic` (`index.ts:417-423`) — this is the **per-run audit
  trail** (challenges, downgrades, refuted, `droppedSources`, `regatherRounds`).
- **T5 — quality signal (the "quality sentinel" role).** Add
  `metrics.skeptic_challenges` + `metrics.skeptic_downgrades` +
  `metrics.dropped_sources`; extend the `research_run_metrics` view so refutation/
  drop rate trends — a sustained spike = upstream degraded (observability-audit
  alert pattern).
- **T6 — flags + tunables.** `SKEPTIC_ENABLED` env (default `0`, ship-dark then
  enable, the `GAP_DIVE_ENABLED` pattern; off ⇒ byte-identical to today) +
  `SKEPTIC_REGATHER_MAX` (tunable iteration cap, default TBD — decided later).
- **T7 — tests (`skeptic.test.ts`).** downgrade a `[SOURCED]` with no real support →
  `[UNCERTAIN]`, `[Source N]` preserved; a **bad source is dropped and replaced** by
  re-gather; an **ungroundable claim is kept OUT** (not invested) and degrades to
  `[GAP]`, never fabricated; loop terminates at `SKEPTIC_REGATHER_MAX`; loop
  terminates on backstop `wall_time`/`max_fetch`; audit (`droppedSources`+`refuted`)
  present in `result.skeptic`; `SKEPTIC_ENABLED=0` run identical to baseline.

## Phase 2b — durable audit (curator-side; recommended soon after core)

The per-run audit (`result.skeptic`, T4) ships in core. The **durable, queryable**
rejection history — an explicit `refuted`/quarantine claim state + a "screened-out"
source marker so **future runs skip known-bad sources and don't re-litigate**
rejections — needs the curator to consume a Skeptic verdict (`claims.ts` +
additive `init-claims.sql` change). Kept out of the no-migration core, but the
operator's "audit history for future use" intent makes this the **recommended next
increment**, not an optional tail.

## Deploy ladder (built image)

`deno check`/`lint`/`test` → `docker compose build openbrain-research` →
`up -d openbrain-research`. No migration for the core path (Phase 2b would add
one). Ship with `SKEPTIC_ENABLED=0` (and `SKEPTIC_REGATHER_MAX=0` for the first
enable = judge-only, cheapest), then raise the cap once refutation/drop rates are
observed. Verify on a known over-confident query: downgrades applied, a bad source
dropped+replaced, an ungroundable claim kept out as `[GAP]`, `result.skeptic` echoed.
No stack-map change.

## Dependencies

**Phase 2 is independent of Phase 1** — the self-heal loop stands on the existing
gather machinery + `backstopDecision`, needs no contract. Phase 1's contract, when
present, can *additionally* constrain the re-gather's sources/budget. **No human in
the loop** (Phase 4 is downstream + separate). Feeds **Track B** (deferred) via the
structured output. **Phase 2b** (durable audit) recommended as the next increment.
