#!/usr/bin/env python3
"""
AI Stack Module Scaffolding Generator

Generates complete module directory structures and manifests based on templates.
Implements the scaffolding automation described in the refactoring guide.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import argparse

def create_manifest_template(module_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a module manifest from template data"""
    return {
        "name": module_data.get("name", "New AI Stack Module"),
        "slug": module_data.get("slug", "new-module"),
        "version": module_data.get("version", "1.0.0"),
        "description": module_data.get("description", "AI Stack module description"),
        "author": module_data.get("author", "AI Stack Team"),
        "license": "MIT",
        "entry": {
            "kind": module_data.get("entry_kind", "cli"),
            "path": f"service/{module_data.get('slug', 'new-module').replace('-', '_')}.py",
            "function": "main"
        },
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "format": "uuid"
                    },
                    "input": {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": f"Text query for {module_data.get('name', 'module')}"
                            },
                            {
                                "type": "object",
                                "description": "Structured input parameters"
                            }
                        ]
                    },
                    "user": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"}
                        }
                    }
                },
                "required": ["request_id", "input"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "format": "uuid"},
                    "module_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["ok", "error", "partial", "streaming_end"]},
                    "content": {"type": "string", "description": "Markdown-formatted response"},
                    "structured_data": {"type": "object"}
                }
            }
        },
        "capabilities": module_data.get("capabilities", ["system_monitoring"]),
        "limits": {
            "timeout_ms": module_data.get("timeout_ms", 30000),
            "memory_mb": module_data.get("memory_mb", 256),
            "cpu_cores": module_data.get("cpu_cores", 1.0),
            "input_size_mb": 5,
            "concurrent_requests": 5
        },
        "environment": {
            "kind": module_data.get("env_kind", "venv"),
            "python_version": ">=3.8",
            "dependencies": module_data.get("dependencies", []),
            "gpu_required": module_data.get("gpu_required", False)
        },
        "health": {
            "probe_interval_ms": 30000,
            "startup_timeout_ms": 60000
        },
        "help": {
            "short": module_data.get("help_short", f"{module_data.get('name', 'Module')} functionality"),
            "long": module_data.get("help_long", f"Detailed help for {module_data.get('name', 'the module')}"),
            "examples": module_data.get("examples", [
                {
                    "input": "help",
                    "description": "Get module help and usage information"
                }
            ])
        },
        "permissions": {
            "allowed_roles": ["user", "admin"],
            "safety_rating": module_data.get("safety_rating", "SAFE")
        },
        "compatibility": {
            "router_version": ">=1.0.0",
            "openwebui_version": ">=0.3.0"
        }
    }

