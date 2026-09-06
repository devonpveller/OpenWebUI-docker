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

**A service with `build:` is not a pinned image - it deploys through the
door, never `up -d` alone.** Today that is the OB1 integrations in
`OB1/docker/docker-compose.yml` (openbrain-curator, openbrain-research, the
workers). After a gitlink bump lands on the deployment line:

    powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research -WhatIfOnly
    powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research

`scripts/stack/ob1-deploy.ps1` refuses when OB1 on disk is not the parent's
gitlink (the rule gates 5b/5c/5d apply at the commit), builds with
`--build-arg OB1_SHA=<pin>` so the image label
`org.opencontainers.image.revision` names the commit, brings the service up,
force-recreates the dependents you name (`up -d` after a depends_on-only
change does NOT recreate them - seen on the 2026-09-06 curator deploy), waits
for healthy and exits non-zero naming any container that loops within 60 s.
**Recreate is not rebuild**: a dependent named in `-Recreate` is recreated on
the image its tag already holds. So the rule is **one door call per service
whose image changed, then `-Recreate` for the depends_on-only dependents** -
a bump that touches both research-curator and research-service is
`-Service openbrain-research` first (nothing depends on it), then
`-Service openbrain-curator -Recreate openbrain-research`; the example above
is the depends_on-only case (only the curator's image moved). The summary
prints the label of the service it built; check a co-bumped dependent's
label yourself.
A bare `docker compose up -d` reuses whatever image is cached under the
`:local` tag and says nothing about any of that. Verify afterwards with
`scripts/checks/check-openbrain-health.ps1`, which also names `research_jobs`
rows that ended in `error` in the last 24 h. Only research-curator and
research-service carry the label today (2026-09-06); the other six
integration Dockerfiles are a recorded follow-up
(`documentation/notes/deploy-gate-2026-09-06.md`).

## After any update

1. `mcp` sysadmin `stack_health` or `scripts/recovery/status_check.py` — everything
   running.
2. Affected plane's check script passes.
3. Backups still fresh the next morning (`sysadmin-mcp/check_backups.py`
   scheduled daily 09:30 posts to Mattermost on staleness).
