"""Hermetic tests for scripts/stack/model_labels.py (model-roles, 2026-09-28).

No Docker daemon and no Open WebUI. The label generator derives from compose's render;
these tests hand it renders shaped exactly as `docker compose config --format json`
gives them, and the few tests that ask the REAL compose CLI (no daemon; DOCKER_HOST
dead) skip where there is none. The Open WebUI
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
import os
import posixpath
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import NamedTuple

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
# a scratch world: the REAL local.yaml and llama-swap config, a scratch model store,
# and a RENDER shaped exactly like `docker compose config --format json` gives it
# (services -> environment map, volumes -> [{type, source, target, read_only}])
# --------------------------------------------------------------------------

QWEN38 = "/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf"


class World(NamedTuple):
    root: Path
    store: Path
    embed: Path


@pytest.fixture
def world(tmp_path: Path) -> World:
    root = tmp_path / "repo"
    for rel in (ml.MODEL_LIST_REL, ml.LLAMA_SWAP_REL):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, root / rel)
    store = tmp_path / "models"
    (store / "unsloth" / "Qwen3.8-27B-GGUF").mkdir(parents=True)
    (store / "unsloth" / "Qwen3.8-27B-GGUF" / "Qwen3.8-27B-Q4_K_M.gguf").write_bytes(b"GGUF")
    embed = tmp_path / "embeddings"
    embed.mkdir()
    (embed / "bge-m3-f16.gguf").write_bytes(b"GGUF")
    return World(root, store, embed)


def make_render(w: World, chat=QWEN38, embed_model="/models/bge-m3-f16.gguf", drop=()) -> dict:
    def bind(src, dst):
        return {"type": "bind", "source": str(src), "target": dst, "read_only": True, "bind": {}}
    services = {
        "llm-gateway": {"environment": {"COMPOSE_PROFILES": "local"},
                        "volumes": [bind(w.root / "inference/config/litellm/model_list", "/app/conf.d")]},
        "llama-cpp-upstream": {"command": ["-config", "/app/config.yaml"],
                               "environment": {"LLAMA_SWAP_QWEN36_27B_MODEL_PATH": chat},
                               "volumes": [bind(w.store, "/models"),
                                           bind(w.root / ml.LLAMA_SWAP_REL, "/app/config.yaml")]},
        "llama-cpp-embed-upstream": {"environment": {"LLAMA_ARG_MODEL": embed_model},
                                     "volumes": [bind(w.embed, "/models")]},
    }
    for key in drop:
        services.pop(key)
    return {"name": "inference", "services": services}


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
    ("Mistral-7B-Instruct-v0.3.Q4_K_M.gguf", "thinking", "Mistral-7B-Instruct-v0.3 Q4_K_M (thinking)"),
    ("model.Q4_K_M.gguf", "thinking", "model Q4_K_M (thinking)"),
    ("x-q4_k_m.gguf", "thinking", "x q4_k_m (thinking)"),
    ("x-TQ1_0.gguf", "thinking", "x TQ1_0 (thinking)"),
    ("x-fp16.gguf", "embeddings", "x fp16 (embeddings)"),
    ("Qwen3.8-27B.gguf", "thinking", "Qwen3.8-27B (thinking)"),
    ("Qwen3.8.gguf", "thinking", "Qwen3.8 (thinking)"),
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
# the derivation, from compose's render
# --------------------------------------------------------------------------


def test_labels_come_from_the_file_compose_renders(world):
    labels = by_role(ml.derive_labels(make_render(world)))
    assert {role: item.label for role, item in labels.items()} == {
        "local-large": "Qwen3.8-27B Q4_K_M (thinking)",
        "local-large:nothink": "Qwen3.8-27B Q4_K_M (no thinking)",
        "local-small": "Qwen3.8-27B Q4_K_M (no thinking)",
        "local-small:nothink": "Qwen3.8-27B Q4_K_M (no thinking)",
        "local-embed": "bge-m3 f16 (embeddings)",
    }
    assert labels["local-large"].source == "LLAMA_SWAP_QWEN36_27B_MODEL_PATH as compose renders llama-cpp-upstream"
    assert labels["local-large"].host_path.endswith("Qwen3.8-27B-Q4_K_M.gguf")
    assert labels["local-embed"].source == "LLAMA_ARG_MODEL as compose renders llama-cpp-embed-upstream"


def test_changing_the_rendered_path_changes_the_label_with_no_hand_edit(world):
    other = world.store / "vendor" / "Other-14B-GGUF"
    other.mkdir(parents=True)
    (other / "Other-14B-Q8_0.gguf").write_bytes(b"GGUF")
    labels = by_role(ml.derive_labels(make_render(world, chat="/models/vendor/Other-14B-GGUF/Other-14B-Q8_0.gguf")))
    assert labels["local-large"].label == "Other-14B Q8_0 (thinking)"
    assert labels["local-small"].label == "Other-14B Q8_0 (no thinking)"


def test_a_missing_model_file_fails_loudly_and_produces_no_label(world):
    with pytest.raises(ml.LabelError) as err:
        ml.derive_labels(make_render(world, chat="/models/unsloth/Gone-27B-GGUF/Gone-27B-Q4_K_M.gguf"))
    assert "local-large" in str(err.value) and "Gone-27B-Q4_K_M.gguf" in str(err.value)
    assert "is not a file on this machine" in str(err.value)


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("path", ["/elsewhere/x-Q4_K_M.gguf", "/models/../x/Esc-1B-Q4_0.gguf"])
def test_a_path_that_leaves_the_models_bind_fails(world, tmp_path, path, check):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "Esc-1B-Q4_0.gguf").write_bytes(b"GGUF")
    with pytest.raises(ml.LabelError, match="not under llama-cpp-upstream's /models bind|leads outside the /models"):
        ml.derive_labels(make_render(world, chat=path), check_files=check)


@pytest.mark.parametrize("path", ["/models/..\\outside/Esc-1B-Q4_0.gguf",
                                  "/models/a\\..\\..\\outside\\Esc-1B-Q4_0.gguf",
                                  "/models/unsloth\\Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf"])
@pytest.mark.parametrize("check", [True, False])
def test_a_backslash_in_the_container_path_is_refused(world, path, check):
    with pytest.raises(ml.LabelError, match="contains a backslash"):
        ml.derive_labels(make_render(world, chat=path), check_files=check)


def test_a_role_forwarding_an_id_no_upstream_serves_fails(world):
    path = world.root / ml.MODEL_LIST_REL
    path.write_text(path.read_text(encoding="utf-8").replace(
        "model: openai/qwen36-27b\n      api_base: http://llm-queue:8080/v1\n      api_key: dummy\n  - model_name: "
        "local-large:nothink", "model: openai/qwen99-typo\n      api_base: http://llm-queue:8080/v1\n"
        "      api_key: dummy\n  - model_name: local-large:nothink"), encoding="utf-8")
    with pytest.raises(ml.LabelError, match="local-large forwards 'qwen99-typo', which no upstream serves"):
        ml.derive_labels(make_render(world))


@pytest.mark.parametrize("value", ["", None])
def test_a_variable_the_render_leaves_empty_fails(world, value):
    with pytest.raises(ml.LabelError, match="LLAMA_SWAP_QWEN36_27B_MODEL_PATH is not set in llama-cpp-upstream"):
        ml.derive_labels(make_render(world, chat=value))


@pytest.mark.parametrize("service", ["llm-gateway", "llama-cpp-upstream", "llama-cpp-embed-upstream"])
def test_a_render_without_a_needed_service_fails(world, service):
    with pytest.raises(ml.LabelError, match=f"the render has no {service} service"):
        ml.derive_labels(make_render(world, drop=[service]))


# --- the render itself: compose is asked, and there is no fallback ----------------------


def test_the_render_is_compose_config_of_the_inference_plane_with_local():
    cmd = ml.render_command(Path("/r"), Path("/r/inference/.env.example"))
    assert cmd[:3] == ["docker", "compose", "-f"] and cmd[3].replace("\\", "/").endswith("inference/docker-compose.yml")
    assert cmd[4:] == ["--env-file", str(Path("/r/inference/.env.example")), "--profile", "local",
                       "config", "--format", "json"]


@pytest.mark.parametrize("result,why", [
    ((127, "", "FileNotFoundError: docker"), "exit 127"),
    ((1, "", "line 1: unexpected character"), "unexpected character"),
    ((0, "not json", ""), "is not JSON"),
    ((0, "{}", ""), "has no services"),
])
def test_no_render_means_no_label(world, result, why):
    """compose unavailable, a failed render or garbage: a LabelError - never a guess or a default."""
    calls = []

    def run(cmd, cwd):
        calls.append(cmd)
        return result
    with pytest.raises(ml.LabelError, match=why):
        ml.render_inference(world.root, run=run)
    assert calls and calls[0][-3:] == ["config", "--format", "json"]


def _compose_available():
    import subprocess
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Shapes of the path line in an env file that attempts 1-3's own .env reader read DIFFERENTLY from
# compose (tester attempt 3's table). With compose as the only reader, each gives compose's answer:
# the rendered value (for form-feed / lone-CR that is the compose DEFAULT - compose's own reading,
# so it is also what the upstream loads), or no label at all when compose refuses the file.
ENV_SHAPES = [
    ("plain", "X={p}\n"),
    ("export-tab", "export\tX={p}\n"),
    ("colon", "X: {p}\n"),
    ("bom-at-byte-0", "FIRST:\ufeffX={p}\n"),
    ("bom-mid-file", "\ufeffX={p}\n"),
    ("inline-comment", "X={p} # note\n"),
    ("utf16", "UTF16:X={p}\n"),
    ("bom-line2", "A=1\n\ufeffX={p}\n"),
    ("form-feed", "A=foo\fX={p}\n"),
    ("lone-cr", "A=1\rX={p}\r"),
    ("EXPORT", "EXPORT X={p}\n"),
]


@pytest.mark.skipif(not _compose_available(), reason="needs the docker compose CLI (no daemon): CI and the host")
@pytest.mark.parametrize("name,shape", ENV_SHAPES, ids=[s[0] for s in ENV_SHAPES])
def test_the_label_is_whatever_compose_renders_for_any_env_shape(tmp_path, name, shape):
    """The REAL inference compose file, rendered by the REAL compose (read-only, DOCKER_HOST dead):
    the label names exactly the file compose resolves, or there is no label."""
    import os
    import subprocess
    example = (REPO_ROOT / "inference/.env.example").read_text(encoding="utf-8")
    example = "\n".join(line for line in example.splitlines()
                        if not line.startswith("LLAMA_SWAP_QWEN36_27B_MODEL_PATH="))
    line = shape.replace("X", "LLAMA_SWAP_QWEN36_27B_MODEL_PATH").format(p=QWEN38)
    env_file = tmp_path / "shape.env"
    if line.startswith("UTF16:"):
        env_file.write_bytes((example + "\n" + line[len("UTF16:"):]).encode("utf-16"))
    elif line.startswith("FIRST:"):
        env_file.write_bytes((line[len("FIRST:"):] + example + "\n").encode("utf-8"))
    else:
        env_file.write_bytes((example + "\n" + line).encode("utf-8"))

    def run(cmd, cwd):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLAMA_SWAP_", "COMPOSE_"))}
        env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    try:
        render = ml.render_inference(REPO_ROOT, env_file, run)
    except ml.LabelError as exc:
        assert "exited" in str(exc) or "exit" in str(exc)
        return   # compose refused the file: no label - and never the default's
    rendered = render["services"]["llama-cpp-upstream"]["environment"]["LLAMA_SWAP_QWEN36_27B_MODEL_PATH"]
    labels = by_role(ml.derive_labels(render, check_files=False))
    assert labels["local-large"].container_path == posixpath.normpath(rendered)
    assert labels["local-large"].label == ml.label_for(rendered, "thinking")


# --- symlinks: followed as the container follows them; the label is the file reached ----


def _link(link: Path, target, is_dir=False):
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError as exc:   # Windows without the symlink privilege; CI (Linux) runs these
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_a_relative_link_inside_the_store_is_labelled_by_the_file_it_reaches(world):
    real = world.store / "real" / "Inside-7B-Q8_0.gguf"
    real.parent.mkdir()
    real.write_bytes(b"GGUF")
    _link(world.store / "links" / "Claims-70B-Q2_K.gguf", Path("..") / "real" / "Inside-7B-Q8_0.gguf")
    labels = by_role(ml.derive_labels(make_render(world, chat="/models/links/Claims-70B-Q2_K.gguf")))
    assert labels["local-large"].label == "Inside-7B Q8_0 (thinking)"
    assert labels["local-large"].host_path.endswith("Inside-7B-Q8_0.gguf")


def test_a_link_with_a_host_absolute_target_is_refused_even_inside_the_store(world):
    real = world.store / "real" / "Inside-7B-Q8_0.gguf"
    real.parent.mkdir()
    real.write_bytes(b"GGUF")
    _link(world.store / "links" / "Abs-7B-Q8_0.gguf", real.resolve())
    with pytest.raises(ml.LabelError, match="host-absolute path"):
        ml.derive_labels(make_render(world, chat="/models/links/Abs-7B-Q8_0.gguf"))


def test_a_relative_link_that_climbs_out_of_the_store_is_refused(world, tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "Out-1B-Q4_0.gguf").write_bytes(b"GGUF")
    _link(world.store / "Out-1B-Q4_0.gguf", Path("..") / "outside" / "Out-1B-Q4_0.gguf")
    with pytest.raises(ml.LabelError, match="leads outside the /models store"):
        ml.derive_labels(make_render(world, chat="/models/Out-1B-Q4_0.gguf"))


def test_a_directory_link_is_followed_like_a_file_link(world):
    _link(world.store / "alias", Path("unsloth"), is_dir=True)
    labels = by_role(ml.derive_labels(make_render(world, chat="/models/alias/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf")))
    assert labels["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"


def test_a_symlink_loop_is_refused(world):
    _link(world.store / "a.gguf", Path("b.gguf"))
    _link(world.store / "b.gguf", Path("a.gguf"))
    with pytest.raises(ml.LabelError, match="symlink loop"):
        ml.derive_labels(make_render(world, chat="/models/a.gguf"))


# --- the CLI ----------------------------------------------------------------------------


def test_the_cli_prints_labels_from_a_saved_render_and_fails_with_exit_1_and_no_label(world, tmp_path):
    saved = tmp_path / "render.json"
    saved.write_text(json.dumps(make_render(world)), encoding="utf-8")
    out = io.StringIO()
    assert ml.main(["--root", str(world.root), "--render", str(saved), "--json"], out=out) == 0
    assert [r["role"] for r in json.loads(out.getvalue())] == list(ROLE_TABLE)
    saved.write_text(json.dumps(make_render(world, chat="/models/none/None-1B-Q4_0.gguf")), encoding="utf-8")
    out = io.StringIO()
    assert ml.main(["--root", str(world.root), "--render", str(saved)], out=out) == 1
    assert out.getvalue().startswith("labels: FAILED - role local-large:")
    assert "No label was produced." in out.getvalue() and "(thinking)" not in out.getvalue()


def test_the_cli_renders_with_compose_and_refuses_without_it(world):
    seen = []

    def run(cmd, cwd):
        seen.append(cmd)
        return 127, "", "docker: not found"
    out = io.StringIO()
    assert ml.main(["--root", str(world.root)], out=out, run=run) == 1
    assert "labels: FAILED - `docker compose ... config`" in out.getvalue() and seen


def test_the_cli_survives_a_stream_that_cannot_encode_the_label(world, tmp_path):
    """X4: the CLI's cp1252 fallback (`_say`) - a redirected PowerShell 5.1 pipe."""
    name = "\u6a21\u578b-7B-Q4_0.gguf"
    (world.store / "cjk").mkdir()
    (world.store / "cjk" / name).write_bytes(b"GGUF")
    saved = tmp_path / "render.json"
    saved.write_text(json.dumps(make_render(world, chat=f"/models/cjk/{name}")), encoding="utf-8")
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
    assert ml.main(["--root", str(world.root), "--render", str(saved)], out=stream) == 0
    assert "?-7B Q4_0 (thinking)" in raw.getvalue().decode("cp1252")


