# Design — B2 Front-Ended Inference Queue (admission controller)

> **Status:** DESIGN / SCOPING — not built. Idea-stage with a verified problem
> diagnosis, an architecture recommendation, and a phased plan.
> **Date:** 2026-06-14
> **Anchored to:** [`guide-LiteLLM-Proxy.md`](guide-LiteLLM-Proxy.md) — this doc
> **revises guide §15** ("Backpressure & queue feedback"), replacing its
> *429 + `Retry-After`* admission model with a *hold-and-dispatch* queue.
> **Grounded in:** the 2026-06-14 429 diagnosis (memory
> `llm-429-llama-swap-concurrency`).

---

## 0. TL;DR

Under heavy fan-out (a deep-research run), callers got
`litellm.RateLimitError: Too many requests … Fallbacks=None`. We proved the
origin: **not** LiteLLM, **not** llama.cpp's slots — it's **llama-swap's
per-model `concurrencyLimit`** (default 10 in-flight) returning a flat
`429 Too many requests` once more than ~10 requests are simultaneously in flight.

A stopgap is **live** (raised `concurrencyLimit` 10 → 32). But that just moves a
blunt wall: past 32 it still drops requests, and below 32 it can pile long
requests behind only 3 slots with no ordering. The real fix is a **front-ended
admission queue we own** — between LiteLLM and the upstream — that *holds and
dispatches* requests (never drops within bounds), orders them by priority, and
exposes live state for readout / re-prioritisation / analytics.

This is the foundation for the longer goal: **LiteLLM as the brain that
understands all model state** — LiteLLM keeps the *historical* ledger (Postgres
spend-logs), the queue owns the *live* state (what's running, what's waiting,
in what order).

---

## 1. The verified problem (don't re-litigate — this is measured)

The inference path today:

```
callers ─> llama-cpp:8080 (alias) ─> llm-gateway (LiteLLM) ─> llama-cpp-upstream
                                       analytics front door     │ = llama-swap
                                                                 │   concurrencyLimit (default 10)
                                                                 │   → 429 "Too many requests" over cap
                                                                 ▼
                                                              llama.cpp
                                                              3 slots (--parallel 3)
                                                              + internal FIFO past 3
```

**Empirical proof** (direct to llama-swap `localhost:8080`, bypassing LiteLLM,
so the result is purely the llama layer):

| Concurrent requests | Result | Reading |
|---|---|---|
| 8 | 8 × `200` | llama.cpp queues internally past its 3 slots — fine |
| 48 (cap was 10) | 8 × `200`, 40 × `429` | slammed llama-swap's default cap |
| 24 (cap now 32) | 24 × `200`, 0 × `429` | stopgap works |
| 40 (cap now 32) | 31 × `200`, 9 × `429` | cap confirmed at ~32 |

429 body = plain text `Too many requests` (18 bytes) → llama-swap's own response
(llama.cpp returns JSON errors). LiteLLM has **no** rpm/tpm limits set, so it
relays the upstream 429 verbatim and reports `Fallbacks=None`.

**Two layers of queue already exist** — llama.cpp's internal FIFO (good, but
opaque and unordered) and llama-swap's hard cap (drops the overflow). B2 replaces
the *drop* with a *smart hold*.

### 1.1 Why the stopgap isn't the answer

