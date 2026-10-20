"""The target guard against its refusal corpus (tests/fixtures/guard_corpus.json), plus the pinned
backend's own refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.egress.guard import Refused, check_addresses, check_url, ip_block_reason
from gateway.egress.transport import PinnedBackend

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "guard_corpus.json")
                    .read_text(encoding="utf-8"))
PORTS = frozenset({80, 443})


def test_the_corpus_is_the_size_it_claims() -> None:
    refuse = [r for r in CORPUS["urls"] if r["expect"] == "refuse"]
    allow = [r for r in CORPUS["urls"] if r["expect"] == "allow"]
    assert len(refuse) >= 180 and len(allow) >= 40
    assert len({r["url"] for r in CORPUS["urls"]}) == len(CORPUS["urls"])


@pytest.mark.parametrize("case", [r for r in CORPUS["urls"] if r["expect"] == "refuse"],
                         ids=lambda r: f"{r['group']}:{r['url']!r}")
def test_refused(case: dict[str, str]) -> None:
    with pytest.raises(Refused) as exc:
        check_url(case["url"], PORTS)
    assert case["why"] in str(exc.value), str(exc.value)


@pytest.mark.parametrize("case", [r for r in CORPUS["urls"] if r["expect"] == "allow"],
                         ids=lambda r: f"{r['group']}:{r['url']!r}")
def test_allowed(case: dict[str, str]) -> None:
    t = check_url(case["url"], PORTS)
    assert t.scheme in ("http", "https") and t.host


@pytest.mark.parametrize("case", CORPUS["answers"], ids=lambda r: r["host"])
def test_resolved_answers(case: dict) -> None:
    if case["expect"] == "pin":
        assert check_addresses(case["host"], case["answers"]) == case["pin"]
    else:
        with pytest.raises(Refused):
            check_addresses(case["host"], case["answers"])


def test_numeric_hosts_are_canonicalised_to_the_address_they_mean() -> None:
    assert check_url("http://0x5db8d70e/", PORTS).host == "93.184.215.14"
    assert check_url("http://1572394766/x?y", PORTS).url == "http://93.184.215.14/x?y"


def test_a_fragment_is_dropped_and_the_host_lowered() -> None:
    t = check_url("https://EXAMPLE.com./a#frag", PORTS)
    assert t.url == "https://example.com/a" and t.host_header == "example.com"


def test_ip_block_reason_is_none_only_for_public() -> None:
    assert ip_block_reason("93.184.215.14") is None
    assert ip_block_reason("not-an-ip") == "not an IP address"


async def test_the_pinned_backend_refuses_an_address_it_was_not_given() -> None:
    backend = PinnedBackend(None)
    backend.allow("93.184.215.14", 80)
    with pytest.raises(Refused):
        await backend.connect_tcp("10.0.0.7", 80)
    with pytest.raises(Refused):
        await backend.connect_tcp("93.184.215.14", 6379)  # same address, other port
    with pytest.raises(Refused):
        await backend.connect_tcp("example.com", 80)  # a name is never dialled
    assert backend.dialled == []
