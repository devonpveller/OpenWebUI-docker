"""ao-plancheck - the plan-turn write check attributes writes to the turn that made them.

The C7e 0.3 ao-queue r1 run (bake-off 2026-10-15) lost its only deliverable to this check: the
flail guard stopped the first coding turn on worker-1 after it had written its repro test
(`inference/llm-queue/tests/test_leak_repro.py`, untracked); the flail fork forced a plan turn,
which ran in session `~r1~plan` on worker-2 in a FRESH clone; then the write check probed
`git status` from a `~planclean` session that landed on worker-1, found the coding turn's file,
logged `plan_turn_wrote_files`, and `git checkout -- . && git clean -fd` deleted it. Two defects
in one: the check had no baseline (anything dirty after the plan turn was blamed on it) and it
did not even look at the plan turn's workspace.

These tests replay that sequence end to end through `delegate` with REAL git workspaces - one
per fake worker - so the check's shell and Python actually run against a repository:
  * the C7e shape: coding turn on worker-1, plan turn on worker-2;
  * the shared-workspace shape: the plan turn reuses the coding turn's workspace and even edits
    the coding turn's file - that file must come back with the CODING turn's content;
  * a plan turn that writes and COMMITS in a clean workspace is still caught and fully undone.
Fakes only: no daemon, no container, no GPU. Git runs locally on temporary repositories."""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import SCHED_COMPUTING, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/c7-sandbox/ai-stack"
W1, W2 = "http://w1:8090", "http://w2:8090"

CODING_FILE = "inference/llm-queue/tests/test_leak_repro.py"
CODING_TEXT = "def test_leak_repro():\n    assert False, 'repro: connection leak'\n"
TRACKED = "README.md"
TRACKED_TEXT = "# sandbox\n"
PLAN_SCRATCH = "PLAN_NOTES.md"

_FLAIL_OUT = ("FLAIL-GUARD: stopped the turn — rule=stale: 10 calls in a row brought back "
              "nothing new (window 10, novel 40/51, edits 1, since edit 18). The approach wasn't "
              "converging; a fresh plan is needed.")
_PLAN = ("UNDERSTANDING: fix the llm-queue connection leak.\n"
         "ALREADY DONE: nothing\n"
         "PLAN:\n1. add a repro test in inference/llm-queue/tests\n2. close the upstream client\n"
         "WON'T DO: no unrelated refactors\nRISKS: none known")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


class Workspaces:
    """One real git checkout per fake worker url, cloned from a local 'origin'."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.origin = tmp / "origin"
        self.origin.mkdir()
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "config", "user.email", "t@example.invalid")
        _git(self.origin, "config", "user.name", "t")
        (self.origin / TRACKED).write_text(TRACKED_TEXT)
        src = self.origin / "inference" / "llm-queue" / "src"
        src.mkdir(parents=True)
        (src / "queue.py").write_text("def run():\n    return 1\n")
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", "base")
        self.base = _git(self.origin, "rev-parse", "HEAD").strip()
        self.dirs: dict[str, Path] = {}

    def focus(self, url: str, fresh: bool) -> None:
        d = self.tmp / ("ws-" + re.sub(r"\W", "_", url))
        if fresh or not d.exists():
            if d.exists():
                shutil.rmtree(d, onerror=lambda f, p, _e: (Path(p).chmod(0o700), f(p)))
            _git(self.tmp, "clone", "-q", str(self.origin), str(d))
            _git(d, "config", "user.email", "w@example.invalid")
            _git(d, "config", "user.name", "w")
        self.dirs[url] = d

    def files(self, url: str) -> dict[str, str]:
        d = self.dirs[url]
        out: dict[str, str] = {}
        for p in d.rglob("*"):
            if ".git" in p.relative_to(d).parts or not p.is_file():
                continue
            out[p.relative_to(d).as_posix()] = p.read_text()
        return out


_SHORT_ROOTS: list[Path] = []


@pytest.fixture(autouse=True)
def _drop_short_roots():
    yield
    def _retry(func, path, _exc):
        try:
            Path(path).chmod(0o700)
            func(path)
        except OSError:
            pass    # e.g. a hung proxy call a timed-out check left behind still holds its cwd

    while _SHORT_ROOTS:
        shutil.rmtree(_SHORT_ROOTS.pop(), onerror=_retry)


def _workspaces(tmp: Path) -> Workspaces:
    """`Workspaces` under a SHORT root instead of pytest's tmp dir (`tmp` is unused, kept so the
    call sites read the same). On a Windows host a long --basetemp pushes the workspaces' paths
    past MAX_PATH: `git clone` exits 128 (19 failures in a full-suite run under a 211-char
    workspace path) and a push into the local `origin` dies in unpack-objects; `core.longpaths`
    does not help receive-pack. The workers are Linux containers - this is test plumbing only.
    Removed after each test (`_drop_short_roots`; test modules that import `_workspaces` import
    that fixture too)."""
    root = Path(tempfile.mkdtemp(prefix="apc-"))
    _SHORT_ROOTS.append(root)
    return Workspaces(root)


def _run_shell_probe(ws: Path, command: str) -> tuple[int, str, bool]:
    """Emulates the pre-fix check's two shell commands (only so the RED run is faithful)."""
    if "PROBE-DONE" in command:
        head = _git(ws, "rev-parse", "HEAD").strip()
        status = _git(ws, "status", "--porcelain")
        return 0, f"BEFORE-HEAD={head}\n{status}PROBE-DONE\n", False
    if "REVERT-DONE" in command:
        _git(ws, "checkout", "--", ".")
        _git(ws, "clean", "-fd")
        return 0, "REVERT-DONE\n", False
    raise RuntimeError(f"fake /check: unexpected command {command[:80]!r}")


