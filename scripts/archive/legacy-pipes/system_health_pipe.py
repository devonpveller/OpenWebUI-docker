"""
System Health Pipe for AI Stack

Provides comprehensive system health monitoring including Docker services,
resource usage, and overall system status.
"""

import json
import os
import subprocess
import sys
import time
from typing import Dict, Any, List, Optional

def load_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            "ai_stack": {
                "workspace_root": "d:\\Open WebUI\\ai-stack",
                "name": "OpenWebUI AI Stack"
            }
        }

def check_docker_services(workspace_root: str) -> Dict[str, Any]:
    """Check Docker service status"""
    try:
        # Check if Docker is running
        docker_version_result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if docker_version_result.returncode != 0:
            return {
                "docker_available": False,
                "error": "Docker not available or not running",
                "suggestion": "Start Docker Desktop or Docker service"
            }
        
        # Check Docker Compose services
        compose_result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=workspace_root
        )
        
        docker_info = {
            "docker_available": True,
            "docker_version": docker_version_result.stdout.strip(),
            "services_status": "unknown"
        }
        
        if compose_result.returncode == 0 and compose_result.stdout.strip():
            try:
                # Parse container information
                containers = []
                for line in compose_result.stdout.strip().split('\n'):
                    if line.strip():
                        container_info = json.loads(line)
                        containers.append({
                            "name": container_info.get("Name", "unknown"),
                            "service": container_info.get("Service", "unknown"),
                            "state": container_info.get("State", "unknown"),
                            "status": container_info.get("Status", "unknown"),
                            "health": container_info.get("Health", "unknown")
                        })
                
                # Analyze service health
                healthy_services = sum(1 for c in containers if c["state"] == "running")
                total_services = len(containers)
                
                docker_info.update({
                    "services_status": "healthy" if healthy_services == total_services else "degraded",
                    "total_services": total_services,
                    "running_services": healthy_services,
                    "containers": containers
                })
                
                # Check for critical services
                critical_services = ["openwebui", "ollama", "tailscale"]
                critical_status = {}
                for service in critical_services:
                    service_container = next((c for c in containers if c["service"] == service), None)
                    if service_container:
                        critical_status[service] = {
                            "running": service_container["state"] == "running",
                            "status": service_container["status"],
                            "health": service_container["health"]
                        }
                    else:
                        critical_status[service] = {"running": False, "status": "not found"}
                
                docker_info["critical_services"] = critical_status
                
            except json.JSONDecodeError:
                docker_info["services_status"] = "parse_error"
                docker_info["raw_output"] = compose_result.stdout[:200]  # First 200 chars
        
        else:
            docker_info["services_status"] = "compose_error"
            docker_info["error"] = compose_result.stderr.strip() if compose_result.stderr else "Unknown compose error"
        
        return docker_info
        
    except subprocess.TimeoutExpired:
        return {
            "docker_available": False,
            "error": "Docker command timeout",
            "suggestion": "Check if Docker is responding"
        }
    except Exception as e:
        return {
            "docker_available": False,
            "error": str(e),
            "suggestion": "Check Docker installation and service status"
        }

def get_system_resources() -> Dict[str, Any]:
    """Get basic system resource information"""
    try:
        # Try to use psutil if available (may not be in container)
        try:
            import psutil
            return {
                "cpu_usage_percent": psutil.cpu_percent(interval=1),
                "memory_usage_percent": psutil.virtual_memory().percent,
                "disk_usage_percent": psutil.disk_usage('D:').percent,  # Adjust drive as needed
                "resource_method": "psutil"
            }
        except ImportError:
            # Fallback to basic system commands if psutil not available
            return {
                "resource_method": "system_commands",
                "note": "Limited resource information available (psutil not installed)",
                "suggestion": "Install psutil in host environment for detailed resource monitoring"
            }
    except Exception as e:
        return {
            "resource_method": "error",
            "error": str(e)
        }

def check_gpu_integration() -> Dict[str, Any]:
    """Check GPU integration status"""
    try:
        # Try to import torch for GPU check
        import torch
        return {
            "gpu_integration": "available",
            "cuda_available": torch.cuda.is_available(),
            "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0
        }
    except ImportError:
        return {
            "gpu_integration": "torch_not_available",
            "note": "PyTorch not available for GPU checking"
        }
    except Exception as e:
        return {
            "gpu_integration": "error",
            "error": str(e)
        }

