"""The gate's normal behaviour (the exploits are in test_exploits.py): what a clean page and a clean
result look like, the envelopes intake receives, the read policy, fail-closed screening, the caller
lanes, and the privacy of the logs."""

from __future__ import annotations

import base64
import logging
import re
import sys
from pathlib import Path

import pytest

from fakes import PUBLIC_IP, PUBLIC_IP2
from gate_support import INTAKE_KEY, running_gate

GATEWAY = Path(__file__).resolve().parents[1]
OB1_INTAKE = GATEWAY.parents[1] / "OB1" / "integrations" / "intake"
_FENCE = re.compile(r"^<<UNTRUSTED-([0-9a-f]{12}) src=(\S+) verdict=clean>>\n(.*)\n<<END-\1>>$",
                    re.S)


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with running_gate(tmp_path, monkeypatch) as g:
        yield g


def _intake_validate():
    """intake's own stage-1 validator from the pinned OB1 checkout, or None without it."""
    if not (OB1_INTAKE / "intake" / "envelope.py").is_file():
        return None
    sys.path.insert(0, str(OB1_INTAKE))
    try:
        from intake import envelope  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - e.g. a missing optional dependency
        return None
    finally:
        sys.path.remove(str(OB1_INTAKE))
    return envelope.validate


# -- a clean page ----------------------------------------------------------------------------------
def test_a_clean_page_is_returned_fenced_and_labelled(gate) -> None:
    r = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/page"},
                         headers=gate.auth())
    body = r.json()
    assert body["status"] == "ok", body
    assert "ordinary article about bread" in body["text"]
    m = _FENCE.match(body["fenced"])
    assert m and "ordinary article about bread" in m.group(3), body["fenced"]
    assert body["labels"]["connector"] == "search-gate"
    assert body["labels"]["source"] == "web-page"
    assert body["labels"]["verdict"] == "clean"
    assert gate.internet.connects == [f"{PUBLIC_IP}:80"], "dialled by pinned IP, through the proxy"


def test_the_envelope_is_exactly_intakes_stage_one_schema(gate) -> None:
    gate.client.post("/v1/fetch", json={"url": "http://public.example.com/page"},
                     headers=gate.auth())
    gate.engine.results = [{"url": "https://public.example.com/a", "title": "T", "snippet": "S"}]
    gate.client.post("/v1/search", json={"query": "bread"}, headers=gate.auth())
    assert len(gate.intake.envelopes) == 2
    page, snippet = gate.intake.envelopes
    for env in (page, snippet):
        assert set(env) == {"source", "connector", "external_id", "fetched_at", "content_type",
                            "raw", "raw_encoding", "metadata", "links"}
        assert env["connector"] == "search-gate"
    assert page["source"] == "web-page"
    assert page["external_id"] == "http://public.example.com/page"
    assert page["content_type"] == "text/html; charset=utf-8"
    assert b"ordinary article" in base64.b64decode(page["raw"])
    assert snippet["source"] == "web-search"
    assert base64.b64decode(snippet["raw"]) == b"T\nS"
    assert "bread" not in str(snippet), "the query never goes to intake (it would be stored)"
    validate = _intake_validate()
    if validate is None:
        pytest.skip("OB1 checkout not present: schema asserted above, not intake's validator")
    for env in (page, snippet):
        validate(env)  # raises Rejected on any deviation


def test_a_redirect_to_a_public_page_is_followed_and_every_hop_pinned(gate) -> None:
    r = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/redir-public"},
                         headers=gate.auth())
    body = r.json()
    assert body["status"] == "ok", body
    assert body["final_url"] == "http://other.example.com/page"
    assert body["redirects"] == ["http://other.example.com/page"]
    assert gate.internet.connects == [f"{PUBLIC_IP}:80", f"{PUBLIC_IP2}:80"]
    assert gate.intake.envelopes[0]["metadata"]["final_url"] == "http://other.example.com/page"


def test_a_redirect_loop_is_cut_at_the_cap(gate) -> None:
    r = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/redir-loop"},
                         headers=gate.auth())
    body = r.json()
    assert body["status"] == "refused" and "redirects" in body["reason"], body
    assert len(gate.internet.connects) == 6  # the first request + 5 redirects


