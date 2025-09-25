# AI Agent Instructions for AI Stack Project

This is a containerized AI stack with OpenWebUI, Ollama, and Tailscale VPN running via Docker Compose. The project emphasizes security hardening, autonomous recovery, and Windows/PowerShell workflows.

## Architecture Overview

**Service Dependencies Flow:**
```
Docker Engine → OpenWebUI (healthy) → Tailscale (shared network) → Watchtower
                     ↓
                  Ollama
```

**Critical Network Pattern:**
- Tailscale uses `network_mode: service:openwebui` - shares OpenWebUI's network namespace
- When OpenWebUI gets new container ID (restarts/updates), Tailscale loses network connectivity
- This is the **most common recurring issue** requiring autonomous recovery

## Core Components

### Docker Compose Services
- **openwebui**: AI chat interface (port 3000→8080) with GPU-accelerated reranker models
- **ollama**: LLM server (port 11434)  
- **tailscale**: VPN access via custom build (`dockerfile.tailscale` + `entrypoint.sh`)
- **watchtower**: Auto-updates (monitors Ollama only, OpenWebUI excluded due to custom GPU build)

**⚠️ CRITICAL: Watchtower Limitation with Custom Builds**
- Watchtower is configured to skip OpenWebUI - custom builds cannot be auto-updated
- The `openwebui` service uses `build: dockerfile: Dockerfile.openwebui-gpu` (no `image:` field)
- **All OpenWebUI updates must be manual** - edit Dockerfile base image and rebuild
- Watchtower only monitors standard image-based services (Ollama)

### Custom OpenWebUI GPU Integration
**Files**: `Dockerfile.openwebui-gpu`, `docker-compose.yml`
- Custom build replacing CPU-only PyTorch with CUDA-enabled version
- GPU passthrough configuration with NVIDIA Container Toolkit
- Environment variables: `USE_CUDA=true`, `USE_CUDA_DOCKER=true`
- **Never use pre-built OpenWebUI images** - breaks GPU acceleration for reranker models

**Critical Update Process for New OpenWebUI Versions:**
1. **Check base image compatibility**: Update `FROM ghcr.io/open-webui/open-webui:latest` to specific version tag
2. **Verify CUDA compatibility**: Ensure PyTorch CUDA version matches your NVIDIA drivers
3. **Test GPU availability**: Always run `python -c "import torch; print('CUDA available:', torch.cuda.is_available())"` after updates
4. **Rebuild custom image**: Use `docker compose build --no-cache openwebui` for version updates
5. **Monitor reranker performance**: GPU acceleration should show in container logs during model loading

**Manual Update Workflow (Required - Watchtower Cannot Update Custom Builds):**
```bash
# 1. Check for new OpenWebUI releases
curl -s https://api.github.com/repos/open-webui/open-webui/releases/latest | grep '"tag_name"'

# 2. Backup data directory (critical step)
cp -r ./data ./data-backup-$(date +%Y%m%d)

# 3. Update Dockerfile.openwebui-gpu base image
# Edit: FROM ghcr.io/open-webui/open-webui:v0.X.X (replace with new version)

# 4. Rebuild with clean cache
docker compose build --no-cache openwebui

# 5. Test build before deployment
docker compose up -d openwebui

# 6. Validate GPU functionality
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())"

# 7. Monitor container logs for CUDA initialization
docker compose logs openwebui | grep -i cuda

# 8. Test reranker performance (should use GPU)
# Access OpenWebUI and test embedding generation speed
```

**Version Pinning Strategy:**
```dockerfile
# Pin to specific version for stability
FROM ghcr.io/open-webui/open-webui:v0.3.21  # Replace with current stable
# Always use cu121 index for CUDA 12.1+ compatibility
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

**GPU Troubleshooting Commands:**
```bash
# Verify NVIDIA runtime availability
docker run --rm --gpus all nvidia/cuda:12.1-base-ubuntu22.04 nvidia-smi

# Check OpenWebUI GPU detection
docker compose exec openwebui python -c "import torch; print('GPU count:', torch.cuda.device_count()); print('GPU name:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'None')"

