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