# -- the read policy -------------------------------------------------------------------------------
def test_a_pending_page_is_withheld(gate) -> None:
    body = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/pending"},
                            headers=gate.auth()).json()
    assert body["status"] == "withheld" and body["label"] == "pending", body
    assert body["text"] is None and body["fenced"] is None


def test_search_counts_what_it_withholds(gate) -> None:
    gate.engine.results = [
        {"url": "https://public.example.com/a", "title": "Fine", "snippet": "Plain."},
        {"url": "https://public.example.com/b", "title": "Later", "snippet": "pending-me"},
    ]
    body = gate.client.post("/v1/search", json={"query": "bread"}, headers=gate.auth()).json()
    assert [x["url"] for x in body["results"]] == ["https://public.example.com/a"]
    assert body["withheld"] == {"pending": 1}
    first = body["results"][0]
    assert first["title"] == "Fine" and first["snippet"] == "Plain."
    assert _FENCE.match(first["fenced"])
    assert first["labels"]["source"] == "web-search"


def test_intake_unavailable_fails_closed_after_one_retry(gate) -> None:
    gate.intake.force_status = 503
    body = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/page"},
                            headers=gate.auth()).json()
    assert body["status"] == "withheld" and body["label"] == "error", body
    assert body["text"] is None
    assert len(gate.intake.envelopes) == 2  # one retry, then withheld


@pytest.mark.parametrize("code", [400, 401, 403, 413])
def test_intake_refusals_are_not_retried(gate, code: int) -> None:
    gate.intake.force_status = code
    body = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/page"},
                            headers=gate.auth()).json()
    assert body["status"] == "withheld" and body["label"] == "error"
    assert len(gate.intake.envelopes) == 1


