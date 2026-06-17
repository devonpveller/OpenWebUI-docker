# Integration Plan — ai-stack User-Created Automations (v1)

> **Status:** PLAN — not built. Grounded in live source/config (ports, endpoints,
> and the tailscale-serve pattern are quoted from the real files as of 2026-06-13).
> **Companion docs:** [CONCEPT](CONCEPT-ai-stack-user-created-automations.md) (the idea + feasibility),
> [TASKS](TASKS-integration-automations.md) (the build checklist).

---

## 1. Locked decisions (operator, 2026-06-13)

| Decision | Choice | Consequence for this plan |
|----------|--------|---------------------------|
| Deployment | **Separate compose project** (`automations`) | n8n lives in its **own** compose project that attaches to ai-stack + OB1 networks as *external*. Keeps the fair-code/source-available n8n **isolated** from the open-source/custom ai-stack (clean license boundary). Own lifecycle. Mirrors the OB1 / agent-org precedent. |
| Audience / exposure | **Single-user, tailnet-first** | v1 is tailnet-only (tailscale serve). **No** cloudflared/Authelia, **no** privileged-sink auth boundary yet. |
| UI platform | **Own n8n deployment + first-party custom nodes** | We run *our* build of n8n (keeping its engine), and grow an `n8n-nodes-ai-stack` node package as ai-stack integrations expand. Extensibility is built in from P0. See §2. |
| v1 node scope | **Research fan-out only** | Research node → 3 Format nodes (OWUI / Open Notebook / Podcast) + manual trigger. No search/memory/LLM/email/coder nodes in v1. **But each is built as a custom node**, not a raw HTTP node (§2). |

---

## 2. Headline architecture decision: own n8n deployment + first-party nodes

The concept doc proposed a bespoke `automations-engine` + `automations-ui`. **That
is superseded for v1** — n8n already *is* the engine + editor. Grounded finding
(n8n docs, verified):

- The n8n Docker image ships the **execution engine *and* the node editor
  together**, plus Postgres persistence, Manual/Schedule/Webhook triggers, an HTTP
  Request node, and **Do-While / Wait nodes for async polling** — i.e. *everything*
  the bespoke engine was going to be. **We keep n8n's engine. We do not rebuild it.**

**Two senses of "fork" — we want the right one (operator, 2026-06-13):**
- ❌ *Fork the editor frontend onto a custom backend* — would force us to
  re-implement scheduling, the executor, credential storage, run history. Throws
  away n8n's biggest asset. **Not this.**
- ✅ *Own the n8n **deployment** and **extend it with our own custom nodes*** —
  keep n8n's engine, run **our** build of the image, and grow a first-party
  `n8n-nodes-ai-stack` node library as ai-stack integrations expand. **This.**

**What this means concretely:**
- **Own image, not the stock one.** The `automations` project builds n8n from a
  thin `Dockerfile` (`FROM docker.n8n.io/n8nio/n8n:<pinned>`) that bakes in our
  custom node package — so extensibility is first-class and reproducible, not a
  side-mount. (During dev you may iterate via a mounted `N8N_CUSTOM_EXTENSIONS`
  dir, then bake for durability — same "deployed image is source of truth" habit
  as the rest of the stack.)
- **First-party node package** `n8n-nodes-ai-stack` (TypeScript, n8n declarative
  node SDK, ~50–100 lines per simple node). It starts with the **Research** node
  in P0 and is the seam every future ai-stack capability plugs into.
- **Build the node, don't just call HTTP.** Even though n8n's built-in HTTP
  Request node *could* call `/research`, P0 ships Research as a **custom node**
  (typed `prompt` input, a `research_result` output, the poll loop encapsulated)
  so the user gets a clean palette item and we establish the extension pattern
  from the start.

This honors the "build in extensibility from the beginning" intent, keeps n8n's
engine, and still eliminates the bespoke `automations-engine`/`automations-ui`.

> **Licensing:** n8n is under the Sustainable Use License (fair-code).
> Self-hosting for internal/personal use is permitted free of charge. Embedding
> n8n *as a feature in a product you sell* is the only restricted case — not us.

---

## 3. Target architecture (v1, separate `automations` project)

