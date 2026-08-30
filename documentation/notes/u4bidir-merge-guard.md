# Merge guard for work/u4bidir — one refutation I relayed without adjudicating

Recorded 2026-08-30, while the fix round is still running, so the record shows the error was
caught rather than quietly corrected.

## What happened

`work/u4bidir` was refuted 2/2. I verified the two most consequential findings myself by
reading the branch's source — both confirmed, both genuine:

1. `check-runner-endpoints.ps1` cannot fail for container-DNS rows. `$claimsHost` is false
   for those rows, so a FAILED host probe lands in the `else` branch that leaves `$status`
   as `"ok"`; and a nonexistent container makes `Get-ContainerNetworks` return `$null`,
   setting `netStatus = "skip"`, which never increments `$failed`. A wrong port on a
   nonexistent container passes with exit 0, under a header claiming "Exit 0 = every
   declaration matched reality".
2. `RunnerRegistry.load` does `pool = _pool_from_urls(fallback_urls); if not pool: pool =
   _pool_from_specs(specs)`. With compose's documented default `${AO_WORKER_INSTANCE_URLS:-}`
   ("Empty in P0-P4"), the pool goes from empty pre-U4 to two workers post-U4, and the
   documented disable path silently re-enables it.

I then passed the remaining three refutations through to the fixer **as directives**, without
checking them the way I checked the first two. One of them is wrong.

## The one that is wrong

> "PLAN §2's U4 row still asserts the sentence the findings note calls FALSE."

§2's U4 row is the **task statement** — what the phase is *to do* — not an assertion about
what is currently true. "One profile mechanism governs both" is the goal. A findings note
concluding it is false *today* is not in conflict with it; that is the phase being
incomplete, which is what a phase is before it is done.

Under §C.1 the plan is the confirmed anchor for U0–U7. Editing a phase's description so it
matches what was actually delivered is moving the target to hit it — the precise failure
the anchor exists to prevent, and worse than the over-claim it would be covering.

## The guard, enforced at merge

**If work/u4bidir's diff touches `documentation/implementation-guide/dark-factory-unification/PLAN.md`
§2's U4 row, that hunk does not merge.** The phase description stays as written. If U4
genuinely cannot deliver one direction, the honest record is a park with a reason in
DECISIONS.md plus an unchanged plan row — not a rewritten goal.

Amending the plan is possible, but it is `-AmendAnchor` semantics per §B: a stated reason, a
history entry, at a cost, and it is the operator's call — not a side effect of a fix round.

## The process finding, which is the more useful half

This is A9 (verify before you relay) turned on the orchestrator, and I half-failed it: I
adjudicated the two findings I was most suspicious of and rubber-stamped the other three
because they came in the same list. **A verified finding sitting next to an unverified one
lends it credibility it did not earn.** The refuters were briefed to refute; a briefing that
strong reliably produces some findings that are merely plausible, and the orchestrator is the
only filter between those and a builder's next cycle.

Rule adopted: refutations are adjudicated **individually** before being relayed as
directives. Ones I have not checked get handed over labelled as unverified claims for the
builder to assess — not as instructions to comply with.

## DECISIONS entries to append

- **2026-08-30, U4 clause 3:** `work/u4bidir` refuted 2/2; two defects confirmed by the
  orchestrator reading the source (a reachability check that cannot fail for the rows it
  validates; a registry fallback that turns compose's documented empty-env default from "no
  pool" into "two workers", making the documented disable path re-enable it). Returned to its
  builder with a fix round re-verified by agents that did not fix it.
- **2026-08-30, process:** the orchestrator relayed three unadjudicated refutations as
  directives alongside two it had verified, one of which — "PLAN §2's U4 row asserts a false
  sentence" — mistakes a task statement for a claim of completion. Acting on it would have
  edited the anchor to match the delivery. Guard: the U4 row does not merge if modified
  (`documentation/notes/u4bidir-merge-guard.md`). Rule: refutations are adjudicated
  individually; unverified ones are relayed as claims to assess, never as instructions.
  Revert path: this is a note plus a merge-time refusal — nothing to revert.
