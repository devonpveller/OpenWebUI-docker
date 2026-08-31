# dfudone — the C.8 done-authority, what it says, and what it found

Item `dfudone`, 2026-08-31, worktree `wt-dfudone`, branch `work/dfudone`.
Plane lease held: `open-brain` (owner `dfudone`) for the clause-3 fixture and the migration.

**Built:**
- `scripts/checks/dfu-done.ps1` — the authority for PLAN.md §C.8. Eight clauses, each
  decided by running something, verdict by exhaustive census (the `andon.ps1` shape, reused
  rather than reinvented).
- `scripts/checks/verify-dfu-done.ps1` — the drill. **41 assertions, 0 failed, 8 of 8
  clauses have a constructed failing case.**
- `OB1/docker/init-agent-memory-corpus-failclosed.sql` + its revert, registered at slot
  `190-` in both compose files, documented in the promotion runbook, **applied to the live
  database**.

---

## THE VERDICT: `dfu-done.ps1` exits **7 — NOT DONE**

Census balances 8/8. **6 unmet, 2 unevaluated, 0 met.** Per §C.8 this is a REPORT, not a
redefinition — no plan column was touched.

| Clause | Verdict | Coverage | Why |
|---|---|---|---|
| 1 clean-checkout re-run | UNEVALUATED | 2 of 7 phases | Only U2 and U6 name an executable check; both **re-ran green from a clean checkout**. U0/U1/U3/U4/U5 name none — their columns are prose, which cannot re-run. |
| 2 parked + amendment chain | UNMET | 8 of 8 chains | U3's `VALIDATION-PARKED` entry is never closed by a later ledger heading. Chains all reconstructable; every ORIGINAL requirement survives into CURRENT. |
| 3 personal plane by validation | UNMET | 8 of 8 doors | Two doors returned the synthetic personal row (below). Predicate half now **closed**. |
| 4 nothing in flight, all running | UNMET | 9 of 9 | 7 unmerged `work/*`, 15 worktrees, 2 untracked files, and the RLS boundary runs from code that has not landed. |
| 5 the walkthrough is true | UNEVALUATED | 2 of 8 rows | Six of eight phase sections name **no check at all**. |
| 6 U7 armed | UNMET | 1 of 1 | No ledger entry records a U7 cycle reaching adopted-or-refused. The loop has never run. |
| 7 audit trail complete | UNMET | 8 of 8 phases | U0 has **no DECISIONS.md entry**. U1–U7 all have entries and notes. |
| 8 the plane compounds | UNMET | 3 of 3 | 4 memories, 8 traces, 4 of which returned something — and **not one recall id is cited anywhere** in the ledger or the notes. |

### What would close each, and what it costs

- **1 and 5** — the same gap. Give U0, U1, U3, U4, U5, U7 a `**How to run:**` line naming a
  command that exits 0. Cheap where a check exists (U0's kill-the-poller drill, U1's
  memory-plane gates); U3/U4 need the arena runs their columns actually demand.
- **2** — one closing ledger entry for U3 (the walkthrough already calls it DISCHARGED; the
  ledger does not). Minutes.
- **3** — see below. Two real defects remain, one of them scoped by the orchestrator as its
  own item (Step 2/3 of the RLS work).
- **4** — merge or retire 7 branches, remove 15 worktrees, land `work/u5rls`. Not mine.
- **6** — run one U7 cycle and record it. This is a real piece of work, not paperwork.
- **7** — one U0 entry.
- **8** — cannot be closed by writing anything. It needs a real effort to recall, be
  informed, and **cite what it was told**. §C.8 says this clause may fail; it does.

---

## CLAUSE 3 — one half CLOSED, two defects REPRODUCED

### Closed: the corpus predicate is now fail-closed

`ob_corpus_on_ops_plane` was `md->>'exposure' IS NULL OR = 'ops'` — so an **unlabelled**
thought was visible to the agent plane. Measured before: **12,989 of 12,993 thoughts carried
no label at all**, so nearly the whole corpus was ops-plane by default rather than by
decision. "Unlabelled defaults to fine", in SQL.

Migration order is label-first, then close — the reverse would hide 12,989 rows mid-transaction:

```
BEFORE  agent-plane sees 12993     unlabelled 12989 | ops 4 | personal 0
AFTER   agent-plane sees 12993     unlabelled 0 | ops 12993 | personal 0 | backfill-stamped 12989
PROOF   insert an unlabelled row in a rolled-back tx -> agent plane sees 0
```

