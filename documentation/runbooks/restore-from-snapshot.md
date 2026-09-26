# Restore from a snapshot

For partial restores (one service went bad, everything else is fine).
For total disaster recovery, see
[`scripts/backup/restore-from-snapshot.ps1`](../../scripts/backup/restore-from-snapshot.ps1)
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

## Before ANY wipe of a host bind mount: one call, or nothing

A host bind mount's directory comes from a plane's `.env` (`OPEN_NOTEBOOK_DIR`, `LM_MODELS_DIR`) or a
fixed path in its compose file (`../data/tailscale`). Four ways to get it wrong, each of which makes a
`Remove-Item` delete the wrong directory: a RELATIVE value is resolved by compose against the compose
file's own directory, not this shell's; a blank value falls back to a compose default; a variable set
in THIS SHELL outranks the plane's `.env` in compose's interpolation; and - when a runbook block is
pasted line by line - a refused step does not stop the next line, which then runs with whatever an
earlier paste left in `$nb` or `$models`.

So every host-bind-mount wipe in this runbook is ONE call to `Invoke-BindRestore`, on one line. It
takes the directory from the container's OWN mount (`docker inspect` - running, or stopped by step 1),
compares it with the compose render made after the shell variable is removed, checks the archive's
`.sha256`, and only then deletes, restores and starts the containers. Everything it uses is a
parameter or assigned inside it (strict mode, so a name it did not assign is an error, never an older
value from your session). Any failure THROWS, and the delete is never reached without a resolve that
returned. There is no separate "resolve", "delete" or "restore" line to paste on its own.

Paste both functions once per session (as one paste; if they are not defined, the call line fails with
"not recognized" and nothing runs):

