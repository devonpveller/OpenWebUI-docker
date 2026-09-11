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

## T16 — an unidentifiable session is not on the list

The defect attempt 5 fixed, which attempt 6 had to write its own case for because
the plan gained a case for a known RESIDUAL and none for the change that round
actually made. **A plan revised for an attempt must cover that attempt's change.**

With a one-line allowlist naming some other session, drive an id that normalises
to nothing — `-----`, `::::`, and the same via `MM_SESSION_ID` — plus a call with
no id at all.

PASS: all silent, exit 0, channel unchanged. Then remove the allowlist and drive
`-----` again: it POSTS, because with no list there is nothing to be absent from.
FAIL: an unidentifiable session posts while a list exists — that is the gate
being bypassed by anything that fails to name itself.

## T15 — one session, started twice at once

Three notifications from the SAME session id, fired concurrently, must not open
three threads. **This is no longer a known residual — attempt 9 added a per-session
`mkdir` lock around the lookup-and-announce, so a failure here is a REGRESSION, not
a finding.** Attempts 5 and 8 both measured three roots; do not accept a green
without having seen this go red on the parent.

PREFER THE SHIM, NOT THE REAL CHANNEL. A concurrency case that fails posts three
roots to the operator's channel, and the whole point is that it might. Copy the
script into a throwaway root (it derives `ROOT_DIR` from its own location, so it
will read that root's `.env` and write that root's state), put a fake `curl`
earlier on `PATH` that records each call's `root_id` and echoes
`{"id":"<unique>"}` then `201`, and count roots from the log. A call with an EMPTY
`root_id` is an announce. Remember the trap: a `PATH` entry written `C:/Users/...`
is silently ignored by Git Bash — it must be `/c/Users/...`, or your shim never
runs and you will be measuring the real thing.

Run it against the PARENT too. A concurrency case that has not been seen to fail
is not evidence of a lock.

PASS: one root, one map line, N replies all under that one root — at 3 concurrent
AND at 10. The parent must show N roots at the same N.
FAIL: more than one root on the branch; or the parent also showing one, which
means your harness is not exercising what you think it is.

## T18 — the lock cannot become a new way to go silent

A lock with no expiry is a single point of silence, which is the exact shape of the
two-month outage this whole item exists to fix. Four cases, all in the shim lab:

1. **Stale, readable.** `mkdir scripts/.mm-session-threads.lock.<key>` and write an
   old epoch into its `at`. PASS: the run breaks it, posts, exits 0.
2. **Stale, unreadable.** Same but create NO `at` file (a holder that died between
   its `mkdir` and its write). PASS: same.
3. **Held by something alive.** Take the lock and hold it past the wall-clock
   budget. PASS: the run gives up waiting and posts ANYWAY — unthreaded is
   acceptable, silent is not — and still exits 0.
4. **Different sessions do not queue.** Three DIFFERENT session ids concurrently
   must still produce three roots. A global lock would pass every case above and
   fail this one.

PASS: every case exits 0 and sends its message; no lock directory survives the run.
FAIL: any case where the script exits non-zero, sends nothing, or leaves a lock
behind — and specifically any case where waiting eats the budget so completely that
no post is attempted.

## T17 — the removed allowlist's backup cannot be swept into a commit

Attempt 7 found `scripts/.mm-notify-sessions.removed-2026-09-11.bak` untracked and
NOT ignored: `.gitignore` covered only the exact path `scripts/.mm-notify-sessions`,
so a broad `git add` in the operator's checkout could commit a session uuid. Do not
take the fix on the commit message's word.

Run, in YOUR worktree (never in the operator's checkout, and never `git add` there):

    git check-ignore -v scripts/.mm-notify-sessions.removed-2026-09-11.bak
    touch scripts/.mm-notify-sessions.anything && git status --porcelain scripts/ ; rm scripts/.mm-notify-sessions.anything

PASS: `check-ignore` names the new `.gitignore` rule, and a freshly created
`scripts/.mm-notify-sessions.*` file does NOT appear in `git status`.

FAIL: the file is still untracked-and-visible; OR the rule is wide enough to hide
something that should be tracked — check that `scripts/.mm-notify-sessions` itself
is the only other path the pattern can reach, and that no TRACKED file matches it
(`git ls-files scripts/ | grep mm-notify` must stay empty).

The second half is the real risk here: an ignore rule that silently stops git from
seeing a file someone later intends to commit is a worse failure than the leak it
was added to prevent.

## Out of scope for this plan

The inbound direction (replying from Mattermost into a session), Telegram, the
watchdog's own operator alerts, the known-false `BACKUP STALE` alert, and
migrating the posts already in `#claude-code`.
