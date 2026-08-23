---
issue: 24
title: Podcast pipeline: Mattermost alert when the ON audio job fails
created: 2026-08-23T13:26:38+00:00
base_sha: a1322f5245f8340ca1b56374cdce00b455a2f4ef
target_branch: development
status: executing
triage: bounded
verdict: fix
repro: confirmed-in-code
touches_live: true
touched_paths: OB1/recipes/daily-digest/src/podcast/mm-alert.ts, OB1/recipes/daily-digest/link-enrich.ts, OB1/docker/docker-compose.scheduled.yml, OB1, CLEANUP-PLAN.md
---

# Plan: Podcast pipeline: Mattermost alert when the ON audio job fails

## Problem

Every report claim re-derived at base `a1322f5` (OB1 gitlink unchanged since then; submodule tree clean):

- **Silent failure confirmed in code.** `OB1/recipes/daily-digest/link-enrich.ts:824-825` — when `onClient.waitForJob(jobId, …)` returns anything but `"completed"`, `generateAudio()` only does `console.log(...); return null`. The catch at `link-enrich.ts:829-831` (which also swallows `waitForJob`'s deadline throw, `src/podcast/on-client.ts:93`, and `generate()` failures such as the 401s) is likewise `console.warn` + `return null`. The chain then proceeds: `link-enrich.ts:419-422` builds null URLs, and `openbrain-podcast`'s `NEXT_TRIGGER_URL` contract ("success OR failure, trigger the digest", `OB1/docker/docker-compose.scheduled.yml:164-167`) ships the email without audio. Nobody is told.
- **The motivating incident is real and documented**: `CLEANUP-PLAN.md:1103-1112` (K.10) — ON's stored provider credentials 401'd every `generate_podcast` outline call after the J.1 master-key flip; jobs ended `'failed'` silently.
- **This is the K.10 PLANNED item verbatim**: `CLEANUP-PLAN.md:1128-1130` — "MM alert when the ON audio job fails (no more silent email-only nights)".
- **Delivery path exists.** The report's cited `mm_post.py` pattern (`scripts/sysadmin-mcp/mm_post.py`) is host-side Python and not directly usable from the Deno container — but the repo has an exact in-container precedent: `openbrain-idea-refinery` posts to Mattermost from a Deno container via bot token + `api/v4` (`OB1/integrations/openbrain-idea-refinery/index.ts:322-358`; env wiring `OB1/docker/docker-compose.scheduled.yml:244-251`). Reachability: `mattermost` sits on `ai-stack_llm-net` (`agent-org/docker/docker-compose.yml:75`) and `openbrain-podcast` is on `llm-net` too (`docker-compose.scheduled.yml:212-215`), so `http://mattermost:8065` resolves by name — no `extra_hosts` needed.

All work lands in the **OB1 submodule** (both `link-enrich.ts` and the compose file live under `OB1/`), so the parent-repo deliverable is a gitlink bump via PR per CLAUDE.md.

## Approach

1. **OB1 work branch** off the pinned branch (`feature/integrated-knowledge-system`): add `OB1/recipes/daily-digest/src/podcast/mm-alert.ts` — a small fail-soft `mmAlert(text): Promise<boolean>` mirroring the idea-refinery pattern (resolve team → channel by name → `POST /api/v4/posts`). Contract mirrors `mm_post.py`: any missing env → disabled with one log line; any error → `console.warn`, **never throws** — a down Mattermost must never break the email chain.
2. **Wire it into `generateAudio()`** (`link-enrich.ts:814-833`): hoist `jobId` above the `try` so both paths can report it, then alert on (a) the `status !== "completed"` branch — message carries episode name, ON job id, and status; (b) the catch — episode name, job id if obtained, and the error string (covers the 2026-08-22 401 shape and `waitForJob` deadline throws). Keep the existing console lines and `return null` behavior unchanged.
3. **Compose env** on `openbrain-podcast` (`OB1/docker/docker-compose.scheduled.yml:154`): `MM_ALERT_URL: ${PODCAST_MM_URL:-http://mattermost:8065}`, `MM_ALERT_TOKEN: ${PODCAST_MM_TOKEN:-}`, `MM_ALERT_CHANNEL: "sysadmin"` — same shape as idea-refinery's block, with a comment noting the bot must be **on the team and a member of #sysadmin** (the documented idea-refinery gotcha at lines 246-248).
4. **Operator step (no secrets in git or chat):** set `PODCAST_MM_TOKEN` in gitignored `OB1/docker/.env` (that is the file OB1's compose interpolates — not the main `.env`), using either `AO_MATTERMOST_BOT_TOKEN`'s bot or a dedicated low-privilege bot added to #sysadmin.
5. **Deploy:** recreate the one container — `docker compose -f OB1/docker/docker-compose.yml --env-file OB1/docker/.env up -d openbrain-podcast`. No container added/removed/moved → the full SERVICE-LIFECYCLE checklist is not triggered; stack-map/recovery inventories are unaffected.
6. **Land it:** push the OB1 branch to OB1's remote first (pinned-SHA reachability), then a parent-repo work branch off `development` bumps the `OB1` gitlink and flips the K.10 PLANNED bullet (`CLEANUP-PLAN.md:1128-1130`) to built, merging via PR with the evidence below.

## Validation (evidence required before merge)

- **Unit (new, GREEN):** `deno test` on `mm-alert.ts` + the two call-path shapes (stubbed fetch): non-completed status posts id+status; thrown error posts error text; missing env and MM-down both return false without throwing.
- **Live RED (at base, forced fail):** with the container idle (no run in `docker logs openbrain-podcast`), force the catch path with a dead ON base on a dry-run (no `--commit`):
  `docker exec -e ON_BASE=http://127.0.0.1:9 openbrain-podcast sh -c "cd /app && deno run --unstable-net -A link-enrich.ts --window=168 --limit=2 --audio"`
  (window per the file's own header recipe at `link-enrich.ts:40`, so segments exist and `generateAudio` is reached). Evidence: "audio generation failed (best-effort)" in output and **no** post in #sysadmin (read via `scripts/mattermost-mcp/mm.py` / the mattermost MCP tools). The `failed`-status branch variant: `-e ON_EPISODE_PROFILE=__nonexistent__` instead.
- **Live GREEN (fix deployed):** identical command → same console behavior **plus** an alert in #sysadmin carrying episode name + job id/status (or error). Screenshot/permalink into the MM approval thread.
- **Regression:** next morning's scheduled healthy run — audio link present in the email, **no** alert posted; `docker logs openbrain-podcast` clean.

## Risks / interlocks

- **Live-service action (operator approval per Part M):** the `up -d openbrain-podcast` recreate and the forced-fail exec runs must happen in the daytime window, outside the ~01:00-local → morning digest chain, and only while no pipeline run is in flight.
- **Secret handling:** `PODCAST_MM_TOKEN` exists only in gitignored `OB1/docker/.env`; never in compose files, commits, or the MM thread. The `.githooks` secret guard applies to the OB1 branch too.
- **Bot membership:** if the bot isn't on the team + in #sysadmin, channel resolution 403s — the helper's fail-soft contract makes this a logged no-op, not a chain break; verify membership during the operator step.
- **Submodule discipline:** never bump the parent gitlink to a SHA not reachable on OB1's remote (CLAUDE.md; gitlink-reachability gate). OB1 push first, parent PR second.
- **Alert fatigue:** the job runs once nightly, so one alert per failed run — no throttle needed; if ON is down for days this is exactly the repeated signal the incident showed we were missing.
