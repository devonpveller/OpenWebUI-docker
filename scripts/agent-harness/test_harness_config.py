"""Tests for the harness configuration - including that both readers agree.

The cross-language test is the point of this file. Two readers of one config file is a
standing invitation to drift: someone edits a default in `config.ps1`, the bridge keeps
serving the old one, and nothing complains until an agent runs on a model nobody chose.
So the last test asks PowerShell the same questions and compares answers.

    python -m pytest scripts/agent-harness/test_harness_config.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AI_STACK_HARNESS_CONFIG", "AI_STACK_HARNESS_ENABLED", "AI_STACK_HARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    config.load(fresh=True)
    yield
    config.load(fresh=True)


def test_defaults_survive_a_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(tmp_path / "nope.json"))
    config.load(fresh=True)
    # A deleted config must degrade to the documented default, not crash the toolkit.
    assert config.get("default_profile") == "all-cloud"
    assert config.resolve_role("worker")["model"] == "opus"


def test_the_shipped_config_is_all_cloud_by_default():
    for role in config.ROLES:
        t = config.resolve_role(role)
        assert t["runner"] == "claude-code"
        assert t["model"] == "opus"


def test_extension_is_locked_to_all_cloud():
    # Operator decision 2026-08-28: the interactive surface never silently degrades.
    assert config.is_profile_locked("extension")
    t = config.resolve_role("worker", profile="all-local", surface="extension")
    assert t["runner"] == "claude-code"


def test_mattermost_honours_a_requested_profile():
    assert not config.is_profile_locked("mattermost")
    t = config.resolve_role("worker", profile="all-local", surface="mattermost")
    assert t["runner"] == "little-coder"
    r = config.resolve_role("reviewer", profile="local-work-cloud-review", surface="mattermost")
    assert r["runner"] == "claude-code"


def test_an_unknown_profile_is_loud():
    # A typo in a `profile:` directive must be visible, never served by the default.
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_role("worker", profile="all-cloudd", surface="mattermost")
    assert "all-cloudd" in str(e.value)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("AI_STACK_HARNESS_ENABLED", "0")
    config.load(fresh=True)
    assert not config.is_enabled()
    assert "disabled" in config.disabled_reason()


def test_a_surface_can_be_turned_off_alone(monkeypatch, tmp_path):
    cfg = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))
    cfg["surfaces"]["mattermost"]["enabled"] = False
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(p))
    config.load(fresh=True)
    assert config.is_enabled("extension")
    assert not config.is_enabled("mattermost")


def test_lists_replace_rather_than_extend(monkeypatch, tmp_path):
    # Narrowing worktree.env_files is how a plane's secrets are kept out of an agent tree.
    # If lists concatenated, narrowing would be impossible and the setting would lie.
    cfg = {"worktree": {"env_files": [".env"]}}
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(p))
    config.load(fresh=True)
    assert config.get("worktree.env_files") == [".env"]


def test_unknown_role_is_rejected():
    with pytest.raises(config.HarnessConfigError):
        config.resolve_role("architect")


# ── the runner registry (dark-factory-unification U4) ────────────────────────
# These pin the SHARED half of U4: `runners` is read by this harness AND by agent-org's
# bridge (agent-org/agent-bridge/app/modules/runners.py, whose own suite reads this same
# file). Their PROFILE tables were deliberately left separate - see
# documentation/notes/u4bidir-findings.md.


def test_every_declared_runner_states_its_kind_status_and_reachability():
    """A runner row is a claim about a substrate. `reachable_from` is the part that was
    silently FALSE for as long as nothing dispatched: the shipped little-coder endpoint was
    http://127.0.0.1:8090 while the coder plane publishes only the metrics port, so the
    address was refused by the host every time. Declaring reachability makes the claim
    checkable (check-runner-endpoints.ps1); requiring it here stops the next row from
    omitting it."""
    names = config.runner_names()
    assert names, "the registry must declare at least one runner"
    for name in names:
        r = config.runner(name)
        assert r["kind"], f"runner '{name}' has no kind"
        assert r["status"] in ("proven", "unproven", "unknown"), r["status"]
        assert r["reachable_from"], f"runner '{name}' does not say where it is reachable from"


def test_an_unknown_runner_is_loud():
    with pytest.raises(config.HarnessConfigError) as e:
        config.runner("little-codr")
    assert "little-codr" in str(e.value)


def test_the_pool_carries_agent_orgs_workers():
    """The 'agent-org workers as harness runners' half of U4: the ao-worker pool is
    declared HERE, in the file both systems read, not only in agent-org's .env."""
    pool = config.runner_pool()
    urls = [p["url"] for p in pool]
    assert "http://ao-worker-1:8090" in urls and "http://ao-worker-2:8090" in urls
    ao = [p for p in pool if p["runner"] == "agent-org-worker"]
    assert {p["kind"] for p in ao} == {"little-coder"}


def test_claude_code_contributes_no_pool_address():
    """Not an oversight - the honest statement of the OTHER half of U4. A Claude Code agent
    is a host process with no task endpoint, so there is nothing for agent-org's scheduler
    to address; `RunnerDispatch` raises RunnerNotProvisioned rather than pretending."""
    assert config.runner("claude-code")["endpoint"] == ""
    assert not config.runner("claude-code")["instances"]
    assert "claude-code" not in {p["runner"] for p in config.runner_pool()}


