#requires -Version 5
<#
.SYNOPSIS
  Gate 5d. When an OB1 gitlink bump changes an integration that ships as a
  Docker image, prove that the image BUILDS and that its entrypoint's module
  graph RESOLVES - at the commit, where gates 5b/5c already stand.

.DESCRIPTION
  Why this exists (2026-09-06): OB1/integrations/research-curator crash-looped
  in production a day after its gitlink bump landed. index.ts had gained
  `import { ResilientPool } from "./pool.ts"`, and the Dockerfile COPYs its
  sources BY NAME - index.ts, claims.ts, two operator scripts - so pool.ts was
  never in the image. `docker build` succeeded (COPY of the listed files
  cannot fail), every gate in this chain passed (none of them looks at a
  Dockerfile), and the defect surfaced as "Module not found" at CONTAINER
  START, after the deploy. research-service had shipped the same defect
  earlier (contract.ts / skeptic.ts, see its Dockerfile's own comment) and
  fixed it locally with a glob COPY plus `RUN deno check index.ts`; nothing
  carried that lesson to the sibling Dockerfiles, and nothing was in a
  position to.

  What it does, in order:
    1. HOOK MODE (no -OldPin/-NewPin): if no OB1 gitlink is staged, exit 0
       without touching git objects, docker or deno. Staged pin from the
       index (`git ls-files -s OB1`), old pin from HEAD:OB1, uninitialised-
       submodule guard, and the staged==disk clause, exactly as 5b/5c.
       BY-PIN MODE (-OldPin X -NewPin Y): both must resolve to commits in the
       OB1 clone; the staged/disk clauses are skipped, and everything below
       reads git objects AT the pins, never the working tree - so a tester's
       RED/GREEN by hand is honest even when disk != pin.
    2. `git -C OB1 diff --name-only <old> <new> -- integrations/`. Every
       changed `integrations/<svc>/` that carries a Dockerfile AT THE NEW PIN
       is a candidate. None -> one skip line, exit 0 (a recipes-only bump).
    3. STATIC HALF, no docker: for each candidate whose Dockerfile COPYs
       files BY NAME, walk the relative-import graph out from the entrypoint
       (the last *.ts in the Dockerfile's CMD; index.ts by default) and
       refuse if any reachable file is not covered by a COPY - naming the
       Dockerfile, the missing file, the importing file:line and the fix.
       A Dockerfile that copies by glob (`COPY *.ts ./`, `COPY . .`) cannot
       drift this way, so the static half is skipped for it. Finishes in
       seconds; every static failure across all candidates is reported before
       any build starts.
    4. BUILD HALF: docker must be reachable (else REFUSE). For each candidate,
       `git archive` the service directory AT THE NEW PIN into a temp dir (the
       context is the pinned tree, not the disk), `docker build` it under the
       throwaway tag ob1-gate/<svc>:<sha7>, then `docker run --rm --network
       none` the image with `deno check <entrypoint>`. The tag and the temp
       dir are removed either way. A build or check failure refuses the
       commit quoting the last 20 lines of the log.

  A MERGE COMMIT GETS THIS TOO: .githooks/pre-merge-commit execs pre-commit,
  so a clean merge that bumps the gitlink runs 5d against the merged index
  (verified once on 2026-09-06 by reading pre-merge-commit: it is a one-line
  `exec "$(dirname "$0")/pre-commit"`).

  WHAT A GREEN DOES NOT PROVE - read a pass as "the image builds and its
  module graph resolves", never as "the service works":
    * Nothing here starts the service against a real database, a real
      message bus, or the LiteLLM gateway. `deno check` type-checks; it does
      not execute index.ts.
    * Env wiring is unseen: a missing OPENBRAIN_* / CHAT_API_KEY / DATABASE_URL
      in OB1/docker/.env or docker-compose.yml fails at runtime exactly as
      before. The J.1 "Bearer not-needed" class of defect is invisible here.
    * Compose changes are unseen: this gate reads Dockerfiles under
      integrations/, not docker/docker-compose.yml. A renamed image, a dropped
      volume or a changed command line is out of its sight.
    * The static half follows RELATIVE imports only. Bare specifiers
      ("postgres"), jsr:/npm:/https: imports and anything resolved through
      deno.json's import map are left to `deno check` in the image.
    * The tests of the service are NOT run. 5b runs *.test.mjs for recipes;
      no gate runs integration test suites.

  DELIBERATE LIMITS, stated so a green is not read as more than it is:

  * SCOPE IS THE PIN DIFF, NOT EVERY DOCKERFILE. Only integrations whose
    directory appears in the old->new diff are checked. A curator-shaped
    Dockerfile that is already broken but UNTOUCHED by this bump stays
    unrefused here (linting all eight regardless of the diff is Phase D
    hardening, deliberately out of scope). The corollary is deliberate too:
    when the OLD pin is not an object in this OB1 clone (each worktree has
    its own clone; the object may simply not be fetched), the diff cannot be
    narrowed and this gate checks EVERY image-bearing integration at the new
    pin rather than none - loud over-check, never silent under-check.
  * THE STATIC HALF IS AN APPROXIMATION and the build half is the authority.
    It treats every non-`--from` COPY/ADD source across all stages as
    covering, treats a directory source as covering everything beneath it,
    does not model a COPY that RENAMES its destination, and reads import
    specifiers with a regex (`from "./x.ts"`, `import "./x.ts"`,
    `import("./x.ts")`). It exists so that the curator class is refused in
    seconds with the importing line named; anything it cannot see, `deno
    check` inside the built image sees.
  * THE BUILD HALF NEEDS DOCKER AND, ON A COLD LAYER CACHE, THE NETWORK:
    every integration Dockerfile runs `deno install` against deno.json, which
    downloads on the first build of that layer. With the base image and the
    dependency layer cached (the normal state on the host that deploys these
    images) a build is a few seconds. A cold cache turns this gate red on
    innocent work only if the network is down - loud, on one operator, and
    said plainly in the log it quotes.
  * THE CONTAINER RUNS WITH --network none and is never attached to an
    ai-stack_* network; the image is never tagged :local. The throwaway tag
    is ob1-gate/<svc>:<sha7> and is removed after the check, pass or fail.

  WHEN docker IS UNREACHABLE this gate REFUSES the commit; it does not warn
  and pass. The state this file was written to end is "nothing checks
  whether these images build", and a gate that switches itself off when its
  tool is missing reproduces that state quietly. 5b makes the same call for
  node, 5c for deno.

  Exit code 0 = clean or not applicable, 1 = refuse.

.EXAMPLE
  powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1
  # hook mode: skips unless an OB1 gitlink bump is staged

.EXAMPLE
  powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin a07103b
  # RED against the real incident pin: names research-curator/Dockerfile, pool.ts, index.ts:39
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$OldPin,
    [string]$NewPin
)

$ErrorActionPreference = 'Stop'
$Tag = '[check-ob1-integration-images]'
$EmptyTree = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

function Fail([string]$msg) {
    Write-Host "$Tag FAIL: $msg" -ForegroundColor Red
    exit 1
}

# Query the OB1 SUBMODULE repo with a clean git environment. Under a hook, git
# exports GIT_DIR (absolute, the PARENT repo - in a linked worktree its admin
# dir) and GIT_INDEX_FILE, and those OVERRIDE `-C`: `git -C OB1 rev-parse HEAD`
# then answers with the PARENT's HEAD (5b's first live run, 2026-09-03).
# Interactive runs never see it - only hook runs do - which is why this gate's
# verdicts are proven with a real `git commit` in the test plan, not only by
# invoking this file by hand.
function Git-InOB1([string[]]$GitArgs) {
    $saved = @{}
    foreach ($k in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k)
        [Environment]::SetEnvironmentVariable($k, $null)
    }
    try {
        # PS 5.1 under $ErrorActionPreference='Stop' THROWS a NativeCommandError the
        # moment a redirected native command writes to stderr. Relax it for the
        # native call only; function-scoped.
        $ErrorActionPreference = 'Continue'
        & git -C (Join-Path $Root 'OB1') @GitArgs 2>$null
    }
    finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

# Run a native command capturing stdout+stderr as plain strings and its exit
# code. deno and docker write diagnostics to STDERR; with `2>&1` PowerShell
# hands ErrorRecord objects, and "$_" on a blank one renders as the literal
# "System.Management.Automation.RemoteException" (5c's first live run). Unwrap.
function Invoke-Native([string]$Exe, [string[]]$CmdArgs) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & $Exe @CmdArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prevEap }
    $lines = @(foreach ($o in $raw) {
        if ($o -is [System.Management.Automation.ErrorRecord]) { $o.Exception.Message }
        else { [string]$o }
    })
    [pscustomobject]@{ Code = $code; Lines = $lines }
}

