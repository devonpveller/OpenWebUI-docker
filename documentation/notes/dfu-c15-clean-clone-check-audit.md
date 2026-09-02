# Clean-clone audit of the DFU phase checks - 2026-09-01 (item dfuc15, clauses 1 and 5)

**What was done.** Every phase in
`documentation/implementation-guide/dark-factory-unification/WALKTHROUGH.md` was given a
`**How to run:**` marker naming a check that validates its section 2 *Validated by* column -
**and only where that check actually exits 0 from a clean clone**. Six phases got one (U0, U1,
U2, U4, U5, U8). Three did not (U3, U6, U7), and this file holds the measurements that decided
that, together with the defects they turned up in checks that belong to other items.

**How everything below was measured.** A clone of `refactor/ai-stack-cleanup` at `fba111d`,
built the way `dfu-done.ps1` builds its sandbox (`git clone --shared --single-branch --branch
<line>`, `core.longpaths true` set INSIDE the clone, submodules from the local mirror),
`git status --porcelain` asserted empty before anything ran, every command issued through
`cmd.exe /c` from the clone root - the same interpreter and working directory clauses 1 and 5
use. The live 81-container stack was up; nothing here wrote to `openbrain-db` or to the
personal plane.

## Fixed here

**F1. U4's committed evidence was inadmissible from any checkout but the one that made it.**
From the clean clone, `python scripts/agent-harness/quadrant/cli.py report --results-dir
documentation/evidence/dfu-u4/quadrant` answered **COMPARED 0/4, exit 1** and
`python scripts/checks/check_quadrant_evidence_reproduces.py --auto` answered **7 NOT
REPRODUCIBLE, exit 1**, every one of them "evidence.workspace does not exist on disk" naming an
absolute path inside the removed worktree `wt-u4close`. All 48 files under
`documentation/evidence/dfu-u4/` are tracked and present. `quadrant/record.py:admit` resolved
`evidence.*` as the absolute path the producing worktree wrote, and that worktree was removed
when the branch landed - so round 9's "clean clone" greens held only while the author's
directory still existed. Round 8 made the evidence durable and left the gate that reads it
pointed at a machine.

Fixed in `quadrant/record.py` (plus `quadrant/cli.py`, `quadrant/prove_guards.py`,
`scripts/checks/check_quadrant_evidence_reproduces.py`): a record naming its evidence inside its
own run directory is checked BESIDE THE RECORD and the recorded absolute path is then not
consulted - the rule `check_quadrant_evidence_reproduces.py` already applied to the workspace it
re-runs in, now applied at admission too. RED to GREEN to RED to GREEN proved in the clone
(0/4 and 7-not-reproducible; then 4/4 and 7-re-derived; both exit 1 again with
`documentation/evidence/dfu-u4/` moved away; both exit 0 on restore).
In the clean clone: `quadrant.prove_guards` 25/25 bite, `ruff check .` clean, and
`pytest scripts/agent-harness -q` 295 passed / 2 skipped / 1 failed - the pre-existing
`test_the_check_is_banked_in_the_registry_with_the_form_that_runs_anywhere`, which fails
identically at base `fba111d` because the durable-check registry lives inside `.git`. The same
suite is 298 passed / 0 failed in a worktree.
Detail in WALKTHROUGH.md section U4, "Round 10".

## Found and NOT fixed - each belongs to another item

**F2. `scripts/checks/recall-falsifiability-drill.py` is vacuously green without the
agent-bridge interpreter.** In the clean clone it exits **0** and prints
`ALL MUTATIONS RED - every guard can fail`, while **eleven of its twelve "RED" lines carry
`E   ModuleNotFoundError: No module named 'sqlalchemy'`** - only the twelfth, the Deno
mutation, needed no Python and is a real red. It decides a mutation was caught from
`returncode != 0` and never asserts that the UNMUTATED tree is green, so any interpreter that
cannot import the package under test satisfies every Python mutation at once. It was one of the two
commands in U6's `How to run` line, so this document was shipping a check that is green while
checking nothing - the class clause 5 exists to catch. The fix is a baseline assertion (run the
suite unmutated first, require green, refuse the run otherwise), not a different marker.
U6's owner.

**F3. `dark` mode cannot auto-pass a gate on this work line, and `drill-dark-factory.ps1`
cannot be green in any checkout.** The drill is exit 1 with 48 failed assertions in the clone,
and they share one cause, measured on the work line itself (NOT in a clone):

    powershell -File scripts/agent-harness/andon.ps1 -Evaluate -Only policy-declared-unread
    ANDON BOARD: RAISED
      [fire] policy-declared-unread -> declared policy nothing reads: pipeline.convergence
                                                                            exit 6

`scripts/agent-harness/harness.config.json` declares `pipeline.convergence` and nothing reads
it. The andon condition that exists to catch a dead knob therefore fires against the SHIPPED
config, every drill fixture inherits that config, and a raised board is exit 6 by design - so
the second half of U6's column (*"one that hits none lands with a complete audit trail"*) cannot
be demonstrated, and no unattended run can auto-pass a gate. Whether the key is dead or its
reader is misnamed is a config decision, not a guess to make under the C.10 freeze. U6's owner.

