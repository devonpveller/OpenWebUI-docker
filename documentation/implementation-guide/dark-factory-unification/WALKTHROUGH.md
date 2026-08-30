# Dark Factory Unification — the walkthrough

The index into the audit trail. §C.7 makes that trail the deliverable's twin, so this file is
held to the same standard as everything it points at: **it states what was verified, by which
command, and by whom — and it says "parked" where things are parked.**

A row saying DONE means its §2 *Validated by* column is satisfied by an executable check that
someone who did not build it re-ran. Anything else says PARKED, with what would close it.

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
| **U3** | PARKED | Code complete and drills green in both systems; the **gym arena run** has not happened. |
| **U4** | PARKED, closure in flight | Column unmet — the harness's own report prints `COMPARED 2/4, exit 1`. Row amended (§2.1 A1) because its premise was falsified. |
| **U5** | PARKED, closure in flight | Doors are hardened and proven; content has a **second home** in `thoughts` and a **third** in an unauthenticated PostgREST projection. |
| **U6** | clause 4 DONE, clauses 1–3 in round 4 | Recall-informed briefs merged and live-proven; andon + gate profiles work, their honesty layer is still being closed. |
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

## U4 — runner unification — **PARKED, closure in flight**

**Row amended 2026-08-30** — see **§2.1 A1**. The row presumed a profile mechanism existed to be
extended across both directions. It did not: `Resolve-RoleTarget` has **zero executable callers**
repo-wide (definition, one test, and a skill doc telling a human to run it), and the runner
`status` field is **read nowhere**. It governed *neither* side. Amending the premise, not the
goal, and not the column.

**Built, and independently reproduced:**
- a real little-coder dispatch over `docker exec` that carried **one** anchored item end to end
  (7 commands, 88 s, outcome pass) — A11 moves off zero at n=1, and `harness.config.json`
  correctly still says `status: unproven`;
- an oracle-on-stall mechanism, 6 mutations all red, its signature function `_failure_sig`
  verbatim from agent-org;
- a quadrant harness that **refuses to report a cell it did not run**;
- the agent-org direction of the runner registry — changing one word in the shared config flips
  a live dispatch to `UnprovisionedHarness`.

**Validated by (§2):** *"same anchored item run per quadrant (runner × target), outcomes
compared; stall→oracle observed firing at least once."*
**Why it is parked — from the deliverable's own machine output:**
`python -m quadrant.cli report` → **`COMPARED 2/4, INCOMPLETE, exit 1`**. Only the target axis
ran; the runner axis has zero coverage. The oracle fired on a **constructed** stall, not an
observed one.
**What would close it:** the two little-coder cells actually run (a dispatch now exists, which is
why closure is being attempted rather than assumed), and a stall that *happened*.
**Known-open on the branches:** the harness direction of "governs both" is a declaration with
zero executable consumers; one false sentence remains in `check-runner-endpoints.ps1` claiming
`.Port` throws on a relative Uri — it does not, it returns `$null` with `$Error.Count = 0`
(orchestrator-verified). Branches `work/dfu-u4`, `work/u4quad`, `work/u4oracle`, `work/u4bidir`
are unmerged.
**Verified by:** orchestrator (the dead-code groundwork, the `.Port` claim, the container's
published ports), verifier (everything else).

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

**STANDING CONSTRAINT: do not write a personal-exposure memory until (1) is closed.** It is
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
`python -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q`
**Evidence:** `3bdf7a8`. Two verifiers not-refuted; they counted **4 and 5** live seams (one
found an `_open_handoff` seam beyond the four the plan names).
**Orchestrator-verified:** `agent_memory_recall_traces` went **0 → 8 rows** — recall has run
against a real Open Brain, not a fake transport. Personal rows still 0. Gitlink `adb7345`
confirmed reachable on the OB1 remote before merging.
**Disclosed, not hidden:** `AGENT_MEMORY_RECALL_RECENCY_WEIGHT` defaults to 0, so the phase-2
re-rank is order-preserving — two-phase overfetch is proven in tests and a **no-op in
production** until that tuning is set. Threshold calibration remains blocked on corpus size (4).

### Clauses 1–3 — andon config, `dark`/`attended` profiles, auto-pass audit records
**Confirmed working by two verifiers, in their own fixtures:** all **5** andon conditions fire on
real instances and stay quiet on clean ones; the halt works end-to-end at the real gate (exit 6,
item parked, condition named in a `decision=refused` ledger record); `DISABLED` is
distinguishable from `EVALUATED-OK` across four byte-distinct board states; a **thinned** board
(entries deleted, disabled, or renamed) refuses and names the missing ids; the negative control
still auto-passes at exit 0 signed `auto:dark` with `-VerifyAudit COMPLETE`.
**Still open (round 4):** `on_fire` set to anything but `"halt"` makes a condition **fire while
the board reports `clear`**, with the fire absent from the ledger entirely — `gate-audit.ps1`
derives `fired` from `action -eq 'halt'`. And README/MODULE claim no config route opens the
gates, which is false: a predicate swap, a `params.repo` redirect and an id-squatting rename
also pass, because the completeness check compares **ids only**.
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

The rule adopted: **enumerate-and-patch loses.** Enforce at a chokepoint that cannot be bypassed
by omission, and derive the completeness test from a **scan of the code** — then prove it has
teeth by adding an unguarded site yourself.

Two incidents and three orchestrator errors are recorded in `DECISIONS.md` under 2026-08-30,
including one hypothesis I later **retracted** after re-testing it in the right shell. They are
in the log because a trail that only records successes is not an audit trail.
