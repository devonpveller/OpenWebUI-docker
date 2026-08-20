# PLAN — Supervised research pipeline (Skeptic · contracts · attribution · outlet gates)

> Status: DRAFT 2026-08-05 — design only, no build. Derived from an audit of the
> live research service against an external "supervised agent workflow for
> sensitive research & forecasting" outline (Sergio Coronado conference talk).
> Scope of THIS plan = research-service core (Phases 1–4). Forecasting (Track B)
> and stack-wide research-debt generalization (Track C) are separate, optional,
> and gated on an explicit mission decision.
>
> **All inference stays LOCAL for this effort.** Cloud routing (OpenRouter /
> model-diversity on stronger models) is explicitly out of scope and deferred with
> Track B — the stack wires no cloud model today. Phases 1–4 are local-only.
>
> Sibling / prerequisite reading:
> - `documentation/implementation-guide/research-engine-for-OB/PLAN-research-engine.md` (the engine + grounding model)
> - `documentation/implementation-guide/digest-gap-deep-research/PLAN-digest-gap-deep-research.md` (gap dives — the built research-debt drain this plan generalizes from)
> - `documentation/implementation-guide/expand-OB1-research-inlet-service/PLAN-research-inlet-service.md` (curator)

## Framing

The outline describes a 7-role supervised pipeline (Searcher → Extractor → Triage
→ Synthesizer → **Skeptic → Evaluator → Human**) wrapped in a harness of
declarative per-agent contracts, red lines, budgets, and traces. The stack
already implements the front two-thirds — searcher/extractor/triage/synthesizer
roles live in `OB1/integrations/research-service/harness.ts::runResearch`, with
per-run traces in `research_jobs`, an epistemic claims KB (`init-claims.sql`),
honest `[GAP]` degradation, prompt-injection defense, and controlled egress
(SearXNG-over-Mullvad + Tor). It exceeds the talk on injection defense and the
durable grounded-claims layer.

The gaps cluster at the **back of the pipeline** and in the **control plane**.
This plan closes them as extensions of existing machinery, not a re-architecture.
The service stays one Deno service; the durable move is **policy-as-data** so new
consumers differ by contract, not by code fork.

Ordering rationale: the **contract (Phase 1)** is the general policy seam, so it
lands first — but the phases are loosely coupled. The **Skeptic (Phase 2)** stands
on the existing gather + backstop and is **independent of Phase 1**; the contract,
when present, only *further* constrains the Skeptic's re-gather (sources/budget).
Attribution (Phase 3) is nearly free and independent. Outlet gating (Phase 4)
touches consumers, not the engine, and its scope-widening gate (Trigger B) is the
one piece that genuinely needs Phase 1.

---

## Phase 1 — Per-job contract (policy-as-data)

> Build spec: `TASKS-phase1-contract.md` (task breakdown + verified code anchors +
> deploy ladder). No schema migration; enforced by wrapping `jobDeps` at
> `index.ts:401-403`.

**Why first:** the outline's entire harness layer (contracts / red lines /
permissions / budget) reduces, for this engine, to *a declared, enforced,
recorded contract per job*. `research_jobs.options` (jsonb) already exists as the
home; today it carries only `confidence_floor`, `mode`, and gap-research knobs.
Promote it to a first-class contract and the Linux-advisories reference use case
("official advisories only, no PoC retrieval") becomes **expressible** —
currently it is not: the engine has no source allow/deny list at all, only
authority *weighting* (`.gov/.edu/.mil` confidence boost in `init-claims.sql`).

