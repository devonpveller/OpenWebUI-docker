"""
GPU Helper Utilities for AI Stack

Provides GPU availability checks and monitoring utilities for the AI stack environment.
"""

import json
import subprocess
import sys
import time
from typing import Dict, Any, List, Optional

# Try to import torch - may not be available in all environments
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

def check_torch_installation() -> Dict[str, Any]:
    """Check PyTorch installation and CUDA support"""
    if not TORCH_AVAILABLE:
        return {
            "torch_available": False,
            "error": "PyTorch not installed or not available",
            "suggestion": "Install PyTorch with CUDA support: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121"
        }
    
    return {
        "torch_available": True,
        "torch_version": torch.__version__,
        "cuda_compiled": hasattr(torch.version, 'cuda') and torch.version.cuda is not None,
        "cuda_version": torch.version.cuda if hasattr(torch.version, 'cuda') else None
    }

def get_nvidia_smi_info() -> Dict[str, Any]:
    """Get NVIDIA GPU information using nvidia-smi"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return {
                "nvidia_smi_available": False,
                "error": result.stderr.strip(),
                "suggestion": "Check NVIDIA drivers installation"
            }
        
        gpus = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = line.split(', ')
                if len(parts) >= 7:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1].strip(),
                        "memory_total_mb": int(parts[2]),
                        "memory_used_mb": int(parts[3]),
                        "memory_free_mb": int(parts[4]),
                        "temperature_c": int(parts[5]) if parts[5].strip() != '[Not Supported]' else None,
                        "utilization_percent": int(parts[6]) if parts[6].strip() != '[Not Supported]' else None
                    })
        
        return {
            "nvidia_smi_available": True,
            "gpu_count": len(gpus),
            "gpus": gpus
        }
    
    except FileNotFoundError:
        return {
            "nvidia_smi_available": False,
            "error": "nvidia-smi command not found",
            "suggestion": "Install NVIDIA drivers or ensure nvidia-smi is in PATH"
        }
    except subprocess.TimeoutExpired:
        return {
            "nvidia_smi_available": False,
            "error": "nvidia-smi command timeout",
            "suggestion": "GPU may be unresponsive or drivers may need restart"
        }
    except Exception as e:
        return {
            "nvidia_smi_available": False,
            "error": str(e)
        }

def check_cuda_availability() -> Dict[str, Any]:
    """Check CUDA availability through PyTorch"""
    torch_info = check_torch_installation()
    
    if not torch_info["torch_available"]:
        return {
            "cuda_available": False,
            "reason": "PyTorch not available",
            "torch_info": torch_info
        }
    
    try:
        cuda_available = torch.cuda.is_available()
        
        result = {
            "cuda_available": cuda_available,
            "torch_info": torch_info
        }
        
        if cuda_available:
            device_count = torch.cuda.device_count()
            current_device = torch.cuda.current_device()
            
            result.update({
                "device_count": device_count,
                "current_device": current_device,
                "devices": []
            })
            
            for i in range(device_count):
                device_props = torch.cuda.get_device_properties(i)
                device_info = {
                    "index": i,
                    "name": torch.cuda.get_device_name(i),
                    "total_memory_gb": round(device_props.total_memory / 1024**3, 2),
                    "compute_capability": f"{device_props.major}.{device_props.minor}",
                    "multi_processor_count": device_props.multi_processor_count
                }
                
                # Get memory usage
                try:
                    torch.cuda.set_device(i)
                    device_info.update({
                        "memory_allocated_gb": round(torch.cuda.memory_allocated(i) / 1024**3, 2),
                        "memory_reserved_gb": round(torch.cuda.memory_reserved(i) / 1024**3, 2)
                    })
                except Exception as e:
                    device_info["memory_error"] = str(e)
                
                result["devices"].append(device_info)
        
        else:
            result["possible_issues"] = [
                "CUDA drivers not installed",
                "PyTorch compiled without CUDA support", 
                "CUDA version mismatch",
                "GPU not detected by system"
            ]
        
        return result
    
    except Exception as e:
        return {
            "cuda_available": False,
            "error": str(e),
            "torch_info": torch_info
        }

def run_gpu_test() -> Dict[str, Any]:
    """Run a simple GPU computation test"""
    if not TORCH_AVAILABLE:
        return {
            "test_result": "skipped",
            "reason": "PyTorch not available"
        }
    
    try:
        if not torch.cuda.is_available():
            return {
                "test_result": "skipped",
                "reason": "CUDA not available"
            }
        
        # Simple tensor operations test
        device = torch.cuda.current_device()
        x = torch.randn(100, 100, device=device)
        y = torch.randn(100, 100, device=device)
        
        start_time = time.time()
        result = torch.mm(x, y)
        end_time = time.time()
        
        # Ensure computation completed
        torch.cuda.synchronize()
        
        return {
            "test_result": "success",
            "device_used": device,
            "device_name": torch.cuda.get_device_name(device),
            "computation_time_ms": round((end_time - start_time) * 1000, 2),
            "result_shape": list(result.shape)
        }
    
    except Exception as e:
        return {
            "test_result": "failed",
            "error": str(e)
        }

def get_comprehensive_gpu_status() -> Dict[str, Any]:
    """Get comprehensive GPU status combining all available information"""
    try:
        import time
        
        status = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "torch_check": check_torch_installation(),
            "cuda_check": check_cuda_availability(),
            "nvidia_smi": get_nvidia_smi_info(),
            "gpu_test": run_gpu_test()
        }
        
        # Determine overall status
        if status["cuda_check"].get("cuda_available") and status["gpu_test"].get("test_result") == "success":
            status["overall_status"] = "✅ GPU Fully Operational"
        elif status["cuda_check"].get("cuda_available"):
            status["overall_status"] = "⚠️ GPU Available (test issues)"
        elif status["nvidia_smi"].get("nvidia_smi_available"):
            status["overall_status"] = "⚠️ GPU Detected (CUDA issues)"
        else:
            status["overall_status"] = "❌ GPU Not Available"
        
        return status
    
    except Exception as e:
        return {
            "overall_status": "❌ GPU Status Check Failed",
            "error": str(e)
        }

def diagnose_gpu_issues() -> Dict[str, Any]:
    """Diagnose common GPU issues and provide solutions"""
    status = get_comprehensive_gpu_status()
    diagnosis = {
        "timestamp": status.get("timestamp"),
        "issues": [],
        "recommendations": []
    }
    
    # Check torch availability
    if not status["torch_check"].get("torch_available"):
        diagnosis["issues"].append("PyTorch not installed")
        diagnosis["recommendations"].append("Install PyTorch with CUDA: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121")
    
    # Check CUDA compilation
    elif not status["torch_check"].get("cuda_compiled"):
        diagnosis["issues"].append("PyTorch compiled without CUDA support")
        diagnosis["recommendations"].append("Reinstall PyTorch with CUDA support")
    
    # Check CUDA availability
    if not status["cuda_check"].get("cuda_available"):
        diagnosis["issues"].append("CUDA not available to PyTorch")
        diagnosis["recommendations"].extend([
            "Check NVIDIA drivers: nvidia-smi",
            "Verify CUDA installation",
            "Restart container with GPU access: docker compose restart openwebui"
        ])
    
    # Check nvidia-smi
    if not status["nvidia_smi"].get("nvidia_smi_available"):
        diagnosis["issues"].append("NVIDIA drivers or nvidia-smi not available")
        diagnosis["recommendations"].append("Install or update NVIDIA drivers")
    
    # Check GPU test
    if status["gpu_test"].get("test_result") == "failed":
        diagnosis["issues"].append("GPU computation test failed")
        diagnosis["recommendations"].append("GPU may be busy or have memory issues - try restarting services")
    
    # Recovery commands
    if diagnosis["issues"]:
        diagnosis["recovery_commands"] = [
            "scripts\\quick-fixes.bat gpu",
            "docker compose restart openwebui",
            "docker compose build --no-cache openwebui"
        ]
    
    return diagnosis