if (-not $Root) {
    $Root = (& git rev-parse --show-toplevel 2>$null)
    if (-not $Root) { Fail "not inside a git repository and no -Root given" }
}
$Root = (Resolve-Path $Root).Path
$ob1Dir = Join-Path $Root 'OB1'

# --- 1. Which two pins? -----------------------------------------------------
$byPin = [bool]($OldPin -or $NewPin)
if ($byPin -and -not ($OldPin -and $NewPin)) {
    Fail "-OldPin and -NewPin go together (got OldPin='$OldPin' NewPin='$NewPin')."
}

if (-not $byPin) {
    $staged = @(& git -C $Root diff --cached --name-only)
    if ($staged -notcontains 'OB1') {
        Write-Host "$Tag no OB1 gitlink staged - skipped."
        exit 0
    }
    $lsLine = (& git -C $Root ls-files -s OB1) | Select-Object -First 1
    if (-not $lsLine -or $lsLine -notmatch '^160000\s+([0-9a-f]{40})\s') {
        Fail "could not read the staged OB1 gitlink from the index (got '$lsLine')"
    }
    $newSha = $Matches[1]
}

# An UNINITIALIZED submodule is an empty dir with no .git - and `git -C` on it
# does not fail, it walks UP and answers from the PARENT repo. Catch it before
# any git query, in both modes: by-pin mode still needs the OB1 objects.
if (-not (Test-Path (Join-Path $ob1Dir '.git'))) {
    Fail ("OB1/ has no .git - the submodule is not initialized, so there is no OB1 object " +
          "store to read Dockerfiles and sources from. Run: git submodule update --init OB1")
}

