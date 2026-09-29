#!/usr/bin/env python3
"""drill_model_roles.py - prove the model roles end to end in DISPOSABLE containers.

Item mr-gateway (model-roles, 2026-09-28). The unit tests pin the logic with fakes;
this drill runs the real things, and nothing it starts is part of the live stack:

  gateway phase   the REAL LiteLLM image (the digest inference/compose/gateway.yml
                  pins), started exactly as that file starts it (assemble-config.py
                  entrypoint, the tree's base config + model_list fragments +
                  custom_callbacks.py, COMPOSE_PROFILES=local) with its own
                  Postgres, in front of the tree's llm-queue (built as
                  llm-queue:wt-<owner>) in front of two STAND-IN upstreams that
                  record the model id and path of every request. Every role and
                  every old name is sent through LiteLLM; the drill prints the
                  status and the id each stand-in received.
  owui phase      a disposable Open WebUI (--owui-image, default the image the
                  host's frontend runs, under a new name) wired to that LiteLLM;
                  an admin is signed up and given an API key, three rows are
                  seeded (a stale role row, an old-name row, a preset), and the
                  tree's `stack.py labels` is run from a python container on the
                  same network TWICE, then after a model-file swap, then with an
                  unresolvable path. The model and access-grant tables are dumped
                  (read-only sqlite) before and after each run.

ISOLATION. One `--internal` network (no route out, no host port) and every
container on it are named `mrg-<run>-*` and carry `ai-stack.harness.owner=<owner>`
plus `ai-stack.drill.run=<run>`, so scripts/agent-harness/reap.ps1 removes them if
this is killed. EVERY docker call goes through `_docker`, which refuses any verb
outside a fixed allowlist (run, exec, rm, inspect, logs, network create/rm/inspect,
build -t llm-queue:wt-* / mrg-sync:wt-*, image inspect) and any `rm` / `network rm` of
a name outside this run's prefix - no prune, no stop/restart/up of anything else, ever.
No image is pulled: the images must already be present (`image inspect` checks). The
one exception to "nothing fetched": the sync image mrg-sync:wt-<owner> is python:3.12-slim
plus git, and its build installs git from Debian's apt (the build's network, never the
drill network). Git is needed because labels are only derived from the COMMITTED
llama-swap config (operator decision, 2026-09-29): the scratch repo is a git checkout
committed on the host, and one run edits its config without committing (refused).

Run from a worktree, e.g.:
  python scripts/stack/drill_model_roles.py --owner wt-mr-gateway --tree . --phase all
  python scripts/stack/drill_model_roles.py --owner wt-mr-gateway --tree <0fb1c0c export> --phase gateway
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LITELLM_IMAGE = ("ghcr.io/berriai/litellm@sha256:"
                 "c98c9395c56a35b7abacff8269d43ff99aabacb62bbf42a04cc1514fcb9bde4a")
POSTGRES_IMAGE = "postgres:16-alpine"
PYTHON_IMAGE = "python:3.12-slim"
CLI_IMAGE = "docker:27-cli"      # carries the compose plugin; runs `config` only, with no socket
OWUI_IMAGE = "openwebui:local"
ROLES = ["local-large", "local-large:nothink", "local-small", "local-small:nothink", "local-embed"]
OLD = ["qwen36-27b", "qwen36-27b:nothink", "bge-m3", "bge-m3-f16.gguf", "qllama/bge-m3:latest"]
EXPECT = {"local-large": "qwen36-27b", "local-large:nothink": "qwen36-27b:nothink",
          "local-small": "qwen36-27b:nothink", "local-small:nothink": "qwen36-27b:nothink",
          "local-embed": "bge-m3", "qwen36-27b": "qwen36-27b", "qwen36-27b:nothink": "qwen36-27b:nothink",
          "bge-m3": "bge-m3", "bge-m3-f16.gguf": "bge-m3", "qllama/bge-m3:latest": "bge-m3"}

RUN = secrets.token_hex(3)
PREFIX = f"mrg-{RUN}-"
OWNER = "wt-mr-gateway"
FAILS: list[str] = []

STANDIN = r'''
import json, http.server
SEEN = []
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/seen": return self._send(200, SEEN)
        return self._send(200, {"status": "ok"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        m = req.get("model"); SEEN.append({"path": self.path, "model": m})
        if self.path.endswith("/embeddings"):
            return self._send(200, {"object": "list", "model": m, "usage": {"prompt_tokens": 1, "total_tokens": 1},
                                    "data": [{"object": "embedding", "index": 0, "embedding": [0.0] * 1024}]})
        return self._send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": m,
                                "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": "pong"}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
http.server.ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
'''


def say(line=""):
    print(line, flush=True)


def check(ok: bool, what: str):
    say(f"  [{'OK' if ok else 'FAIL'}] {what}")
    if not ok:
        FAILS.append(what)


_ALLOWED = {("run",), ("exec",), ("rm",), ("inspect",), ("logs",), ("network", "create"), ("network", "rm"),
            ("network", "inspect"), ("image", "inspect"), ("build",)}


def _docker(*args, input_text=None, check_rc=True, quiet=False):
    """The ONE door to docker. Refuses anything this drill has no business doing."""
    args = [str(a) for a in args]
    verb = tuple(args[:2]) if args[0] in ("network", "image") else (args[0],)
    if verb not in _ALLOWED:
        raise SystemExit(f"drill refused docker {' '.join(args)}: verb not allowed")
    if verb in (("rm",), ("network", "rm")):
        for name in [a for a in args[len(verb):] if not a.startswith("-")]:
            if not name.startswith(PREFIX):
                raise SystemExit(f"drill refused to remove {name!r}: not this run's ({PREFIX}*)")
    if verb == ("build",) and not any(re.fullmatch(r"(llm-queue|mrg-sync):wt-[\w.-]+", a) for a in args):
        raise SystemExit("drill refused a build that is not tagged llm-queue:wt-* or mrg-sync:wt-*")
    proc = subprocess.run(["docker", *args], input=input_text, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check_rc and proc.returncode != 0:
        raise SystemExit(f"docker {' '.join(args[:3])} ... exited {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout).strip()[:400]}")
    if not quiet and proc.returncode != 0:
        say(f"  (docker {' '.join(args[:2])} exited {proc.returncode})")
    return proc


def labels():
    return ["--label", f"ai-stack.harness.owner={OWNER}", "--label", f"ai-stack.drill.run={RUN}"]


def bind(src: Path, dst: str, ro=True):
    return ["--mount", f"type=bind,src={src},dst={dst}" + (",readonly" if ro else "")]


def run_container(name, image, *args, env=None, mounts=(), aliases=(), entrypoint=None, cmd=()):
    argv = ["run", "-d", "--name", PREFIX + name, "--network", NET, *labels()]
    for alias in aliases:
        argv += ["--network-alias", alias]
    for k, v in (env or {}).items():
        argv += ["-e", f"{k}={v}"]
    for m in mounts:
        argv += m
    if entrypoint:
        argv += ["--entrypoint", entrypoint]
    argv += [*args, image, *cmd]
    _docker(*argv)
    return PREFIX + name


def pyexec(container, code, timeout=60):
    """Run python -c inside a container; (rc, stdout)."""
    proc = subprocess.run(["docker", "exec", container, "python", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def wait_for(what, probe, budget=300):
    deadline = time.time() + budget
    while time.time() < deadline:
        if probe():
            say(f"  ready: {what}")
            return True
        time.sleep(3)
    say(f"  NOT ready after {budget}s: {what}")
    return False


# --------------------------------------------------------------------------
# gateway phase
# --------------------------------------------------------------------------

CALL = r'''
import json, sys, urllib.request, urllib.error
name, kind, key = sys.argv[1], sys.argv[2], sys.argv[3]
body = {"model": name, "input": "ping"} if kind == "embed" else \
       {"model": name, "max_tokens": 3, "messages": [{"role": "user", "content": "ping"}]}
path = "/v1/embeddings" if kind == "embed" else "/v1/chat/completions"
req = urllib.request.Request("http://localhost:8080" + path, data=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print(r.status, json.loads(r.read()).get("model"))
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode("utf-8", "replace")[:300].replace("\n", " "))
'''

SEEN = "import json,urllib.request;print(urllib.request.urlopen('http://localhost:8080/seen').read().decode())"


def _landing_script(tree: Path) -> str:
    """The in-container landing-completion script of the tree's OWN stack.py, with its port."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tree_stack", tree / "scripts" / "stack" / "stack.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(tree / "scripts" / "stack"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod._LANDING_COMPLETION


