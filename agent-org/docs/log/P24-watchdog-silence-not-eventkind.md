# P24 — the stall watchdog must key on SILENCE, not the event kind (evidence: gym-022, 2026-07-22)

## Evidence

gym-022 abandoned its first turn and then produced **zero events for 2 hours** (open, workers
suspended, never re-engaged):

```
22:36:33  worker_turn_abandoned   <- terminal event; NOTHING for the next 2h
lifecycle: open   drain_round: 0   scope_completed: 0
```

The effort's last event is `worker_turn_abandoned`, which is **NOT in `_STALL_MIDDISPATCH_KINDS`**,
so the watchdog's kind-gate (`orchestrator.py:2249` — `if kind not in _STALL_MIDDISPATCH_KINDS:
continue`) classified it as "awaiting the operator" and skipped it on every 240s tick for two hours.

## Root cause — a self-inflicted whack-a-mole

`worker_turn_abandoned` is the event **P21 F1 itself added** (to rotate the session on an abandon).
It made the session-rotation work, but it also moved the effort's *terminal* event from the covered
`wake_done` to the uncovered `worker_turn_abandoned` — re-opening the exact gap P21 F4 closed for
`check_exec`, which had re-opened the gym-008 gap for `wake_done`. **The allow-list has now been
defeated three times** (wake_done → check_exec → worker_turn_abandoned). subagent B's P21 review
named this: *"the allow-list approach is fragile — one trailing event re-opens the hole."* It was
right; keying recovery on the event KIND is the defect.

Also uncovered: `stall_recovered` and `stall_tree_discarded` (P16/P23 events) — the next two moles.

## The fix — key on silence, gate on authority (design §8)

ORCHESTRATION-DESIGN §8 is titled *"Liveness — silence detection, not a timer"* and states the
mechanism: *"has the worker emitted any agent-loop event in the last T?"* The kind allow-list is a
workaround that drifted from that principle. P24 restores it: the idle-effort sweep
(`_sweep_stalled_efforts`, Arm 2) recovers ANY effort that is silent past the threshold, EXCEPT the
ones genuinely awaiting a human — decided by AUTHORITATIVE state, not by guessing from the last
event's kind.

1. **Add the authoritative human-gate exclusion.** Skip an effort that is in a pending-decision map
   (`_pending_plan` / `_pending_merge` / `_pending_capability` / `_pending_lifecycle`) — it is
   awaiting an operator `approve`/`merge`. This is the real check the kind-gate was a fragile proxy
   for (the 2026-07-16 `plan_drafted` incident: a plan gate registers `_pending_plan`, so this
   catches it directly).
2. **Invert the kind allow-list to a small deny-list** of terminal kinds that mean "correctly
   awaiting the human" and do NOT register a pending decision:
   `{plan_drafted, lifecycle_plan_drafted, worker_plan_stopped, capability_proposed, stall_escalated}`.
   `stall_escalated` is critical — the watchdog already gave up there and asked for a re-run;
   re-recovering it would be the loop the escalation exists to stop. Everything else — every
   mid-pipeline / recovery event, present and FUTURE — is recovered when silent. No more moles.

Existing exclusions (`frozen`, `_waiting_on` a human, parked, `_handoff_waiting`, `_delegating`) and
the bounded-recovery cap + escalation are unchanged.

## Alignment (checked)

- **Design.** §8 *"silence detection, not a timer"* — this IS §8, applied where the kind-gate had
  drifted from it. §4.5 — *"no timeout may bypass a human gate"* is preserved and STRENGTHENED: the
  gate is now the authoritative pending-decision + frozen + `_waiting_on` state, not an event-kind
  guess. §3.0 fail-safe — the deny-list + pending check err toward leaving a human-gated effort
  paused.
- **Research.** The paper's dropped-signal (F3): a refusal FREEZES the effort (caught by the frozen
  exclusion) and is never recovered around — unchanged. Small-model reliability lives in the
  deterministic bridge (ANALYSIS-frontier §3) — a robust, kind-agnostic recovery is exactly that.
- No new gate, no human removed from any decision. This is a reliability fix on the observe/recover
  half of the loop, and it makes the human-gate check MORE authoritative, not less.

## Plan
1. Arm 2 of `_sweep_stalled_efforts`: add the pending-decision exclusion; replace the allow-list
   check with the deny-list.
2. Tests: an effort silent-terminal at `worker_turn_abandoned` / `stall_recovered` / `check_exec` is
   recovered; one at `plan_drafted` (and one in `_pending_plan`), `worker_plan_stopped`,
   `stall_escalated` is NOT.
3. Full suite green → deploy → wipe arena → gym-023. Success: an abandon-terminal effort
   auto-recovers (no 2h stall) and the loop can reach `scope_completed`.

Deferred (watch gym-023): P16 discard wiping a COMPLETE uncommitted build (gym-022's other loss —
run the check before discarding; commit a coherent build, discard only wreckage). Address if gym-023
loops on build→stall→discard.
