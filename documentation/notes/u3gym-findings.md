# Findings — U3's seeded-regression run (2026-08-30, `u3gym`)

Item: discharge U3's *Validated by* column — "Gym: a seeded regression must be caught by
a check born from a *tester* finding in a prior round (gym-007's shape, new source);
drills green in both systems".

## DECISIONS entries to append

### 2026-08-30 · U3 · class 2 — the seeded regression ran as a DRILL, not an ai-orchestration-gym scenario
DECISION: The seeded-regression demonstration is two executable drills
          (`scripts/agent-harness/seeded_regression_drill.py`,
          `agent-org/agent-bridge/tests/test_corpus_seeded_regression.py`), run
          against this repo and against agent-org's real acceptance-corpus gate.
          NO `ai-orchestration-gym` scenario was run.
CITED:    §2's preamble defines "gym" as *measured runs in ai-orchestration-gym,
          never live planes or a real target* — the property being bought is a
          disposable sandbox, which both drills have. §C.7 requires the phase's
          evidence to be an EXECUTABLE check and requires a limitation to be
          written down rather than papered over.
WHY NOT A SCENARIO RUN: a gym scenario drives a whole org iteration (worker turns,
          a real remote, hours) to score a DELIVERY. What U3's column tests is the
          finding→check→regression→red loop, which is a gate property, not a
          delivery property. The gym's own recent evidence argues the same way: its
          last recorded run is 2026-07-31 and the last four (`038`–`041`) all end
          `published_assertions_unmet`, `041` graded DISHONEST-CLOSE with 0/5
          assertions. A scenario run would have measured the org's convergence, not
          the pipeline U3 built.
WHAT IS THEREFORE NOT CLAIMED: that the org, running autonomously, produced or was
          stopped by this check in a live iteration. That belongs to U4's quadrants,
          where a runner actually executes an item.
REVERT:   Delete the two drill files and `scripts/checks/check_stack_services_paths.py`;
          drop the registry row (`.git/agent-worktrees/durable-checks.json`) and the
          `check-25fa4d2b94a3ef8e` memory. Nothing else imports them.

### 2026-08-30 · U3 · class 2 — the banked check is Python, under `scripts/checks/`
DECISION: `scripts/checks/check_stack_services_paths.py` is Python, in a directory
          that otherwise holds only PowerShell.
CITED:    §A's house-pattern rule vs. the fact that this exact command is executed
          from three places with different shells — `durable_checks.run` (shell
          exec), the harness drill (Windows host), and agent-org's delivery gate
          (a Linux worker container, after `git fetch && git checkout -f`). A
          `.ps1` body would make the durable check unrunnable in the third, which
          is the one U3 exists to unify.
WHY IT IS NOT A PRECEDENT BREAK: the check reads a JSON inventory and the file
          system. It touches no PowerShell-only surface, and the repo already runs
          Python checks from `scripts/agent-harness/`.
REVERT:   Rewrite as `.ps1` and re-bank; the registry is content-addressed on the
          command, so the old row must be retired by hand.

### 2026-08-30 · U3 · class 3 (QUESTION for the operator) — the check is NOT wired into pre-commit
DECISION: The new check runs from the durable-check registry (`durable_checks.py run`)
          and from both drills. It was NOT added to `.githooks/pre-commit`.
CITED:    the precedent in the very finding that created it — the same tester noted
          `check-watchdog-repair-targets.ps1` is "deliberately NOT wired into
          pre-commit (the anchor makes that the operator's call)". Wiring a new gate
          into everyone's commit path is a change to the commit path, not to U3.
COST OF THE DEFAULT: a stale `projects[*].file` can still be committed; it is caught
          the next time the registry is run rather than at the commit that made it.
REVERT:   n/a (nothing was wired). To adopt: one line in `.githooks/pre-commit`.

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
- `scripts/checks/check-project-configs.ps1:60-102` builds `$known[container] = project`
  from `$inv.planes` only, and the whole file contains no read of `$inv.projects`.
- `scripts/checks/stack-watchdog.ps1:132-141` DOES consume `$Proj.file` and
  `$Proj.env_file` to build every plane repair command.
So the gap was real, and still open two days later.

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

This is the failure class CLAUDE.md names ("a check that passes while checking nothing")
observed in this session's own work, and it is why both drills assert the reason as well
as the verdict.

## F4 — both drills were proven RED before being reported GREEN

Sabotage: `check_stack_services_paths.py`'s `main` was edited to report zero problems
unconditionally (a check that checks nothing), then reverted.

| drill | sabotaged | restored |
|---|---|---|
| `scripts/agent-harness/seeded_regression_drill.py` | 14/17, **exit 1** | 17/17, exit 0 |
| `agent-org/.../test_corpus_seeded_regression.py` | 5/9, **exit 1** | 9/9, exit 0 |

In the sabotaged agent-org run the merge gate was NOT withdrawn — i.e. the gate's
verdict tracks the check's verdict, which is the property the drill claims.

## F5 — `scripts/agent-harness/README.md` says the merge-protocol drill is "45 checks"

It prints `66/66 checks passed` (run 2026-08-30). A count in prose that the artifact
itself contradicts. Left alone deliberately — it is not this item's deliverable and the
convention is that findings about neighbouring work land here, not in the artifact.

## F6 — what was NOT done, in full

- **No `ai-orchestration-gym` scenario was run.** No org iteration, no worker turn, no
  PR, no external remote mutation. See the first DECISIONS entry for why.
- **No live plane was mutated** beyond one additive write to the agent-memory plane
  (`memory_type='check'`, idempotency key `check-25fa4d2b94a3ef8e`, count 1 -> 2) and
  the creation of the shared durable-check registry file. No lease was taken because
  nothing needed a plane to be stable; the plane write is a single idempotent insert
  through the ops door.
- **The worker turn and the GitHub API are faked** in the agent-org drill, as in every
  agent-bridge test. The check RESULT, the delivery branch, the git fetch/checkout, the
  gate, the route-back, the withdrawal and the audit record are real.
- **`little-coder` was not exercised.** U3's column does not ask for it; A11/U4 does.
