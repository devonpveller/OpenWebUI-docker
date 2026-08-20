# P31 — reliability: an ENVIRONMENT failure is not a WORKER failure (design §8 + infra-freeze pattern)

## The evidence (gym-030 → 033, 2026-07-26/27)

Since the disk-compaction maintenance restarted all 87 containers, four convergence runs died — none on
convergence code, all on the org **misattributing an environment failure to the worker/capacity plane**:

```
gym-032  15:18:19  worker_dispatch_failed {reason:"unreachable", error:"Temporary failure in name resolution"}
gym-032  15:18:35  effort_parked          {reason:"no_worker_slot", stage:"delegate"}     ← never resumed
gym-030  00:28:01  worker_dispatch_failed {reason:"unreachable", error:"All connection attempts failed"}
gym-030  00:28:01  effort_parked          {reason:"no_worker_slot"}                        ← never resumed
gym-031  00:29:21  effort_parked          {reason:"no_worker_slot", stage:"delegate"}      ← never resumed
gym-030  22:20:22  stall_escalated        {reason:"worker_silent"}   (inference was shed: 503 llm-queue cap)
gym-033  00:02:43  stall_escalated        {reason:"worker_silent"}   (worker silent, but env was healthy — a genuine hang)
```

Two distinct failure modes, one root:
- **Stuck-park.** A `worker_dispatch_failed {reason:"unreachable"}` (a DNS blip, or inference shed with
  "All connection attempts failed") is parked as **`no_worker_slot`** — a *capacity* label. But the
  worker wasn't busy; it was *unreachable*. `_drain_parked_once` treats `no_worker_slot` as "normal,
  self-resolving — wait for a worker RELEASE." No release ever comes (the worker was idle, just
  unreachable), so the park sits forever — verified: gym-031/032 stuck with both workers `idle`, and
  neither `/nl re-run` nor a bridge restart woke them.
- **Environment-silence escalation.** When inference was shed (llm-queue at its connection cap, 503),
  workers went `worker_silent`; the stall watchdog recovered them, re-engaged into the *same broken
  environment*, and after the bounded cap `stall_escalated` — surfacing a code/worker gate for an
  infra problem the worker could never fix.

The org already has the right idea elsewhere — `_is_infra_failure` (§ infra-freeze-autorecovery: an
ENVIRONMENT/WORKSPACE symptom auto-clears + retries bounded, real code deviations stop). P31 extends that
principle to the **inference / dispatch / park** plane, where it is currently missing.

## Why this maps to ORCHESTRATION-DESIGN

- **§8 (Liveness — silence detection).** The silence signal is correct; what's missing is the **cause**.
  A worker silent because *inference is down* is not a hung worker — it is a stalled environment. §8's
  "has the worker emitted an agent-loop event?" must be paired with "and is the environment even
  reachable?" before the answer is "the worker hung."
- **infra-freeze-autorecovery (built).** ENVIRONMENT symptoms auto-clear + retry bounded; only real code
  deviations stop for the human. Inference-shed / unreachable-dispatch are ENVIRONMENT symptoms and must
  be handled the same way, not escalated as worker hangs.
- **§2 (gates produce honesty).** The org must attribute the failure honestly — "the environment is
  down, waiting for it to heal" — not "the worker is stuck, escalating."
- **§3.0 fail-safe.** Err toward *pausing* (park-until-healed), never toward a false code escalation.

## A note on the operator's challenge (2026-07-28) — why the fix is cause-aware, not blind

The operator pushed back correctly: "resume a park and you just re-run the same ineffective context →
same outcome." The distinction that resolves it: **a bad/ineffective context never *parks*.** It goes
down the *recovery* path — `worker_silent` → `stall_recovered` (P23 rotates to a FRESH session) →
`stall_escalated` past the cap. gym-033 is exactly this (env healthy, a real hang), and the existing
machinery handled it right: it did NOT blindly re-run; it rotated and then escalated. P31 does **not**
touch that path. What P31 fixes is the *other* cause — an ENVIRONMENT outage — which the recovery
machinery (built for transient worker issues) mis-handles by re-engaging into the same wedged env and
then escalating a code/worker gate. The gate is a **deterministic** signal (all workers quarantined),
never an LLM verdict, and a genuine hang on a healthy env still escalates unchanged.

