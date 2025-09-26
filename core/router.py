"""
AI Stack Refactored Router - Core Infrastructure

This is the new manifest-driven router based on the refactoring guide.
Implements explicit contracts, module isolation, and comprehensive observability.
"""

from __future__ import annotations
import asyncio
import json
import jsonschema
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from enum import Enum

# Add paths for existing modules during migration
sys.path.append('/host_scripts/ai_pipes')
sys.path.append('/host_scripts')

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
    
    def __init__(self, schema_dir: str = "/host_scripts/schemas"):
        self.schema_dir = Path(schema_dir)
        self._schemas = {}
        self._load_schemas()
    
    def _load_schemas(self):
        """Load all schema files"""
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
        if "request_envelope" not in self._schemas:
            return True, None  # Skip validation if schema not loaded
        
        try:
            jsonschema.validate(request_data, self._schemas["request_envelope"])
            return True, None
        except jsonschema.ValidationError as e:
            return False, f"Request validation error: {e.message}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def validate_result(self, result_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate result envelope against schema"""
        if "module_result" not in self._schemas:
            return True, None  # Skip validation if schema not loaded
        
        try:
            jsonschema.validate(result_data, self._schemas["module_result"])
            return True, None
        except jsonschema.ValidationError as e:
            return False, f"Result validation error: {e.message}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"

    def validate_manifest(self, manifest_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate module manifest against schema"""
        if "module_manifest" not in self._schemas:
            return True, None  # Skip validation if schema not loaded
        
        try:
            jsonschema.validate(manifest_data, self._schemas["module_manifest"])
            return True, None
        except jsonschema.ValidationError as e:
            return False, f"Manifest validation error: {e.message}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"

class ModuleRegistry:
    """Registry for managing module manifests and discovery"""
    
    def __init__(self, modules_dir: str = "/host_scripts/modules"):
        self.modules_dir = Path(modules_dir)
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
    
    def _load_module_manifest(self, manifest_file: Path):
        """Load and validate a single module manifest"""
        try:
            with open(manifest_file, 'r') as f:
                manifest = json.load(f)
            
            # Validate manifest
            is_valid, error = self.validator.validate_manifest(manifest)
            if not is_valid:
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
    
    def update_module_status(self, slug: str, status: ModuleStatus):
        """Update module status"""
        if slug in self.modules:
            self.module_status[slug] = status
            self.logger.info(f"📊 Module {slug} status: {status.value}")

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
            format='%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        return logging.getLogger("ai_stack_router")
    
    def _setup_adapters(self):
        """Setup execution adapters"""
        # For now, use legacy adapter for backward compatibility
        from .legacy_adapter import LegacyModuleAdapter
        self._execution_adapters["legacy"] = LegacyModuleAdapter(self.logger)
    
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
            session=additional_data.get("session") if additional_data else None,
            attachments=additional_data.get("attachments", []) if additional_data else [],
            context=additional_data.get("context") if additional_data else None
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
                # Fallback to legacy routing for backward compatibility
                return self._legacy_route(request)
            
            # Execute module
            return self._execute_module(target_module, request)
            
        except Exception as e:
            self.logger.error(f"❌ Router error: {e}", extra={"request_id": request.request_id})
            return self._create_error_result(request.request_id, "router", 
                                           "EXECUTION_ERROR", str(e))
    
    def _analyze_input_for_routing(self, user_input: Union[str, Dict[str, Any]]) -> str:
        """Analyze input to determine target module"""
        if isinstance(user_input, dict):
            # Structured input - check for explicit module
            return user_input.get("module", "help")
        
        # Text analysis for routing
        input_lower = str(user_input).lower()
        
        # Define routing patterns
        if any(keyword in input_lower for keyword in ["gpu", "cuda", "graphics", "nvidia"]):
            return "gpu-status"
        elif any(keyword in input_lower for keyword in ["recovery", "fix", "repair", "emergency"]):
            return "emergency-recovery"
        elif any(keyword in input_lower for keyword in ["health", "status", "monitor"]):
            return "system-health"
        elif any(keyword in input_lower for keyword in ["tools", "available", "commands"]):
            return "custom-tools"
        else:
            return "help"
    
    def _execute_module(self, module_slug: str, request: RequestEnvelope) -> ModuleResult:
        """Execute a specific module"""
        module_manifest = self.registry.get_module(module_slug)
        if not module_manifest:
            return self._create_error_result(request.request_id, module_slug, 
                                           "MODULE_NOT_FOUND", f"Module {module_slug} not found")
        
        # Determine execution adapter
        entry_kind = module_manifest["entry"]["kind"]
        adapter = self._execution_adapters.get("legacy")  # Use legacy for now
        
        if not adapter:
            return self._create_error_result(request.request_id, module_slug, 
                                           "NO_ADAPTER", f"No adapter for {entry_kind}")
        
        # Execute module
        return adapter.execute(module_manifest, request)
    
    def _legacy_route(self, request: RequestEnvelope) -> ModuleResult:
        """Fallback to legacy routing during migration"""
        try:
            # Import and use existing router for backward compatibility
            from ai_stack_router import router as legacy_router
            
            # Convert to legacy format
            legacy_payload = {
                "input": str(request.input),
                "user_id": request.user["id"],
                "timestamp": request.timestamp,
                "messages": []  # Default empty
            }
            
            # Execute legacy router
            result = legacy_router.route_request(str(request.input), legacy_payload)
            
            # Convert result to new format
            return ModuleResult(
                request_id=request.request_id,
                module_id="legacy-router",
                status=ExecutionStatus.OK,
                content=self._format_legacy_result(result),
                structured_data=result if isinstance(result, dict) else None,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            
        except Exception as e:
            return self._create_error_result(request.request_id, "legacy-router", 
                                           "LEGACY_ERROR", str(e))
    
    def _format_legacy_result(self, result: Any) -> str:
        """Format legacy result for display"""
        if isinstance(result, dict):
            # Extract meaningful content from legacy format
            if "status" in result and result["status"] == "error":
                return f"❌ **Error**: {result.get('message', 'Unknown error')}"
            
            # Format structured result
            content_parts = []
            if "service" in result:
                content_parts.append(f"**{result['service']}**")
            
            if "message" in result:
                content_parts.append(result["message"])
            
            if "description" in result:
                content_parts.append(result["description"])
            
            return "\n\n".join(content_parts) if content_parts else str(result)
        
        return str(result)
    
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
                    "name": manifest["name"],
                    "description": manifest.get("description", ""),
                    "capabilities": manifest.get("capabilities", []),
                    "version": manifest["version"]
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