def gateway_phase(tree: Path, owner: str, key: str) -> str:
    say("== gateway phase: LiteLLM (pinned digest) -> llm-queue (the tree's) -> recording stand-ins")
    for image in (LITELLM_IMAGE, POSTGRES_IMAGE, PYTHON_IMAGE):
        _docker("image", "inspect", image, "--format", "{{.Id}}")
    queue_tag = f"llm-queue:{owner}" if owner.startswith("wt-") else f"llm-queue:wt-{owner}"
    say(f"  building {queue_tag} from {tree / 'inference' / 'llm-queue'}")
    _docker("build", "-q", "-t", queue_tag, "--label", f"ai-stack.harness.owner={owner}",
            str(tree / "inference" / "llm-queue"))
    chat = run_container("chat", PYTHON_IMAGE, cmd=["python", "-c", STANDIN])
    embed = run_container("embed", PYTHON_IMAGE, cmd=["python", "-c", STANDIN])
    run_container("queue", queue_tag, aliases=["llm-queue"], env={
        "LLM_QUEUE_UPSTREAM_BASE_URL": f"http://{chat}:8080",
        "LLM_QUEUE_EMBED_UPSTREAM_BASE_URL": f"http://{embed}:8080"})
    db = run_container("db", POSTGRES_IMAGE, env={"POSTGRES_DB": "litellm", "POSTGRES_USER": "litellm",
                                                  "POSTGRES_PASSWORD": "drill"})
    cfg = tree / "inference" / "config"
    gw = run_container(
        "litellm", LITELLM_IMAGE,
        env={"DATABASE_URL": f"postgres://litellm:drill@{db}:5432/litellm", "LITELLM_MASTER_KEY": key,
             "LITELLM_LOCAL_MODEL_COST_MAP": "True", "COMPOSE_PROFILES": "local",
             # a dummy key REGISTERS cloud-large/cloud-small (listed first, as on a host with a
             # real key); the --internal network makes them unreachable, as the real gateway's is
             "OPENROUTER_API_KEY": "sk-or-drill-not-a-key"},
        mounts=[bind(cfg / "litellm.config.yaml", "/app/config.base.yaml"),
                bind(cfg / "litellm" / "model_list", "/app/conf.d"),
                bind(cfg / "litellm" / "assemble-config.py", "/app/assemble-config.py"),
                bind(cfg / "litellm" / "custom_callbacks.py", "/app/custom_callbacks.py")],
        entrypoint="/bin/sh",
        cmd=["-c", 'python /app/assemble-config.py && exec docker/prod_entrypoint.sh "$@"', "--",
             "--config", "/app/config.yaml", "--port", "8080"])
    live = ("import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health/liveliness').status==200 else 1)")
    if not wait_for("LiteLLM /health/liveliness", lambda: pyexec(gw, live, 15)[0] == 0, 420):
        say(_docker("logs", "--tail", "40", gw, check_rc=False).stdout)
        FAILS.append("LiteLLM did not start")
        return gw
    say("  assemble-config said: " + " | ".join(
        ln for ln in (_docker("logs", gw, check_rc=False).stderr or "").splitlines() if "[assemble-config]" in ln))
    say("  -- stack.py's landing probe, the TREE's own script, run inside this gateway (cloud models listed)")
    listed = pyexec(gw, "import json,urllib.request as u;r=u.Request('http://localhost:8080/v1/models',"
                        f"headers={{'Authorization':'Bearer {key}'}});"
                        "print([m['id'] for m in json.load(u.urlopen(r))['data']])", 30)[1].strip()
    say(f"  /v1/models: {listed}")
    script = _landing_script(tree)
    before = json.loads(pyexec(chat, SEEN)[1] or "[]")
    proc = _docker("exec", "-e", f"LITELLM_MASTER_KEY={key}", gw, "python", "-c", script, check_rc=False, quiet=True)
    after = json.loads(pyexec(chat, SEEN)[1] or "[]")
    line = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or ["<no output>"]
    say(f"  probe printed: {line[0][:300]}")
    say(f"  chat stand-in received: {[x['model'] for x in after[len(before):]]}")
    check(line[0].startswith("OK ") and "local-small" in line[0]
          and [x["model"] for x in after[len(before):]] == ["qwen36-27b:nothink"],
          "the landing probe asked for local-small (reached the chat upstream as qwen36-27b:nothink), "
          "not the cloud model listed first")
    for name in ROLES + OLD:
        kind = "embed" if EXPECT[name] == "bge-m3" else "chat"
        target = embed if kind == "embed" else chat
        before = json.loads(pyexec(target, SEEN)[1] or "[]")
        rc, out = pyexec(gw, CALL.replace("sys.argv[1], sys.argv[2], sys.argv[3]", f"{name!r}, {kind!r}, {key!r}"),
                         180)
        after = json.loads(pyexec(target, SEEN)[1] or "[]")
        got = [s["model"] for s in after[len(before):]]
        status = out.strip().split(" ", 1)[0]
        say(f"  {name:<22} -> HTTP {out.strip()[:160]} | stand-in {target[len(PREFIX):]} received {got}")
        check(status == "200" and got == [EXPECT[name]],
              f"{name}: 200 and the {kind} upstream received exactly {EXPECT[name]!r}")
    return gw


