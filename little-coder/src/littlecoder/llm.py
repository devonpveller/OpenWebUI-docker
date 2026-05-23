"""Minimal LLM + embedding clients for the control plane (design §3.5).

The agent's inner loop has its own LLM client (little-coder upstream); this
module is for the CONTROL plane — `meta`'s judge calls and the embedding
calls behind cluster similarity (design §5.2).

Two clients, both thin httpx wrappers around the llama-cpp OpenAI-compatible
endpoints:

  - `ChatClient`        POST /v1/chat/completions
  - `EmbeddingClient`   POST /v1/embeddings

Both expose protocol-narrow interfaces so the judge and similarity modules
can be mocked at the seam without touching network code. A `MockChatClient`
and `MockEmbeddingClient` live alongside for the tests.

**Sanitization-aware**: the judge's `ChatClient.chat` wraps every outbound
text segment through `Sanitizer.apply()` in enforcing mode (design §10.2).
A SanitizerError ABORTS the call — never "send anyway".
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Protocol

import httpx

from .sanitize import Sanitizer, SanitizerError


@dataclasses.dataclass(frozen=True)
class ChatMessage:
    """One chat message — `role` is `system` / `user` / `assistant`."""

    role: str
    content: str


@dataclasses.dataclass(frozen=True)
class ChatResponse:
    """Result of one chat call. `content` is the assistant's reply text;
    `usage` is the token-usage block when llama-cpp returns one."""

    content: str
    finish_reason: str
    usage: dict[str, int] = dataclasses.field(default_factory=dict)


class ChatLike(Protocol):
    """Narrow protocol so the judge can be tested against a mock."""

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResponse: ...


class EmbedLike(Protocol):
    """Narrow protocol for embedding clients."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LlmError(RuntimeError):
    """A transport-level or response-shape problem with the LLM call. The
    caller decides whether to retry, defer, or abort (design §12.10 —
    nothing fails open)."""


class ChatClient:
    """OpenAI-compatible `/v1/chat/completions` client.

    Sanitization (design §10.2) is wired in here, not at the call sites,
    so every outbound payload is filtered on a single path. The Sanitizer
    mode is whatever the caller constructs it with — `judge.Judge`
    always builds an enforcing-mode Sanitizer per design's "Observer
    onward, judge calls are filtered enforcing"."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        default_model: str = "qwen36-27b",
        timeout_seconds: float = 60.0,
        sanitizer: Sanitizer | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        # Default to enforcing — the control plane only calls the LLM
        # in outbound contexts (judge calls, future PR bodies).
        self.sanitizer = sanitizer or Sanitizer(mode="enforcing")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _sanitize(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Run every message's content through the sanitizer. Aborts on
        SanitizerError (never 'send anyway')."""
        out: list[ChatMessage] = []
        for msg in messages:
            result = self.sanitizer.apply(msg.content)
            # In enforcing mode, .cleaned is the scrubbed text; in shadow
            # mode it equals the original (the apply contract). Either
            # way, we pass `.cleaned` along — shadow mode is for Tool,
            # where nothing leaves the stack.
            out.append(ChatMessage(role=msg.role, content=result.cleaned))
        return out

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Send a chat completion request. Raises LlmError on transport or
        response-shape problems; raises SanitizerError if filtering
        fails. Never returns silently on a degenerate response."""
        try:
            sanitized = self._sanitize(messages)
        except SanitizerError:
            # Surface the failure unchanged — judges must abort on filter
            # failure (design §10.2). We don't try to "send anyway".
            raise

        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [{"role": m.role, "content": m.content} for m in sanitized],
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format is not None:
            body["response_format"] = response_format

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise LlmError(f"chat completion request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LlmError(f"chat response was not JSON: {exc}") from exc

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason") or "unknown"
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"chat response malformed: {data!r}") from exc

        return ChatResponse(
            content=str(content),
            finish_reason=str(finish),
            usage=dict(data.get("usage") or {}),
        )


class EmbeddingClient:
    """OpenAI-compatible `/v1/embeddings` client. Batched: one call
    handles many texts, which is what cluster-similarity wants when a
    new occurrence is scored against every in-scope cluster."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        default_model: str = "text-embedding-3-small",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """Embed a batch of texts. Returns a vector per input, in order.
        Raises LlmError on transport/shape problems."""
        if not texts:
            return []
        body = {"model": model or self.default_model, "input": texts}
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers(),
                    json=body,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise LlmError(f"embedding request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LlmError(f"embedding response was not JSON: {exc}") from exc

        try:
            return [list(item["embedding"]) for item in data["data"]]
        except (KeyError, TypeError) as exc:
            raise LlmError(f"embedding response malformed: {data!r}") from exc


# --- mocks for tests ----------------------------------------------------


class MockChatClient:
    """Deterministic stand-in for ChatClient. Configure with a list of
    canned responses; each call pops one off. Records the messages it
    was sent so tests can assert on the prompt content."""

    def __init__(self, responses: list[ChatResponse | str]):
        self._responses: list[ChatResponse | str] = list(responses)
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise LlmError("MockChatClient: no more canned responses")
        next_resp = self._responses.pop(0)
        if isinstance(next_resp, str):
            return ChatResponse(content=next_resp, finish_reason="stop")
        return next_resp


class MockEmbeddingClient:
    """Deterministic stand-in for EmbeddingClient. Pass a `vectors_by_text`
    dict; unknown texts map to a zero vector of `dim` (so the similarity
    floor catches them as 'no match' rather than blowing up)."""

    def __init__(
        self,
        vectors_by_text: dict[str, list[float]] | None = None,
        dim: int = 4,
    ) -> None:
        self._vectors = dict(vectors_by_text or {})
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vectors.get(t, [0.0] * self.dim) for t in texts]
