"""The daemon re-stores the focus's git credential before every task (cf-lc-token, 2026-09-28).

The token no longer rides in `.git/config` (a volume); it sits in the executor's credential store in
its container-local HOME, which a recreate of the executor empties. A task dispatched without a
fresh /project (OWUI, or after an executor restart) must still be able to push."""

from __future__ import annotations

from types import SimpleNamespace

from littlecoder.daemon import LittleCoderDaemon

FOCUS = SimpleNamespace(canonical_url="https://github.com/x/y")


def _daemon(focus_token, current_focus=FOCUS, raises=False):
    d = object.__new__(LittleCoderDaemon)
    d.current_focus = current_focus
    d._focus_token = focus_token
    calls = []

    def refresh(repo, token):
        calls.append((repo, token))
        if raises:
            raise RuntimeError("open-terminal down")

    d.workspace = SimpleNamespace(refresh_origin_auth=refresh)
    return d, calls


def test_reasserts_the_callers_token(monkeypatch):
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    d, calls = _daemon("req-tok")
    d._ensure_git_credentials()
    assert calls == [(FOCUS, "req-tok")]           # the per-request token wins over the env


def test_falls_back_to_the_deploy_token(monkeypatch):
    monkeypatch.setenv("LC_DEPLOY_TOKEN", "env-tok")
    d, calls = _daemon(None)
    d._ensure_git_credentials()
    assert calls == [(FOCUS, "env-tok")]


def test_no_token_or_no_focus_does_nothing(monkeypatch):
    monkeypatch.delenv("LC_DEPLOY_TOKEN", raising=False)
    d, calls = _daemon(None)
    d._ensure_git_credentials()
    d2, calls2 = _daemon("tok", current_focus=None)
    d2._ensure_git_credentials()
    assert calls == [] and calls2 == []


def test_an_unreachable_executor_does_not_crash_the_task(monkeypatch):
    d, calls = _daemon("tok", raises=True)
    d._ensure_git_credentials()                    # swallowed; the task reports its own push error
    assert len(calls) == 1
