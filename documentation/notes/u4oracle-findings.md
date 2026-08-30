# Findings — U4: frontier-oracle-on-stall (2026-08-30)

Item: FRONTIER-ORACLE-ON-STALL, per PLAN §2's U4 row and ORCHESTRATION-DESIGN §7.
Branch: `work/u4oracle`. Round 2 (fix round) appended 2026-08-30 after the item was
refuted 2/2.

**STATUS: PARKED, not complete.** U4's *Validated by* column is not satisfied and cannot
be from this item. §C.7: "a phase that cannot satisfy its column does not merge. It parks
with a written reason." The reason is F4. What IS delivered is a stall signal a machine can
compute, an escalation decision, a durable record, and the handle a dispatcher will read —
all executable, all proven RED→GREEN.

## DECISIONS entries to append

### 2026-08-30 · U4 · class 2 — the stall definition is agent-org's, ported, with two stated differences
DECISION: `oracle_on_stall.evaluate` adopts the burn-down loop's convergence test
          (`orchestrator.py` ~8414): a round whose failure SIGNATURE is not novel
          against every signature seen on this item is not progress, and two such
          rounds in a row is a stall (`stall >= 2`, agent-org's threshold).
          `failure_signature` is `Orchestrator._failure_sig` (`@staticmethod` at
          orchestrator.py:8845, `def` at 8846) byte for byte.
          TWO DIFFERENCES, deliberate:
          (1) agent-org has a countable metric (compiler errors) so its test is
              `improved OR novel_sig`. A harness round is a tester's pass/fail with
              prose — there is no count to improve, so the signature axis carries
              the whole test rather than a faked `improved`.
          (2) a round must also have MOVED THE BRANCH HEAD. This is the round-level
              analogue of little-coder's FLAIL GUARD (`flail_tripped`,
              `little-coder/src/littlecoder/agent.py:165`), and §6's hygiene rule
              justifies it: a failure that changes while the code does not is NOISE,
              and noise "must never be recorded as a constraint". Without it a flaky
              test resets the stall counter forever and the detector never fires on
              the item that most needs it.
          A THIRD DIFFERENCE, found in round 2 and worth stating because it is the
          reason the ported detector cannot reproduce the state a verifier reported:
          agent-org SEEDS `seen_sigs = {last_sig}` from the pre-existing failing log
          BEFORE its loop, so its round 1 can be non-novel. The port has no such
          prior log, so its round 1 is unconditionally progress. That is what makes
          `stall >= 2` require strictly more than 2 rounds here and not there.
CITED:    §C.2 class 2 — closest to an existing house pattern; PLAN §3 keeps
          agent-org's CDCL constraint learning, so a second dialect of "did this
          round teach us anything" would be a regression, not a unification.
REVERT:   One function, `evaluate()`. Deleting the `moved` term restores the pure
          agent-org test; the tests name each axis separately so the effect of
          dropping one is visible immediately.

### 2026-08-30 · U4 · class 2 — a frontier worker gets `no-oracle-above`, not a self-escalation
DECISION: When the stalled worker already runs on the `claude-code` runner — which
          is exactly what the shipped default `all-cloud` profile means — the stall
          is recorded with outcome `no-oracle-above` and NO escalation.
CITED:    §7 — "the frontier is an oracle invoked on a stall signal, not a better
          worker". claude-code -> claude-code would fill the audit trail while
          changing nothing, and C.7 makes that trail the deliverable's twin: a
          record that reads as an escalation must correspond to one.
CONSEQUENCE: the escalation path is only exercisable under a profile whose worker
          is local (`all-local`, `local-work-cloud-review`) — §7's 95/5 split. The
          drill therefore submits with `-RunnerProfile local-work-cloud-review`.
REVERT:   Delete the `worker.runner == oracle_name` branch in
          `resolve_escalation`; every stall then records an escalation.

### 2026-08-30 · U4 · class 1/2 — the ledger's field is `item_id`, never `item`
DECISION: The escalation ledger names the work item `item_id`.
CITED:    §C.2 class 1, but paid for rather than chosen: `.item` on a .NET
          collection resolves to the `IList.Item` INDEXER. A PowerShell reader
          writing `Where-Object { $_.item -eq $id }` therefore compares a PSMethod
          to a string — false, always, and with no error. See F1: it made this
          drill's own control checks pass while checking nothing.
