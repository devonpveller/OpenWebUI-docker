# Self-Improving Little-Coder — Task Tracker

> **Last updated:** 2026-05-20
> **Plan:** [`integration-plan.md`](integration-plan.md) — chapters, rationale, locked decisions.
> **Design doc:** [`Self-improving-little-coder-design.md`](Self-improving-little-coder-design.md) — source of truth. Design doc wins on conflict.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → bump `Last updated` and add a Decision Log row if a choice changed.

## Current state

- **Active chapter:** 1 (Tool) — not started.
- **Build sequence:** five chapters, gated by operator judgment (plan §2). One chapter at a time.
- **Tier-0 build reminder:** `session_id` + `channel` + `user_id` on every journal line from line 1. Unrecoverable retroactively (design §4.1).
- **Tool ships the open-terminal network change.** OWUI's direct access to open-terminal ends; it returns in chapter 2 via `lc-mcpo`.

---

## Chapter 1 — Tool

> Refs: design §3, §4, §10, §12. Plan §5.

### 1a. Network + remotes (ship first; these are unrecoverable later)

- [ ] Verify `http://llama-cpp:8080/v1` reachable; both `qwen3.6:27b` and `qwen3.6:27b-nothink` respond
- [ ] **Ship open-terminal network change:**
  - [ ] Move open-terminal off `network_mode: service:openwebui`; give it its own network
  - [ ] Explicit egress allowlist: `llama-cpp` for inference, operator-configured git remote, private search gateway (if enabled), nothing else
  - [ ] Verify isolation: from inside open-terminal, only allowlist endpoints reachable
  - [ ] **Confirm OWUI no longer has direct access to open-terminal** (intended; returns in chapter 2)
- [ ] Decide compose layout (extend `ai-stack` compose vs. new compose file); match `mcpo`/`search-mcpo` pattern
- [ ] Pick the **private** self-improvement git remote (design §10.6); register URL
- [ ] Provision fine-grained PAT scoped to `contents:write` on that remote only

### 1b. Container scaffold

- [ ] Build `little-coder` container image; pin to a known-good upstream commit
- [ ] Run `agent` as MCP server (not raw HTTP/socket); mirror `search-mcpo` pattern
- [ ] Build `lc-mcpo` sidecar (built but dormant in Tool — chapter 2 activates it). API-key'd at the edge (design §10.3)
- [ ] Compose healthchecks: `agent` (MCP socket), `lc-mcpo` (`/openapi.json`)
- [ ] LLM client default → `qwen3.6:27b-nothink`; reasoning variant via call-site selection

### 1c. Journals (tier-0 build requirement — unrecoverable if missed)

- [ ] Implement writers for `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl`
- [ ] Envelope per design §4.1: `ts`, `task_id` (ULID), `session_id`, `channel`, `user_id`, `repo`, `lang`, `seq`, `schema_version: 1`
- [ ] Write-time schema validation; malformed records **rejected, not appended**
- [ ] `task_started` / `task_ended` bracket every task; reconstruct by `task_id`, never adjacency
- [ ] Outcome label per design §4.2: `pass` / `fail` / `unverified`
- [ ] Durability per design §4.3: append + fsync on every terminal and every error record
- [ ] Schema versioning plumbed through readers; tolerate older shapes (forward-compat for later chapters)

### 1d. Audit log

- [ ] `audit.jsonl` writer per design §4.4, separate from task journals
- [ ] Records emitted in Tool: `project_switched`, `task_outcome_amended`, `shutdown`
- [ ] Longer retention than task journals; different access controls

### 1e. Persistence (also unrecoverable if missed)

- [ ] Declare all four named volumes at compose time:
  - [ ] `little-coder-skill` (used Learner+; declared now)
  - [ ] `little-coder-journals` (used Tool)
  - [ ] `little-coder-cohorts` (used Observer+; declared now)
  - [ ] `little-coder-polyglot` (used Learner+; declared now)
