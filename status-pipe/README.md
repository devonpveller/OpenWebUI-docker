# status-pipe — the AI-Stack unified status pipe subsystem

> Consolidated 2026-08-20 (CLEANUP-PLAN v3 G.1) from four top-level dirs:
> `scripts/ai_pipes/` + `core/` + `modules/` + `schemas/`. This directory is
> the ONLY code mount into the OpenWebUI container
> (`./status-pipe:/host_project/status-pipe:ro`), replacing the old
> whole-repo mount (v3 A.2).

One logical component: the OWUI "Server Status" pipe and everything it
dispatches.

```
status-pipe/
  orchestrator.py   the pipe entrypoint — deployed VERBATIM as
                    owui/pipes/server_status.py (OWUI id
                    ai_stack_unified_pipe_function). It exec_module-loads
                    router.py fresh from this mount on every call, so router/
                    module edits go live WITHOUT re-pasting; edits to
                    orchestrator.py itself DO need a re-paste (owui/README).
  router.py         keyword → module routing + ModuleRegistry (discovers
                    modules/*/module.manifest.json, validates against schemas/)
  modules/          custom-tools · gpu-status · help-system · llm-traffic ·
                    system-health · system-orchestrator
  schemas/          request_envelope / module_result / module_manifest
  serve/            tailscale_serve_pipe.py — LIVE serve management, dispatched
                    by the custom-tools module; drives
                    modules/custom-tools/service/tailscale_serve_admin.py
  utilities/        shared helpers imported by modules (docker/gpu/system)
```

Rules:

- Inference stays behind the gateway: anything here that talks to a model
  uses the `llama-cpp` / `llama-cpp-embed` aliases — never `*-upstream`
  (`scripts/checks/check-llm-gateway-routing.ps1` enforces).
- The retired `emergency-recovery` module (ollama-era guidance) lives in
  `scripts/archive/emergency-recovery-module/`; recovery questions route to
  help-system, and the real recovery story is
  `scripts/recovery/emergency-recovery.ps1` (v3 D-15).
- The serve pipe also reads `/host_project/data/tailscale/tailnet-info.json`
  (a second, read-only mount) for tailnet URL reporting.
