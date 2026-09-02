# The two C.8 record gaps closed, and the four things left standing

Written 2026-09-02 by the section C.10 round-3 item that owns
`documentation/implementation-guide/dark-factory-unification/PLAN.md` section 2.1 and
`DECISIONS.md`. The item's own two probes are green; everything below is what the run turned up
that the item did NOT fix, recorded here rather than folded into the deliverable.

Measured from a clean clone at `738c8bf` (source: the main checkout; `git -c core.longpaths=true
clone --branch work/dfur3g --single-branch --recurse-submodules`, `core.longpaths` set inside the
clone, `git status --porcelain` asserted EMPTY before the run).

## What the item closed

- `amendment-A2-accounted` exit 1 -> **exit 0**. The parser does not require a `**Evidence:**`
  heading at all - `Test-Clause2` tests for a CITED, CHECKABLE ARTIFACT anywhere in the `#### A<n>`
  block (a file path, a 7-40 char sha, or a `file.ext:line`), and says so in its own comment,
  which names A2 as the reason the heading test was rejected. A2 argued from a measurement and
  cited nothing checkable. The Evidence line added names artifacts that already existed.
- `no-unmerged-work-branches` exit 1 -> **exit 0**. `Get-BranchExclusionGrant` needs BOTH a
  `## ... clause 4 exclusion` heading AND an `Excluded from C.8 clause 4:` directive naming the
  branch exactly; the 2026-08-31 operator ruling had neither, so the probe refused it as a
  mention. A grant in the readable form was added; the branch is still unmerged and the entry
  says so.

## Left standing, and why the two clauses are still UNMET

**1. Clause 2's other red: one PARKED entry is outstanding.**
`no-outstanding-parked` exit 1 - `2026-08-30 - U3 - CORRECTION - code-complete,
VALIDATION-PARKED`. This is not a record gap. The park is CORRECT and was re-affirmed on
2026-09-01: the phase's discharge (`scripts/agent-harness/u3_evidence_regression_gym.py`) refuses
for want of evidence, exit 2, because no outcome record exists from the `gym` venue. Un-parking
it is a runner-level job in the arena, not a documentation edit, and writing an `Un-parks:`
directive without that run would be the exact forbidden move.

**2. Clause 4's other red: `service-rls-boundary` exit 1.**
`1 of 13 stage table(s) are not relrowsecurity/relforcerowsecurity = t/t: wiki_pages/f/f`. Every
other stage table is `t/t`. This is a live-database gap, not a record gap; the item that owns it
is whoever owns the boundary migration. Recorded, not touched - this round changes no production
state.

**3. A2's revert path is now factually stale, and was deliberately left alone.**
It reads: *"the branch work is unmerged, so reverting costs only the policy migration. Nothing in
production changes until that migration is applied, and `agent_memories` holds 0 personal rows
throughout."* Both halves have since moved: the migration WAS applied to the live database on
2026-08-31 (its own ledger entry, with a rolled-back canary as proof), and the personal plane is
no longer empty. The sentence is a statement of cost made at amendment time, the probe reads it
only for the label, and editing it in a freeze round - in the same file as an operator-resolved
incident - is a bigger move than it looks. The Evidence line added directly above it now says
the migration was applied, so a reader meets both facts together. **Whoever re-opens section 2.1
should decide whether to date-stamp that sentence as superseded.**

**4. A method trap I walked into myself, inside the authority's own containment rule.**
The first run wrote its transcript to `run24.txt` INSIDE the clean clone. Clause 4's `clean-repo`
probe promptly went red - `1 dirty path(s): ?? run24.txt` - a red caused entirely by how the run
was captured. The script's rule 8 exists for exactly this shape ("a checker must not measure a
world its subject can change"), and its containment covers the WALKTHROUGH commands it executes,
not the operator's own shell. Cheap rule: **redirect a dfu-done transcript outside the tree it is
auditing**, and re-assert `git status --porcelain` empty after the run as well as before. The
re-run with the log at `D:\dfg3-run24.txt` was clean before and after.

## One convention note

Section C.7b's literal clean-clone command is a bare `--single-branch` clone; `CLAUDE.md`'s is
`--recurse-submodules`. They disagree, and it matters for these two clauses: clause 4's
`gitlink-reachable-on-remote` reads the OB1 remote URL out of the submodule working tree, so a
bare clone would leave it unable to ask the remote and the probe would go INDETERMINATE rather
than pass. This run used `--recurse-submodules` and the probe passed. The 2026-09-02 ledger entry
on the agent-memory smoke already filed the same complaint from the other direction. The two
documents should say one thing.
