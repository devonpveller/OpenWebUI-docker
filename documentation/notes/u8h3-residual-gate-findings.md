# H3 residuals — filed under §C.10, not fixed

**2026-08-31.** `work/u8h3` merged at `d7aa2eb` (OB1 gitlink `b604d55`) because H3's *Validated
by* passed from a verified-complete clean clone: `prove-agent-memory-rls.ps1`, **68 checks, exit
0**, every green with a red beside it, including *"an absent or malformed exposure is refused BY
THE DATABASE"* and the RED showing the pre-boundary schema accepts the same write.

Everything below is a **residual**: a check that could be less vacuous, a sentence that could be
tighter, or a sibling of an already-recorded class. §C.10: *"a residual finding does not hold the
branch: it becomes a dated follow-up line."* None of these makes H3's *Validated by* wrong —
**the database is the enforcement; the gate is authoring-time convenience.** A green from the
gate was never evidence that a row carries a plane.

## The gate (`scripts/checks/check-corpus-exposure-producers.ps1`)

1. **The same case-fold assumption survives one layer down, and its comment claims otherwise.**
   Round 6 fixed the evidence test (`-match` → `-cmatch`, so `Exposure:` is now correctly RED).
   But `:536` pre-filters with `$text.IndexOf('thoughts')`, which is **case-sensitive**, while
   `$siteUrl`/`$siteArg`/`$siteOrm` are applied with `-match`, which is **case-insensitive**. The
   comment at `:530-535` asserts an exactness the code does not have. Fixed one, left the
   sibling — the effort's most repeated class, appearing one final time in the file that exists
   to catch it.

2. **The category statement is not agreed to be fully general.** Verifier 1 judged it general;
   verifier 2 did not. It now reads: *"ANY occurrence of the key that is not THIS STATEMENT'S OWN
   PLANE DECLARATION can clear it… THE LIST IS ILLUSTRATIVE, NOT EXHAUSTIVE."* Five of six known
   near-misses stay open **and declared**: type annotation, sibling object, string literal, SQL
   text, block-comment continuation.

3. **One verifier still found completeness promised somewhere** (`completeness_promised_anywhere`
   split true/false). The named surfaces were rewritten — the gate header, `.githooks/pre-commit`'s
   justification, `195-` §7, `PROMOTION-RUNBOOK.md`, the u5 note — but the sweep is not certified
   exhaustive by both.

4. **The fence is fixed only for ORM victims.** A donor `thoughts` POST stating `exposure:"ops"`
   **above** an unlabelled `agent_memories` POST still clears it at 0/3/10/25 lines for URL and
   ARG victims; RED at 40 (the window edge). Declared in `$SHAPES_BLIND`, scoped to the measured
   separations rather than claimed universal.

5. **Two blind spots are not closable by pattern-matching at all** — a table name held in a
   variable, and one built from parts. Correctly left: the `NOT NULL` + `CHECK` refuses those rows.

## The drill (`scripts/checks/drill-personal-plane-exclusion.ps1`)

6. **`RED-COVERAGE`** — 7 of 15 ATTACK sections have greens with no red that RAN. For those,
   deleting the mechanism would look like it working. Counted and reported, not closed.

7. **`VACUOUS-WIKIPAGES`** — the drill's compile writes zero `wiki_pages` rows, so ATTACK 14's
   table-side assertion measures an empty set. **The cause is not in this branch**: it is the live
   `extractLinks` scope bug, parked under the §C.10 scope freeze — see
   [wiki-pages-extractlinks-outage-2026-08-31.md](wiki-pages-extractlinks-outage-2026-08-31.md).
   This vacuity is what FOUND that outage, by refusing to go green.

8. **Five audit vacuities** each state what a green does not rule out. They close with H1's
   `SECURITY DEFINER` existence probe, and — because round 5 routed all 25 dispositioned ids
   through `Resolve-Gap` — they will then report **CLOSED**, not fail the build.

## A deploy happened as a consequence of this merge — stated plainly

Completing the merge required `git submodule update` to bring `OB1/` to the merged gitlink.
`OB1/recipes` is **bind-mounted into the live scheduled services**, so this deployed the producer
fix. It was checked first and it **repairs** rather than risks: production's `exposure` column
exists on both tables as `text nullable=YES default=none`, so a producer naming the column cannot
`400`, and the mirror it also writes is what the currently-deployed policy reads — which ends
`openbrain-gmail-pull`'s daily `42501`. Reversible with `git -C OB1 checkout 4fdc21c`.
The migration itself is still **not** applied to production; that remains the gated promotion in
`PROMOTION-RUNBOOK.md`.
