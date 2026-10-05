"""A NOOP re-focus re-stores a fork's PRIVATE upstream credential (ef-lc-upstream, 2026-10-05).

The credential store lives in the executor's HOME (emptied by a recreate or the credential scrub);
the `upstream` remote lives in the workspace volume and survives. Before this item the NOOP branch of
`switch_project` re-stored only origin's credential and baked upstream only when the remote was
MISSING, so a present upstream stayed unauthenticated: `git fetch upstream` failed until the next
clone/switch (found in the cf-lc-token review, R1).

Real git, real WorkspaceManager, real `switch_project`. Origin and the private upstream are local bare
repositories behind a loopback smart-HTTP server that demands Basic auth (ephemeral port, plain
HTTP, nothing leaves the machine); the credential store is a temp HOME. Needs git (+ http-backend)
and bash on PATH; runs on Windows (Git for Windows) and Linux. Tokens are dummies assembled from parts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from littlecoder.daemon import LittleCoderDaemon, ProjectRequest
from littlecoder.openterminal import ExecResult
from littlecoder.urlnorm import normalize_repo_url
from littlecoder.workspace import WorkspaceManager

GIT = shutil.which("git")
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not (GIT and BASH), reason="needs git and bash on PATH")

ORIGIN_TOK = "github" + "_pat_" + "DUMMYorigin" + "a" * 12 + "_" + "0" * 40
UPSTREAM_TOK = "github" + "_pat_" + "DUMMYupstream" + "c" * 10 + "_" + "1" * 40
TOKENS = (ORIGIN_TOK, UPSTREAM_TOK)
ACCEPTED = {"acme/": ORIGIN_TOK, "parent/": UPSTREAM_TOK}
GITHUB_WIDGET = "https://github.com/acme/widget"   # what the caller focuses (normalizes cleanly)


def _git(*args, cwd=None, env=None):
    r = subprocess.run([GIT, *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    root = ""

    def log_message(self, *a):
        pass

    def _serve(self):
        path, _, query = self.path.partition("?")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        repo = path.lstrip("/")
        want = next((t for pre, t in ACCEPTED.items() if repo.startswith(pre)), None)
        expect = "Basic " + base64.b64encode(f"x-access-token:{want}".encode()).decode()
        if want is None or self.headers.get("Authorization") != expect:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="t"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        env = {**os.environ, "GIT_PROJECT_ROOT": self.root, "GIT_HTTP_EXPORT_ALL": "1",
               "PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": self.command,
               "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "x-access-token",
               "REMOTE_ADDR": "127.0.0.1", "GIT_PROTOCOL": self.headers.get("Git-Protocol", "")}
        out = subprocess.run([GIT, "http-backend"], input=body, env=env,
                             capture_output=True, timeout=120).stdout
        head, sep, payload = out.partition(b"\r\n\r\n")
        if not sep:
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


@pytest.fixture()
def rig(tmp_path):
    home, ws, repos = tmp_path / "home", tmp_path / "workspace", tmp_path / "repos"
    for d in (home, repos / "acme", repos / "parent"):
        d.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    env.pop("LC_GIT_TOKEN", None)
    env.pop("LC_DEPLOY_TOKEN", None)
    for name in ("acme/widget", "parent/widget"):
        bare = repos / f"{name}.git"
        _git("init", "-q", "--bare", "-b", "main", str(bare), env=env)
        work = tmp_path / "_seed" / name.replace("/", "_")
        _git("init", "-q", "-b", "main", str(work), env=env)
        (work / "README").write_text(name)
        _git("add", "-A", cwd=work, env=env)
        _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed",
             cwd=work, env=env)
        _git("push", "-q", str(bare), "main", cwd=work, env=env)
        _git("config", "http.receivepack", "true", cwd=bare, env=env)
    _Handler.root = str(repos)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield SimpleNamespace(env=env, home=home, ws=ws, base=base,
                          origin=f"{base}/acme/widget", upstream=f"{base}/parent/widget")
    srv.shutdown()


class LocalOT:
    """open-terminal's POST /execute: bash -c <command>, per-exec env merged over the base env."""

    def __init__(self, base_env):
        self.base_env = base_env

    def execute(self, command, cwd=None, env=None, timeout=None):
        r = subprocess.run([BASH, "-c", command], cwd=cwd or None,
                           env={**self.base_env, **(env or {})},
                           capture_output=True, text=True, timeout=timeout or 120)
        return ExecResult(command, r.returncode, r.stdout, r.stderr, "done", "local")


class _Wm(WorkspaceManager):
    """The real manager, except origin is the loopback server: `refresh_origin_auth` is the real
    code run against the server's URL (the caller focuses a github.com URL, which cannot be served)."""

    served_origin = ""

    def refresh_origin_auth(self, repo, deploy_token, *, if_missing=False):
        return super().refresh_origin_auth(
            SimpleNamespace(canonical_url=self.served_origin), deploy_token, if_missing=if_missing)


class _Audit:
    def __init__(self):
        self.lines = []

    def write(self, event, **kw):
        self.lines.append(json.dumps({"event": event, **kw}, default=str))