LC = ROOT.parents[1] / "little-coder"
PROXY_DIR = LC / "git-proxy"
_SHIM = r'''
import os, subprocess, sys, time
sys.path.insert(0, {proxy!r})
import git_proxy
argv = sys.argv[1:]
sub, _r, _e = git_proxy._find_subcommand(argv)
with open({calls!r}, "a") as fh:
    fh.write(str(sub) + "\n")
time.sleep(float(os.environ.get("AO_SHIM_LATENCY") or 0))   # the worker proxy's ~0.27 s per call
if sub and sub == os.environ.get("AO_SHIM_HANG"):            # a call that never returns in time
    time.sleep(float(os.environ.get("AO_SHIM_HANG_SECS") or 30))
if sub and sub in (os.environ.get("AO_SHIM_FAIL") or "").split(","):   # a call that fails
    sys.stderr.write("shim: forced failure of " + sub + "\n")
    sys.exit(1)
needs = sub in {{"fetch", "push", "reset"}}
remotes = git_proxy._configured_remotes("git") if needs else set()
is_tag = git_proxy._make_is_tag("git") if needs else (lambda _ref: False)
d = git_proxy.classify(argv, remotes, is_tag, None)
if d.action == "deny":
    with open({log!r}, "a") as fh:
        fh.write(repr(argv) + " " + d.rule + "\n")
    sys.stderr.write("git-proxy: DENIED (" + d.rule + ")\n")
    sys.exit(git_proxy.EXIT_DENIED)
sys.exit(subprocess.run(["git", *argv]).returncode)
'''