def create_python_service_template(module_data: Dict[str, Any]) -> str:
    """Create Python service implementation template"""
    slug = module_data.get("slug", "new-module")
    name = module_data.get("name", "New Module")
    class_name = "".join(word.capitalize() for word in slug.replace("-", "_").split("_")) + "Module"
    
    return f'''#!/usr/bin/env python3
"""
{name} - AI Stack Refactored Module

Manifest-driven module implementing the new AI Stack architecture.
Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

def setup_logging() -> logging.Logger:
    """Setup module logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("{slug.replace('-', '_')}_module")

logger = setup_logging()

class {class_name}:
    """
    {name} implementing manifest-driven architecture
    
    TODO: Implement your module logic here
    """
    
    def __init__(self):
        self.module_id = "{slug}"
        self.version = "{module_data.get('version', '1.0.0')}"
    
    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {{
            "module_id": self.module_id,
            "version": self.version,
            "name": "{name}",
            "capabilities": {module_data.get('capabilities', ['system_monitoring'])},
            "status": "ready"
        }}
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        # TODO: Implement health checks specific to your module
        return {{
            "status": "healthy",
            "score": 100,
            "issues": [],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }}
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute module functionality"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Parse input
            input_data = request_data.get("input", "")
            
            # TODO: Implement your module logic here
            result_data = self._process_request(input_data)
            
            # Format response
            content = self._format_content(result_data)
            execution_time = int((time.time() - start_time) * 1000)
            
            return {{
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok",
                "content": content,
                "structured_data": result_data,
                "diagnostics": {{
                    "execution_time_ms": execution_time,
                    "module_version": self.version
                }},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}
            
        except Exception as e:
            logger.error(f"❌ {{self.module_id}} execution error: {{e}}")
            return {{
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **{{name}} Error**: {{str(e)}}",
                "error": {{
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                }},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}
    
    def validate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate input without execution"""
        required_fields = ["request_id", "input"]
        missing_fields = [field for field in required_fields if field not in input_data]
        
        if missing_fields:
            return {{
                "valid": False,
                "errors": [f"Missing required field: {{field}}" for field in missing_fields]
            }}
        
        # TODO: Add specific validation logic for your module
        
        return {{"valid": True, "errors": []}}
    
    def _process_request(self, input_data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process the actual request
        
        TODO: Replace this with your module's core logic
        """
        if isinstance(input_data, str):
            input_str = input_data.lower()
            
            # Example processing logic
            if "status" in input_str:
                return {{
                    "type": "status",
                    "status": "operational",
                    "message": "Module is running normally"
                }}
            elif "help" in input_str:
                return {{
                    "type": "help",
                    "module": "{name}",
                    "description": "{module_data.get('description', 'AI Stack module')}",
                    "usage": "Send queries to interact with this module"
                }}
            else:
                return {{
                    "type": "generic",
                    "input": input_data,
                    "processed": True,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }}
        
        # Handle structured input
        return {{
            "type": "structured",
            "input": input_data,
            "processed": True
        }}
    
    def _format_content(self, data: Dict[str, Any]) -> str:
        """Format data as markdown content"""
        content = [f"**{name}**", ""]
        
        data_type = data.get("type", "generic")
        
        if data_type == "status":
            content.extend([
                f"**Status**: {{data.get('status', 'Unknown')}}",
                f"**Message**: {{data.get('message', 'No message')}}"
            ])
        
        elif data_type == "help":
            content.extend([
                f"**Description**: {{data.get('description', 'No description')}}",
                f"**Usage**: {{data.get('usage', 'No usage information')}}"
            ])
        
        elif data_type == "error":
            content.extend([
                f"❌ **Error**: {{data.get('error', 'Unknown error')}}",
                f"**Details**: {{data.get('details', 'No details available')}}"
            ])
        
        else:
            # Generic formatting
            content.append(f"**Request processed successfully**")
            if "message" in data:
                content.append(f"**Result**: {{data['message']}}")
        
        content.extend(["", f"*Updated: {{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}}*"])
        
        return "\\n".join(content)

# Module instance
{slug.replace('-', '_')}_module = {class_name}()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return {slug.replace('-', '_')}_module.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return {slug.replace('-', '_')}_module.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""
    return {slug.replace('-', '_')}_module.health()

def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate input"""
    return {slug.replace('-', '_')}_module.validate(input_data)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # CLI mode
        if sys.argv[1] == "--describe":
            print(json.dumps(describe(), indent=2))
        elif sys.argv[1] == "--health":
            print(json.dumps(health(), indent=2))
        else:
            # Process input from stdin or args
            if sys.stdin.isatty():
                input_text = " ".join(sys.argv[1:])
                input_data = {{"request_id": str(time.time()), "input": input_text}}
            else:
                input_data = json.loads(sys.stdin.read())
            
            result = main(input_data)
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Interactive mode
        print(json.dumps(describe(), indent=2))
'''

def create_readme_template(module_data: Dict[str, Any]) -> str:
    """Create README template for module"""
    slug = module_data.get("slug", "new-module")
    name = module_data.get("name", "New Module")
    
    return f'''# {name}

**Module ID**: `{slug}`  
**Version**: {module_data.get('version', '1.0.0')}  
**Capabilities**: {', '.join(module_data.get('capabilities', ['system_monitoring']))}

## Overview

{module_data.get('description', 'AI Stack module description')}

## Usage

### Basic Usage
```
{slug} status
{slug} help
```

### Structured Input
```json
{{
  "request_id": "uuid-here",
  "input": {{
    "action": "status"
  }}
}}
```

## Capabilities

{chr(10).join(f"- {cap}" for cap in module_data.get('capabilities', ['system_monitoring']))}

## Configuration

Environment requirements:
- Python {module_data.get('python_version', '>=3.8')}
- Dependencies: {', '.join(module_data.get('dependencies', ['None']))}
- GPU Required: {'Yes' if module_data.get('gpu_required', False) else 'No'}

## Development

### Structure
```
{slug}/
├── module.manifest.json    # Module configuration
├── service/
│   └── {slug.replace('-', '_')}.py        # Main implementation
├── env/                    # Virtual environment (auto-generated)
├── artifacts/              # Generated artifacts
└── docs/
    └── README.md          # This file
```

### Local Testing
```bash
# Health check
python service/{slug.replace('-', '_')}.py --health

# Describe module
python service/{slug.replace('-', '_')}.py --describe

# Test execution
echo '{{"request_id": "test", "input": "status"}}' | python service/{slug.replace('-', '_')}.py
```

### Manifest Schema

The module follows the AI Stack manifest-driven architecture with:
- Explicit input/output contracts
- Resource limits and timeouts  
- Health monitoring configuration
- Security permissions and capabilities
- Structured error handling

## Integration

This module integrates with the AI Stack router and can be accessed through:
- Direct CLI execution
- Router-mediated requests
- OpenWebUI pipe functions

Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
'''

