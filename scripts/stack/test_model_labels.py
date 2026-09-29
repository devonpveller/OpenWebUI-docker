"""Hermetic tests for scripts/stack/model_labels.py (model-roles, 2026-09-28).

No Docker and no Open WebUI: the label generator reads files, and the Open WebUI
sync is driven through FakeOwui, an in-memory stand-in for the four admin-API
calls it makes (GET /health, GET /api/v1/models/model, POST .../create, POST
.../model/update) that behaves the way Open WebUI 0.11.0's routers/models.py
does for them (read on the live container, read-only, 2026-09-28). The same
sync is run against a REAL disposable Open WebUI by the item's drill; this file
pins the logic.

Run:  python -m pytest scripts/stack -q
"""

from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_labels as ml  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_TABLE = {
    "local-large": ("qwen36-27b", "thinking"),
    "local-large:nothink": ("qwen36-27b:nothink", "no thinking"),
    "local-small": ("qwen36-27b:nothink", "no thinking"),
    "local-small:nothink": ("qwen36-27b:nothink", "no thinking"),
    "local-embed": ("bge-m3", "embeddings"),
}
OLD_NAMES = ["qwen36-27b", "qwen36-27b:nothink", "bge-m3", "bge-m3-f16.gguf", "qllama/bge-m3:latest"]


# --------------------------------------------------------------------------
# a scratch repo root: the REAL inference config and compose files, a scratch
# env file and a scratch model store
# --------------------------------------------------------------------------


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in (ml.MODEL_LIST_REL, ml.LLAMA_SWAP_REL, ml.UPSTREAMS_REL):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, root / rel)
    store = tmp_path / "models"
    (store / "unsloth" / "Qwen3.8-27B-GGUF").mkdir(parents=True)
    (store / "unsloth" / "Qwen3.8-27B-GGUF" / "Qwen3.8-27B-Q4_K_M.gguf").write_bytes(b"GGUF")
    embed = root / "data" / "models" / "embeddings"
    embed.mkdir(parents=True)
    (embed / "bge-m3-f16.gguf").write_bytes(b"GGUF")
    write_env(root, store, "/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf")
    return root


def write_env(root: Path, store: Path | None, path: str | None, extra: str = "") -> Path:
    lines = ["COMPOSE_PROFILES=local"]
    if store is not None:
        lines.append(f"LM_MODELS_DIR={store}")
    if path is not None:
        lines.append(f"LLAMA_SWAP_QWEN36_27B_MODEL_PATH={path}")
    env = root / ml.ENV_REL
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    return env


def by_role(labels):
    return {item.role: item for item in labels}


# --------------------------------------------------------------------------
# the label format
# --------------------------------------------------------------------------


@pytest.mark.parametrize("filename,mode,label", [
    ("/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf", "thinking", "Qwen3.8-27B Q4_K_M (thinking)"),
    ("Qwen3.8-27B-Q4_K_M.gguf", "no thinking", "Qwen3.8-27B Q4_K_M (no thinking)"),
    ("/models/bge-m3-f16.gguf", "embeddings", "bge-m3 f16 (embeddings)"),
    ("Qwen3.6-35B-A3B-Q4_K_M.gguf", "thinking", "Qwen3.6-35B-A3B Q4_K_M (thinking)"),
    ("Big-70B-IQ4_XS-00001-of-00003.gguf", "thinking", "Big-70B IQ4_XS (thinking)"),
    ("model-BF16.gguf", "thinking", "model BF16 (thinking)"),
    ("gpt-oss-120b-MXFP4.gguf", "thinking", "gpt-oss-120b MXFP4 (thinking)"),
    ("plainname.gguf", "thinking", "plainname (thinking)"),
])
def test_label_is_the_file_stem_with_its_quant_split_off_plus_the_mode(filename, mode, label):
    assert ml.label_for(filename, mode) == label


def test_a_label_needs_a_gguf_and_a_known_mode():
    with pytest.raises(ml.LabelError, match="not a .gguf"):
        ml.label_for("/models/x.bin", "thinking")
    with pytest.raises(ml.LabelError, match="unknown mode"):
        ml.label_for("x.gguf", "fast")


# --------------------------------------------------------------------------
# the roles, read from the real local.yaml
# --------------------------------------------------------------------------


def test_the_repo_registers_the_five_roles_with_their_concrete_ids():
    """R1-R3: five roles, each forwarding the concrete id the queue and llama-swap already know."""
    found = {r.name: r.concrete for r in ml.roles(REPO_ROOT)}
    assert found == {name: concrete for name, (concrete, _mode) in ROLE_TABLE.items()}


