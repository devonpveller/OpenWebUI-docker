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
import re
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


def swap_env(model_path) -> dict:
    """Every `${env.*}` the real llama-swap config makes llama-swap substitute (ALL entries - one bad
    value anywhere makes it refuse the whole config), as compose renders them, with the model path given."""
    text = (REPO_ROOT / ml.LLAMA_SWAP_REL).read_text(encoding="utf-8")
    refs = getattr(ml, "swap_env_refs", None)   # getattr: the file also runs against an older module
    names = refs(text) if refs else re.findall(r"\$\{env\.([A-Z0-9_]+)\}", text)
    env = {v: "1" for v in names}
    env["LLAMA_SWAP_QWEN36_27B_MODEL_PATH"] = model_path
    return env


def make_render(w: World, chat=QWEN38, embed_model="/models/bge-m3-f16.gguf", drop=()) -> dict:
    def bind(src, dst):
        return {"type": "bind", "source": str(src), "target": dst, "read_only": True, "bind": {}}
    services = {
        "llm-gateway": {"environment": {"COMPOSE_PROFILES": "local"},
                        "volumes": [bind(w.root / "inference/config/litellm/model_list", "/app/conf.d")]},
        "llama-cpp-upstream": {"command": ["-config", "/app/config.yaml"],
                               "environment": swap_env(chat),
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
    assert labels["local-large"].source == ("LLAMA_SWAP_QWEN36_27B_MODEL_PATH as compose renders "
                                            "llama-cpp-upstream, through llama-swap entry 'qwen36-27b'")
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
    assert "is not a directory on this machine" in str(err.value) or "is not a file on this machine" in str(err.value)


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("path", ["/elsewhere/x-Q4_K_M.gguf", "/models/../x/Esc-1B-Q4_0.gguf"])
def test_a_path_that_leaves_the_models_bind_fails(world, tmp_path, path, check):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "Esc-1B-Q4_0.gguf").write_bytes(b"GGUF")
    with pytest.raises(ml.LabelError, match="not under llama-cpp-upstream's /models bind|`.` or `..` segment"):
        ml.derive_labels(make_render(world, chat=path), check_files=check)


@pytest.mark.parametrize("path", ["/models/..\\outside/Esc-1B-Q4_0.gguf",
                                  "/models/a\\..\\..\\outside\\Esc-1B-Q4_0.gguf",
                                  "/models/unsloth\\Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf"])
@pytest.mark.parametrize("check", [True, False])
def test_a_backslash_in_the_container_path_is_refused(world, path, check):
    # through llama-swap the lexer rule (a backslash) refuses first; either refusal is right
    with pytest.raises(ml.LabelError, match="character Windows treats specially|a backslash"):
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
    _link(world.store / "Claims-70B-Q2_K.gguf", Path("real") / "Inside-7B-Q8_0.gguf")
    labels = by_role(ml.derive_labels(make_render(world, chat="/models/Claims-70B-Q2_K.gguf")))
    assert labels["local-large"].label == "Inside-7B Q8_0 (thinking)"
    assert labels["local-large"].host_path.endswith("Inside-7B-Q8_0.gguf")


def test_a_link_with_a_host_absolute_target_is_refused_even_inside_the_store(world):
    real = world.store / "real" / "Inside-7B-Q8_0.gguf"
    real.parent.mkdir()
    real.write_bytes(b"GGUF")
    _link(world.store / "links" / "Abs-7B-Q8_0.gguf", real.resolve())
    with pytest.raises(ml.LabelError, match="host-absolute path"):
        ml.derive_labels(make_render(world, chat="/models/links/Abs-7B-Q8_0.gguf"))


def test_a_link_whose_target_has_a_dotdot_is_refused(world, tmp_path):
    """Links lead DOWN from their own directory only: any `..` in a target is refused, so none
    can climb out of the store (or reach a sibling whose resolution depends on the kernel)."""
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "Out-1B-Q4_0.gguf").write_bytes(b"GGUF")
    _link(world.store / "Out-1B-Q4_0.gguf", Path("..") / "outside" / "Out-1B-Q4_0.gguf")
    with pytest.raises(ml.LabelError, match="`.` or `..` segment"):
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


