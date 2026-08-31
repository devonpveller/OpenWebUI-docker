# dfudone — the C.8 done-authority, what it says, and what it found

Item `dfudone`, 2026-08-31, worktree `wt-dfudone`, branch `work/dfudone`.
Plane lease held: `open-brain` (owner `dfudone`) for the clause-3 fixture and the migration.

**Built:**
- `scripts/checks/dfu-done.ps1` — the authority for PLAN.md §C.8. Eight clauses, each
  decided by running something, verdict by exhaustive census (the `andon.ps1` shape, reused
  rather than reinvented).
- `scripts/checks/verify-dfu-done.ps1` — the drill. **81 assertions with `-Live` (75
  without), 0 failed, 8 of 8 clauses have a constructed failing case.**
- `OB1/docker/init-agent-memory-corpus-failclosed.sql` + its revert, registered at slot
  `190-` in both compose files, documented in the promotion runbook, **applied to the live
  database**.

---

## THE VERDICT: `dfu-done.ps1` exits **7 — NOT DONE**

Full live run on `work/dfudone`, 2026-08-31, after round 2. Census balances 8/8.
**6 unmet, 2 unevaluated, 0 met.** Per §C.8 this is a REPORT, not a redefinition — no plan
column was touched.

| Clause | Verdict | Coverage | Why |
|---|---|---|---|
| 1 clean-checkout re-run | UNEVALUATED | 2 of 7 phases | Only U2 and U6 name an executable check; both **re-ran green from a clean checkout**. U0/U1/U3/U4/U5 name none — their columns are prose, which cannot re-run. Two named manual checks are pending: §2's U2 and U6 columns name no runnable artifact, so no machine can confirm the walkthrough command re-runs *that* column. |
| 2 parked + amendment chain | UNMET | 8 of 8 chains | U3's `VALIDATION-PARKED` entry is never closed by a later ledger heading. All eight chains reconstruct; every ORIGINAL requirement survives **verbatim** into CURRENT. |
| 3 personal plane by validation | UNMET | 9 of 11 subjects | Three doors returned the personal fixture or a derivative of it (below). Two subjects refuse: `wiki-compiler-output` is a named manual check, `mcp-read-tools` returned neither twin. Predicate half **closed**. |
| 4 nothing in flight, all running | UNMET | 9 of 9 | 8 unmerged `work/*`, 15 worktrees, a dirty tree, a submodule off its recorded commit, and 5 of 13 RLS stage tables not `t/t`. |
| 5 the walkthrough is true | UNEVALUATED | 2 of 8 rows | Six of eight phase sections name **no check at all**. |
| 6 U7 armed | UNMET | 1 of 1 | No ledger entry records a U7 cycle reaching adopted-or-refused. The loop has never run. |
| 7 audit trail complete | UNMET | 8 of 8 phases | U0 and U7 have **no DECISIONS.md entry**. U1–U6 all have entries and notes. |
| 8 the plane compounds | UNMET | 3 of 3 | 4 memories, 10 traces, 4 of which returned something — and **not one recall id is cited anywhere** in the ledger or the notes. |

### What would close each, and what it costs

- **1 and 5** — the same gap. Give U0, U1, U3, U4, U5, U7 a `**How to run:**` line naming a
  command that exits 0, and make §2's columns name the artifact that satisfies them (which
  also discharges clause 1's two manual checks). Cheap where a check exists (U0's
  kill-the-poller drill, U1's memory-plane gates); U3/U4 need the arena runs their columns
  actually demand.
- **2** — one closing ledger entry for U3 (the walkthrough already calls it DISCHARGED; the
  ledger does not). Minutes.
- **3** — see below. Three measured defects remain, one scoped by the orchestrator as its own
  item (Step 2/3 of the RLS work), plus one manual check nobody has run.
- **4** — merge or retire 8 branches, remove 15 worktrees, land `work/u5rls`, and decide what
  the RLS boundary should be on the four derived tables and on `wiki_pages`. Not mine.
- **6** — run one U7 cycle and record it. Real work, not paperwork.
- **7** — one U0 entry and one U7 entry.
- **8** — cannot be closed by writing anything. It needs a real effort to recall, be
  informed, and **cite what it was told**. §C.8 says this clause may fail; it does.

---

## ROUND 2 — eight defects the verifiers found, and what they were

