# Mattermost MCP — Claude Code ⟷ Mattermost (read + post)

A **permanent, dependency-free** bridge so a Claude Code session can READ operator messages and
POST updates to the self-hosted Mattermost with native tools — replacing ad-hoc
`docker exec … curl` one-liners. This is the durable version of the two-way loop sketched in
[`documentation/implementation-guide/claude-code-mattermost-bridge/DESIGN.md`](../../documentation/implementation-guide/claude-code-mattermost-bridge/DESIGN.md).

## What it is

- [`server.py`](server.py) — an MCP **stdio** server (JSON-RPC over stdin/stdout), **stdlib only**
  (urllib/json — no pip install). Registered in the repo [`.mcp.json`](../../.mcp.json) as
  `mattermost`, so every Claude Code session in this repo gets the tools.
- [`mm.py`](mm.py) — a tiny CLI over the same functions, for a shell (and for a session *before*
  the MCP server has loaded, since MCP servers register at session start).

## Tools

| tool | what |
|------|------|
| `mattermost_read` | recent posts from a channel (default `#claude-code`), oldest→newest, each tagged `OPERATOR:<user>` vs `me(bot)`. Supports `limit` (1–60) and `since` (ms-epoch) to poll only new messages, and `exclude_self`. |
| `mattermost_post` | post to a channel (default `#claude-code`); optional `thread_id` (root post id) to reply in-thread. Posts as the bot, tagged `props.from_claude` (the claude-sessions bridge's follow/auto-wake scanner uses the tag to never wake a session on its own posts). |
| `mattermost_channels` | list/resolve channel names → ids (optional `query`). |

## Identity (since 2026-07-15)

Like the claude-sessions bridge, the server prefers the **dedicated bot-claude token**
(`CLAUDE_MM_BOT_TOKEN`, searched in `MM_ENV_FILE` then the repo-root `.env`) so Claude's own
posts are visually distinct from agent-org's `bot-pm` — and so a bridge session's *follows*
(see `scripts/claude-sessions-bridge/`) can tell Claude's posts apart from the bots it waits
on. It falls back to `AO_MATTERMOST_BOT_TOKEN` (bot-pm) when the dedicated token is absent or
rejected (401/403); network errors never switch identity. Override order:
`MM_TOKEN` env > `AO_MATTERMOST_BOT_TOKEN` env > `CLAUDE_MM_BOT_TOKEN` from env files >
`AO_MATTERMOST_BOT_TOKEN` from `MM_ENV_FILE`. `MM_TOKEN_KEY` renames the dedicated key.

## Pull vs. trigger (the listener)

The MCP tools + `read`/`post` are **pull**: they let the agent read and post, but an operator
message does **not** trigger the agent. To make a message an *event*, the CLI has a **`wait`**
command (the LISTENER): it blocks until a new operator (non-bot) message arrives after `--since`,
prints it, and exits 0 (timeout → exit 2). Run it as a **background task** — its completion is the
wake-up, so:

```
you message  ->  the backgrounded `wait` exits  ->  the agent is re-engaged  ->  reads + replies  ->  re-arms `wait`
```

```
python scripts/mattermost-mcp/mm.py wait --since <last-ms-epoch> --timeout 2700 --interval 15
```

This is a **session-scoped** listener: it works while a Claude Code session/loop is running. A
**persistent** listener that survives the session ending (a message reaching a *fresh* Claude) needs
a small always-on bridge service that spawns `claude -p --resume <session>` per message — the
DESIGN's Phase-1/§5 "channels" model; not yet built.

## Config (env, all optional)

| var | default |
|-----|---------|
| `MM_URL` | `http://localhost:8065` |
| `MM_ENV_FILE` | `agent-org/docker/.env` (in this repo) |
| `MM_TOKEN` | else resolved per the *Identity* section above (bot-claude preferred, bot-pm fallback) |
| `MM_TOKEN_KEY` | `CLAUDE_MM_BOT_TOKEN` (the dedicated-identity env-file key) |
| `MM_DEFAULT_CHANNEL` | `#claude-code` channel id |

**The bot token is read from `.env` at run time — never hardcoded, never committed** (same
non-negotiable as `scripts/notify-mattermost.sh`). The `.mcp.json` entry carries no secret.

## Use

MCP tools (after a session reload): `mattermost_read`, `mattermost_post`, `mattermost_channels`.

CLI (any time):
```
python scripts/mattermost-mcp/mm.py read --limit 5 --exclude-self
python scripts/mattermost-mcp/mm.py post "message here"
python scripts/mattermost-mcp/mm.py read --since 1783787469236   # only newer than that ts
```

## Relationship to the rest

- **Outbound-only** notifier `scripts/notify-mattermost.sh` (+ the Stop hook) still pings
  `#claude-code` when a turn ends — complementary; this adds the READ side and thread-aware posting.
- This is **not** the full "channels" push model (operator drives a live session with a permission
  relay) from the DESIGN's Phase-1/§5 — that's the next step. This gives reliable, non-janky I/O so
  an all-day loop can read replies and report progress on its own cadence.