def _redact():
    """little-coder's own `redact_secrets` (the daemon runs it over every /check reply)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lc_sanitize",
                                                  LC / "src" / "littlecoder" / "sanitize.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod     # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod.redact_secrets


def _channel(stdout: str, stderr: str) -> str:
    """What the bridge receives from a real worker's /check: open-terminal hands output back as
    one entry per write from a PTY (CRLF), an older daemon joined entries with an extra newline
    (ao-planrevert attempt 2 died on exactly that), then `redact_secrets`, then the last 64 KB."""
    pty = stdout.replace("\r\n", "\n").replace("\n", "\r\n")
    chunks = [pty[i:i + 37] for i in range(0, len(pty), 37)]    # deliberately awkward chunking
    out = "\n".join(chunks)
    if stderr:
        out += "\n" + stderr
    return _redact()(out[-65536:])


def _proxy_log(ws: Path) -> Path:
    return ws.parent / (ws.name + ".git-proxy-denied.log")


def _calls_log(ws: Path) -> Path:
    return ws.parent / (ws.name + ".git-calls.log")


def _git_calls(ws: Path) -> list[str]:
    f = _calls_log(ws)
    return f.read_text().split() if f.exists() else []


def _run_python_check(ws: Path, command: str, timeout: int = 120,
                      env: dict | None = None) -> tuple[int | None, str, bool]:
    """Runs the bridge's base64-encoded Python check for real, against this worker's checkout,
    with EVERY git call routed through the worker's real git-proxy `classify()`. `timeout`
    kills it like open-terminal does (the reply is then whatever it printed: nothing)."""
    import os
    m = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", command)
    assert m, command[:120]
    assert len(command) < 120_000, "a /check command must stay under Linux's 128 KB arg limit"
    src = base64.b64decode(m.group(1)).decode()
    shim = ws.parent / (ws.name + ".git-shim.py")
    shim.write_text(_SHIM.format(proxy=str(PROXY_DIR), log=str(_proxy_log(ws)),
                                 calls=str(_calls_log(ws))))
    assert "WS = '/workspace'" in src and 'GIT = ["git"]' in src
    src = src.replace("WS = '/workspace'", f"WS = {str(ws)!r}")
    src = src.replace('GIT = ["git"]', f"GIT = [{sys.executable!r}, {str(shim)!r}]")
    script = ws.parent / (ws.name + ".check.py")      # a file: Windows caps a command line at 32K
    script.write_text(src, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, str(script)], cwd=ws, capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired:
        return None, "", True
    return p.returncode, _channel(p.stdout, p.stderr), False


def _denials(ws: Path) -> str:
    f = _proxy_log(ws)
    return f.read_text() if f.exists() else ""


async def _orch(db_url, tmp: Path, *, coding_edits: bool = True, plan_edits_coding: bool = False,
                plan_commits: bool = False, coding_bulk: int = 0, stale_task: bool = False):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=f"{W1},{W2}",
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="off",
        worker_flail_guard=True,
    )
    db = Database(db_url)
    harness = FakeHarness()
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=harness)
    await orch.setup()
    ws = _workspaces(tmp)
    trace: dict = {"plan_url": None, "coding_url": None, "after_plan": None, "checks": [],
                   "stale": None, "order": []}

    orig_set_project = harness.set_project

    async def set_project(base_url, repo, **kw):
        if trace["plan_url"] is not None and trace["after_plan"] is None:
            # the NEXT focus after the plan turn: the gate has finished - freeze what it left
            trace["after_plan"] = {u: ws.files(u) for u in ws.dirs}
        ws.focus(base_url, bool(kw.get("fresh")))
        return await orig_set_project(base_url, repo, **kw)

    orig_wake = harness.wake

    async def wake(base_url, session_id, prompt, **kw):
        d = ws.dirs[base_url]
        if kw.get("plan_only"):
            trace["plan_url"] = base_url
            # the read-only plan turn writes anyway (gym-015: `sed -i`, `python3 -c`)
            (d / PLAN_SCRATCH).write_text("scratch from the plan turn\n")
            (d / TRACKED).write_text(TRACKED_TEXT + "edited by the plan turn\n")
            if plan_edits_coding and (d / CODING_FILE).exists():
                (d / CODING_FILE).write_text("# clobbered by the plan turn\n")
            if plan_commits:
                _git(d, "add", "-A")
                _git(d, "commit", "-q", "-m", "plan turn commit")
            harness.output_queue.insert(0, _PLAN)
        elif trace["coding_url"] is None:
            trace["coding_url"] = base_url
            if coding_edits:
                f = d / CODING_FILE
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(CODING_TEXT)
            for i in range(coding_bulk):
                g = d / "gen" / f"coding-{i:05d}-{'x' * 40}.txt"
                g.parent.mkdir(exist_ok=True)
                g.write_text(str(i))
            if stale_task:
                trace["stale"] = base_url      # the flail-stopped task has not wound down yet
            harness.output_queue.insert(0, _FLAIL_OUT)
        return await orig_wake(base_url, session_id, prompt, **kw)

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command})
        trace["checks"].append((base_url, command))
        trace["order"].append("check")
        d = ws.dirs.get(base_url)
        if d is None:
            raise RuntimeError("fake /check: worker not focused")
        if "b64decode" in command:
            return _run_python_check(d, command)
        return _run_shell_probe(d, command)

    async def has_running_task(base_url):
        return trace["stale"] == base_url

    async def running_task_progress(base_url, *a, **kw):
        return ("stale-coding-task", 1) if trace["stale"] == base_url else None

    async def cancel_task(base_url, task_id):
        # the stopped task's LAST write lands as it is being cancelled
        trace["order"].append("cancel")
        if trace["stale"] == base_url:
            (ws.dirs[base_url] / "late.py").write_text("late write of the stopped coding task\n")
            trace["stale"] = None
        return True

    harness.set_project = set_project
    harness.wake = wake
    harness.run_check = run_check
    harness.has_running_task = has_running_task
    harness.running_task_progress = running_task_progress
    harness.cancel_task = cancel_task
    return orch, harness, db, ws, trace


async def _route_plan_turn_to_worker_2(orch) -> None:
    """C7e: the plan session `~r1~plan` landed on worker-2 while worker-1 (the coding turn's
    workspace) stayed suspended. Make worker-1 un-pickable for the plan acquire only."""
    orig = orch.scheduler.acquire

    async def acquire(effort_id, role, session_id, *a, **kw):
        if not session_id.endswith("~plan"):
            return await orig(effort_id, role, session_id, *a, **kw)
        async with orch.db.session_factory() as s:
            w1 = (await s.execute(select(WorkerInstance).where(
                WorkerInstance.base_url == W1))).scalar_one()
            prev, w1.sched_state = w1.sched_state, SCHED_COMPUTING
            await s.commit()
        try:
            return await orig(effort_id, role, session_id, *a, **kw)
        finally:
            async with orch.db.session_factory() as s:
                w1 = (await s.execute(select(WorkerInstance).where(
                    WorkerInstance.base_url == W1))).scalar_one()
                w1.sched_state = prev
                await s.commit()

    orch.scheduler.acquire = acquire


async def _drain(orch, rounds: int = 3):
    for _ in range(rounds):
        if orch._bg_tasks:
            await asyncio.gather(*list(orch._bg_tasks))


async def _shutdown(orch, db):
    await _drain(orch)
    for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    await db.dispose()


async def _run_c7e(orch):
    await orch.projects.add("ai-stack", REPO)
    eid, chan, root = await orch.router.open_effort("ao-queue", project="ai-stack")
    await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
    await _drain(orch)
    return eid


def _no_denials(ws: Workspaces) -> None:
    denied = "".join(_denials(d) for d in ws.dirs.values())
    assert not denied, "the worker's git-proxy would refuse: " + denied


def _reverted_paths(payload: dict | None) -> set[str]:
    return {r["path"] for r in (payload or {}).get("reverted", [])}


# -- the C7e r1 sequence: coding turn on worker-1, plan turn on worker-2 ------------------
async def test_c7e_coding_turn_file_survives_a_plan_turn_on_another_worker(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        await _route_plan_turn_to_worker_2(orch)
        eid = await _run_c7e(orch)
        assert await orch._event_count(eid, "flail_replanned") == 1
        assert trace["coding_url"] == W1 and trace["plan_url"] == W2, trace
        after = trace["after_plan"]
        assert after is not None, "the effort never got past the plan gate"
        # the coding turn's repro test is untouched in ITS workspace ...
        assert after[W1].get(CODING_FILE) == CODING_TEXT
        # ... and the plan turn's writes were reverted in the plan turn's workspace
        assert PLAN_SCRATCH not in after[W2]
        assert after[W2][TRACKED] == TRACKED_TEXT
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED}, payload
        assert payload["worker"] == "worker-2"
        assert all(r["why"] for r in payload["reverted"])
        # nothing was ever run against worker-1 by the check
        assert not any(u == W1 and ("PROBE" in c or "b64decode" in c)
                       for u, c in trace["checks"]), trace["checks"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- the shared-workspace shape: the plan turn even edits the coding turn's file -----------
async def test_shared_workspace_keeps_the_coding_turns_content(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, plan_edits_coding=True)
    try:
        eid = await _run_c7e(orch)
        assert trace["coding_url"] == trace["plan_url"] == W1, trace   # one workspace
        after = trace["after_plan"]
        assert after is not None
        # the coding file is back to what the CODING turn wrote (not deleted, not the plan's edit)
        assert after[W1].get(CODING_FILE) == CODING_TEXT
        assert PLAN_SCRATCH not in after[W1]
        assert after[W1][TRACKED] == TRACKED_TEXT
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED, CODING_FILE}, payload
        why = {r["path"]: r["why"] for r in payload["reverted"]}
        assert "before the plan turn" in why[CODING_FILE]
        assert CODING_FILE not in payload["kept_before_turn"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_shared_workspace_untouched_coding_file_is_kept_and_counted(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        eid = await _run_c7e(orch)
        assert trace["coding_url"] == trace["plan_url"] == W1, trace
        after = trace["after_plan"]
        assert after[W1].get(CODING_FILE) == CODING_TEXT
        assert PLAN_SCRATCH not in after[W1]
        assert after[W1][TRACKED] == TRACKED_TEXT
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED}, payload
        assert payload["kept_before_turn"] == [CODING_FILE]
        assert payload["kept_before_turn_count"] == 1
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- a plan turn that writes AND commits in a clean workspace is still caught ---------------
async def test_a_plan_turn_commit_is_undone_and_recorded(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, coding_edits=False,
                                               plan_commits=True)
    try:
        await _route_plan_turn_to_worker_2(orch)
        eid = await _run_c7e(orch)
        after = trace["after_plan"]
        assert after is not None
        assert PLAN_SCRATCH not in after[W2]
        assert after[W2][TRACKED] == TRACKED_TEXT
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert payload is not None
        assert payload.get("head_reset"), payload
        assert {PLAN_SCRATCH, TRACKED} <= _reverted_paths(payload)
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- the scripts themselves, against a real repository ------------------------------------
def _run(ws_dir: Path, command: str) -> dict:
    from app.modules.plan_turn_guard import parse_check, parse_snapshot

    _code, out, _t = _run_python_check(ws_dir, command)
    return parse_snapshot(out) or parse_check(out) or {"raw": out}


def test_a_turn_that_deletes_the_snapshot_still_has_its_new_files_reverted(tmp_path):
    """The bridge holds the baseline manifest, so a plan turn cannot erase the record of what was
    on disk before it by deleting `<git-dir>/ao-plan-snapshot` - only the copies, and then a
    pre-existing file it changed is reported as not restorable rather than silently kept."""
    from app.modules.plan_turn_guard import check_command, new_token, snapshot_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / "earlier.py").write_text("from an earlier turn\n")
    tok = new_token()
    snap = _run(d, snapshot_command(tok))
    assert snap["manifest"] == {"earlier.py": snap["manifest"]["earlier.py"]}
    # the plan turn: writes a new file, edits the earlier file, wipes the snapshot
    (d / "sneaky.py").write_text("x\n")
    (d / "earlier.py").write_text("tampered\n")
    shutil.rmtree(Path(_git(d, "rev-parse", "--absolute-git-dir").strip()) / "ao-plan-snapshot")
    rep = _run(d, check_command(tok, snap))
    assert rep["status"] == "ok"
    assert {r["path"] for r in rep["reverted"]} == {"sneaky.py"}
    assert {u["path"] for u in rep["unrestored"]} == {"earlier.py"}
    assert not (d / "sneaky.py").exists()
    assert (d / "earlier.py").read_text() == "tampered\n"    # no copy: left, and reported
    assert not _denials(d)


def test_a_tampered_copy_is_never_restored(tmp_path):
    """The copies live where the turn's shell can reach them: one is used only if its hash
    matches the baseline the bridge holds."""
    from app.modules.plan_turn_guard import check_command, new_token, snapshot_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / "earlier.py").write_text("from an earlier turn\n")
    tok = new_token()
    snap = _run(d, snapshot_command(tok))
    copy = (Path(_git(d, "rev-parse", "--absolute-git-dir").strip()) / "ao-plan-snapshot"
            / "files" / "earlier.py")
    copy.write_text("planted by the plan turn\n")
    (d / "earlier.py").write_text("changed by the plan turn\n")
    rep_ = _run(d, check_command(tok, snap))
    assert {u["path"] for u in rep_["unrestored"]} == {"earlier.py"}
    assert (d / "earlier.py").read_text() == "changed by the plan turn\n"
    assert not _denials(d)


def test_a_plan_turn_that_wipes_the_tree_gets_the_earlier_work_back(tmp_path):
    """`git checkout -- . && git clean -fd` by the plan turn itself: the earlier turn's edit and
    new file come back from the snapshot, exactly as they were."""
    from app.modules.plan_turn_guard import check_command, new_token, snapshot_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / TRACKED).write_text(TRACKED_TEXT + "coding edit\n")
    (d / CODING_FILE).parent.mkdir(parents=True)
    (d / CODING_FILE).write_text(CODING_TEXT)
    tok = new_token()
    snap = _run(d, snapshot_command(tok))
    _git(d, "checkout", "--", ".")
    _git(d, "clean", "-fd")
    rep_ = _run(d, check_command(tok, snap))
    assert {r["path"] for r in rep_["reverted"]} == {TRACKED, CODING_FILE}
    assert (d / TRACKED).read_text() == TRACKED_TEXT + "coding edit\n"
    assert (d / CODING_FILE).read_text() == CODING_TEXT
    assert not _denials(d)


def test_token_shaped_and_unicode_paths_survive_the_channel(tmp_path):
    """The daemon's `redact_secrets` masks token-shaped text and open-terminal re-chunks output;
    neither reaches inside the framed base64 reply (ao-planrevert attempt 2 trusted mangled
    paths)."""
    from app.modules.plan_turn_guard import check_command, new_token, snapshot_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    odd = ["ghp_" + "A1b2C3d4E5" * 4 + ".txt", "dir with space/caf\u00e9 \u00fc.py"]
    for name in odd:
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("earlier\n", encoding="utf-8")
    tok = new_token()
    snap = _run(d, snapshot_command(tok))
    assert set(snap["manifest"]) == set(odd)
    (d / "new ghp_file.md").write_text("plan\n")
    rep_ = _run(d, check_command(tok, snap))
    assert {r["path"] for r in rep_["reverted"]} == {"new ghp_file.md"}
    assert sorted(rep_["kept"]) == sorted(odd)
    assert not _denials(d)


def _baseline(d: Path, tok: str, env: dict | None = None) -> dict:
    """What the router's pre-turn hook does: snapshot, paged back if large (`take_baseline`)."""
    from app.modules.plan_turn_guard import parse_snapshot, take_baseline

    async def run(command: str, timeout: int = 120):
        return _run_python_check(d, command, timeout, env)[1]

    snap = parse_snapshot(asyncio.run(take_baseline(run, tok)))
    assert snap is not None and isinstance(snap["manifest"], dict)
    return snap


