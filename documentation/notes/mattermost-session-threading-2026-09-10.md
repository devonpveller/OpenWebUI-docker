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
   `GET /api/v4/channels/{channel_id}/members/{user_id}`, which `bot-claude`
   cannot read for the operator (403). **An earlier draft of this line cited
   `GET /api/v4/users/me/teams/{team}/channels/members` instead, which returns
   200** — it is the caller's OWN membership rows, not the operator's, so it could
   never have carried the figure and the 403 it blamed was never there. Caught by
   a tester actually issuing both calls. Treat the 0 as CORROBORATED, NOT READ:
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
   `scripts/mattermost-mcp/server.py:200`. The IDE notifier was the one sender
   that did not.

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

  Measured at the end, 20 rounds of 10 concurrent runs each: **parent — 10 roots
  every round, 20 of 20; this version — 1 root every round, 0 of 20, and 200 of
  200 messages delivered.** Also 1 root at 10-way concurrency with a 1s-per-call
  API, and three DIFFERENT sessions still open three threads.

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
  `scripts/notify-mattermost.sh:81`:
  stdin redirected (both real hooks, since `</dev/null` is not a tty) **3 steady,
  5 on a first notification**; stdin skipped, as when a person runs it by hand or
  sets `MM_SESSION_ID`, **2 and 4**; the parent, **2**. Two earlier measurements
  disagreed because of exactly this, and because a shim directory written
  `C:/Users/…` is silently ignored by Git Bash — it must be `/c/Users/…`, or the
  counter reads zero and the run looks cheaper than it is.
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
  (`scripts/notify-mattermost.sh:147`), so a DIFFERENT uuid sharing its first
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

## What the lock still does not do, measured

- **A ~5s-per-call API still loses about one message in twelve** at 3 concurrent
  runs. Two calls do not fit a 10s budget at that latency, with or without a lock;
  the parent survives it only because every run announces its own root and never
  waits. This is the budget, not the lock, and the honest summary is that
  `MM_DEADLINE_SECS` must exceed twice the API's worst call time or some runs
  cannot both announce and speak. Nothing here degrades toward silence by design —
  it degrades toward an unthreaded message — but a budget too small for one call
  is silence whatever the design.
- **`MM_DEADLINE_SECS` must stay below the hook's own timeout.** The pathological
  case (a lock directory `rm` cannot clear) returns in 8s at the default 10, but
  16s at 20 or 30 — past the Stop hook's 15s. The default is safe; raising this
  variable above the hook limit is not, and nothing in the script can detect that.

## A pre-existing stderr leak, now fixed anyway

The `2>/dev/null` inside a command substitution silences the COMMAND; the warning
bash itself prints about a NUL byte in the captured output escapes it and reaches
the hook's stderr. A tester measured 2180 bytes from the lock's `at` read — which
this item introduced — and 217 bytes from the stdin read, which is pre-existing and
which an earlier version of this note excused on exactly that ground. Both are now
wrapped as `{ var=$(...); } 2>/dev/null`, because it is one line and the same
defect, and "the parent does it too" is a reason to check whether the parent is
right, not a reason to keep it. Measured after: 0 bytes on a NUL stdin.

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
