"""
AI Stack Refactored OpenWebUI Adapter

New OpenWebUI pipe function implementing the refactored manifest-driven architecture.
Provides intelligent routing with explicit contracts and comprehensive observability.
"""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import json, os, sys, subprocess, importlib.util
import logging
import uuid
from datetime import datetime, timezone

# Add paths for the refactored system
sys.path.append('/host_scripts/core')
sys.path.append('/host_scripts')

class Pipe:
    class Valves(BaseModel):
        ROUTER_SCRIPT_PATH: str = Field(
            default="/host_scripts/core/router.py",
            description="Path to the refactored router script"
        )
        ENABLE_LEGACY_FALLBACK: bool = Field(
            default=True,
            description="Enable fallback to legacy router during migration"
        )
        TIMEOUT_SEC: int = Field(
            default=120,
            description="Timeout for module execution"
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
        SCHEMA_VALIDATION: bool = Field(
            default=True,
            description="Enable request/response schema validation"
        )

    def __init__(self):
        self.valves = self.Valves()
        self.logger = self._setup_logging()
        
        # Initialize router
        self.router = None
        self._initialize_router()
        
        if self.valves.ENABLE_GPU_CHECK:
            gpu_status = self._check_gpu()
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"GPU Status: {gpu_status}")

    def _setup_logging(self) -> logging.Logger:
        """Setup logging for the pipe"""
        logging.basicConfig(
            level=logging.DEBUG if self.valves.DEBUG_MODE else logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger("ai_stack_refactored_pipe")

    def _initialize_router(self):
        """Initialize the refactored router"""
        try:
            if os.path.exists(self.valves.ROUTER_SCRIPT_PATH):
                # Load refactored router
                spec = importlib.util.spec_from_file_location("refactored_router", self.valves.ROUTER_SCRIPT_PATH)
                if spec and spec.loader:
                    router_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(router_module)
                    
                    if hasattr(router_module, 'router'):
                        self.router = router_module.router
                        self.logger.info("✅ Initialized refactored router")
                    else:
                        self.logger.error("❌ Router instance not found in refactored module")
                else:
                    self.logger.error("❌ Cannot load refactored router spec")
            else:
                self.logger.warning(f"⚠️ Refactored router not found: {self.valves.ROUTER_SCRIPT_PATH}")
                
        except Exception as e:
            self.logger.error(f"❌ Error initializing refactored router: {e}")
            if not self.valves.ENABLE_LEGACY_FALLBACK:
                raise

    def _extract_user_text(self, messages: List[Dict]) -> str:
        """Extract the latest user message from OpenWebUI messages"""
        try:
            if not messages:
                return ""
            
            # Find the last user message
            for message in reversed(messages):
                if message.get("role") == "user":
                    content = message.get("content", "")
                    if isinstance(content, str):
                        return content.strip()
                    elif isinstance(content, dict):
                        # Handle structured content
                        return content.get("text", "").strip()
            
            return ""
        except Exception as e:
            self.logger.error(f"❌ Error extracting user text: {e}")
            return ""

    def _check_gpu(self) -> str:
        """Check GPU availability"""
        try:
            import torch
            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                device_name = torch.cuda.get_device_name(0) if device_count > 0 else "Unknown"
                return f"✅ {device_count} GPU(s) available ({device_name})"
            else:
                return "❌ CUDA not available"
        except ImportError:
            return "❌ PyTorch not available"
        except Exception as e:
            return f"❌ GPU check failed: {str(e)}"

    def _create_request_envelope(self, user_input: str, user_data: Dict[str, Any], 
                               additional_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create request envelope following the schema contract"""
        request_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        
        envelope = {
            "version": "v1.0.0",
            "request_id": request_id,
            "timestamp": timestamp,
            "user": {
                "id": user_data.get("id", "unknown"),
                "roles": user_data.get("roles", []),
                "permissions": user_data.get("permissions", [])
            },
            "input": user_input,
            "locale": "en-US",
            "attachments": [],
            "capabilities_allowed": []
        }
        
        if additional_data:
            if "session" in additional_data:
                envelope["session"] = additional_data["session"]
            if "context" in additional_data:
                envelope["context"] = additional_data["context"]
            if "attachments" in additional_data:
                envelope["attachments"] = additional_data["attachments"]
        
        return envelope

    def _execute_refactored_router(self, request_envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Execute using the refactored router"""
        if not self.router:
            raise Exception("Refactored router not available")
        
        try:
            # Convert envelope to RequestEnvelope object
            from router import RequestEnvelope
            
            request_obj = RequestEnvelope(
                version=request_envelope["version"],
                request_id=request_envelope["request_id"],
                timestamp=request_envelope["timestamp"],
                user=request_envelope["user"],
                input=request_envelope["input"],
                session=request_envelope.get("session"),
                locale=request_envelope.get("locale", "en-US"),
                timezone=request_envelope.get("timezone"),
                attachments=request_envelope.get("attachments", []),
                context=request_envelope.get("context"),
                capabilities_allowed=request_envelope.get("capabilities_allowed", [])
            )
            
            # Route request through refactored system
            result = self.router.route_request(request_obj)
            
            # Convert ModuleResult back to dict for processing
            result_dict = {
                "request_id": result.request_id,
                "module_id": result.module_id,
                "status": result.status.value,
                "content": result.content,
                "structured_data": result.structured_data,
                "diagnostics": result.diagnostics,
                "timestamp": result.timestamp
            }
            
            if result.error:
                result_dict["error"] = result.error
            
            return result_dict
            
        except Exception as e:
            self.logger.error(f"❌ Refactored router execution error: {e}")
            raise

    def _execute_legacy_fallback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback to legacy router execution"""
        try:
            # Use subprocess to execute legacy router
            legacy_router_path = "/host_scripts/ai_pipes/ai_stack_router.py"
            
            if not os.path.exists(legacy_router_path):
                raise FileNotFoundError(f"Legacy router not found: {legacy_router_path}")
            
            # Execute legacy router
            process = subprocess.run(
                [sys.executable, legacy_router_path],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self.valves.TIMEOUT_SEC,
                cwd=os.path.dirname(legacy_router_path)
            )
            
            if process.returncode != 0:
                raise RuntimeError(f"Legacy router execution failed: {process.stderr}")
            
            result = json.loads(process.stdout) if process.stdout.strip() else {}
            
            # Add legacy marker
            result["_execution_mode"] = "legacy_fallback"
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Legacy fallback error: {e}")
            raise

    def _format_response(self, result: Dict[str, Any]) -> str:
        """Format response for OpenWebUI display with enhanced formatting"""
        try:
            # Handle error responses
            if result.get("status") == "error" or result.get("error"):
                return self._format_error_response(result)
            
            # Use structured content if available
            if "content" in result and result["content"]:
                content = result["content"]
                
                # Add execution metadata if in debug mode
                if self.valves.DEBUG_MODE:
                    metadata = self._format_debug_metadata(result)
                    content = f"{content}\n\n{metadata}"
                
                return content
            
            # Fallback to structured data formatting
            return self._format_structured_data(result)
            
        except Exception as e:
            self.logger.error(f"❌ Response formatting error: {e}")
            return f"❌ **Response Formatting Error**: {str(e)}"

    def _format_error_response(self, result: Dict[str, Any]) -> str:
        """Format error responses"""
        error_info = result.get("error", {})
        
        content_parts = ["❌ **AI Stack Error**", ""]
        
        # Error message
        message = error_info.get("message") or result.get("message", "Unknown error")
        content_parts.append(f"**Message**: {message}")
        
        # Error code
        if error_info.get("code"):
            content_parts.append(f"**Code**: {error_info['code']}")
        
        # Module information
        if result.get("module_id"):
            content_parts.extend(["", f"**Module**: {result['module_id']}"])
        
        # Retry information
        if error_info.get("retriable"):
            content_parts.extend([
                "",
                "🔄 **This error may be retriable**",
                "Try your request again or use a different approach."
            ])
        
        # Execution mode
        if result.get("_execution_mode") == "legacy_fallback":
            content_parts.extend(["", "*Executed via legacy fallback*"])
        
        return "\n".join(content_parts)

    def _format_structured_data(self, result: Dict[str, Any]) -> str:
        """Format structured data when no content is available"""
        if not result.get("structured_data"):
            return f"**AI Stack Response**\n\nService: {result.get('module_id', 'Unknown')}\nStatus: {result.get('status', 'Unknown')}"
        
        structured = result["structured_data"]
        
        # Try to extract meaningful content
        content_parts = ["**AI Stack Response**", ""]
        
        if "service" in structured:
            content_parts.append(f"**Service**: {structured['service']}")
        
        if "status" in structured:
            status_icon = "✅" if structured["status"] == "ok" else "❌"
            content_parts.append(f"**Status**: {status_icon} {structured['status'].title()}")
        
        if "message" in structured:
            content_parts.extend(["", structured["message"]])
        
        if "description" in structured:
            content_parts.extend(["", structured["description"]])
        
        return "\n".join(content_parts)

    def _format_debug_metadata(self, result: Dict[str, Any]) -> str:
        """Format debug metadata for development"""
        metadata_parts = ["---", "**Debug Information**", ""]
        
        # Request information
        if result.get("request_id"):
            metadata_parts.append(f"**Request ID**: `{result['request_id'][:8]}...`")
        
        # Module information
        if result.get("module_id"):
            metadata_parts.append(f"**Module**: {result['module_id']}")
        
        # Execution information
        if result.get("diagnostics"):
            diag = result["diagnostics"]
            if "execution_time_ms" in diag:
                metadata_parts.append(f"**Execution Time**: {diag['execution_time_ms']}ms")
            if "adapter" in diag:
                metadata_parts.append(f"**Adapter**: {diag['adapter']}")
        
        # Execution mode
        if result.get("_execution_mode"):
            metadata_parts.append(f"**Execution Mode**: {result['_execution_mode']}")
        
        # Timestamp
        if result.get("timestamp"):
            metadata_parts.append(f"**Timestamp**: {result['timestamp']}")
        
        return "\n".join(metadata_parts)

    def pipe(self, body: dict, __user__: dict) -> str:
        """Main pipe function - entry point for all AI Stack functionality"""
        try:
            # Extract user input
            user_input = self._extract_user_text(body.get("messages", []))
            
            if self.valves.DEBUG_MODE:
                self.logger.debug(f"Processing input: {user_input[:100]}...")
            
            # Create request envelope
            request_envelope = self._create_request_envelope(
                user_input, 
                __user__, 
                {
                    "session": {
                        "conversation_id": body.get("conversation_id"),
                        "turn_number": len(body.get("messages", []))
                    },
                    "context": {
                        "prior_turns": body.get("messages", [])[:-1] if body.get("messages") else []
                    },
                    "attachments": body.get("files", [])
                }
            )
            
            # Execute through refactored router or fallback
            if self.router and self.valves.SCHEMA_VALIDATION:
                result = self._execute_refactored_router(request_envelope)
            else:
                # Convert to legacy payload format
                legacy_payload = {
                    "input": user_input,
                    "user_id": __user__.get("id", "unknown"),
                    "timestamp": request_envelope["timestamp"],
                    "messages": body.get("messages", [])
                }
                
                if self.valves.ENABLE_LEGACY_FALLBACK:
                    result = self._execute_legacy_fallback(legacy_payload)
                else:
                    raise Exception("Refactored router unavailable and legacy fallback disabled")
            
            # Format and return response
            formatted_response = self._format_response(result)
            
            if self.valves.LOG_EXECUTION:
                self.logger.info(f"✅ Successfully processed request for user {__user__.get('id', 'unknown')}")
            
            return formatted_response
            
        except Exception as e:
            error_msg = f"❌ AI Stack pipe execution error: {str(e)}"
            if self.valves.LOG_EXECUTION:
                self.logger.error(error_msg)
            
            # Return user-friendly error
            return f"""**❌ AI Stack Error**

**Message**: {str(e)}

**Troubleshooting:**
• Check that AI Stack containers are running
• Verify GPU access if using GPU-enabled features
• Try the emergency recovery command: "emergency recovery"
• Check the logs for detailed error information

*If the problem persists, contact your AI Stack administrator.*"""

# Pipe metadata
__all__ = ["Pipe"]