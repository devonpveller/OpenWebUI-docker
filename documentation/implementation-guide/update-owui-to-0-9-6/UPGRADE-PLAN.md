# Open WebUI 0.8.10 → 0.9.6 — upgrade plan

Status: **planning**. First irreversible action is Step 4 (rebuild + first 0.9.6
boot runs forward-only DB migrations). Everything before that is reversible.

Source of risk assessment: official 0.9.x changelog + 0.9.0 plugin migration guide,
cross-checked against this workspace's actual OWUI config and custom code.

## How OWUI is wired here (the facts the plan depends on)

- **Image:** custom GPU build, `Dockerfile.openwebui-gpu` → base `ghcr.io/open-webui/open-webui:v0.8.10`,
  plus a `sed` patch on `builtin.py` (UTC→local timezone) and a CUDA 12.1 PyTorch layer.
  `pull_policy: build` — we build locally, we do **not** pull the upstream tag at runtime.
- **Live data:** named volume **`openwebui-data`** mounted at `/app/backend/data`
  (NOT the stale `data/openwebui/` host dir). Holds `webui.db` (~634 MB), `vector_db/`
  (HNSW), `uploads/`, and the auto-generated `.webui_secret_key`.
- **DB:** SQLite (`aiosqlite` under 0.9.x async), single instance. No `DATABASE_URL` set.
- **`WEBUI_SECRET_KEY`:** **not set** — currently auto-generated into the volume.
- **Custom Functions/Tools** (pasted into OWUI, deploy-by-paste; source artifacts in repo):
  - `data/openwebui/deep_research_function.py` — imports `open_webui.utils.chat.generate_chat_completion`,
    `open_webui.models.users.UserModel`, `open_webui.routers.retrieval.search_web`.
  - `scripts/ai_pipes/superpowers.py` — same internal imports.
  - `scripts/ai_pipes/fileshed.py` — uses `open_webui.models.groups.Groups`,
    `open_webui.models.files.Files/FileForm` **directly** (DB model classes).
  - `scripts/ai_pipes/github_chat_mcp_*.py`, `tailscale_serve_pipe.py` — HTTP-only
    (the latter's `/api/config` probe targets open_notebook, not OWUI).
- **Env flags already set:** `BYPASS_MODEL_ACCESS_CONTROL=true`, web search via SearXNG/Tor
  gateway, code interpreter disabled, several AIOHTTP timeouts.

## Breaking changes that actually affect this stack

Ranked by impact. Items the changelog lists but that **don't** apply here are at the bottom.

### 1. Async backend refactor (0.9.0) — HIGH

Every model method (`Users./Files./Knowledges./Groups./Tools.` …) is now `async def`
and must be `await`ed; internal helpers moved (`get_db_context` → `get_async_db_context`,
`Session` → `AsyncSession`, raw `db.query()` → `await db.execute(select(...))`).

**Exposure here:**
- `fileshed.py` — **highest risk**: calls `Files`/`Groups`/`FileForm` model classes
  directly. Each such call now needs `await`, and any method wrapping them must become
  `async`. Will throw (coroutine never awaited / attribute errors) until patched.
- `deep_research_function.py` and `superpowers.py` — import `generate_chat_completion`,
  `UserModel`, `routers.retrieval.search_web`. Re-validate: (a) the symbols still exist
  at those import paths, (b) `search_web`'s signature/behaviour (it now enforces KB
  access — see §5), (c) anything previously sync is awaited.
- Pure-HTTP pipes (`github_chat_mcp_*`, `tailscale_serve_pipe`) — **no change needed**
  per the migration guide ("Tools that only call external APIs … no change required").

**Action (before upgrade):**
1. Stand up a 0.9.6 image in a throwaway build (Step 3) and import each function file.
2. Mechanical sweep: add `await` before every `Users./Chats./Files./Models./Functions./Tools./Knowledges./Groups.` call;
   make enclosing methods `async`; swap `get_db_context`→`get_async_db_context`,
   `Session`→`AsyncSession`, `db.query(...)`→`await db.execute(select(...))`.
3. Confirm the moved import paths resolve against 0.9.6 source.

**Action (after upgrade):** re-paste the patched functions (these deploy by paste).
Keep the originals as rollback artifacts.

### 2. `WEBUI_SECRET_KEY` now hard-required (0.9.6) — HIGH

> "WEBUI_SECRET_KEY is now a hard requirement even for unsupported deployments."

We don't set it; the live key is auto-generated **inside the `openwebui-data` volume**.
If 0.9.6 refuses to boot without the env var — or we set a *different* value — every
JWT session invalidates and any encrypted-at-rest secret becomes undecryptable.

**Action (before upgrade):** extract the live key and pin it verbatim.
```powershell
# container up:
docker exec openwebui sh -c 'cat /app/backend/data/.webui_secret_key'
# container down:
docker run --rm -v ai-stack_openwebui-data:/d alpine cat /d/.webui_secret_key
```
Set `WEBUI_SECRET_KEY=<that exact value>` in `.env` / compose env for `openwebui`.
Do **not** invent a new one.

### 3. Forward-only DB migrations, no rolling update (0.9.3, 0.9.6) — HIGH

