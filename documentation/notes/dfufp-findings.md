# dfufp findings — U3, U6 and U7: honest runnable checks (2026-09-02)

Findings sink for the item that gave U3, U6 and U7 real checks or an honest report where a
check could not exist. Everything here was measured on `work/dfufp` (based on
`refactor/ai-stack-cleanup` at `375b2e7`), with the command and the exit code it returned.

---

## U3 — the gym drill could not find its own evidence, and the counterfactual measured nothing

**Finding 1 — the evidence died the same way U4's did.**
`scripts/agent-harness/u3_evidence_regression_gym.py` seeds copies of REAL gym run evidence and
looked for it in one place: `.quadrant/gym-runs`, which `.gitignore` covers as *"run artifacts
(evidence for a run, not source)"*. Reproduced before changing anything, from the main checkout:

    $ python scripts/agent-harness/u3_evidence_regression_gym.py
    venue      : gym (gym) - D:\Open WebUI\ai-orchestration-gym @ main
    NO EVIDENCE: D:\Open WebUI\ai-stack\.quadrant\gym-runs holds no outcome record from
                 venue 'gym'.                                                        exit 2

The venue RESOLVED and was READY — the arena is present and reachable. What was missing was the
evidence, deleted with the worktree that produced it. That is byte for byte the loss
`documentation/evidence/README.md` was created in response to, in the drill that first earned
the rule that evidence resolves *beside the record*.

**Fixed** by giving the drill the same `SOURCE_ROOTS` treatment
`check_quadrant_evidence_reproduces.py`'s `DISCOVERY_ROOTS` already had: `.quadrant/gym-runs`
(working) **and** `documentation/evidence/` (committed). The venue filter
(`record.venue.name == <configured venue>`) is untouched, so widening *where* it looks cannot
widen *what* it seeds from.

**Finding 2 — and this one would have shipped a vacuous green.** `admit_against` called
`record.admit` **without** `record_dir`. Admission then resolves each record's absolute
`evidence.workspace` — a path inside the worktree that produced the record, which no longer
exists. Against the newly-reachable committed records that means **every** record is REFUSED,
**every** seed reads "caught by the pre-existing gate", and the drill exits **0** reporting
`0 of 3 seeds are caught ONLY by the banked check` — a pass whose own headline says it measured
nothing. Fixed by passing `record_dir`, the same correction `record._evidence_present` and
`check_quadrant_evidence_reproduces.py` already carry, for the same stated reason.

**Result:** exit 0, 3 seeds, 2 caught only by the banked check, 1 by `record.admit`, 0 by
neither. Red-proofed (banked check neutered → exit 1, seeds A and B `MISSED BY EVERY GATE`;
restored → exit 0). Transcripts: `documentation/evidence/dfu-u3/gym-20260902T093709Z/`.

**Still open — and it is not U3's work.** A `Gym:` column names a PLACE, and
`quadrant/venue.py` resolves the arena as `../ai-orchestration-gym` relative to the checkout.
`dfu-done.ps1` clones into `%TEMP%`, so the drill is `VENUE REFUSED`, exit 2, from any
disposable clone. **Loosening that gate was considered and refused**: it is available (every
record carries `venue.identity`, the arena's root-commit sha, so provenance could be read off
the evidence) and it is a redefinition of the column's first word, made by the party who would
benefit from the green. U3 stays PARKED and carries no marker.

---

## U6 — "one config key wide" was an assertion; it is now a measurement, and it was incomplete

**Finding 3 — the claim was true of the drill and FALSE of the live board.**
`WALKTHROUGH.md` recorded that `drill-dark-factory.ps1`'s red traces to `pipeline.convergence`.
Measured both halves:

| what | command | result |
|---|---|---|
| the drill, shipped config | `drill-dark-factory.ps1` | exit 1 — 146 passed, **67 failed** |
| the drill, `pipeline.convergence` deleted (temporary, reverted) | same | exit 0 — **213 passed, 0 failed** |
| the LIVE board, shipped config | `andon.ps1 -Evaluate` | exit 6 — **four** conditions raised |

So the drill's red really is one key wide, and its step A2 (*"the shipped pipeline block is
fully read"*) is a defect detector doing its job. But the **live board** is raised on four:
`policy-declared-unread` (`pipeline.convergence`), `git-error-swallowed` (**27 call sites**,
including `dfu-done.ps1:1614`), `work-branch-on-remote` (`work/pod-key` — clause 4 carves that
branch out, the andon board does not), and `protected-ref-moved` (**indeterminate**, no
baseline). The fixtures do not see the middle two because their globs and remotes are the
fixture's. **A `dark` run could not auto-pass a gate in this checkout even with
`pipeline.convergence` resolved.** That correction matters: the previous wording implied one
decision would restore unattended operation, and it would not.

