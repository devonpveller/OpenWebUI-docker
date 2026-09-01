# U8 hardening - MERGE ORDER, and why the floor-pin lands LAST

**Operator direction, 2026-08-31. Read this before merging `work/u8floor`.**

## The rule

> **Do NOT merge `work/u8floor` until H1, H2 and H3 exist.**

Order:

| # | item | branch | state (2026-08-31) |
|---|------|--------|--------------------|
| 1 | U4 - runner unification | `work/u4close` | **MERGED**, park lifted |
| 2 | H3 - typed exposure column | `work/u8h3` | in flight, round 4 |
| 3 | H2 - boot-time RLS assertion | not started | blocked on H3's schema |
| 4 | H1 - no superuser app connections | not started | blocked on H3's schema |
| 5 | **H4/H5 floor-pin** | `work/u8floor` | **HOLD - merge LAST** |

## Why

`work/u8floor` extends `dfu-done.ps1`'s pinned phase floor to `@("U0".."U6","U8")` and clause
1's population filter to `'^U(?:[0-6]|8)$'`. That is correct and required by §2's U8 row. But
the floor is what the authority *measures against*: pinning U8 before H1-H3 exist makes the
done-script permanently red on subjects nothing can yet satisfy -

```
[fail] phase-floor-present   1 floor phase(s) are NOT present in WALKTHROUGH.md's
                             phase sections: U8
[indeterminate] U8-validated-by            no executable check is recorded for this phase
[indeterminate] walkthrough-U8-names-a-check
[fail] audit-trail-U8
```

A permanently-red authority is one nobody reads, and an authority nobody reads is how §C.8's
forbidden move gets made by accident later.

**This is sequencing, not a redefinition.** The floor is not weakened, not made optional, and
not deferred past U8. What is sequenced is *when a true statement becomes a measured one*.
§C.8 forbids amending a column so the script goes green; it says nothing against landing a
red-making change after the thing it measures exists.

## Two things that travel with this branch and must not be lost

1. **§C.8 clause 1's prose still reads "For U0-U6"** while §C.9 and §2's U8 row both require U8
   in clause 1's population. `work/u8floor` deliberately leaves `phase-floor-matches-plan` RED
   on four clauses rather than relaxing the check to "pinned ⊇ named" - which would clear the
   red *and* re-open the hole the check exists to close (a plan that stopped naming U5 would
   then pass). **Closing it is a PLAN.md edit and it is the operator's.**

2. **H4 is blocked on an operator promotion, not on code.** `origin/development`'s `ci.yml` is
   blob `e9ff281` and still reads `branches: [main, develop, …]`; `develop` has never existed.
   GitHub resolves a push workflow from the ref being *pushed*, so fixing that line on a
   feature branch changes the behaviour of **zero** pushes. `refactor/**` already matched the
   work line before the fix. §C.8 clause 4 puts `development` out of scope and §C.9 H5 pushes
   only the work line, so H4's "clauses shown green on a CI run" cannot be met until the
   corrected line is promoted to `development`. Recorded as a dependency, not a step.

## Operational note for whoever runs the authority

`dfu-done.ps1` defaults to `$DbContainer = "openbrain-db"` - **the live database** - and its
clause 3 plants personal-exposure fixture rows there. Run it with `-SkipLive`, or acquire
`scripts/agent-harness/lease.ps1 -Acquire -Name open-brain` first. An uncoordinated run on
2026-08-31 at 19:53 collided with a verifier mid-read and left another check reporting
"production is not clean". A crash mid-run would strand personal-exposure rows in production.

Related: `Get-AuditedFingerprint` includes repo-wide `git for-each-ref` and `git worktree list`
run in the shared root, so a *concurrent session* in a sibling worktree makes the authority
report `INTEGRITY: FAILED / board: UNACCOUNTED` and blame whichever command happened to be
running - an innocent one. Two of four runs did this. Under the worktree-per-session policy
that recurs structurally.
