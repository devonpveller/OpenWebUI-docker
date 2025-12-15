#!/usr/bin/env python3
"""
Tailscale Serve Admin V2 - HTTP API Implementation

Uses Tailscale Local API (localhost:41641) for container-native service management.
This works because OpenWebUI and Tailscale containers share network namespace.
"""

import requests
import json
import logging
import time
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class TailscaleServeAdminV2:
    """Tailscale service management using HTTP Local API"""
    
    def __init__(self):
        self.api_base = "http://localhost:41641/localapi/v0"
        self.state_file = Path("/host_project/data/tailscale/serve-state.json")
        
        # Ensure state directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _api_request(self, method: str, endpoint: str, data: Optional[Dict] = None, timeout: int = 30) -> Dict[str, Any]:
        """Make request to Tailscale Local API"""
        try:
            url = f"{self.api_base}{endpoint}"
            
            if method == "GET":
                response = requests.get(url, timeout=timeout)
            elif method == "POST":
                response = requests.post(url, json=data, timeout=timeout)
            elif method == "DELETE":
                response = requests.delete(url, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            
            # Try to parse JSON response
            try:
                return response.json()
            except:
                return {"status": "ok", "raw": response.text}
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Cannot connect to Tailscale Local API at {url}")
            return {
                "success": False,
                "error_code": "API_UNAVAILABLE",
                "message": "Tailscale Local API not accessible. Ensure Tailscale container is running and network namespace is shared."
            }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error_code": "API_TIMEOUT",
                "message": f"Tailscale API request timed out after {timeout}s"
            }
        except requests.exceptions.HTTPError as e:
            return {
                "success": False,
                "error_code": "API_ERROR",
                "message": f"Tailscale API error: {e}",
                "status_code": e.response.status_code
            }
        except Exception as e:
            return {
                "success": False,
                "error_code": "UNKNOWN_ERROR",
                "message": str(e)
            }
    
    def check_tailscale_ready(self) -> Dict[str, Any]:
        """Check if Tailscale is ready"""
        result = self._api_request("GET", "/status")
        
        if "error_code" in result:
            return {"ready": False, **result}
        
        # Check if we're logged in
        backend_state = result.get("BackendState", "")
        if backend_state == "Running":
            return {
                "ready": True,
                "hostname": result.get("Self", {}).get("HostName", "unknown"),
                "tailnet": result.get("CurrentTailnet", {}).get("Name", "unknown")
            }
        else:
            return {
                "ready": False,
                "error_code": "TAILSCALE_NOT_READY",
                "message": f"Tailscale backend state: {backend_state}"
            }
    
    def serve_start(self, path: str, target_host: str = "127.0.0.1", 
                   target_port: int = 5506, proxy_port: Optional[int] = None,
                   **kwargs) -> Dict[str, Any]:
        """Start serving a service at specified path using Tailscale serve"""
        
        # Check if Tailscale is ready
        ready_check = self.check_tailscale_ready()
        if not ready_check.get("ready"):
            return ready_check
        
        try:
            # Normalize path
            serve_path = f"/{path.lstrip('/')}"
            target_url = f"http://{target_host}:{target_port}"
            
            # Configure serve using Tailscale API
            # Note: The Local API for serve configuration may differ - this is a starting point
            serve_config = {
                "TCP": {
                    "443": {  # HTTPS port
                        "HTTPS": True
                    }
                },
                "Web": {
                    serve_path: {
                        "Handlers": {
                            "/": {
                                "Proxy": target_url
                            }
                        }
                    }
                }
            }
            
            # Set serve configuration
            result = self._api_request("POST", "/serve-config", data=serve_config)
            
            if "error_code" in result:
                return result
            
            # Save state
            self._save_state(serve_path, target_url)
            
            return {
                "success": True,
                "summary": f"Successfully started serving at {serve_path}",
                "details": {
                    "serve_url": f"https://{ready_check.get('hostname')}.{ready_check.get('tailnet')}{serve_path}",
                    "path_map": {
                        serve_path: target_url
                    }
                }
            }
            
        except Exception as e:
            logger.error(f"Error starting serve: {e}")
            return {
                "success": False,
                "error_code": "SERVE_START_FAILED",
                "message": str(e)
            }
    
    def serve_stop(self, path: str) -> Dict[str, Any]:
        """Stop serving a service at specified path"""
        
        # Check if Tailscale is ready
        ready_check = self.check_tailscale_ready()
        if not ready_check.get("ready"):
            return ready_check
        
        try:
            serve_path = f"/{path.lstrip('/')}"
            
            # Remove serve configuration (implementation depends on API)
            # For now, return a helpful message
            return {
                "success": False,
                "error_code": "NOT_IMPLEMENTED",
                "message": "HTTP API serve configuration removal not yet implemented. Use CLI: tailscale serve --remove"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error_code": "SERVE_STOP_FAILED",
                "message": str(e)
            }
    
    def status(self) -> Dict[str, Any]:
        """Get status of all served paths"""
        
        ready_check = self.check_tailscale_ready()
        if not ready_check.get("ready"):
            return ready_check
        
        # Get current serve status
        result = self._api_request("GET", "/serve-config")
        
        if "error_code" in result:
            return result
        
        return {
            "success": True,
            "tailscale_status": ready_check,
            "serve_config": result
        }
    
    def health(self, path: str = "/") -> Dict[str, Any]:
        """Check health of Tailscale and served services"""
        
        ready_check = self.check_tailscale_ready()
        
        return {
            "success": True,
            "tailscale_ready": ready_check.get("ready", False),
            "details": ready_check
        }
    
    def _save_state(self, path: str, target: str):
        """Save serve state to file"""
        try:
            state = {}
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
            
            if "paths" not in state:
                state["paths"] = {}
            
            state["paths"][path] = target
            state["last_updated"] = time.time()
            
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

# CLI interface
if __name__ == "__main__":
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description="Tailscale Serve Admin V2 (HTTP API)")
    parser.add_argument("--action", required=True, 
                       choices=["serve_start", "serve_stop", "status", "health"],
                       help="Action to perform")
    parser.add_argument("--path", help="Path to serve at (e.g., /lmstudio)")
    parser.add_argument("--target_host", default="127.0.0.1", help="Target host")
    parser.add_argument("--target_port", type=int, help="Target port")
    parser.add_argument("--proxy_port", type=int, help="Proxy port (optional)")
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    admin = TailscaleServeAdminV2()
    
    # Execute action
    if args.action == "serve_start":
        if not args.path or not args.target_port:
            print(json.dumps({
                "success": False,
                "error_code": "MISSING_ARGUMENTS",
                "message": "serve_start requires --path and --target_port"
            }))
            sys.exit(1)
        
        result = admin.serve_start(
            path=args.path,
            target_host=args.target_host,
            target_port=args.target_port,
            proxy_port=args.proxy_port
        )
    elif args.action == "serve_stop":
        if not args.path:
            print(json.dumps({
                "success": False,
                "error_code": "MISSING_ARGUMENTS",
                "message": "serve_stop requires --path"
            }))
            sys.exit(1)
        
        result = admin.serve_stop(path=args.path)
    elif args.action == "status":
        result = admin.status()
    elif args.action == "health":
        result = admin.health(path=args.path if args.path else "/")
    
    # Output result
    print(json.dumps(result, indent=2))
    
    # Exit with appropriate code
    sys.exit(0 if result.get("success") else 1)
