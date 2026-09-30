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
  chat model  the llama-swap config llama-cpp-upstream reads (its rendered command's
              `-config` - required, absolute - through that path's bind): the entry
              whose id is the concrete id (`:nothink` is llama-swap's thinking
              switch, not an entry); its cmd is expanded (macros, `${env.*}` from the
              service's RENDERED environment). llama-swap lexes it like a POSIX
              shell and parses the config as YAML; neither is emulated. BY ALLOWLIST:
              every `${env.*}` in the WHOLE config and every literal cmd word must
              match [A-Za-z0-9_./:,+=@-] (no `$`, quote, backslash, whitespace,
              control or non-ASCII character; at most 4096 long; a value may not
              start with `@` or end with `:`), every `${env.*}` sits in a block
              scalar, a macro may use only earlier macros, and the config must be
              in a recognised YAML subset (check_swap_config_subset). ABOVE ALL,
              ONLY THE COMMITTED CONFIG (operator decision, 2026-09-29): the file
              must be byte-for-byte the blob committed at HEAD of the stack root's
              own checkout - unresolved path, no link or nested repo on the way,
              GIT_* scrubbed, `cat-file blob` compared in Python (check_committed) -
              and that committed file is pinned by a test; the allowlists
              and the subset are defence in depth. What is left is read with the
              llama.cpp flag rules below; its last `-m`/`--model` is the path
  embed model llama-cpp-embed-upstream's rendered command `-m`/`--model` if it has one
              (llama.cpp takes the flag over the env), else its rendered
              `LLAMA_ARG_MODEL`; a model from a URL / repo / directory / preset /
              built-in default is refused, in any spelling llama.cpp accepts
              (llm-queue sends every `bge*` id to the embed upstream)
  the file    the service's rendered /models bind source. REFUSE, DON'T EMULATE: a
              path (env, command or link target) with an empty / `.` / `..`
              segment, a segment ending in `.` or space, a `:` or other
              Windows-special character, a device name or an 8.3 form is refused;
              then links are followed one component at a time (every component
              before the last an existing directory, the last a regular file; link
              targets relative and plain; host-absolute links and Windows junctions
              refused); the label is the name of the file finally reached

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
validates EVERY row it manages before it writes any (a refusal - a PRESET on a
managed id, a row needing a write without its access grants, an unreadable row or
model list, a refused key -
writes nothing), then writes only the rows that differ; it never touches a row
the local gateway does not serve, and sends a written row's meta, params, access
grants and active flag back exactly as it read them (only `meta.hidden` changes,
and only when the visibility changes). A PRESET (a `base_model_id` is set) on a
managed id is refused rather than someone's workspace model rewritten.
`scripts/stack/stack.py labels` drives it; `stack.py up` / `recover` run it when
inference (with `local`) and the frontend are both enabled.

THE PICKER (model-roles item mr-picker, 2026-09-30). The sync also OWNS the picker
visibility - Open WebUI's own `meta.hidden`, the flag its admin UI's "hide" sets -
of every row whose id the local gateway serves (every `model_name` in local.yaml):
among the chat roles, grouped by label, the FIRST role in CHAT_ROLE_ORDER of each
label is shown and the rest hidden; the embedding role and every served id that is
not a role (the old concrete names) are hidden. So the picker lists each real model
once per mode, by its derived label, and follows a model swap with no hand edit (a
local-small on its own file gets its own label, so it is shown). An admin un-hiding
one of these rows is reverted on the next run. Measured against a disposable Open
WebUI 0.11.0: `meta.hidden` lives only on a row, and a served id with NO row is
listed (not hidden) to admins - normal users do not see it at all - so such an id
gets a hidden row created for it, but only when Open WebUI actually lists it
(GET /api/models); an id it does not list gets no row. A hidden role still answers
chats and still serves as a preset's base (measured the same way). A created row has no
access grants: with BYPASS_ADMIN_ACCESS_CONTROL off, a second admin loses that id (and
presets on it) once it exists (model-roles findings F4). `rollback_steps` prints how to
undo each write - a CREATED row is deleted, not un-hidden.

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
# The picker shows the FIRST chat role of each label in this order (mr-picker); a chat
# role not named here ranks after these, in local.yaml's order.
CHAT_ROLE_ORDER = ("local-large", "local-large:nothink", "local-small", "local-small:nothink")
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


class SwapEntry(NamedTuple):
    cmd: str            # the entry's `cmd` block, as written
    keys: frozenset     # the entry's keys (cmd, filters, env, ...)


def _block(lines: list[str], i: int, parent: int, style: str) -> tuple[str, int]:
    """A YAML block scalar (`|` keeps newlines, `>`/`>-` folds them) starting after line i-1."""
    body = []
    while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > parent):
        body.append(lines[i].strip())
        i += 1
    while body and not body[-1]:
        body.pop()
    return ("\n" if style.startswith("|") else " ").join(body), i


def parse_llama_swap_config(text: str) -> tuple[dict[str, str], dict[str, SwapEntry]]:
    """(macros, models) of a llama-swap config: each macro's text and each model entry's whole
    `cmd` block. Only the block-style shapes this stack's config uses are read; the caller refuses
    what it does not interpret."""
    lines = text.splitlines()
    macros: dict[str, str] = {}
    models: dict[str, SwapEntry] = {}
    section, current, keys, cmd = None, None, set(), ""
    i = 0

    def close():
        if current is not None:
            models[current] = SwapEntry(cmd, frozenset(keys))

    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        ind, body = _indent(raw), raw.strip()
        if ind == 0:
            close()
            current, keys, cmd = None, set(), ""
            section = body.rstrip(":").strip() if body.endswith(":") else body.split(":", 1)[0]
            i += 1
            continue
        key, _, value = _strip_comment(body).partition(":")
        key, value = _scalar(key), value.strip()
        if section == "macros" and ind == 2:
            if value in ("|", "|-", ">", ">-"):
                macros[key], i = _block(lines, i + 1, ind, value)
                continue
            macros[key] = _scalar(value)
        elif section == "models" and ind == 2 and not value:
            close()
            current, keys, cmd = key, set(), ""
        elif section == "models" and current is not None and ind == 4:
            keys.add(key)
            if key == "cmd":
                if value in ("|", "|-", ">", ">-"):
                    cmd, i = _block(lines, i + 1, ind, value)
                    continue
                cmd = _scalar(value)
        i += 1
    close()
    return macros, models


def parse_llama_swap_models(text: str) -> dict[str, str]:
    """{llama-swap model id: its `cmd` block} - kept for the id set the probe and tests read."""
    return {model: entry.cmd for model, entry in parse_llama_swap_config(text)[1].items()}


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


class _WindowsJob:
    """A Windows job object with KILL_ON_JOB_CLOSE: every process the child starts is in it, so
    terminating (or closing) the job ends the WHOLE tree - including a grandchild whose parent has
    already exited, which `taskkill /T` cannot find (tester attempt 6). Standard library (ctypes)."""

    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateJobObjectW.restype = wintypes.HANDLE
        self.k.OpenProcess.restype = wintypes.HANDLE
        self.job = self.k.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        handle = self.k.OpenProcess(0x0001 | 0x0100, False, pid)   # PROCESS_TERMINATE | SET_QUOTA
        if not handle:
            self.close()
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not self.k.AssignProcessToJobObject(self.job, handle):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except OSError:
            self.close()
            raise
        finally:
            self.k.CloseHandle(handle)

    def kill(self):
        if self.job:
            self.k.TerminateJobObject(self.job, 1)

    def close(self):
        if self.job:
            self.k.CloseHandle(self.job)   # KILL_ON_JOB_CLOSE: anything still in the job dies
            self.job = None


def run_bounded(cmd, cwd, timeout: int, env=None, grace: int = 5) -> tuple[int, bytes, bytes]:
    """Run `cmd` capturing bytes, and on timeout kill its WHOLE process tree, then wait at most
    `grace` seconds more - so the call returns within about `timeout + grace`, whatever the tree does.

    On Windows `docker.exe` starts `docker-compose.exe` as a child that holds the output pipe, so
    killing only `docker.exe` does not return (tester attempt 5). The child is put in a job object
    (KILL_ON_JOB_CLOSE) as soon as it starts; on timeout the job is terminated, which ends every
    process in it, orphans included. If the job cannot be set up, `taskkill /T /F` is the fallback -
    which cannot reach an orphaned grandchild (recorded in the findings). Elsewhere the child gets its
    own session and the whole group is killed. stdin is never inherited (DEVNULL). (124, ...) on timeout."""
    import signal
    import subprocess
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(list(cmd), cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, env=env, **kwargs)
    except OSError as exc:
        return 127, b"", f"{type(exc).__name__}: {exc}".encode()
    job = None
    if os.name == "nt":
        try:
            job = _WindowsJob(proc.pid)
        except OSError:
            job = None
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            _kill_tree(proc, job, signal, subprocess)
            try:
                proc.communicate(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
            return 124, b"", f"timed out after {timeout} s (the process tree was killed)".encode()
    finally:
        # also after a SUCCESSFUL run: anything the child left running dies with it (Windows:
        # closing the job, KILL_ON_JOB_CLOSE; elsewhere: the child's process group)
        if job is not None:
            job.close()
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass


def _kill_tree(proc, job, signal, subprocess) -> None:
    if job is not None:
        job.kill()
    elif os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def render_inference(root: Path, env_file: Path | None = None, run=None) -> dict:
    """`docker compose config --format json` of the inference plane, or a LabelError. Never a guess.

    `run(cmd, cwd) -> (returncode, stdout, stderr)`; the default runs the real docker CLI.
    """
    cmd = render_command(root, env_file)
    if run is None:
        def run(cmd, cwd):
            code, out, err = run_bounded(cmd, cwd, 120)
            try:
                return code, out.decode("utf-8", errors="strict"), err.decode("utf-8", errors="replace")
            except UnicodeDecodeError as exc:
                return 1, "", f"the output is not UTF-8 ({exc})"
    code, stdout, stderr = run(cmd, root)
    if code != 0:
        why = " | ".join((stderr or stdout or "no output").strip().splitlines()[:3])
        raise LabelError(f"`docker compose ... config` of {COMPOSE_REL.as_posix()} failed (exit {code}): {why} - "
                         f"a label is derived only from compose's own render, never guessed")
    return parse_render(stdout, "`docker compose config`")


def parse_render(text: str, what: str) -> dict:
    try:
        data = json.loads(text or "")
    except (ValueError, RecursionError) as exc:
        raise LabelError(f"{what} is not JSON this module can read ({type(exc).__name__}: {str(exc)[:120]})") from None
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise LabelError(f"{what} has no services")
    return data


def _service(render: dict, name: str) -> dict:
    spec = render["services"].get(name)
    if not isinstance(spec, dict):
        raise LabelError(f"the render has no {name} service (is the `local` profile in the render?)")
    return spec


def _bind_source(spec: dict, service: str, target: str) -> Path:
    volumes = spec.get("volumes") or []
    if not isinstance(volumes, list):
        raise LabelError(f"{service}'s rendered volumes are not a list")
    for mount in volumes:
        if isinstance(mount, dict) and mount.get("target") == target and mount.get("type") == "bind":
            source = mount.get("source")
            if not isinstance(source, str) or not source:
                raise LabelError(f"{service}'s bind at {target} has no source path in the render")
            if not (Path(source).is_absolute() or source.startswith("/")):
                raise LabelError(f"{service}'s bind at {target} has the RELATIVE source {source!r}; compose "
                                 f"renders absolute paths - this is not a compose render")
            return Path(source)
    raise LabelError(f"{service} has no bind mount at {target} in the render")


def _env_map(spec: dict, service: str) -> dict:
    env = spec.get("environment") or {}
    if isinstance(env, list):   # compose renders a map; accept the list form too
        env = dict(str(item).split("=", 1) if "=" in str(item) else (str(item), None) for item in env)
    if not isinstance(env, dict):
        raise LabelError(f"{service}'s rendered environment is not a map")
    return env


def _environment(spec: dict, service: str, var: str) -> str:
    value = _env_map(spec, service).get(var)
    if value is not None and not isinstance(value, str):
        raise LabelError(f"{var} in {service}'s rendered environment is not a string")
    if not value:
        raise LabelError(f"{var} is not set in {service}'s rendered environment")
    return value


def _command(spec: dict, service: str) -> list[str]:
    """The rendered command. An `entrypoint` override is refused: what it runs is not interpreted."""
    if spec.get("entrypoint"):
        raise LabelError(f"{service} has an `entrypoint` in the render - what it runs is not interpreted here")
    command = spec.get("command") or []
    if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
        raise LabelError(f"{service}'s rendered command is not a list of strings")
    return command


def _flag_name(arg: str) -> str:
    """A flag as llama.cpp matches it: `_` is `-` in a LONG flag (`--hf_repo` is `--hf-repo`)."""
    name = arg.split("=", 1)[0]
    return name.replace("_", "-") if name.startswith("--") else name


def _flag(command: list[str], names: tuple[str, ...]) -> str | None:
    """The value of the LAST occurrence of any of `names`, written `--x value`, or None. The
    `--x=value` form is REFUSED: llama-server rejects `--model=x` (measured, tester attempt 5), and
    a form this module cannot be sure the server accepts is not labelled."""
    value = None
    for i, arg in enumerate(command):
        name = _flag_name(arg)
        if name not in names:
            continue
        if "=" in arg:
            raise LabelError(f"`{arg}`: a model/config flag in `--flag=value` form is refused - the server may "
                             f"not accept it (llama-server rejects `--model=...`)")
        if i + 1 >= len(command):
            raise LabelError(f"`{arg}` is the last word of the command, with no value")
        value = command[i + 1]
    return value


# llama.cpp's server, as the PINNED image's `llama-server --help` lists it (fixture:
# scripts/stack/fixtures/llama-server-help.txt; test_model_labels checks this list against it):
# a command-line model flag beats LLAMA_ARG_MODEL, and these load a model from somewhere that is
# not a /models file - refused. Every `--*-default` flag downloads a built-in model - refused.
_EMBED_MODEL_FLAGS = ("-m", "--model")
_EMBED_REMOTE_FLAGS = ("-mu", "--model-url", "-dr", "--docker-repo", "-hf", "-hfr", "--hf-repo",
                       "-hff", "--hf-file", "-hfd", "-hfrd", "--hf-repo-draft", "-hfv", "-hfrv",
                       "--hf-repo-v", "-hffv", "--hf-file-v", "--models-dir", "--models-preset")
_EMBED_REMOTE_ENV = ("LLAMA_ARG_MODEL_URL", "LLAMA_ARG_DOCKER_REPO", "LLAMA_ARG_HF_REPO", "LLAMA_ARG_HF_FILE",
                     "LLAMA_ARG_HFD_REPO", "LLAMA_ARG_HF_REPO_V", "LLAMA_ARG_HF_FILE_V",
                     "LLAMA_ARG_MODELS_DIR", "LLAMA_ARG_MODELS_PRESET")


def _check_remote(command: list[str], env: dict, who: str) -> None:
    """Refuse a llama-server told to load a model from anywhere but a /models file (see the list)."""
    remote = [a for a in command if _flag_name(a) in _EMBED_REMOTE_FLAGS
              or (_flag_name(a).startswith("--") and _flag_name(a).endswith("-default"))]
    remote += [k for k in _EMBED_REMOTE_ENV if env.get(k)]
    if remote:
        raise LabelError(f"{who} loads its model from {', '.join(remote)} in the render - not a "
                         f"{MODELS_MOUNT} file this module can label")


def _embed_model(spec: dict) -> tuple[str, str]:
    """(the path llama-cpp-embed-upstream loads, where that came from): its rendered command's
    `-m`/`--model` if present (llama.cpp takes the flag over the env), else LLAMA_ARG_MODEL."""
    command = _command(spec, EMBED_SERVICE)
    _check_remote(command, _env_map(spec, EMBED_SERVICE), EMBED_SERVICE)
    flagged = _flag(command, _EMBED_MODEL_FLAGS)
    if flagged is not None:
        return flagged, f"`--model` in {EMBED_SERVICE}'s rendered command"
    return _environment(spec, EMBED_SERVICE, EMBED_VAR), f"{EMBED_VAR} as compose renders {EMBED_SERVICE}"


_SWAP_BUILTINS = {"PORT", "MODEL_ID"}
_REF = re.compile(r"\$\{([^}]*)\}")
# ALLOWLIST, NOT DENYLIST (tester attempts 1-8). Every value llama-swap substitutes into a command,
# and every literal word of the role's command, must be made ONLY of these printable ASCII
# characters - no `$` (llama-swap re-expands `${...}` inside substituted values, and compose's JSON
# writes a literal `$` as `$$`), no quote, no backslash, no whitespace of any kind, no control
# character, nothing outside ASCII. These allowlists are defence in depth behind the committed-config
# rule (check_committed) and the pinned parse of that config (test_model_labels).
SAFE_WORD = re.compile(r"^[A-Za-z0-9_./:,+=@-]+$")


MAX_WORD = 4096   # a 200 000-character word made llama-swap fail to start (E2BIG, tester attempt 8)


def _safe(text: str) -> bool:
    return isinstance(text, str) and len(text) <= MAX_WORD and bool(SAFE_WORD.match(text))


def swap_env_refs(config_text: str) -> set[str]:
    """Every `${env.VAR}` llama-swap will see in its config: in block scalars (a cmd, a folded
    macro - where `#` is content) and in every other line with its YAML comment removed. A
    full-line comment outside a block is not config and is skipped."""
    lines = config_text.splitlines()
    seen: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        body = _strip_comment(raw)
        seen.append(body)
        value = body.split(":", 1)[1].strip() if ":" in body else ""
        if value in _BLOCK_STYLES:
            block, i = _block(lines, i + 1, _indent(raw), value)
            seen.append(block)
            continue
        i += 1
    return set(re.findall(r"\$\{env\.([^}]*)\}", "\n".join(seen)))


STACK_ROOT = Path(__file__).resolve().parents[2]   # the checkout this module belongs to


def _git_env() -> dict:
    """The environment git runs with: the caller's, minus EVERY `GIT_*` variable (GIT_DIR,
    GIT_WORK_TREE, GIT_INDEX_FILE, GIT_CONFIG_* ... would each point git at another repo, index or
    config - tester attempt 10), then two of OURS: GIT_NO_REPLACE_OBJECTS=1 (`refs/replace/*` would
    let `HEAD:<rel>` name a blob no commit holds - tester attempt 11) and GIT_GRAFT_FILE pointing at
    nothing (grafts rewrite parents, never a commit's tree, so `HEAD:<rel>` does not depend on them;
    neutralised anyway)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = os.devnull
    return env


# every git call of the committed-config check: no replace objects and no fsmonitor program (a
# configured `core.fsmonitor` is a command git would RUN during ls-files - tester attempt 12),
# whatever the repository's config says. GIT_ALTERNATE_OBJECT_DIRECTORIES goes with the GIT_* scrub.
_GIT_PREFIX = ["--no-replace-objects", "-c", "core.useReplaceRefs=false", "-c", "core.fsmonitor=false"]


def _object_id(kind: bytes, body: bytes, oid: str) -> str:
    """The id git gives an object of this kind and body, in the hash the repo uses (the length of
    `oid` says which: 40 hex = SHA-1, 64 = SHA-256)."""
    import hashlib
    algo = hashlib.sha256 if len(oid) == 64 else hashlib.sha1
    return algo(kind + b" " + str(len(body)).encode() + b"\0" + body).hexdigest()


def _run_git(args, cwd):
    import subprocess
    try:
        proc = subprocess.run(["git", *_GIT_PREFIX, "-C", str(cwd), *args], cwd=str(cwd), capture_output=True,
                              env=_git_env(), timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, b"", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def check_committed(path: Path, root: Path, run=None) -> str:
    """ONLY TRUST THE COMMITTED CONFIG (operator decision, 2026-09-29). Returns the config's text,
    or raises a LabelError naming the file. The llama-swap config the render mounts must be, byte for
    byte, the blob committed at HEAD of the STACK ROOT's own checkout:

      - git runs as `git --no-replace-objects -c core.useReplaceRefs=false -C <root>` with every
        `GIT_*` variable removed from its environment (then GIT_NO_REPLACE_OBJECTS=1 and an empty
        GIT_GRAFT_FILE set), and `git rev-parse --show-toplevel` must be the root itself - so
        `HEAD:<path>` is the blob in HEAD's own tree, never a `refs/replace/*` substitute;
      - the path is taken AS THE RENDER NAMES IT, never resolved: it must lie under the root with no
        `..` segment (pathlib has already dropped `.` segments and doubled separators, which name
        the same directory), and neither the file nor any directory between the root and it may be a
        symlink, a junction or another reparse point, or hold a `.git` entry (a nested repo or a
        gitfile - git would use the nearest repo);
      - the file must be tracked, and its bytes must equal `git cat-file blob HEAD:<path>` compared
        here in Python, with CRLF -> LF the only normalisation (a Windows checkout of an LF blob; a
        lone CR is a YAML line break and is NOT normalised away). The text returned - and parsed - is
        decoded from those verified bytes; the file is not read again.
        No clean filter, attribute or index flag takes part: assume-unchanged, skip-worktree, a
        staged edit, a local `filter.*.clean` all leave the bytes different and are refused.

    The committed file is pinned by test_model_labels (its expected parse, filters and
    concurrencyLimit included), so a change to it goes through review; the YAML subset check and the
    allowlists stay as defence in depth. Every git call also passes `-c core.fsmonitor=false` (no
    fsmonitor program runs), and the blob returned is re-hashed here: it must hash to the id `git
    rev-parse HEAD:<path>` names (git does not verify an object on read).

    THREAT MODEL (operator decision, 2026-09-29): this protects against ORDINARY AND ACCIDENTAL
    edits - uncommitted/staged edits, index flags, symlinks, junctions, nested repos, GIT_* overrides
    (GIT_ALTERNATE_OBJECT_DIRECTORIES included), filters, attributes, replace refs, an fsmonitor
    program, a loose object overwritten in place. It does NOT protect against someone who can write
    to .git's internals (a forged tree or commit with a valid id, an alternates store or pack naming
    HEAD's ids, rewritten refs) or to the stack's own code - such a person can edit scripts/stack
    directly. Those are out of scope by decision.

    `run(args, cwd) -> (code, stdout bytes, stderr)` runs git (injectable for tests)."""
    run = run or _run_git
    refuse = (f"the llama-swap config {path} differs from the committed version, or cannot be checked "
              f"against it; labels are only derived from the committed config")
    root = Path(root)
    path = Path(path)
    if not path.is_absolute() or not root.is_absolute():
        raise LabelError(f"{refuse} (not an absolute path under the stack root {root})")
    parts, root_parts = path.parts, root.parts
    if (len(parts) <= len(root_parts)
            or not all(_same_path(Path(a), Path(b)) for a, b in zip(parts[:len(root_parts)], root_parts))):
        raise LabelError(f"{refuse} (it is not under the stack root {root})")
    rel_parts = parts[len(root_parts):]
    if any(part in ("", ".", "..") for part in rel_parts):
        raise LabelError(f"{refuse} (a `..` segment in the path)")
    here = root
    for n, part in enumerate(rel_parts):
        here = here / part
        if here.is_symlink() or _is_reparse_point(here):
            raise LabelError(f"{refuse} ({here} is a symlink, junction or reparse point)")
        if n < len(rel_parts) - 1 and os.path.lexists(here / ".git"):
            raise LabelError(f"{refuse} ({here} holds a .git entry - a nested repository)")
    if not path.is_file():
        raise LabelError(f"{refuse} (not a regular file)")
    code, top, err = run(["rev-parse", "--show-toplevel"], root)
    if code != 0 or not top.strip():
        raise LabelError(f"{refuse} (the stack root {root} is not a git checkout, or git is unavailable: "
                         f"{err.strip()[:120]})")
    top_path = Path(top.decode("utf-8", "replace").strip())
    if not _same_path(top_path.resolve(), root.resolve()):
        raise LabelError(f"{refuse} (git's checkout is {top_path}, not the stack root {root})")
    rel = "/".join(rel_parts)
    code, _, _ = run(["ls-files", "--error-unmatch", "--", rel], root)
    if code != 0:
        raise LabelError(f"{refuse} (it is not tracked by git)")
    code, oid, _ = run(["rev-parse", "--verify", "--end-of-options", f"HEAD:{rel}"], root)
    code2, blob, _ = run(["cat-file", "blob", f"HEAD:{rel}"], root)
    if code != 0 or code2 != 0:
        raise LabelError(f"{refuse} (it is not committed at HEAD)")
    oid = oid.decode("ascii", "replace").strip()
    if _object_id(b"blob", blob, oid) != oid:
        # git does not verify an object on read: a loose object file overwritten in place
        raise LabelError(f"{refuse} (the object git returned for HEAD:{rel} does not hash to its id {oid})")
    data = path.read_bytes()
    if data.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
        raise LabelError(f"{refuse} (the file on disk is not the blob committed at HEAD)")
    return data.decode("utf-8")


def check_swap_env_placement(config_text: str) -> None:
    """Every `${env.*}` must sit inside a BLOCK scalar (a `|`/`>` cmd or macro). In a plain scalar a
    substituted value is re-read as YAML, where a leading `@`, `-` or `&`, or a trailing `:`, changes
    its type or breaks the config (tester attempt 9, y26b)."""
    lines = config_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        body = _strip_comment(raw)
        value = body.split(":", 1)[1].strip() if ":" in body else ""
        if value in _BLOCK_STYLES:
            _, i = _block(lines, i + 1, _indent(raw), value)
            continue
        if "${env." in body:
            raise LabelError(f"the llama-swap config, line {i + 1}: a `${{env.*}}` outside a block scalar - "
                             f"not interpreted here")
        i += 1


def check_swap_env(config_text: str, env: dict, who: str = "the llama-swap config") -> None:
    """Every `${env.VAR}` ANYWHERE in the llama-swap config must be set and match SAFE_WORD:
    llama-swap substitutes them all when it loads the config, and one bad value in ANY entry makes
    it refuse (or re-lex) the whole config (tester attempts 7-8)."""
    for var in sorted(swap_env_refs(config_text)):
        value = env.get(var)
        if value is not None and not isinstance(value, str):
            raise LabelError(f"{who}: {var} in {CHAT_SERVICE}'s rendered environment is not a string")
        if not value:
            raise LabelError(f"{who}: {var} is not set in {CHAT_SERVICE}'s rendered environment "
                             f"(llama-swap substitutes every `${{env.*}}` in the config)")
        if value.startswith("@") or value.endswith(":"):
            raise LabelError(f"{who}: {var}={value!r} starts with `@` or ends with `:` (YAML indicators) - "
                             f"not interpreted here")
        if not _safe(value):
            raise LabelError(f"{who}: {var}={value!r} has a character outside the allowlist "
                             f"[A-Za-z0-9_./:,+=@-] (as compose renders it; a literal `$` renders as `$$`) - "
                             f"not interpreted here")


# --- the recognised YAML subset of the llama-swap config -----------------------------------
_BLOCK_STYLES = ("|", "|-", ">", ">-")
_KEY = re.compile(r'^(?:[A-Za-z0-9_.:-]+|"[A-Za-z0-9_.:${}-]+")$')
_PLAIN_VALUE = re.compile(r"^[A-Za-z0-9_./:,+=@ ${}-]*$")
_TOP_KEYS_SCALAR = {"listen", "healthCheckTimeout", "includeAliasesInList", "globalTTL", "logToStdout",
                    "logLevel", "startPort", "metricsMaxInMemory"}
_ENTRY_KEYS = {"cmd", "filters", "concurrencyLimit"}


def check_swap_config_subset(text: str) -> None:
    """Refuse a llama-swap config that is not in the small YAML subset this module reads: a few
    top-level plain scalars, a `macros:` map and a `models:` map; per entry only `cmd` (a single-line
    plain scalar, or a `|`/`|-`/`>`/`>-` block with no other indicator), `filters` (nested plain
    maps) and `concurrencyLimit`; plain keys (or a double-quoted key without escapes); no tab, no
    anchor/tag/alias (`&`, `!`, `*`), no flow collection, no quotes in values, no backslash, no
    multi-line plain scalar; non-ASCII only in full-line comments. Any line not recognised is a
    LabelError naming it. This reads the recognised shapes; it is not a YAML validator and does not
    promise to catch every construct YAML reads differently - defence in depth behind check_committed
    and the pinned parse."""
    lines = text.splitlines()
    stack: list[tuple[int, set, str]] = []   # (indent of the keys at this level, keys seen, parent path)
    section = None
    entry_depth_path = ""
    last_value_indent = None                 # indent of the last `key: value` line (to spot continuations)
    i = 0

    def refuse(n, why):
        raise LabelError(f"the llama-swap config, line {n}: {why} - not in the YAML subset this module reads")

    while i < len(lines):
        n, raw = i + 1, lines[i]
        if "\t" in raw:
            refuse(n, "a tab")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if any(ord(c) > 126 or ord(c) < 32 for c in raw):
            refuse(n, "a non-ASCII or control character outside a comment")
        ind = _indent(raw)
        if last_value_indent is not None and ind > last_value_indent:
            refuse(n, "a continuation of a multi-line plain scalar")
        last_value_indent = None
        body = _strip_comment(raw).strip()
        if body.startswith(("- ", "-", "? ")) and not body.startswith("--"):
            refuse(n, "a sequence or complex-key entry")
        key, sep, value = _split_key(body)
        if not sep:
            refuse(n, "not a `key: value` or `key:` line")
        if not _KEY.match(key) or key.startswith(("&", "*", "!")):
            refuse(n, f"the key {key!r}")
        while stack and stack[-1][0] > ind:
            stack.pop()
        if stack and stack[-1][0] == ind:
            keys = stack[-1][1]
        else:
            if stack and ind < stack[-1][0]:
                refuse(n, "an indentation that matches no enclosing map")
            keys = set()
            stack.append((ind, keys, key))
        if key in keys:
            refuse(n, f"the duplicate key {key!r}")
        keys.add(key)
        depth = len(stack)
        if depth == 1:
            section = key
            if key in ("macros", "models"):
                if value:
                    refuse(n, f"`{key}:` must be a block map")
            elif key not in _TOP_KEYS_SCALAR or not _plain(value):
                refuse(n, f"the top-level key {key!r} (only macros, models and plain scalars "
                          f"{sorted(_TOP_KEYS_SCALAR)})")
            else:
                last_value_indent = ind
            i += 1
            continue
        if section == "macros" and depth == 2:
            if value in _BLOCK_STYLES:
                i = _subset_block(lines, i + 1, ind, refuse)
                continue
            if not _plain(value):
                refuse(n, f"the macro value {value!r}")
            last_value_indent = ind
            i += 1
            continue
        if section == "models" and depth == 2:
            if value:
                refuse(n, f"the model entry {key!r} must be a block map")
            entry_depth_path = key
            i += 1
            continue
        if section == "models" and depth == 3:
            if key not in _ENTRY_KEYS:
                refuse(n, f"the entry key {key!r} in {entry_depth_path!r} (only cmd, filters, concurrencyLimit)")
            if key == "cmd":
                if value in _BLOCK_STYLES:
                    i = _subset_block(lines, i + 1, ind, refuse)
                    continue
                if not _plain(value):
                    refuse(n, f"the cmd value {value!r}")
            elif key == "concurrencyLimit":
                if not re.fullmatch(r"[0-9]+", value):
                    refuse(n, f"concurrencyLimit {value!r}")
            elif value:
                refuse(n, "`filters:` must be a block map")
            last_value_indent = ind if value else None
            i += 1
            continue
        if section == "models" and depth > 3:
            # filters: nested plain maps with plain scalar leaves
            if value and not _plain(value):
                refuse(n, f"the filters value {value!r}")
            last_value_indent = ind if value else None
            i += 1
            continue
        refuse(n, f"the key {key!r} under {section!r}")


def _split_key(body: str) -> tuple[str, str, str]:
    """(key, ':', value) of `key: value` / `key:` / `"quoted key": value`; ('', '', '') otherwise."""
    if body.startswith('"'):
        end = body.find('"', 1)
        if end < 0 or body[end + 1:end + 2] != ":":
            return "", "", ""
        rest = body[end + 2:]
        if rest and not rest.startswith(" "):
            return "", "", ""
        return body[:end + 1], ":", rest.strip()
    if ": " in body:
        key, _, value = body.partition(": ")
        return key.strip(), ":", value.strip()
    if body.endswith(":") and body.count(":") == 1:
        return body[:-1].strip(), ":", ""
    return "", "", ""


def _plain(value: str) -> bool:
    """A single-line plain scalar this module reads: SAFE characters plus spaces and `${...}`, not
    starting with a YAML indicator, no `: ` or ` #` inside (which would make it something else)."""
    return (bool(value) and bool(_PLAIN_VALUE.match(value)) and value[0] not in "&*!|>{[%@`-?:,"
            and ": " not in value and " #" not in value) or (value.startswith("--") and bool(_PLAIN_VALUE.match(value)))


def _subset_block(lines: list[str], i: int, parent: int, refuse) -> int:
    """The lines of a `|`/`>` block: each deeper than its key, printable ASCII only."""
    while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > parent):
        if "\t" in lines[i] or any(ord(c) > 126 or ord(c) < 32 for c in lines[i]):
            refuse(i + 1, "a tab, control or non-ASCII character inside a block")
        i += 1
    return i


def _expand_macros(macros: dict[str, str], who: str) -> dict[str, str]:
    """Each macro with the macros it references substituted - only macros defined EARLIER may be
    referenced (a later one is not expanded by the pinned llama-swap, tester attempt 7); a
    reference to anything else but `${env.*}` and the built-ins is refused."""
    done: dict[str, str] = {}
    for name, text in macros.items():
        def sub(match, name=name):
            ref = match.group(1)
            if ref in _SWAP_BUILTINS or ref.startswith("env."):
                return match.group(0)
            if ref in done:
                return done[ref]
            if ref in macros:
                raise LabelError(f"{who}: macro {name!r} references {ref!r}, which is defined LATER - "
                                 f"not interpreted here")
            raise LabelError(f"{who}: macro {name!r} references `${{{ref}}}`, which is not a macro")
        done[name] = _REF.sub(sub, text)
    return done


def _swap_model(entry: SwapEntry, macros: dict[str, str], spec: dict, model_id: str,
                config_text: str) -> tuple[str, str]:
    """(the path llama-server loads for this llama-swap entry, where it came from).

    llama-swap substitutes `${macro}` and `${env.VAR}` into the entry's cmd and splits the result
    with a POSIX-shell lexer. REFUSE, DON'T EMULATE, BY ALLOWLIST: the config must be in the
    recognised YAML subset (check_swap_config_subset); every `${env.*}` in the WHOLE config must be
    set and match SAFE_WORD (check_swap_env); every literal word of the expanded cmd must match
    SAFE_WORD once its `${env.*}` and built-in references are set aside; a word starting with `--`
    alone (`--`) or `#` cannot match; macros may reference only earlier macros; an unknown `${...}`
    is refused. The config itself is the committed one (check_committed) whose parse is pinned by a
    test; the allowlists are defence in depth. The words are split on ASCII spaces and newlines and
    the result gets the embed server's
    flag rules (`_`/`-`, `=`-form and remote-model flags refused, the LAST `-m`/`--model` wins,
    none refused)."""
    who = f"llama-swap entry {model_id!r}"
    extra = sorted(entry.keys - _ENTRY_KEYS)
    if extra:
        raise LabelError(f"{who} has {', '.join(extra)} - not interpreted here")
    if not entry.cmd:
        raise LabelError(f"{who} has no cmd - not interpreted here")
    env = _env_map(spec, CHAT_SERVICE)
    check_swap_env(config_text, env)
    expanded_macros = _expand_macros(macros, "the llama-swap config")

    def literal(match):
        name = match.group(1)
        if name in _SWAP_BUILTINS or name.startswith("env."):
            return match.group(0)
        if name in expanded_macros:
            return expanded_macros[name]
        raise LabelError(f"{who}: `${{{name}}}` is not a macro, an env reference or a llama-swap built-in")

    literal_cmd = _REF.sub(literal, entry.cmd)
    if set(literal_cmd) - set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:,+=@-${} \n"):
        bad = sorted(set(literal_cmd) - set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                                            "0123456789_./:,+=@-${} \n"))
        raise LabelError(f"{who}: its cmd has {''.join(bad)!r} - outside the allowlist; not interpreted here")
    for word in literal_cmd.replace("\n", " ").split(" "):
        if not word:
            continue
        shown = re.sub(r"\$\{env\.[A-Za-z_][A-Za-z0-9_]*\}", "v", word)
        shown = re.sub(r"\$\{(?:PORT|MODEL_ID)\}", "v", shown)
        if not _safe(shown) or word == "--":
            raise LabelError(f"{who}: the word {word!r} in its cmd is outside the allowlist [A-Za-z0-9_./:,+=@-] "
                             f"(or is `--`) - not interpreted here")
    cmd = _REF.sub(lambda m: m.group(0) if m.group(1) in _SWAP_BUILTINS else env[m.group(1)[4:]], literal_cmd)
    tokens = [t for t in cmd.replace("\n", " ").split(" ") if t]
    _check_remote(tokens, env, who)
    flagged = _flag(tokens, _EMBED_MODEL_FLAGS)
    if flagged is None:
        raise LabelError(f"{who}'s cmd has no `--model`/`-m` - not interpreted here")
    ref = re.search(r"(?:^|\s)(?:-m|--model)\s+\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}", entry.cmd)
    source = (f"{ref.group(1)} as compose renders {CHAT_SERVICE}, through llama-swap entry {model_id!r}"
              if ref and env.get(ref.group(1)) == flagged else f"the cmd of llama-swap entry {model_id!r}")
    return flagged, source


def _swap_config_target(spec: dict) -> str:
    """Where llama-swap reads its config: the rendered command's `-config`, an ABSOLUTE path of plain
    names. Absent, relative (it would depend on the working directory) or `=`-form: refused."""
    flagged = _flag(_command(spec, CHAT_SERVICE), ("-config", "--config"))
    if flagged is None:
        raise LabelError(f"{CHAT_SERVICE}'s rendered command has no `-config` - llama-swap's default location "
                         f"is not interpreted here")
    if not flagged.startswith("/"):
        raise LabelError(f"{CHAT_SERVICE}'s `-config {flagged}` is relative - it depends on the working "
                         f"directory, which is not interpreted here")
    _check_segments(flagged[1:], flagged)
    return flagged


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


def served_ids_from(text: str, where: str) -> list[str]:
    """Every `model_name` a LiteLLM model_list fragment registers, in file order, once each."""
    ids: list[str] = []
    for name, _model in parse_model_list(text):
        if name not in ids:
            ids.append(name)
    if not ids:
        raise LabelError(f"{where} registers no model")
    return ids


def served_ids(render: dict) -> list[str]:
    """Every id the LOCAL gateway serves: the `model_name`s of the local.yaml llm-gateway's
    /app/conf.d bind holds in compose's render - the same file the roles are read from."""
    try:
        gateway = _service(render, GATEWAY_SERVICE)
        local = _bind_source(gateway, GATEWAY_SERVICE, FRAGMENT_TARGET) / LOCAL_FRAGMENT
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        raise LabelError(f"the render is not the shape `docker compose config` writes "
                         f"({type(exc).__name__}: {str(exc)[:160]})") from None
    if not local.is_file():
        raise LabelError(f"{local} (llm-gateway's {FRAGMENT_TARGET}/{LOCAL_FRAGMENT}) does not exist")
    return served_ids_from(local.read_text(encoding="utf-8"), str(local))


def picker_hidden(labels: list[RoleLabel], served=()) -> dict[str, bool]:
    """{id: hidden} for every managed id: the roles, then every other served id (hidden).

    Chat roles are ranked by CHAT_ROLE_ORDER (others after, in the order given); the first
    of each LABEL is shown, the rest hidden. The embedding role is hidden: it cannot chat."""
    rank = {name: i for i, name in enumerate(CHAT_ROLE_ORDER)}
    ranked = sorted(enumerate(labels), key=lambda pair: (rank.get(pair[1].role, len(rank)), pair[0]))
    shown: set[str] = set()
    out: dict[str, bool] = {}
    for _i, item in ranked:
        if item.mode == "embeddings":
            out[item.role] = True
            continue
        out[item.role] = item.label in shown
        shown.add(item.label)
    hidden = {item.role: out[item.role] for item in labels}   # in the order given
    for sid in served:
        hidden.setdefault(sid, True)
    return hidden


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


def _check_container_path(container_path: str, service: str) -> None:
    """`/models/<segments>` with every segment passing `_check_segments`. Nothing is normalised."""
    if not isinstance(container_path, str) or not container_path.startswith(MODELS_MOUNT + "/"):
        raise LabelError(f"{container_path} is not under {service}'s {MODELS_MOUNT} bind")
    if not _safe(container_path):
        raise LabelError(f"{container_path!r} has a character outside the allowlist [A-Za-z0-9_./:,+=@-] (as "
                         f"compose renders it; a literal `$` renders as `$$`) - not interpreted here")
    _check_segments(container_path[len(MODELS_MOUNT) + 1:], container_path)


# REFUSE, DON'T EMULATE (tester attempts 1-5). Every shape below is one where a Windows host
# and the Linux container read the same path differently (Win32 strips a trailing `.` or space
# and maps `sub::$INDEX_ALLOCATION` to `sub`; `x.gguf/` and `x.gguf/.` open on one and fail
# ENOTDIR on the other; `nosuch/../x` resolves lexically on one and ENOENTs on the other), or
# one this module would have to emulate the kernel for. They are refused, not interpreted.
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]|CONIN\$|CONOUT\$)(?:\..*)?$", re.IGNORECASE)
_WINDOWS_ONLY_CHARS = set('<>:"|?*\\') | {chr(c) for c in range(32)}
_SHORT_NAME = re.compile(r"~[0-9]")


