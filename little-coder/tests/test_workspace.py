"""Project focus — the /project decision table (design §12.3)."""

from littlecoder.urlnorm import normalize_repo_url
from littlecoder.workspace import SwitchAction, WorkspaceManager, decide_switch

WIDGET = normalize_repo_url("https://github.com/acme/widget")
WIDGET_SSH = normalize_repo_url("git@github.com:acme/widget.git")
GADGET = normalize_repo_url("https://github.com/acme/gadget")


def test_no_current_focus_clones():
    d = decide_switch(WIDGET, current=None, task_in_flight=False)
    assert d.action is SwitchAction.CLONE


def test_same_repo_is_noop_even_across_url_forms():
    d = decide_switch(WIDGET_SSH, current=WIDGET, task_in_flight=False)
    assert d.action is SwitchAction.NOOP


def test_different_repo_with_task_in_flight_is_rejected():
    d = decide_switch(GADGET, current=WIDGET, task_in_flight=True)
    assert d.action is SwitchAction.REJECT


def test_different_repo_when_clear_switches():
    d = decide_switch(GADGET, current=WIDGET, task_in_flight=False)
    assert d.action is SwitchAction.SWITCH


# --- WorkspaceManager filesystem + open-terminal routing ------------------


class _FakeOT:
    """Records commands and returns a canned ExecResult."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def execute(self, command, cwd=None, env=None, timeout=None):
        from littlecoder.openterminal import ExecResult

        self.calls.append((command, cwd))
        return ExecResult(command, 0, "", "", "done", "p1")


def test_is_focused_reads_the_shared_volume(tmp_path):
    ws = WorkspaceManager(_FakeOT(), workspace_path=str(tmp_path))
    assert not ws.is_focused()
    (tmp_path / ".git").mkdir()
    assert ws.is_focused()


def test_clone_runs_in_open_terminal_with_real_git():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET)
    cmd, _cwd = ot.calls[0]
    assert "/usr/bin/git" in cmd  # operator-bypass clone, not the proxy
    assert "clone" in cmd
    assert "github.com/acme/widget" in cmd


def test_clone_with_deploy_token_injects_https_credential():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.clone(WIDGET, deploy_token="tok-secret")
    cmd, _cwd = ot.calls[0]
    assert "x-access-token:tok-secret@" in cmd


def test_wipe_keeps_the_mount_point():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.wipe()
    cmd, _cwd = ot.calls[0]
    assert "-mindepth 1" in cmd  # contents removed, mount kept


# --- clone is FULL history (no --depth 1) so branches are visible -------


def test_clone_is_not_shallow_so_all_branches_arrive():
    """The shallow `--depth 1` single-branch fetch was hiding every
    branch other than the default. Full clone fixes that — operator
    can `git checkout <other-branch>` inside the workspace and the
    agent can `git log` past commit 1."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.clone(WIDGET)
    cmd, _ = ot.calls[0]
    assert "--depth" not in cmd
    assert "--single-branch" not in cmd


def test_clone_passes_branch_flag_when_url_carried_one():
    """The `#<branch>` fragment from the link surfaces as `-b <branch>`
    on `git clone`. shlex-quoted so branch names with special chars
    can't break out into a separate shell token."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    repo = normalize_repo_url("https://github.com/acme/widget#feature/auth")
    ws.clone(repo)
    cmd, _ = ot.calls[0]
    assert " -b 'feature/auth' " in cmd or " -b feature/auth " in cmd


def test_clone_omits_branch_flag_when_no_branch():
    """No `#<branch>` → no `-b` flag, so the remote's default branch
    is checked out (HEAD)."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.clone(WIDGET)
    cmd, _ = ot.calls[0]
    assert " -b " not in cmd