def test_dotdot_after_a_directory_link_is_refused_not_emulated(world):
    """Row A: `linkdir -> sub/deeper`; `/models/linkdir/../X-7B-Q8_0.gguf`. Attempt 5 emulated the
    kernel (links before `..`); attempt 6 REFUSES any `..` segment - nothing to get wrong."""
    for rel in ("A-7B-Q8_0.gguf", "sub/B-13B-Q4_K_M.gguf"):
        (world.store / rel).parent.mkdir(parents=True, exist_ok=True)
        (world.store / rel).write_bytes(b"GGUF")
    (world.store / "sub" / "deeper").mkdir()
    _link(world.store / "linkdir", Path("sub") / "deeper", is_dir=True)
    _link(world.store / "X-7B-Q8_0.gguf", Path("A-7B-Q8_0.gguf"))
    _link(world.store / "sub" / "X-7B-Q8_0.gguf", Path("B-13B-Q4_K_M.gguf"))
    with pytest.raises(ml.LabelError, match="`.` or `..` segment"):
        ml.derive_labels(make_render(world, chat="/models/linkdir/../X-7B-Q8_0.gguf"))


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
                    ["--port", "8080", "-m", "/models/bge-m3-f16.gguf", "--model", "/models/Other-Embed-Q8_0.gguf"]):
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
    render["services"]["llama-cpp-upstream"]["environment"] = [f"{k}={v}" for k, v in swap_env(QWEN38).items()]
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


# --------------------------------------------------------------------------
# attempt 6 (tester attempt 5): REFUSE, DON'T EMULATE - paths, flags, the timeout, survivors
# --------------------------------------------------------------------------

REFUSED_PATHS = [
    ("/models/nosuch/../A-7B-Q8_0.gguf", "`.` or `..` segment"),
    ("/models/A-7B-Q8_0.gguf/../B-13B-Q4_K_M.gguf", "`.` or `..` segment"),
    ("/models/A-7B-Q8_0.gguf/", "`.` or `..` segment"),
    ("/models/A-7B-Q8_0.gguf/.", "`.` or `..` segment"),
    ("/models//A-7B-Q8_0.gguf", "`.` or `..` segment"),
    ("/models/sub./X-7B-Q8_0.gguf", "ends in `.` or a space"),
    ("/models/sub /X-7B-Q8_0.gguf", "ends in `.` or a space"),
    ("/models/sub::$INDEX_ALLOCATION/X-7B-Q8_0.gguf", "character Windows treats specially"),
    ("/models/X-7B-Q8_0.gguf:stream", "character Windows treats specially"),
    ("/models/a<b-Q8_0.gguf", "character Windows treats specially"),
    ("/models/a\tb-Q8_0.gguf", "character Windows treats specially"),
    ("/models/CON/X-7B-Q8_0.gguf", "Windows device name"),
    ("/models/nul.gguf", "Windows device name"),
    ("/models/LONGDI~1/X-7B-Q8_0.gguf", "8.3 short name"),
]


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("path,why", REFUSED_PATHS, ids=[p for p, _ in REFUSED_PATHS])
def test_a_path_the_host_and_the_container_could_read_differently_is_refused(world, path, why, check):
    """Each row of tester attempt 5's disagreement table (and its neighbours) is REFUSED, whatever
    is on disk - from the env, and from the embed command."""
    for rel in ("A-7B-Q8_0.gguf", "B-13B-Q4_K_M.gguf", "sub/X-7B-Q8_0.gguf"):
        (world.store / rel).parent.mkdir(parents=True, exist_ok=True)
        (world.store / rel).write_bytes(b"GGUF")
    # through llama-swap's cmd a whitespace refusal comes first (llama-swap splits on it)
    with pytest.raises(ml.LabelError, match=re.escape(why) + "|contains whitespace"):
        ml.derive_labels(make_render(world, chat=path), check_files=check)
    embed_path = path.replace("/models/", "/models/", 1)
    with pytest.raises(ml.LabelError):
        ml.derive_labels(_embed(make_render(world), command=["-m", embed_path]), check_files=check)


def test_a_link_target_with_dotdot_or_a_windows_only_name_is_refused(world):
    (world.store / "A-7B-Q8_0.gguf").write_bytes(b"GGUF")
    _link(world.store / "L1.gguf", Path("nosuch") / ".." / "A-7B-Q8_0.gguf")
    with pytest.raises(ml.LabelError, match="`.` or `..` segment"):
        ml.derive_labels(make_render(world, chat="/models/L1.gguf"))


