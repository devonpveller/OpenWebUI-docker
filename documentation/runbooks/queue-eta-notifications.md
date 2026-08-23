# Queue ETA notifications (issue #25)

Mattermost pings when a caller's request is going to sit in the llm-queue
admission queue long enough to matter. Closes the K.10 PLANNED item
"queue-ETA notifications to the user (MM/OWUI) for long-waiting jobs".

## What it does

`scripts/checks/queue-eta-notify.ps1` (PowerShell 5.1, ASCII, no-BOM —
mirrors `scripts/checks/stack-watchdog.ps1`) polls the queue snapshot:

```
docker exec llm-queue curl -fsS http://localhost:8080/observe/queue
```

and flags waiting rows where `waited_s + est_wait_s >= threshold`
(default **120 s**). For each flagged (caller key, model) lane it posts
**one** Mattermost message with the flag timestamp, est start
(`now + est_wait_s`) and est completion (`now + est_wait_s + avg_T_s`,
the per-model mean service time from the snapshot).

Dedup: a state JSON under `logs/` carries a **10-minute per-lane cooldown**,
so a persistently queued caller is pinged at most once per 10 minutes per
lane. Lanes that clear are removed from the state, so they re-notify
normally the next time they queue up.

Mattermost posting mirrors `scripts/notify-mattermost.sh`: the bot token is
read from `agent-org/docker/.env` (`AO_MATTERMOST_BOT_TOKEN`) at **run time**
(never committed), the channel id is a parameter, and it is **fail-soft** —
a down Mattermost or missing token is logged and never fails the check.

## Cadence

- **Daemon (default deployment):** one pass every `-IntervalSeconds`
  (default **60 s**). The daemon is the `QueueEtaNotifier` Windows Service
  (see below).
- **Manual / one-shot:** `-Mode check` runs a single pass and exits
  (0 = probe succeeded, 1 = snapshot could not be fetched).
- The 10-minute per-lane cooldown is independent of the cadence: at the
  default 60 s interval a lane can fire at most ~6 times per hour.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `-Mode` | `check` | `check` (one pass) / `daemon` (loop) / `install-service` (Windows Service, admin) |
| `-IntervalSeconds` | `60` | Daemon poll interval (10–3600) |
| `-ThresholdSeconds` | `120` | Flag when `waited_s + est_wait_s >=` this (1–86400) |
| `-ChannelId` | `qqq97fwxd3f8ufenjybrf5w1yr` (#claude-code, same as `notify-mattermost.sh`) | Mattermost channel id to post to |
| `-DryRun` | off | Print the would-be message to the log instead of posting |
| `-SnapshotFile` | — | Feed a fixture JSON (e.g. `scripts/checks/fixtures/queue-eta-busy.json`) instead of `docker exec` — sandbox/host-harness testing without Docker |

## Task name / service

- **Windows Service:** `QueueEtaNotifier` (installed by
  `-Mode install-service`, requires an elevated PowerShell). It runs the
  daemon with the parameters captured at install time. If the service
  already exists the install **refuses** (no auto-removal — a destructive
  action needs explicit operator intent): stop and remove it first, then
  re-run. Uninstall: `Stop-Service QueueEtaNotifier`, then remove it with
  the standard `sc.exe` removal command (Services.msc works too).

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\queue-eta-notify.ps1 -Mode daemon -IntervalSeconds 60 -ThresholdSeconds 120 -ChannelId <id>
  ```

- **Manual runs:**

  ```powershell
  # one pass, real post
  .\scripts\checks\queue-eta-notify.ps1 -Mode check
  # one pass against the busy fixture, no post (sandbox evidence)
  .\scripts\checks\queue-eta-notify.ps1 -Mode check -SnapshotFile .\scripts\checks\fixtures\queue-eta-busy.json -DryRun
  ```

## Logs and state

| File | Purpose |
|---|---|
| `logs/queue-eta-notify.log` | Structured `[timestamp] [LEVEL] message` log (same format as the watchdog) |
| `logs/queue-eta-notify-state.json` | Dedup state: `{"version":1,"lanes":{"<key>|<model>":{"last_notified":"<ISO-8601>"}}}` |

## Silencing

Pick the narrowest one that fits:

1. **Stop the service** (daemon keeps no state, so this is clean):
   `Stop-Service QueueEtaNotifier` (or `sc.exe stop QueueEtaNotifier`).
2. **Raise the threshold** so nothing flags:
   `-ThresholdSeconds 86400` (max allowed).
3. **Dry-run mode:** `-DryRun` logs instead of posting — the daemon keeps
   running and the state file keeps updating, but nothing reaches Mattermost.
4. **Reset the cooldown** (force an immediate re-ping of a still-flagged
   lane): remove `logs/queue-eta-notify-state.json` (it is recreated on the
   next pass).
5. **Per-lane:** edit `logs/queue-eta-notify-state.json` and set the lane's
   `last_notified` to a recent timestamp — that single lane is then in
   cooldown for 10 minutes without touching the others.

## Fixtures

- `scripts/checks/fixtures/queue-eta-busy.json` — full `snapshot()` shape
  (see `llm-queue/src/llm_queue/scheduler.py`, `snapshot()`): 2 running +
  3 waiting rows; at the default 120 s threshold two lanes flag
  (`owui-chat`, `little-coder`) and one row stays under.
- `scripts/checks/fixtures/queue-eta-idle.json` — same shape, empty
  `waiting`; exercises the all-clear path (state lanes are dropped).

Validate them without PowerShell/Docker:

```bash
python3 - <<'EOF'
import json
TOP = ["model", "running", "waiting", "avg_T_s", "P", "permits_free", "inflight_by_key"]
for name in ("busy", "idle"):
    s = json.load(open(f"scripts/checks/fixtures/queue-eta-{name}.json"))
    assert all(k in s for k in TOP), name
    for w in s["waiting"]:
        assert all(k in w for k in ("id", "key", "prio", "waited_s", "est_wait_s")), w
assert json.load(open("scripts/checks/fixtures/queue-eta-busy.json"))["waiting"]
assert json.load(open("scripts/checks/fixtures/queue-eta-idle.json"))["waiting"] == []
print("fixtures OK")
EOF
```
