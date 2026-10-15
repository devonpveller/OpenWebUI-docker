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
import subprocess, sys
sys.path.insert(0, {proxy!r})
import git_proxy
argv = sys.argv[1:]
sub, _r, _e = git_proxy._find_subcommand(argv)
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


def _run_python_check(ws: Path, command: str) -> tuple[int, str, bool]:
    """Runs the bridge's base64-encoded Python check for real, against this worker's checkout,
    with EVERY git call routed through the worker's real git-proxy `classify()`."""
    m = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", command)
    assert m, command[:120]
    assert len(command) < 120_000, "a /check command must stay under Linux's 128 KB arg limit"
    src = base64.b64decode(m.group(1)).decode()
    shim = ws.parent / (ws.name + ".git-shim.py")
    shim.write_text(_SHIM.format(proxy=str(PROXY_DIR), log=str(_proxy_log(ws))))
    assert "WS = '/workspace'" in src and 'GIT = ["git"]' in src
    src = src.replace("WS = '/workspace'", f"WS = {str(ws)!r}")
    src = src.replace('GIT = ["git"]', f"GIT = [{sys.executable!r}, {str(shim)!r}]")
    p = subprocess.run([sys.executable, "-c", src], cwd=ws, capture_output=True, text=True,
                       timeout=120)
    return p.returncode, _channel(p.stdout, p.stderr), False


def _denials(ws: Path) -> str:
    f = _proxy_log(ws)
    return f.read_text() if f.exists() else ""


async def _orch(db_url, tmp: Path, *, coding_edits: bool = True, plan_edits_coding: bool = False,
                plan_commits: bool = False):
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
    ws = Workspaces(tmp)
    trace: dict = {"plan_url": None, "coding_url": None, "after_plan": None, "checks": []}

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
            harness.output_queue.insert(0, _FLAIL_OUT)
        return await orig_wake(base_url, session_id, prompt, **kw)

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command})
        trace["checks"].append((base_url, command))
        d = ws.dirs.get(base_url)
        if d is None:
            raise RuntimeError("fake /check: worker not focused")
        if "b64decode" in command:
            return _run_python_check(d, command)
        return _run_shell_probe(d, command)

    harness.set_project = set_project
    harness.wake = wake
    harness.run_check = run_check
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

    ws = Workspaces(tmp_path)
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

    ws = Workspaces(tmp_path)
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

    ws = Workspaces(tmp_path)
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

    ws = Workspaces(tmp_path)
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


def test_a_huge_earlier_change_set_still_attributes_from_disk(tmp_path):
    """Past the size the bridge carries back, the snapshot omits the manifest and the check
    uses the on-disk baseline (token-matched): still attributed, nothing earlier lost."""
    import secrets

    from app.modules.plan_turn_guard import check_command, new_token, snapshot_command

    ws = Workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    gen = d / "gen"
    gen.mkdir()
    for _ in range(1500):
        (gen / (secrets.token_hex(24) + ".txt")).write_text(secrets.token_hex(8))
    tok = new_token()
    snap = _run(d, snapshot_command(tok))
    assert snap["manifest"] is None and snap["count"] == 1500     # overflowed, still a baseline
    cmd = check_command(tok, snap)
    (d / PLAN_SCRATCH).write_text("plan\n")
    rep_ = _run(d, cmd)
    assert {r["path"] for r in rep_["reverted"]} == {PLAN_SCRATCH}
    assert rep_["kept_count"] == 1500
    assert len(list(gen.iterdir())) == 1500
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

    ws = Workspaces(tmp_path)
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

    ws = Workspaces(tmp_path)
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
