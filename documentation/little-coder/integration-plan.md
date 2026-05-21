# Self-Improving Little-Coder — Integration Plan

> **Source of truth for design:** [Self-improving-little-coder-design.md](Self-improving-little-coder-design.md). This plan is the build sequencing; the design doc is the _why_ and the _what_. If the two disagree, the design doc wins and this plan gets updated.
>
> **Paired task tracker:** [integration-tasks.md](integration-tasks.md) — living checklist with status and decision log. Update both together when scope shifts.
>
> **Posture:** evidence-based, additive-by-default. Knowledge artifacts come first, code changes come last. Every phase has an exit criterion; phases are not parallelizable unless explicitly noted.

---

## 1. Goal (one paragraph)

Stand up little-coder as a containerized service inside `ai-stack` that drives [open-terminal] as its workspace, accumulates expertise from its own work, and — through an outer "meta" loop — periodically writes new knowledge / tool-craft / plan-template slots / routing rules, and in rare justified cases proposes code changes to itself. The system's output is **knowledge first**, code change last. Every artifact passes a Polyglot regression gate plus a human approval gate until trust is earned.

Non-goals for v1:

- Hot-patching the running process (`§18` is blue/green deploy, not in-process patching).
- Auto-pulling fork-parent upstream (`/upstream pull` is an explicit operator action).
- Multi-project concurrency (one focused project at a time, see `§17`).

---

## 2. Architectural summary (cheat sheet)

Two planes, kept distinct:

| Plane              | Container(s)                                     | Role                                                                                                                  |
| ------------------ | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| Control            | `little-coder` (with `agent` + `meta` processes) | Inner loop, outer loop, journals, judge, cohort math, augmenter. State on named volumes.                              |
| Workspace + exec   | `open-terminal` (existing container)             | One focused project cloned directly into the workspace. One task at a time, FIFO across triggers.                     |
| Inference          | `llama-cpp` / llama-swap (existing)              | `qwen3.6:27b` (reasoning) + `qwen3.6:27b-nothink` (fast). Inner loop is a client today; judge becomes one in Phase 4. |
| MCP edge           | `lc-mcpo` (new sidecar, mirrors `search-mcpo`)   | Exposes little-coder's MCP server as OpenAPI for OWUI and CLI clients; API-key'd at the edge.                         |
| Safety choke point | `git-proxy` (in-container wrapper)               | Sole git path inside the workspace. Whitelist/blocklist per `§4`; `.git/config`+hooks read-only to agent.             |

Persistent state lives on four named volumes (`§16 step 1`) mounted into little-coder containers; the container is ephemeral, the volumes are the persistence boundary.

- `little-coder-skill/` — artifact library (`§22`)
- `little-coder-journals/` — three journals + audit log (`§19`)
- `little-coder-cohorts/` — derived counters + repro corpora (`§20`, `§18 step 6`)
- `little-coder-polyglot/` — canonical Polyglot benchmark clone (`§13`)

---

## 3. Tier ladder (read this before any artifact work)

| Tier | Artifact                                         | Trigger                                            | Risk class      | Auto-merge?       | Restart on deploy?                |
| ---- | ------------------------------------------------ | -------------------------------------------------- | --------------- | ----------------- | --------------------------------- |
| 0    | Knowledge entry (`skill/knowledge/*.md`)         | N ≥ ~5 occurrences, no prior intervention          | Pure addition   | Yes, once trusted | No                                |
| 1    | Tool-craft (`skill/tools/*.md`) **or** plan-slot | ~20+ after tier-0, rate unchanged                  | Prompt-shape    | Yes, once trusted | No (hot-reload via atomic-rename) |
| 2    | Routing rule (config file)                       | Same persistence after tier-1                      | Decision-shape  | Yes, once trusted | No (hot-reload via atomic-rename) |
| 3    | Code change to `agent.py` / `local/`             | Same persistence after tier-2 + `§8` justification | Behaviour shift | **No, ever**      | Yes — `§18` blue/green flow       |