def test_the_env_example_gives_the_documented_default():
    """inference/.env.example documents Qwen3.6-27B-Q4_K_M - asked of compose when it is available."""
    if not _compose_available():
        pytest.skip("needs the docker compose CLI")
    import os
    import subprocess

    def run(cmd, cwd):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLAMA_SWAP_", "COMPOSE_"))}
        env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    render = ml.render_inference(REPO_ROOT, REPO_ROOT / "inference/.env.example", run)
    labels = by_role(ml.derive_labels(render, check_files=False))
    assert labels["local-large"].label == "Qwen3.6-27B Q4_K_M (thinking)"
    assert labels["local-embed"].label == "bge-m3 f16 (embeddings)"


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
    # every row is read and validated BEFORE any write: the refusal wrote nothing, although
    # local-large and local-large:nothink (read before local-small) needed writing
    assert owui.writes == []


@pytest.mark.parametrize("bad_role", list(ROLE_TABLE))
def test_a_refusal_on_any_role_writes_nothing_at_all(bad_role):
    """A preset on ANY role id - first, middle or last - refuses before a single write."""
    rows = {"local-large": _row("local-large", "stale")}
    rows[bad_role] = _row(bad_role, "My preset", base_model_id="qwen36-27b")
    owui = FakeOwui(rows)
    before = copy.deepcopy(owui.rows)
    with pytest.raises(ml.OwuiError, match="Nothing was written"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui)
    assert owui.writes == [] and owui.rows == before
    assert {m for m, _ in owui.calls if _ != "/health"} == {"GET"}


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


