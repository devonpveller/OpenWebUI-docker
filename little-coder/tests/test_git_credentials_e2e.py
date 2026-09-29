"""END-TO-END: little-coder's real git paths against a real authenticating HTTPS git server
(cf-lc-token, 2026-09-28). Nothing here reaches the internet and no real token is used.

What it proves, with a DUMMY token and real git:
  * a clone/refresh/upstream/submodule made by WorkspaceManager leaves NO token in any git config
    in the workspace (before 2026-09-28 every one of them wrote it into `.git/config`);
  * the worker's own `git push` - through the git-proxy when it is installed - still authenticates;
  * the server really does refuse an unauthenticated clone, so the green is not vacuous;
  * the scrub removes a legacy token from a COPY of a workspace and is a no-op the second time.

It needs Linux, git, openssl, bash and port 443 on loopback, with `github.com` resolving to
127.0.0.1, so it is OPT-IN (`LC_GIT_E2E=1`) and runs in a disposable container:

    docker run --rm --network none --add-host github.com:127.0.0.1 -e LC_GIT_E2E=1 \\
      -v <little-coder>:/src:ro <image with pytest, e.g. FROM little-coder-open-terminal:local> \\
      sh -c 'cp -r /src /tmp/lc && cd /tmp/lc && python3 -m pytest -q tests/test_git_credentials_e2e.py'

`--network none` is the guard: even a test that went wrong cannot send anything to GitHub.
In the open-terminal image `git` on PATH IS the git-proxy, so the worker pushes here are policed
exactly as in production; `/usr/bin/git.real` is the operator path the daemon uses.
"""

from __future__ import annotations

import base64
import os
import shutil
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from littlecoder.openterminal import ExecResult
from littlecoder.urlnorm import normalize_repo_url
from littlecoder.workspace import WorkspaceManager

pytestmark = pytest.mark.skipif(
    os.environ.get("LC_GIT_E2E") != "1" or not sys.platform.startswith("linux"),
    reason="opt-in end-to-end git test: LC_GIT_E2E=1 in a disposable Linux container",
)

# Dummy tokens in the REAL shapes (fine-grained PAT, App installation token), assembled at run time
# so the repository's secret guard never sees a token-shaped literal. None of them is a credential.
DUMMY = "github" + "_pat_" + "DUMMY" + "a" * 20 + "_" + "0" * 59
DUMMY2 = "ghs" + "_" + "DUMMYrotated" + "b" * 30
UPSTREAM_TOK = "github" + "_pat_" + "DUMMYupstream" + "c" * 10 + "_" + "1" * 59
TOKEN_MARKERS = (DUMMY, DUMMY2, UPSTREAM_TOK, "x-access-token:")
REAL_GIT = "/usr/bin/git.real" if os.path.exists("/usr/bin/git.real") else (shutil.which("git") or "git")


# --- an authenticating smart-HTTP git server (git http-backend behind Basic auth) -------------

class _State:
    root = ""
    # repo-owner prefix -> accepted password; anything else is a 401
    accepted = {"acme/": DUMMY, "parent/": UPSTREAM_TOK}
    public_read = ("acme/lib",)
    unauthorized = 0


def _read_body(handler) -> bytes:
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        chunks = []
        while True:
            size = int(handler.rfile.readline().strip() or b"0", 16)
            if size == 0:
                handler.rfile.readline()
                break
            chunks.append(handler.rfile.read(size))
            handler.rfile.readline()
        return b"".join(chunks)
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


class _GitHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _serve(self):
        path, _, query = self.path.partition("?")
        body = _read_body(self)
        repo_path = path.lstrip("/")
        want = next((tok for pre, tok in _State.accepted.items() if repo_path.startswith(pre)), None)
        expect = "Basic " + base64.b64encode(f"x-access-token:{want}".encode()).decode()
        # acme/lib plays a PUBLIC fork: anonymous read (as the submodule init at clone time gets from
        # GitHub), authenticated push. Everything else needs its token for every request.
        is_push = "receive-pack" in self.path
        anon_ok = repo_path.startswith(_State.public_read) and not is_push
        if not anon_ok and (want is None or self.headers.get("Authorization") != expect):
            _State.unauthorized += 1
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_PROJECT_ROOT": _State.root, "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "x-access-token",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
            "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
        }
        out = subprocess.run([REAL_GIT, "http-backend"], input=body, env=env,
                             capture_output=True, timeout=120).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        if not _:
            head, _, payload = out.partition(b"\n\n")
        status, headers = 200, []
        for line in head.decode("latin-1").splitlines():
            k, _, v = line.partition(":")
            if k.lower() == "status":
                status = int(v.strip().split()[0])
            elif k:
                headers.append((k, v.strip()))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _serve
    do_POST = _serve


