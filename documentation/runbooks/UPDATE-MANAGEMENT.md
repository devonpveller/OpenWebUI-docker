# Update management

> Status: LIVE · rewritten 2026-08-20 (merges the retired Ollama-era
> `UPDATE-QUICK-START.md` + `UPDATE-MANAGEMENT.md`, both archived under
> `documentation/archive/`).

How the stack takes updates. Everything is **manual and verified** — the only
auto-updater is Watchtower, scoped to the `openwebui` image and pending
retirement (CLEANUP-PLAN v3, decision D-2).

## Open WebUI

Upgrades are planned, executed, and verified per release with a written plan —
the pattern to copy is
[`implementation-guide/update-owui-to-0-11-0/UPGRADE-PLAN.md`](../implementation-guide/update-owui-to-0-11-0/UPGRADE-PLAN.md)
(executed 2026-08-20). Non-negotiables learned there:

- `WEBUI_SECRET_KEY` is pinned in `.env` — never let a recreate regenerate it
  (it encrypts values at rest in `webui.db`).
- Never restart `openwebui` alone: the tailscale container shares its netns —
  restart order is **openwebui → tailscale**, then verify the 8 tailnet serve
  routes (`scripts/checks/stack-watchdog.ps1` self-heals them).
- Never smoke-test OWUI tools via `/api/chat/completions` (false regression);
  test through the UI or the tool-server path.
- Re-verify the `owui/` plugin snapshots against `webui.db` after the upgrade
  (see `owui/README.md` "Deployment sync status").

## Inference plane (llama.cpp / llama-swap / LiteLLM)

- Model swaps follow the written-plan pattern:
  `implementation-guide/qwen3.8-model-swap/` is the reference execution.
- `scripts/recovery/update-stack.bat` drives image updates for the llama-cpp upstreams.
- LiteLLM (`llm-gateway`) and `llm-queue` are pinned images / local builds —
  bump deliberately, one PR each, and re-run
  `scripts/checks/check-llm-gateway-routing.ps1`.

## Everything else

Images are digest- or tag-pinned in the compose files. Updating one means:
bump the pin → `docker compose up -d <service>` → verify via
`scripts/checks/stack-watchdog.ps1` (main), `check-openbrain-health.ps1`
(OB1), or `check-agent-org-health.ps1` (agent-org). The **container rule**
applies to anything that adds/removes/renames a service: compose + recovery
scripts + stack-map doc change together.

## After any update

1. `mcp` sysadmin `stack_health` or `scripts/recovery/status_check.py` — everything
   running.
2. Affected plane's check script passes.
3. Backups still fresh the next morning (`sysadmin-mcp/check_backups.py`
   scheduled daily 09:30 posts to Mattermost on staleness).
