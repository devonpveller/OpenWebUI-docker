# Test plan — `mmthread` (one Mattermost thread per Claude Code session)

Anchor: `queue.ps1 -Show -Id mmthread`.
Branch: `work/mmthread`. One file changes plus a `.gitignore` line.

**What changed, in one line:** Claude Code notifications move from flat top-level
posts in `#claude-code` to one thread per session in `#claude-sessions`, posted as
`bot-claude`, every message mentioning the operator.

## BEFORE YOU RUN ANYTHING — two rules

**This item posts to the operator's REAL Mattermost.** There is no staging server.
So:

1. **Label every test post** so a human scrolling the channel knows it is yours:
   start the message with `TEST (mmthread tester <your-id>, ignore)`.
2. **Delete every post you create**, and prove the channel is clean at the end by
   reading it back. The developer left six test posts and a stray root and had to
   remove them; the operator had just asked for less noise in that channel. Your
   cleanup section must show the newest visible post is from **2026-09-03**, which
   is what the channel looked like before this item touched it.

Use throwaway session ids (`testsess-<yourid>-1`), never a real one, and point the
state file somewhere disposable — the script derives its paths from its own
location, so running it from your worktree writes your worktree's state.

## T1 — the shape, against the real API

Run three notifications: two from one fake session id, one from another.

```bash
cd <worktree>
rm -f scripts/.mm-session-threads
S1="testsess-<yourid>-aaa"; S2="testsess-<yourid>-bbb"
echo "{\"session_id\":\"$S1\"}" | bash scripts/notify-mattermost.sh "TEST (mmthread tester, ignore) A1"
echo "{\"session_id\":\"$S1\"}" | bash scripts/notify-mattermost.sh "TEST (mmthread tester, ignore) A2"
echo "{\"session_id\":\"$S2\"}" | bash scripts/notify-mattermost.sh "TEST (mmthread tester, ignore) B1"
```

Read the channel back through the API and print `root_id` for each post.

PASS: session A has ONE root and TWO replies carrying that root's id; session B has
its OWN root and one reply; B's reply is not under A's root.
FAIL: any message is a top-level post, or the two sessions share a root.

## T2 — it is genuinely RED on the current code

The point of the item is that today every message is top-level.

**The parent hardcodes `#claude-code` and the agent-org bot**, so running it
verbatim violates T4 and T11. Retarget its `CHANNEL` and token to
`#claude-sessions` and `CLAUDE_MM_BOT_TOKEN` in a SCRATCH COPY, say in your
report that you did, and delete the posts like any other. Two attempts reported
this as a plan defect before it was written down.

PASS: parent produces three roots and zero replies; the branch produces two roots
and three replies.
FAIL: the parent already threads — then this item is not fixing what it claims.

## T3 — the mention is on every post

Every post the notifier creates must contain the operator's mention, because an
unread channel stays unread and a mention is what survives that. Count them.

PASS: mentions == posts created.
FAIL: any post without one — including the announce root.

## T4 — identity and destination

PASS: every post's `user_id` resolves to `bot-claude`
(`cdm6ctf1wtdyzroagu7c5hymqh`), and every post is in `#claude-sessions`
(`6z9khgkdd7df9q454be6fimw1h`). **Zero posts land in `#claude-code`** — read that
channel too and confirm its newest post is unchanged by your run.
FAIL: posts as `bot-pm`, or anything reaches `#claude-code`.

## T5 — state survives separate processes

Each notification is its own process; the thread map is the only thing carrying
the root between them.

PASS: after T1, `scripts/.mm-session-threads` holds exactly two lines, one per
session id, each with a post id that resolves through the API.
FAIL: empty, or a session id appearing twice.

## T6 — a dead root does not wedge the session

Delete session A's root post through the API, then send another notification from
session A.

PASS: the notifier notices the failure, drops the stale mapping, opens a NEW root
and posts the message under it. The session keeps talking.
FAIL: the message is lost, or the script hangs, or it keeps retrying a dead root
forever.

## T7 — THE CONTRACT: it never breaks a turn

This runs as a Stop hook. A down Mattermost must not fail the caller. Drive each
of these and assert `echo $?` is `0` every time, with no stderr reaching the
caller:

- no token at all (point `ROOT_DIR` at an empty dir)
- token present, Mattermost unreachable (stop the container, or point `API` at a
  closed port)
- a session id that is not in the map and an unwritable state file
- garbage on stdin instead of hook JSON
- no arguments at all — note this one forces the DEFAULT message, which cannot
  carry the label this plan otherwise mandates. Send it, then delete it like the
  rest; do not skip the case to keep the labelling rule intact.

