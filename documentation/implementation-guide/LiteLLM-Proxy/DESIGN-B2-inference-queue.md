# Design — B2 Front-Ended Inference Queue (admission controller)

> **Status:** P1–P4 BUILT + DEPLOYED + VERIFIED LIVE (2026-06-14). Component at
> [`llm-queue/`](../../../llm-queue/) (FastAPI + asyncio; 33 tests pass; lint
> clean). Live: LiteLLM **chat + embed** `api_base` → `http://llm-queue:8080/v1`
> (both `qwen36-27b` variants + all three `bge-m3` aliases), llama-swap
> `concurrencyLimit: 0`, `enforce_budget: true`, analytics SQLite store, §9 OWUI
> live board. Per-phase proof in §6. Original design below is the record.
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
1. LiteLLM `api_base` → `http://llm-queue:8080/v1` (was `llama-cpp-upstream`).
   **Both** `qwen36-27b` **and** `qwen36-27b:nothink` carry this `api_base`
   ([`config/litellm.config.yaml`](../../../config/litellm.config.yaml) lines
   24/29) and share the one upstream slot — repoint **both** entries or the
   `nothink` variant bypasses the queue.
2. llama-swap `concurrencyLimit: 0` (unlimited) — llama.cpp's internal FIFO stays,
   but the *admission decision* now belongs entirely to `llm-queue`. **Headroom
   discipline (reconciles with §7.4):** admit `N = slots` (3), or at most
   `slots + 1`. Anything admitted beyond a free slot sits in llama.cpp's
   *unordered* internal FIFO — so headroom is a deliberate, minimal trade
   (one in-flight extra to mask the completion→dispatch gap and keep slots from
   idling), **not** a buffer. Keep `N ≤ slots + 1` or the priority ordering is a
   no-op for everything past the first few. The real depth lives in **our**
   ordered heap.

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
  needs a one-line allowance for `llm-queue` as a sanctioned upstream caller —
  add the queue's compose service path to `$allowPathLike` (or, better, narrow the
  allowance to the specific `*.config` file that holds `llm-queue`'s forward
  target, so the queue's *other* files still get scanned).

### 3.3 Network reachability — the read & analytics plane (GAP to resolve in P1)

The isolation that makes B2 clean also creates a reachability hole the design
must close, because **the live board and the analytics sink sit on the wrong
side of the bridge**:

| Flow | Source net | Target net | Reachable today? |
|---|---|---|---|
| LiteLLM → `llm-queue` (data plane) | `llm-backend-net` | `llm-backend-net` | ✅ |
| `llm-queue` → `*-upstream` (forward) | `llm-backend-net` | `llm-backend-net` | ✅ |
| OWUI `llm-traffic` pipe → `GET /queue` (live board, §5/§9) | `llm-net` | `llm-backend-net` | ❌ **no route** |
| `llm-queue` → `llm-gateway-db` (analytics events, §4.1.7/§4.4) | `llm-backend-net` | `llm-net` (db is `llm-net`-only) | ❌ **no route** |

Putting `llm-queue` on **both** nets would fix reachability but **re-exposes the
mutating control API** (`POST /queue/{id}/priority`, `/cancel`,
`/keys/{key}/policy`) to every caller on `llm-net` — breaking the
"LiteLLM is the front door / callers can't reach the backend plane" invariant.
**Resolution (recommended, keeps `llm-queue` on `llm-backend-net` only):**

1. **Read-only state via LiteLLM pass-through.** Surface `GET /queue`,
   `/queue/stats`, `/queue/estimate` to `llm-net` consumers through a LiteLLM
   `pass_through_endpoints` entry forwarding to `llm-queue` — the *exact* pattern
   the gateway config already documents for reviving `/slots`
   ([`config/litellm.config.yaml`](../../../config/litellm.config.yaml) §general_settings
   note). This keeps LiteLLM the single front door and never exposes the mutating
   verbs.
2. **Mutating control endpoints stay un-bridged.** They are reachable only from
   `llm-backend-net` (i.e. operator via `docker exec` for now). When a dashboard
   needs them, add them as *authenticated* pass-throughs later — never as open
   `llm-net` routes.
