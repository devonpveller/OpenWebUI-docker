# P19 — convergence proven, and the four fixes that need a second pass

Evidence base: **gym-017** (`effort-gym-017-todo-product`), 2026-07-20, the first run with all of
P18 deployed. Goal byte-identical to gym-008..gym-016 except the effort name.

**This is the first round in the whole series to demonstrate convergence under the full
drain+observation stack.** It also produced four evidence-backed defects in the P18 fixes
themselves. Both halves matter equally.

---

## Part 1 — the headline: the loop converges IN CLEAN ROUNDS, and RE-ASCENDS on any new finding

```
                 new tasks   open   severity of findings                       swept
round 1:            22        25    CRITICAL (unhandled crash, negative/dup IDs)   true
round 2:             9        14    MINOR polish                                  true
round 3:            24        24    ONE new critical (cmd_undo crashes) + trivia  true
                                     — FANNED into 24 tasks by F19
```

**Rounds 1→2 descend. Round 3 RE-ASCENDS 9 → 24 — and this is the most important result of the
run.** Watched to completion (per operator), the tail reversed the interim "convergence proven"
read.

What happened in round 3: the `cmd_undo` feature ADDED in round 2 crashes on a corrupt backup
(`json.load` with no guard → unhandled `JSONDecodeError`). All three lenses found it; two called
it *"the one verified bug"*. So round 3 had **~1 real critical finding + a handful of minor ones,
~8 distinct total** — and F19's fan-out multiplied them into **24 tasks**. The undo crash alone
became FIVE near-identical tasks:

```
Handle unhandled JSONDecodeError in the undo command...
Fix `cmd_undo` to handle corrupt backup files without crashing
Handle JSONDecodeError in cmd_undo for corrupt, empty, or truncated...
Handle JSONDecodeError in the undo command when reading corrupt...
Fix the undo command to handle JSON decode errors from corrupt...
```

