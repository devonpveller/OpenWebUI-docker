# Findings — U4, the little-coder runner dispatch (2026-08-30, `work/dfu-u4`)

Sink for the dark-factory-unification U4 item: build the dispatch layer the harness lacked,
settle the reachability disagreement in `harness.config.json`, and take A11 ("95% small-model
/ 5% frontier — UNTESTED and preserved") off the unproven list with one real anchored item.

Everything below was verified by running the named command in this session unless it says
otherwise.

---

## 1. A11 was understated: resolution existed, dispatch did not

`scripts/agent-harness/config.ps1:187` `Resolve-RoleTarget` answers "role + profile → runner
and model". Nothing consumed the answer.

- `grep -rn 8090 scripts/agent-harness/*.ps1 *.py` → no hits.
- The only files naming `little-coder` were `harness.config.json`, `lease-names.conf`,
  `MODULE.md` and two test files.

So the config could *name* a local runner and the pipeline had no way to *run* one. PLAN.md
A11's "wired, `status: unproven`" is accurate about the config and optimistic about the
system. Fixed by `scripts/agent-harness/dispatch.ps1`.

## 2. The config named a door that did not exist

`harness.config.json`'s little-coder runner declared `endpoint: "http://127.0.0.1:8090"`.

- The rendered compose publishes ONLY `('127.0.0.1','9091',9090)` for `little-coder` — the
  Prometheus metrics port. `curl http://127.0.0.1:8090/health` from the host is
  connection-refused.
- `docker exec little-coder curl -fsS http://localhost:8090/health` answers
  `{"status":"ok","version":"0.1.0",...}`.

**Decision (class 2, DECISIONS U4-1): dispatch over `docker exec`, and correct the config**,
rather than publishing the port. The deciding fact is in the daemon's own header
(`little-coder/src/littlecoder/daemon.py` lines 1-7): *"It is NOT the task-trigger
authentication surface; that is `lc-mcpo`"* — and `lc-mcpo` was retired 2026-08-20
(CLEANUP-PLAN v3 D-13). `POST /tasks` (arbitrary agent execution with shell), `POST /project`
and `POST /admin/shutdown` are therefore unauthenticated, protected only by network placement.
Publishing them on the host loopback would hand them to every process on the workstation for
no capability the harness needs; `docker exec` reaches the same API across a boundary that
already requires Docker access.

Revert path is in `harness.config.json`'s `_why_docker_exec` block and is a config flip plus
one compose line — `dispatch.ps1`'s `Invoke-LcApi` implements the `http` transport already.

## 3. `python3 -m pytest ... | tail -5` returns exit 0 with pytest not installed

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

## 4. The local runner cannot PUSH to this repo — `LC_DEPLOY_TOKEN` gets 403

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
   *last* command in the list is `echo`. Same exit-code-swallowing family as finding 3, from a
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

## 5. `little-coder`'s workspace model constrains what "target: self" can mean

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
  is a live-plane mutation and needs the `coder` lease. This session took it
  (`lease.ps1 -Acquire -Name coder -Owner dfu-u4`) and restored the prior focus
  (`https://github.com/anthropics/skills`) afterwards.

## 6. Pre-existing: `ruff check scripts/agent-harness/` was RED on the work line

`test_anchor_schema.py` re-imported `shutil` and `subprocess` mid-file (lines 266-267) while
already importing both at line 18-19 — F811, and `ruff check .` is the repo's lint gate
(CLAUDE.md). Present on the base commit `5f4817d`, so not introduced here. Removed in this
branch (one dead line) because a lint gate that is already red cannot be cited as evidence for
anything else, which is what this item needed it for. Reverting is `git revert` of that hunk.

## 7. What is NOT proven by this item

Stated plainly so nobody reads more into the result than it carries.

- **The 95/5 split is not measured.** One item completed on the local runner. That is A11's
  first data point, not a demonstration that 95% of work can run there. PLAN.md U4's second
  half (same item per runner × target quadrant, outcomes compared) and the
  frontier-oracle-on-stall wiring are untouched — see the parked list in the handoff.
- **The item was scoped and specified by a frontier agent.** The RED test, the acceptance
  command and the prompt were written here; the local model implemented against them. That is
  the honest shape of the 95/5 split, but it means the local runner's demonstrated capability
  is "implement to a precise executable spec", not "take an item end to end".
- **No stall/oracle path exists yet.** `dispatch.ps1` returns exit 1 with `status: timeout`
  when a task does not terminate. Nothing escalates it to a frontier runner. That is U4's
  remaining half.
- **Delivery is manual.** See finding 4: the bundle round-trip was driven by hand in this
  session. `dispatch.ps1` does not do it, and a `pass` verdict still cannot see whether the
  work reached the branch.

---

## The A11 evidence, in one place

Claim under test (PLAN.md §0 A11): *"95% small-model search / 5% frontier oracle-on-stall —
UNTESTED and preserved. The harness ran 100% frontier."*

What now exists, all reproducible:

| | evidence |
|---|---|
| A dispatch path exists | `scripts/agent-harness/dispatch.ps1`; `verify-dispatch.ps1` 33/33 including a live probe |
| The declared transport is the real one | `verify-dispatch.ps1 -Live`: reachable over `docker-exec`, version 0.1.0; `test_harness_config.py` door check + 3 negative cases |
| A real item completed on the local runner | task `01M19JABFNQHR7CPDGCPDK2VEV`, repo `devonpveller/openwebui-docker#work/dfu-u4-lc`, 7 commands, 88s, outcome **pass**, signal "acceptance command exit 0" |
| The agent did not grade itself | the daemon ran the acceptance command (`agent.py:_verdict`); the command was written before dispatch and proved RED first (`/check` → exit 1, AttributeError) |
| The change is real and durable | commit `247bac7` by `little-coder <little-coder@ai-stack.local>`, merged as `8a3539b`; host suite went 3 failed/15 passed → **18 passed**; whole module 113 passed, ruff clean |
| It is consumed, not shelved | `bridge.py`'s `profile: list` now renders a **Runners** section, so an operator switching a thread sees `little-coder: model=local-default, transport=docker-exec, status=unproven` |

**Verdict: A11 moves from UNTESTED to PARTIALLY PROVEN.** One item, one quadrant
(local runner × self-remote target), specified by a frontier agent, delivered by hand.
The `status: "unproven"` field in `harness.config.json` is deliberately LEFT AS IS — one
successful item does not make a substrate proven, and the honest listing above is worth
more than a flipped flag.

---

## DECISIONS entries to append

```
## 2026-08-30 · U4 · class 2
DECISION: The little-coder runner is dispatched over `docker exec`, NOT over a
          published host port. `harness.config.json`'s little-coder runner
          declared endpoint `http://127.0.0.1:8090`; that door does not exist —
          `coder/docker-compose.yml` publishes only `127.0.0.1:9091->9090`
          (Prometheus). Replaced `endpoint` with `transport: docker-exec` +
          `container` + a container-local `base_url`, and built the dispatcher
          around it (`scripts/agent-harness/dispatch.ps1`).
          The deciding fact is the daemon's own header: "It is NOT the
          task-trigger authentication surface; that is lc-mcpo"
          (`little-coder/src/littlecoder/daemon.py:1-7`) — and lc-mcpo was
          retired 2026-08-20. `POST /tasks` (arbitrary agent execution),
          `POST /project` and `POST /admin/shutdown` are unauthenticated;
          publishing them on the host loopback hands them to every process on
          the workstation for no capability the harness needs. `docker exec`
          reaches the same API across a boundary that already requires Docker
          access.
CITED:    §C.2 class 2 — two defensible designs; took the more reversible one,
          closest to an existing boundary. §C.7 — the disagreement between the
          config and reality had to be resolved, not documented.
REVERT:   Set `runners.little-coder.transport` to `"http"` and `base_url` to
          `"http://127.0.0.1:8090"`, and add `"127.0.0.1:8090:8090"` to
          little-coder's `ports` in `coder/docker-compose.yml`. `Invoke-LcApi`
          already implements the http transport; the rationale and this revert
          path are also in `harness.config.json`'s `_why_docker_exec`.

## 2026-08-30 · U4 · class 2
DECISION: `runners.little-coder.status` stays `"unproven"` even though a real
          item completed through it. One item, one quadrant, a spec written by
          a frontier agent, and a delivery step done by hand is a first data
          point, not a proven substrate. The evidence table in
          `documentation/notes/dfu-u4-findings.md` says exactly what was shown;
          flipping a flag would say more than that.
CITED:    §C.7 (a phase closes on an executable check, and the audit trail must
          be true) + the 2026-08-30 process entry above — "code-complete" is
          not a synonym for done, and neither is "it worked once".
REVERT:   One word in `harness.config.json` once U4's quadrant comparison and
          the oracle-on-stall path land.

## 2026-08-30 · U4 · class 2
DECISION: Removed a dead mid-file `import shutil` / `import subprocess` from
          `scripts/agent-harness/test_anchor_schema.py` (both already imported
          at line 18-19). It is pre-existing on the base commit `5f4817d` and
          not part of this item, but it made `ruff check scripts/agent-harness/`
          RED, and this item needed to cite that gate as evidence.
CITED:    §C.2 class 2 (a discovered gap, most reversible option) — and the
          CLAUDE.md rule that findings go to notes: recorded there as finding 6
          rather than left silent.
REVERT:   `git revert` that hunk; it is one deleted line plus a comment.

## 2026-08-30 · U4 · STATUS
STATUS:   **U4 = PARTIALLY DONE, REMAINDER PARKED.**
          Satisfied: "prove little-coder through one real anchored item end to
          end (the standing unproven claim, A11)" — task
          `01M19JABFNQHR7CPDGCPDK2VEV`, outcome pass on an acceptance command
          the daemon ran, commit `247bac7` merged as `8a3539b`, host suite
          3 failed/15 passed → 18 passed.
          NOT satisfied, and therefore parked with a written reason:
          - §2's U4 *Validated by* column ("same anchored item run per quadrant
            (runner × target), outcomes compared; stall→oracle observed firing
            at least once"). No quadrant comparison was run and no oracle-on-
            stall path exists — `dispatch.ps1` reports `status: timeout` and
            escalates to nobody.
          - agent-org workers as harness runners and vice versa: untouched.
          - CONSEQUENCE FOR U3: the 2026-08-30 U3 entry parks its seeded-
            regression gym run "at U4's quadrants". That did not happen here, so
            U3 stays validation-parked; the debt did not move.
          Blocking finding for the quadrant work: the local runner cannot push
          to this repo (`LC_DEPLOY_TOKEN` → 403), so every local-runner item
          needs a delivery path the harness does not yet own. See finding 4.
REVERT:   n/a — a status record.
```