3. **Analytics sink decision (settle in P1).** Either (a) give `llm-queue` a
   read/write foot to the Postgres *only* — but the db is on `llm-net`, so this
   reopens (1)'s exposure; or (b) **preferred:** `llm-queue` writes to its **own
   table** in its **own** small store and the join in §5 happens at query time, or
   it ships events to LiteLLM via a callback. Do **not** write into LiteLLM's
   `LiteLLM_SpendLogs` (LiteLLM owns that schema and migrates it — see §10).

---

## 4. The `llm-queue` component

A small, single-responsibility async service. **Not** a general workflow engine —
an admission controller for one backend (generalise later).

### 4.1 Responsibilities

1. **Concurrency semaphore, release-on-completion** — the primitive nothing
   upstream has. Admit ≤ N in-flight (N ≈ slots + headroom, configurable); release
   a permit when a request *finishes*, not on a timer.
2. **Priority wait-queue with time-based admission** — waiting requests held in a
   priority heap. Admission is gated by **projected wait vs the caller's
   acceptable-wait budget** (§8b), not a fixed depth, with depth ≈ 24 as a coarse
   backstop. Reject *before* enqueuing when over budget — honest degradation,
   never a fake `200` (cf. §15.1), with an informative body (§4.5).
3. **Rolling completion-time metric** — per model, the mean/EWMA duration of the
   last ~5 completed requests (`T`). Feeds the wait estimate
   `ceil(position_ahead / P) × T`. The single most load-adaptive signal in the
   service — exposed on the queue API and in response headers.
4. **Priority classes** — per caller / per virtual-key (interactive OWUI chat >
   batch entity-worker > scheduled wiki). Default policy + per-key overrides.
5. **Live state** — the authoritative view of `{running:[…], waiting:[…]}` with
   per-key counts, enqueue time, wait duration, and current wait estimate.
6. **Control API** (the future vision) — read the queue, re-sort, bump/drop
   priority of a waiting request, pause/drain a key.
7. **Analytics events** — emit admit/start/finish/reject/wait events with the
   measured duration and estimate-vs-actual into **`llm-queue`'s own table**
   (same Postgres instance is fine; **not** LiteLLM's `LiteLLM_SpendLogs` — that
   schema is LiteLLM-owned and migrated, §10) for evals over time.
8. **Transparent proxy** — for everything else it is a pass-through reverse proxy
   (streaming SSE preserved end-to-end; it must not buffer token streams).

### 4.2 API sketch (control plane is the differentiator)

```
POST /v1/chat/completions      # data plane — enqueue (or reject if over budget), await dispatch, proxy
GET  /v1/models                # pass-through
GET  /queue                    # {running:[{id,key,model,started,elapsed}],
                               #  waiting:[{id,key,prio,waited,est_wait_s}], avg_T_s, P}
GET  /queue/stats              # depth, admit/reject rate, p50/p95 wait, est-vs-actual error, per-key shares
GET  /queue/estimate?key=…     # projected wait for a hypothetical request from this key (pre-flight)
POST /queue/{id}/priority      # dynamic re-prioritise a waiting request
POST /queue/{id}/cancel        # drop a waiting request
POST /keys/{key}/policy        # set priority class / max-concurrency / acceptable_wait_s for a caller
GET  /healthz                  # liveness (no model load — like llama-swap router liveness)
```

`GET /queue` + `GET /queue/stats` are what the §9 `llm-traffic` OWUI pipe (and a
future dashboard) render — and what lets "LiteLLM understand live model state".
Each `POST /v1/chat/completions` response also carries `X-Queue-Wait`,
`X-Queue-Position`, `X-Queue-Avg-T` headers so a caller can see what it waited for.

### 4.5 Honest, informative rejection (replaces the bare `Too many requests`)

When a request is rejected (projected wait > budget, or the depth-24 backstop),
`llm-queue` returns a **real** `429`/`503` (per §15.1 — never a fake `200`) but
with a **structured, actionable body** and a `Retry-After` set from the live
estimate — the opposite of today's opaque `Too many requests` / `Fallbacks=None`:

```json
{
  "error": {
    "type": "queue_over_budget",
    "message": "qwen36-27b saturated: projected wait ~95s exceeds this service's 30s budget.",
    "model": "qwen36-27b",
    "projected_wait_s": 95,
    "acceptable_wait_s": 30,
    "queue_depth": 18,
    "avg_completion_s": 16,
    "slots": 3,
    "retry_after_s": 95
  }
}
```

