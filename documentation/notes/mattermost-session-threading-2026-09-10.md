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

- `#claude-code` has two members, `bot-pm` and `profnovice`. `mention_count` is
  **0** — nothing in that channel had ever mentioned them, so nothing bolded it
  and nothing pushed. `last_viewed_at` read **NEVER** when measured; by 20:25 it
  read that evening's timestamp, so the operator opened the channel between the
  measurement and the item being proposed. Mattermost keeps no history of the
  field, so the original reading cannot be re-derived — it is recorded here as
  "measured at the time", not as something a reader can reproduce. The
  `mention_count` half, which is the load-bearing one, reproduces exactly.
- `root_id` appeared nowhere in **`scripts/notify-mattermost.sh`**. It was NOT
  absent from the repo: the agent-bridge threads its own posts
  (`agent-org/agent-bridge/app/adapters/mattermost.py:100`), and so do
  `scripts/claude-sessions-bridge/bridge.py:499` and
  `scripts/mattermost-mcp/server.py:200`. The first version of this note claimed
  it appeared "nowhere in ... the agent-bridge, or any script", which was a
  repo-wide universal drawn from a grep over two wrong paths — the bridge lives
  at `agent-org/agent-bridge/`, not `agent-org/bridge/`. **Refuted in test,
  attempt 1.** The true and narrower statement is the point anyway: the IDE
  notifier was the one sender in the system that did not thread.

## Residuals — true, measured, not fixed by this item

- **`#claude-code` keeps its 235 posts and its two members.** This item stops
  adding to it; it does not migrate or archive what is there. If those posts
  matter, that is a separate decision.
- **The operator's other surfaces were quiet for the same period, and that is
  NOT a fault.** `#claude-sessions` last saw traffic 2026-09-03; the watchdog's
  Telegram sentinels in the main checkout are dated 2026-09-07. Both are
  consistent with nothing having gone wrong, not with a broken sender — the
  watchdog's own scheduled task ran at 20:07 that evening with result 0.
- **The traffic mix, counted rather than characterised.** Of the 40 live posts
  in `#claude-code`: **28 are real permission notifications**, 3 are harness
  tester noise, and the rest are watchdog warnings. The first version of this
  note said "most of the week's traffic was test noise from this session's own
  testers" — refuted in test, attempt 1, and again by counting: 3 of 40. The
  `BACKUP STALE` warnings are still a known false positive
  (`watchdog-findings.md` item 10 — a watchdog run from a git worktree looks for
  backup directories that only exist in the main checkout), and still belong to
  the `crashloop` item. What this does change is the weight of the finding
  below: the dominant message class is the one the operator must answer.
- **The watchdog's operator alerts still go to `#claude-code`.** This item moves
  SESSION chatter only. Whether pages should follow is a separate decision and a
  different audience: a crash-loop page is not session chatter.
- **Nothing here fixes the inbound direction.** The operator can now reply in a
  thread, but an IDE session does not read those replies — that is the
  claude-sessions bridge, and it is what makes this only half a conversation.
  Worth saying plainly: threading makes replying POSSIBLE and does not yet make
  it EFFECTIVE.

## The Notification hook, and why this item nearly missed its own point

Found in test, attempt 1. `.claude/settings.local.json` invokes this script for
the "Claude needs your permission" messages — **28 of the last 40 notifications,
and precisely the ones the operator has to answer.** That hook reads the hook
JSON itself and then calls the notifier with `</dev/null`, so the notifier saw no
`session_id`, took the deliberate "post FLAT" path, and the dominant message
class would have arrived unthreaded in the channel the operator had just asked to
be quieter. The item would have shipped succeeding at its test cases and failing
at its goal.

That settings file is **operator-local and gitignored**, so this script cannot
require it to change. The notifier now takes the session id from, in order:
`MM_SESSION_ID`, the hook JSON on stdin, and finally the message text itself —
the hook formats `session <8 hex>` into what it passes, so the id is recoverable
even when stdin is gone. Proven with the hook's exact shape: two permission
requests, stdin closed, both threaded under one root.

## A reply in this channel starts a NEW headless session

Demonstrated unintentionally during attempt 1: the operator replied "test
received" to a test post, and the claude-sessions bridge picked it up and started
headless session `9fae8f75` under it. So in `#claude-sessions`, a reply is input
to the BRIDGE, not to the IDE session that posted. That is sharper than "the
session cannot read your reply" — the reply is consumed, by something else, and
it looks like an answer.

It is out of scope here and it is the strongest argument for doing the inbound
half deliberately rather than letting the channel choice imply it.

## A trap for whoever tests this

There is no staging Mattermost. Tests post to the operator's real channel. Label
every test post and delete it afterwards — the developer left six posts and a
stray root behind and had to clean them up, in the same channel the operator had
just asked to be quieter.