Escalation gates are **quarantine windows per cluster**, cohort-accounted (before/after), not raw counters. See `§6` for the math and `§20` for cluster identity.

---

## 4. Phase plan (sequenced, gated)

Phases mirror the design doc's `§16` "Suggested build order" with explicit entry/exit criteria. Phase N may not begin until Phase N-1's exit is met. **The phases are not parallelizable** except where noted.

### Phase 0 — Prerequisites & open-terminal hardening

**Goal:** lock the prerequisites that, if missed, are unrecoverable later. **Land the open-terminal network change before any autonomous workload runs.**

- Confirm inference reachability: `http://llama-cpp:8080/v1` for both `qwen3.6:27b` and `qwen3.6:27b-nothink`.
- **Ship the open-terminal network change.** Move open-terminal off `network_mode: service:openwebui` to its own network with explicit egress: `llama-cpp` for inference, the operator-configured git remote for the focused repo, the private search gateway if web-search is enabled, nothing else. This is the threat that appears first (Phase 1), so it must land first. Without it, AI-generated or untrusted code running in open-terminal inherits Open WebUI's full reachability — that is the blast radius. (`§17`, `§15`.)
- Decide compose layout: extend existing `ai-stack` compose, or its own compose file imported in. Pattern follows the existing `mcpo` / `search-mcpo` setup.
- Confirm volume conventions and backup pattern (Alpine-cron, matching `mnemory-backup`).
- Decide self-improvement git remote (must be **private** per `§12`); provision fine-grained PAT scoped to `contents:write` on that repo only.

**Exit:** open-terminal compose change shipped, network isolation verified (open-terminal can reach only the explicit egress list); inference + remote + PAT verified reachable from a throwaway container on the right networks.

### Phase 1 — Container scaffold + journals + persistence (tier-0 build requirement)

**Goal:** stand up `agent` as an MCP server behind `lc-mcpo`, with the full `§19` journal schema **including `session_id`, `channel`, `user_id`** from day one. These fields are unrecoverable retroactively (`§16 step 1`).

- Build `little-coder` container image: `agent` process running little-coder's REPL as MCP server.
- Build `lc-mcpo` sidecar: MCP→OpenAPI bridge, API-key'd, healthcheck on `/openapi.json` (mirrors `search-mcpo`).
- Implement journal writers for `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl` with the full envelope (`§19`): `ts`, `task_id` (ULID), `session_id`, `channel`, `user_id`, `repo`, `lang`, `seq`, `schema_version`. Write-time schema validation; append+fsync on terminal+error records.
- Declare the four named volumes (`§2` above) at compose time and mount them into `agent`. Tier-3 rebuilds (`§18 step 7`) must preserve volumes.
- **Minimal workspace handling**: `agent` can clone a single repo directly into open-terminal, edit files, run tests, and journal what happened. No project switching yet (Phase 2), no operator surface yet (Phase 2). Just enough to make a Flow 2 trigger complete end-to-end.
- Wire `agent`'s LLM client to `llama-cpp:8080/v1`; default model `qwen3.6:27b-nothink` for inner-loop work; reasoning variant reserved for judge and drafting (Phases 3+).
- Compose healthchecks on both `agent` (MCP socket) and `lc-mcpo` (`/openapi.json`).
- SIGTERM drain behavior: refuse new task triggers, allow in-flight to complete to a configurable deadline (`§24`).

**Exit:** a Flow 2 (CLI) trigger completes end to end against a throwaway repo cloned into open-terminal, every line of `tool_calls.jsonl`/`errors.jsonl`/`outcomes.jsonl` validates against the envelope schema, the trio of journals survives a container `up -d --build` without data loss, healthchecks green.

### Phase 2 — Project focus + CLI operator surface + preflight start

**Goal:** project switching is an explicit operator action; CLI operator surface is live; preflight workload starts collecting attributed journals.

