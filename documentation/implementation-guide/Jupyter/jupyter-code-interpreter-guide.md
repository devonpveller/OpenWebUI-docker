# Jupyter as OWUI's Code-Interpreter Engine — Reasoning & Build Guide

**Status:** Not built. Captured for future use.
**Audience:** the operator (or agent) deciding whether to stand this up later.
**Related:** [docker-compose.yml](../../../docker-compose.yml) (OWUI service, where `ENABLE_CODE_INTERPRETER=False` is currently set).

---

## 1. Why this exists

Open WebUI ships with a built-in `execute_code` tool (`backend/open_webui/tools/builtin.py`). It has two backends:

| Engine | How it runs | Failure mode observed |
|---|---|---|
| **`pyodide`** (default) | Browser-side WebAssembly Python. Backend does `await __event_call__({"type": "execute:python", ...})` and waits for the browser tab to post the result back over WebSocket. | **No timeout.** If the tab is backgrounded, Pyodide fails to load, the WS round-trip stalls, or the worker raises an unhandled error, the `await` never returns. The model sees an in-flight tool call forever; the chat hangs. Confirmed still present on OWUI `main` as of 2026-05-09. |
| **`jupyter`** | Server-side Jupyter kernel reached over HTTP. | Honors `CODE_INTERPRETER_JUPYTER_TIMEOUT`. Doesn't depend on the browser tab being alive. |

In **2026-05-29** we hit the Pyodide hang during a Voya 401k math turn (after a clean research run). Workaround applied:

```yaml
# docker-compose.yml, openwebui service
- ENABLE_CODE_INTERPRETER=${ENABLE_CODE_INTERPRETER:-False}
- ENABLE_CODE_EXECUTION=${ENABLE_CODE_EXECUTION:-False}
```

That removes the broken tool entirely. The chat model now does arithmetic-grade math inline. For anything Qwen-class can compute in its head (compound interest, percentages, simple algebra, unit conversions), this is fine and faster.

**Jupyter is the answer when "in its head" stops being good enough.**

---

## 2. When to build this (decision rules)

Build the Jupyter engine if **any** of the following becomes a recurring need — not for a single one-off:

- **Numeric work on uploaded files.** User attaches a CSV/XLSX and asks for analysis (groupbys, joins, summary stats). The model can describe the answer but cannot actually compute it without running pandas.
- **Plots and visualizations.** Matplotlib/Plotly output that the user wants embedded in the chat.
- **Scientific compute.** scipy stats tests, numpy linalg at scale, sklearn fits, symbolic math via sympy where the model alone gets it wrong past a certain size.
- **Long-running calc with reproducible state.** Multi-step analysis where each step builds on prior cell state (persistent kernel per session).
- **Deterministic arithmetic at scale.** Once the calc has more than ~5 multiplied terms with non-trivial precision, LLM-inline math becomes unreliable enough to want a Python check.

Do **not** build if:

- Your usage is "math that fits on a calculator" and the model handles it inline today.
- You're not willing to keep one more container patched.
- You're not willing to enforce egress sandboxing (server-side Python = remote code execution on your host).

If none of the above bullets hit you in the next 30 days of normal use, stay on the env-var disable. Revisit the decision when you catch yourself wishing for a Python sandbox more than twice.

---

## 3. Architecture choice

| Option | Image | Pros | Cons |
|---|---|---|---|
| **A. `jupyter/scipy-notebook`** | Official Jupyter stack with numpy/scipy/pandas/sklearn/matplotlib pre-installed. | Just works. Familiar. Easy to extend. | ~3 GB image; full notebook server UI you don't need. |
| **B. `jupyter/minimal-notebook`** | Smaller official image. | ~1 GB. Add only what you need. | You'll be `pip install`-ing every dep; harder to reproduce. |
| **C. Custom slim Dockerfile** | `python:3.12-slim` + `jupyter_server` + a pinned scientific stack. | Smallest, most controlled. Best for security. | You maintain the deps. |

**Default recommendation: A** for the first build (lowest friction), revisit B/C only if image-size or supply-chain becomes a real problem.

---

## 4. Pre-flight decisions

Resolve all of these **before** writing the compose service.

