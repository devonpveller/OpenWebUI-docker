# AI Stack with Tailscale VPN

A comprehensive Docker Compose setup for running OpenWebUI, Ollama, and secure remote access via Tailscale VPN.

## 🚀 Overview

This project provides a complete AI chat interface with:
- **OpenWebUI**: Modern web interface for AI models with GPU-accelerated reranker models
- **Ollama**: Local LLM hosting and management
- **Tailscale**: Secure VPN access for remote connections
- **Watchtower**: Automatic container updates
- **GPU Support**: NVIDIA GPU acceleration for both Ollama and OpenWebUI components

## 📁 Project Structure

```
ai-stack/
├── docker-compose.yml          # Main container orchestration
├── Dockerfile.openwebui-gpu    # Custom OpenWebUI build with GPU support
├── dockerfile.tailscale        # Custom Tailscale container build
├── entrypoint.sh              # Tailscale startup script
├── .env                       # Environment variables (create this)
├── config/                    # Application configurations
│   ├── ollama.conf
│   └── openwebui.conf
├── data/                      # Persistent data storage
│   ├── ollama/                # Ollama models and data
│   ├── openwebui/             # OpenWebUI database and uploads
│   ├── openwebui-models/      # Custom model definitions
│   └── tailscale/             # Tailscale state and certificates
├── scripts/                   # Utility scripts
│   ├── emergency-recovery.ps1 # Advanced PowerShell recovery tool
│   ├── emergency-recovery.bat # Enhanced legacy recovery script  
│   ├── quick-fixes.bat        # Simple targeted fixes
│   └── ...                   # Other utility scripts
├── documentation/             # Additional documentation
└── README.md                  # This file
```

## 🔧 Prerequisites

### Windows Setup
Run the automated setup script to install prerequisites:

```powershell
# Run as Administrator
.\scripts\setup-prereqs.ps1
```

This installs:
- Docker Desktop with WSL2 backend
- NVIDIA Container Toolkit (if NVIDIA GPU detected)
- Required Windows features

### Manual Prerequisites
- Docker Desktop with WSL2 enabled
- NVIDIA GPU drivers (for GPU acceleration)
- Tailscale account and auth key

## ⚙️ Configuration

### 1. Environment Variables

Create a `.env` file in the project root:

```env
# Tailscale Configuration
TAILSCALE_AUTH_KEY=tskey-auth-your-key-here
TS_HOSTNAME=openwebui
TS_ACCEPT_DNS=false

# Ollama Configuration
OLLAMA_HOST=http://ollama:11434

# OpenWebUI GPU Configuration
USE_CUDA=true
USE_CUDA_DOCKER=true
```

### 3. GPU Support Configuration

This project includes custom GPU support for OpenWebUI to accelerate reranker models and other AI components.

#### GPU Prerequisites
- NVIDIA GPU with CUDA support
- NVIDIA drivers installed on host system
- NVIDIA Container Toolkit installed via setup script

#### Custom GPU Build
The project uses a custom `Dockerfile.openwebui-gpu` that:
- Removes CPU-only PyTorch packages from base OpenWebUI image
- Installs CUDA-enabled PyTorch for GPU acceleration
- Configures proper device selection for reranker models

```dockerfile
# Custom build replaces CPU PyTorch with GPU version
FROM ghcr.io/open-webui/open-webui:latest
RUN pip uninstall -y torch torchvision torchaudio
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

#### GPU Environment Variables
- `USE_CUDA=true`: Enables CUDA support in OpenWebUI
- `USE_CUDA_DOCKER=true`: Configures proper device selection in containerized environment

#### Verify GPU Support
```bash
# Check if GPU is detected
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())"

# Check GPU device name
docker compose exec openwebui python -c "import torch; print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'No GPU')"
```

### 2. Tailscale Auth Key

1. Go to [Tailscale Admin Console](https://login.tailscale.com/admin/settings/keys)
2. Generate a new auth key with these settings:
   - **Reusable**: ✅ (for container restarts)
   - **Ephemeral**: ❌ (for persistent device)
   - **Preauthorized**: ✅ (for automatic approval)
   - **Expiry**: 90 days or longer

## 🚀 Startup Sequence

### Quick Start

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f

# Stop all services
docker compose down
```

### Detailed Startup Process

1. **Build Custom Images** (first time only):
   ```bash
   docker compose build
   ```

