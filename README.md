# AI Stack with Tailscale VPN

A comprehensive Docker Compose setup for running OpenWebUI, Ollama, and secure remote access via Tailscale VPN.

## 🚀 Overview

This project provides a complete AI chat interface with:
- **OpenWebUI**: Modern web interface for AI models with GPU-accelerated reranker models
- **Ollama**: Local LLM hosting and management
- **Tailscale**: Secure VPN access for remote connections
- **Watchtower**: Automatic container updates
- **GPU Support**: NVIDIA GPU acceleration for both Ollama and OpenWebUI components
- **AI Stack Pipe Functions**: Unified intelligent routing system for system management and automation

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
│   ├── ai_pipes/              # AI Stack pipe function modules
│   │   ├── ai_stack_router.py        # Unified intelligent router
│   │   ├── unified_openwebui_pipe.py # Single OpenWebUI integration
│   │   ├── gpu_status_pipe.py        # GPU monitoring and diagnostics
│   │   ├── emergency_recovery_pipe.py # System recovery automation
│   │   ├── system_health_pipe.py     # Health monitoring
│   │   ├── custom_tools_pipe.py      # Tool discovery and automation
│   │   └── help_pipe.py              # Help system and documentation
│   ├── emergency-recovery.ps1 # Advanced PowerShell recovery tool
│   ├── emergency-recovery.bat # Enhanced legacy recovery script  
│   ├── quick-fixes.bat        # Simple targeted fixes
│   └── ...                   # Other utility scripts
├── core/                      # Refactored architecture (manifest-driven)
│   ├── router.py              # Core manifest-driven router
│   ├── openwebui_adapter.py   # OpenWebUI integration layer
│   └── legacy_adapter.py      # Legacy pipe compatibility
├── modules/                   # Refactored modular components
│   └── gpu-status/            # Example refactored module
│       ├── manifest.json      # Module capabilities definition
│       ├── module.py          # Module implementation
│       └── schema.json        # Input/output contracts
├── schemas/                   # JSON Schema definitions
│   ├── request_envelope.schema.json  # Request validation
│   ├── module_result.schema.json     # Response validation
│   └── module_manifest.schema.json   # Module contracts
├── tools/                     # Development and migration tools
│   ├── refactor_orchestrator.py      # Automated refactoring
│   ├── migration_tool.py             # Legacy-to-manifest migration
│   ├── scaffold_generator.py         # New module scaffolding
│   └── validation_tool.py            # Schema and contract validation
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

### AI Stack Pipe Functions Setup
The AI Stack includes an intelligent pipe function system that provides:
- **Unified Interface**: Single pipe function in OpenWebUI instead of multiple separate functions
- **Intelligent Routing**: Natural language analysis routes requests to appropriate modules
- **System Management**: GPU monitoring, health checks, emergency recovery, and automation tools
- **Manifest-Driven Architecture**: Modern architecture with explicit contracts and schema validation

#### Pipe Function Architecture
The system includes both legacy and refactored architectures:

**Current Legacy System** (`scripts/ai_pipes/`):
- `unified_openwebui_pipe.py`: Single OpenWebUI integration point
- `ai_stack_router.py`: Intelligent routing based on keyword analysis
- Individual pipe modules: `gpu_status_pipe.py`, `emergency_recovery_pipe.py`, `system_health_pipe.py`, etc.

**Refactored Architecture** (`core/`, `modules/`, `schemas/`):
- **Manifest-driven modules** with explicit capability definitions
- **Schema validation** for all requests and responses  
- **Comprehensive observability** and error handling
- **Automated migration tools** from legacy to new architecture

#### Setup OpenWebUI Pipe Function
1. **Mount scripts directory** in `docker-compose.yml`:
   ```yaml
   openwebui:
     volumes:
       - ./scripts:/host_scripts:ro  # Expose pipe functions
   ```

2. **Access OpenWebUI Admin** → Functions → Create New Function

3. **Paste the unified pipe function** from `scripts/ai_pipes/unified_openwebui_pipe.py`

4. **Test with queries** like:
   - "Check GPU status"
   - "System health report"  
   - "Run emergency recovery"
   - "Show available tools"

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

### AI Stack Management (via Pipe Functions)
Once the unified pipe function is installed in OpenWebUI, you can manage the system through natural language:

**GPU Management**:
- "Check GPU status" - Monitor GPU availability and usage
- "GPU diagnostics" - Comprehensive GPU health report

**System Health**:
- "System health" - Overall system status and diagnostics
- "Container status" - Docker container health checks

**Emergency Recovery**:
- "Fix network issues" - Resolve Tailscale connectivity problems
- "Restart services" - Graceful service recovery
- "Emergency recovery" - Complete system recovery procedures

