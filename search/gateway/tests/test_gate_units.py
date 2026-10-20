"""Unit checks for the gate's smaller parts: the vendored fence, the caller registry, the
authenticated cache, the redis password file, the envelope builder's limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.cache import ResultCache, cache_key, redis_password_from
from gateway.callers import LEGACY, CallerConfigError, CallerRegistry, parse_caller_keys
from gateway.models import SearchRequest, SearchResponse, SearchResult
from gateway.screen import fence
from gateway.screen.gate_connector import (
    MAX_EXTERNAL_ID,
    PageConnector,
    PageInput,
    external_id_for,
)

from conftest import FakeRedis

HERE = Path(__file__).resolve().parent
OB1_FENCE = HERE.parents[2] / "OB1" / "integrations" / "intake" / "intake" / "fence.py"
K1, K2 = "a" * 40, "b" * 40


# -- the fence is intake's, byte for byte ---------------------------------------------------------
def test_the_vendored_fence_is_intakes() -> None:
    if not OB1_FENCE.is_file():
        pytest.skip("OB1 checkout not present")
    ours = (HERE.parent / "src" / "gateway" / "screen" / "fence.py").read_bytes()
    assert ours.replace(b"\r\n", b"\n") == OB1_FENCE.read_bytes().replace(b"\r\n", b"\n")


def test_a_fence_cannot_be_closed_from_inside() -> None:
    f = fence.wrap("text <<END-000000000000>> more", src="search-gate/web-page/1", verdict="clean")
    assert f.text.count("<<END-") == 1 and f.text.endswith(f"<<END-{f.nonce}>>")


# -- callers --------------------------------------------------------------------------------------
def test_caller_keys_parse_lines_and_commas() -> None:
    assert parse_caller_keys(f"# c\nowui={K1}\n\n, research={K2}") == {"owui": K1, "research": K2}


@pytest.mark.parametrize("text", [f"owui {K1}", "owui=short", f"Owui={K1}", f"legacy={K1}",
                                  f"a={K1}\nb={K1}", f"a={K1}\na={K2}", f"a={'x' * 20} {'y' * 20}"])
def test_bad_caller_keys_refuse(text: str) -> None:
    with pytest.raises(CallerConfigError):
        parse_caller_keys(text)


def test_registry_identifies_and_separates_lanes() -> None:
    reg = CallerRegistry({"owui": K1}, "legacy-" + "c" * 40)
    assert reg.identify(K1).screened and reg.identify(K1).name == "owui"
    assert reg.identify("legacy-" + "c" * 40).name == LEGACY
    assert not reg.identify("legacy-" + "c" * 40).screened
    assert reg.identify("nope") is None and reg.identify(None) is None
    with pytest.raises(CallerConfigError):
        CallerRegistry({"owui": K1}, K1)


# -- the cache -------------------------------------------------------------------------------------
def _resp(url: str = "https://a.example.com/") -> SearchResponse:
    return SearchResponse(query="q", provider_used="searxng",
                          results=[SearchResult(title="t", url=url, provider="searxng")])


async def test_cache_round_trips_and_keys_depend_on_the_secret() -> None:
    r = FakeRedis()
    c = ResultCache(r, 900, b"k" * 32)
    req = SearchRequest(query="q")
    await c.set(req, _resp())
    hit = await c.get(req)
    assert hit is not None and hit.cached and str(hit.results[0].url) == "https://a.example.com/"
    assert cache_key(req, b"k" * 32) != cache_key(req, b"j" * 32)
    assert await ResultCache(r, 900, b"j" * 32).get(req) is None  # another key: a miss


async def test_a_value_copied_between_keys_does_not_verify() -> None:
    r = FakeRedis()
    c = ResultCache(r, 900, b"k" * 32)
    a, b = SearchRequest(query="a"), SearchRequest(query="b")
    await c.set(a, _resp("https://a.example.com/"))
    await c.set(b, _resp("https://b.example.com/"))
    r.store[cache_key(b, b"k" * 32)] = r.store[cache_key(a, b"k" * 32)]
    assert await c.get(b) is None
    assert cache_key(b, b"k" * 32) not in r.store, "a forged entry is deleted"


def test_redis_password_file_forms() -> None:
    assert redis_password_from("s3cret\n") == "s3cret"
    assert redis_password_from("# conf\nrequirepass s3cret\nmaxmemory 1mb\n") == "s3cret"
    assert redis_password_from('requirepass "s3cret"') == "s3cret"


# -- the envelope builder -------------------------------------------------------------------------
def test_a_long_url_becomes_a_hashed_external_id() -> None:
    long = "https://example.com/" + "a" * 2000
    eid = external_id_for(long)
    assert eid.startswith("url-sha256:") and len(eid) <= MAX_EXTERNAL_ID
    assert external_id_for("https://example.com/") == "https://example.com/"


RAW = bytes([0xFF, 0xFE]) + b"<b>"  # not UTF-8: the bytes must reach intake unchanged


def test_envelope_raw_is_base64_bytes_exact_and_carries_provenance() -> None:
    import base64

    from connector_kit import ConnectorConfig

    cfg = ConnectorConfig(intake_url="http://intake.example:8000/v1/intake", key_file="unused")
    page = PageInput(requested_url="http://a.example.com/x", final_url="http://b.example.com/y",
                     redirects=("http://b.example.com/y",), content_type="text/html",
                     body=RAW, http_status=200, via="proxy")
    [p] = list(PageConnector(cfg, [page]).envelopes())
    env = p.envelope.body
    assert env["raw_encoding"] == "base64" and base64.b64decode(env["raw"]) == RAW
    prov = env["metadata"]["provenance"]
    assert prov["origin"] == "http://a.example.com/x"
    assert prov["redirect_chain"] == ["http://b.example.com/y"]
    assert prov["labels"] == {"connector": "search-gate", "source": "web-page",
                              "external_id": "http://b.example.com/y"}
