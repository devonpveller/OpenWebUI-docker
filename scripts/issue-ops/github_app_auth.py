#!/usr/bin/env python3
"""GitHub App auth for issue-ops (Part M, 2026-08-22).

Mints short-lived installation access tokens from the SAME GitHub App the
agent-org bridge uses (AO_GITHUB_APP_ID / _OWNER in agent-org/docker/.env,
private key at agent-org/agent-bridge/secrets/github-app-key.pem). This is
OPERATOR-context tooling: the key already lives on this host, owned by the
operator — it is never handed to workers (the bridge's containment rule
stands; this module just reuses the credential the operator already holds).

Token cache: scripts/issue-ops/state/gh-token.json (gitignored), re-minted
when <5 minutes of lifetime remain. Mirrors agent-bridge's
app/modules/github_app.py flow: App JWT -> owner installation id ->
installation access token.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import jwt  # PyJWT[crypto]

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "agent-org" / "docker" / ".env"
KEY_PATH = ROOT / "agent-org" / "agent-bridge" / "secrets" / "github-app-key.pem"
STATE_DIR = Path(__file__).resolve().parent / "state"
CACHE = STATE_DIR / "gh-token.json"
API = "https://api.github.com"
REMINT_MARGIN_S = 300


def _env(name: str) -> str:
    for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def _gh(url: str, bearer: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-stack-issue-ops/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode() or "{}")


def _app_jwt() -> str:
    app_id = _env("AO_GITHUB_APP_ID")
    if not app_id or not KEY_PATH.is_file():
        raise RuntimeError(
            "GitHub App not configured (AO_GITHUB_APP_ID in agent-org/docker/.env "
            f"+ key at {KEY_PATH})"
        )
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": app_id},
        KEY_PATH.read_text(encoding="utf-8"),
        algorithm="RS256",
    )


def installation_token(force: bool = False) -> str:
    """Cached installation token; re-minted near expiry."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not force and CACHE.is_file():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            if c.get("expires_at_epoch", 0) - time.time() > REMINT_MARGIN_S:
                return c["token"]
        except Exception:
            pass
    bearer = _app_jwt()
    owner = _env("AO_GITHUB_APP_OWNER")
    inst = _gh(f"{API}/users/{owner}/installation", bearer)
    tok = _gh(
        f"{API}/app/installations/{inst['id']}/access_tokens", bearer, method="POST", body={}
    )
    expires = tok.get("expires_at", "")
    # ISO8601 Z -> epoch (tokens live ~1h). calendar.timegm, NOT time.mktime:
    # mktime reads the struct as LOCAL time, which on a UTC-negative host made
    # the cache overstate lifetime by hours and serve expired tokens (2026-08-23).
    import calendar
    exp_epoch = calendar.timegm(time.strptime(expires, "%Y-%m-%dT%H:%M:%SZ")) if expires else time.time() + 3000
    CACHE.write_text(
        json.dumps({"token": tok["token"], "expires_at_epoch": exp_epoch}), encoding="utf-8"
    )
    return tok["token"]


def api(path: str, method: str = "GET", body: dict | None = None) -> dict | list:
    """Authenticated GitHub REST call with the installation token."""
    tok = installation_token()
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-stack-issue-ops/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


if __name__ == "__main__":
    t = installation_token(force=True)
    print("token minted:", t[:8] + "…")