def create_module_scaffold(modules_dir: str, module_data: Dict[str, Any]) -> bool:
    """Create complete module scaffold"""
    slug = module_data.get("slug", "new-module")
    module_path = Path(modules_dir) / slug
    
    try:
        # Create directory structure
        module_path.mkdir(parents=True, exist_ok=True)
        (module_path / "service").mkdir(exist_ok=True)
        (module_path / "docs").mkdir(exist_ok=True)
        (module_path / "artifacts").mkdir(exist_ok=True)
        
        # Create manifest
        manifest = create_manifest_template(module_data)
        with open(module_path / "module.manifest.json", 'w') as f:
            json.dump(manifest, f, indent=2)
        
        # Create service implementation
        service_code = create_python_service_template(module_data)
        service_file = module_path / "service" / f"{slug.replace('-', '_')}.py"
        with open(service_file, 'w') as f:
            f.write(service_code)
        service_file.chmod(0o755)  # Make executable
        
        # Create README
        readme_content = create_readme_template(module_data)
        with open(module_path / "docs" / "README.md", 'w') as f:
            f.write(readme_content)
        
        print(f"✅ Created module scaffold: {module_path}")
        print(f"   - Manifest: module.manifest.json")
        print(f"   - Service: service/{slug.replace('-', '_')}.py")
        print(f"   - Documentation: docs/README.md")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating module scaffold: {e}")
        return False

def interactive_module_creation():
    """Interactive module creation wizard"""
    print("🚀 AI Stack Module Scaffolding Generator")
    print("=" * 50)
    
    module_data = {}
    
    # Basic information
    module_data["name"] = input("Module Name: ").strip() or "New AI Stack Module"
    default_slug = module_data["name"].lower().replace(" ", "-").replace("_", "-")
    module_data["slug"] = input(f"Module Slug [{default_slug}]: ").strip() or default_slug
    module_data["version"] = input("Version [1.0.0]: ").strip() or "1.0.0"
    module_data["description"] = input("Description: ").strip() or f"{module_data['name']} functionality"
    
    # Capabilities
    print("\nAvailable capabilities:")
    available_caps = [
        "system_monitoring", "gpu_access", "network_access", "file_processing",
        "code_generation", "data_analysis", "admin_operations", "security_sensitive"
    ]
    
    for i, cap in enumerate(available_caps):
        print(f"  {i+1}. {cap}")
    
    cap_input = input("Select capabilities (comma-separated numbers) [1]: ").strip()
    if cap_input:
        try:
            selected_indices = [int(x.strip()) - 1 for x in cap_input.split(",")]
            module_data["capabilities"] = [available_caps[i] for i in selected_indices if 0 <= i < len(available_caps)]
        except (ValueError, IndexError):
            module_data["capabilities"] = ["system_monitoring"]
    else:
        module_data["capabilities"] = ["system_monitoring"]
    
    # Environment
    module_data["gpu_required"] = input("Requires GPU access? [y/N]: ").lower().startswith('y')
    deps_input = input("Dependencies (comma-separated): ").strip()
    module_data["dependencies"] = [dep.strip() for dep in deps_input.split(",")] if deps_input else []
    
    # Safety rating
    safety_options = ["SAFE", "CAUTION", "RESTRICTED", "DESTRUCTIVE"]
    print(f"\nSafety ratings: {', '.join(safety_options)}")
    module_data["safety_rating"] = input("Safety Rating [SAFE]: ").strip().upper() or "SAFE"
    
    return module_data

def main():
    """Main scaffolding generator"""
    parser = argparse.ArgumentParser(description="AI Stack Module Scaffolding Generator")
    parser.add_argument("--modules-dir", default="/host_scripts/modules", 
                       help="Modules directory path")
    parser.add_argument("--config", help="JSON config file with module data")
    parser.add_argument("--interactive", "-i", action="store_true", 
                       help="Interactive module creation")
    parser.add_argument("--list-templates", action="store_true",
                       help="List available module templates")
    
    args = parser.parse_args()
    
    if args.list_templates:
        print("Available module templates:")
        templates = [
            ("basic", "Basic module template"),
            ("system-monitor", "System monitoring module"),
            ("gpu-access", "GPU-enabled module"),  
            ("file-processor", "File processing module"),
            ("network-tool", "Network operations module")
        ]
        
        for template_id, description in templates:
            print(f"  {template_id}: {description}")
        return
    
    if args.config:
        # Load from config file
        try:
            with open(args.config, 'r') as f:
                module_data = json.load(f)
        except Exception as e:
            print(f"❌ Error loading config file: {e}")
            return
    elif args.interactive:
        # Interactive mode
        module_data = interactive_module_creation()
    else:
        print("❌ Either --config or --interactive is required")
        parser.print_help()
        return
    
    # Create scaffold
    success = create_module_scaffold(args.modules_dir, module_data)
    
    if success:
        print(f"\n🎉 Module '{module_data['slug']}' created successfully!")
        print("\nNext steps:")
        print(f"1. Edit service/{module_data['slug'].replace('-', '_')}.py to implement your logic")
        print("2. Test the module: python service/{}.py --health".format(module_data['slug'].replace('-', '_')))
        print("3. Add to router by restarting the AI Stack system")
        print("4. Test through OpenWebUI with queries related to your module")

if __name__ == "__main__":
    main()