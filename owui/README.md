# `owui/` — centralized Open WebUI plugins

Canonical source for every **deploy-by-paste** Open WebUI plugin in this stack:
Tools, Filters, Pipes, and Actions. Each file's content is an **exact export of
what is deployed** in the live `webui.db` (exported 2026-06-16). This folder is
the source of truth — edit here, then redeploy (paste / `UPDATE …content`).

Files are named for **human readability** (usually == the OWUI plugin id). Where
the id is cryptic the filename uses the friendly name instead — e.g. the
`Server Status` pipe is `pipes/server_status.py` (OWUI id
`ai_stack_unified_pipe_function`). `manifest.csv` is the authoritative
`file → owui_id` map; use the `owui_id` when redeploying by `UPDATE`.

| Folder | OWUI kind | Files |
|--------|-----------|-------|
| `tools/`   | Tools (model-callable)        | `superpowers_tool`, `fileshed`, `mnemory`, `deep_research`, `github_chat_mcp_tools` (GitHub Repo Analyzer), `code_agent_tools` |
| `filters/` | Filter functions              | `context_window_manager`, `mnemory_persistent_memory` |
| `pipes/`   | Pipe functions (custom models)| `server_status` (the AI-Stack unified status pipe), `little_coder`, `githelper`, `github_chat_mcp`, `code_agent` *(inactive)* |
| `actions/` | Action functions (buttons)    | `add_web_sources_to_knowledge`, `copy_research_note`, `copy_sources` |

`manifest.csv` lists `file, type, name, owui_id, bytes` for all 16. All are
async-compatible with OWUI **0.11.0** (re-verified 2026-08-20 against v0.11.0
source: `Files/Groups/Chats/Notes/Knowledges` model methods are still `async`,
so the 2026-06 async port carries forward unchanged).

## Relationship to service folders

Self-contained plugins live **only** here. Plugins that front a **service** keep
their service code in the service's own folder; only the OWUI-facing artifact is
centralized here:

- `pipes/little_coder.py` ← service: [`little-coder/`](../little-coder/)
- `tools/deep_research.py` ← service: [`smolcrawl/deep_research/`](../smolcrawl/deep_research/) (openbrain-research)
- `pipes/code_agent.py` + `tools/code_agent_tools.py` ← [`tools/code-generation/`](../tools/code-generation/) (docs/system prompt)
- `pipes/server_status.py` (OWUI id `ai_stack_unified_pipe_function`) is the
  flattened deploy snapshot of the **AI-Stack unified status pipe**. Its build
  subsystem — the orchestrator `unified_openwebui_pipe.py`, its `core/router.py`,
  and the per-capability `*_pipe.py` modules — lives in
  [`scripts/ai_pipes/`](../scripts/ai_pipes/) (see that folder's README). The
  orchestrator loads the router fresh from disk on each call; this snapshot is
  what's pasted into OWUI.

## Deployment sync status

**2026-08-20 — all 16 files here are byte-identical to the live `webui.db`**
(CR-normalized SHA-256 compared against the deployed `content` of every tool and
function). `manifest.csv` byte counts were regenerated at the same time.

`tools/deep_research.py` was re-pasted the same day at **v1.2.0** (async
completion callback: the tool hands off and the `openbrain-research` engine POSTs
the finished report back into the chat message). Verified live in `webui.db`.
The tool reads `callback_armed` from the engine's submit response, so it is safe
against an older engine — it simply keeps blocking — but the engine needs
`RESEARCH_OWUI_API_KEY` in **`OB1/docker/.env`** before the callback does
anything, and a key in the main stack `.env` silently reads as "not armed".

The two drift items previously recorded here are resolved:

- **`pipes/server_status.py`** (Server Status) — WAS stale: the deployed snapshot
  lacked the `llm-traffic` panel and reported 32 services. Rebuilt from
  [`scripts/ai_pipes/unified_openwebui_pipe.py`](../scripts/ai_pipes/unified_openwebui_pipe.py)
  and redeployed during the 0.11.0 upgrade; now carries the LiteLLM/llm-queue
  gateway panel and 34-service coverage.
- **`tools/deep_research.py`** — was NOT actually drifted. The live tool and the
  repo copy differed only by black line-wrapping of one `await emit(...)` call;
  the deployed valve `research_url` has been the correct
  `http://openbrain-research:8000` all along (only the in-code *default* still
  reads `host.docker.internal:8818`, which the stored valve overrides). This file
  is now an exact re-export of the deployed content.

> Valves are stored separately from `content`, so a content redeploy does not
> reset them — but a fresh *paste* through the Admin UI can. Capture valves before
> re-pasting anything that carries a URL or key.

## Redeploy mechanism

These deploy by paste (Admin → Functions / Tools → edit → replace → save) or by a
direct `UPDATE function|tool SET content=…` in the container's `webui.db`
(stage in `/app`, not the noexec `/tmp`), then restart `openwebui` **and** the
`tailscale` sidecar. The async ports are **0.9.x/0.11.x** — do not paste into a
0.8.x instance.

> **Netns ordering (not optional):** `tailscale` runs `network_mode:
> service:openwebui`, so it lives inside OWUI's network namespace and carries
> **8 tailnet serve routes, 7 of which are nothing to do with OWUI**
> (open_notebook ×2, quartz wiki, llm-gateway-ui, mattermost, and the
> `llama-cpp` / `llama-cpp-embed` gateway aliases). Restarting `openwebui`
> rebuilds that namespace and orphans `tailscale` — it stays "Up" but loses all
> connectivity. Always: restart `openwebui` → wait until **healthy** → then
> restart `tailscale`, which re-applies its whole serve config on boot.