def _git(*args, cwd=None, env=None):
    r = subprocess.run([REAL_GIT, *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def _bare_with_commit(root: Path, name: str, files: dict[str, str], env, gitlinks=()) -> str:
    bare = root / f"{name}.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), env=env)
    _git("config", "http.receivepack", "true", cwd=bare, env=env)
    work = root / "_seed" / name
    _git("init", "-q", "-b", "main", str(work), env=env)
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    _git("add", "-A", cwd=work, env=env)
    for sha, p in gitlinks:
        _git("update-index", "--add", "--cacheinfo", f"160000,{sha},{p}", cwd=work, env=env)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed", cwd=work, env=env)
    _git("push", "-q", str(bare), "main", cwd=work, env=env)
    return _git("rev-parse", "HEAD", cwd=work, env=env)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    base = tmp_path_factory.mktemp("gitsrv")
    repos = base / "repos"
    (repos / "acme").mkdir(parents=True)
    (repos / "parent").mkdir()
    env = {**os.environ, "HOME": str(base)}
    lib_sha = _bare_with_commit(repos, "acme/lib", {"lib.txt": "lib\n"}, env)
    _bare_with_commit(repos, "acme/widget",
                      {"README": "widget\n",
                       ".gitmodules": '[submodule "vendor/lib"]\n\tpath = vendor/lib\n'
                                      "\turl = https://github.com/acme/lib\n"},
                      env, gitlinks=[(lib_sha, "vendor/lib")])
    _bare_with_commit(repos, "parent/widget", {"README": "parent\n"}, env)
    # github.com answers from loopback ONLY because the container maps it (--add-host); the cert is
    # self-signed for that name and trusted through GIT_SSL_CAINFO, so TLS verification stays on.
    cert, key = base / "cert.pem", base / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=github.com", "-addext", "subjectAltName=DNS:github.com",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    _State.root = str(repos)
    srv = ThreadingHTTPServer(("127.0.0.1", 443), _GitHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield SimpleServer(repos=repos, cert=cert)
    srv.shutdown()


class SimpleServer:
    def __init__(self, repos, cert):
        self.repos, self.cert = repos, cert

    def has_branch(self, repo: str, branch: str) -> bool:
        r = subprocess.run([REAL_GIT, "--git-dir", str(self.repos / f"{repo}.git"),
                            "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                           capture_output=True)
        return r.returncode == 0


class LocalOT:
    """Stands in for open-terminal's POST /execute: bash -c <command>, with the per-exec `env`
    merged over the process environment - exactly what open-terminal does (main.py:
    `subprocess_env = {**os.environ, **request.env}`)."""

    def __init__(self, base_env):
        self.base_env = base_env

    def execute(self, command, cwd=None, env=None, timeout=None):
        r = subprocess.run(["bash", "-c", command], cwd=cwd or "/",
                           env={**self.base_env, **(env or {})},
                           capture_output=True, text=True, timeout=timeout or 300)
        return ExecResult(command, r.returncode, r.stdout, r.stderr, "done", "local")


@pytest.fixture()
def rig(server, tmp_path):
    home = tmp_path / "home"          # the executor's container-local HOME
    ws = tmp_path / "workspace"       # the workspace VOLUME
    home.mkdir()
    ws.mkdir()
    env = {**os.environ, "HOME": str(home), "GIT_SSL_CAINFO": str(server.cert),
           "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"}
    env.pop("LC_GIT_TOKEN", None)
    _State.accepted = {"acme/": DUMMY, "parent/": UPSTREAM_TOK}
    ot = LocalOT(env)
    wm = WorkspaceManager(ot, workspace_path=str(ws), real_git=REAL_GIT, clone_timeout=300)
    return wm, ot, ws, home, server


def _git_configs(ws: Path) -> list[Path]:
    return [p for p in ws.rglob("config") if ".git" in p.parts and (p.parent / "HEAD").exists()]


def _tokens_in_git_configs(ws: Path) -> list[str]:
    """Which workspace git configs carry a token (names only - never the value)."""
    hits = []
    for p in _git_configs(ws):
        text = p.read_text(errors="replace")
        if any(m in text for m in TOKEN_MARKERS):
            hits.append(str(p.relative_to(ws)))
    return hits


def _worker(ot, cwd, cmd):
    """A worker git command: plain `git` on PATH (the git-proxy in the open-terminal image), no
    token in its env - it must authenticate from the credential store alone."""
    return ot.execute(cmd, cwd=str(cwd))


WIDGET = normalize_repo_url("https://github.com/acme/widget")


def test_clone_leaves_no_token_in_git_config_and_worker_push_works(rig):
    wm, ot, ws, home, server = rig
    res = wm.clone(WIDGET, deploy_token=DUMMY)
    assert res.ok, res.stderr[-400:]
    assert _git_configs(ws), "the clone produced no git config - nothing was checked"
    # THE ACCEPTANCE: no token in any git config in the workspace (base 0fb1c0c fails HERE)
    assert _tokens_in_git_configs(ws) == []
    assert (ws / "vendor" / "lib" / "lib.txt").exists()          # the submodule was populated
    assert DUMMY not in res.command                              # nor in the exec's command string
    # the credential lives in the executor's HOME, owner-only
    cred = home / ".lc-git-credentials"
    assert DUMMY in cred.read_text()
    assert cred.stat().st_mode & 0o077 == 0
    # the worker pushes the host repo AND the submodule with no token in its env
    r = _worker(ot, ws, "git checkout -q -b agent/t && echo x > f && git add f && "
                        "git commit -q -m t && git push -q origin agent/t")
    assert r.ok, r.stderr[-400:]
    assert server.has_branch("acme/widget", "agent/t")
    r = _worker(ot, ws / "vendor" / "lib", "git checkout -q -b agent/s && echo y > g && "
                "git add g && git commit -q -m s && git push -q origin agent/s")
    assert r.ok, r.stderr[-400:]
    assert server.has_branch("acme/lib", "agent/s")
    assert _tokens_in_git_configs(ws) == []


def test_the_server_refuses_an_unauthenticated_clone(rig):
    wm, ot, ws, home, server = rig
    before = _State.unauthorized
    res = wm.clone(WIDGET)                                       # no token → no credential
    assert not res.ok
    assert _State.unauthorized > before                          # refused by auth, not by chance


def test_refresh_rotates_the_token_and_keeps_config_clean(rig):
    wm, ot, ws, home, server = rig
    assert wm.clone(WIDGET, deploy_token=DUMMY).ok
    _State.accepted = {"acme/": DUMMY2, "parent/": UPSTREAM_TOK}    # the 1h App token expired
    r = _worker(ot, ws, "git checkout -q -b agent/r1 && git push -q origin agent/r1")
    assert not r.ok                                              # old credential now refused
    assert wm.refresh_origin_auth(WIDGET, DUMMY2).ok
    r = _worker(ot, ws, "git push -q origin agent/r1")
    assert r.ok, r.stderr[-400:]
    cred = (home / ".lc-git-credentials").read_text()
    assert DUMMY2 in cred and DUMMY not in cred                  # replaced, not appended
    assert _tokens_in_git_configs(ws) == []


def test_private_upstream_token_is_kept_apart_from_origin(rig):
    wm, ot, ws, home, server = rig
    assert wm.clone(WIDGET, deploy_token=DUMMY).ok
    up = wm.add_upstream_remote("https://github.com/parent/widget", token=UPSTREAM_TOK)
    assert up.ok, up.stderr[-400:]
    r = _worker(ot, ws, "git fetch -q upstream")
    assert r.ok, r.stderr[-400:]
    r = _worker(ot, ws, "git checkout -q -b agent/u && git push -q origin agent/u")
    assert r.ok, r.stderr[-400:]                                 # origin still has ITS token
    assert _tokens_in_git_configs(ws) == []


def test_private_submodule_add_keeps_gitmodules_clean(rig):
    wm, ot, ws, home, server = rig
    assert wm.clone(WIDGET, deploy_token=DUMMY).ok
    res = wm.add_submodule("https://github.com/acme/lib", "libs/lib2", token=DUMMY)
    assert res.ok, res.stderr[-400:]
    assert all(m not in (ws / ".gitmodules").read_text() for m in TOKEN_MARKERS)
    pushed = _git("--git-dir", str(server.repos / "acme/widget.git"), "show", "main:.gitmodules")
    assert "libs/lib2" in pushed and all(m not in pushed for m in TOKEN_MARKERS)
    assert _tokens_in_git_configs(ws) == []


def _make_legacy(ws: Path, ot):
    """Put the workspace in the pre-2026-09-28 state: tokens spliced into origin and submodule URLs."""
    for cwd in (ws, ws / "vendor" / "lib"):
        u = _git("config", "--get", "remote.origin.url", cwd=cwd, env=ot.base_env)
        _git("remote", "set-url", "origin", u.replace("https://", f"https://x-access-token:{DUMMY}@", 1),
             cwd=cwd, env=ot.base_env)
    assert len(_tokens_in_git_configs(ws)) == 2


def test_refresh_cleans_a_legacy_clone(rig):
    wm, ot, ws, home, server = rig
    assert wm.clone(WIDGET, deploy_token=DUMMY).ok
    _make_legacy(ws, ot)
    assert wm.refresh_origin_auth(WIDGET, DUMMY).ok
    assert _tokens_in_git_configs(ws) == []
    r = _worker(ot, ws, "git checkout -q -b agent/l && git push -q origin agent/l")
    assert r.ok, r.stderr[-400:]


def test_scrub_on_a_copy_of_a_legacy_workspace(rig, tmp_path):
    wm, ot, ws, home, server = rig
    assert wm.clone(WIDGET, deploy_token=DUMMY).ok
    _make_legacy(ws, ot)
    copy = tmp_path / "copy"
    shutil.copytree(ws, copy, symlinks=True)
    script = Path(__file__).resolve().parents[1] / "src" / "littlecoder" / "credscrub.py"

    def run():   # exactly the landing invocation: the file on stdin, stdlib only
        with open(script, "rb") as fh:
            return subprocess.run([sys.executable, "-", str(copy)], stdin=fh,
                                  capture_output=True, text=True)

    first = run()
    assert first.returncode == 0
    assert all(m not in first.stdout + first.stderr for m in TOKEN_MARKERS)
    assert "removed credentials from 1 URL(s)" in first.stdout
    assert _tokens_in_git_configs(copy) == []
    assert len(_tokens_in_git_configs(ws)) == 2                  # the original was not touched
    second = run()
    assert second.returncode == 0 and "0 credential URL(s) removed" in second.stdout
    assert "removed credentials from" not in second.stdout
    # the scrubbed copy is still a working repo once the credential is re-stored
    wm2 = WorkspaceManager(ot, workspace_path=str(copy), real_git=REAL_GIT)
    assert wm2.refresh_origin_auth(WIDGET, DUMMY).ok
    r = _worker(ot, copy, "git checkout -q -b agent/c && git push -q origin agent/c")
    assert r.ok, r.stderr[-400:]


def test_if_missing_keeps_an_existing_credential_and_fills_an_empty_store(rig):
    """N5, the real shell: a SEEDED focus re-stores with if_missing=True. An existing entry (the
    caller's token) must survive; after an executor recreate (empty HOME) the store is filled."""
    wm, ot, ws, home, server = rig
    assert wm.clone(WIDGET, deploy_token=DUMMY).ok
    assert wm.refresh_origin_auth(WIDGET, DUMMY2, if_missing=True).ok
    cred = (home / ".lc-git-credentials").read_text()
    assert DUMMY in cred and DUMMY2 not in cred                  # kept, not overwritten
    r = _worker(ot, ws, "git checkout -q -b agent/m1 && git push -q origin agent/m1")
    assert r.ok, r.stderr[-400:]
    for f in (home / ".lc-git-credentials", home / ".gitconfig"):  # executor recreated
        f.unlink()
    assert wm.refresh_origin_auth(WIDGET, DUMMY, if_missing=True).ok
    r = _worker(ot, ws, "git push -q origin agent/m1:refs/heads/agent/m2")
    assert r.ok, r.stderr[-400:]
    assert _tokens_in_git_configs(ws) == []
