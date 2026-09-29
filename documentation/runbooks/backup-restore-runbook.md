# Backup Restore Runbook

**Purpose:** recover a service from its backup when it reaches an irreversibly
broken state (corrupt DB, wiped/garbled volume, bad migration). Every procedure
here restores a *point-in-time* copy — **anything written since that backup is
lost**, so treat restore as a last resort after normal recovery
(`scripts/recovery/emergency-recovery.ps1`) has failed.

> Restore is **destructive**: it overwrites live data. Always (1) verify the
> backup's integrity first, (2) take a fresh pre-restore snapshot of the current
> state so the restore itself is reversible, (3) stop the service's consumers,
> restore, restart, and verify.

All procedures below were validated with a live restore drill on 2026-08-01
(agent-bridge-db + mattermost-db restored into a scratch Postgres, row-for-row
faithful; every archive integrity-checked).

---

## 1. Backup inventory

Backups land in repo-root `./backups/<service>/`, newest-per-service, with a
`.sha256` sentinel next to each artifact. A weekly two-slot NAS mirror
(`scripts/backup/backup-to-nas.ps1`) copies all of `./backups/` to
`\\<nas>\backups\...\slot-A|B`. The same run copies the cold archives in
`./backup/` (singular - the orphan-volume tars, the May-2025 Open WebUI volumes,
the OWUI model exports, i.e. every file in a SUBDIRECTORY of `./backup/`; any
junction or symbolic link anywhere under `./backup/` fails the run instead of being
followed) to a sibling folder, `\\<nas>\backups\...\archive`, as sha256-verified
copies: never mirrored and a complete copy never replaced (the one exception: a complete NAS copy that ANOTHER tool stamped inside robocopy's unfinished-copy window (1979-12-31 to 1980-01-02 UTC) cannot be told from an unfinished one and is re-copied from the local file), so an
archive deleted or damaged on D: stays intact on the NAS (§8).

| Service | Type | Artifact | Restore tool |
|---|---|---|---|
| agent-bridge-db | Postgres 15 custom dump | `agent-bridge-db-*.dump` | `pg_restore` |
| mattermost-db | Postgres 15 custom dump | `mattermost-db-*.dump` | `pg_restore` |
| openbrain-db | Postgres 16 (pgvector) custom dump | `openbrain-*.dump` | `pg_restore` |
| llm-gateway | Postgres 16 SQL, gzipped | `llm-gateway-*.sql.gz` | `psql` |
| open-notebook | SurrealDB export + notebook tar | `surreal-*.surql.gz` + `notebook-data-*.tar.gz` | `surreal import` + tar |
| openwebui | volume tar | `openwebui-backup-*.tar.gz` | tar extract |
| mnemory | volume tar | `mnemory-backup-*.tar.gz` | tar extract (see `backup/mnemory-restore.sh`) |
| little-coder | volume tar (5 expertise vols) | `little-coder-backup-*.tar.gz` | tar extract |
| openbrain-wiki | volume tar (git tree + assets) | `openbrain-wiki-*.tar.gz` | tar extract |
| tailscale | state-dir tar | `tailscale-*.tar.gz` | tar extract |
| lm-models | llama.cpp model store tar (~120 GB) | `lm-models-*.tar.gz` | tar extract |
| caddy / authelia | volume tar (portal) | `caddy-*` / `authelia-*.tar.gz` | tar extract |

`smolcrawl` is retired and has no backup sidecar any more; an old `smolcrawl-*.tar.gz`
archive has nothing to restore into (see `restore-from-snapshot.md`).

---

## 2. Step 0 — pick and verify the backup (ALWAYS FIRST)

```bash
cd <your ai-stack checkout>
SVC=agent-bridge-db                       # the service to restore
F=$(ls -1t backups/$SVC/*.dump backups/$SVC/*.tar.gz backups/$SVC/*.sql.gz 2>/dev/null | head -1)
echo "restoring from: $F"

# integrity: the sha256 sentinel path is the in-container path, so verify inside
# the service's *-backup container where /backups matches:
docker exec ${SVC}-backup sh -c "cd /backups && sha256sum -c \"$(basename "$F").sha256\""
# tar archives can also be checked with: gzip -t "$F"
```

If the newest is suspect (e.g. captured *after* the corruption), pick an older
one — `ls -1t backups/$SVC/` lists newest first. **Do not proceed on a failed
integrity check.**

---

## 3. Step 1 — pre-restore safety snapshot (make it reversible)

Capture the *current* (broken) state so a wrong restore can be undone. Cheap for
DBs; for the biggest volumes (lm-models ~120 GB) weigh the disk cost.

```bash
# Postgres (agent-bridge-db / mattermost-db / openbrain-db): trigger the sidecar's own dump
docker exec ${SVC}-backup sh -c 'sh /scripts/backup.sh'     # writes a fresh dated dump

# Volume services: tar the current volume out-of-band
docker run --rm -v <volume>:/data:ro -v "$PWD/backups/pre-restore":/out alpine \
  sh -c 'tar czf /out/'"$SVC"'-prerestore-$(date -u +%Y%m%dT%H%M%SZ).tar.gz -C /data .'
```

---

## 4. Postgres restore (agent-bridge-db, mattermost-db, openbrain-db)

Custom-format dumps restore with `pg_restore --clean --if-exists` (drops and
recreates objects). Do it while the app consumers are stopped so nothing writes
mid-restore.

```bash
# creds live in the *-backup container env (PGUSER / PGDATABASE / PGHOST)
docker inspect ${SVC}-backup --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^PG(USER|DATABASE|HOST)='

# 1) stop consumers (examples):
#    agent-bridge-db -> agent-bridge, ao-worker-1, ao-worker-2, ao-ot-1, ao-ot-2
#    mattermost-db   -> mattermost
#    openbrain-db    -> the openbrain-* app containers that use it
docker compose stop <consumers...>          # main stack
# (agent-org services: docker compose -f agent-org/docker/docker-compose.yml stop <...>)

# 2) restore INTO THE LIVE DB. Copy the dump into the DB container, then pg_restore.
F=$(ls -1t backups/$SVC/*.dump | head -1)
docker cp "$F" ${SVC}:/tmp/restore.dump
docker exec ${SVC} sh -c 'pg_restore -U <PGUSER> -d <PGDATABASE> --clean --if-exists --no-owner --no-acl /tmp/restore.dump; echo rc=$?'
docker exec ${SVC} rm -f /tmp/restore.dump

# 3) restart consumers, then verify
docker compose start <consumers...>
docker exec ${SVC} psql -U <PGUSER> -d <PGDATABASE> -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
```

**Drill-verified (2026-08-01):** restoring into a *scratch* pg15 reproduced live
counts exactly (agent-bridge 27 tables / events 34766; mattermost 92 tables).
To rehearse non-destructively, restore into a throwaway container first:
`docker run -d --name drill -e POSTGRES_PASSWORD=x -v "$PWD/backups:/backups:ro" postgres:15-alpine`,
`createdb`, `pg_restore`, compare, `docker rm -f drill`.

---

## 5. llm-gateway restore (SQL gzip)

Non-authoritative spend-log telemetry; restore is rarely needed.

```bash
F=$(ls -1t backups/llm-gateway/*.sql.gz | head -1)
docker exec llm-gateway-db sh -c 'psql -U litellm -d litellm' < <(gunzip -c "$F")
# or: gunzip -c "$F" | docker exec -i llm-gateway-db psql -U litellm -d litellm
```

---

## 6. SurrealDB restore (open-notebook)

Two-phase: the SurrealDB export **and** the notebook-data tar.

```bash
SQ=$(ls -1t backups/open-notebook/surreal-*.surql.gz | head -1)
docker compose stop open_notebook
gunzip -c "$SQ" > /tmp/on.surql
docker cp /tmp/on.surql surrealdb:/tmp/on.surql
docker exec surrealdb sh -c 'surreal import --conn http://localhost:8000 --user root --pass root --ns open_notebook --db open_notebook /tmp/on.surql'
# then restore the notebook-data tar into its bind mount (see §7 pattern), and:
docker compose start open_notebook
```

---

## 7. Volume tar restore (openwebui, mnemory, little-coder, wiki, tailscale, lm-models, caddy, authelia)

General pattern: stop consumers, wipe the volume, extract the tar, restart.

```bash
SVC=mnemory ; VOL=mnemory-data ; CONSUMER=mnemory
F=$(ls -1t backups/$SVC/*.tar.gz | head -1)
docker compose stop $CONSUMER
docker run --rm -v $VOL:/data -v "$PWD/backups/$SVC":/b:ro alpine \
  sh -c 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf /b/'"$(basename "$F")"' -C /data'
docker compose start $CONSUMER
```

**Service-specific care:**
- **openwebui** — never restart `openwebui` alone; it shares a network namespace
  with `tailscale`. Order: bring OWUI up, then tailscale (see the
  `openwebui-tailscale-netns-restart` note). Volume: `openwebui-data`.
- **lm-models** — the tar is the `LM_MODELS_DIR` bind mount (set in `inference/.env`), not
  a named volume; extract back to that host path. ~120 GB — ensure free space.
- **openbrain-wiki** — tar is a git working tree; `wiki-assets` (uploaded
  binaries) is a *separate* volume captured in the same run.
- **tailscale** — restoring state avoids re-authenticating the device to the
  tailnet; bring OWUI+tailscale up together per the netns note.

---

## 8. If local `./backups/` is gone — pull from the NAS

The weekly mirror keeps two slots (`slot-A` even ISO weeks, `slot-B` odd) on
`\\<nas>\backups\...`. Copy the needed artifact + its `.sha256` back
into `./backups/<service>/`, re-verify integrity (§2), then follow the matching
procedure. Prefer the newer slot unless it is the corrupted set.

The cold archives from `./backup/` are NOT in the slots: they are in the
`archive` folder next to them (`\\<nas>\backups\ai-stack\archive\<dir>\`),
copied one file at a time (temp name, sha256 check, rename) and never replaced or
purged (the one exception: a complete NAS copy that ANOTHER tool stamped inside robocopy's unfinished-copy window (1979-12-31 to 1980-01-02 UTC) cannot be told from an unfinished one and is re-copied from the local file). If a complete NAS copy and the local file differ, the weekly run FAILS
with an `[ERROR] archive: <STATUS> <file> ...` line in `logs/nas-sync-*.log`, an
alert, and no completion marker (`nas-offsite` goes red). Nothing on the NAS is
overwritten in any of them. What each means and what to do:

| status | means | do |
|---|---|---|
| `FAIL-LOCAL` | the LOCAL file contradicts the checksum recorded beside it (its `SHA256SUMS` entry or `<file>.sha256`), or those two records disagree with each other; it was not copied. THE RECORD ITSELF CAN BE THE BAD PART | read `Trust:` - `NAS` = the NAS copy matches the record: restore the local file from it; `RECORD` = the local file and the NAS copy agree with each other but not with the record, or the two records disagree: fix the recorded checksum (re-derive it from a copy you trust), the next run then copies normally; `NONE ON NAS` = never archived: the local file may be damaged - or the record is wrong: check the record against another copy before recovering; `NEITHER` = local, NAS and record all differ: investigate, starting with the record |
| `MISMATCH` | the NAS copy is complete but differs from the local file | `Trust: LOCAL` = the local file matches its recorded checksum, the NAS copy is damaged: after checking, move the NAS copy aside and let the next run (or `copy-archives-to-nas.ps1`) copy it again; `Trust: UNKNOWN` = no recorded checksum: compare both by hand and decide |
| `FAIL-COPY` | this one file could not be written, verified or renamed (share full or read-only, network drop, the local file locked or unreadable, another run wrote the file meanwhile, or a directory / junction sits at the file's name or its `.cf-partial` temp name - never written through), or `(pass)`: the pass itself failed, e.g. an archive folder under `./backup/` could not be listed (access denied) or a junction / symbolic link was found under `./backup/` (the line names it; replace it with the real folder or file) | read the reason in the line; fix the cause; the next run retries. Our temp file is removed; if it could not be, the line says `temp ... could not be removed` - delete that `.cf-partial` by hand. The other files of the pass are still processed |

**What the archive copy protects against, and what it does not.** It refuses, loudly (an
`[ERROR]`, an alert, no completion marker), the ordinary conditions under `./backup/`: junctions
and symbolic links at any depth, folders it cannot read, interrupted or stale copies, locked
files, and missing, wrong or conflicting checksum records; hidden files and folders are archived
like any other. It does NOT defend against someone actively rearranging `./backup/` while a run is
in progress (for example swapping a folder for a junction between the listing and the copy), nor
against the project folder being reached through a link in its parent path - a person with that
write access can change these scripts as well. Such a swap does not stay silent: the next run
compares every archived file with its local source and reports a difference as `MISMATCH`.

Where a directory carries a
`SHA256SUMS` (or a `.sha256` per file) verify against it after copying back.
`scripts/backup/copy-archives-to-nas.ps1 -Destination <archive folder> -VerifyOnly`
re-hashes the local and NAS copies of the default archive directories and says
VERIFIED / ABSENT / MISMATCH per file without writing anything.

---

## 8b. The NAS sync stopped working

**Symptom:** the newest `logs/nas-sync-*.log` ends in an `[ERROR]`, or the slot
timestamps on the NAS stop advancing. Since 2026-09-13 `check_backups.py` also
raises a `nas-offsite` row in the daily `#sysadmin` post for both cases.

**By far the most likely cause is an expired password.** Synology expires the
`backup-user` account on a schedule. It did so between 2026-08-30 and
2026-09-06, and the sync then missed two weekly runs.

```
net use: The password of this user has expired.
net use: System error 2242 has occurred.
```

**Fix (5 minutes):**

1. Set a new password for `backup-user` in DSM → Control Panel → User.
2. Put it in the gitignored `.env` at the repo root:
   ```
   NAS_BACKUP_USER=backup-user
   NAS_BACKUP_PASSWORD=<the new one>
   ```
   The script reads `.env` **first**, so this alone fixes the next run.
3. Keep the DPAPI fallback in step:
   `powershell -File scripts/backup/set-nas-credential.ps1 -FromEnv`
4. Re-run it now rather than waiting a week:
   `powershell -File scripts/backup/backup-to-nas.ps1 -NasUncRoot "\\<nas>\backups\ai-stack\portal"`

**There is no hardcoded password to hunt for.** Before 2026-09-13 the only copy
lived DPAPI-encrypted in `secrets/nas-backup-vault.dat` — unfindable by grep,
which is exactly why a rotation turned into an investigation. `.env` is now the
front door; the vault is the fallback.

### Other failures, by name

| net use error | Meaning | Fix |
|---|---|---|
| **2242** | password expired | above |
| **1219** | another session to the same NAS under different credentials — typically an operator's mapped drive (`M:`) | the script opens its session by **IP** to avoid this; if it still fires, `net use` and disconnect the clashing session, or pass the IP as `-NasUncRoot` |
| **1326** | wrong password, or the account is disabled/locked | check DSM |
| **53** | share missing / NAS offline / SMB disabled | `Test-NetConnection <nas> -Port 445` |

**Why by IP.** Windows keys SMB sessions by *server name* and allows one
credential set per name. With the NAS mapped interactively, a hostname session
as `backup-user` fails with 1219. Connecting by IP gives the backup its own
session identity, and the two coexist. `-NoIpResolve` restores hostname
behaviour. The session and the robocopy destination must use the **same**
spelling, or a second session opens and 1219 returns.

### Do not trust a green alerter

The failure alert goes to the portal-alerter **and** to Mattermost **and** to
Telegram, and the log records which channels answered. This is deliberate: the
alerter's Gmail refresh token is dead, so it returns 500 to every `/alert` while
its `/health` still answers `ready: true` with HTTP 200 — the healthcheck only
proves the listener is up, not that mail can be sent. Both 2026-09 backup
failures alerted correctly into that void.

**`portal-alerter` and the daily digest are different services with different
OAuth clients.** `openbrain-digest` has been sending fine throughout. A working
digest is *not* evidence that the alerter works, and re-consenting one does
nothing for the other.

To check the alerter specifically:

```powershell
docker logs --timestamps portal-alerter | Select-String "Token refresh failed" | Select-Object -Last 3
```

If present, its refresh token returns `invalid_grant` and must be re-minted:

```powershell
deno run --allow-net --allow-read --allow-write --allow-env portal/config/alerter/setup-token.ts
docker compose -f portal/docker-compose.yml up -d --force-recreate portal-alerter
```

**Do not date the outage from the container log.** The log begins when the
container was created, not when the token broke. The honest last-known-good is
`expiry_date` inside `secrets/google/portal-alerter/token.json` — `alerter.ts`
rewrites that file on every successful refresh, so its timestamp is the last time
mail actually worked. On 2026-09-13 the log implied 23 days and the token file
said 14 weeks.

Likewise ignore the 0-byte `portal/config/alerter/token.json` / `credentials.json`:
those are Docker's bind-mount placeholders, created because the compose file
mounts `./config/alerter:/app` and then layers the real files from `secrets/`
over it. They are not the credentials and never were.

---

## 9. After any restore

- Verify container health: `docker ps` / the sysadmin `stack_health` tool.
- Spot-check the restored data (row counts, a known record, the app UI).
- Keep the pre-restore snapshot (§3) until you've confirmed the restore is good.

---

## 10. Backup intervals and the maintenance rotation

(Moved here from the root README.)

Every stateful store has exactly one backup sidecar **in its own plane
project**, writing verified artifacts (plus sha256 sentinels) to
`./backups/<service>/`, mirrored WEEKLY to the NAS (Sundays at 04:00 -
`scripts/backup/install-nas-backup-task.ps1`, its `New-ScheduledTaskTrigger
-Weekly -DaysOfWeek Sunday -At 4am`). Two scheduler idioms: **sleep-loop** for
interval tars (once at container start, then every `BACKUP_INTERVAL` seconds)
and **supercronic** for cron-timed DB dumps.

**Changing a backup interval**: set the variable in that plane's `.env` and
recreate that one sidecar (`docker compose -f <plane>/docker-compose.yml up -d
<sidecar>`). All intervals are seconds; the defaults live in the compose files:

| Variable | Sidecar (plane) | Default |
|---|---|---|
| `MNEMORY_BACKUP_INTERVAL` | mnemory-backup (memory) | 86400 (daily) |
| `OPENWEBUI_BACKUP_INTERVAL` | openwebui-backup (frontend) | 86400 |
| `TAILSCALE_BACKUP_INTERVAL` | tailscale-backup (frontend) | 86400 |
| `LITTLE_CODER_BACKUP_INTERVAL` | little-coder-backup (coder) | 86400 |
| `LM_MODELS_BACKUP_INTERVAL` | lm-models-backup (inference) | 604800 (weekly; empty = disabled) |
| `OPENBRAIN_WIKI_BACKUP_INTERVAL` | openbrain-wiki-backup (ob1; set in `OB1/docker/.env`) | 86400 |
| *(not an interval)* | llm-gateway-backup (inference) sleeps 86400 s, hard-coded in its entrypoint; `caddy-backup` / `authelia-backup` (portal) are supercronic on `*_BACKUP_CRON`, default `0 3 * * *`; `openbrain-db-backup` and `open-notebook-backup` likewise, defaulting to 02:00 and 02:20 UTC | |

**Disk rotation** is autonomous: the `AI-Stack Weekly Maintenance` scheduled
task (Sundays 03:15) runs `scripts/maintenance/weekly-maintenance.ps1` - a safe
docker reclaim (dangling images and build cache; **never** a volume prune),
then the elevated vhdx compaction task, then a post to Mattermost `#sysadmin`.
Re-register after edits with `weekly-maintenance.ps1 -Register`.

Restore procedures: `documentation/runbooks/restore-from-snapshot.md` (per
store) and `scripts/backup/restore-from-snapshot.ps1` (orchestrated DR).
Adding or changing a service? Work through
[`documentation/runbooks/SERVICE-LIFECYCLE.md`](SERVICE-LIFECYCLE.md)
- it is what keeps backups, recovery, health probes and the sysadmin plane
telling the truth.
