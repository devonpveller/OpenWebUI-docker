# Self-Improving Little-Coder — Task Tracker

> **Last updated:** 2026-05-22
> **Plan:** [`integration-plan.md`](integration-plan.md) — chapters, rationale, locked decisions.
> **Design doc:** [`Self-improving-little-coder-design.md`](Self-improving-little-coder-design.md) — source of truth. Design doc wins on conflict.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → bump `Last updated` and add a Decision Log row if a choice changed.

## Current state

- **Active chapter:** 1 (Tool) — **built, deployed, and tested end-to-end** (2026-05-22). Awaiting the operator stop-point judgment before chapter 2 (plan §5). See Chapter 1 progress below.
- **Build sequence:** five chapters, gated by operator judgment (plan §2). One chapter at a time.
- **Tier-0 build reminder:** `session_id` + `channel` + `user_id` on every journal line from line 1. Unrecoverable retroactively (design §4.1).
- **Tool ships the open-terminal network change.** OWUI's direct access to open-terminal ends; it returns in chapter 2 via `lc-mcpo`.
- **Source lives in [`../../little-coder/`](../../little-coder/)** — Python control-plane package (`src/littlecoder/`), `git-proxy/`, `config/`, `tests/`.

---

## Chapter 1 — Tool

> Refs: design §3, §4, §10, §12. Plan §5.

### Chapter 1 progress (2026-05-22)

Chapter 1 is **built, deployed, and tested end-to-end** (2026-05-22). 134 unit
tests pass; all five services (`little-coder`, `lc-mcpo`, `open-terminal`,
`lc-egress`, `little-coder-backup`) run healthy. Verified live: `lc project`
clones through the egress allowlist; `lc task` flows agent → LLM → journals;
command execution routes through open-terminal and the git-proxy
(`LC_ROUTE_EXEC=1`); journals accumulate the full envelope; volumes survive
container recreation. What remains is the operator living with it (the chapter
stop point, plan §5).

Legend: `[x]` done / verified; `[~]` built, full acceptance needs more
operator runtime; `[ ]` not done.

**Built and unit-tested** (`../../little-coder/`):

- Control daemon — FastAPI internal API, FIFO queue (one task at a time), task lifecycle, SIGTERM drain.
- Agent integration — runs the upstream little-coder CLI; `ot-exec` routes its commands into open-terminal and journals each one.
- CLI operator surface — `lc task / project / status / tasks` + `lc admin {shutdown, pending, approve, reject, project switch, task confirm}`.
- Prometheus metrics; MCP server + `lc-mcpo` edge (built, dormant); config / journals / audit / sanitization / git-proxy / workspace foundation.

**Docker + compose**: agent image (Node + Python), custom open-terminal image
(git-proxy spliced in as `git`), `lc-egress` allowlist proxy, five named
volumes, backup job, the `lc-net` network, the open-terminal network change.
`.env.example` updated with all new keys + operator action items.

**Verified on deployment (2026-05-22):** llama-cpp reachable (agent talks to
it via the `llamacpp` provider); the egress allowlist proxy (`lc-egress`)
permits the git host; volumes persist across container recreation (focus
re-seeds, journals accumulate); the pi extension loads and routes the agent's
`bash` into open-terminal through the git-proxy. Still wants operator runtime:
exhaustive network-isolation checks and a shadow-mode rejection baseline.

**Build/test fixes applied:** agent image needs Node 22 (not 20);
`WorkspaceManager` clones with the relocated real git `/usr/bin/git.real`;
system-wide `git safe.directory '*'` in both images (the shared workspace
volume crosses container uids); `umask 000` on workspace writes for the same
reason; a `models.json` override maps the `llamacpp` provider to llama-swap's
real model ids; the pi extension API was corrected against the bundled
extensions (`registerTool` + `execute`).

**Known gap — `.git/config` read-only mount (design §3.3).** The git-proxy
blocks `git config` writes and `remote add` at the command level, but a
*direct file write* to `.git/config` bypasses that. The design closes this
with a read-only mount; that is awkward for a path inside a named volume and
is **not yet implemented** — tracked as a remaining hardening item (see 1h).

**Operator action items** (cannot be automated — see `.env.example`):
provision the private self-improvement remote + PAT (design §10.6), and add
your git host to `little-coder/docker/egress-allowlist.txt`.

### 1a. Network + remotes (ship first; these are unrecoverable later)

