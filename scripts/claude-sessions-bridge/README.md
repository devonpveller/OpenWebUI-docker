# Claude-Sessions bridge — Mattermost threads ⟷ Claude Code sessions

The inbound half of
[claude-code-mattermost-bridge/DESIGN.md](../../documentation/implementation-guide/claude-code-mattermost-bridge/DESIGN.md)
(P-CCB.1 "thread = session" + the P-CCB.3 mid-turn approval relay), built 2026-07-13.

## What it does

- Watches the **#Claude-Sessions** channel (`claude-sessions`, id `6z9khgkdd7df9q454be6fimw1h`)
  on the self-hosted Mattermost (`http://localhost:8065`, the agent-org server).
- **Root post → new Claude Code session** (headless `claude -p`, working dir = this repo).
  The session id is captured and stored; **every reply in the thread resumes that session**
  (`--resume <id>`), so context carries across messages. One thread = one line of work.
- **Quiet acknowledgments:** ordinary replies get a **⏳ reaction** on your message (flipping
  to ✅ / ❌ when the turn ends) — no "resuming…" post per reply. Informative text posts appear
  only at transitions: 🧵 new session, 🔗 handoff/fork, 🎛️ directive switches.
- **Live progress while the turn runs:** a progress post appears once the session takes its
  first tool action, then is edited in place with a compact activity log (`🔧 Read —
  config.yaml`, `💬 <narration>`…) — watchable from a phone without notification spam; it
  closes with "✔ turn complete". Trivial replies produce no progress post at all — just your
  message → result.
- **Turn result → posted back into the thread** when ready (async; long turns are fine).
- **Approvals move the effort along:** the session runs in the *default* permission mode —
  reads and safe commands are automatic, but any gated tool (file writes, mutating Bash,
  pushes…) fires a `--permission-prompt-tool` MCP call that `approval_server.py` turns into a
  **🛑 Approval needed** post in the thread. The turn blocks until the operator replies
  **`approve`** or **`deny [reason]`** — from desktop or phone. Timeout / Mattermost outage /
  any relay error ⇒ **deny (fail-closed)**.

```
#claude-sessions
  └─ thread (root post = the task)
       ├─ 🧵 bridge ack
       ├─ 🛑 Approval needed — tool `Bash` … reply approve / deny
       ├─ operator: approve
       ├─ ✅ Approved — running `Bash`.
       └─ <final result> · session 75265312 · 35s · $0.01
```

## Files

| file | role |
|---|---|
| `bridge.py` | poller + per-thread workers + session table + follow registry (`state/state.json`) |
| `approval_server.py` | stdio MCP server loaded into each turn; relays permission prompts to the thread; exposes the `follow_thread`/`unfollow`/`list_follows` tools |
| `test_follows.py` | unit tests for the follow/auto-wake matcher (`python test_follows.py`) |
| `state/` | *(gitignored)* session map, `bridge.log`, audit logs (`audit.jsonl`, `approvals.jsonl`), per-turn MCP configs, pending `follow-req-*.json` handoffs |

*(A `run-bridge.ps1` supervisor existed briefly on 2026-07-13 and was removed the same day —
superseded by the Scheduled Task, which calls `pythonw.exe` directly and supplies the
restart-on-failure behavior without any execution-policy changes.)*

## Running (Scheduled Task — registered 2026-07-13, this is the production path)

The **`claude-sessions-bridge`** Scheduled Task runs the bridge at logon via `pythonw.exe`
(no console window), restarts it up to 99× (1-min interval) if it crashes, and has no
execution time limit. Manage it with:

```powershell
Start-ScheduledTask  -TaskName "claude-sessions-bridge"
Stop-ScheduledTask   -TaskName "claude-sessions-bridge"   # then kill pythonw if needed
Get-ScheduledTask    -TaskName "claude-sessions-bridge"
```

Reliability behavior (all built into `bridge.py`):

