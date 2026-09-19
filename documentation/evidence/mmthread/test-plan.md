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

## HOW TO RUN THE CASES THAT SAY "POST"

**CASES T1-T5 AND T11 AS WRITTEN MANDATE THE OPERATOR'S REAL SERVER, AND A TESTER
IS FORBIDDEN TO USE IT.** Both attempt-17 and attempt-18 testers hit this; the
second ran everything against a local fake and said so. The plan was requiring
what the brief forbids, which makes those cases unrunnable as written rather than
strict.

**Use a local fake Mattermost** - one long-lived process, real `curl`, loopback
port - and COPY the script into a lab with the API retargeted and a fake token:

    sed -i "s|API=\"http://localhost:8065/api/v4/posts\"|API=\"http://127.0.0.1:<port>/api/v4/posts\"|" "$LAB/scripts/notify-mattermost.sh"
    grep -q "127.0.0.1:<port>" "$LAB/scripts/notify-mattermost.sh" || exit 9
    grep -q "localhost:8065"   "$LAB/scripts/notify-mattermost.sh" && exit 9   # must NOT survive

The error body for a dead root must put `root_id` in the JSON **`id`** field, not
in `message` - a developer's shim put it in `message`, the `!deadroot` sentinel
never fired, and the recovery looked dead on BOTH builds. One Python process for
the whole run, never one per call: a per-call fork dies under concurrency and once
made a parent look like it delivered 3 of 10.

**The worktree carries a real `.env` with a real bot token.** Running the real
script posts to the operator's real `#claude-sessions`. That has happened. Also
check `PATH`: an entry in Windows form (`C:/...`) splits on the drive colon, and a
shim then silently never loads.

Anything a fake cannot answer - that Mattermost really threads on `root_id`, that
the channel renders it - is a DEPLOY-time check, not a test-time one. Say so in
the evidence rather than reaching for the real server.

## T1 — the shape, against a LOCAL FAKE

**NOT "against the real API".** That heading survived the round that added the
local-fake recipe, and three testers in a row were told by the title to do what
the brief forbids. A heading is the first thing read and the last thing updated.

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

**COUNT THE FIRST MESSAGE AS THE ROOT, NOT AS A REPLY UNDER AN ANNOUNCE POST.**
There is no announce post any more - T15 says so and the code agrees - so three
messages from one session are ONE root plus TWO replies, which is what the line
above means. Read with the old announce in mind it reads as four posts, and an
attempt-19 tester flagged the arithmetic as contradicting T15.
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

**TWENTY PASSES OF THE WORST CASE, NOT THREE AND NOT FIVE, AND REPORT THE
DISTRIBUTION.** The worst case is real `curl`, a listener that accepts and never
answers, the lock held by another process, and the default budget. Report min,
p50, p90 and max, and the COUNT over 15s - not a range.

This requirement is the whole history of this case. A three-sample "14s, 14s,
13s" was refuted by a tester at 16.8s and 17.3s. The replacement five-sample
figure was published as "deterministic" one commit after that retraction, and
refuted again at p90 15.08 / max 15.77, three of twenty past the Stop hook. **A
handful of runs cannot see a p90**, and this plan demanded 12 to 30 rounds for
concurrency while letting three stand for timing. Twenty, and the count over the
limit, or the case is not executed.

PASS: exit 0 in every case, no stderr reaches the caller, and **0 of 20 runs
exceed 15s** — measured at the current tip: min 10.90, p50 11.78, p90 12.29, max
12.37. The number that matters is the HOOK timeout: 15s on Stop and 20s on
Notification in the operator's `.claude/settings.local.json`.
FAIL: any run over 15s, or a figure reported as a range or an average instead of
a distribution with a count.

**This case used to say "the 8s curl bound", which was the pre-threading figure
and was never updated when threading made it up to four calls.**

**AND THEN THIS CASE WENT STALE IN THE OPPOSITE DIRECTION.** It went on to say
"check the budget is honoured, not a fixed 8s: set `MM_DEADLINE_SECS=3` and
confirm later calls are SKIPPED rather than started" - and attempt 14
deliberately made the FIRST send ignore the budget, because rationing it is what
was losing messages. A tester ran the case as written and correctly failed the
code for doing the thing the same round had been revised to do. **A plan revised
for a change must be revised for the change**, and this one was edited in the
round that made the contradiction.

