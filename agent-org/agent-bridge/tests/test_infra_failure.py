"""Infra-vs-code check failures (operator 2026-07-10, the atlas effort): the org's check failed
on its OWN environment — a git-proxy DENIED the submodule branch fetch → MSB1009 project-not-found
— and the burn-down SPUN on it as if it were a code error (trajectory 1→1→1→1, stalled). You can't
fix a git-proxy denial or a missing tool by editing source. A red check that is the CHECK's own
infrastructure breaking is now a distinct verdict, surfaced honestly (never a burn-down, never the
worker's fault). Generic across toolchains."""

from __future__ import annotations

from app.orchestrator import _is_infra_failure, _is_transient_focus_collision


def test_git_proxy_denial_is_infra():
    assert _is_infra_failure("git-proxy: DENIED (blocklist:fetch-remote) — 'origin' not configured")


def test_missing_project_is_infra():
    assert _is_infra_failure("MSBUILD : error MSB1009: Project file does not exist.\nSwitch: X.sln")
    assert _is_infra_failure("Couldn't find a project to run. Ensure a project exists")


def test_msb3202_missing_referenced_project_is_infra_despite_its_locus():
    """LIVE 2026-07-12 (atlas composition): a vendored NESTED submodule wasn't populated, so the
    build hit MSB3202 with a `NuGet.targets(line): error MSB3202` locus that LOOKS like a source
    error — it must be classified INFRA (a workspace/focus problem), never burned down as code."""
    log = ("/usr/share/dotnet/sdk/8.0.422/NuGet.targets(465,5): error MSB3202: The project file "
           "\"/workspace/vendor/murder/bang/src/Bang/Bang.csproj\" was not found.\nBuild FAILED.")
    assert _is_infra_failure(log)


def test_other_msbuild_setup_errors_are_infra():
    assert _is_infra_failure("x.csproj(1,1): error MSB4019: The imported project was not found.")
    assert _is_infra_failure("error MSB4236: The SDK 'Microsoft.NET.Sdk' specified could not be found.")


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


# ── transient verify-focus collision (2026-07-11): worth ONE deterministic retry before the LLM ──
def test_clone_already_exists_is_a_transient_focus_collision():
    assert _is_transient_focus_collision(
        "verification focus failed: clone failed (exit 128): fatal: destination path "
        "'/workspace' already exists and is not an empty directory.")
    assert _is_transient_focus_collision(
        "clone failed: destination '/workspace' already exists and is not empty")


def test_persistent_or_unrelated_failures_are_not_transient():
    assert not _is_transient_focus_collision("verification focus failed: authentication failed")
    assert not _is_transient_focus_collision("MSB1009: project file does not exist")
    assert not _is_transient_focus_collision("no /check on this daemon")
    assert not _is_transient_focus_collision("")
