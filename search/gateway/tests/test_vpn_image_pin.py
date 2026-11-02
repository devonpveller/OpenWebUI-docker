"""rw-p5a-intake round 3 (reviewer R1): the search plane's egress image is pinned.

The intake add's fetch and every engine query leave through `vpn` (gluetun); its DNS-rebinding
refusal is what closes the window between the research service's own check of a name and the
proxy's lookup. A floating tag (`qmcgaw/gluetun:latest`) could swap that behaviour under the
plane silently, as the SearXNG image pin in the same file already argues.
"""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def test_vpn_image_default_is_pinned_by_digest() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    m = re.search(r"image:\s*\$\{VPN_IMAGE:-([^}]+)\}", text)
    assert m, "the vpn service names its image through VPN_IMAGE"
    default = m.group(1)
    assert not default.endswith(":latest"), default
    assert re.search(r"@sha256:[0-9a-f]{64}$", default), f"pinned by digest: {default}"
