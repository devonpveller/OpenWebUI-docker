"""The port refuses everything the original refuses (OB1 ingest-egress.ts urlBlockReason).

Runs tests/tools/ts_parity.ts under deno over the guard corpus. Skipped where deno or the OB1
checkout is missing (the image, CI without deno); the evidence of a run is in the item's findings.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gateway.egress.guard import Refused, check_url

HERE = Path(__file__).resolve().parent
TS = HERE.parents[2] / "OB1" / "integrations" / "kubernetes-deployment" / "ingest-egress.ts"
CORPUS = HERE / "fixtures" / "guard_corpus.json"
PORTS = frozenset({80, 443})


def ts_verdicts() -> dict[str, str | None]:
    deno = shutil.which("deno")
    if deno is None or not TS.is_file():
        pytest.skip("deno or the OB1 checkout is not available")
    r = subprocess.run([deno, "run", "--allow-read", str(HERE / "tools" / "ts_parity.ts"),
                        str(TS), str(CORPUS)], capture_output=True, text=True, timeout=120,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def py_reason(url: str) -> str | None:
    try:
        check_url(url, PORTS)
    except Refused as e:
        return str(e)
    return None


def test_every_url_the_original_refuses_the_port_refuses() -> None:
    ts = ts_verdicts()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["urls"]
    assert set(ts) == {r["url"] for r in corpus}
    missed = [u for u, why in ts.items() if why is not None and py_reason(u) is None]
    assert missed == [], missed


def test_the_port_is_stricter_only_where_the_plan_says() -> None:
    """Where the port refuses and the original does not, the reason is one of the gaps
    PLAN-connectors.md names (ports, .localdomain, wildcard DNS) or a stricter address/number
    rule (non-global ranges, site-local, invalid numeric hosts, URL hygiene)."""
    ts = ts_verdicts()
    stricter = {u: py_reason(u) for u, why in ts.items() if why is None and py_reason(u)}
    allowed_kinds = ("port", "localdomain", "wildcard-DNS", "non-global", "site-local",
                     "numeric but is not a valid IPv4", "control characters", "valid domain name",
                     "credentials", "not a valid absolute URL")
    odd = {u: r for u, r in stricter.items() if not any(k in (r or "") for k in allowed_kinds)}
    assert odd == {}, odd
