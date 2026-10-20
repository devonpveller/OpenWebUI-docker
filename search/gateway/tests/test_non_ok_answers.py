"""Review T-2 (search-gate round 1 blocker): a non-`ok` /v1/fetch answer must carry NO string the
fetched site chose - only the caller's own url, status, label, an int http_status and a fixed
gate-authored reason. An `ok` answer's content_type is the allow-listed media type alone.

The six shapes of the review's repro, each with a site-chosen MARKER; the marker must be absent
from the WHOLE JSON body of /v1/fetch and of /v1/web-loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import MARKER
from gate_support import running_gate

SHAPES = [
    ("withheld page after a redirect through a marker path", "/marker-redirect", "withheld"),
    ("withheld page whose Content-Type parameter is marker prose", "/marker-ct-param", "withheld"),
    ("marker as the media type", "/marker-ct-prose", "refused"),
    ("marker as the Content-Encoding", "/marker-encoding", "refused"),
    ("marker in Content-Type on an HTTP 404", "/marker-404", "error"),
    ("redirect to a marker host that is refused", "/marker-redirect-host", "refused"),
]
NON_OK_KEYS = {"status", "url", "label", "http_status", "reason"}


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with running_gate(tmp_path, monkeypatch) as g:
        yield g


@pytest.mark.parametrize("what,path,status", SHAPES, ids=[s[0] for s in SHAPES])
def test_no_site_text_in_a_non_ok_fetch(gate, what: str, path: str, status: str) -> None:
    url = f"http://public.example.com{path}"
    r = gate.client.post("/v1/fetch", json={"url": url}, headers=gate.auth())
    body = r.json()
    assert body["status"] == status, body
    assert MARKER not in r.text.lower(), body
    present = {k for k, v in body.items() if v not in (None, [], {})}
    assert present <= NON_OK_KEYS, present - NON_OK_KEYS
    assert body["url"] == url
    assert body["http_status"] is None or isinstance(body["http_status"], int)


@pytest.mark.parametrize("what,path,status", SHAPES, ids=[s[0] for s in SHAPES])
def test_no_site_text_in_the_loader(gate, what: str, path: str, status: str) -> None:
    r = gate.client.post("/v1/web-loader", json={"urls": [f"http://public.example.com{path}"]},
                         headers=gate.auth())
    assert r.status_code == 200
    assert MARKER not in r.text.lower(), r.text


def test_an_ok_answer_carries_the_media_type_alone(gate) -> None:
    r = gate.client.post("/v1/fetch", json={"url": "http://public.example.com/ok-with-params"},
                         headers=gate.auth())
    body = r.json()
    assert body["status"] == "ok", body
    assert body["content_type"] == "text/html"
    assert MARKER not in r.text.lower()
