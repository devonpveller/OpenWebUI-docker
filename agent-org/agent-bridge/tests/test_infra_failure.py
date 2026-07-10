"""Infra-vs-code check failures (operator 2026-07-10, the atlas effort): the org's check failed
on its OWN environment — a git-proxy DENIED the submodule branch fetch → MSB1009 project-not-found
— and the burn-down SPUN on it as if it were a code error (trajectory 1→1→1→1, stalled). You can't
fix a git-proxy denial or a missing tool by editing source. A red check that is the CHECK's own
infrastructure breaking is now a distinct verdict, surfaced honestly (never a burn-down, never the
worker's fault). Generic across toolchains."""

from __future__ import annotations

from app.orchestrator import _is_infra_failure


def test_git_proxy_denial_is_infra():
    assert _is_infra_failure("git-proxy: DENIED (blocklist:fetch-remote) — 'origin' not configured")


def test_missing_project_is_infra():
    assert _is_infra_failure("MSBUILD : error MSB1009: Project file does not exist.\nSwitch: X.sln")
    assert _is_infra_failure("Couldn't find a project to run. Ensure a project exists")


def test_tool_and_path_errors_are_infra():
    assert _is_infra_failure("bash: mgfxc: command not found")
    assert _is_infra_failure("cat: /workspace/x: No such file or directory")
    assert _is_infra_failure("fatal: could not read Username; authentication failed")
    assert _is_infra_failure("Permission denied")


def test_real_compiler_errors_are_NOT_infra():
    # a genuine build error present → it IS the code, even if some noise looks infra-ish
    log = ("Game.cs(1,2): error CS1503: cannot convert\n    3 Error(s)\n"
           "some No such file or directory noise")
    assert not _is_infra_failure(log)
    assert not _is_infra_failure("src/A.cs(9,9): error CS0117: no member\nBuild FAILED")


def test_clean_output_is_not_infra():
    assert not _is_infra_failure("Build succeeded.\n0 Error(s)")
    assert not _is_infra_failure("")
