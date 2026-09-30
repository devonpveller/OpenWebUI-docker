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


def commit_all(root: Path) -> None:
    """Make `root` a throwaway git checkout with everything in it committed: labels are only derived
    from the COMMITTED llama-swap config (operator decision, 2026-09-29). No hooks run here."""
    import subprocess
    nohooks = root / ".git-nohooks"
    nohooks.mkdir(exist_ok=True)
    base = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false",
            "-c", f"core.hooksPath={nohooks}"]
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        # in the repo's OWN config, so the commit and model_labels' `git hash-object` agree
        subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True, capture_output=True)
    subprocess.run([*base, "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*base, "-C", str(root), "commit", "-q", "--allow-empty", "-m", "t"], check=True,
                   capture_output=True)


@pytest.fixture
def world(tmp_path: Path, monkeypatch) -> World:
    root = tmp_path / "repo"
    # the stack root the committed-config check holds the config to (the driver passes its --root)
    monkeypatch.setattr(ml, "STACK_ROOT", root)
    for rel in (ml.MODEL_LIST_REL, ml.LLAMA_SWAP_REL):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, root / rel)
    store = tmp_path / "models"
    (store / "unsloth" / "Qwen3.8-27B-GGUF").mkdir(parents=True)
    (store / "unsloth" / "Qwen3.8-27B-GGUF" / "Qwen3.8-27B-Q4_K_M.gguf").write_bytes(b"GGUF")
    embed = tmp_path / "embeddings"
    embed.mkdir()
    (embed / "bge-m3-f16.gguf").write_bytes(b"GGUF")
    commit_all(root)
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
    with pytest.raises(ml.LabelError, match="character Windows treats specially|a backslash|allowlist"):
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
    ("dollar", "X=/models/A$$B-Q8_0.gguf\n"),
    ("dollar-brace", "X=/models/X-$${{PORT}}-Q8_0.gguf\n"),
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
    # compose's JSON writes a literal `$` as `$$`: the container gets the UN-escaped value, which is what
    # the label must name - and since attempt 9 any `$` at all is refused by the allowlist
    unescaped = rendered.replace("$$", "$")
    if "$" in unescaped:
        with pytest.raises(ml.LabelError, match="allowlist"):
            ml.derive_labels(render, check_files=False)
        return
    labels = by_role(ml.derive_labels(render, check_files=False))
    assert labels["local-large"].container_path == unescaped
    assert labels["local-large"].label == ml.label_for(unescaped, "thinking")


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
    # a non-ASCII name is outside the allowlist since attempt 9: the REFUSAL naming it must still print
    assert ml.main(["--root", str(world.root), "--render", str(saved)], out=stream) == 1
    text = raw.getvalue().decode("cp1252")
    assert text.startswith("labels: FAILED - ") and "allowlist" in text and "??-7B-Q4_0.gguf" in text


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
    a real disposable 0.11.0), and a key other than ADMIN_KEY gets 401. GET /api/models
    lists every base model the connections serve (`listed`, {id: name}) plus the rows,
    hidden ones INCLUDED - Open WebUI 0.11.0 filters `meta.hidden` in the browser, not in
    that endpoint (measured by mr-picker against a disposable 0.11.0). Every call is
    recorded; `writes` lists the POSTs.
    """

    def __init__(self, rows=None, healthy=True, listed=None):
        self.rows = copy.deepcopy(rows or {})
        self.listed = dict(listed or {})
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
        if method == "GET" and parsed.path == "/api/models":
            data = [{"id": rid, "name": name} for rid, name in self.listed.items() if rid not in self.rows]
            data += [{"id": rid, "name": row["name"], "info": row} for rid, row in self.rows.items()]
            return 200, json.dumps({"data": data})
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
    commit_all(world.root)
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
    with pytest.raises(ml.LabelError, match=re.escape(why) + "|contains whitespace|allowlist"):
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
    commit_all(world.root)   # a COMMITTED edit: what follows tests the defence in depth behind rule 1


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
    with pytest.raises(ml.LabelError, match="contains whitespace|allowlist"):
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
    with pytest.raises(ml.LabelError, match="has env - not interpreted|entry key 'env'"):
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
    with pytest.raises(ml.LabelError, match=re.escape(why) + "|allowlist"):
        ml.derive_labels(make_render(world))


def _macros(world, text):
    path = world.root / ml.LLAMA_SWAP_REL
    body = path.read_text(encoding="utf-8")
    start = body.index("macros:")
    end = body.index("\nmodels:")
    path.write_text(body[:start] + "macros:\n" + text + body[end:], encoding="utf-8")
    commit_all(world.root)


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


# --------------------------------------------------------------------------
# attempt 9 (tester attempt 8): an ALLOWLIST for every value and word, and a recognised YAML
# SUBSET for the llama-swap config - every row of tester attempt 8's evidence
# --------------------------------------------------------------------------

_NBSP, _IDEO, _THIN, _OGHAM = chr(0xA0), chr(0x3000), chr(0x2009), chr(0x1680)
_LS, _NEL, _VT, _FS, _C1 = chr(0x2028), chr(0x85), chr(0x0B), chr(0x1C), chr(0x81)

ALLOWLIST_VALUE_ROWS = [
    # (row, variable, value AS COMPOSE RENDERS IT)
    ("e09-C1", "LLAMA_SWAP_QWEN36_35B_CTX_SIZE", f"1{_C1}2"),
    ("e10-FFFE", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", f"40{chr(0xFFFE)}96"),
    ("Y3-control", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", f"40{chr(1)}96"),
    ("Y11-DEL", "LLAMA_SWAP_QWEN36_35B_BATCH", f"10{chr(0x7F)}24"),
    ("q03-NEL", "LLAMA_SWAP_QWEN36_27B_BATCH", f"10{_NEL}24"),
    ("e05-escaped-env-ref", "LLAMA_SWAP_QWEN36_35B_CTX_SIZE", "$${env.UNSETX}"),
    ("e01-escaped-macro", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", "$${common-args}"),
    ("e02-escaped-port", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", "$${PORT}"),
    ("e03-other-entry-var", "LLAMA_SWAP_QWEN36_27B_MODEL_PATH", "/models/$${env.LLAMA_SWAP_QWEN36_35B_MODEL_PATH}.gguf"),
    ("e04-port-in-path", "LLAMA_SWAP_QWEN36_27B_MODEL_PATH", "/models/X-$${PORT}-Q8_0.gguf"),
    ("e06-model-id-in-path", "LLAMA_SWAP_QWEN36_27B_MODEL_PATH", "/models/$${MODEL_ID}-Q8_0.gguf"),
    ("e08-literal-dollar", "LLAMA_SWAP_QWEN36_27B_MODEL_PATH", "/models/A$$B-7B-Q8_0.gguf"),
    ("unescaped-dollar", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", "4$096"),
    ("nbsp", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", f"4096{_NBSP}--model"),
    ("ideographic-space", "LLAMA_SWAP_QWEN36_35B_CTX_SIZE", f"4096{_IDEO}x"),
    ("thin-space", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", f"4096{_THIN}x"),
    ("ogham-space", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", f"4096{_OGHAM}x"),
    ("n17-huge", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", "9" * 200000),
    ("brace", "LLAMA_SWAP_QWEN36_27B_CTX_SIZE", "{4096}"),
]


@pytest.mark.parametrize("row,var,value", ALLOWLIST_VALUE_ROWS, ids=[r[0] for r in ALLOWLIST_VALUE_ROWS])
def test_every_substituted_value_must_match_the_allowlist(world, row, var, value):
    """A value compose renders with `$$` (a literal `$` - llama-swap then RE-EXPANDS `${...}` in it),
    a C0/C1 control, DEL, U+FFFE, any non-ASCII whitespace, a brace or a 200 000-character value:
    refused by the allowlist [A-Za-z0-9_./:,+=@-], whichever entry of the config it belongs to."""
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"][var] = value
    with pytest.raises(ml.LabelError, match="allowlist"):
        ml.derive_labels(render)


@pytest.mark.parametrize("value", ["/models/A$$B-7B-Q8_0.gguf", f"/models/x{_NBSP}y-Q8_0.gguf",
                                   "/models/x y-Q8_0.gguf"])
def test_the_embed_model_path_must_match_the_allowlist_too(world, value):
    """compose renders a literal `$` as `$$`: the embed path is refused rather than read escaped."""
    render = make_render(world)
    render["services"]["llama-cpp-embed-upstream"]["environment"]["LLAMA_ARG_MODEL"] = value
    with pytest.raises(ml.LabelError, match="allowlist"):
        ml.derive_labels(render, check_files=False)


@pytest.mark.parametrize("sep", [_NBSP, _IDEO, _THIN, _OGHAM, _LS, _NEL, _VT, _FS, _C1],
                         ids=["k01-NBSP", "n14c-U3000", "n14e-U2009", "n14f-U1680", "n14b-U2028",
                              "n14d-NEL", "n14g-VT", "n14h-U001C", "q09-C1"])
def test_a_literal_cmd_word_outside_the_allowlist_is_refused(world, sep):
    """k01: `--alias x<NBSP>--model<NBSP>/models/B` - Python's split() separated it (a label for B)
    while llama-swap's lexer kept one word (loads A). Any such character is now refused."""
    (world.store / "B-13B-Q4_K_M.gguf").write_bytes(b"GGUF")
    _swap_cmd(world, "--reasoning-budget ${env.LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET}",
              f"--reasoning-budget ${{env.LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET}} --alias x{sep}--model{sep}"
              f"/models/B-13B-Q4_K_M.gguf")
    with pytest.raises(ml.LabelError, match="allowlist|not in the YAML subset"):
        ml.derive_labels(make_render(world))


