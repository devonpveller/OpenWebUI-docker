# Findings — U4, the little-coder runner dispatch (2026-08-30, `work/dfu-u4`)

Sink for the dark-factory-unification U4 item: build the dispatch layer the harness lacked,
settle the reachability disagreement in `harness.config.json`, and take A11 ("95% small-model
/ 5% frontier — UNTESTED and preserved") off the unproven list with one real anchored item.

Everything below was verified by running the named command in this session unless it says
otherwise.

**U4 IS PARKED, NOT DONE.** The §2 *Validated by* column is not satisfied and this branch does
not claim it is. The precise park is at the bottom ("The park, stated precisely").

---

## 0. Second round: what verification found in the FIRST round, and what it cost

The first round of this branch was refuted 2/2 and returned. What the verifiers confirmed as
real is unchanged and is listed in the evidence table below; what they found wrong is fixed
here, each with an executable proof that goes RED without the fix (section 8).

The through-line in every defect is one habit: **rounding a weaker true statement up to a
stronger false one.** "declared" became "exists"; "the mapping returns 3" became "the script
exits 3"; "a comment says callers take the lease" became "the lease is taken"; "describe_runner
is consumed" became "the dispatch layer is consumed"; "PARKED" became "GREEN" in a commit
subject. Each individual step is small. Every one of them makes an audit surface lie.

### Process defect, recorded so the merge record is accurate

The brief for this item said to create the worktree with `-Id u4disp`. The work was done on
`work/dfu-u4` in `wt-dfu-u4` instead. Both verifiers' first finding was that the branch they
were sent to review does not exist — "a reviewer following the brief literally would find
nothing to review". Nothing is being renamed now (the branch has history and sibling branches
reference it); the correct branch name for any record about this item is **`work/dfu-u4`**.

## 1. A11 was understated: resolution existed, dispatch did not

`scripts/agent-harness/config.ps1:187` `Resolve-RoleTarget` answers "role + profile → runner
and model". Nothing consumed the answer.

- `grep -rn 8090 scripts/agent-harness/*.ps1 *.py` → no hits.
- The only files naming `little-coder` were `harness.config.json`, `lease-names.conf`,
  `MODULE.md` and two test files.

So the config could *name* a local runner and the pipeline had no way to *run* one. PLAN.md
A11's "wired, `status: unproven`" is accurate about the config and optimistic about the
system. Fixed by `scripts/agent-harness/dispatch.ps1`.

## 2. The config named a door no compose file declared

`harness.config.json`'s little-coder runner declared `endpoint: "http://127.0.0.1:8090"`.

- The rendered compose publishes ONLY `('127.0.0.1','9091',9090)` for `little-coder` — the
  Prometheus metrics port. `curl http://127.0.0.1:8090/health` from the host is
  connection-refused.
- `docker exec little-coder curl -fsS http://localhost:8090/health` answers
  `{"status":"ok","version":"0.1.0",...}`.

**Decision (class 2; the DECISIONS.md entry is PENDING — staged at the bottom of this file for
the orchestrator to append at merge, since this branch does not touch DECISIONS.md): dispatch
over `docker exec`, and correct the config**, rather than publishing the port. The deciding
fact is in the daemon's own header (`little-coder/src/littlecoder/daemon.py` lines 1-7):
*"It is NOT the task-trigger authentication surface; that is `lc-mcpo`"* — and `lc-mcpo` was
retired 2026-08-20 (CLEANUP-PLAN v3 D-13). `POST /tasks` (arbitrary agent execution with
shell), `POST /project` and `POST /admin/shutdown` are therefore unauthenticated, protected
only by network placement. Publishing them on the host loopback would hand them to every
process on the workstation for no capability the harness needs; `docker exec` reaches the same
API across a boundary that already requires Docker access.

Revert path is in `harness.config.json`'s `_why_docker_exec` block and is a config flip plus
one compose line — `dispatch.ps1`'s `Invoke-LcApi` implements the `http` transport already.

