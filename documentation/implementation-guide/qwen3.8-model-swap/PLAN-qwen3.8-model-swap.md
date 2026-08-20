# PLAN — Swap the chat model to Qwen 3.8 27B

**Status:** ✅ APPLIED + VERIFIED 2026-08-16 (Option A drop-in) · **Created:** 2026-08-14
**Owner:** operator · **Scope:** replace the live `qwen36-27b` chat model with
`unsloth/Qwen3.8-27B-GGUF` (Q4_K_M).

> **APPLIED 2026-08-16 — what was done & verified**
> - `.env`: `LLAMA_SWAP_QWEN36_27B_MODEL_PATH` → `/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf`;
>   `SPEC_TYPE=none`. Backup at `.env.bak-pre-qwen38` (rollback insurance).
> - Recreated only `llama-cpp-upstream` (`docker compose up -d --no-deps --force-recreate`).
> - **Follow-up 2026-08-16 (restore 3 concurrent lanes):** ctx 196608→**262144**, parallel
>   2→**3** (n_ctx_slot=87552, still > the ~69k tool-synth max), `LLM_QUEUE_SLOTS`=3 /
>   `MAX_IN_FLIGHT`=4. Recreated `llama-cpp-upstream` + `llm-queue`. VRAM **92.9%** (steady).
>   Verified 3 concurrent front-door requests served (queue P=3). Per-request lane 98304→87552;
>   context-lane consumers left at 98304 (safe — every real prompt < 69k; trim to ~86k for strict sync).
> - **Follow-up 2 — 2026-08-16 (MTP re-enabled):** the stock GGUF was FOUND to carry the MTP head
>   (`blk.64.nextn.*`, `mtp_num_hidden_layers=1`) — the earlier "no MTP head" assumption was wrong.
>   `SPEC_TYPE=draft-mtp`, reverted to ctx 196608 / parallel 2 (MTP ~doubles KV, won't fit at parallel 3),
>   `LLM_QUEUE_SLOTS`=2/`MAX_IN_FLIGHT`=3. MTP active (draft acceptance 0.68) → **62.5 tok/s (~1.49×** vs
>   42). VRAM **93.7%**. Tool-calling + front door verified. Trade: parallel 3→2 (lane grew 87552→98304).
>   Backup `.env.bak-pre-mtp` = parallel-3/no-MTP. **MTP and parallel-3 are ~mutually exclusive on this
>   24 GB / b9935** — a newer llama.cpp with independent draft-ctx could allow both (future).
> - **Verified:** loads from the unsloth path; basic completion (thinking on, `reasoning_content`
>   present); `:nothink` suppresses thinking; **tool-calling** returns proper `tool_calls`;
>   **front door** (openwebui → `llama-cpp:8080` → LiteLLM → llm-queue → upstream) returns
>   `FRONTDOOR-OK`; **bge-m3 embeddings** still return 1024-dim vectors; all ~30 stack
>   containers remained healthy (only `llama-cpp-upstream` restarted).
> - **VRAM:** 21227 / 24564 MiB (86%) — below the ThinkingCap+MTP 22737 MiB, as predicted.
> - **Speed:** ~42 tok/s gen (no MTP speculative decoding — the expected tradeoff).
> - Diagnostic note: host port `:8081` is NOT published on the live stack (removed by
>   `docker-compose.override.yml` for isolation); validate via `docker exec` into the
>   container (router on `localhost:8080`), not `127.0.0.1:8081`.

Related memory: [[thinkingcap-mtp-experiment]], [[litellm-proxy-status]],
[[llm-queue-b2-admission-controller]], [[gateway-only-llm-routing-enforced]].

---

## 0. Goal & what's already done

Replace the model served behind the llama-swap id `qwen36-27b` with the new
**Qwen 3.8 27B** (Unsloth GGUF). The id, LiteLLM registration, and all downstream
consumers stay untouched — this mirrors the 2026-07-09 ThinkingCap swap, which
kept id `qwen36-27b` and only repointed the `MODEL_PATH` env.

**Download (in progress / done before this plan is applied):**

- Repo: `unsloth/Qwen3.8-27B-GGUF` (confirmed to exist; 27B, multimodal w/ vision
  encoder, 1M native ctx, Unsloth Dynamic V3.0 quants).
- Quant chosen: **`Qwen3.8-27B-Q4_K_M.gguf`** (~17.1 GB, single unsharded file) —
  mirrors the current deployment's Q4_K_M quant, so it drops into the existing
  VRAM/ctx profile with minimal surprise.
- Destination (host): `C:\Users\yamao\.lmstudio\models\unsloth\Qwen3.8-27B-GGUF\`
  → inside the container: `/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf`
  (the `.lmstudio\models` dir is bind-mounted read-only as `/models` on
  `llama-cpp-upstream`, and is captured by the `lm-models-backup` sidecar).
- Command used:
  `huggingface-cli download unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-Q4_K_M.gguf --local-dir <dest>`
- Disk at swap time: C: had ~126 GB free; a 17 GB pull is comfortable (old
  ThinkingCap + baseline GGUFs remain on disk for rollback).

> Alternative quant if you want max quality-per-byte at the same size class:
> `Qwen3.8-27B-UD-Q4_K_XL.gguf` (~17.9 GB, Unsloth Dynamic). Only a filename
> change in step 3.1. Q4_K_M was chosen as the conservative, VRAM-matched default.

---

## 1. What runs today (baseline to preserve)

- **llama-swap id:** `qwen36-27b` (+ `qwen36-27b:nothink` variant). Pinned by many
  callers — do **not** rename in the swap itself.
- **Live GGUF:** `/models/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf`
  (RL fine-tune, ~50% fewer thinking tokens, MTP head baked in).
- **Serving profile (`.env`):** ctx `196608`, parallel `2`, batch `1024`/ubatch
  `512`, KV cache `q4_0`/`q4_0`, reasoning-budget `4096`,
  **`SPEC_TYPE=draft-mtp`** (self-speculative via the baked-in MTP head),
  spec-draft KV `q4_0`. Measured ~91.4% VRAM (22451 MiB) on the 3090.
- **Lane:** 196608 / 2 = **98304 tokens** per request. Several consumers budget
  against this exact number (see `.env` "LANE CONSUMERS" block).
- **Front door:** LiteLLM (`config/litellm.config.yaml`) registers `qwen36-27b`
  + `:nothink`, both `api_base → http://llm-queue:8080/v1`. `llm-queue`
  (`LLM_QUEUE_SLOTS=2`, `MAX_IN_FLIGHT=3`) admits, then forwards to
  `llama-cpp-upstream`. **Never route inference around LiteLLM.**

---

## 2. Compatibility analysis — the real risks

### 2.1 Speculative decoding (BLOCKER if ignored)
`SPEC_TYPE=draft-mtp` depends on an MTP head that exists only in the ThinkingCap
fine-tune. **Stock Unsloth Qwen3.8 GGUFs have no MTP head**, so this must become
**`SPEC_TYPE=none`** (the documented safe value, same as `qwen36-27b-baseline` and
the ThinkingCap rollback note). The three `SPEC_DRAFT_*` vars become inert.
→ **Consequence:** we lose the ThinkingCap MTP speed win (~+39% gen tok/s per the
experiment). Base 3.8 gains newer model quality but generation throughput will
likely be lower than today's MTP-accelerated ThinkingCap. This is the headline
tradeoff — flag it, measure it (§5), decide if acceptable.

### 2.2 Chat template (validate)
`config/chat-template.jinja` is a permissive ChatML+Tools template "based on the
Qwen3.6 native template," and already emits vision sentinels
(`<|vision_start|><|image_pad|><|vision_end|>`) and `<think>` handling with an
`enable_thinking` kwarg. Qwen3.8 is ChatML/Qwen-family, so it is **likely
compatible as-is**. Must verify: (a) tool-call loop still parses (the template's
whole purpose), (b) `:nothink` still suppresses thinking, (c) 3.8's native
template didn't introduce new control tokens the current one omits. If 3.8 ships a
materially different template, capture the new one and re-derive our permissive
variant (do **not** silently drop the tool-loop / system-position relaxations).

### 2.3 VRAM headroom (opportunity)
Dropping the MTP head frees the spec-draft KV. Net: base 3.8 Q4_K_M (~17.1 GB
weights) + ctx 196608 + q4_0 KV should sit **below** today's 91.4%. Start at the
known-good ctx `196608` / parallel `2` (no consumer edits), confirm VRAM, then
optionally raise ctx toward `262144` (the `.env` already notes "restore 262144 if
spec decoding is ever turned off"). Raising ctx changes the lane → must update all
"LANE CONSUMERS" (§ follow-ons), so treat it as a separate, later step.

### 2.4 Thinking behavior
ThinkingCap was RL-tuned for fewer thinking tokens; base 3.8 will "think" more
freely. Watch reasoning-budget adherence and tool-loop latency in §5.

---

## 3. Swap procedure — Option A (drop-in, RECOMMENDED)

Keep id `qwen36-27b`; only touch `.env` + recreate one container. Zero changes to
`litellm.config.yaml`, `llm-queue`, or lane consumers. Fully reversible.

### 3.0 Pre-flight — prove the new model BEFORE the live alias points at it (recommended)
This validates the new GGUF in isolation so `qwen36-27b` never serves an unproven
model. Mirrors the existing `qwen36-27b-baseline` precedent (a diagnostic-only
llama-swap id, unregistered in LiteLLM, unreachable from the service plane).
1. Add a throwaway entry to `config/llama-swap.config.yaml` — copy the
   `qwen36-27b-baseline` block, rename id to `qwen38-27b-probe`, hardcode
   `--model /models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf`, ctx `196608`,
   **no** `--spec-type`. Do **not** add it to `litellm.config.yaml`.
2. `docker compose up -d --force-recreate llama-cpp-upstream` (live `qwen36-27b`
   still points at ThinkingCap — its MODEL_PATH is untouched).
3. Probe it directly on the diagnostic port (host-only, bypasses LiteLLM/the alias
   for a *diagnostic* load — allowed; never for service traffic):
   `POST http://127.0.0.1:8081/v1/chat/completions` with `"model":"qwen38-27b-probe"`.
   Check: loads, fits VRAM, a tool-call parses. (llama-swap is one-model-at-a-time,
   so this briefly swaps out the live model — normal swap behavior; a subsequent
   `qwen36-27b` request swaps it back.)
4. Only if the probe passes, proceed to §3.1. Delete the `qwen38-27b-probe` entry
   afterward (leave it if you want a permanent A/B diagnostic id).

> If you'd rather skip pre-flight, go straight to §3.1 — the fast-rollback in §6
> still returns you to the current state within ~1–2 min. Pre-flight just means the
> live alias is never exposed to a model that might not load.

### 3.1 Edit `.env` (the `Qwen 3.6 27B profile` block)
- **Step 0 — back up `.env` first (`.env` is gitignored → git will NOT restore it):**
  `Copy-Item .env .env.bak-pre-qwen38`. Rollback then = `Copy-Item .env.bak-pre-qwen38 .env -Force`.
- Repoint the model path (leave the old line commented for rollback, mirroring the
  existing ThinkingCap comment style):
  ```
  # LLAMA_SWAP_QWEN36_27B_MODEL_PATH=/models/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/ThinkingCap-Qwen3.6-27B-Q4_K_M.gguf
  LLAMA_SWAP_QWEN36_27B_MODEL_PATH=/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf
  ```
- Turn off speculative decoding:
  ```
  LLAMA_SWAP_QWEN36_27B_SPEC_TYPE=none
  ```
- Leave ctx/parallel/batch/KV/reasoning-budget **unchanged** for the first cutover
  (ctx 196608, parallel 2). `SPEC_DRAFT_*` vars can stay; they're inert with
  `SPEC_TYPE=none`.

### 3.2 Apply
- Recreate the inference server so it picks up the new env + GGUF:
  `docker compose up -d --force-recreate llama-cpp-upstream`
- No OWUI/tailscale restart involved, so the netns-ordering caveat does **not**
  apply here. `llm-gateway` / `llm-queue` do not need recreating (path resolves
  inside llama-swap on first request).
- llama-swap loads the new GGUF lazily on the first request to `qwen36-27b`.

### 3.3 Sanity before declaring done — see §5.

---

## 4. Swap procedure — Option B (rename to `qwen38-27b`, OPTIONAL / LATER)

Semantically correct but higher blast radius. Do this only after Option A is
proven, as a cleanup pass:
- Add a new `qwen38-27b` model block in `config/llama-swap.config.yaml` and new
  `qwen38-27b` (+`:nothink`) entries in `config/litellm.config.yaml`
  (`api_base → llm-queue`, mirroring the current pair).
- Repoint every consumer that pins `qwen36-27b` (little-coder `models.json`,
  mnemory `LLM_MODEL`, deep_research, OWUI pipes/filters, OB1). Grep for
  `qwen36-27b` across the workspace first.
- Keep `qwen36-27b` as an alias for a deprecation window, then remove.
- **Not needed to run 3.8** — Option A already serves 3.8 under the old id.

---

## 5. Verification checklist (run after §3.2)

1. **Loads + fits VRAM:** fire one request; confirm the server loads and GPU stays
   under the ~95% crash line (target well below today's 91.4% given no MTP). Probe
   VRAM via the health path, not the alias.
2. **Health probe:** direct `GET /health` on `llama-cpp-upstream` (allowed — real
   server, health only; never the alias, never a completion to `/health`).
3. **Completion via the front door:** a real chat completion for `qwen36-27b`
   *through LiteLLM* (proves the whole plane: LiteLLM → llm-queue → upstream).
4. **Tool-calling loop:** exercise an agentic tool call (this is what the custom
   chat template exists for) and a `qwen36-27b:nothink` request (thinking
   suppressed).
5. **Tool-heavy long prompt:** replay the failure mode that broke at lane 60160 —
   a large (~69k-token) OpenBrain tool-result synthesis prompt — to confirm the
   98304 lane still holds.
6. **Throughput note:** record gen tok/s vs the ThinkingCap+MTP baseline so the
   §2.1 tradeoff is quantified, not assumed.
7. **Ledger:** confirm the request shows in `LiteLLM_SpendLogs` (attribution
   intact).

---

## 6. Contingency & rollback

**Why return-to-current is guaranteed:** the swap is *non-destructive* — it changes
two `.env` lines and recreates one container. Nothing that defines the current state
is overwritten or deleted:
- The live **ThinkingCap GGUF stays on disk** (`/models/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF/…Q4_K_M.gguf`,
  15.66 GB) — verified present, alongside the original lmstudio-community 3.6
  (15.41 GB). The new 3.8 lives in a *separate* `unsloth/` dir; it never touches them.
- The id `qwen36-27b`, `litellm.config.yaml`, `llm-queue`, and every consumer are
  **unchanged**, so downstream never knows a swap happened — nothing to unwind there.

**Fast rollback (≤ ~2 min):**
1. Restore config: `Copy-Item .env.bak-pre-qwen38 .env -Force` (from §3.1 step 0) —
   or manually revert `LLAMA_SWAP_QWEN36_27B_MODEL_PATH` to the ThinkingCap path and
   set `LLAMA_SWAP_QWEN36_27B_SPEC_TYPE=draft-mtp` (ctx 196608 unchanged).
2. `docker compose up -d --force-recreate llama-cpp-upstream`. On the next
   `qwen36-27b` request llama-swap reloads ThinkingCap+MTP — prior behavior fully
   restored. Recreating this container is **safe in isolation**: it has no
   `network_mode: service:` netns coupling (unlike OWUI↔tailscale); `llm-gateway` /
   `llm-queue` just re-establish their connections over `llm-backend-net`.

**Blast radius while a bad swap is live:** only the `qwen36-27b` chat id fails —
embeddings (`bge-m3`, a *different* container `llama-cpp-embed-upstream`) and every
non-chat service keep working.

**Failure modes → each caught at a checkpoint, all recoverable:**
| Failure | Detected by | Recovery |
|---------|-------------|----------|
| Wrong path / file not found | server won't start; §5.1 load errors immediately | rollback |
| OOM on load (unlikely — 3.8 w/o MTP uses *less* VRAM than today) | §5.1 VRAM check / crash | rollback (or lower ctx) |
| Chat template incompatible (tool-loop / `:nothink`) | §5.4 | rollback, re-derive template |
| Loads fine but quality/speed regression | §5.6 tok/s + eval | rollback (same steps) |

**The only ways rollback could NOT be clean — and how we prevent them:**
- *Old GGUF deleted* → don't prune it during the trial (see §7 disk-cleanup: only
  after 3.8 is proven).
- *Container/GPU wedged by the recreate itself* → backstop is the recovery stack
  (`scripts/emergency-recovery.ps1 recover`, or `gpu-reset` if the GPU hangs), which
  restarts the inference plane in order. Independent of this swap.

---

## 7. Optional follow-ons (do NOT block the swap)

- **Vision (multimodal) unlock:** Qwen 3.8 ships a vision encoder. To enable image
  input, also download `mmproj-F16.gguf` from the same repo and add
  `--mmproj /models/unsloth/Qwen3.8-27B-GGUF/mmproj-F16.gguf` to the model's `cmd`
  in `llama-swap.config.yaml`. Text-only path needs none of this.
- **Bigger context:** with MTP off, raise ctx toward `262144`. This changes the
  lane (262144/2 = 131072) → update every "LANE CONSUMERS" value in `.env` in
  lockstep (mnemory, little-coder `models.json`, deep_research
  `context_budget.py`, OWUI filter/pipe valves). Separate change.
- **Higher-quality quant:** switch the filename to `Qwen3.8-27B-UD-Q4_K_XL.gguf`
  (Unsloth Dynamic, ~17.9 GB) if the VRAM/quality trade is worth it.
- **Disk cleanup:** once 3.8 is proven, prune the old ThinkingCap / baseline /
  lmstudio-community 3.6 GGUFs to shrink the `lm-models-backup` tarball. Follow the
  "explain removals" discipline — record what/why/replacement before deleting.
- **Rename to `qwen38-27b`** (Option B) as a correctness cleanup.

---

## 8. Files touched (Option A)

| File | Change |
|------|--------|
| `.env` | repoint `LLAMA_SWAP_QWEN36_27B_MODEL_PATH`, set `SPEC_TYPE=none` |
| (recreate) `llama-cpp-upstream` | pick up new env + lazy-load new GGUF |

No container add/remove → `emergency-recovery.ps1/.bat`, the `/stack-map`
reference, and `litellm.config.yaml` are **unchanged** for Option A.
