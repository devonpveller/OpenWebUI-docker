# MODULE — `quadrant`: the runner x target comparison

PLAN `dark-factory-unification` §1 L3 names two orthogonal axes — **runner**
(`little-coder` | `claude-code`) and **target** (`self` | `project(<repo>)`) — and §2's U4
row is validated by *"Gym: same anchored item run per quadrant (runner x target), outcomes
compared"*. This module is that comparison.

**The column has THREE parts, and the first one is a place.** §2's preamble, four lines
above the phase table, binds it: *"'gym' means measured runs in `ai-orchestration-gym`,
never live planes or a real target."* So a comparison is over one item, one set of cells,
**and one VENUE** — see `venue.py` and mechanisms 5-7 below.

## What it is for

Not "show that the quadrants differ". **Let someone DECIDE between them.** A table saying
two cells finished and two did not supports no decision; the module therefore measures
quality against one fixed executable target, the cost of reaching it, the iterations and
human taps it took, whether the work stayed in scope, what actually contained the runner,
and how confident a reader may be at this sample size.

## The failure mode it is built against

> A comparison silently missing two of four quadrants reads as a completed comparison.

Seven mechanisms make that unrepresentable (stated as data in `schema.json`, enforced in
`record.py` / `report.py` / `cli.py` / `venue.py`, and each proven to bite by
`prove_guards.py`). Mechanisms 1-4 are about WHAT ran; 5-7 are about WHERE, and each of
those three was added because the previous one described itself by what it was for rather
than by what it did:

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

5. **One venue, pinned per results set.** Added 2026-08-30 after a verifier found the
   dimension missing entirely: mechanisms 1-4 were all satisfied by a four-cell, exit-0,
   fully evidenced comparison that ran **against ai-stack**, with `target: self` resolving
   to the repository the harness lives in — and nothing in the record, the report, the exit
   code or the config could say so. The config's own restatement of U4's column had even
   dropped the leading word `Gym:`. A `venue` is now a name, a kind, a repo and a ref
   (`quadrant.venue` / `quadrant.venues`); `venue.probe` REFUSES a `gym`-kind venue that
   resolves to the harness's own repository (compared by **git common dir**, so a worktree
   of ai-stack is recognised as ai-stack) and refuses one that is not a repository **root**
   (git discovers upward, so a wrong path silently adopts whatever repo encloses it —
   measured: `C:/Users/<user>` is itself a repo on this machine, so every path under the
   home directory, temp included, answers `git rev-parse` with the personal repo). Every
   record carries its venue, the results set pins it in `matrix.json` on first write, and
   admission refuses a record from any other one.

6. **A venue is identified by its REPOSITORY, not by its name** — mechanism 5's own defect,
   found by a verifier the day after it shipped and fixed the same day. Three of mechanism
   5's public sentences were about a label:
   - `record.admit` compared the venue **NAME**. Four records re-pointed at
     `D:/SomeOther/arena-clone` with `venue.name` still reading `gym` were admitted into the
     arena's own results set: COMPARED 4/4, exit 0 — while `matrix.json`'s `_why` asserted
     that "record.admit refuses a record from any other venue".
   - the report rendered **today's** venue whenever the names agreed, so
     `report --results-dir <a gym set> --repo <ai-stack>` printed *"Venue: `gym` — SATISFIES
     a Gym: column"* over ai-stack's path, exit 0, and wrote it to `COMPARISON.md`.
   - *"satisfies a Gym: column"* asserted PLAN §2's preamble ("never live planes or **a real
     target**"), which no probe derives. `AI_STACK_GYM_REPO` pointed at a throwaway repo
     named `not-the-arena` printed it at READY 4/4, exit 0.

   So `venue.identity_of` records the repository's **root commit** reachable from the
   venue's ref — a name is config and a path is a filename, and both can be edited into
   agreement; a root commit cannot, and a checkout that moved still matches, which is
   correct. `record._venue_problems` compares identity where the record and the pin both
   carry one, **refuses** a record with no identity against a pin that has one, and compares
   every label only for a set that predates identity (saying so in the refusal). The report
   renders the **pinned** venue always; `--repo` at report time warns and changes nothing.
   The verdict is two sentences: **DECLARED** (what `schema.json` says the kind is worth),
   then explicit **CHECKED** and **NOT CHECKED** lists — the first NOT-CHECKED line being
   that no probe can tell a disposable arena from a real target. `preflight` prints it too.