- **Single-instance lock** (localhost port `48291`, `BRIDGE_LOCK_PORT`) — a second copy exits
  immediately, so a manual start can never double-process messages alongside the task.
- **Waits for Mattermost at startup** — at logon the bridge typically starts before Docker
  brings Mattermost up; it retries with backoff and only falls back off the dedicated token on
  a real 401/403, never on a connection error.
- **Catch-up transparency** — messages posted while the bridge was down are still processed
  (the `last_seen` cursor persists); threads get a "🔁 Bridge back online — catching up" note
  when the backlog is >15 min old.
- **File log**: `state/bridge.log` (5 MB rotation) — the primary record under the task.

Manual foreground run (debugging): `& .venv\Scripts\python.exe scripts\claude-sessions-bridge\bridge.py`
(exits if the task instance holds the lock — stop the task first).

## Governance (DESIGN.md §7 — defense in depth, fail-closed)

1. **Operator allow-list** — only posts from `BRIDGE_OPERATORS` (default `profnovice`) are
   processed; everything else is logged and dropped. Bridge/bot posts are tagged
   (`props.from_bridge`) and never re-ingested.
2. **Approval level** — default **`auto`** (operator choice 2026-07-13): the classifier-backed
   mode — routine actions run without asking; flagged/risky ones still relay to the thread.
   Change the default with `BRIDGE_PERMISSION_MODE`, or per thread with a **`mode: <level>`**
   directive (`auto`, `acceptEdits`, `manual`, `dontAsk`, `plan`) — a directive-only message
   switches without running a turn. **`bypassPermissions` is refused** both via directive and
   flag — that's the bridge's hard floor. Note: headless `auto` is somewhat more conservative
   than interactive auto (e.g. first-time file writes may still ask). `--setting-sources
   user,project` excludes `settings.local.json`, so allow-rules accumulated in interactive
   sessions don't widen what a remote message can do.
3. **Hard gate** — every gated tool call stops for an explicit in-thread `approve`;
   deny and timeout both return deny to Claude, which then adapts or wraps up.
4. **Budget rails** — `--max-budget-usd` per turn (default $50). On a subscription nothing is
   billed — this is a runaway-turn backstop (the estimate tracks Max-quota burn), sized to
   never fire on legitimate work; a budget-killed turn says so in-thread with recovery options.
   Set `BRIDGE_MAX_BUDGET_USD=""` to disable. Turn timeout: 2h.
5. **Audit** — `state/audit.jsonl` (messages, turns, costs) + `state/approvals.jsonl`
   (every approval request and verdict).
6. **Fail-closed** — bridge down ⇒ nothing executes; token missing ⇒ nothing posts; the
   Mattermost token is read from `agent-org/docker/.env` at run time and never committed.

## Credentials (nothing to configure)

- **Claude:** the bridge spawns your installed `claude` CLI as your Windows user, so it inherits
  the login Claude Code already has on this machine (currently claude.ai / Max subscription —
  check with `claude auth status`). Turns draw on the subscription; the `$` figure in result
  footers is the CLI's estimate, not a separate charge. To bill an API key instead, set
  `ANTHROPIC_API_KEY` in the bridge's environment — the CLI prefers it over the stored login.
  Logging out of Claude Code on this machine de-auths the bridge too.
- **Mattermost:** bot token read at run time from `agent-org/docker/.env`. The bridge prefers a
  **dedicated identity**: if `CLAUDE_MM_BOT_TOKEN` exists in that file it posts as that bot
  (intended: `bot-claude`); otherwise it falls back to `AO_MATTERMOST_BOT_TOKEN` (the agent-org
  `bot-pm`). Startup logs which identity is active (`posting as @…`). Never committed.

### Switching to the dedicated `bot-claude` identity (one-time admin step)

Creating a bot account needs Mattermost admin rights (bot-pm's token can't — 403). Either:

