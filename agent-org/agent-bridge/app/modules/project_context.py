"""project-context — cached Stage-1 anchor for the readiness gate (UX-FLOW Stage 1, P3.8).

The readiness gate must ANCHOR to the existing project ("what's actually there — existing code,
branch, conventions") so it resolves placement/language/pattern itself instead of asking the
operator. Surveying the repo is a worker task (a GPU cycle), so we do it **once per project** and
cache the factual summary, reusing it across every effort on that project.

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
        self._cache: dict[str, str] = {}

    async def ensure(self, project: str, repo: str) -> str:
        """Return the project's cached survey summary, building it once on first use. "" when
        disabled / no repo / survey failed (caller anchors on conventions only)."""
        if not self._enabled or not repo:
            return ""
        if project in self._cache:
            return self._cache[project]
        try:
            summary = await self._survey(repo)
        except Exception as exc:  # noqa: BLE001 - advisory; never block intake
            log.warning("project survey for %s (%s) failed: %s", project, repo, exc)
            summary = ""
        # Cache even an empty result so a flaky survey isn't retried on every request; an operator
        # can force a refresh with `invalidate` (e.g. after a big repo change).
        self._cache[project] = summary or ""
        return self._cache[project]

    def get(self, project: str) -> str:
        return self._cache.get(project, "")

    def invalidate(self, project: str | None = None) -> None:
        if project is None:
            self._cache.clear()
        else:
            self._cache.pop(project, None)
