# U4 round 8 — the evidence could not survive a clone, and two premises had gone stale

Session `wt-u4close` (branch `work/u4close`), 2026-08-31.
Base validated at **`1a6b0b8`** (`refactor/ai-stack-cleanup`) — §C.7b: a result that does not
name its base is not a result.

Everything below was produced by running the named command in this session. Where a number
comes from a *previous* round it says so.

---

## 0. The one-line answer

U4's *Validated by* column is **MET**, and its proof is now **committed** at
`documentation/evidence/dfu-u4/`. It was not, three hours ago: the round-7 comparison that
closed the column had been deleted, and the deliverable's own machine output said so while
two places in `WALKTHROUGH.md` said two different other things.

The *What* cell's second half — *"agent-org workers as harness runners and vice versa — one
profile mechanism governs both"* — is **not delivered on this line** and is not claimed to be.
§2.1 A1 states the amendment *"does not touch the Validated-by column"*, so that debt is a
*What*-cell debt, recorded rather than rounded away.

---

## 1. THE FINDING: `.gitignore` deleted U4's audit trail, and nothing could tell

`.gitignore:88-89` read:

    # quadrant comparison run artifacts (evidence for a run, not source)
    .quadrant/

Round 7 wrote its four-quadrant comparison to `.quadrant/gym-runs` and its oracle observation
to `.quadrant/gym-stall` — **inside `.claude/worktrees/wt-u4close`**, the per-session worktree
that produced them (`quadrant.cli._repo_root()` resolves to the worktree, not the main
checkout). `work/u4close` merged as `4e5608d`; the worktree was removed; both directories went
with it. Measured today:

    $ ls "D:/Open WebUI/ai-stack/.quadrant"
    runs  stall                      # gym-runs and gym-stall: gone

    $ python -m quadrant.cli report --results-dir "D:/Open WebUI/ai-stack/.quadrant/runs"
    **COMPARED 0/4**  - this comparison is INCOMPLETE
    exit 1

Every mechanism the `quadrant` package owns worked. The runs were real, the verifiers were
right, the report is honest. **The evidence simply no longer existed** — and no check
anywhere can distinguish *"this never ran"* from *"this ran and the proof was deleted"*,
because to an auditor they are the same state.

That comment is the whole defect in one clause. *"Evidence for a run, not source"* is the
sentence that justified ignoring the artifact §C.6 calls the deliverable's twin.

> **A trap for the exit code, met while measuring this.** The first `report` run I did was
> piped to `tail` and printed `EXIT=0`. The pipeline's exit code is `tail`'s. It is the same
> swallowed-exit-code family as `dfu-u4-findings.md` findings 4 and 5, arriving a third time,
> and the correct number (1) only appeared after re-running with a redirect.

### What was changed, each RED before GREEN

| change | file | red-proof |
|---|---|---|
| evidence has a committed home, and the ignore rule says what it really covers | `documentation/evidence/` (+ `README.md`, `.gitattributes` `* -text`), `.gitignore` | n/a — a location, not a mechanism. Its proof is §3 |
| `--auto` reaches the committed root | `scripts/checks/check_quadrant_evidence_reproduces.py` `DISCOVERY_ROOTS` | `test_auto_discovery_reaches_the_committed_evidence_root` — RED with the root list restored to `(".quadrant",)` |
| a record is re-runnable in another checkout | `quadrant/cli.py` (`check_template`), `check_..._reproduces.py` (`_runnable`) | `test_a_record_whose_recorded_command_died_with_its_worktree_re_derives_from_the_template`, `test_the_guards_placeholder_is_re_expanded_against_the_checking_checkout` — both RED with the template branch disabled |
| a `project` run directory can be committed at all | `quadrant/adapters.py` (`finalize_target`, `_rmtree_force`) | `test_a_finalized_project_workspace_can_be_committed` — RED with the branch removed |