# --------------------------------------------------------------------------
# attempts 3-4: `Model-2024-Q1`, and the pass-1/pass-2 race
# --------------------------------------------------------------------------


@pytest.mark.parametrize("filename,label", [
    ("Model-2024-Q1.gguf", "Model-2024-Q1 (thinking)"),
    ("x-Q4.gguf", "x-Q4 (thinking)"),
    ("x-Q2_K.gguf", "x Q2_K (thinking)"),
])
def test_a_q_without_an_underscore_is_part_of_the_name(filename, label):
    assert ml.label_for(filename, "thinking") == label


def _racing(owui, after_gets, change):
    """A request that applies `change` once pass 1 has read every role row."""
    real = owui.__call__
    gets = []

    def request(method, url, headers, body, timeout):
        if method == "GET" and "/api/v1/models/model" in url:
            gets.append(url)
            if len(gets) == after_gets + 1:
                change()
        return real(method, url, headers, body, timeout)
    return request


def test_a_row_that_becomes_a_preset_between_read_and_write_is_left_alone():
    """N3: local-small becomes a PRESET (with a grant) after pass 1 read it."""
    owui = FakeOwui({"local-large": _row("local-large", "stale"), "local-small": _row("local-small", "old")})

    def edit():
        owui.rows["local-small"].update(base_model_id="qwen36-27b", params={"system": "mine"}, updated_at=99)
        owui.grants["local-small"].append({"principal_type": "group", "principal_id": "g2",
                                           "permission": "write"})
    with pytest.raises(ml.OwuiError, match="local-small row changed in Open WebUI while this sync ran"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, _racing(owui, len(ROLE_TABLE), edit))
    assert owui.rows["local-small"]["base_model_id"] == "qwen36-27b"
    assert owui.rows["local-small"]["params"] == {"system": "mine"}
    assert {"principal_type": "group", "principal_id": "g2", "permission": "write"} in owui.grants["local-small"]
    # a row written before the change carries its correct new label
    assert owui.rows["local-large"]["name"] == "Qwen3.8-27B Q4_K_M (thinking)"


