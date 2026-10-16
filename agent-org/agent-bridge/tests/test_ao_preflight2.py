"""ao-preflight2 - the stoppers the ao-plancheck and ao-replancarry testers found after both passed.

(1) A carried WIP-only branch is never delivered or closed done (ao-replancarry attempt 2,
    Finding 1). After a carry the effort branch head is the stopped turn's WIP commit. Two routes
    still ended at `_finish_effort`'s no_changes re-verify, which saw a branch ahead of main and
    DELIVERED it (PR, D2, develop): (a) the execution turn says `NO CHANGES:` on a goal where
    `_no_changes_acceptable` holds; (b) silent turn -> WIP-only stale path -> silent re-engage ->
    read-only STATE CHECK says `STATE HOLDS` (the worker sees the carried file in its checkout).
    Now: (b) runs no state check on a WIP-only branch (it escalates), and both `_finish_effort`
    chokepoints - the no_changes re-verify and the landed branch - hold a WIP-only head for a
    human (`delivery_wip_only_held`, needs-attention, no PR). The composition path does not bump
    an engine to a WIP-only submodule head either.
(2) Paths that were UNTRACKED before the plan turn and that the plan turn `git add`ed (or
    committed: the soft reset leaves them staged) are unstaged by the check, so the next F33
    sync's `checkout -f -B` does not delete them.
(3) A plan turn that rewrites .gitignore / .gitattributes / .git/info/exclude no longer makes the
    check delete the earlier turn's ignored files (the rules are restored first and status is
    read again; a staged ignored file is unstaged, never removed), and a check cut off later has
    already restored the earlier turn's files (they go first).
(4) A snapshot dir the turn replaced with a file is replaced by a directory again, no crash.
(5) The journal is named by a nonce the bridge makes after the turn: a journal the turn planted
    is never reported.
Review notes on ao-replancarry: (R1) a whitespace-only stopped-turn output no longer raises in
the carry / the re-plan; (R2) no model text in the pushed WIP commit; (R3) a failed commit with
an unknown unpushed count is reported failed, not ok.

Same rigs as test_ao_plancheck / test_ao_replancarry: real git checkouts per fake worker, every
git call through the worker's real `git_proxy.classify` (shim), the modelled open-terminal channel.
No daemon, container, GPU or network."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.capabilities import BranchDelivery
from test_ao_plancheck import (
    CODING_FILE,
    CODING_TEXT,
    PLAN_SCRATCH,
    TRACKED,
    TRACKED_TEXT,
    W1,
    W2,
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
from test_ao_replancarry import (
    _keep_worker_1_off_the_new_session,
    _no_denials,
    _orch,
    _origin_branches,
    _pgit,
    _remote_delivery,
)
from test_ao_replancarry import _run as _run_effort


# == (1) a WIP-only branch is never delivered or done ============================================
def _wire_delivery(orch, harness, ws, tmp_path, *, no_changes_ok: bool, exec_reply: str,
                   state_holds: bool = False, build: str = "pass"):
    """GitHub reads against the local origin, an org build with a fixed verdict, and probes on
    every place a delivery is accepted (tester t2's harness): `_finish_effort`'s argument,
    `_open_delivery_pr`, the effort card, the posts, and how many state checks ran."""
    key = tmp_path / "app.pem"
    key.write_text("not a key - the GitHub reads below are faked\n")
    orch.github = SimpleNamespace(owner="not-this-repos-owner")
    orch.s.github_app_id = "1"
    orch.s.github_app_private_key_path = str(key)
    assert orch.s.github_app_enabled
    orch._verify_delivery = _remote_delivery(ws)
    rec: dict = {"builds": [], "finish": [], "prs": [], "state_checks": 0, "cards": [],
                 "posts": []}

    async def org_build(effort_id, *, on_branch=True):
        rec["builds"].append(on_branch)
        return build, "1 passed" if build == "pass" else "1 failed", 0
    orch._org_build_check = org_build

    async def machine_verified(effort_id):
        return True
    orch._machine_verified_effort = machine_verified

    async def nc_ok(effort_id, output):
        return no_changes_ok
    orch._no_changes_acceptable = nc_ok
    orig_finish = orch._finish_effort

    async def finish(effort_id, result, *, delivery=None):
        rec["finish"].append(delivery)
        return await orig_finish(effort_id, result, delivery=delivery)
    orch._finish_effort = finish

    async def open_pr(effort_id, repo, branch, *, merge_id=None, verified_sha="", body_extra=""):
        rec["prs"].append((branch, verified_sha))
        return ""
    orch._open_delivery_pr = open_pr
    orig_card = orch.router.update_effort_card

    async def card(effort_id, state, *a, **kw):
        rec["cards"].append(state)
        return await orig_card(effort_id, state, *a, **kw)
    orch.router.update_effort_card = card
    orig_post = orch.comms.post

    async def post(intent, text, *a, **kw):
        rec["posts"].append(str(text))
        return await orig_post(intent, text, *a, **kw)
    orch.comms.post = post
    orig_wake = harness.wake

    async def wake(base_url, session_id, prompt, **kw):
        r = await orig_wake(base_url, session_id, prompt, **kw)
        if "STATE CHECK" in prompt:
            rec["state_checks"] += 1
            if state_holds:
                r.output = f"STATE HOLDS: {CODING_FILE} is in place on the branch"
        elif exec_reply == "no-changes" and ("PUBLISH YOUR WORK" in prompt
                                             or "NOTHING NEW WAS DELIVERED" in prompt):
            r.output = "NO CHANGES: the repro test is already on the branch"
        return r
    harness.wake = wake
    return rec


def _assert_wip_not_delivered_nor_done(rec, wip):
    for d in rec["finish"]:
        assert not (d is not None and d.landed and d.head_sha == wip), (
            "_finish_effort was handed the carried WIP as a landed delivery", d)
    for branch, sha in rec["prs"]:
        assert sha != wip, ("a delivery PR was opened for the carried WIP commit", branch, sha)
    assert "done" not in rec["cards"], rec["cards"]
    assert not any("finished (**done**)" in p for p in rec["posts"]), rec["posts"][-3:]


@pytest.mark.parametrize("no_changes_ok", [True, False], ids=["acceptable", "needs-a-build"])
async def test_no_changes_on_a_wip_only_branch_is_held_not_delivered(db_url, tmp_path,
                                                                     no_changes_ok):
    """Route (a): the execution turn says NO CHANGES and `_no_changes_acceptable` holds (a goal
    with no check_cmd / REQUIRED VERIFICATION); `needs-a-build` is round 2's covered route."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, fresh_exec_on=W2)
    try:
        await _keep_worker_1_off_the_new_session(orch)
        rec = _wire_delivery(orch, harness, ws, tmp_path, no_changes_ok=no_changes_ok,
                             exec_reply="no-changes")
        eid = await _run_effort(orch)
        wip = (await orch._last_event_payload(eid, "stopped_turn_work_carried"))["commit"]
        assert _origin_branches(ws)[f"agent/{eid}"] == wip
        _assert_wip_not_delivered_nor_done(rec, wip)
        held = await orch._event_count(eid, "delivery_wip_only_held")
        stale = await orch._event_count(eid, "delivery_stale_head")
        assert held + stale >= 1, (held, stale)
        if no_changes_ok:
            assert held == 1 and "needs-attention" in rec["cards"], rec["cards"]
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


async def test_state_holds_on_a_wip_only_branch_is_never_asked(db_url, tmp_path):
    """Route (b): silent execution turn -> WIP-only stale path -> silent re-engage. The state
    check would read the carried WIP in its checkout and say STATE HOLDS (rigged here to), closing
    done on the WIP: on a WIP-only branch it is not asked, the effort escalates."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, fresh_exec_on=W2)
    try:
        await _keep_worker_1_off_the_new_session(orch)
        rec = _wire_delivery(orch, harness, ws, tmp_path, no_changes_ok=False,
                             exec_reply="silent", state_holds=True)
        eid = await _run_effort(orch)
        wip = (await orch._last_event_payload(eid, "stopped_turn_work_carried"))["commit"]
        _assert_wip_not_delivered_nor_done(rec, wip)
        assert rec["state_checks"] == 0, rec["state_checks"]
        assert await orch._event_count(eid, "effort_state_holds") == 0
        assert await orch._event_count(eid, "delivery_stale_head") == 1
        _no_denials(ws)
    finally:
        await _shutdown(orch, db)


@pytest.mark.parametrize("kind", ["landed", "no-changes"])
async def test_finish_effort_holds_a_wip_only_head_from_any_caller(db_url, tmp_path, kind):
    """The chokepoints themselves, as burn-down green / the host-context run / a state check would
    call them: a landed WIP-only delivery, or a no-op close while the remote branch is WIP-only,
    after a bridge restart (nothing in memory: the carried heads come from the audit log)."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, fresh_exec_on=W2)
    try:
        await _keep_worker_1_off_the_new_session(orch)
        eid = await _run_effort(orch)
        wip = (await orch._last_event_payload(eid, "stopped_turn_work_carried"))["commit"]
        rec = _wire_delivery(orch, harness, ws, tmp_path, no_changes_ok=True, exec_reply="silent")
        orch._carried_heads.clear()
        landed = await orch._verify_delivery(eid, "repo")
        assert landed.landed and landed.head_sha == wip
        delivery = landed if kind == "landed" else BranchDelivery(no_changes=True,
                                                                  branch=landed.branch)
        # (round 2: run 1 itself already ended held - the rig has no App, so its own closure
        # could not see the head and a WIP was carried)
        n0 = await orch._event_count(eid, "delivery_wip_only_held")
        await orch._finish_effort(eid, SimpleNamespace(status="done", output="all green"),
                                  delivery=delivery)
        assert rec["prs"] == [] and rec["cards"] == ["needs-attention"], rec
        assert await orch._event_count(eid, "delivery_wip_only_held") == n0 + 1
        assert any("did **not** open a PR" in p for p in rec["posts"])
    finally:
        await _shutdown(orch, db)


async def test_real_work_on_top_of_the_wip_is_still_delivered(db_url, tmp_path):
    """No regression: the publish step commits the run's own fix on top of the WIP -> a PR for the
    NEW head (tester t2's variant)."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path, fresh_exec_on=W2)
    try:
        await _keep_worker_1_off_the_new_session(orch)
        rec = _wire_delivery(orch, harness, ws, tmp_path, no_changes_ok=False,
                             exec_reply="silent")
        inner = harness.wake

        async def wake(base_url, session_id, prompt, **kw):
            r = await inner(base_url, session_id, prompt, **kw)
            d = ws.dirs.get(base_url)
            if trace["exec_url"] == base_url and not trace.get("fixed"):
                trace["fixed"] = True
                (d / "inference/llm-queue/src/queue.py").write_text("def run():\n    return 2\n")
            if "PUBLISH YOUR WORK" in prompt:
                _git(d, "add", "-A")
                _git(d, "commit", "-q", "-m", "fix the leak")
                _git(d, "push", "-q", "origin", "HEAD:refs/heads/agent/effort-ao-queue")
            return r
        harness.wake = wake
        eid = await _run_effort(orch)
        wip = (await orch._last_event_payload(eid, "stopped_turn_work_carried"))["commit"]
        head = _origin_branches(ws)[f"agent/{eid}"]
        assert head != wip and _git(ws.origin, "rev-parse", f"{head}~1").strip() == wip
        assert any(sha == head for _b, sha in rec["prs"]), rec["prs"]
        assert await orch._event_count(eid, "delivery_wip_only_held") == 0
    finally:
        await _shutdown(orch, db)


async def test_a_composition_never_bumps_the_engine_to_a_wip_only_head(db_url, tmp_path,
                                                                      monkeypatch):
    import app.orchestrator as om

    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        wip = "a" * 40
        bumps: list = []

        async def bump(*a, **kw):
            bumps.append((a, kw))
            return SimpleNamespace(ok=True, summary="")
        monkeypatch.setattr(om, "bump_submodule", bump)

        async def delegate(*a, **kw):
            return None
        orch.delegate = delegate

        async def repo(eid):
            return "https://github.com/c7-sandbox/sub"
        orch._effort_repo = repo

        async def verify(eid, r):
            return BranchDelivery(verifiable=True, exists=True, ahead=1, files_changed=1,
                                  head_sha=wip, branch=f"agent/{eid}", base="main")
        orch._verify_delivery = verify
        orch._carried_heads["e-comp"] = {wip}

        async def repo_for(slug):
            return "https://github.com/c7-sandbox/engine"
        orch.projects.repo_for = repo_for
        orch.github = SimpleNamespace(owner="x")
        posts: list[str] = []

        async def post(intent, text, *a, **kw):
            posts.append(str(text))
        orch.comms.post = post
        await orch._run_composition(
            "e-comp", "chan", "root", "goal", SimpleNamespace(target="sub"),
            SimpleNamespace(target="engine", path="vendor/sub"), None, "mgmt")
        assert bumps == [], bumps
        assert any("was **not** bumped" in p for p in posts), posts
    finally:
        await _shutdown(orch, db)


# == review R1/R2/R3 ===============================================================================
async def test_a_whitespace_only_stopped_turn_output_still_re_plans(db_url, tmp_path):
    """R1: the carry and the re-plan read the stopped turn's first output line; a loop-guard stop
    can carry a whitespace-only answer. Neither may raise - the re-plan must still happen."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("ai-stack", "https://github.com/c7-sandbox/ai-stack")
        eid, _chan, _root = await orch.router.open_effort("ws-only", project="ai-stack")
        res = SimpleNamespace(output="  \n \t\n", instance_id="worker-1", loop_trip=object(),
                              status="stopped")

        async def exec_check(*a, **kw):
            return 0, "", False                  # no framed reply: recorded as a failed carry
        orch.router.exec_check = exec_check
        assert await orch._carry_stopped_turn_work(eid, res, "repo") is False
        assert await orch._event_count(eid, "stopped_turn_carry_failed") == 1
        await orch._flail_replan(eid, res)
        assert await orch._event_count(eid, "flail_replanned") == 1
    finally:
        await _shutdown(orch, db)


async def test_the_wip_commit_carries_no_model_text(db_url, tmp_path):
    """R2: the stopped turn's output never goes into the commit pushed to the remote."""
    orch, harness, db, ws, trace = await _orch(db_url, tmp_path)
    try:
        await orch.projects.add("ai-stack", "https://github.com/c7-sandbox/ai-stack")
        eid, _chan, _root = await orch.router.open_effort("r2", project="ai-stack")
        marker = "SECRET-ISH-" + "ghp_" + "Z9" * 18
        res = SimpleNamespace(output=marker + "\nmore", instance_id="worker-1", loop_trip=None)
        sent: list[str] = []

        async def exec_check(effort_id, *, command, **kw):
            sent.append(command)
            return 0, "", False
        orch.router.exec_check = exec_check
        await orch._carry_stopped_turn_work(eid, res, "repo")
        src = base64.b64decode(sent[0].split("b64decode('")[1].split("'")[0]).decode()
        assert "flail guard stopped this coding turn" in src
        assert marker not in src and "ghp_" not in src
    finally:
        await _shutdown(orch, db)


def test_a_failed_commit_with_an_unknown_unpushed_count_is_reported_failed(tmp_path):
    """R3: `rev-list` failed (unpushed unknown) and `git commit` failed: nothing was committed
    and nothing known is unpushed - the carry must not push HEAD and call it ok."""
    from app.modules.replan_carry import carry_command, parse_carry

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / "n.py").write_text("x = 1\n")
    _code, out, _t = _run_python_check(
        d, carry_command("agent/e1", subject="wip", body="b", author=("w", "w@x.invalid")),
        env={"AO_SHIM_FAIL": "rev-list,commit"})
    rep = parse_carry(out)
    assert rep["status"] == "error" and "commit failed" in rep["why"], rep
    assert "agent/e1" not in _origin_branches(ws)


