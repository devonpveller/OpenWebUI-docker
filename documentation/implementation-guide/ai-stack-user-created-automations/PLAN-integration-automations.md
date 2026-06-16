# Integration Plan — ai-stack User-Created Automations (v1)

> **Status:** PLAN — not built. Grounded in live source/config (ports, endpoints,
> and the tailscale-serve pattern are quoted from the real files as of 2026-06-13).
> **Companion docs:** [CONCEPT](CONCEPT-ai-stack-user-created-automations.md) (the idea + feasibility),
> [TASKS](TASKS-integration-automations.md) (the build checklist).

---

## 1. Locked decisions (operator, 2026-06-13)

| Decision | Choice | Consequence for this plan |
|----------|--------|---------------------------|
| Deployment | **Fold into main stack** | `n8n` + `n8n-db` added to the main `docker-compose.yml`; managed by plain `docker compose`. |
| Audience / exposure | **Single-user, tailnet-first** | v1 is tailnet-only (tailscale serve). **No** cloudflared/Authelia, **no** privileged-sink auth boundary yet. |
| UI platform | **Adopt n8n** (operator said "fork/adapt n8n editor") | See §2 — grounded recommendation is *run full n8n + custom nodes*, **not** fork the editor. |
| v1 node scope | **Research fan-out only** | Research node → 3 Format nodes (OWUI / Open Notebook / Podcast) + manual trigger. No search/memory/LLM/email/coder nodes in v1. |

---

## 2. Headline architecture decision: n8n *as platform*, not a forked editor

The concept doc proposed a bespoke `automations-engine` + `automations-ui`. **That
is now superseded for v1.** Grounded finding (n8n docs, verified):

- The official n8n Docker image (`docker.n8n.io/n8nio/n8n`) ships the **execution
  engine *and* the node editor together**. It also provides Postgres persistence,
  Manual/Schedule/Webhook triggers, an HTTP Request node (custom headers, JSON),
  and **Do-While / Wait nodes for async polling** — i.e. *everything* the bespoke
  engine was going to be.
- **Forking only the editor** (the literal option chosen) would mean re-pointing
  it at a backend we'd have to *build* — re-implementing scheduling, credential
  storage, run history, and the executor. That throws away n8n's single biggest
  asset.

**Recommendation (please confirm):** run **full n8n** and express ai-stack
capabilities as **n8n nodes**, starting with n8n's *built-in HTTP Request node*
(zero custom code for v1), then optionally packaging thin **custom nodes**
(`N8N_CUSTOM_EXTENSIONS`, ~50–100 lines TS each) for nicer UX once the flows work.

This honors the n8n direction, eliminates a whole service we'd otherwise build,
and is dramatically less work. The rest of this plan assumes the platform
approach. *(If you specifically want the forked-editor path, say so — it roughly
triples v1 effort and re-introduces `automations-engine`; not recommended.)*

> **Licensing:** n8n is under the Sustainable Use License (fair-code).
> Self-hosting for internal/personal use is permitted free of charge. Embedding
> n8n *as a feature in a product you sell* is the only restricted case — not us.

---

## 3. Target architecture (v1, folded into main stack)

```
   Tailnet device ──HTTPS :8446──▶ tailscale serve ──socat :8241──▶ n8n:5678
                                                                      │
   ┌──────────────────────────────────────────────────────────────┐ │
   │  n8n  (engine + editor + triggers)         n8n-db (Postgres)  │◀┘
   │   workflow: "Research fan-out"                                 │
   └───┬───────────────┬───────────────────────┬──────────────────┘
       │ HTTP          │ HTTP                   │ HTTP
       ▼               ▼                        ▼
 openbrain-research  open_notebook         open_notebook
 POST /research      /api/sources          /api/podcasts/generate
 (poll job)          (Format→ON)           (Format→Podcast)
 [obnet seam]                              + OWUI delivery (see §5.2 gap)
```

Two new containers only: **`n8n`** and **`n8n-db`**. Everything they call already
runs. No `automations-ui` / `automations-engine` / `automations-db` (the concept's
trio collapses into n8n + its DB).

---

## 4. Network & reachability (the cross-stack seam)

This is the **one non-obvious wiring problem**, because the flagship target lives
in a *different compose project*:

- **`openbrain-research` is an OB1 (`open-brain`) container**, on networks
  `obnet`, `llm-net`, `search-gw-net` — published to host at `127.0.0.1:8818:8000`.
  It is **not** on the main stack's default networks.
