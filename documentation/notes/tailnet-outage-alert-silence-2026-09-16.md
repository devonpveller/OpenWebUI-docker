# Tailnet outage 2026-09-16 — 94 minutes, zero alerts

**What the operator saw:** "Mattermost is unresponsive again."
**What was actually wrong:** the tailscale node was logged out; Mattermost was
healthy the whole time. Every tailnet route was gone, and nothing told anyone.

Related: [`../sysadmin-out-of-band-channel.md`](../sysadmin-out-of-band-channel.md)
(updated by this work), the 2026-09-09 podcast-TTS netns outage (same root cause
class), and [`sysadmin-disk-alert-silence-2026-09-06.md`](sysadmin-disk-alert-silence-2026-09-06.md)
(same *shape*: a check that ran, found the fault, and told nobody).

---

## Timeline (UTC)

| Time | Event |
|---|---|
| 20:39:46 | `tailscaled.state` rewritten; node logs out. `Received error: invalid key: API key does not exist` |
| 20:39:46 → 22:13 | Container entrypoint retries every ~35 s. Every `tailscale serve` fails with `Logged out.` |
| 16:47 → 18:07 local | `StackWatchdog` detects it **8 times**, logs two WARN lines each cycle, alerts **nowhere** |
| 22:13 | Operator reports it. Diagnosis; operator issues a fresh auth key |
| 22:13:06 | `up -d --force-recreate --no-deps tailscale` — node re-registers, `nodeKeyExpired=true → new nodekey` |
| 22:14:04 | All 6 serve listeners restored; Mattermost reachable at `:8446` |

**Impact:** OpenWebUI, Mattermost (`:8446`), the wiki (`:8444`), the LiteLLM UI
(`:8445`), Open Notebook (`:8443`/`:5055`) and the tailnet `/llama-cpp` routes —
all unreachable for 94 minutes. Host-local `127.0.0.1:8065` was unaffected
throughout, which is why the containers all looked healthy.

## Root cause

The **node key expired** (180-day default), forcing a re-login. The re-login
failed because the `TAILSCALE_AUTH_KEY` stored in `.env` had itself been revoked
upstream. Registration confirmed it on the fix:

```
RegisterReq: got response; nodeKeyExpired=true, machineAuthorized=true
control: server reports new node key [o5klC] has expired
control: Generating a new nodekey.
```

`machineAuthorized=true` — device approval was never involved. The initial
hypothesis that the device needed approval was **wrong**; the registration log
disproved it.

## Why nothing alerted — four defects

All four were in `scripts/checks/stack-watchdog.ps1`.