def _check_segments(rel: str, whole: str) -> None:
    """A relative path of plain names: no empty, `.` or `..` segment (so no `//`, no trailing `/`),
    no segment ending in `.` or a space, no `:` (an NTFS stream), no character or reserved device
    name Windows treats specially, no 8.3 short-name form. LabelError naming the first offence."""
    if not rel:
        raise LabelError(f"{whole!r} names no file")
    for segment in rel.split("/"):
        why = ""
        if segment in ("", ".", ".."):
            why = "an empty, `.` or `..` segment"
        elif segment[-1] in ". ":
            why = f"the segment {segment!r} ends in `.` or a space (Windows strips it, Linux does not)"
        elif set(segment) & _WINDOWS_ONLY_CHARS:
            why = f"the segment {segment!r} has a character Windows treats specially (`:` is an NTFS stream)"
        elif _WINDOWS_RESERVED.match(segment):
            why = f"the segment {segment!r} is a Windows device name"
        elif _SHORT_NAME.search(segment):
            why = f"the segment {segment!r} looks like an 8.3 short name (host-only name normalisation)"
        if why:
            raise LabelError(f"{whole!r} is refused: {why} - a path the host and the container could read "
                             f"differently is not labelled")


_REPARSE_POINT = 0x400   # FILE_ATTRIBUTE_REPARSE_POINT


