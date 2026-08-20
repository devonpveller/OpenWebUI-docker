# Open WebUI 0.9.6 → 0.11.0 — upgrade plan

Status: **EXECUTED 2026-08-20 — the stack is on v0.11.0.** See the
[EXECUTED record](#executed--2026-08-20-success-owui-now-on-v0110) at the end for
what actually happened, the deviations, and the rollback that is still available.
The plan below is preserved as written *before* the cutover; the first irreversible
action was Step 10 (rebuild + first 0.11.0 boot runs forward-only DB migrations).

Supersedes the executed [0.8.10 → 0.9.6 plan](../update-owui-to-0-9-6/UPGRADE-PLAN.md)
(kept as the historical record — do **not** follow that runbook for this upgrade).

Source of risk assessment: upstream v0.10.0 / v0.10.2 / v0.11.0 release notes,
cross-checked line-by-line against **v0.11.0 source** and against the **live
0.9.6 container + `webui.db`** — not against assumptions. Audit run 2026-08-20.

---

## Scope confirmation — what changed vs the 2026-08-07 audit

A prior audit (2026-08-07) set the direction for this upgrade. Re-running it
against live ground truth **removed its two largest risk items**. Both were
based on reasonable inference that turns out not to match the deployed system.

### ✅ CORRECTION 1 — `--jinja` is NOT a prerequisite. Native tool calling already works.

The 08-07 audit locked: *"Current `config/llama-swap.config.yaml` runs llama-server
WITHOUT `--jinja`, so the inference plane can't emit structured tool_calls today.
Plan: add `--jinja`, validate, THEN set OWUI models to Native."*

**This is false.** `--jinja` is indeed absent (`config/llama-swap.config.yaml:28`
`common-args` carries only `--chat-template-file`), but a live probe through the
**full production path** — `llama-cpp` alias → LiteLLM → `llm-queue` →
llama-swap → llama.cpp `b9935-f2d1c2f39` — returned a correctly structured
tool call:

```
finish_reason : "tool_calls"
tool_calls[0] : {"name":"get_weather","arguments":"{\"city\":\"Boston\"}"}
```

Structured tool calling is **already live and correct without `--jinja`**.
Corroborating evidence: 7 of the 9 tool-attaching OWUI models are *already* set
to `function_calling=native` on 0.9.6 today and are in daily use.

**Effect on scope:** the entire "inference-plane prerequisite" work stream is
**deleted**. No `--jinja` change, no separate pre-upgrade validation window, no
llama-server restart, no risk of Qwen3.8 template regression. Do not add
`--jinja` as part of this upgrade — it is an unnecessary change to a working
plane. (Speculative decoding is also healthy in that probe: `draft_n: 18,
draft_n_accepted: 18`.)

### ✅ CORRECTION 2 — the `smolcrawl/deep_research/*` coupling is dead code.

The 08-07 audit named this the *deepest* coupling: *"in-process `open_webui.*`
imports in `smolcrawl/deep_research/*`"*, listing `research.py`, `sub_agent.py`,
`domain_discovery.py`, `knowledge_research.py`, `rag_research.py`.

Those files do hold `open_webui.*` imports — but **none of them execute anywhere
in the live stack**:

- `openbrain-research` (the research engine) is a **Deno / TypeScript** service.
  Its `/app` contains `harness.ts`, `index.ts`, `injection.ts`, `kb.ts`,
  `lib.ts`, `deno.json`. There is no `python` on its PATH and no `research.py`
  or `rag_research.py` anywhere in the image.
- The OWUI-side artifact is **`deep_research` — a 10 KB thin HTTP client**
  (v1.1.0, "Thin OWUI client … ALL the harness logic lives server-side; this
  tool carries none of it"). It imports only `asyncio`, `json`, `aiohttp`,
  `pydantic`.
- The Python harness under `smolcrawl/deep_research/` was superseded by the
  TypeScript reimplementation and is now unreferenced legacy source.

**Effect on scope:** the Python harness needs **no upgrade work**. It also means
the audit's flagged concern — `research.py:406` reading the migrating
`request.app.state.config.WEB_SEARCH_ENGINE` path — is a non-issue: that line
never runs. (It is still worth deciding whether to retire the dead tree; see
Adjacent item **F**.)

> Both corrections **reduce** scope. Nothing found in this audit increases the
> risk over the 08-07 assessment.

---

## How OWUI is wired here (the facts this plan depends on)

- **Image:** custom GPU build, `Dockerfile.openwebui-gpu` → base
  `ghcr.io/open-webui/open-webui:v0.9.6` *(pre-cutover; now `v0.11.0`)*, plus a `sed` patch on
  `open_webui/tools/builtin.py` (UTC→local timezone, with a fail-loud build
  assertion) and a CUDA 12.1 PyTorch layer. `pull_policy: build`.
- **Live data:** named volume **`openwebui-data`** at `/app/backend/data`
  (**not** the stale `data/openwebui/` host dir).
  `webui.db` = **1.1 GB** (was 639 MB at the 0.9.6 upgrade); total data dir 40 GB.
- **DB:** SQLite (`aiosqlite`), single instance, no `DATABASE_URL`.
- **`WEBUI_SECRET_KEY`:** pinned in `.env`, referenced in compose. ✅ already safe.
- **Backups:** `openwebui-backup` sidecar, nightly. Latest verified:
  `openwebui-backup-20260820-020000.tar.gz` (18 GB) + `.sha256`.
- **Plugins:** 16 deploy-by-paste artifacts (6 tools + 10 functions), centralized
  in [`owui/`](../../../owui/). All active except the `code_agent` pipe
  (`is_active=0`).
- **Models:** 144 rows in the `model` table; 9 attach tools.
- **Ingress:** Caddy `openwebui.{$PUBLIC_DOMAIN}` subdomain behind
  `forward_auth authelia:9091` (`config/caddy/Caddyfile:143-180`), **and**
  tailscale serve at tailnet `:443/` → `:8080` (no Authelia on that lane).
- **Healthcheck:** `curl -f http://localhost:8080/health`.

---

## Verified against v0.11.0 source

Every claim below was checked against the actual v0.11.0 files, not the changelog.

| Check | Result |
|---|---|
| `tools/builtin.py` — Dockerfile `sed` targets | ✅ `now.isoformat()` ×4, `adjusted.isoformat()` ×2 — patch still applies |
| `utils/chat.py::generate_chat_completion` | ✅ present, `async def`, line 151 |
| `models/users.py::UserModel` | ✅ present, line 86 |
| `models/{files,groups,chats,notes,knowledge}.py` | ✅ all present; **no `core/` layout move** |
| Model methods (`Files.*`, `Groups.*`, `Chats.*`, `Notes.*`, `Knowledges.*`) | ✅ **still `async`** — June's async port holds, **no re-port needed** |
| `Knowledges.get_file_metadatas_by_id` | ✅ present (line 684) — `add_web_sources` primary path survives |
| `routers/retrieval.py::get_content_from_url` re-export | ✅ still importable from `routers.retrieval` (line 71) |
| `routers/retrieval.py::search_web` | ✅ `async def search_web(request, engine, query, user=None)` — **identical to 0.9.6** |
| `main.py` `/health` | ✅ present (line 2768) — compose healthcheck unchanged |
| `ENABLE_PLUGINS` (new in 0.11.0) | ✅ defaults `True` — our 16 plugins survive |
| `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` | ✅ still honored; default still 256, our pin of 40 still applies |

**Env-var sweep** — every var we set, checked against 0.11.0 `config.py`/`env.py`:
all present except `OLLAMA_HOST` / `USE_CUDA` (base-image vars, not OWUI config —
fine), `ENABLE_OPEN_TERMINAL` (already commented out), and
`AIOHTTP_CLIENT_TIMEOUT_TOOL_CALL` — see Adjacent item **C**.

---

## Breaking changes that actually affect this stack

Ranked by impact. Items the changelog lists but that don't apply are at the bottom.

### 1. Forward-only DB migration on a 1.1 GB SQLite DB (0.10.0, 0.11.0) — HIGH

> "This update contains database migrations. Please be sure to back up your
> database before updating, as downgrading after the migration is not supported."

The DB has grown **639 MB → 1.1 GB** since the last upgrade, so budget a longer
migration window than the 0.9.6 run. 0.11.0 also adds a large table set
(automations, skills, access_grants, chat_messages, oauth_sessions,
prompt_history, shared_chats, calendar…).

**Known trap, already sidestepped:** 0.10.0 and 0.10.1 shipped a SQLite
**user-table migration crash** that also corrupted saved user settings; 0.10.2
fixed it ("Upgrading an existing SQLite database no longer crashes during the
user-table migration or corrupts saved user settings"). Going **straight to
0.11.0** skips the broken intermediates. Never stage through 0.10.0/0.10.1.

**Action (before upgrade):**
- Rehearse on a copy first (§Pre-flight step 3) — this is the step that *proves*
  the trap is sidestepped and gives a real downtime number.
- Stop `openwebui` **and** the `tailscale` sidecar cleanly (shared netns).
- Take a **fresh** volume snapshot — do not rely on the nightly tar mid-window.

### 2. Native tool-calling is the default (0.10.0) — MEDIUM

> "Every chat and model that had not explicitly chosen a tool-calling mode now
> runs Native … the old behavior has been renamed 'Legacy' and made the explicit
> opt-out."

Measured blast radius against the live `model` table:

| | count |
|---|---|
| Total models | 144 |
| `function_calling = native` (explicit) | 10 |
| `function_calling` **UNSET** → flips to Native | 134 |
| Models that actually **attach tools** | **9** |
| …of those, already explicitly `native` | **7** |
| …of those, UNSET **and active** | **1** — `Code - Unity 6` |
| …of those, UNSET but inactive | 2 — `Analyze - Transcription`, `MCPO - create new tool` |

The 134 UNSET models overwhelmingly attach no tools, so the flip is inert for
them. **Real exposure is one active model.** And per Correction 1, Native
already works on this inference plane — so the expected outcome of the flip is
"it just works", not a regression.

**Action (after upgrade):** exercise `Code - Unity 6` with a tool; if it
misbehaves, set it to Legacy explicitly (per-model), not globally.

### 3. Full UI rebuild — asset serving across two ingress lanes (0.11.0) — MEDIUM

0.11.0 is "visually rebuilt from the ground up". Both ingress lanes serve OWUI at
a **root path** (Caddy subdomain `/`, tailscale serve `:443/`), which is the
safe configuration — the known-bad case is subpath deployment, which the
Caddyfile already documents and avoids. Risk is therefore low but non-zero
(new asset paths, changed cache headers).

**Action (after upgrade):** hard-reload both lanes. If assets 404 behind Caddy,
purge the Cloudflare cache — this stack has a documented history of CF caching
broken assets, and `:8444` style direct-port access bypasses CF for diagnosis.

### 4. Pyodide client-side Python now sandboxed (0.10.0) — N/A but confirm

> "Client-side Python now runs in a sandboxed iframe by default. Code accessing
> same-origin Open WebUI endpoints from Pyodide will no longer function."

We run `ENABLE_CODE_INTERPRETER=False` and `ENABLE_CODE_EXECUTION=False`
(deliberately — the Pyodide engine hangs with no timeout; see the compose
comment and `documentation/implementation-guide/Jupyter/`). **Non-issue**, but
do not flip these on during the upgrade.

### 5. Redirects blocked by default — `AIOHTTP_CLIENT_ALLOW_REDIRECTS` (0.9.5) — LOW

Carried forward from the 0.9.6 plan and still unset. Web search runs through the
SearXNG/Mullvad gateway; a redirecting hop fails.

**Action (after upgrade):** smoke-test web search; set
`AIOHTTP_CLIENT_ALLOW_REDIRECTS=true` only if it breaks.

### 6. `ENABLE_RAG_LOCAL_WEB_FETCH` → `ENABLE_LOCAL_WEB_FETCH` (0.10.0) — LOW

Old name still accepted as a deprecated alias. We set neither. No action;
noted so a future reader does not re-derive it.

### Not applicable here (explicitly cleared)

- **System events auto-fire webhooks (0.10.0):** no global webhook configured
  (`webhook_url` empty, `WEBHOOK_URL` unset). N/A.
- **PKCE for Google/Microsoft/GitHub sign-in (0.11.0):** no OAuth providers
  configured; single local operator account. N/A.
- **LDAP group sync (0.11.0):** no LDAP. N/A.
- **asyncpg → psycopg v3 (0.9.2):** Postgres-only; we are SQLite. N/A.
- **Auth settings page relocation (0.10.0):** cosmetic admin-UI move. N/A.
- **Container inventory:** no service added or removed → **no changes needed** to
  `emergency-recovery.ps1` / `.bat` or the stack-map reference doc. Verified.

---

## Adjacent stack work confirmed by this audit

These are real, independently-verified items that travel with the upgrade. They
are **not** OWUI version changes; they are drift and cruft the upgrade touches.

### A. Redeploy the `server_status` pipe — repo source is AHEAD of live — MEDIUM

Byte-exact comparison of all 16 plugins against live `webui.db` (CR-normalized
SHA-256) found **only one semantic drift**:

`owui/pipes/server_status.py` (OWUI id `ai_stack_unified_pipe_function`) — the
repo source carries an **LLM gateway panel** (LiteLLM · llm-queue: now
processing, queue depth, top requester, free slots, idle time) and covers **34
services**; the deployed copy lacks the panel and covers **32**. The deployed
snapshot predates `llm-gateway` + `llm-queue`.

**Action:** rebuild from `scripts/ai_pipes/` and redeploy. Do this **after** the
upgrade (it is a paste-deploy, and the post-upgrade redeploy step is already in
the checklist).

### B. `owui/` snapshot metadata is stale — LOW

- `owui/manifest.csv` byte counts are wrong for 2 rows: `deep_research`
  (says 8105, actual 10172) and `ai_stack_unified_pipe_function` (says 18863,
  actual 19088). Regenerate after redeploying.
- `owui/README.md`'s "Known deployment drift" section is **half stale**: it
  claims `tools/deep_research.py` deployed is behind the repo. It is not — the
  only difference is black line-wrapping of one `await emit(...)` call
  (semantically identical). The `server_status` half of that note is still
  accurate (item A).

**14 of 16 plugins are byte-identical to live.** The `owui/` folder is otherwise
trustworthy as the redeploy source.

### C. `AIOHTTP_CLIENT_TIMEOUT_TOOL_CALL` is a phantom env var — LOW, pre-existing

`docker-compose.yml` sets `AIOHTTP_CLIENT_TIMEOUT_TOOL_CALL=3600`. This var
**does not exist in 0.9.6 or 0.11.0** — grepping the live container's
`env.py` and v0.11.0's `env.py` finds no reader. It has been a **no-op the whole
time**; the long-tool-call protection it implies does not exist.

The real knob is **`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA`, default 10 s**,
which we do **not** set. That governs fetching tool-server specs — relevant to
`mcpo` / `lc-mcpo` MCP servers on a cold start.

**Action:** delete the phantom var; decide whether to raise
`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA` above 10 s for the MCP servers. Not
upgrade-blocking either way.

### D. ⚠️ Live third-party API keys sit in plaintext in `webui.db` config — SECURITY

The OWUI runtime config blob holds at least a live **OpenAI API key** and a live
**Google PSE API key** in cleartext. Any `webui.db` copy — including the
migration-rehearsal copy this plan calls for, and every nightly 18 GB backup tar
— carries them.

**Action:** treat rehearsal copies as secret material (keep them in the volume /
container, delete after). Rotation is a separate decision, consistent with the
existing key-hygiene posture in this workspace. **Not a blocker**, but do not
copy `webui.db` anywhere shared.

### E. `deep_research` valve default URL is unreachable — LOW

The deployed tool's `research_url` valve **default** is
`http://host.docker.internal:8818`, unreachable from inside a container; the
working value is `http://openbrain-research:8000`. Valves are stored separately
from `content`, so the live override survives a content redeploy — but a fresh
paste that also resets valves would break research.

**Action:** record the current valve value before any redeploy; re-assert after.

### F. Decide the fate of two dead trees — LOW, optional

- `smolcrawl/deep_research/*.py` — superseded by the Deno `openbrain-research`
  service (Correction 2). Nothing imports it in the live stack.
- `owui/pipes/code_agent.py` / `tools/code_agent_tools.py` — the `code_agent`
  pipe is deployed but `is_active=0`.

Neither affects the upgrade. Flagged so the post-upgrade redeploy step does not
silently resurrect them. **Do not delete anything as part of this upgrade** —
retirement is its own decision with its own justification.

---

## Execution checklist

**Pre-flight (fully reversible):**
1. ☐ Confirm `WEBUI_SECRET_KEY` is set in `.env` and resolves in compose
   (`docker compose config | grep WEBUI_SECRET_KEY`). Already pinned — verify, don't rotate.
2. ☐ Record current `deep_research` tool **valve** values (item E).
3. ☐ **Rehearse the migration on a copy** — `docker cp` `webui.db` out of the
   container to a scratch dir, run a throwaway 0.11.0 container against it,
   confirm Alembic completes and record wall-clock. Delete the copy afterward
   (item D — it contains live keys).
4. ☐ Patch `Dockerfile.openwebui-gpu` base tag → `v0.11.0`. The `sed` patch and
   its fail-loud assertion are already verified to apply — leave them as-is.
5. ☐ Remove the phantom `AIOHTTP_CLIENT_TIMEOUT_TOOL_CALL` from compose (item C).
6. ☐ Optionally pin `ENABLE_PLUGINS=true` explicitly (defaults true; pin to be
   immune to a future default change).
7. ☐ Build the 0.11.0 image **without** starting it; confirm the build assertion passed.

**Cutover (first irreversible step = 9):**
8. ☐ Stop `openwebui` **then** `tailscale` cleanly (shared netns — order matters).
9. ☐ Fresh volume snapshot:
   ```powershell
   docker run --rm -v ai-stack_openwebui-data:/d -v ${PWD}/backups/openwebui/manual:/out `
     alpine tar czf /out/openwebui-20260820-preupgrade-0110.tar.gz -C /d .
   ```
   plus a consistent `webui.db` copy via the SQLite backup API.
10. ☐ **Rebuild + start 0.11.0** — forward-only migration runs here.
11. ☐ Start `tailscale` after `openwebui` is healthy.

**Post-flight verification:**
12. ☐ `/api/version` returns `0.11.0`; container healthy; login works (secret-key continuity).
13. ☐ All 16 plugins load with 0 errors (Admin → Functions / Tools).
14. ☐ Tool calling: exercise `Code - Unity 6` (the one active UNSET model, §2) and
    one already-native model (e.g. `Research` → `deep_research`).
15. ☐ `deep_research` end-to-end against `openbrain-research`; valves intact (item E).
16. ☐ MCP tool servers re-discovered (`mcpo` / `lc-mcpo`).
17. ☐ Web search smoke test through the SearXNG/Mullvad gateway (§5).
18. ☐ Timezone rendering correct (the `builtin.py` patch held).
19. ☐ GPU inference + embeddings healthy via the `llama-cpp` / `llama-cpp-embed`
    aliases (**gateway plane only** — never probe `*-upstream` for this).
20. ☐ Both ingress lanes: Caddy subdomain (through Authelia) **and** tailscale
    serve — hard-reload, check assets (§3).
21. ☐ Redeploy the `server_status` pipe from `scripts/ai_pipes/` (item A), then
    regenerate `owui/manifest.csv` and correct `owui/README.md` (item B).

**Rollback:** stop `openwebui` + `tailscale` → restore
`openwebui-20260820-preupgrade-0110.tar.gz` into the volume → revert the
Dockerfile base tag to `v0.9.6` → rebuild → start. Plugin content needs no
rollback: the async port is 0.9.x/0.11.x-compatible in both directions (model
methods are async in both versions).

---

## Open questions for the operator

1. **Authelia SSO (trusted-header auth)** — greenlit by the 08-07 triage to kill
   the double login, *not* scoped into this upgrade. It carries a real security
   subtlety: the tailnet lane bypasses Authelia entirely, so a tailnet client
   could forge `Remote-Email` unless the header is scrubbed on that path.
   Recommend a separate effort doc.
2. **#7 async front door** — 0.11.0's assistant-notifications / chat timers /
   notification targets would let OWUI dispatch a long job and release the chat.
   High value, but a *feature* build on top of 0.11.0, not part of the upgrade.
3. **Dead-tree retirement** (item F) — separate decision, separate justification.

---

## EXECUTED — 2026-08-20 (SUCCESS, OWUI now on v0.11.0)

**Outcome:** upgrade complete. `{"version":"0.11.0"}`; `openwebui` + `tailscale`
healthy; **29/29 post-flight checks pass, 0 failures**; no data loss; every
neighbouring system verified intact.

### Timeline (UTC)

| Time | Event |
|---|---|
| 12:45:51 | `tailscale` stopped, then `openwebui` stopped — **downtime begins** |
| 12:46:06 | Full-volume `tar` started… |
| ~12:57:30 | …**ABORTED** (see deviation 1) |
| 12:57:49 | `docker compose up -d openwebui` — 0.11.0 boots, migration runs |
| ~12:59:50 | `openwebui` **healthy** (~13 min OWUI downtime) |
| 13:00:07 | `tailscale` force-recreated (openwebui had a new netns) |
| ~13:01 | All **8 tailnet serve routes restored** (~15 min total) |
| 13:03–13:04 | Second short restart to reload the redeployed `server_status` pipe |

### Pre-flight (all reversible, done before any stop)

- **Migration rehearsed on an isolated copy first.** Consistent `webui.db` copy
  (SQLite backup API, 6.5 s) → throwaway Docker volume → real
  `ghcr.io/open-webui/open-webui:v0.11.0` container on `--network none`.
  Result: **11 Alembic upgrades in ~31 s, `integrity ok`, all row counts
  identical.** The rehearsal volume was destroyed afterwards (it held live API
  keys — Adjacent item D). This is what made the live cutover a known quantity.
- Image built **before** stopping anything; the Dockerfile's fail-loud timezone
  assertion (step 6/6) **passed**, proving the `sed` patch applied to 0.11.0.
- `WEBUI_SECRET_KEY` continuity verified (pinned value == container value, before
  and after).

### Migration result (live)

Identical to the rehearsal — **`461111b60977` → `f0bd01a18a3d`**, 11 upgrades:
reshape config to per-key rows · add context summary to chat message ·
add memory type · add memory path and meta · add chat message meta ·
add current_message_id to chat · add chat variables · add user variables ·
add memory (id,user_id) covering index · add automation folder id ·
add unique normalized user email index.

**Data preserved — verified against the pre-migration baseline:**

| | before | after |
|---|---|---|
| chat | 926 | **926** |
| user | 1 | **1** |
| function | 10 | **10** |
| tool | 6 | **6** |
| model | 144 | **144** |
| `pragma integrity_check` | ok | **ok** |

All 8 new 0.11.0 tables created (`automation`, `skill`, `access_grant`,
`chat_message`, `oauth_session`, `shared_chat`, `prompt_history`,
`calendar_event`). All 16 plugin bodies intact. **Plugin valves byte-identical**
to the pre-upgrade dump — `deep_research.research_url` still
`http://openbrain-research:8000`.

### Deviations from plan

1. **The full-volume `tar` was aborted, deliberately.** It compressed at
   ~100 MB/min (3.7 GB after 11 min), projecting **2+ hours of downtime** for
   ~18 GB. That is not a defensible trade for redundant insurance, so it was
   killed and the partial file deleted. The rollback position actually held was:
   - `backups/openwebui/manual/webui-20260820-preupgrade-0110.db` (1.1 GB,
     consistent via SQLite backup API, `integrity ok`, alembic head
     `461111b60977`) + `.sha256` — **this covers the only thing the migration
     rewrites**; and
   - `backups/openwebui/openwebui-backup-20260820-020000.tar.gz` (18 GB, +sha256)
     — the nightly full-volume tar, covering `vector_db/`, `uploads/`, and the
     embedding cache, none of which the migration touches.

   **Follow-up worth having:** a full-volume snapshot of this dataset is no
   longer practical inside a maintenance window. Step 9 of the checklist above
   should be read as "consistent DB copy + confirm a recent nightly tar", not
   "fresh full tar". Backup throughput deserves its own look.
2. **Steps 5/6 (env changes) were applied as planned but expanded** — see below.

### Config changes made

- `Dockerfile.openwebui-gpu`: base tag `v0.9.6` → **`v0.11.0`**; the patch
  comment now records verification against v0.11.0 (4× `now.isoformat()`,
  2× `adjusted.isoformat()`).
- `docker-compose.yml`:
  - **Removed the phantom `AIOHTTP_CLIENT_TIMEOUT_TOOL_CALL=3600`** (Adjacent
    item C — no reader exists in 0.9.6 *or* 0.11.0; it never did anything) and
    replaced it with the **real** knob
    `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA=30` (upstream default 10 s, tight
    for a cold `mcpo` / `lc-mcpo`).
  - **Pinned `ENABLE_PLUGINS=true`** so a future upstream default flip cannot
    silently disable all 16 plugins.

### Adjacent stack work completed

- **`server_status` pipe redeployed** (Adjacent item A). Rebuilt from
  `scripts/ai_pipes/unified_openwebui_pipe.py`, written via
  `UPDATE function SET content=…`, reloaded by restart. Now carries the
  LiteLLM/llm-queue gateway panel and 34-service coverage (was 32, no panel).
  Pre-swap content was saved before the write.
- **`owui/` is now byte-identical to the live `webui.db` for all 16 plugins**
  (CR-normalized SHA-256, verified). `tools/deep_research.py` was re-exported
  from live; the previously recorded "drift" there was only black line-wrapping.
- **`owui/manifest.csv` regenerated** with true byte counts (2 rows were stale).
- **`owui/README.md`** drift section replaced with a sync-status section, the
  async-compatibility claim updated to 0.11.0, and the **netns ordering rule**
  documented at the redeploy instructions.

### Post-flight verification (29/29)

A verification harness was run **before** the cutover as a baseline (27/29 — the
only failures being the expected `version != 0.11.0`, and `mnemory` answering
`401`, i.e. reachable-but-authenticated) and again after. Post-cutover: **29/29**.

Beyond OWUI itself it explicitly proves the neighbours were **not** sacrificed:

- **Inference plane untouched** — the `llama-cpp` alias still resolves to
  LiteLLM; **native `tool_calls` still returned correctly**; embeddings still
  served via the `bge-m3-f16.gguf` gateway alias. No `--jinja` change was made
  (Correction 1).
- **All 8 tailnet serve routes restored**, matching the pre-cutover baseline
  exactly — including the **7 that have nothing to do with OWUI**
  (open_notebook UI + API, quartz wiki, llm-gateway-ui, mattermost, and the two
  inference aliases), plus their socat backends reachable through the rebuilt
  namespace.
- **OWUI's outbound deps** — `openbrain-research`, `mnemory`, and the SearXNG
  gateway all reachable.
- **Portal lane** — `caddy` running and reaching `openwebui:8080`. The Portal was
  live throughout; OWUI is internet-exposed on `openwebui.<domain>`.
- **3 MCP tool servers re-initialized**; zero errors or tracebacks in the boot log.
- `smolcrawl-pipelines`' REST use of OWUI's knowledge API was checked against
  v0.11.0 source *before* cutover: `GET /`, `POST /create`,
  `POST /{id}/file/add`, `POST /{id}/file/remove` all still exist with
  **identical response models** to 0.9.6.

### Tool-execution verification — resolved 2026-08-20 (operator, in the UI)

**Tools work on 0.11.0.** Operator ran a read-only batch across the main tool
categories in the Open WebUI interface, all green on first pass:

| Category | Tool | Result |
|---|---|---|
| Fileshed | `parameters`, `stats`, `tree` | ✅ (5.48 MB / 1000 MB quota) |
| Time | `current_timestamp` | ✅ America/New_York — **confirms the `builtin.py` TZ patch is live** |
| Open Brain (core) | `thought_stats` | ✅ 12,850 thoughts |
| Wiki | `list_pages` | ✅ |
| Notes | `search_notes` | ✅ |
| Calendar | `search_events` | ✅ (0 events, correct) |
| Automations | `list_automations` | ✅ 2 found |
| Job-hunt | `pipeline_overview` | ✅ |
| Recipes | `search_recipes` | ✅ |
| Household | `list_vendors` | ⚠️ 500 on first call, ✅ on retry — **not upgrade-related**, see below |

> **⚠️ GOTCHA — do NOT verify OWUI tool execution through `/api/chat/completions`.**
> During post-flight, driving that endpoint with an API key produced *narrated*
> tool calls (the model writing `**Calling:** get_repo_overview(...)` or
> `<function_calls><invoke …>` as prose) with **no `tool_calls` and no sources**,
> on both an explicitly-`native` model and an UNSET one. That looked like a
> Native-flip regression and was **a false alarm**: the OpenAI-compatible API
> path does not run OWUI's tool middleware the way the UI does. Plain generation
> on that path is a valid check; **tool execution is only meaningful in the UI.**
> Historical evidence had also pointed the wrong way here — 97 of 400 pre-upgrade
> chats carry stored tool `sources` and none carry narrated syntax, which
> made the API artifact look like a genuine behaviour change.

**On the `list_vendors` 500:** a stale DB session, not an upgrade effect and not
random flakiness. `openbrain-db` was restarted 6 days ago; `openbrain-ext` has
been up 2 weeks, so it still holds connections opened before that restart — the
first call after an idle period hits a dead session, the pool reconnects, the
retry succeeds. Same class as the known `openbrain-mcp` stale-connection issue
(`openbrain-mcp` *was* restarted with the DB; `openbrain-ext` was missed).
Clears with `docker restart openbrain-ext`.

**`deep_research` verified end-to-end 2026-08-20** (operator, in the UI). A live
job returned sourced findings with attribution, emitted `[GAP]` markers for
sub-topics it could not ground rather than fabricating, recorded the open gaps,
and cited only the source actually used — coverage reported honestly at 25%.
That is the grounded-only contract behaving exactly as designed, and it exercises
the deepest chain in the stack: **OWUI 0.11.0 → `deep_research` thin client →
`openbrain-research` (Deno) → LiteLLM → llm-queue → llama.cpp**.

**With that, every path is verified. No unexercised paths remain from this upgrade.**

### Not done (deliberately, still open)

- **No `--jinja` change** — proven unnecessary (Correction 1). The inference
  plane was not touched at all.
- Authelia SSO trusted-header auth (Open question 1) — separate effort.
- 0.11.0 async front door (Open question 2) — separate effort.
- Dead-tree retirement (Adjacent item F) — separate decision.
- **Adjacent item D (plaintext API keys in `webui.db`) is unresolved.** Rotation
  remains an operator decision; the rehearsal copy that held them was destroyed.

**Rollback (still available):** stop `openwebui` + `tailscale` → restore
`backups/openwebui/manual/webui-20260820-preupgrade-0110.db` over `webui.db` in
the `ai-stack_openwebui-data` volume → revert `Dockerfile.openwebui-gpu` base tag
to `v0.9.6` → rebuild → start `openwebui`, wait healthy, then start `tailscale`.
Plugin content needs no rollback (model methods are async in both versions).

---

## FOLLOW-UPS RESOLVED — 2026-08-20

Everything the EXECUTED record left open (except key rotation, deferred by the
operator) is now closed. Final state re-verified at **29/29**, `owui/` byte-identical
to live for all 16 plugins.

### 1. Backup throughput — FIXED (~50 min → 8.5 min)

**Correction to the EXECUTED record:** the deviation note projected "2+ hours" for a
full-volume tar. That was extrapolated from a 60-second sample and was wrong. The
backup log gives the true figure — **~50 minutes, consistently**, five nights running
(02:00 → ~02:50). Still far too long for a maintenance window, so aborting the
cutover tar was still the right call, but the number quoted was not.

Root cause was single-threaded `gzip` over a ~40 GB volume. Volume breakdown:

| Path | Size | Nature |
|---|---|---|
| `vector_db/` | **29.1 GB** | derived (Chroma; 22,896 collections, one 12.3 GB `chroma.sqlite3`) |
| `cache/` | 7.9 GB | **fully regenerable** (HF embedding/reranker models, whisper, audio) |
| `webui.db` | 1.1 GB | **irreplaceable** |
| `webui.db.backup_20260424_184325` | 605 MB | stale April copy, still in the live volume |
| `uploads/` + `user_files/` | ~0.5 GB | **irreplaceable** |

Only ~1.6 GB is genuinely irreplaceable. Two changes to
[`backup/openwebui-backup.sh`](../../../backup/openwebui-backup.sh):

- **`pigz -p 8` instead of `gzip`** — benchmarked **8× faster** on this data
  (466 MB sample: 16 s → 2 s). Output is a standard gzip stream, so `.tar.gz` and the
  restore path are unchanged. Falls back to `gzip` if pigz is absent, so a failed
  install can never break backups. Threads are capped at 8, not `nproc` (14),
  because the sidecar is deliberately memory-limited to 1 g and runs at 02:00
  alongside the OB1 scheduled slice.
- **`cache/` excluded** — 7.9 GB of regenerable model snapshots.
  *Restore note: first boot after a restore needs internet to re-pull the
  embedding/reranker models.*

`docker-compose.yml` installs pigz at sidecar start (best-effort, logged).

**Measured result:** `510s` (8.5 min), 17.7 GB → 11.9 GB. Archive verified with
`pigz -t` — full decompression valid, sha256 written.

> `vector_db/` was **deliberately left in**. It is derived data, but rebuilding means
> re-embedding thousands of files. It is also 72% of what remains — if OWUI RAG is
> ever formally retired (newest knowledge collection is dated 2026-05-14), excluding
> it would take the nightly to roughly a minute. **Operator decision, not taken here.**

### 2. `openbrain-ext` stale DB session — FIXED

`docker restart openbrain-ext`. It had been up 2 weeks holding connections opened
before `openbrain-db` restarted 6 days ago, so the first extension call after an idle
period hit a dead session (`list_vendors` → 500, fine on retry). `openbrain-mcp` had
been restarted with the DB; `openbrain-ext` was missed. Extension spec now answers
200 on a cold first call.

### 3. OWUI config cleanups — DONE

Applied with `openwebui` **stopped**, so its in-memory config could not clobber the
writes. All four in one restart window:

- **Two dead connections removed** — `http://169.254.83.107:5506/v1` and
  `http://host.docker.internal:5506/v1` (stale LM Studio; both connection-refused).
  Remaining: `api.openai.com`, `host.docker.internal:9099` (Pipelines),
  `llama-cpp:8080`, `llama-cpp-embed:8080`.
- **Duplicate tool server removed** — `host.docker.internal:8000` was registered
  twice, differing only by `path`.
- **`deep_research` stale default fixed** — in-code default
  `host.docker.internal:8818` → `http://openbrain-research:8000`. The stored valve
  (already correct) was verified unchanged afterwards. The dormant landmine is gone:
  a valve reset no longer breaks research.
- **`code_agent` repointed** — `MODEL_ID` `Qwen3.6-35B-A3B-Q4_K_M.gguf` (removed from
  the gateway 2026-06-12) → `qwen36-27b`. Still `is_active=0`; this only means
  enabling it would no longer fail instantly.

> **⚠️ A regression was introduced and fixed during this step — worth reading.**
> De-duplicating the tool servers dropped `Initialized 3 tool server(s)` to **2**.
> The two duplicate entries were *not* equivalent: the one kept (first in list order)
> carried `config.enable: false`, and the one removed was the **enabled** one. Both
> URLs and both `path` forms resolved 200, so probing the endpoint could not have
> caught it — only the init count did. Fixed by setting `config.enable: true` on the
> surviving entry; back to **3**. **Lesson: when de-duplicating OWUI tool servers,
> dedupe on `url` but merge on `config.enable` — never keep the first blindly.**

**Separate finding, not acted on:** that `host.docker.internal:8000` registration has
**stale metadata**. Its stored `info.name` is `nlp-microservice` (spaCy), but the
endpoint now actually serves `Local LAN STT→LM Studio→TTS v2.1.0`. The registration
still works, but the tools it exposes are not what the entry claims. Worth
re-registering or removing.

### Still open (operator decisions)

- **Plaintext API keys in `webui.db`** — deferred by the operator.
- **`webui.db.backup_20260424_184325` (605 MB)** — a four-month-old stale copy inside
  the live volume, backed up nightly. Deliberately **not** deleted: removing files is
  the operator's call. Deleting it from the volume is the clean fix (it then leaves
  the backups naturally); excluding it from the tar instead would silently stop
  protecting it.
- **`vector_db/` (29.1 GB, 72% of the backup)** — see above.