What to check now:
- the FIRST send always goes out, with `-m` at least the floor (8), even at
  `MM_DEADLINE_SECS=3`. A budget too small to send is not a reason not to send;
  the pre-item sender has no budget at all and always uses `-m 8`.
- LATER calls are FLOORED TOO, and clamped by the wall like every other call.
  Verify by instrumenting `-m` per call, not by timing.

  **THREE ASSERTIONS THAT STOOD HERE DESCRIBED A BUILD THAT NO LONGER EXISTS**,
  and an attempt-19 tester failed the item for doing what was intended - which is
  the plan's fault, not theirs. They said a dead-root retry at
  `MM_DEADLINE_SECS=20` shows `-m 19` then `-m 16` (measured: `-m 10` then
  `-m 9` - MM_WALL_SECS clamps both, and the budget stopped being the only
  bound); that at budget 3 "the retry does not happen at all" - which contradicted
  T23 in this same file, since budget 3 is below `MM_THREAD_MIN_BUDGET` and ONE
  call is the correct behaviour there, not a missing retry; and the FAIL clause below forbade "a later call that is floored"
  while this round deliberately floors it, because a `!deadroot` response is the
  server REJECTING the root rather than serving the message.

  WHEN THE ARITHMETIC CHANGES, EVERY PREDICTION DERIVED FROM IT IS STALE. The
  wall landed two attempts ago and these three were left describing the budget
  that preceded it.
- the budget is clamped at BOTH ends (1 and 60) and a non-integer falls back to
  the default, all with zero stderr.
- **THE SHAPE THAT WAS DETERMINISTICALLY OVER 15s - a first call that is SLOW AND
  THEN FAILS - IS FIXED, because it was the recovery's second call.** Measured
  after the recovery was removed: max 12.09s, 0 of 12 over the limit. Drive it
  anyway; it is the shape most likely to regress if a second in-run call ever
  returns.

  The history, since the figure below is what the case was written around: A stale root plus a server taking ~6s before rejecting it costs
  the floored first call, the failure, and then the recovery - and a tester
  measured 15.6-16.1s, five of five over the Stop hook's limit, at the DEFAULT
  budget. This case named only the hung-API shape, which is cheaper. Drive a
  server that is slow AND rejects, not one that merely hangs.

FAIL: any non-zero exit, any stderr reaching the caller, a first send that does
not happen, or a run that could exceed 15s. (A floored LATER call is correct now
and is no longer a failure - see above.)

**DEFINE "DELIVERED" BEFORE COUNTING IT.** Server receipt and a client-confirmed
201 diverge on EVERY run at budgets 8-10 against a slow API: curl can abandon a
request the server has already accepted and recorded. Count server receipt for
DELIVERY - that is what reaches the operator - and client confirmation for
anything that depends on the RESPONSE, which the map repair does, because it needs
the new root id out of the 201. An attempt-19 tester had to pick a definition the
plan never gave, and the two answers differ.

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

**EXCEPT AT A PER-CALL COST ABOVE `LOCK_WAIT_SECS`, WHERE DELIVERY WINS AND MORE
THAN ONE ROOT IS THE ACCEPTED OUTCOME** (operator decision, 2026-09-18). At the
`N=3 with ~5s per call` setting the one-root rule above cannot hold and must not
be asserted:

- `LOCK_WAIT_SECS` is 4, and line 618 caps the wait at it *regardless of budget*
  (`[ "$_wait" -gt "$LOCK_WAIT_SECS" ] && _wait="$LOCK_WAIT_SECS"`). A call that
  costs 5s therefore cannot publish its map line inside any other run's wait.
- So the losers stop waiting and post UNTHREADED. That is not a defect; it is
  T18 case 5 being obeyed - *"the run stops waiting and posts ANYWAY -
  unthreaded is acceptable, silent is not."* The two rules contradicted each
  other at this setting, and the operator resolved it in favour of delivery.
- Measured 2026-09-18 (attempt 22): 3 roots in 12 of 12 rounds with
  **delivered=3/3 in every round** - nothing lost. Raising the constant was the
  alternative and was declined: T7's worst case already measures max 12.10s
  against the Stop hook's 15s, and buying threading here would spend that margin.