def test_a_huge_literal_word_is_refused(world):
    _swap_cmd(world, "--reasoning-budget ${env.LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET}",
              "--reasoning-budget ${env.LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET} --alias " + "a" * 200000)
    with pytest.raises(ml.LabelError, match="allowlist"):
        ml.derive_labels(make_render(world))


def _config_with(world, old, new):
    _swap_cmd(world, old, new)
    return make_render(world)


SUBSET_ROWS = [
    ("n06-multiline-plain-cmd", "    cmd: |\n      llama-server ${common-args}\n      --model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
     "    cmd: llama-server ${common-args} --model ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}\n"
     "      --model /models/B-13B-Q4_K_M.gguf\n      --model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}",
     "multi-line plain scalar"),
    ("n08-multiline-plain-macro", "  common-args: >-\n", "  common-args: --host 0.0.0.0\n    --model /models/B-13B-Q4_K_M.gguf\n  other: >-\n",
     "multi-line plain scalar"),
    ("n09-anchor", "  common-args: >-", "  common-args: &x >-", "macro value"),
    ("n09b-tag", "  common-args: >-", "  common-args: !!str >-", "macro value"),
    ("n09c-keep", "  common-args: >-", "  common-args: >+", "macro value"),
    ("n09d-indent-indicator", "  common-args: >-", "  common-args: >2-", "macro value"),
    ("n10b-keep-block", "  qwen36-35b-a3b:\n    cmd: |", "  qwen36-35b-a3b:\n    cmd: |+", "cmd value"),
    ("n10c-indent-block", "  qwen36-35b-a3b:\n    cmd: |", "  qwen36-35b-a3b:\n    cmd: |2", "cmd value"),
    ("n12-duplicate-cmd", "    concurrencyLimit: 0\n", "    concurrencyLimit: 0\n    cmd: llama-server --model /models/B.gguf\n",
     "duplicate key"),
    ("n12b-duplicate-entry", "\nmodels:\n", "\nmodels:\n  qwen36-27b:\n    cmd: llama-server --model /models/B.gguf\n",
     "duplicate key"),
    ("n13-tab", "listen: 0.0.0.0:8080", "listen:\t0.0.0.0:8080", "a tab"),
    ("n13b-unclosed-quote", "logToStdout: both", 'logToStdout: "both', "top-level key"),
    ("n19-escaped-quote", "logToStdout: both", 'logToStdout: "a \\" #${env.U}"', "top-level key"),
    ("flow-map", "logToStdout: both", "logToStdout: {a: b}", "top-level key"),
    ("flow-seq", "logToStdout: both", "logToStdout: [a, b]", "top-level key"),
    ("alias", "logToStdout: both", "logToStdout: *x", "top-level key"),
    ("sequence", "      setParamsByID:\n", "      setParamsByID:\n        - x\n", "sequence"),
    ("unknown-top-key", "logToStdout: both", "logToStdout: both\nhooks: x", "top-level key"),
    ("per-entry-env", "    concurrencyLimit: 0\n", "    concurrencyLimit: 0\n    env:\n      - A=1\n", "entry key 'env'"),
    ("non-ascii-in-block", "    --no-mmap", f"    --no-mmap{_NBSP}", "non-ASCII"),
]


@pytest.mark.parametrize("row,old,new,why", SUBSET_ROWS, ids=[r[0] for r in SUBSET_ROWS])
def test_a_llama_swap_config_outside_the_recognised_subset_is_refused(world, row, old, new, why):
    """Every YAML shape of tester attempt 8 that the line reader did not parse the way llama-swap does:
    refused, with the line named - never a label read from a misparsed config."""
    with pytest.raises(ml.LabelError, match=re.escape(why)):
        ml.derive_labels(_config_with(world, old, new))


def test_the_live_shaped_config_is_in_the_subset():
    """The repo's own llama-swap config (the one landing deploys) is recognised."""
    ml.check_swap_config_subset((REPO_ROOT / ml.LLAMA_SWAP_REL).read_text(encoding="utf-8"))


def test_a_quoted_filters_key_with_a_colon_is_read_as_one_key():
    ml.check_swap_config_subset('models:\n  m:\n    cmd: llama-server --model /models/a.gguf\n'
                                '    filters:\n      setParamsByID:\n        "${MODEL_ID}:nothink":\n'
                                '          chat_template_kwargs:\n            enable_thinking: false\n')


# --------------------------------------------------------------------------
# attempt 10: ONLY TRUST THE COMMITTED CONFIG (operator decision 2026-09-29), the pin, Z8
# --------------------------------------------------------------------------


