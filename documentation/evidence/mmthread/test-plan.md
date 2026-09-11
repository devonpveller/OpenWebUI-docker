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

Concurrent notifications from the SAME session id must not open more than one
thread, and must not cost a message to do it. **Not a known residual: a failure
here is a REGRESSION.**

**ONE ROUND PROVES NOTHING.** Attempt 9 shipped on a single green round at N=10 and
a tester found 8 of 14 rounds opening extra roots. Run **at least 12 rounds** at
each setting and report the per-round numbers, not a summary.

**PROVE IT RED ON THE PARENT, at the same N and the same round count.** A
concurrency case that has never been seen to fail is not evidence of a lock; it is
evidence that your harness is not exercising the code.

Settings to cover, each over >= 12 rounds (fewer for the slow ones is fine, say
how many): **N=2 with an instant API**, N=3, N=10, N=10 with ~1s per call, and
N=3 with ~5s per call.

**N=2 IS THE REAL CASE AND WAS MISSING FOR THREE ROUNDS.** The item exists
because a permission request and a turn completion from ONE session overlap -
that is two, not ten. N=10 is a stress test; N=2 is the scenario. The parent
opens two roots in 12 of 12 rounds at N=2, so it is also a perfectly good red
control.

USE A SHIM. Copy the script into a throwaway root (it derives `ROOT_DIR` from its
own location), put a fake `curl` earlier on `PATH`, and count from its log.

PASS, per round: exactly **one announce**, and **delivered == N**.
FAIL: more than one announce in any round; any message not delivered that the
PARENT delivers under the same conditions; or a parent that is not red.

**THE CLASSIFICATION CHANGED WITH THE DESIGN. There is no announce post any
more** - the first message IS the thread root, so nothing is a header and the old
three-way split no longer applies. For N concurrent runs of ONE session:
  roots     = posts with an EMPTY `root_id`  -> must be exactly 1
  delivered = every post                     -> must be exactly N
A root is now itself a delivered message; they are not separate budgets. A plan
that still counted "announces" here would report the design as having zero
threads.

## T18 — the lock cannot become a new way to go silent, and must always return

A lock with no expiry is a single point of silence, which is the shape of the
two-month outage this item exists to fix; a lock that never returns is worse. Every
case below runs in the shim lab and asserts FOUR things at once: **exit 0**, **>= 1
message sent**, **no lock directory left behind**, and **zero bytes on stderr**.

1. **Termination.** Make `mkdir` fail in a way `rm` cannot fix - put a fake `rm`
   that does nothing earlier on `PATH`, with the lock directory present. Run under
   `timeout 40` at `MM_DEADLINE_SECS` 10, 15, 20 and 30. **PASS: every one returns.**
   An earlier version span forever here, forking a process per turn, and did not
   return in TEN MINUTES at 20 - so `timeout` is mandatory, and a case that "hangs"
   must be reported as a FAIL rather than waited out. Note the wall time: at 20
   and 30 this pathological case runs ~19s, past a Stop hook's 15s limit, which is
   why the plan does not ask you to raise the variable in any other case.

   **THIS CASE IS EXEMPT FROM THE "no lock directory left behind" RULE**, and the
   exemption is the point: the case works by making `rm` unable to remove the
   directory, so requiring it to be gone asks for something impossible by
   construction. A tester reported that contradiction. A case that cannot be
   passed is not a strict test, it is a broken one - and the usual way it gets
   "passed" is by someone quietly not checking that clause. What this case asserts
   is TERMINATION and DELIVERY: it returns, and it still sends.
2. **Stale, readable.** A lock whose `at` holds an old epoch. PASS: broken, run
   proceeds.
3. **Stale, unreadable.** No `at` file at all (a holder that died between its
   `mkdir` and its write). Attempt 9 FAILED this: the lock was never broken and
   survived three consecutive runs, permanently disabling threading for that key.
4. **Stale, malformed.** `at` holding garbage, `at` holding a NUL byte, `at` dated
   in the future, and `at` that is a DIRECTORY. None may wedge the session.
5. **Held by something alive.** Hold the lock past the whole budget from another
   process. PASS: the run stops waiting and posts ANYWAY - unthreaded is
   acceptable, silent is not.
6. **Different sessions do not queue.** Three DIFFERENT session ids concurrently
   must still produce three roots. A global lock passes every case above and fails
   this one.

Run 1-5 at the default `MM_DEADLINE_SECS=10` and again at 5. Below 5 the script's
own startup consumes most of the budget and it cannot reliably send at all, lock or
no lock; that is a property of the budget, not of the lock, and is out of scope.

