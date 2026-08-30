"""THE VENUE: which repository the experiment is performed ON, checked rather than assumed.

WHY THIS FILE EXISTS - a defect this package shipped, found by a verifier and not by us.

dark-factory-unification PLAN section 2 binds the term four lines above the phase table:

    "'gym' means measured runs in `ai-orchestration-gym`, never live planes or a real
     target."

Every U-phase whose *Validated by* column begins "Gym:" therefore names TWO things: an
experiment, and the place it is performed. This package measured the experiment with four
mechanisms and represented the place NOWHERE. So a complete, evidenced, exit-0 comparison -
four cells, real dispatches, real acceptance runs, a declared-matrix lock - was produced
against ai-stack itself, with `target: self` resolving to the repository the harness lives
in, and nothing in the record, the report or the config could tell anyone. The word "Gym:"
had even been dropped from the config's own restatement of the column.

That is this package's own stated failure mode one layer up: not a wrong number, but a
MISSING dimension read as a satisfied one.

WHAT A VENUE IS. A name, a KIND, a repository and a ref. The repository is the SUBJECT of
the experiment - the thing the item is planted into and the runner works on. It is not
where the harness's code lives, not where the evidence is written, and not which containers
run the work: using little-coder as a RUNNER is fine, because the preamble forbids a live
plane or a real repo being the SUBJECT.

THE ONE CHECK THAT MATTERS. `kind: "gym"` declares a disposable arena. `probe` refuses when
such a venue resolves to the harness's OWN repository - compared by git common dir, so a
worktree of ai-stack is recognised as ai-stack rather than as "a different path". That is
the exact sentence a verifier had to write by hand; here it is an exit code.

WHAT IT DELIBERATELY DOES NOT DO. It does not decide that a venue is "safe". A gym repo can
still hold something precious, and no probe can know. It answers one question - is the
subject of this experiment a repository other than the one under test, of the kind the
column demands - and it answers it the same way every time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from . import proc as _proc


class VenueConfigError(ValueError):
    """The venue section is malformed. Wrapped as QuadrantConfigError at the matrix boundary."""


@dataclass(frozen=True)
class Venue:
    name: str
    kind: str
    repo: Path
    ref: str
    source: str = ""           # how `repo` was arrived at - config path, env var, or flag
    rules: Dict[str, Any] = field(default_factory=dict)

    @property
    def satisfies_gym_column(self) -> bool:
        return bool(self.rules.get("satisfies_gym_column"))

    def as_record(self) -> Dict[str, Any]:
        """What a run record carries. A record with no venue is not admissible."""
        return {"name": self.name, "kind": self.kind, "repo": str(self.repo),
                "ref": self.ref, "source": self.source}


@dataclass(frozen=True)
class VenueCheck:
    """Ready, or not-ready WITH A REASON - the contract matrix.PreflightResult keeps.

    A local dataclass rather than that class because this module must stay a LEAF: matrix
    imports venue, so venue importing matrix would be a cycle.
    """
    ready: bool
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ready and not (self.reason or "").strip():
            raise ValueError("a not-ready VenueCheck must carry a reason")


def _section(cfg: Dict[str, Any]) -> Dict[str, Any]:
    q = (cfg or {}).get("quadrant")
    return q if isinstance(q, dict) else {}


def validate_shape(cfg: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Config-only validation: no filesystem, no git. Returns the selected venue's block.

    Kept apart from `resolve` on the same principle matrix.py states for its two kinds of
    "no": a typo in the operator's file is a CONFIG ERROR and must be loud, while a venue
    that cannot be reached today is a fact about the world and belongs in a preflight
    reason. This half is the typo half.
    """
    q = _section(cfg)
    name = str(q.get("venue") or "").strip()
    venues = q.get("venues")
    venues = venues if isinstance(venues, dict) else {}
    if not name:
        raise VenueConfigError(
            "quadrant.venue is not set. PLAN section 2's preamble binds every 'Gym:' column "
            "to a venue - 'measured runs in ai-orchestration-gym, never live planes or a "
            "real target' - so a comparison that does not say where it ran cannot be judged "
            "against one (defined venues: {})".format(", ".join(sorted(venues)) or "none"))
    v = venues.get(name)
    if not isinstance(v, dict):
        raise VenueConfigError(
            "quadrant.venue names '{}', which no 'quadrant.venues' entry defines "
            "(defined: {})".format(name, ", ".join(sorted(venues)) or "none"))
    missing = [f for f in schema.get("venue_required_fields", [])
               if not str(v.get(f) or "").strip()]
    if missing:
        raise VenueConfigError(
            "venue '{}' is missing required field(s): {}".format(name, ", ".join(missing)))
    kinds = schema.get("venue_kinds") or {}
    kind = str(v.get("kind") or "").strip()
    if kind not in kinds:
        raise VenueConfigError(
            "venue '{}' declares kind '{}', which this harness has no rules for (known: {}). "
            "A venue kind the harness cannot check is one it must not silently accept."
            .format(name, kind, ", ".join(sorted(kinds)) or "none"))
    return dict(v)


