# MODULE — the agent harness

This directory is a **module**: a self-contained way to run several agents on one
repository without them treading on each other, and to land their work through a
pipeline with separated duties. It can be configured, switched off, lifted out, or
dropped into another distribution.

It was called `scripts/worktree/` until 2026-08-28. The name described one of the
five things in here; the rename was to stop it mislabelling the rest.

## What it is for

Several agents work at once, each in its own `git worktree`. Work reaches the
shared branch through a queue with roles: the developer never tests and never
merges their own work. Two human gates bracket the machine work — the operator
agrees **what the work is for** before it starts (the anchor), and releases it
for review after the tests pass.

## The public surface

Everything else in here is internal and may change without notice.

| entry point | what it does |
|---|---|
| `new-worktree.ps1 -Id <id>` | provision an agent's worktree (branch, env files, registry row) |
| `remove-worktree.ps1 -Id <id>` | retire it; refuses while it holds unmerged work |
| `sync-worktree-env.ps1 -Id <id>` | re-copy runtime env files when the main checkout's are newer |
| `queue.ps1` | the pipeline: propose → confirm → submit → test → release → review → merge |
| `lease.ps1` | named leases for SHARED RUNTIME only (Docker, GPU, ports, live DBs) |
| `dispatch.ps1` | RUN the work: role+profile → runner → submit, follow, one outcome + exit code |
| `verify-merge-protocol.ps1` | the executable drill over the whole protocol |
| `verify-dispatch.ps1` | the executable drill over the dispatch layer (probes the REAL daemon by default; `-Offline` skips that and says so) |
| `verify-oracle-on-stall.ps1` | the executable drill for frontier-oracle-on-stall - a CONSTRUCTED stall, seconds, no live planes |
| `observe-oracle-on-stall.ps1` | the OBSERVATION: dispatches an unsatisfiable item to the live local runner N times and lets the detector judge the real rounds. Needs the coder plane, the coder lease and minutes - an experiment, never a CI check. **Its verdict names the ledger row THIS run appended** (row ids are snapshot before the rounds), every queue and git call is exit-code checked, and each run takes a stamped item id and probe branch which it deletes - including on every refusal path. Exit 0 = observed, 1 = no escalation, 2 = misuse, 3 = a step failed and there is no verdict |
| `oracle_on_stall.py` | the stall detector, the escalation, and its ledger (U4) |
| `durable_checks.py` | tester finding -> durable executable check, banked in the SHARED git-dir registry (U3) |
| `u3_evidence_regression_gym.py` | U3's GYM RUN: banks the check born from a tester finding, seeds regressions into a sandbox inside the ARENA, and measures the counterfactual against the pre-existing gate. Refuses outside a gym venue |
| `test_evidence_reproduces.py` | the fast guards for that banked check - what the drill proves in the arena, provable anywhere in a second |
| `andon.ps1` | the andon board: evaluate the stop-the-line conditions (`-Evaluate`, `-List`, `-Baseline`) |
| `drill-dark-factory.ps1` | the executable drill over the andon board, the gate profiles and the audit trail |
| `harness.config.json` | the configuration (see below) |
| `config.py` | the reader other Python code imports (`bridge.py` does) |
| `quadrant/` | the runner x target comparison - its own submodule with its own boundary, see [quadrant/MODULE.md](quadrant/MODULE.md). One of the TWO places that actually run a resolved runner - `dispatch.ps1` is the other, and `adapters.py` here calls the same docker-exec transport. Everything else in the module only resolves one. |

