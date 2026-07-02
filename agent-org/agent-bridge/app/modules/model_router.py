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

import logging
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..config import Settings
from .profiles import ProfileRegistry

log = logging.getLogger("agent_bridge.model_router")

T = TypeVar("T", bound=BaseModel)


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

    def queue_structured(self, obj: BaseModel) -> None:
        self._structured.append(obj)

    def queue_text(self, text: str) -> None:
        self._text.append(text)

    async def structured(self, *, schema, **kw):  # type: ignore[no-untyped-def]
        self.calls.append({"kind": "structured", **kw})
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

    async def structured(
        self, profile_name: str, system: str, user: str, schema: type[T], max_retries: int = 2
    ) -> T:
        p = self.profiles.get(profile_name)
        api_base, api_key = self._endpoint(p.lane)
        return await self._get_client().structured(
            api_base=api_base,
            api_key=api_key,
            model=p.model,
            caller_key=p.caller_key,
            temperature=p.temperature,
            system=system,
            user=user,
            schema=schema,
            max_retries=max_retries,
        )

    async def complete(self, profile_name: str, system: str, user: str) -> str:
        p = self.profiles.get(profile_name)
        api_base, api_key = self._endpoint(p.lane)
        return await self._get_client().complete(
            api_base=api_base,
            api_key=api_key,
            model=p.model,
            caller_key=p.caller_key,
            temperature=p.temperature,
            system=system,
            user=user,
        )
