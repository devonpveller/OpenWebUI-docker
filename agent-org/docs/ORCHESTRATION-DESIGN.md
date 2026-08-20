# Orchestration Design — Ground Truth

**What this is.** The distilled design for the agent-org orchestration system, from the
2026-07-17 brainstorm between the operator and Claude. It is deliberately kept **clean of dev
logs, experiment mechanics, commit hashes, and register churn** — those live in
[`log/P9-make-the-fixes-real.md`](log/P9-make-the-fixes-real.md), which is the execution record. This
file is the *design north star*: read it to understand what we are building and why.

**We are building the ORCHESTRATION, not any test project.** The todo CLI used in the gym is a
*probe* — a vehicle for measuring the orchestration's behavior. Nothing here is about making a
todo app good; it is about the org that builds software autonomously with a human as governor.

**Status in one line** (2026-07-26). **Built + deployed:** worker liveness (§8), the
finding→durable-check pipeline (§10, *proven* live), CDCL constraint learning + fixed-point drain
(§5–6), faithful escalation (§11), the tiered scope tree (§4, foundation), and **Mode A — generative
convergence (§6.6): North-Star realignment, deterministic off-theme→constraint, goal-lens resilience —
PROVEN (gym-027/029 reached `scope_completed` on real work, one self-repairing a red build).** **Not
built:** the frontier/OpenRouter oracle (§7), the security standing adversary (§9), **Mode B —
adversarial hardening + its data ledger (§9.5) — slice 1 built, rest designed**, the **North-Star
alignment gate (§6.6.1) — designed 2026-07-29, being built next** (the general form of Mode A's
misaligned→constraint; the built git-meta filter is only its deterministic sliver — its absence let a
misaligned tail run gym-035 ~13h/2109 events), and — inside the pieces above — per-task executable
goal-posts, a first-class diff-check, auto-conversion of reviews into checks, and wiring scope nodes into
planning/dispatch. See §14.

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

**Refinement (§6.6).** "Zero propagation" is precise for one mode of the loop and wrong for the other.
§6.6 separates *generative discovery* (which must never be forced to zero) from *adversarial hardening*
(which should approach zero, diagnosed by data) — read it before treating a non-zero count as failure.

---

## 6.6 The North Star and the two convergence modes (operator, 2026-07-23)

§6.5 terminates on "zero propagation." Applied to the whole loop that is a category error: it is exactly
right for one mode and exactly wrong for the other, and conflating them made a *delivered* product read as
"unconverged" (gym-024: a complete, D2-passing PR that closed on a plateau of 2–4 because an off-theme tail
kept inflating the count). Three streams flow through the loop; they converge differently.

**The North Star is the original prompt.** The human element is not a checkpoint the org waits on each
round — it *already happened*. The prompt that initiated the effort **is** the goal, and it is a **theme,
not a hard target**: a direction the work is *found toward*, not a spec to be exhausted. Every round's gap
analysis (§6.5 Step 2) realigns against **that original prompt** — not a re-derived scope goal, whose drift
§6.5 already warns produces a meaningless count. Maintaining alignment to the North Star is what keeps a
generative loop from wandering; it is the standing form of the human-as-governor (§1), spent once, up front.

**Mode A — generative discovery (does NOT go to zero).** Task generation propagates *toward* the theme. As
elements land, their inclusion inspires adjacent ideas that were invisible before — the goal **comes into
focus by walking the path**, not by checking off a fixed list. So:
- Ideas that **align** with the North Star become **tasks** that advance and sharpen the path.
- Ideas that **do not align** become **constraints** that *narrow* it — "the way to this goal does not run
  here." Not worked, not silently dropped: recorded as a clause (§6) that focuses every later round. This
  extends CDCL (§6) from *reproducible failures* to *off-theme drift* — both are "this direction is wrong."
- **Zero propagation here is a red flag, not success.** It more likely means the search went blind (a lens
  re-deriving its own scope, or one that stopped seeing) than that the theme is exhausted. Convergence in
  Mode A is the **path tightening on the theme**, delivered as coherent increments; the human's merge (§1)
  is the checkpoint, not a count.

