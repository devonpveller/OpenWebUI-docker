"""
AI Stack Unified Pipe Function for OpenWebUI - Refactored Architecture

This is a SINGLE pipe function that replaces all individual AI Stack pipe functions.
It provides intelligent routing to all AI Stack capabilities through one interface.
Uses ONLY the new manifest-driven refactored modules - NO legacy formatting.

SETUP INSTRUCTIONS:
1. Go to OpenWebUI Admin → Functions
2. Create new function (delete any existing AI Stack pipe functions)
3. Paste this ENTIRE file as the function code
4. Save and test with queries like "Check GPU status" or "System health"

BENEFITS:
- Only ONE pipe function needed in OpenWebUI (instead of 4-5 separate functions)
- Intelligent routing based on user input
- Unified management and maintenance
- Uses new manifest-driven architecture
- Better user experience
"""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import json, os, sys, subprocess, importlib.util
import logging

class Pipe:
    class Valves(BaseModel):
        ROUTER_SCRIPT_PATH: str = Field(
            default="",  # Will be set dynamically
            description="Path to the unified AI Stack router script"
        )
        TIMEOUT_SEC: int = Field(
            default=120,
            description="Timeout for script execution"
        )
        ENABLE_GPU_CHECK: bool = Field(
            default=True,
            description="Check GPU availability on startup"
        )
        LOG_EXECUTION: bool = Field(
            default=True,
            description="Enable execution logging"
        )
        DEBUG_MODE: bool = Field(
            default=False,
            description="Enable verbose debug logging"
        )

    def __init__(self):
        self.valves = self.Valves()
        
        # Dynamically determine router path (container vs host environment)
        # This MUST be done here as OpenWebUI may not call __init__ at the right time
        if not self.valves.ROUTER_SCRIPT_PATH:  # Only set if not already set
            if os.path.exists('/host_project/core'):
                self.valves.ROUTER_SCRIPT_PATH = "/host_project/core/router.py"
            else:
                # Host environment - use relative path
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(os.path.dirname(current_dir))
                self.valves.ROUTER_SCRIPT_PATH = os.path.join(project_root, "core", "router.py")
        
        self.logger = self._setup_logging()
        
        if self.valves.ENABLE_GPU_CHECK:
            gpu_status = self._check_gpu()
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"GPU Status: {gpu_status}")

    def _setup_logging(self) -> logging.Logger:
        """Setup logging for the pipe"""
        logging.basicConfig(
            level=logging.DEBUG if self.valves.DEBUG_MODE else logging.INFO,
            format='%(asctime)s - AIStack - %(levelname)s - %(message)s'
        )
        return logging.getLogger("ai_stack_unified_pipe")

    def _extract_user_text(self, messages: List[Dict]) -> str:
        """Extract the latest user message from OpenWebUI messages"""
        try:
            for message in reversed(messages):
                if message.get("role") == "user":
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
            return ""
        except Exception as e:
            self.logger.warning(f"Failed to extract user text: {e}")
            return ""

    def _check_gpu(self) -> str:
        """Check GPU availability"""
        try:
            import torch
            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                current_device = torch.cuda.current_device()
                device_name = torch.cuda.get_device_name(current_device)
                return f"✅ {device_name} ({device_count} devices)"
            else:
                return "⚠️ GPU Not Available"
        except ImportError:
            return "⚠️ PyTorch not available"
        except Exception as e:
            return f"❌ GPU Check Failed: {str(e)}"

    def _run_router(self, payload: Dict[str, Any]) -> Any:
        """Execute the unified router script"""
        try:
            # Safety check: ensure router path is set (in case __init__ didn't run properly in OpenWebUI)
            if not self.valves.ROUTER_SCRIPT_PATH:
                if os.path.exists('/host_project/core'):
                    self.valves.ROUTER_SCRIPT_PATH = "/host_project/core/router.py"
                else:
                    # Host environment - use relative path
                    current_dir = os.path.dirname(os.path.abspath(__file__))
                    project_root = os.path.dirname(os.path.dirname(current_dir))
                    self.valves.ROUTER_SCRIPT_PATH = os.path.join(project_root, "core", "router.py")
            
            if not os.path.exists(self.valves.ROUTER_SCRIPT_PATH):
                return {
                    "service": "AI Stack Unified Pipe",
                    "status": "error", 
                    "message": f"Router script not found: {self.valves.ROUTER_SCRIPT_PATH}",
                    "help": "Ensure the router.py file exists and is mounted correctly",
                    "debug_info": {
                        "checked_paths": [
                            "/host_project/core/router.py",
                            "/host_scripts/core/router.py"
                        ],
                        "host_project_exists": os.path.exists('/host_project'),
                        "host_scripts_exists": os.path.exists('/host_scripts'),
                        "current_path": self.valves.ROUTER_SCRIPT_PATH
                    }
                }
                
            # Direct import approach instead of dynamic loading
            if os.path.exists('/host_project/core'):
                sys.path.append('/host_project/core')
                from router import main as router_main
            else:
                # Host environment - add core directory path
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(os.path.dirname(current_dir))
                core_dir = os.path.join(project_root, "core")
                sys.path.append(core_dir)
                from router import main as router_main
            
            # Execute router main function
            result = router_main(payload)
            
            if self.valves.LOG_EXECUTION:
                user_input = payload.get('input', '')[:50]
                self.logger.info(f"✅ Router executed for: '{user_input}...'")
            
            return result
            
        except Exception as e:
            error_msg = f"❌ Router execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            return {
                "service": "AI Stack Unified Pipe",
                "status": "error",
                "message": error_msg,
                "debug_info": {
                    "router_path": self.valves.ROUTER_SCRIPT_PATH,
                    "router_exists": os.path.exists(self.valves.ROUTER_SCRIPT_PATH),
                    "payload_keys": list(payload.keys()) if payload else []
                }
            }

    def _format_response(self, result: Any) -> str:
        """Format the response for OpenWebUI display - using refactored architecture only"""
        if isinstance(result, dict):
            
            # Refactored module responses (manifest-driven architecture)
            if result.get("module_id") in ["system-health", "gpu-status", "emergency-recovery", "custom-tools", "help-system", "system-orchestrator"] and "content" in result:
                # Use the pre-formatted content from refactored modules
                return result.get("content", "Module response unavailable")
            
            # Router status response (for compatibility with router)
            elif result.get("service") == "AI Stack Unified Router" and "loaded_modules" in result:
                modules = result.get("loaded_modules", {})
                capabilities = result.get("capabilities", [])
                
                response = "**🤖 AI Stack Unified System**\n\n"
                response += f"**Status**: {result.get('status', 'unknown').title()}\n"
                response += f"**Modules Loaded**: {result.get('module_count', 0)}\n\n"
                
                response += "**Available Capabilities:**\n"
                for cap in capabilities:
                    response += f"• {cap}\n"
                
                response += "\n**Loaded Modules:**\n"
                for name, desc in modules.items():
                    response += f"• **{name}**: {desc}\n"
                
                if "timestamp" in result:
                    response += f"\n*Updated: {result['timestamp']}*"
                
                return response
            
            # Error response
            elif result.get("status") == "error":
                response = "**❌ Error**\n\n"
                response += f"**Service**: {result.get('service', 'Unknown')}\n"
                response += f"**Message**: {result.get('message', 'Unknown error')}\n"
                
                if "debug_info" in result:
                    response += "\n**Debug Info**:\n"
                    for key, value in result["debug_info"].items():
                        response += f"• {key}: {value}\n"
                
                if "timestamp" in result:
                    response += f"\n*Updated: {result['timestamp']}*"
                
                return response
            
            # Generic JSON response for unrecognized formats
            else:
                try:
                    # Try to format as readable JSON
                    formatted_json = json.dumps(result, indent=2, ensure_ascii=False)
                    return f"```json\n{formatted_json}\n```"
                except:
                    return str(result)
        
        # Non-dict responses
        return str(result)

    def pipe(self, body: dict, __user__: dict) -> str:
        """Main pipe function - single entry point for all AI Stack functionality"""
        try:
            # Extract user input from OpenWebUI messages
            user_input = self._extract_user_text(body.get("messages", []))
            
            # Create payload for router
            payload = {
                "input": user_input,
                "user_id": __user__.get("id", "unknown"),
                "timestamp": body.get("timestamp") or "2024-01-01T12:00:00Z",  # Provide default timestamp
                "messages": body.get("messages", [])
            }
            
            if self.valves.DEBUG_MODE:
                self.logger.debug(f"Processing input: {user_input[:100]}...")
            
            # Execute router
            result = self._run_router(payload)
            
            # Format response for OpenWebUI
            formatted_response = self._format_response(result)
            
            return formatted_response
            
        except Exception as e:
            error_msg = f"❌ Pipe execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            
            return f"**❌ AI Stack Error**\n\n{error_msg}\n\n*Check container logs for details*"