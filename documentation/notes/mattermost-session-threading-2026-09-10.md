# Mattermost session threading — findings (2026-09-10)

Sink for the `mmthread` item.

**A BARE NUMBER DOES NOT SHIP FROM THIS NOTE.** Every figure must name the
command or file it came from, so a reader can re-run it. That rule exists because
the claims here that carried citations all re-derived exactly, and the ones that
failed carried none — twice a figure was lifted from a tester's report and
restated as this note's own measurement. If a number cannot be cited, state the
property instead and say how to measure it.

**This note carries no history of its own attempts.** It used to, and that is why
its claims kept failing: a figure was found wrong, the correction went into a
commit message, and the wrong line stayed here. A running narrative of its own
drafts cannot converge — `git log -- documentation/notes/` holds the history and,
unlike a document, cannot go stale. (This sentence counted those rounds until a
tester pointed out that the counter was itself the thing being cut, and went
stale with every further round. It carries no number now.) What is below is
either re-derivable today or explicitly marked as a reading taken at a time.

## What was wrong

The operator reported no Mattermost messages in nearly a week. The sender was
never broken — posts were landing, the bot token authenticated, the channel
returned 200. Three separate things were:

1. **A stale allowlist, and this was the actual cause.**
   `scripts/.mm-notify-sessions` held one session id written 2026-07-04, and the
   notifier silently drops any session not listed. Turn-completion pings had
   therefore been suppressed since July — not for a week. Permission requests
   still arrived only because they carried no session id for the gate to check.
   **Resolved 2026-09-11 by the operator: the file was removed (backup at
   `scripts/.mm-notify-sessions.removed-2026-09-11.bak`), so every session pings.**
   Verified: the first genuine turn-completion post since July is
   `🤖 Claude Code finished a turn in ai-stack · session 5881c42f`, 2026-09-11
   00:07:41, in `#claude-code`.

2. **Nothing mentioned the operator.** Nothing bolded the channel, so nothing
   pushed. `mention_count` for another user comes from
   `GET /api/v4/channels/{channel_id}/members/{user_id}`. **Two drafts of this
   line were wrong about it and the second was wrong in a more interesting way.**
   The first cited `GET /users/me/teams/{team}/channels/members`, which returns
   200 and carries the caller's OWN rows — it could never have held the figure.
   The second said `bot-claude` "cannot read it for the operator (403)", which is
   not a property of the endpoint at all: a tester got **200** on
   `#claude-sessions`, `mention_count=2`. It 403s on `#claude-code` and ONLY
   there, because `bot-claude` is not a member of that channel. Membership, not
   permission; the channel, not the API. Treat the 0 as CORROBORATED, NOT READ:
   what is directly checkable is that none of the 241 posts then in `#claude-code`
   contained a mention token at all, which is the same conclusion by a route that
   does not need the operator's own membership row. (`last_viewed_at` read NEVER
   when first measured; it has moved several times since and is not re-derivable —
   Mattermost keeps no history of it.)

3. **Nothing threaded.** `root_id` appeared nowhere in
   `scripts/notify-mattermost.sh`. It was NOT absent from the repo — the
   agent-bridge threads its own posts
   (`agent-org/agent-bridge/app/adapters/mattermost.py:100`), as do
   `scripts/claude-sessions-bridge/bridge.py:499` and
   `scripts/mattermost-mcp/server.py:200`.

   **"The IDE notifier was the one sender that did not" is false**, and a tester
   found three counterexamples: `restart_bridge.py:111`, `check_disk.py:126` and
   `mm_post.py:30` all post without a `root_id`. What is true and was the actual
   point: the notifier was the one sender whose THREADING MATTERED to the operator
   here, because it is the one that pages them about their own sessions. A
   universal was used to carry an argument that never needed one.

## Residuals — true, measured, NOT fixed

