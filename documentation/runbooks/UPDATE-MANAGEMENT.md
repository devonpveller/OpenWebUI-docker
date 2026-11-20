# Update management

> Status: LIVE · rewritten 2026-08-20 (merges the retired Ollama-era
> `UPDATE-QUICK-START.md` + `UPDATE-MANAGEMENT.md`, both archived under
> `../documentation-plans-ai-stack/journal/archive/`).

How the stack takes updates. Everything is **manual and verified** - there is
no auto-updater (Watchtower was retired 2026-08-20, CLEANUP-PLAN v3 D-2).
Run every command below from the repo root. Each `docker compose` command below
that you are meant to run names its plane's compose file with `-f`: a bare `docker compose` addresses the root anchor, which has no
services.

## Open WebUI

Upgrades are planned, executed, and verified per release with a written plan —
the pattern to copy is
[`update-owui-to-0-11-0/UPGRADE-PLAN.md` in the plan store](../../../documentation-plans-ai-stack/implementation-guide/update-owui-to-0-11-0/UPGRADE-PLAN.md)
(executed 2026-08-20). Non-negotiables learned there:

- `WEBUI_SECRET_KEY` is pinned in `.env` — never let a recreate regenerate it
  (it encrypts values at rest in `webui.db`).
- Never restart `openwebui` alone: the tailscale container shares its netns —
  restart order is **openwebui → tailscale**, then verify the 8 tailnet serve
  routes (`scripts/checks/stack-watchdog.ps1` self-heals them).
- Never smoke-test OWUI tools via `/api/chat/completions` (false regression);
  test through the UI or the tool-server path.
- Re-verify the `frontend/owui/` plugin snapshots against `webui.db` after the upgrade
  (see `frontend/owui/README.md` "Deployment sync status").

### 0.11.0 -> 0.13.4: deploy and rollback

Plan, lab evidence and the test plan: `owui-0.13.4-upgrade/` in the plan store. This is
an operator-window deploy (a rebuild of `openwebui:local` is a deliberate deploy, never a
side effect). The migrations (0.11.1 .. 0.13.1) are one-way and a failed one now stops
startup instead of half-starting, so the **database backup is the rollback**.

Before: hold the `frontend` lease (`lease.ps1 -Acquire -Name frontend`), tell the operator
no chat should be mid-stream, confirm `frontend/.env` has `OAUTH_SCOPES` with
`offline_access` and `WEBUI_SECRET_KEY` set (names only - never print values).

1. **Back up webui.db** (a consistent copy, WAL-safe; about 1.2 GB, check free space):
   `docker exec openwebui python -c "import sqlite3;s=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);d=sqlite3.connect('/app/backend/data/webui.db.pre-0134');s.backup(d);d.close()"`
   then `docker cp openwebui:/app/backend/data/webui.db.pre-0134 <backup dir>\webui.db.pre-0134`
   and remove the in-volume copy. Keep the file until the 0.13.4 line has been live and
   verified for a week.
2. **Keep the old image:** `docker tag openwebui:local openwebui:pre-0134-v0.11.0` (a
   retag, not a rebuild).
3. **Build:** `docker compose -f frontend/docker-compose.yml --profile gpu build openwebui`
   (the torch swap downloads the cu121 wheels; about 4-5 minutes). The build fails loudly
   if the builtin.py timestamp patch does not apply.