def _check(d: Path, tok: str, snap: dict, timeout: int = 120, env: dict | None = None):
    """What `_revert_plan_turn_writes` does on the worker: stage (if large), then check."""
    from app.modules.plan_turn_guard import check_command, parse_check, parse_page, stage_commands

    for cmd in stage_commands(tok, snap):
        assert parse_page(_run_python_check(d, cmd, timeout, env)[1])["status"] == "ok"
    _code, out, timed = _run_python_check(d, check_command(tok, snap), timeout, env)
    return parse_check(out), timed


def _snapdir(d: Path) -> Path:
    return Path(_git(d, "rev-parse", "--absolute-git-dir").strip()) / "ao-plan-snapshot"


def test_a_huge_earlier_change_set_is_paged_to_the_bridge_and_staged_back(tmp_path):
    """Round 2 finding 2: past the inline size the baseline is PAGED back to the bridge before
    the turn and STAGED back (sha256-pinned) for the check - the on-disk state the turn could
    forge is never trusted. Here the turn forges it to adopt its own file: still reverted."""
    import secrets

    from app.modules.plan_turn_guard import MANIFEST_B64_CAP, new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    gen = d / "gen"
    gen.mkdir()
    for _ in range(1500):
        (gen / (secrets.token_hex(24) + ".txt")).write_text(secrets.token_hex(8))
    tok = new_token()
    snap = _baseline(d, tok)
    assert len(snap["blob"]) > MANIFEST_B64_CAP and len(snap["manifest"]) == 1500
    # the plan turn writes, and rewrites the on-disk baseline to claim its file was there before
    (d / PLAN_SCRATCH).write_text("plan\n")
    (_snapdir(d) / "state.b64").write_text("forged")
    (_snapdir(d) / f"bridge-{tok}.b64").write_text("forged")
    rep_, _t = _check(d, tok, snap)
    assert rep_["status"] == "ok"
    assert {r["path"] for r in rep_["reverted"]} == {PLAN_SCRATCH}
    assert rep_["kept_count"] == 1500
    assert len(list(gen.iterdir())) == 1500
    assert not _snapdir(d).exists()
    assert not _denials(d)


