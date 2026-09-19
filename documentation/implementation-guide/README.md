# implementation-guide — index

> Status: LIVE · created 2026-08-20 (docs truth pass). One row per feature.
> Rule: when a plan ships or is superseded, update this table **and** banner
> the old doc in the same PR. Completed plan/task checklists move to
> `../archive/implementation-guide/`.
>
> **This index spans two repos.** A row is the feature's status wherever its plan
> lives. NEW plans, build logs and plan sets are written in the private plan store
> `documentation-plans-ai-stack` (`implementation-guide/<feature>/`), committed and
> pushed there, and get a row here naming that location - see CLAUDE.md, "Plans live
> in the plan store". **Phase 2 ran 2026-09-18**: the feature directories that
> were still here moved to the store, so every row below is marked **@ plan store**
> except the two that stay here for a MECHANICAL reason - `multi-agent-concurrency/`
> (MERGE-PROTOCOL.md must travel with every worktree) and `dark-factory-unification/`
> (`scripts/checks/dfu-done.ps1` reads its PLAN/DECISIONS/WALKTHROUGH in CI). A third
> directory appearing here is drift.
> `scripts/checks/check-doc-placement.ps1` blocks a new plan staged into this repo
> and, with `-All`, audits what is sitting here untracked.

| Folder | State | Notes |
|---|---|---|
| `LiteLLM-Proxy/` **@ plan store** | ✅ shipped | `guide-LiteLLM-Proxy.md` = source of truth; B2 queue design lives here too. Executed plan/tasks archived. |
| `Systems-of-structured-data/` **@ plan store** | ✅ shipped | Phases 0–5 live (see `PHASE6-WIRING-HANDOFF.md`). |
| `update-owui-to-0-11-0/` **@ plan store** | ✅ executed 2026-08-20 | The reference OWUI upgrade procedure. |
| `qwen3.8-model-swap/` **@ plan store** | ✅ deployed 2026-08-16 | Reference model-swap procedure. |
| `digest-gap-deep-research/` **@ plan store** | ✅ deployed 2026-08-05 | |
| `podcast-on-demand-audio/` **@ plan store** | ✅ shipped 2026-08-02 | |
| `disk-prune-watcher/` **@ plan store** | ✅ shipped | Realized as `scripts/sysadmin-mcp/`. |
| `claude-code-mattermost-bridge/` **@ plan store** | ✅ shipped | Code: `scripts/mattermost-mcp/` + `scripts/claude-sessions-bridge/`. |
| `web-search/` **@ plan store** | ✅ shipped | `search-gateway/README.md` is the living doc. |
| `open-source authentication front ends for ai stack/` **@ plan store** | ✅ shipped | Portal live; posture + post-audit kept, plan/tasks archived. |
| `expand-quartz-4/` **@ plan store** | ✅ shipped | Plan + outcomes + promotion runbook kept. |
| `open-notebook-integration-openbrain/` **@ plan store** | ✅ shipped (IKS) | Ledger + sync/pending plans kept. its `iks-dev` overlay was torn down + archived 2026-08-20 (idle since 08-01; volumes kept; tree at `scripts/archive/iks-dev/`). |
| `little-coder/` **@ plan store** | ✅ shipped | Design + workflow guide + UPDATE-NOTES kept. |
| `teams-chat-agent-orchestration/` **@ plan store** | ✅ built as `agent-org/` | Governing specs kept (SAFETY, COMMS-MODEL, PLAN, …); tasks/outline/analyses archived. |
| `research-engine-for-OB/` **@ plan store** | ✅ deployed; `PLAN-research-trust-2026-09-11.md` **merged, awaiting deploy** | `GROUNDING-MODEL.md` is the governing spec; `REPO-SOURCES-WIRING.md` design not built. research-trust adds: a collapsed search is a search failure not an absent topic (`search-quality.ts`), KB-recall pages face the relevance gate, `no_relevant_sources` skips the curator, numeric grounding (`grounding.ts`), `needs answered X of N` in place of `coverage NN%`, a curator meta-claim filter, and a remeasured SearXNG engine policy on a pinned image. Evidence: `documentation/notes/research-audit-optiplex-100hz-2026-09-11.md`, `search-engine-alternatives-2026-09-11.md`, `research-trust-findings.md`; deploy + retraction steps in `documentation/evidence/research-trust/TEST-PLAN.md`. |
| `expand-OB1-research-inlet-service/` **@ plan store** | 🟡 built, not live | Activation reference. |
| `autonomous-project-lifecycle/` **@ plan store** | 🟡 in build | D1/D4 human-gated merge live; D5 staging open. |
| `agent-memory-plane/` **@ plan store** | 🟡 P0-P3 built, recall OFF | Phases 0-3 of the canonical plan (which lives in the `documentation-plans-ai-stack` private repo). Schema + 7 MCP tools + 3 REST twins live in `openbrain-mcp`; write paths on (`AO_MEMORY_WRITEBACK_ENABLED=true`); recall's live acceptance MET 2026-08-30 (`scripts/checks/smoke-agent-memory-live.ps1`) but `AO_MEMORY_RECALL_ENABLED` stays **off** until the similarity floor is calibrated against a corpus bigger than 4 rows (`documentation/notes/agent-memory-recall-threshold.md`). `PROMOTION-RUNBOOK.md` + the Phase-3 gate table moved to the plan store on 2026-09-18; the Phase-1 rows in that table are STALE and say so. The gate table was this repo's `PLAN.md` and is now `VALIDATION-RECORD.md` in the store - renamed on the move because the store already holds the canonical `PLAN.md` and that file's own first line says it is not a plan. P4 not started. |
| `idea-refinery/` **@ plan store** | ✅ built (local) | OpenRouter cloud route PARKED → archived. |
| `wiki-dynamic-index/` **@ plan store**| 🟡 P0-P4 shipped 2026-08-26; A-E planned | ContentIndex OFF, wiki_pages feeds search/nav/graph; new note 900s->29s. `PLAN.md` v2 = shipped work, `BUILD-LOG.md` = results + traps, `PLAN-NO-REBUILD.md` = remaining phases A-E (DB-rendered pages, live nav/graph, search UI), `PLAN-VIEWER-PERF.md` = 2026-08-28 plan (not built) for the 2-4s-per-click / unresponsive-on-mobile symptom: per-nav whole-vault explorer rebuild + nav-cache stall. |
| `research-workbench/` **@ plan store** → lives in the `documentation-plans-ai-stack` private repo (written there 2026-09-18) | 📝 plan set v2 drafted 2026-09-18, awaiting operator review; nothing built, no anchor proposed | Panel workspace + live read path + typed retrieval contract + trust surfaces + branching sessions + export + Kokoro podcasts + ON retirement. `01-OPEN-ITEMS` (D1–D16) · `02-FRAMEWORKS` (verified) · `03-PLAN` v2 (P-1, P0, P1a/b/c, P2–P8) · `04-AUDIT` (32 findings; the "retire the builder" premise was REFUTED — the live renderer must be built to Quartz-chrome parity first) · `05-SWARM` + `anchors/` (19 drafts). Findings and the two libraries it grew from stay here: `notes/research-ux-borrow-library-2026-09-15.md`, `notes/research-panel-library-2026-09-18.md`, `notes/nodus-vs-openbrain-research-comparison-2026-09-14.md`. |
| `supervised-research-pipeline/` **@ plan store** | 📝 draft 2026-08-05 | 4 phases, no build. |
| `research-source-admission/` **@ plan store** | 📝 shelved 2026-08-20 | |
| `ai-stack-control-tower/` **@ plan store** | 📝 draft, not built | |
| `ai-stack-user-created-automations/` **@ plan store** | 📝 design (n8n), not built | |
| `vllm-inference-exploration/` **@ plan store** | 📝 draft, nothing built | |
| `quartz-production-build-migration/` **@ plan store** | 📝 plan, not started | Genuinely outstanding (viewer still dev-serve). |
| `deploy-gate-and-curator-recovery/` **@ plan store** | 📝 draft 2026-09-06 | Incident plan (openbrain-curator crash loop, gate 5d); Phase A/B in flight via harness items `gate5d`, `curatorimg`, `passplan`. |
| `wsl-resource-governance/` **@ plan store** | 📝 drafted, not applied | Pairs with `C:\Users\yamao\.wslconfig` header. |
| `reaching-level-4-autonomy/` **@ plan store** | 💡 ideas only | Not committed scope. |
| `Jupyter/` **@ plan store** | 💡 captured, not built | |
| `autonomous-updates-with-security/` **@ plan store** | ⚠️ unverified | No completion markers; both real OWUI upgrades ran manually. Folded into Watchtower decision D-2. |
| `portable-research-service/` **@ plan store** | 📦 evergreen | Deliberately workspace-agnostic extractions. |
| `multi-agent-concurrency/` **stays here** | BUILT + LIVE 2026-08-28 | Worktree tooling, plane leases, the develop/test/review pipeline, the anchor gate. Kept in this repo because `MERGE-PROTOCOL.md` must travel with every worktree an agent provisions. |
| `dark-factory-unification/` **stays here** | PLANNED 2026-08-29, partly executed | One org, pluggable substrates, one memory plane. Kept here because `scripts/checks/dfu-done.ps1` and `verify-dfu-done.ps1` READ its `PLAN.md`/`DECISIONS.md`/`WALKTHROUGH.md` and CI runs them against a checkout of this repo alone. `DECISIONS.md` is also the `source_of_record` in `scripts/checks/defect-classes.json`. |
| `stack-layers/` **@ plan store** | PLAN 2026-09-19, wave 1 in progress | The stack as three layers (foundations / engines / surfaces) behind one `stack.manifest.toml` and one stdlib `stack.py` driver, so a fresh clone starts Open WebUI alone and `enable <product>` turns the rest on. Supersedes CLEANUP-PLAN v3 Part L (colocation, per-plane `.env`), D.1 x-anchors, D-12 (inventory generator) and I.4/J.6 (conventions page + port registry). Evidence: `documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md`. |
| `cluster-transition/` **@ plan store** | PLAN 2026-09-17, not built | ai-stack onto three OptiPlex nodes; inference stays on the GPU box. Supersedes the placement half of the portal plan. |
| `portal-authentik-traefik/` **@ plan store** | PLAN 2026-09-16, not built | Portal refactor Authelia+Caddy to Authentik+Traefik; as of 2026-09-17 it lands on cluster node 1, so only its Phase 3 and tailnet lane change. |
| `validated-work-memory/` **@ plan store** | PLANNED 2026-09-11, nothing implemented | Receipt to lesson to skill, with re-validation. |
| `source-admission-adversarial-gate/` **@ plan store** | DRAFT v3 2026-08-26, nothing built | Content-screen PDP + gateway acquisition chokepoint + admission gate across every intake lane. |