**Mode B — adversarial hardening ("polish") (SHOULD approach zero).** A distinct, *contrarian* loop pointed
at a delivered increment — §9 generalized past security. Deliberately shift perspective to **break** the
thing: expose **bugs**, **exploits**, and **edge cases**. Unlike Mode A it *does* have a diminishing-returns
floor — a given surface can only be hardened so far. But **it fluctuates by approach**: a security lens, an
edge-case lens, and a break-the-parser lens each surface different things, so one lens drying up is not
"hardened." Completion is **diminishing returns across DIVERSE approaches** — K varied contrarian
perspectives all coming up near-empty — the loop-until-dry pattern applied to *perspectives*, not rounds.

**Cosmetic / commit-hygiene is neither mode.** "Rewrite this commit body", "split the scaffold commit" —
off-theme meta-work that is not the product and not an attack surface. It is Mode-A misalignment: it becomes
a **constraint** (narrow away from it), never a task. Left uncaught it inflates the count and stalls both
modes. (Note: the project-documentation lens of §6.5 legitimately observes commit history for *future-worker
readability*; the failure is treating its output as **product** propagation. It is process/meta work — a
non-counting backlog at most, never a Mode-A termination gate.)

### 6.6.1 The North-Star alignment gate — and why a runaway is a misalignment symptom (operator, 2026-07-29)

Mode A says *misaligned → constraint*. What was **built** enforced only a sliver of that: a deterministic
git-meta filter (P28, `_sort_off_theme`) that catches "names a commit/SHA" and nothing else. There was no
**general** check of whether the work about to be done actually serves the North Star. So off-North-Star
work reached the worker team unchecked, and the loop wandered.

gym-035 is the proof. A run that had already delivered a polished product kept generating and dispatching
work its *own workers escalated as off-scope* — packaging metadata, linting config, a `__version__`
attribute (developer-facing, not the "a real person would enjoy" North Star), a commit-subject rewrite
(git-meta), and an unbounded tail of REPL corner-cases no real person would hit. Nothing asked "does this
serve the North Star?" before handing it over, so it ran **~13 hours / 2109 events**, abandoning and
recovering and re-sticking, long after the gym harness's wall budget stopped *watching*.

**The lesson: a runaway is a symptom of missing North-Star alignment, not of too many rounds.** An arbitrary
round/event cap is the wrong fix — a large project legitimately needs far more rounds than a small one, so a
fixed limit punishes complexity instead of catching drift. The correct bound is *convergence by exhausting
the **aligned** task list*: off-tangent plans never become work, so the loop tightens on the theme and stops
on its own; a big project still gets every round it needs.

**The gate.** Before any of a round's proposed work is handed to the worker team, a **North-Star alignment
check** judges the round's **generated task list against the original prompt**, at generation (right after
P28's deterministic git-meta filter). **A candidate that serves the North Star → stays a task. One that
serves it in no way → a constraint (§6), never worked.** This is the standing, general form of Mode A's
*misaligned → constraint*; the P28 git-meta filter is one cheap deterministic case of it, not the whole.

**Judge the GROUP, never a task in isolation (operator, 2026-07-29).** Alignment is a property of the plan,
not of a line item. An enabling/scaffolding task ("add a `_normalize_priority` helper") is a tangent *alone*
but essential to an aligned group ("sort the list by priority"). Checked in isolation it would be falsely
pruned — and the aligned work it enables could never land. So the check sees the **entire round's proposal
at once** and flags a candidate **only when it serves no part of the North Star even given the rest of the
group**. That also answers "2 of 12 are off": the checker keeps the 10 aligned *and* any enabling steps
among them, and constrains only the genuine tangents — no all-or-nothing, no amputated scaffolding.

**Why this is a reasoning pass and still not a P26 mirror — context management is the mechanism.** Alignment
to a *theme* ("does this serve a polished todo app a real person would enjoy?") is genuinely interpretive; it
needs an LLM, and P26 warned an LLM grading an LLM is a mirror. But **the mirror is a shared-context
artifact, not a property of LLM-checks-LLM.** P26 inverted because the checker shared the generator's frame
— same context in, same confusion out. The fix is per-pass context management:

- **Task generation** gets **North Star + the lens reports + a summary of what's already built**. It needs
  the current state to decide *what to propose next*.
- **The alignment check** gets **North Star + the candidate list. Only.** It is blind to the current-state
  summary and to the generator's reasoning. Its single question is "which of these serve no part of the
  North Star, even as a group?"

