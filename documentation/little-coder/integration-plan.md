# Self-Improving Little-Coder — Build Plan

> **Source of truth:** [`Self-improving-little-coder-design.md`](Self-improving-little-coder-design.md). This plan is sequencing; the design doc is the _why_ and the _what_. Design doc wins on conflict.
>
> **Paired task tracker:** [`integration-tasks.md`](integration-tasks.md) — checklist with status and decision log. Update both together when scope shifts.
>
> **Posture:** evidence-based, additive-by-default. Knowledge first, code change last. Build is **chaptered**, not phased — each chapter is a complete, deployable system. The build agent stops at the end of a chapter; the operator decides when to start the next.

---

## 1. Goal

Build little-coder into the `ai-stack` as a usable coding tool, then grow it over time — chapter by chapter — into a self-improving system. Each chapter ends in a working deployable system. The operator advances to the next chapter after living with the current one long enough to understand what's wanted from the next.

**Non-goals (any chapter):** hot-patching the running process; auto-pulling fork-parent upstream; multi-project concurrency.

---

## 2. The five chapters

| #   | Chapter           | What gets built                                                                                                                                                                                                                                                      | Stopping here means                                                                  |
| --- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| 1   | **Tool**          | little-coder + open-terminal pipeline. Llama-cpp client. CLI surface. Journals quietly running with full envelope. Named volumes. git-proxy at workspace edge. `/project repo:` switching. Sanitization filter in shadow mode. Metrics endpoint. Centralized config. | A working little-coder driven from CLI. No OWUI yet, no `meta`. Daily-driver useful. |
| 2   | **OWUI pipeline** | `lc-mcpo` activated. OWUI registers little-coder as a tool. Slash-commands for operator actions.                                                                                                                                                                     | Same system, plus chat. Still no `meta`.                                             |
| 3   | **Observer**      | `meta` reads journals, clusters occurrences, surfaces patterns. Nothing written. Judge prompt calibrated. Sanitization gates live judge calls.                                                                                                                       | You see what would be learned, without artifacts.                                    |
| 4   | **Learner**       | `meta` drafts tier-0/1 artifacts with manual approval. Polyglot validation. Efficacy reversion. Augmenter loading approved artifacts. No code changes.                                                                                                               | Self-improvement with you as gatekeeper.                                             |
| 5   | **Self-modifier** | Auto-merge trusted tiers. Tier-2 routing rules + exploration. Tier-3 code changes (rare, blue/green).                                                                                                                                                                | Endpoint.                                                                            |

Chapters are not parallel and not skippable. Each gates the next.

---

## 3. Architectural summary

Two planes, kept separate.

| Plane              | Container(s)                                                 | Role                                                                                                                                                                         |
| ------------------ | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Control            | `little-coder` (`agent` always; `meta` from Observer onward) | Inner loop (Tool); outer loop (Observer+). Journals, judge, cohort math, augmenter as chapters add them. State on named volumes.                                             |
| Workspace + exec   | `open-terminal` (existing, reconfigured)                     | One focused project cloned directly. One task at a time. FIFO across triggers. **Own network with explicit egress allowlist — disconnected from OWUI's network as of Tool.** |
| Inference          | `llama-cpp` / llama-swap (existing)                          | `qwen3.6:27b` (reasoning) + `qwen3.6:27b-nothink` (fast). Inner loop is the client in Tool; judge joins from Observer onward.                                                |
| MCP edge           | `lc-mcpo` (built in Tool, activated in OWUI)                 | MCP→OpenAPI bridge for OWUI/CLI. API-key'd.                                                                                                                                  |
| Safety choke point | `git-proxy` (in-container wrapper, in Tool)                  | Sole git path inside the workspace. `.git/config`+hooks read-only to agent.                                                                                                  |

**Persistent state** lives on named volumes mounted into little-coder's containers. The container is ephemeral; the volumes are the persistence boundary (design §3.6):

- `little-coder-skill/` — artifact library (used from Learner; declared in Tool)
- `little-coder-journals/` — three task journals + `audit.jsonl` (used from Tool)
- `little-coder-cohorts/` — derived counters + repro corpora (used from Observer; declared in Tool)
- `little-coder-polyglot/` — canonical Polyglot clone (used from Learner; declared in Tool)
- `little-coder-workspace/` — the focused project clone, **shared with `open-terminal`** (used from Tool). Project-scoped, not accumulated expertise: `/project` wipes and re-clones it (design §12.3). This volume is the agent↔open-terminal integration surface — see Decision Log 2026-05-22.