def test_the_old_five_names_stay_registered_unchanged():
    """R5: every existing name keeps working until mr-retire."""
    pairs = ml.parse_model_list((REPO_ROOT / ml.MODEL_LIST_REL).read_text(encoding="utf-8"))
    assert pairs[:5] == [
        ("qwen36-27b", "openai/qwen36-27b"),
        ("qwen36-27b:nothink", "openai/qwen36-27b:nothink"),
        ("bge-m3", "hosted_vllm/bge-m3"),
        ("bge-m3-f16.gguf", "hosted_vllm/bge-m3"),
        ("qllama/bge-m3:latest", "hosted_vllm/bge-m3"),
    ]
    assert [name for name, _ in pairs] == OLD_NAMES + list(ROLE_TABLE)


def test_every_local_entry_shares_the_queue_api_base():
    """A role pointed anywhere but llm-queue would bypass admission (design section 3.1)."""
    text = (REPO_ROOT / ml.MODEL_LIST_REL).read_text(encoding="utf-8")
    bases = [line.split(":", 1)[1].strip() for line in text.splitlines()
             if line.strip().startswith("api_base:")]
    assert bases == ["http://llm-queue:8080/v1"] * 10


def test_parse_model_list_reads_comments_quotes_and_nested_params():
    text = (
        "# header\n"
        "model_list:\n"
        "  - model_name: \"a\"   # inline\n"
        "    litellm_params:\n"
        "      model: openai/x   # the id\n"
        "      extra_body:\n"
        "        model: not-this-one\n"
        "  - model_name: b\n"
        "    model_info:\n"
        "      id: 1\n"
        "    litellm_params: {}\n"
        "x-requires-profile: local\n"
    )
    assert ml.parse_model_list(text) == [("a", "openai/x"), ("b", "")]


# --------------------------------------------------------------------------
# the derivation
# --------------------------------------------------------------------------


def test_labels_come_from_the_configured_file(scratch):
    labels = by_role(ml.derive_labels(scratch, environ={}))
    assert {role: item.label for role, item in labels.items()} == {
        "local-large": "Qwen3.8-27B Q4_K_M (thinking)",
        "local-large:nothink": "Qwen3.8-27B Q4_K_M (no thinking)",
        "local-small": "Qwen3.8-27B Q4_K_M (no thinking)",
        "local-small:nothink": "Qwen3.8-27B Q4_K_M (no thinking)",
        "local-embed": "bge-m3 f16 (embeddings)",
    }
    assert labels["local-large"].source == "LLAMA_SWAP_QWEN36_27B_MODEL_PATH in inference/.env"
    assert labels["local-large"].host_path.endswith("Qwen3.8-27B-Q4_K_M.gguf")
    assert labels["local-embed"].source == "LLAMA_ARG_MODEL as written in inference/compose/upstreams.yml"


def test_changing_the_path_changes_the_label_with_no_hand_edit(scratch, tmp_path):
    store = tmp_path / "models"
    other = store / "vendor" / "Other-14B-GGUF"
    other.mkdir(parents=True)
    (other / "Other-14B-Q8_0.gguf").write_bytes(b"GGUF")
    write_env(scratch, store, "/models/vendor/Other-14B-GGUF/Other-14B-Q8_0.gguf")
    labels = by_role(ml.derive_labels(scratch, environ={}))
    assert labels["local-large"].label == "Other-14B Q8_0 (thinking)"
    assert labels["local-small"].label == "Other-14B Q8_0 (no thinking)"


def test_an_unset_variable_falls_back_to_the_compose_default_as_compose_does(scratch, tmp_path):
    write_env(scratch, tmp_path / "models", None)
    labels = by_role(ml.derive_labels(scratch, environ={}, check_files=False))
    assert labels["local-large"].container_path == \
        "/models/lmstudio-community/Qwen3.6-27B-GGUF/Qwen3.6-27B-Q4_K_M.gguf"
    assert labels["local-large"].source.startswith("the compose default in inference/compose/upstreams.yml")
    # `:-` means an EMPTY value falls back too
    write_env(scratch, tmp_path / "models", "")
    labels = by_role(ml.derive_labels(scratch, environ={}, check_files=False))
    assert labels["local-large"].label == "Qwen3.6-27B Q4_K_M (thinking)"


def test_the_shell_wins_over_the_env_file_as_compose_does(scratch, tmp_path):
    other = tmp_path / "models" / "x"
    other.mkdir(parents=True)
    (other / "Shell-8B-Q6_K.gguf").write_bytes(b"GGUF")
    labels = by_role(ml.derive_labels(
        scratch, environ={"LLAMA_SWAP_QWEN36_27B_MODEL_PATH": "/models/x/Shell-8B-Q6_K.gguf"}))
    assert labels["local-large"].label == "Shell-8B Q6_K (thinking)"
    assert labels["local-large"].source == "LLAMA_SWAP_QWEN36_27B_MODEL_PATH from the shell environment"