**Finding 4 — a check that added a finding to the board it was measuring.** The first version of
`scripts/checks/drill-u6-dark-gate.ps1` swallowed git exit codes in its own `Invoke-GitAt`, and
its own step-M measurement of the shipped board came back naming it:
`drill-u6-dark-gate.ps1:109 in Invoke-GitAt() runs git and does not check the result within 5
line(s)`. Fixed by checking `$LASTEXITCODE` at the call site and throwing — which is right on
its own merits, since every git call there is setup that must not fail silently.

**Finding 5 - the drill caught its own fixture testing two things at once.** The column's word
is **each**, so the drill builds one fixture per required condition and asserts the ledger
refusal names *that condition and no other*. That assertion immediately caught the drill's own
`git-error-swallowed` fixture: it committed its bait file, which moved `main` after the andon
baseline and fired `protected-ref-moved` as well - `fired=git-error-swallowed;
protected-ref-moved`. The gate still halted, so the weaker assertion (*"the refusal names this
condition"*) would have passed over it. Fixed by writing the bait file without committing it.
The general shape is worth more than the fix: **a halt assertion that does not pin WHICH
condition halted cannot tell a working detector from a noisy board.**

**And the evidence for that finding was itself wrong for twenty minutes, which is worth
recording.** The failing transcript was captured to a scratch file that a later green run
overwrote, so the committed file *named* for the two-condition failure held a **passing**
transcript — a name and a content that disagree, in an evidence directory. It was not
described from memory: the fixture was put back to committing its bait file, the drill re-run
(**exit 1, 1 of 54 failed**, `fired=git-error-swallowed; protected-ref-moved`) and the drill
restored with `git checkout`. `documentation/evidence/dfu-u6/dark-gate-20260902/`'s
`outcome.json` says the transcript is a re-measurement rather than the original capture.

**A correction to an earlier draft of this note.** The first version of this drill quoted the
column as *"an unattended run that hits **an** andon condition"*. The column says **each**. The
misquote was in the check's own header and in the walkthrough, and it would have made a
one-condition drill look like full coverage of the column's first half. The check was widened to
the column rather than the column narrowed to the check.

**Not closed, and it is an operator decision:** what `pipeline.convergence` should be. Its own
`_status` says wiring the reader is *"a pipeline item, not a config edit"*. Until it is
resolved, `drill-dark-factory.ps1` cannot be this phase's marker without putting a ~10-minute
red into clauses 1 and 5. The 27 `git-error-swallowed` call sites and the missing andon baseline
are separately open and are nobody's item today.

---

## U7 — the walkthrough and the done-authority disagreed about whether the loop had run

**Finding 6.** `WALKTHROUGH.md` said U7 was **NOT STARTED** and that *"a loop that has never run
is an intention"*, while `dfu-done.ps1` clause 6 **arms** U7 on §2.1 **A2** and the ledger entry
`2026-08-31 · U7 · A2 IS a complete cycle by clause 6's enumeration` says so explicitly. Both
could not stand. Resolved in favour of the ledger and the checker: the loop has run one cycle,
§C.8 clause 6's own word for that is **ARMED**, and the walkthrough now uses it. No evidence was
added to reach that state — the status line was simply wrong about evidence that existed.

Verified by reading, 2026-09-02: A2 carries `§1.1` **×4**, `AVO` **×1** and a `Revert path:` —
the citation the ledger entry rests on is present in the text it cites, and there is no A6 or
A7 in it, which is the correction that entry already records against its own first draft.

**A runnable verification of that chain was refused, not overlooked.** It is constructible —
*the entry exists → it cites A2 → A2 carries the citation it claims* is three greps. It would
contradict §2's U7 row as amended by **A4** the same day (*"names NO runnable artifact and must
not be given one"*), and `PLAN.md` is not this item's file; and clause 7 matches a phase's
checks against the artifacts **§2's column names**, so a walkthrough command naming a script the
column does not name would discharge nothing anyway.

**The consequence, stated rather than worked around:** `audit-trail-U7` stays RED in clause 7,
and clause 5 cannot reach `met` while U7 is in its population and the plan forbids it a check.
Closing either is a `PLAN.md` decision.

---

## What a reader should distrust here

- `documentation/evidence/dfu-u3/` and `dfu-u6/` are **transcripts**, not run records a checker
  re-derives. This project's own banked rule applies to them: a record of a check is not a
  check. Each `outcome.json` carries a `not_claimed` list.
- `drill-u6-dark-gate.ps1` was built and run by the same agent. It has **not** been re-run by
  someone who did not build it, which is the weaker statement and is marked as such in the
  walkthrough.
- The `drill-dark-factory.ps1` measurements were taken by deleting a key from
  `harness.config.json` in a worktree and reverting it. `git status --porcelain` was asserted
  clean afterwards; the shipped config is unchanged.
