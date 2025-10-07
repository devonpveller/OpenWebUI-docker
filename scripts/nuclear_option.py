#!/usr/bin/env python3
"""
Nuclear Option - Python equivalent of quick-fixes.bat nuclear

Complete system restart with proper container ordering.
"""

import os
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

def pre_nuclear_diagnostic():
    """Run pre-nuclear diagnostic check"""
    log_info("Pre-nuclear diagnostic check...")
    
    if test_connectivity():
        log_success("Wait - connectivity is actually working!")
        log_info("Issue may be performance/timing related, not connectivity")
        log_info("Try: status check to see detailed status")
        log_info("Or just wait a bit longer for services to stabilize")
        return True
    
    return False

def main():
    """Main nuclear option function"""
    log_warn("==========================================")
    log_warn("NUCLEAR OPTION - FULL STACK RESTART")
    log_warn("==========================================")
    
    project_root = find_project_root()
    if not project_root:
        log_error("Could not find project root")
        return 1
    
    log_info(f"Using project root: {project_root}")
    
    # Pre-nuclear diagnostic
    if pre_nuclear_diagnostic():
        return 0  # Everything is actually working
    
    log_warn("This will restart ALL containers and may take several minutes")
    log_warn("This will DESTROY containers and rebuild them")
    
    # Note: In container environment, we can't pause for user input
    # So we'll proceed automatically
    log_info("Proceeding with nuclear option...")
    
    # Step 1: Stop all services
    log_info("Performing full stack restart...")
    result = run_docker_command(
        ["docker", "compose", "down"],
        project_root,
        timeout=120
    )
    
    if not result or result.returncode != 0:
        log_warn("Stop command had issues, continuing...")
        if result:
            log_warn(f"Warning: {result.stderr}")
    
    # Wait for clean shutdown
    log_info("Waiting for clean shutdown...")
    time.sleep(15)
    
    # Step 2: Start all services
    log_info("Starting all services...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d"],
        project_root,
        timeout=300  # 5 minutes for startup
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to start services")
        if result:
            log_error(f"Error: {result.stderr}")
        return 1
    
    # Wait for complete stack initialization
    log_info("Waiting for complete stack initialization...")
    time.sleep(90)
    
    # Test final connectivity
    log_info("Testing final connectivity...")
    if test_connectivity():
        log_success("Nuclear option successful")
        return 0
    else:
        log_error("Nuclear option failed - manual intervention required")
        return 1

if __name__ == "__main__":
    sys.exit(main())