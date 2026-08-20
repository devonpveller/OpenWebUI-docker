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


# ---------------------------------------------------------------------------
# AI-Stack service roster — the key service endpoints this module probes to
# report stack health to the end user. Each is reachable from the openwebui
# container over the internal docker networks (default + llm-net). Grouped by
# plane. Keep in sync with docker-compose.yml / OB1 — see the /stack-map skill.
# ---------------------------------------------------------------------------
_AI_STACK_SERVICES: List[Dict[str, Any]] = [
    # Core inference (caller plane). The gateway is the single ingress, so from
    # this network its liveness IS the "can the stack serve inference" signal.
    # The real servers (llama-cpp-upstream / llama-cpp-embed-upstream) are
    # isolated on llm-backend-net (2026-06-13) and intentionally NOT reachable
    # from here; their detailed health is owned by the recovery plane
    # (check-tailscale-health.ps1 probes them via `docker exec ... localhost:8080`
    # + each container's docker healthcheck + host ports 127.0.0.1:8081/8082).
    # Do NOT add direct *-upstream probes here -- that routes around the gateway
    # (see scripts/check-llm-gateway-routing.ps1).
    {"name": "llm-gateway",              "plane": "Core", "host": "llm-gateway",              "port": 8080, "path": "/health/liveliness",       "critical": True},
    # Memory layer.
    {"name": "mnemory",         "plane": "Memory",       "host": "mnemory",         "port": 8051, "path": "/health",                  "critical": False},
    {"name": "mnemory-gateway", "plane": "Memory",       "host": "mnemory-gateway", "port": 8060, "path": "/health",                  "critical": False},
    # Private Search Gateway (`gateway` also joins the default network).
    # Probe /readyz (NOT /healthz): /healthz is liveness-only and returns 200
    # whenever the event loop serves, so a dead Redis or an unreachable SearXNG
    # (Tor chain down, all engines failing) stays invisible. /readyz is 503
    # unless Redis answers AND at least one provider is actually healthy.
    {"name": "gateway",         "plane": "Search",       "host": "gateway",         "port": 8080, "path": "/readyz",                  "critical": False},
    # little-coder control plane.
    {"name": "open-terminal",   "plane": "little-coder", "host": "open-terminal",   "port": 8000, "path": "/health",                  "critical": False},
    {"name": "little-coder",    "plane": "little-coder", "host": "little-coder",    "port": 8090, "path": "/health",                  "critical": False},
    {"name": "lc-mcpo",         "plane": "little-coder", "host": "lc-mcpo",         "port": 8002, "path": "/openapi.json",            "critical": False},
    # Auxiliary.
    {"name": "open_notebook",   "plane": "Auxiliary",    "host": "open_notebook",   "port": 5055, "path": "/api/config",              "critical": False},
    # Open Brain (OB1) — separate compose project on the shared llm-net.
    {"name": "openbrain-mcpo",  "plane": "Open Brain",   "host": "openbrain-mcpo",  "port": 8000, "path": "/open-brain/openapi.json", "critical": False},
    # openbrain-research (shared research engine, on ai-stack_llm-net). Its
    # /health does a live `SELECT 1`, returning 503 when the Postgres pool has
    # gone stale (e.g. after an openbrain-db restart the Deno pool never
    # reconnects) — the exact failure that surfaces to callers as a research
    # 500 while web search is fine. Without this probe that outage was invisible.
    {"name": "openbrain-research", "plane": "Open Brain", "host": "openbrain-research", "port": 8000, "path": "/health",            "critical": False},
]


