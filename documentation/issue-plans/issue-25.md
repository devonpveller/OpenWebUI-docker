---
issue: 25
title: Queue-ETA notifications: tell the user when a long job is queued and when to expect it
created: 2026-08-23T13:26:39+00:00
base_sha: a1322f5245f8340ca1b56374cdce00b455a2f4ef
target_branch: development
status: executing
triage: bounded
verdict: fix
repro: confirmed-in-code
touches_live: false
touched_paths: scripts/checks/queue-eta-notify.ps1, documentation/runbooks/queue-eta-notifications.md, CLEANUP-PLAN.md
---

# Plan: Queue-ETA notifications: tell the user when a long job is queued and when to expect it

## Problem

Every report claim re-derives cleanly from the tree at `a1322f5`:

- **Jobs wait silently.** llm-queue is hold-and-dispatch (`llm-queue/src/llm_queue/main.py:47`): `enqueue()` heaps the waiter and `await_dispatch()` blocks the caller's HTTP request until a slot frees (`llm-queue/src/llm_queue/scheduler.py:129-181`). The only side-channel is the analytics `EventSink` — structured logs plus optional SQLite, explicitly best-effort and consumed by nothing user-facing (`llm-queue/src/llm_queue/events.py:9-11,69-87`). No notification path exists anywhere in `llm-queue/`.
- **Both named long-job callers route through the queue.** LiteLLM forwards chat through llm-queue by `api_base` (`inference/docker-compose.yml:274-275`); research runs on its own virtual-key lane and Open Notebook podcasts call the gateway via `OPEN_NOTEBOOK_LLM_API_KEY` (CLEANUP-PLAN.md K.10 notes, lines 1103-1112).
- **`/observe/queue/estimate` exists and returns projected waits** (`llm-queue/src/llm_queue/routes/observe.py:34-38` → `routes/control.py:60-79` → `scheduler.py:114-125`, returning `projected_wait_s`, `avg_T_s`, `acceptable_wait_s`). Better still for this feature, the full board `GET /observe/queue` already carries per-waiting-request `key`, `waited_s`, and `est_wait_s` plus per-model `avg_T_s` (`scheduler.py:272-303`) — enough to compute est-start and est-completion timestamps per caller lane. The GET-only `/observe/*` namespace is bridged to llm-net via LiteLLM pass-through (`config/litellm.config.yaml:112-120`).
- **The research async OWUI callback exists** (`owui/tools/deep_research.py:123-159`, `callback_armed` hand-off) but fires only at completion — no interim "queued" signal.
- **The K.10 PLANNED item is real at the pinned base**: "queue-ETA notifications to the user (MM/OWUI) for long-waiting jobs" (CLEANUP-PLAN.md:1128-1132, operator idea 2026-08-22).

One architectural fact shapes the fix: the inference plane deliberately has **no outbound route and no host port** — `llm-gateway` sits only on internal networks, and operator access is documented as `docker exec` (`inference/docker-compose.yml:248-256`). So the queue must not post to Mattermost itself; the notifier belongs host-side, reading the queue's read-only board.

## Approach