def test_no_intake_key_means_503_not_unscreened(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    with running_gate(tmp_path, monkeypatch, {"INTAKE_KEY_FILE": ""}) as g:
        assert g.client.post("/v1/fetch", json={"url": "http://public.example.com/page"},
                             headers=g.auth()).status_code == 503
        assert g.client.post("/v1/search", json={"query": "x"},
                             headers=g.auth()).status_code == 503
        assert g.client.get("/search", params={"q": "x"}, headers=g.auth()).status_code == 503
        assert g.internet.connects == [] and g.engine.requests == []


# -- the caller lanes ------------------------------------------------------------------------------
def test_the_legacy_key_keeps_todays_unscreened_answer(gate) -> None:
    body = gate.client.post("/v1/search", json={"query": "bread"},
                            headers=gate.auth("legacy")).json()
    assert "withheld" not in body and len(body["results"]) == 2
    assert gate.intake.envelopes == []


def test_the_legacy_key_cannot_use_the_gate_routes(gate) -> None:
    for path, payload in (("/v1/fetch", {"url": "http://public.example.com/page"}),
                          ("/v1/web-loader", {"urls": ["http://public.example.com/page"]}),
                          ("/v1/owui/search", {"query": "x"})):
        assert gate.client.post(path, json=payload, headers=gate.auth("legacy")).status_code == 403


def test_a_wrong_key_is_401_everywhere(gate) -> None:
    bad = {"Authorization": "Bearer " + "z" * 40}
    assert gate.client.get("/search", params={"q": "x"}, headers=bad).status_code == 401
    assert gate.client.post("/v1/search", json={"query": "x"}, headers=bad).status_code == 401
    assert gate.client.post("/v1/fetch", json={"url": "http://x.example.com/"},
                            headers=bad).status_code == 401


def test_the_anonymous_legacy_switch_restores_todays_compat_route(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with running_gate(tmp_path, monkeypatch, {"SEARCH_ANONYMOUS_LEGACY": "true"}) as g:
        r = g.client.get("/search", params={"q": "bread", "format": "json"})
        assert r.status_code == 200 and r.json()["number_of_results"] == 2
        assert g.client.post("/v1/fetch", json={"url": "http://public.example.com/page"}
                             ).status_code == 401  # the switch opens nothing else


def test_the_compat_route_screens_a_keyed_caller(gate) -> None:
    body = gate.client.get("/search", params={"q": "bread", "format": "json"},
                           headers=gate.auth()).json()
    assert body["number_of_results"] == 2
    assert all(_FENCE.match(x["content"]) for x in body["results"])
    assert all(x["labels"]["verdict"] == "clean" for x in body["results"])


def test_tavily_screens_a_keyed_caller(gate) -> None:
    from gate_support import CALLERS

    body = gate.client.post("/tavily/search", json={"query": "bread",
                                                    "api_key": CALLERS["research"]}).json()
    assert len(body["results"]) == 2 and all(_FENCE.match(x["content"]) for x in body["results"])


# -- Open WebUI's two external contracts -----------------------------------------------------------
def test_owui_web_loader_contract(gate) -> None:
    docs = gate.client.post("/v1/web-loader",
                            json={"urls": ["http://public.example.com/page",
                                           "http://10.0.0.7/secret",
                                           "http://public.example.com/pending"]},
                            headers=gate.auth()).json()
    assert len(docs) == 1
    assert set(docs[0]) == {"page_content", "metadata"}
    assert docs[0]["metadata"]["source"] == "http://public.example.com/page"
    assert _FENCE.match(docs[0]["page_content"])


def test_owui_web_loader_caps_the_batch(gate) -> None:
    urls = [f"http://public.example.com/page?{i}" for i in range(21)]
    r = gate.client.post("/v1/web-loader", json={"urls": urls}, headers=gate.auth())
    assert r.status_code == 413


def test_owui_external_search_contract(gate) -> None:
    rows = gate.client.post("/v1/owui/search", json={"query": "bread", "count": 1},
                            headers=gate.auth()).json()
    assert len(rows) == 1 and set(rows[0]) == {"link", "title", "snippet"}
    assert _FENCE.match(rows[0]["snippet"])


# -- privacy: no query and no URL in the gateway's output (T0-d / A3) -----------------------------
class _Grab(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "http://testserver/" in msg:  # the TEST's own client (TestClient) logging its request
            return
        self.lines.append(f"{record.name}: {msg}")


def test_no_query_or_url_reaches_the_log(gate, capfd: pytest.CaptureFixture[str]) -> None:
    """Both outputs: structlog's (stdout) and the standard logging tree, where libraries log -
    httpx logs every request URL, query string included, at INFO (found by the docker round
    trip: the live gateway's log carries every engine query that way)."""
    grab = _Grab()
    root = logging.getLogger()
    before = root.level
    # production: configure_logging's basicConfig sets the root to LOG_LEVEL (INFO); under pytest
    # basicConfig is a no-op (pytest owns the root handlers), so set the level it would have set
    root.setLevel(logging.INFO)
    root.addHandler(grab)
    from gateway import logging as gwlog

    gwlog._configured = False  # run the gateway's logging set-up as production does
    gwlog.configure_logging("INFO")
    secret_query = "operator-private-query-7f3a"
    gate.client.post("/v1/search", json={"query": secret_query}, headers=gate.auth())
    gate.client.get("/search", params={"q": secret_query}, headers=gate.auth("legacy"))
    gate.client.post("/v1/fetch", json={"url": "http://public.example.com/page?token=7f3a"},
                     headers=gate.auth())
    gate.client.post("/v1/fetch", json={"url": "http://10.0.0.7/x?leak=7f3a"}, headers=gate.auth())
    root.removeHandler(grab)
    root.setLevel(before)
    out, err = capfd.readouterr()
    logged = chr(10).join(grab.lines)
    for needle in (secret_query, "7f3a", "public.example.com", "10.0.0.7", INTAKE_KEY):
        assert needle not in out and needle not in err, needle
        assert needle not in logged, (needle, [ln for ln in grab.lines if needle in ln][:3])


def test_the_image_runs_without_an_access_log() -> None:
    dockerfile = (GATEWAY / "Dockerfile").read_text(encoding="utf-8")
    cmd = [ln for ln in dockerfile.splitlines() if ln.startswith("CMD")]
    assert cmd and "--no-access-log" in cmd[-1], cmd
