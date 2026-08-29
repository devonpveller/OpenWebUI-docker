# Restore from a snapshot

For partial restores (one service went bad, everything else is fine).
For total disaster recovery, see
[`scripts/backup/restore-from-snapshot.ps1`](../scripts/backup/restore-from-snapshot.ps1)
which orchestrates the whole stack in dependency order.

**Pre-flight, every restore**:

1. Identify the snapshot source. Locally:
   `./backups/<service>/<service>-<UTC-ts>.<ext>`
   On the NAS: `\\<nas>\<share>\ai-stack\portal\slot-<A|B>\<service>\<same-file>`
2. **Verify the sha256 sentinel before restoring**:
   ```powershell
   cd <path-to-snapshot-dir>
   docker run --rm -v "${PWD}:/backups:ro" alpine sh -c "cd /backups && sha256sum -c <service>-*.sha256"
   ```
   If it doesn't say `OK`, **stop**. The archive is corrupted; use the
   previous day's backup or the other NAS slot.
3. Snapshot the current (possibly broken) state before overwriting. The
   easiest is to rename the affected Docker volume:
   `docker volume create --name <name>-pre-restore-<UTC-ts>` and `docker
   run --rm` a one-shot tar of the broken volume into it.

---

## openbrain-db (PostgreSQL via pg_dump)

**Source**: `./backups/openbrain-db/openbrain-<UTC-ts>.dump` (custom format)

**Restore is destructive** — `pg_restore --clean --if-exists` drops then
recreates every object. Do it while the OB1 stack is up (the db must be
running and reachable) but everything that USES the db should be stopped
first.

```powershell
# 1. Stop OB1 services that write to the db (keep the db itself running).
$ob1Writers = @('openbrain-mcp','openbrain-ext','openbrain-mcpo',
                'openbrain-mcpo-ext','openbrain-gateway','openbrain-rest',
                'openbrain-postgrest','openbrain-entity-worker',
                'openbrain-wiki','openbrain-cron','openbrain-gmail-pull',
                'openbrain-gmail-prune','openbrain-digest')
docker compose -f .\OB1\docker\docker-compose.yml stop @ob1Writers

# 2. Verify the dump sentinel.
docker run --rm -v "${PWD}\backups\openbrain-db:/backups:ro" alpine sh -c "cd /backups && sha256sum -c openbrain-*.sha256 | tail -1"

# 3. Restore. pg_restore reads the dump file mounted in.
$dump = 'openbrain-20260530T010718Z.dump'   # <-- substitute your timestamp
docker run --rm `
  --network open-brain_obnet `
  -v "${PWD}\backups\openbrain-db:/in:ro" `
  -e PGPASSWORD=$env:OB1_PG_PASSWORD `
  postgres:16-alpine `
  pg_restore --host openbrain-db --port 5432 --username postgres `
    --dbname openbrain --clean --if-exists --no-owner --no-acl --verbose "/in/$dump"

# 4. Start the writers back up.
docker compose -f .\OB1\docker\docker-compose.yml start @ob1Writers
```

`$env:OB1_PG_PASSWORD` should be set from `OB1/docker/.env`
(`POSTGRES_PASSWORD`).

