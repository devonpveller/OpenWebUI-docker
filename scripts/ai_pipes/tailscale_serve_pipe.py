#!/usr/bin/env python3
"""
Tailscale Serve Admin Pipe Function
OpenWebUI integration for Tailscale service management
"""

import json
import sys
import subprocess
from typing import Any, Dict

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for Tailscale Serve Admin pipe function.
    
    Args:
        payload: Request payload from OpenWebUI
        
    Returns:
        Formatted response for OpenWebUI
    """
    try:
        # Extract input
        user_input = payload.get("input", "")
        
        # Parse natural language input to determine action
        action, params = parse_user_input(user_input)
        
        if not action:
            return {
                "status": "error",
                "message": "Could not determine action from input",
                "suggestion": "Try: 'start serving lmstudio on port 5506' or 'show tailscale status'"
            }
        
        # Execute the tailscale_serve_admin tool
        result = execute_tailscale_admin(action, params)
        
        return format_response(result)
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Tailscale Serve Admin error: {str(e)}"
        }


def parse_user_input(user_input: str) -> tuple:
    """
    Parse natural language input to determine action and parameters.
    
    Returns:
        (action, params) tuple
    """
    user_input_lower = user_input.lower()
    
    # Determine action
    if any(word in user_input_lower for word in ["start", "serve", "enable", "expose"]):
        action = "serve_start"
        params = parse_serve_start_params(user_input)
    elif any(word in user_input_lower for word in ["stop", "disable", "remove"]):
        action = "serve_stop"
        params = parse_serve_stop_params(user_input)
    elif any(word in user_input_lower for word in ["status", "list", "show"]):
        action = "status"
        params = {}
    elif any(word in user_input_lower for word in ["health", "check"]):
        action = "health"
        params = parse_health_params(user_input)
    else:
        return None, {}
    
    return action, params


def parse_serve_start_params(user_input: str) -> Dict[str, Any]:
    """Parse parameters for serve_start action"""
    params = {
        "target_host": "127.0.0.1",
        "health_path": "/api/status"
    }
    
    # Extract path
    if "lmstudio" in user_input.lower() or "lm studio" in user_input.lower():
        params["path"] = "lmstudio"
    else:
        # Try to extract path from input
        import re
        path_match = re.search(r'at\s+/?([\w-]+)', user_input)
        if path_match:
            params["path"] = path_match.group(1)
        else:
            params["path"] = "service"
    
    # Extract port
    import re
    port_match = re.search(r'port\s+(\d+)', user_input)
    if port_match:
        params["target_port"] = int(port_match.group(1))
    elif "lmstudio" in user_input.lower():
        params["target_port"] = 5506  # Default LM Studio port
    else:
        params["target_port"] = 8080  # Generic default
    
    return params


def parse_serve_stop_params(user_input: str) -> Dict[str, Any]:
    """Parse parameters for serve_stop action"""
    params = {}
    
    # Extract path
    if "lmstudio" in user_input.lower():
        params["path"] = "lmstudio"
    else:
        import re
        path_match = re.search(r'/?([\w-]+)', user_input)
        if path_match:
            params["path"] = path_match.group(1)
    
    return params


def parse_health_params(user_input: str) -> Dict[str, Any]:
    """Parse parameters for health check action"""
    params = {}
    
    # Extract path
    if "lmstudio" in user_input.lower():
        params["path"] = "lmstudio"
    else:
        import re
        path_match = re.search(r'/?([\w-]+)', user_input)
        if path_match:
            params["path"] = path_match.group(1)
    
    return params


def execute_tailscale_admin(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the tailscale_serve_admin tool.
    
    Args:
        action: Action to perform
        params: Parameters for the action
        
    Returns:
        Result from tailscale_serve_admin
    """
    # Build command with environment-aware path
    import os
    if os.path.exists('/host_project/modules'):
        # Container environment
        admin_script = "/host_project/modules/custom-tools/service/tailscale_serve_admin.py"
    else:
        # Host environment
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        admin_script = os.path.join(project_root, "modules", "custom-tools", "service", "tailscale_serve_admin.py")
    
    cmd = [
        "python",
        admin_script,
        "--action", action
    ]
    
    # Add parameters
    for key, value in params.items():
        if key == "target_port":
            cmd.extend(["--target_port", str(value)])
        elif key == "path":
            cmd.extend(["--path", value])
        elif key == "target_host":
            cmd.extend(["--target_host", value])
        elif key == "health_path":
            cmd.extend(["--health_path", value])
    
    # Execute command
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {
                "success": False,
                "error_code": "EXECUTION_FAILED",
                "message": result.stderr or "Command execution failed"
            }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error_code": "TIMEOUT",
            "message": "Command execution timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error_code": "UNKNOWN_ERROR",
            "message": str(e)
        }


def format_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format tailscale_serve_admin result for OpenWebUI display.
    
    Args:
        result: Result from tailscale_serve_admin
        
    Returns:
        Formatted response for OpenWebUI
    """
    if result.get("success"):
        # Success response
        response = {
            "status": "success",
            "message": result.get("summary", "Operation completed successfully")
        }
        
        # Add details if present
        if "details" in result:
            details = result["details"]
            
            if "serve_url" in details:
                response["url"] = details["serve_url"]
                response["message"] += f"\n\n🌐 **Access URL**: {details['serve_url']}"
            
            if "path_map" in details:
                response["message"] += "\n\n**Path Mappings:**"
                for path, target in details["path_map"].items():
                    response["message"] += f"\n- `{path}` → `{target}`"
            
            if "health_status" in details:
                status_emoji = "✅" if details["health_status"] == "healthy" else "⚠️"
                response["message"] += f"\n\n{status_emoji} **Health**: {details['health_status']}"
            
            if "paths" in details:
                response["message"] += "\n\n**Currently Served Paths:**"
                for path, target in details["paths"].items():
                    response["message"] += f"\n- `{path}` → `{target}`"
        
        return response
    else:
        # Error response
        error_code = result.get("error_code", "UNKNOWN_ERROR")
        message = result.get("message", "Unknown error occurred")
        
        response = {
            "status": "error",
            "error_code": error_code,
            "message": f"❌ **Error**: {message}"
        }
        
        # Add remediation suggestions based on error code
        if error_code == "TAILSCALE_NOT_READY":
            response["suggestion"] = "Try: 'fix namespace' or restart Tailscale service"
        elif error_code == "AUTH_REQUIRED":
            response["suggestion"] = "Tailscale authentication required. Check auth key configuration."
        elif error_code == "TARGET_UNREACHABLE":
            response["suggestion"] = "Ensure the target service is running and accessible."
        elif error_code == "SERVE_CONFLICT":
            response["suggestion"] = "Path is already being served. Use 'stop serving' first."
        
        return response


if __name__ == "__main__":
    # Read payload from stdin or command line
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