# --------------------------------------------------------------------------
# owui phase
# --------------------------------------------------------------------------

DUMP = r'''
import sqlite3, json
c = sqlite3.connect("file:/app/backend/data/webui.db?mode=ro", uri=True)
rows = [dict(zip(["id","user_id","base_model_id","name","meta","params","is_active","created_at","updated_at"], r))
        for r in c.execute("select id,user_id,base_model_id,name,meta,params,is_active,created_at,updated_at "
                           "from model order by id")]
grants = [list(r) for r in c.execute("select resource_id,principal_type,principal_id,permission from access_grant "
                                     "where resource_type='model' order by resource_id,principal_id,permission")]
print(json.dumps({"model": rows, "grants": grants}))
'''

OWUI_API = r'''
import json, sys, urllib.request, urllib.error
method, path, token, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
req = urllib.request.Request("http://localhost:8080" + path, method=method,
                             data=(body.encode() if body else None),
                             headers={"Content-Type": "application/json",
                                      **({"Authorization": "Bearer " + token} if token else {})})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print(r.status); print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode())
'''


def owui_call(owui, method, path, token="", body=None):
    code = OWUI_API.replace("sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]",
                            f"{method!r}, {path!r}, {token!r}, {json.dumps(body) if body is not None else ''!r}")
    _rc, out = pyexec(owui, code, 90)
    status, _, text = out.partition("\n")
    try:
        return int(status.strip()), json.loads(text) if text.strip() else None
    except ValueError:
        return 0, out