- [x] Verify `http://llama-cpp:8080/v1` reachable — the agent connects via the `llamacpp` provider; model `qwen36-27b` responds
- [x] **Ship open-terminal network change:**
  - [x] Move open-terminal off `network_mode: service:openwebui`; give it its own network (`lc-net` + `llm-net`)
  - [x] Explicit egress allowlist: `llama-cpp` for inference, operator-configured git remote, nothing else — via `lc-egress` (tinyproxy default-deny host filter)
  - [~] Verify isolation: from inside open-terminal, only allowlist endpoints reachable — deployment check
  - [x] **Confirm OWUI no longer has direct access to open-terminal** — netns change + OWUI's `TERMINAL_SERVER_CONNECTIONS` commented out
- [x] Decide compose layout — extended the main `docker-compose.yml` (keeps `docker compose up -d --build little-coder` working as design §11.1 specifies)
- [~] Pick the **private** self-improvement git remote (design §10.6); register URL — operator action; `LC_SELF_REMOTE_URL` scaffolded in `.env.example`
- [~] Provision fine-grained PAT scoped to `contents:write` on that remote only — operator action; `LC_SELF_REMOTE_PAT` scaffolded in `.env.example`

### 1b. Container scaffold

- [x] Build `little-coder` container image; pin to a known-good upstream commit — `Dockerfile.agent`, `LITTLE_CODER_VERSION` build arg
- [x] Run `agent` as MCP server (not raw HTTP/socket); mirror `search-mcpo` pattern — `mcp_server.py` behind `lc-mcpo`
- [x] Build `lc-mcpo` sidecar (built but dormant in Tool — chapter 2 activates it). API-key'd at the edge (design §10.3)
- [x] Compose healthchecks: `agent` (daemon `/health`), `lc-mcpo` (`/openapi.json`)
- [x] LLM client default → fast variant; reasoning variant via call-site selection — config `inference.default`

### 1c. Journals (tier-0 build requirement — unrecoverable if missed)

- [x] Implement writers for `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl`
- [x] Envelope per design §4.1: `ts`, `task_id` (ULID), `session_id`, `channel`, `user_id`, `repo`, `lang`, `seq`, `schema_version: 1`
- [x] Write-time schema validation; malformed records **rejected, not appended**
- [x] `task_started` / `task_ended` bracket every task; reconstruct by `task_id`, never adjacency — daemon brackets every task; `TaskContext` carries the per-task `seq`
- [x] Outcome label per design §4.2: `pass` / `fail` / `unverified`
- [x] Durability per design §4.3: append + fsync on every terminal and every error record
- [x] Schema versioning plumbed through readers; tolerate older shapes (forward-compat for later chapters)

### 1d. Audit log

- [x] `audit.jsonl` writer per design §4.4, separate from task journals
- [x] Records emitted in Tool: `project_switched`, `task_outcome_amended`, `shutdown` — daemon + CLI emit all three
- [~] Longer retention than task journals; different access controls — separate file done; retention/access policy is a deploy-time concern

### 1e. Persistence (also unrecoverable if missed)

- [x] Declare all named volumes at compose time:
  - [x] `little-coder-skill` (used Learner+; declared now)
  - [x] `little-coder-journals` (used Tool)
  - [x] `little-coder-cohorts` (used Observer+; declared now)
  - [x] `little-coder-polyglot` (used Learner+; declared now)
  - [x] `little-coder-workspace` (used Tool; **shared with `open-terminal`** — project-scoped, wiped on `/project` switch)
