"""ao-temp06: the agent-org worker's per-call sampling temperature (operator D22: 0.6), through the
REAL config path - the generator writes the worker configs, little-coder loads them, the AgentRunner
turns them into the env pi runs with (LC_TEMPERATURE, applied to the request body by the
open-terminal-exec extension; tests/node/e2e_temp06 proves the body against the real pi).

Where 0.3 comes from: little-coder's pi package ships `.pi/settings.json` whose
`default_model_profile.temperature` is 0.3 (our model ids match no per-model profile), and its
`benchmark-profiles` extension stamps it on every provider request. Nothing in this repo set it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from littlecoder.agent import AgentRunner
from littlecoder.config import load_config

LC = Path(__file__).resolve().parent.parent
REPO = LC.parent
GENERATOR = REPO / "agent-org" / "scripts" / "gen-worker-configs.py"
CANONICAL = LC / "config" / "little-coder.config.yaml"
PROFILES = REPO / "agent-org" / "agent-bridge" / "profiles"

# Every agent-bridge role profile's temperature as of ao-temp06 (judgement, planner, PM/PO, reviewers
# and the worker-default profile). This item changes none of them.
PROFILE_TEMPERATURES = {
    "planner": 0.3, "pm-voice": 0.5, "pm": 0.3, "po": 0.4, "reviewer-correctness": 0.2,
    "reviewer-ethics": 0.2, "reviewer-scope": 0.2, "reviewer-security": 0.2, "worker-default": 0.2,
}


class _State:
    task_id = "t1"
    session_id = "s1"
    channel = "cli"
    user_id = "u"
    repo = "r"
    prompt = "p"
    plan_only = False


class _Ctx:
    state = _State()


def _env(cfg):
    return AgentRunner(cfg, journals=None, ot_client=None)._build_env(_Ctx(), "/tmp/ev")  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def worker_configs(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("worker-configs")
    proc = subprocess.run([sys.executable, str(GENERATOR), "--out-dir", str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.mark.parametrize("n", [1, 2])
def test_worker_config_carries_0_6_and_it_reaches_pi_env(worker_configs, n):
    cfg = load_config(worker_configs / f"worker-{n}" / "little-coder.config.yaml")
    assert cfg.agent.temperature == 0.6
    assert _env(cfg)["LC_TEMPERATURE"] == "0.6"


def test_coder_plane_config_is_unchanged(monkeypatch):
    cfg = load_config(CANONICAL)
    assert cfg.agent.temperature is None
    monkeypatch.setenv("LC_TEMPERATURE", "0.9")        # never inherited from the daemon's environment
    assert "LC_TEMPERATURE" not in _env(cfg)


def test_worker_override_is_set_not_inherited(worker_configs, monkeypatch):
    monkeypatch.setenv("LC_TEMPERATURE", "0.9")
    cfg = load_config(worker_configs / "worker-1" / "little-coder.config.yaml")
    assert _env(cfg)["LC_TEMPERATURE"] == "0.6"


@pytest.mark.parametrize("bad", ["-0.1", "2.5", "hot"])
def test_config_rejects_a_bad_temperature(tmp_path, bad):
    src = CANONICAL.read_text(encoding="utf-8").replace("  temperature: null", f"  temperature: {bad}")
    f = tmp_path / "c.yaml"
    f.write_text(src, encoding="utf-8")
    with pytest.raises(Exception):
        load_config(f)


def test_judgement_and_planner_profiles_are_unchanged():
    found = {p.stem: json.loads(p.read_text(encoding="utf-8"))["temperature"] for p in PROFILES.glob("*.json")}
    assert found == PROFILE_TEMPERATURES


def test_judge_calls_are_still_deterministic():
    """The judge (little-coder's llm client, not pi) sends its own temperature=0.0; not touched."""
    src = (LC / "src" / "littlecoder" / "judge.py").read_text(encoding="utf-8")
    assert src.count("temperature=0.0") >= 4
    assert "LC_TEMPERATURE" not in src
    assert "LC_TEMPERATURE" not in (LC / "src" / "littlecoder" / "llm.py").read_text(encoding="utf-8")
