# dfudone — the C.8 done-authority, what it says, and what it found

Item `dfudone`, 2026-08-31, worktree `wt-dfudone`, branch `work/dfudone`.
Plane lease held: `open-brain` (owner `dfudone`) for the clause-3 fixture and the migration.

**Built:**
- `scripts/checks/dfu-done.ps1` — the authority for PLAN.md §C.8. Eight clauses, each
  decided by running something, verdict by exhaustive census (the `andon.ps1` shape, reused
  rather than reinvented).
- `scripts/checks/verify-dfu-done.ps1` — the drill. **201 assertions without `-Live`, 0
  failed, 8 of 8 clauses have a constructed failing case** (round 5; it was 81/75 at round 2).
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

---

## ROUND 4 — WHERE A ROW MAY LIVE, AND WHICH CELL IS *Validated by*

A verifier's summary of what round 3 left open, and it is exact: *round 3 pinned WHICH ids
must exist; it did not pin WHERE the row that defines an id may live, or WHICH CELL is
Validated by.* Round 4 closes that, plus five more places where a substring stood in for a
structure. Full live run after this round: **exit 7, census 8/8 balances, 0 clauses met,
production 0 personal rows.** Drill: **168 assertions with `-Live` (153 without), 0 failed,
8 of 8 clauses have a constructed failing case.**

### 2026-08-31 · C.8 · THE PHASE TABLE PARSER READ THE WHOLE DOCUMENT, POSITIONALLY
`Get-PhaseTable` line-scanned the ENTIRE file for a row naming `U<n>`, honoured no section
boundary, no code fence and no HTML comment, took LAST-WINS on a duplicated id, and indexed
cells by POSITION without ever parsing the header row. Three attacks landed on it, all
constructed in the drill now:
- **SUBSTITUTION** (drill P1) — weaken U4's visible *Validated by* and append the ORIGINAL row
  verbatim inside `<!-- ... -->` at the end of the file. Clause 2 passed with both original
  requirements reported CARRIED. The same edit WITHOUT the comment failed correctly, which is
  the tell that the comment was doing the work.
- **RESURRECTION** (drill P2) — delete U1's row from §2 and put a one-line row for it inside an
  HTML comment. `phase-floor-present` passed — "every floor phase has a row in section 2's
  table" — for a row no reader sees.
- **COLUMN SHIFT** (drill P3) — insert `| Original validation (historical) |` ahead of the real
  column; cell 3 read the decoy while the visible *Validated by* said something weaker.
The parser now anchors to §2's heading and stops at the next heading OF ANY LEVEL (a
same-or-higher rule ran the table's region straight through `### 2.1 Amendments`), strips fenced
blocks and HTML comments first, finds the *Validated by* column BY NAME in the header row, and
REFUSES on a duplicated id rather than picking a winner. Drill P5 is the positive control: the
columns are REORDERED and the same requirements are still found.
REVERT: restore the previous `Get-PhaseTable`.

### 2026-08-31 · record · PLAN.md REVISION `2151193` CARRIES TWO U4 ROWS
Not hypothetical, and found by the new parser on the real history: that revision's §2 table has
`| **U4** | ... |` AND `| **U4 status (2026-08-30)** | **PARTIALLY DONE, REMAINDER PARKED.** ... |`.
Under LAST-WINS the chain read the STATUS row's cell as U4's *Validated by* — the printed chain
step *"The Validated by column is NOT satisfied — no quadrant comparison was run..."* was never
U4's column at all. U4's chain therefore crosses a hole, and `chain-U4-has-a-hole` now says so
by name while the other phases' chains are unaffected.
REVERT: n/a — a fact about the record.

### 2026-08-31 · C.8 clause 5 · THE ONE CLAUSE THAT NEVER GOT THE FLOOR
`Add-PhaseFloorProbes` was applied to clauses 1, 2 and 7 and **not** to 5, whose
`coverage.expected` was the number of `## U<n>` sections parsed out of WALKTHROUGH.md — the
document under test. Deleting the six phase sections that name no check made clause 5 report
**MET at "evaluated 2 of 2"** with no `not_evaluated` entry. The floor is applied now, unioned
with whatever sections the walkthrough has; drill P8 constructs the trimmed document. This is
the population class again, in the clause that did not get the fix — the shape was grepped for
across the whole file afterwards.
REVERT: drop the `Add-PhaseFloorProbes` call in `Test-Clause5`.