The asymmetry *is* the independence: the checker cannot inherit the generator's rationalization ("given
everything we've built, this next step makes sense") because it never receives the context that produced it.
Current state is what a plan needs to be *generated*; it is irrelevant to whether a plan *serves the goal*, so
withholding it is correct, not a limitation. **Standing principle: independence between a producer and its
checker comes from the difference in their contexts — not from adding a second model with the same view.**
Every LLM pass in the loop gets exactly the context its job needs, no more; that discipline is what makes a
check a check and not an echo.

**P33 (gym-037, 2026-07-30) — the gate belongs on the GAP candidates, NOT on `derived`.** As first built,
the alignment check ran on the whole `derived` list. gym-037's audit proved that was backwards: `derived`
holds only goal-gaps (from gap analysis) and DEFECTs (from the lens grader) — genuine, product-serving
correctness work *by construction* — and the interpretive gate **amputated it**, pruning the `load_items`
crash fix, duplicate-ID detection, exception hygiene and date validation as "off-North-Star"
(`off_north_star_pruned kept:0` in rounds 3 AND 4). That is the P26 "an LLM grading an LLM over-prunes real
work" failure resurrected — the exact thing P28's deterministic filter was built to retire, reintroduced by
pointing the interpretive gate at the real-work list. Meanwhile the ACTUAL off-North-Star tangents
(linting/packaging config, a SOLID refactor, commit-message conventions) never reach `derived` at all —
they are GAP-graded (§P13.6/P15.2: "required but not malfunctioning") and queued on the separate
`_pending_gaps` path, which bypassed the gate entirely. **So the gate was gutting the real work and waving
the tangents through.** The fix: run BOTH gates — deterministic git-meta (P28) then the interpretive
North-Star group gate — on the **GAP-candidate list**, and leave `derived` guarded only by the
deterministic git-meta filter (which cannot amputate product code). A DEFECT or a goal-gap is, by
definition, in service of the product; it is never a "tangent nobody asked for," so it must never face the
interpretive verdict. This is the same lesson as P28, one level up: **place the interpretive alignment
verdict only where a genuine tangent can appear (GAP-graded polish), never where the grade already proves
the work is real.**

### Diagnosing Mode B by data, not assertion (operator, 2026-07-23)

"Polish is done" must be a **reading, not a guess**, and the reading requires evidence that each round
actually *improved* the product. The ledger reuses what the design already has:
- **Findings per round, tagged by approach, gated by reproducibility** (§6 hygiene: only a reproducible,
  attributable failure registers; noise never does, or the curve lies).
- **The standing adversarial corpus (§10) is the improvement ledger.** Each reproduced finding becomes a
  durable check that flips **red→green** and must *stay* green. Corpus growth = cumulative hardening; a round
  adding many red→green checks improved the product; a round adding none across diverse approaches did not.
- **The completion signal is then measured:** new reproducible findings/round → ~0 across K diverse
  perspectives, corpus growth plateaus, zero regressions. That is Mode B complete *for this increment* —
  until the schedule (§9.2) or a new increment re-opens the surface.

This is the same discipline as §2.3 and §10: the human's "is it hardened enough?" judgment becomes a
*measured, durable* quantity the org carries forward, not a per-round opinion.

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

## 9.5 Mode B — adversarial hardening, and the data that says "done" (operator, 2026-07-26)

§6.6 split the loop into two convergence modes. §5–6.6 built **Mode A** — generative discovery, which
propagates toward the North Star and converges on real work (proven twice: gym-027/029 reached an
evidenced `scope_completed`, one of them burning down a 13-error red build to green). This section is
**Mode B**: the standing *contrarian* loop that hardens a delivered Mode-A increment. §9 (security) is
its first special case; Mode B is the generalization to *all* breakage.

**Why it is distinct from Mode A's own QA — proven, not assumed.** Mode A's lenses passed **105 tests**
on the gym product and still missed three real bugs (an operator review, 2026-07-26, found
`db_path("")` silently returning cwd, an *unpersisted* schema version that breaks the migration
contract, and a parser rebuilt on every `list` call). A loop optimized to *satisfy* a goal does not find
where it *breaks*; a differently-goaled agent optimized to *refute* does (§2.3, the reviewer charter).
That review *is* Mode B's intended output. Two of its findings also expose Mode A's blind spot precisely:
the REPL parser is still hand-rolled and fragile *after* a drain round explicitly "fixed REPL parsing"
(a point-fix that did not generalize), and `cmd_add` rejects empty text while `cmd_edit` accepts it (the
fix touched one path, not the *consistency*). Contrarian "find the edge the fix missed" is the complement
Mode A structurally lacks.

