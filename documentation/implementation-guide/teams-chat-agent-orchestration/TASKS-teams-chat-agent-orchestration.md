# Tasks — Teams-style Chat for Agent Orchestration

Companion to [PLAN-teams-chat-agent-orchestration.md](PLAN-teams-chat-agent-orchestration.md)
and the governing [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md).
Status keys: ⬜ todo · 🔧 in progress · ✅ done · 🧪 needs test · 🚀 deploy/operator.

> Governance refs in parentheses (e.g. §3, F3) point to the governance doc — each task should
> trace back to a control it implements.

---

## P0 — Platform spike

- ⬜ **P0.1** `agent-org` compose project scaffold: `mattermost` + `mattermost-db` (Postgres),
  attach external `ai-stack_llm-net`; internal net `agent-org`; `127.0.0.1` host ports.
- ⬜ **P0.2** Bring Mattermost up; create the org, a `#mgmt` channel, and one **bot account**
  (`@pm`) with an access token.
- ⬜ **P0.3** `agent-bridge` skeleton: persistent **WebSocket** client (consume events) +
  REST poster; `/health`; config from env; small persistent state store.
- ⬜ **P0.4** Echo test: bridge sees an @mention event and posts a reply in the same thread
  (proves post→event→bridge→post). (PLAN §7 P0)

## P1 — Wake mechanic

- ⬜ **P1.1** Channel↔effort↔session map in the bridge (`#effort-x` thread → little-coder
  `--session <thread_id>`). (memory: little-coder-per-chat-sessions)
- ⬜ **P1.2** `wake(worker, thread)` — resume a dormant `little-coder` session and deliver the
  thread context; worker reply posts back via the bridge.
- ⬜ **P1.3** A→B hand-off: worker A posts an @mention of B in an effort thread → B is woken →
  B replies in-thread (observable). (PLAN §7 P1)
- ⬜ **P1.4** Bus-only enforcement: workers have no transport but the bridge→Mattermost path
  (no side-channels). (§5)

## P2 — Escalation gate (CORE SAFETY — do not skip/defer)

- ⬜ **P2.1** Gate state machine in the bridge: per-effort state {active, frozen}; **persisted**
  so a frozen effort stays frozen across a bridge restart (fail-safe). (§3)
- ⬜ **P2.2** Triggers → freeze: refusal/objection, deviation, ambiguous scope, cross-effort
  conflict, irreversible/external action, unresolved disagreement, wake-storm cap. (§3 triggers)
- ⬜ **P2.3** **CONCERN** message: bridge posts a structured CONCERN to `#mgmt` (what/why/
  options/recommendation) and freezes the effort **+ dependents**. (§3)
- ⬜ **P2.4** **PO decision** parse: PO reply → approve / modify-scope / abort → propagate →
  unfreeze; decision logged. (§3)
- ⬜ **P2.5** **Fail-safe default**: PO unavailable → stays paused, **no auto-resume, no
  ask-another-agent**. Negative test: refusal cannot be re-routed to a second worker (F3).
- ⬜ **P2.6** **Global kill switch**: PO command freezes the entire fleet. (§3)
- 🧪 **P2.7** Safety tests per PLAN §7 P2 (freeze holds, restart-keeps-frozen, no F3 reroute).

## P3 — Charters + grounding (rules-as-skills, floor/steering, goals)

- ⬜ **P3.1** Author **charters as skills** (PM/orchestrator, worker, reviewer) under
  `.claude/skills/` — hold whole-task view, escalation duties, bus-only, no self-clear. (§4)
- ⬜ **P3.2** **Floor vs steering split**: hard rules = always-on non-overridable skill;
  steering layer = mutable per-session inject. (§4.2)
