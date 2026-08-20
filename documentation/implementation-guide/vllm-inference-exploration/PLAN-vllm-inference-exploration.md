# PLAN — Explore vLLM as the inference engine (maximize single-GPU VRAM)

**Status:** DRAFT / exploration (nothing built) · **Created:** 2026-08-16
**Owner:** operator · **Scope:** evaluate replacing llama.cpp/llama-swap with vLLM
for the `qwen36-27b` chat model on GPU 0, to better use the 24 GB card under
concurrent load. **Decision-gated spike — not a commitment to migrate.**

Related: [[llama-swap-perf-tuning]], [[llm-queue-b2-admission-controller]],
[[gateway-only-llm-routing-enforced]], [[litellm-proxy-status]],
[[qwen3-8-model-swap-plan]].

---

## 0. Objective — and defining "maximize VRAM" precisely

The hypothesis: vLLM's PagedAttention + continuous batching use VRAM more
gracefully than llama.cpp's fixed `ctx ÷ parallel` lanes, which we just hit the
edge of (going to parallel 3 forced the per-request lane down 98304 → 87552).

"Maximize VRAM" is ambiguous, so this plan measures **four distinct objectives**
and lets the benchmark decide which vLLM actually wins:

| # | Objective | Why it matters here |
|---|-----------|---------------------|
| **A** | Aggregate throughput (tok/s) under concurrent load | deep-research fan-out, OB1 worker bursts |
| **B** | Graceful concurrency — how many simultaneous requests before latency cliffs | interactive chat + background jobs coexisting |
| **C** | Effective context a *single* request can use under low concurrency | removes the rigid per-lane ceiling (87552 today) |
| **D** | KV-pool efficiency — usable context-tokens per GB of KV VRAM | the literal "VRAM maximization" question |

**Spoiler / honest framing (see §3):** vLLM is very likely to win A, B, and C, but
**may lose D** to llama.cpp's aggressive q4_0 KV quant. That nuance is the whole
reason to spike-and-measure rather than assume.

---

## 1. Baseline we are comparing against (current, live)

- Engine: `llama-swap` → `llama.cpp` (`llama-cpp-upstream`), single RTX 3090 Ti (24564 MiB).
- Model: Qwen3.8-27B **Q4_K_M GGUF** (~16 GB weights).
- Config: `ctx 262144 / parallel 3` → **fixed lane 87552 tok/req**, **q4_0 KV cache**.
- VRAM: **22809 MiB (92.9%)**, steady (llama.cpp pre-allocates all slot KV + compute
  buffers at load).
- Front door: **LiteLLM** (`llm-gateway`) → **`llm-queue`** admission controller
  (slots 3 / in-flight 4) → upstream. Callers reach `http://llama-cpp:8080`.
- Strengths to preserve: on-demand model **hot-swap** (llama-swap), q4_0 KV density,
  a working ChatML+tools template with `<think>` reasoning + `:nothink` variant,
  gateway-only routing enforced by `scripts/check-llm-gateway-routing.ps1`.

The rigidity we want to escape: **you must pre-pick `parallel N`, and total context
is split into N fixed lanes.** Idle lanes waste KV; a big single request is capped
at one lane.

---

## 2. How vLLM differs (the mechanism)

- **PagedAttention:** KV cache is stored in fixed-size *blocks* (like OS paging), not
  one contiguous region per slot. No fixed per-request lane; a **shared KV pool** is
  allocated block-by-block across all in-flight requests. ~<4% waste vs llama.cpp's
  pre-allocated lanes. → directly attacks objectives **B, C, D**.
- **Continuous (iteration-level) batching:** requests join/leave the running batch at
  *token* granularity; a finished request frees its blocks immediately for the next.
  → objective **A** (throughput) and **B** (grace under bursts).
- **Automatic prefix caching:** shared prefixes (our large tool schema, ~280 tok, and
  system prompts) are KV-cached across requests → less recompute.