# == (2) earlier untracked work the plan turn staged is unstaged =================================
def _effort_branch_checkout(ws, d: Path, branch: str = "agent/e1") -> None:
    _git(d, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    _git(d, "checkout", "-q", "-b", branch)


def _sync(d: Path, branch: str = "agent/e1") -> None:
    """F33's delivery-branch sync, through the proxy (as the next wake runs it)."""
    for argv in (("fetch", "origin", branch), ("checkout", "-f", "-B", branch, "FETCH_HEAD")):
        p = _pgit(d, *argv)
        assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("plan", ["add-and-commit", "add-only"])
def test_earlier_untracked_files_the_plan_turn_staged_survive_the_next_sync(tmp_path, plan):
    from app.modules.plan_turn_guard import new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    _effort_branch_checkout(ws, d)
    late = [f"late/f_{i:03d}.txt" for i in range(40)]
    (d / "late").mkdir()
    for p in late:
        (d / p).write_text(p + "\n")
    (d / CODING_FILE).parent.mkdir(parents=True)
    (d / CODING_FILE).write_text(CODING_TEXT)
    (d / "pre_staged.py").write_text("staged before\n")
    _git(d, "add", "pre_staged.py")                         # staged BEFORE the turn: stays so
    tok = new_token()
    snap = _baseline(d, tok)
    # the plan turn
    (d / PLAN_SCRATCH).write_text("plan\n")
    (d / TRACKED).write_text(TRACKED_TEXT + "plan edit\n")
    _pgit(d, "add", "-A")
    if plan == "add-and-commit":
        _pgit(d, "commit", "-q", "-m", "plan turn")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok", rep
    st = _git(d, "status", "--porcelain", "-uall").splitlines()
    assert sorted(st) == sorted([f"?? {p}" for p in late] + [f"?? {CODING_FILE}",
                                                              "A  pre_staged.py"]), st
    assert not (d / PLAN_SCRATCH).exists() and (d / TRACKED).read_text() == TRACKED_TEXT
    _sync(d)
    for p in late:
        assert (d / p).read_text() == p + "\n", p
    assert (d / CODING_FILE).read_text() == CODING_TEXT
    assert rep["unstaged_count"] == len(late) + 1, rep["unstaged_count"]
    assert not _denials(d)


# == (3) ignore / attribute rules ===================================================================
def _ignore_repo(tmp_path, rules: str = "build/\n*.env\n"):
    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / ".gitignore").write_text(rules)
    (d / ".gitattributes").write_text("*.txt text\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rules")
    # the earlier (coding) turn: ignored build output and local config, one plain new file
    (d / "build").mkdir()
    (d / "build" / "out.bin").write_text("coding build\n")
    (d / "local.env").write_text("CODING_SETTING=1\n")
    (d / "cfg").mkdir()
    (d / "cfg" / "app.env").write_text("CFG=1\n")
    (d / "coding.py").write_text("c\n")
    return ws, d


