# Incident Response — Internet-Exposed Portal

**Scope:** breaches and credible-looking compromises of the internet-exposed portal slice (`cloudflared`, `caddy`, `authelia`, `authelia-watcher`, `integrity-tripwire`, `portal-alerter`, `portal-cron`, `caddy-backup`, `authelia-backup`).

**Out of scope:**
- Non-breach incidents (disk failure, container crash, tunnel outage) — handled by `scripts/recovery/emergency-recovery.ps1` and the recovery stack.
- Compromises of services NOT in the portal slice (OpenWebUI native, llama-cpp, OB1, mnemory, etc.) — those have their own concerns; this doc focuses on the gateway.

**Companion docs:**
- Plan: [implementation-guide/open-source authentication front ends for ai stack/plan-internet-exposed-front-end.md](implementation-guide/open-source%20authentication%20front%20ends%20for%20ai%20stack/plan-internet-exposed-front-end.md)
- Audit: [implementation-guide/open-source authentication front ends for ai stack/audit-plan-internet-exposed-front-end.md](implementation-guide/open-source%20authentication%20front%20ends%20for%20ai%20stack/audit-plan-internet-exposed-front-end.md)

---

## 1. Detection signals

All signals arrive via Gmail to the `DIGEST_TO` inbox. The watchers (`authelia-watcher`, `integrity-tripwire`) POST JSON to `portal-alerter:8080/alert`; the alerter sends the email.

**Subject-line convention:** `[<SEVERITY>] <event> <source_ip>` — e.g., `[HIGH] authentication.failed.burst 203.0.113.42`.

**Event taxonomy:**

| Event | Severity | Source | Means |
|---|---|---|---|
| `authentication.failed.burst` | high | `authelia-watcher` (Authelia log, >=5 fails / 5min, same IP) | Active credential-stuffing probe. Confirm: read the included log line; check whether the username tried exists. |
| `authentication.success.new_ip` | high | `authelia-watcher` (Authelia log, success from IP not in `known-ips.txt`) | Either you logged in from a new place, OR a stolen session/credential. Confirm: did you log in from that IP just now? If yes, append it to `config/watcher/known-ips.txt`. If no, **killswitch immediately**. |
| `regulation.ban` | medium | `authelia-watcher` (Authelia log, regulation event) | An account locked itself out (>=3 fails). Usually benign (typo) but watch for repeats from same source IP. |
| `credential.webauthn.change` | high | `authelia-watcher` (Authelia log, WebAuthn event) | A WebAuthn credential was added/removed. Confirm: did you do that just now? If no, **killswitch immediately**. |
| `credential.totp.change` | high | `authelia-watcher` (Authelia log, TOTP event) | Same as above for TOTP. |
| `authelia.config.reload` | medium | `authelia-watcher` (Authelia log, config_file_loaded) | Someone touched `configuration.yml` or `users_database.yml`. Confirm: did you edit it? If no, **killswitch immediately and check `integrity-tripwire`**. |
| `caddy.401.burst` | medium | `authelia-watcher` (Caddy log, >=10 × 401 / 1min, same IP) | Brute-force or auth probe pattern at the proxy layer. Often paired with `authentication.failed.burst`. |
| `config.drift` | critical | `integrity-tripwire` (nightly hash check) | One of `Caddyfile`, `configuration.yml`, `users_database.yml` was modified outside the operator's awareness. **Killswitch immediately** and check what changed. |
| `killswitch.fired` | critical | `breach-killswitch.ps1` itself | A final email emitted when the killswitch runs — guaranteed record before the alerter goes down. |

**Scheduled digest** (default daily 07:00 UTC, configurable via `PORTAL_DIGEST_CRON`): a single email summarizing the prior `DIGEST_WINDOW_HOURS` of traffic — request counts, top source IPs, route breakdown, auth summary, threat indicators, integrity status. Useful as a baseline; deviations from the usual pattern are worth investigating even without an instant alert firing.

**Audit-trail fallback:** every scheduled digest is also written to the alerter's `/reports` volume as `digest-latest.md` + timestamped copies. If Gmail is unreachable during an incident, `docker exec portal-alerter cat /reports/digest-latest.md` works.

---

## 2. Containment

**Goal:** stop the bleeding without destroying evidence. Tailnet access stays alive throughout — you can do all of this from a tailnet-connected device.

**Procedure:**

1. From the host (or a Tailscale-reachable RDP session):
   ```powershell
   cd "D:\Open WebUI\ai-stack"
   .\scripts\breach-killswitch.ps1
   ```
   The script:
   - Emits a final `killswitch.fired` email (the last alert before the alerter goes down)
   - Stops only the portal services (`docker compose --profile internet stop ...`)
   - Snapshots `caddy-access.log`, `authelia.log`, `authelia-db.sqlite3` to `./incident/<UTC-timestamp>/`
   - Rotates `AUTHELIA_JWT_SECRET` + `AUTHELIA_SESSION_SECRET` in `.env` (commenting old values for the IR record)
   - Prints recovery steps; does **NOT** auto-restart anything.

