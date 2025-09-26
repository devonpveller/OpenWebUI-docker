"""
AI Stack Unified Router Pipe - Single Access Point for All Functions

This unified router replaces the need for multiple separate pipe functions in OpenWebUI.
It intelligently routes user queries to the appropriate AI Stack functionality.

Key Benefits:
- Single pipe function in OpenWebUI (instead of 4+ separate functions)
- Intelligent routing based on natural language input
- Consolidated management and maintenance
- Better user experience with unified interface

Architecture:
- Routes to existing pipe modules: gpu_status_pipe, emergency_recovery_pipe, system_health_pipe, custom_tools_pipe, help_pipe
- Maintains all existing functionality while simplifying OpenWebUI management
- Uses keyword analysis and context detection for accurate routing
"""

from typing import Any, Dict, List, Optional, Tuple
import sys, os, importlib.util, logging, json
import time

# Ensure we can import from the ai_pipes directory
sys.path.append('/host_scripts/ai_pipes')
sys.path.append('/host_scripts')

def setup_logging() -> logging.Logger:
    """Configure logging for router operations"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("ai_stack_router")

logger = setup_logging()

class AIStackRouter:
    """Unified router for all AI Stack pipe functions"""
    
    def __init__(self):
        self.modules = {}
        self._load_modules()
    
    def _load_modules(self):
        """Load all available pipe modules"""
        module_configs = {
            'gpu_status': {
                'path': '/host_scripts/ai_pipes/gpu_status_pipe.py',
                'entrypoint': 'main',
                'description': 'GPU monitoring and diagnostics'
            },
            'emergency_recovery': {
                'path': '/host_scripts/ai_pipes/emergency_recovery_pipe.py', 
                'entrypoint': 'main',
                'description': 'System recovery and troubleshooting'
            },
            'system_health': {
                'path': '/host_scripts/ai_pipes/system_health_pipe.py',
                'entrypoint': 'main', 
                'description': 'Health monitoring and status checks'
            },
            'custom_tools': {
                'path': '/host_scripts/ai_pipes/custom_tools_pipe.py',
                'entrypoint': 'main',
                'description': 'Tool discovery and automation'
            },
            'help': {
                'path': '/host_scripts/ai_pipes/help_pipe.py',
                'entrypoint': 'main',
                'description': 'Help system and command discovery'
            }
        }
        
        for name, config in module_configs.items():
            try:
                if os.path.exists(config['path']):
                    spec = importlib.util.spec_from_file_location(f"_{name}_module", config['path'])
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        
                        if hasattr(module, config['entrypoint']):
                            self.modules[name] = {
                                'module': module,
                                'function': getattr(module, config['entrypoint']),
                                'description': config['description']
                            }
                            logger.info(f"✅ Loaded module: {name}")
                        else:
                            logger.warning(f"⚠️ Module {name} missing entrypoint: {config['entrypoint']}")
                else:
                    logger.warning(f"⚠️ Module file not found: {config['path']}")
            except Exception as e:
                logger.error(f"❌ Failed to load module {name}: {str(e)}")

    def analyze_user_input(self, user_input: str) -> Tuple[str, float]:
        """
        Analyze user input and determine the best route
        Returns: (module_name, confidence_score)
        """
        if not user_input:
            return "help", 1.0
            
        user_input_lower = user_input.lower()
        
        # Define routing patterns with confidence scoring (ordered by priority)
        routing_patterns = [
            # GPU-related queries (highest specificity first)
            {
                'module': 'gpu_status',
                'keywords': ['gpu', 'cuda', 'graphics', 'nvidia', 'rtx', 'vram', 'memory', 'torch', 'pytorch', 'availability'],
                'phrases': ['gpu status', 'gpu memory', 'cuda available', 'cuda availability', 'is my gpu working', 'gpu diagnostics', 'check gpu', 'rtx memory'],
                'confidence': 0.95
            },
            
            # Recovery and troubleshooting (high priority for problem-solving)
            {
                'module': 'emergency_recovery', 
                'keywords': ['recovery', 'fix', 'repair', 'broken', 'down', 'not working', 'emergency', 'restore', 'network', 'connectivity', 'tailscale', 'issues', 'problems'],
                'phrases': ['tailscale down', 'tailscale is down', 'network issues', 'connectivity problems', 'services not responding', 'system recovery', 'tailscale problems'],
                'confidence': 0.9
            },
            
            # System health and monitoring
            {
                'module': 'system_health',
                'keywords': ['health', 'monitor', 'docker', 'containers', 'services', 'system'],
                'phrases': ['system health', 'how is my system', 'container status', 'service status', 'system diagnostics', 'system doing'],
                'confidence': 0.85
            },
            
            # Tool discovery and automation
            {
                'module': 'custom_tools',
                'keywords': ['tools', 'available', 'scripts', 'automation', 'utilities'],
                'phrases': ['what tools', 'available tools', 'show tools', 'tool discovery', 'what commands'],
                'confidence': 0.8
            },
            
            # Help and guidance (lower priority, catches general queries)
            {
                'module': 'help',
                'keywords': ['help', 'guide', 'examples', 'how to', 'what can', 'functions'],
                'phrases': ['help me', 'what can i do', 'pipe functions', 'conversation examples'],
                'confidence': 0.75
            }
        ]
        
        best_match = None
        highest_confidence = 0.0
        
        for pattern in routing_patterns:
            confidence = 0.0
            
            # Check for exact phrase matches (higher weight)
            phrase_matches = sum(1 for phrase in pattern['phrases'] if phrase in user_input_lower)
            if phrase_matches > 0:
                confidence += phrase_matches * 0.7
            
            # Check for keyword matches
            keyword_matches = sum(1 for keyword in pattern['keywords'] if keyword in user_input_lower)
            if keyword_matches > 0:
                confidence += (keyword_matches / len(pattern['keywords'])) * 0.5
            
            # Apply pattern confidence multiplier
            final_confidence = confidence * pattern['confidence']
            
            if final_confidence > highest_confidence:
                highest_confidence = final_confidence
                best_match = pattern['module']
        
        # Default to help if no clear match (lowered threshold)
        if highest_confidence < 0.15:
            return "help", 0.5
            
        return best_match, highest_confidence
    
    def route_request(self, user_input: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Route the request to the appropriate module"""
        try:
            # Analyze input to determine routing
            target_module, confidence = self.analyze_user_input(user_input)
            
            logger.info(f"🎯 Routing to {target_module} (confidence: {confidence:.2f}) for input: '{user_input[:50]}...'")
            
            # Check if target module is available
            if target_module not in self.modules:
                return {
                    "service": "AI Stack Router",
                    "status": "error",
                    "message": f"Target module '{target_module}' not available",
                    "available_modules": list(self.modules.keys()),
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }
            
            # Special handling for custom_tools requests that should show full listing
            modified_payload = payload.copy()
            if (target_module == "custom_tools" and 
                any(phrase in user_input.lower() for phrase in [
                    "what tools", "available tools", "show tools", "list tools", 
                    "tools available", "what commands", "available commands",
                    "show me tools", "show all tools", "list all tools"
                ])):
                # Override input to empty to trigger full tool listing
                modified_payload["input"] = ""
                logger.info("🔄 Modified custom_tools input to empty for full tool listing")
            
            # Special handling for help requests that should route to custom_tools for tool listing
            elif (target_module == "help" and 
                  any(phrase in user_input.lower() for phrase in [
                      "show me all tools", "list available commands", "all tools", 
                      "show all commands", "list all commands", "what tools"
                  ])):
                # Redirect to custom_tools with empty input for comprehensive tool listing
                target_module = "custom_tools"
                modified_payload["input"] = ""
                logger.info("🔄 Redirected help query to custom_tools for comprehensive tool listing")
            
            # Execute the target function
            module_info = self.modules[target_module]
            result = module_info['function'](modified_payload)
            
            # Add routing metadata to result
            if isinstance(result, dict):
                result['_router_info'] = {
                    'routed_to': target_module,
                    'confidence': confidence,
                    'description': module_info['description'],
                    'router_timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
                }
            
            logger.info(f"✅ Successfully routed to {target_module}")
            return result
            
        except Exception as e:
            error_msg = f"❌ Router execution error: {str(e)}"
            logger.error(error_msg)
            return {
                "service": "AI Stack Router",
                "status": "error", 
                "message": error_msg,
                "user_input": user_input,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
    
    def get_router_status(self) -> Dict[str, Any]:
        """Get router status and available modules"""
        return {
            "service": "AI Stack Unified Router",
            "status": "operational",
            "loaded_modules": {
                name: info['description'] 
                for name, info in self.modules.items()
            },
            "module_count": len(self.modules),
            "capabilities": [
                "GPU monitoring and diagnostics",
                "System recovery and troubleshooting", 
                "Health monitoring and status checks",
                "Tool discovery and automation",
                "Help system and command guidance"
            ],
            "routing_confidence": "Intelligent keyword and phrase analysis",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

# Global router instance
router = AIStackRouter()

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for the unified AI Stack router
    
    Routes user input to appropriate AI Stack functionality:
    - GPU status and diagnostics
    - Emergency recovery and troubleshooting
    - System health monitoring  
    - Tool discovery and automation
    - Help and guidance
    """
    try:
        user_input = payload.get("input", "").strip()
        
        # If no input, show router status
        if not user_input:
            return router.get_router_status()
        
        # Special commands for router itself
        if user_input.lower() in ["router status", "router info", "modules", "loaded modules"]:
            return router.get_router_status()
        
        # Route the request
        return router.route_request(user_input, payload)
        
    except Exception as e:
        error_msg = f"❌ Router main error: {str(e)}"
        logger.error(error_msg)
        return {
            "service": "AI Stack Router",
            "status": "error",
            "message": error_msg,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

def process(input_data: str) -> str:
    """CLI compatibility function"""
    try:
        payload = json.loads(input_data) if input_data.strip() else {"input": ""}
        result = main(payload)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        # Treat as direct text input
        result = main({"input": input_data})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "service": "AI Stack Router",
            "status": "error",
            "message": f"Process error: {str(e)}",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }, indent=2)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Command line usage
        input_text = " ".join(sys.argv[1:])
        result = process(input_text)
        print(result)
    else:
        # Interactive mode - show router status
        result = main({"input": ""})
        print(json.dumps(result, indent=2, ensure_ascii=False))