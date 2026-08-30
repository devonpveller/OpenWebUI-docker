"""Tests for the gate profiles and the andon declaration - including that both readers agree.

The cross-language test at the bottom is the point, for the same reason it is the point in
``test_harness_config.py``: two readers of one file is a standing invitation to drift, and a
gate profile that PowerShell reads as ``dark`` while the bridge reads as ``attended`` is a
silent removal of a human from the loop.

    python -m pytest scripts/agent-harness/test_gate_profiles.py -q
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


def test_the_shipped_default_is_attended():
    # The default must be the SAFE one. A typo that lands on the default should leave a
    # human at the gate, not remove one.
    for gate in config.GATES:
        assert config.resolve_gate(gate)["passer"] == "human", gate


def test_dark_auto_passes_every_gate():
    for gate in config.GATES:
        assert config.resolve_gate(gate, "dark")["passer"] == "auto", gate


def test_an_unknown_gate_profile_is_loud():
    # Never silently served by the default. Serving `attended` for a typo would be safe and
    # serving `dark` would not, and a rule that depends on which way the typo fell is not a
    # rule.
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_gate("anchor", "drak")
    assert "drak" in str(e.value)


def test_an_unknown_gate_is_loud():
    with pytest.raises(config.HarnessConfigError):
        config.resolve_gate("post_merge")


def test_a_gate_profile_may_only_say_human_or_auto(monkeypatch, tmp_path):
    cfg = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))
    cfg["gate_profiles"]["sloppy"] = {"anchor": "maybe", "pre_review": "human"}
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(p))
    config.load(fresh=True)
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_gate("anchor", "sloppy")
    assert "maybe" in str(e.value)


def test_every_gate_profile_assigns_every_gate():
    # A profile that omits a gate would leave that gate's behaviour undefined, which in an
    # unattended run means "nobody knows whether a human was meant to see this".
    for name in config.gate_profile_names():
        for gate in config.GATES:
            assert config.resolve_gate(gate, name)["passer"] in ("human", "auto")


def test_the_reserved_auto_namespace_is_declared_once():
    assert config.AUTO_PRINCIPAL_PREFIX == "auto:"
    # No shipped gate profile name may collide with the namespace, or an auto principal
    # would be indistinguishable from a profile-shaped human id.
    for name in config.gate_profile_names():
        assert not name.startswith(config.AUTO_PRINCIPAL_PREFIX)


# --- the andon declaration ------------------------------------------------------------

def _andon_conditions():
    return config.get("andon.conditions") or []


def test_every_andon_condition_is_fully_declared():
    conds = _andon_conditions()
    assert conds, "the shipped config declares no andon conditions"
    for c in conds:
        for field in ("id", "detects", "predicate", "on_fire", "on_indeterminate"):
            assert c.get(field), f"{c.get('id')} is missing '{field}'"
        # PLAN section 0 A6: a condition whose detection is prose is FALSIFIED. Every one
        # must name an incident it came from, so nobody can add an invented condition
        # without noticing they have nothing to cite.
        assert c.get("incident"), f"{c['id']} cites no incident"


def test_no_andon_condition_treats_indeterminate_as_a_pass():
    # A skip that counts as a pass is one of the failure shapes the board exists to catch;
    # it does not get to be the board's own behaviour.
    for c in _andon_conditions():
        assert c["on_indeterminate"] == "halt", c["id"]


def test_andon_condition_ids_are_unique():
    ids = [c["id"] for c in _andon_conditions()]
    assert len(ids) == len(set(ids)), ids


PS = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_every_declared_predicate_is_implemented():
    """A condition may not name a detector nobody wrote.

    Asked of the SHIPPED andon.ps1 rather than of a list kept here, because a list kept here
    is a list with a spell-checker - it would agree with itself while the file disagreed.
    """
    script = (
        "$ErrorActionPreference='Stop';"
        + "& '{0}' -List | Out-String".format((HERE / "andon.ps1").as_posix())
    )
    out = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    for c in _andon_conditions():
        line = [ln for ln in out.stdout.splitlines() if "predicate :" in ln and c["predicate"] in ln]
        assert line, f"andon.ps1 -List never mentions predicate '{c['predicate']}'"
        assert "MISSING" not in line[0], f"{c['id']} names an unimplemented predicate: {line[0]}"


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_powershell_and_python_agree_about_the_gates():
    """The anti-drift test: same file, same questions, same answers."""
    script = (
        ". '{0}';".format((HERE / "config.ps1").as_posix())
        + "$o=[ordered]@{};"
        + "$o.gates=@(Get-GateNames);"
        + "$o.prefix=(Get-AutoPrincipalPrefix);"
        + "$o.profiles=@(Get-GateProfileNames);"
        + "$o.active=(Get-GateProfileName);"
        + "$o.resolved=[ordered]@{};"
        + "foreach($p in (Get-GateProfileNames)){"
        + "foreach($g in (Get-GateNames)){"
        + "$o.resolved[\"$p/$g\"]=(Resolve-Gate -Gate $g -Profile $p).passer}};"
        + "$o | ConvertTo-Json -Depth 6 -Compress"
    )
    out = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    ps = json.loads(out.stdout.strip())

    assert list(ps["gates"]) == list(config.GATES)
    assert ps["prefix"] == config.AUTO_PRINCIPAL_PREFIX
    assert sorted(ps["profiles"]) == sorted(config.gate_profile_names())
    assert ps["active"] == config.gate_profile_name()
    for name in config.gate_profile_names():
        for gate in config.GATES:
            assert ps["resolved"][f"{name}/{gate}"] == config.resolve_gate(gate, name)["passer"]
