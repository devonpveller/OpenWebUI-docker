"""daemon_auth - who may call the control daemon, and from where (ao-dauth, 2026-10-07).

The daemon's HTTP API runs agent tasks (POST /tasks), clears the agent's stop-gates
(POST /tasks/{id}/confirm, journalled as an operator action), approves skills, retargets the
workspace (POST /project) and stops the process (POST /admin/shutdown). Until this module it
answered anything that could open a TCP connection to :8090 - including the open-terminal
sandbox where the agent's own shell commands run (ao-ot-1/2 share ao-worker-net with their
worker). Two layers now:

1. A bearer token, LC_DAEMON_TOKEN, on every route except GET /health:
   - one explicit table, ROUTE_ACCESS; `verify_table` refuses to build the app when a served
     route is missing from it (or a row names no route), and the middleware refuses (403) a
     matched route that is somehow unclassified;
   - a pure ASGI middleware, so the check runs BEFORE the request body is read or validated;
   - constant-time compare; the token is never logged, echoed or returned;
   - fail closed: unset/blank -> every non-public route answers 503 and a startup line says so;
   - websocket (and any other non-http, non-lifespan) scopes are refused outright.
   `take_token_from_env` also REMOVES the variable from the daemon's own environment, so the
   agent, its tools and every subprocess the daemon spawns never inherit it.

2. A bind scope, LC_DAEMON_HIDE_FROM (comma-separated host names): the daemon does not listen on
   any interface whose subnet holds one of those hosts. A worker sets it to its open-terminal
   executor, so the sandbox cannot even connect to :8090 - the worker still reaches the sandbox
   (outbound), and the bridge still reaches the daemon over the other network. Unset = the
   configured host (0.0.0.0), as before. A name that never resolves, or a scope that would leave
   only loopback, stops the daemon (fail closed, the restart policy retries) - it never falls
   back to listening everywhere.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import socket
import sys
import time

from starlette.responses import JSONResponse
from starlette.routing import Match

TOKEN_ENV = "LC_DAEMON_TOKEN"
HIDE_ENV = "LC_DAEMON_HIDE_FROM"

PUBLIC = "public"     # no token: liveness probes (compose healthchecks, stack.py, recovery)
TOKEN = "token"       # Authorization: Bearer <LC_DAEMON_TOKEN>

# THE route table. Every (method, path) the daemon serves, and nothing else. Add a route to
# build_app without a row here and the daemon refuses to start (verify_table).
ROUTE_ACCESS: dict[tuple[str, str], str] = {
    ("GET", "/health"): PUBLIC,
    ("POST", "/tasks"): TOKEN,
    ("GET", "/tasks"): TOKEN,
    ("GET", "/tasks/{task_id}"): TOKEN,
    ("GET", "/tasks/{task_id}/events"): TOKEN,
    ("POST", "/tasks/{task_id}/confirm"): TOKEN,
    ("POST", "/tasks/{task_id}/cancel"): TOKEN,
    ("GET", "/focus"): TOKEN,
    ("POST", "/project"): TOKEN,
    ("POST", "/project/submodule"): TOKEN,
    ("POST", "/check"): TOKEN,
    ("GET", "/admin/observe"): TOKEN,
    ("GET", "/admin/pending"): TOKEN,
    ("POST", "/admin/approve/{artifact_id}"): TOKEN,
    ("POST", "/admin/reject/{artifact_id}"): TOKEN,
    ("POST", "/admin/bootstrap-agents"): TOKEN,
    ("POST", "/admin/upstream/pull"): TOKEN,
    ("POST", "/admin/shutdown"): TOKEN,
}
_CLASSES = {PUBLIC, TOKEN}

# Every refusal carries this header, so a caller (and the tests) can tell an auth refusal from
# an endpoint's own 4xx/5xx.
REFUSAL_HEADER = "x-lc-auth"


def _say(msg: str) -> None:
    # The daemon logs with print (docker logs); never pass a token value here.
    print(f"[little-coder] {msg}", file=sys.stderr, flush=True)


def _clean(raw: str | None) -> bytes | None:
    raw = (raw or "").strip()
    return raw.encode("utf-8") if raw else None


def client_headers(environ=None) -> dict[str, str]:
    """The header a client inside the container (lc CLI, lc-mcp) sends: the token from its own
    environment (docker exec carries the container's configured env), or nothing."""
    env = os.environ if environ is None else environ
    tok = (env.get(TOKEN_ENV) or "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def take_token_from_env(environ=None) -> bytes | None:
    """Read LC_DAEMON_TOKEN and remove it from the environment, so nothing the daemon spawns
    (the agent, ot-exec, git, checks) inherits it. Call once, in main()."""
    env = os.environ if environ is None else environ
    return _clean(env.pop(TOKEN_ENV, None))


def announce(token: bytes | None) -> None:
    """The startup line (never the value)."""
    if token is None:
        _say(f"auth: {TOKEN_ENV} is not set - every daemon route except GET /health refuses "
             f"(503) until it is set (fail closed)")
    else:
        _say("auth: daemon token configured; every route except GET /health requires it")


def verify_table(app, table: dict[tuple[str, str], str] = ROUTE_ACCESS) -> None:
    """Every (method, path) the app serves is classified, and every table row is a live route."""
    served: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", repr(route))
        if methods:
            for method in methods:
                served.add((method, path))
        else:  # mounts / websocket routes / docs: unclassified surface
            served.add(("*", path))
    unclassified = sorted(served - set(table))
    stale = sorted(set(table) - served)
    bad = sorted(k for k, v in table.items() if v not in _CLASSES)
    if unclassified or stale or bad:
        raise RuntimeError(f"little-coder daemon route access table is out of date: "
                           f"unclassified={unclassified} stale={stale} bad_class={bad}")


def _presented(scope) -> bytes | None:
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            scheme, _, cred = value.partition(b" ")
            if scheme.lower() == b"bearer" and cred.strip():
                return cred.strip()
            return None
    return None


def decide(access: str | None, presented: bytes | None, token: bytes | None) -> tuple[int, str] | None:
    """None = let it through; else (status, reason). Pure, for unit tests."""
    if access == PUBLIC:
        return None
    if access != TOKEN:
        return 403, "route is not classified for access; refused"
    if token is None:
        hmac.compare_digest(b"x", presented or b"y")
        return 503, f"daemon authentication is not configured ({TOKEN_ENV} unset); refused"
    if hmac.compare_digest(presented or b"", token):
        return None
    return 401, f"daemon token required (Authorization: Bearer <{TOKEN_ENV}>)"


class DaemonAuthMiddleware:
    """Pure ASGI: classify the matched route, check the bearer, refuse before the endpoint runs."""

    def __init__(self, app, *, router, token: bytes | None,
                 table: dict[tuple[str, str], str] = ROUTE_ACCESS):
        self.app = app
        self.router = router
        self.token = token
        self.table = table

    def _access(self, scope) -> tuple[bool, str | None]:
        for route in self.router.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return True, self.table.get((scope["method"], getattr(route, "path", "")))
        return False, None

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind != "http":
            _say(f"auth: refused a {kind!r} connection (the daemon serves plain HTTP only)")
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        matched, access = self._access(scope)
        if not matched:  # 404 / 405 - nothing runs; let the router answer
            await self.app(scope, receive, send)
            return
        verdict = decide(access, _presented(scope), self.token)
        if verdict is None:
            await self.app(scope, receive, send)
            return
        status, reason = verdict
        why = {401: "no/invalid token", 403: "unclassified route", 503: "not configured"}.get(
            status, "refused")
        _say(f"auth: refused {scope['method']} {scope.get('path')} -> {status} ({why})")
        headers = {REFUSAL_HEADER: "refused"}
        if status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        await JSONResponse({"detail": reason}, status_code=status, headers=headers)(scope, receive, send)


# ---------------------------------------------------------------------------
# Bind scope (LC_DAEMON_HIDE_FROM)
# ---------------------------------------------------------------------------

def hidden_hosts(environ=None) -> list[str]:
    env = os.environ if environ is None else environ
    return [h.strip() for h in (env.get(HIDE_ENV) or "").split(",") if h.strip()]


def local_ipv4_interfaces() -> list[tuple[str, str]]:
    """(address, netmask) of every IPv4 interface. Linux only (SIOCGIFADDR / SIOCGIFNETMASK)."""
    import fcntl
    import struct

    out: list[tuple[str, str]] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _idx, name in socket.if_nameindex():
            req = struct.pack("256s", name.encode()[:15])
            try:
                addr = fcntl.ioctl(s.fileno(), 0x8915, req)[20:24]
                mask = fcntl.ioctl(s.fileno(), 0x891B, req)[20:24]
            except OSError:
                continue  # no IPv4 address on this interface
            out.append((socket.inet_ntoa(addr), socket.inet_ntoa(mask)))
    return out


def resolve_ipv4(host: str, *, wait_s: float = 60.0, step_s: float = 2.0) -> list[str]:
    deadline = time.monotonic() + wait_s
    while True:
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            ips = sorted({i[4][0] for i in infos})
            if ips:
                return ips
        except socket.gaierror:
            pass
        if time.monotonic() >= deadline:
            return []
        time.sleep(step_s)


def select_bind_hosts(interfaces: list[tuple[str, str]], hidden_ips: list[str]) -> list[str]:
    """The interface addresses to listen on: every IPv4 interface whose subnet holds none of
    `hidden_ips`. Pure, for unit tests. Loopback is always kept (the healthcheck uses it)."""
    hidden = [ipaddress.IPv4Address(ip) for ip in hidden_ips]
    keep: list[str] = []
    for addr, mask in interfaces:
        net = ipaddress.IPv4Network(f"{addr}/{mask}", strict=False)
        if ipaddress.IPv4Address(addr).is_loopback or not any(h in net for h in hidden):
            keep.append(addr)
    return keep


def bind_hosts(default_host: str, environ=None) -> list[str]:
    """Where the daemon listens. Raises SystemExit (fail closed) when the scope cannot be
    established - never widens to every interface."""
    hosts = hidden_hosts(environ)
    if not hosts:
        return [default_host]
    hidden_ips: list[str] = []
    for h in hosts:
        ips = resolve_ipv4(h)
        if not ips:
            raise SystemExit(f"[little-coder] bind: {HIDE_ENV} names {h!r}, which does not resolve; "
                             f"refusing to start rather than listen on its network (fail closed)")
        hidden_ips.extend(ips)
    keep = select_bind_hosts(local_ipv4_interfaces(), hidden_ips)
    if not any(not ipaddress.IPv4Address(a).is_loopback for a in keep):
        raise SystemExit(f"[little-coder] bind: every non-loopback interface shares a network with "
                         f"{hosts}; refusing to start (nothing could reach the daemon)")
    _say(f"bind: listening on {keep} only - not on the network(s) of {hosts} ({HIDE_ENV})")
    return keep
