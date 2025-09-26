"""
Legacy Module Adapter

Provides backward compatibility with existing pipe modules during the migration
to the new manifest-driven architecture.
"""

import importlib.util
import logging
import os
import subprocess
import sys
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pathlib import Path

from router import RequestEnvelope, ModuleResult, ExecutionStatus

class LegacyModuleAdapter:
    """Adapter for executing existing pipe modules in the new architecture"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        
        # Legacy module mappings
        self.legacy_modules = {
            "gpu-status": {
                "path": "/host_scripts/ai_pipes/gpu_status_pipe.py",
                "function": "main"
            },
            "emergency-recovery": {
                "path": "/host_scripts/ai_pipes/emergency_recovery_pipe.py",
                "function": "main"
            },
            "system-health": {
                "path": "/host_scripts/ai_pipes/system_health_pipe.py",
                "function": "main"
            },
            "custom-tools": {
                "path": "/host_scripts/ai_pipes/custom_tools_pipe.py",
                "function": "main"
            },
            "help": {
                "path": "/host_scripts/ai_pipes/help_pipe.py",
                "function": "main"
            }
        }
    
    def execute(self, module_manifest: Dict[str, Any], request: RequestEnvelope) -> ModuleResult:
        """Execute a module using legacy adapter"""
        module_slug = module_manifest["slug"]
        
        try:
            # Check if we have a legacy mapping
            if module_slug in self.legacy_modules:
                return self._execute_legacy_module(module_slug, request)
            
            # Try to execute using manifest entry point
            return self._execute_manifest_module(module_manifest, request)
            
        except Exception as e:
            self.logger.error(f"❌ Legacy adapter execution error: {e}")
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.ERROR,
                content=f"❌ **Execution Error**: {str(e)}",
                error={
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                }
            )
    
    def _execute_legacy_module(self, module_slug: str, request: RequestEnvelope) -> ModuleResult:
        """Execute using legacy module mapping"""
        legacy_config = self.legacy_modules[module_slug]
        module_path = legacy_config["path"]
        function_name = legacy_config["function"]
        
        if not os.path.exists(module_path):
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.ERROR,
                content=f"❌ **Module Not Found**: {module_path}",
                error={
                    "code": "MODULE_NOT_FOUND",
                    "message": f"Legacy module file not found: {module_path}",
                    "retriable": False
                }
            )
        
        try:
            # Load and execute legacy module
            spec = importlib.util.spec_from_file_location(f"_{module_slug}_legacy", module_path)
            if not spec or not spec.loader:
                raise ImportError(f"Cannot load module spec: {module_path}")
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if not hasattr(module, function_name):
                raise AttributeError(f"Function {function_name} not found in {module_path}")
            
            # Prepare legacy payload
            legacy_payload = {
                "input": str(request.input),
                "user_id": request.user["id"],
                "timestamp": request.timestamp,
                "messages": request.context.get("prior_turns", []) if request.context else []
            }
            
            # Execute function
            func = getattr(module, function_name)
            start_time = datetime.now()
            result = func(legacy_payload)
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            
            # Format result
            content = self._format_legacy_result(result)
            structured_data = result if isinstance(result, dict) else None
            
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.OK,
                content=content,
                structured_data=structured_data,
                diagnostics={
                    "execution_time_ms": int(execution_time),
                    "adapter": "legacy",
                    "module_path": module_path
                }
            )
            
        except Exception as e:
            self.logger.error(f"❌ Legacy module execution error: {e}")
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.ERROR,
                content=f"❌ **Legacy Module Error**: {str(e)}",
                error={
                    "code": "LEGACY_EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                }
            )
    
    def _execute_manifest_module(self, module_manifest: Dict[str, Any], request: RequestEnvelope) -> ModuleResult:
        """Execute module using manifest entry point"""
        entry = module_manifest["entry"]
        entry_kind = entry["kind"]
        entry_path = entry["path"]
        
        if entry_kind == "cli":
            return self._execute_cli_module(module_manifest, request, entry_path)
        elif entry_kind == "http":
            return self._execute_http_module(module_manifest, request, entry_path)
        else:
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_manifest["slug"],
                status=ExecutionStatus.ERROR,
                content=f"❌ **Unsupported Entry Kind**: {entry_kind}",
                error={
                    "code": "UNSUPPORTED_ENTRY_KIND",
                    "message": f"Entry kind '{entry_kind}' not supported",
                    "retriable": False
                }
            )
    
    def _execute_cli_module(self, module_manifest: Dict[str, Any], request: RequestEnvelope, entry_path: str) -> ModuleResult:
        """Execute CLI-based module"""
        module_slug = module_manifest["slug"]
        
        try:
            # Prepare input for CLI
            cli_input = {
                "request_id": request.request_id,
                "input": request.input,
                "user": request.user,
                "timestamp": request.timestamp
            }
            
            # Execute subprocess
            process = subprocess.run(
                [sys.executable, entry_path],
                input=json.dumps(cli_input),
                capture_output=True,
                text=True,
                timeout=module_manifest.get("limits", {}).get("timeout_ms", 30000) / 1000,
                cwd=os.path.dirname(entry_path)
            )
            
            if process.returncode != 0:
                error_msg = f"CLI execution failed (exit code {process.returncode}): {process.stderr}"
                return ModuleResult(
                    request_id=request.request_id,
                    module_id=module_slug,
                    status=ExecutionStatus.ERROR,
                    content=f"❌ **CLI Error**: {error_msg}",
                    error={
                        "code": "CLI_EXECUTION_ERROR",
                        "message": error_msg,
                        "retriable": True
                    }
                )
            
            # Parse output
            try:
                result_data = json.loads(process.stdout)
                content = self._format_legacy_result(result_data)
                structured_data = result_data if isinstance(result_data, dict) else None
            except json.JSONDecodeError:
                content = process.stdout.strip()
                structured_data = None
            
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.OK,
                content=content,
                structured_data=structured_data,
                diagnostics={
                    "adapter": "cli",
                    "entry_path": entry_path,
                    "exit_code": process.returncode
                }
            )
            
        except subprocess.TimeoutExpired:
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.ERROR,
                content="❌ **Timeout**: Module execution timed out",
                error={
                    "code": "EXECUTION_TIMEOUT",
                    "message": "Module execution exceeded timeout limit",
                    "retriable": True
                }
            )
        except Exception as e:
            return ModuleResult(
                request_id=request.request_id,
                module_id=module_slug,
                status=ExecutionStatus.ERROR,
                content=f"❌ **CLI Adapter Error**: {str(e)}",
                error={
                    "code": "CLI_ADAPTER_ERROR",
                    "message": str(e),
                    "retriable": True
                }
            )
    
    def _execute_http_module(self, module_manifest: Dict[str, Any], request: RequestEnvelope, entry_url: str) -> ModuleResult:
        """Execute HTTP-based module (placeholder for future implementation)"""
        return ModuleResult(
            request_id=request.request_id,
            module_id=module_manifest["slug"],
            status=ExecutionStatus.ERROR,
            content="❌ **HTTP modules not yet supported**",
            error={
                "code": "HTTP_NOT_IMPLEMENTED",
                "message": "HTTP module execution not yet implemented",
                "retriable": False
            }
        )
    
    def _format_legacy_result(self, result: Any) -> str:
        """Format legacy result for display"""
        if isinstance(result, dict):
            # Handle error responses
            if result.get("status") == "error":
                return f"❌ **Error**: {result.get('message', 'Unknown error')}"
            
            # Format health status
            if "health_summary" in result:
                content_parts = ["**📊 System Health**", ""]
                health = result["health_summary"]
                
                if "status" in health:
                    content_parts.append(f"**Status**: {health['status']}")
                
                if "health_score" in health:
                    content_parts.append(f"**Score**: {health['health_score']}/100")
                
                return "\n".join(content_parts)
            
            # Format GPU status
            if "gpu_available" in result:
                gpu_status = "✅ Available" if result["gpu_available"] else "❌ Not Available"
                content_parts = ["**🎮 GPU Status**", "", f"**Availability**: {gpu_status}"]
                
                if "torch_available" in result:
                    torch_status = "✅" if result["torch_available"] else "❌"
                    content_parts.append(f"**PyTorch**: {torch_status}")
                
                return "\n".join(content_parts)
            
            # Format tools listing
            if "available_tools" in result:
                content_parts = ["**🔧 Available Tools**", ""]
                tools = result["available_tools"]
                
                for category, tool_list in tools.items():
                    if tool_list:
                        content_parts.append(f"**{category.title()}:**")
                        for tool in tool_list[:5]:  # Limit to 5 per category
                            content_parts.append(f"• {tool}")
                        content_parts.append("")
                
                return "\n".join(content_parts)
            
            # Format help content
            if "service" in result and "help" in result:
                content_parts = [f"**{result['service']}**", ""]
                if "description" in result:
                    content_parts.append(result["description"])
                    content_parts.append("")
                
                return "\n".join(content_parts)
            
            # Generic structured result
            if "service" in result:
                content_parts = [f"**{result['service']}**", ""]
                
                if "message" in result:
                    content_parts.append(result["message"])
                
                if "description" in result:
                    content_parts.append(result["description"])
                
                return "\n".join(content_parts)
            
            # Fallback: JSON representation
            return f"```json\n{json.dumps(result, indent=2)}\n```"
        
        return str(result)