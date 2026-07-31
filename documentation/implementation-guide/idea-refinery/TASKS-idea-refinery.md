# TASKS — Idea Refinery (build ledger)

**Companion to** [`DESIGN-idea-refinery.md`](DESIGN-idea-refinery.md) (all decisions resolved
2026-07-25). This is the buildable breakdown of phases **IR.0–IR.7**. Each phase is deployable,
tested, and reversible; **deployment to the live stack is a separate, operator-run step** gated by
the §12.1 evidence bar — this ledger produces artifacts, it does not auto-deploy.

**Status:** 🟢 **COMPLETE + 100% LOCAL — 2026-07-26.** The full Idea Refinery is deployed and running,
entirely on local hardware: capture (bge-m3), nightly research (qwen36-27b + bge-m3 + private SearXNG/
Tor), gap-centered dossier delivery to `#ideas`, dirty re-research, fizzle/resurface, backfill (32 ideas
parked), health-monitoring + recovery, and the **local brainstorm** (`brainstormLoop` — qwen via the
gateway; no cloud, no cloud MCP). Rollout gate `test-e2e.sh` 🟢 20/20. IR.0–IR.7 all done. (The initial
cloud claude-sessions-bridge brainstorm was reverted for privacy — see IR.4/IR.6.)

---

## Architecture decisions taken at build time (refinements to the design)

- **`ideas` / `idea_revisions` live in the OB Postgres** as a new numbered init file
  `OB1/docker/init-ideas.sql` → mounted `98-init-ideas.sql`, **additive + operator-applied** to the
  live DB (the init scripts only auto-run on a fresh volume — same as `init-claims.sql`).
- **UUID primary keys** for `ideas` (via `gen_random_uuid()`), consistent with `claims`/`threads`/
  `sources` — avoids slug-collision handling. (Design wrote `idea-<slug>`; UUID is the impl choice.
  `title` remains the human handle; `find_idea` resolves by title/embedding.)
- **The MCP tools (`capture_idea`/`update_idea`/`find_idea`/`research_idea`) live in the OB MCP
  server** (`OB1/integrations/kubernetes-deployment/index.ts`, where `capture_thought` lives) so
  existing clients (OWUI, Claude Desktop) get them with no new connector. They write the
  refinery-owned `ideas` tables in the same DB. The **refinery service** owns the batch/drain/
  delivery, not the MCP surface.
- **The refinery service** `openbrain-idea-refinery` is a Deno HTTP service under
  `OB1/integrations/openbrain-idea-refinery/`, mirroring `research-service` — a `POST /run` batch
  handler in the `openbrain-cron` scheduled slice (§6.1/§6.2 of the design).
- **No new inference path / no new GPU governance** — research goes through the existing
  `openbrain-research` (`POST /research`) under a distinct `idea-refinery` llm-queue origin.

---

## IR.0 — Idea aggregate + deterministic inlet

**Goal:** the durable data model + the MCP inlet tooling. Additive, low-risk (no GPU, no inference).

- [x] **IR.0a — Schema.** `OB1/docker/init-ideas.sql` — `ideas` + `idea_revisions` + the
  `ideas_owed_research` read-view + `ideas_touch_updated_at` trigger + RLS/grants. Idempotent,
  additive, no DROP/TRUNCATE (OB1 guardrails). *Done + **validated 2026-07-25** in a throwaway
  `pgvector:pg16` container (owed-queue/coalesce/trigger/CHECK/policies all pass); live
  `openbrain-db` untouched.*
- [x] **IR.0b — Mount.** `98-init-ideas.sql` mount added to `OB1/docker/docker-compose.yml`
  (fresh-volume installs). *Live-DB apply via `psql` is an operator deployment step (see below).*
