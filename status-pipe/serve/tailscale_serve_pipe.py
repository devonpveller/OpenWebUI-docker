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
  "show tailnet services"           -> inventory (registry + GPU env vars)
  "status"                          -> stack status (containers + GPU + processing
                                       + LLM gateway queue metrics)
  "status of llama-cpp"             -> stack status filtered to one service
  "health check open-notebook"      -> tailscale-side health check
  "tailscale serve status"          -> tailscale serve config dump
  "start serving llama-cpp-embed on port 8080"
  "stop serving lmstudio"
"""

import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


HTTP_TIMEOUT = 4
HTTP_TIMEOUT_SLOW = 8  # for services with slow cold-start (Streamlit/uvicorn)
NVIDIA_SMI_TIMEOUT = 8
DOCKER_PS_TIMEOUT = 10

# LiteLLM gateway — the inference front door on llm-net. The ONLY side-effect-
# free surfaces reachable from the openwebui container are /health/liveliness
# and the read-only /observe/* pass-throughs to the llm-queue admission
# controller. NEVER GET /health on the gateway: with background_health_checks
# off, LiteLLM's /health fires a LIVE health-check completion at every
# registered model, which forces a llama-swap model load (swap thrash — see
# config/litellm.config.yaml). No host publish (llm-net is internal), so host
# runs report the panel as unreachable; that is expected.
LLM_GATEWAY_URL = os.environ.get("LLM_GATEWAY_URL", "http://llm-gateway:8080").rstrip("/")


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
        # llama-cpp is a network ALIAS on llm-gateway (LiteLLM) since 2026-06-12.
        # Must be /health/liveliness: LiteLLM's /health actively health-checks
        # every registered model (forces a llama-swap load -> thrash).
        "health_path": "/health/liveliness",
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
        # Gateway alias too (see llama-cpp above) — liveliness only.
        "health_path": "/health/liveliness",
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


# ---------------------------------------------------------------------------
# Full container roster — every service across BOTH compose projects: the main
# ai-stack project (docker-compose.yml) and the separate Open Brain / OB1
# project (OB1/docker/docker-compose.yml). Drives the `status` container
# table. Keep in sync with the compose files — see the /stack-map skill.
# ---------------------------------------------------------------------------
_STACK_ROSTER: List[str] = [
    # main project — core (llm-gateway = LiteLLM front door; llm-queue = B2
    # admission controller on llm-backend-net, observed via the gateway's
    # read-only /observe/* pass-through)
    "openwebui", "tailscale", "llm-gateway", "llm-queue",
    "llama-cpp", "llama-cpp-embed",
    # main project — memory layer
    "mnemory", "mnemory-cloud-gateway", "mnemory-backup",
    # main project — Private Search Gateway
    "vpn", "redis", "searxng", "gateway",
    # main project — little-coder control plane
    "open-terminal", "little-coder", "lc-egress", "little-coder-backup",
    # main project — auxiliary
    "smolcrawl-pipelines", "surrealdb", "open_notebook", "openwebui-backup",
    # Open Brain (OB1) — separate compose project (project name "open-brain")
    "openbrain-db", "openbrain-mcp", "openbrain-ext", "openbrain-mcpo",
    "openbrain-mcpo-ext", "openbrain-postgrest", "openbrain-rest",
    "openbrain-entity-worker", "openbrain-wiki", "openbrain-wiki-viewer",
]


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

        if action == "help":
            return build_help()

        if action == "inventory":
            return build_inventory()

        if action == "stack_status":
            return build_stack_status(params.get("service"))

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

    # Help / discovery keywords
    if (
        text in {"help", "commands", "?", "admin help", "stack help"}
        or any(
            phrase in text
            for phrase in (
                "admin help", "stack help", "tailscale help",
                "what commands", "available commands", "list commands",
                "show commands", "what can i do", "what can you do",
                "admin commands", "stack commands",
            )
        )
    ):
        return "help", {}

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

    # "tailscale serve status" / "show serve config" / etc. — keep going to the
    # tailscale_serve_admin CLI, since that surfaces the live tailnet serve table.
    serve_status_request = (
        ("serve" in text and any(kw in text for kw in ("status", "list", "show")))
        or ("tailscale" in text and any(kw in text for kw in ("status", "list", "show")))
    )
    if serve_status_request and not any(
        kw in text for kw in ("start", "stop", "enable", "disable", "remove")
    ):
        return "status", _params_for_service(text, include_target=False)

    if any(kw in text for kw in ("start", "enable", "expose")) or (
        "serve" in text and not serve_status_request
    ):
        return "serve_start", _params_for_service(text, include_target=True)

    if any(kw in text for kw in ("stop", "disable", "remove", "unserve")):
        return "serve_stop", _params_for_service(text, include_target=False)

    if "health" in text or "ping" in text:
        return "health", _params_for_service(text, include_target=False)

    # Bare "status" / "show" / "list" → stack-wide status (containers + GPU + jobs)
    if any(kw in text for kw in ("status", "list", "show", "overview")):
        params = _params_for_service(text, include_target=False)
        if "service" not in params:
            scope = _resolve_stack_scope(text)
            if scope:
                params["service"] = scope
        return "stack_status", params

    return None, {}


# Names recognized for status scoping ("status of X"). Generated from the
# stack roster so every container is matchable by its own name (and
# hyphen/underscore-spaced variants), plus a few informal aliases.
def _build_scope_aliases() -> Dict[str, List[str]]:
    aliases: Dict[str, List[str]] = {}
    for svc in _STACK_ROSTER:
        variants = {svc, svc.replace("-", " "), svc.replace("_", " ")}
        aliases[svc] = sorted(variants, key=len, reverse=True)
    extras: Dict[str, List[str]] = {
        "smolcrawl-pipelines": ["smolcrawl", "smol crawl", "pipelines"],
        "mnemory":             ["memory service"],
        "open_notebook":       ["notebook"],
        "surrealdb":           ["surreal", "surreal db"],
        "open-terminal":       ["terminal"],
        "lc-egress":           ["egress"],
        "gateway":             ["search gateway"],
        "llm-gateway":         ["litellm", "lite llm"],
        "llm-queue":           ["inference queue"],
        "openwebui-backup":    ["owui-backup"],
        "openbrain-mcp":       ["openbrain", "open brain", "ob1"],
    }
    for svc, extra in extras.items():
        aliases.setdefault(svc, []).extend(extra)
    return aliases


_STACK_SCOPE_ALIASES: Dict[str, List[str]] = _build_scope_aliases()


def _resolve_stack_scope(text: str) -> Optional[str]:
    """Match input against stack-level aliases (docker service names)."""
    matches: List[Tuple[int, str]] = []
    for canonical, aliases in _STACK_SCOPE_ALIASES.items():
        for alias in aliases:
            if alias in text:
                matches.append((len(alias), canonical))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


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

# ---------------------------------------------------------------------------
# Help — discoverability for admin commands
# ---------------------------------------------------------------------------

_HELP_COMMANDS: List[Dict[str, Any]] = [
    {
        "name": "Stack status",
        "description": (
            "Itemized container roster + per-service processing detail + LLM "
            "gateway queue metrics (now processing, queue depth, top requester, "
            "free slots, idle time) + GPU temp/VRAM panel."
        ),
        "phrases": ["status", "show", "overview", "stack status"],
    },
    {
        "name": "Scoped status",
        "description": "Filter the stack status to one service.",
        "phrases": [
            "status of llama-cpp", "status of llm-gateway",
            "status of smolcrawl", "status of mnemory",
            "status of open-notebook", "status of surrealdb",
        ],
    },
    {
        "name": "Tailnet inventory",
        "description": "Tailnet-exposed services with target host:port, tailnet HTTPS port, and GPU env mapping.",
        "phrases": ["inventory", "show services", "list services", "show tailnet services"],
    },
    {
        "name": "Tailscale serve status",
        "description": "Live `tailscale serve` config dump (currently served paths). Requires the tailscale CLI.",
        "phrases": ["tailscale serve status", "serve status"],
    },
    {
        "name": "Health check",
        "description": "Probe one service's health endpoint via the tailscale daemon.",
        "phrases": [
            "health check llama-cpp", "health check open-notebook",
            "ping llama-cpp-embed",
        ],
    },
    {
        "name": "Start serving",
        "description": "Add a tailscale serve mapping for a registered service. Optional `port N` overrides the registry default.",
        "phrases": [
            "start serving llama-cpp", "serve lmstudio",
            "expose llama-cpp-embed on port 8080",
        ],
    },
    {
        "name": "Stop serving",
        "description": "Remove an existing tailscale serve mapping.",
        "phrases": ["stop serving lmstudio", "unserve llama-cpp"],
    },
    {
        "name": "Help",
        "description": "Show this list.",
        "phrases": ["help", "admin help", "commands", "what can i do"],
    },
]


def build_help() -> Dict[str, Any]:
    """Return the admin command catalog with example phrasings."""
    return {
        "status": "success",
        "action": "help",
        "commands": _HELP_COMMANDS,
        "message": _format_help_message(_HELP_COMMANDS),
    }


def _format_help_message(commands: List[Dict[str, Any]]) -> str:
    lines = [
        "**AI Stack admin server — available commands**",
        "",
        "Type any of the example phrasings below. Service names are matched "
        "by longest-alias, so most natural phrasings work.",
        "",
    ]
    for cmd in commands:
        lines.append(f"### {cmd['name']}")
        lines.append(cmd["description"])
        lines.append("")
        lines.append("Examples:")
        for phrase in cmd["phrases"]:
            lines.append(f"- `{phrase}`")
        lines.append("")
    lines.append(
        "_Service registry covers: openwebui, lmstudio, llama-cpp, "
        "llama-cpp-embed, open-notebook, open-notebook-api. "
        "Stack scope adds smolcrawl, mnemory, surrealdb, tailscale, "
        "open-terminal._"
    )
    return "\n".join(lines)


def build_inventory() -> Dict[str, Any]:
    """Return the current admin-server view: tailnet exposure + GPU + container."""
    identity = _resolve_tailnet_identity()
    fqdn = identity.get("fqdn")

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
            "tailnet_url": _build_tailnet_url(info, fqdn),
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
        "tailnet_identity": identity,
        "message": _format_inventory_message(services, identity),
    }


# ---------------------------------------------------------------------------
# Tailnet identity discovery (FQDN, MagicDNS suffix)
# ---------------------------------------------------------------------------

# Paths we'll try to read tailnet-info.json from, in priority order.
# The tailscale container writes this file (see entrypoint.sh).
_TAILNET_INFO_CANDIDATES = [
    "/host_project/data/tailscale/tailnet-info.json",  # inside openwebui
    "/var/lib/tailscale/tailnet-info.json",            # inside tailscale container
]


def _read_tailnet_info() -> Optional[Dict[str, Any]]:
    """Read the tailnet-info.json dump produced by the tailscale entrypoint."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = list(_TAILNET_INFO_CANDIDATES) + [
        # Host-side fallback for development: <repo>/data/tailscale/tailnet-info.json
        os.path.join(here, "..", "..", "data", "tailscale", "tailnet-info.json"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError, PermissionError):
            continue
    return None


