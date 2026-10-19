"""The intake client: the ONLY code in a connector that opens a socket to intake.

    IntakeClient(url, key, connector=...).submit(envelope) -> Submission

WHAT IT SENDS: `POST <url>` (http, path exactly /v1/intake, no query, no userinfo) with
`Authorization: Bearer <the connector's own key>`, `Content-Type: application/json`, a
Content-Length (never chunked) and `Connection: close`. The body is an Envelope that is CHECKED
AGAIN here (envelope.from_body) right before it is sent, so nothing intake's stage 1 would
refuse ever leaves the process, whoever made the Envelope object; its connector must be the
client's. A body over `max_request_bytes` (default 8 MiB, intake's INTAKE_MAX_REQUEST_BYTES) is
refused locally ("too_large") - intake would answer 413 without reading it.

THE BOUNDED SEND (intake's own pattern, transport/http_gateway.py): ONE deadline per attempt,
`timeout_s` from its start, not per socket operation. The connect is bounded by the socket
timeout set to what is left; once connected, a watchdog timer shuts the socket down at the
deadline (ending a stall while sending or while waiting for the headers); the reply is read in
pieces with the socket timeout set to what is left, to at most `max_reply_bytes` + 1 bytes. No
proxy is consulted (http.client, not urllib), a redirect is not followed (a 3xx is just an
answer: stop), and a reply shorter than its Content-Length is a network failure.

RETRIES (outcomes.py has the table): 503, 408 and every no-answer failure are retried with
capped exponential backoff (Retry-After honoured, capped) until `max_attempts` or the item's
`budget_s` runs out; then the item is `deferred` (not stored; a later run sends it again - the
key makes that idempotent). Everything else is final for the item: record, or stop the run.

CLEAN TEXT (opt-in, `return_clean_text=True`; the search gate only): a `clean` answer's Outcome
carries intake's text, title and labels in `extra` (outcomes.py). Off by default.

LOGGING: one line per attempt - the connector, the key's sha256 prefix, the HTTP status, the
label, the action. Never a header, the key, a body or intake's text.
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from . import envelope as _env
from . import outcomes
from .secret import Secret

log = logging.getLogger("connector_kit.client")

INTAKE_PATH = "/v1/intake"
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_REPLY_BYTES = 32 * 1024 * 1024
_PIECE = 65536


class TransportFailure(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class ClientConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_s: float = 1.0
    cap_s: float = 30.0
    retry_after_cap_s: float = 60.0
    budget_s: float = 600.0

    def delay(self, attempt: int, retry_after: float | None) -> float:
        """The wait before attempt `attempt + 1` (attempt counts from 1)."""
        backoff = min(self.cap_s, self.base_s * (2 ** (attempt - 1)))
        if retry_after is not None and retry_after >= 0:
            return min(max(backoff, retry_after), self.retry_after_cap_s)
        return backoff


@dataclass(frozen=True)
class Submission:
    outcome: outcomes.Outcome
    attempts: int
    history: tuple[outcomes.Outcome, ...] = field(default_factory=tuple)

    @property
    def final(self) -> str:
        """record | stop | deferred | too_large"""
        if self.outcome.action == "retry":
            return "deferred"
        return self.outcome.action


def check_url(url: str) -> tuple[str, int]:
    u = urlsplit(url)
    if u.scheme != "http":
        raise ClientConfigError("the intake URL must be http://<host>[:port]/v1/intake (intake is internal)")
    if not u.hostname or u.username is not None or u.password is not None:
        raise ClientConfigError("the intake URL needs a host and no userinfo")
    if u.path != INTAKE_PATH or u.query or u.fragment:
        raise ClientConfigError(f"the intake URL path must be exactly {INTAKE_PATH}, with no query")
    try:
        port = u.port or 80
    except ValueError:
        raise ClientConfigError("the intake URL port is not a number") from None
    return u.hostname, port


class _Watchdog:
    """Shuts the connection's socket down at the deadline (from a timer thread)."""

    def __init__(self, seconds: float):
        self.sock = None
        self.fired = False
        self._lock = threading.Lock()
        self._timer = threading.Timer(max(seconds, 0.0), self._fire)
        self._timer.daemon = True

    def start(self):
        self._timer.start()

    def watch(self, sock):
        with self._lock:
            self.sock = sock
            if self.fired:
                self._shutdown()

    def _shutdown(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001 - already closed: nothing left to stop
                pass

    def _fire(self):
        with self._lock:
            self.fired = True
            self._shutdown()

    def cancel(self):
        self._timer.cancel()


def _retry_after(value: str | None) -> float | None:
    if value is None or not value.strip().isdigit():
        return None
    return float(int(value.strip()))


class IntakeClient:
    def __init__(self, url: str, key: Secret, *, connector: str, timeout_s: float = 60.0,
                 max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
                 max_reply_bytes: int = DEFAULT_MAX_REPLY_BYTES, retry: RetryPolicy = RetryPolicy(),
                 sleep: Callable[[float], None] = time.sleep, return_clean_text: bool = False):
        if not isinstance(key, Secret):
            raise ClientConfigError("the key must be a Secret (secret.load_key_file)")
        if not _env.is_slug(connector):
            raise ClientConfigError("the connector id must match [a-z0-9][a-z0-9._-]*")
        if timeout_s <= 0 or max_request_bytes <= 0 or max_reply_bytes <= 0:
            raise ClientConfigError("timeouts and caps must be positive")
        self.host, self.port = check_url(url)
        self.url = url
        self._key = key
        self.connector = connector
        self.timeout_s = float(timeout_s)
        self.max_request_bytes = int(max_request_bytes)
        self.max_reply_bytes = int(max_reply_bytes)
        self.retry = retry
        self._sleep = sleep
        self.return_clean_text = bool(return_clean_text)

    def __repr__(self) -> str:
        return f"IntakeClient({self.url}, connector={self.connector}, key=***)"

    # -- one attempt --------------------------------------------------------------------------------
    def _post(self, body: bytes) -> tuple[int, dict, bytes]:
        deadline = time.monotonic() + self.timeout_s
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        dog = _Watchdog(self.timeout_s)
        dog.start()
        try:
            conn.connect()
            sock = conn.sock
            dog.watch(sock)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TransportFailure("timeout", "the attempt passed its deadline")
            sock.settimeout(left)
            conn.putrequest("POST", INTAKE_PATH, skip_accept_encoding=True)
            conn.putheader("Authorization", "Bearer " + self._key.reveal())
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
            conn.putheader("Connection", "close")
            conn.endheaders()
            try:
                conn.send(body)
            except OSError:
                pass  # intake may answer (413/401/403) and close before reading the body
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            data = bytearray()
            while True:
                left = deadline - time.monotonic()
                if left <= 0 or dog.fired:
                    raise TransportFailure("timeout", "the attempt passed its deadline")
                if resp.isclosed():
                    break
                try:
                    sock.settimeout(left)
                except OSError:
                    if resp.isclosed():
                        break
                    raise
                piece = resp.read1(min(_PIECE, self.max_reply_bytes + 1 - len(data)))
                if not piece:
                    break
                data += piece
                if len(data) > self.max_reply_bytes:
                    raise TransportFailure("oversize", f"reply over {self.max_reply_bytes} bytes")
            if dog.fired or time.monotonic() > deadline:
                raise TransportFailure("timeout", "the attempt passed its deadline")
            declared = headers.get("content-length")
            if declared is not None and declared.strip().isdigit() and int(declared) != len(data):
                raise TransportFailure("network", f"reply body {len(data)} bytes, Content-Length {declared}")
            return resp.status, headers, bytes(data)
        except TransportFailure:
            raise
        except (socket.timeout, TimeoutError):
            raise TransportFailure("timeout", "no reply in time") from None
        except Exception as e:  # noqa: BLE001 - every other failure is a network outcome
            if dog.fired or time.monotonic() >= deadline:
                raise TransportFailure("timeout", "the attempt passed its deadline") from None
            raise TransportFailure("network", type(e).__name__) from None
        finally:
            dog.cancel()
            conn.close()

    def send(self, env: _env.Envelope) -> outcomes.Outcome:
        """One attempt. Raises InvalidEnvelope / ValueError before any byte is sent."""
        if not isinstance(env, _env.Envelope):
            raise _env.InvalidEnvelope("the client sends Envelope objects only (envelope.build)")
        checked = _env.from_body(env.body)          # re-check: a hand-made Envelope gains nothing
        if checked.key != env.key:
            raise _env.InvalidEnvelope("the envelope's key does not match its body")
        if checked.connector != self.connector:
            raise _env.InvalidEnvelope("the envelope names another connector than this client's key")
        body = checked.to_bytes()
        if len(body) > self.max_request_bytes:
            raise RequestTooLarge(len(body), self.max_request_bytes)
        tag = checked.key.sha256[:12]
        try:
            status, headers, data = self._post(body)
        except TransportFailure as e:
            out = outcomes.network(e.kind, e.detail)
            log.info("intake %s %s: no answer (%s) -> %s", self.connector, tag, e.kind, out.action)
            return out
        try:
            doc = json.loads(data.decode("utf-8")) if data else None
        except (UnicodeDecodeError, ValueError):
            doc = None
        out = outcomes.classify(status, doc, _retry_after(headers.get("retry-after")), checked.key.to_dict(),
                                self.connector, keep_clean_text=self.return_clean_text)
        log.info("intake %s %s: http %s label=%s -> %s", self.connector, tag, status, out.label, out.action)
        return out

    # -- with retries -------------------------------------------------------------------------------
    def submit(self, env: _env.Envelope) -> Submission:
        start = time.monotonic()
        history: list[outcomes.Outcome] = []
        attempt = 0
        while True:
            attempt += 1
            out = self.send(env)
            history.append(out)
            if out.action != "retry":
                return Submission(out, attempt, tuple(history))
            if attempt >= self.retry.max_attempts:
                return Submission(out, attempt, tuple(history))
            wait = self.retry.delay(attempt, out.retry_after)
            if time.monotonic() - start + wait > self.retry.budget_s:
                return Submission(out, attempt, tuple(history))
            self._sleep(wait)


class RequestTooLarge(ValueError):
    def __init__(self, size: int, cap: int):
        super().__init__(f"the request body is {size} bytes, over the {cap}-byte cap; not sent")
        self.size = size
        self.cap = cap
