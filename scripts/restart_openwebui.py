#!/usr/bin/env python3
"""
Restart OpenWebUI - Python equivalent of quick-fixes.bat restart-openwebui

Properly restart OpenWebUI with dependent containers in correct order.
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

def wait_for_openwebui_healthy():
    """Wait for OpenWebUI to be healthy"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    log_info("Waiting for OpenWebUI to be healthy...")
    max_attempts = 30  # 5 minutes total
    attempt = 0
    
    while attempt < max_attempts:
        result = run_docker_command(
            ["docker", "compose", "ps", "openwebui"],
            project_root,
            timeout=10
        )
        
        if result and result.returncode == 0:
            if "healthy" in result.stdout:
                log_success("OpenWebUI is healthy")
                return True
        
        log_info("OpenWebUI not yet healthy, waiting 10 more seconds...")
        time.sleep(10)
        attempt += 1
    
    log_error("OpenWebUI failed to become healthy within timeout")
    return False

def main():
    """Main restart OpenWebUI function"""
    log_info("Restarting OpenWebUI with proper network dependency handling...")
    log_warn("This will restart OpenWebUI, llama-cpp, llama-cpp-embed, Ollama, and Tailscale containers")
    
    project_root = find_project_root()
    if not project_root:
        log_error("Could not find project root")
        return 1
    
    log_info(f"Using project root: {project_root}")
    
    # Step 1: Stop dependent containers first
    log_info("Stopping dependent containers first...")
    result = run_docker_command(
        ["docker", "compose", "stop", "tailscale", "ollama", "llama-cpp-upstream", "llama-cpp-embed-upstream"],
        project_root,
        timeout=60
    )
    
    if not result or result.returncode != 0:
        log_warn("Stop command had issues, continuing...")
        if result:
            log_warn(f"Warning: {result.stderr}")
    
    # Step 2: Restart OpenWebUI
    log_info("Restarting OpenWebUI...")
    result = run_docker_command(
        ["docker", "compose", "restart", "openwebui"],
        project_root,
        timeout=120
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to restart OpenWebUI")
        if result:
            log_error(f"Error: {result.stderr}")
        return 1
    
    # Step 3: Wait for OpenWebUI to be healthy
    if not wait_for_openwebui_healthy():
        log_error("OpenWebUI did not become healthy")
        return 1
    
    # Step 4: Start Ollama
    log_info("Starting Ollama...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d", "ollama"],
        project_root,
        timeout=60
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to start Ollama")
        if result:
            log_error(f"Error: {result.stderr}")
        return 1
    
    # Wait for Ollama to start
    time.sleep(15)
    
    # Step 5: Start llama-cpp services
    log_info("Starting llama-cpp...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d", "llama-cpp-upstream"],
        project_root,
        timeout=60
    )
    
    if not result or result.returncode != 0:
        log_warn("Failed to start llama-cpp")
    
    log_info("Starting llama-cpp-embed...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d", "llama-cpp-embed-upstream"],
        project_root,
        timeout=60
    )
    
    if not result or result.returncode != 0:
        log_warn("Failed to start llama-cpp-embed")
    
    # Wait for llama-cpp services to initialize
    log_info("Waiting for llama-cpp services to initialize...")
    time.sleep(30)
    
    # Step 6: Start Tailscale
    log_info("Starting Tailscale...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d", "tailscale"],
        project_root,
        timeout=60
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to start Tailscale")
        if result:
            log_error(f"Error: {result.stderr}")
        return 1
    
    # Wait for Tailscale to start
    time.sleep(30)
    
    log_success("OpenWebUI restart sequence complete")
    return 0

if __name__ == "__main__":
    sys.exit(main())