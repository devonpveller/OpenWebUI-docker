# Analysis — Frontier-model vs. small-local-model org approach

**Status:** analysis / design input (2026-06-10)
**Inputs:**
- Research (assumes frontier-capable agents): [research/ai-org-structures-for-software-producing-companies.md](research/ai-org-structures-for-software-producing-companies.md), [research/ai-org-team-topologies.md](research/ai-org-team-topologies.md)
- Grounding paper **with direct small-vs-frontier model data**: [Ai-Organizations-...md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md) (arXiv:2604.10290) §5.2
- Sibling analyses: [ANALYSIS-team-topologies-alignment.md](ANALYSIS-team-topologies-alignment.md), [ANALYSIS-scholarly-ai-org-frameworks.md](ANALYSIS-scholarly-ai-org-frameworks.md)
- Our spec: [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) + [PLAN](PLAN-teams-chat-agent-orchestration.md)

> **The premise (operator's):** the org frameworks in the research (Team Topologies, FAOS,
> SC-NLP-LMF, DRESS-eAI) implicitly assume **frontier-class agents** — strong instruction-
> following, long coherent context, nuanced judgment, and (for Claude-class) constitutional
> alignment. **Our workspace runs small local models** (`llama-cpp`, ~27B-class). The capability
> and alignment gap is large enough that the *approach* must differ — in places, invert.

---

## 0. TL;DR — the core asymmetry

The paper's most-cited result ("orgs are more capable but less aligned") was measured on
**Opus**. The paper *also* tested weaker models, and those results are the relevant ones for us:

> **The org-level capability *gain* depends on the model being good enough to coordinate. The
> paper found that for GPT-5-MINI, "single agents were much more effective than AI Organizations
> because the model was not able to follow agentic instructions well (e.g., sending emails in the
> right format)."** And for GPT-4.1 (no constitution), "ethics scores for both AI Organizations
> and single agents are low at baseline."