REVERT:   Rename the key in `record()` and its one test; no consumer outside this
          module reads the ledger yet.

### 2026-08-30 · U4 · class 1 — `queue.ps1 -RunnerProfile`, not `-Profile`
DECISION: The new submit parameter recording which profile an item is worked under
          is `-RunnerProfile`.
CITED:    `$Profile` is a PowerShell AUTOMATIC variable (the profile script path);
          a param of that name shadows it for the whole script scope.
          PSScriptAnalyzer flagged it on the first edit.
REVERT:   Rename the parameter and the one `Set-Field` call; the stored field is
          `profile`, and an item without it means "the surface default", which is
          also what every item queued before today means.

### 2026-08-30 · U4 · class 2 — the detector reads `results[]`, it does not keep its own round store
DECISION: Rounds come from the queue item's existing `results[]` array (verdict,
          sha, reason, evidence) that `queue.ps1 -Fail` has always written.
CITED:    §C.2 class 2 (most reversible, closest to the house pattern). A second
          store would have to be kept in step with the first by whoever remembers
          to, and the stall signal would then be as reliable as that habit.
REVERT:   `failing_rounds()` is the whole adapter; nothing else knows the shape.

### 2026-08-30 · U4 · class 2 — the drill runs in a scratch state namespace, not the live one
DECISION: `verify-oracle-on-stall.ps1` points `AI_STACK_WORKTREE_STATE` at a temp
          directory for its whole run.
CITED:    §C.7 — "the audit trail is the deliverable's twin". `verify-merge-protocol.ps1`
          drives the LIVE queue and deletes its own rows afterwards; that is
          survivable for queue rows and not for an APPEND-ONLY evidence ledger,
          where the cleanup would be a rewrite. A drill must not put invented
          firings into the record the phase is validated against.
REVERT:   Delete the two `$env:AI_STACK_WORKTREE_STATE` lines; the drill then runs
          against the live namespace exactly as the merge-protocol drill does.

### 2026-08-30 · U4 round 2 · class 2 — an UNREADABLE branch head is not "the code did not move"
DECISION: Reversed the round-1 rule that "a missing sha counts as not moved — fail
          toward detecting". A round whose branch head could not be read is now
          recorded but NOT SCORED: the stall counter is neither advanced nor reset,
          and the trail says why. Three enforcement points, because one was not
          enough: (a) `queue.ps1 -Pass/-Fail` now checks `$LASTEXITCODE` on its
          `git rev-parse` and REFUSES the verdict, matching the two sibling call
          sites that already did (`-Submit`, `-Resubmit`); (b) `evaluate` does not
          score an unmeasured round; (c) `failing_rounds` normalizes any sha that is
          not a git object name to `""`, so items ALREADY written with a bad value
          cannot manufacture an escalation when the detector next reads them.
WHY:      `git rev-parse <missing-ref>` prints the REF NAME on stdout and exits 128.
          The verdict path did not check the exit code, so a branch deleted or
          renamed between rounds was recorded as `sha: "drill/oracle-stall"` — the
          same constant every round, which the old rule read as "the code did not
          move" and escalated on. A tooling failure manufacturing a frontier
          escalation, silently, in the false-positive direction. §6's hygiene rule
          ("noise must never be recorded as a constraint") applies with more force
          to a failed MEASUREMENT than to a flaky test.
EVIDENCE: reproduced directly — a queue item written through the real tool after
          `git branch -D` recorded `"sha": "probe/oracle"` and `"tested_at_sha":
          "probe/oracle"` with exit 0. Now RED→GREEN in the drill: with the guard
          removed 31/34 (the three Step-8 checks fail: `exit=0`, `results=1`, the
          branch name stored as a sha); with it, 34/34.
REVERT:   Three named places, each small: the `if ($LASTEXITCODE -ne 0 ...) { Die }`
          block in queue.ps1's verdict path; the `scored` term in `evaluate`; the
          `object_name()` call in `failing_rounds`. Reverting all three restores the
          round-1 behaviour exactly.

