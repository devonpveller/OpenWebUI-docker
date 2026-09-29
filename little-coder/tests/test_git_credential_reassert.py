"""The daemon re-stores the focus's git credential before every task (cf-lc-token, 2026-09-28).

The token no longer rides in `.git/config` (a volume); it sits in the executor's credential store in
its container-local HOME, which a recreate of the executor empties. A task dispatched without a
fresh /project (OWUI, or after an executor restart) must still be able to push - with the token the
CALLER focused with, not silently the env PAT.

The first block unit-tests `_ensure_git_credentials`. The second drives the REAL `_run_task` and the
REAL `/project` clone/switch/noop paths with a fake executor whose credential store is a dict, so
deleting the call in `_run_task` or the `_focus_token = req.token` lines turns a test red (attempt-1
mutants MD1/MD2 survived the suite before these existed)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from littlecoder.daemon import LittleCoderDaemon, ProjectRequest
from littlecoder.config import Config
from littlecoder.openterminal import ExecResult, OpenTerminalError
from littlecoder.tasks import TaskContext, TaskState
from littlecoder.urlnorm import normalize_repo_url

FOCUS = SimpleNamespace(canonical_url="https://github.com/x/y")


# --- _ensure_git_credentials, unit ------------------------------------------------------------

def _daemon(focus_token, current_focus=FOCUS, raises=False, from_project=True):
    d = object.__new__(LittleCoderDaemon)
    d.current_focus = current_focus
    d._focus_token = focus_token
    d._focus_from_project = from_project
    calls = []

    def refresh(repo, token, *, if_missing=False):
        calls.append((repo, token, if_missing))
        if raises:
            raise OpenTerminalError("open-terminal down")

    d.workspace = SimpleNamespace(refresh_origin_auth=refresh)
    return d, calls


def test_reasserts_the_callers_token(monkeypatch):
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    d, calls = _daemon("req-tok")
    d._ensure_git_credentials()
    assert calls == [(FOCUS, "req-tok", False)]    # the per-request token wins, and overwrites


def test_falls_back_to_the_deploy_token(monkeypatch):
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    d, calls = _daemon(None)
    d._ensure_git_credentials()
    assert calls == [(FOCUS, "env-tok", False)]


def test_a_seeded_focus_only_fills_an_empty_store(monkeypatch):
    """N5: after a little-coder restart the focus is seeded from disk and the caller's token is
    unknown. The env PAT may fill an EMPTY store, never overwrite the last /project's token."""
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    d, calls = _daemon(None, from_project=False)
    d._ensure_git_credentials()
    assert calls == [(FOCUS, "env-tok", True)]


def test_no_token_or_no_focus_does_nothing(monkeypatch):
    monkeypatch.delenv("LC_DEPLOY_TOKEN", raising=False)
    d, calls = _daemon(None)
    d._ensure_git_credentials()
    d2, calls2 = _daemon("tok", current_focus=None)
    d2._ensure_git_credentials()
    assert calls == [] and calls2 == []


def test_a_programming_error_in_the_restore_is_not_swallowed(monkeypatch):
    """Only an executor outage is swallowed; a bug (e.g. a missing attribute) must surface."""
    d, calls = _daemon("tok")
    del d._focus_from_project
    with pytest.raises(AttributeError):
        d._ensure_git_credentials()


def test_an_unreachable_executor_does_not_crash_the_task(monkeypatch):
    d, calls = _daemon("tok", raises=True)
    d._ensure_git_credentials()                    # swallowed; the task reports its own push error
    assert len(calls) == 1


# --- the real daemon paths, against a fake executor ------------------------------------------

class FakeExecutor:
    """The executor's credential store as a dict {repo url: token}. `recreate()` empties it, as a
    recreate of open-terminal empties /home/user."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def recreate(self):
        self.store.clear()


class FakeWorkspace:
    """WorkspaceManager's contract as the daemon uses it, writing into FakeExecutor.store with the
    same semantics as the shell (a plain re-store replaces; `if_missing` only fills a gap)."""

    def __init__(self, ex: FakeExecutor):
        self.ex = ex

    def is_focused(self):
        return True

    def wipe(self):
        return ExecResult("wipe", 0, "", "", "done", "p")

    def tag_prior_state(self, label):
        return ExecResult("tag", 0, "", "", "done", "p")

    def clone(self, repo, token=None, recurse=False):
        if token:
            self.ex.store[repo.canonical_url] = token
        return ExecResult("clone", 0, "", "", "done", "p")

    def refresh_origin_auth(self, repo, token, *, if_missing=False):
        if token and not (if_missing and repo.canonical_url in self.ex.store):
            self.ex.store[repo.canonical_url] = token
        return ExecResult("refresh", 0, "", "", "done", "p")


class _Sink:
    def write(self, *a, **k):
        pass


def _real_daemon(tmp_path, ex):
    d = object.__new__(LittleCoderDaemon)
    d.cfg = SimpleNamespace(workspace=SimpleNamespace(path=str(tmp_path)),
                            tasks=SimpleNamespace(abandoned_timeout_seconds={}))
    d.workspace = FakeWorkspace(ex)
    d.audit = _Sink()
    d.journals = _Sink()
    d.current_focus = None
    d._focus_token = None
    d._focus_from_project = False
    d.in_flight = None
    d.queue = asyncio.Queue()
    d.tasks, d.contexts = {}, {}

    async def no_meta():
        return None

    d._maybe_trigger_meta = no_meta
    return d


def _run_one_task(d, ex):
    """Drive the REAL _run_task; the fake agent records what the executor's store held when the
    agent started - i.e. what the worker's `git push` would authenticate with."""
    seen = {}

    def run_task(ctx, timeout):
        seen["store"] = dict(ex.store)
        return SimpleNamespace(outcome="unverified", signal=None, commands_run=0)

    d.agent = SimpleNamespace(run_task=run_task)
    st = TaskState(task_id="t1", session_id="s1", channel="cli", user_id="u", prompt="p",
                   repo=d.current_focus.canonical_url)
    d.tasks["t1"] = st
    d.contexts["t1"] = TaskContext(st)
    asyncio.run(d._run_task(st))
    return seen["store"]


