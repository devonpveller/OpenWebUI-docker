#!/usr/bin/env python3
"""
Status Check - Python equivalent of quick-fixes.bat status

Comprehensive system status check with detailed diagnostics.
"""

import json
import subprocess
import sys
from pathlib import Path

def log_info(message):
    """Log info message"""
    print(f"[INFO] {message}")

def log_success(message):
    """Log success message"""
    print(f"[SUCCESS] {message}")

def log_error(message):
    """Log error message"""
    print(f"[ERROR] {message}")

def log_warn(message):
    """Log warning message"""
    print(f"[WARN] {message}")

def find_project_root():
    """Find the project root directory containing docker-compose.yml"""
    current_dir = Path.cwd()
    
    # Check if we're running from container (look for /host_project)
    if Path("/host_project").exists():
        log_info("Running from container environment...")
        return Path("/host_project")
    
    # Otherwise search for docker-compose.yml
    project_root = current_dir
    while not (project_root / "docker-compose.yml").exists():
        parent = project_root.parent
        if parent == project_root:  # Reached root
            break
        project_root = parent
    
    if not (project_root / "docker-compose.yml").exists():
        log_error("docker-compose.yml not found in current directory or parent directories")
        return None
    
    return project_root

def run_docker_command(command, cwd, timeout=30):
    """Run docker command with error handling"""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result
    except subprocess.TimeoutExpired:
        log_error(f"Command timed out after {timeout} seconds: {' '.join(command)}")
        return None
    except Exception as e:
        log_error(f"Command failed: {e}")
        return None