def test_the_committed_llama_swap_config_is_accepted(world):
    assert by_role(ml.derive_labels(make_render(world)))["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"


def test_an_uncommitted_edit_of_the_llama_swap_config_is_refused(world):
    """Any byte that differs from HEAD refuses - even a harmless comment."""
    path = world.root / ml.LLAMA_SWAP_REL
    path.write_text(path.read_text(encoding="utf-8") + "# an uncommitted comment\n", encoding="utf-8")
    with pytest.raises(ml.LabelError, match="differs from the committed version.*not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_a_staged_but_uncommitted_edit_is_refused(world):
    import subprocess
    path = world.root / ml.LLAMA_SWAP_REL
    path.write_text(path.read_text(encoding="utf-8") + "# staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(world.root), "add", "-A"], check=True, capture_output=True)
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_an_untracked_llama_swap_config_is_refused(world):
    alt = world.root / "untracked.yaml"
    alt.write_text((world.root / ml.LLAMA_SWAP_REL).read_text(encoding="utf-8"), encoding="utf-8")
    render = make_render(world)
    up = render["services"]["llama-cpp-upstream"]
    up["command"] = ["-config", "/app/untracked.yaml"]
    up["volumes"].append({"type": "bind", "source": str(alt), "target": "/app/untracked.yaml", "read_only": True})
    with pytest.raises(ml.LabelError, match="not tracked by git"):
        ml.derive_labels(render)


def _bind_config(render: dict, source: Path) -> dict:
    for mount in render["services"]["llama-cpp-upstream"]["volumes"]:
        if mount["target"] == "/app/config.yaml":
            mount["source"] = str(source)
    return render


def _git(root: Path, *args, env=None):
    import subprocess
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false",
                           "-c", f"core.hooksPath={root / '.git-nohooks'}", "-C", str(root), *args],
                          check=True, capture_output=True, env=env)


def _other_config(world) -> bytes:
    """The committed config with the 27B entry loading ANOTHER model (label B if it got through)."""
    text = (world.root / ml.LLAMA_SWAP_REL).read_bytes()
    out = text.replace(b"${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}", b"/models/vendor/B-13B-GGUF/B-13B-Q4_K_M.gguf")
    assert out != text
    return out


def test_a_config_outside_the_stack_root_or_without_git_is_refused(world, tmp_path_factory):
    outside = tmp_path_factory.mktemp("loose")   # a sibling of the stack root, not under it
    shutil.copy(world.root / ml.LLAMA_SWAP_REL, outside / "llama-swap.config.yaml")
    with pytest.raises(ml.LabelError, match="not under the stack root"):
        ml.derive_labels(_bind_config(make_render(world), outside / "llama-swap.config.yaml"))
    with pytest.raises(ml.LabelError, match="not a git checkout, or git is unavailable"):
        ml.derive_labels(make_render(world), git=lambda args, cwd: (127, b"", "FileNotFoundError: git"))


def test_a_stack_root_that_is_not_the_checkouts_top_is_refused(world, monkeypatch):
    """git's toplevel must be the stack root itself: a root INSIDE some bigger checkout is refused."""
    inner = world.root / "inference"
    monkeypatch.setattr(ml, "STACK_ROOT", inner)
    with pytest.raises(ml.LabelError, match="not the stack root"):
        ml.derive_labels(make_render(world))


def test_a_config_that_differs_from_the_head_blob_is_refused(world):
    """The comparison is the file's bytes against `git cat-file blob HEAD:<path>`, in Python (kills a
    check that only asks whether the file is tracked). The injected HEAD blob is self-consistent - its
    id is its hash - so it is the byte comparison, not the re-hash, that refuses."""
    real = ml._run_git(["cat-file", "blob", f"HEAD:{ml.LLAMA_SWAP_REL.as_posix()}"], world.root)[1]
    fake = real.replace(b"--no-mmap", b"--mmap")
    assert fake != real

    def git(args, cwd):
        code, out, err = ml._run_git(args, cwd)
        if args[:2] == ["cat-file", "blob"]:
            out = fake
        if args[:1] == ["rev-parse"] and args[-1].startswith("HEAD:"):
            out = (ml._object_id(b"blob", fake, out.decode().strip()) + "\n").encode()
        return code, out, err
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world), git=git)


@pytest.mark.parametrize("relative", [False, True])
def test_a_file_symlink_at_the_config_path_is_refused(world, relative):
    """Tester attempt 10: the link's TARGET (another committed file) was what got hashed -> label B."""
    other = world.root / "inference" / "config" / "other.yaml"
    other.write_bytes(_other_config(world))
    cfg = world.root / ml.LLAMA_SWAP_REL
    commit_all(world.root)
    cfg.unlink()
    _link(cfg, Path("other.yaml") if relative else other)
    commit_all(world.root)   # even a COMMITTED symlink: git's blob is the link text, not the file
    with pytest.raises(ml.LabelError, match="is a symlink, junction or reparse point"):
        ml.derive_labels(make_render(world))


def test_a_directory_symlink_between_the_root_and_the_config_is_refused(world):
    cfg_dir = world.root / "inference" / "config"
    copy = world.root / "copy"
    shutil.copytree(cfg_dir, copy)
    (copy / "llama-swap.config.yaml").write_bytes(_other_config(world))
    commit_all(world.root)
    shutil.rmtree(cfg_dir)
    _link(cfg_dir, copy, is_dir=True)
    with pytest.raises(ml.LabelError, match="is a symlink, junction or reparse point"):
        ml.derive_labels(make_render(world))


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist only on Windows")
def test_a_directory_junction_between_the_root_and_the_config_is_refused(world):
    import subprocess
    cfg_dir = world.root / "inference" / "config"
    copy = world.root / "copy"
    shutil.copytree(cfg_dir, copy)
    commit_all(world.root)
    shutil.rmtree(cfg_dir)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(cfg_dir), str(copy)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip(f"mklink /J failed: {made.stdout} {made.stderr}")
    try:
        with pytest.raises(ml.LabelError, match="is a symlink, junction or reparse point"):
            ml.derive_labels(make_render(world))
    finally:
        os.rmdir(cfg_dir)   # removes the junction only, never its target


@pytest.mark.parametrize("kind", ["nested repo", "gitfile"])
def test_a_nested_repository_between_the_root_and_the_config_is_refused(world, kind):
    """Tester attempt 10: git uses the NEAREST repo - one inside inference/config committed label B."""
    cfg = world.root / ml.LLAMA_SWAP_REL
    cfg.write_bytes(_other_config(world))
    cfg_dir = cfg.parent
    if kind == "nested repo":
        commit_all(cfg_dir)
    else:
        (cfg_dir / ".git").write_text("gitdir: ../../.git\n", encoding="utf-8")
    with pytest.raises(ml.LabelError, match="holds a .git entry"):
        ml.derive_labels(make_render(world))


