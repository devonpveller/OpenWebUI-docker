"""
AI Stack Help Pipe

Provides comprehensive help and command listing for all available AI stack
pipe functions and tools.
"""

import json
import os
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
        return {"ai_stack": {"name": "OpenWebUI AI Stack"}}

def get_pipe_functions_help() -> Dict[str, Any]:
    """Get help information for all pipe functions"""
    return {
        "gpu_status_pipe": {
            "name": "GPU Status & Monitoring",
            "description": "Monitor GPU status, memory usage, and CUDA availability",
            "commands": [
                "Check GPU status",
                "Show me GPU memory usage", 
                "Detailed GPU diagnostics",
                "GPU memory analysis",
                "CUDA availability check"
            ],
            "features": [
                "Real-time GPU monitoring with PyTorch",
                "Memory usage tracking", 
                "Device information and diagnostics",
                "Recovery suggestions for GPU issues"
            ]
        },
        "emergency_recovery_pipe": {
            "name": "Emergency Recovery System",
            "description": "AI-driven system recovery and troubleshooting",
            "commands": [
                "Network connectivity issues",
                "Tailscale problems", 
                "GPU not working",
                "System recovery needed",
                "Complete restart required"
            ],
            "features": [
                "Intelligent issue analysis from conversational input",
                "Maps keywords to recovery actions",
                "Integration with quick-fixes.bat and emergency-recovery.ps1",
                "Safety warnings and execution guidance"
            ]
        },
        "system_health_pipe": {
            "name": "System Health Monitoring", 
            "description": "Comprehensive system health and service monitoring",
            "commands": [
                "Check system health",
                "Docker service status",
                "System diagnostics",
                "Comprehensive system check",
                "Service health overview"
            ],
            "features": [
                "Docker Compose service monitoring",
                "Health scoring and issue analysis", 
                "Critical service status tracking",
                "Structured recommendations"
            ]
        },
        "custom_tools_pipe": {
            "name": "Custom Tools & Automation",
            "description": "Access to all AI stack tools and automation scripts",
            "commands": [
                "What tools are available?",
                "Show recovery tools",
                "List monitoring utilities", 
                "Development tools",
                "Available commands"
            ],
            "features": [
                "Tool discovery and enumeration",
                "Request analysis and tool suggestion",
                "Execution guidance for PowerShell/batch scripts",
                "Integration with existing AI stack tooling"
            ]
        }
    }

def get_recovery_commands_help() -> Dict[str, Any]:
    """Get help for recovery commands"""
    return {
        "quick_fixes": {
            "script": "scripts\\quick-fixes.bat", 
            "description": "Quick targeted fixes for common issues",
            "commands": {
                "namespace": "Network namespace reset (most common fix for Tailscale)",
                "gpu": "GPU availability check and restart", 
                "status": "System overview and health check",
                "nuclear": "Complete system restart (last resort)"
            },
            "usage": "Most common recovery tool - handles 90% of issues"
        },
        "emergency_recovery": {
            "script": "scripts\\emergency-recovery.ps1",
            "description": "Advanced PowerShell recovery with health checks", 
            "commands": {
                "recover": "Standard comprehensive recovery",
                "gpu-reset": "GPU-specific recovery operations",
                "nuclear": "Advanced complete system recovery"
            },
            "usage": "Advanced recovery with full error handling and health validation"
        }
    }

def get_monitoring_commands_help() -> Dict[str, Any]:
    """Get help for monitoring commands"""
    return {
        "simple_monitor": {
            "script": "scripts\\simple-monitor.ps1",
            "description": "Background system monitoring",
            "commands": {
                "start": "Start background monitoring",
                "stop": "Stop background monitoring", 
                "status": "Check monitoring status"
            }
        },
        "tailscale_health": {
            "script": "scripts\\check-tailscale-health.ps1",
            "description": "Tailscale health monitoring service"
        }
    }

