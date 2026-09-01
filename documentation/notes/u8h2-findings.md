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
