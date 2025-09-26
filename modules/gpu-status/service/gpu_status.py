#!/usr/bin/env python3
"""
GPU Status Module - Refactored Architecture

Manifest-driven GPU monitoring module implementing the new AI Stack architecture.
Provides comprehensive GPU monitoring with structured contracts.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

# Try to import torch - it should be available in the OpenWebUI container
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

# Try to import psutil for system monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

def setup_logging() -> logging.Logger:
    """Setup module logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger("gpu_status_module")

logger = setup_logging()

class GPUStatusModule:
    """GPU Status Module implementing manifest-driven architecture"""
    
    def __init__(self):
        self.module_id = "gpu-status"
        self.version = "1.0.0-migrated"
    
    def check_torch_availability(self) -> Dict[str, Any]:
        """Check if PyTorch is available and properly configured"""
        if not TORCH_AVAILABLE:
            return {
                "torch_available": False,
                "error": "PyTorch not available in container",
                "suggestion": "Check if container was built with GPU support",
                "recovery_action": "Rebuild container: docker compose build --no-cache openwebui"
            }
        
        return {
            "torch_available": True,
            "torch_version": torch.__version__,
            "cuda_compiled": torch.version.cuda if hasattr(torch.version, 'cuda') else "Unknown"
        }
    
    def get_comprehensive_gpu_status(self) -> Dict[str, Any]:
        """Get comprehensive GPU status information"""
        if not TORCH_AVAILABLE:
            return self.check_torch_availability()
        
        try:
            gpu_info = {
                "cuda_available": torch.cuda.is_available(),
                "torch_info": {
                    "version": torch.__version__,
                    "cuda_version": torch.version.cuda if hasattr(torch.version, 'cuda') else "Unknown"
                }
            }
            
            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                current_device = torch.cuda.current_device()
                
                gpu_info.update({
                    "status": "✅ GPU Available",
                    "device_count": device_count,
                    "current_device": current_device,
                    "devices": []
                })
                
                # Get information for each GPU device
                for i in range(device_count):
                    device_props = torch.cuda.get_device_properties(i)
                    device_info = {
                        "device_id": i,
                        "name": torch.cuda.get_device_name(i),
                        "total_memory_gb": round(device_props.total_memory / 1024**3, 2),
                        "major": device_props.major,
                        "minor": device_props.minor,
                        "multi_processor_count": device_props.multi_processor_count
                    }
                    
                    # Get current memory usage
                    try:
                        memory_allocated = torch.cuda.memory_allocated(i) / 1024**3
                        memory_reserved = torch.cuda.memory_reserved(i) / 1024**3
                        device_info.update({
                            "memory_allocated_gb": round(memory_allocated, 2),
                            "memory_reserved_gb": round(memory_reserved, 2),
                            "memory_free_gb": round(device_info["total_memory_gb"] - memory_reserved, 2)
                        })
                    except Exception as e:
                        device_info["memory_error"] = str(e)
                    
                    gpu_info["devices"].append(device_info)
                
                # Overall memory summary for current device
                try:
                    gpu_info.update({
                        "current_device_memory": {
                            "allocated_gb": round(torch.cuda.memory_allocated() / 1024**3, 2),
                            "reserved_gb": round(torch.cuda.memory_reserved() / 1024**3, 2),
                            "max_reserved_gb": round(torch.cuda.max_memory_reserved() / 1024**3, 2)
                        }
                    })
                except Exception as e:
                    gpu_info["memory_summary_error"] = str(e)
            
            else:
                gpu_info.update({
                    "status": "❌ GPU Not Available",
                    "possible_causes": [
                        "CUDA drivers not properly installed",
                        "Container not started with GPU support",
                        "PyTorch compiled without CUDA support",
                        "NVIDIA Container Toolkit not configured"
                    ],
                    "recovery_suggestions": [
                        "Check host GPU status: nvidia-smi",
                        "Verify container GPU access: docker compose exec openwebui nvidia-smi",
                        "Run GPU recovery: scripts\\quick-fixes.bat gpu",
                        "Rebuild container with GPU support"
                    ]
                })
            
            return gpu_info
            
        except Exception as e:
            return {
                "status": "❌ GPU Status Check Failed",
                "error": str(e),
                "torch_available": TORCH_AVAILABLE,
                "recovery_action": "scripts\\quick-fixes.bat gpu"
            }
    
    def run_gpu_diagnostics(self, user_input: str) -> Dict[str, Any]:
        """Run comprehensive GPU diagnostics"""
        user_input = user_input.lower()
        
        # Check for specific diagnostic requests
        detailed_check = any(keyword in user_input for keyword in [
            "detailed", "comprehensive", "full", "diagnostic", "memory", "usage"
        ])
        
        gpu_status = self.get_comprehensive_gpu_status()
        
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnostic_type": "detailed" if detailed_check else "standard",
            "gpu_status": gpu_status
        }
        
        # Add specific recommendations based on status
        if gpu_status.get("cuda_available"):
            result["recommendations"] = {
                "status": "GPU functioning normally",
                "optimization_tips": [
                    "Monitor memory usage during model loading",
                    "Use torch.cuda.empty_cache() to free unused memory",
                    "Consider using mixed precision for better performance"
                ]
            }
        else:
            result["recommendations"] = {
                "status": "GPU issues detected",
                "immediate_actions": [
                    "Run: scripts\\quick-fixes.bat gpu",
                    "Check: docker compose logs openwebui",
                    "Verify: docker compose exec openwebui nvidia-smi"
                ],
                "escalation_path": [
                    "If basic recovery fails, run: scripts\\emergency-recovery.ps1 -Action gpu-reset",
                    "Consider rebuilding container: docker compose build --no-cache openwebui"
                ]
            }
        
        return result

    def describe(self) -> Dict[str, Any]:
        """Return module metadata"""
        return {
            "module_id": self.module_id,
            "version": self.version,
            "name": "GPU Status Monitor",
            "capabilities": ["system_monitoring", "gpu_access"],
            "status": "ready",
            "torch_available": TORCH_AVAILABLE,
            "psutil_available": PSUTIL_AVAILABLE
        }
    
    def health(self) -> Dict[str, Any]:
        """Module health check"""
        health_score = 100
        issues = []
        
        # Check PyTorch availability
        if not TORCH_AVAILABLE:
            health_score -= 50
            issues.append("PyTorch not available")
        
        # Check GPU access
        if TORCH_AVAILABLE and not torch.cuda.is_available():
            health_score -= 30
            issues.append("CUDA not available")
        
        return {
            "status": "healthy" if health_score > 70 else "degraded" if health_score > 30 else "unhealthy",
            "score": health_score,
            "issues": issues,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute GPU status check with migrated functionality"""
        start_time = time.time()
        request_id = request_data.get("request_id", "unknown")
        
        try:
            # Parse input - handle both string and structured input
            input_data = request_data.get("input", "")
            if isinstance(input_data, dict):
                user_input = input_data.get("query", "")
            else:
                user_input = str(input_data)
            
            # If no specific input, return basic status
            if not user_input.strip():
                gpu_status = self.get_comprehensive_gpu_status()
                result_data = {
                    "service": "GPU Status Module",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "quick_status": gpu_status.get("status", "Unknown"),
                    "cuda_available": gpu_status.get("cuda_available", False),
                    "torch_available": TORCH_AVAILABLE,
                    "usage_tip": "Ask for 'detailed gpu status' or 'gpu diagnostics' for comprehensive information"
                }
                content = self._format_basic_status(result_data)
            else:
                # Run comprehensive diagnostics
                result_data = self.run_gpu_diagnostics(user_input)
                content = self._format_diagnostic_content(result_data)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok",
                "content": content,
                "structured_data": result_data,
                "diagnostics": {
                    "execution_time_ms": execution_time,
                    "torch_available": TORCH_AVAILABLE
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ GPU status execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **GPU Status Error**: {str(e)}",
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": str(e),
                    "retriable": True
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _format_basic_status(self, result_data: Dict[str, Any]) -> str:
        """Format basic status for display"""
        status = result_data.get("quick_status", "Unknown")
        cuda_available = result_data.get("cuda_available", False)
        torch_available = result_data.get("torch_available", False)
        
        return f"""## 🖥️ GPU Status Overview

**Status**: {status}
**CUDA Available**: {"✅" if cuda_available else "❌"}
**PyTorch Available**: {"✅" if torch_available else "❌"}

{result_data.get("usage_tip", "")}

*Use "detailed gpu status" for comprehensive diagnostics*
"""

    def _format_diagnostic_content(self, result_data: Dict[str, Any]) -> str:
        """Format diagnostic results for display"""
        gpu_status = result_data.get("gpu_status", {})
        recommendations = result_data.get("recommendations", {})
        
        content = f"""## 🔍 GPU Diagnostics Report

**Timestamp**: {result_data.get("timestamp", "Unknown")}
**Diagnostic Type**: {result_data.get("diagnostic_type", "standard").title()}

### GPU Status
**Overall Status**: {gpu_status.get("status", "Unknown")}
**CUDA Available**: {"✅" if gpu_status.get("cuda_available") else "❌"}
"""
        
        # Add device information if available
        if "devices" in gpu_status and gpu_status["devices"]:
            content += "\n### 🎮 GPU Devices\n"
            for device in gpu_status["devices"]:
                content += f"""
**Device {device['device_id']}**: {device['name']}
- Memory: {device.get('memory_allocated_gb', 0):.2f}GB / {device.get('total_memory_gb', 0):.2f}GB
- Free: {device.get('memory_free_gb', 0):.2f}GB
- Compute: {device.get('major', 0)}.{device.get('minor', 0)}
"""
        
        # Add memory summary
        if "current_device_memory" in gpu_status:
            mem = gpu_status["current_device_memory"]
            content += f"""
### 💾 Memory Usage (Current Device)
- **Allocated**: {mem.get('allocated_gb', 0):.2f}GB
- **Reserved**: {mem.get('reserved_gb', 0):.2f}GB
- **Max Reserved**: {mem.get('max_reserved_gb', 0):.2f}GB
"""
        
        # Add recommendations
        if recommendations:
            content += f"\n### 💡 Recommendations\n**Status**: {recommendations.get('status', 'Unknown')}\n"
            
            if "optimization_tips" in recommendations:
                content += "\n**Optimization Tips**:\n"
                for tip in recommendations["optimization_tips"]:
                    content += f"- {tip}\n"
            
            if "immediate_actions" in recommendations:
                content += "\n**Immediate Actions**:\n"
                for action in recommendations["immediate_actions"]:
                    content += f"- `{action}`\n"
            
            if "escalation_path" in recommendations:
                content += "\n**Escalation Path**:\n"
                for step in recommendations["escalation_path"]:
                    content += f"- {step}\n"
        
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
    
    def _parse_action(self, input_data: Union[str, Dict[str, Any]]) -> str:
        """Parse action from input"""
        if isinstance(input_data, dict):
            return input_data.get("action", "status")
        
        input_str = str(input_data).lower()
        
        if any(word in input_str for word in ["memory", "mem", "vram"]):
            return "memory"
        elif any(word in input_str for word in ["detailed", "detail", "full"]):
            return "detailed"
        elif any(word in input_str for word in ["diagnostics", "diagnostic", "diag", "debug"]):
            return "diagnostics"
        else:
            return "status"
    
    def _get_basic_status(self) -> Dict[str, Any]:
        """Get basic GPU status"""
        if not TORCH_AVAILABLE:
            return {
                "gpu_available": False,
                "torch_available": False,
                "error": "PyTorch not available in container",
                "suggestion": "Check if container was built with GPU support"
            }
        
        cuda_available = torch.cuda.is_available()
        
        status = {
            "gpu_available": cuda_available,
            "torch_available": True,
            "torch_version": torch.__version__,
            "cuda_compiled": torch.version.cuda if hasattr(torch.version, 'cuda') else "Unknown"
        }
        
        if cuda_available:
            status.update({
                "gpu_count": torch.cuda.device_count(),
                "current_device": torch.cuda.current_device(),
                "device_name": torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else "Unknown"
            })
        
        return status
    
    def _get_detailed_status(self) -> Dict[str, Any]:
        """Get detailed GPU status"""
        basic_status = self._get_basic_status()
        
        if not basic_status["gpu_available"]:
            return basic_status
        
        detailed_info = basic_status.copy()
        
        try:
            # GPU device information
            devices = []
            for i in range(torch.cuda.device_count()):
                device_props = torch.cuda.get_device_properties(i)
                device_info = {
                    "index": i,
                    "name": device_props.name,
                    "compute_capability": f"{device_props.major}.{device_props.minor}",
                    "total_memory_mb": device_props.total_memory // (1024 * 1024),
                    "multiprocessor_count": device_props.multi_processor_count
                }
                devices.append(device_info)
            
            detailed_info["devices"] = devices
            
            # CUDA runtime information
            detailed_info.update({
                "cuda_runtime_version": torch.version.cuda,
                "cudnn_version": torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None,
                "cudnn_enabled": torch.backends.cudnn.enabled
            })
            
        except Exception as e:
            detailed_info["detailed_error"] = str(e)
        
        return detailed_info
    
    def _get_memory_status(self) -> Dict[str, Any]:
        """Get GPU memory status"""
        basic_status = self._get_basic_status()
        
        if not basic_status["gpu_available"]:
            return basic_status
        
        memory_info = basic_status.copy()
        
        try:
            memory_stats = []
            for i in range(torch.cuda.device_count()):
                torch.cuda.set_device(i)
                
                # Get memory info
                memory_allocated = torch.cuda.memory_allocated(i)
                memory_cached = torch.cuda.memory_reserved(i)
                memory_total = torch.cuda.get_device_properties(i).total_memory
                
                memory_stats.append({
                    "device_index": i,
                    "device_name": torch.cuda.get_device_name(i),
                    "memory_allocated_mb": memory_allocated // (1024 * 1024),
                    "memory_cached_mb": memory_cached // (1024 * 1024),
                    "memory_total_mb": memory_total // (1024 * 1024),
                    "memory_free_mb": (memory_total - memory_cached) // (1024 * 1024),
                    "utilization_percent": round((memory_cached / memory_total) * 100, 2)
                })
            
            memory_info["memory_stats"] = memory_stats
            memory_info["memory_summary"] = {
                "total_devices": len(memory_stats),
                "total_memory_mb": sum(stat["memory_total_mb"] for stat in memory_stats),
                "total_allocated_mb": sum(stat["memory_allocated_mb"] for stat in memory_stats),
                "average_utilization": round(sum(stat["utilization_percent"] for stat in memory_stats) / len(memory_stats), 2)
            }
            
        except Exception as e:
            memory_info["memory_error"] = str(e)
        
        return memory_info
    
    def _get_diagnostics(self) -> Dict[str, Any]:
        """Get comprehensive diagnostics"""
        detailed_status = self._get_detailed_status()
        memory_status = self._get_memory_status()
        
        diagnostics = {
            **detailed_status,
            "memory_info": memory_status.get("memory_stats", []),
            "system_info": {}
        }
        
        # Add system information if available
        if PSUTIL_AVAILABLE:
            try:
                diagnostics["system_info"] = {
                    "cpu_count": psutil.cpu_count(),
                    "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
                    "memory_available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
                    "memory_percent": psutil.virtual_memory().percent
                }
            except Exception as e:
                diagnostics["system_info"]["error"] = str(e)
        
        # Add environment diagnostics
        diagnostics["environment"] = {
            "python_version": sys.version,
            "torch_available": TORCH_AVAILABLE,
            "psutil_available": PSUTIL_AVAILABLE,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", "Not set"),
            "nvidia_visible_devices": os.environ.get("NVIDIA_VISIBLE_DEVICES", "Not set")
        }
        
        return diagnostics
    
    def _format_content(self, data: Dict[str, Any], action: str) -> str:
        """Format data as markdown content"""
        if data.get("gpu_available", False) is False:
            return self._format_error_content(data)
        
        if action == "memory":
            return self._format_memory_content(data)
        elif action == "detailed":
            return self._format_detailed_content(data)
        elif action == "diagnostics":
            return self._format_diagnostics_content(data)
        else:
            return self._format_basic_content(data)
    
    def _format_basic_content(self, data: Dict[str, Any]) -> str:
        """Format basic status as markdown"""
        content = ["**🎮 GPU Status**", ""]
        
        gpu_status = "✅ Available" if data.get("gpu_available") else "❌ Not Available"
        content.append(f"**Status**: {gpu_status}")
        
        if data.get("gpu_available"):
            content.extend([
                f"**Device Count**: {data.get('gpu_count', 0)}",
                f"**Current Device**: {data.get('device_name', 'Unknown')}",
                f"**PyTorch**: ✅ {data.get('torch_version', 'Unknown')}",
                f"**CUDA**: {data.get('cuda_compiled', 'Unknown')}"
            ])
        else:
            content.append(f"**PyTorch**: {'✅' if data.get('torch_available') else '❌'}")
            if data.get("suggestion"):
                content.extend(["", f"💡 **Suggestion**: {data['suggestion']}"])
        
        return "\n".join(content)
    
    def _format_detailed_content(self, data: Dict[str, Any]) -> str:
        """Format detailed status as markdown"""
        content = ["**🎮 GPU Detailed Status**", ""]
        
        # Basic info
        content.extend([
            f"**Status**: {'✅ Available' if data.get('gpu_available') else '❌ Not Available'}",
            f"**PyTorch Version**: {data.get('torch_version', 'Unknown')}",
            f"**CUDA Version**: {data.get('cuda_compiled', 'Unknown')}",
            ""
        ])
        
        # Device details
        if data.get("devices"):
            content.append("**GPU Devices:**")
            for device in data["devices"]:
                content.extend([
                    f"• **{device['name']}** (Device {device['index']})",
                    f"  - Compute Capability: {device['compute_capability']}",
                    f"  - Memory: {device['total_memory_mb']} MB",
                    f"  - Multiprocessors: {device['multiprocessor_count']}",
                    ""
                ])
        
        # Runtime info
        if data.get("cuda_runtime_version"):
            content.extend([
                "**Runtime:**",
                f"• CUDA Runtime: {data['cuda_runtime_version']}",
                f"• cuDNN: {'✅' if data.get('cudnn_enabled') else '❌'} ({data.get('cudnn_version', 'Unknown')})",
                ""
            ])
        
        return "\n".join(content)
    
    def _format_memory_content(self, data: Dict[str, Any]) -> str:
        """Format memory status as markdown"""
        content = ["**🎮 GPU Memory Status**", ""]
        
        if data.get("memory_summary"):
            summary = data["memory_summary"]
            content.extend([
                f"**Total Devices**: {summary['total_devices']}",
                f"**Total Memory**: {summary['total_memory_mb']} MB",
                f"**Allocated**: {summary['total_allocated_mb']} MB",
                f"**Average Utilization**: {summary['average_utilization']}%",
                ""
            ])
        
        if data.get("memory_stats"):
            content.append("**Per-Device Memory:**")
            for stat in data["memory_stats"]:
                utilization = stat["utilization_percent"]
                status_icon = "🟢" if utilization < 50 else "🟡" if utilization < 80 else "🔴"
                
                content.extend([
                    f"{status_icon} **{stat['device_name']}** (Device {stat['device_index']})",
                    f"  - Total: {stat['memory_total_mb']} MB",
                    f"  - Allocated: {stat['memory_allocated_mb']} MB",
                    f"  - Cached: {stat['memory_cached_mb']} MB",
                    f"  - Free: {stat['memory_free_mb']} MB",
                    f"  - Utilization: {utilization}%",
                    ""
                ])
        
        return "\n".join(content)
    
    def _format_diagnostics_content(self, data: Dict[str, Any]) -> str:
        """Format diagnostics as markdown"""
        content = ["**🎮 GPU Comprehensive Diagnostics**", ""]
        
        # Basic status
        content.extend([
            f"**GPU Available**: {'✅' if data.get('gpu_available') else '❌'}",
            f"**PyTorch**: {data.get('torch_version', 'Unknown')}",
            f"**CUDA**: {data.get('cuda_compiled', 'Unknown')}",
            ""
        ])
        
        # System info
        if data.get("system_info"):
            sys_info = data["system_info"]
            content.extend([
                "**System Information:**",
                f"• CPU Cores: {sys_info.get('cpu_count', 'Unknown')}",
                f"• RAM: {sys_info.get('memory_available_gb', 0):.1f}/{sys_info.get('memory_total_gb', 0):.1f} GB ({sys_info.get('memory_percent', 0):.1f}%)",
                ""
            ])
        
        # Environment
        if data.get("environment"):
            env = data["environment"]
            content.extend([
                "**Environment:**",
                f"• Python: {env.get('python_version', 'Unknown').split()[0]}",
                f"• CUDA_VISIBLE_DEVICES: {env.get('cuda_visible_devices', 'Not set')}",
                f"• NVIDIA_VISIBLE_DEVICES: {env.get('nvidia_visible_devices', 'Not set')}",
                ""
            ])
        
        # Memory info (abbreviated)
        if data.get("memory_info") and len(data["memory_info"]) > 0:
            content.append("**Memory Summary:**")
            for stat in data["memory_info"][:2]:  # Show first 2 devices
                content.append(f"• {stat['device_name']}: {stat['memory_allocated_mb']}/{stat['memory_total_mb']} MB ({stat['utilization_percent']}%)")
            
            if len(data["memory_info"]) > 2:
                content.append(f"• ... and {len(data['memory_info']) - 2} more devices")
        
        return "\n".join(content)
    
    def _format_error_content(self, data: Dict[str, Any]) -> str:
        """Format error content as markdown"""
        content = ["**🎮 GPU Status - Error**", ""]
        
        if data.get("error"):
            content.append(f"❌ **Error**: {data['error']}")
        
        if data.get("suggestion"):
            content.extend(["", f"💡 **Suggestion**: {data['suggestion']}"])
        
        # Recovery actions
        content.extend([
            "",
            "**Recovery Actions:**",
            "• Check container GPU access: `nvidia-smi`",
            "• Rebuild OpenWebUI container: `docker compose build --no-cache openwebui`",
            "• Verify Docker GPU runtime configuration",
            "• Check NVIDIA drivers on host system"
        ])
        
        return "\n".join(content)

# Module instance
gpu_module = GPUStatusModule()

def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module"""
    return gpu_module.execute(input_data)

def describe() -> Dict[str, Any]:
    """Return module description"""
    return gpu_module.describe()

def health() -> Dict[str, Any]:
    """Return module health status"""
    return gpu_module.health()

def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate input"""
    return gpu_module.validate(input_data)

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