def check_container_status():
    """Check container status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Container Status (all projects, docker ps):")
    result = run_docker_command(
        ["docker", "ps", "--format", "table {{.Names}}	{{.Status}}"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
        return True
    else:
        log_error("Failed to get container status")
        return False

def start_missing_services():
    """REPORT missing services (starting them belongs to stack.ps1 / the
    recovery scripts - this checker stays read-only). Was a leftover
    `docker compose up -d watchtower` until 2026-08-22; watchtower itself
    was retired 2026-08-20."""
    print()
    log_info("Missing-service report (start with: scripts\stack\stack.ps1 up <plane>):")
    inv = load_inventory()
    if not inv:
        return False
    missing = []
    for services in inv.get("planes", {}).values():
        for svc in services:
            if _container_state(svc["container"]) != "running":
                missing.append(f"{svc['container']} ({svc.get('project', '?')})")
    if missing:
        log_warn("  not running: " + ", ".join(missing))
    else:
        log_success("  every inventoried container is running")
    return True

def check_gpu_status():
    """Check OpenWebUI GPU status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("OpenWebUI GPU Status:")
    result = run_docker_command(
        ["docker", "exec", "openwebui", "python", "-c", 
         "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count())"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
        return True
    else:
        log_error("GPU status check failed")
        return False

def check_llm_gateway_status():
    """Check the LiteLLM gateway — the front door every caller reaches inference
    through. Probed from inside the `tailscale` container (also on llm-net), since
    llm-net is internal-only (no host port). The wolfi-based litellm image has no
    curl, but we probe over the network with wget here, not inside the container."""
    project_root = find_project_root()
    if not project_root:
        return False

    print()
    log_info("llm-gateway (LiteLLM front door) Status:")
    result = run_docker_command(
        ["docker", "exec", "tailscale", "wget", "-q", "-T", "5", "-O", "-", "http://llm-gateway:8080/health/liveliness"],
        project_root,
        timeout=15
    )

    if result and result.returncode == 0:
        log_success("llm-gateway liveliness: OK")
        print(result.stdout.strip())
        return True
    else:
        log_error("llm-gateway liveliness: FAILED (callers cannot reach inference — check llama-cpp-upstream first)")
        return False


def check_llama_cpp_status():
    """Check llama-cpp server status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("llama-cpp Status:")
    result = run_docker_command(
        ["docker", "exec", "llama-cpp-upstream", "curl", "-sf", "--max-time", "5", "http://localhost:8080/health"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp health: OK")
        print(result.stdout.strip())
    else:
        log_error("llama-cpp health: FAILED")
        return False
    
    # Check models loaded
    result = run_docker_command(
        ["docker", "exec", "llama-cpp-upstream", "curl", "-sf", "--max-time", "5", "http://localhost:8080/v1/models"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp models endpoint: OK")
        print(result.stdout.strip())
        return True
    else:
        log_warn("llama-cpp models endpoint not responding")
        return False

def check_llama_cpp_embed_status():
    """Check llama-cpp-embed server status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("llama-cpp-embed Status:")
    result = run_docker_command(
        ["docker", "exec", "llama-cpp-embed-upstream", "curl", "-sf", "--max-time", "5", "http://localhost:8080/health"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp-embed health: OK")
        print(result.stdout.strip())
    else:
        log_error("llama-cpp-embed health: FAILED")
        return False
    
    # Check embeddings endpoint
    result = run_docker_command(
        ["docker", "exec", "llama-cpp-embed-upstream", "curl", "-sf", "--max-time", "5", "http://localhost:8080/v1/models"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp-embed models endpoint: OK")
        print(result.stdout.strip())
        return True
    else:
        log_warn("llama-cpp-embed models endpoint not responding")
        return False

def check_network_connectivity():
    """Check network connectivity"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Network Connectivity:")
    result = run_docker_command(
        ["docker", "exec", "tailscale", "ping", "-c", "1", "8.8.8.8"],
        project_root,
        timeout=15
    )
    
    if result and result.returncode == 0:
        log_success("Network connectivity: OK")
        return True
    else:
        log_error("Network connectivity: FAILED")
        return False

def check_tailscale_status():
    """Check Tailscale status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Tailscale Status:")
    result = run_docker_command(
        ["docker", "exec", "tailscale", "tailscale", "--socket=/tmp/tailscaled.sock", "status"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
    else:
        log_warn("Tailscale status check failed")
    
    return result is not None

def check_tailscale_serve():
    """Check Tailscale serve status"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Tailscale Serve Status:")
    result = run_docker_command(
        ["docker", "exec", "tailscale", "tailscale", "--socket=/tmp/tailscaled.sock", "serve", "status"],
        project_root,
        timeout=30
    )
    
    if result and result.returncode == 0:
        print(result.stdout)
    else:
        log_warn("Tailscale serve status check failed")
    
    return result is not None

def check_open_terminal_status():
    """Check Open Terminal service health"""
    project_root = find_project_root()
    if not project_root:
        return False

    print()
    log_info("Open Terminal Status:")

    # open-terminal is the little-coder workspace plane — it left openwebui's
    # network namespace (it is on lc-net / llm-net now), so probe it INSIDE
    # its own container, not via openwebui's localhost:8000.
    result = run_docker_command(
        ["docker", "exec", "open-terminal", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8000/health"],
        project_root,
        timeout=10
    )

    if result and result.returncode == 0 and result.stdout.strip() == "200":
        log_success("Open Terminal health: OK")
        return True
    else:
        log_error("Open Terminal health: FAILED (is the open-terminal container running?)")
        return False


def check_service_accessibility():
    """Check service accessibility"""
    project_root = find_project_root()
    if not project_root:
        return False
    
    print()
    log_info("Service Accessibility Check:")
    
    # Check OpenWebUI accessibility
    result = run_docker_command(
        ["docker", "exec", "tailscale", "wget", "-q", "-T", "3", "-O", "/dev/null", "http://127.0.0.1:8080"],
        project_root,
        timeout=10
    )
    
    if result and result.returncode == 0:
        log_success("OpenWebUI accessibility: OK")
    else:
        log_error("OpenWebUI accessibility: FAILED")

    # NOTE: the Ollama API check was removed — the ollama container is disabled
    # in this stack (see CLAUDE.md). Inference is direct to llama-cpp.

    # Check llama-cpp accessibility
    result = run_docker_command(
        ["docker", "exec", "llama-cpp-upstream", "curl", "-sf", "-o", "/dev/null", "--max-time", "5", "http://localhost:8080/health"],
        project_root,
        timeout=10
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp accessibility: OK")
    else:
        log_error("llama-cpp accessibility: FAILED")
    
    # Check llama-cpp-embed accessibility
    result = run_docker_command(
        ["docker", "exec", "llama-cpp-embed-upstream", "curl", "-sf", "-o", "/dev/null", "--max-time", "5", "http://localhost:8080/health"],
        project_root,
        timeout=10
    )
    
    if result and result.returncode == 0:
        log_success("llama-cpp-embed accessibility: OK")
    else:
        log_error("llama-cpp-embed accessibility: FAILED")
    
    return True

def load_inventory():
    """Load the canonical service inventory (lib/stack-services.json)."""
    # scripts/lib/ - NOT ./lib: this script moved to scripts/recovery/ in the
    # 2026-08-21 G.2 reorg and the relative path silently broke (found 08-22).
    inv_path = Path(__file__).resolve().parents[1] / "lib" / "stack-services.json"
    try:
        with open(inv_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_error(f"Could not load service inventory {inv_path}: {e}")
        return None


def _container_state(name):
    """docker inspect a container's state BY NAME (project-agnostic — works for
    both the ai-stack and open-brain compose projects)."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", name],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else "absent"
    except Exception:
        return "error"


def _container_started_at(name):
    """Raw ISO-8601 UTC start time. UTC ISO strings sort lexicographically, so a
    plain string compare is a safe 'started after' test."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", name],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def check_extended_planes():
    """Liveness across ALL planes — including the SEPARATE open-brain project that
    `docker compose` (ai-stack) cannot see — plus the openbrain-mcp stale-DB-pool
    guard. Data-driven from lib/stack-services.json so this stays in sync with the
    single canonical inventory instead of drifting like the old hard-coded lists."""
    print()
    log_info("Extended Plane Liveness (all planes incl. Open Brain):")
    inv = load_inventory()
    if not inv:
        return False

    ok = True
    planes = inv.get("planes", {})
    for plane_name, services in planes.items():
        down = []
        for svc in services:
            name = svc["container"]
            state = _container_state(name)
            if state != "running":
                down.append(f"{name}={state}")
        if down:
            ok = False
            log_error(f"  {plane_name}: " + ", ".join(down))
        else:
            log_success(f"  {plane_name}: all {len(services)} running")

    # Stale-pool guard: a service holding a long-lived DB connection that died
    # when its DB restarted. Signature case: openbrain-db restarts AFTER
    # openbrain-mcp -> every MCP tool call returns 'Broken pipe' -> OWUI tool 500s,
    # while the container still shows 'Up'. See memory: openbrain-mcp-stale-db-connection.
    for services in planes.values():
        for svc in services:
            guard = svc.get("stale_pool_guard")
            if not guard:
                continue
            mcp = svc["container"]
            mcp_started = _container_started_at(mcp)
            db_started = _container_started_at(guard)
            if mcp_started and db_started and db_started > mcp_started:
                ok = False
                log_warn(f"  {mcp}: STALE DB POOL — {guard} started {db_started} > {mcp} {mcp_started}")
                log_warn(f"     fix: docker restart {mcp}   (or: quick-fixes.bat openbrain)")

    return ok


def main():
    """Main status check function"""
    log_info("==========================================")
    log_info("SYSTEM STATUS CHECK")
    log_info("==========================================")
    
    project_root = find_project_root()
    if not project_root:
        log_error("Could not find project root")
        return 1
    
    log_info(f"Using project root: {project_root}")
    
    # Run all status checks
    checks = [
        check_container_status,
        start_missing_services,
        check_gpu_status,
        check_llm_gateway_status,
        check_llama_cpp_status,
        check_llama_cpp_embed_status,
        check_open_terminal_status,
        check_extended_planes,
        check_network_connectivity,
        check_tailscale_status,
        check_tailscale_serve,
        check_service_accessibility
    ]
    
    success_count = 0
    for check in checks:
        try:
            if check():
                success_count += 1
        except Exception as e:
            log_error(f"Check failed: {e}")
    
    print()
    log_info(f"Status check completed: {success_count}/{len(checks)} checks passed")
    
    if success_count == len(checks):
        log_success("All systems operational")
        return 0
    else:
        log_warn("Some systems need attention")
        return 1

if __name__ == "__main__":
    sys.exit(main())