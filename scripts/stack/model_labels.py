#!/usr/bin/env python3
"""model_labels.py - the display label of every local model ROLE, derived from the model file.

WHY THIS EXISTS (model-roles, operator decisions R1-R5, 2026-09-28)
------------------------------------------------------------------
Services ask the LiteLLM gateway for a ROLE (`local-large`, `local-small`,
`local-embed`, ...), registered in inference/config/litellm/model_list/local.yaml
with `litellm_params.model` = the concrete llama-swap / llama.cpp id. The id was
kept fixed across model swaps on purpose, which made every hand-typed name a lie:
`qwen36-27b` serves Qwen3.8-27B while llama-swap's `name:` said "Qwen 3.6 27B
(ThinkingCap)" and Open WebUI said "Qwen 3.6 27B". So no label is typed by hand
any more. The label comes from the file the inference plane actually loads, as
COMPOSE resolves it - `docker compose -f inference/docker-compose.yml --profile
local config --format json`, a read-only render (nothing is created, started or
pulled; no daemon is needed; ~1 s), so the shell, inference/.env and the compose
defaults are read by compose itself and never re-implemented here:

  roles       llm-gateway's /app/conf.d bind -> local.yaml: every `model_name`
              starting `local-`, with its `litellm_params.model`
  chat model  llama-cpp-upstream's /app/config.yaml bind (the llama-swap config):
              the `--model` of the entry whose id is the concrete id (`:nothink`
              is llama-swap's thinking switch, not an entry), usually
              `${env.LLAMA_SWAP_..._MODEL_PATH}` -> that variable in the service's
              RENDERED environment
  embed model llama-cpp-embed-upstream's rendered `LLAMA_ARG_MODEL` (llm-queue sends
              every `bge*` id to the embed upstream - registry.upstream_for)
  the file    the service's rendered /models bind source, the path followed the way
              the container follows it (relative symlinks inside the store; a
              host-absolute link is refused), and checked to EXIST; the label is
              the name of the file finally reached

and turns the file name into the label:

  /models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf, role local-large
      -> `Qwen3.8-27B Q4_K_M (thinking)`
  the same file, role local-small (concrete id qwen36-27b:nothink)
      -> `Qwen3.8-27B Q4_K_M (no thinking)`
  /models/bge-m3-f16.gguf, role local-embed
      -> `bge-m3 f16 (embeddings)`

THE LABEL FORMAT: the file's stem with its last `-<quant>` or `.<quant>` segment
turned into ` <quant>` (the quant written exactly as the file writes it; a quant is
Q<n>_*/IQ<n>_*/TQ<n>_*, F16/F32/FP16/FP32, BF16 or MXFP*, in any case - anything else leaves the
stem unsplit), then the mode in
parentheses: `thinking`, `no thinking` or `embeddings`. A multi-part GGUF's
`-00001-of-00003` shard suffix is dropped first. Nothing in the label is typed.

IT FAILS LOUDLY, never with a stale label: compose unavailable or its render
failing, a role whose concrete id no upstream serves, a variable with no value, a
path outside the `/models` bind or with a backslash, a symlink the container could
not follow, or a file that does not exist is a LabelError naming what could not be
resolved, and no label at all is produced - so the Open WebUI sync below
never writes anything from a half-resolved set.

THE OPEN WEBUI SYNC (`sync_owui`) sets the Open WebUI `model` row NAME for each
role id through Open WebUI's own admin API (`/api/v1/models/...`) - the path its
admin UI uses to rename a base model - with an admin API key. It reads and
validates EVERY role row before it writes any (a refusal - a PRESET on a role id,
a row without its access grants, an unreadable row, a refused key - writes
nothing), then writes only the rows whose name differs; it never touches a row
that is not a role id, and sends a renamed row's meta, params, access grants and
active flag back exactly as it read them. A PRESET (a `base_model_id` is set) on a
role id is refused rather than someone's workspace model rewritten. `scripts/stack/stack.py labels` drives
it; `stack.py up` / `recover` run it when inference (with `local`) and the
frontend are both enabled.

Standard library only: stack.py and its CI job install nothing but pytest.

CLI (prints the labels, writes nothing):
  python scripts/stack/model_labels.py
  python scripts/stack/model_labels.py --env-file inference/.env.example --skip-file-check
  python scripts/stack/model_labels.py --render saved-render.json
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

ROLE_PREFIX = "local-"
MODEL_LIST_REL = Path("inference/config/litellm/model_list/local.yaml")
LLAMA_SWAP_REL = Path("inference/config/llama-swap.config.yaml")
# The two llama.cpp services (inference/compose/upstreams.yml), read in compose's render.
# This module makes no inference request at all.
CHAT_SERVICE = "llama-cpp-upstream"
EMBED_SERVICE = "llama-cpp-embed-upstream"
EMBED_VAR = "LLAMA_ARG_MODEL"
MODELS_MOUNT = "/models"

MODES = ("thinking", "no thinking", "embeddings")
OWUI_KEY_VAR = "OWUI_ADMIN_API_KEY"
OWUI_URL_VAR = "OWUI_BASE_URL"
OWUI_DEFAULT_URL = "http://127.0.0.1:3000"


class LabelError(Exception):
    """A label could not be derived. The message names what could not be resolved."""


class Role(NamedTuple):
    name: str        # the LiteLLM model_name, e.g. local-small
    concrete: str    # the id LiteLLM forwards, provider prefix removed, e.g. qwen36-27b:nothink
    provider: str    # the litellm provider prefix, e.g. openai / hosted_vllm


class RoleLabel(NamedTuple):
    role: str
    concrete: str
    mode: str
    label: str
    container_path: str   # the path the upstream loads, e.g. /models/unsloth/.../x.gguf
    host_path: str        # where that is on this machine ('' when not checked)
    source: str           # where the path came from, in words


# --------------------------------------------------------------------------
# minimal YAML readers. The three files are hand-written, block-style YAML; these
# read exactly the shapes they use and say so when a shape is not recognised.
# inference/llm-queue/tests/test_model_roles.py compares parse_model_list with
# PyYAML on the real file, so the reader cannot silently disagree with LiteLLM.
# --------------------------------------------------------------------------


def _strip_comment(line: str) -> str:
    """Drop a trailing ` # comment` that is not inside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_model_list(text: str) -> list[tuple[str, str]]:
    """(model_name, litellm_params.model) for every entry of a LiteLLM model_list fragment."""
    entries: list[list[str]] = []
    in_list = False
    list_indent = None
    in_params = False
    params_indent = 0
    child_indent = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        ind = _indent(line)
        body = line.strip()
        if ind == 0:
            in_list = body == "model_list:"
            list_indent = None
            in_params = False
            continue
        if not in_list:
            continue
        if body.startswith("- "):
            if list_indent is None:
                list_indent = ind
            if ind == list_indent:
                entries.append(["", ""])
                in_params = False
                body = body[2:].strip()
                ind += 2
        if not entries:
            continue
        key, sep, value = body.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key == "model_name" and not in_params:
            entries[-1][0] = _scalar(value)
        elif key == "litellm_params":
            in_params = True
            params_indent = ind
            child_indent = None
        elif in_params and ind <= params_indent:
            in_params = False
        if in_params and key != "litellm_params" and child_indent is None:
            child_indent = ind   # the indent of litellm_params' direct children
        if in_params and key == "model" and ind == child_indent and not entries[-1][1]:
            # a DIRECT child of litellm_params only - a `model:` nested deeper
            # (inside extra_body, say) is not the model LiteLLM forwards
            entries[-1][1] = _scalar(value)
    return [(name, model) for name, model in entries if name]