`Retry-After: 95` rides in the header too. **LiteLLM must relay this body
faithfully** rather than re-wrapping it as a generic `RateLimitError` — verify in
P1 whether LiteLLM passes the upstream error body through (it should, with no
fallbacks configured); if it mangles it, add a thin LiteLLM exception mapping so
the caller sees the real reason. This is what the operator asked for: an error
that "better suits what's going on."

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
is ephemeral by nature: on crash, in-flight and waiting requests die with the
process — the caller sees the **fail-closed** hard error of §8(a) and
`restart: unless-stopped` brings the queue back in seconds. State is **never**
persisted and replayed (that would dispatch stale requests against callers that
already timed out). *(Earlier drafts said "fail open" here — that conflicts with
the settled §8(a) fail-**closed** decision; the only thing that "fails open" is
durability, i.e. we deliberately drop live state, not routing.)* Persist only the
**analytics events**, and to **`llm-queue`'s own table**, not LiteLLM's
schema-managed `LiteLLM_SpendLogs` (§10). Redis/multi-instance is a non-goal until
there's a second gateway.

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
| **P0 — Spec** | This doc + operator decisions in §8 | ✅ Architecture + open questions agreed |
| **P1 — Hold-and-dispatch + time estimate** ✅ DONE 2026-06-14 | `llm-queue` as a transparent proxy: release-on-completion semaphore (N), rolling completion-`T` metric, projected-wait estimate, soft depth-24 backstop, and the informative 429 body + `Retry-After`/`X-Queue-*` headers (§4.5). Repoint LiteLLM `api_base` → `llm-queue`; set llama-swap `concurrencyLimit: 0`. Single model (`qwen36-27b`). SSE passthrough verified. | ✅ **PROVEN LIVE:** direct-to-queue 24-burst → **24×200 / 0×429**; 48-burst → 28 served + 20 structured 429s (`queue_over_depth` body w/ `projected_wait_s`/`queue_depth`/`slots`/`retry_after_s`); **through LiteLLM 48-burst → 0×429** (LiteLLM `num_retries:3` retries the queue 429 → hold-and-dispatch absorbs the burst, the original deep-research 429 cause now resolves all-200); SSE streams unbuffered end-to-end; `X-Queue-*` headers present; embeddings unaffected (bypass queue); no permit leak (turnover confirmed); revert is one config line. **§4.5 caveat found:** LiteLLM re-wraps the structured body into its `RateLimitError` envelope but the human reason survives in the message; full live state stays on `X-Queue-*` + `GET /queue`. |
| **P2 — Priority + per-service budgets + readout** ✅ DONE 2026-06-14 | Per-key priority classes (§8c); per-service `acceptable_wait_s` budgets gating admission (§8b, `enforce_budget: true`); per-key max-concurrency (§8d); `GET /queue` + `/queue/stats` + `/queue/estimate` via a GET-only `/observe/*` LiteLLM pass-through. | ✅ **PROVEN LIVE:** priority ordering — under contention `owui-chat` (arriving 0.15s *later*) mean wait **1.26s vs `ob-entity` 4.18s** (interactive jumped the batch queue); budget gate — `owui-chat` rejected `queue_over_budget` with `est_wait=60s` when projected > its 30s budget; readout — `GET /observe/queue/estimate` reachable from `llm-net` through the gateway pass-through (mutating verbs stay un-bridged on `llm-backend-net`); per-key cap unit-tested. **Attribution caveat (VERIFIED, §10.3.2):** LiteLLM's openai-client path strips the caller key to `dummy` (not forwarded), so per-caller priority THROUGH the gateway defaults unless the caller sets the OpenAI `user` field (llm-queue reads it as a fallback); direct callers + `user`-setting callers attribute today; full attribution tightens with master_key/virtual-keys. Mechanism is live and proven. |
| **P3 — Dynamic control + analytics** ✅ DONE 2026-06-14 | `POST /queue/{id}/priority`, `/keys/{key}/policy`, `/cancel`; emit admit/start/finish/reject events with estimate-vs-actual → llm-queue's OWN SQLite store (NOT LiteLLM's Postgres, §4.4); wire the §9 pipe / dashboard; audit caller timeouts ≥ their budget. | ✅ **PROVEN LIVE:** dynamic re-prioritise — a request at waiting **position 14 (rank 3) bumped to rank 0 jumped to the FRONT** of the heap; `POST /keys/{key}/policy` sets a runtime class (verified read-back); `/cancel` wired (404 on unknown); analytics → `/data/events.db` (named volume, unprivileged-writable) capturing est-vs-actual; the §9 `modules/llm-traffic` OWUI pipe now renders a **live board** (running/waiting/free-slots/avg-T/in-flight-by-key/held) from `/observe/queue` alongside the ledger; `scripts/eval_events.py` reports per-model reject-rate / wait p50·p95 / duration / estimate-error. **Caller-timeout audit:** budgets (owui-chat 30s … batch 600s) ≤ LiteLLM `request_timeout` 600s ≤ OWUI `AIOHTTP_CLIENT_TIMEOUT` 3600s — contract holds. |
| **P4 — Generalise** ✅ DONE 2026-06-14 | Embed upstream fronted; multi-model registry; eval harness over the event log. | ✅ **PROVEN LIVE:** embed `api_base` (bge-m3 / bge-m3-f16.gguf / qllama/bge-m3) → llm-queue; **60-concurrent embedding burst → 60/60 valid 1024-dim, 0 rejects** (no regression — the plain-llama.cpp embed upstream has no llama-swap cap, so the embed queue uses a GENEROUS backstop 256 + no budget gate + LiteLLM retry); registry keys per-model (`qwen36-27b` + `bge-m3`, each with own slots/N/backstop/budget — §10.2 insurance now exercised); `eval_events.py` runs over the live log (bge-m3: 0 rejects, 0.09s mean duration, 0.55s est-error). **Swap latency:** rolling-T absorbs a post-swap slow first request; 35B is unregistered so no swaps occur today. |

