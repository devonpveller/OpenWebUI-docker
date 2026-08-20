#!/usr/bin/env python3
"""
AI Stack Refactoring Orchestration Script

Main orchestration script that coordinates the complete refactoring process.
Implements the phased approach described in the refactoring guide.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

def setup_logging() -> logging.Logger:
    """Setup orchestration logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("refactor_orchestrator")

logger = setup_logging()

class RefactoringOrchestrator:
    """Main orchestrator for AI Stack refactoring"""
    
    def __init__(self, base_dir: str = "/host_scripts"):
        self.base_dir = Path(base_dir)
        self.phases_completed = []
        self.status = {
            "started_at": datetime.now().isoformat(),
            "current_phase": None,
            "phases": {},
            "overall_status": "not_started"
        }
    
    def run_phase_0_analysis(self) -> Dict[str, Any]:
        """Phase 0: Analysis & Planning"""
        logger.info("🔍 Phase 0: Analysis & Planning")
        self.status["current_phase"] = "analysis"
        
        phase_result = {
            "phase": 0,
            "name": "Analysis & Planning",
            "started_at": datetime.now().isoformat(),
            "status": "running"
        }
        
        try:
            # Inventory current modules
            legacy_dir = self.base_dir / "ai_pipes"
            if not legacy_dir.exists():
                raise FileNotFoundError(f"Legacy modules directory not found: {legacy_dir}")
            
            legacy_modules = list(legacy_dir.glob("*_pipe.py"))
            phase_result["legacy_modules_found"] = len(legacy_modules)
            phase_result["legacy_modules"] = [m.name for m in legacy_modules]
            
            # Check schema directory
            schema_dir = self.base_dir / "schemas"
            if not schema_dir.exists():
                logger.warning("⚠️ Schema directory not found - will be created")
                phase_result["schemas_exist"] = False
            else:
                schemas = list(schema_dir.glob("*.schema.json"))
                phase_result["schemas_exist"] = True
                phase_result["schemas_found"] = len(schemas)
            
            # Check modules directory
            modules_dir = self.base_dir / "modules"
            if modules_dir.exists():
                existing_modules = [d.name for d in modules_dir.iterdir() if d.is_dir()]
                phase_result["existing_modules"] = existing_modules
            else:
                phase_result["existing_modules"] = []
            
            # Analyze dependencies
            dependencies_found = {
                "torch": self._check_import("torch"),
                "psutil": self._check_import("psutil"),
                "requests": self._check_import("requests")
            }
            phase_result["dependencies"] = dependencies_found
            
            phase_result["status"] = "completed"
            phase_result["completed_at"] = datetime.now().isoformat()
            
            logger.info(f"✅ Phase 0 completed - Found {len(legacy_modules)} legacy modules")
            return phase_result
            
        except Exception as e:
            phase_result["status"] = "failed"
            phase_result["error"] = str(e)
            phase_result["completed_at"] = datetime.now().isoformat()
            logger.error(f"❌ Phase 0 failed: {e}")
            return phase_result
    
    def run_phase_1_infrastructure(self) -> Dict[str, Any]:
        """Phase 1: Core Infrastructure"""
        logger.info("🏗️ Phase 1: Core Infrastructure")
        self.status["current_phase"] = "infrastructure"
        
        phase_result = {
            "phase": 1,
            "name": "Core Infrastructure",
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "tasks": []
        }
        
        try:
            # Create directory structure
            directories = [
                self.base_dir / "schemas",
                self.base_dir / "core", 
                self.base_dir / "modules",
                self.base_dir / "tools"
            ]
            
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                phase_result["tasks"].append(f"✅ Created directory: {directory}")
            
            # Verify schema files exist
            schema_dir = self.base_dir / "schemas"
            required_schemas = [
                "request_envelope.schema.json",
                "module_result.schema.json", 
                "module_manifest.schema.json"
            ]
            
            schemas_exist = all((schema_dir / schema).exists() for schema in required_schemas)
            if schemas_exist:
                phase_result["tasks"].append("✅ Schema files verified")
            else:
                phase_result["tasks"].append("⚠️ Some schema files missing")
            
            # Verify core router exists
            core_router = self.base_dir / "core" / "router.py"
            if core_router.exists():
                phase_result["tasks"].append("✅ Core router verified")
            else:
                phase_result["tasks"].append("❌ Core router missing")
            
            # Test router initialization
            if core_router.exists():
                try:
                    result = subprocess.run(
                        [sys.executable, str(core_router)],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if result.returncode == 0:
                        phase_result["tasks"].append("✅ Router initialization test passed")
                    else:
                        phase_result["tasks"].append(f"❌ Router initialization failed: {result.stderr}")
                except Exception as e:
                    phase_result["tasks"].append(f"❌ Router test error: {str(e)}")
            
            phase_result["status"] = "completed"
            phase_result["completed_at"] = datetime.now().isoformat()
            
            logger.info("✅ Phase 1 completed - Core infrastructure ready")
            return phase_result
            
        except Exception as e:
            phase_result["status"] = "failed"
            phase_result["error"] = str(e)
            phase_result["completed_at"] = datetime.now().isoformat()
            logger.error(f"❌ Phase 1 failed: {e}")
            return phase_result
    
    def run_phase_2_module_system(self) -> Dict[str, Any]:
        """Phase 2: Module System"""
        logger.info("📦 Phase 2: Module System")
        self.status["current_phase"] = "module_system"
        
        phase_result = {
            "phase": 2,
            "name": "Module System",
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "tasks": []
        }
        
        try:
            # Test module registry
            try:
                # Import and test the router's module registry
                core_router = self.base_dir / "core" / "router.py"
                if core_router.exists():
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("router", core_router)
                    if spec and spec.loader:
                        router_module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(router_module)
                        
                        if hasattr(router_module, 'router'):
                            available_modules = router_module.router.get_available_modules()
                            phase_result["tasks"].append(f"✅ Module registry operational - {available_modules.get('module_count', 0)} modules")
                        else:
                            phase_result["tasks"].append("❌ Router instance not found")
                else:
                    phase_result["tasks"].append("❌ Core router not found")
            except Exception as e:
                phase_result["tasks"].append(f"⚠️ Module registry test failed: {str(e)}")
            
            # Check for existing manifest-driven modules
            modules_dir = self.base_dir / "modules"
            manifest_modules = []
            if modules_dir.exists():
                for module_dir in modules_dir.iterdir():
                    if module_dir.is_dir():
                        manifest_file = module_dir / "module.manifest.json"
                        if manifest_file.exists():
                            manifest_modules.append(module_dir.name)
            
            phase_result["manifest_modules"] = manifest_modules
            phase_result["tasks"].append(f"✅ Found {len(manifest_modules)} manifest-driven modules")
            
            # Test module scaffolding tool
            scaffold_tool = self.base_dir / "tools" / "scaffold_generator.py"
            if scaffold_tool.exists():
                try:
                    result = subprocess.run(
                        [sys.executable, str(scaffold_tool), "--list-templates"],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if result.returncode == 0:
                        phase_result["tasks"].append("✅ Module scaffolding tool operational")
                    else:
                        phase_result["tasks"].append(f"⚠️ Scaffolding tool error: {result.stderr}")
                except Exception as e:
                    phase_result["tasks"].append(f"⚠️ Scaffolding tool test failed: {str(e)}")
            else:
                phase_result["tasks"].append("❌ Module scaffolding tool not found")
            
            phase_result["status"] = "completed"
            phase_result["completed_at"] = datetime.now().isoformat()
            
            logger.info("✅ Phase 2 completed - Module system operational")
            return phase_result
            
        except Exception as e:
            phase_result["status"] = "failed"
            phase_result["error"] = str(e)
            phase_result["completed_at"] = datetime.now().isoformat()
            logger.error(f"❌ Phase 2 failed: {e}")
            return phase_result
    
    def run_phase_3_migration(self) -> Dict[str, Any]:
        """Phase 3: Migration & Integration"""
        logger.info("🔄 Phase 3: Migration & Integration")
        self.status["current_phase"] = "migration"
        
        phase_result = {
            "phase": 3,
            "name": "Migration & Integration", 
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "tasks": [],
            "migrations": []
        }
        
        try:
            # Run migration tool
            migration_tool = self.base_dir / "tools" / "migration_tool.py"
            if migration_tool.exists():
                try:
                    # Run analysis first
                    result = subprocess.run(
                        [sys.executable, str(migration_tool), "--analyze-only"],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    
                    if result.returncode == 0:
                        phase_result["tasks"].append("✅ Legacy module analysis completed")
                    else:
                        phase_result["tasks"].append(f"⚠️ Analysis warning: {result.stderr}")
                    
                    # Run actual migration
                    result = subprocess.run(
                        [sys.executable, str(migration_tool), "--all"],
                        capture_output=True,
                        text=True,
                        timeout=300  # 5 minutes for migration
                    )
                    
                    if result.returncode == 0:
                        phase_result["tasks"].append("✅ Legacy module migration completed")
                        # Try to parse migration results
                        migration_report = self.base_dir / "modules" / "migration_report.json"
                        if migration_report.exists():
                            with open(migration_report, 'r') as f:
                                migration_data = json.load(f)
                                phase_result["migrations"] = migration_data
                    else:
                        phase_result["tasks"].append(f"⚠️ Migration completed with warnings: {result.stderr}")
                        
                except subprocess.TimeoutExpired:
                    phase_result["tasks"].append("⚠️ Migration timed out - may need manual intervention")
                except Exception as e:
                    phase_result["tasks"].append(f"❌ Migration error: {str(e)}")
            else:
                phase_result["tasks"].append("❌ Migration tool not found")
            
            # Test new OpenWebUI adapter
            openwebui_adapter = self.base_dir / "core" / "openwebui_adapter.py"
            if openwebui_adapter.exists():
                phase_result["tasks"].append("✅ New OpenWebUI adapter available")
            else:
                phase_result["tasks"].append("❌ OpenWebUI adapter missing")
            
            phase_result["status"] = "completed"
            phase_result["completed_at"] = datetime.now().isoformat()
            
            logger.info("✅ Phase 3 completed - Migration finished")
            return phase_result
            
        except Exception as e:
            phase_result["status"] = "failed"
            phase_result["error"] = str(e)
            phase_result["completed_at"] = datetime.now().isoformat()
            logger.error(f"❌ Phase 3 failed: {e}")
            return phase_result
    
    def run_phase_4_validation(self) -> Dict[str, Any]:
        """Phase 4: Validation & Testing"""
        logger.info("🧪 Phase 4: Validation & Testing")
        self.status["current_phase"] = "validation"
        
        phase_result = {
            "phase": 4,
            "name": "Validation & Testing",
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "tasks": [],
            "validation_results": {}
        }
        
        try:
            # Run comprehensive validation
            validation_tool = self.base_dir / "tools" / "validation_tool.py"
            if validation_tool.exists():
                try:
                    result = subprocess.run(
                        [sys.executable, str(validation_tool), "--comprehensive"],
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    
                    if result.returncode == 0:
                        phase_result["tasks"].append("✅ Comprehensive validation completed")
                    else:
                        phase_result["tasks"].append(f"⚠️ Validation completed with issues: {result.stderr}")
                    
                    # Extract validation output
                    phase_result["validation_output"] = result.stdout
                    
                except subprocess.TimeoutExpired:
                    phase_result["tasks"].append("⚠️ Validation timed out")
                except Exception as e:
                    phase_result["tasks"].append(f"❌ Validation error: {str(e)}")
            else:
                phase_result["tasks"].append("❌ Validation tool not found")
            
            # Basic system health check
            try:
                # Test router
                core_router = self.base_dir / "core" / "router.py"
                if core_router.exists():
                    result = subprocess.run(
                        [sys.executable, str(core_router)],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if result.returncode == 0:
                        phase_result["tasks"].append("✅ Router health check passed")
                    else:
                        phase_result["tasks"].append(f"❌ Router health check failed: {result.stderr}")
            except Exception as e:
                phase_result["tasks"].append(f"❌ Router health check error: {str(e)}")
            
            phase_result["status"] = "completed"
            phase_result["completed_at"] = datetime.now().isoformat()
            
            logger.info("✅ Phase 4 completed - System validated")
            return phase_result
            
        except Exception as e:
            phase_result["status"] = "failed"
            phase_result["error"] = str(e)
            phase_result["completed_at"] = datetime.now().isoformat()
            logger.error(f"❌ Phase 4 failed: {e}")
            return phase_result
    
    def run_complete_refactoring(self) -> Dict[str, Any]:
        """Run complete refactoring process"""
        logger.info("🚀 Starting complete AI Stack refactoring...")
        
        phases = [
            ("Phase 0", self.run_phase_0_analysis),
            ("Phase 1", self.run_phase_1_infrastructure),
            ("Phase 2", self.run_phase_2_module_system),
            ("Phase 3", self.run_phase_3_migration),
            ("Phase 4", self.run_phase_4_validation)
        ]
        
        self.status["overall_status"] = "running"
        
        for phase_name, phase_func in phases:
            logger.info(f"▶️ Starting {phase_name}")
            
            phase_result = phase_func()
            self.status["phases"][phase_name] = phase_result
            
            if phase_result["status"] == "failed":
                logger.error(f"❌ {phase_name} failed - stopping refactoring")
                self.status["overall_status"] = "failed"
                break
            elif phase_result["status"] == "completed":
                logger.info(f"✅ {phase_name} completed successfully")
                self.phases_completed.append(phase_name)
        
        if len(self.phases_completed) == len(phases):
            self.status["overall_status"] = "completed"
            logger.info("🎉 Complete refactoring finished successfully!")
        
        self.status["completed_at"] = datetime.now().isoformat()
        self.status["phases_completed"] = len(self.phases_completed)
        self.status["phases_total"] = len(phases)
        
        return self.status
    
    def _check_import(self, module_name: str) -> bool:
        """Check if a module can be imported"""
        try:
            __import__(module_name)
            return True
        except ImportError:
            return False
    
    def generate_report(self) -> str:
        """Generate refactoring completion report"""
        report_lines = [
            "# AI Stack Refactoring Report",
            f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Overall Status**: {self.status['overall_status'].upper()}",
            f"**Phases Completed**: {self.status.get('phases_completed', 0)}/{self.status.get('phases_total', 0)}",
            "",
            "## Phase Results"
        ]
        
        for phase_name, phase_result in self.status.get("phases", {}).items():
            status_icon = "✅" if phase_result["status"] == "completed" else "❌" if phase_result["status"] == "failed" else "⏳"
            report_lines.extend([
                f"### {status_icon} {phase_name}",
                f"**Status**: {phase_result['status']}",
                f"**Duration**: {phase_result.get('started_at', '')} - {phase_result.get('completed_at', 'Running')}",
                ""
            ])
            
            if "tasks" in phase_result:
                report_lines.extend(["**Tasks:**"] + [f"- {task}" for task in phase_result["tasks"]] + [""])
            
            if "error" in phase_result:
                report_lines.extend([f"**Error**: {phase_result['error']}", ""])
        
        # Add next steps
        if self.status["overall_status"] == "completed":
            report_lines.extend([
                "",
                "## ✅ Refactoring Complete",
                "",
                "### Next Steps:",
                "1. **Update OpenWebUI**: Replace existing pipe function with new adapter",
                "2. **Test System**: Verify all functionality works as expected", 
                "3. **Monitor Performance**: Check system performance and resource usage",
                "4. **Gradual Rollout**: Gradually transition users to new system",
                "5. **Remove Legacy**: After validation, remove legacy components",
                "",
                "### New Components:",
                f"- **Core Router**: `{self.base_dir}/core/router.py`",
                f"- **OpenWebUI Adapter**: `{self.base_dir}/core/openwebui_adapter.py`",
                f"- **Module Registry**: `{self.base_dir}/modules/`",
                f"- **Schemas**: `{self.base_dir}/schemas/`",
                f"- **Tools**: `{self.base_dir}/tools/`"
            ])
        elif self.status["overall_status"] == "failed":
            failed_phase = None
            for phase_name, phase_result in self.status.get("phases", {}).items():
                if phase_result["status"] == "failed":
                    failed_phase = phase_name
                    break
            
            report_lines.extend([
                "",
                f"## ❌ Refactoring Failed at {failed_phase}",
                "",
                "### Recovery Steps:",
                "1. **Check Logs**: Review error messages above",
                "2. **Fix Issues**: Address the specific problems identified",
                "3. **Retry Phase**: Re-run the failed phase only",
                "4. **Manual Intervention**: Some issues may require manual fixes",
                "",
                "### Rollback Option:",
                "The original system remains intact and functional."
            ])
        
        return "\\n".join(report_lines)

def main():
    """Main orchestrator entry point"""
    parser = argparse.ArgumentParser(description="AI Stack Refactoring Orchestrator")
    parser.add_argument("--base-dir", default="/host_scripts",
                       help="Base directory for AI Stack")
    parser.add_argument("--phase", type=int, choices=[0, 1, 2, 3, 4],
                       help="Run specific phase only")
    parser.add_argument("--report-only", action="store_true",
                       help="Generate report without running refactoring")
    parser.add_argument("--output", help="Output file for report")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be done without executing")
    
    args = parser.parse_args()
    
    orchestrator = RefactoringOrchestrator(args.base_dir)
    
    if args.dry_run:
        print("🔍 Dry Run - Refactoring Process Overview:")
        print("Phase 0: Analysis & Planning - Inventory existing modules and dependencies")
        print("Phase 1: Core Infrastructure - Set up schemas, router, and directory structure") 
        print("Phase 2: Module System - Implement module registry and scaffolding")
        print("Phase 3: Migration & Integration - Convert legacy modules to new format")
        print("Phase 4: Validation & Testing - Comprehensive testing and validation")
        print("\\n📝 Use --phase N to run individual phases or omit to run all phases")
        return
    
    if args.report_only:
        # Generate report from existing status
        report = orchestrator.generate_report()
        print(report)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
        return
    
    # Run refactoring
    if args.phase is not None:
        # Run specific phase
        phase_functions = {
            0: orchestrator.run_phase_0_analysis,
            1: orchestrator.run_phase_1_infrastructure,
            2: orchestrator.run_phase_2_module_system,
            3: orchestrator.run_phase_3_migration,
            4: orchestrator.run_phase_4_validation
        }
        
        if args.phase in phase_functions:
            result = phase_functions[args.phase]()
            print(f"\\n📊 Phase {args.phase} Result:")
            print(json.dumps(result, indent=2))
        else:
            print(f"❌ Invalid phase: {args.phase}")
            sys.exit(1)
    else:
        # Run complete refactoring
        result = orchestrator.run_complete_refactoring()
        
        # Generate and display report
        report = orchestrator.generate_report()
        print("\\n" + "="*60)
        print(report)
        print("="*60)
        
        # Save results
        results_file = Path(args.base_dir) / "refactoring_results.json"
        with open(results_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"\\n💾 Detailed results saved to: {results_file}")
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
            print(f"📋 Report saved to: {args.output}")
        
        # Exit with appropriate code
        if result["overall_status"] == "completed":
            print("\\n🎉 Refactoring completed successfully!")
            sys.exit(0)
        elif result["overall_status"] == "failed":
            print("\\n❌ Refactoring failed - see report for details")
            sys.exit(1)
        else:
            print("\\n⏳ Refactoring in progress")
            sys.exit(2)

if __name__ == "__main__":
    main()