**Contract fields (all optional; absent = today's behavior):**
- `sources.allow` / `sources.deny` — domain globs; enforced in `searchWeb` result
  filtering and `fetchPage` before fetch. Empty allow = permit-all (status quo).
- `sources.classes` — named source sets (e.g. `advisories` = CVE/NVD/vendor PSIRT
  domains) resolved from a small in-repo registry, so callers name intent not URLs.
- `budget.tokens` — soft/hard token ceiling (pairs with Phase 3 accounting).
- `budget.max_fetch` / `budget.wall_ms` / `budget.rounds` — already exist as env
  defaults; contract lets a job override *down* (never up past service ceiling).
- `redlines` — hard denials, e.g. `no_exploit_fetch` (block PoC/exploit-DB
  domains + suppress `deepen` queries that request exploitation steps).

**Enforcement points (code-grounded):**
- `harness.ts` gather loop → filter `searchWeb` / `fetchPage` against
  `sources.*` and `redlines`.
- `lib.ts::backstopDecision` → honor `budget.*` overrides (already hard-stops with
  a named reason; extend the reason set with `contract_budget` / `redline_block`).
- Echo the resolved contract verbatim into `research_jobs.result` (new
  `result.contract`) so the trace records *what the job was allowed to do*, not
  just what it did — the outline's "no traces, no trust" applied to scope.

**Invariant:** a contract can only *narrow* the service's standing limits. A job
cannot raise `max_fetch`, widen egress, or disable injection defense. Enforced in
a single `resolveContract(options)` helper (fail-closed on malformed contract).

**Not a decomposition refactor.** No component split; the engine stays monolithic.
The modularity win is that consumers (article mode, gap dives, agent-org advisory,
a future advisories monitor) become *the same engine under different contracts*.

---

## Phase 2 — Skeptic stage

> Build spec: `TASKS-phase2-skeptic.md`. Downgrade = tag rewrite before the curator
> parses tags→edges (`claims.ts:78`) → no curator/schema change for the core path.
> Defensive investment-gate with a bounded self-heal loop (drop bad source → gather
> more → re-evaluate), fully autonomous. **Independent of Phase 1** (stands on the
> existing gather + backstop). All-local (cloud deferred with Track B).

**Distinct from today's grounding checks.** Everything current verifies *support*
and is **self-reported at generation time**: the synthesizer assigns its own
`[SOURCED]` tags + `[Source N]` citations; cited-only promotion, grounding edges,
the `claim_confidence` function, and the ungrounded-claims backfiller are all
structural bookkeeping over those self-assigned tags. No stage re-reads a source
to confirm it says what the claim asserts, and nothing examines the synthesis as
an argument. (The curator's `detectConflicts` is reactive and cross-run — it
writes `contradicts` edges between *different* runs' near-claims; it is not an
adversarial review of the synthesis before it lands.)

**The Skeptic is goal-inverted and independent** — objective is to *refute*, not
write. In-stack precedent: the agent-org reviewer charter ("optimize to refute,
not bless; advisory, never self-approve"). Four axes it adds:
1. **Independence** — a separate pass; the tagger cannot skeptic itself (the
   subtly-wrong synthesis is the one whose own tags look right).
2. **Argument-level scrutiny** — source independence (all echoing one press
   release?), currency, severity inflation, does-the-conclusion-follow, what
   evidence would disconfirm.
3. **Disconfirming capability** — optionally one bounded gather round of *negated*
   queries. All current gathering is confirmatory (coverage-filling); nothing
   searches against the thesis.
4. **Independent existence check** — a spot-check tier ("quote the source span
   that supports claim N") is the one true claim↔source verification we lack.

**Outcome: a defensive investment-gate — AUTONOMOUS, no human in the loop.** The
Skeptic's job is *defense*: keep low-quality sources and ungrounded/fake claims
from being invested into Open Brain, with an audit trail of what was rejected.
Sources are abundant, so it self-heals — **bad source → drop it → gather more →
re-evaluate** — and an ungroundable claim is simply **not invested** (degrades to
honest `[UNCERTAIN]`/`[GAP]`, never fabricated). A bounded, terminating loop
(charged against the existing backstop, capped like the gap-dive drain) so it
cannot thrash the GPU or hang. **No human is ever in this loop** — Phase 4's gate
is a separate *downstream* concern at consequential outlets, not a step here.
Secondary effects fall out: a non-invested claim sits below the `0.50` reuse floor
(re-researched, not compounded); the reader hears the caveat; refutation-rate
trends as a quality sentinel. Audit splits: per-run `result.skeptic` (core, free);
a durable/queryable KB rejection record (skip known-bad sources next time) is
Phase 2b — recommended soon after core given the "for future use" intent. See
TASKS §Outcome.

**Build the output structured + quantified, not a boolean.** Emit each challenge
as a record (target claim/thesis · challenge type · evidence · a confidence
delta), not a pass/fail verdict. This is the forecasting hook (Track B): the
prediction record consumes the same `result.skeptic` field unchanged — the
Skeptic's confidence delta *becomes the forecast's probability driver*, its
challenges populate the forecast's decision packet (a Track-B/human concern —
never inside the autonomous research loop), and its disconfirming-gather queries
are the pattern re-run at horizon for hit/miss calibration. A boolean gate
would force a Track-B rework; the structured shape is what makes "build Phase 2
now, decide forecasting later" free. NB: in the research pipeline the Skeptic
reviews a single run's *synthesis*; in Track B it reviews the *clustered
prediction* — same role, different object, so cross-run clustering must exist
first (dependency: clustering → candidate forecast → Skeptic → probability + decision packet).

**Placement:** `harness.ts`, between synthesize and prose/persist. A local
`:nothink` judge pass over synthesis + sources, then the bounded self-heal loop
(drop flagged sources → re-gather → re-synthesize → re-judge), capped by the
existing backstop **and** a tunable `SKEPTIC_REGATHER_MAX` (default TBD — decided
later; `0` = judge-only). All local. (A cloud/model-diversity refutation variant —
running the skeptic on a stronger, differently-trained model — is a natural future
extension but is **deferred with Track B** and not part of this effort.)

---

## Phase 3 — Job-scoped spend attribution (nearly free)

> Build spec: `TASKS-phase3-attribution.md`. Two mechanisms: in-process `usage`
> accumulation → `result.tokens` (no cross-DB join) + lane-safe `user`-field
> stamping (`<lane>:job-<id>`). Includes the `agent-org` origin-coercion fix
> (the one additive schema migration in Phases 1–4).

**Goal:** a real token-cost line in every run's trace. Today `research_jobs`
records fetch counts + wall time but **no token cost**; LiteLLM *does* log per
request to `LiteLLM_SpendLogs` in `llm-gateway-db`, but those rows can't be joined
to a research job.

**Mechanism:** stamp the OpenAI `user` body field with a job-scoped value on
every `chat()` / embedding call, so spend-log rows join back to `research_jobs.id`.
No LiteLLM config change, no keys required.

**Lane-aware gotcha (must design around):** llm-queue lane classification
(`llm-queue/src/llm_queue/policy.py::_DEFAULT_CLASSES`) **substring-matches** the
`user`/key value. Today only `origin:"notebook"` sets `user="ob-research"` (rank-3
batch lane, 1800s acceptable wait); interactive origins are *deliberately*
unattributed to keep default-lane treatment. Naïvely stamping `user` on every
origin would shove interactive OWUI research into the 30-minute batch lane.
→ Keep the lane class as the **leading token**: `ob-research:job-<id>` for
notebook; a non-batch prefix (or explicit `LLM_QUEUE_POLICY_JSON` class) for
interactive origins. Substring match on `ob-research` still lands notebook jobs
in the right lane; the `:job-<id>` suffix survives to the spend ledger.

**Fold in the adjacent attribution bug:** `origin:"agent-org"` currently coerces
to `"owui"` (noted in the gap-dive audit) → agent-org advisory research lands in
the default lane. Same attribution-cleanup family; fix here.

**Relation to the parked keys plan (they compose, not compete):**
- Parked per-service **keys** plan = caller *identity* ("who") at the front door.
- This = per-*job* attribution ("which run"), all local.
Do the `user`-field stamping now; when keys unpark, identity stops being
self-asserted and the `:job-<id>` suffix rides underneath unchanged.
**This plan is not the vehicle for the keys plan and should not wait for it.**
Attribution is local-only; any future route (deferred) inherits it without rework.

---

## Phase 4 — Gate consequential outlets (not research itself)

> Build spec: `TASKS-phase4-outlet-gates.md`. Mostly agent-org-side wiring of
> existing gate primitives; carries one operator policy decision (which outlets
> count as consequential). Lands in agent-bridge, not the research image.

**Principle (matches the stack's own Idea Refinery stance — "the worth call stays
with the human"):** keep the engine autonomous. Research is read-only in effect
(it produces claims + emails); gating the loop would kill the flywheel. The
outline's terminal human gate belongs at **consequential use**, i.e. where
research output *steers action* — today that's agent-org consuming grounded claims
to steer efforts.

**Reuse existing fail-closed machinery — build nothing new:**
- Approvals MCP (`scripts/claude-sessions-bridge/approval_server.py`) — in-thread
  Mattermost approve/deny, FAIL-CLOSED, JSONL audit, `follow_thread` auto-wake.
- `agent-bridge/app/modules/pending_store.py` — durable pending-approval mirror
  (survives restart).
- `governance_gate.py` — freeze/escalation FSM: no timeout auto-resume, authority
  separation, default-deny on unknown state.

**Where the gate fires:** when a research result is about to drive a
consequential downstream action, or (ties to Phase 1) when a **chained** research
job requests scope outside its contract — that scope-expansion moment is the
approval, not the research itself.

**Ideas→Mattermost is the exemplar to replicate:** autonomous research → human
re-engaged in `#ideas` as *decider* → promotion explicit, never automatic.

---

## Track B — Forecasting (NEW capability; PARKED — undefined integration)

> Status 2026-08-05 (operator DECISION): **deferred as a future feature — do not
> build until a clearer use-case exists in the overall stack.** Forecasting is an
> interest, but *how it connects to the ai-stack is not yet defined*, and the
> blocker is NOT technical feasibility (the layering below already lands cleanly) —
> it is **mission + consumer definition**. This section stays a parked design note
> and is explicitly out of the active plan; revisit only when a concrete stack
> use-case surfaces. Phases 1–4 do not depend on it.

**The undefined questions (what "connects to the ai-stack" means here):**
- **Domain/mission** — the source talk is *cybersecurity threat* forecasting on a
  30–90d horizon. This stack's research service is *general-purpose* (digest
  topics, advisory grounding, idea refinement). Forecasting needs a declared
  subject: is it security horizon-scanning, market/tech signal (cf.
  `weekly-signal-diff` skill), or forecasts over the operator's own domains?
- **Consumer** — who acts on a forecast? Candidates already in the stack: the
  daily digest/podcast (a "forward-looking" brief section), agent-org (a forecast
  that spawns a prep effort), the Idea Refinery (`#ideas` as decision surface), or
  a standalone operator-facing brief. Nothing consumes probabilistic outputs today.
- **Trigger** — on-demand vs. an always-on scan loop. An always-on scanner is new
  infrastructure (a scheduled cross-run clustering pass), unlike the current
  on-demand + nightly-chain model.

**Once mission + consumer are chosen, the technical layering (already scoped):**
- **Prediction record schema** (new): evidence observed · assumptions · prediction
  + horizon (30/90d) · skeptic's disconfirming points (Phase 2 feeds this
  directly) · human decision.
- **Cross-run signal clustering** (new) — the one genuinely missing analytic; the
  current synthesizer is per-query, not cross-run trend/weak-signal.
- **Horizon revalidation = hit/miss checker** — the claims schema already carries
  `volatility` / `revalidate_days` / `researched_on`; a revalidation scheduler
  doubles as the calibration ledger at horizon.
- **Delivery** — the daily digest chain is exactly the outline's "daily/weekly
  brief" vehicle; `research_run_metrics` is the precedent trend view.

**Note:** Phases 1–4 are forecasting-agnostic and do not depend on this decision.
The Skeptic (Phase 2) is the only piece that later *feeds* forecasting (its
disconfirming points populate the prediction record), so building Phase 2 keeps
the forecasting option open at zero extra cost.

## Track C — Generalize the research-debt drain (later; only if stack-wide)

The gap-dive plan (built 2026-08-05) is already the outline's "research triggering
deeper research" — but in the **safe shape**: signals → ledger → *budgeted
scheduled drain*, not event-chained session-spawns-session (which is what sprawls).
Three convergent precedents share this shape: gap-dive ledger, grounding
backfiller (`limit=50` + skip-stamp), Idea Refinery owed-research drain (3
attempts). To generalize stack-wide (any producer can *owe* research, one drain
worker services the debt under one nightly budget):
- Promote the `/reports/gap-dives-pending.json` ledger to a DB table (converges
  with the `ideas`/`idea_revisions` mechanic).
- Add an explicit `depth` field with a **hard cap** — dives are implicitly
  depth-1; a gap-of-a-gap is depth-2 and stops. Never direct session→session.
- Keep `LIMIT` / `MAX_ATTEMPTS` discipline (no retry storms on unanswerable
  questions).
**Recommendation:** let the digest-scoped gap-dive version run and prove the
budget/attempt discipline *before* widening the funnel.

---

## What the outline's §11 open questions map to (already answered here)

- Budget enforcement → hard-stop with honest `[GAP]` degradation (`backstopDecision`).
- Persistent state → claims KB + `research_jobs`.
- Escalation semantics → `governance_gate` freeze-FSM (no auto-resume, authority sep).
- Runner selection → moot; the Deno harness *is* the runner (the garbled
  "Lobster/OpenCloud" name never needs resolving).

## Change surface summary (Phases 1–4)

| Phase | Primary files | New container? | Schema change? |
|---|---|---|---|
| 1 Contract | `harness.ts`, `lib.ts` (`backstopDecision`), new `resolveContract`, source-class registry; `result.contract` | No | No (options/result are jsonb) |
| 2 Skeptic (core) | `harness.ts` (judge + self-heal loop + `SKEPTIC_SYS`), reuse gather/`screenSources`; `result.skeptic` (per-run audit) | No | **No** (core) |
| 2b Durable audit | curator consumes verdict: `refuted`/quarantine state + screened-out source marker | No | Additive (`claims.ts` + `init-claims.sql`) |
| 3 Attribution | `harness.ts`/`kb.ts` chat call sites (`user` field); `policy.py` lane-prefix awareness; origin-coercion fix | No | No |
| 4 Outlet gates | agent-org consumer paths; reuse approvals MCP + `pending_store` | No | No |
