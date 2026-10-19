"""Shared set-up for the gate tests: the fakes started, the gateway configured through its
environment exactly as compose would, a FakeRedis in place of redis, a TestClient over the app."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import FakeRedis
from fakes import FakeEngine, FakeIntake, FakeInternet

LEGACY_KEY = "legacy-key-0123456789abcdef0123456789"
CALLERS = {
    "owui": "ck-owui-0123456789abcdef0123456789abcdef",
    "research": "ck-research-0123456789abcdef0123456789ab",
}
INTAKE_KEY = "ik-search-gate-0123456789abcdef0123456789"


class Gate:
    def __init__(self, client: TestClient, redis: FakeRedis, engine: FakeEngine,
                 internet: FakeInternet, intake: FakeIntake) -> None:
        self.client = client
        self.redis = redis
        self.engine = engine
        self.internet = internet
        self.intake = intake

    def auth(self, caller: str = "owui") -> dict[str, str]:
        key = LEGACY_KEY if caller == "legacy" else CALLERS[caller]
        return {"Authorization": f"Bearer {key}"}


def _env(tmp: Path, engine: FakeEngine, internet: FakeInternet, intake: FakeIntake,
         extra: dict[str, str] | None) -> dict[str, str]:
    callers = tmp / "gate_caller_keys"
    callers.write_text("\n".join(f"{n}={k}" for n, k in CALLERS.items()) + "\n", encoding="utf-8")
    ikey = tmp / "gate_intake_key"
    ikey.write_text(INTAKE_KEY + "\n", encoding="utf-8")
    env = {
        "GATEWAY_API_KEY": LEGACY_KEY,
        "SEARXNG_URL": engine.url,
        "GATE_CALLER_KEYS_FILE": str(callers),
        "GATE_FETCH_PROXY_URL": internet.url,
        "GATE_DOH_URL": "http://doh.test/dns-query",
        "GATE_FETCH_TIMEOUT_SECONDS": "4",
        "INTAKE_URL": intake.url + "/v1/intake",
        "INTAKE_KEY_FILE": str(ikey),
    }
    env.update(extra or {})
    return env


@contextmanager
def running_gate(tmp: Path, monkeypatch: pytest.MonkeyPatch,
                 extra_env: dict[str, str] | None = None) -> Iterator[Gate]:
    engine = FakeEngine().start()
    internet = FakeInternet().start()
    intake = FakeIntake(INTAKE_KEY).start()
    try:
        for k, v in _env(tmp, engine, internet, intake, extra_env).items():
            monkeypatch.setenv(k, v)
        from gateway import config, main

        config.get_settings.cache_clear()
        redis = FakeRedis()
        monkeypatch.setattr(main, "make_redis", lambda *a, **k: redis)
        with TestClient(main.app) as client:
            yield Gate(client, redis, engine, internet, intake)
        config.get_settings.cache_clear()
    finally:
        engine.stop()
        internet.stop()
        intake.stop()
        os.environ.pop("GATE_CALLER_KEYS_FILE", None)