> An earlier revision of `dispatch.ps1` and of this note cited this as "logged in DECISIONS.md
> as U4-1". It was not logged anywhere: `DECISIONS.md` is untouched by this branch. A citation
> to a record that does not exist is worse than no citation, because it reads as checkable.

## 3. DECLARED is not EXISTS, and this host proves it

`test_harness_config.py`'s `_door_problems` is a **substring match over the text of
`coder/docker-compose.yml` and `./docker-compose.yml`**. It proves a door is DECLARED. The
first round's `MODULE.md` said it "asserts that a declared transport corresponds to a door
that actually exists"; the report to the orchestrator repeated it ("a REAL published port or a
REAL container_name"). Both were wrong, and the two claims genuinely come apart here:

    $ grep -n -A1 "ports:" coder/docker-compose.yml
    101:      - "127.0.0.1:9091:9090"   # Prometheus metrics (loopback only)

    $ docker port little-coder
    (prints nothing)

    $ docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'
    {"9090/tcp":[]}

The compose file DECLARES a published metrics port; the RUNNING container publishes nothing,
because it predates that declaration and was never recreated. A static check over compose text
can never see that.

Fixed two ways: the prose in `MODULE.md` and in the test module now says DECLARED and says
what it does not prove; and `verify-dispatch.ps1`'s live section — **which now runs by
default** — probes the runtime (`docker inspect -f {{.State.Running}}`, then a real
`/health` over the declared transport).

Not fixed, and deliberately not: the drift itself. `docker compose -f coder/docker-compose.yml
up -d little-coder` would recreate the container and publish 9091 — but the dispatch path uses
`docker exec`, nothing consumes the metrics port today, and recreating a live container is not
part of this item. Recorded here so the next person does not rediscover it.

## 4. `python3 -m pytest ... | tail -5` returns exit 0 with pytest not installed

Observed in the little-coder agent's own activity log during the U4 item (task
`01M19JABFNQHR7CPDGCPDK2VEV`): the agent ran

    cd /workspace && python3 -m pytest scripts/agent-harness/test_harness_config.py -q 2>&1 | tail -5

twice and got `exit_code: 0` both times. `pytest` is **not installed in `open-terminal`**
(`python3 -c "import pytest"` → ModuleNotFoundError; Python 3.12.13, git 2.47.3, no node).
The pipeline's exit code is `tail`'s, so a completely absent test runner reads as a pass.

This is the "a check that passes while checking nothing" class CLAUDE.md warns about, arriving
from a new direction: not a bad assertion, a swallowed exit code. It is why the U4 item's
acceptance command is a bare `python3 -c "...assert..."` with no pipe — the daemon's
`_verdict` (`little-coder/src/littlecoder/agent.py:496`) grades on the REAL exit code.

**Worth building:** a lint that rejects `| tail`/`| head` on the right of a test invocation in
acceptance commands and CI scripts, or `set -o pipefail` where the shell allows it. Not built
here — out of scope for U4 and it needs a survey of existing call sites first.

## 5. The local runner cannot PUSH to this repo — `LC_DEPLOY_TOKEN` gets 403

The largest finding of the item, and it did not surface until the work was done.

Task `01M19JABFNQHR7CPDGCPDK2VEV` implemented the function, ran the acceptance command
(exit 0), and committed `247bac7` in its workspace. Its push failed:

    git push origin HEAD
    remote: Permission to devonpveller/openwebui-docker.git denied to devonpveller.
    fatal: unable to access '.../openwebui-docker/': The requested URL returned error: 403

`LC_DEPLOY_TOKEN` is present in the container (93 chars) and the clone read fine, so the
credential exists and is read-scoped for this repo — it has no write. Nothing in the harness
or the compose file says so; the failure is only visible in a task's activity log.

Two compounding problems:

