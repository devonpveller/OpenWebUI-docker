#!/usr/bin/env python3
"""
Tailscale Serve Admin Pipe Function
OpenWebUI integration for AI Stack tailnet service management.

Covers the current ai-stack roster:
  - openwebui            (front door, /)              GPU: GPU_AISTACK_DEVICE_ID
  - lmstudio             (host LM Studio, /lmstudio)  GPU: host
  - llama-cpp            (llama-swap CUDA, /llama-cpp)        GPU: GPU_LLAMA_CPP_DEVICE_ID
  - llama-cpp-embed      (BGE-M3 embeddings, /llama-cpp-embed) GPU: GPU_LLAMA_CPP_EMBED_DEVICE_ID
  - open-notebook        (Streamlit UI, HTTPS 8443/)  GPU: cpu
  - open-notebook-api    (REST API, HTTPS 5055/api)   GPU: cpu

Recognized phrasings (a few examples):
  "show tailnet services"
  "inventory"
  "status of llama-cpp"
  "health check open-notebook"
  "start serving llama-cpp-embed on port 8080"
  "stop serving lmstudio"
"""

import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Service registry — single source of truth for the pipe.
# Defaults mirror docker-compose.yml + entrypoint.sh + .env.example.
# ---------------------------------------------------------------------------

SERVICE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "openwebui": {
        "aliases": ["openwebui", "open-webui", "open webui", "owui", "webui"],
        "path": "",  # served at root
        "target_host": "127.0.0.1",
        "target_port": 8080,
        "health_path": "/health",
        "tailnet_https_port": 443,
        "exposes_at": "/",
        "container": "openwebui",
        "gpu_device_env": "GPU_AISTACK_DEVICE_ID",
        "gpu_device_default": "1",
        "description": "OpenWebUI front door",
    },
    "lmstudio": {
        "aliases": ["lmstudio", "lm studio", "lm-studio"],
        "path": "lmstudio",
        "target_host": "host.docker.internal",
        "target_port": 1234,
        "health_path": "/v1/models",
        "tailnet_https_port": 443,
        "exposes_at": "/lmstudio",
        "container": "host (LM Studio desktop app)",
        "gpu_device_env": None,
        "gpu_device_default": None,
        "description": "LM Studio OpenAI-compatible API (host)",
    },
    "llama-cpp": {
        "aliases": ["llama-cpp", "llamacpp", "llama cpp", "llama-swap", "llama"],
        "path": "llama-cpp",
        "target_host": "llama-cpp",
        "target_port": 8080,
        "health_path": "/health",
        "tailnet_https_port": 443,
        "exposes_at": "/llama-cpp",
        "container": "llama-cpp (llama-swap, CUDA)",
        "gpu_device_env": "GPU_LLAMA_CPP_DEVICE_ID",
        "gpu_device_default": "0",
        "description": "llama-swap CUDA inference (Qwen3.6-35B / 27B)",
    },
    "llama-cpp-embed": {
        "aliases": ["llama-cpp-embed", "llama-embed", "embeddings", "embed"],
        "path": "llama-cpp-embed",
        "target_host": "llama-cpp-embed",
        "target_port": 8080,
        "health_path": "/health",
        "tailnet_https_port": 443,
        "exposes_at": "/llama-cpp-embed",
        "container": "llama-cpp-embed",
        "gpu_device_env": "GPU_LLAMA_CPP_EMBED_DEVICE_ID",
        "gpu_device_default": "1",
        "description": "BGE-M3 embeddings server (CUDA)",
    },
    "open-notebook": {
        "aliases": ["open-notebook", "open notebook", "open_notebook", "notebook"],
        "path": "",  # Streamlit hosts at root on a dedicated HTTPS port
        "target_host": "open_notebook",
        "target_port": 8502,
        "health_path": "/",
        "tailnet_https_port": 8443,
        "exposes_at": "/",
        "container": "open_notebook",
        "gpu_device_env": None,
        "gpu_device_default": None,
        "description": "Open Notebook Streamlit UI",
    },
    "open-notebook-api": {
        "aliases": [
            "open-notebook-api", "open notebook api", "notebook-api",
            "notebook api", "on-api",
        ],
        "path": "",
        "target_host": "open_notebook",
        "target_port": 5055,
        "health_path": "/api/config",
        "tailnet_https_port": 5055,
        "exposes_at": "/api",
        "container": "open_notebook",
        "gpu_device_env": None,
        "gpu_device_default": None,
        "description": "Open Notebook REST API (SurrealDB-backed)",
    },
}