Red-proofs, run by deleting the named line(s), running, restoring, running again:

    (roots + template reverted)  3 failed, 11 passed   ->  restored  14 passed
    (project finalize removed)   1 failed, 48 passed   ->  restored  49 passed

### The `-text` attribute is load-bearing, not tidiness

`guards.py unmodified` compares the **SHA-256** of each frozen file in the retained workspace
against the item's own bytes (`quadrant/guards.py:44-57`). A checkout that normalised line
endings would turn every committed record into `MODIFIED - the item forbids editing it` on any
machine whose git normalises differently from the producing one — a false NOT REPRODUCIBLE
that reads as evidence tampering. `documentation/evidence/.gitattributes` carries `* -text`
for exactly the reason `quadrant/items/.gitattributes` does.

### A defect found by the new test, one layer down

`finalize_target`'s first version removed the scratch repo with
`shutil.rmtree(dotgit, ignore_errors=True)`. It reported success and **left the directory in
place**: git marks loose objects and packs read-only, and `rmtree` on Windows fails on a
read-only file — which `ignore_errors=True` then swallowed. The test caught it because it
asserts about the FILESYSTEM afterwards rather than about the call returning. `_rmtree_force`
clears the mode bit in an `onexc`/`onerror` hook and **raises** if anything survives.

### Which is which — durable vs ephemeral

| | where | tracked | why |
|---|---|---|---|
| evidence | `documentation/evidence/<item>/…` | **yes** | a claim rests on it, and the claim outlives the session |
| working state | `.quadrant/` (`scratch/`, unclaimed result sets) | no | per-checkout, cited by nothing; deleting it loses no assertion |

The rule is not *"large things are ignored"*. It is **if a row cites it, it is committed.**

---

## 2. What the machine says NOW

All four cells re-run 2026-08-31 at venue `gym`
(`D:\Open WebUI\ai-orchestration-gym` @ `main`, identity `root:f12ba2ecd0ed02c30ce3fa32e1dbe4b8ae7bf31d`),
into the committed set, by the committed code:

    $ python -m quadrant.cli run-all --item u4-baseline \
        --results-dir documentation/evidence/dfu-u4/quadrant \
        --scratch-root .quadrant/scratch
      COMPLETED little-coder x self
      COMPLETED little-coder x project
      COMPLETED claude-code  x self
      COMPLETED claude-code  x project                                   exit 0

    $ python -m quadrant.cli report --results-dir documentation/evidence/dfu-u4/quadrant
      **COMPARED 4/4**                                                   exit 0
      | little-coder x self    | completed | 2/2 | 65.5 |
      | little-coder x project | completed | 2/2 | 65.8 |
      | claude-code  x self    | completed | 2/2 | 35.4 |
      | claude-code  x project | completed | 2/2 | 35.2 |

    $ ./scripts/agent-harness/observe-oracle-on-stall.ps1 `
        -ResultsDir documentation/evidence/dfu-u4/stall -LeaseOwner wt-u4close
      3 real dispatches of the unsatisfiable item u4-stall, all FAILED
      round 1  sig=3925e1845fc3353b  sha=97894e7a394e  PROGRESS: new failure on new code
      round 2  sig=3925e1845fc3353b  sha=f6753fb40b5a  no progress: a cycle, not a step
      round 3  sig=3925e1845fc3353b  sha=21a9c59a9e67  no progress: a cycle, not a step
      STALLED - 2 consecutive rounds with no new information
      ORACLE-ON-STALL: little-coder/local-default -> claude-code/opus, hand back
      ledger row 417aa274750da712 - APPENDED BY THIS RUN (0 rows before, 1 after)  exit 0

    $ python scripts/checks/check_quadrant_evidence_reproduces.py --auto
      7 outcome record(s) re-derived their verdict from the evidence they kept   exit 0

The stall was **observed, not constructed**: the item (`quadrant/items/u4-stall`) is
unsatisfiable by construction — two of its tests demand different outputs for the same input —
but the failure to converge is the runner's, the failure text is the item's own guards', the
three commits carry real bytes, and the DETECTOR decided it was a stall. The run refuses to
speak about any ledger row that was there before it started.

**The `coder` lease was held for the whole of it** (`lease.ps1 -Acquire -Name coder -Owner
wt-u4close`); each little-coder cell mirrors a workspace into the container, which wipes what
is there. The daemon's prior focus (`https://github.com/anthropics/skills`) is restored by the
adapter and was confirmed restored afterwards.

