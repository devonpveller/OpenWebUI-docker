# Self-Improving Little-Coder — Task Tracker

> **Last updated:** 2026-05-23
> **Plan:** [`integration-plan.md`](integration-plan.md) — chapters, rationale, locked decisions.
> **Design doc:** [`Self-improving-little-coder-design.md`](Self-improving-little-coder-design.md) — source of truth. Design doc wins on conflict.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → bump `Last updated` and add a Decision Log row if a choice changed.

## Current state

- **Active chapter:** 2 (OWUI pipeline) — chapter 1 (Tool) built, deployed, tested, and operator-accepted (2026-05-22). Chapter 2 in progress; see the Chapter 2 section.
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

- [ ] Daily flow feels stable; no new bugs in the basic pipeline — _operator judgment over time_
- [x] Journals are accumulating with the full envelope; no fields missing — verified on the first tasks
- [x] `audit.jsonl` records every project switch + shutdown — verified
- [x] Volumes survive at least one intentional `docker compose up -d --build` rebuild — verified
- [ ] Sanitization rejection rate (shadow mode) has a stable baseline — _needs accumulated runtime_
- [ ] **You find yourself wanting to drive little-coder from chat as well as CLI** — this is the actual trigger to advance

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
- [ ] Operator smoke test: drive a task end-to-end via OWUI; verify the journal records `channel = owui`, `user_id = <OWUI user>`

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
- [ ] Operator-verified in a task: the agent no longer probes `cd` / `git` to rediscover its environment, AND uses the four-command orientation pattern (one `git log`, one `git status`, one `ls`, one `cat README.md`) then stops — instead of cat-ing every file or running redundant probes — _needs the next OWUI task_
- [ ] **`engineering-principles.md` SOLID/code-craft content is the instruction half of a pair** — the measurement half is the §9.1 longitudinal track (Chapter 4 §4h). When authoring principles, keep them phrased so the longitudinal metrics (complexity, fan-out, churn) can later detect decay against them. No build action in Chapter 2 beyond awareness; the link is wired in Chapter 4.

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
- [ ] `meta` consumes journals via `iter_records` (the reader built in Chapter 1, never called from the agent path)

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
- [ ] **Founding knowledge in the judge's context (plan §3, locked decision #17).** The judge receives `agent-knowledge/environment.md` + `engineering-principles.md`. A cluster the baseline already covers is a **compliance gap** → escalate to tier-1 enforcement (tool-craft / plan-slot), **not** a tier-0 restatement. The prompt instructs the judge to check the baseline first and never re-draft what it already says. Add a `baseline_covers: true|false` field to the structured output so this is auditable.
- [ ] Dry-run against real journals; human-rate outputs — include cases the baseline covers, to verify the compliance-vs-knowledge distinction fires
- [ ] Resolve open item #2 → Decision Log
- [ ] **Promote sanitization filter from shadow to enforcing** for judge calls (design §10.2): filter failure aborts the call
- [ ] Set sanitization drift threshold from Tool-era baseline (open item #5 → Decision Log)

### 3f. Observer surface

- [ ] `meta` produces _reports_ (clusters, occurrences, candidate craft gaps) viewable through the operator surface
- [ ] No artifacts drafted, no merges proposed
- [ ] Reports visible in CLI (`lc admin observe`) and OWUI (`/observe`)
- [ ] Reports distinguish **knowledge gaps** (baseline silent) from **compliance gaps** (baseline covers it but the agent isn't following) — the tier-0-vs-tier-1 signal from §3e

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

- [ ] Directory layout per design §7: `skill/knowledge/*.md`, `skill/tools/*.md`, `skill/plan-slots/*.md`
- [ ] Each artifact authored as a `SKILL.md` with `name` + `description` frontmatter (Agent Skills format): lean body, progressive disclosure (link heavy reference material rather than inlining), under ~500 lines, "explain-the-why" drafting
- [ ] Frontmatter schema enforced at draft time — Agent Skills fields (`name`, `description`) + design §7.1 fields (`id`, `cluster_id`, `tier`, `lang`, `domain`, `tool`, `task_shape`, `created`, `supersedes` (null), `status` (`active`))
- [ ] `description` field written for discovery — it feeds the §7.4 augmenter's tag/embedding selection
- [ ] Judge drafting prompt instructed in the Agent Skills authoring conventions (description-driven discovery, progressive disclosure, explain-the-why)
- [ ] **Tier-0 only fires for genuine knowledge gaps** — the §3e `baseline_covers` check gates this: a baseline-covered cluster enters at tier-1, not tier-0 (locked decision #17)
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

> Cohort efficacy reversion is the production-truth validation. The skill-creator
> A/B eval loop is deliberately not adopted (Decision Log) — efficacy reversion
> is its equivalent and is authoritative.

- [ ] In-context assertion (design §8.4): if augmenter didn't select the artifact during validation → gate result = **void**, not pass
- [ ] Single-exercise flip inside noise margin is **not** a regression (design §8.3)
- [ ] Efficacy reversion (design §8.5): post-window indistinguishability → flag `ineffective` → auto-revert next iteration. Retirement journaled to `audit.jsonl`
- [ ] Augmenter selects only `active`

### 4e. Tier 0 and Tier 1

- [ ] Tier 0 trigger: N ≥ ~5 occurrences in a cluster, no prior intervention (design §5.6) — **and** `baseline_covers == false` (§3e); a baseline-covered cluster is a compliance gap → tier-1
- [ ] Tier 0 intervention: knowledge entry (`skill/knowledge/*.md`)
- [ ] Tier 1 trigger: ~20+ new occurrences after tier-0, rate unchanged — **or** a baseline-covered cluster recurring (compliance gap; enters at tier-1 directly)
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

---

## Open items (mirror of plan §11)

Tuned by preflight (observed usage) unless noted.

- [ ] **#1** Polyglot N + regression margin (baseline variance collected during Tool/OWUI; computed at Observer; used in Learner)
- [ ] **#2** Counterfactual judge prompt wording + few-shot (Observer dry-run; resolves at Decision Log entry)
- [ ] **#3** `task_abandoned` timeout per channel (Tool default usable; tune during Tool/OWUI usage; lock by Learner). **Observer must cluster `task_abandoned` distinctly from `fail`.**
- [ ] **#4** Neutral test-runner for design §11.1 step 3 (later hardening; not blocking Self-modifier)
- [ ] **#5** Sanitization audit drift threshold (Tool-era shadow-mode baseline; resolves at Observer chapter when filter goes enforcing)
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