def _resolve_tailnet_identity() -> Dict[str, Optional[str]]:
    """Resolve the tailnet FQDN and MagicDNS suffix.

    Strategy: read tailnet-info.json (written by tailscale container) first;
    fall back to TAILNET_DOMAIN + TS_HOSTNAME env vars; finally None when
    nothing is known (renderer shows a placeholder + remediation hint).
    """
    info = _read_tailnet_info()
    if info:
        self_node = info.get("Self") or {}
        dns_name = (self_node.get("DNSName") or "").rstrip(".")
        if dns_name:
            magic = info.get("MagicDNSSuffix") or info.get("CurrentTailnet", {}).get("MagicDNSSuffix")
            return {
                "fqdn": dns_name,
                "magic_dns_suffix": magic.rstrip(".") if magic else None,
                "source": "tailnet-info.json",
            }

    domain = os.environ.get("TAILNET_DOMAIN")
    hostname = os.environ.get("TS_HOSTNAME", "openwebui")
    if domain:
        return {
            "fqdn": f"{hostname}.{domain.lstrip('.').rstrip('.')}",
            "magic_dns_suffix": domain.lstrip(".").rstrip("."),
            "source": "TAILNET_DOMAIN env",
        }

    return {"fqdn": None, "magic_dns_suffix": None, "source": None}