- [x] **IR.0c — MCP tools.** Added `capture_idea`/`update_idea`/`find_idea`/`research_idea` to
  `OB1/integrations/kubernetes-deployment/index.ts`, mirroring `capture_thought`/`search_thoughts`
  (bge-m3 embed via `getEmbedding`, raw-SQL writes on the `ResilientPool`). `capture_idea` embeds +
  **dedup-asks** at ≈0.70 sim (DT-3, ask-don't-merge, `force_new` override) + writes idea+thought+
  revision in one tx; `update_idea` appends a revision, **no-op-guards** identical text, sets
  `dirty`; `find_idea` = embedding search; `research_idea` sets a `metadata.research_now` flag
  (the drain honors it in IR.1; the MM thread pre-create + permalink move to IR.2). Tools return
  **text acks** — research lands in Mattermost, not OWUI (§7). *Done + **validated 2026-07-25**:
  every tool's SQL exercised in a throwaway container (capture/dedup 1.0-vs-0.0/coalesce/find/flag/
  lineage all pass); `deno check index.ts` clean; live server untouched.*
- **Tests done:** capture → one `ideas` row + revision 1 + linked thought; update → new revision,
  `current_revision` bumps, status `dirty`, only latest revision owes; near-dup detection at the
  threshold; `find_idea` resolves by embedding; no-op guard on identical text. **Deferred to
  staging (needs live embed + MCP runtime):** end-to-end tool call through OWUI.

## IR.1 — Research the batch (the throttled drain) — ✅ built + tested (not deployed)

- [x] Refinery service `OB1/integrations/openbrain-idea-refinery/` (Deno `POST /run` + `/health`,
  `deno.json`, `Dockerfile`). The **submit-on-complete drain** (§6.2): reads the owed set (priority
  `research_now` → fresh → oldest; attempts-capped), submits to `openbrain-research` (origin `agent`
  — the CHECK-constrained lane; our own K-bound is the throttle), polls to completion, marks
  `researched`; coalesces (only the current revision), rolls over on wall-budget, gives up after 3
  attempts. Mattermost `deliver()` is a **stub** (IR.2). `deno check` clean.
- [x] Compose entry in `OB1/docker/docker-compose.scheduled.yml`, **profile-gated `idea-refinery`**
  (inert until IR.2) + cron line `0 3 * * *` UTC in `OB1/docker/cron/crontab` (`|| true`, harmless
  until live).
- **Tests done (integration, throwaway DB + mock research + built service):** correct selection
  (fresh + `research_now` priority, attempts-cap excluded), deliberate re-research overwrites the
  stamp + clears the flag, owed drains to 0, `{selected:2, succeeded:2, failed:0}`. **Deferred to
  staging:** real research jobs (GPU) + the live delivery (IR.2).
- **At deploy (after IR.2):** `--profile idea-refinery up -d openbrain-idea-refinery`; sync the
  recovery-scripts inventory + `/stack-map` (new container).

## IR.2 — Deliver to Mattermost — ✅ built + tested (not deployed)