- For n8n (main stack) to reach it **by container name**, n8n must join OB1's
  network as an **external** network — the documented precedent is `open_notebook`
  and `openbrain-db-backup`, which already join `open-brain_obnet`:

```yaml
# main docker-compose.yml — n8n network attachments
networks:
  obnet:
    external: true
    name: open-brain_obnet      # reach openbrain-research:8000
  # plus a main-stack net to reach open_notebook (it's on llm-net/app-net/default)
```

- **`open_notebook` is a main-stack container** (API on `open_notebook:5055`,
  published `127.0.0.1:5055`). n8n reaches it over a shared main-stack network
  (e.g. `default` or `app-net`).

**Reachability matrix for v1:**

| n8n must call | Host:port (by container name) | Network n8n needs |
|---------------|-------------------------------|-------------------|
| Research submit/poll | `openbrain-research:8000` | `open-brain_obnet` (external) |
| Open Notebook (ON + Podcast) | `open_notebook:5055` | shared main net (`default`/`app-net`) |
| OWUI (Format→OWUI, see §5.2) | `openwebui:8080` | shared main net |

> **Bring-up order:** n8n depends on OB1 being up (for the research seam). Since
> OB1 is a separate project started *after* the main stack, n8n must tolerate
> `openbrain-research` being absent at boot (a workflow simply errors until OB1
> is healthy). Don't add a hard `depends_on` across projects — it can't span
> compose projects anyway.

---

## 5. The v1 automation: "Research fan-out" — exact contracts

A single n8n workflow: **Manual trigger → Research → {Format→OWUI, Format→ON,
Format→Podcast}**. All three Format nodes consume the *same* research result.

### 5.1 Research node (HTTP Request + poll)

**Submit** (HTTP Request node):
```
POST http://openbrain-research:8000/research
Headers:  x-brain-key: {{ $env.MCP_ACCESS_KEY }}
Body (JSON):
  {
    "query": "{{ $json.prompt }}",
    "origin": "automation",            // 'owui'|'agent'|'notebook'|'manual' — add 'automation' upstream or use 'manual'
    "options": { "confidence_floor": 0.5 }
  }
→ 202 { "job_id": "...", "status": "queued" }
```
**Poll** (Do-While / Wait loop) until terminal:
```
GET http://openbrain-research:8000/research/jobs/{{ $json.job_id }}
Headers:  x-brain-key: {{ $env.MCP_ACCESS_KEY }}
→ { id, status, progress, result, metrics, error }
   stop when status ∈ { done, error, cancelled }
```
**Result fields available downstream** (verified from `harness.ts` / `index.ts`):
`result.synthesis` (tagged `[SOURCED]/[INFERRED]/[UNCERTAIN]/[GAP]`),
`result.prose` (human markdown w/ `[Source N]`), `result.cited_sources[{url,title}]`,
`result.gaps[]`, `result.curator.thread_id`, `result.backstop`, `result.fetch_stats`.

> `origin` currently validates to `owui|agent|notebook|manual`. Use `"manual"` for
> v1, or add an `"automation"` enum value to the research service (1-line change)
> so runs are attributable in `research_jobs`.

### 5.2 Format → OWUI chat ⚠️ (the real gap)

**Gap found:** the existing `deep_research_thin_client.py` delivers results by
**returning a string into a live OWUI chat turn** via `__event_emitter__`. That
mechanism only works **inside** an OWUI tool call — an n8n workflow runs *outside*
OWUI and has no live turn to return into. There is **no code in the repo** that
creates an OWUI chat programmatically.

So "Format → OWUI chat" from an automation requires one of:
- **(a)** Call OWUI's REST API to create a chat and insert the synthesis as a
  message (OWUI 0.9.x exposes `/api/v1/chats/...` + an API-key auth). **Feasible
  but unverified in this repo — must be spiked** before promising it.
- **(b)** Deliver to OWUI as a *Knowledge* entry instead of a chat
  (`/api/v1/knowledge/...`) — different UX, also unverified.
- **(c)** Drop the OWUI sink from v1 and deliver to Open Notebook only (ON is the
  cleaner, verified write path — §5.3).

**Plan:** P0 ships **Format→ON** (verified). The OWUI sink is a **P1 spike**
(option a), demoted from "READY" to "ADAPTER — needs verification." This corrects
the concept doc's optimistic READY rating for the OWUI format.

### 5.3 Format → Open Notebook (verified write path)

