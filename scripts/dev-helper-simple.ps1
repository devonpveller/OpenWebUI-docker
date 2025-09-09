# Development Helper Script for AI Stack
# Prevents common Windows/Docker issues

[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("validate", "fix-lineendings", "rebuild", "full-check")]
    [string]$Action = "validate"
)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$PROJECT_DIR = Split-Path -Parent $SCRIPT_DIR

function Write-ColorOutput {
    param([string]$Message, [string]$Color = "White")
    Write-Host $Message -ForegroundColor $Color
}

function Test-LineEndings {
    Write-ColorOutput "Checking line endings..." "Cyan"
    
    $Issues = @()
    $ShellFiles = Get-ChildItem -Path $PROJECT_DIR -Filter "*.sh" -Recurse | Where-Object { $_.FullName -notmatch "\.git" }
    
    foreach ($File in $ShellFiles) {
        $Content = Get-Content $File.FullName -Raw
        if ($Content -match "`r`n") {
            $Issues += $File.Name
        }
    }
    
    if ($Issues.Count -gt 0) {
        Write-ColorOutput "ERROR: Found Windows line endings in: $($Issues -join ', ')" "Red"
        return $false
    } else {
        Write-ColorOutput "SUCCESS: All shell scripts have Unix line endings" "Green"
        return $true
    }
}

function Repair-LineEndings {
    Write-ColorOutput "Fixing line endings..." "Yellow"
    
    $ShellFiles = Get-ChildItem -Path $PROJECT_DIR -Filter "*.sh" -Recurse | Where-Object { $_.FullName -notmatch "\.git" }
    
    foreach ($File in $ShellFiles) {
        $Content = Get-Content $File.FullName -Raw
        if ($Content -match "`r`n") {
            Write-ColorOutput "  Fixing: $($File.Name)" "Yellow"
            $Content -replace "`r`n", "`n" | Set-Content $File.FullName -NoNewline
        }
    }
    
    Write-ColorOutput "SUCCESS: Line endings fixed" "Green"
}

function Test-EntrypointShebang {
    Write-ColorOutput "Checking entrypoint.sh shebang..." "Cyan"
    
    $EntrypointPath = Join-Path $PROJECT_DIR "entrypoint.sh"
    if (Test-Path $EntrypointPath) {
        $FirstLine = Get-Content $EntrypointPath -First 1
        if ($FirstLine -eq "#!/bin/sh") {
            Write-ColorOutput "SUCCESS: Entrypoint shebang is correct" "Green"
            return $true
        } else {
            Write-ColorOutput "ERROR: Entrypoint shebang is incorrect: '$FirstLine'" "Red"
            Write-ColorOutput "   Should be: #!/bin/sh" "Yellow"
            return $false
        }
    } else {
        Write-ColorOutput "ERROR: entrypoint.sh not found" "Red"
        return $false
    }
}

# Main execution
Set-Location $PROJECT_DIR

if ($Action -eq "validate") {
    Write-ColorOutput "Running validation checks..." "Cyan"
    $AllGood = $true
    
    if (-not (Test-LineEndings)) { $AllGood = $false }
    if (-not (Test-EntrypointShebang)) { $AllGood = $false }
    
    if ($AllGood) {
        Write-ColorOutput "`nAll validations passed!" "Green"
    } else {
        Write-ColorOutput "`nSome validations failed. Run with -Action fix-lineendings to auto-fix." "Red"
        exit 1
    }
}
elseif ($Action -eq "fix-lineendings") {
    Repair-LineEndings
    Test-LineEndings | Out-Null
}
elseif ($Action -eq "rebuild") {
    Write-ColorOutput "Rebuilding Tailscale container..." "Yellow"
    docker compose build --no-cache tailscale
    if ($LASTEXITCODE -eq 0) {
        Write-ColorOutput "SUCCESS: Tailscale container rebuilt" "Green"
    } else {
        Write-ColorOutput "ERROR: Failed to rebuild Tailscale container" "Red"
        exit 1
    }
}
elseif ($Action -eq "full-check") {
    Write-ColorOutput "Running full development check..." "Cyan"
    
    # Fix any line ending issues
    if (-not (Test-LineEndings)) {
        Repair-LineEndings
    }
    
    # Validate everything
    $AllGood = $true
    if (-not (Test-EntrypointShebang)) { $AllGood = $false }
    
    # Rebuild if needed
    if ($AllGood) {
        Write-ColorOutput "Rebuilding Tailscale container..." "Yellow"
        docker compose build --no-cache tailscale
        Write-ColorOutput "`nFull check completed successfully!" "Green"
    } else {
        Write-ColorOutput "`nIssues found that require manual intervention." "Red"
        exit 1
    }
}
