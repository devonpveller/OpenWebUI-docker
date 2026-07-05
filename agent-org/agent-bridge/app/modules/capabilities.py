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
    head_sha: str = ""           # the branch head commit — FULL sha (truncate at display sites)
    branch: str = ""
    base: str = ""
    detail: str = ""             # short context on an unverifiable/failed check (never a token)
    # Set by the ORCHESTRATOR (not the remote read) when the worker explicitly reported
    # `NO CHANGES: <why>` — a read-only/investigation task with nothing to publish. A legitimate
    # completion whose deliverable is the worker's ANSWER, not a branch (never escalated as
    # undelivered — the live miss force-marched a read-only task through publish → escalation).
    no_changes: bool = False

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
            sha = (br.json().get("commit", {}) or {}).get("sha", "")   # full sha (for a submodule bump)
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


async def read_broken_gitlinks(
    github: GitHubApp, repo_url: str, branch: str, *, base_branch: str = "",
    api_base: str = "https://api.github.com",
    transport: httpx.BaseTransport | None = None,
) -> list[dict]:
    """DELIVERY-PIPELINE gitlink-reachability gate. For each submodule POINTER the branch changed
    (vs base), verify the referenced commit actually EXISTS on the submodule's remote. A worker
    can commit inside its vendored submodule checkout, bump the superproject pointer and publish
    ONLY the superproject — the branch then references a commit nobody else can fetch, and every
    fresh clone dies on `git submodule update --init --recursive` with `fatal: remote error:
    upload-pack: not our ref …` (live 2026-07-05: the engine branch pointed vendor/MonoGame at
    `ac3a830b…`, made only inside the worker's container). "Landed" without this check invites a
    merge of a branch no one else can build. Returns [{path, sha, submodule_repo}] per UNREACHABLE
    changed gitlink. Fail-open: only a positive 'commit not found' (404/422) marks a gitlink
    broken — infra errors and unparseable/off-host submodule URLs are skipped (can't check ≠
    broken); partial findings survive a mid-scan error."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return []
    if owner.lower() != (github.owner or "").lower():
        return []
    try:
        token = await github.installation_token()
    except GitHubAppError:
        return []
    base = api_base.rstrip("/")
    h = _headers(token)
    broken: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            if not base_branch:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                if meta.status_code >= 400:
                    return []
                base_branch = meta.json().get("default_branch") or "main"
            cmp = await c.get(f"{base}/repos/{owner}/{repo}/compare/{base_branch}...{branch}",
                              headers=h)
            if cmp.status_code != 200:
                return []
            changed = [f.get("filename", "") for f in (cmp.json().get("files") or [])[:50]]
            for path in changed:
                if not path:
                    continue
                r = await c.get(f"{base}/repos/{owner}/{repo}/contents/{path}",
                                headers=h, params={"ref": branch})
                if r.status_code != 200 or not isinstance(r.json(), dict):
                    continue                     # deleted path / directory listing / plain file
                entry = r.json()
                if entry.get("type") != "submodule":
                    continue
                sha = entry.get("sha") or ""
                try:
                    sub_owner, sub_repo = parse_owner_repo(entry.get("submodule_git_url") or "")
                except ValueError:
                    continue                     # can't check ≠ broken (fail-open)
                cr = await c.get(f"{base}/repos/{sub_owner}/{sub_repo}/commits/{sha}", headers=h)
                # GitHub answers 422 ("No commit found for SHA") — or 404 — for a missing commit.
                if cr.status_code in (404, 422):
                    broken.append({"path": path, "sha": sha,
                                   "submodule_repo": f"{sub_owner}/{sub_repo}"})
    except (httpx.HTTPError, ValueError):
        return broken                            # keep positives found before the infra error
    return broken


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


async def read_branch_changes(
    github: GitHubApp, repo_url: str, branch: str, *, base_branch: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> tuple[str, list[str], list[str]]:
    """(base_branch, commit subjects, file-change lines) for `base...branch` — the DESCRIPTIVE
    content of a delivery-PR body (corpus D1: the PR carries the intent + the changes; chat
    instructions belong in Mattermost, not the PR). Best-effort: ('', [], []) on any failure —
    the caller degrades to a minimal body."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return "", [], []
    if owner.lower() != (github.owner or "").lower():
        return "", [], []
    try:
        token = await github.installation_token()
    except GitHubAppError:
        return "", [], []
    base = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            if not base_branch:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                if meta.status_code >= 400:
                    return "", [], []
                base_branch = meta.json().get("default_branch") or "main"
            cmp = await c.get(f"{base}/repos/{owner}/{repo}/compare/{base_branch}...{branch}", headers=h)
            if cmp.status_code != 200:
                return base_branch, [], []
            d = cmp.json()
    except (httpx.HTTPError, ValueError):
        return base_branch, [], []
    commits = [
        (c.get("commit", {}).get("message") or "").splitlines()[0][:120]
        for c in (d.get("commits") or [])[:10]
    ]
    files = [
        f"`{f.get('filename')}` (+{f.get('additions', 0)}/−{f.get('deletions', 0)})"
        for f in (d.get("files") or [])[:25]
    ]
    return base_branch, [s for s in commits if s], files


