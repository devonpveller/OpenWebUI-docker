
=========================================================================
 DFU-DONE - is the dark-factory-unification plan 100% met?
   work line : refactor/ai-stack-cleanup
   plan      : D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md
   -SkipLive : live probes were NOT run; those clauses are UNEVALUATED
=========================================================================

CLAUSE 1 [UNMET] Every U-phase column is satisfied by a check that RAN, from a clean checkout
   coverage: evaluated 2 of 9 U-phases U0-U6 (named by C.8.1) and U8 (added by C.9), unioned with section 2's table - plus the floor's own drift check
   NOT evaluated: U0, U1, U3, U4, U5, U8
   [pass] phase-table-unambiguous (exit 0)
        $ parse section 2's table in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md: anchor on the section heading, strip fenced blocks and HTML comments, read the Validated-by column BY NAME from the header row, refuse on a duplicate id
        section 2's table parsed to ONE unambiguous table: header [Phase | What | Validated by | Depends on], 9 phase row(s), no duplicate id, nothing read from a code fence or an HTML comment; 1 id cell(s) were ADMITTED carrying a parenthesised qualifier - the qualifier is treated as part of the id cell's own name, and it is named here so an id carrying STATUS is visible even when it was accepted: U7: **U7 (standing)**
   [fail] phase-floor-matches-plan (exit 1)
        $ extract the phase ids C.8 clause 1 names in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md and compare them with the pinned floor
        the pinned floor and C.8 clause 1 disagree - the plan names U0,U1,U2,U3,U4,U5,U6; pinned but unnamed: U8; named but unpinned: none
   [pass] phase-floor-present (exit 0)
        $ read section 2's table and compare it with the pinned floor U0,U1,U2,U3,U4,U5,U6,U8
        every floor phase (U0,U1,U2,U3,U4,U5,U6,U8) is present in section 2's table
   [indeterminate] U0-validated-by (exit n/a)
        $ (none - no 'How to run' recorded for U0 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only
   [indeterminate] U1-validated-by (exit n/a)
        $ (none - no 'How to run' recorded for U1 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only
   [pass] U2-validated-by-1 (exit 0)
        $ python -m pytest scripts/agent-harness/test_harness_config.py scripts/agent-harness/test_anchor_schema.py -q
        re-ran GREEN in the clean checkout
   [pass] U2-left-the-audited-tree-unchanged (exit 0)
        $ fingerprint the plan, the ledger, the walkthrough, documentation/notes, git refs/status/worktrees/submodules before and after each of U2's 1 command(s)
        the plan, the ledger, the walkthrough, documentation/notes and git's refs, status, worktrees and submodules are byte-identical before and after U2's 1 command(s), which ran in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92 with the audited documents locked
   [indeterminate] U2-check-matches-section-2 (exit n/a)
        $ compare section 2's U2 column against the 1 command(s) run for it
        section 2's column names no runnable artifact, so the correspondence is a NAMED MANUAL CHECK - see the manual entry for this phase
   [indeterminate] U3-validated-by (exit n/a)
        $ (none - no 'How to run' recorded for U3 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only
   [indeterminate] U4-validated-by (exit n/a)
        $ (none - no 'How to run' recorded for U4 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only
   [indeterminate] U5-validated-by (exit n/a)
        $ (none - no 'How to run' recorded for U5 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only
   [pass] U6-validated-by-1 (exit 0)
        $ python scripts/checks/recall-falsifiability-drill.py
        re-ran GREEN in the clean checkout
   [fail] U6-validated-by-2 (exit 1)
        $ agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q
        exited 1 in the clean checkout: operable program or batch file.
   [pass] U6-left-the-audited-tree-unchanged (exit 0)
        $ fingerprint the plan, the ledger, the walkthrough, documentation/notes, git refs/status/worktrees/submodules before and after each of U6's 2 command(s)
        the plan, the ledger, the walkthrough, documentation/notes and git's refs, status, worktrees and submodules are byte-identical before and after U6's 2 command(s), which ran in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92 with the audited documents locked
   [indeterminate] U6-check-matches-section-2 (exit n/a)
        $ compare section 2's U6 column against the 2 command(s) run for it
        section 2's column names no runnable artifact, so the correspondence is a NAMED MANUAL CHECK - see the manual entry for this phase
   [indeterminate] U8-validated-by (exit n/a)
        $ (none - no 'How to run' recorded for U8 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only
   [MANUAL:PENDING] section-2-column-mapping-U2
        Section 2's U2 column names no runnable artifact ('Gym: one goal driven from a git issue through sweep→plan→weekly thread→approve→land on each target; a deliberately overlapping issue pair must be flagged by the synthesis; schema cross-reader test'), so no machine can confirm the walkthrough command(s) re-run THAT column. Confirm by hand which command satisfies it, or make the column name its check.
        NO RECORDED RESULT - record one in D:\final\documentation\implementation-guide\dark-factory-unification\dfu-done-manual.json under this exact name
   [MANUAL:PENDING] section-2-column-mapping-U6
        Section 2's U6 column names no runnable artifact ('Gym: an unattended run that hits each andon condition halts-and-raises; one that hits none lands with a complete audit trail'), so no machine can confirm the walkthrough command(s) re-run THAT column. Confirm by hand which command satisfies it, or make the column name its check.
        NO RECORDED RESULT - record one in D:\final\documentation\implementation-guide\dark-factory-unification\dfu-done-manual.json under this exact name
   . clean checkout of 'refactor/ai-stack-cleanup': git -c core.longpaths=true clone --quiet --shared --branch refactor/ai-stack-cleanup --single-branch D:\final C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92 (exit 0)
   . clean checkout submodules: git submodule update --init --recursive (exit 0) Cloning into 'C:/Users/yamao/AppData/Local/Temp/dfu-done-clean-cb5fcc92/OB1'... done.Submodule path 'OB1': checked out 'b604d555f37bf79b14d6e5d0db73dec023305917'
   . U0 section 2 Validated by: Each item's own anchor + tester; inbox: a kill-the-poller drill proves no message is lost
   . U1 section 2 Validated by: The memory-plane plan's own per-phase gates (already written, file/line-grounded)
   . U2 section 2 Validated by: Gym: one goal driven from a git issue through sweep→plan→weekly thread→approve→land on each target; a deliberately overlapping issue pair must be flagged by the synthesis; schema cross-reader test
   . U3 section 2 Validated by: Gym: a seeded regression must be caught by a check born from a *tester* finding in a prior round (gym-007's shape, new source); drills green in both systems
   . U4 section 2 Validated by: Gym: same anchored item run per quadrant (runner × target), outcomes compared; stall→oracle observed firing at least once
   . U5 section 2 Validated by: Adversarial drill: an agent instructed to bypass hooks / reach personal-plane data is mechanically stopped and the attempt is visible in an audit record
   . U6 section 2 Validated by: Gym: an unattended run that hits each andon condition halts-and-raises; one that hits none lands with a complete audit trail
   . U6: at least one recorded check went RED in the clean checkout
   . U8 section 2 Validated by: Each H-item's own runnable check in §C.9; and `dfu-done.ps1`'s pinned phase floor + clause 1 EXTENDED to include U8, so U8's columns re-run green from a clean checkout like any other phase

CLAUSE 2 [UNMET] No phase is parked, and every amendment chain is reconstructable and accounted for
   coverage: evaluated 9 of 10 phases whose Validated-by chain must be reconstructable - the pinned floor (C.8.1's U0-U6 plus C.9's U8) unioned with section 2's table - plus the floor's own drift check
   [fail] no-outstanding-parked (exit 1)
        $ split D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md on '^## ' headings; for each PARKED heading look for a LATER section carrying '**Un-parks:** <that heading>'
        1 of 2 PARKED entry/entries are outstanding - no later entry carries an 'Un-parks:' directive citing them: 2026-08-30 · U3 · CORRECTION — code-complete, VALIDATION-PARKED (not "complete")
   [pass] amendment-A1-accounted (exit 0)
        $ parse section 2.1 block A1 in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md
        carries a checkable evidence citation and a revert path (cites path=True sha=False file:line=True)
   [fail] amendment-A3-accounted (exit 1)
        $ parse section 2.1 block A3 in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md
        amendment is missing: Revert path
   [pass] amendment-A2-accounted (exit 0)
        $ parse section 2.1 block A2 in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md
        carries a checkable evidence citation and a revert path (cites path=True sha=False file:line=False)
   [pass] phase-table-unambiguous (exit 0)
        $ parse section 2's table in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md: anchor on the section heading, strip fenced blocks and HTML comments, read the Validated-by column BY NAME from the header row, refuse on a duplicate id
        section 2's table parsed to ONE unambiguous table: header [Phase | What | Validated by | Depends on], 9 phase row(s), no duplicate id, nothing read from a code fence or an HTML comment; 1 id cell(s) were ADMITTED carrying a parenthesised qualifier - the qualifier is treated as part of the id cell's own name, and it is named here so an id carrying STATUS is visible even when it was accepted: U7: **U7 (standing)**
   [fail] phase-floor-matches-plan (exit 1)
        $ extract the phase ids C.8 clause 1 names in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md and compare them with the pinned floor
        the pinned floor and C.8 clause 1 disagree - the plan names U0,U1,U2,U3,U4,U5,U6; pinned but unnamed: U8; named but unpinned: none
   [pass] phase-floor-present (exit 0)
        $ read section 2's table and compare it with the pinned floor U0,U1,U2,U3,U4,U5,U6,U8
        every floor phase (U0,U1,U2,U3,U4,U5,U6,U8) is present in section 2's table
   [pass] chain-U0-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U0, requirement by requirement
        2 of 2 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U1-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U1, requirement by requirement
        1 of 1 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U2-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U2, requirement by requirement
        3 of 3 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U3-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U3, requirement by requirement
        2 of 2 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U4-declined-rows (exit 0)
        $ list the rows naming U4 that section 2's table DECLINED to read as id cells across 23 PLAN.md revision(s), and check U4 still had a real row at each
        1 row(s) naming U4 were declined as id cells in the history, and U4 has a real row at every one of those revisions, so no chain step was lost: 2151193: '**U4 status (2026-08-30)**' [the phase also has a real id-cell row at this revision - the step is intact]
   [pass] chain-U4-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U4, requirement by requirement
        2 of 2 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U5-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U5, requirement by requirement
        1 of 1 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U6-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U6, requirement by requirement
        2 of 2 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U7-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-29 451ebfa) vs CURRENT(2026-08-29 451ebfa) for U7, requirement by requirement
        1 of 1 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   [pass] chain-U8-original-vs-current (exit 0)
        $ compare ORIGINAL(2026-08-31 fe6c7fb) vs CURRENT(2026-08-31 fe6c7fb) for U8, requirement by requirement
        2 of 2 ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; 0 addition(s), which never fail this clause
   . UN-PARKED: '2026-08-30 · U4 · PARKED — the runner axis is unmeetable until little-coder can complete an item' cited by '2026-08-31 - U4 - UN-PARK'
   . chain U0: 1 distinct state(s)
   .     2026-08-29 451ebfa : Each item's own anchor + tester; inbox: a kill-the-poller drill proves no message is lost
   .     U0 CARRIED : each item's own anchor + tester
   .     U0 CARRIED : inbox: a kill-the-poller drill proves no message is lost
   . chain U1: 1 distinct state(s)
   .     2026-08-29 451ebfa : The memory-plane plan's own per-phase gates (already written, file/line-grounded)
   .     U1 CARRIED : the memory-plane plan's own per-phase gates (already written, file/line-grounded)
   . chain U2: 1 distinct state(s)
   .     2026-08-29 451ebfa : Gym: one goal driven from a git issue through sweep→plan→weekly thread→approve→land on each target; a deliberately overlapping issue pair must be flagged by the synthesis; schema cross-reader test
   .     U2 CARRIED : gym: one goal driven from a git issue through sweep→plan→weekly thread→approve→land on each target
   .     U2 CARRIED : a deliberately overlapping issue pair must be flagged by the synthesis
   .     U2 CARRIED : schema cross-reader test
   . chain U3: 1 distinct state(s)
   .     2026-08-29 451ebfa : Gym: a seeded regression must be caught by a check born from a *tester* finding in a prior round (gym-007's shape, new source); drills green in both systems
   .     U3 CARRIED : gym: a seeded regression must be caught by a check born from a tester finding in a prior round (gym-007's shape, new source)
   .     U3 CARRIED : drills green in both systems
   . chain U4: 1 distinct state(s)
   .     2026-08-29 451ebfa : Gym: same anchored item run per quadrant (runner × target), outcomes compared; stall→oracle observed firing at least once
   .     U4 DECLINED ROW : 2151193: '**U4 status (2026-08-30)**' [the phase also has a real id-cell row at this revision - the step is intact]
   .     U4 CARRIED : gym: same anchored item run per quadrant (runner × target), outcomes compared
   .     U4 CARRIED : stall→oracle observed firing at least once
   . chain U5: 1 distinct state(s)
   .     2026-08-29 451ebfa : Adversarial drill: an agent instructed to bypass hooks / reach personal-plane data is mechanically stopped and the attempt is visible in an audit record
   .     U5 CARRIED : adversarial drill: an agent instructed to bypass hooks / reach personal-plane data is mechanically stopped and the attempt is visible in an audit record
   . chain U6: 1 distinct state(s)
   .     2026-08-29 451ebfa : Gym: an unattended run that hits each andon condition halts-and-raises; one that hits none lands with a complete audit trail
   .     U6 CARRIED : gym: an unattended run that hits each andon condition halts-and-raises
   .     U6 CARRIED : one that hits none lands with a complete audit trail
   . chain U7: 1 distinct state(s)
   .     2026-08-29 451ebfa : The evidence ledger itself: every design change carries its anchor citation or its ledger amendment
   .     U7 CARRIED : the evidence ledger itself: every design change carries its anchor citation or its ledger amendment
   . chain U8: 1 distinct state(s)
   .     2026-08-31 fe6c7fb : Each H-item's own runnable check in §C.9; and `dfu-done.ps1`'s pinned phase floor + clause 1 EXTENDED to include U8, so U8's columns re-run green from a clean checkout like any other phase
   .     U8 CARRIED : each h-item's own runnable check in §c.9
   .     U8 CARRIED : and dfu-done.ps1's pinned phase floor + clause 1 extended to include u8, so u8's columns re-run green from a clean checkout like any other phase

CLAUSE 3 [UNEVALUATED] The personal-plane constraint is lifted by VALIDATION, never by emptiness
   coverage: evaluated 2 of 14 the doors C.8 clause 3 names, the corpus predicate, the PostgREST surface derived from the live schema, and the door set itself
   NOT evaluated: postgrest-thoughts, postgrest-agent-memories, postgrest-thought-entities, postgrest-derived-queue, wiki-compiler-output, openbrain-mcp-door, cloud-search-thoughts, mcp-read-tools, corpus-predicate-fail-closed, corpus-backfill-landed, fixture-write-landed, postgrest-surface-sweep
   [pass] door-set-matches-plan (exit 0)
        $ extract backticked identifiers from C.8 clause 3 in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md and compare with this clause's subjects
        every one of the 10 door/predicate name(s) C.8 clause 3 writes is claimed by a subject of this clause
   [pass] corpus-predicate-source-on-work-line (exit 0)
        $ git ls-tree refactor/ai-stack-cleanup OB1 ; git -C OB1 cat-file -e <pin>:docker/init-agent-memory-corpus-failclosed.sql
        docker/init-agent-memory-corpus-failclosed.sql is in the OB1 tree the work line pins (b604d55)
   [indeterminate] door-postgrest-thoughts (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-postgrest-agent-memories (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-postgrest-thought-entities (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-postgrest-derived-queue (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-wiki-compiler-output (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-openbrain-mcp-door (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-cloud-search-thoughts (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] door-mcp-read-tools (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed
   [indeterminate] corpus-predicate-fail-closed (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed
   [indeterminate] corpus-backfill-landed (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed
   [indeterminate] fixture-write-landed (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed
   [indeterminate] postgrest-surface-sweep (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive was passed

CLAUSE 4 [UNEVALUATED] Nothing is left in flight, and everything is DEPLOYED AND RUNNING
   coverage: evaluated 7 of 10 in-flight checks, the services this plan adds, and the service floor's own drift check
   NOT evaluated: work-branches, ops-gateway, rls-boundary
   [pass] service-set-matches-plan (exit 0)
        $ read C.8 clause 4's own enumeration of services from D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md and compare it with the pinned set
        all 4 service(s) C.8 clause 4 enumerates are claimed by a subject of this clause: the ops gateway | the andon board | the gate profiles | the RLS boundary at every stage including the direct clients
   [indeterminate] no-unmerged-work-branches (exit n/a)
        $ git for-each-ref refs/heads/work/
        could not enumerate work branches
   [pass] no-worktrees (exit 0)
        $ git worktree list --porcelain
        no worktrees beyond the main checkout
   [pass] clean-repo (exit 0)
        $ git status --porcelain
        working tree clean
   [pass] clean-submodules (exit 0)
        $ git submodule status --recursive
        every submodule is at its recorded commit
   [pass] gitlink-reachable-on-remote (exit 0)
        $ git ls-tree refactor/ai-stack-cleanup OB1 ; git -C OB1 ls-remote origin ; then, in an EMPTY scratch repo, git fetch --depth=1 <ob1-remote> <pinned-sha>
        the pinned OB1 commit b604d55 is a ref tip on the remote - matched in the SHA COLUMN of 57 advertised ref(s), not anywhere in the output
   [indeterminate] service-ops-gateway (exit n/a)
        $ docker ps --filter name=openbrain-ops-gateway --format {{.Names}}
        -SkipLive
   [pass] service-andon-board (exit 0)
        $ powershell -NoProfile -File D:\final\scripts\agent-harness\andon.ps1 -List
        the board lists its conditions and exits 0
   [pass] service-gate-profiles (exit 0)
        $ read gate_profiles from D:\final\scripts\agent-harness\harness.config.json
        both gate profiles are declared: _comment,attended,dark
   [indeterminate] service-rls-boundary (exit n/a)
        $ psql: relrowsecurity/relforcerowsecurity for the corpus tables AND every table with a foreign key into them ; docker network/inspect -> the DB role every direct client connects as -> pg_roles.rolsuper/rolbypassrls ; git ls-tree refactor/ai-stack-cleanup OB1 -> submodule cat-file
        -SkipLive

CLAUSE 5 [UNMET] The walkthrough is true - every row names a check and that check re-runs green
   coverage: evaluated 2 of 10 phases - the pinned floor (C.8.1's U0-U6 plus C.9's U8) unioned with WALKTHROUGH.md's own sections - each of which must name a check that re-runs green, plus the floor's own drift check
   NOT evaluated: U0, U1, U3, U4, U5, U7, U8
   [fail] phase-floor-matches-plan (exit 1)
        $ extract the phase ids C.8 clause 1 names in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md and compare them with the pinned floor
        the pinned floor and C.8 clause 1 disagree - the plan names U0,U1,U2,U3,U4,U5,U6; pinned but unnamed: U8; named but unpinned: none
   [pass] phase-floor-present (exit 0)
        $ read WALKTHROUGH.md's phase sections and compare it with the pinned floor U0,U1,U2,U3,U4,U5,U6,U8
        every floor phase (U0,U1,U2,U3,U4,U5,U6,U8) is present in WALKTHROUGH.md's phase sections
   [indeterminate] walkthrough-U0-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U0 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row
   [indeterminate] walkthrough-U1-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U1 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row
   [pass] walkthrough-U2-check-1 (exit 0)
        $ python -m pytest scripts/agent-harness/test_harness_config.py scripts/agent-harness/test_anchor_schema.py -q
        the row's named check re-runs green
   [pass] walkthrough-U2-left-the-audited-tree-unchanged (exit 0)
        $ fingerprint the plan, the ledger, the walkthrough, documentation/notes, git refs/status/worktrees/submodules before and after each of U2's 1 command(s)
        nothing this row's 1 command(s) did moved the plan, the ledger, the walkthrough, documentation/notes or git's refs, status, worktrees or submodules
   [indeterminate] walkthrough-U3-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U3 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row
   [indeterminate] walkthrough-U4-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U4 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row
   [indeterminate] walkthrough-U5-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U5 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row
   [pass] walkthrough-U6-check-1 (exit 0)
        $ python scripts/checks/recall-falsifiability-drill.py
        the row's named check re-runs green
   [fail] walkthrough-U6-check-2 (exit 1)
        $ agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q
        exited 1 in the clean checkout: operable program or batch file.
   [pass] walkthrough-U6-left-the-audited-tree-unchanged (exit 0)
        $ fingerprint the plan, the ledger, the walkthrough, documentation/notes, git refs/status/worktrees/submodules before and after each of U6's 2 command(s)
        nothing this row's 2 command(s) did moved the plan, the ledger, the walkthrough, documentation/notes or git's refs, status, worktrees or submodules
   [indeterminate] walkthrough-U7-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U7 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row
   [indeterminate] walkthrough-U8-names-a-check (exit n/a)
        $ (none - no 'How to run' recorded for U8 in D:\final\documentation\implementation-guide\dark-factory-unification\WALKTHROUGH.md)
        this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row

CLAUSE 6 [MANUAL-PENDING] U7 is ARMED - its loop has run one full cycle on the record
   coverage: evaluated 1 of 1 one complete U7 cycle on the record
   [pass] u7-cycle-recorded (exit 0)
        $ grep '^## ' D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md | grep U7, then check for adopted/refused
        1 U7 cycle entry/entries on the record: 2026-08-31 · U7 · A2 IS a complete cycle by clause 6's enumeration — cited, not manufactured
   [MANUAL:PENDING] u7-cycle-judged-against-pinned-anchor
        Confirm the recorded U7 cycle was judged against a PINNED section 0/B research anchor, and that the entry carries the anchor citation or the ledger amendment.
        NO RECORDED RESULT - record one in D:\final\documentation\implementation-guide\dark-factory-unification\dfu-done-manual.json under this exact name

CLAUSE 7 [UNMET] The audit trail is complete
   coverage: evaluated 9 of 10 phases - the pinned floor (C.8.1's U0-U6 plus C.9's U8) unioned with section 2's table - each needing a ledger entry, a findings note AND a commit message, plus the floor's own drift check
   [pass] phase-table-unambiguous (exit 0)
        $ parse section 2's table in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md: anchor on the section heading, strip fenced blocks and HTML comments, read the Validated-by column BY NAME from the header row, refuse on a duplicate id
        section 2's table parsed to ONE unambiguous table: header [Phase | What | Validated by | Depends on], 9 phase row(s), no duplicate id, nothing read from a code fence or an HTML comment; 1 id cell(s) were ADMITTED carrying a parenthesised qualifier - the qualifier is treated as part of the id cell's own name, and it is named here so an id carrying STATUS is visible even when it was accepted: U7: **U7 (standing)**
   [fail] phase-floor-matches-plan (exit 1)
        $ extract the phase ids C.8 clause 1 names in D:\final\documentation\implementation-guide\dark-factory-unification\PLAN.md and compare them with the pinned floor
        the pinned floor and C.8 clause 1 disagree - the plan names U0,U1,U2,U3,U4,U5,U6; pinned but unnamed: U8; named but unpinned: none
   [pass] phase-floor-present (exit 0)
        $ read section 2's table and compare it with the pinned floor U0,U1,U2,U3,U4,U5,U6,U8
        every floor phase (U0,U1,U2,U3,U4,U5,U6,U8) is present in section 2's table
   [fail] audit-trail-U0 (exit 2)
        $ '^## .*U0' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U0 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U0 and one of its own checks, excluding commits that touch only the done-authority
        no findings note and this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check' for U0
   [fail] audit-trail-U1 (exit 1)
        $ '^## .*U1' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U1 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U1 and one of its own checks, excluding commits that touch only the done-authority
        this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check' for U1
   [fail] audit-trail-U2 (exit 1)
        $ '^## .*U2' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U2 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U2 and one of its own checks, excluding commits that touch only the done-authority
        no commit message on the work line carries a validation claim naming the phase AND one of the checks this phase names (test_anchor_schema.py, test_harness_config.py) in the SAME statement - the shape is a directive line, e.g. 'Validated: U2 ... by test_anchor_schema.py' (3 commit(s) co-mention both without claiming one validated the other) for U2
   [fail] audit-trail-U3 (exit 1)
        $ '^## .*U3' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U3 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U3 and one of its own checks, excluding commits that touch only the done-authority
        this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check' for U3
   [fail] audit-trail-U4 (exit 1)
        $ '^## .*U4' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U4 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U4 and one of its own checks, excluding commits that touch only the done-authority
        this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check' for U4
   [fail] audit-trail-U5 (exit 1)
        $ '^## .*U5' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U5 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U5 and one of its own checks, excluding commits that touch only the done-authority
        this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check' for U5
   [fail] audit-trail-U6 (exit 1)
        $ '^## .*U6' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U6 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U6 and one of its own checks, excluding commits that touch only the done-authority
        no commit message on the work line carries a validation claim naming the phase AND one of the checks this phase names (recall-falsifiability-drill.py, test_recall_seams.py) in the SAME statement - the shape is a directive line, e.g. 'Validated: U6 ... by recall-falsifiability-drill.py' (3 commit(s) co-mention both without claiming one validated the other) for U6
   [fail] audit-trail-U7 (exit 2)
        $ '^## .*U7' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U7 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U7 and one of its own checks, excluding commits that touch only the done-authority
        no findings note and this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check' for U7
   [fail] audit-trail-U8 (exit 1)
        $ '^## .*U8' in D:\final\documentation\implementation-guide\dark-factory-unification\DECISIONS.md ; a note in D:\final\documentation\notes whose FILENAME or a HEADING names U8 ; and a commit on refactor/ai-stack-cleanup whose message carries a Validated/Verified claim naming U8 and one of its own checks, excluding commits that touch only the done-authority
        no commit message on the work line carries a validation claim naming the phase AND one of the checks this phase names (dfu-done.ps1) in the SAME statement - the shape is a directive line, e.g. 'Validated: U8 ... by dfu-done.ps1' (15 commit(s) co-mention both without claiming one validated the other) for U8
   .     U2: 3 commit(s) mention the phase AND one of its checks, but in different statements - a co-mention is not a claim that THIS check validated THIS phase
   .     U6: 3 commit(s) mention the phase AND one of its checks, but in different statements - a co-mention is not a claim that THIS check validated THIS phase
   .     U8: 15 commit(s) mention the phase AND one of its checks, but in different statements - a co-mention is not a claim that THIS check validated THIS phase

CLAUSE 8 [UNEVALUATED] THE MEMORY PLANE COMPOUNDS - a recall demonstrably informed a later effort
   coverage: evaluated 0 of 3 the write half, the recall half, and the consumer link
   NOT evaluated: plane-written-to, recall-returned-something, recall-informed-a-later-effort
   [indeterminate] plane-written-to (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive - the plane was not measured
   [indeterminate] recall-returned-something (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive - the plane was not measured
   [indeterminate] recall-informed-a-later-effort (exit n/a)
        $ (not run: -SkipLive)
        -SkipLive - the plane was not measured

-------------------------------------------------------------------------
 COMMANDS THIS AUTHORITY EXECUTED (taken from WALKTHROUGH.md, run in a clone)
   clause 1 / U2 (exit 0)
        $ python -m pytest scripts/agent-harness/test_harness_config.py scripts/agent-harness/test_anchor_schema.py -q
        in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92
   clause 1 / U6 (exit 0)
        $ python scripts/checks/recall-falsifiability-drill.py
        in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92
   clause 1 / U6 (exit 1)
        $ agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q
        in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92
   clause 5 / U2 (exit 0)
        $ python -m pytest scripts/agent-harness/test_harness_config.py scripts/agent-harness/test_anchor_schema.py -q
        in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92
   clause 5 / U6 (exit 0)
        $ python scripts/checks/recall-falsifiability-drill.py
        in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92
   clause 5 / U6 (exit 1)
        $ agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q
        in C:\Users\yamao\AppData\Local\Temp\dfu-done-clean-cb5fcc92

   platform: Microsoft Windows NT 10.0.26200.0 / PowerShell 5.1.26100.8875
   containment: write-lock APPLIED per command, plus the pre-run snapshot and the before/after fingerprint
   snapshot of every artifact a clause reads was taken at 2026-09-01T05:17:45-04:00, BEFORE the first command
   INTEGRITY: the audited tree is byte-identical before and after this run.

-------------------------------------------------------------------------
 CENSUS (every clause in exactly one bucket; the buckets must sum)
   unrecognised     0
   unmet            4
   unevaluated      3
   manual_pending   1
   met              0
   total 8 for 8 clause(s) - balances: True

 NOT DONE - board: FAILED
   - 4 clause(s) in the 'unmet' bucket: clause 1, clause 2, clause 5, clause 7
   - 3 clause(s) in the 'unevaluated' bucket: clause 3, clause 4, clause 8
   - 1 clause(s) in the 'manual_pending' bucket: clause 6

 This is a REPORT, not a redefinition (C.8). Amending a plan column so this
 script goes green is the one move that section exists to forbid.

