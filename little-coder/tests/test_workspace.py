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
        self.envs: list[dict | None] = []

    def execute(self, command, cwd=None, env=None, timeout=None):
        from littlecoder.openterminal import ExecResult

        self.calls.append((command, cwd))
        self.envs.append(env)
        return ExecResult(command, 0, "", "", "done", "p1")


def test_is_focused_reads_the_shared_volume(tmp_path):
    ws = WorkspaceManager(_FakeOT(), workspace_path=str(tmp_path))
    assert not ws.is_focused()
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    # a bare/partial `.git` (crashed clone: only submodule `modules/`, no HEAD) is NOT focused
    (gitdir / "modules").mkdir()
    assert not ws.is_focused()
    # a real repo has .git/HEAD
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    assert ws.is_focused()


def test_clone_runs_in_open_terminal_with_real_git():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET)
    cmd, _cwd = ot.calls[0]
    assert "/usr/bin/git" in cmd  # operator-bypass clone, not the proxy
    assert "clone" in cmd
    assert "github.com/acme/widget" in cmd


def test_clone_wipes_the_workspace_before_cloning():
    """A CLONE into the PERSISTENT /workspace mount must first clear any corrupt/partial leftovers,
    or `git clone` fails 'destination path already exists and is not an empty directory' (live
    2026-07-10: exit 128 wedged a composition focus for ~2h). The wipe must precede the clone."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET)
    cmd, _cwd = ot.calls[0]
    assert "-mindepth 1 -delete" in cmd                     # the workspace contents are wiped
    assert cmd.index("-delete") < cmd.index("clone")        # wipe happens BEFORE the clone


def _assert_token_only_in_env(ot, i, token):
    """cf-lc-token (2026-09-28): a token must never be spliced into a URL or the command string -
    it would land in .git/config inside the workspace VOLUME. It travels in the exec's env only."""
    cmd, _ = ot.calls[i]
    assert token not in cmd
    assert "x-access-token:" not in cmd                  # no userinfo in any URL
    assert ot.envs[i] == {"LC_GIT_TOKEN": token}


def test_clone_with_deploy_token_uses_credential_store_not_url():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.clone(WIDGET, deploy_token="tok-secret")
    cmd, _cwd = ot.calls[0]
    _assert_token_only_in_env(ot, 0, "tok-secret")
    assert "credential-store" in cmd and '"$LC_GIT_TOKEN"' in cmd
    assert " clone https://github.com/acme/widget /workspace;" in cmd   # the clone URL is token-free
    # the credential is stored BEFORE the clone that authenticates with it
    assert cmd.index("credential-store") < cmd.index(" clone ")
    # ...into the executor's HOME, never the workspace volume
    assert "$HOME/.lc-git-credentials" in cmd and "/workspace/.lc-git" not in cmd


def test_clone_without_token_sends_no_env_and_stores_nothing():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.clone(WIDGET)
    cmd, _ = ot.calls[0]
    assert ot.envs[0] is None and "credential-store" not in cmd


def test_clone_exit_code_survives_the_token_reauth_suffix():
    """2026-07-14 (the false-focus incident): with a deploy token, clone() appended the submodule
    origin re-bake as `; (... || true)` — making the SHELL'S exit code the reauth's uncondition-
    al 0, never the clone's. A clone failing 128 on a non-wipeable workspace reported ok=True in
    0.3s; the daemon claimed focus on a VOID tree and the bridge quarantine-looped both workers
    with an idle GPU. The clone's rc must be captured and re-raised as the command's exit, with
    every best-effort extra gated on it."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET, deploy_token="tok-x")
    cmd, _cwd = ot.calls[0]
    assert "rc=$?" in cmd                                   # the clone's exit code is captured...
    assert cmd.rstrip().endswith("exit $rc")                # ...and is the command's final word
    assert cmd.index("clone") < cmd.index("rc=$?")
    # the best-effort extras only run on a SUCCESSFUL clone
    assert cmd.count("if [ $rc -eq 0 ]") == 2               # submodule init + submodule credential


def test_clone_exit_code_is_honest_without_a_token_too():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET)
    cmd, _cwd = ot.calls[0]
    assert "rc=$?" in cmd and cmd.rstrip().endswith("exit $rc")


def test_deploy_token_rebakes_submodule_push_credential_on_task_focus():
    """2026-07-12: a composition fix is edited inside a vendored submodule and PUSHED to ITS own
    remote on a NORMAL task focus (non-recursive). The submodule origin must get the push token
    re-baked even when recurse=False — otherwise the worker's submodule push has no credential, it
    fails, and the host's gitlink points at an unreachable commit (live: the atlas fix landed on the
    engine but its murder branch couldn't push)."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET, deploy_token="tok-sub", recurse=False)     # a normal task focus
    cmd, _cwd = ot.calls[0]
    assert "submodule foreach --recursive" in cmd               # the submodule credential step runs
    assert "remote set-url origin" in cmd                       # (origin reset to the token-free URL)
    _assert_token_only_in_env(ot, 0, "tok-sub")