@pytest.mark.parametrize("var", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"])
def test_git_variables_in_the_callers_environment_are_ignored(world, tmp_path_factory, monkeypatch, var):
    """Tester attempt 10: GIT_DIR (+GIT_WORK_TREE) pointing at another repo where the EDITED config is
    committed labelled B. Every GIT_* variable is removed before git runs."""
    other = tmp_path_factory.mktemp("other")
    (other / ml.LLAMA_SWAP_REL).parent.mkdir(parents=True)
    (other / ml.LLAMA_SWAP_REL).write_bytes(_other_config(world))
    commit_all(other)
    (world.root / ml.LLAMA_SWAP_REL).write_bytes(_other_config(world))   # uncommitted in the stack root
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(world.root))
    if var == "GIT_INDEX_FILE":
        monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    if var == "GIT_CONFIG_COUNT":
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(other))
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))
    # the caller's are gone; only the check's own two are set
    assert {k for k in ml._git_env() if k.upper().startswith("GIT_")} == {"GIT_NO_REPLACE_OBJECTS", "GIT_GRAFT_FILE"}


@pytest.mark.parametrize("where", ["info/attributes", ".gitattributes"])
def test_a_local_clean_filter_cannot_make_an_edit_look_committed(world, tmp_path_factory, where):
    """Tester attempt 10: `hash-object` applied a local `filter.x.clean` that printed the HEAD blob.
    `cat-file blob` + a byte comparison here takes no filter or attribute into account."""
    keep = tmp_path_factory.mktemp("keep") / "head.yaml"
    keep.write_bytes((world.root / ml.LLAMA_SWAP_REL).read_bytes())
    _git(world.root, "config", "filter.x.clean", f'cat "{keep.as_posix()}"')
    line = f"{ml.LLAMA_SWAP_REL.as_posix()} filter=x\n"
    if where == "info/attributes":
        (world.root / ".git" / "info").mkdir(exist_ok=True)
        (world.root / ".git" / "info" / "attributes").write_text(line, encoding="utf-8")
    else:
        (world.root / ".gitattributes").write_text(line, encoding="utf-8")   # uncommitted
    (world.root / ml.LLAMA_SWAP_REL).write_bytes(_other_config(world))
    probe = _git(world.root, "hash-object", "--", ml.LLAMA_SWAP_REL.as_posix()).stdout.strip()
    head = _git(world.root, "rev-parse", f"HEAD:{ml.LLAMA_SWAP_REL.as_posix()}").stdout.strip()
    assert probe == head   # the attack is live: hash-object is fooled
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flags_cannot_hide_an_edit(world, flag):
    _git(world.root, "update-index", flag, ml.LLAMA_SWAP_REL.as_posix())
    (world.root / ml.LLAMA_SWAP_REL).write_bytes(_other_config(world))
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_a_dot_dot_segment_in_the_bound_path_is_refused(world):
    cfg = world.root / "inference" / "config" / ".." / "config" / "llama-swap.config.yaml"
    with pytest.raises(ml.LabelError, match="a `..` segment"):
        ml.derive_labels(_bind_config(make_render(world), Path(str(cfg))))


def test_a_dot_segment_is_normalised_away_before_the_check(world):
    """Exactly what the docs say: pathlib drops `.` segments (and doubled separators) before the
    check sees the path - they name the same directory - so such a bind is the committed file."""
    cfg = str(world.root / ml.LLAMA_SWAP_REL).replace("config", "." + os.sep + "config", 1)
    assert "." + os.sep in cfg and "." not in Path(cfg).parts
    assert by_role(ml.derive_labels(_bind_config(make_render(world), Path(cfg))))["local-large"].label \
        == "Qwen3.8-27B Q4_K_M (thinking)"


def _plain_git_blob(root: Path) -> bytes:
    """`git cat-file blob HEAD:<rel>` as plain git answers it - replace refs honoured."""
    return _git(root, "cat-file", "blob", f"HEAD:{ml.LLAMA_SWAP_REL.as_posix()}").stdout


def test_a_replaced_head_blob_is_not_the_committed_config(world):
    """Tester attempt 11: `git replace <HEAD blob> <edited blob>` - the edited blob in NO commit - made
    plain `cat-file blob HEAD:<rel>` return the edit, and the edit labelled B-13B."""
    rel = ml.LLAMA_SWAP_REL.as_posix()
    head_blob = _git(world.root, "rev-parse", f"HEAD:{rel}").stdout.decode().strip()
    other = _other_config(world)
    (world.root / ml.LLAMA_SWAP_REL).write_bytes(other)
    evil = _git(world.root, "hash-object", "-w", "--", rel).stdout.decode().strip()
    _git(world.root, "replace", head_blob, evil)
    assert _plain_git_blob(world.root) == other   # the attack is live for plain git
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_a_replaced_head_commit_is_not_the_committed_config(world):
    """... and `git replace <HEAD commit> <a commit holding the edit>` after `reset --soft` back to the
    real HEAD: `git status` is even clean, yet HEAD's own tree holds the committed config."""
    real = _git(world.root, "rev-parse", "HEAD").stdout.decode().strip()
    (world.root / ml.LLAMA_SWAP_REL).write_bytes(_other_config(world))
    commit_all(world.root)
    evil = _git(world.root, "rev-parse", "HEAD").stdout.decode().strip()
    _git(world.root, "reset", "-q", "--soft", real)
    _git(world.root, "replace", real, evil)
    assert _git(world.root, "status", "--porcelain", "--", ml.LLAMA_SWAP_REL.as_posix()).stdout.strip() == b""
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_git_runs_without_replace_objects_or_grafts(world):
    """Every git call of the check: `--no-replace-objects -c core.useReplaceRefs=false`, and the
    environment sets GIT_NO_REPLACE_OBJECTS=1 and an empty GIT_GRAFT_FILE after the scrub."""
    seen = []

    def spy(args, cwd):
        seen.append(list(args))
        return ml._run_git(args, cwd)
    ml.derive_labels(make_render(world), git=spy)
    assert seen and ml._GIT_PREFIX == ["--no-replace-objects", "-c", "core.useReplaceRefs=false",
                                       "-c", "core.fsmonitor=false"]
    env = ml._git_env()
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1" and env["GIT_GRAFT_FILE"] == os.devnull
    assert {k for k in env if k.upper().startswith("GIT_")} == {"GIT_NO_REPLACE_OBJECTS", "GIT_GRAFT_FILE"}


def test_a_lone_cr_is_not_normalised_away(world):
    """X2: only CRLF -> LF is normalised. A lone CR is a YAML line break: `# comment\rX: y` is a
    comment AND a key to YAML. A lone CR the commit does not have is a difference, refused."""
    path = world.root / ml.LLAMA_SWAP_REL
    data = path.read_bytes()
    at = data.index(b"#")                     # inside the first comment line
    path.write_bytes(data[:at + 1] + b"\r" + data[at + 1:])
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_the_parsed_bytes_are_the_verified_bytes(world, monkeypatch):
    """X4: the config is read ONCE; what is parsed is what was compared with HEAD's blob. A file that
    changes after the check (every later read returns B) still labels from the verified content."""
    path = (world.root / ml.LLAMA_SWAP_REL).resolve()
    other = _other_config(world)
    reads = []
    real_bytes, real_text = Path.read_bytes, Path.read_text

    def read_bytes(self):
        if self.resolve() == path:
            reads.append("bytes")
            return real_bytes(self) if len(reads) == 1 else other
        return real_bytes(self)

    def read_text(self, *a, **k):
        if self.resolve() == path:
            reads.append("text")
            return other.decode("utf-8")
        return real_text(self, *a, **k)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)
    labels = by_role(ml.derive_labels(make_render(world)))
    assert labels["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"
    assert reads == ["bytes"]


def _committed_config_text() -> str:
    """The llama-swap config as COMMITTED at HEAD in this checkout (not the working copy). In an
    export of a commit (`git archive <sha> | tar -x`, not a checkout of its own) the file on disk IS
    that commit's file, so it is read from disk."""
    import subprocess
    top = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != REPO_ROOT.resolve():
        return (REPO_ROOT / ml.LLAMA_SWAP_REL).read_bytes().decode("utf-8").replace("\r\n", "\n")
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"HEAD:{ml.LLAMA_SWAP_REL.as_posix()}"],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