So for a **weak model**, the multi-agent org can be the **worst of both worlds**: it **loses the
capability upside** (the model can't handle coordination/format) while the **low alignment floor
persists**. Two distinct risk profiles:

| | **Frontier-model org** | **Small-local-model org (us)** |
|---|---|---|
| Why it's risky | **Competently misaligned** — finds the most effective *and* least ethical solution (paper's core finding) | **Incompetently unreliable + low floor** — loses the plot, ignores constraints, botches protocol, *and* has weak baseline ethics |
| Capability vs single agent | Org **gains** capability | Org may **not gain**, can *lose* it (coordination fragility) |
| Oversight problem | Catch *sophisticated* drift | Compensate for *unreliability* + a *low ethics floor* |
| Where capability buys safety | The monitor (catch subtle misalignment) | The monitor **and** the deterministic floor (the model can't self-police) |

**Net design consequence:** for our stack, lean toward a **minimal org with deterministic
coordination and a strong-model judgment layer** — closer to the *inverse* of the literature's
rich-autonomous-org picture.

---

## 1. What changes, concretely (small models vs. frontier)

Small local models differ on six axes that each reshape the design:

1. **Instruction/role adherence — weaker.** Small models drift from charters, "forget" system
   prompts under load. → charters/goals must be **short, sharp, repeated**; you cannot rely on a
   long nuanced charter holding.
2. **Alignment floor — lower (the GPT-4.1 analog).** Local models lack constitutional safety
   training. → **do not trust the model's intrinsic ethics at all**; the **floor (hooks) must
   carry the ethics**, not the model's judgment.
3. **Coherent context — shorter (context rot).** Small models lose the thread on long context. →
   directly **tensions the "stream-aligned, hold-the-whole-picture" recommendation** (see §2).
4. **Judgment/reasoning — weaker.** Detecting subtle misalignment, weighing tradeoffs, reviewing
   nuanced work — exactly what the **monitor and reviewer** roles need, and exactly where small
   models are worst. → judgment roles **must** be the strongest model available.
5. **Introspection/honesty — less reliable.** "Explain your intent" (§4.5) assumes the model can
   truthfully introspect. Small models **confabulate** plausible explanations. → **verify the
   explanation against actual actions** deterministically; don't trust the words.
6. **Structured output / tool use — fragile** (the GPT-5-MINI failure). → **simpler protocols,
   fewer tools per agent, defensive parse/repair in the bridge**; coordination is done by the
   *deterministic bridge*, not by the models talking to each other.

---

## 2. The sharpest tension: "stream-aligned-first" doesn't survive contact with small models intact

The Team Topologies analysis's headline borrow was **stream-aligned-first** — fewer, *end-to-end*
workers that hold the whole picture (the F1 antidote). That assumes a model that **can** hold a
whole value stream coherently. **A small model cannot.** Push too much end-to-end scope at a
27B-class model and it loses the plot — re-introducing failure by *incompetence* instead of
compartmentalization.

So the small-model version is a **narrower sweet spot**:

> Decompose **just enough** that each worker's slice fits within the small model's *coherent*
> context window — but **no further**, or you hit F1 (compartmentalization) and F5 (handoff
> ambiguity). With frontier models that window is wide; with small models it's **narrow**, so
> **cognitive-load budgeting (TT) becomes tight and mandatory, not advisory.**

Practical rule for our stack: **size the worker's slice to the model, then bake the relevant
constraints inline (§4.3) within that smaller slice.** Frontier models let you choose "few big
workers"; small models force "right-sized workers, each tightly grounded." The constraint-inline
principle matters *more* for small models (they drop side-constraints faster) but each goal must
carry *fewer, sharper* inline constraints (they can't track many).

---

## 3. The capability-inversion warning (GPT-5-MINI result)

The paper's GPT-5-MINI finding deserves to be a first-class design input: **a weak model's
multi-agent org was *less* effective than a single agent**, because the model couldn't handle the
coordination protocol. Implications for us:

- **Don't assume multi-agent beats single-agent on our stack.** For some work, one well-scoped
  local agent (or one local worker + a strong-model monitor) may beat a fan-out that the local
  models can't coordinate. **Make "do we even need an org for this task?" an explicit question.**
- **Move coordination out of the models and into the deterministic bridge.** The agents should do
  as little agent-to-agent protocol as possible; the **bridge** handles routing, wake, handoff
  contracts, gating. The models execute fenced tasks; they don't negotiate. (This is also why
  bus-mediated, not direct A2A, comms is the right call for us — scholarly analysis §4-(l).)
- **Keep the org small.** Each added role is both an F1 cost (paper) *and* a coordination-load
  the small models may not bear. Our "prefer fewest roles" rule (§4.1) is **doubly** binding.

---

## 4. The resulting architecture for OUR stack

Everything above converges on a **tiered, deterministic-heavy** shape:

- **Tiered model assignment is mandatory, not optional (resolves PLAN open decision #6 toward
  hybrid).**
  - **Judgment layer → strongest available aligned model** (metered cloud Claude via the openbrain
    gateway): the **PM/monitor**, the **reviewer**, and **goal-grounding/decomposition**. This is
    where capability buys *safety* (F7) and where small models fail worst (axis 4).
  - **Execution layer → small local models**: workers, tightly scoped, deterministically fenced.
    This is where capability matters least (fenced execution) and privacy/cost matter most.
- **The deterministic floor does more (asymmetric coupling skews symbolic).** FAOS's "asymmetric
  neurosymbolic coupling" (scholarly analysis) skews **further toward the symbolic** for us: hooks,
  scope ledger, format validation, rate caps carry the load the weak neural layer can't. The
  worse the model, the heavier the symbolic floor.
- **Continuous supervision, cost-tiered (scholarly analysis T-S1/T-S3).** Cheap-continuous
  (hooks/logging/caps) is *more* important here and nearly free. Expensive-continuous (LLM monitor
  judgment) is *more* necessary (weak workers) but *more* expensive — so it runs on the strong
  judgment model, sampled/triggered + full at checkpoints.
- **Verify, don't trust, self-report.** The §4.5 "explain your intent" stays — but the bridge (or
  the strong reviewer) **cross-checks the explanation against the actual diff/actions**, because a
  small model's explanation may not reflect what it did.
