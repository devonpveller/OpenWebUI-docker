# ai-stack User-Created Automations

A **concept design** for a node-based automation builder — a web front-end on the
**tailnet** and **cloudflared** — that chains together the ai-stack's *existing*
services (research, podcast, Open Brain, search, memory, LLM, …) into
user-editable automations. Inspired by Open WebUI's *Automations*, but pointed at
this stack's own capabilities instead of OWUI primitives.

> Status: **CONCEPT / DESIGN — not built.** Idea-stage, with a feasibility verdict.

## Read order

1. [CONCEPT-ai-stack-user-created-automations.md](CONCEPT-ai-stack-user-created-automations.md)
   — the full concept: vocabulary (node / format node / automation / run),
   architecture, node catalogue grounded in live services, the flagship
   Research-fan-out example, tailnet+cloudflared exposure, risks, and the
   **feasibility verdict** (§10).
2. [PLAN-integration-automations.md](PLAN-integration-automations.md) — the v1
   integration plan, grounded in live ports/endpoints. **Supersedes the concept's
   bespoke `automations-engine`: v1 adopts n8n as the engine+UI.** Covers the
   cross-project network seam, exact Research/ON/Podcast contracts, the OWUI-sink
   gap, tailnet serve wiring, and compose/recovery discipline.
3. [TASKS-integration-automations.md](TASKS-integration-automations.md) — the
   phased build checklist (P0 tailnet spike → P1 fan-out → P2 deferred), each task
   with a done-when.

### Locked decisions (2026-06-13)
Fold into **main stack** · **single-user, tailnet-first** (no cloudflared in v1) ·
adopt **n8n** (recommended: run full n8n + nodes, *not* fork the editor — see
PLAN §2) · v1 scope = **Research fan-out only**.

## One-line verdict

**HIGH feasibility, MEDIUM build effort.** The hard parts — long-running async
jobs, service-to-service chaining, dual-plane exposure with auth — already run in
production (the digest→podcast chain, research jobs, the Portal, tailscale serve).
The work is an engine + UI + a few thin adapters over surfaces that are already
live. Start with a tailnet-only **Research → Format→OWUI** spike (§10 phasing).

## Relationship to other designs in this repo

- Builds on the de-facto first automation: the daily-digest → podcast chain
  (`../../expanding-daily-digest-with-auto-podcast/`, `OB1/recipes/daily-digest/`).
- Reuses the new-compose-project + external-network pattern and the stop-gate
  governance model from
  [`../teams-chat-agent-orchestration/`](../teams-chat-agent-orchestration/).
- Depends on the Research async contract documented in
  [`../research-engine-for-OB/`](../research-engine-for-OB/).
