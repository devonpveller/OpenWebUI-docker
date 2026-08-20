# Step-by-Step Guide: Adding Python Scripts to OpenWebUI Pipes

This guide provides clear instructions for integrating Python scripts into your AI Stack's OpenWebUI pipe system. Whether you're a developer or an autonomous coding agent, this will help you make the right decisions about script integration.

---
## Decision Matrix: New Pipe vs. Extending Existing Pipe

### 🆕 Create a NEW Pipe When:

1. **Different Purpose/Domain**
   - Script serves a completely different function (e.g., adding a file converter when you have a GPU monitor)
   - Targets different user workflows
   - Has different security requirements

2. **Different Execution Requirements**
   - Needs different timeout settings (e.g., long-running analysis vs. quick status check)
   - Requires different GPU/resource access patterns
   - Has different error handling needs

3. **Independent Configuration**
   - Script needs its own set of configuration parameters
   - Different logging requirements
   - Separate access control or permissions

4. **User Experience**
   - Should appear as a separate "model" in OpenWebUI sidebar
   - Users need to select it specifically for distinct tasks
   - Different conversation contexts

### 🔧 EXTEND Existing Pipe When:

1. **Related Functionality**
   - Script is a logical extension of existing pipe (e.g., adding memory stats to GPU status pipe)
   - Shares the same domain/purpose
   - Users would expect it as part of the same tool

2. **Shared Configuration**
   - Uses same timeout, logging, and security settings
   - Benefits from existing error handling patterns
   - Shares common utility functions

3. **Workflow Integration**
   - Script enhances existing workflow rather than creating new one
   - Should be triggered by same user contexts
   - Complements existing pipe functionality

---
## Step-by-Step: Adding a Script to EXISTING Pipe

### Step 1: Analyze the Existing Pipe Structure
```bash
# Navigate to your scripts directory
cd "d:\Open WebUI\ai-stack\scripts\ai_pipes"

# Examine existing pipe script
code gpu_status_pipe.py  # or whatever pipe you're extending
```

### Step 2: Understand Current Functionality
Look for these key elements in the existing script:
- **Main function signature**: `def main(payload):`
- **Input processing**: How it handles `payload.get("input", "")`
- **Return format**: JSON structure it returns
- **Error handling patterns**: How exceptions are managed

### Step 3: Plan Your Integration
Ask yourself:
- Does my new functionality fit logically with existing features?
- Can I add it as a new function called by `main()`?
- Will it use the same input parameters?
- Should it return data in the same format?

### Step 4: Implement the Extension

#### Example: Adding Memory Stats to GPU Status Pipe
```python
# scripts/ai_pipes/gpu_status_pipe.py (BEFORE)
import torch
import json

def main(payload):
    """GPU status check leveraging your CUDA setup"""
    if not torch.cuda.is_available():
        return {
            "status": "❌ GPU Not Available",
            "recommendation": "Check CUDA installation...",
            "recovery_command": "scripts\\quick-fixes.bat gpu"
        }
    
    device_info = {
        "status": "✅ GPU Available",
        "device_count": torch.cuda.device_count(),
        "current_device": torch.cuda.current_device(),
        "device_name": torch.cuda.get_device_name(),
        "memory_allocated": f"{torch.cuda.memory_allocated() / 1024**3:.2f} GB",
        "memory_reserved": f"{torch.cuda.memory_reserved() / 1024**3:.2f} GB"
    }
    
    return device_info
```