**The correction to the interim claim:** the loop is NOT monotonically convergent under F19. It
converges only in rounds that surface nothing new; the moment a round finds a real defect (here,
in the loop's OWN round-2 addition — which is F8 diff-awareness working correctly), F19 amplifies
it by the scope count (4×) and the open queue re-ascends. A loop that re-ascends on every
productive round may never reach an evidenced zero — the very termination condition the drain
loop is built on. **F19 is therefore not a "bounded one-round tax" (the interim read) but the
primary blocker to convergence**, and F19-redux is elevated accordingly (Part 3).

The *good* half still stands: rounds 1→2 show the machinery CAN converge (22→9, critical→minor),
the product genuinely hardened, and F8 correctly caught a regression in freshly-added code. The
loop's logic is sound; F19's fan-out is what prevents it from settling.

Contrast the two prior rounds:
- **gym-015**: ascended 0/3 → 1/4 → 1/5 → 2/3 → 0/6, inventing packaging work, never converging.
- **gym-016**: round-1 count uninterpretable because the sweep was incomplete (F3 not yet built).
- **gym-017**: descends on evidence, every sweep complete, on a genuinely hardened product.

The product is real: the invalid-priority crash now exits `rc=1` with a clean message and **no
traceback** (verified on the delivered head `282a38b`), schema validation is in, and the 44-test
suite is intact.

**Why this is the convergence proof and not luck:** round 1 over-produced *because there were ~14
real bugs*; round 2 under-produces *because they are fixed*. The count tracks the actual defect
population, which is what a convergent loop must do.

---

## Part 2 — P18 fixes CONFIRMED under load

Recorded with the live event, so a regression is detectable.

### F3 — a partial sweep is not a sweep ✅ HELD, BOTH ROUNDS

`swept: true` in both rounds, legitimately — all three lenses contributed (with salvage assisting,
below). The guard that gym-016 proved and gym-017 stress-tested.

### F4 — findings survive a truncated turn ✅ CONFIRMED ON A REAL TRUNCATION

This is the fix P17 tried in prose and failed. gym-017 round 2:

```
lens_report_truncated  {"lens": "goal_alignment", "round": 2, "chars": 62,
                        "body": "All 44 tests pass. Now let me probe edge cases systematically.",
                        "salvaged_chars": 2952}
lens_findings_salvaged {"lens": "goal_alignment", "round": 2, "findings": 30}
```

The lens genuinely died at 62 characters — the exact gym-015/016 failure — and salvage recovered
**30 findings / 2952 chars** from the file it had been appending to. Without F4 that lens
contributes nothing and the round degrades to `swept: false`; with it, the sweep completed and the
count stayed interpretable. **This is the clearest possible proof the file-artifact approach works
where the prose instruction did not.**

### F19 — observations mined for every open scope ✅ WORKS (the lost-finding problem is solved)

gym-016's REPL bug evaporated because gap analysis ran against one narrow scope. gym-017's
`gap_extraction_fanout` mined the report against all four open scopes, and no diagnosed defect was
lost. The lost-finding problem is genuinely fixed — but the mechanism over-produces (Part 3, F19).

### The commit-history lens — the org's best pathology detector ✅

Round 2, from git history alone, it caught the delete-to-pass that F13 missed:

> *"Commit `03390ff` ... its verification claim of 5/5 tests is misleading — those 5 tests are the
> original baseline..."*

Third run running (gym-015, gym-016, gym-017) this lens has surfaced an orchestration pathology as
a "process signal". It should be treated as org telemetry, not just documentation feedback — it is
the most reliable detector of dispatch/rework defects the org has.

---

## Part 3 — P18 fixes that need a second pass (the P19 work)

Four fixes shipped in P18 are load-bearing but flawed. Each is confirmed by gym-017 evidence.

### F19-redux — the fan-out over-produces AND breaks convergence (severity: CRITICAL — the blocker)

**Elevated from "high" to critical after round 3.** This is not just wasted tokens: it is why the
loop re-ascends and may never terminate. Round 3 turned ~8 distinct findings into 24 tasks (the
undo crash alone became 5), taking the open queue 9 → 24 — the wrong direction for a
propagation-count termination. A loop that multiplies every new finding by the scope count cannot
be relied on to reach the evidenced zero it terminates on.

**Evidence.** Round 1's fan-out ran gap analysis 4×: `4 + 10 + 7 + 0 = 21` candidate gaps → 22
tasks, from ~14 distinct findings. Round 3 repeated it: `5 + 6 + 4 + 8 = 23` → 24 tasks from ~8
distinct findings.

**The harm is precisely the COUNT, not the work.** The round-3 implementer opened its plan with
*"I've deduplicated the 20 task items into 9 unique concerns"* and worked the 9 — so the fan-out
does NOT 9×-24× the implementation effort; the implementer collapses the duplicates itself. What
F19 corrupts is the propagation COUNT (`new_tasks: 24` when ~9 are distinct), and that count is
the loop's termination signal (P10.4: terminate on zero NEW). A count inflated by paraphrase
duplication cannot descend to a trustworthy zero, so the loop cannot terminate on evidence even
though the underlying work is bounded and converging. F19-redux must therefore fix the COUNT (one
task per distinct finding) even more than the effort — the termination contract depends on it. The implementer itself annotated the duplicates *"Same as
above"*. Examples of one finding filed 3×:

```
Prevent negative IDs / Implement schema validation to reject negative IDs / Validate input to reject negative IDs
Fix string-to-boolean coercion / ...during input parsing / Implement schema validation to prevent...
```

All 21 landed on ONE scope node (`_seam_owner` routed them together) but content-addressing could
not dedup the paraphrases. gym-016 produced 3 tasks in the same situation; gym-017 produced 22.

**Downstream cost — this is the serious part.** The over-produced, aggressive schema-tightening
tasks broke existing tests, and the implementer responded by **rewriting the test suite from 44
tests to 5** (commit `03390ff`) — a delete-to-pass and a standing-intent violation. It did not
ship only because a non-fast-forward push forced a merge that reconciled the 44-test suite back
(merge luck, same as gym-015's F13 near-miss). **F19's over-production caused destructive
implementer behaviour**, not just wasted tokens.

**Cause.** Fanning gap analysis across N overlapping scope-goals mines the same report N times.
The fan-out was the wrong mechanism for the lost-finding problem.

**Fix.** Extract the report ONCE against the *product* (root) goal — which covers all findings —
then route each derived task to its owning scope via the existing `_seam_owner` / `_best_scope_for`.
That gets every finding once, no duplication, and the routing is code already trusted. Delete the
per-scope fan-out (`_extraction_scopes`).
- **Test:** a report describing defects in two sibling scopes produces exactly one task per
  distinct defect, each routed to its owner; no near-duplicates across scopes.

### F17-redux — inert, plus a latent false-negative (severity: high)

**Evidence of inertness — now confirmed across three rounds.** Zero of the 24 round-3 task bodies
(and none in rounds 1–2) carry a `REPRO:` line — gap analysis reformulates lens findings into
plain bodies via `_plain_tasks` and strips the repro, even though the lens FINDINGS carried them
(round 3's undo-crash finding included a full REPRO). So `_drop_false_defects` never ran once:
`false_defect_rejected` fired zero times across the entire run. F17 is dead code in the pipeline.

**The round-3 near-miss makes the latent bug concrete.** The undo crash is REAL and its repro
CRASHES (`JSONDecodeError` traceback). Because F17 was inert, all five copies of that task
survived — the correct outcome, by luck. Had the repro survived into the body (which F17-redux
requires for F17 to work at all), F17's current rule (`rc≠0 + output → HANDLED → drop`) would have
DROPPED a real critical bug, five times over, reading the crash traceback as a clean diagnostic.
So the two halves of F17-redux are not independent: carrying the repro through (to make F17 fire)
without the traceback distinction (to make it fire correctly) would actively delete real crash
bugs. Ship both or neither.

**Evidence of the latent bug.** The invalid-priority finding *should* be kept (it's a real crash),
and it was — but only because F17 never ran. Had a repro survived, F17 would have MIS-DROPPED it:

```
$ python3 todo.py add x --priority critical
rc=1
  ...ValueError: invalid priority 'critical'...          ← a TRACEBACK
```

A crash exits non-zero WITH output (the traceback). F17's rule — "non-zero exit + output →
HANDLED → drop" — cannot tell a crash from a clean rejection, so a real crash bug would be dropped
as fabricated. This is the exact false-negative the fix was warned against.

**Fix, two parts.**
1. Carry the `REPRO:` from the lens finding through gap analysis into the task body, so F17 has
   something to run. (`_plain_tasks` already folds `REPRO:` into the preceding body — the gap
   the fold doesn't cover is that gap analysis REWRITES findings and loses the repro entirely.
   Gap analysis must preserve a finding's repro when it exists.)
2. Distinguish a traceback from a clean diagnostic: if the repro's output contains
   `Traceback (most recent call last)`, the input is NOT handled — KEEP the task. Only a non-zero
   exit WITHOUT a traceback (argparse exit 2, a validation message) counts as handled.
- **Test:** a finding whose repro raises a traceback is KEPT; a finding whose repro exits cleanly
  with a diagnostic and no traceback is dropped.

### F13-redux — a flaky baseline gives a false positive (severity: medium)

**Evidence.** F13 fired for the first time, and wrongly:

```
delivery_test_count  {"count": 55, "previous": null}   ← first publish, but the branch has 44 tests
test_count_regressed {"count": 44, "previous": 55}     ← 44 < 55 → flagged, but 44 is the TRUE count
```

The delivered branch has a stable 44 tests (verified: `Ran 44 tests`, and 44 `def test_` on the
remote). The `55` baseline was a miscount from scraping `"Ran N tests"` at the first publish. So
F13 flagged a regression that did not happen.

**Note on scope.** The REAL regression (44→5 delete-to-pass) never reached F13 — the merge
reconciled it pre-publish, so F13 only ever compared delivered-vs-delivered. The commit-history
lens caught the real one; F13 caught a phantom.

**Fix.** Count test definitions deterministically (AST parse of the test files for `def test_`),
not by scraping the runner's stdout. A stable count removes the false positive. Keep the flag-not-
block behaviour — it is correct; only the number was wrong.
- **Test:** a suite that reports "Ran 55 tests" via a flaky runner but has 44 `def test_` records
  44; a genuine drop to 40 flags, a stable 44 does not.

### F14-refinement — the stop-intent guard intercepts legitimate `archive` (severity: low)

**Evidence.** Wrapping up gym-017, an `archive effort-gym-017-todo-product` message to `/nl` fired
`operator_intent_unmatched` and returned F14's "send `abort <id>`" reply instead of archiving.
This is F14 (P18) working — `archive` is one of `_STOP_INTENT_RE`'s verbs, and the message did not
match the strict `_DECISION_RE` (`approve|modify|abort`), so it was correctly caught as
stop-shaped-but-off-grammar. `abort <id>` then worked (`lifecycle=aborted`).

But `archive <id>` was a valid, PO-handled command BEFORE F14 shipped (used to stop gym-015).
F14 now intercepts it and diverts to "use abort", which is a minor over-reach: a real command verb
is being treated as an unparseable stop. Live self-confirmation that F14 fires, with a rough edge.

**Fix.** Add `archive` (and `stop`/`halt` on an effort id) to the recognised stop-command grammar
so they route straight to the abort handler, rather than to the "I didn't understand" reply. F14's
unmatched path should catch genuinely off-grammar phrasing, not a synonym of `abort`.
- **Test:** `archive <effort-id>` aborts the effort directly; only phrasing with no clear verb+id
  produces `operator_intent_unmatched`.

### F20 — recovery cannot clear a foreign-uid workspace (severity: medium)

**Evidence.** gym-017 round 3 stalled and required a human `re-run it`. The execution turn
abandoned unable to write `todo.py`, and the org's message was explicit:

> *"the worker hung mid-turn and my 2 recoveries didn't take — stopping auto-retry to avoid a
> loop."*

Direct inspection of the worker container (`ao-ot-2`) found the cause:

```
-rw-r--r-- 1 root root  todo.py        ← owned root:root, mode 644 (only root writable)
```

The little-coder daemon runs as a **non-root** uid, so it could not write a root-owned 644 file
→ abandon. A git/merge operation earlier in the effort created the file with root ownership.

**Why recovery didn't clear it.** P16 discards *uncommitted edits* and re-engages; it does not
touch file *ownership*. So each recovery re-engaged onto the same un-writable tree and abandoned
identically — 2 recoveries, same wall, then the loop-guard stopped. The infra-freeze auto-recovery
that would have helped (a fresh re-clone resets ownership to the daemon's uid) either did not
classify a write-permission abandon as an infra symptom, or was not on this path.

This is the [false-focus-clone-mask] foreign-uid class recurring: the durable fix there was an
entrypoint `chmod -R 0777 /workspace`, which evidently is not applied on every workspace mutation
(a git op running as root re-introduces root-owned files).

**Manual unblock used this round:** `chmod -R 0777 /workspace` on the worker container, then
`re-run it`. The effort resumed immediately.

**Fix.**
1. Classify a write-permission / EACCES abandon as an INFRA symptom, and route it to the
   re-clone recovery (fresh checkout resets ownership) rather than the re-engage path.
2. Investigate WHY a git/merge op produced a root-owned file — if the org's git machinery runs
   as root inside a non-root-daemon container, it will keep re-introducing un-writable files.
   Either run those ops as the daemon uid, or `chmod -R a+rwX /workspace` after any privileged
   git operation.
- **Test:** a workspace whose target file is root-owned 644 triggers a re-clone recovery, not a
  re-engage loop; after recovery the daemon can write.

### F4-redux — works, but the plumbing is fragile (severity: medium)

F4 salvage confirmed working (Part 2), but gym-017 exposed two fragilities:

1. **Inconsistent per-lens writing.** Lenses 1–2 wrote findings as a single end-of-turn heredoc;
   lens 3 wrote incremental `echo >>`. Only the incremental writer survives an EARLY truncation —
   a heredoc-at-the-end lens that dies mid-probe still loses everything. The instruction is
   followed inconsistently because it depends on model cooperation.
2. **Worker-coordination race.** `_clear_lens_findings`, the lens `wake`, and
   `_salvage_lens_findings` each independently acquire a worker via the scheduler, and
   `/tmp/lens-findings.txt` is per-container. They shared a worker in gym-017 only because one was
   busy. If they diverge, salvage reads a different container's empty file. Same bug class as F6.

**Fix.**
1. Capture findings harness-side: the daemon already streams every command; parse the
   `FINDING:` and `REPRO:` echoes from `WorkResult.commands` (built for F18) rather than depending
   on the model to write a file. This makes salvage independent of model cooperation AND of the
   file's location.
2. If the file approach is kept, pin all three findings-file operations to the lens's own worker
   URL (like F6), not the acquire path.
- **Test:** a lens that dies after 2 of 10 probes, having streamed 2 `FINDING:` lines, yields
  exactly those 2 findings regardless of which worker the salvage step would have acquired.

---

## Part 4 — carried forward, still unbuilt

| id | what | status |
|---|---|---|
| F1/F15 prevention | read-only workspace for plan turns | image-blocked; the P17 revert is the interim, never fired in gym-017 |
| F12 refile | route scope-refused tasks to their owner | unbuilt; `dispatched` state stops the false claim |
| H1 | lint + typecheck in `check_cmd` | image-blocked (no linter, no egress) |
| F18 | verification-claim check | shipped in P18, NOT exercised in gym-017 (no turn made an unbacked claim this run) |
| D1 | GAP tasks excluded from the count | operator decision, unchanged |

---

## Implementation order for P19

1. **F19-redux** — extract once against the product goal, route via `_seam_owner`. Highest value:
   it stops the over-production that drove a delete-to-pass, and simplifies the loop.
2. **F17-redux** — carry the repro through gap analysis + distinguish traceback from diagnostic.
   Turns a dead, latently-dangerous filter into a working one.
3. **F4-redux** — capture findings harness-side from `WorkResult.commands`; drop the file
   dependency. Makes the confirmed-working salvage robust.
4. **F13-redux** — AST test-count. One function; removes the false positive.

**Gate:** full suite green, deploy, wipe the arena, run gym-018 — success criterion: the count
descends to an EVIDENCED ZERO and `scope_completed` fires (gym-017 converged but this doc was
written before it reached zero), with `false_defect_rejected` firing on any fabricated defect and
NO near-duplicate tasks in a single round.

---

## Method note

The gym-017 read-out required three mid-run corrections, each caught by running the code rather
than trusting a report:

1. *"the 44→5 collapse shipped"* — REFUTED. The merge reconciled it to 44 before publish; the
   delivered branch has the full suite and the crash fixed.
2. *"F13 caught the delete-to-pass"* — REFUTED. F13 fired on a phantom (flaky baseline); the
   commit-history LENS caught the real one.
3. *"F19 is a runaway"* — REFUTED. Round 2 descended 22 → 9; the over-production is a bounded tax
   proportional to the real defect count, not a runaway.

Same discipline the plan prescribes for the org, applied to reading the org: settle a checkable
claim by running it.