# Monitor GPU usage during reranker operations
docker compose exec openwebui nvidia-smi
```

### Custom Tailscale Integration
**Files**: `dockerfile.tailscale`, `entrypoint.sh`
- Custom Alpine build with socat
- Network namespace sharing with OpenWebUI
- HTTPS serve configuration: `443 → 127.0.0.1:8080`
- **Never mix `image:` and `build:` directives** - breaks custom entrypoint

### Autonomous Recovery System
**3-tier recovery redundancy** for system reliability:
1. **Quick Fixes** (`scripts/quick-fixes.bat`): Simple targeted repairs
   - Network namespace reset (most common)
   - GPU availability check and restart
   - System status overview
   - Nuclear option for complete restart
2. **Enhanced Legacy** (`scripts/emergency-recovery.bat`): Updated batch with GPU awareness
3. **Advanced PowerShell** (`scripts/emergency-recovery.ps1`): Full-featured recovery
   - Graceful shutdown with timeout handling
   - Health check validation before/after operations
   - GPU availability testing and recovery
   - Multiple recovery modes with comprehensive error handling

**Plus existing 5-tier Tailscale redundancy**:
4. Docker health checks (`docker-compose.yml`)
5. Internal monitoring (`entrypoint.sh`)
6. Watchtower coordination (`docker-compose.override.yml`)
7. Windows service (`scripts/check-tailscale-health.ps1`)
8. Simple background monitor (`scripts/simple-monitor.ps1`)

## Development Patterns

### PowerShell Conventions
- Use `[CmdletBinding()]` and proper parameter validation
- Structured logging with levels: `INFO`, `WARN`, `ERROR`, `SUCCESS`
- Error handling: `$ErrorActionPreference = "Stop"`
- Avoid unused variables (linting enabled)

### Docker Compose Patterns
- **Security-first**: `no-new-privileges:true`, localhost-only ports, read-only mounts
- **Health checks**: All services have comprehensive health validation
- **Dependency management**: `depends_on` with `condition: service_healthy`
- **Custom builds only**: Never use pre-built images for `openwebui` or `tailscale` - breaks GPU and VPN functionality
- **Network namespace sharing**: `network_mode: service:openwebui` in Tailscale service creates shared networking

### Configuration Management
- **Environment variables**: `.env` file (excluded from git)
- **Secrets**: Never commit auth keys - use `.env.example` template
- **Persistent data**: `./data/` directory for state retention

## Critical Debugging Knowledge

### GPU Integration Issues (Critical for OpenWebUI Updates)
**Symptoms**: Reranker models using CPU instead of GPU, CUDA not available, slow embedding generation
**Root causes**: 
- Default OpenWebUI images ship with CPU-only PyTorch
- CUDA version mismatch between PyTorch and NVIDIA drivers
- Missing `USE_CUDA=true` environment variables
- GPU memory exhaustion from other processes

**Diagnostic Commands**:
```bash
# Check GPU availability in container (most important)
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count()); print('GPU memory:', torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else 'N/A')"

# Verify NVIDIA Container Toolkit
docker run --rm --gpus all nvidia/cuda:12.1-base-ubuntu22.04 nvidia-smi

# Check host GPU status
nvidia-smi
```

**Recovery Steps**:
1. **Immediate fix**: `.\scripts\emergency-recovery.ps1 -Action gpu-reset`
2. **Rebuild custom image**: `docker compose build --no-cache openwebui`
3. **Update PyTorch version**: Edit `Dockerfile.openwebui-gpu` with compatible CUDA index
4. **Clear GPU cache**: Restart containers to free GPU memory

**Manual Update Troubleshooting:**
```bash
# Build failed with CUDA errors
docker compose build --no-cache --progress=plain openwebui  # Verbose output

# GPU not available after update
docker compose exec openwebui nvidia-smi  # Check GPU visibility
docker compose exec openwebui python -c "import torch; print(torch.version.cuda)"  # Check PyTorch CUDA version