PINNED_COMMON_ARGS = ["--host", "0.0.0.0", "--port", "${PORT}", "--chat-template-file",
                      "/etc/llama/chat-template.jinja", "--flash-attn", "on", "--no-mmap"]
PINNED_27B_CMD = ["llama-server", "${common-args}", "--model", "${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}"] + [
    w for flag, var in [("--ctx-size", "CTX_SIZE"), ("--n-gpu-layers", "N_GPU_LAYERS"), ("--parallel", "N_PARALLEL"),
                        ("--batch-size", "BATCH"), ("--ubatch-size", "UBATCH"), ("--cache-type-k", "CACHE_TYPE_K"),
                        ("--cache-type-v", "CACHE_TYPE_V"), ("--reasoning-budget", "REASONING_BUDGET"),
                        ("--spec-type", "SPEC_TYPE"), ("--spec-draft-n-max", "SPEC_DRAFT_N_MAX"),
                        ("--spec-draft-type-k", "SPEC_DRAFT_CACHE_TYPE_K"),
                        ("--spec-draft-type-v", "SPEC_DRAFT_CACHE_TYPE_V")]
    for w in (flag, "${env.LLAMA_SWAP_QWEN36_27B_" + var + "}")]


PINNED_EFFECTIVE_LINES = [
    'listen: 0.0.0.0:8080',
    'healthCheckTimeout: 300',
    'includeAliasesInList: true',
    'globalTTL: 0',
    'logToStdout: both',
    'macros:',
    '  common-args: >-',
    '    --host 0.0.0.0 --port ${PORT}',
    '    --chat-template-file /etc/llama/chat-template.jinja',
    '    --flash-attn on',
    '    --no-mmap',
    'models:',
    '  qwen36-35b-a3b:',
    '    cmd: |',
    '      llama-server ${common-args}',
    '      --model        ${env.LLAMA_SWAP_QWEN36_35B_MODEL_PATH}',
    '      --ctx-size     ${env.LLAMA_SWAP_QWEN36_35B_CTX_SIZE}',
    '      --n-gpu-layers ${env.LLAMA_SWAP_QWEN36_35B_N_GPU_LAYERS}',
    '      --parallel     ${env.LLAMA_SWAP_QWEN36_35B_N_PARALLEL}',
    '      --batch-size   ${env.LLAMA_SWAP_QWEN36_35B_BATCH}',
    '      --ubatch-size  ${env.LLAMA_SWAP_QWEN36_35B_UBATCH}',
    '      --cache-type-k ${env.LLAMA_SWAP_QWEN36_35B_CACHE_TYPE_K}',
    '      --cache-type-v ${env.LLAMA_SWAP_QWEN36_35B_CACHE_TYPE_V}',
    '      --reasoning-budget ${env.LLAMA_SWAP_QWEN36_35B_REASONING_BUDGET}',
    '    filters:',
    '      setParamsByID:',
    '        "${MODEL_ID}":',
    '          chat_template_kwargs:',
    '            enable_thinking: true',
    '        "${MODEL_ID}:nothink":',
    '          chat_template_kwargs:',
    '            enable_thinking: false',
    '  qwen36-27b-baseline:',
    '    cmd: |',
    '      llama-server ${common-args}',
    '      --model        /models/lmstudio-community/Qwen3.6-27B-GGUF/Qwen3.6-27B-Q4_K_M.gguf',
    '      --ctx-size     262144',
    '      --n-gpu-layers 99',
    '      --parallel     3',
    '      --batch-size   1024',
    '      --ubatch-size  512',
    '      --cache-type-k q4_0',
    '      --cache-type-v q4_0',
    '      --reasoning-budget 4096',
    '    filters:',
    '      setParamsByID:',
    '        "${MODEL_ID}":',
    '          chat_template_kwargs:',
    '            enable_thinking: true',
    '        "${MODEL_ID}:nothink":',
    '          chat_template_kwargs:',
    '            enable_thinking: false',
    '  qwen36-27b:',
    '    concurrencyLimit: 0',
    '    cmd: |',
    '      llama-server ${common-args}',
    '      --model        ${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}',
    '      --ctx-size     ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}',
    '      --n-gpu-layers ${env.LLAMA_SWAP_QWEN36_27B_N_GPU_LAYERS}',
    '      --parallel     ${env.LLAMA_SWAP_QWEN36_27B_N_PARALLEL}',
    '      --batch-size   ${env.LLAMA_SWAP_QWEN36_27B_BATCH}',
    '      --ubatch-size  ${env.LLAMA_SWAP_QWEN36_27B_UBATCH}',
    '      --cache-type-k ${env.LLAMA_SWAP_QWEN36_27B_CACHE_TYPE_K}',
    '      --cache-type-v ${env.LLAMA_SWAP_QWEN36_27B_CACHE_TYPE_V}',
    '      --reasoning-budget ${env.LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET}',
    '      --spec-type    ${env.LLAMA_SWAP_QWEN36_27B_SPEC_TYPE}',
    '      --spec-draft-n-max ${env.LLAMA_SWAP_QWEN36_27B_SPEC_DRAFT_N_MAX}',
    '      --spec-draft-type-k ${env.LLAMA_SWAP_QWEN36_27B_SPEC_DRAFT_CACHE_TYPE_K}',
    '      --spec-draft-type-v ${env.LLAMA_SWAP_QWEN36_27B_SPEC_DRAFT_CACHE_TYPE_V}',
    '    filters:',
    '      setParamsByID:',
    '        "${MODEL_ID}":',
    '          chat_template_kwargs:',
    '            enable_thinking: true',
    '        "${MODEL_ID}:nothink":',
    '          chat_template_kwargs:',
    '            enable_thinking: false',
]