def test_a_grant_added_between_read_and_write_is_not_dropped():
    owui = FakeOwui({"local-large": _row("local-large", "stale")})

    def grant():
        owui.grants["local-large"].append({"principal_type": "user", "principal_id": "*", "permission": "read"})
    with pytest.raises(ml.OwuiError, match="changed in Open WebUI"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, _racing(owui, len(ROLE_TABLE), grant))
    assert owui.writes == []
    assert {"principal_type": "user", "principal_id": "*", "permission": "read"} in owui.grants["local-large"]


def test_a_row_created_by_someone_else_between_read_and_write_is_left_alone():
    owui = FakeOwui()

    def create():
        owui.rows["local-large"] = _row("local-large", "made by hand")
    with pytest.raises(ml.OwuiError, match="local-large row changed"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, _racing(owui, len(ROLE_TABLE), create))
    assert owui.writes == [] and owui.rows["local-large"]["name"] == "made by hand"


@pytest.mark.parametrize("field,value", [
    ("name", "renamed by hand"),                 # R3
    ("updated_at", 12345),                       # R4 - the commonest concurrent edit
    ("base_model_id", "qwen36-27b"),             # R5
    ("meta", {"description": "edited in the same second"}),
    ("params", {"temperature": 0.1}),
    ("is_active", False),
])
def test_each_fingerprint_component_alone_stops_the_write(field, value):
    """ONE field changes between pass 1 and the write, nothing else (updated_at included, since
    Open WebUI stores it in whole seconds): the row is refused and left as the other party left it."""
    owui = FakeOwui({"local-large": _row("local-large", "stale")})

    def edit():
        owui.rows["local-large"][field] = value
    with pytest.raises(ml.OwuiError, match="local-large row changed in Open WebUI"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, _racing(owui, len(ROLE_TABLE), edit))
    assert owui.writes == []
    assert owui.rows["local-large"][field] == value