def test_a_staged_baseline_that_does_not_match_the_bridge_attributes_nothing(tmp_path):
    from app.modules.plan_turn_guard import check_command, new_token, parse_check

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    tok = new_token()
    snap = _baseline(d, tok)
    snap = dict(snap, blob=snap["blob"] + "A" * 50_000)      # bigger than inline -> staged
    (d / PLAN_SCRATCH).write_text("plan\n")
    # a forged staged copy: the sha pinned in the check does not match -> fail closed
    (_snapdir(d) / f"bridge-{tok}.b64").write_text("forged")
    rep_ = parse_check(_run_python_check(d, check_command(tok, snap))[1])
    assert rep_["status"] == "no-snapshot"
    assert (d / PLAN_SCRATCH).exists()                        # nothing touched without a baseline


def _big_plan_turn(d: Path, n: int) -> None:
    """A plan turn that runs a build into non-ignored paths, edits a tracked file and stages a new
    file (the shapes that used to cost git calls per path)."""
    out = d / "obj" / "Debug"
    out.mkdir(parents=True)
    for i in range(n):
        (out / f"gen.{i}.cache").write_text(str(i))
    (d / TRACKED).write_text(TRACKED_TEXT + "plan edit\n")
    (d / "staged_new.py").write_text("x\n")
    _git(d, "add", "staged_new.py")


