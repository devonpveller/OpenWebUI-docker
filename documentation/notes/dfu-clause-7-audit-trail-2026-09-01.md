# DFU clause 7 — what the audit trail is actually missing, per phase (2026-09-01)

Findings sink for the C.8 clause 2 + clause 7 item (`work/dfuc27`). Everything below was
measured on 2026-09-01 against work line `refactor/ai-stack-cleanup` at `fba111d`, from a
clean clone (`git -c core.longpaths=true clone --branch refactor/ai-stack-cleanup
--single-branch`, `git config core.longpaths true` set inside it, `git status --porcelain`
empty) except where a run is explicitly marked as needing more than a bare clone.

Nothing here is a fix. Under §C.10 these are filed, not rounded.

## The two different reasons clause 7 is red — U0, U1, U3, U4, U5 and U7 versus U2, U6, U8

`dfu-done.ps1`'s `audit-trail-<phase>` probe wants three artifacts per phase: a `## ` entry in
DECISIONS.md naming it, a note under `documentation/notes/` whose filename or a heading names
it, and a commit on the work line carrying a validation *claim* — one statement naming the
phase and one of the checks that phase itself names.

The red splits cleanly, and the split decides what an honest fix can be:

| phase | ledger | note | commit claim | why |
|---|---|---|---|---|
| U0 | yes | **was missing** | impossible | names no runnable check |
| U1 | yes | yes | impossible | names no runnable check |
| U2 | yes | yes | **writable** | names `test_harness_config.py`, `test_anchor_schema.py` |
| U3 | yes | yes | impossible | names no runnable check |
| U4 | yes | yes | impossible | names no runnable check |
| U5 | yes | yes | impossible | names no runnable check |
| U6 | yes | yes | **writable** | names `recall-falsifiability-drill.py`, `test_recall_seams.py` |
| U7 | yes | **was missing** | impossible | names no runnable check |
| U8 | yes | yes | **writable** | names `dfu-done.ps1` |

"Impossible" is literal, not rhetorical. `Get-NamedArtifacts` reads script/source filenames out
of §2's *Validated by* column and out of the walkthrough's *How to run* lines for that phase.
Six phases name none in either place — U0's column is "each item's own anchor + tester; inbox: a
kill-the-poller drill", U1's is "the memory-plane plan's own per-phase gates", and so on — so the
probe reports "no commit message can state 'by which check'" and no commit message can. Closing
those six needs either a runnable check added to §2's column (amending an anchor column, which
§C.8 forbids) or a *How to run* line added to WALKTHROUGH.md. Neither belongs to a
DECISIONS/PLAN item.

This note is itself the missing findings artifact for **U0** and **U7**; it does not make either
green, because the commit-claim half stays impossible for both.

## The checks that were actually run, and what each one licenses

Only a phase whose named check this session ran and saw green got a validation directive.

- **U2** — `python -m pytest scripts/agent-harness/test_harness_config.py
  scripts/agent-harness/test_anchor_schema.py -q`, from the clean clone at `fba111d`:
  `64 passed, 1 skipped in 20.61s`, exit 0. This licenses a claim about the *schema
  cross-reader test* requirement in U2's column. It licenses nothing about U2's gym
  requirements (a goal driven from a git issue through sweep→plan→weekly thread→approve; an
  overlapping issue pair flagged by the synthesis), which were not run.
- **U6** — `python scripts/checks/recall-falsifiability-drill.py`: exit 0,
  `ALL MUTATIONS RED - every guard can fail`; and
  `agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest
  agent-org/agent-bridge/tests/test_recall_seams.py -q`: `25 passed in 26.21s`, exit 0.
  **Not from the bare clone** — see the next section.
- **U8** — `scripts/checks/dfu-done.ps1 -Only 2,7 -SkipLive` from the clean clone.
  `phase-floor-matches-plan` was RED before the PLAN edit ("pinned but unnamed: U8") and green
  after. That is U8's *second* column requirement. U8's first — "each H-item's own runnable
  check in §C.9" — was **not** run and is not claimed anywhere.

## `recall-falsifiability-drill.py` is green while checking nothing under a bare interpreter

Established class, from this effort's own list: *a check green while checking nothing*.

The drill mutates a guard's source, runs the guard's test, and scores the mutation RED when the
test process fails:

```
red = p.returncode != 0
```

