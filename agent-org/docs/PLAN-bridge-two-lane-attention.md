# Plan — Bridge two-lane attention model (user cut-through + valved background)

**For:** the Claude-sessions bridge (`scripts/claude-sessions-bridge/`).
**Status:** ✅ **BUILT 2026-07-18** (Phases 1–5), 41 unit tests green (`test_lanes.py` 21 +
`test_follows.py` 20), deployed via `restart_bridge.py`. **Live behaviour not yet exercised
end-to-end** — operator testing pending. UNCOMMITTED. **Absorbs**
`PROPOSAL-follow-subscription-signal-filtering.md` (that proposal's §1–§4 land here as Phase 5).
**Origin:** 2026-07-17 incident — session `abbd8817` appeared unreachable from Mattermost.
**Author:** Claude session, from operator design discussion.

---

## The incident that motivated this

Operator messages to session `abbd8817` (bridge thread `ktf6s349…`) "weren't getting through."
They *were* being received and queued — the bridge logged every one. They were stuck behind a
backlog of **follow auto-wake turns**: **80 of that session's 193 turns (41%) were auto-wakes**
from follow `fw-d2e30c`, which wakes on every `bot-pm` post in `#management`. During an active
gym round that is a firehose. Each wake is a full Opus turn (5–25 min, $2–21). The operator's
message sat behind them.

Three compounding causes, all still live:

1. **One FIFO lane.** Auto-wakes (`bridge.py:1023`) and operator messages (`bridge.py:1110`) go
   into the *same* per-thread queue. Ordering is arrival-time, so the human loses.
2. **No admission control.** `_dispatch_wake` enqueues a new turn unconditionally — it never asks
   whether a turn is already running or whether a human is mid-conversation.
3. **No protected question window.** Nothing distinguishes "turn finished" from "turn is waiting
   on the human," so background wakes run *between* a question and its answer and mutate the
   session the question was asked in.

Two latent bugs found while diagnosing, both fixed by this plan:

- **Verdict swallow** (`bridge.py:1096`): while a turn is in flight, an operator message starting
  `yes|ok|approve|no|deny|stop|abort|…` is consumed as an approval verdict and **never queued**.
  An answer of "yes, go" to an agent's question silently vanishes.
- **Session bloat** (out of scope, tracked separately): `abbd8817`'s session file is **15 MB /
  7,174 lines**, which is why turns cost $15–21 and some fail with `session:null`. This plan does
  not fix that; it stops the flood that *feeds* it.

---

## The model

Two lanes, one control law.

| Lane | Carries | Gating |
|---|---|---|
| **Lane 1 — user** | Operator messages; answers to a parked question | **Ungated.** Always admitted, always ahead of lane 2. |
| **Lane 2 — valved** | Follow auto-wakes, PM updates, cron, other background sources | **Valved.** Held while lane 1 is active. **Deferred, never discarded.** |

**The valve is the gate on lane 2, and its state is a pure deterministic function of lane 1:**

- Operator message queued or running → **CLOSED**
- Agent parked on `ask_user` → **stays CLOSED** until the answer arrives or the timer fires
- Turn ends with no parked question → **OPEN**

Determinism comes from two explicit signals, never from inference:

- **Lane 1 activity** — a literal operator message. Already unambiguous (`from_bridge` /
  `from_claude` / `from_webhook` props + `OPERATORS` membership).
- **`ask_user` tool invocation** — the agent *declares* it is blocked. No prose heuristics
  (ends-with-"?" is not determinism).

Same architectural move in both directions: **the producer declares intent; the consumer never
guesses.** Phase 5's `props.ao_class` is the third instance of it.

### Non-goal: filtering what the primary sees

The primary observes **every** alert. Phase 5 changes what **spawns a turn**, not what is
**visible** — progress posts are buffered and delivered as a digest. Nothing is dropped.

---

## Implementation phases

Each phase leaves the bridge working, ships independently, and is behind a flag defaulting to
**today's behaviour**.

### Phase 0 — Preconditions

- [ ] Every phase gets an env flag, default = current behaviour. Nothing changes on deploy until
      flipped.
- [ ] Confirm the deploy path: the bridge runs under a Scheduled Task with an instance lock;
      restart via `restart_bridge.py`. **A restart kills in-flight turns** — deploy when idle.
- [ ] Extend the `test_follows.py` pattern. Lane arbitration and valve transitions are pure logic
      and must be unit-testable **without** a live Mattermost.

### Phase 1 — Lane split (foundation)

Replace the single per-thread `queue.Queue` with **two queues + a condition variable**.

> **Design note.** A single `PriorityQueue` was considered and rejected: Phase 4 must purge *lane 2
> only*, and Phase 5 must re-validate *lane 2 as a collection* at release time. Both are awkward
> with one priority queue (dequeue-and-push-back) and trivial with two. Two queues also encode the
> operator's mental model literally rather than emulating it.

- Per thread: `lane1: deque`, `lane2: deque`, one `threading.Condition`.
- Worker loop: wait on the condition; take from `lane1` if non-empty; **else** if the valve is open,
  take from `lane2`; else keep waiting.
- Items carry `kind` (`user` | `wake`) and the originating post id (already threaded through as
  `trigger_post` / `pid`, used for the ⏳ reaction at `bridge.py:642`).

**Touch points:** `bridge.py:532` (queue construction), `:542–548` (`ensure_worker`), `:550–567`
(`worker`), `:1023` (auto-wake enqueue → lane 2), `:1110` (operator enqueue → lane 1).

**Acceptance:** an operator message queued behind N auto-wakes runs **next**, after the in-flight
turn completes. FIFO preserved within each lane.

### Phase 2 — The valve

- Per-thread state: `valve: open|closed`, `awaiting_answer: bool`, `question_deadline: ts`.
- Transitions exactly as the control law above. Enforced in the **bridge** (the persistent layer) —
  the agent is ephemeral and cannot hold valve state between turns.
- **No preemption.** A running turn always completes; the valve governs admission, not interruption.

**On re-open: one catch-up turn** (operator-set). Held lane-2 items are **coalesced into a single
turn** rather than replayed one-by-one — everything is still observed, without a post-conversation
turn-storm. This is the *same machinery* as Phase 5's progress digest: build **one catch-up
composer** and both features use it.

**Acceptance:** during an operator exchange, zero lane-2 turns start. On quiescence, all held
lane-2 items arrive as exactly one coalesced catch-up turn, in order, nothing dropped.

### Phase 3 — `ask_user` (the deterministic question signal)

A **blocking** tool, modelled on the existing approval relay (`approval_server.py`), which already
parks a live turn and resumes it from a side channel — this is a generalisation of that from
yes/no to free-form.

- Agent calls `ask_user(question)` → tool posts the question to the thread, registers a pending
  question, and **parks**, holding the turn open.
- The next operator message in that thread routes to the parked tool and returns **verbatim** —
  the agent continues **in the same turn**, context intact (critical given a 15 MB session).
- Timeout → **30 minutes** (operator-set) → returns a `TIMED_OUT` sentinel; the agent decides
  (proceed/abort) and ends the turn.
- A 30-minute park makes **semaphore-release-while-parked mandatory, not optional** — without it a
  single unanswered question holds half the bridge's total turn capacity for half an hour.
- **Release the global semaphore while parked.** With `MAX_CONCURRENT = 2` (`bridge.py:134`) a
  parked question otherwise holds **half** the bridge's total turn capacity for the whole timeout,
  starving every other thread. The turn is idle-waiting, not computing.
- **Fixes the verdict swallow:** while a question is parked, the next operator message is *known*
  to be the answer, so it bypasses `VERDICT_RE` (`bridge.py:1096`) and is delivered as free-form
  text. "yes, go" reaches the agent as an answer, not an approval verdict.

**Acceptance:** a full question→answer round-trip completes inside one turn; no lane-2 turn runs
between question and answer; an unanswered question releases the valve at the deadline; a parked
question does not consume a concurrency slot.

### Phase 4 — Operator overrides

- **`!stop`** (operator-set; must **not** be bare `stop`/`abort`, which `VERDICT_RE` already
  claims; parse it *before* the verdict check): purge **lane 2**, keep **lane 1** intact, and abort the
  in-flight turn **only if it is a lane-2 turn** (never kill the operator's own work). Reuse
  `kill_tree` (already used by the turn-timeout timer, `bridge.py:478`). Post a receipt with counts.
  Scope: the thread it is posted in.
- **👎 cancel:** react `:-1:` on a queued message to skip **that** item. Implemented as a
  **tombstone** (lazy deletion): reaction → post id added to a `cancelled` set → the worker skips
  it on dequeue, flips ⏳→🚫, audits `turn_cancelled`. Also works on auto-wake note posts, so a
  single wake can be dropped without a full stop.
  - Detection: **bounded** REST reaction-poll over *pending items only* (usually 0–3; zero calls
    when queues are empty). Must not add unbounded polling — `WinError 10055` socket exhaustion is
    a recurring failure in this bridge's history.
  - Operator reactions only (reactor ∈ `OPERATORS`), so a bot's 👎 cannot cancel.

**Acceptance:** `!stop` clears the backlog while every operator message survives; 👎 skips exactly
one item and nothing else.

### Phase 5 — Classification, digest, staleness *(absorbs the other session's proposal)*

- **Class:** read `props.ao_class = decision|progress`, stamped by the orchestrator. Prose-matching
  is the fallback for un-stamped posts.
  - **Fail open.** Unstamped *or* ambiguous → treat as **decision** and wake. Under-waking means a
    **missed gate**, which is the dangerous direction. Never silently downgrade.
- **`wake_on: decision | all | none`** per-follow config. **Start at `all`** (today's behaviour),
  flip to `decision` once classification is proven.
- **Digest:** progress posts are buffered and delivered on the next wake. Nothing is lost. The
  valve gives the digest a natural delivery moment — it rides out when lane 2 reopens.
- **Staleness guard — validate at *release*, not enqueue.** This is load-bearing *because* of
  Phase 2: deferral **manufactures** staleness. A held "plan approved" for an effort aborted during
  the operator's conversation is precisely the near-miss the proposal documents. Every wake carries
  the post's **age** and the referenced effort's **current state**; decision posts for efforts no
  longer blocking are downgraded to progress.

**Acceptance (extends the proposal's):**
1. A full round wakes only for plan gate / concern-freeze / merge gate / escalation.
2. Progress is buffered and summarised, never lost.
3. A decision post whose effort is no longer blocking does not wake.
4. Every wake states post age + current effort state.
5. `wake_on: all` reproduces today's behaviour exactly.
6. **Safety twin to (1): zero blocking gates went un-woken over the round.** Criterion 1 alone is
   satisfiable by *missing* a novel decision type — this is the criterion that actually matters.

**Touch points:** `bridge.py:904` (`poll_follows`), `:967` (`_dispatch_wake`), `approval_server.py`
(the `follow_thread` tool surface, for `wake_on` / `digest_progress`), `state/state.json` (follow
records).

---

## Out of scope (tracked, not built here)

- **Alteration 3 — honest decision class (orchestrator-side).** `wake_on: decision` is only
  trustworthy if a gate that can *freeze* an effort has a **deterministic, falsifiable trigger**
  (e.g. test count vs the **base commit**, flagging only a genuine drop). An LLM "this looks off"
  must be a non-blocking advisory, never a freeze. Worked example: **register #26** in
  `P9-make-the-fixes-real.md` — a false-positive freeze on a healthy, purely-additive delivery.
  **This is a dependency of Phase 5's value, not of its implementation.**
- **Session bloat.** `abbd8817` at 15 MB / 7,174 lines. Needs a fresh session, separately.
- **`WinError 10055`** socket exhaustion — pre-existing; this plan must not worsen it (see Phase 4).

## Decisions (operator, 2026-07-17)

1. ✅ **`ask_user` question timer = ~30 minutes.** Forces semaphore-release-while-parked (Phase 3).
2. ✅ **Valve re-open = one coalesced catch-up turn**, not burst-replay. Shared "catch-up composer"
   with Phase 5's digest.
3. ✅ **Full-stop keyword = `!stop`** (option (b) below). `/stop` was ruled out — Mattermost eats it.
   A real slash command can be added later as a front-end on the same handler.
4. ✅ **Classifier ships in SHADOW mode** — wakes unchanged (`wake_on: all`), suppressions
   **logged only**. Flip to `wake_on: decision` after one round of clean evidence.

### ⚠️ Why not `/stop` (resolved — kept as rationale)

Mattermost intercepts any message beginning with `/` as a **slash command**. An unregistered
`/stop` is rejected client-side ("command with a trigger of 'stop' not found") and **never becomes
a channel post** — so the bridge, which works by polling posts, would never see it. Two ways to
honour the `/stop` intent:

- **(a) Register a real Mattermost slash command** pointing at an endpoint on `approval_server.py`
  (already an HTTP server). Proper slash-command UX, autocomplete, works from any channel. Costs:
  an integration + token + endpoint, and it bypasses the post-polling path so it needs its own
  auth/routing.
- **(b) Use a non-`/` token** that still reads as a command — `!stop`, `.stop`, `stop!`. Zero
  integration work; rides the existing post path.

**Recommendation: (b) now, (a) later** if the slash UX is wanted — it is purely a front-end on the
same handler.

### Rephrased — decision 4 (classifier rollout mode)

When Phase 5 ships, how live should the decision/progress classifier be?

- **Shadow (recommended).** Everything still wakes you exactly as today (`wake_on: all`), but the
  bridge **logs what it *would* have suppressed**. Zero risk of a missed gate, and it produces the
  evidence to flip confidently. No volume reduction yet — the valve (Phases 1–2) is what fixes the
  cut-through complaint; Phase 5 is the volume win, and it can wait for proof.
- **Live immediately** (`wake_on: decision`). Volume drops at once (~20 wakes → ~2), but any
  classifier error is a **missed gate** before you have data showing it is trustworthy.

## Coordination

Phases 1–4 and Phase 5 modify the **same code path** (`poll_follows` / `_dispatch_wake` / the worker
loop). If another session builds the filtering proposal independently, the two will collide. This
plan exists so it lands as **one coherent change to one code path**. Sequence or merge — do not run
both concurrently.

## Suggested order

**1 → 2 → 3** delivers the operator's actual complaint (cut-through + protected Q&A).
**4** adds manual escape hatches. **5** cuts volume. Phases 1–2 alone would have prevented the
incident that started this.
