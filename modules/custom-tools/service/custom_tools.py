#!/usr/bin/env python3
"""
Custom Tools Module - Refactored Architecture

Manifest-driven tool discovery and automation module implementing the new AI Stack architecture.
Provides comprehensive tool management with structured contracts.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

def setup_logging() -> logging.Logger:
    """Setup module logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("custom_tools_module")

logger = setup_logging()

class CustomToolsModule:
    """Custom Tools Module implementing manifest-driven architecture"""
    
    def __init__(self):
        self.module_id = "custom-tools"
        self.version = "1.0.0-migrated"
        self.config = self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        """Load tools configuration"""
        return {
            "tools": {
                "ai_pipes": {
                    "path": "/host_scripts/ai_pipes",
                    "description": "AI Stack pipe functions for system management"
                },
                "recovery_scripts": {
                    "path": "/host_scripts/scripts",
                    "description": "Emergency recovery and automation scripts"
                },
                "refactored_modules": {
                    "path": "/host_scripts/modules",
                    "description": "New manifest-driven modules"
                }
            }
        }
    
    def discover_available_tools(self) -> Dict[str, Any]:
        """Discover all available tools and scripts"""
        tools_inventory = {
            "ai_pipes": [],
            "recovery_scripts": [],
            "refactored_modules": [],
            "other_tools": []
        }
        
        # Discover AI pipe functions
        ai_pipes_path = Path(self.config["tools"]["ai_pipes"]["path"])
        if ai_pipes_path.exists():
            for pipe_file in ai_pipes_path.glob("*_pipe.py"):
                if not pipe_file.name.startswith("__"):
                    tools_inventory["ai_pipes"].append({
                        "name": pipe_file.stem.replace("_pipe", "").replace("_", " ").title(),
                        "file": pipe_file.name,
                        "type": "legacy_pipe",
                        "description": f"Legacy pipe function: {pipe_file.stem}"
                    })
        
        # Discover recovery scripts
        scripts_path = Path(self.config["tools"]["recovery_scripts"]["path"])
        if scripts_path.exists():
            script_patterns = ["*.ps1", "*.bat", "*.sh"]
            for pattern in script_patterns:
                for script_file in scripts_path.glob(pattern):
                    tools_inventory["recovery_scripts"].append({
                        "name": script_file.stem.replace("-", " ").replace("_", " ").title(),
                        "file": script_file.name,
                        "type": script_file.suffix.lstrip("."),
                        "description": f"Recovery script: {script_file.name}"
                    })
        
        # Discover refactored modules
        modules_path = Path(self.config["tools"]["refactored_modules"]["path"])
        if modules_path.exists():
            for module_dir in modules_path.iterdir():
                if module_dir.is_dir() and (module_dir / "module.manifest.json").exists():
                    try:
                        with open(module_dir / "module.manifest.json", "r") as f:
                            manifest = json.load(f)
                        
                        tools_inventory["refactored_modules"].append({
                            "name": manifest.get("name", module_dir.name),
                            "slug": manifest.get("slug", module_dir.name),
                            "version": manifest.get("version", "unknown"),
                            "type": "refactored_module",
                            "description": manifest.get("description", "Manifest-driven module"),
                            "capabilities": manifest.get("capabilities", [])
                        })
                    except Exception as e:
                        logger.warning(f"Could not read manifest for {module_dir.name}: {e}")
        
        return tools_inventory
    
    def get_tool_usage_guide(self) -> Dict[str, Any]:
        """Get comprehensive tool usage guide"""
        return {
            "ai_stack_tools": {
                "description": "Comprehensive tool ecosystem for AI Stack management",
                "categories": {
                    "System Management": [
                        "GPU monitoring and diagnostics",
                        "System health checks", 
                        "Emergency recovery procedures",
                        "Network connectivity testing"
                    ],
                    "Development Tools": [
                        "Module scaffolding and generation",
                        "Migration from legacy to refactored architecture",
                        "Schema validation and testing",
                        "Orchestrated refactoring automation"
                    ],
                    "Recovery & Maintenance": [
                        "Quick fixes for common issues",
                        "Advanced PowerShell recovery scripts",
                        "Automated health monitoring",
                        "Service restart procedures"
                    ]
                }
            },
            "usage_patterns": {
                "natural_language": [
                    "Check GPU status",
                    "Run system health report", 
                    "Fix network issues",
                    "Show available tools",
                    "Emergency recovery"
                ],
                "direct_execution": [
                    "scripts\\quick-fixes.bat namespace",
                    "python modules/gpu-status/service/gpu_status.py",
                    "python tools/validation_tool.py --all"
                ]
            }
        }
    
    def search_tools(self, query: str) -> Dict[str, Any]:
        """Search for tools matching query"""
        query_lower = query.lower()
        results = {
            "query": query,
            "matches": [],
            "suggestions": []
        }
        
        # Get all available tools
        all_tools = self.discover_available_tools()
        
        # Search through all tool categories
        for category, tools in all_tools.items():
            for tool in tools:
                # Check if query matches name, description, or capabilities
                matches = []
                if query_lower in tool.get("name", "").lower():
                    matches.append("name")
                if query_lower in tool.get("description", "").lower():
                    matches.append("description")
                if "capabilities" in tool:
                    for capability in tool["capabilities"]:
                        if query_lower in capability.lower():
                            matches.append("capability")
                
                if matches:
                    results["matches"].append({
                        **tool,
                        "category": category,
                        "match_reasons": matches,
                        "relevance_score": len(matches)
                    })
        
        # Sort by relevance
        results["matches"].sort(key=lambda x: x["relevance_score"], reverse=True)
        
        # Add suggestions if few matches
        if len(results["matches"]) < 3:
            results["suggestions"] = [
                "Try broader terms like 'gpu', 'network', 'recovery', or 'health'",
                "Use 'show all tools' to see complete inventory",
                "Ask for 'tool categories' to see available categories"
            ]
        
        return results

    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {
            "module_id": self.module_id,
            "version": self.version,
            "name": "Custom Tools Discovery",
            "capabilities": ["tool_discovery", "automation", "script_management"],
            "status": "ready"
        }
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        health_score = 100
        issues = []
        
        # Check if tool directories are accessible
        for tool_type, config in self.config["tools"].items():
            path = Path(config["path"])
            if not path.exists():
                health_score -= 25
                issues.append(f"Tool directory not accessible: {config['path']}")
        
        status = "healthy" if health_score >= 75 else "degraded" if health_score >= 50 else "unhealthy"
        
        return {
            "module_id": self.module_id,
            "status": status,
            "health_score": health_score,
            "issues": issues,
            "capabilities": ["tool_discovery", "automation"]
        }
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute custom tools discovery with migrated functionality"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Parse input - handle both string and structured input
            input_data = request_data.get("input", "")
            if isinstance(input_data, dict):
                user_input = input_data.get("query", "")
            else:
                user_input = str(input_data)
            
            user_input_lower = user_input.lower()
            
            # Check if this is a Tailscale serve command
            is_tailscale_serve = any(keyword in user_input_lower for keyword in ["serve", "serving", "expose", "tailscale"]) and \
                                any(keyword in user_input_lower for keyword in ["start", "stop", "status", "lmstudio", "service", "health", "port"])
            
            if is_tailscale_serve:
                # Route to tailscale_serve_pipe
                return self._execute_tailscale_serve_pipe(request_data)
            
            # Determine what the user wants
            elif not user_input.strip() or "available" in user_input_lower or "list" in user_input_lower:
                # Show all available tools
                result_data = self.discover_available_tools()
                content = self._format_tools_inventory(result_data)
            elif "search" in user_input_lower or "find" in user_input_lower:
                # Extract search query
                search_terms = user_input_lower.replace("search", "").replace("find", "").strip()
                result_data = self.search_tools(search_terms)
                content = self._format_search_results(result_data)
            elif "guide" in user_input_lower or "help" in user_input_lower or "usage" in user_input_lower:
                # Show usage guide
                result_data = self.get_tool_usage_guide()
                content = self._format_usage_guide(result_data)
            else:
                # Search for tools matching the input
                result_data = self.search_tools(user_input)
                content = self._format_search_results(result_data)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok",
                "content": content,
                "structured_data": result_data,
                "diagnostics": {
                    "execution_time_ms": execution_time
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Custom tools execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **Custom Tools Error**: {str(e)}",
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _execute_tailscale_serve_pipe(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tailscale serve pipe function"""
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Import and execute the tailscale_serve_pipe
            import sys
            import os
            
            # Environment-aware path setup
            if os.path.exists('/host_project/scripts'):
                pipe_path = '/host_project/scripts/ai_pipes/tailscale_serve_pipe.py'
            else:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
                pipe_path = os.path.join(project_root, 'scripts', 'ai_pipes', 'tailscale_serve_pipe.py')
            
            # Execute via subprocess to isolate execution
            import subprocess
            result = subprocess.run(
                ["python", pipe_path, json.dumps(request_data)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                pipe_result = json.loads(result.stdout)
                
                # Convert to module result format
                return {
                    "request_id": request_id,
                    "module_id": "custom-tools",
                    "status": "ok" if pipe_result.get("status") == "success" else "error",
                    "content": pipe_result.get("message", ""),
                    "structured_data": pipe_result,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            else:
                return {
                    "request_id": request_id,
                    "module_id": "custom-tools",
                    "status": "error",
                    "content": f"❌ Tailscale Serve execution failed:\n\n```\n{result.stderr}\n```",
                    "error": {
                        "code": "PIPE_EXECUTION_ERROR",
                        "message": result.stderr
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error(f"Error executing tailscale_serve_pipe: {e}")
            return {
                "request_id": request_id,
                "module_id": "custom-tools",
                "status": "error",
                "content": f"❌ **Tailscale Serve Error**: {str(e)}",
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": str(e)
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _format_tools_inventory(self, result_data: Dict[str, Any]) -> str:
        """Format tools inventory for display"""
        content = "## 🔧 AI Stack Tools Inventory\n\n"
        
        for category, tools in result_data.items():
            if tools:  # Only show categories with tools
                category_name = category.replace("_", " ").title()
                content += f"### {category_name}\n"
                
                for tool in tools:
                    content += f"- **{tool['name']}** ({tool['type']})\n"
                    content += f"  - {tool['description']}\n"
                    if "capabilities" in tool:
                        content += f"  - Capabilities: {', '.join(tool['capabilities'])}\n"
                    content += "\n"
        
        content += "*Use 'search <term>' to find specific tools or 'tool usage guide' for help*"
        return content
    
    def _format_search_results(self, result_data: Dict[str, Any]) -> str:
        """Format search results for display"""
        query = result_data.get("query", "")
        matches = result_data.get("matches", [])
        suggestions = result_data.get("suggestions", [])
        
        content = f"## 🔍 Tool Search Results for '{query}'\n\n"
        
        if matches:
            content += f"Found {len(matches)} matching tools:\n\n"
            for match in matches[:10]:  # Limit to top 10
                content += f"### {match['name']}\n"
                content += f"- **Category**: {match['category']}\n"
                content += f"- **Type**: {match['type']}\n"
                content += f"- **Description**: {match['description']}\n"
                if "capabilities" in match:
                    content += f"- **Capabilities**: {', '.join(match['capabilities'])}\n"
                content += f"- **Relevance**: {match['relevance_score']}/10\n\n"
        else:
            content += "No matching tools found.\n\n"
            
        if suggestions:
            content += "### 💡 Suggestions:\n"
            for suggestion in suggestions:
                content += f"- {suggestion}\n"
        
        return content
    
    def _format_usage_guide(self, result_data: Dict[str, Any]) -> str:
        """Format usage guide for display"""
        guide = result_data.get("ai_stack_tools", {})
        patterns = result_data.get("usage_patterns", {})
        
        content = f"## 📖 AI Stack Tools Usage Guide\n\n{guide.get('description', '')}\n\n"
        
        # Categories
        if "categories" in guide:
            content += "### Tool Categories:\n"
            for category, items in guide["categories"].items():
                content += f"\n**{category}**:\n"
                for item in items:
                    content += f"- {item}\n"
        
        # Usage patterns
        if patterns:
            content += "\n### Usage Patterns:\n"
            
            if "natural_language" in patterns:
                content += "\n**Natural Language Queries**:\n"
                for example in patterns["natural_language"]:
                    content += f"- \"{example}\"\n"
            
            if "direct_execution" in patterns:
                content += "\n**Direct Execution**:\n"
                for command in patterns["direct_execution"]:
                    content += f"- `{command}`\n"
        
        return content
    
    def validate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate input without execution"""
        required_fields = ["request_id", "input"]
        missing_fields = [field for field in required_fields if field not in input_data]
        
        if missing_fields:
            return {
                "valid": False,
                "errors": [f"Missing required field: {field}" for field in missing_fields]
            }
        
        return {"valid": True, "errors": []}

# Create module instance
tools_module = CustomToolsModule()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return tools_module.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return tools_module.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""
    return tools_module.health()

def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate input"""
    return tools_module.validate(input_data)

if __name__ == "__main__":
    # Check for piped input first
    if not sys.stdin.isatty():
        # Input from pipe
        try:
            input_data = json.loads(sys.stdin.read())
            result = main(input_data)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            error_result = {"error": str(e), "type": "CLI execution error"}
            print(json.dumps(error_result, indent=2))
            sys.exit(1)
    elif len(sys.argv) > 1:
        # CLI mode with arguments
        if sys.argv[1] == "--describe":
            print(json.dumps(describe(), indent=2))
        elif sys.argv[1] == "--health":
            print(json.dumps(health(), indent=2))
        else:
            # Process command line arguments as input
            input_text = " ".join(sys.argv[1:])
            input_data = {"request_id": str(time.time()), "input": input_text}
            result = main(input_data)
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Interactive mode - show description
        print(json.dumps(describe(), indent=2))