- Implement `/project repo: <link>` per `§17`:
  - Normalize URL to canonical form (host+owner+repo, lowercased).
  - No current focus → clone, set focus, journal `project_switched`.
  - Matches current focus → no-op.
  - Different focus + task in flight → reject, suggest cancel-or-wait.
  - Different focus + clear → tag prior state, wipe workspace, clone new repo, journal `project_switched`.
- FIFO queue across triggers: a Flow 2 trigger arriving while a Flow 1 task is running waits its turn. No "busy, try later."
- CLI operator subcommands (`§24`):
  - `lc admin project switch <link>`
  - `lc admin pending` (list pending artifacts; empty until Phase 4)
  - `lc admin approve <id>` / `reject <id>`
  - `lc admin upstream pull` (no-op until tier-3, but the command exists)
  - `lc admin shutdown [--drain-deadline 30m]`
  - `lc admin task confirm <task_id> [pass|fail]` (outcome amendment, `§19`)
- `audit.jsonl` writer (`§24`): every operator action journaled here, separate retention from task journals.
- Begin preflight (`§14`): journals on, meta-loop **off**. Real OWUI/CLI users driving real workloads. Run for ≥ 1–2 weeks.

**Exit:** preflight workload is collecting attributed journals; project-switch wipes workspace cleanly without touching named volumes; `audit.jsonl` records every operator action with timestamps.

### Phase 3 — Cluster taxonomy, judge prompt, sanitization filter

**Goal:** stand up the judge path with privacy controls **before** the judge is ever invoked on real data.

- Draft initial cluster taxonomy (`§20`): immutable synthetic `cluster_id` + mutable human label. Similarity-floor logic for ingest-time assignment. `unassigned` pool for occurrences without a home.
- Implement split/merge lineage records (`§20`): parent↔child `cluster_id`s; inherited vs observed counts; quarantine reset on merge.
- Draft and dry-run the **counterfactual + adversarial judge prompt** (`§23`) against preflight journals. Human-rate outputs on real examples.
- Implement the **sanitization filter** (`§23`): redact secrets/key-shaped strings, reduce large file bodies to structural digests, strip PII. Pin and test the filter against a fixed test set. Apply on the judge path, PR-body assembly (`§18 step 6`), and any future operator-export path. Filter failure aborts the call — never "send anyway."
- Wire the cohort store as an event-sourced projection over journals (`§20`): rebuildable from journals on demand; `schema_version`'d.
- **Per-language cohort scoping** (`§24`): clusters keyed by `lang` + `task_shape`, aggregated across repos (`repo` is recorded for drill-down only).

**Exit:** judge prompt dry-runs on real journals pass human rating; sanitization filter catches the seeded test cases (false-positive + false-negative); cohort store rebuilds correctly from a journal replay.

### Phase 4 — Meta tier-0 with manual review

**Goal:** the outer loop produces knowledge artifacts only (tier-0), and every one routes to the operator surface for manual approval.

- Build `meta` process: clustering ingest → judge counterfactual call → tier-0 artifact draft.
- Frontmatter schema enforcement at draft time (`§22`): `id`, `cluster_id`, `tier`, `lang`, `domain`, `tool`, `task_shape`, `created`, `supersedes` (null), `status` (`active`).
- Augmenter selection logic (`§22`): hard filter on structured tags → embedding rank → hard token budget. Cohort-proven + tighter match wins ties; tier is not a tiebreaker. Per-task selection logged.
- Hot-reload pattern for plan-slots/routing rules using **atomic-rename** (`§22`): write to `.tmp`, `rename(2)` into place; readers ignore `*.tmp`.
- Operator surface lists pending artifacts with text + provenance (`§23`) + cohort evidence (`§20`). Approve / reject is the merge gate.
- Single-flight lock on `meta` iterations (`§24`).
- Budget caps (`§24`): artifacts/iteration = 1, judge wall-clock minutes/day, Polyglot exercise-runs/day, journal write rate. Exceeding defers, never drops evidence. Coalesce-don't-drop for deferred iterations.
- Resource isolation policy (`§24`): `meta` checks llama-cpp slot occupancy before issuing inference; interactive always wins.