_EARLIER_IGNORED = {"build/out.bin": "coding build\n", "local.env": "CODING_SETTING=1\n",
                    "cfg/app.env": "CFG=1\n"}


@pytest.mark.parametrize("how", ["rewrite", "delete", "nested-unignore", "rewrite-add-commit",
                                 "exclude-truncated"])
def test_a_plan_turn_that_changes_ignore_rules_does_not_delete_earlier_ignored_files(tmp_path,
                                                                                   how):
    from app.modules.plan_turn_guard import new_token

    ws, d = _ignore_repo(tmp_path)
    gd = Path(_git(d, "rev-parse", "--absolute-git-dir").strip())
    if how == "exclude-truncated":
        # the earlier file is ignored only through the git dir's own exclude file
        (d / "notes.local").write_text("mine\n")
        (gd / "info" / "exclude").write_text("*.local\n")
    tok = new_token()
    snap = _baseline(d, tok)
    (d / "PLAN.md").write_text("p\n")
    if how in ("rewrite", "rewrite-add-commit"):
        (d / ".gitignore").write_text("# rewritten by the plan turn\n")
    elif how == "delete":
        (d / ".gitignore").unlink()
    elif how == "nested-unignore":
        (d / "cfg" / ".gitignore").write_text("!*.env\n")
    elif how == "exclude-truncated":
        (gd / "info" / "exclude").write_text("")
    if how == "rewrite-add-commit":
        _pgit(d, "add", "-A")
        _pgit(d, "commit", "-q", "-m", "plan turn")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok", rep
    for p, text in _EARLIER_IGNORED.items():
        assert (d / p).read_text() == text, (p, rep["reverted"])
    if how == "exclude-truncated":
        assert (d / "notes.local").read_text() == "mine\n"
        assert (gd / "info" / "exclude").read_text() == "*.local\n"
    assert (d / ".gitignore").read_text() == "build/\n*.env\n"
    assert not (d / "cfg" / ".gitignore").exists()
    assert not (d / "PLAN.md").exists()
    assert (d / "coding.py").read_text() == "c\n"
    reverted = {r["path"] for r in rep["reverted"]}
    assert not reverted & set(_EARLIER_IGNORED), reverted
    # the tree is exactly the earlier turn's: one untracked file, nothing staged
    assert _git(d, "status", "--porcelain", "-uall").splitlines() == ["?? coding.py"]
    assert not _denials(d)


