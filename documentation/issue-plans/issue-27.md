---
issue: 27
title: Residual unkeyed gateway callers in the nightly chain: `not-needed` (7/day) + `no-key` burst (17)
created: 2026-08-23T13:42:53+00:00
base_sha: a1322f5245f8340ca1b56374cdce00b455a2f4ef
target_branch: development
status: executing
triage: bounded
verdict: fix
repro: confirmed-in-code
touches_live: true
touched_paths: OB1/docker/docker-compose.scheduled.yml, OB1, llm-queue/src/llm_queue/policy.py, documentation/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md
---

# Plan: Residual unkeyed gateway callers in the nightly chain: `not-needed` (7/day) + `no-key` burst (17)

## Problem

Two more J.1 missed callers (same class as #26; issue-26.md:65 — a working-tree/history citation, committed in `458bf90` after this base — predicted exactly this residue and named the daily-digest consumers as the next suspects). Code citations below are at base `a1322f5`, whose OB1 gitlink is `48a84aec` (the working tree is FOUR wiki-scoped commits past the pin — `23c65a5`, `950c0be`, `c170307`, `14bcedd`, the in-flight wiki session's work — none touching these files).

**`no-key` (17-call burst, 05:31:27–28) = `openbrain-digest` — identified, confirmed-in-code.**
`OB1/recipes/daily-digest/send-digest.ts:58-64` builds one `LlmClient` with `bearer: env("LOCAL_LLM_BEARER", "no-key")` and gateway-alias defaults (`http://llama-cpp:8080/v1` chat, `http://llama-cpp-embed:8080/v1` embed); `src/clients/llm.ts:95` sends it as the `Authorization` bearer. Nothing sets `LOCAL_LLM_BEARER`: the service's `env_file` (`../recipes/daily-digest/.env`, gitignored — variable *names* inspected, values untouched) holds only `DIGEST_*`/`OPEN_BRAIN_URL`, and the compose `environment:` block (`OB1/docker/docker-compose.scheduled.yml:57-63`) adds nothing → literal `no-key` at the gateway. The burst shape matches the code: sections run concurrently (`src/digest.ts:80` `Promise.allSettled`) and the synthesizer/semantic-search fan out embeds (`src/considerations/synthesizer.ts:98` `Promise.all`); the digest fires at the end of the 05:00 chain (prune → podcast → digest), landing ~05:31. **Why J.1 missed it:** `docker-compose.scheduled.yml:47` claims "No LLM call (mechanical formatting)" even though lines 74-77 put the service on `llm-net` for the weather brief, and the J.1 Phase C inventory (J1-VIRTUAL-KEYS-CUTOVER.md:64) listed "digest" as covered by the shared `OB_*` vars — no such wiring exists. **Impact is functional, not cosmetic:** since the 08-21 flip, every LLM-backed digest section (weather brief `src/sections/weather.ts`, relevance filter, semantic search, synthesizer) 401s and is silently omitted by the orchestrator — the morning email has been quietly degraded for two days.

**`not-needed` (7 calls, chain window) — SPLIT OUT to its own follow-up issue (host-side identification 2026-08-23).**
Ledger detail pins the shape: ALL `not-needed` rows are CHAT calls (`qwen36-27b:nothink`), arriving in rapid retry bursts starting ~05:00:22-05:00:45 — the podcast-render stage, not an embed path. Static search finds exactly one chat-path fallback with that literal inside the chain codebase: `OB1/recipes/daily-digest/src/podcast/script-renderer.ts:75` (`Authorization: Bearer ${cfg.apiKey ?? "not-needed"}`) — the script-renderer's config never receives the existing `OB_PODCAST_LLM_KEY`/`CHAT_API_KEY`. Confirming the invoking container and wiring the existing key through `cfg.apiKey` is its own bounded fix, filed separately so THIS plan stays determinate. No new key is needed there (the podcast lane key already exists).

**`ob-research` 35 failures / 263 successes:** almost certainly llm-queue 429 admission backpressure that the research service deliberately rides out (`docker-compose.scheduled.yml:169-175`; the gap-dive fan-out saturates the `ob-research` lane, `max_concurrency=2` in `llm-queue/src/llm_queue/policy.py:80`). A keyed lane with an 88% success rate is not an unkeyed caller. Triaged in step 2 by error-code, not assumed.

## Approach

OB1 changes follow the submodule bump-via-PR flow (push to the OB1 remote first, then gitlink bump in the parent on a work branch off `development`). No container is added/removed/moved — config + recreate on existing services, so the full SERVICE-LIFECYCLE.md checklist is not triggered.

1. **Identification — RESOLVED host-side (2026-08-23), nothing for the worker here:** `no-key` = `openbrain-digest` (`LOCAL_LLM_BEARER` unset; 27 bge-m3 embeds + 2 chats in the ledger match the synthesizer/semantic-search fan-out). `not-needed` = chat-only, split to its own follow-up issue (see Problem). This plan's scope is the digest caller alone.

2. **Triage `ob-research`:** error-code breakdown of the 35 failed rows. Expected: HTTP 429 from llm-queue admission with adjacent successful retries → document as backpressure noise in the issue reply, no change. Only a 401/permission signature escalates it to a third caller (then it repeats steps 3–6).
3. **Mint key (J.1 Phase C/D) — HOST harness action:** `/key/generate` on the live gateway, alias `ob-digest`. Store the value in **`OB1/docker/.env`** (TRAP from #26: the OB1 project interpolates from `OB1/docker/.env`, not the root `.env` and not the recipe `env_file`). Additive admin op, no restart.
4. **Compose (OB1 submodule):** in `docker-compose.scheduled.yml`, add `LOCAL_LLM_BEARER: ${OB_DIGEST_LLM_KEY:-no-key}` to the `openbrain-digest` `environment:` block with a one-line J.1 comment (matching the `OB_PODCAST_LLM_KEY` annotation at :184-185), and fix the misleading ":47 No LLM call" header comment. Compose `environment` overrides `env_file`; the podcast shares the recipe `env_file` but reads `CHAT_API_KEY`, so there is no cross-talk. ON needs no compose change — its `:-not-needed` line already reads the var being filled.
5. **llm-queue lanes (parent repo):** add `"ob-digest": PriorityClass("ob-digest", rank=3, acceptable_wait_s=600.0, max_concurrency=2)` to `_DEFAULT_CLASSES` (`llm-queue/src/llm_queue/policy.py:64-92`). Run the llm-queue test suite. Effective at the next llm-queue restart (interlock below); until then the rank-2 default lane is acceptable, or apply the non-persistent runtime override via the control API.
6. **Recreate consumers** (recreate, NOT `restart` — env changes don't reach restarted containers; ao-worker stale-token lesson): `docker compose -f OB1/docker/docker-compose.yml ... up -d openbrain-digest`, outside the 03:00–07:30 UTC cron band.
7. **Docs:** J1-VIRTUAL-KEYS-CUTOVER.md — correct the Phase C rows (digest was listed as covered at :64 but had no wiring; ON's compose-embed surface missing at :62), add the new caller/key rows and the miss #6 (+#7) LESSON entries.
8. **Land it:** push OB1 commits to the OB1 remote's pinned line, then parent gitlink bump + policy.py + docs on the work branch → PR into `development` with the validation evidence. NOTE: the working tree currently has OB1 checked out on `feature/wiki-production-hardening` (FOUR wiki commits past the pinned `48a84aec` - `23c65a5..14bcedd`, the in-flight wiki session's work; gitlink bump uncommitted) — coordinate with the operator so this fix and the wiki work land on one line rather than divergent gitlink bumps.

## Validation (evidence required before merge)

**Evidence assignment (gate-plan adjustment, 2026-08-23):** the sandboxed
worker's deliverable is tracked-file diffs ONLY - the `openbrain-digest`
env line + the `:47` comment fix in `docker-compose.scheduled.yml` (OB1
submodule), the `ob-digest` lane in `llm-queue/src/llm_queue/policy.py`,
and the J1 doc rows. The worker's own evidence = the llm-queue test suite
run in-sandbox (`python3 -m pytest llm-queue/tests -q` or the suite's
documented runner - stdlib env questions resolved by the suite itself),
included in the PR description. The worker must NOT run `docker exec`,
`stack.ps1`, SQL, or compose commands, and must NOT claim any host tier
below. ALL RED/GREEN/scheduled-run/residual tiers execute on the HOST
harness (Claude session / operator), which attaches transcripts to the PR.
The mint (step 3) and recreates (step 6) are HOST actions.

**RED (before fix):**
- Ledger: `scripts/stack/stack.ps1 stats` (or spend-log SQL) reproducing the `no-key` signature for the 05:00 UTC window (17-call burst ~05:31) and the 35 failed `ob-research` rows (`not-needed` is tracked in its own follow-up issue).
- In-container replay without firing the chain: `docker exec openbrain-digest sh -c "curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer no-key' -H 'content-type: application/json' -d '{\"model\":\"qwen36-27b:nothink\",\"messages\":[{\"role\":\"user\",\"content\":\"probe\"}]}' http://llama-cpp:8080/v1/chat/completions"` → **401**; same shape with `bge-m3` against `http://llama-cpp-embed:8080/v1/embeddings` → **401**.

**GREEN (after fix + recreate):**
- Recreated containers carry the keys (`docker exec openbrain-digest sh -c 'test -n "$LOCAL_LLM_BEARER" && echo set'` — never print values) and the same replays using the containers' own env bearers return **200**.
- Next **scheduled** 05:00 UTC run (no manual `/run` — interlock below): morning ledger shows zero `no-key`/`not-needed` rows; `ob-digest` appear with succeeded calls, attributed to their lanes in the queue stats view.
- Function restored, not just errors silenced: the morning digest email contains the weather brief and synthesis/related-memory sections again (silently omitted since 08-21).
- `ob-research` disposition: either the 35 failures are documented 429 backoff (with error-code evidence in the PR) or the third caller is fixed under the same pattern.
- Residual sweep: ledger query for any remaining non-virtual `api_key` values in the window returns empty (catches a miss #8 instead of declaring victory).

## Risks / interlocks

- **Live actions needing operator awareness:** virtual-key mints (additive, no restart); recreate of `openbrain-digest` (brief, safe outside the cron band); llm-queue restart for the new lanes = inference-plane blip — ride the next maintenance window (runtime override or rank-2 default covers the gap).
- **Do NOT validate via manual `POST /run`:** the chain ends in a real outbound email and podcast render. Evidence comes from the next scheduled run.
- **Secrets hygiene:** all env probes are existence/sentinel checks (`test -n`, `= not-needed`); key values never appear in logs, the PR, or this plan's evidence.
- **Submodule discipline:** gitlink bumps only to commits reachable on the OB1 remote; parent merge to `development` with evidence; `main` promotion stays with the operator. Coordinate with the in-flight `feature/wiki-production-hardening` checkout (step 8).
- **Rollback:** J.1 global rollback (comment out `master_key`) untouched; local rollback = revert gitlink + recreate → the prior 401 behavior returns, nothing worse.
