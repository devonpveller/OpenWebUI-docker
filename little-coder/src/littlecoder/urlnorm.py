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
    # Optional branch specifier the operator attached to the link via
    # `#<branch>` (npm convention). Does NOT participate in focus_key —
    # same repo at a different branch is still the same focus; the
    # operator can `git checkout` inside the workspace once cloned.
    # WorkspaceManager.clone uses this for the initial `-b <branch>`.
    branch: str | None = None

    @property
    def focus_key(self) -> str:
        """Stable identity used to decide same-vs-different focus."""
        return f"{self.host}/{self.owner}/{self.repo}"

    @property
    def canonical_url(self) -> str:
        """The `repo` value recorded in every journal envelope. The
        branch fragment is intentionally NOT included — journals
        record the repo identity, not the working branch."""
        return f"https://{self.focus_key}"


def normalize_repo_url(link: str) -> NormalizedRepo:
    """Parse any common git URL form into a NormalizedRepo. Raises
    RepoUrlError on anything that is not a host + owner/repo link.

    Branch specifier: a trailing `#<branch>` (npm convention) is
    recognized and carried on `NormalizedRepo.branch`. Examples:

      - https://github.com/foo/bar             → branch=None
      - https://github.com/foo/bar.git#dev     → branch='dev'
      - git@github.com:foo/bar#feature/x       → branch='feature/x'

    The `#<branch>` form is preferred over `@<branch>` (npm/pip-style)
    because `@` is already overloaded as the SSH user-info delimiter
    and produces malformed URLs when treated as a branch separator
    (git returns exit 128). The `#` fragment is URL-safe and never
    appears inside a real repo URL path."""
    if not link or not link.strip():
        raise RepoUrlError("empty repo link")
    s = link.strip()

    # Pull a `#<branch>` fragment off the back FIRST so the rest of
    # the parser doesn't see it. Forward-slashes inside the branch
    # are allowed (e.g. `feature/x`); `#` chars beyond the first are
    # not allowed (real branch names can't contain them anyway).
    branch: str | None = None
    if "#" in s:
        url_part, _, frag = s.partition("#")
        frag = frag.strip()
        if frag:
            # Git branch-name rules: no spaces, no `..`, no control
            # chars, etc. We do a conservative whitelist; anything
            # weird → reject so the operator gets a clear error
            # rather than a `git clone -b <weird>` exit 128.
            if ".." in frag or not _BRANCH_NAME_RE.match(frag):
                raise RepoUrlError(
                    f"unusable branch fragment {frag!r} in {link!r}"
                )
            branch = frag
        s = url_part

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
        branch=branch,
    )


# Branch-name whitelist — conservative subset of git's actual rules.
# Allowed: alphanumeric, `-`, `_`, `.`, `/`. Reject anything outside
# this set so a typo or shell-quoted artifact doesn't propagate into
# the `git clone -b` argv unchecked.
_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,200}$")
