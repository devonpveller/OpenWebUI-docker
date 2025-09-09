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
    Write-ColorOutput "🔍 Checking line endings..." "Cyan"
    
    $Issues = @()
    $ShellFiles = Get-ChildItem -Path $PROJECT_DIR -Filter "*.sh" -Recurse | Where-Object { $_.FullName -notmatch "\.git" }
    
    foreach ($File in $ShellFiles) {
        $Content = Get-Content $File.FullName -Raw
        if ($Content -match "`r`n") {
            $Issues += $File.Name
        }
    }
    
    if ($Issues.Count -gt 0) {
        Write-ColorOutput "❌ Found Windows line endings in: $($Issues -join ', ')" "Red"
        return $false
    } else {
        Write-ColorOutput "✅ All shell scripts have Unix line endings" "Green"
        return $true
    }
}

function Repair-LineEndings {
    Write-ColorOutput "🔧 Fixing line endings..." "Yellow"
    
    $ShellFiles = Get-ChildItem -Path $PROJECT_DIR -Filter "*.sh" -Recurse | Where-Object { $_.FullName -notmatch "\.git" }
    
    foreach ($File in $ShellFiles) {
        $Content = Get-Content $File.FullName -Raw
        if ($Content -match "`r`n") {
            Write-ColorOutput "  Fixing: $($File.Name)" "Yellow"
            $Content -replace "`r`n", "`n" | Set-Content $File.FullName -NoNewline
        }
    }
    
    Write-ColorOutput "✅ Line endings fixed" "Green"
}

function Test-DockerCompose {
    Write-ColorOutput "🔍 Validating Docker Compose configuration..." "Cyan"
    
    Set-Location $PROJECT_DIR
    try {
        $null = docker compose config 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "✅ Docker Compose configuration valid" "Green"
            return $true
        } else {
            Write-ColorOutput "❌ Docker Compose configuration invalid" "Red"
            return $false
        }
    } catch {
        Write-ColorOutput "❌ Failed to validate Docker Compose: $($_.Exception.Message)" "Red"
        return $false
    }
}

function Test-EntrypointShebang {
    Write-ColorOutput "🔍 Checking entrypoint.sh shebang..." "Cyan"
    
    $EntrypointPath = Join-Path $PROJECT_DIR "entrypoint.sh"
    if (Test-Path $EntrypointPath) {
        $FirstLine = Get-Content $EntrypointPath -First 1
        if ($FirstLine -eq "#!/bin/sh") {
            Write-ColorOutput "✅ Entrypoint shebang is correct" "Green"
            return $true
        } else {
            Write-ColorOutput "❌ Entrypoint shebang is incorrect: '$FirstLine'" "Red"
            Write-ColorOutput "   Should be: #!/bin/sh" "Yellow"
            return $false
        }
    } else {
        Write-ColorOutput "❌ entrypoint.sh not found" "Red"
        return $false
    }
}

function Invoke-Rebuild {
    Write-ColorOutput "🔨 Rebuilding Tailscale container..." "Yellow"
    
    Set-Location $PROJECT_DIR
    try {
        docker compose build --no-cache tailscale
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "✅ Tailscale container rebuilt successfully" "Green"
            return $true
        } else {
            Write-ColorOutput "❌ Failed to rebuild Tailscale container" "Red"
            return $false
        }
    } catch {
        Write-ColorOutput "❌ Rebuild failed: $($_.Exception.Message)" "Red"
        return $false
    }
}

# Main execution
Set-Location $PROJECT_DIR

switch ($Action) {
    "validate" {
        Write-ColorOutput "🔍 Running validation checks..." "Cyan"
        $AllGood = $true
        
        if (-not (Test-LineEndings)) { $AllGood = $false }
        if (-not (Test-EntrypointShebang)) { $AllGood = $false }
        if (-not (Test-DockerCompose)) { $AllGood = $false }
        
        if ($AllGood) {
            Write-ColorOutput "`n🎉 All validations passed!" "Green"
        } else {
            Write-ColorOutput "`nSome validations failed. Run with -Action fix-lineendings to auto-fix." "Red"
            exit 1
        }
    }
    
    "fix-lineendings" {
        Repair-LineEndings
        Test-LineEndings | Out-Null
    }
    
    "rebuild" {
        Invoke-Rebuild
    }
    
    "full-check" {
        Write-ColorOutput "🔍 Running full development check..." "Cyan"
        
        # Fix any line ending issues
        if (-not (Test-LineEndings)) {
            Repair-LineEndings
        }
        
        # Validate everything
        $AllGood = $true
        if (-not (Test-EntrypointShebang)) { $AllGood = $false }
        if (-not (Test-DockerCompose)) { $AllGood = $false }
        
        # Rebuild if needed
        if ($AllGood) {
            Invoke-Rebuild
            Write-ColorOutput "`n🎉 Full check completed successfully!" "Green"
        } else {
            Write-ColorOutput "`nIssues found that require manual intervention." "Red"
            exit 1
        }
    }
}
