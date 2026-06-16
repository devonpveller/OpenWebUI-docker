#!/usr/bin/env python3
"""
GPU Status Module - Refactored Architecture

Manifest-driven GPU monitoring module implementing the new AI Stack architecture.
Provides comprehensive GPU monitoring with structured contracts.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

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

    # ── nvidia-smi process-level detail ─────────────────────────────────
    # torch.cuda can't see which processes hold VRAM or why utilization is
    # high — only nvidia-smi can. This subset of the module shells out to
    # nvidia-smi to answer "why is GPU util 99%?" and "what is in memory?"

    @staticmethod
    def _parse_gpu_index(text: str) -> Optional[int]:
        """Parse a GPU index from text: 'gpu 0', 'gpu1', 'first gpu', etc.
        Returns None when no scope is given (show all GPUs)."""
        m = re.search(r"\bgpu\s*(\d+)\b", text)
        if m:
            return int(m.group(1))
        if "first" in text:
            return 0
        if "second" in text:
            return 1
        return None

    @staticmethod
    def _int(s, default: int = 0) -> int:
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _float(s) -> Optional[float]:
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    # Map compose env var → (container, description, compose default). Tracks
    # which container claims which GPU index. Drives the "Assigned containers"
    # section of the smi report — useful because NVIDIA's consumer drivers on
    # WSL2 mask per-process names ([Not Found] / [Insufficient Permissions]),
    # so the assignment + live workload below ARE the real answer to "what is
    # in memory?".
    _GPU_ENV_MAP: Dict[str, Dict[str, str]] = {
        "GPU_AISTACK_DEVICE_ID":         {"container": "openwebui",       "description": "Open WebUI + reranker (torch)", "default": "1"},
        "GPU_LLAMA_CPP_DEVICE_ID":       {"container": "llama-cpp-upstream",       "description": "llama-swap CUDA inference",     "default": "0"},
        "GPU_LLAMA_CPP_EMBED_DEVICE_ID": {"container": "llama-cpp-embed-upstream", "description": "BGE-M3 embeddings",             "default": "1"},
    }

    def _get_gpu_assignments(self) -> Dict[int, List[Dict[str, str]]]:
        """Map GPU index → list of containers assigned to it via compose env."""
        out: Dict[int, List[Dict[str, str]]] = {}
        for env_var, meta in self._GPU_ENV_MAP.items():
            device_id = os.environ.get(env_var, meta["default"])
            if not str(device_id).isdigit():
                continue
            out.setdefault(int(device_id), []).append({
                "container": meta["container"],
                "description": meta["description"],
                "env_var": env_var,
            })
        return out

    def _probe_llama_workload(self, base_url: str) -> Optional[Dict[str, Any]]:
        """Probe a llama-swap server for the running model + slot state — the
        most actionable answer to "what is in memory?" when nvidia-smi's
        process list is driver-masked. Returns None when unreachable."""
        import urllib.request
        import urllib.error
        workload: Dict[str, Any] = {}

        # Currently loaded model (llama-swap unloads idle models).
        try:
            with urllib.request.urlopen(f"{base_url}/v1/models", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
                models = data.get("data") or []
                if models and isinstance(models[0], dict):
                    workload["model"] = models[0].get("id")
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, TimeoutError):
            return None

        # Slot occupancy — what is actively processing.
        try:
            with urllib.request.urlopen(f"{base_url}/slots", timeout=3) as r:
                slots = json.loads(r.read().decode("utf-8", errors="replace"))
            if isinstance(slots, list):
                active = [
                    s for s in slots
                    if isinstance(s, dict)
                    and s.get("state", 0) not in (0, "0", False, None)
                ]
                workload["slots_total"] = len(slots)
                workload["slots_active"] = len(active)
                workload["slots_detail"] = [
                    {
                        "id": s.get("id"),
                        "state": s.get("state"),
                        "prompt_tokens": s.get(
                            "n_prompt_tokens", s.get("n_prompt_tokens_processed")
                        ),
                        "decoded": s.get("n_decoded", s.get("n_predict")),
                    }
                    for s in active[:3]
                ]
            else:
                workload["model_status"] = "model_unloaded"
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, TimeoutError):
            # /slots 404s / is unavailable when no model is loaded.
            workload.setdefault("model_status", "model_unloaded")

        return workload

    def _get_gpu_workload(self, containers: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """For each assigned container with a known API, probe what is loaded
        and the slot state."""
        workloads: List[Dict[str, Any]] = []
        for c in containers:
            name = c["container"]
            api: Optional[str] = None
            # The real servers are isolated on llm-backend-net (2026-06-13) and
            # unreachable by DNS from here; this module runs where nvidia-smi
            # lives (the host), so probe their host-published loopback ports.
            # Best-effort: _probe_llama_workload returns None on failure and GPU
            # device info still reports. Never point these at the `llama-cpp`
            # alias -- that is the gateway, not the real inference server.
            if name == "llama-cpp-upstream":
                api = "http://127.0.0.1:8081"
            elif name == "llama-cpp-embed-upstream":
                api = "http://127.0.0.1:8082"
            if not api:
                continue
            workload = self._probe_llama_workload(api)
            if workload is not None:
                workloads.append({"container": name, **workload})
        return workloads

    def _nvidia_smi_detail(self, gpu_index: Optional[int] = None) -> Dict[str, Any]:
        """Run nvidia-smi for per-GPU detail + the compute-process list.

        Returns per-GPU stats (utilization, VRAM, clocks, power, temperature,
        encoder/decoder) joined with the list of compute processes (PID,
        process name, VRAM use) from `--query-compute-apps`. Optionally
        scoped to one GPU via `gpu N` in the input. Dependency-free.
        """
        gpu_query = (
            "index,uuid,name,temperature.gpu,utilization.gpu,utilization.memory,"
            "utilization.encoder,utilization.decoder,memory.used,memory.free,"
            "memory.total,power.draw,power.limit,clocks.current.sm,"
            "clocks.current.memory,compute_mode,pstate,fan.speed"
        )
        cmd_gpu = [
            "nvidia-smi", f"--query-gpu={gpu_query}",
            "--format=csv,noheader,nounits",
        ]
        if gpu_index is not None:
            cmd_gpu.extend(["-i", str(gpu_index)])

        proc_query = "pid,process_name,used_memory,gpu_uuid"
        cmd_proc = [
            "nvidia-smi", f"--query-compute-apps={proc_query}",
            "--format=csv,noheader,nounits",
        ]
        if gpu_index is not None:
            cmd_proc.extend(["-i", str(gpu_index)])

        try:
            gpu_result = subprocess.run(cmd_gpu, capture_output=True, text=True, timeout=8)
            proc_result = subprocess.run(cmd_proc, capture_output=True, text=True, timeout=8)
        except FileNotFoundError:
            return {"available": False, "reason": "nvidia-smi not available in this environment"}
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "nvidia-smi timed out"}
        except Exception as exc:
            return {"available": False, "reason": f"nvidia-smi failed: {exc}"}

        if gpu_result.returncode != 0:
            return {"available": False, "reason": (gpu_result.stderr or "nvidia-smi error").strip()}

        gpus_by_uuid: Dict[str, Dict[str, Any]] = {}
        gpus: List[Dict[str, Any]] = []
        for line in gpu_result.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 16:
                continue
            gpu = {
                "index": self._int(parts[0]),
                "uuid": parts[1],
                "name": parts[2],
                "temp_c": self._int(parts[3]),
                "util_gpu_pct": self._int(parts[4]),
                "util_mem_pct": self._int(parts[5]),
                "util_enc_pct": self._int(parts[6]),
                "util_dec_pct": self._int(parts[7]),
                "vram_used_mib": self._int(parts[8]),
                "vram_free_mib": self._int(parts[9]),
                "vram_total_mib": self._int(parts[10]),
                "power_draw_w": self._float(parts[11]),
                "power_limit_w": self._float(parts[12]),
                "clock_sm_mhz": self._int(parts[13]),
                "clock_mem_mhz": self._int(parts[14]),
                "compute_mode": parts[15] if len(parts) > 15 else "?",
                "pstate": parts[16] if len(parts) > 16 else "?",
                "fan_speed_pct": self._int(parts[17]) if len(parts) > 17 else None,
                "processes": [],
            }
            gpus.append(gpu)
            gpus_by_uuid[gpu["uuid"]] = gpu

        # Join processes to GPUs by uuid. --query-compute-apps may return
        # "[Not Supported]" on consumer drivers — that's an empty list, not
        # an error.
        if proc_result.returncode == 0:
            for line in proc_result.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue
                target = gpus_by_uuid.get(parts[3])
                if target is None:
                    continue
                target["processes"].append({
                    "pid": self._int(parts[0]),
                    "name": parts[1],
                    "vram_mib": self._int(parts[2]),
                })

        # Attach container assignments (from env) and live workload (from
        # llama-swap APIs) so the report answers "what is in memory?" even
        # when the driver masks per-process names ([Not Found] /
        # [Insufficient Permissions] on consumer GeForce + WSL2/Docker).
        assignments = self._get_gpu_assignments()
        for gpu in gpus:
            gpu_containers = assignments.get(gpu["index"], [])
            gpu["assigned_containers"] = gpu_containers
            gpu["workload"] = self._get_gpu_workload(gpu_containers)

        return {"available": True, "gpus": gpus, "scope": gpu_index}

    def _format_smi_detail(self, data: Dict[str, Any]) -> str:
        """Render the nvidia-smi detail report as markdown."""
        if not data.get("available"):
            return f"❌ **nvidia-smi check failed** — {data.get('reason', 'unknown')}"
        gpus = data.get("gpus", [])
        if not gpus:
            return "_No GPUs reported._"

        scope = data.get("scope")
        title = "## 🔬 GPU Detail — `nvidia-smi`"
        if scope is not None:
            title += f" (scoped to GPU {scope})"
        lines = [title, ""]

        for gpu in gpus:
            total = gpu["vram_total_mib"]
            vram_pct = round(100 * gpu["vram_used_mib"] / total, 1) if total else 0
            vram_used_gb = round(gpu["vram_used_mib"] / 1024, 1)
            vram_total_gb = round(total / 1024, 1)

            lines.append(f"### GPU {gpu['index']} — {gpu['name']}")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            lines.append(f"| Utilization | GPU **{gpu['util_gpu_pct']}%** · Memory **{gpu['util_mem_pct']}%** |")
            lines.append(f"| VRAM | {vram_used_gb} / {vram_total_gb} GB ({vram_pct}% used) |")
            lines.append(f"| Temperature | {gpu['temp_c']}°C |")
            if gpu.get("power_draw_w") is not None:
                p = f"{gpu['power_draw_w']:.0f} W"
                if gpu.get("power_limit_w"):
                    p += f" / {gpu['power_limit_w']:.0f} W"
                lines.append(f"| Power | {p} |")
            if gpu.get("clock_sm_mhz"):
                lines.append(
                    f"| Clocks | SM {gpu['clock_sm_mhz']} MHz · Mem {gpu['clock_mem_mhz']} MHz |"
                )
            if gpu.get("compute_mode"):
                lines.append(
                    f"| Compute mode | {gpu['compute_mode']} (pstate {gpu.get('pstate', '?')}) |"
                )
            if gpu.get("util_enc_pct") or gpu.get("util_dec_pct"):
                lines.append(
                    f"| Encoder / Decoder | {gpu['util_enc_pct']}% / {gpu['util_dec_pct']}% |"
                )
            if gpu.get("fan_speed_pct") is not None:
                lines.append(f"| Fan | {gpu['fan_speed_pct']}% |")
            lines.append("")

            # Assigned containers — from compose env vars. This is the
            # "what container is on this GPU" answer regardless of driver
            # process-name restrictions.
            assignments = gpu.get("assigned_containers", [])
            if assignments:
                lines.append("**Assigned containers** (per `GPU_*_DEVICE_ID` env):")
                for c in assignments:
                    lines.append(f"- `{c['container']}` — {c['description']}")
                lines.append("")

            # Live workload — the real answer to "what is loaded?" / "why
            # 99%?" via the container APIs (model + slot state).
            workloads = gpu.get("workload", [])
            for w in workloads:
                container = w.get("container", "?")
                model = w.get("model")
                slots_total = w.get("slots_total")
                slots_active = w.get("slots_active")
                if model and slots_total is not None:
                    state = (
                        f"{slots_active}/{slots_total} slot(s) active — **processing**"
                        if slots_active
                        else f"idle ({slots_total} slot(s))"
                    )
                    lines.append(
                        f"**Workload on `{container}`**: model `{model}` — {state}"
                    )
                    for s in w.get("slots_detail", []):
                        tok = ""
                        if s.get("prompt_tokens") is not None or s.get("decoded") is not None:
                            tok = (
                                f" prompt={s.get('prompt_tokens')}, "
                                f"decoded={s.get('decoded')}"
                            )
                        lines.append(
                            f"  - slot `{s.get('id')}` state=`{s.get('state')}`{tok}"
                        )
                elif model:
                    lines.append(
                        f"**Workload on `{container}`**: model `{model}` loaded "
                        f"(/slots endpoint unavailable)"
                    )
                elif w.get("model_status") == "model_unloaded":
                    lines.append(
                        f"**Workload on `{container}`**: idle — no model loaded "
                        f"(llama-swap unloads idle models)"
                    )
                lines.append("")

            # Compute processes — driver-masked on consumer/WSL2; collapse
            # to a one-liner when every name is "[Not Found]" or similar.
            processes = gpu.get("processes", [])
            masked_tokens = {"[Not Found]", "[Insufficient Permissions]", "[N/A]", ""}
            masked = sum(
                1 for p in processes
                if p.get("name") in masked_tokens or p.get("vram_mib", 0) == 0
            )
            if processes and masked == len(processes):
                lines.append(
                    f"**Processes on GPU {gpu['index']}**: {len(processes)} running — "
                    f"names/VRAM masked by NVIDIA driver "
                    f"(consumer GeForce + WSL2/Docker restriction). "
                    f"See **Workload** above for what is actually loaded."
                )
                lines.append("")
            elif processes:
                lines.append(
                    f"**Processes on GPU {gpu['index']}** "
                    f"({len(processes)} running, sorted by VRAM):"
                )
                lines.append("")
                lines.append("| PID | Process | VRAM |")
                lines.append("|---|---|---|")
                for p in sorted(processes, key=lambda x: -x["vram_mib"]):
                    if p["vram_mib"] >= 512:
                        vram_str = f"{round(p['vram_mib']/1024, 1)} GB"
                    else:
                        vram_str = f"{p['vram_mib']} MiB"
                    lines.append(f"| {p['pid']} | `{p['name']}` | {vram_str} |")
                lines.append("")
            else:
                lines.append(f"_No compute processes reported on GPU {gpu['index']}._")
                lines.append("")

        lines.append(
            "> _Source: `nvidia-smi` (in-container view) + container API probes. "
            "When the per-process VRAM is masked (consumer GeForce on WSL2/Docker), "
            "the **Assigned containers** and **Workload** sections above carry the "
            "actionable answer — which container is on the GPU and what model it "
            "has loaded._"
        )
        return "\n".join(lines)

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
                    "usage_tip": "Ask for 'detailed gpu status', 'gpu diagnostics', or 'smi' for the nvidia-smi process check"
                }
                content = self._format_basic_status(result_data)
            else:
                # nvidia-smi process-level check — answers "why is util 99%?"
                # and "what's in memory?" by shelling to nvidia-smi (torch
                # can't see other processes' VRAM).
                input_lower = user_input.lower()
                smi_triggers = (
                    "smi", "nvidia-smi", "processes", "what's in memory",
                    "what is in memory", "compute apps", "pmon",
                )
                if any(kw in input_lower for kw in smi_triggers):
                    gpu_index = self._parse_gpu_index(input_lower)
                    result_data = self._nvidia_smi_detail(gpu_index)
                    content = self._format_smi_detail(result_data)
                else:
                    # Run comprehensive diagnostics (torch-based)
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