ON API at `open_notebook:5055`. Add the synthesis as a source/note:
```
POST http://open_notebook:5055/api/sources          (create source)
POST http://open_notebook:5055/api/notebooks/{id}/sources/{srcId}   (link to notebook/thread)
```
(The IKS integration uses exactly these routes; `link_source_to_thread(...)` is
the underlying op.) v1: create one source carrying `result.prose`, link it to a
designated "Automations" notebook.

### 5.4 Format → Podcast (verified — bypass openbrain-podcast)

**Do NOT** call `openbrain-podcast POST /run` — that spawns the *entire*
gmail-driven digest subprocess and assembles its own segments from the day's
emails. It cannot take an arbitrary synthesis.

**Instead, call Open Notebook's podcast API directly** (the same calls
`on-client.ts` makes):
```
POST http://open_notebook:5055/api/podcasts/generate
Body: {
  episode_profile: "tech_discussion",
  speaker_profile: "tech_experts",
  episode_name: "auto-{{ runId }}",
  content: "{{ rendered script from result.synthesis }}",
  briefing_suffix: "<grounding framing>"
}
→ { job_id }
GET /api/podcasts/jobs/{job_id}            (poll until status done)
GET /api/podcasts/episodes                 (find by name → audio_url)
GET /api/podcasts/episodes/{id}/audio      (the mp3)
```
**Script rendering:** the digest's `script-renderer.ts` takes
`EpisodeInput{ segments:[{ label, items:[{title,url,synthesis}] }] }`. For a
single research result we wrap it as one segment with one item — a small,
self-contained render step (n8n Function/Code node, or a thin custom node).
This is the **one piece of genuinely new glue** in v1 (the "podcast decouple"
flagged in CONCEPT §9.4), and it's small because we reuse ON's renderer/TTS.

---

## 6. Tailnet exposure (tailscale serve)

n8n listens on **:5678**. Follow the exact open-notebook pattern in
`entrypoint.sh` (Streamlit-style: served at **root on a distinct TS port**, via a
socat proxy, with deferred setup + monitoring-loop self-heal).

**Allocated ports (next free, verified against entrypoint.sh):**
- socat local port: **8241** (8234–8240 already used)
- tailnet HTTPS port: **8446** (443, 8443, 5055, 8444, 8445 already used)

**Add to the `tailscale` service env in docker-compose.yml:**
```yaml
- N8N_HOST_INTERNAL=n8n
- N8N_PORT_INTERNAL=5678
- N8N_TS_PORT=8446
- N8N_LOCAL_PORT=8241
- N8N_SERVE_ENABLED=true
```
**Add to `entrypoint.sh`:** a `setup_n8n_serve()` function (copy
`setup_open_notebook_api_serve`, lines ~390–417: pkill old socat → start
`socat … TCP-LISTEN:8241 … TCP:n8n:5678` → `tailscale serve --https=8446 --bg
http://127.0.0.1:8241` → touch flag), the boot-time health-gated call, and a
deferred-setup + socat-health block in the monitoring loop (lines ~696–725).

**n8n must know its external URL** (so the editor + any webhooks build correct
links). Set on the `n8n` service:
```yaml
- N8N_HOST=<tailnet-hostname>           # e.g. openwebui.<tailnet>.ts.net
- N8N_PROTOCOL=https
- N8N_PORT=8446                          # the public-facing TS port
- WEBHOOK_URL=https://<tailnet-hostname>:8446/
- N8N_EDITOR_BASE_URL=https://<tailnet-hostname>:8446/
```
**Auth:** n8n's built-in owner account (email+password) is sufficient for a
single user on the tailnet. No Authelia in v1.

---

## 7. Compose integration (services to add)