def test_the_committed_llama_swap_config_is_pinned():
    """THE PIN. The committed config, parsed as llama-swap reads it: its macros, its entries and their
    keys, the role's cmd words and its model flag. A committed change to the config must change this
    test too - and so goes through review. (Rule 1 already refuses any uncommitted difference.)"""
    text = _committed_config_text()
    ml.check_swap_config_subset(text)
    ml.check_swap_env_placement(text)   # every ${env.*} sits inside a block scalar
    macros, models = ml.parse_llama_swap_config(text)
    assert list(macros) == ["common-args"] and macros["common-args"].split() == PINNED_COMMON_ARGS
    assert list(models) == ["qwen36-35b-a3b", "qwen36-27b-baseline", "qwen36-27b"]
    assert {k: sorted(v.keys) for k, v in models.items()} == {
        "qwen36-35b-a3b": ["cmd", "filters"], "qwen36-27b-baseline": ["cmd", "filters"],
        "qwen36-27b": ["cmd", "concurrencyLimit", "filters"]}
    assert models["qwen36-27b"].cmd.split() == PINNED_27B_CMD
    assert ml._flag(models["qwen36-27b"].cmd.split(), ml._EMBED_MODEL_FLAGS) == "${env.LLAMA_SWAP_QWEN36_27B_MODEL_PATH}"
    refs = ml.swap_env_refs(text)
    assert refs == {f"LLAMA_SWAP_QWEN36_27B_{v}" for v in ("MODEL_PATH", "CTX_SIZE", "N_GPU_LAYERS", "N_PARALLEL",
                                                          "BATCH", "UBATCH", "CACHE_TYPE_K", "CACHE_TYPE_V",
                                                          "REASONING_BUDGET", "SPEC_TYPE", "SPEC_DRAFT_N_MAX",
                                                          "SPEC_DRAFT_CACHE_TYPE_K", "SPEC_DRAFT_CACHE_TYPE_V")} | {
        f"LLAMA_SWAP_QWEN36_35B_{v}" for v in ("MODEL_PATH", "CTX_SIZE", "N_GPU_LAYERS", "N_PARALLEL", "BATCH",
                                               "UBATCH", "CACHE_TYPE_K", "CACHE_TYPE_V", "REASONING_BUDGET")}
    # EVERYTHING llama-swap reads, not only what the labels use (tester attempt 10: a flipped `:nothink`
    # filter or an out-of-range concurrencyLimit passed the narrower pin): every line that is not blank
    # or a full-line comment, exactly, in order - top-level settings, concurrencyLimit, the filters.
    effective = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert effective == PINNED_EFFECTIVE_LINES
    # ... and, spelled out, the two the tester found unpinned:
    assert effective.count("    concurrencyLimit: 0") == 1 and sum("concurrencyLimit" in x for x in effective) == 1
    for entry in models:   # the labels say "(no thinking)" for `:nothink` - its filter must say so too
        block = effective[effective.index(f"  {entry}:"):]
        nothink = block.index('        "${MODEL_ID}:nothink":')
        thinking = block.index('        "${MODEL_ID}":')
        assert block[nothink + 2] == "            enable_thinking: false", entry
        assert block[thinking + 2] == "            enable_thinking: true", entry


@pytest.mark.parametrize("value", ["@1", "@x", "4096:", "q4_0:"])
def test_z8_a_value_starting_with_at_or_ending_with_a_colon_is_refused(world, value):
    render = make_render(world)
    render["services"]["llama-cpp-upstream"]["environment"]["LLAMA_SWAP_QWEN36_27B_CTX_SIZE"] = value
    with pytest.raises(ml.LabelError, match="starts with `@` or ends with `:`"):
        ml.derive_labels(render)


def test_z8_an_env_reference_outside_a_block_scalar_is_refused(world):
    """y26b: `${env.X}` in a PLAIN scalar - its substituted value is re-read as YAML. The subset
    accepts this shape; the placement check is what refuses it."""
    _macros(world, "  common-args: --host 0.0.0.0 --port ${PORT} --ctx ${env.LLAMA_SWAP_QWEN36_27B_CTX_SIZE}\n")
    with pytest.raises(ml.LabelError, match="outside a block scalar"):
        ml.derive_labels(make_render(world))
    placement_only = "models:\n  m:\n    cmd: llama-server ${env.X}\n"
    with pytest.raises(ml.LabelError, match="outside a block scalar"):
        ml.check_swap_env_placement(placement_only)


@pytest.mark.parametrize("value", ["@x --no-mmap", "`x", "&a x", "*a", "!t x", "%x", "? x", ", x"])
def test_z8_a_plain_macro_starting_with_a_yaml_indicator_is_refused(value):
    """The subset's first-character check (Z8 survived attempt 9): tested directly on the subset."""
    with pytest.raises(ml.LabelError, match="macro value"):
        ml.check_swap_config_subset(f"macros:\n  common-args: {value}\nmodels:\n  m:\n    cmd: |\n      x\n")


def test_a_crlf_checkout_of_the_committed_config_is_the_committed_version(world):
    """The live checkout (core.autocrlf=true on Windows) holds the config as CRLF over an LF blob:
    `git diff` shows nothing, so it is the committed version. A real edit on top is still refused."""
    import subprocess
    path = world.root / ml.LLAMA_SWAP_REL
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    path.write_bytes(text.encode("utf-8"))
    commit_all(world.root)   # an LF blob, as in the real repo
    subprocess.run(["git", "-C", str(world.root), "config", "core.autocrlf", "true"], check=True, capture_output=True)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    diff = subprocess.run(["git", "-C", str(world.root), "diff", "--quiet", "--", ml.LLAMA_SWAP_REL.as_posix()])
    assert diff.returncode == 0
    assert by_role(ml.derive_labels(make_render(world)))["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"
    path.write_bytes((text + "# edit\n").replace("\n", "\r\n").encode("utf-8"))
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


def test_a_configured_fsmonitor_program_does_not_run(world, tmp_path_factory):
    """Tester attempt 12: a `core.fsmonitor` program in the repo's config RAN during the check (and
    could swap HEAD and the file under it). Every git call passes `-c core.fsmonitor=false`."""
    marker = tmp_path_factory.mktemp("fsm") / "ran"
    script = world.root.parent / "fsmonitor.sh"
    script.write_bytes(f'#!/bin/sh\necho ran >> "{marker.as_posix()}"\nexit 1\n'.encode())
    script.chmod(0o755)
    _git(world.root, "config", "core.fsmonitor", script.as_posix())
    import subprocess
    subprocess.run(["git", "-C", str(world.root), "status", "--porcelain"], capture_output=True)
    subprocess.run(["git", "-C", str(world.root), "ls-files", "--error-unmatch", "--",
                    ml.LLAMA_SWAP_REL.as_posix()], capture_output=True)
    if not marker.exists():
        pytest.skip("this git does not run a core.fsmonitor hook program here - the attack is not live")
    marker.unlink()
    assert by_role(ml.derive_labels(make_render(world)))["local-large"].label == "Qwen3.8-27B Q4_K_M (thinking)"
    assert not marker.exists(), "the fsmonitor program ran during the check"


def test_an_overwritten_loose_blob_is_refused(world):
    """Tester attempt 12: HEAD's loose blob file rewritten in place with other content (git does not
    verify an object on read) returned the edit. The check re-hashes the blob it gets and requires the
    id `rev-parse HEAD:<rel>` names."""
    import zlib
    rel = ml.LLAMA_SWAP_REL.as_posix()
    oid = _git(world.root, "rev-parse", f"HEAD:{rel}").stdout.decode().strip()
    obj = world.root / ".git" / "objects" / oid[:2] / oid[2:]
    assert obj.is_file(), "the fixture's objects are loose"
    evil = _other_config(world)
    obj.chmod(0o644)
    obj.write_bytes(zlib.compress(b"blob %d\0" % len(evil) + evil))
    (world.root / ml.LLAMA_SWAP_REL).write_bytes(evil)
    assert _plain_git_blob(world.root) == evil   # the attack is live for plain git
    with pytest.raises(ml.LabelError, match="does not hash to its id"):
        ml.derive_labels(make_render(world))


def test_the_alternates_variable_is_scrubbed(monkeypatch):
    """GIT_ALTERNATE_OBJECT_DIRECTORIES (another object store searched for HEAD's ids) is a GIT_*
    variable: the scrub removes it."""
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "/elsewhere/objects")
    assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in ml._git_env()


