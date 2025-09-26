"""
Emergency Recovery Pipe for AI Stack

Integrates with the existing emergency recovery system to provide
AI-driven system recovery through OpenWebUI conversations.
"""

import json
import os
import subprocess
import sys
import time
from typing import Dict, Any, List, Optional

def load_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        return {
            "recovery": {
                "quick_fixes_script": "scripts\\quick-fixes.bat",
                "emergency_recovery_script": "scripts\\emergency-recovery.ps1",
                "available_actions": {
                    "namespace": "Network namespace reset (most common fix)",
                    "gpu": "GPU availability check and restart", 
                    "status": "System overview and health check",
                    "nuclear": "Complete system restart (last resort)",
                    "tailscale": "Standard Tailscale recovery",
                    "advanced": "Advanced PowerShell recovery"
                }
            }
        }

def analyze_user_input(user_input: str) -> Optional[str]:
    """Analyze user input to determine appropriate recovery action"""
    user_input_lower = user_input.lower()
    
    # Keyword mapping for recovery actions
    action_keywords = {
        "namespace": ["network", "connectivity", "unreachable", "tailscale down", "connection"],
        "gpu": ["cuda", "gpu", "graphics", "nvidia", "torch", "reranker"],
        "status": ["status", "health", "check", "overview", "system", "running"],
        "nuclear": ["nuclear", "complete restart", "full restart", "everything broken"],
        "tailscale": ["tailscale", "vpn", "derp", "serve"],
        "advanced": ["advanced", "powershell", "comprehensive"]
    }
    
    # Check for keywords in user input
    for action, keywords in action_keywords.items():
        if any(keyword in user_input_lower for keyword in keywords):
            return action
    
    return None

def get_recovery_suggestion(action: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Get recovery suggestion for a specific action"""
    recovery_config = config.get("recovery", {})
    available_actions = recovery_config.get("available_actions", {})
    
    if action == "namespace":
        quick_fixes_script = recovery_config.get('quick_fixes_script', 'scripts\\quick-fixes.bat')
        return {
            "action": "namespace",
            "command": f"{quick_fixes_script} namespace",
            "description": available_actions.get("namespace", "Network namespace reset"),
            "urgency": "medium",
            "success_probability": "high",
            "execution_note": "This is the most common fix for network connectivity issues"
        }
    elif action == "gpu":
        quick_fixes_script = recovery_config.get('quick_fixes_script', 'scripts\\quick-fixes.bat')
        return {
            "action": "gpu",
            "command": f"{quick_fixes_script} gpu",
            "description": available_actions.get("gpu", "GPU availability check"),
            "urgency": "medium",
            "success_probability": "high",
            "execution_note": "Checks CUDA availability and restarts GPU-dependent services"
        }
    elif action == "status":
        quick_fixes_script = recovery_config.get('quick_fixes_script', 'scripts\\quick-fixes.bat')
        return {
            "action": "status",
            "command": f"{quick_fixes_script} status",
            "description": available_actions.get("status", "System overview"),
            "urgency": "low",
            "success_probability": "high",
            "execution_note": "Safe diagnostic command - no system changes"
        }
    elif action == "nuclear":
        quick_fixes_script = recovery_config.get('quick_fixes_script', 'scripts\\quick-fixes.bat')
        return {
            "action": "nuclear",
            "command": f"{quick_fixes_script} nuclear",
            "description": available_actions.get("nuclear", "Complete system restart"),
            "urgency": "high",
            "success_probability": "high",
            "execution_note": "⚠️  WARNING: This will restart all services. Use as last resort."
        }
    elif action == "tailscale":
        emergency_script = recovery_config.get('emergency_recovery_script', 'scripts\\emergency-recovery.ps1')
        return {
            "action": "tailscale",
            "command": f"{emergency_script} -Action recover",
            "description": available_actions.get("tailscale", "Tailscale recovery"),
            "urgency": "medium",
            "success_probability": "high",
            "execution_note": "Standard PowerShell recovery with health checks"
        }
    elif action == "advanced":
        emergency_script = recovery_config.get('emergency_recovery_script', 'scripts\\emergency-recovery.ps1')
        return {
            "action": "advanced",
            "command": f"{emergency_script} -Action nuclear",
            "description": available_actions.get("advanced", "Advanced recovery"),
            "urgency": "high",
            "success_probability": "very_high",
            "execution_note": "Comprehensive PowerShell recovery with full error handling"
        }
    
    return {}

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for emergency recovery pipe"""
    try:
        config = load_config()
        user_input = payload.get("input", "")
        
        if not user_input:
            # Return available options if no specific input
            return {
                "status": "ready",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "available_actions": list(config.get("recovery", {}).get("available_actions", {}).keys()),
                "description": "AI Stack Emergency Recovery Integration",
                "usage": "Mention keywords like 'network', 'gpu', 'tailscale', 'status', etc. for targeted recovery suggestions",
                "quick_access": {
                    "most_common": "network issues → use 'namespace' action",
                    "gpu_issues": "CUDA problems → use 'gpu' action", 
                    "system_check": "health status → use 'status' action",
                    "last_resort": "complete failure → use 'nuclear' action"
                }
            }
        
        # Analyze input for appropriate action
        suggested_action = analyze_user_input(user_input)
        
        if suggested_action:
            suggestion = get_recovery_suggestion(suggested_action, config)
            return {
                "status": "suggestion_ready",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "suggested_action": suggestion,
                "user_input_analysis": f"Detected issue type: {suggested_action}",
                "next_steps": [
                    "Review the suggested command",
                    "Ensure you understand the impact",
                    "Execute the command in PowerShell/Command Prompt",
                    "Monitor system status after execution"
                ],
                "warning": "⚠️  Always review commands before execution. Some actions restart services."
            }
        else:
            # No specific action detected, provide general guidance
            recovery_actions = config.get("recovery", {}).get("available_actions", {})
            return {
                "status": "general_guidance",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "message": "No specific recovery action detected from your input",
                "available_actions": recovery_actions,
                "common_patterns": {
                    "network_issues": ["connectivity", "unreachable", "tailscale"],
                    "gpu_issues": ["cuda", "gpu", "graphics", "reranker"],
                    "system_issues": ["status", "health", "overview"]
                },
                "suggestion": "Try describing your issue with keywords like 'network down', 'gpu not working', or 'system status'"
            }
    
    except Exception as e:
        return {
            "status": "error",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e),
            "type": "emergency_recovery_pipe_error",
            "fallback": {
                "quick_fixes": "scripts\\quick-fixes.bat status",
                "emergency_recovery": "scripts\\emergency-recovery.ps1 -Action recover"
            }
        }

if __name__ == "__main__":
    """CLI mode execution"""
    try:
        payload = json.loads(sys.stdin.read())
        result = main(payload)
        print(json.dumps(result, indent=2))
    except Exception as e:
        error_result = {"error": str(e), "type": "CLI execution error"}
        print(json.dumps(error_result, indent=2))
        sys.exit(1)