**The unit.** Point the loop at the **delivered increment** as an adversary and shift perspective to
break it — expose **bugs, exploits, edge cases**. A *reproducible* break is a failing test → a
constraint → a patch task → re-attack → drain (§5–6, §9). Unlike Mode A, Mode B *should approach zero*:
a given surface has a diminishing-returns floor.

**It fluctuates by approach — so completion is diminishing returns across DIVERSE lenses**, never one
drying up. The reference lens set (the operator review's own categories):
- **correctness/logic** — a valid-but-unusual input that yields the wrong result (`db_path("")`);
- **edge-case/robustness** — hostile/boundary input (unicode, 10KB text, 1000+ items, empty-string
  consistency, unhandled exception types);
- **performance** — the hidden cost (a parser rebuilt per call);
- **fragility/DRY** — reimplemented logic that will rot (a hand-rolled parser beside `argparse`);
- **security-at-the-seams** (§9) — the composition boundary.
One lens empty is not "hardened"; K *diverse* lenses near-empty is.

**"Done" is a reading, not a guess — the data ledger** (extends §6.6's data subsection). The improvement
record is the **acceptance corpus (§10)**: every *reproducible, attributable* finding (§6 hygiene — noise
never registers, or the curve lies) becomes a durable check that flips **red→green** and must *stay*
green. Per round, tagged by lens: findings, reproduced, fixed, corpus delta. **Mode B is complete for
this increment when** new reproducible findings/round → ~0 across the diverse lenses, corpus growth
plateaus, and zero regressions — the same measured discipline as §6 termination and §10 compounding. The
corpus is what proves the product *measurably improved*, and it is the durable artifact carried into
every future round (§2.3): the human's bar, learned once, enforced forever.

**Governance (§9.4 applies).** An autonomous breakage generator is dual-use: scoped by the floor,
attacks only the org's own product in a controlled environment, never reaches real data or external
systems; the human governs *direction* (the standing lens set), not each finding.

**Placement — a distinct phase after Mode A delivers a coherent increment.** A converged product is the
stable surface an adversary attacks, and separating the phases keeps the two convergence models from
contaminating each other's counts (Mode A must not zero; Mode B should).

**The acid test (first build's success criterion).** Mode B works when it autonomously surfaces the same
class the operator's hand-review found — **re-derived, not told**. First proof: without being handed the
report, Mode B surfaces the three bugs + the design/edge gaps; each reproduced finding becomes a corpus
check the org can never regress on. Then it compounds. (Seeding the corpus from the report directly is
the §10 operator-in-the-loop fork; we re-derive first precisely to test the capability.)

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
| Mode A — generative convergence (§6.6) | **BUILT + PROVEN** — North-Star realignment (P26), deterministic off-theme→constraint (P28, replacing an LLM verdict that amputated real work), goal-lens resilience (P27/P29: focused bounded retry + no-done-on-incomplete-sweep + bounded incomplete-sweep escalation). gym-027 and gym-029 reached a genuine `scope_completed` on real work (67 tasks, zero amputation), gym-029 self-repairing a 13-error red build. *In-loop triggers for P28's pruning and P29's retry are intermittent — ready insurance, unit-validated, awaiting a run that fires them.* |
| Mode B — adversarial hardening + data ledger (§9.5) | **designed, being built next** — the contrarian loop that drives to ~0 (bugs/exploits/edge-cases across diverse lenses), each reproducible finding a durable §10 red→green check; "done" is measured (findings/round→0 across lenses, corpus green). Acid test: autonomously re-derive the 2026-07-26 operator review's 3 bugs + gaps. |

**Forks resolved (§12):** the finding→check pipeline was built **executable-from-day-one** (a check
is a command run against the delivery, not prose) and **operator-in-the-loop** (a governor-issued
`accept check for <project>: <command> :: <note>`, not auto-conversion). Auto-conversion remains a
future addition on top of this base.

The build sequence and its evidence are tracked in `log/P9-make-the-fixes-real.md`. This document is
the *what and why*; that one is the *how and when*.