```python
# scripts/ai_pipes/gpu_status_pipe.py (AFTER - Extended)
import torch
import json
import psutil  # NEW: Add system memory monitoring

def get_gpu_info():
    """Existing GPU information gathering"""
    if not torch.cuda.is_available():
        return {
            "status": "❌ GPU Not Available",
            "recommendation": "Check CUDA installation...",
            "recovery_command": "scripts\\quick-fixes.bat gpu"
        }
    
    return {
        "status": "✅ GPU Available",
        "device_count": torch.cuda.device_count(),
        "current_device": torch.cuda.current_device(),
        "device_name": torch.cuda.get_device_name(),
        "memory_allocated": f"{torch.cuda.memory_allocated() / 1024**3:.2f} GB",
        "memory_reserved": f"{torch.cuda.memory_reserved() / 1024**3:.2f} GB"
    }

def get_system_memory_info():  # NEW: System memory function
    """Get system memory statistics"""
    memory = psutil.virtual_memory()
    return {
        "total_ram": f"{memory.total / 1024**3:.2f} GB",
        "available_ram": f"{memory.available / 1024**3:.2f} GB",
        "used_ram": f"{memory.used / 1024**3:.2f} GB",
        "ram_percent": f"{memory.percent}%"
    }

def main(payload):
    """Enhanced GPU and system status check"""
    user_input = payload.get("input", "").lower()
    
    # Get GPU information
    gpu_info = get_gpu_info()
    
    # Check if user wants system memory info too
    include_system_memory = any(keyword in user_input for keyword in 
                               ["memory", "ram", "system", "full"])
    
    result = {
        "gpu": gpu_info,
        "timestamp": payload.get("timestamp", "unknown")
    }
    
    # Conditionally add system memory info
    if include_system_memory:
        result["system_memory"] = get_system_memory_info()
    
    return result
```

### Step 5: Test the Extended Pipe
1. **Save your changes**
2. **Restart OpenWebUI container** (if needed):
   ```powershell
   docker compose restart openwebui
   ```
3. **Test in OpenWebUI**:
   - Select your pipe as a model
   - Test basic functionality: "Check GPU status"
   - Test new functionality: "Check GPU and system memory"

### Step 6: Update Documentation
Add comments explaining:
- What new functionality was added
- How to trigger the new features
- Any new dependencies required

---
## Step-by-Step: Creating a NEW Pipe

### Step 1: Determine Script Purpose and Name
Choose a descriptive name that clearly indicates function:
- `system_diagnostics_pipe.py` - System health checking
- `file_operations_pipe.py` - File manipulation tasks
- `docker_management_pipe.py` - Docker container operations

### Step 2: Choose Script Location and Template
```powershell
# Navigate to AI pipes directory
cd "d:\Open WebUI\ai-stack\scripts\ai_pipes"

# Copy appropriate template
copy ..\templates\ai_stack_library_template.py your_new_pipe.py
```

### Step 3: Customize the Template

#### Example: Creating a Docker Management Pipe
```python
# scripts/ai_pipes/docker_management_pipe.py
import subprocess
import json
import re

def get_container_status():
    """Get status of AI stack containers"""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="d:\\Open WebUI\\ai-stack"  # Your AI stack directory
        )
        
        if result.returncode == 0:
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    containers.append(json.loads(line))
            return {
                "status": "✅ Containers Retrieved",
                "containers": containers,
                "count": len(containers)
            }
        else:
            return {
                "status": "❌ Failed to get container status",
                "error": result.stderr
            }
    except Exception as e:
        return {
            "status": "❌ Docker command failed",
            "error": str(e)
        }

def restart_service(service_name):
    """Restart a specific AI stack service"""
    valid_services = ["openwebui", "ollama", "tailscale", "watchtower"]
    
    if service_name not in valid_services:
        return {
            "status": "❌ Invalid service",
            "error": f"Service must be one of: {valid_services}",
            "requested": service_name
        }
    
    try:
        result = subprocess.run(
            ["docker", "compose", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=30,
            cwd="d:\\Open WebUI\\ai-stack"
        )
        
        if result.returncode == 0:
            return {
                "status": f"✅ {service_name} restarted successfully",
                "service": service_name,
                "output": result.stdout
            }
        else:
            return {
                "status": f"❌ Failed to restart {service_name}",
                "error": result.stderr
            }
    except Exception as e:
        return {
            "status": "❌ Restart command failed",
            "error": str(e)
        }

def main(payload):
    """Main entry point for Docker management operations"""
    user_input = payload.get("input", "").lower()
    
    try:
        # Parse user intent
        if "status" in user_input or "containers" in user_input:
            return get_container_status()
        
        elif "restart" in user_input:
            # Extract service name from input
            service_patterns = {
                "openwebui": ["openwebui", "open-webui", "webui"],
                "ollama": ["ollama"],
                "tailscale": ["tailscale", "vpn"],
                "watchtower": ["watchtower", "updater"]
            }
            
            for service, patterns in service_patterns.items():
                if any(pattern in user_input for pattern in patterns):
                    return restart_service(service)
            
            return {
                "status": "❓ Unclear restart request",
                "help": "Specify which service to restart: openwebui, ollama, tailscale, or watchtower",
                "example": "restart ollama"
            }
        
        else:
            return {
                "status": "ℹ️ Docker Management Help",
                "available_commands": [
                    "Check container status",
                    "Restart [service_name]"
                ],
                "services": ["openwebui", "ollama", "tailscale", "watchtower"],
                "examples": [
                    "Show container status",
                    "Restart ollama",
                    "Restart tailscale"
                ]
            }
            
    except Exception as e:
        return {
            "status": "❌ Docker management error",
            "error": str(e)
        }
```

