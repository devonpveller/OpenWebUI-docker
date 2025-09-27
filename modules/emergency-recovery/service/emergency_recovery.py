#!/usr/bin/env python3
"""
Emergency Recovery Module - Refactored Architecture

Manifest-driven emergency recovery module implementing the new AI Stack architecture.
Provides comprehensive system recovery capabilities with structured contracts.
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
    return logging.getLogger("emergency_recovery_module")

logger = setup_logging()

class EmergencyRecoveryModule:
    """Emergency Recovery Module implementing manifest-driven architecture"""
    
    def __init__(self):
        self.module_id = "emergency-recovery"
        self.version = "1.0.0-migrated"
        self.config = self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        """Load recovery configuration"""
        return {
            "recovery": {
                "quick_fixes_script": "scripts\\quick-fixes.bat",
                "emergency_recovery_script": "scripts\\emergency-recovery.ps1",
                "container_startup_order": {
                    "phase1_shutdown": ["tailscale", "ollama", "openwebui"],
                    "phase2_cleanup_wait": 15,
                    "phase3_startup": [
                        {
                            "service": "openwebui",
                            "description": "Primary service with GPU passthrough",
                            "wait_healthy": 240,
                            "network_stabilization": 20
                        },
                        {
                            "service": "ollama", 
                            "description": "Depends on OpenWebUI network namespace",
                            "wait_healthy": 60,
                            "dependency": "openwebui"
                        },
                        {
                            "service": "tailscale",
                            "description": "Shares OpenWebUI network namespace",
                            "wait_healthy": 90,
                            "dependency": "openwebui"
                        },
                        {
                            "service": "watchtower",
                            "description": "Independent monitoring service",
                            "dependency": None,
                            "critical": False
                        }
                    ]
                },
                "available_actions": {
                    "namespace": "Network namespace reset (most common fix for connectivity)",
                    "gpu": "GPU availability check and restart", 
                    "status": "System overview and health check",
                    "nuclear": "Complete system restart with proper container ordering",
                    "tailscale": "Standard Tailscale recovery",
                    "advanced": "Advanced PowerShell recovery with 5-phase process"
                },
                "critical_dependencies": {
                    "network_sharing": "Both Ollama and Tailscale use network_mode: service:openwebui",
                    "startup_order": "OpenWebUI must be healthy before dependent services start",
                    "gpu_passthrough": "OpenWebUI and Ollama both require GPU device access",
                    "stabilization_periods": "Network namespace changes require stabilization time"
                }
            }
        }
    
    def analyze_user_input(self, user_input: str) -> Optional[str]:
        """Analyze user input to determine appropriate recovery action"""
        user_input_lower = user_input.lower()
        
        # Keyword mapping for recovery actions
        action_keywords = {
            "namespace": ["network", "connectivity", "unreachable", "tailscale down", "connection"],
            "gpu": ["cuda", "gpu", "graphics", "nvidia", "torch", "reranker"],
            "status": ["status", "health", "check", "overview", "system", "running"],
            "nuclear": ["nuclear", "complete restart", "full restart", "everything broken"],
            "tailscale": ["tailscale", "vpn", "derp", "serve"],
            "advanced": ["advanced", "powershell", "comprehensive"]
        }
        
        # Find the best match
        for action, keywords in action_keywords.items():
            if any(keyword in user_input_lower for keyword in keywords):
                return action
        
        # Default to status check if no specific action detected
        return "status"
    
    def get_container_startup_sequence(self) -> Dict[str, Any]:
        """Get detailed container startup sequence and dependencies"""
        return {
            "startup_sequence": self.config["recovery"]["container_startup_order"],
            "critical_dependencies": self.config["recovery"]["critical_dependencies"],
            "network_architecture": {
                "shared_namespace": "Ollama and Tailscale share OpenWebUI's network namespace",
                "configuration": "network_mode: service:openwebui",
                "implications": [
                    "OpenWebUI container restarts break network access for dependent services",
                    "Dependent services must be restarted after OpenWebUI container changes",
                    "Network namespace requires stabilization time after OpenWebUI starts"
                ]
            },
            "recovery_phases": {
                "1_shutdown": "Reverse dependency order to prevent orphaned connections",
                "2_cleanup": "Allow network namespace cleanup and stabilization",
                "3_startup": "Sequential startup with health checks and stabilization periods",
                "4_verification": "Test connectivity between all services",
                "5_validation": "Verify service functionality and configuration"
            }
        }

    def get_available_recovery_actions(self) -> Dict[str, Any]:
        """Get list of available recovery actions"""
        return {
            "available_actions": self.config["recovery"]["available_actions"],
            "scripts": {
                "quick_fixes": self.config["recovery"]["quick_fixes_script"],
                "advanced_recovery": self.config["recovery"]["emergency_recovery_script"]
            },
            "usage_examples": [
                "Fix network issues",
                "Check system status",
                "GPU recovery", 
                "Complete system restart",
                "Advanced recovery"
            ]
        }
    
    def execute_recovery_action(self, action: str, user_input: str = "") -> Dict[str, Any]:
        """Execute specific recovery action"""
        try:
            action_info = self.config["recovery"]["available_actions"].get(action, "Unknown action")
            
            # Simulate recovery action execution (would need to be adapted for container context)
            result = {
                "action": action,
                "description": action_info,
                "status": "simulated",  # In real implementation, this would execute the actual recovery
                "command_template": self._get_command_template(action),
                "execution_notes": [
                    "Recovery actions should be executed in the host environment",
                    "This module provides guidance and monitoring capabilities",
                    "Actual execution requires proper host access and permissions"
                ]
            }
            
            # Add specific guidance based on action
            if action == "namespace":
                result.update({
                    "immediate_command": "scripts\\quick-fixes.bat namespace",
                    "description_detailed": "Resets network namespace sharing - most common fix for connectivity issues",
                    "root_cause": "OpenWebUI container recreation breaks shared network namespace",
                    "affected_services": ["tailscale", "ollama"],
                    "procedure": [
                        "1. Stop Tailscale (dependent service)",
                        "2. Restart Tailscale to rejoin OpenWebUI network namespace",  
                        "3. Test connectivity and wait for stabilization"
                    ],
                    "success_indicators": [
                        "Tailscale container restarts successfully",
                        "Network connectivity restored between services",
                        "docker compose ps shows all services healthy",
                        "OpenWebUI can reach Ollama via localhost:11434"
                    ],
                    "when_to_use": "When OpenWebUI cannot reach Ollama or Tailscale connectivity fails"
                })
            elif action == "gpu":
                result.update({
                    "immediate_command": "scripts\\quick-fixes.bat gpu",
                    "description_detailed": "Checks and restarts GPU services for CUDA availability",
                    "success_indicators": [
                        "CUDA becomes available in OpenWebUI container",
                        "PyTorch GPU tests pass",
                        "Reranker models use GPU acceleration"
                    ]
                })
            elif action == "nuclear":
                result.update({
                    "immediate_command": "scripts\\emergency-recovery.ps1 -Action recover",
                    "warning": "⚠️ This will restart all containers with proper dependency ordering",
                    "description_detailed": "5-phase emergency recovery with correct container startup sequence",
                    "procedure_phases": [
                        "Phase 1: Graceful shutdown (reverse dependency order: tailscale → ollama → openwebui)",
                        "Phase 2: Network namespace cleanup (15-second stabilization)",
                        "Phase 3: Service restart with proper dependencies",
                        "  - OpenWebUI first (with GPU support, wait for healthy status)",
                        "  - 20-second network namespace stabilization",
                        "  - Ollama (depends on OpenWebUI network, 60s timeout)",
                        "  - Tailscale (shares OpenWebUI network, 90s timeout)",
                        "  - Watchtower (independent service)",
                        "Phase 4: Connectivity verification (25s)",
                        "Phase 5: Service verification and status reporting"
                    ],
                    "critical_dependencies": {
                        "network_sharing": "Ollama and Tailscale both use 'network_mode: service:openwebui'",
                        "startup_order": "OpenWebUI MUST be healthy before dependent services start",
                        "gpu_requirements": "Both OpenWebUI and Ollama require GPU device access"
                    },
                    "prerequisites": [
                        "Backup any important temporary data",
                        "Ensure no critical operations are running",
                        "Verify all volumes are properly mounted",
                        "Confirm GPU drivers and NVIDIA Container Toolkit are available"
                    ]
                })
            elif action == "advanced":
                result.update({
                    "immediate_command": "scripts\\emergency-recovery.ps1 -Action recover",
                    "description_detailed": "Comprehensive 5-phase recovery with health checks and dependency management",
                    "container_dependencies": {
                        "openwebui": "Primary service - provides network namespace for others",
                        "ollama": "Dependent on OpenWebUI network namespace",
                        "tailscale": "Dependent on OpenWebUI network namespace", 
                        "watchtower": "Independent monitoring service"
                    },
                    "health_checks": [
                        "OpenWebUI: HTTP health check + GPU availability test",
                        "Ollama: API version endpoint + model list verification",
                        "Tailscale: VPN status + serve configuration check",
                        "Network: External connectivity + internal service communication"
                    ]
                })
            
            return result
            
        except Exception as e:
            return {
                "action": action,
                "status": "error",
                "error": str(e),
                "fallback_suggestion": "Try manual recovery using emergency-recovery.ps1"
            }
    
    def _get_command_template(self, action: str) -> str:
        """Get command template for action"""
        templates = {
            "namespace": "scripts\\quick-fixes.bat namespace",
            "gpu": "scripts\\quick-fixes.bat gpu",
            "status": "scripts\\quick-fixes.bat status", 
            "nuclear": "scripts\\emergency-recovery.ps1 -Action recover",
            "tailscale": "scripts\\emergency-recovery.ps1 -Action recover",
            "advanced": "scripts\\emergency-recovery.ps1 -Action recover"
        }
        return templates.get(action, "scripts\\quick-fixes.bat status")

    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {
            "module_id": self.module_id,
            "version": self.version,
            "name": "Emergency Recovery System",
            "capabilities": ["system_recovery", "automated_repair", "diagnostics"],
            "status": "ready"
        }
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        health_score = 100
        issues = []
        
        # Check if recovery scripts exist (in actual container deployment)
        recovery_paths = [
            self.config["recovery"]["quick_fixes_script"],
            self.config["recovery"]["emergency_recovery_script"]
        ]
        
        for script_path in recovery_paths:
            # In container environment, these would be checked differently
            if not Path(f"/host_scripts/{script_path}").exists():
                health_score -= 25
                issues.append(f"Recovery script not accessible: {script_path}")
        
        status = "healthy" if health_score >= 75 else "degraded" if health_score >= 50 else "unhealthy"
        
        return {
            "module_id": self.module_id,
            "status": status,
            "health_score": health_score,
            "issues": issues,
            "capabilities": ["emergency_recovery", "system_repair"]
        }
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute emergency recovery with migrated functionality"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Parse input - handle both string and structured input
            input_data = request_data.get("input", "")
            if isinstance(input_data, dict):
                user_input = input_data.get("query", "")
            else:
                user_input = str(input_data)
            
            # If no specific input, show available actions
            if not user_input.strip():
                result_data = self.get_available_recovery_actions()
                content = self._format_available_actions(result_data)
            else:
                # Analyze input and execute appropriate recovery
                action = self.analyze_user_input(user_input)
                result_data = self.execute_recovery_action(action, user_input)
                content = self._format_recovery_result(result_data)
            
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
            logger.error(f"❌ Emergency recovery execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **Emergency Recovery Error**: {str(e)}",
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _format_available_actions(self, result_data: Dict[str, Any]) -> str:
        """Format available actions for display"""
        actions = result_data.get("available_actions", {})
        scripts = result_data.get("scripts", {})
        examples = result_data.get("usage_examples", [])
        
        content = """## 🚨 Emergency Recovery System