**F4. `scripts/agent-harness/verify-merge-protocol.ps1` cannot run in `dfu-done.ps1`'s
sandbox.** Exit 1, `40/66 checks passed`, first line `fatal: not a valid object name:
'development'`. It cuts its scratch line with `git branch drill/verify-d development` and the
sandbox is `--single-branch`, so 26 assertions collapse from that one failure. A full
`git clone` does not help, measured: in an all-branches clone `refs/remotes/origin/development`
is present and `git rev-parse development` still answers `fatal: ambiguous argument
'development': unknown revision`, because clone creates a local branch only for the remote's
HEAD and `rev-parse` does not search `refs/remotes/origin/`. The base is hard-coded with no
parameter, so no INVOCATION fixes it. Harness owner.

**F5. A `Gym:` column is unreachable from a disposable clone by construction.**
`scripts/agent-harness/u3_evidence_regression_gym.py` exits **2** with
`VENUE REFUSED: venue 'gym' resolves to ... which is not a directory`, because
`harness.config.json` configures the venue as `../ai-orchestration-gym` - relative to the
checkout - and `dfu-done.ps1` clones into `%TEMP%`. `AI_STACK_GYM_REPO` can override it, but
only with an absolute path to one machine, which is the shape this round exists to stop
repeating. U3/U4's owner: either the venue resolves through an env var CI and the sandbox can
set, or the "Gym:" columns are acknowledged as not clean-clone re-runnable in the plan rather
than in a walkthrough footnote.

**F6. `scripts/checks/test-quartz4-offline.ps1 -Phase unit` is red.** Exit 1,
`7 CHECK(S) FAILED`. The schema half passes (29 migrations derived from compose,
`agent_memory_tables(8)`, the trigger, both functions, the wiki links GIN index); every failure
is in the section that executes the agent-memory module's own SQL against the fresh volume, and
the first is an INSERT that omits a column C.9 H3 made `NOT NULL` (`null value in null
constraint`). The harness's fixture SQL was not updated when the constraint landed. Measured
only in the clone: that section builds its database from this repository's own migration chain
and runs SQL from this repository's own module, so it is hard to see how a clone could matter,
but that is an inference and is not recorded as a measurement. It is therefore not U1's marker
- `smoke-agent-memory.ps1` is, and it is green - but somebody owns this.

**F7. `dfu-done.ps1` clause 1 cannot reach `met` while any section 2 column names no runnable
artifact, and 7 of the 8 do not.** For a phase whose column names no `.ps1`/`.py`/... file,
`Test-Clause1` files a NAMED MANUAL CHECK (`section-2-column-mapping-<phase>`) **and** adds a
probe whose verdict is `indeterminate` unconditionally (`dfu-done.ps1:2092-2097`); the recorded
manual result is never consulted by that probe, and `Resolve-ClauseVerdict` turns any
indeterminate probe into `unevaluated`. So recording a pass in `dfu-done-manual.json` cannot
lift it, and only U8's column names an artifact today. Adding the six new markers therefore adds
six more of these probes: clause 1's per-phase `*-validated-by` indeterminates are gone, and
clause 1 still reports `unevaluated` for this reason. Closing it is either a `dfu-done.ps1`
change (let a recorded manual pass satisfy the probe) or a PLAN.md change (make the columns name
their checks) - both outside this item, and the second is what C.8 clause 1's own text points
at.

**F8. A cost, not a defect.** Clauses 1 and 5 each re-run every marker, and the markers are now
real drills: U8's is a measured 13m24s, U5's 2m30s, and U1's builds a throwaway image the first
time. A full `dfu-done.ps1` now spends the better part of an hour in its sandbox. That is what re-running the columns
from a clean checkout costs; it is recorded so nobody trims it by accident.

## Green, with their exit codes

| phase | command | measured |
|---|---|---|
| U0 | `python -m pytest scripts/claude-sessions-bridge/test_inbox.py -q` | exit 0, `20 passed in 10.82s` |
| U1 | `scripts/checks/smoke-agent-memory.ps1` | exit 0, 23 checks, `ALL AGENT-MEMORY SMOKE CHECKS PASSED` (4 min cold, 20s with the `:smoke` image cached) |
| U2 | `python -m pytest scripts/agent-harness/test_harness_config.py scripts/agent-harness/test_anchor_schema.py -q` | exit 0, `64 passed, 1 skipped` |
| U4 | `quadrant/cli.py report --results-dir documentation/evidence/dfu-u4/quadrant` | exit 0, `COMPARED 4/4` (after F1) |
| U4 | `scripts/checks/check_quadrant_evidence_reproduces.py --auto` | exit 0, 7 re-derived, 0 skipped (after F1) |
| U5 | `scripts/checks/drill-personal-plane-exclusion.ps1 -AcceptDispositionedGaps` | exit 0 in 2m30s, `CONTAINMENT GREEN, 25 gap(s), ALL DISPOSITIONED (106 checks passed, 0 failed)` |
| U8 | `scripts/checks/verify-dfu-done.ps1 -Target scripts/checks/dfu-done.ps1` | exit 0 in 13m24s, `DRILL GREEN - 216 assertions, 0 failed`; the clone was still clean afterwards |

Also measured green and deliberately NOT recorded as a phase's check, because they prove the
drill's own machinery rather than U5's column: `drill-personal-plane-exclusion.ps1
-SelfTestLedger` and `-SelfTestVacuity`, exit 0, about a second each.

## What this does NOT claim

The six markers re-run their phase's column from a clean clone. They do not make any phase
DONE: U3 and U5 stay parked, U6's clauses 1-3 stay in flight, U7 has not started, and U8's H4
and H5 remain blocked on the operator. Clause 1 remains `unevaluated` for the reason in F7, and
clause 5 remains `unevaluated` for as long as U7 exists as a section without a runnable check -
which is the correct report, not a gap to close.
