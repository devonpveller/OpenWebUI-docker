import os
import subprocess
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class TailscaleServeAdmin:
    """Tailscale-based service management tool for OpenWebUI"""
    
    def __init__(self):
        self.state_file = "/var/lib/tailscale/serve-state.json"
        self.tailscale_bin = "tailscale"
        
        # Ensure state directory exists
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        
    def execute_command(self, command: list) -> subprocess.CompletedProcess:
        """Execute a shell command and return the result"""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )
            return result
        except FileNotFoundError as e:
            # Most common case: running inside the openwebui container where
            # the `tailscale` binary doesn't exist. Surface a clean diagnostic
            # rather than a Python errno trace.
            missing = command[0] if command else "<unknown>"
            logger.error(f"Binary not available: {missing}")
            raise FileNotFoundError(
                f"'{missing}' binary not available in this environment. "
                "tailscale CLI is shipped only inside the tailscale container — "
                "this admin action must be run from a process that has access "
                "to it (e.g. exec'd inside the tailscale container, or via the "
                "tailscale daemon's local API)."
            ) from e
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {' '.join(command)}")
            logger.error(f"Error: {e}")
            raise e
    
    def initialize_tailscale(self, authkey: str, hostname: str = "owui-node") -> bool:
        """Initialize tailscale with the provided auth key"""
        try:
            # Start tailscaled in userspace mode
            self.execute_command([
                "tailscaled",
                "--tun=userspace-networking",
                f"--state=/var/lib/tailscale/tailscaled.state"
            ])
            
            # Authenticate with non-interactive auth
            env = os.environ.copy()
            env["TS_AUTHKEY"] = authkey
            
            self.execute_command([
                "tailscale",
                "up",
                "--authkey=" + authkey,
                f"--hostname={hostname}",
                "--reset"
            ])
            
            return True
        except subprocess.CalledProcessError:
            return False
    
    def check_tailscale_ready(self) -> bool:
        """Check if tailscale is ready.

        Raises FileNotFoundError if the tailscale CLI is missing — callers
        catch it and surface a TAILSCALE_CLI_MISSING response. Returns False
        when the binary exists but reports a non-ready state.
        """
        try:
            self.execute_command(["tailscale", "status"])
            return True
        except subprocess.CalledProcessError:
            return False

    def _no_cli_response(self) -> Dict[str, Any]:
        """Standard response when the tailscale binary is missing."""
        return {
            "success": False,
            "error_code": "TAILSCALE_CLI_MISSING",
            "message": (
                "tailscale CLI is not available in this environment. The "
                "openwebui container ships without it; tailscale lives in a "
                "separate container and exposes its API only via Unix socket "
                "(/tmp/tailscaled.sock inside that container). Run this "
                "command from the host or inside the tailscale container, "
                "or fetch state from the daemon's local API."
            ),
            "remediation": [
                "Host:  docker exec tailscale tailscale serve status",
                "Inside tailscale container:  tailscale --socket=/tmp/tailscaled.sock serve status",
            ],
        }
    
    def serve_start(self, path: str, target_host: str = "127.0.0.1",
                   target_port: int = 5506, proxy_port: Optional[int] = None,
                   ts_hostname: Optional[str] = None,
                   require_userspace_tun: bool = True,
                   health_path: str = "/api/status") -> Dict[str, Any]:
        """Start serving a service at a specified path"""

        # Check if tailscale is ready
        try:
            if not self.check_tailscale_ready():
                return {
                    "success": False,
                    "error_code": "TAILSCALE_NOT_READY",
                    "message": "Tailscale is not ready"
                }
        except FileNotFoundError:
            return self._no_cli_response()

        try:
            # Health check first
            health_check_url = f"http://{target_host}:{target_port}{health_path}"
            health_result = self.execute_command([
                "curl", "-f", "-s", "-o", "/dev/null", health_check_url
            ])
            
            # Start serving
            serve_command = [
                "tailscale", "serve",
                f"--https=/{path}",
                f"http://{target_host}:{target_port}"
            ]
            
            if proxy_port:
                serve_command.extend([f"--proxy-port={proxy_port}"])
                
            self.execute_command(serve_command)
            
            # Get the resolved hostname
            status_result = self.execute_command(["tailscale", "status", "--json"])
            status_data = json.loads(status_result.stdout)
            resolved_hostname = status_data.get("Hostname", "unknown")
            
            # Create serve URL
            serve_url = f"https://{resolved_hostname}/{path}"
            
            # Save state
            self._save_state(path, target_host, target_port, health_path)
            
            return {
                "success": True,
                "summary": f"Successfully started serving at /{path}",
                "details": {
                    "serve_url": serve_url,
                    "resolved_hostname": resolved_hostname,
                    "path_map": {
                        f"/{path}": f"http://{target_host}:{target_port}"
                    },
                    "health_status": "healthy"
                }
            }
            
        except subprocess.CalledProcessError as e:
            error_msg = str(e.stderr)
            if "already serving" in error_msg:
                return {
                    "success": False,
                    "error_code": "SERVE_CONFLICT",
                    "message": f"Path /{path} is already being served"
                }
            elif "authentication required" in error_msg:
                return {
                    "success": False,
                    "error_code": "AUTH_REQUIRED",
                    "message": "Authentication required for Tailscale"
                }
            else:
                return {
                    "success": False,
                    "error_code": "UNKNOWN_ERROR",
                    "message": f"Failed to start serving: {str(e)}"
                }
    
    def serve_stop(self, path: str) -> Dict[str, Any]:
        """Stop serving a service at a specified path"""
        try:
            # Stop serving
            self.execute_command([
                "tailscale", "serve",
                "--remove=/" + path
            ])

            # Remove from state file
            self._remove_from_state(path)

            return {
                "success": True,
                "summary": f"Successfully stopped serving at /{path}",
                "details": {}
            }
        except FileNotFoundError:
            return self._no_cli_response()
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error_code": "UNKNOWN_ERROR",
                "message": f"Failed to stop serving: {str(e)}"
            }
    
    def status(self) -> Dict[str, Any]:
        """Get current status of all served paths"""
        try:
            result = self.execute_command([
                "tailscale", "serve", "status"
            ])

            # Parse the output (this is a simplified version)
            lines = result.stdout.strip().split('\n')
            paths = {}

            for line in lines:
                if ':' in line:
                    path, url = line.split(':', 1)
                    paths[path.strip()] = url.strip()

            return {
                "success": True,
                "summary": "Current serve status",
                "details": {
                    "paths": paths
                }
            }
        except FileNotFoundError:
            return self._no_cli_response()
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error_code": "UNKNOWN_ERROR",
                "message": f"Failed to get status: {str(e)}"
            }
    
    def health(self, path: str) -> Dict[str, Any]:
        """Check health of a specific service"""
        try:
            # Get the target URL from state
            state = self._load_state()
            if not state or "paths" not in state:
                return {
                    "success": False,
                    "error_code": "UNKNOWN_ERROR",
                    "message": "No state found"
                }
                
            path_config = state["paths"].get(path)
            if not path_config:
                return {
                    "success": False,
                    "error_code": "UNKNOWN_ERROR",
                    "message": f"No configuration found for path {path}"
                }
            
            # Extract host and port
            import urllib.parse
            parsed_url = urllib.parse.urlparse(path_config)
            target_host = parsed_url.hostname
            target_port = parsed_url.port
            
            # Health check
            health_path = "/api/status"  # Default from guide
            health_check_url = f"http://{target_host}:{target_port}{health_path}"
            
            try:
                self.execute_command([
                    "curl", "-f", "-s", "-o", "/dev/null", health_check_url
                ])
            except FileNotFoundError:
                return self._no_cli_response()

            return {
                "success": True,
                "summary": f"Service at /{path} is healthy",
                "details": {
                    "path": path,
                    "health_status": "healthy"
                }
            }
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error_code": "TARGET_UNREACHABLE",
                "message": f"Service at /{path} is unreachable: {str(e)}"
            }
    
    def _save_state(self, path: str, target_host: str, target_port: int, health_path: str):
        """Save current configuration to state file"""
        import os
        if not os.path.exists(os.path.dirname(self.state_file)):
            os.makedirs(os.path.dirname(self.state_file))
            
        try:
            state = self._load_state()
        except:
            state = {"paths": {}, "last_health": {}}
            
        state["paths"][path] = f"http://{target_host}:{target_port}"
        state["last_health"][path] = "healthy"
        
        with open(self.state_file, 'w') as f:
            json.dump(state, f)
    
    def _remove_from_state(self, path: str):
        """Remove path from state file"""
        try:
            state = self._load_state()
            if not state:
                return
                
            if "paths" in state and path in state["paths"]:
                del state["paths"][path]
                
            if "last_health" in state and path in state["last_health"]:
                del state["last_health"][path]
                
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except:
            pass
    
    def _load_state(self) -> Dict[str, Any]:
        """Load current configuration from state file"""
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except:
            return None