1. **The daemon's verdict was `pass` while delivery had failed.** `_verdict`
   (`little-coder/src/littlecoder/agent.py:496`) grades on the acceptance command alone. The
   acceptance command tested the CODE, which was correct — so a correct verdict coexisted
   with an undelivered artifact. Exactly the shape of
   [[fix-delivery-not-observability]]: an unattended item's success criterion is the ARTIFACT.
2. **The agent's own retry masked the failure**: it ran
   `git push origin HEAD 2>&1; echo "exit=$?"`, which journals `exit_code: 0` because the
   *last* command in the list is `echo`. Same exit-code-swallowing family as finding 4, from a
   different direction. Had nobody checked the remote, the run would read as a clean pass.

**Delivery path actually used**, and the one to codify: `git bundle` from the *control plane*
container (`little-coder`, which holds the real git — `open-terminal`'s git-proxy hard-denies
`git bundle`, "not-whitelisted"), `docker cp` it to the host, `git fetch <bundle>`,
`git merge --no-ff`. The bundle carries the agent's real commit and authorship
(`little-coder <little-coder@ai-stack.local>`), so the record is not laundered through a
re-commit. Note the bundle needs a REF, not a range: `git bundle create f <branch> ^<base>`;
`f a..b` fails "Refusing to create empty bundle".

**Not built here** (parked, with a reason): a `-FetchInto` on `dispatch.ps1` that does the
bundle round-trip automatically, and a delivery assertion so a `pass` verdict cannot outlive a
failed push. Both are real work; both are bigger than the U4 slice and belong with the
"outcomes compared per quadrant" half. Building them now would have meant shipping an
untested delivery path in the same commit as the proof that the path is needed.

## 6. `little-coder`'s workspace model constrains what "target: self" can mean

The daemon's workspace is a clone of a **git-host URL** (`normalize_repo_url` rejects local
paths and `file://` — it requires host + owner/repo, `little-coder/src/littlecoder/
urlnorm.py:47`). There is no bind mount from a harness worktree into the container.

So a little-coder runner cannot work *in* a `.claude/worktrees/wt-*` directory. The path that
does work, and the one this item used, is: push the branch → focus the daemon on
`<remote>#<branch>` → the agent works in its own clone → it pushes → the host fetches. PLAN.md
§L3's `target: self` ("worktrees off the operator's loaded branch") is therefore **not**
available to the local runner as written; what is available is `target: project(self-remote)`.

Two consequences worth recording before U6 assumes otherwise:

- A local-runner item costs a **branch round-trip through the remote**. It is not free and it
  is not offline.
- Focusing the daemon **wipes the workspace** (`SwitchAction.SWITCH` → `workspace.wipe`), so it
  is a live-plane mutation and needs the `coder` lease. This session took it by hand
  (`lease.ps1 -Acquire -Name coder -Owner dfu-u4`) and restored the prior focus
  (`https://github.com/anthropics/skills`) afterwards. The code did not require it — see
  finding 7.

## 7. A lease claim with no lease — now an assertion

`dispatch.ps1`'s `Set-LcFocus` carried the comment *"this is a live-plane mutation: callers
take the coder lease"*. Nothing acquired a lease, nothing asserted one, there were no callers,
and the function wipes the runner's workspace. The comment described an intention as if it
were a mechanism.

Fixed by making it one:

- `harness.config.json`'s little-coder runner declares `"lease": "coder"` (policy in the
  config, not in the dispatcher; a runner with no `lease` key needs none).
- `dispatch.ps1`'s `Assert-RunnerLease` runs BEFORE the focus wipe and before the submit, and
  requires the lease to be **held by this caller** — `-LeaseOwner <id>` or
  `AI_STACK_LEASE_OWNER`. A lease held by another agent must block the dispatch, not satisfy
  it; an assertion that accepted anyone's lease would pass exactly when it matters least.