Round 1 shipped a script that was honest at the headline (exit 7, 0 met) and wrong in eight
places underneath. Every one is a sibling of a class already on this effort's list, and every
one produced a *confident* answer. They are recorded because the file that decides "done" is
the worst place for any of them.

**1. A retraction that keeps the words was invisible (clause 2).** The survival test was
`if (-not $curNorm.Contains($req))` — a raw substring test. A column rewritten from
"the gym run is observed" to "the gym run is observed **is NO LONGER REQUIRED — dropped as
unnecessary**" still *contains* the words, so the clause reported "all 2 requirement(s) in the
ORIGINAL column survive". The one edit shape the clause exists to catch — an erosion that
stays defensible at every step — was the one shape it could not see, while the identical
removal written as a DELETION was caught correctly.
*Fix:* the test is now structural, so it does not depend on guessing English. A requirement is
CARRIED forward only if one of the CURRENT column's own semicolon-separated requirements is
exactly it. Anything else is a rewrite and needs an explicit disposition — `kept`,
`incoherent` (with checkable evidence) or `follow-on` (with an owner and a findings sink that
must EXIST), and for the last two the phase's change must also appear in §2.1 or DECISIONS.md.
Retraction and hedge word-lists are a second, harder refusal on top: a requirement the column
itself says is no longer required can never be dispositioned `kept`. Additions still never
fail. Red-proved in five shapes — deletion, retraction-in-place, "where feasible", moved to a
footnote, and an addition that must pass — plus a ledger that lies.

**2. `Invoke-Curl` asked curl for `%{http_code}` and never read it (clause 3).** The only
tests were curl's exit code and whether the marker appeared in the body, and PostgREST answers
a missing table with 404 and a JSON error body at curl exit 0. Proven live: with
`-PostgrestHost 'openbrain-postgrest:3000/nosuch'` **all five PostgREST doors reported "pass
:: attacked with the fixture and it did not come back", coverage 8 of 8 — including the door
that correctly fails against the real endpoint.** A renamed table, a schema change or auth
being switched on would have turned this clause green the same way.
*Fix:* the status is parsed and a non-200 is INDETERMINATE, never a pass. `-g` (globoff) was
also required: PostgREST filters carry `* ( )` and curl otherwise rejects the URL as
"malformed input to a URL function" — exit 3, no request sent.

**3. Three doors could not have failed under ANY boundary state (clause 3).**
`agent_memories` was never written to; `thought_entities` was probed with an unfiltered
`?select=thoughts(content)&limit=200` over 54,050 rows whose returned window covered
thought_ids 3..71 while the fixture sat at ~13,386; `wiki_pages` cannot hold a page the
compiler published for a row inserted seconds earlier. All three reported "attacked and it did
not come back" — a STRUCTURAL pass, not a measurement.
*Fix, and it is now the rule for every negative probe in the file:* **each carries a POSITIVE
CONTROL.** An ops-labelled twin of the fixture is written the same way at the same moment into
the same table, and a door passes only when it RETURNS the twin and REFUSES the personal row.
A door that returns neither is INDETERMINATE. Every probe is filtered to the fixture. The
fixture is now written to `thoughts`, `agent_memories`, `entities`, `thought_entities` and
`entity_extraction_queue`; `wiki_pages` became a named manual check rather than a probe that
cannot fail.

**4. The gitlink fallback was a LOCAL-object check (clause 4).** The tip-branch path did query
the remote; the fallback was `git fetch --dry-run origin <sha>` run inside OB1, which git
answers from the local object store — it sees it already has the commit and does nothing, exit
0. The comment three lines above said "QUERY THE REMOTE. Local remote-tracking refs are not
evidence".
*Fix:* the fallback fetch now runs in an EMPTY scratch repository, so only the remote can
satisfy it. "not our ref" is a FAIL; anything else non-zero is INDETERMINATE. Red-proved
against a real bare remote that lacks the pinned commit, and green-proved against a commit
that is reachable but not a tip.

**5. The script manufactured the defect it reports (clauses 1 and 4).** Clause 1's clean
checkout used `git worktree add`, which REGISTERS the scratch directory with the repo — so
clause 4's `git worktree list` counted it, including a concurrent run's.
*Fix:* the clean checkout is a `git clone --shared` into a temp directory. A clone is a
separate repository, so `git worktree list` in the work repo cannot see it: the exclusion is
by construction, not by name matching. Asserted in the drill by comparing the worktree list
before and after a clause-1 run.