def test_every_component_before_the_last_must_be_a_directory_and_the_last_a_file(world):
    (world.store / "A-7B-Q8_0.gguf").write_bytes(b"GGUF")
    with pytest.raises(ml.LabelError, match="is not a directory on this machine"):
        ml.derive_labels(make_render(world, chat="/models/A-7B-Q8_0.gguf/B-13B-Q4_K_M.gguf"))
    (world.store / "adir.gguf").mkdir()
    with pytest.raises(ml.LabelError, match="is not a file on this machine"):
        ml.derive_labels(make_render(world, chat="/models/adir.gguf"))


def test_a_file_link_used_as_a_directory_is_refused(world):
    (world.store / "A-7B-Q8_0.gguf").write_bytes(b"GGUF")
    _link(world.store / "looks-like-dir", Path("A-7B-Q8_0.gguf"))
    with pytest.raises(ml.LabelError, match="is not a directory on this machine"):
        ml.derive_labels(make_render(world, chat="/models/looks-like-dir/X-7B-Q8_0.gguf"))


@pytest.mark.parametrize("flag", ["--hf_repo", "--model_url", "--hf_file", "--docker_repo", "--models-dir",
                                  "--models_preset", "--embd-gemma-default", "--fim_qwen_7b_default", "-hfr"])
def test_a_remote_model_flag_is_refused_in_any_spelling_llama_cpp_accepts(world, flag):
    """llama.cpp treats `_` as `-` in a LONG flag: `--hf_repo` downloads exactly as `--hf-repo`."""
    with pytest.raises(ml.LabelError, match="not a /models file this module can label"):
        ml.derive_labels(_embed(make_render(world), command=[flag, "org/repo"]))


@pytest.mark.parametrize("command", [["--model=/models/bge-m3-f16.gguf"], ["-m=/models/bge-m3-f16.gguf"],
                                     ["--model_x", "y", "--model=/models/bge-m3-f16.gguf"]])
def test_a_model_flag_in_equals_form_is_refused(world, command):
    """llama-server rejects `--model=x` (measured, tester attempt 5): a plane that does not start."""
    with pytest.raises(ml.LabelError, match="`--flag=value` form is refused"):
        ml.derive_labels(_embed(make_render(world), command=command))


@pytest.mark.parametrize("command,why", [
    (["-config=/app/config.yaml"], "`--flag=value` form is refused"),
    (["--config=/app/config.yaml"], "`--flag=value` form is refused"),
    (["-config", "config.yaml"], "is relative"),
    (["-config", "alt.yaml"], "is relative"),
    ([], "has no `-config`"),
    (["-config", "/app/../app/config.yaml"], "`.` or `..` segment"),
])
def test_llama_swaps_config_flag_is_refused_unless_plain_and_absolute(world, command, why):
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["command"] = command
    with pytest.raises(ml.LabelError, match=re.escape(why)):
        ml.derive_labels(render)