## The fix (staged)

### F31.1 — a silent turn during an ENVIRONMENT outage HOLDS, it does not escalate *(BUILT)*
Deterministic signal: `scheduler.environment_down()` is True iff there are workers and **every**
non-retired one is currently health-quarantined (unreachable / 502-503 shed / 409-wedged) — a
busy-but-reachable worker is `computing`, never quarantined, so slot contention never trips it. The
stall watchdog, in both arms (a hung worker and an idle-silent effort), consults it *before* the
escalation/re-engage decision (`_env_wait_hold`): if the environment is down it audits `env_wait`, posts
ONE honest "waiting on the environment" note, and holds — no `stall_escalated`, no re-engage into a dead
env. `env_wait` is excluded from `_last_event`, so the silence clock stays anchored to the last REAL
event and the effort **auto-resumes the instant a worker is reachable again** (the infra-freeze
contract) — no human re-run.

**Why it fixes it:** gym-030's three `stall_escalated` fired because the watchdog, at its recovery cap,
could not tell "the worker hung" from "inference is down." The deterministic all-quarantined check
supplies exactly that missing cause; gym-030 now holds-and-auto-resumes, while gym-033 (healthy env,
real hang) still escalates. It touches only the recovery path's *escalation decision* — not the fragile
park-resume — so it cannot reintroduce a stuck-park or a blind-resume loop.

### F31.2 — the stuck-park is HEAD-OF-LINE BLOCKING, not a bad resume token *(NEXT — evidenced)*
Traced from the audit + bridge logs (2026-07-28), the real mechanism is different from the first guess:
- gym-030 parked `no_worker_slot` at 00:28:01; the bridge log shows `resuming parked effort
  effort-gym-030 … reason=no_worker_slot` **repeated many times** — the capacity loop *was* firing and
  resuming it, but it re-parked every time (the env was down, so `acquire` re-raised `NoCapacityError`).
- gym-031 parked `no_worker_slot` at 00:29:21 — **~1 min later** — and shows `parked=1, wake_queued=1,
  worker_acquire=0`: parked once, **never resumed, zero acquires**. gym-032 (next day) the same.

`_drain_parked_once` resumes only the **oldest dispatchable** park per tick (FIFO: `for t in parks.all():
if can_dispatch: break`), and a `no_worker_slot` park is **never aged out** — only `inference_backpressure`
bumps attempts + escalates at the cap (orchestrator.py:2532); slot-contention "waits patiently, never
escalates on count." So gym-030, stuck at the FIFO head re-parking forever during the outage,
**starved every newer park behind it** (gym-031, gym-032). That is head-of-line blocking, not a stale
`from_step` token (both parked at `from_step:1`; the token was never even replayed — the resume never
reached them).

The evidence-backed fix has two independent parts, both reusing F31.1's deterministic signal:
1. **Gate the whole drain on `environment_down()`.** When the fleet is quarantined, don't spin the drain
   resuming the head into a dead env (that is exactly the loop that monopolised the slot). Wait; drain
   when it clears. This stops the outage-driven monopoly at the source.
2. **Don't let one un-resumable park starve the rest.** Either resume ALL dispatchable parks per tick, or
   age-out / rotate a park that has re-parked N times (the same backstop `inference_backpressure` already
   has, extended to `no_worker_slot`). Fairness so a permanently-stuck head can't block a resumable tail.

Labeling an all-quarantined dispatch failure `waiting_on_environment` (vs the `no_worker_slot` mislabel)
is still worth doing — it makes the card + audit honest and lets #1 target exactly the env-caused parks —
but it is **not** the fix by itself. This slice touches the live capacity drain, so it needs its own
tests + a gym; kept separate from F31.1 deliberately.