def resolve_service(user_input: str) -> Optional[str]:
    """Match the input against the registry. Longest alias wins."""
    text = user_input.lower()
    matches: List[Tuple[int, str]] = []
    for key, info in SERVICE_REGISTRY.items():
        for alias in info["aliases"]:
            if alias in text:
                matches.append((len(alias), key))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the Tailscale Serve Admin pipe."""
    try:
        user_input = payload.get("input", "") or ""
        action, params = parse_user_input(user_input)

        if action == "inventory":
            return build_inventory()

        if not action:
            return {
                "status": "error",
                "message": "Could not determine action from input",
                "suggestion": (
                    "Try: 'show tailnet services', 'status of llama-cpp', "
                    "'health check open-notebook', or 'start serving llama-cpp-embed'."
                ),
            }

        result = execute_tailscale_admin(action, params)
        return format_response(result, params)

    except Exception as e:
        return {
            "status": "error",
            "message": f"Tailscale Serve Admin error: {e}",
        }


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def parse_user_input(user_input: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Parse natural language input into (action, params)."""
    text = user_input.lower().strip()

    if not text:
        return None, {}

    # Inventory / overview keywords
    if any(
        kw in text
        for kw in (
            "inventory", "services list", "list services",
            "show tailnet", "tailnet services", "what services",
            "show services", "service map", "service overview",
        )
    ):
        return "inventory", {}

    if any(kw in text for kw in ("start", "serve", "enable", "expose")):
        return "serve_start", _params_for_service(text, include_target=True)

    if any(kw in text for kw in ("stop", "disable", "remove", "unserve")):
        return "serve_stop", _params_for_service(text, include_target=False)

    if "health" in text or "ping" in text:
        return "health", _params_for_service(text, include_target=False)

    if any(kw in text for kw in ("status", "list", "show")):
        return "status", _params_for_service(text, include_target=False)

    return None, {}


def _params_for_service(text: str, include_target: bool) -> Dict[str, Any]:
    """Build params for a tailscale_serve_admin invocation."""
    service_key = resolve_service(text)
    params: Dict[str, Any] = {}

    if service_key:
        info = SERVICE_REGISTRY[service_key]
        params["service"] = service_key
        if info["path"]:
            params["path"] = info["path"]
        if include_target:
            params["target_host"] = info["target_host"]
            params["target_port"] = info["target_port"]
            params["health_path"] = info["health_path"]
    else:
        # Best-effort path extraction for free-form requests
        path_match = re.search(r"(?:at|path)\s+/?([\w\-]+)", text)
        if path_match:
            params["path"] = path_match.group(1)

    # Explicit port override always wins
    port_match = re.search(r"port\s+(\d+)", text)
    if port_match:
        params["target_port"] = int(port_match.group(1))

    return params


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def build_inventory() -> Dict[str, Any]:
    """Return the current admin-server view: tailnet exposure + GPU + container."""
    services = []
    for key, info in SERVICE_REGISTRY.items():
        gpu_env = info.get("gpu_device_env")
        gpu_id = (
            os.environ.get(gpu_env, info.get("gpu_device_default"))
            if gpu_env
            else None
        )
        services.append({
            "service": key,
            "description": info["description"],
            "container": info["container"],
            "tailnet_https_port": info["tailnet_https_port"],
            "exposes_at": info["exposes_at"],
            "target": f"{info['target_host']}:{info['target_port']}",
            "health_path": info["health_path"],
            "gpu_device_env": gpu_env,
            "gpu_device_id": gpu_id,
        })

    return {
        "status": "success",
        "action": "inventory",
        "service_count": len(services),
        "services": services,
        "message": _format_inventory_message(services),
    }


