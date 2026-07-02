# Teams-Chat Agent Orchestration — index & read-order

**What this is:** the design corpus for a self-hosted, mobile-accessible **Microsoft-Teams-style
chat platform that doubles as the coordination fabric for a governed fleet of coding agents**
(Human Operator → PO → PM → little-coder workers). It is grounded in the empirical paper
*"AI Organizations are More Effective but Less Aligned than Individual Agents"* (arXiv:2604.10290)
and three supporting framework analyses.

**Status:** 🛠️ **v1 BUILT (2026-07-01); comms model CM.1–CM.6 + P4.0 ground/dry-run BUILT
(2026-07-02).** The design below is implemented as the **`agent-org` compose project** at
[`agent-org/`](../../../agent-org/) — the `agent-bridge` service (all §3.1.1 modules incl. the P2
gate, the deterministic [comms router](COMMS-MODEL-deterministic-routing.md): channel = project,
effort = thread, and the P4.0 risk-gated dry-run gate + grounding client),
charters/floor/hooks/profiles, the compose project, and **93 passing tests**. See
[`agent-org/IMPLEMENTATION-NOTES.md`](../../../agent-org/IMPLEMENTATION-NOTES.md) for the
task-by-task build record + what remains **operator-gated** (Mattermost bot token, the P0.5
capability-floor decision, the conditional cloud lane, worker-pool bring-up, tailnet exposure).
Design baselined against the live workspace **2026-06-13**; the build's 3-place change
reconciled the stack-map + recovery scripts **2026-07-01**.

---

## Start here — canonical read order

1. **[SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md)** — 📐 **the
   governing spec.** Roles, the paper's failure modes → controls, the escalation gate (incl. the
   formal **§3.0 two-FSM model**), charters, goal-grounding, the learning loop. **Read this first.**
2. **[UX-FLOW.md](UX-FLOW.md)** — the user journey (intake → readiness-gate → plan → ground/dry-run
   → execute → escalate), the intent thread, the CONCERN schema, the idle-wait DAG.
3. **[COMMS-MODEL-deterministic-routing.md](COMMS-MODEL-deterministic-routing.md)** — 📐 **spec
   refinement (2026-07-02):** the deterministic *audience × intent → destination* routing table, the
   flow rules (ladder, decide-private/record-public, bring-back-down), and the taxonomy
   **channel = project, effort = thread** — which **supersedes PLAN §5.2's per-effort channels** and
   refines governance §7. Includes its own implementation plan (CM.1–CM.6).
4. **[PLAN-teams-chat-agent-orchestration.md](PLAN-teams-chat-agent-orchestration.md)** — *how we
   build it*: workspace baseline (§0), architecture, the `agent-bridge` module map (§3.1.1), phases
   **P0 → Pc → P7**, component contracts, open decisions. *(Channel taxonomy in §5.2 is superseded by
   the COMMS-MODEL doc.)*
5. **[TASKS-teams-chat-agent-orchestration.md](TASKS-teams-chat-agent-orchestration.md)** — the
   executable checklist (paths + done-when per task; 🚩 = decision-gate).
6. **[TOOLING-selection.md](TOOLING-selection.md)** — what to reuse vs. build (most worker substrate
   already exists in this workspace).
7. **Background / rationale of record** (read when you need the *why*):
   - [OUTLINE-teams-chat-agent-orchestration.md](OUTLINE-teams-chat-agent-orchestration.md) —
     platform comparison (Mattermost vs Matrix vs Zulip). *Governance content here is superseded.*
   - [ANALYSIS-team-topologies-alignment.md](ANALYSIS-team-topologies-alignment.md) — TT × the paper.
   - [ANALYSIS-scholarly-ai-org-frameworks.md](ANALYSIS-scholarly-ai-org-frameworks.md) — governance
     literature × the paper.
   - [ANALYSIS-frontier-vs-small-model-approach.md](ANALYSIS-frontier-vs-small-model-approach.md) —
     why our small-local-model stack inverts the literature's assumptions.
   - [Ai-Organizations-...md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md)
     — full source paper.
   - [research/](research/) — raw research-engine outputs the analyses build on.

> **Precedence rule:** **governance spec > PLAN > TASKS.** Where any doc disagrees with the
> governance model, the governance model wins and the other doc gets corrected. UX-FLOW is the
> *journey*, not the spec; the ANALYSIS/OUTLINE docs are *rationale*, not the spec.

---

## Build-phase map

```
P0  Platform spike + capability-floor test (P0.5 🚩 decision-gate)
 │
 ├─(P0.5: local 27B judge too weak?)──▶ Pc  Cloud lane (conditional: llm-gateway-cloud + ao-egress)
 │                                          └── skipped entirely if 27B judgment passes
 ▼
P1  Wake mechanic (incl. reliable event delivery)
P2  Escalation gate ── CORE SAFETY (the spine; nothing scales before it)
P3  Charters + grounding + readiness-gate + plan-approval
P4  Plan-stop-gates + review + ground/dry-run (risk-gated)
P5  Dynamic roles + worker pool + scheduler FSM + scope ledger      ◀── HARD GATE: needs P2+P3 green
P6  Audit trail + learning loop (propose-not-dispose)
P7  Mobile + hardening (operator-deployed)
```