if ($byPin) {
    $newSha = (Git-InOB1 @('rev-parse', '-q', '--verify', "$NewPin^{commit}"))
    if (-not $newSha -or $newSha -notmatch '^[0-9a-f]{40}$') {
        Fail "-NewPin '$NewPin' is not a commit in the OB1 clone at $ob1Dir."
    }
    $oldSha = (Git-InOB1 @('rev-parse', '-q', '--verify', "$OldPin^{commit}"))
    if (-not $oldSha -or $oldSha -notmatch '^[0-9a-f]{40}$') {
        Fail "-OldPin '$OldPin' is not a commit in the OB1 clone at $ob1Dir."
    }
}
else {
    # The tree on disk must BE the tree being pinned. This gate reads git
    # objects, not the disk, so it would be honest either way - but the gates
    # beside it read the disk, and a bump whose disk is elsewhere is the
    # mismatch 5b/5c refuse; refusing it here too keeps one story.
    $diskSha = (Git-InOB1 @('rev-parse', 'HEAD'))
    if (-not $diskSha) { Fail "OB1/ has no readable git HEAD - is the submodule initialized?" }
    if ($newSha -ne $diskSha) {
        Fail ("staged OB1 gitlink is $($newSha.Substring(0,7)) but the OB1 working tree is at " +
              "$($diskSha.Substring(0,7)). Align them first (git -C OB1 checkout " +
              "$($newSha.Substring(0,7)), or re-stage: git add OB1).")
    }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $oldSha = (& git -C $Root rev-parse -q --verify 'HEAD:OB1' 2>$null)
    $ErrorActionPreference = $prevEap
    if (-not $oldSha -or $oldSha -notmatch '^[0-9a-f]{40}$') {
        Write-Host ("$Tag HEAD has no OB1 gitlink (first pin) - comparing against the empty " +
            "tree, so every image-bearing integration at the new pin is checked.") -ForegroundColor Yellow
        $oldSha = $EmptyTree
    }
}
$new7 = $newSha.Substring(0, 7)
$old7 = $oldSha.Substring(0, 7)