`concurrencyLimit: 32` trades a **fast failure for a slow one**. Queued requests
cost no VRAM (KV isn't allocated until a slot frees), so memory is safe — but
with only 3 slots and requests that can run **9+ minutes** (observed), a deep
queue means the tail can wait long enough to hit a caller timeout (LiteLLM
`request_timeout=600s`, plus OWUI's own). And there is **no ordering**: an
interactive OWUI chat can sit behind 20 batch entity-worker requests. The cap is
a ceiling, not a scheduler.

---

## 2. Why a true queue — and why this revises guide §15

Guide **§15** already wanted backpressure, but adopted **429 + `Retry-After` +
per-key TPM/RPM caps**, driven by llama.cpp `/slots`. That approach has two
problems this design retires:

1. **Caller compliance burden (§15.5).** 429 + `Retry-After` only works if every
   caller honours the header and retries. §15.5 itself flags that
   `openbrain-entity-worker` (Deno `fetch`) does **not**, and others are "TBD" —
   i.e. the pattern needs a patch in every non-compliant caller, forever, for
   each new caller. A **hold-and-dispatch** queue needs **zero** caller changes:
   the request simply takes longer; the caller's normal HTTP wait covers it.
2. **The `/slots` signal is dead here.** §15.2 polls `http://llama-cpp:8080/slots`
   for occupancy, but llama-swap returns **404** for `/slots`
   (memory `litellm-proxy-status`). A queue we own doesn't need `/slots` — **it
   *is* the source of truth** for occupancy (it counts its own in-flight set).

§15's **capacity-planning value still holds** (§15.3 queries) — the queue emits
the same saturation/latency events into the same Postgres, so the
`llm saturation` / `scale signal` views in §9/§15.3 keep working, now fed by a
component that actually knows the live depth.

### 2.1 Why LiteLLM can't just do this itself (verified in 1.88.1 source)

We read the running gateway's source:

- **`scheduler.py:poll()`** releases a queued request the moment
  `health_deployments > 0`. The queue only engages while the deployment is in
  **cooldown**.
- **`cooldown_handlers.py:236`** — *single-deployment model groups don't cool
  down on 429 by default*. `qwen36-27b` is one deployment → never cools → the
  scheduler never holds. (Matches the observed pass-through.)
- **`model_rate_limit_check.py`** — the only other lever is **rpm/tpm**
  (per-*minute*) and it `raise`s with `num_retries=0`. Wrong unit: our constraint
  is *3 concurrent slots held for minutes*, not N-per-minute.
- Redis is **not** required — its `DualCache` is in-memory and we run a **single**
  `llm-gateway` instance. (Redis only matters for multi-instance LiteLLM.)

Conclusion: LiteLLM's native queue is built for the *multi-deployment, rpm-limited
cloud* case. A single, concurrency-bound, long-request llama.cpp backend needs a
**purpose-built admission controller**. That's B2.

---

## 3. Architecture — where the queue sits (B1 vs B2)

| | Request path | LiteLLM role | Verdict |
|---|---|---|---|
| **B1** | caller → **queue** → LiteLLM → upstream | queue is the caller-facing front door | ✗ demotes LiteLLM from front door; undoes the cut-in's `llama-cpp` alias design; splits the ledger from the gate |
| **B2** ✅ | caller → LiteLLM → **queue** → upstream | LiteLLM stays front door + ledger; queue is the admission/scheduling layer it forwards through | ✓ preserves the entire cut-in; LiteLLM "understands state" by reading the queue's API |

**Reconciling "front-ended queue":** the queue is the **front end to the
inference backend** (the admission gate in front of llama-swap), while **LiteLLM
remains the front door to callers**. Both are "front ends" of different things;
B2 keeps them in the right order.

### 3.1 The B2 path

```
callers ─> llama-cpp:8080 (alias)
            ─> llm-gateway (LiteLLM)         api_base → http://llm-queue:8080
                analytics ledger (Postgres)
                ─> llm-queue  (NEW)          the admission controller
                    semaphore + priority + live state + control API
                    ─> llama-cpp-upstream    llama-swap, concurrencyLimit: 0
                        ─> llama.cpp          3 slots
```

Two config changes make the queue the **sole gatekeeper**:
1. LiteLLM `api_base` for `qwen36-27b` → `http://llm-queue:8080/v1` (was
   `llama-cpp-upstream`).
2. llama-swap `concurrencyLimit: 0` (unlimited) — llama.cpp's internal FIFO stays,
   but the *admission decision* now belongs entirely to `llm-queue`, which admits
   only ~`slots + small headroom` at a time so **our** ordered queue holds the
   depth, not llama.cpp's opaque FIFO. (If llama-swap held a queue too, our
   priority ordering would be meaningless past the first few.)

### 3.2 Isolation & routing-guard implications (must address)

The two-plane isolation (memory `gateway-only-llm-routing-enforced`) makes
`llm-gateway` the **sole** bridge from `llm-net` to `llm-backend-net` (where
`*-upstream` lives). B2 inserts `llm-queue` into that bridge:

- `llm-queue` joins `llm-backend-net`; LiteLLM reaches it, it reaches
  `*-upstream`. It becomes a second element on the backend plane — **downstream**
  of LiteLLM, so the "LiteLLM is the front door" invariant holds.
- `scripts/check-llm-gateway-routing.ps1` fails if an inference endpoint points at
  a `*-upstream`. After B2, **LiteLLM's** api_base points at `llm-queue` (guard
  still green); only `llm-queue`'s own forward target is `*-upstream`. The guard
  needs a one-line allowance for `llm-queue` as a sanctioned upstream caller.

---

## 4. The `llm-queue` component

A small, single-responsibility async service. **Not** a general workflow engine —
an admission controller for one backend (generalise later).

### 4.1 Responsibilities

1. **Concurrency semaphore, release-on-completion** — the primitive nothing
   upstream has. Admit ≤ N in-flight (N ≈ slots + headroom, configurable); release
   a permit when a request *finishes*, not on a timer.