### Step 4: Create the OpenWebUI Pipe Function
1. **Access OpenWebUI** via your Tailscale IP
2. **Go to Admin → Functions → New Function**
3. **Name it clearly**: "Docker Management - AI Stack"
4. **Paste the enhanced pipe template** from the main guide
5. **Configure the valves**:
   - `SCRIPT_PATH`: `/host_scripts/ai_pipes/docker_management_pipe.py`
   - `ENTRYPOINT`: `main`
   - `EXEC_MODE`: `import`
   - `TIMEOUT_SEC`: `60`
   - `ENABLE_GPU_CHECK`: `false` (not needed for Docker ops)
   - `LOG_EXECUTION`: `true`

### Step 5: Add Required Docker Compose Volume
Update your `docker-compose.yml` to include the scripts mount (if not already added):

```yaml
services:
  openwebui:
    # ... existing configuration
    volumes:
      - ./data/openwebui:/app/backend/data
      - ./config:/app/config:ro
      - ./scripts:/host_scripts:ro  # Ensure this line exists
```

### Step 6: Test the New Pipe
1. **Restart OpenWebUI** to pick up volume changes:
   ```powershell
   docker compose restart openwebui
   ```
2. **Select your new pipe** as a model in OpenWebUI
3. **Test basic functionality**:
   - "Show container status"
   - "Restart ollama"
   - "Help" (to see available commands)

---
## Troubleshooting Common Issues

### Script Not Found Error
```
❌ Script not found: /host_scripts/ai_pipes/your_script.py
```
**Solution**: Check volume mount and file path:
```powershell
# Verify file exists on host
ls "d:\Open WebUI\ai-stack\scripts\ai_pipes\your_script.py"

# Check container mount
docker compose exec openwebui ls /host_scripts/ai_pipes/
```

### Import/Module Errors
```
❌ Import execution error: No module named 'some_module'
```
**Solution**: Install missing dependencies in the container:
```dockerfile
# Add to Dockerfile.openwebui-gpu
RUN pip install psutil docker  # or whatever modules you need
```

### Permission Errors
```
❌ Permission denied
```
**Solution**: Ensure scripts are readable and volume is mounted correctly:
```powershell
# Check file permissions
icacls "d:\Open WebUI\ai-stack\scripts\ai_pipes\*.py"
```

### Function Not Available in Available Functions
**Solution**: Function names must be unique. If extending existing functionality, consider:
1. Updating the existing pipe instead of creating new one
2. Using a clearly different name
3. Checking OpenWebUI logs for errors

---
## Best Practices Summary

### For Script Extensions:
1. **Maintain backwards compatibility** - existing functionality should still work
2. **Use clear function names** that indicate purpose
3. **Add conditional logic** to handle different user inputs
4. **Keep error handling consistent** with existing patterns

### For New Pipes:
1. **Choose descriptive names** that clearly indicate function
2. **Follow the template structure** for consistency
3. **Include comprehensive help** in the main function
4. **Test thoroughly** before deploying to production

### For Both:
1. **Document your changes** in code comments
2. **Test with various inputs** to ensure robustness
3. **Consider security implications** of any new functionality
4. **Follow your AI stack's logging patterns** for consistency

This guide ensures that script integration follows your AI stack's patterns while providing clear decision-making criteria for when to extend vs. create new pipes.