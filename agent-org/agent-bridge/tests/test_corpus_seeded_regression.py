"""SEEDED-REGRESSION DRILL, agent-org side — dark-factory-unification U3's Validated-by.

    "a seeded regression must be caught by a check born from a *tester* finding in a
     prior round (gym-007's shape, new source)"

gym-007 proved the shape with an OPERATOR finding (ORCHESTRATION-DESIGN §10 / PR#15: a
missing `reopen`, captured once, forced a later round to ship it). U3's extension is the
SOURCE: a TESTER finding. This drill runs that source through agent-org's real corpus gate.

THE FINDING — real, from a prior harness round, quoted from its evidence file:
  item      watchdog-fix, attempt 1        tester  wt-tester-3, 2026-08-28, verdict PASS
  evidence  .git/agent-worktrees/queue/watchdog-fix.attempt1.evidence.md, section C item 4
  said      "the projects map's file and env_file fields [are] NOT covered by that verifier
             ... A plane compose-file RENAME would silently stale the file field"
  banked as scripts/checks/check_stack_services_paths.py (ai-stack), the same command the
            harness side banks through scripts/agent-harness/durable_checks.py.

WHAT IS REAL HERE AND WHAT IS SIMULATED — said plainly, because a drill that overstates its
own fidelity is the failure §C.7 exists to prevent:
  REAL       the acceptance-corpus storage, the delivery gate, the route-back-once rule, the
             merge-gate withdrawal and the audit record — live orchestrator code, untouched.
  REAL       the delivery. The regression is a COMMIT on a real `agent/<effort>` branch in a
             real git remote, and the gate's own command
             (`git fetch origin <branch> && git checkout -f FETCH_HEAD && <check>`) fetches
             and runs it. `LocalCheckHarness.run_check` executes; nothing is hand-fed.
             The existing corpus tests queue constants into `check_queue`; this one cannot,
             which is what makes it a seeded-regression drill rather than a unit test.
  SIMULATED  the worker turn and the GitHub API (FakeChatAdapter / MockTransport), as in every
             agent-bridge test. No model ran; no PR was opened.
  NOT RUN    an ai-orchestration-gym scenario. The gym drives a whole org iteration against a
             disposable remote and scores it from outside; this drill drives the GATE that a
             gym round would exercise, deterministically and in seconds.

THE FIRST RUN FAILED, AND THAT IS RECORDED ON PURPOSE. The sandbox was a plain directory, so
the gate's `git fetch` exited 128 — a red for the wrong reason that still withdrew the merge
gate, i.e. an assertion that would have passed while proving nothing. Asserting the check's
OWN exit code (1, not merely non-zero) is what caught it, and is why the assertions below
name the number.

    python -m pytest tests/test_corpus_seeded_regression.py -q
    python tests/test_corpus_seeded_regression.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.chat import FakeChatAdapter  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.modules.model_router import FakeModelClient  # noqa: E402
from app.orchestrator import Orchestrator  # noqa: E402
from app.worker.harness import FakeHarness  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# ai-stack repo root: agent-org/agent-bridge/tests -> agent-bridge -> agent-org -> repo
AI_STACK = ROOT.parents[1]

CHECK_CMD = "python scripts/checks/check_stack_services_paths.py"
ORIGIN = ("wt-tester-3, watchdog-fix attempt 1 (2026-08-28) evidence section C item 4: the "
          "projects map's file/env_file fields are covered by NO verifier; a plane compose-file "
          "rename stales them silently and only the watchdog finds out, mid-incident")
SEED_TARGET = "search/docker-compose.yml"
SEED_RENAMED = "search/compose.yml"

GIT = ["git", "-c", "user.email=drill@invalid.local", "-c", "user.name=u3 seeded-regression drill",
       "-c", "commit.gpgsign=false"]


class LocalCheckHarness(FakeHarness):
    """A FakeHarness whose /check is NOT faked: it runs the command and reports the truth.

    The daemon contract is (exit_code, output, timed_out) from a real exec against the worker's
    workspace. Reproducing that with subprocess in a real clone is the difference between
    proving the gate reacts to a red and proving it reacts to a number a test typed in."""

    def __init__(self, workdir: Path) -> None:
        super().__init__()
        self.workdir = workdir
        self.real_checks: list[tuple[str, int, str]] = []

    async def run_check(self, base_url, command, *, cwd=None, timeout=600):
        self.checks.append({"base_url": base_url, "command": command, "cwd": cwd,
                            "timeout": timeout})
        p = subprocess.run(command, shell=True, cwd=str(self.workdir),
                           capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        self.real_checks.append((command, p.returncode, out))
        return p.returncode, out, False


def _git(repo: Path, *args: str) -> None:
    p = subprocess.run(GIT + list(args), cwd=str(repo), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {p.stdout}{p.stderr}")


def _copy_tree(dest: Path) -> None:
    """The tree the check reads. Its file list is DERIVED from the inventory, so the sandbox
    cannot quietly stop standing in for the thing it represents."""
    inv = json.loads((AI_STACK / "scripts/lib/stack-services.json").read_text(encoding="utf-8"))
    wanted = ["scripts/lib/stack-services.json",
              "scripts/checks/check_stack_services_paths.py", ".gitmodules", ".env.example"]
    for row in inv.get("projects", {}).values():
        for key in ("file", "env_file"):
            v = (row or {}).get(key)
            if v:
                wanted.append(str(v).replace("\\", "/"))
    for rel in dict.fromkeys(wanted):
        src = AI_STACK / rel
        if src.is_file():
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def build_remote(dest: Path) -> Path:
    """A real git remote holding the clean tree on `main`."""
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q", "-b", "main")
    _copy_tree(dest)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "baseline: the inventory matches the tree")
    return dest


def add_delivery_branch(remote: Path, branch: str, *, seed: bool) -> None:
    """Publish a delivery branch, WITH or WITHOUT the regression the tester predicted.

    Seeded, it is exactly the commit that finding describes: the plane's compose file is
    renamed and scripts/lib/stack-services.json is left pointing at the old path."""
    _git(remote, "checkout", "-q", "-b", branch)
    if seed:
        _git(remote, "mv", SEED_TARGET, SEED_RENAMED)
        _git(remote, "commit", "-q", "-m", "rename the search plane's compose file")
    else:
        (remote / "search" / "NOTES.md").write_text("a harmless delivery\n", encoding="utf-8")
        _git(remote, "add", "-A")
        _git(remote, "commit", "-q", "-m", "add a note to the search plane")
    _git(remote, "checkout", "-q", "main")


def clone_workspace(remote: Path, dest: Path) -> Path:
    """The worker workspace the gate's check command runs in (origin -> the remote)."""
    p = subprocess.run(GIT + ["clone", "-q", str(remote), str(dest)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"clone failed: {p.stdout}{p.stderr}")
    return dest


def _remote_api(state: dict):
    """Mocked GitHub — same shape as tests/test_acceptance_corpus.py."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "/branches/" in p:
            handler.reads = getattr(handler, "reads", 0) + 1
            sha = "prehead000000" if handler.reads == 1 else "cafe1234beef"
            return httpx.Response(200, json={"commit": {"sha": sha}})
        if "/compare/" in p:
            return httpx.Response(200, json={"ahead_by": 1, "commits": [],
                "files": [{"filename": "search/docker-compose.yml",
                           "additions": 1, "deletions": 0}]})
        if p.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 7,
                "html_url": "https://github.com/devonpveller/ai-orchestration-gym/pull/7"})
        if "/merge" in p and request.method == "PUT":
            state["merged"] = True
            return httpx.Response(200, json={"merged": True})
        if p.count("/") == 3:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404)
    return handler


async def _orch(db_url: str, tmp_path: Path, harness):
    key = tmp_path / "app.pem"
    key.write_text("dummy")
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
        github_app_id="1", github_app_owner="devonpveller",
        github_app_private_key_path=str(key),
    )
    db = Database(db_url)
    orch = Orchestrator(settings, db, FakeChatAdapter(),
                        model_client=FakeModelClient(), harness=harness)
    await orch.setup()
    return orch, orch.chat, db


async def deliver(orch, harness, remote: Path, goal: str, *, seed: bool) -> str:
    """One delivery through the real gate: capture the tester's finding as a durable check,
    publish the branch, then let the orchestrator verify and decide."""
    await orch.projects.add("gym", "https://github.com/devonpveller/ai-orchestration-gym")
    eid, chan, root = await orch.router.open_effort(goal, project="gym")
    await orch.projects.add_acceptance_check("gym", ORIGIN, CHECK_CMD, created_by="tester")
    add_delivery_branch(remote, f"agent/{eid}", seed=seed)
    orch._gh_transport = httpx.MockTransport(_remote_api({}))
    harness.output_queue = ["did the work", "pushed", "tried again", "pushed"]
    await orch.delegate(eid, chan, root, goal, plan_steps=["work"])
    return eid


async def _stage(tmp_path: Path, name: str, goal: str, *, seed: bool):
    remote = build_remote(tmp_path / f"{name}-remote")
    work = clone_workspace(remote, tmp_path / f"{name}-work")
    harness = LocalCheckHarness(work)
    orch, chat, db = await _orch(f"sqlite+aiosqlite:///{tmp_path / (name + '.db')}",
                                 tmp_path, harness)
    eid = await deliver(orch, harness, remote, goal, seed=seed)
    return orch, chat, db, harness, eid


# ── pytest ───────────────────────────────────────────────────────────────────────────────

async def test_seeded_regression_withdraws_the_merge(tmp_path):
    """THE POINT: a delivery carrying the regression is stopped by a check a TESTER's finding
    created — and stopped by the check's OWN red (exit 1), not by an incidental failure."""
    orch, chat, db, harness, eid = await _stage(tmp_path, "red", "rename the compose file",
                                                seed=True)
    try:
        assert harness.real_checks, "the gate never ran the check"
        assert all(rc == 1 for _c, rc, _o in harness.real_checks), harness.real_checks
        assert all("projects.search.file" in o for _c, _rc, o in harness.real_checks)
        msgs = " ".join(p["message"] for p in chat.posted)
        assert "Acceptance corpus" in msgs and "withdrawn" in msgs.lower()
        assert f"merge-{eid}" not in orch._pending_merge
        assert [e for e in await orch.audit.replay(eid) if e["kind"] == "acceptance_corpus_failed"]
    finally:
        await db.dispose()


async def test_clean_delivery_keeps_the_merge_gate(tmp_path):
    """The same check, the same pipeline, a delivery without the regression: green, gate
    presented. Without this half the drill proves only that something was broken."""
    orch, chat, db, harness, eid = await _stage(tmp_path, "green", "add a note", seed=False)
    try:
        assert harness.real_checks and all(rc == 0 for _c, rc, _o in harness.real_checks)
        assert "Acceptance corpus passed" in " ".join(p["message"] for p in chat.posted)
        assert f"merge-{eid}" in orch._pending_merge
    finally:
        await db.dispose()


async def test_the_tester_finding_reaches_the_worker_upstream(tmp_path):
    """gym-007's actual lesson: the corpus must be visible at plan/build time, not only at the
    gate, or the org builds wrong and burns a fix round. The TESTER's origin note travels with
    the command, so the human judgment arrives with the constraint."""
    orch, _chat, db, harness, _eid = await _stage(tmp_path, "up", "add a note", seed=False)
    try:
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        assert "ACCEPTANCE CORPUS" in prompts
        assert CHECK_CMD in prompts
        assert "wt-tester-3" in prompts
    finally:
        await db.dispose()


# ── standalone ───────────────────────────────────────────────────────────────────────────

async def _run_drill() -> int:
    results = []

    def check(label, ok):
        results.append((label, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    # mkdtemp + rmtree(ignore_errors) rather than TemporaryDirectory: on Windows a sqlite file
    # the engine has not fully released makes cleanup raise PermissionError AFTER every check
    # has passed — a red that says nothing about the org (test_org_drill.py records the same).
    tmp = tempfile.mkdtemp(prefix="u3gym-corpus-")
    try:
        tmp_path = Path(tmp)
        print("\n=== agent-org SEEDED-REGRESSION DRILL - a tester finding at the merge gate ===")

        probe_remote = build_remote(tmp_path / "probe")
        probe = subprocess.run(CHECK_CMD, shell=True, cwd=str(probe_remote),
                               capture_output=True, text=True)
        check("the banked check is GREEN on the untouched baseline tree", probe.returncode == 0)

        orch, chat, db, harness, eid = await _stage(tmp_path, "green", "add a note", seed=False)
        check("the gate RAN the banked check itself (real exec, no queued constant)",
              bool(harness.real_checks))
        check("the gate ran it against the DELIVERED BRANCH, not the worker's current tree",
              all(f"git fetch origin agent/{eid}" in c for c, _rc, _o in harness.real_checks))
        check("a clean delivery is GREEN and the merge gate is presented",
              all(rc == 0 for _c, rc, _o in harness.real_checks)
              and "Acceptance corpus passed" in " ".join(p["message"] for p in chat.posted)
              and f"merge-{eid}" in orch._pending_merge)
        prompts = " ".join(w["prompt"] for w in harness.wakes)
        check("the TESTER's finding reaches the worker upstream, command and origin together",
              "ACCEPTANCE CORPUS" in prompts and CHECK_CMD in prompts and "wt-tester-3" in prompts)
        await db.dispose()

        orch2, chat2, db2, harness2, eid2 = await _stage(
            tmp_path, "red", "rename the search plane's compose file", seed=True)
        check("the seeded delivery makes the check go RED by its OWN exit code (1, not 128)",
              bool(harness2.real_checks) and all(rc == 1 for _c, rc, _o in harness2.real_checks))
        check("RED for the RIGHT reason - the output names the stale field",
              all("projects.search.file" in o for _c, _rc, o in harness2.real_checks))
        msgs = " ".join(p["message"] for p in chat2.posted)
        check("routed back once, still red -> the merge gate is WITHDRAWN",
              "Acceptance corpus" in msgs and "withdrawn" in msgs.lower()
              and f"merge-{eid2}" not in orch2._pending_merge)
        ev = [e for e in await orch2.audit.replay(eid2)
              if e["kind"] == "acceptance_corpus_failed"]
        check("the refusal is in the audit trail, not only in chat", bool(ev))
        await db2.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} corpus seeded-regression checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_drill()))
