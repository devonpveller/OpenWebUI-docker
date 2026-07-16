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
    # Net files changed vs base (-1 = unknown/couldn't read). `ahead` counts COMMITS, not
    # substance: a branch can be ahead-by-N with ZERO net file diff (live 2026-07-05: the worker
    # fixed code inside a vendored submodule checkout, re-pointed the gitlink back, and published
    # an engine branch whose commits cancel out — an EMPTY PR claiming the fix). 0 here means the
    # "delivery" changes nothing for consumers.
    files_changed: int = -1
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
            files_changed = -1
            if branch != base:
                cmp = await c.get(
                    f"{base_api}/repos/{owner}/{repo}/compare/{base}...{branch}", headers=h)
                if cmp.status_code == 200:
                    d = cmp.json()
                    ahead = int(d.get("ahead_by", 0) or 0)
                    if "files" in d:      # absent ⇒ unknown (-1), never a false "empty"
                        files_changed = len(d.get("files") or [])
    except (httpx.HTTPError, ValueError) as exc:
        return BranchDelivery(branch=branch, detail=str(exc)[:120])
    return BranchDelivery(verifiable=True, exists=True, ahead=ahead, head_sha=sha,
                          files_changed=files_changed, branch=branch, base=base)


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


async def read_added_lines(
    github: GitHubApp, repo_url: str, branch: str, *, base_branch: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """The ADDED lines (patch `+` lines, not `+++` headers) across `base...branch` — so a
    standing-intent gate can check whether a delivery RE-INTRODUCES a forbidden term at the diff
    level (deterministic, general — no repo-specific logic). Best-effort + bounded; [] on any
    failure (fail-open — an unreadable diff never blocks)."""
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
    added: list[str] = []
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
            for f in (cmp.json().get("files") or [])[:100]:
                for ln in (f.get("patch") or "").splitlines():
                    if ln.startswith("+") and not ln.startswith("+++"):
                        added.append(ln[1:])
                        if len(added) >= 4000:
                            return added
    except (httpx.HTTPError, ValueError):
        return added
    return added


async def read_removal_summary(
    github: GitHubApp, repo_url: str, branch: str, *, base_branch: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> dict:
    """What a delivery REMOVED, for the no-silent-removals gate (operator 2026-07-09: a burn-down
    round DELETED a whole feature file — MouseCursor.Sdl.cs — to make errors go away, and the org
    green-passed it without ever surfacing the removal). Returns
    {deleted_files:[...], gutted_files:[{file,removed,added}], insertions, deletions, removed_symbols:[...]}
    from `base...branch` — deterministic, general, no repo-specific logic. Fail-open: an unreadable
    diff returns empty (never blocks). `gutted_files` = files whose patch removes far more than it
    adds (a body replaced by nothing / a stub). `removed_symbols` = deleted method/type/property
    signatures scraped from the `-` lines (best-effort, language-agnostic-ish)."""
    empty = {"deleted_files": [], "gutted_files": [], "insertions": 0, "deletions": 0,
             "removed_symbols": []}
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return empty
    if owner.lower() != (github.owner or "").lower():
        return empty
    try:
        token = await github.installation_token()
    except GitHubAppError:
        return empty
    base = api_base.rstrip("/")
    h = _headers(token)
    deleted: list[str] = []
    gutted: list[dict] = []
    removed_syms: list[str] = []
    ins = dels = 0
    # a deleted def/method/type/prop on a `-` line — signatures worth surfacing (C#/TS/py/go/etc.)
    sig = re.compile(
        r"\b(?:class|struct|interface|enum|record|def|func|function|void|public|private|"
        r"protected|internal|static)\b.*?\b([A-Z_a-z][\w]*)\s*[\(<{]")
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            if not base_branch:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                if meta.status_code >= 400:
                    return empty
                base_branch = meta.json().get("default_branch") or "main"
            cmp = await c.get(f"{base}/repos/{owner}/{repo}/compare/{base_branch}...{branch}",
                              headers=h)
            if cmp.status_code != 200:
                return empty
            for f in (cmp.json().get("files") or [])[:300]:
                a = int(f.get("additions") or 0)
                d = int(f.get("deletions") or 0)
                ins += a
                dels += d
                st = f.get("status") or ""
                fn = f.get("filename") or "?"
                if st == "removed":
                    deleted.append(fn)
                elif d >= 20 and d >= a * 4:   # far more removed than added → gutted
                    gutted.append({"file": fn, "removed": d, "added": a})
                for ln in (f.get("patch") or "").splitlines():
                    if ln.startswith("-") and not ln.startswith("---"):
                        m = sig.search(ln[1:])
                        if m and len(removed_syms) < 60:
                            removed_syms.append(f"{m.group(1)} ({fn.split('/')[-1]})")
    except (httpx.HTTPError, ValueError):
        return empty
    # de-dup removed symbols, preserve order
    seen: set[str] = set()
    removed_syms = [s for s in removed_syms if not (s in seen or seen.add(s))]
    return {"deleted_files": deleted, "gutted_files": gutted, "insertions": ins,
            "deletions": dels, "removed_symbols": removed_syms}


async def read_sibling_agent_prs(
    github: GitHubApp, repo_url: str, own_branch: str, *,
    api_base: str = "https://api.github.com",
    transport: httpx.BaseTransport | None = None,
) -> list[dict]:
    """The OTHER open agent PRs on the same repo (head `agent/*`, excluding `own_branch`), each
    with its changed files — so a delivery closure can SAY how parallel effort-PRs relate. (Live
    2026-07-05 operator confusion: successive fixes landed on two different effort branches —
    "the worker keeps switching branches and PRs". One-effort-one-branch-one-PR is the D1 design;
    the RELATIONSHIP between parallel PRs must therefore be self-explaining at closure time.)
    Best-effort + bounded (≤5 siblings, ≤50 files each): [] / partial on any failure."""
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
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            r = await c.get(f"{base}/repos/{owner}/{repo}/pulls",
                            headers=h, params={"state": "open", "per_page": 20})
            if r.status_code != 200:
                return []
            for pr in r.json():
                head = ((pr.get("head") or {}).get("ref")) or ""
                if not head.startswith("agent/") or head == own_branch:
                    continue
                fr = await c.get(f"{base}/repos/{owner}/{repo}/pulls/{pr['number']}/files",
                                 headers=h, params={"per_page": 50})
                files = ([f.get("filename", "") for f in fr.json()]
                         if fr.status_code == 200 else [])
                out.append({"number": pr.get("number"), "head": head,
                            "title": pr.get("title") or "",
                            "files": [f for f in files if f]})
                if len(out) >= 5:
                    break
    except (httpx.HTTPError, ValueError):
        return out                       # partial visibility beats none
    return out


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


async def read_merge_base(
    github: GitHubApp, repo_url: str, branch: str, *, base_branch: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> str:
    """The MERGE-BASE commit of `branch` and the repo's default branch — the 'before the fix' point
    for a before/after reproduction check (the code as it was when the branch forked). Returns the
    full sha, or '' if unresolvable (the caller fails closed: no base ⇒ no red→green proof). Generic."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return ""
    try:
        token = await github.installation_token()
    except GitHubAppError:
        return ""
    base = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            if not base_branch:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                if meta.status_code >= 400:
                    return ""
                base_branch = meta.json().get("default_branch") or "main"
            if branch == base_branch:
                return ""
            cmp = await c.get(
                f"{base}/repos/{owner}/{repo}/compare/{base_branch}...{branch}", headers=h)
            if cmp.status_code != 200:
                return ""
            return (cmp.json().get("merge_base_commit", {}) or {}).get("sha", "") or ""
    except (httpx.HTTPError, ValueError):
        return ""


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


async def close_pull_request(
    github: GitHubApp, repo_url: str, pr_number: int, *,
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """Repo hygiene (operator-plane, like merge but REVERSIBLE — a closed PR can be reopened and
    its branch persists): close a superseded/duplicate agent PR on the operator's say-so. Interim
    hand tool until the repo-maintainer role (P5.3 catalog proposal) owns hygiene sweeps."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{repo_url}` isn't a valid GitHub repo.", detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return CapabilityResult(ok=False, summary=f"`{owner}/{repo}` isn't on the App's account — can't close.")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            r = await c.patch(f"{base}/repos/{owner}/{repo}/pulls/{pr_number}",
                              headers=_headers(token), json={"state": "closed"})
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to close PR #{pr_number}.",
                                detail=str(exc)[:160])
    if r.status_code == 200:
        return CapabilityResult(
            ok=True,
            summary=f"PR **#{pr_number}** closed on `{owner}/{repo}` (branch kept; reopen any time)",
            url=f"https://github.com/{owner}/{repo}/pull/{pr_number}")
    return CapabilityResult(ok=False, summary=f"Closing PR #{pr_number} failed ({r.status_code}).",
                            detail=r.text[:160])


async def read_open_pr_numbers(
    github: GitHubApp, repo_url: str, *,
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> set[int] | None:
    """All OPEN PR numbers on a repo — for reconciling stale merge gates against reality
    (operator 2026-07-08: a bare `approve` listed 14 items, 11 of them gates for PRs that no
    longer exist). Returns None when unreadable — the caller fails OPEN (never drops a gate on
    an API hiccup)."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return None
    if owner.lower() != (github.owner or "").lower():
        return None
    try:
        token = await github.installation_token()
    except GitHubAppError:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            r = await c.get(f"{api_base.rstrip('/')}/repos/{owner}/{repo}/pulls",
                            headers=_headers(token), params={"state": "open", "per_page": 100})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return {int(p.get("number") or 0) for p in r.json()}
    except (ValueError, TypeError, AttributeError):
        return None


async def merge_branch(
    github: GitHubApp, repo_url: str, base_branch: str, head_branch: str, *,
    message: str = "", api_base: str = "https://api.github.com",
    transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """Merge one branch into another WITHIN a repo (POST /merges) — the burn-down partition's
    join step: each part-worker pushes `agent/<effort>-ptN`, and the org folds the parts back
    into the effort branch. File-disjoint parts merge cleanly; a 409 conflict is reported (never
    forced) so the caller can fall back to sequential rounds. NOT a PR merge (that's D4,
    human-gated) — this only moves an agent working branch, never a default branch."""
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
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            r = await c.post(
                f"{api_base.rstrip('/')}/repos/{owner}/{repo}/merges", headers=_headers(token),
                json={"base": base_branch, "head": head_branch,
                      "commit_message": message or f"merge {head_branch} into {base_branch}"})
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to merge `{head_branch}`.",
                                detail=str(exc)[:160])
    if r.status_code == 201:
        sha = (r.json().get("sha") or "")[:10]
        return CapabilityResult(ok=True, summary=f"`{head_branch}` merged into `{base_branch}` @ `{sha}`")
    if r.status_code == 204:  # base already contains head
        return CapabilityResult(ok=True, summary=f"`{base_branch}` already contains `{head_branch}`")
    if r.status_code == 409:
        return CapabilityResult(ok=False, summary=f"merge CONFLICT folding `{head_branch}` into "
                                f"`{base_branch}` — parts overlap", detail=r.text[:160])
    return CapabilityResult(ok=False, summary=f"Merging `{head_branch}` failed ({r.status_code}).",
                            detail=r.text[:160])


async def ensure_branch(
    github: GitHubApp, repo_url: str, branch: str, *, from_branch: str = "",
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """Create `branch` at the head of `from_branch` (default: the repo's default branch) if it does
    not already exist — the DEVELOP-INTEGRATION seed (operator 2026-07-15: accepted per-effort PRs
    accumulate on a `develop` branch that converges to the whole product). Idempotent: a present
    branch is left untouched (its accumulated history is the point). Own account only."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{repo_url}` isn't a valid GitHub repo.", detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return CapabilityResult(ok=False, summary=f"`{owner}/{repo}` isn't on the App's account.")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            exists = await c.get(f"{base}/repos/{owner}/{repo}/git/ref/heads/{branch}", headers=h)
            if exists.status_code == 200:
                return CapabilityResult(ok=True, summary=f"`{branch}` already exists on `{owner}/{repo}`")
            src = from_branch
            if not src:
                meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
                src = (meta.json().get("default_branch") or "main") if meta.status_code == 200 else "main"
            ref = await c.get(f"{base}/repos/{owner}/{repo}/git/ref/heads/{src}", headers=h)
            if ref.status_code >= 400:
                return CapabilityResult(ok=False, summary=f"Couldn't read `{src}` of `{owner}/{repo}`.",
                                        detail=ref.text[:160])
            sha = ref.json()["object"]["sha"]
            r = await c.post(f"{base}/repos/{owner}/{repo}/git/refs", headers=h,
                             json={"ref": f"refs/heads/{branch}", "sha": sha})
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to create `{branch}`.",
                                detail=str(exc)[:160])
    if r.status_code == 201:
        return CapabilityResult(ok=True, summary=f"branch `{branch}` created on `{owner}/{repo}` "
                                f"from `{src}` @ `{sha[:10]}`")
    if r.status_code == 422:      # a race created it between our check and create — fine
        return CapabilityResult(ok=True, summary=f"`{branch}` already exists on `{owner}/{repo}`")
    return CapabilityResult(ok=False, summary=f"Creating `{branch}` on `{owner}/{repo}` failed "
                            f"({r.status_code}).", detail=r.text[:160])


async def delete_branch(
    github: GitHubApp, repo_url: str, branch: str, *,
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> CapabilityResult:
    """Repo hygiene — IRREVERSIBLE (commits reachable only from this branch become garbage):
    runs ONLY on the operator's explicit, branch-NAMING instruction (their words are the §3
    clearance, like "merge it"). The caller restricts this to `agent/*` branches; here we refuse
    the repo's default branch outright (belt-and-braces)."""
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError as exc:
        return CapabilityResult(ok=False, summary=f"`{repo_url}` isn't a valid GitHub repo.", detail=str(exc))
    if owner.lower() != (github.owner or "").lower():
        return CapabilityResult(ok=False, summary=f"`{owner}/{repo}` isn't on the App's account — can't delete.")
    try:
        token = await github.installation_token()
    except GitHubAppError as exc:
        return CapabilityResult(ok=False, summary="The GitHub App isn't ready.", detail=str(exc))
    base = api_base.rstrip("/")
    h = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
            meta = await c.get(f"{base}/repos/{owner}/{repo}", headers=h)
            default = (meta.json().get("default_branch") or "main") if meta.status_code == 200 else "main"
            if branch == default:
                return CapabilityResult(ok=False, summary=f"`{branch}` is `{owner}/{repo}`'s DEFAULT "
                                        f"branch — refusing to delete it.")
            r = await c.delete(f"{base}/repos/{owner}/{repo}/git/refs/heads/{branch}", headers=h)
    except httpx.HTTPError as exc:
        return CapabilityResult(ok=False, summary=f"Couldn't reach GitHub to delete `{branch}`.",
                                detail=str(exc)[:160])
    if r.status_code == 204:
        return CapabilityResult(ok=True, summary=f"branch `{branch}` deleted from `{owner}/{repo}`")
    if r.status_code in (404, 422):
        return CapabilityResult(ok=False, summary=f"`{branch}` doesn't exist on `{owner}/{repo}`.")
    return CapabilityResult(ok=False, summary=f"Deleting `{branch}` on `{owner}/{repo}` failed "
                            f"({r.status_code}).", detail=r.text[:160])


async def classify_agent_branches(
    github: GitHubApp, repo_url: str, *,
    api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None,
) -> dict:
    """Classify a repo's `agent/*` branches by merge state so the org can reason about hygiene
    the way a human does — "these were already merged and no longer need to be here" (operator
    2026-07-10). Returns:
      {"default": <default branch>,
       "merged":   [names whose commits are ALL in the default branch — safe to delete, zero loss],
       "unmerged": [{"name","ahead"} — carries commits NOT in the default branch],
       "open_pr":  [names with an OPEN PR — keep, they're live]}
    Merge test = compare(default...branch).ahead_by == 0 (every commit is already contained).
    Fail-SAFE: a branch we can't classify is dropped from all buckets (never called deletable)."""
    owner, name = parse_owner_repo(repo_url)
    token = await github.installation_token()
    base = api_base.rstrip("/")
    h = _headers(token)
    # `dates`: branch → last-commit ISO (for supersession/staleness); `pr_num`: branch → open PR #
    # (so a superseded branch's PR can be closed when it is reaped). Additive — existing keys unchanged.
    out: dict = {"default": "main", "merged": [], "unmerged": [], "open_pr": [],
                 "dates": {}, "pr_num": {}}
    async with httpx.AsyncClient(timeout=30.0, transport=transport) as c:
        meta = await c.get(f"{base}/repos/{owner}/{name}", headers=h)
        if meta.status_code == 200:
            out["default"] = meta.json().get("default_branch") or "main"
        default = out["default"]
        # all agent/* branches (paginated) + each branch's HEAD commit date (from the list item)
        agent: list[str] = []
        page = 1
        while True:
            r = await c.get(f"{base}/repos/{owner}/{name}/branches", headers=h,
                            params={"per_page": 100, "page": page})
            if r.status_code != 200:
                break
            b = r.json()
            for x in b:
                nm = x.get("name") or ""
                if nm.startswith("agent/"):
                    agent.append(nm)
            if len(b) < 100:
                break
            page += 1
        # heads of OPEN PRs → keep the branch→PR# map (a superseded PR is CLOSED when reaped, not left)
        open_heads: set[str] = set()
        page = 1
        while True:
            r = await c.get(f"{base}/repos/{owner}/{name}/pulls", headers=h,
                            params={"state": "open", "per_page": 100, "page": page})
            if r.status_code != 200:
                break
            j = r.json()
            for p in j:
                ref = (p.get("head") or {}).get("ref")
                if ref:
                    open_heads.add(ref)
                    out["pr_num"][ref] = p.get("number")
            if len(j) < 100:
                break
            page += 1
        for b in sorted(set(agent)):
            # last-commit date (single-branch GET carries the nested committer date) — best-effort
            br = await c.get(f"{base}/repos/{owner}/{name}/branches/{b}", headers=h)
            if br.status_code == 200:
                out["dates"][b] = (((br.json().get("commit") or {}).get("commit") or {})
                                   .get("committer") or {}).get("date") or ""
            if b in open_heads:
                out["open_pr"].append(b)
                continue
            cmp = await c.get(f"{base}/repos/{owner}/{name}/compare/{default}...{b}", headers=h)
            if cmp.status_code != 200:
                continue  # unknown → leave it alone (never classify as safe-to-delete)
            ahead = cmp.json().get("ahead_by")
            if ahead == 0:
                out["merged"].append(b)
            elif isinstance(ahead, int):
                out["unmerged"].append({"name": b, "ahead": ahead})
    return out


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