def parse_llama_swap_models(text: str) -> dict[str, str]:
    """{llama-swap model id: the raw `--model` argument of its cmd} for every entry."""
    models: dict[str, str] = {}
    in_models = False
    current = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        ind = _indent(raw)
        body = raw.strip()
        if ind == 0:
            in_models = body.rstrip() == "models:"
            current = None
            continue
        if not in_models:
            continue
        if ind == 2 and body.endswith(":") and not body.startswith("-"):
            current = _scalar(body[:-1])
            continue
        if current is None:
            continue
        match = re.search(r"(?:^|\s)--model\s+(\S+)", body)
        if match and current not in models:
            models[current] = match.group(1)
    return models


# --------------------------------------------------------------------------
# the derivation - from COMPOSE'S OWN RENDER of the inference plane
# --------------------------------------------------------------------------
#
# Attempts 1-3 re-implemented compose's `.env` reading and interpolation, and a
# tester found shape after shape where the two silently disagreed (`export<TAB>KEY`,
# UTF-16, a BOM on line 2, form feeds, U+2028 ...) - each a WRONG label, the compose
# default's, with the file check passing. So nothing here reads `.env` any more.
# `docker compose -f inference/docker-compose.yml --profile local config --format
# json` is asked instead: it resolves every variable exactly as `up` will (the same
# binary, the same shell, the same inference/.env), creates, starts and pulls
# nothing, needs no daemon (it renders with DOCKER_HOST pointed at a dead endpoint)
# and takes about a second. If it is unavailable or the render fails, there is no
# label - never a fallback.

