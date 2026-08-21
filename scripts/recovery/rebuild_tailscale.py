#!/usr/bin/env python3
"""
Rebuild Tailscale - Python equivalent of quick-fixes.bat rebuild

Rebuilds Tailscale container for persistent issues that simple restart can't fix.
"""

import subprocess
import sys
import time
from pathlib import Path

def log_info(message):
    """Log info message"""
    print(f"[INFO] {message}")

def log_success(message):
    """Log success message"""
    print(f"[SUCCESS] {message}")

def log_error(message):
    """Log error message"""
    print(f"[ERROR] {message}")

def log_warn(message):
    """Log warning message"""
    print(f"[WARN] {message}")

def find_project_root():
    """Find the project root directory containing docker-compose.yml"""
    current_dir = Path.cwd()
    
    # Check if we're running from container (look for /host_project)
    if Path("/host_project").exists():
        log_info("Running from container environment...")
        return Path("/host_project")
    
    # Otherwise search for docker-compose.yml
    project_root = current_dir
    while not (project_root / "docker-compose.yml").exists():
        parent = project_root.parent
        if parent == project_root:  # Reached root
            break
        project_root = parent
    
    if not (project_root / "docker-compose.yml").exists():
        log_error("docker-compose.yml not found in current directory or parent directories")
        return None
    
    return project_root

def run_docker_command(command, cwd, timeout=120):
    """Run docker command with error handling"""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result
    except subprocess.TimeoutExpired:
        log_error(f"Command timed out after {timeout} seconds: {' '.join(command)}")
        return None
    except Exception as e:
        log_error(f"Command failed: {e}")
        return None

def test_connectivity():
    """Test network connectivity"""
    log_info("Testing connectivity...")
    project_root = find_project_root()
    if not project_root:
        return False
    
    # Test ping from Tailscale container
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "tailscale", "ping", "-c", "1", "8.8.8.8"],
        project_root,
        timeout=10
    )
    
    if result and result.returncode == 0:
        return True
    return False

def main():
    """Main rebuild Tailscale function"""
    log_info("Rebuilding Tailscale container...")
    
    project_root = find_project_root()
    if not project_root:
        log_error("Could not find project root")
        return 1
    
    log_info(f"Using project root: {project_root}")
    
    # Step 1: Stop Tailscale service
    log_info("Stopping Tailscale service...")
    result = run_docker_command(
        ["docker", "compose", "down", "tailscale"],
        project_root,
        timeout=60
    )
    
    if not result or result.returncode != 0:
        log_warn("Stop command had issues, continuing with rebuild...")
        if result:
            log_warn(f"Warning output: {result.stderr}")
    
    # Step 2: Rebuild Tailscale container
    log_info("Building Tailscale container (no cache)...")
    result = run_docker_command(
        ["docker", "compose", "build", "--no-cache", "tailscale"],
        project_root,
        timeout=300  # 5 minutes for build
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to build Tailscale container")
        if result:
            log_error(f"Build error: {result.stderr}")
        return 1
    
    # Step 3: Start Tailscale service
    log_info("Starting Tailscale service...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d", "tailscale"],
        project_root,
        timeout=120
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to start Tailscale service")
        if result:
            log_error(f"Start error: {result.stderr}")
        return 1
    
    # Wait for rebuild completion
    log_info("Waiting for rebuild completion...")
    time.sleep(45)
    
    # Test connectivity
    if test_connectivity():
        log_success("Tailscale rebuild successful")
        return 0
    else:
        log_error("Tailscale rebuild failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())