A `ModuleNotFoundError` is also `returncode != 0`. Run from a checkout whose default interpreter
has no `sqlalchemy`, eleven of the twelve mutations print

```
RED   seam 4 injection deleted   E   ModuleNotFoundError: No module named 'sqlalchemy'
```

and the drill exits **0** with `ALL MUTATIONS RED - every guard can fail`. The guards were never
executed. There is no positive control: the drill never asserts that the *unmutated* test is
green, so "the test failed because I broke the code" and "the test failed because the
environment cannot run it" are the same observation to it.

Distinguishing run, same tree, same command, one environment variable:

```
$env:AI_STACK_PYTEST_PYTHON = "…\agent-org\agent-bridge\.venv\Scripts\python.exe"
python scripts/checks/recall-falsifiability-drill.py
```

```
RED   seam 4 injection deleted            1 failed, 2 passed, 44 deselected in 8.78s
RED   seam 3 stops stripping the block…   1 failed, 46 deselected in 2.99s
…
ALL MUTATIONS RED - every guard can fail        exit 0
```

Real failures, real counts. So U6's guards **are** falsifiable — the drill's verdict is correct
here — but the verdict it prints is not *evidence* of that unless the runner is checked, and
nothing in the drill or in WALKTHROUGH.md's *How to run* line says which interpreter to use.

Two smaller siblings, same command:

- from a **bare clean clone** the drill crashes rather than refusing:
  `FileNotFoundError: …\OB1\integrations\kubernetes-deployment\agent-memory-ranking.ts` — the
  OB1 submodule is not initialised in a plain clone, and the twelfth mutation reads a file
  inside it. A missing submodule should be a refusal with a reason, not a traceback nine
  mutations in.
- the drill's dependency on the agent-bridge venv is undeclared. `AI_STACK_PYTEST_PYTHON`
  exists as the seam; nothing points a reader at it.

Fix shape, if this is ever picked up: assert the unmutated test is GREEN before scoring any
mutation, and treat a non-zero exit whose output contains a collection/import error as
INDETERMINATE rather than RED.

## U3's park cannot be lifted today — the drill refuses for want of evidence

`scripts/agent-harness/u3_evidence_regression_gym.py` is where U3's column discharges. Run
2026-09-01:

- clean clone, no arguments → `VENUE REFUSED: venue 'gym' resolves to …\ai-orchestration-gym,
  which is not a directory` (exit 2).
- clean clone, `--repo "D:\Open WebUI\ai-orchestration-gym"` → venue resolves
  (`venue : gym (gym) - D:\Open WebUI\ai-orchestration-gym @ main`), then
  `NO EVIDENCE: …\.quadrant\gym-runs holds no outcome record from venue 'gym'. Run the quadrant
  comparison in the arena first - this drill seeds a copy of REAL run evidence and will not
  fabricate one.` (exit 2)

`.quadrant/gym-runs` is present in no checkout on this machine — `find . -type d -name gym-runs`
returns nothing in the main tree or any of the three live worktrees. `.quadrant/runs` holds only
`COMPARISON.md`, `comparison.json`, `matrix.json` from the U4 work.

The 2026-08-30 ledger entry *"U4 + U3 — the arena runs LANDED"* reports U3 as discharged with a
content-addressed check id (`fd500152ab692af3`). That id appears in exactly one file in the
repository — DECISIONS.md itself. So the record's only witness is the record. That is not a
reason to call the run fictional; it is a reason the claim cannot be re-derived, which is the
standard §C.8 clause 1 sets.

**U3 stays parked. Clause 2's `no-outstanding-parked` stays red on it, deliberately.** What
would discharge it: a four-quadrant `quadrant` comparison run in the arena leaving records under
`.quadrant/gym-runs`, then `u3_evidence_regression_gym.py` exiting 0.

## Residual polish, filed under §C.10 rather than rounded

- `-Only 2,7` passed to `powershell -File dfu-done.ps1` binds as the single integer `27`, and
  the run then reports all eight clauses `unevaluated` without complaining that no clause
  matched. `-Only @(2,7)` through the call operator works. A `-Only` value matching zero
  clauses should be a config error.
- The A3 revert-path label (`**Revert:**` vs `**Revert path:**`) was a one-word mismatch that
  read as a missing revert path for two days. The parser accepts `**Revert path…` or a
  line-initial `REVERT:`; A1 and A2 happened to use the first, A3 the shape of neither.