2. **Start Services in Order**:
   ```bash
   # Start core services first
   docker compose up -d ollama openwebui
   
   # Wait for services to be healthy
   docker compose ps
   
   # Start Tailscale (depends on openwebui network)
   docker compose up -d tailscale
   
   # Start monitoring
   docker compose up -d watchtower
   ```

3. **Verify Connectivity**:
   ```bash
   # Check all services are running
   docker compose ps
   
   # Verify Tailscale connection
   docker compose exec tailscale tailscale status
   
   # Check serve configuration
   docker compose exec tailscale tailscale serve status
   ```

### Service Dependencies

```mermaid
graph TD
    A[Docker Engine] --> B[OpenWebUI]
    A --> C[Ollama]
    A --> D[Watchtower]
    B --> E[Tailscale]
    C --> B
```

## 🌐 Access Points

### Local Access
- **OpenWebUI**: http://localhost:3000
- **Ollama API**: http://localhost:11434

### Remote Access (via Tailscale)
- **OpenWebUI**: https://openwebui-13.tail[your-tailnet].ts.net/
- **Secure HTTPS** with automatic certificates

## ⚠️ Critical Areas & Cautions

### 🔴 Tailscale Configuration
**Location**: `entrypoint.sh`, `docker-compose.yml`

**Critical Points**:
- **Never remove the `build:` directive** from docker-compose.yml
- **Don't add `image:` directive** alongside `build:` (causes conflicts)
- **Auth key must be reusable** for container restarts
- **Persistent volume required** for device identity

```yaml
# ✅ CORRECT - Use build only
tailscale:
  build:
    context: .
    dockerfile: dockerfile.tailscale

# ❌ WRONG - Don't mix image and build
tailscale:
  image: tailscale/tailscale:latest  # This breaks custom entrypoint
  build:
    context: .
    dockerfile: dockerfile.tailscale
```

### 🔴 Entrypoint Script
**Location**: `entrypoint.sh`

**Critical Points**:
- **Socket path must be consistent**: `/tmp/tailscaled.sock`
- **State directory must be persistent**: `/var/lib/tailscale`
- **Serve backend must be localhost**: `http://127.0.0.1:8080` (not container names)
- **Don't use `--reset` flag** in production (causes device re-registration)

### 🔴 Network Configuration
**Location**: `docker-compose.yml`

**Critical Points**:
- Tailscale uses `network_mode: service:openwebui`
- This shares the network namespace between containers
- Port 8080 in Tailscale container maps to OpenWebUI
- Don't change network modes without updating serve config

### 🔴 Volume Mounts
**Persistent Data**:
```yaml
volumes:
  - ./data/tailscale:/var/lib/tailscale    # Device identity
  - ./data/ollama:/root/.ollama            # Models
  - ./data/openwebui:/app/backend/data     # User data
```

**⚠️ Never delete these directories** - you'll lose:
- Tailscale device registration
- Downloaded AI models (large files)
- User accounts and conversations

## 🐛 Debugging

### Enhanced Recovery Scripts

The project includes multiple recovery tools for different scenarios:

#### 1. PowerShell Advanced Recovery (`emergency-recovery.ps1`)
Full-featured recovery with health checks and timing controls:
```powershell
# Standard recovery with health monitoring
.\scripts\emergency-recovery.ps1 -Action recover

# Nuclear option for complete stack restart
.\scripts\emergency-recovery.ps1 -Action nuclear

# GPU-specific recovery
.\scripts\emergency-recovery.ps1 -Action gpu-reset
```

Features:
- Graceful service shutdown with timeout handling
- Health check validation before/after operations
- GPU availability testing and recovery
- Comprehensive error handling and logging
- Multiple recovery modes with fallback options

#### 2. Enhanced Legacy Recovery (`emergency-recovery.bat`)
Updated batch script with GPU awareness:
```batch
# Quick network recovery (most common fix)
scripts\emergency-recovery.bat

# Includes nuclear option for complete reset
```

#### 3. Quick Targeted Fixes (`quick-fixes.bat`)
Simple, fast fixes for specific issues:
```batch
# Network namespace reset (most common)
scripts\quick-fixes.bat namespace

# GPU check and restart
scripts\quick-fixes.bat gpu

# System status overview
scripts\quick-fixes.bat status

# Complete stack restart
scripts\quick-fixes.bat nuclear
```

