# Update Management Guide

## Overview

The AI Stack now uses **manual update management** to prevent accidental version changes. Use `update-stack.bat` for controlled updates with automatic backups and verification.

## Quick Reference

### Check Current Versions

```bash
scripts\update-stack.bat check
```

**Output includes:**

- Current OpenWebUI version
- Current Ollama version
- Dockerfile base image version
- Links to latest releases

### Update OpenWebUI

```bash
scripts\update-stack.bat openwebui
```

**Process:**

1. Creates backup: `data-backup-YYYYMMDD-HHMMSS/`
2. Prompts for version: `v0.6.41`
3. Updates `Dockerfile.openwebui-gpu`
4. Rebuilds with GPU support (CUDA-enabled PyTorch)
5. Restarts services: OpenWebUI → Ollama → Tailscale
6. Verifies GPU availability

**Time:** ~5-10 minutes (depending on image pull/build speed)

### Update Ollama

```bash
scripts\update-stack.bat ollama
```

**Process:**

1. Pulls latest `ollama/ollama:latest` image
2. Restarts Ollama container
3. Verifies version

**Time:** ~1-2 minutes

### Update Both

```bash
scripts\update-stack.bat all
```

Runs both OpenWebUI and Ollama updates sequentially with confirmation prompt.

## Update Lock Configuration

### Docker Compose Pull Policies

**Before (auto-update on every rebuild):**

```yaml
openwebui:
  pull_policy: always # Pulls new base image every time
ollama:
  pull_policy: always # Pulls new image every time
```

**After (manual updates only):**

```yaml
openwebui:
  pull_policy: build # Only rebuilds from Dockerfile, no auto-pull
ollama:
  pull_policy: never # Never auto-pulls, manual only
```

### Benefits

- ✅ Prevents accidental version changes
- ✅ Ensures tested versions remain stable
- ✅ Automatic backups before updates
- ✅ GPU compatibility verification
- ✅ Service coordination (network namespace awareness)

## Version History

Track your updates by checking backup directories:

```bash
dir data-backup-*
```

Each backup folder is timestamped: `data-backup-YYYYMMDD-HHMMSS`

## Troubleshooting Updates

### Build Fails

**Symptom:** `docker compose build` fails during PyTorch installation

**Solution:**

```bash
# Check build logs for CUDA version mismatch
docker compose build --no-cache --progress=plain openwebui

# Verify NVIDIA drivers
nvidia-smi

# Common fix: Update PyTorch index in Dockerfile.openwebui-gpu
# Change cu121 to match your CUDA version (cu118, cu121, cu124, etc.)
```

### GPU Not Available After Update

**Symptom:** `CUDA available: False` after update

**Solution:**

```bash
# Quick GPU recovery
scripts\quick-fixes.bat gpu

# Manual verification
docker compose exec openwebui python -c "import torch; print('CUDA:', torch.cuda.is_available())"

# Check GPU passthrough
docker compose exec openwebui nvidia-smi
```

### Services Not Starting

**Symptom:** Ollama or Tailscale unhealthy after OpenWebUI update

**Root Cause:** Network namespace sharing requires coordinated restart

**Solution:**

```bash
# Restart dependent services
docker compose up -d ollama tailscale

# Or use nuclear option
scripts\quick-fixes.bat nuclear
```

### Rollback Required

**Symptom:** Update caused issues, need to restore previous version

**Solution:**

```bash
# 1. Stop services
docker compose down

# 2. Restore the OpenWebUI named volume from a tarball backup
docker run --rm \
  -v ai-stack_openwebui-data:/data \
  -v ${PWD}/backups/openwebui:/backups:ro \
  -v ${PWD}/backup/openwebui-restore.sh:/scripts/restore.sh:ro \
  alpine:3.21 sh /scripts/restore.sh /backups/openwebui-backup-TIMESTAMP.tar.gz

# 3. Edit Dockerfile.openwebui-gpu - change version to previous
# Example: FROM ghcr.io/open-webui/open-webui:v0.6.40

# 4. Rebuild and restart
docker compose build --no-cache openwebui
docker compose up -d
```

## Version Compatibility Matrix

### Current Tested Configuration

- **OpenWebUI**: v0.6.41
- **PyTorch**: 2.5.1+cu121
- **Ollama**: 0.13.3
- **CUDA**: 12.1+ (compatible with 12.9 drivers)

### PyTorch CUDA Compatibility

| CUDA Version | PyTorch Index URL                        |
| ------------ | ---------------------------------------- |
| 11.8         | `https://download.pytorch.org/whl/cu118` |
| 12.1         | `https://download.pytorch.org/whl/cu121` |
| 12.4         | `https://download.pytorch.org/whl/cu124` |

**Check your CUDA version:**

```bash
nvidia-smi  # Look for "CUDA Version: XX.X"
```

## Best Practices

### Before Updating

1. ✅ Check release notes for breaking changes
2. ✅ Verify sufficient disk space (backups + new images ~5-10 GB)
3. ✅ Update during low-usage time
4. ✅ Have rollback plan ready

### During Update

1. ✅ Monitor build output for errors
2. ✅ Don't interrupt image pull/build
3. ✅ Wait for health checks to pass

### After Update

1. ✅ Test basic functionality (chat, model loading)
2. ✅ Verify GPU acceleration (check reranker speed)
3. ✅ Check Tailscale connectivity
4. ✅ Monitor logs for errors: `docker compose logs -f`

## Automation Considerations

### Why No Auto-Updates?

**Custom builds require careful handling:**

- GPU-specific PyTorch installation
- CUDA version compatibility
- Network namespace coordination
- Data migration considerations

**Manual updates ensure:**

- Controlled version testing
- Automatic backups
- GPU compatibility verification
- Service coordination

### Future: Scripted Updates

For advanced users, create a scheduled task:

```powershell
# Example: Weekly update check (manual execution still required)
$action = New-ScheduledTaskAction -Execute "scripts\update-stack.bat" -Argument "check"
$trigger = New-ScheduledTaskTrigger -Weekly -At 9am -DaysOfWeek Sunday
Register-ScheduledTask -TaskName "AI Stack Update Check" -Action $action -Trigger $trigger
```

## Getting Help

**Check versions:**

```bash
scripts\update-stack.bat check
```

**Service health:**

```bash
docker compose ps
scripts\quick-fixes.bat status
```

**Detailed logs:**

```bash
docker compose logs openwebui | tail -100
docker compose logs ollama | tail -50
```

**GPU status:**

```bash
scripts\quick-fixes.bat gpu
```

## Related Documentation

- [Main README](../README.md) - Full project documentation
- [Scripts README](README.md) - All available scripts
- [Emergency Recovery Guide](../documentation/AUTONOMOUS-RECOVERY-GUIDE.md) - Recovery procedures
- [AI Agent Instructions](../.github/copilot-instructions.md) - Update workflow details