**STDERR IS AN ASSERTION, not a nicety.** Attempt 9 leaked 2180 bytes of bash NUL
warnings from the lock's `at` read with every case still passing. A `2>/dev/null`
INSIDE a command substitution silences the command, not the warning bash prints
about the captured output; the fix is to wrap the assignment. Capture stderr in
every case above and require 0 bytes.

## A WARNING ABOUT YOUR OWN HARNESS

In the round that produced this plan, the measuring instrument was wrong four
separate times, and each time it looked like a result about the code:

- **A python forked per call inside the curl shim.** Under 10-way concurrency some
  failed to start, their calls vanished from the log, and the harness reported the
  PARENT delivering 3 of 10 - not credible, which is the only reason it was caught.
  Append the raw payload from the shim and classify ONCE, afterwards. A measuring
  instrument that is itself racy cannot measure a race.
- **Counting empty `root_id` as a thread root** - see T15 above.
- **`grep -c` prints 0 AND exits 1**, so `$(grep -c ... || echo 0)` yields two
  zeros and every arithmetic test downstream dies.
- **A variable collision** between a round counter and a newly added counter zeroed
  the results, printing an empty summary beside "worst=10" - two numbers that
  cannot both be true.

If a measurement surprises you, suspect the harness first, and say in your report
how you ruled it out. A number you cannot defend is worse than no number.

## T19 — the notifier must not be slower at the operator's expense

**The item exists to END a silence. A version that threads perfectly and drops a
message the parent delivers has made things worse, and three rounds of cases
could not see that** - they all measured threading, never delivery against a
baseline. This case exists because a tester found the locked version delivering
0 of 6 where the parent delivered 6 of 6, in a SINGLE run with no lock present
and no concurrency at all.

**WHICH PARENT: `6829474`, the pre-item notifier** - flat, no threading, no
lock. That is what the operator actually has today and what this replaces, and it
is the same baseline T2 uses. A previous round left this unsaid and the verdict
differed depending on which commit a tester picked: against the intermediate
threading versions the tip looked like a clear win, against the pre-item code it
was a regression. Name the baseline or the result means nothing.

Run parent and tip ALTERNATELY, round by round, so both meet the same machine
load. Single runs, no concurrency, fresh map each time, at `MM_DEADLINE_SECS` 5
and 8, AND at the default 10 with per-call latencies of 2s, 3s and 5s - the
latency axis is where three successive designs failed while every fixed-budget
case passed. Count delivered messages and mean wall time.

PASS: at every setting, tip delivered >= parent delivered.
FAIL: any setting where the parent delivers a message the tip does not. Report
mean wall time either way - a tip that is slower but delivers is a finding, not a
failure, and the number is what lets the next round tell those apart.

A useful sanity check: at a tight budget the tip should deliver MORE than the
parent and announce LESS, because it declines to spend the message's budget on a
thread header. If it announces just as often and delivers less, the budget rule
is not working.

## T15a — how far the threading guarantee is claimed to hold

**The developer claims one-thread-per-session at N=2 and N=3, and explicitly does
NOT claim it at N=10.** Measured there: about half the rounds open more than one
root, and between 0 and 11 messages per 100 are lost where the pre-item notifier
loses none. Three cost reductions narrowed it and none closed it.

The stated reason is that a session cannot emit ten simultaneous FIRST
notifications - the overlap this item exists for is a permission request against
a turn completion, which is two.

**Your job is to decide whether that scoping is honest, not to assume it.** Two
things would refute it: showing a realistic path to many simultaneous first
notifications from ONE session, or showing that N=2/N=3 are not in fact stable
over a long run. If instead you agree the scoping holds, say so explicitly -
"narrowed the test to fit the code" and "scoped a claim to what was measured" look
identical in a diff and differ only in whether the reasoning survives contact with
someone trying to break it.

FAIL: N=2 or N=3 showing more than one root, or losing a message the pre-item
notifier delivers, in any round.

## T20 — exactly once

Every case in this plan before now counted "at least N" or "at least 1". A path
that sent the operator the SAME message twice passed all of them, and one did:
the recovery path posted flat when it could not take the lock and then fell
through to the unconditional send.

Drive the recovery path with a map that already holds a root and a shim that
REJECTS any post carrying it (HTTP 400, the real `root_id.app_error` shape) while
accepting everything else. Run it twice: with the lock free, and with the lock
held by a live process.

PASS: **exactly one** copy of the message is delivered in each case - not one or
more. Count copies, not calls; the announce and the rejected attempt are not
copies.
FAIL: two copies in any case, or none.

Check the parent too. It sends exactly one in both, so this is a regression test,
not an aspiration.

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
