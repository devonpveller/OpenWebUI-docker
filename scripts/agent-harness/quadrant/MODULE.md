# MODULE — `quadrant`: the runner x target comparison

PLAN `dark-factory-unification` §1 L3 names two orthogonal axes — **runner**
(`little-coder` | `claude-code`) and **target** (`self` | `project(<repo>)`) — and §2's U4
row is validated by *"Gym: same anchored item run per quadrant (runner x target), outcomes
compared"*. This module is that comparison.

## What it is for

Not "show that the quadrants differ". **Let someone DECIDE between them.** A table saying
two cells finished and two did not supports no decision; the module therefore measures
quality against one fixed executable target, the cost of reaching it, the iterations and
human taps it took, whether the work stayed in scope, what actually contained the runner,
and how confident a reader may be at this sample size.

## The failure mode it is built against

> A comparison silently missing two of four quadrants reads as a completed comparison.

Three mechanisms make that unrepresentable (stated as data in `schema.json`, enforced in
`record.py` / `report.py`, and each proven to bite by `prove_guards.py`):

1. **The report is built from the MATRIX, never from the records.** Every configured cell
   gets a row; a cell with no record renders `NOT RUN - no record produced`.
2. **Evidence-gated admission.** `completed` is admitted only when timestamps, a non-zero
   wall clock, evidence paths that EXIST on disk, and acceptance entries carrying the
   command and its exit code all attest it. A status field is a claim; the artifacts are
   what make it a measurement (§0 A6).
3. **One item, proven by digest.** Every record carries the sha256 of the item spec,
   anchor and planted bytes it ran. A record from a different item is refused, so "the
   same anchored item" is mechanical rather than assumed.

## Public surface

| Entry point | Contract |
|---|---|
| `python -m quadrant.cli preflight` | one line per configured cell: READY, or BLOCKED with the reason. Exit 0 when all ready, 1 otherwise. |
| `python -m quadrant.cli run [--runner R] [--target T] [--item I]` | runs the selected cells, writes one run directory each, then renders the FULL-matrix report. Exit 0 iff every cell it attempted completed. |
| `python -m quadrant.cli report` | re-renders from the accumulated records. **Exit 0 only when all four cells produced an admitted comparable outcome**; 1 while the comparison is incomplete. |
| `python -m quadrant.prove_guards` | mutation drill: breaks each guard in turn and requires its test to go RED. Exit 0 iff every guard bites. |
| `python -m pytest scripts/agent-harness/test_quadrant.py -q` | 33 checks over the matrix, admission, the report's completeness invariant, and one end-to-end fixture run. |
| `quadrant/guards.py <tests\|unmodified> --item <id>` | the acceptance checks themselves, run by the harness with the workspace as CWD. |

Artifacts: `<repo>/.quadrant/runs/<utc>-<runner>-<target>/{record.json,transcript.txt,manifest.json,workspace/}`
plus `COMPARISON.md` and `comparison.json` at the runs root. Gitignored — evidence for a
run, not source.

## Configuration

Everything lives in `harness.config.json` (one file, the module only reads it):

- `runners.*` — extended with `fixture` (`status: "self-test"`).
- `targets.self` / `targets.project` — the target axis, new.
- `quadrant.{runners,targets,repeats,results_dir,item}` — which cells exist.

Adding a fifth runner or a third target is a config edit plus one probe and one adapter
branch; the matrix, the report and the exit codes follow automatically.

## Dependencies, one way only

`cli -> {matrix, record, report, item, adapters}`; `report -> {matrix, record}`;
`record -> matrix` (for the schema); `item -> anchor_schema` (the shared anchor contract,
reused rather than re-implemented); `guards -> item`. Nothing in the harness imports
`quadrant`.

## To delete this module

Delete `scripts/agent-harness/quadrant/`, `scripts/agent-harness/test_quadrant.py`, the
`targets` and `quadrant` sections plus the `fixture` runner from `harness.config.json`, the
`.quadrant/` line in `.gitignore`, and any `.quadrant/` directory. Nothing else in the
workspace references it.

## Known limits (as of 2026-08-30)

- **Two of four cells have never run.** `little-coder x *` is blocked: the running
  container publishes no ports, so the API `harness.config.json` declares
  (`http://127.0.0.1:8090`) has no route from the host. The harness reports this as
  NOT RUN with that reason; it does not implement a second dispatch, because one is being
  built as its own item.
- **n=1.** `quadrant.repeats` is 1, so no cell's number can yet be separated from one
  run's luck. The report says so in the confidence column and again at the bottom.
- **The fixture runner is scaffolding**, permanently excluded from decision tables by its
  `self-test` status, and `test_quadrant.py` asserts no profile can assign a role to it.
