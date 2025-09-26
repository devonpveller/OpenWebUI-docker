"""
Enhanced AI Stack Pipe Function Template for OpenWebUI

This is the complete pipe function template to be pasted into OpenWebUI Admin → Functions.
Provides integration with host Python scripts mounted in the container.

Copy the entire content below and paste it as a new function in OpenWebUI.
"""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import json, os, sys, subprocess, importlib.util
import logging

class Pipe:
    class Valves(BaseModel):
        SCRIPT_PATH: str = Field(
            default="/host_scripts/ai_pipes/gpu_status_pipe.py",
            description="Absolute path to script inside container (mounted from host scripts/ directory)."
        )
        ENTRYPOINT: str = Field(
            default="main",
            description="Function to call if EXEC_MODE='import'. For AI stack scripts, typically 'main' or 'process'."
        )
        EXEC_MODE: str = Field(
            default="import",
            description="Choose 'import' (library mode) or 'subprocess' (CLI mode)."
        )
        TIMEOUT_SEC: int = Field(
            default=120,
            description="Increased timeout for AI/ML processing scripts."
        )
        ENABLE_GPU_CHECK: bool = Field(
            default=True,
            description="Check GPU availability before script execution (leverages your CUDA setup)."
        )
        LOG_EXECUTION: bool = Field(
            default=True,
            description="Log script execution for debugging and monitoring."
        )
        
        @field_validator("EXEC_MODE")
        def check_mode(cls, v):
            if v not in ("import", "subprocess"):
                raise ValueError("EXEC_MODE must be 'import' or 'subprocess'")
            return v

    def __init__(self):
        self.valves = self.Valves()
        self.logger = self._setup_logging()

    def _setup_logging(self):
        """Setup structured logging consistent with AI stack patterns"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(__name__)

    def pipe(self, body: Dict[str, Any]) -> str:
        """Main pipe execution with AI stack optimizations"""
        try:
            # GPU availability check (leverages your custom CUDA build)
            gpu_status = ""
            if self.valves.ENABLE_GPU_CHECK:
                gpu_status = self._check_gpu_status()
                if self.valves.LOG_EXECUTION:
                    self.logger.info(f"GPU Status: {gpu_status}")

            user_input = self._extract_user_text(body.get("messages", []))
            payload = {
                "input": user_input, 
                "messages": body.get("messages", []),
                "gpu_available": "✅" in gpu_status,
                "workspace_context": "ai-stack"  # Identify source environment
            }
            
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"Executing script: {self.valves.SCRIPT_PATH} in {self.valves.EXEC_MODE} mode")
            
            if self.valves.EXEC_MODE == "import":
                result = self._run_import(payload)
            else:
                result = self._run_subprocess(payload)
            
            # Format result for display
            if isinstance(result, dict):
                return self._format_json_result(result)
            else:
                return str(result)
                
        except Exception as e:
            error_msg = f"❌ AI Stack Pipe Error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return error_msg

    def _check_gpu_status(self) -> str:
        """Check GPU status (consistent with your emergency recovery patterns)"""
        try:
            import torch
            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                current_device = torch.cuda.current_device()
                device_name = torch.cuda.get_device_name(current_device)
                return f"✅ GPU Available: {device_name} ({device_count} devices)"
            else:
                return "⚠️ GPU Not Available (check CUDA configuration)"
        except ImportError:
            return "⚠️ PyTorch not available for GPU checking"
        except Exception as e:
            return f"❌ GPU Check Failed: {str(e)}"

    def _run_import(self, payload: Dict[str, Any]) -> Any:
        """Import-based execution with enhanced error handling"""
        try:
            if not os.path.exists(self.valves.SCRIPT_PATH):
                return f"❌ Script not found: {self.valves.SCRIPT_PATH}"
                
            spec = importlib.util.spec_from_file_location("_ai_stack_script", self.valves.SCRIPT_PATH)
            if spec is None or spec.loader is None:
                return f"❌ Cannot load script spec: {self.valves.SCRIPT_PATH}"
                
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            if not hasattr(mod, self.valves.ENTRYPOINT):
                available_functions = [attr for attr in dir(mod) if not attr.startswith('_')]
                return f"❌ Function '{self.valves.ENTRYPOINT}' not found. Available: {available_functions}"
            
            func = getattr(mod, self.valves.ENTRYPOINT)
            result = func(payload)
            
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"✅ Script executed successfully: {self.valves.ENTRYPOINT}")
            
            return result
            
        except Exception as e:
            error_msg = f"❌ Import execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return error_msg

    def _run_subprocess(self, payload: Dict[str, Any]) -> str:
        """Subprocess execution with PowerShell compatibility"""
        try:
            if not os.path.exists(self.valves.SCRIPT_PATH):
                return f"❌ Script not found: {self.valves.SCRIPT_PATH}"
            
            # Enhanced subprocess execution compatible with your Windows/PowerShell environment
            proc = subprocess.run(
                [sys.executable, self.valves.SCRIPT_PATH],
                input=json.dumps(payload),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.valves.TIMEOUT_SEC,
                text=True,
                encoding='utf-8',
                cwd=os.path.dirname(self.valves.SCRIPT_PATH)  # Set working directory
            )
            
            if proc.returncode != 0:
                error_output = proc.stderr.strip()
                return f"❌ Script execution failed (exit code {proc.returncode}): {error_output}"
            
            output = proc.stdout.strip()
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"✅ Subprocess executed successfully")
            
            return output if output else "✅ Script completed successfully (no output)"
            
        except subprocess.TimeoutExpired:
            return f"❌ Timeout: Script execution exceeded {self.valves.TIMEOUT_SEC} seconds"
        except Exception as e:
            error_msg = f"❌ Subprocess execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return error_msg

    def _extract_user_text(self, messages: List[Dict[str, Any]]) -> str:
        """Enhanced message extraction supporting various content types"""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    # Handle multimodal content (text, images, etc.)
                    text_items = [
                        item.get('text', '') for item in content 
                        if isinstance(item, dict) and item.get('type') == 'text'
                    ]
                    return ' '.join(text_items)
        return ""

    def _format_json_result(self, result: Dict[str, Any]) -> str:
        """Format JSON results for better readability in chat"""
        try:
            # Check if result has structured information we can format nicely
            if isinstance(result, dict):
                formatted_lines = []
                
                # Handle status/service information
                if "service" in result:
                    formatted_lines.append(f"**{result['service']}**")
                
                if "status" in result or "overall_status" in result:
                    status = result.get("overall_status", result.get("status", ""))
                    formatted_lines.append(f"Status: {status}")
                
                # Handle health summary
                if "health_summary" in result:
                    health = result["health_summary"]
                    formatted_lines.append(f"\n**Health Summary:**")
                    formatted_lines.append(f"Score: {health.get('summary', 'Unknown')}")
                    
                    if health.get("recommendations"):
                        formatted_lines.append(f"\n**Recommendations:**")
                        for rec in health["recommendations"][:3]:  # Limit to top 3
                            formatted_lines.append(f"• {rec}")
                
                # Handle quick status (dict format)
                if "quick_status" in result and isinstance(result["quick_status"], dict):
                    quick = result["quick_status"]
                    formatted_lines.append(f"\n**Quick Status:**")
                    for key, value in quick.items():
                        formatted_lines.append(f"• {key}: {value}")
                
                # Handle GPU information
                if "cuda_available" in result:
                    gpu_status = "✅ Available" if result["cuda_available"] else "❌ Not Available"
                    formatted_lines.append(f"\nGPU: {gpu_status}")
                
                # Handle GPU status details (from gpu_status_pipe.py)
                if "gpu_status" in result and isinstance(result["gpu_status"], dict):
                    gpu_data = result["gpu_status"]
                    formatted_lines.append(f"\n**GPU Status Details:**")
                    if gpu_data.get("status"):
                        formatted_lines.append(f"Status: {gpu_data['status']}")
                    if gpu_data.get("devices") and len(gpu_data["devices"]) > 0:
                        device = gpu_data["devices"][0]  # Show first device
                        formatted_lines.append(f"Device: {device.get('name', 'Unknown')}")
                        formatted_lines.append(f"Memory: {device.get('memory_allocated_gb', 0)} GB used / {device.get('total_memory_gb', 0)} GB total")
                
                # Handle basic service responses (like no-input GPU status)
                if "service" in result and "quick_status" in result and isinstance(result["quick_status"], str):
                    formatted_lines.append(f"\n**{result['service']}**")
                    formatted_lines.append(f"Status: {result['quick_status']}")
                    if result.get("usage_tip"):
                        formatted_lines.append(f"\n*Tip: {result['usage_tip']}*")
                
                # Handle recommendations
                if "recommendations" in result and isinstance(result["recommendations"], dict):
                    rec_data = result["recommendations"]
                    if rec_data.get("status"):
                        formatted_lines.append(f"\n**Recommendations:**")
                        formatted_lines.append(f"Status: {rec_data['status']}")
                        if rec_data.get("optimization_tips"):
                            for tip in rec_data["optimization_tips"][:2]:  # Show first 2 tips
                                formatted_lines.append(f"• {tip}")
                
                # Handle suggestions/commands
                if "suggested_action" in result:
                    action = result["suggested_action"]
                    if isinstance(action, dict):
                        formatted_lines.append(f"\n**Suggested Action:**")
                        formatted_lines.append(f"Command: `{action.get('command', 'Unknown')}`")
                        formatted_lines.append(f"Description: {action.get('description', 'No description')}")
                        if action.get("execution_note"):
                            formatted_lines.append(f"Note: {action['execution_note']}")
                
                # Add timestamp if available
                if "timestamp" in result:
                    formatted_lines.append(f"\n*Updated: {result['timestamp']}*")
                
                if formatted_lines:
                    return '\n'.join(formatted_lines)
            
            # Fallback to pretty JSON
            return f"```json\n{json.dumps(result, indent=2)}\n```"
            
        except Exception:
            # Ultimate fallback
            return str(result)