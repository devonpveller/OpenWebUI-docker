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

**Operator only - disable the watchdog for the whole window (deploy AND rollback).** While
`openwebui` is stopped, the 10-minute `\StackWatchdog` (`scripts/checks/stack-watchdog.ps1`) would
alert on the unhealthy openwebui after 180 s (`:3464-3491`), recreate tailscale (`:3546`), or, in its
"tailscale not running" branch, run `docker compose -f frontend\docker-compose.yml up -d tailscale`
WITHOUT `--no-deps` (`:3553`), which starts `openwebui` too - on the already-rebuilt 0.13.4
`openwebui:local`, in the middle of the file-context step or the restore. From an elevated prompt:
`schtasks /change /tn "\StackWatchdog" /disable` (confirm with `schtasks /query /tn "\StackWatchdog" /fo list`:
`Status: Disabled`). The agent never runs this; the operator does.

`<backup dir>` below is a Windows folder with about 3 GB free, outside every repo. `<repo>` is the
checkout root (`D:\Open WebUIi-stack`: it contains a space, so every bind mount below is quoted and must stay quoted). Order matters: **build first (openwebui still serving), then stop, then back up the
quiet database**, so a rollback loses nothing written during the build.

1. **Keep the old image:** `docker tag openwebui:local openwebui:pre-0134-v0.11.0` (a
   retag, not a rebuild).
2. **Build** (users are still served): `docker compose -f frontend/docker-compose.yml --profile gpu build openwebui`
   (the torch swap downloads the cu121 wheels; about 4-5 minutes). The build fails loudly
   if the builtin.py timestamp patch does not apply. `openwebui:local` is now 0.13.4 but the
   running container is untouched.
3. **Stop tailscale, then openwebui** (netns order: tailscale shares openwebui's namespace, so it
   must not be left running in a dead one; the tailnet is down from here until step 7):
   `docker compose -f frontend/docker-compose.yml --profile gpu stop tailscale`, then
   `docker compose -f frontend/docker-compose.yml --profile gpu stop openwebui`.
4. **Back up the quiet webui.db** (SQLite's backup API from a throwaway container on the data volume;
   no network, the old image's python; it folds in any WAL; about 1.2 GB; the last line must print
   `quick_check [('ok',)]`):
   `docker run --rm --network none -v frontend_openwebui-data:/data -v "<backup dir>:/b" --entrypoint python openwebui:pre-0134-v0.11.0 -c "import sqlite3;s=sqlite3.connect('/data/webui.db');d=sqlite3.connect('/b/webui.db.pre-0134');s.backup(d);d.close();s.close();c=sqlite3.connect('file:/b/webui.db.pre-0134?mode=ro&immutable=1',uri=True);print('quick_check',c.execute('pragma quick_check').fetchall())"`
   Keep the file until the 0.13.4 line has been live and verified for a week.