### 2026-08-30 · U4 round 2 · class 2 — the drill's refs and work line are pinned per run
DECISION: `verify-oracle-on-stall.ps1` suffixes every branch and every queue id with
          a per-run token, and pins `AI_STACK_WORK_LINE` to its own checkout's HEAD.
          On ANY failed check it KEEPS the scratch namespace and prints the raw
          ledger and the queue items.
WHY:      See F7. Isolating the state dir did not isolate the drill: git refs are
          repository-global and the drill force-DELETED fixed names in its preamble,
          so any second run anywhere tore the branch out from under a running one —
          and the branch heads are exactly what the stall signal is computed from.
          `Resolve-WorkLine` falls back to the OPERATOR'S main checkout's current
          branch, which sibling sessions move mid-run. And the old drill deleted its
          scratch dir unconditionally, which is why the one run that ever failed
          could not be diagnosed afterwards.
REVERT:   Drop the `$RUN` suffixes and the two `$env:AI_STACK_WORK_LINE` lines; the
          keep-on-failure block is the `if ($failed.Count)` branch at the end.

### 2026-08-30 · U4 round 2 · class 2 — `record()` refuses a structurally impossible firing
DECISION: `record()` raises rather than writing a row whose `stall` exceeds
          `rounds - 1`, whose `stall >= threshold` with `rounds <= threshold`, or
          whose trail length does not equal its round count. `evaluate`'s `stalled`
          test tightened from `len(rounds) >= threshold` to `> threshold`.
WHY:      F7. The reported anomaly was a ledger row that cannot exist; the ledger is
          the audit trail the phase is validated against, so an impossible row must
          fail where it happens rather than becoming something a reader finds later
          and cannot explain.
REVERT:   Delete the two `raise ValueError` blocks at the top of `record()`.

---

## F1 — the drill's own control checks passed while checking nothing (fixed, round 1)

The first green-looking run had `[PASS] round 1 records no escalation` and
`[PASS] three failing rounds and NO escalation`. Both were vacuous. Two independent
silent-false mechanisms stacked in one expression:

1. **`$json | ConvertFrom-Json` vs `ConvertFrom-Json $json`.** In PS5.1 the PIPELINE
   form emits a JSON array as ONE object, so `@($raw | ConvertFrom-Json)` produced a
   one-element array whose single element was the whole array.
2. **`.item` on that array** resolved to the `IList.Item` indexer, so
   `$_.item -eq $id` compared a PSMethod to a string: false, silently, forever.

It was caught only because the POSITIVE check (`a ledger row exists`) failed on the
same helper — a suite of controls alone would have shipped this. Fixed by parsing
with the parameter form, flattening explicitly, and renaming the field to `item_id`
so the landmine cannot be re-armed by the next reader. A test now pins the name.

This is PLAN §0 A6's class exactly: a check that passes while checking nothing.

## F2 — PS5.1: a one-element array returned from a function has no `.Count`

`(Get-Ledger).Count -eq 1` was FALSE with exactly one row on the ledger. A function
returning a one-element array unrolls it to a scalar `PSCustomObject`, and a
`PSCustomObject` has no `Count` property — so the comparison was `$null -eq 1`.
`@(Get-Ledger).Count` is the correct idiom. Worth knowing anywhere in this repo a
PowerShell helper returns "zero, one or many" and a caller counts them.

## F3 — the two "shared state dir" resolvers disagree (NOT fixed)

`resolve.ps1:25` (`Get-SharedStateDir`) honours `AI_STACK_WORKTREE_STATE`.
`durable_checks.registry_path` (U3) does not — it goes straight to
`git rev-parse --git-common-dir`. So a drill or test that redirects the namespace
gets an isolated queue and an isolated oracle ledger, but writes the durable-check
registry into the LIVE `.git/agent-worktrees/`.

`oracle_on_stall.state_dir` honours the override and says in a comment that
`durable_checks` does not, so nobody copies the wrong half. Not fixed here: it is
another module's file and no current caller redirects the namespace while adding a
durable check. One-line fix when someone owns it.

## F4 — U4's Validated-by column: ONE HALF IS PARKED AND THE OTHER IS WEAKER THAN CLAIMED

§2's U4 column reads: *"Gym: same anchored item run per quadrant (runner x target),
outcomes compared; stall->oracle observed firing at least once"*.

### "stall -> oracle observed firing at least once" — PARTIALLY met. The stall is CONSTRUCTED, not observed.

