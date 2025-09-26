"""
GPU Status Pipe for AI Stack

Provides comprehensive GPU monitoring and status checking leveraging
the custom CUDA setup in the AI stack environment.
"""

import json
import os
import sys
import time
from typing import Dict, Any, Optional

# Try to import torch - it should be available in the OpenWebUI container
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

def load_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception:
        return {"gpu": {"cuda_required": True, "check_availability": True, "log_gpu_status": True}}

def check_torch_availability() -> Dict[str, Any]:
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

def get_comprehensive_gpu_status() -> Dict[str, Any]:
    """Get comprehensive GPU status information"""
    if not TORCH_AVAILABLE:
        return check_torch_availability()
    
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

def run_gpu_diagnostics(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run comprehensive GPU diagnostics"""
    user_input = payload.get("input", "").lower()
    
    # Check for specific diagnostic requests
    detailed_check = any(keyword in user_input for keyword in [
        "detailed", "comprehensive", "full", "diagnostic", "memory", "usage"
    ])
    
    gpu_status = get_comprehensive_gpu_status()
    
    result = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
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

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for GPU status pipe"""
    try:
        config = load_config()
        
        if not payload.get("input"):
            # Return basic GPU status
            gpu_status = get_comprehensive_gpu_status()
            return {
                "service": "GPU Status Pipe",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "quick_status": gpu_status.get("status", "Unknown"),
                "cuda_available": gpu_status.get("cuda_available", False),
                "torch_available": TORCH_AVAILABLE,
                "usage_tip": "Ask for 'detailed gpu status' or 'gpu diagnostics' for comprehensive information"
            }
        
        # Run full diagnostics
        return run_gpu_diagnostics(payload)
        
    except Exception as e:
        return {
            "status": "❌ GPU Status Pipe Error",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e),
            "type": "gpu_status_pipe_error",
            "fallback": {
                "command": "scripts\\quick-fixes.bat gpu",
                "description": "Run manual GPU recovery"
            }
        }

if __name__ == "__main__":
    """CLI mode execution"""
    try:
        payload = json.loads(sys.stdin.read())
        result = main(payload)
        print(json.dumps(result, indent=2))
    except Exception as e:
        error_result = {"error": str(e), "type": "CLI execution error"}
        print(json.dumps(error_result, indent=2))
        sys.exit(1)