### General Docker Debugging

```bash
# Check service status
docker compose ps

# View logs for all services
docker compose logs

# View logs for specific service
docker compose logs tailscale -f

# Enter container for debugging
docker compose exec tailscale sh

# Check resource usage
docker stats

# Rebuild after changes
docker compose down
docker compose build --no-cache
docker compose up -d
```

### GPU-Specific Debugging

#### GPU Not Detected
**Symptoms**: Reranker models running on CPU instead of GPU
**Debug Steps**:
```bash
# Check GPU availability in container
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# Check environment variables
docker compose exec openwebui env | grep -E "(USE_CUDA|DEVICE_TYPE)"

# Verify GPU passthrough
docker compose exec openwebui nvidia-smi  # Should show GPU info

# Check PyTorch CUDA installation
docker compose exec openwebui python -c "import torch; print('PyTorch CUDA version:', torch.version.cuda)"
```

**Common Solutions**:
```bash
# Restart OpenWebUI container
docker compose restart openwebui

# Rebuild with GPU support (if Dockerfile changed)
docker compose build --no-cache openwebui
docker compose up -d openwebui

# Use GPU-specific recovery
.\scripts\emergency-recovery.ps1 -Action gpu-reset
```

#### GPU Memory Issues
**Symptoms**: CUDA out of memory errors
**Debug Steps**:
```bash
# Check GPU memory usage
docker compose exec openwebui nvidia-smi

# Monitor GPU utilization during operations
watch -n 1 'docker compose exec openwebui nvidia-smi'
```

**Solutions**:
- Reduce model size or batch size in OpenWebUI settings
- Restart container to clear GPU memory: `docker compose restart openwebui`

### Common Issues

#### Container Exit Code 137
**Symptoms**: Container killed unexpectedly
**Causes**: Out of memory, resource limits, image conflicts

**Debug Steps**:
```bash
# Check system resources
docker system df
docker system prune

# Check for image conflicts
docker images | grep tailscale

# Rebuild from scratch
docker compose down
docker compose build --no-cache tailscale
docker compose up -d
```

#### OpenWebUI Not Accessible
**Symptoms**: Can't reach web interface

**Debug Steps**:
```bash
# Check container health
docker compose ps
docker compose logs openwebui

# Test local connectivity
curl http://localhost:3000

# Check port bindings
docker port openwebui
```

## 🔧 Tailscale-Specific Debugging

### Check Connection Status
```bash
# Basic status
docker compose exec tailscale tailscale status

# Detailed connection info
docker compose exec tailscale tailscale status --json

# Check network info
docker compose exec tailscale tailscale netcheck
```

### Debug Serve Configuration
```bash
# Check serve status
docker compose exec tailscale tailscale serve status

# Test backend connectivity from inside container
docker compose exec tailscale curl http://127.0.0.1:8080

# Check certificates
docker compose exec tailscale ls -la /var/lib/tailscale/certs/
```

### Common Tailscale Issues

#### Device Name Incrementing
**Symptoms**: Device shows as openwebui-14, openwebui-15, etc.
**Cause**: Using `--reset` flag or losing persistent state

**Solution**:
```bash
# Check if state directory exists
docker compose exec tailscale ls -la /var/lib/tailscale/

# Ensure no --reset flag in entrypoint.sh
grep -n reset entrypoint.sh

# Restart without --reset
docker compose restart tailscale
```

#### "No Webserver Configured" Error
**Symptoms**: TLS handshake errors, serve not working
**Cause**: Wrong backend URL in serve configuration

**Debug**:
```bash
# Check serve config
docker compose exec tailscale tailscale serve status

# Test backend from inside tailscale container
docker compose exec tailscale curl -v http://127.0.0.1:8080

# Check if openwebui is accessible in shared network
docker compose exec tailscale netstat -tlnp
```

**Fix**:
```bash
# Reset serve config
docker compose exec tailscale tailscale serve reset

# Reconfigure with correct backend
docker compose exec tailscale tailscale serve --https=443 --bg http://127.0.0.1:8080
```

#### Auth Key Issues
**Symptoms**: "machine not authorized", login URLs

**Debug**:
```bash
# Check if auth key is set
docker compose exec tailscale env | grep TAILSCALE_AUTH_KEY

# Verify auth key in Tailscale admin console
# Check if key is expired or single-use
```