- ⬜ **P3.3** **Hooks enforce the floor**: a PreToolUse-style hook blocks irreversible/external
  actions (push/deploy/delete/spend/send) without a cleared PO decision (hard-rule #4). (§4.2)
- ⬜ **P3.4** Rule/goal **version store** in the bridge; a floor change requires PO + version
  bump + audit entry; workers report which version they run. (§4.2)
- ⬜ **P3.5** **Goal injection**: bridge delivers the worker's goal with **constraints inline**
  on spawn/wake; PM-owned canonical objective → scoped slices. (§4.3)
- ⬜ **P3.6** **Re-ground in flight**: PM/PO edit a worker's goal; change reaches the worker on
  next turn/wake; re-ground that invalidates in-progress work → §3 freeze-and-surface. (§4.3)

## P4 — Plan-stop-gates + review + self-report

- ⬜ **P4.1** **Plan-doc checkpoints**: worker plan docs encode `⛔ STOP — review required`
  between phases; enforcement rules in a **separate floor doc** so the editable plan can't drop
  them. (§4.5)
- ⬜ **P4.2** Bridge **blocks past a checkpoint** until review is cleared. (§4.5)
- ⬜ **P4.3** **Explain-intent**: at each stop the worker emits a structured explanation
  (intent / goal-as-understood / tradeoffs hit / what I'd flag). (§4.5, §4.3)
- ⬜ **P4.4** **Differently-goaled reviewer**: bridge spawns a reviewer with an ethics/
  whole-picture goal (inline lens "find where this trades safety/scope for the metric");
  reviewer **reports to PM, cannot self-approve**. Config rejects a same-goal reviewer. (§4.4)
- ⬜ **P4.5** **Risk-gated review depth**: routine → 1 reviewer/none; irreversible/cross-effort
  → multi-lens panel (correctness/security/scope/ethics). Don't let review become a wake-storm. (§4.4, §8 #9)
- ⬜ **P4.6** PM aggregates review → re-grounds worker (§4.3) → worker refactors → continue.

## P5 — Dynamic roles + scope ledger + provenance routing

- ⬜ **P5.1** **Scope ledger**: who's authorized for what path/domain; requests logged; deny
  self-granted scope (hard-rule #2). (§5, §4.1)
- ⬜ **P5.2** **Role authority split**: PM may spin up another **instance** of an approved role;
  a **new role type** is PO-gated via the §3 gate (proposed charter + scope). (§4.1)
- ⬜ **P5.3** Optional **approved-role catalog** (auth/DB/frontend/infra pre-cleared); only
  novel domains escalate. (§8 #7)
- ⬜ **P5.4** **Last-owner provenance**: resolve "who last touched this area" — git-blame/last
  commit (v1) → ownership ledger (v1.5). Drives the A→B hand-off target. (PLAN OD-4)
- ⬜ **P5.5** Channel taxonomy: `#mgmt`, `#effort-*`, `#incidents`, `#suggestions`, DMs.
- ⬜ **P5.6** **Wake-storm rate cap** per effort/window; exceeding → §3 trigger. (§5)

## P6 — Audit trail + learning loop (propose-not-dispose)

- ⬜ **P6.1** **Full event log**: every wake, hand-off, CONCERN, decision, goal/rule change,
  review verdict — persisted with versions. (§5)
- ⬜ **P6.2** **Mirror to Open Brain**: critical hand-offs + decisions captured via
  `capture_thought` for durable, queryable provenance. (§5; memory: openbrain cloud gateway)
- ⬜ **P6.3** **Suggestion pool**: `#suggestions` channel → bridge collects worker suggestions
  for consideration. (§6)
- ⬜ **P6.4** **Pattern surfacing**: detect recurring failure/suggestion patterns across efforts
  (manual PM synthesis v1; assisted later) — route via Open Brain + claudeception. (§6)
- ⬜ **P6.5** **Propose-not-dispose flow**: pattern → PM synthesizes a *proposed* change → **PO
  approves** → lands via versioned floor/steering update (P3.4). **No auto-apply.** (§6)

## 3-place change (per new container)

- ⬜ **R.1** `agent-org` compose — `mattermost`, `mattermost-db`, `agent-bridge` (env, networks,
  host ports, depends_on, restart). (PLAN §3.2)
- ⬜ **R.2** `scripts/emergency-recovery.ps1` + `.bat` — add the three services to the inventory
  and startup/shutdown sequences (after `llama-cpp` healthy on start; before main stack on stop).
- ⬜ **R.3** `.claude/skills/stack-map/references/workspace-stacks.md` — add the new project +
  service rows (networks, ports, dependency order). Run `/stack-map` to confirm no drift.

## P7 — Mobile + hardening (author here; exposure = operator)

- ⬜ **P7.1** PO mobile flow: install Mattermost app, PO = system admin (join any channel/DM),
  decide CONCERNs and trigger kill switch from phone. (§1, §3)
- ⬜ **P7.2** **Model-by-role**: `PM_MODEL` / `WORKER_MODEL` config; option to route PM/monitor
  to a metered cloud Claude via the openbrain cloud gateway. (§2.1 / §8 #6)
- ⬜ **P7.3** CONCERN UX: optional Mattermost plugin for rich CONCERN/decision cards (else
  structured plain posts). (PLAN OD-5)
- 🚀 **P7.4** Operator: tailnet exposure (reuse `tailscale`); no public exposure; confirm no
  E2EE on agent channels (observability = safety). (§5)

---

## Sequencing

Build **P0 → P1 → P2** first — prove the loop *and* that we can stop it (P2 is the safety
spine; nothing scales before it). Then **P3 → P4** (the alignment core: charters, floor/hooks,
goal-grounding, stop-gates, review). Then **P5 → P6** (scale + the temporal learning loop).
**P7** last (mobile + exposure + model-by-role), operator-deployed.

**Hard gate:** do not begin **P5** (more roles, fan-out) until **P2** (stop) and **P3** (floor)
pass their safety tests — the paper says more roles = more misalignment, so the brakes must
exist before the fleet grows.