def _probe_service_http(host: str, port: int, path: str, timeout: float = 3.0) -> Dict[str, Any]:
    """HTTP GET a service health endpoint. Returns reachability and whether the
    response code indicates health. Dependency-free (stdlib urllib)."""
    import urllib.request
    import urllib.error

    url = f"http://{host}:{port}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai-stack-health/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
            return {"reachable": True, "http_code": code, "healthy": 200 <= code < 400}
    except urllib.error.HTTPError as exc:
        # The server answered — it is up, just not with a 2xx/3xx.
        return {"reachable": True, "http_code": exc.code, "healthy": False}
    except Exception as exc:  # URLError, timeout, DNS failure, refused, …
        reason = getattr(exc, "reason", exc)
        return {"reachable": False, "healthy": False, "error": str(reason)[:80]}


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
                "workspace_root": "/host_project/status-pipe",  # Container context
                "name": "OpenWebUI AI Stack",
                # Core always-on containers. ollama was retired — llama-cpp is
                # the inference backend now. Live per-service health is probed by
                # check_ai_stack_services().
                "expected_services": ["openwebui", "llm-gateway", "llama-cpp-upstream", "llama-cpp-embed-upstream", "tailscale"]
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
            "host_project_mounted": os.path.exists("/host_project/status-pipe"),
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
        """Check Docker service status - adapted for container context"""
        try:
            # If we're in a container, we can't run docker commands directly
            # Instead, infer Docker status from our container environment
            in_container = os.path.exists("/.dockerenv")
            
            if in_container:
                # We're running inside OpenWebUI container - Docker is obviously working
                docker_available = True
                docker_version = "Available (inferred from container context)"
                compose_available = True  # If we're here, compose worked to start us
                
                service_status = {
                    "docker_available": docker_available,
                    "docker_version": docker_version,
                    "compose_available": compose_available,
                    "ai_stack_services": "accessible",
                    "note": "Running inside container - Docker services inferred as healthy",
                    "context": "container"
                }
            else:
                # Host environment - try actual Docker commands
                docker_available = False
                docker_version = None
                compose_available = False
                
                try:
                    # Test Docker availability
                    result = subprocess.run(
                        ["docker", "version", "--format", "json"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    
                    if result.returncode == 0:
                        docker_available = True
                        try:
                            import json
                            version_data = json.loads(result.stdout)
                            docker_version = version_data.get("Client", {}).get("Version", "Unknown")
                        except:
                            docker_version = "Available"
                            
                except Exception:
                    pass
                
                # Test Docker Compose availability in host context
                try:
                    result = subprocess.run(
                        ["docker", "compose", "version"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    compose_available = result.returncode == 0
                except Exception:
                    pass
                
                service_status = {
                    "docker_available": docker_available,
                    "docker_version": docker_version,
                    "compose_available": compose_available,
                    "context": "host"
                }
                
                if docker_available:
                    # Try to get AI Stack specific services if we're in the right context
                    try:
                        result = subprocess.run(
                            ["docker", "compose", "ps", "--format", "json"],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            cwd=self.config["ai_stack"].get("workspace_root", ".")
                        )
                        
                        if result.returncode == 0:
                            service_status["ai_stack_services"] = "accessible"
                            # Could parse JSON to get specific service status
                        else:
                            service_status["ai_stack_services"] = "not_accessible"
                            service_status["note"] = "Docker available but AI Stack compose not accessible from current location"
                            
                    except Exception as e:
                        service_status["ai_stack_services"] = "unknown"
                        service_status["note"] = f"Docker available but compose check failed: {str(e)[:100]}"
                
                # Check docker socket access (for container environments)
                if os.path.exists("/var/run/docker.sock"):
                    service_status["docker_socket_accessible"] = True
                else:
                    service_status["docker_socket_accessible"] = False
                    
            return service_status
            
        except Exception as e:
            return {
                "docker_available": False,
                "error": str(e),
                "suggestion": "Check Docker Desktop installation or Docker service status"
            }
    
    def check_network_connectivity(self) -> Dict[str, Any]:
        """Check network connectivity - adapted for container environment"""
        connectivity_results = {
            "checks_performed": [],
            "overall_status": "unknown"
        }
        
        # Test basic internet connectivity using Python instead of ping
        try:
            import socket
            import time
            
            # Test connection to Google DNS
            start_time = time.time()
            sock = socket.create_connection(("8.8.8.8", 53), timeout=5)
            sock.close()
            response_time = int((time.time() - start_time) * 1000)
            
            connectivity_results["checks_performed"].append({
                "test": "Internet connectivity (8.8.8.8)",
                "status": "success",
                "details": f"Can reach external internet ({response_time}ms)"
            })
            
        except socket.timeout:
            connectivity_results["checks_performed"].append({
                "test": "Internet connectivity (8.8.8.8)",
                "status": "timeout",
                "details": "Connection timeout - possible network issues"
            })
        except Exception as e:
            connectivity_results["checks_performed"].append({
                "test": "Internet connectivity (8.8.8.8)",
                "status": "error",
                "details": f"Network test error: {str(e)}"
            })
        
        # AI-Stack service reachability is checked separately, and in depth,
        # by check_ai_stack_services() — this method now covers only internet
        # egress. (The old Ollama socket probe here was retired with the
        # container; llama-cpp is the inference backend now.)

        # Determine overall status
        successful_checks = sum(1 for check in connectivity_results["checks_performed"] if check["status"] == "success")
        warning_checks = sum(1 for check in connectivity_results["checks_performed"] if check["status"] == "warning")
        total_checks = len(connectivity_results["checks_performed"])
        
        if successful_checks == total_checks:
            connectivity_results["overall_status"] = "healthy"
        elif successful_checks > 0 and warning_checks > 0:
            connectivity_results["overall_status"] = "partial"  # Some success, some warnings
        elif successful_checks > 0:
            connectivity_results["overall_status"] = "partial"
        else:
            connectivity_results["overall_status"] = "unhealthy"
        
        return connectivity_results

    def check_ai_stack_services(self) -> Dict[str, Any]:
        """Probe the AI-Stack service endpoints reachable from this container.

        HTTP-GETs each service's health endpoint in parallel over the internal
        docker networks and reports per-service health. This is the end-user
        "is my stack up?" view; it spans both compose projects (main + OB1).
        """
        from concurrent.futures import ThreadPoolExecutor

        def _one(svc: Dict[str, Any]) -> Dict[str, Any]:
            probe = _probe_service_http(svc["host"], svc["port"], svc["path"])
            return {
                "service": svc["name"],
                "plane": svc["plane"],
                "critical": svc.get("critical", False),
                "endpoint": f"{svc['host']}:{svc['port']}{svc['path']}",
                **probe,
            }

        try:
            with ThreadPoolExecutor(max_workers=min(10, len(_AI_STACK_SERVICES))) as pool:
                services = list(pool.map(_one, _AI_STACK_SERVICES))
        except Exception as e:
            return {"available": False, "error": str(e), "services": []}

        return {
            "available": True,
            "total": len(services),
            "healthy": sum(1 for s in services if s.get("healthy")),
            "reachable": sum(1 for s in services if s.get("reachable")),
            "services": services,
        }

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

        # AI-Stack service health check — the per-service "is my stack up?" view
        report["checks"]["ai_stack_services"] = self.check_ai_stack_services()

        # System resources check
        report["checks"]["system_resources"] = self.check_system_resources()
        
        # Calculate overall health score
        health_score = 100
        critical_issues = []
        
        # Check container environment
        container_env = report["checks"]["container_environment"]
        in_container = container_env.get("in_container", False)
        
        if not container_env.get("host_project_mounted"):
            health_score -= 30
            critical_issues.append("Host project not mounted - pipe functions may not work")
        
        # Only penalize GPU if we're in a container context where it's expected
        if in_container and not container_env.get("gpu_available", False):
            health_score -= 20  # Not critical but important for AI workloads in container
        
        # Check Docker services - adjust scoring based on context
        docker_services = report["checks"]["docker_services"]
        docker_context = docker_services.get("context", "unknown")
        
        if docker_context == "container":
            # In container context, Docker is inferred to be working
            # Only minor penalty if we can't detect it properly
            if not docker_services.get("docker_available", False):
                health_score -= 10  # Minor penalty for detection issues
        else:
            # Host context - penalize more heavily for missing Docker
            if not docker_services.get("docker_available", False):
                health_score -= 30
                critical_issues.append("Docker not available - AI Stack services cannot run")
            elif not docker_services.get("compose_available", False):
                health_score -= 15
                critical_issues.append("Docker Compose not available - limited service management")
        
        # Check network connectivity
        network_status = report["checks"]["network_connectivity"].get("overall_status")
        if network_status == "unhealthy":
            health_score -= 40
            critical_issues.append("Network connectivity issues detected")
        elif network_status == "partial":
            health_score -= 20

        # Check AI-Stack services
        ai_services = report["checks"].get("ai_stack_services", {})
        if ai_services.get("available"):
            down_critical = [s for s in ai_services["services"]
                             if s.get("critical") and not s.get("healthy")]
            down_other = [s for s in ai_services["services"]
                          if not s.get("critical") and not s.get("healthy")]
            if down_critical:
                health_score -= min(40, 20 * len(down_critical))
                names = ", ".join(s["service"] for s in down_critical)
                critical_issues.append(f"Critical service(s) unavailable: {names}")
            if down_other:
                health_score -= min(20, 5 * len(down_other))

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

### 📊 Quick Status
"""
        
        # Quick status summary
        docker_status = checks.get("docker_services", {})
        docker_icon = "✅" if docker_status.get("docker_available") else "❌"
        
        services_icon = "✅"
        if not docker_status.get("docker_available"):
            services_icon = "❌"
        elif docker_status.get("ai_stack_services") == "not_accessible":
            services_icon = "⚠️"
        elif docker_status.get("ai_stack_services") != "accessible":
            services_icon = "❓"
            
        gpu_status = checks.get("container_environment", {})
        gpu_icon = "✅" if gpu_status.get("gpu_available") else "❌"

        ai_services = checks.get("ai_stack_services", {})
        if ai_services.get("available"):
            healthy_n, total_n = ai_services["healthy"], ai_services["total"]
            ai_icon = "✅" if healthy_n == total_n else "⚠️" if healthy_n else "❌"
            ai_line = f"\n• **AI-Stack services**: {ai_icon} {healthy_n}/{total_n} healthy"
        else:
            ai_line = ""

        content += f"""• **Docker**: {docker_icon}
• **Services**: {services_icon}
• **GPU**: {gpu_icon}{ai_line}
"""
        
        # Add issues found
        issues_found = []
        recommendations = []
        
        # Check for specific issues
        if not docker_status.get("docker_available"):
            issues_found.append("Docker not available")
            recommendations.append("Install Docker Desktop or check Docker service")
        
        if not docker_status.get("compose_available"):
            issues_found.append("Docker Compose not available") 
            recommendations.append("Install Docker Compose or update Docker Desktop")
        
        if not gpu_status.get("gpu_available"):
            issues_found.append("GPU not available")
            recommendations.append("Check GPU drivers and CUDA installation")
        
        # Add critical issues
        issues_found.extend(critical_issues)
        
        if issues_found:
            content += "\n### ⚠️ Issues Found\n"
            for issue in issues_found:
                content += f"• {issue}\n"
        
        if recommendations:
            content += "\n### 💡 Recommendations\n"
            for rec in recommendations:
                content += f"• {rec}\n"
        
        # Container Environment
        if "container_environment" in checks:
            env = checks["container_environment"]
            content += f"""
### 🐳 Container Environment
- **In Container**: {"✅" if env.get("in_container") else "❌"}
- **OpenWebUI Context**: {"✅" if env.get("openwebui_context") else "❌"}  
- **Host Project Mounted**: {"✅" if env.get("host_project_mounted") else "❌"}
- **GPU Available**: {"✅" if env.get("gpu_available") else "❌"}
"""
            if "torch_version" in env:
                content += f"- **PyTorch Version**: {env['torch_version']}\n"

        # Docker Services
        if "docker_services" in checks:
            docker = checks["docker_services"]
            content += f"""
### 🐳 Docker Services
- **Docker Available**: {"✅" if docker.get("docker_available") else "❌"}"""
            
            if docker.get("docker_available"):
                content += f" (v{docker.get('docker_version', 'Unknown')})"
                content += f"""
- **Docker Compose**: {"✅" if docker.get("compose_available") else "❌"}
- **AI Stack Services**: {"✅" if docker.get("ai_stack_services") == "accessible" else "⚠️" if docker.get("ai_stack_services") == "not_accessible" else "❓"}
"""
                if docker.get("note"):
                    content += f"- **Note**: {docker['note']}\n"
            else:
                content += "\n"
        
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

        # AI Stack Services — per-service health across both compose projects
        if "ai_stack_services" in checks:
            ai = checks["ai_stack_services"]
            content += "\n### 🧩 AI Stack Services\n"
            if not ai.get("available"):
                content += f"_Service probe unavailable — {ai.get('error', 'unknown error')}_\n"
            else:
                content += (
                    f"**{ai['healthy']}/{ai['total']} healthy** "
                    f"({ai['reachable']}/{ai['total']} reachable)\n"
                )
                planes: Dict[str, List[Dict[str, Any]]] = {}
                for svc in ai["services"]:
                    planes.setdefault(svc["plane"], []).append(svc)
                for plane, svcs in planes.items():
                    content += f"\n**{plane}**\n"
                    for svc in svcs:
                        if svc.get("healthy"):
                            icon, detail = "✅", f"HTTP {svc.get('http_code')}"
                        elif svc.get("reachable"):
                            icon, detail = "⚠️", f"HTTP {svc.get('http_code')} (degraded)"
                        else:
                            icon, detail = "❌", svc.get("error", "unreachable")
                        crit = " _(critical)_" if svc.get("critical") else ""
                        content += f"- {icon} `{svc['service']}`{crit} — {detail}\n"

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
        
        # Add GPU details if available
        if gpu_status.get("gpu_available") and gpu_status.get("torch_available"):
            try:
                import torch
                if torch.cuda.is_available():
                    device_count = torch.cuda.device_count()
                    content += f"\n### 🎮 GPU Details\nCUDA Available ({device_count} devices)\n"
            except:
                pass
        
        content += f"\n*Updated: {result_data.get('timestamp', 'Unknown')}*"
        
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