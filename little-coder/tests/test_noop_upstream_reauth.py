"""A NOOP re-focus re-stores a fork's PRIVATE upstream credential (ef-lc-upstream, 2026-10-05).

The credential store lives in the executor's HOME (emptied by a recreate); the `upstream` remote lives
in the workspace volume and survives. After the credential scrub (credscrub.py strips tokens from the
git-config URLs) a legacy upstream's in-URL token is gone and nothing is stored for it. Before this item the NOOP branch of
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
    d._focus_upstream, d._focus_upstream_token = None, None
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
    assert "DISABLED-fork-parent-is-fetch-only" in urls         # the fence is intact (NOT proof the remote
    # was left alone: re-adding re-fences too; test_reauth_runs_no_remote_command_... pins that)
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


# --- ef-lc-upstream attempt 2: F1 (store under the remote's own URL) and the pinned probes -------

class RecOT(LocalOT):
    """Records every command, stdout and stderr the executor sees."""

    def __init__(self, inner):
        super().__init__(inner.base_env)
        self.seen = []

    def execute(self, command, cwd=None, env=None, timeout=None):
        r = super().execute(command, cwd=cwd, env=env, timeout=timeout)
        self.seen.append(command + "\n" + r.stdout + "\n" + r.stderr)
        return r


def _entries(rig):
    return [ln for ln in (rig.home / ".lc-git-credentials").read_text().splitlines() if ln.strip()]


def _refocus_url(d, up, **kw):
    return asyncio.run(d.switch_project(ProjectRequest(repo=GITHUB_WIDGET, upstream=up, **kw)))


def test_f1_a_request_url_that_differs_from_the_remote_stores_under_the_remotes_url(rig):
    """F1: the caller names parent/other while the remote is parent/widget. The credential must be
    stored for the URL the remote HAS, so `upstream_reauthed` is true only when fetch really works;
    the remote is not re-pointed and the mismatch is reported."""
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    remote_before = _remote_urls(rig)
    out = _refocus_url(d, rig.base + "/parent/other", token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)
    assert _fetch_upstream(ot, rig).ok                          # base of F1: reported true, fetch failed
    assert out["upstream_reauthed"] is True
    assert out["upstream_mismatch"] is True
    assert _remote_urls(rig) == remote_before                   # the remote was not changed
    assert any(ln.endswith("/parent/widget") for ln in _entries(rig))
    assert not any("/parent/other" in ln for ln in _entries(rig))
    blob = "\n".join(d.audit.lines) + json.dumps(out)
    assert not any(t in blob for t in TOKENS)


def test_f1_a_dot_git_variant_is_the_same_repo_and_not_a_mismatch(rig):
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    out = _refocus_url(d, rig.upstream + ".git", token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)
    assert _fetch_upstream(ot, rig).ok
    assert out["upstream_reauthed"] is True and "upstream_mismatch" not in out


def test_f1_a_legacy_remote_url_with_userinfo_never_reaches_the_store_or_output(rig):
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    legacy = rig.upstream.replace("http://", f"http://x-access-token:{UPSTREAM_TOK}@", 1)
    _git("remote", "set-url", "upstream", legacy, cwd=rig.ws, env=rig.env)
    out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)
    # f2-runtime F4: git sends the URL's own userinfo, so the stored entry is not vouched for
    assert out["upstream_reauthed"] is False and out["upstream_url_has_userinfo"] is True
    mine =[ln for ln in _entries(rig) if ln.endswith("/parent/widget")]
    assert len(mine) == 1 and mine[0].count("@") == 1           # one clean entry, no doubled userinfo
    assert not any(t in "\n".join(d.audit.lines) + json.dumps(out) for t in TOKENS)


def test_reauth_with_no_remote_is_not_ok(rig):
    """ok means stored for a real remote's URL; with no remote the NOOP bakes (A8), and the helper
    itself reports failure rather than storing under the request's URL."""
    d, wm, ot = _focused_fork(rig)
    _git("remote", "remove", "upstream", cwd=rig.ws, env=rig.env)
    _recreate(rig)
    assert wm.refresh_upstream_auth(UPSTREAM_TOK, rig.upstream).ok is False
    assert not (rig.home / ".lc-git-credentials").exists() or not _entries(rig)