1. **UI (~60s):** System Console → Integrations → Bot Accounts → *Add Bot Account* →
   username `bot-claude` → copy the token → append `CLAUDE_MM_BOT_TOKEN=<token>` to
   `agent-org/docker/.env` → add the bot to the team (the bridge self-joins the channel on
   startup once it's a team member) → restart the bridge.
2. **mmctl local mode:** enable `ServiceSettings.EnableLocalMode` in the container's
   `config.json`, restart Mattermost, `mmctl --local bot create …` + token + team add, then
   revert local mode. (Deliberately not automated — it's a temporary admin-socket enable.)

## Config (env vars, all optional)

See the docstring at the top of `bridge.py` for the full table. The ones you'll actually
touch: `BRIDGE_MODEL` (e.g. `haiku` for cheap tests; default = the CLI's default model),
`BRIDGE_OPERATORS`, `BRIDGE_APPROVAL_TIMEOUT` (default 1800 s), `BRIDGE_MAX_BUDGET_USD`,
`BRIDGE_REPO` (working dir for sessions). `BRIDGE_ALLOW_SELF=1` is **smoke-test only** — it
lets the bot's own posts drive sessions.

## Conventions in the channel

- **New thread = new session.** Put the whole task in the root post (intent + constraints).
- **Reply in the thread** to continue that session; it remembers prior turns.
- **`approve` / `deny [reason]`** while a 🛑 request is pending. Anything else you post
  mid-turn is queued as the session's next prompt.

### Choosing the model per thread

- Every result post ends with **`[model:<id>]`** — the model that *actually* ran the turn
  (from its usage report), so behavior can be tracked per model.
- **`model: <alias-or-id>`** at the start of any message (colon required) sets the model for
  that thread's turns from then on — e.g. `model: haiku`, `model: sonnet`, `model: fable`.
  Persisted per thread; wins over `BRIDGE_MODEL`; a directive-only message just switches it
  (🎛️ ack, no turn). Precedence: thread `model:` > `BRIDGE_MODEL` env > CLI default
  (`~/.claude/settings.json` → currently `claude-fable-5[1m]`).
- Combine with handoff: put `model: …` on the first line, `handoff`/`fork` after it.

### Session handoff (desktop → Mattermost)

Bridge sessions and interactive sessions are the same on-disk objects
(`~/.claude/projects/<project>/<session-id>.jsonl`), so a **root post** can attach a thread to
an existing session instead of starting fresh:

- `handoff <session-id> [first message]` — continue that session from the thread (same id).
  Use when you're done driving it interactively — two writers on one session id diverge.
- `fork <session-id> [first message]` — continue a **forked copy** (new id, full context);
  the original session is untouched. Use when the source session may still be open on the
  desktop. (`resume`/`attach` are aliases of `handoff`.)

Omitting the first message asks the session to summarize where it left off. Get a session's id
from `/status` in the running session, the `/resume` picker, or the newest
`~/.claude/projects/d--Open-WebUI-ai-stack/*.jsonl`. The reverse direction needs no feature:
any bridge session can be picked up interactively with `claude --resume <session-id>`.

**Identifying sessions:** post **`sessions`** as a message in the channel — the bridge itself
replies with the inventory: full session id, age, title, 🧵mm tag for bridge threads. Copy an
id straight into `handoff <id>` / `fork <id>`. Variants: `sessions 100` (deeper listing),
`sessions <text>` (**searches titles AND full transcript content** — the reliable way to find
a session by any phrase you remember). Same listing on the host:
`python scripts/claude-sessions-bridge/sessions.py [filter] [--limit N]`.

Caveat: titles here are each session's first substantial typed message; the `/resume` picker
*derives* its display names with its own heuristic, so the two won't always match verbatim —
when in doubt, search by a remembered phrase, not by the picker's name. Additionally: each
bridge thread's first message becomes its `title` in `state/state.json`, bridge sessions are
named `mm <title>` in the `/resume` picker, result footers carry the id prefix, and a
`state.json` thread key opens as `http://localhost:8065/<team>/pl/<post-id>`.

## Follows — auto-wake on replies in other threads (2026-07-15)

A session can **subscribe to any Mattermost thread (or whole channel)** and be woken —
resumed with the new posts as its next prompt — when someone replies there *after its turn
ended*. This is how a session converses with an **asynchronous counterpart** (agent-org's
`bot-pm`, another bot, a human in a project channel) without polling or dying: post the
message, follow the thread, end the turn; the bridge does the waiting.

**How it flows:**

1. Mid-turn, Claude calls the approvals MCP tool **`follow_thread`** (`channel`, optional
   `thread_id` = the root post id — `mattermost_post` returns it; a reply id is normalized to
   its root). Optional: `wake_on` (usernames whose posts count), `note` (echoed back on wake),
   `expire_hours` (default 48, max 336), `max_wakes` (default 20), `one_shot`.
2. The tool verifies the bot can **read the target channel** (self-joins public channels; if
   it can't, it tells Claude to ask for an `/invite`) and drops an atomic
   `state/follow-req-*.json` handoff; the bridge ingests it on the next poll (~4 s) and posts
   a **📡 Following** confirmation in the session's thread.
3. The bridge polls every followed channel alongside `#claude-sessions`. A matching new post →
   a **📡 Auto-wake** note in the session's thread (with ⏳→✅ reactions like a normal turn) and
   the session resumes with the new messages quoted in the prompt (batched per poll, capped at
   10 posts / 1800 chars each).
4. Ending: `unfollow` tool (or automatically at expiry / wake cap / `one_shot`), each with an
   in-thread 📡 note. Nothing expires silently.

**What can never wake a session** (loop guards): the bot's own posts, posts tagged
`props.from_bridge` (bridge/approval traffic), `props.from_claude` (anything Claude sent via
the mattermost MCP — holds even if that MCP falls back to the bot-pm token), webhook posts,
and system posts. Everything is capped: wake cap, expiry, and the wake prompt tells Claude the
wake was automatic so it doesn't wait on an operator who isn't there.

**Operator surface:** post **`follows`** in the channel → inventory of active follows
(target link, wake filter, wakes used, expiry, owning session). **`unfollow <fw-id>`**
anywhere (or **`unfollow all`** inside a session's thread) is the kill switch. Everything is
audited (`follow_registered` / `follow_wake` / `follow_dropped` in `audit.jsonl`).

**Known limits:** wakes queued in memory are lost if the bridge dies between the wake and the
turn (the `last_seen` cursor has already advanced — the reply is still in the thread, just
un-acted-on). Posts made *between* your `mattermost_post` and the `follow_thread` registration
don't trigger (only posts newer than registration count). Agent-org's `bot-pm` currently
**ignores all bot posts** (its event gateway drops `props.from_bot`), so bot-pm won't *answer*
bot-claude until agent-org allow-lists it — following bot-pm's activity in effort threads
works regardless.

## Channel hygiene

Keep **bot-pm out of #claude-sessions**: agent-org's NL intake treats a bare `approve` as an
agent-org command and answers in the thread ("nothing's awaiting your approval") — confusing
cross-talk with this bridge's approval flow. The operator removed bot-pm from the channel on
2026-07-13; only `@bot-claude` and operators belong here.

## Known limits (v1)

- Sessions are bound to this repo (`BRIDGE_REPO`); multi-repo routing is the DESIGN.md §9
  expansion.
- One approval at a time per turn (Claude Code serializes permission prompts — fine).
- The bridge polls (default 4 s); no websocket yet.
- Verdict words (`approve/deny/yes/no/ok/stop…`) posted while a turn is running are consumed
  as verdicts, not prompts — phrase mid-turn steering as full sentences.