The four expertise volumes are declared in Tool so the first `docker compose up -d --build` later in the project doesn't silently wipe state that doesn't yet exist.

### Two knowledge layers

The agent's knowledge comes from two deliberately separate layers. Keeping them apart is what lets `meta` learn the subtle craft gaps instead of re-teaching constraints.

| Layer                  | Authored by | Loaded                                                                                                          | Lives in                                              | Built     |
| ---------------------- | ----------- | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | --------- |
| **Founding knowledge** | Operator    | Always — appended to the system prompt via little-coder's own `--append-system-prompt` flag (inner loop stays upstream-stock, design §3.1) | `little-coder/agent-knowledge/` (baked into the image) | Chapter 2 |
| **Skill library (§7)** | `meta`      | On demand — augmenter selects per task by tag + embedding + token budget (design §7.4)                          | `little-coder-skill/` named volume                    | Chapter 4+ |

Founding knowledge is the **baseline**: the operating environment (so the agent doesn't burn tokens rediscovering the git-proxy, `/workspace`, the no-ShellSession boundary every task) and engineering principles (SOLID, encapsulation, naming, patterns). The §7 library is the **learned layer** — meta-drafted, cohort-evidenced, discovered per task. A solid baseline raises the floor so self-improvement targets the ceiling (design §13 preflight). The §7 library adopts the Anthropic Agent Skills format — see §8.

---

## 4. Tier ladder (Self-modifier context)

Listed for completeness; not relevant until chapters 4–5.

| Tier | Artifact                           | Trigger                                          | Risk class      | Auto-merge?         | Restart on deploy?   |
| ---- | ---------------------------------- | ------------------------------------------------ | --------------- | ------------------- | -------------------- |
| 0    | Knowledge (`skill/knowledge/*.md`) | N ≥ ~5, no prior intervention                    | Pure addition   | Yes (Self-modifier) | No                   |
| 1    | Tool-craft or plan-slot            | ~20+ after tier-0, rate unchanged                | Prompt-shape    | Yes (Self-modifier) | No                   |
| 2    | Routing rule                       | Same persistence after tier-1                    | Decision-shape  | Yes (Self-modifier) | No                   |
| 3    | Code change                        | Same persistence after tier-2 + §6 justification | Behaviour shift | **No, ever**        | Yes — §11 blue/green |

See design §5.6 for the ladder math.

---

## 5. Chapter 1 — Tool

> **Goal:** a working little-coder you drive from CLI. Stop here for a useful tool with no self-learning. Refs: design §3, §4, §10, §12.

### Critical decisions to land before Tool ships

These are unrecoverable later. Get them right in Tool or pay the cost downstream.

- **Open-terminal network change.** open-terminal moves to its own network with explicit egress allowlist: `llama-cpp` for inference, the operator-configured git remote, the private search gateway if web-search is enabled, nothing else. **OWUI's current direct access to open-terminal stops working — that is the intended safety boundary.** OWUI access returns in chapter 2 via `lc-mcpo`, on a controlled path.
- **Journal envelope.** `ts`, `task_id`, `session_id`, `channel`, `user_id`, `repo`, `lang`, `seq`, `schema_version` from line 1. Fields not present on day one cannot be retrofitted onto append-only journals.
- **All four named volumes declared** even though only `little-coder-journals/` actively records in Tool. Missing a declaration means the first rebuild in a later chapter wipes the new state.
- **git-proxy from day one.** Even pure-tool use commits user code. Retrofitting safety constraints onto a system that already has habits is harder than starting with them.
- **Schema versioning** (`schema_version: 1`) on the journal envelope. Forward-compat for later chapters.

### Build list

- llama-cpp reachability verified: `http://llama-cpp:8080/v1`, both `qwen3.6:27b` and `qwen3.6:27b-nothink`.
- Ship the open-terminal network change. Verify isolation from inside.
- Build `little-coder` container image with `agent` (the REPL).
- Build `lc-mcpo` sidecar (built but dormant — activated in chapter 2). Healthcheck on `/openapi.json`.
- Compose healthchecks: `agent` (MCP socket), `lc-mcpo` (`/openapi.json`).
- LLM client default → `qwen3.6:27b-nothink`; reasoning variant available via call-site selection.
- Journal writers for `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl` with the full envelope. Schema-validated at write time; malformed records rejected. Append + fsync on terminal/error records.
- Outcome label per design §4.2: `pass` / `fail` / `unverified`.
- `audit.jsonl` writer from day one (design §4.4): records `project_switched`, `shutdown`, `task_outcome_amended`. Later chapters add more event types.
- Declare all four named volumes; verify `docker compose up -d --build` preserves them.
- Backup job for the volumes (Alpine-cron daily by default; cadence + restore drill tracked).
- Workspace handling: clone a single repo directly into open-terminal. Edit files, run tests.
- `/project repo: <link>` per design §12.3: URL normalization, no-current-focus / matches / doesn't-match branches.
- FIFO queue across triggers (one task at a time; later: OWUI triggers join the same queue).
- CLI operator surface (design §12.6):
  - `lc admin project switch <link>`
  - `lc admin shutdown [--drain-deadline 30m]`
  - `lc admin task confirm <task_id> [pass|fail]` — outcome amendment, design §4.2. 7-day window.
  - `lc admin pending` and `approve` / `reject` stubs (will be wired up in chapter 4)
- Build `git-proxy` per design §3.3. Whitelist + blocklist; sited at workspace edge; no raw-git fallback.
- `.git/config`, `.git/hooks/`, `.git/info/` mounted read-only to agent. Allowed remotes baked in at project-switch time. `core.hooksPath` set outside `.git/`.
- Per-repo deploy tokens per design §10.3: least-privilege, injected per task, never ambient.
- Provision **private** self-improvement git remote (per design §10.6) and fine-grained PAT scoped to `contents:write` on that remote only. (Unused until later chapters, but the credentials chain is set up now.)
- Sanitization filter built per design §10.2 and run in **shadow mode**: redact secrets, structural-digest large bodies, strip PII. Tested against a fixed test set (seeded false-positive + false-negative). Applied to all writes that _would_ be outbound in later chapters; in Tool nothing leaves the stack so the filter just records what it _would_ have done. Rejection rate metric collected for later baseline.
- Prometheus metrics endpoint on `agent` (design §9.3): queue depth, journal write rate, llama-cpp slot occupancy, sanitization rejection rate (shadow-mode counts). Later chapters layer more metrics on this.
- Centralized typed config (YAML + JSON schema) per design §12.8, validated at boot. Near-empty schema in Tool (drain deadline, `task_abandoned` timeouts per channel, basic budget caps). Later chapters add fields.
- SIGTERM drain mode per design §12.7.

### Stop point (chapter 1 → 2)

Tool is "done" when the operator has been using it through the CLI long enough to know whether they want OWUI access. Not a metric — a judgment. Indicators that suggest readiness:

- Day-to-day flow feels stable; you're not finding new bugs in the basic pipeline.
- Journals are accumulating with the full envelope; no fields missing.
- `audit.jsonl` records every project switch and shutdown.
- Volumes survive rebuilds (verified by at least one intentional `docker compose up -d --build`).
- Sanitization rejection rate (shadow mode) has a baseline (open item #5 partially resolved).
- You find yourself wanting to drive little-coder from chat as well as CLI.

The last bullet is the actual trigger. Until you wish for it, don't advance.

---

## 6. Chapter 2 — OWUI pipeline

> **Goal:** OWUI registers little-coder as a tool; chat-shaped triggers and slash-commands work with parity to the CLI. Still no `meta`. Refs: design §12.6.

### Build list

- Register `lc-mcpo` OpenAPI as an OWUI tool. API-key authentication at the edge.
- Task triggers stream conversationally.
- Slash-commands wired to the same control-plane entries as the CLI:
  - `/project repo:` · `/upstream pull` (stub — actual behavior lands in chapter 5) · `/pending` (empty until chapter 4) · `/approve <id>` and `/reject <id>` (no-op until chapter 4) · `/confirm <task_id> pass|fail`
- Privilege separation per design §12.6: operator commands authenticated at the OWUI surface (OWUI's configured auth); MCP server only authenticates task triggers (API key).
- Operator smoke test: drive a task end-to-end via OWUI and verify the journal records `channel = owui`, `user_id = <OWUI user>`, and the task completes against open-terminal with identical effect to CLI invocation.
- **Founding knowledge** — the baseline knowledge layer (§3). Author and bake in `agent-knowledge/environment.md` (operating environment + git-proxy whitelist/blocklist, so the agent stops rediscovering its constraints each task) and `agent-knowledge/engineering-principles.md` (SOLID / encapsulation / naming / patterns / DRY-YAGNI). Wired through `config/little-coder.config.yaml` → `agent.extra_args` → `--append-system-prompt`. Operator-maintained, always-loaded, never meta-touched.

### Stop point (chapter 2 → 3)

OWUI parity confirmed; journals attributing both channels correctly. Indicators that suggest readiness to advance to Observer:

- Multi-channel usage has accumulated enough journal volume that you're curious what patterns the system would see.
- You've started noticing repeated mistake-types yourself (e.g. "it keeps doing X wrong in Rust async") and wonder if `meta` would catch them.

---

## 7. Chapter 3 — Observer

> **Goal:** `meta` reads journals and surfaces clustered patterns to the operator. Nothing written. Pure read-only insight. Refs: design §5, §9.2, §10.1, §13.

### Critical to land in Observer

- Preflight exit criteria per design §13 — Observer's entry is preflight's exit:
  - (a) ≥ K distinct clusters each have ≥ their M window of observed occurrences (K, M from real data).
  - (b) Polyglot baseline variance measured (for chapter 4's gate).
  - (c) Counterfactual + adversarial judge prompt dry-run on real examples + human-rated.
  - Transition journaled to `audit.jsonl`.

### Build list

- Build `meta` process per design §3.2 — separate process, single-flight lock, evidence-triggered (not clock).
- Cluster identity per design §5.1–§5.4: immutable `cluster_id`, mutable label, ingest-time assignment, similarity floor, `unassigned` pool, split/merge lineage with `inherited`/`observed` counts.
- Per-language cohort scoping per design §5.5: `lang` + `task_shape`, aggregated across repos.
- Cohort store as event-sourced projection per design §5.4: derived index, rebuildable from journals.
- Judge prompt drafted and dry-run against accumulated journals (design §10.1 + §1 principles).
- Sanitization filter **promoted from shadow to enforcing** for judge calls (design §10.2): filter failure aborts the call, never "send anyway."
- Observer surface: meta produces _reports_ (clusters, occurrences, candidate gaps in craft) viewable through the operator surface. No artifacts drafted, no merges proposed.
- Drift-trigger metric on sanitization rejection rate (open item #5 resolved here using the Tool-era baseline).

### Stop point (chapter 3 → 4)

Observer reports stabilize and you trust what the system is seeing. Indicators:

- The clusters meta identifies match (or sharpen) your intuition about what little-coder gets wrong.
- Cluster labels are auditable — you can read them and they make sense.
- You find yourself wanting meta to _draft fixes_ for the patterns, not just describe them.

---

## 8. Chapter 4 — Learner

> **Goal:** meta drafts tier-0/1 artifacts; you approve each one. Polyglot validation. Efficacy reversion. No code changes. Refs: design §5.6, §5.7, §7, §8, §12.6.

### Build list

- Skill library directory layout per design §7: `skill/knowledge/*.md`, `skill/tools/*.md`, `skill/plan-slots/*.md`. Each artifact is authored in the **Anthropic Agent Skills format** — a `SKILL.md` body with `name` + `description` frontmatter, progressive disclosure (lean body; link heavier reference material rather than inlining), under ~500 lines, "explain-the-why" drafting — layered with design §7.1's `id` / `cluster_id` / `tier` / `lang` / `domain` metadata. The `description` field feeds the §7.4 augmenter's tag/embedding selection. Frontmatter schema (both metadata sets) enforced at draft time.
- Augmenter selection logic per design §7.4: tag filter → embedding rank → token budget. Cohort-proven + tighter match wins ties.
- Atomic-rename writers per design §7.3 for all watched files.
- Polyglot oracle wrapper per design §8.1: `Oracle` interface, biased subset by cluster domain.
- Baseline + regression margin per design §8.2–§8.3, set from preflight variance (open item #1).
- In-context assertion per design §8.4.
- Efficacy reversion per design §8.5.
- Supersession per design §7.5.
- Operator surface lists pending artifacts with text + cohort evidence + provenance; approve/reject is the merge gate.
- Tier ladder entries:
  - **Tier 0**: knowledge entry triggered at N ≥ ~5 occurrences.
  - **Tier 1**: tool-craft _or_ plan-slot, judge picks within the tier per design §5.7. Plan-slots loaded at planner-process boot from `skill/plan-slots/`; planner watches the file.
- Budget caps per design §12.5: 1 artifact/iteration, judge wall-clock minutes/day, Polyglot exercise-runs/day, journal write rate. Coalesce-per-`cluster_id` on hard limits.
- Resource isolation per design §12.5: `meta` backs off when interactive lanes busy.
- Longitudinal track per design §9.1: cyclomatic complexity, file size, fan-out, churn sampled across repos. Trend miner. Surfaces anomalies (silent clusters) to operator — does not auto-act.

### Stop point (chapter 4 → 5)

Tier-0 and tier-1 artifacts have been merged through the human gate enough times that:

- You trust the drafting quality (artifacts read well and tend to be on-topic).
- Efficacy reversion is working (artifacts that don't help auto-retire without operator intervention).
- The Polyglot gate is catching the regressions you'd expect it to catch.
- You're tired of approving every tier-0 entry by hand and want auto-merge for the well-behaved ones.

---

## 9. Chapter 5 — Self-modifier

> **Goal:** trusted tiers auto-merge; tier-2 routing rules with exploration; tier-3 code changes via blue/green deploy. Endpoint. Refs: design §5.8, §6, §11.

### Build list

- Tier-0 auto-merge, with sampled human-review continuing per design §10.4 control 2.
- Tier-2 routing rules per design §5.8:
  - Staged-freeze gates entry: no routing rule until tier-0 and tier-1 windows have run and resisted.
  - 5–10% random-exploration against each rule indefinitely.
  - Efficacy reversion (design §8.5) retires rules that don't pay off.
- Tier-3 §6 justification gate: judge produces structured written argument; if §6(3) cannot be articulated, the structural change is not justified.
- Tier-3 candidate topology per design §11.1 step 2: paired `candidate-little-coder` + `candidate-open-terminal`; active's volumes mounted read-only; writable tmpfs; same containment as active.
- Active drives the test per design §11.1 step 3; verdict external (Polyglot + §6 + human gate).
- Two acceptance tests per design §11.1 step 4: issue-fixed and no-regression. Stratified Polyglot subset for upstream-merge validation.
- Repro persistence per design §11.1 step 5: persisted to `little-coder-cohorts/repro/<artifact_id>/`.
- PR per design §11.1 step 6: opened on the private remote, templated mechanically, passed through the sanitization filter before posting.
- Human merge + manual `docker compose up -d --build little-coder` per design §11.1 step 7.
- Post-deploy verification per design §11.1 step 8: healthcheck green; smoke task = re-run the persisted §6 repro.
- Rollback: `git revert` + same compose command, or pin to prior tag.

### Stop point

This is the endpoint. Beyond here, the system grows by tier-3 artifacts, not new chapters.

---

## 10. Locked decisions

Settled in the design doc. Reproduced for plan independence.

| #   | Decision                                                                                                                                                                        | Design ref                |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| 1   | Models: `qwen3.6:27b` (reasoning) + `qwen3.6:27b-nothink` (fast) on `http://llama-cpp:8080/v1`. No other backends.                                                              | §3.5                      |
| 2   | Session model: one focused project cloned directly into open-terminal; one task at a time; FIFO across triggers; tier-3 escalates to ephemeral container.                       | §3.4, §11.1, §12.3, §12.4 |
| 3   | Upstream pulls are operator-initiated via `/upstream pull`. Tiers 0–2 land cleanly; tier-3 conflicts → `upstream-merge/<date>` branch.                                          | §12.2                     |
| 4   | Deploy actor: operator, via PR + manual `docker compose up -d --build`. Tier-3 only.                                                                                            | §11.1, §11.3              |
| 5   | Cohort schema: envelope per §4, identity + split/merge lineage per §5.                                                                                                          | §4, §5                    |
| 6   | Artifact-type selection: tier ladder controls risk class; judge picks type within tier.                                                                                         | §5.7                      |
| 7   | Routing-rule exploration: staged-freeze + 5–10% random-exploration indefinitely.                                                                                                | §5.8                      |
| 8   | Skill frontmatter schema fixed; augmenter selects on tag + embedding + token budget.                                                                                            | §7.1, §7.4                |
| 9   | Judge model: in-stack `qwen3.6:27b` under adversarial framing. External judge deferred, not rejected.                                                                           | §10.1                     |
| 10  | Cohort scoping: per `lang` + `task_shape`, aggregated across repos.                                                                                                             | §5.5                      |
| 11  | Open-terminal network change is a **chapter 1 (Tool) requirement**, not a later hardening. OWUI's direct access to open-terminal ends; chapter 2 restores access via `lc-mcpo`. | §3.4                      |
| 12  | Sanitization filter built in Tool, run in shadow mode; promoted to enforcing in Observer.                                                                                       | §10.2                     |
| 13  | Four expertise volumes declared in Tool, even though only `little-coder-journals/` actively records before Observer. A fifth volume, `little-coder-workspace/`, is shared with `open-terminal` (project-scoped). | §3.6                      |
| 14  | Upstream little-coder is a Node.js CLI on the `pi` framework, not Python. The agent container is Node-based; the control-plane wrapper is Python, mirroring `search-mcpo`. The `agent.py` reference in design §6 is a Chapter-5 illustration only. | §3.1                      |
| 15  | Agent reaches the workspace via a shared `little-coder-workspace` volume: it edits files directly, and routes build/test/git execution to `open-terminal`'s `POST /execute` REST API — execution stays in the network-isolated plane. | §1.5, §3.4                |
| 16  | Two knowledge layers: **founding knowledge** (operator-authored baseline, always-loaded via `--append-system-prompt`, in `agent-knowledge/`) is distinct from the **§7 skill library** (meta-learned, discovered on demand, in `little-coder-skill/`). The §7 library adopts the Anthropic Agent Skills format (`SKILL.md` + `name`/`description` frontmatter, progressive disclosure) layered with §7.1 metadata. | §3.1, §7, §7.4, §13       |

---

## 11. Open items (preflight-tuned or deferred)

| #   | Open item                                      | Resolves where                                                         | Blocks chapter                         |
| --- | ---------------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------- |
| 1   | Polyglot N + regression margin                 | Preflight variance (collected during Tool, OWUI, computed at Observer) | Learner                                |
| 2   | Counterfactual judge prompt wording + few-shot | Observer dry-run                                                       | Observer (entry to Learner)            |
| 3   | `task_abandoned` timeout per channel           | Real usage in Tool/OWUI                                                | Tool (default usable; tune at Learner) |
| 4   | Neutral test-runner for design §11.1 step 3    | Later hardening                                                        | Not blocking (Self-modifier)           |
| 5   | Sanitization audit drift threshold             | Tool-era shadow-mode baseline + envelope                               | Observer (filter goes enforcing)       |
| 6   | Reserved-slot promotion threshold (`meta` GPU) | Steady-state load observation                                          | Learner+ (only if starvation observed) |
| 7   | Backup cadence + restore drill                 | Decided alongside volumes in Tool; drill before Learner                | Learner                                |
| 8   | `.git/config` flexibility upgrade              | Deferred; only if real need arises                                     | None today                             |
| 9   | `.git/config` read-only **enforcement** for the agent + `core.hooksPath` | Tool hardening — known gap, see tasks doc | Before hostile-repo workload            |

---

## 12. Cross-cutting concerns

Apply throughout all chapters — they are the design doc's §1 principles applied to operations.

- **Append-only journals.** Schema fields are unrecoverable retroactively. Ship `session_id` / `channel` / `user_id` / `schema_version` on day one of Tool.
- **Named volumes are the persistence boundary.** All four declared in Tool; the container is ephemeral.
- **Sanitization runs on every outbound.** Shadow mode in Tool; enforcing from Observer onward. Filter failure aborts the call.
- **Nothing fails open.** Defer + alarm + journal; never silent pass.
- **Evidence-triggered, not time-based.** No fixed-cadence audits anywhere.
- **Trust boundary.** Operator trusted; agent untrusted; user repos under task triggers actively hostile by default.
- **Schema versioning everywhere.** Readers tolerate older shapes; migrations are explicit operator-run jobs. **Tier-3 self-changes cannot propose schema changes** (design §12.9).

---

## 13. How to use this plan

- **Plan is sequencing; [tasks](integration-tasks.md) is execution.** Every chapter has a corresponding tasks block.
- **One chapter at a time.** The build agent stops at the end of a chapter; the operator advances when ready.
- **Stop points are judgment calls, not metrics.** Each chapter ends with indicators that suggest readiness, not thresholds.
- **Update both docs together.** Decisions go to the tasks Decision Log first, then to this plan's lock table if permanent.
- **Design doc wins on conflict.** If a chapter here implies something the design doc forbids, fix the plan.
