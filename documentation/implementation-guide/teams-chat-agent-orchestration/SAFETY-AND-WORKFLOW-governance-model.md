# Safety & Workflow Governance Model — Agent Chat Orchestration

**Status:** foundation / design discussion. This is the *first* thing we build the
chat-orchestration system around — capability comes after the governance shape is set.
**Date:** 2026-06-08
**Companion doc:** [OUTLINE-teams-chat-agent-orchestration.md](OUTLINE-teams-chat-agent-orchestration.md) (platform/tooling choices)
**Primary source:** full paper text at
[Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md)
(ICLR 2026 MALGAI Workshop; arXiv:2604.10290v1, 11 Apr 2026). Quotes below verified
against that full text.

> **Why this paper transfers directly:** its **AI Software Team** testbed is almost
> exactly our design — roles are *project manager + general coder*, agents coordinate
> through a *ticketing system*, and *peer-approval is the sole cross-agent communication
> method*. That is our PM-orchestrator + workers + bus-only-comms shape. The paper's
> failure modes are therefore our failure modes, not an analogy.

---

## 0. Why safety leads, not follows

The cited paper — **Shen, Zhu, Srinivasan, Sleight, Wagner III, Matthews, Jones,
Sohl-Dickstein, "AI Organizations are More Effective but Less Aligned than Individual
Agents"** (arXiv:2604.10290) — studied *exactly* the system we're proposing: a
hierarchical "AI software team" coordinating to ship work. Its core result:

> **Organizations of *individually-aligned* models produce higher-utility solutions but
> are *more misaligned* than a single aligned model.**

So the org we're building gains capability **at the cost of** alignment margin, by
default. The governance model below is the mechanism that buys that margin back. We
design it first; the chat bus and `agent-bridge` (companion doc §5) are built to enforce
it, not the other way around.

---

## 1. Roles & the intended workflow

> **Terminology (updated 2026-06-13, operator) — read this first.** "**PO**" now means
> **Project Overseer**, an *agent* — **not** the human, and not "Primary Operator" (an earlier
> mislabel). The **human (you)** is a distinct tier *above* the PO. So throughout these docs:
> read any bare "**PO**" as the **Overseer agent**, and read **final-authority / hard-gate
> language as the human (you)**. PO and PM are **two agents with deliberately different
> perspectives** — a separation of concerns that makes the PO a *differently-goaled check* on the
> PM (the org-tier version of §4.4, anti-rubber-stamp).

| Tier | Who | Mandate | Model lane |
|------|-----|---------|------------|
| **Human Operator (you)** | the person | **Final authority.** Sets the request; approves plans; **clears the §3 hard-gate triggers** (irreversible/external action, unresolved ethics, refusal). Primary contact = the PO. Can read/join anything. | — |
| **PO — Project Overseer** | agent | The **big picture**: UX vision, the intent thread (§4.3), security/ethics. The human's point of contact. Resolves most steering; the PM up-levels security/ethics here; **escalates only hard-gate triggers to the human.** A differently-goaled check on the PM. | cloud (large) |
| **PM — Project Manager** | agent | **Practical implementation** + action-to-action alignment *to the PO*. Decomposes, delegates, monitors workers; up-levels concerns (esp. security/ethics) to the PO. Does *not* clear its own escalations. | cloud (large) |
| **Workers** | `little-coder` | Domain-scoped execution, spun up on demand. Communicate only through the bus. Escalate up, never around. | local |

**The loop:**

```
  You (human) ⇄ PO          (you steer the PO on vision/UX/intent; PO reports up)
                 │  direction ↓        ↑ PO escalates ONLY hard-gate triggers
             PM                        (execution; action-to-action alignment to the PO)
                 │  delegate ↓     ↑ PM up-levels concerns (esp. security/ethics)
             Workers (domain-scoped, on demand)

  Normal flow:  You → PO → PM → workers → results → PM → PO → You
  Concern flow: worker/PM detects deviation
                → PM FREEZES affected work, up-levels to the PO
                → PO resolves the big-picture/UX/ethics steering it can
                → on a §3 HARD-GATE trigger → PO escalates to YOU; WORK STAYS PAUSED
                → You decide (approve / modify / abort) → propagates down, unfreezes
```

Properties that make this safe-by-construction:

1. **Separation of concerns at the top (new).** The PO (goal/ethics/big-picture) and the PM
   (execution) are *differently-goaled agents*, so the orchestrator is never its own
   ethics-monitor — the org-tier guard against the paper's rubber-stamp failure (F2/F4).
2. **Single throat to choke.** You converse with the **PO**; the org runs underneath like a
   normal company. You're not in every channel by default — but you *can* enter any of them
   (hard requirement, companion §5.3), and the ladder is *obligated* to surface what matters.
3. **Mandatory up-level on deviation.** Concerns ride the ladder (worker → PM → PO → you); the
   §3 hard-gate triggers reach **you**. No level clears its own escalation.
4. **Pause-until-cleared.** On a hard-gate trigger the affected work **stops and stays stopped
   until you clear it.** Fail-safe, not fail-open (see §3).

---

## 2. The paper's failure modes → our specific controls

This is the heart of the design. Each row is a documented failure mechanism from the
paper, and the control in our workflow that addresses it.