# --- 2. Which image-bearing integrations changed? --------------------------
# core.quotepath=false: a non-ASCII path would otherwise come back C-quoted
# and fall out of the `integrations/<svc>/` match below (5b, 2026-09-04).
$oldKnown = $true
if (-not $byPin -and $oldSha -ne $EmptyTree) {
    # HEAD:OB1 is a parent-repo fact; whether the OB1 clone HAS that commit is
    # a separate question (by-pin mode already answered it with rev-parse).
    $null = Git-InOB1 @('cat-file', '-e', "$oldSha^{commit}")
    if ($LASTEXITCODE -ne 0) { $oldKnown = $false }
}
if (-not $oldKnown) {
    Write-Host ("$Tag WARNING: old pin $old7 is not an object in the OB1 clone (not fetched " +
        "here), so the diff cannot be narrowed. Checking EVERY image-bearing integration at " +
        "$new7 instead of none. 'git -C OB1 fetch' makes this exact again.") -ForegroundColor Yellow
    $changed = @(Git-InOB1 @('-c', 'core.quotepath=false', 'ls-tree', '-r', '--name-only', $newSha, '--', 'integrations/'))
}
else {
    $changed = @(Git-InOB1 @('-c', 'core.quotepath=false', 'diff', '--name-only', $oldSha, $newSha, '--', 'integrations/'))
    if ($LASTEXITCODE -ne 0) { Fail "git diff $old7..$new7 -- integrations/ failed in the OB1 clone." }
}

$svcNames = @{}
foreach ($p in $changed) {
    if ($p -match '^integrations/([^/]+)/') { $svcNames[$Matches[1]] = $true }
}
$candidates = @()
foreach ($svc in @($svcNames.Keys | Sort-Object)) {
    $null = Git-InOB1 @('cat-file', '-e', "${newSha}:integrations/$svc/Dockerfile")
    if ($LASTEXITCODE -eq 0) { $candidates += $svc }
}
if ($candidates.Count -eq 0) {
    Write-Host ("$Tag OB1 $old7..$new7 changes $($changed.Count) path(s) under integrations/, " +
        "none in a directory that carries a Dockerfile at $new7 - no image to build, skipped.")
    exit 0
}
Write-Host ("$Tag OB1 $old7..$new7 touches image-bearing integration(s): " +
    ($candidates -join ', '))

# --- 3. STATIC HALF -----------------------------------------------------------
function Read-AtPin([string]$RelPath) {
    $lines = @(Git-InOB1 @('show', "${newSha}:$RelPath"))
    if ($LASTEXITCODE -ne 0) { return $null }
    return $lines
}

function Join-Continuations([string[]]$Lines) {
    $out = @(); $acc = ''
    foreach ($l in $Lines) {
        if ($l -match '^\s*#') { continue }
        if ($l -match '\\\s*$') { $acc += ($l -replace '\\\s*$', ' '); continue }
        $out += ($acc + $l); $acc = ''
    }
    if ($acc) { $out += $acc }
    return $out
}

function Normalize-Rel([string]$Path) {
    $parts = @()
    foreach ($seg in (($Path -replace '\\', '/') -split '/')) {
        if ($seg -eq '' -or $seg -eq '.') { continue }
        if ($seg -eq '..') {
            if ($parts.Count -gt 0 -and $parts[-1] -ne '..') {
                if ($parts.Count -eq 1) { $parts = @() } else { $parts = @($parts[0..($parts.Count - 2)]) }
            }
            else { $parts += '..' }
            continue
        }
        $parts += $seg
    }
    return ($parts -join '/')
}

