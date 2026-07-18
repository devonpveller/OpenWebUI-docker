# Orchestration Design — Ground Truth

**What this is.** The distilled design for the agent-org orchestration system, from the
2026-07-17 brainstorm between the operator and Claude. It is deliberately kept **clean of dev
logs, experiment mechanics, commit hashes, and register churn** — those live in
[`P9-make-the-fixes-real.md`](P9-make-the-fixes-real.md), which is the execution record. This
file is the *design north star*: read it to understand what we are building and why.

**We are building the ORCHESTRATION, not any test project.** The todo CLI used in the gym is a
*probe* — a vehicle for measuring the orchestration's behavior. Nothing here is about making a
todo app good; it is about the org that builds software autonomously with a human as governor.

**Status in one line** (2026-07-17). **Built + deployed:** worker liveness (§8), the
finding→durable-check pipeline (§10, *proven* live), CDCL constraint learning + fixed-point drain
(§5–6), faithful escalation (§11), and the tiered scope tree (§4, foundation). **Not built:** the
frontier/OpenRouter oracle (§7), the security standing adversary (§9), and — inside the pieces
above — per-task executable goal-posts, a first-class diff-check, the adversarial task-drain,
auto-conversion of reviews into checks, and wiring scope nodes into planning/dispatch. See §14.

---

## 1. The goal

A **dark factory** for software: an autonomous organization of AI agents that takes a project
goal and delivers working software, with the **human acting only as governor** — setting
direction, judging quality, and holding the irreversible gates (merges to `main`). The workers
are **small local models** (~27B), not frontier models. That constraint shapes everything.

---

## 2. The core insight

Three findings, earned the hard way, define the whole direction:

1. **Gates produce honesty, not quality.** Adding checks (plan gates, closure invariants, QA
   lenses) makes the org *tell the truth about what it did*. It does not make what it did
   *good*. Quality is a separate axis, and stacking gates never moved it.

2. **Quality comes from a coherent model aimed at a stable target.** A worker produces good work
   when it holds a coherent model of the code and aims at a fixed, external standard. It produces
   noise when the target is self-generated — an LLM re-inventing "what's wrong with this" each
   pass oscillates forever and converges on nothing. **An LLM grading an LLM is a mirror.**

3. **The human's judgment must COMPOUND, not evaporate.** Today every operator review lives once,
   in a chat thread, and is gone. The org cannot learn the human's standard from a message it
   never reads — so it repeats the same class of defect round after round. The entire system's
   leverage is in turning each human judgment into something **durable and executable** that the
   org carries forward and cannot regress on.

The program is therefore about **model and target**, not more gates.

---

## 3. Research grounding

Grounded against the operator-provided paper *"AI Organizations Are More Effective But Less
Aligned Than Individual Agents"* (Anthropic + Constellation/MATS, arXiv:2604.10290) and
Anthropic's *"How we built our multi-agent research system."*

- **On small models, the multi-agent advantage inverts.** The paper's "orgs beat individuals"
  holds for frontier models; on the smaller models it tested, single agents match or beat the
  org *due to coordination failures*. Better alignment training shrinks the multi-agent penalty —
  so the benefit is a frontier-model phenomenon, and a local 27B sits at the wrong end of that
  curve. **This is the operator's caveat, confirmed.**

- **The failure mechanism is compartmentalization from AMBIGUOUS decomposition** — not
  decomposition itself. The paper's own words: *"sub-tasks that do not strictly specify clear
  constraints and handoffs → verification failures."* Anthropic's fix — *"each subagent needs an
  objective, an output format, and clear task boundaries"* — is exactly a sharp-scope contract.

- **Coding is a poor fit for naive multi-agent** (few truly parallel sub-tasks, heavy shared
  context). So parallelism is *not* our justification. The one honest justification the research
  leaves us is **horizon**: a project spanning many components genuinely exceeds a single
  context window. That — not speed — is why this org should exist.

**Our one economic advantage over the research's caution:** their "don't do this" for coding is
substantially a *token-cost* argument (multi-agent ~15× tokens). Our marginal token is
electricity. So loops that would be uneconomic in a product are affordable here — provided they
*converge* (see §7).