---

## 3. Two premises in PLAN §2.1 A1 are now FALSE — measured, not argued

A1's amendment was **correct when written**. Both of the facts it rests on were falsified by
work that landed *after* it: `dispatch.ps1` (merged `211febc`) and `oracle_on_stall.py`
(`5dbf05b`). I searched for these across `.ps1 .psm1 .py .ts .mjs .js .json .md` and the
skill/hook directories, untruncated (`head_limit 0`), because the repeated failure of this
effort is a search whose alphabet was too narrow.

### 3a. `Resolve-RoleTarget` has executable callers

    scripts/agent-harness/dispatch.ps1:88          $t = Resolve-RoleTarget -Role $Role ...
    scripts/agent-harness/verify-dispatch.ps1      reaches it via Invoke-HarnessTask, x12
    scripts/agent-harness/test_harness_config.py:292
    .claude/skills/merge-queue/SKILL.md:133        (a doc — correctly excluded)

and its Python twin is called from the **pipeline**:

    scripts/agent-harness/oracle_on_stall.py:223   worker = cfg.resolve_role("worker", ...)
    scripts/agent-harness/queue.ps1:219            Invoke-OracleOnStall — run on EVERY -Fail

### 3b. The runner `status` field is read, and it is decision-bearing

`quadrant/matrix.py:175` — `comparable = r["status"] in comparable_statuses`. Flipped and
measured:

    little-coder.status=unproven   -> comparable=True
    little-coder.status=self-test  -> comparable=False
       "runner status 'self-test' is not comparable - it is scaffolding"

A non-comparable cell is kept out of the decision table by `report.render`. One word in
`harness.config.json` decides whether a quadrant's number may be used.

### 3c. So what IS true — the claim at its real width

Selecting a profile changes what the machine does, measured:

    profile local-work-cloud-review -> escalate  little-coder/local-default -> claude-code/opus
    profile all-local               -> escalate  little-coder/local-default -> claude-code/opus
    profile all-cloud               -> no-oracle-above
                                       "the worker already runs on 'claude-code'"

`all-cloud` produces **no escalation at all**. That is the profile mechanism governing a real
decision, and the committed ledger row carries `"profile": "local-work-cloud-review"` as
proof it ran through it.

What it does **not** govern is the pipeline's own execution. `queue.ps1` records an item's
`profile` and never starts a runner; which agent picks up a worker or tester role is decided
by a human or the orchestrator, not by the profile. And the **agent-org direction does not
exist on this line at all**.

### Why option (b) — correct the claim — and not (a), build a consumer

The brief offered: give the mechanism a pipeline consumer, or state the narrower reality.
I took **(b)**, and the reason is a fact rather than a preference:

- The default profile `all-cloud` assigns `worker → claude-code`, and `dispatch.ps1:394`
  **throws** for a runner kind it cannot start — it implements `docker-exec`/`http` to
  little-coder and nothing else. A `queue.ps1` dispatch consumer would therefore refuse
  under the shipped default configuration. Making *"selecting a profile changes what runs"*
  true would require porting a second runner kind (spawning `claude` CLI child processes)
  into the dispatcher — which is the quadrant adapter's job, duplicated.
- Dispatching from `queue.ps1` is a **live-plane mutation** (it wipes little-coder's
  workspace) on the queue every agent in this workspace shares.
