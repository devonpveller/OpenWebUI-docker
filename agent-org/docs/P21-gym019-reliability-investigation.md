# P21 — gym-019 reliability investigation (evidence-first)

Operator directive (2026-07-21): investigate the gym-019 failures with EVIDENCE, not assumptions
(after I wrongly headlined an EMPTY plan reply as a "small-model capability wall" with no support).
Audit every point worth investigating, document the findings, then run a SECOND audit for the CAUSE
of each, theorize an evidence-backed solution per issue, and only then a plan.

This document is that record. Part 1 = findings (symptom + evidence). Part 2 = cause audit
(mechanism + evidence). Part 3 = solution theories (evidence-backed). Part 4 = the plan.

**Method note.** Every claim below cites a live audit event (`http://127.0.0.1:8830/audit`), a
`file:line` in `agent-org/agent-bridge/`, or a container log. Where I do not have evidence I say so.

---

## Run context (gym-019, effort-gym-019-todo-product)

Byte-identical goal to gym-017/018 ("turn the python todo CLI into a polished final product"),
running the P20 one-task-at-a-time build. Span 11:05 → parked at 21:48 UTC (~10.7h wall), and it
**never reached an evidenced zero** — it is stopped at the plan gate as of writing (4h+ idle,
workers suspended, no resource use).

Where the wall-clock went (audit `ts` gaps): **~4.9h idle waiting for a human plan-approval tap**
(11:28→16:22), a 60-min plan turn that abandoned, a **~2h silent stall** after that abandon, a
47-min turn, a 22-min survey. **~90% of the span was idle.**

---

## Part 1 — FINDINGS

### F1 — a re-run resumes a ROTTED session → the "EMPTY plan" (severity: HIGH — caused the terminal stop)

**Symptom.** At 21:45 and 21:48 the worker plan-gate turn returned an empty reply twice; the org
stopped at the plan gate (`worker_plan_stopped`) and escalated. I had called this a "capability
wall" — **that was wrong.** The same worker produced APPROVED plans six times earlier in the SAME
run (`worker_plan_approved` at 16:23, 16:53, 16:57, 17:03, 17:58, 18:09). The model can plan this
work; it did, repeatedly.

**Evidence it is a rotted-session symptom, not capability:**
- `_worker_plan_gate` treats an empty plan as exactly this — `orchestrator.py:11997-12016`: *"An
  EMPTY plan reply is a rotted/overflowing SESSION symptom, not a worker decision (live 2026-07-14:
  a 593KB base session made the model return EMPTY on every plan turn)."*
- A gym-019 plan turn carried ~53k tokens of context (`llama-cpp-upstream` slot release
  `n_tokens = 53197`) — not a fresh few-thousand-token plan.
- The model/GPU is healthy — a llama-cpp request seconds later generated 128 tokens fine.

### F2 — the risk class is an uninstructed LLM coin-flip → a plan-approval HOLD → ~4.9h idle (severity: HIGH — the single biggest time sink)

**Symptom.** gym-018 and gym-019 were classified `cascading_refactor` (→ `dry_run_status:required`
→ plan-approval HOLD), gym-017 `routine` (dispatched in 1 second, no hold). Byte-identical goal.
gym-019's hold cost **294 minutes** (`plan_drafted 11:28:36` → `plan_approved 16:22:14`).

**Evidence (subagent-verified, live audit + code):**
- `blast_radius` is an **uninstructed enum field** in the readiness-gate schema —
  `schemas.py:90-94`: `blast_radius: Literal["routine","cross_effort","cascading_refactor"] =
  "routine"`, **no `Field(description=...)`**.
- The readiness prompt `_READINESS_SYS` (`planner.py:31-57`) **never mentions blast_radius, risk,
  or dry-run** — it is entirely about clarifying questions. The model fills the field with zero
  criteria, at **temperature 0.3** (`profiles/planner.json`), so identical goals decode to
  different enum members. No caching per `(goal, project)`.
