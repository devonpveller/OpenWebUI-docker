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

Four mechanisms make that unrepresentable (stated as data in `schema.json`, enforced in
`record.py` / `report.py` / `cli.py`, and each proven to bite by `prove_guards.py`):

1. **The report is built from the MATRIX, never from the records.** Every cell in the
   matrix gets a row; a cell with no record renders `NOT RUN - no record produced`.
2. **Evidence-gated admission.** `completed` is admitted only when timestamps, a non-zero
   wall clock, evidence paths that EXIST on disk, and acceptance entries carrying the
   command and its exit code all attest it. A status field is a claim; the artifacts are
   what make it a measurement (§0 A6).
3. **One item, proven by digest.** Every record carries the sha256 of the item spec,
   anchor and planted bytes it ran. A record from a different item is refused, so "the
   same anchored item" is mechanical rather than assumed.
4. **The matrix is DECLARED per results set, and only ever grows.** Added 2026-08-30 after
   a verifier reproduced this module's own failure mode through its shipped CLI: mechanism
   1 is only as strong as what "the matrix" is, and building it from today's configuration
   let a one-line edit (`quadrant.runners: ["claude-code"]`) drop the two never-run cells
   out of the table - the same evidence then read `COMPARED 2/2`, complete, exit 0. A
   comparison is now over the union of the results set's pinned `matrix.json`, the
   configured cells, and every cell a record on disk names. A declared cell that is no
   longer configured renders `OFF MATRIX`, carries whatever reason its records gave, and
   counts against completeness.

### What mechanism 4 does not defend against

A comparison begun in a **fresh** results directory with narrow axes is a genuinely narrow
comparison, and it says so: `COMPARED 2/2` over a two-cell declared matrix, with the axes
written into `matrix.json`. Laundering requires reusing an existing evidence set under a
smaller matrix, and that is what the lock stops. The one remaining move - delete
`matrix.json` **and** the records of the cells being hidden - is a deletion of evidence
rather than a report that lies about it.

## Public surface

| Entry point | Contract |
|---|---|
| `python -m quadrant.cli preflight` | one line per configured cell: READY, or BLOCKED with the reason. Exit 0 when all ready, 1 otherwise. |
| `python -m quadrant.cli run [--runner R] [--target T] [--item I]` | runs the selected cells, writes one run directory each, then renders the FULL-matrix report. Exit 0 iff every cell it attempted completed. |
| `python -m quadrant.cli report` | re-renders from the accumulated records. **Exit 0 only when every DECLARED cell produced an admitted comparable outcome**; 1 while the comparison is incomplete; 2 misconfigured. Narrowing the configured axes cannot raise this to 0 (mechanism 4). |
| `python -m quadrant.prove_guards` | mutation drill: breaks each guard in turn and requires its test to go RED. Exit 0 iff every guard bites; prints `N/N guards proven to bite`. |
| `python -m pytest scripts/agent-harness/test_quadrant.py -q` | the module's suite - the matrix, admission, the report's completeness invariant, the declared-matrix lock, and one end-to-end fixture run. The count is whatever the command prints; on 2026-08-30 it printed `39 passed`. |
| `quadrant/guards.py <tests\|unmodified> --item <id>` | the acceptance checks themselves, run by the harness with the workspace as CWD. |

Artifacts: `<repo>/.quadrant/runs/<utc>-<runner>-<target>/{record.json,transcript.txt,manifest.json,workspace/}`
plus `COMPARISON.md`, `comparison.json` and `matrix.json` (the declared matrix, append-only) at the runs root. Gitignored — evidence for a
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
reused rather than re-implemented); `guards -> item`. The only importer outside the package
is its own suite: `git grep -ln "from quadrant import" -- '*.py' ':!OB1' ':!scripts/agent-harness/quadrant'`
prints `scripts/agent-harness/test_quadrant.py` and nothing else.

## To delete this module

Delete `scripts/agent-harness/quadrant/`, `scripts/agent-harness/test_quadrant.py`, the
`targets` and `quadrant` sections plus the `fixture` runner from `harness.config.json`, the
`.quadrant/` line in `.gitignore`, and any `.quadrant/` directory. Two rows in
`scripts/agent-harness/MODULE.md` then dangle and should go with it. Nothing else executes
against the module: `git grep -ln quadrant -- . ':!OB1'` lists only those files plus prose
that mentions it (this plan's `PLAN.md` / `DECISIONS.md` and two findings notes).

## Known limits (as of 2026-08-30)

- **The RUNNER axis has zero coverage, so U4's Validated by column is UNMET.** Both cells
  that ran are `claude-code`; this is a comparison of the TARGET axis alone. What the
  module can support today is a decision about `self` vs `project` at n=1. It cannot
  support any statement about `little-coder` vs `claude-code`, and the report says so
  rather than implying otherwise by silence. The park, its reason (rewritten 2026-08-30
  after a verifier disproved the first one with a single command) and what would lift it
  are in `documentation/notes/u4quad-findings.md`.
- **Two of four cells have never run**, because this module speaks ONE transport and it
  is the wrong one. `adapters._dispatch_little_coder` uses the HTTP endpoint
  `harness.config.json` declares here (`http://127.0.0.1:8090`), and that door does not
  exist: the running container publishes nothing
  (`docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` →
  `{"9090/tcp":[]}`) and `curl -s -m 4 http://127.0.0.1:8090/health` exits 7. The module
  reports both cells NOT RUN with that reason, which is accurate about the transport and
  must not be read as "little-coder is unreachable" — `docker exec little-coder curl -s
  http://localhost:8090/health` answers 200, and `work/dfu-u4` has driven a real anchored
  item through that route. The docker-exec transport is not added here because that item
  already wrote one (`scripts/agent-harness/dispatch.ps1`). See
  `documentation/notes/u4quad-findings.md` F7.
- **n=1.** `quadrant.repeats` is 1, so no cell's number can yet be separated from one
  run's luck. The report says so in the confidence column and again at the bottom.
- **The fixture runner is scaffolding**, permanently excluded from decision tables by its
  `self-test` status, and `test_quadrant.py` asserts no profile can assign a role to it.
