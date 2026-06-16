# `owui/` — centralized Open WebUI plugins

Canonical source for every **deploy-by-paste** Open WebUI plugin in this stack:
Tools, Filters, Pipes, and Actions. Each file is named by its OWUI **plugin id**
and its content is an **exact export of what is deployed** in the live
`webui.db` (exported 2026-06-16). This folder is the source of truth — edit here,
then redeploy (paste / `UPDATE …content`).

| Folder | OWUI kind | Files |
|--------|-----------|-------|
| `tools/`   | Tools (model-callable)        | `superpowers_tool`, `fileshed`, `mnemory`, `deep_research`, `github_chat_mcp_tools` (GitHub Repo Analyzer), `code_agent_tools` |
| `filters/` | Filter functions              | `context_window_manager`, `mnemory_persistent_memory` |
| `pipes/`   | Pipe functions (custom models)| `ai_stack_unified_pipe_function` (Server Status), `little_coder`, `githelper`, `github_chat_mcp`, `code_agent` *(inactive)* |
| `actions/` | Action functions (buttons)    | `add_web_sources_to_knowledge`, `copy_research_note`, `copy_sources` |

`manifest.csv` lists `id, type, name, bytes` for all 16. All are async-compatible
with OWUI **0.9.6** (audited 2026-06-16 — no un-awaited now-async model calls).

## Relationship to service folders

Self-contained plugins live **only** here. Plugins that front a **service** keep
their service code in the service's own folder; only the OWUI-facing artifact is
centralized here:

- `pipes/little_coder.py` ← service: [`little-coder/`](../little-coder/)
- `tools/deep_research.py` ← service: [`smolcrawl/deep_research/`](../smolcrawl/deep_research/) (openbrain-research)
- `pipes/code_agent.py` + `tools/code_agent_tools.py` ← [`tools/code-generation/`](../tools/code-generation/) (docs/system prompt)
- `pipes/ai_stack_unified_pipe_function.py` (Server Status) is **assembled** from
  the modular build subsystem in [`scripts/ai_pipes/`](../scripts/ai_pipes/)
  (orchestrator + component pipes, dynamically loaded via `importlib`, with a
  test in `test/`). That subsystem stays put; this is the flattened deploy snapshot.

## ⚠️ Known deployment drift (deployed is BEHIND the repo source)

Two plugins have repo dev-sources that are **newer** than what was exported here
(i.e. the live deployment is stale and should be re-pasted):

- **`tools/deep_research.py`** — the deployed copy defaults its base URL to
  `http://host.docker.internal:8818`, which is unreachable from inside a
  container ("Server disconnected"). The newer
  [`smolcrawl/deep_research_thin_client.py`](../smolcrawl/deep_research_thin_client.py)
  fixes this to `http://openbrain-research:8000` and adds reuse/fetch/wall-time
  metrics. **Redeploy that file** to OWUI, then re-export.
- **`pipes/ai_stack_unified_pipe_function.py`** (Server Status) — the
  `scripts/ai_pipes/` build source carries an `llm-traffic` panel the deployed
  snapshot lacks. Rebuild + redeploy from there when convenient.

Every other file here == its repo source semantically (AST-verified; the repo
copies differed only by black formatting and were removed during centralization).

## Redeploy mechanism

These deploy by paste (Admin → Functions / Tools → edit → replace → save) or by a
direct `UPDATE function|tool SET content=…` in the container's `webui.db`
(stage in `/app`, not the noexec `/tmp`), then restart `openwebui` **and** the
`tailscale` sidecar (shared netns). The async ports are **0.9.x-only** — do not
paste into a 0.8.x instance.