---

## 4. The tiered-scope model (the composition layer)

The operator's architecture, in their frame:

- The goal decomposes into **single tasks, each delegated to a single worker.** From the worker's
  view it holds **one task at a time and is unaware of the bigger picture.** This deliberately
  sidesteps the small-model long-horizon failure: the worker never carries the horizon.
- The org is **tiered task-lists, top-down.** The lever with small models is **SCOPE**: a small
  model cannot plan the whole codebase, but it can plan and execute one *tier's* bounded scope.
  Plan the scaffolding tier by tier — borrowing/blending well-defined industry architectures — 
  until code defines the scope border.
- **Top-down planning, bottom-up escalation.** A worker's work surfaces issues; those escalate to
  the **superior tier that owns the adjacent scope**; where scopes touch, mismatches become
  *proposed tasks* that stay within a tier's scope.
- **This is encapsulation / SOLID applied to the org.** The long horizon is *relative to each
  scope and intentionally excluded from the project's global horizon* — each scope protects its
  own horizon.

**Why it holds against the research:** it is the paper's own remedy (sharp contracts) *scaled
into tiers*. The paper never tested small models on bounded, well-specified scopes — so it does
not refute this; it refutes the ambiguous decomposition this model also rejects.

**The load-bearing claim:** encapsulation works in code because the interface is a *checkable
contract* (a violation throws a lossless exception). So **each tier's scope boundary must be an
executable contract, or the encapsulation is only nominal** — prose scope judged by an LLM is the
paper's ambiguous handoff wearing a SOLID costume.

### The engineering risks (where to build, not where it's wrong)

1. **Escalation faithfulness is the crux — the paper's proven-lossy step.** Between LLM tiers,
   escalation is prose a superior re-interprets and can drop or soften. The model relies on it
   working. **Fix:** escalation carries a *structured payload* — the failing test, the exact
   violated constraint — never a summary. (See §11; the operator's cleared-context adversarial
   review largely solves the *generation* half of this.)
2. **The top tier that draws the first scope boundary needs horizon vision the small model
   lacks.** A wrong seam dooms everything below it. **Fix:** a frontier model or human at the
   scope-definition tier — small models *execute* scopes, they should not *choose the seams*.
3. **Depth is a reliability tax even when tokens are free** — small per-hop losses multiply
   (90%-faithful × 3 tiers ≈ 73%). **Rule:** tier only as deep as the scope genuinely nests. (This
   tax mostly dissolves if escalation is lossless per §11.)

---

## 5. The worker execution loop (the unit)

The composition sits on one repeated unit: how ONE worker executes ONE bounded task. It is
deliberately **CDCL-shaped** (see §6) — the intelligence lives in the loop, not the model. The
model proposes; the environment remembers.

1. **Goal-post** — the module goal as an **executable acceptance test**, not prose. The clearest
   possible prompt. No path to it is given.
2. **Attempt** — the small model runs guided search toward the goal-post. A *proposer*, not a
   reasoner.
3. **Liveness** — a per-worker **silence detector** bounds the turn, forcing termination only if
   the worker goes silent (see §9). Not a wall-clock deadline.
4. **Diff-check** — on termination, evaluate the change (the git diff) against the module goal's
   test. *Absence of a meaningful change = not solved* — the same branch as a wrong change.
5. **Pass** → route the outcome as context; a **sequential** sweep of adversarial prompts each
   append scoped `module goal + task` items to the **queue** (a *map that accumulates*, never a
   single reduce over all prompts — a reduce is a horizon problem for a small model).
6. **Fail / empty** → if reproducible in a **cleared context**, record the failure as a
   **constraint**; wipe; retry with the narrowed search. (A hang is *not* a constraint — it is an
   absence, not "this approach is wrong.")
7. **Drain** → the list is done when a full sweep of the QA lenses **propagates zero new tasks**
   (§6.5). The honest zero: a *counted quantity*, not a model saying "none" — that phrasing is the
   bias §6.5 exists to remove. A scope completes when its goal is met AND all lenses are green.
8. **Escalate** → when guided search plateaus, invoke the frontier oracle (§8).

