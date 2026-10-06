# Sysadmin out-of-band channel & Docker-down recovery protocol

**Purpose.** Keep the operator in contact — and able to recover the stack — during the
one window where the normal channel is gone: a **full Docker-down**. That happens on
vhdx compaction (deliberate, ~10–15 min) and on a crash/OOM. Because **Mattermost is a
Docker container**, when Docker is down every Mattermost-based path (the `#sysadmin`
bridge, `notify-mattermost.sh`) is dead too. The fix is a **Docker-independent Telegram
channel** plus an autonomous **engine-restart watchdog**.

> **Scope widened 2026-09-16.** Telegram was originally scoped to a full
> Docker-down *only*. That left the opposite failure silent: on 2026-09-16 the
> tailscale node logged out (expired node key) and every tailnet route -
> OpenWebUI, Mattermost `:8446`, the wiki, the LiteLLM UI - was gone for 94
> minutes while Docker stayed perfectly healthy. The watchdog detected it 8
> times and alerted nowhere. Telegram now also carries the **catastrophe tier**
> (Layer 4). Full account:
> [`../documentation-plans-ai-stack/journal/notes/tailnet-outage-alert-silence-2026-09-16.md`](../../documentation-plans-ai-stack/journal/notes/tailnet-outage-alert-silence-2026-09-16.md).

Related: [`runbooks/backup-restore-runbook.md`](runbooks/backup-restore-runbook.md) (data recovery),
`scripts/recovery/emergency-recovery.ps1` (ordered restart), and the `litellm-proxy-status` /
disk-bloat memories.

---

## What survives a Docker-down (the failure geometry)

| Component | Runs as | During Docker-down |
|---|---|---|
| Mattermost (our normal channel) | **Docker container** (agent-org) | ❌ DOWN |
| `notify-mattermost.sh` alerts | POST to MM container :8065 | ❌ silent |
| Compaction task (`compact-vhdx.ps1`) | Host, elevated Scheduled Task | ✅ runs, self-drives Docker back |
| `stack-watchdog.ps1` watchdog | Host Scheduled Task (every 10 min, `PT10M`) | ✅ runs (see Layer 2) |
| Host **Tailscale** (your remote access) | Host daemon, **unattended mode** | ✅ UP — you can still RDP/SSH to the box |
| claude-sessions bridge / sysadmin bridge / **Telegram listener** | Host Scheduled Tasks (48291/48292/48293) | ✅ processes alive |
| **Telegram** (out-of-band channel) | Host HTTPS → api.telegram.org | ✅ UP both directions |

The host is reachable (host Tailscale) and Telegram works — so the operator is never truly
stranded, provided alerts reach them and a recovery lever exists. That's what this adds.

---

## The four layers

### Layer 1 — out-of-band alerts (`scripts/sysadmin-mcp/telegram_notify.py`)
Reads `SYSADMIN_TELEGRAM_BOT_TOKEN` + `SYSADMIN_TELEGRAM_CHAT_ID` from the repo-root `.env`
at runtime and POSTs to the Telegram Bot API (stdlib only, best-effort, never throws).
Wired into `compact-vhdx.ps1`:
- **STARTING** ping the moment compaction commits (expect the silence window).
- **OK** ping on success (reclaimed GB, stack back).
- **ALERT** ping on failure — actionable ("reply `docker up` / `recover` / `status`").
  This is the fix for *"a compaction stranded Docker silently."*

### Layer 2 — engine-down watchdog (`scripts/checks/stack-watchdog.ps1`)
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

### Layer 4 - catastrophe alerts for IN-STACK faults (added 2026-09-16)
`Send-CatastropheAlert` / `Resolve-Catastrophe` in `scripts/checks/stack-watchdog.ps1`.
Layers 1-2 answer *"Docker is gone"*; this one answers *"Docker is fine and the
thing you need is still unreachable."*

