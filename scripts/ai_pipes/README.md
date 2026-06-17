# `scripts/ai_pipes/` — the "Server Status" pipe build subsystem

This folder is **not** a pile of loose plugins — it is the **build source for one
OWUI pipe**: the AI-Stack unified status pipe (shows as **"Server Status"** in the
model picker; OWUI plugin id `ai_stack_unified_pipe_function`).

> **The deployed, paste-ready snapshot lives at
> [`owui/pipes/server_status.py`](../../owui/pipes/server_status.py)** (canonical,
> == live `webui.db`). Everything here is the *source* it's assembled from. Edit
> here → re-flatten → redeploy that snapshot.

## Which file is the pipe?

**[`unified_openwebui_pipe.py`](unified_openwebui_pipe.py)** — THIS is the Server
Status pipe (the single OWUI entry point / orchestrator). It is the only file in
this folder that gets pasted into OWUI. On each call it loads the router fresh
from disk and dispatches:

```
OpenWebUI  →  unified_openwebui_pipe.py  →  core/router.py  →  modules/ (manifest-driven)
              (the OWUI Pipe = "Server Status")   (routing)     (system-health, gpu-status,
                                                                  llm-traffic, emergency-recovery,
                                                                  help-system, custom-tools, …)
```

The live routing logic is [`core/router.py`](../../core/router.py) at the repo
root; the capability implementations are the top-level [`modules/`](../../modules/).

## The other files

| File | What it is |
|------|------------|
| `unified_openwebui_pipe.py` | **The deployed Server Status pipe** (orchestrator). ← this one. |
| `gpu_status_pipe.py`, `system_health_pipe.py`, `emergency_recovery_pipe.py`, `custom_tools_pipe.py`, `help_pipe.py`, `tailscale_serve_pipe.py` | **Legacy individual pipes** — the standalone status/admin pipes the unified pipe *replaced* (it "replaces all individual AI Stack pipe functions"). Kept as reference / partial sources; `gpu_status_pipe.py` is still imported by `test/test_gpu_formatting.py`. The current capability logic lives under `modules/`. |
| `openwebui_pipe_template.py` | Boilerplate template for authoring a new host-script pipe. |
| `config.json` | Subsystem config. |
| `__init__.py` | Makes `ai_pipes` importable (used by tests). |

## Why it's not in `owui/`

`owui/` holds **flattened, deploy-ready** plugin snapshots — one file per OWUI
plugin. This pipe is *composed* from a router + modules at runtime, so its source
is a multi-file subsystem (like a service). Only its flattened result belongs in
`owui/pipes/`; the build source stays here. Same pattern as `little-coder/` and
`smolcrawl/deep_research/`.
