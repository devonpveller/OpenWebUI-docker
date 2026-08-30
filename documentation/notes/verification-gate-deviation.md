# My merge gate is stricter than §C.7's letter — recorded as a deliberate deviation

Recorded 2026-08-30 by the orchestrator, after noticing it rather than after being caught.

## The discrepancy

§C.7 says:

> Nothing merges unrefuted. Every item is verified by agents that did not build it, prompted
> to REFUTE that it meets its Validated by column rather than to bless it. **Majority-refuted**
> returns the item to its builder.

I run **two** verifiers per item and return the item on **any** refutation:

```js
survives = refuted < Math.ceil(total / 2)   // with total = 2, survives only if refuted == 0
```

With two verifiers, a majority is two. One refutation is a tie, not a majority. So my gate
returns items the plan's letter would merge.

## Why I am keeping it

Empirically it has been right every single time. Across U4, U5 and U6, **every** refutation so
far has been substantively correct and has named a real defect — including several I then
confirmed myself by reading the source:

- `promote_exposure` escalating a personal memory onto the ops plane through a door with no
  exposure predicate;
- the attestation digest being lossy, with the ledger column that would catch it read by
  nothing;
- seam 4 of the recall work being dead on the real path, its test satisfied by a *different*
  seam;
- a reachability check that could not fail for the rows it existed to validate;
- a registry fallback that turned compose's documented empty-env default into two live workers.

Not one refutation in this effort has been a false positive on a *defect*. (One was wrong on a
*process* point — a verifier read §2's U4 row as a completion claim rather than a task
statement — and I caught that separately; see `u4bidir-merge-guard.md`.) Under those base
rates, requiring two independent agents to *both* miss nothing before returning an item would
merge known defects on a 1–1 split.

The asymmetry decides it: §C.7 also says the operator audits afterwards by reading the trail
instead of the diffs. A false return costs one cycle. A false merge lands a defect nobody
reads. Those are not comparable costs, and the strict gate buys the cheap error.

## Why it is written down rather than just done

§C.1 makes the plan the confirmed anchor for U0–U7, and quietly running a different rule than
the anchor states is precisely the drift the anchor exists to prevent — even when the different
rule is *stricter*. Silent strictness is still silent divergence, and the next reader would
have no way to tell a deliberate deviation from a coding error in the workflow script.

This is not an `-AmendAnchor`: I am not changing §C.7, and a future round may reasonably revert
to the literal rule. It is a recorded operating choice, reversible in one line.

## Cost, stated honestly

It is a large part of why nothing has merged yet. Every item has gone back at least once. That
is the intended trade — but if a later round produces refutations that are merely *plausible*
rather than demonstrated, this gate would start burning cycles on noise, and the right response
then is to raise the verifier count to three and apply the literal majority rule, not to relax
to 1-of-2.

**Revert path:** change `refuted < Math.ceil(total / 2)` to `refuted < total` in the workflow
scripts, or raise the panel to three verifiers and keep the majority rule as written.

## DECISIONS entries to append

- **2026-08-30, method:** the orchestrator's merge gate returns an item on ANY refutation from
  a two-agent panel, which is stricter than §C.7's "majority-refuted". Kept deliberately: every
  refutation in this effort has named a real defect, and a false return costs one cycle while a
  false merge lands a defect into a trail the operator reads instead of the diffs. Recorded
  rather than applied silently, because running a different rule than the anchor states is the
  drift §C.1 exists to prevent even when the difference is strictness.
  Revert path: `refuted < total`, or a three-verifier panel with the literal majority rule.
