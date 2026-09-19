"""End-to-end smoke test against a running compose stack.

Deselected by default (pyproject addopts: -m 'not integration'). Run with the
stack up via:  pytest -m integration

Requires:
  GATEWAY_E2E_URL  (default http://localhost:8080)
  GATEWAY_API_KEY  (same shared key the gateway runs with)
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE = os.environ.get("GATEWAY_E2E_URL", "http://localhost:8080")
KEY = os.environ.get("GATEWAY_API_KEY", "")


def test_readyz_then_native_search() -> None:
    with httpx.Client(base_url=BASE, timeout=60) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200, ready.text

        resp = client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {KEY}"},
            json={"query": "anthropic claude"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provider_used"] == "searxng"
        assert len(body["results"]) >= 1