Each phase is independently revertible (the gate is one LiteLLM `api_base` line +
the llama-swap cap). P1 alone removes the user-facing 429 with ordering deferred.

---

## 7. Risks & constraints

1. **`llm-queue` is now in the critical inference path.** Every chat/embed flows
   through it. It must be robust, restart-fast (no model load — it's stateless
   proxy logic), and **fail-open** decision explicit (§8). A hang here = inference
   down. Mitigation: tiny codebase, `restart: unless-stopped`, healthz, and the
   one-line revert (api_base back to `*-upstream`).
2. **Tail latency, starvation & the caller-timeout contract.** The time-based
   budget (§8b) is the primary control: a request is admitted only if its
   *projected* wait fits its service's budget, and rejected-with-reason otherwise —
   so nothing silently starves, even as dynamic re-prioritisation pushes
   low-priority items back. Two follow-ons: (i) the **caller's HTTP timeout must
   be ≥ its budget** or it'll abandon a request the queue intended to serve — audit
   in P3; (ii) the rolling `T` is only as good as its window — a single 9-minute
   deep-research request can skew `T` and over-reject the next arrivals; consider
   per-priority-class `T` or trimming outliers. Long deep-research is the stress
   case — likely its own low-priority class with a generous budget.
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

## 8. Operator decisions (settled 2026-06-14)

- **(a) Fail-closed.** If `llm-queue` crashes/hangs it returns a hard error and
  leans on `restart: unless-stopped` — no fallback alias around it. Matches the
  search-gateway behaviour; keeps isolation/ordering intact. The component is
  small and restart-fast (stateless proxy logic, no model load), so the blast
  radius of a restart is seconds.