2. **Bounded priority wait-queue** — waiting requests held in a priority heap.
   Configurable `max_depth`; only when *full* does it return a bounded 429
   (honest degradation — never a fake `200`, per §15.1).
3. **Priority classes** — per caller / per virtual-key (interactive OWUI chat >
   batch entity-worker > scheduled wiki). Default policy + per-key overrides.
4. **Live state** — the authoritative view of `{running:[…], waiting:[…]}` with
   per-key counts, enqueue time, and wait duration.
5. **Control API** (the future vision) — read the queue, re-sort, bump/drop
   priority of a waiting request, pause/drain a key.
6. **Analytics events** — emit admit/start/finish/reject/wait events (into the
   LiteLLM Postgres or its own table) for evals over time.
7. **Transparent proxy** — for everything else it is a pass-through reverse proxy
   (streaming SSE preserved end-to-end; it must not buffer token streams).

### 4.2 API sketch (control plane is the differentiator)

```
POST /v1/chat/completions      # data plane — enqueue, await dispatch, proxy upstream
GET  /v1/models                # pass-through
GET  /queue                    # {running:[{id,key,model,started,elapsed}], waiting:[{id,key,prio,waited}]}
GET  /queue/stats              # depth, admit rate, reject count, p50/p95 wait, per-key shares
POST /queue/{id}/priority      # dynamic re-prioritise a waiting request
POST /queue/{id}/cancel        # drop a waiting request
POST /keys/{key}/policy        # set priority class / max-concurrency for a caller
GET  /healthz                  # liveness (no model load — like llama-swap router liveness)
```

`GET /queue` + `GET /queue/stats` are what the §9 `llm-traffic` OWUI pipe (and a
future dashboard) render — and what lets "LiteLLM understand live model state".

### 4.3 Implementation options

| Option | What | Fit |
|---|---|---|
| **(a) Custom async service** ✅ | FastAPI/Starlette + `asyncio.Semaphore` + `asyncio.PriorityQueue`, httpx streaming passthrough. ~few hundred LOC. | Best fit: we own readout/priority/analytics — exactly the stated future goals. SSE passthrough is the main care-point. |
| (b) Off-the-shelf proxy | nginx `limit_conn` / Envoy / a queue-proxy | Gives concurrency limiting but **no** priority, readout, or analytics surface — fails the future vision |
| (c) LiteLLM custom `async_pre_call_hook` that `asyncio`-waits | keep it in-process | Couples to LiteLLM internals, makes the gateway hold connections + own scheduling state; we already proved the native machinery resists this. Rejected. |

**Recommend (a)** — a dedicated `llm-queue` container. Single responsibility,
own API, swappable.

### 4.4 State store

In-memory is correct for **v1** (single instance, single backend). The live queue
is ephemeral by nature — a crash should *drain to upstream / fail open*, not
replay stale requests. Persist only the **analytics events** (append to LiteLLM
Postgres). Redis/multi-instance is a non-goal until there's a second gateway.

---

## 5. How "LiteLLM understands all model state"

Two complementary stores, one picture:

| Layer | Owns | Question it answers |
|---|---|---|
| **LiteLLM Postgres** (ledger) | *historical* — every request, key, tokens, latency, status | "demand last month", "p95 per model", "who hit the wall" (§15.3) |
| **`llm-queue`** (live) | *real-time* — running, waiting, order, per-key shares | "what's happening **right now**, and in what order" |

The OWUI `llm-traffic` pipe (§9) and any dashboard read **both**: ledger for
trends, queue API for the live board. "Dynamically adjusting what takes priority"
= `POST /queue/{id}/priority` and `POST /keys/{key}/policy`. "Evaluations and
analytics over time" = the emitted events joined against the ledger.

---

## 6. Phased plan

| Phase | Scope | Done when |
|---|---|---|
| **P0 — Spec** | This doc + operator decisions in §8 | Architecture + open questions agreed |
| **P1 — Minimal hold-and-dispatch** | `llm-queue` as a transparent proxy with a release-on-completion semaphore (N configurable) + bounded wait + 429-only-when-full. Repoint LiteLLM `api_base` → `llm-queue`; set llama-swap `concurrencyLimit: 0`. Single model (`qwen36-27b`). SSE passthrough verified. | A 48-burst → **0 × 429** (all queued+served) up to `max_depth`; SSE chat still streams; revert is one config line |
| **P2 — Priority + readout** | Per-key priority classes; `GET /queue` + `/queue/stats`. Interactive > batch proven (a chat jumps ahead of queued entity-worker load). | Live board shows running/waiting; priority ordering observable |
| **P3 — Dynamic control + analytics** | `POST /queue/{id}/priority`, `/keys/{key}/policy`, `/cancel`; emit events → Postgres; wire the §9 pipe / dashboard. | Operator can re-sort live; saturation/latency views populated |
| **P4 — Generalise** | Embed upstream (`llama-cpp-embed`, also default cap 10); multi-model/swap-aware admission; eval harness over the event log. | Both upstreams fronted; swap latency accounted for |

