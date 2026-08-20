# implementation-guide — index

> Status: LIVE · created 2026-08-20 (docs truth pass). One row per folder.
> Rule: when a plan ships or is superseded, update this table **and** banner
> the old doc in the same PR. Completed plan/task checklists move to
> `../archive/implementation-guide/`.

| Folder | State | Notes |
|---|---|---|
| `LiteLLM-Proxy/` | ✅ shipped | `guide-LiteLLM-Proxy.md` = source of truth; B2 queue design lives here too. Executed plan/tasks archived. |
| `Systems-of-structured-data/` | ✅ shipped | Phases 0–5 live (see `PHASE6-WIRING-HANDOFF.md`). |
| `update-owui-to-0-11-0/` | ✅ executed 2026-08-20 | The reference OWUI upgrade procedure. |
| `qwen3.8-model-swap/` | ✅ deployed 2026-08-16 | Reference model-swap procedure. |
| `digest-gap-deep-research/` | ✅ deployed 2026-08-05 | |
| `podcast-on-demand-audio/` | ✅ shipped 2026-08-02 | |
| `disk-prune-watcher/` | ✅ shipped | Realized as `scripts/sysadmin-mcp/`. |
| `claude-code-mattermost-bridge/` | ✅ shipped | Code: `scripts/mattermost-mcp/` + `scripts/claude-sessions-bridge/`. |
| `web-search/` | ✅ shipped | `search-gateway/README.md` is the living doc. |
| `open-source authentication front ends for ai stack/` | ✅ shipped | Portal live; posture + post-audit kept, plan/tasks archived. |
| `expand-quartz-4/` | ✅ shipped | Plan + outcomes + promotion runbook kept. |
| `open -notebook-integration-openbrain/` | ✅ shipped (IKS) | Ledger + sync/pending plans kept. `iks-dev/` = dev overlay, still running (decision #11 pending). |
| `little-coder/` | ✅ shipped | Design + workflow guide + UPDATE-NOTES kept. |
| `teams-chat-agent-orchestration/` | ✅ built as `agent-org/` | Governing specs kept (SAFETY, COMMS-MODEL, PLAN, …); tasks/outline/analyses archived. |
| `research-engine-for-OB/` | ✅ deployed | `GROUNDING-MODEL.md` is the governing spec; `REPO-SOURCES-WIRING.md` design not built. |
| `expand-OB1-research-inlet-service/` | 🟡 built, not live | Activation reference. |
| `autonomous-project-lifecycle/` | 🟡 in build | D1/D4 human-gated merge live; D5 staging open. |
| `idea-refinery/` | ✅ built (local) | OpenRouter cloud route PARKED → archived. |
| `supervised-research-pipeline/` | 📝 draft 2026-08-05 | 4 phases, no build. |
| `research-source-admission/` | 📝 shelved 2026-08-20 | |
| `ai-stack-control-tower/` | 📝 draft, not built | |
| `ai-stack-user-created-automations/` | 📝 design (n8n), not built | |
| `vllm-inference-exploration/` | 📝 draft, nothing built | |
| `quartz-production-build-migration/` | 📝 plan, not started | Genuinely outstanding (viewer still dev-serve). |
| `wsl-resource-governance/` | 📝 drafted, not applied | Pairs with `C:\Users\yamao\.wslconfig` header. |
| `reaching-level-4-autonomy/` | 💡 ideas only | Not committed scope. |
| `Jupyter/` | 💡 captured, not built | |
| `autonomous-updates-with-security/` | ⚠️ unverified | No completion markers; both real OWUI upgrades ran manually. Folded into Watchtower decision D-2. |
| `portable-research-service/` | 📦 evergreen | Deliberately workspace-agnostic extractions. |