def test_the_helper_is_a_noop_without_a_token(rig):
    d, wm, ot = _focused_fork(rig)
    before = _entries(rig)
    assert wm.refresh_upstream_auth(None, rig.upstream).ok
    assert wm.refresh_upstream_auth("", rig.upstream).ok
    assert _entries(rig) == before


def test_noop_with_upstream_token_but_no_origin_token_still_configures_the_helper(rig, monkeypatch):
    """M9: the helper config must come from the upstream re-store itself, not only origin's."""
    d, wm, ot = _focused_fork(rig)
    monkeypatch.delenv("LC_DEPLOY_TOKEN", raising=False)
    _recreate(rig)
    out = _refocus(d, rig, upstream_token=UPSTREAM_TOK)
    assert "origin_reauthed" not in out
    assert out["upstream_reauthed"] is True
    assert _fetch_upstream(ot, rig).ok
    assert len(_entries(rig)) == 1


def test_upstream_token_without_upstream_is_ignored(rig):
    """F2 (out of scope, pinned as current behaviour): no `upstream` in the request -> no reauth, and
    nothing is invented. The bridge only sends upstream_token together with upstream."""
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    out = asyncio.run(d.switch_project(ProjectRequest(repo=GITHUB_WIDGET, token=ORIGIN_TOK,
                                                      upstream_token=UPSTREAM_TOK)))
    assert "upstream_reauthed" not in out and "upstream" not in out
    assert not any("project_upstream_reauthed" in ln for ln in d.audit.lines)


def test_a_failed_store_is_reported_false_in_response_and_journal(rig):
    """M4/M7: the flag and the journal's ok must come from the real result."""
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    (rig.home / ".lc-git-credentials").mkdir()                  # credential-store cannot write here
    out = _refocus(d, rig, upstream_token=UPSTREAM_TOK)
    assert out["upstream_reauthed"] is False and "upstream_mismatch" not in out
    assert any('"ok": false' in ln and "project_upstream_reauthed" in ln for ln in d.audit.lines)


def test_reauth_runs_no_remote_command_and_reports_the_upstream(rig, monkeypatch):
    """M5/M12: the response names the upstream; the NOOP never adds or re-points the UPSTREAM remote.
    C1 (f2-runtime): independent of an ambient LC_DEPLOY_TOKEN (set in the little-coder and ao-worker
    containers), whose legitimate `remote set-url origin` must not read as a changed upstream."""
    monkeypatch.delenv("LC_DEPLOY_TOKEN", raising=False)
    d, wm, ot = _focused_fork(rig)
    rec = RecOT(ot)
    wm.ot = rec
    _recreate(rig)
    out = _refocus(d, rig, upstream_token=UPSTREAM_TOK)
    assert out["upstream"] == rig.upstream
    cmds = [c.split("\n", 1)[0] for c in rec.seen]
    upstream_changes = ("remote add upstream", "remote set-url upstream",
                        "remote set-url --push upstream")
    assert cmds and not any(u in c for c in cmds for u in upstream_changes), "upstream was changed"


def test_noop_with_a_missing_remote_still_bakes_only(rig):
    """M13: the missing-remote path is unchanged (bake), and no reauth is reported on top of it."""
    d, wm, ot = _focused_fork(rig)
    _git("remote", "remove", "upstream", cwd=rig.ws, env=rig.env)
    _recreate(rig)
    out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)
    assert out["upstream_ok"] is True and "upstream_reauthed" not in out
    assert _fetch_upstream(ot, rig).ok
    assert not any("project_upstream_reauthed" in ln for ln in d.audit.lines)