4. **Keep attached files injected (owui-0134).** On 0.11.0 a model with no `file_context`
   capability got attached files injected (`middleware.py:2925`, default True); on 0.13.4 it does
   not (`utils/tools.py:618-623`: default is false unless `legacy` or no session), and the same
   holds for a model with no row at all. One idempotent config cell restores 0.11.0: a global
   default `capabilities.file_context = true` in `models.default_metadata` (a model's own value,
   true or false, still wins). `function_calling` needs no step: unset means native on BOTH
   versions (`middleware.py:2907` on 0.11.0). First a dry run on the backup (safe, lists the active
   rows it applies to by id and name, no secrets):
   `python scripts/stack/owui_role_migration.py --db <backup dir>\webui.db.pre-0134 --file-context-default`
   Then, with `openwebui` STOPPED (`docker compose -f frontend/docker-compose.yml --profile gpu stop openwebui`;
   tailscale stays down until step 5), apply it to the volume file from a throwaway container
   (no network, the old image's python; the script is stdlib only):
   `docker run --rm --network none -v frontend_openwebui-data:/data -v <repo>\scripts\stack:/s:ro --entrypoint python openwebui:pre-0134-v0.11.0 /s/owui_role_migration.py --db /data/webui.db --file-context-default --apply --restore-file /data/fc-restore-0134.json`
   A second run prints "nothing to change". **Revert** (OWUI stopped): the same command with
   `--restore /data/fc-restore-0134.json --apply`, or (OWUI running) clear it in Admin Settings >
   Models > default metadata; a rollback to the backup `webui.db` undoes it too. Then move
   `/data/fc-restore-0134.json` out of the volume (it holds only the one cell's before/after text).
5. **Recreate openwebui, wait healthy, then tailscale** (never openwebui alone):
   `docker compose -f frontend/docker-compose.yml --profile gpu up -d --no-deps openwebui`,
   wait for `healthy` (the first start runs the migrations on a 1.1 GB database; the lab
   took about 1 minute with no GPU), then
   `docker compose -f frontend/docker-compose.yml --profile gpu up -d --no-deps tailscale`
   and verify the 8 tailnet serve routes.
6. **Verify:** `scripts/checks/check-owui-drift.ps1` (only the `deep_research` row differs,
   as before the upgrade); sign in through SSO in a browser; open a chat on a model bound
   to `open-brain` and call a tool; `python scripts/stack/stack.py labels` dry run reports
   nothing to change; Admin > Settings > Integrations still lists the three tool servers.
   Expect one-time: the image-generation settings are converted into an image connection
   on first start (log line `Migrated image generation settings`).

**Rollback** (the migrations cannot be reversed in place): stop `openwebui`; put the
backup over `webui.db` in the `openwebui-data` volume (chats and settings made on 0.13.4
are lost with it); `docker tag openwebui:pre-0134-v0.11.0 openwebui:local`; revert the
compose / Dockerfile pin commit; `up -d --no-deps openwebui`, wait healthy, then
`up -d --no-deps tailscale`.

## Inference plane (llama.cpp / llama-swap / LiteLLM)

- Model swaps follow the written-plan pattern:
  `qwen3.8-model-swap/` in the plan store is the reference execution.
- The llama-cpp upstreams run floating tags (`llama-swap:cuda`,
  `llama.cpp:server-cuda`, both in `inference/compose/upstreams.yml`). To take a
  new build: `docker compose -f inference/docker-compose.yml pull llama-cpp-upstream llama-cpp-embed-upstream`,
  then `docker compose -f inference/docker-compose.yml up -d --no-deps llama-cpp-upstream llama-cpp-embed-upstream`,
  then `python scripts/stack/stack.py health`. Add `--dry-run` after the `-f <file>`
  to see what either compose step would do first. Both services sit behind the
  `local` profile, which `inference/.env` supplies. (`update-stack.bat` used to do
  this with bare `docker compose` calls; it was archived 2026-09-25 to
  `scripts/archive/legacy-recovery/`.)
- LiteLLM (`llm-gateway`) and `llm-queue` are pinned images / local builds —
  bump deliberately, one PR each, and re-run
  `scripts/checks/check-llm-gateway-routing.ps1`.

## Everything else

Images are digest- or tag-pinned in the compose files. Updating one means:
bump the pin → `docker compose -f <plane>/docker-compose.yml up -d <service>` → verify via
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
(`../documentation-plans-ai-stack/journal/notes/deploy-gate-2026-09-06.md`).

## After any update

1. `mcp` sysadmin `stack_health` or `scripts/recovery/status_check.py` — everything
   running.
2. Affected plane's check script passes.
3. Backups still fresh the next morning (`sysadmin-mcp/check_backups.py`
   scheduled daily 09:30 posts to Mattermost on staleness).
