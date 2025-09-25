# Open WebUI Pipe Template: Exposing Host Scripts to AI Stack

This guide enables autonomous coding agents and developers to expose Python scripts running on the host machine to **Open WebUI** through **Pipe Functions** in your containerized AI stack. This template provides a bridge between your local Python development environment and the containerized AI services, allowing seamless integration of custom tools, automation scripts, and system management functions.

---
## 1. AI Stack Integration Setup

### Current Docker Compose Configuration
Your existing `docker-compose.yml` uses a custom GPU-enabled OpenWebUI build. To add host script support, you'll need to add a volume mount:

```yaml
services:
  openwebui:
    build:
      context: .
      dockerfile: Dockerfile.openwebui-gpu
    container_name: openwebui
    ports:
      - "127.0.0.1:3000:8080"  # Localhost only (security hardened)
    volumes:
      - ./data/openwebui:/app/backend/data
      - ./config:/app/config:ro  # Matches your actual mount path
      - ./scripts:/host_scripts:ro  # NEW: Expose scripts directory
      # Alternative: Mount any directory with your Python scripts
      # - D:/my_python_scripts:/host_scripts:ro
    environment:
      - OLLAMA_HOST=http://localhost:11434  # Correct - both services share network namespace
      - USE_CUDA=true
      - USE_CUDA_DOCKER=true
      - TRANSFORMERS_CACHE=/tmp/transformers_cache
      - HF_HOME=/tmp/hf_cache
      # ... rest of your existing environment variables
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:noexec,nosuid,size=100m
    depends_on:
      openwebui:
        condition: service_healthy
```

### Security Considerations for AI Stack
- **Read-only mount**: Scripts are mounted with `:ro` flag for security
- **Localhost binding**: Maintains your security-hardened localhost-only approach
- **No new privileges**: Consistent with your `no-new-privileges:true` security model
- **Container isolation**: Scripts remain isolated within the container environment

### Setup Steps
1. **Add volume mount** to your existing `docker-compose.yml` (see above)
2. **Restart OpenWebUI**: `docker compose up -d openwebui`
3. **Access OpenWebUI** at `https://openwebui.tail37f875.ts.net`
4. **Go to Admin → Functions → New Function**
5. **Paste the enhanced pipe template** (below)
6. **Configure valves** from the gear ⚙️ icon
7. **Save** - The function will appear as a selectable model in the sidebar

---
## 2. Enhanced Pipe Function Template for AI Stack

This template provides a robust foundation for exposing host Python scripts to OpenWebUI, with examples optimized for your GPU-enabled environment and recovery automation:

```python
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import json, os, sys, subprocess, importlib.util
import logging
import torch  # Available due to your custom GPU build

class Pipe:
    class Valves(BaseModel):
        SCRIPT_PATH: str = Field(
            default="/host_scripts/example_script.py",
            description="Absolute path to script inside container (mounted from host scripts/ directory)."
        )
        ENTRYPOINT: str = Field(
            default="main",
            description="Function to call if EXEC_MODE='import'. For AI stack scripts, typically 'main' or 'process'."
        )
        EXEC_MODE: str = Field(
            default="import",
            description="Choose 'import' (library mode) or 'subprocess' (CLI mode)."
        )
        TIMEOUT_SEC: int = Field(
            default=120,
            description="Increased timeout for AI/ML processing scripts."
        )
        ENABLE_GPU_CHECK: bool = Field(
            default=True,
            description="Check GPU availability before script execution (leverages your CUDA setup)."
        )
        LOG_EXECUTION: bool = Field(
            default=True,
            description="Log script execution for debugging and monitoring."
        )
        
        @field_validator("EXEC_MODE")
        def check_mode(cls, v):
            if v not in ("import", "subprocess"):
                raise ValueError("EXEC_MODE must be 'import' or 'subprocess'")
            return v

    def __init__(self):
        self.valves = self.Valves()
        self.logger = self._setup_logging()

    def _setup_logging(self):
        """Setup structured logging consistent with AI stack patterns"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(__name__)

    def pipe(self, body: Dict[str, Any]) -> str:
        """Main pipe execution with AI stack optimizations"""
        try:
            # GPU availability check (leverages your custom CUDA build)
            if self.valves.ENABLE_GPU_CHECK:
                gpu_status = self._check_gpu_status()
                if self.valves.LOG_EXECUTION:
                    self.logger.info(f"GPU Status: {gpu_status}")

            user_input = self._extract_user_text(body.get("messages", []))
            payload = {
                "input": user_input, 
                "messages": body.get("messages", []),
                "gpu_available": torch.cuda.is_available() if self.valves.ENABLE_GPU_CHECK else False,
                "workspace_context": "ai-stack"  # Identify source environment
            }
            
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"Executing script: {self.valves.SCRIPT_PATH} in {self.valves.EXEC_MODE} mode")
            
            if self.valves.EXEC_MODE == "import":
                return self._run_import(payload)
            else:
                return self._run_subprocess(payload)
                
        except Exception as e:
            error_msg = f"❌ AI Stack Pipe Error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return error_msg

    def _check_gpu_status(self) -> str:
        """Check GPU status (consistent with your emergency recovery patterns)"""
        try:
            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                current_device = torch.cuda.current_device()
                device_name = torch.cuda.get_device_name(current_device)
                return f"✅ GPU Available: {device_name} ({device_count} devices)"
            else:
                return "⚠️ GPU Not Available (check CUDA configuration)"
        except Exception as e:
            return f"❌ GPU Check Failed: {str(e)}"

    def _run_import(self, payload: Dict[str, Any]) -> str:
        """Import-based execution with enhanced error handling"""
        try:
            if not os.path.exists(self.valves.SCRIPT_PATH):
                return f"❌ Script not found: {self.valves.SCRIPT_PATH}"
                
            spec = importlib.util.spec_from_file_location("_ai_stack_script", self.valves.SCRIPT_PATH)
            if spec is None or spec.loader is None:
                return f"❌ Cannot load script spec: {self.valves.SCRIPT_PATH}"
                
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            if not hasattr(mod, self.valves.ENTRYPOINT):
                available_functions = [attr for attr in dir(mod) if not attr.startswith('_')]
                return f"❌ Function '{self.valves.ENTRYPOINT}' not found. Available: {available_functions}"
            
            func = getattr(mod, self.valves.ENTRYPOINT)
            result = func(payload)
            
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"✅ Script executed successfully: {self.valves.ENTRYPOINT}")
            
            return str(result)
            
        except Exception as e:
            error_msg = f"❌ Import execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return error_msg

    def _run_subprocess(self, payload: Dict[str, Any]) -> str:
        """Subprocess execution with PowerShell compatibility"""
        try:
            if not os.path.exists(self.valves.SCRIPT_PATH):
                return f"❌ Script not found: {self.valves.SCRIPT_PATH}"
            
            # Enhanced subprocess execution compatible with your Windows/PowerShell environment
            proc = subprocess.run(
                [sys.executable, self.valves.SCRIPT_PATH],
                input=json.dumps(payload),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.valves.TIMEOUT_SEC,
                text=True,
                encoding='utf-8',
                cwd=os.path.dirname(self.valves.SCRIPT_PATH)  # Set working directory
            )
            
            if proc.returncode != 0:
                error_output = proc.stderr.strip()
                return f"❌ Script execution failed (exit code {proc.returncode}): {error_output}"
            
            output = proc.stdout.strip()
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"✅ Subprocess executed successfully")
            
            return output if output else "✅ Script completed successfully (no output)"
            
        except subprocess.TimeoutExpired:
            return f"❌ Timeout: Script execution exceeded {self.valves.TIMEOUT_SEC} seconds"
        except Exception as e:
            error_msg = f"❌ Subprocess execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return error_msg

    def _extract_user_text(self, messages: List[Dict[str, Any]]) -> str:
        """Enhanced message extraction supporting various content types"""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    # Handle multimodal content (text, images, etc.)
                    text_items = [
                        item.get('text', '') for item in content 
                        if isinstance(item, dict) and item.get('type') == 'text'
                    ]
                    return ' '.join(text_items)
        return ""
```

---
## 3. AI Stack Directory Structure for Local Script Access

Place scripts in your existing `scripts/` directory to expose them to OpenWebUI:

```
d:\Open WebUI\ai-stack\
├── scripts\                      # Mount this as /host_scripts in container
│   ├── ai_pipes\                # New: Dedicated pipe scripts
│   │   ├── __init__.py
│   │   ├── config.json          # Central configuration
│   │   ├── emergency_recovery_pipe.py  # Integrate with your recovery system
│   │   ├── gpu_status_pipe.py   # GPU monitoring integration
│   │   ├── system_health_pipe.py       # Overall system status
│   │   └── custom_tools_pipe.py        # Your custom automation scripts
│   ├── utilities\               # Enhanced version of existing utilities
│   │   ├── __init__.py
│   │   ├── docker_helpers.py    # Docker compose operations
│   │   ├── system_helpers.py    # System monitoring utilities
│   │   └── gpu_helpers.py       # GPU availability checks
│   ├── templates\               # Templates for new pipe scripts
│   │   ├── ai_stack_cli_template.py
│   │   └── ai_stack_library_template.py
│   └── README.md                # Documentation for pipe scripts
├── data\                        # Your existing data structure
├── config\                      # Your existing config structure
└── ...                          # Rest of your ai-stack structure
```

