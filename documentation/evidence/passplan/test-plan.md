# Test plan - `passplan`

Branch `work/passplan`, worktree `wt-passplan`, base `refactor/ai-stack-cleanup` (`e72c678`
at branch time). Anchor: `queue.ps1 -Show -Id passplan`.

Changed: `scripts/agent-harness/queue.ps1` (case/evidence parser, `plan_sha256`, `-Pass`
refusal, per-case rows in `results[]`, `-Show` verdict block, UTF-8 item files, fenced
blocks ignored), `verify-queue-defects.ps1`
(fixtures gain case headings; D8-D11), `verify-merge-protocol.ps1` (its evidence carries
per-case PASS lines; four new checks; cleans its evidence files), `MERGE-PROTOCOL.md` (the
rule, the line shape, the three decided edges),
`scripts/agent-harness/README.md` (one table row), `documentation/evidence/passplan/`
(this plan + `fixtures/` = the real `curator2` plan and evidence, byte-identical to the live
queue's copies), `documentation/notes/deploy-gate-2026-09-06.md` (findings sink).

**You are testing whether a tester's `-Pass` can still be recorded when a case in the plan
was not executed and passed, and whether anything ELSE about verdicts moved.** No
containers, no leases. Everything below is git, PowerShell 5.1 and files under `$env:TEMP`.

## READ THIS FIRST

- **Never run `queue.ps1` against the live queue** for these cases. Every case that drives
  the tool does so through `AI_STACK_WORKTREE_STATE` pointing at a scratch directory - the
  same mechanism `verify-queue-defects.ps1` uses. The snippet in T2 sets it up.
- **The curator2 fixtures are the test.** `documentation/evidence/passplan/fixtures/` must
  hash identically to `.git/agent-worktrees/queue/curator2.plan.md` and
  `curator2.attempt1.evidence.md` (T0 checks). If they differ, the item FAILS - an edited
  fixture would be a test made easier.
- The rule under test, stated once: a case is `^##\s+(T\d+|Case\s+\d+)\b`; a case passes
  only when EVERY evidence heading with that id ends in the bare, upper-case token `PASS`;
  every plan case must have at least one such heading; an evidence heading for a case not
  in the plan is also held to bare-PASS. Anything else is a refusal that names the case
  and quotes the verdict as written.

## Environment

    $WT  = "<path to wt-passplan>"
    $QP  = "$WT\scripts\agent-harness\queue.ps1"
    $FIX = "$WT\documentation\evidence\passplan\fixtures"
    $LIVE = (git -C $WT rev-parse --path-format=absolute --git-common-dir) + "\agent-worktrees\queue"
    function Q { param([Parameter(ValueFromRemainingArguments = $true)][string[]]$a)
        $o = (& powershell.exe -NoProfile -NonInteractive -File $QP @a 2>&1 | Out-String); $global:QExit = $LASTEXITCODE; $o }

`Q` runs `queue.ps1` in a CHILD process and returns its whole output as one string, with the
exit code in `$QExit`. Every case below that asserts on refusal text uses it: `Die` prints
through `Write-Host`, which an in-process `& $QP ... | Tee-Object` cannot capture (attempt 1's
T4 failed on exactly that). `$QExit` is what you compare, not `$LASTEXITCODE`.

---

## T0 - the fixtures are the real files, and the gate code did not move

    Get-FileHash -Algorithm SHA256 "$FIX\curator2.plan.md", "$LIVE\curator2.plan.md", "$FIX\curator2.attempt1.evidence.md", "$LIVE\curator2.attempt1.evidence.md" | Format-Table Hash, Path

PASS: the two plan hashes are equal and the two evidence hashes are equal (at branch time:
plan `0040ec4d...`, evidence `0157e68f...`). FAIL: any pair differs.

    git -C $WT diff refactor/ai-stack-cleanup...work/passplan -- scripts/agent-harness/queue.ps1 | Select-String "^[-+]" | Select-String -Pattern "Resolve-Gate|Invoke-AndonForGate|Write-GateRecord|Invoke-AutoGate|Stop-OnAndon|Set-ItemGate"

PASS: ZERO matches - no added or removed line names any of those functions (the `-Pass`
handler's comment says "pre_review auto-gate", not a function name). FAIL: any match. Then
read the `-Pass` diff end to end and confirm every new `Die` sits BEFORE `Copy-IntoQueue`,
before the `rev-parse` of the branch, and before `$item.results +=`.

## T1 - the defects drill: D1-D7 still green, D8-D11 green, nothing left behind

    powershell -NoProfile -File "$WT\scripts\agent-harness\verify-queue-defects.ps1"; "exit=$LASTEXITCODE"
    Get-ChildItem $env:TEMP -Filter "queue-defects-*"
    Select-String -Path "$WT\scripts\agent-harness\verify-queue-defects.ps1" -Pattern 'AI_STACK_WORKTREE_STATE = \$fix\.state' | Measure-Object | Select-Object -Expand Count

PASS: the last line of the drill is exactly `140 check(s), 0 failed.` and `exit=0` (140 is
the count at branch time; it must not SHRINK - a smaller number means a check was dropped; a
larger one is fine if the section list grew); the output contains `=== D8`, `=== D9`,
`=== D10`, `=== D11`; the section list still contains D1..D7 and R; the `Get-ChildItem` prints
nothing; the last line prints `2` (both child-process helpers point the tool at the fixture's
own state dir - the drill never sees the live queue, which is why a live-queue listing diff is
NOT a check here: other agents write to it concurrently). Read the D8 REPLAY lines: they must
say the real evidence was refused, T5 and T6 named with their quoted verdicts, T7 `MISSING`,
and `NOTHING recorded`. Read the D10 lines: bytes `E2 80 94`, no BOM, `-Show prints the
heading with the em-dash intact`. Read the D11 lines: fenced-only plan refused at `-Submit`,
`-Requeue -TestPlan` and `-Resubmit -TestPlan`; a fenced `## T1 ... PASS` refused as `T1:
MISSING`. FAIL: any `[FAIL]`, a missing section, fewer than 140 checks, or a
`queue-defects-*` directory surviving.

## T2 - the curator2 replay by hand: refused, naming T5 and T6, before any mutation

Hermetic setup (a scratch repo + a scratch state dir; copy-paste as one block):

    $Root = Join-Path $env:TEMP ("passplan-t2-" + $PID); New-Item -ItemType Directory -Force $Root | Out-Null
    $repo = Join-Path $Root "repo"; New-Item -ItemType Directory -Force $repo | Out-Null
    Push-Location $repo
    git init -q -b base; git config user.email t@x.invalid; git config user.name t
    Set-Content README.md x -Encoding ascii; git add README.md; git commit -q -m base
    git checkout -q -b work/qd; Set-Content WORK.md w -Encoding ascii; git add WORK.md; git commit -q -m work; git checkout -q base
    $state = Join-Path $Root "state"; New-Item -ItemType Directory -Force $state | Out-Null
    $anchor = Join-Path $Root "anchor.json"
    Set-Content $anchor -Encoding ascii -Value '{"goal":"WORK.md states what the work was, unambiguously.","artifact":"WORK.md - a one-line note produced by the drill fixture.","audience":"The next agent to read the file with no other context.","acceptance":["WORK.md exists on the branch. Fail: it is absent.","It contains exactly one line. Fail: it is empty or contradictory."],"out_of_scope":["Anything outside WORK.md."],"findings_sink":"documentation/notes/queue-defect-drill.md"}'
    $env:AI_STACK_WORKTREE_STATE = $state; $env:AI_STACK_WORK_LINE = "base"
    Q -Propose -Id curator2 -Anchor $anchor -Developer qdev | Out-Null
    Q -ConfirmAnchor -Id curator2 -By op | Out-Null
    Q -Submit -Id curator2 -Branch work/qd -Developer qdev -TestPlan "$FIX\curator2.plan.md"
    Q -Claim -Id curator2 -Role tester -By qtester | Out-Null
    Q -Pass -Id curator2 -By qtester -Evidence "$FIX\curator2.attempt1.evidence.md" -PlanAdequate; "exit=$QExit"
    $it = Get-Content -Raw -Encoding UTF8 "$state\queue\curator2.json" | ConvertFrom-Json
    "state=$($it.state) results=$(@($it.results).Count) claim=$(Test-Path "$state\queue\curator2.tester.claim") evidenceCopied=$(Test-Path "$state\queue\curator2.attempt1.evidence.md")"

PASS: `-Submit` prints `8 case(s) the tester must execute: T0, T1, T2, T3, T4, T5, T6, T7`;
`-Pass` prints `ERROR: -Pass on 'curator2' is REFUSED ...` whose `the evidence says:` block
is exactly these four lines (order as in the evidence file):

    T1: PASS (with caveats)
    T5: PASS (scoped — read the caveat)
    T6: PASS (blocking path driven; chat half NOT run)
    T7: MISSING - no '## T7' heading in the evidence at all

`exit=1`; the last line prints `state=testing results=0 claim=True evidenceCopied=False`.
FAIL: exit 0, a state other than `testing`, a non-empty `results`, a dropped claim, an
evidence file at the destination, or a `the evidence says:` block that omits T5 or T6.
(The em-dash in T5's line is the fixture's own; if it prints as `â€"` the tool read the file
in the wrong encoding - that is a FAIL too.)

Leave `$Root`, `$state` and the env vars in place for T3 and T4; clean up at the end:

    Pop-Location; Remove-Item Env:\AI_STACK_WORKTREE_STATE; Remove-Item Env:\AI_STACK_WORK_LINE; Remove-Item -Recurse -Force $Root

## T3 - the synthetic matrix by hand, then the one shape that passes, then -Show

Still inside T2's environment. A three-case plan, submitted as a new item:

    $plan3 = Join-Path $Root "plan3.md"
    Set-Content $plan3 -Encoding ascii -Value @("# plan", "## T0 - first", "## T1 - second", "## T2 - third")
    Q -Propose -Id syn -Anchor $anchor -Developer qdev | Out-Null; Q -ConfirmAnchor -Id syn -By op | Out-Null
    Q -Submit -Id syn -Branch work/qd -Developer qdev -TestPlan $plan3 | Out-Null
    Q -Claim -Id syn -Role tester -By qtester | Out-Null
    function Ev([string[]]$l) { $p = Join-Path $Root ("ev-" + [guid]::NewGuid().ToString("N") + ".md"); [IO.File]::WriteAllText($p, ($l -join "`n"), (New-Object Text.UTF8Encoding($false))); $p }
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  PASS")); "exit=$QExit"
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  FAIL", "## T2 - c  PASS")); "exit=$QExit"
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  SKIPPED", "## T2 - c  PASS")); "exit=$QExit"
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  PASS", "## T2 - c  PASS (scoped - no stack)")); "exit=$QExit"
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  PASS", "## T2 - c  PASS (partial)")); "exit=$QExit"
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS - mostly", "## T1 - b  PASS", "## T2 - c  PASS")); "exit=$QExit"
    (Get-Content -Raw -Encoding UTF8 "$state\queue\syn.json" | ConvertFrom-Json).state

PASS: six refusals, each `exit=1`, each naming exactly the offending case with its verdict
as written - `T2: MISSING ...`, `T1: FAIL`, `T1: SKIPPED`, `T2: PASS (scoped - no stack)`,
`T2: PASS (partial)`, `T0: PASS - mostly` - and the state prints `testing`. FAIL: any exit 0,
a refusal that names a case that WAS bare-PASS, or a state change.

The shape that passes. The em-dash element is PARENTHESISED: under PS 5.1 the comma binds
tighter than `+`, so without the parentheses the array has five elements and the T1 heading
is written with nothing after its id (attempt 1's snippet had exactly that defect):

    $dash = "## T1 " + [string][char]0x2014 + " b   PASS"
    Q -Pass -Id syn -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", $dash, "## t2 - c  PASS")); "exit=$QExit"
    $it = Get-Content -Raw -Encoding UTF8 "$state\queue\syn.json" | ConvertFrom-Json
    $it.state; $it.results[0].attempt; $it.results[0].cases | Format-Table case, verdict, line
    $it.results[0].cases[1].line -eq $dash
    [IO.File]::ReadAllBytes("$state\queue\syn.json") | ForEach-Object { $_.ToString("X2") } | Out-String -Stream | Select-String "E2" | Measure-Object | Select-Object -Expand Count
    $prev = [Console]::OutputEncoding; [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $show = (& powershell.exe -NoProfile -NonInteractive -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; & '$QP' -Show -Id syn" 2>&1 | Out-String); [Console]::OutputEncoding = $prev
    $show.Contains($dash); $show.IndexOf("--- VERDICTS ---") -lt $show.IndexOf("--- RECORD ---")

PASS: `exit=0`; state `test-passed`; `attempt` 1; three rows `T0/T1/T2` all `PASS`, each
`line` the full heading text (`t2` normalised to `T2`); the `-eq $dash` line prints `True`
(the em-dash survived into the record - attempt 1 stored `?`); the byte count line is >= 1
(the file holds UTF-8 bytes, `E2 80 94`); `-Show` prints a `--- VERDICTS ---` block with
`attempt 1: PASS by qtester at <sha8> (plan_adequate=True)`, one indented `T<n>     PASS`
line per case each followed by the heading as written, BEFORE the `--- RECORD ---` JSON, so
the last two lines print `True` `True`. FAIL: the rows are missing, a verdict is not `PASS`,
a `?` where the em-dash was, or `-Show` has no VERDICTS block.

## T4 - the plan hash: written at submit, drift refused naming both, revised by the two doors

Still inside T2's environment:

    Q -Propose -Id h -Anchor $anchor -Developer qdev | Out-Null; Q -ConfirmAnchor -Id h -By op | Out-Null
    Q -Submit -Id h -Branch work/qd -Developer qdev -TestPlan $plan3 | Out-Null
    $rec = (Get-Content -Raw -Encoding UTF8 "$state\queue\h.json" | ConvertFrom-Json).plan_sha256
    $rec -eq (Get-FileHash -Algorithm SHA256 "$state\queue\h.plan.md").Hash.ToLower()
    Add-Content "$state\queue\h.plan.md" "## T3 - added after submit"
    $now = (Get-FileHash -Algorithm SHA256 "$state\queue\h.plan.md").Hash.ToLower()
    Q -Claim -Id h -Role tester -By qtester | Out-Null
    $out = Q -Pass -Id h -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  PASS", "## T2 - c  PASS", "## T3 - d  PASS")); $out; "exit=$QExit"
    $out -match $rec; $out -match $now; $out -match "is not the plan that was submitted"
    (Get-Content -Raw -Encoding UTF8 "$state\queue\h.json" | ConvertFrom-Json).state

PASS: the first comparison prints `True` (64 lower-case hex); the refusal says `is not the
plan that was submitted`, `exit=1`, all three `-match` lines print `True`, state `testing`.
FAIL: the pass is accepted although the evidence matches the drifted file, or only one hash
is named.

    Q -Fail -Id h -By qtester -Reason drift -Evidence "drifted" -PlanInadequate | Out-Null
    Q -Resubmit -Id h -By qdev -TestPlan $plan3 | Out-Null
    (Get-Content -Raw -Encoding UTF8 "$state\queue\h.json" | ConvertFrom-Json).plan_sha256 -eq (Get-FileHash -Algorithm SHA256 "$state\queue\h.plan.md").Hash.ToLower()

PASS: `True` - `-Resubmit -TestPlan` re-recorded the hash of the newly queued plan. The
`-Requeue -TestPlan` door is proven mechanically by D9 in T1 (`D9: -Requeue -TestPlan
re-records plan_sha256`) and `-Requeue` WITHOUT a plan leaves it untouched (same section);
confirm both lines are `[PASS]` in the T1 output rather than re-driving them.

## T5 - the end-to-end drill passes under the new rule, and its own evidence is checked

    powershell -NoProfile -File "$WT\scripts\agent-harness\verify-merge-protocol.ps1"

PASS: the DRILL SUMMARY contains these four new rows as PASS: `evidence that names no case
is REFUSED - the pass rule holds on the drill itself`, `a 'PASS (scoped' case is REFUSED,
and the claim survives the refusal`, `the pass carries per-case verdicts in results[]
({case, verdict, line})`, `plan_sha256 was recorded at submit and matches the queued plan`;
and the drill's total is `70/70` OR its FAIL set is EXACTLY the six pre-existing rows below
and nothing else. `git branch --list "work/drilla" "work/drillb" "drill/verify-d"` prints
nothing afterwards and `Get-ChildItem $LIVE -Filter "drill-*"` prints nothing.

**KNOWN RED on this machine at branch time, not attributable to this change** (findings
sink, `documentation/notes/deploy-gate-2026-09-06.md`): the main checkout's pre-commit hook
runs `./scripts/checks/check-corpus-exposure-producers.ps1`, which `development` (the
drill's hard-coded base) does not carry, so the drill's two step-2 commits are refused and
six rows fail: `two divergent commits exist`, `rebase produced a real conflict`, `the
tested sha is no longer what would land`, `A's intent survived the later merge`, `B's
adapted intent is present`, `two --no-ff merge commits on the line`. Prove it is
pre-existing rather than take it on trust: run the BASE checkout's own copy,
`powershell -NoProfile -File "D:\Open WebUI\ai-stack\scripts\agent-harness\verify-merge-protocol.ps1"`,
and confirm it fails the same six (`60/66` at branch time). FAIL for this item: any OTHER
row red, any of the four new rows red, or drill leftovers. NOTE: this drill runs in the
main checkout by design (its header, lines 16-18); it never switches the operator's branch.

## T6 - nothing else about verdicts moved

Read `git -C $WT diff refactor/ai-stack-cleanup...work/passplan -- scripts/agent-harness/queue.ps1`
for the `-Pass/-Fail` handler and confirm, by reading and not by grep:

- `Assert-Claim $item "tester" $By` is still the second statement and unchanged.
- The `-PlanAdequate`/`-PlanInadequate` block and the empty `-Evidence` refusal are unchanged.
- The only additions to the `-Fail` path are `$caseRows = @(Get-EvidenceVerdicts ...)`
  (shared with `-Pass`), the `attempt` and `cases` fields on the results record, and a
  one-line count message. Every new `Die` is inside `if ($Pass)`.

Then mechanically, in T1's output: `R: the developer cannot TEST their own work (exit 4)`,
`R: the prefixed form ... (exit 4)`, `R: only the DEVELOPER may -Resubmit (exit 4)` and
`D8: -Fail is recorded (exit 0, test-failed) with the per-case rows it found` are all
`[PASS]`. PASS: all of the above. FAIL: any new `Die` reachable on `-Fail`, or a moved
separation-of-duties check.

## T7 - a plan with no case headings is refused at the door, saying what a heading is

Inside T2's environment:

    $bad = Join-Path $Root "bad.md"; Set-Content $bad -Encoding ascii -Value @("# plan", "Case 1: it works.", "T2: also works")
    Q -Propose -Id z -Anchor $anchor -Developer qdev | Out-Null; Q -ConfirmAnchor -Id z -By op | Out-Null
    $out = Q -Submit -Id z -Branch work/qd -Developer qdev -TestPlan $bad; $out; "exit=$QExit"
    $out -match "has no case headings"; $out -match "## T0 - what it checks"; $out -match "## Case 1 - what it checks"
    (Get-Content -Raw -Encoding UTF8 "$state\queue\z.json" | ConvertFrom-Json).state; Test-Path "$state\queue\z.plan.md"

PASS: `exit=1`; the three `-match` lines print `True`; state `anchor-confirmed`; `False`
(no plan copied). FAIL: the item queues, or the message does not show a heading.

## T10 - a heading inside a fenced code block counts for nothing (the false-pass vector)

Inside T2's environment. The plan carries two real cases and a fenced phantom; the evidence
carries one real heading and the other only inside a fence - the protocol's example, pasted:

    $pf = Join-Path $Root "plan-fence.md"; Set-Content $pf -Encoding ascii -Value @("# plan", "## T0 - a", "## T1 - b", '```', "## T9 - phantom", '```')
    Q -Propose -Id f -Anchor $anchor -Developer qdev | Out-Null; Q -ConfirmAnchor -Id f -By op | Out-Null
    $out = Q -Submit -Id f -Branch work/qd -Developer qdev -TestPlan $pf; $out -match "2 case\(s\) the tester must execute: T0, T1\s"; "exit=$QExit"
    Q -Claim -Id f -Role tester -By qtester | Out-Null
    $out = Q -Pass -Id f -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "the shape that counts:", '```markdown', "## T1 - b  PASS", '```')); $out -match "T1: MISSING"; "exit=$QExit"
    $out = Q -Pass -Id f -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "~~~", "## T1 - b  PASS", "~~~")); $out -match "T1: MISSING"; "exit=$QExit"
    $out = Q -Pass -Id f -By qtester -PlanAdequate -Evidence (Ev @("## T0 - a  PASS", "## T1 - b  PASS", '```', "## T1 - draft  FAIL", '```')); "exit=$QExit"
    $onlyFenced = Join-Path $Root "plan-only-fenced.md"; Set-Content $onlyFenced -Encoding ascii -Value @("# plan", '```', "## T0 - looks like a case", '```')
    Q -Propose -Id g -Anchor $anchor -Developer qdev | Out-Null; Q -ConfirmAnchor -Id g -By op | Out-Null
    $out = Q -Submit -Id g -Branch work/qd -Developer qdev -TestPlan $onlyFenced; $out -match "no case headings"; "exit=$QExit"

PASS, in order: `True` `exit=0` (T9 is not a case); `True` `exit=1` (a ``` fenced T1 does
not count); `True` `exit=1` (nor a ~~~ fenced one); `exit=0` (a fenced FAIL is a quotation -
the two real PASS headings carry it); `True` `exit=1` (a plan whose only heading is fenced
is refused at the door). FAIL: any of those inverted - in particular the first `-Pass`
reaching `exit=0`, which is the false-pass vector attempt 1's tester found. The same rule at
the `-Resubmit -TestPlan` and `-Requeue -TestPlan` doors is D11 in T1 (both rows `[PASS]`).

## T8 - MUTATION: disable the refusal and watch D8 go red (the drill checks something)

`queue.ps1` dot-sources its siblings from `$PSScriptRoot`, so the mutant is a COPY OF THE
WHOLE DIRECTORY with one file edited, and the drill under test stays the worktree's own:

    $mut = Join-Path $env:TEMP "ah-mutant"; if (Test-Path $mut) { Remove-Item -Recurse -Force $mut }
    Copy-Item -Recurse "$WT\scripts\agent-harness" $mut
    $mq = Join-Path $mut "queue.ps1"; $src = [IO.File]::ReadAllText($mq)
    $new = $src.Replace('if (-not $compared.ok) {', 'if ($false -and -not $compared.ok) {').Replace('if ($planThen -ne $planNow) {', 'if ($false) {')
    ($src -ne $new) -and $new.Contains('if ($false -and -not $compared.ok) {') -and $new.Contains('if ($false) {')
    [IO.File]::WriteAllText($mq, $new, (New-Object Text.UTF8Encoding($false)))
    powershell -NoProfile -File "$WT\scripts\agent-harness\verify-queue-defects.ps1" -Script $mq 2>&1 | Select-String "FAILED:|check\(s\)"; "exit=$LASTEXITCODE"
    Remove-Item -Recurse -Force $mut

The comparison line must print `True` - both mutations landed (the case refusal and the
hash refusal are each switched off).

PASS: the drill ends `N check(s), K failed.` with K >= 1, `exit=1`, and the FAILED list
includes `D8 REPLAY: the real curator2 evidence is REFUSED (non-zero exit)`, at least one
`-> refused, named` row, and `D9: -Pass against the drifted plan is REFUSED`. FAIL: `0
failed` against the mutant - the drill would be checking nothing - or either of those two
rows absent from the FAILED list.

## T9 - the documents say what the tool does

    Select-String -Path "$WT\documentation\implementation-guide\multi-agent-concurrency\MERGE-PROTOCOL.md" -Pattern "plan inadequacy|never a scoped pass|last word is the bare token|Which headings are cases|ids are literal|count for nothing|Inline .-Evidence. text may carry"
    Select-String -Path "$WT\scripts\agent-harness\README.md" -Pattern "D8/D9"

PASS: the first prints the new bullet under **Cases that keep earning their place** (two
sentences: cannot-execute = `-PlanInadequate`, never a scoped pass), the **evidence line
shape** block in Step 3 with a fenced example whose headings end in `PASS`, and the "three
edges" paragraph (ids literal - `T05` and `Case 5` are not `T5`; fences count for nothing;
inline `-Evidence` may carry verdicts); the second prints the `verify-queue-defects.ps1` row.
Then read both passages against T2/T3/T10's actual messages: every claim the doc makes
about what is refused must be one you saw refused, and the three edges must match what
you observed (`## T05` -> `T5: MISSING`; a fenced heading ignored; an inline
`-Evidence "## T0 - a  PASS`n## T1 - b  PASS`n## T2 - c  PASS"` accepted on a fresh
three-case item). FAIL: a documented behaviour you could not reproduce.

## What a FAIL looks like overall

A `-Pass` that lands with a case reading anything but bare `PASS`; a refusal that leaves a
result row, drops the claim or copies the evidence; a drift the tool did not see; a drill
that is green against the mutant; or a fixture that no longer hashes to the live file.
