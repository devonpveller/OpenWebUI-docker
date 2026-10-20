"""A fake intake service for connector tests: records every request and replays every outcome the
real I5 service produces (integrations/intake/intake_service/web.py + core.py).

    with FakeIntake({"my-connector": key}) as fake:
        fake.script("item-1", 503, "clean")      # first answer 503, the retry gets `clean`
        fake.default = "pending"                 # everything not scripted
        ... connector.run() against fake.url ...
        fake.received                            # what arrived, checked against intake's schema

THE HTTP CONTRACT IT MIRRORS, in the real order (web.py): route and method (404/405); the key
(none or not a bearer token: 401 with WWW-Authenticate; a key that is no caller's: 403); then
Transfer-Encoding (411), no Content-Length (411), a malformed one (400), over max_request_bytes
(413; the body is never read); the body within body_read_s in total (408); then core: not UTF-8
JSON / not an object (400), stage 1 (422 with a full `rejected` result body), the connector bound
to the key (403); then the scripted outcome. 503 answers carry Retry-After as the real ones do
(5 for "too many in flight", 30 for the rest).

OUTCOMES (`script()` / `default`): the result labels "clean", "suspicious", "pending",
"injected", "rejected" (a 200 with the real result body: every key core._response() writes, the
key, the labels, text only for clean/suspicious), and the HTTP answers 400, 401, 403, 408, 411,
413, 422, 500, and 503 ("503" = store down; "503:busy", "503:not-ready", "503:inflight",
"503:no-key" for the other real 503 bodies). A result is stored like the real service stores it:
the same key again is a REPEAT (`repeat: true`, the same intake_id, nothing new); new bytes for
the same (source, external_id) are revision n+1 superseding the last hash.

THE SCHEMA ORACLE: every body is validated with intake's own `intake.envelope.validate` when the
intake package is importable (it is, inside this repo: ../../intake), else with the kit's mirror;
`oracle` says which. `received[i].valid` is that verdict, so a test can fail a connector for any
non-envelope that reached intake, whatever the fake answered.

connector_tests/test_fake_parity.py checks this file against the real service code.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import envelope as _kit_env

INTAKE_DIR = Path(__file__).resolve().parents[3] / "intake"

# intake.result.COUNT_KEYS (the `stripped` object); parity-tested
COUNT_KEYS = (
    "hidden_elements", "dropped_elements", "links_dropped", "truncated_bytes", "controls_refs", "attachments",
    "tag", "bidi", "variation_selectors", "controls", "surrogates", "cf", "default_ignorable", "flag_tags_kept",
    "nonstarters_trimmed", "neutralised_lines", "neutralised_refs",
)
# the keys of core.Intake._response / _repeat_response, in order; parity-tested
RESULT_KEYS = ("intake_id", "status", "verdict", "label", "d2_row", "repeat", "rescreen", "revision", "supersedes",
               "key", "reasons", "stripped", "text", "text_withheld", "title", "links", "attachments", "labels",
               "error")
_STATUS = {"clean": "done", "suspicious": "done", "injected": "done", "pending": "pending", "rejected": "rejected"}
# the D2 row of a FIRST answer for each label (intake/d2.py), as the real service writes it
FIRST_ROW = {"clean": "no-signal", "suspicious": "permanent-error", "pending": "awaiting-second-opinion",
             "injected": "strong-rule", "rejected": None}
LABELS = tuple(_STATUS)
# the real error bodies (web.py / core.py), word for word; parity-tested
ERR = {
    401: "a key is required (Authorization: Bearer <key>)",
    403: "this key is not valid",
    404: "not found",
    405: "method not allowed",
    408: "the body did not arrive within {s:g}s",
    411: "Content-Length is required",
    "411:chunked": "send the body with a Content-Length (no chunked transfer)",
    "400:length": "Content-Length is not a whole number",
    "400:short": "the body is shorter than its Content-Length",
    "400:utf8": "the body is not UTF-8 JSON",
    "400:object": "the body is not a JSON object",
    413: "the body is over {n} bytes; send raw as base64 if it is near the 5 MB raw cap",
    "503:inflight": "too many requests in flight; retry later",
    "503:busy": "busy: no screening worker is free; retry later",
    "503:not-ready": "not ready: no screening worker is loaded; retry later",
    503: "store unavailable; retry later",
    500: "internal error",
}
HTTP_OUTCOMES = (400, 401, 403, 408, 411, 413, 422, 500, 503, "503:busy", "503:not-ready", "503:inflight",
                 "503:no-key")
OUTCOMES = LABELS + HTTP_OUTCOMES


def oracle():
    """(validate, name): intake's own stage 1 when importable, else the kit's mirror."""
    try:
        if str(INTAKE_DIR) not in sys.path and INTAKE_DIR.is_dir():
            sys.path.append(str(INTAKE_DIR))
        from intake import envelope as real  # noqa: PLC0415
        return real.validate, "intake.envelope.validate"
    except Exception:  # noqa: BLE001 - outside the repo the mirror stands in
        return _kit_env.check, "connector_kit.envelope.check (mirror)"


@dataclass
class Received:
    status: int
    caller: str | None              # the connector the key named (None: no valid key)
    body: bytes
    valid: bool | None = None       # stage 1 per the oracle (None: the body was not read)
    error: str = ""
    outcome: object = None
    envelope: dict | None = None


@dataclass
class _Stored:
    intake_id: str
    label: str
    revision: int
    supersedes: str | None
    connector: str


@dataclass
class FakeIntake:
    keys: dict
    max_request_bytes: int = 8 * 1024 * 1024
    body_read_s: float = 5.0
    default: object = "clean"
    received: list = field(default_factory=list)

    def __post_init__(self):
        self._scripts: dict[str, list] = {}
        self._items: dict[tuple, _Stored] = {}
        self._latest: dict[tuple, tuple[int, str]] = {}
        self._lock = threading.Lock()
        self._validate, self.oracle = oracle()
        self._server = None
        self._thread = None

    # -- control ------------------------------------------------------------------------------------
    def script(self, external_id: str, *outcomes) -> None:
        for o in outcomes:
            if o not in OUTCOMES:
                raise ValueError(f"unknown outcome {o!r}; one of {OUTCOMES}")
        with self._lock:
            self._scripts.setdefault(external_id, []).extend(outcomes)

    def start(self) -> FakeIntake:
        self._server = _Server(("127.0.0.1", 0), _Handler, self)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-intake", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address[:2]

    @property
    def url(self) -> str:
        h, p = self.address
        return f"http://{h}:{p}/v1/intake"

    def requests_for(self, connector: str) -> list:
        return [r for r in self.received if r.caller == connector]

    # -- the answers --------------------------------------------------------------------------------
    def _caller(self, token: str) -> str | None:
        import hmac
        found = None
        for c, k in self.keys.items():
            if hmac.compare_digest(token.encode("ascii", "replace"), str(k).encode("ascii", "replace")) and found is None:
                found = c
        return found

    def _next(self, external_id: str):
        with self._lock:
            q = self._scripts.get(external_id)
            if q:
                return q.pop(0)
            return self.default

    def respond(self, method, path, headers, read) -> tuple[int, dict, list]:
        """The real order of checks; returns (status, doc, extra headers) and records the request."""
        route = path.partition("?")[0]
        if route not in ("/v1/intake", "/v1/stats", "/metrics", "/healthz"):
            return self._rec(404, {"error": ERR[404]}, None, b"")
        allowed = ("POST",) if route == "/v1/intake" else ("GET",)
        if method not in allowed:
            return self._rec(405, {"error": ERR[405]}, None, b"", [("Allow", ", ".join(allowed))])
        if route == "/healthz":
            return 200, {"ok": True, "workers": {"ready": 1, "total": 1}, "db": True}, []
        value = (headers.get("Authorization") or "").strip()
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return self._rec(401, {"error": ERR[401]}, None, b"", [("WWW-Authenticate", 'Bearer realm="intake"')])
        caller = self._caller(token.strip())
        if caller is None:
            return self._rec(403, {"error": ERR[403]}, None, b"")
        if route != "/v1/intake":
            return 200, {"schema": "intake-stats/1"} if route == "/v1/stats" else {}, []
        if headers.get("Transfer-Encoding") is not None:
            return self._rec(411, {"error": ERR["411:chunked"]}, caller, b"")
        raw_len = headers.get("Content-Length")
        if raw_len is None:
            return self._rec(411, {"error": ERR[411]}, caller, b"")
        if not (raw_len.isdigit() and 1 <= len(raw_len) <= 9 and raw_len.isascii()):
            return self._rec(400, {"error": ERR["400:length"]}, caller, b"")
        n = int(raw_len)
        if n > self.max_request_bytes:
            return self._rec(413, {"error": ERR[413].format(n=self.max_request_bytes)}, caller, b"")
        try:
            data = read(n)
        except TimeoutError:
            return self._rec(408, {"error": ERR[408].format(s=self.body_read_s)}, caller, b"")
        if data is None or len(data) != n:
            return self._rec(400, {"error": ERR["400:short"]}, caller, data or b"")
        return self._intake(caller, data)

    def _rec(self, status, doc, caller, body, extra=(), **kw):
        self.received.append(Received(status, caller, body, **kw))
        return status, doc, list(extra)

    def _intake(self, caller: str, data: bytes):
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return self._rec(400, {"error": ERR["400:utf8"]}, caller, data, valid=False, error="not UTF-8 JSON")
        if not isinstance(obj, dict):
            return self._rec(400, {"error": ERR["400:object"]}, caller, data, valid=False, error="not an object")
        try:
            env = self._validate(obj)
        except Exception as e:  # noqa: BLE001 - Rejected (or the mirror's InvalidEnvelope)
            doc = self.result_doc("rejected", None, caller, None, None, None, reason=str(e))
            return self._rec(422, doc, caller, data, valid=False, error=str(e)[:300])
        raw = env.raw if hasattr(env, "raw") else env
        key = {"source": obj["source"], "external_id": obj["external_id"], "sha256": hashlib.sha256(raw).hexdigest()}
        if obj["connector"] != caller:
            return self._rec(403, {"error": f"this key is not valid for connector {obj['connector']!r}"}, caller, data,
                             valid=True, envelope=obj)
        outcome = self._next(obj["external_id"])
        text = raw.decode("utf-8", "replace")
        if outcome in LABELS:
            status, doc = self._store(caller, key, outcome, text)
            extra = []
        else:
            status, doc, extra = self.http_answer(outcome)
        return self._rec(status, doc, caller, data, extra, valid=True, outcome=outcome, envelope=obj)

    def http_answer(self, outcome) -> tuple[int, dict, list]:
        if outcome == 408:
            return 408, {"error": ERR[408].format(s=self.body_read_s)}, []
        if outcome == 413:
            return 413, {"error": ERR[413].format(n=self.max_request_bytes)}, []
        if outcome == 401:
            return 401, {"error": ERR[401]}, [("WWW-Authenticate", 'Bearer realm="intake"')]
        if outcome == 400:
            return 400, {"error": ERR["400:object"]}, []
        if outcome == 422:
            return 422, self.result_doc("rejected", None, None, None, None, None, reason="scripted 422"), []
        if outcome == "503:inflight":
            return 503, {"error": ERR["503:inflight"]}, [("Retry-After", "5")]
        if outcome == "503:no-key":
            return 503, {"status": "pending", "error": {"kind": "transient", "detail": "worker: no key"}}, \
                [("Retry-After", "30")]
        if outcome in (503, "503:busy", "503:not-ready"):
            return 503, {"error": ERR[outcome]}, [("Retry-After", "30")]
        return int(outcome), {"error": ERR.get(outcome, "internal error")}, []

    def _store(self, caller, key, label, text) -> tuple[int, dict]:
        k = (key["source"], key["external_id"], key["sha256"])
        with self._lock:
            got = self._items.get(k)
            if got is not None:
                return 200, self.result_doc(got.label, got.intake_id, got.connector, key, got.revision,
                                            got.supersedes, repeat=True, text=text)
            last = self._latest.get(k[:2])
            revision, supersedes = (1, None) if last is None else (last[0] + 1, last[1])
            st = _Stored(str(uuid.uuid4()), label, revision, supersedes, caller)
            self._items[k] = st
            self._latest[k[:2]] = (revision, key["sha256"])
        return 200, self.result_doc(label, st.intake_id, caller, key, revision, supersedes, text=text)

    @staticmethod
    def result_doc(label, intake_id, connector, key, revision, supersedes, *, repeat=False, text=None,
                   reason="") -> dict:
        readable = label in ("clean", "suspicious")
        reasons = []
        if label == "rejected":
            reasons = [{"rule": "envelope" if key is None else "parser-bomb", "strength": "stage", "detail": reason}]
        elif label in ("injected",):
            reasons = [{"rule": "fake-strong", "strength": "strong", "detail": ""}]
        elif label == "pending":
            reasons = [{"rule": "fake-weak", "strength": "weak", "detail": ""}]
        doc = {
            "intake_id": intake_id,
            "status": _STATUS[label],
            "verdict": label if label in ("clean", "suspicious", "injected") else None,
            "label": label,
            "d2_row": FIRST_ROW[label],
            "repeat": repeat,
            "rescreen": False,
            "revision": revision,
            "supersedes": supersedes,
            "key": key,
            "reasons": reasons,
            "stripped": dict.fromkeys(COUNT_KEYS, 0),
            "text": text if readable else None,
            "text_withheld": None,
            "title": None,
            "links": [],
            "attachments": [],
            "labels": {"verdict": label, "connector": connector, "source": key and key["source"],
                       "external_id": key and key["external_id"], "intake_id": intake_id,
                       "classifier": "fake/none", "flags": []},
            "error": None,
        }
        return doc


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, fake: FakeIntake):
        self.fake = fake
        super().__init__(addr, handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = "intake"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    def _read(self, n: int) -> bytes:
        end = time.monotonic() + self.server.fake.body_read_s
        out = bytearray()
        while len(out) < n:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError
            self.connection.settimeout(left)
            try:
                piece = self.rfile.read1(min(65536, n - len(out)))
            except (TimeoutError, OSError):
                raise TimeoutError from None
            if not piece:
                break
            out += piece
        return bytes(out)

    def _do(self):
        status, doc, extra = self.server.fake.respond(self.command, self.path, self.headers, self._read)
        body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _do

    def log_message(self, format, *args):  # noqa: A002 - never log a header
        return