**Exit:** at least one real tier-0 knowledge artifact drafted, surfaced, human-approved, and merged. Cluster identity survives a relabel without resetting cohort history.

### Phase 5 — Polyglot oracle + real validation semantics

**Goal:** turn `§13/§21`'s "must not regress against baseline" into a real, parameterized gate.

- Stand up the Polyglot benchmark on the canonical clone (`little-coder-polyglot/` volume).
- Wrap behind an `Oracle` interface (`§13` DIP note): `run_subset(cluster_id, biased_subset) → ScoredResult`. Cheap now; rewrite-class expensive if deferred.
- Implement biased subset selection (`§13`): exercises in the cluster's domain over a uniform random subset.
- **Measure baseline variance during preflight** (`§14`); set the regression margin and minimum subset N from measured variance, not by guess (`§21`, `§15` open item).
- Implement the **in-context assertion** (`§21`): if the augmenter did not select the artifact into context during validation, the gate measured nothing → result is **void**, not pass.
- Implement **efficacy reversion** (`§21`): if post-intervention cohort rate is statistically indistinguishable from pre after the window, auto-flag `ineffective` and revert on the next iteration. Retirement journaled.
- Implement supersession (`§22`): new artifact on existing `cluster_id` sets `supersedes` and flips prior to `superseded`. Augmenter selects only `active`.

**Exit:** a tier-0 artifact that does not help its cluster is auto-retired; one that does pulls the rate as expected. Polyglot subset gate blocks a deliberately-noisy artifact PR.

### Phase 6 — git-proxy at the workspace edge

**Goal:** the agent has no un-proxied raw-git path. The whitelist's guarantees only hold if it is the _only_ git path.

- Build `git-proxy` wrapper. Whitelist (`§4`): `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (restricted to operator-pre-configured remotes; no all-refs, no new remotes mid-task). Blocklist: `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, **all `submodule` subcommands**, all history rewrites, anything touching `.git/` directly.
- Site the proxy at the **open-terminal workspace edge** (`§17`): git inside the workspace is the proxied binary; agent has no raw-git fallback.
- Mount `.git/config`, `.git/hooks/`, `.git/info/` **read-only to the agent**. Allowed remotes baked in by operator at project-switch time. Dynamic paths (`.git/objects/`, `.git/refs/`, `.git/index`) stay writable.
- Set `core.hooksPath` to an operator-controlled directory outside `.git/` (belt-and-suspenders for `.gitattributes`/hook execution vectors).
- Branch/tag conventions (`§12`): outer-loop changes land on `auto/<date>-<topic>` branches. Never direct to `main`. Tag `pre-iteration-<n>` before each outer-loop attempt.
- Implement **least-privilege per-repo deploy tokens** for work repos (`§23`): injected per task, never ambient, never the self-PAT. Self-PAT lives with `meta`/git-proxy only, unreachable from workspace plane.

**Exit:** every git operation the agent attempts goes through the proxy; a synthetic test that tries `remote add` via direct `.git/config` write is blocked because `.git/config` is read-only; a hostile `.gitmodules` causes no submodule clone.

### Phase 7 — OWUI pipeline (operator parity)

**Goal:** real OWUI implementation of the operator surface — not just a chat passthrough.