7. **What the venue constrains is stated PER TARGET.** A `target: self` cell's workspace IS
   a detached worktree of the venue repository. A `target: project` cell's is a **fresh
   `git init` scratch repository** created for that run under the results directory, holding
   only the planted item — not in the venue repo at all. That is legitimate under §2's
   preamble (a per-run scratch repo is neither a live plane nor a real target) and it is not
   obvious, so the report renders it (`schema.json` `target_venue_binding`) rather than
   letting one venue heading be read as a claim about every row.

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
| `python -m quadrant.cli preflight` | prints the harness repo, the VENUE (kind, what the schema DECLARES it worth, its repository identity, and the first thing it does NOT check) and the item repo, then one line per configured cell: READY, or BLOCKED with the reason. Exit 0 when all ready, 1 otherwise. |
| `python -m quadrant.cli run [--runner R] [--target T] [--item I]` | runs the selected cells, writes one run directory each, then renders the FULL-matrix report. Exit 0 iff every cell it attempted completed. |
| `python -m quadrant.cli report` | re-renders from the accumulated records. **Exit 0 only when every DECLARED cell produced an admitted comparable outcome**; 1 while the comparison is incomplete; 2 misconfigured. Narrowing the configured axes cannot raise this to 0 (mechanism 4). |
| `python -m quadrant.prove_guards` | mutation drill: breaks each guard in turn and requires its test to go RED. Exit 0 iff every guard bites; prints `N/N guards proven to bite`. |
| `python -m pytest scripts/agent-harness/test_quadrant.py -q` | the module's suite - the matrix, admission, the report's completeness invariant, the declared-matrix lock, the UTF-8 chokepoint scan, the retained-evidence invariant, and one end-to-end fixture run. The count is whatever the command prints. |
| `python -m pytest scripts/agent-harness/test_quadrant_venue.py -q` | the VENUE's suite - config loudness, the venue-violation refusal, the repository-root refusal, relative-path resolution from the main checkout, admission by repository IDENTITY (and the label fallback for sets that predate it), the report rendering the results-set PIN rather than today's configuration, and the per-target note. |
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

`cli -> {matrix, record, report, item, adapters, venue}`; `report -> {matrix, record}`;
`matrix -> venue` (and `venue` imports only `proc`, so it stays a leaf and the cycle cannot form);
`record -> matrix` (for the schema); `item -> anchor_schema` (the shared anchor contract,
reused rather than re-implemented); `guards -> item`; `adapters -> lc_docker` (the
little-coder transport, kept out of `adapters` so the axis file stays about the two axes);
and everything that runs a subprocess -> `proc` (the UTF-8 chokepoint - see its docstring
for the failure that produced it). The only importer outside the package is its own suite: `git grep -ln "from quadrant import" -- '*.py' ':!OB1' ':!scripts/agent-harness/quadrant'`
prints `scripts/agent-harness/test_quadrant.py` and nothing else.

## To delete this module

Delete `scripts/agent-harness/quadrant/`, `scripts/agent-harness/test_quadrant.py`,
`scripts/agent-harness/test_quadrant_venue.py`,
`scripts/agent-harness/observe-oracle-on-stall.ps1` (its only consumer), the `targets` and
`quadrant` sections plus the `fixture` runner from `harness.config.json`, the `.quadrant/`
line in `.gitignore`, and any `.quadrant/` directory. Two rows in
`scripts/agent-harness/MODULE.md` then dangle and should go with it. Nothing else executes
against the module: `git grep -ln quadrant -- . ':!OB1'` lists only those files plus prose
that mentions it (this plan's `PLAN.md` / `DECISIONS.md` and two findings notes).

## Known limits (as of 2026-08-30, after the runner axis was closed)

- **n=1.** `quadrant.repeats` is 1, so no cell's number can be separated from one run's
  luck. The report says so in every confidence column and again at the bottom. The
  comparison supports *"both runners completed this item"* and nothing about which is
  better: the wall-clock spread (72.8/65.4s local vs 35.8/33.5s cloud) is one run each.
- **The two claude-code records are REUSED, not re-run.** They were produced by
  `work/u4quad` on 2026-08-30 and copied into the results set unmodified, because
  re-running them is real external spend for no new information. Their
  `evidence.workspace` still points into `.claude/worktrees/wt-u4quad`, so **removing that
  worktree makes them fail admission and the comparison drops to 2/4** - the honest
  behaviour of the admission gate. Whoever retires that worktree re-runs those two cells
  or accepts the drop. `documentation/notes/u4close-findings.md` F5.
- **The little-coder cells run on a MIRROR, and the record says so.** `lc_docker.py` copies
  the workspace into the container and copies the changed files back; the host workspace's
  `.git` is not carried across, so the runner gets a fresh `git init` and no history, while
  the claude-code cells get a real checkout. Every little-coder record carries that as a
  note. It is a real difference between the cells, not a detail.
- **The fixture runner is scaffolding**, permanently excluded from decision tables by its
  `self-test` status, and `test_quadrant.py` asserts no profile can assign a role to it.
- **Records produced before 2026-08-30 are no longer admissible.** They carry no venue, so
  `record.admit` refuses them with that reason. They were real runs in the wrong place; a
  refusal with a reason is the honest rendering, and deleting them would be worse.
- **`items/u4-stall` is not a comparison item.** It is deliberately unsatisfiable - a stall
  probe for `observe-oracle-on-stall.ps1`. `quadrant.item` names `u4-baseline`; the probe is
  only reachable with `--item u4-stall`. Scoring a comparison on an impossible task would
  measure nothing about the quadrants.

## History: what UNBLOCKED the runner axis (2026-08-30)

Both little-coder cells reported NOT RUN for one reason: this module spoke ONE transport and
it was the wrong one. `adapters._dispatch_little_coder` used the HTTP endpoint the config
declared (`http://127.0.0.1:8090`), and that door does not exist - the running container
publishes nothing (`docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'`
→ `{"9090/tcp":[]}`) while `docker exec little-coder curl -s http://localhost:8090/health`
answers 200. Merging `work/u4quad` with `work/dfu-u4` (which had already built the
docker-exec dispatch) turned that into a hard config error, and `quadrant/lc_docker.py` is
the transport that resolved it. The park's reason is in
`documentation/notes/u4quad-findings.md` F7; what lifted it is in
`documentation/notes/u4close-findings.md`.