```powershell
function Resolve-BindTarget {
    param([Parameter(Mandatory)][string]$Container, [Parameter(Mandatory)][string]$Destination,
          [Parameter(Mandatory)][string]$ComposeFile, [string[]]$ComposeProfile = @(),
          [Parameter(Mandatory)][string]$Service, [string]$ShellVar = '')
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    # 1. A shell variable outranks the plane's .env for compose: remove it from this session.
    if ($ShellVar) { Remove-Item "Env:$ShellVar" -ErrorAction SilentlyContinue }
    # 2. What the container really mounts at $Destination (running, or stopped by step 1).
    #    JSON, filtered here: Windows PowerShell 5.1 strips the double quotes a Go template
    #    comparison needs when it passes them to docker.
    $mounted = $null; $mounts = $null; $list = $null
    $mounts = docker inspect $Container --format '{{json .Mounts}}'
    if ($LASTEXITCODE -eq 0 -and $mounts) {
        $list = $mounts | ConvertFrom-Json      # assigned first: in 5.1 a piped array is ONE object
        $mounted = @($list | Where-Object { $_.Destination -eq $Destination } | ForEach-Object { $_.Source })
        if ($mounted.Count -ne 1) { $mounted = $null } else { $mounted = $mounted[0] }
    }
    # 3. What the compose render says, now from the plane's .env (or its default) only.
    $profileArgs = @(); foreach ($pr in $ComposeProfile) { $profileArgs += @('--profile', $pr) }
    $cfg = $null; $vols = $null; $rendered = $null
    $cfg = docker compose -f $ComposeFile @profileArgs config --format json | ConvertFrom-Json
    if ($cfg -and $cfg.services.PSObject.Properties[$Service]) {
        $vols = $cfg.services.$Service.volumes
        $rendered = @($vols | Where-Object { $_.target -eq $Destination } | ForEach-Object { $_.source })
        if ($rendered.Count -ne 1) { $rendered = $null } else { $rendered = $rendered[0] }
    }
    if (-not $mounted)  { throw "REFUSED: no container '$Container' with a mount at $Destination - nothing proves which directory it uses" }
    if (-not $rendered) { throw "REFUSED: the compose render has no bind source at $Destination for '$Service'" }
    # Windows paths compare case-insensitively (lower-cased here); everywhere else case is part of
    # the name, so the comparison is -cne: /X and /x are different directories.
    $onWindows = ($env:OS -eq 'Windows_NT')
    $norm = { param($p) $x = "$p".Trim().TrimEnd('\', '/').Replace('\', '/'); if ($onWindows) { $x.ToLowerInvariant() } else { $x } }
    if ((& $norm $mounted) -cne (& $norm $rendered)) {
        throw "REFUSED: the container mounts '$mounted' but the render says '$rendered' - find out why before wiping anything"
    }
    if (-not (Test-Path -LiteralPath $rendered -PathType Container)) { throw "REFUSED: '$rendered' is not an existing directory" }
    $rendered
}

function Invoke-BindRestore {
    param([Parameter(Mandatory)][string]$Container, [Parameter(Mandatory)][string]$Destination,
          [Parameter(Mandatory)][string]$ComposeFile, [string[]]$ComposeProfile = @(),
          [Parameter(Mandatory)][string]$Service, [string]$ShellVar = '',
          [Parameter(Mandatory)][string]$BackupDir, [Parameter(Mandatory)][string]$Archive,
          [Parameter(Mandatory)][string[]]$Start)
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $target = $null; $backup = $null
    if ($Archive -notmatch '^[A-Za-z0-9._-]+\.tar\.gz$') { throw "REFUSED: '$Archive' is not an archive file name" }
    $backup = (Resolve-Path -LiteralPath $BackupDir).Path
    if (-not (Test-Path -LiteralPath (Join-Path $backup $Archive) -PathType Leaf)) { throw "REFUSED: $Archive is not in $backup" }
    # 1. The directory - resolved, or this call ends here (a throw), before anything is touched.
    $target = Resolve-BindTarget -Container $Container -Destination $Destination -ComposeFile $ComposeFile `
        -ComposeProfile $ComposeProfile -Service $Service -ShellVar $ShellVar
    if (-not $target) { throw 'REFUSED: no target was resolved' }
    # 2. The archive matches its sentinel, or nothing is deleted.
    docker run --rm -v "${backup}:/in:ro" alpine sh -c "cd /in && sha256sum -c '$Archive.sha256'"
    if ($LASTEXITCODE -ne 0) { throw "REFUSED: $Archive does not match $Archive.sha256 - nothing was deleted" }
    Write-Host "Wiping and restoring: $target"
    # 3. Wipe, restore, start - in this order, each checked.
    Remove-Item -Recurse -Force -Path (Join-Path $target '*')
    docker run --rm -v "${target}:/dest" -v "${backup}:/in:ro" alpine sh -c "cd /dest && tar xzf '/in/$Archive'"
    if ($LASTEXITCODE -ne 0) { throw "RESTORE FAILED (exit $LASTEXITCODE): $target was wiped and is not restored - fix the cause and run this same line again" }
    foreach ($c in $Start) {
        docker start $c | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "restored, but 'docker start $c' failed (exit $LASTEXITCODE)" }
    }
    Write-Host "Restored $Archive into $target; started: $($Start -join ', ')"
}
```

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

# 2. Verify, wipe, restore and start - ONE line (see "Before ANY wipe"). OPEN_NOTEBOOK_DIR lives
#    in OB1/docker/.env; blank means ../../../open-notebook, resolved against OB1/docker/.
Invoke-BindRestore -Container open_notebook -Destination /app/data -ComposeFile OB1/docker/docker-compose.yml -ComposeProfile notebook -Service open_notebook -ShellVar OPEN_NOTEBOOK_DIR -BackupDir .\backups\open-notebook -Archive 'notebook-data-20260530T011617Z.tar.gz' -Start open_notebook
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
| openwebui | `frontend_openwebui-data` | `docker compose -f frontend/docker-compose.yml --profile gpu --profile tailscale stop tailscale openwebui` (netns rule: tailscale first; start openwebui then tailscale). **The profile flags are not optional**: the frontend plane is profile-gated, and naming `tailscale` without `gpu` active exits 1 with `no such service: openwebui`. `scripts/backup/restore-from-snapshot.ps1` passes them for you. |
| mnemory | `memory_mnemory-data` | `docker compose -f memory/docker-compose.yml stop mnemory mnemory-cloud-gateway` |
| little-coder | `coder_little-coder-{journals,skill,cohorts,polyglot,sessions}` — **one archive, five volumes**, see below | `docker compose -f coder/docker-compose.yml stop little-coder open-terminal lc-egress` |
| tailscale | bind `./data/tailscale` | frontend project, see below |
| lm-models | bind `LM_MODELS_DIR` (from `inference/.env`; relative = against `inference/compose/`) | `docker compose -f inference/docker-compose.yml stop llama-cpp-upstream llama-cpp-embed-upstream` |
| ao-journals | `agent-org_ao-worker-1-journals`, `agent-org_ao-worker-2-journals` | `docker compose -f agent-org/docker/docker-compose.yml --profile workers stop ao-worker-1 ao-worker-2` |

**little-coder** is the one service whose backup is a SINGLE archive covering
several volumes. `backup/little-coder-backup.sh` tars `/data` whole, so
`little-coder-backup-<ts>.tar.gz` contains five top-level directories —
`journals/ skill/ cohorts/ polyglot/ sessions/` — each of which restores into
its own volume. `restore-from-snapshot.ps1` handles this with the
`volume-tar-subdir` type; you do not need to unpack it by hand.

> Until 2026-08-29 the restore catalog looked for five separate archives
> (`little-coder-journals-*.tar.gz` and friends) that **nothing has ever
> produced**, so `restore-from-snapshot.ps1 -Services little-coder` reported
> "No archives discovered" and aborted — with good backups sitting in the
> directory. If you are restoring from an archive older than that date, it is
> the combined shape and the current script reads it correctly.

To restore one volume by hand (the script does this for you), extract just its
directory:

```powershell
docker run --rm -v coder_little-coder-journals:/dest `
  -v "${PWD}\backups\little-coder:/in:ro" alpine sh -c `
  "mkdir -p /x && tar xzf /in/little-coder-backup-<ts>.tar.gz -C /x && `
   find /dest -mindepth 1 -delete && cp -a /x/journals/. /dest/"