**6. The manual mechanism was UNREACHABLE (clause 6).** `Get-ManualResult` guarded with
`-not $Store.PSObject.Properties.Name -contains $Name`, which binds as
`(-not <array>) -contains $Name` — always false. The next line then read a property that does
not exist; under `Set-StrictMode` that THROWS, the clause evaluator's catch replaced the whole
clause with `clause-6-threw`, and the machine probe that had already decided its half was
DISCARDED. §C.8 requires the script to REFUSE without a recorded result; crashing is not
refusing. **The drill was green over it because every fixture passed a path with no file at
all.**
*Fix:* the parentheses, plus four new drill cases — no file, a keyless file, a key with empty
fields, and a complete result that must actually LAND (a gate that can never be satisfied is a
wall, not a gate). A recorded *failing* result now makes the clause unmet rather than pending.

**7. Claims wider than their evidence.** Clause 1 said it re-runs "the §2 Validated by check"
and never opened §2 — it ran the first How-to-run span in WALKTHROUGH.md and dropped the
additional named checks in the same section while reporting full coverage. The
`openbrain-mcp` door printed "bound by the boundary" for a role whose flags read
`false/true`, because `rolsuper::text||'/'||rolbypassrls::text` yields `"false/true"` and the
comparison tested `'^t'` and `'/t$'`. Clause 4 checked `relforcerowsecurity` on `thoughts`
alone while §C.8.4 asks for "the RLS boundary at every stage". The `work/pod-key` carve-out
cited an operator ruling that appears in neither DECISIONS.md nor PLAN.md.
*Fixes:* §2's column is read and printed, EVERY command in a phase's section runs, and the
correspondence between the column and the commands is checked by the artifact names the column
uses (or becomes a named manual check where it names none). The MCP door is now CALLED rather
than inferred about — `list_thoughts` over HTTP, with the role flags kept as corroboration and
parsed with `CASE WHEN`. The RLS probe reports every stage table, derived from the schema (the
corpus tables, the published output, and every table with a foreign key into a corpus table).
The branch carve-out applies **only while DECISIONS.md records the branch**, and says so in the
output when it does not.

**8. Enumerations that were hand-lists.** Against this file's own rule 3.
*Fix:* clause 3's door floor stays pinned — it is §C.8's list, and a config could be thinned to
the doors that pass — but a new `door-set-matches-plan` probe extracts every backticked
identifier from C.8 clause 3 at run time and FAILS if any is unclaimed by a subject. A pinned
floor plus a drift check is a different thing from a hand list. Beyond the floor, the whole
PostgREST surface is enumerated from PostgREST's own path list and swept with one filtered
query per exposed table (57 paths, 56 swept), with the ops twin as the sweep's own control.

---

## CLAUSE 3 — one half CLOSED, three defects MEASURED

### Closed: the corpus predicate is now fail-closed

`ob_corpus_on_ops_plane` was `md->>'exposure' IS NULL OR = 'ops'` — so an **unlabelled**
thought was visible to the agent plane. Measured before: **12,989 of 12,993 thoughts carried
no label at all**, so nearly the whole corpus was ops-plane by default rather than by
decision. "Unlabelled defaults to fine", in SQL.

Migration order is label-first, then close — the reverse would hide 12,989 rows mid-transaction:

```
BEFORE  agent-plane sees 12993     unlabelled 12989 | ops 4 | personal 0
AFTER   agent-plane sees 12993     unlabelled 0 | ops 12993 | personal 0 | backfill-stamped 12989
PROOF   insert an unlabelled row AND an ops control row in a rolled-back tx
        -> agent plane sees the ops row and NOT the unlabelled one
```

The proof gained its control in round 2. Before, it measured only that the unlabelled row was
invisible — which is also what a broken query says.

### Measured #1 — the agent plane's own door RETURNS the personal row

`openbrain-mcp` connects with `DB_USER=postgres`, which is `rolsuper=t, rolbypassrls=t`, so
RLS does not bind it, FORCE or not. Round 1 inferred the leak from those flags. Round 2 CALLS
the door:

```
POST http://openbrain-mcp:8000/mcp  tools/call list_thoughts {"limit":25}
  -> 200, and the response contains DFU-DONE-PERSONAL-FIXTURE-<stamp>
     (the ops twin comes back too, so the query demonstrably works)
```

The boundary is void at precisely the door U5 exists to contain. Confirms the orchestrator's
Step 2/Step 3 split.

### Measured #2 — a hash is a disclosure, and so is a foreign key