- Both gym-017 and gym-019 readiness verdicts were `clear_and_safe:true` with zero questions — the
  ONLY divergence was the unguided `blast_radius`.
- Downstream: `_risk_from_blast` (`orchestrator.py:5100-5103`) → `set_risk` (`execution_gate.py:43-61`,
  `RISKY={irreversible,cross_effort,cascading_refactor}` → `dry_run_status=required`) → default
  `plan_approval:"risky"` (`config.py:308`) → `_plan_required` (`orchestrator.py:5891-5899`) HOLDs
  until a human types `approve <effort>`.
- Already logged as a known defect: `docs/P8-org-self-knowledge.md:204-207` ("Risk classification
  is a coin flip … make it deterministic/explainable per (goal, project)"); `docs/P9-...:880-882`.

### F3 — a 60-min per-turn wall-clock deadline abandons a hard single task (severity: MEDIUM)

**Symptom.** A plan-gate turn ran 18:13:17→19:13:22 (**3605s ≈ 60 min**) and ended
`wake_done {"status":"abandoned"}`.

**Cause — PROVEN (agent-verified, daemon code).** It is a **hard per-turn wall-clock deadline**, not
a flail kill or a silence timer. The bridge wakes the worker on the `batch` trigger channel
(`worker/harness.py:70`), and the little-coder daemon's per-channel deadline for `batch` is **3600s**
(`little-coder/src/littlecoder/config.py:121-127`). At 3605s the agent subprocess is killed with
`TaskTimeout` (`agent.py:437-443`, `daemon.py:295-303`) → status `abandoned` (passed through verbatim,
`harness.py:192-203`). It was NOT the flail guard — the turn had ≥1 edit, which exempts it
(`agent.py:170-171`), and no `flail_replanned` event exists in gym-019's audit. It was NOT the
harness poll ceiling (`worker_poll_timeout_s=5400s`, `config.py:263`) — the daemon's 3600s won first.

### F4 — the silent-stall watchdog skips an effort whose last event is `check_exec` → the ~2h stall (severity: HIGH — the durable gap)

**Symptom.** After the 19:13 abandon, effort-gym-019 produced **NO audit events for ~2h**
(19:13:22 → 21:26:30), cleared only by a human `re-run it`. Both workers sat `suspended`. gym-019's
audit contains **no** `stall_recovered` / `worker_silent` / `flail_replanned` / `infra_auto_recovered`
event — no recovery path fired.

**Cause — PROVEN (agent-verified, code).** Three background loops exist; all three miss it:
1. **Capacity-drain loop** (`orchestrator.py:1186-1201` → `_drain_parked_once` `:2502-2531`) only
   iterates PARKED efforts (`self.parks.all()`); gym-019 was *suspended after an abandon*, never
   parked → invisible.
2. **Branch reaper** (`:2348+`) never re-engages efforts.
3. **Stall watchdog** (`_stall_watchdog_loop` `:2117-2130`, 240s sweep):
   - *Arm 1* (hung RUNNING worker, keyed on `worker_silence_s=300s`): both workers were `suspended`,
     so `running_task_progress` returns None (`:2160-2162`) → nothing to cancel.
   - *Arm 2* (idle-effort sweep `:2214-2287`): reaches the decisive gate `:2249` — `if kind not in
     self._STALL_MIDDISPATCH_KINDS: continue`. gym-019's last event was **`check_exec`**, which is
     **NOT** in `_STALL_MIDDISPATCH_KINDS` (`:2209-2231`) → the watchdog classified it as "a
     resolution / surfaced state correctly awaiting the operator" and `continue`d past it every 240s
     for the full 2h.

**The precise insight — a prior fix defeated by one trailing event.** The gym-008 fix
(`:2222-2230`) deliberately added `wake_done` to the allow-list so an abandoned turn (which leaves
`wake_done` last) would be swept — the comment says it "would have stranded FOREVER, silently"
otherwise. In gym-019 that fix was bypassed: the abandon (`wake_done`, id 6214, covered) was
immediately followed by a **verifier `worker_acquire` + `check_exec`** (ids 6216-6217) whose
verify/publish coroutine then went silent — moving the terminal event **past** the covered
`wake_done` to the uncovered `check_exec`, re-opening the exact gym-008 gap.

### F5 — the human is a single point of latency for every gate (severity: HIGH — compounds F2/F4)

**Symptom.** Two of the three big idle blocks were "waiting on a human tap": the 4.9h plan-approval
(F2) and the 2h silent stall's re-run (F4). The bridge session is not a continuous watcher — it
acts on operator messages, and background watchers die at session boundaries. **The Mattermost
follow (fw-67a2ab) now closes the gate-latency half** (it woke me on the 21:48 plan-gate escalation
within minutes) but structurally cannot see a silent stall (F4).

### F6 — F13-redux is CORRECT; it exposed real test removal (severity: LOW — a validation, plus a worker-quality signal)

**Symptom.** `test_count_regressed` fired twice (44→40 at 17:08, 41→40 at 18:12).

**Evidence it is a TRUE positive (contrast gym-017's phantom):** all `delivery_test_count`
measurements were on the **same `agent/effort-gym-019` branch** with a deterministic AST count, and
the count genuinely fell 44→40 across two real publishes. So F13-redux works — and it surfaced a
real issue: **a one-task turn removed 4 tests despite the standing intent** forbidding it
(flag-not-block, correct). Lower priority than F1–F5, but the delete-to-weaken tendency survives
one-task dispatch.

### F7 — even one-task turns run very long (severity: MEDIUM)

**Symptom.** Single turns took **47 min** (17:08) and **60 min** (the 18:13 plan turn that
abandoned). P20 removed the multi-task *hang*, but a lone turn — especially a plan turn on a
rotted session (F1) — can still burn an hour. Compounds F3 (long turns are the ones that abandon).

---

## Part 2 — CAUSE AUDIT (how each is caused)

### F1 cause — an `abandoned` turn does NOT rotate the session generation → the re-run reuses it. **PROVEN.**

`_session_for` (`orchestrator.py:5330-5371`) derives the session id from a COUNT of specific
"failed-run-END" event kinds: `{effort_undelivered, delivery_stale_head, delivery_empty_diff,
burndown_stalled, check_infra_error, org_build_unverifiable, flail_replanned, worker_plan_empty,
worker_plan_stopped, drain_round}`. Generation `n` → session `effort_id` (n=0) or `effort_id~r{n}`;
the plan gate appends `~plan` (`orchestrator.py:11967`).

**`wake_done status=abandoned` is NOT in that set.** Live proof from the gym-019 audit — the
generation-bumping events and their running `n`:

```
16:51:41  n=1  drain_round
18:08:38  n=2  worker_plan_empty
[19:13:22  wake_done status=abandoned  <-- NOT counted; n stays 2]
21:45:13  n=3  worker_plan_empty
21:48:19  n=4  worker_plan_empty
21:48:19  n=5  worker_plan_stopped
```

So at the 18:13 abandoned plan turn, `n=2` → session `~r2~plan`. At my **21:26 re-run**,
`_session_for` still returned `n=2` (the abandon didn't bump it) → session `~r2~plan` **again** →
the plan turn **resumed the bloated session the 60-min turn had abandoned** → EMPTY (21:45). The
retry then bumped to `~r3~plan` (n=3) but was also empty in ~3 min (secondary, see below).

This is the EXACT bug class `_session_for`'s own docstring warns about (`orchestrator.py:5339-5342`):
*"the atlas re-run ended in `burndown_stalled`/`check_infra_error`, which weren't counted, so the
re-dispatch reused the rotted base session and the worker no-op'd 0 commands for 18 min — the
stateless-session law applies to re-runs too."* Abandon is a new member of the same class.

**Secondary (NOT fully evidenced):** the retry's fresh `~r3~plan` session also returned empty in
~3 min. A truly fresh session going empty that fast is unexplained by session rot. Candidates
(unproven): the shared workspace was left in a bad state by the abandoned turn; or a transient
model/clone issue. Resolving this needs the worker turn transcript, which I have not pulled.

### F2 cause — see Part 1 F2 (proven): uninstructed schema field + non-zero temperature.

### The causal CASCADE — these four bugs chained into the whole failure

The gym-019 collapse was not one bug; it was **F2 → F3 → F4 → F1 in sequence**, each independently
real and independently fixable:

1. **F2** misclassified the goal `cascading_refactor` → a plan-approval HOLD → **4.9h** idle.
2. Work then proceeded one-task-at-a-time (P20 held; F6 flagged a real test drop).
3. **F3** — a plan turn hit the 60-min `batch` deadline → **abandoned**, leaving a bloated session.
4. **F4** — the abandon's trailing `check_exec` moved the effort's last event outside the watchdog's
   allow-list → the effort sat **silent for 2h**, no recovery.
5. **F1** — my manual `re-run it` reused the bloated abandoned session (abandon doesn't rotate the
   generation) → **EMPTY plan** → the plan gate stopped and escalated (caught by the follow).

Fixing F4 alone stops the 2h stall; fixing F1 makes any recovery start clean; fixing F2 removes the
4.9h and the whole gate. They compose. F3/F4 causes are PROVEN in the F3 and F4 findings above.

### F6 cause — the AST counter is correct; the CAUSE of the drop is worker behavior (a task turn
removing tests), which is a work-alignment issue, not a measurement bug. The flag is doing its job.

### F7 cause — the `batch` 3600s deadline (F3) is the ceiling; below it, a plan turn on a large
product is just slow on a 27B model. Attributing further needs the per-turn transcript.

---

## Part 3 — SOLUTION THEORIES (evidence-backed, per issue)

### F1 — make an abandoned turn rotate the session generation
Emit a distinct `worker_turn_abandoned` audit event where the bridge sees
`WorkResult.status == "abandoned"` (`harness.py:47-49` / the wake path — there is no such event kind
today, only the `wake_done` payload), and add it to `_session_for`'s counted set
(`orchestrator.py:5347-5365`). Matches the existing pattern (the set is all event KINDS) and directly
kills the PROVEN cause. **Test:** an effort whose last turn abandoned gets a higher generation
(fresh session) on its next dispatch. **Coupling:** prerequisite for F4 — a recovery that reuses a
rotted session just re-fails (exactly the 21:26 EMPTY).

### F2 — stop gating on an uninstructed coin-flip
Options, in increasing effort: (i) **give the field criteria** — a `Field(description=...)` on
`blast_radius` + one line in `_READINESS_SYS` defining routine vs cascading, and/or drop the
planner temperature toward 0 for this structured call; (ii) **make it deterministic per (goal,
project)** as P8/P9 already prescribe (cache/memoize the class); (iii) **decouple the gym/dev
plan-approval from the human** — a time-boxed autonomous-window grant so the bridge auto-approves a
routine-shaped dev plan (production merge stays human). (i)+(iii) together remove both the
misclassification and its latency. Evidence: the classification is the ONLY divergence on identical
goals (F2), so anchoring it is high-leverage.

### F4 — recover a silent drain effort regardless of its last event KIND
The allow-list (`_STALL_MIDDISPATCH_KINDS`) is fragile — gym-008 patched it by adding `wake_done`,
and one trailing `check_exec` re-opened the hole. Stop keying on the last event's KIND. Add a third
arm to `_sweep_stalled_efforts` (`orchestrator.py:2214-2287`) that re-engages ANY effort that is:
open, not frozen, not `_waiting_on` a human, not parked, has **no running worker turn** (both workers
idle on it), and has produced **no audit event for ≥ `stall_threshold_s`** (900s). A positive
"no progress + nobody's working it + not awaiting a human" test, not a negative kind-exclusion.
Bounded (cap re-engages, back off) against thrash, and clear a stale `_delegating` entry (B found the
post-abandon verify coroutine went silent, which would also skip the effort at `:2216`). **Test:** an
effort suspended after an abandon with a trailing `check_exec` and no worker running is re-engaged
after the threshold — and, with F1, into a FRESH session.

### F3 — treat a deadline-abandon as recoverable, not terminal
The 3600s `batch` deadline is defensible (a hung turn must die); the bug is that its death STRANDS
the effort (F4). Once F1+F4 land, a deadline-abandon auto-recovers in a fresh session — no deadline
change needed. Raise `batch` toward `cli/owui` 21600s only if telemetry shows legitimate turns need
>60 min (no evidence yet). Defer.

### F5 — no new work: the built follow closes gate-latency, F4 closes silent-stall latency, F2(ii)
removes the dev gate. Together they remove the human as a hard dependency for forward motion.

### F6 — no change to F13 (it is correct); the real lever is the anti-delete-to-pass gate already in
the pipeline — worth checking why a test removal reached a publish. Low priority.

---

## Part 3.5 — ALIGNMENT with ORCHESTRATION-DESIGN.md + the research (checked before the plan)

Every fix was tested against the ground-truth design and the teams-chat-agent-orchestration research
before committing to the plan. Verdict: **all four are aligned, and three are arguably *mandated* by
the design** — none adds a gate, none removes a human from an irreversible decision.

- **F2a (deterministic risk class) — MANDATED by §11.** *"Every boundary in the system — module
  interface, escalation, human finding — is an executable contract. That single principle is the
  spine of the design"* (§11:350-351). A risk class that is an unguided LLM sample at temp 0.3 is the
  exact opposite — the "ambiguous handoff wearing a SOLID costume" §3 rejects (§3:66-69, the paper's
  *"sub-tasks that do not strictly specify clear constraints → verification failures"*). Making it
  deterministic turns a coin-flip into a contract. Also §2.1: *"Gates produce honesty, not quality …
  the program is about model and target, not more gates"* — removing a spurious gate is aligned.

- **F4 (silent-effort recovery) — FINISHES §8.** §8 is titled *"Liveness — silence detection, not a
  timer"* and states the mechanism verbatim: *"has the worker emitted any agent-loop event in the
  last T?"* (§8:281-294). The current implementation only covers a *running* worker; F4 extends the
  SAME principle to an effort whose worker has exited (the case §8's own framing implies but the code
  misses). Convergence caveat respected: §3 sanctions expensive loops *"provided they converge"*
  (§3:76-79) — the fix is bounded (cap re-engages, back off).

- **F2b (auto-approve DEV plans) — the design's exact human boundary.** §1: the human is *"governor —
  setting direction, judging quality, and **holding the irreversible gates (merges to `main`)**"*
  (§1:24-26). A dev/gym plan is reversible and PR-gated; it is not one of the design's named
  human-governor artifacts (the merge gate §1/§10, the vulnerability list §9.4). Auto-approving it
  while the merge-to-main gate stays human is the design's boundary, not a weakening of it. (Also the
  standing [[autonomous-window-approval-authority]] precedent.)

- **F1 (rotate session on abandon) — §5 hygiene.** §5: *"the intelligence lives in the loop, not the
  model … the model proposes, the environment remembers."* A rotted session corrupts what the
  environment remembers (→ EMPTY); §2.2 warns an incoherent target *"oscillates forever and converges
  on nothing."* Keeping the session clean is loop hygiene, not a philosophy change.

**Two design-care refinements folded into the plan (they strengthen alignment):**
1. **F4 must ESCALATE after bounded re-engages, never loop silently.** §11's spine and the paper's
   dropped-signal danger (§3) require that a persistent problem reach the human as a real escalation —
   auto-recovery is for the INFRA-silence symptom only (matching the [[agent-org-infra-freeze-autorecovery]]
   precedent: "real code deviations still stop for the human"). So: N bounded re-engages, then a human
   escalation — recovery must not be able to bury a stuck effort.
2. **F2b must enforce the dev↔irreversible boundary cleanly** — auto-approve may never reach an
   action §1 reserves for the human (merge to main). Scope it to routine/dev plans by construction.

**Research corroboration (teams-chat-agent-orchestration folder) — CONFIRMS alignment, and sharpens
the two sensitive fixes into hard safety conditions.** (Note: these reliability labels F1/F4/F2a/F2b
are unrelated to the *paper's* failure modes; the paper's "silently dropped objection" is written
paper-F3 below.)

- **Reliability belongs in the bridge — direct support.** `ANALYSIS-frontier-vs-small-model-approach
  §3`: *"Move coordination out of the models and into the deterministic bridge … The weaker the
  model, the more the floor carries"* (`SAFETY-...governance-model §4.2/§9`). F1 (session rotation)
  and F4 (bridge-level recovery) are exactly this. F1 targets the documented failure — `§1 axis 3
  "context rot … small models lose the thread on long context."*
- **Governance decisions must be deterministic/contract-shaped — direct support for F2a.**
  `governance §4.2 "Prompt for steering; hook for enforcement"`; `COMMS-MODEL "No agent message is
  routed by vibe"`; `DELIVERY-PIPELINE "run a command, report its output must never be an LLM turn …
  verdicts come from exit codes."* The risk class sits IN the governance path (it gates dry-run +
  review depth), so making it deterministic is the design's own rule applied to the classifier.
