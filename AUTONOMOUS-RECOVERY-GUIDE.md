# Quick Start Guide for Autonomous Tailscale Recovery

## 🔒 **Security Hardening Applied**

The codebase has been enhanced with enterprise-level security:

✅ **Container Security**
- no-new-privileges enabled
- tmpfs with noexec,nosuid  
- Read-only configs
- Services bound to localhost only

✅ **Network Security**
- Docker socket read-only access
- Limited Watchtower scope
- Tailscale network isolation

✅ **Secret Management**
- Auth key sanitized in .env
- Credentials excluded from git
- Structured logging for audit trails

⚠️ **Action Required**: Update TAILSCALE_AUTH_KEY in .env before November 27, 2025

---

## Solution Hierarchy (Redundant Protection)

### 🔄 **Level 1: Docker Native (Primary)**
- **File:** `docker-compose.yml` 
- **What:** Health checks with automatic restart policies
- **Triggers:** Container failures, health check failures
- **Recovery Time:** 30-60 seconds

### 🛡️ **Level 2: Enhanced Container (Secondary)** 
- **File:** `entrypoint.sh`
- **What:** Internal monitoring loop with network checks
- **Triggers:** Network connectivity loss, Tailscale daemon issues
- **Recovery Time:** 15-30 seconds

### 🔄 **Level 3: Watchtower Coordination (Update Protection)**
- **File:** `docker-compose.override.yml`
- **What:** Coordinates container updates and dependencies
- **Triggers:** Watchtower container updates
- **Recovery Time:** During update process

### 🐧 **Level 4: External Daemon (Linux/WSL)**
- **File:** `scripts/tailscale-service-manager.sh`
- **What:** Independent monitoring process
- **Usage:** `chmod +x scripts/tailscale-service-manager.sh && scripts/tailscale-service-manager.sh start`

### 🪟 **Level 5: Windows Service (Windows Native)**
- **Files:** `scripts/check-tailscale-health.ps1` + `scripts/install-service.ps1`
- **What:** Windows Service with comprehensive monitoring
- **Usage:** See Windows Setup below

---

## Quick Setup Instructions

### For Windows Users (Two Options):

#### **Option A: Windows Service (Requires Admin)**
1. **Right-click PowerShell** and select **"Run as Administrator"**
2. **Install as Windows Service:**
```powershell
cd "d:\Open WebUI\ai-stack\scripts"
.\install-service.ps1 -Action install
```

#### **Option B: Simple Background Monitor (No Admin Required)**
1. **Start Background Monitor:**
```powershell
cd "d:\Open WebUI\ai-stack\scripts"
.\simple-monitor.ps1 -Action start -IntervalSeconds 30
```

2. **Check Monitor Status:**
```powershell
.\simple-monitor.ps1 -Action status
```

3. **Stop Monitor:**
```powershell
.\simple-monitor.ps1 -Action stop
```

### For Linux/WSL Users:

1. **Start External Monitor:**
```bash
chmod +x scripts/tailscale-service-manager.sh
scripts/tailscale-service-manager.sh start
```

2. **Check Monitor Status:**
```bash
scripts/tailscale-service-manager.sh status
```

---

## Testing Your Setup

### Test Scenario 1: Container Restart
```powershell
# Force container recreation
docker compose down tailscale
docker compose up -d tailscale

# Watch recovery (should auto-recover in 30-60 seconds)
docker compose logs -f tailscale
```

### Test Scenario 2: Network Disruption
```powershell
# Simulate network issue
docker compose exec tailscale ip route del default

# Monitor recovery in logs
docker compose logs -f tailscale
```

### Test Scenario 3: Watchtower Update
```powershell
# Update containers (simulates Watchtower)
docker compose pull
docker compose up -d

# Verify Tailscale dependency handling
docker compose ps
```

---

## Monitoring & Logs

### View Real-time Logs:
```powershell
# Container logs
docker compose logs -f tailscale

# Windows Service logs  
Get-Content "logs\tailscale-health.log" -Wait

# Linux daemon logs
tail -f logs/tailscale-service.log
```

### Check Current Status:
```powershell
# Quick connectivity test
docker compose exec tailscale ping -c 1 8.8.8.8

# Tailscale status
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status

# Serve configuration
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status
```

---

## Service Management Commands

### Windows Service:
```powershell
# Install service
.\install-service.ps1 -Action install -IntervalSeconds 30

# Check status and recent logs
.\install-service.ps1 -Action status

# Restart service
.\install-service.ps1 -Action restart

# Remove service
.\install-service.ps1 -Action uninstall
```

### Linux/WSL Daemon:
```bash
# Start daemon
scripts/tailscale-service-manager.sh start

# Check status
scripts/tailscale-service-manager.sh status

# Stop daemon
scripts/tailscale-service-manager.sh stop

# Restart daemon
scripts/tailscale-service-manager.sh restart
```

---

## Recovery Capabilities

✅ **Automatic Recovery From:**
- Watchtower container updates
- Docker daemon restarts
- Container crashes or exits
- Network connectivity loss  
- Tailscale daemon failures
- Serve configuration loss
- OpenWebUI dependency issues

✅ **Self-Healing Features:**
- Network namespace re-attachment
- Tailscale serve reconfiguration
- Container dependency management
- Health check validation
- Retry logic with backoff
- Comprehensive logging

✅ **Zero Downtime Goals:**
- Proactive monitoring
- Fast recovery (15-60 seconds)
- Multiple redundant systems
- No manual intervention required

---

## Troubleshooting

### If Service Won't Start:
```powershell
# Check Docker Compose is working
docker compose ps

# Verify scripts exist
ls scripts/

# Check permissions (Windows)
Get-Acl scripts\check-tailscale-health.ps1

# Run manual test
scripts\check-tailscale-health.ps1 -Mode check
```

### If Recovery Isn't Working:
```powershell
# Check all solutions are active
docker compose ps                    # Level 1: Health checks
docker compose logs tailscale       # Level 2: Entrypoint monitoring  
Get-Service TailscaleHealthMonitor   # Level 5: Windows service

# Force test recovery
docker compose restart tailscale
```

---

Your Tailscale setup is now **fully autonomous** and will automatically recover from any restart scenario! 🚀
