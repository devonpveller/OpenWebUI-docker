"""model-router — local-vs-cloud lane selection + profile binding + structured output.

Responsibilities (PLAN §3.1.1 / §3.4 / §5.4):
- Bind a *profile* (role) to a lane (local `llm-gateway` | cloud `llm-gateway-cloud`)
  and an underlying model name. Adding a role = adding a profile — never a gateway change.
- Carry the profile's caller-key so the gateways' spend ledgers attribute traffic by
  role (C7).
- Emit structured output reliably from weak local models: JSON-schema constrained
  decoding (GBNF, llama.cpp via llama-swap) + Pydantic validation (Instructor).
- NEVER probe model health (C5): monitoring uses bounded real completions only.

The concrete OpenAI/Instructor client is created lazily and isolated behind
`ModelClient` so the deterministic tests can inject a `FakeModelClient` (no network,
no openai/instructor dependency required to run the FSM/scheduler tests).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..config import Settings
from .profiles import ProfileRegistry

log = logging.getLogger("agent_bridge.model_router")

T = TypeVar("T", bound=BaseModel)


class ModelBackpressureError(Exception):
    """The inference queue SHED the request (429/503 admission backpressure) after bounded retries
    — the shared GPU is saturated, not a bad request. Distinct from a schema/parse failure so an
    interactive caller (the PO) can say "the model's busy, one moment" instead of "couldn't parse"."""


_BACKPRESSURE_MARKERS = (
    "queue_connections_exhausted", "backpressure", "serviceunavailable",
    "service unavailable", "ratelimiterror", "rate limit", "shedding load",
    "error code: 503", "error code: 429", "status 503", "status 429",
    "hard connection cap",
)


def is_backpressure_text(text: str | None) -> bool:
    """True if free text (an error message, a worker result) signals a 429/503 admission shed.
    Used for worker task results whose inference was shed inside little-coder (not a bridge call)."""
    s = (text or "").lower()
    return any(m in s for m in _BACKPRESSURE_MARKERS)


def _is_backpressure(exc: BaseException) -> bool:
    """True if `exc` is a 429/503 admission shed from the gateway/llm-queue (not a bad request).
    Matches by HTTP status where available, else the queue's marker text — robust to how the
    openai/instructor stack wraps the upstream error."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (429, 503):
        return True
    return is_backpressure_text(str(exc))


class ModelClient(Protocol):
    async def structured(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        caller_key: str,
        temperature: float,
        system: str,
        user: str,
        schema: type[T],
        max_retries: int = 2,
    ) -> T: ...

    async def complete(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        caller_key: str,
        temperature: float,
        system: str,
        user: str,
    ) -> str: ...


