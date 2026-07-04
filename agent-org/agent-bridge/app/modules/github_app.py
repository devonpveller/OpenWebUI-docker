"""GitHub App — the capability plane's root of trust (autonomous-project-lifecycle P-APL.0).

A GitHub App authorises the governed capability plane (fork / create / submodule) instead of a
long-lived admin PAT. The DURABLE secret is the App private key (a mounted, read-only file — never in
git, never handed to a worker); it only signs a short-lived JWT used to mint per-installation,
revocable INSTALLATION TOKENS (~1h). No privileged credential is ever persisted in a repo. This is
also the durable answer to the deferred at-rest-token concern ("Bug 5b").

Server-to-server auth flow:
  1. sign an App JWT (RS256, iss=app_id, exp <= 10m) with the private key
  2. resolve the installation id for the owner (GET /users/{owner}/installation) — cached
  3. mint an installation access token (POST /app/installations/{id}/access_tokens) — cached to expiry

The seam is a Protocol so capability handlers + tests never touch real crypto/GitHub. A `transport`
may be injected for deterministic tests (httpx.MockTransport).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Protocol

import httpx
import jwt  # PyJWT

from ..config import Settings

log = logging.getLogger("agent_bridge.github_app")

# Re-mint an installation token when the cached one is within this margin of expiry.
_EXPIRY_MARGIN_S = 120.0
_JWT_TTL_S = 540  # 9 min (GitHub caps the App JWT at 10)


class GitHubAppError(RuntimeError):
    """The App isn't configured, its key is unreadable, or GitHub rejected the request. Capability
    handlers translate this into an operator-facing message; it never leaks a token."""


def _parse_expiry(iso: str | None) -> float:
    """Parse GitHub's `expires_at` (ISO-8601, e.g. '2026-07-04T12:00:00Z') to an epoch. Falls back to
    now+1h if absent/unparseable — the margin re-mint keeps that safe."""
    if not iso:
        return time.time() + 3600.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time() + 3600.0


class GitHubApp(Protocol):
    @property
    def owner(self) -> str:
        """The account (personal login) the App is installed on — the fork/create target."""
        ...

    async def installation_token(self) -> str:
        """A short-lived installation access token for git ops + REST (minted + cached to expiry)."""
        ...

    async def verify(self) -> dict:
        """Confirm the App is reachable + installed; return {app_slug, owner, installation_id}."""
        ...


class GitHubAppClient:
    """Real GitHub App client. Caches the private key, the installation id, and the current
    installation token (re-minted only near expiry). One instance per process."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.s = settings
        self._transport = transport            # injected in tests (httpx.MockTransport); None = real net
        self._key: str | None = None
        self._installation_id: int | None = None
        self._token: str | None = None
        self._token_exp: float = 0.0

    @property
    def owner(self) -> str:
        return self.s.github_app_owner

    def _client(self, timeout: float = 20.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    def _private_key(self) -> str:
        if self._key is None:
            try:
                with open(self.s.github_app_private_key_path, encoding="utf-8") as fh:
                    self._key = fh.read()
            except OSError as exc:
                raise GitHubAppError(
                    f"cannot read the App private key at {self.s.github_app_private_key_path} "
                    f"({exc}) — check the mount + file permissions"
                ) from exc
        return self._key

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + _JWT_TTL_S, "iss": self.s.github_app_id}
        try:
            return jwt.encode(payload, self._private_key(), algorithm="RS256")
        except Exception as exc:  # noqa: BLE001 - bad key / lib error → one clear failure
            raise GitHubAppError(f"could not sign the App JWT (bad private key?): {exc}") from exc

    @staticmethod
    def _headers(bearer: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _resolve_installation_id(self) -> int:
        if self._installation_id is None:
            base = self.s.github_api_base.rstrip("/")
            async with self._client() as c:
                r = await c.get(
                    f"{base}/users/{self.s.github_app_owner}/installation",
                    headers=self._headers(self._app_jwt()),
                )
            if r.status_code >= 400:
                raise GitHubAppError(
                    f"resolving the App installation for {self.s.github_app_owner!r} failed "
                    f"({r.status_code}) — is the App installed on that account? {r.text[:160]}"
                )
            self._installation_id = int(r.json()["id"])
        return self._installation_id

    async def installation_token(self) -> str:
        if self._token and time.time() < self._token_exp - _EXPIRY_MARGIN_S:
            return self._token
        base = self.s.github_api_base.rstrip("/")
        iid = await self._resolve_installation_id()
        async with self._client() as c:
            r = await c.post(
                f"{base}/app/installations/{iid}/access_tokens",
                headers=self._headers(self._app_jwt()),
            )
        if r.status_code >= 400:
            raise GitHubAppError(
                f"minting an installation token failed ({r.status_code}): {r.text[:160]}"
            )
        data = r.json()
        self._token = data["token"]
        self._token_exp = _parse_expiry(data.get("expires_at"))
        return self._token

    async def verify(self) -> dict:
        base = self.s.github_api_base.rstrip("/")
        async with self._client() as c:
            app = await c.get(f"{base}/app", headers=self._headers(self._app_jwt()))
        if app.status_code >= 400:
            raise GitHubAppError(f"GET /app failed ({app.status_code}): {app.text[:160]}")
        iid = await self._resolve_installation_id()
        return {"app_slug": app.json().get("slug"), "owner": self.s.github_app_owner,
                "installation_id": iid}


class FakeGitHubApp:
    """Deterministic App for tests/dev — no crypto, no network. Records token requests so the
    capability tests can assert an ephemeral token was minted per operation."""

    def __init__(self, token: str = "ghs_faketoken", owner: str = "test-owner") -> None:
        self._token = token
        self._owner = owner
        self.token_calls = 0

    @property
    def owner(self) -> str:
        return self._owner

    async def installation_token(self) -> str:
        self.token_calls += 1
        return self._token

    async def verify(self) -> dict:
        return {"app_slug": "fake-app", "owner": self._owner, "installation_id": 1}


def build_github_app(settings: Settings) -> GitHubApp | None:
    """Fake in the `fake` chat-adapter/dev mode; the real client when the App is CONFIGURED (id set +
    key file present); None otherwise — the capability plane stays offline (and its inlets refuse
    with a clear "App not set up yet" message) until you register the App. So the bridge runs normally
    before P-APL.0's one-time human setup is done."""
    if settings.chat_adapter == "fake":
        return FakeGitHubApp(owner=settings.github_app_owner or "test-owner")
    if not settings.github_app_enabled:
        return None
    return GitHubAppClient(settings)
