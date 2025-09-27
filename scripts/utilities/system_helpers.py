"""
System Helper Utilities for AI Stack

Provides system monitoring utilities and helper functions for the AI stack environment.
"""

import os
import platform
import subprocess
import time
from typing import Dict, Any, Optional

def get_system_info() -> Dict[str, Any]:
    """Get basic system information"""
    try:
        return {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version()
        }
    except Exception as e:
        return {
            "error": str(e),
            "fallback": "System info not available"
        }

def check_process_running(process_name: str) -> bool:
    """Check if a process is running (Windows-focused)"""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
                capture_output=True,
                text=True
            )
            return process_name in result.stdout
        else:
            result = subprocess.run(
                ["pgrep", "-f", process_name],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
    except Exception:
        return False

def get_windows_service_status(service_name: str) -> Dict[str, Any]:
    """Get Windows service status (if running on Windows)"""
    try:
        if platform.system() != "Windows":
            return {"error": "Windows service check only available on Windows"}
        
        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            output = result.stdout
            if "RUNNING" in output:
                status = "running"
            elif "STOPPED" in output:
                status = "stopped"
            else:
                status = "unknown"
            
            return {
                "service": service_name,
                "status": status,
                "available": True,
                "raw_output": output.strip()
            }
        else:
            return {
                "service": service_name,
                "status": "not_found",
                "available": False,
                "error": result.stderr.strip()
            }
    
    except Exception as e:
        return {
            "service": service_name,
            "error": str(e),
            "available": False
        }

def check_port_availability(port: int, host: str = "127.0.0.1") -> Dict[str, Any]:
    """Check if a port is available/in use"""
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        
        return {
            "port": port,
            "host": host,
            "available": result != 0,
            "in_use": result == 0
        }
    except Exception as e:
        return {
            "port": port,
            "host": host,
            "error": str(e),
            "available": None
        }

def get_disk_usage(path: str = "D:") -> Dict[str, Any]:
    """Get disk usage information"""
    try:
        if hasattr(os, 'statvfs'):  # Unix/Linux
            statvfs = os.statvfs(path)
            total = statvfs.f_frsize * statvfs.f_blocks
            free = statvfs.f_frsize * statvfs.f_available
            used = total - free
        else:  # Windows
            import shutil
            total, used, free = shutil.disk_usage(path)
        
        return {
            "path": path,
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "usage_percent": round((used / total) * 100, 2)
        }
    except Exception as e:
        return {
            "path": path,
            "error": str(e)
        }

def run_powershell_command(command: str, timeout: int = 30) -> Dict[str, Any]:
    """Run a PowerShell command and return structured results"""
    try:
        if platform.system() != "Windows":
            return {
                "success": False,
                "error": "PowerShell commands only supported on Windows"
            }
        
        result = subprocess.run(
            ["powershell", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "command": command
        }
    
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "timeout",
            "timeout": timeout,
            "command": command
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "command": command
        }

def check_network_connectivity(hosts: Optional[list] = None) -> Dict[str, Any]:
    """Check network connectivity to specified hosts"""
    if hosts is None:
        hosts = ["8.8.8.8", "1.1.1.1", "google.com"]
    
    results = {}
    
    for host in hosts:
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["ping", "-n", "1", "-w", "2000", host],
                    capture_output=True,
                    text=True
                )
            else:
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", host],
                    capture_output=True,
                    text=True
                )
            
            results[host] = {
                "reachable": result.returncode == 0,
                "response_time": "unknown"  # Could parse this from output if needed
            }
        
        except Exception as e:
            results[host] = {
                "reachable": False,
                "error": str(e)
            }
    
    # Calculate overall connectivity
    reachable_count = sum(1 for r in results.values() if r.get("reachable"))
    total_hosts = len(hosts)
    
    return {
        "hosts": results,
        "connectivity_score": f"{reachable_count}/{total_hosts}",
        "overall_status": "good" if reachable_count == total_hosts else "degraded" if reachable_count > 0 else "poor"
    }

def get_environment_info() -> Dict[str, Any]:
    """Get relevant environment information for AI stack"""
    env_info = {
        "python_path": os.environ.get("PYTHONPATH", "not set"),
        "path_dirs": os.environ.get("PATH", "").split(os.pathsep)[:5],  # First 5 PATH entries
        "user": os.environ.get("USERNAME" if platform.system() == "Windows" else "USER", "unknown"),
        "home": os.environ.get("USERPROFILE" if platform.system() == "Windows" else "HOME", "unknown")
    }
    
    # Check for Docker-related environment variables
    docker_env = {}
    docker_vars = ["DOCKER_HOST", "DOCKER_COMPOSE_PROJECT_NAME", "COMPOSE_FILE"]
    for var in docker_vars:
        if var in os.environ:
            docker_env[var] = os.environ[var]
    
    if docker_env:
        env_info["docker_env"] = docker_env
    
    # Check for CUDA-related environment variables
    cuda_env = {}
    cuda_vars = ["CUDA_PATH", "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES"]
    for var in cuda_vars:
        if var in os.environ:
            cuda_env[var] = os.environ[var]
    
    if cuda_env:
        env_info["cuda_env"] = cuda_env
    
    return env_info

def create_system_snapshot() -> Dict[str, Any]:
    """Create a comprehensive system snapshot for diagnostics"""
    snapshot = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system_info": get_system_info(),
        "environment": get_environment_info(),
        "network": check_network_connectivity(),
        "disk_usage": get_disk_usage(),
        "ports": {
            "openwebui": check_port_availability(3000),
            "ollama": check_port_availability(11434)
        }
    }
    
    # Add Docker service if available
    if check_process_running("Docker Desktop.exe" if platform.system() == "Windows" else "dockerd"):
        snapshot["docker_process"] = True
    else:
        snapshot["docker_process"] = False
    
    # Add Windows service checks if on Windows
    if platform.system() == "Windows":
        services = ["Docker Desktop Service", "com.docker.service"]
        snapshot["windows_services"] = {}
        for service in services:
            snapshot["windows_services"][service] = get_windows_service_status(service)
    
    return snapshot