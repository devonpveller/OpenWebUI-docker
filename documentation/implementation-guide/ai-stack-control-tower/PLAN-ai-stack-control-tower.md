# PLAN — AI-Stack Control Tower (unified oversight, audit & attribution front end)

**Status:** DESIGN DRAFT 2026-08-06 — not built. No code changed yet.
**Owner:** operator. **Branch context:** `feature/orchestration-automations`.

## 1. Problem

The stack has grown to ~60 containers across three compose projects plus host
daemons. Operator questions that are currently hard or impossible to answer in
one place:

- **Where is the GPU going?** (which service/agent/pipeline consumed inference,
  when, how much)
- **What ran?** (research jobs, digest/gap-dive runs, podcast renders, agent-org
  efforts, email ingestion pulls, backups, MCP calls from cloud clients)
- **Can I audit one activity end-to-end?** (pick a digest run → see its LLM
  calls, queue waits, sources fetched, artifacts produced)
- **What is awaiting my decision?** (approvals are spread across MM threads, a
  JSONL relay, and the agent-bridge `pending_approvals` table)
- **Can I act from the same pane?** (workflows, connections, permissions)

## 2. What already exists (survey 2026-08-06)

Four data planes exist today, none share a viewer:

| Plane | Store | Contents | Surfaced today |
|---|---|---|---|
| LiteLLM ledger | `llm-gateway-db` Postgres, `LiteLLM_SpendLogs` | every inference request: tokens, latency, presented API-key string, `end_user` | LiteLLM Admin UI :8445/ui; OWUI `server status` pipe; `modules/llm-traffic` |
| llm-queue | in-mem board + `queue_events` SQLite (`llm-queue-data` vol) | running/waiting/permits, admit/finish/reject events with wait & duration | `/observe/*` pass-through → status pipe only; SQLite is write-only in practice |
| agent-bridge | `agent-bridge-db` Postgres (27 tables) | efforts FSM, `pending_approvals`, reviews, events, kill_switch | Mattermost effort threads only; loopback :8830 |
| Host ops | PS logs, `scripts/sysadmin-mcp/state|bridge-state/*.jsonl`, MM `#claude-code`/`#sysadmin`, Telegram OOB | health verdicts, backup freshness, disk audits, sysadmin approvals | transient MM/Telegram posts; no queryable history |

Also relevant:

- **No Prometheus / Grafana / cAdvisor / GPU exporter anywhere.** One orphan
  exporter exists: little-coder Prometheus at `127.0.0.1:9091`, unscraped.
- **GPU telemetry** = `nvidia-smi` shell-outs (device-level only; no per-caller
  signal). Per-caller compute attribution can only come from the LiteLLM ledger.
- `config/llama-swap.config.yaml` does **not** pass `--metrics` to llama.cpp, so
  model servers expose no Prometheus endpoint today.
- **openbrain gmail pull**: dedup/state in `openbrain-db`
  (`content_fingerprint`), report artifacts to `D:\_data`, logs to stdout only.
- **Cloud-client OpenBrain/mnemory use** goes through the MCP privacy proxies
  (`mnemory-gateway` :8060, `openbrain-gateway` :8061) — tool-level access, no
  inference, and currently no structured request log.
- Prior art that this plan must compose with, not duplicate:
  - `documentation/implementation-guide/ai-stack-user-created-automations/` —
    n8n locked as a **separate compose project** for user automations; its
    CONCEPT §9.2 names the confused-deputy / action-authorization problem.
  - `documentation/implementation-guide/supervised-research-pipeline/` Phase 3 —
    job-scoped spend attribution with the `end_user = <lane-class>:job-<id>`
    convention (lane class first, because llm-queue classifies by substring).
  - `documentation/implementation-guide/LiteLLM-Proxy/guide-LiteLLM-Proxy.md`
    §keys — parked master_key + per-service virtual-keys plan.
  - Governance plane: teams-chat governance model, agent-org floor/charters,
    `governance_gate.py`, approvals MCP + `pending_store` — the existing
    permissions/approval machinery.

## 3. Attribution gaps (measured, with evidence)

Ledger identity = presented `Authorization` string (permissive mode logs it
verbatim, distinct strings → distinct rows). Queue identity = the OpenAI `user`
field only, because LiteLLM rewrites `Authorization` to `Bearer dummy` before
llm-queue (`config/litellm.config.yaml:94-105`; sentinel fallback in
`llm-queue/src/llm_queue/routes/data.py:65-97`).

