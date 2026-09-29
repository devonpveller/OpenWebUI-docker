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
any more. This module reads the SAME files the inference plane runs from:

  roles       inference/config/litellm/model_list/local.yaml
              every `model_name` starting `local-`, with its `litellm_params.model`
  chat model  inference/config/llama-swap.config.yaml
              the `--model` argument of the llama-swap entry whose id is the
              concrete id (the `:nothink` suffix is llama-swap's thinking switch,
              not a separate entry), usually `${env.LLAMA_SWAP_..._MODEL_PATH}`
  embed model inference/compose/upstreams.yml
              the embed upstream's `LLAMA_ARG_MODEL` (llm-queue sends every
              `bge*` id to the embed upstream - registry.upstream_for)
  the value   inference/compose/upstreams.yml's `environment:` line for that
              variable, interpolated the way compose does it: the shell
              environment, then inference/.env, then the `${VAR:-default}`
              written in the compose file
  the file    the `/models` bind of the same service, mapped to the host path,
              and checked to EXIST

and turns the file name into the label:

  /models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf, role local-large
      -> `Qwen3.8-27B Q4_K_M (thinking)`
  the same file, role local-small (concrete id qwen36-27b:nothink)
      -> `Qwen3.8-27B Q4_K_M (no thinking)`
  /models/bge-m3-f16.gguf, role local-embed
      -> `bge-m3 f16 (embeddings)`

THE LABEL FORMAT: the file's stem with its last `-<quant>` or `.<quant>` segment
turned into ` <quant>` (the quant written exactly as the file writes it; a quant is
Q*/IQ*/TQ*, F16/F32/FP16/FP32, BF16 or MXFP*, in any case - anything else leaves the
stem unsplit), then the mode in
parentheses: `thinking`, `no thinking` or `embeddings`. A multi-part GGUF's
`-00001-of-00003` shard suffix is dropped first. Nothing in the label is typed.

IT FAILS LOUDLY, never with a stale label: a role whose concrete id no upstream
serves, a variable with no value and no compose default, a path outside the
`/models` bind, or a file that does not exist is a LabelError naming what could
not be resolved, and no label at all is produced - so the Open WebUI sync below
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
UPSTREAMS_REL = Path("inference/compose/upstreams.yml")
ENV_REL = Path("inference/.env")
# The two llama.cpp services in UPSTREAMS_REL. Read, never called: this module
# makes no inference request at all.
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


class ServiceSpec(NamedTuple):
    environment: dict[str, str]   # NAME -> raw value (before interpolation)
    volumes: list[str]            # raw volume strings


def parse_compose_service(text: str, service: str) -> ServiceSpec:
    """The `environment:` (list form) and `volumes:` of one service in a compose file."""
    in_services = False
    in_service = False
    service_indent = None
    section = None
    section_indent = 0
    env: dict[str, str] = {}
    vols: list[str] = []
    found = False
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        ind = _indent(line)
        body = line.strip()
        if ind == 0:
            in_services = body == "services:"
            in_service = False
            continue
        if not in_services:
            continue
        if service_indent is None and body.endswith(":"):
            service_indent = ind
        if ind == service_indent:
            in_service = body == f"{service}:"
            found = found or in_service
            section = None
            continue
        if not in_service:
            continue
        if not body.startswith("- ") and body.endswith(":") and (section is None or ind <= section_indent):
            section = body[:-1]
            section_indent = ind
            continue
        if section is not None and ind <= section_indent and not body.startswith("- "):
            section = None
        if body.startswith("- ") and section == "environment":
            item = _scalar(body[2:])
            name, sep, value = item.partition("=")
            if sep:
                env[name.strip()] = value
        elif body.startswith("- ") and section == "volumes":
            vols.append(_scalar(body[2:]))
    if not found:
        raise LabelError(f"service {service!r} is not declared in {UPSTREAMS_REL.as_posix()}")
    return ServiceSpec(env, vols)


# --------------------------------------------------------------------------
# compose interpolation
# --------------------------------------------------------------------------

_INTERP = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}]*)|(:?\?)([^}]*))?\}"
                     r"|\$([A-Za-z_][A-Za-z0-9_]*)")


def interpolate(raw: str, env: dict[str, str]) -> str:
    """`${VAR}`, `${VAR:-default}`, `${VAR-default}`, `${VAR:?err}`, `$VAR` and `$$`, as compose reads them."""
    def repl(match: re.Match) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(6)
        value = env.get(name)
        op = match.group(2)
        if op == ":-":
            return value if value else match.group(3)
        if op == "-":
            return value if value is not None else match.group(3)
        guard = match.group(4)
        if guard and (value is None or (guard == ":?" and value == "")):
            raise LabelError(f"{name} is not set ({match.group(5) or 'required'})")
        return value or ""
    return _INTERP.sub(repl, raw)


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; the same reading stack.py's read_env_file does."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


