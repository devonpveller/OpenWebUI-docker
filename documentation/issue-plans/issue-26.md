---
issue: 26
title: Identify and key the residual `local-trust` gateway caller (05:00 wiki compile)
created: 2026-08-22T16:23:46+00:00
base_sha: 9e465758e
target_branch: development
status: done
triage: bounded
touches_live: true
touched_paths: OB1/recipes/email-history-import/pull-gmail.ts, OB1/docker/docker-compose.scheduled.yml, OB1, llm-queue/src/llm_queue/policy.py, documentation/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md
---

> **RESOLVED 2026-08-22** directly by the operator-partner session (live data-loss regression — nightly Gmail ingestion had been silently dropping/un-embedding new mail since the J.1 flip; too urgent for the worker-org queue). Fix: minted the `ob-gmail` virtual key (lane-attributed), wired it into the recipe .env replacing the `local-trust` placeholder, recreated `openbrain-gmail-pull`, verified an authed embed from inside the container. This plan file stands as the autonomous planner's first production artifact — it identified the true culprit beyond the issue's framing.

# Plan: Identify and key the residual `local-trust` gateway caller (05:00 wiki compile)

## Problem

The LiteLLM ledger records ~6 failed calls/day with `api_key='local-trust'` at exactly 05:00 UTC — 3× `bge-m3` embed + 3× `qwen36-27b:nothink` chat — surviving the J.1 virtual-keys cutover (this is missed caller #5; #1–#4 are in `documentation/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md`).

**The caller is identified: `openbrain-gmail-pull`** — the *top* of the 05:00 cascade that ends in the wiki recompile (issue framing "wiki compile chain" is the same event chain: cron → pull → prune → podcast → digest, with prune also triggering the wiki recompile). Evidence:

- `OB1/docker/cron/crontab:54` — `0 5 * * *` POSTs `http://openbrain-gmail-pull:8080/run`. This is the only LLM-calling job firing at exactly 05:00 UTC (the wiki service's own `WIKI_RECOMPILE_HOUR=1` ET compile is fully keyed since miss #4, `OB1/docker/docker-compose.yml:549-555`).
- `OB1/recipes/email-history-import/pull-gmail.ts:713-767` — `getEmbedding()` and `extractMetadata()` both send `Authorization: Bearer ${OPENROUTER_API_KEY}` to the gateway aliases `http://llama-cpp-embed:8080/v1/embeddings` (bge-m3) and `http://llama-cpp:8080/v1/chat/completions` (qwen36-27b:nothink). The var name is a relic of the upstream OpenRouter recipe; the local-repoint comment (line 67) calls the bearer "a non-secret placeholder".
- Gitignored evidence (per CLAUDE.md convention, checked): `OB1/recipes/email-history-import/.env:8` sets `OPENROUTER_API_KEY=local-trust` — the same placeholder string used as `OPEN_BRAIN_SERVICE_KEY` elsewhere, which is why the ledger shows `local-trust`. This `.env` is the `env_file` of `openbrain-gmail-pull` in `OB1/docker/docker-compose.scheduled.yml:91-92`.
- Cardinality fits: only *truly new* `brain/`-labeled emails do LLM work (dedup short-circuits the rest, crontab comment lines 41-47) — ~3 new emails/night × (1 metadata chat + 1 embed) ≈ the observed 6 failures.

**Impact beyond ledger noise:** since the J.1 master-key flip (2026-08-21), nightly Gmail ingestion of new emails is silently broken — metadata extraction and embeddings 401, so new email thoughts are dropped or land unembedded (unretrievable). The fix restores a broken pipeline, not just hygiene.

`prune-short-term.ts` makes no LLM calls (its `local-trust` fallback is PostgREST-only); `openbrain-podcast` already carries `OB_PODCAST_LLM_KEY`. Scope is the one caller.

## Approach

All code/compose changes live in the **OB1 pinned submodule** — follow the bump-via-PR flow (push to OB1 remote first, then gitlink bump in the parent on a work branch off `development`; `main` untouched per branch policy). No container is added/removed/moved, so the full SERVICE-LIFECYCLE.md checklist is not triggered — this is a config/recreate change on an existing service.

1. **Re-confirm at runtime** (identification evidence for the record): pull last night's ledger rows via `scripts/stack/stack.ps1 stats` (or SQL against `llm-gateway-db` spend logs) and `docker logs openbrain-gmail-pull` around 05:00 UTC showing the 401s. Confirm the container's live env carries the bad bearer: `docker exec openbrain-gmail-pull sh -c 'echo $OPENROUTER_API_KEY'` → `local-trust`.
2. **Issue the virtual key** per the J1 doc Phase C/D procedure: `/key/generate` on the live gateway with `key_alias: ob-gmail-pull` (deterministic memorable key, same pattern as the other `OB_*` keys). Store the value as `OB_GMAIL_LLM_KEY=...` in **`OB1/docker/.env`** (TRAP: the OB1 compose project interpolates from `OB1/docker/.env`, not the root `.env`). Additive admin op; no restart.
3. **Code (OB1 submodule):** in `pull-gmail.ts`, add `const LLM_KEY = Deno.env.get("LOCAL_LLM_KEY") || "not-needed";` alongside the existing `LOCAL_LLM_*` family and use it as the bearer in `getEmbedding()` (line 717) and `extractMetadata()` (line 760) instead of `OPENROUTER_API_KEY`. This is the J1 doc's "renames to fold in while touching callers" pattern (honest var name; `OPENROUTER_API_KEY` stays untouched for the upstream recipe's cloud mode).
4. **Compose (OB1 submodule):** in `OB1/docker/docker-compose.scheduled.yml`, add `LOCAL_LLM_KEY: ${OB_GMAIL_LLM_KEY:-not-needed}` to the `openbrain-gmail-pull` `environment:` block (compose `environment` overrides `env_file`) with a one-line J.1 comment matching the existing `OB_PODCAST_LLM_KEY` annotation. Optionally retire the stale `OPENROUTER_API_KEY=local-trust` line from the recipe `.env` with a comment noting what replaced it.
5. **llm-queue lane (parent repo):** add `"ob-gmail-pull": PriorityClass("ob-gmail-pull", rank=3, acceptable_wait_s=600.0, max_concurrency=2)` to `_DEFAULT_CLASSES` in `llm-queue/src/llm_queue/policy.py` (batch nightly job → rank 3 like `ob-entity`/`ob-wiki`; the unknown-caller default of rank 2 is too favorable). Run llm-queue's test suite. The code change takes effect at the next llm-queue restart (interlock below); until then the default lane is functionally acceptable, or apply the non-persistent runtime override via the control API (`POST /keys/ob-gmail-pull/policy`).
6. **Recreate the consumer:** `docker compose -f OB1/docker/docker-compose.yml -f OB1/docker/docker-compose.scheduled.yml --env-file OB1/docker/.env up -d openbrain-gmail-pull` — must be a **recreate**, not `restart` (env changes don't reach a restarted container — same trap as the ao-worker stale-token incident). Script is bind-mounted into a stock deno image, so no rebuild.
7. **Docs:** add caller row + miss #5 entry to the J1-VIRTUAL-KEYS-CUTOVER.md caller table (`OB_GMAIL_LLM_KEY`, configured in `OB1/docker/.env` + `docker-compose.scheduled.yml`).
8. **Land it:** push the OB1 commits to the OB1 remote (`feature/integrated-knowledge-system`), then in the parent commit the gitlink bump + policy.py + doc changes on the work branch; PR into `development` with the validation evidence below.

## Validation (evidence required before merge)

**RED (broken, before fix):**
- Ledger: `scripts/stack/stack.ps1 stats` (or spend-log SQL) showing the 05:00 UTC failed rows, `api_key='local-trust'`, 3× bge-m3 + 3× qwen36-27b:nothink.
- Direct replay of the failing call without firing the chain: `docker exec openbrain-gmail-pull sh -c "curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer local-trust' -H 'content-type: application/json' -d '{\"model\":\"bge-m3\",\"input\":\"probe\"}' http://llama-cpp-embed:8080/v1/embeddings"` → **401**. Same shape against `http://llama-cpp:8080/v1/chat/completions` → **401**.
- `docker logs openbrain-gmail-pull` from last night's run showing embed/metadata failures.

**GREEN (after fix + recreate):**
- Same replay using the container's own new env (`Bearer $LOCAL_LLM_KEY`) → **200** on both endpoints, and the recreated container shows `LOCAL_LLM_KEY` set (`docker exec openbrain-gmail-pull sh -c 'test -n "$LOCAL_LLM_KEY" && echo set'`).
- Next scheduled 05:00 UTC run (preferred over a manual `/run` — see interlocks): morning ledger sweep shows **zero** `local-trust` rows for the new day; the `ob-gmail-pull` alias appears with succeeded calls; `x-ai-stack-caller` attribution lands the calls on the intended lane (`stack.ps1 stats` queue view).
- Function restored, not just errors silenced (chunk-worker-emoji-bug lesson — verify RETRIEVABLE): the night's newly pulled email thoughts exist in PostgREST **with non-null embeddings**; the morning digest email arrives (chain completed).
- Residual sweep: the same ledger query filtered to any remaining non-virtual `api_key` values returns empty (catches a hypothetical miss #6 — e.g. the digest weather-brief chat path — rather than declaring victory on this caller alone).

## Risks / interlocks

- **Live actions needing operator awareness/approval:** virtual-key generation on the live gateway (additive, no restart); recreate of `openbrain-gmail-pull` (brief, HTTP-triggered service — do it outside the 03:00–07:30 UTC cron band so no chain is mid-flight); llm-queue restart for the policy.py lane (inference-plane restart — ride the next maintenance window; the runtime control-API override or the rank-2 default covers the gap).
- **Do NOT validate via manual `POST /run`** without operator sign-off: the pull chains prune → podcast → digest and ends in a real outbound email + podcast render. Preferred evidence is the next scheduled run.
- **Submodule discipline:** the gitlink must only be bumped to a commit already reachable on the OB1 remote (fresh `--recurse-submodules` clones break otherwise); parent-side merge follows branch policy (work branch → `development` with evidence; `main` promotion is the operator's).
- **Env-file split brain:** the key value lives in gitignored `OB1/docker/.env` (compose interpolation), *not* the root `.env` and *not* the recipe `env_file` — mixing these up yields `not-needed` at runtime and a fresh 401 caller under a different name. The GREEN env check in validation guards this.
- **Rollback:** J.1's global rollback (comment out `master_key`) is untouched; local rollback is reverting the gitlink + recreating the container — the old behavior (401s) returns, nothing worse.
- If the residual sweep still shows `local-trust` after this lands, the identification evidence points next at the same cascade's other `daily-digest/.env` consumers (`send-digest.ts` weather brief, podcast link-enrich) — file as miss #6 rather than widening this change.
