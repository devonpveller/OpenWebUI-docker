# Phase 6 — Wiring + Smoke Handoff

Phases 0–5 are complete and live-verified, fully local. Phase 6 has two
kinds of step: things already done for you (the routing skill, infra
smokes) and things only you can do (client config reload/approve, the
conversational smokes, OWUI tool-server registration). This is the
handoff for the latter.

## 1. Routing skill — DONE

Written verbatim from the spec to:
`C:\Users\yamao\.claude\skills\memory-stack-routing\SKILL.md`
No action needed; Claude Code picks it up on next session.

## 2. `.mcp.json` (Claude Code / Claude Desktop) — YOUR STEP

The agent is not allowed to edit its own startup MCP config. Paste the
two new entries below into `d:\Open WebUI\ai-stack\.mcp.json` (keep your
existing `mnemory` entry), then reload MCP servers and approve them.

Door mapping (decision **D17**): the six `wiki_*` tools physically live
in the `openbrain-ext` server (8809) alongside the life-app extension
tools — there is no separate wiki MCP server. Lane separation is
enforced by the routing skill + system prompts (wiki_* = synthesis
lane), not by a server boundary. A dedicated wiki-only MCP door can be
split out later if you want stricter isolation; functionally unnecessary.

```jsonc
// merge into "mcpServers" in d:\Open WebUI\ai-stack\.mcp.json
"openbrain": {
  "type": "http",
  "url": "http://127.0.0.1:8808/",
  "headers": { "x-brain-key": "<MCP_ACCESS_KEY from OB1/docker/.env>" },
  "description": "Structured personal records and external source documents: captured thoughts, projects, ingested papers/articles/transcripts (ingest_url/ingest_urls). Look up or add a specific record or source. Authoritative over the wiki."
},
"openbrain-ext": {
  "type": "http",
  "url": "http://127.0.0.1:8809/",
  "headers": { "x-brain-key": "<MCP_ACCESS_KEY from OB1/docker/.env>" },
  "description": "Life-app records (household, meals, contacts, maintenance, job-hunt) AND the compiled-wiki synthesis lane: wiki_search, wiki_read_page, wiki_get_backlinks, wiki_get_related, wiki_list_pages, wiki_trigger_recompile. Prefer wiki_* (compiled pages) for synthesis questions; fall back to openbrain if the wiki is insufficient."
}
```

`MCP_ACCESS_KEY` value is in `OB1/docker/.env` (gitignored). No new
secret needed — `.mcp.json` already holds your mnemory bearer.

## 3. Open WebUI tool servers — YOUR STEP (OWUI admin UI)

OWUI consumes the mcpo OpenAPI bridges, which are already running on the
shared `ai-stack_llm-net` (in-network only — no host port, by design).
In **OWUI → Settings → Tools (or Admin → Tool Servers)** add two
OpenAPI servers (OWUI and the bridges share `ai-stack_llm-net`, so use
the container names):

| Name             | URL                                              | Auth (Bearer) |
|------------------|--------------------------------------------------|---------------|
| open-brain       | `http://openbrain-mcpo:8000/open-brain`          | `MCPO_API_KEY` |
| open-brain-ext   | `http://openbrain-mcpo-ext:8000/open-brain-extensions` | `MCPO_API_KEY` |

`MCPO_API_KEY` is in `OB1/docker/.env`. The `wiki_*` tools appear under
**open-brain-ext** (verified: all six register in its `openapi.json`).
The general/research system prompts (already updated for the 3-layer
model) drive lane selection.

## 4. Smoke tests

**Infra-level (done by the agent — see INTEGRATION-TASKS findings):**
- Smoke 2 (records): thought captured → retrieved via core MCP. ✅
- Smoke 3 (source ingest): `ingest_url` → row in `sources`, semantically
  retrievable. ✅ (B.2 path)
- Smoke 4 (wiki): `wiki_search`/`wiki_read_page` return compiled
  synthesis with `[S:id]`/`[#id]` provenance. ✅ (B.4)

**Conversational (YOUR STEP — require a live OWUI/Claude session):**
1. **mnemory:** say "Remember I prefer Python over JavaScript for new
   projects." → confirm it appears in mnemory's management UI.
2. **OB1 records:** capture a thought via chat, ask the agent to recall
   it → comes back via `openbrain`.
3. **OB1 source:** `ingest_url` a page in chat, ask a fact only in that
   page → answered via `openbrain`.
4. **Wiki:** ask a topic-synthesis question → answered from `wiki_*`
   with compiled-page references.
5. **Routing:** three back-to-back questions, one per lane → correct
   lane 3/3.

If any conversational smoke fails, note which and we adjust the routing
skill / system prompt (no schema or service changes expected).

## 4b. Operational rule (finding F12)

`openbrain-mcp` and `openbrain-ext` connect to Postgres with a direct
pooled connection. If `openbrain-db` is ever restarted/recreated (e.g.
`docker compose up -d` that brings up new services depending on it),
those pools go stale and every DB op fails with
`Broken pipe (os error 32)`. **Fix:** restart the DB-direct services
after the DB:

```
docker compose -f OB1/docker/docker-compose.yml restart openbrain-mcp openbrain-ext
```

PostgREST-based services (entity-worker, wiki-service) self-heal and
need no restart.

## 5. Phase 7 — GATED, do not run unprompted

Open Notebook + surrealdb stay UP. Teardown is yours to execute only
after you accept the replacement proof (see INTEGRATION-TASKS Phase 7).
The agent will prepare exact teardown commands when you ask; it will not
run irreversible deletions.