- **(b) Admission is TIME-based, not a fixed count.** The gate is a projected
  **wait budget per service**, not a hard depth. Mechanics:
  - **Rolling completion metric** — track the mean (or EWMA) duration of the last
    ~5 completed requests *per model*, call it `T`. This adapts to whatever the
    backend is actually doing (a `T` of 5s vs 90s changes everything).
  - **Projected wait for a new request** = `ceil(position_ahead / P) × T`, where
    `P` = parallel slots (3) and `position_ahead` = requests that will run *before*
    it given current queue + its priority class. Example: 24 ahead, P=3, T=5s →
    `24/3 × 5 = 40s`.
  - **Per-service acceptable-wait budget** — each caller/key declares a tolerable
    wait (interactive `owui-chat` short, e.g. 15–30s; batch `ob-wiki`/`ob-entity`
    long, minutes). At **enqueue**, if `projected_wait > budget` → **reject now**
    with an informative error (see §4.5) rather than enqueue-and-starve. This is
    also the **anti-starvation guard**: dynamic re-prioritisation pushes
    lower-priority items further back, so their projected wait *grows* — the budget
    check rejects them honestly instead of letting them rot behind newer
    high-priority arrivals.
  - **Soft backstop depth ≈ 24** for now — a coarse ceiling while the time-based
    model is the real gate; revisit once `T` distributions are observed.
  - **Two-sided contract:** a caller's HTTP timeout should be ≥ its declared
    acceptable-wait budget, so the service honours the wait it asked for instead
    of timing out mid-queue. Audit per caller during P2/P3.

- **(c) Priority policy** — `owui-chat` (interactive) > `mnemory` / `ob-mcp`
  (semi-interactive) > `lc-coder` > `ob-entity` / `ob-wiki` (batch). Reuses the
  §15.4 key taxonomy as **ordering**, not hard rpm caps.

- **(d) Per-key max-concurrency — yes.** Cap batch callers (e.g. `ob-entity`) at
  1–2 in-flight so they can't own all 3 slots. Layers on top of global admission;
  strong starvation guard alongside (b).

- **(e) Python.** For codebase consistency (the `modules/` services + LiteLLM
  ecosystem). Hot path is I/O-bound proxying, so the language overhead is moot.

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

---

## 10. Audit — SOLID / architecture / security (2026-06-14)

Audited against the live workspace (compose, `litellm.config.yaml`,
`llama-swap.config.yaml`, the routing guard). **Verdict: the B2 architecture is
correct and well-grounded** — admission-control-behind-the-gateway is the right
pattern, the LiteLLM-source analysis (§2.1) is verified, and the time-based gate
(§8b) is a genuine improvement over the §15 `Retry-After` model. The fixes below
were folded into §3–§4 above; the rest are recommendations for P1/P2.

### 10.1 Corrections applied inline

- **Fail-open ↔ fail-closed contradiction (fixed §4.4).** §4.4 said a crash should
  "fail open"; §8(a), the *settled* operator decision, says **fail-closed**.
  Reconciled: only durability "fails open" (live state is dropped, never
  replayed); routing is fail-closed.
- **`:nothink` variant would have bypassed the queue (fixed §3.1).** The repoint
  named only `qwen36-27b`, but `qwen36-27b:nothink` carries its own `api_base`
  to the same upstream. Both must move.
- **Headroom vs no-double-queue (fixed §3.1).** §3.1 ("slots + small headroom")
  lightly contradicted §7.4 ("don't double-queue"). Pinned to `N ≤ slots + 1`
  with the trade made explicit.
- **Analytics table coupling (fixed §4.1.7/§4.4).** Writing into LiteLLM's
  `LiteLLM_SpendLogs` couples `llm-queue` to a schema LiteLLM **owns and
  migrates** — a Liskov/encapsulation violation that will break on a LiteLLM
  upgrade. Use `llm-queue`'s **own** table.
- **Reachability gap (added §3.3).** The live board (OWUI→queue) and analytics
  sink (queue→`llm-gateway-db`) both cross the `llm-net`↔`llm-backend-net`
  boundary with **no route**. Resolved via LiteLLM read-only pass-through + own
  store, keeping the queue on `llm-backend-net` only.

### 10.2 SOLID / encapsulation / expandability

- **Decompose `llm-queue` internally (SRP within the service).** §4.1 lists 8
  responsibilities; they cohere as "admission + observability of one backend,"
  but the *implementation* should split into clear modules so each varies
  independently: **(i) transport** (httpx streaming reverse-proxy, SSE
  passthrough), **(ii) admission/scheduler** (semaphore + priority heap +
  time-budget gate), **(iii) policy** (priority classes, per-key budgets/caps),
  **(iv) metrics** (rolling `T`, event emission), **(v) control API**. P1 needs
  only (i)+(ii)+(iv-lite); the split keeps P2/P3 additive.