| Caller | Key at ledger | `user` field | Verdict |
|---|---|---|---|
| agent-org bridge (PM/PO/planner/workers/reviewers) | `agent-org` | per-role (`agent-org-pm`, …) | **best in stack** |
| OWUI chat / embeddings | `owui-chat` / `owui-embed` (UI-configured, not in repo) | none | OK, verify in UI |
| mnemory | `ollama` | none | misleading name |
| little-coder standalone | `llama` | none | collides with ao-workers |
| ao-workers (little-coder instances) | `llama` | none | **not separable from little-coder** |
| entire OB1 fleet (mcp, entity, wiki, curator, chunk, suggestion, extract, workbench, grounding) | `not-needed` | none | **one identity for ~9 services** |
| openbrain-research | `not-needed` | `ob-research` (notebook-origin only) | partial |
| idea-refinery | `not-needed` | `idea-brainstorm` | OK |
| digest/podcast LLM polish (`daily-digest` LlmClient) | `no-key` | none | **anonymous** |
| open_notebook (embed) | `not-needed` | none | folded into OB1 blob |

Known bug: `modules/llm-traffic/service/llm_traffic.py` friendly-name map says
`ollama → openwebui`; compose proves `ollama` is **mnemory**
(`docker-compose.yml:356`).

## 4. Design

Read-first, act-through-existing-gates. Four phases; each independently
valuable and rollback-able.

### Phase 1 — Attribution standard (prereq; env/config-only, no new containers)

You cannot audit what you cannot attribute. Adopt one convention:

> **Every inference caller sends (a) a distinct plaintext API key = its service
> identity (ledger), and (b) a `user` field starting with its llm-queue lane
> class, optionally `:job-<id>` suffixed (queue lane + per-run audit).**

- Permissive mode is kept — distinct plaintext keys already produce distinct
  ledger rows; **no master_key needed for attribution**. The parked
  virtual-keys plan remains the later hardening step and composes cleanly.
- Assign keys: `ob-mcp`, `ob-entity`, `ob-wiki`, `ob-curator`, `ob-chunk`,
  `ob-suggest`, `ob-extract`, `ob-workbench`, `ob-grounding`, `ob-digest`,
  `ob-podcast`, `on-embed`, `ao-worker` (split from `lc-coder`), `mnemory`
  (rename from `ollama`). Keep `owui-chat`/`owui-embed`, `agent-org` as-is.
- Keep `llm-queue/src/llm_queue/policy.py` `_DEFAULT_CLASSES` in sync — the
  class names it already provisions (`ob-entity`, `ob-wiki`, `ob-research`,
  `ob-podcast`, …) become the canonical lane prefixes. **Gotcha:** substring
  classification means a wrong prefix silently changes a caller's lane.
- Fix the `llm-traffic` friendly-name map; de-duplicate it with
  `tailscale_serve_pipe.py` into one shared mapping file.
- Rollback: revert env vars; old keys keep working (permissive).

### Phase 2 — Observatory (read-only console; the actual front end)

New **separate compose project `observatory`** (mirrors the n8n decision:
own project, attaches to `ai-stack_llm-net` etc. as external networks):

- **Prometheus** — scrapes: cAdvisor (container CPU/mem/net), little-coder
  :9091 (already exists), llm-queue (add a small `/metrics` endpoint), a GPU
  exporter (see build-time checks), and llama.cpp `--metrics` if enabled.
- **Grafana** — tailnet serve **:8447**, dashboards over:
  - Prometheus (GPU, containers, queue depth over time),
  - `llm-gateway-db` (read-only Postgres user) — spend/tokens by key, by
    `user` lane, by hour; "who used the GPU today" as a graph, not a chat pipe,
  - `agent-bridge-db` (read-only user) — efforts timeline, pending approvals
    count, freeze/kill-switch state,
  - `openbrain-db` (read-only user) — research_jobs, ingestion counts,
    gap-dive ledger.
- **Approvals board**: one Grafana table panel unioning
  `agent-bridge.pending_approvals` + sysadmin `approvals.jsonl` (small
  file-to-table shim) → "what is awaiting me". Clicking through still resolves
  in Mattermost — the board is read-only.