# Main function to handle the command line interface
def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Tailscale Serve Admin Tool")
    parser.add_argument("--action", required=True, choices=["serve_start", "serve_stop", "status", "health"])
    parser.add_argument("--path", help="Path to serve at")
    parser.add_argument("--target_host", default="127.0.0.1")
    parser.add_argument("--target_port", type=int, help="Port of the service")
    parser.add_argument("--proxy_port", type=int, help="Port to proxy through if needed")
    parser.add_argument("--ts_hostname", help="Override Tailscale hostname")
    parser.add_argument("--require_userspace_tun", action="store_true", default=True)
    parser.add_argument("--health_path", default="/api/status")
    
    args = parser.parse_args()
    
    tool = TailscaleServeAdmin()
    
    try:
        if args.action == "serve_start":
            if not args.target_port:
                raise ValueError("target_port is required for serve_start")
            result = tool.serve_start(
                path=args.path,
                target_host=args.target_host,
                target_port=args.target_port,
                proxy_port=args.proxy_port,
                ts_hostname=args.ts_hostname,
                require_userspace_tun=args.require_userspace_tun,
                health_path=args.health_path
            )
        elif args.action == "serve_stop":
            result = tool.serve_stop(path=args.path)
        elif args.action == "status":
            result = tool.status()
        elif args.action == "health":
            result = tool.health(path=args.path)
        
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({
            "success": False,
            "error_code": "UNKNOWN_ERROR",
            "message": str(e)
        }, indent=2))

if __name__ == "__main__":
    main()