@pytest.mark.parametrize("n", [1000, 4000])
def test_a_plan_turn_with_thousands_of_new_paths_is_reverted_well_inside_the_timeout(tmp_path, n):
    """Round 2 FAIL: on the real proxy each git call costs ~0.27 s and the check made two per
    new path, so >~600 paths outlived the 300 s exec timeout and hundreds of plan-turn files
    stayed. Now an untracked path costs no git call and the rest are batched: the shim charges the
    proxy's latency per call, and the whole check must finish in a small fraction of 300 s."""
    import time

    from app.modules.plan_turn_guard import REPORT_CAP, new_token

    lat = {"AO_SHIM_LATENCY": "0.27"}
    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / CODING_FILE).parent.mkdir(parents=True)
    (d / CODING_FILE).write_text(CODING_TEXT)                    # the earlier turn's work
    tok = new_token()
    snap = _baseline(d, tok, lat)
    _big_plan_turn(d, n)
    before = len(_git_calls(d))
    t0 = time.monotonic()
    rep_, timed = _check(d, tok, snap, 300, lat)
    secs = time.monotonic() - t0
    calls = len(_git_calls(d)) - before
    print(f"\nn={n} check {secs:.1f}s, {calls} proxied git calls")
    assert not timed and rep_["status"] == "ok"
    assert secs < 60, secs
    assert calls <= 10, calls                                 # flat: not per path
    assert rep_["reverted_count"] == n + 2                     # n files + README + staged_new.py
    assert len(rep_["reverted"]) == REPORT_CAP                  # capped, with an exact count
    assert not (d / "obj").exists() and not (d / "staged_new.py").exists()
    assert (d / TRACKED).read_text() == TRACKED_TEXT
    assert (d / CODING_FILE).read_text() == CODING_TEXT
    assert _git(d, "status", "--porcelain", "-uall").strip() == "?? " + CODING_FILE
    assert not _snapdir(d).exists()
    assert not _denials(d)


def test_a_check_cut_off_by_its_timeout_resumes_and_reports_everything(tmp_path):
    """Finding 1: a check killed mid-way (here: `checkout` never returns in time) has already
    removed files. A second run resumes from the journal and reports BOTH runs' work."""
    from app.modules.plan_turn_guard import check_command, new_token, parse_check

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    tok = new_token()
    snap = _baseline(d, tok)
    _big_plan_turn(d, 300)
    _code, out, timed = _run_python_check(d, check_command(tok, snap), 8,
                                          {"AO_SHIM_HANG": "checkout", "AO_SHIM_HANG_SECS": "20"})
    assert timed and out == ""                                  # what open-terminal hands back
    assert not (d / "obj").exists()                             # ...yet the removals happened
    assert (d / TRACKED).read_text() != TRACKED_TEXT            # and the restore did not
    rep_ = parse_check(_run_python_check(d, check_command(tok, snap))[1])
    assert rep_["status"] == "ok" and rep_["resumed"] is True
    assert rep_["reverted_count"] == 302
    assert (d / TRACKED).read_text() == TRACKED_TEXT


def test_a_check_that_never_finishes_is_reported_partial_from_its_journal(tmp_path):
    from app.modules.plan_turn_guard import check_command, new_token, parse_check, report_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    tok = new_token()
    snap = _baseline(d, tok)
    _big_plan_turn(d, 250)
    hang = {"AO_SHIM_HANG": "checkout", "AO_SHIM_HANG_SECS": "20"}
    assert _run_python_check(d, check_command(tok, snap), 8, hang)[2]
    rep_ = parse_check(_run_python_check(d, report_command(tok))[1])
    assert rep_["status"] == "partial"
    assert rep_["reverted_count"] == 251                        # 250 + staged_new.py, removed
    assert rep_["dirty_after"] == 1                             # README.md still edited
    assert not _snapdir(d).exists()