Round 1 of this item claimed this half "SATISFIED, and executable". That was an
over-claim and it is withdrawn. What `verify-oracle-on-stall.ps1` does is drive the
real `queue.ps1` through a stall the DRILL MANUFACTURES: a scripted tester reports
the same failing case three times, and the script itself moves the branch head
between rounds. Nothing was actually stuck. No agent was actually failing to
converge. The column asks for the oracle to be **observed firing**, and the ordinary
reading of that is on a real item, in a real run.

- **What IS proven, executably:** the detector's definition (52 unit tests, both
  mutations RED with near-disjoint failure sets), the wiring at the only moment the
  line learns anything (`-Fail`; unwire it and the drill drops to 18/25), the
  escalation decision including `no-oracle-above`, the durable record, and
  `pending()` — the handle a dispatcher reads. 34/34 in the drill.
- **What is NOT proven:** that the mechanism fires on a stall nobody arranged. That
  needs an item worked by a runner, failing its tests repeatedly, on its own.
- **What would satisfy it:** the dispatcher (below) plus one real anchored item
  driven until it genuinely fails to converge, with the ledger row read back
  afterwards. `queue.ps1 -Oracle` is the surface that would show it; today it prints
  nothing, because nothing has stalled.

The drill prints this caveat on every green run, so a reader of the output cannot
mistake the one for the other.

### the per-quadrant gym runs — NOT MET, and cannot be from this item.

A quadrant run needs the same anchored item DISPATCHED to each runner. Nothing in
the harness dispatches to any runner: `config.resolve_role` / `Resolve-RoleTarget`
resolve role+profile to a runner and model, and no code path then submits work to
one.

Verified in this worktree, and stated precisely because the loose version is wrong:
`grep -rn 8090 scripts/agent-harness/*.ps1 *.py *.json *.md *.conf` returns EXACTLY
ONE hit — `harness.config.json:47`, the endpoint DECLARATION. No `.ps1` and no `.py`
mentions the port at all, which is the claim that matters: the address is configured
and nothing dials it.

**Parked, per §C.7**, not papered over: the escalation this item builds is a
DECISION plus a durable record plus `pending()`, the handle a dispatcher reads. What
it does not do is run the oracle's round, because there is nothing to run it with.

## F5 — the live `little-coder` container publishes NO ports at all (verified twice)

Sharper than "the API port is unpublished". Verified on this machine, 2026-08-30, and
re-verified in round 2:

- `docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` ->
  `{"9090/tcp":[]}`. `docker port little-coder` prints nothing, exit 0.
- `coder/docker-compose.yml`, rendered, DOES declare `127.0.0.1:9091 -> 9090` for
  the metrics port. The running container does not have it. The live container
  diverges from its own compose file: it was started before that block, or without it.
- From the host: `:8090` and `:9091` both refuse. From inside:
  `docker exec little-coder curl -fsS http://localhost:8090/health` returns
  `{"status":"ok","version":"0.1.0",...}` — the daemon is healthy, just unreachable.

**The transferable rule: `docker inspect` / `docker port` for what IS; compose text
for what was INTENDED. Never the second when you mean the first.** A substring match
over a compose file proves a door is DECLARED, never that it EXISTS, and this host is
a live counterexample.

Consequences for whoever builds the dispatcher:
`harness.config.json`'s `runners.little-coder.endpoint = "http://127.0.0.1:8090"` is
unreachable from where the harness runs. Either the coder plane publishes the API
port (a plane change, with the SERVICE-LIFECYCLE checklist that implies) or the
dispatcher goes through `docker exec` / `lc-net`. That choice belongs to the
dispatcher item, so this file records the constraint rather than pre-empting it.

## F6 — PLAN §0's A11 row understates the gap (proposed amendment, NOT applied)

A11 reads: *"The harness ran 100% frontier (its `little-coder` runner is wired,
`status: unproven`, and honest about it)."*

"Wired" is doing more work than the code supports: what exists is CONFIG RESOLUTION
(`Resolve-RoleTarget`, `config.ps1:187`; `resolve_role` in `config.py`), and no
dispatch of any kind for either runner. The row's verdict (UNTESTED and preserved) is
unaffected — this is a precision correction, not a contradiction.

