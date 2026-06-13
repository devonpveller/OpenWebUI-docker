# Tasks — Teams-style Chat for Agent Orchestration

Companion to [PLAN-teams-chat-agent-orchestration.md](PLAN-teams-chat-agent-orchestration.md)
and the governing [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md).
New reader? Start at [README.md](README.md) for read-order + precedence.
Status keys: ⬜ todo · 🔧 in progress · ✅ done · 🧪 needs test · 🚀 deploy/operator · 🚩 **decision-gate** (stop, decide with operator — not a build task).

> **Conventions for autonomous execution (added 2026-06-13):**
> - **Governance refs** in parentheses (e.g. §3, F3) point to the governance doc — each task traces
>   to a control it implements.
> - **Target paths** follow the **OB1 convention** (operator-confirmed): the new compose project
>   lives at **`agent-org/docker/docker-compose.yml`**, new service source under **`agent-org/agent-bridge/`**,
>   charters/skills under **`.claude/skills/`**. Paths are given per task as `→ path`.
> - **Done-when** = the machine-checkable acceptance criterion for the task (mirrors PLAN §7 tests).
> - **🚩 decision-gates** halt for an operator decision (e.g. P0.5); they are *not* buildable units.

---

## P0 — Platform spike

- ⬜ **P0.0** **Prerequisites (audit §0, updated 2026-06-13):** (a) ✅ **Local LiteLLM is LIVE** —
  the existing air-gapped `llm-gateway` (reach via `http://llama-cpp:8080`); agent-org consumes it,
  builds nothing on it. The **cloud** LiteLLM (`llm-gateway-cloud`) is a *separate* **Pc** add-on, only
  if P0.5 mandates. (b) ✅ `workspace-stacks.md` reconciled 2026-06-13 (portal/backups **and** the
  `llama-cpp`→`*-upstream` + `llm-gateway` flip); recovery scripts + CLAUDE.md already synced by
  the LiteLLM work. Baseline is accurate for the agent-org 3-place change.
  *Done-when:* both ✅ confirmed; nothing to build.
- ⬜ **P0.1** `agent-org` compose project scaffold: `mattermost` + `mattermost-db` (Postgres),
  attach external `ai-stack_llm-net`; internal net `ao-net`; `127.0.0.1` host ports.
  → `agent-org/docker/docker-compose.yml`. *Done-when:* `docker compose -f agent-org/docker/docker-compose.yml config` validates and both containers reach `healthy`.
- ⬜ **P0.2** Bring Mattermost up; create the org, a `#mgmt` channel, and one **bot account**
  (`@pm`) with an access token (token via env, not file).
  *Done-when:* an authenticated REST call as `@pm` posts to `#mgmt`.
- ⬜ **P0.3** `agent-bridge` skeleton: **Python (FastAPI + Pydantic + Instructor)**; persistent
  **WebSocket** client (consume events) + REST poster; `/health`; config from env; Postgres state.
  Lay out the §3.1.1 modules (event-gateway/governance-gate/scheduler/scope-ledger/router/model-router/audit-sink)
  even if most are stubs. → `agent-org/agent-bridge/`. *Done-when:* `/health` returns 200 and the WS stays connected.
- ⬜ **P0.3b** One **GBNF/JSON-schema constrained** structured call validated by Instructor on
  **`qwen36-27b`** **via the existing `llm-gateway`** (`http://llama-cpp:8080`) — prove constrained
  decoding holds through the gateway. Worker + local-judge profiles both bind `qwen36-27b` (same
  model, no swap thrash). **No model health-probes** (C5).
  *Done-when:* 20/20 constrained calls return schema-valid JSON parsed by Instructor with zero repair.
- ⬜ **P0.4** Echo test: bridge sees an @mention event and posts a reply in the same thread
  (proves post→event→bridge→post). (PLAN §7 P0)
  *Done-when:* an @mention of `@pm` produces a threaded bridge reply within the WS round-trip.
