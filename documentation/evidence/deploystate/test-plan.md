# Test plan - `deploystate`

Branch `work/deploystate`, worktree `wt-deploystate`, base `refactor/ai-stack-cleanup`
(`49bb2db`, the passplan merge, at branch time). Anchor: `queue.ps1 -Show -Id deploystate`.

Changed: `scripts/agent-harness/queue.ps1` (`-Deployed` verb, `-Surface`, the deploy-surface
derivation at `-Merged`, commit/OB1-pin resolution in `-List`/`-Show`, the hand-off flag on
terminal states only, the attempt bump after an anchor amendment, the unterminated-fence
warning, the BOM-free evidence spill), `verify-queue-defects.ps1` (D12-D17),
`verify-merge-protocol.ps1` (two deploy checks + `Get-QueueBoard`), `scope_node.py` /
`test_scope_node.py` (`deployed` maps to `done`), `gate-audit.ps1` (`deployed` crossed
`pre_review`), `MERGE-PROTOCOL.md` (step 6, the §4 row, the §6 limit, three evidence-shape
edges), `scripts/agent-harness/README.md` (three table rows, the typical session, a new
section), `documentation/notes/deploy-gate-2026-09-06.md` (findings sink), this plan.

**Attempt 3** (this revision) is documentation and plan only - `queue.ps1`'s behaviour is
deliberately untouched. It rewrites the sink's line-ending sentence to something reproducible
at the tip, makes `queue.ps1`, `verify-queue-defects.ps1` and the README agree with the sink
on the hand-off count, stops quoting a timing range as though it were a bound, widens T12's
enumerator and declares what it still cannot see, and adds **T13**, which asks the question
attempt 2's tester had to be told to ask: does a line-ending repair carry anything besides
the repair?

**Attempt 2** additionally: the paste rule is manifest-driven, the
`-Deployed` pin must be hex, the Dockerfile probe no longer prints git's `fatal:`,
`queue.ps1`'s line endings were repaired, and three false or stale claims in the docs and
the sink were corrected. T1 gains a sixth replay, T2 gains the epoch refusal, and **T12 is
new**: it checks the findings sink, which attempt 1 had no case for - which is exactly how
the false claim reached a tester.

**You are testing whether a merged item that shipped an image or a paste can still read as
finished, whether a row naming commits that exist nowhere can still read as live work, and
whether anything else about the pipeline moved.** No containers, no images, no leases, no
deploys - everything below is git, PowerShell 5.1 and files under `$env:TEMP`.

## READ THIS FIRST

- **Never drive the live queue.** Every case that MUTATES runs `queue.ps1` with
  `AI_STACK_WORKTREE_STATE` pointing at a scratch directory - the same redirect
  `verify-queue-defects.ps1` uses. T3 and T4 are the only cases that touch the live queue and
  they are `-List` / `-Show` only, which T4 proves write nothing.
- **Refusal text comes from `Die`, which prints through `Write-Host`.** An in-process
  `& $QP ... | Tee-Object` or `| Out-String` captures NOTHING from it - passplan's tester lost
  a case to exactly that, and this item's own `verify-merge-protocol.ps1` check first read
  FAIL against a clean row for the same reason. Every assertion on printed text below uses
  the `Q` helper, which runs a CHILD `powershell.exe` and reads its stdout.
- **No case prints a secret.** Nothing here reads `.env*`, `secrets/`, a queue evidence file
  of another item, or any container environment. The only shas printed are git object ids and
  the fixtures' own fake pins.
- The rule under test, stated once: at `-Merged` the deploy surfaces are read off
  `git diff --name-only <first parent>..<merge sha>` and never off the item; `-Deployed`
  closes them one or all at a time, refusing evidence that does not name, per surface, a pin
  AND a health state; `-List`/`-Show` resolve every recorded commit and its OB1 pin and are
  read-only.

## Environment

    $WT   = "<path to wt-deploystate>"
    $QP   = "$WT\scripts\agent-harness\queue.ps1"
    $LIVE = Join-Path ((git -C $WT rev-parse --path-format=absolute --git-common-dir).Trim()) "agent-worktrees\queue"
    $TMP  = Join-Path $env:TEMP ("deploystate-test-" + $PID)
    New-Item -ItemType Directory -Force -Path $TMP | Out-Null
    function Q { param([Parameter(ValueFromRemainingArguments = $true)][string[]]$a)
        $o = (& powershell.exe -NoProfile -NonInteractive -File $QP @a 2>&1 | Out-String); $global:QExit = $LASTEXITCODE; $o }

`Q` returns the whole child output as one string and leaves its exit code in `$QExit`.
Compare `$QExit`, never `$LASTEXITCODE`. Run every case from `$WT` (`Set-Location $WT`)
unless the case says otherwise.

---

## T0 - the tree under test is the tree the plan describes

    git -C $WT rev-parse --abbrev-ref HEAD
    git -C $WT log --oneline -1
    git -C $WT status --short

PASS: the branch is `work/deploystate`, and `status --short` lists NOTHING under
`scripts/agent-harness/`, `documentation/` (a stray untracked scratch file elsewhere is not a
failure - say so if you see one). FAIL: the tree is dirty in the files this plan tests, so
what you measure is not what would land.

    powershell -NoProfile -Command "$e=$null;[void][System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw '$WT\scripts\agent-harness\queue.ps1'),[ref]$e); $e.Count"

PASS: prints `0` - `queue.ps1` parses. FAIL: any other number.

---

## T1 - the surfaces are DERIVED from real merges, and the manifest says what is pasteable