Each phase is independently revertible (the gate is one LiteLLM `api_base` line +
the llama-swap cap). P1 alone removes the user-facing 429 with ordering deferred.

---

## 7. Risks & constraints

1. **`llm-queue` is now in the critical inference path.** Every chat/embed flows
   through it. It must be robust, restart-fast (no model load — it's stateless
   proxy logic), and **fail-open** decision explicit (§8). A hang here = inference
   down. Mitigation: tiny codebase, `restart: unless-stopped`, healthz, and the
   one-line revert (api_base back to `*-upstream`).
2. **Tail latency vs caller timeouts.** A bounded queue still means waits. Tune
   `max_depth` against (3 slots × typical request seconds) so the deepest waiter
   stays under the tightest caller timeout. Long deep-research requests are the
   stress case — consider a separate low-priority lane with its own depth.
3. **SSE streaming passthrough.** The queue must not buffer token streams (would
   break OWUI's live render and add latency). httpx streaming + careful
   backpressure; covered by a P1 streaming test.
4. **Don't double-queue.** llama-swap `concurrencyLimit: 0` is required so depth
   lives in *our* ordered queue, not llama.cpp's opaque FIFO — otherwise priority
   past the first few is a no-op.
5. **Isolation guard.** Update `check-llm-gateway-routing.ps1` to sanction
   `llm-queue` → `*-upstream` while keeping LiteLLM the front door (§3.2).
6. **Three-place rule.** New container ⇒ compose + `emergency-recovery.ps1`/`.bat`
   (inventory + startup/shutdown order: `*-upstream` healthy → `llm-queue` →
   `llm-gateway` → callers) + stack-map. Run `/stack-map` to check drift.
7. **Scope discipline.** This is an admission controller for one backend, not
   "another LiteLLM" and not a workflow engine. Resist generalising past P4.

---

## 8. Open questions for the operator

- **(a) Fail-open or fail-closed** if `llm-queue` crashes/hangs? Fail-open
  (callers briefly hit the upstream directly via a fallback alias) preserves
  uptime but punches the isolation/ordering; fail-closed (hard 503, lean on
  restart) is simpler and matches how the search gateway behaves. *Recommend
  fail-closed for v1 — small, restart-fast component.*
- **(b) Admission N and `max_depth`?** Start N = 3 (slots) + 1 headroom, depth ≈
  24? Or N a touch higher to keep slots warm. Tunable; needs a first load cycle.
- **(c) Priority policy** — concrete classes/order. Proposed: `owui-chat` (interactive)
  > `mnemory` / `ob-mcp` (semi-interactive) > `lc-coder` > `ob-entity` / `ob-wiki`
  (batch). Reuses the §15.4 key taxonomy — but as **ordering**, not hard rpm caps.
- **(d) Per-key max-concurrency** (e.g. cap entity-worker at 1–2 in-flight so it
  can't own all 3 slots) in addition to global N? Cheap to add; strong starvation
  guard.
- **(e) Build language** — Python (matches the modules / LiteLLM ecosystem, fast
  to write) vs Go (matches llama-swap, lower overhead in the hot path)? *Recommend
  Python for v1 velocity; the hot path is I/O-bound proxying, not CPU.*

---

## 9. Relationship to existing docs

- **Revises guide §15** — 429+`Retry-After` (and its §15.5 caller-compliance
  burden) → hold-and-dispatch. §15.3 capacity queries survive, now fed by
  `llm-queue` events instead of a dead `/slots` poll.
- **Complements guide §9** (`llm-traffic` pipe) — the pipe gains a live board from
  `GET /queue` alongside the historical ledger views.
- **Extends the cut-in** ([`CUT-IN-READY.md`](CUT-IN-READY.md)) — same transparent
  philosophy: callers never change; the new layer slots in behind the gateway.
- **Stopgap of record** — `concurrencyLimit: 32` in `config/llama-swap.config.yaml`
  holds the line until P1 lands; P1 sets it to `0`.
- **Memory** — `llm-429-llama-swap-concurrency` (the diagnosis),
  `gateway-only-llm-routing-enforced` (isolation/guard), `litellm-proxy-status`
  (front-door topology), `llama-swap-perf-tuning` (slots/VRAM).