- 🚩 **P0.5** **Capability-floor test — DECISION-GATE (§8 #13, gates Pc):** measure **`qwen36-27b`** on
  (i) instruction/charter-following, (ii) structured-output reliability (with GBNF), (iii) coordination
  — using **bounded real completions, never health-probes** (C5). Decide the **binary** judge question
  (OD-10): is **27B-as-judge** good enough, or must the judge profile move to the **cloud lane**
  (OpenRouter via `llm-gateway-cloud`, off-GPU)? **Local 35B is not an option** (removed from the
  gateway; swap thrash). Also record the per-task "org vs. single agent?" guidance. Workers stay local.
  *Decision recorded → if "cloud judge needed", **Pc fires next**; else Pc is skipped and all lanes stay local.*

## Pc — Cloud lane (CONDITIONAL — runs immediately after P0 **only if P0.5 mandates a cloud judge**)

> **Why here, not P7 (audit fix 2026-06-13).** The PM/PO/reviewer run on the cloud lane (governance
> §1), so the **alignment core (P3/P4) depends on this infra**. If P0.5 says local judgment is too
> weak, build Pc **before P3**; if 27B-as-judge passes, **skip Pc entirely** and stay all-local.
> Mirrors PLAN §4 phase **Pc**. *(Tasks moved here from the old P7.2/P7.2b.)*

- ⬜ **Pc.1** Stand up a **separate `llm-gateway-cloud`** (+ its own spend DB) with **`master_key`
  + per-role virtual keys + budgets** (the cost-tier cap), OpenRouter models in its `model_list`;
  egress **only** via **`ao-egress`** (allowlist `openrouter.ai`; pin no-log/ZDR, prefer open-weight).
  **Leave the local `llm-gateway` air-gap untouched.** → `agent-org/docker/docker-compose.yml` (+ `agent-org/config/litellm-cloud.config.yaml`).
  *Done-when:* a budgeted OpenRouter call succeeds through `ao-egress`; a call to any non-allowlisted host is refused; local `llm-gateway` still has no egress. (C1=B / OD-6, §2.1, §8 #6/#13)
- ⬜ **Pc.2** Bridge builds + logs the **governance-summary-only** egress payload (claim/goal/
  deviation/options) — **no raw code/secrets** leave the box; flip judge/reviewer **profiles** (§5.4)
  `lane: cloud`. *Done-when:* an egress-payload audit entry exists for every cloud call and a redaction test proves no raw file content leaves.
- ⬜ **Pc.3** **Role/model profiles registry (C4, PLAN §5.4)**: a versioned profile registry the
  bridge reads — {lane, model, system_prompt_ref=charter, temperature, tool_access=scope, caller_key}.
  Adding a role = adding a profile; distinct **caller-keys per profile** (C7) for gateway analytics.
  → `agent-org/agent-bridge/profiles/`. *Done-when:* flipping a profile `lane` local↔cloud routes the next call to the other gateway with no code change.
- ⬜ **Pc.4** Plan to **join local+cloud analytics later, tagged by lane** (no live merge). *Done-when:* both spend DBs carry a `lane` tag on agent-org rows.
- 🚀 **Pc.0** Operator: confirm the OpenRouter spend ceiling (OD-10) before Pc.1.

## P1 — Wake mechanic

- ⬜ **P1.0** **Reliable event delivery (PLAN §3.1.1):** event-gateway dedupes on `event_id`
  (idempotent, at-least-once); on reconnect/restart **REST catch-up of posts since last-processed
  timestamp** per active channel, replayed through the idempotent path, then resume WS. An
  undeliverable wake past a bound is a §3 trigger, not a silent stall.
  *Done-when:* killing the bridge mid-conversation then restarting recovers the missed @mention exactly once (no double-wake, no lost wake).
- ⬜ **P1.1** Channel↔effort↔session map in the bridge (`#effort-x` thread → little-coder
  `--session <thread_id>`). (memory: little-coder-per-chat-sessions)
  *Done-when:* a thread id round-trips to the right `--session` and back.
- ⬜ **P1.2** `wake(worker, thread)` — resume a dormant `little-coder` session and deliver the
  thread context; worker reply posts back via the bridge.
  *Done-when:* a dormant session resumes and posts a reply in-thread.
- ⬜ **P1.3** A→B hand-off: worker A posts an @mention of B in an effort thread → B is woken →
  B replies in-thread (observable). (PLAN §7 P1) *Done-when:* the A→B→in-thread-reply loop completes end-to-end on one effort.
- ⬜ **P1.4** Bus-only enforcement: workers have no transport but the bridge→Mattermost path
  (no side-channels). (§5) *Done-when:* a worker network probe to anything but the bridge path is refused (egress allowlist).

## P2 — Escalation gate (CORE SAFETY — do not skip/defer)

- ⬜ **P2.1** **Governance-gate FSM (machine A, governance §3.0)** in the `governance-gate` module:
  per-effort state **{active ⇄ frozen}** — *this is the only safety FSM; the scheduler's
  {computing, waiting, suspended} (machine B, §3.6) is built separately at P5.0 and `frozen` is NOT
  one of its states.* **Persisted** so a frozen effort stays frozen across a bridge restart (fail-safe;
  default-deny on unknown state). (§3.0) *Done-when:* a frozen effort survives a bridge restart still frozen.
- ⬜ **P2.2** Triggers → freeze: refusal/objection, deviation, ambiguous scope, cross-effort
  conflict, irreversible/external action, unresolved disagreement, wake-storm cap. (§3 triggers)
  *Done-when:* each trigger, injected in test, transitions the effort `active → frozen`.
- ⬜ **P2.3** **CONCERN** message: bridge posts a structured CONCERN to `#mgmt` (what/why/
  options/recommendation) and freezes the effort **+ dependents**. (§3)
  *Done-when:* a CONCERN post matching the UX-FLOW §3 schema appears in `#mgmt` and dependents are frozen too.
- ⬜ **P2.4** **Operator-decision** parse: the Human Operator's reply → approve / modify-scope /
  abort → propagate → unfreeze; decision logged. (PO may clear steering; hard-gate → Human Operator.) (§3)
  *Done-when:* each of approve/modify/abort unfreezes correctly and writes an audit entry.
- ⬜ **P2.5** **Fail-safe default**: Human Operator unavailable → stays paused, **no auto-resume, no
  ask-another-agent, PO cannot self-clear a hard-gate**. Negative test: refusal cannot be re-routed to a second worker (F3).
  *Done-when:* a frozen hard-gate effort never auto-resumes and a reroute attempt is rejected.
- ⬜ **P2.6** **Global kill switch**: Human-Operator command freezes the entire fleet. (§3)
  *Done-when:* one operator command moves every effort to `frozen`.
- 🧪 **P2.7** Safety tests per PLAN §7 P2 (freeze holds, restart-keeps-frozen, no F3 reroute).

## P3 — Charters + grounding (rules-as-skills, floor/steering, goals)

- ⬜ **P3.1** Author **charters as skills** (PM/orchestrator, worker, reviewer) under
  `.claude/skills/` — hold whole-task view, escalation duties, bus-only, no self-clear. (§4)
  → `.claude/skills/agent-org-*/`. *Done-when:* a worker loads its charter skill and can quote its hard rules.
- ⬜ **P3.2** **Floor vs steering split**: hard rules = always-on non-overridable skill;
  steering layer = mutable per-session inject. (§4.2)
  *Done-when:* a steering edit reaches the worker on next turn; a floor edit does not without P3.4 approval.
- ⬜ **P3.3** **Hooks enforce the floor**: a PreToolUse-style hook blocks irreversible/external
  actions (push/deploy/delete/spend/send) without a cleared Human-Operator decision (hard-rule #4). (§4.2)
  *Done-when:* an uncleared push/deploy/delete is blocked by the hook (not merely warned).
- ⬜ **P3.4** Rule/goal **version store** in the bridge; a floor change requires Human-Operator approval + version
  bump + audit entry; workers report which version they run. (§4.2)
  *Done-when:* a floor change without operator approval is rejected; an approved one bumps the version + logs.
- ⬜ **P3.5** **Goal injection**: bridge delivers the worker's goal with **constraints inline**
  on spawn/wake; PM-owned canonical objective → scoped slices. (§4.3)
  *Done-when:* a woken worker's prompt contains its goal with constraints inline (not as a separate rule).
- ⬜ **P3.6** **Re-ground in flight**: PM/PO edit a worker's goal; change reaches the worker on
  next turn/wake; re-ground that invalidates in-progress work → §3 freeze-and-surface. (§4.3)
  *Done-when:* an in-flight goal edit reaches the worker next turn; an invalidating one freezes+surfaces.
- ⬜ **P3.7** **Cost-tiered continuous supervision** (gate = escalation arm, §3): cheap-continuous
  (hooks/bus-logging/caps) always on; expensive-continuous (LLM monitor on `JUDGE_MODEL`)
  sampled/triggered + full at checkpoints. Floor/steering coupling **skews symbolic** on weak
  models (asymmetric neurosymbolic coupling). (§3, §4.2)
  *Done-when:* cheap-continuous controls run on every action; the LLM monitor runs sampled/triggered, never per-token, never via health-probe.
- ⬜ **P3.8** **Readiness gate + clarify-loop (UX-FLOW Stage 2):** the planner judges *is this plan
  clear AND safe against this codebase?* On `false` (gaps / cascading-refactor risk) → generate
  clarifying questions (plan gaps + implementation-safety/blast-radius) → PO asks the **Human
  Operator** → iterate; plan stays `draft` until coherent. Judgment → cloud lane if Pc built.
  (governance F5; §4.3) *Done-when:* an under-specified request loops to operator questions instead of spawning a worker.
- ⬜ **P3.9** **Plan presentation + approval gate (UX-FLOW Stage 3):** PO presents a structured plan
  — **Feature Overview · Implementation Plan (stop-gates embedded) · Delegation DAG (sequence +
  bounded parallelism, *not* wide fan-out) · Estimate** — as the top-level stop-gate; **Human-Operator
  `approved` → Stage 4.** (§4.5 + §3) *Done-when:* no effort proceeds to execution without a recorded operator approval of the plan.

## P4 — Plan-stop-gates + review + self-report

- ⬜ **P4.0** **Ground + dry-run, RISK-GATED (UX-FLOW Stage 4; operator-confirmed policy):** before
  touching real code — (a) **ground assumptions** via `openbrain-research` (live); (b) **dry-run** in
  an **isolated throwaway branch/workspace** (little-coder per-instance containment + git-proxy make
  this naturally safe; **never merges**). **Policy = risk-gated (mirrors P4.5):** the dry-run is
  **mandatory for irreversible / cross-effort / cascading-refactor efforts** and **skipped for routine
  ones** (keeps the ~1–2-slot budget honest). Any issue here → escalate to PM (Stage 6).
  *Done-when:* a flagged high-blast-radius effort cannot reach real-code execution without a completed isolated dry-run; a routine effort skips it.
- ⬜ **P4.1** **Plan-doc checkpoints**: worker plan docs encode `⛔ STOP — review required`
  between phases; enforcement rules in a **separate floor doc** so the editable plan can't drop
  them. (§4.5) *Done-when:* deleting the stop marker from the editable plan does NOT remove the enforced halt.
- ⬜ **P4.2** Bridge **blocks past a checkpoint** until review is cleared. (§4.5)
  *Done-when:* a worker cannot take an action past a checkpoint until a review verdict is recorded.
- ⬜ **P4.3** **Explain-intent**: at each stop the worker emits a structured explanation
  (intent / goal-as-understood / tradeoffs hit / what I'd flag). (§4.5, §4.3)
  *Done-when:* each checkpoint produces the 4-field explanation artifact.
- ⬜ **P4.3b** **Verify, don't trust, the explanation**: bridge / judgment-model reviewer
  cross-checks the explanation against the actual diff/actions (audit trail). Small models
  confabulate — words are a lead, actions are ground truth. (§4.5)
  *Done-when:* an explanation that contradicts the actual diff is flagged as a mismatch.
- ⬜ **P4.4** **Differently-goaled reviewer**: bridge spawns a reviewer with an ethics/
  whole-picture goal (inline lens "find where this trades safety/scope for the metric");
  reviewer **reports to PM, cannot self-approve**. Config rejects a same-goal reviewer. (§4.4)
  *Done-when:* a same-goal reviewer config is rejected; reviewer output routes to PM, never auto-merges.
- ⬜ **P4.5** **Risk-gated review depth**: routine → 1 reviewer/none; irreversible/cross-effort
  → multi-lens panel (correctness/security/scope/ethics). Don't let review become a wake-storm. (§4.4, §8 #9)
  *Done-when:* an irreversible deliverable triggers the multi-lens panel; a routine one does not.
- ⬜ **P4.6** PM aggregates review → re-grounds worker (§4.3) → worker refactors → continue.
  *Done-when:* a review-flagged drift produces a re-grounded goal and a worker refactor before resume.
- ⬜ **P4.7** **Reviewers run on `JUDGE_MODEL`** (local-first → OpenRouter if P0 mandates);
  small-model review paired with deterministic checks (tests/lints/scope-diff). (§4.4)
  *Done-when:* reviewer calls bind the `JUDGE_MODEL` profile and run alongside the deterministic checks.
- ⬜ **P4.8** **Lateral concern channel**: a worker can raise a cross-domain concern to a peer/
  reviewer, but it surfaces on the bus and routes to the PM (never private resolution, never peer
  merge-authority). Mark this channel exempt from rate/flow caps. (§4.4, §5)
  *Done-when:* a lateral concern appears on the bus + routes to PM, and is NOT subject to the wake-storm cap.

## P5 — Dynamic roles + worker pool + scope ledger + provenance routing

- ⬜ **P5.0** **Worker pool + concurrency scheduler — the scheduler FSM (machine B, governance §3.0;
  PLAN §3.6, the real lift)**: implements **{computing, waiting, suspended}** (NOT `frozen` — that's
  the governance gate, machine A). Stand up N `(little-coder + open-terminal)` instance pairs; bridge
  instance registry; `assign_effort` acquires an instance under a **`MAX_CONCURRENT_WORKERS` semaphore**;
  a `waiting` agent releases its slot and wakes on a dependency's `finish`. ⚠️ **`/slots` is dead on
  llama-swap (404, C6)** — there is **no live GPU-occupancy signal**, so use a **static,
  conservatively-sized semaphore** (interactive reserve held by *config*, not by probing), never a
  model health-probe. Default backend **3 parallel @ ~83k** (burst 4 @ 64k; never 32k); fleet cap =
  slots − interactive reserve (~1–2 workers). Queue efforts when no instance is free. (OD-8)
- ⬜ **P5.1** **Scope ledger**: who's authorized for what path/domain; requests logged; deny
  self-granted scope (hard-rule #2). (§5, §4.1)
  *Done-when:* a worker's self-grant attempt is denied + logged; a PM grant is recorded.
- ⬜ **P5.2** **Role authority split**: PM may spin up another **instance** of an approved role;
  a **new role type** is Human-Operator-gated (PO proposes) via the §3 gate (charter + scope). (§4.1)
  *Done-when:* PM instantiating an approved role succeeds; a new role *type* routes through the §3 gate.
- ⬜ **P5.3** Optional **approved-role catalog** (auth/DB/frontend/infra pre-cleared); only
  novel domains escalate. (§8 #7)
  *Done-when:* a catalog role spawns without escalation; a novel domain escalates.
- ⬜ **P5.4** **Last-owner provenance**: resolve "who last touched this area" — git-blame/last
  commit (v1) → ownership ledger (v1.5). Drives the A→B hand-off target. (PLAN OD-4)
  *Done-when:* a file path resolves to its last owner and the hand-off @mentions that worker.
- ⬜ **P5.5** Channel taxonomy: `#mgmt`, `#effort-*`, `#incidents`, `#suggestions`, DMs.
  *Done-when:* the bridge creates an `#effort-<name>` channel on new-effort assignment.
- ⬜ **P5.6** **Wake-storm rate cap** per effort/window; exceeding → §3 trigger. **Applies to
  work chatter only — the brake/objection channel is exempt (sacred).** (§5)
  *Done-when:* exceeding the cap on work chatter freezes the effort; objections are never capped.
- ⬜ **P5.7** **Stream-aligned, right-sized scoping**: bias workers toward end-to-end slices with
  constraints inline (§4.3), sized to the local model's coherent window; cognitive-load heuristic
  triggers a (reluctant) split. (§4.1, §8 #12)
  *Done-when:* an effort exceeding the cognitive-load heuristic is flagged for split.
- ⬜ **P5.8** **Retirement/decommission**: revoke scope from the ledger, retire role from the
  catalog, expire stale goals/rules, archive effort artifacts — all logged; PM vs PO vs Human-Operator authority per
  step. (§4.1 lifecycle, §8 #14)
  *Done-when:* retiring a worker revokes its ledger scope and leaves no zombie authority; the action is logged.

## P6 — Audit trail + learning loop (propose-not-dispose)

- ⬜ **P6.1** **Full event log**: every wake, hand-off, CONCERN, decision, goal/rule change,
  review verdict — persisted with versions. (§5)
  *Done-when:* replaying the log reconstructs who-woke-whom and every gate decision with versions.
- ⬜ **P6.2** **Mirror to Open Brain**: critical hand-offs + decisions captured via
  `capture_thought` for durable, queryable provenance. (§5; memory: openbrain cloud gateway)
  *Done-when:* a CONCERN+decision pair is queryable in Open Brain after the fact.
- ⬜ **P6.3** **Suggestion pool**: `#suggestions` channel → bridge collects worker suggestions
  for consideration. (§6) *Done-when:* a worker suggestion lands in the pool and is retrievable by the PM.
- ⬜ **P6.4** **Pattern surfacing**: detect recurring failure/suggestion patterns across efforts
  (manual PM synthesis v1; assisted later) — route via Open Brain + claudeception. (§6)
  *Done-when:* a pattern recurring across ≥2 efforts is surfaced as a candidate for hardening.
- ⬜ **P6.5** **Propose-not-dispose flow**: pattern → PM synthesizes a *proposed* change → **the Human
  Operator approves** → lands via versioned floor/steering update (P3.4). **No auto-apply.** (§6)
  *Done-when:* a proposed rule change cannot apply without operator approval (no auto-apply path exists).

## 3-place change (per new container)

- ⬜ **R.1** `agent-org` compose (`name: agent-org`) — `mattermost`, `mattermost-db`,
  `agent-bridge`, the **pooled `little-coder`/`open-terminal` worker instances** + a shared
  git-allowlist egress; `ao-net` (internal) + external **`ai-stack_llm-net`**; host ports on
  `127.0.0.1`; depends_on; restart. **The local `llm-gateway` is NOT added here** (already live,
  reached via the `llama-cpp` alias). The **cloud `llm-gateway-cloud` + `ao-egress` are added only
  in phase Pc** (conditional, right after P0), if a cloud judge is mandated. (PLAN §3.2, §3.6, §3.7)
  *Done-when:* `docker compose -f agent-org/docker/docker-compose.yml config` validates with all v1 services.
- ⬜ **R.2** `scripts/emergency-recovery.ps1` + `.bat` — add all the above to the inventory
  and startup/shutdown sequences (after `llama-cpp` healthy on start; before main stack on stop).
  *Done-when:* a recovery dry-run lists every agent-org container in the correct order.
- ⬜ **R.3** `.claude/skills/stack-map/references/workspace-stacks.md` — add the new project +
  service rows (networks, ports, dependency order). Run `/stack-map` to confirm no drift.
  *Done-when:* `/stack-map` reports no drift between compose, recovery scripts, and the reference doc.

## P7 — Mobile + hardening (author here; exposure = operator)

- ⬜ **P7.1** Human-Operator mobile flow: install Mattermost app, the Human Operator = system admin (join any channel/DM),
  decide CONCERNs and trigger kill switch from phone. (§1, §3)
  *Done-when:* the operator approves a CONCERN and triggers the kill switch from the phone app.
- ↪️ **P7.2 → moved to phase Pc** (cloud LiteLLM) and **Pc.3** (role/model profiles registry). They
  were relocated out of P7 because the alignment core (P3/P4) depends on them when P0.5 mandates a
  cloud judge — see the **Pc** section above. *(Left as a pointer so old links resolve.)*
- ⬜ **P7.3** CONCERN UX: optional Mattermost plugin for rich CONCERN/decision cards (else
  structured plain posts). (PLAN OD-5) *Done-when:* a CONCERN renders as a distinct card/post the operator can act on.
- 🚀 **P7.4** Operator: tailnet exposure (reuse `tailscale`); no public exposure; confirm no
  E2EE on agent channels (observability = safety). (§5)
  *Done-when:* Mattermost is reachable on the tailnet only (not public) and agent channels are non-E2EE.

---

## Sequencing

Build **P0 → P1 → P2** first — prove the loop *and* that we can stop it (P2 is the safety
spine; nothing scales before it). **Pc (conditional)** fires **right after P0** *iff* the P0.5
decision-gate mandates a cloud judge — so the cloud lane exists before the alignment core needs it
(else Pc is skipped). Then **P3 → P4** (the alignment core: charters, floor/hooks, goal-grounding,
readiness-gate, plan-approval, ground+dry-run, stop-gates, review). Then **P5 → P6** (scale + the
temporal learning loop). **P7** last (mobile + exposure + CONCERN-card UX), operator-deployed.

```
P0 ──┬──(P0.5: cloud judge needed?)──▶ Pc (cloud lane) ──┐
     └──(no)─────────────────────────────────────────────┴──▶ P1 ▶ P2 ▶ P3 ▶ P4 ▶ P5 ▶ P6 ▶ P7
```

**Hard gate:** do not begin **P5** (more roles, fan-out) until **P2** (stop) and **P3** (floor)
pass their safety tests — the paper says more roles = more misalignment, so the brakes must
exist before the fleet grows.