This replays merges that already happened on this line through the derivation, in a hermetic
state dir. It never touches the live queue and it deploys nothing. Save as
`$TMP\replay.ps1` and run it:

    param([string]$Wt)
    $ErrorActionPreference = "Continue"
    $state = Join-Path $env:TEMP ("deploystate-replay-" + $PID)
    if (Test-Path $state) { Remove-Item -Recurse -Force $state }
    New-Item -ItemType Directory -Force -Path (Join-Path $state "queue") | Out-Null
    (@{ worktrees = @{ "wt-deploystate" = @{ id = "wt-deploystate"; path = $Wt; branch = "work/deploystate" } } } | ConvertTo-Json -Depth 6) |
        Set-Content -Path (Join-Path $state "worktrees.json") -Encoding ASCII
    $env:AI_STACK_WORKTREE_STATE = $state
    $queue = Join-Path $Wt "scripts\agent-harness\queue.ps1"
    Set-Location $Wt
    $cases = @(
        @{ id = "rp-gate5d";        sha = "5b797314b15e215bb21a65cd5afea08a73b9414c" },
        @{ id = "rp-curatorimg";    sha = "e72c678499793a4ee3c1d9eb5e6d5b6ed1680320" },
        @{ id = "rp-curator2";      sha = "08c4ae1a71801319846ecbb7d167cf2d12008eb4" },
        @{ id = "rp-opsdoor";       sha = "b06057f0bf564f28b5c59f7d537f451a620b2b6a" },
        @{ id = "rp-researchretry"; sha = "76145568064df9d590d3acd883fc78d207ce0224" },
        @{ id = "rp-owuidrift";     sha = "e98926562e190b7adb0472ca08e1314d1e7e74d2" }
    )
    foreach ($c in $cases) {
        $parents = @((& git.exe rev-list --parents -n 1 $c.sha) -split '\s+' | Where-Object { $_ })
        $item = [ordered]@{
            id = $c.id; branch = "work/replay"; line = "development"; developer = "wt-deploystate"
            state = "reviewing"; test_plan = ""; thread = ""; attempt = 1; line_mergeable = $true
            submitted_sha = ""; tested_at_sha = $parents[2]; merged_sha = ""
            results = @(); history = @()
        }
        [System.IO.File]::WriteAllText((Join-Path $state ("queue\" + $c.id + ".json")),
            ($item | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
        (@{ by = "qrev"; at = 0 } | ConvertTo-Json) |
            Set-Content -Path (Join-Path $state ("queue\" + $c.id + ".reviewer.claim")) -Encoding ASCII
        Write-Host ("===== " + $c.id + "  merge " + $c.sha.Substring(0,7) + "  (first parent " + $parents[1].Substring(0,7) + ") =====")
        & powershell -NoProfile -NonInteractive -File $queue -Merged -Id $c.id -By qrev -Sha $c.sha -FitsCodebase
        $it = Get-Content -Raw -Path (Join-Path $state ("queue\" + $c.id + ".json")) -Encoding UTF8 | ConvertFrom-Json
        Write-Host ("DERIVED: [" + ((@($it.deploy_surfaces) | Where-Object { $_ }) -join ", ") + "]")
    }
    Write-Host ("state dir (hermetic): " + $state)

    powershell -NoProfile -NonInteractive -File "$TMP\replay.ps1" -Wt $WT

PASS: exactly these six DERIVED lines - the anchor's three merges, the two that landed on the
line after the anchor was written, and the `owui/`-metadata merge attempt 1's tester found -

    rp-gate5d        DERIVED: []
    rp-curatorimg    DERIVED: [image:openbrain-curator]
    rp-curator2      DERIVED: [image:openbrain-curator, image:openbrain-research, paste:owui/tools/deep_research.py]
    rp-opsdoor       DERIVED: [image:openbrain-curator, image:openbrain-research]
    rp-researchretry DERIVED: [image:openbrain-research]
    rp-owuidrift     DERIVED: []

and `rp-owuidrift` must additionally print, as NOTEs rather than surfaces:

    NOTE: owui/README.md changed but owui/manifest.csv does not list it - it is not pasted into OWUI, so it is not a deploy surface
    NOTE: owui/manifest.csv changed but owui/manifest.csv does not list it - it is not pasted into OWUI, so it is not a deploy surface

FAIL: any list differs. In particular `rp-curatorimg` must NOT contain
`image:openbrain-research` and must not contain a `paste:`; `rp-gate5d` must be empty; and
`rp-owuidrift` must derive NOTHING - it is the merge that, under the path-based rule this
item started with, derived `paste:owui/manifest.csv` and `paste:owui/README.md`, two surfaces
nobody could ever close honestly. `rp-curator2` must still derive its paste, because
`owui/tools/deep_research.py` IS listed in the manifest at that tree - so the case
discriminates the rule rather than just switching pastes off:

    git -C $WT show 08c4ae1:owui/manifest.csv | Select-String "tools/deep_research.py"
    git -C $WT show e989265:owui/manifest.csv | Select-String "README"

PASS: the first prints a row, the second prints nothing.

No `fatal:` line may appear anywhere in the six replays' output. `-Merged` used to probe for
an OB1 Dockerfile with `git cat-file -e`, whose stderr printed
`fatal: path ... does not exist` ABOVE a successful merge record. FAIL: any `fatal:` in a run
that exits 0.

Confirm two of them independently, so the derivation is not just agreeing with itself:

    git -C $WT diff-tree -r --name-only --no-commit-id 5b79731 e72c678
    git -C $WT diff-tree -r --name-only --no-commit-id 49bb2db b06057f
    $old = (git -C $WT rev-parse 49bb2db:OB1).Trim(); $new = (git -C $WT rev-parse b06057f:OB1).Trim()
    git -C "$WT\OB1" diff-tree -r --name-only --no-commit-id $old $new | Select-String "^integrations/"

PASS: the curatorimg range contains `OB1` and no `owui/` path; the opsdoor range contains
`OB1`; the OB1 diff for opsdoor touches `integrations/research-curator/` and
`integrations/research-service/` - which are the two compose services the tool named
(`openbrain-curator`, `openbrain-research` in `OB1/docker/docker-compose.yml`). FAIL:
the tool named a service the OB1 diff does not support.

---

## T2 - `-Deployed` refuses evidence that does not say what is running and that it is healthy

Hermetic; uses T1's replayed `rp-curatorimg` (one surface, `image:openbrain-curator`).

    $ST = "<the 'state dir (hermetic)' path T1 printed>"
    $env:AI_STACK_WORKTREE_STATE = $ST
    Set-Location $WT
    Set-Content -Path "$TMP\ev-nohealth.md" -Encoding ascii -Value "openbrain-curator: label org.opencontainers.image.revision=d89c126d, RestartCount=0"
    Set-Content -Path "$TMP\ev-nopin.md"    -Encoding ascii -Value "openbrain-curator: State.Health.Status=healthy, RestartCount=0"
    Set-Content -Path "$TMP\ev-unhealthy.md" -Encoding ascii -Value "openbrain-curator: label org.opencontainers.image.revision=d89c126d, State.Health.Status=unhealthy"
    Set-Content -Path "$TMP\ev-good.md"     -Encoding ascii -Value "openbrain-curator: label org.opencontainers.image.revision=d89c126, State.Health.Status=healthy, RestartCount=0"
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-nohealth.md"; $QExit
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-nopin.md"; $QExit
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-unhealthy.md"; $QExit
    Q -Deployed -Id rp-curatorimg -By auto:dark  -Evidence "$TMP\ev-good.md"; $QExit

PASS, in order: `names no health state` (exit non-zero); `names no pin` (exit non-zero);
`health state 'unhealthy' is not one a deploy closes on` (exit non-zero); the `auto:`
principal refused with exit **4**. FAIL: any of the four is accepted. Each of the FIRST
THREE - the ones that reach surface resolution - must also name the surface
`image:openbrain-curator`; the fourth must not be held to that, because the principal is
refused before any surface is looked at, and a clause a correct tool trips is a clause that
teaches testers to read past the plan (attempt 1's tester raised exactly this).

Then the pin, which must be HEX - attempt 1's tester closed a real surface with a decimal
epoch:

    Set-Content -Path "$TMP\ev-epoch.md" -Encoding ascii -Value "openbrain-curator deployed at 1788720066 State.Status=running"
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-epoch.md"; $QExit
    Set-Content -Path "$TMP\ev-digithex.md" -Encoding ascii -Value "openbrain-curator: label org.opencontainers.image.revision=0a1b2c3"
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-digithex.md"; $QExit

PASS: the epoch is REFUSED with `names no pin` and the refusal says `an all-digit token such
as a timestamp is not a pin`; the second is also refused, but ONLY for `names no health
state` and NOT for `names no pin` - a hex id that merely starts with a digit is still a pin,
and this asserts that without closing the surface the rest of the case needs open. FAIL: the
epoch is accepted, or the digit-leading hex id is reported as no pin.

    (Get-Content -Raw "$ST\queue\rp-curatorimg.json" -Encoding UTF8 | ConvertFrom-Json) |
        Select-Object state, @{n="pending";e={@($_.deploy_pending) -join ","}}, @{n="closed";e={@($_.deployed).Count}}
    Test-Path "$ST\queue\rp-curatorimg.deploy1.evidence.md"

PASS: `state=merged`, `pending=image:openbrain-curator`, `closed=0`, and the evidence file
does NOT exist - four refusals recorded nothing. FAIL: anything was written.

    Q -Deployed -Id rp-curatorimg -By profnovice -Surface image:not-a-surface -Evidence "$TMP\ev-good.md"; $QExit
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-good.md"; $QExit

PASS: the first is refused with `is not an open deploy surface` and lists the open one; the
second is ACCEPTED (exit 0), prints `deploy recorded for image:openbrain-curator` and
`Every derived surface is closed`. FAIL: either behaves otherwise.

    (Get-Content -Raw "$ST\queue\rp-curatorimg.json" -Encoding UTF8 | ConvertFrom-Json) |
        Select-Object state, @{n="pending";e={@($_.deploy_pending).Count}}, @{n="by";e={$_.deployed[0].by}}
    Q -Deployed -Id rp-curatorimg -By profnovice -Evidence "$TMP\ev-good.md"; $QExit
    Q -Show -Id rp-curatorimg

PASS: `state=deployed`, `pending=0`, `by=profnovice`; the repeat is refused with
`already 'deployed'`; `-Show` prints a `--- DEPLOY ---` block reading
`image:openbrain-curator  CLOSED by profnovice`. FAIL: the item can be closed twice.

Now the same on a surface basis, and on an item with nothing to close:

    Q -Deployed -Id rp-curator2 -By profnovice -Surface image:openbrain-curator -Evidence "$TMP\ev-good.md"; $QExit
    Q -List
    Q -Deployed -Id rp-gate5d -By profnovice -Evidence "$TMP\ev-good.md"; $QExit

PASS: the `-Surface` close is accepted, the item STAYS `merged`, and `-List` shows
`rp-curator2 ... [UNDEPLOYED: image:openbrain-research, paste:owui/tools/deep_research.py]`
- the closed surface gone from the flag, the other two still there. `rp-gate5d` is refused
with `records no deploy surface`. FAIL: a partly-deployed item reads `deployed`, or an item
with no surfaces accepts a deploy record.

---

## T3 - the live board (READ-ONLY): the zombie is flagged, the hand-off flag is gone from terminal rows

    Remove-Item Env:\AI_STACK_WORKTREE_STATE -ErrorAction SilentlyContinue
    Set-Location $WT
    $board = Q -List
    $board

PASS: the FIRST data row is
`curatorpool        closed-outside-gates wt-curatorpool        [UNRESOLVABLE: OB1 22f41b6]`
- flagged and sorted ahead of the alphabetically earlier rows. FAIL: it is unflagged, or not
first.

    ($board -split "`n" | Where-Object { $_ -match "\s(merged|rejected|closed-outside-gates|deployed)\s" } | Where-Object { $_ -match "needs hand-off" }).Count
    ($board -split "`n" | Where-Object { $_ -match "needs hand-off" }) | ForEach-Object { $_.TrimEnd() }

PASS: the first count is **`0`** - not one terminal row carries the flag. The second command
prints the rows that DO carry it, and every one of them is in a non-terminal state; when the
developer measured, that was one row, `owuidrift  testing  ... [needs hand-off]`, which is a
genuine hand-off (its line is checked out elsewhere) and is the positive control for the
whole change - the figure of record, counted by attempt 1's tester, is 32 of 42 rows wearing
the flag with 31 of those terminal, so the one true instance was one in thirty-two; the
reconciliation of the three readings taken hours apart is the table in the findings sink, and
T12 checks it. An empty second list is also a pass (no hand-off is
pending right now); D15 in `verify-queue-defects.ps1`, run in T5, is what proves the flag
still appears on a non-terminal row regardless of what the live board happens to hold. FAIL:
the first count is not 0.

Independently confirm the zombie is real rather than a flag the tool invented:

    git -C $WT rev-parse f71772bb7f427e92e4a9e54c796cb7751b31a043:OB1
    git -C "$WT\OB1" cat-file -e 22f41b6a6c31a38feb847ad3e704bea5a5ed3ad5; $LASTEXITCODE

PASS: the pin is `22f41b6a6c31a38feb847ad3e704bea5a5ed3ad5` and `cat-file -e` FAILS
(non-zero) - the OB1 commit that row pins is held by no clone here. FAIL: the object exists,
which would make the flag wrong.

---

## T4 - `-List` and `-Show` mutate nothing, and finish under 5 s on the live board

    $before = @(Get-ChildItem $LIVE -Filter *.json | ForEach-Object { $_.Name + "=" + (Get-FileHash -Algorithm SHA256 $_.FullName).Hash }) -join ";"
    $sw = [Diagnostics.Stopwatch]::StartNew(); $null = Q -List; $sw.Stop(); "list: {0:N2} s" -f $sw.Elapsed.TotalSeconds
    $sw = [Diagnostics.Stopwatch]::StartNew(); $null = Q -Show -Id curatorpool; $sw.Stop(); "show: {0:N2} s" -f $sw.Elapsed.TotalSeconds
    $after = @(Get-ChildItem $LIVE -Filter *.json | ForEach-Object { $_.Name + "=" + (Get-FileHash -Algorithm SHA256 $_.FullName).Hash }) -join ";"
    $before -eq $after
    (Get-ChildItem $LIVE -Filter *.json).Count

PASS: `$before -eq $after` is `True` (every item file byte-identical), the list time is under
5 s and the show time is under 5 s. **Five seconds is the bar; anything tighter is not.**
Observed so far on a 42-row board: 1.19-1.88 s in-process / 2.00-2.27 s around the child
process over three runs each (developer), and 1.02-2.18 / 2.03-2.39 over eight runs each
(attempt 2's tester) - two honest measurers straddling each other at both ends, which is why
neither this case nor the docs quote a narrower range. Record what you saw. FAIL: any file
changed, or either command takes 5 s or more.

    Q -Show -Id curatorpool

PASS: a `--- RESOLUTION ---` block naming `[UNRESOLVABLE: OB1 22f41b6]`. FAIL: no block.

---

## T5 - the defects drill: D1-D17 green, and the count has not shrunk

    Set-Location $WT
    powershell -NoProfile -NonInteractive -File "scripts\agent-harness\verify-queue-defects.ps1" 2>&1 | Tee-Object "$TMP\drill.txt" | Select-Object -Last 5
    ($LASTEXITCODE)
    (Select-String -Path "$TMP\drill.txt" -Pattern "\[FAIL\]").Count

PASS: the last line reads `<N> check(s), 0 failed.` with **N >= 213** (the developer measured
213 at attempt 2, up from 206 at attempt 1 - D12 gained three manifest checks and D13 four
pin checks; the passplan tally this must never fall below was 140), exit code `0`, and zero
`[FAIL]` lines. FAIL: any failure, or N < 213 - a shrinking count means checks were removed,
which is the one way this drill can go green by proving less.

    Select-String -Path "$TMP\drill.txt" -Pattern "^=== D1[2-7] " | ForEach-Object { $_.Line }

PASS: six section headings, D12 through D17, are present. FAIL: a section is missing.

---

## T6 - the merge-protocol drill: its rows pass, and the six pre-existing reds are unchanged

This drill uses the LIVE state dir by design (it drives the real pipeline end to end with
`drill-*` ids and cleans them up). It creates and removes its own worktrees off a scratch
branch and never touches `development`. Expect it to take a few minutes.

    powershell -NoProfile -NonInteractive -File "scripts\agent-harness\verify-merge-protocol.ps1" 2>&1 | Tee-Object "$TMP\mp.txt" | Select-Object -Last 3
    Select-String -Path "$TMP\mp.txt" -Pattern "^  \[FAIL\]" | ForEach-Object { $_.Line }

PASS: the tail reads `66/72 checks passed`, and the `[FAIL]` lines are EXACTLY these six,
no more and no fewer -

    two divergent commits exist
    rebase produced a real conflict
    the tested sha is no longer what would land
    A's intent survived the later merge
    B's adapted intent is present
    two --no-ff merge commits on the line

FAIL: any seventh failure, or a different six. These six are pre-existing and have one
cause: the drill cuts its scratch line from `development`, whose `.githooks/pre-commit`
invokes `./scripts/checks/check-corpus-exposure-producers.ps1`, a file that does not exist on
`development`, so the drill's two synthetic commits are refused and the five checks that
depend on those commits existing cannot pass. Confirm the cause rather than taking it on
trust:

    Select-String -Path "$TMP\mp.txt" -Pattern "check-corpus-exposure-producers" | Select-Object -First 1
    git -C $WT cat-file -e development:scripts/checks/check-corpus-exposure-producers.ps1; $LASTEXITCODE

PASS: the drill log shows `The argument './scripts/checks/check-corpus-exposure-producers.ps1'
... does not exist`, and `cat-file -e` against `development` fails (non-zero). FAIL: the
script IS on `development`, which would mean the six reds have some other cause.

    Select-String -Path "$TMP\mp.txt" -Pattern "deploy_pending \(present\)|no \[needs hand-off\] and no \[UNDEPLOYED\]" | ForEach-Object { $_.Line }

PASS: both new rows read `[PASS]`. FAIL: either is red.

    Set-Location $WT; git status --short; powershell -NoProfile -NonInteractive -File "scripts\agent-harness\queue.ps1" -List | Select-String "^drill-"

PASS: no `drill-*` rows survive in the queue and the worktree is not left dirty - the drill
cleaned up after itself. FAIL: leftovers.

---

## T7 - `-Submit` after `-AmendAnchor` bumps the attempt, so evidence is never overwritten

Covered hermetically by D16 in T5's drill; this case reads the proof rather than re-building
the fixture.

    Select-String -Path "$TMP\drill.txt" -Pattern "^  \[(PASS|FAIL)\] D16" | ForEach-Object { $_.Line }

PASS: five `[PASS] D16 ...` lines, including `-Submit after the amendment BUMPS the attempt to
2 and says so`, `the second pass lands in attempt2's OWN file; attempt1's is byte-identical`,
`both results rows are present, attempts 1 and 2`, and the negative `an amendment with NO
verdict at the current attempt does not bump (still attempt 1)`. FAIL: any is `[FAIL]`, or
the negative case is missing (a bump on every submit would be a different bug).

---

## T8 - an unterminated fence is warned by line, a TAB-indented fence is not a fence, a spill has no BOM

Covered by D17; read the proof, then reproduce the warning by hand so the operator-facing
text is seen as printed:

    Select-String -Path "$TMP\drill.txt" -Pattern "^  \[(PASS|FAIL)\] D17" | ForEach-Object { $_.Line }

PASS: every `D17` line is `[PASS]`, including `a TAB-indented ``` is NOT a fence - three
cases, no WARNING` and `the spilled file has NO BOM (byte0 != EF)`. FAIL: any is red.

    $ST2 = Join-Path $TMP "hermetic-t8"; New-Item -ItemType Directory -Force -Path (Join-Path $ST2 "queue") | Out-Null
    Copy-Item "$LIVE\deploystate.anchor.json" "$ST2\anchor.json"   # a READ of the live queue; this item own anchor, reused
    $env:AI_STACK_WORKTREE_STATE = $ST2
    Set-Content -Path "$TMP\plan-open.md" -Encoding ascii -Value @("# plan", "## T0 - the first", "run x", '```', "## T1 - the second", "## T2 - the third")
    Set-Location $WT
    Q -Propose -Id t8 -Anchor "$ST2\anchor.json" -Developer wt-deploystate | Out-Null; $QExit
    Q -ConfirmAnchor -Id t8 -By profnovice | Out-Null; $QExit
    Q -Submit -Id t8 -Branch work/deploystate -Developer wt-deploystate -TestPlan "$TMP\plan-open.md"; $QExit

PASS: `-Submit` exits 0 AND prints `WARNING: -TestPlan '<path>' opens a code fence at line 4
that is never closed` followed by `Recognised 1 case(s): T0`, and the submit line itself says
`1 case(s) the tester must execute: T0`. FAIL: no warning, or the warning names the wrong
line, or the recognised list disagrees with the enforced list.

    Remove-Item Env:\AI_STACK_WORKTREE_STATE

The three exits are 0, 0, 0 - the anchor gate is satisfied by the copied anchor, and the
fence is a WARNING, not a refusal.

---

## T9 - MUTATION: break the derivation and a NAMED check must go red

The drill is only worth its green if it can go red. `verify-queue-defects.ps1` takes
`-Script <path>` for exactly this. **The mutant needs a copy of the DIRECTORY**, not of the
one file: `queue.ps1` dot-sources `common.ps1`, `anchor.ps1` and `gate-audit.ps1` from its
own `$PSScriptRoot`, and a lone copy in `$TMP` dies on the first line. Nothing below is ever
written inside the repository, and the drill is hermetic - its own scratch repo and state
dir per case - so no mutant can reach the live queue.

    foreach ($n in @("mut1","mut2")) {
        $d = Join-Path $TMP $n; if (Test-Path $d) { Remove-Item -Recurse -Force $d }
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        Get-ChildItem "$WT\scripts\agent-harness" -File | Copy-Item -Destination $d -Force
    }
    $m1 = Join-Path $TMP "mut1\queue.ps1"
    (Get-Content -Raw $m1).Replace('if ($changed -contains "OB1") {', 'if ($false -and ($changed -contains "OB1")) {') | Set-Content -Path $m1 -Encoding UTF8 -NoNewline
    (Select-String -Path $m1 -Pattern 'if \(\$false -and').LineNumber
    powershell -NoProfile -NonInteractive -File "$WT\scripts\agent-harness\verify-queue-defects.ps1" -Script $m1 2>&1 | Tee-Object "$TMP\mutant.txt" | Select-Object -Last 2
    Select-String -Path "$TMP\mutant.txt" -Pattern "^  \[FAIL\]" | ForEach-Object { $_.Line } | Select-Object -First 3

PASS: the mutation lands on exactly ONE line - print it rather than trusting a number, since
any line citation drifts with the next edit (attempt 1's plan said "line 1018", which was
wrong: it was 751 then and 760 now, and 1018 is unrelated andon code). The run ends
`<N> check(s), <M> failed.` with **M >= 1** (15 when the developer ran it at attempt 1), and
the FIRST failure is the D12 check named `deploy_pending is EXACTLY the four surfaces git
saw ...` whose detail reads

    pending=image:rooty,image:thing,paste:owui/tools/deep.py

- the OB1 image `image:openbrain-curatorish` is gone, which is precisely what the mutation
removed. FAIL: `0 failed`, or the D12 surfaces check still passes - either would mean the
derivation is not what D12 checks.

Second mutation, on the other half of the item - the hand-off flag:

    $m2 = Join-Path $TMP "mut2\queue.ps1"
    (Get-Content -Raw $m2).Replace('if (($it.state -notin $TerminalStates) -and ($it.PSObject.Properties.Name -contains "line_mergeable")', 'if (($true) -and ($it.PSObject.Properties.Name -contains "line_mergeable")') | Set-Content -Path $m2 -Encoding UTF8 -NoNewline
    powershell -NoProfile -NonInteractive -File "$WT\scripts\agent-harness\verify-queue-defects.ps1" -Script $m2 2>&1 | Tee-Object "$TMP\mutant2.txt" | Select-Object -Last 2
    Select-String -Path "$TMP\mutant2.txt" -Pattern "^  \[FAIL\]" | ForEach-Object { $_.Line }

PASS: three failures, and they include
`D15: the four TERMINAL states (merged, deployed, rejected, closed-outside-gates) carry NO
[needs hand-off]` with the detail

    flagged=m-ready,m-review,m-testing,t-closed,t-deployed,t-merged,t-rejected

- every terminal fixture now wears the flag. FAIL: D15 still passes with the state test
removed.

    Remove-Item -Recurse -Force (Join-Path $TMP "mut1"), (Join-Path $TMP "mut2")

---

## T10 - the docs describe the verb, the surfaces and the flags, and every command they show exists

    Select-String -Path "$WT\documentation\implementation-guide\multi-agent-concurrency\MERGE-PROTOCOL.md" -Pattern "-Deployed|UNDEPLOYED|UNRESOLVABLE|needs hand-off" | ForEach-Object { "{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }
    Select-String -Path "$WT\scripts\agent-harness\README.md" -Pattern "-Deployed|UNDEPLOYED|UNRESOLVABLE" | ForEach-Object { "{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }

PASS: MERGE-PROTOCOL has a **Step 6** describing what a merge SHIPS (the three derivation
rules as a table), the `-Deployed` command with `-Surface`, the per-surface evidence
requirement, and the two other flags; §4's human table has a row for recording the deploy;
§6's last bullet no longer says the protocol "does not cover deploy verification" and points
at step 6 instead while keeping the deploy human and gated. The README has a
`## What a merge SHIPS` section covering the same three flags in the operator's terms.
FAIL: any of those is missing, or §6 still disclaims deploy verification.

    Select-String -Path "$WT\documentation\implementation-guide\multi-agent-concurrency\MERGE-PROTOCOL.md" -Pattern "does not cover deploy verification"

PASS: no match. FAIL: a match.

**Every command the docs show must exist** - the standing case, and the one that catches a
description written against an imagined tool. Check every flag they name against the
script's own `param()` block, mechanically:

    $params = (Select-String -Path "$WT\scripts\agent-harness\queue.ps1" -Pattern '^\s*\[(switch|string|int)[^\]]*\]\$(\w+)' | ForEach-Object { $_.Matches[0].Groups[2].Value })
    "declared params: " + $params.Count
    $params -contains "Deployed"; $params -contains "Surface"; $params -contains "Evidence"; $params -contains "By"; $params -contains "Id"; $params -contains "Show"
    $verbs = Select-String -Path "$WT\documentation\implementation-guide\multi-agent-concurrency\MERGE-PROTOCOL.md","$WT\scripts\agent-harness\README.md" -Pattern "queue\.ps1 -\w+" -AllMatches | ForEach-Object { $_.Matches } | ForEach-Object { $_.Value } | Sort-Object -Unique
    $verbs
    @($verbs | ForEach-Object { $v = ($_ -split " -")[1]; if ($params -notcontains $v) { "MISSING: $v" } })

PASS: `declared params: 43`, all six `-contains` print `True`, `$verbs` lists 14 distinct
verbs (including `queue.ps1 -Deployed`), and the last command prints NOTHING - every verb
the two documents show is a declared parameter. FAIL: any `MISSING:` line; a document that
shows a flag the script does not have is the failure this case exists for. **Note the
single quotes on the `-Pattern`**: in a double-quoted PowerShell string `$(\w+)` is a
subexpression and the regex is destroyed.

    powershell -NoProfile -NonInteractive -File "$WT\scripts\agent-harness\queue.ps1" -Deployed -Id nope-does-not-exist -By profnovice -Evidence "x"; $LASTEXITCODE

PASS: `ERROR: no queue item 'nope-does-not-exist'` and exit `1` - the switch reaches a
handler, it is not merely declared. (Read-only: that id does not exist, so nothing is
written; this is the one live-queue touch in T10 and it writes nothing.) FAIL:
`pass one of -Submit | ...`, which would mean the verb falls through to the usage line.

---

## T11 - the Python side, and nothing prints a secret

    Set-Location "$WT\scripts\agent-harness"; python -m pytest test_scope_node.py -q; Set-Location $WT

PASS: `25 passed`, including the `("deployed", "done")` parametrisation - the bridge's board
must not read a deployed item as still open. FAIL: any failure or error.

    Select-String -Path "$TMP\drill.txt","$TMP\mp.txt","$TMP\mutant.txt","$TMP\mutant2.txt" -Pattern "(?i)(api[_-]?key|secret|password|token\s*[=:]|BEGIN [A-Z ]*PRIVATE KEY)" | ForEach-Object { $_.Line }

PASS: no output, or only lines that quote this repository's own harness vocabulary
(`LC_DEPLOY_TOKEN` as a bare word in a comment, say) with NO value after it. FAIL: any line
carries a value that looks like a credential - report it to the operator directly and do not
paste it into the evidence file.

---

## T12 - the FINDINGS SINK is checked: a derived claim list, and a by-eye pass over what it cannot see

Attempt 1 shipped a findings note saying `development`'s pre-commit hook "names only three
checkers"; the command the sentence itself cites returns **four**. It reached a tester because
the plan listed the sink among the changed files and had no case that opened it. Attempt 2
then shipped a sentence in that same section - *"proven byte-equal to the previous blob with
CRs stripped"* - that no surviving revision satisfies, and **this case did not catch it**,
because the sentence sits in plain prose with no code span and no number. Both halves below
exist for that: a derived list, and a declared blind spot with a by-eye step over it.

### T12a - the derived list

**The list of claims is DERIVED FROM THE SECTION, not enumerated here.** A plan that lists the
claims by hand can only check the ones its author remembered - which is the same failure one
level up. Save as `$TMP\claims.ps1` and run it:

    param([string]$Wt = "D:\Open WebUI\ai-stack\.claude\worktrees\wt-deploystate")
    # Enumerate the checkable claims in the findings sink's own `## deploystate` section.
    # The list is DERIVED from the section, never typed out here: if a claim is added to the sink
    # and this prints one more row, the plan has grown a case without anyone editing the plan.
    #
    # WHAT IT CANNOT SEE is printed at the bottom, and is not a small print: attempt 2's tester
    # found that the one sink sentence that did not hold sat in plain prose with no code span at
    # all. A derived list that silently under-enumerates is the failure this case exists to catch,
    # one level up - so the limits are declared, and the plan pairs them with a by-eye step.
    $sink  = Join-Path $Wt "documentation\notes\deploy-gate-2026-09-06.md"
    $lines = [System.IO.File]::ReadAllLines($sink)
    $start = -1; $end = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -eq "## deploystate") { $start = $i; continue }
        if ($start -ge 0 -and $lines[$i] -match '^## ') { $end = $i; break }
    }
    if ($start -lt 0) { Write-Host "FAIL: no '## deploystate' section in $sink"; exit 1 }
    $body = $lines[($start + 1)..($end - 1)]
    Write-Host ("section: lines {0}-{1} of {2}" -f ($start + 1), $end, (Split-Path -Leaf $sink))

    $rows = @()
    for ($i = 0; $i -lt $body.Count; $i++) {
        $ln = $start + 2 + $i          # 1-based line number in the file
        $t  = $body[$i]
        foreach ($m in [regex]::Matches($t, '`([^`]+)`')) {
            $v = $m.Groups[1].Value
            $kind = $null
            if ($v -match '^(git|grep|powershell|queue\.ps1|\.\\|tr |python)\b' -or $v -match '\bgit (show|cat-file|config|rev-parse|diff-tree|log)\b') { $kind = "COMMAND" }
            elseif ($v -match '^[\w./\\-]+\.(ps1|py|md|json|csv|yml|conf):\d+$') { $kind = "FILE:LINE" }
            elseif ($v -match '^[0-9a-f]{7,40}$') { $kind = "SHA" }
            elseif ($v -match '^\d+$') { $kind = "FIGURE" }              # a bare number in a code span
            elseif ($v -match '^[\w./\\-]+\.(ps1|py|md|json|csv|yml|conf)$' -or $v -match '^[\w./-]+/$') { $kind = "PATH" }
            if ($kind) { $rows += [pscustomobject]@{ n = 0; line = $ln; kind = $kind; claim = $v } }
        }
        # ISO dates are not claims about the world, they are timestamps on claims. Everything else
        # numeric counts, INCLUDING single digits - attempt 2's tester found `9` invisible to a
        # `\d{2,}` pattern, and a one-digit count is exactly as checkable as a two-digit one.
        $tn = [regex]::Replace($t, '\d{4}-\d{2}-\d{2}', '<date>')
        $tn = [regex]::Replace($tn, '`[^`]+`', '<code>')          # already covered above
        foreach ($m in [regex]::Matches($tn, '(?<![\w.\-])\*{0,2}(\d+)\*{0,2}(?![\w.\-])')) {
            $rows += [pscustomobject]@{ n = 0; line = $ln; kind = "FIGURE"; claim = $m.Groups[1].Value }
        }
        # numbers written as words, which no digit pattern can see
        foreach ($m in [regex]::Matches($t, '(?i)(?<![\w])(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|thirty-one|thirty-two|forty)(?![\w])')) {
            $rows += [pscustomobject]@{ n = 0; line = $ln; kind = "WORD-NUM"; claim = $m.Groups[1].Value.ToLower() }
        }
    }
    $seen = @{}; $out = @()
    foreach ($r in $rows) {
        $k = $r.kind + "|" + $r.claim
        if ($seen.ContainsKey($k)) { continue }
        $seen[$k] = $true; $r.n = $out.Count + 1; $out += $r
    }
    $out | Format-Table n, line, kind, claim -AutoSize -Wrap
    Write-Host ("TOTAL ENUMERATED CLAIMS: {0}  (COMMAND {1}, FILE:LINE {2}, PATH {3}, SHA {4}, FIGURE {5}, WORD-NUM {6})" -f `
        $out.Count,
        @($out | Where-Object { $_.kind -eq "COMMAND" }).Count,
        @($out | Where-Object { $_.kind -eq "FILE:LINE" }).Count,
        @($out | Where-Object { $_.kind -eq "PATH" }).Count,
        @($out | Where-Object { $_.kind -eq "SHA" }).Count,
        @($out | Where-Object { $_.kind -eq "FIGURE" }).Count,
        @($out | Where-Object { $_.kind -eq "WORD-NUM" }).Count)
    Write-Host ""
    Write-Host "DECLARED LIMIT - this list is NOT every checkable claim in the section. It cannot see:"
    Write-Host "  * an assertion stated in plain prose with no code span, no number and no word-number"
    Write-Host "    (attempt 2's tester found the ONE sentence that did not hold in exactly that class);"
    Write-Host "  * a quantity implied rather than written ('the damage was confined to one file');"
    Write-Host "  * whether a sentence's CONCLUSION follows from figures that each check out."
    Write-Host "Read the section by eye for those three, and record what you found - see the plan case."

    powershell -NoProfile -NonInteractive -File "$TMP\claims.ps1" -Wt $WT

It prints one numbered row per enumerated claim in the `## deploystate` section: every inline
code span that is a COMMAND, a FILE:LINE, a SHA or a PATH; every FIGURE (any number, including
a single digit and including a bare number inside a code span - ISO dates excluded, since a
timestamp is not a claim about the world); and every WORD-NUM (a count written as a word). The
developer measured **61 rows (COMMAND 13, FILE:LINE 2, PATH 8, SHA 13, FIGURE 19, WORD-NUM
6)** against the sink as committed at this attempt's tip; the count moves whenever the section
does, which is the point - if yours differs, say so and reconcile it against the section rather
than against this number. Attempt 2's enumerator
was narrower - `\d{2,}` only, no SHA, no WORD-NUM, and it double-counted line numbers out of
FILE:LINE spans - and its tester named each of those gaps; they are closed here.

**Now work the table, top to bottom, and record a result for EVERY row.** For each row:

- **COMMAND** - run it, in the tester's worktree, and paste enough of the output to settle the
  sentence it appears in. A command that is a template (`git cat-file blob :<path>`) is
  recorded as `TEMPLATE` with a real path substituted, which is a result.
- **FILE:LINE** - open that file at that line and say what is there, in the sink's own terms.
  If the line has moved, the citation is wrong even though the code is right.
- **SHA** - resolve it (`git cat-file -t`, or the replay/diff the sentence uses it in) and say
  the object is what the sentence calls it.
- **PATH** - confirm it exists, or - for a path the sentence says is ABSENT - that it is.
- **FIGURE / WORD-NUM** - re-derive it. A figure the sink states as measured at a moment (the
  hand-off counts) is checked as "the arithmetic reconciles and the sentence says when", not
  as "it is still that number now" - a live board moves.

PASS: every row has a recorded result and every result agrees with the sentence the claim sits
in. **A claim whose command you did not re-run is a FAIL, not a skip.** FAIL: any row
unaddressed, or any sentence the re-run contradicts.

### T12b - the declared blind spot, read by eye

The enumerator prints its own limits at the end of its output, and they are not small print.
It cannot see: an assertion in plain prose carrying no code span, no digit and no word-number;
a quantity implied rather than written; and whether a sentence's CONCLUSION follows from
figures that each check out. **Read the `## deploystate` section straight through, hunting
only those three classes, and record what you found - including "nothing".**

Two shapes to hunt for by name, because each has already occurred here:

- **an equality or a proof asserted against a state that did not survive the commit.** Attempt
  2's "proven byte-equal to the previous blob with CRs stripped" was true of a working state
  and false at the tip; a reader checking it finds it false. Any sentence of the form "proven
  X" must name something runnable at the tip.
- **a count or a comparison stated without saying when it was taken**, where the thing counted
  moves (the live queue, a worktree registry, a branch tip).

PASS: the by-eye pass is recorded, every sentence of those shapes is named with its verdict,
and each one either resolves or is reported. FAIL: the section is not read, or a sentence of
either shape is left unexamined - "the table was all green" is exactly the answer that let the
last one through.

### T12c - the two rows that are attempt 1's and attempt 2's corrections

    git -C $WT show development:.githooks/pre-commit | Select-String "powershell.exe"

PASS: FOUR invocations, at lines 18, 25, 34 and 43, and none of them is
`check-corpus-exposure-producers.ps1` - so the sink's corrected count is right AND the
conclusion that rests on it still holds. FAIL: any other count, or the missing checker turns
out to be there.

    git -C $WT cat-file blob ff34eda:scripts/agent-harness/queue.ps1 | tr -cd '\r' | wc -c
    git -C $WT cat-file blob 49bb2db:scripts/agent-harness/queue.ps1 | tr -cd '\r' | wc -c
    git -C $WT cat-file blob HEAD:scripts/agent-harness/queue.ps1     | tr -cd '\r' | wc -c

PASS: `376` at `ff34eda`, `0` at the base `49bb2db`, and **`0` at HEAD** - the line-ending
damage the sink describes is real, and it is repaired on the tip you are testing. (`tr` and
`wc` come from Git's `usr/bin`; if they are not on your PATH, count with
`([regex]::Matches((Get-Content -Raw ...), "`r")).Count` and say which you used.) FAIL: HEAD
still carries a CR. **These three numbers are necessary and NOT sufficient** - they read the
same whether the repair commit repairs the file or repairs it and smuggles something in. T13
is the sufficient half.

---

## T13 - a line-ending repair carries the repair and NOTHING ELSE

A whitespace-only diff of several hundred lines is the ideal hiding place, and CR counts
cannot see into it: `376 / 0 / 0` is what you get whether the commit only strips CRs or strips
CRs and changes behaviour. This case is general - **run it for any commit on this branch whose
message claims a line-ending repair** - and it is the case attempt 2's tester ran only because
they were told to, not because the plan asked.

Identify the repair commit `$FIX` and the commit before it `$PREV` from the branch's history
(`git -C $WT log --oneline`; here `$FIX` = `af6f483` and `$PREV` = `7b5beb9`), and the file
`$F` the repair names (`scripts/agent-harness/queue.ps1`). Then:

    $F = "scripts/agent-harness/queue.ps1"; $PREV = "7b5beb9"; $FIX = "af6f483"
    git -C $WT diff --ignore-cr-at-eol $PREV $FIX -- $F | Set-Content "$TMP\residual.diff" -Encoding UTF8
    "hunks         : " + (Select-String -Path "$TMP\residual.diff" -Pattern '^@@').Count
    "changed lines : " + (Select-String -Path "$TMP\residual.diff" -Pattern '^[-+][^-+]').Count
    Select-String -Path "$TMP\residual.diff" -Pattern '^@@' | ForEach-Object { $_.Line }
    Get-Content "$TMP\residual.diff"

**Use `git diff --ignore-cr-at-eol`, not a shell pipeline** - the developer wrote this case
with `git cat-file blob ... | tr -d '\r' > old.txt` and `diff -u`, ran it, and it failed
twice over: in PowerShell `diff` is an alias for `Compare-Object` (`A parameter cannot be
found that matches parameter name 'u'`), and `>` writes through PowerShell's text encoder,
which turned every LF back into CRLF and made the byte counts read `bare CR 2271`. Git does
the whole job in one process with no encoding in the middle. If you want the raw byte counts
as well, take them in Git Bash (`git cat-file blob "$PREV:$F" | tr -cd '\r' | wc -c`), not
in PowerShell, and say which shell you used.

PASS, in three parts:

1. `--ignore-cr-at-eol` ignores a CR only at END of line, so a CR anywhere else would appear
   in the residual as a real change. The residual containing no such line IS the proof that
   the strip was lossless. FAIL: a hunk whose only content is an invisible character.
2. The residual is non-empty only where the attempt says it should be, and **every hunk is
   nameable from that attempt's stated scope**. For `af6f483` that is **86 changed lines (9
   removed, 77 added) in FOUR hunks**: the derivation section header rewritten for the
   manifest rule; the `ls-tree` probe plus the manifest-driven paste block; a comment block
   documenting the line-scoped evidence limit (no code); and the pin regex with its refusal
   message. Read the whole residual diff - not the hunk headers, whose offsets move - and
   write down what each hunk is. (If you count with `grep -c '^[-+]'` you will get 88: that
   pattern also matches the `---`/`+++` file headers. The developer and attempt 2's tester
   both did, and both reported 88.)
3. **A hunk nobody can name is a FAIL**, not a curiosity: it is a change that entered the
   repository inside a whitespace diff, which is the failure mode this case exists for. Say
   which hunk and what it does; do not pass the case by finding it plausible.

Also confirm the repair is described where a reader will look:

    Select-String -Path "$WT\documentation\notes\deploy-gate-2026-09-06.md" -Pattern "1d43a129|0735e9fc|FOUR hunks"

PASS: the sink names both blob ids and the four-hunk residual, and does NOT claim the landed
blob equals the previous blob with CRs stripped (it does not - `1d43a129` vs `0735e9fc`).
Those two ids are checkable. The second is shell-agnostic; **the first must be run in Git
Bash**, and the plan says so because the developer ran it in PowerShell while writing this
very case and got a third id:

    # Git Bash:
    git cat-file blob 7b5beb9:scripts/agent-harness/queue.ps1 | tr -d '\r' | git hash-object --stdin
    # either shell:
    git -C $WT rev-parse af6f483:scripts/agent-harness/queue.ps1

PASS: `1d43a129...` and `0735e9fc...`, and they differ - which is the whole point of the
rewritten sentence. **In PowerShell the same first pipeline returns `5cae3219...`**, because
PS5.1 decodes a native command's stdout into text lines and re-emits them with CRLF before
the next process sees them - so `hash-object` hashes bytes that never existed. If you get
`5cae3219`, you are in the wrong shell, and that is a demonstration of the very trap the
sink entry is about rather than a failure of the case. FAIL: the sink still asserts an
equality no revision satisfies, or the landed blob is not `0735e9fc`.

---
## What a PASS costs

Every heading above must appear in your evidence with the bare word `PASS` as the last word
on the heading line. A case you could not execute is `-Fail -PlanInadequate` with what the
plan should have made runnable - never a scoped pass. If the drill in T5 reports fewer than
213 checks, that is a FAIL even at `0 failed`. In T12a, a claim whose command you did not
re-run is a FAIL rather than a row left blank; in T12b, "the table was all green" is not a
result - attempt 1 passed every case it had and shipped a false checkable sentence because no
case opened the file it was in, and attempt 2 passed T12 and shipped an unsupportable one
because the sentence carried nothing the enumerator could see. In T13, a hunk nobody can name
is a FAIL.
