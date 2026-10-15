"""ao-replancarry - a stopped coding turn's uncommitted work reaches the next turn after a re-plan.

C7e 0.3 ao-queue r1 (bake-off 2026-10-15, `c7/capture/db/events.jsonl`): the first coding turn ran
on worker-1 (session `effort-c7-ao-queue-r1`) and wrote its repro test
`inference/llm-queue/tests/test_leak_repro.py` without committing it (ev 12-15); the flail guard
stopped it (ev 17, `flail_replanned`); the forced plan turn ran on worker-2 in a fresh clone
(ev 20-22); after the plan gate the EXECUTION turn (`~r1`) also ran on worker-2, `fresh: true` at
base, and its delivery-branch sync found no `agent/<effort>` branch (ev 32-34). The coding turn's
file only ever existed on worker-1's disk, so the execution turn could not see it - with or
without ao-plancheck's revert.

The fix (see `app/modules/replan_carry.py`): before the re-plan, the bridge commits the stopped
turn's working tree as a WIP commit on the effort branch `agent/<effort>` ON THE STOPPED TURN'S
WORKER and pushes it there through the worker's git (= its git-proxy), the way a normal turn's
commits travel. Every later turn's focus syncs to that branch (F33), on any worker.

These tests replay the sequence through `delegate` with one REAL git checkout per fake worker and a
local `origin`; every git call of the carry, the sync and the plan check goes through the worker's
real `git_proxy.classify` (shim), and every /check reply passes the modelled open-terminal channel
(CRLF, chunking, newline join, `redact_secrets`, 64 KB tail). Fakes only: no daemon, no container,
no GPU, no network (origin is a local path)."""

from __future__ import annotations

import re
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
from test_ao_plancheck import (
    _FLAIL_OUT,
    _PLAN,
    _SHIM,
    CODING_FILE,
    CODING_TEXT,
    PLAN_SCRATCH,
    PROXY_DIR,
    REPO,
    ROOT,
    TRACKED,
    TRACKED_TEXT,
    W1,
    W2,
    Workspaces,
    _calls_log,
    _channel,
    _denials,
    _drain,
    _git,
    _proxy_log,
    _reverted_paths,
    _run_python_check,
    _shutdown,
)

_EXEC_OUT = "Implemented the fix; tests pass."


def _shim(ws: Path) -> Path:
    shim = ws.parent / (ws.name + ".git-shim.py")
    shim.write_text(_SHIM.format(proxy=str(PROXY_DIR), log=str(_proxy_log(ws)),
                                 calls=str(_calls_log(ws))))
    return shim


def _pgit(ws: Path, *args: str) -> subprocess.CompletedProcess:
    """One git call as the worker's shell makes it: through the git-proxy's classify()."""
    return subprocess.run([sys.executable, str(_shim(ws)), *args], cwd=ws, capture_output=True,
                          text=True)


_SYNC = re.compile(r"^cd /workspace && git fetch origin (\S+) && git checkout -f -B (\S+) "
                   r"FETCH_HEAD$")


def _run_sync(ws: Path, command: str) -> tuple[int, str, bool]:
    """F33's delivery-branch sync (`router.wake(checkout_branch=)`), step by step through the
    proxy with the shell's `&&` semantics. The real shell runs it in the open-terminal e2e."""
    m = _SYNC.match(command)
    assert m, f"unexpected sync command {command!r}"
    out = ""
    for argv in (("fetch", "origin", m.group(1)), ("checkout", "-f", "-B", m.group(2),
                                                   "FETCH_HEAD")):
        p = _pgit(ws, *argv)
        out += p.stdout + p.stderr
        if p.returncode != 0:
            return p.returncode, _channel(out, ""), False
    return 0, _channel(out, ""), False


