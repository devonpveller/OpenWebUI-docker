# Test plan - queue item `regempty`

**Branch:** `work/regempty` **Developer:** `regempty`

**The change:** `scripts/agent-harness/queue.ps1` `Test-KnownAgent` already exempted a
MISSING and a NULL worktree registry from the unregistered-developer refusal - "cannot check"
is not "checked and failed". It did not exempt an EMPTY-but-present one, and that is the shape
`remove-worktree.ps1` writes when the LAST worktree is retired (and when `-PruneRegistry`
drops the last dead row). The result was that ordinary, correct cleanup made `-Submit` refuse
EVERY developer with exit 4. The fix adds the zero-rows case at that one site, plus a red-first
`D7` block in `scripts/agent-harness/verify-queue-defects.ps1`.

---

## READ THIS FIRST - two rules, or you will test nothing

**1. Drive the CHANGED copy by path.** This item changes the tool you use to run the queue.
`queue.ps1` typed as a bare command runs the MAIN CHECKOUT's copy, not this branch's. Every
command below names a full path into the worktree under test. If your output says
`D:\Open WebUI\ai-stack\scripts\...` rather than a path under `.claude\worktrees\`, you tested
the wrong file. The drill prints the copy it is driving as its first line - check it.

**2. Never point anything at the real queue state.** The live queue has ~19 rows and other
agents are working in it right now. Everything below is either hermetic by construction (the
drill builds its own scratch git repo and state dir per case) or explicitly redirected with
`$env:AI_STACK_WORKTREE_STATE`. Do not run a bare `queue.ps1 -Submit` to "see if it works":
that writes to the real queue. Case 2 below shows the redirected form.

Set this once and use `$W` everywhere:

```powershell
$W = "D:\Open WebUI\ai-stack\.claude\worktrees\wt-regempty"
```

(Substitute the path the reviewer/tester actually has the branch checked out at, if different.
It must be the checkout that holds `work/regempty`, not the main checkout.)

---

## Case 1 - the drill is GREEN against the changed copy

```powershell
& powershell -NoProfile -NonInteractive -File "$W\scripts\agent-harness\verify-queue-defects.ps1" `
    -Script "$W\scripts\agent-harness\queue.ps1"
```

**Pass:** first line names `...\wt-regempty\scripts\agent-harness\queue.ps1`; last line reads
`77 check(s), 0 failed.`; exit code 0 (`$LASTEXITCODE`).
**Fail:** any non-zero failure count, or a `-Script` line naming a path outside the worktree.

## Case 2 - the drill is RED against the UNCHANGED copy (the red-first requirement)

A fix without a test that fails before it does not count. Take the version of `queue.ps1` from
the commit BEFORE the fix and drive the same drill at it. The copy has to sit in
`scripts\agent-harness\` because `queue.ps1` dot-sources its siblings via `$PSScriptRoot`.

```powershell
Push-Location $W
$fixCommit = (git log -1 --format=%H -- scripts/agent-harness/queue.ps1).Trim()
git show "$fixCommit^:scripts/agent-harness/queue.ps1" |
    Set-Content -Path "$W\scripts\agent-harness\queue.PREFIX.ps1" -Encoding ASCII
Pop-Location
```

Confirm the guard really is absent before you trust the RED - a "pre-fix" copy that still has
the fix would hand you a false green:

```powershell
Select-String -Path "$W\scripts\agent-harness\queue.PREFIX.ps1" -Pattern 'props.Count -eq 0' -SimpleMatch
# expect: NO output. If it prints a line, you took the wrong commit - stop and fix that first.

& powershell -NoProfile -NonInteractive -File "$W\scripts\agent-harness\verify-queue-defects.ps1" `
    -Script "$W\scripts\agent-harness\queue.PREFIX.ps1"

Remove-Item "$W\scripts\agent-harness\queue.PREFIX.ps1"   # do not leave it behind
```

**Pass:** `77 check(s), 3 failed.`, exit 1, and the three failures are exactly:

- `D7: -Submit against an EMPTY registry is ACCEPTED (exit 0), not refused` - `exit=4`
- `D7: and the item really was queued` - `state=anchor-confirmed`
- `D7: an arbitrary developer id is equally accepted when the registry names nobody` - `exit=4`

