# Dark Factory Unification — the walkthrough

The index into the audit trail. §C.7 makes that trail the deliverable's twin, so this file is
held to the same standard as everything it points at: **it states what was verified, by which
command, and by whom — and it says "parked" where things are parked.**

A row saying DONE means its §2 *Validated by* column is satisfied by an executable check that
someone who did not build it re-ran. Anything else says PARKED, with what would close it.

**Every command in this file must run AS WRITTEN, from the repository root.** One did not:
U6's second check was listed as `python -m pytest agent-org/...` and fails with
`ModuleNotFoundError: No module named 'sqlalchemy'`, because it needs the agent-bridge venv.
Corrected 2026-08-31 to name that interpreter (it then passes, 25 tests). It was caught by a
verifier attacking `dfu-done.ps1`, not by this file — the checker captured only the FIRST
command after each *How to run* marker, so a red named check read green in the deliverable that
clause 5 exists to audit. Both are fixed.

**How to read the "verified by" column.** `orchestrator` = I ran the command myself.
`verifier` = an adversarial agent that did not build the item ran it and reported the output.
`merge-record` = it landed through the pipeline in an earlier session and I have the merge
commit but did not personally re-run its check in this run. That last one is the weakest and
is marked deliberately rather than rounded up.

---

## Status at a glance

| Phase | Status | One line |
|---|---|---|
| **U0** | DONE (merge-record) | The in-flight work landed; the durable inbox replaced the one-shot poller. |
| **U1** | DONE (merge-record) | Memory plane phases 0–2: schema, ops door, write paths. |
| **U2** | DONE (merge-record) | Intent unification: shared anchor schema, git-issue door, depth-1 ScopeNodes. |
| **U3** | DISCHARGED (closing with U4) | The arena run landed: seeds caught, check banked `source: tester-finding`, arena clean before/after. |
| **U4** | COLUMN MET, round 8 — **evidence now committed** | 4/4 quadrants ran in the arena and the stall was real, but round 7's proof of it was deleted with the worktree that made it: `report` answered **COMPARED 0/4, exit 1**. Re-run 2026-08-31 into `documentation/evidence/dfu-u4/` — **4/4, exit 0**, oracle fired, 7 records re-derivable. The *What* cell's "governs both" half is still undelivered — see §U4. |
| **U5** | **STEP 1 APPLIED TO LIVE** | RLS + FORCE on `thoughts` and nine `agent_memory*` tables. Canary proof: agent plane sees **0** personal, **12993** ops. Every PostgREST path bound — including the wiki compiler. Steps 2–3 (direct deno clients) open. |
| **U6** | **DONE** (clause 4 `3bdf7a8`, clauses 1–3 `8695deb`) | Recall at four+ seams, live-proven. Andon: 5 conditions halt at the real gate, verdict by exhaustive census, drill 213/0. Closed on §C.7's convergence bound. |
| **U7** | NOT STARTED | Standing, per §B. Depends on U6. |

---

## U0 — land what was in flight

**Built:** the three reviewed items merged; the durable Mattermost inbox replaced the one-shot
poller.
**Validated by (§2):** each item's own anchor + tester; inbox: a kill-the-poller drill proving
no message is lost.
**Evidence:** `68e016e Merge work/dfu-inbox: a durable inbox, so an operator message cannot
vanish`, over `cac1f85`.
**Verified by:** merge-record. I confirmed the merge exists and closed the stale queue row that
still read `test-passed` with an empty `merged_sha`. **I did not re-run the kill-the-poller
drill in this session.**

## U1 — memory plane, phases 0–2

**Built:** schema deploy, the ops door, and the write paths.
**Validated by (§2):** the memory-plane plan's own per-phase gates (in the sibling repo
`documentation-plans-ai-stack/implementation-guide/agent-memory-plane/PLAN.md` — **not** in
ai-stack; a session that searched only ai-stack once concluded it did not exist and rebuilt it
wrongly).
**Evidence:** `954b97b` (2.1 write path), `5a662d3` (2.2 outcomes), `4aed54f` (2.2 abort-path
thin records), `7982440` (2.3 constraint promotion), `ebfcbbc` (2.4 bridge rollups),
`105d835` (1.3 acceptance).
**Verified by:** merge-record, plus one orchestrator check — the plane holds **4 ops memories
and 0 personal rows**.