@pytest.mark.parametrize("how", ["final newline dropped", "an LF swapped for a CR"])
def test_the_comparison_is_of_bytes_not_lines(world, how):
    """Y4: a comparison of `splitlines()` would call these equal; YAML and the committed-config rule
    do not. Only CRLF -> LF is normalised."""
    path = world.root / ml.LLAMA_SWAP_REL
    data = path.read_bytes()
    if how == "final newline dropped":
        assert data.endswith(b"\n")
        data = data[:-2] if data.endswith(b"\r\n") else data[:-1]
    else:
        at = data.index(b"\n", data.index(b"#"))   # the end of the first comment line
        data = data[:at - 1] + b"\r" + data[at + 1:] if data[at - 1:at] == b"\r" else data[:at] + b"\r" + data[at + 1:]
    path.write_bytes(data)
    with pytest.raises(ml.LabelError, match="not the blob committed at HEAD"):
        ml.derive_labels(make_render(world))


# --------------------------------------------------------------------------
# mr-picker (2026-09-30): the sync owns the PICKER visibility (`meta.hidden`) of every
# row whose id the local gateway serves - one row per derived label, nothing else.
# Each test below fails at 58bdeb5 (no picker_hidden / served ids / visibility writes).
# --------------------------------------------------------------------------

THINK, NOTHINK, EMBED = ("Qwen3.8-27B Q4_K_M (thinking)", "Qwen3.8-27B Q4_K_M (no thinking)",
                         "bge-m3 f16 (embeddings)")
SERVED = list(ROLE_TABLE) + OLD_NAMES
PUBLIC = {"principal_type": "user", "principal_id": "*", "permission": "read"}


def _live_like():
    """The live table as the coordinator measured it on 2026-09-30: the five role rows with
    today's labels (none hidden), qwen36-27b as 'Qwen 3.6 27B', bge-m3 and bge-m3-f16.gguf
    already hidden, a preset on local-large and on local-small, a pipe row and a cloud row.
    qwen36-27b:nothink and qllama/bge-m3:latest have NO row; Open WebUI lists the first only."""
    rows = {rid: _row(rid, THINK if mode == "thinking" else NOTHINK if mode == "no thinking" else EMBED)
            for rid, (_c, mode) in ROLE_TABLE.items()}
    rows["qwen36-27b"] = _row("qwen36-27b", "Qwen 3.6 27B")
    for rid in ("bge-m3", "bge-m3-f16.gguf"):
        rows[rid] = _row(rid, rid, meta={"profile_image_url": "/static/favicon.png", "hidden": True})
    rows["writer"] = _row("writer", "Writer", base_model_id="local-large")
    rows["terse"] = _row("terse", "Terse", base_model_id="local-small")
    rows["server_status"] = _row("server_status", "Server Status")   # a pipe's row
    rows["cloud-large"] = _row("cloud-large", "Cloud Large")
    return FakeOwui(rows, listed={"qwen36-27b:nothink": "qwen36-27b:nothink"})


def _shown(owui):
    """What the picker lists: Open WebUI's model list minus `meta.hidden` (its browser filter)."""
    _s, text = owui("GET", "http://owui:8080/api/models", {"Authorization": f"Bearer {ADMIN_KEY}"}, None, 5)
    return {m["id"] for m in json.loads(text)["data"] if not ((m.get("info") or {}).get("meta") or {}).get("hidden")}


def _swap_labels(small_file="Qwen3.8-27B Q4_K_M"):
    """The five roles with local-small / local-small:nothink on `small_file` (a model swap)."""
    out = []
    for item in _labels():
        if item.role.startswith("local-small"):
            item = item._replace(label=f"{small_file} (no thinking)")
        out.append(item)
    return out


def test_the_picker_rule_is_one_row_per_label_in_role_order():
    hidden = ml.picker_hidden(_labels(), SERVED)
    assert list(hidden) == SERVED, "every role, then every other served id, each once"
    assert [rid for rid, h in hidden.items() if not h] == ["local-large", "local-large:nothink"]
    # the ORDER decides, not the list's order: local-small first in the list is still hidden
    rev = list(reversed(_labels()))
    assert [r for r, h in ml.picker_hidden(rev).items() if not h] == ["local-large:nothink", "local-large"]
    # a chat role the order does not name ranks after the named ones
    extra = _labels() + [ml.RoleLabel("local-medium", "x", "thinking", THINK, "", "", "t"),
                         ml.RoleLabel("local-tiny", "y", "thinking", "Tiny Q4_0 (thinking)", "", "", "t")]
    got = ml.picker_hidden(extra)
    assert got["local-medium"] is True and got["local-tiny"] is False and got["local-large"] is False