- **F2b sits on the design's exact human boundary — with a firewall.** `governance §8 #5`: the
  irreversible line is additive-vs-destructive — *"feature-branch push is routine; merge-to-main +
  deploy stay the human gate."* The plan-proceed checkpoint F2b auto-clears is NOT a §3 hard-gate.
  A time-boxed autonomous-window grant is itself a Human-Operator act (hard-rule #2) and matches the
  operator's own recorded grant.

**The two hard conditions the research makes non-negotiable (paper-F3 / fail-safe):**
- **F4 and F2b must never auto-clear a §3 hard-gate.** `governance §2(F3)` + hard-rule #1: *"A
  refusal, objection, or hit boundary is a mandatory escalation event that BLOCKS — never a signal
  that can be dropped or routed around,"* and `§3.0`: *"there is no timeout that auto-resumes a
  hard-gate frozen."* So:
  - **F4** is legitimate *scheduler* recovery (`§3.0(B)`: `waiting` is woken by a `finish` event or a
    timeout; *"only `frozen` is a brake; `waiting` is just idle"*) — SUPPORTED **iff** it reads the
    AUTHORITATIVE gate state, hard-excludes `frozen` + human-waiting, is bounded, **fails safe (leaves
    paused) on any ambiguity**, and — defense-in-depth against paper-F3 — **refuses to re-engage an
    effort whose last event is a refusal/objection/boundary regardless of gate state**.
  - **F2b** must be firewalled to the plan-proceed gate only; it may NEVER auto-clear a refusal /
    ethics CONCERN / irreversible-external / cross-effort trigger raised *during* the window (the
    brake channel is *"sacred — exempt from any flow minimization,"* `§4.4/§5`), and the grant is
    versioned/audited + time-boxed so it cannot become a silent permanent relaxation (`§6`).
- **F1 re-grounding + F2a fail-toward-more-gating.** F1's fresh session must re-inject the floor +
  steering + goal-with-constraints-inline (`§4.2/§4.3`) or it restarts ungrounded. F2a's temp-0 only
  removes variance, not misclassification (`ANALYSIS-frontier §5`: weak-model judgment must be
  *"paired with deterministic checks"*), so bind the class to OBJECTIVE inputs (files-touched /
  touches-main / blast-radius) and **default to the MORE-gated branch when criteria are unmet**
  (fail-safe, not fail-open).

**These conditions are folded into Part 4 below.** They do not change the verdict (aligned) — they
make the two edge-riding fixes provably safe.

---

## Part 4 — IMPLEMENTATION PLAN

Ordered by (durable value × evidence strength) and by the coupling above. **F1 and F4 ship
together** — F4's recovery is worthless if it recovers into a rotted session (F1).

**P21.1 — F1 + F4: abandon-clean silent-stall recovery (the keystone). [PROVEN causes]**
- **F1:** emit `worker_turn_abandoned` on `WorkResult.status=="abandoned"`; add it to `_session_for`'s
  counted set. The re-engaged fresh session **re-injects floor + steering + goal-with-constraints-
  inline** (governance §4.2/§4.3 — never restart ungrounded). Tests: post-abandon dispatch rotates the
  generation; the fresh session carries the floor.
- **F4:** add a third arm to `_sweep_stalled_efforts` that re-engages an effort with no audit event
  for ≥ `stall_threshold_s` AND no worker running — SAFETY-CONDITIONED per the research/§3 fail-safe:
  (a) read the AUTHORITATIVE governance-gate state (machine A) — hard-exclude `frozen` and
  human-`_waiting_on`; (b) **refuse to re-engage if the last event is a refusal/objection/boundary**,
  regardless of gate state (defense-in-depth vs a dropped-signal — paper-F3); (c) **fail safe**:
  leave paused on ANY ambiguity rather than wake; (d) **bounded — after N re-engages, ESCALATE to the
  human**, never loop silently; clear a stale `_delegating`. Tests: a suspended-post-abandon effort
  (trailing `check_exec`, no worker) re-engages into a FRESH session; a `frozen`/refusal effort is
  NEVER re-engaged; after N tries it escalates.
- This one change would have prevented the 2h stall, the EMPTY that followed it, and gym-017's manual
  re-runs. Highest durable value.

**P21.2 — F2: deterministic, criteria'd risk class + firewalled dev auto-approve. [PROVEN cause]**
- **F2a:** anchor `blast_radius` — `Field(description=...)` + one line in `_READINESS_SYS` + planner
  temperature → 0. Bind the class to OBJECTIVE inputs where possible (files-touched / touches-main /
  blast radius), not pure model opinion, and **default to the MORE-gated branch when criteria are
  unmet** (fail-safe, not fail-open — governance §3/F5). Test: identical goal → same class across N
  calls; ambiguous input → the higher-risk branch.
- **F2b:** time-boxed autonomous-window auto-approve for the routine/dev **plan-proceed checkpoint
  ONLY**. FIREWALL (non-negotiable, paper-F3 / §3): it may NEVER auto-clear a §3 hard-gate raised in
  the window — refusal, ethics CONCERN, irreversible-external, cross-effort — those stay human-only
  even inside the window; production merge-to-main stays human. The grant is versioned/audited +
  time-boxed (§6, cannot become a silent permanent relaxation). Test: a hard-gate raised during an
  open window still blocks and escalates.
- Removes the single biggest time sink (4.9h) and the coin-flip gate — without touching the brake
  channel.

**P21.3 — deferred / lower priority.**
- F3: no deadline change (covered by P21.1) unless telemetry justifies it.
- F6/F7: investigate the delete-to-weaken-a-test path and per-turn slowness separately.

**Gate for the next gym (gym-020):** deploy P21.1 + P21.2, wipe the arena, run the same convergence
scenario, and confirm — no plan-approval idle (F2), no 2h silent stall (an abandon auto-recovers in a
fresh session within `stall_threshold_s`, F1+F4), and the loop reaches an evidenced zero with no
human nudge. Each fix has a live-event tell: `worker_turn_abandoned` → auto re-engage → non-empty
plan; a single deterministic `effort_risk_set`.

---

## Open, not-yet-evidenced (honest gaps)
- The **secondary EMPTY** (the fresh `~r3~plan` retry, empty in ~3 min) is unexplained — needs the
  worker turn transcript. Hypotheses (unproven): the shared workspace was left bad by the abandoned
  turn, or a transient clone/model issue.
- **F6/F7** attribution (which turn removed the tests; why a plan turn is slow) needs per-turn
  transcripts, not pulled here.
