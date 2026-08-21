# Container Registry — purpose & justification

> Status: LIVE · created 2026-08-20 during the CLEANUP-PLAN v3 execution day,
> from a container-by-container audit of every running project. Topology,
> networks and ports live in the stack-map reference
> ([workspace-stacks.md](../.claude/skills/stack-map/references/workspace-stacks.md));
> **this document answers "why does this container exist"**. Update both under
> the container rule.

Verdict shorthand: every container listed KEEP carries a live, verified
purpose. Containers that failed that test today were removed (see
"Retired 2026-08-20" at the bottom).

---

## Project `ai-stack` — main stack (31 default services)

### Core

| Container | Purpose | Why it exists / what breaks without it |
|---|---|---|
| `openwebui` | The chat frontend (OWUI 0.11.0, GPU build) | The primary human surface; hosts the paste-deployed tools/pipes/skills (`owui/`) |
| `tailscale` | Tailnet ingress; **shares openwebui's netns** | Carries all 8 tailnet serve routes (OWUI, llama-cpp aliases, ON ×2, wiki, LiteLLM UI, Mattermost). Restart order openwebui→tailscale is mandatory |
| `open-terminal` | Sandboxed exec backend for little-coder | The only place agent code executes; isolated on lc-net with key'd API |

### Inference plane (the LiteLLM front door)

| Container | Purpose | Why |
|---|---|---|
| `llm-gateway` | LiteLLM front door; holds the `llama-cpp`/`llama-cpp-embed` **aliases** | Single inference inlet: analytics/spend ledger + future multi-backend router. Nothing may route around it (pre-commit enforced) |
| `llm-gateway-db` | Postgres spend-log ledger for LiteLLM | Attribution/audit of every inference call |
| `llm-gateway-ui` | Master-key'd LiteLLM Admin UI (tailnet :8445/ui) | Read-only analytics dashboard; deliberately a separate container so the main gateway stays permissive & UI-less |
| `llm-queue` | B2 admission controller between LiteLLM and the upstreams | Hold-and-dispatch + per-caller priority lanes on ONE GPU; turned fan-out 429s into ordered queuing. (Lanes reach full power after the J.1 virtual-keys cutover) |
| `llama-cpp-upstream` | Real chat inference: llama-swap → llama.cpp (qwen36-27b, MTP) | The GPU worker; isolated on llm-backend-net so callers physically cannot bypass the gateway |
| `llama-cpp-embed-upstream` | Real embedding inference (bge-m3, plain llama.cpp) | Separate from chat so embedding bursts (OB1 backfills) never contend for the swap slot |

### Memory (decision D-9 keeps this plane, direction pending)