**The queue is the synthesis substrate.** No model ever holds all the adversarial outcomes (the
horizon problem). The queue carries them; dedup is mechanical (same module+task = same item).
Synthesis happens *at the queue*, precisely because the queue needs no horizon.

---

## 6. Convergence — conflict-driven clause learning

The loop is **CDCL** (how modern SAT solvers work): a dumb proposer plus a growing set of learned
constraints solves what no single reasoning step can. Each reproducible failure is a learned
clause that prunes the search space. That pedigree is the *convergence evidence* — the reason the
loop terminates rather than wandering.

**Hygiene:** only reproducible, attributable failures become constraints. Noise (a flaky test, a
hang) corrupts the search and must never be recorded as a constraint.

---

## 6.5 Prompt determinism — reasoning vs structural propagation

**The principle (operator, 2026-07-18).** The model will reason regardless. The question is *what it
reasons toward*. **If it reasons toward an answer, the answer is biased toward completion** — the
model is hungry to finish the task it was given, and "yes, it's done" is the cheapest way to finish.
If the prompt states an **objective** instead, the reasoning goes into *how to satisfy that
objective*, because the objective becomes the target. **So prompts must avoid asking for a verdict
and target determinism instead.**

This is a first-class defect class, not a style preference. Evidence from our own system: the QA
prompt contained

```
DEFECTS: … Say `none` ONLY if adversarial testing genuinely surfaced zero.
FOLLOWUPS: … Say `none` if none.
```

and in gym-008 the functional lens took exactly that affordance — *"No defects or follow-ups
surfaced"* — on a codebase where a differently-framed lens found real defects (`SystemExit` in the
data layer, unhandled `IsADirectoryError`, REPL greedy token-stripping) and an operator review of a
comparable product found 5 bugs + 3 gaps. **Same code, two prompts, opposite answers.** A prompt that
sanctions "nothing" will be told "nothing."

### The rules

1. **Never offer a "nothing" affordance.** No "say none if none."
2. **Never ask for a verdict where an observation will do.** Not *"is this goal accomplished?"* but
   *"find the gaps in the solution for the problem this codebase is attempting to solve."*
3. **Keep the GOAL OUT of the observation prompt.** This is the structural fix and it is the
   counter-intuitive one: an observation prompt that contains the goal invites the model to reason
   *toward* that goal and declare it met. Observe first, compare second.
4. **State the task plainly and thoroughly.** The prompt must *be* the work.
5. **Terminate on a counted quantity, never on a model's opinion** — see propagation below.

### The two-step alignment (why the goal is withheld)

> *Alignment begins with objectivity and ends in reasoning.*

```
STEP 1 — OBSERVE (no goal present):  codebase → objective report of what literally exists
STEP 2 — COMPARE (goal present):     report vs THIS SCOPE'S goal → GAPS
                                     gaps = misalignment = work = TASKS
```

Step 1 is arbitrary and reusable — the same prompt works on any codebase. Step 2 is the reasoning
effort, and **with the scope as its constraint it is obtainable by a small model** (which is exactly
why scopes are bounded, §4). Gap generation is where tasks are *discovered organically* rather than
invented.

### The three standing QA lenses (operator's, verbatim — these are the reference)

**1. Goal alignment** — *objective observation, then reasoned gap analysis*
> "test the codebase thoroughly treating as a final product, checking each function, find gaps in the
> solution for the problem the codebase is attempting to solve and write a short report. Do not edit
> files in this codebase, this is just evaluative."

Note: **no goal in the prompt.** The resulting report is compared against the scope's goal to produce
gaps.

**2. Clean code** — *objective task generation*
> "evaluate the codebase code cleanliness, is the code practicing SOLID, industry standard
> programming patterns, clear naming conventions and does the code support good documentation? How
> does or doesn't this codebase support documentation for its code?"

*Why:* most defects distil to bad organisation or an architecture that is hard to work with. Cleaner
code takes longer to write but **reduces plan token counts**, because changes become less complicated
to implement. Proactive, and measurable against known standards — hence *objective*.

**3. Project documentation** — *reasoning task generation*
> "evaluate the comments in the git repo through its history here. Are the titles and descriptions
> clear with intent focused and enough to grasp an evolving projects history? how does is the
> information helpful and how could the information be better written for you to be able to pick up
> the project where it left off?"

