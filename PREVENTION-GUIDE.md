# Prevention Guide: Avoiding Common Docker and Line Ending Issues

## Overview
This guide outlines preventive measures to avoid common issues that can cause container startup failures, particularly line ending problems that affect shell scripts in Docker containers.

## Root Cause Analysis
The most common issue occurs when shell scripts created on Windows have CRLF (`\r\n`) line endings instead of Unix LF (`\n`) line endings. When these scripts are copied into Linux containers, the `\r` character breaks the shebang line, making scripts non-executable.

## Prevention Strategies

### 1. Git Configuration (Automatic)
The repository now includes a `.gitattributes` file that enforces:
- Unix line endings (LF) for shell scripts (`*.sh`)
- Unix line endings for Docker files
- Windows line endings (CRLF) for PowerShell scripts (`*.ps1`)

### 2. Enhanced Docker Build Process
The `dockerfile.tailscale` now includes:
- `dos2unix` utility installation
- Automatic line ending conversion during build
- Validation that shebang is properly formatted

### 3. Development Helper Tools

#### PowerShell Development Script
```powershell
# Validate everything before committing
.\scripts\dev-helper.ps1 -Action validate

# Fix line ending issues automatically
.\scripts\dev-helper.ps1 -Action fix-lineendings

# Full development check (fix + rebuild)
.\scripts\dev-helper.ps1 -Action full-check
```

#### Enhanced Health Monitoring
The health monitoring script now detects:
- Windows line endings in `entrypoint.sh`
- Missing entrypoint files in containers
- Provides specific fix commands

### 4. Pre-Commit Validation
Git pre-commit hook validates:
- Shell script line endings
- Shebang format
- Docker Compose syntax

## Quick Fix Commands

### If Line Ending Issues Occur
```powershell
# PowerShell - Fix specific file
(Get-Content .\entrypoint.sh -Raw) -replace "`r`n", "`n" | Set-Content .\entrypoint.sh -NoNewline

# PowerShell - Fix all shell scripts
Get-ChildItem -Filter "*.sh" -Recurse | ForEach-Object {
    $content = Get-Content $_.FullName -Raw
    if ($content -match "`r`n") {
        $content -replace "`r`n", "`n" | Set-Content $_.FullName -NoNewline
        Write-Host "Fixed: $($_.Name)" -ForegroundColor Green
    }
}
```

```bash
# Linux/WSL - Fix with dos2unix
dos2unix entrypoint.sh

# Or with sed
sed -i 's/\r$//' entrypoint.sh
```

### If Container Won't Start
```powershell
# 1. Check for line ending issues
.\scripts\dev-helper.ps1 -Action validate

# 2. Fix and rebuild
.\scripts\dev-helper.ps1 -Action full-check

# 3. Manual rebuild if needed
docker compose build --no-cache tailscale
docker compose up -d tailscale
```

## Best Practices for Development

### For Windows Developers
1. **Always run validation before committing**:
   ```powershell
   .\scripts\dev-helper.ps1 -Action validate
   ```

2. **Use WSL or Git Bash for shell script editing**
3. **Configure VS Code for Unix line endings**:
   ```json
   {
     "files.eol": "\n",
     "files.associations": {
       "*.sh": "shellscript"
     }
   }
   ```

### For All Developers
1. **Test Docker builds locally** before pushing
2. **Run health checks** after updates:
   ```powershell
   .\scripts\check-tailscale-health.ps1 -Mode check
   ```
3. **Monitor container logs** for early warning signs

## Automated Monitoring

The enhanced health monitoring system now provides:
- **Proactive detection** of line ending issues
- **Specific fix commands** in log output
- **Validation before attempting recovery**

Run comprehensive health check:
```powershell
.\scripts\check-tailscale-health.ps1 -Mode check
```

## Emergency Recovery

If issues occur despite prevention measures:

1. **Quick fix for line endings**:
   ```powershell
   .\scripts\dev-helper.ps1 -Action fix-lineendings
   ```

2. **Emergency rebuild**:
   ```batch
   .\scripts\emergency-recovery.bat
   ```

3. **Full system recovery**:
   ```powershell
   .\scripts\check-tailscale-health.ps1 -Mode check
   ```

## Monitoring and Alerts

The monitoring system will now:
- ✅ Detect line ending issues before they cause failures
- ✅ Provide specific remediation commands
- ✅ Prevent unnecessary restart loops
- ✅ Log detailed diagnostic information

This multi-layered approach ensures robust protection against line ending and other common Docker issues.
