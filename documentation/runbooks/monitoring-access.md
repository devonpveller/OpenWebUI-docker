# Monitoring portal access

Everything covered here is opt-out: the watchers are always running while
the portal is up. This doc is the operator's reference for *where to
look* when something looks odd, and *what already alerts you*
automatically.

## What alerts you automatically (via email)

All emails arrive in `${DIGEST_TO}` (your Gmail). The portal-alerter
container is the single Gmail egress; everything POSTs to it over the
internal `auth-net` Docker network.

| Source | Condition | Severity |
|---|---|---|
| **authelia-watcher** | 5+ failed Authelia logins from same IP in 5 min | HIGH |
| | First successful login from an IP not in `config/watcher/known-ips.txt` *(IP is then auto-recorded; subsequent logins from the same source won't re-alert)* | HIGH |
| | Authelia regulation ban applied | MEDIUM |
| | WebAuthn / TOTP credential added or removed | HIGH |
| | Authelia config reload | MEDIUM |
| | 10+ × 401 from same IP in 1 min at the Caddy layer | MEDIUM |
| **tunnel-watcher** | cloudflared `/ready` failed 3 consecutive probes (~90s default) | HIGH |
| | Cloudflared tunnel recovered | INFO |
| **integrity-tripwire** | Any change to Caddyfile / authelia config / users db | (file-based) |
| **authelia-notif-bridge** | Any user-facing Authelia notification (2FA OTP, password reset, …) | INFO |
| **portal-cron** | Daily 07:00 UTC scheduled digest of the last 24 h | (digest format) |

The first two — burst + new-IP — cover the realistic "tunnel
compromised" case. If someone gets past the Cloudflare Access OTP and
hits Authelia from an IP you've never used, you get an email within
seconds.

## known-ips.txt — how to read and edit

[`config/watcher/known-ips.txt`](../config/watcher/known-ips.txt) is the
list of IPs that *don't* fire the new-IP alert. Format: one full IP per
line (`#` comments). It's mounted **read-write** into authelia-watcher;
the watcher auto-appends an IP after firing the first new-IP alert from
that source, so subsequent logins from the same device are silent.

To remove a stale IP (e.g. a mobile carrier address you've moved off),
edit the file directly on the host. The watcher re-reads on every event,
no restart needed.

If you want to nuke the whole "known IPs" memory and start fresh, just
empty the file -- next login from every device will alert again.

## Interactive log query — when an alert isn't enough

[`scripts/portal/access-query.ps1`](../scripts/portal/access-query.ps1) is the
operator's "show me what happened" tool. It reads the tail of
`authelia.log` and `caddy-access.log` from inside the watcher container,
applies filters, and prints a table.

Common patterns:

```powershell
# Quick "anyone hit the portal in the last hour?" sweep
.\scripts\access-query.ps1 -Hours 1

# Just Authelia activity (sign-in attempts, etc.)
.\scripts\access-query.ps1 -Hours 24 -OnlyAuth

# Filter by hostname (subdomain)
.\scripts\access-query.ps1 -Subdomain openwebui

# Only failed/forbidden requests
.\scripts\access-query.ps1 -Status 401 -Status 403

# Investigate a specific IP that fired a new-IP alert
.\scripts\access-query.ps1 -IP 2a09:bac3:b936

# Summary view: who's been hitting the portal at all?
.\scripts\access-query.ps1 -Hours 168 -UniqueIPs   # 1 week
```

Time range is in `-Hours` (default 24). Output is sorted newest-first.
The script reads up to the last 5000 lines per log -- if you need older
history, the rolled access logs are in the `caddy-data` volume under
`/data/caddy-access.log.*`.

## Cloudflare Access logs (free tier — dashboard only)

Cloudflare Access has its own access log -- *every* OTP-gate
authentication attempt before the request even reaches your tunnel.
That's distinct from Authelia's log: CF Access protects the perimeter,
Authelia protects the apps.

**On the CF free tier**, the only way to view CF Access logs is in the
dashboard:

- Zero Trust → Logs → Access (full list of every OTP gate hit)
- Zero Trust → Logs → Gateway (network-level, less relevant here)

Bookmark that page; check it weekly. Anything truly suspicious there
(repeated denials from unusual ASNs, success from countries you've
never been in) is the precursor to a real attack -- the alerts above
would only fire **after** the bad actor passes CF Access.

### When/if you upgrade to a paid CF plan

CF Business+ unlocks **Logpush**, which can push CF Access logs to an
HTTP endpoint or storage bucket every minute. The integration path would
be:

1. Create a Logpush job in Zero Trust dashboard pointing at
   `https://devinveller.ai/cf-logs-ingest` (a new Caddy route, secured
   by a long shared header secret).
2. Add a tiny `cf-log-ingest` sidecar (similar shape to
   `authelia-notif-bridge`) that receives the POST, classifies anomalous
   patterns, and forwards them to `portal-alerter /alert`.

Until then, the CF dashboard is the source of truth for CF Access
events. Audit Logs API (CF's `/accounts/{id}/audit_logs`) is available
on lower tiers but only covers **admin** actions, not user-facing
access events.

## Cloudflare tunnel health

[`tunnel-watcher`](../config/tunnel-watcher/tunnel-watch.sh) polls
`http://cloudflared:2000/ready` every 30 seconds (configurable via
`TUNNEL_WATCHER_POLL_SEC`). After 3 consecutive failures
(`TUNNEL_WATCHER_FAILURES_BEFORE_ALERT`, default 3 = ~90s), it fires
a HIGH-severity email and waits silently until the tunnel recovers, at
which point it sends an INFO recovery event. A heartbeat line lands in
`docker logs tunnel-watcher` every 120 probes (60 min by default) so
silent ≠ ambiguous.

The /ready endpoint returns `{"status":200,"readyConnections":N,...}`.
N drops to 0 = tunnel is fully disconnected. The watcher treats any
non-2xx as "down".

## Where the logs actually live

| Log | Container path (in volume) | Host path |
|---|---|---|
| Authelia | `/logs/authelia/authelia.log` (in `authelia-data` volume) | `docker exec authelia-watcher tail /logs/authelia/authelia.log` |
| Caddy access | `/data/caddy-access.log` (in `caddy-data` volume) | `docker exec authelia-watcher tail /logs/caddy/caddy-access.log` |
| Cloudflared | container stdout only | `docker logs cloudflared` |
| Tunnel-watcher | container stdout only | `docker logs tunnel-watcher` |
| Portal-alerter | container stdout only | `docker logs portal-alerter` |
| Portal-alerter digest reports | `/reports/` (now persisted) | `./reports/portal-digest/` |

Rotation: Caddy rolls its log at 100 MB / 7 backups / 30 days
(`access_log` snippet in [Caddyfile](../config/caddy/Caddyfile)).
Authelia's log doesn't self-rotate; size monitoring is an open
follow-up.
