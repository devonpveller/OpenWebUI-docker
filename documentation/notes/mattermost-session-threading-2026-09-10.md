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

2. **Nothing mentioned the operator.** `mention_count` on `#claude-code` is 0, so
   nothing bolded the channel or pushed. (`last_viewed_at` read NEVER when first
   measured; it has moved several times since and is not re-derivable — Mattermost
   keeps no history of it.)

3. **Nothing threaded.** `root_id` appeared nowhere in
   `scripts/notify-mattermost.sh`. It was NOT absent from the repo — the
   agent-bridge threads its own posts
   (`agent-org/agent-bridge/app/adapters/mattermost.py:100`), as do
   `scripts/claude-sessions-bridge/bridge.py:499` and
   `scripts/mattermost-mcp/server.py:200`. The IDE notifier was the one sender
   that did not.

## Residuals — true, measured, NOT fixed

- **Concurrent first notifications from ONE session open several threads.** Three
  at once produced three roots and three map lines. The map race is gone (the map
  is append-only and read last-wins), but the announce itself is unsynchronised.
  Needs a lock, which this item did not add.
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
(`scripts/claude-sessions-bridge/state/audit.jsonl:5533`). So a reply
looks answered and is answered by something else. Threading makes replying
possible; it does not make it effective.

## The watchdog's own alerts are a different audience

They still go to `#claude-code`. A crash-loop page is not session chatter, and
moving it is a separate decision. Its known-false `BACKUP STALE` warning — a
watchdog run from a git worktree looking for backup directories that only exist
in the main checkout — is recorded with the `crashloop` item, on the branch where
that file lives.

## The removed allowlist's backup is not gitignored

`scripts/.mm-notify-sessions.removed-2026-09-11.bak` holds a session uuid, is
untracked, and `.gitignore:65` covers only the exact path
`scripts/.mm-notify-sessions` — so a broad `git add` in the operator's checkout
could commit it. The ignore rule is widened to the backup in this item.

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
