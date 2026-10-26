"""Engine-agnostic (artifact (7)): both adapters pass the same contract, and the engine is chosen
by configuration alone. The docker round trip repeats the contract against the real images."""

from __future__ import annotations

from pathlib import Path

import pytest

from adapter_contract import run
from fake_engine import DEFAULT_RESULTS
from fakes import FakeEngine
from gate_support import running_gate


@pytest.fixture
def engine():
    e = FakeEngine().start()
    e.results = list(DEFAULT_RESULTS)
    yield e
    e.stop()


@pytest.mark.parametrize("adapter", ["fake", "searxng"])
async def test_adapter_contract(engine: FakeEngine, adapter: str) -> None:
    out = await run(adapter, engine.url)
    assert out["ok"], [c for c in out["checks"] if not c["ok"]]


@pytest.mark.parametrize("adapter,path", [("fake", "/query"), ("searxng", "/search")])
def test_switching_the_config_switches_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  adapter: str, path: str) -> None:
    with running_gate(tmp_path, monkeypatch, {"SEARCH_ENGINE": adapter}) as g:
        body = g.client.post("/v1/search", json={"query": "bread"},
                             headers=g.auth("legacy")).json()
        assert body["provider_used"] == adapter
        assert [r["path"] for r in g.engine.requests] == [path]


def test_the_engine_neutral_names_win_over_the_legacy_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.config import Settings

    monkeypatch.setenv("PROVIDER_PRIORITY", "searxng")
    monkeypatch.setenv("SEARCH_ENGINE", "fake")
    monkeypatch.setenv("SEARXNG_URL", "http://old:8080")
    monkeypatch.setenv("SEARCH_ENGINE_URL", "http://new:8080")
    s = Settings()  # type: ignore[call-arg]
    assert s.engine_order == ["fake"] and s.search_engine_url == "http://new:8080"


def test_an_unknown_engine_refuses_the_start() -> None:
    from gateway.rotation import build_provider

    with pytest.raises(ValueError, match="Unknown engine"):
        build_provider("bing", "http://x", 1.0)
