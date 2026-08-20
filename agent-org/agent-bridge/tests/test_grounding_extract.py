"""`_extract` must pull the SESSION REPORT (synthesis/prose) + the VALIDATED claims (reuse_claims)
out of an openbrain-research result. Operator 2026-07-12: the research 'returned the sources and
research data but not the session report which contains the validated claims and useful information'
— because `_extract` only read `synthesis`/`claims`, while the engine puts the fuller report in
`prose` and the validated claims in `reuse_claims` ({id, text}). Plus: a `[GAP]` result (the corpus
had nothing) is UNGROUNDED, so its stale reused claims never get injected as fix context."""

from __future__ import annotations

from app.modules.grounding import _extract, _is_gap


def test_extract_reads_validated_claims_from_reuse_claims():
    result = {
        "synthesis": "MSB3202 means a referenced project file is missing.",
        "cited_sources": [{"url": "https://learn.microsoft.com/msb3202"}],
        "reuse_claims": [
            {"id": "a", "text": "MSB3202 fires when a referenced .csproj is not on disk."},
            {"id": "b", "text": "For git submodules, run `git submodule update --init --recursive`."},
        ],
    }
    summary, claims = _extract(result)
    assert "referenced project file is missing" in summary
    assert len(claims) == 2
    assert any("submodule update --init --recursive" in c for c in claims)   # validated claim kept


def test_extract_prefers_the_fuller_prose_report():
    result = {
        "synthesis": "short.",
        "prose": "A much fuller narrative session report with the useful detail the operator wants.",
    }
    summary, _ = _extract(result)
    assert "fuller narrative session report" in summary       # the fuller report, not the terse line


def test_extract_dedupes_and_reads_plain_string_claims():
    result = {"claims": ["c1", "c1"], "reuse_claims": [{"text": "c2"}]}
    _, claims = _extract(result)
    assert claims == ["c1", "c2"]                             # deduped across shapes


def test_gap_result_is_detected():
    assert _is_gap("[GAP] The provided sources do not contain information about MSB3202.")
    assert _is_gap("")                                        # blank = no answer
    assert _is_gap("the provided sources do not contain the answer")
    assert not _is_gap("MSB3202 means a referenced project file is missing; init the submodules.")


def test_gap_synthesis_carries_the_gap_marker_for_the_caller():
    """A gap result still parses, but the summary is the gap marker so ground()/advise() flag it
    ungrounded and its (irrelevant) reused claims are never injected."""
    gap = {
        "synthesis": "[GAP] The provided sources do not contain information about MSB3202.",
        "reuse_claims": [{"id": "x", "text": "Reconnecting the USB cable fixes the display."}],
    }
    summary, _claims = _extract(gap)
    assert _is_gap(summary)
