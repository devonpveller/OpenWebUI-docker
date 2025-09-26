#!/usr/bin/env python3
"""
System Health Module - Refactored Architecture

Manifest-driven system health monitoring module implementing the new AI Stack architecture.
Provides comprehensive system monitoring with structured contracts.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

def setup_logging() -> logging.Logger:
    """Setup module logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("system_health_module")

logger = setup_logging()

class SystemHealthModule:
    """System Health Module implementing manifest-driven architecture"""
    
    def __init__(self):
        self.module_id = "system-health"
        self.version = "1.0.0-migrated"
        self.config = self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        """Load system health configuration"""
        return {
            "ai_stack": {
                "workspace_root": "/host_scripts",  # Container context
                "name": "OpenWebUI AI Stack",
                "expected_services": ["openwebui", "ollama", "tailscale", "watchtower"]
            },
            "monitoring": {
                "check_docker": True,
                "check_gpu": True,
                "check_network": True,
                "check_storage": True
            }
        }
    
    def check_container_environment(self) -> Dict[str, Any]:
        """Check if we're running in the expected container environment"""
        checks = {
            "in_container": os.path.exists("/.dockerenv"),
            "openwebui_context": os.path.exists("/app"),
            "host_scripts_mounted": os.path.exists("/host_scripts"),
            "gpu_available": False
        }
        
        # Check GPU availability
        try:
            import torch
            checks["gpu_available"] = torch.cuda.is_available()
            checks["torch_version"] = torch.__version__
        except ImportError:
            checks["torch_available"] = False
        
        return checks
    
    def check_docker_services(self) -> Dict[str, Any]:
        """Check Docker service status"""
        try:
            # In container context, we can only check our own container and possibly siblings
            # This would need to be adapted based on actual deployment setup
            
            service_status = {
                "docker_available": True,  # If we're running, Docker is available
                "current_container": "openwebui",  # Assuming we're in OpenWebUI container
                "services_accessible": False,
                "note": "Service checks limited in container context"
            }
            
            # Try to check if we can access docker socket (if mounted)
            if os.path.exists("/var/run/docker.sock"):
                service_status["docker_socket_accessible"] = True
                # Could potentially run docker commands here if socket is accessible
            else:
                service_status["docker_socket_accessible"] = False
                service_status["limitation"] = "Docker socket not mounted - cannot check external services"
            
            return service_status
            
        except Exception as e:
            return {
                "docker_available": False,
                "error": str(e),
                "suggestion": "Check Docker service availability"
            }
    
    def check_network_connectivity(self) -> Dict[str, Any]:
        """Check network connectivity"""
        connectivity_results = {
            "checks_performed": [],
            "overall_status": "unknown"
        }
        
        # Test basic internet connectivity
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "8.8.8.8"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            connectivity_results["checks_performed"].append({
                "test": "Internet connectivity (8.8.8.8)",
                "status": "success" if result.returncode == 0 else "failed",
                "details": "Can reach external internet" if result.returncode == 0 else "Cannot reach external internet"
            })
            
        except subprocess.TimeoutExpired:
            connectivity_results["checks_performed"].append({
                "test": "Internet connectivity (8.8.8.8)",
                "status": "timeout",
                "details": "Ping timeout - possible network issues"
            })
        except Exception as e:
            connectivity_results["checks_performed"].append({
                "test": "Internet connectivity (8.8.8.8)",
                "status": "error",
                "details": f"Network test error: {str(e)}"
            })
        
        # Test internal service connectivity (if we can)
        try:
            # Test Ollama connectivity (typical internal service)
            result = subprocess.run(
                ["curl", "-s", "-f", "http://localhost:11434/api/tags"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            connectivity_results["checks_performed"].append({
                "test": "Ollama service connectivity",
                "status": "success" if result.returncode == 0 else "failed",
                "details": "Can reach Ollama API" if result.returncode == 0 else "Cannot reach Ollama API"
            })
            
        except Exception as e:
            connectivity_results["checks_performed"].append({
                "test": "Ollama service connectivity",
                "status": "error", 
                "details": f"Service test error: {str(e)}"
            })
        
        # Determine overall status
        successful_checks = sum(1 for check in connectivity_results["checks_performed"] if check["status"] == "success")
        total_checks = len(connectivity_results["checks_performed"])
        
        if successful_checks == total_checks:
            connectivity_results["overall_status"] = "healthy"
        elif successful_checks > 0:
            connectivity_results["overall_status"] = "partial"
        else:
            connectivity_results["overall_status"] = "unhealthy"
        
        return connectivity_results
    
    def check_system_resources(self) -> Dict[str, Any]:
        """Check system resource usage"""
        resources = {
            "cpu": {"available": False},
            "memory": {"available": False}, 
            "disk": {"available": False}
        }
        
        try:
            # Try to get CPU info
            if os.path.exists("/proc/cpuinfo"):
                with open("/proc/cpuinfo", "r") as f:
                    cpu_lines = f.readlines()
                cpu_count = len([line for line in cpu_lines if line.startswith("processor")])
                resources["cpu"] = {
                    "available": True,
                    "cores": cpu_count,
                    "info_source": "/proc/cpuinfo"
                }
        except Exception as e:
            resources["cpu"]["error"] = str(e)
        
        try:
            # Try to get memory info
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    meminfo = f.read()
                
                # Parse key memory metrics
                import re
                total_match = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
                free_match = re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo)
                
                if total_match and free_match:
                    total_kb = int(total_match.group(1))
                    free_kb = int(free_match.group(1))
                    
                    resources["memory"] = {
                        "available": True,
                        "total_gb": round(total_kb / 1024 / 1024, 2),
                        "free_gb": round(free_kb / 1024 / 1024, 2),
                        "used_percent": round((total_kb - free_kb) / total_kb * 100, 1),
                        "info_source": "/proc/meminfo"
                    }
        except Exception as e:
            resources["memory"]["error"] = str(e)
        
        try:
            # Check disk usage for key paths
            disk_checks = ["/", "/tmp", "/app"]
            disk_info = []
            
            for path in disk_checks:
                if os.path.exists(path):
                    stat = os.statvfs(path)
                    total_bytes = stat.f_frsize * stat.f_blocks
                    free_bytes = stat.f_frsize * stat.f_available
                    used_percent = (total_bytes - free_bytes) / total_bytes * 100
                    
                    disk_info.append({
                        "path": path,
                        "total_gb": round(total_bytes / 1024**3, 2),
                        "free_gb": round(free_bytes / 1024**3, 2),
                        "used_percent": round(used_percent, 1)
                    })
            
            resources["disk"] = {
                "available": True,
                "mounts": disk_info
            }
            
        except Exception as e:
            resources["disk"]["error"] = str(e)
        
        return resources
    
    def get_comprehensive_health_report(self) -> Dict[str, Any]:
        """Get comprehensive system health report"""
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_status": "unknown",
            "checks": {}
        }
        
        # Container environment check
        report["checks"]["container_environment"] = self.check_container_environment()
        
        # Docker services check
        report["checks"]["docker_services"] = self.check_docker_services()
        
        # Network connectivity check
        report["checks"]["network_connectivity"] = self.check_network_connectivity()
        
        # System resources check
        report["checks"]["system_resources"] = self.check_system_resources()
        
        # Calculate overall health score
        health_score = 100
        critical_issues = []
        
        # Check container environment
        container_env = report["checks"]["container_environment"]
        if not container_env.get("host_scripts_mounted"):
            health_score -= 30
            critical_issues.append("Host scripts not mounted - pipe functions may not work")
        
        if not container_env.get("gpu_available", False):
            health_score -= 20  # Not critical but important for AI workloads
        
        # Check network connectivity
        network_status = report["checks"]["network_connectivity"].get("overall_status")
        if network_status == "unhealthy":
            health_score -= 40
            critical_issues.append("Network connectivity issues detected")
        elif network_status == "partial":
            health_score -= 20
        
        # Determine overall status
        if health_score >= 80:
            report["overall_status"] = "healthy"
        elif health_score >= 60:
            report["overall_status"] = "degraded"
        else:
            report["overall_status"] = "unhealthy"
        
        report["health_score"] = health_score
        report["critical_issues"] = critical_issues
        
        return report

    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {
            "module_id": self.module_id,
            "version": self.version,
            "name": "System Health Monitor",
            "capabilities": ["system_monitoring", "health_checks", "resource_monitoring"],
            "status": "ready"
        }
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        health_score = 100
        issues = []
        
        # Check if we can perform basic system checks
        if not os.path.exists("/proc"):
            health_score -= 30
            issues.append("Cannot access /proc filesystem for system metrics")
        
        # Check if network tools are available
        try:
            subprocess.run(["ping", "--help"], capture_output=True, timeout=2)
        except:
            health_score -= 20
            issues.append("Network testing tools not available")
        
        status = "healthy" if health_score >= 75 else "degraded" if health_score >= 50 else "unhealthy"
        
        return {
            "module_id": self.module_id,
            "status": status,
            "health_score": health_score,
            "issues": issues,
            "capabilities": ["system_health", "resource_monitoring"]
        }
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute system health check with migrated functionality"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Parse input - handle both string and structured input
            input_data = request_data.get("input", "")
            if isinstance(input_data, dict):
                user_input = input_data.get("query", "")
            else:
                user_input = str(input_data)
            
            # Always run comprehensive health report
            result_data = self.get_comprehensive_health_report()
            content = self._format_health_report(result_data, user_input)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok",
                "content": content,
                "structured_data": result_data,
                "diagnostics": {
                    "execution_time_ms": execution_time
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ System health execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **System Health Error**: {str(e)}",
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _format_health_report(self, result_data: Dict[str, Any], user_input: str = "") -> str:
        """Format health report for display"""
        overall_status = result_data.get("overall_status", "unknown")
        health_score = result_data.get("health_score", 0)
        critical_issues = result_data.get("critical_issues", [])
        checks = result_data.get("checks", {})
        
        # Status emoji mapping
        status_emoji = {
            "healthy": "✅",
            "degraded": "⚠️", 
            "unhealthy": "❌",
            "unknown": "❓"
        }
        
        content = f"""## 🏥 System Health Report

**Overall Status**: {status_emoji.get(overall_status, "❓")} {overall_status.title()}
**Health Score**: {health_score}/100
**Timestamp**: {result_data.get("timestamp", "Unknown")}
"""
        
        # Add critical issues if any
        if critical_issues:
            content += "\n### 🚨 Critical Issues:\n"
            for issue in critical_issues:
                content += f"- {issue}\n"
        
        # Container Environment
        if "container_environment" in checks:
            env = checks["container_environment"]
            content += f"""
### 🐳 Container Environment
- **In Container**: {"✅" if env.get("in_container") else "❌"}
- **OpenWebUI Context**: {"✅" if env.get("openwebui_context") else "❌"}  
- **Host Scripts Mounted**: {"✅" if env.get("host_scripts_mounted") else "❌"}
- **GPU Available**: {"✅" if env.get("gpu_available") else "❌"}
"""
            if "torch_version" in env:
                content += f"- **PyTorch Version**: {env['torch_version']}\n"
        
        # Network Connectivity
        if "network_connectivity" in checks:
            net = checks["network_connectivity"]
            content += f"""
### 🌐 Network Connectivity
**Overall Status**: {status_emoji.get(net.get("overall_status"), "❓")} {net.get("overall_status", "unknown").title()}

**Tests Performed**:
"""
            for check in net.get("checks_performed", []):
                status_icon = "✅" if check["status"] == "success" else "❌" if check["status"] == "failed" else "⚠️"
                content += f"- {status_icon} **{check['test']}**: {check['details']}\n"
        
        # System Resources
        if "system_resources" in checks:
            resources = checks["system_resources"]
            content += "\n### 💻 System Resources\n"
            
            # CPU
            cpu = resources.get("cpu", {})
            if cpu.get("available"):
                content += f"- **CPU**: {cpu.get('cores', 'Unknown')} cores\n"
            
            # Memory
            memory = resources.get("memory", {})
            if memory.get("available"):
                content += f"- **Memory**: {memory.get('used_percent', 0)}% used ({memory.get('free_gb', 0):.1f}GB free / {memory.get('total_gb', 0):.1f}GB total)\n"
            
            # Disk
            disk = resources.get("disk", {})
            if disk.get("available"):
                content += "- **Disk Usage**:\n"
                for mount in disk.get("mounts", []):
                    content += f"  - `{mount['path']}`: {mount['used_percent']}% used ({mount['free_gb']:.1f}GB free)\n"
        
        content += "\n*Use 'detailed system health' for comprehensive diagnostics*"
        
        return content
    
    def validate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate input without execution"""
        required_fields = ["request_id", "input"]
        missing_fields = [field for field in required_fields if field not in input_data]
        
        if missing_fields:
            return {
                "valid": False,
                "errors": [f"Missing required field: {field}" for field in missing_fields]
            }
        
        return {"valid": True, "errors": []}

# Create module instance
health_module = SystemHealthModule()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return health_module.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return health_module.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""
    return health_module.health()

def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate input"""
    return health_module.validate(input_data)

if __name__ == "__main__":
    # Check for piped input first
    if not sys.stdin.isatty():
        # Input from pipe
        try:
            input_data = json.loads(sys.stdin.read())
            result = main(input_data)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            error_result = {"error": str(e), "type": "CLI execution error"}
            print(json.dumps(error_result, indent=2))
            sys.exit(1)
    elif len(sys.argv) > 1:
        # CLI mode with arguments
        if sys.argv[1] == "--describe":
            print(json.dumps(describe(), indent=2))
        elif sys.argv[1] == "--health":
            print(json.dumps(health(), indent=2))
        else:
            # Process command line arguments as input
            input_text = " ".join(sys.argv[1:])
            input_data = {"request_id": str(time.time()), "input": input_text}
            result = main(input_data)
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Interactive mode - show description
        print(json.dumps(describe(), indent=2))