```

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

**Tailscale**: the bind mount is `../data/tailscale` from `frontend/` (i.e. `./data/tailscale`), not a
named volume - a host bind-mount wipe, so it goes through `Invoke-BindRestore` like the others (see
"Before ANY wipe"; there is no shell variable to clear). Steps 2-4 become ONE line; `-Start` lists
openwebui before tailscale (the netns rule; starting a running container is a no-op):
```powershell
Invoke-BindRestore -Container tailscale -Destination /var/lib/tailscale -ComposeFile frontend/docker-compose.yml -ComposeProfile gpu,tailscale -Service tailscale -BackupDir .\backups\tailscale -Archive 'tailscale-<ts>.tar.gz' -Start openwebui,tailscale
```

**LM Studio models**: the bind mount is `LM_MODELS_DIR` from `inference/.env`. A RELATIVE
value (the `.env.example` default is `../../data/models/gguf`) is resolved by compose against
`inference/compose/`, not against the repo root this runbook runs from, and a `$env:LM_MODELS_DIR`
in your shell would outrank `inference/.env` - so never paste the variable into a delete. Steps
2-4 become ONE line through `Invoke-BindRestore` (see "Before ANY wipe"; the container is the one
step 1 stopped):
```powershell
Invoke-BindRestore -Container llama-cpp-upstream -Destination /models -ComposeFile inference/docker-compose.yml -ComposeProfile local -Service llama-cpp-upstream -ShellVar LM_MODELS_DIR -BackupDir .\backups\lm-models -Archive 'lm-models-<ts>.tar.gz' -Start llama-cpp-upstream,llama-cpp-embed-upstream
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