def test_an_exclude_rule_the_plan_turn_adds_no_longer_hides_its_files(tmp_path):
    """F-D1: the plan turn appends `*.plan` to .git/info/exclude so its own files vanish from
    `status`; the exclude file is restored first, so they are seen and removed."""
    from app.modules.plan_turn_guard import new_token

    ws, d = _ignore_repo(tmp_path)
    gd = Path(_git(d, "rev-parse", "--absolute-git-dir").strip())
    had = (gd / "info" / "exclude").read_text() if (gd / "info" / "exclude").exists() else None
    tok = new_token()
    snap = _baseline(d, tok)
    (gd / "info").mkdir(exist_ok=True)
    with open(gd / "info" / "exclude", "a") as fh:
        fh.write("*.plan\n")
    for i in range(3):
        (d / f"x{i}.plan").write_text("p")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok"
    assert not any((d / f"x{i}.plan").exists() for i in range(3))
    now = (gd / "info" / "exclude").read_text() if (gd / "info" / "exclude").exists() else None
    assert now == had
    assert "(.git/info/exclude)" in {r["path"] for r in rep["reverted"]}


def test_a_check_cut_off_later_has_already_restored_the_earlier_turns_files(tmp_path):
    """F-D2: the earlier turn's clobbered file comes back FIRST - before any `checkout` (which
    hangs here and the check is killed)."""
    from app.modules.plan_turn_guard import check_command, new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / "coding.py").write_text("coding\n")
    tok = new_token()
    snap = _baseline(d, tok)
    (d / "coding.py").write_text("plan clobber\n")
    (d / TRACKED).write_text(TRACKED_TEXT + "plan edit\n")
    (d / PLAN_SCRATCH).write_text("plan\n")
    _code, out, timed = _run_python_check(d, check_command(tok, snap), 8,
                                          {"AO_SHIM_HANG": "checkout", "AO_SHIM_HANG_SECS": "20"})
    assert timed
    assert (d / "coding.py").read_text() == "coding\n"


