"""project-registry — the repos the org works on (COMMS-MODEL §4: channel = project = repo).

The org is NOT bound to one repo. An operator onboards any project via `/project add <name>
<repo>` (Mattermost); each project maps a `#proj-<slug>` channel to a repo URL. At dispatch the
bridge resolves an effort's project → repo and focuses the worker on it (little-coder `/project`
clone). `AO_DEFAULT_REPO` is only the FALLBACK for a `#mgmt` request that doesn't name a project.

This module is pure registry state; the egress-allowlist side-effect (a project's git host must be
reachable) is owned by `EgressAllowlist`, driven off `hosts()`.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select

from ..db import Database
from ..models import Project
from .audit_sink import AuditSink

log = logging.getLogger("agent_bridge.projects")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return s or "project"


def host_of(repo_url: str) -> str:
    """Extract the host from a git remote URL — https/http/ssh/git schemes AND the scp-like
    `git@host:owner/repo` form. Returns "" if unparseable."""
    u = (repo_url or "").strip()
    # scp-like: [user@]host:path
    m = re.match(r"^[\w.+-]+@([^:/]+):", u)
    if m:
        return m.group(1).lower()
    # scheme://[user@]host[:port]/...
    m = re.match(r"^[a-zA-Z][\w+.\-]*://(?:[^@/]+@)?([^:/?#]+)", u)
    if m:
        return m.group(1).lower()
    # bare host/path (has a dotted host before the first slash)
    m = re.match(r"^([^/:@\s]+\.[^/:@\s]+)/", u)
    if m:
        return m.group(1).lower()
    return ""


def owner_of(repo_url: str) -> str:
    """The owner/org segment of a git URL — e.g. 'PolyshDesign' from
    `https://github.com/PolyshDesign/repo.git` (or `git@github.com:PolyshDesign/repo.git`).
    Case preserved; "" if unparseable. For nested groups the top-level group is returned."""
    u = (repo_url or "").strip()
    m = re.match(r"^[\w.+-]+@[^:/]+:(?P<path>.+)$", u)                 # scp: git@host:owner/repo
    if not m:
        m = re.match(r"^[a-zA-Z][\w+.\-]*://(?:[^@/]+@)?[^/]+/(?P<path>.+)$", u)  # scheme://host/owner/repo
    path = (m.group("path") if m else "").strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    return parts[0] if len(parts) >= 2 else ""


def owner_token_env(repo_url: str) -> str:
    """The per-owner deploy-token env-var NAME by convention: `LC_<OWNER>_TOKEN`
    (e.g. PolyshDesign → `LC_POLYSHDESIGN_TOKEN`). "" if the owner can't be parsed. The bridge
    uses this env var only if it is actually SET; otherwise it falls back to the pool `LC_DEPLOY_TOKEN`."""
    owner = owner_of(repo_url)
    if not owner:
        return ""
    return "LC_" + re.sub(r"[^A-Za-z0-9]+", "_", owner).upper().strip("_") + "_TOKEN"


def _row(p: Project) -> dict:
    return {
        "slug": p.slug, "name": p.name, "repo_url": p.repo_url, "git_host": p.git_host,
        "channel_id": p.channel_id, "created_by": p.created_by, "active": p.active,
        "token_env": p.token_env, "upstream_url": p.upstream_url,
        "check_cmd": getattr(p, "check_cmd", None),
    }


class ProjectRegistry:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    async def add(
        self, name: str, repo_url: str, *, channel_id: str | None = None,
        created_by: str = "operator", token_env: str | None = None,
        upstream_url: str | None = None,
    ) -> dict:
        """Register (or update) a project. Slug is derived from `name`. `token_env` = the NAME of the
        env var holding this project's deploy token (multi-PAT). `upstream_url` = the fork PARENT (if
        this project is a fork), re-baked as the `upstream` remote on every focus. Returns the row."""
        slug = slugify(name)
        host = host_of(repo_url)
        async with self.db.session_factory() as s:
            p = await s.get(Project, slug)
            if p is None:
                p = Project(slug=slug, name=name, repo_url=repo_url, git_host=host,
                            channel_id=channel_id, created_by=created_by, active=True,
                            token_env=token_env, upstream_url=upstream_url)
                s.add(p)
            else:
                p.name, p.repo_url, p.git_host, p.active = name, repo_url, host, True
                if channel_id:
                    p.channel_id = channel_id
                if token_env is not None:
                    p.token_env = token_env
                if upstream_url is not None:
                    p.upstream_url = upstream_url
            await s.commit()
            await s.refresh(p)
            row = _row(p)
        await self.audit.log("project_added", actor=created_by,
                             payload={"slug": slug, "repo": repo_url, "host": host,
                                      "token_env": token_env, "upstream": upstream_url})
        return row

    async def set_channel(self, slug: str, channel_id: str) -> None:
        async with self.db.session_factory() as s:
            p = await s.get(Project, slug)
            if p is not None and not p.channel_id:
                p.channel_id = channel_id
                await s.commit()

    async def set_check(self, slug: str, check_cmd: str) -> bool:
        """Set the project's D2 check/test command (run on delivered PR branches before the merge
        gate; red routes back to the effort). Empty string clears it. False if the project is
        unknown."""
        async with self.db.session_factory() as s:
            p = await s.get(Project, slug)
            if p is None:
                return False
            p.check_cmd = check_cmd.strip() or None
            await s.commit()
        await self.audit.log("project_check_set", payload={"slug": slug, "check_cmd": check_cmd[:200]})
        return True

    async def get(self, slug: str) -> dict | None:
        async with self.db.session_factory() as s:
            p = await s.get(Project, slug)
        return _row(p) if p else None

    async def resolve(self, name_or_slug: str) -> dict | None:
        """Find a project by slug OR display name (operator may type either)."""
        if not name_or_slug:
            return None
        want = slugify(name_or_slug)
        async with self.db.session_factory() as s:
            p = await s.get(Project, want)
            if p is not None and p.active:   # a removed/forgotten project must NOT resolve
                return _row(p)
            rows = (await s.execute(select(Project).where(Project.active.is_(True)))).scalars().all()
        low = name_or_slug.strip().lower()
        for p in rows:
            if p.name.lower() == low or p.slug == want:
                return _row(p)
        return None

    async def repo_for(self, slug: str) -> str | None:
        p = await self.get(slug)
        return p["repo_url"] if p and p["active"] else None

    async def upstream_for(self, slug: str) -> str | None:
        """The fork PARENT URL for a project, or None if it isn't a fork."""
        p = await self.get(slug)
        return (p.get("upstream_url") or None) if p and p["active"] else None

    async def set_upstream(self, slug: str, upstream_url: str | None) -> bool:
        """Set/track (or clear, with None) the fork parent on an EXISTING project — so an operator
        can add an upstream after onboarding ('maintain X as upstream'). Returns True if found."""
        async with self.db.session_factory() as s:
            p = await s.get(Project, slugify(slug))
            if p is None or not p.active:
                return False
            p.upstream_url = upstream_url
            await s.commit()
        await self.audit.log("project_upstream_set", actor="operator",
                             payload={"slug": slugify(slug), "upstream": upstream_url})
        return True

    async def list(self) -> list[dict]:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(Project).where(Project.active.is_(True)).order_by(Project.slug))
            ).scalars().all()
        return [_row(p) for p in rows]

    async def remove(self, slug: str, *, actor: str = "operator") -> bool:
        async with self.db.session_factory() as s:
            p = await s.get(Project, slugify(slug))
            if p is None:
                return False
            p.active = False
            await s.commit()
        await self.audit.log("project_removed", actor=actor, payload={"slug": slugify(slug)})
        return True

    async def hosts(self) -> set[str]:
        """Distinct git hosts of active projects — feeds the egress allowlist. Includes each fork's
        UPSTREAM host too (a worker must reach the parent to `git fetch upstream`), so the host
        survives every egress re-render/rebuild without a separate manual allow."""
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(Project.git_host, Project.upstream_url).where(Project.active.is_(True))
                )
            ).all()
        out: set[str] = set()
        for git_host, upstream_url in rows:
            if git_host:
                out.add(git_host)
            uh = host_of(upstream_url or "")
            if uh:
                out.add(uh)
        return out
