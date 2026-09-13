# Off-site NAS backup silently stopped for two weeks — 2026-09-13

Operator found it by looking at the NAS by hand: slot-B last written 2026-08-30,
slot-A 2026-08-23, and no `#sysadmin` alert about either. This records what was
measured and what was changed.

Anchor: `nasbackup`. Artifact: the `scripts/backup/` + `check_backups.py` patches
this note accompanies.

---

## What actually happened

Four independent things had to line up, and they did:

| # | Defect | First seen | Evidence |
|---|---|---|---|
| 1 | portal-alerter's Gmail OAuth refresh token is dead → every `/alert` 500s | **no later than 2026-06-05**, see below | `docker logs --timestamps portal-alerter` → 56 × `Token refresh failed: Bad Request`; live refresh returns `invalid_grant` |
| 2 | Its `/health` still answers `ready:true` + HTTP 200 | — | [alerter.ts:662](../../config/alerter/alerter.ts#L662); healthcheck is `wget -q -O /dev/null …/health`, which discards the body that *does* carry `last_error` |
| 3 | NAS `backup-user` password expired | between 08-30 and 09-06 | `logs/nas-sync-2026-09-06.log`: `The password of this user has expired. System error 2242` |
| 4 | `check_backups.py` never looked at the off-site layer | since it was written | `grep -i "nas\|slot-" scripts/sysadmin-mcp/check_backups.py` → no matches |

So: the sync failed, correctly detected its own failure, correctly tried to alert
— into a channel that had been dead for 16 days — logged one `[WARN]`, and
exited. The daily backup watchdog ran throughout and exited 0, because local
`./backups/` freshness was perfect. **Local freshness says nothing about
off-site safety, and nothing in the system knew that.**

### Timeline, from the logs

| run | ISO week | slot | outcome |
|---|---|---|---|
| 2026-08-23 | 34 | slot-A | OK |
| 2026-08-30 | 35 | slot-B | OK — last good |
| 2026-09-06 | 36 | slot-A | FAILED, error 2242, alert 500 |
| 2026-09-13 | 37 | slot-B | FAILED, error 2242, alert 500 |

Exactly matches what the operator saw on the NAS. Successful logs are ~25 KB;
both failed logs are 1455 bytes — the size alone distinguishes them.

## There was never a hardcoded password

The operator's working assumption was a hardcoded credential. There isn't one.
`scripts/backup/set-nas-credential.ps1` DPAPI-encrypts (LocalMachine scope) into
`secrets/nas-backup-vault.dat` — 262 bytes of ciphertext, last written
**2025-05-29**, i.e. set once and never rotated. That design is *more* secure
than a file, and it is why grep found nothing.

The real defect is discoverability: a credential nobody can find is a credential
nobody rotates, and the rotation path (`Get-Credential`) could not run
non-interactively at all. Fixed by reading `.env` first (gitignored, where this
stack's other secrets already live) with the vault as fallback, plus
`set-nas-credential.ps1 -FromEnv`.

**This is a deliberate security trade-off**, made on the operator's instruction:
the password is now plaintext in `.env` rather than only DPAPI ciphertext. `.env`
is gitignored and the secret-guard pre-commit hook covers it, but anything that
can read the repo can now read the NAS password. The vault remains supported and
takes over whenever the `.env` keys are absent.

## Error 1219 — found while fixing, not pre-existing

With the password corrected, the run then failed with `System error 1219:
Multiple connections to a server … by the same user`. Cause: the operator had
`M: -> \\PolyshDesignNAS\PDNAS` connected while inspecting the NAS. Windows keys
SMB sessions by **server name** and allows one credential set per name, so an
interactive mapping blocks the backup's own `backup-user` session.

This is latent in the original design — it would fire any time someone had the
NAS mapped at 04:00 Sunday. Fixed by opening the session against the resolved
**IP**, which is a separate session identity. Session and robocopy destination
are rewritten together; using one spelling for the session and the other
reintroduces the same error.

Verified live: `net use \\192.168.1.247\backups /user:backup-user` returned exit
0 while `M:` stayed connected, and the slot listing read
`slot-A 8/1/2026`, `slot-B 8/30/2026 4:00:06 AM`.

## What this change does NOT fix

- **The portal-alerter is still dead.** Its refresh token returns `invalid_grant`.
  Re-consent with
  `deno run --allow-net --allow-read --allow-write --allow-env config/alerter/setup-token.ts`,
  then recreate the container. Operator action (browser consent). This work makes
  the alerter's death non-fatal by fanning out to Mattermost and Telegram, rather
  than repairing it.

  ### Correction: when it died, and what it is NOT (2026-09-13)

  An earlier revision of this note said the token "died 2026-08-21" and blamed the
  same 7-day Testing-mode expiry as `daily-digest-oauth-7day-expiry`. Both claims
  were wrong as stated, and the operator was right to push back — the daily digest
  has been working fine throughout.

  - **They are different services with different OAuth clients.** `openbrain-digest`
    has its own credentials and shows **zero** token errors; `portal-alerter` uses
    `secrets/google/portal-alerter/`, its own `client_id`. Fixing one does nothing
    for the other, and the digest working never implied the alerter was.
  - **2026-08-21 is when the CONTAINER was created**, not when the token broke:
    `docker inspect portal-alerter` → `Created: 2026-08-21T10:11:23Z`, eighteen
    seconds before the first logged failure at `10:11:41Z`. That is commit
    `9a0a737`, the Part K portal split. The log starts there because the container
    does; it says nothing about earlier.
  - **The real last-known-good is 2026-06-05.** `alerter.ts` writes the refreshed
    token back to `token.json` on every successful refresh
    ([alerter.ts:103](../../config/alerter/alerter.ts#L103)), and that file still
    carries `expiry_date: 2026-06-05T07:59:56Z` with an mtime to match. No
    successful refresh has happened since. The outage is potentially **14 weeks**,
    not 23 days.
  - Testing-mode expiry is now *plausible again* on those dates (Jun 5 + 7 days
    ≈ Jun 12) but is **not established**: `invalid_grant` covers revocation, a
    password change, and the per-client token cap equally. Check the app's
    publishing status in Google Cloud before assuming.
  - The 0-byte `config/alerter/token.json` and `credentials.json` dated Aug 21
    06:11 are **Docker's own bind-mount placeholders**, not a truncated token —
    `portal/docker-compose.yml` mounts `../config/alerter:/app` and then layers
    the two real files from `secrets/` on top, and Docker creates a missing mount
    target. Their timestamp matching the first failure to the minute is the
    container's creation, and it is a coincidence worth not chasing twice. Same
    trap as `observability-audit`: a bind-mount of a missing file is silently
    created.
- **`/health` still lies.** It returns `ready:true` regardless. Deliberately left
  alone: [alerter.ts:14](../../config/alerter/alerter.ts#L14) says the killswitch
  and `portal-status.ps1` consume it, so returning 503 when *mail* breaks could
  take the portal down because email broke. The honest fix is a separate,
  carefully-scoped change — it needs those two consumers audited first.
- **Every other `Send-PortalAlert` caller is still single-channel.**
  `scripts/lib/portal-alerter-client.ps1` has other users (watchers, tripwire,
  cron). They have the same "alerted into the void" exposure and were not
  touched. Worth its own anchor.
- **56 alerts were lost**, not queued. Whatever else tried to page between
  2026-08-21 and now never arrived, and there is no record of what they were
  beyond each caller's own log.

## Other findings, not acted on

- `secrets/nas-backup-vault.dat` was last written 2025-05-29, so the NAS password
  had gone ~15 months without rotation before Synology forced it.
- The `robocopy /MIR` to slot-B on 2026-09-13 removed **120 extra files,
  ~143 GB** — the Aug 25–30 generations that local retention had rotated out.
  Normal mirror behaviour, but it means a recovery slot loses intermediate
  generations on every run; slot-A is the only thing preserving an older set.
- `install-nas-backup-task.ps1` registers the task with the **hostname** UNC. It
  was not changed here, so the scheduled task still uses the hostname and relies
  on the in-script IP resolution rather than the argument.