- [ ] Mount into `agent`; confirm `docker compose up -d --build little-coder` preserves all four
- [ ] Backup job (Alpine-cron daily default; cadence + restore drill tracked as open item #7)

### 1f. Workspace handling + project focus

- [ ] `agent` can clone a single repo directly into open-terminal
- [ ] `agent` can edit files; run tests/commands
- [ ] `/project repo: <link>` CLI subcommand per design §12.3
- [ ] URL normalization: host + owner + repo, lowercased
- [ ] No current focus → clone, journal `project_switched`
- [ ] Matches current focus → no-op
- [ ] Different focus + task in flight → reject, suggest cancel-or-wait
- [ ] Different focus + clear → tag prior state, wipe workspace, clone new repo, journal `project_switched`
- [ ] One task at a time; FIFO queue across triggers (design §12.4)
- [ ] Human attach is read-only

### 1g. CLI operator surface

- [ ] `lc admin project switch <link>` (alias of `/project repo:`)
- [ ] `lc admin shutdown [--drain-deadline 30m]`
- [ ] `lc admin task confirm <task_id> [pass|fail]` — outcome amendment (design §4.2); 7-day window; last-amendment-wins
- [ ] `lc admin pending` (stub: returns empty in Tool; wired in chapter 4)
- [ ] `lc admin approve <id>` / `lc admin reject <id>` (stubs in Tool)

### 1h. git-proxy

- [ ] Build `git-proxy` wrapper; site at open-terminal workspace edge (the git binary inside the workspace IS the proxy)
- [ ] No raw-git fallback reachable by the agent
- [ ] Whitelist per design §3.3: `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (operator-pre-configured remotes only)
- [ ] Blocklist per design §3.3: `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, all `submodule` subcommands, all history rewrites, anything touching `.git/` directly
- [ ] Mount `.git/config`, `.git/hooks/`, `.git/info/` **read-only to the agent**
- [ ] Allowed remotes baked in by operator at project-switch time
- [ ] `core.hooksPath` set to operator-controlled directory **outside** `.git/`
- [ ] Branch / tag discipline (design §12.1): outer-loop changes on `auto/<date>-<topic>` branches; never direct to `main`
- [ ] Per-repo deploy tokens per design §10.3: least-privilege, injected per task, never ambient, never the self-PAT
- [ ] Adversarial tests:
  - [ ] Agent attempts `remote add` via direct `.git/config` write → blocked (read-only mount)
  - [ ] Hostile `.gitmodules` → no submodule clone on any whitelisted op
  - [ ] Agent attempts `git push --force` → blocked; journaled

### 1i. Sanitization filter (shadow mode)

- [ ] Build filter per design §10.2: redact secrets/key-shaped strings; reduce large file bodies to structural digests; strip PII
- [ ] **Pinned and tested** against fixed test set (seeded false-positives + false-negatives)
- [ ] In Tool, **run in shadow mode**: filter records what it _would_ redact but does not block (nothing leaves the stack in Tool)
- [ ] Rejection rate metric collected on the Prometheus endpoint (feeds chapter 3 drift baseline)

### 1j. Metrics endpoint

- [ ] Prometheus endpoint on `agent` (design §9.3)
- [ ] Tool-relevant metrics: queue depth; journal write rate; llama-cpp slot occupancy; sanitization rejection rate (shadow-mode counts)
- [ ] Later chapters layer additional metrics on this endpoint

### 1k. Centralized config

- [ ] Typed config (YAML + JSON schema) per design §12.8, validated at boot
- [ ] Tool-era tunables: drain deadline default; `task_abandoned` timeout per channel; basic budget caps
- [ ] Schema version on the config file; forward-compat for later chapters

### 1l. Shutdown semantics

- [ ] SIGTERM drain mode per design §12.7: refuse new triggers, allow in-flight to a configurable deadline, then SIGKILL
- [ ] Open `task_id`s past the deadline → journaled `task_abandoned` with reason `shutdown`
- [ ] Operator override `lc admin shutdown --drain-deadline 30m`

### Tool stop point (chapter 1 → 2)

Tool is "done" when:

- [ ] Daily flow feels stable; no new bugs in the basic pipeline
- [ ] Journals are accumulating with the full envelope; no fields missing
- [ ] `audit.jsonl` records every project switch + shutdown
- [ ] Volumes survive at least one intentional `docker compose up -d --build` rebuild
- [ ] Sanitization rejection rate (shadow mode) has a stable baseline
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

---

## How to drive this

1. Active chapter is at the top.
2. Work tasks top-to-bottom within the chapter; mark `[~]` when starting, `[x]` when the **acceptance criterion** in the task is met (not just "code written").
3. On chapter stop-point satisfaction → flip chapter status; add Decision Log row; bump `Last updated`.
4. **The build agent stops at the chapter boundary.** Resuming work means deliberately starting the next chapter — not a hand-off the agent makes by itself.
5. New decisions → Decision Log first, then update [`integration-plan.md`](integration-plan.md) lock table if permanent.
6. Discovered scope → if it lives inside an existing chapter, add a task; if it's a new gate, propose a new chapter in the plan first.
7. **Design doc wins on conflict.** If a chapter here implies something the design doc forbids, fix this doc.