def _is_reparse_point(path: Path) -> bool:
    """A Windows junction (or any other reparse point) that is not a symlink: `is_symlink()` is
    False for it and the HOST follows it, but inside the container it is a link to a host-absolute
    path (/mnt/host/c/...) the container cannot open."""
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(path):
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _REPARSE_POINT)


def _absolute_link_target(target: str) -> bool:
    return (target.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", target))
            or target.startswith("\\\\?\\"))


def resolve_in_store(store: Path, container_path: str, role: str) -> Path:
    """The file `container_path` names, found by following symlinks one component at a time.

    `container_path` has already passed `_check_container_path` (plain names only). Every
    component before the last must be an existing DIRECTORY and the last a regular FILE, each
    checked on the host. A symlink's target must be RELATIVE (a host-absolute one names nothing
    in the container) and pass the same plain-names rule - so no `..`, and a link can only lead
    DOWN from its own directory, never out of the store. A Windows junction is refused (the host
    follows it; the container sees a host-absolute link). The label is then taken from the file
    finally reached: a link `Claims-70B-Q2_K.gguf` -> `real/Inside-7B-Q8_0.gguf` labels Inside-7B.
    """
    hops = [0]
    return _walk(store, container_path[len(MODELS_MOUNT) + 1:].split("/"), "file", role, container_path, hops)