**Tier (operator decision 2026-09-16) - total loss of remote access, of the
comms channel itself, or of inference** - and only **after** an automatic repair
has already FAILED, so a self-healed blip stays quiet:

| Key | Fires when |
|---|---|
| `tailscale-container` | the tailscale container will not become healthy |
| `tailscale-logout` | the node is logged out (every `serve` route gone) |
| `tailscale-daemon` | tailscaled stops answering |
| `tailnet-connectivity` | no egress from inside the tailscale netns |
| `mattermost` | Mattermost unreachable - an alert about it *cannot* go through it |
| `inference` / `llm-gateway` | llama-cpp unreachable, or the gateway/queue front door is down |

Behaviour: Telegram first (Docker-independent), Mattermost mirrored best-effort;
re-alerts hourly while the fault persists; sends a RESOLVED ping and re-arms once
it clears. **Not** catastrophe tier, deliberately: stale backups, search gateway,
Open Notebook, little-coder - those stay in the log and Mattermost.

**Container-loop pages - catastrophe path, but with no repair (added 2026-09-29).**
The watchdog also counts EVERY container, not only the listed ones, and pages
through the same Telegram + Mattermost path. These keys are not "after a repair
failed": the watchdog attempts no repair at all, because a restart loop is usually
a credential or config fault and restarting again hides the evidence. The page
says what to look at; the fix is yours. The `crashloop-<name>` page, and the
`netns-<name>` pages for an owner that is not running or restarted after its
joiner, carry the container's last fault line from `docker logs`. The
`docker-unreadable` page and the `netns-<name>` "owner no longer exists" page
carry none (docker could not be asked). Every page text is credential-scrubbed
before it is logged or leaves the host (`Hide-CredentialShapes` in
`stack-watchdog.ps1`; its permanent regression table is case P34 of
`scripts/checks/test-watchdog-loops.ps1`). The scrub masks the SHAPE and keeps
the words around it, so `invalid key` and `password authentication failed`
still read.

**Covered shape families.** URL and DSN userinfo (`scheme://user:pw@host`, a
password holding `/` or `@`, the scheme-less `user:pw@tcp(host:3306)/db` and
`user:pw@host:5432`, Oracle `user/pw@//host:1521/SVC`, `user/pw@host:1521/SVC`
and `user/pw@alias`, also inside quotes); `Authorization:` header values
(Bearer, Basic, Token); `key=value` and `key: value` for secret-named keys
(password, passwd, passphrase, `pwd` as in ODBC `PWD=`, `pass` as a key - `pass=`,
`Pass:`, `DB_PASS`, `dbPass`, `DBPASS=`, `PASS=` - secret, token, api key, auth
key, access key, private key, `secret_key` / `SECRET_KEY` / `secretKey`,
`SIGNING_KEY`, `ENCRYPTION_KEY`, `MASTER_KEY`, `LICENSE_KEY`, credentials, dsn),
with the value bare, quoted (`"..."`, `'...'`), bracketed or URL-encoded, after
`=` or `:`, in JSON or YAML, and values that hold `;` or `,`. **The rule for
the value:** after `=` everything is masked except `true`, `false`, `null`,
`none`, `nil`; after `:` a value stays readable ONLY when it is one plain,
unquoted word of letters (1-15 letters, a status word such as `required`,
`missing`, `invalid`, `expired`; a trailing run of `,` `.` `;` `:` `)` is stripped
before the test, so `expired,` is still a word and `expired!` is not).
Any value that starts with a character other than whitespace, a quote, `&`,
`;` or `,` and holds a digit, punctuation, a quote or a symbol is masked at any
length (`p@ss!wOrd`, `Trub-Fx#q`, `a1b2c`, `zx9!`, `./sa.json`, `3`), and so is
any letters-only value of 16 or more characters; CLI flags (`--password <pw>`, `--token <t>`,
mysql `-p<pw>`, `docker login -p`, `curl -u` / `-uUSER:PW` / `--user=`,
`redis-cli -a`, `sshpass -p`); vendor tokens (sk-, ghp_, github_pat_, xox*-,
AKIA, tskey-, hf_, glpat-, npm_, AIza), JWTs, Telegram bot tokens, Slack and
Discord webhook secrets; docker config `"auth":"..."`, Azure SAS `sig=`,
`<password>..</password>` tags, `Cookie:` / `Set-Cookie:` values (to the end of
the line); PEM private-key blocks; padded base64, base64 blobs holding `+` or a
few `/`, and any 40+ character run of letters, digits, `_` or `-` holding a
letter and a digit (that rule is also what catches a real 90-character Vault
`hvs.` token).

