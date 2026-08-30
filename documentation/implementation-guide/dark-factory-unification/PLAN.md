# Dark Factory Unification — one org, pluggable substrates, one memory plane

Status: PLANNED 2026-08-29 (nothing implemented). Written from a grounded read of
all three sources at their current tips, not from memory:

- `agent-org/docs/ORCHESTRATION-DESIGN.md` — the org's ground truth (§1–§14)
- `implementation-guide/agent-memory-plane/PLAN.md` in the **documentation-plans-ai-stack**
  private repo (moved there 2026-08-29) — the NVIDIA
  ARC-AGI-3 / AVO adoption plan (typed memory, exposure invariant, three doors)
- `scripts/agent-harness/` + `documentation/implementation-guide/multi-agent-concurrency/`
  — this repo's session harness (anchor gate, pipeline, profiles), including the
  evidence from its first two days of real runs

Operator's directive (2026-08-29, verbatim intent): *these three components can
be one and the same — one agent org, usable for ai-stack or pointed at any
project via little-coder + open-terminal, using the NVIDIA AVO findings and the
proposed memory systems for long-horizon and short-term learned context. The
goal is a dark factory producing consistent, quality results, with determinism
and AI propagation each where they are best suited. The agent-org design work is
unique and stays — this is a merge of concepts, not a replacement.*

Operator extensions (2026-08-29, same day): git issues are an intake door and
the existing issue-ops effort is part of this plan; the gym is the component
where orchestration design is tested and iterated safely; Open Brain's
personal/agent/knowledge distinction is binding — code agents, local or cloud,
never access personal information; the self-target (ai-stack) vs
pointed-target (external project) distinction is a first-class axis; and the
research evidence is **pinned as the design's own North Star** — once
development completes and real-world outcomes drive design changes, those
changes are judged against the pinned research anchors, not re-derived.

---

## A. Binding design constraints (codebase standards)

These are not aspirations; every phase's review gate checks them, and a phase
that violates one does not land. They restate this codebase's existing
standards so the plan is self-contained:

1. **SOLID + modularity.** One responsibility per module; dependencies point
   one way; extension is a new row/module, not an edit to a working core
   (the harness's `$RoleRules` table and `common.ps1` composition root are
   the house pattern). Every component in §1 must be removable: a MODULE.md
   states its public surface and what to delete to delete it.
2. **Configuration over hardcoding.** Any value that would benefit from a
   config file lives in one. One config, multiple readers, and a
   cross-language test that asks every reader the same questions
   (`test_harness_config.py` is the template). No literal that encodes
   policy survives in source.
3. **Human-readable code.** Comments carry the *why* and the incident that
   paid for it, in the voice this repo already uses. A future maintainer must
   be able to reconstruct the decision from the file alone.