**Tool Discovery**:
- "Available tools" - List all available management functions
- "Help" - Get assistance with system commands

## 🏗️ AI Stack Pipe Function Architecture

### Overview
The AI Stack includes a sophisticated pipe function system that bridges OpenWebUI with host system management capabilities. This provides autonomous system management through natural language interfaces.

### Architecture Components

#### 1. Current Legacy System (`scripts/ai_pipes/`)
**Unified OpenWebUI Integration**:
- `unified_openwebui_pipe.py`: Single pipe function for OpenWebUI
- `ai_stack_router.py`: Intelligent routing engine with keyword analysis
- Modular pipe functions for specific capabilities

**Available Modules**:
- `gpu_status_pipe.py`: GPU monitoring, CUDA diagnostics, performance metrics
- `emergency_recovery_pipe.py`: Automated recovery procedures, service restart automation
- `system_health_pipe.py`: Container health checks, resource monitoring, connectivity testing
- `custom_tools_pipe.py`: Tool discovery, script automation, workflow management
- `help_pipe.py`: Interactive help system, command discovery, documentation access

**Key Features**:
- **Natural Language Processing**: Analyzes user input and routes to appropriate modules
- **GPU Integration**: Leverages custom CUDA-enabled OpenWebUI build for GPU diagnostics
- **Recovery Automation**: Integrates with PowerShell recovery scripts for autonomous problem resolution
- **Security Hardened**: Read-only script mounts, container isolation, structured logging

#### 2. Refactored Architecture (`core/`, `modules/`, `schemas/`)
**Modern Manifest-Driven Design**:
- **Explicit Contracts**: JSON Schema validation for all communications
- **Module Isolation**: Independent modules with clear capability definitions
- **Comprehensive Observability**: Structured logging, error handling, health monitoring
- **Automated Testing**: Built-in validation and testing frameworks

**Core Components**:
- `core/router.py`: Advanced routing with schema validation and module registry
- `core/openwebui_adapter.py`: OpenWebUI integration layer with backward compatibility
- `core/legacy_adapter.py`: Bridge between legacy and refactored systems
- `schemas/*.json`: Request/response contracts and module manifests
- `tools/`: Migration automation, scaffolding, and validation utilities

### Implementation Guide

#### Quick Setup (Legacy System)
1. **Enable pipe functions** in `docker-compose.yml`:
   ```yaml
   openwebui:
     volumes:
       - ./scripts:/host_scripts:ro
   ```

2. **Install in OpenWebUI**:
   - Admin → Functions → Create New
   - Copy content from `scripts/ai_pipes/unified_openwebui_pipe.py`
   - Save and test with "Check GPU status"

#### Advanced Setup (Refactored Architecture)
```bash
# Automated refactoring (recommended)
python tools/refactor_orchestrator.py

# Manual phase-by-phase implementation
python tools/refactor_orchestrator.py --phase 1  # Core infrastructure
python tools/refactor_orchestrator.py --phase 2  # Module system
python tools/refactor_orchestrator.py --phase 3  # Migration
python tools/refactor_orchestrator.py --phase 4  # Validation
```

### Usage Examples

**System Management through Natural Language**:
```
"Check GPU status" → gpu_status_pipe.py → GPU diagnostics report
"System health" → system_health_pipe.py → Container health checks
"Fix network issues" → emergency_recovery_pipe.py → Automated recovery
"Available tools" → custom_tools_pipe.py → Tool discovery
"Help with commands" → help_pipe.py → Interactive assistance
```

**Advanced Capabilities**:
- **GPU Monitoring**: Real-time CUDA diagnostics, memory usage, temperature monitoring
- **Container Orchestration**: Health checks, dependency management, graceful restarts
- **Network Recovery**: Automated Tailscale namespace fixes, connectivity restoration
- **Security Monitoring**: Container isolation checks, resource usage alerts
- **Performance Optimization**: Resource allocation recommendations, bottleneck identification

### Development and Extension

#### Creating New Modules (Legacy)
1. Create new pipe module in `scripts/ai_pipes/`
2. Implement `main(payload)` function
3. Add routing keywords to `ai_stack_router.py`
4. Test through unified pipe function

#### Creating New Modules (Refactored)
```bash
# Generate module scaffold
python tools/scaffold_generator.py --name my-module --type system-management

# Implement module following manifest contract
# Validate with schema tools
python tools/validation_tool.py --module modules/my-module/
```

### Migration Path

**Phase 1**: Use legacy system for immediate functionality  
**Phase 2**: Gradually migrate modules to manifest-driven architecture  
**Phase 3**: Full migration with comprehensive testing and validation  
**Phase 4**: Deprecate legacy system, maintain only refactored architecture

The system is designed for seamless migration with zero downtime and backward compatibility throughout the transition.

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

