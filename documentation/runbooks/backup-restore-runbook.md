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
`\\PolyshDesignNAS\backups\...\slot-A|B`.

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
| smolcrawl | volume tar | `smolcrawl-*.tar.gz` | tar extract |
| tailscale | state-dir tar | `tailscale-*.tar.gz` | tar extract |
| lm-models | llama.cpp model store tar (~120 GB) | `lm-models-*.tar.gz` | tar extract |
| caddy / authelia | volume tar (portal) | `caddy-*` / `authelia-*.tar.gz` | tar extract |

---

## 2. Step 0 — pick and verify the backup (ALWAYS FIRST)

```bash
cd "D:/Open WebUI/ai-stack"
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
docker run --rm -v <volume>:/data:ro -v "D:/Open WebUI/ai-stack/backups/pre-restore":/out alpine \
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

## 7. Volume tar restore (openwebui, mnemory, little-coder, wiki, smolcrawl, tailscale, lm-models, caddy, authelia)

General pattern: stop consumers, wipe the volume, extract the tar, restart.

```bash
SVC=mnemory ; VOL=mnemory-data ; CONSUMER=mnemory
F=$(ls -1t backups/$SVC/*.tar.gz | head -1)
docker compose stop $CONSUMER
docker run --rm -v $VOL:/data -v "D:/Open WebUI/ai-stack/backups/$SVC":/b:ro alpine \
  sh -c 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf /b/'"$(basename "$F")"' -C /data'
docker compose start $CONSUMER
```

**Service-specific care:**
- **openwebui** — never restart `openwebui` alone; it shares a network namespace
  with `tailscale`. Order: bring OWUI up, then tailscale (see the
  `openwebui-tailscale-netns-restart` note). Volume: `openwebui-data`.
- **lm-models** — the tar is the `C:\Users\yamao\.lmstudio\models` bind mount, not
  a named volume; extract back to that host path. ~120 GB — ensure free space.
- **openbrain-wiki** — tar is a git working tree; `wiki-assets` (uploaded
  binaries) is a *separate* volume captured in the same run.
- **tailscale** — restoring state avoids re-authenticating the device to the
  tailnet; bring OWUI+tailscale up together per the netns note.

---

## 8. If local `./backups/` is gone — pull from the NAS

The weekly mirror keeps two slots (`slot-A` even ISO weeks, `slot-B` odd) on
`\\PolyshDesignNAS\backups\...`. Copy the needed artifact + its `.sha256` back
into `./backups/<service>/`, re-verify integrity (§2), then follow the matching
procedure. Prefer the newer slot unless it is the corrupted set.

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
   `powershell -File scripts/backup/backup-to-nas.ps1 -NasUncRoot "\\PolyshDesignNAS\backups\ai-stack\portal"`

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
alerter's Gmail OAuth refresh token died on 2026-08-21, after which it returned
500 to every `/alert` while its `/health` still answered `ready: true` with HTTP
200 — its healthcheck only proves the listener is up, not that mail can be sent.
Both 2026-09 backup failures alerted correctly into that void.

If `docker logs portal-alerter` shows `Token refresh failed: Bad Request`, the
OAuth grant needs re-consenting in Google Cloud (publish the app out of Testing
mode — a Testing-mode refresh token expires after 7 days).

---

## 9. After any restore

- Verify container health: `docker ps` / the sysadmin `stack_health` tool.
- Spot-check the restored data (row counts, a known record, the app UI).
- Keep the pre-restore snapshot (§3) until you've confirmed the restore is good.