Not applied to PLAN.md from this branch. Two reasons: sibling worktrees are live on
the same file, and amending the plan is `-AmendAnchor` semantics per §B — the
operator's call, not a side effect of a fix round. `MODULE.md` now states the
limitation at the place a reader would act on it.

## F7 — THE DRILL'S NON-DETERMINISM: what it was, and what the reported figure was not

A verifier's FIRST run of round 1's drill in a clean checkout scored 19/25 and
reported a ledger row with `rounds=2, stall=2` and a two-entry trail, plus two ledger
rows where there should be one. Fifteen further attempts — sequential, 2-way and
3-way concurrent, a fresh detached worktree — all scored 25/25. This section is the
resolution, in two parts, because the honest answer is not one thing.

### (a) The reported figure is UNREACHABLE, and that is now executable, not an argument.

`evaluate`'s round 1 is unconditionally progress: `seen` is empty so the signature is
novel, and `i == 1` so the head cannot have "failed to move". The counter rises by at
most one per round. Therefore `stall <= rounds - 1` always, and `stall >= threshold`
needs STRICTLY more than `threshold` rounds. `rounds=2, stall=2` cannot come out of
this function.

Three things now hold that, rather than a paragraph:

- `test_stall_can_never_exceed_rounds_minus_one` asserts the invariant by EXHAUSTION
  over all 1,555 sequences of up to four rounds drawn from two failure texts and
  three head states (including "not recorded").
- `evaluate`'s `stalled` test tightened from `len(rounds) >= threshold` to
  `> threshold`, so the impossible state is impossible rather than merely unobserved.
- `record()` REFUSES a verdict violating the invariant, or one whose trail length
  does not match its round count, and says so loudly. If it ever happens again it
  fails where it happens, with the verdict in the message, instead of leaving a row
  nobody can explain.

The mutation that WOULD make the reported state reachable is caught: forcing
`progress = False` reddens `test_stall_can_never_exceed_rounds_minus_one` among 21
others.

Worth recording, because it is why the figure looks plausible: **agent-org's original
CAN reach it.** `orchestrator.py` seeds `seen_sigs = {last_sig}` from the
pre-existing failing log before its loop, so its round 1 can be non-novel and its
stall can reach 2 in two rounds. The port has no prior log. A reader carrying
agent-org's shape across would expect exactly the state that was reported.

### (b) A real, reachable spurious-firing mechanism — FOUND AND FIXED.

Round 1's drill shared three pieces of mutable global state with every other process
on this machine, and only the first was isolated:

1. **The state dir** — isolated, correctly, via `AI_STACK_WORKTREE_STATE`.
2. **The git refs — NOT isolated.** `drill/oracle-stall` and `drill/oracle-move` are
   fixed names in the repository's shared ref store, visible from every worktree, and
   the drill's own preamble force-DELETED them. Any second run anywhere — a
   verifier's, a sibling agent's, a crashed run's leftovers — rewrote or removed the
   branch heads a live run was computing its stall signal from.
3. **The work line — NOT isolated.** `Resolve-WorkLine` falls back to the OPERATOR'S
   MAIN CHECKOUT's current branch. That is out-of-process global state, and sibling
   sessions move it: observed directly on 2026-08-30, the main checkout went from a
   branch, to detached mid-rebase, to a different commit, inside ten minutes, because
   another session's `verify-merge-protocol.ps1` was rebasing in it. When the line
   resolved to one that does not contain the drill's branches, `-Submit` was refused
   by the hook-attestation guard — reproduced here exactly, and it fails in a way
   that reads as a detector bug.

(2) and (3) meet at a silent defect: `git rev-parse <missing-ref>` prints the REF
NAME on stdout and exits 128, and `queue.ps1 -Fail` did not check the exit code. So a
branch pulled out from under a running drill did not error — it recorded
`sha: "drill/oracle-stall"`, identical on every subsequent round, which the old
"missing sha counts as not moved" rule read as a stall. **That is a reachable path
from cross-run interference to a spurious frontier escalation, in the false-positive
direction, with no error anywhere.** It would fire the CONTROL item, which produces
the second ledger row.

Fixed: per-run ref and id suffixes, a pinned work line, the exit-code check, the
not-scored rule, and `object_name()` normalization. RED→GREEN evidence is in the
DECISIONS entries above.

