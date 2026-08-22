# J.1 — per-caller identity through the gateway (virtual-keys cutover)

> Status: **EXECUTED 2026-08-21** (operator present). Junk keys 401; virtual
> keys 200; llm-queue attributes lanes via `x-ai-stack-caller`. Rollback
> stays as documented below (comment out master_key).

## Why

`llm-queue`'s per-caller priority lanes (`llm-queue/src/llm_queue/policy.py:64-81`)
are structurally defeated: LiteLLM rebuilds each upstream request and sends
`Authorization: Bearer dummy` (`config/litellm.config.yaml` B2/P2 note —
**verified 2026-06-14**, including that `forward_client_headers_to_llm_api`
does NOT forward Authorization on the openai-client path). Today, lane
assignment only survives for callers that volunteer the OpenAI `user` body
field. Full fix = LiteLLM `master_key` + per-caller virtual keys (the config's
own §10.3.2 pointer): real authn at the gateway, real spend attribution, and
a caller identity llm-queue can trust.

## Phase A — EXECUTED 2026-08-21 (throwaway rig: LiteLLM main-stable + header-echo upstream + scratch postgres)

**Findings (all verified live):**

1. ❌ `user` body field is now STRIPPED upstream by this LiteLLM build (the
   2026-06-14 note that it forwards is obsolete) — llm-queue's `user`
   fallback is dead through the gateway.
2. ❌ No `x-litellm-*` attribution headers reach the upstream.
3. ❌ Arbitrary custom key strings rejected — but ✅ **`sk-` prefixed custom
   strings ARE accepted** (`/key/generate {"key":"sk-<name>"}`) →
   deterministic memorable keys per caller.
4. ✅ **THE mechanism: a pre-call hook** (`config/litellm/custom_callbacks.py`,
   `LaneHeaderInjector`) reads the virtual key's `metadata.lane` (falling
   back to `key_alias`) and injects **`x-ai-stack-caller: <lane>`** upstream
   — verified arriving at the upstream alongside `Bearer dummy`.
5. ✅ Near-zero-downtime sequencing: **`llm-gateway-ui` is master-key'd
   against the SAME `llm-gateway-db`** — pre-generate all keys through it,
   re-key callers while the main gateway is still permissive (any string
   accepted), flip `master_key` on the main gateway LAST.

**llm-queue change:** read `x-ai-stack-caller` as the primary lane id in
`routes/data.py` (before the Authorization/`user` fallbacks) + a test.

## Phase B — enable master_key (one line + one env)

- `.env`: set `LITELLM_MASTER_KEY` (generate: 43+ chars; the staged-secrets
  guard's entropy rules don't scan `.env` — it's gitignored — but never let it
  near a tracked file).
- `config/litellm.config.yaml` `general_settings`:
  `master_key: os.environ/LITELLM_MASTER_KEY`.
- `docker compose up -d llm-gateway`. From this moment every caller without a
  valid key gets 401 — do this only after Phase C keys exist (or Phase A.3
  proved same-string keys work).

## Phase C — caller inventory (who must carry a key)

| Caller | Lane today (policy.py) | Key configured where |
|---|---|---|
| Open WebUI main connection | `owui-chat` (prio 0) | Admin → Connections in `webui.db` (also listed in update-owui-to-0-11-0 UPGRADE-PLAN §connections) |
| Open WebUI **embed** connection + `rag.openai.api_key` | `owui-chat` (same key) | **MISSED at cutover — found 2026-08-21 PM** ("error when prompting": `/openai/models/3` 500 on the keyless `llama-cpp-embed:8080/v1` connection, and RAG embeddings 401'd silently). Both set in `webui.db` config (`openai.api_keys[3]`, `rag.openai.api_key`) + frontend restart. LESSON: OWUI holds MULTIPLE gateway credentials — connections list AND the RAG embedding config; audit all of them on any auth change. |
| `githelper` pipe | default | valve in `webui.db` (base `llama-cpp:8080/v1`) |
| `mnemory` | `ollama` (prio 1) | `compose/memory.yml` `LLM_API_KEY` (rename the string to `mnemory` while touching it) |
| `smolcrawl-pipelines` | — | (VALIDATED 2026-08-21: does NOT call the gateway — its LLM work goes through OWUI; no key needed) |
| `open_notebook` | — | model config in its SurrealDB, **encrypted with `OPEN_NOTEBOOK_ENCRYPTION_KEY`** — update via its UI, not the DB |
| `little-coder` / workers | `lc-coder` (prio 2) | `little-coder/config/little-coder.config.yaml` + compose env (recreate ao-workers after — they keep stale env, see `ao-worker-stale-deploy-token` memory) |
| OB1 services (research, entity/suggestion/chunk workers, digest, podcast, wiki, workbench, mcp, extract) | `ob-mcp`/`ob-entity`/`ob-wiki`/`ob-research`/`ob-podcast` | `OB1/docker/.env` + compose env (one shared var per lane) |
| agent-org bridge (advisory/research) | — | `agent-org/docker/.env` |
| `openbrain-gmail-pull` (nightly 05:00 cron) | `ob-gmail` | **MISSED at cutover — found 2026-08-22 by the Part M autonomous planner (issue #26)**: `OB1/recipes/email-history-import/pull-gmail.ts` sends `Bearer $OPENROUTER_API_KEY`, which the gitignored recipe `.env` set to the literal `local-trust` — nightly ingestion of NEW emails 401'd silently (metadata chat + embeddings both dropped) from the flip until 08-22. Key alias `ob-gmail` now in the recipe `.env`; container recreated + authed embed verified in-container. LESSON: gitignored per-recipe `.env`s are invisible to tracked-file audits — `grep --no-ignore` the whole tree for gateway URLs/keys on any auth change. |
| Embeddings callers (same services) | routed by model to embed upstream | same keys ride along |

## Phase D — flip order (minimizes blast radius)

1. Phase A test key proven end-to-end (completion + embedding).
2. Issue all keys (`/key/generate`, alias per caller; store in the respective
   `.env`s only).
3. Update caller configs (table above), **restart/recreate each consumer**.
4. Enable master_key (Phase B). Watch `llm-gateway` logs + spend table for
   401s — every 401 names a caller you missed.
5. `llm-queue`: confirm lanes attribute (GET `/observe/queue/stats` via the
   gateway pass-through; or the events DB) under real traffic.
6. Update `SECURITY.md` (the permissive-gateway note dies) and
   `config/litellm.config.yaml`'s header comment.

## Rollback

Comment out `master_key` in the config + `up -d llm-gateway` — callers'
extra keys are ignored in permissive mode; nothing else needs reverting.

## Renames to fold in while touching callers

- `LLM_API_KEY=ollama` (mnemory) → `mnemory` — the string is a caller-id, and
  `ollama` is a retired backend's name squatting in an identity field.
- `stack-watchdog.ps1` `$ExpectedTailscaleServes` labels
  `llama-cpp-upstream`/`llama-cpp-embed-upstream` → they probe the gateway
  ALIASES; rename labels `llama-cpp`/`llama-cpp-embed` (found during D.2).