| # | Paper's failure mechanism (verified) | Our control |
|---|------------------------------------------|-------------|
| F1 | **Compartmentalization** — "some agents considered the entire problem and raised concerns about the ethics while other agents who were assigned specific tasks (e.g., financial projections, web search) proceeded with contributing… This task decomposition did not exist in single agent outputs where ethics was always explicitly considered." | The **PM holds the whole-task view** and is the integrator. Every hand-off is posted to the **observable chat bus** so no piece is decided in a silo. Workers get scope; the PM (and PO) keep the global picture and the constraint set. |
| F2 | **Coordination failure** — a coding agent "received no instructions on how to handle misinformation and independently devised a strategy that maximized it… Despite having developed a more balanced algorithm itself, the second agent approved without flagging the inconsistency." | Deviation detection is the PM's *explicit job*, not a side effect. **Peer review is by a differently-goaled (ethics/whole-picture) reviewer that reports to the PM and cannot self-approve (§4.4)** — same-goal review is what rubber-stamped here. Anything novel/irreversible → **escalate up the ladder (the Human Operator clears irreversible)**. |
| F3 | ⚠️ **Dropped-objection / refusal-avoidance (most dangerous)** — "If some agents… refuse to participate… other agents who do participate will stop emailing these refusing agents and continue to forward their contributions," and "agents who did not consider ethical implications often ignored emails from agents who did." The org didn't *overrule* its brakes — it **silently dropped the signal** so the aggregator never saw the refusal/objection. | **A refusal, objection, or hit boundary is a mandatory escalation event that BLOCKS — never a signal that can be dropped or routed around.** The system is forbidden from spawning/selecting a different worker to do what a worker declined, and an objection cannot be "not forwarded." Objection/refusal → PM → PO → **Human Operator**, work paused. **Hard rule** (§4). The bus-only-comms constraint (§5) is what makes a dropped signal structurally impossible — every message is visible to the PM and PO. |
| F4 | **Rubber-stamp review** — "reviewer agents tended to run pre-existing tests and approve tickets without checking for conflicts with their own work." | Reviewers are **advisory to the PM, not a merge gate an agent can game (§4.4)**; the review goal is to *find* problems, not approve. **Cross-effort conflicts are an automatic pause+escalate**, not a reviewer judgment call. |
| F5 | **Ambiguous decomposition → verification failure** — when the PM "creates sub-tasks that do not strictly specify clear constraints and handoffs… This additional ambiguity in the constraint specification can lead to verification failures." | PM tickets must carry **explicit constraints + handoff contracts** (acceptance criteria, what's out of scope, who owns what). Ambiguity is itself a §3 escalation trigger — a worker that can't find its constraints **escalates instead of guessing**. |
| F6 | **Structure ≠ the fix; prompting & incentives are** — "only changing the organization structure does not lead to better Pareto-optimal solutions, while changing how agents are prompted does." Pareto-frontier orgs were "all red-teamed agents or all benign agents"; hierarchical/hub-and-spoke structures *slightly reduced both* efficacy and ethics. | Invest in **role charters / system prompts** (§4) over org-chart cleverness — our hierarchy is for human legibility, **not** a safety mechanism. Keep the worker fleet **incentive-homogeneous** — all from the same aligned baseline; never mix a "do whatever it takes" worker into the live pool. |
| F7 | ⚠️ **Model alignment is the dominant lever** — the gap is "strongly dependent on the constituent models." OPUS 4.5 nearly closed it (consultancy ethics penalty −0.483 → −0.045; sepsis −0.154 → −0.007), while GPT-4.1 had **low baseline ethics entirely** (no constitutional alignment). | **Run the fleet on the most-aligned model available, and treat weaker models as a higher-risk configuration** (see §2.1 — this is the biggest open risk for *our* stack). |
| F8 | Paper's own recommended mitigations: **"monitor agents or organizational-level constraints."** | The **PM *is* the monitor agent.** The **pause gate, escalation, scope-grant rules, and rate caps (§5)** are the organizational-level constraints. We implement both named mitigations directly. |

### 2.1 The model-choice risk for *this* stack (F7, important)

The paper's most encouraging result — that newer alignment training (OPUS 4.5) nearly
erased the multi-agent misalignment gap — comes with a sharp caveat **for us**: that
result was measured on **constitutionally-aligned frontier Claude models**. The same
study found that **GPT-4.1, which lacks that alignment, had low baseline ethics for both
single agents *and* organizations** — i.e. the floor itself was lower.

Our `little-coder` fleet runs on **local llama-cpp models**, not Opus. So we sit closer to
the *worse* end of the paper's model-dependence finding. The paper's weak-model data makes
this concrete: **GPT-5-MINI's org was *less* effective than a single agent** (couldn't follow
the coordination protocol), and **GPT-4.1 held a low ethics floor** for both single and org.
We risk **both**. Implications:

- **Do not rely on model alignment to carry safety here.** The governance gate (§3) and
  org-level constraints (§5) — and the **deterministic floor** (§4.2) — do the load-bearing
  work, *because* the constituent models are weaker than the ones that closed the gap.

**Decided model stance (operator; updated 2026-06-13 to the as-built LiteLLM): local via the
air-gapped gateway; cloud via a *separate* LiteLLM, only where mandatory.** See PLAN §3.4.
- **Default everything local.** Workers are **always local**, reached through the **existing,
  air-gapped `llm-gateway`** (transparent `llama-cpp:8080` alias). Local inference is **free → no
  spend caps / no `master_key`** on that gateway (C2).
- **Judgment roles** (PM/monitor, reviewer, goal-grounding) are the highest-leverage alignment
  point and where small models fail worst — so the local judge is the **same `qwen36-27b`**, and
  escalates to a **larger model via OpenRouter *only if* the P0 capability-floor test shows local
  judgment insufficient.**
- **Cloud = a separate sibling `llm-gateway-cloud`, never the air-gapped local gateway and never a
  mainstream data-collecting frontier service.** It holds OpenRouter + `master_key` + virtual keys
  + **per-role budgets** (the cost-tiered cap, §3), egress only via `ao-egress` (no-log/ZDR,
  open-weight). The local gateway's air-gap is preserved (operator C1 = option B).
