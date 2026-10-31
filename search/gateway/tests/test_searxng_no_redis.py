"""search-post-gate, acceptance line 1 (static half): SearXNG gets no redis it cannot log in to.

search-redis requires a password since the gate. SearXNG's valkey client takes its password only
from its URL: `valkey.url` or the deprecated `redis.url`, each overridable by SEARXNG_VALKEY_URL /
SEARXNG_REDIS_URL (the env var wins over settings.yml). The plane's URL (the env var, and a
settings.yml `redis:` block) carried no password, so once the password existed every request to /
answered 500 (the compose healthcheck went red). The limiter is off and the gateway
keeps its own cache in redis db 1, so SearXNG needs no redis at all. The runtime half is
tests/docker/run_post_gate_lab.py (a real SearXNG against a passworded redis).
"""

from __future__ import annotations

from pathlib import Path

import yaml

SEARCH = Path(__file__).resolve().parents[2]


def test_settings_yml_has_no_redis_block() -> None:
    settings = yaml.safe_load((SEARCH / "searxng" / "settings.yml").read_text(encoding="utf-8"))
    assert settings["server"]["limiter"] is False
    assert "redis" not in settings and "valkey" not in settings


def test_compose_gives_searxng_no_redis_url() -> None:
    compose = yaml.safe_load((SEARCH / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["searxng"]["environment"]
    assert not [e for e in env if "REDIS" in e.upper() or "VALKEY" in e.upper()], env