---
## 4. Practical Integration Examples

These examples demonstrate common use cases for exposing host scripts to OpenWebUI:

### Emergency Recovery Integration
```python
# scripts/ai_pipes/emergency_recovery_pipe.py
import subprocess
import json
import sys

def main(payload):
    """Integrate with your emergency recovery system"""
    user_input = payload.get("input", "").lower()
    
    recovery_actions = {
        "namespace": "scripts\\quick-fixes.bat namespace",
        "gpu": "scripts\\quick-fixes.bat gpu", 
        "status": "scripts\\quick-fixes.bat status",
        "nuclear": "scripts\\quick-fixes.bat nuclear",
        "tailscale": "scripts\\emergency-recovery.ps1 -Action recover",
        "advanced": "scripts\\emergency-recovery.ps1 -Action nuclear"
    }
    
    for keyword, command in recovery_actions.items():
        if keyword in user_input:
            return {
                "action": keyword,
                "suggested_command": command,
                "description": f"Suggested recovery action for {keyword} issues",
                "warning": "This will execute recovery commands - ensure this is intended"
            }
    
    return {
        "available_actions": list(recovery_actions.keys()),
        "description": "AI Stack Emergency Recovery Integration",
        "usage": "Mention keywords like 'namespace', 'gpu', 'tailscale', etc."
    }
```

### GPU Status Integration  
```python
# scripts/ai_pipes/gpu_status_pipe.py
import torch
import json

def main(payload):
    """GPU status check leveraging your CUDA setup"""
    if not torch.cuda.is_available():
        return {
            "status": "❌ GPU Not Available",
            "recommendation": "Check CUDA installation or run emergency recovery with 'gpu' option",
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

### System Health Integration
```python
# scripts/ai_pipes/system_health_pipe.py
import subprocess
import json
import psutil  # Add psutil to your host Python environment

def main(payload):
    """Overall system health check"""
    try:
        # Check Docker service status
        docker_result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="d:\\Open WebUI\\ai-stack"  # Your actual path
        )
        
        health_data = {
            "timestamp": payload.get("timestamp", "unknown"),
            "docker_services": "✅ Running" if docker_result.returncode == 0 else "❌ Issues detected",
            "cpu_usage": f"{psutil.cpu_percent()}%",
            "memory_usage": f"{psutil.virtual_memory().percent}%",
            "disk_usage": f"{psutil.disk_usage('D:').percent}%",  # Adjust drive as needed
            "gpu_status": "✅ Available" if torch.cuda.is_available() else "❌ Not Available"
        }
        
        if docker_result.returncode == 0:
            # Parse container status if available
            try:
                containers = json.loads(docker_result.stdout)
                health_data["container_count"] = len(containers)
                health_data["services"] = [c.get("Service", "unknown") for c in containers]
            except:
                health_data["containers"] = "Status parsing failed"
        
        return health_data
        
    except Exception as e:
        return {
            "status": "❌ Health Check Failed",
            "error": str(e),
            "suggestion": "Check if Docker is running and accessible"
        }
```

---
## 5. Configuration for AI Stack Environment

### Updated Docker Compose Mount
Add to your existing `docker-compose.yml`:

```yaml
services:
  openwebui:
    # ... your existing configuration
    volumes:
      - ./data/openwebui:/app/backend/data
      - ./config:/app/config:ro  # Matches your actual mount path
      - ./scripts:/host_scripts:ro  # NEW: Mount scripts directory
```

### Pipe Function Configuration in OpenWebUI
1. Access OpenWebUI at `https://openwebui.tail37f875.ts.net`
2. Go to **Admin → Functions → New Function**
3. Paste the enhanced pipe template
4. Configure valves:
   - `SCRIPT_PATH`: `/host_scripts/ai_pipes/gpu_status_pipe.py`
   - `ENTRYPOINT`: `main`
   - `EXEC_MODE`: `import`
   - `ENABLE_GPU_CHECK`: `true`
   - `LOG_EXECUTION`: `true`

### Template Scripts for Different Use Cases