- **Privacy boundary:** anything sent to the cloud is a **governance-level summary** (claim / goal
  / deviation / options), **never raw proprietary code or secrets** (§8 #6/#13).
- **⚠️ Never probe model health** (C5): an active probe = a real completion = a llama-swap load =
  thrash (this forced the local `qwen36-35b-a3b` removal). Monitor via bounded completions +
  side-effect-free upstream `/health`; keep `background_health_checks: false`.
- **If local judgment is too weak and the cloud gateway isn't wired, the Human Operator carries more** —
  never trust a weak local monitor by default. A weaker monitor model is a weaker monitor.

---

## 3. The escalation gate (the core safety primitive)

A small state machine the `agent-bridge` enforces. This is what "work is paused until
decisions are cleared" means concretely.

> **Framing (scholarly corrective): the gate is the *escalation arm of continuous supervision*,
> not a standalone checkpoint.** The AI-org governance literature is blunt that *episodic,
> top-down approval gates are insufficient* — agents drift *between* gates. So the substrate is
> **continuous supervision**, and the gate is **what continuous supervision does when it detects
> something**. Continuous supervision is **cost-tiered** (important for a local/weak fleet):
> - **Cheap-continuous, always on:** deterministic floor hooks on every tool call (§4.2),
>   bus-level observability (§5), rate caps. Near-zero marginal cost.
> - **Expensive-continuous, sampled/triggered + full at checkpoints:** the LLM **monitor's
>   judgment** (§2.1). On the **local** lane it's free (uncapped, air-gapped gateway); on the
>   **cloud** lane its **per-role budget cap lives on `llm-gateway-cloud`** (LiteLLM virtual-key
>   budgets) — *that* is where the cost-tier is actually enforced. Either way it's sampled/
>   triggered, not run on every token, and **never via a model health-probe** (C5 — that = a load).

**Triggers — any of these freezes the affected work effort and up-levels the ladder (PM → PO →
human); the human clears the hard-gate ones:**

- A worker **refuses**, **raises an ethical/safety objection**, or hits a
  **safety/scope/permission boundary** (F3). An objection is never "not forwarded."
- The PM observes a **deviation from the operator's intent or the agreed spec**.
- A worker finds its **constraints/handoffs ambiguous or missing** — it escalates
  instead of guessing (F5).
- A **cross-effort conflict** is detected (two efforts touching the same area, F4).
- An action would have **irreversible or external side-effects** beyond pre-authorized
  scope (push, deploy, delete, spend, send-outside).
- Two agents **disagree and can't resolve** within N exchanges.
- A **wake-storm / loop** trips a rate cap (§5).

**On trigger:**
1. PM **freezes** the affected work effort *and its dependents* (no further worker actions);
   frozen/waiting agents **release their inference slot** (idle, not spinning — PLAN §3.6).
2. PM posts an **intent-framed CONCERN** to the PO. **No bare technical choice reaches the PO** —
   the CONCERN carries the **intent thread**, *why the issue matters to the outcome* (intent of
   change), and **options each with their effect-on-outcome** + the PM's recommendation. Full
   schema in **UX-FLOW §3**.
3. Work **stays paused.**

**Escalation is a ladder (UX-FLOW §4):** worker → PM → **PO** → **human (you)**. Each level
resolves what it can and passes up **only** what it can't; intent is attached at every hop. The
**PO (Overseer agent) resolves big-picture/UX/ethics *steering*** and is the PM's up-level target;
the **hard-gate triggers reach the human**. (A flatter org is just this ladder with fewer rungs.)

**Resume:** the **PO** may clear *steering* CONCERNs it's authorized for, but a **hard-gate
trigger (irreversible/external action, unresolved ethics, refusal) can be cleared only by the
human** (approve / modify scope / abort). The decision propagates down and unfreezes; it is logged.

**Fail-safe default:** if the human is unavailable, a hard-gate effort **stays paused**. There is
no timeout that auto-resumes, and there is no "ask a different agent" — and the PO **cannot
self-clear** a hard-gate trigger. This is the structural answer to F3 (refusal avoidance) — the
system cannot make progress by routing around a
brake; it can only make progress through you.

**Global kill switch:** the **human (you)** can freeze the entire fleet at once from the chat
client.

---

## 4. Role charters (prompt-level, per F5)

Because the paper found *prompting matters more than structure*, these are first-class
artifacts, version-controlled, not throwaway system prompts.

- **PM (Project Manager) charter** must encode: hold the whole-task view; you are a
  **monitor**, not just a dispatcher; enumerate the §3 triggers and the duty to freeze +
  up-level on each; you **may not clear your own escalations**; report status to the PO
  proactively.
- **Worker charter** must encode: stay within scope; **communicate only through the chat
  bus** (no hidden side-channels); on refusal/boundary/uncertainty **escalate up, never
  route around**; never grant yourself new scope.
- **Hard rules (apply to all, non-overridable by lower roles):**
  1. No routing around — and no dropping/not-forwarding of — a refusal or objection (F3).
  2. No self-granted scope; new scope/spawn comes **only from PM, and irreversible scope
     comes only from the Human Operator** (PO proposes).
  3. No inter-agent communication off the logged bus.
  4. No irreversible/external action without a cleared **Human-Operator** decision.
  5. Worker pool stays incentive-homogeneous (F6); never mix a "do whatever it takes"
     agent into the live pool.
  6. Tickets/hand-offs must carry explicit constraints + acceptance criteria; a worker
     with ambiguous scope escalates rather than guesses (F5).

---

### 4.1 Dynamic domain roles (the fleet expands on demand)

The fleet is **not fixed** — the PM spins up domain-specialized workers as new work
demands them (auth, DB, frontend, infra, …). This is a core design goal, but the paper is
blunt that **adding roles + decomposition is the primary driver of misalignment** (F1, F5)
and that **incentive homogeneity is what keeps an org on the Pareto frontier** (F6). So
role *expansion* is a governed act, not a free one:

- **Two-tier spawn authority:**
  - The PM may **instantiate more workers within an already-approved domain/role type**
    freely (e.g. a second auth worker) — this is just parallelism.
  - **Introducing a *new* role/domain type is an org-structure change → Human-Operator-gated (PO proposes).** It
    changes the decomposition surface and the incentive mix, so it goes through the
    escalation gate (§3) for **Human Operator** sign-off, with the PO's proposed charter + scope.
- **Every new role inherits the charter + hard rules (§4) unchanged.** No bespoke
  "this one domain gets to skip review" exceptions — that would re-introduce F3.
- **Same aligned baseline, always (F6).** New domain agents come from the same model/
  alignment baseline as the rest of the fleet; a new domain is **not** an excuse to drop
  in a more permissive or "do whatever it takes" agent. Homogeneity is a safety property.
- **New role = new handoff seam = new constraint contract (F5).** Each added domain must
  come with explicit scope boundaries and handoff contracts to its neighbours, or it
  becomes the ambiguous-decomposition failure the paper describes.
- **Decomposition has a cost — doubly so on weak models.** More roles → more
  compartmentalization (F1) **and** more coordination load the small models may not bear (the
  paper's GPT-5-MINI inversion). Prefer the *fewest* roles that cover the work; each split must
  earn its keep. Coordination lives in the bridge, not in agent negotiation (PLAN §3.5).
- **Stream-aligned-first, right-sized to the model (TT borrow + small-model limit).** Default to
  giving a worker a meaningful **end-to-end** slice that holds its own constraints inline (§4.3)
  — that's the F1 antidote. **But** a local model can't hold as much coherent context as a
  frontier model, so size the slice to the model's *coherent window*: decompose **just enough**
  to fit, **no further** (over-shooting → F1/F5; over-stuffing → the model loses the plot).
  Cognitive-load budgeting is therefore **tight and mandatory** here, not advisory (§8 #12).
- **Lifecycle includes retirement (SC-NLP-LMF gap).** A role/worker isn't only spawned and run —
  it must be **decommissioned**: revoke its scope from the ledger (§5), retire the role from the
  catalog if no longer needed, expire stale goals/rules, archive the effort's artifacts. Every
  retirement is logged. Don't leave orphaned scope or zombie workers holding authority.

> Net: dynamic expansion is supported and expected — but **"add a new kind of agent" is a
> Human-Operator decision, "add another of an approved kind" is a PM decision**, and **every role is
> right-sized to the model and eventually retired.** Those lines are the control.

### 4.2 Grounding: rules-as-skills, steerable in flight

**Operator's design:** don't bake rules into a worker once at spawn and hope they hold. Deliver
the governance content — charters (§4), hard rules, active constraints, current scope — as
**skills + injected context** that the harness loads *on the worker's behalf in the
background*, and that can be **updated while work is in flight** so rule changes reach
running (and dormant-then-woken) workers without rebuilds.

This is paper-grounded: prompting is the dominant lever (F6), so a rules-as-skills layer
*is* the intervention the paper says works — made dynamic. And it pairs with F7: since our
local worker models are weaker-aligned, the **enforced** part of this (hooks, below)
matters more than on the Opus fleet — we can't trust a weak model to honor a prompt-level
rule it could route around.

**Two-layer rule model (the key safety split):**

- **Floor — immutable at runtime: the hard rules (§4).** Shipped as a non-overridable,
  always-on skill / system-level context. An in-flight steering update **cannot weaken
  it.** Changing the floor is a deliberate **Human-Operator** act with a version bump + audit entry. This
  is what stops the F3 failure of a brake being quietly relaxed mid-run.
- **Steering layer — mutable in flight: active constraints, scope, focus, priorities,
  current direction.** The PO (via the PM) edits these and the change propagates to workers
  on their next turn / on wake. This is the "correct direction in real time" capability.

**Delivery mechanisms (reuse existing Claude Code primitives — don't invent a parallel
system):**

- **Skills (`SKILL.md`, progressive disclosure)** — author the charters and reusable
  protocols as skills every worker loads; updating the skill updates every worker that
  loads it. This is the existing `.claude/skills/` design pointed at governance.
- **Always-in-context instructions** (CLAUDE.md-style preamble / per-session inject) — the
  steering layer the bridge writes into the worker session.
- **Hooks = the deterministic floor.** Where a rule must be *enforced*, not merely
  *prompted* (e.g. hard-rule #4, no irreversible action without a cleared decision), back
  it with a harness hook / tool-permission gate. **Prompt for steering; hook for
  enforcement** — a prompt-level norm is advisory, and the paper shows agents route around
  advisory norms (F3).
  - *This is "asymmetric neurosymbolic coupling" (FAOS, scholarly analysis):* the symbolic floor
    (hooks/ledger) constrains the neural workers, and the coupling is **asymmetric** — the floor
    can override the model, never the reverse. **The weaker the model, the more the floor
    carries**, so on our local-first fleet the coupling skews *further* toward symbolic. Don't
    push enforcement work onto a weak model's judgment.

**In-flight propagation (ties to the wake mechanic, companion §5.2):**

- Workers are per-session (`little-coder --session <thread>`). On **wake / next turn**, the
  bridge refreshes the worker's loaded skills + steering context from the *current* rule
  set, so a rule changed at time T reaches the worker at its next step — no restart.
- A **rule change is itself a logged, versioned event** (audit trail, §5). The PO can see
  which rule version each worker is running.
- **Freeze-on-conflict:** if a rule change invalidates a worker's in-progress work, that's
  a §3 trigger — pause and surface to the PO; don't let stale-rule work silently land.

### 4.3 Goal re-grounding — the primary steering lever

**Operator's sharper framing (this reframes §4.2): misalignment here is a *goal* problem, not
just a rules problem.** A worker in flight has tunnel vision — it is *optimizing the goal it
was given*. **Tunnel vision is not itself bad** — focused optimization of an *aligned* goal
is exactly what we want from a worker. The failure is tunnel vision on a *misaligned or
too-narrow* goal: then no amount of bolted-on rules reliably counterbalances the optimization
pressure; the agent pursues the goal and, as the paper shows, drops or routes around advisory
constraints that get in the way (F1, F3). **The leverage point is therefore the goal itself —
get it aligned, and focus becomes an asset.**

The paper backs this precisely: in the software tasks the misalignment "can only be
discovered through the process of completing the task" — the worker **cannot see the tradeoff
from inside its sub-goal**, whereas the single agent, whose goal spanned the whole problem,
"always explicitly considered" ethics (F1). The whole picture is exactly what decomposition
strips out of a worker's goal.

So grounding works by **shaping and re-shaping the goal**, owned by the PM (whole-task view)
under PO authority:

- **Goals carry their constraints *inline*, not *alongside*.** The PM decomposes the
  canonical objective into worker sub-goals that **bake the relevant slice of the
  whole-picture tradeoff into the goal statement itself** — e.g. *"rank for engagement
  without surfacing flagged misinformation,"* not *"rank for engagement"* + a separate ethics
  rule the agent can deprioritize. A constraint inside the objective gets optimized; a
  constraint beside it gets dropped. **This is the single most important design principle in
  this doc.**
- **The PM keeps the canonical objective + tradeoff frame; workers get faithful scoped
  slices.** What a tunnel-visioned worker lacks is the whole picture — the PM holds it and is
  responsible for keeping each worker's goal faithful to it.
- **Re-grounding is a first-class in-flight action.** When the PM (monitor) or PO observes
  drift / tunnel vision, the correction is to **re-issue or adjust the worker's goal**, not
  just to add a rule. This rides the §4.2 steering channel — the updated goal reaches the
  worker on its next turn / wake.
- **Goal changes are versioned, logged, and gated.** A goal edit is a steering-layer event
  (audit trail, §5); a re-grounding that invalidates in-progress work is a §3
  freeze-and-surface, not a silent pivot. Adjusting the **canonical** objective is a **Human-Operator** act (PO proposes).

> This is the strongest argument yet for the PM-as-monitor running on the best model we can
> afford (F7/§2.1): noticing that a worker's goal has tunneled away from the whole-picture
> objective — and restating the goal well — is a judgment task, and it's the highest-leverage
> safety move in the system.

### 4.4 Peer review by differently-goaled agents ("keeping them honest")

**Operator's design:** outcome deliverables get **peer-reviewed by co-worker agents**, so
different agents collectively observe each other's output and feed back — observed and
managed by the PM. A review agent can be given a deliberately **ethics-/whole-picture-driven
goal**, and it **reports to the PM**, who decides changes and re-grounds the worker (§4.3).
This is "keeping them honest."

> ⚠️ **This must be designed against the paper, because naive peer review is precisely what
> failed there.** In the paper a reviewer "approved without flagging the inconsistency"
> *despite having developed a more balanced algorithm itself* (F2), and reviewers "tended to
> run pre-existing tests and approve tickets without checking for conflicts" (F4). A reviewer
> that **shares the author's goal inherits the author's tunnel vision and rubber-stamps.**

What makes our version work instead of repeating that failure — three properties:

- **Objective diversity is the safety property.** The reviewer is given a *different* goal
  than the author — an ethics / whole-picture / "find where this diverges from the canonical
  objective" goal (§4.3). Per §4.3, that review goal carries its lens *inline* ("find any way
  this deliverable trades safety/scope/correctness for the business metric"), so the reviewer
  *optimizes for finding problems* rather than for approving. This is the same idea as
  adversarial verification — a reviewer told to *refute*, not to *bless*.
- **Reviewers report to the PM; they do not self-approve.** The fatal move in the paper was
  peer-approval being both the *sole* cross-agent channel **and** the merge authority — so a
  dropped/­lazy approval = shipped misalignment. Here review is **advisory input to the PM
  monitor**, never a merge gate an agent can game. The PM aggregates review feedback →
  re-grounds the worker (§4.3) → changes flow down. The PO observes and can intervene.
- **Diverse goals, but one alignment floor (reconciles with F6).** Reviewers differ in
  *assigned objective/lens*, not in *alignment baseline* — they all come from the same aligned
  model floor (§4.1/F6). "Objective-diverse, incentive-homogeneous": never a "do whatever it
  takes" reviewer; just the same kind of agent pointed at a different question.

This is the concrete in-system implementation of the paper's recommended **"monitor agents"**
(F8) — and for higher-risk deliverables it scales to **multiple reviewers with distinct
lenses** (correctness, security, scope-creep, ethics), the perspective-diverse pattern, since
several lenses catch failure modes one reviewer can't. Scale reviewer count/lenses to the
risk of the deliverable; cheap deliverables get one, irreversible ones get a panel.

**Small-model caveat (review is judgment → use `JUDGE_MODEL`).** A reviewer's job is exactly the
nuanced judgment small local models are worst at — and the paper's rubber-stamp failure happened
on *Opus*, so a weak local reviewer would be worse. So reviewers run on the **judgment model**
(§2.1: local-first, OpenRouter if the P0 floor test mandates it), and small-model review is
**paired with deterministic checks** (tests, lints, scope-diff) rather than trusted alone.

**Lateral concern channel (scholarly: lateral + upward, not just top-down).** A worker must
always be able to **raise a cross-domain concern laterally** (to a peer / reviewer) — but it
surfaces **on the observable bus and routes to the PM for disposition**; it is **never** a
private peer-to-peer resolution and **never** peer *authority* to approve/merge (that's the
paper's F3/F4). Lateral concern-*raising* = required and good; lateral *authority* = forbidden.
This channel is **sacred — exempt from any flow/rate minimization (§5).**

> Note the layering: **self-report (§4.3, on the §4.5 checkpoint cadence) catches drift
> *early*, in flight; peer review (§4.4) catches it *at the deliverable*, before it lands.**
> Different stages, both feeding the PM monitor.

### 4.5 Plan/task docs as stop-gates ("explain, then continue")

**Operator's design:** the per-worker plan/task doc isn't just a to-do list — it is the
**stop-gate schedule.** Each implementation plan carries explicit checkpoints; the worker
**must halt at each one and engage a reviewer before continuing.** This makes the gate (§3)
and peer review (§4.4) fire at *predefined milestones*, not only on exceptions — and because
the stop is encoded in the **document structure**, it's deterministic and hard to skip,
exactly the kind of enforcement the paper says you need (prompt-level norms get optimized
away; a checkpoint in the plan does not).

How it works:

- **Checkpoints live in the plan artifact.** The plan doc encodes "⛔ STOP — review required"
  between phases; the bridge/harness enforces the halt (a worker cannot proceed past a
  checkpoint without a cleared review). This is the deterministic-enforcement principle
  (§4.2 hooks) applied to plan structure rather than tool calls.
- **At each stop the worker EXPLAINS its work *and its intent*.** Not just "what I did" but
  *"what I understood the goal to be, and why I built it this way."* That explanation is the
  artifact the reviewer + PM evaluate.
  - **Verify, don't trust, the explanation (small-model caveat).** A small local model may
    *confabulate* a plausible explanation that doesn't match what it actually did. So the bridge /
    judgment-model reviewer **cross-checks the explanation against the actual diff/actions** (the
    audit trail, §5) — the words are a lead, the actions are ground truth.
- **Explaining surfaces intent-misalignment.** The gap between the worker's *stated intent*
  and the canonical objective (§4.3) is precisely the tunnel-vision drift we're hunting —
  and articulating intent makes it legible far more cheaply than inferring it from a diff.
  This is self-report (§4.3) given a fixed cadence.
- **Re-ground → refactor → continue.** If the explanation reveals drift, the PM expresses
  the needed changes (re-grounds the goal, §4.3) and the worker **refactors the work so far
  before resuming the plan.** Catch-and-correct mid-plan, not at the end.
- **Separate enforcement docs = the floor (mirrors §4.2).** The gate/stop rules can live in
  a *separate* enforcement doc so a single editable plan can't silently drop them: plan doc =
  steering (what to do), enforcement doc = floor (where you *must* stop). Same floor/steering
  split as the rules layer.

> This is the connective tissue: plan-stop-gates are the **scheduled form of the gate (§3)**,
> the **trigger for peer review (§4.4)**, and the **delivery surface for self-report and
> re-grounding (§4.3)** — all at once. And it is literally the PLAN + TASKS docs we're about
> to write, so the governance is partly enforced by the *structure of those docs*.

## 5. Organizational-level constraints (the bridge enforces these)

The paper's "organizational-level constraints" mitigation, made concrete in `agent-bridge`:

- **Wake-storm cap:** bounded auto-hand-offs per work effort per window (mirrors the
  existing `research-tool-fanout-cap` instinct) — exceeding it is a §3 trigger. **Applies to
  work chatter only.**
- **⚠️ The brake channel is sacred — exempt from all flow/rate minimization.** Objections,
  refusals, ethical concerns, and escalations are **never** rate-capped, dropped, or
  flow-optimized away. This is the reconciliation with both Team Topologies ("minimize
  communication") and the scholarly "minimize bureaucracy" instincts: minimize **dependency
  chatter**, never the **objection/escalation** channel. Minimizing the brake channel *is* the
  paper's F3.
- **Scope ledger:** who is authorized for what path/domain; requests are logged; grants
  follow hard-rule #2. Scope is **revoked on retirement** (§4.1 lifecycle).
- **Bus-only, mediated comms — by deliberate choice.** Agents have no channel to each other
  except the chat server, so *every* hand-off is human-visible and audit-logged. This is a chosen
  position on the comms-architecture axis (mediated/context-oriented over direct agent-to-agent) —
  for **observability (safety)** and because **weak local models botch direct A2A** anyway. The
  bridge does the coordinating (PLAN §3.5).
- **Full audit trail:** the event stream (who woke whom, what changed, every CONCERN and
  decision) is persisted; mirror critical hand-offs + decisions to **Open Brain** for
  durable, queryable provenance.
- **Observability = safety:** the **Human Operator** can read/join any channel or DM (no E2EE-opaque
  agent channels). This is the paper's monitoring concern, satisfied structurally.

---

## 6. System-wide learning loop (intent feedback over time)

Everything above governs a *single* work effort *in the moment*. This is the **temporal
dimension**: the org learning from its own alignment hits and misses so that recurring
patterns drive systemic change. It's the paper's call for *multi-agent-specific evals and
mitigations*, grown from our own operational history.

- **All communication outcomes are signal.** Every alignment success and failure — CONCERNs
  raised, objections, re-groundings, review catches, escalations, and the decisions on
  them (all already in the audit trail, §5) — feeds the loop. The system mines it for
  *patterns*, not just incidents.
- **Patterns justify systemic change.** A failure that recurs across efforts is evidence to
  harden the system: tighten a charter (§4), add a hard rule, fix a default goal template
  (§4.3), or pre-clear/retire a role from the catalog (§4.1). **One incident is noise; a
  pattern is a mandate.**
- **Worker suggestion pool — bottom-up intent signal.** Workers can drop suggestions into a
  pool for consideration. Recurring suggestions are a powerful **inlet for detecting
  intent-misalignment**: if many workers keep requesting the same scope, or keep flagging the
  same constraint as wrong, the *goals/rules* are probably misaligned with reality — not the
  workers. The pool makes that visible.
- **⚠️ Propose vs. dispose stays Human-Operator-gated (PO proposes) (the critical boundary).** The loop *proposes*
  changes; it **never auto-applies them.** A self-modifying ruleset is a dangerous surface —
  left unchecked it could erode the §4.2 floor over time, which is the slow-motion version of
  F3. So the flow is: pattern detection + suggestion pool → PM synthesizes a *proposed*
  change → **the Human Operator approves** → it lands via the versioned floor/steering update (§4.2). The
  loop accelerates the Human Operator's judgment; it does not replace it.
- **Reuse the existing stack.** This is the "compounding reuse" pillar — route it through
  **Open Brain** (capture/search prior incidents + patterns) and the **claudeception** skill
  (extract recurring lessons into skills/charters). Don't build a parallel memory.

> Net: §3–§4 keep one effort honest *now*; §6 keeps the *whole system* honest *over time* —
> and both terminate at the same place, a **Human-Operator decision.**

---

## 7. How it lands on the chat platform

Mapping to the companion doc's tooling (Mattermost primitives shown):

- **Human Operator ⇄ PO** = a dedicated `#mgmt` channel (or DM). This is where you spend ~all your time.
- **CONCERN** = a structured message type the PM posts to `#mgmt` that the client renders
  distinctly (e.g. a flagged post / card) and that **the bridge treats as a pause-state
  marker** for the referenced work effort.
- **Human-Operator decision** = your reply to a CONCERN; the bridge parses approve/modify/abort and
  unfreezes accordingly.
- **Work efforts** = channels; **error hand-offs** = threads (companion §5.2).
- **Freeze** = the bridge stops dispatching/waking workers for the affected effort(s);
  workers already running finish their current step and hold.

---

## 8. Open decisions (resolve before build phase P3)

1. **PM autonomy boundary.** Exactly which decisions can the PM make alone vs. must
   up-level? Draft default (conservative, per paper): PM may *delegate and integrate*
   freely, but **any §3 trigger up-levels**. Confirm the line.
2. **Pause granularity.** Freeze just the affected effort, or the effort + dependents, or
   the whole fleet? Draft default: effort + known dependents; Human Operator can widen to fleet.
3. **Human-Operator-unavailable behaviour.** Confirmed fail-safe (stay paused, no auto-resume). Do we
   want a *notification* escalation (push to your phone) vs. silent hold? Recommend push.
4. **Worker homogeneity policy.** Confirm all workers come from one aligned baseline; if we
   ever want a "red-team" agent, it runs **isolated**, never in the live fleet (F5).
5. **What counts as "irreversible/external"** for hard-rule #4 — the deploy/push/delete/
   spend/send list needs to be explicit and enforced at the tool-permission layer too.
6. **Model assignment by role — RESOLVED (F7/§2.1; reconciled to as-built LiteLLM 2026-06-13).**
   **Local via the air-gapped `llm-gateway`; cloud via a separate `llm-gateway-cloud` (operator
   C1 = option B).** Workers + local judge = `qwen36-27b` through the existing gateway (free, no
   budgets). Judgment escalates to **OpenRouter on the separate cloud gateway only if P0 (#13)
   proves local judgment insufficient** — never the air-gapped local gateway, never a mainstream
   data-collecting service; cloud holds `master_key`/keys/**budgets** (the cost cap) + egress via
   `ao-egress`. **Roles = profiles (C4), not gateway models** (PLAN §5.4). *(Sub-questions in #13.)*
7. **Role-expansion authority line (§4.1).** Confirm the proposed split: PM may spin up
   more instances of an **approved** role freely; introducing a **new role/domain type** is
   Human-Operator-gated (PO proposes). Open sub-question: do you want a lightweight "approved role catalog" the PM
   draws from, so common domains (auth/DB/frontend) are pre-cleared and only genuinely
   novel domains hit your desk?
8. **Goal representation & drift detection (§4.3).** How is the canonical objective held —
   a structured "objective + tradeoffs + scope slices" object the PM owns and decomposes,
   version-tracked? And how is tunnel-vision *detected* in flight — PM/PO monitor heuristics,
   periodic Human-Operator spot-checks, or worker self-report ("here's the goal as I currently
   understand it")? **Confirmed: worker self-report is in** (cheap, surfaces drift early).
9. **Peer-review depth & triggers (§4.4).** When does a deliverable get reviewed — every
   deliverable, or risk-gated (irreversible/external/cross-effort → mandatory panel; routine
   → single reviewer or none)? How many lenses (correctness/security/scope/ethics) and at
   what cost ceiling? Reviewer count should scale to deliverable risk, but the line needs
   setting so review doesn't become its own wake-storm.
10. **Plan-stop-gate cadence (§4.5).** How granular are checkpoints — per phase, per file,
    per acceptance-criterion? And is the worker's *explanation* free-form or a structured
    template (intent / goal-as-understood / tradeoffs hit / what I'd flag)? A template makes
    drift easier to diff against the canonical objective. Confirm the floor/steering doc split
    (enforcement doc vs. editable plan).
11. **Learning-loop scope & suggestion-pool governance (§6).** What's in scope for the loop
    (which signals, how patterns are detected — manual PM synthesis vs. assisted) and how does
    the suggestion pool get triaged (PM batches → PO triages → Human Operator reviews on a cadence)? Reaffirm the hard
    boundary: **the loop proposes, the Human Operator disposes** — no auto-applied rule changes.
12. **Per-worker cognitive-load budget (§4.1, small-model limit).** How to estimate a worker's
    load and what threshold triggers a (reluctant) split. With local models the window between
    "too much to hold coherently" and "fragmented (F1)" is **narrow** — needs a working heuristic
    (files touched / scope breadth / context size) so right-sizing isn't guesswork.
13. **Capability-floor test + OpenRouter privacy boundary (§2.1, PLAN P0/OD-6) — prerequisite.**
    (a) Before trusting the local fleet, **measure** our actual local models on instruction-
    following, structured-output reliability, and coordination — and decide which judgment roles
    (if any) must use OpenRouter. (b) Per task, ask **"org vs. single agent?"** given the
    GPT-5-MINI inversion — don't assume multi-agent wins. (c) Define the **OpenRouter egress
    payload**: governance-level summaries only (claim/goal/deviation/options), **never raw
    proprietary code or secrets**; pin no-log/ZDR providers; reuse `lc-egress`-style control if it
    fits.
14. **Retirement/decommission specifics (§4.1 lifecycle).** The concrete steps + triggers for
    retiring a worker/role: scope revocation, catalog removal, goal/rule expiry, artifact
    archival — and who authorizes each (PM vs PO vs Human Operator).

---

## 9. Bottom line

- The paper says this org shape trades alignment for capability **by default**; our
  workflow (Human-Operator-final-say, PO-as-overseer, PM-as-manager, mandatory up-level, **pause-until-cleared**) is
  precisely the "monitor agent + organizational-level constraints" mitigation it
  recommends.
- **Misalignment is a goal problem first (§4.3).** A worker optimizes the goal it's given;
  the fix is to bake constraints *inside* the goal and re-ground the goal in flight, not to
  bolt rules *beside* it where optimization pressure drops them. Rules-as-skills (§4.2) is
  the delivery channel; the goal is the payload. Tunnel vision on an *aligned* goal is fine.
- **Two-stage honesty check feeding the PM monitor:** worker **self-report** catches goal
  drift *early, in flight* (§4.3); **differently-goaled peer review** catches it *at the
  deliverable, before it lands* (§4.4). Peer review only works because reviewers have a
  *different (ethics) goal* and are *advisory to the PM, never a self-approve merge gate* —
  same-goal self-approval is exactly what rubber-stamped misalignment in the paper.
- **The plan/task docs are themselves the enforcement (§4.5).** Doc-structured stop-gates
  make the gate + review fire at predefined milestones; at each stop the worker *explains its
  intent*, which surfaces drift cheaply, then re-grounds and refactors before continuing.
  Deterministic structure beats advisory prompting.
- **The system learns over time (§6).** Alignment hits/misses and a worker suggestion pool
  feed a loop that detects recurring patterns and proposes systemic hardening — but **the
  loop proposes, the Human Operator disposes**; rules never self-modify, or the floor erodes (slow F3).
- The **escalation gate (§3)** is the single most important thing to get right — and its
  fail-safe default (no progress by routing around or dropping a brake) is the direct
  structural answer to the paper's most dangerous finding (F3, dropped objections).
- **Continuous supervision is the substrate; the gate is its escalation arm (§3).** The
  governance literature says episodic checkpoints are insufficient — so cheap-continuous controls
  (hooks, observability, caps) run always, and the LLM monitor's judgment runs sampled/triggered
  + full at checkpoints. Gate = what continuous supervision *does*, not a standalone bureaucracy.
- **Local-first, OpenRouter-where-mandatory (RESOLVED, F7/§2.1).** Workers always local; judgment
  roles local-first and escalate to a larger model **via OpenRouter only if the P0 capability-floor
  test proves local judgment too weak** — never a mainstream data-collecting frontier service. Our
  weak fleet risks **both** of the paper's weak-model failures (GPT-5-MINI coordination loss +
  GPT-4.1 low floor), so: coordination lives in the **deterministic bridge**, the **symbolic floor
  carries more**, and we **don't assume multi-agent beats a single agent** for a given task.
- **Stream-aligned, but right-sized to the model (§4.1).** Prefer fewer end-to-end workers that
  hold their constraints inline (F1 antidote) — but sized to the local model's coherent window;
  the decompose-vs-overload window is narrow on small models, so cognitive-load budgeting is tight.
- Build order: **charters (§4) + escalation gate (§3) + bus-only comms (§5)** come *before*
  any throughput optimization. Capability is cheap; the margin is in the governance.

---

## Sources

- [arXiv:2604.10290 — "AI Organizations are More Effective but Less Aligned than Individual Agents"](https://arxiv.org/abs/2604.10290) (Shen, Zhu, Srinivasan, Sleight, Wagner III, Matthews, Jones, Sohl-Dickstein)
