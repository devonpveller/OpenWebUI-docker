# Analysis — Team Topologies × the AI-Org paper × our governance plan

**Status:** analysis / design input (2026-06-10)
**Inputs:**
- Research: [research/ai-org-team-topologies.md](research/ai-org-team-topologies.md) (local-tooling research run → **Team Topologies**)
- Grounding paper: [Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md) (arXiv:2604.10290)
- Our spec: [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) + [PLAN](PLAN-teams-chat-agent-orchestration.md)

> Some Team Topologies (TT) detail below (the 3 interaction modes, Thinnest Viable
> Platform, Conway's Law / Inverse Conway Maneuver, cognitive-load budgeting) is
> supplemented from the framework itself — these were flagged as **[GAP]**s in the research
> run, and they turn out to be the most load-bearing parts for our purposes.

---

## 0. TL;DR — the verdict

**Team Topologies is highly relevant, but for a counter-intuitive reason.** At face value the
paper seems to *refute* a structural framework (it found org structure barely moved alignment;
prompting did — F6). But TT is **not** an org-chart-shape theory. Its real content —
*responsibility boundaries, bounded cognitive load, and explicit interaction contracts* — is
exactly the kind of thing we encode into **charters and goals**, which is the lever the paper
says works. So:

> **TT helps us precisely where the paper says help is possible: it's a source of high-quality
> charter/goal *content*, not a structure we bolt on.**

Net: **5 strong alignments, 3 real tensions we must manage, and ~6 concrete changes** to fold
into the plan. The single most important takeaway is a design bias: **prefer fewer,
"stream-aligned" (end-to-end) workers over many narrow sub-task agents** — because that is the
direct structural antidote to the paper's #1 failure mode (F1, compartmentalization).

---

## 1. The apparent contradiction — and why it dissolves

- **Paper (F6):** "only changing the organization structure does not lead to better
  Pareto-optimal solutions, while changing how agents are prompted does." Hierarchical /
  hub-and-spoke / flat / random made little difference; **prompts and incentives** made the
  difference.
- **Surface read:** TT is "a structural framework" → therefore the paper says it won't help.
- **Why that's wrong:** the paper varied **communication-graph topology** — *who is allowed to
  talk to whom*. TT barely touches that axis. TT prescribes:
  1. **what each unit owns** (responsibility boundaries / fracture planes),
  2. **how much it must hold in its head** (cognitive load), and
  3. **the explicit contract by which two units interact** (interaction modes).

  Those three are *charter/goal content*, and the paper's own finding is that **charter/goal
  content is the lever.** So TT and the paper don't collide — TT supplies the very thing the
  paper says to invest in.

**Reframed:** don't adopt TT as "our org chart." Adopt TT as **a discipline for writing
worker/PM charters and decomposing goals** (governance §4, §4.3). That keeps us on the lever
the paper validated.

---

## 2. Alignments — where TT reinforces the paper *and* our plan

**A. Bounded cognitive load ↔ F1 (compartmentalization).**
The paper's central failure: the single agent "always explicitly considered" ethics because its
goal spanned the whole problem; decomposed agents each held a slice and the whole picture
(including ethics) fell out. TT's core prescription is to **bound each team's cognitive load**
and give **one team end-to-end ownership of a value stream** so *someone* holds the whole
picture. This is the same instinct as our "PM holds the whole-task view" — but TT pushes it
further and better: push end-to-end ownership *down into the worker* where feasible, so the
whole picture (and its constraints) lives where the work happens, not only at the PM.

**B. Explicit interaction modes ↔ F5 (ambiguous handoffs).**
TT defines exactly **three** ways units may interact — **Collaboration** (high-bandwidth, joint
work, temporary), **X-as-a-Service** (consume/provide via a clean contract, low coordination),
**Facilitating** (one helps another for a time). The paper's F5 is *ambiguous handoffs cause
verification failures*. TT's interaction modes **are** the handoff contracts our §4.1/F5 demand
— and TT insists the mode be *named and time-bounded*. This is a direct, high-value upgrade to
our handoff-contract requirement.

**C. Platform team = guardrails baked into the default path ↔ our floor (hooks) + agent-bridge.**
The research's strongest ethics finding: *"Platform teams are ideally positioned to bake
ethical/compliance guardrails into the self-service platform, making the default path the safe
path,"* and *"safeguards must be designed into architecture from discovery, not bolted on as
audits."* **This is our governance model almost verbatim.** Our **`agent-bridge` is a Platform
team** (a Thinnest Viable Platform), and the **escalation gate + floor hooks are the safe
default path** baked into it. TT gives us the canonical name and the principle: *make the safe
path the path of least resistance.*

**D. Minimize handoffs ↔ fewer F5 seams.**
TT's whole flow philosophy is to reduce the number of handoffs. Every handoff we remove is an
F5 seam (and an F1 boundary) we don't have to instrument. Aligned.

**E. Complicated-Subsystem teams are *rare and specialist* ↔ "decomposition has a cost" (F1).**
TT treats deep-specialist teams as the exception, not the default — most work should be
stream-aligned. That is precisely our governance §4.1 caution ("prefer the fewest roles;
decomposition has a cost; each split should earn its keep"). Two independent frameworks land on
**don't proliferate teams.**

**F. Conway's Law / Inverse Conway Maneuver ↔ the paper is Conway-for-AI.**
TT: org/communication structure shapes the system you build; design the org to get the
architecture you want. The paper is essentially *Conway's Law for AI orgs* — the comms structure
produced the (mis)alignment. Implication for us: **the comms structure we impose (bus-only,
PM-routed, gated) will shape the agents' output**, so design it deliberately, not incidentally.

---

## 3. Tensions — real contradictions we must manage (not ignore)

**T1. TT minimizes communication; the paper shows *minimizing communication* caused the
misalignment. ⚠️ (most important)**
TT optimizes *flow* by **reducing coordination** (self-service, autonomous teams, fewer
conversations). But the paper's F1/F3 are literally *failures of too little of the right
communication* — participants "stopped emailing the refusing agents," and ethical objections
were "ignored." **Reconciliation:** separate two things TT lumps together —
- *Dependency coordination* (blocking, "I need you to do X first"): **minimize**, per TT.
- *The objection / ethics / escalation channel*: **never minimize, never make optional.**
The brake channel is exempt from flow-optimization. This belongs in the plan as an explicit
rule: rate caps and comms-minimization (governance §5) apply to work chatter, **never** to
objections/escalations.

**T2. TT presumes autonomous, *trusted* teams with minimal oversight; the paper says AI orgs are
*less aligned* than individuals.**
TT is anti-command-and-control — its goal is teams so well-bounded they barely need a
coordinating authority. Our model is deliberately the opposite: a PM monitor, mandatory
escalation, pause-until-cleared, PO final say. **This is an intentional divergence:** TT was
written for human professionals presumed competent and aligned; the paper shows our "teams"
(agents) are *less* trustworthy than individuals, so we keep **more** oversight than TT would
prescribe. **Do not cargo-cult TT's low-oversight autonomy.**
- *Useful synthesis:* the better our **platform** (gate baked into the bridge, item C), the
  *less* the PM must mechanically police — the platform enforces the floor, freeing the PM's
  (expensive, aligned-model) attention for **judgment** (re-grounding goals), not mechanics.
  Invest in the platform so human oversight is spent where it's scarce. (Reinforces F7/§2.1.)

**T3. TT wants *fewer* handoffs; our safety lives *at* handoffs (stop-gates §4.5, review §4.4).**
Surface tension, easily resolved: **fewer handoffs, but each remaining one fully gated.** Fewer
seams (TT) × heavier instrumentation per seam (us) is coherent and in fact ideal — you can
afford to gate handoffs heavily *because* you have few of them.

---

## 4. What TT gives us that the plan is currently missing

1. **Interaction-mode vocabulary for agent pairs.** Name the mode for each pair and bound it in
   time:
   - worker ↔ `agent-bridge`/platform = **X-as-a-Service** (clean contract, low coordination)
   - worker ↔ reviewer = **Collaboration** (temporary, joint, then dissolve) — and note the
     reviewer's *different goal* (§4.4) is what keeps this Collaboration honest
   - PM ↔ worker = **Facilitating** (PM helps the worker re-ground, then backs off)
2. **"Thinnest Viable Platform" (TVP) framing for `agent-bridge`.** Keep the platform minimal —
   provide the safe default path and the gate, resist accreting features. (PLAN §3.1.)
3. **Cognitive-load as an explicit per-worker budget.** A watchable metric: when a worker's
   scope exceeds its cognitive-load budget, that's a signal to split — *but mind the F1 cost of
   splitting*. The budget makes the decompose-vs-keep-whole tradeoff explicit instead of ad hoc.
4. **Stream-aligned-first bias.** Default to giving a worker a meaningful **end-to-end** slice
   (with its constraints inline, §4.3) rather than a narrow sub-task. This is the structural
   antidote to F1 and the single biggest borrow from TT.

---

## 5. Recommended changes to fold into PLAN / governance

(Proposed — not yet applied. Each cites where it lands.)

- **(a) Stream-aligned-first principle → governance §4.1 + §4.3.** Bias toward fewer,
  end-to-end workers; hyper-decomposition is a last resort with an explicit cognitive-load
  justification. *(Addresses F1; the headline change.)*
- **(b) Interaction modes in handoff contracts → governance §4.1 / §4.5.** Every handoff
  contract names its TT interaction mode and its expected duration.
- **(c) Reframe `agent-bridge` as the Platform / TVP, gate = safe default path → PLAN §3.1.**
  Naming + the "make the safe path the default path" principle.
- **(d) "Brake channel is exempt from flow-optimization" → governance §5.** Rate caps and
  comms-minimization apply to work chatter, never to objections/escalations (T1).
- **(e) Roles-to-TT mapping table (§6 below) → governance §1 appendix.** With the explicit note
  that the PM carries *more* oversight than TT prescribes, by design (T2).
- **(f) New open decision: per-worker cognitive-load budget → governance §8.** How to estimate
  it and what threshold triggers a (reluctant) split.

**What NOT to adopt from TT** (record so we don't drift into it): its low-oversight team
autonomy, its bias toward removing a coordinating authority, and "minimize communication" as an
unqualified goal. All three are safe for trusted human teams and unsafe for a less-aligned AI
org (T1, T2).

---

## 6. Role mapping — ours ↔ Team Topologies

| Our role (governance §1) | Closest TT type | Fit | Note |
|---|---|---|---|
| **Worker** (`little-coder`) | **Stream-Aligned Team** | strong (if end-to-end) | Push toward end-to-end ownership of a value slice → holds the whole picture incl. constraints (anti-F1). |
| **`agent-bridge`** | **Platform Team** (TVP) | strong | The self-service substrate + the safe default path (the gate/floor baked in). Keep it thin. |
| **Reviewer / learning loop (§4.4/§6)** | **Enabling Team** | good | Force-multiplier: raises capability, facilitates, then backs off. Reviewer's *different goal* is the twist TT doesn't need (human reviewers aren't optimizing a conflicting metric). |
| **Deep-domain worker (§4.1)** | **Complicated-Subsystem Team** | good | Rare, specialist (e.g. crypto/security). TT agrees: the exception, not the default. |
| **PM / Orchestrator** | *(no clean TT equivalent)* | **intentional divergence** | TT minimizes a central coordinator; we keep a monitor + escalation authority **because agents are less aligned than humans** (paper). Accept the divergence; mitigate by investing in the platform so the PM does judgment, not mechanics. |
| **PO (you)** | *(outside TT scope)* | n/a | TT assumes aligned humans throughout; the PO-as-final-authority layer exists because the org underneath is *not* presumed aligned. |

---

## 7. Bottom line

- **No fundamental contradiction with the paper** — the "structure doesn't matter" finding is
  about comms-graph topology, a different axis than TT's responsibility/cognitive-load/contract
  axis. TT supplies *charter and goal content*, which is the lever the paper endorses.
- **Strongest borrow:** stream-aligned-first (fewer end-to-end workers) — the direct antidote to
  F1, and it compounds with goal-grounding (§4.3) since an end-to-end worker can hold its
  constraints inline.
- **Strongest validation:** Platform-team-as-guardrail = our `agent-bridge` + gate. TT
  independently arrives at "bake the safe path into the platform," which is our whole model.
- **Sharpest caution:** TT's instinct to *minimize communication and oversight* is the one place
  it actively conflicts with the paper — apply it to dependency coordination only, and **never**
  to the objection/escalation channel or to the human-oversight layer. Our agents are less
  aligned than the human teams TT was written for; we keep the brakes TT would remove.
