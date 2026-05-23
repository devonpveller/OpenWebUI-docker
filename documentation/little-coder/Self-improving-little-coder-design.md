# Self-Improving Little-Coder: Design

A containerized deployment of [little-coder](https://github.com/itayinbarr/little-coder) that accumulates expertise from its own work and, on evidence, writes new skill artifacts or proposes changes to itself. Knowledge first, code change last.

**Companion documents:** [`integration-plan.md`](integration-plan.md) (build sequencing) · [`integration-tasks.md`](integration-tasks.md) (execution checklist). Design doc wins on conflict.

---

## 0. How to read this document

This design describes the **complete target architecture** across all five chapters of the build. **Not all of it is implemented at any given time.** Sections carry chapter tags to indicate when they activate:

- **[Tool]** — chapter 1. A working little-coder driven from CLI, journals quietly recording, safety controls in place.
- **[OWUI]** — chapter 2. Adds the chat/slash-command surface via `lc-mcpo`.
- **[Observer]** — chapter 3. `meta` reads journals and surfaces clustered patterns. Nothing written.
- **[Learner]** — chapter 4. `meta` drafts tier-0/1 artifacts; you approve each one. Polyglot validation. No code changes.
- **[Self-modifier]** — chapter 5. Auto-merge for trusted tiers; tier-2 routing rules; tier-3 code changes via §11.

**Current chapter:** _OWUI_ (chapters 1–2 built and deployed; chapter 2 in operator acceptance). Sections tagged `[Observer]` and later describe target architecture, not present implementation. The build agent stops at the end of each chapter; the operator decides when to advance.

**This doc is synced at chapter boundaries, not on every decision. Synced as of 2026-05-22.** The live working surface is the plan + tasks Decision Log; this source-of-truth absorbs decided facts when a chapter closes. Between syncs, if this doc and the plan disagree on something the plan's Decision Log records as decided, the decision stands — and the gap is a sync debt to close, not a live ambiguity. Decisions absorbed at the 2026-05-22 sync: the Node/Python split (§3.1), agent statelessness (§3.1, §4), shared-workspace routing (§3.4), the fifth volume (§3.6), the two knowledge layers (§3.7), the compliance-vs-knowledge-gap tier rule (§5.6), the SOLID instruction↔measurement pairing (§9.1), and journal-backed episodic memory as a rejected alternative (§15).

**Why everything is documented now even though only chapters 1–2 are built:** so that early-chapter decisions don't conflict with later-chapter architecture (especially: journal schema, named volumes, git-proxy, network posture, schema versioning — all "unrecoverable if missed" — must be right early because retrofitting them costs more than building them now).

---

## 1. Guiding principles

These rules apply throughout. Stated once; not re-justified per section.

1. **Knowledge first, code last.** The system's output is skill artifacts. Code changes to little-coder itself are rare, justified, and gated.
2. **Evidence-triggered, not time-based.** Cohort math, escalation, efficacy reversion, sanitization audits — nothing runs on a clock.
3. **Fail closed, never open.** Judge unreachable → defer. Polyglot won't run → "insufficient evidence." Sanitization filter errors → abort. No silent passes.
4. **Sanitize every outbound.** One filter, all egress (judge calls, PR bodies, future exports). Filter failure aborts the call.
5. **Two planes, kept separate.** Control plane (little-coder) decides; workspace plane (open-terminal) executes. Boundary is the safety surface.
6. **Trust boundary.** Operator trusted. Agent untrusted. User repos under task triggers actively hostile by default.
7. **Append-only journals.** Schema fields are unrecoverable retroactively. Ship the full envelope from line 1. Journals are write-only from the agent's side — they feed `meta`, never the agent's own context (§3.1).
8. **Named volumes are the persistence boundary.** Containers are ephemeral. State lives on volumes.
9. **Design doc wins on conflict.** Plan and tasks docs serve it; not the other way around. (Synced at chapter boundaries — see §0.)

---

## 2. Topology

```
                       ┌──────────────┐
                       │  llama-cpp   │   qwen3.6:27b (reasoning)
                       │              │   qwen3.6:27b-nothink (fast)
                       │ :8080/v1     │   on llm-net
                       └──────┬───────┘
                              │ inference (both planes share the backend)
                ┌─────────────┴─────────────┐
                │                           │
         ┌──────▼──────┐             ┌──────▼──────┐
         │    OWUI     │◄───pipe────►│ little-coder│        Control plane
         │             │             │   (agent +  │
         │             │   trigger   │    meta)    │
         │             │  task/admin │             │
         └──────┬──────┘             └──────┬──────┘
                                            │ drives (shared workspace volume
                CLI ─────── trigger ───────►│  + POST /execute routing)
                                            │ (sole git path is git-proxy)
                                     ┌──────▼───────────┐
                                     │  open-terminal   │   Workspace + exec
                                     │  (own network,   │
                                     │   egress allow-  │
                                     │   listed only)   │
                                     │                  │
                                     │  one focused     │
                                     │  project clone   │
                                     └──────────────────┘

  Self-improvement (rare):  meta → tier artifact → PR to private remote → operator merge → docker compose up
```

Three flows:

- **Inference path** — llama-cpp serves both little-coder (inner loop, judge, drafting) and OWUI (chat).
- **User path** — user triggers via OWUI or CLI; little-coder receives the trigger, drives open-terminal.
- **Self-improvement path** — meta-loop observes journals, produces artifacts, PRs to a private remote, operator merges and redeploys.

---

## 3. Components

### 3.1 `agent` (little-coder REPL) [Tool]

Exposed as an MCP server behind `lc-mcpo` (an MCP→OpenAPI sidecar, mirroring the existing `search-mcpo` pattern). API-key'd at the edge. Runs the inner loop: generate → tool/test error → retry. Drives open-terminal for all file edits and command execution.

The inner loop's logic is unchanged from upstream little-coder; instrumentation wraps it (journals, session/channel attribution).

**Implementation note — Node, not Python.** Upstream little-coder is a Node.js CLI on the `pi` framework. The agent container is therefore Node-based; the control-plane _wrapper_ around it (journals, config, sanitization, git-proxy, CLI, MCP edge) is Python, mirroring `search-mcpo`. Where this document writes `agent.py` (e.g. §6), read it as "the agent's code surface" — illustrative shorthand, not a literal path. The real tier-3 surface is two-part: the Node agent and the Python wrapper. See §6 and §11.

**The agent is stateless across tasks and across chat turns.** Each task is a fresh run (`--no-session`); the agent receives only the current task prompt plus the always-appended founding knowledge (§3.7). It never reads a journal — the journal reader exists solely for `meta` (Observer onward) and is never called in the agent path. The OWUI pipe forwards only the latest user message, not earlier turns. Continuity comes from two non-journal sources: the **workspace filesystem** (the repo as it currently sits, which the agent reads) and **git history** (the git-proxy permits `log`/`checkout`; commit messages and `auto/<date>-<topic>` branches are the durable record). The feedback path from journals back to the agent is indirect and generalized — journals → meta clusters patterns → drafts skills → augmenter loads matching skills → agent — never episodic recall. Journal-backed episodic memory is a deliberately rejected alternative (§15).

### 3.2 `meta` (outer loop) [Observer onward]

Triggered by evidence thresholds (§5), not a clock. Reads journals, assigns occurrences to clusters, calls the judge, drafts artifacts, runs validation, opens PRs. Single-flight: at most one iteration in progress.

The five concerns inside `meta` (clustering, judging, drafting, validating, merging) are designed with a clean seam — they share state via journals, skill library, and cohort store, not shared memory. Splitting into sub-services later requires adding network-hop concerns (timeouts, retries, partial failure) but no architectural rework.

### 3.3 `git-proxy` (safety choke point) [Tool]

Wraps every git call inside open-terminal. Sole git path; agent has no raw-git fallback. (Implemented by relocating the real git to `/usr/bin/git.real` and placing the proxy as the only `git` on `$PATH`.)

- **Whitelist:** `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (operator-pre-configured remotes only; no all-refs, no new remotes mid-task). `log`/`checkout` are permitted deliberately — git is the agent's project-state memory (§3.1).
- **Blocklist:** `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, all `submodule` subcommands, all history rewrites, anything touching `.git/` directly.
- **`.git/config`, `.git/hooks/`, `.git/info/` are mounted read-only to the agent.** Operator bakes allowed remotes in at project-switch time. Blocking `remote add` at the command level is bypassable by direct file writes to `.git/config`; the read-only mount closes that. Dynamic paths (`.git/objects/`, `.git/refs/`, `.git/index`) remain writable. **Enforcement status (2026-05-22): the command-level blocks are live and tested; the read-only mount itself is a known gap inside the named-volume layout — resolve before hostile-repo workload (plan open item #9).**
- **`core.hooksPath`** set to an operator-controlled directory outside `.git/`.
- **Operator git (via `docker exec`) bypasses the proxy by design.** The proxy exists for the agent, not the operator.

### 3.4 `open-terminal` (workspace plane) [Tool]

The repo lives here; edits, builds, tests run here. One focused project at a time, cloned directly into the workspace. No worktrees, no per-task subdirectories.

**Agent↔workspace integration.** The agent and open-terminal share the `little-coder-workspace` named volume (§3.6). The agent edits files on it directly; build/test/git **execution** is routed to open-terminal's `POST /execute` REST API (controlled by the `LC_ROUTE_EXEC` switch, default on). This keeps execution inside the network-isolated plane while letting the agent see and write the files. The shared volume crosses container uids, so both images set `git safe.directory '*'` and write with `umask 000`.

**Execution containment.** The agent's only execution path is `bash → open-terminal → git-proxy`. Upstream pi tools that would run commands locally and unrouted (`shell-session`, `browser`) are removed at image build, and `permission-gate` is set to `accept-all` so nothing prompts around the routing. This closes the bypass where the agent could escape the git-proxy via a local shell.

**Network posture:** open-terminal runs on its own network with explicit egress only — llama-cpp for inference, the operator-configured upstream git remote for the focused repo, the private search gateway if web-search is enabled, nothing else. Implemented as `lc-egress` (a tinyproxy default-deny host filter), since a precise per-host allowlist isn't expressible with plain compose networks. Reachability is the blast radius for anything the inner loop executes; this is non-negotiable before real workload. **Moving open-terminal to its own network removed OWUI's prior direct access to it (intended); chapter 2 restores access through `lc-mcpo` on a controlled path.**

### 3.5 Inference backend [Tool]

`llama-cpp` / llama-swap at `http://llama-cpp:8080/v1` on `llm-net`. Two variants:

- **`qwen3.6:27b`** (reasoning): judge, artifact drafting, §6 justifications, type selection.
- **`qwen3.6:27b-nothink`** (fast): cluster assignment, sanitization checks, routing, augmenter selection.

(The real llama-swap model id in the deployment is `qwen36-27b`; a `models.json` override maps the `llamacpp` provider names onto it.) Little-coder is the client in Tool. From Observer onward, the judge is also a client on the same backend. `n_parallel=2` is shared between interactive and meta work; meta backs off when interactive lanes are busy (interactive always wins).

### 3.6 Persistence (named volumes) [Tool — all declared on day one]

| Volume                    | Contents                                                          | Used from |
| ------------------------- | ----------------------------------------------------------------- | --------- |
| `little-coder-skill/`     | Artifact library (§7)                                             | Learner   |
| `little-coder-journals/`  | Three journals + `audit.jsonl` (§4)                               | Tool      |
| `little-coder-cohorts/`   | Derived counters + repro corpora (§5)                             | Observer  |
| `little-coder-polyglot/`  | Canonical Polyglot clone (§8)                                     | Learner   |
| `little-coder-workspace/` | The focused project clone, **shared with `open-terminal`** (§3.4) | Tool      |

The first four are the **expertise volumes** (accumulated state). `little-coder-workspace/` is different in kind: it's **project-scoped, not accumulated expertise** — `/project` switching wipes and re-clones it (§12.3). All are declared on day one so the first `docker compose up -d --build` in a later chapter doesn't silently wipe state that doesn't yet exist.

Mounted into `agent` and `meta`. **`docker compose up -d --build` recreates containers but preserves volumes.** Treating any of the expertise volumes as inside-container state silently wipes accumulated expertise on the first rebuild.

### 3.7 Two knowledge layers [founding: OWUI · skill library: Learner+]

The agent's knowledge comes from two deliberately separate layers. Keeping them apart is what lets `meta` learn the subtle craft gaps instead of re-teaching constraints.

| Layer                  | Authored by | Loaded                                                                                                                              | Lives in                                  |
| ---------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| **Founding knowledge** | Operator    | Always — appended to the system prompt via little-coder's own `--append-system-prompt` flag (inner loop stays upstream-stock, §3.1) | `agent-knowledge/` (baked into the image) |
| **Skill library (§7)** | `meta`      | On demand — augmenter selects per task by tag + embedding + token budget (§7.4)                                                     | `little-coder-skill/` volume              |

**Founding knowledge** is the baseline: the operating environment (so the agent doesn't burn tokens rediscovering the git-proxy, `/workspace`, the no-local-shell boundary every task — `agent-knowledge/environment.md`) and engineering principles (SOLID, encapsulation, naming, patterns, DRY/YAGNI — `agent-knowledge/engineering-principles.md`). It is operator-authored, always-loaded, and **never meta-touched.** A solid baseline raises the floor so self-improvement targets the ceiling (§13).

**The §7 library** is the learned layer — meta-drafted, cohort-evidenced, discovered per task. It adopts the **Anthropic Agent Skills format**: a `SKILL.md` body with `name` + `description` frontmatter, progressive disclosure (lean body; link heavy reference material rather than inlining), under ~500 lines, "explain-the-why" drafting — layered with §7.1's `id`/`cluster_id`/`tier`/`lang`/`domain` metadata. The `description` field feeds the §7.4 augmenter's selection. The Agent Skills _format_ is adopted; the skill-creator _A/B eval loop_ is **not** (it assumes subagents + a fixed eval set little-coder lacks) — cohort efficacy reversion (§8.5) is the production-truth equivalent and is authoritative.

**The layers interact at the tier ladder (§5.6):** a cluster the founding-knowledge baseline already covers is not a knowledge gap. See §5.6.

---

## 4. Data: journals [Tool]

The cohort math and clustering are only as trustworthy as the journals. Pin the envelope before any real traffic — fields cannot be retrofitted onto append-only history. **Journals are write-only from the agent's side (§3.1): they exist to feed `meta`, never to give the agent episodic context.**

### 4.1 Envelope

Every line in `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl`:

```
ts             UTC timestamp
task_id        ULID, minted at trigger, closed by terminal record
session_id     trigger session
channel        owui | cli | validation | batch
user_id        OWUI authn id (or "cli")
repo           full canonical URL
lang           detected primary language
seq            per-task counter
schema_version envelope version (forward-compat for readers)
```

`session_id` + `channel` + `user_id` are **tier-0 build requirements** — unrecoverable if added later. Without them, cohort windows (§5) interleave across unrelated callers and rate-delta math silently lies.

### 4.2 Task lifecycle

`task_started` / `task_ended` bracket every task. Tasks are reconstructed by `task_id`, never adjacency. Interleaved sessions are legal.

Outcome ∈ `{pass, fail, unverified}`. Asserts `pass`/`fail` only with a checkable signal (test-suite exit, the task's own acceptance command, or explicit caller confirmation via the operator surface). Otherwise `unverified`: feeds the acute track as error evidence, never as a success signal. Longitudinal track (§9) is the net for `unverified` work.

`task_abandoned` records an unclosed task past a per-channel timeout. Timeout is non-trivial: an interactive long refactor must not be abandoned; a hung short validation must not consume a worker overnight. Per-channel, preflight-tuned. **The deployed Tool default is 30m for owui/cli (a loop-backstop, not a craft signal); `meta` clustering (§5) must treat `task_abandoned` distinctly from `fail`, or a wrong timeout breeds a phantom cluster.**

**Outcome amendment.** Triggers are fire-and-await — the caller awaits a result, the harness doesn't. After `task_ended`, the caller may amend via `lc admin task confirm <task_id> [pass|fail]` (CLI) or `/confirm <task_id> pass|fail` (OWUI). Emits `task_outcome_amended` referencing the original `task_id`. Cohort math uses the amended outcome from that point forward. 7-day window; frozen outside.

### 4.3 Durability + rotation

- Append + fsync on every terminal and every error record. Line-buffered for the rest.
- Size-triggered rotation. On rotation, the longitudinal miner (§9) consumes the segment into trend aggregates before archival; the acute track keeps raw segments for `max(M across live clusters) + margin`.
- Schema-validated at write time. Malformed records are rejected, not appended.
- `meta` reads up to a committed offset. In-flight tasks are never clustered or counted.

### 4.4 `audit.jsonl`

Separate journal for operator actions: `project_switched`, `upstream_pulled`, `approve_decision`, `task_outcome_amended`, `artifact_retired`, deploys, preflight exit. Different reader, longer retention, different access controls than the three task journals. Mixing them works but bleeds responsibilities.

---

## 5. Clusters and escalation [Observer → Self-modifier]

### 5.1 Identity vs. label

Each cluster has an **immutable synthetic `cluster_id`** and a **mutable human label**. Cohort records key on `cluster_id`. Relabeling never touches cohort history.

### 5.2 Assignment

New occurrences join the nearest existing cluster above a similarity floor (embedding + the cluster's judge-written discriminator). Below the floor → `unassigned` pool. The judge mints a new `cluster_id` only when the unassigned pool itself forms a coherent group.

### 5.3 Split / merge lineage

Recorded as parent↔child events. A split copies the parent window to each child marked `inherited` (not `observed`); escalation cannot fire on inherited counts. A merge sums observed counts and resets the quarantine window.

### 5.4 Cohort store (derived index)

The journals are the durable source of truth. Cohort counters are an **event-sourced projection over them** — periodically checkpointed, fully rebuildable from journals on demand. A corrupt counter file is a recoverable incident, not data loss. Schema changes don't require migration drama — bump version, rebuild.

### 5.5 Scoping

Cohorts are scoped by `lang` + `task_shape`, **aggregated across repos**. A craft gap (Rust lifetimes, multi-file refactors) recurs across repos; per-repo scoping never reaches the quarantine window and never escalates. `repo` is recorded per occurrence for drill-down.

### 5.6 Tier ladder

The trigger is not recurrence; it is **recurrence after intervention.**

| Tier | Trigger                                                                         | Intervention              | Risk            |
| ---- | ------------------------------------------------------------------------------- | ------------------------- | --------------- |
| 0    | N ≥ ~5 occurrences, no prior intervention, **baseline-silent**                  | Knowledge entry           | Pure addition   |
| 1    | ~20+ after tier-0, rate unchanged — **or a baseline-covered cluster recurring** | Tool-craft _or_ plan-slot | Prompt-shape    |
| 2    | Same persistence after tier-1                                                   | Routing rule              | Decision-shape  |
| 3    | Same persistence after tier-2 + §6 justification                                | Code change               | Behaviour shift |

Each tier requires a **quarantine window M tasks** before escalation. M is per-cluster, sized to the cluster's natural frequency. Cohort accounting (before/after intervention with rate delta), not raw counters.

**Founding knowledge raises the tier-0 bar (compliance vs knowledge gap).** Founding knowledge (§3.7) already states the operating environment and engineering principles. So a recurring cluster splits two ways:

- **Knowledge gap** — the baseline is _silent_ on this. Tier-0: write the knowledge entry. (Pure addition.)
- **Compliance gap** — the baseline already _covers_ this, but the instruction isn't landing. Restating it as tier-0 would be noise (and inflate the sense that meta "learned" something it was already told). This enters at **tier-1 enforcement** (tool-craft or plan-slot to make the principle bite) — not a tier-0 restatement.

To make the distinction, the judge receives founding knowledge in its context and emits a `baseline_covers` flag (§9.2 / Observer judge prompt). Tier-0 fires only when `baseline_covers == false`.

### 5.7 Type selection within a tier

The ladder controls **risk class**; the judge picks **type** within a tier. At tier-1: _"Given this cluster's signature, is the gap better addressed by tool-craft or a plan-slot? Argue both, then pick."_ The argument is journaled and shown on the operator surface for approval. Same shape as the §6 escalation argument, applied within a tier.

### 5.8 Routing-rule exploration

Routing rules can suppress their own evidence — a rule saying "don't invoke planner for X" stops generating data about whether planner would have helped X.

- **Staged-freeze gates entry into tier-2.** No routing rule until tier-0 and tier-1 windows have run and the cluster has resisted them.
- **5–10% random-exploration** runs against each rule indefinitely — deliberately take the path the rule says to avoid. Without this, a wrong rule becomes self-confirming forever.
- §8 efficacy reversion retires rules that don't move the cohort.

---

## 6. Code-change justification (tier-3 gate) [Self-modifier]

A self-PR to the agent's code surface requires a written argument:

1. The cluster and its persistence record across tiers 0–2.
2. Specific interventions tried.
3. **Explicit argument for why no plausible knowledge entry, tool-craft pattern, plan-slot, or routing rule could have closed the gap.**
4. Proposed structural change and expected effect.

The argument is itself a journal entry. **If (3) cannot be articulated, the structural change is not justified** — write the missing skill instead and let the cohort prove it insufficient.

**Two-part code surface.** Per §3.1, "the agent's code surface" is two things: the Node agent (`pi` framework) and the Python control-plane wrapper. A tier-3 change may touch either or both, and they have different blast radii — a wrapper change is contained to the control plane; a Node-agent change alters the inner loop itself. The §6 argument must name which surface it touches; the §11 candidate validation must exercise that surface. The single-`agent.py` framing elsewhere in this document is illustrative shorthand (§3.1).

---

## 7. Skill library [Learner → Self-modifier]

Artifacts adopt the Anthropic Agent Skills format layered with the metadata below — see §3.7 for the format rationale and the format-adopted/eval-loop-not decision.

### 7.1 Frontmatter

Required keys, enforced at draft time. Agent Skills fields (`name`, `description`) plus the §7.1 metadata:

```
name          Agent Skills: human-readable skill name
description    Agent Skills: when-to-use + what-it-does; feeds the §7.4 augmenter
id            unique
cluster_id    the cluster this artifact serves (§5)
tier          0 | 1 | 2
lang          target language (or "*")
domain        topic tag (e.g. async, fs, parsing)
tool          tool/operation tag (or "*")
task_shape    structural tag (e.g. refactor, bugfix, greenfield)
created       UTC
supersedes    prior artifact id or null
status        active | superseded | retired
```

No artifact is written without all keys. The body follows Agent Skills conventions: lean, progressive disclosure, under ~500 lines, explain-the-why.

### 7.2 Three consumption paths

Not all artifact types are loaded the same way:

| Type                  | Path                    | Loaded                              |
| --------------------- | ----------------------- | ----------------------------------- |
| Knowledge (tier-0)    | `skill/knowledge/*.md`  | Augmenter selects per-task          |
| Tool-craft (tier-1)   | `skill/tools/*.md`      | Augmenter selects per-task          |
| Plan-slot (tier-1)    | `skill/plan-slots/*.md` | Planner reads at boot; watches file |
| Routing rule (tier-2) | `skill/routing/*.yaml`  | Router reads at boot; watches file  |

Plan-slots and routing rules are **configuration**, not retrieval — they apply universally once installed, gated only by their own conditions (e.g. `if lang == rust`). The augmenter's selection logic below applies to skill entries only.

### 7.3 Atomic-rename writes

Hot-reloaded files (plan-slots, routing rules) must be written via **atomic rename**: write to `<name>.tmp`, then `rename(2)` into place. Watchers see either the old file or the new one, never a partial write. Readers ignore `*.tmp`. Standard discipline for watched-config files.

### 7.4 Augmenter selection

For skill entries (knowledge, tool-craft):

1. Hard filter on structured tags (`lang`, `domain`, `task_shape` inferred from trigger + early tool calls).
2. Embedding rank within the filtered set (the Agent Skills `description` is the primary text ranked against).
3. Hard token budget; over budget, prefer **cohort-proven entries (§8) and tighter match**. Tier is **not** a tiebreaker on its own.
4. Per-task selection logged — required by §8's in-context assertion.

A tightly-matched tier-0 knowledge entry beats a loosely-matched tier-1 tool-craft entry. Tier governs production discipline, not runtime selection.

### 7.5 Supersession

A new artifact on an existing `cluster_id` sets `supersedes = <prior_id>` and flips the prior to `superseded`. The augmenter selects only `active`. Cross-cluster contradictions can't be auto-resolved; a periodic judge pass flags them to the operator surface. (Supersession is _within_ the §7 library; contradictions against founding knowledge are handled upstream at the tier ladder via the compliance-gap rule, §5.6 — meta shouldn't draft what the baseline already says.)

### 7.6 Retirement

Driven by §8 efficacy reversion: `ineffective` → `retired`. Dropped from augmenter selection, kept on disk for audit and rollback, retirement journaled to `audit.jsonl`.

---

## 8. Validation [Learner → Self-modifier]

### 8.1 Polyglot oracle

The Aider Polyglot benchmark (225 exercises) is the system's score function. Wrapped behind an `Oracle` interface (`run_subset(cluster_id, biased_subset) → ScoredResult`) so additional harnesses (MultiPL-E, SWE-bench) can be added without rewriting the validation pipeline.

Subset selection biases toward exercises in the cluster's domain. Pure random subsets are noisy at small N.

### 8.2 Baseline

Polyglot score at the last `main` green tag, **re-measured on the current biased subset**. A stale global number is invalid. A successful merge sets the new baseline.

### 8.3 Regression test

Block the merge if the candidate subset scores below baseline by more than a noise margin, at a minimum subset N. Below N → "insufficient evidence", not a pass. A single-exercise flip inside the margin is not a regression. N and margin are preflight-derived from measured Polyglot variance.

### 8.4 In-context assertion

A tier-0/1 artifact matters only if the augmenter selects it into context during validation. If it was never in-context, **the gate measured nothing → result is void, not pass.** Validation logs per-task augmenter selections to assert this.

### 8.5 Efficacy reversion

No-regression is not enough — it justifies merging, not keeping. Each merged artifact carries its cluster's cohort window. If post-intervention rate is statistically indistinguishable from pre after the window, the artifact is auto-flagged `ineffective` and reverted on the next iteration. Keeps the library lean and stops dead weight loading into every future context. **This is the production-truth validation that stands in for the (not-adopted) skill-creator A/B eval loop (§3.7).**

---

## 9. Telemetry [Tool (metrics) · Observer (acute) · Learner (longitudinal)]

### 9.1 Two tracks

- **Acute (errors)** — per-task tool errors, failed tests, parse failures, loops. Drives the cluster system.
- **Longitudinal (structure)** — cyclomatic complexity, file size, fan-out, churn, sampled across repos. Drives a separate pattern miner on **trends**, not error counts.

The longitudinal track is the safety net for **silent clusters**: tasks where tests pass but code degrades (e.g. SOLID decay after many features). The acute track cannot see these because the inner loop reports success.

**Longitudinal metrics are the measurement half of the SOLID/code-craft mechanism.** `engineering-principles.md` founding knowledge (§3.7) is the _instruction_ half — it tells the agent to write SOLID, encapsulated, readable code. The longitudinal track is the _verification_ half — it measures whether the agent did, over time. A longitudinal anomaly that maps to a stated baseline principle is a **compliance gap** (the agent is drifting from a principle it was told), which escalates as tier-1 enforcement per §5.6 — not a tier-0 restatement of the principle. Instruction without measurement is aspiration; this closes the loop.

### 9.2 Clustering

The judge proposes human-readable cluster labels in natural language, refined across iterations. Raw embedding clusters tend to pick up shallow features (same language, same file size) and miss craft shape. Labels are auditable: an operator can read what the system thinks it's learning. The judge also receives founding knowledge and emits the `baseline_covers` flag (§5.6) so reports distinguish knowledge gaps from compliance gaps.

Two cadences, deliberately unequal: clustering reshapes the taxonomy slowly over a large window; escalation evaluation runs faster over existing clusters. Occurrences are assigned at ingest, so you never need a cluster to count one; the judge only periodically reshapes, carrying lineage so counts survive.

### 9.3 Metrics

Prometheus endpoint on `agent` (Tool) and `meta` (Observer+):

- Queue depth, judge wall-clock minutes/day, GPU minutes/day, candidate-validation duration histogram
- Journal write rate, sanitization rejection rate (shadow-mode counts in Tool)
- Augmenter selection count per artifact (feeds §8 in-context assertion at a glance)
- llama-cpp slot occupancy

Alarms route to the operator UI: queue-depth, sanitization-drift, candidate-validation-timeout.

---

## 10. Security [Tool sets foundation; sanitization-filter shadow in Tool; full activation Learner+]

### 10.1 Judge stays in-stack

The judge runs on the in-stack `llama-cpp` backend, never egresses. Privacy mandate — no user code leaves internal nets — overrides the orthodox "different and stronger judge model" advice. The same-model blind spot is real and addressed in two layers:

- **Three model-independent backstops carry the load:** the Polyglot oracle (model-independent), cohort proof (empirical), human gate (external). The judge proposes; none of these is the judge.
- **Adversarial framing is hygiene, not a substitute.** The judge is invoked contrarian — different system framing, fresh context, opposed priors, tasked to argue the counterfactual _and then argue why it would not have helped_. Catches blind spots rooted in momentary stance; misses those rooted in training distribution. The three backstops above are what carry the load.

OpenRouter to a stronger external judge is a future option if the privacy posture relaxes; deferred, not rejected on principle.

### 10.2 Sanitization (every outbound)

Journals are scrubbed before entering any outbound: secrets/key-shaped strings redacted, large file bodies reduced to structural digests, PII stripped. Applies to:

- Judge calls
- PR-body assembly (§11.6)
- Any future operator export

**Filter is pinned and tested.** One filter, all call sites. **Filter failure aborts the call — never "send anyway."** **Built in Tool and run in shadow mode** (records what it would redact; nothing leaves the stack in Tool, so this accumulates a rejection-rate baseline); **promoted to enforcing in Observer** when the judge starts making real calls. Audits are evidence-triggered: when rejection rate moves outside its baseline envelope, an audit is queued for the operator surface (sample raw records, human-review for false negatives). Threshold is preflight-derived from the Tool-era shadow baseline.

### 10.3 Credentials

- **Self-improvement PAT:** fine-grained, scoped to `contents:write` on the private self-improvement remote only. No repo deletion, no admin, no other repos. Lives with `meta` / git-proxy; unreachable from the workspace plane.
- **Per-repo deploy tokens** for work repos under Flow 1/2: least-privilege, injected per task, never ambient, never the self-PAT. A task for repo A cannot reach repo B.
- **MCP edge authentication:** the `lc-mcpo` task-trigger surface is API-key'd, matching `search-mcpo`. Operator commands authenticate at their surfaces (CLI shell access; OWUI authn) — never at the MCP server.

### 10.4 Self-poisoning

Skill artifacts inject into every future context. A hostile repo or prompt-injected error message could steer an artifact's text. **The threat is model compliance with prompt context, not file execution** — prompt context IS instruction. Saying artifacts are "data, never eval'd" misses the threat.

Controls:

1. **Provenance.** Each artifact records its `cluster_id` and the journal evidence range it was derived from.
2. **Human text review.** Pre-trust, every artifact; post-trust, sampled review of auto-merged tier-0 entries.
3. **Efficacy auto-retire.** A poisoned-but-useless entry auto-retires via §8. A poisoned-and-useful entry is the hard case — the human gate is the actual catch.
4. **No code paths in skill files.** Artifacts are markdown. The skill loader does not import, eval, or interpret them. Eliminates code-execution-by-file-load; does not eliminate model-compliance attacks (see above).

Note: founding knowledge (§3.7) is operator-authored and so is not a self-poisoning surface — it is never meta-written. This is part of why the two layers are kept distinct.

### 10.5 Trust boundary (stated)

- **Operator:** trusted. Operator-level git via `docker exec` bypasses the proxy by design.
- **Agent:** untrusted. `.git/config` read-only (enforcement gap noted in §3.3), MCP key required, per-repo tokens only, no local-shell escape (§3.4).
- **User repos under task triggers:** actively hostile by default. Sandboxed by open-terminal's network policy (§3.4).

### 10.6 Privacy of the self-improvement remote

The remote little-coder pushes `auto/*` branches and tier-3 PRs to is **private, full stop.** Cohort evidence, journal ranges, and §6 justifications travel via the PR body; routing them anywhere a third party might train on is incompatible with the privacy posture. Precondition, not hardening.

---

## 11. Tier-3 deploy (candidate/active) [Self-modifier]

Blue/green for self-modification. Converts the dangerous thing ("a process mutates itself") into a well-understood one ("stand up a candidate, validate, swap"). Applies only to tier-3 — the only tier where the running container has the affected code loaded.

### 11.1 Flow

1. Stage the artifact in a separate ephemeral open-terminal workspace.
2. Provision **paired candidate topology**: `candidate-little-coder` + `candidate-open-terminal`. Active's persistent volumes mounted **read-only** to candidate (cannot mutate active state). Writable tmpfs for candidate's ephemeral state. Same containment as active.
3. Active drives the test as a stable, known-good harness. The verdict is external (Polyglot + §6 justification + human gate), never active's own assessment.
4. Two acceptance tests, both required:
   - **Issue fixed**: reproduce-then-fix the specific §6 cluster.
   - **No regression**: Polyglot biased subset score ≥ baseline.
   - **Upstream-merge validation uses a stratified subset across all live cluster domains** (or full Polyglot if compute allows), since an upstream merge can touch the harness, planner prompt, or skill loader.
   - **Two-part surface (§6):** the candidate must exercise whichever surface the change touches — Node agent, Python wrapper, or both.
5. On pass: persist the executable repro to `little-coder-cohorts/repro/<artifact_id>/` (surviving volume). Tear down candidate.
6. Open PR from `auto/<date>-<topic>` → `main` on the **private** remote. PR body templated from cohort + validation records, **passed through sanitization (§10.2) before posting**. Filter failure aborts the PR.
7. **Human merge + manual deploy.** Operator reviews PR, merges. Operator runs `docker compose up -d --build little-coder`. Active container recreated; volumes preserved.
8. **Post-deploy verification:** (a) compose healthcheck green on `agent` + `lc-mcpo`; (b) **smoke task = re-run the persisted §6 repro** against the rebuilt active. The repro is already known-good against the candidate; a failure here means the deployed binary is not what got validated. Reusing the validation repro avoids both the too-easy smoke ("echo hello passes a broken build") and the too-expensive ("re-run full Polyglot defeats the point") traps.
9. **Rollback:** `git revert` + same compose command, or pin to prior tag.

### 11.2 PR body contents

Mechanically templated:

- §6 justification (cluster history, interventions tried, structural argument, which code surface).
- §5.3 cohort evidence (cluster_id, occurrences, lineage).
- §11.1 validation outputs (exercises run, scores vs. baseline, repro result).
- §10 provenance (journal evidence range, sanitization pass).

Templating is mechanical. **The judgment is the reviewer's**, not the template's.

### 11.3 Deploy actor

The operator. No automated supervisor at this scale — tier-3 is rare by design. If tier-3 ever becomes frequent, a dedicated tiny supervisor service is the upgrade path. Not now.

---

## 12. Operations [Tool foundation; extensions per chapter]

### 12.1 Branch + tag discipline

- All outer-loop changes land on `auto/<date>-<topic>` branches. Never direct to `main`.
- Tag `pre-iteration-<n>` before each outer-loop attempt. Rollback target = active instance + last green tag.

### 12.2 Upstream fork-parent

`devonpveller/little-coder-extended` tracks `itayinbarr/little-coder`. **Pulls are operator-initiated** via `/upstream pull` — never auto. Behavior:

- Tiers 0–2 (additive skill files under `skill/`) land cleanly; upstream doesn't touch those paths.
- Tier-3 conflicts resolved manually on `upstream-merge/<date>` branch, then run §11.1 validation with the broader stratified subset (§11.1 step 4) before becoming active.
- Self-authored tier-3 artifacts invalidated by an upstream pull are journaled as `invalidated_by_upstream` and retired.
- The pull is journaled as `upstream_pulled` with old and new commit ids.

Auto-rebasing onto a moving upstream is avoided: an artifact validated yesterday sitting on a subtly different base today is the failure mode we're preventing.

### 12.3 Project focus

open-terminal hosts one focused project at a time. Switching is an explicit operator action via `/project repo: <link>`:

```
/project repo: <link>
  → normalize <link> to canonical form (host + owner + repo, lowercased)
  → if no current focus:
      clone, set focus, journal project_switched, proceed
  → if matches current focus:
      no-op, proceed
  → if doesn't match current focus:
      → if a task is in flight: reject, suggest cancel-or-wait
      → else: tag prior state, wipe workspace, clone, set focus,
              journal project_switched, proceed
```

URL normalization prevents spurious wipes when SSH/HTTPS forms of the same repo are used. The in-flight guard prevents silent wipe-under-load. The `project_switched` journal record matters for cohort analysis: rate changes coinciding with a switch must be visible in the data. The wipe targets `little-coder-workspace/` only (§3.6) — the expertise volumes are untouched.

### 12.4 Concurrency

- **One task at a time** in open-terminal.
- **FIFO queue across triggers.** A CLI trigger arriving during an OWUI task waits its turn. Users see "queued", not "busy, try later."
- **Validation runs** against fresh ephemeral clones, not the focused-project workspace. Never share workspace with interactive work.
- **Human attach** is read-only. Mid-task writes from a human collide with the inner loop in ways no design can save. (The OWUI Stop control maps to whole-task cancellation — operator-triggered abandonment — not mid-flight redirection.)

### 12.5 Single-flight + budgets

- **`meta`:** at most one iteration in progress.
- **Budget caps** per window: artifacts/iteration = 1, judge wall-clock minutes/day, Polyglot exercise-runs/day, journal write rate. Exceeding defers; never drops evidence.
- **Deferral queue is bounded; evidence is not.** Soft limit on queue depth → operator alarm. Hard limit → **coalesce per `cluster_id`**, never drop. When a coalesced entry runs, cohorts are re-read fresh from journals (no stale snapshots). Cross-cluster FIFO; no cluster is starved.
- **Resource isolation:** `meta` checks llama-cpp slot occupancy before issuing inference; backs off when interactive lanes are busy. Interactive always wins.

### 12.6 Operator surface

The §10.5 human gate needs somewhere to live. One surface, two forms:

- **CLI** ships first (near-free against little-coder's existing CLI): `lc admin project switch`, `pending`, `approve`, `reject`, `upstream pull`, `shutdown [--drain-deadline]`, `task confirm`.
- **OWUI pipeline** (chapter 2, implemented as a Pipe function): task triggers map to chat; operator commands map to slash-commands (`/project repo:`, `/upstream pull`, `/approve <id>`, `/pending`, `/confirm <task_id>`). Artifact-review messages render artifact text + cohort evidence + provenance + Approve/Reject controls.

**Privilege separation:** operator commands authenticate at the surface (CLI = host shell; OWUI = configured auth / user role inside the Pipe), never at the MCP server. The MCP server takes task triggers only (`lc-mcpo` exposes `trigger_task`/`task_status`/`project_focus` and no operator surface). A regular user cannot escalate into an operator action by shaping a task trigger like one.

The surface lists: pending artifacts, tier-3 justifications, contradiction flags, efficacy-reversion notices, queue-depth alarms, sanitization-audit prompts. Observer reports (from chapter 3) distinguish knowledge gaps from compliance gaps (§5.6). Approve/reject is the merge gate. Pre-trust, everything routes here; post-trust, only tiers ≥ 1 and all flags.

### 12.7 Health + shutdown

- Compose healthcheck on `agent` (daemon `/health`) and `lc-mcpo` (`/openapi.json` reachable).
- **SIGTERM = drain mode.** Refuse new triggers (return "shutting down"); allow in-flight to complete to a configurable deadline; then SIGKILL.
- **Drain deadline** is shorter than the shortest channel's expected p95, with operator override per shutdown (`lc admin shutdown --drain-deadline 30m`). Open `task_id`s past the deadline are journaled `task_abandoned` with reason `shutdown`. Volumes survive; restart resumes from the journals.

### 12.8 Config

All tunables in a **centralized typed config** (YAML + JSON schema), validated at boot. Hot-reloadable where safe (filter ruleset, budgets, thresholds); restart-required where schema-affecting. Prose-as-config doesn't survive contact with operations.

Tunables include: per-cluster M, Polyglot N/margin, budget caps, `task_abandoned` timeout per channel, exploration rate (5–10%), coalesce thresholds, similarity floor, augmenter token budget, sanitization drift threshold, sanitization filter ruleset, drain deadline.

### 12.9 Schema migration

Journal envelope, frontmatter, cohort store, config — all carry `schema_version` and evolve. Readers tolerate older shapes (forward-compat). Migrations are explicit operator-run jobs.

**Tier-3 self-changes cannot propose schema changes.** Mixing autonomous self-modification with schema migration is the class of failure §6 is built to prevent — the §6 justification cannot articulate "no skill could have replaced this" for a data-shape change. Operator-only, full stop.

### 12.10 Failure semantics

Nothing fails open:

- Judge unreachable → defer, alarm, no merge.
- Polyglot won't run → "insufficient evidence" (not pass), defer.
- Candidate won't boot → fail closed, tear down, cluster stays at its current tier (no escalation credit for a failed deploy).
- Sanitization filter errors → abort the call.
- Every failure journaled.

### 12.11 Golden-journal test suite

Preflight validates against real journals — that tests the journals, not `meta`'s logic. A **golden-journal suite** of synthetic journals with known cohort shapes exercises cluster assignment, split/merge lineage, tier escalation, efficacy reversion, and the sanitization filter. Each release of `meta` runs against the suite before deploy.

---

## 13. Preflight [Observer entry gate]

Before any meta iteration runs, deploy with **journals on, meta off** for ≥ 1–2 weeks of real workload. Reasons:

- Cluster taxonomy needs real errors, not imagined ones.
- Cohort math needs baselines.
- Judge prompt needs real examples to calibrate against.

Founding knowledge (§3.7) is in place from chapter 2, so preflight observes a system already at its baseline floor — the clusters that survive are genuine gaps above the baseline, not constraints the agent could have been told up front.

Exit only when, against real journals:

1. ≥ K distinct clusters each have ≥ their M window of _observed_ occurrences.
2. Polyglot baseline variance measured; N + margin set (§8.3).
3. Counterfactual + adversarial judge prompt dry-run on real examples + human-rated (including baseline-covered cases, to verify the compliance-vs-knowledge distinction fires — §5.6).

The transition is a human decision, journaled to `audit.jsonl` — not an automatic threshold.

---

## 14. Calibration (preflight-derived) [Learner → Self-modifier]

Numbers below are tuned in preflight against measured behavior, not guessed up front. Until preflight measures the relevant baseline, the number has no value to set.

| Tunable                                        | Source                             | Used in         |
| ---------------------------------------------- | ---------------------------------- | --------------- |
| Per-cluster M (quarantine window)              | Cluster natural frequency          | §5.6 escalation |
| Polyglot N (min subset) + regression margin    | Measured Polyglot variance         | §8.3            |
| `task_abandoned` timeout per channel           | Channel p95 observation            | §4.2            |
| Sanitization drift threshold                   | Rejection-rate baseline + envelope | §10.2           |
| Drain deadline default                         | Shortest channel p95               | §12.7           |
| Reserved-slot promotion threshold              | `meta` starvation rate observation | §12.5           |
| Counterfactual judge prompt wording + few-shot | Dry-run human rating               | §5, §10.1       |

---

## 15. Design context (rejected alternatives)

Options considered and consciously not taken. Kept so they aren't re-derived under context pressure.

- **External judge model (e.g. OpenRouter frontier model).** Rejected for the privacy posture (no user code leaves internal nets). Reconsidered as a future option if posture relaxes.
- **Auto-pull from upstream fork-parent.** Rejected because an artifact validated yesterday on a subtly different base today is hard to debug; `/upstream pull` is operator-initiated.
- **Watchtower / registry-poll auto-deploy for tier-3.** Rejected because "new tag → auto-restart" makes the tag the trigger; the §10.5 human approval should be the trigger.
- **`meta` triggers its own deploy.** Rejected — puts the to-be-replaced process in charge of replacing itself. Race conditions, and a tier-3 bug could prevent its own rollback.
- **Shared open-terminal for interactive and validation.** Rejected; validation runs on every merge and would collide with interactive work deterministically. Validation runs in its own ephemeral container.
- **Per-task git worktrees, per-session directories.** Considered as isolation; rejected for the simpler one-repo / one-task / FIFO model. Tier-3 candidate validation is the only place a separate ephemeral container is used.
- **Per-session containers as the default substrate.** Rejected — open-terminal isn't built to spawn containers; infra burden too high for the common case.
- **Per-repo cohort scoping.** Rejected — a craft gap recurs across repos; per-repo never reaches M and never escalates. Scope by `lang` + `task_shape`.
- **Embedding-only clustering.** Rejected — picks up shallow features (language, file size), misses craft shape. Judge-proposed human-readable labels (§9.2) instead.
- **Single global lock on the workspace as the concurrency model.** Earlier drafts rejected this for serializing inner behind outer; reconsidered and accepted in the final design — validation runs in its own container, never sharing the focused-project workspace, so "single lock" never blocks validation.
- **Tier-locked artifact-type selection (mechanical per-tier mapping).** Rejected — forces wrong type when a cluster's signature doesn't match. Judge picks within the tier.
- **Time-based audit cadences.** Rejected as inconsistent with the evidence-based posture. Replaced by metric-drift triggers (§10.2 for sanitization).
- **History-rewriting tools in the git-proxy whitelist.** Off the table — defeats the rollback story. Operator-level git bypasses the proxy and can do anything; the proxy exists for the agent.
- **`meta` running its own deploy automation.** Rejected — see "`meta` triggers its own deploy" above. The operator is the deploy actor.
- **`meta` authoring founding knowledge.** Rejected — founding knowledge is the operator-authored baseline (§3.7). If meta could write it, the compliance-vs-knowledge-gap distinction (§5.6) collapses and the baseline becomes a self-poisoning surface (§10.4). The two knowledge layers stay distinct on purpose.
- **Skill-creator A/B eval loop as the §7 validation engine.** Rejected — Anthropic's skill-creator loop (draft → with-skill-vs-baseline → improve) assumes subagents and a fixed eval set little-coder doesn't have. The Agent Skills _format_ is adopted (§3.7, §7.1); cohort efficacy reversion (§8.5) is the production-truth validation instead.
- **Journal-backed episodic memory for the agent.** Considered — assemble a scoped slice of `outcomes.jsonl`/`audit.jsonl` for the current repo into the task prompt so the agent could recall _why_ prior tasks acted as they did. Not adopted: the agent is intentionally stateless (§3.1), and **git-as-project-memory is the intended boundary** (the agent reads the filesystem and `git log` to reconstruct project state). If the stateless boundary ever proves limiting, this is a deliberate future addition at the daemon layer — distinct from the §7 skill loop, which carries _generalized_ craft, not episodic recall.
