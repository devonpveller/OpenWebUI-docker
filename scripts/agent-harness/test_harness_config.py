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


def test_unknown_runner_is_rejected():
    with pytest.raises(config.HarnessConfigError):
        config.runner("little-codr")


# --- describe_runner ----------------------------------------------------------------
# `profile: list` in a Mattermost thread renders describe_profile() for every profile:
# "all-local: worker=little-coder/local-default, ...". It never says that little-coder is
# UNPROVEN, or how the harness reaches it. An operator switching a thread to a local
# profile is choosing a substrate, and the listing does not tell them what they chose.
# describe_runner() is the runner half of that listing (bridge.py:1305 renders it).

def test_describe_runner_names_the_substrate_and_its_status():
    line = config.describe_runner("little-coder")
    assert line.startswith("little-coder: "), line
    assert "local-default" in line, line          # the default model
    assert "docker-exec" in line, line            # how the harness reaches it
    assert "unproven" in line, line               # the A11 caveat, visible at the point of choice


def test_describe_runner_reports_a_proven_runner_as_proven():
    line = config.describe_runner("claude-code")
    assert line.startswith("claude-code: "), line
    assert "opus" in line, line
    # "unproven" contains "proven"; a substring check alone would pass on the wrong word.
    assert "proven" in line and "unproven" not in line, line


def test_describe_runner_is_unknown_not_an_exception():
    # Mirrors describe_profile: a listing must never blow up on a name it does not know.
    assert config.describe_runner("nope") == "nope: (unknown)"


# --- the door check -----------------------------------------------------------------
# A runner record names how to REACH a daemon. Until 2026-08-30 the little-coder runner
# declared endpoint "http://127.0.0.1:8090" and nothing ever called it, so nobody noticed
# that coder/docker-compose.yml publishes only 127.0.0.1:9091->9090 (Prometheus): the
# config named a door that did not exist. Prose cannot keep that honest; this can.

REPO_ROOT = HERE.parents[1]   # scripts/agent-harness -> scripts -> the checkout root
COMPOSE_FILES = [
    REPO_ROOT / "coder" / "docker-compose.yml",
    REPO_ROOT / "docker-compose.yml",
]


def _compose_text() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in COMPOSE_FILES if p.is_file()
    )


def _door_problems(runners: dict, compose: str) -> list:
    """Every declared transport must correspond to a door that actually exists.

    docker-exec -> the named container must appear as a `container_name:` somewhere in the
    stack's compose files, and its base_url must be container-local (a host address there is
    a sign someone changed transport without changing the URL).
    http         -> a loopback base_url must have its port PUBLISHED in a compose file.
    """
    problems = []
    for name, r in runners.items():
        if name.startswith("_") or not isinstance(r, dict):
            continue
        transport = r.get("transport")
        if not transport:
            if "base_url" in r or "endpoint" in r:
                problems.append(f"{name}: declares a URL but no transport - nothing can call it")
            continue
        if transport not in ("docker-exec", "http"):
            problems.append(f"{name}: unknown transport '{transport}'")
            continue
        base = str(r.get("base_url", ""))
        host = base.split("//", 1)[-1].split("/", 1)[0]
        hostname, _, port = host.partition(":")
        if transport == "docker-exec":
            container = r.get("container")
            if not container:
                problems.append(f"{name}: transport docker-exec but no container named")
            elif f"container_name: {container}" not in compose:
                problems.append(
                    f"{name}: container '{container}' is in no compose file in this repo")
            if hostname not in ("localhost", "127.0.0.1"):
                problems.append(
                    f"{name}: docker-exec base_url '{base}' is not container-local")
        else:  # http
            if hostname in ("localhost", "127.0.0.1"):
                if f'"127.0.0.1:{port}:' not in compose and f"127.0.0.1:{port}:" not in compose:
                    problems.append(
                        f"{name}: transport http on {base} but no compose file publishes "
                        f"127.0.0.1:{port} - the door does not exist")
    return problems


def test_no_runner_names_a_door_that_does_not_exist():
    assert _door_problems(config.get("runners") or {}, _compose_text()) == []


def test_the_door_check_catches_an_unpublished_port():
    """The red half. This is the EXACT defect U4 started from, kept executable so it cannot
    come back: an http transport pointed at a host port nothing publishes."""
    bad = {"little-coder": {"kind": "little-coder", "transport": "http",
                            "base_url": "http://127.0.0.1:8090"}}
    problems = _door_problems(bad, _compose_text())
    assert problems and "does not exist" in problems[0], problems


def test_the_door_check_catches_a_url_with_no_transport():
    """The pre-2026-08-30 shape: a URL and no way to call it."""
    bad = {"little-coder": {"kind": "little-coder", "endpoint": "http://127.0.0.1:8090"}}
    assert _door_problems(bad, _compose_text())


def test_the_door_check_catches_a_missing_container():
    bad = {"little-coder": {"kind": "little-coder", "transport": "docker-exec",
                            "container": "no-such-container", "base_url": "http://localhost:8090"}}
    problems = _door_problems(bad, _compose_text())
    assert problems and "no compose file" in problems[0], problems


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
        + "$o.runners=@(Get-HarnessRunnerNames);"
        + "$lc=Get-HarnessRunner -Name 'little-coder';"
        + "$o.lc=[ordered]@{};"
        + "foreach($k in @('kind','status','transport','container','base_url','submit_path','task_path','events_path','health_path','project_path','default_model')){"
        + "$o.lc[$k]=[string]$lc[$k]};"
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
    # The RUNNER RECORD has to agree too, not just the policy answer. It is what a
    # dispatcher calls, and it is now the only place the transport is written down.
    assert sorted(ps["runners"]) == sorted(config.runner_names())
    lc = config.runner("little-coder")
    for key, value in ps["lc"].items():
        assert value == str(lc.get(key, "")), key
    for role in config.ROLES:
        t = config.resolve_role(role, surface="extension")
        assert ps["roles"][role] == "{0}/{1}".format(t["runner"], t["model"]), role