async def open_pull_request(
    github: GitHubApp, repo_url: str, head_branch: str, *,
    title: str, body: str, base_branch: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """DELIVERY-PIPELINE D1 — open the PR that makes delivered work VISIBLE (the 'promotion
    artifact'): branch pushes are easy to miss; a PR shows up in GitHub's UI/notifications with the
    diff, and is the thing the operator reviews + merges (D4 keeps the merge human-gated). Idempotent:
    if a PR for this head already exists, returns it instead of failing. Own account only."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{repo_url}` isn't a valid GitHub repo.", detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return CapabilityResult(ok=False, summary=f"`{owner}/{repo}` isn't on the App's account — can't open a PR.")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            if not base_branch:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                if meta.status_code >= 400:
                    return CapabilityResult(ok=False, summary=f"Couldn't read `{owner}/{repo}` ({meta.status_code}).",
                                            detail=meta.text[:160])
                base_branch = meta.json().get("default_branch") or "main"
            r = await c.post(f"{base}/repos/{owner}/{repo}/pulls", headers=h,
                             json={"title": title, "head": head_branch, "base": base_branch, "body": body})
            if r.status_code == 422:
                # usually "A pull request already exists" — surface the existing one (idempotent)
                ex = await c.get(f"{base}/repos/{owner}/{repo}/pulls"
                                 f"?head={owner}:{head_branch}&state=open", headers=h)
                if ex.status_code == 200 and ex.json():
                    d = ex.json()[0]
                    return CapabilityResult(
                        ok=True, summary=f"PR **#{d['number']}** already open for `{head_branch}`",
                        url=d.get("html_url", ""), detail=str(d.get("number", "")))
                return CapabilityResult(ok=False, summary=f"GitHub rejected the PR for `{head_branch}` (422).",
                                        detail=r.text[:200])
            if r.status_code >= 400:
                return CapabilityResult(ok=False, summary=f"PR for `{head_branch}` failed ({r.status_code}).",
                                        detail=r.text[:200])
            d = r.json()
            return CapabilityResult(
                ok=True, summary=f"PR **#{d['number']}** opened: `{head_branch}` → `{base_branch}`",
                url=d.get("html_url", ""), detail=str(d.get("number", "")))
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to open the PR for `{head_branch}`.",
                                detail=str(exc)[:160])


async def merge_pull_request(
    github: GitHubApp, repo_url: str, pr_number: int, *,
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """DELIVERY-PIPELINE D4 — the HUMAN-GATED merge. Runs ONLY after the operator's explicit
    `approve merge-…` (the §3 hard-gate clearance — merge is irreversible); the bridge then merges via
    the host API with a merge commit (the `--no-ff` equivalent). No auto-merge; no agent authority."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{repo_url}` isn't a valid GitHub repo.", detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return CapabilityResult(ok=False, summary=f"`{owner}/{repo}` isn't on the App's account — can't merge.")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            r = await c.put(f"{base}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
                            headers=_headers(token), json={"merge_method": "merge"})
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to merge PR #{pr_number}.",
                                detail=str(exc)[:160])
    if r.status_code == 200:
        return CapabilityResult(ok=True, summary=f"PR **#{pr_number}** merged into `{owner}/{repo}` (merge commit)",
                                url=f"https://github.com/{owner}/{repo}/pull/{pr_number}")
    if r.status_code in (405, 409):
        return CapabilityResult(ok=False, summary=f"PR #{pr_number} isn't mergeable ({r.status_code}) — "
                                f"conflicts or branch protection; resolve on GitHub.", detail=r.text[:160])
    return CapabilityResult(ok=False, summary=f"Merge of PR #{pr_number} failed ({r.status_code}).",
                            detail=r.text[:160])