**Fail:** 0 failures (the test does not actually test the fix - the worst outcome here), or
failures outside D7 (the change broke something else), or a different failure count.

Note the three `D7 premise:` checks PASS in both runs. That is correct: they assert what
`remove-worktree.ps1` writes, which this change does not touch.

## Case 3 - the four registry shapes, each proven by running `-Submit`

The drill covers these, but check them yourself against the changed copy with a hermetic
throwaway state dir. This is the acceptance criterion that must not be taken on trust, and it
is where an over-broad fix would show up as an authorization hole.

```powershell
$Root = Join-Path $env:TEMP ("regempty-tester-" + $PID)
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$repo = Join-Path $Root "repo"
New-Item -ItemType Directory -Force -Path $repo | Out-Null
Push-Location $repo
git init -q -b base; git config user.email "t@example.invalid"; git config user.name "t"
Set-Content README.md "scratch" -Encoding ascii; git add README.md; git commit -q -m base
git checkout -q -b work/qd; Set-Content WORK.md "the work" -Encoding ascii
git add WORK.md; git commit -q -m work; git checkout -q base
Pop-Location

$anchor = Join-Path $Root "anchor.json"
@'
{
  "goal": "WORK.md states what the work was.",
  "artifact": "WORK.md - one line from the fixture.",
  "audience": "The next agent with no other context.",
  "acceptance": ["WORK.md exists. Fail: absent."],
  "out_of_scope": ["Anything else."],
  "findings_sink": "documentation/notes/tt.md"
}
'@ | Set-Content -Path $anchor -Encoding ascii
$plan = Join-Path $Root "plan.md"
Set-Content -Path $plan -Value "# plan`r`nCase 1: WORK.md exists." -Encoding ascii

function Try-Shape([string]$name, $json, [string]$dev) {
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    if ($null -ne $json) { Set-Content -Path (Join-Path $state "worktrees.json") -Value $json -Encoding ASCII }
    $env:AI_STACK_WORKTREE_STATE = $state     # <-- SCRATCH STATE. never unset while running these.
    $env:AI_STACK_WORK_LINE = "base"
    Push-Location $repo
    $q = "$W\scripts\agent-harness\queue.ps1"                 # <-- THE COPY UNDER TEST, by path
    & powershell -NoProfile -NonInteractive -File $q -Propose -Id tt -Anchor $anchor -Developer $dev | Out-Null
    & powershell -NoProfile -NonInteractive -File $q -ConfirmAnchor -Id tt -By op | Out-Null
    & powershell -NoProfile -NonInteractive -File $q -Submit -Id tt -Branch work/qd -Developer $dev -TestPlan $plan | Out-Null
    $code = $LASTEXITCODE
    Pop-Location
    $st = (Get-Content -Raw (Join-Path $state "queue\tt.json") | ConvertFrom-Json).state
    Write-Host ("{0,-24} dev={1,-10} exit={2} state={3}" -f $name, $dev, $code, $st)
    Remove-Item Env:\AI_STACK_WORKTREE_STATE, Env:\AI_STACK_WORK_LINE
}

Try-Shape "empty"        '{"worktrees":{}}'                                     "anydev"
Try-Shape "null"         '{"worktrees":null}'                                   "anydev"
Try-Shape "absent-key"   '{}'                                                   "anydev"
Try-Shape "no-file"      $null                                                  "anydev"
Try-Shape "populated"    '{"worktrees":{"qdev":{"id":"qdev","branch":"work/qdev"}}}' "stranger"
Try-Shape "populated-ok" '{"worktrees":{"qdev":{"id":"qdev","branch":"work/qdev"}}}' "qdev"

Remove-Item -Recurse -Force $Root
```

**Pass** - exactly this table:

| shape | `-Developer` | expected exit | expected state |
|---|---|---|---|
| `{"worktrees":{}}` (empty) | anything | 0 | `ready-to-test` |
| `{"worktrees":null}` | anything | 0 | `ready-to-test` |
| `{}` (absent key) | anything | 0 | `ready-to-test` |
| no registry file | anything | 0 | `ready-to-test` |
| one row, developer NOT in it | `stranger` | **4** | `anchor-confirmed` |
| one row, developer IS in it | `qdev` | 0 | `ready-to-test` |