def _walk(base: Path, parts: list[str], want: str, role: str, whole: str, hops: list[int]) -> Path:
    cur = base
    for i, part in enumerate(parts):
        here = cur / part
        need = want if i == len(parts) - 1 else "dir"
        if not here.is_symlink() and _is_reparse_point(here):
            raise LabelError(f"role {role}: {here} is a junction/reparse point - the host follows it, but the "
                             f"container sees a link to a host-absolute path it cannot open")
        if here.is_symlink():
            hops[0] += 1
            if hops[0] > 40:
                raise LabelError(f"role {role}: {whole} has a symlink loop")
            target = os.readlink(here)
            if _absolute_link_target(target):
                raise LabelError(f"role {role}: {here} is a symlink to the host-absolute path {target} - the "
                                 f"container cannot open it")
            # Windows stores a relative link target with `\` separators; the container reads the
            # same link through the bind (measured: tester attempt 5, row "backslash link target")
            target = target.replace("\\", "/")
            _check_segments(target, f"{here} -> {target}")
            here = _walk(cur, target.split("/"), need, role, whole, hops)
        if need == "dir" and not here.is_dir():
            raise LabelError(f"role {role}: {whole} - {here} is not a directory on this machine")
        if need == "file" and (not here.is_file() or here.is_dir()):
            raise LabelError(f"role {role}: {whole} is not a file on this machine - looked for {here}")
        cur = here
    return cur