def get_conversational_examples() -> Dict[str, Any]:
    """Get examples of conversational commands"""
    return {
        "gpu_monitoring": {
            "category": "GPU & Hardware",
            "examples": [
                "Is my GPU working?",
                "How much GPU memory am I using?", 
                "Check CUDA availability",
                "GPU diagnostics please",
                "Why is my GPU not detected?"
            ]
        },
        "system_recovery": {
            "category": "System Recovery", 
            "examples": [
                "Tailscale is down",
                "Network connectivity problems",
                "Can't access OpenWebUI",
                "Services not responding",
                "Complete system restart needed"
            ]
        },
        "system_health": {
            "category": "Health & Monitoring",
            "examples": [
                "How is my system doing?",
                "Check all services", 
                "System health report",
                "Are my containers running?",
                "Overall system status"
            ]
        },
        "tool_discovery": {
            "category": "Tools & Commands",
            "examples": [
                "What tools do I have?",
                "Show me recovery options",
                "Available monitoring tools",
                "Help with development utilities"
            ]
        }
    }

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for help pipe"""
    try:
        user_input = payload.get("input", "").lower()
        config = load_config()
        
        # Determine what kind of help is needed
        help_type = "general"
        if any(word in user_input for word in ["pipe", "function", "command"]):
            help_type = "pipes"
        elif any(word in user_input for word in ["recovery", "fix", "repair"]):
            help_type = "recovery"
        elif any(word in user_input for word in ["monitor", "health", "status"]):
            help_type = "monitoring" 
        elif any(word in user_input for word in ["example", "how", "conversation"]):
            help_type = "examples"
        
        result = {
            "service": "AI Stack Help System",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "help_type": help_type
        }
        
        if help_type == "pipes":
            result.update({
                "pipe_functions": get_pipe_functions_help(),
                "summary": "4 main pipe functions available for conversational system management"
            })
        elif help_type == "recovery":
            result.update({
                "recovery_commands": get_recovery_commands_help(),
                "summary": "Recovery tools for fixing system issues"
            })
        elif help_type == "monitoring":
            result.update({
                "monitoring_commands": get_monitoring_commands_help(), 
                "summary": "Monitoring tools for system health tracking"
            })
        elif help_type == "examples":
            result.update({
                "conversational_examples": get_conversational_examples(),
                "summary": "Examples of how to talk to your AI stack"
            })
        else:
            # General help
            result.update({
                "overview": {
                    "description": "AI Stack Pipe System - Conversational System Management",
                    "capabilities": [
                        "GPU monitoring and diagnostics",
                        "System recovery and troubleshooting", 
                        "Health monitoring and status checks",
                        "Tool discovery and automation"
                    ]
                },
                "quick_start": {
                    "gpu_check": "Say: 'Check my GPU status'",
                    "system_health": "Say: 'How is my system doing?'", 
                    "recovery": "Say: 'Network issues' or 'GPU problems'",
                    "tools": "Say: 'What tools are available?'"
                },
                "pipe_functions_available": list(get_pipe_functions_help().keys()),
                "total_recovery_tools": len(get_recovery_commands_help()),
                "usage_tip": "Ask for specific help: 'pipe functions help', 'recovery commands help', 'monitoring help', or 'conversation examples'"
            })
        
        return result
        
    except Exception as e:
        return {
            "service": "AI Stack Help System",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "❌ Help System Error",
            "error": str(e),
            "fallback_help": {
                "basic_commands": [
                    "Check GPU status",
                    "System health check", 
                    "Network issues",
                    "What tools are available?"
                ]
            }
        }

if __name__ == "__main__":
    """CLI mode execution"""
    try:
        payload = json.loads(sys.stdin.read()) if sys.stdin.read().strip() else {"input": ""}
        result = main(payload)
        print(json.dumps(result, indent=2))
    except Exception as e:
        error_result = {"error": str(e), "type": "CLI execution error"}
        print(json.dumps(error_result, indent=2))
        sys.exit(1)