# Container won't start after update
docker compose logs openwebui  # Check startup errors
docker compose exec openwebui python -c "import torch; print('Import successful')"  # Test PyTorch import

# Rollback to previous version
# 1. Restore data backup: rm -rf ./data && mv ./data-backup-YYYYMMDD ./data
# 2. Revert Dockerfile.openwebui-gpu to previous base image version  
# 3. Rebuild: docker compose build --no-cache openwebui && docker compose up -d
```

**Version Update Workflow for OpenWebUI**:
1. **Watchtower CANNOT update OpenWebUI** - custom builds are ignored by Watchtower
2. **Manual updates required**: Edit `Dockerfile.openwebui-gpu` to update base image version
3. **Rebuild process**: `docker compose build --no-cache openwebui && docker compose up -d`
4. **Post-rebuild testing**: Run GPU diagnostic commands before considering deployment complete
5. **Monitor performance**: Check embedding generation speed and reranker logs

### Network Namespace Issues (Most Common)
**Symptoms**: "Network unreachable", Tailscale can't connect to DERP servers
**Root cause**: OpenWebUI container recreation breaks shared network namespace
**Quick fix**:
```bash
docker compose down tailscale
docker compose up -d tailscale
```

### Tailscale Service Configuration
**Socket path**: `/tmp/tailscaled.sock` (consistent across all operations)
**State directory**: `/var/lib/tailscale` (must be persistent)
**Serve backend**: `http://127.0.0.1:8080` (not container names)

### Security Hardening Applied
- Container security options (`tmpfs`, `security_opt`)
- Docker socket read-only access
- Localhost-only port binding (`127.0.0.1:port`)
- Structured audit logging

## Essential Commands

### Quick Diagnostics & Recovery (Most Important)
```powershell
# Quick targeted fixes (start here for common issues)
scripts\quick-fixes.bat namespace    # Network namespace reset (most common)
scripts\quick-fixes.bat gpu         # GPU availability check and restart
scripts\quick-fixes.bat status      # System overview
scripts\quick-fixes.bat nuclear     # Complete restart as last resort

# Advanced PowerShell recovery with comprehensive health checks
.\scripts\emergency-recovery.ps1 -Action recover  # Standard recovery
.\scripts\emergency-recovery.ps1 -Action gpu-reset  # GPU-specific issues
```

### Health Diagnostics
```bash
# System health overview
docker compose ps

# GPU availability check (critical for OpenWebUI reranker models)
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# Network connectivity test (most common failure point)
docker compose exec tailscale ping -c 1 8.8.8.8

# Tailscale status and serve configuration
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status
```

### Recovery Operations (Graduated Response)
```bash
# 1. Simple restart (try first)
docker compose restart tailscale

# 2. Full namespace recovery (most effective for network issues)
docker compose down tailscale; docker compose up -d tailscale

# 3. Rebuild custom images (for deeper issues)
docker compose build --no-cache; docker compose up -d

# 4. Monitor background processes
.\scripts\simple-monitor.ps1 -Action start  # No admin required
```

## File Organization Patterns

### Configuration Files
- `config/*.conf`: Application-specific settings
- `.env`: Runtime environment (SECRET - never commit)
- `docker-compose.override.yml`: Development/testing overrides

### Scripts Directory
- `*.ps1`: Windows PowerShell utilities
- `*.bat`: Quick batch fixes
- `emergency-recovery.ps1`: Advanced recovery with health checks
- `emergency-recovery.bat`: Enhanced legacy recovery with GPU awareness
- `quick-fixes.bat`: Simple targeted fixes for common issues
- Monitoring and health check automation

### Data Persistence
- `data/tailscale/`: Device identity (critical for VPN)
- `data/ollama/`: AI models (large files)  
- `data/openwebui/`: User data and conversations
- `data/openwebui-models/`: Custom model definitions with GPU configurations

### Critical File Dependencies
- **Never edit**: `Dockerfile.openwebui-gpu` without understanding PyTorch CUDA requirements
  - Must uninstall CPU-only PyTorch: `pip uninstall -y torch torchvision torchaudio`
  - Must use CUDA index: `--index-url https://download.pytorch.org/whl/cu121`
  - Version compatibility critical: Match PyTorch CUDA version to NVIDIA drivers