# --------------------------------------------------------------------------
# the derivation
# --------------------------------------------------------------------------


def roles(root: Path) -> list[Role]:
    path = root / MODEL_LIST_REL
    if not path.is_file():
        raise LabelError(f"{MODEL_LIST_REL.as_posix()} does not exist under {root}")
    found = []
    for name, model in parse_model_list(path.read_text(encoding="utf-8")):
        if not name.startswith(ROLE_PREFIX):
            continue
        if not model:
            raise LabelError(f"role {name} in {MODEL_LIST_REL.as_posix()} has no litellm_params.model")
        provider, sep, concrete = model.partition("/")
        if not sep:
            provider, concrete = "", model
        found.append(Role(name, concrete, provider))
    if not found:
        raise LabelError(f"{MODEL_LIST_REL.as_posix()} registers no `{ROLE_PREFIX}*` role")
    return found


def mode_of(role: Role, served_by_embed: bool) -> str:
    if served_by_embed:
        return "embeddings"
    return "no thinking" if role.concrete.endswith(":nothink") else "thinking"


_SHARD = re.compile(r"-\d{5}-of-\d{5}$")
# case-insensitive: Q4_K_M / q4_k_m, IQ3_XXS, TQ1_0, F16 / fp16 / FP32, BF16, MXFP4
_QUANT = re.compile(r"^(?:I?Q\d\w*|TQ\d\w*|FP?(?:16|32)|BF16|MXFP\d\w*)$", re.IGNORECASE)


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


class _Env(dict):
    """The interpolation environment, remembering where each non-empty value came from."""

    def __init__(self):
        super().__init__()
        self.origin: dict[str, str] = {}


def compose_env(root: Path, env_file: Path | None, environ: dict[str, str] | None) -> tuple[_Env, Path]:
    """The interpolation environment compose would use: the shell over inference/.env."""
    path = env_file if env_file is not None else root / ENV_REL
    values = _Env()
    for name, value in read_env_file(path).items():
        values[name] = value
        if value:
            values.origin[name] = "file"
    for name, value in (os.environ if environ is None else environ).items():
        values[name] = value
        if value:
            values.origin[name] = "shell"
        else:
            values.origin.pop(name, None)
    return values, path


def _served_by_embed(concrete: str, swap_models: dict[str, str]) -> bool | None:
    base = concrete.split(":", 1)[0]
    if base in swap_models:
        return False
    # llm-queue's rule (inference/llm-queue/src/llm_queue/registry.py upstream_for):
    # every bge* id goes to the embed upstream.
    if base.startswith("bge"):
        return True
    return None


def _resolve_var(raw: str, env: dict, spec: ServiceSpec, env_path: Path, root: Path, service: str):
    """(container path, where it came from) for a llama-swap `${env.X}` or a literal."""
    match = re.fullmatch(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}", raw)
    if not match:
        return raw, f"a literal path in {LLAMA_SWAP_REL.as_posix()}"
    var = match.group(1)
    return _service_value(var, env, spec, env_path, root, service)