**There is NO path exemption.** A value under a secret-named key is masked
whatever it looks like - `credentials: /etc/app/credentials.json`,
`GOOGLE_APPLICATION_CREDENTIALS=/etc/app/sa.json`, `password:/Xy..`,
`"token": "~Xy.."`, `PWD=/home/app/src`. Four rounds of path exemptions each
traded one leak for another; masking a path in an error line costs a little
readability, a leaked secret costs far more, and the text around the value
stays (`credentials: [redacted] no such file or directory`).

**Deliberately NOT covered (a stated limit, not a surprise).** A secret written
as plain prose with no shape ("the password is hunter2"); a password that is
itself a single plain word of 1-15 letters after `:` (`secret_key: abcdefgh`,
`X-Api-Key: abcdefgh` - indistinguishable from the status word `required`;
with `=` it is masked); an upper-case `PASS:` (a key only with `=`); a key that
names a file rather than a secret (`password_file: /run/secrets/pw`,
`credentials file: sa.json`); a value that starts with `&`, `;`, `,` or an unterminated quote
(`password: &<pw>`, `token=&<pw>`, `password: ;<pw>`); generic `*_KEY` names beyond the listed prefixes
(`SESSION_KEY`, `HMAC_KEY`, `JWT_KEY`, `APP_KEY`, `client_key`, `webhook_key`,
`stripe_key`, `deploy_key`, `root_key`, a bare `KEY`); an unterminated quoted
value (`{"password": "Kp4v...` with no closing quote); Ruby / Perl hash syntax with
spaces (`password => "..."`; the compact `{"password"=>"..."}` form is partly masked); three or more spaces or tabs after the `:` / `=`
separator (the fault line collapses whitespace before the scrub, so this cannot
reach a transport, but it can in a direct unit call); a bare `key=` value shorter
than 16 characters; a Vault `hvs.` token shorter than the 40-character
opaque-run threshold; a bare Bearer of letters only with no `Authorization:`
header; redis `AUTH <pw>` replies; PEM body lines after the header (the fault
line is one collapsed line, so this does not arise there); Telegram-style tokens
that are not 35 characters after the colon; Oracle `user/pw@host.example.com`
with a dotted host and no port (masking it would also hit versioned archive
names with an at-sign); `curl -u <digits>:<6 or fewer digits>` (read as uid:gid, like
`docker run -u 1000:1000`).

