# Mattermost session threading — findings (2026-09-10)

Sink for the `mmthread` item.

**This note carries no history of its own attempts.** It used to, and that is why
its claims failed five test rounds running: a figure was found wrong, the
correction went into a commit message, and the wrong line stayed here. A running
narrative of its own drafts cannot converge — `git log -- documentation/notes/`
holds the history and, unlike a document, cannot go stale. What is below is
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
- **The thread map is never pruned.** It grows for the life of the machine.
  Measured harmless: 200k lines costs ~2.8s, and because the deadline is
  wall-clock from script start, file work eats the HTTP budget rather than adding
  to it.
- **Three python invocations per post.** If `python` leaves `PATH`, the script
  exits 0 having sent nothing. Pre-existing, widened by this item from one to
  three.
- **A message body containing `session <8 hex>` steers the post into that
  session's thread.** Where the Notification hook supplies its own prefix, the
  prefix wins.
- **IPv6-style ids, ids under 8 characters, and ids with no alphanumerics** all
  normalise to something usable or, in the last case, to nothing — and an
  unidentifiable session is treated as not-on-the-list when a list exists.

## The inbound half does not exist, and the channel choice has a consequence

The operator can now reply in a thread, but the IDE session cannot read the
reply. Worse, in `#claude-sessions` a reply is consumed by the **bridge**, which
starts a new headless session under it — observed when the operator replied to a
test post and session `9fae8f75` appeared (`state/audit.jsonl:5533`). So a reply
looks answered and is answered by something else. Threading makes replying
possible; it does not make it effective.

## The watchdog's own alerts are a different audience

They still go to `#claude-code`. A crash-loop page is not session chatter, and
moving it is a separate decision. Its known-false `BACKUP STALE` warning — a
watchdog run from a git worktree looking for backup directories that only exist
in the main checkout — is recorded with the `crashloop` item, on the branch where
that file lives.

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
