"""auth - who may call which agent-bridge HTTP route (ao-auth, 2026-10-07).

The bridge's HTTP surface drives the org: `/nl` takes any operator verb (approve, abort, retire a
check, move a model lane), `/decision` clears gates, `/kill-switch` stops it. Before this module it
was open to anything that could open a TCP connection to agent-bridge:8000 - including the worker
command sandboxes. Now every route carries one access class from the table in `main.py`:

  public    no token (only GET /health - liveness probes)
  operator  `Authorization: Bearer <AO_OPERATOR_TOKEN>` - operator tooling on the host
  worker    `Authorization: Bearer <AO_WORKER_TOKEN>` (or the operator token) - the routes the
            worker side really calls (the floor-check hook); AO_WORKER_TOKEN lives only in
            ao-worker-1/2, never in the ao-ot command sandboxes

Fail closed, never open:
- AO_OPERATOR_TOKEN unset/blank -> every operator route answers 503 (and a startup log line says so).
- both tokens unset -> worker routes answer 503 too.
- the two tokens equal -> both are discarded (a worker would otherwise hold the operator's key).
- a route that exists but is missing from the table -> `verify_table` refuses to build the app, and
  the middleware refuses it (403) should one ever slip past.

Tokens are compared in constant time and never logged, echoed or returned. The check is an ASGI
middleware, so it runs BEFORE the request body is read or validated: an unauthorised caller learns
nothing about the body schema.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass

from fastapi.routing import APIRoute
from starlette.responses import JSONResponse
from starlette.routing import Match

log = logging.getLogger("agent_bridge.auth")

PUBLIC = "public"
OPERATOR = "operator"
WORKER = "worker"
_CLASSES = {PUBLIC, OPERATOR, WORKER}


@dataclass(frozen=True)
class Tokens:
    operator: bytes | None
    worker: bytes | None


def _clean(secret) -> bytes | None:
    raw = secret.get_secret_value() if hasattr(secret, "get_secret_value") else (secret or "")
    raw = raw.strip()
    return raw.encode("utf-8") if raw else None


def load_tokens(settings) -> Tokens:
    """Resolve the two tokens from Settings and log (without the values) what that means."""
    op = _clean(getattr(settings, "operator_token", None))
    wk = _clean(getattr(settings, "worker_token", None))
    if op is not None and wk is not None and hmac.compare_digest(op, wk):
        log.error("auth: AO_WORKER_TOKEN equals AO_OPERATOR_TOKEN - both discarded; every "
                  "authenticated route refuses (503) until they differ")
        return Tokens(None, None)
    if op is None:
        log.error("auth: AO_OPERATOR_TOKEN is not set - every operator route refuses (503) until it "
                  "is set in agent-org/docker/.env (fail closed)")
    else:
        log.info("auth: operator token configured; operator routes require it")
    if wk is None:
        log.warning("auth: AO_WORKER_TOKEN is not set - worker routes (floor check) accept only the "
                    "operator token, so the workers' floor hook fails closed")
    else:
        log.info("auth: worker token configured; accepted on worker routes only")
    return Tokens(op, wk)


def verify_table(app, table: dict[tuple[str, str], str]) -> None:
    """Every (method, path) the app serves is classified, and every table row is a live route."""
    served: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                served.add((method, route.path))
        else:  # docs/openapi/static mounts are disabled: anything else is unclassified surface
            served.add(("*", getattr(route, "path", repr(route))))
    unclassified = sorted(served - set(table))
    stale = sorted(set(table) - served)
    bad = sorted(k for k, v in table.items() if v not in _CLASSES)
    if unclassified or stale or bad:
        raise RuntimeError(f"agent-bridge route access table is out of date: unclassified={unclassified} "
                           f"stale={stale} bad_class={bad}")


def _presented(scope) -> bytes | None:
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            scheme, _, cred = value.partition(b" ")
            if scheme.lower() == b"bearer" and cred.strip():
                return cred.strip()
            return None
    return None


def _match(presented: bytes | None, token: bytes | None) -> bool:
    # compare_digest on equal-type bytes; a None side never matches (and still costs a compare).
    if token is None:
        hmac.compare_digest(b"x", presented or b"y")
        return False
    return hmac.compare_digest(presented or b"", token)


def decide(access: str | None, presented: bytes | None, tokens: Tokens) -> tuple[int, str] | None:
    """None = let it through; else (status, reason). Pure, for unit tests."""
    if access == PUBLIC:
        return None
    if access not in (OPERATOR, WORKER):
        return 403, "route is not classified for access; refused"
    is_op = _match(presented, tokens.operator)
    is_wk = _match(presented, tokens.worker)
    if access == OPERATOR:
        if tokens.operator is None:
            return 503, "operator authentication is not configured (AO_OPERATOR_TOKEN unset); refused"
        if is_op:
            return None
        if is_wk:
            return 403, "this route needs the operator token"
        return 401, "operator token required (Authorization: Bearer <AO_OPERATOR_TOKEN>)"
    # WORKER route: the worker token, or the operator's
    if tokens.operator is None and tokens.worker is None:
        return 503, "worker authentication is not configured (AO_WORKER_TOKEN unset); refused"
    if is_op or is_wk:
        return None
    return 401, "worker token required (Authorization: Bearer <AO_WORKER_TOKEN>)"


class AuthMiddleware:
    """Pure ASGI: classify the matched route, check the bearer, refuse before the endpoint runs."""

    def __init__(self, app, *, router, table: dict[tuple[str, str], str], tokens: Tokens):
        self.app = app
        self.router = router
        self.table = table
        self.tokens = tokens

    def _access(self, scope) -> tuple[bool, str | None]:
        for route in self.router.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return True, self.table.get((scope["method"], getattr(route, "path", "")))
        return False, None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        matched, access = self._access(scope)
        if not matched:  # 404 / 405 - nothing runs; let the router answer
            await self.app(scope, receive, send)
            return
        verdict = decide(access, _presented(scope), self.tokens)
        if verdict is None:
            await self.app(scope, receive, send)
            return
        status, reason = verdict
        log.warning("auth: refused %s %s -> %d (%s)", scope["method"], scope.get("path"), status,
                    {401: "no/invalid token", 403: "wrong token class", 503: "not configured"}.get(
                        status, "refused"))
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        await JSONResponse({"detail": reason}, status_code=status, headers=headers)(scope, receive, send)