def test_the_swap_case_a_small_model_on_its_own_file_is_shown_and_hidden_again_on_the_way_back():
    """The anchor's swap case: local-small on a DIFFERENT GGUF gets its own label and becomes
    visible (local-small:nothink, same file and mode, stays hidden); switching back hides it."""
    swapped = ml.picker_hidden(_swap_labels("Other-8B Q8_0"), SERVED)
    assert [rid for rid, h in swapped.items() if not h] == ["local-large", "local-large:nothink", "local-small"]
    owui = _live_like()
    ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    assert "local-small" not in _shown(owui)
    changes = ml.sync_owui(_swap_labels("Other-8B Q8_0"), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    assert ("local-small", "renamed") in [(c.role, c.action) for c in changes]
    assert owui.rows["local-small"]["name"] == "Other-8B Q8_0 (no thinking)"
    assert owui.rows["local-small"]["meta"]["hidden"] is False
    assert _shown(owui) >= {"local-large", "local-large:nothink", "local-small"}
    assert "local-small:nothink" not in _shown(owui)
    ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    assert owui.rows["local-small"]["meta"]["hidden"] is True and "local-small" not in _shown(owui)


def test_the_sync_leaves_one_gateway_row_per_label_touches_nothing_else_and_is_idempotent():
    owui = _live_like()
    unmanaged = ("writer", "terse", "server_status", "cloud-large")
    before = copy.deepcopy(owui.rows)
    grants_before = copy.deepcopy(owui.grants)
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    got = {c.role: c.action for c in changes}
    assert got == {"local-large": "unchanged", "local-large:nothink": "unchanged", "local-small": "hidden",
                   "local-small:nothink": "hidden", "local-embed": "hidden", "qwen36-27b": "hidden",
                   "qwen36-27b:nothink": "created", "bge-m3": "unchanged", "bge-m3-f16.gguf": "unchanged",
                   "qllama/bge-m3:latest": "absent"}
    # the picker: exactly one gateway row per label, plus the rows the gateway does not serve
    assert _shown(owui) == {"local-large", "local-large:nothink", *unmanaged}
    assert owui.rows["local-large"]["name"] == THINK and owui.rows["local-large:nothink"]["name"] == NOTHINK
    # rows the local gateway does not serve are never read, let alone written
    assert {rid: owui.rows[rid] for rid in unmanaged} == {rid: before[rid] for rid in unmanaged}
    read = {urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["id"][0]
            for m, path in owui.calls if path.startswith("/api/v1/models/model?")}
    assert read == set(SERVED)
    # a hidden row keeps everything else it had: name, meta keys, params, grants, active flag
    for rid in ("local-small", "qwen36-27b"):
        row = owui.rows[rid]
        assert row["meta"] == {**before[rid]["meta"], "hidden": True}
        assert {k: v for k, v in row.items() if k not in ("meta", "updated_at")} == \
               {k: v for k, v in before[rid].items() if k not in ("meta", "updated_at")}
        assert owui.grants[rid] == grants_before[rid]
    # the only row created is the served id Open WebUI lists with no row; it is created hidden
    assert owui.rows["qwen36-27b:nothink"]["meta"]["hidden"] is True
    assert "qllama/bge-m3:latest" not in owui.rows
    assert [p for p, _ in owui.writes].count("/api/v1/models/create") == 1
    # a second run changes nothing and writes nothing
    writes = len(owui.writes)
    again = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    assert {c.action for c in again} <= {"unchanged", "absent"} and len(owui.writes) == writes


def test_an_admin_un_hiding_a_served_duplicate_is_reverted_and_a_hidden_visible_one_is_shown():
    owui = _live_like()
    ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    owui.rows["qwen36-27b"]["meta"]["hidden"] = False       # an admin un-hides it in the UI
    owui.rows["local-large"]["meta"]["hidden"] = True       # and hides the one the rule shows
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    assert [(c.role, c.action) for c in changes if c.action in ml.WRITTEN] == [
        ("local-large", "shown"), ("qwen36-27b", "hidden")]
    assert owui.rows["qwen36-27b"]["meta"]["hidden"] is True
    assert owui.rows["local-large"]["meta"]["hidden"] is False
    shown = next(c for c in changes if c.role == "local-large")
    assert ml.describe(shown) == f"local-large: now shown in the picker ({THINK!r})"


def test_a_dry_run_prints_the_visibility_plan_and_writes_nothing():
    owui = _live_like()
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, dry_run=True, served=SERVED)
    assert owui.writes == []
    lines = [ml.describe(c) for c in changes]
    assert f"local-large: unchanged ({THINK!r}, shown in the picker)" in lines
    assert f"local-small: would be hidden from the picker ({NOTHINK!r})" in lines
    assert "qwen36-27b: would be hidden from the picker ('Qwen 3.6 27B')" in lines
    assert "qwen36-27b:nothink: would-create as 'qwen36-27b:nothink', hidden from the picker" in lines
    assert ("qllama/bge-m3:latest: no row, and Open WebUI does not list it - nothing to hide, no row created"
            in lines)


def test_a_rename_that_also_changes_visibility_says_both():
    owui = FakeOwui({"local-small": _row("local-small", "Qwen 3.6 27B")})
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui)
    small = next(c for c in changes if c.role == "local-small")
    assert small.action == "renamed"
    assert ml.describe(small) == f"local-small: renamed 'Qwen 3.6 27B' -> {NOTHINK!r}, now hidden from the picker"
    assert owui.rows["local-small"]["meta"]["hidden"] is True
    assert owui.rows["local-small"]["meta"]["capabilities"] == {"vision": True}


@pytest.mark.parametrize("bad", ["preset on a served old id", "preset on a role", "unreadable row",
                                 "refused key on the model list", "unreadable model list",
                                 "old id without grants"])
def test_every_refusal_aborts_before_any_visibility_write(bad):
    """Pass 1 plans the visibility writes too: with several rows needing a hide or a create,
    one refusal anywhere - the LAST managed id included - writes nothing at all."""
    owui = _live_like()
    real = owui.__call__
    if bad == "preset on a served old id":
        owui.rows["qllama/bge-m3:latest"] = _row("qllama/bge-m3:latest", "Mine", base_model_id="bge-m3")
    elif bad == "preset on a role":
        owui.rows["local-embed"]["base_model_id"] = "bge-m3"
    before = copy.deepcopy(owui.rows)

    def request(method, url, headers, body, timeout):
        path = urllib.parse.urlparse(url).path
        if bad == "unreadable row" and "bge-m3-f16.gguf" in url:
            return 500, "Internal Server Error"
        if bad == "refused key on the model list" and path == "/api/models":
            return 403, '{"detail":"forbidden"}'
        if bad == "unreadable model list" and path == "/api/models":
            return 200, "<html>not json</html>"
        status, text = real(method, url, headers, body, timeout)
        if bad == "old id without grants" and method == "GET" and "id=qwen36-27b" in url and status == 200:
            data = json.loads(text)
            data.pop("access_grants", None)
            text = json.dumps(data)
        return status, text
    with pytest.raises(ml.OwuiError, match="Nothing was written"):
        ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, request, served=SERVED)
    assert owui.writes == [] and owui.rows == before


def test_a_served_id_with_no_row_that_open_webui_does_not_list_gets_no_row():
    owui = FakeOwui()
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    assert {c.role for c in changes if c.action == "absent"} == set(OLD_NAMES)
    assert set(owui.rows) == set(ROLE_TABLE)
    # the model list is read ONCE, however many served ids have no row
    assert [p for m, p in owui.calls].count("/api/models") == 1
    # and not at all when every managed id has a row
    owui.calls.clear()
    ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui)
    assert "/api/models" not in [p for m, p in owui.calls]


def test_the_served_ids_are_every_name_the_rendered_local_yaml_registers(world):
    assert ml.served_ids(make_render(world)) == ["qwen36-27b", "qwen36-27b:nothink", "bge-m3", "bge-m3-f16.gguf",
                                                 "qllama/bge-m3:latest", *ROLE_TABLE]
    with pytest.raises(ml.LabelError, match="llm-gateway"):
        ml.served_ids(make_render(world, drop=("llm-gateway",)))


def test_the_rollback_deletes_created_rows_and_restores_changed_ones():
    """Tester attempt 1 (T8): `meta.hidden: false` does not undo a CREATED row - deleting it does."""
    owui = _live_like()
    changes = ml.sync_owui(_labels(), "http://owui:8080", ADMIN_KEY, owui, served=SERVED)
    steps = ml.rollback_steps(changes)
    assert steps == [
        "row 'local-small': meta.hidden back to false (POST /api/v1/models/model/update, everything else as read)",
        "row 'local-small:nothink': meta.hidden back to false (POST /api/v1/models/model/update, "
        "everything else as read)",
        "row 'local-embed': meta.hidden back to false (POST /api/v1/models/model/update, everything else as read)",
        "row 'qwen36-27b': meta.hidden back to false (POST /api/v1/models/model/update, everything else as read)",
        "delete the row 'qwen36-27b:nothink' (this run created it): "
        'POST /api/v1/models/model/delete {"id": "qwen36-27b:nothink"}']
    renamed = ml.Change("local-small", "renamed", "old", "new", True, False)
    assert ml.rollback_steps([renamed]) == [
        "row 'local-small': name back to 'old' and meta.hidden back to false "
        "(POST /api/v1/models/model/update, everything else as read)"]
    assert ml.rollback_steps([c._replace(action="would-hide") for c in changes]) == []
