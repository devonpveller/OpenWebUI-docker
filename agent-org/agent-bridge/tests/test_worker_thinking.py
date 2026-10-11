"""ao-thinkon: an agent-org worker turn requests THINKING ON, end to end through the committed config.

The chain a worker turn's model travels (no network, no model; static reads of the repo's files):
  worker-default profile seed `model`  ->  the bridge sends it as the /tasks `model`
  -> little-coder `agent.allowed_models[<role>]` = `llamacpp/<role>` (config/little-coder.config.yaml)
  -> pi registers that id as a reasoning model (little-coder/config/models.json)
  -> LiteLLM role `<role>` forwards `openai/<concrete id>` (inference/config/litellm/model_list/local.yaml)
  -> llama-swap's `setParamsByID` for that concrete id sets `enable_thinking` (inference/config/
     llama-swap.config.yaml): `${MODEL_ID}` = true, `${MODEL_ID}:nothink` = false.
Before ao-thinkon the seed was `local-small` -> `qwen36-27b:nothink` -> enable_thinking false.

The bridge venv has no PyYAML, so the few fields are read with anchored patterns; a change in the
files' shape fails these tests loudly rather than passing vacuously.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # agent-org/agent-bridge
REPO = ROOT.parents[1]
LC_CONFIG = REPO / "little-coder" / "config" / "little-coder.config.yaml"
LC_MODELS = REPO / "little-coder" / "config" / "models.json"
LITELLM_LOCAL = REPO / "inference" / "config" / "litellm" / "model_list" / "local.yaml"
LLAMA_SWAP = REPO / "inference" / "config" / "llama-swap.config.yaml"


def _need(*paths: Path) -> None:
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"not in this checkout: {missing}")


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def _seed_model() -> str:
    return json.loads((ROOT / "profiles" / "worker-default.json").read_text(encoding="utf-8"))["model"]


def _litellm_concrete(role: str) -> str:
    """`litellm_params.model` of the `- model_name: <role>` entry."""
    pat = r"^  - model_name: " + re.escape(role) + r"\n    litellm_params:\n      model: (\S+)$"
    found = re.findall(pat, _text(LITELLM_LOCAL), re.M)
    assert len(found) == 1, (role, found)
    return found[0]


def _swap_block(model: str) -> str:
    """The llama-swap `models.<model>:` block (2-space key, up to the next 2-space key)."""
    m = re.search(r"^  " + re.escape(model) + r":\n(.*?)(?=^  \S|\Z)", _text(LLAMA_SWAP), re.M | re.S)
    assert m, model
    return m.group(1)


def _thinking_for_role(role: str) -> bool:
    provider, _, concrete = _litellm_concrete(role).partition("/")
    assert provider == "openai", concrete
    base, nothink, rest = concrete.partition(":nothink")
    assert rest == "", concrete
    key = '"${MODEL_ID}:nothink"' if nothink else '"${MODEL_ID}"'
    pat = re.escape(key) + r":\n\s+chat_template_kwargs:\n\s+enable_thinking: (true|false)"
    m = re.search(pat, _swap_block(base))
    assert m, (base, key)
    return m.group(1) == "true"


def test_the_worker_seed_asks_for_a_thinking_role():
    _need(LITELLM_LOCAL, LLAMA_SWAP)
    assert _seed_model() == "local-large"
    assert _thinking_for_role(_seed_model()) is True


def test_the_role_check_is_not_vacuous():
    """The same reader says local-small is the no-think role, so a seed on it would fail above."""
    _need(LITELLM_LOCAL, LLAMA_SWAP)
    assert _thinking_for_role("local-small") is False


def test_little_coder_runs_the_worker_seed_as_a_reasoning_model():
    _need(LC_CONFIG, LC_MODELS)
    role = _seed_model()
    allowed = re.search(r"^  allowed_models:\n((?:    \S+: \S+\n)+)", _text(LC_CONFIG), re.M)
    assert allowed, "agent.allowed_models block not found"
    assert f"    {role}: llamacpp/{role}\n" in allowed.group(1)
    models = json.loads(LC_MODELS.read_text(encoding="utf-8"))["providers"]["llamacpp"]["models"]
    (m,) = [x for x in models if x["id"] == role]
    assert m["reasoning"] is True


def test_the_worker_thinking_role_is_bounded_by_the_server_reasoning_budget():
    """ao-thinkon: with little-coder's client-side cap gone, the server's reasoning budget is the ONLY
    bound on a thought. The llama-swap entry the worker's role reaches must pass `--reasoning-budget`
    from an env var; when this checkout has `inference/.env` (gitignored), its value must be a
    positive integer (an empty or -1 value would be an unbounded thought)."""
    _need(LITELLM_LOCAL, LLAMA_SWAP)
    base = _litellm_concrete(_seed_model()).partition("/")[2].partition(":nothink")[0]
    m = re.search(r"^\s+--reasoning-budget \$\{env\.([A-Z0-9_]+)\}\s*$", _swap_block(base), re.M)
    assert m, f"no --reasoning-budget in the llama-swap entry for {base}"
    env_file = REPO / "inference" / ".env"
    if env_file.exists():
        vals = re.findall(r"^" + re.escape(m.group(1)) + r"=(\S*)\s*$", _text(env_file), re.M)
        assert vals, f"{m.group(1)} not set in inference/.env"
        assert vals[-1].isdigit() and int(vals[-1]) > 0, (m.group(1), vals[-1])
