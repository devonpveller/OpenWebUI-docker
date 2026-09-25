# Simple line ending validator for git pre-commit hook
#
# Validates that GIT-TRACKED shell scripts use Unix (LF) line endings.
# Only tracked files are checked: vendored / gitignored dependency clones
# (e.g. OB1/) legitimately contain CRLF shell scripts on Windows checkouts
# and are not this repo's concern - scanning them blocked every commit.

$ProjectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$HasErrors = $false

Write-Host "Checking line endings (git-tracked *.sh)..." -ForegroundColor Cyan

# Ask git for tracked shell scripts - respects .gitignore, skips vendored trees.
Push-Location $ProjectDir
try {
    $Tracked = & git ls-files '*.sh' 2>$null
    $LsRc = $LASTEXITCODE
} finally {
    Pop-Location
}

# FAIL CLOSED (ac-hooks-portable2, review blocker 1). When git cannot read this
# repository (WSL's Windows git on a Linux checkout: "dubious ownership"; a wrong
# GIT_DIR), `ls-files` returns nothing and exits non-zero - and this check used to
# print "No tracked shell scripts to check" and pass. A failed query is not an answer.
if ($LsRc -ne 0) {
    Write-Host "FAILED: 'git ls-files' exited $LsRc - cannot tell which shell scripts are tracked" -ForegroundColor Red
    exit 1
}

if (-not $Tracked) {
    Write-Host "SUCCESS: No tracked shell scripts to check" -ForegroundColor Green
    exit 0
}

foreach ($Rel in $Tracked) {
    $Full = Join-Path $ProjectDir $Rel
    if (-not (Test-Path $Full)) { continue }
    try {
        $Content = Get-Content $Full -Raw -ErrorAction Stop
    } catch {
        # A tracked script that exists but cannot be read was not checked: refuse.
        Write-Host "FAILED: cannot read $Rel ($($_.Exception.Message))" -ForegroundColor Red
        exit 1
    }
    if ($Content -and $Content -match "`r`n") {
        Write-Host "ERROR: Windows line endings found in: $Rel" -ForegroundColor Red
        $HasErrors = $true
    }
}

if (-not $HasErrors) {
    Write-Host "SUCCESS: All tracked shell scripts have Unix line endings" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAILED: Line ending validation failed" -ForegroundColor Red
    exit 1
}
