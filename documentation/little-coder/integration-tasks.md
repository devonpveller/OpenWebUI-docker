# Self-Improving Little-Coder — Task Tracker

> **Last updated:** 2026-05-23
> **Plan:** [`integration-plan.md`](integration-plan.md) — chapters, rationale, locked decisions.
> **Design doc:** [`Self-improving-little-coder-design.md`](Self-improving-little-coder-design.md) — source of truth. Design doc wins on conflict.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → bump `Last updated` and add a Decision Log row if a choice changed.

## Current state

- **Active chapter:** 4 (Learner) — Stage 1 in progress (skill library data model). Chapter 3 (Observer) build-complete + deployed 2026-05-23, awaiting operator dry-run of judge prompt (open item #2) before `judge_enabled` flips. Chapter 2 closed 2026-05-23; chapter 1 operator-accepted 2026-05-22.

  **Chapter 3 carry-overs (operator-only):** flip `observer.judge_enabled: true` after dry-run; accumulate cluster-eligible journals via real tasks; decide whether minted labels are trustworthy; record chapter-3 → chapter-4 advance to `audit.jsonl`. These are not blockers for Chapter 4 build work — the data model + tier ladder math don't need real clusters to exist.
- **Build sequence:** five chapters, gated by operator judgment (plan §2). One chapter at a time.
- **Tier-0 build reminder:** `session_id` + `channel` + `user_id` on every journal line from line 1. Unrecoverable retroactively (design §4.1).
- **Tool ships the open-terminal network change.** OWUI's direct access to open-terminal ends; it returns in chapter 2 via `lc-mcpo`.
- **Source lives in [`../../little-coder/`](../../little-coder/)** — Python control-plane package (`src/littlecoder/`), `git-proxy/`, `config/`, `tests/`.
- **Two knowledge layers (plan §3):** founding knowledge — the operator-authored baseline in `agent-knowledge/`, built in Chapter 2 — is separate from the §7 self-improvement skill library (meta-learned, discovered per task, Chapter 4+).
- **The agent is stateless (plan §3).** Journals are write-only from the agent's side; they feed `meta`, never the agent's context. Project continuity is the workspace filesystem + git history, not journal-backed episodic recall.

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
_direct file write_ to `.git/config` bypasses that. The design closes this
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

> **Write-only from the agent's side (plan §3).** These feed `meta` (Observer onward), never the agent's context. The agent is stateless per task.

- [x] Implement writers for `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl`
- [x] Envelope per design §4.1: `ts`, `task_id` (ULID), `session_id`, `channel`, `user_id`, `repo`, `lang`, `seq`, `schema_version: 1`
- [x] Write-time schema validation; malformed records **rejected, not appended**
- [x] `task_started` / `task_ended` bracket every task; reconstruct by `task_id`, never adjacency — daemon brackets every task; `TaskContext` carries the per-task `seq`
- [x] Outcome label per design §4.2: `pass` / `fail` / `unverified`
- [x] Durability per design §4.3: append + fsync on every terminal and every error record
- [x] Schema versioning plumbed through readers; tolerate older shapes (forward-compat for later chapters)
- [x] Journal reader (`iter_records`) built for `meta` consumption (Chapter 3) — **not** called in the agent path; agent never reads journals

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

> Project continuity is the workspace filesystem + git history (plan §3), not journals. The agent re-derives context from the files and `git log` each task.

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
- [x] Whitelist per design §3.3: `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (operator-pre-configured remotes only) — note `log`/`checkout` are permitted; git is the agent's project-state memory (plan §3)
- [x] Blocklist per design §3.3: `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, all `submodule` subcommands, all history rewrites, anything touching `.git/` directly
- [~] Mount `.git/config`, `.git/hooks/`, `.git/info/` **read-only to the agent** — **PARTIAL CLOSURE 2026-05-22**: a workspace-edge bash filter (`git_artifact_filter.py`, wired into `ot-exec`) blocks the obvious direct-file-write bypass — `>`/`>>`/`>|`/`tee` redirects, `cp`/`mv`/`install`/`truncate`/`dd of=`, and in-place editors (`sed -i`/`awk -i inplace`/`perl -i`) targeting `.git/config|hooks/|info/`. Symmetric with the git-proxy; same `git-proxy: DENIED` marker, journaled as `git_blocked`. **Residual gap**: open-terminal still runs the agent's commands as root, so `python -c '...write...'` / base64-obfuscated paths / a renamed util reaching `.git/config` are not caught — full closure needs either `CAP_DAC_OVERRIDE` dropped from open-terminal (so chmod-based read-only is enforced kernel-side) or a uid split between setup and agent execution. Tracked as the residual half of open item #9
- [~] Allowed remotes baked in by operator at project-switch time — proxy enforces `fetch`/`push` against the configured remote set; baking happens at clone time
- [x] `core.hooksPath` set to operator-controlled directory **outside** `.git/` — `Dockerfile.open-terminal` sets `git config --system core.hooksPath /etc/lc-git-hooks` and bakes the empty dir at 0555. Every repo's hook lookup is redirected to that operator-controlled path; whatever lands in any `.git/hooks/` is never executed by git
- [~] Branch / tag discipline (design §12.1): outer-loop changes on `auto/<date>-<topic>` branches; never direct to `main` — proxy permits the ops; the `auto/<date>` convention is enforced by the outer loop, which arrives in Observer+
- [x] Per-repo deploy tokens per design §10.3: least-privilege, injected per task, never ambient, never the self-PAT — `LC_DEPLOY_TOKEN`, injected at clone, never the self-PAT
- [x] Adversarial tests:
  - [x] Agent attempts `remote add` → blocked + journaled (the `.git/config` read-only mount is the compose-level backstop, verified at deploy)
  - [x] Hostile `.gitmodules` → no submodule clone (`submodule` + `clone --recurse-submodules` both blocked + tested)
  - [x] Agent attempts `git push --force` → blocked; journaled
  - [x] Agent attempts `echo > .git/config` / `sed -i .git/config` / `cp x .git/hooks/pre-commit` / `tee .git/info/exclude` → blocked at the ot-exec edge + journaled (`tests/test_git_artifact_filter.py`, 49 cases covering redirects, write-commands, in-place editors, and three documented residual-gap shapes that pass through — see partial-closure bullet above)

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

- [x] Daily flow feels stable; no new bugs in the basic pipeline — operator-confirmed across CLI + OWUI runtime; only regression caught was the pager-hang, fixed 2026-05-23 (Decision Log)
- [x] Journals are accumulating with the full envelope; no fields missing — verified on the first tasks
- [x] `audit.jsonl` records every project switch + shutdown — verified
- [x] Volumes survive at least one intentional `docker compose up -d --build` rebuild — verified
- [-] Sanitization rejection rate (shadow mode) has a stable baseline — **stop-point item was design-impossible**: per design §10.2 the filter runs on outbound, and Tool/OWUI has no outbound (no judge, no PR). Confirmed: `lc_sanitization_processed = 0` at chapter-2 close. The baseline forms when judge calls begin in Chapter 3 (Observer); tracked at open item #5
- [x] **You find yourself wanting to drive little-coder from chat as well as CLI** — operator triggered the advance to OWUI on 2026-05-22 (Decision Log)

Stop here as long as needed. Advance only when ready.

---

## Chapter 2 — OWUI pipeline

> Refs: design §12.6. Plan §6. **Active — chapter 1 stop point reached 2026-05-22.**

### Chapter 2 progress (2026-05-22)

Implemented as an OWUI **Pipe function** — `little-coder/owui/little_coder_pipe.py`
(install instructions: `owui/README.md`). It registers a "Little Coder" model:
plain messages trigger tasks; `/`-commands are operator actions gated by the
OWUI user role. `lc-mcpo` now joins `llm-net` so OWUI can also register it as
an OpenAPI tool.

**Backend deployed and verified (2026-05-22):** `lc-mcpo` is on `llm-net`;
from the `openwebui` container both `http://little-coder:8090/health` and
`http://lc-mcpo:8002/openapi.json` respond. Installing the Pipe and running
the in-OWUI smoke test is an operator action (`owui/README.md`) — it needs an
OWUI admin to paste the function and a logged-in user to send a chat message.

Legend: `[x]` done · `[~]` built, needs the in-OWUI smoke test · `[ ]` not done.

- [~] Register `lc-mcpo` OpenAPI as an OWUI tool. API-key authentication at the edge — `lc-mcpo` on `llm-net`; registration is an OWUI admin step (`owui/README.md`)
- [x] Task triggers stream conversationally — the Pipe polls the daemon and emits status events
- [x] Slash-commands wired to the same control-plane entries as CLI:
  - [x] `/project repo:`
  - [x] `/upstream pull` (stub — daemon `/admin/upstream/pull` → 501 until chapter 5)
  - [x] `/pending` (empty until chapter 4)
  - [x] `/approve <id>` (501 stub until chapter 4)
  - [x] `/reject <id>` (501 stub until chapter 4)
  - [x] `/confirm <task_id> pass|fail`
- [x] Privilege separation per design §12.6: operator slash-commands gated by the OWUI user role inside the Pipe; `lc-mcpo` exposes only `trigger_task`/`task_status`/`project_focus` — no operator surface
- [x] Operator smoke test: drive a task end-to-end via OWUI; verify the journal records `channel = owui`, `user_id = <OWUI user>` — **verified 2026-05-23 post-pager-fix**: task `01KS9E1APPV2KVPDWQGDW2FFDD` (prompt: "what can you see in your workspace?") completed in 26s with `channel=owui`, `user_id=yamaoka01@gmail.com`, 3 successful bash tool calls, `outcome=unverified`. Full envelope (`session_id`, `repo`, `lang`, `seq`, `schema_version`) present on every record

> **Statelessness note (plan §3):** the Pipe sends only the latest user message (`_last_user_text`); earlier turns in the same chat are not passed. "refactor X" then "now also do Y" runs the second task fresh — it sees the workspace as X left it (filesystem + git), not the intent behind X. This is the intended boundary; journal-backed episodic memory is open item #10, not built.

### Founding knowledge (baseline knowledge layer)

The operator-authored baseline appended to the agent's system prompt — distinct
from the §7 self-improvement skill library (the meta-learned layer, Chapter 4+).
See plan §3 "Two knowledge layers". Built during Chapter 2; not a Chapter 2
stop-point gate.

- [x] `agent-knowledge/environment.md` — operating environment: bash runs in open-terminal, git-proxy whitelist/blocklist, `/workspace`, no ShellSession/Browser, network limits. Stops the agent rediscovering its constraints each task
- [x] `agent-knowledge/project-context.md` — cheap project-orientation patterns: the four-command read (`git log -n 10` / `git status -sb` / `ls -la` / `cat README.md`), project-type-file shortcut (`package.json` / `pyproject.toml` / `Cargo.toml` / ...), anti-patterns (`cd`, `find /`, re-orienting mid-task). The concrete answer to "git+filesystem as project memory" — design §3.1 / §15 — so the agent stops re-deriving project state each turn (token-cost reduction described in the 2026-05-22 user feedback that motivated this file)
- [x] `agent-knowledge/engineering-principles.md` — SOLID, encapsulation, naming/readability, patterns/standardization, DRY/YAGNI, error handling, verification
- [x] `agent-knowledge/README.md` — documents the layer + its relationship to the §7 skill library; enumerates the three files and the load order (environment → project-context → engineering-principles)
- [x] Wired through `config/little-coder.config.yaml` → `agent.extra_args` → three `--append-system-prompt` flags; baked into the agent image (`Dockerfile.agent` `COPY agent-knowledge`)
- [x] Deployed and live — `environment.md` + `engineering-principles.md` deployed 2026-05-22; `project-context.md` added 2026-05-22, image rebuilt + container recreated the same day, verified present in `/app/agent-knowledge/` inside the running container, focus preserved across the rebuild
- [x] Operator-verified in a task: the agent no longer probes `cd` / `git` to rediscover its environment, AND uses the four-command orientation pattern — **verified on the 2026-05-23 smoke task**: agent ran exactly `git log --oneline -n 10` + `git status -sb` + `ls -la` (skipped `cat README.md` because `ls` already revealed contents — acceptable optimization on a trivial repo), produced a coherent answer, **and** noted the absence of `package.json`/`pyproject.toml`/`Cargo.toml` — i.e. it followed the `project-context.md` project-type-file shortcut. No `cd`, no `find /`, no mid-task re-orient
- [x] **`engineering-principles.md` SOLID/code-craft content is the instruction half of a pair** — the file is phrased so each principle is structurally measurable (function length, parameters, fan-out, churn, dependency direction), giving the §9.1 longitudinal track concrete shapes to detect decay against (verified 2026-05-23 at chapter close). The Chapter-2 awareness half is done; the Chapter-4 wiring half tracks at §4h below.

### Chapter 2 stop point (→ 3)

- [x] OWUI parity confirmed; journals attributing both channels correctly — `owui` and `cli` channel records both verified in `outcomes.jsonl` / `tool_calls.jsonl` (2026-05-23)
- [x] Multi-channel journal volume accumulating — 121 lines across the four journals at chapter close (53 outcomes, 48 tool_calls, 15 audit, 5 errors)
- [x] **You're curious what patterns the system would see in the journals** — operator triggered the advance to chapter 3 on 2026-05-23 ("if we've completed chapter 2, then lets move onto chapter 3")

---

## Chapter 3 — Observer

> Refs: design §5, §9.2, §10.1, §13. Plan §7. **Not active until chapter 2 stop point reached.**

### 3a. Preflight exit criteria (design §13)

- [ ] (a) ≥ K distinct clusters each have ≥ their M window of _observed_ occurrences (K and M derived from accumulated data; recorded in Decision Log)
- [ ] (b) Polyglot baseline variance measured on the canonical clone (sets up chapter 4's gate)
- [ ] (c) Counterfactual + adversarial judge prompt dry-run on real examples + human-rated
- [ ] Transition journaled to `audit.jsonl`

### 3b. `meta` process

- [x] Build `meta` as a separate process (design §3.2 sub-service seam preserved for later split) — `meta.MetaRunner` lives in its own module with a clean seam (it only sees `journals_dir`, `cohorts_dir`, and an injectable similarity function). Currently colocated with the daemon process; can be lifted to its own container in a later chapter without architectural rework
- [x] **Single-flight lock** on `meta` iterations (design §12.5) — `MetaState._lock` is acquired non-blocking; a second trigger arriving mid-iteration returns `None` and is DROPPED (not queued). Tested with a parallel-threads case
- [x] Triggered by **evidence thresholds**, not a clock — `meta.should_trigger(state, current_record_count, threshold)`. Threshold lives in `ObserverConfig.evidence_trigger_records` (default 5). No timer code in this module at all
- [x] `meta` consumes journals via `iter_records` (the reader built in Chapter 1, never called from the agent path) — `cohorts._iter_journals` extends the live-segment-only reader to walk rotated segments too (necessary because clusters outlive a single segment); built on the same envelope-validation contract

### 3c. Cluster identity

- [x] Immutable synthetic `cluster_id` + mutable label (design §5.1) — `clusters.Cluster` carries 16-char-hex id (`new_cluster_id()`), label + discriminator are mutable fields; cohort counters key on `cluster_id` so relabels never touch history
- [x] Ingest-time assignment: nearest existing cluster above similarity floor; below floor → `unassigned` pool (design §5.2) — `clusters.assign` is a pure function; similarity is injectable so the data model unit-tests without an LLM, judge wires the real similarity in Stage 3
- [x] Judge mints new `cluster_id` only when `unassigned` forms a coherent group — `judge.Judge.mint_clusters` is the entry point: refuses pools below `min_pool_size=3`, builds counterfactual+adversarial prompt with founding knowledge inlined, parses structured JSON, materializes `MintingResult` (Clusters + consumed Occurrences) with fresh ids; meta integrates by routing consumed-occurrences out of the unassigned bucket onto the new cluster's counters
- [x] Split/merge lineage records per design §5.3: parent↔child; `inherited` vs `observed` counts — `clusters.SplitEvent` / `MergeEvent` data shapes; `cohorts.apply_split` / `apply_merge` enforce the don't-escalate-on-inherited rule (children's `observed` stays 0 on split)
- [x] Cohort scoping per `lang` + `task_shape` aggregated across repos (design §5.5) — `clusters.assign` filters in-scope first, never cross-scope; `cohorts.UnassignedBucket` keyed by `(lang, task_shape)`; `ClusterCounters.per_repo_observed` for drill-down. Task shape inferred from per-task records via `task_shape.classify_records` (heuristic; judge refines later)

### 3d. Cohort store (derived index)

- [x] Cohort counters as event-sourced projection over journals (design §5.4) — `cohorts.project` rolls records into the store; `cohorts.rebuild` re-derives from disk; the projection only emits occurrences from `error` records and `task_ended(fail)` (passing tasks produce zero cluster events). Walks all three journals (tool_calls included) because task_shape inference needs the full per-task trace
- [x] Periodic checkpoint; **rebuildable from journals on demand** — `cohorts.checkpoint` writes atomically (`.tmp` + `rename(2)` per design §7.3); `cohorts.rebuild` is deterministic (test pins replay equivalence) and walks rotated segments via `_iter_journals`
- [x] `schema_version` on the cohort store; bump-and-rebuild on schema change — `CohortStore.schema_version` enforced; `from_dict` refuses any newer-than-build version per design §12.9

### 3e. Judge prompt + sanitization promotion

- [x] Draft counterfactual + adversarial system prompt per design §10.1 and §1 principles — system prompt in `judge._SYSTEM_PROMPT` instructs the judge to argue BOTH sides (these cohere / these are noise/distinct) before deciding; zero-cluster answers are explicitly valid; `pool_too_small`/`pool_too_noisy` flags carry the negative result
- [~] Few-shot examples drawn from accumulated journals — pending real journal volume (Tool+OWUI produced 106 records but only 4 unassigned occurrences). Resolves at next operator dry-run pass
- [x] Output format: structured (cluster_id, proposed type, proposed text, reasoning, why-not-other-types) — `JudgeOutput` + `ClusterProposal` pydantic models enforce `label`, `discriminator`, `signal_indices`, `baseline_covers` (required), `reasoning`, `not_other_types`. Non-overlap of `signal_indices` enforced in `Judge._materialize`; bounds-checks drop out-of-range claims silently (LLM marginal mistake ≠ iteration abort)
- [x] **Founding knowledge in the judge's context (plan §3, locked decision #17).** — `judge.build_messages` inlines the operator-authored baseline files; `baseline_covers: bool` is REQUIRED in the response schema (pydantic field, no default) so a judge that omits it raises `LlmError`. `Cluster.baseline_covers` carries the flag forward; Chapter-4 tier-0 gating reads it
- [ ] Dry-run against real journals; human-rate outputs — operator action; needs accumulated cluster-eligible occurrences to evaluate
- [ ] Resolve open item #2 → Decision Log — pending the dry-run above
- [x] **Promote sanitization filter from shadow to enforcing** for judge calls (design §10.2): filter failure aborts the call — `llm.ChatClient` constructs a `Sanitizer(mode="enforcing")` by default (control-plane outbound is always enforcing per design §10.2); `_sanitize()` propagates `SanitizerError` without "send anyway" — the existing test_sanitize coverage already verifies the abort path
- [ ] Set sanitization drift threshold from Tool-era baseline (open item #5 → Decision Log) — moot per open item #5 rephrase; baseline forms once judge calls actually run against accumulated journals

### 3f. Observer surface

- [x] `meta` produces _reports_ (clusters, occurrences, candidate craft gaps) viewable through the operator surface — `observer.report_dict` produces the JSON shape; `observer.render_text` produces the terminal rendering; `/admin/observe` daemon endpoint serves both; `?iterate=true` runs a fresh meta pass first (single-flight — a concurrent iterate falls through to the existing checkpoint, never queues)
- [x] No artifacts drafted, no merges proposed — Observer's only writes are the cohort store (`/var/lib/little-coder/cohorts/cohort-store.json`) and the always-present journals; no `skill/` files are touched, the surface enumerates clusters but offers no approve/draft action
- [x] Reports visible in CLI (`lc admin observe`) and OWUI (`/observe`) — `cli.cmd_admin_observe` + `lc admin observe [--iterate] [--json]`; Pipe slash-command `/observe [iterate]` renders Markdown sections per the same `report_dict` shape
- [x] Reports distinguish **knowledge gaps** (baseline silent) from **compliance gaps** (baseline covers it but the agent isn't following) — `Cluster.baseline_covers` is the dividing line; `observer.summarize_store` splits clusters into two ordered lists (most-prominent first by `observed`); CLI section headers carry the tier hint ("tier-0 candidates" / "tier-1 enforcement"); Pipe formatting mirrors

### Chapter 3 stop point (→ 4)

- [ ] Cluster reports stabilize; you trust what the system sees
- [ ] Cluster labels are auditable — you can read them and they make sense
- [ ] **You want meta to draft fixes for the patterns, not just describe them**

---

## Chapter 4 — Learner

> Refs: design §5.6, §5.7, §7, §8, §12.6. Plan §8. **Not active until chapter 3 stop point reached.**

### 4a. Skill library

> Artifacts adopt the **Anthropic Agent Skills format** (Decision Log 2026-05-22)
> layered with design §7.1 metadata. Format adopted; the skill-creator A/B eval
> loop is **not** adopted — see Decision Log + plan §8.

- [x] Directory layout per design §7: `skill/knowledge/*.md`, `skill/tools/*.md`, `skill/plan-slots/*.md` — `skills.SKILL_SUBDIRS` maps `knowledge/tool/plan_slot` → `knowledge/tools/plan-slots/`; `skill_path(skill_dir, skill)` routes by kind; `iter_skills` walks all three; routing (Chapter 5) is left alone (different file shape — YAML)
- [x] Each artifact authored as a `SKILL.md` with `name` + `description` frontmatter (Agent Skills format) — `SkillFrontmatter` pydantic model carries Agent Skills fields + §7.1 metadata; serialization writes YAML frontmatter (`exclude_none=True` so freshly-drafted skills don't carry `supersedes: null` noise) + body. Soft 500-line body cap noted in module docstring (warn, not reject — corruption vs craft separation)
- [x] Frontmatter schema enforced at draft time — `extra="forbid"` rejects stray fields; tier ∈ {0,1,2} (no tier-3 — tier-3 is a code change, not a skill); kind ∈ {knowledge, tool, plan_slot}; status ∈ {active, superseded, retired, pending}. Both `parse_skill` (from disk) and `build_skill` (from judge) raise `SkillFormatError` on failure — uniform error type across paths
- [x] `description` field written for discovery — required field, `min_length=2 max_length=1000`; module docstring explicit that this is what the §7.4 augmenter embeds + ranks against the task
- [ ] Judge drafting prompt instructed in the Agent Skills authoring conventions (description-driven discovery, progressive disclosure, explain-the-why) — Stage 3 (judge extension)
- [ ] **Tier-0 only fires for genuine knowledge gaps** — the §3e `baseline_covers` check gates this: a baseline-covered cluster enters at tier-1, not tier-0 (locked decision #17) — Stage 3 (escalation logic in `meta`)
- [x] Supersession (design §7.5): new artifact on existing `cluster_id` sets `supersedes`, flips prior to `superseded` — `flip_status(skill_dir, skill_id, new_status)` does the atomic rewrite preserving body; the WHEN-to-call lives in Stage 3 (escalation), but the operation is here
- [x] Atomic-rename writers (design §7.3) for all watched files: `.tmp` + `rename(2)`; readers ignore `*.tmp` — `write_skill` round-trip-checks the serialized text before renaming (corrupt drafts never land on disk); `iter_skills` filters `*.tmp` so a watcher reading mid-write sees old-or-new, never partial. Pinned by `test_write_skill_atomic_rename` (monkeypatches `os.replace` to assert the `.tmp` step)

### 4b. Augmenter

- [x] Selection per design §7.4: hard tag filter → embedding rank → hard token budget — `augmenter.select(library, request, similarity, token_budget=...)` runs the pipeline: `_filter_by_tags` (lang/domain/task_shape/tool with `*` wildcards, case-insensitive lang), `_rank_by_similarity` (similarity over the description field, NOT the body — long bodies can't unfairly outrank precise descriptions), `_fit_to_budget` (greedy fill with rough char→token estimate, conservative round-up so we never under-count)
- [x] Over-budget tiebreaker: cohort-proven + tighter match; **tier is not a tiebreaker on its own** — composite sort key `(-score, 0 if cohort_proven else 1)` keeps score descending, breaks ties on cohort_proven flag; `is_cohort_proven` is injectable (Stage 4 wires it to the cohort store's efficacy data; Stage 3 default returns False — no skill is proven yet). Test pins both halves: cohort-proven wins at equal score; higher-score-unproven beats lower-score-proven (locked in test `test_higher_score_beats_cohort_proven_at_lower_score`)
- [x] Per-task augmenter selection logged (required by §8.4 in-context assertion) — `SkillSelection.selected` + `.rejected` (each rejection carries `reason` + optional score); `.includes(skill_id)` is the §8.4 query — was this artifact in-context for the task

### 4c. Polyglot oracle

- [ ] Wrap behind `Oracle` interface per design §8.1: `run_subset(cluster_id, biased_subset) → ScoredResult`
- [ ] Biased subset selection by cluster domain
- [ ] Result schema: per-exercise pass/fail + score + duration + augmenter selections during the run
- [ ] Set N (minimum subset) and regression margin from preflight variance (open item #1 → Decision Log)
- [ ] Baseline per design §8.2: score at last `main` green tag, re-measured on current biased subset
- [ ] Below N → "insufficient evidence", not pass

### 4d. Validation gates

> Cohort efficacy reversion is the production-truth validation. The skill-creator
> A/B eval loop is deliberately not adopted (Decision Log) — efficacy reversion
> is its equivalent and is authoritative.

- [ ] In-context assertion (design §8.4): if augmenter didn't select the artifact during validation → gate result = **void**, not pass
- [ ] Single-exercise flip inside noise margin is **not** a regression (design §8.3)
- [ ] Efficacy reversion (design §8.5): post-window indistinguishability → flag `ineffective` → auto-revert next iteration. Retirement journaled to `audit.jsonl`
- [ ] Augmenter selects only `active`

### 4e. Tier 0 and Tier 1

- [x] Tier 0 trigger: N ≥ ~5 occurrences in a cluster, no prior intervention (design §5.6) — **and** `baseline_covers == false` (§3e); a baseline-covered cluster is a compliance gap → tier-1 — `tier_ladder.evaluate_tier_0(cluster, counter, prior)` is the pure policy function (`TIER0_MIN_OCCURRENCES = 5`). Returns `Escalation` with `eligible` + `reason`; the reason names the failing axis ("baseline covers …", "only N observed", "prior intervention shipped"). Inherited counts (design §5.3) explicitly don't satisfy the threshold — escalation never fires on inherited evidence
- [x] Tier 0 intervention: knowledge entry (`skill/knowledge/*.md`) — `meta._draft_eligible_clusters` walks `eligible_tier_0`'s output in observed-count-descending order, calls `judge.draft_tier_0_skill(cluster, counter, signal_sample)` (new method on `Judge`), materializes the response via `skills.build_skill(kind="knowledge", tier=0, status="pending")` + `skills.write_skill`. `drafts_per_iteration=1` cap per design §12.5. Per-iteration drafting is gated on judge AND skill_dir BOTH wired — Chapter-3-shape runners (no skill_dir) silently skip drafting
- [ ] Tier 1 trigger — Stage 4+ work (not yet — wants the §4d efficacy reversion + rate-delta math first)
- [ ] Tier 1 selection prompt — Stage 4+
- [ ] Plan-slots loaded once at planner-process boot; planner watches the file — Stage 4+ (file shape supported in `skills.py` but planner integration is later)
- [ ] Quarantine window M per cluster (preflight-tuned; default placeholder until usage informs) — Stage 4+

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

> **Measurement half of the SOLID/code-craft pair (plan §8, locked decision #19).**
> `engineering-principles.md` (Chapter 2) instructs; this track verifies. SOLID
> decay is the canonical "silent cluster" — tests pass, code rots — that the
> acute track can't see.

- [ ] Sample cyclomatic complexity, file size, fan-out, churn across repos (design §9.1)
- [ ] Aggregate over time; persist trend snapshots
- [ ] Trend miner runs on rotation events (design §4.3)
- [ ] Surface anomalies (silent clusters) to operator UI (design §9.3); **do not auto-act**
- [ ] Works even when `outcome=unverified`
- [ ] **Wire the explicit link:** longitudinal anomalies that map to a principle in `engineering-principles.md` are tagged as code-craft-decay clusters, so meta can propose tier-1 enforcement of a baseline principle the agent is drifting from (compliance gap, §3e)

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
- [ ] **Two-part tier-3 surface (locked decision #14):** the agent is Node (`pi` framework); the control-plane wrapper is Python. "Code change" can mean the Node agent and/or the Python wrapper — two surfaces, two blast radii. The §6/§11 single-Python-file assumption must be reconciled before building this gate.

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
- [ ] **Founding knowledge upkeep** (plan §3): `agent-knowledge/` stays operator-authored and always-loaded. When the agent's environment changes (a new git-proxy rule, a network change), update `environment.md`. Never meta-authored — keeping the two knowledge layers distinct is what lets `meta` (§7) learn the subtle gaps instead of re-teaching constraints
- [ ] **Alarms** routed to operator UI alongside artifact approvals (design §9.3) — from chapter 4 onward
- [ ] **Golden-journal test suite** (design §12.11): from chapter 3 onward, run on each `meta` release before deploy
- [ ] **Schema-version discipline** (design §12.9): readers tolerate older shapes; migrations are explicit operator jobs; **tier-3 self-changes cannot propose schema changes**
- [ ] **Sanitization drift audit** (design §10.2): evidence-triggered when rejection rate moves outside baseline envelope; sample N raw journal records; human-review for false negatives — from chapter 3 onward

---

## Decision Log

> Append-only. Every status change or scope shift adds a row. Date is decision date.

| Date       | Chapter | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Refs                                 |
| ---------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| 2026-05-19 | n/a     | Plan + tasks docs created from the design doc.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | plan §10                             |
| 2026-05-20 | 1       | Open-terminal network change promoted to a **Tool requirement**. Reachability bounds the blast radius from Tool onward.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | design §3.4                          |
| 2026-05-20 | n/a     | Removed per-task git worktrees from the session model. One focused project cloned directly; one task at a time; FIFO. `/project repo:` wipes and re-clones.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | design §3.4, §12.3, §12.4            |
| 2026-05-20 | n/a     | Removed any "quarterly sanitization floor." Audits are evidence-triggered.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | design §10.2                         |
| 2026-05-20 | 4       | Removed numeric "X%" placeholder for tier-0 efficacy-reversion exit. Qualitative — investigate if patterns look worth investigating.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | plan §8                              |
| 2026-05-20 | n/a     | Design doc reorganized topically (architecture → data → safety → operations → roadmap).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | design — whole doc                   |
| 2026-05-20 | n/a     | Plan restructured from 11 phases into **5 chapters** with explicit stop points. Build agent stops at each chapter boundary; operator advances when ready.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | plan §2                              |
| 2026-05-20 | 1       | Sanitization filter built in Tool, run in **shadow mode**; promoted to enforcing in chapter 3 (Observer). This collects baseline drift behavior during Tool/OWUI usage so chapter 3 doesn't have to wait for it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | design §10.2, plan §5                |
| 2026-05-20 | 1       | All four named volumes declared in Tool (even though only `little-coder-journals/` actively records before chapter 3) so later chapters don't trigger silent wipes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | design §3.6                          |
| 2026-05-20 | 1       | OWUI's current direct access to open-terminal **ends in Tool**; chapter 2 restores it via `lc-mcpo`. Documented as a user-visible change.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | design §3.4, plan §5                 |
| 2026-05-22 | 1       | Upstream little-coder is a Node.js CLI on the `pi` framework, **not Python**. Control-plane wrapper (journals, config, sanitization, git-proxy, CLI, MCP edge) built in Python mirroring `search-mcpo`; the agent container is Node-based. The `agent.py` reference in design §6 is a Chapter-5 illustration only — design doc left unedited (operator's call).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | design §3.1, §6                      |
| 2026-05-22 | 1       | Agent↔open-terminal integration: a fifth named volume `little-coder-workspace` is shared by both containers. The agent edits files on it directly; build/test/git execution is routed to `open-terminal`'s `POST /execute` REST API, keeping execution in the network-isolated plane. Operator-confirmed approach.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | design §1.5, §3.4                    |
| 2026-05-22 | 1       | Control-plane foundation landed and unit-tested (config, journals, audit, sanitization, ULID, git-proxy, URL-norm, open-terminal client, workspace/project-focus) — 120 tests passing. Daemon, agent integration, Dockerfiles, compose pending.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | plan §5                              |
| 2026-05-22 | 1       | Chapter 1 **code-complete**: control daemon, agent integration, CLI, metrics, MCP edge + `lc-mcpo`, Dockerfiles, compose wiring (`lc-net`, 5 volumes, `lc-egress` allowlist proxy, backup job). 134 tests passing. Awaiting deployment + operator verification (the chapter stop point).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | plan §5                              |
| 2026-05-22 | 1       | open-terminal's egress allowlist implemented as `lc-egress` (tinyproxy, default-deny host filter) — mirrors the search stack's Tor-wall pattern. A precise per-host allowlist is not expressible with plain compose networks.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | design §3.4                          |
| 2026-05-22 | 1       | **KNOWN GAP** — the `.git/config` read-only mount (design §3.3) is not implemented; awkward for a path inside a named volume. git-proxy blocks `config`/`remote` at the command level, but a direct file write to `.git/config` bypasses that. Tracked as open item #9.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | design §3.3                          |
| 2026-05-22 | 1       | Chapter 1 **built, deployed, smoke-tested end-to-end** — all 5 services healthy; `lc project` clones via the egress proxy; `lc task` runs agent → LLM → journals; exec routing verified (agent commands run in open-terminal through the git-proxy).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | plan §5                              |
| 2026-05-22 | 1       | Build/test fixes: Node 22 required (not 20); clone uses the relocated real git `/usr/bin/git.real`; `git safe.directory '*'` + `umask 000` make the shared workspace volume usable across container uids; `models.json` override maps `llamacpp` to llama-swap's real model ids; pi extension API corrected against the bundled extensions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | plan §5                              |
| 2026-05-22 | 1       | Agent exec routing made a switch — `LC_ROUTE_EXEC` (compose env, default 1, verified working). 0 falls back to built-in bash inside the network-isolated little-coder container.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | design §3.4                          |
| 2026-05-22 | 1       | `task_abandoned` default lowered to 30m for owui/cli (was 6h per the design §4.2 example) — a saner Tool default and a backstop against agent loops. Open item #3; raise for genuine long refactors. **Observer must cluster `task_abandoned` distinctly from `fail`** (a timeout is a config artifact, not a craft gap) or it will learn a phantom cluster.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | design §4.2, §5                      |
| 2026-05-22 | 2       | Chapter 2 implemented as an OWUI **Pipe function** ("Little Coder" model). Plain messages → task triggers (`channel=owui`, `user_id` = OWUI email); `/`-commands → operator actions gated by the OWUI user role. The Pipe calls the daemon directly over `llm-net` (mnemory trust pattern); `lc-mcpo` joins `llm-net`, registered as an OpenAPI tool exposing triggers only — privilege separation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | design §12.6                         |
| 2026-05-22 | 2       | OWUI Pipe UX reworked after first operator test (it showed only task metadata): the daemon now returns the agent's actual answer (its stdout) and a live `activity` list (commands run, read from the ot-exec event stream). The Pipe streams command progress and renders the answer as the chat reply with a collapsible process log; the `lc` CLI shows the same.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | design §12.6                         |
| 2026-05-22 | 2       | OWUI background generation calls (chat title, tags) were double-triggering real tasks — fixed: the Pipe detects `__task__`/`metadata.task` and answers them cheaply.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | design §12.6                         |
| 2026-05-22 | 2       | **Live streaming + interruption.** The agent runs with pi `--mode json`; the daemon streams its events via `GET /tasks/{id}/events`. The Pipe is an async generator that renders thinking / tool calls / answer into the chat as they happen. OWUI **Stop** → `POST /tasks/{id}/cancel` → the daemon kills the agent's process group (`start_new_session` + `killpg`) — operator-triggered abandonment, consistent with design §12.4. True _mid-flight redirection_ is NOT supported — §12.4 holds human attach read-only.                                                                                                                                                                                                                                                                                                                                                                              | design §12.4, §12.6                  |
| 2026-05-22 | 2       | **ShellSession bypass closed.** The agent escaped the git-proxy via the `ShellSession` tool (which runs locally, unrouted). The `shell-session` + `browser` pi extensions are removed at image build and `permission-gate` set to `accept-all`, so `bash → ot-exec → open-terminal → git-proxy` is the sole execution path.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | design §3.3                          |
| 2026-05-22 | 2       | **Founding knowledge seeded.** `little-coder/agent-knowledge/` — `environment.md` (operating environment + git-proxy policy, so the agent stops rediscovering its constraints each task) and `engineering-principles.md` (SOLID / encapsulation / naming / patterns) — appended to the agent's system prompt via little-coder's own `--append-system-prompt`. Deliberately SEPARATE from the §7 self-improvement skill library: founding knowledge is the operator-authored baseline, the §7 library is the meta-learned layer (Ch.4+). Design-consistent — uses little-coder's own flags (§3.1) and raises the baseline so meta learns subtler gaps (§13).                                                                                                                                                                                                                                             | design §3.1, §7, §13                 |
| 2026-05-22 | 2       | Anthropic skills repo (`anthropics/skills`) reviewed: 17 skills, all domain-capability (documents / design / web / MCP) — none are general coding-craft, so nothing to vendor for little-coder's craft baseline (`engineering-principles.md` authored instead). The valuable part is `skill-creator` — the skill-authoring guidance. **Chapter-4 recommendation:** the §7 meta skill-library should adopt the Agent Skills format (`SKILL.md` + `name`/`description` frontmatter; `description` can feed the §7.4 augmenter; progressive disclosure; <500 lines; "explain-the-why" drafting), layered with §7.1's `cluster_id`/`tier` metadata. The Anthropic two-tier model (baseline always-loaded + specialized discovered-on-demand) matches founding-knowledge + §7. `webapp-testing` / `web-artifacts-builder` / `frontend-design` could be made repo-conditionally available to the agent later. | design §7, §7.4                      |
| 2026-05-22 | n/a     | Skill-development structure promoted from the Decision Log into the plan/task bodies: plan §3 gains a "Two knowledge layers" table (founding knowledge vs the §7 skill library); plan §10 adds locked decision #16; plan §6 + §8 build lists updated; Chapter 2 tasks gain a "Founding knowledge" subsection; Chapter 4 §4a tasks adopt the Anthropic Agent Skills format for §7 artifacts; a founding-knowledge upkeep rule added to cross-cutting. Founding knowledge is layered additively over design §7.1 — the design doc is left unedited (operator's call, consistent with the 2026-05-22 Node.js entry).                                                                                                                                                                                                                                                                                       | plan §3, §6, §8, §10                 |
| 2026-05-22 | n/a     | **Agent Skills format adopted; skill-creator A/B eval loop NOT adopted.** The eval loop (draft → with-skill-vs-baseline → improve) assumes subagents + a fixed eval set little-coder lacks. Cohort efficacy reversion (design §8.5) measures the same thing against production truth and is authoritative. Recorded so the omission reads as deliberate, not missed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | plan §8, locked #18                  |
| 2026-05-22 | n/a     | **SOLID/code-craft is a two-part mechanism.** Instruction lives in `engineering-principles.md` founding knowledge (Chapter 2); measurement lives in the §9.1 longitudinal track (Chapter 4 §4h). SOLID decay is a "silent cluster" the acute track can't see (tests pass). The two are explicitly wired: longitudinal anomalies mapping to a baseline principle become tier-1 compliance-gap signals.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | plan §8, §9.1, locked #19            |
| 2026-05-22 | n/a     | **Founding knowledge raises the tier-0 bar (compliance vs knowledge gap).** A cluster the baseline already covers is a compliance gap (the instruction isn't landing) → escalate to tier-1 enforcement, NOT a tier-0 restatement. The Chapter-3 judge prompt receives founding knowledge and emits a `baseline_covers` field; Chapter-4 tier-0 only fires when `baseline_covers == false`. This is a real refinement to the tier ladder.                                                                                                                                                                                                                                                                                                                                                                                                                                                                | plan §3, §5.6, §7, locked #17        |
| 2026-05-22 | n/a     | **Agent statelessness made explicit.** The agent is stateless across tasks and chat turns (`--no-session`); journals are write-only from its side and feed `meta` only (the `iter_records` reader is never called in the agent path); the OWUI pipe sends only the latest message. Project continuity is the workspace filesystem + git history. Journal-backed **episodic memory** (agent recalling _why_ prior tasks acted) is NOT in the design — git-as-project-memory is the intended boundary. Recorded as locked #20; tracked as open item #10 if ever revisited.                                                                                                                                                                                                                                                                                                                                | plan §3, design §3.1, §4, locked #20 |
| 2026-05-22 | 2       | **Third founding-knowledge file added: `project-context.md`.** Operator feedback ("journaling/context continuity saves a lot of tokens on each agent engagement") motivated a design-aligned response — instead of journal-backed episodic memory (rejected per design §15 / locked #20), teach the agent the cheap git+filesystem inspection patterns it should run at task start. Four-command orientation (`git log` / `git status` / `ls` / `cat README.md`), project-type-file shortcut, and anti-patterns (no `cd`, no `find /`, no mid-task re-orient). Loaded in task order via `extra_args`: environment → project-context → engineering-principles. Extends locked #16 (two knowledge layers) without changing the architecture; statelessness boundary (locked #20) preserved.                                                                                                                | plan §3, §6, design §3.1, §3.7, §15  |
| 2026-05-22 | 1       | **Open item #9 partially closed.** Two-layer hardening landed: (1) `core.hooksPath` set system-wide in the open-terminal image to `/etc/lc-git-hooks` (empty, 0555, baked in), so git's hook lookup never reads `.git/hooks/` — even if a hostile repo or residual bypass drops a script there. (2) A workspace-edge bash filter (`git_artifact_filter.py`) wired into `ot-exec` blocks the obvious direct-write bypasses to `.git/config|hooks/|info/` (redirects, `cp`/`mv`/`install`/`truncate`/`dd of=`, `sed -i`/`awk -i inplace`/`perl -i`) — symmetric with the git-proxy, same `git-proxy: DENIED` marker, journaled as `git_blocked`. 49 unit tests including three documented residual-gap shapes (`python -c`, base64, renamed util) that pass through. **Residual**: open-terminal still runs commands as root; full closure needs `CAP_DAC_OVERRIDE` dropped or a uid split, both bigger and deferred. Acceptable for friendly-upstream workload (current state); the residual gap is tracked on open item #9 and must close before any genuinely hostile-repo workload. | design §3.3, plan §11 (#9)           |
| 2026-05-22 | 2       | **Pager hang fix (OWUI smoke).** The first real OWUI task (`what can you see in your workspace?`) drove the agent's founding-knowledge orientation pattern, and the first command — `git log -n 10` — hung forever, leaving the chat at "Agent working…" until the operator clicked Stop. Root cause: open-terminal's `/execute` endpoint hands commands a pseudo-tty that `less` rejects with "WARNING: terminal is not fully functional / Press RETURN to continue", then blocks on stdin that never arrives. Fix in `Dockerfile.open-terminal`: `git.real config --system core.pager cat` (system-wide; mirrors the existing `core.hooksPath` line) plus `PAGER=cat GIT_PAGER=cat MANPAGER=cat LESS=-FRX` env. Verified post-rebuild: `git log -n 5` returns `status: done` in 0.23s, all four founding-knowledge orientation commands (`git log` / `git status` / `ls` / `cat README.md`) return cleanly. No design/principle change — Chapter 2 stop-point bug surfaced exactly as the design predicted (operator smoke test catching real behavior). | design §3.4                          |
| 2026-05-23 | 2→3     | **Chapter 2 closed; chapter 3 (Observer) opened.** Post-pager-fix the operator's prompt completed cleanly through the Pipe: task `01KS9E1APPV2KVPDWQGDW2FFDD` ran the founding-knowledge orientation (three bash calls, no probing of `cd`/`git`) and produced a coherent answer including the project-type-file shortcut from `project-context.md`. Journal envelope verified for both channels (cli + owui) — `channel`, `user_id`, `session_id`, `repo`, `lang`, `seq`, `schema_version` all present from line 1. Stop-point triggered by the operator's "let's move onto chapter 3" — exactly the qualitative signal the plan §6 names. Note: `lc-mcpo` OpenAPI registration in OWUI is still operator-pending (admin paste), but the Pipe path is the primary surface; mcpo is the alternate-trigger surface and gated by a known operator action, not chapter-3 work. | plan §6, design §13                  |
| 2026-05-23 | 3       | **Observer Stages 1 + 2 landed: data foundations + iteration runner.** Three new modules — `task_shape.py` (heuristic per-task shape classifier, conservative `unknown` fallback per design §5.5), `clusters.py` (immutable `cluster_id`, mutable label, injectable similarity, split/merge lineage per §5.1–§5.3), `cohorts.py` (event-sourced projection over journals per §5.4; atomic-rename checkpoint, deterministic rebuild walking rotated segments; refuses newer-than-build schema_version per §12.9). Then `meta.py` (single-flight `MetaRunner` per §12.5, `should_trigger` evidence threshold per §3.2 — no clock code, `default_similarity` stub returns 0.0 so every occurrence lands in the unassigned pool until Stage 3 wires the judge). `config.ObserverConfig` added (disabled by default), JSON schema regenerated. 49 new tests; 232 passing total. Discovered + fixed: `rebuild()` must walk all three journals (including `tool_calls`) because `task_shape.classify_records` needs the full per-task trace — errors-only walks were producing `unknown` shape for what were really bugfix tasks. Stages 3 (judge prompt + sanitization promotion, LLM-in-the-loop) and 4 (operator surface — `lc admin observe` + `/observe` Pipe command) still to come. | design §3.2, §5, §12.5               |
| 2026-05-23 | 3       | **Observer Stages 3 + 4 landed: judge + operator surface; Chapter 3 build-complete.** `llm.py` — minimal `ChatClient`/`EmbeddingClient` over llama-cpp's OpenAI-compatible endpoints; chat sanitization is enforcing-mode by default (`Sanitizer(mode="enforcing")`) so the control plane never has a "send anyway" path. `similarity.py` — `EmbeddingSimilarity` does cosine over a discriminator anchor with O(N+M) batch preload. `judge.py` — counterfactual+adversarial prompt with founding-knowledge inlined, required `baseline_covers` flag (pydantic enforces, no default → omitting raises `LlmError`), non-overlap+bounds-check on `signal_indices`, `min_pool_size` saves inference budget by skipping thin pools. `Cluster.baseline_covers` added + round-tripped through checkpoint serialization. `observer.py` + `/admin/observe` daemon endpoint + `lc admin observe [--iterate] [--json]` CLI + `/observe [iterate]` Pipe Markdown — all share one `report_dict` shape; clusters split into KNOWLEDGE GAPS (baseline silent — tier-0 candidates) and COMPLIANCE GAPS (baseline covers — tier-1 enforcement) per locked #17. **Deployed and verified**: `observer.enabled: true` in `little-coder.config.yaml`; rebuilt + restarted; first iteration walked 106 accumulated journal records (chapters 1+2 history), surfaced 4 unassigned occurrences across 2 scopes (`python\|unknown`=3, `\|unknown`=1). 35 new tests, 267 passing total. Judge LLM is not yet auto-wired into the daemon's `MetaRunner` — that needs a config switch + the inference client construction at boot; flagged for Stage-5-of-chapter-3 once dry-run prompt calibration (open item #2) is done. | design §3f, §10.1, locked #17        |
| 2026-05-23 | 4       | **Chapter 4 Stage 3 landed: tier-0 escalation + judge drafting.** Three pieces: (1) `tier_ladder.py` — pure escalation policy. `evaluate_tier_0(cluster, counter, prior)` returns an `Escalation` with `eligible`/`reason`; the locked-#17 compliance-vs-knowledge rule is the first check (`baseline_covers=True → ineligible`, reason names tier-1 enforcement). Inherited counts (design §5.3) don't satisfy the threshold. (2) `judge.draft_tier_0_skill(cluster, counter, signals)` — new method on `Judge`. Prompt is counterfactual-aware (the judge can second-guess `baseline_covers` and refuse to draft, returning `escaped_to_compliance=True`); founding knowledge is inlined into the prompt context per locked #17; output schema enforces `name`/`description`/`body` and the `baseline_covers` flag. (3) `meta._draft_eligible_clusters` wires the ladder + judge + skill writer into iteration; `drafts_per_iteration=1` cap (design §12.5); a wired-but-empty `skill_dir` cleanly disables drafting (Chapter-3-shape backwards-compat). `meta_wiring.build_meta_runner` now passes `skill_dir=config.paths.skill_dir` when both `observer.enabled` and `judge_enabled` are on. **Discovered + fixed a real design gap**: `iterate()` was calling `rebuild()` which discards clusters minted by the judge (clusters aren't in journals — only the judge produces them). Added `_rebuild_carrying_clusters` that loads the prior checkpoint, resets counters/unassigned (design §5.4 — those ARE journal-derived), then re-projects onto the seeded clusters. A follow-up should journal cluster-minting events to `audit.jsonl` so even cluster identities are replay-recoverable, tracked as a design refinement not blocking Chapter 4. 23 new tests (10 tier_ladder + 7 judge drafting + 5 meta integration + 1 daemon wiring); 345 passing total. Stage 4 (validation gates — in-context assertion + efficacy reversion) follows. | design §5.6, §5.4, §7, locked #17    |
| 2026-05-23 | 4       | **Chapter 4 Stage 2 landed: per-task augmenter.** `augmenter.py` runs the §7.4 selection pipeline — hard tag filter (`lang`/`domain`/`task_shape`/`tool` with `*` wildcards, case-insensitive lang), embedding rank over `description` (NOT body — long bodies can't unfairly outrank precise descriptions), greedy fit to token budget with `(-score, cohort_proven)` composite sort key. **Tier is NOT a tiebreaker** (locked in design and pinned by `test_tier_is_NOT_a_tiebreaker_on_its_own`). Cohort-proven is INJECTED (Stage 4 wires it to efficacy data); Stage-3 default returns False. `SkillSelection` carries `selected` + `rejected[reason]` + `.includes(skill_id)` for the §8.4 in-context assertion. 21 new tests covering each pipeline step + tiebreaker rules. Pure module, no LLM. | design §7.4, §8.4                    |
| 2026-05-23 | 4       | **Chapter 4 (Learner) Stage 1 landed: skill library data model.** New module `skills.py` — `SkillFrontmatter` pydantic model with `extra="forbid"` enforcing Agent Skills format (`name`, `description`) + §7.1 metadata (`id`, `cluster_id`, `tier`∈{0,1,2}, `kind`∈{knowledge,tool,plan_slot}, `lang`, `domain`, `tool`, `task_shape`, `created`, `supersedes`, `status`∈{active,superseded,retired,pending}). `Skill.serialize()` writes YAML frontmatter + body with `exclude_none=True` (no `supersedes: null` noise on freshly-minted artifacts). `parse_skill` is the strict round-trip inverse; `build_skill` constructs from the judge's drafted fields, wrapping pydantic errors as `SkillFormatError` so callers handle drafting failures uniformly. `write_skill` does atomic `.tmp` + `rename(2)` with a round-trip self-check (corrupt drafts can't be published). `iter_skills` walks all three subdirs (knowledge / tools / plan-slots), filters `*.tmp` files, swallows individual-file format errors so one corrupted artifact doesn't blind the augmenter. `flip_status` does the atomic rewrite for supersession + efficacy retirement, preserving the body. Files land at `<id>.md` (not slugified name) so a label rename never moves the file — `id` is immutable per design §5.1's identity-vs-label rule. 29 new tests; 300 passing total. Stage 2 (augmenter) and Stage 3 (judge extension for drafting) follow. | design §7, §7.1, §7.3, §7.5          |
| 2026-05-23 | 3       | **Pipe `/observe` bugfix — `_call` got a `params` kwarg.** The Chapter-3 `/observe` slash-command landed with a TypeError: it called `self._call("GET", "/admin/observe", params=params)` but the Pipe's `_call` signature only took `body`. Any operator who typed `/observe` would have hit "TypeError: unexpected keyword argument 'params'". Fixed by adding `params: Optional[dict] = None` to `_call` and threading it through to `aiohttp.session.request(..., params=params)`. Pipe version bumped to 0.4.0. README operator-reference table updated with `/observe [iterate]`. The Pipe must be re-pasted into OWUI Admin → Functions → Little Coder for the fix to take effect; the daemon side is unchanged. | design §12.6                         |
| 2026-05-23 | 3       | **Chapter 3 Stage 5 finished — judge auto-wire + auto-iteration + Observer metrics; build-complete.** Three additions: (1) `meta_wiring.build_meta_runner(config)` constructs the right `MetaRunner` flavor — three modes, gated by `observer.enabled` × `observer.judge_enabled`. When both are on, an `EmbeddingClient` (llama-cpp-embed, `bge-m3-f16.gguf`) + `EmbeddingSimilarity` + `Judge(ChatClient with enforcing-mode Sanitizer)` are constructed at boot. Lifted out of `daemon.py` so tests can run without the FastAPI/uvicorn pull-through. (2) `daemon._maybe_trigger_meta` — evidence-triggered auto-iteration after each task ends; fires only when `auto_iterate_on_task_end: true` AND `should_trigger(state, count, threshold)` returns true. Errors are journaled to `audit.jsonl` (new events `observer_iteration_completed` / `observer_iteration_failed` added to the audit whitelist), single-flight in MetaRunner drops concurrent calls. (3) Prometheus metrics: `lc_meta_iterations_total`, `lc_meta_iterations_failed_total`, `lc_meta_clusters`, `lc_meta_occurrences`, `lc_meta_unassigned`, `lc_meta_clusters_minted_total`. Deployed config sets `auto_iterate_on_task_end: true`, leaves `judge_enabled: false` per design §13 dry-run discipline (open item #2). Verified live: `iterate=true` ran, metrics ticked, audit row appeared. +4 daemon-wiring tests; 271 passing total. **Chapter 3 build surface is now done.** Remaining items are operator actions: dry-run the judge prompt (open #2), flip `judge_enabled` when calibrated, accumulate cluster-eligible journals via real tasks, decide whether minted labels are trustworthy → trigger chapter-3 → 4 advance. | design §3.2, §9.3, §13, locked #17   |

---

## Open items (mirror of plan §11)

Tuned by preflight (observed usage) unless noted.

- [ ] **#1** Polyglot N + regression margin (baseline variance collected during Tool/OWUI; computed at Observer; used in Learner)
- [ ] **#2** Counterfactual judge prompt wording + few-shot (Observer dry-run; resolves at Decision Log entry)
- [ ] **#3** `task_abandoned` timeout per channel (Tool default usable; tune during Tool/OWUI usage; lock by Learner). **Observer must cluster `task_abandoned` distinctly from `fail`.**
- [ ] **#4** Neutral test-runner for design §11.1 step 3 (later hardening; not blocking Self-modifier)
- [ ] **#5** Sanitization audit drift threshold — **rephrased 2026-05-23**: at chapter-2 close `lc_sanitization_processed = 0` because Tool/OWUI has no outbound (judge + PRs only start in Observer+). The "Tool-era shadow baseline" the design §10.2 / plan §5 envisaged never accumulated. Baseline forms during Chapter 3 once judge calls begin; the drift threshold gets set from that real Observer-era data, not a synthetic Tool placeholder.
- [ ] **#6** Reserved-slot promotion threshold (`meta` GPU) — Learner+ if starvation observed
- [ ] **#7** Backup cadence + restore drill (Tool: backup cadence decided; drill before Learner chapter merges first artifact)
- [ ] **#8** `.git/config` flexibility upgrade (deferred; no current trigger)
- [~] **#9** `.git/config` / `.git/hooks` / `.git/info` read-only **enforcement** for the agent + `core.hooksPath` outside `.git/` (design §3.3). **PARTIAL CLOSURE 2026-05-22**: (a) `core.hooksPath` set system-wide in `Dockerfile.open-terminal` to `/etc/lc-git-hooks` (empty, 0555, operator-controlled — done); (b) workspace-edge bash filter (`git_artifact_filter.py`) blocks the obvious direct-write bypasses (`>`, `>>`, `tee`, `cp`/`mv`, `sed -i`, etc.) into `.git/config|hooks/|info/`, symmetric with the git-proxy — same `git-proxy: DENIED` marker, journaled as `git_blocked`. **Residual**: open-terminal runs commands as root, so a determined attacker via `python -c '...write...'`, base64-obfuscated paths, or a renamed util still reaches `.git/config`. Full closure requires dropping `CAP_DAC_OVERRIDE` from open-terminal (so chmod-based read-only enforces kernel-side) or a uid split between setup and agent execution — both bigger than the partial closure and not done. Acceptable for current friendly-upstream workload; resolve before real hostile-repo workload.
- [ ] **#10** Journal-backed **episodic memory** for the agent (task-context assembly at the daemon layer — scoped slice of `outcomes.jsonl`/`audit.jsonl` for the current repo into the prompt, distinct from the §7 skill loop). Not in the current design; git-as-project-memory is the boundary. A deliberate future decision if the stateless boundary proves limiting.

---

## How to drive this

1. Active chapter is at the top.
2. Work tasks top-to-bottom within the chapter; mark `[~]` when starting, `[x]` when the **acceptance criterion** in the task is met (not just "code written").
3. On chapter stop-point satisfaction → flip chapter status; add Decision Log row; bump `Last updated`.
4. **The build agent stops at the chapter boundary.** Resuming work means deliberately starting the next chapter — not a hand-off the agent makes by itself.
5. New decisions → Decision Log first, then update [`integration-plan.md`](integration-plan.md) lock table if permanent.
6. Discovered scope → if it lives inside an existing chapter, add a task; if it's a new gate, propose a new chapter in the plan first.
7. **Design doc wins on conflict.** If a chapter here implies something the design doc forbids, fix this doc.