2. Confirm the tailnet path is still alive:
   ```powershell
   # From a different device on the tailnet:
   curl https://<tailnet-host>.ts.net/
   ```
   Expected: OpenWebUI's native login appears as today.

3. Confirm portal containers are stopped:
   ```powershell
   .\scripts\portal-status.ps1
   ```
   Expected: every portal container shows `[DOWN]` or `not present`; `openwebui` shows `[OK]`.

**Killswitch vs portal-off:** `portal-off.ps1` is for planned downtime — it stops containers and exits. The killswitch additionally emits a final alert, snapshots logs, and rotates secrets. **Use the killswitch when you suspect compromise.** Use portal-off when you're just stepping away.

---

## 3. Eradication

**Goal:** purge anything the attacker may have planted or learned. Order matters — rotate secrets before restoring data.

1. **Rotate Authelia secrets** (the killswitch already did this — verify in `.env`):
   - `AUTHELIA_JWT_SECRET`
   - `AUTHELIA_SESSION_SECRET`
   - `AUTHELIA_STORAGE_ENCRYPTION_KEY` — **only rotate this if you also plan to discard the SQLite DB**, since the DB is encrypted with it. Usually leave alone.

2. **Rotate the Cloudflare Tunnel token** if the tunnel container is suspected compromised:
   - Cloudflare Zero Trust → Tunnels → `ai-stack` → "Refresh token" or rotate via API
   - Paste the new value into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`
   - The old token is invalidated within minutes

3. **Regenerate Argon2 hashes** for all users in `users_database.yml`:
   ```powershell
   docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2 --password '<new-strong-password>'
   ```
   Replace the existing hashes. Verify each new password against haveibeenpwned.com first.

4. **Wipe WebAuthn / TOTP enrollments.** The authelia SQLite DB (`/data/db.sqlite3`) contains these. After restoring from yesterday's backup (next section), the operator re-enrolls all 2FA methods. Do this from a clean device, not the one that may have been used while the breach was active.

5. **Audit the portal-alerter's OAuth token.** If you suspect the alerter container itself was compromised:
   - Revoke at https://myaccount.google.com/permissions (revokes the refresh token for the `open-brain-email` OAuth client)
   - Delete `secrets/google/portal-alerter/token.json`
   - Re-run `config/alerter/setup-token.ts` on the host to mint a fresh token
   - **Coupling caveat (plan §6.9):** revoking the OAuth *client* (vs the token) ALSO breaks OB1's daily digest. Revoking just the token (delete the file, re-bootstrap) leaves the client intact and OB1 unaffected. Prefer the token-only path when possible.

6. **If the host is compromised, not just a container** — different playbook entirely. The portal scripts only address container-level compromise; a host compromise requires offline rebuild from images of known-good state. Out of scope for v1.

---

## 4. Recovery

**Goal:** bring the portal back online with the smallest possible window where attacker artifacts could have re-entered.

1. **Verify off-host snapshot of `./incident/<ts>/`** before any further mutation. Copy to S3/B2/USB/etc. The on-host copy is at risk if the host is also compromised.

2. **Restore `authelia-data` from YESTERDAY'S backup, not today's.** Today's backup may have captured attacker-planted state.
   ```powershell
   # Stop the (already-stopped) authelia container if not already
   docker compose --profile internet rm -f authelia
   # Restore the volume — the exact mechanics depend on your backup storage
   # but the contents go into the authelia-data Docker volume.
   # Generic pattern: extract tarball into a fresh volume, replace.
   ```
   Validate by extracting the backup and running `SELECT COUNT(*) FROM webauthn_credentials;` on the SQLite DB — non-zero count proves enrollments survived.

3. **Bring the portal back up one tier at a time** using `portal-on.ps1` (which does this in dependency order):
   ```powershell
   .\scripts\portal-on.ps1
   ```
   After each tier comes up, check logs for clean startup. Stop if anything looks wrong.

4. **Re-enroll WebAuthn / TOTP** from the recovered Authelia. Visit `https://auth.${PUBLIC_DOMAIN}/settings/two-factor` and add fresh credentials.

5. **Validate** by running every box in plan §11 "Functional", "Security smoke tests", and "Breach detection / IR validation". Specifically:
   - Gmail end-to-end (`/alert` instant)
   - Gmail end-to-end (`/run` digest)
   - X-Forwarded-For from CF-Connecting-IP
   - Tailnet path still serves OpenWebUI