def test_a_missing_model_file_fails_loudly_and_produces_no_label(scratch):
    write_env(scratch, scratch.parent / "models", "/models/unsloth/Gone-27B-GGUF/Gone-27B-Q4_K_M.gguf")
    with pytest.raises(ml.LabelError) as err:
        ml.derive_labels(scratch, environ={})
    assert "local-large" in str(err.value) and "Gone-27B-Q4_K_M.gguf" in str(err.value)
    assert "is not a file on this machine" in str(err.value)


def test_a_path_outside_the_models_bind_fails(scratch):
    write_env(scratch, scratch.parent / "models", "/elsewhere/x-Q4_K_M.gguf")
    with pytest.raises(ml.LabelError, match="not under llama-cpp-upstream's /models bind"):
        ml.derive_labels(scratch, environ={})


def test_a_role_forwarding_an_id_no_upstream_serves_fails(scratch):
    path = scratch / ml.MODEL_LIST_REL
    path.write_text(path.read_text(encoding="utf-8").replace(
        "model: openai/qwen36-27b\n      api_base: http://llm-queue:8080/v1\n      api_key: dummy\n  - model_name: "
        "local-large:nothink", "model: openai/qwen99-typo\n      api_base: http://llm-queue:8080/v1\n"
        "      api_key: dummy\n  - model_name: local-large:nothink"), encoding="utf-8")
    with pytest.raises(ml.LabelError, match="local-large forwards 'qwen99-typo', which no upstream serves"):
        ml.derive_labels(scratch, environ={})


def test_a_variable_with_no_value_and_no_default_fails(scratch):
    path = scratch / ml.UPSTREAMS_REL
    text = path.read_text(encoding="utf-8")
    start = text.index("- LLAMA_SWAP_QWEN36_27B_MODEL_PATH=")
    end = text.index("\n", start)
    path.write_text(text[:start] + "- LLAMA_SWAP_QWEN36_27B_MODEL_PATH=${LLAMA_SWAP_QWEN36_27B_MODEL_PATH}"
                    + text[end:], encoding="utf-8")
    write_env(scratch, scratch.parent / "models", None)
    with pytest.raises(ml.LabelError, match="LLAMA_SWAP_QWEN36_27B_MODEL_PATH resolves to nothing"):
        ml.derive_labels(scratch, environ={})


def test_a_missing_env_file_given_explicitly_fails(scratch):
    with pytest.raises(ml.LabelError, match="does not exist"):
        ml.derive_labels(scratch, env_file=scratch / "nope.env", environ={})


@pytest.mark.parametrize("raw,env,value", [
    ("${A:-d}", {}, "d"), ("${A:-d}", {"A": ""}, "d"), ("${A-d}", {"A": ""}, ""), ("${A-d}", {}, "d"),
    ("${A}", {"A": "x"}, "x"), ("$A/b", {"A": "x"}, "x/b"), ("$$A", {"A": "x"}, "$A"),
    ("${LM:-../../data/models/gguf}:/models:ro", {"LM": "C:\\m"}, "C:\\m:/models:ro"),
])
def test_interpolation_follows_compose(raw, env, value):
    assert ml.interpolate(raw, env) == value


def test_a_required_variable_that_is_unset_fails():
    with pytest.raises(ml.LabelError, match="A is not set"):
        ml.interpolate("${A:?set it}", {})


def test_the_env_example_gives_the_documented_default():
    """inference/.env.example documents Qwen3.6-27B-Q4_K_M (the file a stranger starts from)."""
    labels = by_role(ml.derive_labels(REPO_ROOT, env_file=REPO_ROOT / "inference" / ".env.example",
                                      environ={}, check_files=False))
    assert labels["local-large"].label == "Qwen3.6-27B Q4_K_M (thinking)"
    assert labels["local-embed"].label == "bge-m3 f16 (embeddings)"
    assert labels["local-large"].host_path == ""


def test_the_cli_prints_labels_and_fails_with_exit_1_and_no_label(scratch):
    out = io.StringIO()
    assert ml.main(["--root", str(scratch), "--json"], out=out) == 0
    rows = json.loads(out.getvalue())
    assert [r["role"] for r in rows] == list(ROLE_TABLE)
    write_env(scratch, scratch.parent / "models", "/models/none/None-1B-Q4_0.gguf")
    out = io.StringIO()
    assert ml.main(["--root", str(scratch)], out=out) == 1
    assert out.getvalue().startswith("labels: FAILED - role local-large:")
    assert "No label was produced." in out.getvalue() and "(thinking)" not in out.getvalue()