# --- f2-runtime F4: a legacy upstream URL that still carries userinfo --------------------------

@pytest.mark.parametrize("userinfo", ["other-username", "stale-token"])
def test_f4_a_userinfo_remote_whose_fetch_fails_is_not_reported_reauthed(rig, userinfo):
    """ef-lc-upstream attempt-2 P6b/P6c: the remote URL carries another username, or a STALE in-URL
    token. git sends that userinfo instead of the stored entry, so `git fetch upstream` still fails
    after the re-store. The NOOP must say so: upstream_reauthed false + upstream_url_has_userinfo,
    in the response and the journal (base: upstream_reauthed true while the fetch fails)."""
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    stale = "ghs" + "_" + "DUMMYstale" + "e" * 30
    prefix = "someuser@" if userinfo == "other-username" else f"x-access-token:{stale}@"
    _git("remote", "set-url", "upstream", rig.upstream.replace("http://", "http://" + prefix, 1),
         cwd=rig.ws, env=rig.env)

    out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)

    assert not _fetch_upstream(ot, rig).ok                     # the fetch really still fails
    assert out["upstream_reauthed"] is False                   # base: True
    assert out["upstream_url_has_userinfo"] is True
    j = json.loads(next(ln for ln in d.audit.lines if "project_upstream_reauthed" in ln))
    assert j["ok"] is False and j["url_has_userinfo"] is True and j["stored"] is True
    blob = "\n".join(d.audit.lines) + json.dumps(out)
    assert not any(t in blob for t in (*TOKENS, stale)) and "someuser" not in blob


def test_f4_a_clean_remote_is_still_reported_reauthed_without_the_flag(rig):
    """The flag is set only for a userinfo URL: a clean remote's NOOP reports true and fetch works."""
    d, wm, ot = _focused_fork(rig)
    _recreate(rig)
    out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)
    assert _fetch_upstream(ot, rig).ok
    assert out["upstream_reauthed"] is True and "upstream_url_has_userinfo" not in out
    j = json.loads(next(ln for ln in d.audit.lines if "project_upstream_reauthed" in ln))
    assert j["ok"] is True and j["url_has_userinfo"] is False


# --- f2-runtime: the pre-task re-store covers upstream (executor recreate after /project) ------

def test_an_executor_recreate_between_project_and_task_leaves_upstream_authenticated(
        rig, monkeypatch):
    """/project (NOOP with upstream + upstream_token), then the executor is recreated (HOME empties)
    before the task: the pre-task re-store must bring back upstream's OWN entry next to origin's
    (base: only origin is re-stored and `git fetch upstream` fails)."""
    monkeypatch.delenv("LC_DEPLOY_TOKEN", raising=False)
    d, wm, ot = _focused_fork(rig)
    rec = RecOT(ot)
    wm.ot = rec
    out = _refocus(d, rig, token=ORIGIN_TOK, upstream_token=UPSTREAM_TOK)
    assert out["upstream_reauthed"] is True
    _recreate(rig)
    assert not _fetch_upstream(ot, rig).ok

    d._ensure_git_credentials()                                 # what _run_task does first

    r = _fetch_upstream(ot, rig)
    assert r.ok, r.stderr[-300:]
    assert ot.execute("git checkout -q -b agent/r && git push -q origin agent/r",
                      cwd=str(rig.ws)).ok                       # origin too, with its own token
    cred = (rig.home / ".lc-git-credentials").read_text()
    assert ORIGIN_TOK in cred and UPSTREAM_TOK in cred
    assert len(_entries(rig)) == 2                              # separate entries
    urls = _remote_urls(rig)
    assert not any(t in urls for t in TOKENS) and "x-access-token" not in urls
    assert not any(t in "\n".join(rec.seen) for t in TOKENS)    # never in a command or its output
    assert not any(t in "\n".join(d.audit.lines) for t in TOKENS)