def derive_labels(render: dict, check_files: bool = True, git=None, root=None) -> list[RoleLabel]:
    """Every role's label from compose's render. LabelError, naming the cause, rather than a partial set."""
    try:
        return _derive(render, check_files, git, root)
    except UnicodeError:
        raise   # a file the render points at is not UTF-8: the caller names that, not "the render"
    except (TypeError, AttributeError, KeyError, ValueError, RecursionError) as exc:
        # a render that is not the shape compose writes (a hostile or hand-made --render)
        raise LabelError(f"the render is not the shape `docker compose config` writes "
                         f"({type(exc).__name__}: {str(exc)[:160]})") from None


def _derive(render: dict, check_files: bool, git=None, root=None) -> list[RoleLabel]:
    if not isinstance(render, dict) or not isinstance(render.get("services"), dict):
        raise LabelError("the render has no services")
    gateway = _service(render, GATEWAY_SERVICE)
    fragments = _bind_source(gateway, GATEWAY_SERVICE, FRAGMENT_TARGET)
    local = fragments / LOCAL_FRAGMENT
    if not local.is_file():
        raise LabelError(f"{local} (llm-gateway's {FRAGMENT_TARGET}/{LOCAL_FRAGMENT}) does not exist")
    role_list = roles_from(local.read_text(encoding="utf-8"), str(local))
    chat = _service(render, CHAT_SERVICE)
    swap_target = _swap_config_target(chat)
    swap_path = _bind_source(chat, CHAT_SERVICE, swap_target)
    if not swap_path.is_file():
        raise LabelError(f"{swap_path} ({CHAT_SERVICE}'s {swap_target}) does not exist")
    swap_text = check_committed(swap_path, root if root is not None else STACK_ROOT, git)
    check_swap_config_subset(swap_text)
    check_swap_env_placement(swap_text)
    macros, swap_entries = parse_llama_swap_config(swap_text)
    swap_models = {model: entry.cmd for model, entry in swap_entries.items()}
    out = []
    for role in role_list:
        embed = _served_by_embed(role.concrete, swap_models)
        if embed is None:
            raise LabelError(f"role {role.name} forwards {role.concrete!r}, which no upstream serves: it is "
                             f"not a model id in the llama-swap config and not a bge* embedding id")
        if embed:
            service = EMBED_SERVICE
            spec = _service(render, service)
            container, source = _embed_model(spec)
        else:
            service, spec = CHAT_SERVICE, chat
            model_id = role.concrete.split(":", 1)[0]
            container, source = _swap_model(swap_entries[model_id], macros, spec, model_id, swap_text)
        _check_container_path(container, service)
        host = ""
        if check_files:
            # followed component by component, links first, as the container's kernel does
            final = resolve_in_store(_bind_source(spec, service, MODELS_MOUNT), container, role.name)
            host, name = str(final), final.name
        else:
            name = container   # nothing on disk to follow; the output says `file NOT checked`
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
    role: str     # the row id: a role, or another id the local gateway serves
    action: str   # created | renamed | hidden | shown | unchanged | absent, or would-<create|rename|hide|show>
    old: str
    new: str
    hidden: bool | None = None       # the row's picker visibility after the run (None: no row)
    was_hidden: bool | None = None   # before the run (None: there was no row)
    had_hidden_key: bool | None = None   # the row's meta HAD a `hidden` key before the run (None: no row)