```
GET /entity_extraction_queue?thought_id=eq.<personal>&select=thought_id,source_fingerprint
  -> the row exists and carries the SHA-256 of the hidden content
GET /thought_entities?entity_id=eq.<fixture-entity>&select=thought_id,thoughts(content)
  -> the embedded content is correctly NULL, but the row pointing at the hidden
     thought is returned
```

Both are derived data about a protected row. `entity_extraction_queue` is the door §C.8.3
already describes that way; the same standard applied to `thought_entities` is what turned its
structural pass into a measurement. In both cases the ops twin's row comes back as well, which
is what makes these measurements rather than coincidences.

### Not measured, and named as such

- `wiki-compiler-output` — a NAMED MANUAL CHECK. The compiler runs on its own schedule, so
  querying `wiki_pages` for a seconds-old fixture returns nothing whatever the boundary does.
  §C.8 permits a manual check the script refuses to pass without a recorded result; it does not
  permit a probe that is green by construction.
- `mcp-read-tools` (the ops door's `agent_memory_recall` lane) — returns NEITHER twin, so it
  refuses. It CAN fail (the personal twin coming back would be a fail); it cannot currently
  pass. That asymmetry is the correct one.

Fixture cleaned up and the cleanup **verified** across `thoughts`, `agent_memories` and
`entities`: `0` fixture rows, `0` personal rows in either corpus. Checked again by hand after
every run in this session.

---

## CLAUSE 4 — a new shape of "in flight", and the boundary is not one table

`work/u5rls`'s RLS migration is **applied to the live database** while its defining SQL is on
an **unmerged branch**. The work line's pinned OB1 tree has no
`docker/init-agent-memory-rls.sql`. So a fresh clone of the work line, deployed, would not
reproduce production. This is the mirror of the failure clause 4 names: it **runs but has not
merged**, and is just as much unfinished work wearing a finished face.

The RLS probe now reports every stage table rather than `thoughts` alone:

```
t/t  thoughts, agent_memories, agent_memory_{artifacts,audit_events,recall_items,
     relations,review_actions,source_refs}
t/f  entity_extraction_queue, idea_revisions, thought_edges, thought_entities
f/f  wiki_pages
```

Five of thirteen are not `t/t`, which is the same finding as clause 3's measured #2 seen from
the schema instead of from the door.

Measured: 8 unmerged `work/*` (gitreach 4, pod-key 1, u3gym 9, u4bidir 8, u5judge 7, u5pplane
11, u5proxy 6, u5rls 14). `work/pod-key` is a declared carve-out but **DECISIONS.md does not
record it**, so it is counted and the output says why. 15 worktrees. OB1 gitlink `adb7345`
**reachable on the remote** — it is a ref tip, so `ls-remote` decides it; the non-tip fallback
asks the remote from an empty scratch repository.

---

## CLAUSE 2 — what the chain reconstruction actually showed

Judged CURRENT against ORIGINAL, never pairwise. All eight chains reconstruct and every
ORIGINAL requirement survives verbatim, so no disposition is owed. The chain is worth reading
anyway — **U4's column has three distinct states**:

```
2026-08-29 451ebfa  Gym: same anchored item run per quadrant ...; stall->oracle observed firing at least once
2026-08-30 2151193  The *Validated by* column is NOT satisfied - no quadrant comparison was run ...
2026-08-30 bf10d96  Gym: same anchored item run per quadrant ...; stall->oracle observed firing at least once
```

At `2151193` the column was **overwritten with a status report about itself** — the artifact
that decides whether the phase is done was replaced by a claim that it was not. `bf10d96`
restored it. Current equals original, so the clause passes on U4; the chain is printed so a
reader can see the excursion happened at all. This is exactly the history that would be
invisible under pairwise comparison — and, until round 2, exactly the history a substring test
would also have missed had `bf10d96` not restored the text.

---

## METHOD — defects found by RUNNING this, not by reading it

Round 1 found seven; round 2 found three more the same way. Each produced a confident wrong
answer.

1. **PowerShell 5.1 strips embedded `"` handed to a native exe.** Round 1: the clause-3
   fixture's SQL `'{"exposure":"personal"}'` arrived at psql as `'{exposure:personal}'` and
   **every door reported "the fixture could not be written"**. Fixed with `jsonb_build_object`.
   **Round 2, the same trap one transport over:** the MCP JSON-RPC body arrived as
   `{jsonrpc:2.0,...}`, the server answered `-32700 Parse error` with HTTP 400, and all three
   MCP doors reported "answered HTTP 400, nothing was measured". Fixed by escaping every `"` as
   `\"`. The class recurs because the quoting layer is invisible from the source.
2. **`Start-Process -ArgumentList` quotes nothing.** One SQL statement became ~30 arguments;
   psql still exited 0. Replaced with the call operator.
3. **`-notmatch` does not populate `$Matches`.** Guarding with it and then reading
   `$Matches[1]` reads a STALE match — clause 7 reported "no DECISIONS.md entry" for every
   phase against a file holding 52 of them. *Green while checking nothing*, self-inflicted.
4. **`[ordered]@{1=...}` indexes by POSITION, not key.** Integer clause ids returned the wrong
   clause and ran off the end. Keys are strings now.
5. **`boolean::text` is `true`/`false`, not `t`/`f`.** Round 1: a regex written against psql's
   display form made a working probe report "could not read" forever. Round 2: the SAME
   confusion in the openbrain-mcp door printed **"bound by the boundary" for a role with
   BYPASSRLS**. Both now use `CASE WHEN`.
6. **`git worktree add` does not populate submodules** (round 1), and **registers the scratch
   checkout with the repo** (round 2, found by a verifier reading clause 4's own output). The
   clean checkout is a clone now, and it inits submodules from the work repo's own copy.
7. **Python's `\b` in a non-raw string is a BACKSPACE byte.** A generated PowerShell regex
   `('\b' + $id + '\b')` was silently written as `('<0x08>' + $id + '<0x08>')` — invisible in
   every editor and in `grep`. Found only by dumping bytes.
8. **`$pid` is a PowerShell AUTOMATIC variable.** Round 2: assigning the fixture's thought id
   to `$pid` fails with a non-terminating error, the name keeps the *process id*, and the
   "could the fixture be written?" guard saw a non-empty string and marched on to attack every
   door with a thought_id that does not exist. Found by the drill's unreachable-plane step —
   which is the only reason it is not still there.
9. **PowerShell 5.1's `ConvertFrom-Json` rejects PostgREST's OpenAPI document** ("the value of
   argument name is not valid"), which silently degraded the whole surface sweep to "could not
   be enumerated". Replaced with two narrow reads that work: a regex over the path list, and
   `information_schema.columns` for the text columns.
10. **A probe can manufacture its own hit.** The fixture entity was named
    `<personal-marker>-ENTITY`, so the surface sweep found the marker in `entities` and
    reported a leak — this script detecting a row it had just written. A probe that
    manufactures its own hit is as useless as one that cannot hit at all, in the other
    direction.

**And the meta-check.** A drill that cannot fail proves nothing, so `dfu-done.ps1` was mutated
to make `Resolve-ClauseVerdict` always return `met` and the drill re-run against the mutant:
**RED, 15 of 41 assertions failed** (round 1 numbers), every clause's own assertion among them.
That is the evidence the green assertions mean something. The round-2 drill is 81 assertions
with `-Live`, 75 without.

---

## DECISIONS entries to append

*(Not written to DECISIONS.md by this item — the orchestrator owns that file.)*

### 2026-08-31 · C.8 · THE DONE-AUTHORITY EXISTS, AND IT SAYS NOT DONE (6 unmet, 2 unevaluated)
`scripts/checks/dfu-done.ps1` decides every §C.8 clause by running something; verdict by
exhaustive census reusing `andon.ps1`'s shape. `scripts/checks/verify-dfu-done.ps1` proves it
can fail: **81 assertions with `-Live`**, 8 of 8 clauses with a constructed failing case, and a
mutant that always returns `met` turned the round-1 drill RED. Full run on `work/dfudone`:
exit 7, census balances 8/8, **0 met**. Coverage is printed per clause, so "clear because we
looked" and "clear because we didn't" are different words — clauses 1 and 5 are UNEVALUATED at
2-of-7 and 2-of-8 because six phases name no executable check.
REVERT: delete both scripts and `dfu-done-manual.json` / `dfu-done-dispositions.json`; nothing
else depends on them.

### 2026-08-31 · C.8 clause 2 · A RETRACTION THAT KEEPS THE WORDS WAS INVISIBLE
The survival test was a raw substring test, so a requirement rewritten in place to say it is
"no longer required" still counted as surviving; the identical removal written as a deletion
was caught. Replaced with a structural rule (carried forward only if the requirement survives
VERBATIM as one of the current column's requirements) plus an explicit disposition ledger
(`kept` / `incoherent` + evidence / `follow-on` + owner + a findings sink that must exist,
cross-checked against §2.1 and DECISIONS.md). Retraction and hedge word-lists are a second
refusal that no `kept` record can overrule. Additions still never fail. Red-proved in five
shapes plus a lying ledger.
REVERT: restore the previous `Test-Clause2` body; the dispositions file becomes inert.

### 2026-08-31 · C.8 clause 3 · EVERY NEGATIVE PROBE NOW CARRIES A POSITIVE CONTROL
Round 1's doors could report "attacked and it did not come back" while attacking nothing: the
HTTP status was requested from curl and never read (all five PostgREST doors passed against
`openbrain-postgrest:3000/nosuch`), and three doors could not have failed under any boundary
state. Each probe now writes an OPS-labelled twin of the fixture and passes only when the door
RETURNS the twin and REFUSES the personal row; neither = INDETERMINATE; non-200 = INDETERMINATE;
every probe is filtered to the fixture. `wiki_pages` became a named manual check rather than a
probe that cannot fail.
REVERT: n/a — measurement only; the fixtures are removed and verified each run.

### 2026-08-31 · U5 · THREE DOORS RETURN THE PERSONAL ROW OR A DERIVATIVE, measured
`openbrain-mcp` (connecting as `postgres`, `rolsuper=t/rolbypassrls=t`) **returns the personal
fixture from `list_thoughts` over HTTP** — inferred in round 1 from role flags, measured in
round 2 by calling the door. `entity_extraction_queue` returns the row and the SHA-256 of the
hidden content; `thought_entities` nulls the embedded content but returns the row pointing at
the hidden thought. In every case the ops twin comes back too, so these are measurements.
Fixture removed; production shows 0 personal rows in both corpora.
REVERT: n/a — measurement only.

### 2026-08-31 · C.8 clause 4 · THE GITLINK GATE WAS A LOCAL-OBJECT CHECK
The non-tip fallback was `git fetch --dry-run origin <sha>` inside OB1, which git answers from
the local object store: a commit that exists only in this clone passed a gate whose purpose is
"a fresh --recurse-submodules clone would break". The fetch now runs in an EMPTY scratch
repository; "not our ref" is a fail and any other error is indeterminate. Also: the clean
checkout is a clone rather than a worktree, so the script no longer registers a scratch
directory for its own clause 4 to count.
REVERT: restore the previous fallback and `New-CleanCheckout` body.

### 2026-08-31 · C.8 clause 4 · THE RLS BOUNDARY IS NOT ONE TABLE
§C.8.4 asks for the boundary "at every stage"; the probe read `relforcerowsecurity` on
`thoughts` alone. The stage set is now derived from the schema — the corpus tables, the
published output, and every table with a foreign key into a corpus table — and **5 of 13 are
not `t/t`**: `entity_extraction_queue`, `idea_revisions`, `thought_edges`, `thought_entities`
are `t/f` and `wiki_pages` is RLS-off.
REVERT: n/a — measurement only.

### 2026-08-31 · C.8 · A CARVE-OUT MUST BE EARNED EACH RUN
The `work/pod-key` exclusion was applied on the script's own say-so, attributed to an operator
ruling that appears in neither DECISIONS.md nor PLAN.md. It is now conditional on DECISIONS.md
naming the branch; without that entry the branch is counted like any other and the output says
so. **As of this run there is no such entry, so `work/pod-key` is counted among the 8 unmerged
branches.**
REVERT: make the exclusion unconditional again (do not).

### 2026-08-31 · method · A GENERATOR'S ESCAPES ARE PART OF YOUR ALPHABET
Ten defects across two rounds were found by RUNNING the checker, never by reading it, and each
produced a confident wrong answer: quotes stripped by PS5.1 native invocation (twice, in two
different transports); `-notmatch` leaving `$Matches` stale; `[ordered]` integer keys indexing
by position; `boolean::text` (twice); `git worktree add` skipping submodules and registering a
scratch worktree; a Python `\b` written into generated PowerShell as a **backspace byte**;
`$pid` silently refusing assignment because it is an automatic variable; `ConvertFrom-Json`
rejecting a valid document; and a probe matching a row it had written itself. SIBLING of *a
derived gate whose alphabet is too narrow*, with the alphabet being the escape rules and
reserved names of the language writing the code rather than the code itself.
REVERT: n/a — method.
