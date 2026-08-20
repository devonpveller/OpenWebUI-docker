# P22 — the goal_alignment lens is the convergence single-point-of-failure (evidence: gym-020, 2026-07-22)

gym-020 was the first run with the count (P19), one-task dispatch (P20) and reliability (P21) all in
place. P21 worked — F2a classed the goal `routine` (no plan-approval idle), and nothing abandoned or
stalled. But it still did NOT converge, and the audit (docs/log/P21-...md flow) shows a single root
cause, downstream of everything P21 fixed.

## Evidence (gym-020 audit)

```
10:42:04  lens_report_truncated  round=1 lens=goal_alignment body="Now let me write the full evaluation r…"
          lens_findings_salvaged = 0          <- salvage recovered NOTHING
10:46:46  drain_round  round=1 new_tasks=0 open=6 swept=False   <- partial sweep
10:46:48  delivery_pr_opened → develop_integration → lifecycle=done
          6 open tasks, ALL round=0 (GAPs)     <- delivered + done, never goal-compared
```

The `goal_alignment` lens (the ONLY lens that compares the product to the goal — P10.2) spent its
turn probing, said *"Now let me write the full evaluation report:"*, and ended `status=done` with no
report. F4-salvage (P18) read `/tmp/lens-findings.txt` and found it EMPTY — the lens never echoed a
`FINDING:` line, so nothing was banked. Result: `swept=False`, the goal comparison never happened,
the count is uninterpretable, and the effort delivered a PR + marked `done` after ONE partial round.

## Gaps (from the audit)

- **GAP 1 (root) — the lens truncates and salvage can't recover it.** This is the DEFERRED **F4-redux**
  (P19): *"the file approach depends on model cooperation; only an incremental writer survives an
  early truncation. Capture findings harness-side from `WorkResult.commands` instead."* The lens
  didn't cooperate (it narrated instead of echoing), so both the file AND the stream were empty.
- **GAP 2 — a `swept=False` round delivers-and-finishes** instead of retrying the sweep. One truncated
  lens report ended the drain; the 6 GAP tasks were never worked and the effort marked `done` on a
  sweep that never goal-compared. The F3 guard was honest ("not a clean sweep") but did not PREVENT
  the done.
- **GAP 3 — `goal_alignment` is a single point of failure for convergence.** It's the only goal-aware
  lens, and (because it probes the most) the one that truncates the most. The whole termination
  contract hangs on the least-reliable lens.
- **GAP 4 (latent) — the F4 watchdog set still omits delivery/verify terminals** (`develop_integration`,
  `delivery_pr_opened`). Not triggered here (the effort was `done`, correctly excluded), but an OPEN
  effort whose delivery coroutine died at one of those would be missed (same class as the `check_exec`
  gap P21 just fixed).

## Fixes

### F22.1 — capture findings from the ACTIONS, not a file the model must write (GAP 1, the F4-redux)
Two parts, both grounded in the design's "the environment remembers" (§5) and the research's *"verify
self-report against actions"*:
1. **Harness-side capture.** The daemon already streams every command the lens runs (`WorkResult.commands`,
   built for F18). Parse `FINDING:` lines out of that stream in the salvage path, in ADDITION to the
   file. A lens that echoed findings as it went is then recovered even if the file write raced, the
   per-container file was on the wrong worker (the F4-redux/F6 race), or the final report truncated.
2. **Make the echoes the PRIMARY output.** Rewrite the org's finding-capture instruction (the wrapper
   around the verbatim lens prompt — the prompt itself stays untouched) so echoing each finding the
   instant it is observed is the REQUIRED output and the summary is explicitly optional: *"The FINDING
   lines you echo ARE your report; a prose summary is optional and may not fit."* This inverts the
   small model's failure mode (probe → narrate → truncate before the report).
- **Test:** a lens whose streamed commands contain `FINDING:` echoes but whose final answer is empty
  yields those findings as a salvaged report that satisfies `swept` — with no findings file at all.

### F22.2 — a partial sweep RETRIES the goal lens; it does not deliver-and-quit (GAP 2/3)
On a round where `goal_alignment` produced no usable report even after salvage, RE-RUN the
`goal_alignment` lens (bounded — 1 retry) before the round proceeds. A retry is cheap (one worker
turn) and often succeeds (truncation is partly stochastic). If it is STILL missing after the retry,
the round stays `swept=False` and honestly incomplete — the effort may deliver a PR for review but
must NOT mark the scope converged/`done` on a sweep that never goal-compared. Aligned with §10.4 (the
count is meaningless without a complete sweep) and the research (loops are affordable *"provided they
converge"* — the retry is bounded).
- **Test:** a first `goal_alignment` turn that truncates + a second that reports produces a swept
  round; two truncations leave the round un-swept (and it is NOT marked converged).

### F22.3 — add the delivery/verify terminals to the F4 watchdog (GAP 4, latent)
Add `develop_integration` / `delivery_pr_opened` to `_STALL_MIDDISPATCH_KINDS` guarded so a DONE
effort (correctly awaiting your merge) is still excluded — these are bridge-internal delivery steps,
not human gates, so an OPEN effort silent at one of them is a wedge. Small, closes the latent gap the
P21 `check_exec` fix left adjacent.

## Alignment (checked before the plan)

- **Design.** §5 *"the model proposes, the environment remembers"* — F22.1 records findings in the
  environment (the command stream), not the model's fragile synthesis. §8 *"the signal must be
  agent-loop activity"* — the streamed commands ARE that signal. §10.4 / P17 F3 — a sweep must be
  COMPLETE for the propagation count to mean anything; F22.2 makes the loop reach a complete sweep
  instead of shipping on a partial one.
- **Research.** ANALYSIS-frontier-vs-small: small models have *"context rot … structured output is
  fragile"* — so capture from actions + a bounded retry, don't trust the one big synthesis. *"verify
  self-report against actions"* is exactly F22.1. Loops converge because they are *bounded* (F22.2).
- No new gate, no human removed from an irreversible decision (these are reliability fixes on the
  observe half of the loop).

## Plan (iterate one change at a time)
1. **F22.1** (harness-side capture + primary-echo instruction) — the root convergence fix. **SHIP
   FIRST** and validate in gym-021: it directly recovers the goal lens's findings from its actions.
2. **F22.2** (partial-sweep retry) — resilience; a partial sweep never ships as converged. **Only if
   gym-021 still shows a partial sweep** (i.e. the model narrates with zero echoes even under the
   primary-echo instruction, so F22.1 has nothing in the stream to recover). Requires extracting the
   lens-run body into a helper — deferred until proven necessary, to keep the change surface small.
3. **F22.3** (watchdog terminals) — close the latent gap. Deferred (latent; gym-020's effort was
   `done`, correctly excluded, so it did not trigger).

Then per change: full suite green → deploy → wipe arena → gym (same convergence scenario). Success
for F22.1: the goal_alignment lens's findings survive a truncation (salvaged from the STREAM, an
empty file no longer defeats it — `lens_findings_salvaged > 0`), the round reaches `swept=True`, and
the loop can descend to an evidenced zero with `scope_completed`.

**Shipped in this pass: F22.1.** F22.2/F22.3 held pending gym-021's evidence.
