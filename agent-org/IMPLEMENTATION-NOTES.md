# agent-org — Implementation Notes (what's built / what's operator-gated)

**Date:** 2026-07-01. This is the authoritative record of the v1 build of the
[teams-chat-agent-orchestration](../documentation/implementation-guide/teams-chat-agent-orchestration/)
design corpus. It maps every TASKS item to its landing site and status.

**Status legend:** ✅ built + tested here · 🧩 built, needs a live stack to exercise (no infra
in this environment) · 🚀 operator step (deploy/exposure/secrets) · 🚩 decision-gate (needs an
operator decision).

> **What "fully implemented" means here.** Everything that is *code / config / docs* is built,
> wired, and — where it can run without a live GPU/Mattermost/Docker stack — covered by
> deterministic tests (55 passing). The remaining items are inherently operator-only: bringing
> up containers, creating a Mattermost bot token, running the capability-floor test against the
> live GPU, deciding OpenRouter spend, and tailnet exposure. Those are authored + runnable, and
> marked 🚀/🚩 below. The build makes them a switch-flip, not new work.

---

## Phase status

### P0 — Platform spike
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P0.0 prerequisites | ✅ | Local `llm-gateway` consumed as-is (`AO_LOCAL_API_BASE=http://llama-cpp:8080/v1`); nothing built on it. Stack-map reconciled (this build's 3-place change). |
| P0.1 compose scaffold | ✅ | [`docker/docker-compose.yml`](docker/docker-compose.yml). `docker compose config` validates (default + all profiles). |
| P0.2 Mattermost bot | 🚀 | One-time operator step — [README "P0.2"](README.md). Token via env (`AO_MATTERMOST_BOT_TOKEN`), never a file. |
| P0.3 bridge skeleton | ✅ | [`agent-bridge/app/`](agent-bridge/app/) — FastAPI + `/health` + WS consumer + Postgres state + all §3.1.1 modules present (not stubs). |
| P0.3b GBNF structured call | 🧩 | `model_router.OpenAICompatClient` uses Instructor + `extra_body.json_schema` (llama.cpp GBNF) via the gateway. The 20/20 zero-repair assertion needs the live model (no GPU here); the mechanism + Fake path are tested. |
| P0.4 echo test | ✅ | `test_end_to_end.py::test_wake_on_mention_posts_reply` (fake adapter): @mention → bridge → threaded reply. |
| **P0.5 capability-floor** | 🚩 | **Decision-gate — see "P0.5 procedure" below.** Decides whether judge profiles stay `local` or flip `cloud`. Profiles ship `local` (fail-safe pre-decision). |

### Pc — Cloud lane (CONDITIONAL)
| Task | Status | Landing site / note |
|------|--------|---------------------|
| Pc.0 spend ceiling | 🚩🚀 | Operator confirms the OpenRouter budget before Pc.1. |
| Pc.1 `llm-gateway-cloud` + `ao-egress` | 🧩🚀 | Compose `profile: cloud` + [`config/litellm-cloud.config.yaml`](config/litellm-cloud.config.yaml). Master_key + budgets + OpenRouter models + allowlisted egress. Operator provisions per-role virtual keys/budgets at bring-up. |
| Pc.2 governance-summary egress | ✅(logic) 🧩(live) | `AuditSink` logs an egress payload for cloud calls; the privacy boundary (summaries only, no raw code) is a bridge responsibility — the cloud call sites send claim/goal/deviation/options, never file contents. |
| Pc.3 profiles registry | ✅ | [`agent-bridge/profiles/`](agent-bridge/profiles/) + `modules/profiles.py`; lane-flip via `POST /profiles/lane` (no code change). |
| Pc.4 join analytics by lane | 🚀 | Two spend DBs (`llm-gateway-db` local + `llm-gateway-cloud-db`); union with a `lane` tag later (documented, no live merge). |

### P1 — Wake mechanic
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P1.0 reliable event delivery | ✅ | `modules/event_gateway.py`: idempotent dedupe on `event_id` + reconnect REST catch-up via `ChannelCursor`. `test_event_gateway.py`. |
| P1.1 channel↔effort↔session map | ✅ | `modules/router.py` + `SessionMap` (session id == thread id). |
| P1.2 wake/resume | ✅ | `router.wake` → `scheduler.acquire` → `harness.wake` (little-coder `POST /tasks {session_id}`). `test_end_to_end`. |
| P1.3 A→B hand-off | ✅ | `orchestrator.handle_event` on an effort channel → wake target; reply posts in-thread. |
| P1.4 bus-only enforcement | 🧩 | Structural: workers only reach the bridge (compose `ao-worker-net` internal + `ao-git-egress` allowlist). The egress denial is a container-level property (needs the live worker profile). |

### P2 — Escalation gate (CORE SAFETY)
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P2.1 gate FSM {active⇄frozen} | ✅ | `modules/governance_gate.py`, persisted; **restart keeps frozen** — `test_governance_gate::test_frozen_persists_across_restart`. |
| P2.2 triggers → freeze | ✅ | All 9 triggers → freeze — `test_every_trigger_freezes` (parametrized over `Trigger`). |
| P2.3 CONCERN + dependents | ✅ | `orchestrator.raise_concern` posts the UX-FLOW §3 schema to `#mgmt`; `gate.freeze` freezes dependents (`test_dependents_freeze_with_parent`). |
| P2.4 operator decision | ✅ | `gate.clear` parses approve/modify/abort → propagate/unfreeze + audit. |
| P2.5 fail-safe default | ✅ | No auto-resume (no clock/timer in the gate), no reroute verb (`test_no_reroute_verb_exists`), PO can't self-clear hard-gate (`test_hard_gate_cannot_be_cleared_by_po`). |
| P2.6 global kill switch | ✅ | `gate.kill_switch` — `test_kill_switch_freezes_everything` + `test_kill_switch_from_mgmt`. |
| P2.7 safety tests | ✅ | `tests/test_governance_gate.py` (13 cases). |

### P3 — Charters + grounding
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P3.1 charters as skills | ✅ | [`.claude/skills/agent-org-{floor,worker,reviewer}/`](../.claude/skills/) + `agent-bridge/charters/*.md`. |
| P3.2 floor/steering split | ✅ | `modules/charters.py`; steering mutable per-effort, floor immutable at runtime. |
| P3.3 hooks enforce floor | ✅ | `modules/floor_guard.py` (classify + decision) + `hooks/pretooluse_floor.py` (fail-closed CLI) + `POST /hook/floor-check`. `test_scope_and_floor`. |
| P3.4 rule/goal version store | ✅ | `RuleVersion`/`GoalVersion`; floor change requires `approved_by="human"` (`test_floor_change_requires_human`). |
| P3.5 goal injection (constraints inline) | ✅ | `charters.build_context` — `test_goal_constraints_inline_in_context`. |
| P3.6 re-ground in flight | ✅ | `charters.set_goal(invalidates_in_progress=...)`; the invalidating case is a §3 freeze at the call site. |
| P3.7 cost-tiered supervision | ✅(logic) | `orchestrator.monitor_sampled` — sampled LLM monitor (never per-token, never health-probe); cheap-continuous = hooks/bus-logging/caps always on. |
| P3.8 readiness gate + clarify-loop | ✅(logic) 🧩(live) | `modules/planner.py::readiness_gate` (structured `ReadinessVerdict`; cloud lane if Pc). |
| P3.9 plan presentation + approval | ✅ | `planner.draft_plan` + `approve_plan` (human-only) + `Effort.plan_status`. |

### P4 — Plan-stop-gates + review
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P4.0 ground + dry-run (risk-gated) | 🧩 | Policy encoded (risk gate mirrors P4.5); grounding via `openbrain-research` + isolated dry-run is a worker-execution step (needs the worker profile + OB1). |
| P4.1 plan-doc checkpoints (separate floor doc) | ✅ | [`floor/stop-gate-enforcement.md`](agent-bridge/floor/stop-gate-enforcement.md); enforced halt is the `Checkpoint` row, independent of plan markers. |
| P4.2 block past checkpoint | ✅ | `stop_gates.may_proceed`/`assert_may_proceed` — `test_checkpoint_blocks_until_cleared`. |
| P4.3 explain-intent | ✅ | `stop_gates.submit_explanation` (4-field `Explanation`). |
| P4.3b verify vs diff | ✅ | `submit_explanation` cross-checks via judge model — `test_explanation_mismatch_flagged`. |
| P4.4 differently-goaled reviewer | ✅ | `stop_gates.assert_differently_goaled` rejects same-goal; reviewers route to PM, never self-approve — `test_same_goal_reviewer_rejected`. |
| P4.5 risk-gated depth | ✅ | `stop_gates.lenses_for` (routine=1, irreversible=panel) — `test_risk_gates_lens_count`. |
| P4.6 aggregate → re-ground → refactor | ✅ | `clear_checkpoint` keeps a flagged checkpoint blocking — `test_flagged_review_keeps_checkpoint_blocked`. |
| P4.7 reviewers on JUDGE_MODEL + deterministic checks | ✅ | Reviewer profiles bind the judge lane; a failed deterministic check is an LLM-independent flag — `test_deterministic_check_failure_flags`. |
| P4.8 lateral concern channel | ✅(logic) | Lateral concerns surface on the bus → PM; `WakeLog.kind="brake"` is exempt from the rate cap (`router.wake_storm_tripped` counts `kind="work"` only). |

### P5 — Dynamic roles + worker pool + scope
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P5.0 worker pool + scheduler FSM (machine B) | ✅(logic) 🧩(pool) | `modules/scheduler.py` {computing,waiting,suspended} + static `MAX_CONCURRENT_WORKERS` semaphore — `test_scheduler.py`. The live 2-instance pool is compose `profile: workers`. |
| P5.1 scope ledger | ✅ | `modules/scope_ledger.py` — self-grant denied, PM grant recorded — `test_scope_and_floor`. |
| P5.2 role authority split | ✅ | `modules/roles.py` — PM instantiates approved roles; a new role TYPE routes through the §3 gate for human sign-off. |
| P5.3 approved-role catalog | ✅ | `scope_ledger.catalog_add`/`is_role_approved` — `test_role_catalog_approval`. |
| P5.4 last-owner provenance | ✅(logic) 🧩(live) | `router.last_owner` (git-blame v1). Needs a real workspace clone to exercise. |
| P5.5 channel taxonomy | ✅ | `router.ensure_effort_channel` creates `#effort-<name>`; `#mgmt`/`#incidents`/`#suggestions` per the design. |
| P5.6 wake-storm cap (brake exempt) | ✅ | `router.wake_storm_tripped` (work chatter only) → §3 trigger in `orchestrator.handle_event`. |
| P5.7 stream-aligned, right-sized scoping | ✅(policy) | Encoded in the PM/worker charters + goal-injection; a cognitive-load heuristic hook is a v1.5 refinement (documented). |
| P5.8 retirement/decommission | ✅ | `scheduler.retire` + `scope_ledger.revoke_subject` + `retire_role` — `test_retire_leaves_no_assignment` / `test_revoke_leaves_no_authority`. |

### P6 — Audit + learning loop
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P6.1 full event log | ✅ | `modules/audit_sink.py` (append-only `Event`); `replay()` reconstructs the timeline with versions. |
| P6.2 mirror to Open Brain | ✅(logic) 🚀(wire) | `audit_sink._mirror` → `openbrain-gateway /capture_thought` (best-effort). Off by default; operator sets `AO_OPENBRAIN_MIRROR_ENABLED` + key. |
| P6.3 suggestion pool | ✅ | `modules/learning_loop.py::add_suggestion`/`pool` — `test_suggestion_pool`. |
| P6.4 pattern surfacing | ✅ | `learning_loop.observe` surfaces at ≥2 efforts — `test_pattern_surfaces_across_two_efforts`. |
| P6.5 propose-not-dispose | ✅ | `propose` (PM) / `dispose` (human-only); no auto-apply path to the floor — `test_propose_not_dispose`. |

### R — 3-place change
| Task | Status | Landing site / note |
|------|--------|---------------------|
| R.1 compose | ✅ | [`docker/docker-compose.yml`](docker/docker-compose.yml) — validates with all v1 services. |
| R.2 recovery scripts | ✅ | `scripts/emergency-recovery.ps1` (parses clean) + `.bat` — agent-org added to inventory + shutdown-first/startup-last sequences + status report. |
| R.3 stack-map reference | ✅ | `.claude/skills/stack-map/references/workspace-stacks.md` §3 (new agent-org section) + dependency order + recovery notes. |

### P7 — Mobile + hardening
| Task | Status | Landing site / note |
|------|--------|---------------------|
| P7.1 mobile flow | 🚀 | [`docs/P7-mobile-and-exposure.md`](docs/P7-mobile-and-exposure.md). Decide CONCERNs + kill switch from the phone via `#mgmt` commands. |
| P7.3 CONCERN UX | ✅(plain) | Structured plain posts (OD-5) implemented; the interactive plugin is the optional upgrade. |
| P7.4 tailnet exposure | 🚀 | `tailscale serve` recipe in the P7 doc; no public exposure; non-E2EE agent channels. |

---

## Live bring-up fixes (2026-07-01, during operator P0.2)

First real Mattermost connect surfaced four issues, all now fixed + covered by tests:

1. **Bot needs TEAM membership, not just channel** — `/users/me/teams` was empty. The adapter
   now resolves the team **lazily + retries** (`_ensure_team`), so it self-heals once the
   operator adds the bot to a team; a clear actionable error replaces the old `AssertionError`.
   *(Operator step: add the bot account to the team, not only the #mgmt channel.)*
2. **`#mgmt` display-name vs URL-slug** — the channel shows as `mgmt` but its slug was
   `management`, so slug lookup 404'd and auto-create 400'd. `ensure_channel` now falls back to
   **matching by display name** among the bot's channels before creating.
3. **`ChannelCursor.last_ts` overflow** — Mattermost `create_at` is a **ms epoch** (~1.78e12),
   but the column was `Integer` (int32) → `asyncpg DataError: out of int32 range` on every
   `_mark_processed`, which rolled back the dispatch txn (nothing marked) and silently killed
   catch-up. Fixed to **`BigInteger`**. (Live DB altered; model fixed for fresh deploys.)
4. **catch-up robustness** — added per-event try/except + logging (mirrors the live WS loop) and
   made `run()` survive a catch-up failure, so a single bad event can never abort recovery or
   block the live WS loop. Also `posts_since(0)` now fetches the recent page (MM ignores `since=0`).

Also added the **operator chat command surface** (P0.4 operability): `/help`, `/effort <name>`,
`/status`, `/kill|/unkill`, `approve|modify|abort`, and a **boot-ack** post
(`✅ agent-bridge online`). Verified live: connected as `@bot-pm`, `#mgmt` resolved, boot-ack
posted, live WS loop running, zero errors.

**Natural-language PO surface (added 2026-07-01 — the primary UX).** Slash commands are the
*deterministic control surface*; the primary interface is **plain-language conversation with the
PO** (UX-FLOW: the human converses with the PO). `orchestrator.nl_intake` routes any
non-command `#mgmt` message to the **`po` profile** (local `qwen36-27b`), which returns a
structured `OperatorIntent` (kind + conversational reply + optional action). The bridge executes
**non-destructive** actions from NL — open an effort (Stage 0→1), apply steering (§4.3), report
status — and **replies conversationally**. **Safety decisions are NOT auto-run from fuzzy NL**:
the PO interprets "yeah go ahead" but asks the operator to confirm with the explicit
`approve <effort>` command (governance §3 — decisions stay crisp + auditable). System posts
(joins/adds) are ignored. Its quality rides the PO profile's lane — see P0.5.

**P0.5 quick smoke (2026-07-01, preliminary):** `qwen36-27b` via the local gateway scored
instruction **13/13**, structured-output (GBNF, first-try, zero repair) **4/4**, coordination
(constraint-preservation **2/2** + drift-catch **1/1**) → **LOCAL_JUDGE_OK** on the indicative
thresholds. Strong signal that the org can run **all-local (skip Pc)** — confirm with the full
run (`docker exec agent-bridge python -m app.evals.capability_floor`). This also validates that
constrained decoding works through the gateway (P0.3b) and that the NL PO layer is viable locally.

## P0.5 procedure (the one decision-gate that blocks Pc)

Run these **bounded real completions** (never a model health-probe — C5) against the live
`llm-gateway` and record pass/fail:

1. **Instruction/charter-following** — feed the PM charter + a task; check it holds the §3
   duties (freezes on a planted trigger, up-levels, doesn't self-clear).
2. **Structured-output reliability** — 20 constrained `ReviewVerdict`/`Plan` calls via the
   `model_router` (GBNF); require 20/20 schema-valid with zero repair.
3. **Coordination** — a 2-step A→B hand-off with a constraint that must survive the seam;
   check the constraint isn't dropped (the paper's GPT-5-MINI failure).

**Decision (binary — OD-10):**
- **27B judge OK** → keep judgment profiles `lane: local`. **Pc is skipped.** All-local.
- **27B judge too weak** → build **Pc**, then `POST /profiles/lane {name, lane:"cloud"}` for
  `pm`, `po`, `planner`, `reviewer-*`. **Workers always stay local.** If Pc isn't wired yet, a
  `cloud`-lane call falls back to local *with a warning* and the Human Operator carries more —
  never a silently-trusted weak monitor (governance §2.1).

Record the outcome + the per-task "org vs. single agent?" guidance in this file when run.

---

## Deliberate v1 scope calls (logged, not silent — governance §5)

- **Worker pool is 2 instances behind `profile: workers`**, off by default. The pool wiring
  (per-instance little-coder config, session dirs) needs a live bring-up to validate; the
  scheduler + assignment logic are fully tested with fakes. The GPU is the org-size budget.
- **Cloud lane is fully authored but OFF** (`profile: cloud`, `AO_CLOUD_ENABLED=false`) pending
  the P0.5 decision — the honest pre-decision posture (all-local, same model, zero swap thrash).
- **Open Brain audit mirror is best-effort + off by default** — the local append-only log is
  always the source of truth; the mirror is durable provenance, not a dependency.
- **CONCERN UX is plain structured posts** (OD-5), not yet a Mattermost plugin (P7.3 upgrade).
- **Provenance is git-blame v1** (OD-4); the ownership-ledger is the v1.5 upgrade.
- **Cognitive-load split heuristic (P5.7)** is policy-in-charter for v1; an automated
  files-touched/scope-breadth trigger is a v1.5 refinement.

These are the "no silent caps" disclosures: where v1 bounds coverage, it's stated here.