#### Connection Drops
**Symptoms**: Tailscale disconnects after some time, "Network unreachable" errors

**Root Cause**: OpenWebUI container recreation breaks network namespace sharing

**Debug**:
```bash
# Check recent logs for errors
docker compose logs tailscale --since 1h

# Look for specific error patterns
docker compose logs tailscale | grep -E "(error|failed|timeout)"

# Check container resource usage
docker stats tailscale

# Test internet connectivity from Tailscale container
docker compose exec tailscale ping -c 4 8.8.8.8

# Check if network namespace is broken
docker compose exec tailscale ip addr
# Should show more than just loopback (lo) interface
```

**Common Symptoms**:
- Logs show: "Tailscale could not connect to relay server"
- Logs show: "Network unreachable" 
- `ping` fails from Tailscale container but works from OpenWebUI
- `ip addr` in Tailscale shows only loopback interface

**Fix**:
```bash
# Quick fix - restart both containers
docker compose down tailscale
docker compose up -d tailscale

# If that doesn't work, check for orphaned containers
docker compose down
docker compose up -d
```

**Prevention**: This happens when:
- Watchtower updates OpenWebUI (creates new container ID)
- Manual `docker compose restart openwebui` 
- Docker daemon restarts
- System reboots

**Automated Prevention** (Add to crontab or scheduled task):
```bash
# Daily check and fix script
#!/bin/bash
cd /path/to/ai-stack
if ! docker compose exec tailscale ping -c 1 8.8.8.8 >/dev/null 2>&1; then
    echo "Tailscale network broken, fixing..."
    docker compose down tailscale
    docker compose up -d tailscale
fi
```

### Network Namespace Issues

#### Broken Network Sharing (Most Common Recurring Issue)
**Symptoms**: 
- Tailscale shows "Network unreachable" 
- Can't connect to DERP servers
- `ping` fails from Tailscale container
- Only loopback interface visible in Tailscale container

**Root Cause**: 
When OpenWebUI container is recreated (new container ID), Tailscale loses its network namespace attachment.

**Quick Diagnostic**:
```bash
# Test internet from both containers
docker compose exec openwebui curl -I http://google.com  # Should work
docker compose exec tailscale ping -c 1 8.8.8.8         # Fails if broken

# Check network interfaces
docker compose exec tailscale ip addr
# Broken: Only shows '1: lo: <LOOPBACK...'
# Working: Shows eth0 or similar network interface
```

**Immediate Fix**:
```bash
# Restart both containers in correct order
docker compose down tailscale
docker compose up -d tailscale
```

**Permanent Monitoring Solution**:
Create a monitoring script that runs every 10 minutes:

```bash
#!/bin/bash
# Save as: scripts/check-tailscale-health.sh
cd "d:\Open WebUI\ai-stack"

# Test network connectivity
if ! docker compose exec tailscale ping -c 1 8.8.8.8 >/dev/null 2>&1; then
    echo "$(date): Tailscale network broken, restarting..." >> logs/tailscale-health.log
    docker compose down tailscale
    docker compose up -d tailscale
    echo "$(date): Tailscale restarted" >> logs/tailscale-health.log
fi
```

**Windows Task Scheduler Setup**:
```powershell
# Run this PowerShell command as Administrator
schtasks /create /tn "TailscaleHealthCheck" /tr "powershell.exe -File 'C:\path\to\scripts\check-tailscale-health.ps1'" /sc minute /mo 10
```

### Advanced Debugging

#### Packet Analysis
```bash
# Install tcpdump in container
docker compose exec tailscale apk add tcpdump

# Monitor traffic
docker compose exec tailscale tcpdump -i any port 443
```

#### State Inspection
```bash
# Check tailscale state files
docker compose exec tailscale ls -la /var/lib/tailscale/
docker compose exec tailscale cat /var/lib/tailscale/tailscaled.state
```

## 🔄 Update Procedures

### Safe Update Process

1. **Backup Critical Data**:
   ```bash
   # Backup Tailscale state
   cp -r data/tailscale data/tailscale.backup
   
   # Backup OpenWebUI data
   cp -r data/openwebui data/openwebui.backup
   ```

2. **Update Services**:
   ```bash
   # Pull latest images (excluding Tailscale)
   docker compose pull openwebui ollama watchtower
   
   # Restart services
   docker compose up -d
   ```