| # | Decision | Default | Where it lives |
|---|---|---|---|
| 4.1 | Internal-only or also Tailscale-reachable? | **Internal-only.** Jupyter on a private compose network, no published port. The kernel is called by `openwebui` over `llm-net`. | compose `networks:` |
| 4.2 | Which GPU, if any? Jupyter doesn't need a GPU for the workloads in §2 unless you're running torch/tf. | **CPU only.** Saves a GPU slot. Add GPU later if needed. | compose `deploy.resources` |
| 4.3 | Auth mode: `token` or `password`? | **`token`**, generated and stored in `.env` as `JUPYTER_TOKEN`. Simpler than password hashing. | compose env + `.env` |
| 4.4 | Persistence: do kernel users need files to survive restarts? | **Yes.** Bind a named volume `jupyter-data:/home/jovyan/work`. | compose `volumes:` |
| 4.5 | Egress: should kernel code be able to reach the internet? | **No.** Attach to an isolated network with no internet egress, mirroring how `lc-egress` works for little-coder. Models will try to `pip install requests; r.get(...)` if you let them. | compose `networks:` + per-network `internal: true` |
| 4.6 | Per-user kernels or shared? | **Shared single-user server.** OWUI's `CODE_INTERPRETER_JUPYTER_URL` is one URL; multi-user JupyterHub is overkill for one OWUI user. | n/a |
| 4.7 | Resource caps. | **CPU 2, RAM 4 GiB** to start. Tune after observing real workloads. | compose `deploy.resources.limits` |
| 4.8 | Idle timeout. | **30 min** (`CODE_INTERPRETER_JUPYTER_TIMEOUT=1800`). Long enough for a real analysis; short enough that a runaway loop doesn't camp the kernel forever. | OWUI env |

If 4.5 is set to "yes egress," document why — code running here can exfiltrate anything the kernel can read.

---

## 5. Build steps

### 5.1 Generate the auth token