PASS: exit 0 in every case, and the whole run finishes inside
`MM_DEADLINE_SECS` (default 10) plus a second or so of process overhead —
measured 10.2–11.4s against a black hole in attempt 2. The number that matters is
the HOOK timeout: 15s on Stop and 20s on Notification in the operator's
`.claude/settings.local.json`. **This case used to say "the 8s curl bound", which
was the pre-threading figure and was never updated when threading made it up to
four calls.** Check the budget is honoured, not a fixed 8s: set
`MM_DEADLINE_SECS=3` and confirm later calls are SKIPPED rather than started.
FAIL: any non-zero exit, any stderr reaching the caller, or a run that could
exceed 15s.

## T8 — the allowlist still works

`scripts/.mm-notify-sessions` gates which sessions ping. It is pre-existing
behaviour and must not have been broken.

PASS: with a non-empty allowlist not containing your test session, the script
posts NOTHING and exits 0. With the session listed, it posts.
FAIL: it posts for an unlisted session — that would ping the operator for every
session on the machine.

## T9 — the watchdog is untouched

PASS: `git diff` shows no change to anything under `scripts/checks/`.
**That is the whole case.** It used to also ask you to run
`verify-crashloop-detection.ps1`, which does not exist on this line — it lives on
the unmerged `crashloop` branch, so the instruction was unexecutable and two
attempts reported it as a plan defect.
FAIL: anything under `scripts/checks/` changed by this item.

## T10 — claims in the commit message and the findings note

Re-derive every figure and every claim, from the command or file it cites — not
from the prose. Specifically: the `last_viewed_at = NEVER` and `mention_count = 0`
measurements that justify the whole item, the channel ids, the bot id, and the
claim that `#claude-sessions` had been silent since 2026-09-03.

FAIL: any figure that does not reproduce, any citation that does not resolve, or
any universal ("every", "always", "all N") you can find a counterexample to.

## T11 — nothing of the operator's was touched

PASS: `git status` is clean in your worktree; the main checkout shows only its
pre-existing entries; the channel contains none of your posts; the
`#claude-code` channel is unchanged.

## T12 — one session, ONE thread, whichever hook posted

The Stop hook supplies a 36-char uuid on stdin; the Notification hook supplies
only the 8 hex characters it printed into the message text. Attempt 2 drove both
for one session and got TWO threads, both announcing the same short id.

Drive both shapes for the same session:

```bash
UUID="beef0011-26f9-44c5-b923-ba0597393188"
bash scripts/notify-mattermost.sh "🔔 Claude Code (ai-stack) session beef0011 - TEST …" < /dev/null
echo "{\"session_id\":\"$UUID\"}" | bash scripts/notify-mattermost.sh "TEST …"
```

PASS: the map holds ONE line, and both posts are replies under one root.
FAIL: two roots, or two map entries.

## T13 — the allowlist must not silence the messages this item exists to deliver

**The case that nearly shipped an outage.** `scripts/.mm-notify-sessions` gates
pings. The gate only fires when a session id is KNOWN — so before this item, the
Notification hook's permission requests skipped it entirely and posted. Making
the id recoverable from the text subjects them to the gate for the first time,
and an allowlist entry is a 36-char uuid which can never equal an 8-hex id.
Attempt 2 measured the consequence on the operator's real machine: IDE traffic to
zero.

Use a PRODUCTION-SHAPED allowlist — a full 36-char uuid, which is what the real
file contains:

```bash
printf '%s
' "beef0011-26f9-44c5-b923-ba0597393188" > scripts/.mm-notify-sessions
```

PASS: a Notification-shaped call for that session POSTS; a Stop-shaped call for
it POSTS; a call for a session NOT in the file posts nothing and exits 0.
FAIL: the allowed session is silenced — that is the outage.

## T14 — a message cannot hijack another session's thread

Attempt 2 proved a body containing `session <8 hex>` steers the post into that
session's thread. Check the hook's own prefix still wins when both are present,
and say plainly in your report how bad the hijack is.

## T15 — one session, started twice at once

Three notifications from the SAME session id, fired concurrently, must not open
three threads. The map race is fixed (append-only, last-wins) but the announce is
not synchronised, and attempt 5 measured three roots.

```bash
for i in 1 2 3; do ( echo '{"session_id":"race0001-0000-0000-0000-000000000000"}'   | bash scripts/notify-mattermost.sh "TEST (mmthread tester, ignore) $i" ) & done; wait
```

PASS: one root, one map line, three replies.
FAIL: more than one root — report it as a finding with the count; this is a known
residual and the plan records it so a future fix has a case waiting.

## Out of scope for this plan

The inbound direction (replying from Mattermost into a session), Telegram, the
watchdog's own operator alerts, the known-false `BACKUP STALE` alert, and
migrating the posts already in `#claude-code`.
