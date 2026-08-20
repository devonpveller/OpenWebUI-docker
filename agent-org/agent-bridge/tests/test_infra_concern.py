"""`_is_infra_concern` — the classifier that decides whether a FROZEN effort's concern is an
ENVIRONMENT/WORKSPACE symptom the org can self-heal (re-clone + retry) versus a real code/behaviour
deviation that must reach the Human Operator. Conservative by design (operator 2026-07-13: full
infra-autonomy, but a real problem must NEVER be masked by the autonomous recovery)."""

from __future__ import annotations

from app.orchestrator import _is_infra_concern


def test_the_live_incident_text_is_infra():
    # The exact 2026-07-13 freeze that idled the port ~5h — a corrupt/void workspace.
    incident = (
        "The workspace environment deviates from the agreed spec by containing only compiled "
        "artifacts instead of the required source code and properly initialized git submodules. "
        "This creates a hard blocker, making the core port task impossible to execute without "
        "operator intervention to reset the repository setup."
    )
    assert _is_infra_concern(incident) is True


def test_workspace_and_clone_symptoms_are_infra():
    assert _is_infra_concern("the workspace is empty — no clone on disk") is True
    assert _is_infra_concern("fatal: not a git repository; the clone is missing") is True
    assert _is_infra_concern("the vendored submodules are not initialized") is True
    assert _is_infra_concern("needs to reset the repository setup before it can build") is True


def test_real_code_deviations_stay_with_the_human():
    # Any of these = a genuine work-deviation → NOT auto-cleared, even if infra-ish words appear.
    assert _is_infra_concern("the worker removed the SaveGame feature to green the build") is False
    assert _is_infra_concern("the editor output does not match the agreed spec") is False
    assert _is_infra_concern(
        "it reverted the vendored-MonoGame wiring in the workspace back to the NuGet package"
    ) is False


def test_empty_and_plain_code_errors_are_not_infra():
    assert _is_infra_concern("") is False
    assert _is_infra_concern("Foo.cs(12,5): error CS1503: cannot convert int to string") is False
    assert _is_infra_concern("the feature works but the button colour is slightly off") is False