async def bump_submodule(
    github: GitHubApp, engine_url: str, submodule_path: str, commit_sha: str, *,
    branch: str, base_branch: str = "", message: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """Composition wiring-back: point `engine_url`'s submodule at `submodule_path` to `commit_sha` on a
    new `branch`, via the GitHub Git Data API — NO checkout, NO worker. This is how you bump a submodule
    programmatically: a tree entry with mode `160000`, type `commit` IS a gitlink. Steps: read the base
    branch's commit+tree → create a tree (base_tree + the gitlink entry) → create a commit → create the
    branch ref. Additive (a new branch); merge to the engine's `main` stays human-gated (D4). Own
    account only (the App can only write repos it manages)."""
    try:
        owner, repo = parse_owner_repo(engine_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{engine_url}` isn't a valid GitHub repo.", detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return CapabilityResult(ok=False, summary=f"`{owner}/{repo}` isn't on the App's account — can't bump it.")
    if not commit_sha:
        return CapabilityResult(ok=False, summary="No submodule commit to bump to (the worker pushed nothing).")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    h = _headers(token)
    msg = message or f"Bump {submodule_path} to {commit_sha[:10]} (composition wiring)"
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            # resolve the base branch (default branch if not given) → its commit + tree
            if not base_branch:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                if meta.status_code >= 400:
                    return CapabilityResult(ok=False, summary=f"Couldn't read `{owner}/{repo}` ({meta.status_code}).",
                                            detail=meta.text[:160])
                base_branch = meta.json().get("default_branch") or "main"
            ref = await c.get(f"{base}/repos/{owner}/{repo}/git/ref/heads/{base_branch}", headers=h)
            if ref.status_code >= 400:
                return CapabilityResult(ok=False, summary=f"Couldn't read `{base_branch}` of `{owner}/{repo}`.",
                                        detail=ref.text[:160])
            base_commit = ref.json()["object"]["sha"]
            commit_obj = await c.get(f"{base}/repos/{owner}/{repo}/git/commits/{base_commit}", headers=h)
            base_tree = commit_obj.json()["tree"]["sha"]
            # a tree entry with mode 160000 / type commit IS the submodule gitlink — point it at commit_sha
            tree = await c.post(
                f"{base}/repos/{owner}/{repo}/git/trees", headers=h,
                json={"base_tree": base_tree,
                      "tree": [{"path": submodule_path, "mode": "160000", "type": "commit", "sha": commit_sha}]})
            if tree.status_code >= 400:
                return CapabilityResult(ok=False, summary=f"Couldn't build the bumped tree for `{submodule_path}`.",
                                        detail=tree.text[:160])
            new_tree = tree.json()["sha"]
            commit = await c.post(
                f"{base}/repos/{owner}/{repo}/git/commits", headers=h,
                json={"message": msg, "tree": new_tree, "parents": [base_commit]})
            if commit.status_code >= 400:
                return CapabilityResult(ok=False, summary="Couldn't create the bump commit.", detail=commit.text[:160])
            new_commit = commit.json()["sha"]
            # create the branch (or fast-forward it if it already exists)
            mk = await c.post(
                f"{base}/repos/{owner}/{repo}/git/refs", headers=h,
                json={"ref": f"refs/heads/{branch}", "sha": new_commit})
            if mk.status_code == 422:  # ref exists → update it
                mk = await c.patch(f"{base}/repos/{owner}/{repo}/git/refs/heads/{branch}", headers=h,
                                   json={"sha": new_commit, "force": True})
            if mk.status_code >= 400:
                return CapabilityResult(ok=False, summary=f"Couldn't create branch `{branch}` on `{owner}/{repo}`.",
                                        detail=mk.text[:160])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return CapabilityResult(ok=False, summary=f"Bumping `{submodule_path}` in `{owner}/{repo}` failed.",
                                detail=str(exc)[:160])
    return CapabilityResult(
        ok=True,
        summary=f"Bumped `{submodule_path}` → `{commit_sha[:10]}` on `{owner}/{repo}` branch `{branch}`",
        url=f"https://github.com/{owner}/{repo}/tree/{branch}",
    )
