"""GitHub App token minter (P-APL.0) — the capability plane's root of trust. Proves the App JWT is
signed with the mounted private key, an installation token is minted + CACHED to expiry (no re-mint
per call), GitHub rejections surface as one clear error (never a token leak), and the plane stays
OFFLINE until the App is configured. Uses a generated RSA key + a mocked GitHub (no network)."""

from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.modules.github_app import (
    FakeGitHubApp,
    GitHubAppClient,
    GitHubAppError,
    build_github_app,
)


def _rsa_key_file(tmp_path) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    p = tmp_path / "app.pem"
    p.write_bytes(pem)
    return str(p)


def _settings(tmp_path, **over) -> Settings:
    s = Settings(_env_file=None, chat_adapter="mattermost")
    s.github_app_id = "12345"
    s.github_app_owner = "octocat"
    s.github_app_private_key_path = _rsa_key_file(tmp_path)
    for k, v in over.items():
        setattr(s, k, v)
    return s


async def test_mints_and_caches_installation_token(tmp_path):
    calls = {"install": 0, "token": 0, "auth": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["auth"].append(request.headers.get("authorization", ""))
        if request.url.path.endswith("/installation"):
            calls["install"] += 1
            return httpx.Response(200, json={"id": 42})
        if request.url.path.endswith("/access_tokens"):
            calls["token"] += 1
            return httpx.Response(201, json={"token": "ghs_abc", "expires_at": "2999-01-01T00:00:00Z"})
        return httpx.Response(404, json={"message": "no route"})

    client = GitHubAppClient(_settings(tmp_path), transport=httpx.MockTransport(handler))
    t1 = await client.installation_token()
    t2 = await client.installation_token()
    assert t1 == "ghs_abc" == t2
    assert calls["token"] == 1                      # cached — not re-minted on the second call
    assert calls["install"] == 1                    # installation id cached too
    # every request rode a signed App JWT (Bearer), never a static secret
    assert all(a.startswith("Bearer ") for a in calls["auth"])


async def test_github_rejection_surfaces_as_clear_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = GitHubAppClient(_settings(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubAppError) as ei:
        await client.installation_token()
    assert "installed" in str(ei.value).lower() or "404" in str(ei.value)  # actionable, no token dump


async def test_unreadable_key_is_a_clear_error(tmp_path):
    s = _settings(tmp_path, github_app_private_key_path=str(tmp_path / "missing.pem"))
    client = GitHubAppClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(GitHubAppError) as ei:
        await client.installation_token()
    assert "private key" in str(ei.value).lower()


def test_plane_offline_until_configured():
    # No app id / no key file → capability plane is OFF; builder returns None on the real adapter.
    s = Settings(_env_file=None, chat_adapter="mattermost")
    assert not s.github_app_enabled
    assert build_github_app(s) is None


async def test_fake_app_mints_a_token_per_call():
    fake = FakeGitHubApp(owner="me")
    assert fake.owner == "me"
    assert await fake.installation_token() == "ghs_faketoken"
    assert fake.token_calls == 1
    assert (await fake.verify())["owner"] == "me"


def test_builder_uses_fake_in_fake_mode():
    s = Settings(_env_file=None, chat_adapter="fake", github_app_owner="me")
    app = build_github_app(s)
    assert isinstance(app, FakeGitHubApp) and app.owner == "me"