- ~~Concurrent first notifications from ONE session open several threads.~~
  **FIXED — and the way it survived the previous fix is the point.** Making the map
  append-only removed the race over the FILE and measured clean; this was a race
  over an ACTION (announcing), which no property of the file could settle. Three
  concurrent runs still produced three roots and three map lines, measured at
  `b71257f`. Now a per-session `mkdir` lock wraps the lookup-and-announce:
  measured 3 -> 1 root at 3 concurrent and at 10, with all ten messages under the
  one root, while three DIFFERENT sessions still open three threads. The lock is
  per session precisely so they do. Two conflicts that touch the same file are not
  therefore the same conflict.

  **THE LOCK TOOK FOUR GOES, AND EVERY FAILURE WAS THE SAME MISTAKE: BUYING THE
  THREAD WITH THE MESSAGE.** Each version was a correct synchronisation primitive
  and a worse notifier, which is why "the lock works" was never the question.

  1. *Gave up at 3s of the 10s budget left.* An announce and a message then had to
     share 3 seconds, and `post` skips a call with under 2 — so a contended lock
     waited politely and said NOTHING.
  2. *So the reserve went up to two thirds.* Now runs that started slowly arrived
     with less than the reserve and gave up INSTANTLY: a tester measured 5 to 7 of
     10 runs abandoning the lock without waiting, and ten concurrent runs opening
     up to ten roots. One number was doing two jobs, and raising it for one broke
     the other. The wait is now bounded by its own try count, and the budget is
     only a backstop.
  3. *A run that gave up still announced.* So under a slow API three concurrent
     runs opened three roots AND dropped three messages, because the announce is a
     second API call. Now **only the lock holder may create a root** — a run
     without it posts FLAT. One message, unthreaded, never a competing root and
     never a dropped one.
  4. *Losers waited for the lock to be RELEASED.* But a loser does not want the
     lock, it wants the ROOT. Waiting for the wrong event cost 2 messages in 200
     across 20 rounds of 10 concurrent runs, where the unlocked parent lost none.
     The wait now ends the moment the winner's map line appears.

  **A "200 of 200 messages delivered" figure stood here for two rounds after a
  commit message declared it replaced.** It did not reproduce — the same setting
  measured 112 of 120 — and the correction went into the commit while the line
  stayed. That is verbatim the failure this note's own preamble exists to
  prevent, committed by the person who wrote the preamble. The current numbers
  are in the table below, each beside the setting it was taken at.

  **Termination is a property of the code, not of the filesystem.** An earlier
  version bounded itself with `rm -rf; continue`, which skipped both the budget
  check and the sleep: when `mkdir` failed for anything the `rm` could not fix, it
  span forever forking `date` and `cat`, and did not return in TEN MINUTES at
  `MM_DEADLINE_SECS=20`. A Stop hook that never returns is worse than the silence
  this item exists to end. The loop is now bounded by an unconditional try count
  and nothing in it uses `continue`.

  The lock expires two ways so a dead holder cannot wedge a session: a readable
  timestamp older than the whole budget, or an unreadable one after three turns.
  Both bounds are clamped to stay reachable inside the loop bound — at
  `MM_DEADLINE_SECS=3` the loop is 4 turns long, and an expiry set at 5 could never
  fire, which left a blind lock surviving every run and silently disabled threading
  for that session.
- **A whitespace-only allowlist silences everything** — `-s` sees a non-empty
  file and every entry normalises away. An empty-but-present file is now the
  operator's foot-gun rather than the notifier's.
- **`MM_OPERATOR_MENTION=` cannot disable the mention**; `${VAR:-default}` treats
  empty as unset. To suppress it the variable must be set to something harmless.
- **The thread map is never pruned.** It grows for the life of the machine. The
  cost is a linear `awk` scan per notification, and because the deadline is
  wall-clock from script start, file work eats the HTTP budget rather than adding
  to it — so a large map cannot push the hook past its timeout, it can only
  shorten the time left to post. No figure here: two independent measurements of
  the scan disagreed, and neither is worth carrying. Measure it if you need it:
  `seq 200000 | awk '{print "k"$1" r"$1}' > map && time awk -v s=k1 '$1==s{v=$2}
  END{print v}' map`.