def dump(owui):
    rc, out = pyexec(owui, DUMP, 60)
    return json.loads(out.strip().splitlines()[-1])


def _rmtree(path: Path) -> None:
    """shutil.rmtree that also removes git's read-only object files (Windows)."""
    import stat

    def unlock(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onexc=unlock) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=unlock)


def build_repo(tree: Path, scratch: Path, model_path: str, key: str, owui: str) -> Path:
    """A scratch repo the render and sync containers mount: the tree's driver and its whole
    inference plane (compose files, config, llm-queue build context), no secrets - its
    inference/.env is written here, with placeholders for what the render requires."""
    repo = scratch / "repo"
    if repo.exists():
        _rmtree(repo)
    for rel in ("stack.manifest.toml", "scripts/stack/stack.py", "scripts/stack/model_labels.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(tree / rel, repo / rel)
    shutil.copytree(tree / "inference", repo / "inference",
                    ignore=shutil.ignore_patterns(".env", "__pycache__", ".pytest_cache", "*.egg-info"))
    # stand-in model files (empty): the label comes from the NAME, the check from existence
    store = scratch / "store"
    for rel in ("unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf", "vendor/Other-14B-GGUF/Other-14B-Q8_0.gguf"):
        (store / rel).parent.mkdir(parents=True, exist_ok=True)
        (store / rel).write_bytes(b"")
    (repo / "data/models/embeddings").mkdir(parents=True, exist_ok=True)
    (repo / "data/models/embeddings/bge-m3-f16.gguf").write_bytes(b"")
    (repo / "inference/.env").write_text(
        "COMPOSE_PROFILES=local\nLITELLM_DB_PASSWORD=drill\nLITELLM_MASTER_KEY=sk-drill\n"
        f"LM_MODELS_DIR=/store\nLLAMA_SWAP_QWEN36_27B_MODEL_PATH={model_path}\n",
        encoding="utf-8", newline="\n")
    (repo / ".env").write_text(f"OWUI_ADMIN_API_KEY={key}\nOWUI_BASE_URL=http://{owui}:8080\n",
                               encoding="utf-8", newline="\n")
    # labels come only from the COMMITTED llama-swap config: make the scratch repo a checkout with
    # everything committed (a throwaway identity, no hooks, no line-ending conversion)
    nohooks = scratch / "nohooks"
    nohooks.mkdir(exist_ok=True)
    git = ["git", "-c", "user.name=drill", "-c", "user.email=drill@drill.invalid", "-c", "commit.gpgsign=false",
           "-c", f"core.hooksPath={nohooks}", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run([*git, "config", "core.autocrlf", "false"], check=True, capture_output=True)
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "drill"], check=True, capture_output=True)
    return repo


