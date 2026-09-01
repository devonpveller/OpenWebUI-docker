# U8/H2 findings - things that are true about OTHER artifacts

Round 2, 2026-08-31. Work item: the RLS boot assertion (`scripts/checks/assert-rls-force.sh`)
and its drill. These are the things the work turned up that do NOT belong in either file.

---

## 1. PLAN.md C.9 H2's "the nine governed tables" is stale by eight

**Relayed to the operator already; recorded here so it survives the thread.**

Measured on the live `openbrain-db`, 2026-08-31, read-only:

```
SELECT n.nspname, c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relforcerowsecurity;
-- 17 rows, all public, all relkind r
```

Nine come from `init-agent-memory-rls.sql` (the eight `agent_memory_*` tables plus `thoughts`).
Eight more come from `init-graph-plane-rls.sql`, landed a day later: `thought_entities`,
`entity_extraction_queue`, `thought_edges`, `idea_revisions`, `entities`, `edges`,
`source_entities`, `consolidation_log`.

An assertion written to the plan's sentence would pass a database with the entire graph plane
unprotected. **The plan's wording is the hazard, not a tenth table** - it is the eighth through
seventeenth, already shipped. H2's deliverable derives the set instead of quoting the sentence,
so it is not affected; anything else that quotes "nine" is.

## 2. `test-quartz4-offline.ps1` throws away the staged-migration count

`scripts/checks/lib/ob-initdb.ps1`'s `Copy-ObInitChain` **silently skips a chain file it cannot
find** and returns the count it actually staged. That is deliberate - the lib's own header says
"Nothing in this file asserts... the CALLER decides what a missing file means" - so the
obligation is the caller's.

Three callers, and they do not agree:

| caller | line | treats the count as |
|---|---|---|
| `scripts/checks/smoke-agent-memory.ps1` | 63-65 | **asserted** - `if ($staged -ne $chain.Count) { Fail ... }` |
| `scripts/checks/drill-rls-boot-assertion.ps1` | (was) | printed inside an unconditional `Pass` - **fixed in this round** |
| `scripts/checks/test-quartz4-offline.ps1` | 135 | **`$null = Copy-ObInitChain ...`** - discarded entirely |

MEASURED: hiding 5 of `OB1/docker`'s 31 `.sql` files took the staged chain from 28 to 23, with
no error, no throw and no non-zero exit. A short chain builds a database missing tables, and a
suite run against it proves only that nothing failed among the tables that happened to exist.

**Not fixed here** - `test-quartz4-offline.ps1` is outside this item's scope. The one-line fix is
`smoke-agent-memory.ps1`'s: compare `$staged` to `$chain.Count` and fail.

This pairs directly with `clean-clone-maxpath-validation-trap.md`: an incomplete checkout is not
hypothetical on this machine, and `Copy-ObInitChain` is the component that converts it into a
quiet, plausible-looking test result.

## 3. F5 ("the drill's positive control failed in a clean checkout") did NOT reproduce

Recorded honestly, because "it passes for me" is not a diagnosis and neither is "I fixed it".

What was run:

- A clean harness worktree (`new-worktree.ps1 -Id u8h2v`), OB1 submodule initialised at the
  pinned SHA, detached to the **pre-fix** sha (`d9346d8`, the rebase of `60ecd4a`), assertion
  script byte-identical to that sha.
- `drill-rls-boot-assertion.ps1 -SkipComposeGate` -> **33/33, 39.5 s**.
- Full run including the compose gate -> **38/38, 190.5 s**.

So the pre-fix drill passed from a clean checkout at the same sha. Three real
order/environment dependencies were found by asking how F5 *could* be true, and all three are
fixed in this round - but none of them is confirmed to be the one F5 hit:

- **a.** the staged-chain count was printed and never asserted (see §2 - an incomplete checkout
  fails the GREEN sections and passes every RED one, which is the reported shape);
- **b.** a missing chain file made `Get-Content -Raw` return `$null`, and the FORCE regex threw
  "Value cannot be null" from inside a `Where-Object` - an abort naming no file;
- **c.** the container names (`ob-h2-nomig`, `ob-h2-mig`) and the compose project (`ob-h2-gate`)
  were fixed strings, and `Start-ObInitdb` opens with `docker rm -f $Name`. Two drills running
  at once - an author and a verifier on one machine, which is the normal state of this effort -
  **delete each other's databases mid-run**, and the victim's green sections fail against a
  container that is simply gone. Every name is now run-scoped.

**If F5's actual console output still exists, it is worth reading**: it would say which of the
three (or a fourth) it was. Without it this is a list of closed holes, not a closed case.

## 4. The live `openbrain-db` has an `auth` schema, and nothing governs it

`SELECT nspname FROM pg_namespace` on the live database returns `auth` alongside `public`. It
currently holds **no tables** (`relkind IN ('r','p','f')` count = 0, all 51 are in `public`), so
there is nothing unprotected there today.

But every `*-rls.sql` migration declares `public.` explicitly, and the boot assertion now looks
up each declaration **in the schema the declaration names**. So a table created in `auth` later
is outside the exposure boundary and outside the assertion, silently, until a migration declares
it. Worth a decision the next time anything writes to `auth`.

## 5. `.sql.gz` in the initdb chain is executable and was invisible

