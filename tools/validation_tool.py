#!/usr/bin/env python3
"""
AI Stack Validation and Testing Tools

Comprehensive validation, testing, and verification tools for the refactored architecture.
Implements automated testing, schema validation, and regression testing.
"""

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import argparse

# Optional jsonschema import
try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

def setup_logging() -> logging.Logger:
    """Setup validation logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("validation_tool")

logger = setup_logging()

class SchemaValidator:
    """Schema validation for AI Stack contracts"""
    
    def __init__(self, schema_dir: str = "/host_scripts/schemas"):
        self.schema_dir = Path(schema_dir)
        self.schemas = {}
        self._load_schemas()
    
    def _load_schemas(self):
        """Load all schema files"""
        schema_files = {
            "request_envelope": "request_envelope.schema.json",
            "module_result": "module_result.schema.json", 
            "module_manifest": "module_manifest.schema.json"
        }
        
        for name, filename in schema_files.items():
            schema_path = self.schema_dir / filename
            if schema_path.exists():
                try:
                    with open(schema_path, 'r') as f:
                        self.schemas[name] = json.load(f)
                    logger.info(f"✅ Loaded schema: {name}")
                except Exception as e:
                    logger.error(f"❌ Error loading schema {name}: {e}")
            else:
                logger.warning(f"⚠️ Schema file not found: {schema_path}")
    
    def validate_manifest(self, manifest_file: Path) -> Tuple[bool, List[str]]:
        """Validate module manifest"""
        try:
            with open(manifest_file, 'r') as f:
                manifest_data = json.load(f)
            
            # Basic validation without jsonschema
            if not JSONSCHEMA_AVAILABLE:
                errors = []
                required_fields = ["name", "slug", "version", "entry", "schema", "capabilities"]
                for field in required_fields:
                    if field not in manifest_data:
                        errors.append(f"Missing required field: {field}")
                return len(errors) == 0, errors
            
            jsonschema.validate(manifest_data, self.schemas["module_manifest"])
            return True, []
        
        except jsonschema.ValidationError as e:
            return False, [f"Validation error: {e.message}"]
        except json.JSONDecodeError as e:
            return False, [f"JSON parsing error: {e}"]
        except Exception as e:
            return False, [f"Validation error: {str(e)}"]
    
    def validate_request_envelope(self, request_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate request envelope"""
        try:
            # Basic validation without jsonschema
            if not JSONSCHEMA_AVAILABLE:
                errors = []
                required_fields = ["request_id", "input"]
                for field in required_fields:
                    if field not in request_data:
                        errors.append(f"Missing required field: {field}")
                return len(errors) == 0, errors
            
            jsonschema.validate(request_data, self.schemas["request_envelope"])
            return True, []
        except jsonschema.ValidationError as e:
            return False, [f"Request validation error: {e.message}"]
        except Exception as e:
            return False, [f"Validation error: {str(e)}"]
    
    def validate_module_result(self, result_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate module result"""
        try:
            # Basic validation without jsonschema
            if not JSONSCHEMA_AVAILABLE:
                errors = []
                required_fields = ["request_id", "module_id", "status", "content"]
                for field in required_fields:
                    if field not in result_data:
                        errors.append(f"Missing required field: {field}")
                return len(errors) == 0, errors
            
            jsonschema.validate(result_data, self.schemas["module_result"])
            return True, []
        except jsonschema.ValidationError as e:
            return False, [f"Result validation error: {e.message}"]
        except Exception as e:
            return False, [f"Validation error: {str(e)}"]

class ModuleTester:
    """Individual module testing and validation"""
    
    def __init__(self, modules_dir: str = "/host_scripts/modules"):
        self.modules_dir = Path(modules_dir)
        self.validator = SchemaValidator()
    
    def discover_modules(self) -> List[Path]:
        """Discover all modules with manifests"""
        modules = []
        if not self.modules_dir.exists():
            logger.warning(f"⚠️ Modules directory not found: {self.modules_dir}")
            return modules
        
        for module_dir in self.modules_dir.iterdir():
            if module_dir.is_dir():
                manifest_file = module_dir / "module.manifest.json"
                if manifest_file.exists():
                    modules.append(module_dir)
        
        return modules
    
    def validate_module_structure(self, module_dir: Path) -> Tuple[bool, List[str]]:
        """Validate module directory structure"""
        issues = []
        
        # Check required files
        required_files = [
            "module.manifest.json",
            "service",
            "docs"
        ]
        
        for req_file in required_files:
            if not (module_dir / req_file).exists():
                issues.append(f"Missing required file/directory: {req_file}")
        
        # Check manifest validity
        manifest_file = module_dir / "module.manifest.json"
        if manifest_file.exists():
            manifest_valid, manifest_issues = self.validator.validate_manifest(manifest_file)
            if not manifest_valid:
                issues.extend(manifest_issues)
        
        # Check service directory structure
        service_dir = module_dir / "service"
        if service_dir.exists() and service_dir.is_dir():
            py_files = list(service_dir.glob("*.py"))
            if not py_files:
                issues.append("No Python files found in service directory")
        
        return len(issues) == 0, issues
    
    def test_module_health(self, module_dir: Path) -> Tuple[bool, Dict[str, Any]]:
        """Test module health endpoint"""
        try:
            manifest_file = module_dir / "module.manifest.json"
            with open(manifest_file, 'r') as f:
                manifest = json.load(f)
            
            entry_path = module_dir / manifest["entry"]["path"]
            if not entry_path.exists():
                return False, {"error": f"Entry point not found: {entry_path}"}
            
            # Execute health check
            process = subprocess.run(
                [sys.executable, str(entry_path), "--health"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(entry_path.parent)
            )
            
            if process.returncode != 0:
                return False, {
                    "error": "Health check failed",
                    "exit_code": process.returncode,
                    "stderr": process.stderr,
                    "stdout": process.stdout
                }
            
            # Parse health result
            try:
                health_result = json.loads(process.stdout)
                return True, health_result
            except json.JSONDecodeError:
                return False, {
                    "error": "Health check returned invalid JSON",
                    "stdout": process.stdout
                }
        
        except subprocess.TimeoutExpired:
            return False, {"error": "Health check timed out"}
        except Exception as e:
            return False, {"error": f"Health check error: {str(e)}"}
    
    def test_module_execution(self, module_dir: Path, test_inputs: List[str] = None) -> Dict[str, Any]:
        """Test module execution with various inputs"""
        if test_inputs is None:
            test_inputs = ["status", "help", "test"]
        
        test_results = {
            "module": module_dir.name,
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "results": []
        }
        
        try:
            manifest_file = module_dir / "module.manifest.json"
            with open(manifest_file, 'r') as f:
                manifest = json.load(f)
            
            entry_path = module_dir / manifest["entry"]["path"]
            
            for test_input in test_inputs:
                test_results["tests_run"] += 1
                
                # Create test request
                test_request = {
                    "request_id": str(uuid.uuid4()),
                    "input": test_input,
                    "user": {"id": "test_user"},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                
                try:
                    # Execute module
                    process = subprocess.run(
                        [sys.executable, str(entry_path)],
                        input=json.dumps(test_request),
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=str(entry_path.parent)
                    )
                    
                    test_result = {
                        "input": test_input,
                        "success": process.returncode == 0,
                        "exit_code": process.returncode,
                        "output": process.stdout if process.returncode == 0 else None,
                        "error": process.stderr if process.returncode != 0 else None
                    }
                    
                    if process.returncode == 0:
                        test_results["tests_passed"] += 1
                        
                        # Validate output if JSON
                        try:
                            output_data = json.loads(process.stdout)
                            valid, issues = self.validator.validate_module_result(output_data)
                            test_result["schema_valid"] = valid
                            test_result["schema_issues"] = issues
                        except json.JSONDecodeError:
                            test_result["schema_valid"] = False
                            test_result["schema_issues"] = ["Output is not valid JSON"]
                    else:
                        test_results["tests_failed"] += 1
                    
                    test_results["results"].append(test_result)
                
                except subprocess.TimeoutExpired:
                    test_results["tests_failed"] += 1
                    test_results["results"].append({
                        "input": test_input,
                        "success": False,
                        "error": "Execution timeout"
                    })
                except Exception as e:
                    test_results["tests_failed"] += 1
                    test_results["results"].append({
                        "input": test_input,
                        "success": False,
                        "error": str(e)
                    })
        
        except Exception as e:
            test_results["error"] = str(e)
        
        return test_results

class RouterTester:
    """Router integration and functionality testing"""
    
    def __init__(self, router_path: str = "/host_scripts/core/router.py"):
        self.router_path = Path(router_path)
        self.validator = SchemaValidator()
    
    def test_router_initialization(self) -> Tuple[bool, Dict[str, Any]]:
        """Test router initialization"""
        try:
            if not self.router_path.exists():
                return False, {"error": f"Router not found: {self.router_path}"}
            
            # Test router import and initialization
            process = subprocess.run(
                [sys.executable, str(self.router_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if process.returncode != 0:
                return False, {
                    "error": "Router initialization failed",
                    "exit_code": process.returncode,
                    "stderr": process.stderr
                }
            
            try:
                router_status = json.loads(process.stdout)
                return True, router_status
            except json.JSONDecodeError:
                return False, {
                    "error": "Router returned invalid JSON",
                    "stdout": process.stdout
                }
        
        except Exception as e:
            return False, {"error": f"Router test error: {str(e)}"}
    
    def test_router_routing(self, test_queries: List[str] = None) -> Dict[str, Any]:
        """Test router query routing"""
        if test_queries is None:
            test_queries = [
                "gpu status",
                "system health", 
                "emergency recovery",
                "help",
                "show tools"
            ]
        
        routing_results = {
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "results": []
        }
        
        for query in test_queries:
            routing_results["tests_run"] += 1
            
            try:
                test_payload = {
                    "input": query,
                    "user_id": "test_user",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                
                process = subprocess.run(
                    [sys.executable, str(self.router_path)],
                    input=json.dumps(test_payload),
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if process.returncode == 0:
                    routing_results["tests_passed"] += 1
                    result = {
                        "query": query,
                        "success": True,
                        "response": process.stdout[:200] + "..." if len(process.stdout) > 200 else process.stdout
                    }
                else:
                    routing_results["tests_failed"] += 1
                    result = {
                        "query": query,
                        "success": False,
                        "error": process.stderr
                    }
                
                routing_results["results"].append(result)
            
            except Exception as e:
                routing_results["tests_failed"] += 1
                routing_results["results"].append({
                    "query": query,
                    "success": False,
                    "error": str(e)
                })
        
        return routing_results

class RegressionTester:
    """Regression testing between legacy and refactored systems"""
    
    def __init__(self, legacy_router: str = "/host_scripts/ai_pipes/ai_stack_router.py",
                 refactored_router: str = "/host_scripts/core/router.py"):
        self.legacy_router = Path(legacy_router)
        self.refactored_router = Path(refactored_router)
    
    def compare_responses(self, test_queries: List[str] = None) -> Dict[str, Any]:
        """Compare responses between legacy and refactored systems"""
        if test_queries is None:
            test_queries = [
                "gpu status",
                "system health",
                "help",
                "show available tools"
            ]
        
        comparison_results = {
            "tests_run": 0,
            "compatible_responses": 0,
            "incompatible_responses": 0,
            "legacy_failures": 0,
            "refactored_failures": 0,
            "comparisons": []
        }
        
        for query in test_queries:
            comparison_results["tests_run"] += 1
            comparison = {"query": query}
            
            # Test legacy system
            legacy_result = self._execute_system(self.legacy_router, query)
            comparison["legacy"] = legacy_result
            
            # Test refactored system  
            refactored_result = self._execute_system(self.refactored_router, query)
            comparison["refactored"] = refactored_result
            
            # Compare results
            if legacy_result["success"] and refactored_result["success"]:
                compatibility_score = self._calculate_compatibility(
                    legacy_result["output"], refactored_result["output"]
                )
                comparison["compatibility_score"] = compatibility_score
                
                if compatibility_score > 0.8:
                    comparison_results["compatible_responses"] += 1
                else:
                    comparison_results["incompatible_responses"] += 1
            else:
                if not legacy_result["success"]:
                    comparison_results["legacy_failures"] += 1
                if not refactored_result["success"]:
                    comparison_results["refactored_failures"] += 1
            
            comparison_results["comparisons"].append(comparison)
        
        return comparison_results
    
    def _execute_system(self, router_path: Path, query: str) -> Dict[str, Any]:
        """Execute query against a router system"""
        try:
            test_payload = {
                "input": query,
                "user_id": "regression_test",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            process = subprocess.run(
                [sys.executable, str(router_path)],
                input=json.dumps(test_payload),
                capture_output=True,
                text=True,
                timeout=60
            )
            
            return {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "output": process.stdout if process.returncode == 0 else None,
                "error": process.stderr if process.returncode != 0 else None
            }
        
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _calculate_compatibility(self, legacy_output: str, refactored_output: str) -> float:
        """Calculate compatibility score between outputs"""
        try:
            # Parse as JSON and compare structured content
            legacy_data = json.loads(legacy_output)
            refactored_data = json.loads(refactored_output)
            
            # Compare key fields
            key_fields = ["service", "status", "message", "description"]
            matches = 0
            total = 0
            
            for field in key_fields:
                if field in legacy_data and field in refactored_data:
                    total += 1
                    if legacy_data[field] == refactored_data[field]:
                        matches += 1
            
            if total > 0:
                return matches / total
            else:
                # Fallback to text similarity
                return self._text_similarity(legacy_output, refactored_output)
        
        except json.JSONDecodeError:
            # Fallback to text comparison
            return self._text_similarity(legacy_output, refactored_output)
    
    def _text_similarity(self, text1: str, text2: str) -> float:
        """Calculate basic text similarity"""
        # Simple word overlap metric
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 and not words2:
            return 1.0
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union)

def run_comprehensive_validation(modules_dir: str = "/host_scripts/modules",
                               router_path: str = "/host_scripts/core/router.py") -> Dict[str, Any]:
    """Run comprehensive validation of the entire system"""
    validation_results = {
        "started_at": datetime.now().isoformat(),
        "schema_validation": {"passed": True, "issues": []},
        "module_tests": {"passed": 0, "failed": 0, "results": []},
        "router_tests": {"passed": True, "issues": []},
        "regression_tests": {"compatibility_score": 0.0, "details": {}},
        "overall_status": "unknown"
    }
    
    logger.info("🔍 Starting comprehensive system validation...")
    
    # Test schema validity
    validator = SchemaValidator()
    
    # Test module structure and functionality
    module_tester = ModuleTester(modules_dir)
    modules = module_tester.discover_modules()
    
    logger.info(f"📦 Found {len(modules)} modules to test")
    
    for module_dir in modules:
        logger.info(f"🧪 Testing module: {module_dir.name}")
        
        # Structure validation
        struct_valid, struct_issues = module_tester.validate_module_structure(module_dir)
        
        # Health test
        health_valid, health_result = module_tester.test_module_health(module_dir)
        
        # Execution test
        exec_result = module_tester.test_module_execution(module_dir)
        
        module_test_result = {
            "module": module_dir.name,
            "structure_valid": struct_valid,
            "structure_issues": struct_issues,
            "health_valid": health_valid,
            "health_result": health_result,
            "execution_result": exec_result,
            "overall_passed": struct_valid and health_valid and exec_result["tests_passed"] > 0
        }
        
        validation_results["module_tests"]["results"].append(module_test_result)
        
        if module_test_result["overall_passed"]:
            validation_results["module_tests"]["passed"] += 1
        else:
            validation_results["module_tests"]["failed"] += 1
    
    # Test router functionality
    router_tester = RouterTester(router_path)
    router_init_valid, router_init_result = router_tester.test_router_initialization()
    router_routing_result = router_tester.test_router_routing()
    
    validation_results["router_tests"] = {
        "initialization": {"passed": router_init_valid, "result": router_init_result},
        "routing": router_routing_result,
        "overall_passed": router_init_valid and router_routing_result["tests_passed"] > 0
    }
    
    # Regression testing (if legacy router available)
    regression_tester = RegressionTester()
    if regression_tester.legacy_router.exists():
        logger.info("🔄 Running regression tests...")
        regression_result = regression_tester.compare_responses()
        validation_results["regression_tests"] = regression_result
    
    # Calculate overall status
    module_success_rate = validation_results["module_tests"]["passed"] / max(len(modules), 1)
    router_success = validation_results["router_tests"]["overall_passed"]
    
    if module_success_rate > 0.8 and router_success:
        validation_results["overall_status"] = "excellent"
    elif module_success_rate > 0.6 and router_success:
        validation_results["overall_status"] = "good"
    elif module_success_rate > 0.4 or router_success:
        validation_results["overall_status"] = "needs_improvement"
    else:
        validation_results["overall_status"] = "critical_issues"
    
    validation_results["completed_at"] = datetime.now().isoformat()
    
    logger.info(f"✅ Validation completed - Overall status: {validation_results['overall_status']}")
    
    return validation_results

def main():
    """Main validation tool entry point"""
    parser = argparse.ArgumentParser(description="AI Stack Validation and Testing Tools")
    parser.add_argument("--modules-dir", default="/host_scripts/modules", 
                       help="Modules directory path")
    parser.add_argument("--router-path", default="/host_scripts/core/router.py",
                       help="Router script path")
    parser.add_argument("--schema-dir", default="/host_scripts/schemas",
                       help="Schema directory path")
    parser.add_argument("--module", help="Test specific module only")
    parser.add_argument("--router-only", action="store_true", 
                       help="Test router functionality only")
    parser.add_argument("--regression-only", action="store_true",
                       help="Run regression tests only")
    parser.add_argument("--comprehensive", action="store_true",
                       help="Run comprehensive validation")
    parser.add_argument("--output", help="Output file for results")
    
    args = parser.parse_args()
    
    if args.comprehensive or (not any([args.module, args.router_only, args.regression_only])):
        # Comprehensive validation
        results = run_comprehensive_validation(args.modules_dir, args.router_path)
        
        print("\n📊 Validation Summary:")
        print(f"   Overall Status: {results['overall_status'].upper()}")
        print(f"   Modules Tested: {len(results['module_tests']['results'])}")
        print(f"   Modules Passed: {results['module_tests']['passed']}")
        print(f"   Router Status: {'✅' if results['router_tests']['overall_passed'] else '❌'}")
        
        if results.get('regression_tests'):
            reg = results['regression_tests']
            print(f"   Regression Compatibility: {reg.get('compatible_responses', 0)}/{reg.get('tests_run', 0)}")
    
    elif args.module:
        # Single module test
        module_tester = ModuleTester(args.modules_dir)
        module_path = Path(args.modules_dir) / args.module
        
        if not module_path.exists():
            print(f"❌ Module not found: {module_path}")
            sys.exit(1)
        
        struct_valid, struct_issues = module_tester.validate_module_structure(module_path)
        health_valid, health_result = module_tester.test_module_health(module_path)
        exec_result = module_tester.test_module_execution(module_path)
        
        print(f"\n🧪 Module Test Results: {args.module}")
        print(f"   Structure: {'✅' if struct_valid else '❌'}")
        print(f"   Health: {'✅' if health_valid else '❌'}")
        print(f"   Execution: {exec_result['tests_passed']}/{exec_result['tests_run']} passed")
        
        results = {
            "module": args.module,
            "structure_valid": struct_valid,
            "structure_issues": struct_issues,
            "health_result": health_result,
            "execution_result": exec_result
        }
    
    elif args.router_only:
        # Router-only testing
        router_tester = RouterTester(args.router_path)
        init_valid, init_result = router_tester.test_router_initialization()
        routing_result = router_tester.test_router_routing()
        
        print(f"\n🎯 Router Test Results:")
        print(f"   Initialization: {'✅' if init_valid else '❌'}")
        print(f"   Routing: {routing_result['tests_passed']}/{routing_result['tests_run']} passed")
        
        results = {
            "initialization": {"passed": init_valid, "result": init_result},
            "routing": routing_result
        }
    
    elif args.regression_only:
        # Regression testing only
        regression_tester = RegressionTester()
        regression_result = regression_tester.compare_responses()
        
        print(f"\n🔄 Regression Test Results:")
        print(f"   Compatible Responses: {regression_result['compatible_responses']}")
        print(f"   Incompatible Responses: {regression_result['incompatible_responses']}")
        print(f"   Legacy Failures: {regression_result['legacy_failures']}")
        print(f"   Refactored Failures: {regression_result['refactored_failures']}")
        
        results = regression_result
    
    # Output results if requested
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n💾 Results saved to: {args.output}")

if __name__ == "__main__":
    main()