---

## Single-source-of-truth registry (avoid re-duplicating these)

| Concept | **Canonical home** | Referenced (not re-specified) by |
|---------|--------------------|----------------------------------|
| Roles / escalation ladder | governance §1 | UX-FLOW §1/§4, PLAN §1, OUTLINE (banner) |
| **Governance gate FSM (machine A)** {active⇄frozen} | **governance §3.0** | PLAN §3.6, TASKS P2.1, UX-FLOW §5 |
| **Scheduler / idle-wait FSM (machine B)** {computing,waiting,suspended} | **PLAN §3.6** | governance §3.0(B), UX-FLOW §5, TASKS P5.0 |
| Model lanes (local air-gapped / cloud) | PLAN §3.4 | governance §1/§2.1, UX-FLOW §1, TOOLING §3.3 |
| **Channel/comms taxonomy + routing** | **COMMS-MODEL §2/§4** | PLAN §5.2 (**superseded**), governance §7 (refined), OUTLINE §4 |
| CONCERN schema (intent-framed) | UX-FLOW §3 | governance §3, COMMS-MODEL §2 (routing) |
| Role = model profile | PLAN §5.4 | TASKS Pc.3, governance §8 #6 |
| `agent-bridge` internal modules (SRP) | PLAN §3.1.1 | PLAN §5.1, TASKS P0.3 |

---

## Recommendation traceability — analyses (a)–(r) → where folded

The three ANALYSIS docs proposed changes (a)–(r). All are now reflected in the spec/PLAN/TASKS
(2026-06-13 audit pass). Landing sites:

| # | Recommendation | Landed in | Status |
|---|----------------|-----------|--------|
| **(a)** | Stream-aligned-first (fewer end-to-end workers) | governance §4.1, §4.3 | ✅ |
| **(b)** | TT interaction-mode vocabulary in handoff contracts | governance §4.1 (handoff = constraint contract) | ◑ partial (mode-naming not adopted verbatim) |
| **(c)** | `agent-bridge` = Platform / Thinnest Viable Platform | PLAN §3.1.1, §3.5 | ✅ |
| **(d)** | Brake/objection channel exempt from flow-minimization | governance §5, §4.4; TASKS P4.8, P5.6 | ✅ |
| **(e)** | Roles ↔ TT mapping table | ANALYSIS-TT §6 (kept in the analysis) | ◑ optional (not copied into governance §1) |
| **(f)** | Per-worker cognitive-load budget (open decision) | governance §8 #12; TASKS P5.7 | ✅ |
| **(g)** | Continuous supervision primary; gate = its escalation arm | governance §3 (framing), §9 | ✅ |
| **(h)** | Cost-tier supervision (cheap-continuous / expensive-sampled) | governance §3; TASKS P3.7 | ✅ |
| **(i)** | First-class lateral concern channel | governance §4.4, §5; TASKS P4.8 | ✅ |
| **(j)** | Lifecycle / retirement phase | governance §4.1, §8 #14; TASKS P5.8 | ✅ |
| **(k)** | "Asymmetric neurosymbolic coupling" framing for floor/steering | governance §4.2 | ✅ |
| **(l)** | Mediated/context-oriented comms stated as a chosen position | governance §5 | ✅ |
| **(m)** | Tiered model assignment as a stated decision | PLAN §3.4; governance §2.1 | ✅ |
| **(n)** | "Right-size to the model" cognitive-load rule | governance §4.1, §4.3 | ✅ |
| **(o)** | Verify self-report against actual actions | governance §4.5; TASKS P4.3b | ✅ |
| **(p)** | Coordination lives in the deterministic bridge | PLAN §3.5; governance §5 | ✅ |
| **(q)** | Capability-floor test (new open decision) | governance §8 #13; PLAN/TASKS **P0.5** | ✅ |
| **(r)** | Privacy/cost boundary for the cloud judgment layer | PLAN §3.4/OD-6; governance §8 #13(c) | ✅ |

> ◑ items are deliberate non-folds (kept as rationale in the analysis), not omissions.

---

## Conventions (for any contributor or autonomous executor)

- **Paths** follow the OB1 convention: the new compose project lives at
  `agent-org/docker/docker-compose.yml`; service source under `agent-org/agent-bridge/`.
- **3-place change** for every new container: compose **+** `scripts/emergency-recovery.ps1`/`.bat`
  **+** `.claude/skills/stack-map/references/workspace-stacks.md` (run `/stack-map`).
- **G1:** never commit/push or merge to `main` without an explicit ask.
- **No secrets in files** — tokens/keys via env only.