def _build_tailnet_url(service_info: Dict[str, Any], fqdn: Optional[str]) -> str:
    """Construct the public tailnet URL for a registered service."""
    host_part = fqdn if fqdn else "<your-tailnet-host>"
    port = service_info.get("tailnet_https_port", 443)
    path = service_info.get("exposes_at") or "/"
    if port == 443:
        return f"https://{host_part}{path}"
    return f"https://{host_part}:{port}{path}"


def _build_tailnet_urls(scope_service: Optional[str] = None) -> Dict[str, Any]:
    """Build the tailnet URL list for all registered services.

    Returns the resolved identity (fqdn, source) and a list of {service, url}
    entries, one per service in SERVICE_REGISTRY (filtered to scope when set).
    """
    identity = _resolve_tailnet_identity()
    fqdn = identity.get("fqdn")
    entries = []
    for key, info in SERVICE_REGISTRY.items():
        if scope_service and scope_service not in (key, _registry_alias_for(key)):
            continue
        entries.append({
            "service": key,
            "description": info["description"],
            "url": _build_tailnet_url(info, fqdn),
            "tailnet_https_port": info["tailnet_https_port"],
        })
    return {
        "fqdn": fqdn,
        "source": identity.get("source"),
        "magic_dns_suffix": identity.get("magic_dns_suffix"),
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Stack status: containers + per-service processing detail + GPU panel
# ---------------------------------------------------------------------------

# Containers we know how to introspect beyond docker's own state. Each probe
# carries the in-compose hostname/port AND (where one exists) a host-side
# localhost fallback (the published port from docker-compose.yml). The pipe
# tries the internal address first; if it isn't reachable (e.g. running on the
# host instead of inside openwebui), it falls back to the published port.
# Probes use urllib so the pipe stays dependency-free.
#
# Only services REACHABLE from the openwebui container get a probe (openwebui
# is on default + llm-net). Containers on the internal-only search-net
# (vpn/redis/searxng) or OB1's obnet have no probe — they still appear in
# the container table (from _STACK_ROSTER) as "registered".
_PROBES: Dict[str, Dict[str, Any]] = {
    # llama-cpp / llama-cpp-embed are network ALIASES on llm-gateway (LiteLLM)
    # since 2026-06-12 — these hostnames no longer reach a llama.cpp server, so
    # the old llama_slots probe (GET /health + /slots) is both wrong and UNSAFE:
    # LiteLLM's /health fires a live health-check completion at every registered
    # model (llama-swap load thrash), and /slots 404s at the gateway. Probe
    # liveliness only; live inference activity now comes from the LLM gateway
    # panel (_llm_gateway_panel, /observe/queue). The real servers
    # (*-upstream) are isolated on llm-backend-net — unreachable from openwebui
    # by design (host-side recovery scripts probe 127.0.0.1:8081 directly).
    "llama-cpp": {
        "host": "llama-cpp", "port": 8080,
        "kind": "llm_gateway_alias",
    },
    "llama-cpp-embed": {
        "host": "llama-cpp-embed", "port": 8080,
        "kind": "llm_gateway_alias",
    },
    # LiteLLM analytics front door itself (same container the aliases land on).
    "llm-gateway": {
        "host": "llm-gateway", "port": 8080,
        "kind": "http_health", "health_path": "/health/liveliness",
    },
    # B2 admission controller — llm-backend-net only, so probed THROUGH the
    # gateway's read-only pass-through: a 200 from /observe/queue proves
    # llm-queue itself answered. (Gateway down => shows unreachable too.)
    "llm-queue": {
        "host": "llm-gateway", "port": 8080,
        "kind": "http_health", "health_path": "/observe/queue",
    },
    "smolcrawl-pipelines": {
        "host": "smolcrawl-pipelines", "port": 9099,
        "host_fallback": "127.0.0.1", "host_fallback_port": 9099,
        "kind": "http_root",
    },
    "mnemory": {
        "host": "mnemory", "port": 8051,
        "host_fallback": "127.0.0.1", "host_fallback_port": 8051,
        "kind": "http_health",
    },
    "mnemory-cloud-gateway": {
        "host": "mnemory-cloud-gateway", "port": 8060,
        "host_fallback": "127.0.0.1", "host_fallback_port": 8060,
        "kind": "http_health",
    },
    "open_notebook": {
        "host": "open_notebook", "port": 5055,
        "host_fallback": "127.0.0.1", "host_fallback_port": 5055,
        "kind": "open_notebook",
        # Streamlit/uvicorn can take >4s to respond cold, especially on
        # the API side (which is uvicorn-backed). Give it more headroom.
        "timeout": HTTP_TIMEOUT_SLOW,
    },
    # Private Search Gateway — `gateway` also joins `default`, so it is the
    # one search-stack service reachable from openwebui. Health is /healthz.
    "gateway": {
        "host": "gateway", "port": 8080,
        "host_fallback": "127.0.0.1", "host_fallback_port": 8085,
        "kind": "http_health", "health_path": "/healthz",
    },
    # little-coder control plane — reachable over llm-net. No host-published
    # ports for the daemon/edges, so no fallback (host runs show unreachable).
    "open-terminal": {
        "host": "open-terminal", "port": 8000,
        "kind": "http_health",
    },
    "little-coder": {
        "host": "little-coder", "port": 8090,
        "kind": "http_health",
    },
    # Open Brain (OB1) — openbrain-mcpo joins ai-stack_llm-net, so the MCP
    # bridge is reachable; its health endpoint is the core OpenAPI doc.
    "openbrain-mcpo": {
        "host": "openbrain-mcpo", "port": 8000,
        "kind": "http_health", "health_path": "/open-brain/openapi.json",
    },
}


def build_stack_status(scope_service: Optional[str] = None) -> Dict[str, Any]:
    """Comprehensive AI Stack status: containers + processing detail
    + LLM gateway queue metrics + GPUs."""
    containers = _docker_compose_ps()
    gpus = _nvidia_smi_panel()
    tailnet = _build_tailnet_urls(scope_service)

    # LiteLLM / llm-queue live metrics — stack-wide view, or when scoped to a
    # service on the inference plane.
    litellm: Optional[Dict[str, Any]] = None
    if scope_service is None or scope_service in _LLM_PLANE_SCOPES:
        litellm = _llm_gateway_panel()

    # Per-container "is it processing" detail. Only probe containers that are
    # actually running (per docker), or — when docker isn't reachable — every
    # known probe target.
    running_names = {c["service"] for c in containers["entries"] if c["state"] == "running"} \
        if containers["available"] else set(_PROBES.keys())

    processing = {}
    for svc, probe in _PROBES.items():
        if scope_service and scope_service not in (svc, _registry_alias_for(svc)):
            continue
        if containers["available"] and svc not in running_names:
            processing[svc] = {"status": "not_running"}
            continue
        processing[svc] = _probe_processing(probe)

    # If docker CLI isn't available (typical inside openwebui container),
    # synthesize a container roster from the registry + probe results so the
    # user still sees an itemized list rather than just an unavailability note.
    if not containers["available"]:
        containers = _registry_container_roster(processing)

    return {
        "status": "success",
        "action": "stack_status",
        "scope": scope_service or "all",
        "containers": containers,
        "processing": processing,
        "litellm": litellm,
        "gpus": gpus,
        "tailnet": tailnet,
        "message": _format_stack_status_message(
            containers, processing, litellm, gpus, tailnet, scope_service
        ),
    }


def _registry_container_roster(processing: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build the full container roster from _STACK_ROSTER + probe outcomes.

    Used when `docker compose ps` is unavailable (the usual case inside the
    openwebui container, which has no docker socket). Every container in both
    compose projects is listed; probe-backed services get live state inferred
    from their HTTP probe, the rest show as "registered".
    """
    entries = []
    for svc in _STACK_ROSTER:
        proc = processing.get(svc)
        state, health = _infer_state_from_probe(proc)
        entries.append({
            "service": svc,
            "name": svc,
            "state": state,
            "status": _humanize_probe_state(proc),
            "health": health,
            "image": "—",
        })
    entries.sort(key=lambda e: e["service"])
    return {
        "available": True,
        "source": "registry",
        "note": "docker CLI unavailable — listing the full workspace roster; "
                "state inferred from HTTP probes where reachable",
        "entries": entries,
    }


def _infer_state_from_probe(proc: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Translate a probe result into (state, health) for the container table.

    Any responsive service (processing/idle/ready, including the model_unloaded
    sub-state of idle) shows as `running / healthy` so similar states land in
    the same column. Activity nuance lives in the Notes column.
    """
    if proc is None:
        return ("registered", "—")
    status = proc.get("status")
    if status in ("processing", "idle", "ready", "model_unloaded"):
        return ("running", "healthy")
    if status == "degraded":
        return ("running", "degraded")
    if status == "unreachable":
        return ("unreachable", "—")
    if status == "not_running":
        return ("exited", "—")
    return ("registered", "—")


def _humanize_probe_state(proc: Optional[Dict[str, Any]]) -> str:
    """Short, human-readable activity string for the container Notes column."""
    if proc is None:
        return "in registry (no probe)"
    status = proc.get("status", "unknown")

    if status == "processing":
        active = proc.get("slots_active", 0)
        total = proc.get("slots_total", 0)
        return f"processing ({active}/{total} slots active)"
    if status == "idle":
        total = proc.get("slots_total")
        return f"idle ({total} slots)" if total else "idle"
    if status == "model_unloaded":
        return "idle (no active model)"
    if status == "ready":
        return "ready"
    if status == "degraded":
        return "degraded"
    if status == "unreachable":
        err = proc.get("error")
        return f"unreachable ({err})" if err else "unreachable"
    if status == "not_running":
        return "not running"
    return status


def _registry_alias_for(docker_service: str) -> Optional[str]:
    """Map docker-compose service names to registry keys where they differ."""
    return {
        "open_notebook": "open-notebook",
        "smolcrawl-pipelines": "smolcrawl",
    }.get(docker_service, docker_service)


# --- Container roster -----------------------------------------------------

def _docker_compose_ps() -> Dict[str, Any]:
    """Run `docker compose ps` for BOTH compose projects and merge.

    The main ai-stack project plus the separate Open Brain (OB1) project, so
    the container table covers the whole workspace when the docker CLI is
    available. Falls back gracefully when it is not.
    """
    workspace = _resolve_workspace_root()
    main = _compose_ps_one(workspace, None)
    if not main["available"]:
        return main

    entries = list(main["entries"])
    # Open Brain (OB1) is a separate compose project — pull it via -f.
    ob1_file = os.path.join(workspace, "OB1", "docker", "docker-compose.yml")
    if os.path.exists(ob1_file):
        ob1 = _compose_ps_one(workspace, ob1_file)
        if ob1["available"]:
            entries.extend(ob1["entries"])

    entries.sort(key=lambda e: e["service"])
    return {"available": True, "entries": entries}


def _compose_ps_one(workspace: str, compose_file: Optional[str]) -> Dict[str, Any]:
    """Run `docker compose ps --format json` for one project (None = main)."""
    cmd = ["docker", "compose"]
    if compose_file:
        cmd += ["-f", compose_file]
    cmd += ["ps", "--format", "json"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=DOCKER_PS_TIMEOUT, cwd=workspace,
        )
    except FileNotFoundError:
        return {"available": False, "reason": "docker CLI not available in this environment", "entries": []}
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "docker compose ps timed out", "entries": []}
    except Exception as e:
        return {"available": False, "reason": f"docker compose ps failed: {e}", "entries": []}

    if result.returncode != 0:
        return {
            "available": False,
            "reason": (result.stderr or "docker compose ps returned non-zero").strip(),
            "entries": [],
        }

    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append({
            "service": obj.get("Service", "?"),
            "name": obj.get("Name", "?"),
            "state": obj.get("State", "?"),
            "status": obj.get("Status", "?"),
            "health": obj.get("Health", "") or "—",
            "image": obj.get("Image", "?"),
        })
    return {"available": True, "entries": entries}


def _resolve_workspace_root() -> str:
    """Best-effort detection of the ai-stack project root."""
    if os.path.exists("/host_project/docker-compose.yml"):
        return "/host_project"
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.dirname(os.path.dirname(here))  # scripts/ai_pipes -> repo root
    if os.path.exists(os.path.join(candidate, "docker-compose.yml")):
        return candidate
    return os.getcwd()


# --- Per-service processing probes ----------------------------------------

def _http_get(
    url: str, timeout: int = HTTP_TIMEOUT
) -> Tuple[int, Optional[Any], Optional[str]]:
    """GET `url` and return (status_code, parsed_body, error_reason).

    On network failure returns code=0 and a short, human-readable reason
    (DNS / refused / timeout / etc.) so callers can surface it to users.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai-stack-admin-pipe/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body), None
            except json.JSONDecodeError:
                return resp.status, body, None
    except urllib.error.HTTPError as e:
        # The server responded — caller can decide if 4xx/5xx is "unreachable"
        return e.code, None, None
    except socket.timeout:
        return 0, None, f"timeout after {timeout}s"
    except urllib.error.URLError as e:
        reason = e.reason
        # reason can be a string, a socket.gaierror, OSError, or socket.timeout
        if isinstance(reason, socket.timeout):
            return 0, None, f"timeout after {timeout}s"
        if isinstance(reason, socket.gaierror):
            return 0, None, f"DNS lookup failed ({reason.strerror or reason})"
        if isinstance(reason, ConnectionRefusedError):
            return 0, None, "connection refused"
        if isinstance(reason, OSError):
            # e.g. "No route to host", "Network is unreachable"
            return 0, None, str(reason.strerror or reason)
        return 0, None, str(reason)
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def _probe_targets(probe: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Return [(host, port), ...] to try in order: internal first, host fallback second."""
    targets = [(probe["host"], probe["port"])]
    fb_host = probe.get("host_fallback")
    fb_port = probe.get("host_fallback_port")
    if fb_host and fb_port and (fb_host, fb_port) != targets[0]:
        targets.append((fb_host, fb_port))
    return targets


def _try_get(
    probe: Dict[str, Any], path: str
) -> Tuple[int, Optional[Any], Optional[str], Optional[str]]:
    """GET `path` against each probe target until one responds.

    Returns (status_code, body, reachable_url, error_reason). If every target
    fails, status_code is 0 and error_reason carries the consolidated cause
    from the last attempt (e.g. "DNS lookup failed", "connection refused",
    "timeout after 4s").
    """
    timeout = probe.get("timeout", HTTP_TIMEOUT)
    last_error: Optional[str] = None
    attempts: List[str] = []
    for host, port in _probe_targets(probe):
        url = f"http://{host}:{port}{path}"
        code, body, error = _http_get(url, timeout=timeout)
        if code != 0:
            return code, body, url, None
        last_error = error
        attempts.append(f"{url} ({error})")
    return 0, None, None, "; ".join(attempts) if attempts else last_error


def _probe_processing(probe: Dict[str, Any]) -> Dict[str, Any]:
    """Return the live processing detail for a container's HTTP surface."""
    kind = probe["kind"]

    if kind == "llm_gateway_alias":
        # This hostname is a network alias on llm-gateway (LiteLLM). Only the
        # side-effect-free liveliness endpoint is safe here (see _PROBES note);
        # what the inference plane is DOING lives in the LLM gateway panel.
        code, _, url, err = _try_get(probe, "/health/liveliness")
        if code == 0:
            return {"status": "unreachable", "error": err}
        return {
            "status": "ready" if 200 <= code < 300 else "degraded",
            "code": code,
            "via": url,
            "note": "alias lands on llm-gateway — activity in the LLM gateway panel",
        }

    if kind == "http_root":
        code, _, url, err = _try_get(probe, "/")
        if code == 0:
            return {"status": "unreachable", "error": err}
        return {"status": "ready", "code": code, "via": url}

    if kind == "http_health":
        code, body, url, err = _try_get(probe, probe.get("health_path", "/health"))
        if code == 0:
            return {"status": "unreachable", "error": err}
        return {
            "status": "ready" if 200 <= code < 300 else "degraded",
            "code": code, "via": url,
            "detail": body if isinstance(body, dict) else None,
        }

    if kind == "open_notebook":
        # Try the API first. If that fails, fall back to the Streamlit UI port
        # so we can still confirm the container is alive (the API can be slow
        # to come up after a restart while the UI is already serving).
        code, _, url, err = _try_get(probe, "/api/config")
        if code == 0:
            ui_probe = dict(probe)
            ui_probe["port"] = 8502
            ui_probe["host_fallback_port"] = 8503  # host publishes UI on 8503
            ui_code, _, ui_url, ui_err = _try_get(ui_probe, "/")
            if ui_code != 0:
                return {
                    "status": "unreachable",
                    "error": err or ui_err,
                    "ui_error": ui_err,
                }
            return {
                "status": "degraded",
                "code": ui_code,
                "via": ui_url,
                "note": (
                    "API on :5055 not reachable — UI on :8502 responding. "
                    f"API error: {err or 'unknown'}"
                ),
            }
        return {
            "status": "ready" if 200 <= code < 300 else "degraded",
            "code": code, "via": url,
        }

    return {"status": "unknown_probe_kind"}


# --- LLM gateway panel (LiteLLM · llm-queue) --------------------------------
#
# Live inference-plane metrics for the `status` view: what is processing right
# now, queue depth, top requester in the queue, parallel availability, and —
# when fully quiet — how long the plane has been idle. All data comes from
# side-effect-free surfaces: the gateway's read-only /observe/queue pass-through
# (llm-queue admission board) and the /spend/logs ledger (idle time).

# Permissive-mode presented-key -> friendly caller name. The CANONICAL map is
# KEY_FRIENDLY_NAMES in modules/llm-traffic/service/llm_traffic.py — keep in
# sync until virtual keys land (then the ledger carries real aliases).
_LLM_KEY_NAMES: Dict[str, str] = {
    "ollama": "openwebui",
    "not-needed": "openbrain",
    "llama": "little-coder",
    "mnemory": "mnemory",
    "": "anonymous",
    "no-key": "anonymous",
    "sk-admin": "admin",
    # LiteLLM's openai-client path re-keys upstream requests as `dummy` (its
    # litellm_params.api_key) — the caller's real key is NOT forwarded to
    # llm-queue unless the caller sets the OpenAI `user` body field (see
    # config/litellm.config.yaml, B2/P2 attribution note).
    "dummy": "via-gateway (unattributed)",
}

# Scopes for which the LLM gateway panel is relevant when the status view is
# filtered to one service ("status of llm-gateway" etc.).
_LLM_PLANE_SCOPES = {"llm-gateway", "llm-queue", "llama-cpp", "llama-cpp-embed"}


def _llm_caller(key: Any) -> str:
    key = key if isinstance(key, str) else ""
    return _LLM_KEY_NAMES.get(key, key or "anonymous")


def _humanize_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


# How far back to look for the newest ledger row when deriving idle time.
_LLM_IDLE_LOOKBACK_DAYS = 30


def _llm_last_activity() -> Dict[str, Any]:
    """Idle-time source: now minus the newest row in the LiteLLM spend ledger.

    Uses the paginated /spend/logs/ui endpoint (newest-first, page_size=1) —
    verified working unauthenticated in this build. The plain /spend/logs
    ledger has grown too large to pull (times out) and its date params 500,
    so it is NOT a fallback here; failing cheap-and-fast beats hanging the
    status view.
    """
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%d %H:%M:%S"  # this endpoint rejects bare YYYY-MM-DD
    query = urllib.parse.urlencode({
        "start_date": (now - timedelta(days=_LLM_IDLE_LOOKBACK_DAYS)).strftime(fmt),
        "end_date": (now + timedelta(days=1)).strftime(fmt),
        "page": 1,
        "page_size": 1,
    })
    code, body, _err = _http_get(
        f"{LLM_GATEWAY_URL}/spend/logs/ui?{query}", timeout=HTTP_TIMEOUT_SLOW
    )
    if code != 200 or not isinstance(body, dict):
        return {
            "known": False,
            "note": f"spend ledger query failed (HTTP {code}) — idle duration unknown",
        }
    rows = body.get("data") or []
    if not rows:
        return {
            "known": False,
            "note": f"no requests in the last {_LLM_IDLE_LOOKBACK_DAYS} days",
        }
    latest: Optional[datetime] = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        for field in ("endTime", "startTime"):
            raw = r.get(field)
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if latest is None or ts > latest:
                latest = ts
    if latest is None:
        return {"known": False, "note": "ledger rows carried no parseable timestamps"}
    idle_s = max(0.0, (datetime.now(timezone.utc) - latest).total_seconds())
    return {
        "known": True,
        "last_request_utc": latest.isoformat(),
        "idle_s": round(idle_s, 1),
    }


def _llm_gateway_panel() -> Dict[str, Any]:
    """Per-model live queue metrics from the llm-queue admission board."""
    code, board, err = _http_get(
        f"{LLM_GATEWAY_URL}/observe/queue", timeout=HTTP_TIMEOUT
    )
    if code == 0:
        return {"available": False, "gateway": LLM_GATEWAY_URL, "error": err}
    if code != 200 or not isinstance(board, dict):
        return {
            "available": False,
            "gateway": LLM_GATEWAY_URL,
            "error": f"HTTP {code} from /observe/queue",
        }

    models: List[Dict[str, Any]] = []
    total_running = 0
    total_waiting = 0
    for name, m in sorted((board.get("models") or {}).items()):
        if not isinstance(m, dict):
            continue
        running = [r for r in (m.get("running") or []) if isinstance(r, dict)]
        waiting = [w for w in (m.get("waiting") or []) if isinstance(w, dict)]
        total_running += len(running)
        total_waiting += len(waiting)

        waiting_by_key: Dict[str, int] = {}
        for w in waiting:
            k = w.get("key") if isinstance(w.get("key"), str) else ""
            waiting_by_key[k] = waiting_by_key.get(k, 0) + 1
        top_waiting = None
        if waiting_by_key:
            top_key = max(waiting_by_key, key=lambda k: waiting_by_key[k])
            top_waiting = {
                "key": top_key,
                "caller": _llm_caller(top_key),
                "count": waiting_by_key[top_key],
            }

        models.append({
            "model": name,
            "running": [
                {
                    "key": r.get("key", ""),
                    "caller": _llm_caller(r.get("key", "")),
                    "elapsed_s": r.get("elapsed_s"),
                }
                for r in running
            ],
            "waiting_depth": len(waiting),
            "top_waiting": top_waiting,
            "longest_wait_s": max((w.get("waited_s") or 0 for w in waiting), default=0),
            "permits_free": m.get("permits_free"),
            "slots": m.get("P"),
            "avg_T_s": m.get("avg_T_s"),
        })

    panel: Dict[str, Any] = {
        "available": True,
        "gateway": LLM_GATEWAY_URL,
        "models": models,
        "running_total": total_running,
        "waiting_total": total_waiting,
        "held_total": board.get("held_total"),
        "max_total_connections": board.get("max_total_connections"),
        "idle": None,
    }
    # Only consult the ledger when the plane is fully quiet — the idle metric
    # is meaningless (and the extra call wasted) while anything runs or waits.
    if total_running == 0 and total_waiting == 0:
        panel["idle"] = _llm_last_activity()
    return panel


# --- GPU panel (nvidia-smi) -----------------------------------------------

def _nvidia_smi_panel() -> Dict[str, Any]:
    """Run nvidia-smi for temperature, VRAM used/free/total, util%. Falls back gracefully."""
    query = (
        "index,name,temperature.gpu,memory.used,memory.free,memory.total,"
        "utilization.gpu,utilization.memory"
    )
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT,
        )
    except FileNotFoundError:
        return {"available": False, "reason": "nvidia-smi not available in this environment", "gpus": []}
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "nvidia-smi timed out", "gpus": []}
    except Exception as e:
        return {"available": False, "reason": f"nvidia-smi failed: {e}", "gpus": []}

    if result.returncode != 0:
        return {"available": False, "reason": (result.stderr or "nvidia-smi error").strip(), "gpus": []}

    gpu_assignments = _gpu_assignment_index()
    gpus = []
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        try:
            idx = int(parts[0])
            temp_c = int(parts[2])
            mem_used = int(parts[3])
            mem_free = int(parts[4])
            mem_total = int(parts[5])
            util_gpu = int(parts[6])
            util_mem = int(parts[7])
        except ValueError:
            continue
        gpus.append({
            "index": idx,
            "name": parts[1],
            "temp_c": temp_c,
            "vram_used_mib": mem_used,
            "vram_free_mib": mem_free,
            "vram_total_mib": mem_total,
            "vram_used_gb": round(mem_used / 1024, 1),
            "vram_free_gb": round(mem_free / 1024, 1),
            "vram_total_gb": round(mem_total / 1024, 1),
            "vram_used_pct": round(100 * mem_used / mem_total, 1) if mem_total else None,
            "util_gpu_pct": util_gpu,
            "util_mem_pct": util_mem,
            "assigned_to": gpu_assignments.get(str(idx), []),
        })
    # Detect Container Toolkit GPU reindexing: inside a container with
    # NVIDIA_VISIBLE_DEVICES=N, the visible GPU is presented as index 0. So
    # the "host index 0" the registry mapped to may not correspond to nvidia-smi
    # index 0 inside the container. Annotate this so the renderer can warn.
    in_container = os.path.exists("/.dockerenv")
    return {
        "available": True,
        "gpus": gpus,
        "in_container": in_container,
        "source": "nvidia-smi (in-container view)" if in_container else "nvidia-smi (host)",
    }


