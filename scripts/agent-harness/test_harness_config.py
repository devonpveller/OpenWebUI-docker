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
    assert list(ps["env_files"]) == list(config.get("worktree.env_files"))
    assert sorted(ps["profiles"]) == sorted(config.profile_names())
    for role in config.ROLES:
        t = config.resolve_role(role, surface="extension")
        assert ps["roles"][role] == "{0}/{1}".format(t["runner"], t["model"]), role
