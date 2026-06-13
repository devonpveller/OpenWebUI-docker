# UX Flow — the user journey & the intent thread

**Status:** 📝 DESIGN (2026-06-13). The user-facing lifecycle that ties the governance spine
(PO↔PM↔workers, gate, grounding, stop-gates, review) into one journey.
**Companion docs:** [PLAN](PLAN-teams-chat-agent-orchestration.md) · [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) · the ANALYSIS docs.
**Tag key:** ✅ already in the plan · 🆕 added here · ⚠️ tension/tunable.

---

## 0. The intent thread (the spine of the whole UX)

Every implementation carries **one intent thread** — a single, living statement of *what the
user actually wants and why* — from the original (vague) request through anchoring, planning,
execution, and every escalation. It **is** the canonical objective of governance §4.3, made
**user-visible and continuous**.

The UX exists to do two things to that thread, and nothing else:
1. **Sharpen it** — turn a vague ask into a grounded, workspace-anchored intent (Stages 0–3).
2. **Defend it** — never let work drift from it silently; every fork in the thread surfaces back
   to the PO with intent attached (Stages 4–6).

> Practical artifact: the intent thread is the **header of the plan doc** (§4.5) and the **first
> field of every CONCERN** (§3.x). When anything is escalated, the question is always *"does this
> still serve the intent, and if not, how should the intent change?"* — never a bare technical
> choice.

---

## 1. Roles & which model lane each runs on

> **Terminology (2026-06-13):** **PO = Project Overseer**, an *agent* (not the human). The
> **human (you)** is the tier above the PO. PO + PM are two agents with different perspectives
> (overseer = big-picture/ethics; manager = execution) — see governance §1.

| Tier / role | Who | Model lane | Why |
|-------------|-----|-----------|-----|
| **Human (you)** | the person | — | top of the ladder; sets the request, approves plans, clears §3 hard-gate triggers; primary contact = the PO |
| **PO — Project Overseer** | agent | **cloud (larger, OpenRouter)** | big picture / UX vision / **owner of the intent thread** / security-ethics; your point of contact; differently-goaled check on the PM |
| **PM — Project Manager** | agent | **cloud** | practical implementation + action-to-action alignment to the PO; decomposes/delegates/monitors workers |
| **Planner** | agent (PO/PM or a dedicated profile) | **cloud** | **plan generation is a judgment task** — a weak planner caps the productivity ceiling of everything downstream, so it earns the cloud spend (operator, pt 2) |
| **Reviewer** (differently-goaled) | agent | **cloud** candidate | judgment / ethics lens (§4.4) — weak reviewers rubber-stamp |
| **Workers** | `little-coder` pool | **local `qwen36-27b`** | fenced execution; capability matters least here |

- **Lane is a per-profile field (§5.4), tuned empirically (operator, pt 6).** Start with
  judgment-heavy roles on cloud and workers local; **stretch the local boundary as practice shows
  what 27B can hold.** ⚠️ This is trial-and-error, not a fixed line.
- **Cost is governed** by the cloud LiteLLM's per-key budgets (C2), and **idle-wait (§5) keeps idle
  cloud agents from burning tokens** while they wait.
- Reminder: cloud = the *separate* `llm-gateway-cloud` (OpenRouter, no-log/ZDR), never the
  air-gapped local gateway (PLAN §3.4).

---

## 2. The lifecycle, stage by stage

### Stage 0 — Intake  ✅surface / 🆕affordance
- **You see:** post a vague request to `@po` (the Overseer — your point of contact) in a
  project/effort channel (Mattermost).
- **Backend:** bridge routes to the **PO**, which opens the **intent thread** (it owns it) tied to
  the project + branch, and engages the planner/PM.
