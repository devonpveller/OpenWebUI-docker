"""ao-preflight2 round 2 - the tester's attempt-1 failures and findings.

F1  an earlier untracked file the plan turn EDITED and `git add`ed made the whole batched
    `rm --cached` (no `-f`) fail: every file in that chunk stayed staged and the next F33
    `checkout -f -B` deleted it. Now `rm --cached -f`, per-path retry, verified, recorded only on
    success.
F2  the ignored-file unstage was recorded before it ran and its rc ignored. Same fix.
F3  a hang in the branch switch-back came before any restore, and the partial report then deleted
    the snapshot that held the only copies. Now the config keys, the git dir's rules and every
    earlier-turn file are put back git-free FIRST, and a snapshot that still holds earlier work
    not on disk is never deleted (kept under `ao-plan-snapshot.kept-<n>`).
(a) `.git/config` filter/diff/merge/include sections and core.excludesFile / attributesFile /
    fsmonitor are in the baseline and restored first.
(b) the `_finish_effort` closures that never see the head (unverifiable, no branch landed, and the
    no_changes re-verify when the remote cannot be read) hold an effort that had a WIP carried.
(c) the branch reaper never deletes an effort branch that holds carried work.
(d) ignored earlier-turn files are snapshotted (capped, the rest counted) and restored.
(e) the F33 sync unstages files staged as new before `checkout -f -B`, so they survive.
Same rigs as test_ao_plancheck / test_ao_replancarry (real git, every call through the worker's
git-proxy `classify`); no daemon, container, GPU or network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.capabilities import BranchDelivery
from test_ao_plancheck import (
    CODING_FILE,
    CODING_TEXT,
    TRACKED,
    TRACKED_TEXT,
    W1,
    _baseline,
    _check,
    _denials,
    _drop_short_roots,  # noqa: F401 - autouse fixture for _workspaces
    _git,
    _run_python_check,
    _shutdown,
    _snapdir,
    _workspaces,
)
from test_ao_preflight2 import _wire_delivery
from test_ao_replancarry import (
    _keep_worker_1_off_the_new_session,
    _orch,
    _pgit,
    _run_sync,
)
from test_ao_replancarry import _run as _run_effort


def _repo(tmp_path, gitignore: str | None = None):
    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    if gitignore is not None:
        (d / ".gitignore").write_text(gitignore)
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "rules")
    _git(d, "push", "-q", "origin", "HEAD:refs/heads/agent/e1")
    _git(d, "checkout", "-q", "-b", "agent/e1")
    return ws, d


def _sync(d: Path) -> None:
    from app.modules.router import delivery_sync_command

    code, out, _t = _run_sync(d, delivery_sync_command("agent/e1"))
    assert code == 0, out


def _staged(d: Path) -> list[str]:
    return [ln for ln in _git(d, "status", "--porcelain", "-uall").splitlines()
            if ln[:1] not in ("?", " ", "!")]


# == F1 ===========================================================================================
@pytest.mark.parametrize("commit", [False, True], ids=["add", "add-commit"])
def test_one_edited_earlier_file_does_not_sink_its_whole_unstage_batch(tmp_path, commit):
    from app.modules.plan_turn_guard import new_token

    ws, d = _repo(tmp_path)
    (d / "late").mkdir()
    late = [f"late/f_{i:03d}.txt" for i in range(50)]
    for p in late:
        (d / p).write_text(p + "\n")
    (d / "coding.py").write_text("CODING\n")
    tok = new_token()
    snap = _baseline(d, tok)
    # the plan turn clobbers one earlier file and the first late file, then stages everything
    (d / "coding.py").write_text("plan clobber\n")
    (d / late[0]).write_text("plan touched this\n")
    _pgit(d, "add", "-A")
    if commit:
        _pgit(d, "commit", "-q", "-m", "plan")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok", rep
    assert _staged(d) == [], _staged(d)
    assert rep["unstaged_count"] == 51 and rep["unrestored_count"] == 0, rep
    _sync(d)
    assert (d / "coding.py").read_text() == "CODING\n"
    for p in late:
        assert (d / p).read_text() == p + "\n", p
    assert not _denials(d)


# == F2 ===========================================================================================
def test_ignored_files_swept_in_and_rewritten_are_unstaged_and_restored(tmp_path):
    from app.modules.plan_turn_guard import new_token

    ws, d = _repo(tmp_path, gitignore="build/\nlocal.env\n")
    (d / "build").mkdir()
    (d / "build" / "out.bin").write_text("CODING BUILD\n")
    (d / "local.env").write_text("CODING_SETTING=1\n")
    tok = new_token()
    snap = _baseline(d, tok)
    (d / ".gitignore").write_text("# none\n")
    _pgit(d, "add", "-A")
    with open(d / "build" / "out.bin", "a") as fh:
        fh.write("plan rebuild\n")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok", rep
    assert _staged(d) == [], _staged(d)
    assert {"build/out.bin", "local.env"} <= set(rep["unstaged"]), rep["unstaged"]
    _sync(d)
    assert (d / "local.env").read_text() == "CODING_SETTING=1\n"
    assert (d / "build" / "out.bin").read_text() == "CODING BUILD\n"      # (d) restored


# == F3 ===========================================================================================
def test_a_hang_in_the_branch_switch_back_comes_after_the_earlier_files_are_back(tmp_path):
    from app.modules.plan_turn_guard import check_command, new_token, parse_check, report_command

    ws, d = _repo(tmp_path)
    (d / "coding.py").write_text("CODING\n")
    (d / "cod").mkdir()
    (d / "cod" / "new.py").write_text("CODING NEW\n")
    (d / TRACKED).write_text(TRACKED_TEXT + "CODING EDIT\n")
    tok = new_token()
    snap = _baseline(d, tok)
    _git(d, "checkout", "-q", "-b", "plan-side")
    _git(d, "add", TRACKED)
    _git(d, "commit", "-q", "-m", "plan")
    (d / "coding.py").write_text("plan clobber\n")
    (d / "cod" / "new.py").write_text("plan clobber\n")
    hang = {"AO_SHIM_HANG": "checkout", "AO_SHIM_HANG_SECS": "20"}
    assert _run_python_check(d, check_command(tok, snap), 8, hang)[2]       # cut off
    assert (d / "coding.py").read_text() == "CODING\n"
    assert (d / "cod" / "new.py").read_text() == "CODING NEW\n"
    assert (d / TRACKED).read_text() == TRACKED_TEXT + "CODING EDIT\n"
    rep = parse_check(_run_python_check(d, report_command(tok))[1])
    assert rep["status"] == "partial"
    assert {"coding.py", "cod/new.py"} <= {r["path"] for r in rep["reverted"]}


def test_a_snapshot_holding_earlier_work_not_on_disk_is_never_deleted(tmp_path):
    from app.modules.plan_turn_guard import new_token, parse_check, report_command

    ws, d = _repo(tmp_path)
    (d / "coding.py").write_text("CODING\n")
    tok = new_token()
    _baseline(d, tok)
    (d / "coding.py").write_text("plan clobber\n")
    # the check never ran (both runs killed before anything): only the report
    rep = parse_check(_run_python_check(d, report_command(tok))[1])
    kept = Path(rep["snapshot_kept"])
    assert rep["snapshot_pending"] == 1 and kept.name.startswith("ao-plan-snapshot.kept-")
    assert (kept / "files" / "coding.py").read_text() == "CODING\n"
    assert not _snapdir(d).exists()                 # the next snapshot cannot reuse the name


# == (a) .git/config ==============================================================================
def test_config_keys_the_plan_turn_changed_are_restored_first(tmp_path):
    from app.modules.plan_turn_guard import new_token

    ws, d = _repo(tmp_path, gitignore="")
    gd = Path(_git(d, "rev-parse", "--absolute-git-dir").strip())
    # the EARLIER turn ignores build/ through core.excludesFile
    (gd / "my-ign").write_text("build/\n")
    with open(gd / "config", "a") as fh:
        fh.write(f"[core]\n\texcludesFile = {(gd / 'my-ign').as_posix()}\n")
    (d / "build").mkdir()
    (d / "build" / "out.bin").write_text("CODING BUILD\n")
    (d / "coding.py").write_text("CODING\n")
    tok = new_token()
    snap = _baseline(d, tok)
    # the plan turn drops that key, adds its own excludes file hiding *.plan, and a filter
    cfg = (gd / "config").read_text().replace(
        f"\texcludesFile = {(gd / 'my-ign').as_posix()}\n", "")
    (gd / "pl-ign").write_text("*.plan\n")
    cfg += (f"[core]\n\texcludesFile = {(gd / 'pl-ign').as_posix()}\n"
            "[filter \"slow\"]\n\tsmudge = sleep 600; cat\n")
    (gd / "config").write_text(cfg)
    for i in range(3):
        (d / f"x{i}.plan").write_text("p")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok", rep
    assert (d / "build" / "out.bin").read_text() == "CODING BUILD\n"
    assert not any((d / f"x{i}.plan").exists() for i in range(3))
    now = (gd / "config").read_text()
    assert "my-ign" in now and "pl-ign" not in now and "slow" not in now
    assert _git(d, "config", "--get", "remote.origin.url").strip()        # the rest untouched
    assert "(.git/config)" in {r["path"] for r in rep["reverted"]}


# == (d) ignored files =============================================================================
def test_an_ignored_earlier_file_the_plan_turn_edits_or_deletes_is_restored(tmp_path):
    from app.modules.plan_turn_guard import new_token

    ws, d = _repo(tmp_path, gitignore="build/\n*.env\nbig/\n")
    (d / "build").mkdir()
    (d / "build" / "out.bin").write_text("CODING BUILD\n")
    (d / "local.env").write_text("CODING_SETTING=1\n")
    (d / "big").mkdir()
    (d / "big" / "huge.bin").write_bytes(b"x" * (5 * 1024 * 1024))     # over the per-file cap
    tok = new_token()
    snap = _baseline(d, tok)
    (d / "build" / "out.bin").write_text("plan overwrote\n")
    (d / "local.env").unlink()
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok", rep
    assert (d / "build" / "out.bin").read_text() == "CODING BUILD\n"
    assert (d / "local.env").read_text() == "CODING_SETTING=1\n"
    assert rep["ignored_skipped"] == 1
    assert {"build/out.bin", "local.env"} <= {r["path"] for r in rep["reverted"]}


# == (e) the F33 sync keeps files staged as new ====================================================
def test_the_delivery_sync_keeps_an_earlier_staged_new_file(tmp_path):
    ws, d = _repo(tmp_path)
    (d / CODING_FILE).parent.mkdir(parents=True)
    (d / CODING_FILE).write_text(CODING_TEXT)
    _git(d, "add", CODING_FILE)
    (d / TRACKED).write_text(TRACKED_TEXT)
    _sync(d)
    assert (d / CODING_FILE).read_text() == CODING_TEXT
    assert _git(d, "status", "--porcelain", "-uall").splitlines() == [f"?? {CODING_FILE}"]
    assert not _denials(d)


def test_every_git_call_of_the_sync_is_allowed_by_the_worker_proxy():
    import shlex
    import sys

    from app.modules.router import delivery_sync_command
    from test_ao_plancheck import PROXY_DIR

    sys.path.insert(0, str(PROXY_DIR))
    import git_proxy

    cmd = delivery_sync_command("agent/e1")
    calls = [seg.strip() for part in cmd.split("&&") for seg in part.replace("{", "").replace(
        "}", "").replace(";", "|").split("|") if seg.strip().startswith(("git ", "xargs "))]
    assert len(calls) == 4, calls
    for c in calls:
        argv = shlex.split(c)
        argv = argv[argv.index("git") + 1:]
        d = git_proxy.classify(argv, {"origin"}, lambda _r: False, None)
        assert d.action != "deny", (c, d.rule)


# == (b) closures that never see the head ==========================================================
@pytest.mark.parametrize("kind", ["unverifiable", "no-branch", "no-changes-unreadable"])
async def test_a_closure_that_cannot_see_the_head_never_closes_a_carried_effort_done(
        db_url, tmp_path, kind):
    from test_ao_plancheck import W2

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, fresh_exec_on=W2)
    try:
        await _keep_worker_1_off_the_new_session(orch)
        eid = await _run_effort(orch)
        assert (await orch._last_event_payload(eid, "stopped_turn_work_carried"))["pushed"]
        rec = _wire_delivery(orch, harness, ws, tmp_path, no_changes_ok=True, exec_reply="silent")

        async def unreadable(effort_id, repo):
            return BranchDelivery(branch=f"agent/{effort_id}", detail="read failed")
        orch._verify_delivery = unreadable
        orch._carried_heads.clear()                  # as after a restart: only the audit log
        if kind == "unverifiable":
            orch._published_branch[eid] = f"agent/{eid}"
            delivery = BranchDelivery(branch=f"agent/{eid}", detail="read failed")
        elif kind == "no-branch":
            delivery = BranchDelivery(verifiable=True, exists=False, branch=f"agent/{eid}")
        else:
            delivery = BranchDelivery(no_changes=True, branch=f"agent/{eid}")
        await orch._finish_effort(eid, SimpleNamespace(status="done", output="ok"),
                                  delivery=delivery)
        assert rec["cards"] == ["needs-attention"], rec["cards"]
        assert rec["prs"] == []
        held = await orch._last_event_payload(eid, "delivery_wip_only_held")
        assert held and held["unconfirmed"] is True, held
        assert not any("finished (**done**)" in p for p in rec["posts"])
    finally:
        await _shutdown(orch, db)


# == (c) the reaper ================================================================================
async def test_the_reaper_never_deletes_a_branch_that_holds_carried_work(db_url, tmp_path,
                                                                       monkeypatch):
    import app.orchestrator as om

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("ai-stack", "https://github.com/c7-sandbox/ai-stack")
        await orch.audit.log("stopped_turn_work_carried", effort_id="e-held", payload={
            "pushed": True, "commit": "a" * 40, "head": "a" * 40})
        orch.github = SimpleNamespace(owner="x")
        orch.s.github_app_id = "1"
        key = tmp_path / "app.pem"
        key.write_text("x")
        orch.s.github_app_private_key_path = str(key)

        async def classify(*a, **kw):
            return {"merged": [], "unmerged": [{"name": "agent/e-held"}, {"name": "agent/e-old"}],
                    "open_pr": ["agent/e-new"], "pr_num": {},
                    "dates": {"agent/e-held": "2000-01-01T00:00:00Z",
                              "agent/e-old": "2000-01-01T00:00:00Z",
                              "agent/e-new": "2999-01-01T00:00:00Z"}}
        deleted: list[str] = []

        async def delete(github, repo, b, **kw):
            deleted.append(b)
            return SimpleNamespace(ok=True)
        monkeypatch.setattr(om, "classify_agent_branches", classify)
        monkeypatch.setattr(om, "delete_branch", delete)
        await orch._reap_abandoned_branches()
        assert deleted == ["agent/e-old"], deleted
        skipped = await orch._last_event_payload("e-held", "branch_reap_skipped")
        assert skipped and skipped["branch"] == "agent/e-held"
    finally:
        await _shutdown(orch, db)


def test_report_fields_survive_the_channel():
    """the new reply fields are inside the framed base64 like the rest"""
    from app.modules.plan_turn_guard import CHECK_TAG, frame, parse_check
    from test_ao_plancheck import _channel

    obj = {"status": "ok", "snapshot_kept": "/w/.git/ao-plan-snapshot.kept-abc",
           "snapshot_pending": 2, "ignored_skipped": 3, "unstaged": ["a"], "unstaged_count": 1}
    assert parse_check(_channel(frame(CHECK_TAG, obj), "")) == obj
    assert json.dumps(obj)