COMPOSE_REL = Path("inference/docker-compose.yml")
GATEWAY_SERVICE = "llm-gateway"
FRAGMENT_TARGET = "/app/conf.d"          # llm-gateway's bind of the model_list fragments
LOCAL_FRAGMENT = "local.yaml"
SWAP_CONFIG_TARGET = "/app/config.yaml"  # llama-cpp-upstream's bind of the llama-swap config


def render_command(root: Path, env_file: Path | None = None, profiles=("local",)) -> list[str]:
    """The read-only render this module trusts, as `stack.py` would run it from the repo root."""
    cmd = ["docker", "compose", "-f", str(Path(root) / COMPOSE_REL)]
    if env_file is not None:
        cmd += ["--env-file", str(env_file)]
    for profile in profiles:
        cmd += ["--profile", profile]
    return cmd + ["config", "--format", "json"]


def render_inference(root: Path, env_file: Path | None = None, run=None) -> dict:
    """`docker compose config --format json` of the inference plane, or a LabelError. Never a guess.

    `run(cmd, cwd) -> (returncode, stdout, stderr)`; the default runs the real docker CLI.
    """
    cmd = render_command(root, env_file)
    if run is None:
        def run(cmd, cwd):
            import subprocess
            try:
                proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                return 127, "", f"{type(exc).__name__}: {exc}"
            return proc.returncode, proc.stdout, proc.stderr
    code, stdout, stderr = run(cmd, root)
    if code != 0:
        why = " | ".join((stderr or stdout or "no output").strip().splitlines()[:3])
        raise LabelError(f"`docker compose ... config` of {COMPOSE_REL.as_posix()} failed (exit {code}): {why} - "
                         f"a label is derived only from compose's own render, never guessed")
    return parse_render(stdout, "`docker compose config`")


