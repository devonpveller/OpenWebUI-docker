# check-hook-attestation.ps1 - did the hooks actually run for these commits, and is the
# message they validated still the message the commit carries?
#
# WHY THIS EXISTS (PLAN 0 A7, dark-factory-unification U5). The audit verdict:
# "Cloud/worktree agents can be governed normatively" - FALSIFIED. An agent reached for
# `--no-verify` on its first commit, and `--no-verify` leaves NO trace in a git object,
# so "the hooks ran" was unprovable after the fact. A rule in a protocol document did not
# stop it; only a mechanism can. The attesting hook leaves proof - it is
# .githooks/commit-msg, the LAST hook that can veto (it was .githooks/pre-commit until
# 2026-08-30, and being FIRST was a hole: see .githooks/attest-lib.sh) - and this reads it
# back.
#
# IT READS BOTH LEDGER COLUMNS (2026-08-30, round 3). The ledger line is
# "<tree> <message-digest> <iso> <branch>", and until this change NOTHING read the message
# column - this script parsed field 0 and compared trees. A ledger column nothing reads is
# not a control, and the gap was demonstrated rather than theorised: a message-only rewrite
# against an already-attested tree
#
#     git -c core.hooksPath=/nonexistent commit --amend -m "OB1 -> deadbee1, pushed"
#
# left the tree attested, so this script printed "[OK] every commit's tree was validated"
# and exited 0 - and queue.ps1 -Submit gates on exactly that exit code. The commit message
# is the operator's audit surface under PLAN.md SS C.7 and the thing that carries the
# gitlink-SHA claim CLAUDE.md makes hard, so that was the wrong column to leave unread.
#
# THE MESSAGE DIGEST IS OVER RAW STORED BYTES - the same rule .githooks/reference-transaction
# applies live, so the two readers of this ledger cannot disagree about what a pair means.
# The attester canonicalises the message FILE before hashing it (.githooks/attest-lib.sh),
# which is what makes "attested bytes" and "stored bytes" the same bytes.
#
# WHAT IT PROVES, AND WHAT IT DOES NOT. An attested (tree, message) pair means the checks
# passed for that exact content AND the commit still says what was validated. An UNATTESTED
# commit means one of:
#   - the commit was made with --no-verify (the case this exists for);
#   - it was made before attestation shipped, or in a clone without core.hooksPath set;
#   - a rebase/merge produced NEW content that no hook ever saw (conflict resolution);
#   - its content was validated but its MESSAGE was rewritten afterwards.
# It cannot tell those apart, and it does not pretend to - it reports what is unattested
# and lets the caller decide. Being honest about that is what keeps it trustworthy.
#
# MERGE COMMITS USED TO BE EXEMPT, and the reason given here was wrong. It said `git merge`
# runs pre-merge-commit and never pre-commit, so a merge tree could never be attested.
# Running it says otherwise:
#
#   CONFLICTED merge -> git stops; the resolution is committed with `git commit`
#                    -> pre-commit RUNS and attests. Never was a gap.
#   CLEAN merge      -> pre-merge-commit is the only hook invoked, and there was no such
#                       hook -> nothing ran, and the tree went unchecked.
#
# So the exemption was blanket cover for a hole one case wide, and it also discarded the
# conflicted-merge attestations the hook was already writing. Merges are now CHECKED, with
# the false-positive problem solved by activation rather than by exemption: the gate turns
# on only when .githooks/pre-merge-commit existed at the FORK POINT of Base..Branch. If it
# did, every merge made on this branch had the hook available and has no excuse; if it did
# not, the branch is silently skipped. Fork-point content is history - unlike a date or an
# environment variable, the caller cannot arrange it after the fact.
#
# Usage:
#   .\check-hook-attestation.ps1 -Branch work/foo -Base refactor/ai-stack-cleanup
#   .\check-hook-attestation.ps1 -Branch work/foo -Base development -Json
#
# Exit: 0 = every checked commit attested | 1 = one or more unattested | 2 = usage/env

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [Parameter(Mandatory = $true)][string]$Base,
    [string]$RepoRoot = "",
    # Drill-only. The ledger path used to be overridable by AI_STACK_ATTEST_LEDGER alone,
    # which made the guard a one-line env-var switch for exactly the party it constrains:
    # the agent that runs `queue.ps1 -Submit` sets its own environment. The override now
    # requires this explicit flag, which only the drill passes - an environment variable
    # by itself no longer disables anything.
    [switch]$AllowLedgerOverride,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Invoke-GitLines {
    # PS 5.1 trap: with $ErrorActionPreference='Stop', a native command writing to stderr
    # raises NativeCommandError even on exit 0. Flip the preference around the call - the
    # house pattern (see scripts/agent-harness/git-io.ps1).
    param([string[]]$GitArgs)
    # -C rather than a working-directory change, so this script never mutates its caller's
    # location (see the note below the param block).
    if ($script:GitDir) { $GitArgs = @("-C", $script:GitDir) + $GitArgs }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return @(& git @GitArgs 2>$null) } finally { $ErrorActionPreference = $prev }
}
$script:GitDir = $RepoRoot

