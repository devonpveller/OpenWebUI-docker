# Findings — U4: the runner x target quadrant comparison (2026-08-30)

Built on `work/u4quad`. Deliverable: `scripts/agent-harness/quadrant/` (see its
`MODULE.md`), 34 tests in `scripts/agent-harness/test_quadrant.py`, and a mutation drill
`quadrant/prove_guards.py`.

## DECISIONS entries to append

### 2026-08-30 · U4 · class 2 — the comparison is scored from EVIDENCE, not from status fields
DECISION: A quadrant run record enters the comparison only if artifacts on disk attest it:
          non-zero wall clock, evidence paths that EXIST, and acceptance entries carrying
          the command and its integer exit code, with `passed` not contradicting that code.
          A `not_run` record is admitted only with a written reason and no acceptance
          results. Encoded in `quadrant/schema.json`, enforced in `record.admit`.
CITED:    §0 A6 (prose verification FALSIFIED) and §C.7 (only an executable check counts).
          A `status: "completed"` field is a self-report of exactly the kind A6 rejects.
REVERT:   Delete `required_evidence_keys` / `required_acceptance_fields` from
          `quadrant/schema.json`; `admit` degrades to a status check. Five tests go red
          first, which is the intended cost.

### 2026-08-30 · U4 · class 2 — the report is built from the MATRIX, never from the records
DECISION: `report.render` walks the configured quadrants and emits a row for every one; a
          quadrant with no record renders `NOT RUN - no record produced`, and a record for
          a quadrant outside the matrix raises rather than rendering. The headline
          `COMPARED n/4` is derived from the same walk.
CITED:    §C.2 class 2, resolved toward the pinned anchor: the stated failure mode is "a
          comparison silently missing two of four quadrants reads as a completed
          comparison". Iterating records - the obvious implementation - produces exactly
          that artifact and no reader can detect it.
REVERT:   Iterate `records` instead of `quadrants` in `report._rows`. `prove_guards.py`
          mutation 8 exists to make that change go red.

### 2026-08-30 · U4 · class 2 — `run` and `report` carry DIFFERENT exit codes
DECISION: `run --runner X --target Y` exits 0 when the cells it attempted completed;
          `report` exits 0 only when all four cells produced an admitted comparable
          outcome. `run` still writes the full-matrix report.
CITED:    §C.2 class 2, most reversible option. Merging them would make single-cell runs
          exit 1 forever (useless), and dropping the completeness code would remove the
          only machine-readable signal that a comparison is partial.
REVERT:   Return `_emit_report(...)` from `cmd_run`; one line in `quadrant/cli.py`.

### 2026-08-30 · U4 · class 2 — a `fixture` runner exists, and is structurally non-comparable
DECISION: Added `runners.fixture` (`status: "self-test"`) to `harness.config.json`: it
          performs the item deterministically with no model, so the harness can be proven
          end to end at zero cost. `schema.json`'s `comparable_runner_statuses` excludes
          `self-test`, so it can never appear in a decision table, and a test asserts no
          profile assigns a role to it.
CITED:    §A.4 (testing designed in, per phase) plus §C.2 class 2. The alternative -
          proving the harness only with cloud runs - makes every self-test cost money and
          leaves the machinery unproven whenever the CLI is unavailable.
REVERT:   Delete the `fixture` runner from `harness.config.json` and the `fixture` branch
          in `adapters.dispatch`; two tests and the end-to-end proof go red.

### 2026-08-30 · U4 · class 2 — run artifacts live in `.quadrant/`, NOT under `.claude/`
DECISION: `quadrant.results_dir` = `.quadrant/runs`, `targets.project.scratch_root` =
          `.quadrant/scratch`; `.quadrant/` gitignored.
CITED:    §C.2 class 2 on measured evidence (F2 below): a run whose workspace sat under
          `.claude/` scored 1/2 because Claude Code treats that path as sensitive and
          turned every edit into a permission request. The harness was measuring its own
          directory choice.
REVERT:   Change the two config values back; the `.gitignore` line follows.