```powershell
# In d:\Open WebUI\ai-stack
$tok = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | % {[char]$_})
Add-Content .env "`nJUPYTER_TOKEN=$tok"
```

Verify `.env` is git-ignored before committing anything else.

### 5.2 Add the service to `docker-compose.yml`

Insert under `services:`, ordered after `openwebui` so the dependency graph reads naturally:

```yaml
  jupyter:
    image: jupyter/scipy-notebook:latest
    container_name: jupyter
    networks:
      - llm-net           # reachable from openwebui
      # do NOT attach to `default` — keeps kernel off the internet
    volumes:
      - jupyter-data:/home/jovyan/work
    environment:
      - JUPYTER_TOKEN=${JUPYTER_TOKEN}
      - JUPYTER_ENABLE_LAB=no   # OWUI calls the kernel API, not the UI
    command:
      - start-notebook.sh
      - --ServerApp.token=${JUPYTER_TOKEN}
      - --ServerApp.password=''
      - --ServerApp.allow_origin=*
      - --ServerApp.ip=0.0.0.0
      - --ServerApp.port=8888
      - --ServerApp.disable_check_xsrf=True
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    read_only: false
    tmpfs:
      - /tmp:noexec,nosuid,size=200m
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 4G
    healthcheck:
      test: ["CMD", "curl", "-fsS", "-H", "Authorization: token ${JUPYTER_TOKEN}", "http://localhost:8888/api"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```

Then add the named volume at the bottom of the file (next to other `volumes:` entries):

```yaml
volumes:
  jupyter-data:
```

### 5.3 Point OWUI at it

Edit the `openwebui` service env block. Replace the current disable block:

```yaml
      # --- DELETE these (the Pyodide-disable workaround) ---
      - ENABLE_CODE_INTERPRETER=${ENABLE_CODE_INTERPRETER:-False}
      - ENABLE_CODE_EXECUTION=${ENABLE_CODE_EXECUTION:-False}
```

…with the Jupyter wiring:

```yaml
      # Code interpreter: server-side Jupyter kernel on llm-net.
      # See documentation/implementation-guide/Jupyter/.
      - ENABLE_CODE_INTERPRETER=True
      - ENABLE_CODE_EXECUTION=True
      - CODE_INTERPRETER_ENGINE=jupyter
      - CODE_INTERPRETER_JUPYTER_URL=http://jupyter:8888
      - CODE_INTERPRETER_JUPYTER_AUTH=token
      - CODE_INTERPRETER_JUPYTER_AUTH_TOKEN=${JUPYTER_TOKEN}
      - CODE_INTERPRETER_JUPYTER_TIMEOUT=1800
```

### 5.4 Update the recovery scripts

Per `CLAUDE.md`: adding a container means three places change. Touch:

- `docker-compose.yml` (done in 5.2 / 5.3)
- `scripts/emergency-recovery.ps1` and `.bat` — add `jupyter` to the service inventory and to the shutdown/startup sequences. Bring it up **after** `openwebui` is healthy (it's a dependency, not a peer).
- `.claude/skills/stack-map/references/workspace-stacks.md` — add `jupyter` under the "Main" stack's `aux` row.

### 5.5 Bring it up

```powershell
docker compose up -d jupyter
docker compose up -d openwebui   # picks up the new env
```

### 5.6 Verify

```powershell
# 1. Jupyter is healthy and token-authed
docker exec jupyter curl -fsS -H "Authorization: token $env:JUPYTER_TOKEN" http://localhost:8888/api

# 2. OWUI sees it
docker exec openwebui sh -c 'curl -fsS -H "Authorization: token $CODE_INTERPRETER_JUPYTER_AUTH_TOKEN" http://jupyter:8888/api'

# 3. End-to-end: in the OWUI chat, send a model a math prompt that triggers
#    execute_code. Expect a real result in <30s, NOT a hang.
#    Then test a wrong import (e.g. `import urllib; urllib.request.urlopen("https://example.com")`)
#    to confirm egress is blocked (decision 4.5).
```

If step 3 hangs again, the engine selection didn't take — re-check `CODE_INTERPRETER_ENGINE` value inside the openwebui container (`docker exec openwebui env | grep CODE_`), then check OWUI admin UI: Settings → Code Execution.

---

## 6. Security

Server-side Python execution is **remote code execution on your host** dressed up nicely. Treat it that way.

- **Network isolation (decision 4.5).** Without it, anything the model can be talked into running can hit your tailnet, your LAN, the internet. The `lc-egress` pattern is the proven template — replicate it.
- **No host bind mounts.** The kernel must not see `d:\Open WebUI\ai-stack` or any other host path. Only the named `jupyter-data` volume.
- **Resource caps (decision 4.7).** A fork bomb or memory blowup must not take down the host.
- **Token rotation.** Rotate `JUPYTER_TOKEN` on any suspected leak. Coordinated restart of both `jupyter` and `openwebui` is required for the change to take effect.
- **Blocked modules.** OWUI already honors `CODE_INTERPRETER_BLOCKED_MODULES`. Consider blocking at least `socket`, `urllib`, `requests`, `subprocess`, `ctypes` once you confirm none of your legit workloads need them.

---

## 7. Operations

- **Backups.** `jupyter-data` is user work product. If you care about it surviving disk loss, add it to the existing backup rotation (mirror the `openwebui-backup` pattern). If users treat the kernel as scratch — skip.
- **Updates.** Watchtower covers the `latest` tag. For Jupyter that's usually fine; pin to a digest if you've seen breaking changes between releases.
- **Health.** The healthcheck calls `/api` with the token — failure usually means the token in `.env` drifted from what's mounted in the container.
- **Logs.** `docker logs jupyter` shows kernel start/stop and any code that raised. Useful when an `execute_code` call returns an error instead of hanging.

---

## 8. Rollback

If Jupyter becomes a maintenance burden or a security concern:

```powershell
docker compose stop jupyter
docker compose rm -f jupyter
```

Restore the env-var disable in `openwebui`:

```yaml
- ENABLE_CODE_INTERPRETER=False
- ENABLE_CODE_EXECUTION=False
```

Remove `jupyter` from the recovery scripts and stack-map. Optionally `docker volume rm ai-stack_jupyter-data` if you don't want the kernel work product.

`openwebui` keeps running fine — the disable is the original known-good state.

---

## 9. Open questions to resolve at build time

- Does any current OWUI workflow rely on Pyodide-specific behavior (e.g. browser-only file access)? Audit before flipping the engine.
- Do we want to expose Jupyter Lab UI separately (for the operator, not the model)? Adds attack surface; default no.
- GPU access (decision 4.2): if torch ever becomes a real need, plan the device allocation against the existing `GPU_AISTACK_DEVICE_ID` split.
