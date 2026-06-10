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
- ⬜ **P0.3** `agent-bridge` skeleton: **Python (FastAPI + Pydantic + Instructor)**; persistent
  **WebSocket** client (consume events) + REST poster; `/health`; config from env; Postgres state.
- ⬜ **P0.3b** `litellm` gateway: route `WORKER_MODEL`→local llama-swap; one **GBNF/JSON-schema
  constrained** structured call validated by Instructor (prove constrained decoding works on
  `qwen3.6-27b`). `JUDGE_MODEL` alias defined but local until P0.5 says otherwise.
- ⬜ **P0.4** Echo test: bridge sees an @mention event and posts a reply in the same thread
  (proves post→event→bridge→post). (PLAN §7 P0)
- ⬜ **P0.5** **Capability-floor test (prerequisite, §8 #13):** measure our local models on
  (i) instruction/charter-following, (ii) structured-output reliability, (iii) coordination.
  Output a go/no-go per judgment role: stays local vs. must use OpenRouter. Also record the
  per-task "org vs. single agent?" guidance (GPT-5-MINI inversion). Workers stay local regardless.

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
- ⬜ **P3.7** **Cost-tiered continuous supervision** (gate = escalation arm, §3): cheap-continuous
  (hooks/bus-logging/caps) always on; expensive-continuous (LLM monitor on `JUDGE_MODEL`)
  sampled/triggered + full at checkpoints. Floor/steering coupling **skews symbolic** on weak
  models (asymmetric neurosymbolic coupling). (§3, §4.2)

## P4 — Plan-stop-gates + review + self-report

- ⬜ **P4.1** **Plan-doc checkpoints**: worker plan docs encode `⛔ STOP — review required`
  between phases; enforcement rules in a **separate floor doc** so the editable plan can't drop
  them. (§4.5)
- ⬜ **P4.2** Bridge **blocks past a checkpoint** until review is cleared. (§4.5)
- ⬜ **P4.3** **Explain-intent**: at each stop the worker emits a structured explanation
  (intent / goal-as-understood / tradeoffs hit / what I'd flag). (§4.5, §4.3)
- ⬜ **P4.3b** **Verify, don't trust, the explanation**: bridge / judgment-model reviewer
  cross-checks the explanation against the actual diff/actions (audit trail). Small models
  confabulate — words are a lead, actions are ground truth. (§4.5)
- ⬜ **P4.4** **Differently-goaled reviewer**: bridge spawns a reviewer with an ethics/
  whole-picture goal (inline lens "find where this trades safety/scope for the metric");
  reviewer **reports to PM, cannot self-approve**. Config rejects a same-goal reviewer. (§4.4)
- ⬜ **P4.5** **Risk-gated review depth**: routine → 1 reviewer/none; irreversible/cross-effort
  → multi-lens panel (correctness/security/scope/ethics). Don't let review become a wake-storm. (§4.4, §8 #9)
- ⬜ **P4.6** PM aggregates review → re-grounds worker (§4.3) → worker refactors → continue.
- ⬜ **P4.7** **Reviewers run on `JUDGE_MODEL`** (local-first → OpenRouter if P0 mandates);
  small-model review paired with deterministic checks (tests/lints/scope-diff). (§4.4)
- ⬜ **P4.8** **Lateral concern channel**: a worker can raise a cross-domain concern to a peer/
  reviewer, but it surfaces on the bus and routes to the PM (never private resolution, never peer
  merge-authority). Mark this channel exempt from rate/flow caps. (§4.4, §5)

## P5 — Dynamic roles + worker pool + scope ledger + provenance routing

- ⬜ **P5.0** **Worker pool + concurrency scheduler (the real lift, PLAN §3.6)**: stand up N
  `(little-coder + open-terminal)` instance pairs; bridge instance registry; `assign_effort`
  acquires an instance under a **`MAX_CONCURRENT_WORKERS` semaphore** that honors
  **interactive-always-wins backoff** (little-coder §3.5/§12.5). Default backend **3 parallel @
  ~83k** (burst 4 @ 64k; never 32k); fleet cap = slots − interactive reserve (~1–2 workers).
  Queue efforts when no instance is free. (OD-8)
- ⬜ **P5.1** **Scope ledger**: who's authorized for what path/domain; requests logged; deny
  self-granted scope (hard-rule #2). (§5, §4.1)
- ⬜ **P5.2** **Role authority split**: PM may spin up another **instance** of an approved role;
  a **new role type** is PO-gated via the §3 gate (proposed charter + scope). (§4.1)
- ⬜ **P5.3** Optional **approved-role catalog** (auth/DB/frontend/infra pre-cleared); only
  novel domains escalate. (§8 #7)
- ⬜ **P5.4** **Last-owner provenance**: resolve "who last touched this area" — git-blame/last
  commit (v1) → ownership ledger (v1.5). Drives the A→B hand-off target. (PLAN OD-4)
- ⬜ **P5.5** Channel taxonomy: `#mgmt`, `#effort-*`, `#incidents`, `#suggestions`, DMs.
- ⬜ **P5.6** **Wake-storm rate cap** per effort/window; exceeding → §3 trigger. **Applies to
  work chatter only — the brake/objection channel is exempt (sacred).** (§5)
- ⬜ **P5.7** **Stream-aligned, right-sized scoping**: bias workers toward end-to-end slices with
  constraints inline (§4.3), sized to the local model's coherent window; cognitive-load heuristic
  triggers a (reluctant) split. (§4.1, §8 #12)
- ⬜ **P5.8** **Retirement/decommission**: revoke scope from the ledger, retire role from the
  catalog, expire stale goals/rules, archive effort artifacts — all logged; PM vs PO authority per
  step. (§4.1 lifecycle, §8 #14)

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

- ⬜ **R.1** `agent-org` compose — `mattermost`, `mattermost-db`, `agent-bridge`, `litellm`, and
  the **pooled `little-coder`/`open-terminal` worker instances** (env, networks incl. external
  `ai-stack_llm-net`, host ports, depends_on, restart). (PLAN §3.2, §3.6)
- ⬜ **R.2** `scripts/emergency-recovery.ps1` + `.bat` — add all the above to the inventory
  and startup/shutdown sequences (after `llama-cpp` healthy on start; before main stack on stop).
- ⬜ **R.3** `.claude/skills/stack-map/references/workspace-stacks.md` — add the new project +
  service rows (networks, ports, dependency order). Run `/stack-map` to confirm no drift.

## P7 — Mobile + hardening (author here; exposure = operator)

- ⬜ **P7.1** PO mobile flow: install Mattermost app, PO = system admin (join any channel/DM),
  decide CONCERNs and trigger kill switch from phone. (§1, §3)
- ⬜ **P7.2** **Model-by-role (local-first, OpenRouter-where-mandatory)**: `WORKER_MODEL` local;
  `JUDGE_MODEL` local-first → **OpenRouter** large model only where the P0 floor test mandates.
  Wire OpenRouter egress: API key via env; **pin no-log/ZDR providers, prefer open-weight**;
  bridge builds + logs the **governance-summary-only** egress payload (no raw code/secrets);
  reuse `lc-egress`-style control if it fits. (§2.1 / §8 #6, #13)
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
