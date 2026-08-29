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
# Persistence seam (memory-plane Phase 0.2). Injected like `survey_fn`, so this module keeps
# its no-coupling discipline: it knows nothing about the DB, and tests drive it with dicts.
# load: () -> {project: (base_sha, summary)}   save: (project, base_sha, summary) -> None
LoadSurveysFn = Callable[[], Awaitable[dict[str, tuple[str, str]]]]
SaveSurveyFn = Callable[[str, str, str], Awaitable[None]]
# delete: (project | None) -> None   — None means "every project" (mirrors `invalidate`).
DeleteSurveyFn = Callable[[str | None], Awaitable[None]]


class ProjectContext:
    def __init__(
        self,
        survey_fn: SurveyFn,
        *,
        enabled: bool = True,
        load_fn: LoadSurveysFn | None = None,
        save_fn: SaveSurveyFn | None = None,
        delete_fn: DeleteSurveyFn | None = None,
    ) -> None:
        self._survey = survey_fn
        self._enabled = enabled
        self._load = load_fn
        self._save = save_fn
        self._delete = delete_fn
        # {project: (base_sha, summary)} — base_sha "" = surveyed without a known base (the
        # pre-P8 behaviour; reused until a caller states a base that differs from a known one).
        self._cache: dict[str, tuple[str, str]] = {}

    async def hydrate(self) -> int:
        """Load persisted surveys into the cache at boot. Returns how many were restored.

        Without this the in-memory cache started empty on every bounce and the first effort
        after a restart paid for a re-survey. Best-effort like everything else here: a
        failed load leaves an empty cache, which is exactly the pre-persistence behaviour.
        """
        if self._load is None:
            return 0
        try:
            rows = await self._load()
        except Exception as exc:  # noqa: BLE001 - advisory cache; never block boot
            log.warning("project survey hydrate failed: %s", exc)
            return 0
        self._cache.update(rows)
        return len(rows)

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
        # can force a refresh with `invalidate` (e.g. after a big repo change). This EMPTY-RESULT
        # CACHING is load-bearing, not an accident — dropping it brings back the retry storm.
        self._cache[project] = (base_sha, summary or "")
        # Persist the same tuple (empty summaries included, for the same reason). Best-effort:
        # a write failure costs a re-survey after the next restart, never the caller's request.
        if self._save is not None:
            try:
                await self._save(project, base_sha, summary or "")
            except Exception as exc:  # noqa: BLE001 - advisory; never block intake
                log.warning("persisting project survey for %s failed: %s", project, exc)
        return self._cache[project][1]

    def get(self, project: str) -> str:
        return self._cache.get(project, ("", ""))[1]

    async def invalidate(self, project: str | None = None) -> None:
        """Force a re-survey (operator affordance, e.g. after a big repo change).

        ASYNC since Phase 0.2 (it had no callers, so no signature was broken): once the
        cache is durable, clearing only the in-memory copy would be a trap — the row would
        come straight back at the next restart and "invalidate" would silently not have.
        """
        if project is None:
            self._cache.clear()
        else:
            self._cache.pop(project, None)
        if self._delete is not None:
            try:
                await self._delete(project)
            except Exception as exc:  # noqa: BLE001 - advisory; never raise at the operator
                log.warning("clearing persisted survey for %s failed: %s", project, exc)