- **Line endings matter**: `entrypoint.sh` uses `dos2unix` conversion in `dockerfile.tailscale`
- **Environment template**: Always create `.env` from `.env.example` - contains auth key expiration dates
- **Override behavior**: `docker-compose.override.yml` handles Watchtower coordination for updates
- **Windows-specific**: All PowerShell scripts use `[CmdletBinding()]` and structured logging
- **GPU passthrough**: `docker-compose.yml` deploy.resources.reservations.devices section required for GPU access

## Common Workflow Patterns

### Emergency Response Workflow (Use This Order)
1. **Quick Assessment**: `scripts\quick-fixes.bat status` - Get system overview
2. **Most Common Fix**: `scripts\quick-fixes.bat namespace` - Reset network namespace
3. **GPU Issues**: `scripts\quick-fixes.bat gpu` - Check and restart GPU services
4. **Advanced Recovery**: `.\scripts\emergency-recovery.ps1 -Action recover` - Full health checks
5. **Last Resort**: `scripts\quick-fixes.bat nuclear` - Complete system restart

### Development Workflow
```powershell
# Safe testing without affecting production
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d

# Check container build status
docker compose build --dry-run

# Monitor logs during development
docker compose logs -f tailscale  # Most failure-prone service
```

### Safe Updates
1. Always backup `data/` directory first
2. Update images: `docker compose pull` (excludes custom Tailscale/OpenWebUI builds)
3. Restart services: `docker compose up -d`
4. **Critical**: Verify Tailscale connectivity after updates using namespace reset

### Troubleshooting Decision Tree
```
Issue → Start Here:
├── "Network unreachable" → scripts\quick-fixes.bat namespace
├── "CUDA not available" → scripts\quick-fixes.bat gpu  
├── Reranker models slow/CPU-only → Check: docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())"
├── OpenWebUI update broke GPU → Rebuild: docker compose build --no-cache openwebui
├── General slowness → Check logs: docker compose logs --tail=50 openwebui
├── Containers not starting → docker compose ps; check dependencies
└── Unknown/Complex → .\scripts\emergency-recovery.ps1 -Action recover
```

## Security Considerations

### Secrets Management
- **Tailscale auth keys**: Rotate before expiration (check `.env.example` for current expiry)
- **Environment isolation**: All secrets in `.env` (create from `.env.example`), excluded from git
- **Audit trail**: Structured logging for security events

### Container Hardening
- All containers run with `no-new-privileges:true`
- Temporary filesystems use `noexec,nosuid`
- Network access limited to localhost only
- Docker socket access is read-only where possible

When working on this codebase, prioritize understanding the network namespace sharing pattern and autonomous recovery mechanisms - these are the most complex and failure-prone aspects of the system. Additionally, be aware of the custom GPU integration for OpenWebUI which requires specific Docker build contexts and environment variables to function properly.

## Quick Reference Summary

### Most Common Issues & Solutions:
1. **Network connectivity lost** → `scripts\quick-fixes.bat namespace`
2. **GPU not available** → `scripts\quick-fixes.bat gpu`  
3. **OpenWebUI needs update** → Manual rebuild process (see Manual Update Workflow above)
4. **General system issues** → `.\scripts\emergency-recovery.ps1 -Action recover`

### Key Files to Never Edit Without Understanding:
- `Dockerfile.openwebui-gpu` - GPU PyTorch replacement logic
- `entrypoint.sh` - Tailscale startup with line ending handling  
- `docker-compose.yml` - Network namespace sharing configuration
- `.env` - Contains auth keys with expiration dates

### Advanced Topics:
- **Automated Update Strategy**: See `documentation/AICodeAgentGuides/hybrid-openwebui-update-approach.md`
- **Recovery System Architecture**: 8-tier redundancy from quick fixes to Windows services
- **Security Hardening**: Container isolation, localhost-only binding, read-only mounts