def test_clone_with_token_and_branch_both_apply():
    """Token + branch shouldn't interfere. Both must land on the
    same clone command."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    repo = normalize_repo_url("https://github.com/acme/widget#dev")
    ws.clone(repo, deploy_token="tok-x")
    cmd, _ = ot.calls[0]
    assert "x-access-token:tok-x@" in cmd
    assert " -b 'dev' " in cmd or " -b dev " in cmd


# --- fork/upstream onboarding (D0.f) --------------------------------------


def test_add_upstream_remote_uses_real_git_and_fences_push():
    """The fork parent is baked with the REAL git binary (operator-bypass, like clone —
    the proxy blocks `remote add`), idempotently, with the push side fenced so the worker
    can never push to the parent."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.add_upstream_remote("https://github.com/MonoGame/MonoGame")
    cmd, cwd = ot.calls[0]
    assert "/usr/bin/git" in cmd                       # real git, not the proxy
    assert "remote add upstream" in cmd
    assert "https://github.com/MonoGame/MonoGame" in cmd
    assert "remote set-url upstream" in cmd            # idempotent fallback if it already exists
    assert "set-url --push upstream" in cmd            # push fenced to a no-op
    assert "DISABLED" in cmd
    assert cwd == "/workspace"


def test_add_upstream_remote_injects_token_for_private_parent():
    """A private parent needs a read-scoped token, injected into the FETCH url like clone."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_upstream_remote("https://github.com/acme/private-parent", token="ro-tok")
    cmd, _ = ot.calls[0]
    assert "x-access-token:ro-tok@" in cmd


def test_add_upstream_remote_no_token_leaves_url_clean():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_upstream_remote("https://github.com/MonoGame/MonoGame")
    cmd, _ = ot.calls[0]
    assert "x-access-token" not in cmd                 # public parent → no credential baked


def test_refresh_origin_auth_rebakes_fresh_token():
    """LIVE regression ("expired token in origin"): the token embedded at clone time is short-lived;
    a NOOP re-focus / publish must re-bake origin's URL with a CURRENT token via real git set-url."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.refresh_origin_auth(WIDGET, "fresh-tok")
    cmd, _ = ot.calls[0]
    assert "/usr/bin/git" in cmd and "remote set-url origin" in cmd
    assert "x-access-token:fresh-tok@" in cmd
    # no token → resets origin to the clean URL (no stale credential left behind)
    ws.refresh_origin_auth(WIDGET, None)
    cmd2, _ = ot.calls[1]
    assert "remote set-url origin" in cmd2 and "x-access-token" not in cmd2


# --- submodule composition (P-APL.1b) -------------------------------------


def test_add_submodule_uses_real_git_adds_commits_pushes():
    """A submodule is an operator-plane action (the git-proxy hard-denies `submodule` to the worker):
    real git, `submodule add` + commit + push, at the given path."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.add_submodule("https://github.com/devonpveller/murder", "murder")
    cmd, cwd = ot.calls[0]
    assert "/usr/bin/git" in cmd                        # real git, not the proxy
    assert "submodule add" in cmd and "murder" in cmd
    assert "commit -m" in cmd and "push -u origin HEAD" in cmd
    assert cwd == "/workspace"


def test_add_submodule_public_fork_leaves_url_clean():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_submodule("https://github.com/devonpveller/murder", "murder")
    cmd, _ = ot.calls[0]
    assert "x-access-token" not in cmd                  # public submodule → no token at rest in .gitmodules


def test_add_submodule_private_injects_token():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_submodule("https://github.com/acme/private-lib", "libs/priv", token="ro-tok")
    cmd, _ = ot.calls[0]
    assert "x-access-token:ro-tok@" in cmd              # private submodule needs a read token


def test_add_submodule_is_idempotent():
    """Re-adding an already-present submodule (a repeated/partial compose) must SKIP, not fail
    'already exists' — so re-running a plan adds only what's missing."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_submodule("https://github.com/devonpveller/murder", "murder")
    cmd, _ = ot.calls[0]
    assert "submodule status" in cmd and "already present" in cmd   # guarded skip
    assert "submodule add" in cmd                                    # still adds when absent


def test_submodule_added_is_a_known_audit_event():
    """Regression for the live 500: the daemon audit-writes 'submodule_added' after a successful add;
    it MUST be a registered event or the write throws and fakes a failure AFTER a real push."""
    from littlecoder.audit import KNOWN_EVENTS
    assert "submodule_added" in KNOWN_EVENTS
