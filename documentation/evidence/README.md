# `documentation/evidence/` — the committed audit trail

PLAN §C.6 makes the audit trail the deliverable's twin. This directory is where that twin
lives when it is a *run record* rather than a sentence: the artifacts a phase's *Validated by*
column is satisfied by, committed, so a fresh clone can re-derive the verdict instead of
taking a walkthrough row's word for it.

## Why it exists — the failure it was created in response to

U4's four-quadrant comparison ran on 2026-08-30 and genuinely reached `COMPARED 4/4, exit 0`.
It was written to `.quadrant/gym-runs` **inside the per-session worktree that produced it**,
and `.gitignore` covered `.quadrant/` with the comment *"run artifacts (evidence for a run,
not source)"*. The branch merged, the worktree was removed, and the evidence went with it.
On 2026-08-31 the walkthrough's summary table still said *"4/4 quadrants ran in the arena"*
while the deliverable's own machine output said:

    $ python -m quadrant.cli report
    **COMPARED 0/4**  - this comparison is INCOMPLETE
    exit 1

Nothing lied. The run happened. The evidence for it simply no longer existed, and no check
could tell the difference between *"this never ran"* and *"this ran and the proof was
deleted"* — which are the same thing to an auditor.

**Evidence a fresh clone cannot see is not evidence.**

## Which is which

| | where | tracked? | what it is |
|---|---|---|---|
| **evidence** | `documentation/evidence/<item>/…` | **YES** | the run records, retained workspaces, transcripts, manifests, comparison reports and ledgers that a column is closed on. Durable by definition: the claim outlives the session, so its proof must too. |
| **working / scratch** | `.quadrant/` | no (gitignored) | per-checkout working state — `scratch/` mirror staging, ad-hoc runs someone is iterating on, results sets nothing has been claimed on. Genuinely ephemeral: nothing cites it, and deleting it loses nothing anyone has asserted. |

The rule is not "big things are ignored". It is: **if a claim rests on it, it is committed.**

## Re-deriving it yourself

    python scripts/checks/check_quadrant_evidence_reproduces.py --auto

`--auto` searches `documentation/evidence/` as well as `.quadrant/`, so the banked durable
check still has something to audit in a checkout that has never run anything. It re-runs each
record's acceptance commands **in the workspace beside that record** and requires the exit
code the record claims.

Two properties make that possible from a clone, both added 2026-08-31:

- run records carry `acceptance[*].check_template` — the UNEXPANDED criterion — beside
  `check`, which is the exact command that ran and embeds the producing machine's interpreter
  and the producing worktree's `guards.py`. The checker re-expands the template against the
  checkout it is running in; `check` is never rewritten, because it is the historical fact.
- a `target: project` run's scratch `.git` is removed when the cell finalizes. A nested
  repository makes the run directory uncommittable — `git add` records a gitlink to a commit
  that exists in no remote, and the clone gets an empty directory where the workspace was.

## What is here

| set | produced by | says |
|---|---|---|
| `dfu-u4/quadrant/` | `python -m quadrant.cli run-all --item u4-baseline --results-dir …` | U4's runner × target comparison at venue `gym` |
| `dfu-u4/stall/` | `scripts/agent-harness/observe-oracle-on-stall.ps1 -ResultsDir …` | U4's `stall → oracle` observation, including the escalation ledger row and the three real failing rounds it was derived from |
| `dfu-u3/` | `python scripts/agent-harness/u3_evidence_regression_gym.py` | U3's seeded-regression run **in the arena**, with the red-proof beside it. The drill now seeds from the records under `dfu-u4/` — see below |
| `dfu-u6/` | `powershell -File scripts/checks/drill-u6-dark-gate.ps1` | U6's column at the real gate (HALT and CLEAR), its two red-proofs, and the pair of `drill-dark-factory.ps1` runs that measure why the broader drill is red |

## A record of a run is not a run's check — which is why two of these sets differ

`dfu-u4/` holds **run records**: a checker re-executes each one's acceptance in the workspace
beside it and requires the recorded exit code, so the verdict is *re-derived* rather than read.

`dfu-u3/` and `dfu-u6/` hold **transcripts and outcome summaries**. Those are records, and this
project's own banked rule says a record of a check is not a check — so each `outcome.json`
carries a `not_claimed` list saying exactly what it does not stand for. What makes them worth
committing anyway is the pairing: each set holds the GREEN run *and* the run with the checked
thing deliberately broken, so a reader can see the check's green was not free. Re-running them
is how they are verified, and each set names the command that does it.

**Why `dfu-u3/` exists at all.** `u3_evidence_regression_gym.py` seeds copies of REAL gym run
evidence, and it looked for that evidence only in `.quadrant/gym-runs` — the gitignored working
location. So it answered `NO EVIDENCE`, exit 2, in every checkout but the one that ran the arena
dispatch: byte for byte the loss described at the top of this file, in the drill that first
earned the rule. Its search now includes `documentation/evidence/`, still filtered on
`record.venue.name == 'gym'`, so it seeds from the committed U4 records — real runs from the
arena — and no widening of *where* it looks widens *what* it will accept.