### 2026-08-31 · C.8 clauses 1 and 5 · ONLY THE FIRST COMMAND AFTER EACH MARKER RAN
`Get-WalkthroughRuns` captured the FIRST backtick span after each `**How to run:**`.
WALKTHROUGH.md's U6 row names TWO commands under ONE marker and the second was never run — a
verifier ran it by hand and it **FAILED** (`ModuleNotFoundError: sqlalchemy`; it needs the
agent-bridge venv) while clause 5 reported `walkthrough-U6-check-1 = pass` and counted U6 fully
evaluated. Every backtick span in a marker's own block is now a command, the block ending at
the next bold label, heading, or blank line. **Drill step A3's fixture used two SEPARATE
markers — a shape the real document does not use — which is precisely why it could not catch
this**; it now uses the document's shape and A3b keeps the other one.
On `work/dfudone` that second command is still red: the correction (venv interpreter,
`17cfb29`) is on `refactor/ai-stack-cleanup` and is not merged here.
REVERT: restore the single-span regex.

### 2026-08-31 · C.8 clause 4 · AN EXEMPTION GRANTED BY ANY SENTENCE THAT MENTIONS IT
The `work/pod-key` carve-out was granted by `$decForBranches.Contains($b)` — a raw substring
search of DECISIONS.md — so ANY sentence containing that string granted it, **including one
saying it must NOT be excused**; a verifier proved it by appending this note's own text. A
carve-out now requires a STRUCTURED record: a `## ... clause 4 exclusion ...` entry carrying an
`Excluded from C.8 clause 4: <branch>` directive naming the branch exactly. Drill X1 constructs
both halves — the arguing-against-it prose (refused, with the reason recorded) and the record
(granted). **There is still no such entry, so `work/pod-key` is counted.**
REVERT: restore the `.Contains($b)` test (do not).

### 2026-08-31 · C.8 clause 2 · UN-PARKING BY A LATER HEADING THAT SAID "CLOSED"
Any later `## ` heading containing the phase id and CLOSED/CLOSES/DISCHARGED/UNPARKED closed a
PARKED entry, regardless of what was closed. On this ledger that is live: **U4's PARKED entry
was being discharged by "2026-08-30 · U4 clause 3 · final state — two residual defects closed"**,
a heading about the CHECKER's clause 3. A parked entry is now closed only by a later entry that
CITES it (`**Un-parks:** <the parked heading>`), and a citation matching two entries closes
neither. **Outstanding went 1 → 2: U3's VALIDATION-PARKED and U4's PARKED.**
REVERT: restore the heading-word test.

### 2026-08-31 · C.8 clause 7 · TWO OF THE THREE ARTIFACTS WERE GRANTED BY A MENTION
- The commit half passed on any commit whose message named the phase ANYWHERE and any
  `*.ps1/py/ts/sql` ANYWHERE: `audit-trail-U1` was green on two commits **about this checker**,
  neither of which says what was validated for U1. A commit now counts only when the artifact it
  names is one the PHASE names (§2's column ∪ the walkthrough's How-to-run commands for it), and
  a commit whose entire changed-file set is the done-authority and its drill discharges nothing.
- The findings-note half counted any `*.md` in `documentation/notes` whose BODY matched `\bU3\b`,
  so one unrelated note mentioning a phase in passing discharged that artifact. A note must now
  name the phase in its FILENAME or in a HEADING.
Consequence, and it is a report not a redefinition: **U0, U1, U3, U4, U5 and U7 name no runnable
check anywhere** — neither §2's column nor a `How to run` line — so nothing can state "by which
check" for them. U2 and U6 pass this clause honestly.
REVERT: restore the two `-match` tests.

### 2026-08-31 · C.8 · TWO FLOORS WERE READ BY A FIRST-MATCH REGEX OVER THE WHOLE PLAN
`Get-PlanPhaseFloor` and `service-set-matches-plan` both located their clause with a lazy
first-match regex over the entire file, so a decoy passage earlier in PLAN.md becomes what the
floor is checked against. Both are now anchored inside §C.8 and REFUSE when the heading or the
clause matches more than once (drill P6a/P6b). The service enumeration also truncated at the
first `.`, silently dropping every item behind a service named after a period; a period now ends
the sentence only when whitespace follows (drill P7).
REVERT: restore the two regexes.