5. **Keep attached files injected (owui-0134).** On 0.11.0 a model with no `file_context`
   capability got attached files injected (`middleware.py:2925`, default True); on 0.13.4 it does
   not (`utils/tools.py:618-623`: default is false unless `legacy` or no session), and the same
   holds for a model with no row at all. One idempotent config cell restores 0.11.0: a global
   default `capabilities.file_context = true` in `models.default_metadata` (a model's own value,
   true or false, still wins). `function_calling` needs no step: unset means native on BOTH
   versions (`middleware.py:2907` on 0.11.0). First a dry run on the backup (safe, lists the active
   rows it applies to by id and name, no secrets):
   `python scripts/stack/owui_role_migration.py --db "<backup dir>\webui.db.pre-0134" --file-context-default`
   Then, with `openwebui` stopped (step 3), apply it to the volume file from a throwaway container
   (no network, the old image's python; the script is stdlib only):
   `docker run --rm --network none -v frontend_openwebui-data:/data -v "<repo>\scripts\stack:/s:ro" --entrypoint python openwebui:pre-0134-v0.11.0 /s/owui_role_migration.py --db /data/webui.db --file-context-default --apply --restore-file /data/fc-restore-0134.json`
   Running the SAME command again is safe: with nothing left to write it prints `nothing to change`
   and exits 0 without touching the restore file (it still refuses to overwrite a restore file when
   there IS a cell to write, so a stale file from an earlier deploy stops a fresh apply - see
   step 7 and Rollback step 3). **Revert** (OWUI stopped): the same command with
   `--restore /data/fc-restore-0134.json --apply`, or (OWUI running) clear it in Admin Settings >
   Models > default metadata; a rollback to the backup `webui.db` undoes it too. The restore file
   (`/data/fc-restore-0134.json`, only the one cell's before/after text) stays in the volume until
   step 7 has passed.
6. **Recreate openwebui, wait healthy, then tailscale** (never openwebui alone):
   `docker compose -f frontend/docker-compose.yml --profile gpu up -d --no-deps openwebui`,
   wait for `healthy` (the first start runs the migrations on a 1.1 GB database; the lab
   took about 1 minute with no GPU), then
   `docker compose -f frontend/docker-compose.yml --profile gpu up -d --no-deps tailscale`
   and verify the 8 tailnet serve routes.
7. **Verify:** `scripts/checks/check-owui-drift.ps1` (only the `deep_research` row differs,
   as before the upgrade); sign in through SSO in a browser; open a chat on a model bound
   to `open-brain` and call a tool; `python scripts/stack/stack.py labels` dry run reports
   nothing to change; Admin > Settings > Integrations still lists the three tool servers.
   Expect one-time: the image-generation settings are converted into an image connection
   on first start (log line `Migrated image generation settings`). When verify has passed, archive
   the restore file out of the volume (kept, not deleted; otherwise a later deploy's step 5 would
   refuse to write over it): `docker run --rm --network none -v frontend_openwebui-data:/data -v "<backup dir>:/b" --entrypoint sh openwebui:pre-0134-v0.11.0 -c "mv /data/fc-restore-0134.json /b/fc-restore-0134.json.applied && echo moved"`
8. **Operator only - re-enable the watchdog and read its log.** `schtasks /change /tn "\StackWatchdog" /enable`
   (`schtasks /query /tn "\StackWatchdog" /fo list` shows `Ready`; the task name is the one the repo's
   docs use, `check-openbrain-health.ps1:9`, `scripts/README.md:67`, `stack-watchdog.ps1:1813`; note
   `scripts/checks/install-service.ps1` installs a Windows SERVICE of the same name, which is not what
   runs here, so the `/query` must find a task). After its next pass (:x7), from the repo root, with the
   time you disabled it as the last argument (the log is ASCII with a stray UTF-16 BOM, so PowerShell's
   `Get-Content` / `Select-String` mis-decode it; this reads it as latin-1):
   `python -c "import sys,re;t0=sys.argv[1];p=re.compile(r'\[(WARN|ERROR)\].*(Tailscale|OpenWebUI|\[FIX\])');[print(l.rstrip()) for l in open(r'logs\tailscale-health.log',encoding='latin-1') if len(l)>19 and l[:19]>=t0 and p.search(l)]" "2026-11-20 14:30:00"`
   The watchdog writes `yyyy-MM-dd HH:mm:ss [LEVEL] message` (`Write-LogEntry`); its own recoveries are
   `[WARN] Tailscale container ...` (`stack-watchdog.ps1:3545`, `:3549`, `:3552`), `[WARN] OpenWebUI (GPU-enabled)
   is not healthy ...` (`:3465`) and `[ERROR] OpenWebUI failed to become healthy ...` (`:3489`), and the Open Brain
   restarts are `[WARN] OpenBrain [FIX] ...`; the filter catches all of them and ignores unrelated WARN/ERROR
   lines (Open Brain drift, backups). It must print nothing dated inside the window (the task was off); a line
   from the first pass after the re-enable is worth reading. Checked read-only 2026-11-20 against the live
   log: it finds the `07:48:51 [WARN] Tailscale container is RUNNING but health=unhealthy` recovery.

**Rollback** (the migrations cannot be reversed in place; chats and settings made on 0.13.4 are
lost with it). The operator disables `\StackWatchdog` first (above). Then, in this order:

1. Stop tailscale, then openwebui:
   `docker compose -f frontend/docker-compose.yml --profile gpu stop tailscale` then `... stop openwebui`.
2. **Restore the backup, WITHOUT 0.13.4's WAL.** 0.13.4 runs SQLite in WAL mode, so `webui.db-wal` /
   `webui.db-shm` may sit beside the file; copying only `webui.db` would let SQLite replay 0.13.4
   frames onto the restored 0.11.0 file. This deletes them, copies the backup in, and checks it
   (`integrity_check` is slower and also fine; it must print `[('ok',)]`; the head must be
   `f0bd01a18a3d`, 0.11.0's; the last line must list only `webui.db`):
   `docker run --rm --network none -v frontend_openwebui-data:/data -v "<backup dir>:/b:ro" --entrypoint python openwebui:pre-0134-v0.11.0 -c "import os,shutil,sqlite3;[os.remove('/data/'+f) for f in ('webui.db-wal','webui.db-shm') if os.path.exists('/data/'+f)];shutil.copyfile('/b/webui.db.pre-0134','/data/webui.db');c=sqlite3.connect('file:/data/webui.db?mode=ro&immutable=1',uri=True);print('quick_check',c.execute('pragma quick_check').fetchall());print('head',c.execute('select version_num from alembic_version').fetchall());print('left',sorted(f for f in os.listdir('/data') if f.startswith('webui.db')))"`
3. **Move the file-context restore file out of the volume** (it describes a cell the restored database no
   longer has, and a left-over `/data/fc-restore-0134.json` makes a later redeploy's step 5 refuse to
   write; it is kept in the backup dir, not deleted):
   `docker run --rm --network none -v frontend_openwebui-data:/data -v "<backup dir>:/b" --entrypoint sh openwebui:pre-0134-v0.11.0 -c "if [ -f /data/fc-restore-0134.json ]; then mv /data/fc-restore-0134.json /b/fc-restore-0134.json.rolled-back && echo moved; else echo none; fi"`
4. `docker tag openwebui:pre-0134-v0.11.0 openwebui:local`; revert the compose / Dockerfile pin commit
   (the stock profile shares this volume: never start an image newer or older than the database).
5. `docker compose -f frontend/docker-compose.yml --profile gpu up -d --no-deps openwebui`, wait
   `healthy`, then `docker compose -f frontend/docker-compose.yml --profile gpu up -d --no-deps tailscale`.
6. Operator re-enables `\StackWatchdog` and checks the log, as in deploy step 8.

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