def test_refs_the_plan_turn_created_are_recorded(tmp_path):
    """Finding 3: the proxy forbids deleting refs (`branch -D`, `tag -d`, `update-ref`), so a
    branch or tag the plan turn made is RECORDED (it cannot reach origin: only the effort branch
    is pushed). The branch switch and the commit are still undone."""
    from app.modules.plan_turn_guard import new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    tok = new_token()
    snap = _baseline(d, tok)
    _git(d, "branch", "plan-side")
    _git(d, "tag", "plan-tag")
    _git(d, "checkout", "-q", "-b", "agent/plan-sneak")
    (d / "p.txt").write_text("p\n")
    _git(d, "add", "p.txt")
    _git(d, "commit", "-q", "-m", "sneak")
    rep_, _t = _check(d, tok, snap)
    assert _git(d, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert not (d / "p.txt").exists()
    left = {r["ref"]: r["change"] for r in rep_["refs_left"]}
    assert left == {"refs/heads/plan-side": "created", "refs/heads/agent/plan-sneak": "created",
                    "refs/tags/plan-tag": "created"}, left
    assert not _denials(d)


def test_every_git_call_the_scripts_make_is_allowed_by_the_worker_proxy():
    """The literal argv shapes in the scripts, through the real `classify()` (ao-planrevert
    attempt 1 shipped plumbing the proxy denies, so production never had a baseline)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("git_proxy_t", PROXY_DIR / "git_proxy.py")
    gp = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gp
    spec.loader.exec_module(gp)
    from app.modules import plan_turn_guard as g

    src = g._COMMON + g._SNAPSHOT + g._CHECK
    shapes = set()
    for c in re.findall(r"\bgit\(([^)]*)\)", src):
        args = [a.strip() for a in c.split(",") if a.strip()]
        if not args or args[0].startswith("*"):
            continue
        argv = []
        for a in args:
            if a[0] in "\"'":
                argv.append(a.strip("\"'"))
            elif argv and argv[0] == "cat-file":
                argv.append("abc123:p")
            else:
                argv.append("abc123")
        shapes.add(tuple(argv))
    assert len(shapes) >= 8, shapes
    for argv in shapes:
        d = gp.classify(list(argv), set(), lambda _r: False, "agent/x")
        assert d.action == "allow", (argv, d.rule)


def test_no_baseline_means_nothing_is_attributed_or_reverted(tmp_path):
    from app.modules.plan_turn_guard import check_command, new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / "work.py").write_text("an earlier turn's work\n")
    rep = _run(d, check_command(new_token(), None))
    assert rep["status"] == "no-snapshot"
    assert (d / "work.py").read_text() == "an earlier turn's work\n"


def test_a_staged_coding_edit_survives_a_plan_turn_commit(tmp_path):
    """The plan turn commits everything (`git add -A && git commit`), sweeping the earlier turn's
    edit into its commit: the commit is undone and the earlier edit stays on disk."""
    from app.modules.plan_turn_guard import check_command, new_token, snapshot_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    base = _git(d, "rev-parse", "HEAD").strip()
    (d / TRACKED).write_text(TRACKED_TEXT + "coding turn line\n")
    tok = new_token()
    snap = _run(d, snapshot_command(tok))
    (d / PLAN_SCRATCH).write_text("plan\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "plan turn")
    rep = _run(d, check_command(tok, snap))
    assert rep["head_reset"]
    assert _git(d, "rev-parse", "HEAD").strip() == base
    assert {r["path"] for r in rep["reverted"]} == {"(HEAD)", PLAN_SCRATCH}
    assert (d / TRACKED).read_text() == TRACKED_TEXT + "coding turn line\n"
    assert rep["kept"] == [TRACKED]
    assert not (d / PLAN_SCRATCH).exists()


async def test_a_workspace_refocused_for_another_effort_is_not_touched(db_url, tmp_path):
    """Between the plan turn and its check another effort may have taken (and re-cloned) the
    worker: the pinned check refuses to run there, and says why."""
    from types import SimpleNamespace

    from app.modules.plan_turn_guard import new_token

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, _chan, _root = await orch.router.open_effort("ao-queue", project="ai-stack")
        orch.router._ws_focus["worker-1"] = ("some-other-effort", REPO, "")
        tok = new_token()
        from app.modules.plan_turn_guard import SNAP_TAG, frame

        snap = frame(SNAP_TAG, {"status": "ok", "token": tok, "head": "", "branch": "main",
                                "manifest": {}, "count": 0})
        res = SimpleNamespace(instance_id="worker-1", pre_turn_output=snap)
        assert await orch._revert_plan_turn_writes(eid, res, tok) is False
        assert await orch._event_count(eid, "plan_turn_wrote_files") == 0
        p = await orch._last_event_payload(eid, "plan_turn_check_unattributed")
        assert "no longer focused" in p["reason"]
        assert not any("b64decode" in c for _u, c in trace["checks"])
    finally:
        await _shutdown(orch, db)


@pytest.fixture(autouse=True)
def _git_available():
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")


# -- round 2 through `delegate` --------------------------------------------------------------
async def test_a_large_earlier_change_set_is_staged_and_the_plan_writes_still_reverted(
        db_url, tmp_path):
    """Finding 2 end to end: the coding turn left 1500 files, so the baseline is paged to the
    bridge before the plan turn and staged back for the check (several /check calls, all on the
    plan turn's worker)."""
    from app.modules.plan_turn_guard import MANIFEST_B64_CAP

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, coding_bulk=1500)
    try:
        eid = await _run_c7e(orch)
        after = trace["after_plan"]
        assert after[W1].get(CODING_FILE) == CODING_TEXT
        assert sum(1 for k in after[W1] if k.startswith("gen/")) == 1500
        assert PLAN_SCRATCH not in after[W1] and after[W1][TRACKED] == TRACKED_TEXT
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED}
        assert payload["kept_before_turn_count"] == 1501 and payload["complete"] is True
        sent = [c for _u, c in trace["checks"] if "b64decode" in c]
        assert len(sent) >= 5                 # snapshot + pages + stages + check
        assert all(len(c) < 120_000 for c in sent)
        assert all(u == W1 for u, _c in trace["checks"])
        assert max(len(c) for c in sent) > MANIFEST_B64_CAP
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_a_stopped_task_still_writing_is_waited_out_before_the_baseline(db_url, tmp_path):
    """Finding 4: the flail-stopped coding task is still running on the worker the plan turn
    lands on, and writes once more as it dies. The router cancels it and waits for the daemon to
    report no running task BEFORE the baseline, so that write belongs to the earlier turn - kept,
    never blamed on (and reverted for) the plan turn."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, stale_task=True)
    try:
        eid = await _run_c7e(orch)
        assert trace["plan_url"] == W1
        assert trace["order"].index("cancel") < trace["order"].index("check")
        after = trace["after_plan"]
        assert after[W1].get("late.py") == "late write of the stopped coding task\n"
        assert after[W1].get(CODING_FILE) == CODING_TEXT
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED}
        assert "late.py" in payload["kept_before_turn"]
    finally:
        await _shutdown(orch, db)


async def test_a_task_that_will_not_die_means_no_baseline_and_says_why(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        async def always_running(_url):
            return True

        harness.has_running_task = always_running
        note = await orch.router._wait_worker_quiet(W1, attempts=3, pause=0)
        assert note == "the worker's previous task was still running"
        from types import SimpleNamespace

        await orch.projects.add("ai-stack", REPO)
        eid, _c, _r = await orch.router.open_effort("ao-queue", project="ai-stack")
        res = SimpleNamespace(instance_id="worker-1", pre_turn_output=None, pre_turn_note=note)
        assert await orch._revert_plan_turn_writes(eid, res, "tok") is False
        p = await orch._last_event_payload(eid, "plan_turn_check_unattributed")
        assert "no pre-turn baseline" in p["reason"] and note in p["reason"]
    finally:
        await _shutdown(orch, db)


async def test_a_check_that_times_out_twice_is_recorded_from_its_journal(db_url, tmp_path):
    """Finding 1 at the gate: two check runs return nothing (timed out); the report run reads
    the journal. The record says `complete: false` with the exact count - never
    "unattributed" after files were already removed."""
    from types import SimpleNamespace

    from app.modules.plan_turn_guard import CHECK_TAG, SNAP_TAG, frame, new_token

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("ai-stack", REPO)
        eid, _c, _r = await orch.router.open_effort("ao-queue", project="ai-stack")
        orch.router._ws_focus["worker-1"] = (eid, REPO, "")
        tok = new_token()
        snap = frame(SNAP_TAG, {"status": "ok", "token": tok, "head": "h", "branch": "main",
                                "manifest": {}, "count": 0})
        calls: list[str] = []

        async def _exec(effort_id, *, command, session_id, **kw):
            import base64 as b64
            import re as _re

            src = b64.b64decode(_re.search(r"b64decode\('([^']+)'\)", command).group(1)).decode()
            kind = "report" if "journal" in src and "dirty_after" in src else "check"
            calls.append(kind)
            if kind == "check":
                return None, "", True
            return 0, frame(CHECK_TAG, {
                "status": "partial", "reverted": [{"path": f"obj/{i}", "why": "created by the "
                                                   "plan turn - removed"} for i in range(200)],
                "reverted_count": 640, "unrestored": [], "unrestored_count": 0,
                "head_reset": "", "kept": [], "kept_count": 0, "refs_left": [],
                "dirty_after": 363}), False

        orch.router.exec_check = _exec
        res = SimpleNamespace(instance_id="worker-1", pre_turn_output=snap)
        assert await orch._revert_plan_turn_writes(eid, res, tok) is True
        assert calls == ["check", "check", "report"]
        p = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert p["complete"] is False and p["reverted_count"] == 640
        assert p["dirty_after"] == 363 and len(p["reverted"]) == 60
        assert await orch._event_count(eid, "plan_turn_check_unattributed") == 0
    finally:
        await _shutdown(orch, db)