def sync_image(owner: str) -> str:
    """python:3.12-slim + git, tagged mrg-sync:wt-<owner> (a test tag, never :local)."""
    tag = f"mrg-sync:{owner}" if owner.startswith("wt-") else f"mrg-sync:wt-{owner}"
    dockerfile = ("FROM python:3.12-slim\n"
                  "RUN apt-get update && apt-get install -y --no-install-recommends git "
                  "&& rm -rf /var/lib/apt/lists/*\n")
    _docker("build", "-q", "-t", tag, "--label", f"ai-stack.harness.owner={owner}", "-", input_text=dockerfile)
    return tag


SYNC_IMAGE = PYTHON_IMAGE   # replaced by sync_image() in the owui phase


def sync(scratch: Path, repo: Path, args=("labels",)):
    """Render the scratch repo's inference plane with COMPOSE (a `docker:*-cli` container, no
    network, no docker socket - `config` needs no daemon), then run the tree's `stack.py labels
    --render` from a python container on the drill network. Both mount the repo at /repo and
    the store at /store, so the render's paths are the paths the sync container sees."""
    out_dir = scratch / "out"
    out_dir.mkdir(exist_ok=True)
    render = _docker("run", "--rm", "--name", PREFIX + "render-" + secrets.token_hex(2), "--network", "none",
                     *labels(), "--mount", f"type=bind,src={repo},dst=/repo,readonly", "--mount",
                     f"type=bind,src={scratch / 'store'},dst=/store,readonly", "-w", "/repo", CLI_IMAGE,
                     "docker", "compose", "-f", "/repo/inference/docker-compose.yml", "--profile", "local",
                     "config", "--format", "json", check_rc=False, quiet=True)
    if render.returncode != 0:
        text = f"compose render FAILED (exit {render.returncode}): {(render.stderr or '').strip()[:300]}"
        say("    | " + text)
        return 1, text
    (out_dir / "render.json").write_text(render.stdout, encoding="utf-8")
    name = PREFIX + "sync-" + secrets.token_hex(2)
    proc = _docker("run", "--rm", "--name", name, "--network", NET, *labels(),
                   "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory", "-e", "GIT_CONFIG_VALUE_0=*",
                   "--mount", f"type=bind,src={repo},dst=/repo,readonly", "--mount",
                   f"type=bind,src={scratch / 'store'},dst=/store,readonly", "--mount",
                   f"type=bind,src={out_dir},dst=/out,readonly", SYNC_IMAGE,
                   "python", "/repo/scripts/stack/stack.py", "--root", "/repo", "--state", "/tmp/state.json",
                   *args, "--render", "/out/render.json", check_rc=False, quiet=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.strip().splitlines():
        say("    | " + line)
    return proc.returncode, out


def owui_phase(tree: Path, gw: str, master: str, image: str):
    global SYNC_IMAGE
    say(f"== owui phase: a disposable Open WebUI ({image}) wired to the drill LiteLLM")
    _docker("image", "inspect", image, "--format", "{{.Id}}")
    _docker("image", "inspect", CLI_IMAGE, "--format", "{{.Id}}")
    SYNC_IMAGE = sync_image(OWNER)
    say(f"  sync image {SYNC_IMAGE} (python:3.12-slim + git)")
    owui = run_container("owui", image, env={
        "WEBUI_SECRET_KEY": secrets.token_hex(16), "ENABLE_API_KEYS": "true", "ENABLE_OLLAMA_API": "false",
        "OPENAI_API_BASE_URL": f"http://{gw}:8080/v1", "OPENAI_API_KEY": master, "OFFLINE_MODE": "true",
        "HF_HUB_OFFLINE": "1", "RAG_EMBEDDING_ENGINE": "openai", "RAG_OPENAI_API_BASE_URL": f"http://{gw}:8080/v1",
        "RAG_OPENAI_API_KEY": master, "RAG_EMBEDDING_MODEL": "local-embed", "ENABLE_VERSION_UPDATE_CHECK": "false"})
    health = ("import urllib.request,sys;"
              "sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)")
    if not wait_for("Open WebUI /health", lambda: pyexec(owui, health, 15)[0] == 0, 600):
        say(_docker("logs", "--tail", "40", owui, check_rc=False).stdout)
        FAILS.append("Open WebUI did not start")
        return
    status, body = owui_call(owui, "POST", "/api/v1/auths/signup", body={
        "name": "drill admin", "email": "drill@drill.invalid", "password": secrets.token_hex(12)})
    check(status == 200 and body and body.get("role") == "admin", f"first signup is the admin (HTTP {status})")
    token = (body or {}).get("token", "")
    status, body = owui_call(owui, "POST", "/api/v1/auths/api_key", token)
    key = (body or {}).get("api_key", "")
    check(status == 200 and key.startswith("sk-"), f"an admin API key was issued (HTTP {status})")
    seeds = [{"id": "local-large", "base_model_id": None, "name": "Qwen 3.6 27B",
              "meta": {"profile_image_url": "/static/favicon.png", "capabilities": {"vision": False}},
              "params": {"function_calling": "native"}, "is_active": True,
              "access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}]},
             {"id": "qwen36-27b", "base_model_id": None, "name": "Qwen 3.6 27B",
              "meta": {"profile_image_url": "/static/favicon.png"}, "params": {}, "is_active": True},
             {"id": "writer", "base_model_id": "qwen36-27b", "name": "Writer",
              "meta": {"profile_image_url": "/static/favicon.png"}, "params": {"system": "be terse"},
              "is_active": True}]
    for seed in seeds:
        status, _ = owui_call(owui, "POST", "/api/v1/models/create", token, seed)
        check(status == 200, f"seeded row {seed['id']!r} (HTTP {status})")

    scratch = Path(tempfile.mkdtemp(prefix="mrg-drill-"))
    try:
        repo = build_repo(tree, scratch, "/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf", key, owui)
        t0 = dump(owui)
        say("  -- run 1: `stack.py labels` (expect local-large renamed, four created)")
        rc, out = sync(scratch, repo)
        t1 = dump(owui)
        check(rc == 0 and "5 row(s) changed" in out, "run 1 exit 0, 5 rows changed")
        if rc != 0:
            tail = _docker("logs", "--tail", "60", owui, check_rc=False)
            say("  Open WebUI log tail:")
            say(((tail.stdout or "") + (tail.stderr or ""))[-6000:])
        names = {r["id"]: r["name"] for r in t1["model"]}
        want = {"local-large": "Qwen3.8-27B Q4_K_M (thinking)",
                "local-large:nothink": "Qwen3.8-27B Q4_K_M (no thinking)",
                "local-small": "Qwen3.8-27B Q4_K_M (no thinking)",
                "local-small:nothink": "Qwen3.8-27B Q4_K_M (no thinking)",
                "local-embed": "bge-m3 f16 (embeddings)"}
        check({k: names.get(k) for k in want} == want, f"role rows carry the derived labels: "
              f"{ {k: names.get(k) for k in want} }")
        others0 = [r for r in t0["model"] if not r["id"].startswith("local-")]
        others1 = [r for r in t1["model"] if not r["id"].startswith("local-")]
        check(others0 == others1, "every non-role row is byte-identical before/after run 1 "
              f"({[r['id'] for r in others1]})")
        ll0 = next(r for r in t0["model"] if r["id"] == "local-large")
        ll1 = next(r for r in t1["model"] if r["id"] == "local-large")
        check({k: v for k, v in ll0.items() if k not in ("name", "updated_at")}
              == {k: v for k, v in ll1.items() if k not in ("name", "updated_at")},
              "the renamed row kept its meta, params, owner, active flag and created_at")
        check(t0["grants"] == t1["grants"] and any(g[0] == "local-large" for g in t1["grants"]),
              f"access grants unchanged, local-large's public read grant included: {t1['grants']}")

        say("  -- run 2: the same again (expect nothing changed, nothing written)")
        rc, out = sync(scratch, repo)
        t2 = dump(owui)
        check(rc == 0 and "0 row(s) changed, 5 already right" in out, "run 2 exit 0, 0 rows changed")
        check(t1 == t2, "the model and grant tables are byte-identical after run 2 (updated_at included)")

        status, body = owui_call(owui, "GET", "/api/models", key)
        listed = {m["id"]: m.get("name") for m in (body or {}).get("data", [])}
        say(f"  /api/models as the admin: { {k: listed.get(k) for k in ROLES} }")
        check(all(listed.get(k) == want[k] for k in ROLES), "Open WebUI's model list shows the derived labels")
        check(listed.get("qwen36-27b") == "Qwen 3.6 27B", "the old-name row still shows its own name")

        say("  -- swap: LLAMA_SWAP_QWEN36_27B_MODEL_PATH -> Other-14B-Q8_0.gguf (no hand edit of any label)")
        repo = build_repo(tree, scratch, "/models/vendor/Other-14B-GGUF/Other-14B-Q8_0.gguf", key, owui)
        rc, out = sync(scratch, repo)
        t3 = dump(owui)
        n3 = {r["id"]: r["name"] for r in t3["model"]}
        check(rc == 0 and n3.get("local-large") == "Other-14B Q8_0 (thinking)"
              and n3.get("local-small") == "Other-14B Q8_0 (no thinking)"
              and n3.get("local-embed") == "bge-m3 f16 (embeddings)", "the swap relabelled the chat roles only")

        say("  -- uncommitted: the llama-swap config edited but NOT committed (expect exit 1, nothing written)")
        cfg = repo / "inference" / "config" / "llama-swap.config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "# an uncommitted comment\n", encoding="utf-8", newline="\n")
        rc, out = sync(scratch, repo)
        t3b = dump(owui)
        check(rc == 1 and "differs from the committed version" in out and "llama-swap.config.yaml" in out,
              "exit 1 naming the config that differs from the committed version")
        check(t3 == t3b, "nothing was written by the refused run")

        say("  -- unresolvable: a path to a file that does not exist (expect exit 1, nothing written)")
        repo = build_repo(tree, scratch, "/models/vendor/Gone-GGUF/Gone-1B-Q4_0.gguf", key, owui)
        rc, out = sync(scratch, repo)
        t4 = dump(owui)
        check(rc == 1 and "FAILED" in out and "Gone-1B-Q4_0.gguf" in out, "exit 1 naming the missing file")
        check(t3 == t4, "nothing was written by the failed run")
    finally:
        try:
            _rmtree(scratch)
        except OSError as exc:
            say(f"  (scratch {scratch} not fully removed: {exc})")


# --------------------------------------------------------------------------


def cleanup():
    proc = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=ai-stack.drill.run={RUN}"],
                          capture_output=True, text=True)
    ids = proc.stdout.split()
    for cid in ids:
        name = subprocess.run(["docker", "inspect", "--format", "{{.Name}}", cid],
                              capture_output=True, text=True).stdout.strip().lstrip("/")
        if name.startswith(PREFIX):
            _docker("rm", "-f", "-v", name, check_rc=False, quiet=True)
    _docker("network", "rm", NET, check_rc=False, quiet=True)
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=ai-stack.drill.run={RUN}"],
                          capture_output=True, text=True).stdout.split()
    say(f"== cleanup: removed {len(ids)} container(s) and network {NET}; left behind: {len(left)}")