def test_llama_swap_carries_no_hand_typed_name():
    """Artifact (3): nothing hand-typed remains that can drift from the model file."""
    text = (REPO_ROOT / ml.LLAMA_SWAP_REL).read_text(encoding="utf-8")
    names = [line for line in text.splitlines() if line.strip().startswith("name:")]
    assert names == []
    assert set(ml.parse_llama_swap_models(text)) == {"qwen36-35b-a3b", "qwen36-27b-baseline", "qwen36-27b"}


# --------------------------------------------------------------------------
# the Open WebUI sync, against an in-memory Open WebUI
# --------------------------------------------------------------------------


ADMIN_KEY = "sk-test-admin"


class FakeOwui:
    """The four admin-API calls, as Open WebUI 0.11.0 answers them.

    Rows are {id: row}; GET /model answers 404 for an unknown id and returns the
    row WITH its access grants, create refuses a taken id, update REPLACES the grants
    with the list it is sent and answers 500 when `access_grants` is absent or null
    (0.11.0's router re-validates None against `list` - measured by the drill against
    a real disposable 0.11.0), and a key other than ADMIN_KEY gets 401. Every call is
    recorded; `writes` lists the POSTs.
    """

    def __init__(self, rows=None, healthy=True):
        self.rows = copy.deepcopy(rows or {})
        self.grants = {rid: [{"principal_type": "group", "principal_id": "g1", "permission": "read"}]
                       for rid in self.rows}
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, dict]] = []
        self.healthy = healthy

    def __call__(self, method, url, headers, body, timeout):
        parsed = urllib.parse.urlparse(url)
        self.calls.append((method, parsed.path + ("?" + parsed.query if parsed.query else "")))
        if parsed.path == "/health":
            return (200, '{"status":true}') if self.healthy else (0, "ConnectionRefusedError")
        if headers.get("Authorization") != f"Bearer {ADMIN_KEY}":
            return 401, '{"detail":"Not authenticated"}'
        if method == "GET" and parsed.path == "/api/v1/models/model":
            rid = urllib.parse.parse_qs(parsed.query)["id"][0]
            if rid not in self.rows:
                return 404, '{"detail":"We could not find what you\'re looking for :/"}'
            grants = [{"id": f"g{n}", "resource_type": "model", "resource_id": rid, "created_at": 1, **g}
                      for n, g in enumerate(self.grants.get(rid, []))]
            return 200, json.dumps({**self.rows[rid], "access_grants": grants, "write_access": True})
        payload = json.loads(body.decode("utf-8"))
        self.writes.append((parsed.path, payload))
        if parsed.path == "/api/v1/models/create":
            if payload["id"] in self.rows:
                return 401, '{"detail":"Uh-oh! This id is already registered."}'
            row = {"id": payload["id"], "user_id": "admin", "base_model_id": payload.get("base_model_id"),
                   "name": payload["name"], "meta": payload["meta"], "params": payload["params"],
                   "is_active": payload.get("is_active", True), "created_at": 1, "updated_at": 1}
            self.rows[payload["id"]] = row
            if payload.get("access_grants") is not None:
                self.grants[payload["id"]] = payload["access_grants"]
            return 200, json.dumps(row)
        if parsed.path == "/api/v1/models/model/update":
            if payload.get("access_grants") is None:
                return 500, "Internal Server Error"
            row = self.rows[payload["id"]]
            row.update({k: payload[k] for k in ("base_model_id", "name", "meta", "params", "is_active")
                        if k in payload})
            row["updated_at"] += 1
            self.grants[payload["id"]] = payload["access_grants"]
            return 200, json.dumps(row)
        return 405, ""


def _row(rid, name, **over):
    row = {"id": rid, "user_id": "admin", "base_model_id": None, "name": name,
           "meta": {"profile_image_url": "/static/favicon.png", "capabilities": {"vision": True}, "tags": []},
           "params": {"function_calling": "native"}, "is_active": True, "created_at": 1, "updated_at": 1}
    row.update(over)
    return row


def _labels():
    return [ml.RoleLabel(role, concrete, mode, f"Qwen3.8-27B Q4_K_M ({mode})" if mode != "embeddings"
                         else "bge-m3 f16 (embeddings)", "/models/x.gguf", "", "test")
            for role, (concrete, mode) in ROLE_TABLE.items()]


