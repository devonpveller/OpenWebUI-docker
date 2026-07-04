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

import base64
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


class RepoState(BaseModel):
    """A repo's ACTUAL current state (UX-FLOW Stage 1 anchor). Carries STRUCTURED data so the planner
    can reconcile DETERMINISTICALLY (drop steps already satisfied) — not just a string for the model,
    which doesn't reliably subtract against it."""

    readable: bool = False
    default_branch: str = ""
    submodule_paths: list[str] = []
    submodule_urls: list[str] = []
    top_level: list[str] = []

    @property
    def summary(self) -> str:
        if not self.readable:
            return ""
        subs = "; ".join(f"{p} → {u}" for p, u in zip(self.submodule_paths, self.submodule_urls))
        return (f"default branch: {self.default_branch} | submodules: {subs or 'none'} | "
                f"top-level: {', '.join(self.top_level[:25]) if self.top_level else 'empty'}")


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


async def read_repo_state(
    github: GitHubApp, repo_url: str, *,
    api_base: str = "https://api.github.com",
    transport: httpx.BaseTransport | None = None,
) -> RepoState:
    """The ACTUAL current state of a repo — default branch, submodules (from `.gitmodules`) and its
    top-level tree — read via the GitHub App API (no clone). ANCHORS the planner to workspace reality
    (UX-FLOW Stage 1) so it reconciles desired-vs-actual instead of blindly duplicating. Returns an
    unreadable RepoState when the App can't read it (own account only)."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return RepoState(readable=False)
    if owner.lower() != (github.owner or "").lower():
        return RepoState(readable=False)
    try:
        token = await github.installation_token()
    except GitHubAppError:
        return RepoState(readable=False)
    base = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
            if meta.status_code >= 400:
                return RepoState(readable=False)
            ref = meta.json().get("default_branch") or "main"
            gm = await c.get(f"{base}/repos/{owner}/{repo}/contents/.gitmodules?ref={ref}", headers=h)
            tree = await c.get(f"{base}/repos/{owner}/{repo}/contents?ref={ref}", headers=h)
    except httpx.HTTPError:
        return RepoState(readable=False)
    paths: list[str] = []
    urls: list[str] = []
    if gm.status_code == 200 and gm.json().get("content"):
        text = base64.b64decode(gm.json()["content"]).decode("utf-8", "replace")
        paths = [p.strip() for p in re.findall(r"(?mi)^\s*path\s*=\s*(.+?)\s*$", text)]
        urls = [u.strip() for u in re.findall(r"(?mi)^\s*url\s*=\s*(.+?)\s*$", text)]
    entries: list[str] = []
    if tree.status_code == 200 and isinstance(tree.json(), list):
        entries = [e["name"] + ("/" if e.get("type") == "dir" else "") for e in tree.json()]
    return RepoState(readable=True, default_branch=ref, submodule_paths=paths,
                     submodule_urls=urls, top_level=entries)


class BranchDelivery(BaseModel):
    """A CHECKABLE verdict on whether an effort's work actually LANDED on the remote — the deterministic
    acceptance signal the PM verifies against (governance §4.2 / F8), NOT the worker's self-report. A
    worker's pi turn ending `done` only means its turn ended; it does not mean a branch with a real
    commit exists. `verifiable=False` ⇒ the App can't read this repo (not its own account), so the PM
    must fall back to the worker's word and LABEL the result unverified — never silently trust it."""

    verifiable: bool = False     # could we independently check the remote at all?
    exists: bool = False         # the branch is present on the remote
    ahead: int = 0               # commits the branch is ahead of the base (default) branch
    head_sha: str = ""           # the branch head (short) — surfaced so the operator can see it
    branch: str = ""
    base: str = ""
    detail: str = ""             # short context on an unverifiable/failed check (never a token)

    @property
    def landed(self) -> bool:
        """The change verifiably landed: a real branch exists AND carries at least one commit over base."""
        return self.verifiable and self.exists and self.ahead >= 1


async def read_branch_delivery(
    github: GitHubApp, repo_url: str, branch: str, *,
    api_base: str = "https://api.github.com",
    transport: httpx.BaseTransport | None = None,
) -> BranchDelivery:
    """Independently verify a worker's deliverable landed: does `branch` exist on `repo_url`'s remote,
    and is it ahead of the default branch (i.e. carries actual commits)? Read via the GitHub App API —
    the deterministic floor, not the worker's claim. Own-account only; if the App can't read the repo
    the verdict is `verifiable=False` (the PM then falls back to the self-report, honestly labelled)."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError as exc:
        return BranchDelivery(branch=branch, detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return BranchDelivery(branch=branch, detail="repo is not on the App's account")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return BranchDelivery(branch=branch, detail=str(exc)[:120])
    base_api = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            meta = await c.get(f"{base_api}/repos/{owner}/{repo}", headers=h)
            if meta.status_code >= 400:
                return BranchDelivery(branch=branch, detail=f"repo meta {meta.status_code}")
            base = meta.json().get("default_branch") or "main"
            br = await c.get(f"{base_api}/repos/{owner}/{repo}/branches/{branch}", headers=h)
            if br.status_code == 404:
                return BranchDelivery(verifiable=True, exists=False, branch=branch, base=base)
            if br.status_code >= 400:
                return BranchDelivery(branch=branch, base=base, detail=f"branch read {br.status_code}")
            sha = (br.json().get("commit", {}) or {}).get("sha", "")[:10]
            ahead = 0
            if branch != base:
                cmp = await c.get(
                    f"{base_api}/repos/{owner}/{repo}/compare/{base}...{branch}", headers=h)
                if cmp.status_code == 200:
                    ahead = int(cmp.json().get("ahead_by", 0) or 0)
    except (httpx.HTTPError, ValueError) as exc:
        return BranchDelivery(branch=branch, detail=str(exc)[:120])
    return BranchDelivery(verifiable=True, exists=True, ahead=ahead, head_sha=sha,
                          branch=branch, base=base)


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