- **Make priority a Strategy (Open/Closed).** Encode the ordering (§8c) behind a
  `PriorityPolicy` interface resolving `(key, model, request) → class`, loaded
  from config. New callers/classes then plug in via config, not code edits —
  directly serves "dynamically adjust priority" (§5) and P4 generalisation.
- **Key the in-flight set and `T` by model *now*, even with one model.** P4
  generalises to the embed upstream and swap-aware admission. If v1 hardcodes a
  single semaphore and a single `T`, P4 is a rewrite; if v1 uses a
  `model → {semaphore, T, heap}` registry (with exactly one entry), P4 is
  configuration. Cheap insurance for the stated expandability requirement.
- **Single-source `slots`/`P`.** The projected-wait math hardcodes `P=3`, but
  `--parallel` lives in `llama-swap.config.yaml` and `N`/`concurrencyLimit` there
  too. Duplicating these as queue env invites drift. Prefer reading
  `n_parallel`/slot count from the upstream's `/props` (llama.cpp exposes it) at
  startup, or document the three-place coupling (`--parallel` ↔ queue `N` ↔ queue
  `P`) as a tuning invariant.

### 10.3 Security concerns

1. **Control plane is unauthenticated — keep it un-bridged.** `POST /queue/{id}/priority`,
   `/cancel`, `/keys/{key}/policy` mutate scheduling for *all* callers. There is no
   auth in the design. This is **acceptable only** while the queue lives on
   `llm-backend-net` and the mutating verbs are **not** pass-through'd (§3.3) —
   i.e. reachable solely by `docker exec`. **Invariant to state in the doc:** the
   mutating control API must never be exposed on `llm-net` or a host port without
   authentication. (Read-only state may be pass-through'd; mutation may not.)
2. **Priority is cooperative, not a trust boundary.** The gateway runs
   **permissive (no `master_key`)** and callers self-assert plaintext keys
   (`litellm-proxy-status`). So a caller can present a high-priority key string
   (e.g. `owui-chat`) to **jump the queue** or set its own `acceptable_wait_s`.
   Document this explicitly: priority/budget ordering is an **optimization among
   trusted internal callers**, not a security control. It tightens automatically
   when `master_key` + real virtual keys land. Until then, derive priority
   **server-side from the attributed key**, and **never** from a client-supplied
   `X-Priority`-style request header (header-trust = injection).
3. **Connection-exhaustion / hold-and-dispatch DoS.** Holding waiters open means
   LiteLLM and `llm-queue` each pin one socket per waiting request. The §8(b)
   time-budget reject and the depth-24 backstop are the mitigations — but frame
   the backstop as also bounding **held FDs/sockets**, not just wait time, and add
   a hard absolute cap on total concurrent connections (independent of per-service
   budget) as a safety valve.
4. **Client-disconnect eviction (correctness *and* resource safety).** If a caller
   (or LiteLLM) drops while queued, the queue must detect the closed connection
   and **evict the entry / abort the upstream forward** — otherwise it dispatches
   a dead request and burns a slot. Starlette/httpx expose disconnect; make this a
   P1 requirement (and a test), not an afterthought.

### 10.4 Operational gaps to fold into the plan

- **Graceful drain on `SIGTERM`.** On `docker compose stop`/recreate, stop
  admitting, let in-flight finish within a short grace window, then exit — standard
  for an in-path proxy and cheap. Pair with the §7.6 recovery ordering.
- **SSE keep-alive during the wait.** A streaming request can sit in queue for tens
  of seconds with **zero bytes**, risking idle-read timeouts in OWUI/intermediaries
  even when the caller's *total* timeout is generous (§8b). Emit periodic SSE
  comments (`: queued, position N` / heartbeat) while waiting — this also revives
  guide §15.2's "queue position" UI nicety for free. Non-streaming callers can't
  receive partial bytes, so for them the §8b budget/timeout contract is the only
  lever (already covered).
- **Estimate-accuracy as an explicit eval (P3).** The emitted estimate-vs-actual
  (§4.1.7) should drive a tuning loop on `T`'s window size and outlier trimming
  (§7.2) — call it out as a P3 acceptance signal, not just data collected.