async def _orch(db_url, tmp: Path, *, coding_edits: bool = True, plan_edits_coding: bool = False,
                plan_commits: bool = False, fresh_exec_on: str | None = None,
                ignore: str | None = None, extra_coding: dict[str, str] | None = None):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=f"{W1},{W2}",
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="off",
        worker_flail_guard=True, sync_delivery_branch=True,
    )
    db = Database(db_url)
    harness = FakeHarness()
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=harness)
    await orch.setup()
    ws = Workspaces(tmp)
    if ignore:
        (ws.origin / ".gitignore").write_text(ignore)
        _git(ws.origin, "add", "-A")
        _git(ws.origin, "commit", "-q", "-m", "ignore")
        ws.base = _git(ws.origin, "rev-parse", "HEAD").strip()
    trace: dict = {"coding_url": None, "plan_url": None, "plan_saw": None, "exec_url": None,
                   "exec_saw": None, "exec_head": None, "checks": [], "focus": []}

    orig_set_project = harness.set_project

    async def set_project(base_url, repo, **kw):
        fresh = bool(kw.get("fresh"))
        if fresh_exec_on == base_url and trace["plan_url"] is not None \
                and trace["exec_url"] is None:
            fresh = True    # C7e ev 33: the execution turn's focus was `fresh: true` at base
            trace["exec_fresh"] = True
        trace["focus"].append((base_url, fresh))
        ws.focus(base_url, fresh)
        return await orig_set_project(base_url, repo, **kw)

    orig_wake = harness.wake

    async def wake(base_url, session_id, prompt, **kw):
        d = ws.dirs[base_url]
        if kw.get("plan_only"):
            trace["plan_url"] = base_url
            trace["plan_saw"] = ws.files(base_url)
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
                for rel, text in (extra_coding or {}).items():
                    (d / rel).parent.mkdir(parents=True, exist_ok=True)
                    (d / rel).write_text(text)
            harness.output_queue.insert(0, _FLAIL_OUT)
        elif trace["plan_url"] is not None and trace["exec_url"] is None:
            # the EXECUTION turn after the plan gate: what does its workspace start from?
            trace["exec_url"] = base_url
            trace["exec_saw"] = ws.files(base_url)
            trace["exec_head"] = _git(d, "rev-parse", "HEAD").strip()
            harness.output_queue.insert(0, _EXEC_OUT)
        return await orig_wake(base_url, session_id, prompt, **kw)

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command})
        trace["checks"].append((base_url, command))
        d = ws.dirs.get(base_url)
        if d is None:
            raise RuntimeError("fake /check: worker not focused")
        if "b64decode" in command:
            return _run_python_check(d, command)
        if command.startswith("cd /workspace && git fetch origin "):
            return _run_sync(d, command)
        raise RuntimeError(f"fake /check: unexpected command {command[:80]!r}")

    harness.set_project = set_project
    harness.wake = wake
    harness.run_check = run_check
    return orch, harness, db, ws, trace


async def _keep_worker_1_off_the_new_session(orch) -> None:
    """C7e: after the flail fork the new generation's sessions (`~r1~plan`, then `~r1`) landed on
    worker-2 while worker-1 (the coding turn's workspace) stayed suspended. A check pinned to
    worker-1 (`instance_id=`) still reaches it."""
    orig = orch.scheduler.acquire

    async def acquire(effort_id, role, session_id, *a, **kw):
        if "~r1" not in session_id:
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


async def _run(orch):
    await orch.projects.add("ai-stack", REPO)
    eid, chan, root = await orch.router.open_effort("ao-queue", project="ai-stack")
    await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
    await _drain(orch)
    return eid


def _no_denials(ws: Workspaces) -> None:
    denied = "".join(_denials(d) for d in ws.dirs.values())
    assert not denied, "the worker's git-proxy would refuse: " + denied


def _origin_branches(ws: Workspaces) -> dict[str, str]:
    out = _git(ws.origin, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())


def _origin_file(ws: Workspaces, ref: str, path: str) -> str | None:
    p = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ws.origin, capture_output=True,
                       text=True)
    return p.stdout if p.returncode == 0 else None