### Available Recovery Actions:
"""
        
        for action, description in actions.items():
            content += f"**{action.title()}**: {description}\n"
        
        content += f"""
### Recovery Scripts:
- **Quick Fixes**: `{scripts.get('quick_fixes', 'N/A')}`
- **Advanced Recovery**: `{scripts.get('advanced_recovery', 'N/A')}`

### Usage Examples:
"""
        for example in examples:
            content += f"- \"{example}\"\n"
        
        content += "\n*Describe your issue to get specific recovery guidance*"
        
        return content
    
    def _format_recovery_result(self, result_data: Dict[str, Any]) -> str:
        """Format recovery action result for display"""
        action = result_data.get("action", "unknown")
        status = result_data.get("status", "unknown")
        description = result_data.get("description", "No description")
        
        content = f"""## 🔧 Recovery Action: {action.title()}

**Status**: {status.title()}
**Description**: {description}
"""
        
        if "immediate_command" in result_data:
            content += f"\n### 🚀 Immediate Action\n```\n{result_data['immediate_command']}\n```\n"
        
        if "description_detailed" in result_data:
            content += f"\n**Details**: {result_data['description_detailed']}\n"
        
        if "warning" in result_data:
            content += f"\n### ⚠️ Warning\n{result_data['warning']}\n"
        
        if "prerequisites" in result_data:
            content += "\n### 📋 Prerequisites:\n"
            for prereq in result_data["prerequisites"]:
                content += f"- {prereq}\n"
        
        if "success_indicators" in result_data:
            content += "\n### ✅ Success Indicators:\n"
            for indicator in result_data["success_indicators"]:
                content += f"- {indicator}\n"
        
        if "execution_notes" in result_data:
            content += "\n### 📝 Execution Notes:\n"
            for note in result_data["execution_notes"]:
                content += f"- {note}\n"
        
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
recovery_module = EmergencyRecoveryModule()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return recovery_module.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return recovery_module.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""
    return recovery_module.health()

def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate input"""
    return recovery_module.validate(input_data)

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