**12,993 before and after — no row changed visibility**, which is exactly why check 3 is the
necessary one: with the corpus fully labelled, the first two checks look identical whether
the predicate was flipped or not. Additive and reversible: two JSONB keys added, every
touched row stamped `exposure_backfill='dfu-c8-corpus-failclosed'` so the revert strips
exactly those and leaves the 4 deliberately-labelled `ops` rows alone. Nothing dropped.

### Reproduced #1 — a hash is a disclosure

A synthetic `personal` thought was written, then every door attacked. `thoughts`,
`agent_memories`, the `thought_entities` join, `wiki_pages` and the cloud search lane all
correctly returned nothing. But:

```
GET /entity_extraction_queue?thought_id=eq.<fixture>
  -> {"thought_id": <fixture>, "source_fingerprint": "4422ce05...c56ad"}
  == encode(digest(content,'sha256'),'hex') of the hidden row, matched exactly
```

Independently reproduces the verifier's 2026-08-31 finding. The content is bound; a
**derivative of the content is not**. `dfu-done.ps1` checks for the fingerprint as well as
the marker — comparing only the marker text would have called that door closed.

### Reproduced #2 — the agent plane's own door is unbound

`openbrain-mcp` connects with `DB_USER=postgres`, and `postgres` is `rolsuper=t,
rolbypassrls=t`. **RLS does not bind a superuser, FORCE or not.** So the boundary is void at
precisely the door U5 exists to contain. Confirms the orchestrator's Step 2/Step 3 split.

Fixture cleaned up and the cleanup **verified**: `0` fixture rows, `0` personal rows, 12,993
total — the same figure the run started from.

---

## CLAUSE 4 — a new shape of "in flight"

`work/u5rls`'s RLS migration is **applied to the live database** while its defining SQL is on
an **unmerged branch**. The work line's pinned OB1 tree has no
`docker/init-agent-memory-rls.sql`. So a fresh clone of the work line, deployed, would not
reproduce production.

This is the mirror of the failure clause 4 names. The clause says "a deliverable that merges
but does not run is not done"; this one **runs but has not merged**, and is just as much
unfinished work wearing a finished face. `dfu-done.ps1` requires both facts together — live
AND on the work line — and reports which half is missing.

Measured: 7 unmerged `work/*` (gitreach 3, u3gym 9, u4bidir 8, u5judge 7, u5pplane 11,
u5proxy 6, u5rls 14). `work/pod-key` excluded **by name with its reason recorded in the
output**, never silently. 15 worktrees. OB1 gitlink `adb7345` **reachable on the remote** —
queried with `ls-remote`, never from local tracking refs.

---

## CLAUSE 2 — what the chain reconstruction actually showed

Judged CURRENT against ORIGINAL, never pairwise. All eight chains reconstruct; no
requirement was lost. The chain is worth reading anyway — **U4's column has three distinct
states**:

```
2026-08-29 451ebfa  Gym: same anchored item run per quadrant ...; stall->oracle observed firing at least once
2026-08-30 2151193  The *Validated by* column is NOT satisfied - no quadrant comparison was run ...
2026-08-30 bf10d96  Gym: same anchored item run per quadrant ...; stall->oracle observed firing at least once
```

At `2151193` the column was **overwritten with a status report about itself** — the artifact
that decides whether the phase is done was replaced by a claim that it was not. `bf10d96`
restored it. Current equals original, so the clause passes on U4; the chain is printed so a
reader can see the excursion happened at all. This is exactly the history that would be
invisible under pairwise comparison.

---

## METHOD — seven defects found by RUNNING this, not by reading it

Every one produced a confident wrong answer, and every one is a sibling of a class already
on the list. Recorded because the file that decides "done" is a bad place for any of them.

1. **PowerShell 5.1 strips embedded `"` handed to a native exe.** The clause-3 fixture's SQL
   `'{"exposure":"personal"}'` arrived at psql as `'{exposure:personal}'`, was rejected as
   invalid JSON, and **every door reported "the fixture could not be written"**. The clause
   looked merely unevaluated rather than broken. Fixed by using `jsonb_build_object` — no
   double quotes anywhere in the SQL.
2. **`Start-Process -ArgumentList` quotes nothing.** One SQL statement became ~30 arguments;
   psql still exited 0. Replaced with the call operator.
3. **`-notmatch` does not populate `$Matches`.** Guarding with it and then reading
   `$Matches[1]` reads a STALE match — clause 7 reported "no DECISIONS.md entry" for every
   phase against a file holding 52 of them. *Green while checking nothing*, self-inflicted.
