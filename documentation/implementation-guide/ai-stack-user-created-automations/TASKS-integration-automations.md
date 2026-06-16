# Tasks — ai-stack User-Created Automations (v1)

> Companion to [PLAN-integration-automations.md](PLAN-integration-automations.md).
> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked.
> Each task lists a **done-when**. Nothing here is started.

## Phase 0 — Tailnet spike: Manual → Research → Format→ON

### 0.A Confirm decisions
- [ ] **0.A1** Operator confirms **platform-not-fork** (PLAN §2). *Done-when:* a one-line yes/no recorded here. *Default if silent:* platform.
- [ ] **0.A2** Operator confirms n8n may join `open-brain_obnet` external network (PLAN §4/§10b). *Done-when:* recorded.

### 0.B Compose + secrets
- [ ] **0.B1** Add `.env` keys: `N8N_DB_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_HOST`, `N8N_WEBHOOK_URL`. *Done-when:* present in `.env`; `N8N_ENCRYPTION_KEY` is a pinned random value.
- [ ] **0.B2** Add `n8n` + `n8n-db` services, `n8n-data`/`n8n-db-data` volumes, and the `obnet` external-network block to `docker-compose.yml` (PLAN §7). *Done-when:* `docker compose config` validates.
- [ ] **0.B3** `docker compose up -d n8n-db n8n`; complete n8n owner-account setup at `http://127.0.0.1:5678`. *Done-when:* editor loads, owner account created.

### 0.C Tailnet exposure
- [ ] **0.C1** Add n8n env vars to the `tailscale` service (PLAN §6): `N8N_SERVE_ENABLED`, `N8N_TS_PORT=8446`, `N8N_LOCAL_PORT=8241`, `N8N_HOST_INTERNAL`, `N8N_PORT_INTERNAL`.
- [ ] **0.C2** Add `setup_n8n_serve()` to `entrypoint.sh` (copy `setup_open_notebook_api_serve`, ~L390-417) + boot-time health-gated call. *Done-when:* function present, socat 8241 → n8n:5678, serve on :8446.
- [ ] **0.C3** Add deferred-setup + socat-health block for n8n to the monitoring loop (~L696-725). *Done-when:* killing the socat proxy self-heals within one loop.
- [ ] **0.C4** Restart `tailscale` after n8n healthy (respect the OWUI→tailscale netns ordering). *Done-when:* `https://<tailnet-host>:8446/` loads the n8n editor.

### 0.D The workflow (built-in nodes only)
- [ ] **0.D1** Research submit: HTTP Request node `POST openbrain-research:8000/research` with `x-brain-key` header + body `{query, origin:"manual", options:{confidence_floor:0.5}}`. *Done-when:* returns a `job_id`.
- [ ] **0.D2** Poll loop: Do-While/Wait on `GET /research/jobs/:id` until `status∈{done,error,cancelled}`. *Done-when:* loop exits with a `result` object on a real query.
- [ ] **0.D3** Format→ON: create source via `POST open_notebook:5055/api/sources` carrying `result.prose`, link to an "Automations" notebook. *Done-when:* the synthesis appears as a source in ON.
- [ ] **0.D4** Graceful failure: if `openbrain-research` is unreachable (OB1 down), the workflow errors cleanly (no hang). *Done-when:* verified by stopping OB1.

### 0.E Stack discipline
- [ ] **0.E1** Register `n8n` + `n8n-db` in `scripts/emergency-recovery.ps1` **and** `.bat` (inventory + shutdown/startup order). *Done-when:* both scripts list both containers.
- [ ] **0.E2** Add `n8n-db-backup` cron sidecar (existing `*-backup` pattern). *Done-when:* a backup artifact is produced once.
- [ ] **0.E3** Run `/stack-map`; update `workspace-stacks.md`. *Done-when:* skill reports no drift.

**P0 exit criteria:** from a tailnet device, run the workflow by hand and see a
grounded research synthesis land in Open Notebook; recovery scripts + stack-map updated.

## Phase 1 — Fan-out (Podcast + OWUI spike)

- [ ] **1.A** Script-render step: wrap a single `result.synthesis` as `EpisodeInput{segments:[{label,items:[{title,url,synthesis}]}]}` (n8n Code node or thin custom node). *Done-when:* produces a valid script string.
- [ ] **1.B** Format→Podcast: `POST open_notebook:5055/api/podcasts/generate` → poll `/api/podcasts/jobs/{id}` → fetch `/api/podcasts/episodes/{id}/audio`. **Bypass `openbrain-podcast`.** *Done-when:* an mp3 episode is produced from one research run.
- [ ] **1.C** Fan-out wiring: one Research node feeds Format→ON **and** Format→Podcast in parallel. *Done-when:* a single run yields both a source and an episode.
- [ ] **1.D** OWUI sink spike (PLAN §5.2 option a): verify OWUI 0.9.x REST chat-create + message-insert with an API key. *Done-when:* either a synthesis appears as a new OWUI chat, **or** the spike is documented as not-worth-it and OWUI is dropped from v1.
- [ ] **1.E** (if 1.D passes) Add Format→OWUI as the third fan-out branch.

**P1 exit criteria:** the CONCEPT §6 graph runs — one research result fans out to
Open Notebook + Podcast (+ OWUI if the spike passed).

## Phase 2 — deferred (out of v1 scope, tracked only)

- [ ] Read-palette nodes (web search, OB search/capture, memory, LLM via llm-gateway, extract).
- [ ] Schedule + Webhook triggers.
- [ ] Action-authorization boundary (CONCEPT §9.2) — required **before** any privileged sink.
- [ ] cloudflared + Authelia exposure (CONCEPT §10 P3).
- [ ] Privileged sinks: email/digest, wiki recompile, little-coder.
- [ ] Optional: package thin custom n8n nodes for the READY surfaces (nicer UX than raw HTTP nodes).