WIDGET = "https://github.com/acme/widget"
GADGET = "https://github.com/acme/gadget"


@pytest.mark.parametrize("path", ["clone", "switch", "noop"])
def test_project_keeps_the_callers_token_for_later_tasks(tmp_path, monkeypatch, path):
    """MD2: every /project path records the caller's token; a later task's re-store uses THAT
    token, not LC_DEPLOY_TOKEN. Without `_focus_token = req.token` the store would be overwritten
    with env-tok before the worker pushes."""
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    ex = FakeExecutor()
    d = _real_daemon(tmp_path, ex)
    if path in ("switch", "noop"):
        d.current_focus = normalize_repo_url(GADGET if path == "switch" else WIDGET)
    asyncio.run(d.switch_project(ProjectRequest(repo=WIDGET, token="req-tok")))
    assert d.current_focus.canonical_url == WIDGET
    assert _run_one_task(d, ex) == {WIDGET: "req-tok"}
    # MD5/MD7: after a /project the CALLER's token wins over whatever the store holds for origin
    # (reachable e.g. when a fork's upstream URL equals its origin URL and stored its own token):
    # a /project focus re-stores unconditionally, only a disk-seeded one is if_missing.
    ex.store[WIDGET] = "other-tok"
    assert _run_one_task(d, ex) == {WIDGET: "req-tok"}


def test_run_task_restores_the_credential_after_an_executor_recreate(tmp_path, monkeypatch):
    """MD1: the executor is recreated between the /project and the task (its store is empty). The
    real _run_task must re-store the focus's token BEFORE the agent runs."""
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    ex = FakeExecutor()
    d = _real_daemon(tmp_path, ex)
    asyncio.run(d.switch_project(ProjectRequest(repo=WIDGET, token="req-tok")))
    ex.recreate()
    assert _run_one_task(d, ex) == {WIDGET: "req-tok"}


def test_a_seeded_focus_keeps_the_stored_app_token(tmp_path, monkeypatch):
    """N5: little-coder restarted (focus seeded from disk, caller token unknown) but the executor
    did not: the store still holds the caller's token and a task must NOT replace it with the env
    PAT. If the executor was recreated too, the env PAT fills the empty store."""
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    ex = FakeExecutor()
    ex.store[WIDGET] = "app-tok"
    d = _real_daemon(tmp_path, ex)
    d.current_focus = normalize_repo_url(WIDGET)          # what _seed_focus does
    assert _run_one_task(d, ex) == {WIDGET: "app-tok"}
    ex.recreate()
    assert _run_one_task(d, ex) == {WIDGET: "env-tok"}


# --- through the REAL constructor and _seed_focus (a little-coder restart) --------------------

class _SeedOT:
    """Answers _seed_focus's `git config --get remote.origin.url` with the clone on disk."""

    def execute(self, command, cwd=None, env=None, timeout=None):
        return ExecResult(command, 0, WIDGET + "\n", "", "done", "p")


def _restarted_daemon(tmp_path, ex):
    """LittleCoderDaemon built by its real __init__ (so every default is the production one), then
    focus restored from disk by the real _seed_focus. Only the executor-facing collaborators are
    swapped for fakes; the fake agent never runs a process."""
    cfg = Config()
    cfg.journals.dir = str(tmp_path / "journals")
    cfg.workspace.path = str(tmp_path / "ws")
    cfg.workspace.open_terminal_url = "http://127.0.0.1:1"
    d = LittleCoderDaemon(cfg)
    d.ot = _SeedOT()
    d.workspace = FakeWorkspace(ex)
    d._seed_focus()
    assert d.current_focus is not None and d.current_focus.canonical_url == WIDGET

    async def no_meta():
        return None

    d._maybe_trigger_meta = no_meta
    return d


def test_after_a_restart_the_callers_stored_token_survives_and_an_empty_store_is_filled(
        tmp_path, monkeypatch):
    """MD8/MD9: the constructor's `_focus_from_project = False` is what marks a disk-seeded focus.
    A caller token already in the executor's store (e.g. agent-bridge's App token) must survive the
    next task; after an executor recreate the env token fills the empty store."""
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    ex = FakeExecutor()
    ex.store[WIDGET] = "app-tok"
    d = _restarted_daemon(tmp_path, ex)
    assert _run_one_task(d, ex) == {WIDGET: "app-tok"}
    assert not d.tasks["t1"].detail.startswith("daemon error")
    ex.recreate()
    assert _run_one_task(d, ex) == {WIDGET: "env-tok"}
    assert not d.tasks["t1"].detail.startswith("daemon error")