> "database schema changes … back up your database … rolling updates are not supported."

`webui.db` is ~634 MB; first 0.9.6 boot migrates it and there is **no downgrade path**.

**Action (before upgrade):**
- Stop `openwebui` (+ the `tailscale` sidecar sharing its netns) cleanly.
- Take a **fresh** volume snapshot (don't trust the nightly tar mid-window):
  ```powershell
  docker run --rm -v ai-stack_openwebui-data:/d -v ${PWD}/backups/openwebui:/out `
    alpine tar czf /out/openwebui-preupgrade-0910.tar.gz -C /d .
  ```
- Record the current image id / keep the 0.8.10 build context.
- **Rollback = restore that tar into the volume + revert the Dockerfile base tag + rebuild.**
- Budget downtime for migration on a 634 MB DB (minutes, not seconds).

### 4. Custom `builtin.py` `sed` patch may silently no-op (Dockerfile) — MEDIUM/HIGH

The async refactor likely moved/renamed `builtin.py`; a `sed` that matches nothing
fails silently (your TZ fix vanishes) or a missing path breaks the build.

**Action (before upgrade):** diff `Dockerfile.openwebui-gpu`'s patch target against
0.9.6 source; update path/pattern; add a build-time assertion that the substitution
actually applied (grep the patched line, fail the build if absent).

### 5. Knowledge-collection access now enforced + `ENABLE_RETRIEVAL_UNSCOPED_COLLECTIONS` (0.9.6) — MEDIUM

> "Knowledge base access verification now enforced in search tool." Collection
> queries block unauthorized enumeration and require read access.

`BYPASS_MODEL_ACCESS_CONTROL=true` does **not** cover *knowledge* access — that's
separate. RAG can silently return nothing if the querying user lacks read on a
collection. **Largely moot** once we've migrated to OB1 and stopped using OWUI
knowledge (the decided end state), but during the transition:

**Action (after upgrade, only if still using OWUI RAG):** smoke-test retrieval; if a
collection returns nothing, either fix ownership/group or set
`ENABLE_RETRIEVAL_UNSCOPED_COLLECTIONS=true`.

### 6. Tool-call iteration env renamed + default 30→256 (0.9.6) — LOW/MEDIUM

`CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES` → `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`,
default raised **30 → 256**. We don't set it, so we'd inherit 256 — deep_research /
little-coder tool loops could run far longer (latency + token cost).

**Action (before upgrade):** pin `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` to ~30–50.

### 7. Redirects blocked by default — `AIOHTTP_CLIENT_ALLOW_REDIRECTS` (0.9.5) — LOW/MEDIUM

Web search runs through the SearXNG/Tor gateway; a redirecting hop will now fail.

**Action (after upgrade):** smoke-test web search; set `AIOHTTP_CLIENT_ALLOW_REDIRECTS=true`
if it breaks.

### Not applicable here (explicitly cleared)

- **asyncpg → psycopg v3 (0.9.2):** Postgres-only. We're SQLite (`aiosqlite`). N/A.
- **Reduced unauthenticated `/api/config` (0.9.6):** our `/api/config` probes target
  **open_notebook :5055**, not OWUI :8080 — recovery scripts unaffected. (OWUI's own
  healthcheck does not scrape it.)
- **Signout GET→POST (0.9.3):** no script auto-logs-out of OWUI. N/A.
- **`.doc` support, MinerU file-type config, profile-image size cap:** additive, no action.

## Execution checklist

**Pre-flight (reversible):**
1. ☐ Complete the knowledge migration first (see KNOWLEDGE-MIGRATION-PLAN.md) and verify in OB1.
2. ☐ Extract + pin `WEBUI_SECRET_KEY` (§2).
3. ☐ Pin `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` (§6).
4. ☐ Patch `Dockerfile.openwebui-gpu` base tag → `v0.9.6`; fix + assert the `builtin.py` patch (§4).
5. ☐ Throwaway 0.9.6 build; import + patch the three at-risk functions; keep originals (§1).

**Cutover (first irreversible step = 8):**
6. ☐ Stop `openwebui` + `tailscale` sidecar cleanly.
7. ☐ Fresh volume snapshot `openwebui-preupgrade-0910.tar.gz` (§3).
8. ☐ **Rebuild + start 0.9.6** (forward-only migration runs here).
9. ☐ Re-paste patched Functions/Tools (§1).

**Post-flight verification:**
10. ☐ Login works (secret key continuity), chat + model list OK.
11. ☐ deep_research tool runs end-to-end; little-coder + search MCP tools (`:8001/:8002`) re-discovered.
12. ☐ Web search smoke test (§7).
13. ☐ Timezone rendering correct (the `builtin.py` patch held).
14. ☐ GPU inference + embeddings (`llama-cpp` / `llama-cpp-embed`) healthy.
15. ☐ Stack-map / recovery scripts: no inventory change needed (no containers added/removed),
    but confirm the new `WEBUI_SECRET_KEY` env is captured in any env-dump tooling.

**Rollback:** stop → restore `openwebui-preupgrade-0910.tar.gz` into the volume →
revert Dockerfile base tag → rebuild → re-paste original functions.
