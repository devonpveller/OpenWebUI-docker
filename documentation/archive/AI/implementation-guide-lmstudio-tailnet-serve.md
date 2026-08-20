
# Tailscale Serve Admin Tool for OpenWebUI

## Overview

This document outlines the implementation of a Tailscale-based service management tool that allows users to expose local services (such as LM Studio) securely to their tailnet via HTTPS. The tool integrates with OpenWebUI's pipe system to provide a natural language interface for managing these services.

## Tool Specification

### Tool Name: `tailscale_serve_admin`

#### Supported Actions:
- `serve_start`: Start serving a service at a specified path
- `serve_stop`: Stop serving a service at a specified path
- `status`: Get current status of all served paths
- `health`: Check health of a specific service

#### Input Parameters
All parameters are optional unless otherwise noted. When using `serve_start`, `target_port` is required.

| Parameter | Type | Required | Description |
|----------|------|----------|-------------|
| `action` | string | Yes | Action to perform (`serve_start`, `serve_stop`, `status`, `health`) |
| `path` | string | Yes (for serve actions) | Path to serve at (e.g., `lmstudio`) |
| `target_host` | string | No | Host of the service (default: `127.0.0.1`) |
| `target_port` | integer | Yes (for `serve_start`) | Port of the service |
| `proxy_port` | integer | No | Port to proxy through if needed |
| `ts_hostname` | string | No | Override Tailscale hostname |
| `require_userspace_tun` | boolean | No | Use userspace TUN interface (default: `true`) |
| `health_path` | string | No | Path to check for health (default: `/api/status`) |

#### Outputs:
```json
{
  "success": true,
  "summary": "Successfully started serving at /lmstudio",
  "details": {
    "serve_url": "https://owui-node.tailnet.dev/lmstudio",
    "resolved_hostname": "owui-node.tailnet.dev",
    "path_map": {
      "/lmstudio": "http://127.0.0.1:5506"
    },
    "health_status": "healthy"
  }
}
```

#### Failure Codes:
- `TAILSCALE_NOT_READY`
- `AUTH_REQUIRED`
- `TARGET_UNREACHABLE`
- `SERVE_CONFLICT`
- `INSUFFICIENT_PRIVILEGES`
- `UNKNOWN_ERROR`

## Implementation Plan

### 1. Tailscale Lifecycle Management

#### Initialize Tailscale
```bash
# Start tailscaled in userspace mode
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state &

# Authenticate with non-interactive auth
TS_AUTHKEY="${TS_AUTHKEY:?missing}" \
tailscale up \
  --authkey="${TS_AUTHKEY}" \
  --hostname="${TS_HOSTNAME:-owui-node}" \
  --reset
```

#### Verify Status
```bash
# Check if tailscale is ready
if ! tailscale status >/dev/null 2>&1; then
  echo "TAILSCALE_NOT_READY"
  exit 1
fi
```

### 2. Serve Configuration Management

#### Start Serving
```bash
# Create serve configuration
tailscale serve --https=/<path> http://<target_host>:<target_port>
```

#### Stop Serving
```bash
# Remove specific path
tailscale serve --remove=/<path>
```

#### Get Status
```bash
# List all served paths
tailscale serve status
```

### 3. Health Checking

#### Probe Target Service
```bash
# Health check with retries
curl -f -s -o /dev/null "http://<target_host>:<target_port>/<health_path>" || {
  echo "TARGET_UNREACHABLE"
  exit 1
}
```

### 4. State Management

#### Track Current Configuration
```bash
# Save current state to file
cat > /var/lib/tailscale/serve-state.json << EOF
{
  "paths": {
    "/lmstudio": "http://127.0.0.1:5506"
  },
  "last_health": {
    "/lmstudio": "healthy"
  }
}
EOF
```

### 5. Integration with OpenWebUI

#### Define Pipe Tool
```json
{
  "name": "tailscale_serve_admin",
  "description": "Manage Tailscale services for local applications",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["serve_start", "serve_stop", "status", "health"]},
      "path": {"type": "string"},
      "target_host": {"type": "string"},
      "target_port": {"type": "integer"},
      "proxy_port": {"type": "integer"},
      "ts_hostname": {"type": "string"},
      "require_userspace_tun": {"type": "boolean"},
      "health_path": {"type": "string"}
    },
    "required": ["action"]
  }
}
```

### 6. Error Handling

#### Common Errors and Remediation
- `TAILSCALE_NOT_READY`: Restart tailscaled process
- `AUTH_REQUIRED`: Prompt for new auth key
- `TARGET_UNREACHABLE`: Check service is running and port is correct
- `SERVE_CONFLICT`: Offer force option to rebind path
- `INSUFFICIENT_PRIVILEGES`: Inform user about required capabilities

### 7. Operational Defaults

| Setting | Default Value |
|--------|---------------|
| Tailscale mode | Userspace TUN |
| LM Studio port | 5506 |
| Health path | `/api/status` |
| Serve path | `lmstudio` |
| Retries | 30 attempts × 2s |

### 8. Usage Examples

#### User Prompts:
- "Start serving LM Studio at `/lmstudio`, port **5506**."
- "Stop serving `/lmstudio`."
- "Show status of all Tailscale routes."
- "Check health of the LM Studio endpoint."

#### Command Execution:
```bash
# Start serving
tailscale_serve_admin --action serve_start --path lmstudio --target_port 5506

# Stop serving
tailscale_serve_admin --action serve_stop --path lmstudio

# Check status
tailscale_serve_admin --action status

# Check health
tailscale_serve_admin --action health --path lmstudio
```