```yaml
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    environment:
      - DB_TYPE=postgresdb
      - DB_POSTGRESDB_HOST=n8n-db
      - DB_POSTGRESDB_DATABASE=n8n
      - DB_POSTGRESDB_USER=n8n
      - DB_POSTGRESDB_PASSWORD=${N8N_DB_PASSWORD}
      - N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}     # PIN in .env (credential vault key)
      - N8N_HOST=${N8N_HOST}
      - N8N_PROTOCOL=https
      - N8N_PORT=8446
      - WEBHOOK_URL=${N8N_WEBHOOK_URL}
      - MCP_ACCESS_KEY=${MCP_ACCESS_KEY}             # for the research x-brain-key
    volumes:
      - n8n-data:/home/node/.n8n                     # workflows + custom nodes
    ports:
      - "127.0.0.1:5678:5678"                        # loopback only; tailscale/socat is the proxy
    networks:
      - default            # reach open_notebook, openwebui
      - obnet              # reach openbrain-research (external: open-brain_obnet)
    depends_on:
      - n8n-db

  n8n-db:
    image: postgres:16
    container_name: n8n-db
    restart: unless-stopped
    environment:
      - POSTGRES_DB=n8n
      - POSTGRES_USER=n8n
      - POSTGRES_PASSWORD=${N8N_DB_PASSWORD}
    volumes:
      - n8n-db-data:/var/lib/postgresql/data
    networks:
      - default
```
Plus: `obnet` external network block (§4), `n8n-data` + `n8n-db-data` volumes,
and new `.env` keys: `N8N_DB_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_HOST`,
`N8N_WEBHOOK_URL`. **Pin `N8N_ENCRYPTION_KEY` in `.env`** (like `WEBUI_SECRET_KEY`)
— if it rotates on recreate, stored credentials become undecryptable.

---

## 8. Stack discipline (mandatory — the "three places" rule)

Adding `n8n` + `n8n-db` means updating, together:
1. **compose** — `docker-compose.yml` (services, networks, volumes).
2. **recovery scripts** — `scripts/emergency-recovery.ps1` **and** `.bat`: add both
   containers to the service inventory and the shutdown/startup sequences.
3. **stack-map** — run `/stack-map` and update
   `.claude/skills/stack-map/references/workspace-stacks.md`.
4. **backups** — `n8n-db` is stateful: add an `n8n-db-backup` cron sidecar
   following the existing `*-backup` convention (workflows live in the DB).

---

## 9. Phasing

- **P0 — Tailnet spike (the proof):**
  n8n + n8n-db in compose; tailnet serve on :8446; one workflow:
  **Manual → Research → Format→ON**. Proves the research async contract + a
  verified sink end-to-end, all on the tailnet. Uses **built-in HTTP Request +
  Do-While nodes only** — zero custom code.
- **P1 — Fan-out:**
  Add **Format→Podcast** (the ON podcast-API render path, §5.4) and **spike
  Format→OWUI** (§5.2 option a — verify OWUI REST chat creation). Goal: one
  research result → up to three sinks (the CONCEPT §6 graph).
- **P2 (future, out of v1 scope):** read-palette nodes (search/OB/memory/LLM),
  schedule trigger, then — only behind the §9 auth boundary — cloudflared exposure
  and privileged sinks (email/wiki/little-coder). Tracked in CONCEPT §10.

---

## 10. Risks & open items (v1)

1. **OWUI sink is unverified** (§5.2). Biggest correctness risk; do not promise it
   until the REST-create-chat spike passes. P0 deliberately routes around it.
2. **Cross-project network seam** (§4). n8n joining `open-brain_obnet` couples the
   main stack to OB1's network name; if OB1 is down, research nodes error (acceptable,
   but the workflow must fail gracefully, not hang).
3. **Encryption-key pinning** (§7). Same failure mode as the historical
   `WEBUI_SECRET_KEY` rotation bug — pin it in `.env` from day one.
4. **Inference routing** (future nodes only). Any LLM node must hit
   `llm-gateway`/`llama-cpp`, never `*-upstream`; `check-llm-gateway-routing.ps1`
   applies. Not in v1, but note before P2.
5. **Recovery drift** (§8). Two containers + a backup sidecar across two scripts —
   easy to half-do. The `/stack-map` skill check catches it.
6. **n8n scheduling jitter** (~1–2 min under load) — irrelevant for v1 (manual
   trigger), note for P2 schedule nodes.

**Open items to confirm with operator:**
- (a) Confirm **platform-not-fork** (§2). Default assumption: platform.
- (b) OK to **add `obnet` external attach** to the main stack for n8n? (Precedent
  exists; it's the only cross-project coupling.)
- (c) For v1, is **Open Notebook the acceptable primary sink** if the OWUI-chat
  spike (§5.2) proves costly?

---

## 11. Explicitly NOT in v1

To hold scope (CONCEPT §9.6 "don't build another n8n" — ironically we *are* using
n8n, so the discipline is "don't build *nodes/sinks* we don't need yet"):
no cloudflared/Authelia, no schedule/webhook triggers, no search/memory/LLM/
extract nodes, no email/wiki/little-coder sinks, no custom-node packages (built-in
HTTP nodes first), no multi-user. All deferred to P2+.