class OpenAICompatClient:
    """Real client: OpenAI-compatible endpoint + Instructor for schema validation,
    with llama.cpp JSON-schema→GBNF constrained decoding passed via `extra_body`.

    Imports of `openai`/`instructor` are deferred so the module imports cleanly in a
    minimal test env; only instantiating this class needs them.
    """

    def __init__(self) -> None:
        import instructor  # noqa: PLC0415
        from openai import AsyncOpenAI  # noqa: PLC0415

        self._instructor = instructor
        self._AsyncOpenAI = AsyncOpenAI

    def _client(self, api_base: str, api_key: str):
        base = self._AsyncOpenAI(base_url=api_base, api_key=api_key or "agent-org")
        # JSON mode + Pydantic validation with bounded repair retries.
        return self._instructor.from_openai(base, mode=self._instructor.Mode.JSON)

    async def structured(
        self, *, api_base, api_key, model, caller_key, temperature, system, user, schema,
        max_retries=2,
    ):
        client = self._client(api_base, api_key)
        # `user=caller_key`: LiteLLM forwards the OpenAI `user` field, so the spend
        # ledger attributes by role even though the caller key is stripped to `dummy`
        # on the permissive gateway (litellm-proxy-status memory). GBNF grammar is
        # derived by llama.cpp from the response schema (constrained decoding).
        # max_retries=0 lets the P0.5 eval measure FIRST-TRY validity (zero repair).
        return await client.chat.completions.create(
            model=model,
            temperature=temperature,
            response_model=schema,
            max_retries=max_retries,
            user=caller_key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body={"json_schema": schema.model_json_schema()},
        )

    async def complete(
        self, *, api_base, api_key, model, caller_key, temperature, system, user
    ) -> str:
        base = self._AsyncOpenAI(base_url=api_base, api_key=api_key or "agent-org")
        resp = await base.chat.completions.create(
            model=model,
            temperature=temperature,
            user=caller_key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


class FakeModelClient:
    """Test double. `queue_structured`/`queue_text` pre-load responses per profile."""

    def __init__(self) -> None:
        self._structured: list[BaseModel] = []
        self._text: list[str] = []
        self.calls: list[dict[str, Any]] = []
        # Queue exceptions (e.g. a simulated 503) to exercise backpressure handling. An item is
        # popped per call; a BaseException is raised, a model is returned.
        self._raises: list[BaseException] = []

    def queue_structured(self, obj: BaseModel) -> None:
        self._structured.append(obj)

    def queue_text(self, text: str) -> None:
        self._text.append(text)

    def queue_raise(self, exc: BaseException, times: int = 1) -> None:
        """Make the next `times` calls raise `exc` (to test 429/503 backpressure retry)."""
        self._raises.extend([exc] * times)

    async def structured(self, *, schema, **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"kind": "structured", **kw})
        if self._raises:
            raise self._raises.pop(0)
        if self._structured:
            return self._structured.pop(0)
        # Default: a schema instance with empty/false fields where possible.
        return schema.model_construct()

    async def complete(self, **kw) -> str:  # type: ignore[no-untyped-def]
        self.calls.append({"kind": "complete", **kw})
        return self._text.pop(0) if self._text else ""


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        profiles: ProfileRegistry,
        client: ModelClient | None = None,
    ) -> None:
        self.s = settings
        self.profiles = profiles
        self._client = client  # injected in tests; lazily created in prod
        # Self-clocked capacity signal: a SUCCESSFUL call is proof the shared GPU has capacity, so
        # the orchestrator can drain parked-on-backpressure efforts. No health probing (C5) — we
        # only react to real completions. Set by the orchestrator; a no-op if unset.
        self.on_capacity_signal = None

    def _get_client(self) -> ModelClient:
        if self._client is None:
            self._client = OpenAICompatClient()
        return self._client

    def _endpoint(self, lane: str) -> tuple[str, str]:
        if lane == "cloud":
            if not self.s.cloud_enabled:
                # Fail-safe: if the cloud lane isn't wired, judgment falls back to
                # local (never silently to a weak monitor — the Human Operator carries
                # more; see governance §2.1). The caller decides whether that's OK.
                log.warning("cloud lane requested but disabled — falling back to local")
                return self.s.local_api_base, self.s.local_api_key
            return self.s.cloud_api_base, self.s.cloud_api_key
        return self.s.local_api_base, self.s.local_api_key

    async def _with_backpressure_retry(self, make_call, *, label: str):
        """Run an inference call, retrying on 429/503 admission backpressure with exponential
        backoff (a batch job may be squeezing the shared GPU). `make_call` is a zero-arg factory
        that returns a FRESH awaitable each attempt. Raises ModelBackpressureError once retries are
        exhausted (so an interactive caller can degrade honestly); other errors propagate as-is."""
        attempts = max(0, self.s.model_backpressure_retries)
        delay = self.s.model_backpressure_base_delay_s
        for i in range(attempts + 1):
            try:
                result = await make_call()
            except Exception as exc:  # noqa: BLE001 - classify, then retry-or-raise
                if not _is_backpressure(exc):
                    raise
                if i >= attempts:
                    log.warning("model backpressure on %s — shed after %d retries", label, attempts)
                    raise ModelBackpressureError(str(exc)) from exc
                log.info("model backpressure on %s (try %d/%d) — retry in %.1fs",
                         label, i + 1, attempts, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.s.model_backpressure_max_delay_s)
            else:
                # Success = proof the GPU has capacity → let the orchestrator drain parked efforts.
                if self.on_capacity_signal is not None:
                    try:
                        self.on_capacity_signal()
                    except Exception as exc:  # noqa: BLE001 - a signal hiccup must not fail the call
                        log.debug("on_capacity_signal raised: %s", exc)
                return result

    async def structured(
        self, profile_name: str, system: str, user: str, schema: type[T], max_retries: int = 2,
        temperature: float | None = None,
    ) -> T:
        # P21 F2a — `temperature` overrides the profile default for a call that must be DETERMINISTIC
        # (a governance decision, not a creative one — the readiness/risk gate). Omitted → the
        # profile's temperature, unchanged for every other caller.
        p = self.profiles.get(profile_name)
        api_base, api_key = self._endpoint(p.lane)
        temp = p.temperature if temperature is None else temperature

        def _call():
            return self._get_client().structured(
                api_base=api_base,
                api_key=api_key,
                model=p.model,
                caller_key=p.caller_key,
                temperature=temp,
                system=system,
                user=user,
                schema=schema,
                max_retries=max_retries,
            )

        return await self._with_backpressure_retry(_call, label=f"structured:{profile_name}")

    async def complete(self, profile_name: str, system: str, user: str) -> str:
        p = self.profiles.get(profile_name)
        api_base, api_key = self._endpoint(p.lane)

        def _call():
            return self._get_client().complete(
                api_base=api_base,
                api_key=api_key,
                model=p.model,
                caller_key=p.caller_key,
                temperature=p.temperature,
                system=system,
                user=user,
            )

        return await self._with_backpressure_retry(_call, label=f"complete:{profile_name}")
