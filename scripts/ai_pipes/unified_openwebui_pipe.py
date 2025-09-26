"""
AI Stack Unified Pipe Function for OpenWebUI

This is a SINGLE pipe function that replaces all individual AI Stack pipe functions.
It provides intelligent routing to all AI Stack capabilities through one interface.

SETUP INSTRUCTIONS:
1. Go to OpenWebUI Admin → Functions
2. Create new function (delete any existing AI Stack pipe functions)
3. Paste this ENTIRE file as the function code
4. Save and test with queries like "Check GPU status" or "System health"

BENEFITS:
- Only ONE pipe function needed in OpenWebUI (instead of 4-5 separate functions)
- Intelligent routing based on user input
- Unified management and maintenance
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
            default="/host_scripts/ai_pipes/ai_stack_router.py",
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
            if not os.path.exists(self.valves.ROUTER_SCRIPT_PATH):
                return {
                    "service": "AI Stack Unified Pipe",
                    "status": "error", 
                    "message": f"Router script not found: {self.valves.ROUTER_SCRIPT_PATH}",
                    "help": "Ensure the ai_stack_router.py file exists in /host_scripts/ai_pipes/"
                }
                
            # Import and execute router
            sys.path.append('/host_scripts/ai_pipes')
            sys.path.append('/host_scripts')
            
            spec = importlib.util.spec_from_file_location("ai_stack_router", self.valves.ROUTER_SCRIPT_PATH)
            if spec is None or spec.loader is None:
                return {
                    "service": "AI Stack Unified Pipe",
                    "status": "error",
                    "message": f"Cannot load router script: {self.valves.ROUTER_SCRIPT_PATH}"
                }
                
            router_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(router_module)
            
            if not hasattr(router_module, 'main'):
                return {
                    "service": "AI Stack Unified Pipe", 
                    "status": "error",
                    "message": "Router script missing 'main' function"
                }
            
            # Execute router main function
            result = router_module.main(payload)
            
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
        """Format the response for OpenWebUI display"""
        if isinstance(result, dict):
            # Check for specific response types and format accordingly
            
            # Router status response
            if result.get("service") == "AI Stack Unified Router" and "loaded_modules" in result:
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
            
            # GPU status response (actual structure from gpu_status_pipe)
            elif "gpu_status" in result and "devices" in result.get("gpu_status", {}):
                gpu_status = result["gpu_status"]
                response = "**🎮 GPU Status**\n\n"
                
                response += f"**CUDA**: {'✅ Available' if gpu_status.get('cuda_available') else '❌ Not Available'}\n"
                
                if gpu_status.get("torch_info"):
                    torch_info = gpu_status["torch_info"]
                    response += f"**PyTorch**: {torch_info.get('version', 'Unknown')} (CUDA {torch_info.get('cuda_version', 'N/A')})\n"
                
                response += f"**Device Count**: {gpu_status.get('device_count', 0)}\n\n"
                
                if gpu_status.get("devices"):
                    for device in gpu_status["devices"]:
                        response += f"**GPU {device.get('device_id', 0)}**: {device.get('name', 'Unknown')}\n"
                        response += f"**Memory**: {device.get('memory_free_gb', 0):.1f} GB free / {device.get('total_memory_gb', 0):.1f} GB total\n"
                        response += f"**Allocated**: {device.get('memory_allocated_gb', 0):.1f} GB\n\n"
                
                if result.get("recommendations"):
                    rec = result["recommendations"]
                    response += f"**Status**: {rec.get('status', 'Unknown')}\n"
                    
                    if rec.get("optimization_tips"):
                        response += "\n**Optimization Tips**:\n"
                        for tip in rec["optimization_tips"]:
                            response += f"• {tip}\n"
                
                if "timestamp" in result:
                    response += f"\n*Updated: {result['timestamp']}*"
                
                return response
            
            # Recovery response (actual structure from emergency_recovery_pipe)
            elif "suggested_action" in result and isinstance(result["suggested_action"], dict):
                response = "**🔧 System Recovery**\n\n"
                
                suggested = result["suggested_action"]
                response += f"**Action**: {suggested.get('action', 'Unknown').title()}\n"
                response += f"**Description**: {suggested.get('description', 'No description')}\n"
                response += f"**Command**: `{suggested.get('command', 'No command')}`\n"
                response += f"**Urgency**: {suggested.get('urgency', 'Unknown').title()}\n"
                response += f"**Success Probability**: {suggested.get('success_probability', 'Unknown').title()}\n\n"
                
                if result.get("user_input_analysis"):
                    response += f"**Analysis**: {result['user_input_analysis']}\n\n"
                
                if result.get("next_steps"):
                    response += "**Next Steps**:\n"
                    for step in result["next_steps"]:
                        response += f"• {step}\n"
                    response += "\n"
                
                if result.get("warning"):
                    response += f"**⚠️ Warning**: {result['warning']}\n"
                
                if "timestamp" in result:
                    response += f"\n*Updated: {result['timestamp']}*"
                
                return response
            
            # Health status response (actual structure from system_health_pipe)
            elif "health_summary" in result and "quick_status" in result:
                response = "**📊 System Health**\n\n"
                
                health = result["health_summary"]
                quick = result["quick_status"]
                
                response += f"**Overall Status**: {health.get('status_indicator', '')} {health.get('status', 'Unknown').title()} ({health.get('health_score', 0)}/100)\n\n"
                
                response += "**Quick Status**:\n"
                response += f"• **Docker**: {quick.get('docker', '❓')}\n"
                response += f"• **Services**: {quick.get('services', '❓')}\n"
                response += f"• **GPU**: {quick.get('gpu', '❓')}\n\n"
                
                if health.get("issues"):
                    response += "**Issues Found**:\n"
                    for issue in health["issues"]:
                        response += f"• {issue}\n"
                    response += "\n"
                
                if health.get("recommendations"):
                    response += "**Recommendations**:\n"
                    for rec in health["recommendations"]:
                        response += f"• {rec}\n"
                    response += "\n"
                
                if result.get("detailed_info"):
                    detailed = result["detailed_info"]
                    if detailed.get("gpu", {}).get("cuda_available"):
                        gpu_info = detailed["gpu"]
                        response += f"**GPU Details**: CUDA Available ({gpu_info.get('device_count', 0)} devices)\n"
                
                if "timestamp" in result:
                    response += f"\n*Updated: {result['timestamp']}*"
                
                return response
            
            # Help response (actual structure from help_pipe)
            elif result.get("service") == "AI Stack Help System" and "overview" in result:
                response = "**🆘 AI Stack Help**\n\n"
                
                if "overview" in result:
                    overview = result["overview"]
                    if "description" in overview:
                        response += f"**{overview['description']}**\n\n"
                    
                    if "capabilities" in overview:
                        response += "**Capabilities:**\n"
                        for cap in overview["capabilities"]:
                            response += f"• {cap}\n"
                        response += "\n"
                
                if "quick_start" in result:
                    quick = result["quick_start"]
                    response += "**Quick Start:**\n"
                    for key, value in quick.items():
                        response += f"• {value}\n"
                    response += "\n"
                
                if "pipe_functions_available" in result:
                    functions = result["pipe_functions_available"]
                    response += f"**Available Functions**: {len(functions)}\n"
                    for func in functions:
                        response += f"• {func}\n"
                    response += "\n"
                
                if "usage_tip" in result:
                    response += f"**💡 Tip**: {result['usage_tip']}\n"
                
                if "timestamp" in result:
                    response += f"\n*Updated: {result['timestamp']}*"
                
                return response
            
            # Tools response (actual structure from custom_tools_pipe)  
            elif result.get("service") == "AI Stack Custom Tools" and "tools_available" in result:
                response = "**🛠️ Available Tools**\n\n"
                
                tools = result["tools_available"]["available_tools"]
                total_tools = result["tools_available"].get("total_tools", 0)
                total_categories = result["tools_available"].get("total_categories", 0)
                
                response += f"**Total**: {total_tools} tools in {total_categories} categories\n\n"
                
                # Recovery tools
                if "recovery_tools" in tools:
                    response += "**🔧 Recovery Tools:**\n"
                    for tool_name, tool_info in tools["recovery_tools"].items():
                        response += f"• **{tool_name}**: {tool_info.get('description', 'No description')}\n"
                    response += "\n"
                
                # Monitoring tools  
                if "monitoring_tools" in tools:
                    response += "**📊 Monitoring Tools:**\n"
                    for tool_name, tool_info in tools["monitoring_tools"].items():
                        response += f"• **{tool_name}**: {tool_info.get('description', 'No description')}\n"
                    response += "\n"
                
                # Utility tools
                if "utility_tools" in tools:
                    response += "**⚙️ Utility Tools:**\n"
                    for tool_name, tool_info in tools["utility_tools"].items():
                        response += f"• **{tool_name}**: {tool_info.get('description', 'No description')}\n"
                    response += "\n"
                
                # Pipe tools
                if "pipe_tools" in tools:
                    response += "**🔗 Pipe Functions:**\n"
                    for tool_name, tool_info in tools["pipe_tools"].items():
                        response += f"• **{tool_name}**: {tool_info.get('description', 'No description')}\n"
                    response += "\n"
                
                if result.get("usage"):
                    response += f"**Usage**: {result['usage']}\n"
                
                if result.get("quick_access"):
                    quick_access = result["quick_access"]
                    response += "\n**Quick Access:**\n"
                    for category, tip in quick_access.items():
                        response += f"• **{category.title()}**: {tip}\n"
                
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
            
            # Generic JSON response
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
                "timestamp": body.get("timestamp"),
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