- `dfu-u4-findings.md` already orders this work: delivery first (the local runner cannot push
  to this repo — `LC_DEPLOY_TOKEN` gets 403), *then* a pipeline consumer. That ordering has
  not changed.

That is the brief's own condition for (b) — *"if a real consumer cannot be built without
widening scope"* — met on measured grounds. The claim is corrected in both directions: the
mechanism is stronger than A1 says in the harness, and still absent in agent-org.

---

## 4. `check-runner-endpoints.ps1` — verified false, and it ships nowhere

Re-verified in this session (PowerShell **5.1.26100.8875**), under the script's own preamble:

    Set-StrictMode -Version Latest; $ErrorActionPreference = "Stop"; $Error.Clear()
    $u = [Uri]'not a url at all'
    IsAbsoluteUri = False ; $null -eq $u.Port = True ; $u.Host = '' ; $Error.Count = 0

`.Port` does not throw. The .NET getter raises `InvalidOperationException` and PowerShell
swallows it: no throw, no error record, no crash. `DECISIONS.md:636` and `WALKTHROUGH.md:130`
were right to call the sentence false.

**But the file is on no branch.** `git ls-files` has no `check-runner-endpoints.ps1` anywhere;
`git log --all -- scripts/agent-harness/check-runner-endpoints.ps1` returns exactly two commits,
both on `origin/work/u4bidir` (`aabb781`, `ec4ed8d`), and **neither contains the word
`relative` or `throw` anywhere near `.Port`**. The round-3 revision that introduced the
sentence at lines 105-106 was never pushed, and the worktree holding it is gone.

So there is no code to fix and no surrounding logic to correct. For completeness, the logic in
the version that *does* exist does not depend on the false premise either:
`$port = if ($u.Port -gt 0) { $u.Port } else { 80 }` — with `$u.Port` null, `$null -gt 0` is
`$false`, so it takes 80 and connects to an empty `$u.Host`, which faults and returns
`ok = $false`. Not a crash; a misattributed reason.

What was actually wrong is the **audit surface**: `WALKTHROUGH.md` listed the sentence as a
live known-open defect of the deliverable, when the deliverable does not contain it. Corrected
there.

---

## 5. Branches — salvaged and abandoned

| branch | state | disposition |
|---|---|---|
| `work/dfu-u4` | **ancestor of `1a6b0b8`** (merged `211febc`) | nothing to salvage; `dispatch.ps1` is on the line and was re-exercised |
| `work/u4quad` | **ancestor of `1a6b0b8`** (merged `88d5035`) | ditto; the `quadrant` package is what this round ran |
| `work/u4oracle` | **ancestor of `1a6b0b8`** (merged `5dbf05b`) | ditto; `oracle_on_stall.py` produced the ledger row |
| `work/u4bidir` | unmerged, ~87 commits behind | **ABANDONED** (operator direction) |

`WALKTHROUGH.md` said all four were unmerged. Three of them were merged on 2026-08-30;
`git merge-base --is-ancestor <branch> HEAD` succeeds for each. That line was stale and is
corrected.

**What abandoning `u4bidir` costs**, named rather than glossed: the agent-org `RunnerRegistry`
(`agent-org/agent-bridge/app/modules/runners.py`, 398 lines), its 481-line test module, the
scheduler/orchestrator wiring, `check-runner-endpoints.ps1`, `verify-runner-endpoint-check.ps1`
and a 618-line findings note — ~2,400 lines. Independent reasons it does not land:

1. refuted 2/2, both confirmed by the orchestrator reading the source (a reachability check
   that could not fail for the container-DNS rows it validated; a registry fallback that
   turned compose's documented `${AO_WORKER_INSTANCE_URLS:-}` disable path into two enabled
   workers);
2. ~87 commits behind the work line, therefore **UNVALIDATED under §C.7b** regardless of what
   passed on it;
3. it carries a false justification sentence *in code*.

