# Self-Improving Little-Coder — Task Tracker (LIVING DOCUMENT)

> **Last updated:** 2026-05-19
> **Plan:** [integration-plan.md](integration-plan.md) — phases, rationale, locked decisions. Update both together.
> **Design doc:** [Self-improving-little-coder-design.md](Self-improving-little-coder-design.md) — *why* and *what*; this tracker is *how* and *when*. Design doc wins on conflict.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → also bump _Last updated_ and add a Decision Log row if a choice changed.

## Current state

- **Active phase:** Phase 0 — Prerequisites & open-terminal hardening (not started).
- **Build sequence:** §16 of the design doc; 11 phases gated. No phase parallelism except Phase 9 alongside Phase 8.
- **Tier-0 build requirement reminder:** `session_id` + `channel` + `user_id` on every journal line from the very first write. Unrecoverable retroactively (§16 step 1).

---

## Phase 0 — Prerequisites & open-terminal hardening

> **Goal:** lock prerequisites; scope the open-terminal compose change that the autonomous loop will eventually need. Refs: design §17, §15, §12.

- [ ] Verify `http://llama-cpp:8080/v1` reachable from a throwaway container; confirm both `qwen3.6:27b` and `qwen3.6:27b-nothink` respond
- [ ] Inventory current `open-terminal` compose block: `network_mode`, capabilities, mounts, env. Document delta to §17 target posture (own network + explicit egress only)
- [ ] Decide compose layout (extend `ai-stack` compose vs new compose file imported in). Match existing `mcpo`/`search-mcpo` pattern
- [ ] Confirm volume conventions (named volumes, not bind mounts) and backup pattern (Alpine-cron, mirroring `mnemory-backup`)
- [ ] Pick the **private** self-improvement git remote (per §12); register it in design doc / plan if not yet specified
- [ ] Provision fine-grained PAT scoped to `contents:write` on that remote only (no repo deletion, no admin, no other repos)
- [ ] Confirm where the private search gateway lives in the egress allowlist (already in stack per §17 grounding)
- [ ] Produce gap list; pause if anything blocks
- **Exit:** gap list closed; inference + remote + PAT reachable from the right networks; open-terminal compose delta scoped (not yet shipped)

---

## Phase 1 — Container scaffold + journals + persistence

> **Goal:** `agent` as MCP server behind `lc-mcpo`; full §19 journal envelope from line 1; named volumes declared. Refs: §4, §16 step 1, §17, §19, §24.

### 1a. Container + MCP server

- [ ] Build `little-coder` container image (base + little-coder REPL); pin to a known-good upstream commit per §17 ("upstream pulls are operator-initiated")
- [ ] Run `agent` as an MCP server (not raw HTTP/socket) — mirror the search gateway pattern
- [ ] Build `lc-mcpo` sidecar: MCP→OpenAPI bridge, API-key'd at the edge (per §23 MCP edge authentication)
- [ ] Compose healthchecks: `agent` (MCP socket responding), `lc-mcpo` (`/openapi.json` reachable)
- [ ] LLM client default → `qwen3.6:27b-nothink` for inner-loop work; reasoning variant reserved for judge/drafting (Phase 3+)

### 1b. Journals (tier-0 build requirement)

- [ ] Implement journal writers for `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl`
- [ ] Envelope on every line (per §19): `ts` (UTC), `task_id` (ULID), `session_id`, `channel`, `user_id`, `repo`, `lang`, `seq`, `schema_version`
- [ ] Write-time schema validation; malformed records **rejected, not appended**
- [ ] `task_started` / `task_ended` records in `outcomes.jsonl`; reconstruct by `task_id`, never by adjacency
- [ ] Outcome label: `pass` / `fail` / `unverified` (no asserting `pass` without a checkable signal)
- [ ] Durability: append + fsync on every terminal and every error record; line-buffered for the rest
- [ ] `schema_version` plumbed through readers; tolerate older shapes (forward-compat, §24)

### 1c. Persistence