```
   Tailnet device ──HTTPS :8446──▶ tailscale serve ──socat :8241──▶ n8n:5678
   (tailscale is in MAIN stack; reaches n8n via ai-stack_default — see §6)        │
                                                                                   │
   ╔═══ project: automations (separate compose) ════════════════════════════════╗ │
   ║  n8n  (engine + editor + triggers)              n8n-db (Postgres)           ║◀┘
   ║   workflow: "Research fan-out"                                              ║
   ╚═══╤═══════════════╤═══════════════════════╤════════════════════════════════╝
       │ HTTP          │ HTTP                   │ HTTP
       ▼               ▼                        ▼
 openbrain-research  open_notebook         open_notebook
 POST /research      /api/sources          /api/podcasts/generate
 (poll job)          (Format→ON)           (Format→Podcast)
 [open-brain_obnet]  [ai-stack_default]    [ai-stack_default]
```

Two new containers, in a **new compose project** named `automations`: **`n8n`** and
**`n8n-db`**. They attach to existing ai-stack + OB1 networks as **external** — they
own no new networks of consequence and serve no inference. Everything they call
already runs. No `automations-ui` / `automations-engine` / `automations-db` (the
concept's trio collapses into n8n + its DB). **License boundary:** the fair-code
n8n image stays inside the `automations` project; the main `ai-stack` /
`open-brain` projects remain all-open-source/custom.

---

## 4. Network & reachability (the cross-stack seam)

This is the **one non-obvious wiring problem**, because the flagship target lives
in a *different compose project*:

- **`openbrain-research` is an OB1 (`open-brain`) container**, on networks
  `obnet`, `llm-net`, `search-gw-net` — published to host at `127.0.0.1:8818:8000`.
  It is **not** on the main stack's default networks.
- Because n8n is now its **own** project, **every** network it uses is declared
  **external** (the OB1 precedent: OB1 attaches to `ai-stack_llm-net` / `app-net`
  this way; `open_notebook` and `openbrain-db-backup` likewise join
  `open-brain_obnet`):

```yaml
# automations/docker/docker-compose.yml — networks block (all external)
networks:
  ai-stack_default:
    external: true
    name: ai-stack_default       # reach open_notebook:5055, openwebui:8080; reachable by tailscale
  obnet:
    external: true
    name: open-brain_obnet       # reach openbrain-research:8000
```

- **`open_notebook` / `openwebui` are main-stack containers.** n8n reaches them
  over `ai-stack_default` (the host-reachable main bridge they sit on).
- **`openbrain-research` is an OB1 container** — reached over `open-brain_obnet`.

**Reachability matrix for v1:**

| n8n must call | Host:port (by container name) | External network n8n joins |
|---------------|-------------------------------|----------------------------|
| Research submit/poll | `openbrain-research:8000` | `open-brain_obnet` |
| Open Notebook (ON + Podcast) | `open_notebook:5055` | `ai-stack_default` |
| OWUI (Format→OWUI, see §5.2) | `openwebui:8080` | `ai-stack_default` |
| (reached *by* tailscale serve) | `n8n:5678` | `ai-stack_default` (so the main-stack tailscale socat can resolve it — §6) |

> **Bring-up order (mirrors OB1):** the `automations` project starts **after** the
> main stack *and* OB1 are up (its external networks must already exist), and is
> torn down **before** them. n8n must tolerate `openbrain-research` being absent
> (workflows simply error until OB1 is healthy). No cross-project `depends_on` —
> compose can't express it; rely on n8n's per-run error handling instead.

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

n8n listens on **:5678**. The `tailscale` container lives in the **main** stack
(it shares `openwebui`'s netns), so for its socat proxy to resolve `n8n:5678`
across the project boundary, **n8n must be on `ai-stack_default`** (the network
`openwebui`/tailscale can see) — already required by §4. Then follow the exact
open-notebook pattern in `entrypoint.sh` (Streamlit-style: served at **root on a
distinct TS port**, via a socat proxy, with deferred setup + monitoring-loop
self-heal).

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

## 7. Compose integration (new `automations` project)

New file: **`automations/docker/docker-compose.yml`** (project name `automations`,
the OB1 layout). Driven with `docker compose -f automations/docker/docker-compose.yml ...`
— a plain `docker compose` in the workspace root never touches it (same isolation
as OB1).

```yaml
name: automations

services:
  n8n:
    build:
      context: ..                              # automations/ — bakes in n8n-nodes-ai-stack
      dockerfile: docker/Dockerfile.n8n        # FROM docker.n8n.io/n8nio/n8n:<pinned>
    image: ai-stack/n8n:local                  # our owned build
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
      - ai-stack_default   # reach open_notebook, openwebui; reachable by main-stack tailscale
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
      - ai-stack_default

networks:
  ai-stack_default:
    external: true
    name: ai-stack_default
  obnet:
    external: true
    name: open-brain_obnet

volumes:
  n8n-data:
  n8n-db-data:
```
New `.env` keys (in the `automations` project's env): `N8N_DB_PASSWORD`,
`N8N_ENCRYPTION_KEY`, `N8N_HOST`, `N8N_WEBHOOK_URL` — plus `MCP_ACCESS_KEY` must be
shared in (it's the research `x-brain-key`). **Pin `N8N_ENCRYPTION_KEY`** (like
`WEBUI_SECRET_KEY`) — if it rotates on recreate, stored credentials become
undecryptable.

---

## 8. Stack discipline (mandatory)

This adds a **new compose project**, not just containers — so the discipline is
project-level (mirrors how OB1 is handled):
1. **new compose file** — `automations/docker/docker-compose.yml` (§7).
2. **recovery scripts** — `scripts/emergency-recovery.ps1` **and** `.bat`: teach
   them the third project. Bring `automations` up **after** main + OB1, tear it
   down **before** them (it depends on their external networks). Add `n8n` +
   `n8n-db` to the inventory + ordered startup/shutdown, alongside the existing
   per-project handling.
3. **stack-map** — run `/stack-map`; update
   `.claude/skills/stack-map/references/workspace-stacks.md` to list the new
   `automations` project (and note it joins `ai-stack_default` + `open-brain_obnet`).
4. **CLAUDE.md** — the "Stacks at a glance" table currently names *two* compose
   projects + recovery; add `automations` as a third project (own lifecycle, like
   OB1/Portal).
5. **backups** — `n8n-db` is stateful: add an `n8n-db-backup` cron sidecar (inside
   the `automations` project) following the existing `*-backup` convention
   (workflows + credentials live in the DB).

---

## 9. Phasing

- **P0 — Tailnet spike + node-package scaffold (the proof + the seam):**
  the `automations` project (own n8n image) + n8n-db; tailnet serve on :8446;
  scaffold the **`n8n-nodes-ai-stack`** package and ship the **Research** node in
  it; one workflow: **Manual → Research → Format→ON**. Proves the research async
  contract, a verified sink, *and* the custom-node extension path end-to-end on
  the tailnet. (Format→ON may start as a built-in HTTP node and graduate to a
  custom node in P1 — Research is the one that must be custom in P0.)
- **P1 — Fan-out (more first-party nodes):**
  Add **Format→Podcast** (the ON podcast-API render path, §5.4) and **spike
  Format→OWUI** (§5.2 option a — verify OWUI REST chat creation), both as nodes in
  `n8n-nodes-ai-stack`. Goal: one research result → up to three sinks (the
  CONCEPT §6 graph), all as first-party palette items.
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
5. **Recovery drift** (§8). A whole new project + a backup sidecar across two
   recovery scripts + CLAUDE.md + stack-map — more places than a fold-in. The
   `/stack-map` skill check catches container/network drift; the project lifecycle
   ordering is on the recovery scripts.
6. **External-network ordering** (§4). The `automations` project will fail to
   start if `ai-stack_default` or `open-brain_obnet` don't exist yet — bring it up
   strictly after main + OB1.
7. **n8n scheduling jitter** (~1–2 min under load) — irrelevant for v1 (manual
   trigger), note for P2 schedule nodes.

**Open items to confirm with operator:**
- (a) For v1, is **Open Notebook the acceptable primary sink** if the OWUI-chat
  spike (§5.2) proves costly?

*(Resolved 2026-06-13: (1) n8n runs as its own `automations` compose project —
license isolation — attaching to `ai-stack_default` + `open-brain_obnet` as
external. (2) We own the n8n **deployment** (custom-built image) and extend it with
a first-party `n8n-nodes-ai-stack` package, keeping n8n's engine; the Research node
ships as a custom node in P0.)*

---

## 11. Explicitly NOT in v1

To hold scope (CONCEPT §9.6 "don't build another n8n" — ironically we *are* using
n8n, so the discipline is "don't build *nodes/sinks* we don't need yet"):
no cloudflared/Authelia, no schedule/webhook triggers, no search/memory/LLM/
extract nodes, no email/wiki/little-coder sinks, no multi-user. All deferred to
P2+. **Note:** the `n8n-nodes-ai-stack` custom-node package *is* in v1 (the
extensibility seam) — but only the Research + Format nodes live in it for now;
additional nodes land as the palette grows.
