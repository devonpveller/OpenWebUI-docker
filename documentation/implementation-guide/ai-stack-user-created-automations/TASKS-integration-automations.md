# Tasks — ai-stack User-Created Automations (v1)

> Companion to [PLAN-integration-automations.md](PLAN-integration-automations.md).
> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked.
> Each task lists a **done-when**. Nothing here is started.

## Phase 0 — Tailnet spike: Manual → Research → Format→ON

### 0.A Decisions (settled 2026-06-13)
- [x] **0.A1** Deployment = **separate `automations` compose project** (license isolation), attaching to `ai-stack_default` + `open-brain_obnet` as external (PLAN §1/§4).
- [x] **0.A2** UI = **own n8n deployment + first-party `n8n-nodes-ai-stack` custom-node package**, keeping n8n's engine; Research ships as a custom node in P0 (PLAN §2).

### 0.B New project + secrets
- [ ] **0.B1** Create `automations/docker/docker-compose.yml` (project `name: automations`) with `n8n` (build, own image) + `n8n-db`, `n8n-data`/`n8n-db-data` volumes, and external `ai-stack_default` + `obnet` network blocks (PLAN §7). *Done-when:* `docker compose -f automations/docker/docker-compose.yml config` validates (main + OB1 up so the external nets exist).
- [ ] **0.B2** Add `automations/docker/Dockerfile.n8n` (`FROM docker.n8n.io/n8nio/n8n:<pinned>`) that installs/copies the `n8n-nodes-ai-stack` package. *Done-when:* image builds.
- [ ] **0.B3** Add `automations` env keys: `N8N_DB_PASSWORD`, `N8N_ENCRYPTION_KEY` (pinned random), `N8N_HOST`, `N8N_WEBHOOK_URL`, shared `MCP_ACCESS_KEY`. *Done-when:* present; encryption key pinned.
- [ ] **0.B4** Bring up *after* main + OB1: `docker compose -f automations/docker/docker-compose.yml up -d`; complete n8n owner-account setup at `http://127.0.0.1:5678`. *Done-when:* editor loads, owner account created, our custom nodes appear in the palette search.

### 0.X Node package scaffold + Research node (the extensibility seam)
- [ ] **0.X1** Scaffold `automations/n8n-nodes-ai-stack/` from the `n8n-nodes-starter` template (TypeScript, declarative SDK). *Done-when:* `npm run build` produces a loadable package.
- [ ] **0.X2** Implement the **Research** custom node: input `prompt` (+ optional `confidence_floor`); calls `POST openbrain-research:8000/research` with `x-brain-key`; encapsulates the poll loop on `GET /research/jobs/:id` until terminal; output = `research_result` (`synthesis`, `prose`, `cited_sources`, `gaps`, `thread_id`, `backstop`). *Done-when:* dropping the node in a flow and running it returns a real result object.
- [ ] **0.X3** Bake the package into the image (0.B2) for durability; document the dev loop (mounted `N8N_CUSTOM_EXTENSIONS` → rebuild image). *Done-when:* a fresh `up` shows the Research node with no manual install.

### 0.C Tailnet exposure
- [ ] **0.C1** Add n8n env vars to the `tailscale` service (PLAN §6): `N8N_SERVE_ENABLED`, `N8N_TS_PORT=8446`, `N8N_LOCAL_PORT=8241`, `N8N_HOST_INTERNAL`, `N8N_PORT_INTERNAL`.
- [ ] **0.C2** Add `setup_n8n_serve()` to `entrypoint.sh` (copy `setup_open_notebook_api_serve`, ~L390-417) + boot-time health-gated call. *Done-when:* function present, socat 8241 → n8n:5678, serve on :8446.
- [ ] **0.C3** Add deferred-setup + socat-health block for n8n to the monitoring loop (~L696-725). *Done-when:* killing the socat proxy self-heals within one loop.
- [ ] **0.C4** Restart `tailscale` after n8n healthy (respect the OWUI→tailscale netns ordering). *Done-when:* `https://<tailnet-host>:8446/` loads the n8n editor.

