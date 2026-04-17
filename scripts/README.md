# AI Stack Scripts Directory

This directory contains scripts and tools for the OpenWebUI AI Stack, including pipe functions that expose host scripts to the containerized OpenWebUI environment.

## Directory Structure

```
scripts/
├── ai_pipes/                    # Pipe functions for OpenWebUI integration
│   ├── __init__.py
│   ├── config.json             # Central configuration
│   ├── emergency_recovery_pipe.py    # Recovery system integration
│   ├── gpu_status_pipe.py      # GPU monitoring and status
│   ├── system_health_pipe.py   # System health monitoring  
│   └── custom_tools_pipe.py    # Custom automation tools
├── utilities/                   # Helper modules
│   ├── __init__.py
│   ├── docker_helpers.py       # Docker operations
│   ├── system_helpers.py       # System monitoring
│   └── gpu_helpers.py          # GPU utilities
├── templates/                   # Templates for new pipe scripts
│   ├── ai_stack_cli_template.py      # CLI-based template
│   └── ai_stack_library_template.py  # Library-based template
├── emergency-recovery.ps1       # Advanced PowerShell recovery
├── emergency-recovery.bat       # Enhanced legacy recovery
├── quick-fixes.bat             # Quick targeted fixes
├── update-stack.bat            # Manual update manager
├── simple-monitor.ps1          # Background monitoring
├── check-tailscale-health.ps1  # Tailscale health service
└── ... (other existing scripts)
```

## Pipe Functions Overview

### Emergency Recovery Pipe (`emergency_recovery_pipe.py`)
Integrates with existing recovery systems to provide AI-driven system recovery through OpenWebUI conversations.

**Features:**
- Analyzes user input for issue types
- Suggests appropriate recovery actions
- Maps to existing recovery scripts (quick-fixes.bat, emergency-recovery.ps1)
- Provides escalation paths and safety warnings

**Usage in OpenWebUI:**
- "network issues" → suggests namespace reset
- "gpu problems" → suggests GPU recovery
- "system status" → suggests health check
- "complete restart" → suggests nuclear option (with warnings)

### GPU Status Pipe (`gpu_status_pipe.py`)
Provides comprehensive GPU monitoring leveraging the custom CUDA setup.

**Features:**
- PyTorch and CUDA availability checking
- GPU memory usage monitoring
- Device information and diagnostics
- Integration with container GPU passthrough
- Recovery suggestions for GPU issues

**Usage in OpenWebUI:**
- "gpu status" → basic GPU information
- "detailed gpu status" → comprehensive diagnostics
- "gpu memory" → memory usage details

### System Health Pipe (`system_health_pipe.py`)
Monitors overall AI stack system health including Docker services and resources.

**Features:**
- Docker Compose service monitoring
- Critical service status (openwebui, ollama, tailscale)
- Resource usage monitoring (when psutil available)
- Health scoring and issue analysis
- Recovery recommendations

**Usage in OpenWebUI:**
- "system health" → overall health summary
- "detailed system status" → comprehensive diagnostics
- "service status" → Docker service information

### Custom Tools Pipe (`custom_tools_pipe.py`)
Provides integration with custom automation scripts and development tools.

**Features:**
- Tool discovery and enumeration
- Request analysis and tool suggestion
- Execution guidance for PowerShell/batch scripts
- Integration with existing AI stack tooling

**Usage in OpenWebUI:**
- "available tools" → list all available tools
- "recovery tools" → show recovery options
- "monitoring tools" → show monitoring utilities

## Setting Up Pipe Functions

### 1. Update Docker Compose
The `docker-compose.yml` has been updated to mount the scripts directory:

```yaml
volumes:
  - ./scripts:/host_scripts:ro  # Expose scripts directory
```

### 2. Create Pipe Function in OpenWebUI
1. Access OpenWebUI at your Tailscale URL
2. Go to **Admin → Functions → New Function**
3. Paste the enhanced pipe template (see guide documentation)
4. Configure valves:
   - `SCRIPT_PATH`: `/host_scripts/ai_pipes/gpu_status_pipe.py`
   - `ENTRYPOINT`: `main`
   - `EXEC_MODE`: `import`
   - `ENABLE_GPU_CHECK`: `true`

### 3. Enhanced Pipe Template
Use this comprehensive template for creating pipe functions:

```python
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import json, os, sys, subprocess, importlib.util
import logging
import torch  # Available due to custom GPU build

class Pipe:
    class Valves(BaseModel):
        SCRIPT_PATH: str = Field(
            default="/host_scripts/ai_pipes/example_script.py",
            description="Path to script in container"
        )
        EXEC_MODE: str = Field(
            default="import", 
            description="'import' or 'subprocess'"
        )
        # ... (see full template in documentation)
    
    # ... (implementation details)
```

## Utility Modules

### Docker Helpers (`utilities/docker_helpers.py`)
- Container status checking
- Service restart operations
- Docker Compose management
- Log retrieval utilities

### System Helpers (`utilities/system_helpers.py`)
- System information gathering
- Process and service monitoring
- Network connectivity testing
- Resource usage monitoring