def test_no_submodule_rebake_without_a_token():
    """No deploy token → no origin re-bake (there is nothing to inject)."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.clone(WIDGET, recurse=False)
    cmd, _cwd = ot.calls[0]
    assert "submodule foreach" not in cmd


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
    _assert_token_only_in_env(ot, 0, "tok-x")
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


def test_add_upstream_remote_stores_token_for_private_parent():
    """A private parent needs a read-scoped token - stored for the UPSTREAM url (useHttpPath keys
    it apart from origin's), never spliced into the remote url."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_upstream_remote("https://github.com/acme/private-parent", token="ro-tok")
    cmd, _ = ot.calls[0]
    _assert_token_only_in_env(ot, 0, "ro-tok")
    assert "credential.useHttpPath true" in cmd
    assert "credential-store" in cmd and "acme/private-parent" in cmd


def test_add_upstream_remote_no_token_leaves_url_clean():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_upstream_remote("https://github.com/MonoGame/MonoGame")
    cmd, _ = ot.calls[0]
    assert "x-access-token" not in cmd                 # public parent → no credential baked
    assert ot.envs[0] is None and "credential-store" not in cmd


def test_refresh_origin_auth_rebakes_fresh_token():
    """LIVE regression ("expired token in origin"): the token given at clone time is short-lived;
    a NOOP re-focus / publish must re-store origin's credential with a CURRENT token, and reset
    origin to the token-free URL (which also cleans a pre-2026-09-28 clone on its next re-focus)."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.refresh_origin_auth(WIDGET, "fresh-tok")
    cmd, _ = ot.calls[0]
    assert "/usr/bin/git" in cmd and "remote set-url origin https://github.com/acme/widget" in cmd
    assert "credential-store" in cmd
    _assert_token_only_in_env(ot, 0, "fresh-tok")
    # no token → resets origin to the clean URL (no stale credential left behind)
    ws.refresh_origin_auth(WIDGET, None)
    cmd2, _ = ot.calls[1]
    assert "remote set-url origin" in cmd2 and "x-access-token" not in cmd2
    assert ot.envs[1] is None


def test_refresh_origin_auth_also_rebakes_submodule_push_credential():
    """2026-07-12: a NOOP re-focus (persistent workspace, no re-clone) never re-runs clone()'s
    submodule reauth, so a vendored submodule's `origin` kept its token-less `.gitmodules` URL and the
    worker's `git -C <sub> push` had no credential → the engine's gitlink pointed at an unreachable
    commit (live: the murder cursor commit 5b138c12 couldn't push, breaking the composition). Symmetric
    with clone(): refresh_origin_auth must re-bake the token into submodule origins too."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.refresh_origin_auth(WIDGET, "fresh-tok")
    cmd, _ = ot.calls[0]
    assert "submodule foreach --recursive" in cmd               # submodule credentials too
    _assert_token_only_in_env(ot, 0, "fresh-tok")
    # no token → no submodule re-bake (nothing to inject)
    ws.refresh_origin_auth(WIDGET, None)
    cmd2, _ = ot.calls[1]
    assert "submodule foreach" not in cmd2


# --- submodule composition (P-APL.1b) -------------------------------------


def test_add_submodule_uses_real_git_adds_commits_pushes():
    """A submodule is an operator-plane action (the git-proxy hard-denies `submodule` to the worker):
    real git, `submodule add` + commit + push, at the given path."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.add_submodule("https://github.com/demoowner/murder", "murder")
    cmd, cwd = ot.calls[0]
    assert "/usr/bin/git" in cmd                        # real git, not the proxy
    assert "submodule add" in cmd and "murder" in cmd
    assert "commit -m" in cmd and "push -u origin HEAD" in cmd
    assert cwd == "/workspace"


def test_add_submodule_public_fork_leaves_url_clean():
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_submodule("https://github.com/demoowner/murder", "murder")
    cmd, _ = ot.calls[0]
    assert "x-access-token" not in cmd                  # public submodule → no token at rest in .gitmodules


def test_add_submodule_private_stores_token_not_in_gitmodules():
    """Before 2026-09-28 the read token was spliced into the submodule url - which lands in the
    TRACKED .gitmodules and was committed + pushed. Now the url is clean and the token is stored."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_submodule("https://github.com/acme/private-lib", "libs/priv", token="ro-tok")
    cmd, _ = ot.calls[0]
    _assert_token_only_in_env(ot, 0, "ro-tok")
    assert "submodule add https://github.com/acme/private-lib libs/priv" in cmd
    assert cmd.index("credential-store") < cmd.index("submodule add")


def test_add_submodule_is_idempotent():
    """Re-adding an already-present submodule (a repeated/partial compose) must SKIP, not fail
    'already exists' — so re-running a plan adds only what's missing."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace")
    ws.add_submodule("https://github.com/demoowner/murder", "murder")
    cmd, _ = ot.calls[0]
    assert "submodule status" in cmd and "already present" in cmd   # guarded skip
    assert "submodule add" in cmd                                    # still adds when absent


def test_submodule_added_is_a_known_audit_event():
    """Regression for the live 500: the daemon audit-writes 'submodule_added' after a successful add;
    it MUST be a registered event or the write throws and fakes a failure AFTER a real push."""
    from littlecoder.audit import KNOWN_EVENTS
    assert "submodule_added" in KNOWN_EVENTS


def test_refresh_if_missing_only_fills_an_empty_store():
    """cf-lc-token N5: for a focus seeded after a daemon restart, the re-store is conditional on
    the store holding nothing for origin's URL; origin is still reset to the clean URL."""
    ot = _FakeOT()
    ws = WorkspaceManager(ot, workspace_path="/workspace", real_git="/usr/bin/git")
    ws.refresh_origin_auth(WIDGET, "env-tok", if_missing=True)
    cmd, _ = ot.calls[0]
    _assert_token_only_in_env(ot, 0, "env-tok")
    assert "remote set-url origin https://github.com/acme/widget" in cmd
    assert "if ! printf" in cmd and "credential-store" in cmd and " get " in cmd
    assert cmd.index(" get ") < cmd.index(" store)")
