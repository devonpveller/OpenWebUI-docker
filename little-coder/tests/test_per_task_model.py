"""Per-task model (ef-worker-model, tracker H3).

POST /tasks takes an optional `model`. It must be `agent.model` or a key of `agent.allowed_models`
(exact match); anything else, an empty string and anything starting with '-' are refused 422 before
a task exists. An allowed model reaches the agent's argv as `--model <mapped id>` for that task
only; the next task without `model` runs `agent.model` again. GET /tasks/<id> and the journal's
`task_started` record say which model ran.

The tests drive the real daemon (constructor, HTTP route, enqueue, _run_task, AgentRunner) with a
stub agent command: a Python script that records its argv. No model, gateway or GPU is touched.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from littlecoder.agent import AgentRunner
from littlecoder.config import AgentConfig, Config, ConfigError, load_config
from littlecoder.daemon import LittleCoderDaemon, TriggerRequest, build_app
from littlecoder.urlnorm import normalize_repo_url

_STUB = (
    "import json, os, sys\n"
    "with open(os.environ['EFWM_ARGV_LOG'], 'a', encoding='utf-8') as fh:\n"
    "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
)

_ALLOWED = {"local-large": "llamacpp/local-large", "local-small": "llamacpp/local-small"}


def _daemon(tmp_path, monkeypatch, *, allowed: dict[str, str] | None = None):
    stub = tmp_path / "stub_agent.py"
    stub.write_text(_STUB, encoding="utf-8")
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("EFWM_ARGV_LOG", str(argv_log))
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = Config()
    agent = dict(command=[sys.executable, str(stub)], model="llamacpp/local-large",
                 prompt_mode="arg", extra_args=[], use_session=False)
    # Built without the field on a build that lacks it, so the base RED run shows the BEHAVIOUR
    # (the key ignored, agent.model run) rather than a construction error.
    if "allowed_models" in AgentConfig.model_fields:
        agent["allowed_models"] = dict(_ALLOWED if allowed is None else allowed)
    cfg.agent = AgentConfig(**agent)
    cfg.journals.dir = str(tmp_path / "journals")
    cfg.workspace.path = str(ws)
    cfg.workspace.open_terminal_url = "http://127.0.0.1:1"   # never reached: no acceptance cmd
    d = LittleCoderDaemon(cfg)
    d.current_focus = normalize_repo_url("https://github.com/acme/widget")
    d.workspace = SimpleNamespace(is_focused=lambda: True)
    d._ensure_git_credentials = lambda: None
    tok = "-".join(["efwm", "test", "token"])   # ao-dauth: every route but /health needs it
    client = TestClient(build_app(d, token=tok), headers={"Authorization": f"Bearer {tok}"})
    return d, client, argv_log   # no `with`: the lifespan (workers) never starts


def _run_queued(d) -> str:
    task_id = d.queue.get_nowait()
    asyncio.run(d._run_task(d.tasks[task_id]))
    return task_id


def _model_after_flag(argv: list[str]) -> str:
    assert argv.count("--model") == 1, argv
    return argv[argv.index("--model") + 1]


def _started_records(d) -> list[dict]:
    path = Path(d.cfg.journals.dir) / "outcomes.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r.get("event") == "task_started"]


def test_a_task_runs_the_model_it_names_and_the_next_task_runs_agent_model(tmp_path, monkeypatch):
    d, client, argv_log = _daemon(tmp_path, monkeypatch)

    r = client.post("/tasks", json={"prompt": "edit one file", "channel": "batch",
                                    "model": "local-small"})
    assert r.status_code == 200, r.text
    first = _run_queued(d)
    r = client.post("/tasks", json={"prompt": "and another", "channel": "batch"})
    assert r.status_code == 200, r.text
    second = _run_queued(d)

    runs = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert [_model_after_flag(a) for a in runs] == ["llamacpp/local-small", "llamacpp/local-large"]
    # GET /tasks/<id> says which model ran; the config was never changed by the first task.
    assert client.get(f"/tasks/{first}").json()["model"] == "llamacpp/local-small"
    assert client.get(f"/tasks/{second}").json()["model"] == "llamacpp/local-large"
    assert d.cfg.agent.model == "llamacpp/local-large"
    # The journal records it too (the durable record of what ran).
    started = {r["task_id"]: r["model"] for r in _started_records(d)}
    assert started == {first: "llamacpp/local-small", second: "llamacpp/local-large"}


@pytest.mark.parametrize("bad", [
    "gpt-4o",                  # not in the allowlist
    "local-small-x",           # prefix of nothing / extension of an allowed key
    "local-smal",              # prefix of an allowed key
    "LOCAL-SMALL",             # case differs
    " local-small",            # whitespace is not stripped into a match
    "local-small\n",
    "llamacpp/local-small",    # a mapped VALUE is not a name a caller may send
    "local-*",                 # no pattern semantics
    "-local-small",            # flag-shaped
    "--exclude-tools",
    "",                        # empty
])
def test_a_model_outside_the_allowlist_is_refused_and_no_task_is_created(
        tmp_path, monkeypatch, bad):
    d, client, argv_log = _daemon(tmp_path, monkeypatch)
    r = client.post("/tasks", json={"prompt": "x", "channel": "batch", "model": bad})
    assert r.status_code == 422, r.text
    assert "model refused" in r.json()["detail"]
    assert d.tasks == {} and d.contexts == {} and d.queue.qsize() == 0
    assert not argv_log.exists()


def test_an_unconfigured_daemon_allows_only_agent_model(tmp_path, monkeypatch):
    """Default allowed_models = {}: the daemon behaves as before. Naming agent.model itself is
    allowed (it changes nothing); any other name, including a role another daemon allows, is not."""
    d, client, _ = _daemon(tmp_path, monkeypatch, allowed={})
    assert client.post("/tasks", json={"prompt": "x", "channel": "batch",
                                       "model": "local-small"}).status_code == 422
    assert d.tasks == {}
    r = client.post("/tasks", json={"prompt": "x", "channel": "batch",
                                    "model": "llamacpp/local-large"})
    assert r.status_code == 200
    assert client.get(f"/tasks/{r.json()['task_id']}").json()["model"] == "llamacpp/local-large"


def test_old_callers_without_model_are_unchanged(tmp_path, monkeypatch):
    # OWUI's little-coder path and the CLI never send `model`.
    d, client, argv_log = _daemon(tmp_path, monkeypatch)
    r = client.post("/tasks", json={"prompt": "hi", "channel": "owui", "session_id": "chat-1"})
    assert r.status_code == 200
    _run_queued(d)
    (argv,) = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert _model_after_flag(argv) == "llamacpp/local-large"
    assert TriggerRequest(prompt="x").model is None


def test_the_agent_refuses_a_flag_shaped_model_that_bypassed_enqueue():
    """Defence in depth: a TaskState built outside enqueue still cannot put a flag after --model."""
    cfg = Config()
    cfg.agent = AgentConfig(command=["little-coder"], model="llamacpp/m", prompt_mode="arg",
                            extra_args=[], use_session=False)
    runner = AgentRunner(cfg, journals=None, ot_client=None)  # type: ignore[arg-type]
    st = SimpleNamespace(session_id="s", channel="batch", prompt="p", plan_only=False,
                         model="--exclude-tools")
    with pytest.raises(ValueError):
        runner._build_invocation("p", SimpleNamespace(state=st))
    st.model = ""          # unset -> agent.model
    cmd, _ = runner._build_invocation("p", SimpleNamespace(state=st))
    assert _model_after_flag(cmd) == "llamacpp/m"


@pytest.mark.parametrize("entry", [
    {"-x": "llamacpp/local-small"},
    {"local-small": "--exclude-tools"},
    {"": "llamacpp/local-small"},
    {"local-small": ""},
    {"local small": "llamacpp/local-small"},
    {"local-small": "llamacpp/local-small; rm -rf /"},
    # TF4 (attempt 2): a value must be `llamacpp/<gateway role>`, a name a gateway role
    {"local-small": "openai/local-small"},          # another provider
    {"local-small": "local-small"},                 # no provider
    {"local-small": "llamacpp/"},                   # no role
    {"local-small": "llamacpp/a/b"},                # a path, not a role
    {"local-small": "llamacpp/-x"},                 # flag-shaped role
    {"local-small": "LLAMACPP/local-small"},        # the provider is matched exactly
    {"a/b": "llamacpp/local-small"},                # a name with a provider part
])
def test_a_flag_shaped_or_malformed_allowlist_entry_fails_the_boot(tmp_path, entry):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"agent": {"allowed_models": entry}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_gateway_role_entries_load(tmp_path):
    """TF4: the shape the committed config uses still boots (incl. a `:` variant role)."""
    p = tmp_path / "c.yaml"
    entry = {"local-small": "llamacpp/local-small", "local-small:nothink": "llamacpp/local-small:nothink"}
    p.write_text(yaml.safe_dump({"agent": {"allowed_models": entry}}), encoding="utf-8")
    assert load_config(p).agent.allowed_models == entry


# --- the committed config: every allowed model is registered with pi and is a gateway role --------

_LC = Path(__file__).resolve().parents[1]
_LITELLM_LOCAL = _LC.parent / "inference" / "config" / "litellm" / "model_list" / "local.yaml"


def test_every_allowed_model_is_registered_in_models_json():
    cfg = load_config(_LC / "config" / "little-coder.config.yaml")
    models = json.loads((_LC / "config" / "models.json").read_text(encoding="utf-8"))
    registered = {f"{prov}/{m['id']}"
                  for prov, spec in models["providers"].items() for m in spec["models"]}
    assert cfg.agent.model in registered
    assert cfg.agent.allowed_models, "the committed config allows no per-task model"
    for name, target in cfg.agent.allowed_models.items():
        assert target in registered, f"{name} -> {target} is not registered in models.json"


@pytest.mark.skipif(not _LITELLM_LOCAL.exists(), reason="inference plane not in this checkout")
def test_every_allowed_model_is_a_gateway_role_behind_the_llamacpp_alias():
    """Routing posture: a per-task model is a LiteLLM role, reached through the gateway alias the
    llamacpp provider already uses - never an upstream."""
    cfg = load_config(_LC / "config" / "little-coder.config.yaml")
    models = json.loads((_LC / "config" / "models.json").read_text(encoding="utf-8"))
    assert models["providers"]["llamacpp"]["baseUrl"] == "http://llama-cpp:8080/v1"
    roles = {e["model_name"] for e in yaml.safe_load(_LITELLM_LOCAL.read_text(encoding="utf-8"))
             ["model_list"]}
    for name, target in cfg.agent.allowed_models.items():
        provider, _, role = target.partition("/")
        assert provider == "llamacpp", target
        assert name in roles and role in roles, (name, target)
