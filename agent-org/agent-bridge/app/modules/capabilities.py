"""Capability plane executors (autonomous-project-lifecycle P-APL.1).

Deterministic, governed operator-plane actions the BRIDGE performs via the GitHub App — NOT the
worker. Each is a FIXED FUNCTION (not an agent prompt), so it does exactly one thing the same way
every time, and the irreversible ones run ONLY after the human clears the §3 hard-gate. This is the
plane that lets the orchestration own project STRUCTURE (fork/create/compose) while the worker owns
CODE — without any *agent* gaining an unsupervised power (DESIGN §1).

Phase P-APL.1a ships `fork_repo` (works on a personal account via the App). `create_repo` is
org-only via a GitHub App (GitHub disallows user-account repo *creation* with an installation token)
— added when composition targets an org, or the operator creates fresh repos by hand.
"""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel

from .github_app import GitHubApp, GitHubAppError

_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


class CapabilityResult(BaseModel):
    """The outcome of one capability, in operator-facing terms. `ok=False` carries a clear reason
    (never a token) so a failure reads as an actionable problem, not a stack trace."""

    ok: bool
    summary: str            # one-line human-facing result
    url: str = ""           # the resulting repo (html_url) when applicable
    detail: str = ""        # short extra context on failure


def parse_owner_repo(url: str) -> tuple[str, str]:
    """(owner, repo) from a GitHub URL or `owner/repo` shorthand:
    https://github.com/o/r(.git) | git@github.com:o/r(.git) | o/r."""
    s = url.strip()
    if s.endswith(".git"):
        s = s[:-4]
    s = s.rstrip("/")
    m = re.search(r"github\.com[/:]([^/]+)/([^/]+)$", s)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)$", s)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"could not parse a GitHub owner/repo from {url!r}")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }


async def fork_repo(
    github: GitHubApp, parent_url: str, *,
    api_base: str = "https://api.github.com",
    transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """Fork `parent_url` into the App-installed account (POST /repos/{owner}/{repo}/forks). The fork
    lands under `github.owner`; the caller then registers it as a project with the parent as its
    read-only `upstream`. Idempotent-ish: GitHub returns the existing fork if one already exists."""
    try:
        owner, repo = parse_owner_repo(parent_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{parent_url}` isn't a valid GitHub repo.",
                                detail=str(exc))
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            r = await c.post(f"{base}/repos/{owner}/{repo}/forks", headers=_headers(token), json={})
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to fork {owner}/{repo}.",
                                detail=str(exc)[:160])
    if r.status_code in (200, 201, 202):
        d = r.json()
        return CapabilityResult(
            ok=True,
            summary=f"Forked `{owner}/{repo}` → `{d.get('full_name', github.owner + '/' + repo)}`",
            url=d.get("html_url", ""),
        )
    if r.status_code == 403:
        return CapabilityResult(
            ok=False,
            summary=f"GitHub refused the fork of `{owner}/{repo}` (403) — the App may not be granted "
                    f"that repo, or forking is disabled for it.",
            detail=r.text[:160],
        )
    if r.status_code == 404:
        return CapabilityResult(
            ok=False,
            summary=f"`{owner}/{repo}` wasn't found (404) — check the URL, or the App can't see it.",
            detail=r.text[:160],
        )
    return CapabilityResult(ok=False, summary=f"Fork of `{owner}/{repo}` failed ({r.status_code}).",
                            detail=r.text[:160])