3. **Update Tailscale** (if needed):
   ```bash
   # Update base image in Dockerfile
   # Rebuild custom image
   docker compose build --no-cache tailscale
   docker compose up -d tailscale
   ```

### Watchtower Configuration

Watchtower automatically updates containers except Tailscale:
```yaml
tailscale:
  labels:
    - "com.centurylinklabs.watchtower.enable=false"
```

This prevents breaking the custom Tailscale configuration.

## 📊 Monitoring

### Health Checks
```bash
# Check all service health
docker compose ps

# Monitor resource usage
docker stats

# Check disk usage
docker system df
```

### Log Monitoring
```bash
# Tail all logs
docker compose logs -f

# Monitor specific issues
docker compose logs | grep -E "(error|failed|warning)"
```

## 🔧 Maintenance

### Regular Maintenance Tasks

1. **Weekly**:
   - Check service health: `docker compose ps`
   - Review logs for errors: `docker compose logs | grep error`
   - Monitor disk usage: `docker system df`

2. **Monthly**:
   - Clean unused images: `docker system prune`
   - Update non-Tailscale services: `docker compose pull`
   - Backup critical data directories

3. **Quarterly**:
   - Review and rotate Tailscale auth keys
   - Update Tailscale base image if needed
   - Full system backup

### Performance Optimization

```bash
# Clean up unused resources
docker system prune -a

# Optimize Ollama models (remove unused)
docker compose exec ollama ollama list
docker compose exec ollama ollama rm <model-name>

# Monitor memory usage
docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

## 🆘 Emergency Procedures

### Quick Recovery Options

#### Network/Connectivity Issues (Most Common)
```bash
# Quick namespace reset
scripts\quick-fixes.bat namespace

# Advanced PowerShell recovery
.\scripts\emergency-recovery.ps1 -Action recover
```

#### GPU Issues
```bash
# GPU-specific recovery
scripts\quick-fixes.bat gpu

# Advanced GPU reset
.\scripts\emergency-recovery.ps1 -Action gpu-reset
```

#### Complete System Issues
```bash
# Nuclear option (quick)
scripts\quick-fixes.bat nuclear

# Advanced nuclear option with health checks
.\scripts\emergency-recovery.ps1 -Action nuclear
```

### Complete Reset
```bash
# Stop all services
docker compose down

# Remove all containers and networks
docker compose down --volumes --remove-orphans

# Rebuild everything
docker compose build --no-cache
docker compose up -d
```

### Recover from Tailscale Issues
```bash
# Reset Tailscale only
docker compose stop tailscale
docker compose build --no-cache tailscale
docker compose start tailscale

# If needed, clear Tailscale state (WILL LOSE DEVICE IDENTITY)
rm -rf data/tailscale/*
docker compose restart tailscale
```

## 📞 Support

### Useful Commands Summary
```bash
# Quick status check
docker compose ps && docker compose exec tailscale tailscale status

# GPU status check
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# Full system logs
docker compose logs --tail=100

# Quick recovery options
scripts\quick-fixes.bat namespace        # Network issues (most common)
scripts\quick-fixes.bat gpu             # GPU issues
scripts\quick-fixes.bat status          # System overview

# Advanced recovery
.\scripts\emergency-recovery.ps1 -Action recover    # Full recovery with health checks
.\scripts\emergency-recovery.ps1 -Action gpu-reset  # GPU-specific recovery

# Emergency restart
docker compose restart

# Complete rebuild
docker compose down && docker compose build --no-cache && docker compose up -d
```

### Getting Help

1. **Check logs first**: `docker compose logs <service>`
2. **Verify configuration**: Review critical sections marked with 🔴
3. **Test connectivity**: Use debug commands provided above
4. **Check Tailscale admin console** for device status
5. **Review this README** for similar issues

---

## 📝 Notes

- This setup is optimized for development and small-scale production use
- GPU acceleration available for both Ollama and OpenWebUI components
- Custom OpenWebUI build includes CUDA-enabled PyTorch for reranker models
- Enhanced recovery scripts provide multiple repair options with health monitoring
- Tailscale provides zero-config VPN access
- All data persists across container restarts
- Watchtower keeps services updated automatically (except Tailscale)

**Last Updated**: September 19, 2025  
**OpenWebUI**: Custom GPU-enabled build  
**Tailscale Version**: v1.84.3  
**Docker Compose Version**: v2.x