def _focused_fork(rig):
    """A focused fork as a clone leaves it: workspace cloned from origin with origin's credential,
    `upstream` baked (token-free URL, fetch-only push) with ITS credential, daemon focus set."""
    ot = LocalOT(rig.env)
    wm = _Wm(ot, workspace_path=str(rig.ws), real_git=GIT, clone_timeout=120)
    wm.served_origin = rig.origin
    d = object.__new__(LittleCoderDaemon)
    d.workspace, d.audit = wm, _Audit()
    d.current_focus = normalize_repo_url(GITHUB_WIDGET)
    d._focus_token, d._focus_from_project = None, False
    d.in_flight, d.queue = None, asyncio.Queue()
    d.tasks, d.contexts = {}, {}
    # origin's credential, stored the way the daemon's own helpers store it
    seed = ot.execute(
        "git config --global credential.helper \"store --file=$HOME/.lc-git-credentials\" && "
        "git config --global credential.useHttpPath true && "
        f"printf 'url=%s\\nusername=x-access-token\\npassword=%s\\n\\n' {rig.origin} \"$LC_GIT_TOKEN\""
        " | git credential-store --file=\"$HOME/.lc-git-credentials\" store",
        env={"LC_GIT_TOKEN": ORIGIN_TOK})
    assert seed.ok, seed.stderr
    c = ot.execute(f"git clone -q {rig.origin} \"{rig.ws.as_posix()}\"")
    assert c.ok, c.stderr[-300:]
    assert wm.add_upstream_remote(rig.upstream, token=UPSTREAM_TOK).ok
    assert _fetch_upstream(ot, rig).ok          # sanity: the focused fork can fetch its parent
    return d, wm, ot


def _recreate(rig):
    """The executor is recreated / the credential scrubbed: HOME empties, the workspace survives."""
    for f in (rig.home / ".lc-git-credentials", rig.home / ".gitconfig"):
        if f.exists():
            f.unlink()


def _refocus(d, rig, **kw):
    req = ProjectRequest(repo=GITHUB_WIDGET, upstream=rig.upstream, **kw)
    return asyncio.run(d.switch_project(req))


def _fetch_upstream(ot, rig):
    return ot.execute("git fetch -q upstream", cwd=str(rig.ws))


def _remote_urls(rig):
    return _git("config", "--local", "--get-regexp", r"^remote\..*url", cwd=rig.ws, env=rig.env)


def test_noop_refocus_with_upstream_token_restores_a_scrubbed_upstream_credential(rig):
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    assert not _fetch_upstream(ot, rig).ok                      # precondition: fetch really fails now

    out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)

    r = _fetch_upstream(ot, rig)
    assert r.ok, r.stderr[-300:]                                # THE ACCEPTANCE (base: still fails)
    assert out["action"] == "noop"
    assert out["origin_reauthed"] is True
    assert out["upstream_reauthed"] is True                     # reported like origin_reauthed
    # origin kept its OWN token; upstream has its own, separate entry
    cred = (rig.home / ".lc-git-credentials").read_text()
    assert ORIGIN_TOK in cred and UPSTREAM_TOK in cred
    assert len([ln for ln in cred.splitlines() if ln.strip()]) == 2
    assert ot.execute("git checkout -q -b agent/u && git push -q origin agent/u",
                      cwd=str(rig.ws)).ok                       # origin still authenticates
    # no token anywhere it must not be: remote URLs, the audit journal, the response
    urls = _remote_urls(rig)
    assert UPSTREAM_TOK not in urls and ORIGIN_TOK not in urls and "x-access-token" not in urls
    assert "DISABLED-fork-parent-is-fetch-only" in urls         # the remote was not re-added/unfenced
    blob = "\n".join(d.audit.lines) + json.dumps(out)
    assert not any(t in blob for t in TOKENS)
    assert any("project_upstream_reauthed" in ln for ln in d.audit.lines)
    assert not any(t in (rig.ws / ".git" / "config").read_text() for t in TOKENS)


def test_noop_refocus_without_upstream_token_leaves_an_existing_upstream_credential(rig):
    d, wm, ot = _focused_fork(rig)
    assert UPSTREAM_TOK in (rig.home / ".lc-git-credentials").read_text()

    out = _refocus(d, rig, token=ORIGIN_TOK)                    # the caller sent no upstream_token

    assert out["action"] == "noop" and "upstream_reauthed" not in out
    after = (rig.home / ".lc-git-credentials").read_text()
    assert UPSTREAM_TOK in after and ORIGIN_TOK in after        # nothing erased
    assert _fetch_upstream(ot, rig).ok


def test_noop_with_a_rotated_upstream_token_replaces_only_the_upstream_entry(rig):
    d, wm, ot = _focused_fork(rig)
    rotated = "ghs" + "_" + "DUMMYrotated" + "d" * 30
    ACCEPTED["parent/"] = rotated
    try:
        assert not _fetch_upstream(ot, rig).ok                  # the old upstream token expired
        out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=rotated)
        assert _fetch_upstream(ot, rig).ok
        assert out["upstream_reauthed"] is True
        cred = (rig.home / ".lc-git-credentials").read_text()
        assert rotated in cred and UPSTREAM_TOK not in cred and ORIGIN_TOK in cred
        assert rotated not in _remote_urls(rig)
    finally:
        ACCEPTED["parent/"] = UPSTREAM_TOK
