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
- **openwebui**: AI chat interface (port 3000→8080)
- **ollama**: LLM server (port 11434)  
- **tailscale**: VPN access via custom build (`dockerfile.tailscale` + `entrypoint.sh`)
- **watchtower**: Auto-updates (excluded from Tailscale to prevent breaks)

### Custom Tailscale Integration
**Files**: `dockerfile.tailscale`, `entrypoint.sh`
- Custom Alpine build with socat
- Network namespace sharing with OpenWebUI
- HTTPS serve configuration: `443 → 127.0.0.1:8080`
- **Never mix `image:` and `build:` directives** - breaks custom entrypoint

### Autonomous Recovery System
**5-tier redundancy** for Tailscale connectivity:
1. Docker health checks (`docker-compose.yml`)
2. Internal monitoring (`entrypoint.sh`)
3. Watchtower coordination (`docker-compose.override.yml`)
4. Windows service (`scripts/check-tailscale-health.ps1`)
5. Simple background monitor (`scripts/simple-monitor.ps1`)

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

### Configuration Management
- **Environment variables**: `.env` file (excluded from git)
- **Secrets**: Never commit auth keys - use `.env.example` template
- **Persistent data**: `./data/` directory for state retention

## Critical Debugging Knowledge

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

### Health Diagnostics
```bash
# Quick system check
docker compose ps && docker compose exec tailscale tailscale status

# Network connectivity test (most important)
docker compose exec tailscale ping -c 1 8.8.8.8

# Serve configuration check
docker compose exec tailscale tailscale serve status
```

### Recovery Operations
```bash
# Standard Tailscale restart
docker compose restart tailscale

# Full namespace recovery
docker compose down tailscale && docker compose up -d tailscale

# Nuclear option (rebuilds custom image)
docker compose build --no-cache tailscale && docker compose up -d tailscale
```

### Windows Monitoring
```powershell
# Start background monitor (no admin required)
.\scripts\simple-monitor.ps1 -Action start

# Install Windows service (requires admin)
.\scripts\install-service.ps1 -Action install
```

## File Organization Patterns

### Configuration Files
- `config/*.conf`: Application-specific settings
- `.env`: Runtime environment (SECRET - never commit)
- `docker-compose.override.yml`: Development/testing overrides

### Scripts Directory
- `*.ps1`: Windows PowerShell utilities
- `*.bat`: Quick batch fixes
- Monitoring and health check automation

### Data Persistence
- `data/tailscale/`: Device identity (critical for VPN)
- `data/ollama/`: AI models (large files)  
- `data/openwebui/`: User data and conversations

## Common Workflow Patterns

### Safe Updates
1. Always backup `data/` directory first
2. Update images: `docker compose pull` (excludes Tailscale)
3. Restart services: `docker compose up -d`
4. Verify Tailscale connectivity after updates

### Troubleshooting Workflow
1. Check service health: `docker compose ps`
2. Test network connectivity from Tailscale container
3. Examine logs for namespace/DERP connection issues
4. Apply graduated recovery (restart → rebuild → reset state)

## Security Considerations

### Secrets Management
- **Tailscale auth keys**: Rotate before expiration (currently Aug 28, 2025)
- **Environment isolation**: All secrets in `.env`, excluded from git
- **Audit trail**: Structured logging for security events

### Container Hardening
- All containers run with `no-new-privileges:true`
- Temporary filesystems use `noexec,nosuid`
- Network access limited to localhost only
- Docker socket access is read-only where possible

When working on this codebase, prioritize understanding the network namespace sharing pattern and autonomous recovery mechanisms - these are the most complex and failure-prone aspects of the system.