**So at `N=3 with ~5s per call` the assertion is `delivered == N` ONLY.** Record
the root count as an observation, not a verdict. FAIL that setting only if a
message is LOST - which is the outage this item exists to end. Every other
setting keeps the one-root rule unchanged.

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
   must be reported as a FAIL rather than waited out. **The "~19s at 20 and 30"
   warning that stood here is STALE** - `MM_WALL_SECS` bounds the whole run now, so
   an attempt-18 tester measured ~5.4s at EVERY budget. Record what you measure;
   the reason not to raise the variable elsewhere is that no other case needs it,
   not a wall time that no longer happens.

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

   **ALSO EXEMPT FROM "no lock directory left behind"**, for the same reason case 1
   is and one an attempt-18 tester had to point out: the lock belongs to ANOTHER
   LIVE HOLDER. A run that tidied it away would be deleting a lock it does not own,
   which is the bug, not the pass. The clause means "this run leaves no lock of its
   own behind". Two exemptions to one clause is the clause being wrong: it is
   about OWNERSHIP, and it was written as if about existence.
6. **Different sessions do not queue.** Three DIFFERENT session ids concurrently
   must still produce three roots. A global lock passes every case above and fails
   this one.

Run 1-5 at the default `MM_DEADLINE_SECS=10` and again at 8.

**BELOW `MM_THREAD_MIN_BUDGET` (10) THE SCRIPT DOES NOT THREAD AT ALL** - no lock is
taken, no map line is written, and it behaves exactly like the pre-item sender.
That floor exists because threading costs two syscalls that are not free on this
platform, and at 5s per call a budget of 5 delivered 4 of 6 where the pre-item
sender delivered 6, while 8 and 10 were at parity. **That 8 became 10 when the
map-read gate landed, and this paragraph kept saying 8** - an attempt-21 tester
measured that budgets 8 and 9 do not thread either, so a lock exemption hung on
"below 8" exempts nothing at 8 or 9. Name the CONSTANT, not its value. So at a
budget below `MM_THREAD_MIN_BUDGET` the
"no lock directory left behind" clause does NOT apply: a run that never touches
the locking cannot tidy it either, and a stale lock is cleared by the next run at
a normal budget. Every other clause still applies at every budget - exit 0, a
message sent, zero stderr.

The alternative was to tune the gate one more notch and keep claiming threading
everywhere, which is how the previous three rounds each produced a version that
failed at a setting the last one had not been measured at.

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

**WHY THESE ARE T21/T22/T23 AND NOT T6a/T15a/T20a.** `queue.ps1` parses a case as
`^##\s+(T\d+|Case\s+\d+)\b`, so a lettered heading is INVISIBLE to it: its verdict
cannot be recorded, and `-Pass` - which demands every PARSED case read PASS - never
sees it. An attempt-21 tester had to fold three cases under their parents to report
them at all. The same defect was found and fixed in `drilllabel` (a `## T9a` renamed
to `## T10`) and then written again here. **A case the harness cannot parse is a
case that cannot fail.**

## T21 — a dead root must be recoverable MORE THAN ONCE, and the map must show it

**RUN IT AGAINST A SLOW API, NOT ONLY AN INSTANT ONE — SWEEP THE LATENCY.** For
every attempt up to 17 this case was run against an instant-reject shim, and so
never met the budget path at all: the entry gate that made re-threading impossible
above ~2.6s per call was present from attempt 16 and invisible to every run of
this case. **A case that only ever meets the fast path cannot see a budget bug.**
Sweep at least 3s, 4s, 5s and 6s per call and report a rate at each; a single
operating point is not a result, and the RATES are host-dependent (one tester's
machine gave 5/12 where another host gives 12/12 — the ordering and the mechanism
are what reproduce).

T6 asks whether a dead root wedges the session, and answers it by looking at
whether a message got through. That is not enough, and the gap hid a real defect
for several rounds: the recovery path could post flat every time while never
re-threading, so a tester watching only delivery saw success while the session was
permanently out of its own thread.

Drive a map that already holds a root the shim REJECTS (HTTP 400, the real
`root_id.app_error` shape), then send THREE notifications in a row.

PASS:
- run 1 spends two calls (the dead root, then a retry) and **appends a NEW line to
  the map file**;
- runs 2 and 3 spend ONE call each and reply under that new root;
- the map is never rewritten - it is append-only, and the lookup takes the LAST
  match.

