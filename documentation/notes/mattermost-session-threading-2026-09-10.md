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
   pushed. `mention_count` comes from
   `GET /api/v4/users/me/teams/{team}/channels/members` — which `bot-claude`
   cannot read for the operator (403), so treat the 0 as CORROBORATED, NOT READ:
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

  The lock is also a new way to go silent, so it expires two ways: a readable
  timestamp older than the whole wall-clock budget cannot belong to a live run, and
  an unreadable one persisting for seconds means the holder died between its
  `mkdir` and its write. Failing to take it is never fatal — the run proceeds
  unlocked, which is exactly the behaviour it replaces.

  **And the first version of the lock rebuilt the outage inside the fix for it.**
  It waited until 3 seconds of the 10-second budget remained, which left one
  announce and one message to share 3 seconds; `post` skips a call with under 2
  seconds left, so a contended lock would have waited politely and then said
  NOTHING. Measured, then fixed by reserving two thirds of the budget for posting —
  waiting longer bought nothing anyway, since the winner holds the lock for a
  single API call. A correct synchronisation primitive is not the same thing as a
  correct message, and the case that caught it was the one that asked what happens
  when the lock is held by something still alive.
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

## A pre-existing stderr leak, reported by a tester and left alone

At `scripts/notify-mattermost.sh`, the `2>/dev/null` on the stdin read guards
`cat`, not the command substitution around it, so a literal NUL byte arriving on
stdin leaks one bash warning to stderr. Exit is still 0 and the message is still
sent. The parent does the identical thing, and neither real hook can deliver a NUL
— it is recorded because it was found, not because it needs fixing.

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