# --------------------------------------------------------------------------
# attempt 5 (tester attempt 4): `..` after a directory link, junctions, the rendered
# command, hostile renders, and the mutants that survived
# --------------------------------------------------------------------------


def test_dotdot_after_a_directory_link_is_resolved_as_the_kernel_does(world):
    """Row A: `linkdir -> sub/deeper`; `/models/linkdir/../X-7B-Q8_0.gguf` is `sub/X-7B-Q8_0.gguf`
    in the container (the link is followed BEFORE the `..`), which links to B-13B - not the store
    root's X (-> A-7B) a lexical `normpath` would pick."""
    for rel in ("A-7B-Q8_0.gguf", "sub/B-13B-Q4_K_M.gguf"):
        (world.store / rel).parent.mkdir(parents=True, exist_ok=True)
        (world.store / rel).write_bytes(b"GGUF")
    (world.store / "sub" / "deeper").mkdir()
    _link(world.store / "linkdir", Path("sub") / "deeper", is_dir=True)
    _link(world.store / "X-7B-Q8_0.gguf", Path("A-7B-Q8_0.gguf"))
    _link(world.store / "sub" / "X-7B-Q8_0.gguf", Path("B-13B-Q4_K_M.gguf"))
    labels = by_role(ml.derive_labels(make_render(world, chat="/models/linkdir/../X-7B-Q8_0.gguf")))
    assert labels["local-large"].label == "B-13B Q4_K_M (thinking)"
    assert labels["local-large"].host_path.endswith("B-13B-Q4_K_M.gguf")


