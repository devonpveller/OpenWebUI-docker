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
| `tools/`   | Tools (model-callable)        | `superpowers_tool`, `fileshed`, `mnemory`, `deep_research`, `github_chat_mcp_tools` (GitHub Repo Analyzer) |
| `filters/` | Filter functions              | `context_window_manager`, `mnemory_persistent_memory` |
| `pipes/`   | Pipe functions (custom models)| `server_status` (the AI-Stack unified status pipe), `little_coder`, `githelper`, `github_chat_mcp` |
| `actions/` | Action functions (buttons)    | `copy_research_note`, `copy_sources` (`add_web_sources_to_knowledge` retired 2026-08-20 — wrote into the retired OWUI Knowledge layer; deactivated in webui.db, snapshot in `scripts/archive/owui-retired/`) |
| `skills/`  | Skills (attached to models via `skillIds`) | `skill-creator`, `docx`, `canvas-design`, `doc-coauthoring`, `github-repo-analyzer`, `feature-validation-workflow`, `github-repo-expert`, `openwebui-tools` |

**Skills note (2026-08-20):** files are named by their OWUI **skill id**, which
is what `skillIds` on a model references — NOT by display name (the trap that
hid this class from audits: `github-repo-analyzer` was previously a root-level
file named `github-chat-mcp.md`). All 8 exported live from `webui.db`
2026-08-20; the old partial root `skills/` folder (3 of 8, stale names) was
retired the same day in favour of this complete set.

`manifest.csv` lists `file, type, name, owui_id, sha256` for all 13 tool/function
files + the 8 skills — 21 rows (the "16" this line carried until 2026-09-06 was
the pre-retirement count; `add_web_sources_to_knowledge`, `code_agent` and
`code_agent_tools` left the manifest in August and the total was never
recomputed). The `sha256` column is the CR-normalized digest of the repo file,
which is what `scripts/checks/check-owui-drift.ps1` compares against the live
row. Functions are
async-compatible with OWUI **0.11.0** (re-verified 2026-08-20 against v0.11.0
source: `Files/Groups/Chats/Notes/Knowledges` model methods are still `async`,
so the 2026-06 async port carries forward unchanged).

## Relationship to service folders

Self-contained plugins live **only** here. Plugins that front a **service** keep
their service code in the service's own folder; only the OWUI-facing artifact is
centralized here:

- `pipes/little_coder.py` ← service: [`little-coder/`](../little-coder/)
- `tools/deep_research.py` ← service: `OB1/integrations/research-service/` (the
  `openbrain-research` Deno engine). The Python harness that used to live at
  `smolcrawl/deep_research/`, and the v1.0.0 client snapshot at
  `smolcrawl/deep_research_thin_client.py`, were retired 2026-08-20 — this file
  is the only OWUI-side artifact.
- `pipes/server_status.py` (OWUI id `ai_stack_unified_pipe_function`) is the
  verbatim deploy snapshot of [`status-pipe/orchestrator.py`](../status-pipe/)
  (the unified status pipe subsystem — orchestrator + router + modules +
  schemas, consolidated 2026-08-20). The orchestrator loads
  `status-pipe/router.py` fresh from its mount on each call, so router/module
  edits go live without a re-paste; orchestrator edits need a re-paste.

## Deployment sync status

**Ask the check, not this file:**

```powershell
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1
```

It compares the CR-normalized SHA-256 of every `manifest.csv` row against the
live `content` in the `openwebui` container's `webui.db` (hashed inside the
container; read-only, `mode=ro`) and prints IN SYNC / DIFFERS / MISSING LIVE /
MISSING REPO per row, exit 1 on any difference and exit 2 — with a sentence —
if it could not read the container at all. `stack.ps1 health` runs it for the
drifted count.

A dated sentence here cannot stay true: the 2026-08-20 claim that "all 16 files
are byte-identical to the live `webui.db`" was checked by hand once and had no
way to notice the next unpasted fix. **`manifest.csv` digests were regenerated
2026-09-06**, which dates the column and nothing else — the live side moves
without touching this repo, so only a run of the check says anything about now.

What the check will not tell you: whether a plugin WORKS, whether OWUI has
reloaded it, whether its **valves** are right (a separate column, never read),
or which side is newer when two hashes differ.

`tools/deep_research.py` was re-pasted on 2026-08-20 at **v1.2.0** (async
completion callback: the tool hands off and the `openbrain-research` engine POSTs
the finished report back into the chat message). Verified live in `webui.db`.
The tool reads `callback_armed` from the engine's submit response, so it is safe
against an older engine — it simply keeps blocking — but the engine needs
`RESEARCH_OWUI_API_KEY` in **`OB1/docker/.env`** before the callback does
anything, and a key in the main stack `.env` silently reads as "not armed".

History — the two drift items recorded here were resolved by the 2026-08-20
hand check; the drift check above is what answers this question now:

- **`pipes/server_status.py`** (Server Status) — WAS stale; rebuilt and
  redeployed during the 0.11.0 upgrade. Since 2026-08-20 its build source is
  [`status-pipe/orchestrator.py`](../status-pipe/) (G.1 consolidation).
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

> `code_agent` + `code_agent_tools` RETIRED 2026-08-21 (operator): the
> pre-little-coder coding harness. Files archived to
> `scripts/archive/owui-retired/`; rows removed from the live webui.db
> (pipe was already inactive) and from `manifest.csv`.
