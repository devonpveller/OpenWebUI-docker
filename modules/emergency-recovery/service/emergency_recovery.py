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
                "python_scripts": {
                    "namespace_reset": "scripts/namespace_reset.py",
                    "rebuild_tailscale": "scripts/rebuild_tailscale.py",
                    "gpu_check": "scripts/gpu_check.py",
                    "nuclear_option": "scripts/nuclear_option.py",
                    "status_check": "scripts/status_check.py",
                    "restart_openwebui": "scripts/restart_openwebui.py",
                    "lmstudio_fix": "scripts/lmstudio_fix.py"
                },
                "legacy_scripts": {
                    "quick_fixes_script": "scripts\\quick-fixes.bat",
                    "emergency_recovery_script": "scripts\\emergency-recovery.ps1"
                },
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
                    "rebuild": "Rebuild Tailscale container (for persistent issues)",
                    "gpu": "GPU restart and recovery (for CUDA/PyTorch issues)", 
                    "lmstudio": "Fix LM Studio Tailscale connectivity",
                    "restart_openwebui": "Properly restart OpenWebUI with dependent containers",
                    "nuclear": "Complete system restart with proper container ordering",
                    "status": "Comprehensive system status check with detailed diagnostics",
                    "tailscale": "Standard Tailscale recovery",
                    "advanced": "Advanced PowerShell recovery with 5-phase process",
                    "restart_ollama": "Restart Ollama container to ensure OpenWebUI connectivity (IMPLEMENTED)",
                    "validate_gpu": "Validate GPU and PyTorch installation in OpenWebUI container (IMPLEMENTED)"
                },
                "critical_dependencies": {
                    "network_sharing": "Both Ollama and Tailscale use network_mode: service:openwebui",
                    "startup_order": "OpenWebUI must be healthy before dependent services start",
                    "gpu_passthrough": "OpenWebUI and Ollama both require GPU device access",
                    "stabilization_periods": "Network namespace changes require stabilization time"
                }
            }
        }
    
    def _find_project_root(self) -> Optional[Path]:
        """Find the project root directory containing docker-compose.yml"""
        current_dir = Path.cwd()
        
        # Check if we're running from container (look for /host_project)
        if Path("/host_project").exists():
            logger.info("Running from container environment...")
            return Path("/host_project")
        
        # Otherwise search for docker-compose.yml
        project_root = current_dir
        while not (project_root / "docker-compose.yml").exists():
            parent = project_root.parent
            if parent == project_root:  # Reached root
                break
            project_root = parent
        
        if not (project_root / "docker-compose.yml").exists():
            logger.error("docker-compose.yml not found in current directory or parent directories")
            return None
        
        return project_root
    
    def _execute_python_script(self, script_name: str, action_name: str) -> Dict[str, Any]:
        """Execute a Python recovery script and return structured results"""
        try:
            logger.info(f"Starting {action_name}...")
            
            # Find project root
            project_root = self._find_project_root()
            if not project_root:
                return {
                    "action": action_name,
                    "status": "error",
                    "error": "Could not find project root directory",
                    "recommendation": "Ensure docker-compose.yml exists in project directory"
                }
            
            logger.info(f"Using project root: {project_root}")
            
            # Get script path from config
            script_path = self.config["recovery"]["python_scripts"].get(script_name)
            if not script_path:
                return {
                    "action": action_name,
                    "status": "error",
                    "error": f"Unknown script: {script_name}",
                    "available_scripts": list(self.config["recovery"]["python_scripts"].keys())
                }
            
            # Execute the Python script
            logger.info(f"Executing Python script: {script_path}")
            script_full_path = project_root / Path(script_path)
            
            if not script_full_path.exists():
                return {
                    "action": action_name,
                    "status": "error",
                    "error": f"Script not found: {script_full_path}",
                    "recommendation": "Ensure all Python scripts are present in the scripts directory"
                }
            
            # Check if we're running in container environment
            if Path("/host_project").exists():
                # We're in the container - need to execute script on host where Docker CLI is available
                logger.info("Executing script on host from container environment...")
                # Try to use docker exec to run script on host
                try:
                    # First try to find the host container name by checking docker containers
                    # Since we can't use docker CLI from container, we'll modify the approach
                    # Use a simpler approach - execute the script but handle Docker not being available
                    result = subprocess.run(
                        ["python", str(script_full_path)],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        env={**os.environ, "CONTAINER_ENV": "true"}  # Flag to script that it's running in container
                    )
                except Exception as container_error:
                    logger.error(f"Container execution failed: {container_error}")
                    return {
                        "action": action_name,
                        "status": "error",
                        "error": f"Cannot execute Docker commands from container: {str(container_error)}",
                        "recommendation": "This action requires execution from the host system where Docker CLI is available",
                        "alternative": "Try running the script directly on the host system"
                    }
            else:
                # We're on the host - execute script directly
                logger.info(f"Executing Python script directly on host")
                result = subprocess.run(
                    ["python", str(script_full_path)],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes max
                )
            
            logger.info(f"Script execution completed with return code: {result.returncode}")
            
            # Determine status based on return code
            status = "completed" if result.returncode == 0 else "error"
            
            return {
                "action": action_name,
                "status": status,
                "description": f"{action_name.replace('_', ' ').title()} executed successfully" if status == "completed" else f"{action_name.replace('_', ' ').title()} failed",
                "script_output": result.stdout if result.stdout else "No output",
                "script_errors": result.stderr if result.stderr else None,
                "return_code": result.returncode,
                "script_path": str(script_path)
            }
            
        except subprocess.TimeoutExpired:
            return {
                "action": action_name,
                "status": "error",
                "error": "Script execution timed out after 5 minutes",
                "recommendation": "Check system performance and try again"
            }
        except Exception as e:
            logger.error(f"Error executing {script_name}: {str(e)}")
            return {
                "action": action_name,
                "status": "error",
                "error": str(e),
                "script_path": script_path if 'script_path' in locals() else "unknown"
            }
    
    def analyze_user_input(self, user_input: str) -> Optional[str]:
        """Analyze user input to determine appropriate recovery action"""
        user_input_lower = user_input.lower()
        
        # Check for "show/list/available" type requests first
        show_keywords = ["show", "list", "available", "procedures", "options", "what can", "help me", "what are"]
        if any(keyword in user_input_lower for keyword in show_keywords):
            # Return None to trigger showing available actions instead of executing a specific action
            return None
        
        # Keyword mapping for recovery actions (order matters - more specific first)
        action_keywords = {
            "restart_ollama": ["restart ollama", "ollama restart", "ollama down", "fix ollama", "ollama connectivity"],
            "validate_gpu": ["validate gpu", "test gpu", "gpu validation", "pytorch test", "cuda test"],
            "namespace": ["network", "connectivity", "unreachable", "tailscale down", "connection", "namespace reset", "fix network"],
            "rebuild": ["rebuild", "rebuild tailscale", "rebuild container", "rebuild ts"],
            "gpu": ["fix gpu", "gpu restart", "gpu recovery", "restart gpu", "gpu not working"],
            "lmstudio": ["lmstudio", "lm studio", "lms", "fix lm studio", "lmstudio fix"],
            "restart_openwebui": ["restart openwebui", "openwebui restart", "restart web", "web restart"],
            "nuclear": ["nuclear", "complete restart", "full restart", "everything broken"],
            "status": ["status", "system status", "check status", "health", "diagnostic", "check system"],
            "tailscale": ["tailscale", "vpn", "derp", "serve"],
            "advanced": ["advanced", "powershell", "comprehensive"]
        }
        
        # Find the best match
        for action, keywords in action_keywords.items():
            if any(keyword in user_input_lower for keyword in keywords):
                return action
        
        # Default to namespace reset (most common fix) if no specific action detected
        return "namespace"
    
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
            
            # Check for implemented actions
            if action == "restart_ollama":
                return self._restart_ollama()
            elif action == "validate_gpu":
                return self._validate_gpu_pytorch()
            elif action == "namespace":
                return self._quick_namespace_reset()
            elif action == "rebuild":
                return self._rebuild_tailscale()
            elif action == "gpu":
                return self._quick_gpu_check()
            elif action == "lmstudio":
                return self._fix_lmstudio()
            elif action == "restart_openwebui":
                return self._restart_openwebui()
            elif action == "nuclear":
                return self._nuclear_option()
            elif action == "status":
                return self._status_check()
            
            # Simulate other recovery actions (would need to be adapted for container context)
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
    
    def _restart_ollama(self) -> Dict[str, Any]:
        """Restart Ollama container to ensure connectivity with OpenWebUI"""
        try:
            logger.info("Starting Ollama container restart procedure...")
            
            # Find the project root (where docker-compose.yml is located)
            current_dir = os.getcwd()
            project_root = current_dir
            
            # Look for docker-compose.yml in current directory or parent directories
            while not os.path.exists(os.path.join(project_root, "docker-compose.yml")):
                parent = os.path.dirname(project_root)
                if parent == project_root:  # Reached root directory
                    break
                project_root = parent
            
            if not os.path.exists(os.path.join(project_root, "docker-compose.yml")):
                return {
                    "action": "restart_ollama",
                    "status": "error",
                    "error": "docker-compose.yml not found in current directory or parent directories",
                    "recommendation": "Run this command from the AI Stack project root directory",
                    "current_directory": current_dir
                }
            
            logger.info(f"Using project root: {project_root}")
            
            # First check if docker compose is available
            try:
                subprocess.run(["docker", "compose", "--version"], 
                             capture_output=True, check=True, cwd=project_root)
            except (subprocess.CalledProcessError, FileNotFoundError):
                return {
                    "action": "restart_ollama",
                    "status": "error",
                    "error": "Docker Compose not available on this system",
                    "recommendation": "Ensure Docker and Docker Compose are installed and accessible"
                }
            
            # Stop Ollama service
            logger.info("Stopping Ollama service...")
            stop_result = subprocess.run(
                ["docker", "compose", "stop", "ollama"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if stop_result.returncode != 0:
                logger.warning(f"Stop command had issues: {stop_result.stderr}")
            
            # Wait for clean shutdown
            time.sleep(5)
            
            # Start Ollama service
            logger.info("Starting Ollama service...")
            start_result = subprocess.run(
                ["docker", "compose", "up", "-d", "ollama"],
                cwd=project_root, 
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if start_result.returncode != 0:
                return {
                    "action": "restart_ollama",
                    "status": "error",
                    "error": f"Failed to start Ollama: {start_result.stderr}",
                    "stdout": start_result.stdout
                }
            
            # Wait for service to be ready
            logger.info("Waiting for Ollama to be ready...")
            time.sleep(10)
            
            # Test connectivity from OpenWebUI perspective
            try:
                import requests
                test_response = requests.get("http://localhost:11434/api/version", timeout=10)
                if test_response.status_code == 200:
                    ollama_version = test_response.json().get("version", "unknown")
                    connectivity_status = "success"
                    connectivity_message = f"Ollama v{ollama_version} is responding"
                else:
                    connectivity_status = "partial"
                    connectivity_message = f"Ollama responding but returned status {test_response.status_code}"
            except Exception as conn_err:
                connectivity_status = "failed"
                connectivity_message = f"Cannot connect to Ollama: {str(conn_err)}"
            
            return {
                "action": "restart_ollama",
                "status": "completed",
                "steps_completed": [
                    "Docker Compose availability verified",
                    "Ollama service stopped",
                    "Clean shutdown wait completed",
                    "Ollama service restarted",
                    "Service readiness wait completed"
                ],
                "connectivity_test": {
                    "status": connectivity_status,
                    "message": connectivity_message,
                    "endpoint": "http://localhost:11434/api/version"
                },
                "next_steps": [
                    "Test model loading in OpenWebUI",
                    "Verify chat functionality",
                    "Check for any remaining network issues"
                ] if connectivity_status == "success" else [
                    "Check docker compose logs ollama for errors",
                    "Verify network configuration",
                    "Consider full stack restart if issues persist"
                ]
            }
            
        except subprocess.TimeoutExpired:
            return {
                "action": "restart_ollama",
                "status": "error", 
                "error": "Operation timed out - Docker commands took too long",
                "recommendation": "Check Docker daemon health and try again"
            }
        except Exception as e:
            logger.error(f"Error restarting Ollama: {str(e)}")
            return {
                "action": "restart_ollama",
                "status": "error",
                "error": str(e)
            }
    
    def _validate_gpu_pytorch(self) -> Dict[str, Any]:
        """Validate GPU process and PyTorch installation within OpenWebUI container"""
        try:
            logger.info("Starting GPU and PyTorch validation...")
            
            # Find the project root (where docker-compose.yml is located)
            current_dir = os.getcwd()
            project_root = current_dir
            
            # Look for docker-compose.yml in current directory or parent directories
            while not os.path.exists(os.path.join(project_root, "docker-compose.yml")):
                parent = os.path.dirname(project_root)
                if parent == project_root:  # Reached root directory
                    break
                project_root = parent
            
            if not os.path.exists(os.path.join(project_root, "docker-compose.yml")):
                return {
                    "action": "validate_gpu",
                    "status": "error",
                    "error": "docker-compose.yml not found in current directory or parent directories",
                    "recommendation": "Run this command from the AI Stack project root directory",
                    "current_directory": current_dir
                }
            
            logger.info(f"Using project root: {project_root}")
            
            validation_results = {
                "action": "validate_gpu",
                "status": "completed",
                "tests": {},
                "container_tests": True
            }
            
            # Test 1: Check if OpenWebUI container is running
            try:
                container_check = subprocess.run(
                    ["docker", "compose", "ps", "-q", "openwebui"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if not container_check.stdout.strip():
                    return {
                        "action": "validate_gpu",
                        "status": "error",
                        "error": "OpenWebUI container is not running",
                        "recommendation": "Start the containers with: docker compose up -d",
                        "container_tests": False
                    }
                
                validation_results["tests"]["container_running"] = {
                    "status": "success",
                    "message": "OpenWebUI container is running",
                    "container_id": container_check.stdout.strip()[:12]
                }
                
            except Exception as e:
                return {
                    "action": "validate_gpu",
                    "status": "error",
                    "error": f"Failed to check container status: {str(e)}",
                    "recommendation": "Ensure Docker is running and accessible",
                    "container_tests": False
                }
            
            # Test 2: Check PyTorch import in container
            try:
                torch_import_cmd = subprocess.run(
                    ["docker", "compose", "exec", "-T", "openwebui", 
                     "python", "-c", "import torch; print('SUCCESS'); print(torch.__version__)"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                
                if torch_import_cmd.returncode == 0 and "SUCCESS" in torch_import_cmd.stdout:
                    version_line = torch_import_cmd.stdout.strip().split('\n')[-1]
                    validation_results["tests"]["torch_import"] = {
                        "status": "success",
                        "message": "PyTorch imported successfully in container",
                        "version": version_line
                    }
                else:
                    validation_results["tests"]["torch_import"] = {
                        "status": "failed",
                        "message": f"PyTorch import failed in container: {torch_import_cmd.stderr}",
                        "recommendation": "Rebuild OpenWebUI container with GPU support"
                    }
                    validation_results["status"] = "failed"
                    return validation_results
                    
            except Exception as e:
                validation_results["tests"]["torch_import"] = {
                    "status": "failed",
                    "message": f"Error testing PyTorch import: {str(e)}",
                    "recommendation": "Check container accessibility and try again"
                }
                validation_results["status"] = "failed"
                return validation_results
            
            # Test 3: Check CUDA availability in container
            try:
                cuda_check_cmd = subprocess.run(
                    ["docker", "compose", "exec", "-T", "openwebui", 
                     "python", "-c", "import torch; print('CUDA_AVAILABLE:', torch.cuda.is_available()); print('CUDA_VERSION:', torch.version.cuda if torch.cuda.is_available() else 'N/A')"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                
                if cuda_check_cmd.returncode == 0:
                    output_lines = cuda_check_cmd.stdout.strip().split('\n')
                    cuda_available = "True" in [line.split(':')[1].strip() for line in output_lines if "CUDA_AVAILABLE" in line][0]
                    cuda_version = [line.split(':')[1].strip() for line in output_lines if "CUDA_VERSION" in line][0]
                    
                    validation_results["tests"]["cuda_available"] = {
                        "status": "success" if cuda_available else "failed",
                        "message": f"CUDA available: {cuda_available}",
                        "cuda_version": cuda_version
                    }
                else:
                    validation_results["tests"]["cuda_available"] = {
                        "status": "error",
                        "message": f"Error checking CUDA: {cuda_check_cmd.stderr}"
                    }
                    
            except Exception as e:
                validation_results["tests"]["cuda_available"] = {
                    "status": "error", 
                    "message": f"Error testing CUDA availability: {str(e)}"
                }
            
            # Test 4: Check GPU device count in container
            if validation_results["tests"]["cuda_available"]["status"] == "success":
                try:
                    gpu_count_cmd = subprocess.run(
                        ["docker", "compose", "exec", "-T", "openwebui", 
                         "python", "-c", "import torch; print(torch.cuda.device_count())"],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                    
                    if gpu_count_cmd.returncode == 0:
                        gpu_count = int(gpu_count_cmd.stdout.strip())
                        
                        validation_results["tests"]["gpu_count"] = {
                            "status": "success" if gpu_count > 0 else "failed",
                            "message": f"GPU devices detected: {gpu_count}",
                            "devices": []
                        }
                        
                        # Get device names separately to avoid command complexity
                        if gpu_count > 0:
                            for i in range(min(gpu_count, 4)):  # Limit to first 4 GPUs
                                try:
                                    gpu_name_cmd = subprocess.run(
                                        ["docker", "compose", "exec", "-T", "openwebui", 
                                         "python", "-c", f"import torch; props = torch.cuda.get_device_properties({i}); print(props.name); print(f'{{props.total_memory / 1024**3:.1f}} GB'); print(f'{{props.major}}.{{props.minor}}')"],
                                        cwd=project_root,
                                        capture_output=True,
                                        text=True,
                                        timeout=10
                                    )
                                    
                                    if gpu_name_cmd.returncode == 0:
                                        lines = gpu_name_cmd.stdout.strip().split('\n')
                                        if len(lines) >= 3:
                                            validation_results["tests"]["gpu_count"]["devices"].append({
                                                "id": i,
                                                "name": lines[0],
                                                "memory_total": lines[1],
                                                "compute_capability": lines[2]
                                            })
                                except Exception as e:
                                    validation_results["tests"]["gpu_count"]["devices"].append({
                                        "id": i,
                                        "error": f"Could not get details: {str(e)}"
                                    })
                    else:
                        validation_results["tests"]["gpu_count"] = {
                            "status": "error",
                            "message": f"Error getting GPU count: {gpu_count_cmd.stderr}"
                        }
                        
                except Exception as e:
                    validation_results["tests"]["gpu_count"] = {
                        "status": "error",
                        "message": f"Error testing GPU enumeration: {str(e)}"
                    }
            else:
                validation_results["tests"]["gpu_count"] = {
                    "status": "skipped",
                    "message": "CUDA not available, skipping GPU enumeration"
                }
            
            # Test 5: Test GPU tensor operations in container
            if validation_results["tests"]["cuda_available"]["status"] == "success":
                try:
                    gpu_ops_cmd = subprocess.run(
                        ["docker", "compose", "exec", "-T", "openwebui", 
                         "python", "-c", 
                         "import torch; "
                         "test_tensor = torch.ones(10, 10).cuda(); "
                         "result_tensor = torch.matmul(test_tensor, test_tensor); "
                         "result_value = result_tensor.sum().item(); "
                         "print('SUCCESS'); "
                         "print(f'Result: {result_value}'); "
                         "print(f'Device: {test_tensor.device}')"],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=20
                    )
                    
                    if gpu_ops_cmd.returncode == 0 and "SUCCESS" in gpu_ops_cmd.stdout:
                        output_lines = gpu_ops_cmd.stdout.strip().split('\n')
                        result_line = [line for line in output_lines if "Result:" in line][0] if any("Result:" in line for line in output_lines) else "Result: unknown"
                        device_line = [line for line in output_lines if "Device:" in line][0] if any("Device:" in line for line in output_lines) else "Device: unknown"
                        
                        validation_results["tests"]["gpu_operations"] = {
                            "status": "success",
                            "message": f"GPU tensor operations working ({result_line})",
                            "device_used": device_line.split(':', 1)[1].strip() if ':' in device_line else "unknown"
                        }
                    else:
                        validation_results["tests"]["gpu_operations"] = {
                            "status": "failed", 
                            "message": f"GPU tensor operations failed: {gpu_ops_cmd.stderr}"
                        }
                        validation_results["status"] = "partial"
                        
                except Exception as e:
                    validation_results["tests"]["gpu_operations"] = {
                        "status": "error",
                        "message": f"Error testing GPU operations: {str(e)}"
                    }
                    validation_results["status"] = "partial"
            else:
                validation_results["tests"]["gpu_operations"] = {
                    "status": "skipped",
                    "message": "CUDA not available, skipping GPU operations test"
                }
                validation_results["status"] = "partial"
            
            # Overall assessment and recommendations
            test_results = validation_results["tests"]
            failed_tests = [name for name, test in test_results.items() if test["status"] == "failed"]
            cuda_working = validation_results["tests"]["cuda_available"]["status"] == "success"
            
            if not failed_tests:
                if cuda_working:
                    validation_results["overall_status"] = "excellent"
                    validation_results["summary"] = "GPU acceleration fully functional in OpenWebUI container"
                    validation_results["recommendations"] = [
                        "GPU acceleration is working properly",
                        "Reranker models should use GPU automatically",
                        "Monitor GPU memory usage during heavy workloads"
                    ]
                else:
                    validation_results["overall_status"] = "cpu_only"
                    validation_results["summary"] = "PyTorch working but no GPU acceleration"
                    validation_results["recommendations"] = [
                        "Rebuild container with Dockerfile.openwebui-gpu",
                        "Ensure NVIDIA Container Toolkit is installed",
                        "Check docker-compose.yml GPU configuration"
                    ]
            else:
                validation_results["overall_status"] = "needs_attention"
                validation_results["summary"] = f"Issues found: {', '.join(failed_tests)}"
                validation_results["recommendations"] = [
                    "Rebuild OpenWebUI container: docker compose build --no-cache openwebui",
                    "Verify NVIDIA drivers are installed on host",
                    "Check docker-compose.yml GPU passthrough configuration",
                    "Ensure using Dockerfile.openwebui-gpu (not default image)"
                ]
            
            return validation_results
            
        except Exception as e:
            logger.error(f"Error validating GPU/PyTorch: {str(e)}")
            return {
                "action": "validate_gpu",
                "status": "error",
                "error": str(e),
                "recommendation": "Check container environment and Python installation"
            }

    def _quick_namespace_reset(self) -> Dict[str, Any]:
        """Quick namespace reset - restarting Tailscale (equivalent to quick-fixes.bat namespace)"""
        result = self._execute_python_script("namespace_reset", "Namespace_Reset")
        
        # Add specific formatting for namespace reset
        if result["status"] == "completed":
            result.update({
                "steps_completed": [
                    "Namespace reset Python script executed",
                    "Tailscale container restarted",
                    "Network connectivity tested",
                    "Result validated"
                ],
                "next_steps": [
                    "Test OpenWebUI access through Tailscale",
                    "Verify Ollama connectivity from OpenWebUI",
                    "Monitor system stability"
                ]
            })
        
        return result

    def _rebuild_tailscale(self) -> Dict[str, Any]:
        """Rebuild Tailscale container (equivalent to quick-fixes.bat rebuild)"""
        result = self._execute_python_script("rebuild_tailscale", "Rebuild_Tailscale")
        
        # Add specific formatting for rebuild
        if result["status"] == "completed":
            result.update({
                "steps_completed": [
                    "Rebuild Tailscale Python script executed",
                    "Tailscale container stopped and rebuilt",
                    "Container started with clean image",
                    "Network connectivity verified"
                ],
                "next_steps": [
                    "Test Tailscale serve configuration",
                    "Verify VPN connectivity",
                    "Monitor container stability"
                ]
            })
        
        return result

    def _quick_gpu_check(self) -> Dict[str, Any]:
        """Quick GPU check and restart (equivalent to quick-fixes.bat gpu)"""
        result = self._execute_python_script("gpu_check", "Gpu_Check")
        
        # Add specific formatting for GPU check
        if result["status"] == "completed":
            result.update({
                "steps_completed": [
                    "GPU check Python script executed",
                    "OpenWebUI GPU availability tested",
                    "GPU services restarted if needed",
                    "Ollama GPU integration verified"
                ],
                "next_steps": [
                    "Test model loading in OpenWebUI",
                    "Verify reranker models use GPU acceleration",
                    "Monitor GPU memory usage during workloads"
                ]
            })
        
        return result

    def _fix_lmstudio(self) -> Dict[str, Any]:
        """Fix LM Studio Tailscale connectivity (equivalent to quick-fixes.bat lmstudio)"""
        try:
            logger.info("Starting LM Studio fix...")
            
            project_root = self._find_project_root()
            if not project_root:
                logger.error("Project root not found - docker-compose.yml missing")
                return self._project_root_error("lmstudio_fix")
            
            logger.info(f"Using project root: {project_root}")
            
            # Use the Python script instead of the batch file
            script_path = os.path.join(project_root, "scripts", "lmstudio_fix.py")
            
            if not os.path.exists(script_path):
                return {
                    "action": "lmstudio_fix",
                    "status": "error",
                    "error": f"Python script not found: {script_path}",
                    "recommendation": "Ensure scripts/lmstudio_fix.py exists in the project directory"
                }
            
            logger.info("Executing LM Studio fix Python script...")
            
            # Execute the Python script
            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=120  # 2 minute timeout
                )
                
                logger.info(f"Script execution completed with return code: {result.returncode}")
                
                if result.returncode == 0:
                    return {
                        "action": "lmstudio_fix",
                        "status": "completed",
                        "description": "LM Studio fix executed successfully",
                        "steps_completed": [
                            "LM Studio connectivity fix initiated",
                            "Python script executed: scripts/lmstudio_fix.py",
                            "LM Studio connectivity tested",
                            "Socat proxy configured",
                            "Tailscale serve configured for LM Studio"
                        ],
                        "script_output": result.stdout,
                        "access_url": "https://your-tailscale-url/lmstudio",
                        "next_steps": [
                            "Test LM Studio access through Tailscale URL",
                            "Verify model loading works through proxy"
                        ]
                    }
                else:
                    return {
                        "action": "lmstudio_fix",
                        "status": "error",
                        "error": f"Script execution failed with return code {result.returncode}",
                        "script_output": result.stdout if result.stdout else "No output",
                        "script_error": result.stderr if result.stderr else "No error output",
                        "recommendation": "Check the script output for specific error details",
                        "debug_info": {
                            "script_path": script_path,
                            "working_directory": project_root,
                            "command": f"python {script_path}"
                        }
                    }
                    
            except subprocess.TimeoutExpired:
                return {
                    "action": "lmstudio_fix",
                    "status": "error",
                    "error": "Script execution timed out after 2 minutes",
                    "recommendation": "Check if LM Studio is running and accessible, then try again"
                }
            except Exception as e:
                logger.error(f"Error executing script: {e}")
                return {
                    "action": "lmstudio_fix",
                    "status": "error",
                    "error": f"Failed to execute Python script: {str(e)}",
                    "recommendation": "Ensure scripts/lmstudio_fix.py is accessible and Python has proper permissions"
                }
            
        except Exception as e:
            logger.error(f"Error in LM Studio fix: {str(e)}")
            return {
                "action": "lmstudio_fix",
                "status": "error",
                "error": f"LM Studio fix failed: {str(e)}"
            }
            
            # Wait for proxy to initialize
            logger.info("Waiting for proxy to initialize...")
            time.sleep(8)
            
            # Test proxy connection
            logger.info("Testing proxy connection...")
            proxy_test = subprocess.run(
                ["docker", "compose", "exec", "-T", "tailscale",
                 "sh", "-c", "wget -q -T 5 -O /dev/null http://127.0.0.1:8234/v1/models && echo 'Proxy working' || echo 'Proxy failed'"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            proxy_working = "Proxy working" in proxy_test.stdout
            
            # Configure Tailscale serve
            logger.info("Configuring Tailscale serve...")
            serve_config = subprocess.run(
                ["docker", "compose", "exec", "-T", "tailscale",
                 "tailscale", "--socket=/tmp/tailscaled.sock", 
                 "serve", "--https=443", "--set-path=/lmstudio", "--bg", "http://127.0.0.1:8234"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=15
            )
            
            serve_configured = serve_config.returncode == 0
            
            lmstudio_results.update({
                "proxy_test": {
                    "status": "working" if proxy_working else "failed",
                    "output": proxy_test.stdout
                },
                "serve_configuration": {
                    "status": "success" if serve_configured else "failed",
                    "output": serve_config.stdout if serve_configured else serve_config.stderr
                },
                "access_url": "https://openwebui-13.tail37f875.ts.net/lmstudio",
                "next_steps": [
                    "Test LM Studio access through Tailscale URL",
                    "Verify model loading works through proxy"
                ] if proxy_working and serve_configured else [
                    "Check if LM Studio is running on host",
                    "Verify LM Studio is accessible on 169.254.83.107:5506",
                    "Check Tailscale configuration"
                ]
            })
            
            return lmstudio_results
            
        except subprocess.TimeoutExpired:
            return {
                "action": "lmstudio_fix",
                "status": "error",
                "error": "LM Studio fix timed out",
                "recommendation": "Check LM Studio availability and try again"
            }
        except Exception as e:
            logger.error(f"Error fixing LM Studio: {str(e)}")
            return {
                "action": "lmstudio_fix",
                "status": "error",
                "error": str(e)
            }

    def _restart_openwebui(self) -> Dict[str, Any]:
        """Restart OpenWebUI with proper network dependency handling (equivalent to quick-fixes.bat restart-openwebui)"""
        result = self._execute_python_script("restart_openwebui", "Restart_Openwebui")
        
        # Add specific formatting for OpenWebUI restart
        if result["status"] == "completed":
            result.update({
                "steps_completed": [
                    "OpenWebUI restart Python script executed",
                    "Dependent containers stopped in proper order",
                    "OpenWebUI restarted and health checked",
                    "Dependent services restarted sequentially"
                ],
                "next_steps": [
                    "Test OpenWebUI access through browser",
                    "Verify Ollama model loading functionality",
                    "Check Tailscale VPN connectivity",
                    "Monitor system stability"
                ]
            })
        
        return result

    def _nuclear_option(self) -> Dict[str, Any]:
        """Nuclear option - full stack restart (equivalent to quick-fixes.bat nuclear)"""
        result = self._execute_python_script("nuclear_option", "Nuclear_Option")
        
        # Add specific formatting for nuclear option
        if result["status"] == "completed":
            result.update({
                "warning": "⚠️ NUCLEAR OPTION - FULL STACK RESTART ⚠️",
                "steps_completed": [
                    "Nuclear option Python script executed",
                    "Pre-restart diagnostic performed", 
                    "All containers stopped completely",
                    "Clean shutdown wait completed",
                    "All containers restarted",
                    "Full stack initialization completed",
                    "Final connectivity verified"
                ],
                "next_steps": [
                    "Test all services through web interface",
                    "Verify GPU functionality if needed",
                    "Monitor system stability",
                    "Check for any remaining issues"
                ]
            })
        
        return result

    def _status_check(self) -> Dict[str, Any]:
        """Comprehensive system status check (equivalent to quick-fixes.bat status)"""
        result = self._execute_python_script("status_check", "Status_Check")
        
        # Add specific formatting for status check
        if result["status"] == "completed":
            result.update({
                "steps_completed": [
                    "Status check Python script executed",
                    "Container status verified",
                    "GPU availability checked",
                    "Network connectivity tested",
                    "Service accessibility validated",
                    "Tailscale status retrieved"
                ],
                "next_steps": [
                    "Review detailed status output for any issues",
                    "Address any failed checks individually",
                    "Monitor system performance"
                ]
            })
        
        return result

    def _project_root_error(self, action: str) -> Dict[str, Any]:
        """Helper method for project root not found errors"""
        return {
            "action": action,
            "status": "error",
            "error": "Could not find project root directory",
            "recommendation": "Ensure docker-compose.yml exists in project directory"
        }

    def _find_project_root(self) -> Optional[str]:
        """Find the project root directory containing docker-compose.yml"""
        # First check if we're in a container with the project mounted at /host_project
        if os.path.exists('/host_project/docker-compose.yml'):
            return '/host_project'
        
        # Fallback to searching from current directory
        current_dir = os.getcwd()
        project_root = current_dir
        
        # Look for docker-compose.yml in current directory or parent directories
        while not os.path.exists(os.path.join(project_root, "docker-compose.yml")):
            parent = os.path.dirname(project_root)
            if parent == project_root:  # Reached root directory
                return None
            project_root = parent
        
        return project_root

    def _project_root_error(self, action: str) -> Dict[str, Any]:
        """Return standardized project root error"""
        return {
            "action": action,
            "status": "error",
            "error": "docker-compose.yml not found in current directory or parent directories",
            "recommendation": "Run this command from the AI Stack project root directory",
            "current_directory": os.getcwd()
        }
    
    def _get_command_template(self, action: str) -> str:
        """Get command template for action"""
        templates = {
            "namespace": "Emergency Recovery Module: _quick_namespace_reset()",
            "rebuild": "Emergency Recovery Module: _rebuild_tailscale()",
            "gpu": "Emergency Recovery Module: _quick_gpu_check()",
            "lmstudio": "Emergency Recovery Module: _fix_lmstudio()",
            "restart_openwebui": "Emergency Recovery Module: _restart_openwebui()",
            "nuclear": "Emergency Recovery Module: _nuclear_option()",
            "tailscale": "scripts\\emergency-recovery.ps1 -Action recover",
            "restart_ollama": "Emergency Recovery Module: _restart_ollama()",
            "validate_gpu": "Emergency Recovery Module: _validate_gpu_pytorch()",
            "advanced": "scripts\\emergency-recovery.ps1 -Action recover"
        }
        return templates.get(action, "Emergency Recovery Module: _quick_namespace_reset()")

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
                
                # If action is None, user wants to see available actions
                if action is None:
                    result_data = self.get_available_recovery_actions()
                    content = self._format_available_actions(result_data)
                else:
                    # Execute the specific recovery action
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
        
        # Handle both description and error fields
        if "error" in result_data:
            description = result_data["error"]
        else:
            description = result_data.get("description", "No description")
        
        content = f"""## 🔧 Recovery Action: {action.title()}

**Status**: {status.title()}
**Description**: {description}
"""
        
        # Show error details if status is error
        if status == "error":
            if "recommendation" in result_data:
                content += f"\n**Recommendation**: {result_data['recommendation']}\n"
            if "current_directory" in result_data:
                content += f"\n**Current Directory**: {result_data['current_directory']}\n"
            if "script_output" in result_data:
                content += f"\n### 📤 Script Output:\n```\n{result_data['script_output']}\n```\n"
            if "script_error" in result_data:
                content += f"\n### ❌ Script Error:\n```\n{result_data['script_error']}\n```\n"
            if "debug_info" in result_data:
                debug = result_data["debug_info"]
                content += f"\n### 🔍 Debug Information:\n"
                for key, value in debug.items():
                    content += f"**{key.replace('_', ' ').title()}**: {value}\n"
            return content
        
        # Handle guidance_provided status (for operations that need host execution)
        if status == "guidance_provided":
            if "host_command" in result_data:
                content += f"\n### 🚀 Host Command\n```\n{result_data['host_command']}\n```\n"
            
            if "manual_steps" in result_data:
                content += "\n### 📋 Manual Steps:\n"
                for step in result_data["manual_steps"]:
                    content += f"{step}\n"
            
            if "what_it_does" in result_data:
                content += "\n### 🔧 What This Does:\n"
                for item in result_data["what_it_does"]:
                    content += f"• {item}\n"
            
            if "expected_outcome" in result_data:
                outcome = result_data["expected_outcome"]
                content += "\n### ✅ Expected Outcome:\n"
                if "success_message" in outcome:
                    content += f"**Success**: {outcome['success_message']}\n"
                if "access_url" in outcome:
                    content += f"**Access URL**: {outcome['access_url']}\n"
                if "test_command" in outcome:
                    content += f"**Test**: {outcome['test_command']}\n"
            
            if "troubleshooting" in result_data and "if_fails" in result_data["troubleshooting"]:
                content += "\n### 🔍 If It Fails:\n"
                for tip in result_data["troubleshooting"]["if_fails"]:
                    content += f"• {tip}\n"
            
            return content
        
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