def _service_value(var: str, env: dict, spec: ServiceSpec, env_path: Path, root: Path, service: str):
    if var not in spec.environment:
        raise LabelError(f"{var} is not in {service}'s environment in {UPSTREAMS_REL.as_posix()}")
    raw = spec.environment[var]
    value = interpolate(raw, env)
    if not value:
        raise LabelError(f"{var} resolves to nothing ({UPSTREAMS_REL.as_posix()} writes {raw!r}; "
                         f"set it in {_rel(env_path, root)})")
    inner = re.search(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", raw.replace("$$", ""))
    if not inner:
        return value, f"{var} as written in {UPSTREAMS_REL.as_posix()}"
    name = inner.group(1)
    origin = env.origin.get(name) if isinstance(env, _Env) else None
    if origin == "shell":
        return value, f"{name} from the shell environment"
    if origin == "file":
        return value, f"{name} in {_rel(env_path, root)}"
    return value, f"the compose default in {UPSTREAMS_REL.as_posix()} ({name} not set)"


def _rel(path: Path, root: Path) -> str:
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:
        return str(path)


def _under_models(container_path: str, service: str) -> str:
    """The path normalised (`/models/../x` is NOT under /models); a LabelError if it leaves the bind."""
    norm = posixpath.normpath(container_path) if container_path.startswith("/") else container_path
    if not norm.startswith(MODELS_MOUNT + "/"):
        raise LabelError(f"{container_path} is not under {service}'s {MODELS_MOUNT} bind")
    return norm


def _host_path(container_path: str, spec: ServiceSpec, env: dict, root: Path, service: str) -> Path:
    for vol in spec.volumes:
        text = vol
        for suffix in (":ro", ":rw"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        if not text.endswith(":" + MODELS_MOUNT):
            continue
        src = interpolate(text[: -len(":" + MODELS_MOUNT)], env)
        container_path = _under_models(container_path, service)
        base = Path(src)
        if not base.is_absolute():
            base = (root / UPSTREAMS_REL.parent / base)
        return base.joinpath(*container_path[len(MODELS_MOUNT) + 1:].split("/"))
    raise LabelError(f"{service} has no {MODELS_MOUNT} bind in {UPSTREAMS_REL.as_posix()}")


def derive_labels(root: Path, env_file: Path | None = None, environ: dict[str, str] | None = None,
                  check_files: bool = True) -> list[RoleLabel]:
    """Every role's label. Raises LabelError, naming the cause, rather than return a partial set."""
    root = Path(root)
    env, env_path = compose_env(root, env_file, environ)
    if env_file is not None and not Path(env_file).is_file():
        raise LabelError(f"env file {env_file} does not exist")
    swap_path = root / LLAMA_SWAP_REL
    compose_path = root / UPSTREAMS_REL
    for path, rel in ((swap_path, LLAMA_SWAP_REL), (compose_path, UPSTREAMS_REL)):
        if not path.is_file():
            raise LabelError(f"{rel.as_posix()} does not exist under {root}")
    swap_models = parse_llama_swap_models(swap_path.read_text(encoding="utf-8"))
    compose_text = compose_path.read_text(encoding="utf-8")
    specs: dict[str, ServiceSpec] = {}

    def spec(service: str) -> ServiceSpec:
        if service not in specs:
            specs[service] = parse_compose_service(compose_text, service)
        return specs[service]

    out = []
    for role in roles(root):
        embed = _served_by_embed(role.concrete, swap_models)
        if embed is None:
            raise LabelError(f"role {role.name} forwards {role.concrete!r}, which no upstream serves: it is "
                             f"not a model id in {LLAMA_SWAP_REL.as_posix()} and not a bge* embedding id")
        if embed:
            service = EMBED_SERVICE
            container, source = _service_value(EMBED_VAR, env, spec(service), env_path, root, service)
        else:
            service = CHAT_SERVICE
            raw = swap_models[role.concrete.split(":", 1)[0]]
            container, source = _resolve_var(raw, env, spec(service), env_path, root, service)
        _under_models(container, service)   # checked with or without --skip-file-check
        host = ""
        if check_files:
            path = _host_path(container, spec(service), env, root, service)
            if not path.is_file():
                raise LabelError(f"role {role.name}: {container} (from {source}) is not a file on this "
                                 f"machine - looked for {path}")
            host = str(path)
        mode = mode_of(role, embed)
        out.append(RoleLabel(role.name, role.concrete, mode, label_for(container, mode), container, host, source))
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
    Pass 2 writes, in role order, only the rows pass 1 planned to create or rename. A
    write that fails in pass 2 (Open WebUI refusing it) is the one case that can leave
    earlier rows written; the error names them.
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


def describe(change: Change) -> str:
    if change.action in ("created", "would-create"):
        return f"{change.role}: {change.action} as {change.new!r}"
    if change.action == "unchanged":
        return f"{change.role}: unchanged ({change.new!r})"
    return f"{change.role}: {change.action} {change.old!r} -> {change.new!r}"


# --------------------------------------------------------------------------
# CLI: print the labels (writes nothing)
# --------------------------------------------------------------------------


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="model_labels.py",
                                     description="Print each local model role's label, derived from the model file.")
    parser.add_argument("--root", default=None, help="repo root (default: two directories above this script)")
    parser.add_argument("--env-file", default=None, help=f"the inference env file (default: <root>/{ENV_REL.as_posix()})")
    parser.add_argument("--skip-file-check", action="store_true",
                        help="derive from the configured path without checking the file exists "
                             "(for a template such as inference/.env.example on a machine without the models)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    env_file = Path(args.env_file).resolve() if args.env_file else None
    try:
        labels = derive_labels(root, env_file, check_files=not args.skip_file_check)
    except LabelError as exc:
        print(f"labels: FAILED - {exc}. No label was produced.", file=out)
        return 1
    if args.json:
        print(json.dumps([item._asdict() for item in labels], indent=2), file=out)
        return 0
    for item in labels:
        checked = f"; file {item.host_path}" if item.host_path else "; file NOT checked"
        print(f"{item.role:<22} {item.label:<40} <- {item.container_path} ({item.source}{checked})", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