#### CLI Template (EXEC_MODE="subprocess")
```python
# scripts/templates/ai_stack_cli_template.py
import sys
import json

def main():
    """Template for CLI-based pipe scripts"""
    try:
        payload = json.loads(sys.stdin.read())
        user_input = payload.get("input", "")
        gpu_available = payload.get("gpu_available", False)
        
        # Your CLI processing logic here
        result = {
            "processed_input": user_input.strip(),
            "gpu_status": "available" if gpu_available else "not_available",
            "workspace": payload.get("workspace_context", "unknown")
        }
        
        print(json.dumps(result))
        
    except Exception as e:
        error_result = {"error": str(e), "type": "CLI execution error"}
        print(json.dumps(error_result))
        sys.exit(1)

if __name__ == "__main__":
    main()
```

#### Library Template (EXEC_MODE="import")
```python
# scripts/templates/ai_stack_library_template.py
import torch

def process_data(payload):
    """Process input data with GPU awareness"""
    input_text = payload.get("input", "")
    gpu_available = payload.get("gpu_available", False)
    
    # Example processing
    processed = {
        "original": input_text,
        "processed": input_text.strip().lower(),
        "word_count": len(input_text.split()),
        "gpu_accelerated": gpu_available
    }
    
    return processed

def check_system_status(payload):
    """Check AI stack system status"""
    return {
        "cuda_available": torch.cuda.is_available(),
        "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        "workspace": payload.get("workspace_context", "unknown")
    }

def main(payload):
    """Main entry point for library-based scripts"""
    try:
        processed_data = process_data(payload)
        system_status = check_system_status(payload)
        
        return {
            "data": processed_data,
            "system": system_status,
            "status": "success"
        }
    except Exception as e:
        return {
            "error": str(e),
            "status": "failed"
        }
```

---
## 6. Local Script Access Integration Points

### Primary Purpose: Host-Container Bridge
- **Script Execution**: Run host Python scripts from within OpenWebUI conversations
- **Development Integration**: Connect local development tools to AI interface
- **Automation Bridge**: Trigger host-based automation from AI conversations
- **System Management**: Access system utilities through conversational interface

### Practical Getting-Started Examples
- **GPU Monitoring**: Check CUDA availability and GPU memory usage
- **System Recovery**: Trigger emergency recovery scripts when issues detected
- **Docker Management**: Check container health and restart services
- **Custom Tools**: Integrate your existing Python utilities and automation

### Security Compliance
- **Read-only Mounts**: Maintains your security-hardened approach
- **Localhost Binding**: Compatible with your `127.0.0.1` port restrictions
- **No New Privileges**: Consistent with container security model

### Monitoring Integration
- **Structured Logging**: Compatible with your existing log formats
- **Health Checks**: Can integrate with your Docker Compose health checks
- **Background Monitoring**: Can work with your `simple-monitor.ps1` system

---
## 7. Best Practices for Local Script Integration

1. **Script Organization**: Structure scripts in logical directories under `/scripts/ai_pipes/`
2. **Error Handling**: Include comprehensive error handling and recovery suggestions
3. **Logging Consistency**: Use structured logging compatible with your monitoring
4. **Security Awareness**: Maintain read-only script access and localhost-only networking
5. **Windows Compatibility**: Ensure scripts work with your PowerShell-based tooling
6. **Resource Management**: Check system resources before intensive operations
7. **Docker Integration**: Scripts can interact with your Docker Compose setup

### Implementation Workflow for Local Script Access

When creating pipe functions for host script access:

1. **Identify Script Purpose**:
   - System monitoring → Use `import` mode with structured data return
   - Command execution → Use `subprocess` mode for CLI tools
   - Data processing → Use `import` mode with payload processing
   - Custom utilities → Choose mode based on script complexity

2. **Choose Execution Mode**:
   - Interactive/complex logic → Use `import` mode with library template
   - Simple CLI tools → Use `subprocess` mode with CLI template

3. **Security Configuration**:
   - Always use read-only mounts for scripts
   - Follow localhost-only networking patterns
   - Include proper error handling and timeouts

4. **Testing Approach**:
   - Test script execution from host environment first
   - Verify volume mount accessibility
   - Validate error handling and logging output
   - Check integration with your existing tooling

This enhanced framework provides robust integration between your local development environment and containerized AI stack. The pipe functions serve as a bridge for accessing host Python scripts through OpenWebUI's conversational interface, enabling seamless integration of custom tools, automation scripts, and system management functions. The GPU monitoring, recovery automation, and system health examples provide practical starting points for common use cases.
```