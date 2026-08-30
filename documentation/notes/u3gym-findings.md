# Findings — U3's seeded-regression run (`u3gym`)

Item: discharge U3's *Validated by* column — "Gym: a seeded regression must be caught by
a check born from a *tester* finding in a prior round (gym-007's shape, new source);
drills green in both systems".

**Status: U3 PARKS on the `Gym:` clause.** The seeded-regression half is met and is proven
by executable drills; no `ai-orchestration-gym` arena run was performed, and one is not
performable under the standing constraints. Reason, and what would meet it, in §P below.
Per PLAN §C.7 a phase that cannot satisfy its column parks with a written reason rather
than merging on a narrowed reading of it.

**Round 2 (2026-08-30).** Round 1 was refuted 2/2. The verifiers confirmed the drills are
not fake — they survived a five-way vacuity battery, the REAL/SIMULATED/NOT RUN ledgers
were accurate, and `LocalCheckHarness` really executes the gate's own
`git fetch && checkout -f FETCH_HEAD && <check>` against a real remote. Three things
refuted it anyway; all three are addressed below, two by code and one by parking honestly.

**Branch-name note for the merge record.** The brief for this item said `-Id u4gym`; the
worktree was created as `u3gym`, so the branch is **`work/u3gym`** in worktree
`wt-u3gym`. Both round-1 verifiers reported "the branch I was sent to does not exist" —
that was this, not a missing push. Not renamed now (a rename mid-review costs more
confusion than it removes); recorded so the merge record is accurate.

---

## R1 — the counterfactual claim was FALSE. Corrected, and now MEASURED

**What was claimed.** `check_stack_services_paths.py`'s docstring said "WHAT WOULD HAVE
CAUGHT IT OTHERWISE: **nothing**", and `seeded_regression_drill.py` printed section 5 as
"the counterfactual: nothing that already existed catches either seed".

**What disproves it.** Reproduced independently before changing anything, by building a
sandbox from the inventory's own file list and running the PRE-EXISTING
`scripts/checks/check-watchdog-repair-targets.ps1 -SkipDocker` in it:

    clean sandbox                                  -> exit 0
    SEED A (search/docker-compose.yml renamed,
            scripts/lib/stack-services.json left
            pointing at the old path)              -> exit 1, three [FAIL] lines:
      'search-gateway' resolves to 'search/docker-compose.yml', which does not exist on disk
      'search-redis'   ... same
      'searxng'        ... same

`check-watchdog-repair-targets.ps1:193` does `Test-Path` on `projects[*].file`. The claim
was false, and a command shows it.

**How the false claim was reached, because that is the reusable part.** The grep it rested
on was true: `check-project-configs.ps1` really does read `$inv.planes` only, and really
never opens `$inv.projects`. The error was generalising from *the verifier the tester
named* to *everything that exists* without enumerating the other readers — the same
stopping-the-read-early class this effort keeps finding, committed by the author of the
file whose whole point is that findings must be executable. Section 0 A6 landing on its own
advocate.

**The honest, narrower claim — measured on every run, not asserted.** The drill now RUNS
the pre-existing check against four seeds and asserts the matrix (sections 3–6 of
`seeded_regression_drill.py`; each cell is an exit code, both directions):

| seed | what it is | pre-existing `check-watchdog-repair-targets.ps1` | the banked check |
|---|---|---|---|
| A | rename, inventory untouched | **CAUGHT** (exit 1) | caught |
| B | half-fixed rename: `file` updated, the `-f` inside `compose` not | missed (exit 0) | caught |
| C | rename in a project no watchdog-managed container points at (`agent-org`) | missed (exit 0) | caught |
| D | `env_file` points at a file that is not on disk | missed (exit 0) | caught |

So the new check's value is **B, C and D** — not A. Why each pre-existing reader stops
where it does, verified by reading them:

- `check-project-configs.ps1` (the pre-commit hook's drift verifier, lines 60–102) builds
  `$known[container] = project` from `planes[]` and compares it to rendered
  `container_name` values. It reads the CONTAINER rows, and only when a `.yml` is staged.
  It catches **none** of the four seeds.
- `check-watchdog-repair-targets.ps1` reaches a project only by resolving a container the
  watchdog claims to self-heal — 24 of them, spanning `coder`, `frontend`, `inference`,
  `memory`, `open-brain`, `search`. `agent-org` has no row in `planes[]`, so seed C is
  invisible to it. It never reads `env_file`, and never compares `file` to the `-f`
  argument inside `compose`, so B and D pass it. It is also **not** wired into pre-commit
  (its own header says so), so even for seed A nothing catches the commit that makes the
  mistake — it is caught the next time someone runs the script.

A check born from a tester finding is still the U3 point. It just is not the only thing
standing between the repo and that regression, and the artifact no longer says it is.

**RED before GREEN**, both directions:

| sabotage | result | restored |
|---|---|---|
| re-assert the OLD claim (seed A missed by the pre-existing check) | 27/28, exit 1 | 28/28, exit 0 |
| `check_stack_services_paths.py` reports zero problems unconditionally | 23/28, exit 1 | 28/28, exit 0 |

Determinism: 5 consecutive runs, 28/28 each.

---

## R2 — the cited merge-protocol drill is flaky. Root-caused, fixed, and WITHDRAWN as evidence

**The measurement.** `verify-merge-protocol.ps1`, cited at 66/66, launched eight times in a
row; seven completed before the burst's wall-clock cap cut the eighth off mid-run:

    66/66  66/66  63/66  59/66  39/66  34/66  40/66

Two clean of seven completed. It is two defects, not one, and neither is "the machine was
busy".

**Defect 1 — no mutual exclusion.** The drill creates and force-deletes FIXED global names
(`drill/verify-d`, `work/drilla`, `work/drillb`, and three worktree paths) in the
OPERATOR'S checkout, and its preamble deletes them unconditionally so a crashed run cannot
wedge the next one. That preamble is, verbatim, a second copy destroying the first: run 3
died with `fatal: Needed a single revision` on `drill/verify-d` seconds after run 3 had
itself created it. **Observed, not inferred** — a concurrent `verify-merge-protocol.ps1`
(pid 137560, a different session's, started 12:18:20) was caught in
`Get-CimInstance Win32_Process` mid-burst, and another (pid 82748) was running at 12:38
while this note was being written. Other sessions run this drill routinely; the harness is
built for parallel agents, so this is the normal case, not an unlucky one.

`lease-names.conf` says "git state needs no lease (the worktree is the isolation)". That
is right for every other caller in the toolkit and wrong for this one: there is no worktree
to isolate a run whose job is to CREATE worktrees.

**Defect 2 — `git -C` ascends, so a failed provision aims the drill at the operator.**
When provisioning half-fails, `$wtA`/`$wtB` are plain directories inside the repo rather
than worktree roots. Git does not treat that as an error; it walks UP and finds the
enclosing repository, which for a drill that has done `Set-Location $repo` is the MAIN
CHECKOUT. Demonstrated directly:

    $ mkdir .claude/worktrees/probe-empty
    $ git -C .claude/worktrees/probe-empty rev-parse --show-toplevel
    D:/Open WebUI/ai-stack/.claude/worktrees/wt-u3gym        # the enclosing checkout

So `Invoke-DrillGit -C $wtB rebase drill/verify-d` rebased the operator's tree. The main
checkout was found afterwards holding `.git/rebase-merge` with
`head-name refs/heads/refactor/ai-stack-cleanup`, and its reflog carries a complete
`rebase (start) / rebase (pick) / rebase (abort)` cycle from an earlier collision. The
drill's own header promises "the operator's checkout is never switched (a bridge turn
could land mid-switch)" — falsified by its own runs.

**INCIDENT, and a gap in this session's authority.** The stray rebase was left by this
session's own burst - the wedged `.git/rebase-merge` was observed at ~12:15, before the
other session's run (pid 137560) started at 12:18:20, so the timeline is not ambiguous. `git rebase --abort` in the main checkout was **denied by the
permission classifier** — twice, through Bash and PowerShell — so it could not be
repaired from here. It has since cleared on its own (main checkout is back on
`refactor/ai-stack-cleanup` at `98cf02e`, no `.git/rebase-merge`), almost certainly by
another agent's drill preamble. **`wt-drillb`, `work/drillb` and `drill/verify-d` are
present as of 12:38 and belong to a drill running RIGHT NOW (pid 82748) — do not clean
them up.** Flagging the authority gap rather than the outcome: a session able to break the
operator's checkout with an approved action, but not able to restore it, is a bad pairing.

**The fix, both halves, in the house pattern.**

1. `verify-merge-protocol.ps1` acquires the lease `merge-protocol-drill` (added to
   `lease-names.conf` under a new "coordination points that are not planes" section)
   before anything else, and releases it at the summary. Blocked → exit 3 with a reason,
   which is lease.ps1's existing "held, WAIT" code. A crashed run does not strand it: the
   TTL expires it and `-Takeover` reclaims EXPIRED leases only, so the recovery path cannot
   jump a live run. New `-LockProbe` switch takes the decision, prints it, and exits
   without touching anything.
   Exit codes are lease.ps1's own, passed through — `3` another copy holds it (WAIT),
   `2` the harness module is OFF, `1` a check failed or a worktree path is not a worktree
   root. A drill that said "another copy is running" when the real cause was a disabled
   module would send the reader hunting a process that does not exist; the two paths print
   different text and exit differently. Consequence worth naming: with the harness disabled
   the drill now refuses instead of running. That is consistent with MODULE.md ("off must be
   inert and say so") for a drill whose subject is the harness, but it IS a behaviour change.
2. `Test-IsWorktreeRoot` added to `git-io.ps1` (the topology-facts module, where it
   belongs — it is a fact about git, not a policy). The drill asserts it for `$wtA`/`$wtB`
   immediately after provisioning and ABORTS if either is not a worktree root. A wrong
   answer from the drill is recoverable; a rebase in the operator's checkout is the thing
   the toolkit exists to prevent.

**The repro:** `scripts/agent-harness/test_drill_single_flight.py`, 14 checks, run
directly. It proves ORDER, not just refusal — exit 3 would also be produced by a script
that refused *after* its preamble had already deleted the other run's branches, which is
the actual failure mode. The preamble's last act prints `development before: <sha> |
operator checkout on: <branch>`; the test asserts that string is ABSENT from a blocked
run. Deliberately not a grep for `lease.ps1` in the source: a source string proves the line
was written, never that it runs first.

    sabotage: the drill ignores a held lease   -> 12/14, exit 1
              (fails "exits 3, not 0" and "says WHY, naming the lease")
    sabotage: Test-IsWorktreeRoot always true  -> 13/14, exit 1
              (fails "says NOT-ROOT for it", detail: ROOT)
    restored                                   -> 14/14, exit 0
    determinism                                -> 8 consecutive runs, 14/14 each; 3 more
                                                  after the exit-code follow-up

**I nearly shipped a flaky check while fixing a flaky check.** The repro's first version
snapshotted ALL of `refs/heads` and every worktree around the refused run, and failed 1 run
in 6 because other agents were committing on their own branches at the time. It is now
scoped to the drill's own three refs and three worktree paths. Recorded because it is the
same error one layer up: a guard that goes red when a neighbour works is a guard nobody
keeps.

**What is NOT claimed.** `verify-merge-protocol.ps1` has **not** been re-run to a clean
8/8 burst, and it is therefore **withdrawn from U3's evidence** rather than re-cited at a
better number. Two reasons, both about not making things worse:

- another session's copy was executing while this was written, and that copy does NOT have
  the lease (it predates this branch), so a burst now would measure their collisions;
- the lease only excludes copies that HAVE it. **Until this branch merges, the guard is
  one-sided.** That is a merge-order consequence worth stating rather than a caveat to
  bury: the drill becomes single-flight when every checkout that runs it carries the fix.

"Drills green in both systems" is therefore carried by the four drills that ARE
deterministic here (§V), not by this one.

---

## V — the ledger, re-measured this round

| drill | system | result | runs |
|---|---|---|---|
| `scripts/agent-harness/seeded_regression_drill.py` | harness | 28/28 | 5/5 identical |
| `scripts/agent-harness/test_drill_single_flight.py` | harness | 14/14 | 8/8 identical |
| `agent-org/agent-bridge/tests/test_org_drill.py` | agent-org | 9 passed | 6/6 identical |
| `agent-org/.../tests/test_corpus_seeded_regression.py` | agent-org | 3 passed | 6/6 + 3/3 after the change |
| `scripts/agent-harness/verify-merge-protocol.ps1` | harness | **WITHDRAWN** — 2 clean in 8 | see R2 |

Commands, exactly as run (the venv lives in the MAIN checkout, not in this worktree —
round 1's note said "from this worktree" without saying which interpreter, which is
unreproducible):

    python scripts/agent-harness/seeded_regression_drill.py
    python scripts/agent-harness/test_drill_single_flight.py
    cd agent-org/agent-bridge
    "D:/Open WebUI/ai-stack/agent-org/agent-bridge/.venv/Scripts/python.exe" -m pytest -q tests/test_org_drill.py
    "D:/Open WebUI/ai-stack/agent-org/agent-bridge/.venv/Scripts/python.exe" -m pytest -q tests/test_corpus_seeded_regression.py

`ruff check` is clean on every file this item touches.

---

## P — the PARK, precisely: what is unmet, why, and what would meet it

**Unmet:** the `Gym:` prefix of U3's Validated-by column. Everything demonstrated here ran
locally. No `ai-orchestration-gym` scenario was executed: no org iteration, no worker turn,
no PR, no external remote mutation, no score.

**Why it was not run** — read from the gym itself, not assumed:

- `runner/gym_runner.py` mints a **GitHub App installation token inside the `agent-bridge`
  container** and drives `https://api.github.com`, and scores assertions "against GitHub
  remotes and PRs, never the org's self-reports". PLAN §C.2 class 4 excludes calling
  external services beyond the session; a scored gym run is exactly that.
- The arena is `main` of the gym repo, **force-swapped** per scenario
  (`provision.mode: swap`). That is a deliberate, supported operation there — but it is
  destructive-by-design against a branch called `main` and is not a call this item should
  make unattended.
- It needs the whole org live (Mattermost + `agent-bridge` + workers) and the gym's own
  scenarios budget **`wall_minutes: 240`** with `taps: 3` (`scenario-007-corpus-proof`).
- The gym's last four recorded runs (`038`–`041`, 2026-07-30/31) all end
  `published_assertions_unmet`; `041` graded DISHONEST-CLOSE with 0/5 assertions. Its
  present state is itself a finding, not a stable measuring instrument.

**What a real arena run would add that the local run does not** — this is the honest cost
of the park, stated as what is genuinely unproven:

1. **That the org, running autonomously, is actually STOPPED by the banked check.** The
   agent-org drill proves the *gate* withdraws the merge and writes
   `acceptance_corpus_failed` when the check goes red — it drives a real git remote, a real
   `agent/<effort>` delivery branch, and the gate's own fetch/checkout/exec. What it does
   not prove is that a real worker, given a goal that never mentions the inventory,
   produces a delivery that the check catches and then *converges* on the fix. That is
   gym-007's actual claim ("the recurrence broke"), and it is a property of the ORG, not of
   the gate.
2. **Score from the outside.** The gym scores against remotes and PRs precisely because the
   org's self-report is not admissible (§0 A3). A local drill's verdict is produced by the
   same process that seeded the regression.
3. **Cost.** A scenario measures taps and wall-clock. Nothing here measures whether the
   pipeline is affordable in a real iteration.

**What would meet it, concretely.** A new scenario in the gym's catalog of gym-007's shape
— goal that does not mention the inventory, `check_cmd` registered as
`python scripts/checks/check_stack_services_paths.py`, arena swapped to a template carrying
a stale `projects[*].file` — run with `--auth app` against the org, scored from the
remote. That needs: the org live, GitHub App egress, an operator decision on the arena
force-swap, and a ~4h budget. All four are outside this item's authority; none is a
technical blocker.

**Recommendation:** U3 parks here. The seeded-regression clause is discharged and
executable; the arena clause is a scheduled operator-gated run, not a rewrite of the
column. PLAN §2's U3 row is the task statement and is left exactly as written (the
`u4bidir` merge-guard rule applies here too: editing a phase description to match what was
delivered is moving the target to hit it).

---

## F1 — the tester finding this was built from, and why that one

`.git/agent-worktrees/queue/watchdog-fix.attempt1.evidence.md`, section C item 4,
wt-tester-3, 2026-08-28, on an item the same tester **PASSED**:

> RESIDUAL RISK FOR THE REVIEWER: the NEW data this patch adds (the projects map's
> `file` and `env_file` fields) is NOT covered by that verifier, and the verifier only
> runs when a `.yml` is staged. A plane compose-file RENAME would silently stale the
> `file` field.

Chosen over the other candidates (`search-rm` attempts 1–3, `coder-readme` attempt 2)
because it is the class U3 is actually about: a finding that **did not block the merge**.
A FAIL is carried forward by the failure itself — the developer must fix it. A true
finding attached to a PASS is carried forward by nothing at all, which is precisely
A5's evaporation. It is also mechanically checkable, which most prose findings are not.

Verified before building on it (§0 A9 — do not relay a claim you have not opened):
`check-project-configs.ps1:60-102` builds `$known[container] = project` from `$inv.planes`
only, and the whole file contains no read of `$inv.projects`;
`stack-watchdog.ps1:132-141` DOES consume `$Proj.file` and `$Proj.env_file` to build every
plane repair command. So the gap was real, and still open two days later.

**Round-2 correction to my reading of it.** The tester's sentence is narrow and TRUE: "that
verifier" is `check-project-configs.ps1`, and it does not cover those fields. What was
false was my generalisation of it (R1). Note also that the script which DOES catch seed A,
`check-watchdog-repair-targets.ps1`, was added by commit `e857741` — the very item
(`watchdog-fix`) that tester was reviewing. The item partially closed the gap its own
tester flagged, for the subset of projects the watchdog manages, and neither of us noticed.

## F2 — the plane and the local registry can already disagree, and nothing reconciles them

Before this run, `.git/agent-worktrees/durable-checks.json` did not exist (zero banked
checks). The plane already held one:

    select count(*) from agent_memories where memory_type='check'  ->  1
    check-5da31066f5ff1528 | "the merge-protocol drill must stay green - U3 live proof"

So an earlier U3 session mirrored a check to the plane without banking it locally. After
this run the count is 2 and the registry has 1 row. `durable_checks.run` runs the LOCAL
registry, so a check that exists only on the plane is a memory ABOUT a check — the exact
prose form the module's own docstring says it exists to replace.

**Not claimed as fixed.** Recorded because "checks are durable now" is not yet true of
the pair; it is true of the registry. A reconcile direction (plane → registry) is the
missing half and is a natural U6 recall-side item.

**Round-2 addendum:** that plane memory says *"the merge-protocol drill must stay green"*.
R2 shows that drill was 2-clean-in-8 at the time it was banked. A durable check banked on a
flaky command inherits the flakiness — the bank has no notion of a check's reliability, and
`durable_checks.add` will happily content-address a coin flip. Worth a rule when U6 touches
the bank: a check earns a row after N consecutive greens, not on first sight.

## F3 — the agent-org drill's first version passed for the wrong reason

Its first run asserted "the merge gate is WITHDRAWN" and that assertion **passed** —
while `real_checks` showed exit code **128**. The sandbox was a plain directory, so the
gate's own command (`git fetch origin agent/<effort> && git checkout -f FETCH_HEAD &&
<check>`) died in `git fetch`, and the corpus was red because git failed, not because
the seeded regression was found. A withdrawal assertion alone would have shipped as
proof of something that never happened.

What caught it was asserting the check's **own** exit code (1) and its output text
(`projects.search.file`), not merely non-zero. The fix was to make the sandbox a real
git remote with a real `agent/<effort>` delivery branch carrying the regression as a
commit — which also made the drill considerably more faithful than it was going to be.

## F4 — secrets were travelling into the sandboxes, and no longer are

Both drills built their sandbox by following `projects[*].env_file` literally, which for
five of eight projects is `.env` — so every run copied the live root dotenv into `%TEMP%`,
and the agent-org drill went further and **committed it** into a temp git repo. Deleted at
teardown, but "it was under %TEMP%" is not the standard: PLAN §C.2 class 4 is *no secret
VALUES anywhere they persist or travel*.

Fixed in both: the sandbox now copies `<env_file>.example` and never the real file. Nothing
is lost — rule 4 of `check_stack_services_paths.py` deliberately accepts `<path>.example`,
because `.env` is gitignored and a check that is red in a fresh clone is a check nobody
keeps. Asserted, not assumed: the harness drill checks
`not (sandbox/'.env').exists() and (sandbox/'.env.example').is_file()`.

Found while extending the sandbox for R1's counterfactual, on a line neither round-1
verifier flagged.

## F5 — `scripts/agent-harness/README.md` said the merge-protocol drill is "45 checks"

It prints `66/66`. Round 1 recorded this and left it alone, correctly, as a neighbouring
finding. Round 2 had to rewrite that same row anyway (the drill's contract changed: it now
exits 3 when another copy holds the lease), so the stale count went with it — replaced with
no count at all rather than `66`, because a hard-coded count in prose is a claim that rots
every time a check is added. A row that describes the contract does not.

## F6 — `ruff` F811 on the work line, not from this item

`scripts/agent-harness/test_anchor_schema.py:267` re-imports `subprocess` (already imported
at line 19). `ruff check scripts/agent-harness/` fails on it. Introduced by `796bf38`
("U3: acceptance criteria can be commands, not only prose"), already merged. One line to
delete; not touched here because it is not this item's file and a drive-by edit to a
neighbouring U3 commit's work is how two agents end up conflicting.

## F6b — two more counts in prose that the artifacts contradict

`documentation/implementation-guide/dark-factory-unification/PLAN.md:615` describes
`verify-merge-protocol.ps1` as "51/51 green". It prints 66. `README.md` said 45 (F5). Three
different hard-coded counts for one drill, in three files, none of them current. Not edited
here: PLAN.md is the confirmed anchor and a count inside it is the operator's to amend
(`-AmendAnchor` semantics per §B), not a fix round's. The general rule this argues for:
describe a drill by its CONTRACT, never by a check count.

## F7 — what was NOT done, in full

- **No `ai-orchestration-gym` scenario was run.** See §P.
- **No live plane was mutated** beyond one additive write to the agent-memory plane
  (`memory_type='check'`, idempotency key `check-25fa4d2b94a3ef8e`) and the shared
  durable-check registry file. No lease was taken for plane access because nothing needed
  a plane to be stable.
- **The worker turn and the GitHub API are faked** in the agent-org drill, as in every
  agent-bridge test. The check RESULT, the delivery branch, the git fetch/checkout, the
  gate, the route-back, the withdrawal and the audit record are real.
- **`little-coder` was not exercised.** U3's column does not ask for it; A11/U4 does.
- **`verify-merge-protocol.ps1` was not re-run to a clean burst** after the fix. See R2 for
  why, and for why that is a merge-order property rather than an unknown.
- **The full agent-org suite was not re-run to completion this round.** The only agent-org
  file this item touches is `tests/test_corpus_seeded_regression.py`; no production module
  changed. That file passes 3/3 on three consecutive runs.

---

## DECISIONS entries to append

### 2026-08-30 · U3 · class 2 — the "nothing else catches this" counterfactual was FALSE; corrected and made executable
DECISION: `check_stack_services_paths.py`'s docstring and `seeded_regression_drill.py`'s
          counterfactual section no longer assert that nothing pre-existing catches the
          seeded regression. The drill now RUNS `check-watchdog-repair-targets.ps1
          -SkipDocker` against four seeds and asserts the exit-code matrix: seed A is
          CAUGHT by the pre-existing check; seeds B (half-fixed rename), C (rename in a
          project with no watchdog-managed container) and D (env_file drift) are missed by
          everything that existed. The banked check's value rests on B/C/D.
CITED:    §0 A6 (prose verification FALSIFIED) — a counterfactual proved by grepping one
          file is prose wearing a command's clothes. Reproduced by the orchestrator's
          verifier and then independently here before any edit.
CLASS:    2 — a factual correction to a shipped claim, decided and continued.
REVERT:   `git revert` the round-2 commit on `work/u3gym`. The measured matrix is additive
          (sections 3-6 of the drill); reverting restores the earlier, false docstring, so
          prefer editing over reverting if only part is unwanted.

### 2026-08-30 · U3 · class 2 — `verify-merge-protocol.ps1` is single-flight; it was 2 clean runs in 8
DECISION: The merge-protocol drill acquires the lease `merge-protocol-drill` before its
          destructive preamble and exits 3 when another copy holds it; `-LockProbe` takes
          the decision without running. `Test-IsWorktreeRoot` was added to `git-io.ps1` and
          the drill ABORTS if `$wtA`/`$wtB` is not a worktree root.
CITED:    Measured 66/66, 66/66, 63, 59, 39, 34, 40 of 66 over eight consecutive runs, with
          a concurrent copy (pid 137560) observed in `Get-CimInstance` mid-burst. `git -C`
          ASCENDS out of a non-worktree directory, so a half-failed provision aimed the
          drill's `rebase` at the OPERATOR'S checkout, which was found holding
          `.git/rebase-merge` with head-name `refs/heads/refactor/ai-stack-cleanup`.
          `lease-names.conf`'s "git state needs no lease" is right for every other caller
          and wrong for a script that CREATES worktrees.
CLASS:    2 — a real defect in a cited gate, fixed in the house pattern (lease.ps1 + the
          topology-facts module), most-reversible option taken (refuse to start; never
          auto-clean another run's state).
REVERT:   Remove the lease block and the `-LockProbe` param from
          `verify-merge-protocol.ps1`, the `Test-IsWorktreeRoot` guard loop after Step 1,
          the `merge-protocol-drill` row from `lease-names.conf`, the
          `Test-IsWorktreeRoot` function from `git-io.ps1`, and delete
          `scripts/agent-harness/test_drill_single_flight.py`. Nothing else imports any of
          it; the drill's behaviour returns to unlocked exactly as before.

### 2026-08-30 · U3 · class 2 — the drill is WITHDRAWN from U3's evidence rather than re-cited
DECISION: `verify-merge-protocol.ps1`'s 66/66 no longer appears as U3 evidence. "Drills
          green in both systems" is carried by `seeded_regression_drill.py` (28/28, 5/5
          runs), `test_drill_single_flight.py` (14/14, 8/8 runs), `test_org_drill.py`
          (9 passed, 6/6) and `test_corpus_seeded_regression.py` (3 passed, 6/6+3/3).
CITED:    §C.7 — nothing merges unrefuted, and a check that fails half the time cannot
          stand between an item and an unread merge. The fix is shipped but NOT validated
          by a clean burst, and the lease only excludes copies that carry it, so the guard
          is one-sided until this branch merges.
CLASS:    2 — evidence hygiene: a fixed-but-unvalidated gate is not evidence.
REVERT:   n/a (a citation was removed, not code). To re-cite: after this branch merges,
          run the drill 8 times consecutively and require 8/8.

### 2026-08-30 · U3 · class 2 — secrets no longer travel into either drill's sandbox
DECISION: Both sandboxes copy `<env_file>.example` and never the real `.env`. The
          agent-org drill's sandbox is a git repo that COMMITS the tree, so this had been
          putting live secret values into a git object.
CITED:    §C.2 class 4 — no secret VALUES anywhere they persist or travel. Rule 4 of
          `check_stack_services_paths.py` accepts `<path>.example`, so nothing is lost.
CLASS:    2 — a class-4 boundary was being brushed by a test fixture; corrected on sight.
REVERT:   Restore the `for key in ("file", "env_file")` loops in `build_sandbox` /
          `_copy_tree`. Not recommended.

### 2026-08-30 · U3 — PARK on the `Gym:` clause of the Validated-by column
DECISION: U3 does not close. The seeded-regression half is discharged and executable; the
          arena half is not met and is not performable under the standing constraints.
CITED:    §C.7 ("a phase that cannot satisfy its column does not merge; it parks with a
          written reason"). `runner/gym_runner.py` mints a GitHub App token in
          `agent-bridge` and scores against `api.github.com` (§C.2 class 4: no external
          services beyond the session); the arena is `main` of the gym repo, force-swapped
          per scenario; scenarios budget `wall_minutes: 240` with `taps: 3`; and the gym's
          last four runs (038-041) all ended `published_assertions_unmet`, 041 graded
          DISHONEST-CLOSE.
WHAT WOULD MEET IT: a gym-007-shaped scenario whose goal never mentions the inventory,
          `check_cmd` = `python scripts/checks/check_stack_services_paths.py`, arena
          swapped to a template carrying a stale `projects[*].file`, run `--auth app` and
          scored from the remote. Needs the org live, GitHub App egress, an operator
          decision on the force-swap, and ~4h.
WHAT THE PARK COSTS: the local drills prove the GATE withdraws the merge on a red check.
          They do not prove the ORG converges on the fix, which is gym-007's actual claim,
          and they are scored by the same process that seeded the regression rather than
          from outside.
NOT DONE: PLAN §2's U3 row is unchanged. It is the task statement, not a claim of
          completion (see `documentation/notes/u4bidir-merge-guard.md`).
REVERT:   n/a — a park is a status, recorded here and in DECISIONS.

### 2026-08-30 · U3 · process — two self-inflicted slips worth the record
DECISION: Recorded, not hidden. (a) This note's round-2 rewrite was first written into the
          MAIN CHECKOUT instead of the worktree, landing as an untracked file in the
          operator's tree; caught by `git status` showing the staged set without it, copied
          into `wt-u3gym` and removed from the main checkout, which is now exactly as
          found. (b) The repro built to fix a flaky check was itself flaky (1 run in 6)
          because it snapshotted every ref in the repo while other agents were committing;
          scoped to the drill's own names, then 8/8.
CLASS:    1 each. Recorded because both are the SAME error as the ones being fixed —
          a shared surface treated as if it were private — arriving one layer up, twice,
          in the session whose subject is that error.
REVERT:   n/a.

### 2026-08-30 · U3 · process — the branch is `work/u3gym`, not `work/u4gym`
DECISION: The item's brief said `-Id u4gym`; the worktree was created as `u3gym`. Not
          renamed. Both round-1 verifiers reported "the branch does not exist" because of
          it, costing a full verification round.
CLASS:    1, recorded only because it consumed two verifier runs. A worktree id that does
          not match the brief is a merge-record defect even when the code is fine.
REVERT:   n/a.
