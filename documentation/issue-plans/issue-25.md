---
issue: 25
title: Queue-ETA notifications: tell the user when a long job is queued and when to expect it
created: 2026-08-22T18:22:00+00:00
base_sha: 9a4d8c1a6
target_branch: main
status: planned
triage: bounded
touches_live: true
touched_paths: OB1/integrations/research-service/index.ts, llm-queue/src/llm_queue/routes/data.py
---

# Plan: queue-ETA notifications for long-waiting jobs

## Problem
A research/podcast job held behind a busy llm-queue lane is invisible to the
user until it completes: no timestamp, no estimate, no "check back at".

## Approach
Two thin layers, no queue changes needed (the estimates already exist):
1. **Research service (primary UX)**: in the job loop
   (`OB1/integrations/research-service/index.ts`), when a job has been
   RUNNING/QUEUED longer than `NOTIFY_AFTER_MS` (default 120s), fire ONE
   progress notification through the EXISTING OWUI async-callback channel
   (and, when a thread id maps to Mattermost origins, via the MM bot):
   "research #<id> queued behind <lane> — est completion ~HH:MM (from
   /observe/queue/estimate)". Re-notify only on ±50% estimate drift, max 3.
2. **Estimate source**: GET the gateway's `/observe/queue/estimate?model=…`
   (virtual-key authed) — already exposes projected wait; add est_completion
   = now + est_wait + avg_T to the message.

## Validation (evidence required before merge)
- RED: submit 3 concurrent research jobs (fan-out saturates the single lane)
  with `NOTIFY_AFTER_MS=5000` → assert exactly one ETA notification per held
  job lands in the OWUI chat callback (and MM where applicable), timestamps +
  estimates present.
- GREEN: single idle-lane job → NO notification.
- Evidence: callback payload capture + queue board snapshot in the PR.

## Risks / interlocks
- `touches_live: true`: rebuild + recreate `openbrain-research` → operator
  approval in-thread (M.4); OB1 submodule commit/push/gitlink rule.
- Rate-limit the notifications (the max-3 rule) so a saturated night cannot
  spam the chat — this is the same restraint as the digest gap-dive throttle.
