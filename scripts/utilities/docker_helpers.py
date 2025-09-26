"""
Docker Helper Utilities for AI Stack

Provides helper functions for Docker and Docker Compose operations
within the AI stack environment.
"""

import json
import subprocess
import time
from typing import Dict, Any, List, Optional

def run_docker_command(command: List[str], timeout: int = 30, cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run a Docker command and return structured results"""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd
        )
        
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "command": " ".join(command)
        }
    
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "timeout",
            "timeout": timeout,
            "command": " ".join(command)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "command": " ".join(command)
        }

def get_container_status(workspace_root: str) -> Dict[str, Any]:
    """Get detailed container status for AI stack services"""
    compose_result = run_docker_command(
        ["docker", "compose", "ps", "--format", "json"],
        cwd=workspace_root
    )
    
    if not compose_result["success"]:
        return {
            "error": "Failed to get container status",
            "details": compose_result
        }
    
    try:
        containers = []
        if compose_result["stdout"]:
            for line in compose_result["stdout"].split('\n'):
                if line.strip():
                    container_info = json.loads(line)
                    containers.append({
                        "name": container_info.get("Name", "unknown"),
                        "service": container_info.get("Service", "unknown"),
                        "state": container_info.get("State", "unknown"),
                        "status": container_info.get("Status", "unknown"),
                        "health": container_info.get("Health", "unknown"),
                        "ports": container_info.get("Publishers", [])
                    })
        
        # Analyze service health
        services_status = {}
        critical_services = ["openwebui", "ollama", "tailscale"]
        
        for service in critical_services:
            service_container = next((c for c in containers if c["service"] == service), None)
            if service_container:
                services_status[service] = {
                    "running": service_container["state"] == "running",
                    "healthy": service_container["health"] in ["healthy", ""],
                    "status": service_container["status"]
                }
            else:
                services_status[service] = {
                    "running": False,
                    "healthy": False,
                    "status": "not found"
                }
        
        return {
            "containers": containers,
            "services_status": services_status,
            "total_containers": len(containers),
            "running_containers": sum(1 for c in containers if c["state"] == "running")
        }
    
    except json.JSONDecodeError as e:
        return {
            "error": "Failed to parse container information",
            "parse_error": str(e),
            "raw_output": compose_result["stdout"][:200]
        }

def restart_service(service_name: str, workspace_root: str) -> Dict[str, Any]:
    """Restart a specific Docker Compose service"""
    restart_result = run_docker_command(
        ["docker", "compose", "restart", service_name],
        cwd=workspace_root
    )
    
    if not restart_result["success"]:
        return {
            "success": False,
            "service": service_name,
            "error": "Restart command failed",
            "details": restart_result
        }
    
    # Wait a moment and check if service is running
    time.sleep(2)
    status_check = get_container_status(workspace_root)
    
    if "services_status" in status_check:
        service_status = status_check["services_status"].get(service_name)
        if service_status and service_status["running"]:
            return {
                "success": True,
                "service": service_name,
                "status": "running",
                "message": f"Service {service_name} restarted successfully"
            }
    
    return {
        "success": False,
        "service": service_name,
        "status": "unknown",
        "message": f"Service {service_name} restart completed but status unclear"
    }

def get_docker_logs(service_name: str, lines: int = 50, workspace_root: str = None) -> Dict[str, Any]:
    """Get recent logs for a Docker service"""
    logs_result = run_docker_command(
        ["docker", "compose", "logs", "--tail", str(lines), service_name],
        cwd=workspace_root
    )
    
    if not logs_result["success"]:
        return {
            "success": False,
            "service": service_name,
            "error": "Failed to get logs",
            "details": logs_result
        }
    
    return {
        "success": True,
        "service": service_name,
        "logs": logs_result["stdout"],
        "lines_requested": lines
    }

def rebuild_service(service_name: str, workspace_root: str, no_cache: bool = True) -> Dict[str, Any]:
    """Rebuild a Docker Compose service"""
    build_command = ["docker", "compose", "build"]
    if no_cache:
        build_command.append("--no-cache")
    build_command.append(service_name)
    
    build_result = run_docker_command(build_command, timeout=300, cwd=workspace_root)  # Extended timeout for builds
    
    if not build_result["success"]:
        return {
            "success": False,
            "service": service_name,
            "stage": "build",
            "error": "Build failed",
            "details": build_result
        }
    
    # After successful build, restart the service
    restart_result = run_docker_command(
        ["docker", "compose", "up", "-d", service_name],
        cwd=workspace_root
    )
    
    return {
        "success": restart_result["success"],
        "service": service_name,
        "build_success": True,
        "restart_success": restart_result["success"],
        "details": {
            "build": build_result,
            "restart": restart_result
        }
    }

def check_docker_system() -> Dict[str, Any]:
    """Check Docker system status and health"""
    # Check if Docker is running
    version_result = run_docker_command(["docker", "--version"])
    if not version_result["success"]:
        return {
            "docker_available": False,
            "error": "Docker not available",
            "suggestion": "Start Docker Desktop or Docker service"
        }
    
    # Get system info
    info_result = run_docker_command(["docker", "system", "info", "--format", "json"])
    
    system_info = {
        "docker_available": True,
        "version": version_result["stdout"]
    }
    
    if info_result["success"]:
        try:
            docker_info = json.loads(info_result["stdout"])
            system_info.update({
                "containers_running": docker_info.get("ContainersRunning", 0),
                "containers_total": docker_info.get("Containers", 0),
                "images_total": docker_info.get("Images", 0),
                "server_version": docker_info.get("ServerVersion", "unknown")
            })
        except json.JSONDecodeError:
            system_info["info_parse_error"] = "Could not parse Docker system info"
    
    return system_info