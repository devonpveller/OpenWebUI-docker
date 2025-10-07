#!/usr/bin/env python3
"""
LM Studio Fix - Python equivalent of quick-fixes.bat lmstudio
Fixes LM Studio Tailscale connectivity by configuring socat proxy and Tailscale serve
This version works from within the OpenWebUI container environment.
"""

import subprocess
import time
import sys
import os
import requests
from pathlib import Path

def log(level, message):
    """Log a message with level indicator"""
    print(f"[{level}] {message}")

def test_lm_studio_connectivity():
    """Test if LM Studio is accessible on the host"""
    try:
        log("INFO", "Testing LM Studio host connectivity...")
        response = requests.get("http://169.254.83.107:5506/v1/models", timeout=5)
        if response.status_code == 200:
            log("SUCCESS", "LM Studio is running")
            return True
        else:
            log("ERROR", f"LM Studio returned status code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        log("ERROR", f"LM Studio not accessible - make sure it's running: {e}")
        return False

def run_container_command(container_name, command, detach=False, timeout=30):
    """Run a command inside a specific container using docker exec equivalent"""
    try:
        # Since we're running inside a container, we need to use the container's network
        # to communicate with other containers. For Tailscale commands, we can try
        # to execute them directly if the containers share a network namespace.
        
        if container_name == "tailscale":
            # Since Tailscale shares OpenWebUI's network namespace, we might be able
            # to access Tailscale's socket directly
            if "tailscale" in command and "--socket=/tmp/tailscaled.sock" not in command:
                # Add the socket parameter if it's a tailscale command
                command = command.replace("tailscale ", "tailscale --socket=/tmp/tailscaled.sock ")
            
            # Try to run the command directly since we share the network namespace
            result = subprocess.run(
                command,
                shell=True,
                capture_output=not detach,
                text=True,
                timeout=timeout if not detach else None
            )
            return result
        else:
            log("ERROR", f"Cannot execute commands in {container_name} from this context")
            return None
            
    except subprocess.TimeoutExpired:
        log("ERROR", f"Command timed out: {command}")
        return None
    except Exception as e:
        log("ERROR", f"Command failed: {command} - {e}")
        return None

def kill_socat_processes():
    """Kill existing socat processes"""
    log("INFO", "Stopping existing socat processes...")
    # Try to kill socat processes directly since we share network namespace
    command = "pkill socat 2>/dev/null || true"
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    log("INFO", "Socat processes stopped")
    return True

def start_socat_proxy():
    """Start persistent socat proxy for LM Studio"""
    log("INFO", "Starting persistent socat proxy...")
    # Start socat in background
    command = "socat TCP-LISTEN:8234,fork,reuseaddr,keepalive TCP:169.254.83.107:5506 &"
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        log("INFO", "Socat proxy started")
        return True
    else:
        log("ERROR", "Failed to start socat proxy")
        log("ERROR", f"Error: {result.stderr}")
        return False

def test_proxy_connection():
    """Test if the proxy is working"""
    log("INFO", "Testing proxy connection...")
    try:
        response = requests.get("http://127.0.0.1:8234/v1/models", timeout=5)
        if response.status_code == 200:
            log("SUCCESS", "Proxy working")
            return True
        else:
            log("ERROR", f"Proxy test failed with status: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        log("ERROR", f"Proxy test failed: {e}")
        return False

def configure_tailscale_serve():
    """Configure Tailscale serve for LM Studio"""
    log("INFO", "Configuring Tailscale serve...")
    
    # Check if tailscale binary is accessible
    if not os.path.exists("/usr/bin/tailscale") and not os.path.exists("/usr/local/bin/tailscale"):
        log("ERROR", "Tailscale binary not found in this container")
        log("INFO", "This step requires running from Tailscale container or host")
        return False
    
    command = "tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://127.0.0.1:8234"
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
    
    if result.returncode == 0:
        log("SUCCESS", "Tailscale serve configured")
        # Extract access URL from output if available
        if "tail" in result.stdout:
            lines = result.stdout.split('\n')
            for line in lines:
                if "https://" in line and "tail" in line:
                    url = line.strip()
                    if url.startswith("https://"):
                        log("INFO", f"Access URL: {url}/lmstudio")
                        return True
        log("INFO", "Access URL: https://your-tailscale-url/lmstudio")
        return True
    else:
        log("ERROR", "Failed to configure Tailscale serve")
        log("ERROR", f"Error: {result.stderr}")
        log("INFO", "Note: This requires Tailscale access from container")
        return False

def main():
    """Main LM Studio fix function"""
    log("INFO", "Fixing LM Studio Tailscale connectivity...")
    log("INFO", "Running from container environment...")
    
    # Test LM Studio connectivity first
    if not test_lm_studio_connectivity():
        log("ERROR", "Cannot proceed - LM Studio is not accessible")
        return False
    
    # Kill existing socat processes
    if not kill_socat_processes():
        log("WARN", "Failed to stop existing socat processes, continuing anyway...")
    
    # Start socat proxy
    if not start_socat_proxy():
        log("ERROR", "Failed to start socat proxy")
        return False
    
    # Wait for proxy to initialize
    log("INFO", "Waiting for proxy to initialize...")
    time.sleep(8)
    
    # Test proxy connection
    if not test_proxy_connection():
        log("ERROR", "Proxy test failed")
        return False
    
    # Configure Tailscale serve (may not work from this container)
    tailscale_success = configure_tailscale_serve()
    if not tailscale_success:
        log("WARN", "Tailscale serve configuration failed")
        log("INFO", "You may need to run this command manually:")
        log("INFO", "docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://127.0.0.1:8234")
    
    log("SUCCESS", "LM Studio proxy configuration completed")
    if tailscale_success:
        log("SUCCESS", "LM Studio Tailscale configuration restored")
    else:
        log("INFO", "Proxy is running, but Tailscale serve needs manual configuration")
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\n[INFO] For advanced recovery options, use: emergency-recovery.ps1")
            sys.exit(0)
        else:
            sys.exit(1)
    except KeyboardInterrupt:
        log("INFO", "Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        log("ERROR", f"Unexpected error: {e}")
        sys.exit(1)