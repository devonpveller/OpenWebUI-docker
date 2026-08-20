#!/usr/bin/env python3
"""
LM Studio Fix V2 - Uses new Tailscale Serve Admin Tool
Fixes LM Studio Tailscale connectivity using the refactored admin tool.
This version calls the tailscale_serve_admin.py tool instead of managing socat directly.
"""

import subprocess
import sys
import os
import json
from pathlib import Path

def log(level, message):
    """Log a message with level indicator"""
    print(f"[{level}] {message}")

def find_admin_tool():
    """Find the tailscale_serve_admin_v2.py script (HTTP API version)"""
    # Check container environment first
    if Path("/host_project").exists():
        admin_tool = Path("/host_project/modules/custom-tools/service/tailscale_serve_admin_v2.py")
        if admin_tool.exists():
            return admin_tool
        # Fallback to V1
        admin_tool_v1 = Path("/host_project/modules/custom-tools/service/tailscale_serve_admin.py")
        if admin_tool_v1.exists():
            log("WARN", "Using V1 admin tool (CLI-based) - V2 (HTTP API) preferred")
            return admin_tool_v1
    
    # Host environment
    current_dir = Path(__file__).parent
    project_root = current_dir.parent
    admin_tool = project_root / "modules" / "custom-tools" / "service" / "tailscale_serve_admin_v2.py"
    
    if admin_tool.exists():
        return admin_tool
    
    # Fallback to V1
    admin_tool_v1 = project_root / "modules" / "custom-tools" / "service" / "tailscale_serve_admin.py"
    if admin_tool_v1.exists():
        log("WARN", "Using V1 admin tool (CLI-based) - V2 (HTTP API) preferred")
        return admin_tool_v1
    
    return None

def main():
    """Main LM Studio fix function"""
    try:
        log("INFO", "Starting LM Studio Tailscale connectivity fix (V2)...")
        
        # Find admin tool
        admin_tool = find_admin_tool()
        if not admin_tool:
            log("ERROR", "Tailscale Serve Admin tool not found")
            log("ERROR", "Expected location: modules/custom-tools/service/tailscale_serve_admin.py")
            return 1
        
        log("INFO", f"Using admin tool: {admin_tool}")
        
        # Configure LM Studio serve on port 8234 pointing to 169.254.83.107:5506
        log("INFO", "Configuring Tailscale serve for LM Studio...")
        
        result = subprocess.run(
            [
                "python",
                str(admin_tool),
                "--action", "serve_start",
                "--path", "/lmstudio",
                "--proxy_port", "8234",
                "--target_host", "169.254.83.107",
                "--target_port", "5506"
            ],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            log("SUCCESS", "LM Studio Tailscale serve configured successfully")
            log("INFO", "LM Studio should now be accessible at: https://your-tailnet-name/lmstudio")
            
            # Show output
            if result.stdout:
                print("\nAdmin Tool Output:")
                print(result.stdout)
            
            return 0
        else:
            log("ERROR", f"Failed to configure Tailscale serve (exit code: {result.returncode})")
            
            if result.stderr:
                log("ERROR", f"Error output: {result.stderr}")
            
            if result.stdout:
                log("INFO", f"Tool output: {result.stdout}")
            
            return 2  # Partial success - tool exists but configuration failed
            
    except subprocess.TimeoutExpired:
        log("ERROR", "Tailscale serve configuration timed out after 60 seconds")
        return 1
        
    except Exception as e:
        log("ERROR", f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
