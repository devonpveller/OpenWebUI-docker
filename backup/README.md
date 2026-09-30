# backup/ - a shared module, not a plane

The scripts the stack's backup sidecars run, and the one shared backup image.
This directory stays at the repo root because **six planes consume it**, so it
is not any one plane's internals. `stack.manifest.toml` declares it as
`[modules.backup]` with the same consumer list, and
`scripts/stack/test_stack.py` fails if a plane's compose file starts or stops
referencing `../backup` without that list changing too.

| Plane | How it consumes `backup/` |
|---|---|
| frontend | bind mount: `openwebui-backup.sh`, `generic-tar-backup.sh` (tailscale-backup) |
| inference | bind mount: `llm-gateway-backup.sh`, `generic-tar-backup.sh` (`inference/compose/backups.yml`) |
| memory | bind mount: `mnemory-backup.sh` |
| coder | bind mount: `little-coder-backup.sh` |
| portal | build context `../backup` (`Dockerfile`, for the caddy-backup and authelia-backup images); bind mount: `caddy-backup.sh`, `authelia-backup.sh` |
| agent-org | bind mount: `pg-backup.sh`, `generic-tar-backup.sh` |

Each sidecar mounts its script read-only at `/scripts/backup.sh`. OB1 is not a
consumer: its backup sidecars and their scripts live in `OB1/docker/backup/`.

`openwebui-restore.sh` and `mnemory-restore.sh` are run by hand; the restore
procedure is `documentation/runbooks/backup-restore-runbook.md`. The artifacts
the sidecars write go to `backups/` (plural), not here.

Only the files named above are tracked: `.gitignore` ignores `backup/*` and
re-includes each script, the `Dockerfile`, its `.dockerignore` and this README
by name. The `.dockerignore` is `*`: the image copies nothing from here, and on a
live host this directory also holds old exports and archives (gitignored), which the
legacy builder or a broad `COPY` would otherwise send to the daemon. A new script
needs its own `!backup/<name>` line, or a fresh clone will not have it, and
a bind mount of a file that does not exist makes Docker create an empty
DIRECTORY in its place, so the sidecar fails without saying so.
