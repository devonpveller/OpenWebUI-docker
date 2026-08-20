"""project-context — cached Stage-1 anchor for the readiness gate (UX-FLOW Stage 1, P3.8).

The readiness gate must ANCHOR to the existing project ("what's actually there — existing code,
branch, conventions") so it resolves placement/language/pattern itself instead of asking the
operator. Surveying the repo is a worker task (a GPU cycle), so we do it **once per project** and
cache the factual summary, reusing it across every effort on that project.

P8 #5 (2026-07-16): the cache is keyed by the BASE COMMIT the survey was taken at. Fresh-wiped
workspaces (the provenance fix) made "clean" mean "blind" — a worker burned 26 read-only tool
calls re-discovering a tiny template and tripped the flail guard. The answer is not stale state,
it's a shared map: same base ⇒ every effort reuses the one survey; base moved ⇒ re-survey ONCE
and share the new map. The summary is also injected into the worker's brief on dispatch, so a
wiped workspace costs a map lookup, not 26 blind reads.

Best-effort by design: if surveying is disabled, there's no repo, or the survey fails, `ensure`
returns "" and the gate degrades to conventions-only anchoring (never blocks intake). The survey
function is injected (the router's `survey_project` in prod; a fake in tests), so this module has
no worker/scheduler coupling of its own.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("agent_bridge.project_context")

# (repo) -> factual summary
SurveyFn = Callable[[str], Awaitable[str]]


class ProjectContext:
    def __init__(self, survey_fn: SurveyFn, *, enabled: bool = True) -> None:
        self._survey = survey_fn
        self._enabled = enabled
        # {project: (base_sha, summary)} — base_sha "" = surveyed without a known base (the
        # pre-P8 behaviour; reused until a caller states a base that differs from a known one).
        self._cache: dict[str, tuple[str, str]] = {}

    async def ensure(self, project: str, repo: str, base_sha: str = "") -> str:
        """Return the project's cached survey summary, building it once on first use. `base_sha`
        (P8 #5) is the base commit the caller's checkout sits on: a cached survey taken at the
        SAME base (or with no base recorded, or when the caller states none) is reused; a cached
        survey from a DIFFERENT base is stale — re-survey once and share the new map. "" when
        disabled / no repo / survey failed (caller anchors on conventions only)."""
        if not self._enabled or not repo:
            return ""
        cached = self._cache.get(project)
        if cached is not None and (not base_sha or not cached[0] or cached[0] == base_sha):
            return cached[1]
        try:
            summary = await self._survey(repo)
        except Exception as exc:  # noqa: BLE001 - advisory; never block intake
            log.warning("project survey for %s (%s) failed: %s", project, repo, exc)
            summary = ""
        # Cache even an empty result so a flaky survey isn't retried on every request; an operator
        # can force a refresh with `invalidate` (e.g. after a big repo change).
        self._cache[project] = (base_sha, summary or "")
        return self._cache[project][1]

    def get(self, project: str) -> str:
        return self._cache.get(project, ("", ""))[1]

    def invalidate(self, project: str | None = None) -> None:
        if project is None:
            self._cache.clear()
        else:
            self._cache.pop(project, None)