def test_the_harness_side_of_u4_is_declared_not_dispatching():
    """The PARK, made mechanical - and the check that ends it when someone ends it.

    U4 has two directions and they are not equally true. agent-org's direction DISPATCHES:
    `RunnerDispatch` sits on the live wake path and the implementation that runs changes
    when the registry's answer changes (agent-org/agent-bridge/tests/test_runner_registry.py).
    THIS side does not. The harness has no dispatcher at all: nothing here submits a task to
    a runner, no profile names `agent-org-worker`, and `Get-HarnessRunnerPool` /
    `runner_pool()` have no executable caller - only tests and this file.

    documentation/notes/u4-profile-mechanism-deadcode.md set the bar: "importing the
    resolver is not consuming it". A sentence claiming both directions shipped equally would
    fail that bar, so the park is asserted instead of described. When a real dispatcher
    appears this test FAILS, which is the point: it is the reminder to re-state
    `little-coder`'s `status`, MODULE.md and the findings note in the same commit that makes
    the claim true.
    """
    here = HERE
    consumers = []
    for p in sorted(here.glob("*.ps1")):
        if p.name == "config.ps1":
            continue                      # defines the readers; defining is not consuming
        text = p.read_text(encoding="utf-8", errors="replace")
        if "Get-HarnessRunnerPool" in text:
            consumers.append(p.name)
    assert consumers == [], (
        "a harness script now reads the runner POOL: " + ", ".join(consumers) + ". "
        "If it dispatches work, the 'harness runner as an executor' direction is no longer "
        "parked - update runners.little-coder.status, MODULE.md and "
        "documentation/notes/u4bidir-findings.md, then delete this test."
    )
    # The one script that reads the registry today VALIDATES declarations; it does not run
    # work on them. Named explicitly so the distinction survives the next reader.
    check = here / "check-runner-endpoints.ps1"
    assert check.is_file() and "Get-HarnessRunnerAddresses" in check.read_text(
        encoding="utf-8", errors="replace")


def test_no_profile_routes_a_role_to_a_pooled_runner():
    """`pooled` runners are agent-org's to acquire, and agent-org has a scheduler with an
    allocation lock, affinity and quarantine that stop one daemon being double-booked. A
    harness profile that named one would hand out the same workspace behind that scheduler's
    back. Nothing does today; this is the guard that keeps it that way until a harness
    dispatcher exists that goes THROUGH agent-org rather than around it."""
    pooled = {r["runner"] for r in config.runner_pool()}
    for name in config.profile_names():
        for role in config.ROLES:
            target = config.resolve_role(role, profile=name)
            assert target["runner"] not in pooled, (
                "profile '" + name + "' assigns " + role + " to pooled runner '"
                + target["runner"] + "'"
            )


PS = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_powershell_and_python_readers_agree():
    """The anti-drift test: same file, same questions, same answers."""
    script = (
        ". '{0}';".format((HERE / "config.ps1").as_posix())
        + "$o=[ordered]@{};"
        + "$o.enabled=[bool](Get-HarnessSetting 'enabled');"
        + "$o.default_profile=(Get-HarnessSetting 'default_profile');"
        + "$o.claim_ttl=[int](Get-HarnessSetting 'pipeline.claim_ttl_minutes');"
        + "$o.root=(Get-HarnessSetting 'worktree.root');"
        + "$o.branch_prefix=(Get-HarnessSetting 'worktree.branch_prefix');"
        + "$o.env_files=@(Get-HarnessSetting 'worktree.env_files');"
        + "$o.profiles=@(Get-HarnessProfileNames);"
        + "$o.roles=[ordered]@{};"
        + "foreach($r in @('worker','tester','reviewer')){"
        + "$t=Resolve-RoleTarget -Role $r -Surface extension;"
        + "$o.roles[$r]=('{0}/{1}' -f $t.runner,$t.model)};"
        + "$o.runners=@(Get-HarnessRunnerNames);"
        + "$o.pool=@(Get-HarnessRunnerPool | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.runner,$_.label,$_.url,$_.kind });"
        + "$o | ConvertTo-Json -Depth 6 -Compress"
    )
    out = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    ps = json.loads(out.stdout.strip())

    assert ps["enabled"] == config.get("enabled")
    assert ps["default_profile"] == config.get("default_profile")
    assert ps["claim_ttl"] == config.get("pipeline.claim_ttl_minutes")
    assert ps["root"] == config.get("worktree.root")
    assert ps["branch_prefix"] == config.get("worktree.branch_prefix")
    # The runner registry is read by a THIRD reader (agent-org's bridge), so drift here is
    # not a cosmetic disagreement - it is two orchestrators believing in different pools.
    assert ps["runners"] == config.runner_names()
    assert ps["pool"] == [
        "{0}|{1}|{2}|{3}".format(p["runner"], p["label"], p["url"], p["kind"])
        for p in config.runner_pool()
    ]
    assert list(ps["env_files"]) == list(config.get("worktree.env_files"))
    assert sorted(ps["profiles"]) == sorted(config.profile_names())
    for role in config.ROLES:
        t = config.resolve_role(role, surface="extension")
        assert ps["roles"][role] == "{0}/{1}".format(t["runner"], t["model"]), role