### 2026-08-31 · C.8 clause 4 · THE DIRECT-CLIENT SET WAS INCOMPLETE AND SAID NOTHING
`Get-DirectDbClients` matched only `^(DB_USER|PGUSER|POSTGRES_USER)=` or a `proto://user:` URI.
**`open_notebook` reaches `openbrain-db` as `postgres` (rolsuper/rolbypassrls = t/t) via
`OB1_DB_USER` and was never enumerated at all**, and **`openbrain-idea-refinery` carries
`DB_HOST=openbrain-db` with no role variable and was silently skipped** — so the boundary's pass
condition was decidable over an incomplete set with no record of what could not be determined.
The alphabet now covers any prefixed role variable and any URI pointing at the host, a client
whose role cannot be read is INDETERMINATE rather than absent, a role pg_roles does not answer
for is INDETERMINATE too, and the pass note states the restriction (clients are identified from
container ENVIRONMENT; a client that hardcodes the host is not visible). Live result:
`service-rls-boundary` is **indeterminate**, naming `openbrain-idea-refinery`.
**Drill step L4's else-branch asserted `$true`**, which is why it could not catch this; it now
enumerates the network independently through Docker and requires EVERY container whose
environment names the database to appear in clause 4's own detail lines — and it runs whether or
not a bypassing client was found, because "none found" over an incomplete set is the answer it
exists to distrust.
REVERT: restore the three anchored patterns.

### 2026-08-31 · method · THE BOLD SWALLOWS THE COLON
The first version of the new exclusion directive matched `**Excluded from C.8 clause 4**:` while
the record writes `**Excluded from C.8 clause 4:**` — the colon is INSIDE the emphasis. The grant
silently never fired, and the drill's positive control is the only reason it was caught rather
than shipped as "a gate that refuses everything". SIBLING of *a derived gate whose alphabet is
too narrow*; the emphasis is now stripped before the directive is matched on its words.
REVERT: n/a — method.

### 2026-08-31 · process · A PATCH SCRIPT THAT PRINTED `ok` WAS NEVER RUN
One of this round's edits was written to a file and never executed; the `ok` on the console came
from the NEXT script in the same command line. `-SkipLive` runs stayed green because the affected
code path is live-only, and it surfaced as `clause-4-threw` in the first full run. SIBLING of
*stopping the read early then generalising*: the evidence for "the patch applied" was a success
message that belonged to something else. What caught it was running the thing, not re-reading it.
REVERT: n/a — process.

