# Simple line ending validator for git pre-commit hook

$ProjectDir = Split-Path -Parent $PSScriptRoot
$HasErrors = $false

Write-Host "Checking line endings..." -ForegroundColor Cyan

# Check shell scripts
$ShellFiles = Get-ChildItem -Path $ProjectDir -Filter "*.sh" -Recurse | Where-Object { $_.FullName -notmatch "\.git" }

foreach ($File in $ShellFiles) {
    $Content = Get-Content $File.FullName -Raw
    if ($Content -and $Content -match "`r`n") {
        Write-Host "ERROR: Windows line endings found in: $($File.Name)" -ForegroundColor Red
        $HasErrors = $true
    }
}

if (-not $HasErrors) {
    Write-Host "SUCCESS: All shell scripts have Unix line endings" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAILED: Line ending validation failed" -ForegroundColor Red
    exit 1
}