# -- 1. the C7e r1 sequence: coding turn on worker-1, plan + execution on worker-2 ----------------
async def test_c7e_execution_turn_on_another_worker_starts_from_the_coding_turns_file(
        db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, fresh_exec_on=W2)
    try:
        await _keep_worker_1_off_the_new_session(orch)
        eid = await _run(orch)
        assert await orch._event_count(eid, "flail_replanned") == 1
        assert trace["coding_url"] == W1 and trace["plan_url"] == W2, trace
        assert trace["exec_url"] == W2, trace
        assert trace.get("exec_fresh"), trace["focus"]     # a fresh clone at base, as in C7e
        # THE BAR: the execution turn's workspace contains the coding turn's file
        assert trace["exec_saw"] is not None, "the effort never reached the execution turn"
        assert trace["exec_saw"].get(CODING_FILE) == CODING_TEXT, sorted(trace["exec_saw"])
        # and the plan turn observed it too (it plans from the workspace, not from memory)
        assert trace["plan_saw"].get(CODING_FILE) == CODING_TEXT
        # what was carried, recorded
        branch = f"agent/{eid}"
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None
        assert carried["worker"] == "worker-1" and carried["branch"] == branch
        assert carried["files"] == [CODING_FILE] and carried["n_files"] == 1
        assert carried["pushed"] is True and re.fullmatch(r"[0-9a-f]{40}", carried["commit"])
        # it travelled the way turn commits travel: the effort branch on origin, never main
        refs = _origin_branches(ws)
        assert refs["main"] == ws.base
        assert refs[branch] == carried["commit"]
        assert _origin_file(ws, branch, CODING_FILE) == CODING_TEXT
        assert trace["exec_head"] == carried["commit"]
        # ao-plancheck still holds: the plan turn's writes reverted, the WIP commit not blamed
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED}, payload
        assert payload["worker"] == "worker-2" and not payload.get("head_reset")
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- 2. same-worker re-engage: everything on worker-1 -------------------------------------------
async def test_same_worker_reengage_starts_from_the_coding_turns_file(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        eid = await _run(orch)
        assert trace["coding_url"] == trace["plan_url"] == trace["exec_url"] == W1, trace
        assert trace["exec_saw"].get(CODING_FILE) == CODING_TEXT
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried["worker"] == "worker-1" and carried["files"] == [CODING_FILE]
        assert trace["exec_head"] == carried["commit"]
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        # the coding file is committed now: neither reverted nor "kept dirty"
        assert _reverted_paths(payload) == {PLAN_SCRATCH, TRACKED}, payload
        assert CODING_FILE not in payload["kept_before_turn"]
        assert not payload.get("head_reset")
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- 3. a clean tree: nothing to carry, no WIP commit --------------------------------------------
async def test_a_clean_tree_makes_no_wip_commit(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, coding_edits=False)
    try:
        eid = await _run(orch)
        assert await orch._event_count(eid, "flail_replanned") == 1
        assert await orch._event_count(eid, "stopped_turn_work_carried") == 0
        clean = await orch._last_event_payload(eid, "stopped_turn_carry_clean")
        assert clean is not None and clean["worker"] == "worker-1"
        assert f"agent/{eid}" not in _origin_branches(ws)
        assert trace["exec_head"] == ws.base
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- 4. a plan-turn write AFTER the WIP commit is still reverted (ao-plancheck composes) ---------
@pytest.mark.parametrize("other_worker", [True, False], ids=["plan-on-worker-2", "same-worker"])
async def test_a_plan_turn_write_after_the_wip_commit_is_still_reverted(db_url, tmp_path,
                                                                        other_worker):
    orch, harness, db, ws, trace = await _orch(
        db_url, tmp_path, plan_edits_coding=True, plan_commits=True,
        fresh_exec_on=W2 if other_worker else None)
    try:
        if other_worker:
            await _keep_worker_1_off_the_new_session(orch)
        eid = await _run(orch)
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried is not None
        payload = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        # the plan turn's commit is undone back to the WIP commit (not to base) ...
        assert payload["head_reset"], payload
        assert {PLAN_SCRATCH, TRACKED, CODING_FILE} <= _reverted_paths(payload), payload
        # ... and the coding file is back to the coding turn's content, not the plan's edit
        assert trace["exec_saw"].get(CODING_FILE) == CODING_TEXT
        assert PLAN_SCRATCH not in trace["exec_saw"]
        assert trace["exec_saw"][TRACKED] == TRACKED_TEXT
        assert trace["exec_head"] == carried["commit"]
        # the plan turn's commit never reached the effort branch
        assert _origin_branches(ws)[f"agent/{eid}"] == carried["commit"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- 5. the carry commits what a normal turn commit would: .gitignore holds ----------------------
async def test_ignored_files_are_not_carried(db_url, tmp_path):
    orch, harness, db, ws, trace = await _orch(
        db_url, tmp_path, ignore="*.env\n.cache/\n",
        extra_coding={"local.env": "TOKEN=not-for-git\n", ".cache/x.bin": "cache\n"})
    try:
        await _keep_worker_1_off_the_new_session(orch)
        eid = await _run(orch)
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried["files"] == [CODING_FILE]
        branch = f"agent/{eid}"
        assert _origin_file(ws, branch, "local.env") is None
        assert _origin_file(ws, branch, ".cache/x.bin") is None
        assert "local.env" not in trace["exec_saw"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


# -- unit: the carry script against real repositories, through the proxy and the channel --------
def _clone(tmp: Path) -> tuple[Workspaces, Path]:
    ws = Workspaces(tmp)
    ws.focus(W1, True)
    return ws, ws.dirs[W1]


def _carry(d: Path, branch: str = "agent/e1"):
    from app.modules.replan_carry import carry_command, parse_carry

    rc, out, _t = _run_python_check(d, carry_command(
        branch, subject="wip: e1", body="carried", author=("worker-default", "w@x.invalid")))
    return parse_carry(out)


def test_the_carry_refuses_a_protected_or_odd_branch():
    from app.modules.replan_carry import carry_command

    for bad in ("main", "master", "agent/../main", "refs/heads/main", "agent/x y", "agent/",
                "feature/x", "agent/-x"):
        with pytest.raises(ValueError):
            carry_command(bad, subject="s", body="b", author=("a", "a@x"))


def test_unpushed_commits_are_carried_even_with_a_clean_tree(tmp_path):
    ws, d = _clone(tmp_path)
    (d / "n.py").write_text("x = 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "turn commit, never pushed")
    rep = _carry(d)
    assert rep["status"] == "ok", rep
    assert rep["commit"] == "" and rep["unpushed_before"] == 1 and rep["pushed"] is True
    assert _origin_branches(ws)["agent/e1"] == _git(d, "rev-parse", "HEAD").strip()
    assert _origin_branches(ws)["main"] == ws.base
    assert not _denials(d)


def test_a_failed_push_is_reported_and_the_commit_stays_local(tmp_path):
    ws, d = _clone(tmp_path)
    (d / "n.py").write_text("x = 1\n")
    # origin moves out from under the worker (the push has nowhere to go)
    ws.origin.rename(ws.origin.with_name("origin-gone"))
    rep = _carry(d)
    assert rep["status"] == "ok" and rep["pushed"] is False, rep
    assert rep["commit"] and rep["files"] == ["n.py"]
    assert _git(d, "rev-parse", "HEAD").strip() == rep["commit"]
    assert _git(d, "rev-parse", "--abbrev-ref", "HEAD").strip() == "agent/e1"
    assert _git(d, "status", "--porcelain") == ""


def test_token_shaped_and_unicode_paths_are_reported_intact(tmp_path):
    ws, d = _clone(tmp_path)
    tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    names = [f"fixtures/{tok}.txt", "docs/résumé notes.md"]
    for n in names:
        (d / n).parent.mkdir(parents=True, exist_ok=True)
        (d / n).write_text("x\n")
    rep = _carry(d)
    assert rep["status"] == "ok" and rep["pushed"] is True, rep
    assert sorted(rep["files"]) == sorted(names)
    assert not _denials(d)


def test_a_diverged_local_effort_branch_is_not_moved(tmp_path):
    """The worker sits on main with work, but a local agent/e1 exists that main does not contain:
    `checkout -B` would drop its commits, so the carry commits where it stands and says so."""
    ws, d = _clone(tmp_path)
    _git(d, "checkout", "-q", "-b", "agent/e1")
    (d / "other.py").write_text("y = 2\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "local only")
    side = _git(d, "rev-parse", "HEAD").strip()
    _git(d, "checkout", "-q", "main")
    (d / "n.py").write_text("x = 1\n")
    rep = _carry(d)
    assert rep["status"] == "error", rep
    assert "diverged" in rep["why"]
    assert _git(d, "rev-parse", "agent/e1").strip() == side      # untouched
    assert (d / "n.py").read_text() == "x = 1\n"                  # nothing lost
    assert "agent/e1" not in _origin_branches(ws)


def test_every_git_call_the_carry_makes_is_allowed_by_the_worker_proxy():
    sys.path.insert(0, str(PROXY_DIR))
    try:
        import git_proxy
    finally:
        sys.path.remove(str(PROXY_DIR))
    remotes = {"origin"}
    for argv in (
        ["rev-parse", "--absolute-git-dir"], ["rev-parse", "--verify", "-q", "HEAD"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all"],
        ["rev-list", "--count", "HEAD", "--not", "--remotes=origin"],
        ["rev-parse", "--verify", "-q", "refs/heads/agent/e1"],
        ["merge-base", "--is-ancestor", "refs/heads/agent/e1", "HEAD"],
        ["checkout", "-q", "-B", "agent/e1"], ["add", "-A"],
        ["commit", "-q", "-m", "wip: e1", "-m", "carried"],
        ["push", "-q", "origin", "HEAD:refs/heads/agent/e1"],
        ["rev-parse", "--verify", "-q", "refs/remotes/origin/agent/e1"],
    ):
        d = git_proxy.classify(argv, remotes, lambda _r: False, "main")
        assert d.action == "allow", (argv, d)
    # and the proxy would stop a carry aimed at production even if the bridge let one through
    d = git_proxy.classify(["push", "-q", "origin", "HEAD:refs/heads/main"], remotes,
                           lambda _r: False, "agent/e1")
    assert d.action == "deny"


def test_the_reply_survives_the_channel():
    from app.modules.plan_turn_guard import frame
    from app.modules.replan_carry import CARRY_TAG, parse_carry

    obj = {"status": "ok", "files": ["a\nb", "ghp_" + "x" * 30], "pushed": True}
    assert parse_carry(_channel(frame(CARRY_TAG, obj) + "\n", "warning: noise")) == obj
    assert parse_carry("nothing framed here") is None
