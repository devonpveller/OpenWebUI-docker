---
issue: 24
title: Podcast pipeline: Mattermost alert when the ON audio job fails
created: 2026-08-22T18:20:00+00:00
base_sha: 9a4d8c1a6
target_branch: main
status: planned
triage: bounded
touches_live: true
touched_paths: OB1/recipes/daily-digest/link-enrich.ts, OB1/docker/docker-compose.scheduled.yml
---

# Plan: Mattermost alert when the ON audio job fails

## Problem
When Open Notebook's `generate_podcast` job ends in any non-`completed`
state, `generateAudio()` in `OB1/recipes/daily-digest/link-enrich.ts` logs
one line and returns null — the digest email ships without audio and the
operator learns nothing. Found the hard way 2026-08-22: ON's provider
credentials 401'd for a full day of silent audio failures (J.1 miss #4).

## Approach
1. In `generateAudio()` (link-enrich.ts), on `status !== "completed"` AND on
   the catch path, POST a Mattermost message before returning null. Reuse the
   existing MM plumbing the digest chain already has (`host.docker.internal:8065`
   + the bot token already present in `../recipes/daily-digest/.env` for the
   ideas flow) — a ~15-line `notifySysadmin(text)` helper, fail-soft (an MM
   outage must never break the email path).
2. Message carries: episode name, ON job id, terminal status, and the pointer
   "check `docker logs open_notebook` + ON credentials (J1 cutover doc)".
3. Env switch `PODCAST_ALERT_MM=0` to disable (default on), documented in the
   compose service comment.

## Validation (evidence required before merge)
- RED: stub `waitForJob` to return `failed` in a dry run → assert the MM post
  fires (capture via mm.py read) and the function still returns null cleanly.
- GREEN: restore; run one real `/run` cycle (or tonight's scheduled run) →
  audio completes, NO alert sent.
- Evidence = both transcripts in the PR description.

## Risks / interlocks
- `touches_live: true`: `docker compose -f OB1/docker/docker-compose.yml up -d
  openbrain-podcast` to adopt the new script → OPERATOR APPROVAL in the MM
  thread before the recreate (M.4).
- OB1 is a submodule: OB1 commit + push FIRST, then gitlink bump via PR rule.
- Do not schedule the recreate inside the nightly 01:00–05:30 UTC chain window.