- **The script cannot work without `python` on `PATH`**, and if it is missing the
  script exits 0 having sent nothing. Pre-existing; this item added further
  invocations. Counted with a PATH shim, and the figure depends on whether stdin
  is redirected — the deciding line is `[ -z "$sid" ] && [ ! -t 0 ]` at
  `scripts/notify-mattermost.sh`, the `[ -z "$sid" ] && [ ! -t 0 ]` test:
  **NO COUNT SHIPS FROM THIS LINE, and the reason is the history of trying.** It
  has carried three different figures. The first ("3 steady, 5 on a first
  notification; 2 and 4 without stdin") was invalidated by this item's own change
  when startup was made fork-free, and the line stayed. The replacement ("2 in all
  four cases") was measured by a tester as 3 on the no-stdin shapes, and by me as
  1, 2, 1, 1 on a third run. Three attempts, three answers, on a figure nobody
  needs.

  The PROPERTY is stable and is what matters: python is forked for JSON work on
  the posting path, and once more at startup only when the bash regex fails to
  find a session id. Count it yourself if you need a number -- put a wrapper
  earlier on `PATH` that appends a line to a file and then `exec`s the real
  interpreter, and run the shape you care about.

  The lesson underneath is the one the preamble already states, in its sharpest
  form: when you change a thing, the numbers ABOUT that thing are stale until
  re-measured, and a figure that three careful measurements disagree about should
  be replaced by the method, not by a fourth number.
- **A message body containing `session <8 hex>` steers the post into that
  session's thread.** Where the Notification hook supplies its own prefix, the
  prefix wins.
- **IPv6-style ids, ids under 8 characters, and ids with no alphanumerics** all
  normalise to something usable or, in the last case, to nothing — and an
  unidentifiable session is treated as not-on-the-list when a list exists.
- **An allowlist path that is a DIRECTORY fails open.** `[ -s "$ALLOW" ]` is
  false for a directory, so the gate is skipped and an unlisted session posts.
  Pre-existing — the same test passes against the parent — but worth knowing
  before someone `mkdir`s that path.
- **Allowlist matching was NARROWED BY THIS ITEM, and that is a change, not an
  inheritance.** The parent compared the whole line —
  `grep -qxF "$sid" "$ALLOW"` at `6829474:scripts/notify-mattermost.sh:36` — so
  only an exact uuid passed. This version compares the 8-character key
  (the `normkey "$_entry"` comparison inside the allowlist gate in
  `scripts/notify-mattermost.sh`), so a DIFFERENT uuid sharing its first
  eight hex characters both passes the gate and joins the listed session's
  thread: verified with `beef0011-ffff-…` against a list naming
  `beef0011-26f9-…`, which the branch posts and the parent refuses.

  It is deliberate — the gate has to accept the 8-character form the Notification
  hook can supply, which is the whole of T13 — and the odds are negligible for
  real uuids. But a commit message of mine called it "pre-existing" alongside the
  directory residual below, which IS pre-existing. "Pre-existing" is the label
  that gets a widening waved through, and this one was mine.

## The inbound half does not exist, and the channel choice has a consequence

The operator can now reply in a thread, but the IDE session cannot read the
reply. Worse, in `#claude-sessions` a reply is consumed by the **bridge**, which
starts a new headless session under it — observed when the operator replied to a
test post and session `9fae8f75` appeared
(`scripts/claude-sessions-bridge/state/audit.jsonl:5533` — that file is runtime
state in the MAIN checkout, not tracked, so the line resolves there and in no
worktree). So a reply
looks answered and is answered by something else. Threading makes replying
possible; it does not make it effective.

## The watchdog's own alerts are a different audience

They still go to `#claude-code`. A crash-loop page is not session chatter, and
moving it is a separate decision. Its known-false `BACKUP STALE` warning — a
watchdog run from a git worktree looking for backup directories that only exist
in the main checkout — is recorded with the `crashloop` item, on the branch where
that file lives.

## Two ignore rules named an exact path where a FAMILY of files lives

Both are fixed here; the pattern is worth more than either fix.

`scripts/.mm-notify-sessions.removed-2026-09-11.bak` holds a session uuid and is
untracked, and `.gitignore` covered only the exact path
`scripts/.mm-notify-sessions` — so a broad `git add` in the operator's checkout
could have committed it. Widened to `scripts/.mm-notify-sessions.*`.

The thread map had the identical hole and nobody had tripped it yet:
`.gitignore` covered `scripts/.mm-session-threads` exactly, so the lock
directories this item now creates beside it (`.mm-session-threads.lock.<key>`)
would have shown up as untracked. Found by asking the question the first one
taught, not by hitting it. Widened the same way.

A runtime-state path is never one file for long — it acquires a backup, a lock, a
`.tmp`, a `.bak` — so an ignore rule pinned to the exact name is a rule that will
be wrong later, quietly, at whatever moment someone runs `git add -A`.

## THE LOCK COST MESSAGES, AND THE FIX WAS A RULE, NOT AN OPTIMISATION

A tester measured the locked version losing messages the unlocked parent
delivered — 17 of 140 at ten concurrent runs where the parent lost none — and
found the cleanest form needs no concurrency at all: **one run, no lock present,
`MM_DEADLINE_SECS=5`, parent 6 of 6 delivered and the locked version 0 of 6.**
Uncontended, where nothing waits. That killed the excuse that this was contention.

Two causes, and the second is the one worth remembering.

**Process spawns.** `date +%s` forks, and `remaining` is read before every call;
the lock added a `mkdir`, a `date` and an `rm`. On Windows a fork is tens of
milliseconds and the measured gap was ~1.0s per run. bash 5 exposes
`EPOCHSECONDS` as a variable, so the clock no longer forks at all. That closed
about two thirds of it (1002ms -> 355ms) and was still not enough.

  **THERE IS NO ANNOUNCE POST ANY MORE. THE FIRST MESSAGE IS THE ROOT.**

  Three rounds were spent deciding WHEN to spend a second API call on a thread
  header, and each answer failed at a latency the previous one had not been
  measured against. Gating it on 5 seconds of remaining budget was fitted to
  STARTUP cost; two calls at 3-5s each need 6-10s, so at the DEFAULT budget with
  a 5s API the run posted the header and then could not send — 1 of 6 delivered,
  every other round consisting of a thread title announcing a session that then
  says nothing. That is this item's own failure, rebuilt one layer up, for the
  third time.

  The header is gone. The thread's root is the session's first MESSAGE: one call,
  the same as the code being replaced, and the id it returns is what the map
  records. Nothing can be lost to a header because there is no header, and a
  reader opening the thread sees content instead of a title.

  Single runs at `MM_DEADLINE_SECS=10`, by per-call latency, against the pre-item
  notifier `6829474`:

| latency | pre-item | attempt 11 | now |
|---|---|---|---|
| 2s | 6 of 6 | 6 of 6 | **6 of 6** |
| 3s | 6 of 6 | 6 of 6 | **6 of 6** |
| 5s | 6 of 6 | **1 of 6** | **6 of 6** |

  The lesson that survives all three rounds: **the overhead of a feature is not
  paid by the feature.** It is paid by whatever runs last, and here that was the
  only thing anybody wanted.

## The recovery path sent the operator the same message twice

When `lock_take` failed during recovery, the no-lock branch posted flat and then
fell through to the unconditional send below it. Measured 3 of 3 runs by a
tester, and **invisible to every case in the plan, because all of them counted
"at least N"**. A test that cannot distinguish one from two is not a delivery
test. Now fixed, with a case that counts exactly-once against parent and tip.

## Threading holds to about 3 seconds a call, and that is a hard trade

Measured at N=2, 12 rounds per setting: an API answering instantly, at 1s, 2s or
3s per call gives ONE thread every round. At 4s it splits in half the rounds, at
5s in most of them. **No message is lost at any latency** - the degradation is
toward an extra thread, never toward silence, which is the direction this item is
allowed to fail in.

The cause is arithmetic and cannot be tuned away. A losing run has to wait for
the winner to publish its map line, which cannot happen until the winner's post
RETURNS. The wait is 10 turns of about 380ms - 3.8 seconds - so it covers a call
of up to about 3 seconds and not one of 4. Lengthening it to cover 5 would put the
worst case at roughly 15.2s against the Stop hook's 15, and shortening it is what
two earlier attempts did to lose the guarantee at 2s.

So the ceiling is stated rather than hidden: **one thread per session for an API
answering within about 3 seconds**, which is what a local Mattermost does. Beyond
that the operator gets more than one thread and every message.

## Concurrency: solved at the sizes this actually sees, NOT at ten

The lock now has to be held across the root-creating POST, because the map line
cannot exist until that call returns. Releasing before it left the critical
section covering nothing — measured the moment the change was made, 12 of 12
rounds at N=2 opened two roots, indistinguishable from the unlocked code.

Measured against the pre-item notifier, 20 to 30 rounds per setting (the sample
size the plan now requires, after twelve was shown to establish nothing):

| concurrent first notifications | this version | pre-item |
|---|---|---|
| 2 | **1 root in 30 of 30 rounds, 0 messages lost** | 2 roots every round |
| 3 | 1 root in **29 of 30** rounds, 0 messages lost | 3 roots every round |
| 10 | 1 root in about half the rounds, some messages lost | 10 roots every round, 0 lost |

**THE N=3 CLAIM WAS WRONG AND A TESTER MEASURED IT WRONG WITH TWO INDEPENDENT
HARNESSES** — 5 of 30 rounds split, and 8 of 30 on a second instrument built to
rule out the first. Twelve rounds of green had been taken as exact; twelve rounds
cannot exclude a one-in-five rate, and the arithmetic to check that was never
done. The diagnosis was theirs too: losing runs never entered the lock's wait at
all, because the ENTRY gate still tested half the budget while the wait itself
had been decoupled from it — and startup alone spends three to four seconds of
ten. With the gate sized to what actually has to fit (the wait plus one post) and
startup made fork-free, N=3 measures 1 of 30. Better, and still not exact, so it
is not claimed as exact.

**N=10 IS NOT FIXED AND IS NOT CLAIMED TO BE.** Ten runs of one session starting
at the same instant saturate this machine, and the losers' waiting costs some of
them their message where the unlocked code loses none. Three separate reductions
helped and none closed it: a fork-free clock, a fork-free map read (the poll loop
was spawning `awk` twice per turn), and checking the map before attempting the
lock. Between passes the figure swings from 0 to 11 per 100, so it is also not a
number worth quoting to one significant figure.

The reason for shipping anyway, stated so it can be disagreed with: **a session
cannot emit ten simultaneous first notifications.** The overlap this item exists
for is a permission request against a turn completion — two.

**"At two and three the result is exact and stable" contradicted a line a few
paragraphs above it**, which says N=3 is reported at 1 in 30 and NOT claimed
exact - a tester found the pair. The accurate statement: N=2 is exact for an API
answering within about 3 seconds, N=3 is reported rather than claimed, and
neither is unconditional. If you think a synthetic N=10 should block a fix for a
real N=2, that is a legitimate position and the numbers above are what it turns
on - but read them with the latency ceiling attached, because a figure taken
against an instant API is not a figure about this notifier in use.

## What the lock still does not do, measured

- **At ~5s per call the budget cannot fit two calls**, and some messages are lost
  — but LESS than before. Measured, 6 rounds of 3 concurrent runs at 5s per call:
  this version lost 4 of 18 and the parent 15 of 18. **BOTH FIGURES WERE WRONG AND
  THE SIGN WAS INVERTED** - a tester re-measured the same setting at tip 13 of 18
  lost against the parent's 0 of 18, i.e. the opposite of what was published, in a
  paragraph offered as evidence that the trade was acceptable. It was not
  acceptable, and the figure was flattering in exactly the direction that would
  have let it ship.

  That regression is now fixed at the root rather than re-measured: the first send
  is given a floor timeout the way the pre-item sender's fixed `curl -m 8` is, so
  it is never skipped for want of budget. Delivery at 3s and 5s per call is 6 of 6
  against the parent's 6 of 6 at MM_DEADLINE_SECS 5, 8 and 10 - the settings where
  two successive rounds measured 0 to 2 of 6. An earlier draft of this note claimed the parent
  "survives it" and that the loss here was about one in twelve. Both were wrong,
  and in the flattering direction.
- **THE WORST CASE IS 13-14s, AND THE "ABOUT 5s" PUBLISHED HERE WAS MEASURED
  WITH A BROKEN INSTRUMENT.** The shim was written with an UNQUOTED heredoc, so
  `$@`, `$m` and `$want` were expanded when the file was written and it reduced to
  `sleep ""`. It never slept, honoured no `-m`, and the figure was startup plus
  the lock loop with zero network time - which the arithmetic should have given
  away, since the floor alone guarantees 8 seconds. A tester caught it and pointed
  out it was impossible.

  Measured with a shim verified to honour `-m`, live-held lock and an API that
  never answers, three passes: **14s, 14s, 13s**, against the pre-item sender's
  8-9s. The terms are startup (~1.5s), the lock wait (3.8s) and one floored call
  (8s). A tester measured a different path - a stale root and a server that takes
  6s before rejecting it - at 15.6-16.1s, which is over.

  **THIS IS THE THIRD FIGURE IN THIS ITEM PRODUCED BY A HARNESS RATHER THAN BY
  THE CODE**, after a python fork-count and a delivery count. The shared cause is
  a stub that does not implement the behaviour under test: a `curl` that ignores
  `-m` cannot measure a timeout, and one written with an unquoted heredoc is not
  the program you read. Verify the instrument against a case whose answer you
  already know, before trusting it on one you do not.
- **`MM_DEADLINE_SECS` must stay below the hook's own timeout.** The pathological
  case — a lock directory `rm` cannot clear — returns in 10s at the default 10,
  **and that scaling is NOT the lock's doing.** A tester ran the same black-hole
  API with the locking removed entirely and got 13.4s / 21.7s / 32.0s at 10 / 20 /
  30 — the same shape. It is `curl -m $(remaining)`: the budget IS the timeout, so
  raising the budget raises the worst case whether anything is locked or not. Two
  figures previously published here as evidence about the lock (19s, then 19.3s
  and 28.5s) measured the API timeout and were attributed to the wrong cause;
  neither reproduces on a third run either, which is what a figure with no control
  beside it is worth. What matters and does hold: the worst case exceeds a Stop
  hook's 15s limit for any MM_DEADLINE_SECS at or above about 12, and the default
  of 10 stays inside it. (An earlier draft said
  16s; re-measured on this build.) The default is safe; raising the variable above
  the hook limit is not, and nothing in the script can detect that.

## A pre-existing stderr leak, now fixed anyway

The `2>/dev/null` inside a command substitution silences the COMMAND; the warning
bash itself prints about a NUL byte in the captured output escapes it and reaches
the hook's stderr. A tester measured 2180 bytes from the lock's `at` read — which
this item introduced — and 217 bytes from the stdin read, which is pre-existing and
which an earlier version of this note excused on exactly that ground. Both are now
wrapped as `{ var=$(...); } 2>/dev/null`, because it is one line and the same
defect, and "the parent does it too" is a reason to check whether the parent is
right, not a reason to keep it. Measured after: 0 bytes on a NUL stdin.

## A trap that has now caught the developer twice

**Running this script from a worktree POSTS TO THE OPERATOR'S REAL CHANNEL.** The
worktree carries a real `.env` with a real bot token, and `ROOT_DIR` is derived
from the script's own location, so `bash scripts/notify-mattermost.sh "probe"`
is not a dry run - it is a live post. I did it twice in one session while
checking a stderr byte count, each time landing an unlabelled `@profnovice probe`
in `#claude-sessions`; both were deleted after checking their threads for foreign
replies, and the channel's newest post is the 2026-09-03 one either way.

Resolving to be careful did not work the first time. The mechanical rule is: any
behaviour check goes through the shim lab - a copied script root, a fake `curl`
first on `PATH` - and the live script is never invoked to observe anything,
including something as small as how many bytes it writes to stderr.

## Test harnesses here leave runaway processes, and they poison the next reading

An attempt-14 tester found **five orphaned `spin` processes from an earlier
round's shim lab still running hours later, roughly 2,600 CPU-seconds each**.
They were mine. Every timing taken while they ran is inflated by about 3.7x - the
lock loop read 13.7-16.7s alive and 4.0-4.3s after they were killed - which means
a worst-case figure published from this item was measuring its own leftovers.

Two things follow. Kill what you start, with a trap rather than a last line,
because the case that leaks is exactly the case where the script does not reach
its last line - the same argument `scripts/agent-harness/reap.ps1` is built on.
And before trusting any timing here, check what else is running: a figure taken
on a machine somebody else's test is still burning is not a figure about this
code.

## A trap for whoever tests this, part two: the harness

Every wrong answer in this round came from the measuring instrument, not the code,
and each looked like a result:

- **A python forked per call, inside the curl shim.** Under 10-way concurrency some
  failed to start, those calls vanished from the log, and the harness reported the
  PARENT delivering 3 of 10 — not credible, which is the only reason it was caught.
  The shim now appends the raw payload and nothing else; classification happens once,
  afterwards. A measuring instrument that is itself racy cannot measure a race.
- **A flat post and an announce both have an empty `root_id`.** Counting empty
  roots as threads reported the fix's own correct behaviour as a failure. Classify
  by what the post IS, not by one field being empty.
- **`grep -c` prints 0 AND exits 1**, so `$(grep -c ... || echo 0)` yields "0\n0"
  and every arithmetic test downstream dies.
- **A variable collision** between the round counter and a newly added corrupt-line
  counter silently zeroed the results, printing an empty summary next to
  "worst=10" — two numbers that cannot both be true, which is what gave it away.

If a measurement here surprises you, suspect the harness first. It was wrong four
times in one round; the script was wrong four times too, and telling those apart is
most of the work.

## A trap for whoever tests this

There is no staging Mattermost: tests post to the operator's real channel. Label
every test post, delete every one, **replies before roots**, and read a root's
thread for a foreign author first — an early attempt destroyed two genuine
operator replies by deleting a root they had replied under. Two things cannot be
undone: `total_msg_count` never decrements, and deleting posts does not clear the
mention badge.

## The method lesson, which outlived every specific bug here

Fixes kept being validated against the case that exposed them rather than the
class, and each such fix created the next defect:

- the key was made alphanumeric and the gate that consumes it was left filtering
  hex, so a verbatim-listed session was silenced;
- truncation was applied to every id, so two ids sharing eight characters
  collided where the previous scheme kept them apart;
- a temp file was given a unique name to fix a race, and the read-modify-write
  underneath it was left alone — measured: no improvement.

What closed them was structural, not another patch: one normaliser called by both
consumers, truncation only for uuid-length strings, and an append-only map read
last-wins — which deletes the race rather than renaming it. **A rename is not a
synchronisation primitive; not rewriting the file is.**