# Returns @{ Glob; Files; Dirs; Entry; EntryFrom } for one Dockerfile.
function Parse-Dockerfile([string[]]$Raw, [string[]]$TreePaths) {
    $res = @{ Glob = $false; Files = @(); Dirs = @(); Entry = 'index.ts'; EntryFrom = 'default' }
    foreach ($line in (Join-Continuations $Raw)) {
        if ($line -match '^\s*CMD\s+(\[.*\])\s*$') {
            $cmdArgs = @([regex]::Matches($Matches[1], '"([^"]*)"') | ForEach-Object { $_.Groups[1].Value })
            $ts = @($cmdArgs | Where-Object { $_ -match '\.(ts|js|mjs|tsx)$' })
            if ($ts.Count -gt 0) { $res.Entry = ($ts[-1] -replace '^\./', ''); $res.EntryFrom = 'CMD' }
            continue
        }
        if ($line -notmatch '^\s*(COPY|ADD)\s+(.*)$') { continue }
        $rest = $Matches[2].Trim()
        if ($rest -match '--from=') { continue }   # from another stage, not the context
        if ($rest.StartsWith('[')) {
            $copyArgs = @([regex]::Matches($rest, '"([^"]*)"') | ForEach-Object { $_.Groups[1].Value })
        }
        else {
            $copyArgs = @($rest -split '\s+' | Where-Object { $_ -ne '' })
        }
        $copyArgs = @($copyArgs | Where-Object { -not $_.StartsWith('--') })
        if ($copyArgs.Count -lt 2) { continue }
        $sources = @($copyArgs[0..($copyArgs.Count - 2)])
        foreach ($s in $sources) {
            if ($s -match '^[a-z]+://') { continue }   # ADD <url>
            $n = Normalize-Rel $s
            if ($n -eq '' -or $s -match '[\*\?\[]') { $res.Glob = $true; continue }
            $isDir = $false
            foreach ($t in $TreePaths) { if ($t.StartsWith("$n/")) { $isDir = $true; break } }
            if ($isDir) { $res.Dirs += $n } else { $res.Files += $n }
        }
    }
    return $res
}

# import ... from "./x.ts" | import "./x.ts" | import("./x.ts"); also export ... from.
$importRx = [regex]'(?:\bfrom\s+["'']([^"'']+)["'']|^\s*import\s+["'']([^"'']+)["'']|\bimport\s*\(\s*["'']([^"'']+)["'']\s*\))'

# Walk relative imports out from the entrypoint. Returns a list of
# @{ File; Importer; Line; Exists; Escapes } - one entry per distinct file.
function Walk-Imports([string]$Svc, [string]$Entry, [string[]]$TreePaths) {
    $treeSet = @{}
    foreach ($t in $TreePaths) { $treeSet[$t] = $true }
    $seen = @{}
    $found = @()
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@{ File = $Entry; Importer = '(entrypoint)'; Line = 0 })
    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue()
        $f = $item.File
        if ($seen.ContainsKey($f)) { continue }
        $seen[$f] = $true
        $escapes = $f.StartsWith('..')
        $exists = (-not $escapes) -and $treeSet.ContainsKey($f)
        $found += @{ File = $f; Importer = $item.Importer; Line = $item.Line; Exists = $exists; Escapes = $escapes }
        if (-not $exists) { continue }
        if ($f -notmatch '\.(ts|tsx|js|mjs)$') { continue }
        $src = Read-AtPin "integrations/$Svc/$f"
        if ($null -eq $src) { continue }
        $dir = ''
        if ($f.Contains('/')) { $dir = $f.Substring(0, $f.LastIndexOf('/')) }
        for ($i = 0; $i -lt $src.Count; $i++) {
            foreach ($m in $importRx.Matches($src[$i])) {
                $spec = $m.Groups[1].Value
                if (-not $spec) { $spec = $m.Groups[2].Value }
                if (-not $spec) { $spec = $m.Groups[3].Value }
                if (-not ($spec.StartsWith('./') -or $spec.StartsWith('../'))) { continue }
                $spec = $spec -replace '[?#].*$', ''
                if ($dir) { $target = Normalize-Rel "$dir/$spec" } else { $target = Normalize-Rel $spec }
                $queue.Enqueue(@{ File = $target; Importer = $f; Line = ($i + 1) })
            }
        }
    }
    return $found
}