def _gpu_assignment_index() -> Dict[str, List[str]]:
    """Map host GPU index → list of services assigned to it (from registry env)."""
    index: Dict[str, List[str]] = {}
    for key, info in SERVICE_REGISTRY.items():
        gpu_env = info.get("gpu_device_env")
        if not gpu_env:
            continue
        device_id = os.environ.get(gpu_env, info.get("gpu_device_default"))
        if device_id is None:
            continue
        index.setdefault(str(device_id), []).append(key)
    return index


# --- Markdown rendering ---------------------------------------------------

def _format_stack_status_message(
    containers: Dict[str, Any],
    processing: Dict[str, Dict[str, Any]],
    litellm: Optional[Dict[str, Any]],
    gpus: Dict[str, Any],
    tailnet: Dict[str, Any],
    scope: Optional[str],
) -> str:
    lines: List[str] = []
    title = (
        f"**AI Stack status — `{scope}` only**" if scope else "**AI Stack status**"
    )
    lines.append(title)
    lines.append("")

    # Containers
    lines.append("### Containers")
    if not containers["available"]:
        lines.append(f"_docker compose ps unavailable — {containers['reason']}_")
    elif not containers["entries"]:
        lines.append("_No services reported by docker compose._")
    else:
        if containers.get("source") == "registry":
            lines.append(
                f"_Source: registry+probes ({containers.get('note', '')})_"
            )
            lines.append("")
            lines.append("| Service | State | Health | Notes |")
            lines.append("|---|---|---|---|")
            for c in containers["entries"]:
                if scope and scope not in (c["service"], _registry_alias_for(c["service"])):
                    continue
                state_emoji = {
                    "running": "🟢", "exited": "🔴", "restarting": "🟡",
                    "created": "⚪", "paused": "⏸️",
                    "unreachable": "🔌", "registered": "🗒️",
                }.get(c["state"], "❓")
                lines.append(
                    f"| `{c['service']}` | {state_emoji} {c['state']} "
                    f"| {c['health']} | {c['status']} |"
                )
        else:
            lines.append("| Service | State | Health | Status | Image |")
            lines.append("|---|---|---|---|---|")
            for c in containers["entries"]:
                if scope and scope not in (c["service"], _registry_alias_for(c["service"])):
                    continue
                state_emoji = {
                    "running": "🟢", "exited": "🔴", "restarting": "🟡",
                    "created": "⚪", "paused": "⏸️",
                }.get(c["state"], "❓")
                lines.append(
                    f"| `{c['service']}` | {state_emoji} {c['state']} | {c['health']} "
                    f"| {c['status']} | `{c['image']}` |"
                )
    lines.append("")

    # Tailnet URLs
    lines.append("### Tailnet URLs")
    if tailnet.get("fqdn"):
        lines.append(
            f"_Tailnet host: `{tailnet['fqdn']}` (source: {tailnet.get('source') or 'unknown'})_"
        )
    else:
        lines.append(
            "_Tailnet identity unknown — restart the tailscale container to "
            "regenerate `data/tailscale/tailnet-info.json`, or set "
            "`TAILNET_DOMAIN=<your-tailnet>.ts.net` in `.env`. URLs below use "
            "a placeholder host._"
        )
    lines.append("")
    lines.append("| Service | URL |")
    lines.append("|---|---|")
    for entry in tailnet.get("entries", []):
        lines.append(f"| `{entry['service']}` | {entry['url']} |")
    lines.append("")

    # Processing detail
    lines.append("### Processing detail")
    if not processing:
        lines.append("_No active services to introspect._")
    else:
        for svc, info in processing.items():
            lines.append(_format_processing_entry(svc, info))
    lines.append("")

    # LLM gateway panel — live inference-plane metrics from the llm-queue
    # admission board (via the gateway's read-only /observe pass-through).
    if litellm is not None:
        lines.append("### LLM gateway (LiteLLM · llm-queue)")
        if not litellm.get("available"):
            lines.append(
                f"_Queue board unreachable at `{litellm.get('gateway')}/observe/queue` — "
                f"{litellm.get('error') or 'unknown error'}. (Expected when this pipe "
                "runs on the host: llm-net is internal-only.)_"
            )
        elif not litellm.get("models"):
            lines.append("_Queue board reachable but reports no models._")
        else:
            # "Free slots" = free admission permits (how many new requests
            # dispatch immediately); "P" = the model's parallelism used for
            # wait estimates. Same naming as the llm-traffic live board.
            lines.append(
                "| Model | Now processing | Queue | Top requester in queue "
                "| Free slots | P | Avg T (s) |"
            )
            lines.append("|---|---|--:|---|--:|--:|--:|")
            for m in litellm["models"]:
                if m["running"]:
                    now_processing = "; ".join(
                        f"`{r['caller']}` ({r['elapsed_s']}s)" for r in m["running"]
                    )
                else:
                    now_processing = "— idle"
                top = m.get("top_waiting")
                top_cell = (
                    f"`{top['caller']}` ×{top['count']} "
                    f"(longest {m['longest_wait_s']}s)"
                    if top else "—"
                )
                lines.append(
                    f"| `{m['model']}` | {now_processing} | {m['waiting_depth']} "
                    f"| {top_cell} | {m['permits_free']} | {m['slots']} "
                    f"| {m['avg_T_s']} |"
                )
            lines.append("")
            lines.append(
                "_Free slots = free admission permits (N ≤ P+1: one extra "
                "request is admitted so the upstream never idles between "
                "completions); P = real parallel lanes. Avg T = rolling mean "
                "of recent completions, worst trimmed._"
            )
            held = litellm.get("held_total")
            if held is not None:
                lines.append(
                    f"_Held connections (all models): "
                    f"{held}/{litellm.get('max_total_connections')}._"
                )
            idle = litellm.get("idle")
            if idle:
                if idle.get("known"):
                    last = idle["last_request_utc"][:19].replace("T", " ")
                    lines.append(
                        f"_Inference plane idle for "
                        f"{_humanize_seconds(idle['idle_s'])} "
                        f"(last request finished {last} UTC)._"
                    )
                else:
                    lines.append(
                        f"_Inference plane idle (nothing running or queued); "
                        f"{idle.get('note')}._"
                    )
        lines.append("")

    # GPU panel
    lines.append("### GPUs")
    if not gpus["available"]:
        lines.append(f"_nvidia-smi unavailable — {gpus['reason']}_")
    elif not gpus["gpus"]:
        lines.append("_No GPUs reported._")
    else:
        if gpus.get("source"):
            lines.append(f"_Source: {gpus['source']}_")
            lines.append("")
        lines.append(
            "| # | Name | Temp | VRAM Used | VRAM Free | VRAM Total | Used % "
            "| GPU Util | Mem Util | Assigned |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for g in gpus["gpus"]:
            assigned = ", ".join(f"`{s}`" for s in g["assigned_to"]) or "—"
            lines.append(
                f"| {g['index']} | {g['name']} | {g['temp_c']}°C "
                f"| {g['vram_used_gb']} GB | {g['vram_free_gb']} GB "
                f"| {g['vram_total_gb']} GB | {g['vram_used_pct']}% "
                f"| {g['util_gpu_pct']}% | {g['util_mem_pct']}% | {assigned} |"
            )
        if gpus.get("in_container"):
            lines.append("")
            lines.append(
                "> _Note: VRAM figures from inside a container reflect "
                "**total GPU memory used by all processes**, not just this "
                "container. On WSL2/Docker Desktop, values may differ slightly "
                "from `nvidia-smi` run on the Windows host._"
            )

    lines.append("")
    lines.append("_Type `admin help` for the full command list._")
    return "\n".join(lines)


def _format_processing_entry(service: str, info: Dict[str, Any]) -> str:
    status = info.get("status", "unknown")
    icon = {
        "processing": "⚙️",
        "idle": "💤",
        "ready": "✅",
        "model_unloaded": "💤",
        "degraded": "⚠️",
        "unreachable": "🔌",
        "not_running": "⛔",
    }.get(status, "❓")

    if status == "processing":
        detail_lines = [
            f"- {icon} **`{service}`** — processing "
            f"({info['slots_active']}/{info['slots_total']} slots active)"
        ]
        for slot in info.get("slots_detail", []):
            tok = (
                f" prompt={slot['prompt_tokens']}, decoded={slot['predicted']}"
                if slot.get("prompt_tokens") is not None
                or slot.get("predicted") is not None
                else ""
            )
            detail_lines.append(
                f"  - slot `{slot['id']}` state=`{slot['state']}`{tok}"
            )
        return "\n".join(detail_lines)

    if status == "idle":
        return (
            f"- {icon} **`{service}`** — idle "
            f"(0/{info.get('slots_total', '?')} slots active)"
        )

    if status == "model_unloaded":
        return (
            f"- {icon} **`{service}`** — idle "
            f"(no active model — llama-swap unloads when idle)"
        )

    if status in ("ready", "degraded"):
        code = info.get("code")
        note = f" — {info['note']}" if info.get("note") else ""
        return f"- {icon} **`{service}`** — {status} (HTTP {code}){note}"

    if status == "unreachable":
        err = info.get("error")
        if err:
            return (
                f"- {icon} **`{service}`** — unreachable "
                f"(internal hostname + published port both failed: `{err}`)"
            )
        return (
            f"- {icon} **`{service}`** — unreachable from this host "
            "(internal hostname + published port both failed)"
        )

    if status == "not_running":
        return f"- {icon} **`{service}`** — container not running"

    return f"- {icon} **`{service}`** — {status}"


def _format_inventory_message(
    services: List[Dict[str, Any]],
    identity: Optional[Dict[str, Any]] = None,
) -> str:
    lines = ["**AI Stack tailnet services**", ""]
    if identity and identity.get("fqdn"):
        lines.append(
            f"_Tailnet host: `{identity['fqdn']}` (source: {identity.get('source') or 'unknown'})_"
        )
    else:
        lines.append(
            "_Tailnet identity unknown — URLs use a placeholder host. "
            "Set `TAILNET_DOMAIN` in `.env` or wait for the tailscale "
            "container to write `data/tailscale/tailnet-info.json`._"
        )
    lines.append("")
    lines.append("| Service | URL | Target | GPU | Container |")
    lines.append("|---|---|---|---|---|")
    for s in services:
        gpu = (
            f"{s['gpu_device_env']}={s['gpu_device_id']}"
            if s["gpu_device_env"]
            else "—"
        )
        lines.append(
            f"| `{s['service']}` | {s['tailnet_url']} "
            f"| `{s['target']}` | {gpu} | {s['container']} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subprocess dispatch to tailscale_serve_admin CLI
# ---------------------------------------------------------------------------

def execute_tailscale_admin(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the tailscale_serve_admin CLI with the given action/params."""
    if os.path.exists("/host_project/status-pipe/modules"):
        admin_script = "/host_project/status-pipe/modules/custom-tools/service/tailscale_serve_admin.py"
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
        "TAILSCALE_CLI_MISSING":
            "Run from the host (`docker exec tailscale tailscale …`) — the "
            "openwebui container does not ship the tailscale binary.",
    }
    if error_code in suggestions:
        response["suggestion"] = suggestions[error_code]

    # Inline remediation list when the admin tool returned one (e.g. CLI missing)
    if isinstance(result.get("remediation"), list) and result["remediation"]:
        response["message"] += "\n\n**How to run this command:**"
        for step in result["remediation"]:
            response["message"] += f"\n- `{step}`"
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
