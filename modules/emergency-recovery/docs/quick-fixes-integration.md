# Quick Fixes Integration - Emergency Recovery Module

## Overview

The emergency-recovery module now includes all functionality from `scripts/quick-fixes.bat`, providing intelligent system recovery through the AI Stack pipe function system. This allows users to perform emergency recovery operations through natural language in OpenWebUI conversations.

## Available Quick Fix Actions

### 1. Namespace Reset (`namespace`)
**Most common fix for connectivity issues**

- **Trigger keywords**: "namespace reset", "fix network", "connectivity", "tailscale down"
- **What it does**: Restarts Tailscale container to rejoin OpenWebUI's network namespace
- **Use case**: When OpenWebUI can't reach Ollama or Tailscale connectivity fails
- **Execution time**: ~40 seconds (restart + 30s wait + connectivity test)

```json
{
  "input": "fix network"
}
```

### 2. Rebuild Tailscale (`rebuild`)
**For persistent Tailscale issues**

- **Trigger keywords**: "rebuild tailscale", "rebuild container"
- **What it does**: Completely rebuilds Tailscale container from scratch
- **Use case**: When namespace reset doesn't work or container corruption suspected
- **Execution time**: ~2-3 minutes (stop + rebuild + start + test)

```json
{
  "input": "rebuild tailscale"
}
```

### 3. GPU Check (`gpu`)
**GPU availability check and restart**

- **Trigger keywords**: "gpu check", "cuda", "graphics", "reranker"
- **What it does**: Tests GPU availability and restarts GPU services if needed
- **Use case**: When CUDA is not available or reranker models are slow
- **Execution time**: ~1-2 minutes (depending on whether restart is needed)

```json
{
  "input": "check gpu"
}
```

### 4. Status Check (`status`)
**Comprehensive system overview**

- **Trigger keywords**: "emergency status", "system check" (with emergency context)
- **What it does**: Checks all services, GPU, network, and accessibility
- **Use case**: Getting detailed system health before other recovery actions
- **Execution time**: ~5-10 seconds

```json
{
  "input": "emergency status check"
}
```

### 5. LM Studio Fix (`lmstudio`)
**Fix LM Studio Tailscale connectivity**

- **Trigger keywords**: "lmstudio", "lm studio", "fix lm studio"
- **What it does**: Restarts socat proxy and configures Tailscale serve for LM Studio
- **Use case**: When LM Studio is not accessible through Tailscale VPN
- **Execution time**: ~20-30 seconds

```json
{
  "input": "fix lmstudio"
}
```

### 6. Restart OpenWebUI (`restart_openwebui`)
**Properly restart OpenWebUI with dependent containers**

- **Trigger keywords**: "restart openwebui", "openwebui restart"
- **What it does**: Restarts OpenWebUI with proper dependency handling
- **Use case**: When OpenWebUI needs restart but must maintain network namespace
- **Execution time**: ~3-4 minutes (dependency shutdown + restart + health wait)

```json
{
  "input": "restart openwebui"
}
```

### 7. Nuclear Option (`nuclear`)
**Complete system restart (last resort)**

- **Trigger keywords**: "nuclear", "complete restart", "everything broken"
- **What it does**: Full stack shutdown and restart with safety checks
- **Use case**: When all other recovery options have failed
- **Execution time**: ~2-3 minutes (with smart abort if connectivity works)

```json
{
  "input": "nuclear option"
}
```

## Integration Points

### Through AI Stack Router

The emergency recovery actions are accessible through natural language queries in OpenWebUI:

```bash
# Examples that route to emergency-recovery module:
python core/router.py '{"input": "fix network"}'
python core/router.py '{"input": "emergency status check"}'
python core/router.py '{"input": "restart ollama"}'
python core/router.py '{"input": "gpu check"}'
```

### Direct Module Access

For testing and development:

```bash
# Direct module execution
python modules/emergency-recovery/service/emergency_recovery.py "namespace reset"
python modules/emergency-recovery/service/emergency_recovery.py "gpu check"
python modules/emergency-recovery/service/emergency_recovery.py "system status"
```

## Response Format

All quick-fix actions return structured responses following the module schema:

```json
{
  "request_id": "uuid",
  "module_id": "emergency-recovery",
  "status": "ok|error",
  "content": "Markdown formatted response",
  "structured_data": {
    "action": "action_name",
    "status": "completed|error|aborted",
    "steps_completed": ["step1", "step2"],
    "connectivity_test": {"status": "success|failed"},
    "next_steps": ["recommendation1", "recommendation2"]
  },
  "diagnostics": {
    "execution_time_ms": 12345
  },
  "timestamp": "ISO-8601"
}
```

## Safety Features

### Smart Abort (Nuclear Option)
- Pre-flight connectivity check
- Aborts if connectivity is actually working
- Suggests less disruptive alternatives

### Graceful Error Handling
- Timeout protection for all Docker operations
- Detailed error reporting with recommendations
- Fallback suggestions when operations fail

### Project Root Detection
- Automatically finds docker-compose.yml location
- Works from any subdirectory in the project
- Clear error messages if project structure not found

## Container Environment Compatibility

The implementation is designed to work both in:

1. **Host environment** (development/testing)
2. **Container environment** (OpenWebUI pipe functions)

Path resolution and Docker command execution adapt automatically based on the environment.

## Monitoring and Observability

All actions include:
- **Execution timing** - Performance monitoring
- **Step tracking** - Detailed progress reporting
- **Success indicators** - Clear success/failure criteria
- **Next steps** - Actionable recommendations
- **Error context** - Helpful debugging information

## Migration from quick-fixes.bat

The pipe function implementation provides several advantages over the original batch script:

1. **Structured output** - JSON responses vs. console text
2. **Error handling** - Comprehensive exception management
3. **Integration** - Natural language access through OpenWebUI
4. **Observability** - Detailed execution metrics
5. **Safety** - Smart abort mechanisms and validation
6. **Consistency** - Unified response format across all actions

## Testing Scenarios

### Network Issues
```bash
# Most common - network namespace sharing broken
python core/router.py '{"input": "fix network"}'
```

### GPU Problems
```bash
# CUDA not available or reranker models slow
python core/router.py '{"input": "gpu not working"}'
```

### System Health
```bash
# Comprehensive health check
python core/router.py '{"input": "emergency status"}'
```

### Last Resort
```bash
# When everything else fails
python core/router.py '{"input": "nuclear option"}'
```

This integration provides a seamless bridge between the original quick-fixes.bat functionality and the modern AI Stack pipe function architecture, while adding significant improvements in safety, observability, and user experience.