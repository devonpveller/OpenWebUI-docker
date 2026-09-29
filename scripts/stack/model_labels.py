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
              service's RENDERED environment). llama-swap splits it with a POSIX-shell
              lexer (quotes group, backslash escapes), which is not emulated: any
              `${env.*}` in the WHOLE config that is unset, empty, or has whitespace,
              a quote, a backslash or a control character, any literal cmd word with
              a quote, backslash or control character, a `--` or `#...` word, and a
              macro referencing a later one are refused; what is left is read with
              the llama.cpp flag rules below; its last `-m`/`--model` is the path
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
# llama-swap (v236, the pinned image) splits a cmd with a POSIX-shell lexer: quotes group words and
# are removed, a backslash escapes, and a newline in a substituted value breaks the config (tester
# attempt 7). This module does not emulate that lexer: any character that makes the lexer do
# more than split on whitespace is refused, so what is left splits exactly like str.split().
_LEXER_CHARS = set("'\"\\") | {chr(c) for c in range(32)} | {chr(127)}


def _unsafe(text: str) -> str:
    """The first character the lexer would treat specially (a quote, a backslash, a control
    character), or ''."""
    for ch in text:
        if ch in _LEXER_CHARS:
            return ch
    return ""


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
        if value in ("|", "|-", ">", ">-"):
            block, i = _block(lines, i + 1, _indent(raw), value)
            seen.append(block)
            continue
        i += 1
    return set(re.findall(r"\$\{env\.([^}]*)\}", "\n".join(seen)))


def check_swap_env(config_text: str, env: dict, who: str = "the llama-swap config") -> None:
    """Every `${env.VAR}` ANYWHERE in the llama-swap config must be set, non-empty, free of
    whitespace and of lexer characters: llama-swap substitutes them all when it loads the config,
    and one bad value in ANY entry makes it refuse the whole config (tester attempt 7) - so a label
    for the role's entry would then be a label for a plane that does not start."""
    for var in sorted(swap_env_refs(config_text)):
        value = env.get(var)
        if value is not None and not isinstance(value, str):
            raise LabelError(f"{who}: {var} in {CHAT_SERVICE}'s rendered environment is not a string")
        if not value:
            raise LabelError(f"{who}: {var} is not set in {CHAT_SERVICE}'s rendered environment "
                             f"(llama-swap substitutes every `${{env.*}}` in the config)")
        if any(ch.isspace() for ch in value) or _unsafe(value):
            raise LabelError(f"{who}: {var}={value!r} contains whitespace, a quote, a backslash or a control "
                             f"character - llama-swap's shell lexer would change the command it builds")


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
    with a POSIX-shell lexer (quotes group and are removed, a backslash escapes). REFUSE, DON'T
    EMULATE: every `${env.*}` in the WHOLE config must be set and free of whitespace, quotes,
    backslashes and control characters (check_swap_env); every literal word of the expanded cmd
    must be free of quotes, backslashes and control characters; a `--` word or a word starting
    with `#` is refused; macros may reference only earlier macros; an entry with keys other than
    cmd/filters/concurrencyLimit (a per-model `env:`, ...) or an unknown `${...}` is refused. What
    is left splits exactly on whitespace, and gets the embed server's flag rules (`_`/`-`,
    `=`-form and remote-model flags refused, the LAST `-m`/`--model` wins, none refused)."""
    who = f"llama-swap entry {model_id!r}"
    extra = sorted(entry.keys - {"cmd", "filters", "concurrencyLimit"})
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

    # the cmd with its macros, BEFORE env values go in: every literal word is checked
    literal_cmd = _REF.sub(literal, entry.cmd)
    for word in literal_cmd.split():
        ch = _unsafe(word)
        if ch:
            raise LabelError(f"{who}: the word {word!r} in its cmd has {ch!r} - llama-swap's shell lexer would "
                             f"treat it specially; not interpreted here")
        if word == "--" or word.startswith("#"):
            raise LabelError(f"{who}: its cmd has the word {word!r} (`--` or a `#` comment) - not interpreted here")
    cmd = _REF.sub(lambda m: m.group(0) if m.group(1) in _SWAP_BUILTINS else env[m.group(1)[4:]], literal_cmd)
    tokens = cmd.split()
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


def derive_labels(render: dict, check_files: bool = True) -> list[RoleLabel]:
    """Every role's label from compose's render. LabelError, naming the cause, rather than a partial set."""
    try:
        return _derive(render, check_files)
    except UnicodeError:
        raise   # a file the render points at is not UTF-8: the caller names that, not "the render"
    except (TypeError, AttributeError, KeyError, ValueError, RecursionError) as exc:
        # a render that is not the shape compose writes (a hostile or hand-made --render)
        raise LabelError(f"the render is not the shape `docker compose config` writes "
                         f"({type(exc).__name__}: {str(exc)[:160]})") from None


def _derive(render: dict, check_files: bool) -> list[RoleLabel]:
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
    swap_text = swap_path.read_text(encoding="utf-8")
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