4. **Testing is designed in, per phase, not appended.** Every U-phase in §2
   names its validation before implementation starts (the anchor's
   acceptance criteria ARE the phase's test plan seed); every behavioural
   change carries a RED→GREEN reproduction; every protocol change extends an
   executable drill. A phase without a stated validation is not ready to
   start.
5. **Archive, don't delete; explain removals; findings to notes.** Existing
   repo conventions apply unchanged.

## B. The design loop — research anchors pinned as the design's North Star

The anchor mechanism applies to the design itself (operator, 2026-08-29):

- **The evidence ledger is the design's pinned anchor set:** the paper
  grounding in ORCHESTRATION-DESIGN §3, the NVIDIA AVO capability findings,
  the gym-run evidence (gym-007, gym-024, gym-027/029 — where the North-Star
  convergence design itself was uncovered), and this plan's §0 audit verdicts
  A1–A14 with their session evidence. Each is a claim with provenance, held
  to the same standard as any findings sink.
- **After development completes**, when real-world performance outcomes
  motivate modifying the original design, every proposed change is judged
  against the pinned anchors. A change consistent with the evidence proceeds;
  a change that *contradicts* a pinned anchor requires new evidence that
  amends the ledger on the record (`-AmendAnchor` semantics — stated reason,
  history entry, at a cost) — or it is refused. Outcomes may *add* anchors;
  they never silently erase one.
- **Why this exists:** it is the LLM-mirror problem (A3) at the meta level.
  A design re-derived from each round's outcomes oscillates exactly the way
  an LLM re-inventing "what's wrong with this" oscillates. The research
  anchors are the fixed external target that keeps post-development iteration
  convergent instead of circular.
- **Where iteration runs: the gym** (`d:\Open WebUI\ai-orchestration-gym`),
  promoted from an open decision to a named component. Design changes trial
  in gym sessions against measured outcomes before touching live ai-stack
  planes or a real target project — the precedent being that the Mode A/B
  convergence design was itself discovered there.

---

## C. Autonomous execution — the standing decision policy (operator grant, 2026-08-29)

Added while the plan is IN PRODUCTION, because the implementing session kept
stopping for questions this plan should already answer. The operator is away
during implementation; a paused factory is the failure mode this section
exists to prevent. **A wrong reversible decision costs a revert; a stalled
question costs hours of idle line.** Binding for any session implementing
this plan.

### C.1 The plan is the confirmed anchor for U0–U7

The operator confirmed this plan as a whole. Each U-phase's *What* +
*Validated by* row IS its confirmed anchor — do not stop to re-propose it.
`-AmendAnchor` semantics still apply when reality contradicts the plan: amend
on the record, log it (C.4), continue. Do not wait for a human acknowledgment
of the amendment unless it crosses a C.2 class-4 line.

**This effort does not run through `queue.ps1`'s gates.** The harness is the
thing this plan is building; governing its construction with its own
unfinished gates is circular, and it manufactures operator decisions that do
not exist. `harness.config.json` sets `human_gates.anchor: true` and
`-Submit` exits 5 without a human confirmation — **for U0–U7 items that gate
does not apply.** Do not propose an anchor for confirmation, do not wait for
a tester or reviewer hand-off, and never treat a queue state as a blocker.

The record for a U-phase is its **branch**: the commits, the `DECISIONS.md`
entries, the findings sink, and the suites and drills green — evidenced in
the commit message, not in queue state. (Observed 2026-08-29: `dfu-mem0` and
`dfu-anchor` both sat at `anchor-draft` while ~2,000 lines were committed and
building on them. The queue row was not a gate; it was a false record.)

The per-item anchor gate keeps its FULL force for end-user feature work,
where the operator owns an intent the agent can only guess at. That is the
case a real failure paid for. This is not that case.

### C.2 The decision ladder — every question routes here before it routes to a human

| Class | What | Do |
|---|---|---|
| **1 — routine** | Naming, file layout, internal API shapes, test structure, config key names, order of work inside a phase, which house pattern to follow | Decide silently, consistent with §A. Not even logged unless surprising. |
| **2 — judgment** | Plan ambiguities, discovered gaps, small scope adjustments, choosing between two defensible designs | Decide and CONTINUE. Pick the option that is (a) consistent with the pinned anchors (§0/§B), (b) most reversible, (c) closest to an existing house pattern — in that priority order. Record in DECISIONS.md (C.4) with the assumption stated. |
| **3 — preference** | Choices with no technical winner the operator might enjoy weighing in on; cosmetic/product-taste calls | Take the default, record it as a QUESTION in DECISIONS.md, and keep moving. Batched for the operator's return — never a blocker. |
| **4 — HARD STOP** | The only legitimate reasons to halt and wait | See the list below. Halt, post to the Mattermost thread, park that item, **work a different item meanwhile** if any is unblocked. |

**Class 4, exhaustively** (if it is not on this list, it is not a reason to stop):
- Merging or promoting anything to `main`.
- Deploying to production runtime: restarting Scheduled Tasks, recreating or
  stopping live containers, retagging `:local` images.
- Pushing to any remote (the OB1-remote-first step included) — commits to
  local work branches are expected and unrestricted.
- Anything touching the personal data plane, credentials, or secret values.
- Destroying data or any action with no clean revert.
- Spending real money or calling external services beyond the session itself.

### C.3 Formerly-open decisions — now DECIDED with standing defaults

§4's open items were themselves stop-and-ask generators. Resolved (operator
pre-authorized the recommended defaults, 2026-08-29):

1. Reviewer verdict rename (`-FitsAnchor` → `-FitsCodebase`, intent
   challenges route to the release gate): **fold into U2.**
2. Dark-mode default: **per-anchor**, `attended` unless the anchor opts in.
3. Unified config: **shared `org.config.json`**, multiple readers, the
   cross-language test — the pattern already proven in the harness.
4. Cadence scheduler owner: **supercronic** (OB1's crontab — the stack's
   single source of cron truth).
5. Issue intake: already superseded — the daily sweep takes everything;
   selection happens at the weekly verdict thread.

If implementation shows a default wrong, that is a class-2 decision: pick the
better option, log it with the evidence, continue.

### C.4 DECISIONS.md — judgment compounds instead of evaporating

Append-only log beside this plan
(`documentation/implementation-guide/dark-factory-unification/DECISIONS.md`).
One entry per class-2/3 call: timestamp, phase, the decision, the class, the
rule or anchor cited, and **how to revert it**. Before asking anything, check
the log for precedent — the same question is never asked twice. The operator
reviews it on return; a wrong call gets corrected then, which is the cheap
path by design.

**First action in a fresh worktree: merge the work line.** `DECISIONS.md` was
born in `d7d1676`, and so was this section — a worktree cut from an earlier
base contains neither. A session in that state is running the autonomy policy
from a chat paste rather than from the file, and would CREATE the log instead
of appending to it: an add/add conflict at merge, and its judgment invisible
to everyone until someone resolves it by hand. Merge the line first, then
read §C from the file.

### C.5 Report, don't ask