## ⚠️ Critical Areas & Cautions

### 🔴 Pipe Function Integration
**Location**: `scripts/ai_pipes/`, `docker-compose.yml`

**Critical Points**:
- **Script mount required**: `./scripts:/host_scripts:ro` volume mount enables pipe function access
- **Read-only security**: Scripts mounted with `:ro` flag for container security
- **Path consistency**: All pipe modules expect `/host_scripts/ai_pipes/` path structure
- **OpenWebUI compatibility**: Use only `unified_openwebui_pipe.py` as single entry point
- **Router dependencies**: `ai_stack_router.py` must be accessible for intelligent routing

```yaml
# ✅ CORRECT - Secure script mounting
openwebui:
  volumes:
    - ./scripts:/host_scripts:ro  # Read-only access

# ❌ WRONG - Don't give write access
openwebui:
  volumes:
    - ./scripts:/host_scripts     # Security risk
```

### 🔴 Module Architecture Transition
**Location**: `core/`, `modules/`, `schemas/`, `tools/`

**Critical Points**:
- **Two systems coexist**: Legacy (`scripts/ai_pipes/`) and refactored (`core/`) architectures
- **Migration required**: Use `tools/refactor_orchestrator.py` for safe migration
- **Schema validation**: All refactored modules require JSON Schema compliance
- **Backward compatibility**: Legacy adapter ensures continuous operation during migration
- **Don't mix architectures**: Use either legacy OR refactored system, not both simultaneously

### 🔴 GPU Integration Dependencies
**Location**: `Dockerfile.openwebui-gpu`, pipe function modules

**Critical Points**:
- **Custom CUDA build required**: Pipe functions rely on GPU-enabled OpenWebUI container
- **PyTorch availability**: GPU status modules require CUDA-enabled PyTorch installation
- **Container GPU access**: Pipe functions access GPU through shared container environment
- **Environment variables**: `USE_CUDA=true` required for GPU module functionality

## 🐛 Debugging

### Enhanced Recovery Scripts

The project includes multiple recovery tools for different scenarios:

#### 1. AI Stack Pipe Function Debugging
**Test pipe function integration**:
```bash
# Verify script mount is working
docker compose exec openwebui ls -la /host_scripts/ai_pipes/

# Test unified pipe function directly
docker compose exec openwebui python /host_scripts/ai_pipes/unified_openwebui_pipe.py

# Check router functionality
docker compose exec openwebui python /host_scripts/ai_pipes/ai_stack_router.py '{"input": "gpu status"}'

# Validate individual modules
docker compose exec openwebui python /host_scripts/ai_pipes/gpu_status_pipe.py '{"input": "status"}'
```

**Common Pipe Function Issues**:
```bash
# Module not found errors
docker compose exec openwebui python -c "import sys; print(sys.path)"

# GPU module issues
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# Router routing problems
docker compose logs openwebui | grep -i "router\|pipe"
```

#### 2. PowerShell Advanced Recovery (`emergency-recovery.ps1`)
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

# AI Stack pipe function testing
docker compose exec openwebui ls /host_scripts/ai_pipes/                    # Verify script mount
docker compose exec openwebui python /host_scripts/ai_pipes/ai_stack_router.py '{"input": "gpu status"}'  # Test router
docker compose exec openwebui python /host_scripts/ai_pipes/unified_openwebui_pipe.py  # Test unified pipe

# Full system logs
docker compose logs --tail=100

# Quick recovery options
scripts\quick-fixes.bat namespace        # Network issues (most common)
scripts\quick-fixes.bat gpu             # GPU issues
scripts\quick-fixes.bat status          # System overview

# Advanced recovery
.\scripts\emergency-recovery.ps1 -Action recover    # Full recovery with health checks
.\scripts\emergency-recovery.ps1 -Action gpu-reset  # GPU-specific recovery

# AI Stack pipe function development
python tools/refactor_orchestrator.py --dry-run     # Preview refactoring changes
python tools/migration_tool.py --analyze-only       # Analyze legacy modules
python tools/validation_tool.py --all               # Validate all schemas and modules

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
- **AI Stack Pipe Functions**: Unified intelligent system management through natural language interface
- **Dual Architecture Support**: Both legacy pipe system and modern manifest-driven architecture available
- Enhanced recovery scripts provide multiple repair options with health monitoring
- Tailscale provides zero-config VPN access
- All data persists across container restarts
- Watchtower keeps services updated automatically (except Tailscale)

**Last Updated**: September 26, 2025  
**OpenWebUI**: Custom GPU-enabled build with AI Stack pipe functions  
**Tailscale Version**: v1.84.3  
**Docker Compose Version**: v2.x  
**AI Stack Architecture**: Legacy + Refactored (Manifest-driven)