**Fail, and this is the one that matters most:** if the `stranger` row returns 0, the
availability fix has been traded for an authorization hole - a real registry that names
someone must still refuse an id it does not name. Fail the item.

## Case 4 - the premise: `remove-worktree.ps1` really writes the empty shape

Verify by running, not by reading. This is also the third `D7 premise:` check in the drill,
but run it standalone so you have seen the file with your own eyes.

```powershell
$Root = Join-Path $env:TEMP ("regempty-premise-" + $PID)
$repo = Join-Path $Root "repo"; $state = Join-Path $Root "state"; $wt = Join-Path $Root "wt-one"
New-Item -ItemType Directory -Force -Path $repo, $state | Out-Null
Push-Location $repo
git init -q -b development; git config user.email "t@example.invalid"; git config user.name "t"
Set-Content README.md "scratch" -Encoding ascii; git add README.md; git commit -q -m base
git worktree add -q -b work/one $wt
Pop-Location

$reg = Join-Path $state "worktrees.json"
(@{ worktrees = @{ one = @{ id = "one"; path = $wt; branch = "work/one" } } } | ConvertTo-Json -Depth 6) |
    Set-Content -Path $reg -Encoding ASCII

$env:AI_STACK_WORKTREE_STATE = $state       # <-- SCRATCH STATE
$env:AI_STACK_WORK_LINE = "development"
Push-Location $repo
& powershell -NoProfile -NonInteractive -File "$W\scripts\agent-harness\remove-worktree.ps1" -Id one
Pop-Location
Remove-Item Env:\AI_STACK_WORKTREE_STATE, Env:\AI_STACK_WORK_LINE

Get-Content -Raw $reg          # the shape the fix has to survive
Remove-Item -Recurse -Force $Root
```

**Pass:** the file still exists and contains `{"worktrees": {}}` - a present, non-null,
zero-row object. (`(Get-Content -Raw $reg | ConvertFrom-Json).worktrees` is not `$null`, and
`@(... .PSObject.Properties).Count` is 0.)
**Fail:** the file is deleted, or `worktrees` comes back `$null`. Either would mean the premise
is wrong and the item should be reconsidered rather than accepted - say so plainly.

Repeat with `-PruneRegistry` if you want the second door: seed a single row whose `path` points
at a directory that does not exist and run
`remove-worktree.ps1 -PruneRegistry` in place of the `-Id one` line. Same expected output.

## Case 5 - the neighbouring suite still passes

```powershell
Push-Location $W
python scripts/claude-sessions-bridge/test_worktree.py
Pop-Location
```

**Pass:** `Ran 21 tests`, `OK`. **Fail:** any failure or error.

## Case 6 - nothing else in the tree moved

```powershell
Push-Location $W
git diff --stat HEAD~1
Pop-Location
```

**Pass:** exactly five files -

- `scripts/agent-harness/queue.ps1` - the one-condition fix and its comment
- `scripts/agent-harness/verify-queue-defects.ps1` - the D7 block and one line in the header list
- `scripts/agent-harness/README.md` - one word, six -> seven
- `documentation/notes/agent-harness-queue-defects-2026-09-04.md` - an appended out-of-scope
  finding, F4
- `documentation/evidence/regempty/test-plan.md` - this file

**Fail:** `remove-worktree.ps1` appears - the anchor explicitly forbids fixing this by changing
what that script writes. Also fail on any change under `scripts/checks/`, `.githooks/` or `OB1`
- other in-flight items own those and this branch must not touch them.

---

## What is deliberately NOT covered

- `-Unclaim` (anchor F1), the `tested_at_sha` invariant, the vacuous D4 check, and the five
  surviving mutants from `harnessq`: all named out of scope by this anchor.
- A registry whose `worktrees` value is a STRING or an ARRAY still refuses everyone, for the
  same family of reason. Verified by running, filed as F4 in
  `documentation/notes/agent-harness-queue-defects-2026-09-04.md`, and deliberately not fixed
  here - the right answer is a third outcome ("your registry is malformed"), not a wider
  exemption, and that needs its own red-first test.