def test_a_reparse_point_that_is_not_a_symlink_is_refused(world, monkeypatch):
    """Row B, on any OS: whatever `_is_reparse_point` says is a junction is refused, not followed."""
    real = ml._is_reparse_point
    monkeypatch.setattr(ml, "_is_reparse_point", lambda p: p.name == "unsloth" or real(p))
    with pytest.raises(ml.LabelError, match="junction/reparse point"):
        ml.derive_labels(make_render(world))


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist only on Windows")
def test_a_windows_junction_in_the_store_is_refused(world, tmp_path):
    """Row B, for real: `is_symlink()` is False for a junction; the container cannot open it."""
    import subprocess
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "Out-7B-Q8_0.gguf").write_bytes(b"GGUF")
    link = world.store / "j"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip(f"mklink /J failed: {made.stdout} {made.stderr}")
    try:
        assert not link.is_symlink() and ml._is_reparse_point(link)
        with pytest.raises(ml.LabelError, match="junction/reparse point"):
            ml.derive_labels(make_render(world, chat="/models/j/Out-7B-Q8_0.gguf"))
    finally:
        os.rmdir(link)   # removes the junction only, never its target
    assert (outside / "Out-7B-Q8_0.gguf").exists()


def _embed(render, **spec):
    render["services"]["llama-cpp-embed-upstream"].update(spec)
    return render


def test_an_embed_model_flag_in_the_rendered_command_wins_over_the_env(world):
    (world.embed / "Other-Embed-Q8_0.gguf").write_bytes(b"GGUF")
    for command in (["--model", "/models/Other-Embed-Q8_0.gguf"], ["-m", "/models/Other-Embed-Q8_0.gguf"],
                    ["--port", "8080", "--model=/models/Other-Embed-Q8_0.gguf"]):
        labels = by_role(ml.derive_labels(_embed(make_render(world), command=command)))
        assert labels["local-embed"].label == "Other-Embed Q8_0 (embeddings)", command
        assert labels["local-embed"].source.startswith("`--model` in llama-cpp-embed-upstream's rendered command")


@pytest.mark.parametrize("spec", [
    {"command": ["-hf", "org/repo"]},
    {"command": ["--model-url", "https://x/y.gguf"]},
    {"command": ["--hf-repo=org/repo"]},
    {"environment": {"LLAMA_ARG_MODEL": "/models/bge-m3-f16.gguf", "LLAMA_ARG_HF_REPO": "org/repo"}},
])
def test_an_embed_model_this_module_cannot_label_is_refused(world, spec):
    with pytest.raises(ml.LabelError, match="not a /models file this module can label"):
        ml.derive_labels(_embed(make_render(world), **spec))