- Small config changes in the main stack to feed it:
  - `config/llama-swap.config.yaml`: add `--metrics` to common-args
    (build-time check: confirm llama-swap proxies `/metrics`, and that the flag
    doesn't disturb `--no-mmap`/ckpt behavior).
  - llm-queue: expose `queue_events` (Prometheus counters and/or a paged
    read-only `/observe/events`).
  - `mnemory-gateway` + `openbrain-gateway`: add JSONL request logs (tool,
    client, ts) so **cloud-client memory/OB use becomes auditable**; scrape or
    ship to Grafana via the same shim as approvals.
- New-container discipline applies: compose + `emergency-recovery.ps1/.bat`
  inventory + `/stack-map` doc + CLAUDE.md + a `grafana-backup` sidecar
  (dashboards provisioned as code in-repo minimizes what a backup must hold).
- The existing surfaces stay: LiteLLM Admin UI :8445 = LLM deep-dive; OWUI
  `server status` / `llm traffic` pipes = chat-native quick checks; Grafana =
  the overview + history + graphs layer above them.

### Phase 3 — Activity threading (audit any run end-to-end)

Generalize supervised-research Phase 3's convention to every pipeline:

- Digest runs, gap dives, podcast renders, idea-refinery cycles, agent-org
  efforts stamp `user = <lane>:job-<id>` on every LLM call they make; each
  pipeline already has a natural run id (research_jobs.id, effort id, digest
  date). gmail-pull gets a minimal runs record (start/end/counts) in
  openbrain-db next to its existing fingerprint dedup.
- One Grafana "activity" dashboard: pick a job id → its spend rows, queue
  events, research_jobs trace, effort thread permalink, artifacts.
- This phase is incremental per-pipeline and rides entirely on Phase 1's
  convention; no schema coupling across DBs (join at query time).

### Phase 4 — Action plane (workflows, connections, permissions)

Explicitly **not** a new authz system and **not** buttons wired into Grafana:

- **Workflows** → the n8n automations project (its own open decision gate);
  Control Tower only *observes* n8n runs once it exists.
- **Component actions** (restart, prune, compact) → the existing gated
  sysadmin-mcp + @bot-sysadmin, which already has fail-closed approvals and a
  JSONL audit. Candidate growth: a `service_restart` tool with the same gates.
- **Permissions over components** → the existing capability plane: llm-queue
  per-key policy/budgets (`LLM_QUEUE_ENFORCE_BUDGET` exists, currently off —
  Phase 1's clean identities are the prerequisite to ever turning it on),
  later LiteLLM virtual keys, agent-org scope grants/egress_hosts.
- Human decisions stay in Mattermost (Telegram OOB fallback), per the
  established governance model. The front end shows state; it does not hold
  the pen.

## 5. Open decisions (operator)

1. **Grafana/Prometheus vs extending bespoke pipes.** Recommended: Grafana —
   the audit questions are SQL over three Postgres DBs plus time-series, which
   is exactly Grafana's shape; bespoke pipes stay for chat-native quick views.
2. **Separate `observatory` compose project vs main stack.** Recommended:
   separate project (isolation, mirrors n8n precedent, keeps recovery scripts'
   main-stack inventory stable — recovery treats it as optional like OB1).
3. **GPU exporter choice on Docker Desktop/WSL2** — dcgm-exporter vs
   nvidia_gpu_exporter vs a host-side nvidia-smi textfile script. Needs a
   build-time spike; the fallback (host script → Prometheus pushgateway or
   textfile) is known-workable.
4. **Now vs later: master_key + virtual keys.** Recommended later; Phase 1
   gets full attribution without it.

## 6. Risks / gotchas

- llm-queue substring lane classification: a mis-prefixed `user` silently
  reroutes a caller's priority lane (same trap supervised-research Phase 3
  documents). Mitigate: one canonical prefix table in the plan + a check
  script alongside `check-llm-gateway-routing.ps1`.
- `/spend/logs` unpaginated already times out (ledger size) — all readers must
  use paginated endpoints or direct read-only SQL; consider a retention/rollup
  policy for `LiteLLM_SpendLogs` before Grafana makes it popular.
- Grafana/Prometheus containers add disk + memory on an already-tight host —
  size retention (e.g. 15d Prometheus, rely on Postgres ledgers for history).
- Read-only DB users must be genuinely read-only (`GRANT SELECT`), created via
  migration notes in this dir, or the console becomes an unaudited write path.
- Do not probe LiteLLM `/health` via the alias (model-load thrash) — Grafana
  health checks target `/health/liveliness` only (existing rule).

## 7. Suggested build order

1. Phase 1 (half a day: env edits + policy sync + traffic-map fix + verify
   distinct rows appear in `LiteLLM_SpendLogs`).
2. Phase 2 skeleton (observatory project: Prometheus + Grafana + cAdvisor +
   one spend dashboard + tailnet :8447), then GPU exporter spike.
3. Approvals board + privacy-gateway JSONL logs.
4. Phase 3 pipeline-by-pipeline, starting where run ids already exist
   (research_jobs → digest → podcast → efforts).
5. Phase 4 only via the existing governed surfaces, no new authz.