$staticFailures = @()
$plans = @()
foreach ($svc in $candidates) {
    $dockerfileRel = "integrations/$svc/Dockerfile"
    $dfLines = Read-AtPin $dockerfileRel
    if ($null -eq $dfLines) { Fail "could not read OB1 ${new7}:$dockerfileRel (git show failed)." }
    $treePaths = @(Git-InOB1 @('-c', 'core.quotepath=false', 'ls-tree', '-r', '--name-only', $newSha, '--', "integrations/$svc/") |
        ForEach-Object { $_.Substring("integrations/$svc/".Length) })
    $df = Parse-Dockerfile $dfLines $treePaths
    $plans += @{ Svc = $svc; Entry = $df.Entry }

    if ($df.Glob) {
        Write-Host ("$Tag static: $dockerfileRel copies by glob/directory - a name list cannot " +
            "drift, static half skipped (the build half decides). entry=$($df.Entry)")
        continue
    }
    $reach = Walk-Imports $svc $df.Entry $treePaths
    $covered = 0
    $before = $staticFailures.Count
    foreach ($r in $reach) {
        if ($r.Escapes) {
            Write-Host ("$Tag static: $svc/$($r.Importer):$($r.Line) imports '$($r.File)', OUTSIDE " +
                "the build context; the static half cannot see it - the build half decides.") -ForegroundColor Yellow
            continue
        }
        $isCovered = $false
        if ($df.Files -contains $r.File) { $isCovered = $true }
        else { foreach ($d in $df.Dirs) { if ($r.File.StartsWith("$d/")) { $isCovered = $true; break } } }
        if (-not $r.Exists) {
            $staticFailures += ("OB1 ${new7}:$dockerfileRel - $svc/$($r.Importer):$($r.Line) imports " +
                "'$($r.File)', which DOES NOT EXIST at $new7 (no COPY can cover it).")
        }
        elseif (-not $isCovered) {
            $staticFailures += ("OB1 ${new7}:$dockerfileRel - $svc/$($r.Importer):$($r.Line) imports " +
                "'$($r.File)', and the Dockerfile never COPYs it (it lists: " +
                ((@($df.Files) + @($df.Dirs)) -join ', ') + "). The image would start and die on " +
                "'Module not found'. FIX in OB1: add 'COPY $($r.File) ./' to $dockerfileRel, or " +
                "copy by glob ('COPY *.ts ./' + 'RUN rm -f *.test.ts') as research-service does.")
        }
        else { $covered++ }
    }
    if ($staticFailures.Count -eq $before) {
        Write-Host ("$Tag static: $dockerfileRel covers all $covered file(s) reachable from " +
            "$($df.Entry) by relative import.")
    }
}
if ($staticFailures.Count -gt 0) {
    foreach ($m in $staticFailures) { Write-Host "  $m" -ForegroundColor Red }
    Fail ("$($staticFailures.Count) relative import(s) reachable from an entrypoint are not in " +
          "their image (OB1 $old7 -> $new7). This is the research-curator crash-loop class: the " +
          "build succeeds and the container dies at start. No docker build was attempted; fix the " +
          "Dockerfile(s) above in OB1, push, re-stage the gitlink, and commit again.")
}