### 2026-08-30 · U4 · class 2 — build artifacts are excluded from the item, by config
DECISION: `schema.json` gains `item_plant_ignore` (`__pycache__`, `*.pyc`, `*.pyo`,
          `.DS_Store`, `Thumbs.db`); `item.load` filters against it, and `guards.py` sets
          `sys.dont_write_bytecode = True`.
CITED:    §A.2 (configuration over hardcoding) and F3 below - a measured defect, not a
          precaution.
REVERT:   Empty the list; `test_build_artifacts_do_not_enter_the_item` goes red.

---

## What was actually validated, and by which check

§2's U4 row: *"Gym: same anchored item run per quadrant (runner x target), outcomes
compared"*. Status: **the comparison harness is built and proven; the comparison itself is
2/4 and says so.**

| Claim | Check | Result |
|---|---|---|
| The four cells are derived from config, and a misconfigured one fails loudly | `test_quadrant.py` (6 matrix tests) | green |
| A fabricated "completed" cannot enter the table | `test_quadrant.py` (8 admission tests) | green |
| A quadrant that did not run is never absent, never scored | `test_quadrant.py` (6 report tests) | green |
| Each guard would NOTICE if it broke | `python -m quadrant.prove_guards` | **10/10 bite** |
| The machinery works end to end | `test_fixture_runner_completes_the_real_item_end_to_end` | green |
| Two cells really ran | `.quadrant/runs/*/record.json`, admitted | 2/4 |
| A partial comparison is machine-detectable | `python -m quadrant.cli report; echo $?` | **exit 1** |
| Nothing else in the harness regressed | `pytest scripts/agent-harness -q` (138) + `verify-merge-protocol.ps1` (66/66) | green |

`prove_guards.py` is the check that matters most. 34 passing tests prove nothing currently
breaks; the drill breaks each guard on purpose and requires its test to go red, which is
the only evidence that any of them is load-bearing rather than decorative.

## The comparison as it stands (2026-08-30, `.quadrant/runs/COMPARISON.md`)

```
COMPARED 2/4  - INCOMPLETE
claude-code x self     completed  2/2   35.8s   $0.5437  1627 tok  0 taps  1 file changed
claude-code x project  completed  2/2   33.5s   $0.4639  2251 tok  0 taps  1 file changed
little-coder x self    NOT RUN - not dispatchable from this process (see F1)
little-coder x project NOT RUN - not dispatchable from this process (see F1)
```

**Read it as a comparison of the TARGET axis only, at n=1.** Both cells solved the item
with no scope violations and no frozen-file edits. Across three independent pairs run
while building this (0.594/0.479, 0.589/0.504, 0.544/0.464 USD), `self` cost more than
`project` every time - plausibly the repository context the self target loads. Three
paired observations is a hypothesis, not a finding; `quadrant.repeats` is 1 and the report
says "not a basis for a decision" in those words.

## F1 — little-coder is healthy and undispatchable, and the container is drifted

Independently re-verified 2026-08-30, not taken from the brief:

| Claim | Command | Result |
|---|---|---|
| daemon healthy | `docker exec little-coder curl -fsS localhost:8090/health` | `{"status":"ok","version":"0.1.0",...}` |
| reachable from the host | `curl http://127.0.0.1:8090/health` | connection refused |
| what the LIVE container publishes | `docker inspect little-coder --format '{{json .NetworkSettings.Ports}}' ` | `{"9090/tcp":[]}` — **nothing published at all** |
| what compose DECLARES | `coder/docker-compose.yml:121` | `"127.0.0.1:9091:9090"` |

So the running container has drifted from its own compose file: even the metrics port that
`coder/docker-compose.yml` publishes is not published on the live container. Two of four
quadrants are therefore NOT RUN, with that sentence as the recorded reason.

