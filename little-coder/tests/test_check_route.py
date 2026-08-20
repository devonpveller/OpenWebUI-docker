"""Deterministic verification exec (`POST /check`, agent-org bridge 2026-07-08): run ONE command
in the focused workspace and return the REAL exit code + output — no model in the loop. Exists
because build verification is a machine step: an LLM 'verifier' burned its whole turn re-running
builds and never reported the verdict."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from littlecoder.daemon import CheckRequest, LittleCoderDaemon
from littlecoder.openterminal import ExecResult


class _FakeOT:
    def __init__(self, result: ExecResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def execute(self, command, cwd=None, env=None, timeout=None):
        self.calls.append((command, cwd, timeout))
        return self.result


def _daemon_with(result: ExecResult, *, focused=True) -> LittleCoderDaemon:
    d = object.__new__(LittleCoderDaemon)          # no full boot — unit-scope the seam
    d.ot = _FakeOT(result)
    d.current_focus = (SimpleNamespace(canonical_url="https://github.com/x/y")
                       if focused else None)
    d.workspace = SimpleNamespace(is_focused=lambda: focused)
    d.audit = SimpleNamespace(write=lambda *a, **k: None)
    return d


def test_check_returns_real_exit_code_and_output():
    d = _daemon_with(ExecResult("dotnet build", 1, "err CS0001: bad\n  138 Error(s)",
                                "warn: x", "done", "p1"))
    out = asyncio.run(d.run_check(CheckRequest(command="dotnet build X.sln", timeout=600)))
    assert out["ok"] is True and out["exit_code"] == 1 and out["timed_out"] is False
    assert "138 Error(s)" in out["output"] and "warn: x" in out["output"]
    assert d.ot.calls[0][0] == "dotnet build X.sln"
    assert d.ot.calls[0][2] == 600                  # timeout passed through (clamped range)


def test_check_pass_and_timeout_clamped():
    d = _daemon_with(ExecResult("make", 0, "all good", "", "done", "p2"))
    out = asyncio.run(d.run_check(CheckRequest(command="make", timeout=999999)))
    assert out["exit_code"] == 0
    assert d.ot.calls[0][2] == 1800                 # clamped to the ceiling


def test_check_requires_a_focused_workspace():
    from fastapi import HTTPException
    d = _daemon_with(ExecResult("make", 0, "", "", "done", "p3"), focused=False)
    with pytest.raises(HTTPException):
        asyncio.run(d.run_check(CheckRequest(command="make")))
