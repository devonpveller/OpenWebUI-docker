# Update Management - Quick Start

## 🔒 Auto-Updates Now Locked

Your AI Stack is now configured for **manual updates only** to prevent accidental version changes and ensure GPU compatibility.

## 📋 Quick Commands

```bash
# Check versions
scripts\update-stack.bat check

# Update OpenWebUI (prompts for version)
scripts\update-stack.bat openwebui

# Update Ollama
scripts\update-stack.bat ollama

# Update everything
scripts\update-stack.bat all
```

## 🎯 What Changed

### 1. Docker Compose Configuration
- **OpenWebUI**: `pull_policy: build` (no auto-pulls during rebuild)
- **Ollama**: `pull_policy: never` (manual updates only)
- **Result**: `docker compose up` won't accidentally update versions

### 2. New Update Script
- **Location**: `scripts\update-stack.bat`
- **Features**: 
  - Automatic backups before updates
  - Version validation
  - GPU compatibility checks
  - Service coordination (restarts Ollama + Tailscale after OpenWebUI)

### 3. Documentation
- **Full Guide**: [documentation\UPDATE-MANAGEMENT.md](../documentation/UPDATE-MANAGEMENT.md)
- **Scripts README**: [scripts\README.md](README.md#update-management)

## ✅ Benefits

- ✅ No accidental version changes
- ✅ Automatic data backups
- ✅ GPU compatibility verified
- ✅ Network namespace coordination
- ✅ Rollback guidance on failure

## 📊 Current Versions

```bash
OpenWebUI: v0.6.41
PyTorch: 2.5.1+cu121 (CUDA 12.1)
Ollama: 0.13.3
```

## 🚀 Next Update Example

```bash
# 1. Check for new releases
scripts\update-stack.bat check
# Opens GitHub releases in output

# 2. Update OpenWebUI (when new version available)
scripts\update-stack.bat openwebui
# Enter: v0.6.42 (or whatever the new version is)

# 3. Verify
docker compose ps
scripts\quick-fixes.bat status
```

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| Build fails | `docker compose build --progress=plain openwebui` |
| GPU not available | `scripts\quick-fixes.bat gpu` |
| Services unhealthy | `scripts\quick-fixes.bat nuclear` |
| Need rollback | See [UPDATE-MANAGEMENT.md](../documentation/UPDATE-MANAGEMENT.md#rollback-required) |

## 📚 Full Documentation

For complete details, troubleshooting, and advanced topics:
- **[UPDATE-MANAGEMENT.md](../documentation/UPDATE-MANAGEMENT.md)** - Complete update guide
- **[AUTONOMOUS-RECOVERY-GUIDE.md](../documentation/AUTONOMOUS-RECOVERY-GUIDE.md)** - Recovery procedures
- **[copilot-instructions.md](../.github/copilot-instructions.md)** - Technical details

---

**Remember**: Updates are now manual and controlled. This ensures stability and GPU compatibility! 🎉