FAIL: the map unchanged after run 1; or runs 2 and 3 still burning a call on the
dead root. **Read the map file. Do not infer it from the posts** - "a message was
delivered" is true in both the working and the broken case, which is exactly why
this needed its own case.

## T23 - A DEAD ROOT *AND* A MARGINAL BUDGET, which no case crossed

Every case here varies ONE axis. T21 sweeps latency at the default budget; T19
sweeps budget with a live root. An attempt-18 tester crossed them and found the
only real loss of parity in the item:

Measured at 10 rounds per cell, this tip against the same tip WITHOUT the gate
fix and against the pre-item notifier:

| MM_DEADLINE_SECS | this tip | tip WITHOUT the gate fix | pre-item `6829474` |
|---|---|---|---|
| 8 | 15/15, **ONE call per run** | 0-3/15 | 15/15 (one call) |
| 9 | 15/15, **ONE call per run** | 11-14/15 | 15/15 (one call) |
| 10 | see the residual below | - | 15/15 (one call) |

**THIS TABLE ONCE PREDICTED "20 calls" AT BUDGETS 8 AND 9 AND THAT IS NOW WRONG BY
DESIGN.** Those budgets are below `MM_THREAD_MIN_BUDGET`, so the map is not read,
no root is used, and ONE call per run is CORRECT - it is the parity the row is
there to prove. An attempt-21 tester was failed by the old prediction for observing
the intended behaviour. **Every figure derived from a constant must be re-derived
when the constant moves**, and this is the third time in this plan that it was
not.

The call counts are the evidence, not the delivery rate: 11 calls for 10 runs
means the recovery ran ONCE and nine runs sent nothing at all.

Run it: a stale map entry, a server answering in 6s, and `MM_DEADLINE_SECS` at 8,
9 and 10, at least 15 passes each, counting messages the SERVER received.

**THIS CASE WAS WRITTEN FOR A RECOVERY THAT NO LONGER EXISTS**, and an attempt-20
tester failed the item for doing what was intended - the SECOND time this plan has
done that after the arithmetic changed under it. It demanded TWO calls per run and
called a single call "the recovery skipping itself"; below
`MM_THREAD_MIN_BUDGET` one call is CORRECT, because the map is not read at all.

**AND SWEEP PAST THE SEND BUDGET.** Every latency sweep here stopped at 6s, and
the defect this case now exists for only appears when the API is SLOWER THAN THE
CALL'S OWN TIMEOUT - there the rejection never arrives, `post` returns empty
rather than `!deadroot`, and a response keyed on the reason does nothing at all.
Go to 8s, 9s and 10s.

PASS: parity with `6829474` at every budget and latency EXCEPT the single message
that discovers a deleted root at an API slower than the send budget; the sentinel
written in every non-confirming case, so the loss is one message and not the
session; and ZERO duplicates anywhere, including a LIVE root at a latency above the
budget.
FAIL: any duplicate; any loss beyond that one message; a map left holding a dead id
after a non-confirming rooted send; or a run below `MM_THREAD_MIN_BUDGET` that
reads the map at all.

**A CASE THAT VARIES ONE VARIABLE AT A TIME CANNOT SEE A TWO-VARIABLE DEFECT**,
and every case in this plan varied one. The hole sat on the diagonal that neither
sweep walks. When two axes each have a known weak end, cross them before claiming
the corner.

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

## T22 — how far the threading guarantee is claimed to hold

**THE CLAIM IS NOW N=2 ONLY, and the previous round's wider claim was refuted by
measurement rather than argument.** A tester ran 30 rounds and found N=3 splitting
in 5 of them, then built a SECOND independent harness - real `curl` against a
long-lived fake server, no per-call forks - and got 8 of 30. The developer had
called N=3 exact on twelve green rounds.

**TWELVE ROUNDS CANNOT ESTABLISH THIS, and that is the reusable lesson.** A clean
run of twelve has roughly an 11% chance even when the true split rate is one in
five. Run **at least 30 rounds** per setting, and if you report a rate, say what
your sample can and cannot exclude.

