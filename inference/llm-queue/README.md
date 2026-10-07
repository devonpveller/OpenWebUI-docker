# llm-queue — B2 front-ended inference admission controller

Holds-and-dispatches inference requests between **LiteLLM** (the caller-facing
front door) and the **llama.cpp backend** (`llama-cpp-upstream` = llama-swap),
instead of letting llama-swap drop the overflow with a flat `429 Too many
requests`:

```
callers → llama-cpp:8080 (alias) → llm-gateway (LiteLLM) → llm-queue → llama-cpp-upstream
                                     analytics ledger        admission     llama-swap → llama.cpp
                                                             (this service)  (concurrencyLimit: 0)
```

**Design:** [`../../../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md`](../../../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md)

## What it does

- **Release-on-completion semaphore** — admits ≤ N in-flight to the upstream
  (`N = slots + 1` headroom), releasing a permit when a request *finishes*.
- **Priority wait-heap** — waiting requests ordered by class (interactive chat >
  batch), dispatched highest-priority-first; per-key max-concurrency so a batch
  caller can't own all the slots.
- **Rolling completion-`T` metric** + projected-wait estimate
  (`ceil(position_ahead / P) × T`), exposed on `X-Queue-*` response headers and
  `GET /queue`.
- **Honest, structured rejection** (`type`, `projected_wait_s`, `queue_depth`,
  `slots`, `retry_after_s`) instead of the opaque `Too many requests`.
- **Transparent SSE passthrough** — token streams relayed unbuffered.
- **Client-disconnect eviction** — a waiter that drops is removed before it burns
  a slot.
- **Prompt release on an early end** (ao-queue, 2026-10-07) — uvicorn speaks ASGI
  spec 2.3, so Starlette *cancels* the streaming task when the client hangs up.
  The end-of-stream cleanup (`routes/data.py`) runs under
  `anyio.CancelScope(shield=True)` and releases the permit and the held
  connection before any I/O (the upstream close and the events write follow,
  bounded at 10 s). Before, the events-store write raised `CancelledError` first
  and the slot stayed held until the reaper's 1,200 s TTL — gym-002 went 63 → 126
  of 128 and shed. A `finish` event with status 502 now carries `reason`
  `client_disconnect` or `upstream_error`. The reaper stays as the backstop.
  Test: `tests/test_disconnect_leak.py`.
- **One release, decided by the waiter's state** (ao-queue round 2) — a
  `_Lease` per admitted request gives back the heap entry or model permit, the
  held slot and the upstream response exactly once. It reads `waiter.dispatched`
  (enqueue may dispatch before the generator sees it — a client that hung up
  before the first byte used to leak the PERMIT forever), and the response
  wrapper closes it too when the body generator never ran. Backstop:
  `ModelQueue.reap_stale_running` returns a permit with no upstream byte for
  `LLM_QUEUE_CONN_TTL_S`; counted on `/healthz` `permits_reaped_total`.
- **Shedding episodes** (`shedding.py`) — a capacity refusal or held ≥ 75 % of
  the cap opens an episode, held ≤ 50 % with no refusal for 300 s clears it; one
  log line + one event per transition, state on `/healthz` → `shedding`, paged
  once per episode by the host watchdog (see `../README.md`, *Operate*).

## Architecture (modules)

| Module | Responsibility |
|--------|----------------|
| `transport.py` | httpx streaming reverse-proxy, SSE passthrough |
| `scheduler.py` | per-model semaphore + priority heap + time-budget gate |
| `policy.py` | priority classes, per-key budgets/caps (a Strategy) |
| `metrics.py` | rolling-`T` |
| `events.py` | analytics events → llm-queue's OWN store (never LiteLLM's schema) |
| `registry.py` | model → queue routing + global connection cap |
| `shedding.py` | shedding episodes (start / clear with hysteresis) for `/healthz` and the watchdog |
| `routes/` | data plane (admission + proxy), control plane, health |

## Tuning invariant (three-place coupling)

Keep these in sync — see `config.py`:

```
llama-swap --parallel  ==  LLM_QUEUE_SLOTS (P)         # inference/config/llama-swap.config.yaml = .env LLAMA_SWAP_QWEN36_27B_N_PARALLEL
LLM_QUEUE_MAX_IN_FLIGHT (N)  <=  P + 1                 # headroom discipline
llama-swap concurrencyLimit  ==  0                     # the queue is the sole gate
```

**Pending (2026-09-25, not built):** this coupling is why the queue cannot see a
different backend. `P` is one hand-set number for one local upstream; a cloud model
group behind LiteLLM, or a second engine, is invisible to the lanes. The change and
its reasoning are in the plan store:
`../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md`
§10.4 "Per-backend capacity" (raised by the research-workbench effort, D16).

## Development & iteration

```pwsh
# tests (pure logic + ASGI burst sims, no Docker needed)
cd inference/llm-queue; python -m venv .venv; ./.venv/Scripts/python -m pip install -e ".[dev]"
./.venv/Scripts/python -m pytest -q
./.venv/Scripts/python -m ruff check src tests

# rebuild + redeploy the container (no source mount — code is baked).
# ALWAYS name the plane file: since K.1 the root project is the network anchor
# and owns no services, so a bare `docker compose` from the repo root does
# nothing here. Run from the REPO ROOT:
docker compose -f inference/docker-compose.yml build llm-queue
docker compose -f inference/docker-compose.yml up -d llm-queue

# burst verifier (run inside a container that can reach the target)
docker exec -i llm-queue   python - http://localhost:8080 24 < inference/llm-queue/scripts/burst.py   # direct
docker exec -i llm-queue   python - http://llm-gateway:8080 48 < inference/llm-queue/scripts/burst.py  # via LiteLLM
```

## Revert (one config line each)

1. `inference/config/litellm/model_list/local.yaml`: both `qwen36-27b` `api_base` → `http://llama-cpp-upstream:8080/v1`
   (the model_list left `litellm.config.yaml` at sl-inference-split, 2026-09-19 — that file is now the BASE config only)
2. `inference/config/llama-swap.config.yaml`: `concurrencyLimit: 0` → `32`

…then, from the repo root,
`docker compose -f inference/docker-compose.yml restart llm-gateway llama-cpp-upstream`.

`restart` is deliberate and correct here: both steps EDIT THE CONTENTS of files that stay
where they are, so the existing binds still resolve and there is nothing to re-render.
(Do not "upgrade" it to `up -d` after reading `../documentation-plans-ai-stack/journal/notes/stack-layers-sl-colo-inference-findings.md`
F14 — that hazard is about a bind whose SOURCE PATH has moved, which is a different case.)

## Operational notes

- **Fail-closed** (design §8a): if the queue crashes it returns a hard error and
  leans on `restart: unless-stopped`. No fallback alias around it — keeps
  isolation/ordering intact. Restart-fast (stateless, no model load).
- **Network:** `llm-backend-net` only. The mutating control API
  (`POST /queue/{id}/priority`, `/cancel`, `/keys/{key}/policy`) is reachable
  ONLY via `docker exec` — never exposed on `llm-net` or a host port (§10.3.1).
- **LiteLLM retries** a queue 429 (`num_retries: 3`), turning a transient
  over-backstop burst back into hold-and-dispatch (deep-research fan-out → all
  200). Direct callers get the full structured body; LiteLLM-fronted callers get
  the reason embedded in its `RateLimitError` message on sustained saturation.
