# Proposal — Follow-subscription signal filtering (decision-required vs progress)

**For:** a session working on the **agent-bridge Mattermost follower/event-subscription** (the
`follow_thread` / auto-wake mechanism in `scripts/claude-sessions-bridge/`), not the agent-org
orchestrator.
**Author:** Claude session, 2026-07-17, from lived experience following a live gym round.
**Status:** proposal — not built. Self-contained; you need no prior context.

---

## The problem, from real usage

A Claude session that `follow_thread`s a project channel is woken on **every** `bot-pm` post. Over one
gym round (~2h) that produced **~20 wakes**, of which **exactly two** needed the session to do
anything:

- the Stage-3 **plan-approval gate** (`Reply approve <effort> …`), and
- a **CONCERN/freeze** requiring an `approve|modify|abort` decision.

Everything else was progress narration — *"opened effort X"*, *"readiness ✓ dispatching a worker"*,
*"plan approved — dispatching"*, *"agent-bridge online"*, *"archived effort"*, delivery summaries.
Worse, several arrived **out of order / late**, so a post about an already-resolved gate looked
identical to a live one. The session had to spend a state-verifying tool call on nearly every wake
just to decide "is this actionable?" — and on one occasion a stale *"plan approved"* echo for an
already-aborted effort was a genuine near-miss for acting on dead state.

**Why this matters beyond annoyance.** The end goal is a *dark factory*: the human (or a supervising
session) governs **by exception**. A subscription that pushes every lifecycle event makes the
supervisor's attention O(n) in *events* instead of O(1) in *decisions* — the exact tax the
architecture is trying to remove. It also trains the reader to skim, which is how a real gate gets
missed.

---

## Proposed change

Make the follow subscription **classify** each candidate post before waking, and default to waking
only on **DECISION-REQUIRED**.

### 1. Two classes

| Class | Meaning | Default behaviour |
|---|---|---|
| **DECISION-REQUIRED** | The org is *blocked* awaiting a human/session decision | **wake** |
| **PROGRESS / FYI** | Narration; nothing is blocked | **do not wake** — buffer it |

**Decision-required** should be recognised *structurally*, not by keyword-guessing the prose. The
orchestrator already emits the decision grammar in its posts; the reliable markers are:

- a post containing the explicit reply grammar — `` `approve <id>` ``/`abort`/`modify`, or
  *"Reply `approve|modify|abort` …"*
- a **CONCERN / frozen** post (`state=frozen`)
- a **merge gate** invitation (*"say **merge it**"*)
- an **escalation** (`⛔` / *"needs your attention"* / stall-escalated)

Everything else (opened, readiness, dispatching, plan-approved-confirmation, delivered summary,
archived, *"agent-bridge online"*) is PROGRESS.

**Better still (preferred if the bridge can carry it):** have the orchestrator stamp its posts with a
machine field — e.g. Mattermost post `props.ao_class = "decision" | "progress"` (it already sets
`props.from_claude` on some paths, so the mechanism exists). Then the follower filters on the prop and
never parses prose. Prose-matching is the fallback for un-stamped posts.

### 2. Buffer, don't discard

PROGRESS posts must not be lost — they're the audit trail. Accumulate them and deliver on the **next**
wake as a compact digest (*"12 progress posts since last wake: …"*), or on demand. The session stays
informed without being interrupted.

### 3. Staleness guard (this is the near-miss fix)

Every wake payload should carry enough to tell live from replayed:

- the post's **timestamp** and an explicit **age** (*"posted 47 min ago"*),
- and for decision posts, the **current state of the referenced effort** at wake time (e.g.
  `lifecycle=aborted` / `state=frozen` / `gate=open`).

A decision post for an effort that is no longer in a blocking state should be **downgraded to
progress** (or dropped) rather than woken on. This alone removes the class of error where a session
acts on a resolved gate.

### 4. Config, not hardcoding

Per-follow options with safe defaults:

```
wake_on: decision      # decision (default) | all | none
digest_progress: true  # deliver buffered progress on the next wake
max_wakes: 100         # existing cap, unchanged
```

`wake_on: all` preserves today's behaviour for anyone who wants it.

---

## Related, from the orchestrator side (alteration 3)

This proposal's value depends on the **decision** class being *honest* — a gate that fires on a guess
generates a false decision, which is worse than noise because it demands real human judgement.

Live example from the same round: the org's test-scope monitor **froze a healthy effort** on
*"fewer tests executed than expected."* Investigation showed the worker's tests were **purely
additive** (template 5 test functions → 48, +361/−0, all passing). A false positive that cost a
human decision. It's tracked as **register #26** in `agent-org/docs/P9-make-the-fixes-real.md`.

**Recommendation (orchestrator-side, separate from this proposal):** any gate that can *freeze* an
effort or *demand* a decision must have a **deterministic, falsifiable trigger** — e.g. compare the
delivery's test count to the **base commit's** and flag only a genuine *drop*; a failing executable
check; an irreversible action. An LLM "this looks off" judgement should be a **non-blocking advisory
note**, never a freeze. Fewer, truer decisions is what makes `wake_on: decision` viable.

---

## Acceptance criteria

1. Following a project channel through a full round produces **wakes only for** the plan gate, a
   concern/freeze, a merge gate, or an escalation.
2. Progress posts are **buffered and summarised**, not lost.
3. A decision post whose effort is no longer blocking does **not** wake the session.
4. Every wake states the post's **age** and the referenced effort's **current state**.
5. `wake_on: all` reproduces today's behaviour exactly.

## Files likely involved

- `scripts/claude-sessions-bridge/bridge.py` — the follow poller / wake path (`poll_follows`,
  `_renew_follows`).
- `scripts/claude-sessions-bridge/approval_server.py` — the `follow_thread` tool surface (where
  `wake_on` / `digest_progress` options would be accepted).
- Follow state lives in `scripts/claude-sessions-bridge/state/state.json`.
- *(Optional, orchestrator-side)* whichever comms path posts to the project channel, to stamp
  `props.ao_class`.

## Why it's worth doing

It converts a supervising session's attention from **O(n) in events** to **O(1) in decisions** — the
same economics the acceptance-corpus work achieved for code review. It is the interface half of
"human as governor," and without it every autonomy gain is partly cancelled by notification load.
