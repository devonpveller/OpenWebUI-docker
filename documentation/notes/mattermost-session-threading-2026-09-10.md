# Mattermost session threading — findings (2026-09-10)

Sink for the `mmthread` item. Anything true but out of scope lands here with how
it was measured.

## What the operator reported, and what was actually wrong

> "i haven't had a message on mattermost in nearly a week"
> "In mattermost i wasn't seeing a `bold` text over the channel so i didn't look"
> "the messages are streamed in arbitrarily which also means i can't reply
> without the messaging being confusing"

The sender was never broken. Measured 2026-09-10 against the live server:

- posts ARE landing in `#claude-code` — newest at 18:49 that day
- `AO_MATTERMOST_BOT_TOKEN` authenticates (HTTP 200, `bot-pm`)
- the channel resolves (HTTP 200)

What was wrong is presentation, and it explains both symptoms exactly:

- `#claude-code` has two members, `bot-pm` and `profnovice`. The operator's
  `last_viewed_at` is **NEVER** and `mention_count` is **0**. Nothing in that
  channel had ever mentioned them, so nothing bolded it and nothing pushed.
- `root_id` appeared **nowhere** in `scripts/notify-mattermost.sh`, the
  agent-bridge, or any script. Threading was not switched off — it was never
  built. Every notification was its own top-level post, so there was no thread to
  reply into.

## Residuals — true, measured, not fixed by this item

- **`#claude-code` keeps its 235 posts and its two members.** This item stops
  adding to it; it does not migrate or archive what is there. If those posts
  matter, that is a separate decision.
- **The operator's other surfaces were quiet for the same period, and that is
  NOT a fault.** `#claude-sessions` last saw traffic 2026-09-03; the watchdog's
  Telegram sentinels in the main checkout are dated 2026-09-07. Both are
  consistent with nothing having gone wrong, not with a broken sender — the
  watchdog's own scheduled task ran at 20:07 that evening with result 0.
- **Most of the week's traffic in `#claude-code` was test noise from this
  session's own harness testers**, plus repeated `BACKUP STALE` warnings that are
  a known false positive (`watchdog-findings.md` item 10 — a watchdog run from a
  git worktree looks for backup directories that only exist in the main
  checkout). Three of the last eight posts were that alert. It is real noise
  reaching a real operator and it belongs to the `crashloop` item or its own.
- **The watchdog's operator alerts still go to `#claude-code`.** This item moves
  SESSION chatter only. Whether pages should follow is a separate decision and a
  different audience: a crash-loop page is not session chatter.
- **Nothing here fixes the inbound direction.** The operator can now reply in a
  thread, but an IDE session does not read those replies — that is the
  claude-sessions bridge, and it is what makes this only half a conversation.
  Worth saying plainly: threading makes replying POSSIBLE and does not yet make
  it EFFECTIVE.

## A trap for whoever tests this

There is no staging Mattermost. Tests post to the operator's real channel. Label
every test post and delete it afterwards — the developer left six posts and a
stray root behind and had to clean them up, in the same channel the operator had
just asked to be quieter.
