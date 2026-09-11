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

- **`#claude-code` keeps its history and its two members.** (Deliberately no
  count: the live system offers five defensible ones - 238 live, 236 live
  non-system, 241 DB rows, 239 `total_msg_count`, 231 roots - and an earlier
  draft picked a sixth, 235, which is none of them. Two commit messages then
  enumerated the right figures, called 235 wrong, and left this line standing.) This item stops
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

## THE REAL REASON THE OPERATOR HEARD NOTHING — and it predates this item

`scripts/.mm-notify-sessions` in the main checkout is 37 bytes holding ONE
session uuid, `f233ba99-…`, mtime **2026-07-04 13:18**. The gate at the top of
the notifier is: if that file is non-empty AND we know the session id, the
session must be listed or we exit silently.

So every Claude Code session since 4 July has been suppressed. Measured: across
the last 200 posts in `#claude-code` there are **zero** "finished a turn"
messages. The only IDE traffic still arriving is the permission notifications —
28 of the last 40 — and they arrive *because* they carried no session id and
therefore skipped the gate entirely.

The operator's report was "no Mattermost messages in nearly a week". For
turn-completion pings it has been **over two months**, and the cause is a stale
allowlist, not this item and not the sender.

**AND THIS ITEM NEARLY CLOSED THE LAST GAP.** Recovering the session id from the
message text made the permission notifications subject to the gate for the first
time; an allowlist entry is a 36-char uuid, the recovered id is 8 hex characters,
and they can never be equal. Attempt 2 measured it on the real machine: IDE
traffic to zero. The gate now compares on the same 8-hex prefix, so a full-uuid
entry and a short-form id agree.

**The allowlist file itself is the operator's and is left alone.** Deleting it
would restore turn pings for every session on the machine, which is a choice
about interruption, not a bug fix. It is flagged here so the decision is theirs.

## The id has two forms, and keying on the raw one splits a session in half

The Stop hook supplies a 36-char uuid on stdin; the Notification hook supplies
only the 8 hex characters it formatted into the text. Keyed as-is, one session
opened TWO threads — both announcing the same short id — which breaks the
anchor's first criterion. The canonical key is the 8-hex prefix, because it is
the one form every path can produce.

## THE ITEM IS BLOCKED ON AN OPERATOR DECISION, not on more code

Attempt 4 measured the thing this note had been describing as a risk, using the
operator's REAL allowlist and the REAL live session id: **the branch posts
nothing; the baseline, same file, same message, posts.**

That is not a regression in the fix - it is the fix working. The allowlist says
"only session `f233ba99` may ping" and honours it. The BASELINE was leaking
permission requests past the gate only because they carried no session id to
check. So this item converts a partial silence into a complete one, and it cannot
honestly be said to deliver what its anchor promises while that file names a
session from 2026-07-04.

**Three options, and all three are the operator's:**

1. `rm scripts/.mm-notify-sessions` - every session on the machine pings.
2. Replace its contents with the current session id - only that session pings.
3. Leave it - and accept that IDE notifications stay silent, which is the status
   quo for turn-completions and would become the status quo for permission
   requests too.

Recorded here rather than decided, because it is a choice about how often the
operator wants interrupting, not a defect. The code change is correct either way;
what it is NOT is a fix for the silence, and this note should not have implied
otherwise.

## The fixes that were not fixes

Attempt 4's sharpest finding is about method rather than any single defect: **each
fix had been validated against the case that exposed it, not against the class.**

- The key was made alphanumeric to stop it collapsing to `e`, and the gate that
  consumes the key was left filtering hex. A listed session was then silenced.
- `cut -c1-8` was applied to every id, so two ids sharing an eight-character
  prefix - `testsess-…-aaa` and `-bbb` - collided where the previous filter had
  kept them apart. One collision class traded for another.
- The temp file was given a unique name to stop a race, and the read-modify-write
  underneath it was left alone. Measured: no improvement.

What actually closed them: ONE normaliser called by both the key and the gate;
truncation only for strings long enough to be a uuid; and an append-only map read
last-wins, which removes the rewrite and therefore the race rather than renaming
it. **A rename is not a synchronisation primitive; not rewriting the file is.**

## A trap for whoever tests this

There is no staging Mattermost. Tests post to the operator's real channel. Label
every test post and delete it afterwards — the developer left six posts and a
stray root behind and had to clean them up, in the same channel the operator had
just asked to be quieter.
