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
The `frontend/dockerfile.tailscale` now includes:
- `dos2unix` utility installation
- Automatic line ending conversion during build
- Validation that shebang is properly formatted

### 3. Development Helper Tools

#### Checks to run by hand (from the repo root)
```powershell
# Every git-tracked *.sh has LF line endings (the same check pre-commit runs)
powershell -NoProfile -File scripts\checks\validate-lineendings.ps1

# The frontend plane's compose file renders (repeat per plane you touched)
docker compose -f frontend/docker-compose.yml config -q
```
(`scripts\checks\dev-helper.ps1` used to wrap these. Its compose check was a
bare `docker compose config`, which validates only the root anchor - no
services - and reported "valid" without looking at a plane, and its rebuild was
a bare `build tailscale` that exits `no such service`. It was archived
2026-09-25 to `scripts/archive/legacy-recovery/`.)

#### Enhanced Health Monitoring
The health monitoring script now detects:
- Windows line endings in `frontend/entrypoint.sh`
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
(Get-Content .\frontend\entrypoint.sh -Raw) -replace "`r`n", "`n" | Set-Content .\frontend\entrypoint.sh -NoNewline

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
dos2unix frontend/entrypoint.sh

# Or with sed
sed -i 's/\r$//' frontend/entrypoint.sh
```

### If Container Won't Start
```powershell
# 1. Check for line ending issues
powershell -NoProfile -File scripts\checks\validate-lineendings.ps1

# 2. Fix the file (see above), then rebuild the tailscale image deliberately
#    (tailscale:local is pinned) and recreate it. Naming the plane file is
#    required: a bare `docker compose` addresses the service-less root anchor.
#    frontend/.env must carry COMPOSE_PROFILES=gpu,tailscale, or compose cannot
#    load the tailscale service (see the note in frontend/docker-compose.yml).
docker compose -f frontend/docker-compose.yml build --no-cache tailscale
docker compose -f frontend/docker-compose.yml up -d tailscale
```

## Best Practices for Development

### For Windows Developers
1. **Let the pre-commit hook validate** (`git config core.hooksPath .githooks`;
   it runs the line-ending and compose checks on what you stage), or run
   `powershell -NoProfile -File scripts\checks\validate-lineendings.ps1` by hand.

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
   .\scripts\checks\stack-watchdog.ps1 -Mode check
   ```
3. **Monitor container logs** for early warning signs

## Automated Monitoring

The enhanced health monitoring system now provides:
- **Proactive detection** of line ending issues
- **Specific fix commands** in log output
- **Validation before attempting recovery**

Run comprehensive health check:
```powershell
.\scripts\checks\stack-watchdog.ps1 -Mode check
```

## Emergency Recovery

If issues occur despite prevention measures:

1. **Quick fix for line endings**: the PowerShell or `dos2unix` commands under
   "If Line Ending Issues Occur" above.

2. **Emergency recovery** (a gentle restart of openwebui, tailscale and the llama-cpp
   upstreams first when basic checks pass; otherwise an ordered restart of frontend,
   inference, memory, search, coder, OB1 and agent-org with health gates; never the portal):
   ```powershell
   .\scripts\recovery\emergency-recovery.ps1 -Action recover
   ```
   (The `.bat` twin was archived 2026-08-21.)

3. **Full system recovery**:
   ```powershell
   .\scripts\checks\stack-watchdog.ps1 -Mode check
   ```

## Monitoring and Alerts

The monitoring system will now:
- ✅ Detect line ending issues before they cause failures
- ✅ Provide specific remediation commands
- ✅ Prevent unnecessary restart loops
- ✅ Log detailed diagnostic information

This multi-layered approach ensures robust protection against line ending and other common Docker issues.