| Container | Purpose | Why |
|---|---|---|
| `mnemory` | Layer-1 personal memory service (llm-net only, no host port) | Live consumers: OWUI mnemory tool + persistent-memory filter, system-health/llm-traffic modules |
| `mnemory-cloud-gateway` | Privacy proxy (:8060, bearer-key'd, allow-listed verbs) | The ONLY published door to mnemory; label-forcing keeps cloud reads scoped. Twin of openbrain-gateway (unification = E.1, coupled to D-9/H.1) |

### Search (private search gateway)

| Container | Purpose | Why |
|---|---|---|
| `search-vpn` | Mullvad WireGuard egress — ALL search-plane traffic (engine queries + page fetches via its HTTP proxy :8888) | Anonymity without tor-exit blocking. `search-tor` RETIRED 2026-08-21 after validation (zero traffic since the flip; torrc archived) |
| `search-redis` | SearXNG's rate-limit/cache store | SearXNG requirement; FLUSHDB db0 clears engine suspensions |
| `searxng` | The metasearch engine (internal-only net) | Aggregates engines without API keys |
| `search-gateway` | REST face (:8085, key'd) + Tavily shim | What `openbrain-research` and tools actually call; provider abstraction + rotation |

### Coder

| Container | Purpose | Why |
|---|---|---|
| `little-coder` | The self-improving coding agent daemon (:8090 on lc-net) | Executes OWUI-triggered and agent-org-dispatched coding tasks; control plane DECIDES / open-terminal EXECUTES |
| `lc-egress` | tinyproxy default-deny egress allowlist | The coder plane's only internet path (git host allowlist) — blast-radius control |

### Aux (transitional plane — decision D-10)

| Container | Purpose | Why |
|---|---|---|
| `smolcrawl-pipelines` | OWUI Pipelines server (:9099, key'd) for crawl→KB flows | Live crawl/ingest surface (the retired deep-research harness was a different part of smolcrawl) |
| `surrealdb` | Datastore for open_notebook (digest-pinned since today) | Exists solely for ON; retires with it (D-10) |
| `open_notebook` | The IKS fork of Open Notebook | **STAYING (operator, 2026-08-21)** until the wiki workbench UI matures; long-term direction = fold ON's function into the wiki (D-10 decided) |

### Backup sidecars (one per stateful store — backup-conventions runbook)

`mnemory-backup`, `openwebui-backup` (vector_db excluded → 23 s nightly),
`llm-gateway-backup`, `little-coder-backup`, `openbrain-db-backup` (joins
obnet cross-project), `openbrain-wiki-backup` (+ wiki-assets),
`open-notebook-backup` (surql export + notebook tar; env-var creds since
today), `smolcrawl-backup`, `tailscale-backup` (state/certs),
`lm-models-backup` (the GGUF store — **not LM Studio**, despite the host
path). Justification for all ten: every stateful byte has exactly one
sidecar producing verified artifacts into `backups/`, freshness-watched
twice (watchdog recency table + sysadmin daily check). Scheduler flavors are
a known 3-way split (crond/supercronic/sleep-loop) — unification queued in
D.1 follow-ups.

### Portal — own compose project `portal` since 2026-08-21 (12 services, **currently ON**)

| Container | Purpose |
|---|---|
| `portal-init` | One-shot volume chown at portal-on (network_mode: none) |
| `caddy` | Reverse proxy + forward-auth gate; also serves the wiki-workbench path (:8446 → tailnet :8444) |
| `authelia` | AuthN/AuthZ front (TOTP), hardened read-only |
| `cloudflared` | Cloudflare Tunnel — the ONLY internet ingress |
| `authelia-watcher` | Tails auth logs → alerts on failures/bans |
| `authelia-notif-bridge` | Converts Authelia file-notifications → alerter |
| `integrity-tripwire` | Hash-baseline tripwire over portal configs |
| `portal-alerter` | Gmail alert egress (the single portal internet-egress chokepoint) |
| `portal-cron` | Scheduled digests (writes `reports/portal-digest/` daily — verified live today) |
| `tunnel-watcher` | Watches cloudflared health/registration |
| `caddy-backup`, `authelia-backup` | Portal-state sidecars (same-UID trick for 0600 files) |

Justification: the portal is the deliberate, audited internet exposure
(SECURITY.md); every sidecar exists because the 2026-05-29 audit added it.
Running-state is an operator choice (`portal-on.ps1`); the split into its own project positions the portal to front more than ai-stack (operator decision #5). Data lives in `portal_*` volumes (migrated from `ai-stack_*` 2026-08-21).

---

## Project `open-brain` (OB1) — 24 containers

### Store + API plane

| Container | Purpose | Why |
|---|---|---|
| `openbrain-db` | pgvector Postgres — THE canonical knowledge store | Everything else in OB1 orbits it |
| `openbrain-mcp` | Core MCP server (capture/search/fetch tools) | The write/read contract used by OWUI (via mcpo), the gateway (cloud), and curator |
| `openbrain-ext` | Extensions MCP server (wiki/threads/extras) | Split from core so the cloud gateway can expose core-only |
| `openbrain-mcpo` / `openbrain-mcpo-ext` | MCP→OpenAPI bridges for OWUI tool-servers | **Two instances on purpose** — one mcpo proxying two MCP servers crashes (documented upstream bug in `mcpo.config.json`) |
| `openbrain-gateway` | Cloud privacy proxy (:8061, bearer-key'd; forces origin/share=cloud) | The ONLY door external/cloud clients get; twin of mnemory-cloud-gateway (E.1) |
| `openbrain-postgrest` + `openbrain-rest` | PostgREST + its Caddy front (:3001) | Recipes/local integrations read the store without MCP |

### Workers (async enrichment)

| Container | Purpose |
|---|---|
| `openbrain-entity-worker` | Entity extraction → the graph the wiki compiles from |
| `openbrain-suggestion-worker` | Thread/source suggestion triage |
| `openbrain-chunk-worker` | Source chunking + embeddings (:8817; the 08-20 emoji-bug fix lives here) |
| `openbrain-extract` | content-core extraction (docs/images/OCR) for the workbench |
| `openbrain-grounding-backfiller` | Backfills grounding for pre-grounding-era content |

### Knowledge surfaces

| Container | Purpose |
|---|---|
| `openbrain-wiki` | Quartz wiki compiler (+ git vault commits; push severed on purpose) |
| `openbrain-wiki-viewer` | The compiled wiki + workbench UI (:8812; tailnet :8444 via caddy) |
| `openbrain-workbench` | Browser write/read API for the workbench (:8814) — NOT the MCP contract |
| `openbrain-research` | The shared research engine (:8818; queue, GROUNDED synthesis, OWUI async callback) — replaced smolcrawl's in-repo harness |
| `openbrain-curator` | Research-package ingest inlet (persists via openbrain-mcp) |

### Scheduled slice

| Container | Purpose |
|---|---|
| `openbrain-cron` | The clock: HTTP-triggers the jobs below |
| `openbrain-digest` | Daily digest composer (gap-dive triage since 08-05) |
| `openbrain-gmail-pull` / `openbrain-gmail-prune` | Gmail ingest + retention (halve-retry embed fix lives in pull) |
| `openbrain-podcast` | Digest→podcast renderer (on-demand audio via ON) |
| `openbrain-idea-refinery` | Profile-gated idea-honing drain (user-gated loop; 100% local since 07-26) |

---

## Project `agent-org` — 11 running (6 default + 5 `workers` profile)

| Container | Purpose | Why |
|---|---|---|
| `mattermost` + `mattermost-db` | The org's chat fabric (11.7 ESR) | Every operator⟷agent conversation, approvals, and bridge traffic runs through it |
| `agent-bridge` + `agent-bridge-db` | The governed org bus (:8830) + its Postgres | Charters/floor enforcement, wake bus (the one at-least-once delivery in the workspace), delivery gates, 723-test suite |
| `agent-bridge-db-backup`, `mattermost-db-backup` | pg-backup sidecars | Same one-sidecar-per-store rule as the main stack |
| `ao-worker-1/2` + `ao-ot-1/2` | little-coder worker pair ×2 (agent + its open-terminal) | The org's hands; profile-gated so the org can be quiesced |
| `ao-git-egress` | Git-host allowlist proxy for workers | Workers never get raw internet; the bridge writes the allowlist |

(The `cloud` profile — `llm-gateway-cloud` + db + `ao-egress` — is defined but
OFF: cloud-model escalation lane, master-key'd, enabled per-engagement.)

---

## Which door do I use? (the anti-wrong-endpoint matrix)

Every misconnection risk in this stack is one of these lanes. Pick by WHO is
calling, never by which port happens to answer.

| You are… | You want… | Use | NEVER use |
|---|---|---|---|
| Any container needing chat/embeddings | Inference | `http://llama-cpp:8080/v1` / `llama-cpp-embed` aliases + your `sk-` virtual key | `*-upstream` directly (bypasses ledger/queue; pre-commit-blocked) |
| A human/agent on the host | Inference | Same aliases via an llm-net container, or the tailnet `/llama-cpp` route | LiteLLM `/health` via the alias (model-load thrash — `/health/liveliness` only) |
| OWUI (a tool server) | Open Brain | `openbrain-mcpo:8000/open-brain` + `-mcpo-ext` (OpenAPI, key'd) | the MCP servers directly |
| A local/trusted process (Claude Code, recipes) | Open Brain | `openbrain-mcp` (obnet MCP) or `openbrain-rest`:3001 (PostgREST) | the cloud gateway (it FILTERS: share=cloud only) |
| A cloud/external client | Open Brain | `openbrain-gateway` 127.0.0.1:8061 (bearer key; forced share=cloud) | anything else — this is the only cloud door BY DESIGN |
| A local/trusted process | mnemory | `mnemory:8050` REST directly (full access) | `mnemory-cloud-gateway` (it BLOCKS personal reads/writes) |
| A cloud/external client | mnemory | `mnemory-cloud-gateway` 127.0.0.1:8060 | `mnemory:8050` (not published; and no cloud client is trusted with it) |
| Anything fetching web pages | Anonymous egress | Mullvad HTTP proxy `http://vpn:8888` | direct egress (home IP) — tor retired 2026-08-21 |
| Anything searching the web | Search | `gateway:8080` (search-gateway REST, key'd) | SearXNG directly (internal-only by design) |
| OWUI / agent-org | Coding tasks | little-coder daemon `little-coder:8090` (lc-net) | open-terminal directly (exec plane, key'd, workers only) |

The rule behind the table: **cloud doors filter, local doors trust** — a
gateway refusing you data usually means you picked the cloud door for a
local job (or vice versa), not that something is broken.

## External projects on this host (out of scope — per operator, 2026-08-20)

| Project | Containers | Note |
|---|---|---|
| `realtimeaudiochat_local_stt_llm_tts` | `stt-tts-server`, `stt-tts-tailscale` | The OpenAI-compatible TTS/STT service (host :8000) that ON/podcast tooling references; **serves purposes outside ai-stack** — not managed by this repo's compose/recovery |
| `task-management` | `task-management-api-1`, `task-management-db-1` | Separate application; **outside ai-stack** |

---

## Retired 2026-08-20 (this audit + earlier today)

| Container(s) | Why removed |
|---|---|
| `watchtower` | Unpinned auto-updater holding the only docker.sock; one effective target. Manual update runbook replaces it (D-2) |
| `search-mcpo` | Keyless MCP wrapper no consumer could even reach (wrong network) (J.3) |
| `lc-mcpo` | Dormant-since-build third door to little-coder; both real callers use the daemon directly (D-13; source kept for chapter 2) |
| `iks-chunk-worker`, `iks-suggestion-worker` | Exited 2 months; corpses of the dev overlay |
| `iks-db`, `iks-mcp`, `iks-notebook`, `iks-surreal` | The whole **iks-dev** overlay: the development environment that BUILT the `open_notebook:iks` fork now in prod. Idle since 08-01 (verified via logs); volumes kept; tree archived at `scripts/archive/iks-dev/` — recreatable in minutes if fork iteration resumes |

## Redundancies examined and deliberately KEPT

- `openbrain-mcpo` + `openbrain-mcpo-ext` — upstream mcpo crash bug, documented.
- `mnemory-cloud-gateway` vs `openbrain-gateway` — same program twice (~75%); merge
  (E.1) is deliberately sequenced behind the mnemory direction call (D-9) and
  the OB1 submodule move (H.1), not forgotten.
- `surrealdb` + `open_notebook` — transitional pair; retires together (D-10).
- Two LiteLLM instances (`llm-gateway` vs agent-org's off-by-default
  `llm-gateway-cloud`) — opposite auth postures by design (local-permissive
  vs cloud-master-key'd).