**Masked even though they are not secrets (over-scrub, accepted):** any path
under a secret-named key, `credentials` included (`credentials: /etc/app/...`,
`token: /var/run/secrets/kubernetes.io/...`, `private_key: /etc/ssl/private/...`,
`secret: /run/secrets/...`, `api_key: ./config/key.txt`); any `PWD=` /
`OLDPWD=` value, so a working-directory line in an env dump (`PWD=/home/app/src`,
`PWD=C:\work`) is masked; a title-case `Pass:` followed by a test name; sha256
digests (`app@sha256:<64 hex>`) and 64-hex container ids; any 40+ character model
or file name with a letter and a digit (today's `.gguf` names are 23 and 27
characters and are untouched); a 40+ character relative code path with one to three `/`, mixed case
and a digit (for example `internal/Handlers2/RequestProcessorFactoryImpl`); a
user-only URL (`http://user@host/`); `from:bob@example.com`; and
`name/x@word`-shaped text followed by a space (`reg/app@sha256 pulled`,
`pkg/name@alias`; `reg/app@sha256:abc` and `pkg/x@1.2` stay); a 40-hex git
commit id (legacy GitHub tokens are 40 hex too, so the opaque-run rule keeps
masking them); a 16+ character value with a digit under a name that merely ends
in `key` (`sort_key=...`; the old weak rule); a number after `:` under a secret-named key (`Secret: 3 keys
rotated` becomes `Secret: [redacted] keys rotated`). Go / pytest output
such as `--- PASS: TestX (0.01s)` and `PASS: test_x1` is NOT masked.


| Key | Fires when |
|---|---|
| `crashloop-<name>` | the container (unbounded restart policy) keeps restarting: 3+ restarts over consecutive 10-minute passes, or 6+ in 6 h (a slow loop) |
| `netns-<name>` | a container that joins another's network namespace is stranded: its owner is not running, started after it, or no longer exists |
| `docker-unreadable` | docker cannot describe some container within the probe bound, so the census could not judge it |

Per key, a page repeats at most every 6 h (a cooldown, applied to Mattermost too).
`docker-unreadable`'s RESOLVED keeps the 1 h Telegram throttle (the 2026-09-16
anti-flap rule), so a daemon that answers every other pass pages once, not in
pairs. To run only this check from the
host, supervised and between two scheduled passes (it writes the same state file
and CAN send real alerts): `powershell -NoProfile -File scripts\checks\stack-watchdog.ps1 -Mode loops`
(exit 0 nothing found, 1 a finding, 2 the engine did not answer).

**The all-clear bar (RESOLVED).** A paged crash loop is declared over only once
it has *settled*: stopped, or running for at least the settle time with no new
restart. The settle time is `max(60 min, min(6 h, 3 x MaxGap))`, where MaxGap is
the largest gap between two passes that saw restarts, over the loop's life since
its last all-clear. So a fixed fast loop clears 60 minutes after it is fixed -
**only if no restart was seen in the 6 hours before it began**; if one was (even
one), that gap raises the bar and the RESOLVED arrives up to about 6 h after the
last crash (measured: ~368 min instead of ~68). A `netns-<name>` key clears only
once both containers have been up 60 min; `docker-unreadable` clears on the first
pass whose batched inspect succeeds (a pass that had to read the containers one
by one does not clear it). **Known residual (accepted, not tuned):** a *slow* loop can still
get a premature RESOLVED between two of its own crashes (measured on held-out
simulated loops: RESOLVED was the latest word 2.9% of loop time) and is paged
again within roughly 2-4.5 h; a `docker-unreadable` relapse inside 6 h of its
first page is not re-paged; a netns pair flapping at a period over about 60 min
pages ALERT + RESOLVED each cycle.

Two structural fixes shipped with it, both of which had been masking faults:
- **No more fatal early returns.** A failed repair used to `return $false` and
  abort the whole cycle, so one outage blinded the watchdog to inference, backups
  and the bridges. Failures now record into `$script:HealthIssues` and the cycle
  continues; the summary reports `WITH ISSUES: <list>` instead of the old
  unconditional "All health checks passed".
- **Preventive node-key expiry warning.** `Test-TailscaleNodeState` warns once a
  day when the tailscale node key is <14 days from expiring - the root cause of
  the 2026-09-16 outage, which had no detection at all.

> The durable fix for that root cause is **disabling key expiry for this node**
> (or moving to an OAuth client). The 2026-09-16 re-auth reset the clock to ~180
> days, so without it this recurs around 2027-03-15.

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
> `<machine>.<tailnet>.ts.net`, but RDP is off and no SSH server is installed). Until it
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
