# Hybrid OpenWebUI Update Approach: Autonomous Agent Implementation Guide

## Overview

This document outlines a hybrid approach for enabling automated updates of the custom OpenWebUI GPU-enabled container while maintaining GPU acceleration capabilities. This approach is designed for implementation by autonomous coding agents.

## Current Challenge

**Problem**: Watchtower cannot update custom-built containers (those using `build:` directive instead of `image:` field). The OpenWebUI service uses a custom `Dockerfile.openwebui-gpu` to replace CPU-only PyTorch with CUDA-enabled versions.

**Goal**: Enable automated updates while preserving GPU acceleration and maintaining system stability.

## Hybrid Approach Architecture

### Strategy 1: Registry-Based Custom Images

Transform the build process to use a hybrid registry + build args approach.

#### Implementation Steps:

1. **Create Custom Registry Workflow**
   ```dockerfile
   # Modified Dockerfile.openwebui-gpu
   ARG BASE_VERSION=latest
   FROM ghcr.io/open-webui/open-webui:${BASE_VERSION}
   
   # Remove CPU-only PyTorch and install CUDA version
   RUN pip uninstall -y torch torchvision torchaudio && \
       pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
   ```

2. **Docker Compose Configuration**
   ```yaml
   services:
     openwebui:
       build:
         context: .
         dockerfile: Dockerfile.openwebui-gpu
         args:
           - BASE_VERSION=${OPENWEBUI_VERSION:-latest}
       image: local/openwebui-gpu:${OPENWEBUI_VERSION:-latest}
       # ... rest of configuration
   ```

3. **Automated Build Pipeline**
   - Monitor OpenWebUI releases via GitHub API
   - Trigger rebuild when new version detected
   - Test GPU functionality before deployment
   - Tag and push to local registry

### Strategy 2: Watchtower Custom Command Integration

Extend Watchtower with custom update commands for built containers.

#### Implementation Approach:

1. **Custom Update Script**
   ```bash
   #!/bin/bash
   # scripts/update-openwebui.sh
   
   set -euo pipefail
   
   # Check for new OpenWebUI version
   LATEST_VERSION=$(curl -s https://api.github.com/repos/open-webui/open-webui/releases/latest | jq -r .tag_name)
   CURRENT_VERSION=$(docker inspect openwebui | jq -r '.[0].Config.Labels["org.opencontainers.image.version"]')
   
   if [ "$LATEST_VERSION" != "$CURRENT_VERSION" ]; then
       echo "Updating OpenWebUI from $CURRENT_VERSION to $LATEST_VERSION"
       
       # Backup data
       cp -r ./data ./data-backup-$(date +%Y%m%d-%H%M%S)
       
       # Update Dockerfile base image
       sed -i "s|FROM ghcr.io/open-webui/open-webui:.*|FROM ghcr.io/open-webui/open-webui:$LATEST_VERSION|" Dockerfile.openwebui-gpu
       
       # Rebuild and test
       docker compose build --no-cache openwebui
       docker compose up -d openwebui
       
       # Validate GPU functionality
       docker compose exec openwebui python -c "import torch; assert torch.cuda.is_available(), 'GPU not available'"
       
       echo "OpenWebUI updated successfully to $LATEST_VERSION"
   fi
   ```

2. **Cron Integration**
   ```bash
   # Add to system cron or Windows Task Scheduler
   0 2 * * * /path/to/scripts/update-openwebui.sh >> /var/log/openwebui-updates.log 2>&1
   ```

### Strategy 3: GitHub Actions + Webhook Integration

Leverage GitHub Actions to build and deploy custom images automatically.

#### Implementation Components:

1. **GitHub Actions Workflow**
   ```yaml
   # .github/workflows/update-openwebui.yml
   name: Update OpenWebUI GPU Image
   
   on:
     schedule:
       - cron: '0 2 * * *'  # Daily at 2 AM
     workflow_dispatch:
   
   jobs:
     update:
       runs-on: ubuntu-latest
       steps:
         - name: Check for OpenWebUI updates
           # Implementation details for version comparison
         
         - name: Build custom GPU image
           # Build with CUDA PyTorch replacement
         
         - name: Test GPU compatibility
           # Validation steps
         
         - name: Trigger deployment webhook
           # Notify local system of new image
   ```

2. **Local Webhook Receiver**
   ```python
   # scripts/webhook-receiver.py
   from flask import Flask, request
   import subprocess
   
   app = Flask(__name__)
   
   @app.route('/update-openwebui', methods=['POST'])
   def update_openwebui():
       # Verify webhook signature
       # Pull new image
       # Update docker-compose.yml
       # Restart containers
       # Validate functionality
   ```

## Implementation Priority

### Phase 1: Monitoring and Notification (Immediate)
- Implement version checking script
- Add alerting for available updates
- Create manual update documentation

### Phase 2: Semi-Automated Updates (Short-term)
- Develop update script with safety checks
- Implement backup and rollback mechanisms
- Add GPU validation testing

### Phase 3: Full Automation (Long-term)
- Choose and implement one of the three strategies above
- Add comprehensive error handling and recovery
- Integrate with existing monitoring systems

## Risk Mitigation

### Critical Safeguards Required:

1. **Data Backup**: Always backup `./data` before updates
2. **GPU Testing**: Validate CUDA availability after every update
3. **Rollback Capability**: Maintain previous working version
4. **Health Checks**: Verify all services post-update
5. **Network Recovery**: Ensure Tailscale reconnects properly

### Testing Protocol:

```bash
# Post-update validation script
#!/bin/bash

# 1. Container health
docker compose ps | grep -q "healthy.*openwebui" || exit 1

# 2. GPU availability
docker compose exec openwebui python -c "import torch; assert torch.cuda.is_available()" || exit 1

# 3. API functionality
curl -f http://localhost:3000/health || exit 1

# 4. Tailscale connectivity
docker compose exec tailscale ping -c 1 8.8.8.8 || exit 1

echo "All validation checks passed"
```

## Security Considerations

- **Authentication**: Secure webhook endpoints with proper auth
- **Image Verification**: Validate image signatures and checksums
- **Network Isolation**: Limit update mechanism network access
- **Audit Logging**: Log all update activities for security review

## Monitoring and Alerting

- **Update Success/Failure Notifications**
- **GPU Performance Monitoring**
- **Service Health Dashboards**
- **Rollback Alert System**

## File Structure for Implementation

```
ai-stack/
├── scripts/
│   ├── update-openwebui.sh           # Manual/cron update script
│   ├── validate-gpu-setup.sh         # Post-update validation
│   ├── webhook-receiver.py           # GitHub Actions integration
│   └── rollback-openwebui.sh         # Emergency rollback
├── .github/workflows/
│   └── update-openwebui.yml          # GitHub Actions workflow
├── config/
│   └── update-config.json            # Update configuration
└── documentation/
    └── update-procedures.md           # Detailed procedures
```

## Next Steps for Implementation

1. **Assessment**: Evaluate current infrastructure and choose strategy
2. **Prototyping**: Implement basic version checking and notification
3. **Testing**: Develop and test update procedures in safe environment
4. **Integration**: Connect with existing monitoring and recovery systems
5. **Documentation**: Create operational procedures for maintenance team
6. **Monitoring**: Implement comprehensive logging and alerting

This hybrid approach maintains the benefits of custom GPU integration while enabling automated updates, providing the best of both manual control and autonomous operation.