# --- 4. BUILD HALF -----------------------------------------------------------
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Fail ("docker is not on PATH, so the image(s) for " + ($candidates -join ', ') + " cannot be " +
          "built. This gate REFUSES rather than skipping: 'nothing checks whether these images " +
          "build' is the state it was written to end.")
}
$ping = Invoke-Native $dockerCmd.Source @('version', '--format', '{{.Server.Version}}')
if ($ping.Code -ne 0 -or -not ($ping.Lines -join '')) {
    Fail ("Docker is not reachable (docker version failed: " + (($ping.Lines | Select-Object -First 2) -join ' ') +
          "), so the image(s) for " + ($candidates -join ', ') + " cannot be built. This gate " +
          "REFUSES rather than warning and passing - start Docker Desktop, or commit from a " +
          "host that can build.")
}

function Show-Tail([string[]]$Lines, [int]$N) {
    $n = [Math]::Min($N, $Lines.Count)
    if ($n -le 0) { return }
    Write-Host "  --- last $n of $($Lines.Count) log line(s) ---" -ForegroundColor Red
    $Lines[($Lines.Count - $n)..($Lines.Count - 1)] | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
}

foreach ($plan in $plans) {
    $svc = $plan.Svc
    $entry = $plan.Entry
    $safe = ($svc.ToLowerInvariant() -replace '[^a-z0-9._-]', '-')
    $image = "ob1-gate/${safe}:$new7"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ob1-gate-$safe-$new7-" + [IO.Path]::GetRandomFileName().Replace('.', ''))
    $verdict = $null
    $log = @()
    try {
        New-Item -ItemType Directory -Path $tmp -Force | Out-Null
        $zip = Join-Path $tmp 'ctx.zip'
        # The build context is the tree AT THE PIN, never the working tree: in by-pin
        # mode disk may be elsewhere, and in hook mode disk == pin is already proven.
        $null = Git-InOB1 @('archive', '--format=zip', '-o', $zip, $newSha, "integrations/$svc")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zip)) {
            $verdict = "git archive of integrations/$svc at $new7 failed - cannot assemble a build context."
        }
        else {
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $ctx = Join-Path $tmp ("integrations\" + $svc)
            if (-not (Test-Path (Join-Path $ctx 'Dockerfile'))) {
                $verdict = "archive of integrations/$svc at $new7 has no Dockerfile after extraction ($ctx)."
            }
            else {
                $sw = [Diagnostics.Stopwatch]::StartNew()
                $b = Invoke-Native $dockerCmd.Source @('build', '--progress=plain', '-t', $image, $ctx)
                $log = $b.Lines
                if ($b.Code -ne 0) {
                    $verdict = "docker build of $image (integrations/$svc at $new7) FAILED (exit $($b.Code)) after $([int]$sw.Elapsed.TotalSeconds)s."
                }
                else {
                    $c = Invoke-Native $dockerCmd.Source @('run', '--rm', '--network', 'none', '-e', 'NO_COLOR=1', $image, 'deno', 'check', $entry)
                    $log = $c.Lines
                    if ($c.Code -ne 0) {
                        $verdict = ("'deno check $entry' inside $image FAILED (exit $($c.Code)): the image builds " +
                                    "but its module graph does not resolve - this is what the container would " +
                                    "die on at start.")
                    }
                    else {
                        Write-Host ("$Tag build: $image built and 'deno check $entry' resolved inside it " +
                            "($([int]$sw.Elapsed.TotalSeconds)s); tag removed.")
                    }
                }
            }
        }
    }
    finally {
        $null = Invoke-Native $dockerCmd.Source @('rmi', '-f', $image)
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    }
    if ($verdict) {
        Show-Tail $log 20
        Fail ("$verdict Fix it in OB1 (integrations/$svc), push, re-stage the gitlink, and commit " +
              "again. Reproduce by hand: docker build integrations/$svc, then docker run --rm <img> " +
              "deno check $entry.")
    }
}

Write-Host ("$Tag OK - $($plans.Count) integration image(s) build and resolve at OB1 $new7 (" +
    (@($plans | ForEach-Object { $_.Svc }) -join ', ') + "). Image builds + module graph only - " +
    "no service was started, no env or compose wiring was checked.")
exit 0
