"""ao-orient through the REAL orchestrator path: `_intake_or_dispatch` (readiness gate) ->
delegate -> the first coding wake, on a 2-slot worker pool, with each project's repo a REAL git
repository served in the REST shapes of the C7 GitHub stand-in (no trees endpoint, so the bridge
walks the contents API exactly as it would in the sandbox).

What this pins (anchor ao-orient acceptance 1):
* a fresh project's first coding wake carries the orientation map within seconds, and the only
  worker slots ever acquired are the real efforts' own (no survey effort, no orientation slot);
* orientation is built while BOTH slots are busy with real work, and real work is never deferred
  or parked by it;
* a failed build gives that effort "orientation unavailable", is not cached, and the next effort
  on the same project gets the map.

This file deliberately imports nothing from the new module, so it runs unchanged against the
base (work/ao-fix) to show RED there: the model survey takes a `__survey__` slot and its output
is not the repo's facts."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import Event, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.schemas import ReadinessVerdict
from app.worker.harness import FakeHarness
from tests.gitbacked_github import GitBackedGitHub, make_repo

ROOT = Path(__file__).resolve().parents[1]
OWNER = "demoowner"


class TimedHarness(FakeHarness):
    """FakeHarness that records when each wake started."""

    def __init__(self) -> None:
        super().__init__()
        self.t_wake: list[float] = []

    async def wake(self, base_url, session_id, prompt, **kw):
        self.t_wake.append(time.monotonic())
        return await super().wake(base_url, session_id, prompt, **kw)


def _repo(tmp_path: Path, name: str) -> Path:
    return make_repo(tmp_path / name, [
        {"README.md": f"# {name}\n\n## Usage\nbody text that must not be copied\n",
         "pyproject.toml": "[project]\nname = 'x'\n[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
         f"src/{name}/core.py": "X = 1\n", f"tests/test_{name}.py": "def test_x():\n    pass\n"},
        {f"src/{name}/cli.py": "print('x')\n"},
    ])


def _github(stands: dict[str, GitBackedGitHub]) -> httpx.MockTransport:
    async def handle(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        if len(parts) > 3 and parts[1] == "repos" and parts[3] in stands:
            return await stands[parts[3]]._handle(request)
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(handle)


async def _orch(db_url, tmp_path, stands, **over):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090,http://w2:8090",
        max_concurrent_workers=2, database_url=db_url, review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner=OWNER, github_app_private_key_path=str(key),
        **over,
    )
    db = Database(db_url)
    harness = TimedHarness()
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=harness)
    await orch.setup()
    orch._gh_transport = _github(stands)
    acquired: list[str] = []
    real_acquire = orch.scheduler.acquire

    async def acquire(effort_id, role, session_id):
        acquired.append(effort_id)
        return await real_acquire(effort_id, role, session_id)
    orch.scheduler.acquire = acquire
    return orch, harness, db, acquired


async def _drain_bg(orch):
    for _ in range(20):
        if not orch._bg_tasks:
            return
        await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)


async def _intake(orch, project: str, name: str, request: str):
    if await orch.projects.get(project) is None:
        await orch.projects.add(project, f"https://github.com/{OWNER}/{project}")
    eid, chan, root = await orch.router.open_effort(name, project=project)
    orch.models._client.queue_structured(ReadinessVerdict(clear_and_safe=True, blast_radius="routine"))
    await orch._intake_or_dispatch(eid, chan, root, request, reply_prefix="ok",
                                   mgmt_channel=await orch.mgmt_channel_id())
    return eid


def _coding_prompts(harness, eid_hint: str = "") -> list[str]:
    return [w["prompt"] for w in harness.wakes
            if not w["plan_only"] and not str(w["session_id"]).startswith("survey")]


async def _computing(orch) -> int:
    async with orch.db.session_factory() as s:
        return int((await s.execute(select(func.count()).where(
            WorkerInstance.sched_state == "computing"))).scalar_one())


async def _built(orch, project: str) -> list[dict]:
    async with orch.db.session_factory() as s:
        rows = (await s.execute(select(Event).where(Event.kind == "project_orientation"))).scalars()
        return [r.payload for r in rows if (r.payload or {}).get("project") == project]


async def _kinds(orch, eid) -> list[str]:
    return [e["kind"] for e in await orch.audit.replay(eid)]


async def test_a_fresh_project_is_oriented_on_its_first_coding_wake_without_a_slot(db_url, tmp_path):
    stands = {"alpha": GitBackedGitHub(_repo(tmp_path, "alpha"), owner=OWNER, repo="alpha",
                                       trees=False)}
    orch, harness, db, acquired = await _orch(db_url, tmp_path, stands)
    try:
        t0 = time.monotonic()
        eid = await _intake(orch, "alpha", "first", "add a --verbose flag to the alpha cli")
        await _drain_bg(orch)
        prompts = _coding_prompts(harness)
        assert prompts, "no coding wake"
        first = prompts[0]
        assert "PROJECT ORIENTATION MAP" in first
        assert "python -m pytest   [pyproject.toml]" in first and "tests/ (1)" in first
        assert "# alpha | ## Usage" in first and "must not be copied" not in first
        assert harness.t_wake[0] - t0 < 10.0, "orientation must arrive within seconds"
        # the only slots ever taken are the real effort's own: no survey/orientation effort
        assert acquired and set(acquired) == {eid}, acquired
        assert "project_survey" not in await _kinds(orch, eid)
    finally:
        await db.dispose()


async def test_orientation_never_takes_a_slot_and_never_defers_real_work(db_url, tmp_path):
    """2-slot pool. (1) While a new project's orientation is in flight (its repo reads held open),
    no slot is computing and two real efforts on another project dispatch at once - nothing is
    deferred, parked or queued. (2) With BOTH slots taken by real work, the held orientation
    completes anyway: it never needed a slot."""
    stands = {n: GitBackedGitHub(_repo(tmp_path, n), owner=OWNER, repo=n, trees=False)
              for n in ("alpha", "beta")}
    orch, harness, db, acquired = await _orch(db_url, tmp_path, stands)
    try:
        for n in ("alpha", "beta"):
            await orch.projects.add(n, f"https://github.com/{OWNER}/{n}")
        beta = stands["beta"]
        # hold the orientation's own reads (pinned to the base sha); other readers of the repo
        # (the planner's repo state reads `?ref=main`) are not held
        beta.gate, beta.hold_paths = asyncio.Event(), (f"ref={beta.head()}",)
        b = asyncio.ensure_future(_intake(orch, "beta", "b-one", "add a --json flag"))
        await asyncio.wait_for(beta.held.wait(), 10)          # beta's orientation is in flight
        assert await _computing(orch) == 0, "orientation must not hold a worker slot"
        assert acquired == []
        # (1) real work on another project runs at once, on both slots
        a_ids = await asyncio.wait_for(asyncio.gather(
            _intake(orch, "alpha", "a-one", "add a --verbose flag"),
            _intake(orch, "alpha", "a-two", "add a --quiet flag")), 30)
        await _drain_bg_except(orch, b)
        assert not b.done(), "beta's intake should still be waiting on its held repo reads"
        alpha_first = [p for p in _coding_prompts(harness) if "# alpha" in p]
        assert len(alpha_first) >= 2, "both alpha efforts reached a coding wake"
        assert set(acquired) == set(a_ids), acquired
        for eid in a_ids:
            kinds = await _kinds(orch, eid)
            for bad in ("effort_parked", "no_worker_slot", "wake_queued"):
                assert bad not in kinds, (eid, bad)
        # (2) both slots busy with real work; the orientation still completes
        s1 = await orch.scheduler.acquire(a_ids[0], "worker-default", "hold-1")
        s2 = await orch.scheduler.acquire(a_ids[1], "worker-default", "hold-2")
        assert await _computing(orch) == 2
        beta.gate.set()
        for _ in range(500):
            if await _built(orch, "beta"):
                break
            await asyncio.sleep(0.02)
        assert await _built(orch, "beta"), "beta's orientation was not built with the pool full"
        assert await _computing(orch) == 2           # still only the two real holders
        await orch.scheduler.release(s1.id)
        await orch.scheduler.release(s2.id)
        await b
        await _drain_bg(orch)
        beta_first = [p for p in _coding_prompts(harness) if "# beta" in p]
        assert beta_first and "PROJECT ORIENTATION MAP" in beta_first[0]
        assert not any(i.startswith("__") for i in acquired), acquired
    finally:
        await db.dispose()


async def _drain_bg_except(orch, keep):
    """Wait for background work, except the task `keep` (still held on purpose)."""
    for _ in range(20):
        pending = [t for t in orch._bg_tasks if t is not keep]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def test_a_failed_orientation_is_not_cached_and_the_next_effort_gets_the_map(db_url, tmp_path):
    stand = GitBackedGitHub(_repo(tmp_path, "alpha"), owner=OWNER, repo="alpha", trees=False)
    orch, harness, db, acquired = await _orch(db_url, tmp_path, {"alpha": stand})
    try:
        real = stand._handle

        async def flaky(request):                    # the repo reads fail, the base is readable
            if "/contents" in request.url.path or "/git/trees" in request.url.path:
                return httpx.Response(502, json={"message": "bad gateway"})
            return await real(request)
        stand._handle = flaky
        e1 = await _intake(orch, "alpha", "one", "add a --verbose flag")
        await _drain_bg(orch)
        first = [p for p in _coding_prompts(harness) if "--verbose" in p][0]
        assert "PROJECT ORIENTATION: unavailable for this effort" in first
        assert "PROJECT ORIENTATION MAP" not in first
        stand._handle = real                         # the repo is readable again
        e2 = await _intake(orch, "alpha", "two", "add a --quiet flag")
        await _drain_bg(orch)
        second = [p for p in _coding_prompts(harness) if "--quiet" in p][0]
        assert "PROJECT ORIENTATION MAP" in second and "python -m pytest" in second
        assert set(acquired) <= {e1, e2}, acquired
    finally:
        await db.dispose()


# ── round 2 ──────────────────────────────────────────────────────────────────────────────────

def _dotnet(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "dotnetapp", [{
        "App.sln": "x", "src/App/App.csproj": "x", "src/App/Program.cs": "class P {}",
        "tests/App.Tests/App.Tests.csproj": "x", "tests/App.Tests/UserServiceTests.cs": "class T {}",
        "tests/App.Tests/OrderServiceTests.cs": "class O {}",
        "scripts/test_release.py": "def test_r(): pass\n", "README.md": "# App\n"}])


def _mixed(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "mixedapp", [{
        "pyproject.toml": "[tool.pytest.ini_options]\n", "tests/test_a.py": "x", "tests/test_b.py": "x",
        "tests/test_c.py": "x", "svc/Svc.sln": "x", "svc/tests/Svc.Tests/Svc.Tests.csproj": "x",
        "svc/tests/Svc.Tests/ATests.cs": "x", "README.md": "# Mixed\n"}])


async def test_dotnet_and_mixed_repos_through_the_real_path(db_url, tmp_path):
    """The live workers run dotnet: a dotnet repo's first coding wake names `dotnet test` with its
    solution and never a root `python -m pytest` (a stray test_*.py gets its own folder only); a
    mixed repo lists each system at its own root, the dominant one first."""
    stands = {"dotnetapp": GitBackedGitHub(_dotnet(tmp_path), owner=OWNER, repo="dotnetapp", trees=False),
              "mixedapp": GitBackedGitHub(_mixed(tmp_path), owner=OWNER, repo="mixedapp", trees=False)}
    orch, harness, db, acquired = await _orch(db_url, tmp_path, stands)
    try:
        await _intake(orch, "dotnetapp", "dn", "add a --dry-run flag")
        await _intake(orch, "mixedapp", "mx", "add a --json flag")
        await _drain_bg(orch)
        dn = [p for p in _coding_prompts(harness) if "--dry-run" in p][0]
        mx = [p for p in _coding_prompts(harness) if "--json flag" in p][0]
        dn_cmds = dn.split("Likely test commands (from manifests):\n", 1)[1].split("\n")
        assert dn_cmds[0].strip() == "dotnet test App.sln   [App.sln]"
        assert not any(ln.strip().startswith("python -m pytest") for ln in dn.splitlines())
        assert "cd scripts && python -m pytest" in dn
        mx_cmds = [ln.strip() for ln in mx.split("Likely test commands (from manifests):\n", 1)[1]
                   .split("\n") if ln.startswith("  ")]
        assert mx_cmds[:2] == ["python -m pytest   [pyproject.toml]",
                               "cd svc && dotnet test Svc.sln   [svc/Svc.sln]"], mx_cmds
    finally:
        await db.dispose()


async def test_a_slow_build_never_holds_intake_and_the_first_wake_still_gets_the_map(db_url, tmp_path):
    """Intake runs on the serial event loop: it waits at most project_orientation_intake_wait_s for
    the map, then the build goes on in the background and the first coding wake (a background
    task) gets it - no slot taken while it waits."""
    stand = GitBackedGitHub(_repo(tmp_path, "alpha"), owner=OWNER, repo="alpha", trees=False)
    orch, harness, db, acquired = await _orch(db_url, tmp_path, {"alpha": stand},
                                              project_orientation_intake_wait_s=1.0)
    try:
        await orch.projects.add("alpha", f"https://github.com/{OWNER}/alpha")
        stand.gate, stand.hold_paths = asyncio.Event(), (f"ref={stand.head()}",)
        t0 = time.monotonic()
        await _intake(orch, "alpha", "slow", "add a --verbose flag")
        assert time.monotonic() - t0 < 5.0, "intake must not wait for the whole build"
        assert not _coding_prompts(harness) or "PROJECT ORIENTATION" not in _coding_prompts(harness)[0]
        assert await _computing(orch) == 0
        stand.gate.set()
        await _drain_bg(orch)
        first = [p for p in _coding_prompts(harness) if "--verbose" in p][0]
        assert "PROJECT ORIENTATION MAP" in first and "python -m pytest" in first
    finally:
        await db.dispose()
