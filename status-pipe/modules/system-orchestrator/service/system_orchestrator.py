#!/usr/bin/env python3
"""
System Orchestrator Module - Overall System Health Coordination

This module coordinates all other modules to provide comprehensive system health reporting.
It tests each module independently and provides an overall system status.
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
    return logging.getLogger("system_orchestrator")

logger = setup_logging()

class SystemOrchestratorModule:
    """System Orchestrator Module - coordinates all other modules for comprehensive health checks"""
    
    def __init__(self):
        self.module_id = "system-orchestrator"
        self.version = "1.0.0"
        self.modules_dir = Path(__file__).parent.parent.parent  # Points to modules/ directory
        self.python_executable = self._get_python_executable()
        
    def _get_python_executable(self) -> str:
        """Get the appropriate Python executable"""
        # Use current interpreter that's working
        return sys.executable
    
    def _get_available_modules(self) -> Dict[str, Dict[str, Any]]:
        """Discover all available modules in the modules directory"""
        available_modules = {}
        
        if not self.modules_dir.exists():
            logger.warning(f"Modules directory not found: {self.modules_dir}")
            return available_modules
        
        for module_dir in self.modules_dir.iterdir():
            if not module_dir.is_dir():
                continue
                
            module_name = module_dir.name
            service_file = module_dir / "service" / f"{module_name.replace('-', '_')}.py"
            manifest_file = module_dir / "module.manifest.json"
            
            # Skip system-orchestrator (self)
            if module_name == "system-orchestrator":
                continue
            
            module_info = {
                "name": module_name,
                "service_file": service_file,
                "manifest_file": manifest_file,
                "available": service_file.exists(),
                "has_manifest": manifest_file.exists()
            }
            
            # Load manifest if available
            if manifest_file.exists():
                try:
                    with open(manifest_file, 'r') as f:
                        manifest = json.load(f)
                        module_info["description"] = manifest.get("description", "No description")
                        module_info["capabilities"] = manifest.get("capabilities", [])
                except Exception as e:
                    logger.warning(f"Failed to load manifest for {module_name}: {e}")
                    module_info["description"] = "Failed to load description"
                    module_info["capabilities"] = []
            
            available_modules[module_name] = module_info
        
        return available_modules
    
    def _test_module(self, module_name: str, module_info: Dict[str, Any]) -> Dict[str, Any]:
        """Test a single module's functionality"""
        test_result = {
            "module_name": module_name,
            "status": "unknown",
            "available": module_info.get("available", False),
            "response_time_ms": 0,
            "error": None,
            "response": None
        }
        
        if not module_info.get("available", False):
            test_result["status"] = "unavailable"
            test_result["error"] = "Service file not found"
            return test_result
        
        service_file = module_info["service_file"]
        
        try:
            start_time = time.time()
            
            # Create test payload
            test_payload = {
                "request_id": f"orchestrator-test-{int(time.time())}",
                "input": "health check"
            }
            
            # Execute module with test payload
            # Set environment variables to handle Unicode properly
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            
            result = subprocess.run(
                [self.python_executable, str(service_file)],
                input=json.dumps(test_payload),
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
                cwd=self.modules_dir.parent,  # Set working directory to ai-stack root
                env=env,  # Pass environment with UTF-8 encoding
                encoding='utf-8',
                errors='replace'  # Replace problematic characters instead of failing
            )
            
            execution_time = int((time.time() - start_time) * 1000)
            test_result["response_time_ms"] = execution_time
            
            if result.returncode == 0:
                try:
                    response_data = json.loads(result.stdout)
                    test_result["status"] = "healthy"
                    test_result["response"] = {
                        "module_id": response_data.get("module_id", module_name),
                        "status": response_data.get("status", "unknown"),
                        "content_length": len(response_data.get("content", "")),
                        "has_structured_data": "structured_data" in response_data
                    }
                except json.JSONDecodeError as e:
                    test_result["status"] = "response_error"
                    test_result["error"] = f"Invalid JSON response: {str(e)}"
                    test_result["response"] = {"raw_stdout": result.stdout[:200]}
            else:
                test_result["status"] = "execution_error"
                stderr_msg = result.stderr[:200] if result.stderr else "No stderr"
                stdout_msg = result.stdout[:200] if result.stdout else "No stdout"
                test_result["error"] = f"Exit code {result.returncode}: stderr='{stderr_msg}' stdout='{stdout_msg}'"
                
        except subprocess.TimeoutExpired:
            test_result["status"] = "timeout"
            test_result["error"] = "Module execution timed out (30s)"
        except Exception as e:
            test_result["status"] = "error"
            test_result["error"] = f"Test execution failed: {str(e)}"
        
        return test_result
    
    def run_comprehensive_health_check(self) -> Dict[str, Any]:
        """Run comprehensive health check across all modules"""
        logger.info("Starting comprehensive system health check...")
        
        start_time = time.time()
        available_modules = self._get_available_modules()
        
        health_report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "orchestrator_version": self.version,
            "total_modules": len(available_modules),
            "modules_tested": 0,
            "modules_healthy": 0,
            "modules_degraded": 0,
            "modules_failed": 0,
            "overall_status": "unknown",
            "overall_health_score": 0,
            "module_results": {},
            "summary": {},
            "recommendations": []
        }
        
        # Test each module
        for module_name, module_info in available_modules.items():
            logger.info(f"Testing module: {module_name}")
            test_result = self._test_module(module_name, module_info)
            health_report["module_results"][module_name] = test_result
            health_report["modules_tested"] += 1
            
            # Update counters based on test results
            if test_result["status"] == "healthy":
                health_report["modules_healthy"] += 1
            elif test_result["status"] in ["response_error", "timeout"]:
                health_report["modules_degraded"] += 1
            else:
                health_report["modules_failed"] += 1
        
        # Calculate overall health score
        if health_report["modules_tested"] > 0:
            healthy_weight = 100
            degraded_weight = 50
            failed_weight = 0
            
            total_score = (
                health_report["modules_healthy"] * healthy_weight +
                health_report["modules_degraded"] * degraded_weight +
                health_report["modules_failed"] * failed_weight
            )
            
            max_possible_score = health_report["modules_tested"] * healthy_weight
            health_report["overall_health_score"] = int((total_score / max_possible_score) * 100)
        
        # Determine overall status
        if health_report["overall_health_score"] >= 90:
            health_report["overall_status"] = "excellent"
        elif health_report["overall_health_score"] >= 75:
            health_report["overall_status"] = "good"
        elif health_report["overall_health_score"] >= 50:
            health_report["overall_status"] = "degraded"
        else:
            health_report["overall_status"] = "critical"
        
        # Generate summary
        health_report["summary"] = {
            "status_emoji": self._get_status_emoji(health_report["overall_status"]),
            "modules_overview": f"{health_report['modules_healthy']} healthy, {health_report['modules_degraded']} degraded, {health_report['modules_failed']} failed",
            "execution_time_ms": int((time.time() - start_time) * 1000),
            "python_executable": self.python_executable
        }
        
        # Generate recommendations
        if health_report["modules_failed"] > 0:
            health_report["recommendations"].append("Some modules failed to execute - check logs for details")
        
        if health_report["modules_degraded"] > 0:
            health_report["recommendations"].append("Some modules have response issues - verify configurations")
        
        if health_report["overall_health_score"] < 75:
            health_report["recommendations"].append("Overall system health is below optimal - consider running recovery procedures")
        
        logger.info(f"Health check completed in {health_report['summary']['execution_time_ms']}ms")
        return health_report
    
    def _get_status_emoji(self, status: str) -> str:
        """Get emoji for status"""
        emoji_map = {
            "excellent": "🟢",
            "good": "🟡", 
            "degraded": "🟠",
            "critical": "🔴",
            "unknown": "❓"
        }
        return emoji_map.get(status, "❓")
    
    def format_health_report(self, health_data: Dict[str, Any]) -> str:
        """Format health report for display"""
        content = f"""## 🏥 Comprehensive System Health Report

**Overall Status**: {health_data['summary']['status_emoji']} {health_data['overall_status'].title()} ({health_data['overall_health_score']}/100)
**Modules Tested**: {health_data['modules_tested']}
**Results**: {health_data['summary']['modules_overview']}
**Execution Time**: {health_data['summary']['execution_time_ms']}ms

### 📊 Module Health Status
"""
        
        # Sort modules by health status for better display
        sorted_modules = sorted(
            health_data['module_results'].items(),
            key=lambda x: {"healthy": 0, "degraded": 1, "error": 2, "timeout": 3}.get(x[1]["status"], 4)
        )
        
        for module_name, result in sorted_modules:
            status_emoji = "✅" if result["status"] == "healthy" else "⚠️" if result["status"] in ["degraded", "response_error", "timeout"] else "❌"
            content += f"- {status_emoji} **{module_name}**: {result['status'].title()}"
            
            if result["response_time_ms"] > 0:
                content += f" ({result['response_time_ms']}ms)"
            
            if result.get("error"):
                content += f" - {result['error'][:50]}{'...' if len(result['error']) > 50 else ''}"
            
            content += "\n"
        
        if health_data.get("recommendations"):
            content += "\n### 💡 Recommendations\n"
            for rec in health_data["recommendations"]:
                content += f"- {rec}\n"
        
        content += f"\n*Report generated: {health_data['timestamp']}*"
        
        return content
    
    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {
            "module_id": self.module_id,
            "version": self.version,
            "name": "System Orchestrator",
            "capabilities": ["comprehensive_health_check", "module_coordination", "system_overview"],
            "status": "ready"
        }
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        health_score = 100
        issues = []
        
        # Check if modules directory exists
        if not self.modules_dir.exists():
            health_score -= 50
            issues.append(f"Modules directory not found: {self.modules_dir}")
        
        # Check Python executable
        try:
            result = subprocess.run([self.python_executable, "--version"], capture_output=True, timeout=5)
            if result.returncode != 0:
                health_score -= 25
                issues.append("Python executable not working properly")
        except Exception:
            health_score -= 25
            issues.append("Cannot verify Python executable")
        
        status = "healthy" if health_score >= 75 else "degraded" if health_score >= 50 else "unhealthy"
        
        return {
            "module_id": self.module_id,
            "status": status,
            "health_score": health_score,
            "issues": issues,
            "capabilities": ["orchestration", "health_coordination"]
        }
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute system orchestrator with comprehensive health check"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Run comprehensive health check
            health_data = self.run_comprehensive_health_check()
            
            # Format for display
            content = self.format_health_report(health_data)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok",
                "content": content,
                "structured_data": health_data,
                "diagnostics": {
                    "execution_time_ms": execution_time,
                    "orchestrator_version": self.version
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ System orchestrator execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **System Orchestrator Error**: {str(e)}",
                "error": {
                    "code": "ORCHESTRATION_ERROR",
                    "message": str(e),
                    "retriable": True
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

# Create module instance
orchestrator = SystemOrchestratorModule()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return orchestrator.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return orchestrator.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""  
    return orchestrator.health()

if __name__ == "__main__":
    from pathlib import Path as _Path
    import sys as _sys
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
    from utilities.module_cli import run_module_cli
    run_module_cli(main, describe, health)
