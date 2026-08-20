#!/usr/bin/env python3
"""
AI Stack Module Migration Tool

Automates migration from legacy pipe modules to the new manifest-driven architecture.
Implements the migration automation described in the refactoring guide.
"""

import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

def setup_logging() -> logging.Logger:
    """Setup migration logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("migration_tool")

logger = setup_logging()

class LegacyModuleMigrator:
    """Migrates legacy pipe modules to new manifest-driven architecture"""
    
    def __init__(self, legacy_dir: str = "/host_scripts/ai_pipes", 
                 modules_dir: str = "/host_scripts/modules"):
        self.legacy_dir = Path(legacy_dir)
        self.modules_dir = Path(modules_dir)
        self.modules_dir.mkdir(parents=True, exist_ok=True)
        
        # Legacy module patterns
        self.legacy_modules = {
            "gpu_status_pipe.py": {
                "slug": "gpu-status",
                "name": "GPU Status Monitor", 
                "capabilities": ["system_monitoring", "gpu_access"],
                "description": "GPU monitoring and CUDA diagnostics",
                "gpu_required": True
            },
            "emergency_recovery_pipe.py": {
                "slug": "emergency-recovery",
                "name": "Emergency Recovery System",
                "capabilities": ["admin_operations", "system_monitoring"],
                "description": "System recovery and troubleshooting automation",
                "safety_rating": "RESTRICTED"
            },
            "system_health_pipe.py": {
                "slug": "system-health", 
                "name": "System Health Monitor",
                "capabilities": ["system_monitoring"],
                "description": "Comprehensive system health monitoring"
            },
            "custom_tools_pipe.py": {
                "slug": "custom-tools",
                "name": "Custom Tools Discovery",
                "capabilities": ["system_monitoring"],
                "description": "Tool discovery and automation management"
            },
            "help_pipe.py": {
                "slug": "help-system",
                "name": "Help System",
                "capabilities": ["system_monitoring"],
                "description": "AI Stack help and guidance system"
            }
        }
    
    def analyze_legacy_module(self, module_file: Path) -> Dict[str, Any]:
        """Analyze legacy module to extract metadata and dependencies"""
        analysis = {
            "file": str(module_file),
            "functions": [],
            "imports": [],
            "dependencies": [],
            "entry_point": "main",
            "docstring": "",
            "complexity_score": 0
        }
        
        try:
            with open(module_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract docstring
            docstring_match = re.search(r'"""([^"]*?)"""', content, re.DOTALL)
            if docstring_match:
                analysis["docstring"] = docstring_match.group(1).strip()
            
            # Extract imports
            import_matches = re.findall(r'^(?:from .+ )?import (.+)$', content, re.MULTILINE)
            for match in import_matches:
                imports = [imp.strip() for imp in match.split(',')]
                analysis["imports"].extend(imports)
            
            # Extract function definitions
            function_matches = re.findall(r'^def ([a-zA-Z_][a-zA-Z0-9_]*)\(.*?\):', content, re.MULTILINE)
            analysis["functions"] = function_matches
            
            # Determine entry point
            if "def main(" in content:
                analysis["entry_point"] = "main"
            elif "def process(" in content:
                analysis["entry_point"] = "process"
            else:
                # Use first function as entry point
                if analysis["functions"]:
                    analysis["entry_point"] = analysis["functions"][0]
            
            # Estimate complexity (rough metric)
            analysis["complexity_score"] = (
                content.count("def ") * 2 +
                content.count("class ") * 5 +
                content.count("try:") * 3 +
                content.count("import ") +
                len(content.split("\\n")) // 10
            )
            
            # Extract dependencies
            known_deps = {
                "torch": "torch",
                "psutil": "psutil", 
                "requests": "requests",
                "subprocess": None,  # Built-in
                "json": None,       # Built-in
                "os": None,         # Built-in
                "sys": None,        # Built-in
                "logging": None     # Built-in
            }
            
            for imp in analysis["imports"]:
                base_import = imp.split('.')[0].split(' as ')[0].strip()
                if base_import in known_deps and known_deps[base_import]:
                    analysis["dependencies"].append(known_deps[base_import])
            
            # Remove duplicates
            analysis["dependencies"] = list(set(analysis["dependencies"]))
            
        except Exception as e:
            logger.error(f"❌ Error analyzing {module_file}: {e}")
            analysis["error"] = str(e)
        
        return analysis
    
    def create_manifest_from_legacy(self, module_file: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Create manifest from legacy module analysis"""
        
        # Get base configuration
        base_config = self.legacy_modules.get(module_file, {})
        
        manifest = {
            "name": base_config.get("name", f"Legacy {module_file}"),
            "slug": base_config.get("slug", module_file.replace("_pipe.py", "").replace("_", "-")),
            "version": "1.0.0-migrated",
            "description": base_config.get("description", analysis.get("docstring", "Migrated legacy module")),
            "author": "AI Stack Migration Tool",
            "license": "MIT",
            "entry": {
                "kind": "cli",
                "path": f"service/{base_config.get('slug', 'legacy').replace('-', '_')}.py",
                "function": analysis.get("entry_point", "main")
            },
            "schema": {
                "input": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string", "format": "uuid"},
                        "input": {
                            "oneOf": [
                                {"type": "string", "description": "Text query"},
                                {"type": "object", "description": "Structured parameters"}
                            ]
                        },
                        "user": {"type": "object", "properties": {"id": {"type": "string"}}},
                        "timestamp": {"type": "string", "format": "date-time"}
                    },
                    "required": ["request_id", "input"]
                },
                "output": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "module_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["ok", "error"]},
                        "content": {"type": "string"},
                        "structured_data": {"type": "object"}
                    }
                }
            },
            "capabilities": base_config.get("capabilities", ["system_monitoring"]),
            "limits": {
                "timeout_ms": min(60000, max(15000, analysis.get("complexity_score", 0) * 1000)),
                "memory_mb": 256 if analysis.get("complexity_score", 0) < 20 else 512,
                "cpu_cores": 0.5 if analysis.get("complexity_score", 0) < 30 else 1.0,
                "input_size_mb": 5,
                "concurrent_requests": 3
            },
            "environment": {
                "kind": "venv",
                "python_version": ">=3.8",
                "dependencies": analysis.get("dependencies", []),
                "gpu_required": base_config.get("gpu_required", False)
            },
            "health": {
                "probe_interval_ms": 30000,
                "startup_timeout_ms": 60000
            },
            "help": {
                "short": base_config.get("description", "Migrated legacy functionality"),
                "long": f"Migrated from {module_file}. {analysis.get('docstring', '')}".strip(),
                "examples": [
                    {"input": "status", "description": "Get module status"},
                    {"input": "help", "description": "Get help information"}
                ]
            },
            "permissions": {
                "allowed_roles": ["user", "admin"],
                "safety_rating": base_config.get("safety_rating", "SAFE")
            },
            "compatibility": {
                "router_version": ">=1.0.0",
                "openwebui_version": ">=0.3.0"
            },
            "_migration": {
                "source_file": module_file,
                "migrated_at": datetime.now().isoformat(),
                "complexity_score": analysis.get("complexity_score", 0),
                "original_functions": analysis.get("functions", [])
            }
        }
        
        return manifest
    
    def create_wrapper_service(self, module_file: str, analysis: Dict[str, Any], 
                             manifest: Dict[str, Any]) -> str:
        """Create wrapper service that adapts legacy module to new architecture"""
        
        slug = manifest["slug"]
        name = manifest["name"]
        original_file = module_file
        entry_point = analysis.get("entry_point", "main")
        
        return f'''#!/usr/bin/env python3
"""
{name} - Migrated Legacy Module

Auto-generated wrapper for legacy module: {original_file}
Migrated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Original complexity score: {analysis.get("complexity_score", 0)}
Original functions: {', '.join(analysis.get("functions", []))}
"""

import json
import logging
import os
import sys
import time
import importlib.util
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from pathlib import Path

# Add legacy module path
legacy_path = "/host_scripts/ai_pipes"
if legacy_path not in sys.path:
    sys.path.append(legacy_path)

def setup_logging() -> logging.Logger:
    """Setup module logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("{slug.replace('-', '_')}_migrated")

logger = setup_logging()

class {slug.replace('-', '_').title().replace('_', '')}MigratedModule:
    """
    Migrated wrapper for {original_file}
    
    Provides backward compatibility while implementing new architecture contracts.
    """
    
    def __init__(self):
        self.module_id = "{slug}"
        self.version = "{manifest['version']}"
        self.legacy_module = None
        self._load_legacy_module()
    
    def _load_legacy_module(self):
        """Load the original legacy module"""
        try:
            legacy_file = Path("{legacy_path}/{original_file}")
            if not legacy_file.exists():
                raise FileNotFoundError(f"Legacy module not found: {{legacy_file}}")
            
            spec = importlib.util.spec_from_file_location("legacy_module", legacy_file)
            if not spec or not spec.loader:
                raise ImportError(f"Cannot load legacy module: {{legacy_file}}")
            
            self.legacy_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.legacy_module)
            
            if not hasattr(self.legacy_module, "{entry_point}"):
                raise AttributeError(f"Entry point '{entry_point}' not found in legacy module")
            
            logger.info(f"✅ Loaded legacy module: {original_file}")
            
        except Exception as e:
            logger.error(f"❌ Failed to load legacy module: {{e}}")
            raise
    
    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {{
            "module_id": self.module_id,
            "version": self.version,
            "name": "{name}",
            "capabilities": {manifest['capabilities']},
            "status": "ready" if self.legacy_module else "error",
            "migration_info": {{
                "source": "{original_file}",
                "entry_point": "{entry_point}",
                "complexity_score": {analysis.get("complexity_score", 0)}
            }}
        }}
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        if not self.legacy_module:
            return {{
                "status": "unhealthy",
                "score": 0,
                "issues": ["Legacy module not loaded"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}
        
        # Basic health check
        health_score = 90  # Start with good score for loaded module
        issues = []
        
        # Check if entry point is callable
        if not callable(getattr(self.legacy_module, "{entry_point}", None)):
            health_score -= 50
            issues.append("Entry point not callable")
        
        return {{
            "status": "healthy" if health_score > 70 else "degraded" if health_score > 30 else "unhealthy",
            "score": health_score,
            "issues": issues,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }}
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute legacy module with new architecture contracts"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        if not self.legacy_module:
            return {{
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": "❌ **Legacy Module Not Loaded**",
                "error": {{
                    "code": "MODULE_NOT_LOADED",
                    "message": "Legacy module could not be loaded",
                    "retriable": False
                }},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}
        
        try:
            # Prepare legacy payload
            input_data = request_data.get("input", "")
            legacy_payload = {{
                "input": str(input_data),
                "user_id": request_data.get("user", {{}}).get("id", "unknown"),
                "timestamp": request_data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "messages": []  # Default empty for compatibility
            }}
            
            # Execute legacy function
            legacy_function = getattr(self.legacy_module, "{entry_point}")
            legacy_result = legacy_function(legacy_payload)
            
            # Convert legacy result to new format
            content = self._format_legacy_result(legacy_result)
            structured_data = legacy_result if isinstance(legacy_result, dict) else None
            execution_time = int((time.time() - start_time) * 1000)
            
            return {{
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok",
                "content": content,
                "structured_data": structured_data,
                "diagnostics": {{
                    "execution_time_ms": execution_time,
                    "legacy_wrapper": True,
                    "source_module": "{original_file}"
                }},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}
            
        except Exception as e:
            logger.error(f"❌ Legacy execution error: {{e}}")
            return {{
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **Legacy Module Error**: {{str(e)}}",
                "error": {{
                    "code": "LEGACY_EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                }},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}
    
    def validate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate input"""
        required_fields = ["request_id", "input"]
        missing_fields = [field for field in required_fields if field not in input_data]
        
        if missing_fields:
            return {{
                "valid": False,
                "errors": [f"Missing required field: {{field}}" for field in missing_fields]
            }}
        
        return {{"valid": True, "errors": []}}
    
    def _format_legacy_result(self, result: Any) -> str:
        """Format legacy result for new architecture"""
        if isinstance(result, dict):
            # Handle error responses
            if result.get("status") == "error":
                return f"❌ **Error**: {{result.get('message', 'Unknown error')}}"
            
            # Format service responses
            if "service" in result:
                content_parts = [f"**{{result['service']}}**", ""]
                
                if "message" in result:
                    content_parts.append(result["message"])
                
                if "description" in result:
                    content_parts.append(result["description"])
                
                # Add specific formatting for known result types
                if "health_summary" in result:
                    health = result["health_summary"]
                    content_parts.extend([
                        "",
                        f"**Status**: {{health.get('status', 'Unknown')}}",
                        f"**Score**: {{health.get('health_score', 0)}}/100"
                    ])
                
                if "gpu_available" in result:
                    gpu_status = "✅ Available" if result["gpu_available"] else "❌ Not Available"
                    content_parts.extend(["", f"**GPU**: {{gpu_status}}"])
                
                if result.get("timestamp"):
                    content_parts.extend(["", f"*Updated: {{result['timestamp']}}*"])
                
                return "\\n".join(content_parts)
            
            # Generic dict formatting
            return f"```json\\n{{json.dumps(result, indent=2)}}\\n```"
        
        # String result
        return str(result)

# Module instance
{slug.replace('-', '_')}_module = {slug.replace('-', '_').title().replace('_', '')}MigratedModule()

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
            # Process input
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
    
    def migrate_module(self, module_file: str) -> Tuple[bool, Dict[str, Any]]:
        """Migrate a single legacy module"""
        legacy_file = self.legacy_dir / module_file
        
        if not legacy_file.exists():
            return False, {"error": f"Legacy module not found: {legacy_file}"}
        
        logger.info(f"🔄 Migrating {module_file}...")
        
        try:
            # Analyze legacy module
            analysis = self.analyze_legacy_module(legacy_file)
            if "error" in analysis:
                return False, {"error": f"Analysis failed: {analysis['error']}"}
            
            # Create manifest
            manifest = self.create_manifest_from_legacy(module_file, analysis)
            
            # Create module directory
            module_slug = manifest["slug"]
            module_path = self.modules_dir / module_slug
            module_path.mkdir(parents=True, exist_ok=True)
            (module_path / "service").mkdir(exist_ok=True)
            (module_path / "docs").mkdir(exist_ok=True)
            (module_path / "artifacts").mkdir(exist_ok=True)
            
            # Write manifest
            with open(module_path / "module.manifest.json", 'w') as f:
                json.dump(manifest, f, indent=2)
            
            # Create wrapper service
            service_code = self.create_wrapper_service(module_file, analysis, manifest)
            service_file = module_path / "service" / f"{module_slug.replace('-', '_')}.py"
            with open(service_file, 'w') as f:
                f.write(service_code)
            service_file.chmod(0o755)
            
            # Create migration README
            readme_content = f"""# {manifest['name']} - Migrated Module

**Migrated from**: `{module_file}`  
**Migration Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Complexity Score**: {analysis.get('complexity_score', 0)}

## Migration Status

✅ **Successfully Migrated**

- Legacy module: `{legacy_file}`
- New module ID: `{module_slug}`
- Entry point: `{analysis.get('entry_point', 'main')}`
- Capabilities: {', '.join(manifest['capabilities'])}

## Original Functions

{chr(10).join(f"- {func}()" for func in analysis.get('functions', []))}

## Dependencies

{chr(10).join(f"- {dep}" for dep in analysis.get('dependencies', ['None']))}

## Usage

The module maintains backward compatibility while implementing new architecture contracts:

```bash
# Health check
python service/{module_slug.replace('-', '_')}.py --health

# Test execution
echo '{{"request_id": "test", "input": "status"}}' | python service/{module_slug.replace('-', '_')}.py
```

## Next Steps

1. **Test Migration**: Verify the migrated module works correctly
2. **Performance Check**: Compare performance with legacy version
3. **Update Router**: Restart router to pick up new module
4. **Gradual Rollout**: Test thoroughly before removing legacy module

## Rollback Plan

If issues occur, the original module is still available at `{legacy_file}`.
To rollback, simply remove this module directory and restart the system.
"""
            
            with open(module_path / "docs" / "README.md", 'w') as f:
                f.write(readme_content)
            
            migration_report = {
                "success": True,
                "module_slug": module_slug,
                "module_path": str(module_path),
                "manifest": manifest,
                "analysis": analysis,
                "files_created": [
                    "module.manifest.json",
                    f"service/{module_slug.replace('-', '_')}.py",
                    "docs/README.md"
                ]
            }
            
            logger.info(f"✅ Successfully migrated {module_file} -> {module_slug}")
            return True, migration_report
            
        except Exception as e:
            logger.error(f"❌ Migration failed for {module_file}: {e}")
            return False, {"error": str(e)}
    
    def migrate_all_modules(self) -> Dict[str, Any]:
        """Migrate all legacy modules"""
        migration_results = {
            "started_at": datetime.now().isoformat(),
            "modules_processed": 0,
            "successful_migrations": 0,
            "failed_migrations": 0,
            "results": {},
            "summary": []
        }
        
        logger.info("🚀 Starting batch migration of legacy modules...")
        
        for module_file in self.legacy_modules.keys():
            migration_results["modules_processed"] += 1
            
            success, result = self.migrate_module(module_file)
            migration_results["results"][module_file] = result
            
            if success:
                migration_results["successful_migrations"] += 1
                migration_results["summary"].append(f"✅ {module_file} -> {result['module_slug']}")
            else:
                migration_results["failed_migrations"] += 1
                migration_results["summary"].append(f"❌ {module_file}: {result.get('error', 'Unknown error')}")
        
        migration_results["completed_at"] = datetime.now().isoformat()
        
        # Generate migration report
        report_file = self.modules_dir / "migration_report.json"
        with open(report_file, 'w') as f:
            json.dump(migration_results, f, indent=2)
        
        logger.info(f"📊 Migration completed: {migration_results['successful_migrations']}/{migration_results['modules_processed']} successful")
        
        return migration_results

def main():
    """Main migration tool"""
    import argparse
    
    parser = argparse.ArgumentParser(description="AI Stack Module Migration Tool")
    parser.add_argument("--legacy-dir", default="/host_scripts/ai_pipes",
                       help="Legacy modules directory")
    parser.add_argument("--modules-dir", default="/host_scripts/modules", 
                       help="Target modules directory")
    parser.add_argument("--module", help="Migrate specific module file")
    parser.add_argument("--all", action="store_true", help="Migrate all modules")
    parser.add_argument("--analyze-only", action="store_true", 
                       help="Analyze modules without migration")
    parser.add_argument("--report", help="Generate migration report")
    
    args = parser.parse_args()
    
    migrator = LegacyModuleMigrator(args.legacy_dir, args.modules_dir)
    
    if args.analyze_only:
        # Analysis mode
        print("🔍 Analyzing legacy modules...")
        for module_file in migrator.legacy_modules.keys():
            legacy_file = migrator.legacy_dir / module_file
            if legacy_file.exists():
                analysis = migrator.analyze_legacy_module(legacy_file)
                print(f"\\n📄 {module_file}:")
                print(f"   Functions: {', '.join(analysis.get('functions', []))}")
                print(f"   Dependencies: {', '.join(analysis.get('dependencies', []))}")
                print(f"   Complexity: {analysis.get('complexity_score', 0)}")
        return
    
    if args.module:
        # Single module migration
        success, result = migrator.migrate_module(args.module)
        if success:
            print(f"✅ Successfully migrated {args.module}")
            print(json.dumps(result, indent=2))
        else:
            print(f"❌ Migration failed: {result.get('error')}")
            sys.exit(1)
    
    elif args.all:
        # Batch migration
        results = migrator.migrate_all_modules()
        
        print("\\n📊 Migration Summary:")
        for summary_line in results["summary"]:
            print(f"   {summary_line}")
        
        print(f"\\n📈 Results: {results['successful_migrations']}/{results['modules_processed']} successful")
        
        if results["failed_migrations"] > 0:
            print("\\n❌ Failed migrations require manual attention")
            sys.exit(1)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()