- **Intent op:** *capture* the raw ask verbatim (the thread's origin).

### Stage 1 — Anchor + draft plan  🆕 (cloud planner)
- **Backend:** the planner reads the project's current workspace and drafts a plan **anchored to
  what's actually there** (existing code, branch, conventions). Output = a draft plan doc (§4.5).
- **Intent op:** *anchor* — bind the vague intent to concrete workspace reality.
- **Model:** cloud (judgment).

### Stage 2 — Readiness gate (your false/true branch)  🆕 / ⚠️
The planner asks: *is this plan clear AND safe to execute against this codebase?*
- **`false`** (gaps, or the request implies cascading refactors): **generate clarifying questions**
  derived from (a) plan gaps and (b) implementation-safety (how it fits existing code; blast
  radius). Ask the PO; **iterate**; the plan stays `draft` until coherent.
- **`true`** → proceed to Stage 3.
- **Intent op:** *disambiguate* — every question is a fork in the intent thread resolved by the PO.
- **Why it matters:** this is the **cheapest place to catch misalignment — before any worker
  spawns** (paper's goal-problem-first; governance F5 "don't guess, surface it").
- ⚠️ **Capability-gated:** judging clarity+safety is exactly where local 27B is weakest (F7) → a
  prime cloud-judge task.

### Stage 3 — Plan presentation + approval gate  ✅gate / 🆕artifact
- **User sees** a structured plan:
  1. **Feature Overview** — how it behaves *after* implementation; what changes in the workspace; what's added to the codebase.
  2. **Implementation Plan** — the steps (§4.5 stop-gates embedded).
  3. **Delegation Plan** — roles + ⚠️ **sequence/DAG, not "N parallel agents"** (concurrency is GPU-bounded to ~1–2, PLAN §3.6); shows where bounded parallelism applies.
  4. **Estimate** — a time range. ⚠️ **Cold-start:** until the learning loop (§6) has history, the estimate is rough or comes from the Stage-4 dry-run (which reveals real scope), not from past data.
- **The PO presents the plan to you; this is the top-level plan-stop-gate + *your* approval.**
  `approved` (by you) → Stage 4.
- **Intent op:** *commit* — you ratify the sharpened intent; the PO carries it forward.

### Stage 4 — Ground + dry-run (pre-execution validation)  🆕
Before touching real code, the orchestration **validates the plan against reality**:
- **Ground assumptions** — research the proposed frameworks/systems via `openbrain-research` (live).
- **Dry-run** — attempt the implementation in an **isolated throwaway branch/workspace** (little-coder's per-instance containment + git-proxy make this naturally safe), run tests, detect cascading breaks. **Never merges.**
- **Any issue here → escalate to PM** (Stage 6). This is "measure twice, cut once" *pre-commit*, so aborting is cheap.
- **Intent op:** *de-risk* — confirm the intent is achievable as planned, or surface a fork early.

### Stage 5 — Execution  ✅ (already designed)
The main loop: workers wake on @mention hand-offs, halt at plan stop-gates to **explain intent**
(§4.5), differently-goaled review (§4.4), self-report (§4.3), wake-the-last-owner on cross-scope
errors. The intent thread rides along as each worker's grounded goal.

### Stage 6 — Escalation (when a level can't resolve)  ✅ladder / 🆕framing
- **Ladder:** worker → PM → (next-level lead, if the org has one for that domain) → **PO**. Each
  level resolves what it can and passes up only what it can't.
- **Framing (the rule):** an escalation to the PO is **intent-framed** — see §3 below.
- **Intent op:** *fork* — present the branch in the intent thread and let the PO choose; work
  **pauses (idle-wait, §5)** until cleared.

---

## 3. The CONCERN schema (intent-framed) — for Stage 6

Every escalation that reaches the PO carries these fields (sharpens governance §3):

```jsonc
{
  "intent_thread": "…",          // the original/current intent this work serves
  "what_surfaced": "…",          // the issue/deviation/refusal/conflict
  "intent_of_change": "…",       // WHY it matters to the intent/outcome (not just "what broke")
  "options": [
    { "action": "…",
      "effect_on_outcome": "…",  // how THIS option changes the outcome vs. the intent
      "risk": "…" }
  ],
  "pm_recommendation": "…",      // which option + why, in intent terms
  "blocked_efforts": ["…"]       // what is paused (idle-waiting) pending the decision
}
```

The hard rule: **no bare technical choice reaches the PO.** Both the issue and every
recommendation state *how they affect the outcome relative to the intent* (operator's pt 4).

---

## 4. The escalation ladder (generalizes governance §1)

```
 worker ──can't resolve──▶ PM ──can't resolve──▶ PO ──hard-gate only──▶ human (you, top)
        each level resolves what it can; passes up only what it can't; intent attached at every hop
```

- The **PM** resolves most *execution* queries (it owns whole-task context); the **PO** resolves
  most *big-picture/UX/ethics steering*; only the **§3 hard-gate triggers** (irreversible/external,
  unresolved ethics, refusal) reach the **human**.
- Dynamic sub-leads (§4.1) add rungs only when a domain genuinely needs one (keep the org small).

---

## 5. Concurrency as a DAG with idle-waits (Stage 7 — the keystone)

The bounded GPU budget (~1–2 slots, §3.6) only works because **agents hold a slot only while
actively computing.** Three bridge states:

| State | Holds a slot? | Entered when | Woken by |
|-------|---------------|--------------|----------|
| **active** | ✅ yes | doing work | — |
| **waiting** (idle) | ❌ **releases slot** | voluntarily yields while a dependency is pending — PO decision, dry-run, build, **or another agent's effort** | a **`finish` event** or a **timeout** |
| **frozen** | ❌ no work | the safety gate freezes the effort (§3) | PO clears the CONCERN |

- **Dependency DAG (operator, pt 7):** an agent blocked on another's output goes **waiting**
  (slot freed), and wakes on that effort's `finish`. So efforts run **"linearly" by dependency**
  (waiter idle, not spinning) while **independent efforts parallelize** up to the slot budget.
- **"Together when their work touches":** when two efforts **overlap** (touch the same files/area),
  they coordinate through the bus + a handoff contract; a true collision is an **F4 cross-effort
  conflict → pause + escalate**, never blind parallel edits.
- **Maps to existing primitives:** Claude Code's wait/`ScheduleWakeup`; little-coder's `--session`
  suspend/resume (a parked session costs no inference). The bridge owns the wait registry +
  wake-on-event.
- **Why it's load-bearing:** idle-wait is what lets a ~1–2-slot budget run a multi-agent org —
  and what keeps **idle cloud (PM/judge) agents from burning OpenRouter tokens** while they wait.

---

## 6. Open tunables & cold-starts (carry into build)

- **Role→lane tuning (⚠️ empirical, pt 6):** which roles are cloud vs local; stretch local as 27B
  proves capable. Lives in profiles (§5.4); spend governed by cloud-LiteLLM budgets.
- **Estimate cold-start (⚠️):** no history early → lean on the Stage-4 dry-run for scope; accrue
  real ranges via the learning loop (§6) / journals.
- **Delegation = DAG, not headcount (⚠️):** "#agents" is bounded by the GPU budget; present
  sequence + bounded parallelism, not wide fan-out.
- **Readiness + dry-run interpretation are judgment (⚠️):** cloud-lane tasks; quality gates the
  whole journey.

---

## 7. Stage → governance/plan cross-reference

| Stage | Primary home in the other docs |
|-------|--------------------------------|
| 0 Intake | PLAN §5.2 (Mattermost channels); governance §1 |
| 1 Anchor + draft | governance §4.3 (goal-grounding); §4.5 (plan doc); PLAN §3.4 (cloud planner) |
| 2 Readiness gate | governance F5; §4.3; **🆕 new phase — fold into PLAN phases** |
| 3 Plan + approve | governance §4.5 (top stop-gate) + §3 (PO decision); PLAN §3.6 (delegation budget) |
| 4 Ground + dry-run | `openbrain-research`; **🆕 new phase — fold into PLAN phases**; governance "verify before claiming" |
| 5 Execution | governance §3–§6 (the main loop) |
| 6 Escalation | governance §3 (gate) + §3.x CONCERN schema (this doc) + §4.4 |
| 7 Idle-wait DAG | PLAN §3.6 (concurrency) — **🆕 add the three-state model** |

> **Folds owed into PLAN/governance** (so this doc stays the journey, not the spec): intake +
> readiness-gate + ground/dry-run as explicit phases; the idle-wait three-state model into §3.6;
> the CONCERN intent-schema + ladder into governance §3; plan-generation = cloud + role-lane
> tuning into §3.4/§5.4.
