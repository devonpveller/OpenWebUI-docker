"""ao-dauth - daemon_auth internals: the route table, the ASGI middleware, the env hand-off and
the bind scope (LC_DAEMON_HIDE_FROM). Behaviour over the real app is in test_daemon_auth.py."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from littlecoder import daemon_auth as da

_TOKEN = "-".join(["lcd", "unit", "token", "77"])


def _real_app(tmp_path):
    from test_daemon_auth import _daemon  # the same real-daemon fixture

    from littlecoder.daemon import build_app
    return build_app(_daemon(tmp_path), token=_TOKEN)


def test_table_covers_the_real_app_exactly(tmp_path):
    da.verify_table(_real_app(tmp_path))   # build_app already did; be explicit


def test_unclassified_route_fails_startup():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def _h():
        return {}

    @app.post("/admin/new-thing")
    def _n():
        return {}

    table = {("GET", "/health"): da.PUBLIC}
    with pytest.raises(RuntimeError, match="unclassified"):
        da.verify_table(app, table)


def test_stale_row_and_bad_class_fail_startup():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def _h():
        return {}

    with pytest.raises(RuntimeError, match="stale"):
        da.verify_table(app, {("GET", "/health"): da.PUBLIC, ("POST", "/gone"): da.TOKEN})
    with pytest.raises(RuntimeError, match="bad_class"):
        da.verify_table(app, {("GET", "/health"): "open"})


def test_docs_and_openapi_are_not_served(tmp_path):
    from fastapi.testclient import TestClient
    c = TestClient(_real_app(tmp_path))
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(p, headers={"Authorization": f"Bearer {_TOKEN}"}).status_code == 404


def test_build_app_refuses_an_unclassified_route(tmp_path, monkeypatch):
    monkeypatch.delitem(da.ROUTE_ACCESS, ("POST", "/admin/shutdown"))
    with pytest.raises(RuntimeError, match="/admin/shutdown"):
        _real_app(tmp_path)


@pytest.mark.parametrize("access,presented,token,expect", [
    (da.PUBLIC, None, None, None),
    (da.PUBLIC, None, b"t", None),
    (da.TOKEN, b"t", b"t", None),
    (da.TOKEN, None, b"t", 401),
    (da.TOKEN, b"x", b"t", 401),
    (da.TOKEN, b"t", None, 503),
    (da.TOKEN, None, None, 503),
    (None, b"t", b"t", 403),
    ("operator", b"t", b"t", 403),
])
def test_decide(access, presented, token, expect):
    v = da.decide(access, presented, token)
    assert (v and v[0]) == expect if expect else v is None


def _scope(kind="http", method="GET", path="/tasks", headers=()):
    return {"type": kind, "method": method, "path": path, "headers": list(headers),
            "query_string": b"", "root_path": ""}


def _run_mw(app, scope):
    sent: list[dict] = []
    reached: list = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        raise AssertionError("the request body was read before the auth decision")

    async def send(msg):
        sent.append(msg)

    mw = da.DaemonAuthMiddleware(inner, router=app.router, token=_TOKEN.encode())
    asyncio.run(mw(scope, receive, send))
    return sent, reached


def test_middleware_refuses_before_reading_the_body(tmp_path):
    sent, reached = _run_mw(_real_app(tmp_path), _scope(method="POST", path="/admin/shutdown"))
    assert reached == []
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 401
    assert (b"x-lc-auth", b"refused") in start["headers"]


def test_middleware_passes_with_token(tmp_path):
    hdr = [(b"authorization", b"Bearer " + _TOKEN.encode())]
    _sent, reached = _run_mw(_real_app(tmp_path), _scope(method="POST", path="/admin/shutdown",
                                                         headers=hdr))
    assert reached == ["http"]


def test_websocket_scope_is_refused(tmp_path):
    hdr = [(b"authorization", b"Bearer " + _TOKEN.encode())]
    sent, reached = _run_mw(_real_app(tmp_path), _scope(kind="websocket", path="/tasks", headers=hdr))
    assert reached == []
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_unknown_scope_type_is_refused(tmp_path):
    sent, reached = _run_mw(_real_app(tmp_path), _scope(kind="webtransport"))
    assert reached == [] and sent == []


def test_lifespan_passes_through(tmp_path):
    _sent, reached = _run_mw(_real_app(tmp_path), {"type": "lifespan"})
    assert reached == ["lifespan"]


def test_take_token_from_env_removes_it():
    env = {"LC_DAEMON_TOKEN": f"  {_TOKEN} ", "OTHER": "1"}
    assert da.take_token_from_env(env) == _TOKEN.encode()
    assert "LC_DAEMON_TOKEN" not in env and env == {"OTHER": "1"}
    assert da.take_token_from_env({"LC_DAEMON_TOKEN": "  "}) is None
    assert da.take_token_from_env({}) is None


def test_startup_line_never_carries_the_token(capsys, tmp_path):
    from fastapi.testclient import TestClient
    app = _real_app(tmp_path)
    c = TestClient(app)
    c.get("/tasks", headers={"Authorization": "Bearer " + _TOKEN + "x"})
    c.get("/tasks", headers={"Authorization": "Bearer " + _TOKEN})
    err = capsys.readouterr().err
    assert "daemon token configured" in err
    assert "refused GET /tasks -> 401" in err
    assert _TOKEN not in err


def test_startup_line_when_unset(capsys, tmp_path):
    from test_daemon_auth import _daemon

    from littlecoder.daemon import build_app
    build_app(_daemon(tmp_path), token=None)
    assert "LC_DAEMON_TOKEN is not set" in capsys.readouterr().err


# --- bind scope -------------------------------------------------------------------------------

_IFACES = [("127.0.0.1", "255.0.0.0"),       # lo
           ("198.51.100.3", "255.255.255.0"),     # llm-net (the bridge's side)
           ("203.0.113.4", "255.255.255.0")]   # ao-worker-net (shared with ao-ot-N)


def test_select_bind_hosts_drops_the_executor_network():
    assert da.select_bind_hosts(_IFACES, ["203.0.113.9"]) == ["127.0.0.1", "198.51.100.3"]


def test_select_bind_hosts_keeps_everything_when_nothing_hidden_matches():
    assert da.select_bind_hosts(_IFACES, ["192.0.2.9"]) == [a for a, _ in _IFACES]


def test_select_bind_hosts_never_drops_loopback():
    assert da.select_bind_hosts(_IFACES, ["127.0.0.5", "198.51.100.9", "203.0.113.9"]) == ["127.0.0.1"]


def test_bind_hosts_unset_keeps_the_configured_host():
    assert da.bind_hosts("0.0.0.0", environ={}) == ["0.0.0.0"]
    assert da.bind_hosts("0.0.0.0", environ={"LC_DAEMON_HIDE_FROM": " , "}) == ["0.0.0.0"]


def test_bind_hosts_unresolvable_fails_closed(monkeypatch):
    monkeypatch.setattr(da, "resolve_ipv4", lambda h, **k: [])
    with pytest.raises(SystemExit, match="does not resolve"):
        da.bind_hosts("0.0.0.0", environ={"LC_DAEMON_HIDE_FROM": "ao-ot-1"})


def test_bind_hosts_loopback_only_fails_closed(monkeypatch):
    monkeypatch.setattr(da, "resolve_ipv4", lambda h, **k: ["198.51.100.9", "203.0.113.9"])
    monkeypatch.setattr(da, "local_ipv4_interfaces", lambda: _IFACES)
    with pytest.raises(SystemExit, match="refusing to start"):
        da.bind_hosts("0.0.0.0", environ={"LC_DAEMON_HIDE_FROM": "a,b"})


def test_bind_hosts_scopes(monkeypatch):
    monkeypatch.setattr(da, "resolve_ipv4", lambda h, **k: ["203.0.113.9"])
    monkeypatch.setattr(da, "local_ipv4_interfaces", lambda: _IFACES)
    assert da.bind_hosts("0.0.0.0", environ={"LC_DAEMON_HIDE_FROM": "ao-ot-1"}) == [
        "127.0.0.1", "198.51.100.3"]


# --- in-container clients (lc CLI, lc-mcp) ------------------------------------------------------

def test_client_headers():
    assert da.client_headers({"LC_DAEMON_TOKEN": f" {_TOKEN} "}) == {
        "Authorization": f"Bearer {_TOKEN}"}
    assert da.client_headers({}) == {}
    assert da.client_headers({"LC_DAEMON_TOKEN": " "}) == {}


def test_cli_sends_the_token(monkeypatch):
    import httpx

    from littlecoder import cli
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"tasks": []})

    real = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("LC_DAEMON_TOKEN", _TOKEN)
    cli._request("GET", "/tasks")
    assert seen == [f"Bearer {_TOKEN}"]


# --- round 2 (X2): the token leaves /proc/<daemon>/environ via a re-exec + pipe hand-off ---------

def test_acquire_reexecs_without_the_token(monkeypatch):
    import os
    monkeypatch.setattr(da.os, "name", "posix")
    seen = {}

    def fake_execve(exe, argv, env):
        seen.update(exe=exe, argv=argv, env=env)
        raise RuntimeError("execve")       # a real execve never returns

    env = {"LC_DAEMON_TOKEN": f" {_TOKEN} ", "PATH": "/usr/bin"}
    with pytest.raises(RuntimeError, match="execve"):
        da.acquire_token(env, execve=fake_execve, executable="/usr/local/bin/python")
    assert seen["argv"] == ["/usr/local/bin/python", "-P", "-m", "littlecoder.daemon"]  # -P: no cwd on sys.path
    assert "LC_DAEMON_TOKEN" not in seen["env"] and seen["env"]["PATH"] == "/usr/bin"
    assert not any(_TOKEN in v for v in seen["env"].values()) and _TOKEN not in " ".join(seen["argv"])
    fd = int(seen["env"]["LC_DAEMON_TOKEN_FD"])
    assert os.get_inheritable(fd)
    # the re-exec'd process: reads the pipe once, closes it, keeps nothing in its env
    child = dict(seen["env"])
    assert da.acquire_token(child) == _TOKEN.encode()
    assert "LC_DAEMON_TOKEN_FD" not in child and "LC_DAEMON_TOKEN" not in child
    with pytest.raises(OSError):
        os.fstat(fd)                       # closed


def test_acquire_without_token_or_opted_out_does_not_reexec(monkeypatch):
    monkeypatch.setattr(da.os, "name", "posix")

    def boom(*a):
        raise AssertionError("must not re-exec")

    assert da.acquire_token({"LC_DAEMON_TOKEN": "  "}, execve=boom) is None
    assert da.acquire_token({}, execve=boom) is None
    env = {"LC_DAEMON_TOKEN": _TOKEN, "LC_DAEMON_TOKEN_REEXEC": "0"}
    assert da.acquire_token(env, execve=boom) == _TOKEN.encode() and "LC_DAEMON_TOKEN" not in env


def test_metrics_server_takes_the_bind_scope(monkeypatch):
    from littlecoder import metrics
    calls = []
    monkeypatch.setattr(metrics, "start_http_server", lambda port, addr="0.0.0.0": calls.append((port, addr)))
    metrics.start_metrics_server(9090, ["127.0.0.1", "198.51.100.3"])
    metrics.start_metrics_server(9090)
    assert calls == [(9090, "127.0.0.1"), (9090, "198.51.100.3"), (9090, "0.0.0.0")]


# --- round 3 (X3): the token lives in a root-only FILE, in no environment ----------------------------

def test_read_secret_file(tmp_path):
    f = tmp_path / "token"
    f.write_text(f"{_TOKEN}\r\n", encoding="utf-8")
    assert da.read_secret_file(str(f)) == _TOKEN.encode()
    assert da.read_secret_file(str(tmp_path / "missing")) is None
    assert da.read_secret_file(str(tmp_path)) is None              # Docker made a directory
    (tmp_path / "blank").write_text("  \n", encoding="utf-8")
    assert da.read_secret_file(str(tmp_path / "blank")) is None
    assert da.read_secret_file(environ={"LC_DAEMON_TOKEN_FILE": str(f)}) == _TOKEN.encode()


def test_client_headers_read_the_file_when_env_is_unset(tmp_path):
    f = tmp_path / "token"
    f.write_text(_TOKEN, encoding="utf-8")
    assert da.client_headers({"LC_DAEMON_TOKEN_FILE": str(f)}) == {"Authorization": f"Bearer {_TOKEN}"}
    assert da.client_headers({"LC_DAEMON_TOKEN_FILE": str(tmp_path / "nope")}) == {}
    assert da.client_headers({"LC_DAEMON_TOKEN_FILE": str(f), "LC_DAEMON_TOKEN": "x"}) == {
        "Authorization": "Bearer x"}


def test_take_root_secret_reads_then_drops(tmp_path, monkeypatch):
    f = tmp_path / "token"
    f.write_text(_TOKEN, encoding="utf-8")
    monkeypatch.setattr(da.os, "name", "posix")
    monkeypatch.setattr(da.os, "geteuid", lambda: 0, raising=False)
    dropped = []
    monkeypatch.setattr(da, "drop_privileges", lambda user: dropped.append(user))
    assert da.take_root_secret({"LC_DAEMON_TOKEN_FILE": str(f)}) == (True, _TOKEN.encode())
    assert da.take_root_secret({"LC_DAEMON_TOKEN_FILE": str(tmp_path / "x"), "LC_DAEMON_RUN_AS": "u"}) == (True, None)
    assert dropped == ["lc", "u"]          # drops even when the file is missing (never stays root)


def test_take_root_secret_not_root(monkeypatch):
    monkeypatch.setattr(da.os, "name", "posix")
    monkeypatch.setattr(da.os, "geteuid", lambda: 10002, raising=False)
    monkeypatch.setattr(da, "drop_privileges", lambda user: (_ for _ in ()).throw(AssertionError))
    assert da.take_root_secret({}) == (False, None)