The postgres entrypoint (`pgvector/pgvector:pg16`, `/usr/local/bin/docker-entrypoint.sh:180-196`)
executes `*.sql`, `*.sql.gz`, `*.sql.xz`, `*.sql.zst` **and `*.sh`** from
`/docker-entrypoint-initdb.d`. Its glob is `/docker-entrypoint-initdb.d/*` (line 363), so it is
**not recursive** - a `.sql` in a subdirectory of the chain hits the `*)` ignore branch and never
runs, which is why the assertion reads the chain at depth 1 and the migration SOURCE recursively.
Nothing in this repo ships a
compressed or shell migration today, so this is a latent hole rather than a live one - but any
scan of "the migrations" that globs `*.sql` is not scanning the chain, it is scanning part of it.
The assertion now reads all four SQL forms; `.sh` is deliberately not parsed (it is arbitrary
shell, and the assertion's own text lives in the same directory), and is covered instead by the
catalogue completeness cross-check.

---

Round 3, 2026-09-01. The round fixed one thing (the drill's initdb wait). Everything below is
filed under C.10: recorded with what was checked and when, not built.

## 6. `assert-rls-force.sh`'s `*revert*` filename exclusion can hide a real declaration

2026-09-01. `scripts/checks/assert-rls-force.sh:391` scans migrations with
`find "$_d" $_depth -type f ! -name '*revert*' -name '*.sql'`. A migration whose FILENAME
contains "revert" is therefore never parsed - so a file named, say, `90-revert-to-v2.sql` that
declares `ALTER TABLE public.x FORCE ROW LEVEL SECURITY` on a table that is genuinely NOT forced
leaves `x` out of the derived set, and the assertion reports `OK - N governed tables`, exit 0.
The orchestrator reproduced this as `OK ... 1 governed tables`, exit 0.

This contradicts the script's own `L2` at `:103-107`, which claims the rule is "load-bearing in a
safe direction" because renaming a revert file makes the set *ambiguous* and fails loudly. That
holds for the case L2 was written about - a revert file carrying `NO FORCE` for tables the init
migrations FORCE - and not for this one: a revert-named file carrying a FORCE for a table nothing
else declares is simply dropped, and the completeness cross-check cannot see it either (that
backstop catches a relation the catalogue FORCEs and no migration declares; here the table is
neither forced nor declared, so there is nothing for it to compare).

It is a sibling of the round-1 silent-narrowing class, which is why it is here and not in code.

**It does not affect the shipped set - verified 2026-09-01, independently of the relay.** All
seventeen governed tables are declared in `OB1/docker/init-agent-memory-rls.sql` (nine) and
`OB1/docker/init-graph-plane-rls.sql` (eight); `ls` over `OB1/docker` shows the only
revert-matching files are `revert-agent-memory-rls.sql`, `revert-graph-plane-rls.sql` and
`revert-agent-memory-corpus-failclosed.sql`, and `grep -li` over the whole directory shows those
two init files and those reverts are the ONLY files declaring FORCE at all. So no shipped
declaration is being dropped: the hole is latent, not live. It goes live the day someone names a
forward migration with "revert" in it.

The shape of a fix, when it is in scope: exclude by CONTENT (a file whose FORCE declarations are
all `NO FORCE`) rather than by name, or require reverts to live in a `reverts/` directory the
entrypoint does not mount.

## 7. The `180s` initdb timeout was NOT a slow machine - measured, not assumed

2026-09-01. The blocker this round fixed reported `initdb did not complete in 180s` twice on the
operator's machine. The obvious reading - the machine was too busy for the budget - is **not
what the numbers say.**

Measured on that machine, in its normal loaded state (81-84 containers of the live stack running
throughout), against the same 28-file chain the drill uses:

| case | n | times (s) | mean |
|---|---|---|---|
| sequential | 4 | 8.0 / 5.5 / 5.6 / 5.7 | 6.2 |
| eight chains started AT ONCE | 8 | 11.5 / 11.8 / 12.0 / 12.2 / 12.5 / 12.7 / 13.8 / 14.0 | 12.6 |

The old 180s budget was already ~13x the worst contended measurement. A run that exhausts it is
not merely busy - something else went wrong (the daemon refused the `docker run`, the container
died, the name was taken) and the old code reported ALL of those as a timeout, because it started
the container with output discarded and then polled a name for 180 seconds without ever asking
whether that name still existed.

The fix is therefore weighted to CLASSIFICATION, not to a bigger number: `start-failed` (reported
in the same second, with the daemon's own message), `exited` (with the container's exit code and
log tail), `container-gone`, `timeout` (with elapsed and log tail) - and the drill exits **3**,
not 1, for all of them. The ceiling was still raised (600s, ~43x the contended worst) because it
is a ceiling on a signal poll, not a sleep, and costs nothing on a healthy run.

**The original failure was never reproduced here** - the drill passes on this machine at this
sha, both before and after. What changed is that the next occurrence will name itself instead of
saying "180s".

## 8. Residuals of round 3, deliberately not built

2026-09-01, all C.10 filings.

- **The four other callers of `Start-ObInitdb` still judge a boolean.**
  `drill-personal-plane-exclusion.ps1:1087`, `prove-agent-memory-rls.ps1:191`,
  `smoke-agent-memory.ps1:66` and `test-quartz4-offline.ps1:138` inherit the fail-fast and the
  measured ceiling (the boolean wrapper delegates to the classifying function), but they cannot
  distinguish cannot-check from failure, because they only look at true/false. Their call sites
  were deliberately left untouched this round.
- **Section 0's aborts still exit 1.** "this checkout is INCOMPLETE", "incomplete staging" and
  "OB1 compose missing" are cannot-check conditions by the same argument as the initdb wait -
  they call `Fail` and then `throw`, so a clean-clone problem still exits 1 rather than 3. Their
  MESSAGES already say "proves nothing", so the reader is not misled; the exit code is.
- **Section 9's GREEN failure is still classified FAIL.** If the compose db never comes up in the
  green case, the drill says "dependent never started against a correctly migrated db" - a
  sentence about the boundary - rather than blocking. Its budgets are now derived and its elapsed
  time is printed, so the evidence to tell them apart is in the output; the classification is not.