def analyze_system_health(docker_info: Dict[str, Any], resources: Dict[str, Any], gpu_info: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze overall system health and provide recommendations"""
    health_score = 100
    issues = []
    recommendations = []
    
    # Docker service analysis
    if not docker_info.get("docker_available"):
        health_score -= 40
        issues.append("Docker not available")
        recommendations.append("Start Docker Desktop or check Docker service")
    elif docker_info.get("services_status") == "degraded":
        health_score -= 20
        issues.append("Some Docker services not running")
        recommendations.append("Check: docker compose ps and restart failed services")
    elif docker_info.get("services_status") == "compose_error":
        health_score -= 30
        issues.append("Docker Compose error detected")
        recommendations.append("Run: docker compose down && docker compose up -d")
    
    # Critical service analysis
    critical_services = docker_info.get("critical_services", {})
    for service, status in critical_services.items():
        if not status.get("running"):
            health_score -= 15
            issues.append(f"{service} service not running")
            recommendations.append(f"Restart {service}: docker compose restart {service}")
    
    # Resource analysis (if available)
    if resources.get("cpu_usage_percent") and resources["cpu_usage_percent"] > 90:
        health_score -= 10
        issues.append("High CPU usage")
        recommendations.append("Check for high CPU processes")
    
    if resources.get("memory_usage_percent") and resources["memory_usage_percent"] > 90:
        health_score -= 10
        issues.append("High memory usage")
        recommendations.append("Consider restarting services to free memory")
    
    # GPU analysis
    if gpu_info.get("gpu_integration") == "available" and not gpu_info.get("cuda_available"):
        health_score -= 15
        issues.append("GPU not available despite integration")
        recommendations.append("Run: scripts\\quick-fixes.bat gpu")
    
    # Determine overall health status
    if health_score >= 90:
        status = "excellent"
        color = "🟢"
    elif health_score >= 70:
        status = "good"
        color = "🟡"
    elif health_score >= 50:
        status = "fair"
        color = "🟠"
    else:
        status = "poor"
        color = "🔴"
    
    return {
        "health_score": health_score,
        "status": status,
        "status_indicator": color,
        "issues": issues,
        "recommendations": recommendations,
        "summary": f"{color} System health: {status} ({health_score}/100)"
    }

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for system health pipe"""
    try:
        config = load_config()
        workspace_root = config.get("ai_stack", {}).get("workspace_root", "d:\\Open WebUI\\ai-stack")
        
        user_input = payload.get("input", "").lower()
        detailed_check = any(keyword in user_input for keyword in [
            "detailed", "comprehensive", "full", "complete", "diagnostic"
        ])
        
        # Collect system information
        docker_info = check_docker_services(workspace_root)
        resources = get_system_resources()
        gpu_info = check_gpu_integration()
        health_analysis = analyze_system_health(docker_info, resources, gpu_info)
        
        result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "service": "AI Stack System Health",
            "health_summary": health_analysis,
            "quick_status": {
                "docker": "✅" if docker_info.get("docker_available") else "❌",
                "services": "✅" if docker_info.get("services_status") == "healthy" else "⚠️",
                "gpu": "✅" if gpu_info.get("cuda_available") else "❌"
            }
        }
        
        if detailed_check or health_analysis["health_score"] < 70:
            # Include detailed information for comprehensive checks or when issues detected
            result.update({
                "detailed_info": {
                    "docker": docker_info,
                    "resources": resources,
                    "gpu": gpu_info
                }
            })
        
        return result
        
    except Exception as e:
        return {
            "status": "❌ System Health Check Failed",
            "error": str(e),
            "type": "system_health_pipe_error",
            "fallback": {
                "commands": [
                    "docker compose ps",
                    "scripts\\quick-fixes.bat status"
                ]
            }
        }

if __name__ == "__main__":
    """CLI mode execution"""
    try:
        payload = json.loads(sys.stdin.read())
        result = main(payload)
        print(json.dumps(result, indent=2))
    except Exception as e:
        error_result = {"error": str(e), "type": "CLI execution error"}
        print(json.dumps(error_result, indent=2))
        sys.exit(1)