def test_the_last_config_flag_wins(world):
    """Kills the `_flag` first-wins mutant on the chat side too."""
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["command"] = ["-config", "/app/nope.yaml", "-config", "/app/config.yaml"]
    assert by_role(ml.derive_labels(render))["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"


@pytest.mark.parametrize("service", ["llama-cpp-upstream", "llama-cpp-embed-upstream"])
def test_an_entrypoint_override_is_refused(world, service):
    render = make_render(world)
    render["services"][service]["entrypoint"] = ["/app/llama-server", "-m", "/models/Other-Embed-Q8_0.gguf"]
    with pytest.raises(ml.LabelError, match="has an `entrypoint`"):
        ml.derive_labels(render)


HELP = Path(__file__).resolve().parent / "fixtures" / "llama-server-help.txt"


def test_the_refused_flags_cover_every_remote_model_source_in_the_pinned_llama_server():
    """The fixture is `llama-server --help` from the pinned ghcr.io/ggml-org/llama.cpp:server-cuda
    (image sha256:d5b92ecf..., captured 2026-09-29 in a --network none container). Every flag or
    env var there that loads a MODEL from a URL, a repo, a directory, a preset or a built-in default
    must be refused (the multimodal projector URL, `--mmproj-url`, loads no model and is not)."""
    text = HELP.read_text(encoding="utf-8")
    flags, envs = set(), set()
    for line in text.splitlines():
        head = line[:40]   # the help's flag column
        names = re.findall(r"(?<![\w-])(--?[a-z][a-z0-9.-]*[a-z0-9])", head)
        if re.search(r"model-url|docker-repo|hf-repo|hf-file|models-dir|models-preset|-default\b", head) \
                or re.search(r"^\s*-hf[a-z]*,", head):
            flags |= {n for n in names if n.startswith("-")}
        envs |= set(re.findall(r"env: (LLAMA_ARG_(?:MODEL_URL|DOCKER_REPO|HF[A-Z_]*|HFD_REPO|MODELS_DIR|"
                               r"MODELS_PRESET))\b", line))
    flags -= {"--hf-token", "-hft"}
    envs -= {"LLAMA_ARG_HF_TOKEN"}
    assert "--hf-repo" in flags and "--embd-gemma-default" in flags and "LLAMA_ARG_HF_REPO" in envs
    for flag in sorted(flags):
        spec = {"command": [flag, "x"], "environment": {"LLAMA_ARG_MODEL": "/models/bge-m3-f16.gguf"}}
        with pytest.raises(ml.LabelError, match="not a /models file"):
            ml._embed_model(spec)
    for env in sorted(envs):
        spec = {"command": [], "environment": {"LLAMA_ARG_MODEL": "/models/bge-m3-f16.gguf", env: "x"}}
        with pytest.raises(ml.LabelError, match="not a /models file"):
            ml._embed_model(spec)


# --- the survivors of tester attempt 5 ---------------------------------------------------


def test_the_reparse_attribute_alone_marks_a_junction(monkeypatch, tmp_path):
    """Without os.path.isjunction (Python < 3.12) the FILE_ATTRIBUTE_REPARSE_POINT bit decides."""
    monkeypatch.delattr(ml.os.path, "isjunction", raising=False)

    class Stat:
        st_file_attributes = 0x400
    monkeypatch.setattr(ml.os, "lstat", lambda p: Stat())
    assert ml._is_reparse_point(tmp_path) is True
    Stat.st_file_attributes = 0x10
    assert ml._is_reparse_point(tmp_path) is False


def test_a_relative_bind_source_is_named_as_such(world):
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["volumes"][0]["source"] = "relative/models"
    with pytest.raises(ml.LabelError, match="RELATIVE source"):
        ml.derive_labels(render)


def test_a_non_string_environment_value_is_named_as_such(world):
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"]["LLAMA_SWAP_QWEN36_27B_MODEL_PATH"] = 7
    with pytest.raises(ml.LabelError, match="is not a string"):
        ml.derive_labels(render)


def test_an_unexpected_error_inside_the_derivation_becomes_a_label_error(world, monkeypatch):
    def boom(render, check):
        raise TypeError("an internal shape nobody planned for")
    monkeypatch.setattr(ml, "_derive", boom)
    with pytest.raises(ml.LabelError, match="not the shape `docker compose config` writes"):
        ml.derive_labels(make_render(world))


# --------------------------------------------------------------------------
# attempt 7 (tester attempt 6): values substituted into llama-swap's cmd, the tree kill,
# stdin, the bounded grace wait, `/modelsX`
# --------------------------------------------------------------------------


def _swap_cmd(world, old, new):
    path = world.root / ml.LLAMA_SWAP_REL
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.mark.parametrize("var,value", [
    ("LLAMA_SWAP_QWEN36_27B_CTX_SIZE", "4096 --model /models/B-13B-Q4_K_M.gguf"),
    ("LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET", "4096 --hf_repo org/repo"),
    ("LLAMA_SWAP_QWEN36_27B_MODEL_PATH", "/models/unsloth/Qwen3.8 27B/x-Q4_K_M.gguf"),
    ("LLAMA_SWAP_QWEN36_27B_BATCH", "1024\t--model\t/models/B-13B-Q4_K_M.gguf"),
])
def test_a_value_with_whitespace_substituted_into_llama_swaps_cmd_is_refused(world, var, value):
    """llama-swap splits its cmd on whitespace, so such a value becomes extra ARGUMENTS
    (`..._CTX_SIZE=4096 --model /models/B` loads B - measured in the pinned llama-swap image)."""
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"][var] = value
    with pytest.raises(ml.LabelError, match="contains whitespace"):
        ml.derive_labels(render)


@pytest.mark.parametrize("old,new,why", [
    ("--model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
     "--model=${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}", "`--flag=value` form is refused"),
    ("--model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
     "--hf_repo org/repo --model ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}", "not a /models file"),
    ("--model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
     "--ctx-size 1", "has no `--model`/`-m`"),
    ("--model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
     "--model ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH} ${UNKNOWN}", "is not a macro"),
])
def test_llama_swaps_cmd_gets_the_embed_servers_flag_rules(world, old, new, why):
    _swap_cmd(world, old, new)
    with pytest.raises(ml.LabelError, match=re.escape(why)):
        ml.derive_labels(make_render(world))


def test_the_last_model_flag_in_llama_swaps_cmd_wins(world):
    """parse_llama_swap_models used to take the FIRST `--model`; llama-server loads the last."""
    (world.store / "B-13B-Q4_K_M.gguf").write_bytes(b"GGUF")
    _swap_cmd(world, "--model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
              "--model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}\n      -m /models/B-13B-Q4_K_M.gguf")
    labels = by_role(ml.derive_labels(make_render(world)))
    assert labels["local-large"].label == "B-13B Q4_K_M (thinking)"
    assert labels["local-large"].source == "the cmd of llama-swap entry 'qwen36-27b'"


def test_a_remote_model_env_on_llama_swaps_service_is_refused(world):
    """llama-server inherits llama-swap's environment: LLAMA_ARG_HF_REPO there downloads too."""
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"]["LLAMA_ARG_HF_REPO"] = "org/repo"
    with pytest.raises(ml.LabelError, match="not a /models file"):
        ml.derive_labels(render)


def test_a_llama_swap_entry_with_its_own_env_is_refused(world):
    _swap_cmd(world, "    concurrencyLimit: 0\n", "    concurrencyLimit: 0\n    env:\n      - LLAMA_ARG_MODEL=/models/x.gguf\n")
    with pytest.raises(ml.LabelError, match="has env - not interpreted"):
        ml.derive_labels(make_render(world))


def test_llama_swaps_macros_are_expanded_and_checked(world):
    macros, models = ml.parse_llama_swap_config((world.root / ml.LLAMA_SWAP_REL).read_text(encoding="utf-8"))
    assert "--no-mmap" in macros["common-args"] and "${PORT}" in macros["common-args"]
    assert models["qwen36-27b"].cmd.startswith("llama-server ${common-args}")
    assert set(models) == {"qwen36-35b-a3b", "qwen36-27b-baseline", "qwen36-27b"}


def test_a_path_that_only_starts_like_the_models_bind_is_refused(world):
    with pytest.raises(ml.LabelError, match="not under llama-cpp-upstream's /models bind"):
        ml.derive_labels(make_render(world, chat="/modelsX/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf"),
                         check_files=False)


def _held(lock: Path) -> bool:
    """Is the grandchild's lock still held? Identity by LOCK, never by PID: a lock dies with the
    process that took it, so this can never mistake - or touch - a foreign process that reused a PID."""
    with open(lock, "a+") as f:
        try:
            if sys.platform == "win32":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            return True
    return False


_GRANDCHILD = (
    "import os, sys, time\n"
    "f = open(sys.argv[1], 'a+')\n"
    "if os.name == 'nt':\n"
    "    import msvcrt; f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
    "else:\n"
    "    import fcntl; fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "open(sys.argv[2], 'w').write('ready')\n"
    "time.sleep(float(sys.argv[3]))\n"
)


def _tree_stub(tmp: Path, parent_waits: bool, sleep: float = 15, holds_pipe: bool = True) -> list[str]:
    """A child that starts a grandchild. The grandchild takes a lock (its identity), says `ready`, and
    sleeps `sleep` s - SHORT on purpose: if a mutant leaves it alive it ends by itself; no test ever
    kills anything by PID. `holds_pipe=False` detaches it from the output pipe (a success-path leftover)."""
    lock, ready = tmp / "grandchild.lock", tmp / "grandchild.ready"
    (tmp / "grandchild.py").write_text(_GRANDCHILD, encoding="utf-8")
    redirect = "" if holds_pipe else ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"
    code = ("import subprocess, sys, time, os\n"
            f"subprocess.Popen([sys.executable, {str(tmp / 'grandchild.py')!r}, {str(lock)!r}, {str(ready)!r}, "
            f"{str(sleep)!r}]{redirect})\n"
            f"deadline = time.time() + 10\n"
            f"while not os.path.exists({str(ready)!r}) and time.time() < deadline: time.sleep(0.05)\n"
            + (f"time.sleep({sleep})\n" if parent_waits else ""))
    return [sys.executable, "-c", code]


@pytest.mark.parametrize("parent_waits", [True, False], ids=["parent-alive", "parent-already-exited"])
def test_the_timeout_kills_the_whole_tree_and_returns_promptly(tmp_path, parent_waits):
    """A grandchild holds the pipe. A parent-only kill would leave it alive and return only after the
    grace period; a taskkill /T cannot find it once its parent has exited. Both must be told apart
    from a real tree kill: the grandchild's lock must be FREE (it is dead), and the call back well
    inside timeout+grace."""
    import time
    started = time.monotonic()
    code, out, err = ml.run_bounded(_tree_stub(tmp_path, parent_waits), tmp_path, 2, grace=5)
    took = time.monotonic() - started
    assert (tmp_path / "grandchild.ready").exists(), "the stub never started its grandchild"
    time.sleep(0.5)
    assert code == 124 and b"timed out" in err
    assert took < 2 + 2.5, f"returned after {took:.1f}s - the grace wait ran, so the tree was not killed"
    assert not _held(tmp_path / "grandchild.lock"), "the grandchild survived the timeout"


def test_a_successful_run_leaves_nothing_behind(tmp_path):
    """The child exits 0 but leaves a detached grandchild running: it must not outlive the call
    (Windows: the job closes with KILL_ON_JOB_CLOSE; elsewhere: the process group is killed)."""
    import time
    code, out, err = ml.run_bounded(_tree_stub(tmp_path, False, holds_pipe=False), tmp_path, 20)
    assert code == 0, err
    assert (tmp_path / "grandchild.ready").exists()
    deadline = time.monotonic() + 3
    while _held(tmp_path / "grandchild.lock") and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _held(tmp_path / "grandchild.lock"), "the success path left the grandchild running"


def test_the_wait_after_the_kill_is_bounded_even_when_the_kill_fails(tmp_path, monkeypatch):
    """If nothing could be killed, the call still returns at about timeout + grace (never hangs)."""
    import threading
    import time
    monkeypatch.setattr(ml, "_kill_tree", lambda *a: None)
    monkeypatch.setattr(ml, "_WindowsJob", lambda pid: (_ for _ in ()).throw(OSError("no job in this test")))
    result = {}

    def call():
        started = time.monotonic()
        # short-lived on purpose: with the kill disabled, the stub's processes end on their own
        result["code"] = ml.run_bounded(_tree_stub(tmp_path, True, sleep=8), tmp_path, 1, grace=2)[0]
        result["took"] = time.monotonic() - started
    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(20)
    assert not thread.is_alive(), "run_bounded waited without bound after the kill"
    assert result["code"] == 124 and result["took"] < 1 + 2 + 3


def test_stdin_is_never_inherited(monkeypatch):
    import subprocess
    seen = {}
    real = subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", spy)
    ml.run_bounded([sys.executable, "-c", "pass"], ".", 30)
    assert seen.get("stdin") is subprocess.DEVNULL


# --------------------------------------------------------------------------
# attempt 8 (tester attempt 7): llama-swap's cmd goes through a POSIX-shell LEXER, not a
# whitespace split - quotes group and vanish, a backslash escapes, a newline breaks the config
# --------------------------------------------------------------------------

_Q = chr(39)   # '
_DQ = chr(34)  # "
_BS = chr(92)  # backslash


@pytest.mark.parametrize("var,value", [
    ("LLAMA_SWAP_QWEN36_27B_MODEL_PATH", f"/models/unsloth/Qwen3.8-27B-GGUF/Qwen{_Q}s-27B-Q4_K_M.gguf"),
    ("LLAMA_SWAP_QWEN36_27B_MODEL_PATH", f"/models/unsloth/{_DQ}Qwen3.8-27B-GGUF{_DQ}/x-Q4_K_M.gguf"),
    ("LLAMA_SWAP_QWEN36_27B_CTX_SIZE", f"4096{_Q}"),                        # half of a quote pair ...
    ("LLAMA_SWAP_QWEN36_27B_N_GPU_LAYERS", f"99{_BS}"),                     # an escape
    ("LLAMA_SWAP_QWEN36_35B_BATCH", "1024\n--model"),                       # a newline in ANOTHER entry
    ("LLAMA_SWAP_QWEN36_35B_CTX_SIZE", ""),                                  # unset in ANOTHER entry
    ("LLAMA_SWAP_QWEN36_35B_UBATCH", f"512{_DQ}"),                           # a quote in ANOTHER entry
])
def test_a_value_the_shell_lexer_would_reinterpret_is_refused_anywhere_in_the_config(world, var, value):
    """Rows of tester attempt 7: an apostrophe in the file name, quotes in the path, a quote pair
    across two tunables (it swallows `--n-gpu-layers`), a backslash, and a bad value in ANOTHER
    entry (llama-swap then refuses the whole config). Each refused, no label."""
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"][var] = value
    with pytest.raises(ml.LabelError, match="the llama-swap config: " + re.escape(var)):
        ml.derive_labels(render)


@pytest.mark.parametrize("old,new,why", [
    ("--ctx-size     ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}",
     f"--alias {_Q}x --model /models/B-13B-Q4_K_M.gguf{_Q} --ctx-size ${{env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}}",
     "treat it specially"),
    ("--ctx-size     ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}",
     "--ctx-size ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE} -- --model /models/B-13B-Q4_K_M.gguf", "`--` or a `#`"),
    ("--ctx-size     ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}",
     "--ctx-size ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE} #note --model /models/B-13B-Q4_K_M.gguf", "`--` or a `#`"),
    ("--ctx-size     ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}",
     f"--ctx-size ${{env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}} --alias a{_BS}b", "treat it specially"),
])
def test_a_literal_cmd_word_the_lexer_would_reinterpret_is_refused(world, old, new, why):
    """Quotes, a backslash, `--` and a `#` comment word in the cmd itself - kills X5 (the `#` word)."""
    _swap_cmd(world, old, new)
    with pytest.raises(ml.LabelError, match=re.escape(why)):
        ml.derive_labels(make_render(world))


def _macros(world, text):
    path = world.root / ml.LLAMA_SWAP_REL
    body = path.read_text(encoding="utf-8")
    start = body.index("macros:")
    end = body.index("\nmodels:")
    path.write_text(body[:start] + "macros:\n" + text + body[end:], encoding="utf-8")


def test_a_macro_may_reference_an_earlier_macro(world):
    """X3, nested macros: `common-args` built from an EARLIER macro expands and labels normally."""
    _macros(world, "  port-args: --host 0.0.0.0 --port ${PORT}\n"
                   "  common-args: >-\n    ${port-args}\n    --no-mmap\n")
    assert by_role(ml.derive_labels(make_render(world)))["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"


def test_a_macro_that_references_a_later_macro_is_refused(world):
    _macros(world, "  common-args: >-\n    ${port-args}\n    --no-mmap\n"
                   "  port-args: --host 0.0.0.0 --port ${PORT}\n")
    with pytest.raises(ml.LabelError, match="defined LATER"):
        ml.derive_labels(make_render(world))


def test_a_macro_that_references_an_unknown_name_is_refused(world):
    _macros(world, "  common-args: --no-mmap ${nowhere}\n")
    with pytest.raises(ml.LabelError, match="which is not a macro"):
        ml.derive_labels(make_render(world))


def test_env_references_in_yaml_comments_are_not_config(world):
    """A `${env.*}` in a YAML comment is not substituted by llama-swap and must not be required."""
    refs = ml.swap_env_refs("# ${env.IN_A_COMMENT}\nmodels:\n  m:\n    cmd: |\n      x ${env.REAL}\n"
                            "      # ${env.IN_THE_BLOCK}\n    proxy: http://${env.PROXY} # ${env.TRAILING}\n")
    assert refs == {"REAL", "IN_THE_BLOCK", "PROXY"}