- [x] On research completion (or a **redelivery pass** for a prior run's failed post — P25),
  `deliverForIdea` renders the gap-centered dossier (§3.1) and posts it: creates the idea's `#ideas`
  thread (root = idea) or appends an UPDATE to the existing `thread_root`; sets `thread_root` +
  status `researched`. Mattermost reached via `host.docker.internal:8065` (agent-org project),
  bot-token auth, `ensureChannel`/team resolution mirrored from the agent-bridge adapter. Compose
  env + `extra_hosts` added.
- **Tests done (mock Mattermost + mock research + built service):** fresh → new thread; dirty →
  UPDATE appended, root preserved; attempts-capped skipped; **redelivery pass recovers a
  researched-but-undelivered idea** (`delivered_pending:1`). `deno check` clean.
- **Deferred (IR.2b):** `research_idea` pre-creating the thread + returning a live OWUI permalink
  (needs Mattermost access in the MCP server); `dossier_source_id` linkage.

## IR.3 — Dirty → continuation — ✅ built (folded into the drain)

- [x] `ideaQuerySeeded` seeds a dirty re-research with the **prior dossier** (fetched from the prior
  `last_job_id`), so the model builds on earlier findings and focuses on what changed; the UPDATE
  appends to the same thread (IR.2), revision lineage intact. First research uses the base query.
- **Tests:** the update/append path is covered by IR.2's dirty case + `deno check`; the seed *content*
  (prior synthesis in the query) is additive and not separately asserted.

## IR.4 — Brainstorm engagement (gap-driven honing)

- [ ] Lazy-seed the bound headless session on first reply (bridge enhancement: inject the idea's
  dossier as first-turn context for an `#ideas` thread); bind `session_id`.
- [ ] The **grounded document-template** accretion (§7.1/§9): honing fills named slots with
  accredited content only (grounded claim or attributed human decision); `[SOURCED]/[GAP]` tagging;
  no invented facts. Labeled gaps (G1/G2); NL grammar.
- **Tests:** session seeded with dossier, reply=resume continuity, brief slots only accept accredited
  content, ungrounded → `[GAP]`.

## IR.5 — Fizzle / revive / resurface — ✅ built + tested

- [x] `ageIdeas` (runs first in the drain, before research so fresh work isn't aged): researched +
  `engaged_at IS NULL` + older than `IDEA_DORMANCY_DAYS` (14) → `dormant`, silently (no nag).
  `nearDormant` resurfaces: a new idea's dossier gets a cross-link to dormant ideas within
  `IDEA_RESURFACE_MAX_DIST` (0.40 cosine). Revive-on-edit = `update_idea`→`dirty` (drain re-owes);
  rummage = `find_idea` (dormant ideas are searchable). `deno check` clean.
- **Tests done (integration):** stale-unengaged → dormant; engaged idea NOT aged; dormant idea stays;
  fresh idea researched (not aged this run); resurface cross-link lands in the *new* idea's dossier.

## IR.4 + IR.6 — Engagement cluster — ✅ built + tested + DEPLOYED (LOCAL brainstorm)

> **Grounded redesign 2026-07-27 — ✅ built + verified + DEPLOYED (supersedes the plain-chat brainstorm).**
> The plain local-chat brainstorm answered from model memory (hallucination-prone). It is replaced by a
> **grounded research consultant** with the SAME Open Brain MCP tooling OWUI has (Option A, operator-
> approved). Design: `DESIGN-idea-refinery.md` §7.2 / §7.2.1 / §7.2.2. In `openbrain-idea-refinery/index.ts`:
> an inline **MCP client** to `openbrain-mcp` (raw JSON-RPC streamable-HTTP, `x-brain-key`=`MCP_ACCESS_KEY`,
> ported from `tools/owui-knowledge-to-openbrain/promote.py`) exposing the 24 core tools (default-scoped to
> the read/grounding set via `IDEA_BRAINSTORM_TOOLS=read`; `all` opens writes). The rewritten
> `brainstormReply` runs the enforcement loop: **Gate A** forces `search_claims` first → bounded native
> tool-calling loop (OWUI parity, `MAX_TOOL_ITERS=2`, `EV_CAP=18`) → grounded numbered draft citing evidence
> ids → **Gate B** `validateLines` re-checks each line against ONLY its cited evidence in batches
> (`VAL_BATCH=6`) and strips the unsupported → posts grounded bullets with a "Grounded in:" provenance key,
> **or** collapses to a research **GAP** (`gapResearch` fires the local engine + follows up in-thread). Never
> answers from model memory. **Verified** end-to-end in throwaway Deno containers vs the LIVE local services
> (MCP transport, native `tool_calls`, multi-turn loop, draft, validation-strip, gap collapse), `deno check`
> clean. **Deployed** by `docker restart openbrain-idea-refinery` (bind-mount, no image rebuild); post-restart
> the deployed container's own MCP handshake returns 200 / 24 tools / a real grounded claim. Acceptance test:
> reply under a `#ideas` dossier → grounded cited bullets, or "researching that gap" + a 🔬 follow-up.
> **Note:** the claude-sessions-bridge `#ideas` path below (`BRIDGE_IDEAS_CHANNEL_ID`) is a SEPARATE, older
> lane and is NOT part of this local service; leave it disabled to avoid a double-responder in `#ideas`.

> **Privacy correction 2026-07-26:** the cloud claude-sessions-bridge approach documented below was
> **reverted** — it routed the brainstorm to **cloud Claude**, violating "all Open Brain interactions
> local." The brainstorm is now **LOCAL**: `brainstormLoop` in `openbrain-idea-refinery` polls `#ideas`
> and hones an operator's reply with local **`qwen36-27b`** (via `http://llama-cpp:8080` — the same
> gateway the research uses). It reconstructs the thread (dossier→system, refinery-bot→assistant,
> operator→user) and replies in-thread; "make this real"/"promote" → Project Design Brief. `deno check`
> + an isolation test (mock MM + mock local LLM) pass; **deployed live** ("brainstorm loop live on
> #ideas (LOCAL model qwen36-27b …)"). Nothing leaves the box. The bridge edits are fully backed out
> (compiles clean, zero remnants); the `BRIDGE_IDEAS_CHANNEL_ID` line in `OB1/docker/.env` is inert
> (safe to delete). The cloud-bridge notes below are kept for history only.

- [x] `scripts/claude-sessions-bridge/bridge.py` extended **additively** (compiles; **INERT unless
  `BRIDGE_IDEAS_CHANNEL_ID` is set** — the `#claude-sessions` path is byte-identical): a separate
  `poll_ideas_once()` watches `#ideas` (own cursor, shares the dedup + worker/queue); `post()` is now
  channel-aware via `THREAD_CHANNELS` (defaults to `CHANNEL_ID`); on a NEW session in an `#ideas`
  thread, `execute()` seeds the prompt with the thread's dossier + the gap-driven honing / **promote**
  role (`IDEAS_SEED_TEMPLATE`) — IR.4 + IR.6 both fall out of the seed. Boot self-joins `#ideas` +
  rehydrates the channel map. `BRIDGE_IDEAS_CHANNEL_ID` is also readable from the token env file.
- **Enable (operator):** ✅ `BRIDGE_IDEAS_CHANNEL_ID=9rkoru1ufib17ympngqebmwa9e` set in
  `OB1/docker/.env` (the bridge now reads that file too, not just `agent-org/docker/.env`).
  **Remaining = restart the bridge:** Stop/Start the `claude-sessions-bridge` Scheduled Task **between
  turns** (a restart kills an in-flight bridge session — including the live conversation). On boot the
  log shows "idea-refinery brainstorm ENABLED" + a self-join of `#ideas`; then a reply under a dossier
  in `#ideas` starts a seeded brainstorm.
- **Testing note:** the bridge can't be isolation-E2E'd (it drives headless claude + MM); the safety
  net is the inert-without-env guard + `py_compile` + the additive design + live verification.

## IR.6 — Promote → Project Design Brief

- [ ] `promote` finalizes the accreted grounded template into the brief; user-chosen hand-off
  (agent-org `/nl` / PDL / export / shelf); status `promoted`; provenance links resolve.
- **Tests:** brief structure + citations, hand-off only on explicit command, provenance resolves.

## IR.7 — Migration / backfill (post-go-live) — ✅ built + tested

- [x] `migrate-ideas-backfill.py` (standalone, psycopg2): each OB idea-typed thought → a first-class
  `ideas` row (+ revision linked to the thought, embedding reused), `dormant`. **Default = parked/
  on-request** (a `backfill-parked` sentinel keeps it out of the owed queue; surfaced via `find_idea`,
  researched only on `research_idea`); `--research` makes them owed for a slow trickle. Idempotent
  (skips already-migrated + non-idea thoughts); `--limit` / `--dry-run`.
- **Tests done:** migrates only idea-typed + not-already-migrated (1 of 3 seeded), embedding copied,
  dormant + parked (0 owed), re-run migrates 0.
- **OD (IR.7) resolved:** the safe **parked/on-request default** is implemented — most parked ideas
  stay parked, not auto-researched. Flip to `--research` to slow-trickle the whole history.

## Rollout gate (§12.1, before go-live)

- [x] Per-phase integration harnesses (throwaway DB + mock research + mock Mattermost + the built
  service) cover IR.1–IR.3, IR.5, IR.7 end to end; IR.0 proven live.
- [x] **Consolidated, repeatable E2E harness** — `test-e2e.sh` (throwaway DB + mock research + mock
  Mattermost + built service): drives select/priority/cap → research→deliver → dirty-append →
  redelivery → dormancy → resurface → backfill in one run, **20/20 assertions 🟢 GREEN**, exit 0 =
  gate passed. Repeatable regression check; nothing touches live.
- [ ] Staging run against a handful of real ideas in a test channel (after the drain is enabled) —
  operator-gated.
- [ ] Only then: run the backfill (IR.7) — operator-gated.

---

## Deployment steps

**IR.0 — ✅ EXECUTED + smoke-tested live 2026-07-25** (operator-approved):
1. ✅ Applied `init-ideas.sql` to live `openbrain-db` (2 tables, 1 view, 4 policies).
2. ✅ Rebuilt `openbrain-mcp` (rollback image tagged `openbrain-mcp-server:backup-preir0`) + recreated
   the container (clean "Listening on :8000"); restarted `openbrain-mcpo` → `/capture_idea`,
   `/update_idea`, `/find_idea`, `/research_idea` exposed to OWUI (24 endpoints).
3. ✅ End-to-end smoke test: `capture_idea` via mcpo → real bge-m3 embed → live DB row (idea
   `156671a2-010a-445f-97ae-7a9d172e92f2`, "Embedding result cache", owed). Left as the first real
   idea (delete anytime; rollback image can be pruned once trusted).

**IR.1–IR.3/IR.5/IR.7 drain — ✅ DEPLOYED + staged live 2026-07-25** (operator-approved):
1. ✅ `--profile idea-refinery up -d openbrain-idea-refinery` (healthy, DB + MM reachable via
   `host.docker.internal:8065`, token `IDEA_REFINERY_MM_TOKEN`).
2. ✅ Staging run: real research on "Embedding result cache" → dossier delivered to `#ideas` thread
   `ktm8jenzy7n39eic198iriqaxc` (`{selected:1, succeeded:1}`).
3. ✅ Cron enabled: `restart openbrain-cron` loaded the `0 3 * * *` line.
4. ✅ Backfill: `migrate-ideas-backfill.py` migrated 32 idea-typed thoughts (parked), idempotent.

**Drift-sync — ✅ done 2026-07-26** (the CLAUDE.md "three places" for adding a container):
- `scripts/check-openbrain-health.ps1` — liveness probe for `openbrain-idea-refinery` (running=ok,
  exited=fault+`docker start`, absent=WARN since profile-gated). Parses clean.
- `scripts/emergency-recovery.ps1` + `.bat` — added to the OB1 inventory; **every OB1 `up` now uses
  `--profile idea-refinery`** so recovery/nuclear/gpu-reset restart the drain. Parse clean.
- `.claude/skills/stack-map/references/workspace-stacks.md` + root `CLAUDE.md` — inventory rows added.

**Still to do:**
- Nothing — IR.0–IR.7 are all built + deployed (100% local).
- Nothing touched `main`, inference routing, or the GPU beyond the throttled research origin.

**OpenRouter route (parked — grounding + open questions, NOT designed):**
[`PLAN-openrouter-cloud-route.md`](PLAN-openrouter-cloud-route.md). Cloud tags are an **inbound
read-filter**, not an export path; the Idea Refinery must **not** export idea/dossier data to cloud
(stays 100% local). The agent-org OpenRouter lane is **undeployed + placeholder-model**. The route's
actual purpose / data-flow is an **open question for the operator** — not to be invented.