**Post-restore validation**:
```powershell
docker exec openbrain-db psql -U postgres -d openbrain -c "SELECT count(*) FROM thoughts;"
```
The count should match (roughly) what was in the dump (look in the
`.dump`'s metadata via `pg_restore -l /in/<dump>` to see object counts).

---

## openbrain-wiki (volume tar)

**Source**: `./backups/openbrain-wiki/openbrain-wiki-<UTC-ts>.tar.gz`

```powershell
# 1. Stop the wiki container.
docker compose -f .\OB1\docker\docker-compose.yml stop openbrain-wiki

# 2. Verify sentinel.
docker run --rm -v "${PWD}\backups\openbrain-wiki:/backups:ro" alpine sh -c "cd /backups && sha256sum -c openbrain-wiki-*.sha256 | tail -1"

# 3. Wipe + restore the volume contents.
$archive = 'openbrain-wiki-20260530T010915Z.tar.gz'
docker run --rm `
  -v open-brain_openbrain-wiki-data:/dest `
  -v "${PWD}\backups\openbrain-wiki:/in:ro" `
  alpine sh -c "find /dest -mindepth 1 -delete && cd /dest && tar xzf /in/$archive"

# 4. Restart.
docker compose -f .\OB1\docker\docker-compose.yml start openbrain-wiki
```

Validate that `docker exec openbrain-wiki ls -la /wiki/.git` shows the
expected git history.

---

## open-notebook (SurrealDB + notebook_data)

**Two-phase**. Restore SurrealDB FIRST, then the bind mount.

### Phase 1 — SurrealDB (logical import)

**Source**: `./backups/open-notebook/surreal-<UTC-ts>.surql.gz`

The surrealdb container should stay running — `surreal import` works
against a live db.

```powershell
# 1. Verify sentinel.
docker run --rm -v "${PWD}\backups\open-notebook:/backups:ro" alpine sh -c "cd /backups && sha256sum -c surreal-*.sha256 | tail -1"

# 2. Decompress on the host.
$archive = 'surreal-20260530T011617Z.surql.gz'
gzip -d -k -f ".\backups\open-notebook\$archive"   # writes the same name without .gz

# 3. Replay against surrealdb. Use the SAME root credentials as the backup
#    container (root/root by convention; see docker-compose.yml).
$dump = $archive -replace '\.gz$',''
docker exec -i open-notebook-backup surreal import `
  --endpoint http://surrealdb:8000 --username root --password root `
  --auth-level root --namespace open_notebook --database open_notebook `
  /tmp/dump.surql < ".\backups\open-notebook\$dump"
```

Validate:
```powershell
docker exec -i open-notebook-backup surreal sql --endpoint http://surrealdb:8000 -u root -p root --namespace open_notebook --database open_notebook
# At the prompt:
INFO FOR DB;
```

### Phase 2 — notebook_data (host bind mount tar)

**Source**: `./backups/open-notebook/notebook-data-<UTC-ts>.tar.gz`

```powershell
# 1. Stop open_notebook.
docker stop open_notebook

# 2. Verify sentinel.
docker run --rm -v "${PWD}\backups\open-notebook:/backups:ro" alpine sh -c "cd /backups && sha256sum -c notebook-data-*.sha256 | tail -1"

# 3. Wipe + restore the host bind mount.
$archive = 'notebook-data-20260530T011617Z.tar.gz'
Remove-Item -Recurse -Force 'D:\Open WebUI\open-notebook\notebook_data\*'
docker run --rm `
  -v 'D:\Open WebUI\open-notebook\notebook_data:/dest' `
  -v "${PWD}\backups\open-notebook:/in:ro" `
  alpine sh -c "cd /dest && tar xzf /in/$archive"

# 4. Restart open_notebook.
docker start open_notebook
```

---

## Portal (caddy + authelia)

**Sources**:
- `./backups/caddy/caddy-<UTC-ts>.tar.gz`
- `./backups/authelia/authelia-<UTC-ts>.tar.gz`

```powershell
# 1. Stop only the portal services.
.\scripts\portal\portal-off.ps1

# 2. Verify sentinels.
docker run --rm -v "${PWD}\backups\caddy:/backups:ro" alpine sh -c "cd /backups && sha256sum -c caddy-*.sha256 | tail -1"
docker run --rm -v "${PWD}\backups\authelia:/backups:ro" alpine sh -c "cd /backups && sha256sum -c authelia-*.sha256 | tail -1"

# 3. Restore each volume.
$caddyArchive = 'caddy-20260529T183254Z.tar.gz'
docker run --rm `
  -v portal_caddy-data:/dest `
  -v "${PWD}\backups\caddy:/in:ro" `
  alpine sh -c "find /dest -mindepth 1 -delete && cd /dest && tar xzf /in/$caddyArchive"

$autheliaArchive = 'authelia-20260529T183254Z.tar.gz'
docker run --rm `
  -v portal_authelia-data:/dest `
  -v "${PWD}\backups\authelia:/in:ro" `
  alpine sh -c "find /dest -mindepth 1 -delete && cd /dest && tar xzf /in/$autheliaArchive"

# 4. Start back up. portal-init will re-chown if necessary.
.\scripts\portal\portal-on.ps1
```

**Important**: after Authelia restore, any sessions issued before the
backup will still be valid (the JWT secret is the same). If the backup
predates your last WebAuthn enrollment, you may need to re-enroll —
verify by signing in.

---

## Other tar-based services (openwebui, mnemory, little-coder, tailscale, lm-models)

All follow the same pattern. **Volume names carry their PROJECT prefix since
Part K (2026-08-21)** — the live volumes are:

| Service | Volume(s) | Stop/start via |
|---|---|---|
| openwebui | `frontend_openwebui-data` | `docker compose -f frontend/docker-compose.yml --env-file .env stop tailscale openwebui` (netns rule: tailscale first; start openwebui then tailscale) |
| mnemory | `memory_mnemory-data` | `docker compose -f memory/docker-compose.yml --env-file .env stop mnemory mnemory-cloud-gateway` |
| little-coder | `coder_little-coder-{journals,skill,cohorts,polyglot,sessions}` | `docker compose -f coder/docker-compose.yml --env-file .env stop little-coder open-terminal lc-egress` |
| tailscale | bind `./data/tailscale` | frontend project, see below |
| lm-models | bind `C:\Users\yamao\.lmstudio\models` | `docker compose -f inference/docker-compose.yml --env-file .env stop llama-cpp-upstream llama-cpp-embed-upstream` |
| ao-journals | `agent-org_ao-worker-1-journals`, `agent-org_ao-worker-2-journals` | `docker compose -f agent-org/docker/docker-compose.yml --profile workers stop ao-worker-1 ao-worker-2` |

**ao-journals** (agent-org worker task journals, added 2026-08-29 with
memory-plane Phase 0.3): one archive per worker —
`backups/ao-worker-1-journals/ao-worker-1-journals-<ts>.tar.gz` restores into
`agent-org_ao-worker-1-journals`, and likewise for worker 2. Do not cross them.
The `--profile workers` flag is REQUIRED: the workers and their backup sidecars
are profile-gated, so a stop/start without it silently addresses nothing.
These journals are the append-only evidence corpus, **not** authoritative org
state — efforts, gates and audit live in `agent-bridge-db`. Restoring journals
recovers the record, never the org's position.

(The pre-split `ai-stack_*` volumes still exist as rollback copies until the
post-K soak cleanup — do NOT restore into them, nothing reads them.
`smolcrawl` retired 2026-08-21; its old archives have nothing to restore into.)

```powershell
# 1. Stop the consumer service(s) — see the table above.

# 2. Verify sentinel.
docker run --rm -v "${PWD}\backups\<service>:/backups:ro" alpine sh -c "cd /backups && sha256sum -c <service>-*.sha256 | tail -1"

# 3. Restore the volume (project-prefixed name from the table).
$archive = '<service>-<ts>.tar.gz'
docker run --rm `
  -v <project>_<volume-name>:/dest `
  -v "${PWD}\backups\<service>:/in:ro" `
  alpine sh -c "find /dest -mindepth 1 -delete && cd /dest && tar xzf /in/$archive"

# 4. Restart via the same plane compose file (compose start, or `up -d`).
```

**Tailscale**: the bind mount is `./data/tailscale`, not a named volume.
Step 3 becomes:
```powershell
Remove-Item -Recurse -Force '.\data\tailscale\*'
docker run --rm -v "${PWD}\data\tailscale:/dest" -v "${PWD}\backups\tailscale:/in:ro" alpine sh -c "cd /dest && tar xzf /in/$archive"
```

**LM Studio models**: the bind mount is your Windows path
`C:\Users\yamao\.lmstudio\models`. Step 3:
```powershell
Remove-Item -Recurse -Force 'C:\Users\yamao\.lmstudio\models\*'
docker run --rm -v 'C:\Users\yamao\.lmstudio\models:/dest' -v "${PWD}\backups\lm-models:/in:ro" alpine sh -c "cd /dest && tar xzf /in/$archive"
```
**Time this carefully** — restoring 50+ GB over USB or slow disk will
take a while.

---

## What survives a restore vs needs reconfiguration

Persistent state captured by the snapshot:
- All application data (chats, notebooks, OB1 thoughts, etc.)
- WebAuthn / TOTP enrollments (in authelia-data sqlite)
- Caddy ACME state (regenerable, but useful)
- Tailscale device identity (skips a re-auth)
- OB1 wiki git history

**Not captured by backups, must be re-supplied manually**:
- `.env` (gitignored). Keep an off-host copy of your `.env` so a fresh
  Windows install has the secrets the compose needs.
- `secrets/google/portal-alerter/credentials.json` and `token.json`
  (DPAPI-bound — only the original Windows user on the original machine
  can decrypt). On a new machine, re-run `setup-token.ts`.
- `secrets/nas-backup-vault.dat` (DPAPI-bound, LocalMachine scope). On a
  new machine, re-run `scripts/backup/set-nas-credential.ps1`.
- Cloudflare Tunnel registration (`cloudflared` is configured by token;
  if the token expires you re-create at the Cloudflare dashboard).
- Tailscale auth key (re-issue at `https://login.tailscale.com/admin/settings/keys`).

If you're restoring to fresh hardware, restore the data first, then
fill in these secrets, then `.\scripts\stack\stack.ps1 up` (anchor networks first, then every project in dependency order).