1. **New host-side notifier `scripts/checks/queue-eta-notify.ps1`** (PowerShell 5.1, ASCII no-BOM, per workspace shell convention). No container is added, changed, or restarted — SERVICE-LIFECYCLE's container checklist is not triggered; the script follows the existing `scripts/checks/` watchdog shape (`-Mode check|daemon|install-task`, mirroring `stack-watchdog.ps1`).
2. **Poll** `docker exec llm-queue curl -fsS http://localhost:8080/observe/queue` (curl exists in that image — its own healthcheck uses it, `inference/docker-compose.yml:212`). This is the documented read-only operator path; the mutating `/queue/{id}/*` verbs are never touched (invariant at `llm-queue/src/llm_queue/routes/control.py:8-11`).
3. **Detect + aggregate**: flag waiting rows where `waited_s + est_wait_s >= threshold` (param, default 120s). Aggregate per (caller `key`, model) — research fan-out enqueues many requests at once and must produce ONE message, not one per request: "⏳ `ob-research`: 7 requests queued on `qwen36-27b` since 14:02 — longest est start ~14:06, est completion ~14:08 (`est_wait_s + avg_T_s`)." Wording stays honest: these are per-request ETAs from the queue; whole-job ETA is only knowable by the research service (see step 6).
4. **Dedup + throttle**: state JSON under `logs/` (already the watchdog's gitignored home) records notified request ids and a per-lane cooldown (default 10 min) so a held burst pings once. `-DryRun` prints the would-post message; `-SnapshotFile <json>` feeds a fixture board instead of the live exec, so the threshold/aggregate/dedup/format logic is testable without load.
5. **Post to Mattermost** exactly like `scripts/notify-mattermost.sh:14,42-51`: bot token read at runtime from `agent-org/docker/.env` (never committed), `POST localhost:8065/api/v4/posts`, channel id a parameter (operator confirms the target channel before install — see interlocks). Best-effort: a down Mattermost must never fail the check.
6. **Docs + bookkeeping**: short runbook `documentation/runbooks/queue-eta-notifications.md` (cadence, threshold/cooldown params, scheduled-task name, how to silence); tick the K.10 PLANNED item in CLEANUP-PLAN.md. **Explicit non-goals, filed as follow-ups**: (a) the OWUI-callback lane — an interim "queued, est start HH:MM" status from the research service — lives in the OB1 submodule and requires an OB1 PR + gitlink bump-via-PR, out of this repo-scoped fix; (b) the "MM alert when the ON audio job fails" is a separate K.10 planned item, not this issue.

## Validation (evidence required before merge)

**Evidence assignment (gate-plan adjustment, 2026-08-23):** the sandboxed
worker's deliverable is the script + fixture JSONs + runbook + CLEANUP-PLAN
tick **only**. The worker's environment is a Linux container isolated from
the live stack: it has no Windows PowerShell 5.1, no host Docker socket, and
no Mattermost — it must NOT attempt `powershell`, `docker exec`, or any
Mattermost call, and must NOT claim any evidence tier below as done. ALL
three tiers (RED, logic, GREEN) plus the pre-commit ps1 hooks execute on the
**HOST harness** (the Claude session / operator), which attaches the
transcripts to the PR.

**Worker-executable check (the worker's own loop):** ship the fixtures the
host tiers will consume — `scripts/checks/fixtures/queue-eta-busy.json`
(a busy board: `waiting[]` entries each with `key`, `waited_s`, `est_wait_s`,
plus per-model `avg_T_s`, matching `snapshot()` in
`llm-queue/src/llm_queue/scheduler.py:272-303`) and
`.../queue-eta-idle.json` (empty `waiting`). Validate each in the sandbox
with `python3 -c "import json; d=json.load(open(...)); assert
isinstance(d['waiting'], list)"` plus asserts for the named keys — that run
IS the worker's evidence, included in the PR description.

Host-harness tiers (attached by the host, not the worker):
- **RED (today's behavior)**: `docker exec -i llm-queue python - http://localhost:8080 < llm-queue/scripts/priority_demo.py`, then `docker exec llm-queue curl -s http://localhost:8080/observe/queue` showing `waiting` non-empty while no message of any kind arrives in Mattermost (nothing exists to send one).
- **Logic (no live load)**: `powershell -File scripts/checks/queue-eta-notify.ps1 -Mode check -SnapshotFile scripts/checks/fixtures/queue-eta-busy.json -DryRun` renders exactly one aggregated message per (caller, model) with timestamp, est start, est completion; a re-run emits nothing (dedup); the idle fixture emits nothing.
- **GREEN (live)**: the priority_demo burst with the notifier on a 60s cadence → exactly one MM message per caller lane with plausible ETAs; no duplicate on the next poll; `/observe/queue` before/after confirms read-only.
- Pre-commit hooks pass (line-endings, ps1 structural check, secret guard — the token is runtime-read, never in the diff). Note: the new script's mode set must match its own runbook; the existing watchdog's ValidateSet at base is `check|daemon|install-service` (`scripts/checks/stack-watchdog.ps1:7`) — name this script's modes explicitly in both files.

## Risks / interlocks

- **Operator approvals needed in the MM thread before execution**: (1) which Mattermost channel receives these pings (a user-facing channel, not necessarily #claude-code); (2) installing/enabling the scheduled task or daemon — a host state change; (3) threshold/cooldown defaults (too low = noise on routine research fan-out).
- **Validation load occupies the GPU briefly** (priority_demo fires real completions through the gateway). Coordinate timing in MM; do not run while the operator is actively using the stack or running little-coder test triggers.
- **Posture guard**: the notifier must stay host-side. Do NOT add Mattermost egress or any outbound network to `llm-queue`/`llm-gateway` (supply-chain posture, `inference/docker-compose.yml:248-256`), and never bridge the mutating `/queue/{id}/*` verbs anywhere.
- **No container restart/rebuild/redeploy** is involved; `touches_live` is false. The J.1 virtual-key regime is unaffected — the `docker exec` path hits the queue directly and needs no key.
- **Honesty boundary**: the queue can only give per-request ETAs. If the operator wants whole-job ("your research run will finish ~HH:MM") notifications, that is the OB1 research-service follow-up (submodule, bump-via-PR) and should be filed as its own issue.
