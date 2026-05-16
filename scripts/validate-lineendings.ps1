# Simple line ending validator for git pre-commit hook
#
# Validates that GIT-TRACKED shell scripts use Unix (LF) line endings.
# Only tracked files are checked: vendored / gitignored dependency clones
# (e.g. OB1/) legitimately contain CRLF shell scripts on Windows checkouts
# and are not this repo's concern — scanning them blocked every commit.

$ProjectDir = Split-Path -Parent $PSScriptRoot
$HasErrors = $false

Write-Host "Checking line endings (git-tracked *.sh)..." -ForegroundColor Cyan

# Ask git for tracked shell scripts — respects .gitignore, skips vendored trees.
Push-Location $ProjectDir
try {
    $Tracked = & git ls-files '*.sh' 2>$null
} finally {
    Pop-Location
}

if (-not $Tracked) {
    Write-Host "SUCCESS: No tracked shell scripts to check" -ForegroundColor Green
    exit 0
}

foreach ($Rel in $Tracked) {
    $Full = Join-Path $ProjectDir $Rel
    if (-not (Test-Path $Full)) { continue }
    $Content = Get-Content $Full -Raw
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