*Why:* a well-documented project reduces the time a worker spends getting acclimated, and reduces plan
token generation. Deliberately a *reasoning* lens (what would a future reader need?) as opposed to
lens 2's objective one.

### Termination by propagation, not by verdict

Each QA lens yields items; each item propagates tasks. Work the tasks, re-run the lenses, count again.

```
QA lenses → N tasks → work → QA → M tasks → … → a full pass propagates ZERO new tasks
zero propagation  ⇒  requirements met  ⇒  scope complete (goal met AND QA all green)
```

The stopping condition is **a counted quantity**, removing the model's opinion from the decision
entirely — the same discipline as the acceptance corpus (§10) and executable contracts (§11). QA must
therefore have **sufficient items to check against**; a lens that re-derives its own scope each round
produces a drifting count (measured live: defects trickled 6 → 5 → "none").

**Corollary on task size:** handing a worker too much produces incomplete work, which resurfaces as QA
items anyway. Small scopes are not only a context-window concern — they are what makes the propagation
count meaningful.

---

## 7. Frontier vs small model — explore vs execute

- The **frontier explores via knowledge** — it holds the considerations in-weights and finds the
  path in ~one shot.
- The **small model explores via guided search** — it holds nothing and narrows over time via
  accumulated determinism (§6).

Both reach the solution. The cost-effective split is roughly **95% small-model search, 5%
frontier unstick.** The frontier is an **oracle invoked on a stall signal** — not a better
worker. It injects the one constraint that only knowledge provides, then hands back to the small
model. *This is what the paused OpenRouter connection is for.*

---

## 8. Liveness — silence detection, not a timer

A worker that hangs mid-turn looks "busy" forever, so any status-based check defers indefinitely.
The fix reframes the question from *"has the task run too long?"* to **"has the worker emitted any
agent-loop event in the last T?"**

- A working worker emits a constant stream (generation, tool call, edit) → it bumps its activity
  and is **never interrupted, however long the task runs.**
- A hung worker is silent immediately → **caught fast**, so the threshold can be small and
  generous at once.

The signal must be **agent-loop activity specifically** — not "is the container doing something"
(dominated by health-check noise = a false heartbeat), and not shell-command activity (frozen
during long generation/edit phases). *This piece is built and deployed.*

---

## 9. Security — the same loop, standing

Point the orchestration at the **deployed product as an adversary**: exploits and breach-attempts
are failing tests → constraints → patch tasks → re-attack → drain. Rules:

1. **Weight the adversary toward the SEAMS** — borrowed-package safety does not compose at the
   wiring, which is where breaches actually live.
2. **Re-runnable on a schedule**, not a one-time gate — CVEs disclose continuously; clean code
   becomes vulnerable while unchanged.
3. **Security research is a frontier role** (known-CVE knowledge); patching is small-model
   execution.
4. **Governance hard requirement:** an autonomous breach-attempt generator is dual-use. It is
   scoped by the floor, attacks only the org's own product in a controlled environment, and never
   reaches real user data or external systems. The pre-deployment curated vulnerability list is a
   **human-governor artifact** (like the merge gate), not auto-dispositioned.

---

## 10. The compounding mechanism — operator finding → durable check

This is the system's highest-leverage capability and the direct answer to §2.3.

**The pipeline:** the operator reviews a delivery and states a finding → the org converts that
finding into a **durable, executable check owned by the project** → every future round must
satisfy it and *cannot regress on it.*

The intelligence is in the **conversion and persistence**, not in any single test. A human writing
the tests by hand is *building the test project*, not the orchestration — the orchestration is the
mechanism that captures a human judgment once and enforces it forever. This is the "prose
`standing_intent` grown teeth" idea: the operator's review of round N becomes the machine gate of
round N+1.

**Why it is mandated, not hypothetical:** across two rounds of the same probe, the operator's
hand-review found largely the *same* defects (missing reopen, REPL text mangling, no sort, overdue
not flagged, non-monotonic IDs) — while the org's own QA loop ran and fixed *other* defects,
because the operator's findings were never in its target. The org demonstrably cannot learn the
human's bar from a chat thread. This mechanism is how it learns.