**This harness deliberately does not fix it.** A dispatch is being built as its own item,
and a second implementation here would be the "two ready items colliding on one file"
andon condition. What is recorded for whoever owns it: `docker exec little-coder curl -fsS
http://localhost:8090/tasks` returns `{"tasks":[]}`, so a `docker-exec` transport is a
verified-working alternative to publishing a port. `adapters._dispatch_little_coder` is the
seam - it speaks the HTTP API `harness.config.json` already declares and nothing more.

## F2 — a workspace under `.claude/` measured the harness, not the quadrant

The first real `claude-code x project` run scored **1/2** with **0 files changed**, and the
transcript says why:

> "I need your approval to write the file - both the Write and Edit calls came back as
> permission requests for `quadrant-item/slugify.py` (flagged as a sensitive path)."

The model had the correct implementation in its reply. The workspace was at
`.claude/quadrant-runs/<run>/workspace`, and Claude Code treats `.claude/` as sensitive.
Moving the run root to `.quadrant/` turned the same quadrant green (2/2) on the next run.

Worth generalising beyond U4: **any harness that stages an agent's workspace under
`.claude/` will silently measure the permission system.** The failure presented as a
capability difference, not as an error.

## F3 — the experiment's control was drifting under it

The item digest is the mechanism that makes "the same anchored item" checkable. It moved
three times in one afternoon (`67e9c86e`, `ce4ee809`, `c585bee6`) with no edit to any item
file. Cause: `guards.py` imports the pristine test module to run it, which wrote
`files/__pycache__/*.pyc` INSIDE the item directory; `item.load` walked `files/` with
`rglob` and swept them into the planted set. Two silent consequences - every earlier record
became "a different item" and unadmittable, and the byte-compiled cache was copied into
every run workspace.

Fixed both ends (ignore list in `schema.json`; `sys.dont_write_bytecode` in `guards.py`),
proved RED first with `test_build_artifacts_do_not_enter_the_item`, and confirmed the
digest is now stable across two full suite runs.

The lesson is not "remember to ignore `__pycache__`". It is that **a content-addressed
control has to be tested for stability under the tooling that touches it**, or it silently
becomes a control that changes whenever someone runs the tests.

## F4 — a defect in my own honesty mechanism, found by using it

`_run_one` returned `not_run` records without WRITING them: the run directory was created
after the preflight branch. The report then rendered "no record produced" for the
little-coder cells - true, but a strictly weaker sentence than the reason preflight had
already established. Losing a known reason is the same failure as never having one. Fixed;
the run directory is now created before preflight and the blocked record is persisted like
any other.

## F5 — this module is the first executable consumer of the runner axis

Consistent with `documentation/notes/u4-profile-mechanism-deadcode.md` (independent, same
day): `Resolve-RoleTarget` resolves a runner and nothing runs one, so three of the four
shipped profiles name a runner that cannot be dispatched. `quadrant/adapters.dispatch` is
the first code in the harness that takes a runner name and executes it. It does not close
that gap for the PIPELINE - queue.ps1 still dispatches nothing - but it does mean the
runner axis now has one caller that would break loudly if a runner entry were wrong.

## Open, deliberately not done here

1. **Two quadrants have never run.** Blocked on dispatch (F1). Re-running is
   `python -m quadrant.cli run --runner little-coder` and takes seconds once a route
   exists; the NOT RUN rows become real outcomes with no other change.
2. **n=1.** `quadrant.repeats: 1`. Nothing in the report may be read as a difference
   between quadrants until it is at least 2 and the repeats agree.
3. **The `self` target plants a fixture into a worktree of this repo.** Faithful to what
   the axis measures (environment, not task), but it is not the same as the org working on
   a *real* ai-stack issue. A second item sourced from a genuine repo issue would be the
   stronger test, and would need the issue-ops intake (U2) to supply it.
4. **`operator_taps` is recorded but always 0**, because nothing in this harness can be
   tapped - the runs are unattended by construction. The column is there because it is the
   gym's central metric and a future interactive runner must have somewhere to report it.
   Recorded so nobody reads a column of zeroes as a measured result.
5. **`gpu_seconds` is always null.** No runner reports it yet; null means unmeasured, and
   the report says so rather than printing 0.
