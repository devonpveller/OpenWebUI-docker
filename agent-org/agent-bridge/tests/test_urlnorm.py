"""kp-cleanup: app/modules/urlnorm.py is the REFERENCE URL normaliser (coordinator decision FU-1,
reviews/kp-contract.md: the web gate will be Python; the research engine's urlnorm.ts stays in parity
through the shared corpus).

tests/fixtures/url-normalise-corpus.json is a byte-for-byte copy of OB1
integrations/research-service/fixtures/url-normalise-corpus.json (last changed at OB1 c86b49f6, the
pinned gitlink at the copy was OB1 43d35fb). It holds every URL-shaped input of the kp-research /
kp-harden testers' probes (session scratchpad kpr2..kpr5, kph-acc) plus a seeded replay of their
WHATWG bypass fuzz, each with the verdict of the agent-bridge normaliser it was generated from
(development c1bc20a0, text filters that are not URL normalisation switched off). urlnorm.test.ts in
OB1 runs the same rows against the TypeScript port. No network, no OB1 needed (the copy check is
skipped only when the submodule is not checked out).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules import urlnorm

FIXTURE = Path(__file__).parent / "fixtures" / "url-normalise-corpus.json"
CORPUS = json.loads(FIXTURE.read_text(encoding="utf-8"))
AT = "@"   # userinfo probes are built, so the identity gate does not read them as e-mail addresses
OB1_FIXTURE = (Path(__file__).resolve().parents[3] / "OB1" / "integrations" / "research-service"
               / "fixtures" / "url-normalise-corpus.json")


def test_corpus_every_row_gets_its_documented_verdict():
    rows = CORPUS["rows"]
    assert len(rows) == 3251
    bad = []
    for r in rows:
        got = urlnorm.normalise_url(r["url"])
        want = (r["norm"][0], r["norm"][1]) if r["norm"] else None
        if got != want:
            bad.append((r["url"], r["from"], got, want))
    assert bad == [], bad[:20]
    assert sum(1 for r in rows if r["norm"]) == 822          # both verdicts are exercised


def _engine_admits(entries: list[str], norm: tuple[str, list[str]] | None) -> bool:
    """contract.ts ruleMatches over urlnorm's output: `host` / `*.suffix` = host-or-subdomain,
    `host/path` = exact host + whole-segment prefix. Test-only: it shows urlnorm's (host, segs) is
    all the engine's matcher needs to reproduce the corpus's `admit` column."""
    if norm is None:
        return False
    host, segs = norm
    for e in entries:
        if "/" in e:
            h, p = e.split("/", 1)
            ps = p.split("/")
            if host == h and segs[:len(ps)] == ps:
                return True
        else:
            base = e[2:] if e.startswith("*.") else e
            if host == base or host.endswith("." + base):
                return True
    return False


def test_corpus_admit_column_follows_from_the_normalised_view():
    rows = CORPUS["rows"]
    bad = [r["url"] for r in rows
           if _engine_admits(CORPUS["entries"], urlnorm.normalise_url(r["url"])) is not r["admit"]]
    assert bad == [], bad[:20]
    assert sum(r["admit"] for r in rows) == 196


def test_the_fixture_is_the_ob1_corpus():
    if not OB1_FIXTURE.is_file():
        pytest.skip("OB1 submodule not checked out")
    assert json.loads(OB1_FIXTURE.read_text(encoding="utf-8")) == CORPUS


def test_normaliser_host_case_root_dot_unreserved_escapes_empty_port():
    n = urlnorm.normalise_url
    assert n("https://DOCS.Python.ORG./3/%7Euser/x") == ("docs.python.org", ["3", "~user", "x"])
    assert n("https://docs.python.org:/3/") == ("docs.python.org", ["3", ""])
    assert n("https://docs.python.org") == ("docs.python.org", [""])
    assert n("http://docs.python.org/a%3Ab") == ("docs.python.org", ["a%3Ab"])
    assert n("https://docs.python.org/a%3ab") == ("docs.python.org", ["a%3Ab"])
    # N3 (moved from test_knowledge_package): only UNRESERVED escapes are decoded
    assert n("https://numpy.org/doc/a%40b%3Fc") == ("numpy.org", ["doc", "a%40b%3Fc"])
    assert n("https://numpy.org/doc/a%3fc") == ("numpy.org", ["doc", "a%3Fc"])
    assert n("https://numpy.org/doc/%7Euser") == ("numpy.org", ["doc", "~user"])


@pytest.mark.parametrize("url", [
    "https://docs.python.org/3/../x", "https://docs.python.org/3/./x", "https://docs.python.org/3/.../x",
    "https://docs.python.org/3/%2e%2e/x", "https://docs.python.org/3/%2E./x", "https://docs.python.org/3%2fx",
    "https://docs.python.org/3%5cx", "https://docs.python.org/3\\x", "https://docs.python.org/3/..;/x",
    "https://docs.python.org/3/%252e%252e/x", "https://docs.python.org/3/%c0%ae%c0%ae/x",
    "https://docs.python.org/3/%e0%80%ae/x", "https://docs.python.org/3/%f0%80%80%ae/x",
    "https://docs.python.org/3/%ed%a0%80/x", "https://docs.python.org/3/%e2%80%a5/x",
    "https://docs.python.org/3/%ef%bc%8f/x", "https://docs.python.org/3/%e3%80%82/x",
    "https://docs.python.org/3/%00x", "https://docs.python.org/3/%09x", "https://docs.python.org/3/%20x",
    "https://docs.python.org/3/%u002e", "https://docs.python.org/3/%",
    "https://docs.python.org/3/é", "https://docs.python.org/3/\t../x", " https://docs.python.org/3/",
    "https://docs.python.org:443/3/", "https://u:p" + AT + "docs.python.org/3/",
    "https://docs.python.org" + AT + "evil.com/3/",
    "https://127.0.0.1/3/", "https://[::1]/3/", "https://localhost/3/", "https://docs.internal/3/",
    "https://xn--dcs-python-xyz.org/3/", "https://docs.python.org../3/", "https://docs..python.org/3/",
    "https://docs.python.123/3/", "HTTPS://docs.python.org/3/", "https:/docs.python.org/3/",
    "https:docs.python.org/3/", "//docs.python.org/3/", "https://docs.python.org/3/data:x",
    "ftp://docs.python.org/3/", "javascript:alert(1)", "", None, 5,
])
def test_normaliser_refuses_every_hidden_segment_host_and_encoding_trick(url):
    assert urlnorm.normalise_url(url) is None, url


def test_rfc3986_helpers_match_the_reference_algorithm():
    assert urlnorm.remove_dot_segments("/a/b/c/./../../g") == "/a/g"
    assert urlnorm.remove_dot_segments("mid/content=5/../6") == "mid/6"
    assert urlnorm.remove_dot_segments("/../a") == "/a"
    assert urlnorm.remove_dot_segments("/a/b/..") == "/a/"
    assert urlnorm.decode_unreserved("/%41%2d%2F%7e%c3%a9") == "/A-%2F~%C3%A9"


def test_host_shape():
    for ok in ("docs.python.org", "a-b.example.org"):
        assert urlnorm.host_shape_ok(ok), ok
    for bad in ("", "org", "docs.python.123", "1.2.3.4", "xn--dcs-ula.example.org", "a.local",
                "x.svc", "localhost", "nip.io", "a..b.org", "-a.org", "a_b.org"):
        assert not urlnorm.host_shape_ok(bad), bad


def test_docstring_states_parser_vs_server_level():
    doc = " ".join(urlnorm.normalise_url.__doc__.split()).lower()
    assert "parser level" in doc and "server-level" in doc