- Task triggers map to chat (long, streaming, conversational) via the registered MCP tool.
- Slash-commands: `/project repo:`, `/upstream pull`, `/approve <id>`, `/reject <id>`, `/pending`, `/confirm <task_id> pass|fail`.
- Artifact-review messages with structured rendering: artifact text + `§20` cohort evidence + `§23` provenance + Approve/Reject controls.
- Privilege separation (`§24`): operator commands authenticated at the OWUI surface (using OWUI's configured auth); the MCP server itself only authenticates task triggers (API key).
- Render queue-depth alarms, sanitization-drift alarms, candidate-validation-timeout alarms (`§24`) inline with artifact approvals.

**Exit:** an artifact can be approved end-to-end from OWUI with the same effect as the CLI path. UI smoke test by the operator.

### Phase 8 — Exit preflight → tier-0 auto-merge + tier-1 build

**Goal:** the `§24` exit-preflight checklist is met. Trust tier-0 to auto-merge. Begin tier-1.

- Confirm `§24` preflight exit criteria, all three required:
  - (a) ≥ K distinct clusters each have ≥ their M window of _observed_ occurrences;
  - (b) Polyglot baseline variance measured; N/margin set;
  - (c) judge prompt dry-run on real examples + human-rated.
  - The transition is a **human decision**, journaled to `audit.jsonl` — never an automatic threshold.
- Flip tier-0 to auto-merge once trusted, with efficacy reversion live.
- Build tier-1: judge picks tool-craft _vs_ plan-slot within the tier; choice journaled. Selection prompt per `§5`.
- Plan-slots loaded once at planner-process boot from `skill/plan-slots/*.md`; planner watches the file. Atomic-rename writers from Phase 4 already cover this.
- Begin **sampled human-review** of auto-merged tier-0 entries (`§23` control 2) until poisoning-via-text is conclusively quiet.

**Exit:** at least one tier-1 artifact merged with cohort improvement; tier-0 auto-merge running for the M window without unusual efficacy-reversion patterns (qualitative — investigate if the pattern is worth investigating, per the doc's evidence-based posture).

### Phase 9 — Longitudinal structural track (in parallel with Phase 8)

**Goal:** the silent-cluster safety net from `§9` is live.

- Sample cyclomatic complexity, file size, fan-out, churn across repos little-coder has worked in. Aggregate over time.
- Stand up a separate pattern miner that operates on **trends**, not error counts.
- Surface anomalies to the operator UI (`§24`); do not auto-act on them yet.

**Exit:** the longitudinal miner reports a trend over the preflight + Phase 8 corpus, including for tasks where `outcome=unverified` (no oracle).

### Phase 10 — Tier-2 routing rules with staged-freeze + exploration

**Goal:** routing rules only after tier-1 shows demonstrable cohort improvement. Add the **exploration policy** so rules can't be self-confirming.

- Staged-freeze: no routing rule authored for a cluster until its tier-0 and tier-1 interventions have run their quarantine windows and the cluster has demonstrably resisted them (`§7`).
- 5–10% **random-exploration** runs against each routing rule indefinitely after authoring. Where the rule says "don't invoke planner", explore by invoking it; evidence keeps flowing post-rule (`§7`).
- Routing-rule hot-reload via atomic-rename; consulted per task by the router.
- `§21` efficacy retires rules that don't pay off.

**Exit:** one merged routing rule with measurable cohort improvement _and_ exploration evidence that the rule is correct.

### Phase 11 — Tier-3 candidate/active deploy (last, gated)

**Goal:** the rare-and-justified code-change path, with blue/green deploy. Not a near-term deliverable (`§16 step 10`).

- Implement the `§8` justification gate: judge must produce written argument that no plausible knowledge / tool-craft / plan-slot / routing rule could have closed the gap. If `§8 (3)` cannot be articulated, the structural change is not justified — write the missing skill instead.
- Implement `§18` blue/green flow:
  1. Stage artifact in a separate ephemeral open-terminal workspace.
  2. Provision **paired candidate topology**: `candidate-little-coder` + `candidate-open-terminal`. Active's volumes mounted **read-only** to candidate; writable tmpfs for candidate's ephemeral state; same containment as active.
  3. Active drives the test (pragmatic first cut); the verdict is external (Polyglot + `§8` + human gate).
  4. Two acceptance tests, both required: issue-fixed (repro of the `§8` cluster) and no-regression (Polyglot biased subset ≥ baseline).
  5. Stratified Polyglot subset for upstream-merge validation (broader than per-cluster bias).
  6. On pass: persist the executable repro to `little-coder-cohorts/repro/<artifact_id>/` (surviving volume), open PR on private remote, PR body mechanically templated and passed through `§23` sanitization. Filter failure aborts the PR.
  7. Human merge + manual `docker compose up -d --build little-coder`. Volumes survive the rebuild.
  8. Operator post-deploy verification: healthcheck green + smoke task = re-run the persisted repro.
- Rollback: `git revert` + the same compose command, or pinning to the prior tag.

**Exit:** one tier-3 artifact lands end-to-end on a non-production fork. Smoke task passes against the rebuilt active. Rollback drill executed at least once.

---

## 5. Locked decisions (lifted from design doc)

These are settled in `§15` and not up for re-debate unless the design doc changes. Reproduced here so this plan stands on its own.

| #   | Decision                                                                                                                                                                     | Source                     |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| 1   | Models: `qwen3.6:27b` (reasoning) + `qwen3.6:27b-nothink` (fast) on `http://llama-cpp:8080/v1`. No other backends.                                                           | `§4`, `§15`                |
| 2   | Session model: one focused project cloned directly into open-terminal; one task at a time; FIFO across triggers; tier-3 escalates to ephemeral container.                    | `§17`, `§15`               |
| 3   | Upstream pulls are operator-initiated via `/upstream pull`. Tiers 0–2 land cleanly; tier-3 conflicts → `upstream-merge/<date>` branch with stratified Polyglot revalidation. | `§17`, `§15`               |
| 4   | Deploy actor: operator, via PR + manual `docker compose up -d --build`. Tier-3 only.                                                                                         | `§18 step 7`, `§16`, `§15` |
| 5   | Cohort schema: per `§19` (envelope) + `§20` (identity, split/merge lineage).                                                                                                 | `§15`                      |
| 6   | Artifact-type selection: tier ladder governs risk class; judge picks type within tier.                                                                                       | `§5`, `§15`                |
| 7   | Routing-rule exploration: staged-freeze gates tier-2 entry; 5–10% random-exploration indefinitely.                                                                           | `§7`, `§15`                |
| 8   | Skill frontmatter schema: `§22`.                                                                                                                                             | `§15`                      |
| 9   | Judge model: in-stack `qwen3.6:27b` under adversarial framing. External judge deferred, not rejected.                                                                        | `§23`, `§15`, Appendix A   |
| 10  | Cohort scoping: per-language + per-task-shape, aggregated across repos.                                                                                                      | `§24`                      |
| 11  | Open-terminal network change is a **Phase 0 prerequisite**, not a Phase 6 hardening.                                                                                         | `§17`, `§15`               |

---

## 6. Open decisions (tracked, not blocking until specified)

Lifted from `§15`. Most are **preflight-tuned** — they need real data before a number can be set. Track in [integration-tasks.md](integration-tasks.md) decision log.

| #   | Open item                                      | Resolves where                     | Blocks phase                                     |
| --- | ---------------------------------------------- | ---------------------------------- | ------------------------------------------------ |
| 1   | Polyglot N + regression margin                 | Preflight variance measurement     | Phase 5                                          |
| 2   | Counterfactual judge prompt wording + few-shot | Phase 3 dry-run                    | Phase 4 (judge-on)                               |
| 3   | `task_abandoned` timeout per channel           | Preflight                          | Phase 2 (default usable; tune before Phase 8)    |
| 4   | Neutral test-runner for `§18`                  | Later hardening                    | Phase 11 (not blocking)                          |
| 5   | Sanitization audit drift threshold             | Preflight rejection-rate baseline  | Phase 4 (`meta`-on)                              |
| 6   | Reserved-slot promotion threshold (`meta` GPU) | Preflight + steady-state load      | Phase 8 (only if starvation observed)            |
| 7   | Backup cadence + restore drill                 | Decided alongside volumes          | Phase 1 (volumes); drill before Phase 8 (tier-1) |
| 8   | `.git/config` flexibility upgrade              | Deferred; only if real need arises | None today                                       |

---

## 7. Cross-cutting concerns (apply everywhere)

- **Append-only journals.** Schema fields are unrecoverable retroactively. Ship `session_id`/`channel`/`user_id` on day one.
- **Named volumes are the persistence boundary.** Tier-3 rebuilds recreate the container, never the volumes. Treat any other state as ephemeral.
- **Sanitization runs on every outbound.** Judge calls, PR bodies, future operator export. One filter, multiple call sites. Filter failure aborts the call — never "send anyway."
- **Nothing fails open.** Judge unreachable → defer + alarm. Polyglot won't run → "insufficient evidence", defer. Candidate won't boot → cluster stays at current tier (no escalation credit).
- **Evidence-triggered, not time-based.** Cohort math, efficacy reversion, sanitization audits, escalation. The system runs on real evidence; there are no fixed-cadence audits.
- **Operator trusted; agent untrusted; user repos under Flow 1/2 actively hostile by default** (`§23`).
- **Schema versioning everywhere** (journal envelope, frontmatter, cohort store, config). Readers tolerate older shapes; migrations are explicit operator-run jobs. **Tier-3 self-changes cannot propose schema changes** — operator-only, full stop (`§24`).

---

## 8. Reference table (doc section → phase)

Use this when implementing a phase task and the design doc is the source of truth.

| Design doc section                | Phase(s)        | What it pins                                                       |
| --------------------------------- | --------------- | ------------------------------------------------------------------ |
| `§4` Container architecture       | 1, 6            | Processes, journals, git-proxy whitelist/blocklist                 |
| `§5` Artifact taxonomy            | 4, 8, 10, 11    | Four types; within-tier judge selection                            |
| `§6` Evidence-based escalation    | 3, 4, 8, 10, 11 | Tier ladder + cohort math + quarantine windows                     |
| `§7` Routing-rule exploration     | 10              | Staged-freeze + 5–10% random exploration                           |
| `§8` Code-change justification    | 11              | Written argument requirement                                       |
| `§9` Two telemetry tracks         | 9               | Longitudinal structural miner                                      |
| `§10` Clustering                  | 3               | Judge-proposed, human-readable labels                              |
| `§11` Skill organization          | 4               | Tag/frontmatter requirement                                        |
| `§12` Safety rails                | 6, 8, 11        | Branches, tags, PAT scope, private remote, sanitization-everywhere |
| `§13` Polyglot oracle             | 5               | Interface + biased subset + DIP note                               |
| `§14` Preflight                   | 2, 8            | Journals on, meta off; exit checklist                              |
| `§17` Service surface             | 0, 1, 2, 6      | open-terminal network (Phase 0); workspace + project-focus model   |
| `§18` Candidate/active            | 11              | Blue/green tier-3 deploy                                           |
| `§19` Journal schema              | 1               | Envelope + lifecycle + amendment + durability                      |
| `§20` Cluster identity            | 3, 4            | `cluster_id` vs label; split/merge lineage; counters               |
| `§21` Validation semantics        | 5, 8            | Baseline, regression margin, in-context, efficacy reversion        |
| `§22` Skill library               | 4, 8            | Frontmatter; three consumption paths; augmenter; atomic writes     |
| `§23` Privacy + judge + poisoning | 3, 6, 7         | Sanitization; deploy tokens; provenance; MCP key                   |
| `§24` Meta-loop operations        | 2, 4, 7         | Single-flight; budgets; coalesce; operator surface; metrics        |

---

## 9. How to use this plan

- **Plan is sequencing**, [Tasks](integration-tasks.md) **is execution**. Every phase here has a corresponding tasks block over there with checkboxes, exit criteria, and a decision log.
- **Update both together.** If a phase exit criterion changes, change it here _and_ in the tasks doc; if a decision flips, record it in the tasks decision log _and_ update this plan's lock table.
- **Design doc wins.** If a phase here implies something the design doc forbids, the design doc is right and this plan needs a fix.
- **One phase at a time** except where this plan explicitly notes parallel work (Phase 9 alongside Phase 8).