# == (4) the snapshot dir replaced by a file ========================================================
@pytest.mark.parametrize("what", ["file", "symlink"])
def test_a_snapshot_dir_replaced_by_a_file_does_not_crash_the_check(tmp_path, what):
    from app.modules.plan_turn_guard import new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    (d / "earlier.py").write_text("earlier\n")
    tok = new_token()
    snap = _baseline(d, tok)
    (d / "earlier.py").write_text("plan clobber\n")
    (d / PLAN_SCRATCH).write_text("plan\n")
    (d / TRACKED).write_text(TRACKED_TEXT + "plan edit\n")
    import shutil
    shutil.rmtree(_snapdir(d))
    if what == "file":
        _snapdir(d).write_text("x")
    else:
        try:
            _snapdir(d).symlink_to(d, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not permitted on this host")
    rep, _t = _check(d, tok, snap)
    assert rep is not None and rep["status"] == "ok", rep
    assert {r["path"] for r in rep["reverted"]} == {PLAN_SCRATCH, TRACKED}
    assert {u["path"] for u in rep["unrestored"]} == {"earlier.py"}   # its copy is gone
    assert not (d / PLAN_SCRATCH).exists() and (d / TRACKED).read_text() == TRACKED_TEXT
    assert not _snapdir(d).exists() and (d / "earlier.py").exists()


# == (5) a planted journal is never reported ========================================================
def test_a_journal_the_plan_turn_planted_is_not_reported(tmp_path):
    import json

    from app.modules.plan_turn_guard import new_token

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    tok = new_token()
    snap = _baseline(d, tok)
    (d / PLAN_SCRATCH).write_text("plan\n")
    # the turn reads the token from the on-disk state and plants a journal under every name it
    # could guess
    for name in (f"journal-{tok}.jsonl", "journal-.jsonl"):
        (_snapdir(d) / name).write_text(json.dumps({"k": "r", "path": "FAKE.txt",
                                                    "why": "forged"}) + "\n")
    rep, _t = _check(d, tok, snap)
    assert rep["status"] == "ok" and rep["resumed"] is False
    assert {r["path"] for r in rep["reverted"]} == {PLAN_SCRATCH}


def test_the_partial_report_reads_only_the_nonce_journal(tmp_path):
    import json

    from app.modules.plan_turn_guard import check_command, new_token, parse_check, report_command

    ws = _workspaces(tmp_path)
    ws.focus(W1, True)
    d = ws.dirs[W1]
    tok = new_token()
    snap = _baseline(d, tok)
    (d / PLAN_SCRATCH).write_text("plan\n")
    (d / TRACKED).write_text(TRACKED_TEXT + "plan edit\n")
    (_snapdir(d) / f"journal-{tok}.jsonl").write_text(
        json.dumps({"k": "r", "path": "FAKE.txt", "why": "forged"}) + "\n")
    nonce = new_token()
    hang = {"AO_SHIM_HANG": "checkout", "AO_SHIM_HANG_SECS": "20"}
    assert _run_python_check(d, check_command(tok, snap, nonce), 8, hang)[2]
    rep = parse_check(_run_python_check(d, report_command(tok, nonce))[1])
    assert rep["status"] == "partial"
    assert {r["path"] for r in rep["reverted"]} == {PLAN_SCRATCH}
