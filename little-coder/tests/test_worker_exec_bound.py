"""ao-exechang: the agent-org worker's per-command bound and its guidance, through the REAL config
path - the generator writes the worker configs, little-coder loads them, the AgentRunner turns
them into ot-exec's LC_EXEC_TIMEOUT and pi's --append-system-prompt files."""

from __future__ import annotations

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
KNOWLEDGE = LC / "agent-knowledge"


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


@pytest.fixture(scope="module")
def worker_configs(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("worker-configs")
    proc = subprocess.run([sys.executable, str(GENERATOR), "--out-dir", str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.mark.parametrize("n", [1, 2])
def test_worker_config_bound_is_900_and_reaches_ot_exec(worker_configs, n):
    cfg = load_config(worker_configs / f"worker-{n}" / "little-coder.config.yaml")
    assert cfg.workspace.exec_timeout_seconds == 900
    assert cfg.workspace.open_terminal_url == f"http://ao-ot-{n}:8000"
    env = AgentRunner(cfg, journals=None, ot_client=None)._build_env(_Ctx(), "/tmp/ev")  # type: ignore[arg-type]
    assert env["LC_EXEC_TIMEOUT"] == "900"


def test_interactive_little_coder_keeps_its_1800(worker_configs):
    assert load_config(CANONICAL).workspace.exec_timeout_seconds == 1800


def test_worker_config_differs_from_canonical_only_in_the_two_generated_lines(worker_configs):
    canon = CANONICAL.read_text(encoding="utf-8").splitlines()
    gen = (worker_configs / "worker-1" / "little-coder.config.yaml").read_text(encoding="utf-8").splitlines()
    assert all(ln.startswith("# ") for ln in gen[:4])   # the GENERATED header
    gen = gen[4:]
    changed = [(a, b) for a, b in zip(canon, gen) if a != b]
    assert len(canon) == len(gen)
    assert [b.strip() for _, b in changed] == [
        "open_terminal_url: http://ao-ot-1:8000",
        "exec_timeout_seconds: 900  # agent-org worker bound (generator)",
    ]


def test_the_guidance_is_in_the_worker_system_prompt(worker_configs):
    cfg = load_config(worker_configs / "worker-1" / "little-coder.config.yaml")
    cmd, _ = AgentRunner(cfg, journals=None, ot_client=None)._build_invocation("do it", _Ctx())  # type: ignore[arg-type]
    appended = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--append-system-prompt"]
    assert "/app/agent-knowledge/environment.md" in appended
    assert "/app/agent-knowledge/engineering-principles.md" in appended
    # the image copies little-coder/agent-knowledge to /app/agent-knowledge (Dockerfile.agent)
    env_md = (KNOWLEDGE / "environment.md").read_text(encoding="utf-8")
    for phrase in (
        "Every `bash` call has a time limit",
        "timed out after N s, killed",
        "Run the narrowest test that shows the behaviour first",
        "Run the full suite once, after your edits",
        "never as a baseline before\n  your first edit unless the task needs it",
        "nohup python -m pytest -q tests > /tmp/suite.log 2>&1 &",
        "tail -n 20 /tmp/suite.log",
        "`( ... ) &`",
    ):
        assert phrase in env_md, phrase
    assert "Narrowest test\nfirst; the full suite once, after editing" in \
        (KNOWLEDGE / "engineering-principles.md").read_text(encoding="utf-8")


def test_dockerfile_ships_the_knowledge_where_the_config_points():
    dockerfile = (LC / "docker" / "Dockerfile.agent").read_text(encoding="utf-8")
    assert "WORKDIR /app" in dockerfile and "COPY agent-knowledge ./agent-knowledge" in dockerfile