def main():
    global OWNER, NET
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--owner", required=True, help="the harness owner label (your worktree id)")
    ap.add_argument("--tree", required=True, help="the tree under test (a worktree, or an export of a commit)")
    ap.add_argument("--phase", choices=("gateway", "owui", "all"), default="all")
    ap.add_argument("--owui-image", default=OWUI_IMAGE)
    args = ap.parse_args()
    OWNER = args.owner
    NET = PREFIX + "net"
    if os.environ.get("DOCKER_HOST", "").startswith("tcp://127.0.0.1:1"):
        raise SystemExit("DOCKER_HOST points at the dead endpoint; this drill needs a daemon")
    tree = Path(args.tree).resolve()
    say(f"== drill run {RUN}, owner {OWNER}, tree {tree}, network {NET} (--internal)")
    _docker("network", "create", "--internal", *labels(), NET)
    try:
        master = "sk-drill-" + secrets.token_hex(12)
        gw = gateway_phase(tree, OWNER, master)
        if args.phase in ("owui", "all") and not FAILS:
            owui_phase(tree, gw, master, args.owui_image)
    finally:
        cleanup()
    say(f"== {len(FAILS)} failure(s)" + (": " + "; ".join(FAILS) if FAILS else ""))
    return 1 if FAILS else 0


NET = ""
if __name__ == "__main__":
    sys.exit(main())