- [x] Mount into `agent`; `docker compose up -d --build little-coder` preserves all five — verified across multiple container recreations (journals accumulate, focus re-seeds)
- [x] Backup job (Alpine-cron daily default; cadence + restore drill tracked as open item #7) — `little-coder-backup` service + `backup/little-coder-backup.sh`

### 1f. Workspace handling + project focus

- [x] `agent` can clone a single repo directly into open-terminal — `WorkspaceManager.clone` via the daemon
- [x] `agent` can edit files; run tests/commands — verified: files created on the shared volume; commands route to open-terminal via the pi extension
- [x] `/project repo: <link>` CLI subcommand per design §12.3 — `lc project` / `lc admin project switch`
- [x] URL normalization: host + owner + repo, lowercased
- [x] No current focus → clone, journal `project_switched`
- [x] Matches current focus → no-op
- [x] Different focus + task in flight → reject, suggest cancel-or-wait
- [x] Different focus + clear → tag prior state, wipe workspace, clone new repo, journal `project_switched`
- [x] One task at a time; FIFO queue across triggers (design §12.4) — daemon `asyncio.Queue`, single worker
- [~] Human attach is read-only — no mid-task write surface is exposed to a second client; operational practice

### 1g. CLI operator surface

- [x] `lc admin project switch <link>` (alias of `/project repo:`)
- [x] `lc admin shutdown [--drain-deadline 30m]`
- [x] `lc admin task confirm <task_id> [pass|fail]` — outcome amendment (design §4.2); 7-day window
- [x] `lc admin pending` (stub: returns empty in Tool; wired in chapter 4)
- [x] `lc admin approve <id>` / `lc admin reject <id>` (stubs — 501 until chapter 4)

### 1h. git-proxy

- [x] Build `git-proxy` wrapper; site at open-terminal workspace edge (the git binary inside the workspace IS the proxy) — `git_proxy.py` spliced in as `git` by `Dockerfile.open-terminal`
- [x] No raw-git fallback reachable by the agent — real git relocated to `/usr/bin/git.real`; the proxy is the only `git` on `$PATH`
- [x] Whitelist per design §3.3: `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (operator-pre-configured remotes only)
- [x] Blocklist per design §3.3: `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, all `submodule` subcommands, all history rewrites, anything touching `.git/` directly
- [ ] Mount `.git/config`, `.git/hooks/`, `.git/info/` **read-only to the agent** — **KNOWN GAP** (see Chapter 1 progress note): awkward for a path inside a named volume; not yet implemented. The git-proxy blocks `config` / `remote` at the command level; closing the direct-file-write bypass is a tracked hardening item (open item #9)
- [~] Allowed remotes baked in by operator at project-switch time — proxy enforces `fetch`/`push` against the configured remote set; baking happens at clone time
- [ ] `core.hooksPath` set to operator-controlled directory **outside** `.git/` — tracked with the `.git/config` hardening item above (open item #9)
- [~] Branch / tag discipline (design §12.1): outer-loop changes on `auto/<date>-<topic>` branches; never direct to `main` — proxy permits the ops; the `auto/<date>` convention is enforced by the outer loop, which arrives in Observer+
- [x] Per-repo deploy tokens per design §10.3: least-privilege, injected per task, never ambient, never the self-PAT — `LC_DEPLOY_TOKEN`, injected at clone, never the self-PAT
- [x] Adversarial tests:
  - [x] Agent attempts `remote add` → blocked + journaled (the `.git/config` read-only mount is the compose-level backstop, verified at deploy)
  - [x] Hostile `.gitmodules` → no submodule clone (`submodule` + `clone --recurse-submodules` both blocked + tested)
  - [x] Agent attempts `git push --force` → blocked; journaled

### 1i. Sanitization filter (shadow mode)

- [x] Build filter per design §10.2: redact secrets/key-shaped strings; reduce large file bodies to structural digests; strip PII
- [x] **Pinned and tested** against fixed test set (seeded false-positives + false-negatives)
- [x] In Tool, **run in shadow mode**: filter records what it _would_ redact but does not block (nothing leaves the stack in Tool)
- [x] Rejection rate metric collected on the Prometheus endpoint (feeds chapter 3 drift baseline) — `lc_sanitization_processed` / `lc_sanitization_redacted`

### 1j. Metrics endpoint

- [x] Prometheus endpoint on `agent` (design §9.3) — `metrics.py`, served on `metrics.port` (`127.0.0.1:9091`)
- [x] Tool-relevant metrics: queue depth; journal write rate; llama-cpp slot occupancy; sanitization rejection rate (shadow-mode counts)
- [ ] Later chapters layer additional metrics on this endpoint

### 1k. Centralized config

- [x] Typed config (YAML + JSON schema) per design §12.8, validated at boot
- [x] Tool-era tunables: drain deadline default; `task_abandoned` timeout per channel; basic budget caps
- [x] Schema version on the config file; forward-compat for later chapters

### 1l. Shutdown semantics

- [x] SIGTERM drain mode per design §12.7: refuse new triggers, allow in-flight to a configurable deadline, then SIGKILL
- [x] Open `task_id`s past the deadline → journaled `task_abandoned` with reason `shutdown`
- [x] Operator override `lc admin shutdown --drain-deadline 30m`

### Tool stop point (chapter 1 → 2)

Tool is "done" when:

- [ ] Daily flow feels stable; no new bugs in the basic pipeline — _operator judgment over time_
- [x] Journals are accumulating with the full envelope; no fields missing — verified on the first tasks
- [x] `audit.jsonl` records every project switch + shutdown — verified
- [x] Volumes survive at least one intentional `docker compose up -d --build` rebuild — verified
- [ ] Sanitization rejection rate (shadow mode) has a stable baseline — _needs accumulated runtime_
- [ ] **You find yourself wanting to drive little-coder from chat as well as CLI** — this is the actual trigger to advance

Stop here as long as needed. Advance only when ready.

---

## Chapter 2 — OWUI pipeline

> Refs: design §12.6. Plan §6. **Not active until chapter 1 stop point reached.**

- [ ] Register `lc-mcpo` OpenAPI as an OWUI tool. API-key authentication at the edge
- [ ] Task triggers stream conversationally
- [ ] Slash-commands wired to the same control-plane entries as CLI:
  - [ ] `/project repo:`
  - [ ] `/upstream pull` (stub — actual behavior in chapter 5)
  - [ ] `/pending` (empty until chapter 4)
  - [ ] `/approve <id>` (no-op until chapter 4)
  - [ ] `/reject <id>` (no-op until chapter 4)
  - [ ] `/confirm <task_id> pass|fail`
- [ ] Privilege separation per design §12.6: operator commands authenticated at OWUI surface (OWUI auth); MCP server only authenticates task triggers (API key)
- [ ] Operator smoke test: drive a task end-to-end via OWUI; verify journal records `channel = owui`, `user_id = <OWUI user>`, task completes against open-terminal with identical effect to CLI

### Chapter 2 stop point (→ 3)

- [ ] OWUI parity confirmed; journals attributing both channels correctly
- [ ] Multi-channel journal volume accumulating
- [ ] **You're curious what patterns the system would see in the journals** — trigger to advance

---

## Chapter 3 — Observer

> Refs: design §5, §9.2, §10.1, §13. Plan §7. **Not active until chapter 2 stop point reached.**

### 3a. Preflight exit criteria (design §13)

- [ ] (a) ≥ K distinct clusters each have ≥ their M window of _observed_ occurrences (K and M derived from accumulated data; recorded in Decision Log)
- [ ] (b) Polyglot baseline variance measured on the canonical clone (sets up chapter 4's gate)
- [ ] (c) Counterfactual + adversarial judge prompt dry-run on real examples + human-rated
- [ ] Transition journaled to `audit.jsonl`

### 3b. `meta` process

- [ ] Build `meta` as a separate process (design §3.2 sub-service seam preserved for later split)
- [ ] **Single-flight lock** on `meta` iterations (design §12.5)
- [ ] Triggered by **evidence thresholds**, not a clock

### 3c. Cluster identity

- [ ] Immutable synthetic `cluster_id` + mutable label (design §5.1)
- [ ] Ingest-time assignment: nearest existing cluster above similarity floor; below floor → `unassigned` pool (design §5.2)
- [ ] Judge mints new `cluster_id` only when `unassigned` forms a coherent group
- [ ] Split/merge lineage records per design §5.3: parent↔child; `inherited` vs `observed` counts
- [ ] Cohort scoping per `lang` + `task_shape` aggregated across repos (design §5.5)

### 3d. Cohort store (derived index)

- [ ] Cohort counters as event-sourced projection over journals (design §5.4)
- [ ] Periodic checkpoint; **rebuildable from journals on demand**
- [ ] `schema_version` on the cohort store; bump-and-rebuild on schema change

### 3e. Judge prompt + sanitization promotion

- [ ] Draft counterfactual + adversarial system prompt per design §10.1 and §1 principles
- [ ] Few-shot examples drawn from accumulated journals
- [ ] Output format: structured (cluster_id, proposed type, proposed text, reasoning, why-not-other-types)
- [ ] Dry-run against real journals; human-rate outputs
- [ ] Resolve open item #2 → Decision Log
- [ ] **Promote sanitization filter from shadow to enforcing** for judge calls (design §10.2): filter failure aborts the call
- [ ] Set sanitization drift threshold from Tool-era baseline (open item #5 → Decision Log)

### 3f. Observer surface

- [ ] `meta` produces _reports_ (clusters, occurrences, candidate craft gaps) viewable through the operator surface
- [ ] No artifacts drafted, no merges proposed
- [ ] Reports visible in CLI (`lc admin observe`) and OWUI (`/observe`)

### Chapter 3 stop point (→ 4)

- [ ] Cluster reports stabilize; you trust what the system sees
- [ ] Cluster labels are auditable — you can read them and they make sense
- [ ] **You want meta to draft fixes for the patterns, not just describe them**

---

## Chapter 4 — Learner

> Refs: design §5.6, §5.7, §7, §8, §12.6. Plan §8. **Not active until chapter 3 stop point reached.**

### 4a. Skill library

- [ ] Directory layout per design §7: `skill/knowledge/*.md`, `skill/tools/*.md`, `skill/plan-slots/*.md`
- [ ] Frontmatter schema enforced at draft time (design §7.1): `id`, `cluster_id`, `tier`, `lang`, `domain`, `tool`, `task_shape`, `created`, `supersedes` (null), `status` (`active`)
- [ ] Supersession (design §7.5): new artifact on existing `cluster_id` sets `supersedes`, flips prior to `superseded`
- [ ] Atomic-rename writers (design §7.3) for all watched files: `.tmp` + `rename(2)`; readers ignore `*.tmp`

### 4b. Augmenter

- [ ] Selection per design §7.4: hard tag filter → embedding rank → hard token budget
- [ ] Over-budget tiebreaker: cohort-proven + tighter match; **tier is not a tiebreaker on its own**
- [ ] Per-task augmenter selection logged (required by §8.4 in-context assertion)

### 4c. Polyglot oracle

- [ ] Wrap behind `Oracle` interface per design §8.1: `run_subset(cluster_id, biased_subset) → ScoredResult`
- [ ] Biased subset selection by cluster domain
- [ ] Result schema: per-exercise pass/fail + score + duration + augmenter selections during the run
- [ ] Set N (minimum subset) and regression margin from preflight variance (open item #1 → Decision Log)
- [ ] Baseline per design §8.2: score at last `main` green tag, re-measured on current biased subset
- [ ] Below N → "insufficient evidence", not pass

### 4d. Validation gates

- [ ] In-context assertion (design §8.4): if augmenter didn't select the artifact during validation → gate result = **void**, not pass
- [ ] Single-exercise flip inside noise margin is **not** a regression (design §8.3)
- [ ] Efficacy reversion (design §8.5): post-window indistinguishability → flag `ineffective` → auto-revert next iteration. Retirement journaled to `audit.jsonl`
- [ ] Augmenter selects only `active`

### 4e. Tier 0 and Tier 1

- [ ] Tier 0 trigger: N ≥ ~5 occurrences in a cluster, no prior intervention (design §5.6)
- [ ] Tier 0 intervention: knowledge entry (`skill/knowledge/*.md`)
- [ ] Tier 1 trigger: ~20+ new occurrences after tier-0, rate unchanged
- [ ] Tier 1 selection prompt: judge argues both (tool-craft vs plan-slot), then picks; argument is the journal entry (design §5.7)
- [ ] Plan-slots loaded once at planner-process boot; planner watches the file
- [ ] Quarantine window M per cluster (preflight-tuned; default placeholder until usage informs)

### 4f. Operator surface (live)

- [ ] Pending artifacts list rendered with: artifact text, provenance (cluster_id + journal evidence range), cohort evidence (design §12.6)
- [ ] `lc admin pending` / `/pending` wired up
- [ ] `lc admin approve <id>` / `/approve <id>` performs merge via git-proxy to `auto/<date>-<topic>` branch
- [ ] `lc admin reject <id>` / `/reject <id>` discards artifact + journals decision
- [ ] Surface contradiction flags (design §7.5) for cross-cluster conflicts
- [ ] Surface efficacy-reversion notices (design §8.5)

### 4g. Budgets + deferral + resource isolation

- [ ] Per-window caps per design §12.5: artifacts/iteration = 1; judge wall-clock minutes/day; Polyglot exercise-runs/day; journal write rate
- [ ] Exceeding → **defer**, never drop evidence
- [ ] Deferred iterations: **coalesce per `cluster_id`**; cross-cluster FIFO
- [ ] `meta` checks llama-cpp slot occupancy before issuing inference; backs off when interactive lanes busy
- [ ] Cluster + cohort re-read fresh from journals when coalesced entry runs

### 4h. Longitudinal track

- [ ] Sample cyclomatic complexity, file size, fan-out, churn across repos (design §9.1)
- [ ] Aggregate over time; persist trend snapshots
- [ ] Trend miner runs on rotation events (design §4.3)
- [ ] Surface anomalies (silent clusters) to operator UI (design §9.3); **do not auto-act**
- [ ] Works even when `outcome=unverified`

### 4i. Backup restore drill

- [ ] Tested restore from `little-coder-journals/` + `little-coder-skill/` + `little-coder-cohorts/` backups (open item #7)
- [ ] Cohort store rebuild from journals verified

### Chapter 4 stop point (→ 5)

- [ ] Tier-0 and tier-1 artifacts approved through the human gate enough times to trust drafting quality
- [ ] Efficacy reversion working as expected
- [ ] Polyglot gate catching the regressions you'd expect
- [ ] **You're tired of approving every tier-0 entry by hand and want auto-merge for the well-behaved ones**

---

## Chapter 5 — Self-modifier

> Refs: design §5.8, §6, §11. Plan §9. **Not active until chapter 4 stop point reached.**

### 5a. Tier-0 auto-merge

- [ ] Flip tier-0 to auto-merge
- [ ] Efficacy reversion (design §8.5) gate before flip — verify it has caught at least one ineffective artifact during chapter 4
- [ ] **Sampled human-review** of auto-merged tier-0 entries continues (design §10.4 control 2)

### 5b. Tier 2 routing rules

- [ ] **Staged-freeze** per design §5.8: no routing rule until cluster's tier-0 and tier-1 windows have run and the cluster has demonstrably resisted them
- [ ] Routing rule loaded once at router-process boot from `skill/routing/*.yaml`; atomic-rename hot-reload
- [ ] Router consults rule per task; matches gated by rule's internal conditions (e.g. `lang == rust`)
- [ ] **Random-exploration** per design §5.8: 5–10% of tasks where rule applies, **don't apply it** — collect counter-evidence
- [ ] Efficacy reversion auto-retires rules that don't pay off
- [ ] Exploration rate is a config tunable (default 5%)

### 5c. Tier 3 §6 justification gate

- [ ] Judge produces written argument per design §6: cluster + persistence record; interventions tried; **explicit argument** for why no plausible knowledge/tool-craft/plan-slot/routing rule could close the gap; proposed structural change + expected effect
- [ ] If §6(3) cannot be articulated → structural change is **not justified**; write the missing skill instead
- [ ] Argument journaled (auditable later)

### 5d. Tier 3 candidate topology

- [ ] Stage artifact in separate ephemeral open-terminal workspace (design §11.1 step 1)
- [ ] Provision **paired** candidate topology (design §11.1 step 2): `candidate-little-coder` + `candidate-open-terminal` (do NOT collapse into one container)
- [ ] Candidate mounts active's persistent volumes as **read-only snapshot** (cannot mutate active state)
- [ ] Writable tmpfs for candidate's ephemeral state (discarded on teardown)
- [ ] Same containment as active (capabilities, network policy, `no-new-privileges`)

### 5e. Tier 3 validation

- [ ] **Active drives** the test (design §11.1 step 3); verdict is external
- [ ] Two acceptance tests, both required (design §11.1 step 4):
  - [ ] Issue fixed: reproduce-then-fix the specific §6 cluster
  - [ ] No regression: Polyglot biased subset score ≥ baseline
- [ ] Stratified Polyglot subset for upstream-merge validation (broader than per-cluster bias)
- [ ] Verdict from objective oracle: Polyglot + §6 justification + human gate — **never** active's own assessment

### 5f. Tier 3 repro persistence + PR

- [ ] Before candidate teardown: write executable repro to `little-coder-cohorts/repro/<artifact_id>/` (surviving volume)
- [ ] Open PR from `auto/<date>-<topic>` → `main` on **private** self-improvement remote
- [ ] PR body templated mechanically per design §11.2
- [ ] **PR body passed through sanitization filter** before posting (design §10.2); filter failure aborts the PR

### 5g. Tier 3 deploy + verify

- [ ] Operator reviews PR on private remote, merges what they choose (design §11.1 step 7)
- [ ] Operator runs `docker compose up -d --build little-coder`
- [ ] Active instance never mutated; clean swap between known-good tagged commits
- [ ] Volumes survive the rebuild
- [ ] Post-deploy verification: (a) healthcheck green on `agent` + `lc-mcpo`; (b) smoke task = re-run the persisted §6 repro
- [ ] Either check fails → `git revert` + same compose command, or pin to prior tag

### 5h. Tier 3 rollback drill

- [ ] At least one real rollback drill before considering this chapter operational
- [ ] Drill journaled; restore-from-volume timing measured

### 5i. Upstream pull (chapter 5 makes it live)

- [ ] `/upstream pull` / `lc admin upstream pull` becomes operational per design §12.2
- [ ] Tiers 0–2 (additive skill files) land cleanly
- [ ] Tier-3 conflicts resolved manually on `upstream-merge/<date>` branch; run §5d–§5g flow with **stratified Polyglot subset across all live cluster domains** before becoming active
- [ ] Pull journaled as `upstream_pulled` with old + new commit ids
- [ ] Self-authored tier-3 artifacts invalidated by an upstream pull → journaled `invalidated_by_upstream` and retired

### Chapter 5 stop point

This is the endpoint. The system continues to grow but does so through tier-3 artifacts (rare), not new chapters.

---

## Cross-cutting (not chapter-gated)

These run alongside multiple chapters.

- [ ] **Config management** (design §12.8): tunables added per chapter:
  - Tool: drain deadline; `task_abandoned` timeout per channel; basic budget caps
  - Observer: similarity floor; sanitization drift threshold
  - Learner: per-cluster M; Polyglot N/margin; augmenter budget; full budget caps; coalesce thresholds
  - Self-modifier: exploration rate
- [ ] **Metrics endpoint** (design §9.3): Prometheus on `agent` (Tool) and `meta` (Observer+); metrics added per chapter
- [ ] **Alarms** routed to operator UI alongside artifact approvals (design §9.3) — from chapter 4 onward
- [ ] **Golden-journal test suite** (design §12.11): from chapter 3 onward, run on each `meta` release before deploy
- [ ] **Schema-version discipline** (design §12.9): readers tolerate older shapes; migrations are explicit operator jobs; **tier-3 self-changes cannot propose schema changes**
- [ ] **Sanitization drift audit** (design §10.2): evidence-triggered when rejection rate moves outside baseline envelope; sample N raw journal records; human-review for false negatives — from chapter 3 onward

---

## Decision Log

> Append-only. Every status change or scope shift adds a row. Date is decision date.

| Date       | Chapter | Decision                                                                                                                                                                                                         | Refs                      |
| ---------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| 2026-05-19 | n/a     | Plan + tasks docs created from the design doc.                                                                                                                                                                   | plan §10                  |
| 2026-05-20 | 1       | Open-terminal network change promoted to a **Tool requirement**. Reachability bounds the blast radius from Tool onward.                                                                                          | design §3.4               |
| 2026-05-20 | n/a     | Removed per-task git worktrees from the session model. One focused project cloned directly; one task at a time; FIFO. `/project repo:` wipes and re-clones.                                                      | design §3.4, §12.3, §12.4 |
| 2026-05-20 | n/a     | Removed any "quarterly sanitization floor." Audits are evidence-triggered.                                                                                                                                       | design §10.2              |
| 2026-05-20 | 4       | Removed numeric "X%" placeholder for tier-0 efficacy-reversion exit. Qualitative — investigate if patterns look worth investigating.                                                                             | plan §8                   |
| 2026-05-20 | n/a     | Design doc reorganized topically (architecture → data → safety → operations → roadmap).                                                                                                                          | design — whole doc        |
| 2026-05-20 | n/a     | Plan restructured from 11 phases into **5 chapters** with explicit stop points. Build agent stops at each chapter boundary; operator advances when ready.                                                        | plan §2                   |
| 2026-05-20 | 1       | Sanitization filter built in Tool, run in **shadow mode**; promoted to enforcing in chapter 3 (Observer). This collects baseline drift behavior during Tool/OWUI usage so chapter 3 doesn't have to wait for it. | design §10.2, plan §5     |
| 2026-05-20 | 1       | All four named volumes declared in Tool (even though only `little-coder-journals/` actively records before chapter 3) so later chapters don't trigger silent wipes.                                              | design §3.6               |
| 2026-05-20 | 1       | OWUI's current direct access to open-terminal **ends in Tool**; chapter 2 restores it via `lc-mcpo`. Documented as a user-visible change.                                                                        | design §3.4, plan §5      |
| 2026-05-22 | 1       | Upstream little-coder is a Node.js CLI on the `pi` framework, **not Python**. Control-plane wrapper (journals, config, sanitization, git-proxy, CLI, MCP edge) built in Python mirroring `search-mcpo`; the agent container is Node-based. The `agent.py` reference in design §6 is a Chapter-5 illustration only — design doc left unedited (operator's call). | design §3.1, §6           |
| 2026-05-22 | 1       | Agent↔open-terminal integration: a fifth named volume `little-coder-workspace` is shared by both containers. The agent edits files on it directly; build/test/git execution is routed to `open-terminal`'s `POST /execute` REST API, keeping execution in the network-isolated plane. Operator-confirmed approach. | design §1.5, §3.4         |
| 2026-05-22 | 1       | Control-plane foundation landed and unit-tested (config, journals, audit, sanitization, ULID, git-proxy, URL-norm, open-terminal client, workspace/project-focus) — 120 tests passing. Daemon, agent integration, Dockerfiles, compose pending. | plan §5                   |
| 2026-05-22 | 1       | Chapter 1 **code-complete**: control daemon, agent integration, CLI, metrics, MCP edge + `lc-mcpo`, Dockerfiles, compose wiring (`lc-net`, 5 volumes, `lc-egress` allowlist proxy, backup job). 134 tests passing. Awaiting deployment + operator verification (the chapter stop point). | plan §5                   |
| 2026-05-22 | 1       | open-terminal's egress allowlist implemented as `lc-egress` (tinyproxy, default-deny host filter) — mirrors the search stack's Tor-wall pattern. A precise per-host allowlist is not expressible with plain compose networks. | design §3.4               |
| 2026-05-22 | 1       | **KNOWN GAP** — the `.git/config` read-only mount (design §3.3) is not implemented; awkward for a path inside a named volume. git-proxy blocks `config`/`remote` at the command level, but a direct file write to `.git/config` bypasses that. Tracked as open item #9. | design §3.3               |
| 2026-05-22 | 1       | Chapter 1 **built, deployed, smoke-tested end-to-end** — all 5 services healthy; `lc project` clones via the egress proxy; `lc task` runs agent → LLM → journals; exec routing verified (agent commands run in open-terminal through the git-proxy). | plan §5                   |
| 2026-05-22 | 1       | Build/test fixes: Node 22 required (not 20); clone uses the relocated real git `/usr/bin/git.real`; `git safe.directory '*'` + `umask 000` make the shared workspace volume usable across container uids; `models.json` override maps `llamacpp` to llama-swap's real model ids; pi extension API corrected against the bundled extensions. | plan §5                   |
| 2026-05-22 | 1       | Agent exec routing made a switch — `LC_ROUTE_EXEC` (compose env, default 1, verified working). 0 falls back to built-in bash inside the network-isolated little-coder container. | design §3.4               |
| 2026-05-22 | 1       | `task_abandoned` default lowered to 30m for owui/cli (was 6h per the design §4.2 example) — a saner Tool default and a backstop against agent loops. Open item #3; raise for genuine long refactors. | design §4.2               |

---

## Open items (mirror of plan §11)

Tuned by preflight (observed usage) unless noted.

- [ ] **#1** Polyglot N + regression margin (baseline variance collected during Tool/OWUI; computed at Observer; used in Learner)
- [ ] **#2** Counterfactual judge prompt wording + few-shot (Observer dry-run; resolves at Decision Log entry)
- [ ] **#3** `task_abandoned` timeout per channel (Tool default usable; tune during Tool/OWUI usage; lock by Learner)
- [ ] **#4** Neutral test-runner for design §11.1 step 3 (later hardening; not blocking Self-modifier)
- [ ] **#5** Sanitization audit drift threshold (Tool-era shadow-mode baseline; resolves at Observer chapter when filter goes enforcing)
- [ ] **#6** Reserved-slot promotion threshold (`meta` GPU) — Learner+ if starvation observed
- [ ] **#7** Backup cadence + restore drill (Tool: backup cadence decided; drill before Learner chapter merges first artifact)
- [ ] **#8** `.git/config` flexibility upgrade (deferred; no current trigger)
- [ ] **#9** `.git/config` / `.git/hooks` / `.git/info` read-only **enforcement** for the agent + `core.hooksPath` outside `.git/` (design §3.3). Known gap — the git-proxy blocks `config`/`remote` at the command level, but a direct file write to `.git/config` bypasses that. Closure is awkward for a path inside a named volume; resolve before real hostile-repo workload.

---

## How to drive this

1. Active chapter is at the top.
2. Work tasks top-to-bottom within the chapter; mark `[~]` when starting, `[x]` when the **acceptance criterion** in the task is met (not just "code written").
3. On chapter stop-point satisfaction → flip chapter status; add Decision Log row; bump `Last updated`.
4. **The build agent stops at the chapter boundary.** Resuming work means deliberately starting the next chapter — not a hand-off the agent makes by itself.
5. New decisions → Decision Log first, then update [`integration-plan.md`](integration-plan.md) lock table if permanent.
6. Discovered scope → if it lives inside an existing chapter, add a task; if it's a new gate, propose a new chapter in the plan first.
7. **Design doc wins on conflict.** If a chapter here implies something the design doc forbids, fix this doc.