### 2026-08-31 · convergence (§C.7) · ROUND 4 FOUND NO NEW CLASS
Every finding above is a sibling of one of the three already on the record: *a checker deriving
its population from the document under test* (clause 5's floor), *a claim wider than its
evidence* (the first-command-only reader, the incomplete client set, the `$true` assertion), or
*a substring standing in for a structure* (the table parser, the carve-out, the un-parking, the
two commit/note halves, the two first-match regexes). **This is a claim for a verifier to try to
break, not a conclusion.** If it holds, the convergence counter reaches 2 of 2.

---

# ROUND 5 — THE FIFTEENTH CLASS, AND THE FIFTH "FIXED ONE, LEFT THE SIBLING"

Round 4 claimed *no new class*. A verifier broke that claim, and the counter **resets to 0**.
That is the criterion working: the convergence bound in §C.7 is a claim about the world, and
the only thing that can settle it is somebody trying to falsify it.

## The new class — a checker that executes instructions from the artifact under test, then measures a world those instructions can change

Every one of the fourteen classes on this record constrains what the checker **reads** — where
a row may live, which cell is authoritative, whether an absence was measured or assumed. All
fourteen can be closed perfectly and leave this untouched, because it is not about inputs. It
is about **effects**.

**Reproduced, exactly.** Clauses 1 and 5 execute every backtick span under a `**How to run:**`
marker in WALKTHROUGH.md, under `cmd.exe /c`, with the operator's privileges — and clause 5 ran
them with `-WorkDir $Ctx.root`, i.e. *inside the audited repository*. Clauses evaluate in order
1..8. A `## U0` section whose marker read
`cmd /c echo ## U0 findings > <repo>\documentation\notes\u0-findings.md` produced
`walkthrough-U0-check-1 (exit 0) — the row's named check re-runs green`; the file did not exist
before the run and did after; and **clause 7's `audit-trail-U0` went from exit 3 to exit 2** —
its findings-note artifact discharged by a file the run itself had created. The same channel
reaches clause 4's subjects (worktrees, `work/*` branches, tree cleanliness), because clause 1
runs first and `cmd.exe` is not confined to the clean clone.

**The fix, and why it is in that order.** Three parts, and only the second is load-bearing.

1. **Isolation.** Every walkthrough command now runs in the clean clone clause 1 already
   builds — never in `$Ctx.root` — and the documents later clauses depend on are made
   read-only to that process for the duration of the call (a Deny ACE for the running
   identity on the plan's directory and `documentation/notes`, removed in a `finally`, and
   swept at startup so a killed run cannot leave the operator's PLAN.md locked). The sandbox
   is reset between phases, so one phase's command cannot manufacture the artifact another
   phase's command needs.
2. **Effect-nullification — the actual fix.** Isolation is a wall, and a wall can be walked
   around: an absolute path, a `git -C`, a network push. So **every artifact any clause reads
   is snapshotted before the first command runs** — the three documents, the findings notes,
   the commit log, the branch list, the worktree list, the working tree's cleanliness, the
   submodule states, the gitlink, the OB1 remote's advertised refs, and PLAN.md's whole
   revision history — and every clause decides over the snapshot. Nothing created during the
   run can discharge anything, because nothing created during the run is ever read. It does
   not have to enumerate the ways a command reaches the tree, which is why it is not another
   filter.
3. **Disclosure.** Every run now prints **the command set it executed** — what ran, where, and
   what it returned — and fingerprints the audited artifacts before and after each command and
   once at the end. A command that moves the audited tree turns its own probe RED whatever its
   exit code was; a run whose integrity record is false can never be `done`, and the board reads
   `unaccounted`.

**Stated limits, rather than papered over.** The Deny ACE covers the documents and the notes
directory, not `.git` — denying writes there would break the very git commands clause 4 runs.
Git state is protected by the snapshot and the fingerprint, not by the filesystem. A command
that pushes to a *foreign* remote (OB1's) is outside both: `ls-remote` is snapshotted before any
command runs, which closes the read, but this file cannot un-push.

**Red-proof (drill V1/V2).** V1 is the demonstrated attack: the note-writing marker. Its command
is now denied (`Access is denied.`, exit 1), the file does not exist afterwards, and clause 7
still reports `no findings note` for U0. V2 is the harder half — a marker that **succeeds**:
`git -C <root> branch work/drill-injected`. The branch really is created (asserted, so the step
cannot pass against a command that never ran), and it changes nothing: clause 4's answer does not
contain it, the probe fails on contamination, the integrity record is false, and the board is
`unaccounted`.

## The fifth "fixed one, left the sibling" — and this one was a ONE-CALL-SITE function

`Remove-NonProse` strips HTML comments and code fences. It had **exactly one call site in 3,866
lines** — inside `Get-DfuSection`, which serves PLAN.md and nothing else. WALKTHROUGH.md and
DECISIONS.md were read **raw**, so round 4's own attacks worked unchanged one file over:

- five WALKTHROUGH phase sections inside a properly **closed** comment → clause 5 verdict **met**,
  coverage **8 of 8**, floor pass, every `walkthrough-U<n>-check-1` green — over a document showing
  two sections to the operator whose review is that clause's entire justification;
- a commented `## ` ledger entry carrying `**Un-parks:**` closed a PARKED entry; a commented
  `## … clause 4 exclusion` granted the pod-key carve-out (unmerged 8 → 7); a commented heading
  discharged clause 7's ledger artifact;
- and `Get-WalkthroughRuns` **parsed and executed** a `## U<n>` section that lived inside a comment.

Every markdown reader now normalises on the way in — `Get-DfuSection`, `Get-LedgerSections`,
`Get-WalkthroughRuns`, the new `Get-WalkthroughSectionIds` — and the single door a document comes
through is `New-DfuSnapshot`, which stores the normalised form beside the raw one so an inline
scan in a clause body cannot accidentally get the raw text. **The grep is the check:**
`grep -n 'Read-TextFile' scripts/checks/dfu-done.ps1` now shows it outside the plumbing only in
the snapshot and the fingerprint. Drill M1–M4.

**And comment-stripping was defeated outright by an unterminated `<!--`**, because the regex
requires the closer: delete U5's row from §2 and put a copy after a bare opener inside §2, and it
was read as a row. Malformed markup now **fails closed** — everything after the opener is
discarded, exactly as for an unterminated fence — and the malformation is **stated** by a
`markdown-well-formed-<doc>` probe, because "refused wide" and "the document is shorter" are
otherwise indistinguishable. Drill M5.

## Three more, each reproduced

- **GFM's escaped pipe.** `Split-TableRow` split on every `|` and ignored `\|`, so a cell
  containing an escaped pipe became two cells and every column after it shifted. Park the
  original requirement in the What cell behind `\|` and the Validated-by column is read one
  place to the left: the original is "found", the visible column is weakened, and
  `chain-U0-original-vs-current` passes. The header names a column and that name selects an
  **index** — an index applied to misaligned cells is not "found by name". Now split on an
  unescaped pipe, then unescape. Drill T1.
- **"EVERY COMMAND UNDER A MARKER RUNS" was not true.** The block terminator was `(?m)^\s*$`, so
  a second command under the *same* marker separated by a blank line was never executed. The
  block now ends at a **structure** — the next line-initial bold label, heading, table row, or
  horizontal rule — never at a paragraph break. The direction of the error is the argument:
  stopping early skips a named check and still reports full coverage (a silent false green),
  while running one span too many produces a loud red, and it now runs inside a disposable
  clone. Drill W1.
- **A LIVE FALSE PASS.** `audit-trail-U2 = pass` rested entirely on `8b477a9` — a commit about
  U4 whose own summary says *"No code behaviour changed"*, which mentions U2 in one sentence
  (`2 pre-existing F811s from U2's 86ffa62`) and `test_anchor_schema.py` in the next, describing
  a lint finding in someone else's file. The phase-id match and the artifact match were
  **independent substring searches over the whole message**. A commit now discharges the phase
  only through a **validation claim** — a directive line (`Validated: … / Verified by: …`) whose
  own text names the phase **and** one of that phase's own checks *in the same statement*. Same
  shape as the ledger's `Un-parks:` and clause 4's `Excluded from C.8 clause 4:`, and for the
  same reason: a record is something an author wrote on purpose and a reader can find. Drill
  X4a is the co-mention (must not discharge); **X4b is the positive control** — a commit that
  does carry the claim must discharge, or the rule is a wall rather than a measurement.

## A sixth instance, found while fixing the fifth

`door-set-matches-plan` (clause 3) still ran a lazy **first-match** regex over the whole plan.
Round 4 anchored `phase-floor-matches-plan` and `service-set-matches-plan` to section C.8 and made
ambiguity refuse — and left the third instance of the identical read. A decoy passage earlier in
PLAN.md would become the paragraph the door floor was checked back against. Now anchored to C.8
and refusing on ambiguity, like its two siblings.

## What the authority says now

The headline is unchanged and honest: **exit 7, census balances 8/8, 0 clauses met.** The changes
above make more of it red, not less — clause 7's commit half now fails for every phase, because no
commit on this work line was ever written with a validation claim in it. That is the true statement
about a history that did not write it down, and §C.8's instruction is explicit: *a clause that
cannot be met is a REPORT, not a redefinition.* No plan column was touched.

## And one in the drill itself — the same class, one layer down

`git commit -m "<subject>\n\n<body>"` passed through PowerShell 5.1's native-argument handling
is **split at the newlines**: git reads the body as pathspecs, prints
`error: pathspec ... did not match any file(s)`, and exits 1. Every fixture call site wrote
`[void](Invoke-InDir ...)`, which discards the exit code. So step X4a would have "proved" that a
co-mention does not discharge a phase **against a repository that contained no such commit at
all** — an assertion that was true, and true of nothing. It only surfaced because the tightened
clause-7 rule made two *positive controls* (I2 and X3a) go red, and chasing those found the
fixture had never been built. A negative assertion whose fixture silently failed to exist is this
effort's own recurring class, inside the thing that exists to catch it.

Fixture commits now go through `Add-FixtureCommit`: one `-m` per paragraph (git joins them with a
blank line, which is the shape the authority parses) and **the exit code is asserted**. A fixture
that did not get built is a red at the point of building, never a quiet pass downstream.

**This is also the argument for the positive controls.** Two of them are the only reason this was
found: a drill made only of negative assertions would have gone green over an empty repository and
reported the rule working.

## Convergence (§C.7) — the counter RESETS

Round 4 claimed no new class and put the counter at 1 of 2. Round 5 found one — *a checker that
executes instructions from the artifact under test and then measures a world those instructions
can change* — so **the counter is 0**. The class list stands at fifteen. The three siblings above
(the escaped pipe, the blank-line block terminator, the co-mention commit) are NOT new classes;
the fifth `Remove-NonProse` violation is not either. Only the effects one is, and it is the first
on this record that says nothing about what the checker reads.
