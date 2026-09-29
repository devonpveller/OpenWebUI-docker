"""Role-name traffic lands on the right queue (model-roles item mr-gateway, 2026-09-28).

llm-queue is NOT changed for the roles: LiteLLM forwards each role's
`litellm_params.model` - the concrete id - so the queue sees exactly the ids it
always has. These tests read the REAL inference/config/litellm/model_list/local.yaml
with PyYAML (the parser LiteLLM itself uses), strip the provider prefix the way
LiteLLM does before it forwards, and prove:

  * every role maps to the queue and the upstream its concrete id always had;
  * the thinking roles and the no-thinking roles share ONE chat queue (one slot pool);
  * local-embed goes to the embed upstream - which it would NOT do if a role name
    ever reached the queue unrewritten (an unknown id falls to the CHAT upstream);
  * the stdlib reader scripts/stack/model_labels.py uses agrees with PyYAML, so the
    label generator reads the same roles LiteLLM registers;
  * a request carrying each forwarded id goes through the real app to that upstream.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import yaml

from llm_queue.app_state import AppState
from llm_queue.config import Settings
from llm_queue.main import app
from llm_queue.registry import Registry

REPO = Path(__file__).resolve().parents[3]
LOCAL_YAML = REPO / "inference" / "config" / "litellm" / "model_list" / "local.yaml"
ROLES = ["local-large", "local-large:nothink", "local-small", "local-small:nothink", "local-embed"]


def _forwarded():
    """{model_name: the id LiteLLM sends upstream} for every local entry."""
    data = yaml.safe_load(LOCAL_YAML.read_text(encoding="utf-8"))
    return {
        e["model_name"]: e["litellm_params"]["model"].split("/", 1)[1] for e in data["model_list"]
    }


def test_every_role_is_registered_and_forwards_a_concrete_id():
    fwd = _forwarded()
    assert {r: fwd[r] for r in ROLES} == {
        "local-large": "qwen36-27b",
        "local-large:nothink": "qwen36-27b:nothink",
        "local-small": "qwen36-27b:nothink",
        "local-small:nothink": "qwen36-27b:nothink",
        "local-embed": "bge-m3",
    }


def test_role_ids_reach_the_same_queue_and_upstream_as_the_old_names():
    s = Settings()
    reg = Registry(s)
    fwd = _forwarded()
    chat_q, embed_q = reg.queue_for("qwen36-27b"), reg.queue_for("bge-m3")
    assert chat_q is not embed_q
    for role in ("local-large", "local-large:nothink", "local-small", "local-small:nothink"):
        assert reg.queue_for(fwd[role]) is chat_q, role
        assert reg.upstream_for(fwd[role]) == s.upstream_base_url, role
    assert reg.queue_for(fwd["local-embed"]) is embed_q
    assert reg.upstream_for(fwd["local-embed"]) == s.embed_upstream_base_url
    olds = ("qwen36-27b", "qwen36-27b:nothink", "bge-m3", "bge-m3-f16.gguf", "qllama/bge-m3:latest")
    for old in olds:
        want = embed_q if old.startswith(("bge", "qllama")) else chat_q
        assert reg.queue_for(fwd[old]) is want, old


def test_an_unrewritten_role_name_would_land_on_the_wrong_upstream():
    """Why the forwarding matters: the queue does not know role names, and does not need to."""
    s = Settings()
    reg = Registry(s)
    assert reg.upstream_for("local-embed") == s.upstream_base_url  # the CHAT upstream - wrong
    assert reg.upstream_for(_forwarded()["local-embed"]) == s.embed_upstream_base_url


def test_the_label_generators_reader_agrees_with_pyyaml():
    path = REPO / "scripts" / "stack" / "model_labels.py"
    spec = importlib.util.spec_from_file_location("model_labels", path)
    ml = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ml)
    text = LOCAL_YAML.read_text(encoding="utf-8")
    ours = ml.parse_model_list(text)
    entries = yaml.safe_load(text)["model_list"]
    theirs = [(e["model_name"], e["litellm_params"]["model"]) for e in entries]
    assert ours == theirs


class _Resp:
    status_code = 200
    headers = httpx.Headers({"content-type": "application/json"})

    async def aiter_raw(self):
        yield b'{"object":"list","data":[],"choices":[]}'

    async def aclose(self):
        pass


class _Upstream:
    """Records (base_url, path, model) for every request the queue forwards."""

    def __init__(self):
        self.seen = []

    async def request(self, base_url, method, path, *, headers, content=None):
        self.seen.append((base_url, path, json.loads(content or b"{}").get("model")))
        return httpx.Response(200, json={"object": "list", "data": []})

    async def open_stream(self, base_url, method, path, *, headers, content):
        self.seen.append((base_url, path, json.loads(content or b"{}").get("model")))
        return _Resp()

    async def aclose(self):
        pass


async def test_forwarded_role_ids_go_through_the_app_to_the_right_upstream():
    s = Settings()
    state = AppState(s)
    fake = _Upstream()
    state.upstream = fake
    app.state.app = state
    await state.start()
    fwd = _forwarded()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://queue") as c:
        for role in ROLES:
            path = "/v1/embeddings" if role == "local-embed" else "/v1/chat/completions"
            body = (
                {"model": fwd[role], "input": "x"}
                if role == "local-embed"
                else {"model": fwd[role], "messages": [{"role": "user", "content": "hi"}]}
            )
            r = await c.post(path, json=body)
            assert r.status_code == 200, (role, r.text)
    await state.stop()
    assert fake.seen == [
        (s.upstream_base_url, "/v1/chat/completions", "qwen36-27b"),
        (s.upstream_base_url, "/v1/chat/completions", "qwen36-27b:nothink"),
        (s.upstream_base_url, "/v1/chat/completions", "qwen36-27b:nothink"),
        (s.upstream_base_url, "/v1/chat/completions", "qwen36-27b:nothink"),
        (s.embed_upstream_base_url, "/v1/embeddings", "bge-m3"),
    ]