### GPU Helpers (`utilities/gpu_helpers.py`)
- PyTorch installation checking
- CUDA availability testing
- GPU diagnostics and testing
- Issue diagnosis and solutions

## Templates

### CLI Template (`templates/ai_stack_cli_template.py`)
For scripts that should be executed as subprocesses with JSON I/O.

**Use when:**
- Simple command-line tools
- Scripts that need isolated execution
- External tool integration

### Library Template (`templates/ai_stack_library_template.py`) 
For scripts that should be imported and called as functions.

**Use when:**
- Complex data processing
- GPU-accelerated operations
- Rich Python object handling

## Update Management

### Update Stack Script (`update-stack.bat`)
Manual update manager for OpenWebUI and Ollama with automatic backup and verification.

**Features:**
- Automatic data backup before updates
- Version-specific updates with validation
- GPU compatibility verification
- Service coordination (restart dependent services)
- Rollback guidance on failure

**Usage:**
```bash
# Check current versions
scripts\update-stack.bat check

# Update OpenWebUI (interactive - prompts for version)
scripts\update-stack.bat openwebui

# Update Ollama
scripts\update-stack.bat ollama

# Update both (with confirmation)
scripts\update-stack.bat all
```

**Update Process (OpenWebUI):**
1. **Backup**: Creates timestamped backup of `data/openwebui/`
2. **Version Input**: Prompts for target version (e.g., v0.6.41)
3. **Dockerfile Update**: Modifies `Dockerfile.openwebui-gpu` base image
4. **Rebuild**: Builds custom image with GPU support
5. **Restart**: Coordinates restart of OpenWebUI, Ollama, and Tailscale
6. **Verify**: Tests GPU availability and service health

**Safety Features:**
- Automatic data backup with timestamp
- Build failure detection
- GPU verification post-update
- Service health checks
- Rollback instructions on error

**Docker Compose Changes:**
- `pull_policy: build` for OpenWebUI (prevents auto-pulls on rebuild)
- `pull_policy: never` for Ollama (manual updates only)
- Prevents accidental version changes during `docker compose up`

## Configuration

### Central Configuration (`ai_pipes/config.json`)
Contains AI stack configuration including:
- Workspace paths
- Recovery script locations
- GPU and security settings
- Logging configuration

### Security Features
- **Read-only mounts**: Scripts mounted with `:ro` flag
- **Localhost binding**: Maintains security-hardened approach
- **Container isolation**: Scripts isolated within container environment
- **Structured logging**: Comprehensive error tracking

## Integration Examples

### Emergency Recovery Integration
```python
# User says: "tailscale network issues"
# Pipe analyzes: keyword="network" → action="namespace"  
# Suggests: scripts\quick-fixes.bat namespace
# Provides: safety warnings and execution guidance
```

### GPU Monitoring Integration
```python
# User says: "check gpu memory"
# Pipe checks: torch.cuda.memory_allocated()
# Returns: detailed memory usage per device
# Suggests: recovery actions if issues detected
```

### System Health Integration
```python
# User says: "system status"
# Pipe checks: Docker services, resources, GPU
# Returns: health score and issue analysis
# Provides: specific recommendations for problems
```

## Best Practices

1. **Error Handling**: All pipe scripts include comprehensive error handling
2. **Structured Output**: Consistent JSON response format
3. **Recovery Guidance**: Always provide next steps for issues
4. **GPU Awareness**: Leverage custom CUDA setup appropriately
5. **Security Compliance**: Maintain read-only access patterns
6. **Logging Integration**: Use structured logging compatible with monitoring

## Troubleshooting

### Common Issues

**Pipe function not found:**
- Verify volume mount in docker-compose.yml
- Check script path in valve configuration
- Restart OpenWebUI container: `docker compose restart openwebui`

**Import errors in pipe:**
- Ensure required modules available in container
- Check Python path and module imports
- Verify PyTorch availability for GPU functions

**Permission errors:**
- Confirm read-only mount permissions
- Check script file permissions on host
- Validate container security settings

### Recovery Commands

**Quick diagnostics:**
```bash
# Check container mounts
docker compose exec openwebui ls -la /host_scripts/

# Test script access
docker compose exec openwebui python -c "import sys; sys.path.append('/host_scripts'); import ai_pipes"

# Verify GPU in container
docker compose exec openwebui python -c "import torch; print(torch.cuda.is_available())"
```

**Recovery actions:**
```bash
# Restart OpenWebUI with new mounts
docker compose restart openwebui

# Rebuild if needed
docker compose build --no-cache openwebui && docker compose up -d

# Check logs
docker compose logs openwebui | tail -50
```

## Development Workflow

1. **Create new pipe script** in appropriate directory (`ai_pipes/`, `utilities/`, etc.)
2. **Use templates** as starting points for consistency
3. **Test locally** before container integration
4. **Update config.json** if adding new tools/actions
5. **Document functionality** in this README
6. **Test in OpenWebUI** through pipe function interface

This integration provides seamless access to host Python scripts through OpenWebUI's conversational interface, enabling powerful automation and monitoring capabilities while maintaining the security and isolation of the containerized environment.