def parse_render(text: str, what: str) -> dict:
    try:
        data = json.loads(text or "")
    except ValueError as exc:
        raise LabelError(f"{what} is not JSON ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise LabelError(f"{what} has no services")
    return data


def _service(render: dict, name: str) -> dict:
    spec = render["services"].get(name)
    if not isinstance(spec, dict):
        raise LabelError(f"the render has no {name} service (is the `local` profile in the render?)")
    return spec


def _bind_source(spec: dict, service: str, target: str) -> Path:
    for mount in spec.get("volumes") or []:
        if isinstance(mount, dict) and mount.get("target") == target and mount.get("type") == "bind":
            if mount.get("source"):
                return Path(mount["source"])
    raise LabelError(f"{service} has no bind mount at {target} in the render")


def _environment(spec: dict, service: str, var: str) -> str:
    env = spec.get("environment") or {}
    if isinstance(env, list):   # compose renders a map; accept the list form too
        env = dict(item.split("=", 1) if "=" in item else (item, None) for item in env)
    value = env.get(var)
    if not value:
        raise LabelError(f"{var} is not set in {service}'s rendered environment")
    return value


def roles_from(text: str, where: str) -> list[Role]:
    found = []
    for name, model in parse_model_list(text):
        if not name.startswith(ROLE_PREFIX):
            continue
        if not model:
            raise LabelError(f"role {name} in {where} has no litellm_params.model")
        provider, sep, concrete = model.partition("/")
        if not sep:
            provider, concrete = "", model
        found.append(Role(name, concrete, provider))
    if not found:
        raise LabelError(f"{where} registers no `{ROLE_PREFIX}*` role")
    return found


def roles(root: Path) -> list[Role]:
    """The roles in the repo's local.yaml (tests and the probe; the sync reads the render's bind)."""
    path = Path(root) / MODEL_LIST_REL
    if not path.is_file():
        raise LabelError(f"{MODEL_LIST_REL.as_posix()} does not exist under {root}")
    return roles_from(path.read_text(encoding="utf-8"), MODEL_LIST_REL.as_posix())


def mode_of(role: Role, served_by_embed: bool) -> str:
    if served_by_embed:
        return "embeddings"
    return "no thinking" if role.concrete.endswith(":nothink") else "thinking"


_SHARD = re.compile(r"-\d{5}-of-\d{5}$")
# case-insensitive: Q4_K_M / q4_k_m, Q8_0, IQ3_XXS, TQ1_0, F16 / fp16 / FP32, BF16, MXFP4.
# A Q-quant always has `_` after its digits, so `Model-2024-Q1` keeps `Q1` in the name.
_QUANT = re.compile(r"^(?:I?Q\d+_\w+|TQ\d+_\w+|FP?(?:16|32)|BF16|MXFP\d\w*)$", re.IGNORECASE)


def label_for(filename: str, mode: str) -> str:
    """`Qwen3.8-27B-Q4_K_M.gguf`, `thinking` -> `Qwen3.8-27B Q4_K_M (thinking)`."""
    if mode not in MODES:
        raise LabelError(f"unknown mode {mode!r}")
    name = filename.rsplit("/", 1)[-1]
    if not name.lower().endswith(".gguf"):
        raise LabelError(f"{filename!r} is not a .gguf file")
    stem = _SHARD.sub("", name[:-len(".gguf")])
    # the quant is the segment after the LAST `-` or `.` (Qwen3.8-27B-Q4_K_M, model.Q4_K_M)
    cut = max(stem.rfind("-"), stem.rfind("."))
    head, tail = (stem[:cut], stem[cut + 1:]) if cut > 0 else ("", stem)
    model = f"{head} {tail}" if head and _QUANT.match(tail) else stem
    if not model:
        raise LabelError(f"{filename!r} has no model name")
    return f"{model} ({mode})"


def _served_by_embed(concrete: str, swap_models: dict[str, str]) -> bool | None:
    base = concrete.split(":", 1)[0]
    if base in swap_models:
        return False
    # llm-queue's rule (inference/llm-queue/src/llm_queue/registry.py upstream_for):
    # every bge* id goes to the embed upstream.
    if base.startswith("bge"):
        return True
    return None


def _under_models(container_path: str, service: str) -> str:
    """The path normalised (`/models/../x` is NOT under /models); a LabelError if it leaves the bind.

    A BACKSLASH is refused outright: in the Linux container it is part of a file name, but on a
    Windows host `Path` treats it as a separator, so `/models/..<backslash>x` would be checked (and
    labelled) as a file outside the store that llama-swap can never load."""
    if "\\" in container_path:
        raise LabelError(f"{container_path} contains a backslash - a container path uses `/` only")
    norm = posixpath.normpath(container_path) if container_path.startswith("/") else container_path
    if not norm.startswith(MODELS_MOUNT + "/"):
        raise LabelError(f"{container_path} is not under {service}'s {MODELS_MOUNT} bind")
    return norm


def _absolute_link_target(target: str) -> bool:
    return (target.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", target))
            or target.startswith("\\\\?\\"))


def resolve_in_store(store: Path, container_path: str, role: str) -> Path:
    """The file the CONTAINER opens for `container_path`, followed link by link on the host.

    The store is what the container sees at /models. A symlink is followed only as the
    container would follow it: a RELATIVE target is resolved against the link's directory and
    must stay inside the store; a host-ABSOLUTE target is refused - inside the container it
    names a path that does not exist. The label is then taken from the file finally reached,
    so a link named `Claims-70B-Q2_K.gguf` pointing at `Inside-7B-Q8_0.gguf` labels Inside-7B.
    """
    todo = [p for p in container_path[len(MODELS_MOUNT) + 1:].split("/") if p]
    done: list[str] = []
    hops = 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not done:
                raise LabelError(f"role {role}: {container_path} leads outside the {MODELS_MOUNT} store")
            done.pop()
            continue
        here = store.joinpath(*done, part)
        if here.is_symlink():
            hops += 1
            if hops > 40:
                raise LabelError(f"role {role}: {container_path} has a symlink loop")
            target = os.readlink(here)
            if _absolute_link_target(target):
                raise LabelError(f"role {role}: {store.joinpath(*done, part)} is a symlink to the host-absolute "
                                 f"path {target} - the container cannot open it")
            todo = [p for p in re.split(r"[\\/]", target) if p] + todo
            continue
        done.append(part)
    final = store.joinpath(*done)
    if not final.is_file():
        raise LabelError(f"role {role}: {container_path} is not a file on this machine - looked for {final}")
    return final


def derive_labels(render: dict, check_files: bool = True) -> list[RoleLabel]:
    """Every role's label from compose's render. LabelError, naming the cause, rather than a partial set."""
    gateway = _service(render, GATEWAY_SERVICE)
    fragments = _bind_source(gateway, GATEWAY_SERVICE, FRAGMENT_TARGET)
    local = fragments / LOCAL_FRAGMENT
    if not local.is_file():
        raise LabelError(f"{local} (llm-gateway's {FRAGMENT_TARGET}/{LOCAL_FRAGMENT}) does not exist")
    role_list = roles_from(local.read_text(encoding="utf-8"), str(local))
    chat = _service(render, CHAT_SERVICE)
    swap_path = _bind_source(chat, CHAT_SERVICE, SWAP_CONFIG_TARGET)
    if not swap_path.is_file():
        raise LabelError(f"{swap_path} ({CHAT_SERVICE}'s {SWAP_CONFIG_TARGET}) does not exist")
    swap_models = parse_llama_swap_models(swap_path.read_text(encoding="utf-8"))
    out = []
    for role in role_list:
        embed = _served_by_embed(role.concrete, swap_models)
        if embed is None:
            raise LabelError(f"role {role.name} forwards {role.concrete!r}, which no upstream serves: it is "
                             f"not a model id in the llama-swap config and not a bge* embedding id")
        if embed:
            service = EMBED_SERVICE
            spec = _service(render, service)
            container = _environment(spec, service, EMBED_VAR)
            source = f"{EMBED_VAR} as compose renders {service}"
        else:
            service, spec = CHAT_SERVICE, chat
            raw = swap_models[role.concrete.split(":", 1)[0]]
            match = re.fullmatch(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}", raw)
            if match:
                container = _environment(spec, service, match.group(1))
                source = f"{match.group(1)} as compose renders {service}"
            else:
                container, source = raw, "a literal path in the llama-swap config"
        container = _under_models(container, service)
        host, name = "", container
        if check_files:
            final = resolve_in_store(_bind_source(spec, service, MODELS_MOUNT), container, role.name)
            host, name = str(final), final.name
        mode = mode_of(role, embed)
        out.append(RoleLabel(role.name, role.concrete, mode, label_for(name, mode), container, host, source))
    return out


# --------------------------------------------------------------------------
# the Open WebUI sync
# --------------------------------------------------------------------------


class OwuiError(Exception):
    """The sync could not complete. A refusal found while READING writes nothing; a write that
    Open WebUI rejects leaves the rows written before it, and the message names them."""


class Change(NamedTuple):
    role: str
    action: str   # created | renamed | unchanged | would-create | would-rename
    old: str
    new: str


Request = Callable[..., tuple]   # (method, url, headers, body_bytes|None, timeout) -> (status, text)


def urllib_request(method: str, url: str, headers: dict, body: bytes | None, timeout: int) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return exc.code, text
    except Exception as exc:  # noqa: BLE001 - refused, timeout, DNS: no answer
        return 0, f"{type(exc).__name__}: {exc}"


def wait_ready(base_url: str, request: Request = urllib_request, timeout_s: int = 180,
               sleep: Callable[[float], None] = time.sleep) -> bool:
    """Poll Open WebUI's /health until it answers 200 or the budget runs out."""
    deadline = time.monotonic() + timeout_s
    while True:
        status, _ = request("GET", base_url.rstrip("/") + "/health", {}, None, 10)
        if status == 200:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(3)


def sync_owui(labels: list[RoleLabel], base_url: str, api_key: str, request: Request = urllib_request,
              dry_run: bool = False) -> list[Change]:
    """Set each role's Open WebUI model row NAME to its label. Idempotent; writes only a differing row.

    TWO PASSES. Pass 1 READS every role's row and validates all of them - a refused key,
    an unreadable row, a PRESET on a role id, a row returned without its access grants -
    and raises before ANY write if one fails, so a refusal never leaves a partial rename.
    Pass 2 writes, in role order, only the rows pass 1 planned to create or rename, and
    re-reads each row just before its write, refusing if it changed since pass 1. A write
    that fails in pass 2 (Open WebUI refusing it, or a row changed meanwhile) is the one
    case that can leave earlier rows written; the error names them, and each of those rows
    carries its correct new label.
    """
    if not api_key:
        raise OwuiError(f"{OWUI_KEY_VAR} is empty")
    base = base_url.rstrip("/")
    head = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "Accept": "application/json"}

    def call(method, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, text = request(method, base + path, head, body, 30)
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
        return status, data, text

    # ---- pass 1: read and validate every role row; nothing is written ----
    plan: list[tuple[RoleLabel, str, dict | None]] = []   # (label, create|rename|unchanged, row)
    for item in labels:
        status, row, text = call("GET", "/api/v1/models/model?id=" + urllib.parse.quote(item.role, safe=""))
        if status in (401, 403):
            raise OwuiError(f"Open WebUI refused the key ({status}) reading {item.role}: {OWUI_KEY_VAR} must be "
                            f"an ADMIN user's API key, with API keys enabled (Admin Settings > General). "
                            f"Nothing was written.")
        if status == 404:
            plan.append((item, "create", None))
            continue
        if status != 200 or not isinstance(row, dict):
            raise OwuiError(f"reading the {item.role} row failed: HTTP {status} {text[:200]!r}. "
                            f"Nothing was written.")
        if row.get("base_model_id"):
            raise OwuiError(f"the Open WebUI row {item.role!r} is a PRESET on {row.get('base_model_id')!r}, "
                            f"not a base-model row; it was left alone. Nothing was written.")
        if (row.get("name") or "") == item.label:
            plan.append((item, "unchanged", row))
            continue
        # Everything but the name is sent back as it was read - the access grants
        # too. Open WebUI 0.11.0's update REPLACES a row's grants with the list it
        # is sent, and an update WITHOUT the field fails (HTTP 500: its router
        # re-validates `access_grants=None` against `list`; measured against a
        # disposable 0.11.0 by this item's drill). A row whose grants were not
        # returned is refused rather than rewritten with none.
        if not isinstance(row.get("access_grants"), list):
            raise OwuiError(f"the {item.role} row came back without its access grants, so renaming it would "
                            f"replace them; it was left alone. Nothing was written.")
        plan.append((item, "rename", row))

    # ---- pass 2: write what pass 1 planned ----
    changes: list[Change] = []
    done = lambda: ", ".join(f"{c.role} {c.action}" for c in changes  # noqa: E731
                             if c.action in ("created", "renamed")) or "nothing"
    for item, action, row in plan:
        old = (row or {}).get("name") or ""
        if action == "unchanged":
            changes.append(Change(item.role, "unchanged", old, item.label))
            continue
        if dry_run:
            changes.append(Change(item.role, "would-" + action, old, item.label))
            continue
        # RE-READ just before the write and refuse if the row moved since pass 1 (someone
        # made it a preset, renamed it, changed its grants, meta, params or active flag, created
        # or deleted it): a write built from the pass-1 snapshot would overwrite that change.
        # What is NOT closed: a change landing between this GET and the POST (one round trip;
        # Open WebUI has no conditional update) is overwritten.
        status, now, text = call("GET", "/api/v1/models/model?id=" + urllib.parse.quote(item.role, safe=""))
        if _fingerprint(status, now) != _fingerprint(200 if row is not None else 404, row):
            raise OwuiError(f"the {item.role} row changed in Open WebUI while this sync ran (HTTP {status}); "
                            f"it was left alone - run `labels` again. Written before this: {done()}")
        if action == "create":
            payload = {"id": item.role, "base_model_id": None, "name": item.label,
                       "meta": {"profile_image_url": "/static/favicon.png"}, "params": {}, "is_active": True}
            status, made, text = call("POST", "/api/v1/models/create", payload)
        else:
            grants = [{"principal_type": g.get("principal_type"), "principal_id": g.get("principal_id"),
                       "permission": g.get("permission")} for g in row["access_grants"] if isinstance(g, dict)]
            payload = {"id": item.role, "base_model_id": None, "name": item.label,
                       "meta": row.get("meta") or {}, "params": row.get("params") or {},
                       "access_grants": grants, "is_active": bool(row.get("is_active", True))}
            status, made, text = call("POST", "/api/v1/models/model/update", payload)
        if status != 200 or not isinstance(made, dict) or made.get("name") != item.label:
            verb = "creating" if action == "create" else "renaming"
            raise OwuiError(f"{verb} the {item.role} row failed: HTTP {status} {text[:200]!r}. "
                            f"Written before this: {done()}")
        changes.append(Change(item.role, "created" if action == "create" else "renamed", old, item.label))
    return changes


def _fingerprint(status: int, row) -> tuple:
    """What must not move between pass 1 and a write: existence, preset-ness, the time of the
    last update, the name, the access grants (a grant change need not touch `updated_at`), and
    everything the write sends back - meta, params, the active flag - because Open WebUI keeps
    `updated_at` in whole SECONDS, so two edits within one second leave it unchanged."""
    if status == 404:
        return ("absent",)
    if status != 200 or not isinstance(row, dict):
        return ("unreadable", status)
    grants = row.get("access_grants")
    grants = sorted((g.get("principal_type"), g.get("principal_id"), g.get("permission"))
                    for g in grants if isinstance(g, dict)) if isinstance(grants, list) else None
    return ("row", row.get("base_model_id"), row.get("updated_at"), row.get("name"), repr(grants),
            json.dumps(row.get("meta"), sort_keys=True), json.dumps(row.get("params"), sort_keys=True),
            row.get("is_active"))


def describe(change: Change) -> str:
    if change.action in ("created", "would-create"):
        return f"{change.role}: {change.action} as {change.new!r}"
    if change.action == "unchanged":
        return f"{change.role}: unchanged ({change.new!r})"
    return f"{change.role}: {change.action} {change.old!r} -> {change.new!r}"


# --------------------------------------------------------------------------
# CLI: print the labels (writes nothing)
# --------------------------------------------------------------------------


def _say(text: str, out) -> None:
    """print, degrading characters the stream cannot encode (a cp1252 pipe) instead of raising."""
    try:
        print(text, file=out)
    except UnicodeEncodeError:
        encoding = getattr(out, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"), file=out)


def main(argv=None, out=None, run=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="model_labels.py",
                                     description="Print each local model role's label, derived from the model file "
                                                 "compose's own render of the inference plane names.")
    parser.add_argument("--root", default=None, help="repo root (default: two directories above this script)")
    parser.add_argument("--env-file", default=None,
                        help="passed to `docker compose --env-file` (default: compose reads inference/.env itself)")
    parser.add_argument("--render", default=None, metavar="JSON",
                        help="a saved `docker compose ... config --format json` of the inference plane, instead "
                             "of rendering (for a disposable environment with no docker CLI)")
    parser.add_argument("--skip-file-check", action="store_true",
                        help="label the configured path without checking the file exists "
                             "(a template such as inference/.env.example on a machine without the models)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    env_file = Path(args.env_file).resolve() if args.env_file else None
    try:
        if args.render:
            render = parse_render(Path(args.render).read_text(encoding="utf-8"), args.render)
        else:
            render = render_inference(root, env_file, run)
        labels = derive_labels(render, check_files=not args.skip_file_check)
    except (LabelError, OSError, UnicodeError) as exc:
        _say(f"labels: FAILED - {exc}. No label was produced.", out)
        return 1
    if args.json:
        _say(json.dumps([item._asdict() for item in labels], indent=2), out)
        return 0
    for item in labels:
        checked = f"; file {item.host_path}" if item.host_path else "; file NOT checked"
        _say(f"{item.role:<22} {item.label:<40} <- {item.container_path} ({item.source}{checked})", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