def test_llama_swaps_rendered_config_flag_picks_the_config(world):
    alt = world.root / "alt.yaml"
    alt.write_text((world.root / ml.LLAMA_SWAP_REL).read_text(encoding="utf-8").replace(
        "${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}", "/models/alt/Alt-9B-Q5_K_M.gguf"), encoding="utf-8")
    (world.store / "alt").mkdir()
    (world.store / "alt" / "Alt-9B-Q5_K_M.gguf").write_bytes(b"GGUF")
    render = make_render(world)
    up = render["services"]["llama-cpp-upstream"]
    up["command"] = ["-config", "/app/alt.yaml"]
    up["volumes"].append({"type": "bind", "source": str(alt), "target": "/app/alt.yaml", "read_only": True})
    assert by_role(ml.derive_labels(render))["local-large"].label == "Alt-9B Q5_K_M (thinking)"
    up["command"] = ["-config", "/app/unbound.yaml"]
    with pytest.raises(ml.LabelError, match="no bind mount at /app/unbound.yaml"):
        ml.derive_labels(render)


def test_a_mount_at_the_target_that_is_not_a_bind_does_not_count(world):
    render = make_render(world)
    for mount in render["services"]["llama-cpp-upstream"]["volumes"]:
        if mount["target"] == "/models":
            mount["type"] = "volume"
    with pytest.raises(ml.LabelError, match="no bind mount at /models"):
        ml.derive_labels(render)


def test_the_list_form_of_a_rendered_environment_is_read(world):
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"] = [f"LLAMA_SWAP_QWEN36_27B_MODEL_PATH={QWEN38}"]
    assert by_role(ml.derive_labels(render))["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"


def test_a_failed_render_with_output_is_still_a_failure(world):
    good = json.dumps(make_render(world))
    with pytest.raises(ml.LabelError, match="exit 1"):
        ml.render_inference(world.root, run=lambda cmd, cwd: (1, good, "warning: something"))


def _hostile(world):
    base = make_render(world)
    cases = {
        "deep-nesting": "[" * 100000 + "]" * 100000,
        "services-list": json.dumps({"services": []}),
        "service-a-string": json.dumps({"services": {**base["services"], "llama-cpp-upstream": "x"}}),
    }
    for name, (svc, key, value) in {
        "environment-a-string": ("llama-cpp-upstream", "environment", "LLAMA=1"),
        "environment-an-int": ("llama-cpp-upstream", "environment", 7),
        "env-value-an-int": ("llama-cpp-upstream", "environment", {"LLAMA_SWAP_QWEN36_27B_MODEL_PATH": 7}),
        "volumes-an-int": ("llama-cpp-upstream", "volumes", 7),
        "volumes-a-dict": ("llama-cpp-upstream", "volumes", {"a": 1}),
        "command-an-int": ("llama-cpp-upstream", "command", 7),
        "command-with-an-int": ("llama-cpp-embed-upstream", "command", ["--model", 7]),
        "gateway-volumes-a-string": ("llm-gateway", "volumes", "x"),
    }.items():
        r = json.loads(json.dumps(base))
        r["services"][svc][key] = value
        cases[name] = json.dumps(r)
    for name, (svc, field, value) in {
        "source-an-int": ("llama-cpp-upstream", "source", 7),
        "source-relative": ("llama-cpp-upstream", "source", "relative/models"),
        "source-empty": ("llama-cpp-upstream", "source", ""),
        "target-a-list": ("llama-cpp-upstream", "target", ["/models"]),
    }.items():
        r = json.loads(json.dumps(base))
        r["services"][svc]["volumes"][0][field] = value
        cases[name] = json.dumps(r)
    cases["flag-without-value"] = json.dumps(_embed(json.loads(json.dumps(base)), command=["--model"]))
    cases["not-an-object"] = "7"
    cases["bom-prefixed"] = "\ufeff" + json.dumps(base)
    return cases


def test_every_hostile_render_is_a_named_refusal_never_a_traceback(world, tmp_path):
    for name, text in _hostile(world).items():
        saved = tmp_path / f"{name}.json"
        saved.write_text(text, encoding="utf-8")
        out = io.StringIO()
        code = ml.main(["--root", str(world.root), "--render", str(saved)], out=out)
        assert code == 1, (name, out.getvalue())
        assert out.getvalue().startswith("labels: FAILED - "), (name, out.getvalue())
        assert "(thinking)" not in out.getvalue(), name