- [ ] Declare named volumes at compose time: `little-coder-skill`, `little-coder-journals`, `little-coder-cohorts`, `little-coder-polyglot`
- [ ] Mount into `agent` (and later `meta`); confirm `docker compose up -d --build little-coder` preserves volumes (this is the §16 step 1 unrecoverable failure mode)
- [ ] Backup job (Alpine-cron daily default; cadence + restore drill tracked as open item #8 in plan)

### 1d. Shutdown semantics (§24)

- [ ] SIGTERM drain mode: refuse new task triggers (Flow 1/2 return "shutting down"); allow in-flight to a configurable deadline; then SIGKILL
- [ ] Open `task_id`s past the deadline → journaled `task_abandoned` with reason `shutdown`
- [ ] Operator override `lc admin shutdown --drain-deadline 30m` (CLI subcommand stub OK; full surface in Phase 2)

- **Exit:** a Flow 2 trigger completes end-to-end against a throwaway repo; every journal line validates; journals survive a rebuild; healthchecks green.

---

## Phase 2 — Workspace edge + CLI operator surface + preflight start

> **Goal:** open-terminal **is** the workspace (§17); CLI operator surface live; preflight workload collecting attributed journals. Refs: §17, §19, §24, §14.

### 2a. Session model (§17)

- [ ] Per-task git worktree: `/workspace/sessions/<session_id>/` off a canonical clone living on the journal/cohort volume mount in open-terminal
- [ ] Per-session-id: serial (a session never runs two things at once)
- [ ] Per-repo (across sessions): serial — **queued, not rejected** (FIFO; a CLI trigger arriving during an OWUI task waits its turn)
- [ ] Validation worktrees: own worktree, parallel to interactive work, bounded to N=1 to start
- [ ] Human attach: read-only to a session's worktree (writes during inner loop collide unrecoverably)
- [ ] On task completion: push back to canonical clone; `git worktree remove`

### 2b. Project focus (§17)

- [ ] `/project repo: <link>` CLI subcommand
- [ ] URL normalization: host + owner + repo, lowercased (prevents SSH/HTTPS-form spurious wipes)
- [ ] No current focus → clone, set focus, journal `project_switched`
- [ ] Matches current focus → no-op
- [ ] Different focus + active sessions or active validation worktrees → **reject** with list, suggest cancel-or-wait
- [ ] Different focus + clear → tag prior state, tear down worktrees, clone, set focus, journal `project_switched`

### 2c. CLI operator surface (§24)

- [ ] `lc admin project switch <link>` (alias of `/project repo:`)
- [ ] `lc admin pending` — list pending artifacts (will be empty until Phase 4)
- [ ] `lc admin approve <id>` / `lc admin reject <id>`
- [ ] `lc admin upstream pull` — wire the journaling now (`upstream_pulled` with old+new commit ids); tier-3 merge behavior lands in Phase 11
- [ ] `lc admin shutdown [--drain-deadline 30m]`
- [ ] `lc admin task confirm <task_id> [pass|fail]` — emits `task_outcome_amended` (§19); 7-day window; last-amendment-wins within window; frozen outside

### 2d. Audit log (§24)

- [ ] `audit.jsonl` writer, separate from the three task journals
- [ ] Records: `project_switched`, `upstream_pulled`, `approve_decision`, `task_outcome_amended`, `artifact_retired`, deploys, preflight exit
- [ ] Longer retention than task journals; different access controls

### 2e. Preflight start (§14)

- [ ] Real OWUI/CLI users drive real workloads against open-terminal
- [ ] Journals on, **meta-loop off** for ≥ 1–2 weeks
- [ ] Begin collecting baseline metrics: cluster occurrences, sanitization rejection rate (filter exists in Phase 3 — preflight overlaps), Polyglot variance (Phase 5)

- **Exit:** preflight is live; project-switch wipes workspace cleanly without touching named volumes; `audit.jsonl` records every operator action.

---

## Phase 3 — Cluster taxonomy + judge prompt + sanitization

> **Goal:** judge path with privacy controls **before** the judge ever runs on real data. Refs: §20, §10, §23, §22.

### 3a. Cluster identity (§20)

- [ ] Immutable synthetic `cluster_id` + mutable human label
- [ ] Cohort records key on `cluster_id`; relabel never touches cohort history
- [ ] Ingest-time assignment: nearest existing cluster above similarity floor (embedding + judge-written discriminator); below floor → `unassigned` pool
- [ ] Judge mints new `cluster_id` only when `unassigned` forms a coherent group
- [ ] Split/merge lineage records: parent↔child `cluster_id`s; `inherited` vs `observed` counts; escalation cannot fire on inherited counts; merge sums observed + resets quarantine
- [ ] **Cohort scoping per `lang` + `task_shape`**, aggregated across repos (`repo` recorded for drill-down only)

### 3b. Cohort store (§20)

- [ ] Cohort counters as event-sourced projection over journals (derived index, not primary state)
- [ ] Periodic checkpoint; **rebuildable from journals on demand**
- [ ] `schema_version` on the cohort store; bump-and-rebuild on schema change
- [ ] Cohort record fields per §6: occurrences before intervention, intervention timestamp, occurrences after, rate delta, quarantine window M (per cluster, not global)

### 3c. Judge prompt (§23, §2)

- [ ] Draft counterfactual + adversarial system prompt: "If a single fact, heuristic, or strategy had been in the agent's context, would it have flipped the outcome — and what would that thing say?"; argue both sides; pick
- [ ] Few-shot examples drawn from preflight journals
- [ ] Output format: structured (cluster id, proposed type, proposed text, reasoning, why-not-other-types)
- [ ] Dry-run against real journals; human-rate outputs
- [ ] Resolve open item #2 (judge prompt wording) → Decision Log when settled

### 3d. Sanitization filter (§23)

- [ ] Filter: redact secrets/key-shaped strings; reduce large file bodies to structural digests; strip PII
- [ ] **Pinned and tested** against a fixed test set (seeded false-positives and false-negatives)
- [ ] Apply on: judge calls; PR-body assembly (§18 step 6, lands in Phase 11); any future operator-export path
- [ ] **Filter failure aborts the call** — never "send anyway"
- [ ] Metric: rejection rate over rolling window (feeds §23 drift trigger)
- [ ] Quarterly hand-audit floor (§23) — schedule recorded in plan/audit log even if first run is far away

- **Exit:** judge prompt dry-runs pass human rating on real journals; sanitization catches the seeded test cases (both directions); cohort store correctly rebuilds from a journal replay.

---

## Phase 4 — Meta tier-0 with manual review

> **Goal:** outer loop produces tier-0 knowledge artifacts only; every one routes to operator surface. Refs: §5, §22, §24, §6.

### 4a. `meta` process (§4)

- [ ] Build `meta` as a separate process (in same or sibling container — §24 sub-service note keeps the split-later option clean)
- [ ] Clustering ingest → judge counterfactual call → tier-0 artifact draft
- [ ] **Single-flight lock** on `meta` iterations
- [ ] Triggered by **evidence thresholds (§6)**, not a clock

### 4b. Tier-0 ladder (§6)

- [ ] Trigger: N ≥ ~5 occurrences in a cluster, no prior intervention
- [ ] Intervention: knowledge entry (`skill/knowledge/*.md`)
- [ ] Quarantine window M per cluster (preflight-tuned; default placeholder until Phase 8)
- [ ] Cohort accounting (not raw counters): before/after windows + rate delta

### 4c. Artifact draft + frontmatter (§22)

- [ ] Frontmatter required fields enforced at draft time: `id`, `cluster_id`, `tier`, `lang`, `domain`, `tool`, `task_shape`, `created`, `supersedes` (null), `status` = `active`
- [ ] No artifact written without all keys
- [ ] Supersession plumbing: new artifact on existing `cluster_id` sets `supersedes`, flips prior to `superseded`

### 4d. Augmenter (§22)

- [ ] Hybrid selection: hard filter on structured tags (lang/domain/task-shape from trigger + early tool calls) → embedding rank within that set → **hard token budget**
- [ ] Over-budget tiebreaker: cohort-proven (§21) + tighter match wins; **tier is not a tiebreaker on its own**
- [ ] Per-task augmenter selection logged (required by §21 in-context assertion)

### 4e. Hot-reload discipline (§22)

- [ ] Atomic-rename writers everywhere watcher-readable files exist (skill loader, plan-slots, routing rules)
- [ ] Readers ignore `*.tmp`
- [ ] No naive `inotify` reads of in-flight writes

### 4f. Operator surface entries (§24)

- [ ] Pending list rendered with: artifact text, §23 provenance (cluster_id + journal evidence range), §20 cohort evidence
- [ ] Approve/reject = the merge gate. Approve → git-proxy merges to `auto/<date>-<topic>` (Phase 6 hardens this); pre-Phase-6 use a direct file write to the skill volume and journal it
- [ ] Surface contradiction flags (§22) when a periodic judge pass over the active set flags cross-cluster contradictions
- [ ] Surface efficacy-reversion notices (§21, lands in Phase 5)

### 4g. Budgets + deferral (§24)

- [ ] Per-window caps: artifacts/iteration = 1; judge wall-clock minutes/day; Polyglot exercise-runs/day (placeholder until Phase 5); journal write rate
- [ ] Exceeding → **defer**, never drop evidence
- [ ] Deferred iterations: **coalesce per `cluster_id`** (multiple deferred for same cluster collapse into one entry); cross-cluster FIFO; no cluster starved by another
- [ ] Soft-limit queue depth → operator alarm; hard-limit → coalesce
- [ ] Cluster + cohort re-read fresh from journals when coalesced entry runs (avoid stale snapshots)

### 4h. Resource isolation (§24)

- [ ] `meta` checks llama-cpp slot occupancy before issuing inference; back off when interactive lanes busy
- [ ] Interactive always wins
- [ ] No llama-swap config changes required (pragmatic-first cut)

- **Exit:** ≥ 1 tier-0 knowledge artifact drafted → surfaced → human-approved → merged. A relabel of an existing cluster does not reset cohort history.

---

## Phase 5 — Polyglot oracle + real validation semantics

> **Goal:** turn §13/§21 "must not regress" into a real, parameterized gate. Refs: §13, §21, §22, §15.

### 5a. Polyglot harness (§13)

- [ ] Canonical Polyglot clone in `little-coder-polyglot/` volume
- [ ] Wrap behind `Oracle` interface: `run_subset(cluster_id, biased_subset) → ScoredResult` (cheap DIP now; rewrite-class expensive if deferred)
- [ ] Biased subset selection: exercises in the cluster's domain over a uniform random subset
- [ ] Result schema: per-exercise pass/fail + score + duration + augmenter selections during the run

### 5b. Baseline + regression margin (§21, open item #1)

- [ ] **Measure Polyglot variance** during preflight on the current biased subset
- [ ] Set N (minimum subset size) and regression margin from measured variance — not by guess
- [ ] Baseline = score at the last `main` green tag (§12), **re-measured on the current biased subset** (no stale globals)
- [ ] Successful merge sets the new baseline → Decision Log
- [ ] Below N → result is **"insufficient evidence"**, not pass

### 5c. In-context assertion (§21)

- [ ] During validation, log augmenter selections per task
- [ ] If the artifact was **not** in-context for any validation task, gate result = **void**, not pass
- [ ] Without this, the gate measured nothing

### 5d. Efficacy reversion (§21)

- [ ] After the post-merge cohort window, if post-intervention rate is statistically indistinguishable from pre → flag `ineffective`
- [ ] Auto-revert on next iteration (revert is §4-whitelisted)
- [ ] Retirement journaled to `audit.jsonl`
- [ ] Augmenter selects only `active` (already enforced in Phase 4)

### 5e. Single-exercise flip handling (§21)

- [ ] A single-exercise flip inside the noise margin is **not a regression**
- [ ] Document the threshold inline in the validator's output

- **Exit:** an artifact that does not help its cluster auto-retires after one window; one that does pulls the rate as expected; a deliberately-noisy artifact PR is blocked by the gate.

---

## Phase 6 — git-proxy at the workspace edge

> **Goal:** the agent has zero un-proxied raw-git paths. Refs: §4, §12, §17, §23.

### 6a. Proxy binary

- [ ] Build `git-proxy` wrapper; site at **open-terminal workspace edge** (the git binary inside the workspace IS the proxy)
- [ ] No raw-git fallback reachable by the agent
- [ ] Whitelist: `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (restricted to operator-pre-configured remotes; no all-refs; no new remotes mid-task)
- [ ] Blocklist: `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, **all `submodule` subcommands**, all history rewrites, anything touching `.git/` directly

### 6b. `.git/` mounts (§4)

- [ ] `.git/config`, `.git/hooks/`, `.git/info/` mounted **read-only to the agent**
- [ ] Allowed remotes baked in by operator at project-switch time
- [ ] Dynamic paths stay writable: `.git/objects/`, `.git/refs/`, `.git/index`, `.git/worktrees/<id>/`
- [ ] `core.hooksPath` set to operator-controlled directory **outside** `.git/` (belt-and-suspenders)
- [ ] `.gitattributes` filter directives no-op (because `filter=foo` must be declared in read-only `.git/config`)
- [ ] `.gitmodules` cannot trigger submodule clone (blocklist above)

### 6c. Branch / tag discipline (§12)

- [ ] Outer-loop changes land on `auto/<date>-<topic>` branches; never direct to `main`
- [ ] Tag `pre-iteration-<n>` before each outer-loop attempt
- [ ] Rollback target = active instance / last green tag

### 6d. Credentials separation (§23)

- [ ] **Per-repo deploy tokens** for work repos: least-privilege, injected per task, never ambient
- [ ] Self-improvement PAT lives with `meta`/git-proxy; unreachable from workspace plane
- [ ] A task for repo A cannot reach repo B

### 6e. Adversarial tests

- [ ] Synthetic test: agent attempts `remote add` via direct `.git/config` write → blocked (`.git/config` is read-only)
- [ ] Synthetic test: hostile `.gitmodules` in a workspace repo → no submodule clone occurs on any whitelisted op
- [ ] Synthetic test: agent attempts `git push --force` → blocked; journaled

- **Exit:** every git op the agent makes goes through the proxy; the three adversarial tests pass.

---

## Phase 7 — OWUI pipeline (operator parity)

> **Goal:** real OWUI implementation of the operator surface, not a chat passthrough. Refs: §17, §24, §23.

- [ ] Register little-coder's MCP server as an OWUI tool via `lc-mcpo` OpenAPI
- [ ] Task triggers stream conversationally (long, streaming, chat-shaped)
- [ ] Slash-commands wired (each maps to the same control-plane entry as the CLI):
  - [ ] `/project repo:`
  - [ ] `/upstream pull`
  - [ ] `/approve <id>`
  - [ ] `/reject <id>`
  - [ ] `/pending`
  - [ ] `/confirm <task_id> pass|fail`
- [ ] Artifact-review messages: structured rendering with artifact text + cohort evidence + provenance + Approve/Reject controls
- [ ] Privilege separation (§24): operator commands authenticated at OWUI surface (using OWUI's configured auth); MCP server only authenticates task triggers (API key)
- [ ] Alarms surfaced inline with artifact approvals (queue-depth, sanitization-drift, candidate-validation-timeout)
- [ ] Operator smoke test: approve one artifact end-to-end through OWUI; verify identical merge effect to the CLI path

- **Exit:** an artifact can be fully approved from OWUI with the same effect as the CLI path; UI smoke pass by operator.

---

## Phase 8 — Exit preflight → tier-0 auto-merge + tier-1

> **Goal:** §24 preflight exit checklist met; trust tier-0 to auto-merge; build tier-1. Refs: §24, §14, §5, §22.

### 8a. Preflight exit (§24)

- [ ] (a) ≥ K distinct clusters each have ≥ their M window of **observed** occurrences (K and M derived from preflight, recorded in Decision Log)
- [ ] (b) Polyglot baseline variance measured; N + margin set (Phase 5 closes this)
- [ ] (c) Counterfactual + adversarial judge prompt dry-run on real examples; human-rated
- [ ] Human decision to exit preflight; journaled to `audit.jsonl`
- [ ] Operator backup restore drill (open item #8) completed before this exit

### 8b. Tier-0 auto-merge

- [ ] Flip tier-0 to auto-merge once trusted
- [ ] Efficacy reversion (§21) live before flip
- [ ] **Sampled human-review** of auto-merged tier-0 entries continues (§23 control 2)

### 8c. Tier-1 build (§5, §6)

- [ ] Trigger: ~20+ new occurrences after tier-0, rate unchanged
- [ ] Selection prompt at tier-1: judge argues both (tool-craft `skill/tools/*.md` vs plan-slot `skill/plan-slots/*.md`), then picks; argument is the journal entry
- [ ] Plan-slots loaded once at planner-process boot; planner watches file (atomic-rename already in place)
- [ ] Tool-craft entries flow through the same augmenter as tier-0 knowledge

- **Exit:** ≥ 1 tier-1 artifact merged with cohort improvement; tier-0 auto-merge running for ≥ M tasks without efficacy reversion of more than X% (X from preflight, Decision Log).

---

## Phase 9 — Longitudinal structural track (parallel with Phase 8)

> **Goal:** silent-cluster safety net (§9). Operates on **trends**, not error counts.

- [ ] Sample cyclomatic complexity, file size, fan-out, churn across repos little-coder has worked in
- [ ] Aggregate over time; persist trend snapshots to a volume (likely under `little-coder-cohorts/` or a new `little-coder-trends/`)
- [ ] Trend miner runs on rotation events (§19): consumes the rotated segment before archival
- [ ] Surface anomalies to operator UI (§24); **do not auto-act** in v1
- [ ] Works even when `outcome=unverified` (the whole point of the longitudinal track)

- **Exit:** a trend report covering ≥ preflight + Phase 8 corpus is visible on the operator surface.

---

## Phase 10 — Tier-2 routing rules + exploration

> **Goal:** routing rules only after tier-1 cohort improvement. Add the exploration policy. Refs: §7, §22, §21.

- [ ] **Staged-freeze**: no routing rule authored until cluster's tier-0 and tier-1 windows have run and the cluster has demonstrably resisted them
- [ ] Routing rule loaded once at router-process boot from config file; atomic-rename hot-reload
- [ ] Router consults rule per task; matches gated by rule's internal conditions (e.g. `lang == rust`)
- [ ] **Random-exploration**: 5–10% of tasks where the rule applies, **don't apply it** — collect counter-evidence
- [ ] §21 efficacy reversion auto-retires rules that don't pay off
- [ ] Exploration rate is a config tunable (open item: not blocking; default 5%)

- **Exit:** ≥ 1 merged routing rule with measurable cohort improvement **and** exploration evidence that the rule is correct.

---

## Phase 11 — Tier-3 candidate/active deploy (last, gated)

> **Goal:** rare code-change path with blue/green deploy. Refs: §8, §18, §12.

### 11a. §8 justification gate

- [ ] Judge produces written argument with required structure: cluster + persistence record; interventions tried; **explicit argument** for why no plausible knowledge/tool-craft/plan-slot/routing rule could have closed the gap; proposed structural change + expected effect
- [ ] If §8 (3) cannot be articulated → structural change is **not justified**; write the missing skill instead
- [ ] Argument journaled (auditable later)

### 11b. Candidate topology (§18 steps 1–2)

- [ ] Stage artifact in a separate ephemeral open-terminal workspace
- [ ] Provision **paired** candidate topology: `candidate-little-coder` + `candidate-open-terminal` (do NOT collapse into one container)
- [ ] Candidate mounts active's persistent volumes as **read-only snapshot** (cannot mutate active state)
- [ ] Candidate has writable tmpfs for ephemeral journals/state (discarded on teardown)
- [ ] Same containment as active (capabilities, network policy, `no-new-privileges`); tier-3 is not the time to loosen sandboxing

### 11c. Validation (§18 steps 3–5)

- [ ] **Active drives** the test (stable, known-good harness); verdict is external
- [ ] Two acceptance tests, both required:
  - [ ] Issue fixed: reproduce-then-fix the specific §8 cluster
  - [ ] No regression: Polyglot biased subset score ≥ baseline (Phase 5 gate)
- [ ] Stratified Polyglot subset for upstream-merge validation (broader than per-cluster bias)
- [ ] Verdict from objective oracle: Polyglot + §8 justification + §12 human gate — **never** the active instance's own assessment

### 11d. Repro persistence + PR (§18 step 6)

- [ ] Before candidate teardown: write executable repro to `little-coder-cohorts/repro/<artifact_id>/` (surviving named volume)
- [ ] Open PR from `auto/<date>-<topic>` → `main` on **private** self-improvement remote
- [ ] PR body templated mechanically: §8 justification + §20 cohort evidence + §18 validation outputs + §23 provenance (journal evidence range)
- [ ] **PR body passed through sanitization filter** (§23) before posting; filter failure aborts the PR

### 11e. Deploy + verify (§18 step 7)

- [ ] Operator reviews PR on private remote, merges what they choose
- [ ] Operator runs `docker compose up -d --build little-coder`
- [ ] Active instance never mutated; restart is clean swap between known-good tagged commits
- [ ] Volumes survive the rebuild (verified during Phase 1 — re-verify here)
- [ ] Post-deploy verification: (a) healthcheck green on `agent` + `lc-mcpo`; (b) **smoke task = re-run the persisted §8 repro** against deployed-active
- [ ] Either check fails → `git revert` + same compose command, or pin to prior tag

### 11f. Rollback drill

- [ ] At least one real rollback drill before considering this phase exited
- [ ] Drill journaled; restore-from-volume timing measured

- **Exit:** one tier-3 artifact lands end-to-end on a non-production fork; smoke task passes; rollback drill executed.

---

## Cross-cutting tasks (not phase-gated; tracked here)

These run alongside multiple phases.

- [ ] **Config management** (§24): centralized typed config (YAML + JSON schema), validated at boot, hot-reloadable where safe; restart-required where schema-affecting
  - Tunables to enumerate: per-cluster M; Polyglot N/margin; budget caps; `task_abandoned` timeout per channel; exploration rate; coalesce thresholds; similarity floor; augmenter budget; sanitization drift threshold; sanitization filter ruleset; drain deadline
- [ ] **Metrics endpoint** (§24): Prometheus on `agent` and `meta`: queue depth; judge wall-clock minutes/day; GPU minutes/day; candidate-validation duration histogram; journal write rate; sanitization rejection rate; augmenter selection count per artifact; llama-cpp slot occupancy
- [ ] **Alarms** routed to operator UI alongside artifact approvals
- [ ] **Golden-journal test suite** (§24): synthetic journals with known cohort shapes covering cluster assignment + split/merge lineage + tier escalation + efficacy reversion + sanitization. Run on each `meta` release before deploy
- [ ] **Schema-version discipline** (§24): journal envelope, frontmatter, cohort store, config all carry `schema_version`; readers tolerate older shapes; migrations are explicit operator jobs; **tier-3 self-changes cannot propose schema changes**
- [ ] **Sanitization drift audit loop** (§23): trigger when rejection rate moves outside baseline envelope (threshold = open item #6, preflight-tuned); sample N raw journal records uniformly across `repo`/`lang`; human-review for false negatives
- [ ] **Sanitization quarterly floor** (§23): low-cadence spot-check regardless of drift signal; catches a filter wrong from day one against a category that never appeared

---

## Decision Log

> Append-only. Every status change or scope shift adds a row. Date is decision date, not entry date.

| Date       | Phase | Decision | Refs |
| ---------- | ----- | -------- | ---- |
| 2026-05-19 | n/a   | Plan + tasks docs created from `Self-improving-little-coder-design.md`. No deviations from the locked decisions in §15. | plan §5 |
|            |       |          |      |

---

## Open items (mirror of plan §6)

Tuned by preflight unless otherwise noted. Listed here so they don't get lost.

- [ ] **#1** Polyglot N + regression margin (Phase 5; preflight variance)
- [ ] **#2** Counterfactual judge prompt wording + few-shot (Phase 3 dry-run; resolves at Decision Log entry)
- [ ] **#3** `task_abandoned` timeout per channel (Phase 2 default; tune before Phase 8)
- [ ] **#4** Neutral test-runner for §18 (later hardening; not blocking Phase 11)
- [ ] **#5** Open-terminal network posture (ai-stack compose change; Phase 6 hardens; Phase 1 functional without it)
- [ ] **#6** Sanitization audit drift threshold (Phase 4 `meta`-on; preflight rejection-rate baseline)
- [ ] **#7** Reserved-slot promotion threshold (Phase 8; only if starvation observed)
- [ ] **#8** Backup cadence + restore drill (Phase 1 volumes; drill before Phase 8)
- [ ] **#9** `.git/config` flexibility upgrade (deferred; no current trigger)

---

## How to drive this

1. Pick the active phase at the top.
2. Work tasks top-to-bottom within the phase; mark `[~]` when starting, `[x]` when the **acceptance criterion** in the task is met (not just "code written").
3. On exit-criterion satisfaction → flip phase status; add Decision Log row; bump _Last updated_.
4. New decisions → Decision Log first, then update [integration-plan.md](integration-plan.md) lock table if the decision is permanent.
5. Discovered scope → if it lives inside an existing phase, add a task; if it's a new gate, propose a new phase in the plan first.
6. **Design doc still wins on conflict.** If a phase here implies something [Self-improving-little-coder-design.md](Self-improving-little-coder-design.md) forbids, fix this doc.
