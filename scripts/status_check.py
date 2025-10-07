#!/usr/bin/env python3
"""
Status Check - Python equivalent of quick-fixes.bat status

Comprehensive system status check with detailed diagnostics.
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

def run_docker_command(command, cwd, timeout=30):
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

def check_container_status():
    """Check container status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Container Status:")
    result = run_docker_command(
        ["docker", "compose", "ps"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
        return True
    else:
        log_error("Failed to get container status")
        return False

def start_missing_services():
    """Start any missing services"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Starting any missing services...")
    result = run_docker_command(
        ["docker", "compose", "up", "-d", "watchtower"],
        project_root,
        timeout=60
    )
    
    return result is not None

def check_gpu_status():
    """Check OpenWebUI GPU status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("OpenWebUI GPU Status:")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "openwebui", "python", "-c", 
         "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
        return True
    else:
        log_error("GPU status check failed")
        return False

def check_ollama_status():
    """Check Ollama status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Ollama Status:")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "ollama", "ollama", "list"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
        return True
    else:
        log_error("Ollama status check failed")
        return False

def check_network_connectivity():
    """Check network connectivity"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Network Connectivity:")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "tailscale", "ping", "-c", "1", "8.8.8.8"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("Network connectivity: OK")
        return True
    else:
        log_error("Network connectivity: FAILED")
        return False

def check_tailscale_status():
    """Check Tailscale status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Tailscale Status:")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "tailscale", "tailscale", "--socket=/tmp/tailscaled.sock", "status"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
    else:
        log_warn("Tailscale status check failed")
    
    return result is not None

def check_tailscale_serve():
    """Check Tailscale serve status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Tailscale Serve Status:")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "tailscale", "tailscale", "--socket=/tmp/tailscaled.sock", "serve", "status"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
    else:
        log_warn("Tailscale serve status check failed")
    
    return result is not None

def check_service_accessibility():
    """Check service accessibility"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Service Accessibility Check:")
    
    # Check OpenWebUI accessibility
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "tailscale", "wget", "-q", "-T", "3", "-O", "/dev/null", "http://127.0.0.1:8080"],
        project_root,
        timeout=10
    )
    
    if result and result.returncode == 0:
        log_success("OpenWebUI accessibility: OK")
    else:
        log_error("OpenWebUI accessibility: FAILED")
    
    # Check Ollama API accessibility
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "tailscale", "wget", "-q", "-T", "3", "-O", "/dev/null", "http://127.0.0.1:11434/api/version"],
        project_root,
        timeout=10
    )
    
    if result and result.returncode == 0:
        log_success("Ollama API accessibility: OK")
    else:
        log_error("Ollama API accessibility: FAILED")
    
    return True

def main():
    """Main status check function"""
    log_info("==========================================")
    log_info("SYSTEM STATUS CHECK")
    log_info("==========================================")
    
    project_root = find_project_root()
    if not project_root:
        log_error("Could not find project root")
        return 1
    
    log_info(f"Using project root: {project_root}")
    
    # Run all status checks
    checks = [
        check_container_status,
        start_missing_services,
        check_gpu_status,
        check_ollama_status,
        check_network_connectivity,
        check_tailscale_status,
        check_tailscale_serve,
        check_service_accessibility
    ]
    
    success_count = 0
    for check in checks:
        try:
            if check():
                success_count += 1
        except Exception as e:
            log_error(f"Check failed: {e}")
    
    print()
    log_info(f"Status check completed: {success_count}/{len(checks)} checks passed")
    
    if success_count == len(checks):
        log_success("All systems operational")
        return 0
    else:
        log_warn("Some systems need attention")
        return 1

if __name__ == "__main__":
    sys.exit(main())