### 0.D The workflow
- [ ] **0.D1** Build the flow: **Manual trigger → Research (custom node, 0.X2)**. *Done-when:* the Research node runs from a manual trigger and emits `research_result`.
- [ ] **0.D2** Verify the **canonical Open Brain output** (no write node — it's the curator step inside research, PLAN §5.0/§5.3): confirm `result.curator.thread_id` is set and the synthesis/claims are persisted in Open Brain, **and** the thread is visible in the Open Notebook viewer. *Done-when:* the research result is in Open Brain and shows up in Open Notebook with no extra HTTP call.
- [ ] **0.D3** Graceful failure: if `openbrain-research` is unreachable (OB1 down), the flow errors cleanly (no hang). *Done-when:* verified by stopping OB1.

### 0.E Stack discipline (new project lifecycle)
- [ ] **0.E1** Teach `scripts/emergency-recovery.ps1` **and** `.bat` the third project: inventory `n8n` + `n8n-db`; bring `automations` up *after* main + OB1, tear down *before* them (PLAN §8). *Done-when:* both scripts manage the project in the right order.
- [ ] **0.E2** Update `CLAUDE.md` "Stacks at a glance" to add `automations` as a third compose project (own lifecycle, like OB1/Portal). *Done-when:* table lists it.
- [ ] **0.E3** Add `n8n-db-backup` cron sidecar *inside the `automations` project* (existing `*-backup` pattern). *Done-when:* a backup artifact is produced once.
- [ ] **0.E4** Run `/stack-map`; update `workspace-stacks.md` to list the `automations` project + its external network attachments. *Done-when:* skill reports no drift.

**P0 exit criteria:** from a tailnet device, run the flow by hand; the **custom
Research node** (proving the `n8n-nodes-ai-stack` extension seam) produces a
grounded synthesis that lands in **Open Brain** and is visible in Open Notebook;
recovery scripts + CLAUDE.md + stack-map updated.

## Phase 1 — Surfacing outputs (Podcast + OWUI spike), as first-party nodes

- [ ] **1.A** **Format→Podcast** node in `n8n-nodes-ai-stack`: render a single `result.synthesis` into `EpisodeInput{segments:[{label,items:[{title,url,synthesis}]}]}`, then `POST open_notebook:5055/api/podcasts/generate` → poll `/api/podcasts/jobs/{id}` → fetch `/api/podcasts/episodes/{id}/audio`. **Bypass `openbrain-podcast`.** *Done-when:* an mp3 episode is produced from one research run via the node.
- [ ] **1.B** Fan-out wiring: one Research run (already in Open Brain) drives Format→Podcast as an additive surfacing branch. *Done-when:* a single run yields the Open Brain thread **and** an episode.
- [ ] **1.C** OWUI surfacing spike (PLAN §5.2 option a): verify OWUI 0.9.x REST chat-create + message-insert with an API key. *Done-when:* either a synthesis appears as a new OWUI chat, **or** the spike is documented as not-worth-it and OWUI surfacing is dropped from v1.
- [ ] **1.D** (if 1.C passes) Add **Format→OWUI** as a custom node + surfacing branch.

**P1 exit criteria:** the CONCEPT §6 graph runs — one research result fans out to
Open Notebook + Podcast (+ OWUI if the spike passed).

## Phase 2 — deferred (out of v1 scope, tracked only)

- [ ] Read-palette nodes (web search, OB search/capture, memory, LLM via llm-gateway, extract).
- [ ] Schedule + Webhook triggers.
- [ ] Action-authorization boundary (CONCEPT §9.2) — required **before** any privileged sink.
- [ ] cloudflared + Authelia exposure (CONCEPT §10 P3).
- [ ] **Teams-chat (Mattermost) surfacing output** (PLAN §5.5) — `agent-bridge`-mediated, governance-gated; blocked on the teams-chat project reaching its platform spike (P0).
- [ ] Privileged sinks: email/digest, wiki recompile, little-coder.
- [ ] Optional: package thin custom n8n nodes for the READY surfaces (nicer UX than raw HTTP nodes).
