# Sysadmin out-of-band channel & Docker-down recovery protocol

**Purpose.** Keep the operator in contact — and able to recover the stack — during the
one window where the normal channel is gone: a **full Docker-down**. That happens on
vhdx compaction (deliberate, ~10–15 min) and on a crash/OOM. Because **Mattermost is a
Docker container**, when Docker is down every Mattermost-based path (the `#sysadmin`
bridge, `notify-mattermost.sh`) is dead too. The fix is a **Docker-independent Telegram
channel** plus an autonomous **engine-restart watchdog**.

Related: [`backup-restore-runbook.md`](backup-restore-runbook.md) (data recovery),
`scripts/emergency-recovery.ps1` (ordered restart), and the `litellm-proxy-status` /
disk-bloat memories.

---

## What survives a Docker-down (the failure geometry)

| Component | Runs as | During Docker-down |
|---|---|---|
| Mattermost (our normal channel) | **Docker container** (agent-org) | ❌ DOWN |
| `notify-mattermost.sh` alerts | POST to MM container :8065 | ❌ silent |
| Compaction task (`compact-vhdx.ps1`) | Host, elevated Scheduled Task | ✅ runs, self-drives Docker back |
| `check-tailscale-health.ps1` watchdog | Host Scheduled Task (60s) | ✅ runs (see Layer 2) |
| Host **Tailscale** (your remote access) | Host daemon, **unattended mode** | ✅ UP — you can still RDP/SSH to the box |
| claude-sessions bridge / sysadmin bridge / **Telegram listener** | Host Scheduled Tasks (48291/48292/48293) | ✅ processes alive |
| **Telegram** (out-of-band channel) | Host HTTPS → api.telegram.org | ✅ UP both directions |

The host is reachable (host Tailscale) and Telegram works — so the operator is never truly
stranded, provided alerts reach them and a recovery lever exists. That's what this adds.

---

## The three layers

### Layer 1 — out-of-band alerts (`scripts/sysadmin-mcp/telegram_notify.py`)
Reads `SYSADMIN_TELEGRAM_BOT_TOKEN` + `SYSADMIN_TELEGRAM_CHAT_ID` from the repo-root `.env`
at runtime and POSTs to the Telegram Bot API (stdlib only, best-effort, never throws).
Wired into `compact-vhdx.ps1`:
- **STARTING** ping the moment compaction commits (expect the silence window).
- **OK** ping on success (reclaimed GB, stack back).
- **ALERT** ping on failure — actionable ("reply `docker up` / `recover` / `status`").
  This is the fix for *"a compaction stranded Docker silently."*

### Layer 2 — engine-down watchdog (`scripts/check-tailscale-health.ps1`)
- `Confirm-DockerEngine` runs **first** in `Invoke-HealthCheck`. If the engine is up it
  no-ops; if DOWN it attempts `docker desktop start` (reset-and-retry, ~2×150 s). This is
  what keeps trying **after** the compaction script's own 3 finally-block retries give up
  (the watchdog is re-enabled the moment a compaction ends). On unrecovered failure it
  fires an actionable Telegram ALERT.
- **Blind-spot fix:** previously the health check bailed out early when Docker was down and
  never reached the bridge/listener checks. Now, when the engine can't be recovered, it
  still verifies the **host lifelines** (48291/48292/48293) via `Confirm-HostTaskByPort`
  and restarts any whose Scheduled Task has died.
- In the normal (engine-up) flow it also watches the **sysadmin bridge (48292)** and the
  **Telegram listener (48293)**, which nothing watched before.

### Layer 3 — command listener (`scripts/sysadmin-mcp/telegram_listener.py`)
Host Scheduled Task `sysadmin-telegram-listener` (logon start, lock port **48293**). Long-polls
Telegram and runs a **strict whitelist** for the operator's `chat_id` only:

| Text the bot | Action |
|---|---|
| `status` | engine up? · running-container count · C: free · last compaction |
| `docker up` | `docker desktop start` + wait |
| `mattermost` / `mm` | bring up **only** Mattermost + its DB, then confirm the #claude-sessions bridge — fast, safe path to a Claude session (vs a full `recover`) |
| `recover` | `emergency-recovery.ps1 recover` (ordered restart) |
| `compact status` | last vhdx-compaction result |
| `gpu-reset` / `nuclear` | **asks for `confirm <action>`** first |
| `help` | command list |

**Security:** only `SYSADMIN_TELEGRAM_CHAT_ID` is honored (others logged + dropped); whitelist
only (message text is never `exec`'d); destructive actions need a typed confirm; single-instance
lock prevents duplicate pollers; every command is audit-logged to
`scripts/sysadmin-mcp/telegram-state/audit.jsonl`.

---

## Operator runbook — "I got an ALERT while away"

1. **`status`** → see what's actually wrong (engine down? partial stack?).
2. **Just want a Claude session to drive the fixes yourself?** → **`mm`**. It ensures the engine
   is up, brings up **only** Mattermost + its DB, and confirms the #claude-sessions bridge — then
   open the Mattermost app and work in `#claude-sessions`. This is the preferred first move: it
   leaves inference/GPU/the rest untouched, so it can't make a partial outage worse the way a full
   `recover` might.
3. Whole engine down → **`docker up`** (starts the engine; `restart: unless-stopped` containers,
   Mattermost included, come back on their own). Wait, then **`status`**.
4. Still partial/broken after `mm` or `docker up` → **`recover`** (ordered full restart, a few
   minutes; also needed if a prior `nuclear`/`compose down` removed containers so restart policies
   don't apply), or last-resort **`nuclear`** → `confirm nuclear`, or reboot the host.
5. Once Docker is back, Mattermost returns and both `#sysadmin` and `#claude-sessions` resume
   automatically (the host bridges reconnect within one poll).

> Hands-on host access (RDP/SSH over Tailscale) is **not enabled yet** (host is on the tailnet at
> `shuya8873desktop01-1.tail37f875.ts.net`, but RDP is off and no SSH server is installed). Until it
> is, the Telegram commands above are the remote levers. Tailscale-SSH server is not available on a
> Windows host; enabling tailnet-scoped RDP is the planned fallback.

## The compaction protocol (@sysadmin persona)
Before triggering `compact_execute`, announce in `#sysadmin`: window starting, ~10–15 min of
silence, out-of-band ping when back or stuck. The `compact-vhdx.ps1` STARTING ping is the belt
in case the operator is already away. After it finishes, confirm the outcome (via `compact_status`
once Mattermost is back, or the Telegram OK/ALERT ping).

---

## Setup / re-provisioning
1. Create the bot via **@BotFather** → put its token in repo-root `.env` as
   `SYSADMIN_TELEGRAM_BOT_TOKEN=<id>:<secret>`.
2. Message the bot once; capture the chat id from `getUpdates`; store as
   `SYSADMIN_TELEGRAM_CHAT_ID` in `.env`.
3. Register the listener (elevated, once):
   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sysadmin-mcp\register-sysadmin-telegram.ps1`
   then `schtasks /run /tn sysadmin-telegram-listener`.
4. Verify: text the bot `status`.

**Lock ports:** 48291 claude-sessions bridge · 48292 sysadmin bridge · 48293 Telegram listener.