- `Get-HeldLease` / `Get-LeaseDir` live in `resolve.ps1` (the policy file that already owns
  "where shared state lives"), and `lease.ps1` now reads its lock dir from `Get-LeaseDir`, so
  the two cannot drift onto different directories.
- `test_harness_config.py` asserts the declared lease name is one `lease-names.conf` knows —
  a typo there would make the runner undispatchable by anyone following the documented path,
  because `lease.ps1` refuses unknown names without `-AdHoc`.

## 8. The vacuity battery: five guards, each proved RED before GREEN

The first round's drill printed "31/31 checks passed" while three separate deletions left it
fully green, and its only real-transport coverage was opt-in behind `-Live` — so the default
run had none at all. Each of the following was run by deleting the named line(s) from
`dispatch.ps1`, running the drill, restoring, and running it again.

| Mutation | Old drill | New drill (`-Offline`, 51 checks) |
|---|---|---|
| delete `$body["acceptance_command"] = $AcceptanceCommand` | green | **49/51** — "the acceptance command reached the wire"; "its POST body crossed a real socket carrying the acceptance command" |
| replace the script tail `exit (Get-DispatchExitCode $res)` with `exit 0` | green | **50/51** — "the child process actually exits 3 on a failed acceptance command exit=0" |
| delete the post-terminal event drain in `Wait-LcTask` | green | **48/51** — event count 2 not 3, and "including the one only the post-terminal drain can see" |
| delete the `Assert-RunnerLease` call | n/a (no guard existed) | **45/51** — six lease checks, incl. "a child with no lease exits 1 and submits nothing exit=0 requests=5" |
| map not-ok to exit 4 (the header's old false claim, made real) | n/a | **48/51** — "'abandoned' exits 1, not 4" |

Two of those were only reachable by a new test layer:

- **Layer B — a real child process over a real socket.** Everything the old drill did called
  `Invoke-HarnessTask` in-process and read `Get-DispatchExitCode`'s RETURN VALUE. Script
  mode's `exit` line was never executed by anything. The drill now runs `dispatch.ps1` under
  `powershell.exe` against a `TcpListener` it operates itself (a temp `AI_STACK_HARNESS_CONFIG`
  points the runner at that socket with `transport: http`), and reads `$proc.ExitCode`. This
  also exercises the `http` transport branch — the documented revert path — end to end, and
  proves the acceptance command crosses a real wire.
  Gotcha found while building it: `Start-Process -PassThru` returns a process whose `ExitCode`
  reads `$null` after exit unless `.Handle` was touched while it was alive. That would have
  made every exit-code check silently vacuous — the exact failure class this section exists to
  remove. Second gotcha: `Start-Process -ArgumentList` quotes nothing, so any path containing
  a space (this repo lives under `D:\Open WebUI`) arrives split in two.
- **Layer C — the live daemon, now DEFAULT ON.** `-Live` is accepted and ignored; `-Offline`
  is the opt-out, and the summary line states coverage every run:
  `real transport: COVERED (real daemon over docker-exec, version 0.1.0)` or
  `NOT COVERED (-Offline was passed)`. A green count that excludes the only real-transport
  coverage is the failure class this whole effort exists to kill.

Current state: **54/54 with the live section, 51/51 with `-Offline`.**

## 9. Running `verify-merge-protocol.ps1` DETACHED THE OPERATOR'S MAIN CHECKOUT

Found by running it (as a regression check, because this branch touches `lease.ps1`), and
worth its own entry because the blast radius is the shared checkout, not a worktree.

- The drill is **RED on the work line already**: 45/66 with this branch's changes reverted,
  44/66 with them, so it is not this branch's doing. The failures are queue/anchor/test-plan
  semantics (`-Submit is NOT blocked by the inactive guard`, `only the DEVELOPER may re-submit
  their item`, ...) plus its own cleanup checks.
- **It rebases inside `D:\Open WebUIi-stack` itself.** `git reflog` in the main checkout:
  `rebase (start): checkout drill/verify-d` -> `rebase (pick): ...`. One run aborted cleanly
  and returned to `refs/heads/refactor/ai-stack-cleanup`; the next left the checkout
  **detached at 56f30cb with an interactive rebase in progress, 154 commits still queued**.
  The operator's checkout was on `refactor/ai-stack-cleanup`, and any session opening it in
  that state would have been working on a detached HEAD mid-rebase.
  Repaired here: `git rebase --abort` -> `On branch refactor/ai-stack-cleanup`, `98cf02e`,
  which is exactly where the reflog says it was before the drill.
- **It cannot clean up its own worktrees in this repo.** `git worktree remove
  .claude/worktrees/wt-drillb` -> `fatal: working trees containing submodules cannot be moved
  or removed`. That is why its own "all drill worktrees gone" check fails on every run. Left
  behind by my runs and NOT removed (the removal needs a permission this session does not
  have): worktree `.claude/worktrees/wt-drillb` (+ the stale dir `wt-drilla`) and branches
  `work/drillb`, `drill/verify-d`, all created 2026-08-30 12:21 EDT. `remove-worktree.ps1` is
  the harness's own tool for this and is the thing to use.

Not fixed here - it is a different module and a different item. But it is a live hazard:
a drill that mutates the shared checkout and leaves it mid-rebase is more dangerous than the
gap it is testing, and nothing warns you before you run it.

## 10. Pre-existing: `ruff check scripts/agent-harness/` was RED on the work line

`test_anchor_schema.py` re-imported `shutil` and `subprocess` mid-file (lines 266-267) while
already importing both at line 18-19 — F811, and `ruff check .` is the repo's lint gate
(CLAUDE.md). Present on the base commit `5f4817d`, so not introduced here. Removed in this
branch (one dead line) because a lint gate that is already red cannot be cited as evidence for
anything else, which is what this item needed it for. Reverting is `git revert` of that hunk.

---

## The A11 evidence, in one place

Claim under test (PLAN.md §0 A11): *"95% small-model search / 5% frontier oracle-on-stall —
UNTESTED and preserved. The harness ran 100% frontier."*

What now exists, all reproducible:

| | evidence |
|---|---|
| A dispatch path exists | `scripts/agent-harness/dispatch.ps1`; `verify-dispatch.ps1` **54/54**, including a real child process over a real socket and a probe of the real daemon |
| The declared transport is the real one | `verify-dispatch.ps1` (default run): `docker inspect little-coder` → running, reachable over `docker-exec`, version 0.1.0. The compose-text door check is a separate, weaker claim — see finding 3 |
| A real item completed on the local runner | task `01M19JABFNQHR7CPDGCPDK2VEV`, repo `devonpveller/openwebui-docker#work/dfu-u4-lc`, model `qwen36-27b` (`little-coder.config.yaml`: `model: llamacpp/qwen36-27b` via `http://llama-cpp:8080/v1`), 7 commands, outcome **pass**, signal "acceptance command exit 0" |
| How long it took | **83s** — the daemon record says `created_ts 2026-08-30T14:49:00.661Z`, `ended_ts 2026-08-30T14:50:23.770Z`. The first round's commit message said "88s"; that number was not from the record |
| The agent did not grade itself | the daemon ran the acceptance command (`agent.py:_verdict`); the command was written before dispatch and proved RED first (`/check` → exit 1, AttributeError) |
| The change is real and durable | commit `247bac7` by `little-coder <little-coder@ai-stack.local>`, merged as `8a3539b`; host suite went 3 failed/15 passed → **18 passed**; whole module **115 passed**, `ruff` clean |
| What is consumed | `describe_runner()` — the function the local model wrote — is rendered by `bridge.py`'s `profile: list`, so an operator switching a thread sees `little-coder: model=local-default, transport=docker-exec, status=unproven`. **`dispatch.ps1` itself has NO consumer**: `queue.ps1` does not call `Invoke-HarnessTask` and nothing else does. The first round said "the function is consumed, not shelved" — true of `describe_runner`, and it was allowed to read as if it were true of the dispatch layer |

**Verdict: A11 moves from UNTESTED to PARTIALLY PROVEN.** One item, one quadrant
(local runner × self-remote target), specified by a frontier agent, delivered by hand.
The `status: "unproven"` field in `harness.config.json` is deliberately LEFT AS IS — one
successful item does not make a substrate proven, and the honest listing above is worth
more than a flipped flag.

---

## The park, stated precisely

**U4 does not merge as done. It parks (§C.7: "A phase that cannot satisfy its column does not
merge. It parks with a written reason").**

**What IS proven, by a named executable check:**

- A dispatch layer exists and behaves as documented: `verify-dispatch.ps1` 54/54, five of its
  guards proved RED against deliberate deletions (section 8).
- The declared transport reaches the real daemon, checked on every default run.
- One real anchored item ran end to end on the local runner and produced a real commit graded
  by a command the agent did not run itself (evidence table above). n=1.

**What is NOT proven, and would not be by anything on this branch:**

- **The §2 U4 *Validated by* column, in full.** It requires "the same anchored item run per
  quadrant (runner × target), outcomes compared; stall→oracle observed firing at least once".
  One quadrant was run. No comparison exists. No oracle-on-stall path exists at all —
  `dispatch.ps1` returns exit 1 with `status: timeout` and escalates to nobody.
- **The 95/5 ratio.** One data point is not a ratio. It also came with a caveat that matters:
  the RED test, the acceptance command and the prompt were written by a frontier agent. What
  the local runner demonstrated is "implement to a precise executable spec", not "take an item
  end to end".
- **The second half of the phase**, "agent-org workers as harness runners and vice versa —
  one profile mechanism governs both": untouched. And the profile mechanism still does not
  govern the harness's own execution — `dispatch.ps1` has no pipeline consumer, so selecting
  a profile still changes nothing about what actually runs unless a human invokes dispatch.
- **Delivery.** Finding 5: the local runner cannot push to this repo (403), so the artifact
  came back by a hand-driven `git bundle`. A `pass` verdict still cannot see whether the work
  reached the branch.

**Why the runner axis is probably unmeetable as written, right now.** The column's quadrant
comparison needs the same item run on `claude-code × target` and `little-coder × target`. The
little-coder side costs a branch round-trip through a remote it cannot push to (findings 5 and
6), so every local-runner item currently needs a human in the delivery step — which is exactly
what a quadrant comparison is supposed to be measuring. The honest order is: fix delivery
first, then compare.

**What would meet it**, in dependency order:

1. A write-capable credential (or a bundle-based `-FetchInto` in `dispatch.ps1`) so a
   local-runner item delivers its own artifact, plus a delivery assertion so a `pass` verdict
   cannot outlive a failed push.
2. A consumer: `queue.ps1` dispatching a worker/tester role through `Invoke-HarnessTask`, so
   the profile actually governs execution.
3. An oracle-on-stall path: `status: timeout` (or repeated `fail`) escalating the same item to
   the frontier runner, with the escalation visible in the audit record.
4. Then the quadrant run, with outcomes compared — which is a measurement, not a build.

**Consequence for U3:** the 2026-08-30 U3 entry parks its seeded-regression gym run "at U4's
quadrants". That did not happen, so U3 stays validation-parked. The debt did not move.

---

## DECISIONS entries to append

```
## 2026-08-30 · U4 · class 2
DECISION: The little-coder runner is dispatched over `docker exec`, NOT over a
          published host port. `harness.config.json`'s little-coder runner
          declared endpoint `http://127.0.0.1:8090`; no compose file declares
          that door - `coder/docker-compose.yml` publishes only
          `127.0.0.1:9091->9090` (Prometheus). Replaced `endpoint` with
          `transport: docker-exec` + `container` + a container-local `base_url`,
          and built the dispatcher around it
          (`scripts/agent-harness/dispatch.ps1`).
          The deciding fact is the daemon's own header: "It is NOT the
          task-trigger authentication surface; that is lc-mcpo"
          (`little-coder/src/littlecoder/daemon.py:1-7`) - and lc-mcpo was
          retired 2026-08-20. `POST /tasks` (arbitrary agent execution),
          `POST /project` and `POST /admin/shutdown` are unauthenticated;
          publishing them on the host loopback hands them to every process on
          the workstation for no capability the harness needs. `docker exec`
          reaches the same API across a boundary that already requires Docker
          access.
CITED:    §C.2 class 2 - two defensible designs; took the more reversible one,
          closest to an existing boundary. §C.7 - the disagreement between the
          config and reality had to be resolved, not documented.
REVERT:   Set `runners.little-coder.transport` to `"http"` and `base_url` to
          `"http://127.0.0.1:8090"`, and add `"127.0.0.1:8090:8090"` to
          little-coder's `ports` in `coder/docker-compose.yml`. `Invoke-LcApi`
          already implements the http transport; the rationale and this revert
          path are also in `harness.config.json`'s `_why_docker_exec`.

## 2026-08-30 · U4 · class 2
DECISION: A runner record may declare `lease: <name>`, and `dispatch.ps1`
          REFUSES to dispatch unless that lease is held BY THE CALLER
          (`-LeaseOwner` / `AI_STACK_LEASE_OWNER`). little-coder declares
          `coder`. Replaces a comment that claimed "callers take the coder
          lease" while nothing acquired, asserted or checked one - and the
          function it sat on wipes the runner's workspace.
          Someone else's lease refuses the dispatch exactly as a free one does;
          an assertion that accepted any holder would pass when it matters
          least. `Get-HeldLease`/`Get-LeaseDir` were added to `resolve.ps1` and
          `lease.ps1` now reads its lock dir from `Get-LeaseDir`, so the two
          cannot drift onto different directories.
CITED:    §C.2 class 2 (policy belongs in the config file, closest to the
          existing lease primitive). §C.7 / §0 A6 - a prose claim of a guard is
          not a guard.
REVERT:   Delete the `"lease"` key from `runners.little-coder` in
          `harness.config.json`: `Assert-RunnerLease` returns immediately for a
          runner that declares none, so no code change is needed. Removing the
          mechanism entirely is one call site in `Invoke-HarnessTask` plus the
          function, and turns 6 drill checks and 2 pytest checks red.

## 2026-08-30 · U4 · class 2
DECISION: `verify-dispatch.ps1`'s live probe runs BY DEFAULT; `-Live` is
          accepted and ignored, `-Offline` is the opt-out, and the summary
          always prints whether the real transport was covered. It was opt-in,
          so the default run reported "31/31 checks passed" with zero coverage
          of the only transport that ships. The drill also gained a real
          child-process layer (dispatch.ps1 under powershell.exe against a
          TcpListener) because in-process checks cannot reach script mode's
          `exit` line at all.
CITED:    §C.7 - only an executable check closes anything, and a check that
          excludes the real path is the vacuity this effort exists to kill.
REVERT:   `-Offline` restores the old default behaviour per-run; restoring
          opt-in permanently is inverting one `if` in the LAYER C block.

## 2026-08-30 · U4 · class 2
DECISION: `runners.little-coder.status` stays `"unproven"` even though a real
          item completed through it. One item, one quadrant, a spec written by
          a frontier agent, and a delivery step done by hand is a first data
          point, not a proven substrate. The evidence table in
          `documentation/notes/dfu-u4-findings.md` says exactly what was shown;
          flipping a flag would say more than that.
CITED:    §C.7 (a phase closes on an executable check, and the audit trail must
          be true) - "code-complete" is not a synonym for done, and neither is
          "it worked once".
REVERT:   One word in `harness.config.json` once U4's quadrant comparison and
          the oracle-on-stall path land.

## 2026-08-30 · U4 · class 2
DECISION: Removed a dead mid-file `import shutil` / `import subprocess` from
          `scripts/agent-harness/test_anchor_schema.py` (both already imported
          at line 18-19). It is pre-existing on the base commit `5f4817d` and
          not part of this item, but it made `ruff check scripts/agent-harness/`
          RED, and this item needed to cite that gate as evidence.
CITED:    §C.2 class 2 (a discovered gap, most reversible option) - and the
          CLAUDE.md rule that findings go to notes: recorded there as finding 10
          rather than left silent.
REVERT:   `git revert` that hunk; it is one deleted line plus a comment.

## 2026-08-30 · U4 · CORRECTION
DECISION: Commit 2151193's SUBJECT read "U4 GREEN: ...". Its body, its PLAN.md
          edit and its findings note all correctly said the Validated by column
          was NOT satisfied - but under §C.7 the commit message is an audit
          surface the operator reads INSTEAD of the diff, and the one-line
          subject is the part that survives. "GREEN" is a done-word on a parked
          phase, and DECISIONS.md had adopted the governing rule one day
          earlier: a phase is reported DONE only when its Validated by column is
          satisfied and the evidence is named; otherwise PARKED.
          Corrected by a follow-up commit on `work/dfu-u4` rather than a
          rewrite, so the error and its correction both stay in the record.
          Also corrected in that commit: "7 commands, 88s" - the daemon record
          gives 83s (created 14:49:00.661Z, ended 14:50:23.770Z).
CITED:    §C.7 (the audit trail is the deliverable's twin, so it must be true);
          the 2026-08-30 DONE-vs-PARKED entry.
REVERT:   n/a - a correction to the record.

## 2026-08-30 · U4 · PROCESS
DECISION: The item's brief named worktree id `u4disp`; the work was done on
          `work/dfu-u4` in `wt-dfu-u4`. Both verifiers' first finding was that
          the branch named in their brief does not exist. Nothing is renamed
          (the branch has history and siblings reference it) - the merge record
          should name `work/dfu-u4`. Rule for the next dispatch: the branch name
          in a verifier's brief is copied from the builder's actual worktree,
          not from the id the builder was asked to use.
REVERT:   n/a - a record.

## 2026-08-30 · U4 · STATUS
STATUS:   **U4 = PARKED. NOT DONE.**
          Satisfied: "prove little-coder through one real anchored item end to
          end (the standing unproven claim, A11)" - task
          `01M19JABFNQHR7CPDGCPDK2VEV`, outcome pass on an acceptance command
          the daemon ran, commit `247bac7` merged as `8a3539b`, host suite
          3 failed/15 passed -> 18 passed. n=1.
          NOT satisfied, and therefore parked with a written reason:
          - §2's U4 *Validated by* column ("same anchored item run per quadrant
            (runner x target), outcomes compared; stall->oracle observed firing
            at least once"). One quadrant was run; no comparison exists; no
            oracle-on-stall path exists - `dispatch.ps1` reports
            `status: timeout` and escalates to nobody.
          - "one profile mechanism governs both": still false in BOTH
            directions. `dispatch.ps1` has no consumer in `queue.ps1` or
            anywhere else, so selecting a profile still changes nothing about
            what runs unless a human invokes dispatch by hand.
          - agent-org workers as harness runners and vice versa: untouched.
          - CONSEQUENCE FOR U3: the 2026-08-30 U3 entry parks its seeded-
            regression gym run "at U4's quadrants". That did not happen, so
            U3 stays validation-parked; the debt did not move.
          Blocking finding for the quadrant work: the local runner cannot push
          to this repo (`LC_DEPLOY_TOKEN` -> 403), so every local-runner item
          needs a delivery path the harness does not yet own (finding 5). The
          ordered list of what would meet the column is in "The park, stated
          precisely" in the findings note.
REVERT:   n/a - a status record.
```
