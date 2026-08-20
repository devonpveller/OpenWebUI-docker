# Product-Discovery Loop (PDL) — market-grounded autonomous product iteration

**Status:** 📐 **design (2026-07-06, realigned to code 2026-07-07)** — a lifecycle EXTENSION that
turns the agent-org from *reactive* (fix what the operator reports) into *generative* (drive a
product toward market fit). The PDL machinery itself is not built yet, but the **reuse surface it
stands on is now LIVE + DEPLOYED** (see the realignment note). **Precedence:** governance spec >
this doc > PLAN/TASKS. Where they touch, this doc obeys `SAFETY-AND-WORKFLOW-governance-model.md`
and the floor unchanged.

> **Realignment note (2026-07-07) — the ground the plan stands on has firmed up.** Since this doc
> was drafted, the 2026-07-05/06 convergence work shipped and, crucially, two mechanisms landed that
> the plan treated as *to-build* but are now **live precedents**:
> - **`Project.standing_intent`** (models.py:341, `projects.set_standing_intent`, NL-settable via
>   `OperatorIntent.standing_intent`) — a **project-level persistent objective** injected into
>   *every* effort goal as a NON-NEGOTIABLE preamble (`orchestrator._standing_intent_context`) with
>   **forbidden-term rejection** (`_forbidden_terms` — "I will REJECT any delivery whose diff
>   introduces `X`"). *This is the north-star alignment mechanism of §2.1/§8, already running.* It
>   was built to stop the murder→NuGet architectural drift; the PDL's north star is a richer
>   standing_intent reusing this exact injection+rejection path.
> - **`_apply_note`** (the "try it locally" block on every landed delivery), **`check_cmd`
>   NL-settable + auto-derived** (`_extract_check_cmd` / `_derive_check_cmd`), **PR/branch hygiene**
>   (`close_pull_request`, `delete_branch`, `read_sibling_agent_prs`), and **hot-swappable worker
>   env templates** (`dotnet8` live) — these are exactly the batch-digest, mandatory-test-gate,
>   feature-removal, and real-build-in-CI pieces the plan needs, now built.
>
> Net effect: the plan is **more de-risked, not less** — the hardest governance piece (direction in
> every unit + drift rejection) is proven live, and the "back half" is fully reuse. What remains
> genuinely new narrows to the **rich product aggregate** (§3) + the **recurring driver** (§6).

**Operator decisions locked (2026-07-06):**
1. **Maximally autonomous, but feature-modular.** The loop runs continuously without per-iteration
   human review; the organizing constraint is that the product is a **composition of independently
   removable features** — "remove a feature" must be as simple as reverting its merge + re-wiring
   (§3.5 Feature Ledger). This is the load-bearing architectural requirement.
2. **The north-star / charter phase (Movement 1) is where the value is anchored** — clarity there
   is the whole game; invest in it (§2 CHARTER, §10 OD-PDL-5).
3. **The human is left out until a *batch* review of several features** — NOT per iteration. This
   makes **autonomous testing + validation the gate, not human eyeballs**: the operator will not
   review buggy code to hand-hold fixes that the org should catch itself. → auto-integrate to the
   dev branch is **default ON**, gated by a **mandatory real test** (D2) + differently-goaled
   review (D3); the human reviews the accumulated dev branch at a batch cadence and gates the
   `develop → main` release (§7).

**Companions:** [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md)
(§3 gate, §4 hard rules) · [UX-FLOW.md](UX-FLOW.md) (Stages 0-6 — the PDL feeds Stage 0 and obeys
Stages 2-3) · [DELIVERY-PIPELINE.md](DELIVERY-PIPELINE.md) (D0-D6 — the PDL's back half is reuse) ·
[`agent-org/IMPLEMENTATION-NOTES.md`](../../../agent-org/IMPLEMENTATION-NOTES.md).

---

## 0. What this builds and why

The operator's goal: **"work with the PM to get a good idea of what the north-star product idea
is, grounded against the existing market — then execute, iterating until every gap against the
market is satisfied."** Two movements:

- **Movement 1 — converge to a *solid* idea (human ⟷ PM).** The operator and PM co-refine a
  north-star into a durable **Product Charter**; a grounded, cited **Market Brief** validates
  demand and marketability; if the market says "not viable here," the operator pivots and
  re-researches until the idea is solid. Human-collaborative; ends on a human "it's solid" gate.
- **Movement 2 — autonomous build loop (until market gaps are satisfied).** A **Gap Engine**
  (market brief × actual codebase) produces a ranked, deduped **Gap Backlog**; each top gap
  becomes a grounded **PRD**; the PRD runs the existing implement → test → prove pipeline; on a
  green check it integrates to a **development branch**; then the loop re-grounds and continues.
  Terminates on backlog-exhaustion, budget, or a human stop.

**The elegant part:** every reliability mechanism hardened during the 2026-07-05/06 convergence
work — verification contracts, session rotation, gitlink/empty-diff gates, composition context,
per-error verdicts, delivery verification — is exactly what makes an *unattended* loop safe. An
autonomous loop amplifies every reliability gap; those gaps are now closed, so the loop is finally
possible. The PDL is the payoff.

**What is genuinely new** is the *front* of the pipeline (Charter, Market Brief, Gap Engine, PRD)
and the *loop control* (a persisted, budget-capped, checkpoint-cadenced driver — "the PM keeps
workers on task"). The *back* (effort → worker → delivery → merge) and all gates are reuse.

---

## 1. The two missing seams (why this is a real build, not a wiring job)

The seam map (realigned 2026-07-07) leaves **one-and-a-half** structural gaps everything else hangs
off:

1. **A LIGHTWEIGHT project-level objective now exists; the RICH product aggregate does not.** As of
   2026-07-07 `Project.standing_intent` (`models.py:341`) persists a per-project durable
   objective/constraint, injected into every effort (`_standing_intent_context`) and enforced by
   forbidden-term rejection — so the *"a durable product-level intent, present in every unit"*
   primitive is **already live** (it's a single guardrail string, not a versioned charter). What is
   still missing is the **rich aggregate** a loop needs: a versioned north-star charter + market
   brief + ranked gap backlog + feature ledger, keyed per product. → **New: the `ProductLoop`
   aggregate (§3), which subsumes/relates to `standing_intent` — the north star is set as (and
   enforced like) a richer standing_intent, reusing the live injection+rejection path.**
2. **No recurring / self-clocked driver exists.** All orchestration is event-driven; the only
   long-running loops are `_capacity_drain_loop` (`orchestrator.py:570`, event+timer) and the
   event-gateway poll. There is no cron/schedule primitive. → **New: `_discovery_driver_loop`
   (§6), modeled on the drain loop's event+timer shape, durable via a `LoopStore` that mirrors
   `ParkStore` (`capacity_park.py`).**

Everything else — research, planning, delivery, all reliability gates, north-star injection,
apply-notes, check_cmd, PR hygiene, worker env templates — is **reuse, and now LIVE**.

---

## 1.5 Engagement — the PM as Operation Router (how this is invoked)

**There is no "start the discovery loop" command to memorize.** The PDL is engaged through the
*same* natural-language surface as everything else: the operator says what they want in plain
language, and the PM decides **dynamically** what operation the goal needs — the operator's
"tool-calling" framing (2026-07-06). This is the existing architecture taken to its conclusion:
`nl_intake` already routes an operator message through a structured-output classifier
(`OperatorIntent.kind` / `_PO_NL_SYS`) to a governed handler. The PDL **adds operations to that
router**; it does not add a mode the operator has to select.

**The operation family** (the PM picks one per prompt; all share the product context + substrate):

| The operator says (examples) | Operation | Shape | Shares |
|---|---|---|---|
| "fix this build error …" | `fix` | bounded — error-report intake (LIVE) | delivery + gates |
| "add a &lt;feature&gt;" | `add_feature` | bounded — one ledgered feature: PRD→effort→test→dev-merge→ledger | ledger, delivery |
| "keep adding features toward &lt;north star&gt; until it's done" | `discovery_loop` | **continuous** — the PDL (§2) | north star, brief, ledger, gaps |
| "where are we / summarize the product / this effort" | `summarize` | bounded — digest of loop / effort / ledger state | ledger, north star |
| "what gaps exist vs the market" | `gap_report` | bounded — run the gap engine ONCE, report (no auto-build) | brief, ledger, north star |
| "research / explore &lt;topic&gt;" | `explore` | bounded — a research session (advisory, broadened) | research |
| "is this still aligned with the north star / check drift" | `align_check` | bounded — audit work + ledger vs the north star (§2.1) | north star, ledger |
| "audit &lt;area&gt; for security / quality / perf" | `audit` | bounded — differently-goaled review over existing code | review gates |
| "clean up / refactor &lt;module&gt;" | `refactor` | bounded — a refactor session, tests as the gate | delivery + gates |
| "remove the &lt;feature&gt;" | `feature_remove` | bounded — revert merge + unwire (§3.5) | ledger |

**Shared product context** (why these compose instead of being ten disconnected tools):
- **north star** — the product intent thread (§2.1); read by loop / add_feature / gap_report /
  align_check / summarize;
- **market brief** (§4) — written by loop / gap_report, read by every gap operation;
- **feature ledger** (§3.5) — written by add_feature / loop, read by summarize / align_check /
  feature_remove;
- **the substrate** — efforts, workers, DELIVERY-PIPELINE D0-D6, research, and every reliability
  gate — used by any operation that ships code.

**Why this is the right engagement model (and solves the operator's live pain):**
- **No mode-selection burden.** The operator never has to know "is this a fix or a feature or a
  loop?" — they describe the goal; the PM routes, and *says which operation it chose and why*
  (thinking-partner), so a misroute is corrected in one reply ("no, just the one feature"). This is
  the NL-first principle (all inlets stem from NL; slash commands are a power-user fallback) applied
  to *operations*, not just admin verbs.
- **The classifier is already hardened for exactly this.** The 2026-07-05/06 intake work
  (deterministic scoped repair, work-cue gates, project grounding, "names come from the operator's
  literal words") is the robustness a dynamic operation router needs — the router *is* the
  classifier, now with a richer operation set and the same deterministic fallbacks.
- **One-shot vs continuous is the PM's call, transparently.** "add a feature" runs once and stops;
  "keep adding features" starts the loop. Ambiguous → the PM asks (F5), never guesses into an
  unbounded loop.
- **Operations compose over shared state.** "add a feature" → "summarize" → "audit the auth" →
  "start the loop toward &lt;north star&gt;": each reads/writes the same ledger + north star, so the
  product is one coherent thing, not a pile of disconnected commands.

**Build implication:** the Operation Router is the **foundational** piece — it is the entry point
for every operation AND it immediately improves the *current* experience (sharper routing of
today's fix / feature / advisory intents, fewer misroutes). So PDL.0 leads with it: extend
`OperatorIntent` into the operation family + the dynamic dispatcher, alongside the product-context
persistence the operations share. Governance is unchanged — every operation that ships code still
passes Stages 2-3 and the delivery gates; the router only decides *which* governed operation runs.

---

## 2. Machine C — the discovery driver (alongside A and B)

Governance §3.0 already defines two orthogonal FSMs: **machine A** (the governance gate —
`frozen`) and **machine B** (the scheduler — `computing/waiting/suspended`). The PDL adds
**machine C: the discovery driver** — a per-product, persisted phase FSM that *generates* work and
feeds it into Stage 0 intake, then monitors delivery and re-grounds. C never bypasses A or B: every
PRD it emits is an ordinary effort subject to the readiness gate, the execution gate, the delivery
gates, and the floor.

```
IDLE
 │  operator: "start a product loop on <project>: <north-star idea>"
 ▼
CHARTER_DRAFT ─────────────── human ⟷ PM advisory refinement (reuse _advise / thinking-partner)
 │  human: "the north-star is solid — research the market"
 ▼
MARKET_RESEARCH ───────────── grounding.advise-style call (origin="agent-org-market")
 │                            → persist a cited Market Brief
 ├─ brief: weak demand / not viable → CHARTER_DRAFT   (PIVOT — human decides)
 │  human: "proceed to build"
 ▼
GAP_ANALYSIS ──────────────── Gap Engine: Market Brief × codebase inventory
 │                            → ranked, deduped Gap Backlog
 ├─ backlog empty / all-deferred ──────────────────── COMPLETE (report; reopen w/ fresh research)
 ├─ budget hit / checkpoint cadence ───────────────── PAUSED_CHECKPOINT (human digest + review)
 ▼
PRD_DRAFT ─────────────────── top gap → structured PRD (Stage-2 readiness gate; Stage-3 approval
 │                            per cadence). Acceptance criteria → the D2 check.
 ▼
IMPLEMENT ─────────────────── PRD → effort → delegate → DELIVERY-PIPELINE D0-D6 (ALL gates reused)
 │                            D2 green → integrate to the DEV branch (§7, opt-in)
 ├─ stall / fail / D2-red-withdrawn ──────────────── PAUSED_CHECKPOINT (escalate, human)
 ▼
(merged) → GAP_ANALYSIS       re-ground: the codebase changed, so the backlog shrinks
```

The driver cycle is **GAP_ANALYSIS → PRD_DRAFT → IMPLEMENT → (merge) → GAP_ANALYSIS**. Market
research runs once per north-star (re-run on pivot). The charter phase is the human-collaborative
front. `COMPLETE` = the codebase satisfies the market brief's demanded capabilities (within the
loop's scope + budget), not "the software is perfect."

### 2.1 The north star is the product-level intent thread — present in EVERY gap (operator clarification 2026-07-06)

The north star is **not** a charter-phase artifact consumed once and discarded. It is the
**product-level intent thread** — UX-FLOW §0's living statement of *what the user wants and why* and
governance §4.3's **canonical objective** — raised from per-effort to per-product scope. It rides
along through *every* gap, PRD, and effort, exactly as the intent thread today "rides along as each
worker's grounded goal" (UX-FLOW §2).

This is the loop's **alignment mechanism**, and it is not optional: the ICLR paper's #1 failure is
**compartmentalization** (F1) — sub-agents assigned narrow tasks "proceed with contributing" while
losing the whole-task view; the governance answer is that the canonical objective is baked into
every sub-goal and *"tunnel-vision drift from the canonical objective is precisely the drift we're
hunting."* An autonomous gap engine is the sharpest possible version of this risk: the *market* can
demand many things, and without the north star present at every gap, the loop would chase market
signal off-direction. So:

- **Every gap is grounded in BOTH the market brief AND the north star.** A gap is valid only when it
  serves a cited market need *and* advances the north star. A market-attractive gap that does not
  serve the north star is **dropped or flagged for a pivot decision** (§4) — never silently built.
  Each `product_gaps` row records `serves_north_star` (the justification) alongside `market_need`.
- **The north star is baked into every PRD → every effort goal** (the §4.3 discipline) — and this is
  **already the live mechanism**: `_standing_intent_context` (orchestrator.py:2773) injects the
  project's durable objective into every effort as a NON-NEGOTIABLE preamble, and `_forbidden_terms`
  (orchestrator.py:2758) **rejects any delivery whose diff violates it** (it was built precisely to
  stop the murder→NuGet architectural drift). The PDL sets the north star *as* the project's
  standing intent, so "direction present + drift rejected in every unit" is not new code — it is the
  existing path fed a richer objective.
- **The driver DEFENDS the north star** exactly as the UX defends the intent thread and as
  `standing_intent` already defends the architecture: at each gap and PRD the question is *"does this
  still serve the north star, and if not, should the north star change?"* — a fork surfaces to the
  human (pivot), never a silent drift. The north star only changes by human decision (learning-loop
  proposes, never auto-applies — floor #6).

In short: **the north star is the direction; the market brief is the terrain.** The loop navigates
the terrain *toward* the direction; a gap that is terrain-but-not-direction is a pivot question for
the human, not autonomous work.

---

## 3. New persistent state — the `ProductLoop` aggregate

A dedicated table (NOT columns on `Project` — the loop's state is too rich, and `Project` stays
the repo/channel identity). `ProductLoop` references a project the way `Effort` does.

```
product_loops
  id                TEXT PK              # loop-<project-slug>
  project           TEXT FK projects     # the repo this loop builds
  phase             TEXT                 # charter|market|gap|prd|implement|paused|complete
  north_star        TEXT                 # the versioned charter statement (append-only history)
  charter_version   INT
  market_brief      JSONB                # {synthesis, cited_sources[], demanded_capabilities[],
                                         #  viability, marketability_risks[]} — versioned per pivot
  budget_tokens     INT | NULL           # hard ceiling; NULL = human-ack every iteration
  spent_tokens      INT
  checkpoint_every  INT                  # gaps delivered per human checkpoint (cadence dial)
  since_checkpoint  INT
  iteration         INT
  current_gap_id    TEXT | NULL
  current_effort_id TEXT | NULL
  auto_integrate    BOOL                 # dev-branch auto-merge on green (§7); default TRUE (decision 3)
  created_by/at, updated_at

product_gaps
  id                TEXT PK               # gap-<loop>-<n>
  loop_id           TEXT FK product_loops
  title             TEXT
  market_need       TEXT                  # what the market demands (CITED from the brief)
  serves_north_star TEXT                  # HOW this gap advances the north star (§2.1 — required;
                                          # a gap that can't justify this is dropped/flagged, not built)
  code_state        TEXT                  # what the codebase provides today (evidence)
  impact            INT                   # rank by (north-star advancement × consumer impact), 1..5
  status            TEXT                  # open|prd|in_progress|delivered|deferred|off_direction
  effort_id         TEXT | NULL           # the effort that implements it
  created_at/updated_at
```

Durability discipline (learned 2026-07-04, the pending-approval-persistence fix): the loop is
**DB-persisted and rehydrated in `setup()`** — a bridge restart must never silently drop a running
product loop. `LoopStore` mirrors `ParkStore` (`capacity_park.py`): `create / advance / set_gap /
spend / checkpoint / all_active`.

**Relation to the live `Project.standing_intent` (2026-07-07):** the `north_star` is not a parallel
concept — when a loop's charter is set/updated, the bridge also writes it (or its enforceable core)
to `Project.standing_intent` via the existing `projects.set_standing_intent`, so **every effort the
loop spawns already carries the direction + drift-rejection through the live
`_standing_intent_context` path**, no new injection code. The `ProductLoop` row adds what
standing_intent can't hold: the versioned charter history, the market brief, the gap backlog, and
the feature ledger.

---

## 3.5 The Feature Ledger — the product as removable units (decision 1)

The operator's load-bearing requirement: **"remove a feature = remove the merge, then reimplement
the removed wiring."** That is only trivial if every feature is a *bounded, merge-tracked,
wiring-aware* unit. So a delivered gap doesn't just vanish into the codebase — it becomes a
**Feature**, the atomic unit of the product, recorded in a ledger:

```
product_features
  id                  TEXT PK           # feat-<loop>-<slug>
  loop_id             FK product_loops
  gap_id              FK product_gaps    # the market gap it closes
  title
  prd                 JSONB             # the PRD it was built from (re-implementable verbatim)
  merge_commit        TEXT              # the dev-branch merge commit → the REVERT TARGET for removal
  integration_points  JSONB             # where it wires into the rest: files, registrations,
                                        # call-sites, config/flags — the worker DECLARES these
  depends_on          JSONB             # feature ids this one builds on (removal-safety DAG)
  status              TEXT              # active | removed
  created_at/updated_at
```

**Removal is a first-class NL operation** — `"remove the <feature>"` (its NL-routing + the git ops
are **already live**: `_nl_branch_delete` + `delete_branch` and the revert-based recovery paths
exist; removal wires them to the ledger):
1. `git revert <merge_commit>` on the dev branch (reversible — the whole reason dev-merge is safe);
2. emit a small **unwire effort**: repair the `integration_points` elsewhere that referenced it,
   and flag/handle any `depends_on` dependents — this is the "reimplement the removed wiring" step,
   made deterministic instead of archaeology;
3. mark the feature `removed` but KEEP its `prd`/`gap`, so `"re-add the <feature>"` re-applies the
   PRD cleanly later.

**What keeps features removable** (the disciplines that make the ledger true, all reused):
- **Minimal-diff PRDs** (`out_of_scope[]`, the 2026-07-06 "fix ≠ redesign" rule) keep each feature
  bounded — no sprawl across the tree.
- **Declared wiring**: the worker's delivery must end with a structured `WIRING:` block naming every
  integration point it added — a direct generalization of the composition/gitlink wiring we already
  emit. That declaration *is* the removal map; a missing/partial block closes the effort partly
  done (same discipline as ACCEPTANCE VERDICTS).
- **Additive-seam preference**: the gap engine + PRD generator are prompted to favor
  registration/plugin/flag/adapter seams over scattered edits, so a feature plugs in at a boundary
  and unplugs by revert + minimal unwire — not a rewrite.
- **Dependency DAG**: `depends_on` (reusing the scheduler's DAG idea) means removal warns about and
  sequences dependents.

**The ledger IS the product's state versus the market:** `COMPLETE` = every market gap is an
`active` Feature or an explicitly `deferred` gap. "What does this product do, and why?" is answered
by the ledger + each feature's citing gap — auditable, and reversible one feature at a time.

---

## 4. The Gap Engine (the biggest new piece — "research service, two-sided")

Input A — **market demand** (from the Market Brief: the cited capabilities consumers expect and
competitors offer, and where the north-star satisfies vs misses demand).
Input B — **codebase reality** — three cheap sources, all existing:
  - `read_branch_delivery`/`read_repo_state` for structure (`.gitmodules`, tree);
  - the **repo-sync knowledge already in Open Brain** (RS.2 — every onboarded repo's docs are
    ingested as grounded sources at merge/onboard time, `_repo_sync`);
  - a worker-run **project survey** (the existing `__survey__` effort pattern) for a live
    capability inventory.
Output — a **ranked, deduped Gap Backlog**: each gap = {market_need (cited), code_state (evidence),
delta, consumer-impact, effort estimate}.

**Two build options (open decision OD-PDL-2):**
- **(b, recommended MVP)** bridge-side grounded synthesis: `models.structured(<lane>, _GAP_SYS,
  {brief, inventory})` with the brief's cited claims + the repo inventory injected as context —
  reuses the research *output* (the brief) rather than a new service endpoint; in-repo, fake-
  testable. Dedup against `product_gaps.status='delivered'` (the reconciling-planner lesson: never
  re-emit a satisfied gap).
- **(a, future upgrade)** a new research-service endpoint (`POST /gap-analysis`) that runs the
  full multi-source grounding harness server-side. Richer, but a cross-repo change (the service
  lives in the external `OB1/integrations/research-service/`).

Grounding discipline is non-negotiable here (F7/injection defense): every gap must cite the brief;
an ungrounded "the market wants X" is dropped, not shipped. This is a **judgment** task — see the
weak-model risk in §8.

**Two-axis grounding (§2.1):** the gap engine is given the **north star** alongside the brief +
inventory, and each emitted gap must fill BOTH `market_need` (cited) AND `serves_north_star`
(justified). The synthesis prompt scores gaps on *north-star advancement × consumer impact*, and
**quarantines** any market-attractive gap that can't justify serving the north star into
`status='off_direction'` — surfaced to the human as a pivot question ("the market wants X, which is
off your current direction — expand the north star, or skip it?"), never autonomously built. This
is the F1/§4.3 drift defense made mechanical at the exact point drift would enter.

---

## 5. The PRD — a structured, grounded, verifiable goal

For the top gap, synthesize a PRD (new `PRD` schema, `models.structured`):

```
north_star         # the product direction this serves (§2.1 — carried into the effort goal, always
                   # present so the worker never optimizes the local task off-direction; F1/§4.3)
problem            # the market need, CITED from the brief
target_user
requirements[]     # what to build (grounded in the gap's delta)
acceptance[]       # testable criteria  → become the D2 check + ACCEPTANCE VERDICTS
out_of_scope[]     # minimal-diff discipline (the 2026-07-06 "error fix ≠ redesign" rule)
success_metric     # how we'll know it advanced the north star
```

The PRD becomes the effort's goal via `charters.set_goal(effort_id, prd_rendered)`. The `north_star`
reaches every wake through the **already-live** `_standing_intent_context` injection (§2.1) rather
than a new goal-header field, and its `acceptance[]` drives verification — generalizing the **live
ERROR VERDICTS** protocol (orchestrator.py:110) into **ACCEPTANCE VERDICTS** (the one genuinely new
verdict variant): the worker must end with each acceptance criterion marked MET / NOT MET with
evidence, and a NOT-MET (or missing block) closes the effort *partly done* rather than inviting a
merge — identical machinery to the shipped ERROR VERDICTS path. `out_of_scope[]` carries the
minimal-diff instruction (the live "fix ≠ redesign" rule) so a PRD for feature X can't balloon into
a redesign. This is direct reuse of the convergence machinery, now all deployed.

PRDs pass the existing gates: Stage-2 readiness gate (`planner.readiness_gate` — ambiguous PRD →
clarifying questions, not a guess, F5), and Stage-3 plan approval (`_present_plan` /
`approve_effort_plan`) at the loop's checkpoint cadence.

---

## 6. The driver loop (`_discovery_driver_loop`)

Modeled on `_capacity_drain_loop` (`orchestrator.py:570`): an `asyncio` task, event-driven with a
timer fallback (research/gap phases are async-poll like `grounding.advise`, grounding.py:128).
Started in `setup()`, rehydrated from `LoopStore`. Per active loop, per tick:

- **advance the phase FSM** (§2); each transition posts to the loop's effort thread (bus-only,
  observable — floor #5);
- **on IMPLEMENT completion** (a `finish`/merge event fires the driver — reuse the
  `scheduler.wake_finished` / `on_release` signal wiring): mark the gap `delivered`, re-enter
  GAP_ANALYSIS (re-ground — the codebase changed);
- **budget**: every research/gap/PRD/effort call debits `spent_tokens`; at the ceiling → phase
  `paused` + a human digest (never a silent stop — the "no silent caps" rule);
- **checkpoint cadence**: after `checkpoint_every` gaps delivered → `paused` + digest + await
  "continue";
- **convergence**: track backlog size across iterations; empty → COMPLETE; *not decreasing* over
  K iterations (thrash) → escalate (a loop that isn't converging is a defect, not progress);
- **backpressure**: respect the shared-GPU source-guard (`capacity_source_guard_s`) and park-on-
  backpressure (reuse `_park_effort`) — an autonomous loop must never self-DoS the research engine
  or the inference plane;
- **kill/pause**: the global kill switch freezes it; a per-loop "pause the loop" NL control
  suspends it; both durable.

---

## 7. Two-tier merge — the one place the loop touches the irreversibility line

Today all merges are human-gated to `main` (floor #4; DELIVERY-PIPELINE D4). The operator wants
auto-integration to a **development branch** after testing/proving. Governance reconciliation:

- A merge into a **non-release integration branch is REVERSIBLE** (revert / branch delete) and is
  NOT a release or deploy — the same reasoning that reclassified feature-branch *push* as
  "additive/routine" (DELIVERY-PIPELINE §0's framing-error correction). So a dev-branch merge *can*
  be governed as routine-additive — but only under strict conditions, because a standing blanket
  auto-merge grant is exactly the scope-creep the floor guards against (#3, #4).
- **Locked posture (decision 3):** dev-branch auto-merge is **default ON** — the operator will not
  review every iteration, so **an autonomous test gate replaces per-merge human review.** Autonomy
  is *earned by a real gate*, not assumed: auto-merge to `Project.dev_branch` (new column, e.g.
  `develop`) fires **only** when ALL hold — (i) the project has a **real `check_cmd`** and D2 is
  **GREEN** (`_d2_gate`; a loop feature with NO check_cmd CANNOT auto-merge — it falls back to
  human-ack, so "no test ⇒ no autonomy"); (ii) every PRD **ACCEPTANCE VERDICT** is MET; (iii) the
  differently-goaled **D3 review** (autonomous, already advisory-to-PM) raises no blocking concern;
  (iv) the target is the designated dev branch — never `main`.
- **`main`/release stays hard-gated (floor #4 untouched).** The human's review happens at a **batch
  cadence** (`checkpoint_every` *features*, not iterations): the loop accumulates green, tested,
  ledgered features on `develop`; at the cadence it posts a **batch digest** (the features added,
  each with its market gap + acceptance evidence + how to try it) and the human gates the
  `develop → main` **release**. This is the single irreversible human decision, now at the
  granularity the operator actually has bandwidth for — a batch of proven features, not raw diffs.

This keeps floor #4 intact at the true irreversible boundary while letting the loop run
continuously, and it makes the test/validation gate — not human patience — the thing that stops
buggy code from reaching a review. If a check is red or an acceptance verdict is NOT MET, the
feature never merges; it routes back to the worker (existing D2 loop) or escalates — the human only
ever sees green, ledgered work.

**What is already live for this (2026-07-07):** the mandatory-test gate has all its pieces —
`check_cmd` is NL-settable ("before merging, make sure X builds") *and* auto-derived from a pasted
repro (`_extract_check_cmd` / `_derive_check_cmd`), `_d2_gate` runs it and red-routes-back, and the
**worker env templates** (`dotnet8` hot-swapped onto the ao-ot sidecars) mean the check can be a
*real* build/test, not a stub. The **batch digest** reuses `_apply_note` (the "try it locally"
block already appended to every landed delivery: fetch/checkout + submodule sync + the project's own
check). So §7 is mostly *wiring live parts together*, not new machinery — the genuinely-new bit is
`Project.dev_branch` + the two-tier merge target (PDL.6).

---

## 8. Governance & safety (the non-negotiables)

All from `SAFETY-AND-WORKFLOW-governance-model.md`, unchanged:

- **North star present in every gap (F1 / §4.3) — the alignment spine (§2.1), and ALREADY LIVE as a
  pattern:** the product-level intent thread is carried into every gap, PRD, and effort goal via the
  existing `_standing_intent_context` injection, and a delivery that violates it is **already
  rejected** by `_forbidden_terms` (built to stop the murder→NuGet drift). A gap that serves the
  market but not the north star is quarantined `off_direction` and surfaced as a pivot question,
  never autonomously built. This is the direct defense against the paper's #1 failure
  (compartmentalized sub-agents drifting from the whole-task view) — proven live, and what keeps
  *maximum autonomy* on-course.
- **Escalate-don't-guess (F5):** an ambiguous gap or PRD → clarifying question to the human, never
  a guess. The readiness gate already enforces this per effort.
- **No self-granted scope (#3):** the loop cannot widen its own repo access, egress, or budget. A
  gap that needs a new dependency/host → PROPOSE, human clears.
- **Irreversible line (#4):** `main`/release merge + deploy stay human-gated; dev-integration is the
  one operator-opted-in refinement (§7).
- **Bus-only + observable (#5):** every research query, gap, PRD, and result posts to the loop's
  effort thread; the whole loop is auditable; refusals/escalations are never rate-capped or dropped.
- **Learning-loop proposes, never auto-applies (#6):** if new market signal suggests changing the
  north-star, the loop PROPOSES a pivot; the human decides. The loop never rewrites its own charter.
- **Weak-model risk is ACUTE here (F7 / P0.5).** Gap analysis, PRD synthesis, and the "is it solid /
  is it viable" judgment are the highest-stakes-for-weak-models operations in the whole system —
  they set what gets built. **Recommendation (OD-PDL-3):** run these three *judgment* operations on
  the **cloud lane** (`lane: cloud` profiles already exist); workers stay local. At minimum,
  adversarially verify a gap backlog before spending effort on it (the review-panel pattern).
- **Termination is a safety property, not a nicety:** a loop with no convergence detection + budget
  ceiling + checkpoint cadence is a runaway. All three are mandatory (§6).

---

## 9. Phased build (each deployable, tested, reversible — the workspace convention)

> **Realignment (2026-07-07):** the back half (implement → test → prove → deliver) and the
> alignment spine are **live** — north-star injection = `standing_intent`; the test gate =
> `check_cmd` (NL/auto) + `_d2_gate` + env templates; verdicts = ERROR VERDICTS (→ ACCEPTANCE
> VERDICTS is a small variant); the digest = `_apply_note`; removal git-ops = `_nl_branch_delete` /
> `delete_branch` / revert. So PDL.0–.4 are mostly **new front-of-pipeline + wiring live parts**,
> not new delivery machinery.

- **PDL.0 — Operation Router + product-context persistence + the ledger schema.** Lead with the
  router (§1.5): extend `OperatorIntent` with the operation family + the dynamic dispatcher (this
  alone sharpens today's fix/feature/advisory routing). Then `product_loops` + `product_gaps` +
  `product_features` tables + models + `LoopStore` (mirrors `ParkStore`); wire `north_star` →
  `projects.set_standing_intent` (reuse the live injection+rejection); NL to start a
  `discovery_loop` → CHARTER_DRAFT; reuse advisory for refinement; human "solid" gate; rehydrate in
  `setup()`. *Tests:* router picks the right operation from NL (incl. one-shot-vs-loop
  disambiguation), create/persist/rehydrate/advance, restart-survival, ledger CRUD, north-star
  writes standing_intent.
- **PDL.1 — Market Brief.** MARKET_RESEARCH phase: framed market query to the research service
  (origin `agent-org-market`), persist the cited brief; the pivot loop (brief → human → re-charter).
  *Tests:* research call (fake grounding), brief persistence, pivot re-research.
- **PDL.2 — Gap Engine.** Codebase inventory (survey + repo-sync knowledge) + grounded gap synthesis
  → ranked, deduped `product_gaps`. *Tests:* gap generation, dedup vs `delivered`, ranking, "cites
  the brief" grounding assertion.
- **PDL.3 — PRD generator.** Top gap → structured PRD; PRD → effort goal + ACCEPTANCE VERDICTS +
  out-of-scope; Stage-2/3 gates. *Tests:* PRD structure, gap-citation, acceptance→check-cmd.
- **PDL.4 — Single iteration + Feature Ledger write.** One full pass gap → PRD → effort → delivery
  → dev-merge → **record the Feature** (merge_commit + declared WIRING integration points). *Tests:*
  end-to-end single iteration (fakes), mandatory-check gate (no check ⇒ human-ack), main-stays-gated,
  ledger entry written with wiring.
- **PDL.4.r — Feature removal/re-add.** NL "remove the <feature>" → revert merge + unwire effort +
  mark removed; "re-add" → re-apply PRD. *Tests:* removal reverts the right commit, unwire effort
  targets the declared integration points, dependents flagged, re-add restores.
- **PDL.5 — Full driver.** `_discovery_driver_loop`: re-ground after merge, convergence + thrash
  detection, budget caps, checkpoint cadence, PAUSED_CHECKPOINT, pause/resume/status NL controls,
  backpressure park. *Tests:* multi-iteration convergence, budget stop, thrash escalation,
  pause/resume, restart-rehydrate mid-loop.
- **PDL.6 — Two-tier merge + release gate.** `Project.dev_branch`, opt-in dev auto-merge, separate
  human-gated `develop → main` release. *Tests:* dev-merge on green, main gated, revert story.

---

## 10. Decisions

**Locked (2026-07-06):**
- **OD-PDL-1 — Autonomy cadence → MAXIMALLY AUTONOMOUS with a *batch* checkpoint.** The loop runs
  continuously to the dev branch with no per-iteration human review; the human reviews every
  `checkpoint_every` **features** and gates the `develop → main` release. `checkpoint_every` is
  configurable (start moderate, widen as trust builds). The safety comes from the test gate (§7),
  not human frequency.
- **OD-PDL-3 — Judgment lane → CLOUD.** Gap analysis, PRD synthesis, and viability judgment run on
  the cloud lane (F7 — model alignment is the dominant lever; these decide what gets built).
  Workers stay local.
- **OD-PDL-4 — Dev-merge autonomy → DEFAULT ON, gated by a mandatory real test.** Auto-merge to
  `develop` requires D2-green (real `check_cmd`) + all ACCEPTANCE VERDICTS MET + no blocking D3
  concern; no test ⇒ no autonomy (falls back to human-ack). `main`/release always human-gated.

**Still open (safe defaults; operator can override anytime):**
- **OD-PDL-2 — Gap-engine build:** *Rec* **bridge-side grounded synthesis (MVP)**; a server-side
  research-service `/gap-analysis` endpoint is a later grounding-richer upgrade.
- **OD-PDL-5 — "Solid" decision:** *Rec* the PM presents a marketability rubric from the brief
  (demand signal, differentiation, feasibility-vs-code); the **human decides** — never auto-advance
  past the north-star gate. (Decision 2: this gate is where the value is anchored — invest here.)
- **OD-PDL-6 — Market-brief shelf life:** *Rec* re-research on pivot + on operator request; a
  staleness timer is a later refinement.

The one residual risk to name (no silent caps): **max autonomy means the test gate carries the
whole safety load.** If a project's `check_cmd` is weak (compiles ≠ correct), buggy-but-green
features can accumulate on `develop` between batch reviews. Mitigations, in build order: the
mandatory-check rule (no check ⇒ no auto-merge), ACCEPTANCE VERDICTS per PRD, the autonomous D3
review, and — recommended follow-on — a per-PRD *generated test* requirement (the worker must add a
test that encodes each acceptance criterion, so the check_cmd grows teeth as features land). The
batch review is the backstop, but the goal is that it rubber-stamps green work, not debugs it.