### (c) What remains unexplained, stated as such.

The exact figure — `rounds=2, stall=2`, trail of 2 — is not produced by any mechanism
I found, and (a) says it cannot be. The most likely reading is that it was
reconstructed from round 1's own output: check 18's detail string printed the TRAIL
LENGTH under the label `rounds=` (`("rounds=" + @($row.trail).Count)`), directly
beside a passing `stall=2`. A reader of a failed run would assemble exactly that
sentence. That label is now `trail_entries=`, and the drill separately asserts
`$row.rounds -eq 3` with the reason in the check name.

I am not claiming that IS what happened — I could not reproduce the run, and the
scratch namespace that held the answer was deleted by the drill's own cleanup. That
is fixed too: **a failing drill now keeps its scratch dir and prints the raw ledger
and every queue item.** "Could not reproduce it in fifteen attempts" is what a drill
that deletes its own evidence buys you, and it cost a full verification cycle.

## F8 — `verify-merge-protocol.ps1` does not pass, and it mutates the operator's checkout

Round 1's commit message claimed *"verify-merge-protocol.ps1 66/66 — unchanged by the
queue.ps1 edits"*. **That claim does not reproduce.** Run twice on 2026-08-30 from
this worktree, it scored **35/66 both times**. The claim is withdrawn; see the
corrected commit message.

What actually happens, from the captured log:

- The cascade starts at `ERROR: branch 'work/drilla' not found`, then
  `fatal: ambiguous argument 'work/drilla': unknown revision or path not in the
  working tree` — the drill's own branch disappears mid-run. It uses FIXED names
  (`work/drilla`, `work/drillb`, `drill/verify-d`, worktrees `wt-drilla`/`wt-drillb`)
  and force-deletes them in its preamble: the same shared-global-state class as F7(2),
  in a drill that is far more expensive to re-run.
- **It rebases in the OPERATOR'S MAIN CHECKOUT and leaves it mid-rebase when it
  fails.** After the second run the main checkout was detached at a rebase pick
  (`git reflog`: `rebase (start): checkout drill/verify-d` → `rebase (pick): ...`).
  I aborted it and returned it to `refactor/ai-stack-cleanup`. Its own final check,
  `operator checkout still on its own branch`, FAILED — the drill knows, and reports
  it after the damage.
- Concurrent sessions running it collide by construction. While diagnosing, the main
  checkout moved twice more under me, from another session doing the same thing.

Not fixed here — it is another item's file and a live, expensive drill. But nothing
should cite its score without running it, and **nothing should run it while another
session is working in this repository.**

I did NOT establish that my `queue.ps1` edits are innocent by re-running it on a base
without them; re-running it is itself the hazard above. The positive evidence that
they are innocent: `verify-oracle-on-stall.ps1` drives `-Propose`, `-ConfirmAnchor`,
`-Submit`, `-Claim`, `-Fail` and `-Resubmit` through the same file and scores 34/34,
and the failing path (`git rev-parse` of a branch that no longer exists) is not code
this branch touched at the point it fails.

## F9 — `ruff check .` is not clean on the work line (NOT fixed, not mine)

Round 1's commit message said `ruff — clean`. At repo root, `ruff check .` reports
**2 errors**, both `F811 Redefinition of unused 'subprocess'` in
`scripts/agent-harness/test_anchor_schema.py:267` — a duplicate import block. The
duplicate block was introduced by U2's `86ffa62` (`git blame -L 266,268`), which is an
ancestor of this branch, so the errors predate this work and the round-1 claim was false
as stated.

This branch's own files ARE clean (`ruff check scripts/agent-harness/oracle_on_stall.py
scripts/agent-harness/test_oracle_on_stall.py` → all checks passed). Not fixed here:
it is another in-flight item's file and deleting a line from it would be editing
someone else's worktree subject mid-flight. One-line fix for whoever owns it.

## F10 — debris left by the merge-protocol drill: the ref `drill/verify-d`

Deliberately NOT deleted. It is `verify-merge-protocol.ps1`'s base ref, that drill
force-deletes it in its own preamble, and another session may be mid-run on it right
now — deleting a shared ref out from under a running drill is the exact failure F7(2)
and F8 are about. Recorded so the next person knows where it came from.