Internal: `common.ps1` (composition root), `git-io.ps1` (git facts), `resolve.ps1`
(policy), `config.ps1` (settings), `anchor.ps1` (the anchor's shape and validation),
`gate-audit.ps1` (the gate ledger and the definition of a complete audit trail).

The dependency direction is one-way and deliberate:

```
queue.ps1 / lease.ps1 / new-worktree.ps1 / dispatch.ps1
        |
     common.ps1 ──> resolve.ps1 ──> git-io.ps1   (facts about this repository)
                          └──────> config.ps1    (files + environment -> settings)
                    anchor.ps1                   (the shape of an anchor; owns no state)
```

## Runners: resolving one and running one are two different things

`config.ps1`'s `Resolve-RoleTarget` answers *which* runner and model a role uses.
`dispatch.ps1` is what CALLS it. Until 2026-08-30 only the first half existed, which
is why PLAN.md's A11 ("the little-coder runner is wired, status: unproven") read as
optimistic: the wiring was a config entry naming a door — `http://127.0.0.1:8090` —
that `coder/docker-compose.yml` never published. See
`harness.config.json`'s `_why_docker_exec` for the transport decision and its revert path.

A runner record therefore carries topology (`transport`, `container`, `base_url`, the API
paths, and the `lease` a caller must hold) as well as policy, readable through
`Get-HarnessRunner` / `config.runner()`.

**DECLARED is not the same as EXISTS, and two different checks cover the two claims.**
`test_harness_config.py`'s door check is a substring match over the TEXT of the compose
files: it proves the door is **declared** — a `127.0.0.1:<port>` publish for `http`, a
`container_name:` for `docker-exec` — and it proves nothing about the running stack. Those
two genuinely differ here: on 2026-08-30 `coder/docker-compose.yml` declared
`127.0.0.1:9091->9090` for little-coder while `docker port little-coder` printed nothing and
`docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` gave `{"9090/tcp":[]}`.
**The cause of that disagreement is NOT established.** The obvious guess — that the running
container predates the declaration — is wrong: `docker inspect little-coder --format
'{{.Created}}'` is `2026-08-23T17:00:48Z` and the ports line has been in
`coder/docker-compose.yml` since `56af93a` (2026-08-21), so the container POSTDATES the
declaration by two days. The runtime claim is `verify-dispatch.ps1`'s live section, which by
DEFAULT inspects that the container is really running and reaches the daemon over the
declared transport; its summary line states the real transport's coverage on every run that
reaches the end — `COVERED`, `NOT COVERED`, or `ATTEMPTED AND FAILED`.

`dispatch.ps1` returns ONE outcome shape whatever the runner, and its exit code
distinguishes "the dispatch worked" from "the work is right": `0` acceptance passed,
`3` acceptance failed, `4` completed with no checkable signal, `1` no usable outcome at all
(no runner, no lease, unreachable transport, no focus, timeout, or a task that ended
`abandoned`/`rejected`). The little-coder daemon runs the acceptance command itself, so the
agent never grades its own work.

A runner that declares a `lease` cannot be dispatched unless the caller HOLDS that lease
(`-LeaseOwner`, or `AI_STACK_LEASE_OWNER`): focusing little-coder wipes its workspace and a
task runs arbitrary commands in it. Someone else's lease refuses the dispatch just as a free
one does.

**Run the drill as `powershell.exe -File`, not with `&` inside a session you are reusing.**
Under `powershell.exe -NoProfile -File .\scripts\agent-harness\verify-dispatch.ps1` it is
deterministic here. UNVERIFIED, reported by a U4 verifier and NOT reproduced: invoked with
the call operator (`& $drill -Offline`) repeatedly inside one long-lived session they saw
three different counts (50/51, 46/51, 48/51, differing checks) on an unmodified copy. Nine
such runs in one session on 2026-08-30 gave `51/51 checks passed` every time with zero
`[FAIL]` lines, so the cause is unknown and it may not be reproducible. The reported
failures were spurious REDs only — never a green that should have been red — so the risk is
a confusing verdict, not a false pass.

**No pipeline consumes `dispatch.ps1` yet.** `queue.ps1` does not call it and nothing else
does either — today it is driven by hand or by a session, and the only shipped consumer of
the runner layer is `bridge.py`'s `profile: list`, which renders `describe_runner()`.
Wiring dispatch into the queue is U4's remaining half, not something this file already does.

`config.ps1` knows nothing about git, worktrees, queues or leases — which is what
lets another distribution retarget the toolkit by editing JSON instead of code.

## Configuration

One file, two readers. `config.ps1` serves the scripts, `config.py` serves the
bridge, and `test_harness_config.py` asks them the same questions and compares the
answers so they cannot drift apart unnoticed.

Layers, lowest to highest:

```
built-in defaults < harness.config.json < harness.local.json (gitignored) < environment
```

Environment overrides are a short explicit list, not a naming convention:

| variable | effect |
|---|---|
| `AI_STACK_HARNESS_CONFIG` | path to an alternate `harness.config.json` |
| `AI_STACK_HARNESS_ENABLED` | `0` — kill switch, beats both files |
| `AI_STACK_HARNESS_PROFILE` | profile name applied to every surface |
| `AI_STACK_LEASE_OWNER` | default `-LeaseOwner` for `dispatch.ps1`'s lease assertion |
| `AI_STACK_LEASE_DIR` | the lock namespace (drills point it at a temp dir to stay hermetic) |

Two more are named *by* the configuration rather than hardcoded, so a distribution
can rename them: `worktree.work_line_env` (default `AI_STACK_WORK_LINE`, which
branch agents work against) and `worktree.state_dir_env` (default
`AI_STACK_WORKTREE_STATE`, where coordination state lives).

## Profiles: which models the roles run on

A profile assigns each role a **runner** and a model. The runner matters because
"cloud" and "local" are different execution substrates, not two values of one
setting: `claude-code` is a Claude Code agent, `little-coder` is the local control
plane reaching `llama-cpp` through LiteLLM.

`all-cloud` — opus for all three — is the default everywhere. `all-local`,
`local-work-cloud-review` and `cloud-work-local-test` are also defined.

- **Extension sessions are locked** to `all-cloud` (`profile_locked: true`). A
  requested profile is ignored there. Operator decision, 2026-08-28: the surface
  the operator drives interactively should never silently degrade.
- **Mattermost sessions** switch with `profile: <name>` in a thread. The bridge
  passes the choice to the session as `AI_STACK_HARNESS_PROFILE`, which is simply
  the configuration's own top layer — no separate mechanism.

The `little-coder` runner is wired, callable, and **has been dispatched to**. The
four-quadrant comparison committed under `documentation/evidence/dfu-u4/quadrant/`
was produced by real tasks on it — the transcripts carry the daemon's own task ids
(e.g. `01M1D1ZV0J9JYZ347T8QRDT7WX`) and two cells completed with acceptance 2/2.

Its `status` field still reads `unproven`. That is now a stale LABEL rather than a
description, and this branch deliberately does not flip it: `quadrant/matrix.py`
reads that field to decide whether a cell may enter a decision table, and every
committed record carries the value that was in force (`"runner_status": "unproven"`).
Changing it is a decision about the evidence, not a documentation fix.

**How it is dispatched** — measured 2026-08-31, not assumed. `resolve_role` maps a
role and a profile to a runner; `dispatch.ps1` is what then RUNS one. `Submit-LcTask`
POSTs the daemon's `/tasks` through `Invoke-LcApi`, and `quadrant/adapters.py` is the
second caller. The transport is `docker exec`, **not TCP** — the distinction the
previous version of this paragraph collapsed:

| claim | measured |
|---|---|
| a host TCP door onto the task API | **none.** `curl http://127.0.0.1:8090/health` from the host is connection-refused (curl exit 7). The compose file **declares** only `127.0.0.1:9091 -> 9090` (metrics, not the API), and the **running** container publishes nothing at all: `docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` returns `{"9090/tcp":[]}` and `docker port little-coder` prints nothing. Compose text is what was intended; `docker inspect` is what is |
| the API is reachable by the harness | **yes.** `docker exec little-coder curl http://127.0.0.1:8090/health` answers `200`. That is the door `dispatch.ps1` uses. `harness.config.json`'s `_why_docker_exec` records why it was taken over publishing the port — `POST /tasks` (arbitrary agent execution) and `POST /admin/shutdown` are unauthenticated — and how to revert |

**What this replaced, and why it is written out rather than quietly deleted.** The
sentence here used to read *"nothing in this module then dispatches to one. There is
no code path that submits a task to little-coder's API — and that API port is not
reachable from where the harness runs in any case."* The second half is true of a host
TCP port; the first half was false; and joined by *"in any case"* they assert that no
dispatcher can exist, which this module's own evidence disproves. It also contradicted
the entry-point table above (`dispatch.ps1 | RUN the work: role+profile -> runner ->
submit`) and the *Runners* section (`dispatch.ps1 is what CALLS it`) in the same file.
Corrected 2026-08-31 after a verifier found the contradiction. The old paragraph ended
"a dispatcher must be built against the second" — one was.

## Frontier-oracle-on-stall

ORCHESTRATION-DESIGN sec 7 splits the work roughly 95% small-model search / 5%
frontier unstick, and is specific that the frontier is "an oracle invoked on a
stall signal — not a better worker": it injects the one constraint that only
knowledge provides, then hands back.

`oracle_on_stall.py` is that stall signal, and the durable record of the
escalation. It runs on every `queue.ps1 -Fail`, because a failed test round is the
only moment the line learns something new about whether an item is converging.

- **A stall** is agent-org's definition, ported rather than re-invented: a round
  whose failure signature is not novel against every signature seen on this item,
  or whose branch head did not move, is not progress; two such rounds in a row is
  a stall. `failure_signature` is `Orchestrator._failure_sig` byte for byte, and a
  test extracts that function from `orchestrator.py` and compares behaviour, so
  the two cannot drift apart quietly.
- **Firing** appends to `oracle-escalations.jsonl` in the shared state dir: what
  stalled, the round-by-round trail the detector saw, the runner that stalled, the
  oracle above it, and `hand_back_to`. Read it with `queue.ps1 -Oracle`.
- **When the worker is already the frontier runner** — which is what the default
  `all-cloud` profile means — the outcome is `no-oracle-above`: recorded, not
  escalated. Escalating claude-code to claude-code would fill the audit trail
  while changing nothing.
- **A round whose branch head could not be READ is not scored either way.** An
  unmeasured round is not evidence that the code stood still, and treating it as
  such turned a tooling failure into a frontier escalation: `git rev-parse
  <missing-ref>` prints the ref NAME on stdout and exits 128, and `-Fail` did not
  check the exit code, so a deleted branch was recorded as `sha:
  "drill/oracle-stall"` — identical every round. `-Fail` now refuses that verdict,
  and the detector normalizes any non-object-name to "not recorded".
- **Nothing dispatches the oracle round yet.** `pending()` returns the target a
  dispatcher would use; the dispatcher is the unbuilt half of U4.
- **A green drill is still not an observation.** `verify-oracle-on-stall.ps1`
  CONSTRUCTS a stall and proves the mechanism end to end; do not read it as "the
  oracle worked an item". The observation is a separate artifact and it now exists:
  `observe-oracle-on-stall.ps1` dispatched an unsatisfiable item to the live local
  runner three times, round 3 stalled, and the escalation is committed at
  `documentation/evidence/dfu-u4/stall/` (ledger row `417aa274750da712`). It wrote to
  a SCRATCH state namespace on purpose, so this module's own ledger
  (`<git-common-dir>/agent-worktrees/oracle-escalations.jsonl`) is still ABSENT — an
  observation must not append to the ledger the deliverable is audited from. "The
  ledger is empty" thus remains true of that file and is no longer true of the work.
  Corrected 2026-08-31, when this bullet still said nothing had stalled for real.

`queue.ps1 -Submit -RunnerProfile <name>` records which profile an item is worked
under, so the detector can name the runner that stalled. Not `-Profile`: `$Profile`
is a PowerShell automatic variable, and a parameter of that name shadows it.

## Gate profiles: who passes a gate (`attended` vs `dark`)

A **gate profile** says who passes each pipeline gate — `human` or `auto`. It is a
SEPARATE block from `profiles` above, and that is deliberate: `profiles` is keyed by
role, a gate is not a role, and more importantly an operator switching to a cheaper
MODEL profile must not thereby also remove the humans from the gates. Two unrelated
decisions, two names.

| profile | `anchor` | `pre_review` |
|---|---|---|
| `attended` (default) | human | human |
| `dark` | auto | auto |

`pipeline.gate_profile` selects one; `queue.ps1 -GateProfile <name>` overrides for a
single call. **The default is `attended`** — a typo that lands on the default must
leave a human at the gate, not remove one.

What a gate DOES stays in `queue.ps1`: the state transitions, the duty separation,
the refusal of self-service. That is behaviour. Who passes it is tuning.

Under `dark`:

- `-Submit` on an `anchor-draft` item self-passes the anchor gate instead of exiting 5;
- a tester's `-Pass` releases straight to `ready-review` instead of stopping at `test-passed`;
- **both refuse unless the andon board is CLEAR** - exit 6 on any of the other seven words
  (unaccounted, incomplete, raised, warned, indeterminate, partial, not-evaluated), parking
  the item where it stands. This list omitted `indeterminate` and `unaccounted` until
  2026-08-30, the two words that round added - the identical defect to `queue.ps1`'s
  exit-code list, which was missing `warned`, one file over. A partial list of the words that
  refuse reads as though the missing ones do not, so every enumeration of board words in the
  repository is now derived and compared against the code
  (`test_every_enumeration_of_board_words_in_the_repo_is_complete`);
- **every auto-pass is recorded** under the reserved principal `auto:<profile>`, which
  `-ConfirmAnchor` and `-Approve` refuse to accept as a human `-By` (exit 4). The
  namespace is reserved in both directions.

## The andon board: stop the line

`harness.config.json -> andon.conditions` declares the conditions as DATA; `andon.ps1`
holds the executable predicates. A condition naming a predicate that does not exist is
**refused**, so the config cannot declare a detector nobody implemented.

Every shipped condition names the incident it came from — they were mined from the
2026-08-30 unattended run's own record, not invented:

| id | fires when |
|---|---|
| `operator-checkout-off-branch` | the main checkout is detached or mid-rebase/merge/cherry-pick |
| `policy-declared-unread` | a policy knob under `pipeline` **or `andon`** that no executable source reads |
| `git-error-swallowed` | a git call site whose result is not checked within `check_window_lines` lines |
| `work-branch-on-remote` | a work branch of this run exists on a remote |
| `protected-ref-moved` | `main` moved since `andon.ps1 -Baseline` recorded it |

`halt` is the default for BOTH `on_fire` and `on_indeterminate`, and they are the only
two words either may take (`halt`, `warn` — `config.ps1` `$script:AllowedAndonActions`,
mirrored in `config.py`; `andon.ps1` refuses anything else with exit 1 and no verdict,
which every gate reads as "not clear"). A condition that could not be evaluated has not
passed - a skip that counts as a pass is one of the shapes this board exists to catch, and
it does not get to be the board's own behaviour. **That was false at run time until
2026-08-30**: `halt` is only the DEFAULT, and `on_indeterminate: warn` on one condition
auto-passed a dark gate on a condition that could not be evaluated. What makes it true is
not the default but the census below - an indeterminate condition lands in the
`indeterminate` bucket, and `clear` requires that bucket to be empty whatever the action
says.

**`warn` does not buy a pass.** A condition that FIRED is never a clear board whatever its
`on_fire` says, so no unattended gate passes over one; what `warn` changes is the word
(`warned` rather than `raised`) and the record. That is deliberate, and the alternatives
were weighed: *coercing* `on_fire` to `halt` under `dark` would make the config say one
thing while the run did another, and *refusing to start* when any condition declares `warn`
would punish a declaration that may never fire. Narrowing what `clear` MEANS does the same
job at the only point that matters — the gate — and leaves the operator's declaration
readable in the verdict. Under `dark` specifically, `warn` and `halt` have the same
consequence for the gate; the difference is triage for whoever reads the ledger.

**Halt** here means the gate refuses and the item parks; nothing is killed. The
**raise** always goes to the gate ledger — that is not a knob, because a run able to
switch off the record of its own halt is the failure the board exists to prevent —
and, under `andon.raise.stderr`, to stderr.

### `clear` means the board LOOKED, and that it was the WHOLE board

The verdict has **eight** states, and only one of them opens an unattended gate. (This line
said "five" above a SIX-row table on 2026-08-30 - the uncounted sixth was `warned`, added
that morning and never counted. A prose number beside a table is a claim the table can
contradict, so `test_gate_profiles.py::test_the_MODULE_verdict_table_matches_the_board`
now compares both against the board words `config.ps1` can actually produce.)

| board | means | exit |
|---|---|---|
| `clear` | census balances, no REQUIRED condition missing, **every bucket except `evaluated_ok` empty**, and at least one condition in `evaluated_ok` | 0 |
| `unaccounted` | a condition produced an outcome the board does not enumerate (an unknown status or action word), or the census does not sum to the conditions in scope. Outranks everything: a board that cannot say where its own results went cannot report a verdict | 6 |
| `incomplete` | a REQUIRED condition is **not declared at all** - the verdict names which | 6 |
| `raised` | a condition HALTED the line (its action was `halt`) - a fire with `on_fire: halt`, or an unevaluable condition with `on_indeterminate: halt` | 6 |
| `warned` | the `fired` bucket is non-empty: a condition FIRED and its `on_fire` is not `halt` | 6 |
| `indeterminate` | the `indeterminate` bucket is non-empty: a condition COULD NOT BE EVALUATED and its `on_indeterminate` is not `halt`. The sibling of `warned`, and a silent PASS until 2026-08-30 | 6 |
| `partial` | some evaluated ok, others switched off in config (the `disabled` bucket is non-empty beside a non-empty `evaluated_ok`) | 6 |
| `not-evaluated` | conditions are declared, none was evaluated (`andon.enabled: false`, or every one switched off individually) | 6 |

**`clear` is proven, not defaulted, and that is the 2026-08-30 correction.** The row used to
read "none halted", then "none FIRED", and each rewrite closed one outcome key while leaving
its sibling - because the verdict was computed BY EXCEPTION: a halt flag set only for
`action = halt`, a fired list only for `status = fire`, every other outcome setting nothing,
and `clear` as whatever was left when nothing objected. Now every condition is classified
into exactly one **census bucket** - `evaluated_ok`, `fired`, `indeterminate`, `disabled`,
`unrecognised` - the buckets must SUM to the conditions in scope, and `clear` requires every
bucket but `evaluated_ok` to be empty. An outcome nobody enumerated lands in `unrecognised`
and refuses, with no branch naming it. `fired` and `halted` remain separate reported lists
(what the detectors SAW versus what stopped the line); neither decides the verdict any more.
`unaccounted` and `indeterminate` are the two words the census added. If a bucket is ever
added it needs a word in `$script:AndonBucketBoard`, and until it has one the board refuses
rather than guesses.

**Every list of these words, anywhere in the repository, is derived and checked.** The
first version of that check compared this one table in this one file - a derived gate whose
alphabet had a single entry, which is not much better than a hand-written list, and the same
morning proved it: FOUR sentences in three files listed some of the words as though they
were all of them (this file's `dark` bullet; `andon.ps1`'s Invoke-AndonEvaluation comment
and its exit-6 comment; `queue.ps1`'s exit-code line, one round earlier). One defect, four
instances, and a check reading only this table saw none of them. So
`test_every_enumeration_of_board_words_in_the_repo_is_complete` takes its file list from
`git ls-files` and requires every list it can read to be the whole alphabet: the eight, or
the seven that are not `clear`. It reads TWO shapes, and the test's DISCLOSED LIMITS block
writes both out rather than summarising them - "both list shapes" is what this said until
2026-08-30, when an ordered list and a quoted array were planted as tracked files and both
stayed green. The shapes are: an inline run joined by list punctuation (comma, slash, pipe,
semicolon, "or", "and"), whose items may be quoted; and a block of consecutive lines that
each introduce a word - bulleted, numbered, table rows, definition rows, or the word alone
on its line. **If you mean a narrower set, say so in words or write it as
a mapping**; a positional list of board words is read as a claim about all of them, because
that is how all four went wrong. It finds enumerations, not single wrong words - that limit
is stated in the test, and the ways-off citation rule below covers the one place a single
wrong word had actually done damage.

`incomplete` outranks the rest: a verdict from a board that is not the required board
cannot be reported as that board's verdict. The conditions that *are* declared are still
evaluated, still listed, and still raised on stderr, so nothing is hidden by the name.

Every verdict and every gate record carries its **coverage** — declared / evaluated /
switched off / **required missing (by id)** — plus the repository the board was looking
at. Before U6 landed the first three, `andon.enabled: false` produced `board=clear,
conditions=5` on a genuinely detached checkout: indistinguishable from five conditions
that looked and found nothing.

It also carries the **bucket census** (`census`, `census_total`, `census_ids`) - which
condition landed in which of `evaluated_ok` / `fired` / `indeterminate` / `disabled` /
`unrecognised` - and **`looked_at`**, the predicate and params every condition actually ran
with. The census is what lets `-VerifyAudit` re-derive `clear` instead of reading it;
`looked_at` is what makes a `params.repo` redirect readable afterwards, which `andon.repo`
alone did not do (it is the checkout the BOARD resolved, and a `params` override does not
touch it).

### The required SET is code, not config

`config.ps1` (`$script:RequiredAndonConditions`) and `config.py`
(`REQUIRED_ANDON_CONDITIONS`) name the five ids the system requires;
`test_gate_profiles.py` asks both readers and the shipped config the same question so the
two declarations cannot drift.

It lives there rather than in `harness.config.json` because of the defect that produced
it. The board could be switched off two ways and both were closed — but there is a
**third**, and it is the one anybody actually reaches for: *delete condition entries from
`andon.conditions`*. Pruned to one of five on a genuinely detached checkout, the gate
**auto-passed** — exit 0, ledger `clear`, coverage `1 declared / 1 evaluated / 0 switched
off`, `-VerifyAudit COMPLETE`. Every counter was true, because every counter was relative
to the config's own thinned list. A required list kept beside the conditions would be
deleted along with the entry it names; kept in code, retiring a condition is a diff a
reviewer sees.

**The board's MEMBERSHIP is tamper-evident; its BEHAVIOUR is not.** Eight ways of switching
it off, or of getting a pass out of it, are closed - each proved at the real gate by a drill
case, and each open before it was closed. Which way gives which state is stated in ONE place,
README.md's ways-off table, and cited here by route id - because the same mapping written out
a second time went wrong in three files at once:
`andon-disabled` (`not-evaluated`), `andon-block-deleted` (`incomplete`),
`condition-disabled` (`partial`), `conditions-deleted` (`incomplete`),
`on-fire-downgraded` (`warned`), `on-indeterminate-downgraded` (`indeterminate`),
`action-word-unimplemented` (refused at evaluation, which every gate reads as `unavailable`),
and - the general one that subsumes the last three - `outcome-unenumerated`: a new status or
a new action word, which lands in the `unrecognised` census bucket and gives `unaccounted`
with no branch naming it. What is NOT closed is what a *declared*
condition does: its `predicate` and its `params` come from the config, so an entry keeping a
required id while naming a different predicate, or one whose `params.repo` points at a clean
decoy checkout, still passes every check the board makes at run time. `test_gate_profiles.py`
pins the id → predicate map of the **committed** config; nothing pins params, and nothing
pins either at run time or in a config named by `AI_STACK_HARNESS_CONFIG`. README.md lists
the open routes explicitly.

**Which way off gives which state is stated ONCE and cited by id.** `andon-disabled` and
`andon-block-deleted` do not produce the same state, and three files said they did -
`andon.ps1`'s header, `config.ps1` and `config.py` - all three written by the commit that
introduced the state that made them false, and none of them checked, because a sentence is
not a check. The mapping now lives only in README.md's ways-off table, whose rows carry
`route` ids; the drill declares the same map in `$script:WaysOffProven` and its assertions
read it, so `test_the_ways_off_table_matches_the_drill_that_proves_it` fails on a drill that
proves a different word, or on a row reaching no drill code at all. That test compares two
written-down copies, and saying more than that was untrue: until 2026-08-30 it claimed a row
nobody drills is caught, and a phantom row planted in BOTH copies with no assertion anywhere
passed with the suite green. What a row is EXERCISED by is now checked where it can be -
in the drill: `Check` registers a route when a PASSING assertion cites `(route <id>)` in its
label, and **step N** fails on any declared row no assertion exercised. Everything else cites a route id, and
`test_every_citation_of_a_way_off_names_the_state_this_table_proves` checks every citation
in the repository against the table - locations derived from `git ls-files`, matching exact
because a route id is a token rather than a phrase to be recognised. What it does not catch
is a fresh restatement that cites nothing, and the test says so.

**The revert to prior behaviour is `pipeline.gate_profile: attended`** — that is the switch
that puts a human back at the gate. Turning the board off only removes the thing that was
watching the machine.

That revert is the configured **default, not a lock**: `queue.ps1 -GateProfile dark`
names a profile for a single call and takes the dark path regardless of what
`pipeline.gate_profile` says (drill step I drives the same item both ways — exit 5
attended, exit 6 dark). Removing the human from a gate is one flag away by design; what
the gate profile decides is what happens when nobody passes one.

## The gate ledger: which gates no human saw

`<state dir>/audit/gates.jsonl`, append-only, one JSON record per gate event.

- `queue.ps1 -Audit [-Id x]` — read it, auto-passes flagged in words.
- `queue.ps1 -VerifyAudit [-Id x]` — is the trail COMPLETE? `0` complete, `1` findings,
  `7` there were items it could not audit (**not** a green).

"Complete" is defined executably in `gate-audit.ps1`: every gate an item crossed has a
record; every record names a principal and a kind; an auto record additionally names its
gate profile, the andon verdict that authorised it, **and that verdict's coverage** — a
record claiming `clear` with nothing evaluated is refused, so is one auto-passed on a
board missing a required condition (the finding names the ids), and so is one that cannot
state either at all.

Those are named checks, and each was added AFTER an outcome nobody had enumerated turned out
to mean "clear" - which is why the record also carries the board's **bucket census**, and
`-VerifyAudit` **re-derives** the verdict from it rather than reading the word: every
condition in exactly one bucket, the buckets summing to the conditions in scope, every bucket
but `evaluated_ok` empty, and at least one condition in it. The loop reads the buckets the
RECORD carries, so a bucket added to the board later is checked without editing the verifier.

**Ledger schema is 5** (`gate-audit.ps1` `$script:GateLedgerSchema`), and each number is a
question an older record cannot answer, reported as a finding rather than assumed clear:
schema 2 added `andon.repo` and the coverage counters; 3 added `missing` / `missing_ids` (a
thinned board reported full coverage of itself); 4 split `fired` from `halted` (a fire that
did not halt was in no field); 5 added `census` / `census_total` / `census_ids`, without
which the verdict cannot be re-derived at all, plus `looked_at`, which names each condition's
predicate and params so a `params` redirect is readable afterwards. This paragraph said
"schema 3" while the code was already at 4 - a doc number that drifts one release behind is a
reader's only clue that a record is old, so it is now stated once, beside what each number
buys. The item and the ledger must agree. Crossed gates are derived from the
ITEM'S OWN STATE, never from the ledger — that is what makes a missing record detectable
rather than invisible.

**What "complete" does not mean.** It is a statement about the gates those items
*reached*, never that the pipeline's gates were all enforced; `-VerifyAudit` prints that
scope with the green. With the shipped default `pipeline.anchor_required: true`, an item
past `anchor-draft` has crossed the anchor gate whether or not an anchor survives on it, so
an anchorless item is a finding. Set `anchor_required: false` and the anchor gate is not a
gate for an anchorless item — `-VerifyAudit` says so in words rather than quietly counting
a narrower complete.

The failure this is built against is an audit record that says a gate "passed" without
saying who or what passed it. That is worse than no record, because it reads as human
approval.

## Turning it off

| setting | effect |
|---|---|
| `enabled: false`, or `AI_STACK_HARNESS_ENABLED=0` | off everywhere |
| `surfaces.mattermost.enabled: false` | off for bridge sessions |
| `surfaces.extension.enabled: false` | off for editor-driven sessions |

Off means **inert, and it says why**. Every script entry point exits 2 with a
sentence naming the setting; the bridge's `profile:`, `worktree: on` and `release:`
directives answer with the same sentence instead of running anything. Nothing in
the ordinary workflow changes behaviour.

## Where the state lives

`<git-common-dir>/agent-worktrees/` — the `.git` directory of the MAIN checkout,
shared by every worktree. `AI_STACK_WORKTREE_STATE` redirects it, which
is how a drill runs against a scratch namespace instead of the live queue and
ledger.

This is load-bearing. It was originally anchored on the script's own location,
which is correct only while exactly one copy of the toolkit exists — and the whole
point is that copies exist inside worktrees. Each copy resolved its own gitignored
state directory, so two agents were each told they held a claim while excluding
nobody. A lock that reports success and protects nothing is worse than no lock,
because people trust it. `resolve.ps1` now throws rather than fall back.

## Removing the module

1. Delete `scripts/agent-harness/`.
2. Delete `documentation/implementation-guide/multi-agent-concurrency/` and
   `.claude/skills/merge-queue/`.
3. In `scripts/claude-sessions-bridge/bridge.py`, remove the `harness_config`
   import block below `WORKTREE_SCRIPTS`, `harness_off_reason()`, and the
   `profile:` / `worktree:` / `release:` directive branches.
4. Remove the worktree/profile columns from `scripts/claude-sessions-bridge/sessions.py`.
5. Drop the harness rows from `CLAUDE.md`.

State under `.git/agent-worktrees/` is disposable once no worktree holds unmerged
work — check with `git worktree list` before deleting it.

## Related

- [PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/PLAN.md) — why the pipeline replaced a merge lock
- [HARNESS-V2-PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/HARNESS-V2-PLAN.md) — anchors, runners, profiles, this boundary
- [MERGE-PROTOCOL.md](../../documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md) — the agent-facing protocol
- [README.md](README.md) — day-to-day usage and the gotchas found while building it
- [quadrant/MODULE.md](quadrant/MODULE.md) - the runner x target quadrant comparison (dark-factory U4)