# THE STORED MESSAGE, AS BYTES. Everything about this function is about not letting
# PowerShell touch the bytes on the way past.
#
#   - "git cat-file commit <sha>", cutting everything through the first EMPTY line. NOT
#     "git log -1 --format=%B", which appends a newline of its own and so hashes to a
#     different blob than the one the hook attested - measured on four commits drawn from
#     a 14-shape corpus, all four differed.
#   - stdout is read as a RAW STREAM, not as a PowerShell string. PS 5.1 decodes native
#     stdout using the console encoding, which mangles any message that is not pure ASCII.
#   - the blob id is computed IN .NET, not by feeding the bytes back to
#     "git hash-object --stdin". That was the first implementation and it was WRONG: 53
#     bytes written to Process.StandardInput.BaseStream hashed to f2046d1f instead of
#     3316699b, because the StreamWriter PowerShell wraps around that stream emits the
#     console encoding's 3-byte UTF-8 preamble of its own accord. It is the repo's known
#     "PS 5.1 prefixes a BOM when piping into a native process" trap wearing a different
#     hat, and it produced a FALSE POSITIVE on an honest commit - caught only because the
#     honest-commit control was run beside the forged one. The drill now pins the .NET
#     implementation against "git hash-object" over a corpus, so the two cannot drift.
#   - stderr is drained asynchronously so a chatty git cannot deadlock a full pipe.
#
# A signed commit is safe here: git writes multi-line headers (gpgsig) with every
# continuation prefixed by a space, so the blank line inside an ASCII-armored signature is
# stored as " " and is not the header/body separator. The drill exercises that against a
# hand-built commit object carrying a gpgsig header.
function Invoke-GitBytes {
    param([string]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "git.exe"
    $psi.Arguments = $Arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    if ($script:GitDir) { $psi.WorkingDirectory = $script:GitDir }
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.BeginErrorReadLine()
    $ms = New-Object System.IO.MemoryStream
    $p.StandardOutput.BaseStream.CopyTo($ms)
    $p.WaitForExit()
    return , $ms.ToArray()
}

# git's blob id: SHA of "blob <len>NUL<content>". The algorithm follows the repository's
# object format so a SHA-256 repo is not silently judged with SHA-1 hashes it can never
# match. Pinned against "git hash-object" by the drill.
function Get-BlobId {
    param([byte[]]$Bytes)
    $hdr = [System.Text.Encoding]::ASCII.GetBytes("blob " + $Bytes.Length + [char]0)
    $all = New-Object byte[] ($hdr.Length + $Bytes.Length)
    [System.Array]::Copy($hdr, 0, $all, 0, $hdr.Length)
    [System.Array]::Copy($Bytes, 0, $all, $hdr.Length, $Bytes.Length)
    $alg = if ($script:ObjFormat -eq "sha256") { [System.Security.Cryptography.SHA256]::Create() }
           else { [System.Security.Cryptography.SHA1]::Create() }
    return (($alg.ComputeHash($all) | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Get-StoredMessageDigest {
    param([string]$Sha)
    $obj = Invoke-GitBytes -Arguments ("cat-file commit " + $Sha)
    if ($obj.Length -eq 0) { return "" }
    # the first LF LF ends the header block
    $split = -1
    for ($i = 0; $i -lt $obj.Length - 1; $i++) {
        if ($obj[$i] -eq 10 -and $obj[$i + 1] -eq 10) { $split = $i + 2; break }
    }
    if ($split -lt 0) { return "" }
    $msg = New-Object byte[] ($obj.Length - $split)
    [System.Array]::Copy($obj, $split, $msg, 0, $msg.Length)
    return (Get-BlobId $msg)
}

# NO Set-Location ANYWHERE in this script. PowerShell's current location is SESSION-scoped,
# so changing it here silently relocates whoever called us - which is not theoretical: an
# earlier draft did exactly that and broke an unrelated assertion in
# verify-merge-protocol.ps1 ("operator checkout still on its own branch"), because every
# later `git` in that run executed somewhere else. -RepoRoot is threaded into git via -C
# instead (see Invoke-GitLines), which cannot leak.

$common = (Invoke-GitLines @("rev-parse", "--git-common-dir") | Select-Object -First 1)
if (-not $common) {
    Write-Host "not a git repository (or git unavailable)" -ForegroundColor Red
    exit 2
}
if (-not [System.IO.Path]::IsPathRooted($common)) {
    # Relative to the repo we asked, not to wherever the caller happens to be standing.
    #
    # NOT named $base. PowerShell variable names are CASE-INSENSITIVE, so `$base` IS the
    # `$Base` parameter - an earlier version of this line silently overwrote the base ref
    # with a filesystem path, `rev-list "<path>..<branch>"` then matched nothing, and the
    # check reported "0 commits, all clean" on every branch. It only fired in the MAIN
    # checkout, where --git-common-dir returns a relative ".git"; worktrees return an
    # absolute path and skipped this block entirely, so every test passed. A guard that
    # vacuously passes is worse than no guard, and this one very nearly shipped.
    $commonRoot = if ($RepoRoot) { $RepoRoot } else { (Get-Location).Path }
    $common = Join-Path $commonRoot $common
}
$ledgerPath = Join-Path $common "hook-attest.log"
# One override, for the drill only: it needs to drive this against a controlled ledger
# (attested / unattested / absent) without touching the machine's real one. Deliberately a
# single named variable rather than a general "point anything anywhere" scheme - a knob
# nobody can enumerate is a knob that silently disables a guard.
if ($AllowLedgerOverride -and $env:AI_STACK_ATTEST_LEDGER) {
    $ledgerPath = $env:AI_STACK_ATTEST_LEDGER
}

# The ledger is append-only, one line per validated commit-to-be:
#     <tree> <message-digest> <iso-timestamp> <branch>
# Duplicates are expected (the same content and message committed twice) and harmless.
#
# TWO MAPS, because two eras of ledger line exist on a long-lived machine. Lines written
# before 2026-08-30 carry the TIMESTAMP in field 1, not a digest; they can support the tree
# rule and cannot support the pair rule. Field 1 being object-id-shaped is what separates
# them - not a date, which is exactly the caller-controlled thing this file refuses to gate
# on anywhere else.
$attested = @{}   # tree -> $true                (either era)
$pairs    = @{}   # "<tree> <msg>" -> $true      (v2 lines only)
$oidLen = 40
$script:ObjFormat = (Invoke-GitLines @("rev-parse", "--show-object-format") | Select-Object -First 1)
if ($script:ObjFormat -eq "sha256") { $oidLen = 64 }
$oidRe = "^[0-9a-f]{$oidLen}$"
if (Test-Path $ledgerPath) {
    foreach ($line in (Get-Content -Path $ledgerPath -ErrorAction SilentlyContinue)) {
        $f = @($line -split '\s+' | Where-Object { $_ })
        if ($f.Count -lt 1) { continue }
        if ($f[0] -notmatch $oidRe) { continue }
        $attested[$f[0]] = $true
        if ($f.Count -ge 2 -and $f[1] -match $oidRe) { $pairs[($f[0] + " " + $f[1])] = $true }
    }
}

# THE GUARD ACTIVATES PER BRANCH, from the branch's OWN hook.
#
# A branch whose attesting hook does not write attestations CANNOT produce them, so
# demanding them would fail every honest commit on it. That is not hypothetical: it is what
# happened the first time this ran - the merge-protocol drill creates its worktrees from the
# main checkout, so they carry the MERGED hook, and the guard flagged the drill's own honest
# work. A guard that fires on correct behaviour is worse than none, because it teaches people
# the alarm is noise.
#
# Reading the hook out of the BRANCH (not the working tree) also means this cannot be
# defeated by editing the file locally without committing it.
#
# WHICH FILE ATTESTS MOVED (2026-08-30). Attestation used to live in .githooks/pre-commit,
# and this gate read only that file. It is now the last block of .githooks/commit-msg -
# pre-commit ran BEFORE commit-msg, so a tree it attested survived a commit-msg refusal and
# the immediate `--no-verify` retry was waved through (.githooks/attest-lib.sh has the
# reproduction). Both files are consulted, so this gate stays true for branches on either
# side of that move rather than going silently INACTIVE - which, on a check whose whole job
# is to notice absence, would have been the worst possible failure mode.
$attestingHooks = @(".githooks/commit-msg", ".githooks/pre-commit")
$branchHook = ""
foreach ($h in $attestingHooks) {
    $branchHook += ((Invoke-GitLines @("show", "${Branch}:$h")) -join "`n") + "`n"
}
if ($branchHook -notmatch 'hook-attest\.log') {
    if ($Json) {
        [pscustomobject]@{ branch = $Branch; base = $Base; checked = 0; ledger = $ledgerPath
                           ledgerFound = (Test-Path $ledgerPath); inactive = $true
                           reason = "no attesting hook on branch"; unattested = @() } |
            ConvertTo-Json -Depth 5 -Compress
    } else {
        Write-Host "Hook attestation: INACTIVE for '$Branch'."
        Write-Host "  Neither .githooks/commit-msg nor .githooks/pre-commit on that branch records"
        Write-Host "  attestations, so its"
        Write-Host "  commits cannot have them. Nothing was bypassed - the mechanism is simply"
        Write-Host "  not present on this branch yet. It self-activates once the attesting hook"
        Write-Host "  is merged into the line these branches are cut from."
    }
    exit 0
}

# NO LEDGER = THE CHECK IS NOT ACTIVE YET, and it must say so rather than failing everything.
# A guard whose first act is to block every branch in the repo gets reverted within the hour,
# and deservedly - nothing was bypassed, the mechanism simply did not exist yet.
if (-not (Test-Path $ledgerPath)) {
    if ($Json) {
        [pscustomobject]@{ branch = $Branch; base = $Base; checked = 0; ledger = $ledgerPath
                           ledgerFound = $false; inactive = $true; unattested = @() } |
            ConvertTo-Json -Depth 5 -Compress
    } else {
        Write-Host "Hook attestation: INACTIVE - no ledger at $ledgerPath."
        Write-Host "  Nothing has been attested on this machine yet, so nothing can be judged."
        Write-Host "  It starts recording on the next commit that runs .githooks/commit-msg,"
        Write-Host "  which is the attester (it was .githooks/pre-commit before 2026-08-30)."
    }
    exit 0
}

# THE MERGE GATE ACTIVATES FROM THE FORK POINT, not from the branch tip.
#
# Reading it from the tip (which is what the pre-commit gate below does, correctly, for a
# hook that must exist WHEN EACH COMMIT IS MADE) would be wrong here: a branch can carry
# the merge hook at its tip and still contain merges made before it arrived, and flagging
# those would be the cry-wolf failure the old exemption was trying to avoid. The fork point
# is the moment the branch's own history begins, so a hook present THERE was available for
# every merge on the branch. It is committed history: not a date, not an environment
# variable, nothing the submitting party can arrange afterwards.
$forkPoint = (Invoke-GitLines @("merge-base", $Base, $Branch) | Select-Object -First 1)
$mergeHook = if ($forkPoint) { (Invoke-GitLines @("show", "${forkPoint}:.githooks/pre-merge-commit")) -join "`n" } else { "" }
$mergeGateActive = [bool]($mergeHook -match '\S')

# Commits on the branch that are NOT on the base - i.e. the work being submitted.
$revArgs = @("rev-list", "$Base..$Branch")
if (-not $mergeGateActive) { $revArgs = @("rev-list", "--no-merges", "$Base..$Branch") }
$revs = Invoke-GitLines $revArgs
$unattested = @()
$checked = 0

# THE MESSAGE HALF ACTIVATES PER COMMIT, FROM THE HOOK THAT COULD HAVE RUN FOR IT.
#
# The hook that validated commit C is the one in C's FIRST PARENT's tree - the commit that
# INTRODUCES a new attester is itself made by the old one. So the pair rule is demanded of a
# commit only when its parent already carried the v2 library. Anything older is judged by
# the tree rule alone, which is what it was able to produce.
#
# This is deliberately the same shape as the two activation gates above: read out of
# committed history, never out of the environment or a date. A submitter who wanted to
# escape the message rule this way would have to rewrite the parent chain - which changes
# every SHA on the branch, and is refused live by .githooks/reference-transaction anyway.
# THE TOKEN IS THE CONTRACT: .githooks/attest-lib.sh carries "ATTEST-FORMAT: v2" and the
# drill asserts that the shipped library still carries the literal string this looks for, so
# the two cannot drift into a silently-inactive gate.
$v2Cache = @{}
function Test-V2Attester {
    param([string]$Sha)
    $parent = (Invoke-GitLines @("rev-parse", "-q", "--verify", "$Sha^1") | Select-Object -First 1)
    if (-not $parent) { return $false }
    if ($v2Cache.ContainsKey($parent)) { return $v2Cache[$parent] }
    $lib = (Invoke-GitLines @("show", "${parent}:.githooks/attest-lib.sh")) -join "`n"
    $ok = [bool]($lib -match 'ATTEST-FORMAT:\s*v2')
    $v2Cache[$parent] = $ok
    return $ok
}

foreach ($sha in $revs) {
    if (-not $sha) { continue }
    $checked++
    $tree = (Invoke-GitLines @("rev-parse", "$sha^{tree}") | Select-Object -First 1)
    $why = "tree"
    if ($tree -and $attested.ContainsKey($tree)) {
        if (-not (Test-V2Attester $sha)) { continue }
        # v2: the tree is not enough. The commit must still SAY what was validated.
        $md = Get-StoredMessageDigest $sha
        if ($md -and $pairs.ContainsKey("$tree $md")) { continue }
        $why = "message"
    }

    # NO DATE-BASED EXEMPTION. There used to be one: commits whose committer date preceded
    # the ledger's first entry were skipped, so that a branch predating attestation was not
    # punished for the mechanism's start date. It was defeated by one environment variable -
    #     GIT_COMMITTER_DATE=2026-08-01 git commit --no-verify
    # produced a commit the check silently exempted. Committer date is caller-controlled;
    # it can never gate a guard against the caller.
    #
    # Nothing is lost by removing it, because the ADOPTION problem it was solving is
    # already handled better upstream: the per-branch activation gate skips any branch
    # whose own attesting hook (.githooks/commit-msg, or .githooks/pre-commit before the
    # 2026-08-30 move) cannot attest, and that hook is read from the BRANCH, not from the
    # environment. A branch that can attest has no excuse for a commit that is not attested.
    $subject = (Invoke-GitLines @("log", "-1", "--format=%s", $sha) | Select-Object -First 1)
    $parents = (Invoke-GitLines @("log", "-1", "--format=%P", $sha) | Select-Object -First 1)
    $isMerge = (($parents -split '\s+' | Where-Object { $_ }).Count -gt 1)
    $unattested += [pscustomobject]@{ Sha = $sha; Tree = $tree; Subject = $subject; IsMerge = $isMerge; Why = $why }
}

if ($Json) {
    [pscustomobject]@{
        branch      = $Branch
        base        = $Base
        checked     = $checked
        mergesChecked = $mergeGateActive
        ledger      = $ledgerPath
        ledgerFound = (Test-Path $ledgerPath)
        unattested  = @($unattested | ForEach-Object { @{ sha = $_.Sha; tree = $_.Tree; subject = $_.Subject; isMerge = $_.IsMerge; why = $_.Why } })
    } | ConvertTo-Json -Depth 5 -Compress
    exit ($(if ($unattested.Count) { 1 } else { 0 }))
}

Write-Host ("Hook attestation: {0} commit(s) on {1} not in {2} ({3})" -f $checked, $Branch, $Base, $(if ($mergeGateActive) { "merges included" } else { "merges skipped - no pre-merge-commit at the fork point" }))
Write-Host ("  ledger: {0}{1}" -f $ledgerPath, $(if (Test-Path $ledgerPath) { "" } else { "  (NOT FOUND)" }))
if ($unattested.Count -eq 0) {
    Write-Host "  [OK] every commit's content AND message were validated by the hooks." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host ("  [UNATTESTED] {0} commit(s) - the hooks did not validate this:" -f $unattested.Count) -ForegroundColor Red
foreach ($u in $unattested) {
    $what = if ($u.Why -eq "message") { " [MESSAGE REWRITTEN AFTER VALIDATION]" } else { "" }
    Write-Host ("    {0}{1}{2}  {3}" -f $u.Sha.Substring(0, 8), $(if ($u.IsMerge) { " (merge)" } else { "" }), $what, $u.Subject) -ForegroundColor Red
}
if ($unattested | Where-Object { $_.Why -eq "message" }) {
    Write-Host ""
    Write-Host "  A commit marked MESSAGE REWRITTEN has content the hooks DID validate, under a"
    Write-Host "  message they never saw - the shape of a --no-verify amend against an already"
    Write-Host "  attested tree. The message is what a reviewer reads to check a gitlink bump,"
    Write-Host "  so this is not cosmetic. Re-commit it so the hooks see the message that will"
    Write-Host "  actually be stored:"
    Write-Host ""
    Write-Host "    git commit --amend -c HEAD            # no --no-verify"
}
Write-Host ""
Write-Host "  The usual cause is `git commit --no-verify`. Never use it: the checks it skips"
Write-Host "  are the secret guard, the line-ending rule, the LLM-gateway routing rule and"
Write-Host "  the compose/ps1 structural check - each of which exists because it caught a"
Write-Host "  real failure. THE REMEDY is to re-commit the same content so the hooks run:"
Write-Host ""
Write-Host "    git commit --amend --no-edit          # for the tip commit"
Write-Host "    git rebase --exec 'git commit --amend --no-edit' $Base   # for several"
if ($unattested | Where-Object { $_.IsMerge }) {
    Write-Host ""
    # NO BACKTICKS IN THESE STRINGS. A backtick before the closing quote escapes it, the
    # string runs on into the next line, and the leftover words parse as a command - which
    # is how an earlier version of this block died with "parents: CommandNotFoundException"
    # while the file still tokenized clean. A parse check is not a behaviour check.
    Write-Host "  A MERGE commit above is a different cause with a different remedy. --amend"
    Write-Host "  works on it (parents are preserved), but do not rebase across it. A clean"
    Write-Host "  merge is validated by .githooks/pre-merge-commit, so an unattested one means"
    Write-Host "  'git merge --no-verify', or core.hooksPath was not set in that clone:"
    Write-Host ""
    Write-Host "    git config core.hooksPath .githooks   # then re-do the merge"
}
Write-Host ""
Write-Host "  If these commits predate attestation, or came from a clone without"
Write-Host "  core.hooksPath set, say so explicitly in your submission rather than"
Write-Host "  re-writing history you did not author."
exit 1