### 1. Nothing in the catastrophe tier reached Telegram
`Send-TelegramAlert` existed but was wired **only** to Docker-engine-down and
host-lifeline tasks, per the original out-of-band design (Telegram = "what
survives a full Docker-down"). A single dead container — even one carrying every
remote route — produced a `WARN` in a log file and nothing else. The Telegram
path itself was healthy the entire outage; it was simply never called.

### 2. A failed repair aborted the whole cycle (the blind spot)
`return $false` on tailscale-start failure meant that for 94 minutes the
watchdog checked **nothing else** — no inference, no backups, no bridges:

```
17:07 Starting comprehensive health check...
17:07 Docker engine UP
17:07 Tailscale container not running, starting...
17:07 Tailscale failed to start properly, may need OpenWebUI restart
                        <cycle ends — every remaining check skipped>
```

This is the *same* blind spot the out-of-band doc records as fixed for the
Docker-engine case, still present one layer down. Six more fatal `return $false`
sites had the same shape.

### 3. The repair could never have worked
`Test-ServiceHealth` returns `$false` for **both** "not running" and "running but
unhealthy". The container was running, so the log line "Tailscale container not
running" was false, and the repair — `compose up -d tailscale` — is a **no-op**
on an already-running container with unchanged config. It retried that no-op 8
times. The functions that would have found the truth (`Get-MissingTailscaleServes`,
which checks all 8 serve mappings) sit *below* the abort and never ran.

### 4. `Repair-TailscaleService` could never report success
Both of its verification points read:

```powershell
if (Test-NetworkConnectivity -and Test-TailscaleConnection) {   # WRONG
```

PowerShell binds `-and` as a *parameter* to `Test-NetworkConnectivity`. Verified
with a minimal repro — with both functions returning `$true` this **throws**
`A parameter cannot be found that matches parameter name 'and'`, which the
enclosing `try/catch` swallows into `return $false`. The tailscale auto-repair
has therefore been structurally incapable of reporting success. Correct form:
`if ((Test-NetworkConnectivity) -and (Test-TailscaleConnection))`.

## What changed

Operator decision on tier (2026-09-16): **remote access + comms + inference**,
firing only *after* an automatic repair has already failed, so a self-healed blip
stays quiet.

- **`Send-CatastropheAlert` / `Resolve-Catastrophe`** — Telegram (primary,
  Docker-independent) + a best-effort Mattermost mirror; hourly re-alert while
  the fault persists; an all-clear ping that only fires if the key was actually
  firing. 6 call sites: tailnet container, tailnet connectivity, tailscale
  daemon, tailscale logged-out, Mattermost unreachable, inference
  (llama-cpp + llm-gateway/llm-queue).
- **No more fatal aborts.** Every failed repair now records into
  `$script:HealthIssues` and the cycle continues, so one outage can no longer
  blind the watchdog to everything else.
- **`Get-ContainerState`** — distinguishes running / running-but-unhealthy /
  missing, so an unhealthy container is **recreated** (`up -d --force-recreate
  --no-deps`, which also picks up a changed `.env`) rather than "started" with a
  no-op. `--no-deps` keeps `openwebui` — which owns the netns tailscale joins —
  untouched.
- **`Test-TailscaleNodeState`** — login state *and* node-key expiry. The
  preventive half: warns once a day when the key is <14 days from expiry, so
  this class of outage gets notice instead of a 94-minute silence.
- **Honest summary** — the cycle used to print "All health checks passed" even
  when it had logged `ERROR`. It now reports `WITH ISSUES: <list>` and exits 1.
- Fixed the `-and` parameter-binding bug at both sites.

## Verification

- `Parser::ParseFile` — no syntax errors.
- Full live cycle: completes in 32 s, all checks run, **zero** false alerts
  (no new `.tg-alert-*` sentinels).
- Before/after in one log: `19:47` (old code) `All health checks passed` despite
  `BACKUP STALE`; `19:55` (new code) `Health check completed WITH ISSUES: backup-stale`.
- `StackWatchdog` scheduled task triggered manually → `LastTaskResult=1`
  (completed-with-issues), confirming the live task runs the new path. The task
  spawns a fresh `powershell.exe` per 10-minute tick with `-Mode check` (the
  default), so **no restart was needed to deploy**.
- Alert mechanism self-test: fires once → suppresses the duplicate under throttle
  → sends the all-clear and clears the sentinel → stays silent when nothing was
  firing.
- `Test-TailscaleNodeState` against the live node: `Reachable=True`,
  `LoggedIn=True`, `ExpiresInDays=179.9`.

## Adversarial review of the fix — 6 defects found, 6 fixed

The first cut of this fix was reviewed by an agent briefed to **refute** it, not
bless it. It found six real defects, two of them severe. This section is kept
because the review's value was in what it *disproved*.

| # | Defect | Fix |
|---|---|---|
| F1 | **`Resolve-Catastrophe` deleted the throttle sentinel**, re-arming the alert instantly. A service flapping fail/recover each cycle paged **6x/hour**. | Firing state moved to a separate `.tg-state-<key>` file; `Resolve` no longer touches the `.tg-alert-<key>` throttle floor. Capped at 1 ALERT + 1 RESOLVED per key per hour. |
| F2 | Logout guard treated **any** `BackendState != Running` as "LOGGED OUT" — so a normal restart (60s `start_period`) would page with the *wrong remedy*. | Only `NeedsLogin` pages as logged-out; `NeedsMachineAuth` gets its own message (an auth key will NOT fix device approval); `Starting`/`NoState`/`Stopped` log only. |
| F3 | The Mattermost mirror was **dead code**: `-replace '\', '/'` is an invalid regex, inside an empty `catch`. | One character: `'\\'`. The same file gets it right at 4 other sites. |
| F4 | `docker exec ... 2>$null` under `$ErrorActionPreference="Stop"` **throws on a single stderr byte** → empty catch → `Reachable=$false` → the logout alert silently never fires. The check also logged *nothing*, so going blind would look exactly like being healthy. | Routed through `cmd /c "... 2>&1"` (the pattern this file already uses at `:418`/`:726`), plus a `DEBUG` line on success and a `WARN` when the read fails. |
| F5 | The `openwebui` key had no outer `else`, so it was never cleared in the normal case — no all-clear ever, and the *next* outage within the hour throttled away. | Added the outer `else`. |
| F6 | The new `--force-recreate` runs **every 10 min** while unhealthy. The container healthcheck probes `127.0.0.1:8080` — *OpenWebUI's* port over the shared netns — so an OWUI outage would rebuild tailscale every cycle, flapping all 8 routes. | 1-hour cooldown sentinel; the log says to check whether OpenWebUI is the real fault. |

Two claims the review **confirmed**: no alert-less abort path remains in
`Invoke-HealthCheck` (AST-enumerated: 3 returns, all accounted for), and the
`-and` fix is correct with no other instances in `scripts/` — the scanner was
self-tested by first catching the two known-bad lines in the pre-change backup.

It also found that **four stale worktrees** still carry the `-and` bug at
`:529`/`:551` (`.claude/worktrees/{wt-mmtest22,wt-mmthread,wt-nasbackup,wt-vhdxtrim}`).
Merging any of those work lines reintroduces it.

### Red/green proof for F1

Measured against a reconstructed pre-fix copy, then the live script — a fault
alternating fail/recover across six 10-minute cycles:

```
BEFORE  FLAP (6 cycles)   messages/hour = 6   (max allowed 2)  [FAIL]
AFTER   FLAP (6 cycles)   messages/hour = 2   (max allowed 2)  [PASS]
        PERSISTENT        messages/hour = 1   [PASS]   <- steady fault still pages once
        HEALTHY-ONLY      messages/hour = 0   [PASS]   <- no all-clear when nothing fired
        OUTAGE->RECOVERY  ALERT+RESOLVED = 2  [PASS]   <- a real outage still delivers both
```

Post-fix live cycle: 33 s, exit 1 (`backup-stale`), no new sentinels, and the
node-state check is now visible in the log:
`Tailscale node state: BackendState=Running keyExpiresInDays=179.9`.

## Open / follow-ups

- **The durable fix is still outstanding:** disable key expiry for this node in
  the Tailscale admin console, or move to an OAuth client. Today's re-auth reset
  the clock to ~180 days (`ExpiresInDays=179.9`), so without that change this
  recurs around **2027-03-15**. The new <14-day warning is a net, not a cure.
- `lm-models` backup has been stale since 2026-09-06 (253 h vs a 220 h
  threshold). Deliberately **not** catastrophe tier, but it now means
  `StackWatchdog` reports `LastTaskResult=1` persistently — so the task's exit
  code is not a useful "is anything new wrong" signal until that backup is fixed.
  The catastrophe tier (Telegram) and the log's `WITH ISSUES` line are the real
  signals.
- A stale serve reference was visible in `netstack` logs throughout
  (`could not connect to local backend server at 127.0.0.1:8446` — caddy's port,
  not the mattermost backend `:8241`). Harmless, not chased.
- **F7 (not fixed, accepted):** removing every early return makes a worst-case
  compound-fault cycle much longer (180s OWUI + 45s tailscale + 2x115s tailscale
  repair + 165s llama-cpp + ...) against a 10-minute trigger whose
  `MultipleInstances=IgnoreNew` **drops** overrun ticks rather than queueing them.
  Healthy cycles measure ~33 s, so this only bites during a compound fault - which
  is exactly when it matters. Worth bounding the per-repair waits later.
- **F9 (pre-existing):** `Send-TelegramAlert` writes the throttle sentinel without
  checking `$LASTEXITCODE`, so a *failed* send still suppresses re-alerts for an
  hour. The new tier makes this matter more than it did.
- Four stale worktrees still carry the `-and` bug (see the review section) - they
  will reintroduce it if merged.
- Changes are in the working tree of the operator's main checkout,
  **not staged and not committed**.