**Why it fixes it:** #1 removes the monopoly (the head stops re-parking during an outage), #2 removes the
starvation (a stuck head can't block others), and together they resume gym-031/032 the moment the
environment is back — which F31.1's signal detects deterministically.

### F31.3 — abort cancels in-flight worker turns *(SHIPPED+DEPLOYED+DOGFOODED 2026-07-30, 725 tests)*
`abort` archived the effort but left its running turns executing — gym-030's workers stayed
`computing` an archived effort for ~1h until a manual container restart. **Root cause (code-confirmed):
every path that frees a worker SLOT was bookkeeping-only** — abort (`_archive_efforts`→set_lifecycle),
freeze (`enforce_freeze`→SUSPENDED), and boot (`reset_stale`→IDLE) all flip the DB `sched_state` but never
tell the DAEMON to stop. The daemon-cancel (`harness.cancel_task`→POST /tasks/{id}/cancel) existed but was
wired only into the stall-watchdog for HUNG turns. **Fix:** shared `_cancel_worker_turns(effort_id, reason)`
— probes each worker's daemon for its running task (`running_task_progress`, restart-safe ground truth) and
cancels it — wired into abort (effort-scoped), `_freeze` (effort-scoped; closes the latent freeze gap), and
`setup()` boot (`effort_id=None` → cancels ALL running turns, all orphaned post-restart, before `reset_stale`).
Best-effort; audits `worker_turn_cancelled`.

**Why it fixes it:** the daemon is stopped, not just the bookkeeping — so an aborted/archived effort can't
hold capacity hostage, and a bridge recreate no longer leaves workers wedged `computing`. **Dogfooded on its
own deploy: the recreate logged "cancelled 2 orphaned worker turn(s) on startup" and both daemons went
`task=done` — the session-long "recreate re-orphans both workers, needs a manual restart" pain, fixed.**
Follow-up (minor): `reset_stale` clears computing/waiting but not `suspended`, so a freed worker can keep a
stale `effort_id` (harmless — still acquirable).

## Deliberately out of scope (flagged, not fixed here)
- **The llm-queue connection-leak (the ROOT of the inference-shed).** It lives in the *llm-queue*
  service, not agent-org — a separate targeted fix (its counter leaks under the gym's fan-out load and
  wedges at the 128 cap; documented remedy is `docker restart llm-queue`). P31 makes the org *resilient*
  to it (park-until-healed) but does not fix the leak. Worth a dedicated look if it keeps recurring.

## Plan
1. **F31.1** (silent-during-env-outage HOLDS, deterministic all-quarantined gate) — **BUILT + tested**
   (`test_p31_env_wait.py`, 6 tests: the signal, hold-not-escalate, healthy-still-escalates, auto-resume,
   throttle). Deploy → gym: verify a shed/unreachable episode holds-and-auto-resumes, no false
   `stall_escalated`. This is the safest, highest-confidence slice and touches only the escalation
   decision.
2. **F31.2** (stuck-park: `waiting_on_environment` label + resume gated on `environment_down()` clearing;
   trace the drain-phase resume token first) — test → deploy → gym.
3. **F31.3** (abort cancels in-flight turns) — test → deploy → gym.
4. Add a short **§8.5** to ORCHESTRATION-DESIGN ("environment failure ≠ worker failure") so the principle
   is ground truth, then re-attempt the **Mode B acid test** on the hardened, stable environment.

## Status
- **F31.1 SHIPPED + DEPLOYED** (2026-07-28): `scheduler.environment_down()` (deterministic all-quarantined
  signal), `_env_wait_hold` in both watchdog arms, `env_wait` excluded from `_last_event` (auto-resume).
  6 tests. Rebuilt+recreated agent-bridge; live `environment_down()`=False on 2 healthy workers.
- **F31.2 SHIPPED + DEPLOYED** (2026-07-28): root cause was HEAD-OF-LINE BLOCKING (a re-park preserves
  `parked_at`, so a stuck FIFO head monopolised the one-per-tick drain and starved newer parks —
  gym-030 starved gym-031/032). Fix: `_drain_parked_once` now (1) gates the whole drain on
  `environment_down()` (don't resume into a dead env) and (2) resumes EVERY dispatchable park per tick
  (with an `_delegating` double-dispatch guard) so a stuck head can't block the tail. 2 new tests
  (fairness + env-down hold); full suite **701 green** (4 batches). Rebuilt+recreated; deployed code
  verified. Validating in **gym-034** (launched 2026-07-28, healthy env — llm-queue up 46h).
- **Zombie-park fix (surfaced live by F31.2 in gym-034, 2026-07-28).** F31.2's resume-all exposed a
  pre-existing bug: an ABORTED effort keeps `state=active`, and `can_dispatch` reads the governance
  state (active/frozen) NOT lifecycle — so an aborted effort with a lingering park row read as
  dispatchable and the drain resumed it forever (gym-030 churned the drain for a day, its delegate
  making model calls that fired the capacity signal → re-drain → a hot loop burning inference on a dead
  run and starving the live one). Fix: `_effort_terminal()` + the drain UNPARKS a terminal (aborted/
  done) effort instead of resuming it. New test `test_drain_unparks_a_terminal_zombie_...`; full suite
  **702 green**. Deployed; the stale gym-030 park was cleared and old aborted efforts tidied. Re-ran as
  **gym-035**, which is converging cleanly (published a branch, draining, no churn/park/escalation).
- F31.3 (abort cancels in-flight turns) / §8.5: not started. NOTE — abort already unparks
  (orchestrator.py ~5111); gym-030's zombie predates that, and the drain cleanup is the durable backstop.

## gym-035 validation (2026-07-28) — F31.1 / F31.2 / zombie-fix PROVEN under load
A full, long convergence run (446 audit events, 18 task dispatches, 2+ drain rounds) exercised everything:
- **Zero `env_wait`, zero `effort_parked`, zero `stall_escalated`, zero park churn** the whole run — the
  fixes hold under real load and did NOT break normal dispatch/park/resume/recovery.
- Delivered a polished product (52+ tests green, no test weakened); **Mode B fired** (the acid test that
  never happened in gym-030→033) and found real bugs the worker's own tests missed (REPL `str.replace`
  corruption, flag-parser index bug, dup-ID data bug), each fixed RED→GREEN via repro.
- P21/P24 all clean: an abandon auto-recovered; the flail-guard forked a fresh planned session; NO-CHANGES
  claims org-verified; empty-delivery gate re-verified. Propagation descended 11 → 3 (Mode-A converging).

**New reliability findings the run surfaced (next work):**
1. **F31.4 — flail-guard for read-only LENS turns *(SHIPPED + DEPLOYED)*.** A round-3 lens wedged repeating
   one identical command, evading the flail-guard (keys on read-*without-edit*; a lens is read-only by
   design), the offset-silence watchdog (offset advances each repeat), and lens truncation (repeats don't
   grow the findings file). Fix: a BRIDGE-SIDE `max_repeat` guard in the wake poll loop
   (`harness.wake`/`router.wake`) that stops a turn after N consecutive identical commands and salvages the
   findings streamed before the flail; the lens sweep arms it via `lens_flail_repeats` (default 6); audits
   `lens_flail_stopped`. 4 tests; full suite **706 green**. Deployed 2026-07-28.
5. **F31.5 — the drain has NO runaway bound (task #18).** Left unfixed, the wedged lens turned gym-035 into
   a **~13-hour, 2109-event runaway**: the gym runner's 240-min wall budget only stops its *watching*; the
   org kept draining independently (abandon×4 / recover×2 / escalate×3, re-sticking). F31.4 fixes the
   specific cause, but any persistent non-convergence can still loop unbounded — the drain needs a per-effort
   round/event/wall-clock bound that escalates-and-stops. Design-adjacent (§6.6/§8) — for operator input.
2. **Off-theme drift not caught:** 3 dev-tooling tasks (packaging/linting/`__version__`) propagated then all
   escalated as off-scope — they map to no product scope node; P28's filter only catches git-meta.
3. **A git-meta task leaked** into the drain (commit-subject rewrite) instead of becoming a constraint.
4. **Symptom-vs-root patching:** the REPL bugs share one root (hand-rolled parsing vs reusing argparse);
   the drain patches each symptom rather than the root.