---

## 11. Escalation faithfulness (the composition's crux)

The operator's answer to the paper's lossy-escalation problem: escalation is **cleared-context
adversarial review**, and *the results are the escalation*. A fresh reviewer isn't carrying the
builder's rationalizations, and it emits concrete test results, not softened prose — which defeats
the *generation* half of the loss.

The residual is **incorporation and routing**: does the receiving tier actually resolve the
concern, and does it reach the right tier? The clean fix is mechanical: the escalated concern is a
**ticket that cannot close until its own failing test passes.** Then "resolved" is verified, not
asserted, and a routing error surfaces because the wrong tier can't make the test green.

This unifies with §5 and §10: **every boundary in the system — module interface, escalation,
human finding — is an executable contract.** That single principle is the spine of the design.

---

## 12. Open decisions

Two forks are genuinely undecided and shape what gets built:

- **Executable contract from day one, or prose that hardens into tests?** The whole design leans
  on contracts being *executable*. The recommendation is executable from day one — prose scopes
  judged by an LLM reintroduce the ambiguous handoff. *(Operator's answers throughout — `{x}`/`{y}`
  packaging, test-result escalation, ticket-gated-on-a-test — read as already committing to
  executable-from-day-one; to be confirmed.)*

- **Should the finding→check conversion (§10) be automatic or operator-in-the-loop?** i.e. does the
  org read a review and write the check itself, or does the operator approve each check before it
  becomes durable? Given the human-as-governor stance, the latter is likely — but this is the
  operator's to set, and it shapes the whole pipeline.

## 13. Open edges (unsolved, flagged)

- **Seam placement for novel projects.** The loop resolves interface mismatches *within* a
  decomposition, but cannot detect that the decomposition *itself* is wrong — that a boundary is
  misplaced — because that needs both scopes at once, which no encapsulated worker has. Mitigated by
  borrowing proven architectures; the residual risk is genuinely-novel projects with no reference
  architecture, where the scope-definition tier needs real horizon (a frontier or human).

---

## 14. What is built vs designed

| Piece | State |
|---|---|
| Worker liveness (silence detector, §8) | **BUILT + DEPLOYED + validated** |
| Finding → durable check pipeline (§10) | **BUILT + DEPLOYED + PROVEN** — gym-007 (PR#15): a recurring operator finding (missing `reopen`), captured once as a durable check, forced the third round's org to ship a *working* reopen the goal never asked for. The recurrence is broken; the human's judgment compounded. Auto-conversion + scale still pending. |
| CDCL constraint learning + fixed-point drain (§5–6) | **BUILT + DEPLOYED** — failures become durable clauses (infra never learned), injected into every retry; `seen_sigs` set makes the burn-down a real fixed point. *Still open: per-task executable goal-posts, first-class diff-check, adversarial task-drain.* |
| Frontier / OpenRouter oracle (§7) | designed, not built |
| Security standing adversary (§9) | designed, not built |
| Faithful escalation (§11) | **BUILT + DEPLOYED** — escalations carry their executable check; a ticket cannot close until that check has passed (abort/override still close; override audited). Respects §3.0: the check runs while ACTIVE, the clear consults the record. |
| Tiered scope tree (§4) | **BUILT + DEPLOYED (foundation)** — `ScopeNode` tree with depth, bounded per-tier worker brief (own scope + contract; the rest withheld; border named), escalation routes to the adjacent-scope owner. *Still open: wiring nodes into planning/dispatch.* |
| Prompt determinism + 3 standing lenses (§6.5) | **designed, not built** — the QA prompt still contains the `none` affordance and embeds the goal in the observation step |

**Forks resolved (§12):** the finding→check pipeline was built **executable-from-day-one** (a check
is a command run against the delivery, not prose) and **operator-in-the-loop** (a governor-issued
`accept check for <project>: <command> :: <note>`, not auto-conversion). Auto-conversion remains a
future addition on top of this base.

The build sequence and its evidence are tracked in `P9-make-the-fixes-real.md`. This document is
the *what and why*; that one is the *how and when*.
