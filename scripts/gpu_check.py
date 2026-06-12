#!/usr/bin/env python3
"""
GPU Check - Python equivalent of quick-fixes.bat gpu

Checks GPU status and restarts GPU services if needed.
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

def run_docker_command(command, cwd, timeout=60):
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

def test_gpu_availability():
    """Test GPU availability in OpenWebUI container"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    log_info("Testing OpenWebUI GPU access...")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "openwebui", "python", "-c", 
         "import torch; print('CUDA available:', torch.cuda.is_available())"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        log_success("GPU access test completed")
        print(result.stdout.strip())
        return "cuda available: true" in result.stdout.lower()
    else:
        log_error("GPU access test failed")
        if result:
            log_error(f"Error: {result.stderr}")
        return False

def test_ollama_availability():
    """Test Ollama availability and GPU integration"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    log_info("Testing Ollama availability...")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "ollama", "ollama", "list"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        log_success("Ollama GPU integration working")
        return True
    else:
        log_warn("Ollama may need additional time to initialize")
        return False

def test_llama_cpp_availability():
    """Test llama-cpp availability and GPU integration"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    log_info("Testing llama-cpp availability...")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "llama-cpp-upstream", "curl", "-sf", "http://127.0.0.1:8080/health"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp GPU service: OK")
        print(result.stdout.strip())
        return True
    else:
        log_warn("llama-cpp may need additional time to initialize")
        return False

def test_llama_cpp_embed_availability():
    """Test llama-cpp-embed availability and GPU integration"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    log_info("Testing llama-cpp-embed availability...")
    result = run_docker_command(
        ["docker", "compose", "exec", "-T", "llama-cpp-embed-upstream", "curl", "-sf", "http://127.0.0.1:8080/health"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp-embed GPU service: OK")
        print(result.stdout.strip())
        return True
    else:
        log_warn("llama-cpp-embed may need additional time to initialize")
        return False

def restart_gpu_services():
    """Restart GPU services (OpenWebUI and Ollama)"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    log_warn("GPU check failed, restarting GPU services...")
    
    # Restart OpenWebUI, Ollama, llama-cpp, and llama-cpp-embed
    result = run_docker_command(
        ["docker", "compose", "restart", "ollama", "openwebui", "llama-cpp-upstream", "llama-cpp-embed-upstream"],
        project_root,
        timeout=120
    )
    
    if not result or result.returncode != 0:
        log_error("Failed to restart GPU services")
        if result:
            log_error(f"Error: {result.stderr}")
        return False
    
    log_info("Waiting for GPU services to restart...")
    time.sleep(60)
    
    return True

def main():
    """Main GPU check function"""
    log_info("Checking GPU status and restarting GPU services if needed...")
    
    project_root = find_project_root()
    if not project_root:
        log_error("Could not find project root")
        return 1
    
    log_info(f"Using project root: {project_root}")
    
    # Test initial GPU availability
    gpu_working = test_gpu_availability()
    
    if not gpu_working:
        # GPU not working, try restarting services
        if not restart_gpu_services():
            log_error("Failed to restart GPU services")
            return 1
        
        # Re-test GPU access after restart
        log_info("Re-testing GPU access...")
        gpu_working = test_gpu_availability()
        
        if gpu_working:
            log_success("GPU restored after restart")
            
            # Test Ollama integration
            if test_ollama_availability():
                log_success("Ollama GPU integration working")
            else:
                log_warn("Ollama may need additional time to initialize")
            
            # Test llama-cpp services
            if test_llama_cpp_availability():
                log_success("llama-cpp GPU service working")
            else:
                log_warn("llama-cpp may need additional time to initialize")
            
            if test_llama_cpp_embed_availability():
                log_success("llama-cpp-embed GPU service working")
            else:
                log_warn("llama-cpp-embed may need additional time to initialize")
        else:
            log_error("GPU still not available - may need full rebuild")
            log_info("Try: nuclear option for complete restart")
            return 1
    else:
        log_success("OpenWebUI GPU is working correctly")
        
        # Still test Ollama integration
        if test_ollama_availability():
            log_success("Ollama GPU integration working")
        else:
            log_warn("Ollama may need restart")
            # Try restarting just Ollama
            result = run_docker_command(
                ["docker", "compose", "restart", "ollama"],
                project_root,
                timeout=60
            )
            if result and result.returncode == 0:
                time.sleep(30)
                log_info("Ollama restarted, testing again...")
                test_ollama_availability()
        
        # Test llama-cpp services
        if test_llama_cpp_availability():
            log_success("llama-cpp GPU service working")
        else:
            log_warn("llama-cpp may need restart")
            result = run_docker_command(
                ["docker", "compose", "restart", "llama-cpp-upstream"],
                project_root,
                timeout=60
            )
            if result and result.returncode == 0:
                time.sleep(30)
                log_info("llama-cpp restarted, testing again...")
                test_llama_cpp_availability()
        
        if test_llama_cpp_embed_availability():
            log_success("llama-cpp-embed GPU service working")
        else:
            log_warn("llama-cpp-embed may need restart")
            result = run_docker_command(
                ["docker", "compose", "restart", "llama-cpp-embed-upstream"],
                project_root,
                timeout=60
            )
            if result and result.returncode == 0:
                time.sleep(30)
                log_info("llama-cpp-embed restarted, testing again...")
                test_llama_cpp_embed_availability()
    
    log_success("GPU check completed")
    return 0

if __name__ == "__main__":
    sys.exit(main())