def test_the_sync_sets_every_role_name_then_changes_nothing_on_a_second_run():
    others = {"qwen36-27b": _row("qwen36-27b", "Qwen 3.6 27B"), "bge-m3": _row("bge-m3", "bge-m3"),
              "writer": _row("writer", "Writer", base_model_id="qwen36-27b")}
    stale = {"local-large": _row("local-large", "Qwen 3.6 27B", is_active=False)}
    owui = FakeOwui({**others, **stale})
    before = copy.deepcopy(owui.rows)

    first = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui)
    assert [(c.role, c.action) for c in first] == [
        ("local-large", "renamed"), ("local-large:nothink", "created"), ("local-small", "created"),
        ("local-small:nothink", "created"), ("local-embed", "created")]
    assert owui.rows["local-large"]["name"] == "Qwen3.8-27B Q4_K_M (thinking)"
    assert owui.rows["local-small"]["name"] == "Qwen3.8-27B Q4_K_M (no thinking)"
    assert owui.rows["local-embed"]["name"] == "bge-m3 f16 (embeddings)"
    # the renamed row keeps everything but its name, and its access grants
    kept = {k: v for k, v in owui.rows["local-large"].items() if k not in ("name", "updated_at")}
    assert kept == {k: v for k, v in before["local-large"].items() if k not in ("name", "updated_at")}
    assert owui.grants["local-large"] == [{"principal_type": "group", "principal_id": "g1", "permission": "read"}]
    renames = [payload for path, payload in owui.writes if path.endswith("/model/update")]
    assert [p["access_grants"] for p in renames] == [
        [{"principal_type": "group", "principal_id": "g1", "permission": "read"}]]
    # no other row was touched
    assert {rid: owui.rows[rid] for rid in others} == others

    writes = len(owui.writes)
    second = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui)
    assert [c.action for c in second] == ["unchanged"] * 5
    assert len(owui.writes) == writes, "the second run wrote something"


def test_a_created_row_is_a_base_model_row_owned_by_the_key_holder():
    owui = FakeOwui()
    ml.sync_owui(_labels()[:1], "http://owui:8080/", ADMIN_KEY, owui)
    path, payload = owui.writes[0]
    assert path == "/api/v1/models/create"
    assert payload["base_model_id"] is None and payload["is_active"] is True and payload["params"] == {}
    assert payload["id"] == "local-large" and payload["name"] == "Qwen3.8-27B Q4_K_M (thinking)"


def test_a_dry_run_reads_and_writes_nothing():
    owui = FakeOwui({"local-large": _row("local-large", "old")})
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, dry_run=True)
    assert [c.action for c in changes] == ["would-rename"] + ["would-create"] * 4
    assert owui.writes == []
    assert ml.describe(changes[0]) == "local-large: would-rename 'old' -> 'Qwen3.8-27B Q4_K_M (thinking)'"


def test_a_role_id_that_is_a_preset_is_refused_not_rewritten():
    owui = FakeOwui({"local-small": _row("local-small", "My preset", base_model_id="qwen36-27b")})
    with pytest.raises(ml.OwuiError, match="is a PRESET on 'qwen36-27b'"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui)
    assert owui.rows["local-small"]["name"] == "My preset"
    # what was done before the refusal is named
    assert [p for p, _ in owui.writes] == ["/api/v1/models/create", "/api/v1/models/create"]


def test_a_non_admin_or_wrong_key_is_named():
    owui = FakeOwui()
    with pytest.raises(ml.OwuiError, match="must be an ADMIN user's API key"):
        ml.sync_owui(_labels(), "http://owui:8080", "sk-wrong", owui)
    with pytest.raises(ml.OwuiError, match="OWUI_ADMIN_API_KEY is empty"):
        ml.sync_owui(_labels(), "http://owui:8080", "", owui)
    assert owui.writes == []


def test_wait_ready_polls_health_and_gives_up():
    owui = FakeOwui(healthy=False)
    slept = []
    assert ml.wait_ready("http://owui:8080", owui, 0, slept.append) is False
    assert ml.wait_ready("http://owui:8080", FakeOwui(), 0, slept.append) is True


def test_a_row_returned_without_its_grants_is_refused_not_stripped():
    owui = FakeOwui({"local-large": _row("local-large", "old")})
    real = owui.__call__

    def no_grants(method, url, headers, body, timeout):
        status, text = real(method, url, headers, body, timeout)
        if method == "GET" and "/api/v1/models/model" in url and status == 200:
            data = json.loads(text)
            data.pop("access_grants")
            text = json.dumps(data)
        return status, text
    with pytest.raises(ml.OwuiError, match="without its access grants"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, no_grants)
    assert owui.rows["local-large"]["name"] == "old" and owui.writes == []