4. **`[ordered]@{1=...}` indexes by POSITION, not key.** Integer clause ids returned the
   wrong clause and ran off the end. Keys are strings now.
5. **`boolean::text` is `true`/`false`, not `t`/`f`.** A regex written against psql's display
   form made a working probe report "could not read" forever — an outage that never was.
6. **`git worktree add` does not populate submodules.** U6's check failed in the clean
   checkout with ENOENT on an `OB1/` path — a **false red attributed to a phase**. The clean
   checkout now inits submodules and records that init's own exit code.
7. **Python's `\b` in a non-raw string is a BACKSPACE byte.** A generated PowerShell regex
   `('\b' + $id + '\b')` was silently written as `('<0x08>' + $id + '<0x08>')` — invisible in
   every editor and in `grep`. Found only by dumping bytes. If you generate code with a
   script, the generator's escapes are part of your alphabet.

**And the meta-check.** A drill that cannot fail proves nothing, so `dfu-done.ps1` was
mutated to make `Resolve-ClauseVerdict` always return `met`, and the drill re-run against the
mutant: **RED, 15 of 41 assertions failed, every clause's own assertion among them.** That is
the evidence that the 41 green assertions mean something.

---

## DECISIONS entries to append

*(Not written to DECISIONS.md by this item — the orchestrator owns that file.)*

### 2026-08-31 · C.8 · THE DONE-AUTHORITY EXISTS, AND IT SAYS NOT DONE (6 unmet, 2 unevaluated)
`scripts/checks/dfu-done.ps1` decides every §C.8 clause by running something; verdict by
exhaustive census reusing `andon.ps1`'s shape. `scripts/checks/verify-dfu-done.ps1` proves it
can fail: 41 assertions, 8 of 8 clauses with a constructed failing case, and a mutant that
always returns `met` turns the drill RED (15 failures). Full run on `refactor/ai-stack-cleanup`:
exit 7, census balances 8/8, **0 met**. Coverage is printed per clause, so "clear because we
looked" and "clear because we didn't" are different words — clauses 1 and 5 are UNEVALUATED
at 2-of-7 and 2-of-8 because six phases name no executable check.
REVERT: delete both scripts; nothing else depends on them.

### 2026-08-31 · U5 / C.8 clause 3 · THE CORPUS PREDICATE IS FAIL-CLOSED (applied live)
`ob_corpus_on_ops_plane` was `IS NULL OR = 'ops'`; **12,989 of 12,993 thoughts were
unlabelled**, so the corpus was ops-visible by default. Closed by
`OB1/docker/init-agent-memory-corpus-failclosed.sql` — label first (no row changes
visibility), then drop the `IS NULL` arm. Agent plane sees **12993 before and after**;
an unlabelled row inserted in a rolled-back transaction is now **invisible (0)**. Additive,
idempotent, every touched row stamped for exact reversal.
REVERT: `OB1/docker/revert-agent-memory-corpus-failclosed.sql` (expect unlabelled 12989, ops 4,
stamped 0).

### 2026-08-31 · U5 · TWO DOORS STILL RETURN A PERSONAL ROW, reproduced against a canary
`entity_extraction_queue` returns the **SHA-256 of the hidden content, matched exactly** — a
hash is a disclosure, and the boundary governs the row but not its derivatives.
`openbrain-mcp` connects as `postgres` (`rolsuper=t, rolbypassrls=t`), so RLS does not bind
the agent plane's own door. Fixture removed; production shows 0 personal rows.
REVERT: n/a — measurement only.

### 2026-08-31 · C.8 clause 4 · DEPLOYED FROM CODE THAT HAS NOT LANDED
The RLS boundary is live on `openbrain-db` while its defining SQL exists only on the unmerged
`work/u5rls`; the work line's pinned OB1 tree does not contain it. A fresh clone of the work
line would not reproduce production. Clause 4 requires live AND on-the-work-line together.
REVERT: n/a — measurement only.

### 2026-08-31 · method · A GENERATOR'S ESCAPES ARE PART OF YOUR ALPHABET
Seven defects in this item were found by RUNNING the checker, never by reading it, and each
produced a confident wrong answer: quotes stripped by PS5.1 native invocation; `-notmatch`
leaving `$Matches` stale; `[ordered]` integer keys indexing by position; `boolean::text`;
`git worktree add` skipping submodules (a **false red attributed to a phase**); and a Python
`\b` written into generated PowerShell as a **backspace byte**, invisible to every editor and
to `grep`. SIBLING of *a derived gate whose alphabet is too narrow*, with the alphabet being
the escape rules of the language writing the code rather than the code itself.
REVERT: n/a — method.