## U2 — intent unification

**Built:** shared anchor schema with executable criteria; the git-issue intake door on the
daily/weekly cadence; agent-org consuming and producing anchors at the `set_goal` seam;
reviewer verdict re-scoped to codebase-fit; queue items projected as depth-1 ScopeNodes.
**Validated by (§2):** a goal driven from a git issue through sweep→plan→weekly thread→approve;
an overlapping issue pair flagged by the synthesis; a schema cross-reader test.
**How to run:** `python -m pytest scripts/agent-harness/test_harness_config.py
scripts/agent-harness/test_anchor_schema.py -q`
**Evidence:** `840f29b` (ScopeNodes), `27c5355` (git-issue door), `39e4c03` / `9da169a`
(anchor schema, both directions).
**Verified by:** merge-record. `scripts/agent-harness/scope_node.py` confirmed present.

## U3 — verification unification — **PARKED**

**Built:** tester-finding→durable-check in both systems; failure signatures writing through to
the plane; executable acceptance criteria in anchors; the harness's drill pattern ported to
agent-org as an executable org drill.
**Validated by (§2):** *"Gym: a seeded regression must be caught by a check born from a tester
finding in a prior round (gym-007's shape, new source); drills green in both systems."*

| Half | State |
|---|---|
| drills green in both systems | **MET** — `scripts/agent-harness/verify-merge-protocol.ps1` and `agent-org/agent-bridge/tests/test_org_drill.py`, both confirmed present and reported green by verifiers |
| seeded regression caught by a check born from a **tester** finding, **in the gym** | **NOT MET** — the run was local, not in the arena |

**What would close it:** a run in `d:\Open WebUI\ai-orchestration-gym`, or an amendment
narrowing the arena clause with evidence that it cannot be run.
**A correction that belongs here:** the drill originally claimed *"nothing that already existed
catches either seed."* A verifier disproved it by running the pre-existing
`scripts/checks/check-watchdog-repair-targets.ps1 -SkipDocker` against seed A: exit 1, three
`[FAIL]` lines. The claim was narrowed. The new check's value rests on the genuine remainder.
**Evidence:** `c77306e`, `ed83a9c`, `01ad0a2`, `a9e271f`, `321829d`, and the status correction
`5f4817d`. Branch `work/u3gym` is unmerged.
**Verified by:** verifier (both halves), orchestrator (status).

## U4 — runner unification — **COLUMN MET on committed evidence; the *What* cell is not fully delivered**

**Round 8, 2026-08-31.** Validated at `1a6b0b8` (`refactor/ai-stack-cleanup`), on branch
`work/u4close`. Round 7 met the column and *deleted its own proof*; this round re-ran it into
a location a clone can read, and corrected two premises that had gone stale.

### What the machine says now — run it yourself

```
$ python -m quadrant.cli report --results-dir documentation/evidence/dfu-u4/quadrant
**COMPARED 4/4**
| little-coder x self    | completed | 2/2 | 65.5 | 1/2/0 | 1 changed | mechanical |
| little-coder x project | completed | 2/2 | 65.8 | 1/2/0 | 1 changed | mechanical |
| claude-code  x self    | completed | 2/2 | 35.4 | 1/2/0 | 1 changed | normative  |
| claude-code  x project | completed | 2/2 | 35.2 | 1/2/0 | 1 changed | normative  |
                                                                        exit 0

$ python scripts/checks/check_quadrant_evidence_reproduces.py --auto
7 outcome record(s) re-derived their verdict from the evidence they kept    exit 0

$ ./scripts/agent-harness/observe-oracle-on-stall.ps1 `
      -ResultsDir documentation/evidence/dfu-u4/stall -LeaseOwner <you>
  3 real dispatches of the unsatisfiable item; round 3 STALLED (2 rounds, no new information)
  ORACLE-ON-STALL: little-coder/local-default -> claude-code/opus, hand back to little-coder
  ledger row 417aa274750da712 - APPENDED BY THIS RUN (0 rows before, 1 after)   exit 0
```

**Validated by (§2):** *"same anchored item run per quadrant (runner × target), outcomes
compared; stall→oracle observed firing at least once"* — **both halves met**, at venue `gym`
(`D:\Open WebUI\ai-orchestration-gym`, identity `root:f12ba2ec…`), on evidence that is
**committed**: `documentation/evidence/dfu-u4/`.

### THE DEFECT THIS ROUND EXISTS TO FIX — evidence that could not survive a clone

Round 7's comparison was real: four cells, real dispatches, `COMPARED 4/4, exit 0`, confirmed
by verifiers. It was written to `.quadrant/gym-runs` **inside the per-session worktree that
produced it**, and `.gitignore:88-89` covered `.quadrant/` as *"run artifacts (evidence for a
run, not source)"*. The branch merged, the worktree was removed, and the proof went with it.
On 2026-08-31 the summary table above still read *"4/4 quadrants ran in the arena"* while:

    $ python -m quadrant.cli report
    **COMPARED 0/4**  - this comparison is INCOMPLETE          exit 1

Not 4/4 as the row said, and not the `COMPARED 2/4` this section used to quote either — that
number was itself a stale citation of an earlier round. **Both places were wrong, in different
directions, and the machine agreed with neither.** No one lied; the runs happened. But an
auditor cannot distinguish *"this never ran"* from *"this ran and the proof was deleted"*, and
§C.6 makes the audit trail the deliverable's twin. Evidence a fresh clone cannot see is not
evidence.

Four things changed so this cannot recur, each proved RED before GREEN:

| | |
|---|---|
| evidence has a committed home | `documentation/evidence/` (+ its `README.md` and a `-text` `.gitattributes`, because `guards.py unmodified` compares BYTES). `.gitignore`'s `.quadrant/` rule stays, with its comment corrected: it covers WORKING state, not evidence |
| the banked check can reach it | `check_quadrant_evidence_reproduces.py --auto` now searches `documentation/evidence/` as well as `.quadrant/` — otherwise every set it could find is one a clone does not have |
| a record can be re-run elsewhere | run records now carry `acceptance[*].check_template` beside `check`. `check` is the exact command that ran and embeds the producing machine's interpreter and the producing worktree's `guards.py`; the checker re-expands the template against ITS checkout. `check` is never rewritten |
| a `project` run directory is committable | its scratch `.git` is removed at finalize. A nested repo makes `git add` record a gitlink to a commit in no remote, so the clone gets an empty directory where the workspace was. The first version used `shutil.rmtree(ignore_errors=True)`, which silently left git's read-only pack files in place — caught by the new test, fixed with a chmod-retry that RAISES if anything survives |

### Two premises in §2.1 A1 have gone stale and are now FALSE

The amendment was correct when written. Both of its supporting facts were falsified by work
that landed after it — `dispatch.ps1` (merged in `211febc`) and `oracle_on_stall.py`
(`5dbf05b`) — and are re-measured here:

| A1 says | measured 2026-08-31 at `1a6b0b8` |
|---|---|
| "`Resolve-RoleTarget` has **zero executable callers** repo-wide" | **FALSE.** `dispatch.ps1:88` calls it; `verify-dispatch.ps1` reaches it 12 times; its Python twin `config.resolve_role` is called by `oracle_on_stall.py:223`, which `queue.ps1` runs on **every `-Fail`** (`Invoke-OracleOnStall`, `queue.ps1:219`) |
| "the runner `status` field is **read nowhere**" | **FALSE**, and it is decision-bearing: `quadrant/matrix.py:175` gates comparability on it. Measured by flipping it — `little-coder.status=unproven` → `comparable=True`; `=self-test` → `comparable=False`, kept out of the decision table |

**What is TRUE, and stated at its real width.** The profile mechanism governs **one
direction**, partially:

- it selects the runner and model a role is dispatched to (`dispatch.ps1`);
- it decides **which runner a stall escalates to** — measured: profile `all-local` →
  `escalate little-coder/local-default → claude-code/opus`; profile `all-cloud` →
  `no-oracle-above` (*"the worker already runs on 'claude-code'"*), i.e. no escalation at
  all. The committed ledger row carries `"profile": "local-work-cloud-review"`;
- it decides whether a quadrant's outcome may enter a decision table (`matrix.py`);
- it is what an operator sees in `profile: list` (`describe_runner`).

It does **not** govern the pipeline's own execution: `queue.ps1` never starts a runner, so
choosing a profile does not change which agent picks up a worker or tester role. And the
**agent-org direction is absent from this line entirely** — `work/u4bidir` built a runner
registry for it and is abandoned by operator direction (below). *"One profile mechanism
governs both"* remains **false in the agent-org direction**, and that is a *What*-cell debt,
not a column debt: §2.1 A1 states explicitly that the amendment *"does not touch the
Validated-by column."*

### `check-runner-endpoints.ps1` — the false `.Port` sentence ships nowhere, because the file does not exist

Re-verified independently, PowerShell 5.1.26100.8875, under the script's own preamble
(`Set-StrictMode -Version Latest`, `$ErrorActionPreference = "Stop"`):

    $Error.Clear(); $u = [Uri]'not a url at all'
    $u.IsAbsoluteUri -> False    $null -eq $u.Port -> True    $u.Host -> ''    $Error.Count -> 0

`.Port` does **not** throw on a relative Uri. The .NET getter raises and PowerShell swallows
it: no throw, no error record. The claim that it "would have CRASHED THE SCRIPT" is false.

And the file carrying that sentence is **on no branch**. `git ls-files` has no
`check-runner-endpoints.ps1`; the only versions in any ref are `origin/work/u4bidir`'s
`aabb781` and `ec4ed8d`, and neither contains the word *relative* or *throw* near `.Port` —
the round-3 revision that introduced it was never pushed and its worktree is gone. There is
no code to fix and no surrounding logic to correct on this line. What was wrong was **this
document**, which listed the sentence as a live known-open defect in the deliverable.

### Branches — what was salvaged and what was abandoned

`work/dfu-u4`, `work/u4quad` and `work/u4oracle` are **ancestors of `1a6b0b8`** — merged in
`211febc`, `88d5035`, `5dbf05b`. The line here listing all four as unmerged was stale.
Nothing had to be salvaged from them; what they built is what this round re-ran.

**`work/u4bidir` is ABANDONED** (operator direction). With it go the agent-org
`RunnerRegistry`, `check-runner-endpoints.ps1` and `verify-runner-endpoint-check.ps1`, ~2,400
lines. It was refuted 2/2 on defects the orchestrator confirmed by reading the source (a
reachability check that could not fail for the rows it validated; a registry fallback that
turned compose's documented empty-env disable path into two enabled workers), it is ~87
commits behind the work line and therefore UNVALIDATED under §C.7b regardless, and its
findings note carries a false sentence in code. Its absence is exactly why *"governs both"*
stays false in the agent-org direction.

**Evidence:** `documentation/evidence/dfu-u4/` (committed),
`documentation/notes/u4close-findings.md`,
`documentation/notes/u4-round8-evidence-durability.md`.
**Verified by:** this session ran every command above and pasted its real output; **an
independent re-run is still owed** — this file's own rule is that a row says DONE when a
verifier who did not build it re-ran the column. That has not happened for round 8.


## U5 — containment parity — **PARKED, closure in flight**

**Built and proven:** the exposure plane is forced server-side on every agent-memory **read**
tool; a refusal returns `not_found` (existence is itself a disclosure) and writes a durable
`access_refused` audit row. A verifier reproduced this live: `agent_memory_inspect` on a
personal fixture returns `Refused (not_found)` and the audit count moves. The `--no-verify`
retry after a `commit-msg` refusal is closed, RED→GREEN with byte-identical hooks.

**Validated by (§2):** *"an agent instructed to bypass hooks / reach personal-plane data is
mechanically stopped and the attempt is visible in an audit record."*

**Why it is parked — two open findings, both orchestrator-verified:**

1. **The second home.** `performWriteback` mirrors a memory's full `content` into `thoughts`
   with `metadata.exposure`, and **no reader of `thoughts` consults that label** — `index.ts` has
   6 `FROM thoughts` statements, 36 query sites, and the word `exposure` appears once, in a
   comment. Live: `agent_memory_inspect` refuses the id while `search_thoughts` returns the
   content verbatim, no audit row. **Deployed** — production `thoughts` holds 4 rows labelled
   `ops`, matching the 4 ops memories.
2. **The third home.** `openbrain-postgrest` runs `PGRST_DB_ANON_ROLE=service_role`; that role
   holds `SELECT, INSERT, UPDATE, DELETE, TRUNCATE` on `agent_memories`; a live GET from a
   container on `open-brain_obnet` returns **200**. Read *and* write, unauthenticated, bypassing
   both doors. **Bounded:** `3000/tcp` has no host binding, so it is not host- or
   internet-reachable, and personal rows are 0.

**STANDING CONSTRAINT: do not write a personal-exposure memory.** A round-5 note claimed this
was LIFTED; the lift was **withdrawn** — the wiki compiler reads the same content and the drill
never fires at it. It may be re-proposed only when the drill's door set is derived the way its
file set is, and the compiler path is closed.

**Round 5 fixed the hard part:** the branch pins OB1 `e26a742`, verified AT the commit rather
than in the working tree, reachable on the remote, and descending from `adb7345` so merged
recall work is preserved — and the drill now refuses to run unless OB1's HEAD matches the
gitlink, with no override. Round 4's decisive defect is closed.

**Round 5 also found a fourth reader and a gate blind spot:** `generate-wiki.mjs` selects
`thoughts` and `thought_entities?select=thoughts(content)` directly on the published
`--batch`/`--ids` path (it only calls `match_thoughts` under `--semantic-expand`), so the SQL
floor does not cover it — a scheduled service publishing corpus content. Orchestrator-verified
unauthenticated from `open-brain_obnet`: both endpoints return **200**; `wiki_pages` holds
**48,032 rows**. And the completeness gate skipped every non-`.ts` file, so it scanned **none**
of the openbrain-wiki image (0 `.ts`, 5 `.mjs`) nor the bind-mounted `../recipes`.

**Superseded original wording:** It is
unexploitable only because the personal plane is empty.
**What would close it:** (1) is in flight — extend the boundary to every `thoughts` reader and
lift the constraint on reproduced refusals, not on assertion. (2) is an **operator decision**:
narrowing those grants touches live consumers (recipes, Open Notebook).
**A merge hazard, recorded:** the work line's OB1 gitlink is now `adb7345`. `work/u5pplane`
pins `8e3f164`; merging it as-is would drag OB1 **backward** and revert merged recall work.
**Full detail:** `documentation/notes/personal-plane-second-home-LATENT-LEAK.md`,
`documentation/notes/u5-round2-findings.md`.

## U6 — dark-factory mode — **clause 4 DONE; clauses 1–3 in round 4**

### Clause 4 — recall-informed briefs at all four seams — **DONE**
**Validated by:** deleting any seam reds a test that names *that* seam; and the live acceptance.
**How to run:** `python scripts/checks/recall-falsifiability-drill.py`, and
`agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q`
**Evidence:** `3bdf7a8`. Two verifiers not-refuted; they counted **4 and 5** live seams (one
found an `_open_handoff` seam beyond the four the plan names).
**Orchestrator-verified:** `agent_memory_recall_traces` went **0 → 8 rows** — recall has run
against a real Open Brain, not a fake transport. Personal rows still 0. Gitlink `adb7345`
confirmed reachable on the OB1 remote before merging.
**Disclosed, not hidden:** `AGENT_MEMORY_RECALL_RECENCY_WEIGHT` defaults to 0, so the phase-2
re-rank is order-preserving — two-phase overfetch is proven in tests and a **no-op in
production** until that tuning is set. Threshold calibration remains blocked on corpus size (4).

### Clauses 1–3 — andon config, `dark`/`attended` profiles, auto-pass audit records — **round 5 in flight**
**Confirmed working by two verifiers, in their own fixtures:** all **5** andon conditions fire on
real instances and stay quiet on clean ones; the halt works end-to-end at the real gate (exit 6,
item parked, condition named in a `decision=refused` ledger record); `DISABLED` is
distinguishable from `EVALUATED-OK` across four byte-distinct board states; a **thinned** board
(entries deleted, disabled, or renamed) refuses and names the missing ids; the negative control
still auto-passes at exit 0 signed `auto:dark` with `-VerifyAudit COMPLETE`.
**Still open (round 5):** `on_fire` is FIXED and verified — a downgraded condition now yields
board `WARNED`, the gate refuses at exit 6, and the ledger carries `fired:[...] halted:[]`. The
guard now pins **values**: swapping a predicate while keeping its id turns 4 tests red. But
`on_indeterminate: warn` reopened the identical hole on the **sibling key** — an unevaluated
condition counted as evaluated, the board fell through to `clear`, the dark gate auto-passed at
exit 0 signed `auto:dark`, `-VerifyAudit` said COMPLETE, and the condition was **absent from the
ledger entirely**.

**Root cause, and why round 5 changed the instruction rather than patching another key:** the
verdict was computed **by exception** — `$raised` set only on `halt`, `$firedIds` only on
`fire` — so any outcome nobody enumerated silently meant *fine*. Round 5 requires `clear` to be
**proven**: every condition outcome into exactly one counted bucket, buckets summing to the
declared count, `clear` requiring all non-ok buckets empty, unrecognised statuses refusing, and
the census carried into the ledger so completeness can be re-derived rather than trusted. The
red-proof is inventing a **new** outcome word and showing the board refuses it *without* a
branch being added for that word.

**Note:** U6's *column* has been met for two rounds. The refutations are against claims the
branch added **beyond** its column. Branch `work/u6dark` is unmerged.

## U7 — post-development design iteration — **NOT STARTED**

Standing, per §B: real-world outcomes → proposed design changes → judged against the pinned
research anchors → trialled in the gym → adopted or refused on the record.
**Validated by (§2):** the evidence ledger itself — every design change carries its anchor
citation or its ledger amendment.
**Depends on:** U6. §2.1 A1 is the first entry of the kind U7 institutionalises.

---

## What this run found that was not in the plan

Ten-plus checks that were **green while checking nothing**, and the pattern behind them. The
recurring shape is not a missing test; it is a guard whose completeness rests on a list. Named
instances, each executed:

- an assertion pattern matching **zero lines** of the file it inspected, passing as
  "refusal at none";
- a completeness test whose enumeration was a hand-written 6-entry file list — an unguarded
  by-id resolver in a file named anything else left the suite at 154/0;
- a seam-4 assertion satisfied by **seam 2**, so deleting seam 4 left 32/32 green;
- a reachability check that could not fail for the container rows it existed to validate;
- a guard asserting only that a config list was **non-empty** — and its replacement asserting
  only that two fields were **truthy**, the same vacuity one round later.

- a board verdict computed **by exception**, where `clear` was simply what you got when nothing
  objected — so `on_fire: warn`, then `on_indeterminate: warn`, each silently meant "fine".

The rule adopted: **enumerate-and-patch loses.** Enforce at a chokepoint that cannot be bypassed
by omission, and derive the completeness test from a **scan of the code** — then prove it has
teeth by adding an unguarded site yourself.

Two incidents and three orchestrator errors are recorded in `DECISIONS.md` under 2026-08-30,
including one hypothesis I later **retracted** after re-testing it in the right shell. They are
in the log because a trail that only records successes is not an audit trail.

**The sharpest form of it**, found in U6 and worth stating separately because it names the
shape rather than an instance:

> A guard that decides by **exception** — flagging the cases it recognises and defaulting to
> "fine" — is not a guard. It is a list of the failures someone thought of, wearing the costume
> of a decision. Its successor decides by **exhaustive accounting**: every input lands in a
> counted bucket, the buckets must sum, and the passing verdict requires every failing bucket to
> be provably empty. The difference is testable — invent an outcome nobody enumerated, and see
> whether the guard refuses it or waves it through.

Three consecutive U6 rounds fixed a key and left its sibling. That is the signature of deciding
by exception, and it is why the fix was eventually aimed at the shape instead of the keys.
