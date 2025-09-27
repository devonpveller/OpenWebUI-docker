"""
Custom Tools Pipe for AI Stack

Provides integration with custom automation scripts and tools specific
to the AI stack environment and development workflow.
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
    except Exception:
        return {
            "ai_stack": {
                "workspace_root": "d:\\Open WebUI\\ai-stack",
                "name": "OpenWebUI AI Stack"
            }
        }

def list_available_tools(workspace_root: str) -> Dict[str, Any]:
    """List available tools and scripts in the AI stack"""
    try:
        scripts_dir = os.path.join(workspace_root, "scripts")
        
        tools = {
            "recovery_tools": {
                "quick-fixes.bat": {
                    "description": "Quick targeted fixes for common issues",
                    "actions": ["namespace", "gpu", "status", "nuclear"],
                    "usage": "scripts\\quick-fixes.bat [action]"
                },
                "emergency-recovery.ps1": {
                    "description": "Advanced PowerShell recovery with health checks",
                    "actions": ["recover", "gpu-reset", "nuclear"],
                    "usage": "scripts\\emergency-recovery.ps1 -Action [action]"
                },
                "emergency-recovery.bat": {
                    "description": "Enhanced legacy recovery with GPU awareness",
                    "usage": "scripts\\emergency-recovery.bat"
                }
            },
            "monitoring_tools": {
                "simple-monitor.ps1": {
                    "description": "Background system monitoring",
                    "actions": ["start", "stop", "status"],
                    "usage": "scripts\\simple-monitor.ps1 -Action [action]"
                },
                "check-tailscale-health.ps1": {
                    "description": "Tailscale health monitoring service",
                    "usage": "scripts\\check-tailscale-health.ps1"
                }
            },
            "utility_tools": {
                "dev-helper.ps1": {
                    "description": "Development utilities and helpers",
                    "usage": "scripts\\dev-helper.ps1"
                },
                "validate-lineendings.ps1": {
                    "description": "Validate line endings for cross-platform compatibility",
                    "usage": "scripts\\validate-lineendings.ps1"
                }
            },
            "pipe_tools": {
                "emergency_recovery_pipe.py": {
                    "description": "Emergency recovery integration for OpenWebUI",
                    "usage": "Available as pipe function in OpenWebUI"
                },
                "gpu_status_pipe.py": {
                    "description": "GPU status monitoring for OpenWebUI",
                    "usage": "Available as pipe function in OpenWebUI"
                },
                "system_health_pipe.py": {
                    "description": "System health monitoring for OpenWebUI",
                    "usage": "Available as pipe function in OpenWebUI"
                }
            }
        }
        
        return {
            "available_tools": tools,
            "total_categories": len(tools),
            "total_tools": sum(len(category) for category in tools.values())
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "fallback": "Unable to enumerate tools directory"
        }

def analyze_user_request(user_input: str) -> Dict[str, Any]:
    """Analyze user input to suggest appropriate tools"""
    user_input_lower = user_input.lower()
    
    suggestions = {
        "tool_category": None,
        "specific_tool": None,
        "suggested_action": None,
        "confidence": "low"
    }
    
    # Recovery-related keywords
    if any(word in user_input_lower for word in ["recover", "fix", "repair", "broken", "down", "not working"]):
        suggestions.update({
            "tool_category": "recovery",
            "confidence": "high"
        })
        
        # Specific recovery actions
        if any(word in user_input_lower for word in ["network", "connectivity", "tailscale"]):
            suggestions.update({
                "specific_tool": "quick-fixes.bat",
                "suggested_action": "namespace",
                "explanation": "Network connectivity issues detected"
            })
        elif any(word in user_input_lower for word in ["gpu", "cuda", "graphics"]):
            suggestions.update({
                "specific_tool": "quick-fixes.bat",
                "suggested_action": "gpu",
                "explanation": "GPU-related issues detected"
            })
        elif any(word in user_input_lower for word in ["complete", "everything", "nuclear", "full restart"]):
            suggestions.update({
                "specific_tool": "quick-fixes.bat",
                "suggested_action": "nuclear",
                "explanation": "Complete system restart requested"
            })
        else:
            suggestions.update({
                "specific_tool": "emergency-recovery.ps1",
                "suggested_action": "recover",
                "explanation": "General recovery needed"
            })
    
    # Monitoring-related keywords
    elif any(word in user_input_lower for word in ["monitor", "status", "health", "check"]):
        suggestions.update({
            "tool_category": "monitoring",
            "confidence": "high"
        })
        
        if "tailscale" in user_input_lower:
            suggestions.update({
                "specific_tool": "check-tailscale-health.ps1",
                "explanation": "Tailscale-specific monitoring requested"
            })
        elif any(word in user_input_lower for word in ["background", "continuous"]):
            suggestions.update({
                "specific_tool": "simple-monitor.ps1",
                "suggested_action": "start",
                "explanation": "Background monitoring requested"
            })
        else:
            suggestions.update({
                "specific_tool": "system_health_pipe.py",
                "explanation": "General system health check"
            })
    
    # Development-related keywords
    elif any(word in user_input_lower for word in ["develop", "dev", "helper", "utility", "validate"]):
        suggestions.update({
            "tool_category": "utility",
            "confidence": "medium"
        })
        
        if any(word in user_input_lower for word in ["line ending", "lineending", "crlf", "lf"]):
            suggestions.update({
                "specific_tool": "validate-lineendings.ps1",
                "explanation": "Line ending validation requested"
            })
        else:
            suggestions.update({
                "specific_tool": "dev-helper.ps1",
                "explanation": "Development utilities requested"
            })
    
    return suggestions

def execute_tool_suggestion(suggestion: Dict[str, Any], workspace_root: str) -> Dict[str, Any]:
    """Provide execution guidance for suggested tools"""
    tool = suggestion.get("specific_tool")
    action = suggestion.get("suggested_action")
    
    if not tool:
        return {
            "status": "no_specific_tool",
            "message": "No specific tool could be determined from your request"
        }
    
    # Build command based on tool and action
    if tool.endswith(".bat"):
        if action:
            command = f"scripts\\{tool} {action}"
        else:
            command = f"scripts\\{tool}"
    elif tool.endswith(".ps1"):
        if action:
            command = f"scripts\\{tool} -Action {action}"
        else:
            command = f"scripts\\{tool}"
    else:
        # Python pipe tools
        command = f"Available as pipe function: {tool}"
    
    return {
        "status": "ready_to_execute",
        "tool": tool,
        "command": command,
        "explanation": suggestion.get("explanation", "Tool execution ready"),
        "working_directory": workspace_root,
        "execution_note": "Execute this command in PowerShell or Command Prompt",
        "safety_reminder": "Review the command before execution"
    }

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for custom tools pipe"""
    try:
        config = load_config()
        workspace_root = config.get("ai_stack", {}).get("workspace_root", "d:\\Open WebUI\\ai-stack")
        user_input = payload.get("input", "")
        
        if not user_input:
            # Return tool listing if no specific input
            tools_info = list_available_tools(workspace_root)
            return {
                "service": "AI Stack Custom Tools",
                "status": "ready",
                "tools_available": tools_info,
                "usage": "Describe what you need help with (e.g., 'network issues', 'gpu problems', 'system status')",
                "quick_access": {
                    "emergency": "Say 'network down' or 'gpu broken' for quick recovery suggestions",
                    "monitoring": "Say 'check status' or 'monitor system' for health checks",
                    "development": "Say 'validate files' or 'dev utilities' for development tools"
                }
            }
        
        # Analyze user request
        suggestion = analyze_user_request(user_input)
        execution_info = execute_tool_suggestion(suggestion, workspace_root)
        
        result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_request": user_input,
            "analysis": suggestion,
            "execution": execution_info
        }
        
        # Add context-specific information
        if suggestion.get("tool_category") == "recovery":
            result["recovery_context"] = {
                "most_common_issues": ["network connectivity", "gpu availability", "service startup"],
                "escalation_path": ["quick-fixes → emergency-recovery → nuclear option"],
                "monitoring_tip": "Always check system status after recovery actions"
            }
        elif suggestion.get("tool_category") == "monitoring":
            result["monitoring_context"] = {
                "available_checks": ["docker services", "gpu status", "system resources", "tailscale health"],
                "automation_tip": "Set up background monitoring with simple-monitor.ps1",
                "integration": "Use pipe functions for real-time monitoring in conversations"
            }
        
        return result
        
    except Exception as e:
        return {
            "status": "❌ Custom Tools Pipe Error",
            "error": str(e),
            "type": "custom_tools_pipe_error",
            "fallback": {
                "basic_tools": [
                    "scripts\\quick-fixes.bat status",
                    "scripts\\emergency-recovery.ps1 -Action recover",
                    "docker compose ps"
                ]
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