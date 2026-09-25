#!/usr/bin/env python3
"""
Help System Module - Refactored Architecture

Manifest-driven help and documentation module implementing the new AI Stack architecture.
Provides comprehensive help system with structured contracts.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

def setup_logging() -> logging.Logger:
    """Setup module logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("help_system_module")

logger = setup_logging()

class HelpSystemModule:
    """Help System Module implementing manifest-driven architecture"""
    
    def __init__(self):
        self.module_id = "help-system"
        self.version = "1.0.0-migrated"
        self.help_database = self.build_help_database()
    
    def build_help_database(self) -> Dict[str, Any]:
        """Build comprehensive help database"""
        return {
            "ai_stack_overview": {
                "title": "AI Stack Overview",
                "description": "Comprehensive AI chat interface with GPU acceleration and VPN access",
                "components": [
                    "OpenWebUI: Modern web interface with GPU-accelerated reranker models",
                    "Ollama: Local LLM hosting and management", 
                    "Tailscale: Secure VPN access for remote connections",
                    "Watchtower: Automatic container updates",
                    "AI Stack Pipe Functions: Unified system management through natural language"
                ]
            },
            "pipe_functions": {
                "title": "AI Stack Pipe Function System",
                "description": "Intelligent system management through natural language interface",
                "capabilities": [
                    "GPU monitoring and CUDA diagnostics",
                    "System health checks and monitoring",
                    "Emergency recovery and automated repair",
                    "Tool discovery and automation",
                    "Interactive help and documentation"
                ],
                "usage_examples": [
                    "Check GPU status",
                    "System health report",
                    "Fix network issues", 
                    "Available tools",
                    "Emergency recovery",
                    "Help with commands"
                ]
            },
            "recovery_procedures": {
                "title": "Emergency Recovery Procedures",
                "description": "Multi-tier recovery system for autonomous problem resolution",
                "quick_fixes": [
                    "scripts\\quick-fixes.bat namespace - Network namespace reset (most common)",
                    "scripts\\quick-fixes.bat gpu - GPU availability check and restart",
                    "scripts\\quick-fixes.bat status - System overview",
                    "scripts\\quick-fixes.bat nuclear - Complete restart (last resort)"
                ],
                "advanced_recovery": [
                    ".\\scripts\\emergency-recovery.ps1 -Action recover - Standard recovery",
                    ".\\scripts\\emergency-recovery.ps1 -Action gpu-reset - GPU-specific recovery",
                    ".\\scripts\\emergency-recovery.ps1 -Action nuclear - Complete system restart"
                ]
            },
            "common_issues": {
                "title": "Common Issues & Solutions",
                "problems": {
                    "network_unreachable": {
                        "symptoms": ["Network unreachable", "Tailscale can't connect", "Connection timeout"],
                        "cause": "OpenWebUI container recreation breaks shared network namespace",
                        "solution": "scripts\\quick-fixes.bat namespace",
                        "explanation": "Resets network namespace sharing between containers"
                    },
                    "gpu_not_available": {
                        "symptoms": ["CUDA not available", "GPU models slow", "Reranker using CPU"],
                        "cause": "GPU passthrough issues or PyTorch CPU-only installation",
                        "solution": "scripts\\quick-fixes.bat gpu",
                        "explanation": "Checks and restarts GPU services for CUDA availability"
                    },
                    "lmstudio_connectivity": {
                        "symptoms": ["LM Studio not accessible via Tailscale", "Port 8234 not working", "LM Studio proxy issues"],
                        "cause": "LM Studio Tailscale serve configuration not properly set up",
                        "solution": "Fix LM Studio connectivity - Use: 'fix lmstudio'",
                        "explanation": "Configures socat proxy and Tailscale serve for LM Studio access",
                        "access_url": "https://your-tailscale-url/lmstudio",
                        "port": "8234 (local LM Studio server)"
                    },
                    "pipe_function_not_working": {
                        "symptoms": ["Pipe function not accessible", "Module not found", "Import errors"],
                        "cause": "Scripts not mounted or incorrect volume configuration",
                        "solution": "Check: docker compose exec openwebui ls /host_project/status-pipe/",
                        "explanation": "Verify script mount and container access"
                    }
                }
            },
            "architecture_guide": {
                "title": "Architecture Guide",
                "description": "Understanding the AI Stack pipe function architecture",
                "legacy_system": {
                    "description": "Current production system using intelligent routing",
                    "components": [
                        "unified_openwebui_pipe.py - Single OpenWebUI integration point",
                        "router.py - Manifest-driven router with intelligent routing",
                        "Individual pipe modules for specific capabilities"
                    ]
                },
                "refactored_system": {
                    "description": "Modern manifest-driven architecture with explicit contracts",
                    "components": [
                        "status-pipe/router.py - keyword routing with schema validation",
                        "status-pipe/modules/ - independent modules with manifest-declared capabilities",
                        "status-pipe/schemas/ - JSON Schema contracts for requests/results",
                        "status-pipe/serve/ - tailscale serve management pipe"
                    ]
                }
            },
            "commands": {
                "title": "Essential Commands",
                "diagnostics": [
                    "docker compose ps - Check service status",
                    "docker compose logs openwebui - Check OpenWebUI logs",
                    "docker compose exec openwebui python -c \"import torch; print('CUDA available:', torch.cuda.is_available())\" - GPU check"
                ],
                "pipe_function_testing": [
                    "docker compose exec openwebui ls /host_project/status-pipe/ - Verify subsystem mount",
                    "docker compose exec openwebui python /host_project/status-pipe/orchestrator.py - Test the orchestrator",
                    "docker compose exec openwebui python /host_project/status-pipe/router.py '{\"input\": \"gpu status\"}' - Test router"
                ]
            }
        }
    
    def search_help(self, query: str) -> Dict[str, Any]:
        """Search help database for relevant information"""
        query_lower = query.lower()
        results = {
            "query": query,
            "matches": [],
            "related_topics": []
        }
        
        # Enhanced keyword mapping for better example matching
        keyword_mappings = {
            "gpu": ["gpu", "cuda", "graphics", "nvidia", "reranker"],
            "recovery": ["recovery", "emergency", "fix", "repair", "restart"],
            "pipe": ["pipe", "function", "module", "system"],
            "problems": ["problems", "issues", "solutions", "common"],
            "architecture": ["architecture", "overview", "system", "guide"],
            "lmstudio": ["lmstudio", "lm studio", "lms"]
        }
        
        # Check for direct keyword matches first
        for topic_key, keywords in keyword_mappings.items():
            if any(keyword in query_lower for keyword in keywords):
                # Special handling for lmstudio - find it in common_issues
                if topic_key == "lmstudio":
                    if "common_issues" in self.help_database:
                        common_issues = self.help_database["common_issues"]
                        lmstudio_content = common_issues.get("problems", {}).get("lmstudio_connectivity")
                        if lmstudio_content:
                            results["matches"].append({
                                "topic": "lmstudio_connectivity",
                                "title": "LM Studio Connectivity",
                                "description": "Fix LM Studio Tailscale connectivity issues",
                                "relevance_score": 20,  # High relevance for direct lmstudio search
                                "match_details": ["lmstudio_keyword"],
                                "content": {
                                    "title": "LM Studio Connectivity",
                                    "description": "Fix LM Studio Tailscale connectivity issues",
                                    **lmstudio_content
                                }
                            })
                else:
                    # Find matching topics in help database
                    for db_key, db_content in self.help_database.items():
                        if topic_key in db_key or any(keyword in db_content.get("title", "").lower() for keyword in keywords):
                            results["matches"].append({
                                "topic": db_key,
                                "title": db_content.get("title", db_key.replace("_", " ").title()),
                                "description": db_content.get("description", ""),
                                "relevance_score": 15,  # High relevance for keyword matches
                                "match_details": ["keyword_match"],
                                "content": db_content
                            })
        
        # If no keyword matches, fall back to original search
        if not results["matches"]:
            # Search through help database
            for topic_key, topic_data in self.help_database.items():
                relevance_score = 0
                match_details = []
                
                # Check title and description
                if query_lower in topic_data.get("title", "").lower():
                    relevance_score += 10
                    match_details.append("title")
                
                if query_lower in topic_data.get("description", "").lower():
                    relevance_score += 5
                    match_details.append("description")
                
                # Search in nested content
                def search_nested(obj, path=""):
                    nonlocal relevance_score, match_details
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            search_nested(value, f"{path}.{key}" if path else key)
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            search_nested(item, f"{path}[{i}]" if path else f"[{i}]")
                    elif isinstance(obj, str):
                        if query_lower in obj.lower():
                            relevance_score += 2
                            match_details.append(path)
                
                search_nested(topic_data)
                
                if relevance_score > 0:
                    results["matches"].append({
                        "topic": topic_key,
                        "title": topic_data.get("title", topic_key.replace("_", " ").title()),
                        "description": topic_data.get("description", ""),
                        "relevance_score": relevance_score,
                        "match_details": match_details,
                        "content": topic_data
                    })
        
        # Remove duplicates and sort by relevance
        seen_topics = set()
        unique_matches = []
        for match in results["matches"]:
            if match["topic"] not in seen_topics:
                unique_matches.append(match)
                seen_topics.add(match["topic"])
        
        results["matches"] = sorted(unique_matches, key=lambda x: x["relevance_score"], reverse=True)
        
        # Add related topics if few matches
        if len(results["matches"]) < 2:
            results["related_topics"] = [
                "ai_stack_overview - Understanding the overall architecture",
                "pipe_functions - Learn about the intelligent system management",
                "recovery_procedures - Emergency recovery and troubleshooting",
                "common_issues - Solutions to frequent problems"
            ]
        
        return results
    
    def get_topic_help(self, topic: str) -> Dict[str, Any]:
        """Get detailed help for a specific topic"""
        topic_key = topic.lower().replace(" ", "_").replace("-", "_")
        
        if topic_key in self.help_database:
            return {
                "topic": topic,
                "found": True,
                "content": self.help_database[topic_key]
            }
        else:
            # Try partial matching
            partial_matches = []
            for key, content in self.help_database.items():
                if topic_key in key or key in topic_key:
                    partial_matches.append({
                        "topic_key": key,
                        "title": content.get("title", key.replace("_", " ").title()),
                        "description": content.get("description", "")
                    })
            
            return {
                "topic": topic,
                "found": False,
                "partial_matches": partial_matches,
                "available_topics": list(self.help_database.keys())
            }
    
    def get_help_overview(self) -> Dict[str, Any]:
        """Get comprehensive help overview"""
        return {
            "ai_stack_help_system": {
                "description": "Comprehensive help and documentation for AI Stack",
                "available_topics": [
                    {
                        "key": key,
                        "title": content.get("title", key.replace("_", " ").title()),
                        "description": content.get("description", "")
                    }
                    for key, content in self.help_database.items()
                ],
                "usage_examples": [
                    "Help with GPU issues",
                    "Show recovery procedures", 
                    "Explain pipe functions",
                    "Common issues and solutions",
                    "Architecture overview",
                    "Fix network problems",
                    "Emergency recovery help",
                    "System troubleshooting"
                ]
            }
        }

    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {
            "module_id": self.module_id,
            "version": self.version,
            "name": "Help System",
            "capabilities": ["documentation", "help_search", "troubleshooting_guide"],
            "status": "ready"
        }
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        health_score = 100
        issues = []
        
        # Check if help database is loaded
        if not self.help_database:
            health_score -= 50
            issues.append("Help database not loaded")
        
        # Check help database completeness
        expected_topics = ["ai_stack_overview", "pipe_functions", "recovery_procedures", "common_issues"]
        missing_topics = [topic for topic in expected_topics if topic not in self.help_database]
        
        if missing_topics:
            health_score -= 10 * len(missing_topics)
            issues.extend([f"Missing help topic: {topic}" for topic in missing_topics])
        
        status = "healthy" if health_score >= 75 else "degraded" if health_score >= 50 else "unhealthy"
        
        return {
            "module_id": self.module_id,
            "status": status,
            "health_score": health_score,
            "issues": issues,
            "capabilities": ["help_system", "documentation"]
        }
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute help system with migrated functionality"""
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
            
            # Determine what type of help the user wants
            if not user_input.strip() or "help" in user_input_lower and len(user_input.strip()) < 10:
                # Show help overview
                result_data = self.get_help_overview()
                content = self._format_help_overview(result_data)
            elif "search" in user_input_lower:
                # Search for help topics
                search_query = user_input_lower.replace("search", "").strip()
                result_data = self.search_help(search_query)
                content = self._format_search_results(result_data)
            else:
                # Try to find specific help topic or search
                result_data = self.search_help(user_input)
                if result_data["matches"]:
                    content = self._format_search_results(result_data)
                else:
                    # Show help overview with suggestions
                    overview_data = self.get_help_overview()
                    content = self._format_help_overview_with_query(overview_data, user_input)
            
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
            logger.error(f"❌ Help system execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **Help System Error**: {str(e)}",
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _format_help_overview(self, result_data: Dict[str, Any]) -> str:
        """Format help overview for display"""
        help_system = result_data.get("ai_stack_help_system", {})
        topics = help_system.get("available_topics", [])
        examples = help_system.get("usage_examples", [])
        
        content = f"""## 📚 AI Stack Help System

