"""Repository-URL normalization for project focus (design §12.3).

URL normalization prevents spurious workspace wipes when the SSH and HTTPS
forms of the same repo are used interchangeably. Both forms reduce to the
same `focus_key` (host + owner + repo, lowercased); the canonical URL is the
`repo` value written to every journal envelope (design §4.1).
"""

from __future__ import annotations

import dataclasses
import re

# scp-like SSH shorthand: [user@]host:path  (e.g. git@github.com:acme/widget.git)
_SCP_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


class RepoUrlError(ValueError):
    """The supplied repo link could not be parsed into host/owner/repo."""


@dataclasses.dataclass(frozen=True)
class NormalizedRepo:
    host: str
    owner: str  # may contain '/' for nested groups (e.g. GitLab subgroups)
    repo: str

    @property
    def focus_key(self) -> str:
        """Stable identity used to decide same-vs-different focus."""
        return f"{self.host}/{self.owner}/{self.repo}"

    @property
    def canonical_url(self) -> str:
        """The `repo` value recorded in every journal envelope."""
        return f"https://{self.focus_key}"


def normalize_repo_url(link: str) -> NormalizedRepo:
    """Parse any common git URL form into a NormalizedRepo. Raises
    RepoUrlError on anything that is not a host + owner/repo link."""
    if not link or not link.strip():
        raise RepoUrlError("empty repo link")
    s = link.strip()

    if "://" in s:  # scheme://[user@]host[:port]/path
        _scheme, rest = s.split("://", 1)
        authority = rest.split("/", 1)[0]
        if "@" in authority:  # drop user info
            rest = rest.split("@", 1)[1]
        if "/" not in rest:
            raise RepoUrlError(f"no path in repo link: {link!r}")
        hostport, path = rest.split("/", 1)
        host = hostport.split(":", 1)[0]
    else:
        m = _SCP_RE.match(s)
        if not m:
            raise RepoUrlError(f"unrecognized repo link: {link!r}")
        host = m.group("host")
        path = m.group("path")

    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if not host or len(parts) < 2:
        raise RepoUrlError(f"repo link needs host and owner/repo: {link!r}")

    return NormalizedRepo(
        host=host.lower(),
        owner="/".join(parts[:-1]).lower(),
        repo=parts[-1].lower(),
    )