- **Knobs that replace `ctx/parallel`:** `--max-model-len` (max tokens *any single*
  request may use — can be the full 262k), `--max-num-seqs` (max concurrent
  sequences), `--gpu-memory-utilization` (fraction of the card vLLM reserves at
  startup for weights + KV pool). You no longer divide context into N lanes; the
  scheduler packs dynamically up to KV availability. **This is exactly the "handles
  context uniquely / favors parallelism / more grace" behavior.**

---

## 3. Honest tradeoff analysis for THIS hardware (24 GB, single Ampere card, Windows/WSL2)

### Wins
- **Concurrency & throughput:** continuous batching should beat static lanes for the
  stack's bursty fan-out. No hand-tuned `parallel` vs lane tradeoff.
- **Dynamic context:** under low load a single request can use the *whole* KV pool
  (objective C), not a fixed 87552 lane. Under high load, requests pack tightly.
- **Multimodal bonus:** Qwen3.8 is vision-capable; vLLM's multimodal serving is
  first-class — potentially a cleaner vision path than llama.cpp's `mmproj`.
- **LiteLLM already speaks vLLM:** the `hosted_vllm/` provider prefix (already used in
  our config for bge-m3's `dimensions`) is literally for self-hosted vLLM OpenAI
  servers. Integration is first-class.

### Hard parts / honest caveats (the reasons this is "challenging on smaller hardware")
1. **GGUF won't cut it — requant required.** vLLM's GGUF path is experimental/slow.
   To run 27B on 24 GB we need a vLLM-native 4-bit quant: **AWQ or GPTQ (int4, runs
   fast on Ampere via Marlin kernels)**. FP8 weights want Ada/Hopper — the 3090 Ti is
   **Ampere, so no fast hardware FP8**; stick to int4 AWQ/GPTQ. → must source or
   produce a Qwen3.8-27B AWQ/GPTQ checkpoint (a real prerequisite, §4).
2. **KV density caveat (objective D may LOSE).** llama.cpp's **q4_0 KV ≈ 4.5 bits/elem**;
   vLLM KV is **fp16 (16-bit)** by default, or **fp8 (8-bit)** via
   `--kv-cache-dtype fp8`. So per-token KV, llama.cpp q4_0 is ~1.8× denser than vLLM
   fp8 and ~3.5× denser than fp16. **vLLM will likely hold FEWER total KV tokens per
   GB** — its advantage is *utilization efficiency and dynamic sharing*, not raw
   bit-density. Use `--kv-cache-dtype fp8` to narrow the gap (with a small quality/
   speed caveat to measure).
3. **Single-model VRAM pinning → lose hot-swap.** vLLM loads one model and holds
   `gpu_memory_utilization` of the card for its lifetime. llama-swap's on-demand
   swap between models goes away. *Acceptable here* — GPU 0 is effectively dedicated
   to `qwen36-27b` today (35B removed from LiteLLM; baseline is diagnostic) — but must
   be an explicit decision.
4. **Windows/WSL2/CUDA fragility & ops weight.** vLLM is Linux-only, needs
   Triton/custom CUDA kernels; runs as the official `vllm/vllm-openai` container under
   Docker Desktop's WSL2 GPU backend (same passthrough we already use for llama.cpp).
   Expect a heavier image, **longer cold start** (needs a long healthcheck
   `start_period`), and sensitivity to driver/CUDA/WSL versions.
5. **Tight fit.** After ~15–16 GB int4 weights + vLLM's CUDA-graph/activation
   overhead, the KV pool is ~6–8 GB. That is a smaller *token* pool than today's
   q4_0 262k — but dynamically shared. `--gpu-memory-utilization` tuning is the
   make-or-break knob on a 24 GB card.

### Net
Worth a **time-boxed spike** *if* the stack is concurrency-bound (its fan-out
patterns suggest yes). If the real need is a single 200k-token request, llama.cpp
q4_0 may still win — measure objective C/D before deciding.

---

## 4. Prerequisites & unknowns to resolve first (Phase 0, no downtime)

- [ ] **Arch support:** confirm the pinned `vllm/vllm-openai` version supports the
  Qwen3.8 text arch (and, if we want vision, the Qwen3.8-VL arch + its vision config).
- [ ] **Quant source:** find a prebuilt **Qwen3.8-27B AWQ or GPTQ (int4)** on HF; if
  none, produce one (AutoAWQ/AutoGPTQ needs a calibration pass — hours on GPU 0, plan
  a window). Record weights size + expected VRAM.
- [ ] **Kernels on Ampere:** verify Marlin int4 path is active for the chosen quant;
  decide `--kv-cache-dtype` = `auto`(fp16) vs `fp8` (bench both).
- [ ] **Template/parser mapping:** map our `chat-template.jinja` +
  `enable_thinking`/`:nothink` + tool format to vLLM flags: `--chat-template`,
  `--tool-call-parser` (`hermes`/`qwen3`), `--reasoning-parser` (`qwen3`), so vLLM
  emits the same `tool_calls` + `reasoning_content` shape our consumers expect. **This
  is the highest-risk integration item** — parity here is a go/no-go.
- [ ] **WSL2 GPU passthrough for vLLM** (already proven for llama.cpp; confirm for the
  vLLM image).

---

## 5. Integration design (how vLLM would sit in the stack)

- **New container `vllm-upstream`** on `llm-backend-net`, GPU 0, image
  `vllm/vllm-openai:<pinned>`, OpenAI server on `:8000`. Isolated behind the gateway
  exactly like `llama-cpp-upstream` (no host port beyond a loopback diagnostic).
- **Front door unchanged in spirit:** register a **new** LiteLLM model id (e.g.
  `qwen38-vllm`) with `model: hosted_vllm/…`, `api_base` → the queue-or-vLLM (below).
  **Do not hijack the live `qwen36-27b` id during the spike** — run side-by-side so
  rollback is "stop the new container."
- **`llm-queue` role — a real design decision:** vLLM does admission + fairness
  internally (continuous batching + `--max-num-seqs`). Options:
  - (a) **Drop the queue for the vLLM model** — LiteLLM → vLLM directly; rely on
    vLLM's scheduler; keep priority/attribution/ledger at LiteLLM. Simpler; loses our
    per-caller priority heap + wait-estimate observability.
  - (b) **Keep the queue** in front, raise its slots to vLLM's `--max-num-seqs`, and
    use it only for priority + `/observe` metrics (risk: double-queuing throttles
    vLLM's batching — the very §3.1 anti-pattern we avoid for llama-swap).
  - Lean (a) for the spike; revisit if we need the priority heap.
- **Routing guard update:** `scripts/check-llm-gateway-routing.ps1` only flags
  `llama-cpp-upstream|llama-cpp-embed-upstream` today; extend the `$badPattern` to
  include `vllm-upstream` so callers still can't bypass LiteLLM to reach vLLM
  directly (preserve the two-plane isolation). If the queue fronts vLLM, add a
  sanctioned `LLM_QUEUE_VLLM_UPSTREAM_BASE_URL` like the existing queue exception.
- **"Three places" rule (CLAUDE.md):** adding `vllm-upstream` means editing the
  compose file **+** `emergency-recovery.ps1/.bat` service inventory & sequences **+**
  the `/stack-map` reference doc together. The spike can skip recovery-script edits
  (it's a throwaway container), but a real cutover cannot.
- **Healthcheck:** long `start_period` (vLLM cold start + graph capture is minutes);
  probe `/health` (liveness), never a completion (avoid load probes, same lesson as
  LiteLLM `background_health_checks:false`).

---

## 6. Phased spike — reversible, time-boxed, GPU-0 window

The single 24 GB card means the 27B spike **cannot run alongside** the live
llama.cpp model. (GPU 1 is a 2080 Super, 8 GB, already ~full with bge-m3 — too small
for 27B.) So the throughput benchmark needs a **maintenance window on GPU 0**.

- **Phase 0 — Desk prep (no downtime):** resolve every §4 prereq; pin image + quant;
  write the `vllm-upstream` compose fragment (profile-gated so a normal `up` never
  starts it); pre-stage the AWQ/GPTQ weights on disk.
- **Phase 1 — Integration parity (short window):** stop `llama-cpp-upstream`; start
  `vllm-upstream` on GPU 0; register `qwen38-vllm` in LiteLLM. Validate **parity**:
  basic completion, `<think>` reasoning_content, `:nothink` equivalent, **tool-calling
  shape**, front-door path, VRAM fits < 94%. If parity fails (esp. tool/reasoning
  parser), **stop here** — restore llama.cpp, document, done.
- **Phase 2 — Benchmark vs baseline (same window):** run a repeatable load harness for
  objectives A–D against both engines (a fan-out of N∈{1,3,8,16} concurrent requests,
  mixed short/long prompts incl. a ~69k tool-synth prompt). Capture tok/s, p50/p95
  latency, max single-request context served, KV pool size / effective tokens-per-GB,
  peak VRAM. Restore llama.cpp at window end regardless.
- **Phase 3 — Decision gate:** compare to §7 criteria. **Go** → plan the real cutover
  (repoint `qwen36-27b`, settle the queue question, do the full three-places +
  routing-guard + recovery-script changes, keep GGUF for rollback). **No-go** →
  archive findings; llama.cpp was never modified, so there is nothing to undo.

---

## 7. Success / go-no-go criteria (fill from Phase 2 data)

Migrate **only if all hold**:
1. **Parity:** tool-calling + `reasoning_content` + `:nothink` behave identically to
   the consumers' expectations (little-coder, OWUI agentic, OB1). Non-negotiable.
2. **Fit:** steady-state VRAM ≤ ~94% at `--max-num-seqs` ≥ 3 with `--max-model-len`
   ≥ 87552 (i.e. no worse per-request ceiling than today).
3. **Concurrency win:** ≥ ~1.5× aggregate tok/s vs llama.cpp at 8 concurrent, **or**
   demonstrably graceful behavior (no 429s / no lane-exceeded 400s) where llama.cpp
   degrades — the "grace" the whole exercise is chasing.
4. **No context regression for real prompts:** a single request can still use ≥ 87552
   tokens (objective C), and the ~69k tool-synth prompt succeeds.

If (3)/(4) are marginal and (2) is tight, the honest call is **stay on llama.cpp** —
its q4_0 KV density + hot-swap may simply suit a single 24 GB card better.

---

## 8. Rollback / safety

- The spike **never modifies** the llama.cpp config or the live `qwen36-27b` id;
  `vllm-upstream` is a separate, profile-gated container and `qwen38-vllm` a separate
  LiteLLM id. Rollback = `docker compose stop vllm-upstream` + `start llama-cpp-upstream`.
- Keep the Q4_K_M GGUF and `.env.bak-pre-qwen38` on disk throughout.
- Time-box each GPU-0 window; announce downtime (chat model briefly unavailable while
  GPU 0 is held by vLLM during Phases 1–2).

---

## 9. Effort / risk summary

| Item | Effort | Risk |
|------|--------|------|
| Source/produce AWQ or GPTQ Qwen3.8-27B | M (calibration window) | Med — may not exist prebuilt |
| Template + tool + reasoning parser parity | M | **High** — the likeliest failure point |
| vLLM container + WSL2 GPU + VRAM tuning | M | Med — tight 24 GB fit |
| LiteLLM/queue wiring + routing-guard update | S | Low |
| Benchmark harness + window logistics | S | Low |

**Bottom line:** a bounded, reversible spike that answers a concrete question —
*does continuous batching + PagedAttention beat fixed lanes on our exact 24 GB
Ampere + concurrent workload, enough to justify losing q4_0-KV density and model
hot-swap?* Everything stays on llama.cpp until Phase 3 says otherwise.
