# Findings — U4: the runner x target quadrant comparison (2026-08-30)

Built on `work/u4quad`. Deliverable: `scripts/agent-harness/quadrant/` (see its
`MODULE.md`), its suite in `scripts/agent-harness/test_quadrant.py`, and a mutation drill
`quadrant/prove_guards.py`.

**Round 2 (2026-08-30, after refutation 2/2).** The two verifiers confirmed the honesty
mechanisms held — the comparison reports itself as 2/4 and exits 1 — and refuted the work
for a real defect: *the shipped CLI could shrink the comparison until it read complete.*
That is fixed here, RED first. The phase's column is still **UNMET**, and this note parks
it rather than manufacturing a pass. See [The park](#the-park--u4s-validated-by-column-is-unmet).

**Round 3 (2026-08-30, audit-trail only — no code changed).** One verifier did not refute
the work; the other refuted the park's JUSTIFICATION and judged it dishonest. The second
was right: the park's stated reason was disproved by one command, `docker exec little-coder
curl -s http://localhost:8090/tasks`. See [F7](#f7--the-parks-reason-was-false-and-one-command-in-this-note-disproved-it)
and [Reconciling the two verifiers](#reconciling-the-two-verifiers). This round rewrites
the park's reason and three other prose claims a command does not support; `git diff
fda4816..HEAD --stat` shows the changed files are documentation only.

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
DECISION: `report.render` walks the comparison's cells and emits a row for every one; a
          cell with no record renders `NOT RUN - no record produced`. The headline
          `COMPARED n/N` is derived from the same walk.
CITED:    §C.2 class 2, resolved toward the pinned anchor: the stated failure mode is "a
          comparison silently missing two of four quadrants reads as a completed
          comparison". Iterating records — the obvious implementation — produces exactly
          that artifact and no reader can detect it.
REVERT:   Iterate `records` instead of the cell list in `report._rows`. `prove_guards.py`
          mutation 8 exists to make that change go red.

### 2026-08-30 · U4 · class 2 — the matrix is DECLARED per results set and only ever grows
DECISION: A comparison is over the union of (a) the results set's pinned
          `<results_dir>/matrix.json`, (b) the configured `quadrant.runners x
          quadrant.targets`, and (c) every cell any record on disk names. The lock is
          append-only. A declared cell that is no longer configured renders `OFF MATRIX`,
          carries whatever reason its records recorded, and counts against completeness.
          `cli._emit_report` no longer filters records to the configured matrix, and
          `report._rows` absorbs off-matrix records instead of raising on them.
CITED:    §C.7 (a check that cannot fail is not a check) plus the verifier's reproduction
          below. Mechanism "the report is built from the MATRIX" was only as strong as
          what *the matrix* is; reading it fresh from configuration made the module's own
          declared failure mode reachable by a one-line config edit.
REVERT:   In `quadrant/cli.py`, restore the record filter in `_emit_report` and stop
          calling `_declared_matrix`; delete `declared_matrix_lock` from
          `quadrant/schema.json`. Four tests go red first
          (`test_narrowing_the_axes_cannot_launder_a_partial_comparison_through_the_cli`,
          `test_the_matrix_lock_holds_a_cell_that_lost_both_its_config_and_its_record`,
          `test_a_declared_cell_survives_a_narrowed_configuration`,
          `test_the_declared_matrix_is_append_only`) and `prove_guards` mutations 11–14
          stop biting. Existing `matrix.json` files become inert data; nothing reads them.

### 2026-08-30 · U4 · class 2 — an off-matrix cell keeps the reason its records gave
DECISION: `report._off_matrix_row` carries the distinct `not_run_reason` / `error` text of
          every record for that cell into the row's `why_not`.
CITED:    F4 (below) generalised: losing a known reason is the same failure as never
          having had one. Found by the test that proves the off-matrix row exists at all —
          the row was rendered, and the sentence explaining why the cell never ran was not.
REVERT:   Delete the `if said:` block in `quadrant/report.py`; mutation 14 stops biting and
          `test_narrowing_the_axes_cannot_launder_a_partial_comparison_through_the_cli`
          goes red.

### 2026-08-30 · U4 · class 2 — `run` and `report` carry DIFFERENT exit codes
DECISION: `run --runner X --target Y` exits 0 when the cells it attempted completed;
          `report` exits 0 only when every DECLARED cell produced an admitted comparable
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
CITED:    §A.4 (testing designed in, per phase) plus §C.2 class 2. The alternative —
          proving the harness only with cloud runs — makes every self-test cost money and
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
CITED:    §A.2 (configuration over hardcoding) and F3 below — a measured defect, not a
          precaution.
REVERT:   Empty the list; `test_build_artifacts_do_not_enter_the_item` goes red.

### 2026-08-30 · U4 · PARK — the runner axis has no coverage in THIS module, which speaks one transport
DECISION: U4's §2 "Validated by" column ("Gym: same anchored item run per quadrant
          (runner x target), outcomes compared; stall→oracle observed firing at least
          once") is **NOT satisfied** by this item and this item does not merge as a
          completion of U4. The comparison harness merges; the phase parks.
          **The reason, restated in round 3 after a verifier refuted the round-2 wording**
          (F7 — round 2 said little-coder "cannot execute an anchored item from this
          process", which is false): `quadrant/adapters.py` dispatches little-coder over
          exactly one transport, the HTTP endpoint `harness.config.json` declares on this
          branch, and that door does not exist. `docker inspect little-coder --format
          '{{json .NetworkSettings.Ports}}'` → `{"9090/tcp":[]}`, and
          `curl -s -m 4 http://127.0.0.1:8090/health` exits 7. A `docker exec` route to
          the same daemon answers (HTTP 200 on `/health`), and `work/dfu-u4` has already
          driven a real anchored item through it to `outcome: pass`. The two cells are
          NOT RUN because this module implements one transport and it is the wrong one —
          not because little-coder is unreachable.
CITED:    §C.7: "A phase that cannot satisfy its column does not merge. It parks with a
          written reason." Forcing the two cells green would require faking a record
          (blocked by evidence-gated admission, by design). Building the docker-exec
          transport HERE would duplicate `scripts/agent-harness/dispatch.ps1`, which
          `work/dfu-u4` shipped the same day (`git log --oneline work/dfu-u4 --
          scripts/agent-harness/dispatch.ps1` → `f856b4e` then `bf10d96`, both
          2026-08-30) — the colliding-items andon §L4 names.
REVERT:   Nothing to revert; this is a status, not a change. It is lifted by the
          conditions in [What would meet the column](#what-would-meet-the-column).

### 2026-08-30 · U4 · class 2 — round 3: a park states the reason a command supports, or it is not a park
DECISION: The PARK entry above was rewritten. Its round-2 reason ("cannot", "no dispatch",
          "permanently", "no amount of work inside this item changes that") was refuted by
          `docker exec little-coder curl -s http://localhost:8090/tasks`, which shows a
          completed little-coder task from a sibling item. The reason now names the one
          thing that is true and checkable: this module implements a single transport and
          the door it uses does not exist here. No code changed in this round —
          `git diff fda4816..HEAD --stat` is documentation and docstrings only.
CITED:    §C.7: the audit trail is what the operator reads instead of the diffs, "so they
          must be true". A park is the one artifact of a phase that does NOT come with an
          executable check, which is exactly why its prose has to be held to one.
REVERT:   `git revert` this commit. Nothing depends on the wording; the module's behaviour,
          its 39 tests and its 14 mutations are untouched by it. What must NOT be reverted
          alone is the correction without the finding: F7 is the record of why the first
          wording was wrong, and deleting it re-creates the conditions for it.

### 2026-08-30 · U4 · incident — the merge-protocol drill left the operator's checkout mid-rebase
DECISION: Recorded, not repaired: running `scripts/agent-harness/verify-merge-protocol.ps1`
          from this worktree rebases inside the SHARED main checkout, and an interrupted
          run left `D:\Open WebUI\ai-stack` on a detached HEAD with `.git/rebase-merge`
          present and a leftover `drill/verify-d` branch. **`git rebase --abort` in the
          main checkout is the repair; this session was denied permission to run it and
          did not work around the denial.**
          **RESOLVED by someone else before this note was finalised.** Re-checked
          2026-08-30 in `D:\Open WebUI\ai-stack`: `git status --short --branch` →
          `## refactor/ai-stack-cleanup...origin/refactor/ai-stack-cleanup [ahead 6]`;
          `ls .git/rebase-merge .git/rebase-apply` → no such file or directory;
          `git branch --list 'drill/*'` → empty.
CITED:    The worktree-per-session policy exists so one session cannot mutate another's
          git state. This drill is an exception to it that the policy does not name.
REVERT:   No longer applicable — the repair has been applied. It was
          `cd "D:\Open WebUI\ai-stack"; git rebase --abort`. The branch tip moves as other
          sessions merge, so the checkable invariant is ancestry, not a SHA:
          `git merge-base --is-ancestor 98cf02e refactor/ai-stack-cleanup` exits 0.

---

## The defect the verifiers found, and the fix

### What was reproduced

A verifier narrowed `quadrant.runners` to `["claude-code"]` — one line of
`harness.config.json` — and ran the shipped `python -m quadrant.cli report` over the SAME
four records. `cli._emit_report` filtered the records to the currently configured matrix
before the report saw them, so the two `little-coder` cells left the table entirely.

I reproduced it before changing anything, against the real evidence set copied to scratch:

```
COMPARED 2/2                         <- headline
(no "INCOMPLETE" clause, no little-coder rows, no "cannot tell you" entries)
exit 0
```

That is precisely the failure mode this module's own `schema.json` says it exists to make
unrepresentable, reached through configuration instead of through code. The verifier is
right and the finding is the strongest one in either review.

### Root cause, which is more interesting than the filter

`report._rows` **raised** `QuadrantReportError` on a record naming a cell outside the
matrix. The only caller had to silence that to work at all, and silenced it by *deleting
those records*. The exception was the pressure that produced the laundering: a guard that
refuses to render is a guard callers route around. Absorbing the record into the table
removes any reason to drop one.

### The fix

1. `cli._emit_report` no longer filters (`records = _load_records(results_dir)`).
2. `cli._declared_matrix` pins the comparison's cells in `<results_dir>/matrix.json` and
   unions it, append-only, with the configured cells and every cell the records name.
3. `report.declared_keys` / `report._off_matrix_row` render a declared-but-unconfigured
   cell as `OFF MATRIX`, never `compared`, with a `MATRIX NARROWED` banner in the headline
   block and the reason its records gave.
4. `report._rows` raises only for a record naming **no** cell at all — the one
   disagreement that cannot be placed in a table.

### Proof, RED before GREEN

| Step | Command | Result |
|---|---|---|
| the attack, pre-fix, on the real evidence | `python -m quadrant.cli report --results-dir <copy>` with `quadrant.runners: ["claude-code"]` | `COMPARED 2/2`, **exit 0** |
| new tests against pre-fix source | `git checkout HEAD -- quadrant/{cli,report,schema}.py*` then `pytest test_quadrant.py -k "off_the_matrix or naming_no_quadrant or narrowed_configuration or launder or matrix_lock or append_only"` | **5 failed, 1 passed** |
| the same tests, post-fix | `pytest scripts/agent-harness/test_quadrant.py -q` | **39 passed** |
| the attack, post-fix, same records | `python -m quadrant.cli report --results-dir <copy>` with `quadrant.runners: ["claude-code"]` | `COMPARED 2/4`, `MATRIX NARROWED`, both little-coder rows `OFF MATRIX` **carrying their not-run reason**, **exit 1** |
| every guard still bites, including the four new ones | `python -m quadrant.prove_guards` | **14/14 guards proven to bite** |

The one test that passed pre-fix is `test_a_record_naming_no_quadrant_at_all_is_unrenderable`
— the old code raised on every off-matrix key including the empty one, so that assertion
was already satisfied. It is not a regression guard for the new behaviour; it exists to
keep `QuadrantReportError` a live, reachable error rather than dead code.

Mutation 12 in `prove_guards.py` restores the deleted filter **verbatim** and requires the
end-to-end CLI test to go red, so this specific regression cannot return quietly.

### What the fix does not claim

A comparison begun in a **fresh** results directory with narrow axes is a genuinely narrow
comparison and reports itself as such (`COMPARED 2/2` over a two-cell declared matrix,
axes visible in `matrix.json`). The lock defends against *reusing an existing evidence set
under a smaller matrix*, which is what was reproduced. The remaining move — deleting
`matrix.json` **and** the records of the hidden cells — is destroying evidence, not
producing a report that lies about it. Stated in `MODULE.md` and in `schema.json` so
nobody over-reads the guarantee.

---

## The park — U4's "Validated by" column is UNMET

§2's U4 row is validated by: *"Gym: same anchored item run per quadrant (runner x target),
outcomes compared; stall→oracle observed firing at least once"*.

| Half of the column | State | Evidence |
|---|---|---|
| same anchored item, per quadrant | **2 of 4 cells** | `.quadrant/runs/*/record.json`; `comparison.json` → `complete: false`, `compared: 2`, `quadrants_total: 4` |
| outcomes compared | **target axis only** | both compared cells are `claude-code`; the runner axis has zero coverage |
| stall→oracle observed firing | **not this item's scope** | `work/u4oracle` |

**Why the runner axis has no coverage — corrected in round 3 (F7).** Round 2 wrote that
`little-coder` "cannot be handed an anchored item from this process". One command disproves
that, and it is the command in this note's own F1: `docker exec little-coder curl -s
http://localhost:8090/tasks`. What is true, and all that is true:

| Claim | Command | Result |
|---|---|---|
| the declared HTTP door does not exist | `curl -s -m 4 -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/health` | `000`, exit 7 |
| the live container publishes nothing | `docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` | `{"9090/tcp":[]}` |
| compose declares only metrics | `coder/docker-compose.yml:121` | `"127.0.0.1:9091:9090"` (9090 is the Prometheus port, not the API) |
| a `docker exec` route DOES answer | `docker exec little-coder curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/health` | `200` |
| this module speaks only the HTTP transport | `grep -n "_dispatch_little_coder\|transport" scripts/agent-harness/quadrant/adapters.py` | four hits: the definition, its call site, and two docstring lines about the transport this module does NOT implement. The function body is `urllib.request` against `rcfg["endpoint"]`; nothing reads a `transport` key and there is no `docker exec` branch |
| the pipeline dispatches no runner at all | `documentation/notes/u4-profile-mechanism-deadcode.md` | `Resolve-RoleTarget` has zero executable callers repo-wide |

So the axis is blocked at ONE place that this item owns — the transport inside
`adapters._dispatch_little_coder` — and at one it does not, the pipeline. It is not blocked
by the world.

**Why this item did not fix it.** The docker-exec transport was built the same day on
`work/dfu-u4` (`scripts/agent-harness/dispatch.ps1`, `Invoke-HarnessTask`; `git show --stat
bf10d96`), together with the config change that points the runner at it
(`git show bf10d96:scripts/agent-harness/harness.config.json` → `"transport":
"docker-exec"`). Writing a second one here is the colliding-items andon §L4 names.
The honest output of this item is a comparison that refuses to pretend, plus this park —
and a park whose stated reason is the one a command supports.

**What is genuinely delivered.** The apparatus that makes the comparison decidable and
un-fakeable, and a real 2-cell measurement of the TARGET axis at n=1, which supports a
`self` vs `project` reading and nothing else.

### What would meet the column

Concretely, in order:

1. **A `docker exec` transport in this module.** The seam is
   `adapters._dispatch_little_coder` and the probe beside it,
   `matrix.probe_little_coder`; nothing else in the module changes. Do NOT publish the
   API port instead: `git show bf10d96:scripts/agent-harness/harness.config.json`
   (`_why_docker_exec`) records that `POST /tasks` is unauthenticated since `lc-mcpo` was
   retired, so publishing it would hand arbitrary agent execution to every process on the
   machine. The transport itself is already written on `work/dfu-u4`
   (`dispatch.ps1`'s `Invoke-HarnessTask`), so the work here is to call it or port it,
   not to design it.
2. **One anchored item completed through little-coder.** `work/dfu-u4` ran one on
   2026-08-30 — `docker exec little-coder curl -s http://localhost:8090/tasks` → one task,
   `user_id harness-dfu-u4`, `status done`, `outcome pass`, `signal "acceptance command
   exit 0"`. Whether that closes A11 is that item's call to make, not this one's, and it
   parked too: `bf10d96`'s message says "One quadrant ran; no comparison exists; no oracle
   path exists at all." The two parks are complementary — that item proved the runner, this
   one built the comparison, and the column needs both plus the oracle.
3. **Then** `python -m quadrant.cli run-all --item u4-baseline` and
   `python -m quadrant.cli report`. When the column is met, `report` exits 0 by itself —
   that exit code is the column's machine-readable form. Nothing else in the module has to
   change for the two NOT RUN rows to become real outcomes.
4. Independently, raise `quadrant.repeats` to ≥2 before treating any difference between
   cells as real.

---

## What was actually validated, and by which check

| Claim | Check | Result |
|---|---|---|
| the module's suite is green | `python -m pytest scripts/agent-harness/test_quadrant.py -q` | **39 passed** |
| nothing else in the harness regressed | `python -m pytest scripts/agent-harness -q` | **144 passed** |
| each guard would NOTICE if it broke | `python -m quadrant.prove_guards` | **14/14 bite** |
| the machinery works end to end without a model | `test_fixture_runner_completes_the_real_item_end_to_end` | green |
| two cells really ran | `.quadrant/runs/*/record.json`, admitted | 2/4 |
| a partial comparison is machine-detectable | `python -m quadrant.cli report; echo $?` | **exit 1** |
| a NARROWED matrix is still machine-detectable | the same command with `quadrant.runners: ["claude-code"]` | **exit 1**, `COMPARED 2/4` |
| the module and its test lint clean | `ruff check scripts/agent-harness/quadrant scripts/agent-harness/test_quadrant.py` | `All checks passed!` |

`prove_guards.py` is the check that matters most. 39 passing tests prove nothing currently
breaks; the drill breaks each guard on purpose and requires its test to go red, which is
the only evidence that any of them is load-bearing rather than decorative.

## Claims I made in round 1 that a command does not support

Audited 2026-08-30 against commands actually run. Recorded rather than quietly corrected,
because the correction is the finding.

| Round-1 claim | What a command shows | Disposition |
|---|---|---|
| "33 checks" (`MODULE.md`) | `pytest test_quadrant.py -q` printed `34 passed` at the time and `39 passed` now | corrected; the doc now points at the command and dates the number |
| "`pytest scripts/agent-harness -q` (138)" | the commit message said 139; today it prints 144 | corrected to 144, dated |
| "6 matrix tests / 8 admission tests / 6 report tests" | hand-partitioned; no `-k` expression reproduces those three numbers (the obvious ones collect 15/15/8 with overlap) | **dropped** — the table now cites whole-suite counts and names individual tests |
| "`ruff check` clean" (commit message) | `ruff check .` at repo root reports **2 errors**, both pre-existing from `e1e73dc` (U3 org drill) in files this branch does not touch: `agent-org/agent-bridge/tests/test_org_drill.py:31` F401, `scripts/agent-harness/test_anchor_schema.py:267` F811 | claim re-scoped to the module, which does pass; the two errors are a finding against U3, below |
| "`verify-merge-protocol.ps1` 66/66" | not reproducible here: it printed **55/66**, and running it left the operator's main checkout detached mid-rebase (see the incident DECISION above) | claim **withdrawn**; the drill is not re-run from this worktree |
| "three independent pairs, 0.594/0.479, 0.589/0.504, 0.544/0.464 USD, `self` cost more every time" | only the LAST pair survives on disk (`comparison.json`: 0.5437155 / 0.4638774999999999); the earlier two run directories were overwritten during development | **dropped as a trend.** One paired observation is not a trend, and the other two are not re-derivable by anyone |
| "the item digest moved three times (`67e9c86e`, `ce4ee809`, `c585bee6`)" | only the current digest is re-derivable (`python -c "from quadrant import item; print(item.load('u4-baseline')['digest'])"` (from `scripts/agent-harness`) → `c585bee6fee3043c…`) | the two historical hashes are dropped; the defect and its regression test stand on their own |

The generalisable half: **a number in a document is a claim, and it decays.** The three
that were simply stale were harmless; the two that named measurements whose artifacts no
longer exist were not, because no reader could have discovered they were uncheckable.

## F1 — little-coder is healthy, the DECLARED door does not exist, and the container is drifted

Heading corrected in round 3: round 2 said "undispatchable", which F7 disproves.

| Claim | Command | Result |
|---|---|---|
| daemon healthy | `docker exec little-coder curl -fsS localhost:8090/health` | `{"status":"ok","version":"0.1.0",...}` |
| reachable from the host | `curl http://127.0.0.1:8090/health` | connection refused |
| what the LIVE container publishes | `docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` | `{"9090/tcp":[]}` — **nothing published at all** |
| what compose DECLARES | `coder/docker-compose.yml:121` | `"127.0.0.1:9091:9090"` (metrics, not the API) |

The running container has drifted from its own compose file: even the metrics port compose
publishes is not published on the live container. Cause not established here. Two of four
quadrants are therefore NOT RUN, with that sentence as the recorded reason — and it
survives even a narrowed matrix (see the fix above).

**This harness deliberately does not fix it.** `adapters._dispatch_little_coder` is the
seam — it speaks the HTTP API `harness.config.json` declares on this branch and nothing
more. A `docker-exec` transport reaches the same daemon and is not hypothetical: see F7.

**A decayed claim, left visible on purpose.** Round 2 wrote that `docker exec little-coder
curl -fsS http://localhost:8090/tasks` returns `{"tasks":[]}`. Re-run on 2026-08-30 it
returns ONE task (F7). A live API's response is a reading, not a fact, and quoting one as
though it were fixed is the same mistake this note names in
[Claims I made in round 1](#claims-i-made-in-round-1-that-a-command-does-not-support) —
made again, in the note that names it.

## F2 — a workspace under `.claude/` measured the harness, not the quadrant

The first real `claude-code x project` run scored **1/2** with **0 files changed**; the
transcript said the Write and Edit calls came back as permission requests for a path
flagged as sensitive, and the model had the correct implementation in its reply. The
workspace was at `.claude/quadrant-runs/<run>/workspace`, and Claude Code treats `.claude/`
as sensitive. Moving the run root to `.quadrant/` turned the same quadrant green (2/2).

**Evidence status, stated because it matters:** that run directory was NOT retained — the
results root moved and the old one is gone, so the transcript quote in round 1 of this note
is a session observation, not a re-derivable artifact. What a reader can still check is the
config comment recording it (`harness.config.json` → `quadrant._results_dir_note`) and the
current `results_dir` value. Anyone wanting the artifact must reproduce it by pointing
`--results-dir` under `.claude/` and running one cell.

Worth generalising beyond U4: **any harness that stages an agent's workspace under
`.claude/` will silently measure the permission system.** The failure presented as a
capability difference, not as an error.

## F3 — the experiment's control was drifting under it

The item digest is the mechanism that makes "the same anchored item" checkable. It moved
several times in one afternoon with no edit to any item file. Cause: `guards.py` imports
the pristine test module to run it, which wrote `files/__pycache__/*.pyc` INSIDE the item
directory; `item.load` walked `files/` with `rglob` and swept them into the planted set.
Two silent consequences — every earlier record became "a different item" and unadmittable,
and the byte-compiled cache was copied into every run workspace.

Fixed both ends (ignore list in `schema.json`; `sys.dont_write_bytecode` in `guards.py`),
proved RED first with `test_build_artifacts_do_not_enter_the_item`. The current digest is
`c585bee6…` and is re-derivable; the intermediate values are not, so they are not quoted.

The lesson is not "remember to ignore `__pycache__`". It is that **a content-addressed
control has to be tested for stability under the tooling that touches it**, or it silently
becomes a control that changes whenever someone runs the tests.

## F4 — a defect in my own honesty mechanism, found by using it

`_run_one` returned `not_run` records without WRITING them: the run directory was created
after the preflight branch. The report then rendered "no record produced" for the
little-coder cells — true, but a strictly weaker sentence than the reason preflight had
already established. Losing a known reason is the same failure as never having one. Fixed;
the run directory is now created before preflight and the blocked record is persisted like
any other.

**Round 2 recurrence, one layer up:** the first version of the off-matrix row knew a cell
had records and forgot what they said. Caught by the test that proves the row exists at
all, and fixed the same way. The pattern is worth naming — *every new rendering path is a
new place for a recorded reason to be dropped* — because it has now happened twice in one
module, both times in code written to prevent exactly that.

## F5 — this module is the first executable consumer of the runner axis

Consistent with `documentation/notes/u4-profile-mechanism-deadcode.md` (independent, same
day): `Resolve-RoleTarget` resolves a runner and nothing runs one, so three of the four
shipped profiles name a runner that cannot be dispatched. `quadrant/adapters.dispatch` is
the first code in the harness that takes a runner name and executes it. It does not close
that gap for the PIPELINE — `queue.ps1` still dispatches nothing — but it does mean the
runner axis now has one caller that would break loudly if a runner entry were wrong.

## F6 — two pre-existing lint errors on the work line, from U3

`ruff check .` at the repo root reports two errors in files this branch does not touch:

```
F401 agent-org/agent-bridge/tests/test_org_drill.py:31   `app.models.Effort` imported but unused
F811 scripts/agent-harness/test_anchor_schema.py:267     Redefinition of unused `subprocess` from line 19
```

`git log -1 -- <those files>` → `e1e73dc` ("U3: the org drill"). Both are auto-fixable
(`ruff --fix`). Not fixed here — they belong to U3's item, and CLAUDE.md's lint gate is a
repo-wide `ruff check .`, so anything merging after this inherits a red gate until U3's
owner clears them.

## F7 — the park's reason was false, and one command in this note disproved it

Round 3, after one verifier judged the park dishonest while another found the work sound.
Both are right, about different objects; see
[Reconciling the two verifiers](#reconciling-the-two-verifiers).

The round-2 park rested on three sentences. Each is checkable, and two are false:

| Round-2 sentence | Command | Verdict |
|---|---|---|
| "`little-coder` cannot execute an anchored item from this process" | `docker exec little-coder curl -s http://localhost:8090/tasks` | **FALSE.** One task: `task_id 01M19JABFNQHR7CPDGCPDK2VEV`, `user_id harness-dfu-u4`, `channel batch`, `status done`, `outcome pass`, `signal "acceptance command exit 0"`, `created_ts 2026-08-30T14:49:00.661Z` |
| "no dispatch to it exists in the harness at all" | `grep -n "_dispatch_little_coder" scripts/agent-harness/quadrant/adapters.py` | **FALSE.** Two hits, a definition and its call site, in this module. A second dispatch, `dispatch.ps1`'s `Invoke-HarnessTask`, exists on `work/dfu-u4`. The true claim is narrower and was already written a paragraph later: the *pipeline* dispatches no runner |
| "both cells are permanently NOT RUN here, and no amount of work inside this item changes that" | this note, [What would meet the column](#what-would-meet-the-column) item 1 | **FALSE, and self-contradicted.** That item says the change is at `adapters._dispatch_little_coder` and "nothing else in the module changes" — which is work inside this item |

**The timeline is the part that stings.** The `harness-dfu-u4` task was created at
`14:49:00.661Z` and ended at `14:50:23.770Z` — 83 seconds, per the daemon's own record,
quoted in `work/dfu-u4`'s commit `bf10d96` (`git log -1 --format=%B bf10d96`). This item's
run records are timestamped `20260830T150151Z` onward (`ls .quadrant/runs`). little-coder
had therefore already executed an anchored item to `outcome: pass` about eleven minutes
BEFORE this module wrote its two little-coder cells down as blocked — and the recorded
reason, "no route from the host", was true about the transport and false about the world.

**What was actually wrong.** Not the code, and not the NOT RUN rows: preflight correctly
found the declared HTTP door missing and correctly refused to invent a result. In fact the
code was more careful than the prose about it. `python -m quadrant.cli preflight` prints,
for both blocked cells: *"The daemon may well be healthy inside its container; what is
missing is a route from the host. Verified 2026-08-30: the running container publishes no
ports at all, so the API port is reachable only via 'docker exec'."* The machine-readable
artifact named the workaround; the human-readable park said it could not be done. What was
wrong is that the park generalised from "my one transport does not work" to "this cannot be
done", without running the one command that separates those — the error class this
workspace has already named: stopping the read early, then generalising.

**Not corrected by this round.** `harness.config.json`'s little-coder entry on this branch
still reads `"status": "unproven"` with `_note: "...no work item has completed through it
yet..."`. That text predates this item (`git log -L` on the block → `0ebebf4`, the
harness-module commit) and `work/dfu-u4` rewrites it. Two branches editing one JSON block
is the collision this note is trying not to cause, so it is recorded here and left alone.
PLAN §0's A11 verdict is left alone for the same reason: `work/dfu-u4` produced that
evidence and should be the item that spends it.

## Reconciling the two verifiers

They disagreed, and both verdicts survive, because they judged different objects.

- **Verifier 1 — the work is sound.** Re-checked here, with no pipe on any exit code:
  `pytest scripts/agent-harness/test_quadrant.py -q` → `39 passed`;
  `pytest scripts/agent-harness -q` → `144 passed`;
  `python -m quadrant.prove_guards` → `14/14 guards proven to bite`, exit 0;
  `python -m quadrant.cli report > out 2>&1; echo $?` → **1** (a pipe here reports the
  pipe's exit code, not the CLI's — worth stating, because it is how an exit-code claim
  gets faked by accident);
  `ruff check scripts/agent-harness/quadrant scripts/agent-harness/test_quadrant.py` →
  `All checks passed!`. F7 disturbs none of that.
- **Verifier 2 — the park is not honest.** Also correct, and about the prose. The park's
  stated REASON ("cannot", "no dispatch", "permanently") was stronger than any command
  supported, and one command refuted it.

Under §C.7 these are not in tension: the code's claims were executable and held; the audit
trail's claims were prose and did not. That asymmetry is what §C.7 exists to catch, and it
is why round 3 rewrote the park rather than the module.

**What is parked, in terms anyone can check without the diff.** U4's §2 column asks for one
anchored item run per quadrant with outcomes compared. Run `python -m quadrant.cli report`
from `scripts/agent-harness`: it prints `COMPARED 2/4`, lists `little-coder x self` and
`little-coder x project` as `NOT RUN`, and exits **1**. That exit code is the park in
machine-readable form. It becomes 0 when, and only when, all four cells hold an admitted
record. Nothing else in the module has to change for that; one transport does.

## Open, deliberately not done here

1. **Two quadrants have never run.** Blocked on dispatch (F1). See
   [What would meet the column](#what-would-meet-the-column).
2. **n=1.** `quadrant.repeats: 1`. Nothing in the report may be read as a difference
   between quadrants until it is at least 2 and the repeats agree.
3. **The `self` target plants a fixture into a worktree of this repo.** Faithful to what
   the axis measures (environment, not task), but it is not the same as the org working on
   a *real* ai-stack issue. A second item sourced from a genuine repo issue would be the
   stronger test, and would need the issue-ops intake (U2) to supply it.
4. **`operator_taps` is recorded but always 0**, because nothing in this harness can be
   tapped — the runs are unattended by construction. The column is there because it is the
   gym's central metric and a future interactive runner must have somewhere to report it.
   Recorded so nobody reads a column of zeroes as a measured result.
5. **`gpu_seconds` is always null.** No runner reports it yet; null means unmeasured, and
   the report says so rather than printing 0.
6. ~~**The operator's main checkout needs `git rebase --abort`**~~ — **RESOLVED by
   someone else** before this note was finalised; re-checked with `git status`,
   `ls .git/rebase-merge`, `git branch --list 'drill/*'` (see the incident DECISION).
   Left in the list because the incident — and that this session could not repair what it
   broke — is the finding.
7. **A `docker exec` transport for `adapters._dispatch_little_coder`** — F7. Not written
   here because `work/dfu-u4` has one; whoever merges both should call it rather than
   write a third.