6. **Confirm `portal-status.ps1` is green.** Pre-existing sessions issued before the JWT rotation are invalidated automatically.

---

## 5. Forensics

**Goal:** understand what happened, ideally enough to harden against it.

1. **Preserve `./incident/<ts>/` off-host** before reading on the host. Never edit the snapshot files in place.

2. **Filesystem deltas inside the stopped containers:**
   ```powershell
   docker diff caddy
   docker diff authelia
   docker diff portal-alerter
   ```
   These show files added/changed by the running container. For a read-only-rootFS container this should be near-empty; unexpected entries are suspicious.

3. **First successful auth from the attacker IP:**
   ```powershell
   docker cp authelia-data-volume-snapshot:/authelia.log .
   jq 'select(.remote_ip=="<attacker-ip>") | select(.msg | test("[Ss]uccessful"))' authelia.log | head -5
   ```
   The earliest such line is "first known attacker action." Look at requests in the Caddy log immediately preceding it.

4. **Cloudflare-side view:** Cloudflare Security Events (Zero Trust dashboard) shows requests CF saw, including ones it blocked before they reached the tunnel. Compare CF's events with Caddy's log — gaps indicate CF dropped the traffic.

5. **Authelia DB content snapshot:**
   ```powershell
   sqlite3 incident/<ts>/authelia-db.sqlite3 ".dump webauthn_credentials"
   sqlite3 incident/<ts>/authelia-db.sqlite3 ".dump totp_configurations"
   ```
   Confirms which 2FA methods existed at the time of the snapshot.

6. **Hash check of the user database file:** compare `incident/<ts>/users_database.yml` (if captured) against `git show HEAD:config/authelia/users_database.yml.template` (the template) to see what differs. The deltas show added users or hash changes.

---

## 6. Notification

**Goal:** the people who need to know, know.

1. **Household / co-resident users** (if applicable): "The portal is down for the next few hours. Reach OpenWebUI via the tailnet host as today. I'll let you know when it's back."

2. **Update `SECURITY.md` in the repo** with:
   - Incident date + UTC timestamp
   - Detection signal (which Gmail subject)
   - Containment timestamp
   - Recovery timestamp
   - Root cause (once known)
   - Hardening changes applied

3. **Cloudflare**: typically no notification is needed unless the tunnel token was suspected compromised, in which case use the Zero Trust dashboard to confirm the new token is the only active one and disable the old one explicitly.

4. **Google account**: if the OAuth client/token was revoked, expect a Google notification email. Confirm it matches your action; investigate if not.

---

## 7. Post-mortem

**Goal:** prevent the next one.

Within 7 days of recovery, write a short post-mortem covering:

1. **Timeline** — first detected → containment → eradication → recovery → final.
2. **Root cause** — what specifically allowed it. If you can't determine root cause, that itself is a finding.
3. **Detection latency** — how long between attacker first action and the first Gmail alert. If > 1 hour, the watcher thresholds may need tuning.
4. **Containment effectiveness** — did the killswitch leave anything running it shouldn't have? Did the snapshot capture useful evidence?
5. **Recovery friction** — what made recovery slow. (Common culprit: not enough off-host backups, or the WebAuthn re-enrollment process required physical keys not on hand.)
6. **Hardening** — concrete changes to land before the next time. Examples:
   - Tighten `authelia-watcher` thresholds
   - Add specific patterns to v2 CrowdSec scenarios
   - Add a new section to the digest's `/run` endpoint
   - Update `known-ips.txt` based on what you saw
   - Provision a dedicated OAuth client for the portal-alerter (closes the §6.9 coupling)
7. **Update this document** with anything that proved incorrect or incomplete during the incident.

---

## Appendix A — Quick-reference commands

```powershell
# Status
.\scripts\portal-status.ps1

# Emergency stop
.\scripts\breach-killswitch.ps1

# Dry-run the killswitch (no changes — see what it would do)
.\scripts\breach-killswitch.ps1 -DryRun

# Manual /alert email (test)
docker exec portal-alerter wget -qO- --post-data='{"severity":"medium","event":"manual.test","timestamp_utc":"2026-05-28T00:00:00Z"}' --header='Content-Type: application/json' http://127.0.0.1:8080/alert

# Force a digest right now (any window)
docker exec portal-alerter wget -qO- --post-data='{"window_hours":1}' --header='Content-Type: application/json' http://127.0.0.1:8080/run

# Read the most recent digest markdown
docker exec portal-alerter cat /reports/digest-latest.md

# Inspect Authelia DB on a stopped container
docker run --rm -v authelia-data:/data:ro nouchka/sqlite3 /data/db.sqlite3 ".tables"
```