{help_system.get('description', '')}

### Available Help Topics:
"""
        
        for topic in topics:
            content += f"**{topic['title']}** - {topic['description']}\n"
        
        content += "\n### Usage Examples:\n"
        for example in examples:
            content += f"- \"{example}\"\n"
        
        content += "\n*Ask specific questions or request help on any topic*"
        
        return content
    
    def _format_help_overview_with_query(self, overview_data: Dict[str, Any], query: str) -> str:
        """Format help overview with query-specific message"""
        content = self._format_help_overview(overview_data)
        content = f"## 🔍 No specific help found for '{query}'\n\n" + content
        content += "\n\n💡 **Suggestion**: Try searching for broader terms or ask about specific topics listed above."
        return content
    
    def _format_search_results(self, result_data: Dict[str, Any]) -> str:
        """Format help search results for display"""
        query = result_data.get("query", "")
        matches = result_data.get("matches", [])
        related = result_data.get("related_topics", [])
        
        content = f"## 🔍 Help Results for '{query}'\n\n"
        
        if matches:
            for match in matches[:3]:  # Show top 3 matches
                topic_content = match.get("content", {})
                content += f"### {match['title']}\n\n{match['description']}\n\n"
                
                # Add specific content based on topic type
                if "components" in topic_content:
                    content += "**Components**:\n"
                    for component in topic_content["components"]:
                        content += f"- {component}\n"
                
                if "capabilities" in topic_content:
                    content += "**Capabilities**:\n"
                    for capability in topic_content["capabilities"]:
                        content += f"- {capability}\n"
                
                if "usage_examples" in topic_content:
                    content += "**Usage Examples**:\n"
                    for example in topic_content["usage_examples"]:
                        content += f"- \"{example}\"\n"
                
                if "quick_fixes" in topic_content:
                    content += "**Quick Fixes**:\n"
                    for fix in topic_content["quick_fixes"]:
                        content += f"- `{fix}`\n"
                
                # Special formatting for LM Studio connectivity info
                if "symptoms" in topic_content and "solution" in topic_content:
                    content += "**Symptoms**:\n"
                    for symptom in topic_content.get("symptoms", []):
                        content += f"- {symptom}\n"
                    content += f"\n**Cause**: {topic_content.get('cause', 'Unknown')}\n\n"
                    content += f"**Solution**: {topic_content.get('solution', 'N/A')}\n\n"
                    if "access_url" in topic_content:
                        content += f"**Access URL**: {topic_content['access_url']}\n\n"
                    if "port" in topic_content:
                        content += f"**Local Port**: {topic_content['port']}\n\n"
                
                if "problems" in topic_content:
                    content += "**Common Issues**:\n"
                    for problem, details in topic_content["problems"].items():
                        content += f"- **{problem.replace('_', ' ').title()}**: {details.get('solution', 'N/A')}\n"
                
                content += "\n---\n\n"
        
        if related:
            content += "### 💡 Related Topics:\n"
            for topic in related:
                content += f"- {topic}\n"
        
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
help_module = HelpSystemModule()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return help_module.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return help_module.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""
    return help_module.health()

def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate input"""
    return help_module.validate(input_data)

if __name__ == "__main__":
    from pathlib import Path as _Path
    import sys as _sys
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
    from utilities.module_cli import run_module_cli
    run_module_cli(main, describe, health)