Its absence is precisely why *"one profile mechanism governs both"* stays false in the
agent-org direction, and the walkthrough now says so instead of implying the debt is closed.

---

## 6. Does U4 close?

**The column is met and its evidence is committed and re-derivable.** What is still owed is
not evidence — it is *independence*. `WALKTHROUGH.md`'s own opening rule:

> A row saying DONE means its §2 *Validated by* column is satisfied by an executable check
> **that someone who did not build it re-ran.**

I ran round 8. Under the merge protocol I do not test or merge my own work, and a subagent I
spawned would not be an independent party either. So the honest state is:

**U4: COLUMN MET, evidence committed, awaiting an independent re-run.** Not DONE by this
session's own hand, and not PARKED — the reason it was parked (no durable evidence, an
unobserved oracle) no longer holds.

The three commands a verifier needs are in §2 and each is a bare invocation with no pipe.
The one that would DISPROVE the claim: clone this branch into a fresh directory and run
`python scripts/checks/check_quadrant_evidence_reproduces.py --auto` there. If the committed
evidence does not re-derive in a tree that has never run anything, this round has failed at
the only thing it set out to do.

---

## 7. Findings recorded but NOT acted on

- **`WALKTHROUGH.md` line 33 vs its own §U3 section disagree, the same way U4's did.** The
  summary table says U3 is `DISCHARGED (closing with U4)`; the §U3 section header still says
  `PARKED` and its table still says the seeded-regression half is `NOT MET — the run was
  local, not in the arena`. Commit `844c02d` (*"U3 GYM RUN: a seeded regression caught by a
  check born from a tester finding"*) is on the line. Not touched here: U3 is not this
  session's item, and the correct direction needs the gym drill re-run, which is a second
  arena run. Recorded so the next reader does not have to rediscover it.
- **`scripts/agent-harness/u3_evidence_regression_gym.py` reads a results set.** Now that the
  U4 evidence lives at `documentation/evidence/dfu-u4/quadrant`, whoever re-runs the U3 drill
  should point it there rather than at the ignored `.quadrant/`, or it will find nothing for
  the same reason §1 describes.
- **A correction to this note, caught by running the command instead of citing an earlier
  round.** I first wrote that the repo-wide lint gate is still red on the pre-existing F401
  at `agent-org/agent-bridge/tests/test_org_drill.py:31` (`dfu-u4-findings.md` finding 10,
  2026-08-30). **Measured on this branch, ruff 0.16.4:** `ruff check .` → *All checks
  passed!*, **exit 0**; that file was last touched by `d54299f`. The repo gate is GREEN and
  the earlier finding is closed. Recorded rather than silently deleted, because the sentence
  I nearly shipped was inherited from a findings note rather than from a command — the exact
  habit §0 of `dfu-u4-findings.md` says every defect in this effort shares.

---

## DECISIONS entries to append (this branch does not edit DECISIONS.md)

```
## 2026-08-31 · U4 round 8 · class 2
DECISION: Evidence a phase's column is closed on is COMMITTED, under
          `documentation/evidence/<item>/`, and `.gitignore`'s `.quadrant/` rule
          is re-scoped to WORKING state with its comment corrected. It read
          "run artifacts (evidence for a run, not source)" - and that sentence is
          how U4's audit trail was deleted: round 7's COMPARED 4/4 comparison and
          its oracle ledger lived in `.quadrant/gym-runs` and `.quadrant/gym-stall`
          inside a per-session worktree, were ignored by that rule, and died with
          the worktree at merge. `quadrant.cli report` then answered COMPARED 0/4,
          exit 1, under a walkthrough row still claiming 4/4.
          Three mechanisms make the committed set usable from a clone, each proved
          RED first: `--auto` discovery reaches `documentation/evidence/`; run
          records carry `acceptance[*].check_template` (the unexpanded criterion)
          beside the machine-bound `check`, and the checker re-expands it against
          ITS checkout; a `target: project` run's scratch `.git` is removed at
          finalize, because a nested repository makes the run directory
          uncommittable. A `-text` .gitattributes protects the byte comparison
          `guards.py unmodified` performs.
CITED:    §C.6 - the audit trail is the deliverable's twin. §C.2 class 2 - the most
          reversible option (a location plus three narrow mechanisms, no change to
          what any cell measures).
REVERT:   delete `documentation/evidence/`, restore the one-line `.quadrant/`
          comment, revert the three code hunks. Seven tests go red, and the next
          phase's evidence becomes undeletable-by-accident again only by hand.

## 2026-08-31 · U4 round 8 · CORRECTION to §2.1 A1
DECISION: A1's two supporting facts are now FALSE and the walkthrough says so.
          "Resolve-RoleTarget has zero executable callers" - `dispatch.ps1:88`
          calls it, and its Python twin `config.resolve_role` is called by
          `oracle_on_stall.py:223`, which `queue.ps1` runs on every `-Fail`.
          "The runner `status` field is read nowhere" - `quadrant/matrix.py:175`
          gates comparability on it; flipping little-coder's status to `self-test`
          makes its cells non-comparable and removes them from the decision table.
          Both were TRUE when A1 was written and were falsified by `dispatch.ps1`
          (`211febc`) and `oracle_on_stall.py` (`5dbf05b`) landing after it.
          The AMENDMENT stands - the task is still to build the mechanism and
          prove it dispatches - but its evidence paragraph now describes a world
          that no longer exists, and A1 explicitly does not touch the Validated-by
          column, so nothing about U4's closure turns on it.
          The accurate statement: the profile mechanism governs ONE direction
          partially (dispatch target, stall-escalation target, quadrant
          comparability, operator display - `all-cloud` yields `no-oracle-above`
          where `all-local` yields an escalation) and is ABSENT in the agent-org
          direction, so "one profile mechanism governs both" remains false there.
CITED:    §C.7 - the audit trail must be true, including where it was true
          yesterday. §C.8 - a column is not amended to manufacture a pass, and
          this changes no column.
REVERT:   n/a - a record correction. PLAN.md is untouched by this branch.

## 2026-08-31 · U4 round 8 · class 2
DECISION: `work/u4bidir` is ABANDONED, on operator direction, and the ~2,400 lines
          it carries are named in `documentation/notes/u4-round8-evidence-durability.md`
          §5 rather than quietly dropped. Independent reasons: refuted 2/2 on
          orchestrator-confirmed defects; ~87 commits behind the work line and so
          UNVALIDATED under §C.7b whatever passed on it; and it carries a false
          justification sentence in code. `work/dfu-u4`, `work/u4quad` and
          `work/u4oracle` needed no salvage - all three are ancestors of `1a6b0b8`.
CITED:    §C.7b; the operator's direction.
REVERT:   the branch still exists on `origin`; nothing was deleted.

## 2026-08-31 · U4 round 8 · STATUS
STATUS:   U4 = COLUMN MET, EVIDENCE COMMITTED, AWAITING AN INDEPENDENT RE-RUN.
          Both halves of §2's column are satisfied at venue `gym`: COMPARED 4/4
          exit 0 over `documentation/evidence/dfu-u4/quadrant`, and an oracle that
          fired on a stall that HAPPENED (ledger row 417aa274750da712, appended by
          the run that reports it, 0 rows before / 1 after). Seven records
          re-derive their verdicts from the evidence they kept.
          NOT claimed: DONE. This file's own rule is that a row says DONE when a
          verifier who did not build it re-ran the column, and round 8 was run by
          the session that produced it.
          STILL OPEN, and a *What*-cell debt rather than a column debt: "agent-org
          workers as harness runners and vice versa - one profile mechanism
          governs both" is false in the agent-org direction, because the branch
          that built it is abandoned.
REVERT:   n/a - a status record.
```