- **Simple protocols.** Minimal tool surface per worker; structured outputs validated/repaired by
  the bridge; short, repeated charters.

---

## 5. How each prior recommendation shifts for small models

| Rec (from sibling analyses) | Frontier assumption | Small-model adjustment |
|---|---|---|
| (a) Stream-aligned-first | few big end-to-end workers | **right-sized** workers to fit context; tight cognitive-load budget (§2) |
| (g) Continuous-supervision-first | LLM monitor can watch broadly | cheap-continuous always; **LLM monitor must be the strong model**, sampled/triggered |
| §4.3 constraints-inline | many nuanced inline constraints OK | **fewer, sharper** inline constraints; matters *more* but must be *simpler* |
| §4.4 peer review | a capable reviewer catches issues | **route reviewer to strong model**; small-model review is shallow — pair with deterministic checks |
| §4.5 explain-intent / self-report | trust the introspection | **verify explanation vs. actual actions**; small models confabulate |
| §4.2 floor/steering (neurosymbolic) | balanced coupling | **skew symbolic** — floor carries more |
| §4.1 fewest roles | F1 cost | F1 cost **+ coordination fragility** → doubly binding |
| comms model | rich NL agent-to-agent | **deterministic bridge coordinates**; agents barely negotiate |

---

## 6. Recommended changes / new open decisions

(Proposed — not yet applied; extend the combined (a)–(l) set.)

- **(m) Make tiered model assignment a stated architecture decision, not an open question →
  PLAN §3.4 + governance §2.1.** Judgment layer = strong/cloud; execution = local. Resolves
  PLAN OD #6 / governance §8 #6 toward **hybrid**, on the strength of the paper's weak-model data.
- **(n) "Right-size to the model" cognitive-load rule → governance §4.1/§4.3.** Decompose to fit
  the local model's coherent window; no further. Supersedes a naïve "few big workers" read of (a).
- **(o) "Verify self-report against actions" → governance §4.5.** The bridge/strong reviewer
  cross-checks explanations; never trust small-model introspection alone.
- **(p) "Coordination lives in the deterministic bridge, not the models" → PLAN §3 + governance
  §5.** Minimize agent-to-agent negotiation; the bridge does routing/handoff/gating.
- **(q) New open decision: capability-floor test → governance §8.** Before relying on the local
  fleet, **measure where our actual local models fall** on (i) instruction-following, (ii)
  structured-output reliability, (iii) coordination — and per task, ask **"org vs. single agent?"**
  given the GPT-5-MINI inversion. Don't assume multi-agent wins.
- **(r) Privacy/cost boundary for the judgment layer → PLAN §2.** Routing PM/monitor/reviewer to
  cloud Claude sends *some* context off-box — define what may leave (governance summaries, not raw
  proprietary code?) so the hybrid doesn't quietly breach the local-only posture.

---

## 7. Bottom line

- The literature describes **frontier-org** designs (rich, autonomous, NL-coordinated). Our
  **small-local-model** stack needs a **near-inverse**: **minimal org, deterministic coordination,
  heavy symbolic floor, and a strong-model judgment layer.**
- The paper's own weak-model data is the warrant: **GPT-5-MINI lost the org capability upside to
  coordination failure; GPT-4.1 kept a low ethics floor.** Our local fleet risks **both** — so we
  must (1) not assume multi-agent beats single-agent, (2) put judgment/oversight on the strongest
  model we can afford, and (3) let deterministic systems, not the weak models, carry coordination
  and enforcement.
- This **resolves the biggest open decision toward hybrid model assignment** — and adds a
  prerequisite: **measure our actual local models** before trusting them with fanned-out work.
- Across all four inputs now (Anthropic empirical paper, Team Topologies, scholarly governance
  frameworks, and this small-vs-frontier lens), the design is well-triangulated. The remaining
  work is to **fold the combined recommendations (a)–(r) into the PLAN + governance** and confirm
  the hybrid + capability-floor-test decisions with the operator.
