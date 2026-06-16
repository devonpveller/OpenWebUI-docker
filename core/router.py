"""
AI Stack Refactored Router - Core Infrastructure

This implements the intended manifest-driven router architecture from the refactoring guide.
Implements explicit contracts, module isolation, and comprehensive observability.
"""

from __future__ import annotations
import json
import logging
import os
import sys
import time
import uuid
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from enum import Enum

# Environment-aware path setup for container vs host
if os.path.exists('/host_project/scripts'):
    # Container environment - use project mount
    sys.path.append('/host_project/scripts/ai_pipes')
    sys.path.append('/host_project/scripts')
    MODULES_DIR = '/host_project/modules'
    SCHEMAS_DIR = '/host_project/schemas'
else:
    # Host environment - use relative paths
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)  # core -> ai-stack
    sys.path.append(os.path.join(project_root, 'scripts', 'ai_pipes'))
    sys.path.append(os.path.join(project_root, 'scripts'))
    MODULES_DIR = os.path.join(project_root, 'modules')
    SCHEMAS_DIR = os.path.join(project_root, 'schemas')

# Try to import jsonschema, fall back gracefully if not available
try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False
    logging.warning("⚠️ jsonschema not available - schema validation disabled")

class ModuleStatus(Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

class ExecutionStatus(Enum):
    OK = "ok"
    ERROR = "error"
    PARTIAL = "partial"
    STREAMING_END = "streaming_end"

@dataclass
class RequestEnvelope:
    """Request envelope following the schema contract"""
    version: str
    request_id: str
    timestamp: str
    user: Dict[str, Any]
    input: Union[str, Dict[str, Any]]
    session: Optional[Dict[str, Any]] = None
    locale: str = "en-US"
    timezone: Optional[str] = None
    attachments: List[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    capabilities_allowed: List[str] = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []
        if self.capabilities_allowed is None:
            self.capabilities_allowed = []

@dataclass
class ModuleResult:
    """Module result envelope following the schema contract"""
    request_id: str
    module_id: str
    status: ExecutionStatus
    content: str
    structured_data: Optional[Dict[str, Any]] = None
    artifacts: List[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    diagnostics: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.artifacts is None:
            self.artifacts = []
        if self.events is None:
            self.events = []
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

class SchemaValidator:
    """JSON Schema validation for envelopes"""
    
    def __init__(self, schema_dir: Optional[str] = None):
        self.schema_dir = Path(schema_dir or SCHEMAS_DIR)
        self._schemas = {}
        self._load_schemas()
    
    def _load_schemas(self):
        """Load all schema files"""
        if not JSONSCHEMA_AVAILABLE:
            logging.warning("⚠️ Schema validation disabled - jsonschema not available")
            return
            
        try:
            schema_files = {
                "request_envelope": self.schema_dir / "request_envelope.schema.json",
                "module_result": self.schema_dir / "module_result.schema.json",
                "module_manifest": self.schema_dir / "module_manifest.schema.json"
            }
            
            for name, path in schema_files.items():
                if path.exists():
                    with open(path, 'r') as f:
                        self._schemas[name] = json.load(f)
                        logging.info(f"✅ Loaded schema: {name}")
                else:
                    logging.warning(f"⚠️ Schema file not found: {path}")
        except Exception as e:
            logging.error(f"❌ Error loading schemas: {e}")
    
    def validate_request(self, request_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate request envelope against schema"""
        if not JSONSCHEMA_AVAILABLE or "request_envelope" not in self._schemas:
            return True, None  # Skip validation if schema not available
        
        try:
            jsonschema.validate(request_data, self._schemas["request_envelope"])
            return True, None
        except jsonschema.ValidationError as e:
            return False, f"Request validation error: {e.message}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def validate_manifest(self, manifest_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate module manifest against schema"""
        if not JSONSCHEMA_AVAILABLE or "module_manifest" not in self._schemas:
            return True, None  # Skip validation if schema not available
        
        try:
            jsonschema.validate(manifest_data, self._schemas["module_manifest"])
            return True, None
        except jsonschema.ValidationError as e:
            return False, f"Manifest validation error: {e.message}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"

class DirectModuleAdapter:
    """Direct execution adapter for Python modules"""
    
    def __init__(self, logger):
        self.logger = logger
    
    def execute(self, manifest: Dict[str, Any], request: RequestEnvelope) -> ModuleResult:
        """Execute module directly"""
        try:
            module_slug = manifest["slug"]
            entry_path = manifest["entry"]["path"]
            
            # Resolve the full path to the module file
            if os.path.isabs(entry_path) and os.path.exists(entry_path):
                module_path = entry_path
            else:
                # Handle relative paths - if entry_path already includes directory structure, use as-is
                if entry_path.startswith('service/'):
                    module_path = os.path.join(MODULES_DIR, module_slug, entry_path)
                else:
                    # Default to service directory for bare filenames
                    module_path = os.path.join(MODULES_DIR, module_slug, "service", entry_path)
            
            if not os.path.exists(module_path):
                return ModuleResult(
                    request_id=request.request_id,
                    module_id=module_slug,
                    status=ExecutionStatus.ERROR,
                    content=f"❌ Module file not found: {module_path}",
                    error={"code": "MODULE_NOT_FOUND", "message": f"Module file not found: {module_path}"}
                )
            
            # Load and execute module
            spec = importlib.util.spec_from_file_location(f"_{module_slug}_module", module_path)
            if spec is None or spec.loader is None:
                return ModuleResult(
                    request_id=request.request_id,
                    module_id=module_slug,
                    status=ExecutionStatus.ERROR,
                    content=f"❌ Cannot load module: {module_path}",
                    error={"code": "MODULE_LOAD_ERROR", "message": f"Cannot load module: {module_path}"}
                )
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Execute the module's main function
            if not hasattr(module, 'main'):
                return ModuleResult(
                    request_id=request.request_id,
                    module_id=module_slug,
                    status=ExecutionStatus.ERROR,
                    content=f"❌ Module missing 'main' function: {module_slug}",
                    error={"code": "MISSING_ENTRYPOINT", "message": f"Module missing 'main' function"}
                )
            
            # Convert request to legacy format for existing modules during migration
            legacy_payload = {
                "input": str(request.input),
                "user_id": request.user["id"],
                "timestamp": request.timestamp,
                "messages": request.context.get("messages", []) if request.context else []
            }
            
            # Execute module
            result = module.main(legacy_payload)
            
            # Convert result to new envelope format
            if isinstance(result, dict) and result.get("module_id"):
                # Already in new format
                return ModuleResult(
                    request_id=request.request_id,
                    module_id=result.get("module_id", module_slug),
                    status=ExecutionStatus.OK if result.get("status") == "ok" else ExecutionStatus.ERROR,
                    content=result.get("content", str(result)),
                    structured_data=result.get("structured_data"),
                    diagnostics=result.get("diagnostics")
                )
            else:
                # Legacy format - convert
                content = result.get("content") if isinstance(result, dict) else str(result)
                return ModuleResult(
                    request_id=request.request_id,
                    module_id=module_slug,
                    status=ExecutionStatus.OK,
                    content=content,
                    structured_data=result if isinstance(result, dict) else None
                )
                
        except Exception as e:
            self.logger.error(f"❌ Module execution error {module_slug}: {e}")
            return ModuleResult(
                request_id=request.request_id,
                module_id=manifest.get("slug", "unknown"),
                status=ExecutionStatus.ERROR,
                content=f"❌ Execution error: {str(e)}",
                error={"code": "EXECUTION_ERROR", "message": str(e)}
            )

class ModuleRegistry:
    """Registry for managing module manifests and discovery"""
    
    def __init__(self, modules_dir: Optional[str] = None):
        self.modules_dir = Path(modules_dir or MODULES_DIR)
        self.modules: Dict[str, Dict[str, Any]] = {}
        self.module_status: Dict[str, ModuleStatus] = {}
        self.validator = SchemaValidator()
        self.logger = logging.getLogger("module_registry")
        self._discover_modules()
    
    def _discover_modules(self):
        """Discover and load module manifests"""
        if not self.modules_dir.exists():
            self.logger.warning(f"⚠️ Modules directory not found: {self.modules_dir}")
            return
        
        for module_path in self.modules_dir.iterdir():
            if module_path.is_dir():
                manifest_file = module_path / "module.manifest.json"
                if manifest_file.exists():
                    self._load_module_manifest(manifest_file)
                else:
                    # Try to create a default manifest for existing modules
                    self._create_default_manifest(module_path)
    
    def _create_default_manifest(self, module_path: Path):
        """Create a default manifest for legacy modules"""
        module_name = module_path.name
        service_dir = module_path / "service"
        
        # Look for Python files in service directory
        if service_dir.exists():
            py_files = list(service_dir.glob("*.py"))
            if py_files:
                main_file = py_files[0].name  # Use first Python file
                
                default_manifest = {
                    "slug": module_name,
                    "name": module_name.replace("-", " ").title(),
                    "version": "1.0.0",
                    "description": f"Legacy module {module_name}",
                    "entry": {
                        "kind": "python",
                        "path": main_file
                    },
                    "capabilities": [],
                    "schema": {
                        "input": {},
                        "output": {}
                    },
                    "limits": {
                        "timeout_ms": 30000
                    },
                    "help": {
                        "short": f"Legacy {module_name} module",
                        "long": f"Automatically generated manifest for legacy module {module_name}"
                    },
                    "routerCompatibility": "v1.0.0"
                }
                
                # Store in memory (don't write to disk)
                self.modules[module_name] = default_manifest
                self.module_status[module_name] = ModuleStatus.READY
                self.logger.info(f"✅ Generated default manifest for legacy module: {module_name}")
    
    def _load_module_manifest(self, manifest_file: Path):
        """Load and validate a single module manifest"""
        try:
            with open(manifest_file, 'r') as f:
                manifest = json.load(f)
            
            # Validate manifest
            is_valid, error = self.validator.validate_manifest(manifest)
            if not is_valid and JSONSCHEMA_AVAILABLE:
                self.logger.error(f"❌ Invalid manifest {manifest_file}: {error}")
                return
            
            slug = manifest["slug"]
            version = manifest["version"]
            
            # Check for conflicts
            if slug in self.modules:
                existing_version = self.modules[slug]["version"]
                self.logger.warning(f"⚠️ Module conflict: {slug} (existing: {existing_version}, new: {version})")
                # Keep highest version (simple comparison)
                if version <= existing_version:
                    return
            
            # Store module
            self.modules[slug] = manifest
            self.module_status[slug] = ModuleStatus.READY
            
            self.logger.info(f"✅ Loaded module: {slug}@{version}")
            
        except Exception as e:
            self.logger.error(f"❌ Error loading manifest {manifest_file}: {e}")
    
    def get_module(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get module manifest by slug"""
        return self.modules.get(slug)
    
    def get_ready_modules(self) -> Dict[str, Dict[str, Any]]:
        """Get all modules with READY status"""
        return {
            slug: manifest for slug, manifest in self.modules.items()
            if self.module_status.get(slug) == ModuleStatus.READY
        }

class AIStackRouter:
    """Main router implementing the refactored architecture"""
    
    def __init__(self):
        self.logger = self._setup_logging()
        self.registry = ModuleRegistry()
        self.validator = SchemaValidator()
        self._execution_adapters = {}
        self._setup_adapters()
    
    def _setup_logging(self) -> logging.Logger:
        """Setup structured logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        return logging.getLogger("ai_stack_router")
    
    def _setup_adapters(self):
        """Setup execution adapters"""
        self._execution_adapters["direct"] = DirectModuleAdapter(self.logger)
    
    def create_request_envelope(self, user_input: str, user_data: Dict[str, Any], 
                              additional_data: Optional[Dict[str, Any]] = None) -> RequestEnvelope:
        """Create a request envelope from OpenWebUI input"""
        request_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        
        return RequestEnvelope(
            version="v1.0.0",
            request_id=request_id,
            timestamp=timestamp,
            user={
                "id": user_data.get("id", "unknown"),
                "roles": user_data.get("roles", []),
                "permissions": user_data.get("permissions", [])
            },
            input=user_input,
            session=additional_data.get("session") or {
                "conversation_id": "default",
                "turn_number": 1
            } if additional_data else {
                "conversation_id": "default",
                "turn_number": 1
            },
            timezone="UTC",  # Provide default timezone instead of None
            attachments=additional_data.get("attachments", []) if additional_data else [],
            context=additional_data.get("context") or {
                "prior_turns": [],
                "system_hints": {}
            } if additional_data else {
                "prior_turns": [],
                "system_hints": {}
            }
        )
    
    def route_request(self, request: RequestEnvelope) -> ModuleResult:
        """Route request to appropriate module"""
        try:
            # Validate request
            request_dict = asdict(request)
            is_valid, error = self.validator.validate_request(request_dict)
            if not is_valid:
                return self._create_error_result(request.request_id, "router", 
                                               "VALIDATION_ERROR", error)
            
            # Analyze input for routing
            target_module = self._analyze_input_for_routing(request.input)
            
            # Check module availability
            module_manifest = self.registry.get_module(target_module)
            if not module_manifest:
                return self._create_error_result(request.request_id, target_module,
                                               "MODULE_NOT_FOUND", f"Module {target_module} not found")
            
            # Execute module
            return self._execute_module(target_module, request)
            
        except Exception as e:
            self.logger.error(f"❌ Router error: {e}")
            return self._create_error_result(request.request_id, "router", 
                                           "EXECUTION_ERROR", str(e))
    
    def _analyze_input_for_routing(self, user_input: Union[str, Dict[str, Any]]) -> str:
        """Analyze input to determine target module"""
        if isinstance(user_input, dict):
            # Structured input - check for explicit module
            return user_input.get("module", "help-system")
        
        # Text analysis for routing
        input_lower = str(user_input).lower()
        
        # Define routing patterns (priority order matters - most specific first)
        
        input_stripped = input_lower.strip()

        # Pull the user's intent from the first non-blank line and its first
        # word. OpenWebUI's RAG/code-interpreter features can prepend or append
        # retrieved chunks to the user's message; matching on the head of the
        # input keeps routing robust against that.
        first_line = ""
        for _line in input_lower.splitlines():
            _stripped = _line.strip()
            if _stripped:
                first_line = _stripped
                break
        first_word = first_line.split()[0] if first_line else ""

        # Tailscale serve management (must be before general "fix" keyword)
        if any(keyword in input_lower for keyword in ["serve", "serving", "expose", "tailscale"]) and \
           any(keyword in input_lower for keyword in ["start", "stop", "status", "lmstudio", "service", "port"]):
            return "custom-tools"  # Routes to custom-tools which will handle tailscale_serve_pipe

        # Stack / server status — bare "status", "inventory", "overview",
        # "show services", "status of X". Surfaces the rich containers +
        # Tailnet URLs + processing + GPU view from tailscale_serve_pipe
        # (build_stack_status). Must run BEFORE the generic health/monitor
        # branch below.
        elif (
            input_stripped in {"status", "inventory", "overview", "show", "list", "stack"}
            or first_line in {"status", "inventory", "overview", "show", "list", "stack"}
            or first_word in {"status", "inventory"}
            or any(p in input_lower for p in (
                "stack status", "stack-status", "show services", "list services",
                "show tailnet", "tailnet services", "service overview", "service map",
            ))
            or input_stripped.startswith(("status of ", "status for ", "inventory of "))
            or first_line.startswith(("status of ", "status for ", "inventory of "))
        ):
            return "custom-tools"

        # Admin help — list available admin commands. Routed BEFORE the generic
        # help-system fallthrough so it surfaces the tailnet/stack command set.
        # Exact-match-only for bare "help"/"?" so phrases like
        # "help with GPU issues" still reach the help-system module.
        elif (
            input_stripped in {
                "help", "?", "admin help", "stack help", "tailscale help",
                "commands", "admin commands", "stack commands",
            }
            or first_line in {
                "help", "?", "admin help", "stack help", "tailscale help",
                "commands", "admin commands", "stack commands",
            }
            or any(p in input_lower for p in (
                "available commands", "list commands", "show commands",
                "what commands", "admin commands", "stack commands",
            ))
        ):
            return "custom-tools"

        # LLM traffic / GPU-demand attribution — per-caller spend ledger from the
        # LiteLLM gateway. MUST precede the gpu-status branch: phrases like
        # "who is using gpu" / "gpu demand" / "gpu traffic" contain "gpu" but want
        # the attribution view (who is driving load), not the nvidia-smi check.
        elif any(p in input_lower for p in (
            "llm traffic", "llm demand", "llm spend", "llm cost", "llm usage",
            "gateway traffic", "gateway usage", "llama traffic", "gpu demand",
            "gpu traffic", "who is using gpu", "who's using gpu", "whos using gpu",
            "who is using the gpu", "who is driving", "what is using the gpu",
        )):
            return "llm-traffic"

        # GPU monitoring. "smi" routes here so bare "smi" hits the nvidia-smi
        # process-detail check inside the gpu-status module.
        elif any(keyword in input_lower for keyword in ["gpu", "cuda", "graphics", "nvidia", "smi"]):
            return "gpu-status"

        # Emergency recovery (general recovery, but NOT tailscale serve)
        elif any(keyword in input_lower for keyword in ["recovery", "fix", "repair", "emergency", "restart", "ollama"]) and \
             not any(keyword in input_lower for keyword in ["serve", "serving", "expose"]):
            return "emergency-recovery"

        # Health and status monitoring (system-health) — kept for explicit
        # "health" / "monitor" queries; bare "status" is handled above.
        elif any(keyword in input_lower for keyword in ["health", "monitor"]) and \
             not any(keyword in input_lower for keyword in ["serve", "serving"]):
            return "system-health"
        
        # Custom tools discovery
        elif any(keyword in input_lower for keyword in ["tools", "commands"]):
            return "custom-tools"
        
        # LM Studio help
        elif input_lower.strip() == "lmstudio":
            return "help-system"
        
        # Default to help
        else:
            return "help-system"
    
    def _execute_module(self, module_slug: str, request: RequestEnvelope) -> ModuleResult:
        """Execute a specific module"""
        module_manifest = self.registry.get_module(module_slug)
        if not module_manifest:
            return self._create_error_result(request.request_id, module_slug, 
                                           "MODULE_NOT_FOUND", f"Module {module_slug} not found")
        
        # Use direct execution adapter
        adapter = self._execution_adapters.get("direct")
        
        if not adapter:
            return self._create_error_result(request.request_id, module_slug, 
                                           "NO_ADAPTER", f"No execution adapter available")
        
        # Execute module
        return adapter.execute(module_manifest, request)
    
    def _create_error_result(self, request_id: str, module_id: str, 
                           error_code: str, error_message: str) -> ModuleResult:
        """Create standardized error result"""
        return ModuleResult(
            request_id=request_id,
            module_id=module_id,
            status=ExecutionStatus.ERROR,
            content=f"❌ **Error**: {error_message}",
            error={
                "code": error_code,
                "message": error_message,
                "retriable": error_code not in ["VALIDATION_ERROR", "MODULE_NOT_FOUND"]
            }
        )
    
    def get_available_modules(self) -> Dict[str, Any]:
        """Get available modules for discovery"""
        ready_modules = self.registry.get_ready_modules()
        
        return {
            "service": "AI Stack Router",
            "status": "operational",
            "available_modules": {
                slug: {
                    "name": manifest.get("name", slug),
                    "description": manifest.get("description", ""),
                    "capabilities": manifest.get("capabilities", []),
                    "version": manifest.get("version", "1.0.0")
                }
                for slug, manifest in ready_modules.items()
            },
            "module_count": len(ready_modules),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

# Global router instance
router = AIStackRouter()

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point maintaining backward compatibility"""
    try:
        user_input = payload.get("input", "").strip()
        user_data = {
            "id": payload.get("user_id", "unknown"),
            "roles": payload.get("user_roles", []),
            "permissions": payload.get("user_permissions", [])
        }
        
        # Create request envelope
        request = router.create_request_envelope(user_input, user_data, payload)
        
        # Route request
        result = router.route_request(request)
        
        # Convert to legacy format for backward compatibility
        return {
            "service": f"AI Stack Router ({result.module_id})",
            "status": result.status.value,
            "content": result.content,
            "structured_data": result.structured_data,
            "request_id": result.request_id,
            "timestamp": result.timestamp,
            "module_id": result.module_id,
            **(result.error or {})
        }
        
    except Exception as e:
        return {
            "service": "AI Stack Router",
            "status": "error",
            "message": f"Router main error: {str(e)}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

if __name__ == "__main__":
    # CLI compatibility
    if len(sys.argv) > 1:
        input_text = " ".join(sys.argv[1:])
        result = main({"input": input_text})
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        result = router.get_available_modules()
        print(json.dumps(result, indent=2, ensure_ascii=False))