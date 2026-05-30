# Backup conventions for ai-stack + OB1

When a new service with persistent state is added to this workspace, the
**same PR** that adds the service should also add its backup container.
This doc is the convention every future addition follows so the snapshot
slowly grows with the stack — no service silently goes unbackuped.

If you're adding a new service, you should ALSO run
[`scripts/check-backup-coverage.ps1`](../scripts/check-backup-coverage.ps1)
to confirm there's no other gap you missed.

---

## Decision tree — pick the backup shape

| Source data type | Pattern | Reference |
|---|---|---|
| Plain files / append-only logs / config / immutable git objects | **Generic tar** — `backup/generic-tar-backup.sh` + alpine sidecar | smolcrawl-backup, tailscale-backup, openbrain-wiki-backup |
| **PostgreSQL** (any version, any extensions including pgvector) | **`pg_dump -Fc`** — custom Dockerfile based on postgres:N-alpine | openbrain-db-backup |
| **SurrealDB** (RocksDB store) | **`surreal export`** logical dump — custom Dockerfile based on debian-slim with the surreal binary copied in | open-notebook-backup |
| **SQLite** under active write | **`sqlite3 .backup`** command (Online Backup API) — NOT a hot tar | (none yet — Authelia's SQLite is currently tar'd via authelia-backup; it works because SQLite WAL mode tolerates concurrent reads) |
| **Redis** with append-only-file persistence | `BGSAVE` + tar the resulting dump.rdb | (none — search-redis is intentionally in-memory only) |
| Any database NOT listed above | Treat as opaque — STOP the container before tarring its data dir | — |

If you can't find your case here, default to **stop-the-container + tar**.
It's the safest. Document why and add a row above.

---

## Required ingredients for a new backup container

Every backup container, regardless of shape, must have:

1. **A non-root UID** matching the source data owner, OR root inside the
   container if the source is root-owned. (The plan §2 hardening floor
   asked for non-root; we deviate where the source is root-owned because
   chown-via-init isn't worth the complexity.) Use UIDs 10000+ to avoid
   colliding with standard service UIDs.

2. **Compose-level hardening**: `cap_drop: [ALL]` (sometimes `cap_add`
   targeted), `security_opt: [no-new-privileges:true]`, `read_only: true`
   where the entrypoint allows, `tmpfs /tmp` if the script stages work,
   `pids:` limit in `deploy.resources.limits` (compose v2.x), CPU/memory
   bounds.

3. **Output path**: `./backups/<service>/<service>-<UTC-timestamp>.<ext>`
   with a matching `.sha256` sentinel beside each archive. The NAS sync
   script ([`scripts/backup-to-nas.ps1`](../scripts/backup-to-nas.ps1))
   automatically mirrors every subdir of `./backups/` — no NAS-side
   change needed.

4. **Retention** via `ls -1t ... | tail -n +$((RETAIN_COUNT + 1))` (keep N
   most recent archives + sentinels), where N comes from a configurable
   env var `<SERVICE>_BACKUP_RETAIN_COUNT` (default 2). Count-based, not
   days-based, so a failed run for 3 days doesn't leave you with zero
   archives if your old retention was tight.

5. **Health gate (precheck)**: before doing anything destructive, the
   script must check that the source data is sane AND, where applicable,
   that the source service is reachable. Skip cleanly (`exit 0` + log
   message) on precheck fail -- never capture a torn snapshot from a
   crashing service. The two probes available:
   - `nc -z -w 5 <host> <port>` if the backup container shares a network
     with the source. The host:port comes from a configurable env var
     `HEALTH_TCP` (e.g. `openwebui:8080`); empty value = skip the probe.
   - Data-existence: directory exists + is non-empty. Always required.

6. **Schedule** via `BACKUP_CRON` env var (cron format, UTC) — operator
   can override in `.env`. Schedule conventions:
   - **02:00 UTC** — large or DB-dump jobs (longest run window)
   - **02:15 / 02:20 / 02:25 UTC** — small tar jobs
   - **03:00 UTC** — portal volumes (after everything else)
   - **04:00 UTC Sunday** — NAS sync (after all nightlies)
   - **Custom (weekly Sunday 01:00 UTC)** — multi-GB jobs that don't need daily cadence (lm-models)

7. **Entry in [restore-from-snapshot.md](./restore-from-snapshot.md)**
   describing how to restore from one of the produced archives.

8. **Pre-created bind-mount target**: the `./backups/<service>/` directory
   must exist before the container starts (Docker creates it as root if
   missing, which then blocks a non-root container from writing). The
   easiest way is to add it to the
   [`scripts/check-backup-coverage.ps1`](../scripts/check-backup-coverage.ps1)
   pre-flight — that script auto-creates missing dirs.

---

## Pattern template (the easy case: hot-tar)

For a new service whose data is "files in a volume / bind mount, no
database under active writes," copy this block into
[`docker-compose.yml`](../docker-compose.yml) (adjust the marked lines):

```yaml
  <SERVICE>-backup:
    image: alpine:3.21                          # <-- if root-owned source; else use backup/Dockerfile pattern
    container_name: <SERVICE>-backup
    volumes:
      - <SERVICE>-data:/data:ro                 # <-- source volume or bind mount, READ-ONLY
      - ./backups/<SERVICE>:/backups            # <-- destination (auto-created by check-backup-coverage)
      - ./backup/generic-tar-backup.sh:/scripts/backup.sh:ro
    environment:
      - DATA_DIR=/data
      - BACKUP_DIR=/backups
      - PREFIX=<service>                        # <-- prefix for filename
      - RETAIN_COUNT=${<SERVICE>_BACKUP_RETAIN_COUNT:-2}
      - HEALTH_TCP=<source-service-host>:<source-service-port>   # <-- service to probe
      - BACKUP_CRON=${<SERVICE>_BACKUP_CRON:-30 2 * * *}   # <-- pick a slot
      - TZ=UTC
    entrypoint: /bin/sh
    command:
      - -c
      - |
        chmod +x /scripts/backup.sh
        echo "$${BACKUP_CRON} sh /scripts/backup.sh >> /var/log/backup.log 2>&1" > /etc/crontabs/root
        echo "[$(date -u +%FT%TZ)] <SERVICE>-backup scheduler started (cron: $${BACKUP_CRON}, retain: $${RETAIN_COUNT} most recent)"
        crond -f -l 2
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
```

Then:

1. Add `<SERVICE>_BACKUP_RETAIN_COUNT=2` and `<SERVICE>_BACKUP_CRON=` to
   [`.env.example`](../.env.example).
2. Add `./backups/<SERVICE>/` to the inventory in
   [`scripts/check-backup-coverage.ps1`](../scripts/check-backup-coverage.ps1).
3. Add a restore section to [`restore-from-snapshot.md`](./restore-from-snapshot.md).
4. `docker compose build` (if applicable) and `docker compose up -d <SERVICE>-backup`.
5. Smoke-test: `docker exec <SERVICE>-backup sh /scripts/backup.sh` —
   verify a `<service>-<ts>.tar.gz` + `.sha256` appears in
   `./backups/<SERVICE>/`, and `docker exec <SERVICE>-backup sh -c "cd
   /backups && sha256sum -c <service>-*.sha256"` returns OK.

That's it. The NAS sync picks it up automatically on the next Sunday.

---

## Pattern template (the database case)

For a database, you write a service-specific dump script (e.g.,
`backup/<service>-backup.sh`) that connects to the live DB and produces
a logical dump. The container's image needs the DB client binary; build
a `backup/Dockerfile.<service>` based on either the official DB image
or a minimal base with the binary copied in.

References:
- `backup/Dockerfile.postgres` + `backup/openbrain-db-backup.sh` — pg_dump
- `backup/Dockerfile.surreal` + `backup/open-notebook-backup.sh` — surreal export

Other ingredients (UIDs, schedule, retention, sentinel, restore doc
entry) are the same as the hot-tar case.

---

## NAS sync — nothing to do

[`scripts/backup-to-nas.ps1`](../scripts/backup-to-nas.ps1) mirrors all
of `./backups/` to the NAS on Sundays at 04:00 UTC, alternating between
`slot-A/` and `slot-B/` weekly. **New `./backups/<service>/` directories
are picked up automatically** — Robocopy's `/MIR` reflects everything
under the source tree. No code change to the sync needed when you add a
new backup.

---

## Verification on every PR that adds a backup

```powershell
# 1. Compose validates
docker compose config --quiet

# 2. Build (if you added a custom Dockerfile)
docker compose build <SERVICE>-backup

# 3. First-run smoke test
docker compose up -d <SERVICE>-backup
docker exec <SERVICE>-backup sh /scripts/backup.sh

# 4. Sentinel verifies
docker exec <SERVICE>-backup sh -c "cd /backups && sha256sum -c <service>-*.sha256 | tail -1"

# 5. Coverage check confirms zero gaps
.\scripts\check-backup-coverage.ps1
```

If any of the five fails, the backup isn't ready to merge.
