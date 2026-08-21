"""
AI Stack Unified Pipe Function for OpenWebUI - Refactored Architecture

A SINGLE pipe function that replaces all individual AI Stack pipe functions.
Intelligent routing to all AI Stack capabilities through one interface; uses
the manifest-driven refactored modules.

SETUP
=====
1. Open WebUI Admin → Functions
2. Create new function (delete any existing AI Stack pipe functions)
3. Paste this ENTIRE file as the function code
4. Save. Test with queries from the COMMAND LIST below.

BENEFITS
========
- Only ONE pipe function needed in OpenWebUI (instead of 4-5 separate functions)
- Intelligent routing based on user input
- Unified management and maintenance
- Uses new manifest-driven architecture

COMMAND LIST
============
Every command this pipe responds to, and the containers each function covers.
Commands are matched by keywords in your message; routing is in status-pipe/router.py.
"★ ALL containers" means every container in the workspace roster (both
compose projects: main `ai-stack` + separate `open-brain`).

─ Stack / server status  ──────────────────────── → tailscale_serve_pipe
  Triggers: status · overview · stack · stack status · stack-status
            status of <service> · status for <service>
  Output:   Full stack view — Containers table, Tailnet URLs, Processing
            detail, LLM gateway panel (LiteLLM · llm-queue: now processing,
            queue depth, top requester in queue, free slots / parallel
            availability, idle time since last request), GPU temp/VRAM panel.
  Coverage: ★ ALL containers across BOTH compose projects (34 services).
            Scopable to any container below:
    core    openwebui · tailscale · llm-gateway · llm-queue · llama-cpp ·
            llama-cpp-embed
    memory  mnemory · mnemory-cloud-gateway · mnemory-backup
    search  vpn · redis · searxng · gateway
    coder   open-terminal · little-coder · lc-egress ·
            little-coder-backup
    aux     surrealdb · open_notebook · openwebui-backup
    OB1     openbrain-db · openbrain-mcp · openbrain-ext · openbrain-mcpo ·
            openbrain-mcpo-ext · openbrain-postgrest · openbrain-rest ·
            openbrain-entity-worker · openbrain-wiki · openbrain-wiki-viewer
  Examples: "status" · "status of mnemory-cloud-gateway" · "status of openbrain-mcpo"

─ System health  ──────────────────────────────── → modules/system-health
  Triggers: system health · health · monitor
  Output:   System Health Report — Docker services, network connectivity,
            AI Stack Services (live HTTP probes grouped by plane), system
            resources, container environment.
  Coverage: 10 probe-backed services across every plane (core, memory,
            search, little-coder, aux, OB1).

─ Tailnet inventory  ──────────────────────────── → tailscale_serve_pipe
  Triggers: inventory · show services · list services · show tailnet ·
            tailnet services · service map · service overview · inventory of X
  Output:   Tailnet-exposed services — host:port, tailnet HTTPS port,
            GPU env mapping, public URL.
  Coverage: Tailnet-served services only (by design — only services exposed
            via `tailscale serve` appear): openwebui · lmstudio · llama-cpp ·
            llama-cpp-embed · open-notebook · open-notebook-api

─ Tailscale serve admin  ──────────────────────── → tailscale_serve_pipe
  Triggers: tailscale serve status · serve status
            start serving <service> · serve <service> ·
              expose <service> [on port N]
            stop serving <service> · unserve <service>
            health check <service> · ping <service>
  Coverage: Tailnet-registered services (same set as Tailnet inventory).
            Internal-only containers (search-net, lc-net, OB1's obnet)
            cannot be tailnet-exposed by design.

─ Emergency recovery  ────────────────────── → modules/emergency-recovery
  Triggers: fix network · fix gpu · rebuild tailscale · restart openwebui ·
            nuclear option · validate gpu · fix lmstudio · recovery · repair
  Output:   Diagnostics + corrective action; for hard issues, the host
            scripts at scripts/emergency-recovery.{ps1,bat}.
  Coverage: ★ ALL containers across both compose projects — graceful
            shutdown in reverse dependency order, dependency-ordered startup,
            OB1 stack brought up after main stack is healthy.

─ GPU status  ────────────────────────────────────── → modules/gpu-status
  Triggers: gpu · gpu status · cuda · graphics · nvidia
  Output:   GPU temperature, VRAM, utilization, per-container assignments
            (3090 Ti aistack-side · 2080 SUPER llama-cpp-side).

─ GPU detail · nvidia-smi check  ────────────────── → modules/gpu-status
  Triggers: smi · nvidia-smi · gpu processes · what is in memory ·
            what's in memory · compute apps · pmon
            Append "gpu 0" / "gpu 1" / "first" / "second" to scope to one GPU.
  Output:   Per-GPU breakdown — utilization · VRAM · temp · power · clocks ·
            encoder/decoder — PLUS the compute-process list (PID, process
            name, VRAM per process). Answers "why is util 99%?" and
            "what's in memory?". Shells to `nvidia-smi`; torch can't see
            other processes' VRAM.
  Examples: "smi" · "nvidia-smi gpu 0" · "what's in memory on gpu 1"

─ LLM traffic · GPU-demand attribution  ─────────── → modules/llm-traffic
  Triggers: llm traffic · who is using gpu · who's using gpu · gpu demand ·
            gpu traffic · llm demand · llm spend · llm cost · gateway traffic ·
            llama traffic
            Append "today" / "last 24h" / "last week" / "since boot" to scope
            the time window (default last 1h).
  Output:   Per-caller breakdown from the LiteLLM gateway spend ledger —
            requests · tokens in/out · failures · avg latency · models ·
            last-seen. In permissive mode the presented key string IS the
            caller identity (friendly-named to services).
  Coverage: Every caller that routes inference through the gateway.

─ Admin help  ──────────────────────────────────── → tailscale_serve_pipe
  Triggers: help · ? · admin help · stack help · tailscale help · commands ·
            what can i do
  Output:   Admin command catalog with example phrasings.

─ LM Studio  ───────────────────────────────────────── → modules/help-system
  Triggers: lmstudio
  Output:   LM Studio integration help.

─ Default fallthrough  ───────────────────────────── → modules/help-system
  Any query not matching the above keywords routes here.

NOTES ON "ALL CONTAINERS"
=========================
- "Scoped status" (`status of <X>`) and "Emergency recovery" cover EVERY
  container in the workspace roster, both compose projects.
- "Tailnet inventory" and `serve start/stop` cover only the tailnet-served
  subset (six services) — internal-only containers (search-net, lc-net,
  OB1's obnet) cannot be exposed on the tailnet by design.
- "System health" probes reachable services over the docker networks; the
  comprehensive per-container view is in `status`.
- Containers in the roster but not currently probed appear in `status` as
  "🗒️ registered (no probe)" — they are tracked, just not deep-introspected.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Dict, List
import json, os, sys, importlib.util
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
            if os.path.exists('/host_project/status-pipe'):
                self.valves.ROUTER_SCRIPT_PATH = "/host_project/status-pipe/router.py"
            else:
                # Host environment - router.py sits next to this orchestrator
                self.valves.ROUTER_SCRIPT_PATH = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "router.py")
        
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
                if os.path.exists('/host_project/status-pipe'):
                    self.valves.ROUTER_SCRIPT_PATH = "/host_project/status-pipe/router.py"
                else:
                    # Host environment - router.py sits next to this orchestrator
                    self.valves.ROUTER_SCRIPT_PATH = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "router.py")
            
            if not os.path.exists(self.valves.ROUTER_SCRIPT_PATH):
                return {
                    "service": "AI Stack Unified Pipe",
                    "status": "error", 
                    "message": f"Router script not found: {self.valves.ROUTER_SCRIPT_PATH}",
                    "help": "Ensure the router.py file exists and is mounted correctly",
                    "debug_info": {
                        "checked_paths": [
                            "/host_project/status-pipe/router.py",
                        ],
                        "host_project_exists": os.path.exists('/host_project'),
                        "current_path": self.valves.ROUTER_SCRIPT_PATH
                    }
                }
                
            # Fresh-load the router from disk on every call so edits to
            # core/router.py take effect immediately without restarting the
            # openwebui container. (`from router import main` would hit the
            # sys.modules cache and pin the first-loaded version.) The module
            # MUST be registered in sys.modules before exec_module — dataclass
            # resolution inside router.py requires self-lookup during class
            # construction.
            router_module_name = "_ai_stack_router_live"
            spec = importlib.util.spec_from_file_location(
                router_module_name, self.valves.ROUTER_SCRIPT_PATH
            )
            if spec is None or spec.loader is None:
                return {
                    "service": "AI Stack Unified Pipe",
                    "status": "error",
                    "message": f"Cannot load router spec from {self.valves.ROUTER_SCRIPT_PATH}",
                }
            router_mod = importlib.util.module_from_spec(spec)
            sys.modules[router_module_name] = router_mod  # register first
            try:
                spec.loader.exec_module(router_mod)  # then exec — reads fresh from disk
            except Exception:
                sys.modules.pop(router_module_name, None)
                raise

            if not hasattr(router_mod, "main"):
                return {
                    "service": "AI Stack Unified Pipe",
                    "status": "error",
                    "message": "router.py is missing a top-level main(payload) function",
                }

            # Execute router main function
            result = router_mod.main(payload)
            
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
            if result.get("module_id") in ["system-health", "gpu-status", "emergency-recovery", "custom-tools", "help-system", "system-orchestrator", "llm-traffic"] and "content" in result:
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