WRITTEN = ("created", "renamed", "hidden", "shown")


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
              dry_run: bool = False, served=()) -> list[Change]:
    """Set each role's Open WebUI row NAME to its label and every managed row's picker
    visibility (`meta.hidden`, see picker_hidden). Idempotent; writes only a differing row.

    MANAGED ids are the roles plus `served` - every id the local gateway serves; nothing
    else is read or written. TWO PASSES. Pass 1 READS every managed row and validates all
    of them - a refused key, an unreadable row, a PRESET on a managed id, a row returned
    without its access grants, an unreadable model list - and raises before ANY write if
    one fails, so a refusal never leaves a partial run. A served non-role id with no row is
    created (hidden) only when Open WebUI lists it (GET /api/models, read once, only if
    such an id exists): `meta.hidden` lives on a row, and without one Open WebUI shows it to
    admins. Pass 2 writes, in order, only the rows pass 1 planned to create or change, and
    re-reads each row just before its write, refusing if it changed since pass 1. A write
    that fails in pass 2 (Open WebUI refusing it, or a row changed meanwhile) is the one
    case that can leave earlier rows written; the error names them, and each of those rows
    carries its correct new name and visibility.
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

    def refused(status, what):
        return OwuiError(f"Open WebUI refused the key ({status}) reading {what}: {OWUI_KEY_VAR} must be "
                         f"an ADMIN user's API key, with API keys enabled (Admin Settings > General). "
                         f"Nothing was written.")

    hidden_of = picker_hidden(labels, served)
    names = {item.role: item.label for item in labels}   # a served non-role id keeps its name
    listed: dict[str, str] | None = None                  # GET /api/models, read at most once

    def listing() -> dict[str, str]:
        nonlocal listed
        if listed is None:
            status, data, text = call("GET", "/api/models")
            if status in (401, 403):
                raise refused(status, "the model list")
            items = data.get("data") if isinstance(data, dict) else None
            if status != 200 or not isinstance(items, list):
                raise OwuiError(f"reading Open WebUI's model list failed: HTTP {status} {text[:200]!r}. "
                                f"Nothing was written.")
            listed = {m["id"]: str(m.get("name") or m["id"]) for m in items
                      if isinstance(m, dict) and isinstance(m.get("id"), str)}
        return listed

    # ---- pass 1: read and validate every managed row; nothing is written ----
    # (id, create|update|unchanged|absent, row, target name, target hidden)
    plan: list[tuple[str, str, dict | None, str, bool]] = []
    for rid, hide in hidden_of.items():
        kind = "role" if rid in names else "served id"
        status, row, text = call("GET", "/api/v1/models/model?id=" + urllib.parse.quote(rid, safe=""))
        if status in (401, 403):
            raise refused(status, rid)
        if status == 404:
            if rid in names:
                plan.append((rid, "create", None, names[rid], hide))
            elif rid in listing():
                plan.append((rid, "create", None, listing()[rid], hide))
            else:
                plan.append((rid, "absent", None, "", hide))
            continue
        if status != 200 or not isinstance(row, dict) or not isinstance(row.get("meta") or {}, dict):
            raise OwuiError(f"reading the {rid} row failed: HTTP {status} {text[:200]!r}. "
                            f"Nothing was written.")
        if row.get("base_model_id"):
            raise OwuiError(f"the Open WebUI row {rid!r} (a {kind} the local gateway serves) is a PRESET on "
                            f"{row.get('base_model_id')!r}, not a base-model row; it was left alone. "
                            f"Nothing was written.")
        old = row.get("name") or ""
        name = names.get(rid, old)
        if old == name and bool((row.get("meta") or {}).get("hidden")) == hide:
            plan.append((rid, "unchanged", row, name, hide))
            continue
        # Everything but the name and `meta.hidden` is sent back as it was read - the
        # access grants too. Open WebUI 0.11.0's update REPLACES a row's grants with the
        # list it is sent, and an update WITHOUT the field fails (HTTP 500: its router
        # re-validates `access_grants=None` against `list`; measured against a disposable
        # 0.11.0 by the mr-gateway drill). A row whose grants were not returned is refused
        # rather than rewritten with none.
        if not isinstance(row.get("access_grants"), list):
            raise OwuiError(f"the {rid} row came back without its access grants, so writing it would "
                            f"replace them; it was left alone. Nothing was written.")
        plan.append((rid, "update", row, name, hide))

    # ---- pass 2: write what pass 1 planned ----
    changes: list[Change] = []
    done = lambda: ", ".join(f"{c.role} {c.action}" for c in changes  # noqa: E731
                             if c.action in WRITTEN) or "nothing"
    for rid, action, row, name, hide in plan:
        old = (row or {}).get("name") or ""
        was = None if row is None else bool((row.get("meta") or {}).get("hidden"))
        key = None if row is None else "hidden" in (row.get("meta") or {})
        if action == "unchanged":
            changes.append(Change(rid, "unchanged", old, name, hide, was, key))
            continue
        if action == "absent":
            changes.append(Change(rid, "absent", "", "", None, None))
            continue
        if action == "create":
            verb = "create"
        elif old != name:
            verb = "rename"
        else:
            verb = "hide" if hide else "show"
        if dry_run:
            changes.append(Change(rid, "would-" + verb, old, name, hide, was, key))
            continue
        # RE-READ just before the write and refuse if the row moved since pass 1 (someone
        # made it a preset, renamed it, changed its grants, meta, params or active flag, created
        # or deleted it): a write built from the pass-1 snapshot would overwrite that change.
        # What is NOT closed: a change landing between this GET and the POST (one round trip;
        # Open WebUI has no conditional update) is overwritten.
        status, now, text = call("GET", "/api/v1/models/model?id=" + urllib.parse.quote(rid, safe=""))
        if _fingerprint(status, now) != _fingerprint(200 if row is not None else 404, row):
            raise OwuiError(f"the {rid} row changed in Open WebUI while this sync ran (HTTP {status}); "
                            f"it was left alone - run `labels` again. Written before this: {done()}")
        if action == "create":
            payload = {"id": rid, "base_model_id": None, "name": name,
                       "meta": {"profile_image_url": "/static/favicon.png", "hidden": hide},
                       "params": {}, "is_active": True}
            status, made, text = call("POST", "/api/v1/models/create", payload)
        else:
            meta = dict(row.get("meta") or {})
            if was != hide:
                meta["hidden"] = hide
            grants = [{"principal_type": g.get("principal_type"), "principal_id": g.get("principal_id"),
                       "permission": g.get("permission")} for g in row["access_grants"] if isinstance(g, dict)]
            payload = {"id": rid, "base_model_id": None, "name": name, "meta": meta,
                       "params": row.get("params") or {}, "access_grants": grants,
                       "is_active": bool(row.get("is_active", True))}
            status, made, text = call("POST", "/api/v1/models/model/update", payload)
        if (status != 200 or not isinstance(made, dict) or made.get("name") != name
                or bool((made.get("meta") or {}).get("hidden")) != hide):
            raise OwuiError(f"{'creating' if action == 'create' else 'updating'} the {rid} row failed: "
                            f"HTTP {status} {text[:200]!r}. Written before this: {done()}")
        past = {"create": "created", "rename": "renamed", "hide": "hidden", "show": "shown"}[verb]
        changes.append(Change(rid, past, old, name, hide, was, key))
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


def rollback_steps(changes: list[Change]) -> list[str]:
    """What undoes a real run, one mechanical step per written row, in the order written.

    A CREATED row is deleted (setting `meta.hidden` false would leave a row where there was
    none - and a row, even a visible one, changes who may use that id; findings F4); a
    renamed or re-hidden row gets its old name / old `meta.hidden` back - and a row whose
    meta had NO `hidden` key gets the key REMOVED, not set to false, so the row is restored
    as it was, not only to the same behaviour."""
    steps = []
    for c in changes:
        if c.action == "created":
            steps.append(f"delete the row {c.role!r} (this run created it): "
                         f"POST /api/v1/models/model/delete {{\"id\": {json.dumps(c.role)}}}")
            continue
        if c.action not in WRITTEN:
            continue
        undo = []
        if c.old != c.new:
            undo.append(f"name back to {c.old!r}")
        if c.was_hidden is not None and c.was_hidden != c.hidden:
            if c.had_hidden_key is False:
                undo.append("meta.hidden REMOVED (the row had no such key)")
            else:
                undo.append(f"meta.hidden back to {str(bool(c.was_hidden)).lower()}")
        if undo:
            steps.append(f"row {c.role!r}: {' and '.join(undo)} (POST /api/v1/models/model/update, "
                         f"everything else as read)")
    return steps


def _picker(hidden: bool | None) -> str:
    return "hidden from the picker" if hidden else "shown in the picker"


def describe(change: Change) -> str:
    c = change
    if c.action == "absent":
        return f"{c.role}: no row, and Open WebUI does not list it - nothing to hide, no row created"
    if c.action in ("created", "would-create"):
        return f"{c.role}: {c.action} as {c.new!r}, {_picker(c.hidden)}"
    if c.action == "unchanged":
        return f"{c.role}: unchanged ({c.new!r}, {_picker(c.hidden)})"
    if c.action in ("hidden", "would-hide", "shown", "would-show"):
        verb = "would be" if c.action.startswith("would-") else "now"
        return f"{c.role}: {verb} {_picker(c.hidden)} ({c.new!r})"
    text = f"{c.role}: {c.action} {c.old!r} -> {c.new!r}"
    if c.was_hidden is not None and c.hidden is not None and c.was_hidden != c.hidden:
        text += f", {'would be' if c.action.startswith('would-') else 'now'} {_picker(c.hidden)}"
    return text


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
        labels = derive_labels(render, check_files=not args.skip_file_check, root=root)
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