def _format_inventory_message(services: List[Dict[str, Any]]) -> str:
    lines = ["**AI Stack tailnet services**", ""]
    lines.append("| Service | Tailnet | Target | GPU | Container |")
    lines.append("|---|---|---|---|---|")
    for s in services:
        gpu = (
            f"{s['gpu_device_env']}={s['gpu_device_id']}"
            if s["gpu_device_env"]
            else "—"
        )
        tailnet = f"HTTPS {s['tailnet_https_port']}{s['exposes_at']}"
        lines.append(
            f"| `{s['service']}` | {tailnet} | `{s['target']}` | {gpu} | {s['container']} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subprocess dispatch to tailscale_serve_admin CLI
# ---------------------------------------------------------------------------

def execute_tailscale_admin(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the tailscale_serve_admin CLI with the given action/params."""
    if os.path.exists("/host_project/modules"):
        admin_script = "/host_project/modules/custom-tools/service/tailscale_serve_admin.py"
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        admin_script = os.path.join(
            project_root, "modules", "custom-tools", "service",
            "tailscale_serve_admin.py",
        )

    cmd = ["python", admin_script, "--action", action]

    cli_keys = {
        "path": "--path",
        "target_host": "--target_host",
        "target_port": "--target_port",
        "health_path": "--health_path",
    }
    for key, flag in cli_keys.items():
        if key in params and params[key] not in (None, ""):
            cmd.extend([flag, str(params[key])])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return json.loads(result.stdout)
        return {
            "success": False,
            "error_code": "EXECUTION_FAILED",
            "message": result.stderr or "Command execution failed",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error_code": "TIMEOUT",
            "message": "Command execution timed out",
        }
    except Exception as e:
        return {
            "success": False,
            "error_code": "UNKNOWN_ERROR",
            "message": str(e),
        }


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

def format_response(result: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Format admin CLI output for OpenWebUI display."""
    service_key = params.get("service")
    service_info = SERVICE_REGISTRY.get(service_key) if service_key else None

    if result.get("success"):
        response = {
            "status": "success",
            "message": result.get("summary", "Operation completed successfully"),
        }

        if service_info:
            gpu_env = service_info.get("gpu_device_env")
            gpu_id = (
                os.environ.get(gpu_env, service_info.get("gpu_device_default"))
                if gpu_env
                else None
            )
            gpu_line = (
                f"\n🎮 **GPU**: `{gpu_env}={gpu_id}`" if gpu_env else ""
            )
            response["message"] += (
                f"\n\n📦 **Service**: `{service_key}` "
                f"({service_info['description']})"
                f"\n🐳 **Container**: `{service_info['container']}`"
                f"{gpu_line}"
                f"\n🌐 **Tailnet**: HTTPS {service_info['tailnet_https_port']}"
                f"{service_info['exposes_at']}"
                f"\n🎯 **Target**: `{service_info['target_host']}:"
                f"{service_info['target_port']}`"
            )

        details = result.get("details", {})
        if "serve_url" in details:
            response["url"] = details["serve_url"]
            response["message"] += f"\n\n🔗 **Access URL**: {details['serve_url']}"
        if "path_map" in details:
            response["message"] += "\n\n**Path Mappings:**"
            for path, target in details["path_map"].items():
                response["message"] += f"\n- `{path}` → `{target}`"
        if "health_status" in details:
            emoji = "✅" if details["health_status"] == "healthy" else "⚠️"
            response["message"] += f"\n\n{emoji} **Health**: {details['health_status']}"
        if "paths" in details:
            response["message"] += "\n\n**Currently Served Paths:**"
            for path, target in details["paths"].items():
                response["message"] += f"\n- `{path}` → `{target}`"
        return response

    error_code = result.get("error_code", "UNKNOWN_ERROR")
    message = result.get("message", "Unknown error occurred")
    response = {
        "status": "error",
        "error_code": error_code,
        "message": f"❌ **Error**: {message}",
    }

    suggestions = {
        "TAILSCALE_NOT_READY":
            "Try: 'fix namespace' or restart the Tailscale container.",
        "AUTH_REQUIRED":
            "Tailscale auth required — refresh TAILSCALE_AUTH_KEY in .env.",
        "TARGET_UNREACHABLE":
            "Ensure the target service container is running and healthy "
            "(docker compose ps).",
        "SERVE_CONFLICT":
            "That path is already being served. Use 'stop serving …' first.",
    }
    if error_code in suggestions:
        response["suggestion"] = suggestions[error_code]
    return response


if __name__ == "__main__":
    if len(sys.argv) > 1:
        payload_str = sys.argv[1]
    else:
        payload_str = sys.stdin.read()

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        payload = {"input": payload_str}

    result = main(payload)
    print(json.dumps(result, indent=2))