**AND N=2 MUST BE RUN ACROSS THE LATENCY AXIS, not only at an instant API.**
This is the gap that let a real defect ship: T15 listed latency only at N=3 and
N=10, and T22 said "N=2 is 30 of 30" naming no latency at all - so a tester
following it literally runs an instant API, gets 30 of 30, and ships. The plan
supplies the missing reasoning itself in T19 ("the latency axis is where three
successive designs failed") and confined it to delivery. An attempt-15 tester
crossed the two axes anyway and found N=2 splitting in 18 of 30 rounds at 5s per
call.

Run N=2 at 0s, 1s, 2s, 3s, 4s and 5s per call, at least 12 rounds each.

**THE CEILING IS ABOUT 2 SECONDS AND THE LAST ROUND MOVED IT TO 3 IN ERROR.** The
two documents disagreed - the note said 2, this said 3 - and I resolved it by
promoting the larger number off a TEN-ROUND sample reading 9/10. An attempt-18
tester measured 45 rounds per build: this tip 19/45 (42%), attempt 17 16/45 (36%),
indistinguishable; re-measured at 30 rounds here, this tip is 14/30. A true 90%
rate yields 19-of-45 with probability about 1e-8.

**And it had no mechanism.** The only lock change between the two builds is the
entry gate; on a CLEAN map both compute a 4-second wait, so neither can out-thread
the other there. The gate acts on the DEAD-ROOT path, which is where the gain
really is. **When two documents disagree, the answer is a measurement, not a
choice** - and a rate needs the same sample size this plan already demands for a
latency.

Measured at N=2 with one instrument, this tip against ATTEMPT 17:

| API answers in | attempt 17 `c822b5c` | this tip | message lost |
|---|---|---|---|
| 2s | 15/15 | **30/30** | 0 |
| 3s | 16/45 (36%), **12/30 here** | 19/45 (42%), **14/30 here** | 0 |
| 3.5s | - | 1/12 | 0 |
| 4s | 0/12 | 0/12 | 0 |

**`c822b5c` IS ATTEMPT 17, NOT THE PRE-ITEM TIP** - it is this commit's parent,
780 lines of threading. The pre-item notifier is `6829474`, 52 lines. T19 below
states the rule ("name the baseline or the result means nothing") and every table
added last round broke it, labelling a previous-attempt comparison as a
comparison against the code being replaced. The plan contradicting itself on the
baseline is itself a defect an attempt-18 tester filed.

**The claim is therefore: one thread per session for an API answering within about
2 seconds.** At 3s it is a coin flip, at 3.5s rare, at 4s gone - equally on both
builds. Beyond that, more than one thread and every message.

"A loser waits 3.8s" was also a turn-count figure from a build whose wait is no
longer counted in turns; it is gone.

**PASS for this case requires at least 30 rounds per setting and a rate reported
as n/N**, for the same reason T7 requires 20 timed passes: a handful of runs
cannot separate 40% from 90%, and one round of this item published exactly that
mistake while the twenty-pass rule sat on the same screen.

The cause is arithmetic: a loser waits for the winner to publish its map line,
which cannot happen until the winner's post returns, so the wait must cover a
whole call and the wait is bounded by what the Stop hook can afford.

N=3 is reported, not claimed exact. N=10 is not claimed at all.

The reason offered for N=10 being out of scope is that a session cannot emit ten
simultaneous FIRST notifications - the overlap this item exists for is a
permission request against a turn completion, which is two. The previous tester
checked `.claude/settings.local.json`, found only `Stop` and `Notification` wired
and no `SubagentStop`, and agreed. That reasoning is now on the record and can be
attacked the same way the N=3 claim was.

**Your job is to decide whether the remaining scoping is honest, not to assume
it.** "Narrowed the test to fit the code" and "scoped a claim to what was
measured" look identical in a diff, and differ only in whether the reasoning
survives someone trying to break it.

FAIL: N=2 showing more than one root in any of 30 rounds **AT OR BELOW THE 2s
CEILING T22 states** - above it, splitting is the documented behaviour and failing
on it contradicts T22 in the same file; N=3 materially worse
than 1 in 30; or any N **in scope** losing a message the pre-item notifier
delivers. **N=10 IS NOT IN SCOPE** - T22 declares it so, and this clause used to
demand `delivered == N` at N=10 anyway, which fails the item on a residual it has
already scoped out. Measured, for honesty rather than as a criterion: N=10 loses 1
of 12 rounds instant and 3 of 12 at 1s, where the parent loses none.

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