def main_checkout(harness_repo: Path) -> Path:
    """The repository root a relative venue path is resolved against.

    NOT the caller's repo root: harness sessions run in `.claude/worktrees/wt-<id>`, so
    resolving '../ai-orchestration-gym' against the worktree would point at
    `.claude/worktrees/ai-orchestration-gym` - a path that does not exist, and would have
    produced a BLOCKED cell whose reason blamed the arena instead of the resolution. The
    git COMMON dir is shared by every worktree of a repository, so its parent is the main
    checkout from anywhere.
    """
    out = _proc.run(["git", "-C", str(harness_repo), "rev-parse", "--git-common-dir"])
    if out.returncode != 0:
        return Path(harness_repo)
    common = Path((out.stdout or "").strip())
    if not common.is_absolute():
        common = Path(harness_repo) / common
    return common.resolve().parent


def resolve(cfg: Dict[str, Any], schema: Dict[str, Any], *, harness_repo: Path,
            override_repo: str = "") -> Venue:
    """Config + environment + flag -> the venue this comparison is over.

    Precedence, most explicit first: an explicit --repo, then the venue's own env var, then
    the configured path. Every one of them is recorded in `source`, because "which repo did
    this actually run against" is the question the whole module exists to answer and an
    answer with no provenance is the thing being fixed.
    """
    v = validate_shape(cfg, schema)
    name = str(_section(cfg).get("venue")).strip()
    kinds = schema.get("venue_kinds") or {}
    kind = str(v["kind"]).strip()

    env_key = str(v.get("repo_env") or "").strip()
    env_val = (os.environ.get(env_key) or "").strip() if env_key else ""
    if override_repo:
        raw, source = override_repo, "--repo"
    elif env_val:
        raw, source = env_val, "${}".format(env_key)
    else:
        raw, source = str(v["repo"]), "config quadrant.venues.{}.repo".format(name)

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = main_checkout(Path(harness_repo)) / p
    try:
        repo = p.resolve()
    except OSError:
        repo = p
    return Venue(name=name, kind=kind, repo=repo,
                 ref=str(v.get("ref") or "HEAD").strip() or "HEAD",
                 source=source, rules=dict(kinds.get(kind) or {}))


def _common_dir(repo: Path) -> str:
    """The identity of the repository CONTAINING `repo`: its shared git dir, lowercased.

    "Containing", not "at": git discovers upward, and `probe` is what turns that into a
    refusal (see `_toplevel`). The `.exists()` guard is belt-and-braces on a resolved path.
    """
    out = _proc.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"])
    if out.returncode != 0:
        return ""
    common = Path((out.stdout or "").strip())
    if not common.is_absolute():
        common = repo / common
    try:
        common = common.resolve()
    except OSError:
        return ""
    if not common.exists():
        return ""
    return str(common).lower()