Progress, amendments and class-4 halts go to the operator's Mattermost thread
as **statements with a default and a deadline-free path forward** — "done X,
assumed Y (logged), next Z" — never as questions that gate the next step.
The operator interjects if they disagree; silence is not consent for class-4
actions (those still wait), but silence never stalls classes 1–3.

---

## 0. The audit — what each system assumes, and what the evidence now says

Each row was checked against the named source and, where possible, against what
actually happened in the harness's first real runs (2026-08-28/29: 5 work items,
9 test cycles, every failure a genuine defect). Verdicts: **CONVERGENT** (both
systems independently arrived here — keep), **FALSIFIED** (evidence contradicts
it — fix), **UNTESTED** (load-bearing but never exercised — flag), **GAP** (one
system has it, the other needs it).

| # | Assumption | Held by | Verdict + evidence |
|---|---|---|---|
| A1 | Workers are small local models, and "that constraint shapes everything" (§1) | agent-org | **PARTIALLY FALSIFIED.** The harness ran frontier agents and produced the *same* misalignment classes agent-org's design exists to contain: over-claimed findings (3 failed cycles on one item), searches truncated and read as conclusions (`head -20` ate a 16-hit result), right-answer-wrong-reason, and one reflexive `--no-verify` bypass. The operator observed the same independently. **What differs by model class is rate and scope-per-task, not kind.** So the constraint legitimately shapes *scope sizing and search strategy* (§4, §7) — it does not justify scoping the honesty machinery to small models. Governance is substrate-independent and belongs in the shared core. |
| A2 | "Gates produce honesty, not quality; quality comes from a coherent model aimed at a stable target" (§2.1–2.2) | agent-org | **CONVERGENT — and the harness is its second proof.** The harness's first soak passed every gate and shipped the wrong artifact (a README that was 46% defect log). Quality arrived only when the **anchor** (a fixed, operator-confirmed external target) was added; the gates then caught lies, not badness. The harness's own history is a controlled demonstration of agent-org's core insight. |
| A3 | "An LLM grading an LLM is a mirror" (§2.2) | agent-org | **CONVERGENT, with a live counter-mechanism.** The harness's tester signed off the same false `/readyz` claim three times — the mirror, exactly. What broke it each time was an *external referent*: the anchor's acceptance criteria, and the rule "verify against the code path, not the comment." Merged design: LLM judgment is only admitted where it is anchored to something executable or operator-confirmed. |
| A4 | The North Star is "a theme, not a hard target" (§6.6) | agent-org | **TRUE FOR MODE A ONLY — agent-org already says so itself.** §6.6 splits generative (Mode A) from adversarial/bounded (Mode B) convergence. The harness's anchor **is the Mode-B North Star**: bounded work can and must have a hard target with stated failure conditions. These are one concept with a mode field, not two concepts. |
| A5 | The human's judgment must compound, not evaporate (§2.3, §10) | agent-org | **CONVERGENT — and the harness is currently the violator.** agent-org BUILT and PROVED finding→durable-check (gym-007). The harness banked every lesson from 9 cycles as **prose in MERGE-PROTOCOL** — the exact evaporation §2.3 names. Highest-leverage single fix in this plan. |
| A6 | Prose test plans executed by an LLM tester are sufficient verification | harness (implicit) | **FALSIFIED by its own runs.** Plans missed the case that mattered on 3 of 5 items; one plan asked the tester to run a command whose *safety* depended on an unverified flag (`compose build --dry-run` — a real `--no-cache` rebuild if unsupported). agent-org's standard — executable checks, red→green, owned by the project, cannot regress — is strictly stronger. Prose cases become the fallback, not the norm. |
| A7 | Cloud/worktree agents can be governed normatively (rules in a protocol doc) | harness (implicit) | **FALSIFIED.** An agent reached for `--no-verify` on its first commit; `--no-verify` leaves **no trace in a git object** (verified — "hooks ran" is unprovable from the repo). agent-org's containment is mechanical: git-proxy hard-denies pushes to main, egress allowlist, workers structurally cannot mint expertise (memory-plane verified assumption #13). The memory plane's own principle — "enforced mechanically, never by model self-restraint" — applies to execution too. Mechanical wins; the harness needs containment parity. |
| A8 | Scope boundaries must be executable contracts "or the encapsulation is only nominal" (§4) | agent-org | **CONVERGENT + GAP.** The harness's anchor is a depth-1 scope contract — and its prose acceptance criteria were twice found wrong *by the work* (a criterion demanded a false statement about `/readyz`; an out-of-scope justification was false). Both incidents prove §4's point: prose contracts drift. The anchor should carry executable criteria where possible, and `-AmendAnchor` (built, costs a cycle by design) is the correction path §4 lacks. |
| A9 | Escalation must carry a structured payload, never a summary (§11) | agent-org | **CONVERGENT — the harness relearned it the hard way.** Three times the orchestrator relayed an agent's conclusion without opening the file, and twice it was wrong. The harness's queue already carries structured payloads (sha, evidence file, plan verdict); the *human-facing relay* was the lossy hop. Rule for the merged system: any claim crossing a tier is verified against the world or labelled unverified — same rule for the orchestrator as for the workers. |
| A10 | Determinism in the skeleton, AI in bounded slots (§6.5 prompt determinism; operator's stated goal) | both | **CONVERGENT.** agent-org designed it (§6.5, not yet built); the harness *built* a version of it: `queue.ps1` is a deterministic state machine whose only LLM judgments are bounded and forced-explicit (`-PlanAdequate`, `-FitsAnchor` — no defaults, refusal on omission). The harness's queue is the closest existing implementation of §6.5's philosophy. Port the pattern, not the PowerShell. |
| A11 | 95% small-model search / 5% frontier oracle-on-stall (§7) | agent-org | **UNTESTED and preserved.** The harness ran 100% frontier (its `little-coder` runner is wired, `status: unproven`, and honest about it). Nothing contradicts §7; nothing has exercised it. Proving the local runner through one real item is the concrete first step (Phase U4). |
| A12 | NVIDIA transfer: "100.00 RHAE on ARC-AGI-3 ⇒ these capabilities produce a software dark factory" | memory-plane plan (implicit) | **UNTESTED — adopt the capabilities, not the score.** ARC-AGI-3 is a game benchmark; the +11.8-RHAE typed-memory result is directional evidence, not proof of transfer to software delivery. What IS locally evidenced: the harness's 9 cycles repeatedly lost lessons to prose (A5), and agent-org's constraints demonstrably compound (§14 CDCL BUILT). The memory plane is justified by local evidence; the NVIDIA numbers are corroboration. |
| A13 | Three memory stores can coexist indefinitely (agent-bridge SQLite clauses/corpus; OB1 `agent_memories`; protocol prose) | all three (by inaction) | **GAP — this is the unification's center of gravity.** agent-org's clauses and checks are per-project SQLite; the harness's lessons are markdown; the memory plane is planned but empty. Nothing recalls across them. One durable plane (OB1, per the memory-plane plan) with the existing stores as write-through hot caches — not a rip-out. |
| A14 | The two orchestrations differ in kind: agent-org is any-project, the harness is ai-stack-only | operator (stated) | **MOSTLY DISSOLVED by the config work.** After `harness.config.json`, the harness's ai-stack residue is lease names, env-file lists, branch defaults — all configuration. The real differences are substrate (cloud sessions vs containerized local models) and depth (flat items vs tiered scopes). Both are the pluggable axes of the merged design. |

**Summary of the audit:** the systems disagree almost nowhere. Where they seem
to, one of them has already written down the resolution (§6.6 modes for A4;
mechanical enforcement for A7). The real findings are the two FALSIFIED
harness assumptions (A6, A7), one partially falsified agent-org assumption
(A1), and one structural gap all three share (A13). The unification is
therefore a *composition*, and agent-org — the deepest design of the three —
is its spine.

---

## 1. Target architecture — the org, layered

One organization. Six layers. Determinism owns the skeleton; AI propagation
owns the bounded slots; the human owns the edges. agent-org's unique design
work (tiered scope, CDCL, Mode A/B convergence, finding→check, liveness) is
the spine; the harness contributes the intent gate, the pipeline pattern, and
the substrate abstraction; the memory plane contributes durability.

```
 L1 INTENT      the anchor (mode A|B) — human-confirmed, amendable at a cost
 L2 SCOPE       tiered ScopeNodes with contracts (§4); flat items = depth 1
 L3 EXECUTION   runners: little-coder/open-terminal (local) | claude-code (cloud)
 L4 VERIFICATION deterministic pipeline; findings compile to executable checks
 L5 MEMORY      one typed plane (OB1) — constraints, checks, outcomes, lessons
 L6 SUPERVISION liveness + durable inbox + andon; escalation is structured data
```

### L1 — Intent: the anchor absorbs the North Star

One intent object for the whole org, replacing the harness's anchor JSON and
agent-org's `set_goal` prose as separate things:

- `mode: B` (bounded): goal, artifact, audience, acceptance (each criterion
  with a stated failure condition, **executable where possible** — a command,
  not a sentence, per A8), out_of_scope, findings_sink. This is today's anchor.
- `mode: A` (generative): the North Star theme + the standing constraint
  ledger (§6.6) — off-theme ideas become clauses, not tasks; zero propagation
  is a red flag, not success. No fixed acceptance list, because §6.6 is right
  that one cannot exist yet.
- Confirmed by the operator; `-AmendAnchor` semantics apply org-wide: the
  world turning out different is corrected on the record, at the cost of
  invalidating in-flight verdicts (stale-pass reasoning).
- **Human placement (operator, 2026-08-29):** the human replies *during work*
  and at the *pre-review release gate*. Review itself is for merge, clean
  code, and different eyes on codebase fit — **not** re-litigating intent.
  The reviewer's forced verdict changes accordingly: from "fits intent" to
  "fits the codebase" (`-FitsCodebase`/`-Misfits` + reason), with intent
  challenges routed back to the release gate, not decided at review.

**Intake doors.** Anchors arrive by two doors, and both end at the same
operator confirm gate — a door produces an `anchor-draft`, never a confirmed
anchor:

- **Operator direct** — chat/session, today's path.
- **Git issues** — the connection EXISTS: `scripts/issue-ops/issue_ops.py`
  (CLEANUP-PLAN Part M, built) already does issue → audited plan via headless
  `claude -p`, staleness against the remote tip (M.3), the focus lock and
  overlap radar (M.6), the Mattermost console (M.2), and GitHub-App auth —
  with plans in `documentation/issue-plans/` under the public-surface posture
  (minimal text on GitHub). This plan extends it with the **cadence the
  design always intended** (operator, 2026-08-29):
  - **Daily sweep:** check the repo's issues; new/changed issues get a plan
    generated (`issue_ops.py plan <N>` — the existing M.1 path, scheduled
    rather than on-demand).
  - **Weekly synthesis:** collect the week's plans and run the cross-plan
    pass — overlaps (the `radar` primitive, widened from plan-vs-PRs to
    plan-vs-plan), shared fixes, restructure considerations.
  - **The human is alerted in a Mattermost thread** carrying what was
    collected and the proposed plans, where the operator discusses the
    approach with an agent and renders **approve / deny / postpone** per
    plan. In the merged design that thread IS this door's operator-confirm
    gate: an approved plan becomes a confirmed anchor; denied is recorded;
    postponed re-enters a later weekly synthesis.
  - **On-demand `check` (operator, 2026-08-29):** the cadence never locks the
    human out. At any time the operator engages an agent — Mattermost thread
    or session — to check current issues and discuss *what needs fixing now*:
    the agent surfaces `issue_ops.py status` (issues × plans × freshness ×
    focus, the existing M.2 view), talks through priorities, and can trigger
    planning or bring a plan to verdict immediately, out of band of the
    weekly synthesis. Same gates, no cadence wait — the daily/weekly loop is
    the floor for unattended operation, not a ceiling on the human.
  Which tracker feeds the sweep follows the target axis (L3): self-target =
  this repo's issues via issue-ops as above; pointed target = agent-org's
  GitHub App / capability-plane intake (autonomous-project-lifecycle, D1/D4
  human-gated) — target-repo issues are user requests, expected uncommon,
  converted through the same plan → weekly thread → verdict path.

### L2 — Scope: the harness's items are depth-1 ScopeNodes

agent-org's tiered-scope model (§4) generalizes the harness rather than
competing with it. A harness work item ≡ a `ScopeNode` of depth 1 whose
contract is the anchor. Deeper projects get a frontier-drawn scope tree
(engineering risk 2: small models execute scopes, they do not choose seams);
ai-stack maintenance items stay flat. One data model, two depths.

### L3 — Execution: runners are the substrate axis

`harness.config.json`'s runner/profile mechanism becomes the org's, with
agent-org's containment as the standard for **both** substrates (A7):

- `little-coder` runner (local, containerized, git-proxy, egress-allowlisted,
  cannot mint expertise) — the factory's default worker, per §7's 95%.
- `claude-code` runner (cloud) — the oracle-on-stall, the seam-drawer, the
  reviewer of last resort, and the whole line when the operator selects
  `all-cloud` (extension sessions stay locked to it).
- Containment parity work: worktree agents get mechanical guards — at minimum
  hook-bypass detection (a commit whose content fails the hook chain cannot
  have run it) and a commit-path proxy equivalent where feasible. Normative
  rules remain as documentation, never as the enforcement.

**The target axis** (operator, 2026-08-29) is orthogonal to the runner axis
and lives in configuration, not code:

- `target: self` — the org works on ai-stack itself: worktrees off the
  operator's loaded branch, plane leases for shared runtime, this repo's
  issues as intake, merges hand back when the line is checked out.
- `target: project(<repo>)` — the org is pointed at an external project:
  agent-org's clone/workspace model, git-proxy containment, the target's own
  branch policy, the GitHub App as the door, gym-style measurement.

Runner × target compose freely: local workers on a pointed project (the
factory default), cloud agents on self-target (today's harness), and the
other two quadrants when the profile says so. Nothing in the pipeline layer
knows which quadrant it is in.

### L4 — Verification: one pipeline, checks over prose

The deterministic skeleton is the harness's queue generalized (A10), with
agent-org's verification standards replacing the weak spots (A6):

- develop → **iterative test** (cycles advance only on real findings; the
  tester never wrote the work) → **human release gate** → review (codebase
  fit + merge; different eyes) → land. Terminal states verified against the
  world (`merge-base --is-ancestor` guard — a pipeline that can reach
  "merged" without merging is worse than no pipeline).
- **Findings compile.** Extend §10's finding→check beyond operator findings:
  a tester's confirmed finding becomes a durable executable check owned by
  the project (red→green, cannot regress), and a failure signature becomes a
  CDCL clause. Prose lands in the plan only when no executable form exists,
  and says so.
- **Dark-factory mode:** the mid-line human gate becomes a raised exception
  rather than a standing station. Andon conditions (all observed in real
  runs, none invented): anchor proven wrong by the work; scope violation;
  two ready items colliding on one file; the change touches deploy/runtime;
  cycle count over threshold; tester–reviewer disagreement; any terminal-
  state verification failure. Absent an andon, the line runs from confirmed
  anchor to landed without waiting; the release gate remains for `attended`
  profiles and is auto-passed with an audit record in `dark` profile.

### L5 — Memory: one plane, three hot caches (A13)

Adopt the memory-plane plan as written (it is already grounded to file/line);
this plan adds only the unification mapping:

| Existing store | Becomes (in OB1 `agent_memories`) | Trust level |
|---|---|---|
| agent-org CDCL clauses (SQLite) | `memory_type='constraint'` | evidence_only on write; instruction-grade only via operator confirm — the schema's CHECK already enforces exactly agent-org's trust ladder |
| acceptance-check corpus | `memory_type='check'` (executable payload) | operator-confirmed = instruction-grade; §10's pipeline is the elevation path |
| effort outcomes (`_finish_effort`) | `output` / `failure` | evidence_only |
| harness protocol lessons (today: prose) | `lesson`, recalled into briefs | evidence_only; promotion by recurrence + operator confirm |

Local stores stay as write-through caches (agent-bridge keeps its SQLite hot
path; nothing slows the inner loop on a network hop). Recall is governed and
self-bounded per memory-plane Phase 3; exposure follows the access-bounds-
writes invariant unchanged. Short-term context = per-effort brief injection;
long-horizon = cross-project recall + Phase-4 reflection. This is the AVO
adoption: supervisor + persistent state + typed memory + typed gates, mapped
onto stores that already exist or are already planned.

**The three-plane distinction is binding** (operator, 2026-08-29): Open Brain
holds **personal** (the operator's life: mail-derived research, assistant
surfaces), **agent** (what the org learns: constraints, checks, outcomes,
lessons), and **knowledge** (the curated corpus/wiki). **Code agents — local
or cloud, any runner, either target — never access the personal plane.**
Enforced mechanically at the doors, never by writer self-restraint: the ops
door forces `exposure='ops'` and allowlists only agent-memory tools; taint
propagation demotes any effort that consumed personal-plane inputs; the only
elevation path is human review. Recall into a code agent's brief draws from
agent + knowledge planes only. This is the memory-plane plan's exposure
invariant restated as the org's hard boundary — a unification that weakened
it would be a regression, so U-phase reviews check it explicitly.

### L6 — Supervision: the orchestrator becomes a component, not a mood

Today's orchestrator (a chat session's attention) demonstrably drops
subscriptions and relays unverified claims. Replace with: agent-org's
liveness detection (§8, built) + the queue as persistent state + a **durable
inbox** for operator I/O (append-only per-thread JSONL with a consumed
offset, replacing the one-shot Mattermost poller) + structured escalation
(§11). The judgment work an orchestrator does (verify before relay, amend
anchors, arbitrate collisions) is a bounded frontier slot with the same rule
as every other tier: claims crossing it are verified or labelled.

---

## 2. Phases

Ordered so nothing in flight is orphaned and every phase lands through the
pipeline it is strengthening. Each phase is its own anchored item (or small
set), with the memory-plane plan proceeding as already written.

Every phase names its validation **before implementation starts** (design
constraint A.4); "gym" means measured runs in `ai-orchestration-gym`, never
live planes or a real target.

| Phase | What | Validated by | Depends on |
|---|---|---|---|
| **U0** | Land what is in flight: the harness-v2 work goes through its own pipeline (operator decision, 2026-08-29 — no privileged actor); the three reviewed items merge; the durable Mattermost inbox replaces the one-shot poller | Each item's own anchor + tester; inbox: a kill-the-poller drill proves no message is lost | — |
| **U1** | Memory plane Phases 0–2 as planned (schema deploy, ops door, write paths). This is the substrate everything else writes to | The memory-plane plan's own per-phase gates (already written, file/line-grounded) | U0 |
| **U2** | **Intent unification:** shared anchor schema (mode A/B); intake doors incl. **git issues on the daily/weekly cadence** — daily sweep schedules M.1 planning, weekly synthesis (plan-vs-plan radar) posts the Mattermost verdict thread (approve/deny/postpone = this door's confirm gate) on both targets; agent-org intake consumes/produces anchors (`set_goal` seam, orchestrator.py:5950); reviewer verdict re-scoped to codebase-fit; queue items become depth-1 ScopeNodes | Gym: one goal driven from a git issue through sweep→plan→weekly thread→approve→land on each target; a deliberately overlapping issue pair must be flagged by the synthesis; schema cross-reader test | U0 |
| **U3** | **Verification unification:** tester-finding→durable-check in both systems (extend §10's built pipeline; harness findings write `memory_type='check'`); failure signatures→clauses write-through to the plane; executable-criteria support in anchors; port the harness's drill pattern to agent-org as an executable org drill | Gym: a seeded regression must be caught by a check born from a *tester* finding in a prior round (gym-007's shape, new source); drills green in both systems | U1, U2 |
| **U4** | **Runner unification:** prove `little-coder` through one real anchored item end to end (the standing unproven claim, A11); then agent-org workers as harness runners and vice versa — one profile mechanism governs both; frontier-oracle-on-stall wired per §7 | Gym: same anchored item run per quadrant (runner × target), outcomes compared; stall→oracle observed firing at least once | U2 |
| **U5** | **Containment parity:** mechanical guards for worktree/cloud agents (hook-bypass detection at minimum; commit-path proxy where feasible); `judge_enabled` calibration plan for expertise minting (the one-line §13 gap); personal-plane exclusion verified end to end | Adversarial drill: an agent instructed to bypass hooks / reach personal-plane data is mechanically stopped and the attempt is visible in an audit record | U0 |
| **U6** | **Dark-factory mode:** andon-condition config; `dark` vs `attended` gate profiles; auto-passed gates leave audit records; recall-informed briefs at all four seams (memory-plane Phase 3) | Gym: an unattended run that hits each andon condition halts-and-raises; one that hits none lands with a complete audit trail | U1–U5 |
| **U7 (standing)** | **Post-development design iteration** per §B: real-world outcomes → proposed design changes → judged against the pinned research anchors → trialed in the gym → adopted or refused on the record | The evidence ledger itself: every design change carries its anchor citation or its ledger amendment | U6 |

Explicitly NOT in scope: rewriting agent-org's orchestrator; replacing
Mattermost; auto-elevation of any memory to instruction-grade; deleting the
local SQLite stores; porting queue.ps1 to Python for its own sake. The spine
stays; the concepts merge.

## 3. What each system keeps (the pride audit)

- **agent-org keeps:** the tiered-scope model, CDCL constraint learning, the
  Mode A/B convergence split and the North Star, finding→durable-check,
  liveness-by-silence, the containment posture, the 95/5 substrate split,
  the GitHub App / issue-ops intake with its public-surface posture, and the
  gym as its proving ground. These are the unique design assets; nothing
  here replaces them.
- **The harness keeps:** the anchor gate (as the org-wide intent object), the
  deterministic queue pattern with forced-explicit judgments, separation of
  duties, profiles/runners, the executable drill, and its two days of paid-for
  ground rules — which move from prose into the memory plane (A5).
- **The memory plane keeps:** its entire plan, unmodified — it was already
  designed as the substrate this unification needs.

## 4. Open decisions for the operator — ALL RESOLVED, see §C.3

Kept for the record of what was open and when; the standing defaults in §C.3
govern. Original items:

1. **Reviewer verdict rename** (`-FitsAnchor` → `-FitsCodebase` + routing of
   intent challenges to the release gate): fold into U2, or ship earlier as a
   harness-only change?
2. **Dark-mode default:** when U6 lands, is `attended` the default with `dark`
   opt-in per anchor, or per profile? (Recommend: per anchor — riskier items
   stay attended regardless of the session's profile.)
3. **Where the unified org's config lives:** extend `harness.config.json` or
   promote a shared `org.config.json` consumed by both agent-bridge and the
   harness? (Recommend: shared file, two readers, cross-language test — the
   pattern that already works.)
4. ~~The gym as the proving ground~~ **DECIDED (operator, 2026-08-29):** the
   gym is a named component (§B) and every phase's validation column names
   it. No longer open.
5. ~~Issue-intake trigger~~ **SUPERSEDED (operator, 2026-08-29):** intake is
   the **daily sweep** over the repo's issues, not a per-issue marker — every
   new/changed issue gets a plan; selection happens at the weekly Mattermost
   thread (approve/deny/postpone), not at intake. Public-surface posture
   confirmed unchanged.
6. **Cadence anchoring:** daily sweep and weekly synthesis need a scheduler
   owner — the watchdog's host Scheduled Task family, OB1's supercronic
   crontab, or the bridge's wake loop. (Recommend: supercronic — single
   source of truth for cron in this stack, and the sweep is a container-
   reachable script away from either.)

---

## 5. Handover — implementing this plan (prepared for an Opus 5 session)

This plan will be implemented by a session that was not in the room when it was
designed. Everything below is what that session needs and cannot infer.
(Consonant with existing policy: issue-ops already tiers its planner/gate
models to `claude-opus-5` in `scripts/issue-ops/issue_ops.py` DEFAULTS.)

### 5.1 Read these first, in this order

1. `CLAUDE.md` — workspace ground rules; the worktree-per-session policy is
   binding on your FIRST mutating intent, not at session start.
2. `documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md`
   — the pipeline you will both use and extend. The "cases that keep earning
   their place" and the ground rules are paid-for; do not relearn them.
3. `agent-org/docs/ORCHESTRATION-DESIGN.md` — the spine. §2, §4, §6.5–6.6,
   §7, §10, §14. Do not redesign what §14 marks BUILT + PROVEN.
4. `implementation-guide/agent-memory-plane/PLAN.md` in the **documentation-plans-ai-stack**
   private repo (clone it beside this one) — adopt as
   written; its verified-assumptions ledger (file/line) is current as of
   2026-08-25 — re-verify anchors older than your session before relying.
5. `scripts/agent-harness/MODULE.md` + `harness.config.json` — the module
   boundary and config pattern you will extend.
6. This plan: §A/§B/§C are binding; §0's audit verdicts are pinned anchors.
   **§C is the answer to every "should I ask the operator?" moment — route the
   question through its ladder before routing it to a human.**

### 5.2 The state you inherit (as of 2026-08-29)

- **U0 is NOT done and blocks everything.** Three items sit reviewed and
  unmerged (`coder-rm`, `search-rm` — FITS ANCHOR; `watchdog-fix` — FITS,
  reviewed with a residual-risk acceptance on the `stack-services.json`
  `file`/`env_file` fields). Merge order matters: **coder-rm first, then
  search-rm, then watchdog-fix** — watchdog-fix's `coder/README.md` hunk
  conflicts with coder-rm's rewrite; resolution is take coder-rm's file
  (`git checkout --ours coder/README.md`). The operator's DST fix in
  `scripts/checks/stack-watchdog.ps1` (~L1294) must be stashed around the
  watchdog merge.
- **The main checkout's dirty tree is the harness-v2 work** (module rename
  `scripts/worktree→scripts/agent-harness`, config layer, anchor mechanism,
  bridge changes, secrets `env_file` removals). Operator decision: it goes
  **through the pipeline** — a developer who is not its author proposes its
  anchor, a tester tests it, the operator releases, a reviewer lands it. It
  blocks the three merges above (staged renames collide), so it is the first
  item. The operator's OWN files in that tree (`stack-watchdog.ps1` DST fix,
  `OB1` gitlink, `agent-org/docker/docker-compose.yml`, the bridge DESIGN.md
  edit) are NOT part of it — never sweep them into a commit.
- **Queue state** lives in `.git/agent-worktrees/queue/` (shared via
  `--git-common-dir`); the drill is `verify-merge-protocol.ps1` (51/51 green
  at handover — run it after any queue.ps1 change).
- **Not yet queued, known work:** the OB1 wiki-compile re-arm-on-failure fix
  (analysis in the Mattermost thread, 2026-08-29: failure path at
  `wiki-service.mjs` ~L1111 schedules nothing; boot race vs PostgREST
  PGRST002); `emergency-recovery.ps1`'s 16 bare compose invocations (10
  service-naming — findings in `documentation/notes/watchdog-findings.md`);
  agent-org's two ao-workers' `env_file: ../../.env`.

### 5.3 Rules that are enforced, not advisory

- **You are not a privileged actor.** Your own changes go through the
  pipeline like everyone's. The queue refuses developer-tests-own-work
  (exit 4), submit-without-confirmed-anchor (exit 5), merge-without-fitness
  verdict, and merge shas that don't contain the branch.
- **Verify before you relay.** The dominant error class of the design
  sessions (A9): claims passed along without opening the file. Three
  incidents were the orchestrator's own. Read to the end of the function;
  check the stated reason, not only the claim; a truncated search is not a
  search; your PATH is not the operator's PATH.
- **Never `--no-verify`; never commit/push on the operator's behalf; OB1
  changes push to the OB1 remote FIRST, then gitlink-bump via PR.**
- **PS 5.1 traps** (each cost a real failure): never `2>&1` a native command
  under `$ErrorActionPreference='Stop'`; capturing native output has the same
  trap — flip the preference around the call (`Invoke-GitCapture` is the
  house pattern); ASCII no-BOM for scripts PS5.1 parses; heredocs eat
  backslashes in Windows paths — use Write/Edit tools and grep for orphan
  fragments after.

### 5.4 Suggested first actions

1. Run `verify-merge-protocol.ps1` and `python -m pytest scripts/agent-harness
   scripts/claude-sessions-bridge -q` — confirm the inherited green.
2. Execute U0 in order: harness-v2 item through the pipeline → three merges →
   durable inbox.
3. Propose the U1 anchor (memory-plane Phase 0) to the operator before
   touching anything OB1-side.
4. Keep the operator's Mattermost thread as the reporting surface; verdicts
   and gate decisions belong there, with the queue as the record.
