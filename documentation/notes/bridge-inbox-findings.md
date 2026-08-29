# Durable inbox (dfu-inbox) — findings sink

Incidental discoveries from the U0(c) durable-inbox work, 2026-08-29. These are OUT OF
SCOPE for that item by its confirmed anchor and are recorded here rather than fixed in
place or dropped.

Held to the same standard as the artifact: every claim below names the file and line it
was checked at, and was read in the code, not inferred from a comment.

## F1 — the follows / auto-wake path has the SAME loss window (real, unfixed)

The anchor put the follows path out of scope and said explicitly that if it shared the
loss window, that was a finding rather than a fix. It does.

`Bridge.poll_follows` (`scripts/claude-sessions-bridge/bridge.py`, ~L1887–1974) does, in
this order:

1. Collects matching posts into `wake_batches` (in memory).
2. Advances each follow's cursor — `follows[fid]["last_seen"] = newest` — and then
   **persists it**: `if advanced: with self.state_lock: save_state(self.state)` (~L1955–1957).
3. **Only then** dispatches: `for bthread, batch in wake_batches.items(): self._dispatch_wake(...)`
   (~L1958+), which lands the wake in the in-memory lane-2 deque.

So a death between steps 2 and 3 — or after dispatch but before the wake's turn runs —
loses the wake exactly the way an operator message used to be lost: the cursor has already
moved past the post, so no restart re-reads it.

**Severity is lower than the operator-message case, and the reason matters.** A lost
operator message is a lost instruction with a human waiting on it. A lost wake means a
session is not told that something it subscribed to changed — recoverable if anything
later posts in that thread, permanently missed if nothing does. It is a real hole, not a
cosmetic one, but it is not the one that had a person on the other end of it.

**Additional wrinkle if this is picked up:** `_dispatch_wake` mutates follow bookkeeping
(`live["wakes"] += 1`, one-shot removal, max-wake eviction — ~L1998–2010) as part of
dispatch. A naive replay would re-run that accounting and could evict a follow twice, so
the fix is not simply "record wakes the way messages are recorded". The wake counter would
need to move to the same consumed-once boundary.

Not attempted here. It needs its own anchor.

## F2 — `processed` is capped at 500 and is a second, weaker dedup

`self.processed` is a `deque(..., maxlen=500)` (~L976) persisted into `state.json`. It is
the guard that stops a post being admitted twice.

With the inbox in place this is now belt-and-braces rather than the only defence, and the
cap is not currently a problem: `last_seen` is the primary cursor and it is monotonic, so
an id ageing out of `processed` is only reachable if `last_seen` also went backwards.

Recorded because the interaction is non-obvious and a future change to either mechanism
should know the other exists. No action proposed.

## F3 — the inbox deliberately does not cover lane-2 items

`Inbox.record` returns False for an empty `post_id`, which is every lane-2 wake item. This
is a decision, not an oversight: wakes carry no post id, and keying them all to `""` would
collapse distinct wakes into a single entry that consumed each other.

Consequence: the durability guarantee this item delivers is scoped to **operator messages**
(lane 1). It should not be described as "the bridge cannot lose work" — see F1 for what is
still losable.