def _toplevel(repo: Path) -> str:
    out = _proc.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"])
    if out.returncode != 0:
        return ""
    try:
        return str(Path((out.stdout or "").strip()).resolve()).lower()
    except OSError:
        return ""


def probe(v: Venue, *, harness_repo: Path) -> VenueCheck:
    """Is this a usable venue of the kind it claims to be?

    Four questions, in the order whose failure is most informative:
      1. is there a git repository there at all;
      2. is that repository AT the configured path, rather than merely above it;
      3. does the ref the experiment is performed against exist in it;
      4. for a kind that must be disposable, is it a DIFFERENT repository from the harness's.

    QUESTION 2 IS NOT PEDANTRY, and it was not in the first version of this file. git
    discovers a repository by walking UP. So a venue path that is wrong - a typo, a
    checkout that was never made, a relative path resolved from the wrong base - does not
    fail: it silently resolves to whatever repository encloses it. Measured on this machine
    while writing the tests for this module: `C:/Users/<user>` is itself a git repository,
    so EVERY path under the user's home - including the system temp directory - answers
    `git rev-parse` with the user's home repo. A mistyped arena path under a home directory
    would therefore have made the operator's personal repository the SUBJECT of an
    experiment, which PLAN section C.2 puts in class 4 (the personal data plane) and which
    no amount of "it is a git repo" would have caught.
    """
    if not v.repo.is_dir():
        return VenueCheck(False, reason=(
            "venue '{}' resolves to '{}' ({}), which is not a directory. The arena is a "
            "checkout on this machine; clone or check it out, or point {} somewhere it "
            "exists.".format(v.name, v.repo, v.source, v.source)))
    subject = _common_dir(v.repo)
    if not subject:
        return VenueCheck(False, reason=(
            "venue '{}' at '{}' is not a git repository (git rev-parse --git-common-dir "
            "failed). The item is planted into a worktree of this repo, so a non-repo "
            "cannot be a venue.".format(v.name, v.repo)))
    top = _toplevel(v.repo)
    here = str(v.repo).lower()
    try:
        here = str(v.repo.resolve()).lower()
    except OSError:
        pass
    if top and top != here:
        return VenueCheck(False, reason=(
            "venue '{}' resolves to '{}' ({}), which is NOT a repository root - it is a "
            "directory inside the repository at '{}'. git discovers upward, so a wrong "
            "venue path does not fail, it silently adopts whatever repo encloses it. Point "
            "{} at the arena checkout itself.".format(v.name, v.repo, v.source, top,
                                                      v.source)))

    rev = _proc.run(["git", "-C", str(v.repo), "rev-parse", "--verify", "--quiet",
                     v.ref + "^{commit}"])
    if rev.returncode != 0:
        return VenueCheck(False, reason=(
            "venue '{}' names ref '{}', which does not resolve to a commit in '{}'. The ref "
            "is the arena the experiment runs against and it is not guessed."
            .format(v.name, v.ref, v.repo)))
    head = (rev.stdout or "").strip()

    if v.rules.get("must_differ_from_harness_repo"):
        ours = _common_dir(Path(harness_repo))
        if ours and subject == ours:
            return VenueCheck(False, reason=(
                "VENUE VIOLATION: venue '{}' is kind '{}', and it resolved to the harness's "
                "OWN repository ({}, git dir {}). PLAN section 2's preamble: \"'gym' means "
                "measured runs in `ai-orchestration-gym`, never live planes or a real "
                "target.\" A run whose SUBJECT is the repository under test is not a gym "
                "run, however real its dispatches were. Point {} at the arena checkout."
                .format(v.name, v.kind, v.repo, subject, v.source)))
    return VenueCheck(True, detail={"repo": str(v.repo), "kind": v.kind, "ref": v.ref,
                                    "head": head, "source": v.source,
                                    "git_common_dir": subject})
